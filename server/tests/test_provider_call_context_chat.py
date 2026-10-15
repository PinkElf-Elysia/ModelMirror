"""C1 provenance at the real stable Chat dispatch guard, using local fixtures."""
from dataclasses import asdict, replace
import json

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

import pytest

from server.model_router.ai_research_bridge import stable_service
from server.model_router.chat_stable import ProviderChatStableService
from server.model_router.service import RouterServiceError
from server.model_router.call_context_storage import ProviderContextStore
from server.tests.test_provider_chat_stable_service import (
    MODEL_ID, SCOPED_MODEL_ID, _qualify_scoped_model, _service,
)


@pytest.mark.asyncio
async def test_durable_context_conflict_is_reported_as_undispatched_failure(tmp_path, monkeypatch):
    service, repository, _, _ = _service(tmp_path, monkeypatch)
    dispatch = (await service.begin(MODEL_ID)).dispatch
    # Only corrupt this test's synthetic record; private in-memory origin stays intact.
    payload = asdict(replace(dispatch.call_context, purpose="evaluation"))
    with repository._connect() as db:
        db.execute("UPDATE provider_call_contexts SET context_json=? WHERE tenant_id='local' AND attempt_id=?",
                   (json.dumps(payload), dispatch.attempt_id))
    with pytest.raises(RouterServiceError) as error:
        service.mark_dispatched(dispatch)
    assert error.value.code == "provider_call_context_conflict"
    row = repository.list_chat_control_receipts("local")["attempts"][0]
    assert row["status"] == "failed"
    assert not row["dispatched"]


@pytest.mark.asyncio
async def test_stable_context_uses_server_call_identity_and_stays_out_of_public_receipt(tmp_path, monkeypatch):
    service, repository, _, _ = _service(tmp_path, monkeypatch)
    dispatch = (await service.begin(MODEL_ID)).dispatch
    context = dispatch.call_context
    assert context.tenant_id == "local"
    assert context.actor_kind == "local_anonymous"
    assert context.actor_reference == "local-anonymous"
    assert context.entry_id == "chat_default"
    assert context.purpose == "user_run"
    assert context.parent_run_id == dispatch.run_id
    assert context.logical_call_id == dispatch.run_id + ":chat"
    assert context.attempt_id == dispatch.attempt_id
    assert context.policy_revision == service.control.get_policy().revision
    receipt = service.route_receipt(dispatch, requested_model=MODEL_ID)
    assert set(asdict(context)).isdisjoint(receipt)
    service.mark_dispatched(dispatch)
    rows = repository.list_chat_control_receipts("local")
    assert sum(int(item["dispatched"]) for item in rows["attempts"]) == 1
    with pytest.raises(RouterServiceError):
        service.mark_dispatched(dispatch)
    assert sum(int(item["dispatched"]) for item in repository.list_chat_control_receipts("local")["attempts"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation,code", [
    ({"actor_kind": "service", "actor_reference": "scheduler"}, "actor_mismatch"),
    ({"entry_id": "xpert"}, "entry_mismatch"),
    ({"purpose": "certification"}, "purpose_mismatch"),
    ({"parent_run_id": "other-run"}, "reference_mismatch"),
    ({"logical_call_id": "other-call"}, "reference_mismatch"),
    ({"attempt_id": "other-attempt"}, "reference_mismatch"),
    ({"policy_revision": 999}, "policy_changed"),
])
async def test_context_drift_fails_before_dispatch_without_fallback(tmp_path, monkeypatch, mutation, code):
    service, repository, _, _ = _service(tmp_path, monkeypatch)
    dispatch = (await service.begin(MODEL_ID)).dispatch
    forged = replace(dispatch, call_context=replace(dispatch.call_context, **mutation))
    with pytest.raises(RouterServiceError) as error:
        service.mark_dispatched(forged)
    assert error.value.code == "provider_call_context_" + code
    assert all(not item["dispatched"] for item in repository.list_chat_control_receipts("local")["attempts"])


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, {}])
async def test_context_absence_is_not_legacy(tmp_path, monkeypatch, value):
    service, repository, _, _ = _service(tmp_path, monkeypatch)
    dispatch = (await service.begin(MODEL_ID)).dispatch
    with pytest.raises(RouterServiceError) as error:
        service.mark_dispatched(replace(dispatch, call_context=value))
    assert error.value.code == "provider_call_context_required"
    assert all(not item["dispatched"] for item in repository.list_chat_control_receipts("local")["attempts"])


@pytest.mark.asyncio
async def test_bridge_context_is_separate_from_route_scope(tmp_path, monkeypatch):
    service, repository, primary, _ = _service(tmp_path, monkeypatch, newapi_ip="8.8.8.8")
    research = stable_service(service.router_service)
    # Literature retains its original stable route, but not anonymous identity.
    dispatch = (await research.begin(MODEL_ID)).dispatch
    assert dispatch.gateway == "default"
    assert dispatch.call_context.entry_id == "ai_research_bridge"
    assert dispatch.call_context.actor_kind == "service"
    assert dispatch.call_context.actor_reference == "ai-research-bridge"
    assert dispatch.call_context.purpose == "user_run"
    research.mark_dispatched(dispatch)
    _qualify_scoped_model(repository, primary)
    scoped = (await research.begin_scoped_certified(SCOPED_MODEL_ID)).dispatch
    assert scoped.gateway == "ai_research_scoped"
    assert scoped.call_context.entry_id == "ai_research_bridge"
    rpg = (await service.begin_rpg_certified(SCOPED_MODEL_ID)).dispatch
    assert rpg.gateway == "rpg_scoped"
    assert rpg.call_context.actor_reference == "rpg-bridge"
    assert rpg.call_context.entry_id == "rpg_bridge"


