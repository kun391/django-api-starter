"""Recovery consistency tests requiring no external storage or DB."""
import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest


def load(name):
    path = Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reconciler_detects_missing_corrupted_and_orphaned_objects():
    dr = load("dr_reconcile")
    file_id = str(uuid4())
    key = dr._key(file_id)
    records = [{"id": file_id, "state": "ready", "size": 3, "sha256": "a" * 64}]
    objects = [{"key": key, "bytes": 3, "sha256": "a" * 64}]
    assert dr.compare(records, objects)["ok"]
    missing = dr.compare(records, [])
    assert missing["missing"] == [key]
    assert not missing["ok"]
    mismatch = dr.compare(records, [{"key": key, "bytes": 3, "sha256": "b" * 64}])
    assert mismatch["mismatched"] == [key]
    assert not mismatch["ok"]
    orphan = dr.compare([], objects)
    assert orphan["unreferenced"] == [key]
    assert orphan["ok"]  # deleted/pending objects may linger until reconciliation


def test_reconciler_rejects_bad_records_and_duplicate_keys():
    dr = load("dr_reconcile")
    uid = str(uuid4())
    row = {"id": uid, "state": "ready", "size": 3, "sha256": "a" * 64}
    with pytest.raises(ValueError, match="Duplicate"):
        dr.compare([row, row], [])
    with pytest.raises(ValueError, match="checksum"):
        dr.compare([{**row, "sha256": "invalid"}], [])


def test_s3_version_pinning_and_verification():
    dr = load("dr_s3")
    key = "objects/aa/" + "a" * 32

    class Body:
        def __init__(self):
            self.done = False

        def read(self, size):
            if self.done:
                return b""
            self.done = True
            return b"sample"

        def close(self):
            pass

    class Client:
        def list_objects_v2(self, **kwargs):
            return {"Contents": [{"Key": key, "Size": 6}], "IsTruncated": False}

        def head_object(self, **kwargs):
            return {"VersionId": "v1"}

        def get_object(self, **kwargs):
            assert kwargs["VersionId"] == "v1"
            return {"Body": Body()}

    client = Client()
    result = dr.inventory(client, "disposable")
    assert result["objects"][0]["version_id"] == "v1"
    dr.verify(client, "disposable", result)


def test_s3_rejects_unversioned_objects():
    dr = load("dr_s3")
    key = "objects/aa/" + "a" * 32

    class Client:
        def list_objects_v2(self, **kwargs):
            return {"Contents": [{"Key": key, "Size": 1}], "IsTruncated": False}

        def head_object(self, **kwargs):
            return {}

    with pytest.raises(ValueError, match="Versioned"):
        dr.inventory(Client(), "disposable")
