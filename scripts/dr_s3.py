"""S3 version-pinned verification and guarded isolated-bucket restore.

Requires optional boto3. Never deletes objects or creates buckets.
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



def restore_to_isolated_bucket(
    client, source_bucket: str, target_bucket: str, manifest: dict, confirm: str
) -> dict:
    """Copy pinned source versions to a pre-provisioned, empty DR bucket.

    This is not an atomic transaction. On failure, leave partial results for
    investigation; never overwrite or remove objects.
    """
    if not isinstance(target_bucket, str) or not re.fullmatch(
        r"dr-[a-z0-9][a-z0-9-]{2,62}", target_bucket
    ):
        raise ValueError("Restore target must be a dedicated dr- bucket")
    if target_bucket == source_bucket:
        raise ValueError("Source and DR target bucket must differ")
    if confirm != f"RESTORE:{source_bucket}->{target_bucket}":
        raise ValueError("Explicit RESTORE:<source>-><dr-target> confirmation required")
    if client.get_bucket_versioning(Bucket=target_bucket).get("Status") != "Enabled":
        raise ValueError("DR target must have S3 versioning enabled")
    existing = client.list_object_versions(Bucket=target_bucket, MaxKeys=1)
    if existing.get("Versions") or existing.get("DeleteMarkers"):
        raise ValueError("DR target has existing objects or historical versions")
    if client.list_objects_v2(Bucket=target_bucket, MaxKeys=1).get("Contents"):
        raise ValueError("DR target must be empty")

    # Validate all historical source object bytes before initiating writes.
    verify(client, source_bucket, manifest)
    entries = manifest["objects"]
    source = {}
    for item in entries:
        key = item["key"]
        size = item["bytes"]
        checksum = item["sha256"]
        if key in source:
            raise ValueError("Duplicate object key in recovery manifest")
        if type(size) is not int or not 0 <= size <= 16 * 1024 * 1024:
            raise ValueError("Invalid recovery object size")
        if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
            raise ValueError("Invalid recovery checksum")
        source[key] = (size, checksum)

    for item in entries:
        client.copy_object(
            Bucket=target_bucket,
            Key=item["key"],
            CopySource={
                "Bucket": source_bucket,
                "Key": item["key"],
                "VersionId": item["version_id"],
            },
            MetadataDirective="COPY",
        )

    # Read the destination bytes, not only copy-object responses or ETags.
    recovered = inventory(client, target_bucket)
    actual = {item["key"]: (item["bytes"], item["sha256"]) for item in recovered["objects"]}
    if actual != source:
        raise ValueError("DR target checksum/key mismatch; isolate partial recovery")
    return recovered

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--endpoint")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--output", type=Path)
    group.add_argument("--verify", type=Path)
    group.add_argument("--restore-to")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--confirm")
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
        elif args.verify:
            if args.verify.is_symlink() or not args.verify.is_file():
                raise ValueError("Manifest must be a regular file")
            data = json.loads(args.verify.read_text(encoding="utf-8"))
            verify(client, args.bucket, data)
            print("S3 recovery inventory verified")
        else:
            if args.manifest is None or args.confirm is None:
                raise ValueError("--manifest and --confirm are required for restore")
            if args.manifest.is_symlink() or not args.manifest.is_file():
                raise ValueError("Manifest must be a regular file")
            data = json.loads(args.manifest.read_text(encoding="utf-8"))
            restored = restore_to_isolated_bucket(
                client, args.bucket, args.restore_to, data, args.confirm
            )
            print(f"Verified {len(restored['objects'])} objects in isolated DR bucket")
        return 0
    except (OSError, ValueError, KeyError, TypeError, ImportError) as exc:
        print(f"S3 recovery verification failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
