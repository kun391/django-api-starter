"""Private backend configuration; S3 dependencies are loaded only when selected."""

from importlib import import_module
from pathlib import Path
from urllib.parse import urlsplit

from decouple import config  # type: ignore[import-untyped]
from django.core.exceptions import ImproperlyConfigured


def build_private_storage(base_dir, media_root, static_root) -> dict:
    backend = config("PRIVATE_FILE_BACKEND", default="filesystem")
    if backend == "filesystem":
        root = Path(config("PRIVATE_FILE_ROOT", default=str(base_dir / "private-files"))).resolve()
        for public in (Path(media_root).resolve(), Path(static_root).resolve(), (base_dir / "static").resolve()):
            if root.is_relative_to(public) or public.is_relative_to(root):
                raise ImproperlyConfigured("Private file root must not overlap a public media/static root.")
        return {"BACKEND": "apps.files.backends.PrivateFileSystemStorage", "OPTIONS": {"location": str(root)}}
    if backend != "s3":
        raise ImproperlyConfigured("PRIVATE_FILE_BACKEND must be filesystem or s3.")
    bucket = config("PRIVATE_FILE_S3_BUCKET", default="")
    if not bucket:
        raise ImproperlyConfigured("PRIVATE_FILE_S3_BUCKET is required for S3.")
    endpoint = config("PRIVATE_FILE_S3_ENDPOINT", default="")
    if endpoint:
        parts = urlsplit(endpoint)
        allow_http = config("PRIVATE_FILE_S3_ALLOW_HTTP", default=False, cast=bool)
        if (
            parts.scheme not in ({"https", "http"} if allow_http else {"https"})
            or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment
        ):
            raise ImproperlyConfigured("Use an HTTPS S3 endpoint without credentials/query parameters.")
    try:
        client_config = import_module("botocore.config").Config(
            signature_version="s3v4", connect_timeout=2, read_timeout=10,
            retries={"mode": "standard", "total_max_attempts": 2},
            s3={"addressing_style": "path"},
        )
        import_module("storages.backends.s3")
    except ImportError as exc:
        raise ImproperlyConfigured("Install the optional storage extra for S3.") from exc
    return {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": bucket, "endpoint_url": endpoint or None,
            "region_name": config("PRIVATE_FILE_S3_REGION", default="us-east-1"),
            "default_acl": None, "querystring_auth": True, "custom_domain": None,
            "file_overwrite": True, "location": "", "gzip": False,
            "client_config": client_config, "max_memory_size": 1024 * 1024,
            "object_parameters": {
                "CacheControl": "private, no-store", "ContentType": "application/octet-stream",
                "ContentDisposition": "attachment",
            },
        },
    }
