import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(
            name="PrivateFile",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("purpose", models.CharField(max_length=48)),
                ("original_name", models.CharField(max_length=120)),
                ("size", models.PositiveBigIntegerField()),
                ("media_type", models.CharField(max_length=100)),
                ("sha256", models.CharField(max_length=64)),
                ("state", models.CharField(choices=[("pending", "Pending"), ("ready", "Ready"), ("deleting", "Deleting"), ("deleted", "Deleted")], default="pending", max_length=10)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("cleanup_after", models.DateTimeField(null=True)),
                ("owner", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="private_files", to=settings.AUTH_USER_MODEL)),
            ],
            options={"indexes": [models.Index(fields=["state", "cleanup_after"], name="files_cleanup_idx")]},
        ),
    ]
