# Resilience and failure testing (Phase 22)

Phase 22 defines failure semantics for the starter and locks them with automated
tests. It does not add a general chaos framework or hide infrastructure outages.

## Failure matrix

| Dependency/failure | Expected API/worker behavior | Recovery |
| --- | --- | --- |
| PostgreSQL unavailable | `/health/ready/` returns generic 503; liveness remains process-only | restore DB connectivity; readiness becomes 200 |
| Read-model Redis unavailable | cache reads/writes/invalidation fail open to the authoritative database | cache reconnects naturally; TTL/generation prevents cache from becoming authority |
| Private object storage unavailable during upload/read/delete | file API returns bounded 503 where applicable; provider details are not exposed; committed manifests/outbox preserve cleanup intent | reconcile/dispatch after storage returns |
| Outbox worker dies after claiming | claimed row remains leased and unpublished | another worker reclaims after lease expiry |
| Webhook worker dies after claiming | delivery remains pending with lease; no terminal state is invented | another worker reclaims after lease expiry |
| Notification worker dies after claiming | delivery remains pending with lease | another worker reclaims after lease expiry |
| Webhook endpoint/email provider is down | retry state is persisted with bounded backoff; terminal failure is observable | automatic retry until max attempts, then operator action/replay where supported |
| One fan-out integration commits and a later integration fails | source outbox event remains unpublished and retries | integration fan-out dedupe prevents duplicate durable deliveries |
| Duplicate source event delivery | supported fan-outs are idempotent by source identity | duplicate attempt becomes a no-op |
| Async queues degraded | API readiness remains PostgreSQL-only | operations snapshot reports warning/critical queue state |

## Hard vs soft dependencies

PostgreSQL is the API's authoritative persistence and therefore a readiness
dependency. If it is unavailable, the instance must not claim readiness.

The performance cache is explicitly non-authoritative. Cache unavailability must
not turn a valid database-backed request into a failure.

Private storage is required only for file operations. A storage outage should not
make unrelated API routes unready.

Webhook endpoints, email providers, Celery workers and brokers are asynchronous
failure domains. Their health belongs in queue/operations monitoring, not the API
readiness probe.

## Worker crash model

Outbox, webhook and notification workers use database leases. Claiming work does
not mean completing work.

If a process disappears after a claim:

1. the row remains unfinished;
2. its lease prevents concurrent duplicate work during the lease window;
3. after `locked_until` expires, another worker can reclaim it;
4. completion is guarded by the current lock token.

Lease duration must exceed normal processing latency but remain short enough to
bound crash recovery. Sustained stale leases are exposed by Phase 20 operations
monitoring.

## At-least-once and idempotency

The transactional outbox is at-least-once, not exactly-once.

A composed handler can partially succeed. For example, webhook fan-out may commit
before notification fan-out encounters a transient database failure. The source
outbox event remains unpublished and is retried. Webhook and notification fan-out
therefore use deterministic dedupe keys so the retry completes missing work
without duplicating already-created deliveries.

External receivers must also treat webhook delivery as at-least-once. The
`Webhook-Id` and source event identity exist so receivers can implement their own
idempotency.

## Storage compensation

Private upload deliberately separates the database manifest from object storage
I/O. The manifest commits before storage write so ambiguous storage outcomes are
recoverable.

If a storage write may have succeeded but the request fails, the manifest is
retired and durable deletion is queued. Reconciliation handles abandoned pending
manifests and tombstone rechecks. Never solve an outage by scanning/deleting an
entire bucket.

## CI coverage

Phase 22 adds explicit recovery tests for:

- abandoned outbox lease -> reclaim -> publish;
- abandoned webhook lease -> reclaim -> one send;
- abandoned notification lease -> reclaim -> one send;
- unexpected webhook process crash -> lease expiry -> recovery;
- partial composed fan-out failure -> source retry without duplicate delivery;
- terminal downstream failure does not make API readiness fail;
- real Redis connection refusal falls back to the authoritative loader.

Existing suites continue to cover:

- generic PostgreSQL readiness 503 without leaking DB errors;
- storage write ambiguity and cleanup compensation;
- storage deletion retry/replay;
- webhook bounded retries/terminal state;
- notification bounded retries/terminal state;
- duplicate fan-out suppression;
- operations visibility of stale leases and terminal failures.

## Operator response

For a degraded dependency:

1. identify the failure domain from readiness, operations snapshot and sanitized
   structured logs;
2. do not purge/requeue active work blindly;
3. restore the dependency first;
4. verify backlog age and stale leases begin decreasing;
5. requeue dead-letter/terminal work only after the cause is corrected;
6. verify no duplicate external side effect was created;
7. record the incident and adjust timeout/lease/alert thresholds only from
   observed latency, not by making retries unbounded.

A retry is not a substitute for an outage budget. Persistent dependency failures
must surface as operationally actionable state.
