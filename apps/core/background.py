"""Commit-aware helpers for best-effort background work."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.db import transaction


def enqueue_after_commit(
    enqueue: Callable[..., Any],
    /,
    *args: Any,
    **kwargs: Any,
) -> None:
    """Enqueue only after a successful DB commit.

    This avoids jobs observing rolled-back state but is not durable: the process may
    crash after commit and before the callback reaches the broker. Use the outbox for
    work that must not be lost.
    """

    transaction.on_commit(lambda: enqueue(*args, **kwargs))
