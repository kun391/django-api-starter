import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction

from apps.core.audit import record_audit_event
from apps.core.models import AuditEvent

pytestmark = pytest.mark.django_db(transaction=True)


def test_audit_requires_business_transaction():
    with pytest.raises(ImproperlyConfigured, match="business transaction"):
        record_audit_event(
            action="example.changed",
            subject_type="example",
            subject_id="1",
            actor_id=1,
        )
    assert AuditEvent.objects.count() == 0


def test_audit_rolls_back_with_business_transaction():
    with pytest.raises(RuntimeError):
        with transaction.atomic():
            record_audit_event(
                action="example.changed",
                subject_type="example",
                subject_id="1",
                actor_id=1,
                metadata={"changed_fields": ["name"]},
            )
            raise RuntimeError("rollback")
    assert AuditEvent.objects.count() == 0


def test_audit_metadata_is_bounded_and_json_only():
    with transaction.atomic():
        with pytest.raises(ImproperlyConfigured, match="plain JSON"):
            record_audit_event(
                action="example.changed",
                subject_type="example",
                subject_id="1",
                actor_id=1,
                metadata={"invalid": object()},
            )

    with transaction.atomic():
        with pytest.raises(ImproperlyConfigured, match="16 KiB"):
            record_audit_event(
                action="example.changed",
                subject_type="example",
                subject_id="1",
                actor_id=1,
                metadata={"value": "x" * (17 * 1024)},
            )
