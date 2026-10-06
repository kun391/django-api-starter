import uuid

from django.conf import settings
from django.db import models


class PrivateFile(models.Model):
    class State(models.TextChoices):
        PENDING = "pending"
        READY = "ready"
        DELETING = "deleting"
        DELETED = "deleted"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Retain the manifest when the user is removed, so reconciliation can delete
    # their storage objects rather than losing the only record of their keys.
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
        related_name="private_files",
    )
    purpose = models.CharField(max_length=48)
    original_name = models.CharField(max_length=120)
    size = models.PositiveBigIntegerField()
    media_type = models.CharField(max_length=100)
    sha256 = models.CharField(max_length=64)
    state = models.CharField(max_length=10, choices=State.choices, default=State.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    cleanup_after = models.DateTimeField(null=True)

    class Meta:
        indexes = [models.Index(fields=["state", "cleanup_after"], name="files_cleanup_idx")]

    @property
    def object_key(self) -> str:
        # Immutable deterministic key: no user metadata can influence its path.
        return f"objects/{self.pk.hex[:2]}/{self.pk.hex}"
