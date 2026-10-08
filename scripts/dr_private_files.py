#!/usr/bin/env python3
"""Read-only private-files snapshot inventory verifier; never performs a backup."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def objects(root: Path):
    base = root.resolve(strict=True)
    if not base.is_dir() or root.is_symlink():
        raise ValueError("Root must be a real directory, not a symlink")
    for directory, dirs, files in os.walk(base, followlinks=False):
        for name in dirs + files:
            p = Path(directory) / name
            if p.is_symlink():
                raise ValueError("Snapshot contains a symbolic link")
        for name in files:
            p = Path(directory) / name
            if not p.is_file() or not stat.S_ISREG(p.lstat().st_mode):
                raise ValueError("Snapshot contains non-regular files")
            relative = p.relative_to(base).as_posix()
            if not re.fullmatch(r"objects/[a-f0-9]{2}/[a-f0-9]{32}", relative):
                raise ValueError("Unexpected private-file object path")
            if relative.split("/")[1] != relative.split("/")[2][:2]:
                raise ValueError("Private-file prefix mismatch")
            yield relative, p


def inventory(root: Path) -> dict:
    entries = []
    for name, path in objects(root):
        before = path.stat()
        checksum = digest(path)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (
            after.st_size, after.st_mtime_ns, after.st_ino
        ):
            raise ValueError("File changed during inventory")
        entries.append({"key": name, "bytes": after.st_size, "sha256": checksum})
    return {"schema_version": 1, "objects": sorted(entries, key=lambda x: x["key"])}


def verify(root: Path, manifest: Path) -> None:
    if manifest.is_symlink() or not manifest.is_file():
        raise ValueError("Manifest must be a regular file")
    expected = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(expected, dict) or expected.get("schema_version") != 1:
        raise ValueError("Unsupported inventory schema")
    actual = inventory(root)
    if actual != expected:
        raise ValueError("Private files mismatch: missing, extra or modified objects")
    print(f"Verified {len(actual['objects'])} snapshot objects")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("inventory", "verify"):
        cmd = sub.add_parser(action)
        cmd.add_argument("--root", required=True, type=Path)
        if action == "inventory":
            cmd.add_argument("--output", required=True, type=Path)
        else:
            cmd.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.action == "inventory":
            if args.output.exists() or args.output.is_symlink():
                raise ValueError("Manifest output already exists")
            result = inventory(args.root)
            fd = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(result, output, indent=2)
                output.write("\n")
            print(f"Inventoried {len(result['objects'])} objects")
        else:
            verify(args.root, args.manifest)
        return 0
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"Private-file DR verification failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
