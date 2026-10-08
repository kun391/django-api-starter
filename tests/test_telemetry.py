import json
import logging
import uuid

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction

pytest.importorskip("opentelemetry.sdk")

from apps.core import telemetry
from apps.core.models import OutboxEvent
from apps.core.observability import JsonFormatter
from apps.core.outbox import process_outbox_batch, record_outbox_event
from apps.notifications.models import NotificationDelivery
from apps.notifications.services import process_delivery_batch as process_notification_batch
from apps.operations.services import operations_snapshot
from apps.organizations.models import Organization, OrganizationMembership
from apps.webhooks.models import WebhookDelivery, WebhookSubscription
from apps.webhooks.services import process_delivery_batch as process_webhook_batch

pytestmark = pytest.mark.django_db(transaction=True)


class RecordingProvider:
    def __init__(self):
        self.trace_ids: list[str | None] = []

    def send_email(self, **_kwargs):
        self.trace_ids.append(telemetry.current_trace_ids()[0])


@pytest.fixture(scope="module", autouse=True)
def telemetry_runtime():
    settings.TELEMETRY_ENABLED = True
    settings.TELEMETRY_EXPORTER = "none"
    settings.TELEMETRY_SERVICE_NAME = "django-api-starter-tests"
    settings.TELEMETRY_ENVIRONMENT = "test"
    settings.TELEMETRY_TRACE_SAMPLE_RATE = 1.0
    assert telemetry.configure_telemetry() is True
    yield


def _trace_id_from_traceparent(value: str) -> str:
    parts = value.split("-")
    assert len(parts) == 4
    return parts[1]


def test_trace_context_and_log_correlation():
    formatter = JsonFormatter()

    with telemetry.span("test.root"):
        trace_id, span_id = telemetry.current_trace_ids()
        assert trace_id is not None
        assert span_id is not None

        carrier = telemetry.capture_trace_context({"request_id": "req-telemetry"})
        assert carrier["request_id"] == "req-telemetry"
        assert _trace_id_from_traceparent(carrier["traceparent"]) == trace_id

        record = logging.LogRecord(
            name="telemetry-test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="inside trace",
            args=(),
            exc_info=None,
        )
        payload = json.loads(formatter.format(record))
        assert payload["trace_id"] == trace_id
        assert payload["span_id"] == span_id


def test_http_metrics_are_low_cardinality(api_client, monkeypatch):
    counters = []
    histograms = []

    monkeypatch.setattr(
        "apps.core.observability.telemetry.counter_add",
        lambda name, amount=1, *, attributes=None: counters.append(
            (name, amount, attributes or {})
        ),
    )
    monkeypatch.setattr(
        "apps.core.observability.telemetry.histogram_record",
        lambda name, value, *, unit="1", attributes=None: histograms.append(
            (name, value, unit, attributes or {})
        ),
    )

    response = api_client.get(
        "/health/live/",
        HTTP_X_REQUEST_ID="telemetry-http-request",
    )
    assert response.status_code == 200

    request_metric = next(
        item for item in counters if item[0] == "app.http.server.requests"
    )
    assert set(request_metric[2]) == {
        "http.request.method",
        "http.route",
        "http.response.status_code",
    }
    assert "telemetry-http-request" not in json.dumps(request_metric[2])

    duration = next(
        item for item in histograms if item[0] == "app.http.server.duration"
    )
    assert duration[2] == "s"
    assert set(duration[3]) == {
        "http.request.method",
        "http.route",
        "http.response.status_code",
    }


def test_db_metric_has_no_sql_or_identifiers(monkeypatch):
    captured = []
    monkeypatch.setattr(
        telemetry,
        "histogram_record",
        lambda name, value, *, unit="1", attributes=None: captured.append(
            (name, value, unit, attributes or {})
        ),
    )

    get_user_model().objects.count()

    metric = next(item for item in captured if item[0] == "app.db.query.duration")
    assert metric[2] == "s"
    assert metric[3] == {"db.system": "postgresql", "outcome": "ok"}


