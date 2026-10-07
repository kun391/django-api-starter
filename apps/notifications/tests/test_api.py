import uuid

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.notifications.models import NotificationDelivery, NotificationPreference
from apps.notifications.topics import SUPPORTED_NOTIFICATION_TOPICS

pytestmark = pytest.mark.django_db(transaction=True)


def test_preferences_default_enabled_and_no_store(authenticated_client):
    response = authenticated_client.get(reverse("notification-preference-list"))
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    body = response.json()
    assert len(body) == len(SUPPORTED_NOTIFICATION_TOPICS)
    assert {row["topic"] for row in body} == set(SUPPORTED_NOTIFICATION_TOPICS)
    assert all(row["email_enabled"] is True for row in body)
    assert all(row["is_override"] is False for row in body)


def test_user_can_override_one_topic(authenticated_client, user):
    topic = "tickets.ticket-updated"
    response = authenticated_client.put(
        reverse("notification-preference-detail", args=[topic]),
        {"email_enabled": False},
        format="json",
    )
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    assert response.json() == {
        "topic": topic,
        "email_enabled": False,
        "is_override": True,
    }
    assert NotificationPreference.objects.get(
        user=user,
        topic=topic,
    ).email_enabled is False

    listed = authenticated_client.get(reverse("notification-preference-list"))
    row = next(item for item in listed.json() if item["topic"] == topic)
    assert row == {
        "topic": topic,
        "email_enabled": False,
        "is_override": True,
    }


def test_unknown_preference_topic_is_hidden(authenticated_client):
    response = authenticated_client.put(
        reverse("notification-preference-detail", args=["not.real"]),
        {"email_enabled": False},
        format="json",
    )
    assert response.status_code == 404


def test_delivery_history_is_user_scoped(authenticated_client, user):
    other = get_user_model().objects.create_user(
        username="notification-other",
        email="notification-other@example.com",
        password="pass",
    )
    visible = NotificationDelivery.objects.create(
        source_event_id=uuid.uuid4(),
        event_topic="tickets.ticket-created",
        recipient_user=user,
        recipient_user_id_snapshot=user.pk,
        recipient_email=user.email,
        template_slug="ticket-created",
        template_context={},
        dedupe_key="visible",
    )
    NotificationDelivery.objects.create(
        source_event_id=uuid.uuid4(),
        event_topic="tickets.ticket-created",
        recipient_user=other,
        recipient_user_id_snapshot=other.pk,
        recipient_email=other.email,
        template_slug="ticket-created",
        template_context={},
        dedupe_key="foreign",
    )

    response = authenticated_client.get(reverse("notification-delivery-list"))
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    assert response.json()["count"] == 1
    assert response.json()["results"][0]["id"] == str(visible.pk)


def test_delivery_history_supports_strict_filtering(authenticated_client, user):
    source_event_id = uuid.uuid4()
    NotificationDelivery.objects.create(
        source_event_id=source_event_id,
        event_topic="tickets.ticket-created",
        recipient_user=user,
        recipient_user_id_snapshot=user.pk,
        recipient_email=user.email,
        template_slug="ticket-created",
        template_context={},
        dedupe_key="filtered",
    )

    filtered = authenticated_client.get(
        reverse("notification-delivery-list"),
        {"source_event_id": str(source_event_id)},
    )
    assert filtered.status_code == 200
    assert filtered.json()["count"] == 1

    invalid = authenticated_client.get(
        reverse("notification-delivery-list"),
        {"unknown": "1"},
    )
    assert invalid.status_code == 400
    assert invalid.json()["code"] == "validation_error"
