from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, cast

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.core.audit import record_audit_event

from .models import NotificationDelivery, NotificationPreference
from .provider import EmailProvider, get_email_provider
from .rendering import render_notification
from .topics import NOTIFICATION_TOPICS, SUPPORTED_NOTIFICATION_TOPICS


@dataclass(frozen=True)
class ClaimedDelivery:
    delivery_id: uuid.UUID
    lock_token: uuid.UUID


def effective_email_enabled(*, user_id: int, topic: str) -> bool:
    value = (
        NotificationPreference.objects.filter(user_id=user_id, topic=topic)
        .values_list("email_enabled", flat=True)
        .first()
    )
    return True if value is None else bool(value)


def set_preference(*, actor, topic: str, email_enabled: bool) -> NotificationPreference:
    if topic not in SUPPORTED_NOTIFICATION_TOPICS:
        raise ValidationError("Unsupported notification topic.")
    with transaction.atomic():
        preference, _ = NotificationPreference.objects.update_or_create(
            user=actor,
            topic=topic,
            defaults={"email_enabled": email_enabled},
        )
        if not email_enabled:
            NotificationDelivery.objects.filter(
                recipient_user=actor,
                event_topic=topic,
                delivered_at__isnull=True,
                failed_at__isnull=True,
                cancelled_at__isnull=True,
            ).update(
                cancelled_at=timezone.now(),
                locked_until=None,
                lock_token=None,
            )
        record_audit_event(
            action="notifications.preference-updated",
            subject_type="notification_preference",
            subject_id=f"{actor.pk}:{topic}",
            actor_id=actor.pk,
            metadata={"topic": topic, "email_enabled": email_enabled},
        )
    return cast(NotificationPreference, preference)


def preference_rows(*, user_id: int) -> list[dict[str, object]]:
    overrides = {
        row.topic: row.email_enabled
        for row in NotificationPreference.objects.filter(user_id=user_id)
    }
    return [
        {
            "topic": topic,
            "email_enabled": overrides.get(topic, True),
            "is_override": topic in overrides,
        }
        for topic in SUPPORTED_NOTIFICATION_TOPICS
    ]


def _dedupe_key(source_event_id, recipient_user_id: int) -> str:
    value = f"{source_event_id}:{recipient_user_id}:email".encode()
    return "event:" + hashlib.sha256(value).hexdigest()


def fanout_event(envelope: dict[str, Any]) -> int:
    topic = str(envelope["topic"])
    definition = NOTIFICATION_TOPICS.get(topic)
    if definition is None:
        return 0

    payload = envelope.get("payload", {})
    raw_recipient_id = payload.get(definition.recipient_field)
    if raw_recipient_id is None:
        return 0
    try:
        recipient_id = int(raw_recipient_id)
    except (TypeError, ValueError):
        return 0

    user = (
        get_user_model()
        .objects.filter(pk=recipient_id, is_active=True)
        .only("id", "email")
        .first()
    )
    if user is None or not user.email:
        return 0
    if not effective_email_enabled(user_id=user.pk, topic=topic):
        return 0

    context = {
        "event_topic": topic,
        "event_id": str(envelope["id"]),
        "occurred_at": envelope["occurred_at"],
        **payload,
    }
    delivery_id = uuid.uuid4()
    _, created = NotificationDelivery.objects.get_or_create(
        dedupe_key=_dedupe_key(envelope["id"], user.pk),
        defaults={
            "id": delivery_id,
            "source_event_id": envelope["id"],
            "event_topic": topic,
            "event_version": int(envelope["version"]),
            "recipient_user": user,
            "recipient_user_id_snapshot": user.pk,
            "recipient_email": user.email,
            "template_slug": definition.template_slug,
            "template_context": context,
        },
    )
    return int(created)


