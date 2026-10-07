"""Optional Celery entrypoints. Imported only by async workers."""

from celery import shared_task

from apps.core.outbox import process_outbox_batch
from apps.operations.logging import log_batch_completed


@shared_task(
    name="core.dispatch_outbox",
    ignore_result=True,
    acks_late=True,
    reject_on_worker_lost=True,
)
def dispatch_outbox() -> int:
    """Dispatch one bounded durable batch.

    Handler failures are persisted on individual outbox rows and therefore do not
    fail the scheduler task itself.
    """

    processed = process_outbox_batch()
    log_batch_completed(queue="outbox", processed_count=processed)
    return processed
