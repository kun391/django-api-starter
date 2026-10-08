"""DR backup freshness contract tests."""
import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dr_freshness.py"
spec = importlib.util.spec_from_file_location("dr_freshness", SCRIPT)
assert spec and spec.loader
dr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dr)


def test_freshness_accepts_recent_timestamp_and_rejects_stale(tmp_path):
    now = datetime(2026, 10, 9, tzinfo=UTC)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": 1,
        "created_at": (now - timedelta(hours=2)).isoformat(),
    }))
    assert dr.check_age(manifest, 4, now) == 2.0
    with pytest.raises(ValueError, match="exceeded"):
        dr.check_age(manifest, 1, now)


def test_freshness_rejects_missing_timezone_and_future(tmp_path):
    now = datetime(2026, 10, 9, tzinfo=UTC)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": 1, "created_at": "2026-10-09T00:00:00"
    }))
    with pytest.raises(ValueError, match="timezone"):
        dr.check_age(manifest, 24, now)
    manifest.write_text(json.dumps({
        "schema_version": 1, "created_at": "2026-10-09T00:30:00+00:00"
    }))
    with pytest.raises(ValueError, match="future"):
        dr.check_age(manifest, 24, now)


def test_freshness_rejects_symlink(tmp_path):
    manifest = tmp_path / "real.json"
    manifest.write_text('{"schema_version":1,"created_at":"2026-10-09T00:00:00Z"}')
    link = tmp_path / "link.json"
    link.symlink_to(manifest)
    with pytest.raises(ValueError, match="regular"):
        dr.check_age(link, 24)
