from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError


@dataclass(frozen=True)
class ResolvedWebhookTarget:
    hostname: str
    port: int
    path_and_query: str
    addresses: tuple[str, ...]


def _allowed_ports() -> set[int]:
    return {int(value) for value in getattr(settings, "WEBHOOK_ALLOWED_PORTS", (443,))}


def _parts(url: str) -> tuple[str, int, str]:
    if any(char in url for char in ("\r", "\n")):
        raise ValidationError("Webhook URL contains invalid control characters.")
    try:
        parsed = urlsplit(url)
        port = parsed.port or 443
    except ValueError as exc:
        raise ValidationError("Webhook URL has an invalid port.") from exc
    if parsed.scheme.lower() != "https":
        raise ValidationError("Webhook endpoints must use HTTPS.")
    if parsed.username is not None or parsed.password is not None:
        raise ValidationError("Webhook endpoints must not contain credentials.")
    if parsed.fragment:
        raise ValidationError("Webhook endpoints must not contain fragments.")
    if not parsed.hostname:
        raise ValidationError("Webhook endpoint hostname is required.")
    if port not in _allowed_ports():
        raise ValidationError("Webhook endpoint port is not allowed.")
    hostname = parsed.hostname.encode("idna").decode("ascii").lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise ValidationError("Localhost webhook endpoints are not allowed.")
    path = parsed.path or "/"
    target = f"{path}?{parsed.query}" if parsed.query else path
    return hostname, port, target


def validate_webhook_url(url: str) -> str:
    hostname, _port, _target = _parts(url)
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        return url
    if (
        not getattr(settings, "WEBHOOK_ALLOW_PRIVATE_ENDPOINTS", False)
        and not literal.is_global
    ):
        raise ValidationError(
            "Private or non-routable webhook endpoints are not allowed."
        )
    return url


def resolve_webhook_target(
    url: str,
    *,
    resolver=socket.getaddrinfo,
) -> ResolvedWebhookTarget:
    hostname, port, target = _parts(url)
    try:
        results = resolver(
            hostname,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except OSError as exc:
        raise ValidationError("Webhook endpoint DNS resolution failed.") from exc
    addresses = tuple(sorted({result[4][0] for result in results}))
    if not addresses:
        raise ValidationError("Webhook endpoint resolved to no addresses.")
    if not getattr(settings, "WEBHOOK_ALLOW_PRIVATE_ENDPOINTS", False):
        if any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise ValidationError(
                "Webhook endpoint resolved to a private or non-routable address."
            )
    return ResolvedWebhookTarget(hostname, port, target, addresses)
