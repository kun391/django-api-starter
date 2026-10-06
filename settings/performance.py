"""Optional application cache; security controls never use this alias."""

from decouple import config  # type: ignore[import-untyped]
from django.core.exceptions import ImproperlyConfigured

DUMMY_BACKEND = "django.core.cache.backends.dummy.DummyCache"
LOCAL_BACKEND = "django.core.cache.backends.locmem.LocMemCache"
REDIS_BACKEND = "django.core.cache.backends.redis.RedisCache"


def build_performance_caches() -> dict[str, dict]:
    backend = config("PERFORMANCE_CACHE_BACKEND", default=DUMMY_BACKEND)
    location = config("PERFORMANCE_CACHE_LOCATION", default="read-models")
    if backend not in {DUMMY_BACKEND, LOCAL_BACKEND, REDIS_BACKEND}:
        raise ImproperlyConfigured("Unsupported PERFORMANCE_CACHE_BACKEND.")
    if backend == REDIS_BACKEND and not location.startswith(("redis://", "rediss://")):
        raise ImproperlyConfigured("Redis requires an explicit PERFORMANCE_CACHE_LOCATION URL.")
    performance = {
        "BACKEND": backend,
        "LOCATION": location,
        "KEY_PREFIX": config("PERFORMANCE_CACHE_KEY_PREFIX", default="django-api-starter"),
        "TIMEOUT": 60,
    }
    if backend == REDIS_BACKEND:
        performance["OPTIONS"] = {
            "socket_connect_timeout": 0.5,
            "socket_timeout": 0.5,
            "retry_on_timeout": False,
        }
    return {
        "default": {"BACKEND": LOCAL_BACKEND},
        "performance": performance,
    }
