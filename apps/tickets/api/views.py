from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.api.conditional import IF_NONE_MATCH_PARAMETER, ConditionalGetMixin
from apps.core.api.idempotency import idempotent_post
from apps.core.api.schema import IDEMPOTENCY_KEY_PARAMETER
from apps.tickets import selectors, services

from .filters import TicketFilter
from .serializers import (
    TicketAttachmentCreateSerializer,
    TicketAttachmentSerializer,
    TicketCreateSerializer,
    TicketSerializer,
    TicketSummarySerializer,
    TicketUpdateSerializer,
)


@extend_schema_view(
    list=extend_schema(parameters=[IF_NONE_MATCH_PARAMETER]),
    retrieve=extend_schema(parameters=[IF_NONE_MATCH_PARAMETER]),
    create=extend_schema(
        request=TicketCreateSerializer,
        responses={201: TicketSerializer},
        parameters=[IDEMPOTENCY_KEY_PARAMETER],
    ),
    partial_update=extend_schema(
        request=TicketUpdateSerializer,
        responses={200: TicketSerializer},
    ),
)
@extend_schema(tags=["tickets"])
class TicketViewSet(ConditionalGetMixin, viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]
    filterset_class = TicketFilter
    search_fields = ["title", "description"]
    ordering_fields = ["id", "title", "created_at", "updated_at"]
    ordering = ["-created_at", "-id"]

    def get_queryset(self):
        return (
            selectors.visible_tickets(self.request.user)
            .select_related("owner")
            .prefetch_related("attachments__file")
        )

    def get_serializer_class(self):
        if self.action == "create":
            return TicketCreateSerializer
        if self.action == "partial_update":
            return TicketUpdateSerializer
        if self.action == "attach":
            return TicketAttachmentCreateSerializer
        if self.action == "summary":
            return TicketSummarySerializer
        return TicketSerializer

    def list(self, request):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        serializer = TicketSerializer(
            page,
            many=True,
            context=self.get_serializer_context(),
        )
        return self.get_paginated_response(serializer.data)

    def retrieve(self, request, pk=None):
        ticket = self.get_object()
        return Response(TicketSerializer(ticket).data)

    def create(self, request):
        serializer = TicketCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        def operation():
            ticket = services.create_ticket(
                actor=request.user,
                **serializer.validated_data,
            )
            return Response(
                TicketSerializer(ticket).data,
                status=status.HTTP_201_CREATED,
            )

        return idempotent_post(
            request,
            operation,
            scope="tickets.create:v1",
        )

    def partial_update(self, request, pk=None):
        serializer = TicketUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ticket = services.update_ticket(
            actor=request.user,
            ticket_id=pk,
            changes=serializer.validated_data,
        )
        ticket = self.get_queryset().get(pk=ticket.pk)
        return Response(TicketSerializer(ticket).data)

    @extend_schema(
        request=TicketAttachmentCreateSerializer,
        responses={201: TicketAttachmentSerializer},
        filters=False,
    )
    @action(detail=True, methods=["post"], url_path="attachments")
    def attach(self, request, pk=None):
        serializer = TicketAttachmentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        attachment = services.attach_file(
            actor=request.user,
            ticket_id=pk,
            file_id=serializer.validated_data["file_id"],
        )
        attachment = TicketAttachmentSerializer(attachment)
        return Response(attachment.data, status=status.HTTP_201_CREATED)

    @extend_schema(
        responses={200: TicketSummarySerializer},
        parameters=[IF_NONE_MATCH_PARAMETER],
        filters=False,
    )
    @action(detail=False, methods=["get"], pagination_class=None)
    def summary(self, request):
        return Response(selectors.ticket_summary(request.user))
