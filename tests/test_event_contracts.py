"""Manifest-to-runtime event contracts, with explicit producer limitations."""

import pytest

from scripts.check_event_contracts import (
    attribute_subscriptions,
    check,
    check_contracts,
)


def test_repository_event_contracts():
    assert check() == []


def test_missing_and_undeclared_runtime_subscribers_fail():
    manifests = {
        "orders": {"emits": ["orders.created"], "consumes": []},
        "notifications": {"emits": [], "consumes": ["orders.created"]},
    }
    errors = check_contracts(
        manifests,
        {"orders": set(), "notifications": {"orders.unknown"}},
        {"orders": {"orders.created"}, "notifications": set()},
    )
    assert any("declared consumes is not subscribed" in x for x in errors)
    assert any("runtime subscriber is undeclared" in x for x in errors)


def test_unowned_consumes_and_unlisted_producer_topic_fail():
    manifests = {
        "orders": {"emits": ["orders.created"], "consumes": []},
        "notifications": {"emits": [], "consumes": ["other.missing"]},
    }
    errors = check_contracts(
        manifests,
        {"notifications": {"other.missing"}},
        {"orders": {"orders.created", "orders.updated"}},
    )
    assert any("consumes missing producer" in x for x in errors)
    assert any("published literal topic is not declared" in x for x in errors)


@pytest.mark.parametrize("invalid", [None, "orders.created", [{"invalid": 1}]])
def test_invalid_event_metadata_must_be_reported(invalid):
    manifests = {"orders": {"emits": invalid, "consumes": []}}
    errors = check_contracts(
        manifests, {"orders": set()}, {"orders": set()}
    )
    assert any("emits must be a list" in x for x in errors)


def test_duplicate_and_misowned_event_declarations_fail():
    manifests = {
        "orders": {"emits": ["other.created", "other.created"], "consumes": []},
        "other": {"emits": ["other.created"], "consumes": []},
    }
    errors = check_contracts(
        manifests, {}, {"orders": {"other.created"}, "other": {"other.created"}}
    )
    assert any("duplicated emits" in x for x in errors)
    assert any("topic not owned" in x for x in errors)
    assert any("duplicate event producer" in x for x in errors)


def test_emitted_topic_without_consumer_fails():
    manifests = {
        "orders": {"emits": ["orders.created"], "consumes": []},
        "notifications": {"emits": [], "consumes": []},
    }
    errors = check_contracts(
        manifests,
        {"orders": set(), "notifications": set()},
        {"orders": {"orders.created"}, "notifications": set()},
    )
    assert "orders: emits topic has no consumer: orders.created" in errors


def test_subscriptions_are_attributed_by_subscriber_id_prefix():
    runtime, errors = attribute_subscriptions(
        {
            "orders.created": ("notifications", "files.delete-stored-object"),
            "orders.updated": ("webhooks.audit",),
        },
        {"notifications", "files", "webhooks"},
    )
    assert errors == []
    assert runtime == {
        "notifications": {"orders.created"},
        "files": {"orders.created"},
        "webhooks": {"orders.updated"},
    }


def test_subscriber_id_without_module_prefix_fails():
    runtime, errors = attribute_subscriptions(
        {"orders.created": ("legacy.abc123",)}, {"notifications"}
    )
    assert runtime == {"notifications": set()}
    assert len(errors) == 1
    assert "'legacy.abc123'" in errors[0]


def test_legacy_single_handler_decorator_is_removed():
    from apps.core import outbox

    assert not hasattr(outbox, "outbox_handler")
