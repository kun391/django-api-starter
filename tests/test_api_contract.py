from typing import Any

import pytest
from django.core.management import call_command
from django.urls import reverse
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.validation import validate_schema

from apps.core.api.schema import add_api_contract


def assert_problem(response, status_code, code):
    assert response.status_code == status_code
    assert response["Content-Type"] == "application/problem+json"
    assert response["Cache-Control"] == "no-store"
    body = response.json()
    assert body["type"] == "about:blank"
    assert body["status"] == status_code
    assert body["code"] == code
    assert body["request_id"] == response["X-Request-ID"]
    assert isinstance(body["errors"], list)
    return body


@pytest.mark.django_db
class TestAPIContract:
    def test_unauthenticated_is_401(self, api_client):
        response = api_client.get(reverse("user-me"), HTTP_X_REQUEST_ID="contract-test-01")
        assert_problem(response, 401, "not_authenticated")
        assert response["WWW-Authenticate"] == "Token"
        assert response["X-Request-ID"] == "contract-test-01"

    def test_bad_token_is_401(self, api_client):
        response = api_client.get(reverse("user-me"), HTTP_AUTHORIZATION="Token invalid-token")
        assert_problem(response, 401, "authentication_failed")

    def test_forbidden(self, authenticated_client):
        assert_problem(authenticated_client.get(reverse("user-list")), 403, "permission_denied")

    def test_not_found_at_router_and_object_levels(self, admin_client):
        assert_problem(admin_client.get("/api/v1/does-not-exist/"), 404, "not_found")
        assert_problem(admin_client.get(reverse("user-detail", kwargs={"pk": 99999999})), 404, "not_found")

    def test_method_not_allowed_preserves_allow_header(self, admin_client):
        response = admin_client.delete(reverse("user-list"))
        assert_problem(response, 405, "method_not_allowed")
        assert "GET" in response["Allow"]

    def test_malformed_json(self, api_client):
        response = api_client.post(reverse("user-list"), '{"broken":', content_type="application/json")
        assert_problem(response, 400, "parse_error")

    def test_unsupported_content_type(self, api_client):
        response = api_client.post(reverse("user-list"), "hello", content_type="text/plain")
        assert_problem(response, 415, "unsupported_media_type")

    def test_unsupported_accept_still_returns_json_problem(self, admin_client):
        response = admin_client.get(reverse("user-list"), HTTP_ACCEPT="text/html")
        assert_problem(response, 406, "not_acceptable")

    def test_session_auth_still_requires_csrf(self, api_client, user):
        api_client.handler.enforce_csrf_checks = True
        api_client.force_login(user)
        response = api_client.patch(reverse("user-update-me"), {"first_name": "No CSRF"}, format="json")
        assert_problem(response, 403, "permission_denied")
        user.refresh_from_db()
        assert user.first_name != "No CSRF"

    def test_nested_validation_and_retry_after(self, api_client, settings):
        settings.ROOT_URLCONF = "tests.phase7_urls"
        response = api_client.get("/api/v1/test-failure/validation/")
        body = assert_problem(response, 400, "validation_error")
        assert {error["attr"] for error in body["errors"]} == {"items.0.email", None}
        response = api_client.get("/api/v1/test-failure/throttle/")
        assert_problem(response, 429, "throttled")
        assert response["Retry-After"] == "7"

    @pytest.mark.parametrize("kind,status_code,code", [("unexpected", 500, "internal_error"), ("bad-request", 400, "bad_request")])
    def test_framework_failures_do_not_leak_details(self, api_client, settings, caplog, kind, status_code, code):
        settings.ROOT_URLCONF = "tests.phase7_urls"
        api_client.raise_request_exception = False
        response = api_client.get(f"/api/v1/test-failure/{kind}/", HTTP_X_REQUEST_ID="error-trace-7")
        assert_problem(response, status_code, code)
        assert b"private-internal-reason" not in response.content
        assert b"Traceback" not in response.content
        assert "private-internal-reason" in caplog.text

    def test_non_api_404_is_not_rewritten(self, api_client):
        response = api_client.get("/missing-html-page/")
        assert response.status_code == 404
        assert response["Content-Type"].startswith("text/html")

    def test_cors_supports_contract_headers(self, api_client):
        response = api_client.options(
            reverse("user-list"),
            HTTP_ORIGIN="http://localhost:3000",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS="idempotency-key,x-request-id",
        )
        allowed = response["Access-Control-Allow-Headers"].lower()
        assert "idempotency-key" in allowed and "x-request-id" in allowed

    def test_schema_validates_without_warnings(self, tmp_path):
        call_command("spectacular", file=str(tmp_path / "schema.yaml"), validate=True, fail_on_warn=True)

    def test_schema_matches_resources_and_errors(self, api_client):
        schema = SchemaGenerator().get_schema(request=None, public=True)
        validate_schema(schema)
        paths = schema["paths"]
        assert paths and all(path.startswith("/api/v1/") for path in paths)
        users = paths["/api/v1/users/"]
        query = {p["name"]: p for p in users["get"]["parameters"]}
        assert {"page", "page_size", "ordering", "search", "email", "username", "is_active"} <= query.keys()
        assert query["page_size"]["schema"]["maximum"] == 100
        assert "201" in users["post"]["responses"]
        success = users["post"]["responses"]["201"]["content"]["application/json"]["schema"]
        assert success["$ref"].endswith("/User")
        token = paths["/api/v1/auth/token/"]["post"]["responses"]["200"]
        assert token["content"]["application/json"]["schema"]["$ref"].endswith("/Token")
        problem = users["get"]["responses"]["400"]["content"]["application/problem+json"]["schema"]
        assert problem["$ref"].endswith("/APIProblem")
        actual = api_client.get("/api/v1/missing/").json()
        assert set(actual) == set(schema["components"]["schemas"]["APIProblem"]["required"])
        # No endpoint silently opts into replay, especially credential endpoints.
        for path_item in paths.values():
            for operation in path_item.values():
                assert not any(p.get("name") == "Idempotency-Key" for p in operation.get("parameters", []))


def test_idempotency_schema_is_opt_in():
    document: dict[str, Any] = {"paths": {"/api/v1/example/": {"post": {
        "parameters": [{"name": "Idempotency-Key", "in": "header"}],
        "responses": {"201": {"description": "Created"}},
    }}}}
    add_api_contract(document, None, None, True)
    responses = document["paths"]["/api/v1/example/"]["post"]["responses"]
    assert {"409", "422"} <= responses.keys()
    assert "Retry-After" in responses["409"]["headers"]
    assert "Idempotency-Replayed" in responses["201"]["headers"]
