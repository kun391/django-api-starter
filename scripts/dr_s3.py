"""Read-only S3 version-pinned inventory for recovery verification.

Requires optional boto3. Selects ONLY a supplied bucket, never deletes or copies.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from importlib import import_module
from pathlib import Path


def inventory(client, bucket: str) -> dict:
    rows = []
    token = None
    while True:
        params = {"Bucket": bucket, "Prefix": "objects/"}
        if token:
            params["ContinuationToken"] = token
        response = client.list_objects_v2(**params)
        for item in response.get("Contents", []):
            key = item["Key"]
            if not re.fullmatch(r"objects/[a-f0-9]{2}/[a-f0-9]{32}", key):
                raise ValueError("Unexpected S3 object key")
            if key.split("/")[1] != key.split("/")[2][:2]:
                raise ValueError("Unexpected S3 prefix")
            metadata = client.head_object(Bucket=bucket, Key=key)
            version = metadata.get("VersionId")
            # Reject mutable/unversioned buckets. Reading a version-pinned object
            # avoids accidentally verifying new bytes after a concurrent overwrite.
            if not version or version == "null":
                raise ValueError("Versioned S3 objects are required")
            stream = client.get_object(Bucket=bucket, Key=key, VersionId=version)["Body"]
            digest = hashlib.sha256()
            length = 0
            try:
                while chunk := stream.read(1024 * 1024):
                    length += len(chunk)
                    digest.update(chunk)
            finally:
                stream.close()
            if length != item["Size"]:
                raise ValueError("Object changed while inventorying")
            rows.append({"key": key, "bytes": length, "sha256": digest.hexdigest(), "version_id": version})
        if not response.get("IsTruncated"):
            break
        token = response.get("NextContinuationToken")
        if not token:
            raise ValueError("Incomplete S3 pagination")
    return {"schema_version": 1, "bucket": bucket, "objects": sorted(rows, key=lambda x: x["key"])}


def verify(client, bucket: str, manifest: dict) -> None:
    if manifest.get("schema_version") != 1 or manifest.get("bucket") != bucket:
        raise ValueError("Wrong bucket or manifest version")
    expected = manifest.get("objects")
    if not isinstance(expected, list):
        raise ValueError("Missing object inventory")
    for item in expected:
        key, version = item["key"], item["version_id"]
        if not re.fullmatch(r"objects/[a-f0-9]{2}/[a-f0-9]{32}", key):
            raise ValueError("Invalid object key")
        if key.split("/")[1] != key.split("/")[2][:2] or not isinstance(version, str) or not version or version == "null":
            raise ValueError("Invalid object identity")
        response = client.get_object(Bucket=bucket, Key=key, VersionId=version)
        digest = hashlib.sha256()
        length = 0
        body = response["Body"]
        try:
            while chunk := body.read(1024 * 1024):
                length += len(chunk)
                digest.update(chunk)
        finally:
            body.close()
        if length != item["bytes"] or digest.hexdigest() != item["sha256"]:
            raise ValueError("Snapshot object mismatch")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--endpoint")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--output", type=Path)
    group.add_argument("--verify", type=Path)
    args = parser.parse_args()
    try:
        if args.endpoint and not args.endpoint.startswith("https://") and os.environ.get("DR_TEST_ALLOW_HTTP") != "yes":
            raise ValueError("S3 endpoint must use HTTPS")
        client = import_module("boto3").client("s3", region_name=args.region, endpoint_url=args.endpoint)
        if args.output:
            if args.output.exists() or args.output.is_symlink():
                raise ValueError("Manifest already exists")
            data = inventory(client, args.bucket)
            fd = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2)
                handle.write("\n")
        else:
            if args.verify.is_symlink():
                raise ValueError("Manifest symlink denied")
            data = json.loads(args.verify.read_text(encoding="utf-8"))
            verify(client, args.bucket, data)
        print("S3 recovery inventory verified")
        return 0
    except (OSError, ValueError, KeyError, TypeError, ImportError) as exc:
        print(f"S3 recovery verification failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
