"""Cache correctness, transaction boundaries and fault injection on PostgreSQL."""

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from django.core.cache import caches
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, connections, transaction

from apps.accounts.models import User
from apps.core.caching import CachePolicy, _scope_key, cache_aside, invalidate_on_commit
from settings import performance

pytestmark = pytest.mark.django_db(transaction=True)
POLICY = CachePolicy("catalog", ttl_seconds=30)


@pytest.fixture
def cache_backend(settings):
    settings.CACHES = {
        **settings.CACHES,
        "performance": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": uuid4().hex,
            "OPTIONS": {"MAX_ENTRIES": 1000},
        },
    }
    return caches["performance"]


def read(load, *, policy=POLICY, scope="tenant:1", key=None, using="default"):
    return cache_aside(policy, scope=scope, key={} if key is None else key, load=load, using=using)


@pytest.mark.parametrize("value", [None, False, 0, "", [], {}, {"items": [1, "two", None]}])
def test_hits_include_empty_values_and_none(cache_backend, value):
    load = Mock(return_value=value)
    assert read(load) == value
    assert read(load) == value
    load.assert_called_once_with()


def test_cached_values_are_detached_json(cache_backend):
    original = {"items": [1]}
    first = read(lambda: original)
    first["items"].append(2)
    second = read(Mock(side_effect=AssertionError("must hit")))
    assert second == {"items": [1]}
    second["items"].append(3)
    assert read(Mock(side_effect=AssertionError("must hit"))) == {"items": [1]}


def test_key_canonicalization_and_isolation(cache_backend):
    load = Mock(side_effect=lambda: {"load": load.call_count})
    assert read(load, key={"page": 1, "language": "en"}) == {"load": 1}
    assert read(load, key={"language": "en", "page": 1}) == {"load": 1}
    read(load, key={"page": 2, "language": "en"})
    read(load, key={"page": 1, "language": "vi"})
    read(load, scope="tenant:2", key={"page": 1, "language": "en"})
    read(load, policy=replace(POLICY, version=2), key={"page": 1, "language": "en"})
    read(load, policy=replace(POLICY, namespace="other"), key={"page": 1, "language": "en"})
    assert load.call_count == 6
    with patch("apps.core.caching.connections", {"default": connection, "replica": connection}):
        read(load, using="replica", key={"page": 1, "language": "en"})
    assert load.call_count == 7


def test_keys_do_not_expose_scope_or_query(cache_backend):
    read(lambda: {}, scope="tenant:private-identity", key={"search": "private-query@example.com"})
    for key in cache_backend._cache:
        assert "private-identity" not in key
        assert "private-query" not in key
        assert len(key) < 250


def test_expiry_without_sleep(cache_backend, monkeypatch):
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now)
    policy = replace(POLICY, ttl_seconds=1)
    load = Mock(side_effect=[1, 2])
    assert read(load, policy=policy) == 1
    assert read(load, policy=policy) == 1
    now += 2
    assert read(load, policy=policy) == 2


def test_oversized_results_are_returned_but_not_cached(cache_backend):
    load = Mock(return_value="x" * 20)
    policy = replace(POLICY, max_bytes=10)
    assert read(load, policy=policy) == "x" * 20
    assert read(load, policy=policy) == "x" * 20
    assert load.call_count == 2


@pytest.mark.parametrize("value", [(1, 2), {1: "bad"}, {1, 2}, float("nan"), float("inf")])
def test_non_json_values_are_rejected(cache_backend, value):
    with pytest.raises((TypeError, ValueError)):
        read(lambda: value)


def test_models_and_querysets_are_not_cached(cache_backend):
    with pytest.raises(TypeError):
        read(lambda: User(username="not-saved"))
    with pytest.raises(TypeError):
        read(lambda: User.objects.all())


@pytest.mark.parametrize("key", [{"q": "x" * 4097}, {1: "bad"}, {"q": (1, 2)}])
def test_invalid_identity_fails_before_loader(cache_backend, key):
    load = Mock()
    with pytest.raises((ValueError, TypeError)):
        read(load, key=key)
    load.assert_not_called()


@pytest.mark.parametrize("changes", [
    {"namespace": ""}, {"namespace": "tenant:caller"}, {"namespace": "a" * 49},
    {"version": 0}, {"version": True}, {"ttl_seconds": 0}, {"ttl_seconds": 86401},
    {"max_bytes": 0}, {"max_bytes": 1048577}, {"alias": ""},
])
def test_policy_bounds(changes):
    with pytest.raises(ValueError):
        replace(POLICY, **changes)


@pytest.mark.parametrize("scope", ["", "x" * 257, None])
def test_invalid_scope(cache_backend, scope):
    with pytest.raises(ValueError):
        read(lambda: 1, scope=scope)


def test_dummy_cache_is_a_true_opt_out():
    load = Mock(side_effect=[1, 2])
    assert read(load) == 1
    assert read(load) == 2


@pytest.mark.parametrize("operation", ["get", "add", "set"])
def test_backend_failure_fails_open_without_logging_secrets(cache_backend, caplog, operation):
    error = RuntimeError("redis://secret-password@private-host/1")
    load = Mock(return_value={"ok": True})
    with patch.object(cache_backend, operation, side_effect=error):
        assert read(load) == {"ok": True}
    load.assert_called_once_with()
    assert "read_model_cache_unavailable" in caplog.text
    assert "secret-password" not in caplog.text
    assert "private-host" not in caplog.text


def test_loader_error_propagates_and_is_not_negative_cached(cache_backend):
    load = Mock(side_effect=ValueError("business failure"))
    with pytest.raises(ValueError, match="business failure"):
        read(load)
    load.assert_called_once_with()
    assert read(lambda: 2) == 2


