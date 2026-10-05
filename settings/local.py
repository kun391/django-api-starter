"""Local development settings."""

from decouple import config

from .base import *  # noqa: F403

DEBUG = True
SECRET_KEY = config(
    "SECRET_KEY",
    default="django-insecure-local-development-only",
)

INSTALLED_APPS += ["debug_toolbar", "django_extensions"]  # noqa: F405
MIDDLEWARE += ["debug_toolbar.middleware.DebugToolbarMiddleware"]  # noqa: F405
INTERNAL_IPS = ["127.0.0.1", "localhost"]

ALLOWED_HOSTS = ["*"]
CORS_ALLOW_ALL_ORIGINS = True

MAILERS = {
    "default": {
        "BACKEND": "django.core.mail.backends.console.EmailBackend",
    },
}

STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

CELERY_TASK_ALWAYS_EAGER = False
CELERY_TASK_EAGER_PROPAGATES = True

LOGGING["loggers"] = {  # noqa: F405
    "django.db.backends": {
        "handlers": ["console"],
        "level": "DEBUG",
        "propagate": False,
    },
}
