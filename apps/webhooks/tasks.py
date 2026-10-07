from celery import shared_task

from .services import process_delivery_batch
from apps.core.observability import log_batch_completed


@shared_task(
    name="webhooks.dispatch_webhooks",
    ignore_result=True,
    acks_late=True,
    reject_on_worker_lost=True,
)
def dispatch_webhooks() -> int:
    processed = process_delivery_batch()
    log_batch_completed(queue="webhooks", processed_count=processed)
    return processed
