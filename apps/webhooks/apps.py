from django.apps import AppConfig


class WebhooksConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.webhooks"

    def ready(self):
        from apps.core import outbox

        from .services import fanout_event
        from .topics import SUPPORTED_WEBHOOK_TOPICS

        for topic in SUPPORTED_WEBHOOK_TOPICS:
            original = outbox._HANDLERS.get(topic)
            if original is None:
                continue
            if getattr(original, "_webhook_fanout_wrapped", False):
                continue

            def combined(envelope, *, original_handler=original):
                original_handler(envelope)
                fanout_event(envelope)

            combined._webhook_fanout_wrapped = True
            outbox._HANDLERS[topic] = combined
