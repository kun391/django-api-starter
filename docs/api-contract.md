# API contract (Phase 7)

## Scope and compatibility

The client API is under `/api/v1/`, with trailing slashes. Keep breaking resource
changes behind an explicit version decision. OpenAPI is generated from code at
`/api/schema/`; interactive documentation remains at `/api/docs/` and `/api/redoc/`.
Operational `/health/*` probes are intentionally outside the client schema.

This phase changes the error body from DRF's field/dict shape to Problem Details.
Clients must migrate field lookups to `errors[].attr`, `errors[].code` and
`errors[].detail`. Unauthenticated requests now consistently return 401 because
TokenAuthentication precedes SessionAuthentication; authenticated forbidden
requests remain 403. Session authentication and its CSRF enforcement remain.
Success resource responses are plain JSON, not a new `data` envelope. Account
create/update responses consistently use the full, password-free User serializer.
Lists retain DRF's `{count, next, previous, results}` envelope. DELETE remains 204.

Use `Accept: application/json`. The browsable API renderer is disabled; Swagger
and ReDoc are still available. Existing form parsers are retained for compatibility.
Only explicitly idempotent operations require JSON input.

## Error responses

Errors use `Content-Type: application/problem+json`, following RFC 9457:

```json
{
  "type": "about:blank",
  "title": "Bad Request",
  "status": 400,
  "code": "validation_error",
  "detail": "Request validation failed.",
  "errors": [{"attr": "items.0.email", "code": "invalid", "detail": "Enter a valid email address."}],
  "request_id": "client-attempt-01"
}
```

`type`/`title` describe the HTTP status; `code`, `errors`, and `request_id` are
project extensions. Client logic uses status and stable codes, never English
messages. `errors` is empty for non-validation failures. Nested field paths use
dots and zero-based indices; object-level errors use a null attribute at the
root or the containing object's path. Request IDs are correlation hints, not
authentication or idempotency keys.

Raise DRF exceptions for expected API failures. Use
`serializer.is_valid(raise_exception=True)`. Translate domain/Django validation
at the owning module boundary; do not turn arbitrary IntegrityError or programmer
errors into client mistakes. Do not return ad-hoc `Response(..., status=400)`:
DRF exception handlers do not process such responses.

The handler preserves `WWW-Authenticate`, `Retry-After`, and `Allow`. Invalid
JSON is 400, unsupported media is 415, and negotiation failure is 406.
Unexpected exceptions are re-raised into Django's normal rollback, logging and
exception-reporting pipeline. With `DEBUG=False`, Django's API error handlers
return a generic 500 body, without the exception text or traceback. Unmatched API
routes also return a JSON problem. Non-API HTML/admin error pages remain native.
Django debug pages may still appear for unexpected errors with `DEBUG=True`.
Errors and authentication token responses use `Cache-Control: no-store`.

## Pagination, filtering and ordering

The default page is 1, page size 20, maximum page size 100. `page` and `page_size`
must each be a single positive integer (up to 10 digits). Bad values, `last`,
repeated parameters, and sizes over the limit return 400 rather than silently
falling back or clamping. A valid but nonexistent page returns 404; an empty
first page returns a normal empty envelope.

Every list declares an explicit FilterSet/field allowlist, search_fields and
ordering_fields. Arbitrary ORM lookup strings and `ordering_fields='__all__'`
are not supported. Unknown or repeated query parameters return 400. Custom
views may declare `extra_query_params` deliberately; array-valued query inputs
need their own explicit validation convention rather than bypassing this guard.

The accounts administration list supports exact `username`, `email` and
`is_active=true|false` filters, `search` across username/email/first/last name,
and comma-separated `ordering` over id/username/email/date_joined. A leading
minus means descending. Invalid boolean and ordering values return 400.
The primary key is appended as a deterministic tie-breaker when absent.

```text
GET /api/v1/users/?page=2&page_size=20&is_active=true&search=alice&ordering=-date_joined
```

These filters never grant access: staff-only permissions still run first.
Stable ordering is not a snapshot across concurrent inserts/deletes; choose a
cursor strategy explicitly for high-churn or very large lists later.

## Idempotency foundation: opt-in, not global middleware

No existing account registration, login/token, or profile endpoint has replay
enabled. Do not store authentication tokens, passwords, secrets or sensitive
response bodies in the replay table. Response JSON is stored as plaintext in
the application database, so assess data classification before opting in.

`apps.core.api.idempotency.idempotent_post` supports short, authenticated JSON
POST operations against the default PostgreSQL database. The caller performs
all authentication, tenant and object-level authorization **before** invoking
it, including on retries. Scope is a server-defined operation/version boundary;
include the server-resolved tenant when the application introduces tenancy.
Never trust a caller-supplied scope or tenant header as authorization.

The header is required only on opted-in endpoints: 1-128 ASCII letters/digits or
`.`, `_`, `:`, `-`. Generate a high-entropy key for each logical operation and
reuse it only when retrying that operation. This is the project's header
convention, not a claim of conformance to an evolving Idempotency-Key draft.

| Situation | Result |
| --- | --- |
| First successful request | Execute once; persist response and database writes together |
| Same scoped key and same fingerprint | Replay original status, JSON bytes, Location and ETag |
| Same scoped key with changed payload/query | 422 `idempotency_key_reused` |
| Duplicate while the original transaction is running | 409 `idempotency_in_progress`, Retry-After: 1 |
| Missing/invalid key or oversized canonical input | 400, before mutation |
| Operation raises a validation/unexpected exception | Roll back both writes and replay record; permit a retry |
| Key expired | Treat the next request as a new operation |

