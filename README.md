# Django API Starter

A Django-first API starter focused on production safety, modularity, reproducible
dependencies, and efficient AI-assisted development.

## Baseline

- Python 3.12-3.14; CI and Docker use Python 3.14
- Django 6.1
- Django REST Framework
- PostgreSQL 17
- uv + committed `uv.lock`
- Ruff, MyPy, pytest
- Docker Compose
- Optional Celery/Redis/RabbitMQ async profile

## Quick start

```bash
cp env.example .env
make dev
make migrate
```

The development API is available at `http://localhost:5001`.

Create an admin user with:

```bash
make createsuperuser
```

## Dependency management

`pyproject.toml` is the dependency source of truth. `uv.lock` contains the
exact resolved dependency graph and is committed to Git.

```bash
uv sync --locked --group dev
uv run pytest
uv run ruff check .
uv run mypy .
```

To upgrade dependencies intentionally:

```bash
uv lock --upgrade
uv sync --group dev
```

Do not add `requirements*.txt` files.

### Optional runtime extras

```bash
# Background jobs / Redis client
uv sync --locked --extra async --group dev

# Object storage integrations
uv sync --locked --extra storage --group dev

# OpenTelemetry traces/metrics
uv sync --locked --extra telemetry --group dev

# Combined capabilities
uv sync --locked --extra async --extra storage --extra telemetry --group dev
```

Available extras:

- `async`: Celery and Redis client
- `storage`: django-storages and boto3
- `telemetry`: OpenTelemetry SDK + OTLP/HTTP exporter

## Docker Compose

Default development stack:

```bash
docker compose up -d
```

This starts only `web + PostgreSQL`.

Enable asynchronous infrastructure only when needed:

```bash
docker compose --profile async up -d
```

The `async` profile adds Redis, RabbitMQ, Celery worker and Celery beat.

## Production images and release

Read [the Phase 12 deployment guide](docs/deployment.md). Production has a
**standalone** `compose.production.yaml`; never combine it with development
Compose. Staging uses the same security contract as production, with independent
runtime secrets and services.

```bash
docker build --target runtime -t django-api-starter .
docker build --target runtime-async -t django-api-starter-async .
docker build --target runtime-storage -t django-api-starter-storage .
docker build --target runtime-telemetry -t django-api-starter-telemetry .
docker build --target runtime-full -t django-api-starter-full .

# Disposable PostgreSQL + real HTTP smoke for all five non-root images:
bash scripts/smoke_production.sh
```

Runtime images contain offline-collected static assets, default to production
settings, and need explicit strong secrets/hosts and verified PostgreSQL TLS.
They contain no uv, compiler, Git, PostgreSQL CLI, debug toolbar or test tooling.
`runtime-telemetry` adds only the optional OTel SDK/exporter. `runtime-full`
combines async, storage and telemetry capabilities.

Release steps are explicit: preflight, migration plan, serialized forward
migration, then application admission. No migration runs on web/worker startup.
`Publish release images` is a manual workflow gated on an exact merged main SHA
and successful CI; it smoke-tests before GHCR push and records deployment digests.
No PR automatically publishes packages or deploys to production.

## Architecture

The starter uses a Django-first modular monolith.

```text
Simple:
API -> ORM

Medium:
API -> Service -> ORM

Complex:
API -> Service -> Domain -> Port -> Adapter
```

Do not add layers until complexity justifies them.

Business modules live under `apps/` and use local documentation and metadata:

