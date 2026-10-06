import pytest
from django.core.cache import caches
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.core.models import IdempotencyRecord, OutboxEvent
from apps.core.outbox import process_outbox_batch
from apps.files import services as file_services
from apps.tickets.models import Ticket, TicketAttachment

pytestmark = pytest.mark.django_db(transaction=True)


def _ticket_payload(**overrides):
    return {
        "title": "Reference ticket",
        "description": "End-to-end foundation check",
        "priority": "high",
        **overrides,
    }


def _create(client, key="ticket-create-1", **overrides):
    return client.post(
        reverse("ticket-list"),
        _ticket_payload(**overrides),
        format="json",
        HTTP_IDEMPOTENCY_KEY=key,
    )


def test_create_replay_is_idempotent_and_outbox_is_atomic(authenticated_client):
    first = _create(authenticated_client)
    second = _create(authenticated_client)

    assert first.status_code == second.status_code == 201
    assert first.content == second.content
    assert first["Idempotency-Replayed"] == "false"
    assert second["Idempotency-Replayed"] == "true"
    assert Ticket.objects.count() == 1
    assert IdempotencyRecord.objects.count() == 1
    event = OutboxEvent.objects.get(topic="tickets.ticket-created")
    assert event.payload["ticket_id"] == first.json()["id"]
    assert process_outbox_batch() == 1
    event.refresh_from_db()
    assert event.published_at is not None


def test_owner_scope_staff_visibility_and_status_permission(
    authenticated_client,
    user,
    admin_user,
):
    staff_client = APIClient()
    staff_client.force_authenticate(admin_user)
    own = _create(authenticated_client).json()
    other = Ticket.objects.create(owner=admin_user, title="Staff ticket")

    response = authenticated_client.get(reverse("ticket-list"))
    assert [row["id"] for row in response.json()["results"]] == [own["id"]]
    assert authenticated_client.get(reverse("ticket-detail", args=[other.pk])).status_code == 404

    response = authenticated_client.patch(
        reverse("ticket-detail", args=[own["id"]]),
        {"status": "resolved"},
        format="json",
    )
    assert response.status_code == 403

    response = staff_client.patch(
        reverse("ticket-detail", args=[own["id"]]),
        {"status": "resolved"},
        format="json",
    )
    assert response.status_code == 200
    assert response.json()["status"] == "resolved"


@pytest.mark.parametrize(
    "query",
    [
        "unknown=1",
        "status=missing",
        "ordering=status",
        "page=0",
        "page_size=101",
        "search=a&search=b",
    ],
)
def test_list_uses_strict_query_contract(authenticated_client, query):
    response = authenticated_client.get(f"{reverse('ticket-list')}?{query}")
    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"


def test_filter_search_order_and_pagination(authenticated_client, user):
    Ticket.objects.bulk_create(
        [
            Ticket(owner=user, title=f"Incident {index:02d}", priority="high")
            for index in range(25)
        ]
    )
    response = authenticated_client.get(
        reverse("ticket-list"),
        {
            "priority": "high",
            "search": "Incident",
            "ordering": "title",
            "page": 2,
            "page_size": 10,
        },
    )
    body = response.json()
    assert response.status_code == 200
    assert set(body) == {"count", "next", "previous", "results"}
    assert body["count"] == 25
    assert len(body["results"]) == 10
    assert body["results"][0]["title"] == "Incident 10"


def test_private_file_can_be_attached_but_other_owner_file_cannot(
    authenticated_client,
    user,
    admin_user,
    settings,
    tmp_path,
):
    backend = {
        "BACKEND": "apps.files.backends.PrivateFileSystemStorage",
        "OPTIONS": {"location": str(tmp_path / "private")},
    }
    settings.STORAGES = {**settings.STORAGES, "private": backend, "default": backend}
    settings.PRIVATE_FILE_POLICIES = {
        "document": {
            "max_bytes": 1024,
            "validators": {".txt": "apps.files.validation.validate_text"},
        },
    }

    ticket_id = _create(authenticated_client).json()["id"]
    owned = file_services.upload_file(
        actor=user,
        upload=SimpleUploadedFile("owned.txt", b"private"),
    )
    response = authenticated_client.post(
        reverse("ticket-attach", args=[ticket_id]),
        {"file_id": str(owned.pk)},
        format="json",
    )
    assert response.status_code == 201
    assert response.json()["file_id"] == str(owned.pk)
    assert TicketAttachment.objects.filter(ticket_id=ticket_id, file=owned).exists()

    foreign = file_services.upload_file(
        actor=admin_user,
        upload=SimpleUploadedFile("foreign.txt", b"private"),
    )
    response = authenticated_client.post(
        reverse("ticket-attach", args=[ticket_id]),
        {"file_id": str(foreign.pk)},
        format="json",
    )
    assert response.status_code == 404


@override_settings(
    CACHES={
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
        "performance": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "ticket-summary-tests",
        },
    },
)
def test_summary_cache_invalidates_after_committed_mutation(authenticated_client):
    caches["performance"].clear()

    first = authenticated_client.get(reverse("ticket-summary"))
    assert first.status_code == 200
    assert first.json()["total"] == 0
    etag = first["ETag"]

    _create(authenticated_client, key="summary-create")
    second = authenticated_client.get(
        reverse("ticket-summary"),
        HTTP_IF_NONE_MATCH=etag,
    )
    assert second.status_code == 200
    assert second.json()["total"] == 1
    assert second["ETag"] != etag

    third = authenticated_client.get(
        reverse("ticket-summary"),
        HTTP_IF_NONE_MATCH=second["ETag"],
    )
    assert third.status_code == 304


