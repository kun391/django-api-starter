import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="NotificationPreference",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("topic", models.CharField(max_length=200)),
                ("email_enabled", models.BooleanField(default=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="notification_preferences",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["topic", "id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("user", "topic"),
                        name="notifications_unique_user_topic",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="NotificationDelivery",
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
                ("source_event_id", models.UUIDField()),
                ("event_topic", models.CharField(max_length=200)),
                ("event_version", models.PositiveSmallIntegerField(default=1)),
                ("recipient_user_id_snapshot", models.BigIntegerField()),
                ("recipient_email", models.EmailField(max_length=254)),
                ("template_slug", models.CharField(max_length=120)),
                ("template_context", models.JSONField(default=dict)),
                ("dedupe_key", models.CharField(max_length=128, unique=True)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                (
                    "next_attempt_at",
                    models.DateTimeField(db_index=True, default=timezone.now),
                ),
                ("last_attempt_at", models.DateTimeField(blank=True, null=True)),
                (
                    "delivered_at",
                    models.DateTimeField(blank=True, db_index=True, null=True),
                ),
                (
                    "failed_at",
                    models.DateTimeField(blank=True, db_index=True, null=True),
                ),
                (
                    "cancelled_at",
                    models.DateTimeField(blank=True, db_index=True, null=True),
                ),
                ("last_error_code", models.CharField(blank=True, max_length=128)),
                (
                    "locked_until",
                    models.DateTimeField(blank=True, db_index=True, null=True),
                ),
                ("lock_token", models.UUIDField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "recipient_user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="notification_deliveries",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at", "-id"],
                "indexes": [
                    models.Index(
                        fields=["recipient_user", "-created_at"],
                        name="notifications_user_time_idx",
                    ),
                    models.Index(
                        fields=[
                            "delivered_at",
                            "failed_at",
                            "cancelled_at",
                            "next_attempt_at",
                        ],
                        name="notifications_ready_idx",
                    ),
                ],
            },
        ),
    ]
