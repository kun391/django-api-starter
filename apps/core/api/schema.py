"""Shared HTTP contract additions to the inferred resource schemas."""

from http import HTTPStatus

from drf_spectacular.utils import OpenApiParameter

IDEMPOTENCY_KEY_PARAMETER = OpenApiParameter(
    name="Idempotency-Key",
    location=OpenApiParameter.HEADER,
    required=True,
    type={"type": "string", "minLength": 1, "maxLength": 128, "pattern": "^[A-Za-z0-9._:-]+$"},
    description="Unique per logical operation. Reuse the same key only for retries of the same JSON request.",
)


def versioned_api_only(endpoints):
    # Operational probes are not part of the versioned client API.
    return [endpoint for endpoint in endpoints if endpoint[0].startswith("/api/v1/")]


def add_api_contract(result, generator, request, public):
    schemas = result.setdefault("components", {}).setdefault("schemas", {})
    # Contract tests compare these shared wire fields with real error responses.
    schemas["APIProblemError"] = {
        "type": "object",
        "required": ["attr", "code", "detail"],
        "properties": {
            "attr": {"type": "string", "nullable": True},
            "code": {"type": "string"},
            "detail": {"type": "string"},
        },
    }
    schemas["APIProblem"] = {
        "type": "object",
        "required": ["type", "title", "status", "code", "detail", "errors", "request_id"],
        "properties": {
            "type": {"type": "string", "format": "uri", "example": "about:blank"},
            "title": {"type": "string"},
            "status": {"type": "integer", "minimum": 400, "maximum": 599},
            "code": {"type": "string"},
            "detail": {"type": "string"},
            "errors": {"type": "array", "items": {"$ref": "#/components/schemas/APIProblemError"}},
            "request_id": {"type": "string", "nullable": True},
        },
    }
    for path, path_item in result.get("paths", {}).items():
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                continue
            parameters = operation.setdefault("parameters", [])
            if not any(p.get("name") == "X-Request-ID" for p in parameters):
                parameters.append({
                    "in": "header",
                    "name": "X-Request-ID",
                    "required": False,
                    "description": "Optional correlation ID. Invalid values are replaced by the server.",
                    "schema": {"type": "string", "pattern": "^[A-Za-z0-9._-]{1,128}$", "maxLength": 128},
                })
            responses = operation.setdefault("responses", {})
            statuses = {400, 401, 403, 405, 406, 429, 500}
            if "{" in path or method == "get":
                statuses.add(404)
            if method in {"post", "put", "patch"}:
                statuses.add(415)
            idempotent = any(p.get("name") == "Idempotency-Key" for p in parameters)
            if idempotent:
                statuses.update({409, 422})
            for status_code in sorted(statuses):
                responses.setdefault(str(status_code), {
                    "description": HTTPStatus(status_code).phrase,
                    "content": {"application/problem+json": {"schema": {"$ref": "#/components/schemas/APIProblem"}}},
                })
            for status_code, response in responses.items():
                headers = response.setdefault("headers", {})
                headers["X-Request-ID"] = {"schema": {"type": "string"}}
                if status_code == "401":
                    headers["WWW-Authenticate"] = {"schema": {"type": "string"}}
                if status_code == "429" or (idempotent and status_code == "409"):
                    headers["Retry-After"] = {"schema": {"type": "string"}}
                if idempotent and status_code.startswith("2"):
                    headers["Idempotency-Replayed"] = {"schema": {"type": "string", "enum": ["true", "false"]}}
    return result
