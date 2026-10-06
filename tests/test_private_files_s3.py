"""Real S3-compatible integration; deliberately not a mock/emulator test."""

import os
from importlib import import_module
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen
from uuid import uuid4

import pytest
from django.core.files.storage import storages
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.core.outbox import process_outbox_batch
from apps.files import services
from settings import file_storage

ENDPOINT = os.environ.get("TEST_S3_ENDPOINT_URL", "")
pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(not ENDPOINT, reason="Set TEST_S3_ENDPOINT_URL to a disposable S3-compatible server."),
]


@pytest.fixture
def s3_backend(settings, monkeypatch, tmp_path):
    # Missing optional dependencies must fail (not skip) when integration is enabled.
    boto3 = import_module("boto3")
    config_class = import_module("botocore.config").Config
    client = boto3.client("s3", endpoint_url=ENDPOINT, region_name="us-east-1", config=config_class(signature_version="s3v4", s3={"addressing_style": "path"}))
    bucket = f"phase11-{uuid4().hex}"
    client.create_bucket(Bucket=bucket)
    values = {"PRIVATE_FILE_BACKEND": "s3", "PRIVATE_FILE_S3_BUCKET": bucket, "PRIVATE_FILE_S3_ENDPOINT": ENDPOINT, "PRIVATE_FILE_S3_ALLOW_HTTP": True}
    monkeypatch.setattr(file_storage, "config", lambda name, default=None, **kwargs: values.get(name, default))
    backend = file_storage.build_private_storage(tmp_path, tmp_path / "media", tmp_path / "staticfiles")
    settings.STORAGES = {**settings.STORAGES, "private": backend}
    settings.PRIVATE_FILE_SIGNED_DOWNLOADS = True
    settings.PRIVATE_FILE_URL_TTL = 60
    try:
        yield storages["private"], client, bucket
    finally:
        # Only the random bucket created above is touched; never clear a supplied bucket.
        objects = client.list_objects_v2(Bucket=bucket).get("Contents", [])
        for obj in objects:
            client.delete_object(Bucket=bucket, Key=obj["Key"])
        client.delete_bucket(Bucket=bucket)
        client.close()


def test_s3_private_upload_signed_get_and_deletion(s3_backend, user):
    backend, client, bucket = s3_backend
    record = services.upload_file(actor=user, upload=SimpleUploadedFile("private.txt", b"s3 bytes"))
    metadata = client.head_object(Bucket=bucket, Key=record.object_key)
    assert metadata["ContentType"] == "application/octet-stream"
    assert metadata["CacheControl"] == "private, no-store"
    unsigned = f"{ENDPOINT}/{bucket}/{record.object_key}"
    with pytest.raises(HTTPError) as denied:
        urlopen(unsigned, timeout=5)
    assert denied.value.code == 403
    signed = services.download_url(record.pk, actor=user)
    assert parse_qs(urlsplit(signed["url"]).query)["X-Amz-Expires"] == ["60"]
    with urlopen(signed["url"], timeout=5) as response:
        assert response.read() == b"s3 bytes"
        assert response.headers["Content-Type"] == "application/octet-stream"
        assert response.headers["Content-Disposition"].startswith("attachment;")
        assert response.headers["Cache-Control"] == "private, no-store"
    services.delete_file(record.pk, actor=user)
    assert process_outbox_batch() == 1
    assert not backend.exists(record.object_key)


def test_s3_download_through_api_and_immutable_replace(s3_backend, user, authenticated_client):
    backend, client, bucket = s3_backend
    first = authenticated_client.post("/api/v1/files/", {"file": SimpleUploadedFile("first.json", b'{"a":1}')}, format="multipart")
    assert first.status_code == 201, first.content
    file_id = first.json()["id"]
    response = authenticated_client.get(f"/api/v1/files/{file_id}/download/", HTTP_ACCEPT="application/octet-stream")
    assert response.status_code == 200
    try:
        assert b"".join(response.streaming_content) == b'{"a":1}'
    finally:
        response.close()
    replaced = authenticated_client.post(f"/api/v1/files/{file_id}/replace/", {"file": SimpleUploadedFile("second.txt", b"new")}, format="multipart")
    assert replaced.status_code == 201
    assert replaced.json()["id"] != file_id
    assert process_outbox_batch() == 1
    assert authenticated_client.get(f"/api/v1/files/{file_id}/").status_code == 404
    url_response = authenticated_client.post(f"/api/v1/files/{replaced.json()['id']}/download-url/")
    assert url_response.status_code == 200
    assert url_response["Cache-Control"] == "no-store"
    with urlopen(url_response.json()["url"], timeout=5) as response:
        assert response.read() == b"new"


def test_s3_tampered_signature_does_not_grant_access(s3_backend, user):
    record = services.upload_file(actor=user, upload=SimpleUploadedFile("file.txt", b"private"))
    signed = services.download_url(record.pk, actor=user)["url"]
    tampered = signed.replace("X-Amz-Expires=60", "X-Amz-Expires=300")
    assert tampered != signed
    with pytest.raises(HTTPError) as denied:
        urlopen(tampered, timeout=5)
    assert denied.value.code == 403
