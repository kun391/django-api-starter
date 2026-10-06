"""Opt-in HTTP validation without bypassing authentication or authorization."""

import pytest
from django.http import StreamingHttpResponse
from django.urls import path
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, serializers
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.test import APIClient

from apps.core.api.conditional import IF_NONE_MATCH_PARAMETER, ConditionalGetMixin

pytestmark = pytest.mark.django_db


class ReadModelSerializer(serializers.Serializer):
    label = serializers.CharField()


class ReadModelView(ConditionalGetMixin):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses=ReadModelSerializer, parameters=[IF_NONE_MATCH_PARAMETER])
    def get(self, request):
        mode = request.query_params.get("mode")
        if mode == "invalid":
            raise ValidationError({"label": "Invalid label."})
        if mode == "missing":
            raise NotFound()
        if mode == "stream":
            return StreamingHttpResponse(iter([b"stream"]))
        response = Response({"label": request.query_params.get("label", "published")})
        if mode == "no-store":
            response["Cache-Control"] = "no-store"
        if mode == "custom-policy":
            response["Cache-Control"] = "private, max-age=10"
        if mode == "custom-etag":
            response["ETag"] = '"owned-by-view"'
        if mode == "cookie":
            response.set_cookie("example", "value")
        if mode == "html":
            response.content_type = "text/html"
        return response

    @extend_schema(request=ReadModelSerializer, responses=ReadModelSerializer)
    def post(self, request):
        return Response({"label": "changed"})


class StaffReadModelView(ReadModelView):
    permission_classes = [permissions.IsAdminUser]


# Only test routes; this phase does not add dummy business resources to production.
urlpatterns = [
    path("api/v1/catalog/", ReadModelView.as_view()),
    path("api/v1/staff-catalog/", StaffReadModelView.as_view()),
]


@pytest.fixture(autouse=True)
def test_urls(settings):
    settings.ROOT_URLCONF = __name__


def test_etag_and_304_keep_metadata_and_current_request_id(authenticated_client):
    first = authenticated_client.get("/api/v1/catalog/", HTTP_X_REQUEST_ID="attempt-one")
    assert first.status_code == 200
    assert first["ETag"].startswith('W/"')
    response = authenticated_client.get(
        "/api/v1/catalog/", HTTP_IF_NONE_MATCH=first["ETag"], HTTP_X_REQUEST_ID="attempt-two",
    )
    assert response.status_code == 304
    assert response.content == b""
    assert "Content-Type" not in response
    assert response["ETag"] == first["ETag"]
    assert response["Cache-Control"] == "private, no-cache, must-revalidate"
    assert response["X-Request-ID"] == "attempt-two"
    assert {"authorization", "cookie", "accept", "accept-language"} <= {
        item.strip().lower() for item in response["Vary"].split(",")
    }


@pytest.mark.parametrize("header_kind", ["weak", "strong", "list", "wildcard"])
def test_if_none_match_uses_weak_comparison(authenticated_client, header_kind):
    etag = authenticated_client.get("/api/v1/catalog/")["ETag"]
    header = {"weak": etag, "strong": etag[2:], "list": f'"other", {etag}', "wildcard": "*"}[header_kind]
    response = authenticated_client.get("/api/v1/catalog/", HTTP_IF_NONE_MATCH=header)
    assert response.status_code == 304


@pytest.mark.parametrize("header", ['"different"', "malformed"])
def test_unmatched_or_malformed_validator_returns_200(authenticated_client, header):
    response = authenticated_client.get("/api/v1/catalog/", HTTP_IF_NONE_MATCH=header)
    assert response.status_code == 200
    assert response.json() == {"label": "published"}


def test_changed_representation_has_new_validator(authenticated_client):
    first = authenticated_client.get("/api/v1/catalog/")
    response = authenticated_client.get("/api/v1/catalog/?label=new", HTTP_IF_NONE_MATCH=first["ETag"])
    assert response.status_code == 200
    assert response["ETag"] != first["ETag"]


def test_head_is_bodyless_and_retains_get_validator(authenticated_client):
    first = authenticated_client.get("/api/v1/catalog/")
    response = authenticated_client.head("/api/v1/catalog/")
    assert response.status_code == 200
    assert response.content == b""
    assert response["ETag"] == first["ETag"]
    assert response["Content-Length"] == str(len(first.content))
    response = authenticated_client.head("/api/v1/catalog/", HTTP_IF_NONE_MATCH=first["ETag"])
    assert response.status_code == 304
    assert response.content == b""


