from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models, transaction
from django.utils import timezone

from apps.core.models import (
    AuditEvent,
    IdempotencyRecord,
    OutboxEvent,
    SecurityThrottleBucket,
)
from apps.files.models import PrivateFile
from apps.notifications.models import NotificationDelivery
from apps.webhooks.models import WebhookDelivery


@dataclass(frozen=True)
class RetentionCategory:
    name: str
    setting_name: str | None
    description: str


@dataclass(frozen=True)
class RetentionResult:
    category: str
    enabled: bool
    candidates: int
    deleted: int


CATEGORIES: tuple[RetentionCategory, ...] = (
    RetentionCategory(
        "idempotency",
        None,
        "Expired idempotency replay records.",
    ),
    RetentionCategory(
        "security_throttles",
        None,
        "Expired security throttle buckets.",
    ),
    RetentionCategory(
        "outbox_published",
        "RETENTION_OUTBOX_PUBLISHED_SECONDS",
        "Successfully published transactional outbox history.",
    ),
    RetentionCategory(
        "outbox_dead_letter",
        "RETENTION_OUTBOX_DEAD_LETTER_SECONDS",
        "Terminal dead-letter outbox history.",
    ),
    RetentionCategory(
        "webhook_deliveries",
        "RETENTION_WEBHOOK_DELIVERY_SECONDS",
        "Terminal outbound webhook delivery history.",
    ),
    RetentionCategory(
        "notification_deliveries",
        "RETENTION_NOTIFICATION_DELIVERY_SECONDS",
        "Terminal notification delivery history.",
    ),
    RetentionCategory(
        "audit_events",
        "RETENTION_AUDIT_SECONDS",
        "Durable application audit history.",
    ),
    RetentionCategory(
        "private_file_tombstones",
        "RETENTION_PRIVATE_FILE_TOMBSTONE_SECONDS",
        "Verified deleted private-file manifests not referenced by tickets.",
    ),
)

CATEGORY_BY_NAME = {category.name: category for category in CATEGORIES}


def _seconds(category: RetentionCategory) -> int | None:
    if category.setting_name is None:
        return None
    value = getattr(settings, category.setting_name, 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ImproperlyConfigured(
            f"{category.setting_name} must be a non-negative integer."
        )
    return value


def _lease_available(now):
    return models.Q(locked_until__isnull=True) | models.Q(locked_until__lte=now)


def _query(category: RetentionCategory, *, now):
    seconds = _seconds(category)
    if seconds == 0:
        return None
    cutoff = now - timedelta(seconds=seconds) if seconds is not None else now

    if category.name == "idempotency":
        return IdempotencyRecord.objects.filter(expires_at__lte=now).order_by(
            "expires_at", "pk"
        )
    if category.name == "security_throttles":
        return SecurityThrottleBucket.objects.filter(expires_at__lte=now).order_by(
            "expires_at", "pk"
        )
    if category.name == "outbox_published":
        return (
            OutboxEvent.objects.filter(
                published_at__lte=cutoff,
                dead_lettered_at__isnull=True,
            )
            .filter(_lease_available(now))
            .order_by("published_at", "pk")
        )
    if category.name == "outbox_dead_letter":
        return (
            OutboxEvent.objects.filter(dead_lettered_at__lte=cutoff)
            .filter(_lease_available(now))
            .order_by("dead_lettered_at", "pk")
        )
    if category.name == "webhook_deliveries":
        terminal = (
            models.Q(delivered_at__lte=cutoff)
            | models.Q(failed_at__lte=cutoff)
            | models.Q(cancelled_at__lte=cutoff)
        )
        return (
            WebhookDelivery.objects.filter(terminal)
            .filter(_lease_available(now))
            .order_by("created_at", "pk")
        )
    if category.name == "notification_deliveries":
        terminal = (
            models.Q(delivered_at__lte=cutoff)
            | models.Q(failed_at__lte=cutoff)
            | models.Q(cancelled_at__lte=cutoff)
        )
        return (
            NotificationDelivery.objects.filter(terminal)
            .filter(_lease_available(now))
            .order_by("created_at", "pk")
        )
    if category.name == "audit_events":
        return AuditEvent.objects.filter(occurred_at__lte=cutoff).order_by(
            "occurred_at", "pk"
        )
    if category.name == "private_file_tombstones":
        return PrivateFile.objects.filter(
            state=PrivateFile.State.DELETED,
            deleted_at__lte=cutoff,
            ticket_attachment__isnull=True,
        ).order_by("deleted_at", "pk")
    raise AssertionError(f"Unhandled retention category: {category.name}")


def retention_plan(*, category: str = "all", now=None) -> list[RetentionResult]:
    if category != "all" and category not in CATEGORY_BY_NAME:
        raise ValueError(f"Unknown retention category: {category}")
    current = now or timezone.now()
    selected = CATEGORIES if category == "all" else (CATEGORY_BY_NAME[category],)
    results: list[RetentionResult] = []
    for item in selected:
        query = _query(item, now=current)
        results.append(
            RetentionResult(
                category=item.name,
                enabled=query is not None,
                candidates=0 if query is None else query.count(),
                deleted=0,
            )
        )
    return results


def purge_retained_data(
    *,
    category: str = "all",
    batch_size: int = 1000,
    confirm: bool = False,
    now=None,
) -> list[RetentionResult]:
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or not 1 <= batch_size <= 10000:
        raise ValueError("batch_size must be between 1 and 10000.")
    if category != "all" and category not in CATEGORY_BY_NAME:
        raise ValueError(f"Unknown retention category: {category}")

    current = now or timezone.now()
    selected = CATEGORIES if category == "all" else (CATEGORY_BY_NAME[category],)
    results: list[RetentionResult] = []

    for item in selected:
        query = _query(item, now=current)
        if query is None:
            results.append(RetentionResult(item.name, False, 0, 0))
            continue

        candidates = query.count()
        deleted = 0
        if confirm and candidates:
            ids = list(query.values_list("pk", flat=True)[:batch_size])
            with transaction.atomic():
                deleted, _ = query.model.objects.filter(pk__in=ids).delete()
        results.append(
            RetentionResult(
                item.name,
                True,
                candidates,
                deleted,
            )
        )
    return results
