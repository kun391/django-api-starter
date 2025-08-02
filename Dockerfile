FROM python:3.13-slim-bullseye

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Set work directory
WORKDIR /app

# Install system dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        git \
        libpq-dev \
        netcat-traditional \
        postgresql-client \
        && rm -rf /var/lib/apt/lists/* \
        && apt-get clean

# Create a non-root user
RUN groupadd -r django && useradd -r -g django django

# Install Python dependencies
COPY requirements/ requirements/
RUN pip install --upgrade pip \
    && pip install -r requirements/local.txt

# Copy project
COPY . /app/

# Create necessary directories
RUN mkdir -p /app/static /app/media /app/logs \
    && chown -R django:django /app

# Switch to non-root user
USER django

# Expose port
EXPOSE 8000

# Default command
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "apps.core.wsgi:application"]