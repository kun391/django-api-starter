# Outbound Webhooks

Phase 18 adds tenant-scoped outbound webhook delivery without coupling external
HTTP calls to business transactions.

## Flow

1. Domain services keep writing their existing transactional outbox events.
2. The webhooks app composes a fan-out consumer with the existing domain outbox
   handler for the supported event allowlist.
3. Fan-out idempotently creates one `WebhookDelivery` per active matching
   subscription and source event.
4. `dispatch_webhooks` leases ready deliveries and performs HTTPS delivery
   outside the request/business transaction.
5. 2xx marks success. Network errors and non-2xx responses retry with bounded
   exponential backoff and eventually become terminal failures.
6. Owner/admin may replay a terminal delivery, which creates a new delivery ID
   while retaining the original source event ID.

The transactional outbox remains the durable event source. The webhook queue owns
network retry/history independently.

## Tenant API

Owner/admin only:

- `GET/POST /api/v1/organizations/{organization_id}/webhooks/`
- `GET/PATCH/DELETE /api/v1/organizations/{organization_id}/webhooks/{id}/`
- `POST /api/v1/organizations/{organization_id}/webhooks/{id}/rotate-secret/`
- `GET /api/v1/organizations/{organization_id}/webhook-deliveries/`
- `POST /api/v1/organizations/{organization_id}/webhook-deliveries/{id}/replay/`

Delete deactivates the subscription and cancels currently pending deliveries. It
does not erase delivery history.

## Signing

Each subscription has a `secret_version`. Delivery signatures are derived from
`WEBHOOK_SIGNING_MASTER_KEY`, the subscription UUID and version; signing
material is never persisted in plaintext and is never returned by the HTTP API.

Provision the derived receiver key out-of-band through your deployment/secret
management workflow. A trusted operator or secret-management integration may use
`apps.webhooks.signing.derive_signing_secret()`; do not expose it through a
public endpoint or logs.

Delivery headers include:

- `Webhook-Id`
- `Webhook-Event-Id`
- `Webhook-Event`
- `Webhook-Timestamp`
- `Webhook-Secret-Version`
- `Webhook-Signature: v1=<hex-hmac-sha256>`

The signed bytes are:

`<delivery-id>.<unix-timestamp>.<exact-json-body>`

Receivers should enforce a timestamp tolerance, recompute the HMAC using
constant-time comparison, and deduplicate by `Webhook-Id`.

Secret rotation increments the version for future attempts. Existing delivery
history remains unchanged.

## SSRF and transport policy

- HTTPS only.
- No URL credentials or fragments.
- Ports are allowlisted; default is 443.
- DNS is resolved immediately before each attempt.
- If any resolved address is private, loopback, link-local or otherwise
  non-global, delivery is rejected by default.
- The transport connects to the validated numeric address while using the
  original hostname for TLS SNI/certificate validation.
- Redirects are never followed.
- Response bodies are never stored.
- Environment HTTP proxy variables are not used.

`WEBHOOK_ALLOW_PRIVATE_ENDPOINTS=True` exists only for deliberately reviewed
private-network deployments. Do not enable it as a workaround for validation
errors.

## Retry and replay

Default retry settings:

- max attempts: 8
- base delay: 30 seconds
- max delay: 1 hour
- lease: 60 seconds

Workers claim rows using database leases and `select_for_update(skip_locked=True)`.
A crashed worker can be retried after lease expiry.

Manual replay is allowed only for terminal deliveries and creates a distinct
delivery record. It never mutates or hides the original failure/success history.

## Operations

Without Celery schedule both commands:

```bash
python manage.py dispatch_outbox --batch-size 100
python manage.py dispatch_webhooks --batch-size 100
```

With the async profile, Celery Beat schedules both dispatchers independently.

Monitor pending age, retry counts, terminal failures, DNS/transport failures and
subscription activity.

## Non-goals

This phase does not add inbound webhooks, arbitrary custom headers, payload
transforms, redirect following, per-subscription retry schedules, delivery
response-body storage, or a UI.
