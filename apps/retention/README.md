# Data lifecycle and retention (Phase 21)

This module coordinates bounded deletion of infrastructure/history rows after
their operational purpose has ended. It does **not** implement business-record
deletion, GDPR/CCPA policy, or a universal legal retention period.

## Safety model

`purge_retained_data` is a dry-run unless `--confirm` is supplied.

```bash
python manage.py purge_retained_data
python manage.py purge_retained_data --category notification_deliveries
python manage.py purge_retained_data --category all --batch-size 1000 --confirm
```

The batch limit is per enabled category. Pending, retrying and actively leased
outbox/webhook/notification work is never selected.

Expired idempotency and security-throttle rows are always eligible because their
expiry is already part of those contracts. All history/tombstone categories use
an explicit retention setting. A value of `0` disables that category.

## Configurable categories

- published outbox events;
- dead-letter outbox events;
- terminal webhook delivery history;
- terminal notification delivery history;
- durable audit events;
- verified private-file tombstone manifests.

Audit retention defaults to disabled. Choose it only after product/legal review.
Webhook and notification history may contain payload/context snapshots and should
not be retained indefinitely by accident.

Private-file tombstones are only removed after object deletion has completed,
the configured tombstone window has elapsed, and no ticket attachment still
references the manifest. The external object must already be gone; this command
never performs storage I/O.

## Business data

This phase intentionally does not add generic soft-delete columns to every model.
Business lifecycle is domain-specific. An organization, ticket, user, or future
clinical/payment record needs explicit archive/deactivate/delete semantics in its
own module rather than a global `deleted_at` convention.

Likewise, data-subject export/deletion workflows should be composed from
module-owned hooks once a real product requirement defines identity,
authorization, legal holds, and fields that must be retained.
