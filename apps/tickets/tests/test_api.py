import pytest
from django.core.cache import caches
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

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
    admin_client,
    user,
    admin_user,
):
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

    response = admin_client.patch(
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
