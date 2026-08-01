from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from server.main import app
from server.rag.api import (
    set_evaluation_executor_for_tests,
    set_pipeline_executor_for_tests,
    set_rag_service_for_tests,
)
from server.rag.embedder import EmbeddingClient
from server.rag.evaluation import (
    EvaluationPromotionError,
    EvaluationRevisionError,
    KnowledgeEvaluationStore,
    aggregate_target_metrics,
    evaluate_promotion_gate,
    evaluate_retrieval_case,
)
from server.rag.evaluation_executor import KnowledgeEvaluationExecutor
from server.rag.pipeline_executor import KnowledgePipelineExecutor
from server.rag.rag_service import RagService
from server.rag.vector_store import LocalJsonVectorStore
from server.xpert_runtime.run_registry import RunRegistry


@pytest_asyncio.fixture
async def evaluation_runtime(tmp_path: Path):
    service = RagService(
        storage_dir=tmp_path / "rag-storage",
        uploads_dir=tmp_path / "rag-uploads",
        embedder=EmbeddingClient(api_key="", dimension=128),
        vector_store=LocalJsonVectorStore(tmp_path / "rag-storage" / "vectors.json"),
        llm_enabled=False,
    )
    registry = RunRegistry()
    pipeline_executor = KnowledgePipelineExecutor(
        service,
        run_registry=registry,
        poll_interval=0.01,
    )
    evaluation_store = KnowledgeEvaluationStore(service.storage_dir / "evaluations.json")
    evaluation_executor = KnowledgeEvaluationExecutor(
        service,
        evaluation_store,
        run_registry=registry,
        poll_interval=0.01,
    )
    set_rag_service_for_tests(service)
    set_pipeline_executor_for_tests(pipeline_executor)
    set_evaluation_executor_for_tests(evaluation_executor)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, service, pipeline_executor, evaluation_executor, registry
    set_evaluation_executor_for_tests(None)
    set_pipeline_executor_for_tests(None)
    set_rag_service_for_tests(None)


async def _create_kb(client: httpx.AsyncClient, name: str = "evaluation") -> str:
    response = await client.post("/api/rag/knowledge_bases", json={"name": name})
    assert response.status_code == 200, response.text
    return str(response.json()["id"])


async def _upload_text(
    client: httpx.AsyncClient,
    kb_id: str,
    filename: str,
    text: str,
) -> str:
    response = await client.post(
        f"/api/rag/knowledge_bases/{kb_id}/documents",
        files={"file": (filename, text.encode("utf-8"), "text/plain")},
    )
    assert response.status_code == 200, response.text
    return str(response.json()["id"])


async def _execute_draft(
    client: httpx.AsyncClient,
    executor: KnowledgePipelineExecutor,
    kb_id: str,
    document_ids: list[str],
) -> dict:
    draft = (await client.get(f"/api/rag/pipeline/draft?kb_id={kb_id}")).json()
    response = await client.post(
        f"/api/rag/pipeline/draft/{kb_id}/execute",
        json={
            "draft_version": draft["version"],
            "source_document_ids": document_ids,
            "xpert_file_refs": [],
        },
    )
    assert response.status_code == 200, response.text
    assert await executor.run_once() is True
    job = (await client.get(f"/api/rag/pipeline/jobs/{response.json()['job_id']}")).json()
    assert job["status"] == "succeeded", job
    return job


def test_evaluation_metrics_match_stable_references_and_rankings() -> None:
    references = [
        {"reference_id": "ref-doc", "document_id": "doc-a", "relevance": 1},
        {
            "reference_id": "ref-page",
            "document_id": "doc-b",
            "page_number": 3,
            "relevance": 3,
        },
    ]
    sources = [
        {"chunk_id": "noise", "doc_id": "doc-z", "score": 0.99},
        {"chunk_id": "a-1", "doc_id": "doc-a", "score": 0.8},
        {"chunk_id": "b-1", "doc_id": "doc-b", "page_number": 3, "score": 0.7},
    ]

    result = evaluate_retrieval_case(sources, references, ks=[1, 3, 5, 10], latency_ms=12.5)

    assert result["metrics"]["hit_at_1"] == 0.0
    assert result["metrics"]["recall_at_3"] == 1.0
    assert result["metrics"]["mrr_at_10"] == 0.5
    assert result["metrics"]["citation_coverage"] == 1.0
    assert result["ranking"][1]["matched_reference_id"] == "ref-doc"
    assert result["ranking"][2]["matched_reference_id"] == "ref-page"
    assert all("text" not in item and "snippet" not in item for item in result["ranking"])

    aggregate = aggregate_target_metrics([result], ks=[1, 3, 5, 10])
    gate = evaluate_promotion_gate(
        aggregate,
        baseline=aggregate,
        policy={"min_recall_at_5": 0.8},
    )
    assert gate["passed"] is True


def test_evaluation_store_persists_revisions_runs_and_recovery(tmp_path: Path) -> None:
    path = tmp_path / "evaluations.json"
    store = KnowledgeEvaluationStore(path)
    evaluation_set = store.create_set("kb-a", "Regression set")
    updated = store.add_cases(
        evaluation_set["eval_set_id"],
        expected_revision=1,
        cases=[
            {
                "query": "Where is the launch policy?",
                "expected_refs": [{"document_id": "doc-a", "relevance": 2}],
            }
        ],
    )
    with pytest.raises(EvaluationRevisionError):
        store.update_set(
            evaluation_set["eval_set_id"],
            expected_revision=1,
            name="stale",
        )

    run = store.create_run(
        evaluation_set=updated,
        targets=[{"target_id": "version-a", "version_id": "version-a"}],
        baseline_version_id=None,
        ks=[1, 3, 5, 10],
        gate_policy=store.get_gate_policy("kb-a"),
    )
    assert store.claim_next_run()["status"] == "running"

    reloaded = KnowledgeEvaluationStore(path)
    assert reloaded.get_set(evaluation_set["eval_set_id"])["cases"][0]["query"].startswith("Where")
    assert reloaded.recover_runs() == 1
    assert reloaded.get_run(run["run_id"])["status"] == "queued"


