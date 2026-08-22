from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

import server.rag.api as rag_api
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
    EvaluationStateError,
    KnowledgeEvaluationStore,
    aggregate_target_metrics,
    assess_formal_evidence_independence,
    build_development_evidence_manifest,
    build_paired_execution_schedule,
    evaluate_promotion_gate,
    evaluate_retrieval_case,
    gold_v2_leakage_receipt,
    gold_v2_review_admission_blockers,
    paired_primary_confidence_report,
    qualify_promotion_evidence,
)
from server.rag.evaluation_executor import KnowledgeEvaluationExecutor
from server.rag.pipeline_executor import KnowledgePipelineExecutor
from server.rag.rag_service import RagService
from server.rag.runtime_identity import (
    build_rag_runtime_identity,
    is_valid_rag_runtime_identity,
    rag_runtime_identity,
)
from server.rag.strategy_tuning_qualification import build_tuning_readiness
from server.rag.vector_store import LocalJsonVectorStore, StoredVectorChunk
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


def _formal_executor_run(*, receipt_top_k: int = 5) -> tuple[dict, dict]:
    runtime = rag_runtime_identity()
    retrieval = {
        "mode": "hybrid",
        "top_k": 5,
        "score_threshold": 0.0,
        "vector_weight": 0.7,
        "fulltext_weight": 0.3,
        "candidate_multiplier": 4,
        "per_document_limit": 2,
        "rerank_enabled": False,
        "rerank_provider": "none",
        "rerank_model": "",
        "rerank_top_n": 5,
        "abstention_enabled": False,
        "abstention_score_domain": "vector_score",
        "abstention_threshold": 0.0,
    }
    target = {
        "target_id": "candidate",
        "version_id": "candidate",
        "retrieval": copy.deepcopy(retrieval),
        "version_evidence": {
            "retrieval": copy.deepcopy(retrieval),
            "runtime": copy.deepcopy(runtime),
        },
    }
    run = {
        "run_id": "evalrun-formal-runtime-contract",
        "kb_id": "kb-formal-runtime-contract",
        "eval_set_id": "evalset-formal-runtime-contract",
        "eval_set_snapshot": {
            "cases": [
                {
                    "case_id": "case-1",
                    "query": "Which source is relevant?",
                    "expected_refs": [{"document_id": "doc-a"}],
                    "expected_no_result": False,
                    "targeting": {"locale": "en-US"},
                }
            ]
        },
        "targets": [target],
        "execution_schedule": [{"case_id": "case-1", "target_id": "candidate"}],
        "execution_seed": 7,
        "baseline_version_id": "candidate",
        "ks": [1, 5, 10],
        "gate_policy": {"min_recall_at_5": 0.0, "min_no_result_accuracy": 0.0},
        "evidence_qualification": {"status": "qualified", "qualified": True},
        "run_mode": "formal",
        "comparability": {"comparable": True, "reasons": []},
        "execution_manifest": {
            "version": "rag-eval-v2",
            "abstention_contract_version": "rag-abstention-v1",
            "observation_depth": 10,
            "target_fingerprints": [
                {
                    "version_id": "candidate",
                    "retrieval": copy.deepcopy(retrieval),
                    "runtime": copy.deepcopy(runtime),
                }
            ],
            "runtime": copy.deepcopy(runtime),
        },
        "case_results": {},
        "receipt_top_k": receipt_top_k,
    }
    return run, target


class _FormalExecutorStore:
    def __init__(self, run: dict) -> None:
        self.run = run

    def get_run(self, _run_id: str) -> dict:
        return self.run

    def cancel_requested(self, _run_id: str) -> bool:
        return False

    def record_case_result(
        self, _run_id: str, target_id: str, case_id: str, result: dict
    ) -> None:
        self.run.setdefault("case_results", {}).setdefault(target_id, {})[case_id] = result

    def complete_run(self, _run_id: str, aggregates: list[dict]) -> dict:
        self.run["status"] = "succeeded"
        self.run["target_results"] = aggregates
        return self.run

    def fail_run(self, _run_id: str, error: str) -> None:
        self.run["status"] = "failed"
        self.run["error"] = error


class _RecordingFormalService:
    def __init__(
        self,
        *,
        receipt_top_k: int,
        include_abstention_receipt: bool = True,
        sources: list[dict] | None = None,
        receipt_overrides: dict | None = None,
        runtime_identity: dict | None = None,
    ) -> None:
        self.receipt_top_k = receipt_top_k
        self.include_abstention_receipt = include_abstention_receipt
        self.sources = (
            copy.deepcopy(sources)
            if sources is not None
            else [{"chunk_id": "chunk-a", "source_document_id": "doc-a"}]
        )
        self.receipt_overrides = copy.deepcopy(receipt_overrides or {})
        self.runtime_identity = copy.deepcopy(
            runtime_identity or rag_runtime_identity()
        )
        self.calls: list[dict] = []

    async def query_pipeline_version(self, _version_id: str, _query: str, **kwargs) -> dict:
        self.calls.append(copy.deepcopy(kwargs))
        retrieval = dict(kwargs.get("retrieval") or {})
        receipt = {
            **retrieval,
            "top_k": self.receipt_top_k,
            "candidate_limit": self.receipt_top_k
            * int(retrieval.get("candidate_multiplier") or 1),
            "observation_depth": int(kwargs.get("observation_depth") or self.receipt_top_k),
        }
        if self.include_abstention_receipt:
            receipt.update(
                {
                    "abstention_applied": False,
                    "abstained": False,
                    "abstention_score": None,
                    "abstention_input_count": len(self.sources),
                    "abstention_reason": "disabled",
                }
            )
        receipt.update(self.receipt_overrides)
        return {
            "sources": copy.deepcopy(self.sources),
            "warnings": [],
            "retrieval": receipt,
        }

    def pipeline_version_evidence(self, _version_id: str) -> dict:
        return {"runtime": copy.deepcopy(self.runtime_identity)}

    @staticmethod
    def _safe_pipeline_error(exc: Exception) -> str:
        return str(exc)


@pytest.mark.asyncio
async def test_formal_executor_keeps_profile_top_k_and_separates_observation_depth() -> None:
    run, _target = _formal_executor_run()
    store = _FormalExecutorStore(run)
    service = _RecordingFormalService(receipt_top_k=5)

    await KnowledgeEvaluationExecutor(service, store)._execute(run)

    assert service.calls == [
        {
            "top_k": 5,
            "retrieval": run["targets"][0]["retrieval"],
            "observation_depth": 10,
            "generate_answer": False,
        }
    ]
    case_result = run["case_results"]["candidate"]["case-1"]
    assert case_result["status"] == "completed"
    assert case_result["retrieval_receipt"]["candidate_limit"] == 20


@pytest.mark.asyncio
async def test_formal_executor_rejects_runtime_change_after_queue(
    tmp_path: Path,
) -> None:
    run, _target = _formal_executor_run()
    source_dir = tmp_path / "different-runtime"
    source_dir.mkdir()
    (source_dir / "runtime.py").write_text("VERSION = 2\n", encoding="utf-8")
    service = _RecordingFormalService(
        receipt_top_k=5,
        runtime_identity=build_rag_runtime_identity(source_dir),
    )

    await KnowledgeEvaluationExecutor(service, _FormalExecutorStore(run))._execute(run)

    assert run["status"] == "failed"
    assert "runtime changed" in str(run["error"]).lower()
    assert service.calls == []


@pytest.mark.asyncio
async def test_formal_executor_rejects_receipt_that_differs_from_manifest() -> None:
    run, _target = _formal_executor_run(receipt_top_k=10)
    store = _FormalExecutorStore(run)
    service = _RecordingFormalService(receipt_top_k=10)

    await KnowledgeEvaluationExecutor(service, store)._execute(run)

    case_result = run["case_results"]["candidate"]["case-1"]
    assert case_result["status"] == "failed"
    assert "retrieval receipt" in case_result["error"].lower()
    assert run["target_results"][0]["promotion_gate"]["passed"] is False


@pytest.mark.asyncio
async def test_formal_executor_rejects_missing_explicit_abstention_receipt() -> None:
    run, _target = _formal_executor_run()
    store = _FormalExecutorStore(run)
    service = _RecordingFormalService(
        receipt_top_k=5, include_abstention_receipt=False
    )

    await KnowledgeEvaluationExecutor(service, store)._execute(run)

    case_result = run["case_results"]["candidate"]["case-1"]
    assert case_result["status"] == "failed"
    assert "abstained" in case_result["error"].lower()


@pytest.mark.asyncio
async def test_formal_executor_rejects_abstention_decision_inconsistent_with_sources() -> None:
    run, _target = _formal_executor_run()
    store = _FormalExecutorStore(run)
    service = _RecordingFormalService(receipt_top_k=5, sources=[])

    await KnowledgeEvaluationExecutor(service, store)._execute(run)

    case_result = run["case_results"]["candidate"]["case-1"]
    assert case_result["status"] == "failed"
    assert "abstained" in case_result["error"].lower()


@pytest.mark.asyncio
async def test_formal_executor_rejects_boolean_abstention_input_count() -> None:
    run, _target = _formal_executor_run()
    store = _FormalExecutorStore(run)
    service = _RecordingFormalService(
        receipt_top_k=5,
        receipt_overrides={"abstention_input_count": True},
    )

    await KnowledgeEvaluationExecutor(service, store)._execute(run)

    case_result = run["case_results"]["candidate"]["case-1"]
    assert case_result["status"] == "failed"
    assert "abstention_input_count" in case_result["error"]


