"""Transactional outbox with leased claims and process-local subscriptions."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, cast

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, models, transaction
from django.utils import timezone

from apps.core.models import OutboxDelivery, OutboxEvent
from apps.core.observability import get_request_id
from apps.core.telemetry import counter_add, inject_trace_context, span

_TOPIC_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+){1,15}$")
_SUBSCRIBER_RE = re.compile(r"^[a-z][a-z0-9._-]{0,95}$")
OutboxHandler = Callable[[dict[str, Any]], object]


@dataclass(frozen=True)
class Subscription:
    """Named process-local subscriber; IDs are stable across deployments."""

    subscriber_id: str
    handler: OutboxHandler
    order: int = 100


_SUBSCRIBERS: dict[str, dict[str, Subscription]] = {}


@dataclass(frozen=True)
class ClaimedEvent:
    event_id: uuid.UUID
    lock_token: uuid.UUID
    envelope: dict[str, Any]
    subscriber_ids: tuple[str, ...] = ()


def subscribe(
    topic: str,
    handler: OutboxHandler,
    *,
    subscriber_id: str,
    order: int = 100,
) -> None:
    """Subscribe independently of producer imports and INSTALLED_APPS order.

    Repeating the exact registration is safe when AppConfig.ready() runs again.
    Changing the handler/order for an existing identity is always an error.
    """
    _validate_topic(topic)
    if not isinstance(subscriber_id, str) or not _SUBSCRIBER_RE.fullmatch(
        subscriber_id
    ):
        raise ValueError("subscriber_id must be a stable, lowercase identifier")
    if not callable(handler):
        raise TypeError("Outbox subscriber handler must be callable")
    if isinstance(order, bool) or not isinstance(order, int) or not 0 <= order <= 1000:
        raise ValueError("Outbox subscriber order must be an integer in 0-1000")

    by_topic = _SUBSCRIBERS.setdefault(topic, {})
    existing = by_topic.get(subscriber_id)
    if existing is not None:
        if existing.handler is handler and existing.order == order:
            return
        raise ImproperlyConfigured(
            f"Outbox subscriber {subscriber_id!r} already registered for {topic!r}."
        )
    by_topic[subscriber_id] = Subscription(subscriber_id, handler, order)


def subscriptions_for(topic: str) -> tuple[Subscription, ...]:
    """Immutable, deterministically ordered view of a topic's subscribers."""
    _validate_topic(topic)
    return tuple(
        sorted(
            _SUBSCRIBERS.get(topic, {}).values(),
            key=lambda registration: (registration.order, registration.subscriber_id),
        )
    )


def registered_subscriptions() -> dict[str, tuple[str, ...]]:
    """Inspect topic -> stable subscriber IDs without touching private state."""
    return {
        topic: tuple(item.subscriber_id for item in subscriptions_for(topic))
        for topic in sorted(_SUBSCRIBERS)
    }


def _validate_topic(topic: str) -> None:
    if not _TOPIC_RE.fullmatch(topic):
        raise ValueError(
            "Outbox topics must be lowercase dotted/dashed/underscored names "
            "with at least two segments."
        )


