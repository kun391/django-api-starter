from celery import shared_task

from .services import process_delivery_batch


@shared_task(
    name="webhooks.dispatch_webhooks",
    ignore_result=True,
    acks_late=True,
    reject_on_worker_lost=True,
)
def dispatch_webhooks() -> int:
    return process_delivery_batch()
