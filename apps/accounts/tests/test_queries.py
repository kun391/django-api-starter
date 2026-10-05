import pytest
from django.urls import reverse

from apps.accounts.models import User

pytestmark = pytest.mark.django_db


def test_pagination_envelope_and_bounds(admin_client):
    User.objects.bulk_create([
        User(username=f"person{index:03d}", email=f"person{index:03d}@example.com")
        for index in range(25)
    ])
    response = admin_client.get(reverse("user-list"))
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"count", "next", "previous", "results"}
    assert body["count"] == 26
    assert len(body["results"]) == 20
    assert body["next"] and body["previous"] is None
    response = admin_client.get(reverse("user-list"), {"page": 2, "page_size": 10})
    assert len(response.json()["results"]) == 10
    assert response.json()["previous"] and response.json()["next"]
    response = admin_client.get(reverse("user-list"), {"page_size": 100})
    assert len(response.json()["results"]) == 26


@pytest.mark.parametrize("query", [
    "page=0", "page=-1", "page=last", "page=1.5", "page=", "page=1&page=2",
    "page_size=0", "page_size=101", "page_size=bad", "page_size=",
    "unknown=1", "email__contains=example", "ordering=password", "ordering=",
    "ordering=id,--email", "is_active=maybe", "search=a&search=b",
])
def test_invalid_query_is_not_silently_ignored(admin_client, query):
    response = admin_client.get(f"{reverse('user-list')}?{query}")
    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"
    assert response.json()["errors"]


def test_page_out_of_range_is_404(admin_client):
    response = admin_client.get(reverse("user-list"), {"page": 999})
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_exact_filter_search_and_stable_ordering(admin_client):
    users = User.objects.bulk_create([
        User(username="z-user", email="z@example.com", first_name="Matching", is_active=False),
        User(username="a-user", email="a@example.com", first_name="Matching", is_active=False),
        User(username="other", email="other@example.com", first_name="Other"),
    ])
    joined = users[0].date_joined
    User.objects.filter(pk__in=[user.pk for user in users]).update(date_joined=joined)
    response = admin_client.get(reverse("user-list"), {
        "is_active": "false", "search": "Matching", "ordering": "-date_joined",
    })
    assert response.status_code == 200
    assert [user["id"] for user in response.json()["results"]] == [users[0].pk, users[1].pk]
    response = admin_client.get(reverse("user-list"), {"email": "a@example.com"})
    assert [user["id"] for user in response.json()["results"]] == [users[1].pk]
    response = admin_client.get(reverse("user-list"), {"is_active": "false", "ordering": "username"})
    assert [user["username"] for user in response.json()["results"]] == ["a-user", "z-user"]


def test_empty_list_is_valid(admin_client):
    response = admin_client.get(reverse("user-list"), {"username": "not-present"})
    assert response.json() == {"count": 0, "next": None, "previous": None, "results": []}
