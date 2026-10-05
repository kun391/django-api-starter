from importlib.util import find_spec

from django.db import connection
from django.db.utils import OperationalError
from django.http import JsonResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny


@api_view(["GET"])
@permission_classes([AllowAny])
def live(request):
    """Liveness: the Django process can serve requests."""
    return JsonResponse({"status": "ok"})


@api_view(["GET"])
@permission_classes([AllowAny])
def ready(request):
    """Readiness: critical dependencies required to serve traffic are available."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except OperationalError:
        return JsonResponse(
            {"status": "not_ready", "database": "unavailable"},
            status=503,
        )

    return JsonResponse({"status": "ready", "database": "ok"})


@api_view(["GET"])
@permission_classes([AllowAny])
def health_check(request):
    """Backward-compatible health endpoint. Prefer /health/live/."""
    return live(request)


@api_view(["GET"])
@permission_classes([AllowAny])
def database_health(request):
    """Backward-compatible database health endpoint. Prefer /health/ready/."""
    return ready(request)


@api_view(["GET"])
@permission_classes([AllowAny])
def celery_health(request):
    """Optional Celery worker health check."""
    if find_spec("celery") is None:
        return JsonResponse(
            {"status": "disabled", "celery": "not installed"},
            status=503,
        )

    try:
        from apps.core.celery import app

        stats = app.control.inspect().stats()
        if stats is None:
            return JsonResponse(
                {"status": "unhealthy", "celery": "no workers available"},
                status=503,
            )
        return JsonResponse(
            {
                "status": "healthy",
                "celery": "running",
                "workers": len(stats),
            }
        )
    except Exception:
        return JsonResponse(
            {"status": "unhealthy", "celery": "unavailable"},
            status=503,
        )
