from django.urls import path

from .views import (
    WebhookDeliveryListView,
    WebhookReplayView,
    WebhookRotateSecretView,
    WebhookSubscriptionDetailView,
    WebhookSubscriptionListCreateView,
)

urlpatterns = [
    path(
        "organizations/<uuid:organization_id>/webhooks/",
        WebhookSubscriptionListCreateView.as_view(),
        name="organization-webhook-list",
    ),
    path(
        "organizations/<uuid:organization_id>/webhooks/<uuid:subscription_id>/",
        WebhookSubscriptionDetailView.as_view(),
        name="organization-webhook-detail",
    ),
    path(
        "organizations/<uuid:organization_id>/webhooks/<uuid:subscription_id>/rotate-secret/",
        WebhookRotateSecretView.as_view(),
        name="organization-webhook-rotate-secret",
    ),
    path(
        "organizations/<uuid:organization_id>/webhook-deliveries/",
        WebhookDeliveryListView.as_view(),
        name="organization-webhook-delivery-list",
    ),
    path(
        "organizations/<uuid:organization_id>/webhook-deliveries/<uuid:delivery_id>/replay/",
        WebhookReplayView.as_view(),
        name="organization-webhook-delivery-replay",
    ),
]
