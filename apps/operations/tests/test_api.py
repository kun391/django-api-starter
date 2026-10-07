import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db(transaction=True)


def test_operations_endpoint_requires_staff(authenticated_client):
    response = authenticated_client.get(reverse("operations-queues"))

    assert response.status_code == 403


def test_staff_can_read_aggregate_queue_state(admin_client):
    response = admin_client.get(reverse("operations-queues"))

    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    body = response.json()
    assert body["status"] == "ok"
    assert set(body["queues"]) == {"outbox", "webhooks", "notifications"}
    for state in body["queues"].values():
        assert set(state) == {
            "pending",
            "retrying",
            "terminal_failures",
            "active_leases",
            "stale_leases",
            "oldest_pending_age_seconds",
            "status",
        }


def test_operations_endpoint_rejects_anonymous_client(api_client):
    response = api_client.get(reverse("operations-queues"))

    assert response.status_code in {401, 403}
