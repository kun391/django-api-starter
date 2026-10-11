from django.db import connection
from django.db.utils import DatabaseError
from django.http import JsonResponse
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.permissions import AllowAny


def _response(data, *, status=200):
    response = JsonResponse(data, status=status)
    response["Cache-Control"] = "no-store"
    return response


def _live_response():
    return _response({"status": "ok"})


def _ready_response():
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except DatabaseError:
        return _response({"status": "not_ready", "database": "unavailable"}, status=503)
    return _response({"status": "ready", "database": "ok"})


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def live(request):
    """Liveness depends only on this process, never an incoming credential."""
    return _live_response()


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def ready(request):
    """A DB connectivity probe; release_check verifies schema separately."""
    return _ready_response()


@api_view(["GET"])
@permission_classes([AllowAny])
def health_check(request):
    """Backward-compatible health endpoint. Prefer /health/live/."""
    return _live_response()


@api_view(["GET"])
@permission_classes([AllowAny])
def database_health(request):
    """Backward-compatible DB endpoint. Prefer /health/ready/."""
    return _ready_response()

