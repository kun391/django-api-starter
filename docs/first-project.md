# Start the first product

This repository is a **Django API starter**, not an upgradable framework.
Create a separate Git repository for each product; keep the platform foundations
and grow business modules only when you have a concrete use case. The reference
`tickets` and `organizations` modules demonstrate conventions; they are not
mandatory product requirements.

## 1. Create the product repository

Use GitHub's **Use this template** action if enabled for this repository.
Otherwise create a fresh product repository from a copy of the source (without
keeping the starter as an upstream dependency). Review the copied files before
shipping. Record the initial starter commit in your product README.

Update **product identifiers** deliberately:

- The new repository and README name/description.
- `pyproject.toml` project name/description and the OpenAPI title/version in
  `settings/base.py`. After changing package metadata, run `uv lock` and
  commit the updated `uv.lock`; do not hand-edit the lockfile.
- `TELEMETRY_SERVICE_NAME` and Sentry service/environment settings if you
  enable telemetry. Set the product's real domain/allowed hosts only through
  deployment configuration.
- Your CI/release registry ownership and publish permissions; a copied
  GitHub Actions workflow must be reviewed for new repository permissions.

Do **not** rename existing Django app labels, migration files or database
tables as a branding exercise. Renaming a Django app is a separate data
migration decision. Keep `apps/core` as the shared platform layer and
`config/` as the project composition root.

## 2. Choose only what the product needs

The starter works with **PostgreSQL** and the web runtime alone. Decide
whether to enable optional capabilities:

| Capability | Default | Enable when |
| --- | --- | --- |
| Auth and permissions | DRF token and session auth | Review access model before handling real users |
| Organizations / tickets | Working reference domain | Reuse only if the product genuinely needs it |
| Celery / broker | Off | Background workers and scheduling are required |
| S3-compatible private storage | Off | Files need managed object storage |
| OpenTelemetry | Off | You have a trusted collector and retention rules |
| Retention jobs | Off by default | A reviewed policy and rollback plan exist |

Do not remove sample domain apps and their migrations blindly. If you want an
empty domain instead, do it as a dedicated reviewed change in the **new**
repository, before real data exists, updating URLs, `INSTALLED_APPS`,
permissions, event subscriptions, tests, deployment and migration history
together. There is no automatic "delete sample modules" script.

## 3. Run locally and verify the baseline

With Docker + Docker Compose available:

```bash
cp env.example .env
# Review the development-only settings in .env. Never commit this file.
docker compose up -d --build
docker compose exec web python manage.py migrate
docker compose exec web python manage.py check
```

The local API is at `http://localhost:5001`. The default Compose profile
starts only web and PostgreSQL. To use optional async infrastructure, add
`--profile async` only after you have a background task that requires it.
The development credentials and examples **must not** be reused for staging or
production.

With a local Python installation and the database reachable as configured:

```bash
uv sync --locked --group dev
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run python scripts/check_architecture.py
uv run python -m scripts.check_event_contracts
uv run pytest
```

The PostgreSQL settings in `.env` must match the environment in which you
run these commands; an in-container hostname such as `db` may not resolve
from your host.

## 4. Implement the first real business capability

Write one short module README and `module.yaml` before building endpoints.
Identify actors, authorization rules, data ownership, the API contract and
events. Start with `crud`; add services or integrations only when needed.

```bash
uv run python scripts/create_module.py products --preset crud
```

Then add `apps.products` to `LOCAL_APPS` in `settings/base.py`, expose its
HTTP routes through `config/urls.py` only if needed, and write models,
migrations and tests. Declare **code imports** under `depends_on`; list
actual emitted/consumed topics in `emits`/`consumes`. Consumers must use
`outbox.subscribe(..., subscriber_id=...)` in `AppConfig.ready()` and remain
idempotent. Reuse `apps/core` primitives; do not import a business module
back into core.

Before implementing file uploads, webhooks or tenant RBAC, read the matching
`docs/storage.md`, `docs/background-jobs.md` or `docs/multitenancy.md` and
the relevant reference module's README.

## 5. Definition of ready for the first deployment

- The repository has product-specific metadata, real environment secrets and
  a reviewed authorization and data-retention policy.
- Core architecture, migration checks, event contracts, API schema and the
  full tests pass. New business behavior has API/permission tests.
- `bash scripts/smoke_production.sh` passes against disposable PostgreSQL
  with **real production images**. This requires Docker and builds all five
  image variants; it does not use production credentials.
- CI has completed successfully on the reviewed commit. Release publication,
  database migrations and deployment remain explicit operations; never
  run migrations at replica startup.
- A backup/restore drill and operational ownership are agreed before real
  production data is stored. See `docs/disaster-recovery.md` and
  `docs/deployment.md`.

**Keep this starter boring:** ship one vertical product feature end-to-end,
prove the conventions work for it, then add the next module. No extra
architecture layer is required merely because the project is new.
