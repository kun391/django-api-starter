# Accounts

Owns user identity, registration, and self-service profile behavior.

## Boundaries

This is a medium-complexity module:

```text
API -> Service -> ORM
```

Registration is implemented as a service because password policy is a business
invariant that must remain enforced outside the HTTP serializer.

User/profile reads remain Django-native because no additional abstraction is
currently justified.

## Authorization

- registration is public
- authenticated users can read/update their own profile
- user list/detail/update/delete are staff-only

## Authentication

The starter exposes DRF token authentication as a minimal built-in API
mechanism. Projects may replace the API authentication adapter with OIDC/JWT
without changing account registration/profile rules.
