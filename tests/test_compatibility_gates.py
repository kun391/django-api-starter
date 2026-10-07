import ast
from pathlib import Path

from scripts.check_migration_safety import inspect_file
from scripts.check_openapi_compatibility import compare


def operation(*, response_properties=None, request_required=None):
    request_schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}, "note": {"type": "string"}},
        "required": request_required or [],
    }
    response_schema = {
        "type": "object",
        "properties": response_properties
        or {"id": {"type": "integer"}, "name": {"type": "string"}},
        "required": ["id", "name"],
    }
    return {
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": request_schema}},
        },
        "responses": {
            "200": {
                "description": "OK",
                "content": {"application/json": {"schema": response_schema}},
            }
        },
    }


def document(op):
    return {"openapi": "3.0.3", "paths": {"/api/v1/items/": {"post": op}}}


def test_openapi_additive_changes_pass():
    base = document(operation())
    candidate = document(operation())
    candidate["paths"]["/api/v1/new/"] = {"get": {"responses": {"200": {"description": "OK"}}}}
    candidate["paths"]["/api/v1/items/"]["post"]["responses"]["201"] = {
        "description": "Created"
    }
    candidate["paths"]["/api/v1/items/"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]["properties"]["extra"] = {"type": "string"}
    assert compare(base, candidate) == []


def test_openapi_removed_operation_and_response_field_fail():
    base = document(operation())
    candidate = document(
        operation(response_properties={"id": {"type": "integer"}})
    )
    findings = compare(base, candidate)
    assert any("property removed: name" in finding for finding in findings)

    removed = {"openapi": "3.0.3", "paths": {"/api/v1/items/": {}}}
    assert "operation removed: POST /api/v1/items/" in compare(base, removed)


def test_openapi_required_response_becoming_optional_fails():
    base = document(operation())
    candidate = document(operation())
    response = candidate["paths"]["/api/v1/items/"]["post"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    response["required"] = ["id"]
    findings = compare(base, candidate)
    assert any(
        "required response property became optional: name" in finding
        for finding in findings
    )


def test_openapi_new_required_input_and_narrowed_enum_fail():
    base = document(operation())
    candidate = document(operation(request_required=["name"]))
    request = candidate["paths"]["/api/v1/items/"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    base_request = base["paths"]["/api/v1/items/"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    base_request["properties"]["kind"] = {"type": "string", "enum": ["a", "b"]}
    request["properties"]["kind"] = {"type": "string", "enum": ["a"]}
    findings = compare(base, candidate)
    assert any("property became required: name" in finding for finding in findings)
    assert any("enum values removed" in finding for finding in findings)


def write_migration(tmp_path: Path, operation: str) -> Path:
    path = tmp_path / "0002_change.py"
    path.write_text(
        "from django.db import migrations, models\n"
        "class Migration(migrations.Migration):\n"
        f"    operations = [{operation}]\n",
        encoding="utf-8",
    )
    ast.parse(path.read_text())
    return path


def test_migration_gate_allows_nullable_expand(tmp_path):
    path = write_migration(
        tmp_path,
        "migrations.AddField(model_name='item', name='note', "
        "field=models.TextField(null=True))",
    )
    assert inspect_file(path) == []


def test_migration_gate_blocks_destructive_and_rename(tmp_path):
    remove = write_migration(
        tmp_path,
        "migrations.RemoveField(model_name='item', name='legacy')",
    )
    assert any("RemoveField" in finding for finding in inspect_file(remove))

    rename = write_migration(
        tmp_path,
        "migrations.RenameField(model_name='item', old_name='a', new_name='b')",
    )
    assert any("RenameField" in finding for finding in inspect_file(rename))


def test_migration_gate_blocks_required_add_without_default(tmp_path):
    path = write_migration(
        tmp_path,
        "migrations.AddField(model_name='item', name='required', "
        "field=models.CharField(max_length=20))",
    )
    assert any("NOT NULL" in finding for finding in inspect_file(path))
