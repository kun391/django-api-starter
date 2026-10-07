from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.notifications"

    def ready(self):
        from apps.core import outbox

        from .services import fanout_event
        from .topics import SUPPORTED_NOTIFICATION_TOPICS

        for topic in SUPPORTED_NOTIFICATION_TOPICS:
            original = outbox._HANDLERS.get(topic)
            if original is None:
                continue
            if getattr(original, "__module__", "") == __name__:
                continue

            def combined(envelope, *, original_handler=original):
                original_handler(envelope)
                fanout_event(envelope)

            outbox._HANDLERS[topic] = combined
