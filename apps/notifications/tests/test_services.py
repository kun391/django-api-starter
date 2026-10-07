import uuid
from datetime import timedelta

import pytest
from django.db import transaction
from django.utils import timezone

from apps.core.models import AuditEvent
from apps.core.outbox import process_outbox_batch, record_outbox_event
from apps.notifications.models import NotificationDelivery, NotificationPreference
from apps.notifications.provider import NotificationProviderError
from apps.notifications.services import (
    fanout_event,
    process_delivery_batch,
    set_preference,
)
from apps.organizations.models import Organization

pytestmark = pytest.mark.django_db(transaction=True)


class RecordingProvider:
    def __init__(self):
        self.messages = []

    def send_email(self, *, subject, body, recipient):
        self.messages.append(
            {"subject": subject, "body": body, "recipient": recipient}
        )


class FailingProvider:
    def send_email(self, **_kwargs):
        raise NotificationProviderError("temporary")


def envelope(*, topic, payload):
    return {
        "id": str(uuid.uuid4()),
        "topic": topic,
        "version": 1,
        "occurred_at": timezone.now().isoformat(),
        "payload": payload,
        "metadata": {"request_id": "notification-test"},
    }


def test_ticket_fanout_is_idempotent_and_snapshots_context(user):
    event = envelope(
        topic="tickets.ticket-created",
        payload={
            "ticket_id": str(uuid.uuid4()),
            "owner_id": user.pk,
            "organization_id": None,
            "priority": "high",
            "revision": 1,
        },
    )

    assert fanout_event(event) == 1
    assert fanout_event(event) == 0

    delivery = NotificationDelivery.objects.get()
    assert delivery.recipient_user == user
    assert delivery.recipient_email == user.email
    assert delivery.template_slug == "ticket-created"
    assert delivery.template_context["priority"] == "high"
    assert delivery.template_context["event_id"] == event["id"]


@pytest.mark.parametrize(
    ("topic", "payload"),
    [
        (
            "organizations.organization-created",
            {"organization_id": str(uuid.uuid4()), "owner_user_id": "RECIPIENT"},
        ),
        (
            "organizations.membership-added",
            {
                "organization_id": str(uuid.uuid4()),
                "user_id": "RECIPIENT",
                "role": "member",
            },
        ),
        (
            "organizations.membership-updated",
            {
                "organization_id": str(uuid.uuid4()),
                "user_id": "RECIPIENT",
                "previous_role": "member",
                "role": "admin",
            },
        ),
        (
            "organizations.membership-removed",
            {"organization_id": str(uuid.uuid4()), "user_id": "RECIPIENT"},
        ),
    ],
)
def test_organization_topics_map_recipient(user, topic, payload):
    payload = dict(payload)
    for key, value in list(payload.items()):
        if value == "RECIPIENT":
            payload[key] = user.pk

    assert fanout_event(envelope(topic=topic, payload=payload)) == 1
    assert NotificationDelivery.objects.get().recipient_user == user


def test_opt_out_prevents_fanout(user):
    set_preference(
        actor=user,
        topic="tickets.ticket-updated",
        email_enabled=False,
    )

    event = envelope(
        topic="tickets.ticket-updated",
        payload={
            "ticket_id": str(uuid.uuid4()),
            "owner_id": user.pk,
            "organization_id": None,
            "changed_fields": ["status"],
            "status": "resolved",
            "revision": 2,
        },
    )
    assert fanout_event(event) == 0
    assert not NotificationDelivery.objects.exists()


def test_disabling_preference_cancels_pending_delivery(user):
    event = envelope(
        topic="tickets.ticket-created",
        payload={
            "ticket_id": str(uuid.uuid4()),
            "owner_id": user.pk,
            "organization_id": None,
            "priority": "normal",
            "revision": 1,
        },
    )
    fanout_event(event)
    delivery = NotificationDelivery.objects.get()

    preference = set_preference(
        actor=user,
        topic="tickets.ticket-created",
        email_enabled=False,
    )

    delivery.refresh_from_db()
    assert preference.email_enabled is False
    assert delivery.cancelled_at is not None
    assert AuditEvent.objects.filter(
        action="notifications.preference-updated",
        actor_id=user.pk,
    ).exists()


