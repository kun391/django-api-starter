import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class NotificationPreference(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_preferences",
    )
    topic = models.CharField(max_length=200)
    email_enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["topic", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "topic"],
                name="notifications_unique_user_topic",
            ),
        ]


class NotificationDelivery(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_event_id = models.UUIDField()
    event_topic = models.CharField(max_length=200)
    event_version = models.PositiveSmallIntegerField(default=1)
    recipient_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="notification_deliveries",
    )
    recipient_user_id_snapshot = models.BigIntegerField()
    recipient_email = models.EmailField()
    template_slug = models.CharField(max_length=120)
    template_context = models.JSONField(default=dict)
    trace_context = models.JSONField(null=True, blank=True)
    dedupe_key = models.CharField(max_length=128, unique=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now, db_index=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True, db_index=True)
    failed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    cancelled_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_error_code = models.CharField(max_length=128, blank=True)
    locked_until = models.DateTimeField(null=True, blank=True, db_index=True)
    lock_token = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["recipient_user", "-created_at"],
                name="notifications_user_time_idx",
            ),
            models.Index(
                fields=["delivered_at", "failed_at", "cancelled_at", "next_attempt_at"],
                name="notifications_ready_idx",
            ),
        ]
