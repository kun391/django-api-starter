"""Explicitly requeue a dead-lettered outbox event after remediation."""

import uuid

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.core.models import OutboxEvent


class Command(BaseCommand):
    help = "Requeue one dead-lettered outbox event by UUID."

    def add_arguments(self, parser):
        parser.add_argument("event_id")

    def handle(self, *args, **options):
        try:
            event_id = uuid.UUID(options["event_id"])
        except ValueError as exc:
            raise CommandError("event_id must be a valid UUID") from exc

        updated = OutboxEvent.objects.filter(
            pk=event_id,
            dead_lettered_at__isnull=False,
            published_at__isnull=True,
        ).update(
            dead_lettered_at=None,
            available_at=timezone.now(),
            locked_until=None,
            lock_token=None,
            last_error_code="",
        )
        if not updated:
            raise CommandError("No dead-lettered unpublished event found.")
        self.stdout.write("Requeued 1 outbox event.")
