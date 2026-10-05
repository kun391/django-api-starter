from django.db import connection
from django.db.utils import OperationalError
from django.http import JsonResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny

from apps.core.celery import app


@api_view(["GET"])
@permission_classes([AllowAny])
def health_check(request):
    """Basic health check endpoint."""
    return JsonResponse(
        {
            "status": "healthy",
            "message": "Django API Template is running",
            "version": "1.0.0",
        }
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def database_health(request):
    """Database health check endpoint."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return JsonResponse(
            {
                "status": "healthy",
                "database": "connected",
            }
        )
    except OperationalError:
        return JsonResponse(
            {
                "status": "unhealthy",
                "database": "disconnected",
            },
            status=503,
        )


@api_view(["GET"])
@permission_classes([AllowAny])
def celery_health(request):
    """Celery health check endpoint."""
    try:
        inspect = app.control.inspect()
        stats = inspect.stats()

        if stats is None:
            return JsonResponse(
                {
                    "status": "unhealthy",
                    "celery": "no workers available",
                },
                status=503,
            )

        return JsonResponse(
            {
                "status": "healthy",
                "celery": "running",
                "workers": len(stats),
            }
        )
    except Exception as exc:
        return JsonResponse(
            {
                "status": "unhealthy",
                "celery": "error",
                "error": str(exc),
            },
            status=503,
        )
