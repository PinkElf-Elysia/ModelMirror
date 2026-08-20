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
    qualify_promotion_evidence,
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


def test_citation_precision_at_5_uses_a_fixed_denominator() -> None:
    references = [{"document_id": "doc-answer"}]
    top_five = [
        {"chunk_id": "answer", "doc_id": "doc-answer", "score": 0.9},
        *[
            {"chunk_id": f"noise-{index}", "doc_id": f"doc-noise-{index}", "score": 0.8 - index / 10}
            for index in range(4)
        ],
    ]
    baseline = evaluate_retrieval_case(top_five, references, ks=[1, 5, 10])
    candidate = evaluate_retrieval_case(
        [
            *top_five,
            {"chunk_id": "tail-1", "doc_id": "tail-doc-1", "score": 0.2},
            {"chunk_id": "tail-2", "doc_id": "tail-doc-2", "score": 0.1},
        ],
        references,
        ks=[1, 5, 10],
    )

    assert baseline["metrics"]["citation_hit_rate"] != candidate["metrics"]["citation_hit_rate"]
    assert baseline["metrics"]["citation_precision_at_5"] == 0.2
    assert candidate["metrics"]["citation_precision_at_5"] == 0.2


def test_promotion_gate_rejects_diagnostic_only_evidence() -> None:
    metrics = {
        "recall_at_5": 1.0,
        "mrr_at_10": 1.0,
        "citation_hit_rate": 1.0,
        "citation_precision_at_5": 0.2,
        "citation_coverage": 1.0,
        "no_result_accuracy": 1.0,
        "positive_no_result_rate": 0.0,
        "p95_latency_ms": 5.0,
        "error_count": 0,
    }

    gate = evaluate_promotion_gate(
        metrics,
        baseline=metrics,
        evidence_qualification={
            "status": "diagnostic_only",
            "qualified": False,
            "positive_case_count": 5,
            "stable_source_block_positive_count": 5,
            "reviewed_hard_negative_count": 1,
        },
    )

    evidence_check = next(
        item for item in gate["checks"] if item["id"] == "qualified_promotion_evidence"
    )
    assert evidence_check["passed"] is False
    assert gate["passed"] is False


def test_promotion_evidence_requires_30_stable_positives_and_12_hard_negatives() -> None:
    positives = [
        {
            "case_id": f"positive-{index}",
            "query": f"positive query {index}",
            "expected_refs": [
                {
                    "document_id": "doc-a",
                    "source_block_id": f"block-{index}",
                    "match_mode": "source_block",
                    "relevance": 3,
                }
            ],
        }
        for index in range(30)
    ]
    negatives = [
        {
            "case_id": f"negative-{index}",
            "query": f"confusable query {index}",
            "expected_no_result": True,
            "expected_refs": [],
            "review_status": "approved",
            "tags": ["corpus_near", "hard_negative"],
        }
        for index in range(12)
    ]
    snapshot = {
        "origin": "manual",
        "benchmark_role": "promotion_evidence",
        "version_id": "evalsetver-fixed",
        "published_at": 1.0,
        "cases": [*positives, *negatives],
    }

    diagnostic = qualify_promotion_evidence({**snapshot, "cases": positives[:6]})
    qualified = qualify_promotion_evidence(snapshot)

    assert diagnostic["status"] == "diagnostic_only"
    assert diagnostic["qualified"] is False
    assert qualified["status"] == "qualified"
    assert qualified["qualified"] is True
    assert qualified["counts"] == {
        "total": 42,
        "positive": 30,
        "stable_source_block_positive": 30,
        "reviewed_hard_negative": 12,
    }

    mutable_unclassified = qualify_promotion_evidence(
        {
            **snapshot,
            "version_id": None,
            "published_at": None,
            "benchmark_role": "unclassified",
        }
    )
    assert mutable_unclassified["status"] == "diagnostic_only"
    assert mutable_unclassified["qualified"] is False
    assert mutable_unclassified["immutable_snapshot"] is False
    assert mutable_unclassified["selection_eligible"] is False


def test_legacy_citation_gate_policy_maps_to_precision_at_5(tmp_path: Path) -> None:
    store = KnowledgeEvaluationStore(tmp_path / "evaluations.json")

    policy = store.set_gate_policy(
        "kb-legacy", {"max_citation_hit_regression": 0.07}
    )
    reloaded = KnowledgeEvaluationStore(tmp_path / "evaluations.json").get_gate_policy(
        "kb-legacy"
    )

    assert policy["max_citation_hit_regression"] == 0.07
    assert policy["max_citation_precision_at_5_regression"] == 0.07
    assert reloaded["max_citation_precision_at_5_regression"] == 0.07
    assert reloaded["max_p95_latency_ms"] == 1500.0


