#!/usr/bin/env python3
"""Operator-only encrypted offsite DR backup and isolated restore using restic.

The caller must freeze writers and prepare a consistent bundle; this utility
checks manifests but cannot prove PostgreSQL and object storage were atomic.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

_REMOTE_PREFIXES = ("s3:", "sftp:", "rest:https://", "b2:", "azure:", "gs:", "rclone:", "swift:")


def _sha256(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def _regular_file(path: Path) -> None:
    if path.is_symlink() or not path.is_file() or not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("Bundle contains missing, non-regular or symlinked files")


def _entry(bundle: Path, value: object) -> Path:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", value):
        raise ValueError("Invalid bundle-relative filename")
    parts = value.split("/")
    if any(part in (".", "..") for part in parts):
        raise ValueError("Bundle traversal is forbidden")
    candidate = bundle
    for part in parts:
        candidate /= part
        if candidate.is_symlink():
            raise ValueError("Bundle symlink is forbidden")
    _regular_file(candidate)
    return candidate


def _manifest(bundle: Path) -> dict:
    if not bundle.is_dir() or bundle.is_symlink():
        raise ValueError("Bundle must be an existing real directory")
    marker = _entry(bundle, "recovery-point.json")
    data = json.loads(marker.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("Unsupported recovery point")
    rid = data.get("recovery_id")
    if not isinstance(rid, str) or not re.fullmatch(r"dr-[a-z0-9][a-z0-9-]{5,62}", rid):
        raise ValueError("Invalid recovery point identifier")
    pg = _entry(bundle, data.get("database_manifest"))
    pg_info = json.loads(pg.read_text(encoding="utf-8"))
    if not isinstance(pg_info, dict) or pg_info.get("schema_version") != 1:
        raise ValueError("Unsupported PostgreSQL manifest")
    db_file = _entry(bundle, str(pg.relative_to(bundle).parent / str(pg_info.get("archive_name", ""))))
    if (
        db_file.stat().st_size != pg_info.get("bytes")
        or _sha256(db_file) != pg_info.get("sha256")
    ):
        raise ValueError("PostgreSQL dump checksum mismatch")
    object_manifest = _entry(bundle, data.get("object_manifest"))
    objects = json.loads(object_manifest.read_text(encoding="utf-8"))
    if not isinstance(objects, dict) or objects.get("schema_version") != 1 or not isinstance(objects.get("objects"), list):
        raise ValueError("Unsupported object manifest")
    root = bundle / "private-files"
    if not root.is_dir() or root.is_symlink():
        raise ValueError("Missing private-files snapshot")
    expected = set()
    for item in objects["objects"]:
        key, size, checksum = item["key"], item["bytes"], item["sha256"]
        if not isinstance(key, str) or not re.fullmatch(r"objects/[a-f0-9]{2}/[a-f0-9]{32}", key):
            raise ValueError("Unexpected private object key")
        if key.split("/")[1] != key.split("/")[2][:2] or key in expected:
            raise ValueError("Invalid/duplicate private object key")
        if type(size) is not int or size < 0 or not isinstance(checksum, str) or not re.fullmatch(r"[a-f0-9]{64}", checksum):
            raise ValueError("Invalid private object digest")
        path = _entry(root, key)
        if path.stat().st_size != size or _sha256(path) != checksum:
            raise ValueError("Private object checksum mismatch")
        expected.add(key)
    actual = set()
    for current, dirs, files in os.walk(root, followlinks=False):
        for dirname in dirs:
            if (Path(current) / dirname).is_symlink():
                raise ValueError("Private snapshot contains symlink")
        for filename in files:
            path = Path(current) / filename
            _regular_file(path)
            actual.add(path.relative_to(root).as_posix())
    if actual != expected:
        raise ValueError("Private snapshot has unexpected or missing files")
    return data


def _restic_environment() -> None:
    repository = os.environ.get("RESTIC_REPOSITORY", "")
    if not repository:
        raise ValueError("RESTIC_REPOSITORY is required")
    if repository.startswith("s3:http://") or repository.startswith("rest:http://"):
        raise ValueError("Offsite repository must use encrypted transport")
    if not repository.startswith(_REMOTE_PREFIXES):
        if os.environ.get("DR_TEST_ALLOW_LOCAL_RESTIC") != "yes":
            raise ValueError("Offsite repository is required (local only in isolated tests)")
    if "RESTIC_PASSWORD" in os.environ or "RESTIC_PASSWORD_COMMAND" in os.environ:
        raise ValueError("Use only RESTIC_PASSWORD_FILE; never inject password as environment text")
    name = os.environ.get("RESTIC_PASSWORD_FILE", "")
    secret = Path(name)
    if not name or not secret.is_absolute() or secret.is_symlink():
        raise ValueError("RESTIC_PASSWORD_FILE must be an absolute regular file")
    _regular_file(secret)
    if secret.stat().st_mode & 0o077:
        raise ValueError("RESTIC_PASSWORD_FILE must be mode 0600")
    if not secret.read_bytes().strip():
        raise ValueError("RESTIC_PASSWORD_FILE is empty")


def _run(*args: str, cwd: Path | None = None) -> None:
    subprocess.run(["restic", *args], cwd=cwd, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("validate", help="Check frozen bundle without restic")
    check.add_argument("--bundle", type=Path, required=True)
    backup = commands.add_parser("backup")
    backup.add_argument("--bundle", type=Path, required=True)
    backup.add_argument("--confirm", required=True)
    commands.add_parser("check", help="Check offsite repository metadata")
    restore = commands.add_parser("restore")
    restore.add_argument("--snapshot", required=True, help="Exact restic snapshot ID, never latest")
    restore.add_argument("--target", type=Path, required=True)
    restore.add_argument("--confirm", required=True)
    args = parser.parse_args()
    try:
        if args.command in ("validate", "backup"):
            data = _manifest(args.bundle)
            if args.command == "validate":
                print(f"Verified frozen recovery bundle {data['recovery_id']}")
                return 0
            if args.confirm != f"FROZEN:{data['recovery_id']}":
                raise ValueError("Explicit FROZEN:<recovery_id> confirmation is required")
            _restic_environment()
            _run("backup", "--tag", data["recovery_id"], "--", str(args.bundle.resolve()))
        elif args.command == "check":
            _restic_environment()
            _run("check")
        else:
            if not re.fullmatch(r"[a-f0-9]{8,64}", args.snapshot):
                raise ValueError("Restore requires an explicit snapshot hex ID")
            if args.confirm != f"RESTORE:{args.snapshot}":
                raise ValueError("Explicit RESTORE:<snapshot-id> confirmation is required")
            target = args.target
            if not target.is_absolute() or target.exists() or target.is_symlink():
                raise ValueError("Restore target must be a new absolute directory")
            _restic_environment()
            target.mkdir(mode=0o700, parents=False, exist_ok=False)
            _run("restore", args.snapshot, "--target", str(target))
        return 0
    except (ValueError, OSError, KeyError, TypeError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f"DR offsite operation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
