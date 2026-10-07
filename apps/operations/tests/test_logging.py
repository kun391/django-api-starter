import json
import logging

from apps.core.observability import JsonFormatter, log_batch_completed


def test_json_formatter_includes_low_cardinality_operational_fields(caplog):
    handler = logging.StreamHandler()
    formatter = JsonFormatter()
    handler.setFormatter(formatter)
    logger = logging.getLogger("operations.background")
    old_handlers = list(logger.handlers)
    old_propagate = logger.propagate
    logger.handlers = [handler]
    logger.propagate = False

    payload = {}
    try:
        with caplog.at_level(logging.INFO, logger="operations.background"):
            record = logging.LogRecord(
                "operations.background",
                logging.INFO,
                __file__,
                1,
                "background.batch.completed",
                (),
                None,
            )
            record.__dict__["operation"] = "background.batch.completed"
            record.__dict__["queue_name"] = "notifications"
            record.__dict__["processed_count"] = 3
            payload = json.loads(formatter.format(record))
    finally:
        logger.handlers = old_handlers
        logger.propagate = old_propagate

    assert payload["operation"] == "background.batch.completed"
    assert payload["queue_name"] == "notifications"
    assert payload["processed_count"] == 3


def test_log_batch_completed_emits_structured_extra(caplog):
    with caplog.at_level(logging.INFO, logger="operations.background"):
        log_batch_completed(queue="outbox", processed_count=2)

    record = caplog.records[-1]
    assert record.getMessage() == "background.batch.completed"
    assert record.__dict__["operation"] == "background.batch.completed"
    assert record.__dict__["queue_name"] == "outbox"
    assert record.__dict__["processed_count"] == 2
