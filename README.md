# Django API Template

A production-ready Django API project template with Docker Compose, comprehensive development tools, and best practices.

## Features

- **Django 6.1+** with REST Framework
- **Docker Compose** for development environment
- **PostgreSQL** database
- **RabbitMQ** for message queuing
- **Celery** for background tasks
- **Comprehensive Makefile** for development commands
- **Code quality tools**: Ruff, MyPy
- **Environment-specific settings**
- **Production-ready configuration**

## Requirements

- [Docker](https://docs.docker.com/get-started/get-docker/)
- [Docker Compose](https://docs.docker.com/compose/install/)\n- [uv](https://docs.astral.sh/uv/) for host-side Python workflows
- [Make](https://www.gnu.org/software/make/)

## Quick Start

1. **Clone and setup**
   ```bash
   git clone <your-repo-url>
   cd django-api-template
   cp .env.example .env
   # Edit .env with your configuration
   ```

2. **Start development environment**
   ```bash
   make dev
   ```

3. **Apply migrations**
   ```bash
   make migrate
   ```

4. **Create superuser**
   ```bash
   make exec-web
   python manage.py createsuperuser
   ```

5. **Access the application**
   - API: http://localhost:5001
   - Admin: http://localhost:5001/admin
   - RabbitMQ Management: http://localhost:15672 (admin/admin)

## Dependency Management

`pyproject.toml` is the dependency source of truth and `uv.lock` is committed
for reproducible environments.

```bash
uv sync --locked --group dev
uv sync --locked --extra async --group dev
uv lock --upgrade
```

Runtime extras are opt-in:
- `async`: Celery + Redis client
- `storage`: django-storages + boto3

Do not add `requirements.txt` files.

## Development Workflow

### Running Tests
- `make test` - Run all tests in parallel
- `make test-linear` - Run tests sequentially
- `make test-<pattern>` - Run specific tests matching a pattern

### Database Operations
- `make migrate` - Apply database migrations
- `make makemigrations` - Create new migrations after model changes
- `make dbshell` - Access the database shell for debugging

### Code Quality
- `make check` - Run linters and type checks
- `make format` - Format code with ruff
- `make lint` - Run linting checks

### Docker Development
- `make up` or `make dev` - Start the development environment
- `make down` - Stop all services
- `make logs` - View all container logs
- `make logs-<service>` - View logs for a specific service
- `make exec-<service>` - Execute bash in a container

### Background Tasks
- `make worker` - Run Celery workers
- `make beat` - Run Celery beat scheduler
- `make flower` - Run Celery monitoring

## Project Structure

```
django-api-template/
├── apps/                    # Django applications
│   ├── core/               # Core functionality
│   └── accounts/           # Identity, registration, and profiles
├── config/                 # Gunicorn configuration
├── settings/               # Django settings
│   ├── base.py            # Base settings
│   ├── local.py           # Local development
│   ├── staging.py         # Staging environment
│   └── production.py      # Production environment
├── requirements/           # Python dependencies
│   ├── base.txt           # Base requirements
│   ├── local.txt          # Development requirements
│   └── production.txt     # Production requirements
├── bin/                   # Scripts and utilities
├── docs/                  # Documentation
├── tests/                 # Test configuration
├── Dockerfile             # Docker configuration
├── docker-compose.yml     # Docker Compose services
├── Makefile               # Development commands
├── pyproject.toml         # Dependency ranges and tool configuration\n├── uv.lock                # Exact cross-platform dependency lock
├── manage.py              # Django management script
└── README.md              # This file
```

## Environment Variables

Create a `.env` file based on `.env.example`:

```bash
# Django
DEBUG=True
SECRET_KEY=your-secret-key-here
DJANGO_SETTINGS_MODULE=settings.local

# Database
DATABASE_URL=postgresql://user:password@db:5432/dbname
POSTGRES_USER=user
POSTGRES_PASSWORD=password
POSTGRES_DB=dbname

# Redis/Celery
REDIS_URL=redis://redis:6379/0

# RabbitMQ
RABBITMQ_URL=amqp://admin:admin@rabbitmq:5672/

# External Services
SENTRY_DSN=your-sentry-dsn
```

## Deployment

### Staging
```bash
make deploy-staging
```

### Production
```bash
make deploy-production
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests: `make test`
5. Check code quality: `make check`
6. Submit a pull request

## License

This project is licensed under the MIT License - see the LICENSE file for details.