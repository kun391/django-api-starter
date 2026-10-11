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

## Subscriptions

Register **named subscribers**, not placeholder handlers in the producer. A
single event topic can have several subscribers:

```python
# apps/notifications/apps.py
from apps.core.outbox import subscribe
from apps.notifications.services import fanout_event

# Inside AppConfig.ready():
subscribe(
    "orders.order-created",
    fanout_event,
    subscriber_id="notifications",
    order=20,
)
```

A subscription is process-local and is registered at Django app startup. It
does **not** require the producer app to be loaded first. Re-registering the
same topic, subscriber ID, handler function and order is safe when
`AppConfig.ready()` is called again. Reusing an ID for a different handler or
order raises a configuration error. The retired single-handler decorator
`outbox_handler()` remains available temporarily for backward compatibility;
new modules must use `subscribe()` with an explicit stable subscriber ID.

Subscribers execute in deterministic order: ascending `order`, then
`subscriber_id` (default order 100). Use
`subscriptions_for(topic)` or `registered_subscriptions()` to inspect the
read-only registration view. Module consumers should have no access to
`_SUBSCRIBERS` or other private registry internals. Do not register a
producer-side no-op handler merely to make its topics available.

Handlers should be thin, idempotent and load business identifiers via
application services. Delivery remains **at-least-once**; a worker can crash
after an external side effect succeeds but before durable acknowledgement.
Use the event UUID as the downstream idempotency key or maintain dedupe state.

**Durable delivery:** Each recorded event captures the process-local subscriber
identities in the same business transaction as the event. A unique
`OutboxDelivery(event_id, subscriber_id)` row stores its own `attempts`,
`available_at`, `succeeded_at`, `dead_lettered_at`, lease token and
sanitized `last_error_code`. Successful subscribers never run again merely
because a sibling failed. A failed subscriber cannot prevent later subscribers
from being attempted. Multiple workers can dispatch separate events concurrently;
an event row lease is the serialization boundary for that event, and the
per-delivery token fences late acknowledgements.

The registry is a **code contract**, not an inbox history: an event is routed
to the subscriber IDs captured when it was created. Adding a subscriber later
does not replay already-seeded events. A queued event created by an older
version without any delivery rows is seeded with the available subscriptions
when first claimed. Missing code for a persisted subscriber ID is a retryable
`LookupError`, never silently skipped. Changing or removing subscriber IDs
requires a reviewed migration/rollout and a drain/inspection of pending rows.

Event-level `OutboxEvent.published_at` is set only after **every** subscriber
succeeds. When one delivery dead-letters and another remains retryable, the
event itself remains pending until that other subscriber resolves; it becomes
dead-lettered once no subscriber remains retryable. Event attempts and
`last_error_code` are compatibility/summary fields, **not** a replacement for
individual delivery state. No automatic dead-letter replay is attempted.

There is no exactly-once claim.

## Claiming, retries, and dead letters

Workers lease rows before invoking handlers. The lease lets multiple workers poll the
same PostgreSQL table without intentionally processing the same row concurrently.
Expired leases are reclaimable after a worker crash.

A **subscriber delivery** failure:
- increments that subscriber's `attempts` only;
- stores only the exception class name in `last_error_code` (not an exception message);
- clears the per-subscriber lease;
- schedules that delivery's exponential backoff independently;
- dead-letters that delivery after `OUTBOX_MAX_ATTEMPTS` without affecting successful siblings.

An event with **no registered subscribers** remains a retryable, unhandled event
and can dead-letter at the event level. It is never marked as published silently.

Defaults:

| Setting | Default |
| --- | ---: |
| `OUTBOX_BATCH_SIZE` | 100 |
| `OUTBOX_LEASE_SECONDS` | 60 |
| `OUTBOX_MAX_ATTEMPTS` | 10 |
| `OUTBOX_RETRY_BASE_SECONDS` | 5 |
| `OUTBOX_RETRY_MAX_SECONDS` | 3600 |
| `OUTBOX_MAX_EVENT_BYTES` | 65536 |

After remediation, explicitly requeue the **failed subscriber**, not
the whole event. The command refuses an active event lease and requires an exact
subscriber ID when more than one delivery is dead-lettered:

```bash
uv run python manage.py requeue_outbox <event-uuid> --subscriber-id notifications
```

A legacy event with no subscriber rows still supports
`requeue_outbox <event-uuid>`. On manual requeue only the selected subscriber's
attempt counter is reset to zero. Successful deliveries remain terminal.

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

## Event metadata contract

`uv run python -m scripts.check_event_contracts` loads the real Django
`AppConfig.ready()` subscriptions and compares each consumer module's
registrations with `module.yaml -> consumes`. Every consumed topic must be
declared by a producer module's `emits`, and topic ownership is checked.
The optional literal-call AST scan catches common missing/unused producer
declarations, but does **not** prove dynamically generated event topics,
business emission semantics or runtime coverage. Behavior tests remain required.

## Rollout and migration safety

The new `core.0005_outboxdelivery` migration is **additive** and does not rewrite
old events. Run migrations as a serialized release step **before** starting
new-version dispatch workers; coordinate a stop/drain of old outbox workers,
deploy the new API and workers together, and only then resume dispatch.
Do not run old and new dispatch code against the same queue during the cutover.
Old pending events without delivery rows are lazily seeded at first claim. Events
already marked published are left untouched. Already-running fanout may repeat
once during upgrades or crashes; downstream deduplication is still required.
Review queues, missing subscribers, and per-subscriber dead-letter alerts before
enabling any retention policy. `operations/queues/` reports additional
`outbox:<subscriber-id>` summary queues (counts/age/status only, no payloads).
