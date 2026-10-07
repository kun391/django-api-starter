from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, cast

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from django.http import Http404
from django.utils import timezone

from apps.core.audit import record_audit_event
from apps.organizations.access import require_roles, resolve_access

from .models import WebhookDelivery, WebhookSubscription
from .security import validate_webhook_url
from .signing import derive_signing_secret, signing_headers
from .topics import SUPPORTED_WEBHOOK_TOPICS
from .transport import WebhookTransportError, send_webhook


class WebhookReplayConflict(Exception):
    pass


@dataclass(frozen=True)
class ClaimedDelivery:
    delivery_id: uuid.UUID
    lock_token: uuid.UUID


def normalize_events(events) -> list[str]:
    values = sorted(set(events))
    if not values:
        raise ValidationError("Select at least one webhook event.")
    unknown = set(values) - set(SUPPORTED_WEBHOOK_TOPICS)
    if unknown:
        raise ValidationError(
            f"Unsupported webhook events: {', '.join(sorted(unknown))}"
        )
    return values


def _admin_access(*, actor, organization_id):
    access = resolve_access(actor=actor, organization_id=organization_id)
    require_roles(access, "owner", "admin")
    return access


def create_subscription(*, actor, organization_id, url: str, events):
    access = _admin_access(actor=actor, organization_id=organization_id)
    validate_webhook_url(url)
    selected = normalize_events(events)
    with transaction.atomic():
        subscription = WebhookSubscription.objects.create(
            organization=access.organization,
            url=url,
            events=selected,
            created_by_id=actor.pk,
        )
        record_audit_event(
            action="webhooks.subscription-created",
            subject_type="webhook_subscription",
            subject_id=subscription.pk,
            actor_id=actor.pk,
            organization_id=access.organization.pk,
            metadata={"events": selected},
        )
    return (
        cast(WebhookSubscription, subscription),
        derive_signing_secret(subscription.pk, subscription.secret_version),
    )


def update_subscription(*, actor, organization_id, subscription_id, changes):
    access = _admin_access(actor=actor, organization_id=organization_id)
    with transaction.atomic():
        subscription = (
            WebhookSubscription.objects.select_for_update()
            .filter(pk=subscription_id, organization=access.organization)
            .first()
        )
        if subscription is None:
            raise Http404()

        changed_fields: list[str] = []
        if "url" in changes:
            validate_webhook_url(changes["url"])
            subscription.url = changes["url"]
            changed_fields.append("url")
        if "events" in changes:
            subscription.events = normalize_events(changes["events"])
            changed_fields.append("events")
        if "is_active" in changes:
            subscription.is_active = bool(changes["is_active"])
            changed_fields.append("is_active")

        if changed_fields:
            subscription.save(update_fields=[*changed_fields, "updated_at"])
            if "is_active" in changed_fields and not subscription.is_active:
                WebhookDelivery.objects.filter(
                    subscription=subscription,
                    delivered_at__isnull=True,
                    failed_at__isnull=True,
                    cancelled_at__isnull=True,
                ).update(
                    cancelled_at=timezone.now(),
                    locked_until=None,
                    lock_token=None,
                )
            record_audit_event(
                action="webhooks.subscription-updated",
                subject_type="webhook_subscription",
                subject_id=subscription.pk,
                actor_id=actor.pk,
                organization_id=access.organization.pk,
                metadata={"changed_fields": sorted(changed_fields)},
            )
    return cast(WebhookSubscription, subscription)


def deactivate_subscription(*, actor, organization_id, subscription_id) -> None:
    update_subscription(
        actor=actor,
        organization_id=organization_id,
        subscription_id=subscription_id,
        changes={"is_active": False},
    )


