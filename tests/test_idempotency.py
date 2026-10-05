import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from uuid import uuid4

import pytest
from django.contrib.auth.models import Group
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, close_old_connections, connection, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.views import APIView

from apps.core.api.idempotency import idempotent_post
from apps.core.models import IdempotencyRecord

# Real transactions and separate PostgreSQL connections, NOT mocked cache locks.
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def invoke(user):
    def call(operation, *, payload=None, key="logical-write-01", scope="tests.create:v1", actor=user, path="/api/v1/test-write/", method="POST", content_type="application/json"):
        class Endpoint(APIView):
            permission_classes = [AllowAny]

            def post(self, request):
                return idempotent_post(request, operation, scope=scope)

            get = post

        request = APIRequestFactory().generic(
            method, path, json.dumps({} if payload is None else payload),
            content_type=content_type, HTTP_IDEMPOTENCY_KEY=key,
        )
        if actor is not None:
            force_authenticate(request, user=actor)
        response = Endpoint.as_view()(request)
        if hasattr(response, "render"):
            response.render()
        return response
    return call


@pytest.fixture
def operation():
    def create():
        group = Group.objects.create(name=f"operation-{uuid4().hex}")
        return Response(
            {"id": group.pk}, status=201,
            headers={"Location": f"/api/v1/test-write/{group.pk}/", "ETag": f'"{group.pk}"'},
        )
    return create


def body(response):
    return json.loads(response.content)


def test_replay_is_byte_identical_and_does_not_repeat_write(invoke, operation):
    first = invoke(operation, payload={"name": "sample"})
    second = invoke(operation, payload={"name": "sample"})
    assert first.status_code == second.status_code == 201
    assert first.content == second.content
    assert first["Idempotency-Replayed"] == "false"
    assert second["Idempotency-Replayed"] == "true"
    assert second["Cache-Control"] == "no-store"
    assert second["Location"] == first["Location"]
    assert second["ETag"] == first["ETag"]
    assert Group.objects.count() == IdempotencyRecord.objects.count() == 1


def test_canonical_json_ignores_object_key_order(invoke, operation):
    first = invoke(operation, payload={"a": 1, "b": {"x": 2, "y": 3}})
    second = invoke(operation, payload={"b": {"y": 3, "x": 2}, "a": 1})
    assert first.content == second.content
    assert second["Idempotency-Replayed"] == "true"


def test_reused_key_with_changed_payload_is_422(invoke, operation):
    invoke(operation, payload={"amount": 1})
    response = invoke(operation, payload={"amount": 2})
    assert response.status_code == 422
    assert body(response)["code"] == "idempotency_key_reused"
    assert Group.objects.count() == 1


def test_query_string_is_part_of_fingerprint(invoke, operation):
    invoke(operation, path="/api/v1/test-write/?mode=a")
    response = invoke(operation, path="/api/v1/test-write/?mode=b")
    assert response.status_code == 422
    assert Group.objects.count() == 1


def test_key_is_isolated_by_principal_scope_path_and_key(invoke, operation, admin_user):
    invoke(operation)
    invoke(operation, actor=admin_user)
    invoke(operation, scope="tests.create:tenant-2:v1")
    invoke(operation, path="/api/v1/other-write/")
    invoke(operation, key="different-key")
    assert Group.objects.count() == IdempotencyRecord.objects.count() == 5


@pytest.mark.parametrize("key", ["", "a" * 129, "has space", "a,b", "a/b", "bad\nkey"])
def test_invalid_key_is_rejected_before_mutation(invoke, operation, key):
    response = invoke(operation, key=key)
    assert response.status_code == 400
    assert body(response)["code"] == "validation_error"
    assert not Group.objects.exists()
    assert not IdempotencyRecord.objects.exists()


def test_anonymous_requests_are_not_replayable(invoke, operation):
    response = invoke(operation, actor=None)
    assert response.status_code == 401
    assert not Group.objects.exists()


def test_only_json_posts_are_supported(invoke, operation):
    assert invoke(operation, method="GET").status_code == 405
    assert invoke(operation, content_type="text/plain").status_code == 415
    assert not IdempotencyRecord.objects.exists()


def test_request_size_is_bounded(invoke, operation):
    response = invoke(operation, payload={"large": "x" * (64 * 1024)})
    assert response.status_code == 400
    assert not Group.objects.exists()


def test_validation_failure_rolls_back_and_allows_retry(invoke, operation):
    def fail():
        Group.objects.create(name="must-roll-back")
        raise ValidationError({"name": "Rejected by the service."})

    assert invoke(fail).status_code == 400
    assert not Group.objects.exists()
    assert not IdempotencyRecord.objects.exists()
    assert invoke(operation)["Idempotency-Replayed"] == "false"
    assert Group.objects.count() == 1


