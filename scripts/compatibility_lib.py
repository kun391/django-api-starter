"""Compatibility gate helpers shared by CI scripts and tests."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


@dataclass(frozen=True)
class MigrationFinding:
    path: str
    operation: str
    level: str
    detail: str


def load_openapi(path: str | Path) -> dict[str, Any]:
    document = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"{path}: expected an OpenAPI object")
    return document


def _resolve_ref(document: dict[str, Any], value: Any) -> Any:
    seen: set[str] = set()
    while isinstance(value, dict) and "$ref" in value:
        ref = value["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/"):
            return value
        if ref in seen:
            raise ValueError(f"cyclic local OpenAPI ref: {ref}")
        seen.add(ref)
        target: Any = document
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            target = target[part]
        value = target
    return value


def _merged_schema(document: dict[str, Any], schema: Any) -> dict[str, Any]:
    schema = _resolve_ref(document, schema)
    if not isinstance(schema, dict):
        return {}

    if "allOf" not in schema:
        return schema

    merged: dict[str, Any] = {
        key: value for key, value in schema.items() if key != "allOf"
    }
    properties: dict[str, Any] = dict(merged.get("properties") or {})
    required: set[str] = set(merged.get("required") or [])
    for part in schema["allOf"]:
        resolved = _merged_schema(document, part)
        properties.update(resolved.get("properties") or {})
        required.update(resolved.get("required") or [])
        for key in ("type", "enum", "items", "nullable"):
            if key in resolved and key not in merged:
                merged[key] = resolved[key]
    if properties:
        merged["properties"] = properties
    if required:
        merged["required"] = sorted(required)
    return merged


def _schema_breaks(
    base_document: dict[str, Any],
    current_document: dict[str, Any],
    base_schema: Any,
    current_schema: Any,
    *,
    direction: str,
    location: str,
) -> list[str]:
    base = _merged_schema(base_document, base_schema)
    current = _merged_schema(current_document, current_schema)
    breaks: list[str] = []

    base_type = base.get("type")
    current_type = current.get("type")
    if base_type and current_type and base_type != current_type:
        breaks.append(
            f"{location}: schema type changed from {base_type!r} to {current_type!r}"
        )

    base_enum = base.get("enum")
    current_enum = current.get("enum")
    if isinstance(base_enum, list) and isinstance(current_enum, list):
        removed = [value for value in base_enum if value not in current_enum]
        if removed:
            breaks.append(f"{location}: enum values removed: {removed!r}")

    base_properties = base.get("properties") or {}
    current_properties = current.get("properties") or {}
    if isinstance(base_properties, dict) and isinstance(current_properties, dict):
        for name, child in base_properties.items():
            if name not in current_properties:
                breaks.append(f"{location}.{name}: property removed")
                continue
            breaks.extend(
                _schema_breaks(
                    base_document,
                    current_document,
                    child,
                    current_properties[name],
                    direction=direction,
                    location=f"{location}.{name}",
                )
            )

        base_required = set(base.get("required") or [])
        current_required = set(current.get("required") or [])
        if direction == "request":
            for name in sorted(current_required - base_required):
                breaks.append(f"{location}.{name}: request field became required")
        else:
            for name in sorted(base_required - current_required):
                breaks.append(
                    f"{location}.{name}: response field is no longer guaranteed"
                )

    base_items = base.get("items")
    current_items = current.get("items")
    if base_items is not None and current_items is not None:
        breaks.extend(
            _schema_breaks(
                base_document,
                current_document,
                base_items,
                current_items,
                direction=direction,
                location=f"{location}[]",
            )
        )

    if direction == "request" and base.get("nullable") is True:
        if current.get("nullable") is False:
            breaks.append(f"{location}: request no longer accepts null")

    return breaks


def _parameters(
    document: dict[str, Any],
    path_item: dict[str, Any],
    operation: dict[str, Any],
) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in [*(path_item.get("parameters") or []), *(operation.get("parameters") or [])]:
        parameter = _resolve_ref(document, raw)
        if not isinstance(parameter, dict):
            continue
        name = parameter.get("name")
        location = parameter.get("in")
        if isinstance(name, str) and isinstance(location, str):
            result[(location, name)] = parameter
    return result


def compare_openapi(
    base_document: dict[str, Any],
    current_document: dict[str, Any],
) -> list[str]:
    """Return backwards-incompatible changes from base -> current."""
    breaks: list[str] = []
    base_paths = base_document.get("paths") or {}
    current_paths = current_document.get("paths") or {}

    for path, base_path_item in base_paths.items():
        if path not in current_paths:
            breaks.append(f"{path}: path removed")
            continue
        current_path_item = current_paths[path]
        if not isinstance(base_path_item, dict) or not isinstance(current_path_item, dict):
            continue

        for method, base_operation in base_path_item.items():
            if method not in HTTP_METHODS:
                continue
            if method not in current_path_item:
                breaks.append(f"{method.upper()} {path}: operation removed")
                continue

            current_operation = current_path_item[method]
            if not isinstance(base_operation, dict) or not isinstance(current_operation, dict):
                continue
            prefix = f"{method.upper()} {path}"

            base_security = base_operation.get(
                "security",
                base_document.get("security"),
            )
            current_security = current_operation.get(
                "security",
                current_document.get("security"),
            )
            if base_security == [] and current_security not in (None, []):
                breaks.append(f"{prefix}: public operation now requires security")

            base_params = _parameters(
                base_document,
                base_path_item,
                base_operation,
            )
            current_params = _parameters(
                current_document,
                current_path_item,
                current_operation,
            )
            for key, parameter in base_params.items():
                if key not in current_params:
                    breaks.append(
                        f"{prefix}: parameter {key[0]}:{key[1]} removed"
                    )
                    continue
                new_parameter = current_params[key]
                if not parameter.get("required", False) and new_parameter.get(
                    "required",
                    False,
                ):
                    breaks.append(
                        f"{prefix}: parameter {key[0]}:{key[1]} became required"
                    )
                breaks.extend(
                    _schema_breaks(
                        base_document,
                        current_document,
                        parameter.get("schema", {}),
                        new_parameter.get("schema", {}),
                        direction="request",
                        location=f"{prefix} parameter {key[0]}:{key[1]}",
                    )
                )
            for key, parameter in current_params.items():
                if key not in base_params and parameter.get("required", False):
                    breaks.append(
                        f"{prefix}: new required parameter {key[0]}:{key[1]}"
                    )

            base_body = base_operation.get("requestBody")
            current_body = current_operation.get("requestBody")
            if base_body is None and current_body is not None:
                resolved = _resolve_ref(current_document, current_body)
                if isinstance(resolved, dict) and resolved.get("required", False):
                    breaks.append(f"{prefix}: request body became required")
            elif base_body is not None:
                if current_body is None:
                    breaks.append(f"{prefix}: request body contract removed")
                else:
                    base_body = _resolve_ref(base_document, base_body)
                    current_body = _resolve_ref(current_document, current_body)
                    if isinstance(base_body, dict) and isinstance(current_body, dict):
                        if not base_body.get("required", False) and current_body.get(
                            "required",
                            False,
                        ):
                            breaks.append(f"{prefix}: request body became required")
                        base_content = base_body.get("content") or {}
                        current_content = current_body.get("content") or {}
                        for media_type, media in base_content.items():
                            if media_type not in current_content:
                                breaks.append(
                                    f"{prefix}: request media type {media_type} removed"
                                )
                                continue
                            breaks.extend(
                                _schema_breaks(
                                    base_document,
                                    current_document,
                                    (media or {}).get("schema", {}),
                                    (current_content[media_type] or {}).get("schema", {}),
                                    direction="request",
                                    location=f"{prefix} request {media_type}",
                                )
                            )

            base_responses = base_operation.get("responses") or {}
            current_responses = current_operation.get("responses") or {}
            for status_code, response in base_responses.items():
                if status_code not in current_responses:
                    breaks.append(f"{prefix}: response status {status_code} removed")
                    continue
                base_response = _resolve_ref(base_document, response)
                current_response = _resolve_ref(
                    current_document,
                    current_responses[status_code],
                )
                if not isinstance(base_response, dict) or not isinstance(
                    current_response,
                    dict,
                ):
                    continue
                base_content = base_response.get("content") or {}
                current_content = current_response.get("content") or {}
                for media_type, media in base_content.items():
                    if media_type not in current_content:
                        breaks.append(
                            f"{prefix} {status_code}: response media type "
                            f"{media_type} removed"
                        )
                        continue
                    breaks.extend(
                        _schema_breaks(
                            base_document,
                            current_document,
                            (media or {}).get("schema", {}),
                            (current_content[media_type] or {}).get("schema", {}),
                            direction="response",
                            location=(
                                f"{prefix} response {status_code} {media_type}"
                            ),
                        )
                    )

    return sorted(set(breaks))


def _call_name(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    function = node.func
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        return function.attr
    return None


def _keyword(call: ast.Call, name: str) -> ast.AST | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _literal_bool(node: ast.AST | None) -> bool | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        return node.value
    return None


def _field_call(operation: ast.Call) -> ast.Call | None:
    field = _keyword(operation, "field")
    return field if isinstance(field, ast.Call) else None


def scan_migration_source(source: str, path: str) -> list[MigrationFinding]:
    """Classify migration operations that need compatibility review."""
    tree = ast.parse(source, filename=path)
    findings: list[MigrationFinding] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "operations"
            for target in node.targets
        ):
            continue
        if not isinstance(node.value, (ast.List, ast.Tuple)):
            continue

        for operation in node.value.elts:
            if not isinstance(operation, ast.Call):
                continue
            name = _call_name(operation)
            if not name:
                continue

            if name in {"RemoveField", "DeleteModel", "RenameField", "RenameModel"}:
                findings.append(
                    MigrationFinding(
                        path,
                        name,
                        "block",
                        "destructive/rename migration; use expand -> migrate -> "
                        "contract across releases",
                    )
                )
                continue

            if name == "AddField":
                field = _field_call(operation)
                if field is None:
                    findings.append(
                        MigrationFinding(
                            path,
                            name,
                            "block",
                            "field shape is not statically reviewable",
                        )
                    )
                    continue
                null = _literal_bool(_keyword(field, "null"))
                has_db_default = _keyword(field, "db_default") is not None
                if null is not True and not has_db_default:
                    findings.append(
                        MigrationFinding(
                            path,
                            name,
                            "block",
                            "new non-null field has no database default; expand "
                            "with null=True or db_default before enforcing",
                        )
                    )
                continue

            if name == "AlterField":
                field = _field_call(operation)
                if field is None:
                    findings.append(
                        MigrationFinding(
                            path,
                            name,
                            "review",
                            "field alteration requires compatibility review",
                        )
                    )
                    continue
                null = _literal_bool(_keyword(field, "null"))
                unique = _literal_bool(_keyword(field, "unique"))
                primary_key = _literal_bool(_keyword(field, "primary_key"))
                if null is False or unique is True or primary_key is True:
                    findings.append(
                        MigrationFinding(
                            path,
                            name,
                            "block",
                            "constraint-tightening AlterField requires staged rollout",
                        )
                    )
                else:
                    findings.append(
                        MigrationFinding(
                            path,
                            name,
                            "review",
                            "verify type/length changes do not narrow existing data",
                        )
                    )
                continue

            if name in {"AddIndex", "AddConstraint"}:
                findings.append(
                    MigrationFinding(
                        path,
                        name,
                        "block",
                        "may lock/scan a production table; use a PostgreSQL-safe "
                        "staged/concurrent strategy",
                    )
                )
                continue

            if name in {"RunSQL", "RunPython", "SeparateDatabaseAndState"}:
                findings.append(
                    MigrationFinding(
                        path,
                        name,
                        "review",
                        "custom migration logic requires explicit rollout review",
                    )
                )

    return findings
