"""Qualification writes participate in the certification owner's transaction.

This module never commits, resolves credentials, calls a Provider or publishes
policy. Missing historical events remain unknown rather than being backfilled.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import json
import sqlite3

from .qualifications import QualificationIdentity, QualificationWindow, decide_renewal, certification_ttl_seconds
from .qualifications import qualification_time_status
from datetime import UTC


_TABLES = {
    "provider_chat": "provider_chat_certifications",
    "provider_workload": "provider_workload_certifications",
}

# Existing adapters use both hard_failure and provider_error result classes.
# Only explicit authentication/contract failures break qualification continuity;
# transport uncertainty, cancellation and request-specific errors do not.
_WORKLOAD_HARD_FAILURE_CODES = frozenset({
    "provider_workload_http_401", "provider_workload_http_402",
    "provider_workload_http_403", "provider_workload_http_404",
    "provider_workload_actual_model_mismatch", "provider_workload_model_mismatch",
    "provider_workload_empty_response", "provider_workload_empty_stream",
    "provider_workload_invalid_sse", "provider_workload_missing_terminal",
    "provider_chat_empty_stream", "provider_chat_missing_terminal",
    # R7/R8 adapters deliberately retain their own public error namespaces.
    # Match explicit authentication/contract facts, not broad error prefixes:
    # request-specific errors, cancellation and transient failures stay distinct.
    "provider_embedding_http_401", "provider_embedding_http_402",
    "provider_embedding_http_403", "provider_embedding_http_404",
    "provider_rerank_http_401", "provider_rerank_http_402",
    "provider_rerank_http_403", "provider_rerank_http_404",
    "provider_embedding_model_mismatch", "provider_rerank_model_mismatch",
    "provider_multimodal_invalid_sse",
})


def workload_qualification_hard_failure(*, dispatched, status, result_class, error_code):
    return bool(dispatched) and status == "failed" and (
        result_class == "hard_failure" or error_code in _WORKLOAD_HARD_FAILURE_CODES
    )


def _transaction(db: sqlite3.Connection) -> None:
    if not db.in_transaction:
        raise ValueError("provider_qualification_transaction_required")


def _certification(db, tenant_id, source, certification_id):
    if source not in _TABLES:
        raise ValueError("provider_qualification_source_invalid")
    row = db.execute(
        f"SELECT * FROM {_TABLES[source]} WHERE tenant_id = ? AND id = ?",
        (tenant_id, certification_id),
    ).fetchone()
    if row is None:
        raise ValueError("provider_qualification_certification_missing")
    return dict(row)


def _identity(row, source):
    return QualificationIdentity(
        tenant_id=row["tenant_id"], source=source, connection_id=row["connection_id"],
        connection_fingerprint=row["connection_fingerprint"], model_id=row["requested_model"],
        execution_shape=row["capability"] if source == "provider_chat" else row["execution_shape"],
        contract_version=row["contract_version"], adapter_contract=row.get("adapter_contract"),
        protocol_version=row.get("protocol_version"), profile_fingerprint=row.get("profile_fingerprint", ""),
    )


def read_qualification_event(db, tenant_id, source, certification_id):
    row = db.execute("""SELECT * FROM provider_qualification_events
        WHERE tenant_id = ? AND source = ? AND certification_id = ?""",
        (tenant_id, source, certification_id),
    ).fetchone()
    return dict(row) if row is not None else None


def read_qualification_state(db, tenant_id, source, certification_id):
    row = db.execute("""SELECT e.*, i.valid_from, i.closed_at, i.closure_reason,
        (SELECT o.observed_at FROM provider_qualification_observations o
         WHERE o.tenant_id=e.tenant_id AND o.source=e.source
           AND o.certification_id=e.certification_id AND o.status='uncertain'
         ORDER BY o.sequence LIMIT 1) AS first_uncertain_at
        FROM provider_qualification_events e
        LEFT JOIN provider_qualification_intervals i
            ON i.tenant_id = e.tenant_id AND i.id = e.interval_id
        WHERE e.tenant_id = ? AND e.source = ? AND e.certification_id = ?""",
        (tenant_id, source, certification_id),
    ).fetchone()
    return dict(row) if row is not None else None


def share_open_qualification_interval(db, tenant_id, source, saved_id, current_id):
    """Approval anchors may outlive one event, never their continuous interval."""
    row = db.execute("""SELECT 1 FROM provider_qualification_events saved
        JOIN provider_qualification_events current
            ON current.tenant_id = saved.tenant_id AND current.source = saved.source
            AND current.series_id = saved.series_id AND current.interval_id = saved.interval_id
        JOIN provider_qualification_intervals i
            ON i.tenant_id = saved.tenant_id AND i.id = saved.interval_id
        WHERE saved.tenant_id = ? AND saved.source = ? AND saved.certification_id = ?
            AND current.certification_id = ? AND saved.status = 'passed'
            AND current.status = 'passed' AND i.closed_at IS NULL""",
        (tenant_id, source, saved_id, current_id),
    ).fetchone()
    return row is not None


def admission_certification(db, tenant_id, source, latest):
    """A pending renewal is not yet a failure, nor a new qualification.

    Only a genuinely new running event of the identical series may retain the
    immediately preceding valid event. A resumed uncertain event, failed event,
    changed profile, unknown history or expired interval never receives this.
    """
    if latest is not None and latest["tenant_id"] != tenant_id:
        raise ValueError("provider_qualification_tenant_mismatch")
    if latest is None or latest["status"] != "running":
        return latest
    event = read_qualification_event(db, tenant_id, source, latest["id"])
    if event is None or event["status"] != "running":
        return latest
    previous = db.execute("""SELECT certification_id FROM provider_qualification_events
        WHERE tenant_id=? AND source=? AND series_id=? AND certification_id!=?
        ORDER BY rowid DESC LIMIT 1""", (tenant_id, source, event["series_id"], latest["id"])).fetchone()
    if previous is None:
        return latest
    state = read_qualification_state(db, tenant_id, source, previous["certification_id"])
    if qualification_time_status(state, now=datetime.now(UTC))[0] is not None:
        return latest
    return _certification(db, tenant_id, source, previous["certification_id"])


def dispatch_qualification_current(db, *, tenant_id, source, certification_id,
                                   connection_id, model_id, execution_shape,
                                   adapter_contract, rerank_access_mode):
    """Recheck the latest exact route qualification inside the dispatch lock.

    A prepared call may keep its original event reference after an on-time
    renewal, but never outlive the current qualification or cross a profile.
    """
    _transaction(db)
    if source not in _TABLES:
        raise ValueError("provider_qualification_source_invalid")
    shape_column = "capability" if source == "provider_chat" else "execution_shape"
    values = [tenant_id, connection_id, model_id, execution_shape]
    adapter_clause = ""
    if source == "provider_workload":
        adapter_clause = " AND COALESCE(adapter_contract, '') = ?"
        values.append(adapter_contract or "")
    rows = db.execute(f"""SELECT * FROM {_TABLES[source]}
        WHERE tenant_id=? AND connection_id=? AND requested_model=? AND {shape_column}=?
        {adapter_clause} ORDER BY created_at DESC, id DESC""", values)
    latest = None
    for row in rows:
        if rerank_access_mode is not None:
            try:
                profile = json.loads(row["profile_json"] or "{}")
            except (ValueError, TypeError):
                return False
            if not isinstance(profile, dict):
                return False
            if profile.get("rerank_access_mode") != rerank_access_mode:
                continue
        latest = row
        break
    latest = admission_certification(db, tenant_id, source, latest)
    if latest is None or not share_open_qualification_interval(
        db, tenant_id, source, certification_id, latest["id"],
    ):
        return False
    return qualification_time_status(
        read_qualification_state(db, tenant_id, source, latest["id"]), now=datetime.now(UTC),
    )[0] is None


def begin_qualification_event(db, *, tenant_id, source, certification_id, configured_ttl):
    _transaction(db)
    row = _certification(db, tenant_id, source, certification_id)
    existing = read_qualification_event(db, tenant_id, source, certification_id)
    if existing is not None:
        return existing  # never reinterpret a saved TTL on an idempotent read
    if row["status"] != "running":
        raise ValueError("provider_qualification_historical_expiry_unknown")
    ttl = certification_ttl_seconds(configured_ttl)
    identity = _identity(row, source)
    values = asdict(identity)
    db.execute("""INSERT OR IGNORE INTO provider_qualification_series
        (tenant_id, id, source, connection_id, connection_fingerprint, model_id,
         execution_shape, contract_version, adapter_contract, protocol_version,
         profile_fingerprint, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (tenant_id, identity.series_id, source, values["connection_id"], values["connection_fingerprint"],
         values["model_id"], values["execution_shape"], values["contract_version"], values["adapter_contract"],
         values["protocol_version"], values["profile_fingerprint"], row["created_at"]),
    )
    db.execute("""INSERT INTO provider_qualification_events
        (tenant_id, source, certification_id, series_id, status, ttl_seconds, created_at)
        VALUES (?, ?, ?, ?, 'running', ?, ?)""",
        (tenant_id, source, certification_id, identity.series_id, ttl, row["created_at"]),
    )
    return read_qualification_event(db, tenant_id, source, certification_id)


