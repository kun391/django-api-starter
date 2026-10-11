"""Starter hygiene and first-project defaults, without runtime boilerplate."""

import re
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.template.loader import get_template


ROOT = Path(__file__).resolve().parents[1]


def test_no_numbered_development_phases_in_user_facing_docs():
    # Historical database migrations are deliberately immutable and excluded.
    docs = [
        ROOT / "README.md",
        ROOT / "AGENTS.md",
        ROOT / "ARCHITECTURE.md",
        *(ROOT / "docs").glob("*.md"),
        *(ROOT / "apps").glob("*/README.md"),
        *(ROOT / "apps").glob("*/module.yaml"),
    ]
    for document in docs:
        assert not re.search(
            r"\bphase\s*\d+\b", document.read_text(encoding="utf-8"), re.I
        ), document.relative_to(ROOT)

    assert (ROOT / "docs/first-project.md").is_file()


def test_failure_fixture_has_descriptive_name():
    assert (ROOT / "tests/failure_urls.py").is_file()
    assert not (ROOT / "tests/phase7_urls.py").exists()


def test_service_layout_is_flat_and_importable():
    from apps.accounts.services import register_user

    assert (ROOT / "apps/accounts/services.py").is_file()
    assert not (ROOT / "apps/accounts/services").exists()
    assert callable(register_user)


def test_app_template_discovery_does_not_require_absent_project_directory():
    # Existing transactional email templates live under their owning app.
    assert settings.TEMPLATES[0]["DIRS"] == []
    assert settings.TEMPLATES[0]["APP_DIRS"] is True
    template = get_template("notifications/ticket-created/subject.txt")
    assert template is not None


@pytest.mark.django_db
def test_accounts_service_registration_preserves_password_policy():
    from apps.accounts.services import register_user

    user = register_user(
        email="first@example.test",
        username="first-project-user",
        password="FirstProject!2026-Safe",
    )
    assert user.check_password("FirstProject!2026-Safe")
    assert not user.is_staff
    assert get_user_model().objects.filter(pk=user.pk).exists()

    with pytest.raises(ValidationError):
        register_user(
            email="weak@example.test",
            username="weak-project-user",
            password="password",
        )
    assert not get_user_model().objects.filter(
        username="weak-project-user"
    ).exists()
