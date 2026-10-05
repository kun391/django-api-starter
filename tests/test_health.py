"""Tests for health check endpoints."""

import pytest
from django.urls import reverse


@pytest.mark.django_db
class TestHealthEndpoints:
    """Test health check endpoints."""

    def test_liveness(self, api_client):
        response = api_client.get(reverse("health_live"))

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_readiness(self, api_client):
        response = api_client.get(reverse("health_ready"))

        assert response.status_code == 200
        assert response.json() == {"status": "ready", "database": "ok"}

    def test_legacy_health_endpoint(self, api_client):
        response = api_client.get(reverse("health_check"))

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_legacy_database_health_endpoint(self, api_client):
        response = api_client.get(reverse("database_health"))

        assert response.status_code == 200
        assert response.json() == {"status": "ready", "database": "ok"}

    def test_celery_health(self, api_client):
        response = api_client.get(reverse("celery_health"))

        assert response.status_code in [200, 503]
        assert "status" in response.json()
        assert "celery" in response.json()
