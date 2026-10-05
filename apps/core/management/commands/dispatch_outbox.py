"""Dispatch one outbox batch without requiring Celery."""

from django.core.management.base import BaseCommand, CommandError

from apps.core.outbox import process_outbox_batch


class Command(BaseCommand):
    help = "Dispatch one bounded batch of ready transactional-outbox events."

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=None)

    def handle(self, *args, **options):
        size = options["batch_size"]
        if size is not None and not 1 <= size <= 1000:
            raise CommandError("batch-size must be between 1 and 1000")
        delivered = process_outbox_batch(batch_size=size)
        self.stdout.write(f"Delivered {delivered} outbox events.")
