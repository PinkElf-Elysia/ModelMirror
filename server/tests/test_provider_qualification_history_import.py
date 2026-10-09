"""Positive inheritance, atomic mapping and shared gate tests: synthetic only."""
import hashlib
import json
import sqlite3
import os
import subprocess
import sys
from pathlib import Path
from contextlib import contextmanager

import pytest

from server.model_router.repository import SQLiteRouterRepository, RouterRepositoryError
from server.model_router.qualification_history_import import audit_import, apply_import
from server.model_router.qualification_history_proof import HistoricalProofError
from server.tests.test_provider_qualification_history_snapshot import seed


@contextmanager
def database(path):
    db = sqlite3.connect(path)
    try:
        with db:
            yield db
    finally:
        db.close()


def inputs(tmp_path, **seed_options):
    directory, snapshot, proof = seed(tmp_path, **seed_options)
    artifact = tmp_path / "reviewed-synthetic-deployment.txt"
    artifact.write_text("SYNTHETIC test witness. TTL=2592000; complete unchanged route history.")
    artifact_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
    document = {
        "contract_version": "modelmirror-qualification-history-proof-v2",
        "source_snapshot_sha256": proof.source_snapshot_sha256,
        "source_epoch_id": proof.source_epoch_id, "target_epoch_id": proof.target_epoch_id,
        "observed_until": proof.observed_until.isoformat(),
        "lifetimes": [{"certification_id": item.certification_id, "ttl_seconds": item.ttl_seconds,
            "deployment_artifact_sha256": artifact_hash, "effective_from": item.effective_from.isoformat(),
            "effective_until": item.effective_until.isoformat()} for item in proof.lifetimes],
        "configuration_continuity": {
            "configuration_fingerprint": proof.continuity.configuration_fingerprint,
            "artifact_sha256": artifact_hash, "effective_from": proof.continuity.effective_from.isoformat(),
            "effective_until": proof.continuity.effective_until.isoformat(), "complete_history_reviewed": True},
    }
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(document))
    return directory, dict(manifest_path=manifest,
        reviewed_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
        source_snapshot=snapshot, deployment_artifacts={artifact_hash: artifact}, acknowledge_provenance_review=True)


def approve(directory, args):
    with SQLiteRouterRepository.open_maintenance(directory) as repo:
        proposal = audit_import(repo, "local", **args)
    with SQLiteRouterRepository.open_maintenance(directory, readonly=False) as repo:
        return apply_import(repo, "local", expected_plan_fingerprint=proposal["plan_fingerprint"], **args)


def summary(directory, tenant="local"):
    with SQLiteRouterRepository.open_maintenance(directory) as repo, repo._connect() as db:
        return repo._summarize_chat_control_gate(db, tenant_id=tenant, epoch_id="new")


def test_positive_apply_keeps_denominator_deduplicates_and_preserves_old_rows(tmp_path):
    directory, args = inputs(tmp_path)
    with database(directory / "router.sqlite3") as db:
        tables = ("provider_chat_runs", "provider_chat_attempts", "provider_chat_certifications",
                  "provider_chat_gate_epochs", "provider_chat_gate_approvals", "provider_chat_stable_policies")
        before = {name: db.execute(f"SELECT * FROM {name}").fetchall() for name in tables}
    assert summary(directory)["request_count"] == 0
    result = approve(directory, args)
    assert result["applied"] and not result["already_recorded"]
    assert approve(directory, args)["already_recorded"]
    assert len(list(directory.glob("router.sqlite3.qualification-import-backup-*"))) == 1
    actual = summary(directory)
    assert actual == {"request_count": 2, "success_count": 1, "hard_failure_count": 0,
                      "observed_days": 14.0, "model_successes": {"model": 1}}
    assert summary(directory, "other")["request_count"] == 0
    with database(directory / "router.sqlite3") as db:
        assert {name: db.execute(f"SELECT * FROM {name}").fetchall() for name in tables} == before
        assert db.execute("SELECT COUNT(*) FROM provider_qualification_inherited_samples").fetchone()[0] == 2


def test_dry_run_has_no_file_or_database_side_effects(tmp_path):
    directory, args = inputs(tmp_path)
    before = {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}
    with SQLiteRouterRepository.open_maintenance(directory) as repo:
        report = audit_import(repo, "local", **args)
    assert report["inherited_sample_count"] == 2
    assert before == {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}


