"""Small observability primitives with no runtime vendor dependency."""

from __future__ import annotations

import json
import logging
import re
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from uuid import uuid4

from django.http import HttpRequest, HttpResponse

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
