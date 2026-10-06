"""Strong resource validators for explicit write concurrency control."""

from hashlib import sha256

from django.utils.cache import get_conditional_response, patch_vary_headers
from drf_spectacular.utils import OpenApiParameter
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.api.conditional import PreconditionFailed

IF_MATCH_PARAMETER = OpenApiParameter(
    name="If-Match",
    location=OpenApiParameter.HEADER,
    required=True,
    type=str,
    description=(
        "Strong ETag from the latest resource GET. The mutation is rejected "
        "with 428 when missing or 412 when stale."
    ),
)


class PreconditionRequired(APIException):
    status_code = 428
    default_detail = "This mutation requires an If-Match precondition."
    default_code = "precondition_required"


class WritePreconditionFailed(APIException):
    status_code = 412
    default_detail = "The resource changed since the supplied validator was issued."
    default_code = "precondition_failed"


def resource_etag(resource_type: str, resource_id, revision: int) -> str:
    """Return a stable strong ETag without exposing mutable representation bytes."""
    identity = f"{resource_type}:{resource_id}:{revision}".encode()
    return f'"{sha256(identity).hexdigest()}"'


def require_if_match(header_value: str | None, *, current_etag: str) -> None:
    """Require a strong validator matching the row version already locked by caller."""
    if header_value is None:
        raise PreconditionRequired()

    value = header_value.strip()
    if value == "*":
        return
    if not value:
        raise WritePreconditionFailed()

    # Server-issued validators contain no commas, so a normal If-Match list can
    # be compared safely without accepting weak validators as strong matches.
    validators = [item.strip() for item in value.split(",")]
    if current_etag in validators:
        return
    raise WritePreconditionFailed()


def mark_resource_response(response: Response, *, etag: str) -> Response:
    response["ETag"] = etag
    response._strong_resource_validator = True  # type: ignore[attr-defined]
    return response


class StrongResourceETagMixin(APIView):
    """Process explicit strong resource ETags without changing weak list caching."""

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        if not getattr(response, "_strong_resource_validator", False):
            return response

        if request.method not in {"GET", "HEAD"}:
            response["Cache-Control"] = "no-store"
            return response

        if (
            not isinstance(response, Response)
            or response.status_code != 200
            or response.exception
            or response.cookies
        ):
            return response

        response.render()
        response["Cache-Control"] = "private, no-cache, must-revalidate"
        patch_vary_headers(
            response,
            ("Authorization", "Cookie", "Accept", "Accept-Language"),
        )
        conditional = get_conditional_response(
            request,
            etag=response["ETag"],
            response=response,
        )
        if conditional.status_code == 412:
            error = self.handle_exception(PreconditionFailed())
            return super().finalize_response(request, error, *args, **kwargs)
        if conditional.status_code == 304:
            conditional["Content-Length"] = str(len(response.content))
        elif request.method == "HEAD" and conditional.status_code == 200:
            conditional["Content-Length"] = str(len(conditional.content))
            conditional.content = b""
        return conditional
