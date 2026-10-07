import json

from django.core.management.base import BaseCommand, CommandError

from apps.retention.services import CATEGORY_BY_NAME, purge_retained_data


class Command(BaseCommand):
    help = (
        "Report or delete one bounded batch of retention-eligible history. "
        "Deletion requires --confirm."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--category",
            choices=["all", *CATEGORY_BY_NAME],
            default="all",
        )
        parser.add_argument("--batch-size", type=int, default=1000)
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Actually delete eligible rows. Without this flag the command is dry-run.",
        )

    def handle(self, *args, **options):
        try:
            results = purge_retained_data(
                category=options["category"],
                batch_size=options["batch_size"],
                confirm=options["confirm"],
            )
        except (ValueError, Exception) as exc:
            from django.core.exceptions import ImproperlyConfigured

            if isinstance(exc, (ValueError, ImproperlyConfigured)):
                raise CommandError(str(exc)) from exc
            raise

        payload = {
            "mode": "delete" if options["confirm"] else "dry-run",
            "results": [
                {
                    "category": result.category,
                    "enabled": result.enabled,
                    "candidates": result.candidates,
                    "deleted": result.deleted,
                }
                for result in results
            ],
        }
        self.stdout.write(json.dumps(payload, sort_keys=True))
