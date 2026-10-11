from django.apps import AppConfig


class FilesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.files"

    def ready(self):
        from apps.core.outbox import subscribe

        from .handlers import on_deletion_requested

        subscribe(
            "files.deletion-requested",
            on_deletion_requested,
            subscriber_id="files.delete-stored-object",
        )
