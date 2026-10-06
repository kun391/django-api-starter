from django_filters import rest_framework as filters

from apps.tickets.models import Ticket


class TicketFilter(filters.FilterSet):
    class Meta:
        model = Ticket
        fields = ["status", "priority"]
