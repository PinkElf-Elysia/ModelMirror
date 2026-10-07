import asyncio
from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

from server.meta_agent.headless_authoring import HeadlessAuthoringError
from server.meta_agent.model_repair import ExplicitModelRepairService, ModelRepairExecuteRequest, ModelRepairPreflightRequest
from server.tests.test_meta_planner_failed_recovery import REPAIR, setup_recovery
from server.tests.test_meta_planner_failed_artifacts import offline_only
from server.xpert_runtime.authoring_store import AuthoringProposalStore


ROUTE = {"mode": "legacy", "label": "离线模拟网关", "fingerprint": "a" * 64}


async def setup(tmp_path, monkeypatch, response=None):
    headless, proposal, authoring, graph = await setup_recovery(tmp_path, monkeypatch)
    calls = []

    async def complete(*args):
        calls.append(args)
        return {"text": json.dumps({"operations": REPAIR}) if response is None else response,
                "receipt": {"provider_dispatched": True, "response_received": True, "total_tokens": 120}}

    service = ExplicitModelRepairService(headless, route_projection=lambda _: deepcopy(ROUTE), completion=complete)
    state = headless.proposal_state(proposal.proposal_id)
    request = ModelRepairPreflightRequest(
        proposal_revision=1, artifact_checksum=state["recovery"]["artifact_checksum"],
        expected_graph_checksum=state["graph_checksum"], expected_candidate_checksum=state["candidate_checksum"],
        model_id="test/repair", focus="补齐可信记录来源，不改变业务语义。",
    )
    return service, proposal, authoring, request, calls


def consent(request, preflight, request_id="repair_" + "a" * 32, **extra):
    return ModelRepairExecuteRequest(**request.model_dump(), request_id=request_id,
        authorization_checksum=preflight["authorization_checksum"], authorization_token=preflight["authorization_token"], acknowledge_external_send=True, **extra)


@pytest.mark.asyncio
async def test_preflight_is_pure_and_one_call_only_suggests(tmp_path, monkeypatch):
    service, proposal, authoring, request, calls = await setup(tmp_path, monkeypatch)
    before = authoring.proposal_store.require(proposal.proposal_id)
    preflight = service.preflight(proposal.proposal_id, request)
    other = service.preflight(proposal.proposal_id, request)
    assert preflight["authorization_checksum"] == other["authorization_checksum"]
    assert preflight["authorization_token"] != other["authorization_token"]
    assert preflight["max_calls"] == 1 and preflight["outbound"]["messages"][1]["content"]
    assert calls == [] and authoring.proposal_store.require(proposal.proposal_id) == before
    approved = consent(request, preflight)
    result = await service.execute(proposal.proposal_id, approved)
    assert result["status"] == "suggested", result
    assert result["preview_summary"]["can_apply"] and not result["automatically_applied"]
    assert result["receipt"]["total_tokens"] == 120 and len(calls) == 1
    current = authoring.proposal_store.require(proposal.proposal_id)
    assert current.payload == before.payload and current.revision == 1
    assert current.payload_digest == before.payload_digest and current.apply_key == before.apply_key
    assert authoring.xpert_store.list_xperts() == []
    assert "meta_planner_model_repairs" not in authoring.proposal_store.serialize(current, include_payload=True)
    assert "patch" not in service.history(proposal.proposal_id)["attempts"][0]
    assert await service.execute(proposal.proposal_id, approved) == result
    assert len(calls) == 1
    with pytest.raises(HeadlessAuthoringError):
        await service.execute(proposal.proposal_id, approved.model_copy(update={"focus": "扩大目标"}))
    assert len(calls) == 1


@pytest.mark.parametrize("field,value", [("acknowledge_external_send", False), ("acknowledge_external_send", "true"), ("max_calls", 2), ("max_calls", True), ("max_output_tokens", 16001)])
def test_consent_and_budget_strict(field, value):
    payload = dict(proposal_revision=1, artifact_checksum="a"*64, expected_graph_checksum="b"*64,
        expected_candidate_checksum="c"*64, model_id="test/repair", request_id="repair_"+"a"*32,
        authorization_checksum="d"*64, authorization_token="a"*32+".9999999999."+"b"*64, acknowledge_external_send=True)
    payload[field] = value
    with pytest.raises(ValidationError):
        ModelRepairExecuteRequest.model_validate(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["revision", "scope", "route", "model", "budget", "focus"])
