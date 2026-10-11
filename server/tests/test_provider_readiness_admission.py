"""R9B4: persisted admission facts, not discovery, determine readiness."""
from __future__ import annotations

import sqlite3
from unittest.mock import Mock

import pytest

from server.tests.test_provider_chat_stable_service import MODEL_ID, _service
from server.tests.test_provider_chat_stable_service import _activate_required_for_test
from server.model_router.schemas import RouterConnectionUpdate
from server.model_router.schemas import (
    ProviderWorkloadActivationRequest, ProviderWorkloadBindingUpdate,
    ProviderWorkloadPolicyUpdate,
)
from server.model_router.service import RouterServiceError
from server.model_router.workload_control import (
    ENTRY_FEATURE_FLAGS, ProviderWorkloadCallService,
)


def test_chat_readiness_has_no_gate_write_or_network(tmp_path, monkeypatch):
    stable, repository, _, _ = _service(tmp_path, monkeypatch)
    before = repository.database_path.read_bytes()
    monkeypatch.setattr(
        repository, "sync_chat_control_gate_epoch",
        Mock(side_effect=AssertionError("readiness wrote gate state")),
    )
    monkeypatch.setattr(
        stable.transport, "authorize_managed_target",
        Mock(side_effect=AssertionError("readiness resolved DNS")),
    )
    assert stable.readiness(MODEL_ID) == (True, None)
    assert stable.control.public_status(MODEL_ID, "chat_text").available
    assert repository.database_path.read_bytes() == before


def test_chat_public_status_agrees_with_required_gate(tmp_path, monkeypatch):
    stable, _, _, _ = _service(tmp_path, monkeypatch)
    policy = stable.control.get_policy().model_copy(update={
        "configured_mode": "newapi_required_default",
        "effective_mode": "newapi_required_default",
    })
    monkeypatch.setattr(stable.control, "_policy_response", lambda _: policy)
    monkeypatch.setattr(
        stable.control, "required_runtime_allowed",
        lambda policy=None: (False, "provider_chat_required_gate_degraded"),
    )
    assert stable.readiness(MODEL_ID) == (
        False, "provider_chat_required_gate_degraded",
    )
    public = stable.control.public_status(MODEL_ID, "chat_text")
    assert not public.available
    assert public.would_block
    assert public.reason_code == "provider_chat_required_gate_degraded"


