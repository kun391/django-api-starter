# Deployment and release foundation (Phase 12)

This guide describes the files that actually exist in this starter. The former
provider shopping list and nonexistent docker-compose.prod.yml examples are
replaced by one tested container contract. No production infrastructure is
created just by merging this phase.

## Deployment contract

The minimal application requires PostgreSQL and a trusted TLS edge. Redis,
RabbitMQ, Celery and S3 are optional capabilities, not universal prerequisites.
Django sessions remain database-backed; API throttles/idempotency/outbox remain
PostgreSQL-backed. Do not introduce a Redis requirement merely for deployment.

Development uses `compose.yaml`. Production uses the **standalone**
`compose.production.yaml`; do not merge the two or copy development bind mounts,
DB port publication, sample credentials or the development startup script.
Docker Compose 2.30+ is required for `env_file.format: raw`; the host operator
wrapper also requires Python 3. Production dependencies can be independently
managed on-premises or by a cloud provider.

Production images default to `settings.production`. Staging imports the same
security policy and needs its OWN secret, explicit hostnames, database and
private storage. `settings.build` is exclusively for offline static collection,
never a runtime settings module. No production secrets are passed to Docker build.

| Target | Installed optional extras | Default command |
| --- | --- | --- |
| `runtime` | None | Gunicorn API |
| `runtime-async` | async (Celery + Redis client) | Celery worker |
| `runtime-storage` | storage (django-storages + boto3) | Gunicorn API |
| `runtime-full` | async + storage | Gunicorn API |

Use `runtime-full` for an S3-backed Celery worker, or an API using both S3 and
Redis cache. Compose explicitly selects the web/worker/beat command, so the
same appropriate image/digest can be used for all roles. Installing an extra
does not require starting every associated service. The minimal image contains
no uv, test tooling, compiler or optional service client.

All runtime targets contain pre-collected WhiteNoise static assets and use UID
10001/GID 10001. Source, dependencies and static files are root-owned. Runtime
storage and `/tmp` are the only intended writable locations. Gunicorn reads
`config/gunicorn.py`: bounded workers/threads/timeouts, graceful shutdown and
stdout/stderr logging without query strings, credential headers or bodies.
Tune worker counts against CPU, RAM and PostgreSQL connection capacity; defaults
are a starting point, not a benchmark-derived capacity claim.

## Build, verify, publish, promote

The PR/main `CI` workflow has four required checks:

- `test`: locked dependencies, Django/migration checks, audits, Ruff/MyPy,
  architecture, schema, full PostgreSQL tests, Compose and shell validation.
- `Optional Redis integration`: cache correctness against real Redis.
- `Private storage integration`: private file behavior against a disposable
  real S3-compatible server (the existing CI fixture, not a production choice).
- `Production smoke`: build ALL four runtime targets and exercise them as
  non-root/read-only containers against an isolated PostgreSQL database.

Run the last gate on a development machine with Docker:

```bash
bash scripts/smoke_production.sh
```

It creates uniquely named disposable containers, a network and a volume; it
never consumes production DB/bucket credentials. It verifies an unmigrated DB
is rejected, applies migrations with the release command, repeats them safely,
starts real Gunicorn, checks static/admin rendering, HTTPS/proxy handling,
registration/login/revoke, private upload/download/delete and cleanup from a
separate process. It then shuts down and removes its isolated resources.
The S3/Redis external protocols are covered by their separate integration jobs;
container smoke uses filesystem storage and no broker.

### Explicit package publication

After merge and successful **main push CI**, run `Publish release images` from
`main` in GitHub Actions. Supply a full 40-character merged `sha` and a stable
version such as `v0.1.0`. This phase does not run that workflow automatically.

The gate rejects non-main workflow refs, invalid input, unmerged/non-ancestor
commits, commits lacking the smoke harness, non-push/fork CI, a failed/latest
incomplete run, or missing/skipped required jobs. Pull-request merge-ref success
alone is not sufficient. Configure main branch rules/reviews and required checks
in repository settings; those administrative protections are not created here.

