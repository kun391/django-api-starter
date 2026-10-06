"""Serialize a forward-only release migration against one PostgreSQL database."""

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

# Stable across code revisions and shared by all releases of this starter.
MIGRATION_LOCK_ID = 62484319012711


class Command(BaseCommand):
    help = "Apply pending migrations once, under a PostgreSQL session advisory lock."
    requires_system_checks: list[str] = []

    def add_arguments(self, parser):
        parser.add_argument("--plan", action="store_true")
        parser.add_argument("--lock-timeout-ms", type=int, default=10000)
        parser.add_argument("--statement-timeout-ms", type=int, default=300000)

    def handle(self, *args, **options):
        if connection.vendor != "postgresql" or connection.in_atomic_block or not connection.get_autocommit():
            raise CommandError("release_migrate requires an autocommit PostgreSQL connection, without transaction pooling.")
        lock_ms, statement_ms = options["lock_timeout_ms"], options["statement_timeout_ms"]
        if not 1 <= lock_ms <= 60000 or not 1 <= statement_ms <= 3600000:
            raise CommandError("Migration timeouts must be positive and bounded (lock <= 60000, statement <= 3600000 ms).")
        acquired = False
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_try_advisory_lock(%s)", [MIGRATION_LOCK_ID])
                acquired = bool(cursor.fetchone()[0])
                if not acquired:
                    raise CommandError("Another release migration is running. Retry after it finishes.")
                cursor.execute("SELECT set_config('lock_timeout', %s, false)", [str(lock_ms)])
                cursor.execute("SELECT set_config('statement_timeout', %s, false)", [str(statement_ms)])
            # Native per-migration transactions remain intact. No outer atomic
            # block: non-atomic/concurrent-index migrations must stay possible.
            call_command("migrate", interactive=False, plan=options["plan"], verbosity=options["verbosity"], stdout=self.stdout)
        finally:
            # Closing also releases the session lock after any error and resets
            # session timeout settings. The next command starts a fresh session.
            if acquired:
                connection.close()
