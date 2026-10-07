from django.urls import path

from .views import (
    NotificationDeliveryListView,
    NotificationPreferenceDetailView,
    NotificationPreferenceListView,
)

urlpatterns = [
    path(
        "notifications/preferences/",
        NotificationPreferenceListView.as_view(),
        name="notification-preference-list",
    ),
    path(
        "notifications/preferences/<str:topic>/",
        NotificationPreferenceDetailView.as_view(),
        name="notification-preference-detail",
    ),
    path(
        "notifications/deliveries/",
        NotificationDeliveryListView.as_view(),
        name="notification-delivery-list",
    ),
]
