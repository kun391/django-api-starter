# syntax=docker/dockerfile:1.7

FROM python:3.14-slim-bookworm AS python-base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH="/app/.venv/bin:$PATH" \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libpq5 \
    && rm -rf /var/lib/apt/lists/*

FROM python-base AS uv-base
COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /uvx /bin/

FROM uv-base AS prod-deps
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

FROM uv-base AS async-deps
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --extra async --no-install-project

FROM uv-base AS storage-deps
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --extra storage --no-install-project

FROM uv-base AS telemetry-deps
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --extra telemetry --no-install-project

FROM uv-base AS full-deps
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --extra async --extra storage --extra telemetry --no-install-project

# Static collection is offline and needs no real runtime secret, DB or bucket.
FROM prod-deps AS static-build
COPY . /app
RUN DJANGO_SETTINGS_MODULE=settings.build python manage.py collectstatic --noinput

FROM uv-base AS dev-deps
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --group dev --no-install-project

FROM uv-base AS dev-async-deps
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --extra async --group dev --no-install-project

FROM python-base AS runtime-base
ARG RELEASE_SHA=development
ARG RELEASE_VERSION=development
ARG SOURCE_URL=https://github.com/kun391/django-api-starter
LABEL org.opencontainers.image.source=$SOURCE_URL \
    org.opencontainers.image.revision=$RELEASE_SHA \
    org.opencontainers.image.version=$RELEASE_VERSION
ENV DJANGO_SETTINGS_MODULE=settings.production
RUN groupadd --gid 10001 django \
    && useradd --uid 10001 --gid django --no-create-home --home-dir /app django
COPY . /app
COPY --from=static-build /app/staticfiles /app/staticfiles
RUN mkdir -p /app/private-files \
    && chown 10001:10001 /app/private-files \
    && chmod 700 /app/private-files
# Source and collected assets remain root-owned; the runtime user cannot edit code.
USER 10001:10001
EXPOSE 8000

FROM runtime-base AS runtime
COPY --from=prod-deps /app/.venv /app/.venv
CMD ["gunicorn", "--config", "config/gunicorn.py", "apps.core.wsgi:application"]

FROM runtime-base AS runtime-async
COPY --from=async-deps /app/.venv /app/.venv
CMD ["celery", "-A", "apps.core.celery:app", "worker", "--loglevel=info"]

FROM runtime-base AS runtime-storage
COPY --from=storage-deps /app/.venv /app/.venv
CMD ["gunicorn", "--config", "config/gunicorn.py", "apps.core.wsgi:application"]

FROM runtime-base AS runtime-telemetry
COPY --from=telemetry-deps /app/.venv /app/.venv
CMD ["gunicorn", "--config", "config/gunicorn.py", "apps.core.wsgi:application"]

FROM runtime-base AS runtime-full
COPY --from=full-deps /app/.venv /app/.venv
CMD ["gunicorn", "--config", "config/gunicorn.py", "apps.core.wsgi:application"]

FROM uv-base AS development-base
RUN apt-get update \
    && apt-get install -y --no-install-recommends netcat-openbsd postgresql-client \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system django \
    && useradd --system --gid django --home-dir /app django
COPY . /app
RUN mkdir -p /app/staticfiles /app/media /app/private-files \
    && chown -R django:django /app
USER django
EXPOSE 8000

FROM development-base AS development
COPY --from=dev-deps --chown=django:django /app/.venv /app/.venv
CMD ["./bin/run_local.sh"]

FROM development-base AS development-async
COPY --from=dev-async-deps --chown=django:django /app/.venv /app/.venv
CMD ["celery", "-A", "apps.core.celery:app", "worker", "--loglevel=info"]
