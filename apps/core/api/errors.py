"""RFC 9457 problem details with DRF's status codes and error codes."""

from http import HTTPStatus

from django.http import JsonResponse
from django.views import defaults
from django.views.csrf import csrf_failure as django_csrf_failure
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.renderers import JSONRenderer
from rest_framework.settings import api_settings
from rest_framework.views import exception_handler as drf_exception_handler

from apps.core.observability import get_request_id


def _flatten_errors(value, attr=None):
    if isinstance(value, dict):
        for field, child in value.items():
            path = attr
            if field != api_settings.NON_FIELD_ERRORS_KEY:
                path = f"{attr}.{field}" if attr else str(field)
            yield from _flatten_errors(child, path)
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            path = attr
            if isinstance(child, (dict, list, tuple)):
                path = f"{attr}.{index}" if attr else str(index)
            yield from _flatten_errors(child, path)
    else:
        yield {
            "attr": attr,
            "code": getattr(value, "code", "invalid"),
            "detail": str(value),
        }


def problem(status_code, code, detail, *, errors=()):
    return {
        "type": "about:blank",
        "title": HTTPStatus(status_code).phrase,
        "status": status_code,
        "code": code,
        "detail": detail,
        "errors": list(errors),
        "request_id": get_request_id(),
    }


def exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is None:
        # Re-raise unexpected failures: keep Django's rollback, exception signal,
        # logging and error reporting. handler500 supplies the safe public body.
        return None

    if isinstance(exc, ValidationError):
        code = "validation_error"
        detail = "Request validation failed."
        errors = list(_flatten_errors(exc.detail))
    else:
        codes = exc.get_codes() if isinstance(exc, APIException) else None
        code = codes if isinstance(codes, str) else {
            400: "bad_request",
            403: "permission_denied",
            404: "not_found",
        }.get(response.status_code, "api_error")
        detail = HTTPStatus(response.status_code).phrase
        if isinstance(response.data, dict):
            detail = str(response.data.get("detail", detail))
        errors = []

    response.data = problem(response.status_code, code, detail, errors=errors)
    response.content_type = "application/problem+json"
    response["Cache-Control"] = "no-store"
    # Failure bodies are JSON even for views with a custom YAML/HTML renderer.
    request = context.get("request")
    if request is not None:
        request.accepted_renderer = JSONRenderer()
        request.accepted_media_type = "application/json"
    # Keep the DRF response: WWW-Authenticate, Retry-After and Allow must survive.
    return response


def _django_problem(status_code, code, detail):
    response = JsonResponse(
        problem(status_code, code, detail),
        status=status_code,
        content_type="application/problem+json",
    )
    response["Cache-Control"] = "no-store"
    return response


def bad_request(request, exception):
    if request.path.startswith("/api/"):
        return _django_problem(400, "bad_request", "The request could not be processed.")
    return defaults.bad_request(request, exception)


def permission_denied(request, exception):
    if request.path.startswith("/api/"):
        return _django_problem(403, "permission_denied", "Permission denied.")
    return defaults.permission_denied(request, exception)


def not_found(request, exception):
    if request.path.startswith("/api/"):
        return _django_problem(404, "not_found", "Resource not found.")
    return defaults.page_not_found(request, exception)


def server_error(request):
    if request.path.startswith("/api/"):
        return _django_problem(500, "internal_error", "An unexpected error occurred.")
    return defaults.server_error(request)


def csrf_failure(request, reason=""):
    if request.path.startswith("/api/"):
        return _django_problem(403, "permission_denied", "CSRF verification failed.")
    return django_csrf_failure(request, reason=reason)