def test_delivery_renders_template_and_marks_success(user):
    event = envelope(
        topic="tickets.ticket-updated",
        payload={
            "ticket_id": str(uuid.uuid4()),
            "owner_id": user.pk,
            "organization_id": None,
            "changed_fields": ["status"],
            "status": "resolved",
            "revision": 2,
        },
    )
    fanout_event(event)
    provider = RecordingProvider()

    assert process_delivery_batch(provider=provider) == 1
    delivery = NotificationDelivery.objects.get()
    assert delivery.attempts == 1
    assert delivery.delivered_at is not None
    assert delivery.last_error_code == ""
    assert provider.messages == [
        {
            "subject": "Ticket updated",
            "body": (
                f"Ticket {event['payload']['ticket_id']} was updated.\n"
                "Status: resolved\n"
                "Revision: 2\n\n"
                f"Event: {event['id']}\n"
            ),
            "recipient": user.email,
        }
    ]


def test_provider_failure_retries_then_becomes_terminal(user, settings):
    settings.NOTIFICATION_MAX_ATTEMPTS = 2
    settings.NOTIFICATION_RETRY_BASE_SECONDS = 1
    settings.NOTIFICATION_RETRY_MAX_SECONDS = 1
    event = envelope(
        topic="tickets.ticket-created",
        payload={
            "ticket_id": str(uuid.uuid4()),
            "owner_id": user.pk,
            "organization_id": None,
            "priority": "normal",
            "revision": 1,
        },
    )
    fanout_event(event)

    assert process_delivery_batch(provider=FailingProvider()) == 0
    delivery = NotificationDelivery.objects.get()
    assert delivery.attempts == 1
    assert delivery.failed_at is None
    assert delivery.last_error_code == "NotificationProviderError"

    NotificationDelivery.objects.filter(pk=delivery.pk).update(
        next_attempt_at=timezone.now() - timedelta(seconds=1)
    )
    assert process_delivery_batch(provider=FailingProvider()) == 0
    delivery.refresh_from_db()
    assert delivery.attempts == 2
    assert delivery.failed_at is not None


def test_preference_is_rechecked_before_send(user):
    event = envelope(
        topic="tickets.ticket-created",
        payload={
            "ticket_id": str(uuid.uuid4()),
            "owner_id": user.pk,
            "organization_id": None,
            "priority": "normal",
            "revision": 1,
        },
    )
    fanout_event(event)
    NotificationPreference.objects.create(
        user=user,
        topic="tickets.ticket-created",
        email_enabled=False,
    )
    provider = RecordingProvider()

    assert process_delivery_batch(provider=provider) == 0
    delivery = NotificationDelivery.objects.get()
    assert delivery.cancelled_at is not None
    assert provider.messages == []


def test_outbox_fans_out_without_sending_email(user):
    with transaction.atomic():
        event = record_outbox_event(
            topic="tickets.ticket-created",
            payload={
                "ticket_id": str(uuid.uuid4()),
                "owner_id": user.pk,
                "organization_id": None,
                "priority": "normal",
                "revision": 1,
            },
        )

    assert process_outbox_batch() == 1
    event.refresh_from_db()
    assert event.published_at is not None

    delivery = NotificationDelivery.objects.get(source_event_id=event.pk)
    assert delivery.attempts == 0
    assert delivery.delivered_at is None


def test_unknown_or_recipientless_event_is_ignored(user):
    organization = Organization.objects.create(
        name="No Recipient",
        slug=f"no-recipient-{uuid.uuid4().hex[:8]}",
    )
    assert (
        fanout_event(
            envelope(
                topic="organizations.organization-updated",
                payload={
                    "organization_id": str(organization.pk),
                    "changed_fields": ["name"],
                },
            )
        )
        == 0
    )
    assert (
        fanout_event(
            envelope(
                topic="tickets.ticket-created",
                payload={
                    "ticket_id": str(uuid.uuid4()),
                    "owner_id": None,
                    "priority": "normal",
                    "revision": 1,
                },
            )
        )
        == 0
    )
