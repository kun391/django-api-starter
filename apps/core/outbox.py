"""Transactional outbox with leased claims and process-local subscriptions."""

from __future__ import annotations

import hashlib
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

from apps.core.models import OutboxEvent
from apps.core.observability import get_request_id
from apps.core.telemetry import counter_add, inject_trace_context, span

_TOPIC_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+){1,15}$")
_SUBSCRIBER_RE = re.compile(r"^[a-z][a-z0-9._-]{0,95}$")
OutboxHandler = Callable[[dict[str, Any]], None]


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


def outbox_handler(topic: str):
    """Legacy decorator retained temporarily; prefer subscribe() with an ID."""

    def decorator(handler: OutboxHandler) -> OutboxHandler:
        identity = f"{handler.__module__}.{handler.__qualname__}"
        stable_id = "legacy." + hashlib.sha256(identity.encode()).hexdigest()[:24]
        subscribe(topic, handler, subscriber_id=stable_id)
        return handler

    return decorator


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

    return cast(OutboxEvent, OutboxEvent.objects.create(
        topic=topic,
        version=version,
        payload=payload,
        metadata=event_metadata,
        available_at=available_at or timezone.now(),
    ))


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
    """Lease ready events so multiple workers can dispatch without sharing rows."""

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
            token = uuid.uuid4()
            event.lock_token = token
            event.locked_until = lock_until
            event.save(update_fields=["lock_token", "locked_until"])
            claimed.append(
                ClaimedEvent(
                    event_id=event.pk,
                    lock_token=token,
                    envelope=_envelope(event),
                )
            )

    return claimed


def _finish_success(claimed: ClaimedEvent) -> None:
    counter_add(
        "app.queue.delivery.results",
        1,
        attributes={"queue": "outbox", "outcome": "success"},
    )
    OutboxEvent.objects.filter(
        pk=claimed.event_id,
        lock_token=claimed.lock_token,
        published_at__isnull=True,
    ).update(
        attempts=models.F("attempts") + 1,
        published_at=timezone.now(),
        locked_until=None,
        lock_token=None,
        last_error_code="",
    )


def _finish_failure(claimed: ClaimedEvent, error: Exception) -> None:
    counter_add(
        "app.queue.delivery.results",
        1,
        attributes={"queue": "outbox", "outcome": "failure"},
    )
    max_attempts = getattr(settings, "OUTBOX_MAX_ATTEMPTS", 10)
    base_seconds = getattr(settings, "OUTBOX_RETRY_BASE_SECONDS", 5)
    max_seconds = getattr(settings, "OUTBOX_RETRY_MAX_SECONDS", 3600)
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value <= 0
        for value in (max_attempts, base_seconds, max_seconds)
    ):
        raise ImproperlyConfigured("Outbox retry settings must be positive integers.")

    with transaction.atomic():
        event = (
            OutboxEvent.objects.select_for_update()
            .filter(pk=claimed.event_id, lock_token=claimed.lock_token)
            .first()
        )
        if event is None:
            return

        event.attempts += 1
        event.last_error_code = type(error).__name__[:128]
        event.locked_until = None
        event.lock_token = None
        if event.attempts >= max_attempts:
            event.dead_lettered_at = timezone.now()
            fields = [
                "attempts",
                "last_error_code",
                "locked_until",
                "lock_token",
                "dead_lettered_at",
            ]
        else:
            delay = min(max_seconds, base_seconds * (2 ** (event.attempts - 1)))
            event.available_at = timezone.now() + timedelta(seconds=delay)
            fields = [
                "attempts",
                "last_error_code",
                "locked_until",
                "lock_token",
                "available_at",
            ]
        event.save(update_fields=fields)


def process_outbox_batch(*, batch_size: int | None = None) -> int:
    """Run registered handlers for one claimed batch; return successful count."""

    successful = 0
    for claimed in claim_outbox_batch(batch_size=batch_size):
        topic = str(claimed.envelope["topic"])
        subscribers = subscriptions_for(topic)
        if not subscribers:
            _finish_failure(
                claimed,
                LookupError(f"No outbox subscriber registered for {topic!r}."),
            )
            continue
        trace_carrier = {
            key: str(value)
            for key, value in claimed.envelope.get("metadata", {}).items()
            if key in {"traceparent", "tracestate"} and isinstance(value, str)
        }
        try:
            with span(
                "outbox.consume",
                carrier=trace_carrier,
                kind="consumer",
                attributes={
                    "messaging.system": "database",
                    "messaging.destination.name": topic,
                    "messaging.message.id": str(claimed.event_id),
                },
            ):
                # PR #43 preserves event-level retry semantics. A failing
                # subscriber aborts this attempt; the entire ordered group
                # runs again at least once. PR #44 introduces per-subscriber
                # durable delivery and independent retry/dead-letter state.
                for subscriber in subscribers:
                    subscriber.handler(claimed.envelope)
        except Exception as exc:
            _finish_failure(claimed, exc)
        else:
            _finish_success(claimed)
            successful += 1
    return successful
