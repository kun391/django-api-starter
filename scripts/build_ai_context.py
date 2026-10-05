#!/usr/bin/env python
"""Print the smallest useful coding-agent context for one module."""

from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def emit(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(path)

    print(f"\n===== {path.relative_to(ROOT)} =====\n")
    print(path.read_text(encoding="utf-8").rstrip())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("module")
    parser.add_argument(
        "--include",
        action="append",
        default=[],
        help="module-relative source file to include; may be repeated",
    )
    args = parser.parse_args()

    module_dir = ROOT / "apps" / args.module
    if not module_dir.is_dir():
        parser.error(f"unknown module: {args.module}")

    for path in (
        ROOT / "AGENTS.md",
        ROOT / "ARCHITECTURE.md",
        module_dir / "module.yaml",
        module_dir / "README.md",
    ):
        emit(path)

    for relative in args.include:
        path = (module_dir / relative).resolve()
        if module_dir.resolve() not in path.parents:
            parser.error("--include must stay inside the selected module")
        emit(path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