async def test_confirmed_basis_drift_blocks_before_call(tmp_path, monkeypatch, drift):
    service, proposal, authoring, request, calls = await setup(tmp_path, monkeypatch)
    approved = consent(request, service.preflight(proposal.proposal_id, request))
    if drift == "revision":
        authoring.update_pending(proposal.proposal_id, revision=1, title="另一窗口修改")
    elif drift == "scope":
        authoring.proposal_store._items[proposal.proposal_id].payload["meta_planner_report"]["authorized_scope"]["data_table_write_grants"] = []
    elif drift == "route":
        service.route_projection = lambda _: {**ROUTE, "fingerprint": "b"*64}
    else:
        approved = approved.model_copy(update={"model_id" if drift == "model" else "max_output_tokens" if drift == "budget" else "focus":
                                               "different/model" if drift == "model" else 8000 if drift == "budget" else "新要求"})
    with pytest.raises(HeadlessAuthoringError):
        await service.execute(proposal.proposal_id, approved)
    assert calls == []


@pytest.mark.asyncio
async def test_concurrent_submit_and_restart_never_redispatch(tmp_path, monkeypatch):
    service, proposal, authoring, request, calls = await setup(tmp_path, monkeypatch)
    approved = consent(request, service.preflight(proposal.proposal_id, request))
    entered, release = asyncio.Event(), asyncio.Event()

    async def pending(*args):
        calls.append(args)
        entered.set()
        await release.wait()
        raise asyncio.CancelledError()

    service.completion = pending
    task = asyncio.create_task(service.execute(proposal.proposal_id, approved))
    await entered.wait()
    assert (await service.execute(proposal.proposal_id, approved))["status"] == "dispatching"
    with pytest.raises(HeadlessAuthoringError):
        await service.execute(proposal.proposal_id, approved.model_copy(update={"request_id": "repair_"+"b"*32}))
    restarted = AuthoringProposalStore(authoring.proposal_store.storage_dir)
    assert restarted.require(proposal.proposal_id).meta_planner_model_repairs[0]["status"] == "uncertain"
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    service.store = restarted
    service.recovery.headless.authoring_service.proposal_store = restarted
    assert (await service.execute(proposal.proposal_id, approved))["status"] == "uncertain"
    assert len(calls) == 1
    fresh = service.preflight(proposal.proposal_id, request)
    assert fresh["uncertain_previous"] == 1
    with pytest.raises(HeadlessAuthoringError):
        await service.execute(proposal.proposal_id, consent(request, fresh, "repair_"+"c"*32))
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("response", ["not json", '{"operations":[],"model_id":"forged"}', json.dumps({"operations": [{"op": "update_node", "ref": "write", "config": {"tableId": "other"}}]}), json.dumps({"operations": []})])
async def test_invalid_or_injected_suggestion_never_applies(tmp_path, monkeypatch, response):
    service, proposal, authoring, request, calls = await setup(tmp_path, monkeypatch, response)
    result = await service.execute(proposal.proposal_id, consent(request, service.preflight(proposal.proposal_id, request)))
    assert result["status"] == "invalid", result
    assert len(calls) == 1 and authoring.proposal_store.require(proposal.proposal_id).revision == 1
    assert not result.get("preview_summary", {}).get("can_apply")