@pytest.mark.asyncio
async def test_formal_executor_accepts_explicit_no_candidates_without_verifier_call() -> None:
    run, _target = _formal_executor_run()
    evidence_retrieval = {
        **run["targets"][0]["retrieval"],
        "rerank_enabled": True,
        "rerank_provider": "llm",
        "rerank_model": "support-verifier",
        "evidence_verification_enabled": True,
        "abstention_score_domain": "evidence_verdict_v1",
    }
    run["targets"][0]["retrieval"] = copy.deepcopy(evidence_retrieval)
    run["targets"][0]["version_evidence"]["retrieval"] = copy.deepcopy(
        evidence_retrieval
    )
    run["execution_manifest"]["target_fingerprints"][0]["retrieval"] = copy.deepcopy(
        evidence_retrieval
    )
    store = _FormalExecutorStore(run)
    service = _RecordingFormalService(
        receipt_top_k=5,
        sources=[],
        receipt_overrides={
            "abstention_enabled": False,
            "abstention_applied": True,
            "abstained": True,
            "abstention_input_count": 0,
            "abstention_reason": "no_candidates",
            "evidence_verification_enabled": True,
            "evidence_verification_applied": False,
            "evidence_verdict": "unavailable",
            "evidence_provider": "none",
            "evidence_model": "",
        },
    )

    await KnowledgeEvaluationExecutor(service, store)._execute(run)

    case_result = run["case_results"]["candidate"]["case-1"]
    assert case_result["status"] == "completed"
    assert case_result["no_result"] is True
    assert case_result["retrieval_receipt"]["abstention_reason"] == "no_candidates"


@pytest.mark.asyncio
async def test_formal_executor_accepts_valid_verifier_abstain_with_nonzero_input() -> None:
    run, _target = _formal_executor_run()
    evidence_retrieval = {
        **run["targets"][0]["retrieval"],
        "rerank_enabled": True,
        "rerank_provider": "llm",
        "rerank_model": "support-verifier",
        "evidence_verification_enabled": True,
        "abstention_score_domain": "evidence_verdict_v1",
    }
    run["targets"][0]["retrieval"] = copy.deepcopy(evidence_retrieval)
    run["targets"][0]["version_evidence"]["retrieval"] = copy.deepcopy(
        evidence_retrieval
    )
    run["execution_manifest"]["target_fingerprints"][0]["retrieval"] = copy.deepcopy(
        evidence_retrieval
    )
    run["eval_set_snapshot"]["cases"][0].update(
        {"expected_refs": [], "expected_no_result": True}
    )
    store = _FormalExecutorStore(run)
    service = _RecordingFormalService(
        receipt_top_k=5,
        sources=[],
        receipt_overrides={
            "abstention_enabled": False,
            "abstention_applied": True,
            "abstained": True,
            "abstention_score_domain": "evidence_verdict_v1",
            "abstention_input_count": 5,
            "abstention_reason": "requested_fact_absent",
            "evidence_verification_enabled": True,
            "evidence_verification_applied": True,
            "evidence_verdict": "abstain",
            "evidence_provider": "llm",
            "evidence_model": "support-verifier",
        },
    )

    await KnowledgeEvaluationExecutor(service, store)._execute(run)

    case_result = run["case_results"]["candidate"]["case-1"]
    assert case_result["status"] == "completed"
    assert case_result["no_result"] is True
    assert case_result["metrics"]["no_result_accuracy"] == 1.0
    assert case_result["retrieval_receipt"]["abstention_input_count"] == 5
    assert case_result["retrieval_receipt"]["evidence_verdict"] == "abstain"


@pytest.mark.asyncio
async def test_formal_executor_rejects_no_candidates_bypass_with_nonzero_input() -> None:
    run, _target = _formal_executor_run()
    evidence_retrieval = {
        **run["targets"][0]["retrieval"],
        "rerank_enabled": True,
        "rerank_provider": "llm",
        "rerank_model": "support-verifier",
        "evidence_verification_enabled": True,
        "abstention_score_domain": "evidence_verdict_v1",
    }
    run["targets"][0]["retrieval"] = copy.deepcopy(evidence_retrieval)
    run["targets"][0]["version_evidence"]["retrieval"] = copy.deepcopy(
        evidence_retrieval
    )
    run["execution_manifest"]["target_fingerprints"][0]["retrieval"] = copy.deepcopy(
        evidence_retrieval
    )
    store = _FormalExecutorStore(run)
    service = _RecordingFormalService(
        receipt_top_k=5,
        sources=[],
        receipt_overrides={
            "abstention_enabled": False,
            "abstention_applied": True,
            "abstained": True,
            "abstention_input_count": 1,
            "abstention_reason": "no_candidates",
            "evidence_verification_enabled": True,
            "evidence_verification_applied": False,
            "evidence_verdict": "unavailable",
            "evidence_provider": "none",
            "evidence_model": "",
            "rerank_fallback_reason": "llm:invalid_provider_response",
        },
    )

    await KnowledgeEvaluationExecutor(service, store)._execute(run)

    case_result = run["case_results"]["candidate"]["case-1"]
    assert case_result["status"] == "failed"
    assert "evidence_verification_applied" in case_result["error"]
    assert case_result["retrieval_receipt"]["rerank_fallback_reason"] == (
        "llm:invalid_provider_response"
    )


def test_no_result_metric_uses_explicit_abstention_instead_of_source_emptiness() -> None:
    explicitly_rejected = evaluate_retrieval_case(
        [
            {
                "chunk_id": "near-context",
                "source_document_id": "doc-near",
                "fused_score": 1.0,
                "vector_score": 0.55,
            }
        ],
        [],
        ks=[1, 5, 10],
        expected_no_result=True,
        retrieval_receipt={
            "abstention_enabled": True,
            "abstention_applied": True,
            "abstained": True,
            "abstention_score_domain": "vector_score",
            "abstention_threshold": 0.7,
            "abstention_score": 0.55,
            "abstention_input_count": 1,
            "abstention_reason": "below_threshold",
        },
    )
    empty_but_not_rejected = evaluate_retrieval_case(
        [],
        [],
        ks=[1, 5, 10],
        expected_no_result=True,
        retrieval_receipt={
            "abstention_enabled": False,
            "abstention_applied": False,
            "abstained": False,
            "abstention_score_domain": "vector_score",
            "abstention_threshold": 0.0,
            "abstention_score": None,
            "abstention_input_count": 0,
            "abstention_reason": "disabled",
        },
    )

    assert explicitly_rejected["metrics"]["no_result_accuracy"] == 1.0
    assert explicitly_rejected["abstention_contract"] == "explicit"
    assert empty_but_not_rejected["metrics"]["no_result_accuracy"] == 0.0


def test_evaluation_receipt_preserves_only_sanitized_provider_latency_fields() -> None:
    result = evaluate_retrieval_case(
        [],
        [],
        ks=[1, 5, 10],
        expected_no_result=True,
        retrieval_receipt={
            "rerank_provider_http_elapsed_ms": 123.4,
            "rerank_provider_prompt_tokens": 321,
            "rerank_provider_completion_tokens": 45,
            "rerank_provider_total_tokens": 366,
            "rerank_provider_response_char_count": 108,
            "raw_provider_response": "must-not-be-retained",
        },
    )

    receipt = result["retrieval_receipt"]
    assert receipt["rerank_provider_http_elapsed_ms"] == 123.4
    assert receipt["rerank_provider_prompt_tokens"] == 321
    assert receipt["rerank_provider_completion_tokens"] == 45
    assert receipt["rerank_provider_total_tokens"] == 366
    assert receipt["rerank_provider_response_char_count"] == 108
    assert "raw_provider_response" not in receipt


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


def test_mrr_at_10_remains_available_when_request_includes_larger_k() -> None:
    result = evaluate_retrieval_case(
        [
            {"chunk_id": "miss", "document_id": "other"},
            {"chunk_id": "hit", "document_id": "gold"},
        ],
        [{"document_id": "gold", "match_mode": "document", "relevance": 3}],
        ks=[5, 10, 20],
        latency_ms=1,
    )
    aggregate = aggregate_target_metrics([result], ks=[5, 10, 20])

    assert result["metrics"]["mrr_at_10"] == 0.5
    assert result["metrics"]["mrr_at_20"] == 0.5
    assert aggregate["mrr_at_10"] == 0.5
    assert aggregate["mrr_at_20"] == 0.5


def test_failed_cases_remain_in_quality_and_latency_denominators() -> None:
    completed_positive = evaluate_retrieval_case(
        [{"chunk_id": "answer", "doc_id": "doc-answer"}],
        [{"document_id": "doc-answer"}],
        ks=[1, 5, 10],
        latency_ms=10,
    )
    failed_positive = {
        "status": "failed",
        "metrics": {},
        "expected_no_result": False,
        "no_result": True,
        "latency_ms": 900,
    }
    completed_negative = evaluate_retrieval_case(
        [], [], ks=[1, 5, 10], expected_no_result=True, latency_ms=20
    )
    failed_negative = {
        "status": "failed",
        "metrics": {},
        "expected_no_result": True,
        "no_result": True,
        "latency_ms": 800,
    }

    aggregate = aggregate_target_metrics(
        [completed_positive, failed_positive, completed_negative, failed_negative],
        ks=[1, 5, 10],
    )

    assert aggregate["expected_case_count"] == 4
    assert aggregate["completed_case_count"] == 2
    assert aggregate["failed_case_count"] == 2
    assert aggregate["positive_quality_denominator"] == 2
    assert aggregate["no_result_quality_denominator"] == 2
    assert aggregate["recall_at_5"] == 0.5
    assert aggregate["citation_precision_at_5"] == 0.1
    assert aggregate["no_result_accuracy"] == 0.5
    assert aggregate["p95_latency_ms"] == 900.0


def test_paired_schedule_is_seeded_and_keeps_targets_adjacent() -> None:
    cases = [{"case_id": f"case-{index}"} for index in range(8)]
    targets = [{"target_id": "baseline"}, {"target_id": "candidate"}]

    first = build_paired_execution_schedule(cases, targets, seed=41)
    repeated = build_paired_execution_schedule(cases, targets, seed=41)
    changed = build_paired_execution_schedule(cases, targets, seed=42)

    assert first == repeated
    assert first != changed
    assert len(first) == 16
    for index in range(0, len(first), 2):
        assert first[index]["case_id"] == first[index + 1]["case_id"]
        assert {first[index]["target_id"], first[index + 1]["target_id"]} == {
            "baseline",
            "candidate",
        }


