from celery import shared_task

from apps.core.observability import log_batch_completed
from apps.core.telemetry import span

from .services import process_delivery_batch


@shared_task(
    name="webhooks.dispatch_webhooks",
    ignore_result=True,
    acks_late=True,
    reject_on_worker_lost=True,
)
def dispatch_webhooks() -> int:
    with span(
        "celery.webhooks.dispatch_webhooks",
        kind="consumer",
        attributes={
            "messaging.system": "celery",
            "messaging.destination.name": "webhooks.dispatch_webhooks",
        },
    ):
        processed = process_delivery_batch()
        log_batch_completed(queue="webhooks", processed_count=processed)
        return processed
