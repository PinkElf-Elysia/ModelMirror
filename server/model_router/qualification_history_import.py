"""Offline positive inheritance and read-only contribution to the R5 gate.

No old samples, qualifications, policies or approvals are rewritten. The source
ledger remains authoritative; each accepted mapping pins a metadata digest.
"""
from __future__ import annotations

from datetime import UTC, datetime
from contextlib import closing
import sqlite3
import uuid

from .qualification_history_snapshot import (
    ATTEMPT_COLUMNS, RUN_COLUMNS, digest, instant, open_snapshot,
    epoch, policy_material, reject, rows, sample_records, verify_snapshot,
)
from .qualification_history_evidence import read_reviewed_evidence
from .qualification_events import _identity, admission_certification, read_qualification_state
from .qualifications import qualification_time_status


def audit_import(repository, tenant_id, *, manifest_path, reviewed_manifest_sha256,
                 source_snapshot, deployment_artifacts, acknowledge_provenance_review=False):
    """Read-only proposal; no lock creation, backup, migration or recovery."""
    with repository._lock, repository._connect() as db:
        db.execute("BEGIN")
        return _proposal(db, repository._tenant_id(tenant_id), manifest_path=manifest_path,
            reviewed_manifest_sha256=reviewed_manifest_sha256, source_snapshot=source_snapshot,
            deployment_artifacts=deployment_artifacts,
            acknowledge_provenance_review=acknowledge_provenance_review)


def _proposal(db, tenant, **inputs):
    proof = read_reviewed_evidence(**inputs)
    with open_snapshot(inputs["source_snapshot"], proof.source_snapshot_sha256) as source:
        facts = verify_snapshot(source, db, tenant, proof, now=datetime.now(UTC))
    # One original logical sample can only be inherited once, even across target
    # epochs. A fresh target is not permission to reuse old observation windows.
    for sample in facts["samples"]:
        existing = db.execute("""SELECT target_epoch_id,sample_fingerprint FROM
            provider_qualification_inherited_samples WHERE tenant_id=? AND source_run_id=?""",
            (tenant, sample["source_run_id"])).fetchone()
        if existing is not None and (existing["target_epoch_id"] != facts["target_epoch_id"]
                                    or existing["sample_fingerprint"] != sample["sample_fingerprint"]):
            reject("sample_already_inherited")
    report = {"contract_version": "modelmirror-qualification-history-import-v1", "status": "accepted",
              "reason_codes": [], "applied": False, "inherited_sample_count": len(facts["samples"]), **facts}
    report["plan_fingerprint"] = digest({"tenant_id": tenant, **report})
    return report


