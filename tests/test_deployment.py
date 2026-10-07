"""Release guardrails: configuration, preflight and real PostgreSQL locking."""

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch

import pytest
import yaml
from django.core import checks
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, connections
from django.db.utils import OperationalError
from django.test import override_settings

from apps.core.management.commands.release_migrate import MIGRATION_LOCK_ID

ROOT = Path(__file__).resolve().parents[1]
TEST_SECRET = "ci-only-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ-abcdefghijklmnopqrstuvwxyz"


def load_settings(module="settings.production", **overrides):
    environment = {
        **os.environ, "DJANGO_SETTINGS_MODULE": module, "SECRET_KEY": TEST_SECRET,
        "WEBHOOK_SIGNING_MASTER_KEY": TEST_SECRET,
        "ALLOWED_HOSTS": "api.example.test", "TRUST_PROXY_SSL_HEADER": "False",
        "PRIVATE_FILE_BACKEND": "filesystem", "SENTRY_DSN": "",
        "POSTGRES_SSLMODE": "verify-full", "POSTGRES_SSLROOTCERT": "",
        "PERFORMANCE_CACHE_BACKEND": "django.core.cache.backends.dummy.DummyCache",
        **overrides,
    }
    script = (
        "import django,json; django.setup(); from django.conf import settings as s;"
        "print(json.dumps({'debug':s.DEBUG,'session_secure':s.SESSION_COOKIE_SECURE,"
        "'csrf_secure':s.CSRF_COOKIE_SECURE,'redirect':s.SECURE_SSL_REDIRECT,"
        "'proxy':s.SECURE_PROXY_SSL_HEADER,'sslmode':s.DATABASES['default']['OPTIONS']['sslmode']}))"
    )
    return subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=environment, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("module", ["settings.production", "settings.staging"])
def test_production_and_staging_share_secure_defaults(module):
    result = load_settings(module)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"debug": False, "session_secure": True,
        "csrf_secure": True, "redirect": True, "proxy": None, "sslmode": "verify-full"}


def test_proxy_trust_requires_explicit_opt_in():
    result = load_settings(TRUST_PROXY_SSL_HEADER="True")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["proxy"] == ["HTTP_X_FORWARDED_PROTO", "https"]


@pytest.mark.parametrize("overrides", [
    {"SECRET_KEY": ""}, {"SECRET_KEY": "weak"}, {"SECRET_KEY": "a" * 60},
    {"SECRET_KEY": "django-insecure-" + TEST_SECRET}, {"ALLOWED_HOSTS": ""},
    {"ALLOWED_HOSTS": "*"}, {"ALLOWED_HOSTS": "https://api.example.test"},
    {"POSTGRES_SSLMODE": "typo"}, {"WEBHOOK_SIGNING_MASTER_KEY": ""},
])
def test_unsafe_production_settings_fail_closed(overrides):
    result = load_settings(**overrides)
    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr


@pytest.mark.django_db
def test_probes_ignore_credentials_and_do_not_cache(api_client, django_assert_num_queries):
    with django_assert_num_queries(0):
        response = api_client.get("/health/live/", HTTP_AUTHORIZATION="Token bad")
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"


@pytest.mark.django_db
def test_readiness_failure_is_generic_and_uncached(api_client):
    with patch("apps.core.health.views.connection.cursor", side_effect=OperationalError("password-secret")):
        response = api_client.get("/health/ready/", HTTP_AUTHORIZATION="Token bad")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "database": "unavailable"}
    assert response["Cache-Control"] == "no-store"
    assert "password-secret" not in response.content.decode()


@pytest.fixture
def release_settings(tmp_path):
    (tmp_path / "staticfiles.json").write_text("{}")
    # Apply one holder: a later nested override would mask SETTINGS_MODULE with
    # UserSettingsHolder's class-level None, unlike a real production process.
    with override_settings(SETTINGS_MODULE="settings.production", DEBUG=False, STATIC_ROOT=tmp_path):
        yield tmp_path


@pytest.mark.django_db
def test_preflight_blocks_pending_migrations_and_checks_history(release_settings):
    executor = Mock()
    executor.migration_plan.return_value = [("pending", False)]
    with patch("apps.core.management.commands.release_check.checks.run_checks", return_value=[]):
        with patch("apps.core.management.commands.release_check.MigrationExecutor", return_value=executor):
            with pytest.raises(CommandError, match="Pending migrations"):
                call_command("release_check", stdout=StringIO())
            call_command("release_check", allow_pending_migrations=True, stdout=StringIO())
            executor.loader.check_consistent_history.assert_called_with(connection)
            executor.migration_plan.return_value = []
            call_command("release_check", stdout=StringIO())


