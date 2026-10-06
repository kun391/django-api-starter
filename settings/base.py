"""Base settings shared by all environments."""

from pathlib import Path

from corsheaders.defaults import default_headers
from decouple import config  # type: ignore[import-untyped]

from .performance import build_performance_caches

BASE_DIR = Path(__file__).resolve().parent.parent

# Safe defaults: environment-specific settings opt into development behavior.
SECRET_KEY = config("SECRET_KEY", default="")
DEBUG = config("DEBUG", default=False, cast=bool)

ALLOWED_HOSTS = config(
    "ALLOWED_HOSTS",
    default="localhost,127.0.0.1",
    cast=lambda value: [item.strip() for item in value.split(",") if item.strip()],
)

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework.authtoken",
    "corsheaders",
    "django_filters",
    "drf_spectacular",
]

LOCAL_APPS = [
    "apps.core",
    "apps.accounts",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "apps.core.observability.RequestIdMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "apps.core.urls"
CSRF_FAILURE_VIEW = "apps.core.api.errors.csrf_failure"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "apps.core.wsgi.application"

AUTH_USER_MODEL = "accounts.User"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": config("POSTGRES_DB", default="django_api"),
        "USER": config("POSTGRES_USER", default="postgres"),
        "PASSWORD": config("POSTGRES_PASSWORD", default="postgres"),
        "HOST": config("POSTGRES_HOST", default="db"),
        "PORT": config("POSTGRES_PORT", default="5432"),
    }
}

# Dedicated opt-in data cache; never used by security throttles or idempotency.
CACHES = build_performance_caches()

MAILERS = {
    "default": {
        "BACKEND": "django.core.mail.backends.smtp.EmailBackend",
        "OPTIONS": {
            "host": config("EMAIL_HOST", default="localhost"),
            "port": config("EMAIL_PORT", default=25, cast=int),
            "username": config("EMAIL_HOST_USER", default=""),
            "password": config("EMAIL_HOST_PASSWORD", default=""),
            "use_tls": config("EMAIL_USE_TLS", default=False, cast=bool),
        },
    },
}

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "EXCEPTION_HANDLER": "apps.core.api.errors.exception_handler",
    "DEFAULT_PAGINATION_CLASS": "apps.core.api.pagination.StandardPagination",
    "PAGE_SIZE": 20,
    # Never trust X-Forwarded-For by default. Set this only when every request
    # traverses exactly that many trusted proxies which sanitize the header.
    "NUM_PROXIES": config("API_NUM_PROXIES", default=0, cast=int),
    "DEFAULT_FILTER_BACKENDS": [
        "apps.core.api.filtering.StrictDjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "apps.core.api.filtering.StableOrderingFilter",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

SECURITY_THROTTLE_RATES = {
    "registration_ip": {
        "limit": config("REGISTRATION_RATE_LIMIT", default=5, cast=int),
        "window_seconds": config("REGISTRATION_RATE_WINDOW_SECONDS", default=60, cast=int),
    },
    "auth_login_ip": {
        "limit": config("AUTH_LOGIN_IP_RATE_LIMIT", default=20, cast=int),
        "window_seconds": config("AUTH_LOGIN_RATE_WINDOW_SECONDS", default=60, cast=int),
    },
    "auth_login_credential": {
        "limit": config("AUTH_LOGIN_CREDENTIAL_RATE_LIMIT", default=10, cast=int),
        "window_seconds": config("AUTH_LOGIN_RATE_WINDOW_SECONDS", default=60, cast=int),
    },
}

CORS_ALLOWED_ORIGINS = config(
    "CORS_ALLOWED_ORIGINS",
    default="http://localhost:3000,http://127.0.0.1:3000",
    cast=lambda value: [item.strip() for item in value.split(",") if item.strip()],
)
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_HEADERS = (*default_headers, "x-request-id", "idempotency-key", "if-none-match", "if-match")
CORS_EXPOSE_HEADERS = ["X-Request-ID", "Idempotency-Replayed", "Retry-After", "ETag"]

CSRF_TRUSTED_ORIGINS = config(
    "CSRF_TRUSTED_ORIGINS",
    default="",
    cast=lambda value: [item.strip() for item in value.split(",") if item.strip()],
)

IDEMPOTENCY_TTL_SECONDS = config("IDEMPOTENCY_TTL_SECONDS", default=86400, cast=int)

OUTBOX_BATCH_SIZE = config("OUTBOX_BATCH_SIZE", default=100, cast=int)
OUTBOX_LEASE_SECONDS = config("OUTBOX_LEASE_SECONDS", default=60, cast=int)
OUTBOX_MAX_ATTEMPTS = config("OUTBOX_MAX_ATTEMPTS", default=10, cast=int)
OUTBOX_RETRY_BASE_SECONDS = config("OUTBOX_RETRY_BASE_SECONDS", default=5, cast=int)
OUTBOX_RETRY_MAX_SECONDS = config("OUTBOX_RETRY_MAX_SECONDS", default=3600, cast=int)
OUTBOX_MAX_EVENT_BYTES = config("OUTBOX_MAX_EVENT_BYTES", default=65536, cast=int)

CELERY_BROKER_URL = config(
    "RABBITMQ_URL",
    default="amqp://admin:admin@rabbitmq:5672/",
)
CELERY_RESULT_BACKEND = config("REDIS_URL", default="redis://redis:6379/0")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULE = {
    "dispatch-transactional-outbox": {
        "task": "core.dispatch_outbox",
        "schedule": 5.0,
    },
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Django API Template",
    "DESCRIPTION": "Versioned JSON API. See docs/api-contract.md for client conventions.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "PREPROCESSING_HOOKS": ["apps.core.api.schema.versioned_api_only"],
    "POSTPROCESSING_HOOKS": [
        "drf_spectacular.hooks.postprocess_schema_enums",
        "apps.core.api.schema.add_api_contract",
    ],
}

LOG_LEVEL = config("LOG_LEVEL", default="INFO")

# Containers should emit logs to stdout/stderr. Log aggregation belongs to the
# runtime platform rather than the application filesystem.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": "apps.core.observability.JsonFormatter",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": LOG_LEVEL,
    },
}
