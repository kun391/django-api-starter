"""
Staging settings for Django API Template.
"""

from .base import *  # noqa

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = False

# Security settings (less strict than production)
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_SECONDS = 31536000
SECURE_SSL_REDIRECT = False  # Disable for staging
SESSION_COOKIE_SECURE = False  # Disable for staging
CSRF_COOKIE_SECURE = False  # Disable for staging
X_FRAME_OPTIONS = 'SAMEORIGIN'

# Static files
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# Celery settings for staging
CELERY_TASK_ALWAYS_EAGER = False
CELERY_TASK_EAGER_PROPAGATES = False

# Sentry configuration for staging
import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration

sentry_sdk.init(
    dsn=config('SENTRY_DSN', default=''),
    integrations=[DjangoIntegration()],
    traces_sample_rate=0.5,  # Lower sample rate for staging
    send_default_pii=False,  # Don't send PII in staging
)