"""Synthetic Backup API snapshots only; no live deployment evidence."""
from dataclasses import replace
from contextlib import closing
from datetime import UTC, datetime, timedelta
import hashlib
import json
import sqlite3

import pytest

from server.model_router.repository import SQLiteRouterRepository
from server.model_router.qualification_events import begin_qualification_event, complete_qualification_event
from server.model_router.qualification_history_evidence import (
    OriginalLifetime, ReviewedConfigurationContinuity, ReviewedHistoryEvidence,
)
from server.model_router.qualification_history_proof import HistoricalProofError
from server.model_router.qualification_history_snapshot import (
    digest, policy_material, configuration_digest, open_snapshot, verify_snapshot,
)

NOW = datetime.now(UTC).replace(microsecond=0)
START = NOW - timedelta(days=16)


def certify(db, cert_id, fingerprint, at, *, saved):
    db.execute("""INSERT INTO provider_chat_certifications
        (tenant_id,id,connection_id,connection_fingerprint,contract_version,capability,
         requested_model,actual_model,idempotency_key_hash,status,created_at,updated_at)
        VALUES ('local',?,'conn',?,'modelmirror-provider-chat-v1','chat_text','model','model',?,'running',?,?)""",
        (cert_id, fingerprint, cert_id, at.isoformat(), at.isoformat()))
    if saved:
        begin_qualification_event(db, tenant_id="local", source="provider_chat", certification_id=cert_id, configured_ttl="2592000")
    db.execute("UPDATE provider_chat_certifications SET status='passed',completed_at=? WHERE id=?", (at.isoformat(), cert_id))
    if saved:
        return complete_qualification_event(db, tenant_id="local", source="provider_chat", certification_id=cert_id, continuity_blocked=False)


def seed(tmp_path, *, sample_count=2, failure_count=1):
    directory = tmp_path / "current"
    with SQLiteRouterRepository.open(directory, master_key=b"s" * 32) as repo:
        # Explicit synthetic connection. No credential resolution or network.
        with repo._connect() as db:
            db.execute("""INSERT INTO router_connections
                (tenant_id,id,name,kind,base_url,api_key_ciphertext,masked_key,scopes_json,enabled,created_at,updated_at)
                VALUES ('local','conn','synthetic','newapi','https://synthetic.invalid/v1','synthetic-ciphertext','***','["chat"]',1,?,?)""",
                (START.isoformat(), START.isoformat()))
        fp = repo.connection_config_fingerprint("local", "conn")
        with repo._connect() as db:
            certify(db, "old-cert", fp, START, saved=False)
            db.execute("""INSERT INTO provider_chat_stable_policies
                (tenant_id,mode,revision,policy_fingerprint,stable_models_json,created_at,updated_at)
                VALUES ('local','newapi_preferred',1,'pending','["model"]',?,?)""", (START.isoformat(), START.isoformat()))
            db.execute("""INSERT INTO provider_chat_capability_routes
                VALUES ('local','chat_text',0,'conn',?,?)""", (START.isoformat(), START.isoformat()))
            db.execute("""INSERT INTO provider_chat_model_qualifications
                VALUES ('local','chat_text','conn','model','old-cert',?,'modelmirror-provider-chat-v1',?)""", (fp, START.isoformat()))
            _, material = policy_material(db, "local")
            policy_fp = digest(material)
            db.execute("UPDATE provider_chat_stable_policies SET policy_fingerprint=?", (policy_fp,))
            db.execute("""INSERT INTO provider_chat_gate_epochs
                (tenant_id,id,policy_fingerprint,status,started_at) VALUES ('local','old',?,'collecting',?)""", (policy_fp, START.isoformat()))
            for i in range(sample_count):
                status = "failed" if i >= sample_count - failure_count else "succeeded"
                at = (START + timedelta(days=i * 14 / (sample_count - 1), seconds=10)).isoformat()
                end = (START + timedelta(days=i * 14 / (sample_count - 1), seconds=20)).isoformat()
                classification = "success" if status == "succeeded" else "transient_failure"
                db.execute("""INSERT INTO provider_chat_runs
                    (tenant_id,id,epoch_id,policy_fingerprint,capability,requested_model,actual_model,
                     strategy,status,result_class,is_real_user,primary_newapi,created_at,updated_at,completed_at)
                    VALUES ('local',?,'old',?,'chat_text','model','model','newapi_preferred',?,?,1,1,?,?,?)""",
                    (f"run-{i}", policy_fp, status, classification, at, end, end))
                db.execute("""INSERT INTO provider_chat_attempts
                    (tenant_id,id,run_id,capability,position,connection_id,provider_kind,dispatched,status,
                     result_class,actual_model,created_at,updated_at,completed_at)
                    VALUES ('local',?,?,'chat_text',0,'conn','newapi',1,?,?,'model',?,?,?)""",
                    (f"attempt-{i}", f"run-{i}", status, classification, at, end, end))
        snapshot = tmp_path / "source.sqlite3"
        with repo._connect() as db, closing(sqlite3.connect(snapshot)) as backup:
            db.backup(backup)
        with repo._connect() as db:
            new_at = START + timedelta(days=15)
            state = certify(db, "new-cert", fp, new_at, saved=True)
            db.execute("UPDATE provider_chat_model_qualifications SET certification_id='new-cert',qualified_at=?", (new_at.isoformat(),))
            _, new_material = policy_material(db, "local")
            qualifications = [{k: v for k, v in q.items() if k != "certification_id"} | {
                "qualification_series_id": state["series_id"], "qualification_interval_id": state["interval_id"]}
                for q in new_material["qualifications"]]
            new_fp = digest({**new_material, "qualifications": qualifications})
            db.execute("UPDATE provider_chat_stable_policies SET policy_fingerprint=?,revision=2", (new_fp,))
            db.execute("""UPDATE provider_chat_gate_epochs SET status='invalidated',
                hard_failure_code='provider_chat_policy_or_qualification_changed',closed_at=? WHERE id='old'""", (new_at.isoformat(),))
            db.execute("""INSERT INTO provider_chat_gate_epochs
                (tenant_id,id,policy_fingerprint,status,started_at) VALUES ('local','new',?,'collecting',?)""", (new_fp, new_at.isoformat()))
    proof = ReviewedHistoryEvidence("b" * 64, hashlib.sha256(snapshot.read_bytes()).hexdigest(), "old", "new", NOW,
        (OriginalLifetime("old-cert", 2592000, "c" * 64, START - timedelta(days=1), NOW),),
        ReviewedConfigurationContinuity(configuration_digest(material), "c" * 64, START - timedelta(days=1), NOW))
    return directory, snapshot, proof


