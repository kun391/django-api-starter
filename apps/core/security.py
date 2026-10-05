"""Security audit primitives with an intentionally small, non-PII payload."""

from __future__ import annotations

import logging

logger = logging.getLogger("security")


def audit_security_event(
    event: str,
    *,
    outcome: str,
    actor_id: int | None = None,
    subject_id: int | None = None,
    throttle_scope: str | None = None,
) -> None:
    extra: dict[str, object] = {
        "security_event": event,
        "outcome": outcome,
    }
    if actor_id is not None:
        extra["actor_id"] = actor_id
    if subject_id is not None:
        extra["subject_id"] = subject_id
    if throttle_scope is not None:
        extra["throttle_scope"] = throttle_scope
    logger.info("security_event", extra=extra)
