from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import User

from .serializers import UserCreateSerializer, UserSerializer, UserUpdateSerializer


class UserViewSet(viewsets.ModelViewSet):
    """User administration plus self-service profile endpoints."""

    queryset = User.objects.all().order_by("id")

    def get_permissions(self):
        if self.action == "create":
            permission_classes = [permissions.AllowAny]
        elif self.action in {"me", "update_me"}:
            permission_classes = [permissions.IsAuthenticated]
        else:
            permission_classes = [permissions.IsAdminUser]
        return [permission() for permission in permission_classes]

    def get_serializer_class(self):
        if self.action == "create":
            return UserCreateSerializer
        if self.action in {"update", "partial_update", "update_me"}:
            return UserUpdateSerializer
        return UserSerializer

    @action(detail=False, methods=["get"])
    def me(self, request):
        serializer = UserSerializer(request.user)
        return Response(serializer.data)

    @action(detail=False, methods=["put", "patch"])
    def update_me(self, request):
        serializer = UserUpdateSerializer(
            request.user,
            data=request.data,
            partial=request.method == "PATCH",
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)
