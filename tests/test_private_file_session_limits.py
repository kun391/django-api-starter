"""Receive-side upload limits survive SessionAuthentication's CSRF parsing."""

from unittest.mock import patch

import pytest
from django.core.files.storage import storages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.middleware.csrf import get_token
from django.test import RequestFactory
from rest_framework.test import APIClient

from apps.files.models import PrivateFile

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("case", ["oversized", "multiple"])
def test_session_upload_limits_reject_before_storage(user, settings, tmp_path, case):
    settings.STORAGES = {**settings.STORAGES, "private": {
        "BACKEND": "apps.files.backends.PrivateFileSystemStorage",
        "OPTIONS": {"location": str(tmp_path / "private")},
    }}
    settings.PRIVATE_FILE_MAX_BYTES = 8
    settings.PRIVATE_FILE_POLICIES = {"document": {"max_bytes": 8, "validators": {
        ".txt": "apps.files.validation.validate_text",
    }}}
    client = APIClient(enforce_csrf_checks=True)
    client.force_login(user)
    token = get_token(RequestFactory().get("/"))
    client.cookies["csrftoken"] = token
    data = {"file": SimpleUploadedFile("file.txt", b"x" * 9)} if case == "oversized" else {
        "file": [SimpleUploadedFile("first.txt", b"one"), SimpleUploadedFile("second.txt", b"two")],
    }
    with patch.object(storages["private"], "save") as save:
        response = client.post("/api/v1/files/", data, format="multipart", HTTP_X_CSRFTOKEN=token)
    assert response.status_code == 413, response.content
    assert response["Content-Type"] == "application/problem+json"
    assert response.json()["code"] == "upload_too_large"
    assert not PrivateFile.objects.exists()
    save.assert_not_called()
