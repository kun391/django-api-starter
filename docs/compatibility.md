# API and database compatibility gates (Phase 17)

Production releases must preserve a rolling compatibility window. CI now checks
both the public OpenAPI contract and newly changed Django migrations on pull
requests.

## API compatibility

CI generates OpenAPI from both the pull request and its exact base SHA, then runs
`scripts/check_openapi_compatibility.py`.

The gate rejects selected changes existing clients can observe, including:

- removed paths or HTTP methods;
- removed request parameters;
- optional parameters or request properties becoming required;
- request/response property removal;
- schema type changes;
- enum narrowing;
- previously documented response status removal.

Additive endpoints, optional fields and additional response statuses are allowed.

This is intentionally not a proof of semantic compatibility. Changes to business
meaning, authorization, ordering, pagination semantics, defaults, rate limits or
performance can still be breaking and require review.

## Database migration safety

`scripts/check_migration_safety.py` inspects migration files changed relative to
the PR base and rejects or flags operations that are poor defaults for rolling
production releases:

- `RemoveField` / `DeleteModel`;
- direct `RenameField` / `RenameModel`;
- apparently required `AddField` without a migration default;
- `AddConstraint` because lock/runtime impact needs explicit review;
- normal `AddIndex` because large tables should generally use a separate
  non-atomic `AddIndexConcurrently` rollout.

The AST checker is deliberately conservative. It cannot know table size, PostgreSQL
version-specific lock behavior, custom operations, data volume or whether a
default is cheap. Human migration review remains required.

## Expand / migrate / contract

Use three releases when old and new code must coexist:

1. **Expand** — add compatible schema, normally nullable/default-safe columns,
   new tables or separately reviewed concurrent indexes.
2. **Migrate** — deploy code that understands both shapes and backfill in bounded,
   resumable batches.
3. **Contract** — only after all callers/workers are migrated and the rollback
   window closes, remove legacy schema in a separate explicitly approved release.

Do not disguise a rename as a destructive remove/add in one deployment.

## Escape hatch

A deliberate breaking release can use the pull-request label:

`compatibility-approved`

The gates still run and print every finding, but return success. Treat this as a
maintainer/reviewer decision, not a developer convenience. Repository rules
should restrict who can apply the label and require review for such pull requests.

The label does not make a migration safe. The PR/release must document:

- affected clients/workers;
- migration/backfill plan;
- mixed-version behavior;
- lock/runtime estimates where relevant;
- backup/recovery point;
- rollback boundary;
- deployment ordering and communication.

Pushes to `main` retain normal schema/migration/tests; compatibility comparison is
a pull-request gate because it needs the exact base revision.
