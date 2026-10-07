from celery import shared_task

from .services import process_delivery_batch


@shared_task(
    name="notifications.dispatch_notifications",
    ignore_result=True,
    acks_late=True,
    reject_on_worker_lost=True,
)
def dispatch_notifications() -> int:
    return process_delivery_batch()
