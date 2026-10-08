#!/usr/bin/env python3
"""Check recency of an operator-provided, authenticated DR recovery manifest."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path


def check_age(path: Path, maximum_hours: int, now: datetime | None = None) -> float:
    if not 0 < maximum_hours <= 24 * 365:
        raise ValueError("Maximum age must be 1-8760 hours")
    if path.is_symlink() or not path.is_file():
        raise ValueError("Manifest must be a regular file")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("Unsupported manifest schema")
    timestamp = data.get("created_at")
    if not isinstance(timestamp, str):
        raise ValueError("Manifest missing created_at")
    created = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if created.tzinfo is None:
        raise ValueError("Timestamp must include a timezone")
    current = now or datetime.now(UTC)
    hours = (current - created).total_seconds() / 3600
    if hours < -0.0833333333:
        raise ValueError("Backup timestamp is in the future")
    if hours > maximum_hours:
        raise ValueError("Backup recovery point exceeded maximum age")
    return max(0.0, hours)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--max-hours", type=int, required=True)
    args = parser.parse_args()
    try:
        hours = check_age(args.manifest, args.max_hours)
        print(f"DR backup age OK: {hours:.2f} hours")
        return 0
    except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"DR backup freshness check failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
