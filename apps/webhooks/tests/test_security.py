import socket

import pytest
from django.core.exceptions import ValidationError
from django.test import override_settings

from apps.webhooks.security import resolve_webhook_target, validate_webhook_url


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/hook",
        "https://user:pass@example.com/hook",
        "https://example.com/hook#fragment",
        "https://localhost/hook",
        "https://127.0.0.1/hook",
        "https://169.254.169.254/latest/meta-data/",
    ],
)
def test_static_webhook_url_policy_rejects_unsafe_targets(url):
    with pytest.raises(ValidationError):
        validate_webhook_url(url)


def test_static_policy_accepts_public_https_literal():
    assert (
        validate_webhook_url("https://93.184.216.34/hook")
        == "https://93.184.216.34/hook"
    )


def test_dns_resolution_rejects_private_answer():
    def resolver(*_args, **_kwargs):
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("10.0.0.10", 443),
            ),
        ]

    with pytest.raises(ValidationError, match="private or non-routable"):
        resolve_webhook_target("https://hooks.example.com/events", resolver=resolver)


def test_dns_resolution_rejects_mixed_public_and_private_answers():
    def resolver(*_args, **_kwargs):
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", 443),
            ),
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("127.0.0.1", 443),
            ),
        ]

    with pytest.raises(ValidationError, match="private or non-routable"):
        resolve_webhook_target("https://hooks.example.com/events", resolver=resolver)


@override_settings(WEBHOOK_ALLOW_PRIVATE_ENDPOINTS=True)
def test_private_endpoint_opt_in_is_explicit():
    assert validate_webhook_url("https://127.0.0.1/hook").startswith("https://")


@override_settings(WEBHOOK_ALLOWED_PORTS=(443, 8443))
def test_reviewed_alternate_port_can_be_enabled():
    assert (
        validate_webhook_url("https://93.184.216.34:8443/hook")
        == "https://93.184.216.34:8443/hook"
    )