def rotate_secret(*, actor, organization_id, subscription_id):
    access = _admin_access(actor=actor, organization_id=organization_id)
    with transaction.atomic():
        subscription = (
            WebhookSubscription.objects.select_for_update()
            .filter(pk=subscription_id, organization=access.organization)
            .first()
        )
        if subscription is None:
            raise Http404()
        subscription.secret_version += 1
        subscription.save(update_fields=["secret_version", "updated_at"])
        record_audit_event(
            action="webhooks.secret-rotated",
            subject_type="webhook_subscription",
            subject_id=subscription.pk,
            actor_id=actor.pk,
            organization_id=access.organization.pk,
            metadata={"secret_version": subscription.secret_version},
        )
    return (
        cast(WebhookSubscription, subscription),
        derive_signing_secret(subscription.pk, subscription.secret_version),
    )


def _delivery_body(delivery_id, envelope: dict[str, Any], *, replay_of=None):
    metadata = {}
    request_id = envelope.get("metadata", {}).get("request_id")
    if request_id:
        metadata["request_id"] = request_id
    body = {
        "id": str(delivery_id),
        "event_id": str(envelope["id"]),
        "type": str(envelope["topic"]),
        "version": int(envelope["version"]),
        "occurred_at": envelope["occurred_at"],
        "data": envelope["payload"],
        "metadata": metadata,
    }
    if replay_of is not None:
        body["replay_of"] = str(replay_of)
    return body


def _fanout_key(subscription_id, source_event_id) -> str:
    value = f"{subscription_id}:{source_event_id}".encode()
    return "event:" + hashlib.sha256(value).hexdigest()


def fanout_event(envelope: dict[str, Any]) -> int:
    topic = str(envelope["topic"])
    if topic not in SUPPORTED_WEBHOOK_TOPICS:
        return 0

    organization_id = envelope.get("payload", {}).get("organization_id")
    if not organization_id:
        return 0

    subscriptions = list(
        WebhookSubscription.objects.filter(
            organization_id=organization_id,
            is_active=True,
        ).order_by("id")
    )
    created = 0
    with transaction.atomic():
        for subscription in subscriptions:
            if topic not in subscription.events:
                continue
            delivery_id = uuid.uuid4()
            _, was_created = WebhookDelivery.objects.get_or_create(
                dedupe_key=_fanout_key(subscription.pk, envelope["id"]),
                defaults={
                    "id": delivery_id,
                    "subscription": subscription,
                    "source_event_id": envelope["id"],
                    "event_topic": topic,
                    "event_version": int(envelope["version"]),
                    "body": _delivery_body(delivery_id, envelope),
                },
            )
            created += int(was_created)
    return created


