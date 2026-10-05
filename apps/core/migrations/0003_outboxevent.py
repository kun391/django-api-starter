import uuid

import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_securitythrottlebucket"),
    ]

    operations = [
        migrations.CreateModel(
            name="OutboxEvent",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("topic", models.CharField(max_length=200)),
                ("version", models.PositiveSmallIntegerField(default=1)),
                ("payload", models.JSONField()),
                ("metadata", models.JSONField(default=dict)),
                ("occurred_at", models.DateTimeField(auto_now_add=True)),
                (
                    "available_at",
                    models.DateTimeField(
                        db_index=True,
                        default=django.utils.timezone.now,
                    ),
                ),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                (
                    "published_at",
                    models.DateTimeField(blank=True, db_index=True, null=True),
                ),
                (
                    "dead_lettered_at",
                    models.DateTimeField(blank=True, db_index=True, null=True),
                ),
                (
                    "locked_until",
                    models.DateTimeField(blank=True, db_index=True, null=True),
                ),
                ("lock_token", models.UUIDField(blank=True, null=True)),
                ("last_error_code", models.CharField(blank=True, max_length=128)),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=[
                            "published_at",
                            "dead_lettered_at",
                            "available_at",
                        ],
                        name="core_outbox_ready_idx",
                    )
                ],
            },
        ),
    ]
