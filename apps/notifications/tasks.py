from celery import shared_task

from .services import process_delivery_batch
from apps.operations.logging import log_batch_completed


@shared_task(
    name="notifications.dispatch_notifications",
    ignore_result=True,
    acks_late=True,
    reject_on_worker_lost=True,
)
def dispatch_notifications() -> int:
    processed = process_delivery_batch()
    log_batch_completed(queue="notifications", processed_count=processed)
    return processed
