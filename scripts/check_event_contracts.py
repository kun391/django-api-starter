#!/usr/bin/env python
"""Validate module.yaml event declarations against live subscriber registrations.

Runtime registration is authoritative for consumes. Literal producer emits are
also checked through AST as a conservative additional guard, not a proof of
dynamic paths or behavior.
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

from scripts.check_architecture import business_modules, load_metadata


def _literal_emits(module_dir: Path) -> set[str]:
    """Statically discover constant topics in direct record_outbox_event calls."""
    topics: set[str] = set()
    for path in module_dir.rglob("*.py"):
        if "tests" in path.parts or "migrations" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            else:
                continue
            if name != "record_outbox_event":
                continue
            for kw in node.keywords:
                if kw.arg == "topic" and isinstance(kw.value, ast.Constant):
                    if isinstance(kw.value.value, str):
                        topics.add(kw.value.value)
    return topics


def check_contracts(
    manifests: dict[str, dict],
    runtime_consumes: dict[str, set[str]],
    literal_emits: dict[str, set[str]],
) -> list[str]:
    """Check the public runtime consumer registry and declared producer topics."""
    errors: list[str] = []
    owners: dict[str, str] = {}
    for module, metadata in sorted(manifests.items()):
        for field in ("emits", "consumes"):
            values = metadata.get(field)
            if not isinstance(values, list) or not all(
                isinstance(value, str) and value for value in values
            ):
                errors.append(f"{module}: {field} must be a list of topics")
                continue
            if len(set(values)) != len(values):
                errors.append(f"{module}: duplicated {field} topic")

        emits = metadata.get("emits")
        if isinstance(emits, list):
            for topic in emits:
                if not isinstance(topic, str):
                    continue
                if not topic.startswith(f"{module}."):
                    errors.append(f"{module}: emits topic not owned by module: {topic}")
                if topic in owners and owners[topic] != module:
                    errors.append(f"{module}: duplicate event producer for {topic}")
                owners[topic] = module

    for module, metadata in sorted(manifests.items()):
        values = metadata.get("emits")
        emitted = {v for v in values if isinstance(v, str)} if isinstance(
            values, list
        ) else set()
        actual_literals = literal_emits.get(module, set())
        missing_emits = actual_literals - emitted
        unobserved = emitted - actual_literals
        for topic in sorted(missing_emits):
            errors.append(f"{module}: published literal topic is not declared: {topic}")
        for topic in sorted(unobserved):
            errors.append(f"{module}: declared emits has no literal producer: {topic}")

        consumes = metadata.get("consumes")
        declared = set(consumes) if isinstance(consumes, list) and all(
            isinstance(t, str) for t in consumes
        ) else set()
        runtime = runtime_consumes.get(module, set())
        for topic in sorted(runtime - declared):
            errors.append(f"{module}: runtime subscriber is undeclared: {topic}")
        for topic in sorted(declared - runtime):
            errors.append(f"{module}: declared consumes is not subscribed: {topic}")
        for topic in sorted(declared):
            if topic not in owners:
                errors.append(f"{module}: consumes missing producer: {topic}")

    return errors


def check() -> list[str]:
    from apps.core.outbox import registered_subscriptions, subscriptions_for

    paths = business_modules()
    errors: list[str] = []
    manifests = {}
    literal = {}
    for name, path in sorted(paths.items()):
        metadata, parse_errors = load_metadata(name, path)
        errors.extend(parse_errors)
        manifests[name] = metadata
        literal[name] = _literal_emits(path)

    runtime: dict[str, set[str]] = {name: set() for name in paths}
    for topic in registered_subscriptions():
        for registration in subscriptions_for(topic):
            parts = registration.handler.__module__.split(".")
            if len(parts) >= 3 and parts[0] == "apps" and parts[1] in runtime:
                runtime[parts[1]].add(topic)

    return errors + check_contracts(manifests, runtime, literal)


def main() -> int:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings.test")
    import django

    django.setup()
    errors = check()
    if errors:
        print("Event contract checks failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("Event contract checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
