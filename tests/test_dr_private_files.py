"""Filesystem DR inventory safety and integrity regression tests."""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dr_private_files.py"
spec = importlib.util.spec_from_file_location("dr_private_files", SCRIPT)
assert spec and spec.loader
dr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dr)


def create_file(root, uid="a" * 32, payload=b"hello"):
    path = root / "objects" / uid[:2] / uid
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def test_snapshot_roundtrip_and_tamper_detection(tmp_path):
    root = tmp_path / "snapshot"
    root.mkdir()
    item = create_file(root)
    expected = dr.inventory(root)
    assert expected["objects"][0]["sha256"] == dr.digest(item)
    manifest = tmp_path / "manifest.json"
    import json
    manifest.write_text(json.dumps(expected))
    dr.verify(root, manifest)
    item.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="mismatch"):
        dr.verify(root, manifest)


def test_snapshot_fails_on_missing_and_extra_objects(tmp_path):
    root = tmp_path / "snapshot"
    root.mkdir()
    item = create_file(root)
    import json
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(dr.inventory(root)))
    item.unlink()
    with pytest.raises(ValueError, match="mismatch"):
        dr.verify(root, manifest)
    create_file(root, uid="b" * 32)
    with pytest.raises(ValueError, match="mismatch"):
        dr.verify(root, manifest)


def test_snapshot_refuses_symlinks(tmp_path):
    root = tmp_path / "snapshot"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("secret")
    target = root / "objects" / "aa"
    target.mkdir(parents=True)
    (target / ("a" * 32)).symlink_to(outside)
    with pytest.raises(ValueError, match="symbolic link"):
        dr.inventory(root)


def test_snapshot_rejects_unexpected_objects(tmp_path):
    root = tmp_path / "snapshot"
    root.mkdir()
    (root / "unexpected.txt").write_text("not an object")
    with pytest.raises(ValueError, match="Unexpected"):
        dr.inventory(root)
