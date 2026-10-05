# Security baseline (Phase 8)

## Authentication strategy

The starter deliberately keeps authentication provider-neutral.

- SessionAuthentication remains available for Django/browser/internal flows and keeps CSRF protection.
- DRF TokenAuthentication remains the minimal API credential mechanism.
- `POST /api/v1/auth/token/` issues the existing per-user token after credentials are verified.
- `POST /api/v1/auth/token/revoke/` requires that token and revokes it.
- JWT, refresh tokens, OIDC and SSO are not baseline dependencies. Introduce them at the authentication boundary when the product requires token expiry, multi-device sessions, federation or centralized identity.

DRF tokens are bearer-like long-lived secrets. Use them only over HTTPS, never put them
in URLs or logs, and revoke them when a client signs out or a credential is suspected to
be exposed. Applications that require expiry or device/session management should adopt
OIDC/JWT or a dedicated token model rather than adding ad-hoc expiry logic to this token.

## Authorization

The default API permission remains `IsAuthenticated`. Public access is explicit.

The accounts reference module demonstrates action-specific policy:

- registration: public, rate-limited;
- self profile read/update: authenticated user only;
- user list/detail/update/delete: staff only;
- token revoke: TokenAuthentication only.

Self-service serializers do not expose `is_staff`, `is_superuser`, password or email, so
posting those fields cannot escalate privileges or replace the login identity. Business
modules should perform tenant/object authorization before mutations and before invoking
the Phase 7 idempotency helper.

Do not infer authorization from a client-supplied tenant, role or scope header.

## Abuse protection

Login and registration use PostgreSQL-backed fixed-window throttles. This is intentional:
process-local DRF cache throttles are not a sufficient production baseline across multiple
workers, and Redis is optional in this starter.

Defaults:

| Scope | Default |
| --- | --- |
| registration per client IP | 5 / 60 seconds |
| login attempts per client IP | 20 / 60 seconds |
| login attempts per normalized credential | 10 / 60 seconds |

The credential and IP are HMAC inputs; neither raw value is stored in
`SecurityThrottleBucket`. Counters use PostgreSQL `INSERT ... ON CONFLICT ... RETURNING`
so workers increment one shared bucket atomically. A rejected request returns the normal
Phase 7 Problem Details response with status 429 and `Retry-After`.

These controls reduce brute-force and accidental abuse; they are not a WAF or DDoS
service. Keep edge/CDN/load-balancer rate limiting for volumetric attacks.

### Proxy identity

`API_NUM_PROXIES=0` is the safe default: throttling uses `REMOTE_ADDR` and does not trust
caller-controlled `X-Forwarded-For`.

Only set `API_NUM_PROXIES=N` when every production request traverses exactly N trusted
proxies and the outer proxy sanitizes/overwrites forwarded-address headers. An incorrect
value can either collapse all clients into one bucket or let callers bypass IP throttles.

## Security audit events

Security-relevant actions emit structured logs through the existing JSON logger:

- `auth.login.failed`
- `auth.login.succeeded`
- `auth.token.revoked`
- `authorization.denied`
- `csrf.rejected`
- `api.throttle.blocked`
- account registration/profile/admin user mutations

The payload is intentionally small: event, outcome, actor/subject numeric IDs where
available, throttle scope, and the normal request ID. Passwords, tokens, usernames,
emails and raw IP addresses are not added to the audit payload.

A SIEM/exporter can route these JSON events later without changing business modules.

## Browser/session defaults

Production explicitly enables secure and HttpOnly session cookies, `SameSite=Lax`, secure
CSRF cookies, same-origin referrer policy, same-origin opener policy, HSTS and HTTPS
redirects. Cross-origin session deployments must configure `CSRF_TRUSTED_ORIGINS`
deliberately and should review whether their SameSite requirements differ.

Do not blindly enable `SECURE_PROXY_SSL_HEADER`; that setting is safe only when the
trusted reverse proxy strips spoofed forwarded-protocol headers.

## Retention

Throttle buckets expire at the end of their fixed window. Expired rows do not affect
future requests, but should be cleaned periodically:

```bash
uv run python manage.py purge_security_throttles --batch-size 1000
```

Each run deletes one bounded batch. Schedule it with the deployment platform just like
the Phase 7 idempotency cleanup command.

## Configuration

```text
API_NUM_PROXIES=0
AUTH_LOGIN_IP_RATE_LIMIT=20
AUTH_LOGIN_CREDENTIAL_RATE_LIMIT=10
AUTH_LOGIN_RATE_WINDOW_SECONDS=60
REGISTRATION_RATE_LIMIT=5
REGISTRATION_RATE_WINDOW_SECONDS=60
CSRF_TRUSTED_ORIGINS=
```

Use product-specific limits based on traffic and threat model. Very strict credential
limits can themselves become an account-level denial-of-service vector.

## Verification

CI must keep running:

```bash
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py spectacular --file /tmp/schema.yaml --validate --fail-on-warn
uv run mypy .
uv run pytest
```

Security tests cover login/registration throttling, non-trust of X-Forwarded-For by
default, token revocation, privilege-escalation attempts, audit payload hygiene and
expired bucket cleanup.
