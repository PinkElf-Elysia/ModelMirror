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


def test_source_block_and_no_result_metrics_are_aggregated_separately() -> None:
    positive = evaluate_retrieval_case(
        [
            {
                "chunk_id": "candidate-v2-child",
                "source_document_id": "doc-a",
                "source_block_id": "block-stable",
                "score": 0.9,
            }
        ],
        [
            {
                "reference_id": "ref-a",
                "document_id": "doc-a",
                "chunk_id": "initial-v1-child",
                "source_block_id": "block-stable",
                "match_mode": "source_block",
                "relevance": 3,
            }
        ],
        ks=[1, 5],
    )
    abstention = evaluate_retrieval_case(
        [],
        [],
        ks=[1, 5],
        expected_no_result=True,
    )
    false_positive = evaluate_retrieval_case(
        [{"chunk_id": "noise", "source_document_id": "doc-z", "score": 0.4}],
        [],
        ks=[1, 5],
        expected_no_result=True,
    )

    assert positive["metrics"]["recall_at_1"] == 1.0
    assert abstention["metrics"]["no_result_accuracy"] == 1.0
    assert false_positive["metrics"]["false_positive_rate"] == 1.0
    aggregate = aggregate_target_metrics(
        [positive, abstention, false_positive],
        ks=[1, 5],
    )
    assert aggregate["positive_case_count"] == 1
    assert aggregate["no_result_case_count"] == 2
    assert aggregate["recall_at_1"] == 1.0
    assert aggregate["no_result_accuracy"] == 0.5
    assert aggregate["false_positive_rate"] == 0.5


def test_promotion_gate_does_not_penalize_correct_no_result_abstention() -> None:
    positive = evaluate_retrieval_case(
        [{"chunk_id": "answer", "source_document_id": "doc-a", "score": 0.9}],
        [{"document_id": "doc-a"}],
        ks=[1, 5, 10],
    )
    noisy_no_result = evaluate_retrieval_case(
        [{"chunk_id": "noise", "source_document_id": "doc-z", "score": 0.1}],
        [],
        ks=[1, 5, 10],
        expected_no_result=True,
    )
    correct_no_result = evaluate_retrieval_case(
        [],
        [],
        ks=[1, 5, 10],
        expected_no_result=True,
    )
    baseline = aggregate_target_metrics([positive, noisy_no_result], ks=[1, 5, 10])
    candidate = aggregate_target_metrics([positive, correct_no_result], ks=[1, 5, 10])

    gate = evaluate_promotion_gate(
        candidate,
        baseline=baseline,
        policy={
            "min_recall_at_5": 1.0,
            "min_no_result_accuracy": 1.0,
            "max_no_result_increase": 0.0,
        },
    )

    assert baseline["positive_no_result_rate"] == 0.0
    assert candidate["positive_no_result_rate"] == 0.0
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


def test_evaluation_set_versions_are_immutable_and_pin_run_snapshot(tmp_path: Path) -> None:
    store = KnowledgeEvaluationStore(tmp_path / "evaluations.json")
    draft = store.create_set(
        "kb-a",
        "Benchmark",
        origin="benchmark_catalog",
        catalog_ref={"pack_id": "pack-a", "version": 1, "checksum": "a" * 64},
    )
    draft = store.add_cases(
        draft["eval_set_id"],
        expected_revision=draft["revision"],
        cases=[
            {
                "query": "Known fact?",
                "expected_refs": [
                    {
                        "document_id": "doc-a",
                        "chunk_id": "chunk-v1",
                        "source_block_id": "block-a",
                        "match_mode": "source_block",
                    }
                ],
            },
            {
                "query": "Unknown fact?",
                "expected_no_result": True,
                "expected_refs": [],
            },
        ],
    )
    version = store.publish_set(
        draft["eval_set_id"],
        expected_revision=draft["revision"],
        release_notes="v1",
    )
    assert draft["benchmark_role"] == "regression_guard"
    assert version["benchmark_role"] == "regression_guard"
    changed = store.update_case(
        draft["eval_set_id"],
        draft["cases"][0]["case_id"],
        expected_revision=draft["revision"],
        values={"query": "Changed draft?"},
    )
    pinned = store.get_set_version(draft["eval_set_id"], 1)
    assert pinned["cases"][0]["query"] == "Known fact?"
    assert changed["cases"][0]["query"] == "Changed draft?"

    run = store.create_run(
        evaluation_set=store.get_set(draft["eval_set_id"]),
        evaluation_set_version=version,
        targets=[{"target_id": "v1", "version_id": "v1"}],
        baseline_version_id=None,
        ks=[1, 5],
        gate_policy=store.get_gate_policy("kb-a"),
    )
    assert run["eval_set_version"] == 1
    assert run["eval_set_snapshot"]["cases"][0]["query"] == "Known fact?"


def test_published_evaluation_version_remains_available_after_draft_archive(
    tmp_path: Path,
) -> None:
    store = KnowledgeEvaluationStore(tmp_path / "evaluations.json")
    dataset = store.create_set("kb", "Stable benchmark")
    dataset = store.add_cases(
        dataset["eval_set_id"],
        expected_revision=dataset["revision"],
        cases=[
            {
                "query": "Which fixed source is expected?",
                "expected_refs": [
                    {
                        "document_id": "doc-1",
                        "source_block_id": "block-1",
                        "match_mode": "source_block",
                        "relevance": 3,
                    }
                ],
            }
        ],
    )
    version = store.publish_set(
        dataset["eval_set_id"], expected_revision=dataset["revision"]
    )
    store.update_set(
        dataset["eval_set_id"],
        expected_revision=dataset["revision"],
        status="archived",
    )

    persisted = store.get_set_version(dataset["eval_set_id"], version["version"])
    assert persisted["checksum"] == version["checksum"]
    assert len(persisted["cases"]) == 1


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
    published_response = await client.post(
        f"/api/rag/evaluation-sets/{evaluation_set['eval_set_id']}/publish",
        json={
            "expected_revision": case_response.json()["revision"],
            "release_notes": "fixed regression v1",
        },
    )
    assert published_response.status_code == 200, published_response.text
    assert published_response.json()["version"] == 1
    versions_response = await client.get(
        f"/api/rag/evaluation-sets/{evaluation_set['eval_set_id']}/versions"
    )
    assert versions_response.status_code == 200
    assert versions_response.json()["version_count"] == 1

    run_response = await client.post(
        "/api/rag/evaluation-runs",
        json={
            "eval_set_id": evaluation_set["eval_set_id"],
            "eval_set_version": 1,
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
    assert run_response.json()["eval_set_version"] == 1
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