@pytest.mark.django_db
def test_preflight_treats_warnings_as_failures_except_preload_policy(release_settings):
    with patch("apps.core.management.commands.release_check.checks.run_checks", return_value=[checks.Warning("weak", id="security.W009")]):
        with pytest.raises(CommandError, match="security.W009"):
            call_command("release_check", stdout=StringIO())
    with patch("apps.core.management.commands.release_check.checks.run_checks", return_value=[checks.Warning("preload", id="security.W021")]):
        output = StringIO()
        call_command("release_check", stdout=output)
        assert "preload" in output.getvalue()


def test_preflight_rejects_nonproduction_settings():
    with pytest.raises(CommandError, match="production or staging"):
        call_command("release_check", stdout=StringIO())


def test_preflight_requires_built_static_manifest(release_settings):
    (release_settings / "staticfiles.json").unlink()
    with patch("apps.core.management.commands.release_check.checks.run_checks", return_value=[]):
        with pytest.raises(CommandError, match="Static manifest missing"):
            call_command("release_check", stdout=StringIO())


@pytest.mark.django_db(transaction=True)
def test_migration_runner_is_forward_only_and_closes_lock_on_failure():
    with patch("apps.core.management.commands.release_migrate.call_command", side_effect=RuntimeError("migration failure")) as migrate:
        with pytest.raises(RuntimeError):
            call_command("release_migrate", stdout=StringIO())
        assert migrate.call_args.args == ("migrate",)
        assert migrate.call_args.kwargs["interactive"] is False
    # A fresh session must acquire the lock: the failed command released it.
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [MIGRATION_LOCK_ID])
        assert cursor.fetchone()[0] is True
        cursor.execute("SELECT pg_advisory_unlock(%s)", [MIGRATION_LOCK_ID])


@pytest.mark.django_db(transaction=True)
def test_concurrent_migration_is_rejected_without_running_a_second_migrate():
    held, release = Event(), Event()

    def hold_lock():
        try:
            with connections["default"].cursor() as cursor:
                cursor.execute("SELECT pg_advisory_lock(%s)", [MIGRATION_LOCK_ID])
                held.set()
                assert release.wait(timeout=15)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(hold_lock)
        try:
            assert held.wait(timeout=15)
            with patch("apps.core.management.commands.release_migrate.call_command") as migrate:
                with pytest.raises(CommandError, match="Another release migration"):
                    call_command("release_migrate", stdout=StringIO())
                migrate.assert_not_called()
        finally:
            release.set()
        future.result(timeout=15)


@pytest.mark.django_db(transaction=True)
def test_migration_plan_has_bounded_session_timeouts():
    def migration(*args, **kwargs):
        assert args == ("migrate",) and kwargs["plan"] is True
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('lock_timeout'), current_setting('statement_timeout')")
            assert cursor.fetchone() == ("10s", "5min")

    with patch("apps.core.management.commands.release_migrate.call_command", side_effect=migration):
        call_command("release_migrate", plan=True, stdout=StringIO())
    with pytest.raises(CommandError, match="timeouts"):
        call_command("release_migrate", lock_timeout_ms=0, stdout=StringIO())


def test_production_compose_is_standalone_readonly_and_private():
    document = yaml.safe_load((ROOT / "compose.production.yaml").read_text())
    assert "db" not in document["services"]
    for service in document["services"].values():
        assert "build" not in service
        assert service["read_only"] is True
        assert service["user"] == "10001:10001"
        assert service["cap_drop"] == ["ALL"]
        assert service["env_file"][0]["format"] == "raw"
    assert document["services"]["web"]["ports"][0].startswith("127.0.0.1:")


def test_release_workflow_never_publishes_from_pr_and_tests_before_push():
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    triggers = workflow.get("on", workflow.get(True))
    assert set(triggers) == {"workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}
    publish = workflow["jobs"]["publish"]
    assert publish["needs"] == "verify"
    steps = publish["steps"]
    test_index = next(index for index, step in enumerate(steps) if "smoke_production.sh" in step.get("run", ""))
    login_index = next(index for index, step in enumerate(steps) if "docker login" in step.get("run", ""))
    push_index = next(index for index, step in enumerate(steps) if "docker push" in step.get("run", ""))
    assert test_index < login_index < push_index
