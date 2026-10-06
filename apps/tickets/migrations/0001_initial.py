# Generated manually for the Phase 13 reference business module.

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("files", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Ticket",
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
                ("title", models.CharField(max_length=120)),
                ("description", models.TextField(blank=True, max_length=4000)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("open", "Open"),
                            ("in_progress", "In Progress"),
                            ("resolved", "Resolved"),
                        ],
                        default="open",
                        max_length=20,
                    ),
                ),
                (
                    "priority",
                    models.CharField(
                        choices=[
                            ("low", "Low"),
                            ("normal", "Normal"),
                            ("high", "High"),
                        ],
                        default="normal",
                        max_length=10,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="tickets",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.CreateModel(
            name="TicketAttachment",
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
                ("attached_at", models.DateTimeField(auto_now_add=True)),
                (
                    "file",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="ticket_attachment",
                        to="files.privatefile",
                    ),
                ),
                (
                    "ticket",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="attachments",
                        to="tickets.ticket",
                    ),
                ),
            ],
            options={"ordering": ["id"]},
        ),
        migrations.AddIndex(
            model_name="ticket",
            index=models.Index(
                fields=["owner", "status", "-created_at"],
                name="tickets_owner_status_idx",
            ),
        ),
    ]
