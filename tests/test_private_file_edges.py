"""Regression coverage for late storage I/O, content corruption and CSRF."""

from io import BytesIO
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.middleware.csrf import get_token
from django.test import RequestFactory
from rest_framework.test import APIClient

from apps.files import services
from apps.files.validation import validate_json

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def backend(settings, tmp_path):
    settings.STORAGES = {**settings.STORAGES, "private": {
        "BACKEND": "apps.files.backends.PrivateFileSystemStorage",
        "OPTIONS": {"location": str(tmp_path / "private")},
    }}
    return storages["private"]


def create(user):
    return services.upload_file(actor=user, upload=SimpleUploadedFile("file.txt", b"original"))


@pytest.mark.parametrize("data", [b"short", b"different and longer", b"modified"])
def test_corrupted_storage_is_not_a_successful_download(backend, user, authenticated_client, data):
    record = create(user)
    backend.save(record.object_key, ContentFile(data))
    response = authenticated_client.get(f"/api/v1/files/{record.pk}/download/")
    assert response.status_code == 503
    assert response["Content-Type"] == "application/problem+json"
    assert response["Cache-Control"] == "no-store"
    assert response.json()["code"] == "file_storage_unavailable"


def test_failure_on_first_read_is_translated_before_headers(backend, user, authenticated_client, caplog):
    record = create(user)

    class LazyFailure(BytesIO):
        def read(self, size=-1):
            raise OSError("provider-secret-in-url")

    source = LazyFailure(b"original")
    with patch.object(backend, "open", return_value=source):
        response = authenticated_client.get(f"/api/v1/files/{record.pk}/download/", HTTP_ACCEPT="application/octet-stream")
    assert response.status_code == 503
    assert "provider-secret" not in response.content.decode()
    assert "provider-secret" not in caplog.text
    assert source.closed


def test_short_reads_are_supported_and_response_closes_spool(backend, user, authenticated_client):
    record = create(user)

    class ShortReads(BytesIO):
        def read(self, size=-1):
            return super().read(min(size, 2))

    source = ShortReads(b"original")
    with patch.object(backend, "open", return_value=source):
        response = authenticated_client.get(f"/api/v1/files/{record.pk}/download/", HTTP_IF_NONE_MATCH="*")
    assert response.status_code == 200 and "ETag" not in response
    spool = response.file_to_stream
    try:
        assert b"".join(response.streaming_content) == b"original"
    finally:
        response.close()
    assert spool.closed and source.closed


@pytest.mark.parametrize("data", [b"1e9999", b'{"x":-1e9999}', b"[Infinity]"])
def test_json_policy_rejects_nonfinite_or_overflow_numbers(data):
    with pytest.raises(ValidationError):
        validate_json(data)


def test_session_upload_with_valid_csrf(backend, user):
    client = APIClient(enforce_csrf_checks=True)
    client.force_login(user)
    token = get_token(RequestFactory().get("/"))
    client.cookies["csrftoken"] = token
    response = client.post("/api/v1/files/", {"file": SimpleUploadedFile("file.txt", b"valid session")}, format="multipart", HTTP_X_CSRFTOKEN=token)
    assert response.status_code == 201, response.content


def test_debug_does_not_publish_media(backend, user, api_client, settings):
    settings.DEBUG = True
    record = create(user)
    assert api_client.get("/media/" + record.object_key).status_code == 404


def test_delete_handler_ignores_live_keys(backend, user):
    record = create(user)
    services.delete_stored_object(record.pk)
    assert backend.exists(record.object_key)
