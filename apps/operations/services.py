from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from django.conf import settings
from django.db.models import Count, Min, Q
from django.utils import timezone

from apps.core.models import OutboxEvent
from apps.notifications.models import NotificationDelivery
from apps.webhooks.models import WebhookDelivery


@dataclass(frozen=True)
class QueueState:
    pending: int
    retrying: int
    terminal_failures: int
    active_leases: int
    stale_leases: int
    oldest_pending_age_seconds: int | None
    status: str


@dataclass(frozen=True)
class OperationsSnapshot:
    status: str
    generated_at: str
    queues: dict[str, QueueState]


def _age_seconds(value: datetime | None, now: datetime) -> int | None:
    if value is None:
        return None
    return max(0, int((now - value).total_seconds()))


def _status(*, age: int | None, terminal_failures: int) -> str:
    critical_age = int(getattr(settings, "OPERATIONS_QUEUE_CRITICAL_AGE_SECONDS", 900))
    warning_age = int(getattr(settings, "OPERATIONS_QUEUE_WARNING_AGE_SECONDS", 300))
    if terminal_failures > 0 or (age is not None and age >= critical_age):
        return "critical"
    if age is not None and age >= warning_age:
        return "warning"
    return "ok"


def _outbox_state(now: datetime) -> QueueState:
    ready = Q(
        published_at__isnull=True,
        dead_lettered_at__isnull=True,
    )
    values: dict[str, Any] = OutboxEvent.objects.aggregate(
        pending=Count("id", filter=ready),
        retrying=Count("id", filter=ready & Q(attempts__gt=0)),
        terminal_failures=Count("id", filter=Q(dead_lettered_at__isnull=False)),
        active_leases=Count("id", filter=ready & Q(locked_until__gt=now)),
        stale_leases=Count(
            "id",
            filter=ready & Q(lock_token__isnull=False, locked_until__lte=now),
        ),
        oldest=Min("occurred_at", filter=ready),
    )
    age = _age_seconds(values["oldest"], now)
    failures = int(values["terminal_failures"] or 0)
    return QueueState(
        pending=int(values["pending"] or 0),
        retrying=int(values["retrying"] or 0),
        terminal_failures=failures,
        active_leases=int(values["active_leases"] or 0),
        stale_leases=int(values["stale_leases"] or 0),
        oldest_pending_age_seconds=age,
        status=_status(age=age, terminal_failures=failures),
    )


def _delivery_state(model, now: datetime) -> QueueState:
    ready = Q(
        delivered_at__isnull=True,
        failed_at__isnull=True,
        cancelled_at__isnull=True,
    )
    values: dict[str, Any] = model.objects.aggregate(
        pending=Count("id", filter=ready),
        retrying=Count("id", filter=ready & Q(attempts__gt=0)),
        terminal_failures=Count("id", filter=Q(failed_at__isnull=False)),
        active_leases=Count("id", filter=ready & Q(locked_until__gt=now)),
        stale_leases=Count(
            "id",
            filter=ready & Q(lock_token__isnull=False, locked_until__lte=now),
        ),
        oldest=Min("created_at", filter=ready),
    )
    age = _age_seconds(values["oldest"], now)
    failures = int(values["terminal_failures"] or 0)
    return QueueState(
        pending=int(values["pending"] or 0),
        retrying=int(values["retrying"] or 0),
        terminal_failures=failures,
        active_leases=int(values["active_leases"] or 0),
        stale_leases=int(values["stale_leases"] or 0),
        oldest_pending_age_seconds=age,
        status=_status(age=age, terminal_failures=failures),
    )


def operations_snapshot() -> OperationsSnapshot:
    now = timezone.now()
    queues = {
        "outbox": _outbox_state(now),
        "webhooks": _delivery_state(WebhookDelivery, now),
        "notifications": _delivery_state(NotificationDelivery, now),
    }
    overall = "ok"
    statuses = {state.status for state in queues.values()}
    if "critical" in statuses:
        overall = "critical"
    elif "warning" in statuses:
        overall = "warning"
    return OperationsSnapshot(
        status=overall,
        generated_at=now.isoformat(),
        queues=queues,
    )


def snapshot_dict() -> dict[str, object]:
    snapshot = operations_snapshot()
    return {
        "status": snapshot.status,
        "generated_at": snapshot.generated_at,
        "queues": {
            name: asdict(state)
            for name, state in snapshot.queues.items()
        },
    }