@pytest.mark.parametrize("encoded", ["not json", "NaN", "[Infinity]", b"bytes-not-text"])
def test_corrupt_entries_are_recomputed(cache_backend, encoded):
    with patch.object(cache_backend, "set", wraps=cache_backend.set) as write:
        read(lambda: 1)
    entry_key = write.call_args.args[0]
    cache_backend.set(entry_key, encoded)
    load = Mock(return_value=2)
    assert read(load) == 2
    load.assert_called_once_with()


def test_atomic_reads_bypass_both_cache_reads_and_writes(cache_backend):
    assert read(lambda: 1) == 1
    with transaction.atomic():
        assert read(lambda: 2) == 2
        assert read(lambda: 3) == 3
    assert read(Mock(side_effect=AssertionError("existing entry should survive"))) == 1


def test_manual_transaction_also_bypasses(cache_backend):
    assert read(lambda: 1) == 1
    transaction.set_autocommit(False)
    try:
        assert read(lambda: 2) == 2
    finally:
        transaction.rollback()
        transaction.set_autocommit(True)
    assert read(Mock(side_effect=AssertionError("existing entry should survive"))) == 1


def test_invalidation_occurs_after_commit_and_only_in_own_scope(cache_backend):
    read(lambda: 1)
    read(lambda: 10, key={"page": 2})
    read(lambda: 20, scope="tenant:2")
    scope_key = _scope_key(POLICY, "tenant:1", "default")
    generation = cache_backend.get(scope_key)
    with transaction.atomic():
        invalidate_on_commit(POLICY, scope="tenant:1")
        assert cache_backend.get(scope_key) == generation
    assert cache_backend.get(scope_key) != generation
    assert read(lambda: 2) == 2
    assert read(lambda: 11, key={"page": 2}) == 11
    assert read(Mock(side_effect=AssertionError("unrelated scope")), scope="tenant:2") == 20


def test_rollback_discards_invalidation_and_never_publishes_uncommitted_reads(cache_backend):
    read(lambda: 1)
    with pytest.raises(RuntimeError), transaction.atomic():
        invalidate_on_commit(POLICY, scope="tenant:1")
        assert read(lambda: 99) == 99
        raise RuntimeError("rollback")
    assert read(Mock(side_effect=AssertionError("must retain committed data"))) == 1


def test_rolled_back_savepoint_discards_its_callback(cache_backend):
    read(lambda: 1)
    with transaction.atomic():
        try:
            with transaction.atomic():
                invalidate_on_commit(POLICY, scope="tenant:1")
                raise RuntimeError("savepoint rollback")
        except RuntimeError:
            pass
    assert read(Mock(side_effect=AssertionError("savepoint callback must be discarded"))) == 1


def test_invalidation_failure_does_not_reverse_committed_business_write(cache_backend, caplog):
    with patch.object(cache_backend, "set", side_effect=RuntimeError("private-cache-url")):
        with transaction.atomic():
            user = User.objects.create(username="committed")
            invalidate_on_commit(POLICY, scope="tenant:1")
    assert User.objects.filter(pk=user.pk).exists()
    assert "invalidate" in caplog.text
    assert "private-cache-url" not in caplog.text


def test_late_fill_cannot_repopulate_invalidated_generation(cache_backend):
    def old_snapshot():
        invalidate_on_commit(POLICY, scope="tenant:1")
        return "old"

    assert read(old_snapshot) == "old"  # In-flight caller is allowed its old snapshot.
    assert read(lambda: "new") == "new"
    assert read(Mock(side_effect=AssertionError("must hit new generation"))) == "new"


def test_concurrent_invalidation_is_safe_across_connections(cache_backend):
    started, release = Event(), Event()

    def old_snapshot():
        started.set()
        assert release.wait(timeout=10)
        return "old"

    def worker():
        try:
            return read(old_snapshot)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(worker)
        try:
            assert started.wait(timeout=10)
            invalidate_on_commit(POLICY, scope="tenant:1")
            assert read(lambda: "new") == "new"
        finally:
            release.set()
        assert future.result(timeout=10) == "old"
    assert read(Mock(side_effect=AssertionError("late fill must be isolated"))) == "new"


def test_generation_eviction_does_not_reconnect_old_values(cache_backend):
    read(lambda: "old")
    cache_backend.delete(_scope_key(POLICY, "tenant:1", "default"))
    assert read(lambda: "new") == "new"


def test_configuration_has_safe_defaults(monkeypatch):
    monkeypatch.setattr(performance, "config", lambda name, default: default)
    config = performance.build_performance_caches()
    assert config["performance"]["BACKEND"] == performance.DUMMY_BACKEND
    assert config["default"]["BACKEND"] == performance.LOCAL_BACKEND


def test_redis_configuration_is_explicit_and_bounded(monkeypatch):
    values = {
        "PERFORMANCE_CACHE_BACKEND": performance.REDIS_BACKEND,
        "PERFORMANCE_CACHE_LOCATION": "rediss://cache.example:6379/1",
    }
    monkeypatch.setattr(performance, "config", lambda name, default: values.get(name, default))
    config = performance.build_performance_caches()["performance"]
    assert config["OPTIONS"]["socket_connect_timeout"] == 0.5
    assert config["OPTIONS"]["socket_timeout"] == 0.5
    values["PERFORMANCE_CACHE_LOCATION"] = "missing-url-scheme"
    with pytest.raises(ImproperlyConfigured):
        performance.build_performance_caches()
    values["PERFORMANCE_CACHE_BACKEND"] = "typo.backend"
    with pytest.raises(ImproperlyConfigured):
        performance.build_performance_caches()