def claim_delivery_batch(*, batch_size: int | None = None) -> list[ClaimedDelivery]:
    size = batch_size if batch_size is not None else getattr(
        settings, "WEBHOOK_BATCH_SIZE", 100
    )
    lease = getattr(settings, "WEBHOOK_LEASE_SECONDS", 60)
    if (
        not isinstance(size, int)
        or isinstance(size, bool)
        or not 1 <= size <= 1000
        or not isinstance(lease, int)
        or isinstance(lease, bool)
        or lease <= 0
    ):
        raise ValueError(
            "Webhook batch size must be 1-1000 and lease must be positive."
        )

    now = timezone.now()
    claimed: list[ClaimedDelivery] = []
    with transaction.atomic():
        deliveries = list(
            WebhookDelivery.objects.select_for_update(skip_locked=True)
            .filter(
                subscription__is_active=True,
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


def _finish_success(claim: ClaimedDelivery, status_code: int) -> None:
    WebhookDelivery.objects.filter(
        pk=claim.delivery_id,
        lock_token=claim.lock_token,
    ).update(
        attempts=models.F("attempts") + 1,
        last_attempt_at=timezone.now(),
        delivered_at=timezone.now(),
        response_status=status_code,
        last_error_code="",
        locked_until=None,
        lock_token=None,
    )


def _finish_failure(
    claim: ClaimedDelivery,
    *,
    error_code: str,
    response_status: int | None = None,
) -> None:
    max_attempts = getattr(settings, "WEBHOOK_MAX_ATTEMPTS", 8)
    base_seconds = getattr(settings, "WEBHOOK_RETRY_BASE_SECONDS", 30)
    max_seconds = getattr(settings, "WEBHOOK_RETRY_MAX_SECONDS", 3600)
    now = timezone.now()
    with transaction.atomic():
        delivery = (
            WebhookDelivery.objects.select_for_update()
            .filter(pk=claim.delivery_id, lock_token=claim.lock_token)
            .first()
        )
        if delivery is None:
            return
        delivery.attempts += 1
        delivery.last_attempt_at = now
        delivery.response_status = response_status
        delivery.last_error_code = error_code[:128]
        delivery.locked_until = None
        delivery.lock_token = None
        fields = [
            "attempts",
            "last_attempt_at",
            "response_status",
            "last_error_code",
            "locked_until",
            "lock_token",
        ]
        if delivery.attempts >= max_attempts:
            delivery.failed_at = now
            fields.append("failed_at")
        else:
            delay = min(
                max_seconds,
                base_seconds * (2 ** (delivery.attempts - 1)),
            )
            delivery.next_attempt_at = now + timedelta(seconds=delay)
            fields.append("next_attempt_at")
        delivery.save(update_fields=fields)


def process_delivery_batch(
    *,
    batch_size: int | None = None,
    transport: Callable[[str, bytes, dict[str, str]], int] = send_webhook,
) -> int:
    successful = 0
    for claim in claim_delivery_batch(batch_size=batch_size):
        delivery = (
            WebhookDelivery.objects.select_related("subscription")
            .filter(pk=claim.delivery_id, lock_token=claim.lock_token)
            .first()
        )
        if delivery is None:
            continue
        subscription = delivery.subscription
        if not subscription.is_active:
            WebhookDelivery.objects.filter(
                pk=delivery.pk,
                lock_token=claim.lock_token,
            ).update(
                cancelled_at=timezone.now(),
                locked_until=None,
                lock_token=None,
            )
            continue

        body = json.dumps(
            delivery.body,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
        timestamp = str(int(timezone.now().timestamp()))
        headers = signing_headers(
            subscription_id=subscription.pk,
            secret_version=subscription.secret_version,
            delivery_id=delivery.pk,
            source_event_id=delivery.source_event_id,
            event_topic=delivery.event_topic,
            timestamp=timestamp,
            body=body,
        )
        try:
            status_code = transport(subscription.url, body, headers)
        except (WebhookTransportError, ValidationError) as exc:
            _finish_failure(claim, error_code=type(exc).__name__)
            continue

        if 200 <= status_code < 300:
            _finish_success(claim, status_code)
            successful += 1
        else:
            _finish_failure(
                claim,
                error_code=f"http_{status_code}",
                response_status=status_code,
            )
    return successful


def replay_delivery(*, actor, organization_id, delivery_id):
    access = _admin_access(actor=actor, organization_id=organization_id)
    with transaction.atomic():
        original = (
            WebhookDelivery.objects.select_related("subscription")
            .filter(
                pk=delivery_id,
                subscription__organization=access.organization,
            )
            .first()
        )
        if original is None:
            raise Http404()
        if not (original.delivered_at or original.failed_at or original.cancelled_at):
            raise WebhookReplayConflict(
                "Only terminal webhook deliveries can be replayed."
            )
        if not original.subscription.is_active:
            raise PermissionDenied("The webhook subscription is inactive.")

        new_id = uuid.uuid4()
        body = dict(original.body)
        body["id"] = str(new_id)
        body["replay_of"] = str(original.pk)
        replay = WebhookDelivery.objects.create(
            id=new_id,
            subscription=original.subscription,
            source_event_id=original.source_event_id,
            event_topic=original.event_topic,
            event_version=original.event_version,
            body=body,
            dedupe_key=f"replay:{uuid.uuid4()}",
            replay_of_id=original.pk,
        )
        record_audit_event(
            action="webhooks.delivery-replayed",
            subject_type="webhook_delivery",
            subject_id=replay.pk,
            actor_id=actor.pk,
            organization_id=access.organization.pk,
            metadata={"replay_of": str(original.pk)},
        )
    return cast(WebhookDelivery, replay)
