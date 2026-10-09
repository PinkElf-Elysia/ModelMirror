"""Historical audit uses synthetic metadata, never external TTL assertions."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from server.model_router.qualification_history import audit_history, record_rejection
from server.model_router.repository import RouterRepositoryError, SQLiteRouterRepository


def _snapshot(directory):
    return {str(p.relative_to(directory)): (
                ("lease", p.stat().st_size, p.stat().st_ino)
                if p.name == ".provider-writer.lock" else hashlib.sha256(p.read_bytes()).hexdigest())
            for p in directory.rglob("*") if p.is_file()}


def _seed(directory):
    with SQLiteRouterRepository.open(directory, master_key=b"x" * 32) as repository:
        with repository._connect() as db:
            for tenant in ("local", "other"):
                db.execute("""INSERT INTO provider_chat_gate_epochs
                    (tenant_id,id,policy_fingerprint,status,started_at)
                    VALUES (?, 'epoch', 'old-policy', 'collecting', '2026-08-01T00:00:00+00:00')""", (tenant,))
                db.execute("""INSERT INTO provider_chat_runs
                    (tenant_id,id,epoch_id,policy_fingerprint,capability,requested_model,
                     strategy,status,created_at,updated_at)
                    VALUES (?, 'run','epoch','old-policy','chat_text','model',
                            'newapi_preferred','running','2026-08-01','2026-08-01')""", (tenant,))
                db.execute("""INSERT INTO provider_chat_attempts
                    (tenant_id,id,run_id,capability,position,connection_id,provider_kind,
                     dispatched,status,created_at,updated_at)
                    VALUES (?, 'attempt','run','chat_text',0,'conn','newapi',1,
                            'running','2026-08-01','2026-08-01')""", (tenant,))
                db.execute("""INSERT INTO provider_chat_certifications
                    (tenant_id,id,connection_id,connection_fingerprint,contract_version,
                     requested_model,idempotency_key_hash,status,created_at,updated_at,completed_at)
                    VALUES (?, 'old-cert','conn','fp','v1','model','hash','passed',
                            '2026-08-01','2026-08-01','2026-08-01')""", (tenant,))


def _command(directory, *extra):
    env = {key: value for key, value in os.environ.items()
           if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}}
    return subprocess.run([sys.executable, "-B", "-m", "server.model_router.qualification_history",
        "--storage-dir", str(directory), "--epoch-id", "epoch", *extra],
        cwd=Path(__file__).resolve().parents[2], env=env,
        capture_output=True, text=True, timeout=30)


def test_cli_dry_run_is_byte_for_byte_readonly_and_does_not_recover(tmp_path):
    _seed(tmp_path)
    before = _snapshot(tmp_path)
    result = _command(tmp_path)
    assert result.returncode == 0, result.stderr
    assert _snapshot(tmp_path) == before
    report = json.loads(result.stdout)
    assert report["status"] == "rejected"
    assert report["inherited_sample_count"] == 0
    assert report["reason_codes"] == ["provider_qualification_epoch_snapshot_missing",
        "provider_qualification_historical_expiry_unknown", "provider_qualification_historical_result_unresolved"]
    assert '"tenant_id"' not in result.stdout and '"connection_id"' not in result.stdout
    with sqlite3.connect(tmp_path / "router.sqlite3") as db:
        assert db.execute("SELECT status FROM provider_chat_runs WHERE tenant_id='local'").fetchone()[0] == "running"


def test_apply_only_appends_rejection_and_backup_and_is_idempotent(tmp_path):
    _seed(tmp_path)
    with SQLiteRouterRepository.open_maintenance(tmp_path) as repository:
        plan = audit_history(repository, "local", "epoch")
    with SQLiteRouterRepository.open_maintenance(tmp_path, readonly=False) as repository:
        result = record_rejection(repository, "local", "epoch", plan["plan_fingerprint"])
        assert result["applied"] and not result["already_recorded"]
        repeated = record_rejection(repository, "local", "epoch", plan["plan_fingerprint"])
        assert repeated["already_recorded"]
    backups = list(tmp_path.glob("router.sqlite3.qualification-audit-backup-*"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as backup, sqlite3.connect(tmp_path / "router.sqlite3") as db:
        assert backup.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert db.execute("SELECT tenant_id,status,target_epoch_id FROM provider_qualification_epoch_mappings").fetchall() == [("local", "rejected", None)]
        for table in ("provider_chat_runs", "provider_chat_attempts", "provider_chat_certifications", "provider_chat_gate_epochs", "provider_chat_gate_approvals"):
            assert db.execute(f"SELECT * FROM {table}").fetchall() == backup.execute(f"SELECT * FROM {table}").fetchall()


def test_apply_changed_evidence_rejects_reviewed_plan_before_backup(tmp_path):
    _seed(tmp_path)
    with SQLiteRouterRepository.open_maintenance(tmp_path) as repository:
        plan = audit_history(repository, "local", "epoch")
    with sqlite3.connect(tmp_path / "router.sqlite3") as db:
        db.execute("UPDATE provider_chat_runs SET hard_failure=1 WHERE tenant_id='local'")
    with SQLiteRouterRepository.open_maintenance(tmp_path, readonly=False) as repository:
        with pytest.raises(RouterRepositoryError, match="provider_qualification_history_plan_changed"):
            record_rejection(repository, "local", "epoch", plan["plan_fingerprint"])
    assert not list(tmp_path.glob("router.sqlite3.qualification-audit-backup-*"))


def test_history_command_cannot_bypass_live_owner_or_create_missing_storage(tmp_path):
    directory = tmp_path / "missing"
    assert _command(directory).returncode == 1
    assert not directory.exists()
    with SQLiteRouterRepository.open(tmp_path / "active", master_key=b"x" * 32) as repository:
        before = _snapshot(repository.storage_dir)
        assert _command(repository.storage_dir).returncode == 1
        assert _snapshot(repository.storage_dir) == before
        with pytest.raises(RouterRepositoryError, match="provider_qualification_history_offline_required"):
            record_rejection(repository, "local", "epoch", "not-approved")


def test_missing_or_other_tenant_epoch_is_not_disclosed(tmp_path):
    _seed(tmp_path)
    with SQLiteRouterRepository.open_maintenance(tmp_path) as repository:
        with pytest.raises(RouterRepositoryError, match="provider_qualification_epoch_not_found"):
            audit_history(repository, "absent", "epoch")
        local = audit_history(repository, "local", "epoch")
        other = audit_history(repository, "other", "epoch")
        assert local["plan_fingerprint"] != other["plan_fingerprint"]


def test_apply_cli_requires_reviewed_plan(tmp_path):
    _seed(tmp_path)
    before = _snapshot(tmp_path)
    result = _command(tmp_path, "--apply")
    assert result.returncode != 0
    assert _snapshot(tmp_path) == before
