from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import TokenRevokeView, TokenView, UserViewSet

router = DefaultRouter()
router.register("users", UserViewSet, basename="user")

urlpatterns = [
    path("auth/token/", TokenView.as_view(), name="auth-token"),
    path("auth/token/revoke/", TokenRevokeView.as_view(), name="auth-token-revoke"),
    path("", include(router.urls)),
]
