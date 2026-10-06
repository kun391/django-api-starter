"""Private-file API, real filesystem, compensation and PostgreSQL races."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import BytesIO
from threading import Barrier
from unittest.mock import patch

import pytest
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connections, transaction
from django.utils import timezone
from drf_spectacular.generators import SchemaGenerator
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.core.models import OutboxEvent
from apps.core.outbox import process_outbox_batch
from apps.files import services
from apps.files.models import PrivateFile
from apps.files.validation import validate_upload

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def private_storage(settings, tmp_path):
    backend = {"BACKEND": "apps.files.backends.PrivateFileSystemStorage", "OPTIONS": {"location": str(tmp_path / "private")}}
    settings.STORAGES = {**settings.STORAGES, "private": backend, "default": backend}
    settings.PRIVATE_FILE_MAX_BYTES = 1024
    settings.PRIVATE_FILE_POLICIES = {"document": {"max_bytes": 1024, "validators": {
        ".txt": "apps.files.validation.validate_text", ".json": "apps.files.validation.validate_json",
    }}}
    settings.PRIVATE_FILE_SIGNED_DOWNLOADS = False
    return storages["private"]


def upload(data=b"private contents", name="notes.txt", media_type="text/plain"):
    return SimpleUploadedFile(name, data, content_type=media_type)


def create(user, **kwargs):
    return services.upload_file(actor=user, upload=upload(**kwargs))


def test_upload_download_metadata_and_logical_delete(authenticated_client, private_storage):
    response = authenticated_client.post("/api/v1/files/", {"file": upload()}, format="multipart")
    assert response.status_code == 201, response.content
    body = response.json()
    assert set(body) == {"id", "purpose", "original_name", "size", "media_type", "sha256", "created_at"}
    assert body["media_type"] == "text/plain"
    assert response["Cache-Control"] == "no-store"
    record = PrivateFile.objects.get(pk=body["id"])
    assert private_storage.exists(record.object_key)
    path = f"/api/v1/files/{record.pk}/"
    assert authenticated_client.get(path).json() == body
    response = authenticated_client.get(path + "download/", HTTP_ACCEPT="application/octet-stream")
    assert response.status_code == 200
    try:
        assert b"".join(response.streaming_content) == b"private contents"
    finally:
        response.close()
    assert response["Content-Type"] == "application/octet-stream"
    assert response["Content-Disposition"].startswith("attachment;")
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Cache-Control"] == "no-store"
    assert authenticated_client.delete(path).status_code == 204
    assert authenticated_client.get(path + "download/").status_code == 404
    assert authenticated_client.delete(path).status_code == 204
    assert OutboxEvent.objects.filter(topic="files.deletion-requested").count() == 1
    assert private_storage.exists(record.object_key)  # Physical cleanup is asynchronous.
    assert process_outbox_batch() == 1
    assert not private_storage.exists(record.object_key)
    record.refresh_from_db()
    assert record.state == "deleted" and record.original_name == "" and record.size == 0
    assert authenticated_client.delete(path).status_code == 204


def test_no_anonymous_upload_or_listing(api_client):
    response = api_client.post("/api/v1/files/", {"file": upload()}, format="multipart")
    assert response.status_code == 401
    assert response["Cache-Control"] == "no-store"
    assert PrivateFile.objects.count() == 0


def test_other_owner_and_staff_cannot_access_file(user, admin_user, private_storage):
    record = create(user)
    client = APIClient()
    client.force_authenticate(admin_user)
    path = f"/api/v1/files/{record.pk}/"
    with patch.object(private_storage, "open") as read:
        assert client.get(path).status_code == 404
        assert client.get(path + "download/").status_code == 404
        assert client.delete(path).status_code == 404
        assert client.post(path + "replace/", {"file": upload()}, format="multipart").status_code == 404
        assert client.post(path + "download-url/").status_code == 404
    read.assert_not_called()
    record.refresh_from_db()
    assert record.state == "ready"


def test_revoked_token_cannot_download(api_client, user):
    record = create(user)
    token = Token.objects.create(user=user)
    api_client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
    token.delete()
    response = api_client.get(f"/api/v1/files/{record.pk}/download/", HTTP_ACCEPT="application/octet-stream")
    assert response.status_code == 401
    assert response["Content-Type"] == "application/problem+json"


def test_session_upload_requires_csrf(user):
    client = APIClient(enforce_csrf_checks=True)
    client.force_login(user)
    response = client.post("/api/v1/files/", {"file": upload()}, format="multipart")
    assert response.status_code == 403
    assert PrivateFile.objects.count() == 0


def test_owner_id_and_object_key_cannot_be_supplied(authenticated_client):
    response = authenticated_client.post("/api/v1/files/", {
        "file": upload(), "owner": 99, "object_key": "somebody-else/file.txt",
    }, format="multipart")
    assert response.status_code == 400
    assert PrivateFile.objects.count() == 0


@pytest.mark.parametrize("data,name", [(b"not JSON", "file.json"), (b"{", "file.json"), (b"NaN", "file.json"), (b"binary\x00", "file.txt"), (b"\xff", "file.txt"), (b"<svg/>", "file.svg"), (b"abc", "file.exe")])
def test_invalid_content_or_type_never_reaches_storage(authenticated_client, private_storage, data, name):
    with patch.object(private_storage, "save") as save:
        response = authenticated_client.post("/api/v1/files/", {"file": upload(data, name)}, format="multipart")
    assert response.status_code == 400
    save.assert_not_called()
    assert response["Content-Type"] == "application/problem+json"
    assert PrivateFile.objects.count() == 0


def test_content_not_client_mime_determines_type(authenticated_client):
    response = authenticated_client.post("/api/v1/files/", {
        "file": upload(b'{"hello":"world"}', "file.json", "image/jpeg"),
    }, format="multipart")
    assert response.status_code == 201
    assert response.json()["media_type"] == "application/json"


@pytest.mark.parametrize("name", ["../x.txt", "C:\\x.txt", "x\r\n.txt", "x..txt", ".hidden.txt", "x\u202e.txt", "x.txt ", "a" * 121 + ".txt"])
def test_programmatic_filenames_are_validated(name):
    stream = BytesIO(b"safe")
    stream.name = name
    with pytest.raises(ValidationError):
        validate_upload(stream, purpose="document")


def test_receive_side_size_and_file_count_limits(authenticated_client):
    response = authenticated_client.post("/api/v1/files/", {"file": upload(b"x" * 1025)}, format="multipart")
    assert response.status_code == 413
    assert response.json()["code"] == "upload_too_large"
    response = authenticated_client.post("/api/v1/files/", {"file": [upload(), upload()]}, format="multipart")
    assert response.status_code == 413
    assert PrivateFile.objects.count() == 0


def test_unknown_purpose_empty_and_repeated_fields(authenticated_client):
    for data in ({"file": upload(), "purpose": "unknown"}, {"file": upload(b"")}, {"file": upload(), "purpose": ["document", "document"]}):
        assert authenticated_client.post("/api/v1/files/", data, format="multipart").status_code == 400
    assert PrivateFile.objects.count() == 0


def test_only_multipart_is_accepted(authenticated_client):
    response = authenticated_client.post("/api/v1/files/", {"file": "base64"}, format="json")
    assert response.status_code == 415


def test_upload_throttle_is_per_authenticated_actor(authenticated_client, settings):
    settings.SECURITY_THROTTLE_RATES = {**settings.SECURITY_THROTTLE_RATES, "private_upload": {"limit": 1, "window_seconds": 60}}
    assert authenticated_client.post("/api/v1/files/", {"file": upload()}, format="multipart").status_code == 201
    response = authenticated_client.post("/api/v1/files/", {"file": upload()}, format="multipart")
    assert response.status_code == 429 and "Retry-After" in response


def test_local_urls_and_signed_downloads_are_not_public(user, authenticated_client, private_storage):
    record = create(user)
    with pytest.raises(NotImplementedError):
        private_storage.url(record.object_key)
    assert authenticated_client.post(f"/api/v1/files/{record.pk}/download-url/").status_code == 404
    assert authenticated_client.get("/media/" + record.object_key).status_code == 404
    assert authenticated_client.get(f"/api/v1/files/{record.pk}/?object_key=foo").status_code == 400


def test_nested_upload_is_rejected_before_storage(user, private_storage):
    with patch.object(private_storage, "save") as save:
        with transaction.atomic(), pytest.raises(ImproperlyConfigured):
            create(user)
    save.assert_not_called()
    assert PrivateFile.objects.count() == 0


def test_manifest_failure_does_not_write_an_object(user, private_storage):
    with patch.object(PrivateFile.objects, "create", side_effect=RuntimeError("database failure")):
        with patch.object(private_storage, "save") as save, pytest.raises(RuntimeError):
            create(user)
    save.assert_not_called()


def test_ambiguous_storage_failure_has_durable_compensation(user, private_storage, caplog):
    original = private_storage.save

    def failed_save(name, content):
        original(name, content)
        raise OSError("private-password-in-provider-error")

    with patch.object(private_storage, "save", side_effect=failed_save), pytest.raises(services.FileStorageUnavailable):
        create(user)
    record = PrivateFile.objects.get()
    assert record.state == "deleting" and private_storage.exists(record.object_key)
    assert "private-password" not in caplog.text
    assert process_outbox_batch() == 1
    assert not private_storage.exists(record.object_key)


def test_finalize_failure_is_compensated(user, private_storage):
    original = PrivateFile.save

    def failed_finalize(record, *args, **kwargs):
        if record.state == "ready":
            raise RuntimeError("finalization failed")
        return original(record, *args, **kwargs)

    with patch.object(PrivateFile, "save", failed_finalize), pytest.raises(RuntimeError):
        create(user)
    record = PrivateFile.objects.get()
    assert record.state == "deleting"
    process_outbox_batch()
    assert not private_storage.exists(record.object_key)


def test_replace_keeps_old_file_until_new_ready(user, authenticated_client, private_storage):
    old = create(user)
    response = authenticated_client.post(f"/api/v1/files/{old.pk}/replace/", {"file": upload(b"new")}, format="multipart")
    assert response.status_code == 201
    new = PrivateFile.objects.get(pk=response.json()["id"])
    assert new.pk != old.pk and new.state == "ready"
    old.refresh_from_db()
    assert old.state == "deleting"
    process_outbox_batch()
    assert private_storage.exists(new.object_key) and not private_storage.exists(old.object_key)


def test_failed_replace_preserves_old_and_abandoned_manifest(user, private_storage):
    old = create(user)
    with patch("apps.files.services.record_outbox_event", side_effect=RuntimeError("database unavailable")):
        with pytest.raises(RuntimeError):
            services.upload_file(actor=user, upload=upload(b"new"), replace_id=old.pk)
    old.refresh_from_db()
    assert old.state == "ready" and private_storage.exists(old.object_key)
    pending = PrivateFile.objects.exclude(pk=old.pk).get()
    assert pending.state == "pending"
    PrivateFile.objects.filter(pk=pending.pk).update(cleanup_after=timezone.now() - timedelta(seconds=1))
    assert services.reconcile_files() == 1
    process_outbox_batch()
    assert not private_storage.exists(pending.object_key)
    assert private_storage.exists(old.object_key)


def test_delete_rollback_preserves_file_and_discards_outbox(user, private_storage):
    record = create(user)
    with pytest.raises(RuntimeError), transaction.atomic():
        services.delete_file(record.pk, actor=user)
        raise RuntimeError("rollback")
    record.refresh_from_db()
    assert record.state == "ready" and OutboxEvent.objects.count() == 0
    assert private_storage.exists(record.object_key)


def test_deletion_retry_replay_and_error_hygiene(user, private_storage):
    record = create(user)
    services.delete_file(record.pk, actor=user)
    with patch.object(private_storage, "delete", side_effect=OSError("secret-provider-message")):
        assert process_outbox_batch() == 0
    event = OutboxEvent.objects.get()
    assert event.attempts == 1 and event.last_error_code == "OSError"
    OutboxEvent.objects.filter(pk=event.pk).update(available_at=timezone.now())
    assert process_outbox_batch() == 1
    services.delete_stored_object(record.pk)
    assert not private_storage.exists(record.object_key)


def test_pending_hidden_and_cleanup_is_bounded(user, authenticated_client):
    records = [create(user, name=f"file{index}.txt") for index in range(3)]
    PrivateFile.objects.update(state="pending", cleanup_after=timezone.now() - timedelta(seconds=1))
    assert authenticated_client.get(f"/api/v1/files/{records[0].pk}/").status_code == 404
    assert services.reconcile_files(batch_size=2) == 2
    assert OutboxEvent.objects.count() == 2
    assert services.reconcile_files(batch_size=2) == 1
    with pytest.raises(ValueError):
        services.reconcile_files(batch_size=0)
    call_command("reconcile_private_files", batch_size=1)


def test_owner_deletion_does_not_lose_cleanup_manifest(user, private_storage):
    record = create(user)
    user.delete()
    record.refresh_from_db()
    assert record.owner_id is None
    assert services.reconcile_files() == 1
    process_outbox_batch()
    assert not private_storage.exists(record.object_key)


def test_live_late_upload_cannot_resurrect_retired_id(user, private_storage):
    original = private_storage.save

    def delayed_save(name, content):
        PrivateFile.objects.update(cleanup_after=timezone.now() - timedelta(seconds=1))
        services.reconcile_files()
        process_outbox_batch()
        return original(name, content)

    with patch.object(private_storage, "save", side_effect=delayed_save), pytest.raises(services.FileConflict):
        create(user)
    record = PrivateFile.objects.get()
    assert record.state == "deleting"
    process_outbox_batch()
    assert not private_storage.exists(record.object_key)


def test_tombstone_recheck_cleans_put_completed_after_crash(user, private_storage):
    record = create(user)
    services.delete_file(record.pk, actor=user)
    process_outbox_batch()
    # Simulate a remote request finishing after the uploader process died.
    private_storage.save(record.object_key, ContentFile(b"late remote completion"))
    PrivateFile.objects.filter(pk=record.pk).update(cleanup_after=timezone.now() - timedelta(seconds=1))
    assert services.reconcile_files() == 1
    process_outbox_batch()
    assert not private_storage.exists(record.object_key)


def test_concurrent_replacements_have_one_winner(user, private_storage):
    previous = create(user)
    barrier = Barrier(2)
    original = private_storage.save

    def save(name, content):
        result = original(name, content)
        barrier.wait(timeout=10)
        return result

    def replace():
        try:
            try:
                return services.upload_file(actor=user, upload=upload(b"replacement"), replace_id=previous.pk).pk
            except services.FileConflict:
                return None
        finally:
            connections.close_all()

    with patch.object(private_storage, "save", side_effect=save), ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(replace) for _ in range(2)]
        results = [future.result(timeout=20) for future in futures]
    assert sum(result is not None for result in results) == 1
    assert PrivateFile.objects.filter(state="ready").count() == 1
    assert process_outbox_batch() == 2
    winner = PrivateFile.objects.get(state="ready")
    assert private_storage.exists(winner.object_key)


def test_storage_error_http_response_hides_backend_details(authenticated_client, private_storage):
    with patch.object(private_storage, "save", side_effect=OSError("s3-secret-password")):
        response = authenticated_client.post("/api/v1/files/", {"file": upload()}, format="multipart")
    assert response.status_code == 503
    assert response.json()["code"] == "file_storage_unavailable"
    assert "s3-secret-password" not in response.content.decode()


def test_file_schema_is_multipart_binary_and_problem_json():
    schema = SchemaGenerator().get_schema(request=None, public=True)
    operation = schema["paths"]["/api/v1/files/"]["post"]
    assert "multipart/form-data" in operation["requestBody"]["content"]
    for status in ("413", "409", "503"):
        assert "application/problem+json" in operation["responses"][status]["content"]
    download = schema["paths"]["/api/v1/files/{file_id}/download/"]["get"]
    assert download["responses"]["200"]["content"]["application/octet-stream"]["schema"]["format"] == "binary"
