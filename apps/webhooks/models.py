import uuid

from django.db import models
from django.utils import timezone

from apps.organizations.models import Organization


class WebhookSubscription(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="webhook_subscriptions",
    )
    url = models.URLField(max_length=2048)
    events = models.JSONField(default=list)
    is_active = models.BooleanField(default=True)
    secret_version = models.PositiveIntegerField(default=1)
    created_by_id = models.BigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(
                fields=["organization", "is_active", "created_at"],
                name="webhooks_sub_org_active_idx",
            ),
        ]


class WebhookDelivery(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subscription = models.ForeignKey(
        WebhookSubscription,
        on_delete=models.PROTECT,
        related_name="deliveries",
    )
    source_event_id = models.UUIDField()
    event_topic = models.CharField(max_length=200)
    event_version = models.PositiveSmallIntegerField(default=1)
    body = models.JSONField()
    dedupe_key = models.CharField(max_length=128, unique=True)
    replay_of_id = models.UUIDField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now, db_index=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True, db_index=True)
    failed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    cancelled_at = models.DateTimeField(null=True, blank=True, db_index=True)
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    last_error_code = models.CharField(max_length=128, blank=True)
    locked_until = models.DateTimeField(null=True, blank=True, db_index=True)
    lock_token = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["subscription", "-created_at"],
                name="webhooks_delivery_sub_idx",
            ),
            models.Index(
                fields=["delivered_at", "failed_at", "cancelled_at", "next_attempt_at"],
                name="webhooks_delivery_ready_idx",
            ),
        ]