def test_http_to_outbox_to_delivery_workers_preserves_trace(user):
    organization = Organization.objects.create(
        name="Telemetry Org",
        slug=f"telemetry-{uuid.uuid4().hex[:8]}",
    )
    OrganizationMembership.objects.create(
        organization=organization,
        user=user,
        role="owner",
    )
    WebhookSubscription.objects.create(
        organization=organization,
        url="https://93.184.216.34/hook",
        events=["tickets.ticket-created"],
        created_by_id=user.pk,
    )

    with telemetry.span("simulated.http.request"):
        root_trace_id, _ = telemetry.current_trace_ids()
        with transaction.atomic():
            event = record_outbox_event(
                topic="tickets.ticket-created",
                payload={
                    "organization_id": str(organization.pk),
                    "ticket_id": str(uuid.uuid4()),
                    "owner_id": user.pk,
                    "priority": "normal",
                    "revision": 1,
                },
                metadata={"request_id": "request-through-outbox"},
            )

    assert root_trace_id is not None
    assert event.metadata["request_id"] == "request-through-outbox"
    assert _trace_id_from_traceparent(event.metadata["traceparent"]) == root_trace_id

    assert process_outbox_batch() == 1

    webhook = WebhookDelivery.objects.get(source_event_id=event.pk)
    notification = NotificationDelivery.objects.get(source_event_id=event.pk)
    assert webhook.trace_context["request_id"] == "request-through-outbox"
    assert notification.trace_context["request_id"] == "request-through-outbox"
    assert _trace_id_from_traceparent(webhook.trace_context["traceparent"]) == root_trace_id
    assert (
        _trace_id_from_traceparent(notification.trace_context["traceparent"])
        == root_trace_id
    )

    webhook_trace_ids = []

    def transport(_url, _body, _headers):
        webhook_trace_ids.append(telemetry.current_trace_ids()[0])
        return 204

    provider = RecordingProvider()
    assert process_webhook_batch(transport=transport) == 1
    assert process_notification_batch(provider=provider) == 1
    assert webhook_trace_ids == [root_trace_id]
    assert provider.trace_ids == [root_trace_id]


def test_delivery_trace_context_is_internal_not_webhook_payload(user):
    organization = Organization.objects.create(
        name="Telemetry Privacy Org",
        slug=f"telemetry-privacy-{uuid.uuid4().hex[:8]}",
    )
    OrganizationMembership.objects.create(
        organization=organization,
        user=user,
        role="owner",
    )
    WebhookSubscription.objects.create(
        organization=organization,
        url="https://93.184.216.34/hook",
        events=["tickets.ticket-created"],
        created_by_id=user.pk,
    )

    with telemetry.span("privacy.root"):
        with transaction.atomic():
            event = record_outbox_event(
                topic="tickets.ticket-created",
                payload={
                    "organization_id": str(organization.pk),
                    "ticket_id": str(uuid.uuid4()),
                    "owner_id": user.pk,
                    "priority": "normal",
                    "revision": 1,
                },
            )

    assert process_outbox_batch() == 1
    delivery = WebhookDelivery.objects.get(source_event_id=event.pk)
    assert "traceparent" in delivery.trace_context
    assert "traceparent" not in delivery.body.get("metadata", {})
    assert "tracestate" not in delivery.body.get("metadata", {})


def test_queue_metrics_use_only_queue_dimension(monkeypatch):
    captured = []
    monkeypatch.setattr(
        "apps.operations.services.gauge_set",
        lambda name, value, *, unit="1", attributes=None: captured.append(
            (name, value, unit, attributes or {})
        ),
    )

    operations_snapshot()

    assert captured
    for name, _value, _unit, attributes in captured:
        assert name.startswith("app.queue.")
        assert set(attributes) == {"queue"}
        assert attributes["queue"] in {
            "outbox",
            "webhooks",
            "notifications",
        }
