import json

from django.core.management.base import BaseCommand

from apps.operations.services import snapshot_dict


class Command(BaseCommand):
    help = "Print a machine-readable snapshot of durable background queues."

    def handle(self, *args, **options):
        self.stdout.write(json.dumps(snapshot_dict(), sort_keys=True))
