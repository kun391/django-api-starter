from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.notifications"

    def ready(self):
        from apps.core.outbox import subscribe

        from .services import fanout_event
        from .topics import SUPPORTED_NOTIFICATION_TOPICS

        for topic in SUPPORTED_NOTIFICATION_TOPICS:
            subscribe(topic, fanout_event, subscriber_id="notifications", order=20)
