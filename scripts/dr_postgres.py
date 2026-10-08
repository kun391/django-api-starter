#!/usr/bin/env python3
"""Operator-only PostgreSQL backup/restore with fail-closed defaults.

Requires libpq PG* environment or .pgpass; never accepts passwords on CLI.
Private object storage and externally hosted dependencies are separate backups.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone


def run(args: list[str]) -> None:
    subprocess.run(args, check=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def backup(directory: Path, database: str) -> None:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink() or directory.stat().st_mode & 0o077:
        raise ValueError("Backup directory must be private (mode 0700) and not a symlink")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    prefix = f"{database}-{timestamp}-{os.getpid()}"
    archive = directory / f"{prefix}.dump"
    manifest = directory / f"{prefix}.json"
    fd = os.open(archive, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        run(["pg_dump", "--format=custom", "--no-owner", "--no-acl", "--file", str(archive), database])
        run(["pg_restore", "--list", str(archive)])
        details = {
            "schema_version": 1,
            "database": database,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "archive_name": archive.name,
            "sha256": sha256(archive),
            "bytes": archive.stat().st_size,
            "note": "Logical PostgreSQL backup only; private files require an independently consistent snapshot.",
        }
        manifest_fd = os.open(manifest, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(manifest_fd, "w", encoding="utf-8") as stream:
            json.dump(details, stream, indent=2)
            stream.write("\n")
        print(f"Backup created: {archive} (manifest {manifest})")
    except BaseException:
        archive.unlink(missing_ok=True)
        manifest.unlink(missing_ok=True)
        raise


def verify(manifest: Path) -> Path:
    if manifest.is_symlink() or manifest.stat().st_mode & 0o077:
        raise ValueError("Manifest must be a private regular file (mode 0600)")
    info = json.loads(manifest.read_text(encoding="utf-8"))
    if info.get("schema_version") != 1:
        raise ValueError("Unsupported manifest schema")
    name = info.get("archive_name")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+\.dump", name):
        raise ValueError("Invalid archive name")
    archive = manifest.parent / name
    if archive.is_symlink() or not archive.is_file():
        raise ValueError("Archive missing or symlinked")
    if archive.stat().st_mode & 0o077:
        raise ValueError("Archive must be private (mode 0600)")
    if archive.stat().st_size != info.get("bytes") or sha256(archive) != info.get("sha256"):
        raise ValueError("Backup checksum/length mismatch")
    run(["pg_restore", "--list", str(archive)])
    print(f"Verified: {archive}")
    return archive


def restore(manifest: Path, target: str, confirm: str) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9_]{2,62}", target):
        raise ValueError("Target database name must be lowercase ASCII and 3-63 characters")
    if not target.startswith("dr_"):
        raise ValueError("Restore only to isolated dr_ prefixed databases")
    if confirm != f"CREATE:{target}":
        raise ValueError(f"Explicit --confirm CREATE:{target} required")
    archive = verify(manifest)
    # createdb fails if target exists: there is intentionally no overwrite/drop path.
    run(["createdb", "--maintenance-db=postgres", target])
    print(f"Created isolated target {target}; restore starts now. On failure inspect it before cleanup.")
    run(["pg_restore", "--exit-on-error", "--no-owner", "--no-acl", "--dbname", target, str(archive)])
    print(f"Restored to {target}. Application-level validation is still required.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("backup")
    b.add_argument("--directory", type=Path, required=True)
    b.add_argument("--database", required=True)
    v = sub.add_parser("verify")
    v.add_argument("--manifest", type=Path, required=True)
    r = sub.add_parser("restore")
    r.add_argument("--manifest", type=Path, required=True)
    r.add_argument("--target", required=True)
    r.add_argument("--confirm", required=True)
    args = parser.parse_args()
    try:
        if args.command == "backup":
            if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,62}", args.database):
                raise ValueError("Invalid database name")
            backup(args.directory, args.database)
        elif args.command == "verify":
            verify(args.manifest)
        else:
            restore(args.manifest, args.target, args.confirm)
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f"DR operation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
