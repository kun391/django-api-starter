#!/usr/bin/env python
"""Validate modular-monolith metadata and the declared dependency graph."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
APPS_DIR = ROOT / "apps"
EXCLUDED_MODULES = {"core"}

MODULE_KINDS = frozenset({"business", "integration", "support"})
ARCHITECTURE_LEVELS = frozenset({"simple", "medium", "complex"})
# Only unambiguous constraints are enforced; module shape is not inferred from
# services.py or other implementation details.
SIMPLE_FORBIDDEN_LAYERS = ("domain", "ports", "adapters")


def business_modules() -> dict[str, Path]:
    """Discover non-core modules, including infrastructure/support modules."""
    return {
        path.name: path
        for path in APPS_DIR.iterdir()
        if path.is_dir()
        and not path.name.startswith("_")
        and path.name not in EXCLUDED_MODULES
    }


def load_metadata(module: str, path: Path) -> tuple[dict, list[str]]:
    errors: list[str] = []
    metadata_path = path / "module.yaml"
    readme_path = path / "README.md"

    if not metadata_path.exists():
        return {}, [f"{module}: missing module.yaml"]
    if not readme_path.exists():
        errors.append(f"{module}: missing README.md")

    try:
        metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return {}, [f"{module}: invalid module.yaml: {exc}"]

    if not isinstance(metadata, dict):
        return {}, [f"{module}: module.yaml must be a mapping"]

    if metadata.get("name") != module:
        errors.append(f"{module}: module.yaml name must match directory name")

    for field, values in (
        ("kind", MODULE_KINDS),
        ("architecture", ARCHITECTURE_LEVELS),
    ):
        value = metadata.get(field)
        if not isinstance(value, str) or value not in values:
            errors.append(
                f"{module}: {field} must be one of: {', '.join(sorted(values))}"
            )

    if "type" in metadata:
        errors.append(
            f"{module}: legacy type is ambiguous; use kind and architecture"
        )

    depends_on = metadata.get("depends_on")
    if not isinstance(depends_on, list) or not all(
        isinstance(item, str) and item for item in depends_on
    ):
        errors.append(f"{module}: depends_on must be a list of module names")
    elif len(depends_on) != len(set(depends_on)):
        errors.append(f"{module}: duplicate depends_on entries")

    if metadata.get("architecture") == "simple":
        for layer in SIMPLE_FORBIDDEN_LAYERS:
            if (path / layer).is_dir():
                errors.append(
                    f"{module}: simple architecture cannot include {layer}/"
                )

    return metadata, errors


def imported_modules(path: Path) -> set[str]:
    imports: set[str] = set()

    for source in path.rglob("*.py"):
        if "migrations" in source.parts:
            continue

        try:
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]

            for name in names:
                parts = name.split(".")
                if len(parts) >= 2 and parts[0] == "apps":
                    imports.add(parts[1])

    return imports


def dependency_cycles(graph: dict[str, set[str]]) -> list[str]:
    """Report deterministic cycle paths (including self-dependencies)."""
    completed: set[str] = set()
    active: set[str] = set()
    stack: list[str] = []
    found: list[str] = []

    def visit(module: str) -> None:
        active.add(module)
        stack.append(module)
        for dependency in sorted(graph[module]):
            if dependency in active:
                start = stack.index(dependency)
                cycle = [*stack[start:], dependency]
                found.append(" -> ".join(cycle))
            elif dependency not in completed:
                visit(dependency)
        stack.pop()
        active.remove(module)
        completed.add(module)

    for module in sorted(graph):
        if module not in completed:
            visit(module)

    return found


def check() -> list[str]:
    modules = business_modules()
    errors: list[str] = []
    metadata_by_module: dict[str, dict] = {}

    for name, path in sorted(modules.items()):
        metadata, module_errors = load_metadata(name, path)
        metadata_by_module[name] = metadata
        errors.extend(module_errors)

    known_modules = set(modules)
    graph: dict[str, set[str]] = {}

    for name, path in sorted(modules.items()):
        metadata = metadata_by_module.get(name, {})
        depends_on = metadata.get("depends_on")
        declared = (
            set(depends_on)
            if isinstance(depends_on, list)
            and all(isinstance(item, str) for item in depends_on)
            else set()
        )

        unknown = declared - known_modules
        if unknown:
            errors.append(
                f"{name}: unknown depends_on entries: {', '.join(sorted(unknown))}"
            )

        graph[name] = declared & known_modules

        actual = imported_modules(path) & known_modules
        undeclared = actual - declared - {name}
        if undeclared:
            errors.append(
                f"{name}: imports undeclared modules: {', '.join(sorted(undeclared))}"
            )

    for cycle in dependency_cycles(graph):
        errors.append(f"dependency cycle: {cycle}")

    return errors


def main() -> int:
    errors = check()
    if not errors:
        print("Architecture checks passed.")
        return 0

    print("Architecture checks failed:", file=sys.stderr)
    for error in errors:
        print(f"- {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
