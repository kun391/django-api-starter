from django.urls import path

from .views import QueueOperationsView

urlpatterns = [
    path(
        "operations/queues/",
        QueueOperationsView.as_view(),
        name="operations-queues",
    ),
]