def claim_delivery_batch(*, batch_size: int | None = None) -> list[ClaimedDelivery]:
    size = (
        batch_size
        if batch_size is not None
        else getattr(settings, "NOTIFICATION_BATCH_SIZE", 100)
    )
    lease = getattr(settings, "NOTIFICATION_LEASE_SECONDS", 60)
    if (
        not isinstance(size, int)
        or isinstance(size, bool)
        or not 1 <= size <= 1000
        or not isinstance(lease, int)
        or isinstance(lease, bool)
        or lease <= 0
    ):
        raise ImproperlyConfigured(
            "Notification batch size must be 1-1000 and lease must be positive."
        )

    now = timezone.now()
    claimed: list[ClaimedDelivery] = []
    with transaction.atomic():
        deliveries = list(
            NotificationDelivery.objects.select_for_update(skip_locked=True)
            .filter(
                delivered_at__isnull=True,
                failed_at__isnull=True,
                cancelled_at__isnull=True,
                next_attempt_at__lte=now,
            )
            .filter(
                models.Q(locked_until__isnull=True)
                | models.Q(locked_until__lte=now)
            )
            .order_by("created_at", "id")[:size]
        )
        for delivery in deliveries:
            token = uuid.uuid4()
            delivery.lock_token = token
            delivery.locked_until = now + timedelta(seconds=lease)
            delivery.save(update_fields=["lock_token", "locked_until"])
            claimed.append(ClaimedDelivery(delivery.pk, token))
    return claimed


def _finish_success(claim: ClaimedDelivery) -> None:
    now = timezone.now()
    NotificationDelivery.objects.filter(
        pk=claim.delivery_id,
        lock_token=claim.lock_token,
    ).update(
        attempts=models.F("attempts") + 1,
        last_attempt_at=now,
        delivered_at=now,
        last_error_code="",
        locked_until=None,
        lock_token=None,
    )


def _finish_failure(claim: ClaimedDelivery, error: Exception) -> None:
    max_attempts = getattr(settings, "NOTIFICATION_MAX_ATTEMPTS", 8)
    base_seconds = getattr(settings, "NOTIFICATION_RETRY_BASE_SECONDS", 30)
    max_seconds = getattr(settings, "NOTIFICATION_RETRY_MAX_SECONDS", 3600)
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value <= 0
        for value in (max_attempts, base_seconds, max_seconds)
    ):
        raise ImproperlyConfigured(
            "Notification retry settings must be positive integers."
        )

    now = timezone.now()
    with transaction.atomic():
        delivery = (
            NotificationDelivery.objects.select_for_update()
            .filter(pk=claim.delivery_id, lock_token=claim.lock_token)
            .first()
        )
        if delivery is None:
            return
        delivery.attempts += 1
        delivery.last_attempt_at = now
        delivery.last_error_code = type(error).__name__[:128]
        delivery.locked_until = None
        delivery.lock_token = None
        fields = [
            "attempts",
            "last_attempt_at",
            "last_error_code",
            "locked_until",
            "lock_token",
        ]
        if delivery.attempts >= max_attempts:
            delivery.failed_at = now
            fields.append("failed_at")
        else:
            delay = min(max_seconds, base_seconds * (2 ** (delivery.attempts - 1)))
            delivery.next_attempt_at = now + timedelta(seconds=delay)
            fields.append("next_attempt_at")
        delivery.save(update_fields=fields)


def process_delivery_batch(
    *,
    batch_size: int | None = None,
    provider: EmailProvider | None = None,
) -> int:
    sender = provider or get_email_provider()
    successful = 0
    for claim in claim_delivery_batch(batch_size=batch_size):
        delivery = (
            NotificationDelivery.objects.select_related("recipient_user")
            .filter(pk=claim.delivery_id, lock_token=claim.lock_token)
            .first()
        )
        if delivery is None:
            continue

        user = delivery.recipient_user
        if (
            user is None
            or not user.is_active
            or not effective_email_enabled(
                user_id=user.pk,
                topic=delivery.event_topic,
            )
        ):
            NotificationDelivery.objects.filter(
                pk=delivery.pk,
                lock_token=claim.lock_token,
            ).update(
                cancelled_at=timezone.now(),
                locked_until=None,
                lock_token=None,
            )
            continue

        try:
            rendered = render_notification(
                delivery.template_slug,
                delivery.template_context,
            )
            sender.send_email(
                subject=rendered.subject,
                body=rendered.body,
                recipient=delivery.recipient_email,
            )
        except Exception as exc:
            _finish_failure(claim, exc)
            continue

        _finish_success(claim)
        successful += 1
    return successful
