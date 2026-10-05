"""Test-only failure endpoints; never mounted in the application URLconf."""

from django.core.exceptions import SuspiciousOperation
from django.urls import include, path
from rest_framework.exceptions import Throttled, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from apps.core.urls import handler400, handler403, handler404, handler500  # noqa: F401


class FailureView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, kind):
        if kind == "throttle":
            raise Throttled(wait=7)
        if kind == "validation":
            raise ValidationError({"items": [{"email": ["Invalid address."]}], "non_field_errors": ["Invalid combination."]})
        if kind == "bad-request":
            raise SuspiciousOperation("private-internal-reason")
        raise RuntimeError("private-internal-reason")


urlpatterns = [
    path("api/v1/test-failure/<str:kind>/", FailureView.as_view()),
    path("", include("apps.core.urls")),
]
