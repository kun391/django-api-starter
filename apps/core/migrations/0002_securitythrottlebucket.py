from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="SecurityThrottleBucket",
            fields=[
                ("key_digest", models.CharField(max_length=64, primary_key=True, serialize=False)),
                ("scope", models.CharField(max_length=64)),
                ("bucket_start", models.DateTimeField()),
                ("hits", models.PositiveIntegerField(default=0)),
                ("expires_at", models.DateTimeField(db_index=True)),
            ],
        ),
    ]
