from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("files", "0002_privatefile_deleted_at"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="privatefile",
            index=models.Index(
                fields=["state", "deleted_at"],
                name="files_deleted_retention_idx",
            ),
        ),
    ]
