import uuid
from datetime import timedelta
from unittest.mock import Mock, patch

import pytest
from django.db import transaction
from django.utils import timezone

from apps.core import outbox
from apps.core.models import OutboxEvent
from apps.notifications.models import NotificationDelivery
from apps.notifications.services import (
    claim_delivery_batch as claim_notification_batch,
)
from apps.notifications.services import (
    process_delivery_batch as process_notification_batch,
)
from apps.organizations.models import Organization, OrganizationMembership
from apps.webhooks.models import WebhookDelivery, WebhookSubscription
from apps.webhooks.services import claim_delivery_batch as claim_webhook_batch
from apps.webhooks.services import process_delivery_batch as process_webhook_batch

pytestmark = pytest.mark.django_db(transaction=True)


class RecordingProvider:
    def __init__(self):
        self.sent = 0

    def send_email(self, **_kwargs):
        self.sent += 1


def _subscription(user):
    organization = Organization.objects.create(
        name="Resilience Org",
        slug=f"resilience-{uuid.uuid4().hex[:8]}",
    )
    return WebhookSubscription.objects.create(
        organization=organization,
        url="https://93.184.216.34/hook",
        events=["tickets.ticket-created"],
        created_by_id=user.pk,
    )


def test_outbox_worker_crash_is_recovered_after_lease_expiry():
    received = []

    @outbox.outbox_handler("resilience.test-created")
    def handle(envelope):
        received.append(envelope["id"])

    try:
        with transaction.atomic():
            event = outbox.record_outbox_event(
                topic="resilience.test-created",
                payload={"value": 1},
            )

        abandoned = outbox.claim_outbox_batch(batch_size=1, lease_seconds=60)
        assert [claim.event_id for claim in abandoned] == [event.pk]
        assert outbox.process_outbox_batch() == 0

        OutboxEvent.objects.filter(pk=event.pk).update(
            locked_until=timezone.now() - timedelta(seconds=1)
        )
        assert outbox.process_outbox_batch() == 1
        event.refresh_from_db()
        assert event.published_at is not None
        assert event.attempts == 1
        assert received == [str(event.pk)]
    finally:
        outbox._HANDLERS.pop("resilience.test-created", None)


def test_webhook_worker_crash_is_recovered_without_duplicate_send(user):
    subscription = _subscription(user)
    delivery = WebhookDelivery.objects.create(
        subscription=subscription,
        source_event_id=uuid.uuid4(),
        event_topic="tickets.ticket-created",
        body={"data": {}},
        dedupe_key=f"resilience:{uuid.uuid4()}",
    )

    abandoned = claim_webhook_batch(batch_size=1)
    assert [claim.delivery_id for claim in abandoned] == [delivery.pk]
    transport = Mock(return_value=204)
    assert process_webhook_batch(transport=transport) == 0
    transport.assert_not_called()

    WebhookDelivery.objects.filter(pk=delivery.pk).update(
        locked_until=timezone.now() - timedelta(seconds=1)
    )
    assert process_webhook_batch(transport=transport) == 1
    assert transport.call_count == 1
    delivery.refresh_from_db()
    assert delivery.delivered_at is not None
    assert delivery.attempts == 1


def test_notification_worker_crash_is_recovered_without_duplicate_send(user):
    delivery = NotificationDelivery.objects.create(
        source_event_id=uuid.uuid4(),
        event_topic="tickets.ticket-created",
        recipient_user=user,
        recipient_user_id_snapshot=user.pk,
        recipient_email=user.email,
        template_slug="ticket-created",
        template_context={
            "ticket_id": str(uuid.uuid4()),
            "priority": "normal",
            "event_id": str(uuid.uuid4()),
            "event_topic": "tickets.ticket-created",
            "occurred_at": timezone.now().isoformat(),
        },
        dedupe_key=f"resilience:{uuid.uuid4()}",
    )

    abandoned = claim_notification_batch(batch_size=1)
    assert [claim.delivery_id for claim in abandoned] == [delivery.pk]
    provider = RecordingProvider()
    assert process_notification_batch(provider=provider) == 0
    assert provider.sent == 0

    NotificationDelivery.objects.filter(pk=delivery.pk).update(
        locked_until=timezone.now() - timedelta(seconds=1)
    )
    assert process_notification_batch(provider=provider) == 1
    assert provider.sent == 1
    delivery.refresh_from_db()
    assert delivery.delivered_at is not None
    assert delivery.attempts == 1


