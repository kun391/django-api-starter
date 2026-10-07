import uuid

import django.db.models.deletion
from django.db import migrations, models
from django.utils import timezone


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("organizations", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="WebhookSubscription",
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
                ("url", models.URLField(max_length=2048)),
                ("events", models.JSONField(default=list)),
                ("is_active", models.BooleanField(default=True)),
                ("secret_version", models.PositiveIntegerField(default=1)),
                ("created_by_id", models.BigIntegerField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="webhook_subscriptions",
                        to="organizations.organization",
                    ),
                ),
            ],
            options={
                "ordering": ["created_at", "id"],
                "indexes": [
                    models.Index(
                        fields=["organization", "is_active", "created_at"],
                        name="webhooks_sub_org_active_idx",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="WebhookDelivery",
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
                ("body", models.JSONField()),
                ("dedupe_key", models.CharField(max_length=128, unique=True)),
                ("replay_of_id", models.UUIDField(blank=True, null=True)),
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
                ("response_status", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("last_error_code", models.CharField(blank=True, max_length=128)),
                (
                    "locked_until",
                    models.DateTimeField(blank=True, db_index=True, null=True),
                ),
                ("lock_token", models.UUIDField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "subscription",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="deliveries",
                        to="webhooks.webhooksubscription",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at", "-id"],
                "indexes": [
                    models.Index(
                        fields=["subscription", "-created_at"],
                        name="webhooks_delivery_sub_idx",
                    ),
                    models.Index(
                        fields=[
                            "delivered_at",
                            "failed_at",
                            "cancelled_at",
                            "next_attempt_at",
                        ],
                        name="webhooks_delivery_ready_idx",
                    ),
                ],
            },
        ),
    ]
