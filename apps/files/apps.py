from django.apps import AppConfig


class FilesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.files"

    def ready(self):
        from . import handlers  # noqa: F401
