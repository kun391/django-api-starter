import uuid

from django.conf import settings
from django.db import models

from apps.files.models import PrivateFile
from apps.organizations.models import Organization


class Ticket(models.Model):
    class Status(models.TextChoices):
        OPEN = "open"
        IN_PROGRESS = "in_progress"
        RESOLVED = "resolved"

    class Priority(models.TextChoices):
        LOW = "low"
        NORMAL = "normal"
        HIGH = "high"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="tickets",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tickets",
    )
    title = models.CharField(max_length=120)
    description = models.TextField(max_length=4000, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
    )
    revision = models.PositiveBigIntegerField(default=1)
    priority = models.CharField(
        max_length=10,
        choices=Priority.choices,
        default=Priority.NORMAL,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["owner", "status", "-created_at"],
                name="tickets_owner_status_idx",
            ),
            models.Index(
                fields=["organization", "status", "-created_at"],
                name="tickets_org_status_idx",
            ),
        ]


class TicketAttachment(models.Model):
    ticket = models.ForeignKey(
        Ticket,
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    file = models.OneToOneField(
        PrivateFile,
        on_delete=models.PROTECT,
        related_name="ticket_attachment",
    )
    attached_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
