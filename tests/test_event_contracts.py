"""Manifest-to-runtime event contracts, with explicit producer limitations."""

import pytest

from scripts.check_event_contracts import check, check_contracts


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
