from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="notificationdelivery",
            name="trace_context",
            field=models.JSONField(blank=True, null=True),
        ),
    ]
