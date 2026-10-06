from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured

from settings import file_storage


def configuration(monkeypatch, tmp_path, values):
    monkeypatch.setattr(file_storage, "config", lambda name, default=None, **kwargs: values.get(name, default))
    return file_storage.build_private_storage(tmp_path, tmp_path / "media", tmp_path / "staticfiles")


def test_private_filesystem_is_default(monkeypatch, tmp_path):
    backend = configuration(monkeypatch, tmp_path, {})
    assert backend["BACKEND"] == "apps.files.backends.PrivateFileSystemStorage"
    assert Path(backend["OPTIONS"]["location"]) == tmp_path / "private-files"


@pytest.mark.parametrize("root", ["media", "media/uploads", "static", "staticfiles", "."])
def test_private_root_cannot_overlap_public_roots(monkeypatch, tmp_path, root):
    with pytest.raises(ImproperlyConfigured):
        configuration(monkeypatch, tmp_path, {"PRIVATE_FILE_ROOT": str(tmp_path / root)})


@pytest.mark.parametrize("values", [
    {"PRIVATE_FILE_BACKEND": "typo"},
    {"PRIVATE_FILE_BACKEND": "s3"},
    {"PRIVATE_FILE_BACKEND": "s3", "PRIVATE_FILE_S3_BUCKET": "private", "PRIVATE_FILE_S3_ENDPOINT": "http://cache.example"},
    {"PRIVATE_FILE_BACKEND": "s3", "PRIVATE_FILE_S3_BUCKET": "private", "PRIVATE_FILE_S3_ENDPOINT": "https://user:password@cache.example"},
])
def test_invalid_configuration_is_rejected(monkeypatch, tmp_path, values):
    with pytest.raises(ImproperlyConfigured):
        configuration(monkeypatch, tmp_path, values)
