import base64
import hashlib
import hmac

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def derive_signing_secret(subscription_id, secret_version: int) -> str:
    master = getattr(settings, "WEBHOOK_SIGNING_MASTER_KEY", "")
    if not isinstance(master, str) or len(master) < 32:
        raise ImproperlyConfigured(
            "WEBHOOK_SIGNING_MASTER_KEY must contain at least 32 characters."
        )
    material = f"subscription:{subscription_id}:v{secret_version}".encode()
    digest = hmac.new(master.encode(), material, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def signature_for(*, secret: str, delivery_id, timestamp: str, body: bytes) -> str:
    message = f"{delivery_id}.{timestamp}.".encode() + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def signing_headers(
    *,
    subscription_id,
    secret_version: int,
    delivery_id,
    source_event_id,
    event_topic: str,
    timestamp: str,
    body: bytes,
) -> dict[str, str]:
    secret = derive_signing_secret(subscription_id, secret_version)
    signature = signature_for(
        secret=secret,
        delivery_id=delivery_id,
        timestamp=timestamp,
        body=body,
    )
    return {
        "Webhook-Id": str(delivery_id),
        "Webhook-Event-Id": str(source_event_id),
        "Webhook-Event": event_topic,
        "Webhook-Timestamp": timestamp,
        "Webhook-Secret-Version": str(secret_version),
        "Webhook-Signature": f"v1={signature}",
    }
