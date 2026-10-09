"""Offline, fail-closed audit of historical R5 epoch inheritance.

v18 epochs did not retain immutable route/certification snapshots. Neither a
current policy nor a caller-provided TTL can prove their historical identity.
Without external proof this command records rejection without rewriting any
sample, certification or approval. Positive inheritance additionally requires a
reviewed v2 manifest, original snapshot and original deployment/audit artifacts.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
import sqlite3
import uuid
from pathlib import Path

from .repository import DEFAULT_TENANT_ID, SQLiteRouterRepository, RouterRepositoryError
from .storage_lifecycle import ProviderStorageError
from .qualification_history_proof import HistoricalProofError


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _audit(db, tenant_id: str, epoch_id: str) -> dict:
    epoch = db.execute("""SELECT id, policy_fingerprint, status, hard_failure_code,
        started_at, closed_at FROM provider_chat_gate_epochs WHERE tenant_id=? AND id=?""",
        (tenant_id, epoch_id)).fetchone()
    if epoch is None:
        raise RouterRepositoryError("provider_qualification_epoch_not_found")
    # These columns are persisted control-plane metadata only. No credentials,
    # endpoints, prompts, raw responses or external evidence are read.
    runs = db.execute("""SELECT id, policy_fingerprint, capability, requested_model,
        actual_model, gateway, status, result_class, is_real_user, primary_newapi,
        client_cancelled, hard_failure, created_at, updated_at, completed_at
        FROM provider_chat_runs WHERE tenant_id=? AND epoch_id=? ORDER BY id LIMIT 10001""",
        (tenant_id, epoch_id)).fetchall()
    if len(runs) > 10000:
        raise RouterRepositoryError("provider_qualification_history_audit_limit")
    attempts = db.execute("""SELECT a.id, a.run_id, a.capability, a.position,
        a.connection_id, a.provider_kind, a.dispatched, a.status, a.result_class,
        a.error_code, a.actual_model, a.created_at, a.updated_at, a.completed_at
        FROM provider_chat_attempts a JOIN provider_chat_runs r
          ON r.tenant_id=a.tenant_id AND r.id=a.run_id
        WHERE r.tenant_id=? AND r.epoch_id=? ORDER BY a.id LIMIT 10001""",
        (tenant_id, epoch_id)).fetchall()
    if len(attempts) > 10000:
        raise RouterRepositoryError("provider_qualification_history_audit_limit")
    missing_ttl = db.execute("""SELECT COUNT(*) FROM provider_chat_certifications c
        WHERE c.tenant_id=? AND NOT EXISTS (
          SELECT 1 FROM provider_qualification_events q
          WHERE q.tenant_id=c.tenant_id AND q.source='provider_chat' AND q.certification_id=c.id)
        AND EXISTS (SELECT 1 FROM provider_chat_attempts a JOIN provider_chat_runs r
          ON r.tenant_id=a.tenant_id AND r.id=a.run_id
          WHERE r.tenant_id=c.tenant_id AND r.epoch_id=?
            AND a.connection_id=c.connection_id AND r.requested_model=c.requested_model
            AND a.capability=c.capability)""", (tenant_id, epoch_id)).fetchone()[0]
    # Even a saved TTL cannot manufacture the missing historical association.
    reasons = ["provider_qualification_epoch_snapshot_missing"]
    if missing_ttl:
        reasons.append("provider_qualification_historical_expiry_unknown")
    if epoch["hard_failure_code"] or any(r["hard_failure"] for r in runs) or any(
        a["dispatched"] and a["result_class"] == "hard_failure" for a in attempts
    ):
        reasons.append("provider_qualification_historical_hard_failure")
    if any(r["status"] in {"running", "uncertain"} for r in runs):
        reasons.append("provider_qualification_historical_result_unresolved")
    evidence = _digest({"epoch": dict(epoch), "runs": list(map(dict, runs)),
                        "attempts": list(map(dict, attempts)), "unknown_expiry_count": missing_ttl})
    report = {
        "contract_version": "modelmirror-qualification-history-audit-v1",
        "source_epoch_id": epoch_id, "target_epoch_id": None,
        "status": "rejected", "reason_codes": reasons,
        "sample_count": len(runs), "inherited_sample_count": 0,
        "evidence_fingerprint": evidence,
    }
    report["plan_fingerprint"] = _digest({"tenant_id": tenant_id, **report})
    return report


def audit_history(repository, tenant_id: str, epoch_id: str) -> dict:
    """No migration, recovery, network, file creation or status updates."""
    with repository._lock, repository._connect() as db:
        db.execute("BEGIN")
        return _audit(db, repository._tenant_id(tenant_id), epoch_id)


def record_rejection(repository, tenant_id: str, epoch_id: str, expected_plan: str) -> dict:
    """Explicit apply records only a rejection. It never publishes a policy."""
    repository._require_writer()
    if repository._recovered or repository._master_key is not None:
        raise RouterRepositoryError("provider_qualification_history_offline_required")
    tenant_id = repository._tenant_id(tenant_id)
    with repository._lock, repository._connect() as db:
        report = _audit(db, tenant_id, epoch_id)
        if report["plan_fingerprint"] != expected_plan:
            raise RouterRepositoryError("provider_qualification_history_plan_changed")
        existing = db.execute("""SELECT id FROM provider_qualification_epoch_mappings
            WHERE tenant_id=? AND source_epoch_id=? AND plan_fingerprint=?""",
            (tenant_id, epoch_id, expected_plan)).fetchone()
        if existing:
            return {**report, "applied": True, "already_recorded": True}
        # Preserve a full consistent local backup before the additive audit write.
        # Unique exclusive creation prevents overwriting a previous backup.
        backup_path = repository.storage_dir / f"router.sqlite3.qualification-audit-backup-{uuid.uuid4().hex}"
        with backup_path.open("xb"):
            pass
        backup = sqlite3.connect(backup_path)
        try:
            db.backup(backup)
            if backup.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RouterRepositoryError("provider_qualification_history_backup_invalid")
        finally:
            backup.close()
        db.execute("BEGIN IMMEDIATE")
        if _audit(db, tenant_id, epoch_id)["plan_fingerprint"] != expected_plan:
            raise RouterRepositoryError("provider_qualification_history_plan_changed")
        db.execute("""INSERT INTO provider_qualification_epoch_mappings
            (tenant_id,id,source_epoch_id,target_epoch_id,plan_fingerprint,
             evidence_fingerprint,status,reason_codes_json,created_at)
            VALUES (?, ?, ?, NULL, ?, ?, 'rejected', ?, ?)""",
            (tenant_id, "qualmap_" + expected_plan, epoch_id, expected_plan,
             report["evidence_fingerprint"], json.dumps(report["reason_codes"]), datetime.now(UTC).isoformat()))
        return {**report, "applied": True, "already_recorded": False}


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit historical qualification inheritance, read-only by default.")
    parser.add_argument("--storage-dir", required=True)
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--apply", action="store_true", help="Append the reviewed mapping after a consistent backup; never activate policy.")
    parser.add_argument("--expected-plan-fingerprint")
    parser.add_argument("--proof-manifest", type=Path)
    parser.add_argument("--reviewed-manifest-sha256")
    parser.add_argument("--source-snapshot", type=Path)
    parser.add_argument("--deployment-artifact", action="append", default=[], metavar="SHA256=LOCAL_PATH")
    parser.add_argument("--acknowledge-provenance-review", action="store_true")
    values = parser.parse_args()
    if values.apply and not values.expected_plan_fingerprint:
        parser.error("--apply requires --expected-plan-fingerprint from the reviewed dry-run")
    proof_inputs = None
    if values.proof_manifest:
        if not values.reviewed_manifest_sha256 or not values.source_snapshot or not values.acknowledge_provenance_review:
            parser.error("proof import requires reviewed digest, source snapshot and explicit provenance acknowledgement")
        artifacts = {}
        for item in values.deployment_artifact:
            key, separator, path = item.partition("=")
            if not separator or not path or key in artifacts:
                parser.error("deployment artifacts require distinct SHA256=LOCAL_PATH arguments")
            artifacts[key] = Path(path)
        proof_inputs = dict(manifest_path=values.proof_manifest,
            reviewed_manifest_sha256=values.reviewed_manifest_sha256, source_snapshot=values.source_snapshot,
            deployment_artifacts=artifacts, acknowledge_provenance_review=True)
    elif values.reviewed_manifest_sha256 or values.source_snapshot or values.deployment_artifact or values.acknowledge_provenance_review:
        parser.error("proof options require --proof-manifest")
    try:
        with SQLiteRouterRepository.open_maintenance(values.storage_dir, readonly=not values.apply) as repository:
            if proof_inputs is None:
                result = (record_rejection(repository, DEFAULT_TENANT_ID, values.epoch_id, values.expected_plan_fingerprint)
                          if values.apply else audit_history(repository, DEFAULT_TENANT_ID, values.epoch_id))
            else:
                from .qualification_history_import import audit_import, apply_import
                result = audit_import(repository, DEFAULT_TENANT_ID, **proof_inputs)
                if result["source_epoch_id"] != values.epoch_id:
                    raise HistoricalProofError("provider_qualification_history_source_epoch_mismatch")
                if values.apply:
                    result = apply_import(repository, DEFAULT_TENANT_ID,
                        expected_plan_fingerprint=values.expected_plan_fingerprint, **proof_inputs)
    except (RouterRepositoryError, ProviderStorageError, HistoricalProofError) as exc:
        print(json.dumps({"status": "blocked", "reason_code": str(exc)}))
        return 1
    except (OSError, sqlite3.Error):
        print(json.dumps({"status": "blocked", "reason_code": "provider_qualification_history_storage_error"}))
        return 1
    except (ValueError, TypeError, KeyError):
        print(json.dumps({"status": "blocked", "reason_code": "provider_qualification_history_evidence_invalid"}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