@pytest.mark.asyncio
async def test_late_result_never_overwrites_human_edit(tmp_path, monkeypatch):
    service, proposal, authoring, request, calls = await setup(tmp_path, monkeypatch)
    approved = consent(request, service.preflight(proposal.proposal_id, request))
    original = service.completion

    async def late(*args):
        authoring.update_pending(proposal.proposal_id, revision=1, title="用户最新标题")
        return await original(*args)

    service.completion = late
    result = await service.execute(proposal.proposal_id, approved)
    assert result["status"] == "stale" and "patch" not in result
    assert authoring.proposal_store.require(proposal.proposal_id).title == "用户最新标题"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_suggested_receipt_is_not_actionable_after_a_later_edit(tmp_path, monkeypatch):
    service, proposal, authoring, request, calls = await setup(tmp_path, monkeypatch)
    approved = consent(request, service.preflight(proposal.proposal_id, request))
    assert (await service.execute(proposal.proposal_id, approved))["status"] == "suggested"
    authoring.update_pending(proposal.proposal_id, revision=1, title="建议返回后人工修改")
    result = service.result(proposal.proposal_id, approved.request_id)
    assert result["status"] == "stale" and result["stale"] is True
    assert "patch" not in result and "preview_summary" not in result
    assert len(calls) == 1 and service.history(proposal.proposal_id)["attempts"][0]["status"] == "suggested"


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["revision", "snapshot"])
async def test_transport_guard_rechecks_confirmed_basis_before_dispatch(tmp_path, monkeypatch, drift):
    from types import SimpleNamespace
    from server.meta_agent.model_repair import repair_request_body
    from server.meta_agent.model_repair_transport import ModelRepairTransport

    service, proposal, authoring, request, _ = await setup(tmp_path, monkeypatch)
    dispatched = []

    async def legacy(model, system, prompt, max_tokens, receipt, guard, config):
        if drift == "revision":
            authoring.update_pending(proposal.proposal_id, revision=1, title="派发前人工修改")
        else:
            builder = service.recovery.headless.capability_snapshot_builder
            changed = builder().model_copy(deep=True)
            changed.data_tables[0]["schema_versions"][0]["checksum"] = "b" * 64
            service.recovery.headless.capability_snapshot_builder = lambda: changed
        guard(repair_request_body(model, system, prompt, max_tokens, "legacy"))
        dispatched.append(model)
        return json.dumps({"operations": REPAIR})

    transport = ModelRepairTransport(SimpleNamespace(routing_mode=lambda: "legacy"),
        legacy_config=lambda: ("https://test.invalid/v1/chat/completions", "synthetic"), legacy_completion=legacy)
    service.route_projection = transport.route_projection
    service.completion = transport.complete
    approved = consent(request, service.preflight(proposal.proposal_id, request))
    result = await service.execute(proposal.proposal_id, approved)
    assert not dispatched
    assert result["status"] == "stale" and "patch" not in result
    assert result["receipt"]["provider_dispatched"] is False
    assert not result["receipt"]["response_received"]
    assert await service.execute(proposal.proposal_id, approved) == result


@pytest.mark.asyncio
async def test_failed_claim_persistence_does_not_call_or_leave_reservation(tmp_path, monkeypatch):
    service, proposal, authoring, request, calls = await setup(tmp_path, monkeypatch)
    approved = consent(request, service.preflight(proposal.proposal_id, request))
    monkeypatch.setattr(service.store, "_save_model_repairs_unlocked", lambda _: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        await service.execute(proposal.proposal_id, approved)
    assert not calls and service.store.require(proposal.proposal_id).meta_planner_model_repairs == []


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["forged", "expired", "restart", "reused_slot"])
async def test_confirmation_ticket_cannot_be_forged_or_reused(tmp_path, monkeypatch, case):
    import server.meta_agent.model_repair as module
    service, proposal, _, request, calls = await setup(tmp_path, monkeypatch)
    approved = consent(request, service.preflight(proposal.proposal_id, request))
    if case == "forged":
        approved = approved.model_copy(update={"authorization_token": "a"*32+".9999999999."+"b"*64})
    elif case == "expired":
        approved = approved.model_copy(update={"authorization_token": module._ticket(approved.authorization_checksum, expires=1000000000)})
    elif case == "restart":
        monkeypatch.setattr(module, "_CONFIRMATION_KEY", b"another-process-key")
    else:
        assert (await service.execute(proposal.proposal_id, approved))["status"] == "suggested"
        approved = approved.model_copy(update={"request_id": "repair_"+"d"*32})
    with pytest.raises(HeadlessAuthoringError):
        await service.execute(proposal.proposal_id, approved)
    assert len(calls) == (1 if case == "reused_slot" else 0)