def test_unexpected_failure_rolls_back(invoke):
    def fail():
        Group.objects.create(name="must-roll-back")
        raise RuntimeError("operation failed")

    with pytest.raises(RuntimeError, match="operation failed"):
        invoke(fail)
    assert not Group.objects.exists()
    assert not IdempotencyRecord.objects.exists()


def test_replay_record_failure_also_rolls_back_business_write(invoke, operation, monkeypatch):
    def fail(**kwargs):
        raise DatabaseError("record persistence failed")

    monkeypatch.setattr(IdempotencyRecord.objects, "create", fail)
    with pytest.raises(DatabaseError, match="record persistence failed"):
        invoke(operation)
    assert not Group.objects.exists()
    assert not IdempotencyRecord.objects.exists()


@pytest.mark.parametrize("mode", ["cookie", "unsafe-header", "large", "error-response"])
def test_unsafe_responses_fail_closed_and_roll_back(invoke, mode):
    def unsafe():
        Group.objects.create(name="must-roll-back")
        response = Response({"ok": True}, status=201)
        if mode == "cookie":
            response.set_cookie("session", "secret")
        elif mode == "unsafe-header":
            response["Authorization"] = "secret"
        elif mode == "large":
            response.data = {"large": "x" * (64 * 1024)}
        else:
            response.status_code = 400
        return response

    with pytest.raises(ImproperlyConfigured):
        invoke(unsafe)
    assert not Group.objects.exists()
    assert not IdempotencyRecord.objects.exists()


def test_no_content_response_replays_without_body(invoke):
    first = invoke(lambda: Response(status=204))
    second = invoke(lambda: Response(status=204))
    assert first.status_code == second.status_code == 204
    assert first.content == second.content == b""
    assert second["Idempotency-Replayed"] == "true"


def test_ttl_and_expired_key_reuse(invoke, operation, settings):
    settings.IDEMPOTENCY_TTL_SECONDS = 60
    before = timezone.now()
    first = invoke(operation)
    record = IdempotencyRecord.objects.get()
    assert before + timedelta(seconds=60) <= record.expires_at <= timezone.now() + timedelta(seconds=60)
    IdempotencyRecord.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    second = invoke(operation, payload={"new": "logical operation"})
    assert first.content != second.content
    assert second["Idempotency-Replayed"] == "false"
    assert Group.objects.count() == 2
    assert IdempotencyRecord.objects.count() == 1


def test_raw_key_and_payload_are_not_persisted(invoke, operation):
    invoke(operation, key="private-key-value", payload={"private": "private-request-value"})
    record = IdempotencyRecord.objects.get()
    stored = str(record.__dict__)
    assert "private-key-value" not in stored
    assert "private-request-value" not in stored
    assert len(record.key_digest) == len(record.request_digest) == 64


def test_cleanup_only_deletes_a_bounded_expired_batch(invoke, operation):
    for key in ["a", "b", "c"]:
        invoke(operation, key=key)
    expired_ids = list(IdempotencyRecord.objects.order_by("id").values_list("id", flat=True)[:2])
    IdempotencyRecord.objects.filter(pk__in=expired_ids).update(expires_at=timezone.now() - timedelta(seconds=1))
    call_command("purge_idempotency", batch_size=1)
    assert IdempotencyRecord.objects.count() == 2
    call_command("purge_idempotency")
    assert IdempotencyRecord.objects.count() == 1
    with pytest.raises(CommandError):
        call_command("purge_idempotency", batch_size=0)


def test_postgresql_is_required(invoke, operation, monkeypatch):
    monkeypatch.setattr(connection, "vendor", "sqlite")
    with pytest.raises(ImproperlyConfigured, match="PostgreSQL"):
        invoke(operation)


def test_durable_boundary_rejects_nested_transactions(invoke, operation):
    with transaction.atomic(), pytest.raises(RuntimeError, match="durable"):
        invoke(operation)
    assert not Group.objects.exists()


def test_invalid_ttl_fails_closed(invoke, operation, settings):
    settings.IDEMPOTENCY_TTL_SECONDS = 0
    with pytest.raises(ImproperlyConfigured, match="positive integer"):
        invoke(operation)


def test_concurrent_duplicate_uses_real_postgresql_lock(invoke, operation):
    entered = Event()
    release = Event()

    def slow_operation():
        response = operation()
        entered.set()
        if not release.wait(timeout=15):
            raise RuntimeError("test did not release worker")
        return response

    def worker():
        close_old_connections()
        try:
            return invoke(slow_operation)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(worker)
        try:
            assert entered.wait(timeout=15)
            duplicate = invoke(operation)
            assert duplicate.status_code == 409
            assert body(duplicate)["code"] == "idempotency_in_progress"
            assert duplicate["Retry-After"] == "1"
        finally:
            release.set()
        first = future.result(timeout=15)

    replay = invoke(operation)
    assert first.status_code == replay.status_code == 201
    assert first.content == replay.content
    assert replay["Idempotency-Replayed"] == "true"
    assert Group.objects.count() == IdempotencyRecord.objects.count() == 1
