#!/usr/bin/env python
"""Fail on selected backwards-incompatible OpenAPI changes.

This checker is intentionally conservative and deterministic. It compares a base
schema with the candidate schema and reports removals/narrowing that existing
clients can observe.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


def load_document(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not contain an OpenAPI object")
    return data


def _resolve(document: dict[str, Any], schema: Any) -> Any:
    seen: set[str] = set()
    while isinstance(schema, dict) and "$ref" in schema:
        ref = schema["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/") or ref in seen:
            return schema
        seen.add(ref)
        current: Any = document
        for token in ref[2:].split("/"):
            token = token.replace("~1", "/").replace("~0", "~")
            current = current[token]
        schema = current
    return schema


def _parameters(operation: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    result = {}
    for parameter in operation.get("parameters", []):
        if isinstance(parameter, dict):
            name = parameter.get("name")
            location = parameter.get("in")
            if isinstance(name, str) and isinstance(location, str):
                result[(name, location)] = parameter
    return result


def _request_schema(document: dict[str, Any], operation: dict[str, Any]) -> Any:
    body = operation.get("requestBody", {})
    content = body.get("content", {}) if isinstance(body, dict) else {}
    for media in ("application/json", "application/problem+json"):
        entry = content.get(media)
        if isinstance(entry, dict) and "schema" in entry:
            return _resolve(document, entry["schema"])
    return None


def _response_schema(document: dict[str, Any], response: dict[str, Any]) -> Any:
    content = response.get("content", {})
    if not isinstance(content, dict):
        return None
    for media in ("application/json", "application/problem+json"):
        entry = content.get(media)
        if isinstance(entry, dict) and "schema" in entry:
            return _resolve(document, entry["schema"])
    return None


def _compare_schema(
    base_doc: dict[str, Any],
    candidate_doc: dict[str, Any],
    base_schema: Any,
    candidate_schema: Any,
    location: str,
) -> list[str]:
    base_schema = _resolve(base_doc, base_schema)
    candidate_schema = _resolve(candidate_doc, candidate_schema)
    if not isinstance(base_schema, dict) or not isinstance(candidate_schema, dict):
        return []

    findings: list[str] = []
    base_type = base_schema.get("type")
    candidate_type = candidate_schema.get("type")
    if base_type and candidate_type and base_type != candidate_type:
        findings.append(f"{location}: type changed from {base_type!r} to {candidate_type!r}")
        return findings

    base_enum = base_schema.get("enum")
    candidate_enum = candidate_schema.get("enum")
    if isinstance(base_enum, list) and isinstance(candidate_enum, list):
        removed = set(base_enum) - set(candidate_enum)
        if removed:
            findings.append(f"{location}: enum values removed: {sorted(removed)!r}")

    base_props = base_schema.get("properties", {})
    candidate_props = candidate_schema.get("properties", {})
    if isinstance(base_props, dict) and isinstance(candidate_props, dict):
        for name in sorted(set(base_props) - set(candidate_props)):
            findings.append(f"{location}: response/request property removed: {name}")
        base_required = set(base_schema.get("required", []))
        candidate_required = set(candidate_schema.get("required", []))
        for name in sorted(candidate_required - base_required):
            findings.append(f"{location}: property became required: {name}")
        for name in sorted(set(base_props) & set(candidate_props)):
            findings.extend(
                _compare_schema(
                    base_doc,
                    candidate_doc,
                    base_props[name],
                    candidate_props[name],
                    f"{location}.{name}",
                )
            )

    if "items" in base_schema and "items" in candidate_schema:
        findings.extend(
            _compare_schema(
                base_doc,
                candidate_doc,
                base_schema["items"],
                candidate_schema["items"],
                f"{location}[]",
            )
        )
    return findings


def compare(base: dict[str, Any], candidate: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    base_paths = base.get("paths", {})
    candidate_paths = candidate.get("paths", {})
    if not isinstance(base_paths, dict) or not isinstance(candidate_paths, dict):
        return ["OpenAPI paths must be objects"]

    for path in sorted(set(base_paths) - set(candidate_paths)):
        findings.append(f"path removed: {path}")

    for path in sorted(set(base_paths) & set(candidate_paths)):
        base_item = base_paths[path]
        candidate_item = candidate_paths[path]
        if not isinstance(base_item, dict) or not isinstance(candidate_item, dict):
            continue
        base_methods = HTTP_METHODS & set(base_item)
        candidate_methods = HTTP_METHODS & set(candidate_item)
        for method in sorted(base_methods - candidate_methods):
            findings.append(f"operation removed: {method.upper()} {path}")

        for method in sorted(base_methods & candidate_methods):
            base_op = base_item[method]
            candidate_op = candidate_item[method]
            if not isinstance(base_op, dict) or not isinstance(candidate_op, dict):
                continue
            where = f"{method.upper()} {path}"

            base_params = _parameters(base_op)
            candidate_params = _parameters(candidate_op)
            for key, parameter in sorted(base_params.items()):
                if key not in candidate_params:
                    findings.append(f"{where}: parameter removed: {key[1]} {key[0]}")
                    continue
                before_required = bool(parameter.get("required"))
                after_required = bool(candidate_params[key].get("required"))
                if not before_required and after_required:
                    findings.append(f"{where}: parameter became required: {key[1]} {key[0]}")
                findings.extend(
                    _compare_schema(
                        base,
                        candidate,
                        parameter.get("schema", {}),
                        candidate_params[key].get("schema", {}),
                        f"{where} parameter {key[0]}",
                    )
                )

            base_body = base_op.get("requestBody", {})
            candidate_body = candidate_op.get("requestBody", {})
            if isinstance(base_body, dict) and isinstance(candidate_body, dict):
                if not base_body.get("required") and candidate_body.get("required"):
                    findings.append(f"{where}: request body became required")
            base_request = _request_schema(base, base_op)
            candidate_request = _request_schema(candidate, candidate_op)
            if base_request is not None and candidate_request is not None:
                findings.extend(
                    _compare_schema(
                        base,
                        candidate,
                        base_request,
                        candidate_request,
                        f"{where} request",
                    )
                )

            base_responses = base_op.get("responses", {})
            candidate_responses = candidate_op.get("responses", {})
            if isinstance(base_responses, dict) and isinstance(candidate_responses, dict):
                for status in sorted(set(base_responses) - set(candidate_responses)):
                    findings.append(f"{where}: response status removed: {status}")
                for status in sorted(set(base_responses) & set(candidate_responses)):
                    before = base_responses[status]
                    after = candidate_responses[status]
                    if isinstance(before, dict) and isinstance(after, dict):
                        before_schema = _response_schema(base, before)
                        after_schema = _response_schema(candidate, after)
                        if before_schema is not None and after_schema is not None:
                            findings.extend(
                                _compare_schema(
                                    base,
                                    candidate,
                                    before_schema,
                                    after_schema,
                                    f"{where} response {status}",
                                )
                            )
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("base", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--allow-breaking", action="store_true")
    args = parser.parse_args()

    findings = compare(load_document(args.base), load_document(args.candidate))
    if not findings:
        print("OpenAPI compatibility check passed.")
        return 0

    print("OpenAPI compatibility findings:", file=sys.stderr)
    for finding in findings:
        print(f"- {finding}", file=sys.stderr)
    if args.allow_breaking:
        print("compatibility-approved override accepted; findings remain visible.")
        return 0
    print("Breaking API change requires the compatibility-approved PR label.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
