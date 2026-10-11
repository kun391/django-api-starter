"""Public outbox subscription API and transitional event-level retry contract."""

from datetime import timedelta

import pytest
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone

from apps.core import outbox
from apps.core.models import OutboxEvent


@pytest.fixture(autouse=True)
def isolate_registry(monkeypatch):
    # Never mutate the live application startup registry from synthetic tests.
    monkeypatch.setattr(outbox, "_SUBSCRIBERS", {})


def test_register_before_producer_and_order_is_stable():
    called = []

    def first(_event):
        called.append("first")

    def second(_event):
        called.append("second")

    # Registration order must not determine execution order.
    outbox.subscribe("tickets.ticket-created", second, subscriber_id="notifications", order=20)
    outbox.subscribe("tickets.ticket-created", first, subscriber_id="webhooks", order=10)

    registrations = outbox.subscriptions_for("tickets.ticket-created")
    assert tuple(item.subscriber_id for item in registrations) == (
        "webhooks", "notifications"
    )
    assert outbox.registered_subscriptions() == {
        "tickets.ticket-created": ("webhooks", "notifications")
    }
    assert registrations[0].handler is first
    assert registrations[1].handler is second
    # The public read-only view does not expose mutable internal registry.
    assert isinstance(registrations, tuple)


def test_tie_breaker_is_subscriber_id():
    outbox.subscribe("tasks.item-ready", lambda event: None, subscriber_id="z-last")
    outbox.subscribe("tasks.item-ready", lambda event: None, subscriber_id="a-first")
    assert outbox.registered_subscriptions()["tasks.item-ready"] == ("a-first", "z-last")


def test_duplicate_ready_registration_is_idempotent():
    def callback(_envelope):
        return None

    outbox.subscribe("orders.order-created", callback, subscriber_id="webhooks", order=10)
    outbox.subscribe("orders.order-created", callback, subscriber_id="webhooks", order=10)
    assert len(outbox.subscriptions_for("orders.order-created")) == 1


def test_collision_is_fail_closed():
    def one(_event):
        pass

    def other(_event):
        pass

    outbox.subscribe("orders.order-created", one, subscriber_id="webhooks")
    with pytest.raises(ImproperlyConfigured, match="already registered"):
        outbox.subscribe("orders.order-created", other, subscriber_id="webhooks")
    with pytest.raises(ImproperlyConfigured, match="already registered"):
        outbox.subscribe("orders.order-created", one, subscriber_id="webhooks", order=50)


@pytest.mark.parametrize(
    ("topic", "subscriber_id", "order", "expected"),
    [
        ("invalid", "webhooks", 10, ValueError),
        ("orders.order-created", "Bad-ID", 10, ValueError),
        ("orders.order-created", "", 10, ValueError),
        ("orders.order-created", "webhooks", True, ValueError),
        ("orders.order-created", "webhooks", -1, ValueError),
        ("orders.order-created", "webhooks", 1001, ValueError),
    ],
)
def test_invalid_registration_is_rejected(topic, subscriber_id, order, expected):
    with pytest.raises(expected):
        outbox.subscribe(topic, lambda _event: None, subscriber_id=subscriber_id, order=order)


def test_subscriber_must_be_callable():
    with pytest.raises(TypeError, match="callable"):
        outbox.subscribe("orders.order-created", None, subscriber_id="bad")


@pytest.mark.parametrize(
    "registration_order",
    [
        ("webhooks", "notifications", "files"),
        ("files", "notifications", "webhooks"),
    ],
)
def test_django_ready_is_repeatable_independent_of_producer_order(registration_order):
    # A producer must never register placeholder callbacks for consumers to attach.
    for name in registration_order:
        apps.get_app_config(name).ready()
    for name in registration_order:
        apps.get_app_config(name).ready()

    assert outbox.registered_subscriptions()["tickets.ticket-created"] == (
        "webhooks", "notifications"
    )
    assert outbox.registered_subscriptions()["organizations.organization-updated"] == (
        "webhooks",
    )
    assert outbox.registered_subscriptions()["files.deletion-requested"] == (
        "files.delete-stored-object",
    )
    assert outbox.subscriptions_for("organizations.organization-created")[0].order == 10


@pytest.mark.django_db(transaction=True)
def test_multiple_subscribers_dispatch_once_in_stable_order():
    called = []

    def first(_envelope):
        called.append("webhooks")

    def second(_envelope):
        called.append("notifications")

    outbox.subscribe("orders.order-created", second, subscriber_id="notifications", order=20)
    outbox.subscribe("orders.order-created", first, subscriber_id="webhooks", order=10)

    with transaction.atomic():
        event = outbox.record_outbox_event(topic="orders.order-created", payload={"id": 42})
    assert outbox.process_outbox_batch() == 1
    event.refresh_from_db()
    assert event.published_at is not None
    assert event.attempts == 1
    assert called == ["webhooks", "notifications"]


@pytest.mark.django_db(transaction=True)
def test_transitional_shared_retry_replays_completed_subscribers(settings):
    """PR #44 must replace this shared retry behavior with per-subscriber state."""
    settings.OUTBOX_RETRY_BASE_SECONDS = 1
    calls = []
    state = {"fail": True}

    def webhooks(_envelope):
        calls.append("webhooks")

    def notifications(_envelope):
        calls.append("notifications")
        if state["fail"]:
            raise RuntimeError("downstream-failure")

    outbox.subscribe("tickets.ticket-updated", notifications, subscriber_id="notifications", order=20)
    outbox.subscribe("tickets.ticket-updated", webhooks, subscriber_id="webhooks", order=10)

    with transaction.atomic():
        event = outbox.record_outbox_event(topic="tickets.ticket-updated", payload={})
    assert outbox.process_outbox_batch() == 0
    event.refresh_from_db()
    assert event.published_at is None
    assert event.attempts == 1
    assert event.last_error_code == "RuntimeError"
    assert calls == ["webhooks", "notifications"]

    state["fail"] = False
    OutboxEvent.objects.filter(pk=event.pk).update(
        available_at=timezone.now() - timedelta(seconds=1)
    )
    assert outbox.process_outbox_batch() == 1
    event.refresh_from_db()
    assert event.published_at is not None
    assert calls == ["webhooks", "notifications", "webhooks", "notifications"]


@pytest.mark.django_db(transaction=True)
def test_event_without_subscribers_still_retries():
    with transaction.atomic():
        event = outbox.record_outbox_event(topic="missing.subscriber", payload={})
    assert outbox.process_outbox_batch() == 0
    event.refresh_from_db()
    assert event.published_at is None
    assert event.attempts == 1
    assert event.last_error_code == "LookupError"
