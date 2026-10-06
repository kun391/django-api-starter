# Organizations

Phase 14 introduces an explicit tenant boundary and fixed-role RBAC model.

Tenant identity comes from the organization UUID in the route plus the
authenticated user's persisted membership. No client-supplied tenant or role
header grants access. A non-member receives 404, including Django staff users.

Roles are owner, admin and member. Owners manage membership; owner/admin may
manage tenant ticket status; members may use tenant business APIs. Owner
demotion/removal locks the owner set and cannot leave an organization ownerless.

Organization creation is idempotent: organization, creator membership and outbox
event commit atomically. Invitations, SSO/domain discovery, arbitrary permission
strings, billing tenancy and database row-level security are intentionally out of
scope.
