import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0004_auditevent"),
    ]

    operations = [
        migrations.CreateModel(
            name="OutboxDelivery",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "event",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="deliveries",
                        to="core.outboxevent",
                    ),
                ),
                ("subscriber_id", models.CharField(max_length=96)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                (
                    "available_at",
                    models.DateTimeField(
                        db_index=True, default=django.utils.timezone.now
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "succeeded_at",
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
                "constraints": [
                    models.UniqueConstraint(
                        fields=("event", "subscriber_id"),
                        name="core_outbox_subscriber_uq",
                    )
                ],
                "indexes": [
                    models.Index(
                        fields=[
                            "succeeded_at",
                            "dead_lettered_at",
                            "available_at",
                        ],
                        name="core_outbox_delivery_ready",
                    )
                ],
            },
        )
    ]
