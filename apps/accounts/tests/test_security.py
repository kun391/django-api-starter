from datetime import timedelta

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.core.models import SecurityThrottleBucket


def configure_rates(settings, *, registration=100, login_ip=100, login_credential=100):
    settings.SECURITY_THROTTLE_RATES = {
        "registration_ip": {"limit": registration, "window_seconds": 60},
        "auth_login_ip": {"limit": login_ip, "window_seconds": 60},
        "auth_login_credential": {"limit": login_credential, "window_seconds": 60},
    }


@pytest.mark.django_db
class TestAuthSecurity:
    def test_login_is_throttled_by_credential_without_persisting_email(
        self, api_client, user, settings
    ):
        configure_rates(settings, login_credential=2)

        for _ in range(2):
            response = api_client.post(
                reverse("auth-token"),
                {"username": user.email, "password": "wrong-password"},
            )
            assert response.status_code == 400

        response = api_client.post(
            reverse("auth-token"),
            {"username": user.email, "password": "wrong-password"},
        )
        assert response.status_code == 429
        assert response.json()["code"] == "throttled"
        assert int(response["Retry-After"]) >= 1

        buckets = SecurityThrottleBucket.objects.filter(scope="auth_login_credential")
        assert buckets.exists()
        assert all(user.email not in bucket.key_digest for bucket in buckets)

    def test_login_credential_buckets_are_separate(self, api_client, settings):
        configure_rates(settings, login_credential=1)
        first = api_client.post(
            reverse("auth-token"),
            {"username": "one@example.com", "password": "wrong-password"},
        )
        second = api_client.post(
            reverse("auth-token"),
            {"username": "two@example.com", "password": "wrong-password"},
        )
        assert first.status_code == 400
        assert second.status_code == 400

    def test_registration_is_throttled_by_remote_address(self, api_client, settings):
        configure_rates(settings, registration=1)

        first = api_client.post(
            reverse("user-list"),
            {
                "username": "first",
                "email": "first@example.com",
                "password": "Unique-Passphrase-2026!A",
            },
            REMOTE_ADDR="203.0.113.10",
        )
        assert first.status_code == 201

        second = api_client.post(
            reverse("user-list"),
            {
                "username": "second",
                "email": "second@example.com",
                "password": "Unique-Passphrase-2026!B",
            },
            REMOTE_ADDR="203.0.113.10",
        )
        assert second.status_code == 429

    def test_forwarded_for_is_not_trusted_by_default(self, api_client, settings):
        configure_rates(settings, registration=1)

        first = api_client.post(
            reverse("user-list"),
            {
                "username": "xff-first",
                "email": "xff-first@example.com",
                "password": "Unique-Passphrase-2026!A",
            },
            REMOTE_ADDR="198.51.100.8",
            HTTP_X_FORWARDED_FOR="1.1.1.1",
        )
        second = api_client.post(
            reverse("user-list"),
            {
                "username": "xff-second",
                "email": "xff-second@example.com",
                "password": "Unique-Passphrase-2026!B",
            },
            REMOTE_ADDR="198.51.100.8",
            HTTP_X_FORWARDED_FOR="2.2.2.2",
        )
        assert first.status_code == 201
        assert second.status_code == 429

    def test_token_can_be_revoked(self, api_client, user, settings):
        configure_rates(settings)
        login = api_client.post(
            reverse("auth-token"),
            {"username": user.email, "password": "testpass123"},
        )
        token = login.json()["token"]

        token_client = APIClient()
        token_client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
        revoke = token_client.post(reverse("auth-token-revoke"))
        assert revoke.status_code == 204
        assert revoke["Cache-Control"] == "no-store"
        assert not Token.objects.filter(user=user).exists()

        after = token_client.get(reverse("user-me"))
        assert after.status_code == 401

    def test_self_service_cannot_escalate_privileges(
        self, authenticated_client, user, settings
    ):
        configure_rates(settings)
        response = authenticated_client.patch(
            reverse("user-update-me"),
            {
                "first_name": "Still Safe",
                "is_staff": True,
                "is_superuser": True,
                "email": "attacker-controlled@example.com",
            },
        )
        assert response.status_code == 200
        user.refresh_from_db()
        assert user.first_name == "Still Safe"
        assert user.is_staff is False
        assert user.is_superuser is False
        assert user.email == "test@example.com"

    def test_security_events_are_structured_without_credentials(
        self, api_client, user, settings, caplog
    ):
        configure_rates(settings)
        with caplog.at_level("INFO", logger="security"):
            response = api_client.post(
                reverse("auth-token"),
                {"username": user.email, "password": "wrong-password"},
            )
        assert response.status_code == 400
        record = next(
            record
            for record in caplog.records
            if getattr(record, "security_event", None) == "auth.login.failed"
        )
        assert record.outcome == "denied"
        assert not hasattr(record, "username")
        assert user.email not in record.getMessage()

    def test_cleanup_command_deletes_only_expired_buckets(self):
        expired = SecurityThrottleBucket.objects.create(
            key_digest="a" * 64,
            scope="test",
            bucket_start=timezone.now() - timedelta(minutes=2),
            hits=1,
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        active = SecurityThrottleBucket.objects.create(
            key_digest="b" * 64,
            scope="test",
            bucket_start=timezone.now(),
            hits=1,
            expires_at=timezone.now() + timedelta(minutes=1),
        )

        call_command("purge_security_throttles", batch_size=100)

        assert not SecurityThrottleBucket.objects.filter(pk=expired.pk).exists()
        assert SecurityThrottleBucket.objects.filter(pk=active.pk).exists()