def test_corpus_snapshot_uses_parent_block_not_chunk_slices() -> None:
    class VectorStore:
        def __init__(self, child_text: str) -> None:
            self.child_text = child_text

        def list_document_chunks(self, _namespace: str):
            return [
                StoredVectorChunk(
                    chunk_id="child",
                    kb_id="kb",
                    doc_id="v-doc",
                    document_name="doc.md",
                    text=self.child_text,
                    chunk_index=0,
                    chunk_type="child",
                    source_block_id="block-1",
                ),
                StoredVectorChunk(
                    chunk_id="parent",
                    kb_id="kb",
                    doc_id="v-doc",
                    document_name="doc.md",
                    text="Stable complete source block text.",
                    chunk_index=1,
                    chunk_type="parent",
                    source_block_id="block-1",
                ),
            ]

    class Service:
        def __init__(self, child_text: str) -> None:
            self.vector_store = VectorStore(child_text)

        @staticmethod
        def get_pipeline_version(_version_id: str):
            return {
                "kb_id": "kb",
                "document_results": [
                    {
                        "source_id": "doc",
                        "status": "completed",
                        "content_hash": "a" * 64,
                    }
                ],
            }

        @staticmethod
        def _mapping_sha256(value):
            return _gold_v2_hash(value)

    before = RagService.pipeline_corpus_snapshot(Service("first child slice"), "v")
    after = RagService.pipeline_corpus_snapshot(Service("different child boundaries"), "v")

    assert before == after


def test_corpus_snapshot_recovers_document_hash_from_knowledge_metadata() -> None:
    class VectorStore:
        @staticmethod
        def list_document_chunks(_namespace: str):
            return [
                StoredVectorChunk(
                    chunk_id="parent",
                    kb_id="kb",
                    doc_id="v-doc",
                    document_name="doc.md",
                    text="Stable complete source block text.",
                    chunk_index=0,
                    chunk_type="parent",
                    source_block_id="block-1",
                )
            ]

    class Service:
        vector_store = VectorStore()

        @staticmethod
        def get_pipeline_version(_version_id: str):
            return {
                "kb_id": "kb",
                "document_results": [
                    {
                        "source_id": "doc",
                        "status": "completed",
                    }
                ],
            }

        @staticmethod
        def _read_metadata():
            return {
                "documents": {
                    "doc": {
                        "kb_id": "kb",
                        "content_hash": "a" * 64,
                    }
                }
            }

        @staticmethod
        def _mapping_sha256(value):
            return _gold_v2_hash(value)

    snapshot = RagService.pipeline_corpus_snapshot(Service(), "v")

    assert snapshot["corpus_snapshot"]["documents"] == [
        {"document_id": "doc", "content_hash": "a" * 64}
    ]


def test_paired_bootstrap_is_deterministic_and_ci_can_block_point_estimate() -> None:
    cases = [
        {
            "case_id": f"case-{index}",
            "expected_no_result": index >= 30,
            "targeting": {
                "locale": "zh-CN" if index % 2 == 0 else "en-US",
            },
        }
        for index in range(42)
    ]
    baseline = {
        case["case_id"]: {
            "status": "completed",
            "metrics": {
                "recall_at_5": 1.0,
                "no_result_accuracy": 1.0,
            },
        }
        for case in cases
    }
    candidate = copy.deepcopy(baseline)
    for index in (0, 1, 30):
        key = f"case-{index}"
        metric_name = "no_result_accuracy" if index >= 30 else "recall_at_5"
        candidate[key]["metrics"][metric_name] = 0.0
    for index in (2, 3, 31):
        key = f"case-{index}"
        metric_name = "no_result_accuracy" if index >= 30 else "recall_at_5"
        baseline[key]["metrics"][metric_name] = 0.0

    first = paired_primary_confidence_report(
        cases, baseline, candidate, seed=17, iterations=10_000
    )
    repeated = paired_primary_confidence_report(
        cases, baseline, candidate, seed=17, iterations=10_000
    )
    assert first == repeated
    assert first["point_estimate"] == 0.0
    assert first["ci_lower"] < -0.03

    metrics = {
        "recall_at_5": 0.9,
        "mrr_at_10": 0.9,
        "citation_precision_at_5": 0.2,
        "citation_coverage": 1.0,
        "no_result_accuracy": 0.9,
        "positive_no_result_rate": 0.0,
        "p95_latency_ms": 10.0,
        "error_count": 0,
    }
    gate = evaluate_promotion_gate(
        metrics,
        baseline=metrics,
        paired_confidence=first,
        policy={"max_paired_primary_regression": 0.03},
    )
    assert next(
        check for check in gate["checks"] if check["id"] == "paired_primary_non_inferiority"
    )["passed"] is False
    assert gate["passed"] is False


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
    assert qualified["status"] == "diagnostic_only"
    assert qualified["qualified"] is False
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