```text
apps/<module>/
|-- module.yaml
|-- README.md
|-- api/
|-- tests/
`-- ... only the layers that module needs
```

The current `accounts` module is the reference medium-complexity module.

## Module tooling

Create a module:

```bash
python scripts/create_module.py products --type crud
python scripts/create_module.py orders --type domain
python scripts/create_module.py payments --type integration
python scripts/create_module.py notifications --type event-consumer
```

Validate module boundaries:

```bash
python scripts/check_architecture.py
```

Build minimal context for a coding agent:

```bash
python scripts/build_ai_context.py accounts
python scripts/build_ai_context.py accounts --include api/views.py
```

`AGENTS.md` is the source of truth for coding-agent rules.

## Caching and performance

[Phase 10 guide](docs/performance.md) covers opt-in JSON cache-aside, scoped
post-commit invalidation, conditional GET/HEAD, and query-count guardrails.
The performance cache defaults to disabled. Redis is optional and uses the
existing `async` dependency extra. Accounts/auth remain no-store; security
throttles and idempotency do not depend on the cache.

## Private files and storage

[Phase 11 guide](docs/storage.md) covers `/api/v1/files/`, private filesystem/S3,
owner-only downloads, immutable replacement and outbox cleanup. The initial
policy accepts bounded UTF-8 text and JSON, not arbitrary binaries or scanned
malware-free documents. Signed URLs are off by default. **Default media storage
is private and the DEBUG `/media/` route is removed.**

Run migrations and schedule both `reconcile_private_files` and `dispatch_outbox`.
The optional local worker shares the private volume; S3 cleanup must use the same
bucket/configuration and storage dependencies as the API. No S3 service is added
to the default development stack.

## Reference business module (Phase 13)

The `tickets` module is the end-to-end proof that the starter foundations work
together in a real business flow: owner/staff permissions, strict API queries and
pagination, PostgreSQL idempotent create, transactional outbox, reviewed summary
caching with commit-aware invalidation, private-file attachments, and the existing
runtime/worker deployment conventions.

See [apps/tickets/README.md](apps/tickets/README.md) for the API flow and invariants.

## Durable audit trail (Phase 16)

[Phase 16 guide](docs/audit-trail.md) adds append-only business change history
recorded transactionally with mutations. Tenant audit history is read-only,
strictly scoped by organization membership, and intentionally separate from
security logs and the transactional outbox.

## Compatibility gates (Phase 17)

[Phase 17 guide](docs/compatibility.md) adds pull-request gates for backwards
compatibility. CI compares generated OpenAPI against the exact base revision and
inspects changed Django migrations for rolling-deployment hazards. Deliberate
breaking releases require the reviewed `compatibility-approved` PR label; findings
remain visible even when the override is used.

## Outbound webhooks (Phase 18)

The `webhooks` integration module consumes reviewed transactional-outbox events
and fans them into a separate durable delivery queue. Tenant owner/admin can
manage HTTPS subscriptions, inspect delivery history, rotate signing-key versions
and replay terminal deliveries.

Delivery is HMAC-signed, retryable and SSRF-hardened. Signing material is derived
from a separate production master key and is never persisted in plaintext or
returned by the HTTP API. See [apps/webhooks/README.md](apps/webhooks/README.md).

## Notification foundation (Phase 19)

The `notifications` integration module consumes explicit-recipient domain events
from the transactional outbox and fans them into a separate durable email queue.
It adds per-user topic preferences, template rendering, provider abstraction,
leased retry/dead-letter delivery and self-service delivery history.

Email delivery uses Django 6.1 `MAILERS`; no provider call runs inside a request
or business transaction. See
[apps/notifications/README.md](apps/notifications/README.md).

## Operations and observability (Phase 20)

The `operations` integration module exposes staff-only aggregate health for the
transactional outbox, outbound webhook queue and notification queue. It reports
due backlog, retry state, terminal failures, active/stale leases and oldest due
age without exposing payloads, recipients, URLs or secrets.

API readiness remains PostgreSQL-only; async degradation is monitored separately.
Background Celery tasks emit low-cardinality structured completion events through
the existing JSON logger. See
[apps/operations/README.md](apps/operations/README.md).

## Data lifecycle and retention (Phase 21)

[Phase 21 guide](apps/retention/README.md) adds a dry-run-first, bounded retention
orchestrator for expired replay/throttle state and explicitly configured terminal
history. Retention is disabled by default for audit, outbox, webhook,
notification and private-file tombstone history; operators must choose policy
before enabling deletion.

## Resilience and failure semantics (Phase 22)

[Phase 22 guide](docs/resilience.md) defines and tests dependency failure
boundaries: PostgreSQL readiness, cache fail-open behavior, private-storage
compensation, leased worker crash recovery, downstream retry isolation and
at-least-once fan-out idempotency. CI has a dedicated resilience gate in addition
to the existing Redis/S3 integration jobs.

## OpenTelemetry and metrics (Phase 23)

[Phase 23 guide](docs/telemetry.md) adds opt-in OpenTelemetry traces and
low-cardinality metrics with OTLP/HTTP export. The base install remains
dependency-free from OTel; `runtime-telemetry` and `runtime-full` contain the
optional SDK. Trace context follows HTTP -> outbox -> webhook/notification
workers without exposing it to external webhook bodies or email templates.

## Testing and quality

The same gates used in CI can be run locally:

```bash
uv lock --check
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run ruff check .
uv run mypy .
uv run python scripts/check_architecture.py
uv run pytest
```

CI runs the minimal PostgreSQL full suite, resilience contracts, real Redis,
optional OpenTelemetry, S3-compatible integration and production-container smoke
in separate jobs. The minimal job needs neither Redis, S3 nor OpenTelemetry. The
container job builds and exercises all five runtime variants.

## Useful Make targets

```bash
make up
make down
make logs
make shell
make test
make coverage
make check
make format
make migrate
make makemigrations
make dbshell
make architecture
make module name=products type=crud
make ai-context name=accounts
```

## Project structure

```text
.
|-- AGENTS.md
|-- ARCHITECTURE.md
|-- apps/                  # accounts, core, files
|-- config/
|-- deploy/                # production env and single-edge proxy examples
|-- settings/
|-- scripts/               # module tooling, release gates, production smoke
|-- tests/
|-- bin/
|-- compose.yaml           # development only
|-- compose.production.yaml
|-- Dockerfile
|-- env.example            # development only
|-- Makefile
|-- pyproject.toml
`-- uv.lock
```

## Configuration

Copy `env.example` to `.env` for local development. Production uses the private
runtime configuration described in `deploy/production.env.example` and the
deployment guide; do not reuse development secrets, databases or upload volumes.

Sentry is opt-in through `SENTRY_DSN`. PII sending is disabled by default.

## Design principle

Start Django-native. Extract a boundary only when business complexity or
dependency volatility makes that boundary valuable.
