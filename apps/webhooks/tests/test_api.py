import uuid

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from apps.core.models import AuditEvent
from apps.organizations.models import Organization, OrganizationMembership
from apps.webhooks.models import WebhookDelivery, WebhookSubscription

pytestmark = pytest.mark.django_db(transaction=True)


def client_for(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


def organization_for(user, *, role="owner", slug=None):
    organization = Organization.objects.create(
        name="Webhook API Org",
        slug=slug or f"webhook-api-{uuid.uuid4().hex[:8]}",
    )
    OrganizationMembership.objects.create(
        organization=organization,
        user=user,
        role=role,
    )
    return organization


def list_url(organization):
    return reverse("organization-webhook-list", args=[organization.pk])


def test_owner_can_create_list_rotate_and_deactivate(authenticated_client, user):
    organization = organization_for(user)
    response = authenticated_client.post(
        list_url(organization),
        {
            "url": "https://93.184.216.34/events",
            "events": ["tickets.ticket-created", "tickets.ticket-updated"],
        },
        format="json",
    )
    assert response.status_code == 201
    body = response.json()
    assert body["secret_version"] == 1
    assert set(body) == {
        "id",
        "url",
        "events",
        "is_active",
        "secret_version",
        "created_at",
        "updated_at",
    }
    subscription = WebhookSubscription.objects.get(pk=body["id"])
    assert subscription.created_by_id == user.pk
    assert AuditEvent.objects.filter(
        action="webhooks.subscription-created",
        subject_id=str(subscription.pk),
    ).exists()

    listed = authenticated_client.get(list_url(organization))
    assert listed.status_code == 200
    assert listed["Cache-Control"] == "no-store"
    assert listed.json()["count"] == 1

    rotate = authenticated_client.post(
        reverse(
            "organization-webhook-rotate-secret",
            args=[organization.pk, subscription.pk],
        )
    )
    assert rotate.status_code == 200
    assert rotate.json() == {"secret_version": 2}

    deleted = authenticated_client.delete(
        reverse(
            "organization-webhook-detail",
            args=[organization.pk, subscription.pk],
        )
    )
    assert deleted.status_code == 204
    subscription.refresh_from_db()
    assert subscription.is_active is False


def test_member_is_forbidden_and_non_member_is_hidden(user):
    owner = get_user_model().objects.create_user(
        username="webhook-owner",
        email="webhook-owner@example.com",
        password="pass",
    )
    organization = organization_for(owner)
    member = get_user_model().objects.create_user(
        username="webhook-member",
        email="webhook-member@example.com",
        password="pass",
    )
    OrganizationMembership.objects.create(
        organization=organization,
        user=member,
        role="member",
    )

    assert client_for(member).get(list_url(organization)).status_code == 403
    assert client_for(user).get(list_url(organization)).status_code == 404


def test_admin_can_manage_webhooks(user):
    organization = organization_for(user, role="admin")
    client = client_for(user)
    response = client.post(
        list_url(organization),
        {
            "url": "https://93.184.216.34/events",
            "events": ["tickets.ticket-created"],
        },
        format="json",
    )
    assert response.status_code == 201


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/events",
        "https://127.0.0.1/events",
        "https://169.254.169.254/latest/meta-data/",
    ],
)
def test_subscription_api_rejects_unsafe_endpoint(authenticated_client, user, url):
    organization = organization_for(user)
    response = authenticated_client.post(
        list_url(organization),
        {"url": url, "events": ["tickets.ticket-created"]},
        format="json",
    )
    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"
    assert not WebhookSubscription.objects.exists()


def test_subscription_api_rejects_unknown_event(authenticated_client, user):
    organization = organization_for(user)
    response = authenticated_client.post(
        list_url(organization),
        {
            "url": "https://93.184.216.34/events",
            "events": ["tickets.not-real"],
        },
        format="json",
    )
    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"


def test_delivery_history_is_tenant_scoped(authenticated_client, user):
    organization = organization_for(user)
    other = organization_for(user, slug=f"other-{uuid.uuid4().hex[:8]}")
    subscription = WebhookSubscription.objects.create(
        organization=organization,
        url="https://93.184.216.34/events",
        events=["tickets.ticket-created"],
        created_by_id=user.pk,
    )
    foreign = WebhookSubscription.objects.create(
        organization=other,
        url="https://93.184.216.34/events",
        events=["tickets.ticket-created"],
        created_by_id=user.pk,
    )
    visible = WebhookDelivery.objects.create(
        subscription=subscription,
        source_event_id=uuid.uuid4(),
        event_topic="tickets.ticket-created",
        body={"id": "visible"},
        dedupe_key="visible",
    )
    WebhookDelivery.objects.create(
        subscription=foreign,
        source_event_id=uuid.uuid4(),
        event_topic="tickets.ticket-created",
        body={"id": "foreign"},
        dedupe_key="foreign",
    )

    response = authenticated_client.get(
        reverse("organization-webhook-delivery-list", args=[organization.pk])
    )
    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert response.json()["results"][0]["id"] == str(visible.pk)


def test_webhook_queries_use_strict_contract(authenticated_client, user):
    organization = organization_for(user)
    response = authenticated_client.get(f"{list_url(organization)}?unknown=1")
    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"
