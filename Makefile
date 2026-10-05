# Makefile for Django API Template

# Configuration
SHELL := /bin/bash
PROJECT_NAME := django-api-template

# Docker Compose configuration
COMPOSE_FILES := compose.yaml
COMPOSE := docker compose $(foreach f,$(subst :, ,$(COMPOSE_FILES)),-f $(f))
COMPOSE_RUN := $(COMPOSE) run --rm
COMPOSE_EXEC := $(COMPOSE) exec

# Colorful output
GREEN := \033[0;32m
YELLOW := \033[0;33m
RED := \033[0;31m
NC := \033[0m

# Phony targets
.PHONY: help up down build start stop restart logs ps exec \
        check format test migrate makemigrations dbshell clean prune \
        worker beat async-up async-down shell collectstatic architecture module ai-context

# Default target
help:
	@echo -e "$(GREEN)Available targets:$(NC)"
	@echo -e "  ${YELLOW}Development:${NC}"
	@echo "    up         - Start services in detached mode"
	@echo "    down       - Stop and remove containers"
	@echo "    build      - Build or rebuild services"
	@echo "    start      - Start existing containers"
	@echo "    stop       - Stop running containers"
	@echo "    restart    - Restart services"
	@echo "    dev        - Start development environment"
	@echo -e "  ${YELLOW}Logs & Monitoring:${NC}"
	@echo "    logs       - View output from containers"
	@echo "    logs-%     - View logs for a specific service"
	@echo "    ps         - List containers"
	@echo -e "  ${YELLOW}Development Tools:${NC}"
	@echo "    exec-%     - Execute bash in a specific service"
	@echo "    shell      - Open Django shell"
	@echo "    check      - Run linters and type checkers"
	@echo "    format     - Format code"
	@echo "    lint       - Run linting checks"
	@echo -e "  ${YELLOW}Database:${NC}"
	@echo "    migrate    - Apply database migrations"
	@echo "    makemigrations - Create new migrations"
	@echo "    dbshell    - Open database shell"
	@echo "    reset-db   - Reset database (drop and recreate)"
	@echo -e "  ${YELLOW}Testing:${NC}"
	@echo "    test       - Run all tests"
	@echo "    test-linear - Run tests sequentially"
	@echo "    test-%     - Run specific tests matching pattern"
	@echo "    coverage   - Run tests with coverage"
	@echo -e "  ${YELLOW}Background Tasks:${NC}"
	@echo "    worker     - Run Celery worker"
	@echo "    beat       - Run Celery beat scheduler"
	@echo "    async-up   - Start optional async services"
	@echo "    async-down - Stop optional async services"
	@echo -e "  ${YELLOW}Static Files:${NC}"
	@echo "    collectstatic - Collect static files"
	@echo -e "  ${YELLOW}Cleanup:${NC}"
	@echo "    clean      - Remove stopped containers"
	@echo "    prune      - Remove all unused containers, networks, and volumes"

# Core Docker Compose commands
up:
	$(COMPOSE) up -d

forceup-%:
	$(COMPOSE) up -d --build $*

dev:
	make stop
	make up

down:
	$(COMPOSE) down

build:
	$(COMPOSE) build

start:
	$(COMPOSE) start

stop:
	$(COMPOSE) stop

restart:
	$(COMPOSE) restart

# Logs and monitoring
logs:
	$(COMPOSE) logs -f

logs-%:
	$(COMPOSE) logs -f $*

ps:
	$(COMPOSE) ps

# Development tools
exec-%:
	$(COMPOSE_EXEC) $* bash

shell:
	$(COMPOSE_EXEC) web python manage.py shell

# Code quality
check:
	@echo -e "$(GREEN)Running code quality checks...$(NC)"
	$(COMPOSE_EXEC) web ruff check .
	$(COMPOSE_EXEC) web mypy .
	
format:
	@echo -e "$(GREEN)Formatting code...$(NC)"
	$(COMPOSE_EXEC) web ruff format .

lint:
	@echo -e "$(GREEN)Running linting checks...$(NC)"
	$(COMPOSE_EXEC) web ruff check . --fix

architecture:
	$(COMPOSE_EXEC) web python scripts/check_architecture.py

module:
	@test -n "$(name)" || (echo "name is required"; exit 1)
	$(COMPOSE_EXEC) web python scripts/create_module.py "$(name)" --type "$(or $(type),crud)"

ai-context:
	@test -n "$(name)" || (echo "name is required"; exit 1)
	$(COMPOSE_EXEC) web python scripts/build_ai_context.py "$(name)"

# Database operations
migrate:
	$(COMPOSE_EXEC) web python manage.py migrate

makemigrations:
	$(COMPOSE_EXEC) web python manage.py makemigrations

dbshell:
	$(COMPOSE_EXEC) web python manage.py dbshell

reset-db:
	@echo -e "$(RED)Warning: This will delete all data!$(NC)"
	@read -p "Are you sure? [y/N] " -n 1 -r; \
	if [[ $$REPLY =~ ^[Yy]$$ ]]; then \
		$(COMPOSE) down; \
		$(COMPOSE) volume rm django-api-template_postgres_data || true; \
		$(COMPOSE) up -d db; \
		sleep 5; \
		$(COMPOSE_EXEC) web python manage.py migrate; \
		echo -e "$(GREEN)Database reset complete!$(NC)"; \
	else \
		echo -e "$(YELLOW)Database reset cancelled.$(NC)"; \
	fi

# Testing
test:
	$(COMPOSE_EXEC) web pytest -n auto

test-linear:
	$(COMPOSE_EXEC) web pytest

test-%:
	$(COMPOSE_EXEC) web pytest -k "$*"

coverage:
	$(COMPOSE_EXEC) web pytest --cov=apps --cov-report=term-missing --cov-report=html

# Background tasks
async-up:
	$(COMPOSE) --profile async up -d

async-down:
	$(COMPOSE) --profile async down

worker:
	$(COMPOSE_EXEC) web celery -A apps.core.celery:app worker --loglevel=info

beat:
	$(COMPOSE_EXEC) web celery -A apps.core.celery:app beat --loglevel=info

# Static files
collectstatic:
	$(COMPOSE_EXEC) web python manage.py collectstatic --noinput

# Cleanup
clean:
	$(COMPOSE) rm -f

prune:
	docker system prune -a --volumes

# Management commands
createsuperuser:
	$(COMPOSE_EXEC) web python manage.py createsuperuser

loaddata:
	$(COMPOSE_EXEC) web python manage.py loaddata

dumpdata:
	$(COMPOSE_EXEC) web python manage.py dumpdata

# Development utilities
install-deps:
	$(COMPOSE_EXEC) web uv sync --locked --extra async --group dev

update-deps:
	$(COMPOSE_EXEC) web uv lock --upgrade && $(COMPOSE_EXEC) web uv sync --extra async --group dev

# Health checks
health:
	@echo -e "$(GREEN)Checking service health...$(NC)"
	@curl -f http://localhost:5001/health/ || echo -e "$(RED)Web service is down$(NC)"

# Backup and restore
backup:
	@echo -e "$(GREEN)Creating database backup...$(NC)"
	$(COMPOSE_EXEC) db pg_dump -U postgres django_api > backup_$(shell date +%Y%m%d_%H%M%S).sql

restore:
	@echo -e "$(RED)Warning: This will overwrite existing data!$(NC)"
	@read -p "Enter backup file name: " backup_file; \
	$(COMPOSE_EXEC) db psql -U postgres django_api < $$backup_file