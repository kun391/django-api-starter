"""Opt-in cache-aside for reviewed, non-sensitive JSON read models.

Django remains the cache backend. This is not a lock, authorization boundary,
full-response cache or durable invalidation mechanism.
"""

import json
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial
from typing import cast
from uuid import uuid4

from django.core.cache import caches
from django.db import connections, transaction
from django.utils.crypto import salted_hmac

type JSONValue = None | bool | int | float | str | list[JSONValue] | dict[str, JSONValue]

logger = logging.getLogger(__name__)
_NAMESPACE = re.compile(r"[a-z][a-z0-9_.-]{0,47}\Z")
_GENERATION = re.compile(r"[0-9a-f]{32}\Z")


@dataclass(frozen=True)
class CachePolicy:
    namespace: str
    version: int = 1
    ttl_seconds: int = 60
    max_bytes: int = 65536
    alias: str = "performance"

    def __post_init__(self):
        if not isinstance(self.namespace, str) or not _NAMESPACE.fullmatch(self.namespace):
            raise ValueError("Use a short, server-defined cache namespace.")
        for name, value, maximum in (
            ("version", self.version, 1_000_000),
            ("ttl_seconds", self.ttl_seconds, 86400),
            ("max_bytes", self.max_bytes, 1_048_576),
        ):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"{name} must be an integer between 1 and {maximum}.")
        if not isinstance(self.alias, str) or not self.alias:
            raise ValueError("A dedicated cache alias is required.")


def _validate_json(value) -> None:
    if type(value) in (type(None), bool, int, float, str):
        return
    if type(value) is list:
        for child in value:
            _validate_json(child)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for child in value.values():
            _validate_json(child)
        return
    raise TypeError("Cache values and identity parts must be plain JSON, with string object keys.")


def _json(value) -> str:
    _validate_json(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value) -> str:
    return cast(str, salted_hmac("core.read-model-cache", _json(value), algorithm="sha256").hexdigest())


def _scope_key(policy: CachePolicy, scope: str, using: str) -> str:
    if not isinstance(scope, str) or not scope or len(scope) > 256:
        raise ValueError("A nonempty server-resolved scope of at most 256 characters is required.")
    return f"read-model:{policy.namespace}:v{policy.version}:{_digest([using, scope])}"


def _generation(backend, scope_key: str) -> str | None:
    generation = backend.get(scope_key)
    if isinstance(generation, str) and _GENERATION.fullmatch(generation):
        return generation
    if generation is not None:
        return None  # Never overwrite corrupt metadata over a concurrent invalidation.
    candidate = uuid4().hex
    # Metadata is bounded too. Expiry/eviction creates a fresh random generation,
    # never reconnecting orphaned values from a previous generation.
    if backend.add(scope_key, candidate, timeout=86400):
        return candidate
    generation = backend.get(scope_key)
    if isinstance(generation, str) and _GENERATION.fullmatch(generation):
        return generation
    return None  # Includes Django's DummyCache.


def _cache_warning(operation: str) -> None:
    # Backend exception strings and keys may contain credentials or identifiers.
    logger.warning("read_model_cache_unavailable: %s", operation)


def _load_json(load: Callable[[], JSONValue]) -> tuple[JSONValue, str]:
    value = load()
    encoded = _json(value)
    # Normalize misses through the same canonical JSON representation used by
    # cache hits. Besides detaching mutable values, this keeps rendered JSON
    # byte-stable across miss -> hit transitions (important for ETags).
    return cast(JSONValue, json.loads(encoded)), encoded


def _reject_constant(value: str):
    raise ValueError("Non-finite JSON numbers are not supported.")


def cache_aside(
    policy: CachePolicy,
    *,
    scope: str,
    key: Mapping[str, JSONValue],
    load: Callable[[], JSONValue],
    using: str = "default",
) -> JSONValue:
    """Cache a JSON DTO, never a Response, QuerySet, model or authentication state.

    Authorize before calling, including on hits. Scope is server-resolved; key
    includes every normalized query/locale/representation dimension. The loader
    must read from `using`. Transactions bypass both cache reads and writes.
    Backend failures fail open; loader/programming errors propagate unchanged.
    """
    scope_key = _scope_key(policy, scope, using)
    key_json = _json(dict(key))
    if len(key_json.encode()) > 4096:
        raise ValueError("Cache identity exceeds 4096 bytes.")
    connection = connections[using]
    if connection.in_atomic_block or not connection.get_autocommit():
        return _load_json(load)[0]

    backend = caches[policy.alias]  # Invalid configuration is a deployment error.
    try:
        generation = _generation(backend, scope_key)
        entry_key = f"{scope_key}:{_digest([generation, key_json])}"
        encoded = backend.get(entry_key) if generation is not None else None
    except Exception:
        _cache_warning("read")
        return _load_json(load)[0]

    if generation is None:
        return _load_json(load)[0]

    if isinstance(encoded, str) and len(encoded.encode()) <= policy.max_bytes:
        try:
            return cast(JSONValue, json.loads(encoded, parse_constant=_reject_constant))
        except (ValueError, TypeError):
            pass  # Corrupt entries are recomputed, not returned to callers.

    value, encoded = _load_json(load)  # Outside cache exception handling; at most once.
    if len(encoded.encode()) <= policy.max_bytes:
        try:
            # Rotation makes old in-flight fills unreachable from the new generation.
            backend.set(entry_key, encoded, timeout=policy.ttl_seconds)
        except Exception:
            _cache_warning("write")
    return value


def _invalidate(policy: CachePolicy, scope_key: str) -> None:
    try:
        caches[policy.alias].set(scope_key, uuid4().hex, timeout=86400)
    except Exception:
        # Never report a committed write as failed just because cache is down.
        # A stored entry expires by TTL, but this is NOT a strict freshness bound.
        _cache_warning("invalidate")


def invalidate_on_commit(
    policy: CachePolicy,
    *,
    scope: str,
    using: str = "default",
) -> None:
    """Rotate all key variants in one namespace/scope after DB commit.

    Register in the SAME transaction/database as the mutation. Rollback discards
    the callback. This is best effort, not durable outbox delivery.
    """
    scope_key = _scope_key(policy, scope, using)
    caches[policy.alias]  # Check configuration before committing a business write.
    transaction.on_commit(partial(_invalidate, policy, scope_key), using=using)
