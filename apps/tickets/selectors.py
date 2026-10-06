from django.db.models import Count

from apps.core.caching import CachePolicy, cache_aside

from .models import Ticket

SUMMARY_CACHE = CachePolicy(
    "tickets.summary",
    version=1,
    ttl_seconds=60,
    max_bytes=8192,
)


def visible_tickets(actor):
    queryset = Ticket.objects.all()
    if actor.is_staff:
        return queryset
    return queryset.filter(owner_id=actor.pk)


def ticket_summary(actor):
    if not actor.is_authenticated or not actor.is_active:
        raise PermissionError("An active authenticated actor is required.")

    scope = f"staff:{actor.pk}" if actor.is_staff else f"user:{actor.pk}"

    def load():
        queryset = visible_tickets(actor)
        counts = {
            row["status"]: row["count"]
            for row in queryset.values("status").annotate(count=Count("id"))
        }
        attachment_count = queryset.aggregate(
            count=Count("attachments"),
        )["count"]
        return {
            "total": sum(counts.values()),
            "open": counts.get(Ticket.Status.OPEN, 0),
            "in_progress": counts.get(Ticket.Status.IN_PROGRESS, 0),
            "resolved": counts.get(Ticket.Status.RESOLVED, 0),
            "attachments": attachment_count,
        }

    return cache_aside(
        SUMMARY_CACHE,
        scope=scope,
        key={"representation": "v1"},
        load=load,
    )