def test_required_readiness_cannot_use_backup_qualification(tmp_path, monkeypatch):
    stable, _, primary, _ = _service(tmp_path, monkeypatch)
    policy = stable.control.get_policy()
    policy = policy.model_copy(update={
        "configured_mode": "newapi_required_default",
        "effective_mode": "newapi_required_default",
        "qualifications": [
            q.model_copy(update={"valid": False, "reason_code": "provider_chat_certification_expired"})
            if q.connection_id == primary else q
            for q in policy.qualifications
        ],
    })
    monkeypatch.setattr(stable.control, "_policy_response", lambda _: policy)
    monkeypatch.setattr(stable.control, "required_runtime_allowed", lambda policy=None: (True, None))
    ready, reason = stable.readiness(MODEL_ID)
    public = stable.control.public_status(MODEL_ID, "chat_text")
    assert not ready
    assert not public.available
    assert public.would_block
    assert public.reason_code == reason


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [
    "qualified", "flag_off", "legacy", "not_stable", "missing_capability",
    "disabled", "offline", "credential_drift", "expired", "unknown_expiry",
    "required", "required_degraded", "required_missing_qualification",
])
async def test_chat_public_readiness_matches_actual_begin(tmp_path, monkeypatch, state):
    stable, repository, primary, backup = _service(tmp_path, monkeypatch, newapi_ip="8.8.4.4")
    model, capability = MODEL_ID, "chat_text"
    if state == "flag_off":
        monkeypatch.setenv("MODEL_CONTROL_CHAT_ENABLED", "false")
    elif state == "legacy":
        with sqlite3.connect(repository.database_path) as db:
            db.execute("UPDATE provider_chat_stable_policies SET mode='legacy' WHERE tenant_id='local'")
    elif state == "not_stable":
        model = "provider/not-stable"
    elif state == "missing_capability":
        capability = "chat_file_output"
    elif state in {"disabled", "credential_drift"}:
        for connection in (primary, backup):
            repository.update_connection("local", connection, RouterConnectionUpdate(**(
                {"enabled": False} if state == "disabled" else {"api_key": "rotated-synthetic-key"}
            )))
    elif state == "offline":
        for connection in (primary, backup):
            repository.save_test_result("local", connection, health="offline", model_count=1,
                                        checked_at="2026-10-09T00:00:00+00:00")
    elif state in {"expired", "unknown_expiry"}:
        with sqlite3.connect(repository.database_path) as db:
            if state == "expired":
                db.execute("UPDATE provider_qualification_events SET completed_at='2020-01-01T00:00:00+00:00', expires_at='2020-01-02T00:00:00+00:00' WHERE tenant_id='local'")
                db.execute("UPDATE provider_qualification_intervals SET valid_from='2020-01-01T00:00:00+00:00' WHERE tenant_id='local'")
            else:
                db.execute("DELETE FROM provider_qualification_events WHERE tenant_id='local'")
    elif state.startswith("required"):
        _activate_required_for_test(repository)
        if state == "required_degraded":
            with sqlite3.connect(repository.database_path) as db:
                db.execute("UPDATE provider_chat_gate_epochs SET status='degraded' WHERE tenant_id='local'")
        elif state == "required_missing_qualification":
            with sqlite3.connect(repository.database_path) as db:
                db.execute("DELETE FROM provider_chat_model_qualifications WHERE tenant_id='local' AND capability='chat_tools'")

    # Capture the complete logical DB, including WAL-visible writes. Reads must not
    # create/close epochs or alter historical facts, even for stale qualifications.
    with sqlite3.connect(repository.database_path) as db:
        before = list(db.iterdump())
    public = stable.control.public_status(model, capability)
    ready, reason = stable.readiness(model, capability)
    with sqlite3.connect(repository.database_path) as db:
        assert list(db.iterdump()) == before
    assert ready == public.available
    assert reason == (None if public.available else public.reason_code)
    result = await stable.begin(model, capability)
    assert public.available == (result.dispatch is not None)
    assert public.would_block == (result.intercepted and result.dispatch is None)
    if public.would_block:
        assert result.error_code == public.reason_code
    # Preparing a route does not constitute a Provider POST.
    receipts = repository.list_chat_control_receipts("local")
    assert not any(item["dispatched"] for item in receipts["attempts"])


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [
    "qualified", "flag_off", "legacy", "missing_model", "missing_shape",
    "disabled", "credential_drift", "expired", "other_entry",
])
async def test_workload_readiness_matches_prepare_without_side_effects(tmp_path, monkeypatch, state):
    stable, repository, primary, _ = _service(tmp_path, monkeypatch, newapi_ip="8.8.4.4")
    calls = ProviderWorkloadCallService(stable.router_service)
    entry, model, shape = "agent_shadow", MODEL_ID, "chat_tools"
    monkeypatch.setenv(ENTRY_FEATURE_FLAGS[entry][0], "true")
    saved = calls.control.update_policy(entry, ProviderWorkloadPolicyUpdate(
        expected_revision=0, bindings=[ProviderWorkloadBindingUpdate(
            model_id=model, execution_shape=shape, connection_id=primary,
        )],
    ))
    calls.control.activate(entry, ProviderWorkloadActivationRequest(
        expected_revision=saved.revision, no_open_p0_p1=True, acknowledge_fail_closed=True,
    ))
    run_id = calls.start_run(entry)
    if state == "flag_off":
        monkeypatch.setenv(ENTRY_FEATURE_FLAGS[entry][0], "false")
    elif state == "legacy":
        with sqlite3.connect(repository.database_path) as db:
            db.execute("UPDATE provider_workload_policies SET status='legacy' WHERE tenant_id='local'")
    elif state == "missing_model":
        model = "provider/other-model"
    elif state == "missing_shape":
        shape = "chat_text"
    elif state == "other_entry":
        entry = "workflow_interactive_agent"
        monkeypatch.setenv(ENTRY_FEATURE_FLAGS[entry][0], "true")
    elif state in {"disabled", "credential_drift"}:
        repository.update_connection("local", primary, RouterConnectionUpdate(**(
            {"enabled": False} if state == "disabled" else {"api_key": "synthetic-rotated-key"}
        )))
    elif state == "expired":
        with sqlite3.connect(repository.database_path) as db:
            db.execute("UPDATE provider_qualification_events SET completed_at='2020-01-01T00:00:00+00:00', expires_at='2020-01-02T00:00:00+00:00' WHERE tenant_id='local'")
            db.execute("UPDATE provider_qualification_intervals SET valid_from='2020-01-01T00:00:00+00:00' WHERE tenant_id='local'")

    with sqlite3.connect(repository.database_path) as db:
        before = list(db.iterdump())
    public = calls.control.public_status(entry, model, shape)
    assert public.blocks_before_dispatch == (state not in {"qualified", "flag_off", "legacy", "other_entry"})
    with sqlite3.connect(repository.database_path) as db:
        assert list(db.iterdump()) == before
    prepare = dict(run_id=run_id, entry_id=entry, model_id=model, execution_shape=shape,
                   logical_call_key="readiness-check", call_sequence=1)
    if public.available:
        prepared = await calls.prepare_call(**prepare)
        assert prepared.connection_id == primary
    else:
        with pytest.raises(RouterServiceError) as caught:
            await calls.prepare_call(**prepare)
        assert caught.value.code == public.reason_code
    assert not any(item["dispatched"] for item in repository.list_workload_receipts("local")["calls"])
