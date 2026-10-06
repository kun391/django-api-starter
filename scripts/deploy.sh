#!/bin/sh
# Explicit operator steps, never automatic deployment from pull-request CI.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
: "${APP_IMAGE:?Set a reviewed digest-pinned APP_IMAGE}"
: "${APP_ENV_FILE:?Set an absolute private APP_ENV_FILE}"
python3 - <<'PY'
import os
import re
from pathlib import Path
image = os.environ['APP_IMAGE']
if not re.fullmatch(r'[a-z0-9][a-z0-9./:_-]*@sha256:[a-f0-9]{64}', image):
    raise SystemExit('APP_IMAGE must be an explicit image@sha256:digest, not a tag.')
path = Path(os.environ['APP_ENV_FILE'])
if not path.is_absolute() or not path.is_file():
    raise SystemExit('APP_ENV_FILE must be an existing absolute file path.')
if path.stat().st_mode & 0o077:
    raise SystemExit('APP_ENV_FILE must not be group/world accessible; use chmod 600.')
PY
compose() {
    docker compose -p "${DEPLOY_PROJECT:-django-api}" -f "$root/compose.production.yaml" "$@"
}
run() { compose run --rm --no-deps -T web python manage.py "$@"; }
case "${1:-}" in
    preflight)
        compose pull web
        run release_check --allow-pending-migrations
        ;;
    plan) run release_migrate --plan ;;
    migrate)
        [ "${CONFIRM_MIGRATIONS:-}" = yes ] || { echo 'Review the plan and verified backups; set CONFIRM_MIGRATIONS=yes.' >&2; exit 2; }
        run release_migrate
        ;;
    up)
        run release_check
        compose up -d --no-deps web
        ;;
    check) compose exec -T web python scripts/healthcheck.py ;;
    *) echo 'Usage: scripts/deploy.sh preflight|plan|migrate|up|check' >&2; exit 2 ;;
esac
