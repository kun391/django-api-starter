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
    && apt-get install -y --no-install-recommends \
        curl \
        libpq5 \
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

FROM uv-base AS dev-deps
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --extra async --group dev --no-install-project

FROM python-base AS runtime-base
RUN groupadd --system django \
    && useradd --system --gid django --home-dir /app django
COPY . /app
RUN mkdir -p /app/staticfiles /app/media \
    && chown -R django:django /app
USER django
EXPOSE 8000

FROM runtime-base AS runtime
COPY --from=prod-deps --chown=django:django /app/.venv /app/.venv
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "apps.core.wsgi:application"]

FROM runtime-base AS runtime-async
COPY --from=async-deps --chown=django:django /app/.venv /app/.venv
CMD ["celery", "-A", "apps.core.celery:app", "worker", "--loglevel=info"]

FROM uv-base AS development
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        netcat-openbsd \
        postgresql-client \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system django \
    && useradd --system --gid django --home-dir /app django
COPY --from=dev-deps --chown=django:django /app/.venv /app/.venv
COPY . /app
RUN mkdir -p /app/staticfiles /app/media \
    && chown -R django:django /app
USER django
EXPOSE 8000
CMD ["./bin/run_local.sh"]
