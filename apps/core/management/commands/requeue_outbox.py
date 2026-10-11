"""Requeue one dead-lettered subscriber without replaying successful siblings."""

import uuid

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.core.models import OutboxDelivery, OutboxEvent


class Command(BaseCommand):
    help = "Requeue a dead-lettered outbox subscriber (or legacy event)."

    def add_arguments(self, parser):
        parser.add_argument("event_id")
        parser.add_argument(
            "--subscriber-id",
            help="Stable subscriber ID; required when multiple subscribers failed.",
        )

    def handle(self, *args, **options):
        try:
            event_id = uuid.UUID(options["event_id"])
        except ValueError as exc:
            raise CommandError("event_id must be a valid UUID") from exc

        now = timezone.now()
        with transaction.atomic():
            event = OutboxEvent.objects.select_for_update().filter(pk=event_id).first()
            if event is None or event.published_at is not None:
                raise CommandError("No unpublished outbox event found.")
            if event.locked_until is not None and event.locked_until > now:
                raise CommandError("Active outbox lease; retry when worker finishes.")

            targets = list(
                OutboxDelivery.objects.select_for_update().filter(
                    event=event,
                    dead_lettered_at__isnull=False,
                    succeeded_at__isnull=True,
                )
            )
            requested_id = options["subscriber_id"]
            if requested_id:
                targets = [d for d in targets if d.subscriber_id == requested_id]
            if len(targets) > 1:
                raise CommandError(
                    "Multiple dead-lettered subscribers; specify --subscriber-id."
                )
            if not targets:
                if requested_id or event.dead_lettered_at is None or event.deliveries.exists():
                    raise CommandError("No matching dead-lettered subscriber found.")
                # Backwards-compatible requeue for an unhandled legacy event.
                event.dead_lettered_at = None
                event.available_at = now
                event.locked_until = None
                event.lock_token = None
                event.last_error_code = ""
                event.save(update_fields=[
                    "dead_lettered_at", "available_at", "locked_until",
                    "lock_token", "last_error_code",
                ])
                self.stdout.write("Requeued legacy event without subscribers.")
                return

            target = targets[0]
            target.dead_lettered_at = None
            target.available_at = now
            target.attempts = 0
            target.lock_token = None
            target.locked_until = None
            target.last_error_code = ""
            target.save(update_fields=[
                "dead_lettered_at", "available_at", "attempts",
                "lock_token", "locked_until", "last_error_code",
            ])
            event.dead_lettered_at = None
            event.available_at = now
            event.locked_until = None
            event.lock_token = None
            event.last_error_code = ""
            event.save(update_fields=[
                "dead_lettered_at", "available_at", "locked_until",
                "lock_token", "last_error_code",
            ])
        self.stdout.write(f"Requeued subscriber {target.subscriber_id}.")
