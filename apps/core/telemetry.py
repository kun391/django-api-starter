"""Optional vendor-neutral OpenTelemetry integration.

The base starter must run without OpenTelemetry installed. All imports of the
optional SDK live behind TELEMETRY_ENABLED and the telemetry dependency extra.
"""

from __future__ import annotations

import importlib
import time
from collections.abc import Mapping
from contextlib import contextmanager
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db.backends.signals import connection_created

_configured = False
_instruments: dict[tuple[str, str], Any] = {}


def enabled() -> bool:
    return bool(getattr(settings, "TELEMETRY_ENABLED", False))


def _import(name: str):
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        raise ImproperlyConfigured(
            "TELEMETRY_ENABLED requires the optional 'telemetry' dependency extra."
        ) from exc


def _resource():
    resources = _import("opentelemetry.sdk.resources")
    return resources.Resource.create(
        {
            "service.name": getattr(
                settings,
                "TELEMETRY_SERVICE_NAME",
                "django-api-starter",
            ),
            "deployment.environment.name": getattr(
                settings,
                "TELEMETRY_ENVIRONMENT",
                "unknown",
            ),
        }
    )


def configure_telemetry() -> bool:
    global _configured
    if _configured:
        return True
    if not enabled():
        return False

    trace = _import("opentelemetry.trace")
    metrics = _import("opentelemetry.metrics")
    trace_sdk = _import("opentelemetry.sdk.trace")
    trace_export = _import("opentelemetry.sdk.trace.export")
    sampling = _import("opentelemetry.sdk.trace.sampling")
    metrics_sdk = _import("opentelemetry.sdk.metrics")
    metrics_export = _import("opentelemetry.sdk.metrics.export")

    sample_rate = getattr(settings, "TELEMETRY_TRACE_SAMPLE_RATE", 0.1)
    if (
        isinstance(sample_rate, bool)
        or not isinstance(sample_rate, (int, float))
        or not 0 <= float(sample_rate) <= 1
    ):
        raise ImproperlyConfigured(
            "TELEMETRY_TRACE_SAMPLE_RATE must be between 0 and 1."
        )

    exporter = getattr(settings, "TELEMETRY_EXPORTER", "otlp")
    if exporter not in {"otlp", "none"}:
        raise ImproperlyConfigured("TELEMETRY_EXPORTER must be 'otlp' or 'none'.")

    resource = _resource()
    tracer_provider = trace_sdk.TracerProvider(
        resource=resource,
        sampler=sampling.ParentBased(
            sampling.TraceIdRatioBased(float(sample_rate)),
        ),
    )

    metric_readers = []
    if exporter == "otlp":
        endpoint = str(
            getattr(
                settings,
                "TELEMETRY_OTLP_ENDPOINT",
                "http://127.0.0.1:4318",
            )
        ).rstrip("/")
        if not endpoint.startswith(("http://", "https://")):
            raise ImproperlyConfigured(
                "TELEMETRY_OTLP_ENDPOINT must be an http(s) URL."
            )
        otlp_trace = _import("opentelemetry.exporter.otlp.proto.http.trace")
        otlp_metrics = _import("opentelemetry.exporter.otlp.proto.http.metric_exporter")
        tracer_provider.add_span_processor(
            trace_export.BatchSpanProcessor(
                otlp_trace.OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")
            )
        )
        interval = getattr(
            settings,
            "TELEMETRY_METRIC_EXPORT_INTERVAL_MS",
            60000,
        )
        if (
            isinstance(interval, bool)
            or not isinstance(interval, int)
            or not 1000 <= interval <= 3_600_000
        ):
            raise ImproperlyConfigured(
                "TELEMETRY_METRIC_EXPORT_INTERVAL_MS must be 1000-3600000."
            )
        metric_readers.append(
            metrics_export.PeriodicExportingMetricReader(
                otlp_metrics.OTLPMetricExporter(
                    endpoint=f"{endpoint}/v1/metrics"
                ),
                export_interval_millis=interval,
            )
        )

    meter_provider = metrics_sdk.MeterProvider(
        resource=resource,
        metric_readers=metric_readers,
    )
    trace.set_tracer_provider(tracer_provider)
    metrics.set_meter_provider(meter_provider)

    _install_database_metrics()

    _configured = True
    return True


