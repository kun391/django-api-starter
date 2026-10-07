import logging

logger = logging.getLogger("operations.background")


def log_batch_completed(*, queue: str, processed_count: int) -> None:
    logger.info(
        "background.batch.completed",
        extra={
            "operation": "background.batch.completed",
            "queue_name": queue,
            "processed_count": processed_count,
        },
    )