Each variant is built once, smoke-tested, then that exact local image is pushed
to GHCR using job-scoped `packages: write` and `GITHUB_TOKEN`. No production
credentials or SSH access are used. Each tag includes version, variant, run ID
and attempt number; reruns do not overwrite the previous attempt's tag. There is
no mutable `latest` deployment contract.

Each `release-<variant>` artifact contains `release-manifest.json` with the exact
source SHA, version, platform, image digest and workflow attempt. The workflow
also prints digest references in its summary. Package publication can partially
succeed if another variant fails; treat the release as complete only when every
required variant and manifest succeeds. Re-run deliberately and select one
verified manifest set. This publishes images, not a GitHub tag/release entry.

The provided workflow builds linux/amd64 on the hosted runner. It does not claim
ARM64/multi-platform validation, signed provenance/SBOM attestation, byte-for-byte
reproducible OS layers, or production registry permission validation before its
first authorized publication. Base OS image/package updates can change rebuilt
bytes even with the same source; the published **digest** is the identity that
staging and production must promote without rebuilding.

Archive approved manifests and previous known-good digests in the deployment
record; GitHub artifacts expire after 90 days. Keep old registry objects for the
rollback window. Re-run main CI when a previously verified commit needs a fresh
dependency audit before publication.

## Configure runtime and the trusted edge

Copy the example outside the repository and fill the required values:

```bash
install -d -m 700 "$HOME/.config/django-api"
install -m 600 deploy/production.env.example "$HOME/.config/django-api/production.env"
python3 -c 'import secrets; print(secrets.token_urlsafe(64))'
```

Write that independently generated secret into the private file; do not commit
it, echo it in CI logs, or include it as a Docker build argument. The example
intentionally has an empty SECRET_KEY and cannot start without configuration.
The raw env format uses unquoted literal values; no shell sourcing/interpolation.
For managed workload identities/secrets, replace the env-file delivery at the
platform boundary while preserving the settings contract.

Production refuses short/obvious insecure secrets and wildcard/URL hosts.
This validation is a sanity check, not an entropy estimator. PostgreSQL defaults
to `sslmode=verify-full`, a 5-second connect timeout, connection health checks and
60-second connection reuse. Supply the correct CA/hostname and mount a CA file
read-only when using POSTGRES_SSLROOTCERT. `require` encrypts without the same
hostname-verification guarantee. `disable` is used only in the isolated CI
network; any production exception requires an explicit deployment decision.

Use `deploy/nginx.conf.example` only for ONE directly exposed, trusted TLS edge
on the host. Replace its domain/certificate paths and verify the configuration
before reloading Nginx. It publishes no private media root and does not proxy
public `/health/` requests. The API container is bound to host loopback only.
For a containerized proxy or a load balancer use a private network/allowlist
instead, adapting the example to the actual trust topology.

Enable `TRUST_PROXY_SSL_HEADER=True` only after the proxy **overwrites**
X-Forwarded-Proto and direct API ingress is blocked. Gunicorn independently
trusts no secure-scheme headers; Django's opt-in is the only interpretation.
`API_NUM_PROXIES` controls X-Forwarded-For separately. Set it to 1 only for the
single sanitizing edge shown, never blindly for an unknown CDN/proxy chain.

Production and staging require HTTPS and secure session/CSRF cookies. Only the
minimal `/health/live/` and `/health/ready/` paths are exempt from redirect so
internal HTTP container probes do not follow fake TLS redirects. Liveness ignores
credentials and dependencies; readiness checks PostgreSQL connectivity and
returns a generic 503 without secrets. Neither probe is cacheable. Schema,
private storage, worker progress and broker health are separate release/monitoring
checks, not falsely implied by a successful SELECT 1.

HSTS preload remains an explicit domain-owner decision. `release_check` reports
W021 as an informational policy reminder but fails all other deployment warning
IDs. It does not enable preload or submit a domain. Confirm subdomain readiness
and staged HSTS rollout before changing those settings.