def test_changed_plan_rejected_before_backup(tmp_path):
    directory, args = inputs(tmp_path)
    with SQLiteRouterRepository.open_maintenance(directory) as repo:
        proposal = audit_import(repo, "local", **args)
    with database(directory / "router.sqlite3") as db:
        db.execute("UPDATE provider_chat_stable_policies SET revision=revision+1")
    with SQLiteRouterRepository.open_maintenance(directory, readonly=False) as repo:
        with pytest.raises(HistoricalProofError, match="plan_changed"):
            apply_import(repo, "local", expected_plan_fingerprint=proposal["plan_fingerprint"], **args)
    assert not list(directory.glob("router.sqlite3.qualification-import-backup-*"))


@pytest.mark.parametrize("sql,reason", [
    ("DELETE FROM provider_qualification_inherited_samples WHERE source_run_id='run-1'", "recorded_mapping_incomplete"),
    ("DELETE FROM provider_qualification_inherited_samples", "recorded_mapping_incomplete"),
    ("DELETE FROM provider_chat_runs WHERE id='run-1'", "source_samples_changed"),
    ("UPDATE provider_chat_runs SET status='succeeded' WHERE id='run-1'", "source_samples_changed"),
    ("UPDATE provider_chat_attempts SET dispatched=0 WHERE id='attempt-1'", "source_samples_changed"),
    ("UPDATE router_connections SET api_key_ciphertext='changed'", "target_qualification_invalid"),
    ("UPDATE provider_chat_stable_policies SET auto_enabled=1", "target_policy_changed"),
    ("UPDATE provider_qualification_intervals SET closed_at='closed'", "target_qualification_invalid"),
    ("UPDATE provider_chat_certifications SET status='failed' WHERE id='new-cert'", "target_qualification_invalid"),
    ("UPDATE provider_chat_certifications SET actual_model='changed' WHERE id='new-cert'", "target_qualification_invalid"),
])
def test_gate_never_drops_only_the_failed_inherited_sample(tmp_path, sql, reason):
    directory, args = inputs(tmp_path)
    approve(directory, args)
    with database(directory / "router.sqlite3") as db:
        db.execute(sql)
    with pytest.raises(RouterRepositoryError, match=reason):
        summary(directory)


def test_required_activation_mode_change_preserves_valid_mapping(tmp_path):
    directory, args = inputs(tmp_path)
    approve(directory, args)
    with database(directory / "router.sqlite3") as db:
        db.execute("UPDATE provider_chat_stable_policies SET mode='newapi_required_default',revision=3")
    assert summary(directory)["request_count"] == 2


def test_failure_mid_mapping_insert_rolls_back_whole_apply(tmp_path):
    directory, args = inputs(tmp_path)
    with SQLiteRouterRepository.open_maintenance(directory) as repo:
        report = audit_import(repo, "local", **args)
    with database(directory / "router.sqlite3") as db:
        db.execute("""CREATE TRIGGER synthetic_abort BEFORE INSERT ON provider_qualification_inherited_samples
            WHEN NEW.source_run_id='run-1' BEGIN SELECT RAISE(ABORT,'synthetic'); END""")
    with SQLiteRouterRepository.open_maintenance(directory, readonly=False) as repo:
        with pytest.raises(sqlite3.IntegrityError, match="synthetic"):
            apply_import(repo, "local", expected_plan_fingerprint=report["plan_fingerprint"], **args)
    with database(directory / "router.sqlite3") as db:
        for name in ("provider_qualification_inherited_samples", "provider_qualification_epoch_mappings"):
            assert db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] == 0
    assert len(list(directory.glob("router.sqlite3.qualification-import-backup-*"))) == 1


def command(directory, args, *extra):
    argv = [sys.executable, "-B", "-m", "server.model_router.qualification_history",
        "--storage-dir", str(directory), "--epoch-id", "old", "--proof-manifest", str(args["manifest_path"]),
        "--reviewed-manifest-sha256", args["reviewed_manifest_sha256"],
        "--source-snapshot", str(args["source_snapshot"]), "--acknowledge-provenance-review"]
    for key, path in args["deployment_artifacts"].items():
        argv.extend(["--deployment-artifact", f"{key}={path}"])
    env = {k: v for k, v in os.environ.items() if k.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}}
    return subprocess.run([*argv, *extra], cwd=Path(__file__).resolve().parents[2], env=env,
                          capture_output=True, text=True, timeout=30)


