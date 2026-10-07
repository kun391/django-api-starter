#!/usr/bin/env python
"""Detect migration operations that are unsafe for rolling production releases."""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

DESTRUCTIVE = {"DeleteModel", "RemoveField"}
RENAME = {"RenameField", "RenameModel"}
LOCK_SENSITIVE = {"AddConstraint"}


def changed_migrations(base: str) -> list[Path]:
    output = subprocess.check_output(
        ["git", "diff", "--name-only", "--diff-filter=AM", f"{base}...HEAD"],
        text=True,
    )
    return [
        Path(line)
        for line in output.splitlines()
        if line.startswith("apps/")
        and "/migrations/" in line
        and line.endswith(".py")
        and not line.endswith("/__init__.py")
    ]


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _keyword(call: ast.Call, name: str) -> ast.AST | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def inspect_file(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    findings: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        operation = _call_name(node)
        if operation in DESTRUCTIVE:
            findings.append(
                f"{path}: {operation} is a contract/destructive migration; "
                "use expand/migrate/contract and a later approved cleanup release"
            )
        elif operation in RENAME:
            findings.append(
                f"{path}: {operation} is not rolling-safe; expand with the new "
                "name, migrate callers/data, then contract later"
            )
        elif operation == "AddField":
            field = _keyword(node, "field")
            if isinstance(field, ast.Call):
                null_value = _keyword(field, "null")
                default_value = _keyword(field, "default")
                nullable = isinstance(null_value, ast.Constant) and null_value.value is True
                has_default = default_value is not None
                if not nullable and not has_default:
                    findings.append(
                        f"{path}: AddField appears NOT NULL without a migration "
                        "default; use a nullable/default-safe expand step then backfill"
                    )
        elif operation in LOCK_SENSITIVE:
            findings.append(
                f"{path}: {operation} may lock a large table; document lock/runtime "
                "impact or apply the compatibility-approved override"
            )
        elif operation == "AddIndex":
            index = _keyword(node, "index")
            if isinstance(index, ast.Call) and _call_name(index) != "AddIndexConcurrently":
                findings.append(
                    f"{path}: AddIndex uses the normal transactional index path; "
                    "for large production tables prefer a separate non-atomic "
                    "AddIndexConcurrently migration"
                )
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--allow-breaking", action="store_true")
    args = parser.parse_args()

    files = changed_migrations(args.base)
    findings = [finding for path in files for finding in inspect_file(path)]
    if not findings:
        print(f"Migration safety check passed ({len(files)} changed migrations).")
        return 0

    print("Migration safety findings:", file=sys.stderr)
    for finding in findings:
        print(f"- {finding}", file=sys.stderr)
    if args.allow_breaking:
        print("compatibility-approved override accepted; findings remain visible.")
        return 0
    print("Unsafe migration requires the compatibility-approved PR label.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
