from django.apps import AppConfig


class WebhooksConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.webhooks"

    def ready(self):
        from apps.core.outbox import subscribe

        from .services import fanout_event
        from .topics import SUPPORTED_WEBHOOK_TOPICS

        for topic in SUPPORTED_WEBHOOK_TOPICS:
            # Run before notification fanout to preserve the prior contract.
            # Registration does not require the producer to be imported first.
            subscribe(topic, fanout_event, subscriber_id="webhooks", order=10)