## Deploy explicitly, before admitting traffic

Use a reviewed image digest from the release manifest and the same Compose project
name for every release, so persistent volumes do not unexpectedly change:

```bash
export APP_IMAGE='ghcr.io/your-org/your-api@sha256:<approved-64-character-digest>'
export APP_ENV_FILE="$HOME/.config/django-api/production.env"
export DEPLOY_PROJECT=django-api
export APP_PORT=8000

./scripts/deploy.sh preflight
./scripts/deploy.sh plan
# Review forward changes, old/new-code compatibility, locks and verified backups.
CONFIRM_MIGRATIONS=yes ./scripts/deploy.sh migrate
./scripts/deploy.sh up
./scripts/deploy.sh check
```

The wrapper requires a full digest and a private mode-600 env file, never sources
that file, and does not print expanded Compose configuration/secrets. `preflight`
pulls the selected image and checks production settings, the built static
manifest and database migration history. Before migration it permits pending
schema changes. `up` repeats preflight WITHOUT that allowance and refuses to
start the new web container if migrations are still pending.

Migrations are an explicit one-shot command, never a startup side effect across
all replicas. `release_migrate` takes a stable PostgreSQL session advisory lock
and fails immediately when another cooperating release owns it. It uses native
Django per-migration transactions, with default 10-second lock timeout and
5-minute per-statement timeout. Non-atomic concurrent-index migrations remain
possible. Set bounded flags deliberately for approved longer data migrations.
Use a direct or session-pooled DB connection: transaction-pooled PgBouncer cannot
provide the required session-lock guarantee. Manual `manage.py migrate` commands
do not participate in this cooperative release lock.

For platform deployments, run `release_check --allow-pending-migrations`,
`release_migrate`, then `release_check` as release jobs using the same approved
image, settings and DB. Admit app instances only afterwards. Give the migration
role only the additional DDL privileges it needs and keep ordinary runtime
privileges limited. No Kubernetes, Coolify, ECS or SSH deployment integration is
implicitly configured by this repository.

The production Compose file is intentionally a simple single-web-host example.
Replacing its web container can interrupt service: **it does not promise
zero-downtime rolling deployment**. For that use multiple instances and explicit
load-balancer readiness/draining. Match stop_grace_period to Gunicorn graceful
shutdown settings and coordinate worker/scheduler rollout separately.

## Compatibility gate before release

Pull requests run the Phase 17 API/database compatibility gate before merge.
OpenAPI is generated independently from the PR and its exact base SHA. Changed
migration files are inspected for destructive/rename/required-add and lock-sensitive
operations.

A PR carrying the `compatibility-approved` label still prints all findings but
may proceed after explicit review. Treat that label as deployment evidence: the
PR must explain mixed-version behavior, migration/backfill ordering, lock/runtime
risk, rollback boundary and affected consumers. The label is not a substitute
for backups, restore rehearsal or expand/migrate/contract.

## Schema evolution and rollback

Use expand/migrate/contract when old and new code can coexist:

1. Expand with compatible columns/tables/indexes; prefer nullable/default-safe
   additions and assess lock cost on representative data.
2. Deploy code that can handle both representations; backfill in bounded,
   resumable batches outside long API transactions.
3. Verify all web/worker instances and consumers have moved; remove old schema
   only in a separate release after the rollback window closes.

Do not combine renaming/removing required columns with a code rollout that still
has old workers. `makemigrations --check` detects drift, not rolling safety.
Likewise, successful preflight does not prove data semantics, destructive-change
safety or acceptable production lock duration.

Record the previous digest, environment revision, migration plan, backup/restore
point and operator decision for each release. On failure stop admission of the
new build and inspect sanitized logs. If the schema/data change remains backward
compatible, set APP_IMAGE to the previous approved digest and run preflight/up/check
again. For optional workers, pin the matching previous image as well and ensure
only one compatible Beat scheduler runs.

