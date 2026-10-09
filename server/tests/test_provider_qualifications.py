from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from server.model_router.qualifications import (
    QualificationIdentity,
    QualificationWindow,
    certification_ttl_seconds,
    decide_renewal,
    historical_expiry,
    qualification_admin_summary,
)


START = datetime(2026, 10, 1, tzinfo=UTC)


def test_new_deployment_defaults_match_frozen_thirty_day_contract():
    root = Path(__file__).resolve().parents[2]
    variable = "MODEL_MIRROR_PROVIDER_CHAT_CERTIFICATION_MAX_AGE_SECONDS"
    for name in (".env.example", "server/.env.example"):
        assert f"{variable}=2592000" in (root / name).read_text(encoding="utf-8").splitlines()
    assert f"${{{variable}:-2592000}}" in (root / "docker-compose.yml").read_text(encoding="utf-8")
    assert certification_ttl_seconds(None) == 2592000
    assert certification_ttl_seconds("86400") == 86400  # existing explicit shorter policy remains supported


def test_admin_projection_allowlist_and_unknown_historical_expiry():
    summary = qualification_admin_summary(None, now=START)
    assert summary["valid"] is False
    assert summary["reason_code"] == "provider_chat_certification_expiry_unknown"
    contaminated = qualification_admin_summary({"status": "failed", "tenant_id": "private", "api_key": "secret", "prompt": "private"}, now=START)
    assert set(contaminated) == {"series_id", "interval_id", "expires_at", "renewal_reason", "valid", "reason_code"}


IDENTITY = QualificationIdentity(
    tenant_id="local",
    source="provider_workload",
    connection_id="synthetic-connection",
    connection_fingerprint="a" * 64,
    model_id="synthetic/model",
    execution_shape="chat_json_object",
    contract_version="modelmirror-provider-workload-routing-v1",
    adapter_contract=None,
    protocol_version=None,
    profile_fingerprint="b" * 64,
)


def first_window(**overrides):
    args = dict(
        identity=IDENTITY, certification_id="cert-1", status="passed",
        completed_at=START, ttl_seconds=86400,
    )
    args.update(overrides)
    decision = decide_renewal(**args)
    assert decision.window is not None
    return decision.window


@pytest.mark.parametrize("raw,expected", [(None, 2592000), ("", 2592000), ("300", 300), ("86400", 86400), ("2592000", 2592000)])
def test_new_certifications_default_to_thirty_days_and_preserve_shorter_ttl(raw, expected):
    assert certification_ttl_seconds(raw) == expected


@pytest.mark.parametrize("raw", ["0", "299", "2592001", "invalid", "nan", "86400.0"])
def test_invalid_ttl_never_silently_uses_default(raw):
    with pytest.raises(ValueError, match="provider_qualification_ttl_invalid"):
        certification_ttl_seconds(raw)


def test_qualification_identity_has_no_individual_certification_id():
    previous = first_window()
    renewed = decide_renewal(
        identity=IDENTITY, certification_id="cert-2", status="passed",
        completed_at=START + timedelta(hours=23), ttl_seconds=2592000,
        previous=previous,
    )
    assert renewed.reason_code == "provider_qualification_renewed"
    assert renewed.continuity_preserved is True
    assert renewed.window.series_id == previous.series_id
    assert renewed.window.interval_id == previous.interval_id
    assert renewed.window.certification_id == "cert-2"
    assert renewed.window.valid_from == START
    assert renewed.window.expires_at == START + timedelta(hours=23, days=30)
    assert previous.certification_id == "cert-1"
    assert previous.expires_at == START + timedelta(days=1)


@pytest.mark.parametrize("offset", [timedelta(days=1), timedelta(days=1, microseconds=1), timedelta(days=2)])
def test_expiry_boundary_or_gap_never_fills_old_interval(offset):
    previous = first_window()
    decision = decide_renewal(
        identity=IDENTITY, certification_id="cert-2", status="passed",
        completed_at=START + offset, ttl_seconds=2592000, previous=previous,
    )
    assert not decision.continuity_preserved
    assert decision.reason_code == "provider_qualification_validity_gap"
    assert decision.window.series_id == previous.series_id
    assert decision.window.interval_id != previous.interval_id
    assert decision.window.valid_from == START + offset


@pytest.mark.parametrize("field,value", [
    ("tenant_id", "other"), ("source", "provider_chat"),
    ("connection_id", "other"), ("connection_fingerprint", "c" * 64),
    ("model_id", "other/model"), ("execution_shape", "chat_text_unary"),
    ("contract_version", "v2"), ("adapter_contract", "adapter-v2"),
    ("protocol_version", "v2"), ("profile_fingerprint", "d" * 64),
])
def test_each_identity_dimension_isolated(field, value):
    previous = first_window()
    identity = replace(IDENTITY, **{field: value})
    decision = decide_renewal(
        identity=identity, certification_id="cert-2", status="passed",
        completed_at=START + timedelta(hours=1), ttl_seconds=86400,
        previous=previous,
    )
    assert not decision.continuity_preserved
    assert decision.reason_code == "provider_qualification_identity_changed"
    assert decision.window.series_id != previous.series_id
    assert decision.window.interval_id != previous.interval_id


