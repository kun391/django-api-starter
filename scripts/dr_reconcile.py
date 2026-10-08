"""Read-only recovery checks for PrivateFile READY metadata and object inventory.

Run only against an isolated restored DB and an independent recovered snapshot.
No upload, delete, retention or bucket mutation is performed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


def _key(identifier: str) -> str:
    normalized = identifier.replace("-", "").lower()
    if not re.fullmatch(r"[0-9a-f]{32}", normalized):
        raise ValueError("Invalid private file identifier")
    return f"objects/{normalized[:2]}/{normalized}"


def expected(records: list[dict]) -> dict[str, tuple[int, str]]:
    result = {}
    for row in records:
        if row["state"] != "ready":
            continue
        key = _key(str(row["id"]))
        length, checksum = row["size"], row["sha256"]
        if type(length) is not int or not 0 < length <= 16 * 1024 * 1024:
            raise ValueError("Invalid READY file size")
        if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
            raise ValueError("Invalid READY checksum")
        if key in result:
            raise ValueError("Duplicate READY file")
        result[key] = (length, checksum)
    return result


def compare(records: list[dict], objects: list[dict]) -> dict:
    wanted = expected(records)
    found = {}
    for item in objects:
        key, length, checksum = item["key"], item["bytes"], item["sha256"]
        if not isinstance(key, str) or not re.fullmatch(r"objects/[0-9a-f]{2}/[0-9a-f]{32}", key):
            raise ValueError("Invalid object key")
        if key.split("/")[1] != key.split("/")[2][:2]:
            raise ValueError("Invalid object prefix")
        if type(length) is not int or length < 0:
            raise ValueError("Invalid object size")
        if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
            raise ValueError("Invalid object checksum")
        if key in found:
            raise ValueError("Duplicate object key")
        found[key] = (length, checksum)
    missing = sorted(wanted.keys() - found.keys())
    corrupt = sorted(key for key in wanted.keys() & found.keys() if wanted[key] != found[key])
    unreferenced = sorted(found.keys() - wanted.keys())
    return {
        "ready_count": len(wanted),
        "object_count": len(found),
        "missing": missing,
        "mismatched": corrupt,
        "unreferenced": unreferenced,
        "ok": not (missing or corrupt),
    }


def load(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Manifest must be a regular file")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("Unsupported manifest schema")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-manifest", type=Path, required=True)
    parser.add_argument("--objects-manifest", type=Path, required=True)
    args = parser.parse_args()
    try:
        db = load(args.database_manifest)
        storage = load(args.objects_manifest)
        if not isinstance(db.get("files"), list) or not isinstance(storage.get("objects"), list):
            raise ValueError("Missing manifest contents")
        result = compare(db["files"], storage["objects"])
        print(json.dumps(result, sort_keys=True))
        return 0 if result["ok"] else 1
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
