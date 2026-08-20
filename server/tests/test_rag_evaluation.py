from __future__ import annotations

import copy
import hashlib
import json
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
    EvaluationStateError,
    KnowledgeEvaluationStore,
    aggregate_target_metrics,
    build_paired_execution_schedule,
    evaluate_promotion_gate,
    evaluate_retrieval_case,
    paired_primary_confidence_report,
    qualify_promotion_evidence,
)
from server.rag.evaluation_executor import KnowledgeEvaluationExecutor
from server.rag.pipeline_executor import KnowledgePipelineExecutor
from server.rag.rag_service import RagService
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
        benchmark_role="promotion_evidence",
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
        benchmark_role="promotion_evidence",
    )
    published = store.publish_set(approved["eval_set_id"], expected_revision=1)
    assert published["benchmark_contract_version"] == "rag-gold-v2"
    assert published["qualification_manifest"]["qualified"] is True
    assert qualify_promotion_evidence(published)["qualified"] is True

    tampered = copy.deepcopy(published)
    tampered["provenance"]["seed"] = 18
    qualification = qualify_promotion_evidence(tampered)
    assert qualification["qualified"] is False
    assert next(
        check for check in qualification["checks"] if check["id"] == "published_checksum"
    )["passed"] is False


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
        benchmark_role="promotion_evidence",
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
            )
        }
    manifest = {
        "version": "rag-eval-v2",
        "evaluation_set_checksum": published["checksum"],
        "corpus_snapshot_hash": published["corpus_snapshot_hash"],
        "target_fingerprints": target_fingerprints,
        "execution_seed": 19,
        "order_algorithm": "sha256-paired-interleave-v1",
        "schedule_checksum": schedule_checksum,
        "threshold_score_domain": "fused_score",
        "retry_policy": "none",
        "warmup_policy": "none",
    }
    comparable = {
        "comparable": True,
        "corpus_snapshot_hash": published["corpus_snapshot_hash"],
        "reasons": [],
    }

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
    store.complete_run(
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
    with pytest.raises(EvaluationPromotionError, match="candidate, not the baseline"):
        store.assert_promotion_allowed(
            kb_id="kb-gold-v2",
            version_id="baseline",
            evaluation_run_id=run["run_id"],
            require_passed_run=True,
        )
    with pytest.raises(EvaluationPromotionError, match="all 42"):
        store.assert_promotion_allowed(
            kb_id="kb-gold-v2",
            version_id="candidate",
            evaluation_run_id=run["run_id"],
            require_passed_run=True,
        )


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
        benchmark_role="promotion_evidence",
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
