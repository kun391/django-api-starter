"""Durable append-only business audit records.

Call from the owning business service inside the same default-database transaction
as the mutation. Metadata is server-defined and intentionally bounded.
"""

import json
import re
from typing import cast

from django.core.exceptions import ImproperlyConfigured
from django.db import connection

from apps.core.models import AuditEvent
from apps.core.observability import get_request_id

_ACTION_RE = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*\Z")
_SUBJECT_RE = re.compile(r"[a-z][a-z0-9_-]{0,79}\Z")
_MAX_METADATA_BYTES = 16 * 1024


def record_audit_event(
    *,
    action: str,
    subject_type: str,
    subject_id,
    actor_id: int | None,
    organization_id=None,
    metadata: dict[str, object] | None = None,
) -> AuditEvent:
    if not connection.in_atomic_block:
        raise ImproperlyConfigured(
            "Durable audit events must be recorded inside the business transaction."
        )
    if not _ACTION_RE.fullmatch(action) or len(action) > 120:
        raise ImproperlyConfigured("Audit action must be a bounded machine identifier.")
    if not _SUBJECT_RE.fullmatch(subject_type):
        raise ImproperlyConfigured("Audit subject_type must be a bounded machine identifier.")

    subject = str(subject_id)
    if not subject or len(subject) > 128:
        raise ImproperlyConfigured("Audit subject_id must be 1-128 characters.")

    payload = metadata or {}
    if not isinstance(payload, dict):
        raise ImproperlyConfigured("Audit metadata must be a JSON object.")
    try:
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ImproperlyConfigured("Audit metadata must contain plain JSON values.") from exc
    if len(encoded.encode("utf-8")) > _MAX_METADATA_BYTES:
        raise ImproperlyConfigured("Audit metadata is limited to 16 KiB.")

    return cast(
        AuditEvent,
        AuditEvent.objects.create(
            action=action,
            subject_type=subject_type,
            subject_id=subject,
            actor_id=actor_id,
            organization_id=organization_id,
            request_id=get_request_id() or "",
            metadata=payload,
        ),
    )
