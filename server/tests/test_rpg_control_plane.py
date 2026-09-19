from pathlib import Path
from types import SimpleNamespace
import pytest
from server.model_router.provider_catalog import normalize_provider_catalog
from server.model_router.repository import SQLiteRouterRepository
from server.tests.test_provider_chat_stable_service import _service, _qualify_scoped_model, SCOPED_MODEL_ID

@pytest.mark.asyncio
async def test_rpg_dispatch_has_own_scope_exact_certificate_and_durable_completion(tmp_path, monkeypatch):
    service, repository, primary, backup = _service(tmp_path, monkeypatch, newapi_ip="8.8.8.8")
    _qualify_scoped_model(repository, primary)
    result = await service.begin_rpg_certified(SCOPED_MODEL_ID)
    dispatch = result.dispatch
    assert dispatch.gateway == "rpg_scoped"
    assert dispatch.requested_model == SCOPED_MODEL_ID
    assert dispatch.target.connection_id == primary
    service.mark_dispatched(dispatch)
    repository.stage_chat_control_completion("local", dispatch.attempt_id,
        expected_run_id=dispatch.run_id, status="succeeded", result_class="success", actual_model=SCOPED_MODEL_ID)
    restarted = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    run = next(r for r in restarted.list_chat_control_receipts("local")["runs"] if r["id"] == dispatch.run_id)
    assert run["gateway"] == "rpg_scoped"
    assert run["status"] == "succeeded"
    assert restarted.reconcile_chat_control_completions("local")["pending"] == 0
    assert service.readiness(SCOPED_MODEL_ID)[0] is False
    research = await service.begin_scoped_certified(SCOPED_MODEL_ID)
    assert research.dispatch.gateway == "ai_research_scoped"
    service.fail_undispatched(research.dispatch, error_code="test_no_dispatch")

@pytest.mark.asyncio
async def test_rpg_qualification_drift_rejects_before_dispatch(tmp_path, monkeypatch):
    service, repository, primary, _ = _service(tmp_path, monkeypatch, newapi_ip="8.8.8.8")
    _qualify_scoped_model(repository, primary)
    dispatch = (await service.begin_rpg_certified(SCOPED_MODEL_ID)).dispatch
    monkeypatch.setenv("MODEL_CONTROL_CHAT_ENABLED", "false")
    with pytest.raises(Exception, match="资格|策略|policy|qualification"):
        service.mark_dispatched(dispatch)
    attempts = repository.list_chat_control_receipts("local")["attempts"]
    assert all(not a["dispatched"] for a in attempts)

def test_catalog_retains_only_bounded_declared_compatibility_no_inference():
    records = [
        {"id":"model/exact", "context_length":128000, "max_output_tokens":32768,
         "supported_parameters":["temperature","top_p","max_tokens","credential"], "api_key":"must-not-retain"},
        {"id":"model/missing"},
        {"id":"model/invalid", "context_length":True, "max_output_tokens":-1, "supported_parameters":"temperature"},
    ]
    models, _, _, _ = normalize_provider_catalog(records, connection=SimpleNamespace(scopes=["chat"]), observed_at="2026-09-19T00:00:00Z")
    metadata = {m["model_id"]:m["metadata"] for m in models}
    assert metadata["model/exact"] == {"context_length":128000,"max_output_tokens":32768,"supported_parameters":["max_tokens","temperature","top_p"]}
    assert metadata["model/missing"] == metadata["model/invalid"] == {}

@pytest.mark.asyncio
async def test_scoped_outbox_cannot_relabel_another_workload(tmp_path, monkeypatch):
    import json
    service, repository, primary, _ = _service(tmp_path, monkeypatch, newapi_ip="8.8.8.8")
    _qualify_scoped_model(repository, primary)
    dispatch = (await service.begin_rpg_certified(SCOPED_MODEL_ID)).dispatch
    service.mark_dispatched(dispatch)
    payload = repository.stage_chat_control_completion("local", dispatch.attempt_id,
        expected_run_id=dispatch.run_id, status="succeeded", result_class="success")
    payload["gateway"] = "ai_research_scoped"
    payload["payloadSha256"] = repository._chat_completion_identity(payload)
    path = repository._chat_completion_outbox_path("local", dispatch.attempt_id)
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert repository.reconcile_chat_control_completions("local") == {"applied": 0, "pending": 1}
    assert path.exists()
