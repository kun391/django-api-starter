"""The project wiring lives in config; core remains reusable primitives."""

from importlib import import_module
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.urls import resolve, reverse

ROOT = Path(__file__).resolve().parents[1]


def test_project_entrypoints_are_config_owned():
    assert settings.ROOT_URLCONF == "config.urls"
    assert settings.WSGI_APPLICATION == "config.wsgi.application"
    assert import_module("config.urls").urlpatterns
    from config.wsgi import application

    assert application is not None
    for obsolete in (
        ROOT / "apps/core/urls.py",
        ROOT / "apps/core/wsgi.py",
        ROOT / "apps/core/celery.py",
    ):
        assert not obsolete.exists(), f"Obsolete composition entrypoint: {obsolete}"


def test_http_routes_and_error_handlers_remain_at_original_paths():
    urls = import_module("config.urls")
    for name, expected in (
        ("health_live", "/health/live/"),
        ("health_ready", "/health/ready/"),
        ("health_check", "/health/"),
        ("database_health", "/health/db/"),
        ("celery_health", "/health/celery/"),
        ("schema", "/api/schema/"),
    ):
        assert reverse(name) == expected
        assert resolve(expected).url_name == name

    assert urls.handler400 == "apps.core.api.errors.bad_request"
    assert urls.handler403 == "apps.core.api.errors.permission_denied"
    assert urls.handler404 == "apps.core.api.errors.not_found"
    assert urls.handler500 == "apps.core.api.errors.server_error"


def test_optional_celery_health_stays_unavailable_in_minimal_runtime(api_client):
    # Minimal images intentionally have no celery extra installed.
    with patch("config.health.find_spec", return_value=None):
        response = api_client.get(reverse("celery_health"))
    assert response.status_code == 503
    assert response.json() == {
        "status": "disabled",
        "celery": "not installed",
    }
    assert response["Cache-Control"] == "no-store"


def test_optional_celery_entrypoint_when_extra_installed():
    import pytest

    pytest.importorskip("celery")
    from config.celery import app

    assert app.main == "core"
    assert "core.dispatch_outbox" in app.tasks


def test_runtime_manifests_do_not_reference_obsolete_entrypoints():
    references = (
        ROOT / "Dockerfile",
        ROOT / "compose.yaml",
        ROOT / "compose.production.yaml",
        ROOT / "Makefile",
        ROOT / "scripts/smoke_production.sh",
        ROOT / "settings/base.py",
        ROOT / "docs/storage.md",
        ROOT / "tests/phase7_urls.py",
    )
    for path in references:
        source = path.read_text(encoding="utf-8")
        assert "apps.core.wsgi" not in source, path
        assert "apps.core.celery" not in source, path
        assert "apps.core.urls" not in source, path
    assert "config.wsgi:application" in (ROOT / "Dockerfile").read_text()
    assert "config.celery:app" in (ROOT / "Dockerfile").read_text()
