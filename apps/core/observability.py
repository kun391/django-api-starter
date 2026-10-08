"""Small observability primitives with no runtime vendor dependency."""

from __future__ import annotations

import json
import logging
import re
import time
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from uuid import uuid4

from django.http import HttpRequest, HttpResponse

from apps.core import telemetry

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_SECURITY_LOG_FIELDS = (
    "security_event",
    "outcome",
    "actor_id",
    "subject_id",
    "throttle_scope",
)
_OPERATIONAL_LOG_FIELDS = (
    "operation",
    "queue_name",
    "processed_count",
)

_operational_logger = logging.getLogger("operations.background")


def log_batch_completed(*, queue: str, processed_count: int) -> None:
    telemetry.counter_add(
        "app.background.batch.processed",
        processed_count,
        attributes={"queue": queue},
    )
    _operational_logger.info(
        "background.batch.completed",
        extra={
            "operation": "background.batch.completed",
            "queue_name": queue,
            "processed_count": processed_count,
        },
    )


def get_request_id() -> str | None:
    return _request_id.get()


def _normalize_request_id(value: str | None) -> str:
    if value and _REQUEST_ID_RE.fullmatch(value):
        return value
    return uuid4().hex


class RequestIdMiddleware:
    """Attach a validated request ID to logs and the response."""

    header_name = "HTTP_X_REQUEST_ID"
    response_header = "X-Request-ID"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request_id = _normalize_request_id(request.META.get(self.header_name))
        token: Token[str | None] = _request_id.set(request_id)
        request.request_id = request_id

        try:
            response = self.get_response(request)
            response[self.response_header] = request_id
            return response
        finally:
            _request_id.reset(token)


class TelemetryMiddleware:
    """Trace HTTP requests and emit low-cardinality server metrics."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if not telemetry.enabled():
            return self.get_response(request)

        carrier = {
            key[5:].replace("_", "-").lower(): str(value)
            for key, value in request.META.items()
            if key.startswith("HTTP_") and isinstance(value, str)
        }
        method = request.method.upper()
        started = time.perf_counter()
        status_code = 500
        route = "unmatched"
        with telemetry.span(
            f"HTTP {method}",
            carrier=carrier,
            kind="server",
            attributes={
                "http.request.method": method,
                "app.request_id": getattr(request, "request_id", ""),
            },
        ) as current:
            try:
                response = self.get_response(request)
                status_code = response.status_code
                resolver_match = getattr(request, "resolver_match", None)
                if resolver_match is not None and resolver_match.route:
                    route = str(resolver_match.route)
                if current is not None:
                    current.update_name(f"{method} {route}")
                    current.set_attribute("http.route", route)
                    current.set_attribute("http.response.status_code", status_code)
                return response
            except Exception as exc:
                if current is not None:
                    current.record_exception(exc)
                    current.set_attribute("http.response.status_code", 500)
                raise
            finally:
                attributes = {
                    "http.request.method": method,
                    "http.route": route,
                    "http.response.status_code": status_code,
                }
                telemetry.counter_add(
                    "app.http.server.requests",
                    1,
                    attributes=attributes,
                )
                telemetry.histogram_record(
                    "app.http.server.duration",
                    time.perf_counter() - started,
                    unit="s",
                    attributes=attributes,
                )


class JsonFormatter(logging.Formatter):
    """Emit machine-readable logs suitable for container aggregation."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        request_id = get_request_id()
        if request_id:
            payload["request_id"] = request_id

        trace_id, span_id = telemetry.current_trace_ids()
        if trace_id:
            payload["trace_id"] = trace_id
        if span_id:
            payload["span_id"] = span_id

        status_code = getattr(record, "status_code", None)
        if status_code is not None:
            payload["status_code"] = status_code

        for field in (*_SECURITY_LOG_FIELDS, *_OPERATIONAL_LOG_FIELDS):
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False)
