from django.db.models import F
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import generics, permissions, serializers, status, viewsets
from rest_framework.response import Response

from apps.core.api.audit import AuditEventFilter, AuditEventSerializer
from apps.core.api.idempotency import idempotent_post
from apps.core.api.schema import IDEMPOTENCY_KEY_PARAMETER
from apps.core.models import AuditEvent
from apps.organizations import services
from apps.organizations.access import require_roles, resolve_access
from apps.organizations.models import Organization, OrganizationMembership

from .serializers import (
    MembershipCreateSerializer,
    MembershipSerializer,
    MembershipUpdateSerializer,
    OrganizationCreateSerializer,
    OrganizationSerializer,
    OrganizationUpdateSerializer,
)


def _annotated_organizations(actor):
    return (
        Organization.objects.filter(memberships__user_id=actor.pk)
        .annotate(actor_role=F("memberships__role"))
        .order_by("name", "id")
    )


@extend_schema_view(
    create=extend_schema(
        request=OrganizationCreateSerializer,
        responses={201: OrganizationSerializer},
        parameters=[IDEMPOTENCY_KEY_PARAMETER],
    ),
    partial_update=extend_schema(
        request=OrganizationUpdateSerializer,
        responses={200: OrganizationSerializer},
    ),
)
@extend_schema(tags=["organizations"])
class OrganizationViewSet(viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]
    search_fields = ["name", "slug"]
    ordering_fields = ["id", "name", "slug", "created_at"]
    ordering = ["name", "id"]

    def get_queryset(self):
        return _annotated_organizations(self.request.user)

    def get_serializer_class(self):
        if self.action == "create":
            return OrganizationCreateSerializer
        if self.action == "partial_update":
            return OrganizationUpdateSerializer
        return OrganizationSerializer

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response

    def list(self, request):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        return self.get_paginated_response(
            OrganizationSerializer(page, many=True).data
        )

    def retrieve(self, request, pk=None):
        return Response(OrganizationSerializer(self.get_object()).data)

    def create(self, request):
        serializer = OrganizationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        def operation():
            try:
                organization = services.create_organization(
                    actor=request.user, **serializer.validated_data
                )
            except services.OrganizationSlugConflict as exc:
                raise serializers.ValidationError(
                    {"slug": "This slug is already in use."}
                ) from exc
            organization.actor_role = "owner"
            return Response(
                OrganizationSerializer(organization).data,
                status=status.HTTP_201_CREATED,
            )

        return idempotent_post(
            request, operation, scope="organizations.create:v1"
        )

    def partial_update(self, request, pk=None):
        serializer = OrganizationUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        organization = services.rename_organization(
            actor=request.user,
            organization_id=pk,
            name=serializer.validated_data["name"],
        )
        organization.actor_role = "owner"
        return Response(OrganizationSerializer(organization).data)


@extend_schema(tags=["organization-memberships"])
class OrganizationMembersView(generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = MembershipSerializer

    def _access(self, *, owner_only=False):
        access = resolve_access(
            actor=self.request.user,
            organization_id=self.kwargs["organization_id"],
        )
        roles = (
            ("owner",)
            if owner_only
            else (
                "owner",
                "admin",
            )
        )
        require_roles(access, *roles)
        return access

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return OrganizationMembership.objects.none()
        access = self._access()
        return (
            OrganizationMembership.objects.filter(
                organization=access.organization
            )
            .select_related("user")
            .order_by("id")
        )

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response

    @extend_schema(responses={200: MembershipSerializer(many=True)})
    def get(self, request, organization_id):
        page = self.paginate_queryset(self.filter_queryset(self.get_queryset()))
        return self.get_paginated_response(MembershipSerializer(page, many=True).data)

    @extend_schema(
        request=MembershipCreateSerializer,
        responses={201: MembershipSerializer},
    )
    def post(self, request, organization_id):
        self._access(owner_only=True)
        serializer = MembershipCreateSerializer(data=request.data, context={})
        serializer.is_valid(raise_exception=True)
        try:
            membership = services.add_member(
                actor=request.user,
                organization_id=organization_id,
                user=serializer.context["target_user"],
                role=serializer.validated_data["role"],
            )
        except services.MembershipConflict as exc:
            raise serializers.ValidationError(str(exc)) from exc
        membership = OrganizationMembership.objects.select_related("user").get(
            pk=membership.pk
        )
        return Response(
            MembershipSerializer(membership).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["organization-memberships"])
class OrganizationMemberDetailView(generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = MembershipSerializer

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response

    def _owner_access(self):
        access = resolve_access(
            actor=self.request.user,
            organization_id=self.kwargs["organization_id"],
        )
        require_roles(access, "owner")

    @extend_schema(
        request=MembershipUpdateSerializer,
        responses={200: MembershipSerializer},
    )
    def patch(self, request, organization_id, user_id):
        self._owner_access()
        serializer = MembershipUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            membership = services.change_member_role(
                actor=request.user,
                organization_id=organization_id,
                user_id=user_id,
                role=serializer.validated_data["role"],
            )
        except services.MembershipConflict as exc:
            raise serializers.ValidationError(str(exc)) from exc
        membership = OrganizationMembership.objects.select_related("user").get(
            pk=membership.pk
        )
        return Response(MembershipSerializer(membership).data)

    @extend_schema(request=None, responses={204: None})
    def delete(self, request, organization_id, user_id):
        self._owner_access()
        try:
            services.remove_member(
                actor=request.user,
                organization_id=organization_id,
                user_id=user_id,
            )
        except services.MembershipConflict as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return Response(status=status.HTTP_204_NO_CONTENT)



@extend_schema(tags=["organization-audit"])
class OrganizationAuditEventListView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = AuditEventSerializer
    filterset_class = AuditEventFilter
    ordering_fields = ["id", "occurred_at", "action", "subject_type", "actor_id"]
    ordering = ["-occurred_at", "-id"]

    def _access(self):
        access = resolve_access(
            actor=self.request.user,
            organization_id=self.kwargs["organization_id"],
        )
        require_roles(access, "owner", "admin")
        return access

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return AuditEvent.objects.none()
        access = self._access()
        return AuditEvent.objects.filter(
            organization_id=access.organization.pk,
        )

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response
