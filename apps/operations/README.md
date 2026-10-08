# Operations and Observability Hardening

Phase 20 adds vendor-neutral operational visibility for the durable queues already
owned by the starter.

## Queue snapshot

Staff users can read:

`GET /api/v1/operations/queues/`

The response contains aggregate-only state for:

- transactional outbox;
- outbound webhooks;
- notifications.

Each queue reports:

- pending rows;
- retrying rows;
- terminal failures;
- active leases;
- stale leases;
- oldest pending age;
- derived status: `ok`, `warning`, or `critical`.

No payload, email address, webhook URL, secret, event body, or recipient data is
returned.

## Thresholds

Queue age thresholds are runtime configuration:

- `OPERATIONS_QUEUE_WARNING_AGE_SECONDS` (default 300)
- `OPERATIONS_QUEUE_CRITICAL_AGE_SECONDS` (default 900)

Any terminal failure makes that queue critical. Age thresholds are deliberately
simple starter defaults, not claimed SLOs. Production teams should tune them to
their dispatch cadence and business latency requirements.

## Health semantics

`/health/live/` remains process liveness.

`/health/ready/` remains PostgreSQL readiness.

Async queue degradation is intentionally **not** folded into API readiness:
stopping web traffic because email/webhook delivery is delayed would couple
independent failure domains and can make an incident worse.

Use the staff operations endpoint, the CLI snapshot, or external monitoring for
queue health.

## CLI

```bash
python manage.py report_operations
```

The command prints one JSON object suitable for cron, platform probes, or a
metrics/log collector.

## Structured worker logs

Celery task entrypoints emit aggregate completion events:

- `background.batch.completed`
- queue: `outbox`, `webhooks`, or `notifications`
- processed count

These are deliberately low-cardinality fields. Request/event payloads and
recipient data are never attached to operational logs.

## Monitoring guidance

At minimum alert on:

- sustained `critical` queue state;
- increasing oldest pending age;
- any outbox dead letter;
- sustained webhook/notification terminal failures;
- repeated stale leases;
- PostgreSQL readiness failures;
- scheduler/worker process absence.

Phase 23 adds optional OpenTelemetry export for these aggregate queue gauges and
other application signals. It still does not install Prometheus, Grafana, Tempo,
a collector or a hosted monitoring vendor. Queue gauges are emitted when an
operations snapshot is generated, so poll the endpoint or schedule
`report_operations` when fresh queue gauges are required.
