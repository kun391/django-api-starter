# Multi-tenancy and RBAC foundation (Phase 14)

## Boundary

Organizations are explicit tenants. Tenant identity is resolved from:

1. the authenticated principal;
2. the organization UUID in the URL; and
3. a persisted `OrganizationMembership`.

Headers such as `X-Tenant-ID`, `X-Organization-ID` or `X-Role` are never an
authorization source. Django `is_staff` / `is_superuser` do not bypass tenant
membership.

A non-member receives 404 for tenant resources to avoid disclosing tenant
existence.

## Roles

The starter intentionally uses three fixed roles:

- owner — tenant administration and membership management;
- admin — operational management of tenant business resources;
- member — ordinary tenant business access.

This is RBAC, not a general policy engine. Add custom permissions only when a real
product rule requires them.

## Data modeling

Business rows that may be either personal or tenant-owned should make the boundary
explicit. The reference `Ticket` uses nullable `organization_id`:

- null means the original personal Phase 13 scope;
- non-null means tenant scope.

Personal queries always filter `organization_id IS NULL`. Tenant queries always
filter the resolved organization UUID. Never rely on an omitted filter.

## Idempotency, caching and events

Tenant idempotency scopes include the server-resolved organization UUID. The
client does not supply the scope.

Tenant cache scopes use the organization UUID and invalidate after the business
transaction commits.

Outbox events include `organization_id` for tenant rows and null for personal
rows so downstream consumers can preserve the same boundary.

## Membership safety

Owner role changes are serialized by locking the current owner set before
demotion/removal. The service refuses to leave an organization without an owner.

## Deliberate non-goals

This phase does not add invitations, domain discovery, SSO, billing tenancy,
database row-level security, cross-tenant support impersonation, arbitrary
permission strings or tenant-specific encryption keys.