def _payload_size(payload: object, metadata: object) -> int:
    value = json.dumps(
        {"payload": payload, "metadata": metadata},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return len(value.encode("utf-8"))


def record_outbox_event(
    *,
    topic: str,
    payload: dict[str, Any],
    version: int = 1,
    metadata: dict[str, Any] | None = None,
    available_at=None,
) -> OutboxEvent:
    """Persist an event inside the caller's business transaction."""

    if not connection.in_atomic_block:
        raise ImproperlyConfigured(
            "record_outbox_event() must run inside transaction.atomic() so the "
            "business write and event commit together."
        )
    _validate_topic(topic)
    if isinstance(version, bool) or not isinstance(version, int) or not 1 <= version <= 32767:
        raise ValueError("Outbox event version must be an integer between 1 and 32767.")

    event_metadata = inject_trace_context(dict(metadata or {}))
    request_id = get_request_id()
    if request_id and "request_id" not in event_metadata:
        event_metadata["request_id"] = request_id

    max_bytes = getattr(settings, "OUTBOX_MAX_EVENT_BYTES", 64 * 1024)
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
        raise ImproperlyConfigured("OUTBOX_MAX_EVENT_BYTES must be a positive integer.")
    if _payload_size(payload, event_metadata) > max_bytes:
        raise ValueError(f"Outbox event exceeds the {max_bytes}-byte payload limit.")

    event = cast(OutboxEvent, OutboxEvent.objects.create(
        topic=topic,
        version=version,
        payload=payload,
        metadata=event_metadata,
        available_at=available_at or timezone.now(),
    ))
    # Capture the subscriber set in the SAME transaction as the business event.
    # New registrations do not retroactively fan out an already-seeded event.
    _create_deliveries(event)
    return event


def _seed_deliveries(event: OutboxEvent) -> None:
    """Bootstrap legacy unpublished events without delivery rows at claim time.

    Called under an event row lock by the worker. The existing set is
    immutable across app upgrades (unless an operator deliberately requeues a
    dead or missing subscription).
    """
    if OutboxDelivery.objects.filter(event=event).exists():
        return
    _create_deliveries(event)


def _create_deliveries(event: OutboxEvent) -> None:
    subscriptions = subscriptions_for(event.topic)
    if subscriptions:
        OutboxDelivery.objects.bulk_create(
            [
                OutboxDelivery(
                    event=event,
                    subscriber_id=item.subscriber_id,
                    available_at=event.available_at,
                )
                for item in subscriptions
            ],
            ignore_conflicts=True,
        )


def _envelope(event: OutboxEvent) -> dict[str, Any]:
    return {
        "id": str(event.pk),
        "topic": event.topic,
        "version": event.version,
        "occurred_at": event.occurred_at.isoformat(),
        "payload": event.payload,
        "metadata": event.metadata,
    }


def claim_outbox_batch(
    *,
    batch_size: int | None = None,
    lease_seconds: int | None = None,
) -> list[ClaimedEvent]:
    """Lease events and their *due* subscriber deliveries atomically.

    Event lease serializes a particular event's dispatch across workers;
    individual delivery rows persist status and backoff independently.
    """
    size = batch_size if batch_size is not None else getattr(settings, "OUTBOX_BATCH_SIZE", 100)
    lease = lease_seconds if lease_seconds is not None else getattr(
        settings, "OUTBOX_LEASE_SECONDS", 60
    )
    if (
        not isinstance(size, int)
        or isinstance(size, bool)
        or not 1 <= size <= 1000
        or not isinstance(lease, int)
        or isinstance(lease, bool)
        or lease <= 0
    ):
        raise ImproperlyConfigured(
            "Outbox batch size must be 1-1000 and lease seconds must be positive."
        )

    now = timezone.now()
    lock_until = now + timedelta(seconds=lease)
    claimed: list[ClaimedEvent] = []

    with transaction.atomic():
        events = list(
            OutboxEvent.objects.select_for_update(skip_locked=True)
            .filter(
                published_at__isnull=True,
                dead_lettered_at__isnull=True,
                available_at__lte=now,
            )
            .filter(
                models.Q(locked_until__isnull=True) | models.Q(locked_until__lte=now)
            )
            .order_by("occurred_at", "id")[:size]
        )
        for event in events:
            _seed_deliveries(event)
            token = uuid.uuid4()
            event.lock_token = token
            event.locked_until = lock_until
            event.save(update_fields=["lock_token", "locked_until"])

            # The event row is the serialization boundary. A lost lease is
            # reclaimable even when a stale subscriber lease was never cleared.
            due = list(
                OutboxDelivery.objects.select_for_update()
                .filter(
                    event=event,
                    succeeded_at__isnull=True,
                    dead_lettered_at__isnull=True,
                    available_at__lte=now,
                )
                .order_by("subscriber_id")
            )
            for delivery in due:
                delivery.lock_token = token
                delivery.locked_until = lock_until
            if due:
                OutboxDelivery.objects.bulk_update(
                    due, ["lock_token", "locked_until"]
                )
            claimed.append(
                ClaimedEvent(
                    event_id=event.pk,
                    lock_token=token,
                    envelope=_envelope(event),
                    subscriber_ids=tuple(d.subscriber_id for d in due),
                )
            )

    return claimed


def _retry_options() -> tuple[int, int, int]:
    attempts = getattr(settings, "OUTBOX_MAX_ATTEMPTS", 10)
    base = getattr(settings, "OUTBOX_RETRY_BASE_SECONDS", 5)
    limit = getattr(settings, "OUTBOX_RETRY_MAX_SECONDS", 3600)
    if any(
        isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535
        for value in (attempts, base, limit)
    ):
        raise ImproperlyConfigured("Outbox retry settings must be positive integers.")
    return attempts, base, limit


def _finish_missing_subscribers(claimed: ClaimedEvent) -> None:
    """Legacy or newly introduced topic with no registered handlers.

    No row can be acknowledged without a subscriber identity; keep retrying
    until the configured limit rather than treating a missing handler as done.
    """
    max_attempts, base_seconds, max_seconds = _retry_options()
    with transaction.atomic():
        event = (
            OutboxEvent.objects.select_for_update()
            .filter(pk=claimed.event_id, lock_token=claimed.lock_token)
            .first()
        )
        if event is None:
            return
        event.attempts += 1
        event.last_error_code = "LookupError"
        event.locked_until = None
        event.lock_token = None
        fields = ["attempts", "last_error_code", "locked_until", "lock_token"]
        if event.attempts >= max_attempts:
            event.dead_lettered_at = timezone.now()
            fields.append("dead_lettered_at")
        else:
            delay = min(max_seconds, base_seconds * (2 ** (event.attempts - 1)))
            event.available_at = timezone.now() + timedelta(seconds=delay)
            fields.append("available_at")
        event.save(update_fields=fields)
    counter_add(
        "app.queue.delivery.results", 1,
        attributes={"queue": "outbox", "outcome": "failure"},
    )


def _finish_delivery(
    claimed: ClaimedEvent, subscriber_id: str, error: Exception | None
) -> None:
    """Acknowledge ONE subscriber using the event's fencing token.

    Successful deliveries never become eligible again on another subscriber's
    failure. A worker with an expired/replaced lease cannot overwrite status.
    """
    max_attempts, base_seconds, max_seconds = _retry_options()
    with transaction.atomic():
        owned = OutboxEvent.objects.select_for_update().filter(
            pk=claimed.event_id, lock_token=claimed.lock_token
        ).exists()
        if not owned:
            return
        delivery = (
            OutboxDelivery.objects.select_for_update()
            .filter(
                event_id=claimed.event_id,
                subscriber_id=subscriber_id,
                lock_token=claimed.lock_token,
                succeeded_at__isnull=True,
                dead_lettered_at__isnull=True,
            )
            .first()
        )
        if delivery is None:
            return

        delivery.attempts += 1
        delivery.lock_token = None
        delivery.locked_until = None
        fields = [
            "attempts", "lock_token", "locked_until",
        ]
        if error is None:
            delivery.succeeded_at = timezone.now()
            delivery.last_error_code = ""
            fields.extend(["succeeded_at", "last_error_code"])
        else:
            delivery.last_error_code = type(error).__name__[:128]
            fields.append("last_error_code")
            if delivery.attempts >= max_attempts:
                delivery.dead_lettered_at = timezone.now()
                fields.append("dead_lettered_at")
            else:
                delay = min(
                    max_seconds, base_seconds * (2 ** (delivery.attempts - 1))
                )
                delivery.available_at = timezone.now() + timedelta(seconds=delay)
                fields.append("available_at")
        delivery.save(update_fields=fields)
    counter_add(
        "app.queue.delivery.results", 1,
        attributes={
            "queue": "outbox_subscribers",
            "outcome": "success" if error is None else "failure",
        },
    )


def _finalize_event(claimed: ClaimedEvent, error_code: str = "") -> bool:
    """Recalculate aggregate event status from persisted deliveries.

    A single dead-lettered subscriber cannot short-circuit pending siblings.
    The aggregate event becomes dead-lettered only once no retryable siblings
    remain; only ALL succeeded permits published_at.
    """
    with transaction.atomic():
        event = (
            OutboxEvent.objects.select_for_update()
            .filter(pk=claimed.event_id, lock_token=claimed.lock_token)
            .first()
        )
        if event is None:
            return False

        deliveries = list(OutboxDelivery.objects.filter(event=event))
        pending = [
            d for d in deliveries
            if d.succeeded_at is None and d.dead_lettered_at is None
        ]
        any_dead = any(d.dead_lettered_at is not None for d in deliveries)
        event.attempts += 1
        event.lock_token = None
        event.locked_until = None
        fields = ["attempts", "lock_token", "locked_until"]
        if error_code:
            event.last_error_code = error_code
            fields.append("last_error_code")
        if pending:
            event.available_at = min(d.available_at for d in pending)
            fields.append("available_at")
        elif any_dead:
            event.dead_lettered_at = timezone.now()
            fields.append("dead_lettered_at")
        elif deliveries:
            event.published_at = timezone.now()
            event.last_error_code = ""
            fields.extend(["published_at", "last_error_code"])
        else:
            # Never mark an unhandled event as published.
            raise ImproperlyConfigured("Cannot finish outbox event without deliveries")
        event.save(update_fields=fields)
        success = event.published_at is not None

    counter_add(
        "app.queue.delivery.results", 1,
        attributes={
            "queue": "outbox",
            "outcome": "success" if success else "failure",
        },
    )
    return success


def process_outbox_batch(*, batch_size: int | None = None) -> int:
    """Dispatch due subscribers independently and count completed events."""
    successful = 0
    for claimed in claim_outbox_batch(batch_size=batch_size):
        topic = str(claimed.envelope["topic"])
        if not OutboxDelivery.objects.filter(event_id=claimed.event_id).exists():
            _finish_missing_subscribers(claimed)
            continue
        subscribers = {
            subscriber.subscriber_id: subscriber
            for subscriber in subscriptions_for(topic)
        }
        trace_carrier = {
            key: str(value)
            for key, value in claimed.envelope.get("metadata", {}).items()
            if key in {"traceparent", "tracestate"} and isinstance(value, str)
        }
        latest_error = ""
        # Order applies within this dispatch pass only; a retried subscriber
        # may run after siblings that ran later in an earlier pass.
        due_ids = sorted(
            claimed.subscriber_ids,
            key=lambda subscriber_id: (
                subscribers[subscriber_id].order
                if subscriber_id in subscribers else 100,
                subscriber_id,
            ),
        )
        for subscriber_id in due_ids:
            try:
                subscriber = subscribers.get(subscriber_id)
                if subscriber is None:
                    raise LookupError(
                        f"Missing outbox subscriber {subscriber_id!r} on {topic!r}"
                    )
                with span(
                    "outbox.consume",
                    carrier=trace_carrier,
                    kind="consumer",
                    attributes={
                        "messaging.system": "database",
                        "messaging.destination.name": topic,
                        "messaging.message.id": str(claimed.event_id),
                        "messaging.consumer.name": subscriber_id,
                    },
                ):
                    subscriber.handler(claimed.envelope)
            except Exception as exc:
                latest_error = type(exc).__name__[:128]
                _finish_delivery(claimed, subscriber_id, exc)
                # Continue: each sibling's success/failure must be independent.
            else:
                _finish_delivery(claimed, subscriber_id, None)
        if _finalize_event(claimed, latest_error):
            successful += 1
    return successful
