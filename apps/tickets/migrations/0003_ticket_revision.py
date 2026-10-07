from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tickets", "0002_ticket_organization"),
    ]

    operations = [
        migrations.AddField(
            model_name="ticket",
            name="revision",
            field=models.PositiveBigIntegerField(default=1),
        ),
    ]
