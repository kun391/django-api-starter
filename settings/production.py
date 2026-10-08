"""Production settings. Staging intentionally shares this security contract."""

import sentry_sdk
from decouple import config  # type: ignore[import-untyped]
from django.core.exceptions import ImproperlyConfigured
from sentry_sdk.integrations.django import DjangoIntegration

from .base import *  # noqa: F403

DEBUG = False

SECRET_KEY = config("SECRET_KEY", default="")
if len(SECRET_KEY) < 50 or len(set(SECRET_KEY)) < 5 or SECRET_KEY.startswith("django-insecure-"):
    raise ImproperlyConfigured("Production SECRET_KEY must be a strong, independently generated secret of at least 50 characters.")

WEBHOOK_SIGNING_MASTER_KEY = config("WEBHOOK_SIGNING_MASTER_KEY", default="")
if len(WEBHOOK_SIGNING_MASTER_KEY) < 32:
    raise ImproperlyConfigured(
        "Production WEBHOOK_SIGNING_MASTER_KEY must be set separately and contain at least 32 characters."
    )

ALLOWED_HOSTS = config(
    "ALLOWED_HOSTS", default="",
    cast=lambda value: [item.strip() for item in value.split(",") if item.strip()],
)
if not ALLOWED_HOSTS or any("*" in host or "/" in host for host in ALLOWED_HOSTS):
    raise ImproperlyConfigured("Production ALLOWED_HOSTS must contain explicit hosts, without wildcards or URLs.")

# Default to certificate AND hostname verification; local isolated smoke tests
# explicitly select disable. Use a direct/session-pooled connection for migrations.
POSTGRES_SSLMODE = config("POSTGRES_SSLMODE", default="verify-full")
if POSTGRES_SSLMODE not in {"disable", "require", "verify-ca", "verify-full"}:
    raise ImproperlyConfigured("POSTGRES_SSLMODE must be disable, require, verify-ca or verify-full.")
DATABASES = {"default": {**DATABASES["default"], "CONN_MAX_AGE": 60, "CONN_HEALTH_CHECKS": True}}
DATABASES["default"]["OPTIONS"] = {"sslmode": POSTGRES_SSLMODE, "connect_timeout": 5}
POSTGRES_SSLROOTCERT = config("POSTGRES_SSLROOTCERT", default="")
if POSTGRES_SSLROOTCERT:
    DATABASES["default"]["OPTIONS"]["sslrootcert"] = POSTGRES_SSLROOTCERT

# This opt-in is safe only behind a proxy that OVERWRITES forwarded headers,
# with direct application ingress blocked. It does not configure trust for XFF.
TRUST_PROXY_SSL_HEADER = config("TRUST_PROXY_SSL_HEADER", default=False, cast=bool)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https") if TRUST_PROXY_SSL_HEADER else None
USE_X_FORWARDED_HOST = False
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_HSTS_INCLUDE_SUBDOMAINS = config("SECURE_HSTS_INCLUDE_SUBDOMAINS", default=True, cast=bool)
SECURE_HSTS_SECONDS = config("SECURE_HSTS_SECONDS", default=31536000, cast=int)
SECURE_HSTS_PRELOAD = config("SECURE_HSTS_PRELOAD", default=False, cast=bool)
SECURE_SSL_REDIRECT = True
# Minimal non-sensitive probes only. Auth, admin, files and all business APIs
# still require HTTPS. An internal container probe must not follow a redirect.
SECURE_REDIRECT_EXEMPT = [r"^health/live/$", r"^health/ready/$"]
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

CELERY_TASK_ALWAYS_EAGER = False
CELERY_TASK_EAGER_PROPAGATES = False

TELEMETRY_ENVIRONMENT = config("TELEMETRY_ENVIRONMENT", default="production")
TELEMETRY_OTLP_ENDPOINT = config("OTEL_EXPORTER_OTLP_ENDPOINT", default="")
if TELEMETRY_ENABLED:
    if TELEMETRY_EXPORTER != "otlp":
        raise ImproperlyConfigured(
            "Production telemetry requires TELEMETRY_EXPORTER=otlp."
        )
    if not TELEMETRY_OTLP_ENDPOINT.startswith(("http://", "https://")):
        raise ImproperlyConfigured(
            "Production OTEL_EXPORTER_OTLP_ENDPOINT must be an http(s) URL."
        )
    if not TELEMETRY_SERVICE_NAME.strip():
        raise ImproperlyConfigured(
            "Production TELEMETRY_SERVICE_NAME must be non-empty."
        )

SENTRY_DSN = config("SENTRY_DSN", default="")
if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[DjangoIntegration()],
        traces_sample_rate=config("SENTRY_TRACES_SAMPLE_RATE", default=0.0, cast=float),
        send_default_pii=config("SENTRY_SEND_DEFAULT_PII", default=False, cast=bool),
    )
