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


class AuditEvent(models.Model):
    """Append-only application audit history for successful business mutations."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    action = models.CharField(max_length=120)
    subject_type = models.CharField(max_length=80)
    subject_id = models.CharField(max_length=128)
    actor_id = models.BigIntegerField(null=True, blank=True)
    organization_id = models.UUIDField(null=True, blank=True)
    request_id = models.CharField(max_length=128, blank=True)
    metadata = models.JSONField(default=dict)
    occurred_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-occurred_at", "-id"]
        indexes = [
            models.Index(
                fields=["organization_id", "-occurred_at"],
                name="core_audit_org_time_idx",
            ),
            models.Index(
                fields=["subject_type", "subject_id", "-occurred_at"],
                name="core_audit_subject_idx",
            ),
            models.Index(
                fields=["actor_id", "-occurred_at"],
                name="core_audit_actor_idx",
            ),
        ]
