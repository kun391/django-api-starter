# Caching and performance foundation (Phase 10)

## Scope and defaults

This phase supplies opt-in read-model caching, commit-aware invalidation,
conditional GET/HEAD, and SQL regression budgets. It does not turn caching on
for every endpoint, add a mandatory service, or claim a measured latency gain.

The existing accounts API contains personal data, not a suitable public cache
demo. User/profile/admin responses now explicitly send `Cache-Control: no-store`.
Token and error responses keep their existing no-store contract. No accounts
endpoint gains ETags, 304 responses or server-side caching. Tests exercise the
new HTTP primitives on test-only routes; no fictional catalog is deployed.

Do not cache authentication/authorization decisions, tokens, passwords, sessions,
PII, payment/stock balances, idempotency records, outbox state, ORM objects,
QuerySets, HTTP Responses or correctness-critical reads. Throttles and
idempotency remain PostgreSQL-backed, independent of the performance cache.

## Configure a dedicated Django cache

`CACHES['performance']` is separate from Django's default cache. The environment
configuration supports these native backends:

| Backend | Use |
| --- | --- |
| `django.core.cache.backends.dummy.DummyCache` | Default in all environments; load directly, no cache storage |
| `django.core.cache.backends.locmem.LocMemCache` | Single-process local development only |
| `django.core.cache.backends.redis.RedisCache` | Explicit shared deployment for multiple processes |

LocMem invalidation does not cross processes. Never treat a LocMem development
result as proof of multi-worker correctness. The native Redis backend requires
the Redis client already present in the optional `async` extra; no new dependency
or lockfile update is introduced by this phase.

```bash
uv sync --locked --extra async --group dev
# Provision Redis independently, or start only Redis from the optional profile:
docker compose --profile async up -d redis
```

```text
PERFORMANCE_CACHE_BACKEND=django.core.cache.backends.redis.RedisCache
PERFORMANCE_CACHE_LOCATION=redis://redis:6379/1
PERFORMANCE_CACHE_KEY_PREFIX=my-service-production
```

For commands under uv, retain `--extra async` (for example,
`uv run --extra async python manage.py runserver`). Use the existing
`runtime-async` image when deploying the Redis-enabled configuration. Installing
this extra does not require running a Celery worker or RabbitMQ for caching.
The default `runtime` image and web + PostgreSQL stack remain unchanged.

Use a distinct deployment prefix and cache database, not Celery's result database.
Logical Redis databases/prefixes are collision isolation, not a security boundary.
Use a trusted private deployment with authentication/TLS/network ACLs and bounded
memory/eviction policy. Django's native backend serializes values internally;
storing JSON at the application boundary does not make an untrusted Redis safe.
Do not point this cache at read replicas: generation and value reads must observe
the same primary. The configuration supplies 0.5-second connect/socket timeouts
and disables timeout retries. Measure total failure latency with your client and
infrastructure; these settings are not a hard end-to-end request deadline.

`settings.test` does not inherit a developer's cache connection. Integration tests
explicitly override the dedicated alias using a disposable Redis URL and a unique
prefix; they never clear/FLUSHDB a supplied server.

## Cache-aside contract

Use `CachePolicy`, `cache_aside` and `invalidate_on_commit` from
`apps.core.caching`. Policies are server-defined, immutable, local to the owning
business module. Values must be plain JSON types with string object keys and
finite numbers. Convert dates/decimals deliberately at the owning boundary.

```python
from django.db import transaction
from apps.core.caching import CachePolicy, cache_aside, invalidate_on_commit

CATALOG = CachePolicy("catalog.summary", version=1, ttl_seconds=60)

# Illustrative Product/service boundary; Product is NOT included in this starter.
def catalog_summary(*, authorized_tenant_id, language):
    # Authenticate and authorize before calling, on hits as well as misses.
    scope = f"tenant:{authorized_tenant_id}"
    return cache_aside(
        CATALOG,
        scope=scope,
        key={"language": language},
        load=lambda: {
            "items": list(Product.objects.using("default").filter(
                tenant_id=authorized_tenant_id, published=True,
            ).order_by("id").values("id", "label"))
        },
    )


def rename_product(*, authorized_tenant_id, product_id, label):
    with transaction.atomic(using="default"):
        Product.objects.using("default").filter(
            tenant_id=authorized_tenant_id, pk=product_id,
        ).update(label=label)
        invalidate_on_commit(CATALOG, scope=f"tenant:{authorized_tenant_id}")
```

