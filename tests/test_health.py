"""Tests for health check endpoints."""

import pytest
from django.urls import reverse


@pytest.mark.django_db
class TestHealthEndpoints:
    """Test health check endpoints."""

    def test_health_check(self, api_client):
        """Test basic health check endpoint."""
        url = reverse("health_check")
        response = api_client.get(url)

        assert response.status_code == 200
        assert response.json()["status"] == "healthy"
        assert "Django API Template is running" in response.json()["message"]

    def test_database_health(self, api_client):
        """Test database health check endpoint."""
        url = reverse("database_health")
        response = api_client.get(url)

        assert response.status_code == 200
        assert response.json()["status"] == "healthy"
        assert response.json()["database"] == "connected"

    def test_celery_health(self, api_client):
        """Test Celery health check endpoint."""
        url = reverse("celery_health")
        response = api_client.get(url)

        assert response.status_code in [200, 503]
        assert "status" in response.json()
        assert "celery" in response.json()
