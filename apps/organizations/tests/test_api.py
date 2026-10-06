import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from apps.core.models import IdempotencyRecord, OutboxEvent
from apps.organizations.models import Organization, OrganizationMembership

pytestmark = pytest.mark.django_db(transaction=True)


def create_org(client, *, key="org-create-1", name="Acme", slug="acme"):
    return client.post(
        reverse("organization-list"),
        {"name": name, "slug": slug},
        format="json",
        HTTP_IDEMPOTENCY_KEY=key,
    )


def client_for(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


def test_create_is_idempotent_and_creator_is_owner(authenticated_client, user):
    first = create_org(authenticated_client)
    second = create_org(authenticated_client)

    assert first.status_code == second.status_code == 201
    assert first.content == second.content
    assert second["Idempotency-Replayed"] == "true"
    assert Organization.objects.count() == 1
    membership = OrganizationMembership.objects.get()
    assert membership.user == user
    assert membership.role == OrganizationMembership.Role.OWNER
    assert IdempotencyRecord.objects.count() == 1
    assert OutboxEvent.objects.filter(
        topic="organizations.organization-created",
    ).count() == 1


def test_non_member_and_django_staff_do_not_bypass_tenant(
    authenticated_client,
    user,
    admin_user,
):
    organization = Organization.objects.create(name="Hidden", slug="hidden")
    other = get_user_model().objects.create_user(
        username="other",
        email="other@example.com",
        password="pass",
    )
    OrganizationMembership.objects.create(
        organization=organization,
        user=other,
        role=OrganizationMembership.Role.OWNER,
    )

    assert authenticated_client.get(
        reverse("organization-detail", args=[organization.pk]),
    ).status_code == 404
    assert client_for(admin_user).get(
        reverse("organization-detail", args=[organization.pk]),
    ).status_code == 404

    response = authenticated_client.get(
        reverse("organization-list"),
        HTTP_X_TENANT_ID=str(organization.pk),
        HTTP_X_ROLE="owner",
    )
    assert response.status_code == 200
    assert response.json()["results"] == []


def test_owner_admin_member_permissions(authenticated_client, user):
    organization = Organization.objects.create(name="Acme", slug="acme")
    OrganizationMembership.objects.create(
        organization=organization,
        user=user,
        role=OrganizationMembership.Role.OWNER,
    )
    User = get_user_model()
    admin = User.objects.create_user(
        username="tenant-admin",
        email="tenant-admin@example.com",
        password="pass",
    )
    member = User.objects.create_user(
        username="tenant-member",
        email="tenant-member@example.com",
        password="pass",
    )

    add_url = reverse("organization-member-list", args=[organization.pk])
    assert authenticated_client.post(
        add_url,
        {"user_id": admin.pk, "role": "admin"},
        format="json",
    ).status_code == 201
    assert authenticated_client.post(
        add_url,
        {"user_id": member.pk, "role": "member"},
        format="json",
    ).status_code == 201

    assert client_for(admin).get(add_url).status_code == 200
    assert client_for(member).get(add_url).status_code == 403
    assert client_for(admin).post(
        add_url,
        {"user_id": user.pk, "role": "member"},
        format="json",
    ).status_code == 403


def test_last_owner_cannot_be_demoted_or_removed(authenticated_client, user):
    organization = Organization.objects.create(name="Acme", slug="acme")
    OrganizationMembership.objects.create(
        organization=organization,
        user=user,
        role=OrganizationMembership.Role.OWNER,
    )
    url = reverse(
        "organization-member-detail",
        args=[organization.pk, user.pk],
    )

    response = authenticated_client.patch(
        url,
        {"role": "admin"},
        format="json",
    )
    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"
    assert OrganizationMembership.objects.get(
        organization=organization,
        user=user,
    ).role == "owner"

    response = authenticated_client.delete(url)
    assert response.status_code == 400
    assert OrganizationMembership.objects.filter(
        organization=organization,
        user=user,
        role="owner",
    ).exists()


def test_second_owner_allows_first_owner_to_leave(authenticated_client, user):
    organization = Organization.objects.create(name="Acme", slug="acme")
    OrganizationMembership.objects.create(
        organization=organization,
        user=user,
        role=OrganizationMembership.Role.OWNER,
    )
    second = get_user_model().objects.create_user(
        username="second-owner",
        email="second-owner@example.com",
        password="pass",
    )
    OrganizationMembership.objects.create(
        organization=organization,
        user=second,
        role=OrganizationMembership.Role.OWNER,
    )

    response = authenticated_client.delete(
        reverse(
            "organization-member-detail",
            args=[organization.pk, user.pk],
        ),
    )
    assert response.status_code == 204
    assert not OrganizationMembership.objects.filter(
        organization=organization,
        user=user,
    ).exists()
    assert OrganizationMembership.objects.filter(
        organization=organization,
        user=second,
        role="owner",
    ).exists()
