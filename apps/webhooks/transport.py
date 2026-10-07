from __future__ import annotations

import http.client
import socket
import ssl

from django.conf import settings

from .security import resolve_webhook_target


class WebhookTransportError(RuntimeError):
    pass


def send_webhook(url: str, body: bytes, headers: dict[str, str]) -> int:
    target = resolve_webhook_target(url)
    timeout = float(getattr(settings, "WEBHOOK_HTTP_TIMEOUT_SECONDS", 5))
    context = ssl.create_default_context()
    host_header = (
        target.hostname
        if target.port == 443
        else f"{target.hostname}:{target.port}"
    )

    last_error: Exception | None = None
    for address in target.addresses:
        raw = None
        tls = None
        try:
            raw = socket.create_connection((address, target.port), timeout=timeout)
            tls = context.wrap_socket(raw, server_hostname=target.hostname)
            tls.settimeout(timeout)
            connection = http.client.HTTPConnection(target.hostname, target.port)
            connection.sock = tls
            request_headers = {
                "Host": host_header,
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
                "User-Agent": "django-api-starter-webhooks/1",
                "Connection": "close",
                **headers,
            }
            connection.request("POST", target.path_and_query, body=body, headers=request_headers)
            response = connection.getresponse()
            status = int(response.status)
            response.read(0)
            connection.close()
            return status
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            last_error = exc
        finally:
            if tls is not None:
                try:
                    tls.close()
                except OSError:
                    pass
            elif raw is not None:
                try:
                    raw.close()
                except OSError:
                    pass

    raise WebhookTransportError(
        type(last_error).__name__ if last_error is not None else "network_error"
    )
