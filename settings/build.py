"""Offline static collection only. NEVER use this module to serve requests."""

from .base import *  # noqa: F403

DEBUG = False
SECRET_KEY = "static-collection-only-not-a-runtime-secret"
DATABASES = {"default": {"ENGINE": "django.db.backends.dummy"}}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}}
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