def apply_import(repository, tenant_id, *, expected_plan_fingerprint, **inputs):
    """Explicit offline apply: backup, recheck, atomically append, no activation."""
    repository._require_writer()
    if repository._recovered or repository._master_key is not None:
        reject("offline_required")
    tenant = repository._tenant_id(tenant_id)
    with repository._lock, repository._connect() as db:
        report = _proposal(db, tenant, **inputs)
        if report["plan_fingerprint"] != expected_plan_fingerprint:
            reject("plan_changed")
        existing = db.execute("""SELECT id FROM provider_qualification_epoch_mappings
            WHERE tenant_id=? AND source_epoch_id=? AND plan_fingerprint=? AND status='accepted'""",
            (tenant, report["source_epoch_id"], expected_plan_fingerprint)).fetchone()
        if existing is not None:
            _verify_recorded(db, tenant, report, existing["id"])
            return {**report, "applied": True, "already_recorded": True}
        backup_path = repository.storage_dir / f"router.sqlite3.qualification-import-backup-{uuid.uuid4().hex}"
        with backup_path.open("xb"):
            pass
        with closing(sqlite3.connect(backup_path)) as backup:
            db.backup(backup)
            if backup.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                reject("backup_invalid")
        db.execute("BEGIN IMMEDIATE")
        checked = _proposal(db, tenant, **inputs)
        if checked["plan_fingerprint"] != expected_plan_fingerprint:
            reject("plan_changed")
        mapping_id = "qualmap_" + expected_plan_fingerprint
        db.execute("""INSERT INTO provider_qualification_epoch_mappings
            (tenant_id,id,source_epoch_id,target_epoch_id,plan_fingerprint,evidence_fingerprint,
             status,reason_codes_json,created_at) VALUES (?,?,?,?,?,?,'accepted','[]',?)""",
            (tenant, mapping_id, report["source_epoch_id"], report["target_epoch_id"],
             expected_plan_fingerprint, report["evidence_fingerprint"], datetime.now(UTC).isoformat()))
        samples_fingerprint = digest(sorted(
            [[item["source_run_id"], item["sample_fingerprint"]] for item in report["samples"]]))
        for sample in report["samples"]:
            db.execute("""INSERT INTO provider_qualification_inherited_samples
                (tenant_id,mapping_id,source_epoch_id,target_epoch_id,target_policy_fingerprint,
                 source_run_id,sample_fingerprint,source_ledger_fingerprint,mapping_samples_fingerprint)
                 VALUES (?,?,?,?,?,?,?,?,?)""",
                (tenant, mapping_id, report["source_epoch_id"], report["target_epoch_id"],
                 report["target_policy_fingerprint"], sample["source_run_id"], sample["sample_fingerprint"],
                 report["source_ledger_fingerprint"], samples_fingerprint))
        return {**report, "applied": True, "already_recorded": False}


def _verify_recorded(db, tenant, report, mapping_id):
    saved = rows(db, """SELECT source_run_id,sample_fingerprint FROM provider_qualification_inherited_samples
        WHERE tenant_id=? AND mapping_id=? ORDER BY source_run_id""", (tenant, mapping_id))
    expected = sorted(({k: item[k] for k in ("source_run_id", "sample_fingerprint")}
                       for item in report["samples"]), key=lambda item: item["source_run_id"])
    if saved != expected:
        reject("recorded_mapping_incomplete")


def _current_target(db, tenant, epoch_id, now):
    from .repository import SQLiteRouterRepository
    policy, material = policy_material(db, tenant)
    epoch = db.execute("""SELECT policy_fingerprint,closed_at,hard_failure_code,status FROM provider_chat_gate_epochs
        WHERE tenant_id=? AND id=?""", (tenant, epoch_id)).fetchone()
    if (epoch is None or epoch["status"] not in {"open", "collecting", "ready"}
            or epoch["closed_at"] is not None or epoch["hard_failure_code"]
            or epoch["policy_fingerprint"] != policy["policy_fingerprint"]):
        reject("target_epoch_invalid")
    qualified = []
    for q in material["qualifications"]:
        state = read_qualification_state(db, tenant, "provider_chat", q["certification_id"])
        latest = db.execute("""SELECT * FROM provider_chat_certifications
            WHERE tenant_id=? AND connection_id=? AND requested_model=? AND capability=?
            ORDER BY created_at DESC,id DESC LIMIT 1""",
            (tenant, q["connection_id"], q["model_id"], q["capability"])).fetchone()
        latest = admission_certification(db, tenant, "provider_chat", dict(latest) if latest else None)
        latest_state = read_qualification_state(db, tenant, "provider_chat", latest["id"]) if latest else None
        conn = db.execute("""SELECT kind,base_url,scopes_json,api_key_ciphertext,enabled FROM router_connections
            WHERE tenant_id=? AND id=?""", (tenant, q["connection_id"])).fetchone()
        if (state is None or latest_state is None or state["interval_id"] != latest_state["interval_id"]
                or state["series_id"] != latest_state["series_id"]
                or state["status"] != "passed" or latest["status"] != "passed"
                or latest["actual_model"] != q["model_id"]
                or latest["connection_fingerprint"] != q["connection_fingerprint"]
                or latest["contract_version"] != q["contract_version"]
                or _identity(dict(latest), "provider_chat").series_id != latest_state["series_id"]
                or qualification_time_status(latest_state, now=now)[0] is not None
                or conn is None or not conn["enabled"]
                or SQLiteRouterRepository._connection_config_fingerprint_row(conn) != q["connection_fingerprint"]):
            reject("target_qualification_invalid")
        qualified.append({k: v for k, v in q.items() if k != "certification_id"} | {
            "qualification_series_id": state["series_id"], "qualification_interval_id": state["interval_id"]})
    candidate = {**material, "qualifications": qualified}
    # Existing required activation preserves the preferred policy fingerprint.
    candidates = [digest(candidate)]
    if policy["mode"] == "newapi_required_default":
        candidates.append(digest({**candidate, "mode": "newapi_preferred"}))
    if policy["policy_fingerprint"] not in candidates:
        reject("target_policy_changed")
    return policy["policy_fingerprint"]