@pytest.mark.parametrize("status", ["failed", "uncertain", "running"])
def test_non_passed_event_cannot_grant_or_extend_qualification(status):
    previous = first_window()
    decision = decide_renewal(
        identity=IDENTITY, certification_id="cert-2", status=status,
        completed_at=START + timedelta(hours=1), ttl_seconds=86400,
        previous=previous,
    )
    assert decision.window is None
    assert not decision.continuity_preserved
    assert decision.reason_code == "provider_qualification_certification_not_passed"


def test_passed_recertification_after_hard_failure_cannot_inherit_approval():
    previous = first_window()
    decision = decide_renewal(
        identity=IDENTITY, certification_id="cert-2", status="passed",
        completed_at=START + timedelta(hours=1), ttl_seconds=86400,
        previous=previous, continuity_blocked=True,
    )
    assert decision.window is not None
    assert not decision.continuity_preserved
    assert decision.reason_code == "provider_qualification_continuity_blocked"
    assert decision.window.interval_id != previous.interval_id


def test_historical_expiry_requires_evidence_not_current_environment(monkeypatch):
    monkeypatch.setenv("MODEL_MIRROR_PROVIDER_CHAT_CERTIFICATION_MAX_AGE_SECONDS", "2592000")
    assert historical_expiry(completed_at=START, proven_ttl_seconds=None) is None
    assert historical_expiry(completed_at=START, proven_ttl_seconds=300) == START + timedelta(seconds=300)
    assert historical_expiry(completed_at=START, proven_ttl_seconds=86400) == START + timedelta(days=1)


def test_window_is_valid_only_within_half_open_interval():
    window = first_window()
    assert not window.contains(START - timedelta(microseconds=1))
    assert window.contains(START)
    assert window.contains(START + timedelta(hours=23))
    assert not window.contains(START + timedelta(days=1))


def test_out_of_order_completion_cannot_bridge_history():
    previous = first_window()
    with pytest.raises(ValueError, match="provider_qualification_time_invalid"):
        decide_renewal(
            identity=IDENTITY, certification_id="cert-2", status="passed",
            completed_at=START - timedelta(seconds=1), ttl_seconds=86400,
            previous=previous,
        )


def test_naive_timestamp_is_not_assumed_to_be_utc():
    with pytest.raises(ValueError, match="provider_qualification_time_invalid"):
        first_window(completed_at=START.replace(tzinfo=None))


@pytest.mark.parametrize("ttl", [True, 300.0, 299, 2592001])
def test_internal_ttl_is_strict_integer(ttl):
    with pytest.raises(ValueError, match="provider_qualification_ttl_invalid"):
        first_window(ttl_seconds=ttl)


def test_window_rejects_forged_or_empty_time_range():
    with pytest.raises(ValueError, match="provider_qualification_time_invalid"):
        QualificationWindow(
            series_id=IDENTITY.series_id, interval_id="interval", certification_id="cert",
            valid_from=START, completed_at=START, expires_at=START,
        )


def test_same_event_cannot_be_reissued_with_later_expiry():
    with pytest.raises(ValueError, match="provider_qualification_event_already_recorded"):
        decide_renewal(
            identity=IDENTITY, certification_id="cert-1", status="passed",
            completed_at=START + timedelta(hours=1), ttl_seconds=2592000,
            previous=first_window(),
        )


def test_current_ttl_change_does_not_reinterpret_saved_window(monkeypatch):
    window = first_window(ttl_seconds=300)
    monkeypatch.setenv("MODEL_MIRROR_PROVIDER_CHAT_CERTIFICATION_MAX_AGE_SECONDS", "2592000")
    assert not window.contains(START + timedelta(minutes=5))
    assert window.expires_at == START + timedelta(minutes=5)


def test_shorter_renewal_remains_shorter():
    decision = decide_renewal(
        identity=IDENTITY, certification_id="cert-2", status="passed",
        completed_at=START + timedelta(hours=1), ttl_seconds=300,
        previous=first_window(ttl_seconds=2592000),
    )
    assert decision.continuity_preserved
    assert decision.window.expires_at == START + timedelta(hours=1, minutes=5)


def test_latest_completion_orders_multiple_renewals_not_series_start():
    previous = decide_renewal(
        identity=IDENTITY, certification_id="cert-2", status="passed",
        completed_at=START + timedelta(hours=2), ttl_seconds=86400,
        previous=first_window(),
    ).window
    with pytest.raises(ValueError, match="provider_qualification_time_invalid"):
        decide_renewal(
            identity=IDENTITY, certification_id="cert-3", status="passed",
            completed_at=START + timedelta(hours=1), ttl_seconds=86400,
            previous=previous,
        )
