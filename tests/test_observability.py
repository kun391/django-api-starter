import json

from django.urls import reverse

from apps.core.observability import JsonFormatter


def test_request_id_is_generated(api_client):
    response = api_client.get(reverse("health_live"))

    assert response.status_code == 200
    assert response["X-Request-ID"]
    assert len(response["X-Request-ID"]) <= 128


def test_valid_request_id_is_preserved(api_client):
    response = api_client.get(
        reverse("health_live"),
        HTTP_X_REQUEST_ID="client-request-123",
    )

    assert response["X-Request-ID"] == "client-request-123"


def test_invalid_request_id_is_replaced(api_client):
    response = api_client.get(
        reverse("health_live"),
        HTTP_X_REQUEST_ID="invalid request id\nheader",
    )

    assert response["X-Request-ID"] != "invalid request id\nheader"


def test_json_formatter_emits_structured_log():
    formatter = JsonFormatter()
    record = __import__("logging").LogRecord(
        name="tests",
        level=20,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )

    payload = json.loads(formatter.format(record))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "tests"
    assert payload["message"] == "hello"
    assert "timestamp" in payload