The example requires the caller's real tenant/object authorization, validation
and bounded query policy. Do not accept a caller-provided tenant/scope as proof
of access. A scope should represent a bounded server-owned invalidation domain,
not a new namespace for every arbitrary query.

Key identity includes namespace, schema version, database alias, HMAC-digested
scope, current random generation, and canonical JSON identity. Include every
normalized filter, ordering, page/page-size, language, feature/representation
variant and user dimension that changes the reviewed result. Authorization must
not be inferred from the key. Namespace/version remain readable; raw scope/query
identifiers are not stored in keys. Secret-key rotation produces misses rather
than continuity with old keys. Use a version bump for DTO/schema changes.

Defaults/limits:

- TTL: 60 seconds; explicit policies allow 1 through 86400 seconds.
- Stored value: 64 KiB by default, at most 1 MiB per policy. Larger valid values
  are returned without storing them. The limit is not a loader memory limit.
- Identity: at most 4096 UTF-8 bytes; server-resolved scope at most 256 characters.
- Generation metadata expires after 24 hours; old value entries expire by TTL.

Empty lists/dicts, false, zero and null are real cache hits. A hit returns newly
decoded JSON, not a shared mutable Python object. Malformed cached JSON is
recomputed. Invalid policy/alias/identity/DTO and loader errors propagate; only
backend operation failures fail open. Each call executes its loader at most
once. Error logs contain an operation, never exception text, credentials or keys.
Route `read_model_cache_unavailable` warnings into the existing log monitoring;
alert on sustained failures and test database capacity with caching disabled.

## Transaction and invalidation semantics

Register `invalidate_on_commit` inside the same transaction and with the same
`using` alias as the mutation. At autocommit it executes immediately. Successful
commit rotates the scope generation; rollback and rolled-back savepoints discard
the callback. Never call global `cache.clear()` for normal business invalidation.
All query/page variants under the scope rotate together.

Reads inside an atomic block, ATOMIC_REQUESTS, or manually disabled autocommit
bypass both cache reads and writes. This avoids publishing an uncommitted
snapshot and avoids reading stale cache instead of a transaction's own writes.
The loader must actually query the database named by `using`. Cross-database
business transactions and read-replica consistency are not supplied here.

A reader that began with generation A may finish after a writer commits and
rotates to B. Its fill goes into A, so new B readers cannot discover that fill.
Generation eviction also creates a new random generation instead of reusing an
old counter value. An already-running reader may still return its old snapshot.

This is best-effort cache invalidation, not linearizable reads or durable event
delivery. A process can die between database commit and its callback. A backend
outage can prevent invalidation, and a recovered backend can retain an old
entry. TTL bounds an entry's lifetime after publication, not a strict freshness
bound measured from the business commit. Do not use this primitive where stale
reads can violate correctness or authorization. Use authoritative reads or an
explicit durable version/outbox design for those cases.

There is deliberately no distributed lock or stampede suppression. Concurrent
misses can compute the same result. Make loaders side-effect-free and bounded;
measure capacity before enabling caching on an expensive workload. Never use
this cache as a mutex or to claim exactly-once execution.

## Conditional GET/HEAD contract

Opt in with `ConditionalGetMixin` before the DRF view base, and annotate the
operation with `IF_NONE_MATCH_PARAMETER` from `apps.core.api.conditional`:

```python
class CatalogView(ConditionalGetMixin, generics.ListAPIView):
    # Real queryset, serializer and permission policy belong to the module.
    @extend_schema(parameters=[IF_NONE_MATCH_PARAMETER])
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)
```

Authentication, permissions, object checks, query validation and the handler
execute before conditional response handling, including on a matching validator.
The mixin hashes rendered JSON bytes into a weak ETag. This saves transfer bytes;
it does NOT skip queries, serialization or rendering. Cache-aside is a separate
opt-in decision.

Successful JSON GET/HEAD responses receive:

```text
ETag: W/"<representation-digest>"
Cache-Control: private, no-cache, must-revalidate
Vary: Authorization, Cookie, Accept, Accept-Language
```

A matching `If-None-Match` (weak/strong list member or wildcard) produces an empty
304 with ETag, cache policy, Vary and the current attempt's `X-Request-ID`. A
changed/malformed/unmatched validator returns 200. HEAD has no body and retains
the representation validator and GET content length. Django handles precondition
ordering; failed read `If-Match` preconditions retain the Phase 7 412 Problem
Details body (`precondition_failed`). Weak ETags are not write-concurrency tokens. Phase 15 adds a separate opt-in
strong resource ETag / If-Match contract for writes; see docs/api-contract.md.

