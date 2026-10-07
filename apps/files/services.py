"""Private file lifecycle, with a durable manifest before non-transactional I/O."""

import logging
from datetime import timedelta
from hashlib import sha256
from tempfile import SpooledTemporaryFile

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.db import connection, models, transaction
from django.http import Http404
from django.utils import timezone
from django.utils.http import content_disposition_header

from apps.core.outbox import record_outbox_event
from apps.core.security import audit_security_event

from .models import PrivateFile
from .validation import positive_setting, validate_upload

logger = logging.getLogger(__name__)


class FileConflict(Exception):
    pass


class FileStorageUnavailable(Exception):
    pass


def owned_file(file_id, *, actor, retired=False, lock=False):
    if not actor.is_authenticated or not actor.is_active:
        raise PermissionDenied()
    query = PrivateFile.objects.filter(pk=file_id, owner_id=actor.pk)
    if not retired:
        query = query.filter(state=PrivateFile.State.READY)
    else:
        query = query.exclude(state=PrivateFile.State.PENDING)
    if lock:
        query = query.select_for_update()
    record = query.first()
    if record is None:
        raise Http404()
    return record


def _require_autocommit():
    if connection.in_atomic_block or not connection.get_autocommit():
        raise ImproperlyConfigured(
            "File upload needs an outer autocommit boundary: its manifest must commit before storage I/O."
        )


def _retire_locked(record):
    record.state = PrivateFile.State.DELETING
    record.cleanup_after = None
    record.save(update_fields=["state", "cleanup_after"])
    record_outbox_event(topic="files.deletion-requested", payload={"file_id": str(record.pk)})


def _compensate(record_id):
    try:
        with transaction.atomic():
            record = PrivateFile.objects.select_for_update().get(pk=record_id)
            # A new event is intentional: an earlier cleanup may have already
            # completed before a slow/ambiguous PUT finished.
            _retire_locked(record)
    except Exception:
        # The committed manifest remains discoverable by reconciliation.
        # Do not replace the original failure or log URLs/content/filenames.
        logger.error("private_file_cleanup_schedule_failed")


def upload_file(*, actor, upload, purpose="document", replace_id=None):
    """Return a ready new immutable ID; replacement retires the previous ID.

    No DB transaction spans storage I/O. Only default-database operations are
    supported, matching the existing outbox. Do not wrap this in another atomic
    block or Phase 7 idempotent_post: file writes are not database-transactional.
    """
    _require_autocommit()
    if not actor.is_authenticated or not actor.is_active:
        raise PermissionDenied()
    if replace_id is not None:
        previous = owned_file(replace_id, actor=actor)
        purpose = previous.purpose
    validated = validate_upload(upload, purpose=purpose)
    pending_seconds = positive_setting("PRIVATE_FILE_PENDING_SECONDS", 86400)
    backend = storages["private"]  # Configuration errors precede any mutation.
    # If this insert fails, no object has been written. If the process dies
    # afterwards, the known immutable key can be cleaned without bucket scans.
    with transaction.atomic(durable=True):
        record = PrivateFile.objects.create(
            owner=actor, purpose=purpose, original_name=validated.name,
            size=len(validated.data), media_type=validated.media_type,
            sha256=validated.sha256,
            cleanup_after=timezone.now() + timedelta(seconds=pending_seconds),
        )
    try:
        try:
            name = backend.save(record.object_key, ContentFile(validated.data))
            if name != record.object_key:
                raise ImproperlyConfigured("Private backend must preserve immutable object names.")
        except ImproperlyConfigured:
            raise
        except Exception:
            # Only the external storage boundary is translated. DB, validation
            # and programming errors retain Django's normal error pipeline.
            logger.warning("private_file_storage_unavailable: write")
            raise FileStorageUnavailable() from None
        with transaction.atomic(durable=True):
            current = PrivateFile.objects.select_for_update().get(pk=record.pk)
            if (
                current.state != PrivateFile.State.PENDING or current.owner_id != actor.pk
                or current.cleanup_after <= timezone.now()
            ):
                raise FileConflict()
            if replace_id is not None:
                previous = owned_file(replace_id, actor=actor, retired=True, lock=True)
                if previous.state != PrivateFile.State.READY:
                    raise FileConflict()
                _retire_locked(previous)
            current.state = PrivateFile.State.READY
            current.cleanup_after = None
            current.save(update_fields=["state", "cleanup_after"])
    except Exception:
        _compensate(record.pk)
        raise
    audit_security_event("files.upload.succeeded", outcome="succeeded", actor_id=actor.pk)
    return current


