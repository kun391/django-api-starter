"""Infrastructure-owned persistence; no business entities belong here."""

import uuid

from django.db import models
from django.utils import timezone


class IdempotencyRecord(models.Model):
    # HMAC of principal + explicit scope + method/path + caller key.
    key_digest = models.CharField(max_length=64, unique=True)
    request_digest = models.CharField(max_length=64)
    response_body = models.TextField()
    response_status = models.PositiveSmallIntegerField()
    response_headers = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)


class SecurityThrottleBucket(models.Model):
    """Fixed-window abuse counter. Raw IPs and credentials are never persisted."""

    key_digest = models.CharField(max_length=64, primary_key=True)
    scope = models.CharField(max_length=64)
    bucket_start = models.DateTimeField()
    hits = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(db_index=True)


class OutboxEvent(models.Model):
    """Durable event created in the same transaction as the business write."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    topic = models.CharField(max_length=200)
    version = models.PositiveSmallIntegerField(default=1)
    payload = models.JSONField()
    metadata = models.JSONField(default=dict)
    occurred_at = models.DateTimeField(auto_now_add=True)
    available_at = models.DateTimeField(default=timezone.now, db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    published_at = models.DateTimeField(null=True, blank=True, db_index=True)
    dead_lettered_at = models.DateTimeField(null=True, blank=True, db_index=True)
    locked_until = models.DateTimeField(null=True, blank=True, db_index=True)
    lock_token = models.UUIDField(null=True, blank=True)
    last_error_code = models.CharField(max_length=128, blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["published_at", "dead_lettered_at", "available_at"],
                name="core_outbox_ready_idx",
            ),
        ]
