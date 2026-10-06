from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.http import FileResponse
from django.utils.decorators import method_decorator
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, serializers
from rest_framework.exceptions import APIException
from rest_framework.parsers import MultiPartParser
from rest_framework.renderers import BaseRenderer, JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.api.throttling import PostgresFixedWindowThrottle
from apps.files import services
from apps.files.validation import UploadTooLarge

from .serializers import (
    DownloadURLSerializer,
    PrivateFileSerializer,
    ReplaceSerializer,
    UploadSerializer,
)


class UploadLimitExceeded(APIException):
    status_code = 413
    default_detail = "The upload exceeds the size or file-count limit."
    default_code = "upload_too_large"


class StorageUnavailable(APIException):
    status_code = 503
    default_detail = "File storage is temporarily unavailable."
    default_code = "file_storage_unavailable"


class UploadConflict(APIException):
    status_code = 409
    default_detail = "The upload expired or the file was replaced or deleted."
    default_code = "file_conflict"


class PrivateMultipartParser(MultiPartParser):
    def parse(self, stream, media_type=None, parser_context=None):
        request = parser_context["request"]
        length = request.META.get("CONTENT_LENGTH", "")
        if length.isdigit() and int(length) > settings.PRIVATE_FILE_MAX_BYTES + 65536:
            raise UploadLimitExceeded()
        result = super().parse(stream, media_type, parser_context)
        if getattr(request._request, "private_upload_rejected", False):
            raise UploadLimitExceeded()
        return result


class UploadThrottle(PostgresFixedWindowThrottle):
    scope = "private_upload"

    def get_identity(self, request):
        return str(request.user.pk)


class PrivateFileView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if request.query_params:
            raise serializers.ValidationError("This endpoint does not accept query parameters.")

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        response["X-Content-Type-Options"] = "nosniff"
        response["Referrer-Policy"] = "no-referrer"
        return response

    def handle_exception(self, exc):
        if isinstance(exc, UploadTooLarge):
            exc = UploadLimitExceeded()
        elif isinstance(exc, DjangoValidationError):
            exc = serializers.ValidationError({"file": exc.messages}, code=getattr(exc, "code", "invalid"))
        elif isinstance(exc, services.FileConflict):
            exc = UploadConflict()
        elif isinstance(exc, services.FileStorageUnavailable):
            exc = StorageUnavailable()
        return super().handle_exception(exc)


def parse_upload(request, serializer_class):
    data = request.data
    # Session CSRF may parse Django's POST/FILES before DRF selects its parser.
    # Preserve a receive-side rejection even when DRF reuses that parsed form.
    if getattr(request._request, "private_upload_rejected", False):
        raise UploadLimitExceeded()
    allowed = set(serializer_class().fields)
    if set(data) - allowed or any(len(data.getlist(key)) != 1 for key in data):
        raise serializers.ValidationError("Unknown or repeated multipart fields.")
    if set(request.FILES) != {"file"} or len(request.FILES.getlist("file")) != 1:
        raise serializers.ValidationError({"file": "Exactly one file is required."})
    serializer = serializer_class(data=data)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


@method_decorator(transaction.non_atomic_requests, name="dispatch")
class FileUploadView(PrivateFileView):
    parser_classes = [PrivateMultipartParser]
    throttle_classes = [UploadThrottle]

    @extend_schema(request=UploadSerializer, responses={201: PrivateFileSerializer}, tags=["files"])
    def post(self, request):
        data = parse_upload(request, UploadSerializer)
        record = services.upload_file(actor=request.user, upload=data["file"], purpose=data["purpose"])
        return Response(PrivateFileSerializer(record).data, status=201)


class FileDetailView(PrivateFileView):
    @extend_schema(responses=PrivateFileSerializer, tags=["files"])
    def get(self, request, file_id):
        return Response(PrivateFileSerializer(services.owned_file(file_id, actor=request.user)).data)

    @extend_schema(request=None, responses={204: None}, tags=["files"])
    def delete(self, request, file_id):
        services.delete_file(file_id, actor=request.user)
        return Response(status=204)


@method_decorator(transaction.non_atomic_requests, name="dispatch")
class FileReplaceView(PrivateFileView):
    parser_classes = [PrivateMultipartParser]
    throttle_classes = [UploadThrottle]

    @extend_schema(request=ReplaceSerializer, responses={201: PrivateFileSerializer}, tags=["files"])
    def post(self, request, file_id):
        services.owned_file(file_id, actor=request.user)
        data = parse_upload(request, ReplaceSerializer)
        record = services.upload_file(actor=request.user, upload=data["file"], replace_id=file_id)
        return Response(PrivateFileSerializer(record).data, status=201)


class BinaryRenderer(BaseRenderer):
    media_type = "application/octet-stream"
    format = "bin"
    charset = None

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data  # FileResponse streams itself; error responses use JSONRenderer.


class FileDownloadView(PrivateFileView):
    renderer_classes = [JSONRenderer, BinaryRenderer]

    @extend_schema(responses={(200, "application/octet-stream"): OpenApiTypes.BINARY}, tags=["files"])
    def get(self, request, file_id):
        record, stream = services.open_file(file_id, actor=request.user)
        return FileResponse(stream, as_attachment=True, filename=record.original_name, content_type="application/octet-stream")


class FileDownloadURLView(PrivateFileView):
    @extend_schema(request=None, responses=DownloadURLSerializer, tags=["files"])
    def post(self, request, file_id):
        return Response(services.download_url(file_id, actor=request.user))
