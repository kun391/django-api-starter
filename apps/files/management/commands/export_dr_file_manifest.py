"""Export private file metadata from an isolated restored Django database.

Output is a verification manifest, not a database backup. No writes are made.
"""
import json
import os
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.files.models import PrivateFile


class Command(BaseCommand):
    help = "Export read-only private-file metadata for DR consistency checks."

    def add_arguments(self, parser):
        parser.add_argument("--output", required=True, type=Path)

    def handle(self, *args, **options):
        path = options["output"]
        if path.exists() or path.is_symlink():
            raise CommandError("Output already exists")
        records = list(
            PrivateFile.objects.order_by("id").values(
                "id", "state", "size", "sha256"
            )
        )
        data = {
            "schema_version": 1,
            "files": [
                {
                    "id": str(item["id"]),
                    "state": item["state"],
                    "size": item["size"],
                    "sha256": item["sha256"],
                }
                for item in records
            ],
        }
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(data, output, indent=2)
            output.write("\n")
        self.stdout.write(f"Exported {len(records)} private-file records")
