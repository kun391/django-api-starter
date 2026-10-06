from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404

from apps.core.caching import invalidate_on_commit
from apps.core.outbox import record_outbox_event
from apps.files import services as file_services
from apps.organizations.access import resolve_access

from .models import Ticket, TicketAttachment
from .selectors import SUMMARY_CACHE


def _invalidate_personal_summary(*, owner_id):
    invalidate_on_commit(SUMMARY_CACHE, scope=f"user:{owner_id}")
    invalidate_on_commit(SUMMARY_CACHE, scope="staff:global")


def _invalidate_organization_summary(*, organization_id):
    invalidate_on_commit(
        SUMMARY_CACHE,
        scope=f"organization:{organization_id}",
    )


def _locked_personal_ticket(ticket_id, *, actor):
    queryset = Ticket.objects.select_for_update().filter(
        pk=ticket_id,
        organization__isnull=True,
    )
    if not actor.is_staff:
        queryset = queryset.filter(owner_id=actor.pk)
    ticket = queryset.first()
    if ticket is None:
        raise Http404()
    return ticket


def _locked_organization_ticket(ticket_id, *, organization_id):
    ticket = (
        Ticket.objects.select_for_update()
        .filter(pk=ticket_id, organization_id=organization_id)
        .first()
    )
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
                "organization_id": None,
                "priority": ticket.priority,
            },
        )
        _invalidate_personal_summary(owner_id=ticket.owner_id)
    return ticket


def create_organization_ticket(
    *,
    actor,
    organization_id,
    title,
    description="",
    priority=Ticket.Priority.NORMAL,
):
    access = resolve_access(actor=actor, organization_id=organization_id)
    with transaction.atomic():
        ticket = Ticket.objects.create(
            organization=access.organization,
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
                "organization_id": str(access.organization.pk),
                "priority": ticket.priority,
            },
        )
        _invalidate_organization_summary(
            organization_id=access.organization.pk,
        )
    return ticket


def update_ticket(*, actor, ticket_id, changes):
    with transaction.atomic():
        ticket = _locked_personal_ticket(ticket_id, actor=actor)

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
                    "organization_id": None,
                    "changed_fields": sorted(changed_fields),
                    "status": ticket.status,
                },
            )
            _invalidate_personal_summary(owner_id=ticket.owner_id)
    return ticket


def update_organization_ticket(
    *,
    actor,
    organization_id,
    ticket_id,
    changes,
):
    access = resolve_access(actor=actor, organization_id=organization_id)
    with transaction.atomic():
        ticket = _locked_organization_ticket(
            ticket_id,
            organization_id=access.organization.pk,
        )

        status = changes.get("status")
        if status is not None and not access.can_manage_tickets:
            raise PermissionDenied("Only tenant owner/admin may change ticket status.")
        if ticket.status == Ticket.Status.RESOLVED and not access.can_manage_tickets:
            raise PermissionDenied("Resolved tickets are read-only for members.")

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
                    "organization_id": str(access.organization.pk),
                    "changed_fields": sorted(changed_fields),
                    "status": ticket.status,
                },
            )
            _invalidate_organization_summary(
                organization_id=access.organization.pk,
            )
    return ticket


def attach_file(*, actor, ticket_id, file_id):
    with transaction.atomic():
        ticket = _locked_personal_ticket(ticket_id, actor=actor)
        record = file_services.owned_file(file_id, actor=actor)
        attachment = TicketAttachment.objects.create(ticket=ticket, file=record)
        record_outbox_event(
            topic="tickets.attachment-added",
            payload={
                "ticket_id": str(ticket.pk),
                "owner_id": ticket.owner_id,
                "organization_id": None,
                "file_id": str(record.pk),
            },
        )
        _invalidate_personal_summary(owner_id=ticket.owner_id)
    return attachment


def attach_organization_file(
    *,
    actor,
    organization_id,
    ticket_id,
    file_id,
):
    access = resolve_access(actor=actor, organization_id=organization_id)
    with transaction.atomic():
        ticket = _locked_organization_ticket(
            ticket_id,
            organization_id=access.organization.pk,
        )
        record = file_services.owned_file(file_id, actor=actor)
        attachment = TicketAttachment.objects.create(ticket=ticket, file=record)
        record_outbox_event(
            topic="tickets.attachment-added",
            payload={
                "ticket_id": str(ticket.pk),
                "owner_id": ticket.owner_id,
                "organization_id": str(access.organization.pk),
                "file_id": str(record.pk),
            },
        )
        _invalidate_organization_summary(
            organization_id=access.organization.pk,
        )
    return attachment
