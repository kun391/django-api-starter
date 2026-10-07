import uuid
from datetime import timedelta

import pytest
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

from apps.core.models import OutboxEvent
from apps.notifications.models import NotificationDelivery
from apps.operations.services import operations_snapshot, snapshot_dict
from apps.webhooks.models import WebhookDelivery, WebhookSubscription

pytestmark = pytest.mark.django_db(transaction=True)


def webhook_subscription(user):
    Organization = apps.get_model("organizations", "Organization")
    organization = Organization.objects.create(
        name="Operations Org",
        slug=f"operations-{uuid.uuid4().hex[:8]}",
    )
    return WebhookSubscription.objects.create(
        organization=organization,
        url="https://93.184.216.34/hook",
        events=["tickets.ticket-created"],
        created_by_id=user.pk,
    )


def test_empty_queues_are_ok():
    snapshot = operations_snapshot()

    assert snapshot.status == "ok"
    assert set(snapshot.queues) == {"outbox", "webhooks", "notifications"}
    for state in snapshot.queues.values():
        assert state.pending == 0
        assert state.retrying == 0
        assert state.terminal_failures == 0
        assert state.oldest_pending_age_seconds is None
        assert state.status == "ok"


def test_future_scheduled_work_is_not_due_backlog(user):
    future = timezone.now() + timedelta(hours=1)
    OutboxEvent.objects.create(
        topic="tickets.ticket-created",
        payload={},
        available_at=future,
    )
    NotificationDelivery.objects.create(
        source_event_id=uuid.uuid4(),
        event_topic="tickets.ticket-created",
        recipient_user=user,
        recipient_user_id_snapshot=user.pk,
        recipient_email=user.email,
        template_slug="ticket-created",
        template_context={},
        dedupe_key="future-notification",
        next_attempt_at=future,
    )

    snapshot = operations_snapshot()

    assert snapshot.queues["outbox"].pending == 0
    assert snapshot.queues["notifications"].pending == 0


def test_old_due_backlog_becomes_warning_then_critical(user, settings):
    settings.OPERATIONS_QUEUE_WARNING_AGE_SECONDS = 60
    settings.OPERATIONS_QUEUE_CRITICAL_AGE_SECONDS = 120
    event = OutboxEvent.objects.create(
        topic="tickets.ticket-created",
        payload={},
    )
    OutboxEvent.objects.filter(pk=event.pk).update(
        occurred_at=timezone.now() - timedelta(seconds=90)
    )

    warning = operations_snapshot()
    assert warning.queues["outbox"].status == "warning"
    assert warning.status == "warning"

    OutboxEvent.objects.filter(pk=event.pk).update(
        occurred_at=timezone.now() - timedelta(seconds=180)
    )
    critical = operations_snapshot()
    assert critical.queues["outbox"].status == "critical"
    assert critical.status == "critical"


def test_terminal_failure_and_stale_lease_are_visible(user):
    subscription = webhook_subscription(user)
    now = timezone.now()
    WebhookDelivery.objects.create(
        subscription=subscription,
        source_event_id=uuid.uuid4(),
        event_topic="tickets.ticket-created",
        body={},
        dedupe_key="failed-webhook",
        failed_at=now,
    )
    NotificationDelivery.objects.create(
        source_event_id=uuid.uuid4(),
        event_topic="tickets.ticket-created",
        recipient_user=user,
        recipient_user_id_snapshot=user.pk,
        recipient_email=user.email,
        template_slug="ticket-created",
        template_context={},
        dedupe_key="stale-notification",
        lock_token=uuid.uuid4(),
        locked_until=now - timedelta(seconds=1),
    )

    snapshot = operations_snapshot()

    assert snapshot.queues["webhooks"].terminal_failures == 1
    assert snapshot.queues["webhooks"].status == "critical"
    assert snapshot.queues["notifications"].stale_leases == 1
    assert snapshot.status == "critical"


def test_retrying_and_active_lease_counts(user):
    now = timezone.now()
    NotificationDelivery.objects.create(
        source_event_id=uuid.uuid4(),
        event_topic="tickets.ticket-created",
        recipient_user=user,
        recipient_user_id_snapshot=user.pk,
        recipient_email=user.email,
        template_slug="ticket-created",
        template_context={},
        dedupe_key="retrying-notification",
        attempts=2,
        locked_until=now + timedelta(seconds=30),
        lock_token=uuid.uuid4(),
    )

    state = operations_snapshot().queues["notifications"]

    assert state.pending == 1
    assert state.retrying == 1
    assert state.active_leases == 1


def test_invalid_threshold_configuration_fails_closed(settings):
    settings.OPERATIONS_QUEUE_WARNING_AGE_SECONDS = 300
    settings.OPERATIONS_QUEUE_CRITICAL_AGE_SECONDS = 60

    with pytest.raises(ImproperlyConfigured):
        operations_snapshot()


def test_snapshot_dict_is_aggregate_only(user):
    NotificationDelivery.objects.create(
        source_event_id=uuid.uuid4(),
        event_topic="tickets.ticket-created",
        recipient_user=user,
        recipient_user_id_snapshot=user.pk,
        recipient_email=user.email,
        template_slug="ticket-created",
        template_context={"private": "do-not-expose"},
        dedupe_key="aggregate-only",
    )

    rendered = str(snapshot_dict())

    assert user.email not in rendered
    assert "do-not-expose" not in rendered
