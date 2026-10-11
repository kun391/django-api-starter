"""Durable per-subscriber outbox retry, crash recovery and operator tests."""

import uuid
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from django.utils import timezone

from apps.core import outbox
from apps.core.models import OutboxDelivery, OutboxEvent

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def isolated_subscribers(monkeypatch):
    monkeypatch.setattr(outbox, "_SUBSCRIBERS", {})


def enqueue(topic: str) -> OutboxEvent:
    with transaction.atomic():
        return outbox.record_outbox_event(topic=topic, payload={"id": 123})


def due(event: OutboxEvent, subscriber_id: str) -> None:
    past = timezone.now() - timedelta(seconds=1)
    OutboxEvent.objects.filter(pk=event.pk).update(available_at=past)
    OutboxDelivery.objects.filter(
        event=event, subscriber_id=subscriber_id
    ).update(available_at=past)


def test_delivery_set_is_atomic_and_snapshotted():
    def webhooks(_e):
        return None

    def later(_e):
        return None

    outbox.subscribe("sample.changed", webhooks, subscriber_id="webhooks")
    with pytest.raises(RuntimeError), transaction.atomic():
        outbox.record_outbox_event(topic="sample.changed", payload={})
        assert OutboxDelivery.objects.count() == 1
        raise RuntimeError("rollback")
    assert OutboxEvent.objects.count() == 0
    assert OutboxDelivery.objects.count() == 0

    event = enqueue("sample.changed")
    outbox.subscribe("sample.changed", later, subscriber_id="notifications")
    assert outbox.process_outbox_batch() == 1
    assert list(
        OutboxDelivery.objects.filter(event=event).values_list(
            "subscriber_id", flat=True
        )
    ) == ["webhooks"]


def test_subscriber_isolation_and_independent_dead_letter(settings):
    settings.OUTBOX_MAX_ATTEMPTS = 1
    calls: list[str] = []

    def fail(_e):
        calls.append("broken")
        raise RuntimeError("do-not-store-secret")

    def succeed(_e):
        calls.append("healthy")

    outbox.subscribe("sample.changed", fail, subscriber_id="broken", order=10)
    outbox.subscribe("sample.changed", succeed, subscriber_id="healthy", order=20)
    event = enqueue("sample.changed")
    assert outbox.process_outbox_batch() == 0
    assert calls == ["broken", "healthy"]
    broken = OutboxDelivery.objects.get(event=event, subscriber_id="broken")
    healthy = OutboxDelivery.objects.get(event=event, subscriber_id="healthy")
    assert broken.attempts == 1
    assert broken.dead_lettered_at is not None
    assert broken.last_error_code == "RuntimeError"
    assert "do-not-store-secret" not in broken.last_error_code
    assert healthy.succeeded_at is not None
    assert healthy.attempts == 1
    event.refresh_from_db()
    assert event.dead_lettered_at is not None
    assert event.published_at is None
    assert outbox.process_outbox_batch() == 0


def test_requeue_only_failed_subscriber_without_replaying_sibling(settings):
    settings.OUTBOX_MAX_ATTEMPTS = 1
    flags = {"fail": True}
    called: list[str] = []

    def broken(_e):
        called.append("broken")
        if flags["fail"]:
            raise RuntimeError("failure")

    def healthy(_e):
        called.append("healthy")

    outbox.subscribe("sample.changed", broken, subscriber_id="broken")
    outbox.subscribe("sample.changed", healthy, subscriber_id="healthy")
    event = enqueue("sample.changed")
    assert outbox.process_outbox_batch() == 0
    assert OutboxDelivery.objects.filter(
        event=event, dead_lettered_at__isnull=False
    ).count() == 1

    call_command(
        "requeue_outbox", str(event.pk), subscriber_id="broken",
        stdout=StringIO(),
    )
    flags["fail"] = False
    assert outbox.process_outbox_batch() == 1
    assert called.count("healthy") == 1
    assert called.count("broken") == 2
    assert OutboxEvent.objects.get(pk=event.pk).published_at is not None
    assert OutboxDelivery.objects.get(
        event=event, subscriber_id="broken"
    ).attempts == 1


def test_multiple_dead_subscribers_require_explicit_target(settings):
    settings.OUTBOX_MAX_ATTEMPTS = 1

    def fail(_e):
        raise RuntimeError("temporary")

    outbox.subscribe("sample.changed", fail, subscriber_id="first")
    outbox.subscribe("sample.changed", fail, subscriber_id="second")
    event = enqueue("sample.changed")
    assert outbox.process_outbox_batch() == 0
    with pytest.raises(CommandError, match="Multiple"):
        call_command("requeue_outbox", str(event.pk), stdout=StringIO())
    with pytest.raises(CommandError, match="No matching"):
        call_command(
            "requeue_outbox", str(event.pk), subscriber_id="missing",
            stdout=StringIO(),
        )
    call_command(
        "requeue_outbox", str(event.pk), subscriber_id="first",
        stdout=StringIO(),
    )
    assert OutboxDelivery.objects.get(
        event=event, subscriber_id="first"
    ).dead_lettered_at is None
    assert OutboxDelivery.objects.get(
        event=event, subscriber_id="second"
    ).dead_lettered_at is not None


