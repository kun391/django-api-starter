# AI Engineering Rules

This file is the source of truth for coding-agent behavior in this repository.
Keep it short. Architectural rationale belongs in `ARCHITECTURE.md`.

## Priorities

1. Correctness and security.
2. Preserve architecture boundaries.
3. Follow the existing module convention.
4. Reuse existing project primitives.
5. Prefer Python/Django capabilities and existing dependencies.
6. Make the smallest correct change.
7. Introduce abstractions only when justified.

## Django

Use Django directly where it provides value. Do not wrap Django merely to hide
Django.

Simple CRUD may use the ORM directly.

Extract services when business mutations become meaningful. Use selectors when
query composition deserves a named boundary.

Use ports/adapters only for volatile boundaries such as external APIs, object
storage, payments, identity providers, message brokers, or external
notification providers.

## Changes

Do not invent a second pattern when one already exists.

Keep changes local to the owning module.

Background tasks call application/service code rather than duplicating business
logic.

Behavior changes require tests.

Prefer composition and reuse over generation from scratch.

## API boundaries

Read `docs/api-contract.md` before changing API behavior. Reuse the shared error,
pagination and query conventions; update schema annotations and contract tests.
Idempotency is explicitly opt-in, never global middleware. Review authorization,
stored response data and transaction boundaries before enabling it on an endpoint.

Read `docs/security.md` before changing authentication, authorization, proxy identity,
rate limits or security audit behavior. Never log credentials/tokens and never trust a
client-supplied forwarded address, tenant or role without an explicit trusted boundary.

## Background work

Read `docs/background-jobs.md` before introducing asynchronous work or domain events.
Use on-commit enqueue only for best-effort jobs. Use the transactional outbox when
delivery must commit with the business mutation. Outbox handlers are at-least-once and
must be idempotent; never claim exactly-once delivery.

## Caching and performance

Read `docs/performance.md` before caching or changing query behavior. Cache only
reviewed non-sensitive JSON read models; never auth decisions, ORM objects or
correctness-critical state. Authorize before cache access. Register scoped
invalidation in the mutation's transaction. Keep accounts no-store. Add actual
serialization/query-count tests; do not add speculative indexes or mandatory Redis.

## Private files

Read `docs/storage.md` before changing upload/download behavior. Use server-owned
content policies and immutable object keys. Validate authorization on every access;
never publish a media root or infer access from a UUID. Upload requires autocommit,
not a nested DB/idempotency transaction. Reuse outbox cleanup; do not delete storage
objects in save/delete signals. Signed URLs are bearer grants, not ongoing auth.
Keep S3 optional and distinguish format validation from malware scanning.

## Deployment and release

Read `docs/deployment.md` before changing runtime, release or migration behavior.
Never migrate on replica startup, trust unverified proxy headers, or put runtime
secrets in builds/logs. Test actual production images, not only Django's test client.
Promote approved digests; keep publishing and production deployment explicit.
Use expand/migrate/contract and a reviewed rollback plan; no automatic destructive
reverse migration or restore. Preserve minimal and optional runtime variants.

## Agent context

For module-scoped work, start with:

`python scripts/build_ai_context.py <module>`

Include source files explicitly only when needed. Do not generate persistent
copies of repository context.

For new modules, prefer:

`python scripts/create_module.py <name> --type <preset>`

Then remove any generated layer that the module does not actually need.

## Dependencies

Use `pyproject.toml` and uv for dependency changes.

- Do not add or regenerate `requirements*.txt` files.
- Add broad compatible ranges to `pyproject.toml`.
- Regenerate and commit `uv.lock` with uv.
- CI and production installs must use the committed lockfile.
- Prefer an optional extra when a dependency supports an optional runtime capability.
