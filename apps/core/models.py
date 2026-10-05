"""Infrastructure-owned persistence; no business entities belong here."""

from django.db import models


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
