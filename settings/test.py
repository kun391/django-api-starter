"""Deterministic settings for automated tests."""

from .base import *  # noqa: F403

DEBUG = False
SECRET_KEY = "test-only-secret-key"
ALLOWED_HOSTS = ["testserver", "localhost"]

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

MAILERS = {
    "default": {
        "BACKEND": "django.core.mail.backends.locmem.EmailBackend",
    },
}

# Unit tests never contact a shared cache from a developer's environment.
# Cache/backend tests override only the dedicated performance alias explicitly.
CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
    "performance": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"},
}

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
