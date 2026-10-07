# Durable audit trail (Phase 16)

The audit trail is an application-level, append-only history of successful
business mutations. It is separate from security logs, operational logs and the
transactional outbox.

## Transactional contract

Business services call `record_audit_event()` inside the same default-database
transaction as the authoritative mutation. A rollback removes both the mutation
and its audit record. The helper refuses calls outside a transaction.

Audit rows contain only bounded, server-defined fields:

- machine action name;
- subject type and scalar subject ID;
- authenticated actor ID when available;
- scalar organization UUID when the mutation is tenant-scoped;
- current request ID for log correlation;
- small JSON metadata chosen by the owning service;
- server timestamp.

The core table intentionally does not hold foreign keys to business modules.
Historical rows therefore remain readable if a business object or membership is
later deleted, and core does not depend back on domain modules.

Do not put passwords, auth tokens, private file contents, free-form descriptions,
raw request bodies, medical/payment data or arbitrary client JSON in metadata.
The helper caps metadata at 16 KiB but data classification is still the owning
module's responsibility.

## Audit vs outbox

The outbox exists to deliver integration events and may later be purged according
to delivery retention. Audit history exists to explain who changed what and when.
A business service may write both in the same transaction; neither substitutes
for the other.

## Tenant read API

`GET /api/v1/organizations/{organization_id}/audit-events/` is read-only and
requires persisted owner/admin membership. Django staff status does not bypass
tenant membership. The endpoint uses strict filtering/pagination and returns
`Cache-Control: no-store`.

Supported exact filters are `action`, `subject_type`, `subject_id` and
`actor_id`; ISO timestamps are available through `occurred_after` and
`occurred_before`. Ordering is restricted to reviewed fields.

There is intentionally no update/delete audit API and no global support-user
bypass in this phase.

## Retention

Phase 16 does not guess a compliance retention period. Production deployments
must choose retention/export/archive policy from their legal and product
requirements before adding a purge command. Deleting audit history by default
would be a more dangerous starter convention than retaining it.
