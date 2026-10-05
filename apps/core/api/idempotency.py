"""Opt-in, PostgreSQL-backed idempotency for short authenticated JSON POSTs.

Business writes and the replay record commit in ONE default-database transaction.
No distributed exactly-once claim: do not perform external side effects here.
"""

import json
import re
from collections.abc import Callable
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, transaction
from django.http import HttpResponse
from django.utils import timezone
from django.utils.crypto import salted_hmac
from rest_framework.exceptions import (
    APIException,
    MethodNotAllowed,
    NotAuthenticated,
    UnsupportedMediaType,
    ValidationError,
)
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response

from apps.core.models import IdempotencyRecord

_KEY_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
_MAX_JSON_BYTES = 64 * 1024
_REPLAY_HEADERS = {"location", "etag"}


class IdempotencyInProgress(APIException):
    status_code = 409
    default_code = "idempotency_in_progress"
    default_detail = "A request with this key is still processing. Retry later."
    wait = 1


class IdempotencyKeyReused(APIException):
    status_code = 422
    default_code = "idempotency_key_reused"
    default_detail = "This key was already used with a different request."


def _digest(namespace, value):
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return salted_hmac(f"core.idempotency.{namespace}.v1", canonical, algorithm="sha256").hexdigest()


def _response(body, status_code, headers, *, replayed) -> HttpResponse:
    response = HttpResponse(body, status=status_code, content_type="application/json")
    for name, value in headers.items():
        response[name] = value
    response["Idempotency-Replayed"] = "true" if replayed else "false"
    response["Cache-Control"] = "no-store"
    return response


def idempotent_post(request, operation: Callable[[], Response], *, scope: str) -> HttpResponse:
    """Call AFTER authentication and all relevant authorization checks.

    scope is a server-defined operation/tenant boundary, never a client header.
    The header is required only on endpoints that explicitly call this helper.
    Operations raise exceptions for failures; only successful Responses are valid.
    """
    if not request.user.is_authenticated or request.user.pk is None:
        raise NotAuthenticated()
    if request.method != "POST":
        raise MethodNotAllowed(request.method)
    if request.content_type != "application/json":
        raise UnsupportedMediaType(request.content_type)
    key = request.headers.get("Idempotency-Key", "")
    if not _KEY_RE.fullmatch(key):
        raise ValidationError({"Idempotency-Key": "Required: 1-128 ASCII letters, digits, '.', '_', ':' or '-'."})
    if not scope or len(scope) > 200:
        raise ImproperlyConfigured("Idempotency requires a server-defined scope of 1-200 characters.")
    if connection.vendor != "postgresql":
        raise ImproperlyConfigured("Idempotency requires PostgreSQL; no unsafe cache fallback is provided.")

    payload = request.data
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    if len(canonical.encode("utf-8")) > _MAX_JSON_BYTES:
        raise ValidationError({"body": "Idempotent JSON requests are limited to 64 KiB."})
    key_digest = _digest("key", [str(request.user.pk), scope, request.method, request.path, key])
    request_digest = _digest("request", [request.META.get("QUERY_STRING", ""), payload])
    lock_id = int.from_bytes(bytes.fromhex(key_digest)[:8], "big", signed=True)
    ttl = getattr(settings, "IDEMPOTENCY_TTL_SECONDS", 86400)
    if not isinstance(ttl, int) or ttl <= 0:
        raise ImproperlyConfigured("IDEMPOTENCY_TTL_SECONDS must be a positive integer.")

    # durable=True rejects accidental nesting inside ATOMIC_REQUESTS or service
    # transactions. The commit must finish before reporting success to a client.
    with transaction.atomic(durable=True):
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_xact_lock(%s)", [lock_id])
            if not cursor.fetchone()[0]:
                raise IdempotencyInProgress()

        record = IdempotencyRecord.objects.filter(key_digest=key_digest).first()
        if record is not None and record.expires_at > timezone.now():
            if record.request_digest != request_digest:
                raise IdempotencyKeyReused()
            return _response(record.response_body, record.response_status, record.response_headers, replayed=True)
        if record is not None:
            record.delete()

        result = operation()
        if not isinstance(result, Response) or not 200 <= result.status_code < 300:
            raise ImproperlyConfigured("Idempotent operations must return a successful DRF Response; raise exceptions for failures.")
        if result.cookies:
            raise ImproperlyConfigured("Responses that set cookies cannot be replayed.")
        unsupported = {name.lower() for name in result.headers} - _REPLAY_HEADERS - {"content-type"}
        if unsupported:
            raise ImproperlyConfigured("Idempotent response headers must be explicitly replay-safe.")
        body = b"" if result.status_code == 204 else JSONRenderer().render(result.data)
        if len(body) > _MAX_JSON_BYTES:
            raise ImproperlyConfigured("Idempotent responses are limited to 64 KiB.")
        headers = {name: value for name, value in result.headers.items() if name.lower() in _REPLAY_HEADERS}
        record = IdempotencyRecord.objects.create(
            key_digest=key_digest,
            request_digest=request_digest,
            response_body=body.decode("utf-8"),
            response_status=result.status_code,
            response_headers=headers,
            expires_at=timezone.now() + timedelta(seconds=ttl),
        )
        return _response(record.response_body, record.response_status, record.response_headers, replayed=False)
