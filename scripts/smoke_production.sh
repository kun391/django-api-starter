#!/usr/bin/env bash
# Disposable local Docker integration ONLY; never receives production credentials.
# No arguments: build and exercise all four images. IMAGE VARIANT: test a built image.
set -euo pipefail
cd "$(dirname "$0")/.."
tmp=$(mktemp -d)
prefix="starter-smoke-$$-$(date +%s)"
network="$prefix-net"
db="$prefix-db"
web="$prefix-web"
volume="$prefix-files"
cleanup() {
    code=$?
    if [ "$code" -ne 0 ]; then docker logs "$web" 2>/dev/null || true; fi
    docker rm -f "$web" "$db" >/dev/null 2>&1 || true
    docker volume rm "$volume" >/dev/null 2>&1 || true
    docker network rm "$network" >/dev/null 2>&1 || true
    rm -rf "$tmp"
    exit "$code"
}
trap cleanup EXIT
if [ "$#" -eq 0 ]; then
    variants=(runtime runtime-async runtime-storage runtime-full)
    for target in "${variants[@]}"; do
        docker build --target "$target" --build-arg "RELEASE_SHA=${GITHUB_SHA:-development}" \
            --build-arg RELEASE_VERSION=ci --tag "$prefix:$target" .
    done
elif [ "$#" -eq 2 ]; then
    variants=("$2")
    case "$2" in runtime|runtime-async|runtime-storage|runtime-full) ;; *) exit 2;; esac
else
    echo 'Usage: scripts/smoke_production.sh [IMAGE VARIANT]' >&2
    exit 2
fi
python3 - "$tmp/runtime.env" <<'PY'
import secrets
import sys
from pathlib import Path
Path(sys.argv[1]).write_text('\n'.join([
    'DJANGO_SETTINGS_MODULE=settings.production',
    'SECRET_KEY=' + secrets.token_urlsafe(64),
    'ALLOWED_HOSTS=api.example.test',
    'POSTGRES_HOST=db', 'POSTGRES_DB=smoke', 'POSTGRES_USER=smoke',
    'POSTGRES_PASSWORD=' + secrets.token_urlsafe(32), 'POSTGRES_SSLMODE=disable',
    'TRUST_PROXY_SSL_HEADER=True', 'PRIVATE_FILE_BACKEND=filesystem',
    'REGISTRATION_RATE_LIMIT=100', 'AUTH_LOGIN_IP_RATE_LIMIT=100',
    'WEB_CONCURRENCY=2', 'WEB_THREADS=2', 'WEB_GRACEFUL_TIMEOUT=5',
    'SENTRY_DSN=', 'PERFORMANCE_CACHE_BACKEND=django.core.cache.backends.dummy.DummyCache',
]) + '\n')
Path(sys.argv[1]).chmod(0o600)
PY
docker network create "$network" >/dev/null
docker volume create "$volume" >/dev/null
docker run -d --name "$db" --network "$network" --network-alias db \
    --env-file "$tmp/runtime.env" --health-cmd 'pg_isready -U smoke -d smoke' \
    --health-interval 2s --health-timeout 3s --health-retries 30 postgres:17-alpine >/dev/null
for attempt in $(seq 1 60); do
    if [ "$(docker inspect --format '{{.State.Health.Status}}' "$db")" = healthy ]; then break; fi
    sleep 1
done
[ "$(docker inspect --format '{{.State.Health.Status}}' "$db")" = healthy ]
run() {
    docker run --rm --network "$network" --env-file "$tmp/runtime.env" --read-only \
        --cap-drop ALL --security-opt no-new-privileges:true \
        --tmpfs /tmp:rw,noexec,nosuid,size=128m,mode=1777 \
        --mount "type=volume,source=$volume,target=/app/private-files" "$image" "$@"
}
first=true
for variant in "${variants[@]}"; do
    image="${1:-$prefix:$variant}"
    run python -c "import importlib.util, os, shutil; from pathlib import Path; assert os.getuid() == 10001; assert not os.access('/app/manage.py', os.W_OK); assert shutil.which('uv') is None; assert importlib.util.find_spec('pytest') is None; assert Path('/app/staticfiles/staticfiles.json').is_file(); assert bool(importlib.util.find_spec('celery')) == ('$variant' in ('runtime-async', 'runtime-full')); assert bool(importlib.util.find_spec('storages')) == ('$variant' in ('runtime-storage', 'runtime-full'))"
    if [ "$first" = true ]; then
        if run python manage.py release_check >"$tmp/preflight.log" 2>&1; then
            echo 'Preflight incorrectly accepted an unmigrated database.' >&2; exit 1
        fi
        grep -q 'Pending migrations' "$tmp/preflight.log"
        run python manage.py release_check --allow-pending-migrations
        run python manage.py release_migrate --plan
        run python manage.py release_migrate
        first=false
    fi
    run python manage.py release_check
    run python manage.py release_migrate
    docker run -d --name "$web" --network "$network" --env-file "$tmp/runtime.env" \
        --read-only --cap-drop ALL --security-opt no-new-privileges:true \
        --tmpfs /tmp:rw,noexec,nosuid,size=128m,mode=1777 \
        --mount "type=volume,source=$volume,target=/app/private-files" \
        -p 127.0.0.1::8000 "$image" \
        gunicorn --config config/gunicorn.py apps.core.wsgi:application >/dev/null
    for attempt in $(seq 1 60); do
        if docker exec "$web" python scripts/healthcheck.py; then break; fi
        sleep 1
    done
    docker exec "$web" python scripts/healthcheck.py
    port=$(docker port "$web" 8000/tcp | sed 's/.*://')
    python3 scripts/smoke_http.py "$port"
    # A separate process must be able to clean files written by the API.
    run python manage.py reconcile_private_files --batch-size 100
    run python manage.py dispatch_outbox --batch-size 100
    run python -c "from pathlib import Path; assert not any(p.is_file() for p in Path('/app/private-files').rglob('*'))"
    docker stop --time 10 "$web" >/dev/null
    [ "$(docker inspect --format '{{.State.ExitCode}}' "$web")" = 0 ]
    docker rm "$web" >/dev/null
    echo "Production container passed: $variant"
done