def inspect(directory, snapshot, proof):
    with open_snapshot(snapshot, proof.source_snapshot_sha256) as source, closing(sqlite3.connect(directory / "router.sqlite3")) as current:
        current.row_factory = sqlite3.Row
        return verify_snapshot(source, current, "local", proof, now=NOW)


def test_positive_snapshot_preserves_failed_samples_and_original_rows(tmp_path):
    directory, snapshot, proof = seed(tmp_path)
    before = (snapshot.read_bytes(), (directory / "router.sqlite3").read_bytes())
    result = inspect(directory, snapshot, proof)
    assert len(result["samples"]) == 2
    assert [s["success"] for s in result["samples"]] == [True, False]
    assert before == (snapshot.read_bytes(), (directory / "router.sqlite3").read_bytes())


@pytest.mark.parametrize("sql", [
    "UPDATE provider_chat_runs SET result_class='success',status='succeeded' WHERE id='run-1'",
    "UPDATE provider_chat_attempts SET error_code='provider_chat_http_401' WHERE id='attempt-1'",
    "UPDATE provider_chat_stable_policies SET auto_enabled=1",
    "UPDATE provider_chat_stable_policies SET policy_fingerprint='changed'",
    "UPDATE router_connections SET enabled=0",
    "UPDATE router_connections SET api_key_ciphertext='rotated'",
    "UPDATE provider_qualification_intervals SET closed_at='2026-08-16T01:00:00+00:00'",
    "UPDATE provider_chat_certifications SET status='uncertain' WHERE id='new-cert'",
    "UPDATE provider_chat_gate_epochs SET hard_failure_code='hard' WHERE id='old'",
    "UPDATE provider_chat_gate_epochs SET closed_at='2026-08-16T01:00:00+00:00' WHERE id='new'",
])
def test_target_and_source_drift_fail_closed(tmp_path, sql):
    directory, snapshot, proof = seed(tmp_path)
    with sqlite3.connect(directory / "router.sqlite3") as db:
        db.execute(sql)
    with pytest.raises(HistoricalProofError):
        inspect(directory, snapshot, proof)


def test_missing_review_ttl_and_expiry_gap_fail_closed(tmp_path):
    directory, snapshot, proof = seed(tmp_path)
    for changed in (replace(proof, continuity=None), replace(proof, lifetimes=()),
                    replace(proof, lifetimes=(replace(proof.lifetimes[0], ttl_seconds=86400),)),
                    replace(proof, observed_until=NOW + timedelta(days=1))):
        with pytest.raises(HistoricalProofError):
            inspect(directory, snapshot, changed)


def test_snapshot_hash_sidecar_and_invalid_database_rejected(tmp_path):
    directory, snapshot, proof = seed(tmp_path)
    with pytest.raises(HistoricalProofError, match="snapshot_changed"):
        inspect(directory, snapshot, replace(proof, source_snapshot_sha256="0" * 64))
    sidecar = tmp_path / "source.sqlite3-wal"
    sidecar.write_bytes(b"synthetic")
    with pytest.raises(HistoricalProofError, match="not_self_contained"):
        inspect(directory, snapshot, proof)