def test_latency_gate_accepts_absolute_budget_when_local_baseline_is_tiny() -> None:
    baseline = {
        "recall_at_5": 1.0,
        "mrr_at_10": 1.0,
        "citation_hit_rate": 1.0,
        "citation_precision_at_5": 0.2,
        "citation_coverage": 1.0,
        "no_result_accuracy": 1.0,
        "positive_no_result_rate": 0.0,
        "p95_latency_ms": 5.0,
        "error_count": 0,
    }
    candidate = {**baseline, "p95_latency_ms": 1200.0}
    qualification = {"status": "qualified", "qualified": True}

    accepted = evaluate_promotion_gate(
        candidate,
        baseline=baseline,
        evidence_qualification=qualification,
    )
    rejected = evaluate_promotion_gate(
        {**candidate, "p95_latency_ms": 6200.0},
        baseline=baseline,
        evidence_qualification=qualification,
    )

    accepted_latency = next(
        item for item in accepted["checks"] if item["id"] == "max_p95_latency_ratio"
    )
    rejected_latency = next(
        item for item in rejected["checks"] if item["id"] == "max_p95_latency_ratio"
    )
    assert accepted_latency["passed"] is True
    assert accepted_latency["pass_mode"] == "absolute"
    assert rejected_latency["passed"] is False


def test_latency_gate_enforces_absolute_budget_without_baseline() -> None:
    metrics = {
        "recall_at_5": 1.0,
        "citation_coverage": 1.0,
        "no_result_accuracy": 1.0,
        "p95_latency_ms": 1_501.0,
        "error_count": 0,
    }

    gate = evaluate_promotion_gate(metrics, baseline=None)
    latency = next(
        item for item in gate["checks"] if item["id"] == "max_p95_latency_ratio"
    )

    assert latency["relative_baseline_available"] is False
    assert latency["pass_mode"] == "none"
    assert latency["passed"] is False
    assert gate["passed"] is False


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


def test_default_gate_rejects_false_positive_no_result_behavior() -> None:
    positive = evaluate_retrieval_case(
        [{"chunk_id": "answer", "source_document_id": "doc-a", "score": 0.9}],
        [{"document_id": "doc-a"}],
        ks=[1, 5, 10],
    )
    false_positive = evaluate_retrieval_case(
        [{"chunk_id": "noise", "source_document_id": "doc-z", "score": 0.1}],
        [],
        ks=[1, 5, 10],
        expected_no_result=True,
    )
    aggregate = aggregate_target_metrics([positive, false_positive], ks=[1, 5, 10])

    gate = evaluate_promotion_gate(aggregate, baseline=None)
    no_result_check = next(
        item for item in gate["checks"] if item["id"] == "min_no_result_accuracy"
    )

    assert aggregate["no_result_accuracy"] == 0.0
    assert no_result_check["threshold"] == 0.8
    assert no_result_check["passed"] is False
    assert gate["passed"] is False

    explicitly_disabled = evaluate_promotion_gate(
        aggregate,
        baseline=None,
        policy={"min_no_result_accuracy": 0.0},
    )
    assert explicitly_disabled["passed"] is True


@pytest.mark.asyncio
async def test_evaluation_gate_api_defaults_no_result_floor_and_allows_explicit_override(
    evaluation_runtime,
) -> None:
    client, _service, _pipeline_executor, _evaluation_executor, _registry = evaluation_runtime
    kb_id = await _create_kb(client, "default no-result gate")

    default_response = await client.get(f"/api/rag/evaluation-gate/{kb_id}")
    assert default_response.status_code == 200, default_response.text
    assert default_response.json()["min_no_result_accuracy"] == 0.8
    assert default_response.json()["max_citation_precision_at_5_regression"] == 0.02
    assert default_response.json()["max_p95_latency_ms"] == 1500.0

    payload = {
        "mode": "advisory",
        "min_recall_at_5": 0.8,
        "max_mrr_regression": 0.03,
        "max_citation_hit_regression": 0.02,
        "max_no_result_increase": 0.05,
        "min_citation_coverage": 0.0,
        "max_p95_latency_ratio": 2.0,
        "require_zero_errors": True,
    }
    omitted_response = await client.patch(
        f"/api/rag/evaluation-gate/{kb_id}",
        json=payload,
    )
    assert omitted_response.status_code == 200, omitted_response.text
    assert omitted_response.json()["min_no_result_accuracy"] == 0.8
    assert omitted_response.json()["max_citation_precision_at_5_regression"] == 0.02

    override_response = await client.patch(
        f"/api/rag/evaluation-gate/{kb_id}",
        json={**payload, "min_no_result_accuracy": 0.0},
    )
    assert override_response.status_code == 200, override_response.text
    assert override_response.json()["min_no_result_accuracy"] == 0.0


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
    assert completed["evidence_qualification"]["status"] == "diagnostic_only"
    assert candidate["promotion_gate"]["passed"] is False
    evidence = candidate["version_evidence"]
    assert evidence["version_id"] == candidate_version
    assert evidence["version_fingerprint"]
    assert evidence["embedding"]["effective"]["provider"] == "hash"
    assert evidence["embedding"]["effective"]["model"] == "deterministic-hash-v1"
    receipt = candidate["case_results"][0]["retrieval_receipt"]
    assert receipt["embedding_provider"] == "hash"
    assert receipt["embedding_model"] == "deterministic-hash-v1"
    assert receipt["embedding_dimension"] == 128
    assert receipt["rerank_provider_used"] == "none"
    serialized = str(completed).lower()
    assert "project orion deployment requires a signed" not in serialized
    assert "api_key" not in serialized
    assert "endpoint" not in serialized
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
    assert promoted.status_code == 409, promoted.text

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