@pytest.mark.asyncio
async def test_evaluation_api_runs_versions_and_enforces_required_gate(
    evaluation_runtime,
) -> None:
    client, _, pipeline_executor, evaluation_executor, registry = evaluation_runtime
    kb_id = await _create_kb(client)
    baseline_doc = await _upload_text(
        client,
        kb_id,
        "baseline.txt",
        "The legacy handbook discusses office access badges.",
    )
    baseline_job = await _execute_draft(client, pipeline_executor, kb_id, [baseline_doc])
    baseline_version = str(baseline_job["candidate_version_id"])
    assert (await client.post(f"/api/rag/pipeline/versions/{baseline_version}/activate")).status_code == 200

    relevant_doc = await _upload_text(
        client,
        kb_id,
        "orion.txt",
        "Project Orion deployment requires a signed safety review before the production rollout.",
    )
    candidate_job = await _execute_draft(
        client,
        pipeline_executor,
        kb_id,
        [baseline_doc, relevant_doc],
    )
    candidate_version = str(candidate_job["candidate_version_id"])

    created_set = await client.post(
        "/api/rag/evaluation-sets",
        json={"kb_id": kb_id, "name": "Orion release regression"},
    )
    assert created_set.status_code == 200, created_set.text
    evaluation_set = created_set.json()
    case_response = await client.post(
        f"/api/rag/evaluation-sets/{evaluation_set['eval_set_id']}/cases",
        json={
            "expected_revision": evaluation_set["revision"],
            "case": {
                "query": "What approval is required before Project Orion production rollout?",
                "expected_refs": [{"document_id": relevant_doc, "relevance": 3}],
                "tags": ["release"],
            },
        },
    )
    assert case_response.status_code == 200, case_response.text

    run_response = await client.post(
        "/api/rag/evaluation-runs",
        json={
            "eval_set_id": evaluation_set["eval_set_id"],
            "targets": [
                {"version_id": baseline_version, "label": "baseline"},
                {"version_id": candidate_version, "label": "candidate"},
            ],
            "baseline_version_id": baseline_version,
            "ks": [1, 3],
        },
    )
    assert run_response.status_code == 200, run_response.text
    assert run_response.json()["ks"] == [1, 3, 5, 10]
    assert await evaluation_executor.run_once() is True

    completed = (await client.get(f"/api/rag/evaluation-runs/{run_response.json()['run_id']}")).json()
    assert completed["status"] == "succeeded"
    candidate = next(
        item for item in completed["target_results"] if item["version_id"] == candidate_version
    )
    assert candidate["metrics"]["recall_at_5"] == 1.0, candidate
    assert candidate["promotion_gate"]["passed"] is True
    serialized = str(completed).lower()
    assert "project orion deployment requires a signed" not in serialized
    assert "embedding" not in serialized
    assert "stored_path" not in serialized
    assert "sk-" not in serialized

    gate_response = await client.patch(
        f"/api/rag/evaluation-gate/{kb_id}",
        json={
            "mode": "required",
            "min_recall_at_5": 0.8,
            "max_mrr_regression": 0.03,
            "max_citation_hit_regression": 0.02,
            "max_no_result_increase": 0.05,
            "max_p95_latency_ratio": 10,
            "require_zero_errors": True,
        },
    )
    assert gate_response.status_code == 200, gate_response.text

    blocked = await client.post(f"/api/rag/pipeline/versions/{candidate_version}/activate")
    assert blocked.status_code == 409
    promoted = await client.post(
        f"/api/rag/pipeline/versions/{candidate_version}/promote",
        json={"evaluation_run_id": completed["run_id"]},
    )
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["active"] is True

    runs = await registry.list_runs(run_type="knowledge_evaluation")
    assert len(runs) == 1
    checkpoints = await registry.list_checkpoints(runs[0].run_id)
    assert {item.event_type for item in checkpoints} >= {
        "knowledge_evaluation.started",
        "knowledge_evaluation.completed",
    }


def test_promotion_rejects_stale_evaluation_set(tmp_path: Path) -> None:
    store = KnowledgeEvaluationStore(tmp_path / "evaluation.json")
    evaluation_set = store.create_set("kb-a", "gate")
    evaluation_set = store.add_cases(
        evaluation_set["eval_set_id"],
        expected_revision=1,
        cases=[{"query": "q", "expected_refs": [{"document_id": "doc-a"}]}],
    )
    run = store.create_run(
        evaluation_set=evaluation_set,
        targets=[{"target_id": "v1", "version_id": "v1"}],
        baseline_version_id=None,
        ks=[1, 3, 5, 10],
        gate_policy=store.get_gate_policy("kb-a"),
    )
    store.complete_run(
        run["run_id"],
        [{"version_id": "v1", "promotion_gate": {"passed": True}}],
    )
    store.update_set(
        evaluation_set["eval_set_id"],
        expected_revision=evaluation_set["revision"],
        description="changed",
    )

    with pytest.raises(EvaluationPromotionError):
        store.assert_promotion_allowed(
            kb_id="kb-a",
            version_id="v1",
            evaluation_run_id=run["run_id"],
            require_passed_run=True,
        )
