from django.core.management.base import BaseCommand, CommandError

from apps.notifications.services import process_delivery_batch


class Command(BaseCommand):
    help = "Dispatch one bounded batch of ready notification deliveries."

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=None)

    def handle(self, *args, **options):
        size = options["batch_size"]
        if size is not None and not 1 <= size <= 1000:
            raise CommandError("batch-size must be between 1 and 1000")
        delivered = process_delivery_batch(batch_size=size)
        self.stdout.write(f"Delivered {delivered} notifications.")