def test_missing_registered_handler_is_retryable_independently(settings):
    settings.OUTBOX_MAX_ATTEMPTS = 1
    calls: list[str] = []

    def handler(_e):
        calls.append("healthy")

    outbox.subscribe("sample.changed", handler, subscriber_id="missing")
    outbox.subscribe("sample.changed", handler, subscriber_id="healthy")
    event = enqueue("sample.changed")
    outbox._SUBSCRIBERS["sample.changed"].pop("missing")
    assert outbox.process_outbox_batch() == 0
    assert calls == ["healthy"]
    assert OutboxDelivery.objects.get(
        event=event, subscriber_id="missing"
    ).last_error_code == "LookupError"


def test_worker_lost_lease_does_not_ack_new_lease():
    delivered: list[str] = []

    def callback(_e):
        delivered.append("called")

    outbox.subscribe("sample.changed", callback, subscriber_id="worker")
    event = enqueue("sample.changed")
    old = outbox.claim_outbox_batch(batch_size=1)[0]
    OutboxEvent.objects.filter(pk=event.pk).update(
        locked_until=timezone.now() - timedelta(seconds=1)
    )
    fresh = outbox.claim_outbox_batch(batch_size=1)[0]
    assert fresh.lock_token != old.lock_token
    outbox._finish_delivery(old, "worker", None)
    assert OutboxDelivery.objects.get(
        event=event, subscriber_id="worker"
    ).succeeded_at is None
    outbox._finish_delivery(fresh, "worker", None)
    assert outbox._finalize_event(fresh)
    assert OutboxEvent.objects.get(pk=event.pk).published_at is not None


def test_pending_sibling_keeps_event_retryable_after_other_dead(settings):
    settings.OUTBOX_MAX_ATTEMPTS = 1
    current = timezone.now()
    def fail(_e):
        raise RuntimeError("broken")

    outbox.subscribe("sample.changed", fail, subscriber_id="fatal")
    outbox.subscribe("sample.changed", lambda _e: None, subscriber_id="later")
    event = enqueue("sample.changed")
    OutboxDelivery.objects.filter(
        event=event, subscriber_id="later"
    ).update(available_at=current + timedelta(minutes=5))
    assert outbox.process_outbox_batch() == 0
    event.refresh_from_db()
    assert event.dead_lettered_at is None
    assert event.published_at is None
    assert OutboxDelivery.objects.get(
        event=event, subscriber_id="fatal"
    ).dead_lettered_at is not None
    assert event.available_at > current


def test_legacy_missing_subscriber_dead_letter_is_requeueable(settings):
    settings.OUTBOX_MAX_ATTEMPTS = 1
    event = enqueue("missing.topic")
    assert outbox.process_outbox_batch() == 0
    assert OutboxEvent.objects.get(pk=event.pk).dead_lettered_at is not None
    call_command("requeue_outbox", str(event.pk), stdout=StringIO())
    assert OutboxEvent.objects.get(pk=event.pk).dead_lettered_at is None


def test_active_outbox_lease_blocks_requeue(settings):
    settings.OUTBOX_MAX_ATTEMPTS = 1

    def fail(_e):
        raise RuntimeError("failure")

    outbox.subscribe("sample.changed", fail, subscriber_id="broken")
    event = enqueue("sample.changed")
    assert outbox.process_outbox_batch() == 0
    OutboxEvent.objects.filter(pk=event.pk).update(
        locked_until=timezone.now() + timedelta(seconds=30),
        lock_token=uuid.uuid4(),
    )
    with pytest.raises(CommandError, match="Active"):
        call_command(
            "requeue_outbox", str(event.pk),
            subscriber_id="broken", stdout=StringIO(),
        )


def test_active_subscriber_lease_blocks_targeted_requeue(settings):
    settings.OUTBOX_MAX_ATTEMPTS = 1

    def fail(_e):
        raise RuntimeError("failure")

    outbox.subscribe("sample.changed", fail, subscriber_id="broken")
    event = enqueue("sample.changed")
    assert outbox.process_outbox_batch() == 0
    OutboxDelivery.objects.filter(
        event=event, subscriber_id="broken"
    ).update(
        locked_until=timezone.now() + timedelta(seconds=30),
        lock_token=uuid.uuid4(),
    )
    with pytest.raises(CommandError, match="Active subscriber lease"):
        call_command(
            "requeue_outbox", str(event.pk),
            subscriber_id="broken", stdout=StringIO(),
        )