@pytest.mark.asyncio
async def test_restarted_service_cannot_dispatch_old_in_memory_call(tmp_path, monkeypatch):
    service, repository, _, _ = _service(tmp_path, monkeypatch)
    dispatch = (await service.begin(MODEL_ID)).dispatch
    repository.close()
    from server.model_router.repository import SQLiteRouterRepository
    from server.model_router.service import ModelRouterService
    reopened = SQLiteRouterRepository.open(tmp_path, master_key=b"x" * 32)
    restarted = ProviderChatStableService(ModelRouterService(
        reopened, egress_policy=service.router_service.egress_policy,
    ))
    with pytest.raises(RouterServiceError):
        restarted.mark_dispatched(dispatch)
    assert all(not item["dispatched"] for item in reopened.list_chat_control_receipts("local")["attempts"])


@pytest.mark.asyncio
async def test_relabeling_gateway_and_context_together_is_rejected(tmp_path, monkeypatch):
    service, repository, _, _ = _service(tmp_path, monkeypatch)
    dispatch = (await service.begin(MODEL_ID)).dispatch
    forged = replace(
        dispatch, gateway="rpg_scoped",
        call_context=replace(dispatch.call_context, actor_kind="service",
                             actor_reference="rpg-bridge", entry_id="rpg_bridge"),
    )
    with pytest.raises(RouterServiceError):
        service.mark_dispatched(forged)
    assert all(not row["dispatched"] for row in repository.list_chat_control_receipts("local")["attempts"])


@pytest.mark.asyncio
async def test_another_service_cannot_relabel_an_existing_attempt(tmp_path, monkeypatch):
    service, repository, _, _ = _service(tmp_path, monkeypatch)
    dispatch = (await service.begin(MODEL_ID)).dispatch
    research = stable_service(service.router_service)
    forged = replace(
        dispatch,
        call_context=replace(dispatch.call_context, actor_kind="service",
                             actor_reference="ai-research-bridge", entry_id="ai_research_bridge"),
    )
    with pytest.raises(RouterServiceError):
        research.mark_dispatched(forged)
    assert all(not row["dispatched"] for row in repository.list_chat_control_receipts("local")["attempts"])


@pytest.mark.parametrize("scoped", [False, True])
def test_authenticated_research_route_persists_host_identity_before_post(tmp_path, monkeypatch, scoped):
    from server.model_router import ai_research_bridge as bridge
    service, repo, primary, _ = _service(tmp_path, monkeypatch, newapi_ip="8.8.8.8")
    _qualify_scoped_model(repo, primary)
    research = stable_service(service.router_service)
    monkeypatch.setenv("AI_RESEARCH_S2S_ENABLED", "true")
    monkeypatch.setenv("AI_RESEARCH_S2S_TOKEN", "synthetic-service-token")
    monkeypatch.setenv("AI_RESEARCH_LITERATURE_MODEL_ID", MODEL_ID)
    monkeypatch.setenv("AI_RESEARCH_HYPOTHESIS_MODEL_ID", SCOPED_MODEL_ID)
    model = SCOPED_MODEL_ID if scoped else MODEL_ID
    calls = []

    def upstream(request):
        rows = repo.list_chat_control_receipts("local")
        attempt = rows["attempts"][0]
        context = ProviderContextStore(repo).read("local", attempt["id"])
        assert attempt["dispatched"]
        assert (context.entry_id, context.actor_kind, context.actor_reference, context.purpose) == (
            "ai_research_bridge", "service", "ai-research-bridge", "user_run")
        assert context.parent_run_id == rows["runs"][0]["id"]
        assert context.policy_revision == research.control.get_policy().revision
        calls.append(context)
        return httpx.Response(200, json={"model": model, "choices": [
            {"message": {"role": "assistant", "content": "synthetic"}, "finish_reason": "stop"}]})

    monkeypatch.setattr(research.transport, "client_kwargs", lambda: {
        "transport": httpx.MockTransport(upstream), "trust_env": False, "follow_redirects": False})
    app = FastAPI()
    app.include_router(bridge.router)
    app.dependency_overrides[bridge.stable_service] = lambda: research
    payload = {"model": model, "messages": [{"role": "user", "content": "synthetic"}], "stream": False}
    headers = {"Authorization": "Bearer synthetic-service-token"}
    with TestClient(app) as client:
        assert client.post("/api/ai-research/v1/chat/completions", json=payload).status_code == 401
        for field in ("tenant_id", "actor_reference", "entry_id", "connection_id", "call_context"):
            rejected = client.post("/api/ai-research/v1/chat/completions", headers=headers,
                                   json={**payload, field: "forged"})
            assert rejected.status_code == 422
        assert calls == []
        response = client.post("/api/ai-research/v1/chat/completions", headers=headers, json=payload)
        assert response.status_code == 200, response.text
        assert len(calls) == 1
        assert "actor_reference" not in response.text and "call_context" not in response.text
    saved = calls[0]
    repo.close()
    from server.model_router.repository import SQLiteRouterRepository
    with SQLiteRouterRepository.open(tmp_path, master_key=b"x" * 32) as reopened:
        assert ProviderContextStore(reopened).read("local", saved.attempt_id) == saved
