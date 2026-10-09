"""Pure historical continuity checks, not an evidence trust or import boundary.

The offline importer must first authenticate provenance, match immutable source
records and review the dry-run. Hashes alone do not prove that a deployment
actually used a TTL. This module never grants live qualification or writes data.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .qualifications import MAX_QUALIFICATION_TTL_SECONDS, MIN_QUALIFICATION_TTL_SECONDS


_HARD_CODES = frozenset({
    "provider_chat_http_401", "provider_chat_http_402", "provider_chat_http_403",
    "provider_chat_http_404", "provider_chat_invalid_sse", "provider_chat_empty_stream",
    "provider_chat_missing_terminal", "provider_chat_model_mismatch",
    "provider_chat_actual_model_mismatch",
})


class HistoricalProofError(ValueError):
    """Stable, content-free diagnostic; never attach the supplied evidence."""


def _instant(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise HistoricalProofError("provider_qualification_history_time_invalid")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class ProvenCertification:
    """Input only after source-row and deployment-provenance verification.

    completed_at and identity come from the original certification. saved TTL
    comes from its original deployment evidence, never the current environment.
    identity_digest covers the exact connection/credential/model/shape/Adapter/
    protocol/Profile tuple, excluding individual certification IDs.
    """

    certification_id: str
    identity_digest: str
    completed_at: datetime
    original_ttl_seconds: int
    status: str


@dataclass(frozen=True, slots=True)
class ProvenContinuity:
    identity_digest: str
    valid_from: datetime
    expires_at: datetime
    observed_until: datetime
    certification_ids: tuple[str, ...]

    def covers(self, started_at: datetime, completed_at: datetime) -> bool:
        start, end = _instant(started_at), _instant(completed_at)
        return self.valid_from <= start <= end <= self.observed_until and end < self.expires_at


def verify_continuity(
    certifications: tuple[ProvenCertification, ...],
    *,
    expected_identity_digest: str,
    observed_until: datetime,
    hard_failure_times: tuple[datetime, ...] = (),
    drift_times: tuple[datetime, ...] = (),
) -> ProvenContinuity:
    """Verify one entire witnessed interval without filtering inconvenient facts.

    The caller supplies ALL intervening certification outcomes, hard failures,
    and drift observations; selecting only successes is not valid evidence.
    Any gap ends this candidate interval. A later interval must be audited as
    a separate proposal, never joined to this one to satisfy the 14-day gate.
    """
    if not certifications or len(certifications) > 10000:
        raise HistoricalProofError("provider_qualification_history_chain_invalid")
    if not expected_identity_digest:
        raise HistoricalProofError("provider_qualification_history_identity_missing")
    until = _instant(observed_until)
    seen: set[str] = set()
    start = expiry = previous_completed = None
    for event in certifications:
        if not event.certification_id or event.certification_id in seen:
            raise HistoricalProofError("provider_qualification_history_duplicate_event")
        seen.add(event.certification_id)
        if event.identity_digest != expected_identity_digest:
            raise HistoricalProofError("provider_qualification_history_identity_changed")
        if event.status != "passed":
            raise HistoricalProofError("provider_qualification_history_failed_event")
        ttl = event.original_ttl_seconds
        if type(ttl) is not int or not MIN_QUALIFICATION_TTL_SECONDS <= ttl <= MAX_QUALIFICATION_TTL_SECONDS:
            raise HistoricalProofError("provider_qualification_history_ttl_unproven")
        completed = _instant(event.completed_at)
        if completed > until or (previous_completed is not None and completed < previous_completed):
            raise HistoricalProofError("provider_qualification_history_event_order_invalid")
        if expiry is not None and completed >= expiry:
            raise HistoricalProofError("provider_qualification_history_validity_gap")
        if start is None:
            start = completed
        # A shorter explicitly configured renewal is not extended by an older
        # certificate's TTL. It must match the runtime's saved-expiry semantics.
        expiry = completed + timedelta(seconds=ttl)
        previous_completed = completed
    assert start is not None and expiry is not None
    if until >= expiry:
        raise HistoricalProofError("provider_qualification_history_validity_gap")
    for instant in hard_failure_times:
        if start <= _instant(instant) <= until:
            raise HistoricalProofError("provider_qualification_historical_hard_failure")
    for instant in drift_times:
        if start <= _instant(instant) <= until:
            raise HistoricalProofError("provider_qualification_history_identity_changed")
    return ProvenContinuity(expected_identity_digest, start, expiry, until,
                            tuple(event.certification_id for event in certifications))


def audit_sample(
    run: dict, attempts: tuple[dict, ...], *, tenant_id: str,
    source_policy_fingerprint: str, stable_model_ids: frozenset[str],
    primary_connection_id: str, continuity: ProvenContinuity,
) -> dict:
    """Classify one original row; never turn a failed sample into a success.

    Inputs are selected by the offline repository, not accepted from an API.
    This result is only a dry-run proposal until provenance, all epoch facts,
    and the target policy are validated and the operator approves its digest.
    """
    reasons: list[str] = []
    if run.get("tenant_id") != tenant_id or any(
        attempt.get("tenant_id") != tenant_id or attempt.get("run_id") != run.get("id")
        for attempt in attempts
    ):
        raise HistoricalProofError("provider_qualification_history_sample_scope_invalid")
    if run.get("policy_fingerprint") != source_policy_fingerprint:
        reasons.append("provider_qualification_history_policy_mismatch")
    if (run.get("capability") != "chat_text" or run.get("gateway") != "default"
            or run.get("is_real_user") != 1 or run.get("primary_newapi") != 1
            or run.get("client_cancelled") != 0):
        reasons.append("provider_qualification_history_sample_not_eligible")
    if run.get("requested_model") not in stable_model_ids:
        reasons.append("provider_qualification_history_model_not_allowed")
    if run.get("status") not in {"succeeded", "failed"}:
        reasons.append("provider_qualification_historical_result_unresolved")
    dispatched = [attempt for attempt in attempts if attempt.get("dispatched") == 1]
    if (len(dispatched) != 1 or dispatched[0].get("position") != 0
            or dispatched[0].get("connection_id") != primary_connection_id
            or dispatched[0].get("provider_kind") != "newapi"
            or dispatched[0].get("capability") != "chat_text"):
        reasons.append("provider_qualification_history_dispatch_mismatch")
    if run.get("hard_failure") != 0 or any(
        attempt.get("result_class") == "hard_failure" or attempt.get("error_code") in _HARD_CODES
        for attempt in dispatched
    ):
        reasons.append("provider_qualification_historical_hard_failure")
    success = run.get("status") == "succeeded" and run.get("result_class") == "success"
    if run.get("status") == "succeeded" and not success:
        reasons.append("provider_qualification_history_result_inconsistent")
    if run.get("status") == "failed" and run.get("result_class") not in {
        "transient_failure", "request_failure",
    }:
        reasons.append("provider_qualification_history_failure_unclassified")
    for record in (run, *dispatched):
        if ((success and record.get("actual_model") != run.get("requested_model"))
                or (record.get("actual_model") is not None
                    and record["actual_model"] != run.get("requested_model"))):
            reasons.append("provider_qualification_history_actual_model_mismatch")
    if len(dispatched) == 1 and (
        dispatched[0].get("status") != run.get("status")
        or dispatched[0].get("result_class") != run.get("result_class")
    ):
        reasons.append("provider_qualification_history_result_inconsistent")
    try:
        start = _instant(datetime.fromisoformat(run["created_at"]))
        end = _instant(datetime.fromisoformat(run["completed_at"]))
        for record in (run, *dispatched):
            if not start <= _instant(datetime.fromisoformat(record["created_at"])) <= \
                    _instant(datetime.fromisoformat(record["completed_at"])) <= end:
                reasons.append("provider_qualification_history_attempt_outside_run")
            if not continuity.covers(datetime.fromisoformat(record["created_at"]),
                                     datetime.fromisoformat(record["completed_at"])):
                reasons.append("provider_qualification_history_sample_outside_proof")
    except (ValueError, TypeError, KeyError):
        reasons.append("provider_qualification_history_time_invalid")
    return {
        "source_run_id": run.get("id"), "eligible": not reasons,
        "success": success and not reasons,
        "reason_codes": sorted(set(reasons)),
    }
