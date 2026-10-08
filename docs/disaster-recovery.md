# Phase 24 — Backup / Restore / Disaster Recovery

This starter provides **operator tools and a runbook**, not a backup service or
a claim that production recovery is configured. Backups MUST live outside the
host/container and failure domain of the application. A database dump alone is
not complete recovery when private uploads, secrets, or external queues exist.

## Recovery contract and targets

Record environment-specific owners, backup schedules, retention, storage
destination, encryption keys, IAM and monitoring before production release.
Suggested initial targets (not guarantees): PostgreSQL RPO <= 24 hours and
RTO <= 4 hours for a small single-site application. Make these measurable through
an actual isolated restore drill. Continuous WAL archiving/PITR is **not**
implemented by this logical dump utility. If a stricter RPO is required, use
a managed PostgreSQL backup/PITR service or pgBackRest/WAL-G with continuous WAL,
tested timeline restores, and independent operational ownership.

| Asset | Required strategy |
| --- | --- |
| PostgreSQL | Scheduled custom-format logical dumps (or managed physical/WAL backups), offsite encrypted copies, checksum and independent restore test |
| Private filesystem uploads | Consistent snapshot of the shared persistent volume; separate from DB; never rely on container writable layers |
| S3 private files | Bucket versioning/replication and recoverable offsite copies with separate credentials; protect against delete markers/ransomware |
| Runtime secrets/keys | Recovery from independent secret manager, access audited; never include plain secrets in dump archives |
| Container release | Preserve immutable image digests and release manifests, deployment variables and migration compatibility |
| Redis/cache | Rebuildable cache; persistent Celery broker/queue workloads require an explicit continuity/replay design |
| Webhooks/outbox | Reconcile pending durable DB rows and check downstream idempotency; pause consumers until data consistency is verified |

## Backup — PostgreSQL

Install a compatible `pg_dump`, `pg_restore`, and `createdb` on a trusted operator host.
Use a least-privileged backup role with needed schema access, and provide libpq
`PGHOST`, `PGPORT`, `PGUSER`, `PGSSLMODE=verify-full`, `PGSSLROOTCERT`
and credentials through a protected `.pgpass` or secure injection (not flags).
Restrict the operator machine, credentials, output directory, and backup service account.

```bash
install -d -m 700 /secure/local-staging/postgres
python3 scripts/dr_postgres.py backup --database django_api \
  --directory /secure/local-staging/postgres
python3 scripts/dr_postgres.py verify \
  --manifest /secure/local-staging/postgres/<backup>.json
```

Each successful archive has a same-prefix 0600 JSON manifest with SHA-256, bytes,
database and timestamp. `pg_restore --list` validates the archive's catalog,
**not** the consistency of application data or its recoverability. Upload both
files to a separately authorized **encrypted** offsite destination; verify the
remote copy and prevent accidental retention shortening. The utility does not
encrypt, upload, delete old backups, schedule jobs or operate production credentials.

Do not run `docker compose down -v`, `make reset-db` or a destructive
database restore during normal backup operations. The legacy `make backup` /
`make restore` commands do not meet this contract and must not be used for DR.

## Restore drill (isolated target only)

Use a dedicated PostgreSQL **DR server/cluster** or isolated development
instance, with NO production workers, web traffic, webhook delivery, email,
scheduler or external integrations enabled. Choose a new empty database named
`dr_<identifier>`. Check that both the DB and file snapshot refer to an
appropriate recovery point. Never point DR workloads at production buckets.

```bash
python3 scripts/dr_postgres.py verify --manifest /secure/local-staging/postgres/<backup>.json
python3 scripts/dr_postgres.py restore \
  --manifest /secure/local-staging/postgres/<backup>.json \
  --target dr_20261008 --confirm CREATE:dr_20261008
```

The command calls `createdb` without IF NOT EXISTS and never drops or overwrites
an existing database; it aborts if the target exists. **The prefix is NOT a
security boundary**: an operator could name a live database `dr_...`.
Use isolated network routing and dedicated credentials with no production write
permissions. Restore may leave a partial new DB on failure; inspect it and
clean it up manually in the disposable environment only.

Verify migrated schema compatibility against the exact historical image,
representative logins and CRUD, record counts, referential consistency,
private-file links/content and checksum, pending outbox/queue lease handling,
and expiration/retention behavior. Suppress outbound side effects. Record
start/end timestamps, achieved RPO/RTO, missing data and remediation. Fail the
drill if assets are missing or external integrations were accidentally invoked.

## Disaster recovery decision sequence

1. Declare incident, isolate compromised principals and stop writers/consumers.
2. Preserve evidence and determine clean restore point / exact image digest.
3. Confirm availability and independent integrity of PostgreSQL and private-file snapshots.
4. Restore into isolated infrastructure; rehydrate secure config/keys without logging them.
5. Check schema, business data, private objects, auth, outbox and pending messages.
6. Test API and health endpoints privately; rehearse side-effect-safe deliveries.
7. Obtain explicit incident-owner approval for cutover/DNS/traffic and consumer resume.
8. Monitor error rate, reconciliation and missing writes; document achieved RPO/RTO.

Never automatically promote a restored DR database to production. Train at
least two operators and run quarterly drills plus after schema/storage changes.

## Acceptance gate

- Backup archive + manifest created with restrictive permissions.
- A corrupted/truncated backup or tampered manifest fails verification.
- Restore refuses non-`dr_` target, wrong confirmation and an existing target.
- An isolated restore drill demonstrates a recoverable application and private files.
- External storage retention, immutability and key recovery are verified **outside** this repo.
