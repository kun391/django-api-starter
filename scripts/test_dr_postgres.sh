#!/bin/sh
# CI only: destructive operations are restricted to disposable dr_* databases.
set -eu
: "${PGHOST:?Set an isolated PostgreSQL PGHOST}"
: "${PGUSER:?Set a disposable test PGUSER}"
[ "${DR_CI_ISOLATED:-}" = "yes" ] || { echo "DR_CI_ISOLATED=yes required" >&2; exit 2; }
case "$PGHOST" in 127.0.0.1|localhost) ;; *) echo "Only loopback test PostgreSQL allowed" >&2; exit 2;; esac
source_db="dr_source_$$"
target_db="dr_restored_$$"
work=$(mktemp -d)
chmod 700 "$work"
cleanup() {
  dropdb --if-exists "$target_db" || true
  dropdb --if-exists "$source_db" || true
  rm -rf "$work"
}
trap cleanup EXIT INT TERM
createdb "$source_db"
psql -X -v ON_ERROR_STOP=1 -d "$source_db" -c "CREATE TABLE restore_probe(id integer PRIMARY KEY, payload text NOT NULL); INSERT INTO restore_probe VALUES (1, 'expected');"
python3 scripts/dr_postgres.py backup --database "$source_db" --directory "$work"
manifest=$(find "$work" -maxdepth 1 -type f -name '*.json' -print -quit)
[ -n "$manifest" ]
python3 scripts/dr_postgres.py verify --manifest "$manifest"
python3 scripts/dr_postgres.py restore --manifest "$manifest" --target "$target_db" --confirm "CREATE:$target_db"
result=$(psql -X -A -t -v ON_ERROR_STOP=1 -d "$target_db" -c "SELECT payload FROM restore_probe WHERE id=1")
[ "$result" = "expected" ] || { echo "DR data mismatch" >&2; exit 1; }
if python3 scripts/dr_postgres.py restore --manifest "$manifest" --target "$target_db" --confirm "CREATE:$target_db"; then
  echo "Existing-target restore unexpectedly succeeded" >&2; exit 1
fi
archive=$(find "$work" -maxdepth 1 -type f -name '*.dump' -print -quit)
printf '\000' >> "$archive"
if python3 scripts/dr_postgres.py verify --manifest "$manifest"; then
  echo "Corrupt archive unexpectedly verified" >&2; exit 1
fi
echo "DR isolated PostgreSQL roundtrip and safety guards passed."
