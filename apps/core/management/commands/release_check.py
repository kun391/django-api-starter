"""Deployment preflight: security, collected assets and database schema."""

from pathlib import Path

from django.conf import settings
from django.core import checks
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


class Command(BaseCommand):
    help = "Validate a production/staging image before admitting traffic. Does not write data."
    requires_system_checks: list[str] = []

    def add_arguments(self, parser):
        parser.add_argument("--allow-pending-migrations", action="store_true")

    def handle(self, *args, **options):
        if settings.SETTINGS_MODULE not in {"settings.production", "settings.staging"} or settings.DEBUG:
            raise CommandError("release_check requires production or staging settings.")
        findings = checks.run_checks(include_deployment_checks=True)
        # Preload submission is an operator choice, not a reason to disable HSTS.
        blocking = [item for item in findings if item.level >= checks.WARNING and item.id != "security.W021"]
        if blocking:
            raise CommandError("Deployment checks failed: " + ", ".join(sorted({item.id for item in blocking})))
        if any(item.id == "security.W021" for item in findings):
            self.stdout.write("HSTS preload is not enabled; confirm this deliberate domain policy.")
        if not (Path(settings.STATIC_ROOT) / "staticfiles.json").is_file():
            raise CommandError("Static manifest missing: build collected assets into the image.")
        if connection.vendor != "postgresql":
            raise CommandError("Release operations require PostgreSQL.")
        try:
            executor = MigrationExecutor(connection)
            executor.loader.check_consistent_history(connection)
            pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
        except Exception:
            raise CommandError("Database preflight failed; inspect database connectivity and migration history privately.") from None
        if pending and not options["allow_pending_migrations"]:
            raise CommandError("Pending migrations: run the serialized release_migrate job before deploying this image.")
        self.stdout.write(f"Release preflight passed; pending migrations: {len(pending)}.")
