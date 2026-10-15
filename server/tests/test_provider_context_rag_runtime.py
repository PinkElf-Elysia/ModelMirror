import json
import httpx
import pytest

from server.model_router.rag_call_context import operation_scope
from server.tests.test_managed_rerank import _stack
from server.model_router.rag_call_context import opaque_reference
from server.model_router.call_context_storage import ProviderContextStore
from server.model_router.rerank_gateway import ManagedRerankError
from server.rag.rag_service import PipelineJobStateError


@pytest.mark.asyncio
@pytest.mark.parametrize("source,actor,purpose", [
    ("rag_request", "local-anonymous", "user_run"),
    ("rag_evaluation", "rag-evaluation", "evaluation"),
    ("skill_request", "local-anonymous", "user_run"),
    ("skill_evaluation", "skill-evaluation", "evaluation"),
])
async def test_rerank_context_matches_post_and_duplicate_scope_cannot_replay(tmp_path, monkeypatch, source, actor, purpose):
    gateway, repo, requests = await _stack(tmp_path, monkeypatch)
    entry = "skill_rerank" if source.startswith("skill") else "rag_rerank"
    before = len(requests)
    with operation_scope(source, reference="host-evaluation-case"):
        run = gateway.start_run(entry, parent_run_reference="host-slot")
        await run.rerank("PRIVATE_QUERY", ["PRIVATE_DOCUMENT"], model_id=gateway.qualification(entry).model_id,
            top_n=1, logical_call_key="slot", call_sequence=1, timeout_seconds=3)
        run.finish_success()
        with pytest.raises(ManagedRerankError) as caught:
            gateway.start_run(entry, parent_run_reference="new-random-reference-must-not-replay")
        assert caught.value.code == "provider_workload_logical_run_replay_blocked"
    assert len([r for r in requests[before:] if r.method == "POST"]) == 1
    with repo._connect() as db:
        rows = db.execute("SELECT context_json FROM provider_call_contexts").fetchall()
        assert len(rows) == 1
        context = json.loads(rows[0][0])
        assert (context["entry_id"], context["actor_reference"], context["purpose"]) == (entry, actor, purpose)
        assert "PRIVATE_" not in rows[0][0]
    repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["missing", "actor", "entry"])
async def test_rerank_corrupt_source_blocks_before_post(tmp_path, monkeypatch, corruption):
    gateway, repo, requests = await _stack(tmp_path, monkeypatch)
    with operation_scope("rag_request"):
        run = gateway.start_run("rag_rerank", parent_run_reference="host")
    before = len(requests)
    with repo._connect() as db:
        if corruption == "missing":
            db.execute("DELETE FROM provider_workload_run_origins WHERE run_id=?", (run.run_id,))
        elif corruption == "entry":
            db.execute("UPDATE provider_workload_run_origins SET entry_id='rag_embedding' WHERE run_id=?", (run.run_id,))
        else:
            db.execute("UPDATE provider_execution_origins SET actor_reference='forged-user'")
    with pytest.raises(ManagedRerankError) as caught:
        await run.rerank("PRIVATE_QUERY", ["PRIVATE_DOCUMENT"], model_id=gateway.qualification("rag_rerank").model_id,
            top_n=1, logical_call_key="slot", call_sequence=1, timeout_seconds=3)
    assert caught.value.code in {"provider_call_context_origin_missing", "provider_call_context_actor_mismatch", "provider_call_context_entry_mismatch"}
    assert len(requests) == before
    repo.close()


@pytest.mark.asyncio
async def test_rag_query_and_job_use_trusted_user_source_and_child_inheritance(tmp_path, monkeypatch):
    from server.tests.test_rag_managed_generation import _managed_stack, _indexed_service, MODEL_ID
    for name in ("EMBEDDING_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "LLM_GATEWAY_KEY", "RAG_LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RAG_EMBEDDING_MODE", "hash")
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"model": MODEL_ID,
            "choices": [{"message": {"content": "PRIVATE_OUTPUT"}}]})
    service, repo = _managed_stack(tmp_path, monkeypatch, handler)
    with operation_scope("rag_request"):
        kb_id, _, version_id = await _indexed_service(service)
    job_id = service.get_pipeline_version(version_id)["job_id"]
    origin = ProviderContextStore(repo).execution("local", opaque_reference("rag-job", job_id))
    assert origin["actor_reference"] == "local-anonymous"
    with operation_scope("rag_request"):
        result = await service.query_pipeline_version(version_id, "PRIVATE_QUERY", top_k=1, retrieval={"mode": "vector"})
    assert result["answer"] == "PRIVATE_OUTPUT"
    store = ProviderContextStore(repo)
    store.register_execution(execution_id="host-workflow", task_id="host-task", source_kind="workflow_deployment")
    with service.provider_child_scope("host-workflow", "knowledge-node"):
        await service.query_pipeline_version(version_id, "PRIVATE_QUERY", top_k=1, retrieval={"mode": "vector"})
    assert len(requests) == 2
    with repo._connect() as db:
        contexts = [json.loads(row[0]) for row in db.execute("SELECT context_json FROM provider_call_contexts")]
        assert {(c["actor_reference"], c["purpose"]) for c in contexts} == {
            ("local-anonymous", "user_run"), ("workflow-deployment", "background_assistance")}
        assert "PRIVATE_" not in json.dumps(contexts)
        db.execute("DELETE FROM provider_execution_origins WHERE execution_id=?", (origin["execution_id"],))
    with pytest.raises(PipelineJobStateError, match="provider_call_context_origin_missing"):
        await service.process_pipeline_job_sources(job_id)
    service._update_pipeline_job(job_id, lambda job: job.update(status="running"))
    assert service.recover_pipeline_jobs() == 1
    failed = service.get_pipeline_job(job_id)
    assert failed["status"] == "failed"
    assert failed["error_code"] == "provider_call_context_origin_missing"
    assert service.claim_next_pipeline_job() is None
    assert len(requests) == 2
    repo.close()


def test_http_body_cannot_select_actor_source_or_tenant(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from server.rag import api
    from server.model_router.rag_call_context import _origin
    seen = []
    class Service:
        async def query(self, *args, **kwargs):
            seen.append(_origin.get())
            assert not {"actor_kind", "tenant_id", "origin_execution_id"} & kwargs.keys()
            raise api.KnowledgeBaseNotFoundError("synthetic missing knowledge base")
    monkeypatch.setattr(api, "get_rag_service", lambda: Service())
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        result = client.post("/api/rag/query", json={"kb_id": "synthetic", "question": "private",
            "tenant_id": "foreign", "actor_kind": "local_admin", "source_kind": "rag_evaluation",
            "origin_execution_id": "other-run", "connection_id": "other-connection"})
    assert result.status_code == 404
    assert len(seen) == 1 and seen[0].source_kind == "rag_request"
    assert _origin.get() is None
