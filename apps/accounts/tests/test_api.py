import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

User = get_user_model()


@pytest.mark.django_db
class TestAccountsAPI:
    def test_registration_enforces_password_policy(self, api_client):
        response = api_client.post(
            reverse("user-list"),
            {
                "username": "weakuser",
                "email": "weak@example.com",
                "password": "123",
            },
        )

        assert response.status_code == 400
        assert response.json()["code"] == "validation_error"
        assert any(error["attr"] == "password" for error in response.json()["errors"])

    def test_registration_creates_user(self, api_client):
        response = api_client.post(
            reverse("user-list"),
            {
                "username": "newuser",
                "email": "newuser@example.com",
                "password": "Unique-Passphrase-2026!A",
            },
        )

        assert response.status_code == 201
        assert response.json()["email"] == "newuser@example.com"
        assert "password" not in response.json()

    def test_regular_user_cannot_list_users(self, authenticated_client):
        response = authenticated_client.get(reverse("user-list"))

        assert response.status_code == 403

    def test_admin_can_list_users(self, admin_client):
        response = admin_client.get(reverse("user-list"))

        assert response.status_code == 200
        assert "results" in response.json()

    def test_regular_user_cannot_read_another_user(
        self,
        authenticated_client,
        admin_user,
    ):
        response = authenticated_client.get(
            reverse("user-detail", kwargs={"pk": admin_user.pk})
        )

        assert response.status_code == 403

    def test_user_can_read_and_update_self(self, authenticated_client, user):
        me_response = authenticated_client.get(reverse("user-me"))
        assert me_response.status_code == 200
        assert me_response.json()["email"] == user.email

        update_response = authenticated_client.patch(
            reverse("user-update-me"),
            {"first_name": "Updated"},
        )
        assert update_response.status_code == 200
        assert update_response.json()["first_name"] == "Updated"

    def test_token_endpoint_authenticates_user(self, api_client, user):
        response = api_client.post(
            reverse("auth-token"),
            {
                "username": user.email,
                "password": "testpass123",
            },
        )

        assert response.status_code == 200
        assert response.json()["token"]
        assert response["Cache-Control"] == "no-store"
