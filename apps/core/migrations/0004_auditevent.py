import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0003_outboxevent"),
    ]

    operations = [
        migrations.CreateModel(
            name="AuditEvent",
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
                ("action", models.CharField(max_length=120)),
                ("subject_type", models.CharField(max_length=80)),
                ("subject_id", models.CharField(max_length=128)),
                ("actor_id", models.BigIntegerField(blank=True, null=True)),
                ("organization_id", models.UUIDField(blank=True, null=True)),
                ("request_id", models.CharField(blank=True, max_length=128)),
                ("metadata", models.JSONField(default=dict)),
                ("occurred_at", models.DateTimeField(auto_now_add=True, db_index=True)),
            ],
            options={"ordering": ["-occurred_at", "-id"]},
        ),
        migrations.AddIndex(
            model_name="auditevent",
            index=models.Index(
                fields=["organization_id", "-occurred_at"],
                name="core_audit_org_time_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="auditevent",
            index=models.Index(
                fields=["subject_type", "subject_id", "-occurred_at"],
                name="core_audit_subject_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="auditevent",
            index=models.Index(
                fields=["actor_id", "-occurred_at"],
                name="core_audit_actor_idx",
            ),
        ),
    ]
