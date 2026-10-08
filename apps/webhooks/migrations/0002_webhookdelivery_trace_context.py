from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("webhooks", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="webhookdelivery",
            name="trace_context",
            field=models.JSONField(blank=True, null=True),
        ),
    ]