Errors, non-200 responses, writes, streams, non-JSON content, responses with
cookies, and responses with an existing Cache-Control or ETag are untouched.
Existing policies belong to the view and must implement their own semantics.
No global conditional/cache middleware is enabled. A future downstream layer
which changes response data or cache policy must be reviewed with this contract.

Private/no-cache permits browser storage but requires revalidation. It is not
no-store: never opt sensitive representations into this mixin. Accounts remain
no-store. CORS permits If-None-Match/If-Match and exposes ETag. OpenAPI advertises
304/412 only for explicitly annotated GET/HEAD operations; 304 has no body schema.

## Query guardrails

Optimize the measured query shape before adding a cache. Keep query composition
in the owning module, using a selector only when it earns a named boundary.
The current User serializer has no related-object fields, so adding speculative
select_related/prefetch_related would do no useful work.

The reference endpoint tests set these SQL budgets:

| Request | View SQL with forced authentication |
| --- | --- |
| User list at page sizes 1, 20 and 100 | 2: one COUNT + one bounded SELECT |
| Filtered/ordered second page | 2 |
| User detail | 1 |
| Self profile | 0; use the already authenticated user |

A real token-authenticated list additionally tests a budget of 3 and profile 1,
including the joined token/user lookup. These are regression budgets, not a
production latency benchmark or an assurance that two arbitrarily costly queries
are fast. Fixture setup is outside the measured block. Assertions include actual
JSON serialization so a future serializer N+1 fails the test.

When adding relations, use `select_related` for FK/one-to-one access and
`prefetch_related`/`Prefetch` for multi-valued relations. Test the actual serializer
at small and large page sizes: related query counts should be constant per
prefetched relationship, not grow per row. A filtered related-manager lookup may
not reuse an unfiltered prefetch. Avoid `only`/`defer` on fields the serializer
will subsequently access; that can introduce deferred-field queries.

Retain strict pagination and stable ordering from Phase 7. Do not fetch an entire
QuerySet into a list before slicing. Avoid duplicate counts and avoid an `exists`
call immediately before evaluating the same rows when it adds no value. For
large/high-churn datasets, choose cursor pagination and count semantics as an
explicit API contract change rather than silently removing `count`.

Review actual filter/order workload and query plans before adding indexes.
Use QuerySet.explain and safe staging EXPLAIN (ANALYZE, BUFFERS) with representative
data; ANALYZE executes the query. Consider composite ordering/tie-breakers,
selectivity, write cost and concurrent index rollout. There is no speculative
index or migration in this phase.

For background/export work use bounded batches and iterator(chunk_size=...) when
appropriate; do not expose unbounded synchronous exports through the API.
Use bulk_create/bulk_update/update for truly set-based mutations, remembering
that bulk paths can bypass save hooks/signals. Their owning service must register
invalidation explicitly. Keep transaction/lock duration short; never cache
select_for_update results or use cached values for mutation correctness.

## Verification and rollout

```bash
uv lock --check
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py spectacular --file /tmp/schema.yaml --validate --fail-on-warn
uv run ruff check .
uv run mypy .
uv run python scripts/check_architecture.py
uv run pytest

# With disposable PostgreSQL and Redis available:
TEST_REDIS_URL=redis://127.0.0.1:6379/15 uv run --extra async pytest \
  tests/test_caching.py tests/test_cache_redis.py
```

CI keeps the minimal PostgreSQL full-suite, dependency-audit, schema, architecture,
Compose and both Docker-image gates. A separate job installs the existing async
extra and exercises real Redis sharing, expiry and concurrent late-fill
invalidation. The minimal job skips only explicit real-Redis integration tests.
Unit/HTTP/query tests still run without Redis.

Roll out one reviewed non-sensitive read model at a time. Record cold/warm query
counts, p50/p95 latency, failure-mode load and stale-data tolerance. Validate every
write path, including bulk/admin/jobs, uses the owning invalidation policy.
Keep the default disabled until those product-specific checks pass. Roll back by
switching the dedicated alias to DummyCache and restarting application processes;
no database migration or global cache deletion is necessary.

References: Django cache framework (https://docs.djangoproject.com/en/6.1/topics/cache/),
transactions/on_commit (https://docs.djangoproject.com/en/6.1/topics/db/transactions/),
conditional views (https://docs.djangoproject.com/en/6.1/topics/conditional-view-processing/),
and database optimization (https://docs.djangoproject.com/en/6.1/topics/db/optimization/).
