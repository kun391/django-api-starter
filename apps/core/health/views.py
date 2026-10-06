from importlib.util import find_spec

from django.db import connection
from django.db.utils import DatabaseError
from django.http import JsonResponse
from rest_framework.decorators import api_view, authentication_classes, permission_classes
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
    """A bounded DB connectivity probe; release_check verifies schema separately."""
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


@api_view(["GET"])
@permission_classes([AllowAny])
def celery_health(request):
    """Optional worker diagnostic, not a required API readiness dependency."""
    if find_spec("celery") is None:
        return _response({"status": "disabled", "celery": "not installed"}, status=503)
    try:
        from apps.core.celery import app

        stats = app.control.inspect(timeout=2).stats()
        if stats is None:
            return _response({"status": "unhealthy", "celery": "no workers available"}, status=503)
        return _response({"status": "healthy", "celery": "running", "workers": len(stats)})
    except Exception:
        return _response({"status": "unhealthy", "celery": "unavailable"}, status=503)
