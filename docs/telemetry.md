# OpenTelemetry and metrics foundation (Phase 23)

Phase 23 adds an **optional**, vendor-neutral telemetry boundary. The base
application, migrations, tests and minimal production image continue to work
without OpenTelemetry installed.

Enable the extra only where telemetry is required:

```bash
uv sync --locked --extra telemetry --group dev
```

Production can use `runtime-telemetry` for a web-only telemetry image or
`runtime-full` when the same image also needs async and storage capabilities.

## What is instrumented

When `TELEMETRY_ENABLED=True`:

- HTTP server requests produce spans and low-cardinality request metrics.
- PostgreSQL ORM execution produces a manual `db.query` span and duration
  metric. SQL statements and bind parameters are deliberately not attached.
- Celery task entrypoints produce task-boundary spans.
- transactional outbox events persist W3C `traceparent` / `tracestate`
  alongside the existing request ID;
- outbox consumption continues the originating trace;
- webhook and email delivery rows persist internal trace context so delivery
  workers continue the causal trace later;
- structured application logs include current trace/span IDs;
- cache, private-storage and durable delivery outcomes emit low-cardinality
  metrics;
- the Phase 20 operations snapshot emits queue gauges.

The starter exports traces and metrics through OTLP/HTTP. It does not bundle an
OpenTelemetry Collector, Prometheus, Grafana, Tempo, Jaeger, Datadog, Honeycomb
or another backend.

## Configuration

Telemetry is disabled by default.

```env
TELEMETRY_ENABLED=True
TELEMETRY_EXPORTER=otlp
TELEMETRY_SERVICE_NAME=my-api
TELEMETRY_ENVIRONMENT=production
OTEL_EXPORTER_OTLP_ENDPOINT=https://otel-collector.internal.example.com:4318
TELEMETRY_TRACE_SAMPLE_RATE=0.1
TELEMETRY_METRIC_EXPORT_INTERVAL_MS=60000
```

`OTEL_EXPORTER_OTLP_ENDPOINT` is the collector base URL; the application sends
to `/v1/traces` and `/v1/metrics`.

If the collector needs authentication, inject standard OTLP exporter environment
configuration such as `OTEL_EXPORTER_OTLP_HEADERS` through the secret manager.
Do not commit bearer tokens or collector credentials.

Production fails startup when telemetry is enabled without a valid OTLP endpoint.
Telemetry is not part of API readiness: collector downtime must not make the API
claim PostgreSQL is unavailable. The SDK exporter handles export failures
separately from request processing.

## Privacy and cardinality policy

Metrics are operational aggregates. Never add these as metric attributes:

- user ID or username;
- email address;
- organization/tenant ID;
- ticket/file/delivery/event IDs;
- file names, object keys or webhook URLs;
- request IDs, trace IDs or arbitrary payload values.

Reviewed metric dimensions are intentionally small sets such as:

- HTTP method, route template and status code;
- queue name;
- success/failure/cancelled outcome;
- cache operation and hit/miss/error;
- storage operation and outcome;
- database system and outcome.

High-cardinality identifiers may be useful on **sampled spans** for debugging.
The current implementation uses source event ID and request ID on asynchronous
spans, but never as metric labels.

The HTTP route attribute comes from Django's resolved route template rather than
the concrete request path, so resource IDs do not create a new metric series.

## Trace propagation

The causal path is:

```text
HTTP request
  -> business transaction
     -> transactional outbox metadata
        -> outbox consumer span
           -> webhook/notification durable delivery trace context
              -> delivery worker span
```

W3C `traceparent` and `tracestate` are accepted from trusted callers through
normal HTTP propagation and are allowed by CORS. `baggage` is intentionally not
enabled by this starter because arbitrary baggage is an easy PII/cardinality
leak.

Webhook trace context is kept in the internal delivery row. It is **not** copied
into the receiver-facing webhook body. Email recipients likewise never receive
trace context in rendered templates.

Request ID remains an independent operational correlation identifier and
continues to be persisted in audit/outbox metadata.

## Metrics

Current application metrics include:

- `app.http.server.requests`
- `app.http.server.duration`
- `app.db.query.duration`
- `app.cache.operations`
- `app.storage.operations`
- `app.storage.operation.duration`
- `app.background.batch.processed`
- `app.queue.delivery.results`
- `app.queue.pending`
- `app.queue.retrying`
- `app.queue.terminal_failures`
- `app.queue.active_leases`
- `app.queue.stale_leases`
- `app.queue.oldest_pending_age`

Queue gauges are updated when `operations_snapshot()` runs. Phase 20 already
provides the staff operations endpoint and `report_operations` command. Poll or
schedule that snapshot at an appropriate cadence if queue gauges are required in
the OTLP metrics stream. The starter deliberately does not query durable queues
from an exporter background thread.

## Sampling and cost

Trace sampling defaults to 10%. Metrics are not sampled in the same way and are
recorded whenever telemetry is enabled.

Tune sampling from observed traffic and debugging needs. Do not solve cost by
adding user/tenant filters as metric labels. For high-volume database workloads,
measure the overhead of per-query spans and metrics in staging before selecting a
production sampling/export policy.

## Relationship to Sentry

The existing Sentry integration remains independent. You may use Sentry alone,
OpenTelemetry alone, or both. Avoid configuring two tracing systems to export the
same signal unless the duplication is intentional and costed.

Sentry PII remains disabled by default; OpenTelemetry follows the explicit
privacy/cardinality rules in this document.

## Failure semantics

OTLP is an observability dependency, not an authoritative application dependency.
A collector outage must not:

- change a business transaction outcome;
- fail cache/storage compensation;
- mark a durable queue item delivered or failed;
- change PostgreSQL readiness semantics.

Use local structured logs and the Phase 20 operations snapshot when the telemetry
backend itself is impaired.
