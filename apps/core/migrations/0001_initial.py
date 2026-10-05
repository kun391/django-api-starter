from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies: list[tuple[str, str]] = []

    operations = [
        migrations.CreateModel(
            name="IdempotencyRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key_digest", models.CharField(max_length=64, unique=True)),
                ("request_digest", models.CharField(max_length=64)),
                ("response_body", models.TextField()),
                ("response_status", models.PositiveSmallIntegerField()),
                ("response_headers", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField(db_index=True)),
            ],
        ),
    ]