def test_matching_validator_never_bypasses_authentication(authenticated_client):
    etag = authenticated_client.get("/api/v1/catalog/")["ETag"]
    response = APIClient().get("/api/v1/catalog/", HTTP_IF_NONE_MATCH=etag)
    assert response.status_code == 401
    assert response["Cache-Control"] == "no-store"
    assert "ETag" not in response


def test_revoked_token_is_checked_before_conditional_response(api_client, user):
    token = Token.objects.create(user=user)
    api_client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
    etag = api_client.get("/api/v1/catalog/")["ETag"]
    token.delete()
    response = api_client.get("/api/v1/catalog/", HTTP_IF_NONE_MATCH=etag)
    assert response.status_code == 401
    assert response["Cache-Control"] == "no-store"


def test_permissions_are_checked_again_on_every_request(admin_client, admin_user):
    etag = admin_client.get("/api/v1/staff-catalog/")["ETag"]
    admin_user.is_staff = False
    admin_user.save(update_fields=["is_staff"])
    response = admin_client.get("/api/v1/staff-catalog/", HTTP_IF_NONE_MATCH=etag)
    assert response.status_code == 403
    assert response["Cache-Control"] == "no-store"


@pytest.mark.parametrize("mode,status", [("invalid", 400), ("missing", 404)])
def test_error_handling_runs_before_if_none_match(authenticated_client, mode, status):
    response = authenticated_client.get(f"/api/v1/catalog/?mode={mode}", HTTP_IF_NONE_MATCH="*")
    assert response.status_code == status
    assert response["Content-Type"] == "application/problem+json"
    assert response["Cache-Control"] == "no-store"
    assert "ETag" not in response


@pytest.mark.parametrize("mode", ["no-store", "custom-policy", "cookie", "stream", "html"])
def test_unsafe_or_already_owned_responses_are_not_modified(authenticated_client, mode):
    response = authenticated_client.get(f"/api/v1/catalog/?mode={mode}", HTTP_IF_NONE_MATCH="*")
    assert response.status_code == 200
    assert "ETag" not in response
    if response.streaming:
        response.close()


def test_existing_validator_is_not_overwritten(authenticated_client):
    response = authenticated_client.get("/api/v1/catalog/?mode=custom-etag", HTTP_IF_NONE_MATCH="*")
    assert response.status_code == 200
    assert response["ETag"] == '"owned-by-view"'


def test_post_is_not_a_conditional_get(authenticated_client):
    response = authenticated_client.post("/api/v1/catalog/", {}, format="json", HTTP_IF_NONE_MATCH="*")
    assert response.status_code == 200
    assert "ETag" not in response


def test_failed_if_match_retains_problem_contract(authenticated_client):
    response = authenticated_client.get(
        "/api/v1/catalog/", HTTP_IF_MATCH='"different"', HTTP_IF_NONE_MATCH="*",
    )
    assert response.status_code == 412
    assert response["Content-Type"] == "application/problem+json"
    assert response["Cache-Control"] == "no-store"
    assert response.json()["code"] == "precondition_failed"
    assert "ETag" not in response


def test_schema_only_advertises_conditional_reads():
    schema = SchemaGenerator(patterns=urlpatterns).get_schema(request=None, public=True)
    operation = schema["paths"]["/api/v1/catalog/"]["get"]
    responses = operation["responses"]
    assert "content" not in responses["304"]
    assert {"ETag", "Vary", "Cache-Control", "X-Request-ID"} <= responses["304"]["headers"].keys()
    assert "application/problem+json" in responses["412"]["content"]
    assert "304" not in schema["paths"]["/api/v1/catalog/"]["post"]["responses"]


def test_cors_allows_validators_and_exposes_etag(api_client, authenticated_client):
    response = api_client.options(
        "/api/v1/catalog/", HTTP_ORIGIN="http://localhost:3000",
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET", HTTP_ACCESS_CONTROL_REQUEST_HEADERS="if-none-match",
    )
    assert response.status_code == 200
    assert "if-none-match" in response["Access-Control-Allow-Headers"].lower()
    response = authenticated_client.get("/api/v1/catalog/", HTTP_ORIGIN="http://localhost:3000")
    assert "etag" in response["Access-Control-Expose-Headers"].lower()
