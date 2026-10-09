"""Offline historical proof cross-checks. Never grants or rewrites qualification.

Only a reviewed, self-contained Backup API snapshot is accepted. We deserialize
the exact hashed standalone file in immutable read-only mode: no source WAL,
recovery, migrations, repository initialization or secret decryption occurs.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sqlite3

from .qualification_history_evidence import ReviewedHistoryEvidence
from .qualification_history_proof import (
    HistoricalProofError, ProvenCertification, audit_sample, verify_continuity, _HARD_CODES,
)
from .qualification_events import _identity, read_qualification_state
from .qualifications import qualification_time_status


CAPABILITIES = ("chat_text", "chat_tools", "chat_file_output")
CERT_COLUMNS = ("tenant_id,id,connection_id,connection_fingerprint,contract_version,"
                "capability,requested_model,actual_model,status,created_at,completed_at")
RUN_COLUMNS = ("tenant_id,id,epoch_id,policy_fingerprint,capability,requested_model,actual_model,"
               "gateway,status,result_class,is_real_user,primary_newapi,client_cancelled,"
               "hard_failure,created_at,completed_at")
ATTEMPT_COLUMNS = ("tenant_id,id,run_id,capability,position,connection_id,provider_kind,"
                   "dispatched,status,result_class,error_code,actual_model,created_at,completed_at")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def reject(code):
    raise HistoricalProofError("provider_qualification_history_" + code)


def instant(value):
    try:
        result = datetime.fromisoformat(value)
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError()
        return result.astimezone(UTC)
    except (ValueError, TypeError):
        reject("time_invalid")


def rows(db, sql, args):
    result = db.execute(sql, args).fetchmany(10001)
    if len(result) > 10000:
        reject("audit_limit")
    return [dict(row) for row in result]


@contextmanager
def open_snapshot(path: Path, expected_digest: str):
    if path.is_symlink() or not path.is_file():
        reject("snapshot_invalid")
    # Sidecars mean this is not a reviewed self-contained backup.
    if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        reject("snapshot_not_self_contained")
    with path.open("rb") as stream:
        payload = stream.read(512 * 1024 * 1024 + 1)
    if not payload or len(payload) > 512 * 1024 * 1024:
        reject("snapshot_invalid")
    if hashlib.sha256(payload).hexdigest() != expected_digest:
        reject("snapshot_changed")
    # Backup API snapshots can retain a WAL-mode header. Immutable read-only
    # mode reads their self-contained pages without creating sidecars; it also
    # avoids pretending a WAL-header image is an in-memory rollback journal.
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    db.row_factory = sqlite3.Row
    try:
        del payload
        db.execute("PRAGMA trusted_schema=OFF")
        db.execute("PRAGMA query_only=ON")
        budget = 20000
        def progress():
            nonlocal budget
            budget -= 1
            return int(budget <= 0)
        db.set_progress_handler(progress, 1000)
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            reject("snapshot_invalid")
        # Do not execute views or virtual tables supplied by an evidence file.
        for name in ("provider_chat_stable_policies", "provider_chat_capability_routes",
                     "provider_chat_model_qualifications", "provider_chat_certifications",
                     "provider_chat_gate_epochs", "provider_chat_runs", "provider_chat_attempts"):
            record = db.execute("SELECT type,sql FROM sqlite_master WHERE name=?", (name,)).fetchone()
            if record is None or record["type"] != "table" or "VIRTUAL" in record["sql"].upper():
                reject("snapshot_schema_invalid")
        yield db
        with path.open("rb") as stream:
            payload = stream.read(512 * 1024 * 1024 + 1)
        if hashlib.sha256(payload).hexdigest() != expected_digest:
            reject("snapshot_changed")
    except sqlite3.Error:
        reject("snapshot_invalid")
    finally:
        db.close()


def policy_material(db, tenant):
    policy = db.execute("SELECT * FROM provider_chat_stable_policies WHERE tenant_id=?", (tenant,)).fetchone()
    if policy is None or policy["mode"] not in {"newapi_preferred", "newapi_required_default"}:
        reject("policy_missing")
    models = json.loads(policy["stable_models_json"])
    if not isinstance(models, list) or not models or any(not isinstance(m, str) for m in models):
        reject("policy_invalid")
    if models != sorted(set(models)):
        reject("policy_invalid")
    route_rows = rows(db, "SELECT capability,position,connection_id FROM provider_chat_capability_routes WHERE tenant_id=?", (tenant,))
    if any(r["capability"] not in CAPABILITIES for r in route_rows):
        reject("policy_invalid")
    routes = sorted(route_rows, key=lambda r: (CAPABILITIES.index(r["capability"]), r["position"]))
    for capability in CAPABILITIES:
        positions = [r["position"] for r in routes if r["capability"] == capability]
        if positions != list(range(len(positions))):
            reject("policy_invalid")
    if not routes or routes[0]["capability"] != "chat_text":
        reject("policy_invalid")
    qualifications = rows(db, """SELECT capability,connection_id,model_id,certification_id,
        connection_fingerprint,contract_version FROM provider_chat_model_qualifications WHERE tenant_id=?""", (tenant,))
    index = {(q["capability"], q["connection_id"], q["model_id"]): q for q in qualifications}
    ordered = []
    for route in routes:
        for model in models:
            q = index.pop((route["capability"], route["connection_id"], model), None)
            if q is None:
                reject("policy_qualification_missing")
            ordered.append(q)
    if index:
        reject("policy_invalid")
    return dict(policy), {
        "contract_version": "modelmirror-provider-chat-routing-v1",
        "mode": policy["mode"], "auto_enabled": bool(policy["auto_enabled"]),
        "stable_models": models, "routes": routes, "qualifications": ordered,
    }


def configuration_digest(material):
    return digest({**material, "qualifications": [
        {k: v for k, v in q.items() if k != "certification_id"}
        for q in material["qualifications"]]})


def epoch(db, tenant, epoch_id, *, allow_reviewed_invalidation=False):
    row = db.execute("SELECT * FROM provider_chat_gate_epochs WHERE tenant_id=? AND id=?", (tenant, epoch_id)).fetchone()
    if row is None:
        reject("epoch_missing")
    migration_invalidation = (allow_reviewed_invalidation and row["status"] == "invalidated"
        and row["hard_failure_code"] == "provider_chat_policy_or_qualification_changed"
        and row["closed_at"] is not None)
    if not migration_invalidation and (row["hard_failure_code"] or row["status"] not in {"open", "collecting", "ready", "closed"}):
        reject("epoch_not_eligible")
    return dict(row)


def sample_records(db, tenant, epoch_id):
    runs = rows(db, f"SELECT {RUN_COLUMNS} FROM provider_chat_runs WHERE tenant_id=? AND epoch_id=? ORDER BY id", (tenant, epoch_id))
    attempts = rows(db, f"SELECT {','.join('a.' + c for c in ATTEMPT_COLUMNS.split(','))} FROM provider_chat_attempts a JOIN provider_chat_runs r ON r.tenant_id=a.tenant_id AND r.id=a.run_id WHERE r.tenant_id=? AND r.epoch_id=? ORDER BY a.id", (tenant, epoch_id))
    return runs, attempts


def verify_snapshot(source, current, tenant, evidence: ReviewedHistoryEvidence, *, now: datetime):
    """Produce a proposal only. All failures reject the entire candidate epoch.

    No cherry-picking failed eligible samples; hard/unknown results veto the
    proposal. Current qualification and approval are not created by this proof.
    """
    witness = evidence.continuity
    if witness is None:
        reject("complete_history_review_required")
    if evidence.observed_until > now:
        reject("future_evidence")
    original, old = policy_material(source, tenant)
    target_policy, target = policy_material(current, tenant)
    source_epoch = epoch(source, tenant, evidence.source_epoch_id)
    live_source = epoch(current, tenant, evidence.source_epoch_id, allow_reviewed_invalidation=True)
    target_epoch = epoch(current, tenant, evidence.target_epoch_id)
    if (source_epoch["policy_fingerprint"] != original["policy_fingerprint"]
            or digest(old) != original["policy_fingerprint"]):
        reject("source_policy_snapshot_mismatch")
    if (configuration_digest(old) != configuration_digest(target)
            or witness.configuration_fingerprint != configuration_digest(old)):
        reject("configuration_changed")
    if (target_epoch["status"] not in {"open", "collecting", "ready"} or target_epoch["closed_at"] is not None
            or target_epoch["policy_fingerprint"] != target_policy["policy_fingerprint"]
            or not instant(source_epoch["started_at"]) < instant(target_epoch["started_at"]) <= evidence.observed_until):
        reject("target_epoch_invalid")
    if (live_source["policy_fingerprint"] != source_epoch["policy_fingerprint"]
            or live_source["started_at"] != source_epoch["started_at"]):
        reject("source_epoch_changed")
    if (live_source["closed_at"] is None
            or not instant(source_epoch["started_at"]) <= instant(live_source["closed_at"])
            <= instant(target_epoch["started_at"])):
        reject("source_epoch_not_closed")
    original_runs, original_attempts = sample_records(source, tenant, evidence.source_epoch_id)
    live_runs, live_attempts = sample_records(current, tenant, evidence.source_epoch_id)
    if original_runs != live_runs or original_attempts != live_attempts:
        reject("source_samples_changed")
    lifetimes = {item.certification_id: item for item in evidence.lifetimes}
    consumed = set()
    windows = {}
    target_qualifications = []
    for old_q, target_q in zip(old["qualifications"], target["qualifications"], strict=True):
        # Fingerprint comparison is local and never decrypts or reports secrets.
        from .repository import SQLiteRouterRepository
        connection = current.execute("""SELECT kind,base_url,scopes_json,api_key_ciphertext,enabled
            FROM router_connections WHERE tenant_id=? AND id=?""", (tenant, target_q["connection_id"])).fetchone()
        if (connection is None or not connection["enabled"] or "chat" not in json.loads(connection["scopes_json"])
                or SQLiteRouterRepository._connection_config_fingerprint_row(connection) != target_q["connection_fingerprint"]):
            reject("connection_changed")
        if old_q["connection_id"] == old["routes"][0]["connection_id"] and connection["kind"] != "newapi":
            reject("primary_not_newapi")
        original_cert = source.execute(f"SELECT {CERT_COLUMNS} FROM provider_chat_certifications WHERE tenant_id=? AND id=?", (tenant, old_q["certification_id"])).fetchone()
        if original_cert is None:
            reject("certification_missing")
        original_cert = dict(original_cert)
        for q in (old_q, target_q):
            if (q["connection_fingerprint"] != original_cert["connection_fingerprint"]
                    or q["contract_version"] != original_cert["contract_version"]
                    or q["connection_id"] != original_cert["connection_id"]
                    or q["model_id"] != original_cert["requested_model"]
                    or q["capability"] != original_cert["capability"]):
                reject("certification_identity_changed")
        chain = rows(current, f"SELECT {CERT_COLUMNS} FROM provider_chat_certifications WHERE tenant_id=? AND connection_id=? AND requested_model=? AND capability=? ORDER BY created_at,id", (tenant, old_q["connection_id"], old_q["model_id"], old_q["capability"]))
        # Never filter intervening changes/failures by fingerprint or status.
        chain = [c for c in chain if instant(c["created_at"]) >= instant(original_cert["created_at"])]
        if not chain or chain[0] != original_cert or not any(c["id"] == target_q["certification_id"] for c in chain):
            reject("certification_snapshot_changed")
        identity = _identity(original_cert, "provider_chat").series_id
        history = rows(current, """SELECT r.created_at,r.hard_failure,a.result_class,a.error_code
            FROM provider_chat_runs r JOIN provider_chat_attempts a
              ON a.tenant_id=r.tenant_id AND a.run_id=r.id
            WHERE r.tenant_id=? AND a.connection_id=? AND r.requested_model=?
              AND r.capability=? AND a.dispatched=1""", (tenant, old_q["connection_id"], old_q["model_id"], old_q["capability"]))
        for observation in history:
            if instant(observation["created_at"]) >= instant(original_cert["created_at"]) and (
                    observation["hard_failure"] or observation["result_class"] == "hard_failure"
                    or observation["error_code"] in _HARD_CODES):
                reject("historical_hard_failure")
        events = []
        for cert in chain:
            if cert["status"] != "passed" or cert["actual_model"] != cert["requested_model"]:
                reject("certification_not_passed")
            state = read_qualification_state(current, tenant, "provider_chat", cert["id"])
            if state is None:
                witnessed = source.execute(f"SELECT {CERT_COLUMNS} FROM provider_chat_certifications WHERE tenant_id=? AND id=?",
                                           (tenant, cert["id"])).fetchone()
                if witnessed is None or dict(witnessed) != cert:
                    reject("certification_snapshot_changed")
                lifetime = lifetimes.get(cert["id"])
                if lifetime is None or not (lifetime.effective_from <= instant(cert["created_at"])
                                           <= instant(cert["completed_at"]) <= lifetime.effective_until):
                    reject("ttl_unproven")
                consumed.add(cert["id"])
                ttl = lifetime.ttl_seconds
            else:
                if state["status"] != "passed" or state["closed_at"] is not None:
                    reject("qualification_invalidated")
                if state["series_id"] != identity or state["completed_at"] != cert["completed_at"]:
                    reject("qualification_observation_changed")
                ttl = state["ttl_seconds"]
            events.append(ProvenCertification(cert["id"], _identity(cert, "provider_chat").series_id,
                instant(cert["completed_at"]), ttl, cert["status"]))
        if not witness.effective_from <= events[0].completed_at <= instant(source_epoch["started_at"]):
            reject("witness_window_invalid")
        # Reviewed external evidence reaches the currently approved anchor; all
        # subsequent renewals are immutable v19 events, not external assertions.
        anchor = next(c for c in chain if c["id"] == target_q["certification_id"])
        if instant(anchor["completed_at"]) > witness.effective_until:
            reject("witness_window_invalid")
        state = read_qualification_state(current, tenant, "provider_chat", anchor["id"])
        latest_state = read_qualification_state(current, tenant, "provider_chat", chain[-1]["id"])
        if (state is None or latest_state is None or state["interval_id"] != latest_state["interval_id"]
                or qualification_time_status(latest_state, now=now)[0] is not None):
            reject("target_qualification_invalid")
        target_qualifications.append({k: v for k, v in target_q.items() if k != "certification_id"} | {
            "qualification_series_id": state["series_id"], "qualification_interval_id": state["interval_id"]})
        windows[(old_q["capability"], old_q["connection_id"], old_q["model_id"])] = verify_continuity(
            tuple(events), expected_identity_digest=identity, observed_until=now)
    if consumed != set(lifetimes):
        reject("unused_lifetime_evidence")
    if digest({**target, "qualifications": target_qualifications}) != target_policy["policy_fingerprint"]:
        reject("target_policy_changed")
    primary = old["routes"][0]["connection_id"]
    by_run = {}
    for attempt in original_attempts:
        by_run.setdefault(attempt["run_id"], []).append(attempt)
    decisions = []
    for run in original_runs:
        attempts = tuple(by_run.get(run["id"], []))
        # Non-user/Auto/Canary/explicitly cancelled samples never contribute.
        # Hard failures remain a veto even on an otherwise excluded sample.
        if run["hard_failure"] or any(a["result_class"] == "hard_failure" for a in attempts):
            reject("historical_hard_failure")
        if not (run["capability"] == "chat_text" and run["gateway"] == "default"
                and run["is_real_user"] == 1 and run["primary_newapi"] == 1 and run["client_cancelled"] == 0):
            continue
        window = windows.get(("chat_text", primary, run["requested_model"]))
        if window is None:
            reject("sample_model_invalid")
        result = audit_sample(run, attempts, tenant_id=tenant,
            source_policy_fingerprint=original["policy_fingerprint"], stable_model_ids=frozenset(old["stable_models"]),
            primary_connection_id=primary, continuity=window)
        if (not result["eligible"] or instant(run["completed_at"]) > evidence.observed_until
                or instant(run["completed_at"]) > instant(live_source["closed_at"])
                or instant(run["created_at"]) < instant(source_epoch["started_at"])):
            reject("sample_not_proven")
        decisions.append({**result, "sample_fingerprint": digest([run, list(attempts)])})
    if not decisions:
        reject("no_eligible_samples")
    return {"source_epoch_id": evidence.source_epoch_id, "target_epoch_id": evidence.target_epoch_id,
            "target_policy_fingerprint": target_policy["policy_fingerprint"],
            "target_policy_revision": target_policy["revision"], "samples": decisions,
            "source_ledger_fingerprint": digest([live_source, live_runs, live_attempts]),
            "evidence_fingerprint": evidence.manifest_sha256,
            "configuration_fingerprint": configuration_digest(old)}
