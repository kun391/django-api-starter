from django_filters import rest_framework as filters

from apps.notifications.models import NotificationDelivery


class NotificationDeliveryFilter(filters.FilterSet):
    class Meta:
        model = NotificationDelivery
        fields = ["source_event_id", "event_topic"]
