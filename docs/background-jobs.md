# Background jobs and transactional outbox (Phase 9)

## Two delivery classes

Choose the weakest mechanism that still satisfies the business requirement.

### 1. Best-effort job after commit

Use `enqueue_after_commit()` when losing a job in the narrow process-crash window after
DB commit is acceptable:

```python
from apps.core.background import enqueue_after_commit

with transaction.atomic():
    order = create_order(...)
    enqueue_after_commit(send_receipt.delay, order.pk)
```

The callback is discarded when the transaction rolls back, so a worker never receives a
job for state that did not commit. However, Django's `on_commit` callback is not durable:
the process can die after commit and before the callback reaches the broker.

Pass stable IDs and primitive values to background jobs, not ORM instances, request
objects, access tokens, passwords, or giant payloads. The task must load current state and
call application/service code instead of duplicating business rules.

### 2. Transactional outbox

Use `record_outbox_event()` for work/event delivery that must commit atomically with the
business mutation:

```python
from django.db import transaction
from apps.core.outbox import record_outbox_event

with transaction.atomic():
    order = create_order(...)
    record_outbox_event(
        topic="orders.order-created",
        version=1,
        payload={"order_id": order.pk},
    )
```

The helper intentionally refuses to run outside `transaction.atomic()`. Therefore the
business write and outbox row either both commit or both roll back.

Events use a stable envelope:

```json
{
  "id": "uuid",
  "topic": "orders.order-created",
  "version": 1,
  "occurred_at": "2026-10-06T00:00:00+00:00",
  "payload": {"order_id": 123},
  "metadata": {"request_id": "optional-correlation-id"}
}
```

Event payload + metadata are capped at 64 KiB by default. Topics are lowercase,
versioned by the separate integer field, and should describe facts that happened rather
than commands to perform.

## Handlers

Register one handler per topic:

```python
from apps.core.outbox import outbox_handler

@outbox_handler("orders.order-created")
def on_order_created(event):
    order_id = event["payload"]["order_id"]
    ...
```

Handler registration should live in the owning/integration module and be imported during
application startup. Do not put business logic in the handler; load identifiers and call
the appropriate application service.

Handlers are **at-least-once**. They must be idempotent. A worker can crash after an
external side effect succeeds but before `published_at` is persisted, causing the same
event ID to be delivered again. Use the event UUID as the downstream idempotency key when
the provider supports it, or maintain an inbox/deduplication record at the consumer.

There is no exactly-once claim.

## Claiming, retries, and dead letters

Workers lease rows before invoking handlers. The lease lets multiple workers poll the
same PostgreSQL table without intentionally processing the same row concurrently.
Expired leases are reclaimable after a worker crash.

A failure:
- increments `attempts`;
- stores only the exception class name in `last_error_code` (not an exception message);
- clears the lease;
- schedules exponential backoff;
- dead-letters after `OUTBOX_MAX_ATTEMPTS`.

Defaults:

| Setting | Default |
| --- | ---: |
| `OUTBOX_BATCH_SIZE` | 100 |
| `OUTBOX_LEASE_SECONDS` | 60 |
| `OUTBOX_MAX_ATTEMPTS` | 10 |
| `OUTBOX_RETRY_BASE_SECONDS` | 5 |
| `OUTBOX_RETRY_MAX_SECONDS` | 3600 |
| `OUTBOX_MAX_EVENT_BYTES` | 65536 |

After remediation, explicitly requeue a dead-lettered event:

```bash
uv run python manage.py requeue_outbox <event-uuid>
```

## Celery integration

The optional async image discovers `core.dispatch_outbox`. Celery Beat schedules one
bounded dispatch every five seconds. The database row remains the durable source of
truth; RabbitMQ is being used to schedule worker execution, not as the transactional
commit boundary.

You can also dispatch one batch without Celery:

```bash
uv run python manage.py dispatch_outbox --batch-size 100
```

This is useful for operations, tests, or a deployment that prefers a platform scheduler
over Celery Beat.

## External brokers

This phase intentionally does not add Kafka/NATS/SNS/SQS abstractions. If a product needs
a real integration-event broker, keep the same transactional outbox table and replace or
extend the delivery handler at that volatile boundary. Do not make the database
transaction wait on a network publish.

## Relationship to Phase 7 idempotency

Incoming idempotency and outgoing outbox solve different halves of a reliable write:

```text
client retry
   |
   v
Idempotency-Key
   |
   v
business transaction
   +-- business rows
   +-- outbox event
   |
 COMMIT ONCE
   |
   v
at-least-once handler / integration
```

An idempotent POST can persist an outbox row inside the same durable transaction used by
the Phase 7 helper. Do not use `transaction.on_commit` for delivery that is part of the
business guarantee.

## Operations

Apply migrations:

```bash
uv run python manage.py migrate
```

Monitor:
- ready unpublished rows and oldest `available_at`;
- active leases older than expected;
- retry counts;
- dead-lettered rows.

Alert on sustained backlog and any dead-letter count that violates the product SLO.

## Verification

CI covers:
- commit/rollback behavior;
- best-effort on-commit behavior;
- successful delivery;
- retry/backoff and dead letters;
- lease/reclaim behavior;
- payload bounds;
- Django migration/schema/type/lint checks;
- both production Docker targets.

The reference foundation does not emit business events automatically. Product modules
choose explicitly which mutations deserve a durable event.
