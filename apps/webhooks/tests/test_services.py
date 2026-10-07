import json
import uuid
from datetime import timedelta
from typing import Any

import pytest
from django.db import transaction
from django.utils import timezone

from apps.core.models import AuditEvent
from apps.core.outbox import process_outbox_batch, record_outbox_event
from apps.organizations.models import Organization, OrganizationMembership
from apps.webhooks.models import WebhookDelivery, WebhookSubscription
from apps.webhooks.services import (
    fanout_event,
    process_delivery_batch,
    replay_delivery,
    update_subscription,
)
from apps.webhooks.signing import derive_signing_secret, signature_for

pytestmark = pytest.mark.django_db(transaction=True)


def organization_for(user, *, role="owner"):
    organization = Organization.objects.create(name="Webhook Org", slug=f"webhook-{uuid.uuid4().hex[:8]}")
    OrganizationMembership.objects.create(
        organization=organization,
        user=user,
        role=role,
    )
    return organization


def subscription_for(user, organization, *, events=None):
    return WebhookSubscription.objects.create(
        organization=organization,
        url="https://93.184.216.34/hook",
        events=events or ["tickets.ticket-created"],
        created_by_id=user.pk,
    )


def envelope(organization, *, event_id=None, topic="tickets.ticket-created"):
    return {
        "id": str(event_id or uuid.uuid4()),
        "topic": topic,
        "version": 1,
        "occurred_at": timezone.now().isoformat(),
        "payload": {
            "organization_id": str(organization.pk),
            "ticket_id": str(uuid.uuid4()),
        },
        "metadata": {"request_id": "webhook-test-request"},
    }


def test_fanout_is_idempotent_and_ignores_personal_events(user):
    organization = organization_for(user)
    subscription = subscription_for(user, organization)
    event = envelope(organization)

    assert fanout_event(event) == 1
    assert fanout_event(event) == 0
    delivery = WebhookDelivery.objects.get()
    assert delivery.subscription == subscription
    assert str(delivery.source_event_id) == event["id"]
    assert delivery.body["metadata"] == {"request_id": "webhook-test-request"}

    personal = envelope(organization)
    personal["payload"]["organization_id"] = None
    assert fanout_event(personal) == 0
    assert WebhookDelivery.objects.count() == 1


def test_delivery_success_signs_exact_body(user):
    organization = organization_for(user)
    subscription = subscription_for(user, organization)
    fanout_event(envelope(organization))
    delivery = WebhookDelivery.objects.get()

    captured: dict[str, Any] = {}

    def transport(url, body, headers):
        captured.update(url=url, body=body, headers=headers)
        return 204

    assert process_delivery_batch(transport=transport) == 1
    delivery.refresh_from_db()
    assert delivery.attempts == 1
    assert delivery.delivered_at is not None
    assert delivery.response_status == 204

    assert captured["url"] == subscription.url
    body = captured["body"]
    assert json.loads(body) == delivery.body
    headers = captured["headers"]
    assert headers["Webhook-Id"] == str(delivery.pk)
    assert headers["Webhook-Event-Id"] == str(delivery.source_event_id)
    assert headers["Webhook-Event"] == delivery.event_topic
    assert headers["Webhook-Secret-Version"] == "1"

    key = derive_signing_secret(subscription.pk, subscription.secret_version)
    expected = signature_for(
        secret=key,
        delivery_id=delivery.pk,
        timestamp=headers["Webhook-Timestamp"],
        body=body,
    )
    assert headers["Webhook-Signature"] == f"v1={expected}"


def test_non_2xx_retries_then_becomes_terminal_failure(user, settings):
    settings.WEBHOOK_MAX_ATTEMPTS = 2
    settings.WEBHOOK_RETRY_BASE_SECONDS = 1
    settings.WEBHOOK_RETRY_MAX_SECONDS = 1

    organization = organization_for(user)
    subscription_for(user, organization)
    fanout_event(envelope(organization))

    assert process_delivery_batch(transport=lambda *_args: 503) == 0
    delivery = WebhookDelivery.objects.get()
    assert delivery.attempts == 1
    assert delivery.failed_at is None
    assert delivery.response_status == 503
    assert delivery.last_error_code == "http_503"

    WebhookDelivery.objects.filter(pk=delivery.pk).update(
        next_attempt_at=timezone.now() - timedelta(seconds=1)
    )
    assert process_delivery_batch(transport=lambda *_args: 503) == 0
    delivery.refresh_from_db()
    assert delivery.attempts == 2
    assert delivery.failed_at is not None


def test_deactivation_cancels_pending_deliveries(user):
    organization = organization_for(user)
    subscription = subscription_for(user, organization)
    fanout_event(envelope(organization))
    delivery = WebhookDelivery.objects.get()

    update_subscription(
        actor=user,
        organization_id=organization.pk,
        subscription_id=subscription.pk,
        changes={"is_active": False},
    )
    delivery.refresh_from_db()
    assert delivery.cancelled_at is not None
    subscription.refresh_from_db()
    assert subscription.is_active is False


def test_terminal_delivery_can_be_replayed_with_distinct_id(user):
    organization = organization_for(user)
    subscription_for(user, organization)
    fanout_event(envelope(organization))
    original = WebhookDelivery.objects.get()
    WebhookDelivery.objects.filter(pk=original.pk).update(
        delivered_at=timezone.now(),
        attempts=1,
        response_status=200,
    )
    original.refresh_from_db()

    replay = replay_delivery(
        actor=user,
        organization_id=organization.pk,
        delivery_id=original.pk,
    )
    assert replay.pk != original.pk
    assert replay.replay_of_id == original.pk
    assert replay.source_event_id == original.source_event_id
    assert replay.body["replay_of"] == str(original.pk)
    assert AuditEvent.objects.filter(
        action="webhooks.delivery-replayed",
        subject_id=str(replay.pk),
    ).exists()


def test_outbox_fanout_finishes_before_network_delivery(user):
    organization = organization_for(user)
    subscription_for(user, organization)

    with transaction.atomic():
        event = record_outbox_event(
            topic="tickets.ticket-created",
            payload={
                "organization_id": str(organization.pk),
                "ticket_id": str(uuid.uuid4()),
                "owner_id": user.pk,
                "priority": "normal",
                "revision": 1,
            },
        )

    assert process_outbox_batch() == 1
    event.refresh_from_db()
    assert event.published_at is not None

    delivery = WebhookDelivery.objects.get(source_event_id=event.pk)
    assert delivery.delivered_at is None
    assert delivery.attempts == 0