Never automatically run reverse migrations or restore a database as a generic
rollback action. Irreversible/data-destructive changes require a reviewed recovery
plan. A code rollback does not undo data changes, external side effects, outbox
events or files. CI tests repeat migration/no-op behavior and lock safety; it does
not certify every future product migration as rollback-compatible.

## Durable private files and maintenance

The production volume is private and not exposed by the edge. Fresh volumes
inherit UID/GID 10001 from the image; existing volumes created by older images
may need an operator-reviewed ownership migration while services are stopped.
Back up first and adjust ONLY the intended private volume/path, never chmod 777
or recursively change unrelated host directories. New production volumes are not
automatically populated from development media/private volumes.

Web and cleanup processes must share the SAME persistent storage. Single-host
Docker volumes do not become shared multi-host storage by increasing replicas.
Use the Phase 11 S3 backend or explicitly managed shared storage for that design.
Changing PRIVATE_FILE_BACKEND/bucket/root does not migrate existing objects.

Without Celery, schedule these bounded commands with the platform's scheduler,
using the deployment env file and SAME image digest/configuration:

```bash
docker compose -p "$DEPLOY_PROJECT" -f compose.production.yaml run --rm --no-deps -T web \
  python manage.py reconcile_private_files --batch-size 100
docker compose -p "$DEPLOY_PROJECT" -f compose.production.yaml run --rm --no-deps -T web \
  python manage.py dispatch_outbox --batch-size 100
docker compose -p "$DEPLOY_PROJECT" -f compose.production.yaml run --rm --no-deps -T web \
  python manage.py purge_security_throttles --batch-size 1000
```

Also schedule the existing idempotency retention command from the API contract.
Select cadence against event volume and the accepted cleanup delay; one batch
per day is not enough for a busy outbox. Monitor backlog age, retries/dead letters,
abandoned uploads, tombstone rechecks, disk space and failed scheduler executions.

With Celery, provision private broker/cache endpoints and choose runtime-async
(filesystem) or runtime-full (S3). Explicitly start the `async` profile after
preflight/migration; the operator wrapper does NOT restart worker/Beat silently.
Beat already schedules outbox dispatch, not every retention/reconciliation task.
Use the platform scheduler for the latter or explicitly add reviewed periodic
jobs. Never run two Beat schedulers for the same schedule accidentally.

## Backup, restore and operating boundaries

Back up PostgreSQL AND private storage, encrypt backups, restrict access, define
retention/RPO/RTO and perform restore rehearsals into an isolated environment.
A snapshot existing is not evidence that restore works. Coordinate DB manifests,
object versions and outbox state at recovery time. Do not run deletion dispatch
against a restored DB pointing at the live production bucket while investigating.

After an isolated restore: verify schema history/preflight, representative auth
and private-file reads, object/version correspondence, and the consequences of
replaying outstanding outbox events. Only then plan a controlled cutover.
`pg_dump`/PITR/object versioning and backup credentials belong to infrastructure;
no production backup, restore or bucket policy has been executed by this phase.

Keep the existing structured application logs and request IDs; alert on sustained
5xx/readiness failure, PostgreSQL saturation, outbox dead letters, storage errors,
backup failure and certificate expiry. The Nginx example omits query strings in
its access format, but infrastructure/error-log/Sentry retention and redaction
still need review for the actual environment. Do not store auth headers, uploaded
content or signed URL query strings in deployment evidence.

## References

- Django deployment checklist: https://docs.djangoproject.com/en/6.1/howto/deployment/checklist/
- Django proxy settings: https://docs.djangoproject.com/en/6.1/ref/settings/#secure-proxy-ssl-header
- Docker production Compose: https://docs.docker.com/compose/how-tos/production/
- Compose raw env files: https://docs.docker.com/reference/compose-file/services/#env_file
- GitHub package publication: https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images
- GitHub workflow run API: https://docs.github.com/en/rest/actions/workflow-runs
