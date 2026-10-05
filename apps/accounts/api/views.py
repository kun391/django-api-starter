from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import permissions, serializers, status, viewsets
from rest_framework.authtoken.serializers import AuthTokenSerializer
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.decorators import action
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response

from apps.accounts.models import User

from .filters import UserFilter
from .serializers import UserCreateSerializer, UserSerializer, UserUpdateSerializer


class TokenSerializer(serializers.Serializer):
    token = serializers.CharField(read_only=True)


class TokenView(ObtainAuthToken):
    renderer_classes = [JSONRenderer]

    @extend_schema(request=AuthTokenSerializer, responses={200: TokenSerializer}, tags=["accounts"])
    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response


@extend_schema_view(
    create=extend_schema(request=UserCreateSerializer, responses={201: UserSerializer}),
    update=extend_schema(request=UserUpdateSerializer, responses={200: UserSerializer}),
    partial_update=extend_schema(request=UserUpdateSerializer, responses={200: UserSerializer}),
)
@extend_schema(tags=["accounts"])
class UserViewSet(viewsets.ModelViewSet):
    """User administration plus self-service profile endpoints."""

    queryset = User.objects.all().order_by("id")
    serializer_class = UserSerializer
    filterset_class = UserFilter
    search_fields = ["username", "email", "first_name", "last_name"]
    ordering_fields = ["id", "username", "email", "date_joined"]
    ordering = ["id"]

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

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        data = UserSerializer(serializer.instance, context=self.get_serializer_context()).data
        return Response(data, status=status.HTTP_201_CREATED, headers=self.get_success_headers(data))

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        if getattr(instance, "_prefetched_objects_cache", None):
            instance._prefetched_objects_cache = {}
        return Response(UserSerializer(instance, context=self.get_serializer_context()).data)

    @extend_schema(responses=UserSerializer, filters=False)
    @action(detail=False, methods=["get"], pagination_class=None)
    def me(self, request):
        serializer = UserSerializer(request.user)
        return Response(serializer.data)

    @extend_schema(request=UserUpdateSerializer, responses=UserSerializer, filters=False)
    @action(detail=False, methods=["put", "patch"], pagination_class=None)
    def update_me(self, request):
        serializer = UserUpdateSerializer(
            request.user,
            data=request.data,
            partial=request.method == "PATCH",
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(UserSerializer(serializer.instance).data, status=status.HTTP_200_OK)