def complete_qualification_event(
    db, *, tenant_id, source, certification_id, continuity_blocked: bool,
):
    _transaction(db)
    event = read_qualification_event(db, tenant_id, source, certification_id)
    if event is None:
        return None  # old event or old running certification has no proven TTL
    row = _certification(db, tenant_id, source, certification_id)
    observed_at = row["updated_at"]
    if event["status"] != "running":
        if event["status"] == row["status"] and event["completed_at"] == observed_at:
            return event
        if event["status"] != "uncertain":
            raise ValueError("provider_qualification_event_conflict")
        # Legal GET resolution adds an immutable observation. Its validity
        # starts NOW, never at the original uncertain completed_at timestamp.
        continuity_blocked = True
    if row["status"] not in {"passed", "failed", "uncertain"}:
        raise ValueError("provider_qualification_certification_not_terminal")
    identity = _identity(row, source)
    if identity.series_id != event["series_id"]:
        raise ValueError("provider_qualification_identity_changed")
    # Look at the route's actual preceding event, not only matching series.
    # Otherwise A -> B -> A silently bridges the intervening profile drift.
    # Dedicated and JSON rerank remain independent explicitly selected modes.
    profile_column = "c.profile_json" if source == "provider_workload" else "'{}'"
    candidates = db.execute(f"""SELECT e.*, {profile_column} AS route_profile_json
        FROM provider_qualification_events e
        JOIN provider_qualification_series s ON s.tenant_id=e.tenant_id AND s.id=e.series_id
        JOIN {_TABLES[source]} c ON c.tenant_id=e.tenant_id AND c.id=e.certification_id
        WHERE e.tenant_id=? AND e.source=? AND e.certification_id!=?
          AND s.connection_id=? AND s.model_id=? AND s.execution_shape=?
          AND COALESCE(s.adapter_contract, '')=?
        ORDER BY e.rowid DESC""", (tenant_id, source, certification_id,
            identity.connection_id, identity.model_id, identity.execution_shape,
            identity.adapter_contract or ""))
    previous = None
    selected_mode = None
    if identity.execution_shape == "rerank_documents":
        selected_mode = json.loads(row["profile_json"]).get("rerank_access_mode")
    for candidate in candidates:
        if selected_mode is not None:
            profile = json.loads(candidate["route_profile_json"])
            if profile.get("rerank_access_mode") != selected_mode:
                continue
        previous = candidate
        break
    window = None
    interval = None
    if previous is not None:
        if previous["series_id"] != identity.series_id:
            continuity_blocked = True
        if previous["status"] != "passed":
            continuity_blocked = True
        elif previous["interval_id"]:
            interval = db.execute("""SELECT * FROM provider_qualification_intervals
                WHERE tenant_id = ? AND id = ?""", (tenant_id, previous["interval_id"])).fetchone()
            if interval is None or interval["closed_at"] is not None:
                continuity_blocked = True
            else:
                window = QualificationWindow(
                    series_id=previous["series_id"], interval_id=interval["id"],
                    certification_id=previous["certification_id"],
                    valid_from=datetime.fromisoformat(interval["valid_from"]),
                    completed_at=datetime.fromisoformat(previous["completed_at"]),
                    expires_at=datetime.fromisoformat(previous["expires_at"]),
                )
    decision = decide_renewal(
        identity=identity, certification_id=certification_id, status=row["status"],
        completed_at=datetime.fromisoformat(observed_at), ttl_seconds=event["ttl_seconds"],
        previous=window, continuity_blocked=continuity_blocked,
    )
    if interval is not None and not decision.continuity_preserved:
        db.execute("""UPDATE provider_qualification_intervals
            SET closed_at = ?, closure_reason = ?
            WHERE tenant_id = ? AND id = ? AND closed_at IS NULL""",
            (observed_at, decision.reason_code, tenant_id, interval["id"]),
        )
    new = decision.window
    if new is not None:
        db.execute("""INSERT INTO provider_qualification_intervals
            (tenant_id, id, series_id, valid_from, expires_at, latest_certification_id)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (tenant_id, id) DO UPDATE SET
                expires_at = excluded.expires_at, latest_certification_id = excluded.latest_certification_id""",
            (tenant_id, new.interval_id, new.series_id, new.valid_from.isoformat(), new.expires_at.isoformat(), certification_id),
        )
    db.execute("""UPDATE provider_qualification_events
        SET status = ?, interval_id = ?, completed_at = ?, expires_at = ?, renewal_reason = ?
        WHERE tenant_id = ? AND source = ? AND certification_id = ? AND status IN ('running', 'uncertain')""",
        (row["status"], new.interval_id if new else None, observed_at,
         new.expires_at.isoformat() if new else None, decision.reason_code, tenant_id, source, certification_id),
    )
    db.execute("""INSERT INTO provider_qualification_observations
        (tenant_id, source, certification_id, sequence, status, observed_at, expires_at, interval_id, reason_code)
        SELECT ?, ?, ?, COALESCE(MAX(sequence), 0) + 1, ?, ?, ?, ?, ?
        FROM provider_qualification_observations WHERE tenant_id = ? AND source = ? AND certification_id = ?""",
        (tenant_id, source, certification_id, row["status"], observed_at,
         new.expires_at.isoformat() if new else None, new.interval_id if new else None,
         decision.reason_code, tenant_id, source, certification_id),
    )
    return read_qualification_event(db, tenant_id, source, certification_id)


