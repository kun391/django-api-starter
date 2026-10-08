#!/usr/bin/env python3
"""CI-only PostgreSQL + filesystem + encrypted restic recovery drill.

Uses only disposable local databases and a temporary local restic repository.
This is a cross-component contract drill, not a full application recovery test.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def call(*args: str, env: dict[str, str]) -> str:
    result = subprocess.run(args, env=env, text=True, capture_output=True, check=True)
    return result.stdout


def attempt(*args: str, env: dict[str, str]) -> int:
    return subprocess.run(args, env=env, capture_output=True, check=False).returncode


def required_isolation() -> None:
    if os.environ.get("DR_CI_ISOLATED") != "yes":
        raise RuntimeError("DR_CI_ISOLATED=yes is required")
    if os.environ.get("PGHOST") != "127.0.0.1":
        raise RuntimeError("Only isolated loopback PostgreSQL is allowed")
    if os.environ.get("PGPORT") != "5432" or os.environ.get("PGUSER") != "postgres":
        raise RuntimeError("Expected disposable CI PostgreSQL configuration")
    if os.environ.get("PGHOSTADDR") or os.environ.get("PGSERVICE"):
        raise RuntimeError("PGHOSTADDR and PGSERVICE overrides are forbidden")


def main() -> int:
    required_isolation()
    source = f"dr_source_{uuid4().hex[:12]}"
    target = f"dr_target_{uuid4().hex[:12]}"
    base_env = dict(os.environ)
    file_id = "ab" * 16
    file_bytes = b"real private file bytes paired with a PostgreSQL snapshot"
    checksum = hashlib.sha256(file_bytes).hexdigest()
    try:
        with tempfile.TemporaryDirectory(prefix="dr-complete-ci-") as location:
            temp = Path(location)
            bundle = temp / "frozen"
            bundle.mkdir(mode=0o700)
            stored = bundle / "private-files" / "objects" / "ab" / file_id
            stored.parent.mkdir(parents=True, mode=0o700)
            stored.write_bytes(file_bytes)
            object_manifest = bundle / "objects.json"
            call(
                sys.executable, str(SCRIPTS / "dr_private_files.py"), "inventory",
                "--root", str(bundle / "private-files"),
                "--output", str(object_manifest), env=base_env,
            )

            call("createdb", source, env=base_env)
            call(
                "psql", "-X", "-v", "ON_ERROR_STOP=1", "-d", source, "-c",
                "CREATE TABLE dr_private_file_probe ("
                "id text PRIMARY KEY, state text NOT NULL, "
                "size bigint NOT NULL, sha256 text NOT NULL)", env=base_env,
            )
            call(
                "psql", "-X", "-v", "ON_ERROR_STOP=1", "-d", source, "-c",
                "INSERT INTO dr_private_file_probe (id, state, size, sha256) "
                f"VALUES ('{file_id}', 'ready', {len(file_bytes)}, '{checksum}')",
                env=base_env,
            )

            db_dir = bundle / "database"
            call(
                sys.executable, str(SCRIPTS / "dr_postgres.py"), "backup",
                "--database", source, "--directory", str(db_dir), env=base_env,
            )
            manifests = list(db_dir.glob("*.json"))
            assert len(manifests) == 1, "Exactly one database manifest required"
            recovery_id = f"dr-integration-{uuid4().hex[:12]}"
            (bundle / "recovery-point.json").write_text(json.dumps({
                "schema_version": 1,
                "recovery_id": recovery_id,
                "database_manifest": str(manifests[0].relative_to(bundle)),
                "object_manifest": object_manifest.name,
            }))

            password = temp / "restic-key"
            password.write_text("ephemeral-ci-only-not-a-production-key")
            password.chmod(0o600)
            restic_env = {
                **base_env,
                "RESTIC_REPOSITORY": str(temp / "encrypted-repository"),
                "RESTIC_PASSWORD_FILE": str(password),
                "DR_TEST_ALLOW_LOCAL_RESTIC": "yes",
            }
            call("restic", "init", env=restic_env)
            call(
                sys.executable, str(SCRIPTS / "dr_offsite.py"), "validate",
                "--bundle", str(bundle), env=restic_env,
            )
            call(
                sys.executable, str(SCRIPTS / "dr_offsite.py"), "backup",
                "--bundle", str(bundle), "--confirm", f"FROZEN:{recovery_id}",
                env=restic_env,
            )
            call(sys.executable, str(SCRIPTS / "dr_offsite.py"), "check", env=restic_env)
            snapshots = json.loads(call("restic", "snapshots", "--json", env=restic_env))
            assert len(snapshots) == 1, "Exactly one encrypted snapshot required"
            snapshot = snapshots[0]["id"]
            assert len(snapshot) == 64

            restored = temp / "isolated-restore"
            call(
                sys.executable, str(SCRIPTS / "dr_offsite.py"), "restore",
                "--snapshot", snapshot, "--target", str(restored),
                "--confirm", f"RESTORE:{snapshot}", env=restic_env,
            )
            candidates = list(restored.rglob("recovery-point.json"))
            assert len(candidates) == 1, "Recovered bundle must be unique"
            recovered = candidates[0].parent
            call(
                sys.executable, str(SCRIPTS / "dr_offsite.py"), "validate",
                "--bundle", str(recovered), env=restic_env,
            )
            recovered_database_manifest = next((recovered / "database").glob("*.json"))
            call(
                sys.executable, str(SCRIPTS / "dr_postgres.py"), "restore",
                "--manifest", str(recovered_database_manifest), "--target", target,
                "--confirm", f"CREATE:{target}", env=base_env,
            )
            records = call(
                "psql", "-X", "-A", "-t", "-F", "|", "-v", "ON_ERROR_STOP=1",
                "-d", target, "-c",
                "SELECT id, state, size, sha256 FROM dr_private_file_probe",
                env=base_env,
            ).strip().splitlines()
            assert len(records) == 1, "Restored PostgreSQL row count mismatch"
            row = records[0].split("|")
            assert row == [file_id, "ready", str(len(file_bytes)), checksum]
            db_manifest = temp / "restored-file-rows.json"
            db_manifest.write_text(json.dumps({
                "schema_version": 1,
                "files": [{
                    "id": row[0], "state": row[1],
                    "size": int(row[2]), "sha256": row[3],
                }],
            }))
            recovered_files = recovered / "private-files"
            recovered_inventory = recovered / "objects.json"
            call(
                sys.executable, str(SCRIPTS / "dr_private_files.py"), "verify",
                "--root", str(recovered_files),
                "--manifest", str(recovered_inventory), env=base_env,
            )
            call(
                sys.executable, str(SCRIPTS / "dr_reconcile.py"),
                "--database-manifest", str(db_manifest),
                "--objects-manifest", str(recovered_inventory), env=base_env,
            )

            # Negative test: corruption must fail after extraction, not just in the
            # encrypted restic repository or PostgreSQL dump manifest.
            restored_object = recovered_files / "objects" / "ab" / file_id
            restored_object.write_bytes(b"corrupt extracted bytes")
            assert attempt(
                sys.executable, str(SCRIPTS / "dr_private_files.py"), "verify",
                "--root", str(recovered_files), "--manifest", str(recovered_inventory),
                env=base_env,
            ) != 0
            print("Complete isolated PostgreSQL/restic/private-files DR drill passed")
            return 0
    finally:
        # These two exact randomized names are owned by this CI run only.
        for name in (target, source):
            subprocess.run(
                ["dropdb", "--if-exists", name],
                env=base_env, capture_output=True, check=False,
            )


if __name__ == "__main__":
    raise SystemExit(main())
