"""Bounded content validation; no antivirus or arbitrary binary-file claim."""

import json
import re
import unicodedata
from dataclasses import dataclass
from hashlib import sha256
from pathlib import PurePosixPath

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.files.uploadhandler import FileUploadHandler, StopUpload
from django.utils.module_loading import import_string


class UploadTooLarge(Exception):
    pass


@dataclass(frozen=True)
class ValidatedUpload:
    name: str
    data: bytes
    media_type: str
    sha256: str


def positive_setting(name: str, maximum: int) -> int:
    value = getattr(settings, name)
    if type(value) is not int or not 1 <= value <= maximum:
        raise ImproperlyConfigured(f"{name} must be an integer between 1 and {maximum}.")
    return value


def validate_text(data: bytes) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError("Expected UTF-8 text.", code="invalid_file_content") from exc
    if any(ord(char) < 32 and char not in "\t\r\n" for char in text):
        raise ValidationError("Text contains unsupported control bytes.", code="invalid_file_content")
    return "text/plain"


def _reject_constant(value):
    raise ValueError("Non-finite numbers are not JSON.")


def validate_json(data: bytes) -> str:
    validate_text(data)
    try:
        json.loads(data.decode("utf-8"), parse_constant=_reject_constant)
    except (ValueError, RecursionError) as exc:
        raise ValidationError("Expected a valid JSON document.", code="invalid_file_content") from exc
    return "application/json"


def validate_upload(upload, *, purpose: str) -> ValidatedUpload:
    policies = settings.PRIVATE_FILE_POLICIES
    if not isinstance(purpose, str) or purpose not in policies:
        raise ValidationError("Unsupported upload purpose.", code="invalid_file_purpose")
    policy = policies[purpose]
    global_limit = positive_setting("PRIVATE_FILE_MAX_BYTES", 16 * 1024 * 1024)
    limit = policy.get("max_bytes", global_limit)
    if type(limit) is not int or not 1 <= limit <= global_limit:
        raise ImproperlyConfigured("File policy max_bytes must fit PRIVATE_FILE_MAX_BYTES.")
    name = unicodedata.normalize("NFKC", str(getattr(upload, "name", "")))
    if (
        not 1 <= len(name) <= 120 or not name[0].isalnum() or ".." in name
        or any(not (char.isalnum() or char in " ._-") for char in name)
        or name.endswith((" ", "."))
    ):
        raise ValidationError("Invalid filename.", code="invalid_filename")
    extension = PurePosixPath(name).suffix.lower()
    validator_path = policy.get("validators", {}).get(extension)
    if not validator_path:
        raise ValidationError("File type is not allowed for this purpose.", code="invalid_file_type")
    validator = import_string(validator_path)  # Server code/configuration, never request data.
    upload.seek(0)
    # Do not trust UploadedFile.size or MIME metadata. Bounded read protects
    # programmatic callers too, independently of the multipart upload handler.
    data = upload.read(limit + 1)
    if len(data) > limit:
        raise UploadTooLarge()
    if not data:
        raise ValidationError("Empty files are not allowed.", code="empty_file")
    media_type = validator(data)
    if not isinstance(media_type, str) or not re.fullmatch(r"[a-z0-9.+-]+/[a-z0-9.+-]+", media_type) or len(media_type) > 100:
        raise ImproperlyConfigured("File validator must return a valid media type.")
    return ValidatedUpload(name, data, media_type, sha256(data).hexdigest())


class PrivateUploadLimitHandler(FileUploadHandler):
    """Stop temporary-file growth while receiving the private upload routes."""

    def __init__(self, request=None):
        super().__init__(request)
        self.enabled = request is not None and request.path.startswith("/api/v1/files/")
        self.received = 0
        self.file_count = 0

    def new_file(self, *args, **kwargs):
        super().new_file(*args, **kwargs)
        if self.enabled:
            self.file_count += 1
            if self.file_count > 1:
                self.request.private_upload_rejected = True
                raise StopUpload(connection_reset=True)

    def receive_data_chunk(self, raw_data, start):
        if self.enabled:
            self.received += len(raw_data)
            if self.received > positive_setting("PRIVATE_FILE_MAX_BYTES", 16 * 1024 * 1024):
                self.request.private_upload_rejected = True
                raise StopUpload(connection_reset=True)
        return raw_data

    def file_complete(self, file_size):
        return None
