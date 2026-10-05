#!/usr/bin/env python
"""Create a small Django module using one of the supported architecture presets."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APPS_DIR = ROOT / "apps"

PRESETS = {
    "crud": ("api", "tests", "migrations"),
    "domain": ("api", "services", "selectors", "domain", "tests", "migrations"),
    "integration": (
        "api",
        "services",
        "ports",
        "adapters",
        "tests",
        "migrations",
    ),
    "event-consumer": ("services", "tests"),
}


def validate_name(value: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", value):
        raise argparse.ArgumentTypeError(
            "module name must be snake_case and start with a lowercase letter"
        )
    return value


def write(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def create_module(name: str, preset: str) -> Path:
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

    if preset == "event-consumer":
        write(
            module_dir / "tasks.py",
            '''"""Background task entrypoints.

Keep business logic in services; task functions should stay thin.
"""
''',
        )

    write(
        module_dir / "module.yaml",
        f"""name: {name}
type: {preset}
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

Describe the business capability owned by this module.

## Architecture

Preset: `{preset}`

Only keep directories that contain real responsibilities. Remove generated
layers that are not needed before adding business code.

## Public API

Document externally meaningful endpoints, commands, or events.

## Rules and invariants

Document business rules that an engineer or coding agent must preserve.

## Dependencies

Internal module dependencies belong in `module.yaml -> depends_on`.
External/framework dependencies belong in `module.yaml -> uses`.
""",
    )

    return module_dir


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("name", type=validate_name)
    parser.add_argument("--type", choices=sorted(PRESETS), default="crud")
    args = parser.parse_args()

    try:
        module_dir = create_module(args.name, args.type)
    except FileExistsError as exc:
        parser.error(str(exc))

    print(f"Created {module_dir.relative_to(ROOT)}")
    print("Next:")
    print(f'  1. Add "apps.{args.name}" to LOCAL_APPS if this is a Django app.')
    print("  2. Wire API URLs only when the module exposes HTTP endpoints.")
    print("  3. Remove unused generated layers before adding business code.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