def _gold_v2_hash(value: object) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _gold_v2_cases(*, approved: bool = True) -> list[dict]:
    query_types = [
        "factual_lookup",
        "paraphrase",
        "section_context",
        "cross_language",
        "multi_evidence",
        "confusable_content",
    ]
    cases: list[dict] = []
    for index in range(30):
        locale = "zh-CN" if index < 15 else "en-US"
        cases.append(
            {
                "query": f"{locale} grounded benchmark question {index}",
                "expected_refs": [
                    {
                        "document_id": f"doc-{index % 3}",
                        "chunk_id": f"chunk-{index}",
                        "source_block_id": f"positive-block-{index}",
                        "match_mode": "source_block",
                        "relevance": 3,
                    }
                ],
                "expected_no_result": False,
                "review_status": "approved" if approved else "pending",
                "review_evidence": (
                    {
                        "source": "manual_ui",
                        "decision": "approved",
                        "reviewed_at": 1000.0 + index,
                        "dataset_revision": 1,
                        "reason": "Grounding verified against the displayed source block.",
                    }
                    if approved
                    else {}
                ),
                "tags": [query_types[index // 5], locale],
                "targeting": {
                    "query_type": query_types[index // 5],
                    "locale": locale,
                    "evidence_ids": [f"evidence-{index}"],
                    "leakage": {
                        "max_normalized_copy": 0,
                        "warning": False,
                        "blocked": False,
                    },
                },
            }
        )
    for index in range(12):
        locale = "zh-CN" if index < 6 else "en-US"
        cases.append(
            {
                "query": f"{locale} absent but corpus-near question {index}",
                "expected_refs": [],
                "expected_no_result": True,
                "review_status": "approved" if approved else "pending",
                "review_evidence": (
                    {
                        "source": "manual_ui",
                        "decision": "approved",
                        "reviewed_at": 2000.0 + index,
                        "dataset_revision": 1,
                        "reason": "Confirmed that the answer is absent from the fixed corpus.",
                    }
                    if approved
                    else {}
                ),
                "tags": ["no_result", "corpus_near", "hard_negative", locale],
                "targeting": {
                    "query_type": "no_result",
                    "locale": locale,
                    "context_refs": [
                        {
                            "document_id": f"doc-{index % 3}",
                            "chunk_id": f"negative-chunk-{index}",
                            "source_block_id": f"negative-block-{index}",
                        }
                    ],
                },
            }
        )
    return cases


def _gold_v2_provenance() -> dict:
    corpus_snapshot = {
        "kb_id": "kb-gold-v2",
        "documents": [
            {
                "document_id": f"doc-{index}",
                "content_hash": _gold_v2_hash(f"document-{index}"),
            }
            for index in range(3)
        ],
        "source_blocks": [
            {
                "document_id": f"doc-{index % 3}",
                "source_block_id": f"positive-block-{index}",
                "content_hash": _gold_v2_hash(f"positive-block-{index}"),
            }
            for index in range(30)
        ]
        + [
            {
                "document_id": f"doc-{index % 3}",
                "source_block_id": f"negative-block-{index}",
                "content_hash": _gold_v2_hash(f"negative-block-{index}"),
            }
            for index in range(12)
        ],
    }
    return {
        "benchmark_contract_version": "rag-gold-v2",
        "evidence_policy_version": "content-source-block-v1",
        "generator": "test-generator",
        "generator_model_id": "test/model",
        "seed": 17,
        "generation_prompt_hash": "a" * 64,
        "evidence_hash": "b" * 64,
        "blueprint_hash": "c" * 64,
        "generation_attempts": [{"attempt": "initial", "error_code": None}],
        "source_pipeline_version_id": "pipeline-source-only",
        "corpus_snapshot": corpus_snapshot,
        "corpus_snapshot_hash": _gold_v2_hash(corpus_snapshot),
    }


def test_gold_v2_publish_requires_every_case_review_and_protects_integrity(
    tmp_path: Path,
) -> None:
    store = KnowledgeEvaluationStore(tmp_path / "evaluations.json")
    draft = store.create_generated_set(
        "kb-gold-v2",
        "Gold v2",
        "",
        cases=_gold_v2_cases(approved=False),
        provenance=_gold_v2_provenance(),
        coverage={},
        calibration={"status": "calibrated", "dataset_revision": 1},
        benchmark_role="promotion_sealed",
    )
    with pytest.raises(EvaluationStateError, match="42 cases require explicit review"):
        store.publish_set(draft["eval_set_id"], expected_revision=1)

    approved = store.create_generated_set(
        "kb-gold-v2",
        "Gold v2 approved",
        "",
        cases=_gold_v2_cases(),
        provenance=_gold_v2_provenance(),
        coverage={},
        calibration={"status": "calibrated", "dataset_revision": 1},
        benchmark_role="promotion_sealed",
    )
    published = store.publish_set(approved["eval_set_id"], expected_revision=1)
    assert published["benchmark_contract_version"] == "rag-gold-v2"
    assert published["qualification_manifest"]["version"] == "rag-gold-v2-qualification-v2"
    assert published["qualification_manifest"]["qualified"] is True
    assert qualify_promotion_evidence(published)["qualified"] is True
    listed = store.list_set_versions(approved["eval_set_id"])
    assert listed[0]["evidence_qualification"]["qualified"] is True
    with pytest.raises(EvaluationStateError, match="identical sealed Gold"):
        store.publish_set(approved["eval_set_id"], expected_revision=1)

    tampered = copy.deepcopy(published)
    tampered["provenance"]["seed"] = 18
    qualification = qualify_promotion_evidence(tampered)
    assert qualification["qualified"] is False
    assert next(
        check for check in qualification["checks"] if check["id"] == "published_checksum"
    )["passed"] is False


def test_calibration_fork_is_target_bound_and_clears_every_review(
    tmp_path: Path,
) -> None:
    store = KnowledgeEvaluationStore(tmp_path / "evaluations.json")
    source = store.create_generated_set(
        "kb-gold-v2",
        "Independent Gold",
        "",
        cases=_gold_v2_cases(),
        provenance=_gold_v2_provenance(),
        coverage={"source": "independent-holdout"},
        calibration={"status": "not_required", "dataset_revision": 1},
        benchmark_role="promotion_sealed",
    )
    published = store.publish_set(source["eval_set_id"], expected_revision=1)

    forked = store.fork_calibration_set(
        source["eval_set_id"],
        source_version=published["version"],
        target_pipeline_version_id="kpv-target",
        target_corpus_snapshot={
            "corpus_snapshot": published["corpus_snapshot"],
            "corpus_snapshot_hash": published["corpus_snapshot_hash"],
        },
    )

    assert forked["origin"] == "generated"
    assert forked["benchmark_role"] == "calibration"
    assert forked["provenance"]["benchmark_contract_version"] == (
        "rag-calibration-v1"
    )
    assert forked["provenance"]["pipeline_version_id"] == "kpv-target"
    assert forked["provenance"]["source_evidence"]["checksum"] == published["checksum"]
    assert {item["case_id"] for item in forked["cases"]}.isdisjoint(
        {item["case_id"] for item in published["cases"]}
    )
    assert all(item["review_status"] == "pending" for item in forked["cases"])
    assert all(item["review_evidence"] == {} for item in forked["cases"])
    with pytest.raises(EvaluationStateError, match="manual review"):
        store.publish_set(forked["eval_set_id"], expected_revision=1)

    reviewed = forked
    for case in [item for item in forked["cases"] if item["expected_no_result"]]:
        reviewed = store.update_case(
            forked["eval_set_id"],
            case["case_id"],
            expected_revision=reviewed["revision"],
            values={
                "review_status": "approved",
                "review_evidence": {
                    "source": "manual_ui",
                    "decision": "approved",
                    "reviewed_at": 3000.0 + reviewed["revision"],
                    "dataset_revision": reviewed["revision"],
                    "reason": "Confirmed absent against the displayed corpus-near block.",
                },
            },
        )
    calibration_version = store.publish_set(
        forked["eval_set_id"],
        expected_revision=reviewed["revision"],
    )
    assert calibration_version["benchmark_contract_version"] == "rag-calibration-v1"
    assert build_tuning_readiness(
        calibration_version,
        target_version_id="kpv-target",
    )["selection_eligible"] is True

    tampered = copy.deepcopy(calibration_version)
    tampered["provenance"]["source_evidence"]["checksum"] = "0" * 64
    tampered_readiness = build_tuning_readiness(
        tampered,
        target_version_id="kpv-target",
    )
    checksum_check = next(
        item
        for item in tampered_readiness["checks"]
        if item["check_id"] == "published_checksum"
    )
    assert checksum_check["passed"] is False
    assert tampered_readiness["selection_eligible"] is False


def test_calibration_fork_accepts_sealed_gold_from_before_freshness_manifest(
    tmp_path: Path,
) -> None:
    store = KnowledgeEvaluationStore(tmp_path / "evaluations.json")
    source = store.create_generated_set(
        "kb-gold-v2",
        "Legacy independent Gold",
        "",
        cases=_gold_v2_cases(),
        provenance=_gold_v2_provenance(),
        coverage={"source": "independent-holdout"},
        calibration={"status": "not_required", "dataset_revision": 1},
        benchmark_role="promotion_sealed",
    )
    published = store.publish_set(source["eval_set_id"], expected_revision=1)

    data = json.loads(store.path.read_text(encoding="utf-8"))
    sealed = data["versions"][published["version_id"]]
    sealed.pop("freshness_manifest")
    legacy_checksum_payload = {
        key: sealed.get(key) or {}
        for key in (
            "benchmark_contract_version",
            "benchmark_role",
            "cases",
            "provenance",
            "coverage",
            "calibration",
            "corpus_snapshot",
            "qualification_manifest",
        )
    }
    sealed["checksum"] = hashlib.sha256(
        json.dumps(
            legacy_checksum_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    store.path.write_text(json.dumps(data), encoding="utf-8")

    forked = store.fork_calibration_set(
        source["eval_set_id"],
        source_version=published["version"],
        target_pipeline_version_id="kpv-target",
        target_corpus_snapshot={
            "corpus_snapshot": published["corpus_snapshot"],
            "corpus_snapshot_hash": published["corpus_snapshot_hash"],
        },
    )
    assert forked["provenance"]["source_evidence"]["checksum"] == sealed["checksum"]

    data = json.loads(store.path.read_text(encoding="utf-8"))
    data["versions"][published["version_id"]]["provenance"]["seed"] = 99
    store.path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(EvaluationStateError, match="checksum validation failed"):
        store.fork_calibration_set(
            source["eval_set_id"],
            source_version=published["version"],
            target_pipeline_version_id="kpv-target",
            target_corpus_snapshot={
                "corpus_snapshot": published["corpus_snapshot"],
                "corpus_snapshot_hash": published["corpus_snapshot_hash"],
            },
        )


def test_calibration_fork_rejects_a_different_corpus(tmp_path: Path) -> None:
    store = KnowledgeEvaluationStore(tmp_path / "evaluations.json")
    source = store.create_generated_set(
        "kb-gold-v2",
        "Independent Gold",
        "",
        cases=_gold_v2_cases(),
        provenance=_gold_v2_provenance(),
        coverage={},
        calibration={"status": "not_required", "dataset_revision": 1},
        benchmark_role="promotion_sealed",
    )
    published = store.publish_set(source["eval_set_id"], expected_revision=1)

    with pytest.raises(EvaluationStateError, match="same corpus snapshot"):
        store.fork_calibration_set(
            source["eval_set_id"],
            source_version=published["version"],
            target_pipeline_version_id="kpv-target",
            target_corpus_snapshot={
                "corpus_snapshot": {},
                "corpus_snapshot_hash": "f" * 64,
            },
        )


@pytest.mark.asyncio
async def test_calibration_fork_api_resolves_target_corpus_server_side(
    evaluation_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, service, _, _, _ = evaluation_runtime
    store = rag_api.get_evaluation_store()
    source = store.create_generated_set(
        "kb-gold-v2",
        "Independent Gold",
        "",
        cases=_gold_v2_cases(),
        provenance=_gold_v2_provenance(),
        coverage={},
        calibration={"status": "not_required", "dataset_revision": 1},
        benchmark_role="promotion_sealed",
    )
    published = store.publish_set(source["eval_set_id"], expected_revision=1)
    monkeypatch.setattr(
        service,
        "get_pipeline_version",
        lambda version_id: {
            "version_id": version_id,
            "kb_id": "kb-gold-v2",
            "status": "ready",
        },
    )
    monkeypatch.setattr(
        service,
        "pipeline_corpus_snapshot",
        lambda _version_id: {
            "corpus_snapshot": published["corpus_snapshot"],
            "corpus_snapshot_hash": published["corpus_snapshot_hash"],
        },
    )

    response = await client.post(
        f"/api/rag/evaluation-sets/{source['eval_set_id']}/versions/1/fork-calibration",
        json={"target_pipeline_version_id": "kpv-target"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["provenance"]["pipeline_version_id"] == "kpv-target"
    assert payload["provenance"]["corpus_snapshot_hash"] == published[
        "corpus_snapshot_hash"
    ]


@pytest.mark.asyncio
async def test_hard_negative_review_resolves_stable_context_across_versions(
    evaluation_runtime,
) -> None:
    client, service, pipeline_executor, _, _ = evaluation_runtime
    kb_id = await _create_kb(client, "calibration context")
    document_id = await _upload_text(
        client,
        kb_id,
        "context.txt",
        "The fixed policy states that Cedar reviews close after fourteen days.",
    )
    source_job = await _execute_draft(client, pipeline_executor, kb_id, [document_id])
    target_job = await _execute_draft(client, pipeline_executor, kb_id, [document_id])
    source_version_id = str(source_job["candidate_version_id"])
    target_version_id = str(target_job["candidate_version_id"])
    source_chunk = service.vector_store.list_document_chunks(
        f"{source_version_id}_{document_id}"
    )[0]

    store = rag_api.get_evaluation_store()
    dataset = store.create_generated_set(
        kb_id,
        "Target-bound calibration context",
        "",
        cases=[
            {
                "query": "Which regulator receives the Cedar closure report?",
                "expected_refs": [],
                "expected_no_result": True,
                "review_status": "pending",
                "tags": ["generated", "no_result", "en-US", "hard_negative"],
                "targeting": {
                    "query_type": "no_result",
                    "locale": "en-US",
                    "context_refs": [
                        {
                            "document_id": document_id,
                            "chunk_id": source_chunk.chunk_id,
                            "source_block_id": source_chunk.source_block_id,
                        }
                    ],
                },
            }
        ],
        provenance={
            "benchmark_contract_version": "rag-calibration-v1",
            "pipeline_version_id": target_version_id,
        },
        coverage={},
        calibration={"status": "not_required", "dataset_revision": 1},
        benchmark_role="calibration",
    )

    response = await client.get(
        f"/api/rag/evaluation-sets/{dataset['eval_set_id']}/cases/"
        f"{dataset['cases'][0]['case_id']}/evidence"
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["pipeline_version_id"] == target_version_id
    assert payload["evidence"][0]["chunk_id"] != source_chunk.chunk_id
    assert payload["evidence"][0]["source_block_id"] == source_chunk.source_block_id


def test_gold_v2_republish_after_consumption_requires_fresh_queries(
    tmp_path: Path,
) -> None:
    store = KnowledgeEvaluationStore(tmp_path / "evaluations.json")
    draft = store.create_generated_set(
        "kb-gold-v2",
        "Consumed Gold v2",
        "",
        cases=_gold_v2_cases(),
        provenance=_gold_v2_provenance(),
        coverage={},
        calibration={"status": "not_required", "dataset_revision": 1},
        benchmark_role="promotion_sealed",
    )
    published = store.publish_set(draft["eval_set_id"], expected_revision=1)
    with store._lock:
        data = store._read_unlocked()
        data["evidence_usages"][f"checksum:{published['checksum']}"] = {
            "status": "consumed",
            "evidence_checksum": published["checksum"],
            "version_id": published["version_id"],
        }
        store._write_unlocked(data)

    replacement_query = "A replacement for only one of the forty-two queries"
    replacement_targeting = copy.deepcopy(draft["cases"][0]["targeting"])
    replacement_targeting["leakage"] = gold_v2_leakage_receipt(
        replacement_query,
        ["fixed source text with no copied sequence"],
        query_type=str(replacement_targeting["query_type"]),
    )
    changed = store.update_case(
        draft["eval_set_id"],
        draft["cases"][0]["case_id"],
        expected_revision=1,
        values={"query": replacement_query, "targeting": replacement_targeting},
    )
    reviewed = store.update_case(
        draft["eval_set_id"],
        draft["cases"][0]["case_id"],
        expected_revision=2,
        values={
            "review_status": "approved",
            "review_evidence": {
                "source": "manual_ui",
                "decision": "approved",
                "reviewed_at": 3000.0,
                "dataset_revision": 2,
                "reason": "Replacement checked against the fixed source block.",
            },
        },
    )

    assert changed["cases"][0]["review_status"] == "pending"
    assert reviewed["cases"][0]["review_status"] == "approved"
    with pytest.raises(EvaluationStateError, match="fresh queries"):
        store.publish_set(draft["eval_set_id"], expected_revision=3)


def test_rag_runtime_identity_is_deterministic_and_source_sensitive(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "rag"
    source_dir.mkdir()
    (source_dir / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source_dir / "b.py").write_text("VALUE = 2\n", encoding="utf-8")

    original = build_rag_runtime_identity(source_dir)
    assert is_valid_rag_runtime_identity(original) is True
    assert build_rag_runtime_identity(source_dir) == original

    (source_dir / "b.py").write_text("VALUE = 3\n", encoding="utf-8")
    changed = build_rag_runtime_identity(source_dir)
    assert changed["fingerprint"] != original["fingerprint"]
    assert is_valid_rag_runtime_identity(changed) is True

    configured = build_rag_runtime_identity(
        source_dir,
        settings={"rerank_request": {"timeout_budget_ms": 5000}},
    )
    assert configured["fingerprint"] != changed["fingerprint"]
    assert is_valid_rag_runtime_identity(configured) is True

    tampered = copy.deepcopy(changed)
    tampered["source_hashes"][0]["sha256"] = "0" * 64
    assert is_valid_rag_runtime_identity(tampered) is False


def test_gold_v2_publish_rejects_missing_content_source_block_policy(
    tmp_path: Path,
) -> None:
    store = KnowledgeEvaluationStore(tmp_path / "evaluations.json")
    provenance = _gold_v2_provenance()
    provenance.pop("evidence_policy_version")
    draft = store.create_generated_set(
        "kb-gold-v2",
        "Heading-only Gold is not formal evidence",
        "",
        cases=_gold_v2_cases(),
        provenance=provenance,
        coverage={},
        calibration={"status": "not_required", "dataset_revision": 1},
        benchmark_role="promotion_sealed",
    )

    with pytest.raises(
        EvaluationStateError,
        match="content_source_block_evidence",
    ):
        store.publish_set(draft["eval_set_id"], expected_revision=1)


def test_gold_v2_semantic_edit_clears_prior_manual_review(tmp_path: Path) -> None:
    store = KnowledgeEvaluationStore(tmp_path / "evaluations.json")
    draft = store.create_generated_set(
        "kb-gold-v2",
        "Review invalidation",
        "",
        cases=[_gold_v2_cases()[0]],
        provenance=_gold_v2_provenance(),
        coverage={},
        calibration={"status": "calibrated", "dataset_revision": 1},
    )

    changed = store.update_case(
        draft["eval_set_id"],
        draft["cases"][0]["case_id"],
        expected_revision=1,
        values={"query": "materially changed query"},
    )

    assert changed["cases"][0]["review_status"] == "pending"
    assert changed["cases"][0]["review_evidence"] == {}
    assert changed["cases"][0]["targeting"]["leakage"]["stale"] is True
    assert "fresh_leakage_receipts" in gold_v2_review_admission_blockers(changed)
    assert changed["provenance"]["query_revision_contract"] == (
        "server-case-update-v1"
    )
    receipt = changed["provenance"]["query_revision_receipts"]
    assert len(receipt) == 1
    assert receipt[0]["case_id"] == draft["cases"][0]["case_id"]
    assert receipt[0]["source"] == "server_case_update"
    assert receipt[0]["dataset_revision"] == 2
    assert receipt[0]["previous_query_hash"] != receipt[0]["new_query_hash"]


def test_gold_v2_review_admission_separates_structural_and_review_stage_failures(
    tmp_path: Path,
) -> None:
    store = KnowledgeEvaluationStore(tmp_path / "evaluations.json")
    cases = _gold_v2_cases(approved=False)
    cases[0]["targeting"]["leakage"] = {
        "max_normalized_copy": 32,
        "warning": True,
        "blocked": True,
    }
    draft = store.create_generated_set(
        "kb-gold-v2",
        "Review admission",
        "",
        cases=cases,
        provenance=_gold_v2_provenance(),
        coverage={},
        calibration={"status": "not_required", "dataset_revision": 1},
    )

    # Pending reviews, their warning reasons, and an explicit rejection remain
    # review-stage work rather than reasons to lock the workbench.
    assert gold_v2_review_admission_blockers(draft) == []

    corrupted = copy.deepcopy(draft)
    corrupted["provenance"]["corpus_snapshot"]["documents"][0]["content_hash"] = ""
    blockers = gold_v2_review_admission_blockers(corrupted)
    assert "corpus_content_hashes" in blockers
    assert "corpus_snapshot_hash" in blockers


def test_formal_evidence_independence_rejects_exact_near_and_tampered_development_use() -> None:
    development = {
        "version_id": "gold-development-v1",
        "checksum": "d" * 64,
        "corpus_snapshot_hash": "c" * 64,
        "cases": [
            {"case_id": "dev-1", "query": "Which policy requires an audit owner?"},
            {"case_id": "dev-2", "query": "紧急例外会在七天后到期吗"},
        ],
    }
    manifest = build_development_evidence_manifest(development)

    exact = assess_formal_evidence_independence(
        manifest,
        {"cases": [{"case_id": "formal-1", "query": "Which policy requires an audit owner?"}]},
    )
    assert exact["independent"] is False
    assert exact["status"] == "development_evidence_overlap"
    assert exact["overlap_case_count"] == 1

    near = assess_formal_evidence_independence(
        manifest,
        {"cases": [{"case_id": "formal-2", "query": "紧急例外会在七天后到期么"}]},
    )
    assert near["independent"] is False
    assert near["overlap_case_count"] == 1

    fresh = assess_formal_evidence_independence(
        manifest,
        {"cases": [{"case_id": "formal-3", "query": "Who approves the monthly ledger?"}]},
    )
    assert fresh["independent"] is True
    assert fresh["overlap_case_count"] == 0

    tampered = copy.deepcopy(manifest)
    tampered["case_count"] = 999
    invalid = assess_formal_evidence_independence(tampered, {"cases": []})
    assert invalid["independent"] is False
    assert invalid["status"] == "invalid_development_evidence"


def test_formal_run_requires_published_gold_v2_full_pair_and_comparable_corpus(
    tmp_path: Path,
) -> None:
    store = KnowledgeEvaluationStore(tmp_path / "evaluations.json")
    draft = store.create_generated_set(
        "kb-gold-v2",
        "Formal Gold v2",
        "",
        cases=_gold_v2_cases(),
        provenance=_gold_v2_provenance(),
        coverage={},
        calibration={"status": "calibrated", "dataset_revision": 1},
        benchmark_role="promotion_sealed",
    )
    published = store.publish_set(draft["eval_set_id"], expected_revision=1)
    targets = [
        {"target_id": "baseline", "version_id": "baseline"},
        {"target_id": "candidate", "version_id": "candidate"},
    ]
    schedule_checksum = hashlib.sha256(
        json.dumps(
            build_paired_execution_schedule(published["cases"], targets, seed=19),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    runtime_identity = rag_runtime_identity()
    target_fingerprints = [
        {
            "version_id": version_id,
            "version_fingerprint": marker * 64,
            "configuration_fingerprint": marker * 64,
            "processor": {"mode": "general"},
            "retrieval": {"mode": "fulltext", "rerank_provider": "none"},
            "embedding": {
                "effective": {
                    "provider": "hash",
                    "model": "deterministic-hash-v1",
                    "dimension": 128,
                }
            },
            "runtime": copy.deepcopy(runtime_identity),
            "corpus_snapshot_hash": published["corpus_snapshot_hash"],
        }
        for version_id, marker in (("baseline", "a"), ("candidate", "b"))
    ]
    for target, fingerprint in zip(targets, target_fingerprints, strict=True):
        target["corpus_snapshot_hash"] = published["corpus_snapshot_hash"]
        target["version_evidence"] = {
            key: copy.deepcopy(fingerprint[key])
            for key in (
                "version_fingerprint",
                "configuration_fingerprint",
                "processor",
                "retrieval",
                "embedding",
                "runtime",
            )
        }
    manifest = {
        "version": "rag-eval-v2",
        "evaluation_set_checksum": published["checksum"],
        "corpus_snapshot_hash": published["corpus_snapshot_hash"],
        "target_fingerprints": target_fingerprints,
        "execution_seed": 19,
        "observation_depth": 10,
        "order_algorithm": "sha256-paired-interleave-v1",
        "schedule_checksum": schedule_checksum,
        "threshold_score_domain": "fused_score",
        "abstention_contract_version": "rag-abstention-v1",
        "runtime": copy.deepcopy(runtime_identity),
        "retry_policy": "none",
        "warmup_policy": "none",
        "development_evidence_independence": {
            "version": "rag-development-evidence-v1",
            "status": "no_declared_development_evidence",
            "independent": True,
            "overlap_case_count": 0,
            "similarity_threshold": 0.8,
        },
    }
    comparable = {
        "comparable": True,
        "corpus_snapshot_hash": published["corpus_snapshot_hash"],
        "reasons": [],
    }

    manifest_without_runtime = copy.deepcopy(manifest)
    manifest_without_runtime.pop("runtime")
    with pytest.raises(EvaluationStateError, match="runtime"):
        store.create_run(
            evaluation_set=store.get_set(draft["eval_set_id"]),
            evaluation_set_version=published,
            targets=targets,
            baseline_version_id="baseline",
            ks=[1, 5, 10],
            gate_policy=store.get_gate_policy("kb-gold-v2"),
            run_mode="formal",
            metric_contract_version="rag-eval-v2",
            execution_manifest=manifest_without_runtime,
            comparability=comparable,
            execution_seed=19,
        )

    mismatched_runtime_fingerprints = copy.deepcopy(target_fingerprints)
    mismatched_runtime_fingerprints[1]["runtime"]["fingerprint"] = "e" * 64
    with pytest.raises(EvaluationStateError, match="runtime"):
        store.create_run(
            evaluation_set=store.get_set(draft["eval_set_id"]),
            evaluation_set_version=published,
            targets=targets,
            baseline_version_id="baseline",
            ks=[1, 5, 10],
            gate_policy=store.get_gate_policy("kb-gold-v2"),
            run_mode="formal",
            metric_contract_version="rag-eval-v2",
            execution_manifest={
                **manifest,
                "target_fingerprints": mismatched_runtime_fingerprints,
            },
            comparability=comparable,
            execution_seed=19,
        )

    with pytest.raises(EvaluationStateError, match="case subsets"):
        store.create_run(
            evaluation_set=store.get_set(draft["eval_set_id"]),
            evaluation_set_version=published,
            targets=targets,
            baseline_version_id="baseline",
            ks=[1, 5, 10],
            gate_policy=store.get_gate_policy("kb-gold-v2"),
            case_ids=[published["cases"][0]["case_id"]],
            run_mode="formal",
            metric_contract_version="rag-eval-v2",
            execution_manifest=manifest,
            comparability=comparable,
            execution_seed=19,
        )

    with pytest.raises(EvaluationStateError, match="comparable corpus"):
        store.create_run(
            evaluation_set=store.get_set(draft["eval_set_id"]),
            evaluation_set_version=published,
            targets=targets,
            baseline_version_id="baseline",
            ks=[1, 5, 10],
            gate_policy=store.get_gate_policy("kb-gold-v2"),
            run_mode="formal",
            metric_contract_version="rag-eval-v2",
            execution_manifest=manifest,
            comparability={**comparable, "comparable": False, "reasons": ["mismatch"]},
            execution_seed=19,
        )

    with pytest.raises(EvaluationStateError, match="fingerprints"):
        store.create_run(
            evaluation_set=store.get_set(draft["eval_set_id"]),
            evaluation_set_version=published,
            targets=targets,
            baseline_version_id="baseline",
            ks=[1, 5, 10],
            gate_policy=store.get_gate_policy("kb-gold-v2"),
            run_mode="formal",
            metric_contract_version="rag-eval-v2",
            execution_manifest={**manifest, "target_fingerprints": manifest["target_fingerprints"][:1]},
            comparability=comparable,
            execution_seed=19,
        )

    with pytest.raises(EvaluationStateError, match="execution manifest"):
        store.create_run(
            evaluation_set=store.get_set(draft["eval_set_id"]),
            evaluation_set_version=published,
            targets=targets,
            baseline_version_id="baseline",
            ks=[1, 5, 10],
            gate_policy=store.get_gate_policy("kb-gold-v2"),
            run_mode="formal",
            metric_contract_version="rag-eval-v2",
            execution_manifest={**manifest, "schedule_checksum": "0" * 64},
            comparability=comparable,
            execution_seed=19,
        )

    mismatched_fingerprints = copy.deepcopy(target_fingerprints)
    mismatched_fingerprints[1]["corpus_snapshot_hash"] = "f" * 64
    with pytest.raises(EvaluationStateError, match="corpus"):
        store.create_run(
            evaluation_set=store.get_set(draft["eval_set_id"]),
            evaluation_set_version=published,
            targets=targets,
            baseline_version_id="baseline",
            ks=[1, 5, 10],
            gate_policy=store.get_gate_policy("kb-gold-v2"),
            run_mode="formal",
            metric_contract_version="rag-eval-v2",
            execution_manifest={**manifest, "target_fingerprints": mismatched_fingerprints},
            comparability=comparable,
            execution_seed=19,
        )

    run = store.create_run(
        evaluation_set=store.get_set(draft["eval_set_id"]),
        evaluation_set_version=published,
        targets=targets,
        baseline_version_id="baseline",
        ks=[1, 5, 10],
        gate_policy=store.get_gate_policy("kb-gold-v2"),
        run_mode="formal",
        metric_contract_version="rag-eval-v2",
        execution_manifest=manifest,
        comparability=comparable,
        execution_seed=19,
    )
    assert run["run_mode"] == "formal"
    assert run["metric_contract_version"] == "rag-eval-v2"
    assert run["comparability"]["comparable"] is True
    assert len(run["execution_schedule"]) == 84
    assert run["evidence_usage"]["status"] == "reserved"
    assert run["evidence_usage"]["eval_set_version_id"] == published["version_id"]

    with pytest.raises(EvaluationStateError, match="already been consumed"):
        store.create_run(
            evaluation_set=store.get_set(draft["eval_set_id"]),
            evaluation_set_version=published,
            targets=targets,
            baseline_version_id="baseline",
            ks=[1, 5, 10],
            gate_policy=store.get_gate_policy("kb-gold-v2"),
            run_mode="formal",
            metric_contract_version="rag-eval-v2",
            execution_manifest=manifest,
            comparability=comparable,
            execution_seed=19,
        )
    completed = store.complete_run(
        run["run_id"],
        [
            {
                "version_id": "baseline",
                "metrics": {
                    "expected_case_count": 42,
                    "completed_case_count": 42,
                    "failed_case_count": 0,
                    "positive_quality_denominator": 30,
                    "no_result_quality_denominator": 12,
                    "error_count": 0,
                },
                "promotion_gate": {"passed": True},
            },
            {
                "version_id": "candidate",
                "metrics": {
                    "expected_case_count": 42,
                    "completed_case_count": 41,
                    "failed_case_count": 0,
                    "positive_quality_denominator": 30,
                    "no_result_quality_denominator": 12,
                    "error_count": 0,
                },
                "promotion_gate": {"passed": True},
            },
        ],
    )
    assert completed["evidence_usage"]["status"] == "consumed"
    assert completed["evidence_usage"]["terminal_status"] == "succeeded"
    assert completed["evidence_usage"]["evidence_checksum"] == published["checksum"]
    assert completed["evidence_usage"]["evidence_usage_key"] == (
        f"checksum:{published['checksum']}"
    )
    assert completed["evidence_usage"]["consumed_at"] >= run["evidence_usage"]["reserved_at"]
    persisted_usage = store.get_run(run["run_id"])["evidence_usage"]
    assert persisted_usage == completed["evidence_usage"]

    duplicate = copy.deepcopy(published)
    duplicate["version_id"] = "evalsetver_duplicate_checksum"
    duplicate["version"] = 2
    with store._lock:
        data = store._read_unlocked()
        data["versions"][duplicate["version_id"]] = copy.deepcopy(duplicate)
        store._write_unlocked(data)
    with pytest.raises(EvaluationStateError, match="already been consumed"):
        store.create_run(
            evaluation_set=store.get_set(draft["eval_set_id"]),
            evaluation_set_version=duplicate,
            targets=targets,
            baseline_version_id="baseline",
            ks=[1, 5, 10],
            gate_policy=store.get_gate_policy("kb-gold-v2"),
            run_mode="formal",
            metric_contract_version="rag-eval-v2",
            execution_manifest=manifest,
            comparability=comparable,
            execution_seed=19,
        )
    with pytest.raises(EvaluationPromotionError, match="candidate, not the baseline"):
        store.assert_promotion_allowed(
            kb_id="kb-gold-v2",
            version_id="baseline",
            evaluation_run_id=run["run_id"],
            require_passed_run=True,
            current_runtime=runtime_identity,
        )
    with pytest.raises(EvaluationPromotionError, match="all 42"):
        store.assert_promotion_allowed(
            kb_id="kb-gold-v2",
            version_id="candidate",
            evaluation_run_id=run["run_id"],
            require_passed_run=True,
            current_runtime=runtime_identity,
        )


@pytest.mark.parametrize(
    ("initial_status", "terminal_action", "terminal_status"),
    [
        ("running", "complete", "succeeded"),
        ("running", "fail", "failed"),
        ("queued", "request_cancel", "cancelled"),
        ("running", "complete_cancel", "cancelled"),
    ],
)
def test_formal_evidence_usage_is_consumed_for_every_terminal_outcome(
    tmp_path: Path,
    initial_status: str,
    terminal_action: str,
    terminal_status: str,
) -> None:
    store = KnowledgeEvaluationStore(tmp_path / "evaluations.json")
    run_id = f"evalrun-{terminal_action}"
    version_id = f"evalsetver-{terminal_action}"
    usage = {
        "status": "reserved",
        "eval_set_version_id": version_id,
        "run_id": run_id,
        "target_fingerprints": [],
        "reserved_at": 10.0,
    }
    data = store._read_unlocked()
    data["runs"][run_id] = {
        "run_id": run_id,
        "run_mode": "formal",
        "eval_set_version_id": version_id,
        "status": initial_status,
        "cancel_requested": False,
        "evidence_usage": copy.deepcopy(usage),
    }
    data["evidence_usages"][version_id] = copy.deepcopy(usage)
    store._write_unlocked(data)

    if terminal_action == "complete":
        result = store.complete_run(run_id, [])
    elif terminal_action == "fail":
        result = store.fail_run(run_id, "expected failure")
    elif terminal_action == "request_cancel":
        result = store.request_cancel(run_id)
    else:
        result = store.complete_cancel(run_id)

    assert result["status"] == terminal_status
    assert result["evidence_usage"]["status"] == "consumed"
    assert result["evidence_usage"]["terminal_status"] == terminal_status
    assert result["evidence_usage"]["consumed_at"] >= 10.0
    persisted = store._read()
    assert persisted["evidence_usages"][version_id] == result["evidence_usage"]
    assert persisted["runs"][run_id]["evidence_usage"] == result["evidence_usage"]


def test_formal_rejects_legacy_or_nonformal_use_of_sealed_gold(tmp_path: Path) -> None:
    store = KnowledgeEvaluationStore(tmp_path / "evaluations.json")
    legacy = store.create_generated_set(
        "kb-gold-v2",
        "Legacy promotion evidence",
        "",
        cases=_gold_v2_cases(),
        provenance=_gold_v2_provenance(),
        coverage={},
        calibration={"status": "not_required", "dataset_revision": 1},
        benchmark_role="promotion_evidence",
    )
    with pytest.raises(EvaluationStateError, match="promotion_sealed"):
        store.publish_set(legacy["eval_set_id"], expected_revision=1)

    sealed = store.create_generated_set(
        "kb-gold-v2",
        "Sealed promotion evidence",
        "",
        cases=_gold_v2_cases(),
        provenance=_gold_v2_provenance(),
        coverage={},
        calibration={"status": "not_required", "dataset_revision": 1},
        benchmark_role="promotion_sealed",
    )
    published = store.publish_set(sealed["eval_set_id"], expected_revision=1)
    with pytest.raises(EvaluationStateError, match="cannot be used for diagnostic"):
        store.create_run(
            evaluation_set=store.get_set(sealed["eval_set_id"]),
            evaluation_set_version=published,
            targets=[{"target_id": "candidate", "version_id": "candidate"}],
            baseline_version_id=None,
            ks=[5],
            gate_policy=store.get_gate_policy("kb-gold-v2"),
            run_mode="diagnostic",
        )


def test_gold_v2_role_upgrade_reseals_only_unchanged_reviewed_content(
    tmp_path: Path,
) -> None:
    store = KnowledgeEvaluationStore(tmp_path / "evaluations.json")
    draft = store.create_generated_set(
        "kb-gold-v2",
        "Reviewed Gold awaiting final seal",
        "",
        cases=_gold_v2_cases(),
        provenance=_gold_v2_provenance(),
        coverage={},
        calibration={"status": "not_required", "dataset_revision": 1},
        benchmark_role="promotion_evidence",
    )
    reviewed_cases = copy.deepcopy(draft["cases"])

    sealed = store.update_set(
        draft["eval_set_id"],
        expected_revision=draft["revision"],
        benchmark_role="promotion_sealed",
    )
    published = store.publish_set(
        sealed["eval_set_id"],
        expected_revision=sealed["revision"],
    )

    assert sealed["cases"] == reviewed_cases
    assert sealed["calibration"]["dataset_revision"] == sealed["revision"]
    assert published["source_revision"] == sealed["revision"]
    assert published["cases"] == reviewed_cases
    assert qualify_promotion_evidence(published)["qualified"] is True


def test_gold_v2_publication_does_not_require_preformal_retrieval_calibration(
    tmp_path: Path,
) -> None:
    store = KnowledgeEvaluationStore(tmp_path / "evaluations.json")
    draft = store.create_generated_set(
        "kb-gold-v2",
        "Formal Gold without duplicate calibration",
        "",
        cases=_gold_v2_cases(),
        provenance=_gold_v2_provenance(),
        coverage={},
        calibration={
            "status": "not_required",
            "dataset_revision": 1,
            "reason": "Retrieval is measured once by the paired Formal run.",
        },
        benchmark_role="promotion_sealed",
    )

    published = store.publish_set(draft["eval_set_id"], expected_revision=1)

    assert published["benchmark_contract_version"] == "rag-gold-v2"
    assert published["calibration"]["status"] == "not_required"
    assert qualify_promotion_evidence(published)["qualified"] is True


def test_legacy_run_defaults_to_diagnostic_contract(tmp_path: Path) -> None:
    store = KnowledgeEvaluationStore(tmp_path / "evaluations.json")
    dataset = store.create_set("kb", "legacy")
    dataset = store.add_cases(
        dataset["eval_set_id"],
        expected_revision=1,
        cases=[{"query": "legacy query", "expected_refs": [{"document_id": "doc"}]}],
    )
    run = store.create_run(
        evaluation_set=dataset,
        targets=[{"target_id": "v1", "version_id": "v1"}],
        baseline_version_id=None,
        ks=[1, 5],
        gate_policy=store.get_gate_policy("kb"),
    )
    assert run["run_mode"] == "diagnostic"
    assert run["metric_contract_version"] == "legacy"
    assert run["comparability"]["comparable"] is False
    store.complete_run(
        run["run_id"],
        [
            {
                "version_id": "v1",
                "metrics": {"error_count": 0},
                "promotion_gate": {"passed": True},
            }
        ],
    )
    with pytest.raises(EvaluationPromotionError, match="formal rag-eval-v2"):
        store.assert_promotion_allowed(
            kb_id="kb",
            version_id="v1",
            evaluation_run_id=run["run_id"],
            require_passed_run=True,
        )


def test_historical_formal_without_development_independence_cannot_promote(
    tmp_path: Path,
) -> None:
    store = KnowledgeEvaluationStore(tmp_path / "evaluations.json")
    runtime = rag_runtime_identity()
    with store._lock:
        data = store._read_unlocked()
        data["runs"]["evalrun-pre-independence"] = {
            "run_id": "evalrun-pre-independence",
            "kb_id": "kb-legacy-formal",
            "status": "succeeded",
            "run_mode": "formal",
            "metric_contract_version": "rag-eval-v2",
            "comparability": {"comparable": True},
            "execution_manifest": {
                "abstention_contract_version": "rag-abstention-v1",
                "runtime": runtime,
            },
        }
        store._write_unlocked(data)

    with pytest.raises(EvaluationPromotionError, match="independent development"):
        store.assert_promotion_allowed(
            kb_id="kb-legacy-formal",
            version_id="candidate",
            evaluation_run_id="evalrun-pre-independence",
            require_passed_run=True,
            current_runtime=runtime,
        )


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


@pytest.mark.asyncio
async def test_formal_candidate_protection_blocks_direct_activation(
    evaluation_runtime,
) -> None:
    client, service, pipeline_executor, _evaluation_executor, _registry = evaluation_runtime
    kb_id = await _create_kb(client, "formal candidate activation guard")
    document_id = await _upload_text(
        client,
        kb_id,
        "candidate.txt",
        "The immutable candidate remains inactive until its Formal evaluation passes.",
    )
    candidate_job = await _execute_draft(
        client,
        pipeline_executor,
        kb_id,
        [document_id],
    )
    candidate_version_id = str(candidate_job["candidate_version_id"])

    service.mark_pipeline_version_promotion_required(
        candidate_version_id,
        source_run_id="evalrun-formal-guard",
    )

    candidate = service.get_pipeline_version(candidate_version_id)
    assert candidate["status"] == "ready"
    assert candidate["promotion_required"] is True
    assert candidate["promotion_source_run_id"] == "evalrun-formal-guard"

    blocked = await client.post(
        f"/api/rag/pipeline/versions/{candidate_version_id}/activate"
    )
    assert blocked.status_code == 409, blocked.text
    assert service.get_active_pipeline_version(kb_id) is None


@pytest.mark.asyncio
async def test_formal_run_protects_candidate_only_after_valid_queueing(monkeypatch) -> None:
    corpus_hash = "c" * 64
    retrieval = {
        "mode": "fulltext",
        "top_k": 5,
        "score_threshold": 0.0,
        "vector_weight": 0.0,
        "fulltext_weight": 1.0,
        "candidate_multiplier": 4,
        "per_document_limit": 2,
        "rerank_enabled": False,
        "rerank_provider": "none",
        "rerank_model": "",
        "rerank_top_n": 5,
        "abstention_enabled": False,
        "abstention_score_domain": "fused_score",
        "abstention_threshold": 0.0,
    }

    class FakeService:
        def __init__(self) -> None:
            self.mark_calls: list[tuple[str, str | None]] = []
            self.validated_evidence_profiles: list[dict] = []
            self.versions = {
                "baseline": {"version_id": "baseline", "version": 1, "kb_id": "kb", "status": "active"},
                "candidate": {"version_id": "candidate", "version": 2, "kb_id": "kb", "status": "ready"},
            }

        def get_pipeline_version(self, version_id: str) -> dict:
            return copy.deepcopy(self.versions[version_id])

        def pipeline_version_evidence(self, version_id: str) -> dict:
            return {
                "version_fingerprint": ("a" if version_id == "baseline" else "b") * 64,
                "configuration_fingerprint": ("d" if version_id == "baseline" else "e") * 64,
                "processor": {"mode": "general"},
                "retrieval": copy.deepcopy(retrieval),
                "embedding": {
                    "effective": {
                        "provider": "hash",
                        "model": "deterministic-hash-v1",
                        "dimension": 128,
                    }
                },
                "runtime": rag_runtime_identity(),
            }

        def pipeline_corpus_snapshot(self, _version_id: str, **_kwargs) -> dict:
            return {"corpus_snapshot": {}, "corpus_snapshot_hash": corpus_hash}

        def validate_evidence_verifier_identity(
            self, retrieval_profile: dict
        ) -> dict:
            self.validated_evidence_profiles.append(copy.deepcopy(retrieval_profile))
            return copy.deepcopy(retrieval_profile)

        def mark_pipeline_version_promotion_required(
            self,
            version_id: str,
            *,
            source_run_id: str | None = None,
        ) -> dict:
            self.mark_calls.append((version_id, source_run_id))
            return {}

    service = FakeService()

    class FakeStore:
        def get_set(self, _eval_set_id: str) -> dict:
            return {
                "eval_set_id": "set",
                "kb_id": "kb",
                "status": "active",
                "cases": [{"case_id": "case", "query": "q"}],
            }

        def get_set_version(self, _eval_set_id: str, _version: int) -> dict:
            return {
                "version_id": "gold",
                "version": 1,
                "checksum": "f" * 64,
                "cases": [{"case_id": "case", "query": "q"}],
                "corpus_snapshot": {"documents": []},
                "corpus_snapshot_hash": corpus_hash,
            }

        def get_gate_policy(self, _kb_id: str) -> dict:
            return {}

        def create_run(self, **kwargs) -> dict:
            assert service.mark_calls == []
            manifest = kwargs["execution_manifest"]
            assert manifest["runtime"]["version"] == "rag-runtime-v1"
            assert len(manifest["runtime"]["fingerprint"]) == 64
            assert all(
                target["runtime"] == manifest["runtime"]
                for target in manifest["target_fingerprints"]
            )
            return {"run_id": "evalrun-formal"}

    class FakeExecutor:
        def __init__(self) -> None:
            self.notified = False

        def notify(self) -> None:
            self.notified = True

    executor = FakeExecutor()
    monkeypatch.setattr(rag_api, "get_rag_service", lambda: service)
    monkeypatch.setattr(rag_api, "get_evaluation_store", lambda: FakeStore())
    monkeypatch.setattr(rag_api, "get_evaluation_executor", lambda: executor)
    monkeypatch.setattr(
        rag_api,
        "qualify_promotion_evidence",
        lambda _snapshot: {"qualified": True},
    )

    run = await rag_api.create_evaluation_run(
        rag_api.EvaluationRunCreateRequest(
            eval_set_id="set",
            eval_set_version=1,
            targets=[
                rag_api.EvaluationTargetInput(version_id="baseline"),
                rag_api.EvaluationTargetInput(version_id="candidate"),
            ],
            baseline_version_id="baseline",
            run_mode="formal",
            execution_seed=7,
        )
    )

    assert run["run_id"] == "evalrun-formal"
    assert service.mark_calls == [("candidate", "evalrun-formal")]
    assert service.validated_evidence_profiles == [retrieval, retrieval]
    assert executor.notified is True

    service.versions["candidate"]["origin"] = {
        "kind": "rag_strategy_tuner",
        "development_evidence": build_development_evidence_manifest(
            FakeStore().get_set_version("set", 1)
        ),
    }
    with pytest.raises(rag_api.HTTPException) as blocked:
        await rag_api.create_evaluation_run(
            rag_api.EvaluationRunCreateRequest(
                eval_set_id="set",
                eval_set_version=1,
                targets=[
                    rag_api.EvaluationTargetInput(version_id="baseline"),
                    rag_api.EvaluationTargetInput(version_id="candidate"),
                ],
                baseline_version_id="baseline",
                run_mode="formal",
                execution_seed=7,
            )
        )
    assert blocked.value.status_code == 400
    assert "independent" in str(blocked.value.detail)
    assert service.mark_calls == [("candidate", "evalrun-formal")]


@pytest.mark.asyncio
async def test_retrieval_variant_reuses_immutable_index_and_requires_promotion(
    evaluation_runtime,
    monkeypatch,
) -> None:
    client, service, pipeline_executor, _evaluation_executor, _registry = evaluation_runtime
    kb_id = await _create_kb(client, "retrieval-only formal candidate")
    document_id = await _upload_text(
        client,
        kb_id,
        "fixed-corpus.txt",
        "A fixed corpus can support multiple immutable retrieval profiles without rebuilding embeddings.",
    )
    base_job = await _execute_draft(
        client,
        pipeline_executor,
        kb_id,
        [document_id],
    )
    base_version_id = str(base_job["candidate_version_id"])
    assert (
        await client.post(f"/api/rag/pipeline/versions/{base_version_id}/activate")
    ).status_code == 200
    monkeypatch.setattr(
        service.reranker,
        "capabilities",
        lambda: {
            "evidence_verifier_configured": True,
            "evidence_verifier_model": "test/evidence-verifier",
        },
    )

    response = await client.post(
        f"/api/rag/pipeline/versions/{base_version_id}/retrieval-variant",
        json={
            "retrieval_profile": {
                "mode": "hybrid",
                "vector_weight": 0.7,
                "fulltext_weight": 0.3,
                "top_k": 5,
                "score_threshold": 0.0,
                "candidate_multiplier": 4,
                "rerank_enabled": True,
                "rerank_provider": "llm",
                "rerank_top_n": 5,
                "evidence_verification_enabled": True,
            }
        },
    )

    assert response.status_code == 200, response.text
    variant_payload = response.json()
    variant = service.get_pipeline_version(variant_payload["version_id"])
    base = service.get_pipeline_version(base_version_id)
    assert variant["version_id"] != base_version_id
    assert variant["status"] == "ready"
    assert variant["promotion_required"] is True
    assert variant["base_version_id"] == base_version_id
    assert variant["index_reused"] is True
    assert variant["namespace"] == base["namespace"]
    assert variant["embedding_profile"] == base["embedding_profile"]
    assert variant["retrieval_profile"]["evidence_verification_enabled"] is True
    assert variant["retrieval_profile"]["rerank_model"] == "test/evidence-verifier"
    assert service.pipeline_version_evidence(variant["version_id"])["runtime"] == (
        service.pipeline_version_evidence(base_version_id)["runtime"]
    )
    assert service.pipeline_corpus_snapshot(variant["version_id"]) == service.pipeline_corpus_snapshot(
        base_version_id
    )
    assert service.get_active_pipeline_version(kb_id)["version_id"] == base_version_id

    blocked = await client.post(
        f"/api/rag/pipeline/versions/{variant['version_id']}/activate"
    )
    assert blocked.status_code == 409, blocked.text
    assert service.get_active_pipeline_version(kb_id)["version_id"] == base_version_id


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
async def test_gold_v2_api_query_edit_recomputes_leakage_from_fixed_source(
    evaluation_runtime,
) -> None:
    client, service, pipeline_executor, _, _ = evaluation_runtime
    kb_id = await _create_kb(client, "leakage receipt")
    document_id = await _upload_text(
        client,
        kb_id,
        "fixed-source.txt",
        (
            "Production approval requires a signed safety review, a dated rollback "
            "rehearsal, and a fourteen day observation window."
        ),
    )
    job = await _execute_draft(client, pipeline_executor, kb_id, [document_id])
    version_id = str(job["candidate_version_id"])
    source = service.vector_store.list_document_chunks(
        f"{version_id}_{document_id}"
    )[0]
    resolved_source = service.get_knowledge_chunk(
        kb_id,
        source.chunk_id,
        version_id=version_id,
    )
    store = rag_api.get_evaluation_store()
    dataset = store.create_generated_set(
        kb_id,
        "Editable Gold v2",
        "",
        cases=[
            {
                "query": "What is required before production approval?",
                "expected_refs": [
                    {
                        "document_id": resolved_source["document_id"],
                        "chunk_id": source.chunk_id,
                        "source_block_id": resolved_source["source_block_id"],
                        "match_mode": "chunk",
                        "relevance": 3,
                    }
                ],
                "review_status": "approved",
                "review_evidence": {
                    "source": "manual_ui",
                    "decision": "approved",
                    "reviewed_at": 1.0,
                    "dataset_revision": 1,
                    "reason": "Original query reviewed.",
                },
                "targeting": {
                    "query_type": "factual_lookup",
                    "locale": "en-US",
                    "leakage": {
                        "max_normalized_copy": 0,
                        "warning_threshold": 24,
                        "warning": False,
                        "blocked": False,
                    },
                },
            }
        ],
        provenance={
            "benchmark_contract_version": "rag-gold-v2",
            "pipeline_version_id": version_id,
        },
        coverage={},
        calibration={"status": "not_required", "dataset_revision": 1},
        benchmark_role="promotion_sealed",
    )
    copied_query = str(source.text)
    response = await client.patch(
        f"/api/rag/evaluation-sets/{dataset['eval_set_id']}/cases/{dataset['cases'][0]['case_id']}",
        json={
            "expected_revision": 1,
            "case": {
                "query": copied_query,
                "expected_refs": dataset["cases"][0]["expected_refs"],
                "expected_no_result": False,
                "review_status": "approved",
                "tags": [],
                "notes": "",
            },
        },
    )

    assert response.status_code == 200, response.text
    changed = response.json()["cases"][0]
    assert changed["review_status"] == "pending"
    assert changed["review_evidence"] == {}
    assert changed["targeting"]["leakage"] == {
        "max_normalized_copy": 32,
        "warning_threshold": 24,
        "warning": True,
        "blocked": True,
        "query_hash": gold_v2_leakage_receipt(
            copied_query,
            [copied_query],
            query_type="factual_lookup",
        )["query_hash"],
        "stale": False,
    }


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
    assert evidence["runtime"]["version"] == "rag-runtime-v1"
    assert len(evidence["runtime"]["fingerprint"]) == 64
    assert evidence["runtime"]["source_hashes"]
    assert evidence["runtime"]["settings"]["embedding_http"][
        "max_keepalive_connections"
    ] == 10
    assert evidence["runtime"]["settings"]["rerank_request"][
        "timeout_budget_ms"
    ] == 5000
    assert evidence["embedding"]["effective"]["provider"] == "hash"
    assert evidence["embedding"]["effective"]["model"] == "deterministic-hash-v1"
    receipt = candidate["case_results"][0]["retrieval_receipt"]
    assert receipt["embedding_provider"] == "hash"
    assert receipt["embedding_model"] == "deterministic-hash-v1"
    assert receipt["embedding_dimension"] == 128
    assert receipt["rerank_provider_used"] == "none"
    assert receipt["embedding_external_call_count"] == 0
    for timing_key in (
        "retrieval_elapsed_ms",
        "embedding_elapsed_ms",
        "vector_search_elapsed_ms",
        "fulltext_search_elapsed_ms",
        "fusion_elapsed_ms",
    ):
        assert isinstance(receipt[timing_key], (int, float))
        assert receipt[timing_key] >= 0
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
