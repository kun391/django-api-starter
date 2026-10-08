#!/usr/bin/env python3
"""CI-only encrypted restic roundtrip on disposable, local storage."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OFFSITE = ROOT / "scripts" / "dr_offsite.py"


def call(*argv: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*argv], env=env, check=True, text=True, capture_output=True
    )


def make_bundle(root: Path) -> Path:
    bundle = root / "frozen"
    bundle.mkdir(mode=0o700)
    archive = bundle / "database.dump"
    archive.write_bytes(b"disposable test database archive")
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    (bundle / "database.json").write_text(json.dumps({
        "schema_version": 1,
        "archive_name": archive.name,
        "bytes": archive.stat().st_size,
        "sha256": checksum,
    }))
    key = "objects/ab/" + "ab" * 16
    content = b"fixture private file bytes, encrypted at restic destination"
    target = bundle / "private-files" / key
    target.parent.mkdir(parents=True)
    target.write_bytes(content)
    (bundle / "objects.json").write_text(json.dumps({
        "schema_version": 1,
        "objects": [{
            "key": key,
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }],
    }))
    (bundle / "recovery-point.json").write_text(json.dumps({
        "schema_version": 1,
        "recovery_id": "dr-local-ci-20261008",
        "database_manifest": "database.json",
        "object_manifest": "objects.json",
    }))
    return bundle


def main() -> int:
    if os.environ.get("DR_CI_ISOLATED") != "yes":
        raise RuntimeError("DR_CI_ISOLATED=yes is required")
    with tempfile.TemporaryDirectory(prefix="dr-restic-test-") as dirname:
        root = Path(dirname)
        bundle = make_bundle(root)
        secret = root / "password"
        secret.write_text("isolated-ci-test-passphrase-not-for-production")
        secret.chmod(0o600)
        repo = root / "restic-repository"
        env = {
            **os.environ,
            "RESTIC_REPOSITORY": str(repo),
            "RESTIC_PASSWORD_FILE": str(secret),
            "DR_TEST_ALLOW_LOCAL_RESTIC": "yes",
        }
        call("restic", "init", env=env)
        call(sys.executable, str(OFFSITE), "validate", "--bundle", str(bundle), env=env)
        call(
            sys.executable, str(OFFSITE), "backup", "--bundle", str(bundle),
            "--confirm", "FROZEN:dr-local-ci-20261008", env=env,
        )
        call(sys.executable, str(OFFSITE), "check", env=env)
        snapshots = json.loads(call("restic", "snapshots", "--json", env=env).stdout)
        assert len(snapshots) == 1, "Exactly one encrypted snapshot expected"
        snapshot_id = snapshots[0]["id"]
        assert len(snapshot_id) == 64
        restored = root / "restored"
        call(
            sys.executable, str(OFFSITE), "restore", "--snapshot", snapshot_id,
            "--target", str(restored), "--confirm", f"RESTORE:{snapshot_id}", env=env,
        )
        recovered = list(restored.rglob("recovery-point.json"))
        assert len(recovered) == 1, "Recovered manifest missing or ambiguous"
        call(
            sys.executable, str(OFFSITE), "validate", "--bundle",
            str(recovered[0].parent), env=env,
        )
        # Reusing a target must fail; it must not rewrite already-restored files.
        reused = subprocess.run([
            sys.executable, str(OFFSITE), "restore", "--snapshot", snapshot_id,
            "--target", str(restored), "--confirm", f"RESTORE:{snapshot_id}",
        ], env=env, capture_output=True, check=False)
        assert reused.returncode != 0
        # Reject tampered source bundles before they ever reach the repository.
        (bundle / "database.dump").write_bytes(b"corrupted backup")
        tampered = subprocess.run([
            sys.executable, str(OFFSITE), "backup", "--bundle", str(bundle),
            "--confirm", "FROZEN:dr-local-ci-20261008",
        ], env=env, capture_output=True, check=False)
        assert tampered.returncode != 0
        print("DR encrypted offsite backup -> check -> restore -> validate passed")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