def test_unexpected_webhook_process_failure_leaves_recoverable_lease(user):
    subscription = _subscription(user)
    delivery = WebhookDelivery.objects.create(
        subscription=subscription,
        source_event_id=uuid.uuid4(),
        event_topic="tickets.ticket-created",
        body={"data": {}},
        dedupe_key=f"resilience:{uuid.uuid4()}",
    )

    def crash(*_args):
        raise RuntimeError("process-crash")

    with pytest.raises(RuntimeError, match="process-crash"):
        process_webhook_batch(transport=crash)

    delivery.refresh_from_db()
    assert delivery.delivered_at is None
    assert delivery.failed_at is None
    assert delivery.attempts == 0
    assert delivery.lock_token is not None
    assert delivery.locked_until is not None

    WebhookDelivery.objects.filter(pk=delivery.pk).update(
        locked_until=timezone.now() - timedelta(seconds=1)
    )
    transport = Mock(return_value=200)
    assert process_webhook_batch(transport=transport) == 1
    assert transport.call_count == 1


def test_terminal_downstream_failure_does_not_make_api_readiness_fail(
    api_client,
    user,
    settings,
):
    settings.WEBHOOK_MAX_ATTEMPTS = 1
    subscription = _subscription(user)
    WebhookDelivery.objects.create(
        subscription=subscription,
        source_event_id=uuid.uuid4(),
        event_topic="tickets.ticket-created",
        body={"data": {}},
        dedupe_key=f"resilience:{uuid.uuid4()}",
    )

    assert process_webhook_batch(transport=lambda *_args: 503) == 0
    assert WebhookDelivery.objects.get().failed_at is not None

    response = api_client.get("/health/ready/")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "ok"}



def test_partial_fanout_failure_retries_without_duplicate_side_effects(user):
    organization = Organization.objects.create(
        name="Fanout Org",
        slug=f"fanout-{uuid.uuid4().hex[:8]}",
    )
    OrganizationMembership.objects.create(
        organization=organization,
        user=user,
        role="owner",
    )
    WebhookSubscription.objects.create(
        organization=organization,
        url="https://93.184.216.34/hook",
        events=["tickets.ticket-created"],
        created_by_id=user.pk,
    )
    with transaction.atomic():
        event = outbox.record_outbox_event(
            topic="tickets.ticket-created",
            payload={
                "organization_id": str(organization.pk),
                "ticket_id": str(uuid.uuid4()),
                "owner_id": user.pk,
                "priority": "normal",
                "revision": 1,
            },
        )

    with patch.object(
        NotificationDelivery.objects,
        "get_or_create",
        side_effect=RuntimeError("notification-db-failure"),
    ):
        assert outbox.process_outbox_batch() == 0

    event.refresh_from_db()
    assert event.published_at is None
    assert event.attempts == 1
    assert WebhookDelivery.objects.filter(source_event_id=event.pk).count() == 1
    assert NotificationDelivery.objects.count() == 0

    OutboxEvent.objects.filter(pk=event.pk).update(
        available_at=timezone.now() - timedelta(seconds=1)
    )
    assert outbox.process_outbox_batch() == 1

    event.refresh_from_db()
    assert event.published_at is not None
    assert WebhookDelivery.objects.filter(source_event_id=event.pk).count() == 1
    assert NotificationDelivery.objects.filter(source_event_id=event.pk).count() == 1
