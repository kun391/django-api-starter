from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions
from rest_framework.response import Response

from apps.notifications.models import NotificationDelivery
from apps.notifications.services import preference_rows, set_preference
from apps.notifications.topics import SUPPORTED_NOTIFICATION_TOPICS

from .filters import NotificationDeliveryFilter
from .serializers import (
    NotificationDeliverySerializer,
    NotificationPreferenceSerializer,
    NotificationPreferenceUpdateSerializer,
)


class NoStoreMixin:
    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response


@extend_schema(tags=["notifications"])
class NotificationPreferenceListView(NoStoreMixin, generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = NotificationPreferenceSerializer

    @extend_schema(
        responses={200: NotificationPreferenceSerializer(many=True)},
        filters=False,
    )
    def get(self, request):
        return Response(preference_rows(user_id=request.user.pk))


@extend_schema(tags=["notifications"])
class NotificationPreferenceDetailView(NoStoreMixin, generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = NotificationPreferenceUpdateSerializer

    @extend_schema(
        request=NotificationPreferenceUpdateSerializer,
        responses={200: NotificationPreferenceSerializer},
        filters=False,
    )
    def put(self, request, topic):
        if topic not in SUPPORTED_NOTIFICATION_TOPICS:
            from django.http import Http404

            raise Http404()
        serializer = NotificationPreferenceUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        preference = set_preference(
            actor=request.user,
            topic=topic,
            email_enabled=serializer.validated_data["email_enabled"],
        )
        return Response(
            {
                "topic": preference.topic,
                "email_enabled": preference.email_enabled,
                "is_override": True,
            }
        )


@extend_schema(tags=["notifications"])
class NotificationDeliveryListView(NoStoreMixin, generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = NotificationDeliverySerializer
    filterset_class = NotificationDeliveryFilter
    ordering_fields = ["id", "created_at", "attempts", "delivered_at", "failed_at"]
    ordering = ["-created_at", "-id"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return NotificationDelivery.objects.none()
        return NotificationDelivery.objects.filter(
            recipient_user=self.request.user,
        )
