from typing import Any

from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions, serializers, status
from rest_framework.response import Response

from apps.organizations.access import require_roles, resolve_access
from apps.webhooks import services
from apps.webhooks.models import WebhookDelivery, WebhookSubscription

from .filters import WebhookDeliveryFilter, WebhookSubscriptionFilter
from .serializers import (
    WebhookDeliverySerializer,
    WebhookRotationSerializer,
    WebhookSubscriptionCreateSerializer,
    WebhookSubscriptionSerializer,
    WebhookSubscriptionUpdateSerializer,
)


class OrganizationWebhookAccessMixin:
    permission_classes = [permissions.IsAuthenticated]
    request: Any
    kwargs: dict[str, Any]

    def _access(self):
        access = resolve_access(
            actor=self.request.user,
            organization_id=self.kwargs["organization_id"],
        )
        require_roles(access, "owner", "admin")
        return access

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response


@extend_schema(tags=["organization-webhooks"])
class WebhookSubscriptionListCreateView(
    OrganizationWebhookAccessMixin,
    generics.GenericAPIView,
):
    filterset_class = WebhookSubscriptionFilter
    ordering_fields = ["id", "created_at", "updated_at", "url"]
    ordering = ["created_at", "id"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return WebhookSubscription.objects.none()
        access = self._access()
        return WebhookSubscription.objects.filter(
            organization=access.organization,
        )

    @extend_schema(responses={200: WebhookSubscriptionSerializer(many=True)})
    def get(self, request, organization_id):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        return self.get_paginated_response(
            WebhookSubscriptionSerializer(page, many=True).data
        )

    @extend_schema(
        request=WebhookSubscriptionCreateSerializer,
        responses={201: WebhookSubscriptionSerializer},
    )
    def post(self, request, organization_id):
        serializer = WebhookSubscriptionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        subscription = services.create_subscription(
            actor=request.user,
            organization_id=organization_id,
            **serializer.validated_data,
        )
        return Response(
            WebhookSubscriptionSerializer(subscription).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["organization-webhooks"])
class WebhookSubscriptionDetailView(
    OrganizationWebhookAccessMixin,
    generics.GenericAPIView,
):
    serializer_class = WebhookSubscriptionSerializer

    def _subscription(self):
        access = self._access()
        subscription = WebhookSubscription.objects.filter(
            pk=self.kwargs["subscription_id"],
            organization=access.organization,
        ).first()
        if subscription is None:
            from django.http import Http404

            raise Http404()
        return subscription

    def get(self, request, organization_id, subscription_id):
        return Response(WebhookSubscriptionSerializer(self._subscription()).data)

    @extend_schema(
        request=WebhookSubscriptionUpdateSerializer,
        responses={200: WebhookSubscriptionSerializer},
    )
    def patch(self, request, organization_id, subscription_id):
        serializer = WebhookSubscriptionUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        subscription = services.update_subscription(
            actor=request.user,
            organization_id=organization_id,
            subscription_id=subscription_id,
            changes=serializer.validated_data,
        )
        return Response(WebhookSubscriptionSerializer(subscription).data)

    @extend_schema(request=None, responses={204: None})
    def delete(self, request, organization_id, subscription_id):
        services.deactivate_subscription(
            actor=request.user,
            organization_id=organization_id,
            subscription_id=subscription_id,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["organization-webhooks"])
class WebhookRotateSecretView(
    OrganizationWebhookAccessMixin,
    generics.GenericAPIView,
):
    serializer_class = WebhookRotationSerializer

    @extend_schema(request=None, responses={200: WebhookRotationSerializer})
    def post(self, request, organization_id, subscription_id):
        subscription = services.rotate_secret(
            actor=request.user,
            organization_id=organization_id,
            subscription_id=subscription_id,
        )
        return Response({"secret_version": subscription.secret_version})


@extend_schema(tags=["organization-webhooks"])
class WebhookDeliveryListView(
    OrganizationWebhookAccessMixin,
    generics.ListAPIView,
):
    serializer_class = WebhookDeliverySerializer
    filterset_class = WebhookDeliveryFilter
    ordering_fields = ["id", "created_at", "attempts", "delivered_at", "failed_at"]
    ordering = ["-created_at", "-id"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return WebhookDelivery.objects.none()
        access = self._access()
        return WebhookDelivery.objects.filter(
            subscription__organization=access.organization,
        ).select_related("subscription")


@extend_schema(tags=["organization-webhooks"])
class WebhookReplayView(
    OrganizationWebhookAccessMixin,
    generics.GenericAPIView,
):
    serializer_class = WebhookDeliverySerializer

    @extend_schema(request=None, responses={201: WebhookDeliverySerializer})
    def post(self, request, organization_id, delivery_id):
        try:
            delivery = services.replay_delivery(
                actor=request.user,
                organization_id=organization_id,
                delivery_id=delivery_id,
            )
        except services.WebhookReplayConflict as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return Response(
            WebhookDeliverySerializer(delivery).data,
            status=status.HTTP_201_CREATED,
        )
