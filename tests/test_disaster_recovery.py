"""Phase 24 DR CLI fail-closed behavior (no external PostgreSQL required)."""
import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dr_postgres.py"
spec = importlib.util.spec_from_file_location("dr_postgres", SCRIPT)
assert spec and spec.loader
dr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dr)


def test_restore_requires_isolated_name(tmp_path):
    manifest = tmp_path / "missing.json"
    with pytest.raises(ValueError, match="isolated"):
        dr.restore(manifest, "production", "CREATE:production")


def test_restore_requires_exact_confirmation(tmp_path):
    manifest = tmp_path / "missing.json"
    with pytest.raises(ValueError, match="confirm"):
        dr.restore(manifest, "dr_test", "yes")


def test_verify_detects_modified_archive(tmp_path):
    archive = tmp_path / "db.dump"
    archive.write_bytes(b"valid")
    archive.chmod(0o600)
    manifest = tmp_path / "db.json"
    manifest.write_text(
        '{"schema_version":1,"archive_name":"db.dump","bytes":5,"sha256":"'
        + dr.sha256(archive) + '"}'
    )
    manifest.chmod(0o600)
    with patch.object(dr, "run") as run:
        assert dr.verify(manifest) == archive
        run.assert_called_once()
    archive.write_bytes(b"evil!")
    with pytest.raises(ValueError, match="checksum"):
        dr.verify(manifest)


def test_restore_never_overwrites_existing_database(tmp_path):
    archive = tmp_path / "db.dump"
    archive.write_bytes(b"fake")
    archive.chmod(0o600)
    manifest = tmp_path / "db.json"
    manifest.write_text(
        '{"schema_version":1,"archive_name":"db.dump","bytes":4,"sha256":"'
        + dr.sha256(archive) + '"}'
    )
    manifest.chmod(0o600)
    with patch.object(dr, "run", side_effect=[None, dr.subprocess.CalledProcessError(1, "createdb")]) as run:
        with pytest.raises(dr.subprocess.CalledProcessError):
            dr.restore(manifest, "dr_check", "CREATE:dr_check")
    assert run.call_count == 2  # pg_restore --list; createdb; NOT pg_restore --dbname
