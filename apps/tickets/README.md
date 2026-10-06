# Tickets reference business module

This is the Phase 13 end-to-end reference module. It intentionally uses the
starter's existing foundations together instead of introducing another framework.

## What it proves

- Auth and permissions: authenticated owners are scoped to their own tickets;
  staff can read all tickets and change workflow status.
- API contract: strict filters, search, stable ordering, bounded pagination and
  Problem Details remain inherited from the shared API layer.
- Idempotency: `POST /api/v1/tickets/` requires `Idempotency-Key`; replay does
  not create another ticket or outbox event.
- Transactions/outbox: creates, updates and attachment links emit durable events
  in the same database transaction as the business mutation.
- Caching: `GET /api/v1/tickets/summary/` caches a reviewed JSON read model and
  invalidates both owner and staff scopes after committed mutations.
- Private files: upload through `/api/v1/files/`, then attach the READY file ID
  to a ticket. File download authorization remains owned by the files module.
- Deployment: no new runtime service is required. PostgreSQL is authoritative;
  Redis remains optional for shared cache, and outbox/file reconciliation use the
  existing worker or scheduled-command conventions.

## Example flow

1. Authenticate normally.
2. Upload a private `.txt` or `.json` document to `POST /api/v1/files/`.
3. Create a ticket with JSON and an `Idempotency-Key`.
4. Attach the returned private file ID with
   `POST /api/v1/tickets/{ticket_id}/attachments/`.
5. List/filter/search tickets or read `/api/v1/tickets/summary/`.
6. Staff may PATCH `status`; normal owners may update title, description and
   priority until the ticket is resolved.

Outbox delivery is at-least-once. The reference handlers are intentionally
idempotent no-ops so the default dispatcher can drain demonstration events
without inventing an external integration. Replace them with real idempotent
consumers when a product integration exists.