def _meter():
    metrics = _import("opentelemetry.metrics")
    return metrics.get_meter("django-api-starter")


def _instrument(kind: str, name: str, *, unit: str = "1"):
    key = (kind, name)
    instrument = _instruments.get(key)
    if instrument is not None:
        return instrument
    meter = _meter()
    if kind == "counter":
        instrument = meter.create_counter(name, unit=unit)
    elif kind == "histogram":
        instrument = meter.create_histogram(name, unit=unit)
    elif kind == "gauge":
        instrument = meter.create_gauge(name, unit=unit)
    else:
        raise ValueError(f"Unknown telemetry instrument kind: {kind}")
    _instruments[key] = instrument
    return instrument


def counter_add(
    name: str,
    amount: int | float = 1,
    *,
    attributes: Mapping[str, object] | None = None,
) -> None:
    if not enabled():
        return
    _instrument("counter", name).add(amount, attributes=attributes or {})


def histogram_record(
    name: str,
    value: int | float,
    *,
    unit: str = "1",
    attributes: Mapping[str, object] | None = None,
) -> None:
    if not enabled():
        return
    _instrument("histogram", name, unit=unit).record(
        value,
        attributes=attributes or {},
    )


def gauge_set(
    name: str,
    value: int | float,
    *,
    unit: str = "1",
    attributes: Mapping[str, object] | None = None,
) -> None:
    if not enabled():
        return
    _instrument("gauge", name, unit=unit).set(
        value,
        attributes=attributes or {},
    )


@contextmanager
def span(
    name: str,
    *,
    attributes: Mapping[str, object] | None = None,
    carrier: dict[str, str] | None = None,
    kind: str = "internal",
):
    if not enabled():
        yield None
        return

    trace = _import("opentelemetry.trace")
    context = None
    if carrier:
        context = _import("opentelemetry.propagate").extract(carrier)
    span_kind = getattr(trace.SpanKind, kind.upper())
    tracer = trace.get_tracer("django-api-starter")
    with tracer.start_as_current_span(
        name,
        context=context,
        kind=span_kind,
        attributes=attributes or {},
    ) as current:
        yield current


def inject_trace_context(metadata: dict[str, Any]) -> dict[str, Any]:
    result = dict(metadata)
    if not enabled():
        return result
    carrier: dict[str, str] = {}
    _import("opentelemetry.propagate").inject(carrier)
    for key in ("traceparent", "tracestate"):
        value = carrier.get(key)
        if value:
            result.setdefault(key, value)
    return result


def capture_trace_context(
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = dict(metadata or {})
    if not enabled():
        return result
    carrier: dict[str, str] = {}
    _import("opentelemetry.propagate").inject(carrier)
    for key in ("traceparent", "tracestate"):
        value = carrier.get(key)
        if value:
            result[key] = value
    return result


def current_trace_ids() -> tuple[str | None, str | None]:
    if not enabled():
        return None, None
    trace = _import("opentelemetry.trace")
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return None, None
    return f"{context.trace_id:032x}", f"{context.span_id:016x}"


def _db_execute_wrapper(execute, sql, params, many, context):
    started = time.perf_counter()
    outcome = "ok"
    try:
        with span(
            "db.query",
            kind="client",
            attributes={"db.system": "postgresql"},
        ):
            return execute(sql, params, many, context)
    except Exception:
        outcome = "error"
        raise
    finally:
        histogram_record(
            "app.db.query.duration",
            time.perf_counter() - started,
            unit="s",
            attributes={
                "db.system": "postgresql",
                "outcome": outcome,
            },
        )


def _attach_db_wrapper(sender, connection, **_kwargs):
    if _db_execute_wrapper not in connection.execute_wrappers:
        connection.execute_wrappers.append(_db_execute_wrapper)


def _install_database_metrics() -> None:
    connection_created.connect(
        _attach_db_wrapper,
        dispatch_uid="core.telemetry.database_metrics",
        weak=False,
    )
    from django.db import connections

    for connection in connections.all():
        if connection.connection is not None:
            _attach_db_wrapper(None, connection)