def delete_file(file_id, *, actor):
    with transaction.atomic():
        record = owned_file(file_id, actor=actor, retired=True, lock=True)
        if record.state == PrivateFile.State.READY:
            _retire_locked(record)
    audit_security_event("files.delete.requested", outcome="succeeded", actor_id=actor.pk)


def open_file(file_id, *, actor):
    """Stage a bounded verified snapshot before returning an HTTP response.

    Some backends defer I/O until the first read. Catch those failures before
    response headers are sent, rather than leaking provider details or sending
    a partial successful download. FileResponse owns the returned spool.
    """
    record = owned_file(file_id, actor=actor)
    backend = storages["private"]
    stream = SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b")
    try:
        if not 0 < record.size <= 16 * 1024 * 1024:
            raise ValueError("Invalid stored file size.")
        remaining = record.size
        digest = sha256()
        with backend.open(record.object_key, "rb") as source:
            while chunk := source.read(min(65536, remaining + 1)):
                remaining -= len(chunk)
                if remaining < 0:
                    raise ValueError("Stored file exceeds its manifest.")
                digest.update(chunk)
                stream.write(chunk)
        if remaining or digest.hexdigest() != record.sha256:
            raise ValueError("Stored file does not match its manifest.")
        stream.seek(0)
    except Exception:
        stream.close()
        logger.warning("private_file_storage_unavailable: read")
        raise FileStorageUnavailable() from None
    return record, stream


def download_url(file_id, *, actor):
    record = owned_file(file_id, actor=actor)
    if not settings.PRIVATE_FILE_SIGNED_DOWNLOADS:
        raise Http404()
    backend = storages["private"]
    if not getattr(backend, "querystring_auth", False) or getattr(backend, "custom_domain", None):
        raise ImproperlyConfigured("Signed downloads require the private S3 backend with query authentication.")
    ttl = positive_setting("PRIVATE_FILE_URL_TTL", 300)
    try:
        url = backend.url(record.object_key, expire=ttl, parameters={
            "ResponseContentType": "application/octet-stream",
            "ResponseContentDisposition": content_disposition_header(True, record.original_name),
            "ResponseCacheControl": "private, no-store",
        })
    except Exception:
        logger.warning("private_file_storage_unavailable: sign")
        raise FileStorageUnavailable() from None
    return {"url": url, "expires_in": ttl}


def delete_stored_object(file_id):
    """At-least-once deletion. Retain a scrubbed manifest for reconciliation."""
    record = PrivateFile.objects.filter(
        pk=file_id, state__in=[PrivateFile.State.DELETING, PrivateFile.State.DELETED],
    ).first()
    if record is None:
        return  # Replayed/invalid events never delete a READY or PENDING key.
    backend = storages["private"]
    backend.delete(record.object_key)  # Idempotent; errors are retried by core outbox.
    # A crash before this update is safe: the next delivery deletes the same key.
    recheck = positive_setting("PRIVATE_FILE_RECHECK_SECONDS", 86400)
    now = timezone.now()
    PrivateFile.objects.filter(pk=record.pk, state__in=["deleting", "deleted"]).update(
        state=PrivateFile.State.DELETED,
        original_name="",
        media_type="",
        sha256="",
        size=0,
        deleted_at=record.deleted_at or now,
        cleanup_after=now + timedelta(seconds=recheck),
    )


def reconcile_files(*, batch_size=100):
    """Queue one bounded batch; do not call storage or clear a bucket here.

    Recheck tombstones to catch remote PUTs completing after a process crash or
    ambiguous timeout. This is eventual cleanup, not an atomic DB/S3 transaction.
    """
    if type(batch_size) is not int or not 1 <= batch_size <= 1000:
        raise ValueError("batch_size must be between 1 and 1000.")
    due = models.Q(state__in=["pending", "deleted"], cleanup_after__lte=timezone.now())
    due |= models.Q(state="ready", owner__isnull=True)
    with transaction.atomic():
        records = list(
            PrivateFile.objects.select_for_update(skip_locked=True).filter(due)
            .order_by("cleanup_after", "id")[:batch_size]
        )
        for record in records:
            _retire_locked(record)
    return len(records)
