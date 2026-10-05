"""Delete one bounded batch; invoke periodically from the deployment scheduler."""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.core.models import IdempotencyRecord


class Command(BaseCommand):
    help = "Delete expired idempotency records (one batch; default 1000)."

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=1000)

    def handle(self, *args, **options):
        size = options["batch_size"]
        if not 1 <= size <= 10000:
            raise CommandError("batch-size must be between 1 and 10000")
        ids = list(IdempotencyRecord.objects.filter(expires_at__lte=timezone.now()).order_by("expires_at").values_list("pk", flat=True)[:size])
        deleted, _ = IdempotencyRecord.objects.filter(pk__in=ids).delete()
        self.stdout.write(f"Deleted {deleted} expired idempotency records.")
