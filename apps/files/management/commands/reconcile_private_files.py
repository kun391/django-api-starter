from django.core.management.base import BaseCommand, CommandError

from apps.files.services import reconcile_files


class Command(BaseCommand):
    help = "Queue bounded cleanup for abandoned uploads, ownerless files and deletion tombstones."

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=100)

    def handle(self, *args, **options):
        try:
            count = reconcile_files(batch_size=options["batch_size"])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"Queued {count} private file cleanup(s). Run dispatch_outbox to delete objects.")