def test_cli_review_apply_and_missing_authority(tmp_path):
    directory, args = inputs(tmp_path)
    before = {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}
    dry = command(directory, args)
    assert dry.returncode == 0, dry.stderr + dry.stdout
    report = json.loads(dry.stdout)
    assert report["status"] == "accepted" and report["applied"] is False
    assert before == {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}
    assert command(directory, args, "--apply").returncode != 0
    applied = command(directory, args, "--apply", "--expected-plan-fingerprint", report["plan_fingerprint"])
    assert applied.returncode == 0, applied.stderr + applied.stdout
    assert json.loads(applied.stdout)["applied"]
    for forbidden in ("synthetic-ciphertext", "synthetic.invalid", "SYNTHETIC test witness", '"tenant_id"'):
        assert forbidden not in dry.stdout + applied.stdout


def test_original_hard_failure_never_becomes_an_accepted_migration_invalidation(tmp_path):
    directory, args = inputs(tmp_path)
    with database(directory / "router.sqlite3") as db:
        db.execute("UPDATE provider_chat_gate_epochs SET status='degraded',hard_failure_code='provider_chat_http_401' WHERE id='old'")
    with pytest.raises(HistoricalProofError, match="epoch_not_eligible"):
        approve(directory, args)


def test_import_does_not_satisfy_or_lower_required_gate(tmp_path):
    from server.model_router.chat_gate import evaluate_provider_chat_gate
    directory, args = inputs(tmp_path)
    approve(directory, args)
    gate = evaluate_provider_chat_gate(summary(directory), stable_model_ids=["model"])
    assert not gate.ready


@pytest.mark.parametrize("count,failures,ready", [(499, 0, False), (500, 5, True), (500, 6, False)])
def test_full_inheritance_respects_500_fourteen_days_and_99_percent(tmp_path, count, failures, ready):
    from server.model_router.chat_gate import evaluate_provider_chat_gate
    directory, args = inputs(tmp_path, sample_count=count, failure_count=failures)
    approve(directory, args)
    actual = summary(directory)
    assert actual["request_count"] == count and actual["success_count"] == count - failures
    assert actual["observed_days"] == 14.0
    assert evaluate_provider_chat_gate(actual, stable_model_ids=["model"]).ready is ready
    with database(directory / "router.sqlite3") as db:
        # Evidence import does not approve or activate required, even if the
        # numerical gate is satisfied by 500 independently verified samples.
        assert db.execute("SELECT mode FROM provider_chat_stable_policies").fetchone()[0] == "newapi_preferred"
        assert db.execute("SELECT COUNT(*) FROM provider_chat_gate_approvals").fetchone()[0] == 0


def test_cleanup_keeps_the_complete_source_ledger_and_mapping(tmp_path):
    directory, args = inputs(tmp_path)
    approve(directory, args)
    with SQLiteRouterRepository.open_maintenance(directory, readonly=False) as repo:
        dry = repo.cleanup_chat_control_receipts("local", before="2099-01-01", apply=False)
        applied = repo.cleanup_chat_control_receipts("local", before="2099-01-01", apply=True)
    assert dry["runs"] == applied["runs"] == 0
    assert dry["attempts"] == applied["attempts"] == 0
    assert summary(directory)["request_count"] == 2


def test_same_samples_cannot_be_reassigned_to_another_epoch(tmp_path):
    directory, args = inputs(tmp_path)
    approve(directory, args)
    with database(directory / "router.sqlite3") as db:
        db.execute("UPDATE provider_chat_gate_epochs SET status='closed',closed_at=started_at WHERE id='new'")
        db.execute("""INSERT INTO provider_chat_gate_epochs
            SELECT 'next',tenant_id,policy_fingerprint,'collecting',NULL,started_at,NULL
            FROM provider_chat_gate_epochs WHERE id='new'""")
    document = json.loads(args["manifest_path"].read_text())
    document["target_epoch_id"] = "next"
    args["manifest_path"].write_text(json.dumps(document))
    args["reviewed_manifest_sha256"] = hashlib.sha256(args["manifest_path"].read_bytes()).hexdigest()
    with pytest.raises(HistoricalProofError, match="sample_already_inherited"):
        approve(directory, args)
