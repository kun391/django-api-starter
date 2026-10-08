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


## Filesystem snapshot inventory verification (Part 3)

The read-only `scripts/dr_private_files.py` tool fingerprints a **previously
created, quiesced** private-files snapshot. It does not copy objects, stop
writers, create an atomic snapshot, upload/encrypt files, or coordinate a
PostgreSQL recovery point. Run it against an isolated snapshot, NOT live storage:

```bash
python3 scripts/dr_private_files.py inventory --root /isolated/private-files-snapshot \\
  --output /isolated/private-files-inventory.json
python3 scripts/dr_private_files.py verify --root /isolated/private-files-snapshot \\
  --manifest /isolated/private-files-inventory.json
```

The manifest records immutable object keys, byte counts and SHA-256; verification
rejects missing, unexpected, changed or symlinked objects. Store manifest
separately and authenticate it through your encrypted/immutable offsite backup
process; its hash alone cannot prove origin. Filesystem content must be captured
at an application-consistent point with the PostgreSQL backup. Stop uploads,
cleanup consumers and any writers, take a storage snapshot, capture database
recovery state, and only resume after the consistency boundary is recorded.
A sequential `pg_dump` and file copy does **not** guarantee atomic consistency.
For S3 deployments use versioned/immutable bucket snapshots and an independent
object inventory; this filesystem tool is **not** an S3 exporter or verifier.
Do not claim a complete DR drill until restored PrivateFile READY records are
cross-checked against every required object, including byte length and hash.

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

## Database + S3 reconciliation

Use an **isolated restored database** and separately recovered S3 bucket. Never
run external deliveries, notifications or retention workers during the audit.

1. From the restored environment, export PrivateFile states using
   `python manage.py export_dr_file_manifest --output /secure/db-files.json`.
2. For filesystem recovery, use the Part 3 inventory (the output object entries
   match the reconciliation input contract).
3. For S3, use a private, independently restored **versioned** bucket:
   `python scripts/dr_s3.py --bucket isolated-dr --output /secure/s3-files.json`.
   This hashes GET bodies using the returned version identifier; the script
   does not use ETag as a checksum and never modifies bucket contents.
4. Audit with
   `python scripts/dr_reconcile.py --database-manifest /secure/db-files.json --objects-manifest /secure/s3-files.json`.
   A nonzero exit indicates missing or modified READY files, or invalid manifests.
   Non-READY orphaned objects are reported for human review, not automatically
   deleted; pending uploads and tombstones can legitimately have objects.

S3 inventory verification:
`python scripts/dr_s3.py --bucket isolated-dr --verify /secure/s3-files.json`.
Bucket must have versioning enabled before objects are written. The inventory is
not an immutable copy of object bytes: ensure the independent backup contains the
pinned versions and its identity/retention is protected. For cross-provider
restores, version identifiers are provider-specific and must be remapped under
an explicit restore process. S3 list/read operations may race with writes; take
a consistent versioned snapshot or quiesce writers before capture.

### Operational release criteria, not supplied by this starter

- Test offsite backup with encrypted, immutable/locked copies in another failure
  domain; use independent access credentials, key custody and expiry protection.
- Select and enforce retention, backup frequency and escalation via an operator
  scheduler; do not embed vendor credentials in GitHub CI.
- Capture synchronized database and file recovery points. Logical dump plus
  independent bucket listing without quiescence does **not** guarantee consistency.
- Complete a real isolated DR recovery including authentication, authorization,
  private file downloads and safe outbox replay before assigning measured RPO/RTO.

## Encrypted offsite bundle with restic

The operator-only script `scripts/dr_offsite.py` validates a staged recovery
bundle before passing it to restic, which provides authenticated encryption.
The script never automatically initializes, prunes, or deletes backups.

A staged bundle must contain `recovery-point.json` with a unique recovery ID,
the PostgreSQL backup manifest/archive, the filesystem inventory manifest,
and corresponding `private-files/objects/` bytes. Bundle validation checks
object checksums and refuses unexpected files or symlinks. This is not proof
of atomicity: operators must pause all writers and take a consistent database
and file snapshot before preparing the bundle.

Use `validate --bundle` first. Set RESTIC_REPOSITORY to a separately managed
offsite destination and RESTIC_PASSWORD_FILE to a restricted local credential.
For upload, run `backup --bundle ... --confirm FROZEN:<recovery-id>`.
Run `check` to verify the encrypted backup repository. Recovery requires an
explicit snapshot identifier and a new, empty isolated restore location through
`restore --snapshot ... --target ... --confirm RESTORE:<snapshot-id>`.
Never use the latest alias as the source of a DR restore.

The encrypted copy is not automatically immutable or retained. Configure
provider-side WORM/Object Lock where available, separate operator permissions,
retention and alerting. Audit restoration regularly, measure RPO/RTO, and
maintain offline access to encryption keys. No CI job uses real offsite secrets.

## Recovery acceptance monitoring

The `scripts/dr_freshness.py` helper checks the age of an operator-supplied
manifest with timezone-aware `created_at` and fails on stale, future, missing
or invalid timestamps. Run it only against a manifest already authenticated by
the backup repository or trusted manifest verification process:

```bash
python3 scripts/dr_freshness.py --manifest /secure/latest-verified-db.json --max-hours 24
```

This is a **recency signal**, not proof of data consistency or successful backup
and restore. Schedule it externally with alerting, and ensure it examines the
latest remotely verified snapshot, not a local manifest that was never uploaded.
For S3, the CI job tests real MinIO bucket versioning, pinned historical
object reads and mismatch detection against Django PrivateFile metadata. The
test fixture creates and deletes only its own randomly generated bucket.