def test_resolved_ticket_is_read_only_for_owner(authenticated_client):
    ticket_id = _create(authenticated_client).json()["id"]
    Ticket.objects.filter(pk=ticket_id).update(status="resolved")
    response = authenticated_client.patch(
        reverse("ticket-detail", args=[ticket_id]),
        {"title": "cannot edit"},
        format="json",
    )
    assert response.status_code == 403


def test_anonymous_access_is_rejected(api_client):
    assert api_client.get(reverse("ticket-list")).status_code == 401
    assert (
        api_client.post(
            reverse("ticket-list"),
            _ticket_payload(),
            format="json",
            HTTP_IDEMPOTENCY_KEY="anonymous",
        ).status_code
        == 401
    )


def _organization(user, *, name="Tenant", slug="tenant", role="member"):
    from apps.organizations.models import Organization, OrganizationMembership

    organization = Organization.objects.create(name=name, slug=slug)
    OrganizationMembership.objects.create(
        organization=organization,
        user=user,
        role=role,
    )
    return organization


def _organization_ticket_list(organization):
    return reverse(
        "organization-ticket-list",
        kwargs={"organization_id": organization.pk},
    )


def test_tenant_tickets_are_isolated_from_personal_and_other_tenants(
    authenticated_client,
    user,
):
    organization = _organization(user)
    personal = _create(authenticated_client, key="personal-isolation").json()
    tenant_response = authenticated_client.post(
        _organization_ticket_list(organization),
        _ticket_payload(title="Tenant ticket"),
        format="json",
        HTTP_IDEMPOTENCY_KEY="tenant-create",
    )
    assert tenant_response.status_code == 201
    tenant = tenant_response.json()
    assert tenant["organization_id"] == str(organization.pk)

    personal_rows = authenticated_client.get(reverse("ticket-list")).json()["results"]
    tenant_rows = authenticated_client.get(
        _organization_ticket_list(organization),
    ).json()["results"]
    assert [row["id"] for row in personal_rows] == [personal["id"]]
    assert [row["id"] for row in tenant_rows] == [tenant["id"]]

    other = _organization(user, name="Other", slug="other")
    assert authenticated_client.get(
        reverse(
            "organization-ticket-detail",
            kwargs={"organization_id": other.pk, "pk": tenant["id"]},
        ),
    ).status_code == 404


def test_non_member_and_staff_cannot_access_tenant_tickets(user, admin_user):
    owner = type(user).objects.create_user(
        username="tenant-owner",
        email="tenant-owner@example.com",
        password="pass",
    )
    organization = _organization(owner, role="owner")

    for actor in (user, admin_user):
        client = APIClient()
        client.force_authenticate(actor)
        response = client.get(
            _organization_ticket_list(organization),
            HTTP_X_TENANT_ID=str(organization.pk),
            HTTP_X_ROLE="owner",
        )
        assert response.status_code == 404


def test_tenant_admin_can_change_status_but_member_cannot(user):
    from apps.organizations.models import OrganizationMembership

    organization = _organization(user, role="member")
    client = APIClient()
    client.force_authenticate(user)
    created = client.post(
        _organization_ticket_list(organization),
        _ticket_payload(),
        format="json",
        HTTP_IDEMPOTENCY_KEY="tenant-role-create",
    ).json()
    detail = reverse(
        "organization-ticket-detail",
        kwargs={"organization_id": organization.pk, "pk": created["id"]},
    )
    assert client.patch(
        detail,
        {"status": "resolved"},
        format="json",
    ).status_code == 403

    OrganizationMembership.objects.filter(
        organization=organization,
        user=user,
    ).update(role="admin")
    response = client.patch(
        detail,
        {"status": "resolved"},
        format="json",
    )
    assert response.status_code == 200
    assert response.json()["status"] == "resolved"


def test_same_idempotency_key_isolated_between_tenants(authenticated_client, user):
    first_org = _organization(user, name="One", slug="one")
    second_org = _organization(user, name="Two", slug="two")

    first = authenticated_client.post(
        _organization_ticket_list(first_org),
        _ticket_payload(title="One"),
        format="json",
        HTTP_IDEMPOTENCY_KEY="same-key",
    )
    second = authenticated_client.post(
        _organization_ticket_list(second_org),
        _ticket_payload(title="Two"),
        format="json",
        HTTP_IDEMPOTENCY_KEY="same-key",
    )
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] != second.json()["id"]
    assert Ticket.objects.filter(organization__isnull=False).count() == 2


@override_settings(
    CACHES={
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
        "performance": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "tenant-ticket-summary-tests",
        },
    },
)
def test_tenant_summary_cache_is_scoped_and_invalidated(authenticated_client, user):
    caches["performance"].clear()
    first_org = _organization(user, name="One", slug="one")
    second_org = _organization(user, name="Two", slug="two")

    first_summary = reverse(
        "organization-ticket-summary",
        kwargs={"organization_id": first_org.pk},
    )
    second_summary = reverse(
        "organization-ticket-summary",
        kwargs={"organization_id": second_org.pk},
    )
    assert authenticated_client.get(first_summary).json()["total"] == 0
    assert authenticated_client.get(second_summary).json()["total"] == 0

    authenticated_client.post(
        _organization_ticket_list(first_org),
        _ticket_payload(),
        format="json",
        HTTP_IDEMPOTENCY_KEY="tenant-summary-create",
    )
    assert authenticated_client.get(first_summary).json()["total"] == 1
    assert authenticated_client.get(second_summary).json()["total"] == 0
