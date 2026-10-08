"""Offline checks for disaster recovery bundle integrity."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / "scripts" / "dr_offsite.py"
spec = importlib.util.spec_from_file_location("dr_offsite", PATH)
assert spec and spec.loader
dr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dr)


def test_bundle_integrity_and_tampering(tmp_path):
    root = tmp_path / "bundle"
    root.mkdir()
    archive = root / "snapshot.dump"
    archive.write_bytes(b"example")
    (root / "postgres.json").write_text(json.dumps({
        "schema_version": 1, "archive_name": "snapshot.dump",
        "sha256": hashlib.sha256(b"example").hexdigest(), "bytes": 7,
    }))
    key = "objects/aa/" + "a" * 32
    obj = root / "private-files" / key
    obj.parent.mkdir(parents=True)
    obj.write_bytes(b"private")
    (root / "objects.json").write_text(json.dumps({
        "schema_version": 1,
        "objects": [{"key": key, "bytes": 7, "sha256": hashlib.sha256(b"private").hexdigest()}],
    }))
    (root / "recovery-point.json").write_text(json.dumps({
        "schema_version": 1,
        "recovery_id": "dr-example-20261008",
        "database_manifest": "postgres.json",
        "object_manifest": "objects.json",
    }))
    assert dr._manifest(root)["recovery_id"] == "dr-example-20261008"
    obj.write_bytes(b"modified")
    with pytest.raises(ValueError, match="checksum"):
        dr._manifest(root)


def test_restic_location_must_be_offsite(monkeypatch):
    monkeypatch.setenv("RESTIC_REPOSITORY", "/tmp/local-repository")
    monkeypatch.delenv("DR_TEST_ALLOW_LOCAL_RESTIC", raising=False)
    with pytest.raises(ValueError, match="Offsite"):
        dr._restic_environment()