@pytest.mark.asyncio
async def test_lost_receipt_save_cannot_lead_to_automatic_second_call(tmp_path, monkeypatch):
    service, proposal, authoring, request, calls = await setup(tmp_path, monkeypatch)
    approved = consent(request, service.preflight(proposal.proposal_id, request))
    original = service.completion
    save = service.store._save_model_repairs_unlocked

    async def response_then_disk_failure(*args):
        result = await original(*args)
        monkeypatch.setattr(service.store, "_save_model_repairs_unlocked", lambda _: (_ for _ in ()).throw(OSError("disk full")))
        return result

    service.completion = response_then_disk_failure
    with pytest.raises(OSError):
        await service.execute(proposal.proposal_id, approved)
    monkeypatch.setattr(service.store, "_save_model_repairs_unlocked", save)
    assert (await service.execute(proposal.proposal_id, approved))["status"] == "dispatching"
    restarted = AuthoringProposalStore(service.store.storage_dir)
    service.store = restarted
    authoring.proposal_store = restarted
    assert (await service.execute(proposal.proposal_id, approved))["status"] == "uncertain"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_repair_receipts_do_not_change_legacy_proposal_record_format(tmp_path, monkeypatch):
    service, proposal, _, request, _ = await setup(tmp_path, monkeypatch)
    before = json.loads(service.store.snapshot_path.read_text(encoding="utf-8"))
    assert all("meta_planner_model_repairs" not in item for item in before["items"])
    approved = consent(request, service.preflight(proposal.proposal_id, request))
    result = await service.execute(proposal.proposal_id, approved)
    assert json.loads(service.store.snapshot_path.read_text(encoding="utf-8")) == before
    restarted = AuthoringProposalStore(service.store.storage_dir)
    assert restarted.require(proposal.proposal_id).meta_planner_model_repairs[0]["status"] == result["status"]


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["checksum", "foreign_proposal", "invalid_json"])
async def test_corrupt_private_journal_blocks_calls_but_keeps_proposal_readable(tmp_path, monkeypatch, damage):
    service, proposal, authoring, request, calls = await setup(tmp_path, monkeypatch)
    before_ids = {item.proposal_id for item in service.store.list()}
    approved = consent(request, service.preflight(proposal.proposal_id, request))
    await service.execute(proposal.proposal_id, approved)
    path = service.store._repair_path(proposal.proposal_id)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if damage == "checksum":
        raw["attempts"][0]["status"] = "uncertain"
    elif damage == "foreign_proposal":
        raw["proposal_id"] = "proposal_other"
    path.write_text("not json" if damage == "invalid_json" else json.dumps(raw), encoding="utf-8")
    restarted = AuthoringProposalStore(service.store.storage_dir)
    service.store = restarted
    authoring.proposal_store = restarted
    assert restarted.require(proposal.proposal_id).revision == 1
    assert {item.proposal_id for item in restarted.list()} == before_ids
    with pytest.raises(HeadlessAuthoringError):
        service.preflight(proposal.proposal_id, request)
    with pytest.raises(HeadlessAuthoringError):
        await service.execute(proposal.proposal_id, approved)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_restart_journal_limit_only_disables_model_repair(tmp_path, monkeypatch):
    from server.xpert_runtime.authoring_store import AuthoringProposalValidationError
    service, proposal, _, request, calls = await setup(tmp_path, monkeypatch)
    approved = consent(request, service.preflight(proposal.proposal_id, request))
    await service.execute(proposal.proposal_id, approved)
    path = service.store._repair_path(proposal.proposal_id)
    raw = json.loads(path.read_text(encoding="utf-8"))
    row = raw["attempts"][0]
    row.update(status="dispatching", completed_at=None)
    row["checksum"] = service.store._payload_digest({k: v for k, v in row.items() if k != "checksum"})
    path.write_text(json.dumps(raw), encoding="utf-8")
    def over_limit(*_):
        raise AuthoringProposalValidationError("私有修复日志超过存储上限。")
    monkeypatch.setattr(AuthoringProposalStore, "_save_model_repairs_unlocked", over_limit)
    restarted = AuthoringProposalStore(service.store.storage_dir)
    assert restarted.require(proposal.proposal_id).revision == 1
    assert proposal.proposal_id in restarted._repair_errors
    assert len(calls) == 1
