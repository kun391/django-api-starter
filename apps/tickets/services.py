from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404

from apps.core.caching import invalidate_on_commit
from apps.core.outbox import record_outbox_event
from apps.files import services as file_services

from .models import Ticket, TicketAttachment
from .selectors import SUMMARY_CACHE


def _summary_scope(owner_id, *, staff_actor_id=None):
    if staff_actor_id is not None:
        return f"staff:{staff_actor_id}"
    return f"user:{owner_id}"


def _invalidate_summary(*, owner_id, actor):
    invalidate_on_commit(
        SUMMARY_CACHE,
        scope=_summary_scope(owner_id),
    )
    if actor.is_staff:
        invalidate_on_commit(
            SUMMARY_CACHE,
            scope=_summary_scope(owner_id, staff_actor_id=actor.pk),
        )


def _locked_ticket(ticket_id, *, actor):
    queryset = Ticket.objects.select_for_update().filter(pk=ticket_id)
    if not actor.is_staff:
        queryset = queryset.filter(owner_id=actor.pk)
    ticket = queryset.first()
    if ticket is None:
        raise Http404()
    return ticket


def create_ticket(*, actor, title, description="", priority=Ticket.Priority.NORMAL):
    with transaction.atomic():
        ticket = Ticket.objects.create(
            owner=actor,
            title=title,
            description=description,
            priority=priority,
        )
        record_outbox_event(
            topic="tickets.ticket-created",
            payload={
                "ticket_id": str(ticket.pk),
                "owner_id": ticket.owner_id,
                "priority": ticket.priority,
            },
        )
        _invalidate_summary(owner_id=ticket.owner_id, actor=actor)
    return ticket


def update_ticket(*, actor, ticket_id, changes):
    with transaction.atomic():
        ticket = _locked_ticket(ticket_id, actor=actor)

        status = changes.get("status")
        if status is not None and not actor.is_staff:
            raise PermissionDenied("Only staff may change ticket status.")
        if ticket.status == Ticket.Status.RESOLVED and not actor.is_staff:
            raise PermissionDenied("Resolved tickets are read-only for their owner.")

        changed_fields = []
        for field in ("title", "description", "priority"):
            if field in changes:
                setattr(ticket, field, changes[field])
                changed_fields.append(field)
        if status is not None:
            ticket.status = status
            changed_fields.append("status")

        if changed_fields:
            ticket.save(update_fields=[*changed_fields, "updated_at"])
            record_outbox_event(
                topic="tickets.ticket-updated",
                payload={
                    "ticket_id": str(ticket.pk),
                    "owner_id": ticket.owner_id,
                    "changed_fields": sorted(changed_fields),
                    "status": ticket.status,
                },
            )
            _invalidate_summary(owner_id=ticket.owner_id, actor=actor)
    return ticket


def attach_file(*, actor, ticket_id, file_id):
    with transaction.atomic():
        ticket = _locked_ticket(ticket_id, actor=actor)
        record = file_services.owned_file(file_id, actor=actor)
        attachment = TicketAttachment.objects.create(ticket=ticket, file=record)
        record_outbox_event(
            topic="tickets.attachment-added",
            payload={
                "ticket_id": str(ticket.pk),
                "owner_id": ticket.owner_id,
                "file_id": str(record.pk),
            },
        )
        _invalidate_summary(owner_id=ticket.owner_id, actor=actor)
    return attachment
