from datetime import timedelta

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone

from apps.core import outbox
from apps.core.background import enqueue_after_commit
from apps.core.models import OutboxEvent


@pytest.fixture(autouse=True)
def clean_handlers():
    original = dict(outbox._HANDLERS)
    outbox._HANDLERS.clear()
    yield
    outbox._HANDLERS.clear()
    outbox._HANDLERS.update(original)


@pytest.mark.django_db(transaction=True)
class TestBackgroundAndOutbox:
    def test_enqueue_after_commit_runs_only_after_commit(self):
        calls: list[str] = []

        with transaction.atomic():
            enqueue_after_commit(calls.append, "committed")
            assert calls == []

        assert calls == ["committed"]

    def test_enqueue_after_commit_is_discarded_on_rollback(self):
        calls: list[str] = []

        with pytest.raises(RuntimeError), transaction.atomic():
            enqueue_after_commit(calls.append, "rolled-back")
            raise RuntimeError("rollback")

        assert calls == []

    def test_outbox_requires_business_transaction(self):
        with pytest.raises(ImproperlyConfigured):
            outbox.record_outbox_event(
                topic="accounts.user-created",
                payload={"user_id": 1},
            )

    def test_outbox_rolls_back_with_business_transaction(self):
        with pytest.raises(RuntimeError), transaction.atomic():
            outbox.record_outbox_event(
                topic="accounts.user-created",
                payload={"user_id": 1},
            )
            raise RuntimeError("rollback")

        assert not OutboxEvent.objects.exists()

    def test_successful_handler_marks_event_published(self):
        received = []

        @outbox.outbox_handler("accounts.user-created")
        def handle(envelope):
            received.append(envelope)

        with transaction.atomic():
            event = outbox.record_outbox_event(
                topic="accounts.user-created",
                version=2,
                payload={"user_id": 42},
                metadata={"source": "test"},
            )

        assert outbox.process_outbox_batch() == 1
        event.refresh_from_db()
        assert event.published_at is not None
        assert event.attempts == 1
        assert event.dead_lettered_at is None
        assert received[0]["id"] == str(event.pk)
        assert received[0]["version"] == 2
        assert received[0]["payload"] == {"user_id": 42}

        assert outbox.process_outbox_batch() == 0
        assert len(received) == 1

    def test_failure_is_rescheduled_without_storing_exception_message(self, settings):
        settings.OUTBOX_RETRY_BASE_SECONDS = 1
        settings.OUTBOX_RETRY_MAX_SECONDS = 10
        settings.OUTBOX_MAX_ATTEMPTS = 3

        @outbox.outbox_handler("accounts.user-created")
        def fail(_envelope):
            raise RuntimeError("secret-provider-detail")

        with transaction.atomic():
            event = outbox.record_outbox_event(
                topic="accounts.user-created",
                payload={"user_id": 42},
            )

        assert outbox.process_outbox_batch() == 0
        event.refresh_from_db()
        assert event.attempts == 1
        assert event.published_at is None
        assert event.dead_lettered_at is None
        assert event.last_error_code == "RuntimeError"
        assert "secret-provider-detail" not in event.last_error_code
        assert event.available_at > timezone.now()

    def test_event_dead_letters_after_max_attempts(self, settings):
        settings.OUTBOX_MAX_ATTEMPTS = 1

        @outbox.outbox_handler("accounts.user-created")
        def fail(_envelope):
            raise RuntimeError("failed")

        with transaction.atomic():
            event = outbox.record_outbox_event(
                topic="accounts.user-created",
                payload={"user_id": 42},
            )

        outbox.process_outbox_batch()
        event.refresh_from_db()
        assert event.dead_lettered_at is not None
        assert event.attempts == 1

    def test_missing_handler_is_a_retryable_delivery_failure(self, settings):
        settings.OUTBOX_RETRY_BASE_SECONDS = 1
        with transaction.atomic():
            event = outbox.record_outbox_event(
                topic="accounts.user-created",
                payload={"user_id": 42},
            )

        outbox.process_outbox_batch()
        event.refresh_from_db()
        assert event.last_error_code == "LookupError"
        assert event.attempts == 1
        assert event.published_at is None

    def test_active_lease_prevents_second_claim(self):
        with transaction.atomic():
            event = outbox.record_outbox_event(
                topic="accounts.user-created",
                payload={"user_id": 42},
            )

        first = outbox.claim_outbox_batch(batch_size=1, lease_seconds=60)
        second = outbox.claim_outbox_batch(batch_size=1, lease_seconds=60)
        assert [claim.event_id for claim in first] == [event.pk]
        assert second == []

        OutboxEvent.objects.filter(pk=event.pk).update(
            locked_until=timezone.now() - timedelta(seconds=1)
        )
        third = outbox.claim_outbox_batch(batch_size=1, lease_seconds=60)
        assert [claim.event_id for claim in third] == [event.pk]

    def test_payload_limit_is_enforced_before_insert(self, settings):
        settings.OUTBOX_MAX_EVENT_BYTES = 32
        with transaction.atomic():
            with pytest.raises(ValueError, match="payload limit"):
                outbox.record_outbox_event(
                    topic="accounts.user-created",
                    payload={"value": "x" * 100},
                )
        assert not OutboxEvent.objects.exists()