def inherited_gate_rows(db, tenant_id, epoch_id):
    """Used by BOTH status and atomic activate-required, under their transaction.

    Missing/mutated original evidence blocks the inherited set as a whole, never
    silently drops just a failed sample. No files, network or writes occur here.
    """
    headers = rows(db, """SELECT id FROM provider_qualification_epoch_mappings
        WHERE tenant_id=? AND target_epoch_id=? AND status='accepted'""", (tenant_id, epoch_id))
    mappings = rows(db, """SELECT s.* FROM provider_qualification_inherited_samples s
        JOIN provider_qualification_epoch_mappings m ON m.tenant_id=s.tenant_id AND m.id=s.mapping_id
        WHERE s.tenant_id=? AND s.target_epoch_id=? AND m.status='accepted'
          AND m.source_epoch_id=s.source_epoch_id AND m.target_epoch_id=s.target_epoch_id
        ORDER BY s.source_run_id""", (tenant_id, epoch_id))
    if not headers and not mappings:
        return []
    grouped = {}
    for item in mappings:
        grouped.setdefault(item["mapping_id"], []).append(item)
    if set(grouped) != {header["id"] for header in headers}:
        reject("recorded_mapping_incomplete")
    for items in grouped.values():
        expected = digest(sorted([[item["source_run_id"], item["sample_fingerprint"]] for item in items]))
        if any(item["mapping_samples_fingerprint"] != expected for item in items):
            reject("recorded_mapping_incomplete")
    target_fp = _current_target(db, tenant_id, epoch_id, datetime.now(UTC))
    result = []
    ledgers = {}
    for mapping in mappings:
        if mapping["target_policy_fingerprint"] != target_fp or mapping["source_epoch_id"] == epoch_id:
            reject("target_policy_changed")
        source_epoch_id = mapping["source_epoch_id"]
        if source_epoch_id not in ledgers:
            original = epoch(db, tenant_id, source_epoch_id, allow_reviewed_invalidation=True)
            runs, attempts = sample_records(db, tenant_id, source_epoch_id)
            ledgers[source_epoch_id] = digest([original, runs, attempts])
        if ledgers[source_epoch_id] != mapping["source_ledger_fingerprint"]:
            reject("source_samples_changed")
        record = db.execute(f"SELECT {RUN_COLUMNS} FROM provider_chat_runs WHERE tenant_id=? AND id=? AND epoch_id=?",
            (tenant_id, mapping["source_run_id"], mapping["source_epoch_id"])).fetchone()
        attempts = rows(db, f"SELECT {ATTEMPT_COLUMNS} FROM provider_chat_attempts WHERE tenant_id=? AND run_id=? ORDER BY id",
            (tenant_id, mapping["source_run_id"]))
        if record is None or digest([dict(record), attempts]) != mapping["sample_fingerprint"]:
            reject("source_samples_changed")
        result.append(dict(record))
    return result
