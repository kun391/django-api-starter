"""Project-level diagnostics for optional runtime integrations."""

from importlib.util import find_spec

from django.http import JsonResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny


def _response(data: dict[str, object], *, status: int = 200) -> JsonResponse:
    response = JsonResponse(data, status=status)
    response["Cache-Control"] = "no-store"
    return response


@api_view(["GET"])
@permission_classes([AllowAny])
def celery_health(request):
    """Optional worker diagnostic, not an API readiness dependency."""
    if find_spec("celery") is None:
        return _response({"status": "disabled", "celery": "not installed"}, status=503)
    try:
        # Importing is optional; the minimal API image must not require Celery.
        from config.celery import app

        stats = app.control.inspect(timeout=2).stats()
        if stats is None:
            return _response(
                {"status": "unhealthy", "celery": "no workers available"}, status=503
            )
        return _response({
            "status": "healthy", "celery": "running", "workers": len(stats)
        })
    except Exception:
        return _response({"status": "unhealthy", "celery": "unavailable"}, status=503)
