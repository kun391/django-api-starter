#!/usr/bin/env python
"""Scaffold Django-first modules without speculative package layers."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APPS_DIR = ROOT / "apps"

# Presets describe a starting scaffold, not the module's responsibility.
# Add selectors, providers, transports and domain/ports/adapters only when
# application behavior justifies them.
PRESETS = {
    "crud": ("api", "tests", "migrations"),
    "domain": ("api", "tests", "migrations"),
    "integration": ("api", "tests", "migrations"),
    "event-consumer": ("tests",),
}

PRESET_METADATA = {
    "crud": ("business", "simple"),
    "domain": ("business", "medium"),
    "integration": ("integration", "medium"),
    "event-consumer": ("integration", "medium"),
}
MODULE_KINDS = ("business", "integration", "support")
ARCHITECTURE_LEVELS = ("simple", "medium", "complex")


def validate_name(value: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", value):
        raise argparse.ArgumentTypeError(
            "module name must be snake_case and start with a lowercase letter"
        )
    return value


def write(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def create_module(
    name: str,
    preset: str,
    *,
    kind: str | None = None,
    architecture: str | None = None,
) -> Path:
    try:
        validate_name(name)
    except argparse.ArgumentTypeError as exc:
        raise ValueError(str(exc)) from exc
    if name == "core":
        raise ValueError("core is reserved for platform primitives")
    if preset not in PRESETS:
        raise ValueError(f"Unknown scaffold preset: {preset}")
    default_kind, default_architecture = PRESET_METADATA[preset]
    module_kind = default_kind if kind is None else kind
    module_architecture = (
        default_architecture if architecture is None else architecture
    )
    if module_kind not in MODULE_KINDS:
        raise ValueError(f"Invalid module kind: {module_kind}")
    if module_architecture not in ARCHITECTURE_LEVELS:
        raise ValueError(f"Invalid architecture level: {module_architecture}")

    module_dir = APPS_DIR / name
    if module_dir.exists():
        raise FileExistsError(f"module already exists: {module_dir}")

    write(module_dir / "__init__.py")
    write(
        module_dir / "apps.py",
        f'''from django.apps import AppConfig


class {''.join(part.title() for part in name.split('_'))}Config(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.{name}"
''',
    )

    if preset != "event-consumer":
        write(
            module_dir / "models.py",
            '''"""Database models owned by this module."""
''',
        )

    for directory in PRESETS[preset]:
        write(module_dir / directory / "__init__.py")

    if "api" in PRESETS[preset]:
        write(module_dir / "api" / "urls.py", '''"""Module API routes."""

urlpatterns = []
''')

    if preset in {"domain", "integration", "event-consumer"}:
        write(
            module_dir / "services.py",
            '''"""Application services; add behavior only when it is needed."""
''',
        )

    if preset == "event-consumer":
        write(
            module_dir / "tasks.py",
            '''"""Background task entrypoints.

Keep task functions thin; delegate business logic to services.py.
"""
''',
        )

    write(
        module_dir / "module.yaml",
        f"""name: {name}
kind: {module_kind}
architecture: {module_architecture}
depends_on: []
uses: []
emits: []
consumes: []
""",
    )
    write(
        module_dir / "README.md",
        f"""# {name.replace('_', ' ').title()}

## Purpose

Describe the business or integration capability owned by this module.

## Architecture

Kind: `{module_kind}`
Architecture level: `{module_architecture}`
Scaffold preset: `{preset}`

The preset is a starting point, not a mandatory pattern. Prefer flat files
(`services.py`, `selectors.py`, `provider.py`, `transport.py`) and add
layers only when there are real responsibilities. Do not create empty
`domain/`, `ports/` or `adapters/` packages.

## Public API

Document externally meaningful endpoints, commands, or events.

## Rules and invariants

Document business rules that an engineer or coding agent must preserve.

## Dependencies

Internal Python module dependencies belong in `module.yaml -> depends_on`.
External/framework dependencies belong in `module.yaml -> uses`.
Event subscriptions belong in `consumes`, not `depends_on`.
""",
    )

    return module_dir


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("name", type=validate_name)
    parser.add_argument("--preset", choices=sorted(PRESETS))
    parser.add_argument(
        "--type", dest="legacy_type", choices=sorted(PRESETS),
        help="Deprecated alias for --preset (one template release only)",
    )
    parser.add_argument("--kind", choices=MODULE_KINDS)
    parser.add_argument("--architecture", choices=ARCHITECTURE_LEVELS)
    args = parser.parse_args()

    if args.preset is not None and args.legacy_type is not None:
        parser.error("Use --preset or deprecated --type, not both")
    if args.legacy_type is not None:
        print("warning: --type is deprecated; use --preset", file=sys.stderr)

    preset = args.preset or args.legacy_type or "crud"
    try:
        module_dir = create_module(
            args.name, preset, kind=args.kind, architecture=args.architecture,
        )
    except (FileExistsError, ValueError) as exc:
        parser.error(str(exc))

    print(f"Created {module_dir.relative_to(ROOT)}")
    print("Next:")
    print(f'  1. Add "apps.{args.name}" to LOCAL_APPS if this is a Django app.')
    print("  2. Wire API URLs only when the module exposes HTTP endpoints.")
    print("  3. Delete unused scaffolding before adding business code.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
