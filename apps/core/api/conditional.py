"""Conditional GET/HEAD for explicitly reviewed, non-sensitive representations."""

from hashlib import sha256

from django.utils.cache import get_conditional_response, patch_vary_headers
from drf_spectacular.utils import OpenApiParameter
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import APIView

IF_NONE_MATCH_PARAMETER = OpenApiParameter(
    name="If-None-Match",
    location=OpenApiParameter.HEADER,
    required=False,
    type=str,
    description="ETag from a previous GET. A matching representation returns bodyless 304.",
)


class PreconditionFailed(APIException):
    status_code = 412
    default_detail = "The representation does not satisfy the request precondition."
    default_code = "precondition_failed"


class ConditionalGetMixin(APIView):
    """Opt in before a DRF generic view/viewset in the inheritance list.

    Authentication, permission checks and the handler always run first. This
    saves transfer bytes, not database work. Cache-aside is a separate decision.
    Explicit Cache-Control/ETag policies and non-JSON responses are untouched.
    """

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        if (
            request.method not in {"GET", "HEAD"}
            or not isinstance(response, Response)
            or response.status_code != 200
            or response.exception
            or response.cookies
            or "Cache-Control" in response
            or "ETag" in response
            or response.accepted_renderer.media_type != "application/json"
        ):
            return response

        # Hash the negotiated bytes, never repr(response.data). Weak validators
        # remain usable when a downstream layer applies transfer compression.
        response.render()
        if response.get("Content-Type", "").split(";", 1)[0] != "application/json":
            return response
        response["ETag"] = f'W/"{sha256(response.content).hexdigest()}"'
        response["Cache-Control"] = "private, no-cache, must-revalidate"
        patch_vary_headers(response, ("Authorization", "Cookie", "Accept", "Accept-Language"))
        conditional = get_conditional_response(request, etag=response["ETag"], response=response)
        if conditional.status_code == 412:
            # Keep Phase 7 Problem Details rather than Django's empty 412 body.
            error = self.handle_exception(PreconditionFailed())
            return super().finalize_response(request, error, *args, **kwargs)
        if request.method == "HEAD" and conditional.status_code == 200:
            conditional["Content-Length"] = str(len(conditional.content))
            conditional.content = b""
        return conditional
