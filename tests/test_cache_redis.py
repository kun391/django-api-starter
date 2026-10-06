"""Optional real-Redis integration; no Redis dependency in the minimal test job."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from django.core.cache import caches
from django.core.cache.backends.redis import RedisCache
from django.db import connections, transaction

from apps.core.caching import CachePolicy, cache_aside, invalidate_on_commit

REDIS_URL = os.environ.get("TEST_REDIS_URL", "")
POLICY = CachePolicy("redis-integration", ttl_seconds=30)
pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(not REDIS_URL, reason="Set TEST_REDIS_URL to a disposable Redis database."),
]


@pytest.fixture
def redis_cache(settings):
    pytest.importorskip("redis")
    settings.CACHES = {
        **settings.CACHES,
        "performance": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
            "KEY_PREFIX": f"test-{uuid4().hex}",
            "OPTIONS": {"socket_connect_timeout": 0.5, "socket_timeout": 0.5},
        },
    }
    # A unique prefix isolates each test; never FLUSHDB/clear a supplied server.
    return caches["performance"]


def read(load):
    return cache_aside(POLICY, scope="tenant:1", key={"page": 1}, load=load)


def test_redis_shares_hits_and_invalidation_across_clients(redis_cache, settings):
    assert read(lambda: {"revision": 1}) == {"revision": 1}
    other_client = RedisCache(REDIS_URL, settings.CACHES["performance"])
    with patch("apps.core.caching.caches", {"performance": other_client}):
        assert read(Mock(side_effect=AssertionError("must share the cache"))) == {"revision": 1}
        with transaction.atomic():
            invalidate_on_commit(POLICY, scope="tenant:1")
    assert read(lambda: {"revision": 2}) == {"revision": 2}


def test_redis_value_ttl_and_expiration(redis_cache):
    with patch.object(redis_cache, "set", wraps=redis_cache.set) as write:
        assert read(lambda: 1) == 1
    key = redis_cache.make_key(write.call_args.args[0])
    redis_module = pytest.importorskip("redis")
    client = redis_module.Redis.from_url(REDIS_URL, socket_timeout=0.5)
    try:
        assert 0 < client.ttl(key) <= POLICY.ttl_seconds
        client.expire(key, 0)  # Server-side expiry, no timing-sensitive sleep.
        assert read(lambda: 2) == 2
    finally:
        client.close()


def test_redis_inflight_fill_cannot_undo_commit_invalidation(redis_cache):
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
            with transaction.atomic():
                invalidate_on_commit(POLICY, scope="tenant:1")
            assert read(lambda: "new") == "new"
        finally:
            release.set()
        assert future.result(timeout=10) == "old"
    assert read(Mock(side_effect=AssertionError("must hit the new generation"))) == "new"
