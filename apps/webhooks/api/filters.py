from django_filters import rest_framework as filters

from apps.webhooks.models import WebhookDelivery, WebhookSubscription


class WebhookSubscriptionFilter(filters.FilterSet):
    event = filters.CharFilter(method="filter_event")

    class Meta:
        model = WebhookSubscription
        fields = ["is_active"]

    def filter_event(self, queryset, _name, value):
        return queryset.filter(events__contains=[value])


class WebhookDeliveryFilter(filters.FilterSet):
    subscription_id = filters.UUIDFilter(field_name="subscription_id")

    class Meta:
        model = WebhookDelivery
        fields = ["source_event_id", "event_topic"]
