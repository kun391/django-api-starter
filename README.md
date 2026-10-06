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

The API is available at `http://localhost:5001`.

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
# Background jobs
uv sync --locked --extra async --group dev

# Object storage integrations
uv sync --locked --extra storage --group dev
```

Available extras:

- `async`: Celery and Redis client
- `storage`: django-storages and boto3

## Docker Compose

Default development stack:

```bash
docker compose up -d
```

This starts only:

```text
web + PostgreSQL
```

Enable asynchronous infrastructure only when needed:

```bash
docker compose --profile async up -d
```

The `async` profile adds:

```text
Redis + RabbitMQ + Celery worker + Celery beat
```

## Production images

The Dockerfile is multi-stage.

```bash
# Minimal API image
docker build --target runtime -t django-api-starter .

# Image with Celery dependencies
docker build --target runtime-async -t django-api-starter-async .
```

The production targets do not include uv, compilers, Git, PostgreSQL CLI,
netcat, debug toolbar, or test tooling.

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
├── module.yaml
├── README.md
├── api/
├── tests/
└── ... only the layers that module needs
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

CI also builds both production Docker targets and runs optional real-Redis cache
integration in a separate job; the minimal PostgreSQL job needs no Redis.

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
├── AGENTS.md
├── ARCHITECTURE.md
├── apps/
│   ├── accounts/
│   └── core/
├── config/
├── settings/
├── scripts/
├── tests/
├── bin/
├── compose.yaml
├── Dockerfile
├── env.example
├── Makefile
├── pyproject.toml
└── uv.lock
```

## Configuration

Copy `env.example` to `.env` for local development. Production must provide
its own secret key, allowed hosts, database credentials, and any enabled
integration credentials.

Sentry is opt-in through `SENTRY_DSN`. PII sending is disabled by default.

## Design principle

Start Django-native. Extract a boundary only when business complexity or
dependency volatility makes that boundary valuable.