Key identity includes authenticated user PK, server scope, HTTP method, path and
caller key. Fingerprints use canonical JSON (object key order is ignored) and the
raw query string (including its ordering). HMAC digests are stored, not raw keys
or request bodies. Identity is tied to SECRET_KEY: rotating it invalidates lookup
continuity. Coordinate rotation with the replay window or provide a deliberately
versioned key strategy before relying on retries across rotation.

PostgreSQL transaction-level advisory locks provide cross-worker exclusion.
Business writes and the replay record share one durable default-database
transaction; there is no committed 'pending' record to strand after a crash.
Do not call this helper inside ATOMIC_REQUESTS or another atomic block: it
intentionally rejects nesting. Business writes to other database aliases are not
covered. The callback returns a successful DRF Response and raises exceptions
for failures. JSON input and stored responses are each limited to 64 KiB.
Responses with cookies or unapproved headers fail closed and roll back; Location
and ETag are the replay-safe application header allowlist. 204 bodies stay empty.
`Idempotency-Replayed: true|false` describes replay; each HTTP attempt receives
its own current `X-Request-ID` through the normal middleware.

Do not make network calls, charge a payment provider, send email, publish to a
broker, or rely on an on_commit callback for durable delivery inside this
operation. This is not distributed exactly-once processing. Persist an outbox
record in the same database transaction for external work; outbox delivery and
provider-level idempotency are separate, future capabilities.

Integration pattern for a future module (Order serializers/service below are
illustrative, not included endpoints):

```python
from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from apps.core.api.idempotency import idempotent_post
from apps.core.api.schema import IDEMPOTENCY_KEY_PARAMETER

@extend_schema(
    request=OrderCreateSerializer,
    responses={201: OrderSerializer},
    parameters=[IDEMPOTENCY_KEY_PARAMETER],
)
def post(self, request):
    # Normal DRF auth/permissions and any object/tenant checks run first.
    def operation():
        serializer = OrderCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = create_order(**serializer.validated_data)
        return Response(OrderSerializer(order).data, status=201)

    return idempotent_post(request, operation, scope="orders.create:v1")
```

The shared schema hook recognizes that explicit header annotation and documents
409/422 and replay headers; it never adds the header to unrelated endpoints.

## Retention and rollout

Apply `uv run python manage.py migrate` to create the core replay table.
`IDEMPOTENCY_TTL_SECONDS` defaults to 86400 (24 hours after successful completion).
The TTL limits the retry guarantee, not just cleanup. After expiry, a repeated
business command can execute again; enforce business uniqueness separately.

Schedule this bounded cleanup command with the deployment scheduler:

```bash
uv run python manage.py purge_idempotency --batch-size 1000
```

Each invocation deletes at most one batch (allowed size 1-10000). Frequency and
batch size must match traffic and retention requirements. No Redis, worker or
Celery dependency is introduced for this capability.

## Verification

```bash
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py spectacular --file /tmp/schema.yaml --validate --fail-on-warn
uv run pytest tests/test_api_contract.py tests/test_idempotency.py apps/accounts/tests/
```

CI runs schema validation plus the full test suite against PostgreSQL. Tests
cover HTTP/media types, request IDs, permissions and CSRF, query conventions,
replay/isolation/expiry, rollback on operation and persistence failures, cleanup,
and concurrent duplicates on separate real PostgreSQL connections. There is no
schema compatibility diff gate yet: validation is not a substitute for reviewing
client-breaking changes.

References: RFC 9457 (https://www.rfc-editor.org/rfc/rfc9457.html), DRF exception
handling (https://www.django-rest-framework.org/api-guide/exceptions/), PostgreSQL
advisory locking (https://www.postgresql.org/docs/17/functions-admin.html#FUNCTIONS-ADVISORY-LOCKS).


## Optimistic concurrency (Phase 15)

Write concurrency is explicit and opt-in. Resource modules that advertise the
`If-Match` header must issue a strong resource ETag and validate the precondition
inside the same database transaction that locks and mutates the row.

The reference `Ticket` resource returns a strong ETag from create, detail GET and
successful PATCH. Clients PATCH with the latest value:

```text
GET /api/v1/tickets/{id}/
ETag: "..."

PATCH /api/v1/tickets/{id}/
If-Match: "..."
```

Missing `If-Match` returns 428 `precondition_required`. A stale, malformed or
weak validator returns 412 `precondition_failed`. Authorization/object scoping is
resolved before validator comparison so a stale token never reveals a resource the
caller cannot access.

The validator is derived from server-owned resource identity plus a monotonically
increasing row revision. It is not derived from timestamps and it is not the weak
representation ETag used by generic conditional list/read responses. Business
changes that alter the ticket representation, including attachment links, advance
the revision.

`If-Match: *` is accepted only after normal authorization and locked resource
existence are established. It means "mutate the current authorized resource
regardless of its revision"; clients that need lost-update protection should use
the exact strong ETag instead.

Do not validate optimistic concurrency in middleware before authorization, from a
cache, or against an unlocked ORM object. Do not reuse weak ETags as write tokens.