def invalidate_connection_intervals(db, *, tenant_id, connection_id, observed_at, reason_code):
    _transaction(db)
    if reason_code not in {"provider_qualification_connection_changed", "provider_qualification_hard_failure"}:
        raise ValueError("provider_qualification_reason_invalid")
    # A configuration that changes and is later restored must not resurrect
    # an old interval just because its identity hash happens to match again.
    db.execute("""UPDATE provider_qualification_intervals SET closed_at = ?, closure_reason = ?
        WHERE tenant_id = ? AND closed_at IS NULL AND series_id IN (
            SELECT id FROM provider_qualification_series WHERE tenant_id = ? AND connection_id = ?
        )""", (observed_at, reason_code, tenant_id, tenant_id, connection_id))


def invalidate_certification_intervals(db, *, tenant_id, source, certification_id, observed_at):
    """A persisted hard failure closes this precise series, not other models.

    An old in-flight call may finish after an on-time renewal. Close the series'
    open interval too; checking only the old single certification would miss it.
    This never changes a policy mode or erases failure/approval evidence.
    """
    _transaction(db)
    db.execute("""UPDATE provider_qualification_intervals
        SET closed_at = ?, closure_reason = 'provider_qualification_hard_failure'
        WHERE tenant_id = ? AND closed_at IS NULL AND series_id IN (
            SELECT series_id FROM provider_qualification_events
            WHERE tenant_id = ? AND source = ? AND certification_id = ?
        )""", (observed_at, tenant_id, tenant_id, source, certification_id))


def invalidate_chat_attempt_intervals(db, *, tenant_id, attempt_id, observed_at):
    """R5 attempts have no certification ID; use their persisted exact scope."""
    _transaction(db)
    db.execute("""UPDATE provider_qualification_intervals
        SET closed_at = ?, closure_reason = 'provider_qualification_hard_failure'
        WHERE tenant_id = ? AND closed_at IS NULL AND series_id IN (
            SELECT s.id FROM provider_qualification_series s
            JOIN provider_chat_attempts a ON a.tenant_id = s.tenant_id
                AND a.connection_id = s.connection_id AND a.capability = s.execution_shape
            JOIN provider_chat_runs r ON r.tenant_id = a.tenant_id AND r.id = a.run_id
                AND r.requested_model = s.model_id
            WHERE s.tenant_id = ? AND s.source = 'provider_chat'
                AND a.id = ? AND a.dispatched = 1
        )""", (observed_at, tenant_id, tenant_id, attempt_id))
