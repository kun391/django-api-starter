"""SQL budgets for the real reference endpoints, not wall-clock benchmarks."""

import pytest
from django.urls import reverse
from rest_framework.authtoken.models import Token

from apps.accounts.models import User

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("page_size", [1, 20, 100])
def test_paginated_list_has_constant_query_budget(admin_client, django_assert_num_queries, page_size):
    User.objects.bulk_create([
        User(username=f"budget-{index:03d}", email=f"budget-{index:03d}@example.com")
        for index in range(110)
    ])
    # Forced authentication isolates the view: one COUNT and one bounded SELECT.
    # This catches future serializer/relation N+1 queries as page size grows.
    with django_assert_num_queries(2):
        response = admin_client.get(reverse("user-list"), {"page_size": page_size})
        body = response.json()
    assert response.status_code == 200
    assert len(body["results"]) == page_size
    assert body["count"] == 111
    assert response["Cache-Control"] == "no-store"
    assert "ETag" not in response


def test_filtered_second_page_remains_bounded(admin_client, django_assert_num_queries):
    User.objects.bulk_create([
        User(username=f"filtered-{index:03d}", first_name="Match")
        for index in range(45)
    ])
    with django_assert_num_queries(2):
        response = admin_client.get(reverse("user-list"), {
            "search": "Match", "is_active": "true", "ordering": "-username",
            "page": 2, "page_size": 20,
        })
        body = response.json()
    assert response.status_code == 200
    assert len(body["results"]) == 20
    assert body["count"] == 45
    assert body["next"] and body["previous"]


def test_detail_has_one_query(admin_client, user, django_assert_num_queries):
    with django_assert_num_queries(1):
        response = admin_client.get(reverse("user-detail", kwargs={"pk": user.pk}))
        assert response.json()["id"] == user.pk
    assert response["Cache-Control"] == "no-store"


def test_me_reuses_authenticated_user(authenticated_client, django_assert_num_queries):
    with django_assert_num_queries(0):
        response = authenticated_client.get(reverse("user-me"))
        assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"


def test_real_token_auth_does_not_introduce_n_plus_one(api_client, admin_user, django_assert_num_queries):
    token = Token.objects.create(user=admin_user)
    api_client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
    with django_assert_num_queries(3):
        response = api_client.get(reverse("user-list"))
        assert response.status_code == 200
    # TokenAuthentication joins its user; the profile needs no second lookup.
    with django_assert_num_queries(1):
        response = api_client.get(reverse("user-me"))
        assert response.status_code == 200


def test_sensitive_gets_never_turn_into_304(authenticated_client, admin_client):
    for client, path in ((authenticated_client, reverse("user-me")), (admin_client, reverse("user-list"))):
        response = client.get(path, HTTP_IF_NONE_MATCH="*")
        assert response.status_code == 200
        assert response["Cache-Control"] == "no-store"
        assert "ETag" not in response


def test_profile_mutation_returns_no_store(authenticated_client):
    response = authenticated_client.patch(reverse("user-update-me"), {"first_name": "Changed"}, format="json")
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    assert response.json()["first_name"] == "Changed"
