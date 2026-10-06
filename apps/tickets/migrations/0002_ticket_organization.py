import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("organizations", "0001_initial"),
        ("tickets", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="ticket",
            name="organization",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="tickets",
                to="organizations.organization",
            ),
        ),
        migrations.AddIndex(
            model_name="ticket",
            index=models.Index(
                fields=["organization", "status", "-created_at"],
                name="tickets_org_status_idx",
            ),
        ),
    ]
