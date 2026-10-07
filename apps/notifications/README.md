# Notification Foundation

Phase 19 adds durable transactional email notifications on top of the existing
transactional outbox.

## Flow

1. Business services keep writing their existing outbox events.
2. The notifications app composes a fan-out consumer with the existing handler.
3. Fan-out resolves one explicit recipient from the event payload and creates one
   idempotent `NotificationDelivery` row.
4. The row snapshots the delivery email address and template context.
5. `dispatch_notifications` leases ready rows, re-checks the user's current
   preference, renders a template, and calls the configured email provider.
6. Failures retry with bounded exponential backoff and eventually become terminal.

No SMTP/provider call runs inside a request or business transaction.

## Supported topics

- organization created -> owner
- membership added -> target user
- membership updated -> target user
- membership removed -> target user
- ticket created -> owner
- ticket updated -> owner
- ticket attachment added -> owner

Organization rename is intentionally excluded because its event does not identify
an explicit notification recipient.

## Preferences

Authenticated users can manage per-topic email preferences:

- `GET /api/v1/notifications/preferences/`
- `PUT /api/v1/notifications/preferences/{topic}/`

Missing rows mean email is enabled. A stored row is an explicit override.
Disabling a topic cancels pending deliveries for that user/topic, and the worker
checks the preference again immediately before sending.

## Delivery history

`GET /api/v1/notifications/deliveries/` returns only the authenticated user's
history. It supports strict filtering by `source_event_id` and `event_topic`.

Delivery history records status, attempts, timestamps, template key, destination
address and bounded error code. Provider response bodies and credentials are not
stored.

## Provider boundary

The default provider is
`apps.notifications.provider.DjangoMailerEmailProvider`, implemented using
Django 6.1 `MAILERS` and `EmailMessage.send(using=...)`.

Override `NOTIFICATION_EMAIL_PROVIDER` with another class implementing:

```python
def send_email(*, subject: str, body: str, recipient: str) -> None:
    ...
```

Provider exceptions are normalized and persisted as delivery failures instead of
escaping the worker state machine.

## Templates

Templates live under:

`apps/notifications/templates/notifications/<template-slug>/`

Each template has:

- `subject.txt` — must render to one line
- `body.txt` — plain-text body

Phase 19 deliberately starts with plain-text transactional email. HTML,
localization, digesting, marketing campaigns, SMS and push are later concerns.

## Operations

Without Celery, schedule:

```bash
python manage.py dispatch_outbox --batch-size 100
python manage.py dispatch_notifications --batch-size 100
```

With the async profile, Celery Beat schedules notification dispatch every five
seconds independently of the outbox and webhook dispatchers.

Monitor queue age, retry counts, terminal failures and cancellation rate.
