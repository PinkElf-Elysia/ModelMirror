"""Synthetic proofs only; this does not attest any real deployment's history."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from server.model_router.qualification_history_proof import (
    HistoricalProofError, ProvenCertification, audit_sample, verify_continuity,
)


START = datetime(2026, 8, 1, tzinfo=UTC)
DAY = timedelta(days=1)
FIRST = ProvenCertification("cert-1", "exact-config", START, 86400, "passed")


def verify(events=(FIRST,), **changes):
    return verify_continuity(events, expected_identity_digest="exact-config",
                             observed_until=changes.pop("observed_until", START + DAY / 2),
                             **changes)


def test_positive_daily_renewal_preserves_more_than_fourteen_days():
    events = tuple(replace(FIRST, certification_id=f"cert-{i}",
                           completed_at=START + timedelta(hours=23 * i)) for i in range(17))
    until = events[-1].completed_at + timedelta(hours=1)
    interval = verify(events, observed_until=until)
    assert interval.valid_from == START
    assert (until - interval.valid_from).total_seconds() > 14 * 86400
    assert interval.certification_ids == tuple(event.certification_id for event in events)
    assert interval.covers(START, until)
    assert not interval.covers(START - timedelta(microseconds=1), until)
    assert not interval.covers(START, interval.expires_at)
    assert not interval.covers(START, until + timedelta(microseconds=1))
    assert not interval.covers(until, START)


@pytest.mark.parametrize("status", ["failed", "uncertain", "running"])
def test_intervening_non_success_cannot_be_filtered_out(status):
    with pytest.raises(HistoricalProofError, match="failed_event"):
        verify((FIRST, replace(FIRST, certification_id="cert-2", status=status)))


@pytest.mark.parametrize("offset", [DAY, DAY + timedelta(microseconds=1)])
def test_renewal_at_or_after_expiry_does_not_bridge_gap(offset):
    with pytest.raises(HistoricalProofError, match="validity_gap"):
        verify((FIRST, replace(FIRST, certification_id="cert-2", completed_at=START + offset)),
               observed_until=START + offset)


def test_shorter_renewal_never_inherits_longer_old_expiry():
    shorter = replace(FIRST, certification_id="shorter", completed_at=START + DAY / 4,
                      original_ttl_seconds=300)
    with pytest.raises(HistoricalProofError, match="validity_gap"):
        verify((FIRST, shorter))


@pytest.mark.parametrize("ttl", [None, True, 0, 299, 2592001, 86400.0, "86400"])
def test_unknown_or_unbounded_ttl_is_not_guessed(ttl):
    with pytest.raises(HistoricalProofError, match="ttl_unproven"):
        verify((replace(FIRST, original_ttl_seconds=ttl),))


def test_configuration_a_b_a_cannot_resurrect_old_interval():
    with pytest.raises(HistoricalProofError, match="identity_changed"):
        verify((FIRST, replace(FIRST, certification_id="b", identity_digest="other-config"),
                replace(FIRST, certification_id="a-again")))


@pytest.mark.parametrize("field,reason", [
    ("hard_failure_times", "historical_hard_failure"),
    ("drift_times", "identity_changed"),
])
def test_hard_failure_or_drift_cannot_be_erased_by_renewal(field, reason):
    renewal = replace(FIRST, certification_id="renewal", completed_at=START + DAY / 4)
    with pytest.raises(HistoricalProofError, match=reason):
        verify((FIRST, renewal), **{field: (START + DAY / 8,)})


def test_facts_outside_candidate_interval_do_not_get_inherited():
    window = verify(hard_failure_times=(START - DAY,), drift_times=(START + DAY,))
    assert window.valid_from == START


def test_duplicate_and_reverse_order_rejected():
    with pytest.raises(HistoricalProofError, match="duplicate_event"):
        verify((FIRST, FIRST))
    with pytest.raises(HistoricalProofError, match="event_order_invalid"):
        verify((replace(FIRST, certification_id="later", completed_at=START + DAY / 4), FIRST))


def test_naive_future_and_expired_proof_rejected():
    for timestamp, reason in [(START.replace(tzinfo=None), "time_invalid"),
                               (START + DAY, "event_order_invalid")]:
        with pytest.raises(HistoricalProofError, match=reason):
            verify((replace(FIRST, completed_at=timestamp),))
    with pytest.raises(HistoricalProofError, match="validity_gap"):
        verify(observed_until=START + DAY)


def sample():
    shared = dict(tenant_id="local", capability="chat_text", status="succeeded",
                  result_class="success", actual_model="model", created_at=START.isoformat(),
                  completed_at=(START + timedelta(seconds=10)).isoformat())
    return ({**shared, "id": "run", "policy_fingerprint": "policy", "gateway": "default",
             "is_real_user": 1, "primary_newapi": 1, "client_cancelled": 0,
             "requested_model": "model", "hard_failure": 0},
            {**shared, "run_id": "run", "position": 0, "dispatched": 1,
             "connection_id": "newapi", "provider_kind": "newapi"})


def inspect_sample(run, *attempts):
    return audit_sample(run, attempts, tenant_id="local", source_policy_fingerprint="policy",
                        stable_model_ids=frozenset({"model"}), primary_connection_id="newapi",
                        continuity=verify())


def test_sample_success_and_transient_failure_both_count_without_rewriting():
    run, attempt = sample()
    assert inspect_sample(run, attempt) == {
        "source_run_id": "run", "eligible": True, "success": True, "reason_codes": []}
    for record in (run, attempt):
        record.update(status="failed", result_class="transient_failure", actual_model=None)
    result = inspect_sample(run, attempt)
    assert result["eligible"] and not result["success"]
    assert run["status"] == "failed" and attempt["status"] == "failed"


@pytest.mark.parametrize("field,value", [
    ("gateway", "auto"), ("gateway", "newapi_canary"), ("is_real_user", 0),
    ("client_cancelled", 1), ("primary_newapi", 0), ("capability", "chat_tools"),
    ("status", "uncertain"), ("hard_failure", 1), ("actual_model", "other-model"),
    ("actual_model", None), ("policy_fingerprint", "changed"), ("requested_model", "other"),
    ("completed_at", None), ("completed_at", (START + DAY).isoformat()),
])
def test_ineligible_or_unproven_samples_are_excluded(field, value):
    run, attempt = sample()
    run[field] = value
    result = inspect_sample(run, attempt)
    assert not result["eligible"] and not result["success"] and result["reason_codes"]


def test_second_dispatch_wrong_connection_and_attempt_uncertainty_rejected():
    run, attempt = sample()
    assert not inspect_sample(run, attempt, dict(attempt))["eligible"]
    assert not inspect_sample(run, {**attempt, "connection_id": "fallback"})["eligible"]
    assert not inspect_sample(run, {**attempt, "status": "uncertain"})["eligible"]


def test_sample_tenant_and_run_scope_cannot_be_forged():
    run, attempt = sample()
    for wrong in ({**attempt, "tenant_id": "other"}, {**attempt, "run_id": "other"}):
        with pytest.raises(HistoricalProofError, match="sample_scope_invalid"):
            inspect_sample(run, wrong)


def test_attempt_must_be_inside_parent_run_even_within_qualification():
    run, attempt = sample()
    attempt["completed_at"] = (START + timedelta(seconds=11)).isoformat()
    assert "provider_qualification_history_attempt_outside_run" in inspect_sample(run, attempt)["reason_codes"]


@pytest.mark.parametrize("code", ["provider_chat_http_401", "provider_chat_http_403",
                                  "provider_chat_empty_stream", "provider_chat_missing_terminal"])
def test_hard_code_cannot_be_hidden_by_incorrect_legacy_classification(code):
    run, attempt = sample()
    for record in (run, attempt):
        record.update(status="failed", result_class="transient_failure", actual_model=None)
    attempt["error_code"] = code
    result = inspect_sample(run, attempt)
    assert not result["eligible"]
    assert "provider_qualification_historical_hard_failure" in result["reason_codes"]
