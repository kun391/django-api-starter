"""PostgreSQL-backed fixed-window throttles for security-sensitive endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.utils import timezone
from django.utils.crypto import salted_hmac
from rest_framework.throttling import BaseThrottle

from apps.core.models import SecurityThrottleBucket
from apps.core.security import audit_security_event


class PostgresFixedWindowThrottle(BaseThrottle):
    """Cross-worker abuse protection without a mandatory Redis dependency."""

    scope = ""

    def __init__(self) -> None:
        self._wait_seconds: int | None = None

    def get_identity(self, request) -> str:
        return str(self.get_ident(request))

    def get_rate(self) -> tuple[int, int]:
        rates = getattr(settings, "SECURITY_THROTTLE_RATES", {})
        rate = rates.get(self.scope)
        if not isinstance(rate, dict):
            raise ImproperlyConfigured(
                f"Missing SECURITY_THROTTLE_RATES configuration for {self.scope!r}."
            )
        limit = rate.get("limit")
        window_seconds = rate.get("window_seconds")
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or limit <= 0
            or not isinstance(window_seconds, int)
            or isinstance(window_seconds, bool)
            or window_seconds <= 0
        ):
            raise ImproperlyConfigured(
                f"Security throttle {self.scope!r} requires positive integer "
                "limit and window_seconds values."
            )
        return limit, window_seconds

    def allow_request(self, request, view) -> bool:
        if connection.vendor != "postgresql":
            raise ImproperlyConfigured(
                "Security throttling requires PostgreSQL; no process-local fallback "
                "is used for security-sensitive endpoints."
            )

        identity = self.get_identity(request)
        limit, window_seconds = self.get_rate()
        now = timezone.now()
        epoch = int(now.timestamp())
        bucket_epoch = epoch - (epoch % window_seconds)
        bucket_start = datetime.fromtimestamp(bucket_epoch, UTC)
        bucket_end = bucket_start + timedelta(seconds=window_seconds)
        key_digest = salted_hmac(
            f"core.security-throttle.{self.scope}.v1",
            f"{bucket_epoch}:{identity}",
            algorithm="sha256",
        ).hexdigest()

        table = connection.ops.quote_name(SecurityThrottleBucket._meta.db_table)
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO {table}
                    (key_digest, scope, bucket_start, hits, expires_at)
                VALUES (%s, %s, %s, 1, %s)
                ON CONFLICT (key_digest)
                DO UPDATE SET hits = {table}.hits + 1
                RETURNING hits
                """,
                [key_digest, self.scope, bucket_start, bucket_end],
            )
            hits = int(cursor.fetchone()[0])

        if hits <= limit:
            return True

        self._wait_seconds = max(1, int((bucket_end - now).total_seconds()))
        audit_security_event(
            "api.throttle.blocked",
            outcome="blocked",
            throttle_scope=self.scope,
        )
        return False

    def wait(self) -> float | None:
        return self._wait_seconds


class RegistrationIPThrottle(PostgresFixedWindowThrottle):
    scope = "registration_ip"


class LoginIPThrottle(PostgresFixedWindowThrottle):
    scope = "auth_login_ip"


class LoginCredentialThrottle(PostgresFixedWindowThrottle):
    scope = "auth_login_credential"

    def get_identity(self, request) -> str:
        value = request.data.get("username", "")
        return str(value).strip().casefold() or "<missing>"
