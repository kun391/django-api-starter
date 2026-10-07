import uuid
from datetime import timedelta

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from apps.core.models import (
    AuditEvent,
    IdempotencyRecord,
    OutboxEvent,
    SecurityThrottleBucket,
)
from apps.files.models import PrivateFile
from apps.notifications.models import NotificationDelivery
from apps.organizations.models import Organization
from apps.retention.services import purge_retained_data, retention_plan
from apps.webhooks.models import WebhookDelivery, WebhookSubscription

pytestmark = pytest.mark.django_db(transaction=True)


def _past(seconds=3600):
    return timezone.now() - timedelta(seconds=seconds)


def test_retention_history_disabled_by_default():
    AuditEvent.objects.create(
        action="test.changed",
        subject_type="test",
        subject_id="1",
        occurred_at=_past(),
    )
    result = retention_plan(category="audit_events")[0]
    assert result.enabled is False
    assert result.candidates == 0
    purge_retained_data(category="audit_events", confirm=True)
    assert AuditEvent.objects.count() == 1


@override_settings(RETENTION_AUDIT_SECONDS=60)
def test_dry_run_reports_without_deleting_and_confirm_deletes():
    event = AuditEvent.objects.create(
        action="test.changed",
        subject_type="test",
        subject_id="1",
    )
    AuditEvent.objects.filter(pk=event.pk).update(occurred_at=_past())

    dry = purge_retained_data(category="audit_events", confirm=False)[0]
    assert dry.enabled is True
    assert dry.candidates == 1
    assert dry.deleted == 0
    assert AuditEvent.objects.filter(pk=event.pk).exists()

    deleted = purge_retained_data(category="audit_events", confirm=True)[0]
    assert deleted.deleted == 1
    assert not AuditEvent.objects.filter(pk=event.pk).exists()


def test_expired_contract_records_are_always_eligible():
    IdempotencyRecord.objects.create(
        key_digest="a" * 64,
        request_digest="b" * 64,
        response_body="{}",
        response_status=200,
        response_headers={},
        expires_at=_past(),
    )
    SecurityThrottleBucket.objects.create(
        key_digest="c" * 64,
        scope="test",
        bucket_start=_past(),
        hits=1,
        expires_at=_past(),
    )

    assert purge_retained_data(category="idempotency", confirm=True)[0].deleted == 1
    assert purge_retained_data(category="security_throttles", confirm=True)[0].deleted == 1


@override_settings(
    RETENTION_OUTBOX_PUBLISHED_SECONDS=60,
    RETENTION_OUTBOX_DEAD_LETTER_SECONDS=60,
)
def test_outbox_purge_only_terminal_and_not_actively_leased():
    old = _past()
    published = OutboxEvent.objects.create(topic="test", payload={})
    dead = OutboxEvent.objects.create(topic="test", payload={})
    pending = OutboxEvent.objects.create(topic="test", payload={})
    leased = OutboxEvent.objects.create(topic="test", payload={})

    OutboxEvent.objects.filter(pk=published.pk).update(published_at=old)
    OutboxEvent.objects.filter(pk=dead.pk).update(dead_lettered_at=old)
    OutboxEvent.objects.filter(pk=leased.pk).update(
        published_at=old,
        locked_until=timezone.now() + timedelta(minutes=5),
        lock_token=uuid.uuid4(),
    )

    purge_retained_data(category="outbox_published", confirm=True)
    purge_retained_data(category="outbox_dead_letter", confirm=True)

    assert not OutboxEvent.objects.filter(pk=published.pk).exists()
    assert not OutboxEvent.objects.filter(pk=dead.pk).exists()
    assert OutboxEvent.objects.filter(pk=pending.pk).exists()
    assert OutboxEvent.objects.filter(pk=leased.pk).exists()


@override_settings(
    RETENTION_WEBHOOK_DELIVERY_SECONDS=60,
    RETENTION_NOTIFICATION_DELIVERY_SECONDS=60,
)
def test_delivery_retention_keeps_pending_and_active_lease(user):
    organization = Organization.objects.create(name="Org", slug="retention-org")
    subscription = WebhookSubscription.objects.create(
        organization=organization,
        url="https://example.com/hook",
        events=["tickets.ticket-created"],
        created_by_id=user.pk,
    )
    old = _past()

    terminal_webhook = WebhookDelivery.objects.create(
        subscription=subscription,
        source_event_id=uuid.uuid4(),
        event_topic="tickets.ticket-created",
        body={},
        dedupe_key="terminal-webhook",
        delivered_at=old,
    )
    pending_webhook = WebhookDelivery.objects.create(
        subscription=subscription,
        source_event_id=uuid.uuid4(),
        event_topic="tickets.ticket-created",
        body={},
        dedupe_key="pending-webhook",
    )
    leased_webhook = WebhookDelivery.objects.create(
        subscription=subscription,
        source_event_id=uuid.uuid4(),
        event_topic="tickets.ticket-created",
        body={},
        dedupe_key="leased-webhook",
        delivered_at=old,
        locked_until=timezone.now() + timedelta(minutes=5),
        lock_token=uuid.uuid4(),
    )

    terminal_notification = NotificationDelivery.objects.create(
        source_event_id=uuid.uuid4(),
        event_topic="tickets.ticket-created",
        recipient_user=user,
        recipient_user_id_snapshot=user.pk,
        recipient_email=user.email,
        template_slug="ticket-created",
        template_context={},
        dedupe_key="terminal-notification",
        delivered_at=old,
    )
    pending_notification = NotificationDelivery.objects.create(
        source_event_id=uuid.uuid4(),
        event_topic="tickets.ticket-created",
        recipient_user=user,
        recipient_user_id_snapshot=user.pk,
        recipient_email=user.email,
        template_slug="ticket-created",
        template_context={},
        dedupe_key="pending-notification",
    )

    purge_retained_data(category="webhook_deliveries", confirm=True)
    purge_retained_data(category="notification_deliveries", confirm=True)

    assert not WebhookDelivery.objects.filter(pk=terminal_webhook.pk).exists()
    assert WebhookDelivery.objects.filter(pk=pending_webhook.pk).exists()
    assert WebhookDelivery.objects.filter(pk=leased_webhook.pk).exists()
    assert not NotificationDelivery.objects.filter(pk=terminal_notification.pk).exists()
    assert NotificationDelivery.objects.filter(pk=pending_notification.pk).exists()


@override_settings(RETENTION_PRIVATE_FILE_TOMBSTONE_SECONDS=60)
def test_private_file_tombstone_retention(user):
    record = PrivateFile.objects.create(
        owner=user,
        purpose="document",
        original_name="",
        size=0,
        media_type="",
        sha256="",
        state=PrivateFile.State.DELETED,
        deleted_at=_past(),
    )
    result = purge_retained_data(
        category="private_file_tombstones",
        confirm=True,
    )[0]
    assert result.deleted == 1
    assert not PrivateFile.objects.filter(pk=record.pk).exists()


@override_settings(RETENTION_AUDIT_SECONDS=-1)
def test_invalid_retention_setting_fails_closed():
    with pytest.raises(ImproperlyConfigured, match="non-negative integer"):
        retention_plan(category="audit_events")


@override_settings(RETENTION_AUDIT_SECONDS=60)
def test_management_command_is_dry_run_without_confirm(capsys):
    event = AuditEvent.objects.create(
        action="test.changed",
        subject_type="test",
        subject_id="1",
    )
    AuditEvent.objects.filter(pk=event.pk).update(occurred_at=_past())
    call_command("purge_retained_data", category="audit_events")
    assert AuditEvent.objects.filter(pk=event.pk).exists()
