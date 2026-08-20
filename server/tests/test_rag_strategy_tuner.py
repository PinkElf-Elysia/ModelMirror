from __future__ import annotations

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
    set_strategy_tuner_for_tests,
)
from server.rag.embedder import EmbeddingClient
from server.rag.evaluation import KnowledgeEvaluationStore
from server.rag.evaluation_executor import KnowledgeEvaluationExecutor
from server.rag.pipeline_executor import KnowledgePipelineExecutor
from server.rag.rag_service import PipelineJobStateError, RagService
from server.rag.strategy_tuner import (
    KNOWN_WINNER_FIXTURE_VERSION,
    RagStrategyTuner,
    RagStrategyTuningStore,
    apply_optimization_gate,
    calibrate_threshold,
    improvement_summary,
    mark_semantic_duplicate_candidates,
    paired_statistical_validation,
    repeated_validation_plan,
    retrieval_candidates,
    retrieval_semantic_checksum,
    stratified_split,
    summarize_repeated_case_results,
)
from server.rag.vector_store import LocalJsonVectorStore
from server.xpert_runtime.run_registry import RunRegistry


KNOWN_WINNER_FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "rag_strategy_tuner_known_winners.json"
)


def _known_winner_fixture(scenario_id: str) -> dict:
    payload = json.loads(KNOWN_WINNER_FIXTURE_PATH.read_text(encoding="utf-8"))
    assert payload["fixture_version"] == KNOWN_WINNER_FIXTURE_VERSION
    scenarios = {
        str(item["scenario_id"]): item for item in payload.get("scenarios") or []
    }
    scenario = dict(scenarios[scenario_id])
    inherited = str(scenario.get("inherits") or "")
    if inherited:
        scenario = {**dict(scenarios[inherited]), **scenario}
    return scenario


@pytest_asyncio.fixture
async def tuning_runtime(tmp_path: Path):
    service = RagService(
        storage_dir=tmp_path / "rag-storage",
        uploads_dir=tmp_path / "rag-uploads",
        embedder=EmbeddingClient(api_key="", dimension=64),
        vector_store=LocalJsonVectorStore(tmp_path / "rag-storage" / "vectors.json"),
        llm_enabled=False,
    )
    pipeline_executor = KnowledgePipelineExecutor(service, poll_interval=0.01)
    evaluation_store = KnowledgeEvaluationStore(service.storage_dir / "evaluations.json")
    evaluation_executor = KnowledgeEvaluationExecutor(
        service, evaluation_store, poll_interval=0.01
    )
    run_registry = RunRegistry()
    tuning_store = RagStrategyTuningStore(service.storage_dir / "strategy_tuning_runs.json")
    tuner = RagStrategyTuner(
        service,
        tuning_store,
        evaluation_store,
        pipeline_executor,
        evaluation_executor,
        run_registry=run_registry,
        poll_interval=0.01,
    )
    set_rag_service_for_tests(service)
    set_pipeline_executor_for_tests(pipeline_executor)
    set_evaluation_executor_for_tests(evaluation_executor)
    set_strategy_tuner_for_tests(tuner)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, service, pipeline_executor, evaluation_store, tuner
    set_strategy_tuner_for_tests(None)
    set_evaluation_executor_for_tests(None)
    set_pipeline_executor_for_tests(None)
    set_rag_service_for_tests(None)


async def _base_version(
    service: RagService, executor: KnowledgePipelineExecutor
) -> tuple[str, str, str]:
    kb = service.create_knowledge_base("strategy tuner")
    document = await service.upload_document(
        kb["id"],
        "policy.md",
        (
            "# Control policy\n\n"
            "Control MM-2042 requires an owner, a reviewer, and a dated audit record.\n\n"
            "# Exceptions\n\nEmergency exceptions expire after seven days.\n"
        ).encode("utf-8"),
    )
    draft = service.get_pipeline_draft(kb["id"])
    job = service.create_pipeline_job(
        kb["id"],
        draft_version=draft["version"],
        source_document_ids=[document["id"]],
    )
    assert await executor.run_once() is True
    completed = service.get_pipeline_job(job["job_id"])
    assert completed["status"] == "succeeded"
    return kb["id"], document["id"], completed["candidate_version_id"]


def _published_set(
    store: KnowledgeEvaluationStore,
    kb_id: str,
    *,
    stable_blocks: bool = True,
    qualified: bool = False,
) -> tuple[str, int]:
    evaluation_set = store.create_set(
        kb_id,
        "Tuning Gold",
        "fixed benchmark",
        benchmark_role="strategy_tuning" if qualified else "unclassified",
    )
    cases = []
    for index in range(30 if qualified else 12):
        reference = {
            "document_id": "doc-fixed",
            "chunk_id": f"chunk-{index}",
            "relevance": 3,
        }
        if stable_blocks:
            reference.update(
                {
                    "source_block_id": f"block-{index}",
                    "match_mode": "source_block",
                }
            )
        cases.append(
            {
                "query": f"What does control MM-2042 require? Case {index}",
                "expected_refs": [reference],
                "tags": ["fact" if index % 2 else "paraphrase"],
            }
        )
    if qualified:
        cases.extend(
            {
                "query": f"Which similar control is not defined? Case {index}",
                "expected_refs": [],
                "expected_no_result": True,
                "review_status": "approved",
                "tags": ["hard_negative", "corpus_near"],
                "targeting": {
                    "blueprint_id": f"negative-{index}",
                    "query_type": "no_result",
                    "context_evidence_ids": [f"evidence-{index}"],
                },
            }
            for index in range(12)
        )
    updated = store.add_cases(
        evaluation_set["eval_set_id"],
        expected_revision=evaluation_set["revision"],
        cases=cases,
    )
    version = store.publish_set(
        evaluation_set["eval_set_id"], expected_revision=updated["revision"]
    )
    return evaluation_set["eval_set_id"], int(version["version"])


async def _published_set_for_version(
    service: RagService,
    store: KnowledgeEvaluationStore,
    kb_id: str,
    version_id: str,
) -> tuple[str, int]:
    retrieval = await service.query_pipeline_version(
        version_id,
        "control MM-2042 owner reviewer dated audit record",
        top_k=5,
        retrieval={"mode": "fulltext", "top_k": 5},
        generate_answer=False,
    )
    source = retrieval["sources"][0]
    document_id = str(
        source.get("source_document_id")
        or source.get("document_id")
        or source.get("doc_id")
    )
    source_block_id = str(source.get("source_block_id") or "")
    assert document_id
    assert source_block_id
    evaluation_set = store.create_set(
        kb_id,
        "Executable tuning Gold",
        benchmark_role="strategy_tuning",
    )
    updated = store.add_cases(
        evaluation_set["eval_set_id"],
        expected_revision=evaluation_set["revision"],
        cases=[
            {
                "query": "What evidence is required for control MM-2042?",
                "expected_refs": [
                    {
                        "document_id": document_id,
                        "source_block_id": source_block_id,
                        "match_mode": "source_block",
                        "relevance": 3,
                    }
                ],
                "tags": ["fact" if index % 2 else "paraphrase"],
            }
            for index in range(30)
        ]
        + [
            {
                "query": f"Which neighboring control is absent? Case {index}",
                "expected_refs": [],
                "expected_no_result": True,
                "review_status": "approved",
                "tags": ["hard_negative", "corpus_near"],
                "targeting": {
                    "blueprint_id": f"negative-{index}",
                    "query_type": "no_result",
                    "context_evidence_ids": [f"evidence-{index}"],
                },
            }
            for index in range(12)
        ],
    )
    version = store.publish_set(
        evaluation_set["eval_set_id"], expected_revision=updated["revision"]
    )
    return evaluation_set["eval_set_id"], int(version["version"])


async def _known_winner_base_version(
    service: RagService,
    executor: KnowledgePipelineExecutor,
    scenario: dict,
) -> tuple[str, str, str]:
    kb = service.create_knowledge_base("strategy tuner known winner")
    document = await service.upload_document(
        kb["id"],
        str(scenario["document_name"]),
        str(scenario["document_content"]).encode("utf-8"),
    )
    draft = service.update_pipeline_draft(
        kb["id"],
        {},
        retrieval_profile={
            "mode": "fulltext",
            "top_k": 5,
            "score_threshold": 0,
            "rerank_enabled": False,
            "rerank_provider": "none",
        },
    )
    job = service.create_pipeline_job(
        kb["id"],
        draft_version=draft["version"],
        source_document_ids=[document["id"]],
    )
    assert await executor.run_once() is True
    completed = service.get_pipeline_job(job["job_id"])
    assert completed["status"] == "succeeded"
    return kb["id"], document["id"], str(completed["candidate_version_id"])


async def _rebuild_known_winner_base(
    service: RagService,
    executor: KnowledgePipelineExecutor,
    kb_id: str,
    document_id: str,
    *,
    score_threshold: float,
) -> str:
    draft = service.update_pipeline_draft(
        kb_id,
        {},
        retrieval_profile={
            "mode": "fulltext",
            "top_k": 5,
            "score_threshold": score_threshold,
            "rerank_enabled": False,
            "rerank_provider": "none",
        },
    )
    job = service.create_pipeline_job(
        kb_id,
        draft_version=draft["version"],
        source_document_ids=[document_id],
    )
    assert await executor.run_once() is True
    completed = service.get_pipeline_job(job["job_id"])
    assert completed["status"] == "succeeded"
    return str(completed["candidate_version_id"])


async def _known_winner_score_boundary(
    service: RagService,
    version_id: str,
    scenario: dict,
) -> tuple[float, float]:
    async def top_score(query: str) -> float:
        response = await service.query_pipeline_version(
            version_id,
            query,
            top_k=5,
            retrieval={"mode": "fulltext", "top_k": 5, "score_threshold": 0},
            generate_answer=False,
        )
        assert response["sources"], query
        return float(response["sources"][0]["score"])

    positive_scores = [
        await top_score(str(query)) for query in scenario["positive_queries"]
    ]
    negative_scores = [
        await top_score(str(query)) for query in scenario["hard_negative_queries"]
    ]
    positive_floor = min(positive_scores)
    negative_ceiling = max(negative_scores)
    assert positive_floor > negative_ceiling
    return positive_floor, negative_ceiling


async def _known_winner_evaluation_set(
    service: RagService,
    store: KnowledgeEvaluationStore,
    kb_id: str,
    version_id: str,
    scenario: dict,
) -> tuple[str, int]:
    source_block_ids: set[str] = set()
    document_id = ""
    for query in scenario["positive_queries"]:
        response = await service.query_pipeline_version(
            version_id,
            str(query),
            top_k=5,
            retrieval={"mode": "fulltext", "top_k": 5, "score_threshold": 0},
            generate_answer=False,
        )
        assert response["sources"], query
        source = response["sources"][0]
        document_id = str(
            source.get("source_document_id")
            or source.get("document_id")
            or source.get("doc_id")
        )
        source_block_ids.add(str(source.get("source_block_id") or ""))
    assert document_id
    assert len(source_block_ids) == 1
    source_block_id = source_block_ids.pop()
    assert source_block_id

    evaluation_set = store.create_set(
        kb_id,
        f"Known winner: {scenario['scenario_id']}",
        "Project-owned deterministic strategy tuner fixture",
        benchmark_role="strategy_tuning",
    )
    positive_queries = list(scenario["positive_queries"])
    negative_queries = list(scenario["hard_negative_queries"])
    positive_count = int(scenario["positive_case_count"])
    negative_count = int(scenario["hard_negative_case_count"])
    cases = [
        {
            "query": str(positive_queries[index % len(positive_queries)]),
            "expected_refs": [
                {
                    "document_id": document_id,
                    "source_block_id": source_block_id,
                    "match_mode": "source_block",
                    "relevance": 3,
                }
            ],
            "tags": ["fact" if index % 2 else "paraphrase"],
        }
        for index in range(positive_count)
    ]
    cases.extend(
        {
            "query": str(negative_queries[index % len(negative_queries)]),
            "expected_refs": [],
            "expected_no_result": True,
            "review_status": "approved",
            "tags": ["hard_negative", "corpus_near"],
            "targeting": {
                "blueprint_id": f"known-negative-{index}",
                "query_type": "no_result",
                "context_evidence_ids": [source_block_id],
            },
        }
        for index in range(negative_count)
    )
    updated = store.add_cases(
        evaluation_set["eval_set_id"],
        expected_revision=evaluation_set["revision"],
        cases=cases,
    )
    published = store.publish_set(
        evaluation_set["eval_set_id"], expected_revision=updated["revision"]
    )
    return evaluation_set["eval_set_id"], int(published["version"])


def test_tuning_store_recovers_inflight_and_preserves_terminal_runs(tmp_path: Path) -> None:
    store = RagStrategyTuningStore(tmp_path / "runs.json")
    queued = store.create_run(
        {"kb_id": "kb-a"}, {"snapshot_hash": "hash", "warnings": []}
    )
    claimed = store.claim_next_run()
    assert claimed is not None and claimed["status"] == "profiling"
    store.update(
        queued["run_id"],
        validation_plan={"checksum": "plan"},
        validation_baseline={"checksum": "baseline"},
    )
    terminal = store.create_run(
        {"kb_id": "kb-a"}, {"snapshot_hash": "hash-2", "warnings": []}
    )
    store.update(terminal["run_id"], status="completed", completed_at=1.0)

    reloaded = RagStrategyTuningStore(tmp_path / "runs.json")
    assert reloaded.recover_runs() == 1
    assert reloaded.get_run(queued["run_id"])["status"] == "queued"
    assert reloaded.get_run(queued["run_id"])["validation_plan"]["checksum"] == "plan"
    assert (
        reloaded.get_run(queued["run_id"])["validation_baseline"]["checksum"]
        == "baseline"
    )
    assert reloaded.get_run(terminal["run_id"])["status"] == "completed"


def test_explicit_retry_discards_stale_trial_progress(tmp_path: Path) -> None:
    store = RagStrategyTuningStore(tmp_path / "runs.json")
    run = store.create_run(
        {"kb_id": "kb-a"}, {"snapshot_hash": "hash", "warnings": []}
    )
    store.update(
        run["run_id"],
        status="failed",
        candidates=[{"candidate_id": "old"}],
        finalists=[{"candidate_id": "old-finalist"}],
        trial_indexes=[{"chunker_checksum": "old"}],
        trial_version_ids=["version-old"],
        pipeline_job_ids=["job-old"],
        final_version_id="version-final",
        validation_plan={"checksum": "old-plan"},
        validation_baseline={"checksum": "old-baseline"},
        statistical_summary={"eligible_count": 1},
    )

    retried = store.retry(run["run_id"])

    assert retried["status"] == "queued"
    assert retried["candidates"] == []
    assert retried["finalists"] == []
    assert retried["trial_indexes"] == []
    assert retried["pipeline_job_ids"] == []
    assert retried["final_version_id"] is None
    assert retried["validation_plan"] == {}
    assert retried["validation_baseline"] == {}
    assert retried["statistical_summary"] == {}


@pytest.mark.asyncio
async def test_evaluation_executor_can_respect_fixed_profile_top_k(tmp_path: Path) -> None:
    class RecordingService:
        def __init__(self) -> None:
            self.top_ks: list[int] = []

        async def query_pipeline_version(self, _version_id, _query, *, top_k, **_kwargs):
            self.top_ks.append(int(top_k))
            return {"sources": [], "warnings": []}

        @staticmethod
        def _safe_pipeline_error(exc: Exception) -> str:
            return str(exc)

    service = RecordingService()
    store = KnowledgeEvaluationStore(tmp_path / "evaluations.json")
    dataset = store.create_set("kb-a", "Top-K contract")
    dataset = store.add_cases(
        dataset["eval_set_id"],
        expected_revision=dataset["revision"],
        cases=[
            {
                "query": "Which source is expected?",
                "expected_refs": [{"document_id": "doc-a"}],
            }
        ],
    )
    version = store.publish_set(
        dataset["eval_set_id"], expected_revision=dataset["revision"]
    )
    store.create_run(
        evaluation_set=dataset,
        evaluation_set_version=version,
        targets=[
            {
                "target_id": "fixed-profile",
                "version_id": "v1",
                "retrieval": {"top_k": 5},
                "respect_profile_top_k": True,
            },
            {
                "target_id": "legacy-evaluation",
                "version_id": "v2",
                "retrieval": {"top_k": 5},
            },
        ],
        baseline_version_id=None,
        ks=[1, 5, 10],
        gate_policy=store.get_gate_policy("kb-a"),
    )
    executor = KnowledgeEvaluationExecutor(service, store, poll_interval=0.01)

    assert await executor.run_once() is True
    assert sorted(service.top_ks) == [5, 10]


def test_split_and_hash_retrieval_candidates_are_deterministic() -> None:
    cases = [
        {
            "case_id": f"case-{index}",
            "expected_no_result": index % 5 == 0,
            "tags": ["fact" if index % 2 else "context"],
        }
        for index in range(18)
    ]
    first = stratified_split(cases, 42)
    second = stratified_split(cases, 42)
    assert first == second
    assert set(first["optimization_case_ids"]).isdisjoint(first["holdout_case_ids"])
    assert len(first["holdout_case_ids"]) >= 4

    profiles = retrieval_candidates({"mode": "hybrid", "top_k": 5}, degraded=True)
    assert profiles[0]["mode"] == "hybrid"
    assert all(item["mode"] == "fulltext" for item in profiles[1:])


def test_repeated_validation_plan_stays_inside_fixed_holdout() -> None:
    cases = [
        {
            "case_id": f"case-{index}",
            "expected_no_result": index >= 8,
            "tags": ["fact" if index % 2 else "context"],
        }
        for index in range(12)
    ]
    holdout = ["case-1", "case-2", "case-4", "case-8", "case-10"]

    first = repeated_validation_plan(cases, holdout, 42)
    second = repeated_validation_plan(cases, holdout, 42)

    assert first == second
    assert len(first["resamples"]) == 3
    assert first["query_repetitions"] == 3
    for resample in first["resamples"]:
        assert len(resample["case_ids"]) == len(holdout)
        assert set(resample["case_ids"]) <= set(holdout)


def test_repeated_case_summary_uses_per_case_median_latency() -> None:
    results = [
        {
            "case_id": "case-a",
            "status": "completed",
            "metrics": {"ndcg_at_10": value},
            "latency_ms": latency,
            "expected_no_result": False,
            "no_result": False,
        }
        for value, latency in ((1.0, 10), (0.8, 1000), (1.0, 12))
    ]

    summary = summarize_repeated_case_results(results)[0]

    assert summary["latency_ms"] == 12
    assert summary["metrics"]["ndcg_at_10"] == pytest.approx(0.933333, abs=1e-6)
    assert summary["repeat_count"] == 3


def test_paired_bootstrap_gate_accepts_stable_gain_and_rejects_regression() -> None:
    cases = [
        {
            "case_id": f"case-{index}",
            "expected_no_result": index >= 8,
            "tags": ["fact" if index % 2 else "context"],
        }
        for index in range(12)
    ]
    holdout = [str(item["case_id"]) for item in cases]
    plan = repeated_validation_plan(cases, holdout, 7)

    def result(delta: float) -> dict:
        return {
            "metrics": {"p95_latency_ms": 10},
            "case_summaries": [
                {
                    "case_id": case["case_id"],
                    "status": "completed",
                    "expected_no_result": case["expected_no_result"],
                    "metrics": {
                        (
                            "no_result_accuracy"
                            if case["expected_no_result"]
                            else "ndcg_at_10"
                        ): 0.7 + delta
                    },
                }
                for case in cases
            ],
        }

    baseline = result(0)
    improved = paired_statistical_validation(baseline, result(0.1), plan)
    regressed = paired_statistical_validation(baseline, result(-0.05), plan)

    assert improved["passed"] is True
    assert improved["quality_improvement_confident"] is True
    assert improved["confidence_interval"]["lower"] == pytest.approx(0.1)
    assert regressed["passed"] is False
    assert regressed["confidence_interval"]["upper"] == pytest.approx(-0.05)


def test_retrieval_semantic_checksum_ignores_inactive_mode_fields() -> None:
    first = {
        "mode": "fulltext",
        "top_k": 5,
        "score_threshold": 0,
        "candidate_multiplier": 4,
        "vector_weight": 0.9,
        "fulltext_weight": 0.1,
        "rerank_enabled": False,
        "rerank_provider": "llm",
        "rerank_model": "unused-model",
        "rerank_top_n": 20,
    }
    second = {
        **first,
        "vector_weight": 0.1,
        "fulltext_weight": 0.9,
        "rerank_provider": "api",
        "rerank_model": "another-unused-model",
        "rerank_top_n": 2,
    }

    assert retrieval_semantic_checksum(first) == retrieval_semantic_checksum(second)


def test_semantic_outcome_duplicates_cannot_consume_winner_slots() -> None:
    candidates = [
        {
            "candidate_id": "candidate-a",
            "retrieval": {"mode": "fulltext", "top_k": 5},
            "realized_index_fingerprint": "same-index",
            "ranking_fingerprint": "same-ranking",
            "automatic_winner_eligible": True,
        },
        {
            "candidate_id": "candidate-b",
            "retrieval": {
                "mode": "fulltext",
                "top_k": 5,
                "vector_weight": 0.1,
                "fulltext_weight": 0.9,
            },
            "realized_index_fingerprint": "same-index",
            "ranking_fingerprint": "same-ranking",
            "automatic_winner_eligible": True,
        },
    ]

    summary = mark_semantic_duplicate_candidates(candidates)

    assert summary["unique_semantic_outcomes"] == 1
    assert summary["duplicate_count"] == 1
    assert candidates[1]["automatic_winner_eligible"] is False
    assert candidates[1]["ineligible_reason"] == "semantic_duplicate"
    assert candidates[1]["duplicate_of_candidate_id"] == "candidate-a"


def test_no_result_accuracy_counts_as_effective_quality_improvement() -> None:
    improvement = improvement_summary(
        {"ndcg_at_10": 0.9, "no_result_accuracy": 0.0, "p95_latency_ms": 10},
        {"ndcg_at_10": 0.9, "no_result_accuracy": 0.8, "p95_latency_ms": 10},
        {"estimated_index_bytes": 1000, "chunk_count": 10},
        {"estimated_index_bytes": 1000, "chunk_count": 10},
    )

    assert improvement["no_result_accuracy_delta"] == 0.8
    assert improvement["effective"] is True


def test_optimization_gate_filters_candidates_before_holdout() -> None:
    baseline_metrics = {
        "recall_at_5": 1.0,
        "mrr_at_10": 0.8,
        "ndcg_at_10": 0.8,
        "citation_hit_rate": 0.5,
        "citation_coverage": 1.0,
        "no_result_accuracy": 0.5,
        "positive_no_result_rate": 0.0,
        "p95_latency_ms": 20.0,
        "error_count": 0,
    }
    common = {
        "cost": {"chunk_count": 10, "estimated_index_bytes": 1000},
        "automatic_winner_eligible": True,
    }
    candidates = [
        {
            **common,
            "candidate_id": "fails-no-result",
            "optimization_metrics": {
                **baseline_metrics,
                "no_result_accuracy": 0.5,
            },
        },
        {
            **common,
            "candidate_id": "passes-gate",
            "optimization_metrics": {
                **baseline_metrics,
                "no_result_accuracy": 1.0,
            },
        },
    ]

    evaluated, eligible, summary = apply_optimization_gate(
        candidates,
        baseline_metrics=baseline_metrics,
        baseline_cost=common["cost"],
        policy={"min_recall_at_5": 0.7, "min_no_result_accuracy": 0.8},
        objective="balanced",
    )

    assert [item["candidate_id"] for item in eligible] == ["passes-gate"]
    assert evaluated[0]["optimization_gate"]["passed"] is False
    assert evaluated[1]["optimization_gate"]["passed"] is True
    assert summary == {
        "evaluated_count": 2,
        "passed_count": 1,
        "eligible_count": 1,
        "failed_check_counts": {"min_no_result_accuracy": 1},
    }


def test_threshold_calibration_preserves_recall_before_improving_abstention() -> None:
    positive_scores = [0.4, 0.3, 0.2, 0.15]
    no_result_scores = [0.05, 0.05, 0.05, 0.2]
    cases = [
        {
            "case_id": f"positive-{index}",
            "expected_refs": [{"document_id": f"doc-{index}"}],
            "expected_no_result": False,
        }
        for index in range(len(positive_scores))
    ] + [
        {
            "case_id": f"no-result-{index}",
            "expected_refs": [],
            "expected_no_result": True,
        }
        for index in range(len(no_result_scores))
    ]
    case_results = [
        {
            "case_id": f"positive-{index}",
            "ranking": [
                {
                    "chunk_id": f"chunk-{index}",
                    "document_id": f"doc-{index}",
                    "document_name": f"doc-{index}.md",
                    "score": score,
                    "fused_score": score,
                }
            ]
            + (
                [
                    {
                        "chunk_id": "low-confidence-noise",
                        "document_id": "noise-doc",
                        "document_name": "noise.md",
                        "score": 0.1,
                        "fused_score": 0.1,
                    }
                ]
                if index == 0
                else []
            ),
            "latency_ms": 1,
        }
        for index, score in enumerate(positive_scores)
    ] + [
        {
            "case_id": f"no-result-{index}",
            "ranking": [
                {
                    "chunk_id": f"noise-{index}",
                    "document_id": f"noise-doc-{index}",
                    "document_name": f"noise-{index}.md",
                    "score": score,
                    "fused_score": score,
                }
            ],
            "latency_ms": 1,
        }
        for index, score in enumerate(no_result_scores)
    ]

    calibrated = calibrate_threshold(
        {"cases": cases, "case_results": case_results},
        {"mode": "fulltext", "top_k": 10, "score_threshold": 0},
    )

    assert len(calibrated["threshold_candidates"]) <= 8
    assert calibrated["retrieval"]["score_threshold"] > 0
    assert calibrated["metrics"]["recall_at_5"] == 1.0
    assert calibrated["metrics"]["no_result_accuracy"] == 0.75
    assert calibrated["threshold_selection_reason"] == "hard_negative_false_positive_improved"
    assert calibrated["threshold_front"]


def test_threshold_calibration_keeps_baseline_when_abstention_costs_recall() -> None:
    cases = [
        {
            "case_id": "positive",
            "expected_refs": [{"document_id": "doc-positive"}],
            "expected_no_result": False,
        },
        {
            "case_id": "negative",
            "expected_refs": [],
            "expected_no_result": True,
        },
    ]
    case_results = [
        {
            "case_id": "positive",
            "ranking": [
                {"document_id": "doc-positive", "score": 0.1, "fused_score": 0.1}
            ],
            "latency_ms": 1,
        },
        {
            "case_id": "negative",
            "ranking": [
                {"document_id": "noise", "score": 0.2, "fused_score": 0.2}
            ],
            "latency_ms": 1,
        },
    ]

    calibrated = calibrate_threshold(
        {"cases": cases, "case_results": case_results},
        {"mode": "fulltext", "top_k": 10, "score_threshold": 0},
    )

    assert calibrated["retrieval"]["score_threshold"] == 0
    assert calibrated["threshold_selection_reason"] == (
        "baseline_preserved_no_safe_negative_improvement"
    )


def test_threshold_calibration_uses_fused_score_when_rerank_score_disagrees() -> None:
    cases = [
        {
            "case_id": "positive",
            "expected_refs": [{"document_id": "doc-positive"}],
            "expected_no_result": False,
        },
        {
            "case_id": "negative",
            "expected_refs": [],
            "expected_no_result": True,
        },
    ]
    case_results = [
        {
            "case_id": "positive",
            "ranking": [
                {
                    "document_id": "doc-positive",
                    "score": 0.1,
                    "rerank_score": 0.1,
                    "fused_score": 0.9,
                }
            ],
            "latency_ms": 1,
        },
        {
            "case_id": "negative",
            "ranking": [
                {
                    "document_id": "noise",
                    "score": 0.9,
                    "rerank_score": 0.9,
                    "fused_score": 0.1,
                }
            ],
            "latency_ms": 1,
        },
    ]

    calibrated = calibrate_threshold(
        {"cases": cases, "case_results": case_results},
        {
            "mode": "hybrid",
            "top_k": 5,
            "score_threshold": 0,
            "rerank_enabled": True,
        },
    )

    assert calibrated["threshold_calibration_eligible"] is True
    assert calibrated["threshold_score_domain"] == "fused_score"
    assert calibrated["retrieval"]["score_threshold"] > 0.1
    assert calibrated["metrics"]["recall_at_5"] == 1.0
    assert calibrated["metrics"]["no_result_accuracy"] == 1.0


def test_threshold_calibration_fails_closed_without_fused_score_evidence() -> None:
    calibrated = calibrate_threshold(
        {
            "cases": [
                {
                    "case_id": "positive",
                    "expected_refs": [{"document_id": "doc-positive"}],
                    "expected_no_result": False,
                }
            ],
            "case_results": [
                {
                    "case_id": "positive",
                    "ranking": [{"document_id": "doc-positive", "score": 0.9}],
                    "latency_ms": 1,
                }
            ],
        },
        {"mode": "fulltext", "top_k": 5, "score_threshold": 0},
    )

    assert calibrated["threshold_calibration_eligible"] is False
    assert calibrated["threshold_score_domain"] == "fused_score"
    assert calibrated["threshold_selection_reason"] == "missing_fused_score_evidence"
    assert calibrated["retrieval"]["score_threshold"] == 0


@pytest.mark.parametrize(
    "case_results",
    [
        [{"case_id": "positive", "ranking": [], "latency_ms": 1}],
        [],
    ],
)
def test_threshold_calibration_fails_closed_without_any_ranking_evidence(
    case_results: list[dict],
) -> None:
    calibrated = calibrate_threshold(
        {
            "cases": [
                {
                    "case_id": "positive",
                    "expected_refs": [{"document_id": "doc-positive"}],
                    "expected_no_result": False,
                }
            ],
            "case_results": case_results,
        },
        {"mode": "fulltext", "top_k": 5, "score_threshold": 0},
    )

    assert calibrated["threshold_calibration_eligible"] is False
    assert calibrated["threshold_selection_reason"] == "missing_fused_score_evidence"
    assert calibrated["missing_fused_score_count"] == 1


@pytest.mark.asyncio
async def test_preflight_degrades_chunk_tuning_and_hides_sensitive_snapshot(
    tuning_runtime,
) -> None:
    client, service, executor, evaluation_store, _ = tuning_runtime
    kb_id, _, version_id = await _base_version(service, executor)
    eval_set_id, eval_version = _published_set(
        evaluation_store, kb_id, stable_blocks=False
    )
    response = await client.post(
        "/api/rag/strategy-tuner/preflight",
        json={
            "kb_id": kb_id,
            "base_version_id": version_id,
            "eval_set_id": eval_set_id,
            "eval_set_version": eval_version,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["retrieval_only"] is True
    assert payload["selection_eligible"] is False
    assert payload["tuning_readiness"]["status"] == "insufficient_data"
    assert payload["embedding_degraded"] is True
    assert payload["eval_case_count"] == 12
    serialized = response.text
    assert "snapshot_key" not in serialized
    assert "stored_path" not in serialized
    assert "OPENROUTER_API_KEY" not in serialized


@pytest.mark.asyncio
async def test_strategy_tuner_rejects_report_only_evaluation_evidence(
    tuning_runtime,
) -> None:
    client, service, executor, evaluation_store, _ = tuning_runtime
    kb_id, _, version_id = await _base_version(service, executor)
    eval_set_id, eval_version = _published_set(evaluation_store, kb_id)

    response = await client.post(
        "/api/rag/strategy-tuner/runs",
        json={
            "kb_id": kb_id,
            "base_version_id": version_id,
            "eval_set_id": eval_set_id,
            "eval_set_version": eval_version,
        },
    )

    assert response.status_code == 400
    assert "at least 30 answerable cases" in response.text


@pytest.mark.asyncio
async def test_strategy_tuner_api_lists_cancels_and_retries_queued_run(
    tuning_runtime,
) -> None:
    client, service, executor, evaluation_store, _ = tuning_runtime
    kb_id, _, version_id = await _base_version(service, executor)
    eval_set_id, eval_version = _published_set(
        evaluation_store, kb_id, qualified=True
    )
    payload = {
        "kb_id": kb_id,
        "base_version_id": version_id,
        "eval_set_id": eval_set_id,
        "eval_set_version": eval_version,
    }
    created_response = await client.post(
        "/api/rag/strategy-tuner/runs", json=payload
    )
    assert created_response.status_code == 202, created_response.text
    created = created_response.json()
    assert created["status"] == "queued"

    listed = await client.get(
        f"/api/rag/strategy-tuner/runs?kb_id={kb_id}&status=queued"
    )
    assert listed.status_code == 200
    assert [item["run_id"] for item in listed.json()["runs"]] == [created["run_id"]]

    cancelled = await client.post(
        f"/api/rag/strategy-tuner/runs/{created['run_id']}/cancel"
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    retried = await client.post(
        f"/api/rag/strategy-tuner/runs/{created['run_id']}/retry"
    )
    assert retried.status_code == 200
    assert retried.json()["status"] == "queued"


def test_optimization_gate_defers_single_query_latency_to_holdout() -> None:
    baseline = {
        "recall_at_5": 1.0,
        "mrr_at_10": 1.0,
        "citation_hit_rate": 1.0,
        "citation_coverage": 1.0,
        "no_result_accuracy": 1.0,
        "positive_no_result_rate": 0.0,
        "p95_latency_ms": 10.0,
        "error_count": 0,
    }
    candidate = {
        "candidate_id": "cold-start-outlier",
        "optimization_metrics": {**baseline, "p95_latency_ms": 5000.0},
        "cost": {"chunk_count": 10, "estimated_index_bytes": 1000},
        "automatic_winner_eligible": True,
    }

    evaluated, eligible, _ = apply_optimization_gate(
        [candidate],
        baseline_metrics=baseline,
        baseline_cost=candidate["cost"],
        policy={"max_p95_latency_ratio": 1.1},
        objective="balanced",
    )

    assert [item["candidate_id"] for item in eligible] == ["cold-start-outlier"]
    assert evaluated[0]["optimization_gate"]["latency_evidence"] == (
        "deferred_to_repeated_holdout"
    )


@pytest.mark.asyncio
async def test_trial_version_is_hidden_unactivatable_and_cleanup_preserves_draft(
    tuning_runtime,
) -> None:
    _, service, executor, _, _ = tuning_runtime
    kb_id, _, base_version_id = await _base_version(service, executor)
    base = service.get_pipeline_version(base_version_id)
    base_job = service.get_pipeline_job(base["job_id"])
    base_chunker = base_job["config_snapshot"]["stages"]["stage_chunker"]
    changed_chunker = {**base_chunker, "chunk_size": 500, "chunk_overlap": 50}
    draft_before = service.get_pipeline_draft(kb_id)
    trial = service.create_strategy_tuning_pipeline_job(
        kb_id,
        base_version_id=base_version_id,
        chunker_profile=changed_chunker,
        retrieval_profile={"mode": "fulltext", "top_k": 10},
        tuning_run_id="ragtune-test",
        trial=True,
    )
    assert await executor.run_once() is True
    trial_version_id = trial["candidate_version_id"]
    trial_version = service.get_pipeline_version(trial_version_id)
    assert trial_version["origin"]["kind"] == "rag_strategy_tuner_trial"
    assert all(
        item["version_id"] != trial_version_id
        for item in service.list_pipeline_versions(kb_id)
    )
    with pytest.raises(PipelineJobStateError):
        service.activate_pipeline_version(trial_version_id)
    assert service.get_pipeline_draft(kb_id) == draft_before

    service.cleanup_strategy_tuning_trial_version(trial_version_id)
    assert trial["job_id"] not in service._read_metadata()["pipeline_jobs"]


@pytest.mark.asyncio
async def test_holdout_profile_uses_three_queries_and_case_level_latency(
    tuning_runtime,
) -> None:
    _, service, executor, _, tuner = tuning_runtime
    kb_id, _, version_id = await _base_version(service, executor)
    version = service.get_pipeline_version(version_id)

    result = await tuner._evaluate_profile(
        version_id,
        dict(version["retrieval_profile"]),
        [
            {
                "case_id": "repeat-case",
                "query": "Which policy applies?",
                "expected_refs": [],
                "expected_no_result": True,
            }
        ],
        repetitions=3,
    )

    assert result["metrics"]["case_count"] == 1
    assert result["metrics"]["execution_count"] == 3
    assert result["metrics"]["query_repetitions"] == 3
    assert result["metrics"]["latency_aggregation"] == "median_per_case_then_p95"
    assert result["case_summaries"][0]["repeat_count"] == 3


@pytest.mark.asyncio
async def test_tuner_materializes_ready_candidate_without_switching_active_version(
    tuning_runtime,
    monkeypatch,
) -> None:
    _, service, executor, evaluation_store, tuner = tuning_runtime
    kb_id, _, base_version_id = await _base_version(service, executor)
    service.activate_pipeline_version(base_version_id)
    eval_set_id, eval_version = await _published_set_for_version(
        service, evaluation_store, kb_id, base_version_id
    )
    evaluation_store.set_gate_policy(
        kb_id,
        {"min_no_result_accuracy": 0.0},
    )
    request = {
        "kb_id": kb_id,
        "base_version_id": base_version_id,
        "eval_set_id": eval_set_id,
        "eval_set_version": eval_version,
        "objective": "balanced",
    }
    created = tuner.create_run(request)
    stored = tuner.store.get_run(created["run_id"])
    winner = {
        "candidate_id": "forced-safe-winner",
        "chunker": stored["snapshot"]["base_chunker"],
        "retrieval": {
            **stored["snapshot"]["base_retrieval"],
            "mode": "fulltext",
            "top_k": 5,
            "rerank_enabled": False,
            "rerank_provider": "none",
        },
        "optimization_metrics": {},
        "holdout_metrics": {},
        "promotion_gate": {"passed": True},
        "improvement": {"effective": True},
        "statistical_validation": {
            "validation_version": "rag-strategy-validation-v1",
            "passed": True,
        },
        "cost": {},
        "checksum": "forced-safe-winner",
    }

    async def fixed_search(*_args, **_kwargs):
        return {"eligible": [winner], "baseline_metrics": {}}

    monkeypatch.setattr(tuner, "_search", fixed_search)
    assert await tuner.run_once() is True

    completed = tuner.store.get_run(created["run_id"])
    assert completed["status"] == "no_improvement", completed
    assert completed["no_improvement_reason"] == "full_evaluation_gate"
    final_version = service.get_pipeline_version(completed["final_version_id"])
    assert final_version["status"] == "ready"
    assert final_version["promotion_required"] is True
    assert final_version["origin"]["kind"] == "rag_strategy_tuner"
    assert service.get_active_pipeline_version(kb_id)["version_id"] == base_version_id
    evaluation = evaluation_store.get_run(completed["evaluation_run_id"])
    assert evaluation["status"] == "succeeded"
    candidate_result = next(
        item
        for item in evaluation["target_results"]
        if item["version_id"] == final_version["version_id"]
    )
    assert candidate_result["promotion_gate"]["passed"] is False
    evidence_check = next(
        check
        for check in candidate_result["promotion_gate"]["checks"]
        if check["id"] == "qualified_promotion_evidence"
    )
    assert evidence_check["passed"] is False

    registry_run = await tuner.run_registry.get_run(completed["run_registry_id"])
    assert registry_run is not None and registry_run.status == "completed"
    checkpoints = await tuner.run_registry.list_checkpoints(registry_run.run_id)
    serialized = str([item.metadata for item in checkpoints])
    assert "MM-2042" not in serialized
    assert "control policy" not in serialized.lower()

    version_ids_before = {
        item["version_id"] for item in service.list_pipeline_versions(kb_id)
    }
    evaluation_run_id = completed["evaluation_run_id"]
    await tuner._materialize(
        completed["run_id"], completed["request"], completed["snapshot"], winner
    )
    resumed = tuner.store.get_run(completed["run_id"])
    assert resumed["final_version_id"] == final_version["version_id"]
    assert resumed["evaluation_run_id"] == evaluation_run_id
    assert {
        item["version_id"] for item in service.list_pipeline_versions(kb_id)
    } == version_ids_before


def test_known_winner_fixture_contract_is_versioned_and_project_owned() -> None:
    payload = json.loads(KNOWN_WINNER_FIXTURE_PATH.read_text(encoding="utf-8"))

    assert payload["schema_version"] == 1
    assert payload["fixture_version"] == KNOWN_WINNER_FIXTURE_VERSION
    assert payload["source"] == "ModelMirror project-owned synthetic fixture"
    assert {item["scenario_id"] for item in payload["scenarios"]} == {
        "threshold_recovery",
        "already_optimal_control",
    }


@pytest.mark.asyncio
async def test_known_winner_threshold_recovery_runs_real_search_and_materializes(
    tuning_runtime,
) -> None:
    _, service, executor, evaluation_store, tuner = tuning_runtime
    scenario = _known_winner_fixture("threshold_recovery")
    kb_id, _, base_version_id = await _known_winner_base_version(
        service, executor, scenario
    )
    service.activate_pipeline_version(base_version_id)
    _, negative_ceiling = await _known_winner_score_boundary(
        service, base_version_id, scenario
    )
    eval_set_id, eval_version = await _known_winner_evaluation_set(
        service, evaluation_store, kb_id, base_version_id, scenario
    )

    created = tuner.create_run(
        {
            "kb_id": kb_id,
            "base_version_id": base_version_id,
            "eval_set_id": eval_set_id,
            "eval_set_version": eval_version,
            "objective": "quality",
            "max_chunk_indexes": 1,
            "max_retrieval_trials": 1,
            "max_finalists": 1,
            "seed": 42,
        }
    )
    assert await tuner.run_once() is True

    completed = tuner.store.get_run(created["run_id"])
    assert completed["status"] == "no_improvement", completed
    assert completed["no_improvement_reason"] == "full_evaluation_gate"
    winner = completed["winner"]
    assert winner["retrieval"]["mode"] == "fulltext"
    assert float(winner["retrieval"]["score_threshold"]) > negative_ceiling
    assert winner["threshold_selection_reason"] == (
        "hard_negative_false_positive_improved"
    )
    assert float(winner["improvement"]["no_result_accuracy_delta"]) >= float(
        scenario["expected_winner"]["minimum_no_result_accuracy_delta"]
    )
    assert winner["statistical_validation"]["passed"] is True

    final_version = service.get_pipeline_version(completed["final_version_id"])
    assert final_version["status"] == "ready"
    assert final_version["promotion_required"] is True
    assert float(final_version["retrieval_profile"]["score_threshold"]) > 0
    assert service.get_active_pipeline_version(kb_id)["version_id"] == base_version_id
    evaluation = evaluation_store.get_run(completed["evaluation_run_id"])
    assert evaluation["status"] == "succeeded"
    candidate_result = next(
        item
        for item in evaluation["target_results"]
        if item["version_id"] == final_version["version_id"]
    )
    assert candidate_result["promotion_gate"]["passed"] is False
    assert any(
        check["id"] == "qualified_promotion_evidence" and not check["passed"]
        for check in candidate_result["promotion_gate"]["checks"]
    )


@pytest.mark.asyncio
async def test_known_winner_already_optimal_control_does_not_invent_winner(
    tuning_runtime,
) -> None:
    _, service, executor, evaluation_store, tuner = tuning_runtime
    scenario = _known_winner_fixture("already_optimal_control")
    kb_id, document_id, calibration_version_id = await _known_winner_base_version(
        service, executor, scenario
    )
    _, negative_ceiling = await _known_winner_score_boundary(
        service, calibration_version_id, scenario
    )
    safe_threshold = round(negative_ceiling + 0.000001, 6)
    base_version_id = await _rebuild_known_winner_base(
        service,
        executor,
        kb_id,
        document_id,
        score_threshold=safe_threshold,
    )
    service.activate_pipeline_version(base_version_id)
    eval_set_id, eval_version = await _known_winner_evaluation_set(
        service, evaluation_store, kb_id, base_version_id, scenario
    )

    created = tuner.create_run(
        {
            "kb_id": kb_id,
            "base_version_id": base_version_id,
            "eval_set_id": eval_set_id,
            "eval_set_version": eval_version,
            "objective": "quality",
            "max_chunk_indexes": 1,
            "max_retrieval_trials": 1,
            "max_finalists": 1,
            "seed": 42,
        }
    )
    assert await tuner.run_once() is True

    completed = tuner.store.get_run(created["run_id"])
    assert completed["status"] == scenario["expected_outcome"], completed
    assert completed.get("final_version_id") is None
    assert completed.get("winner") is None
    assert completed["no_improvement_reason"] == "optimization_gate"
    assert len(completed["candidates"]) == 1
    assert completed["candidates"][0]["automatic_winner_eligible"] is False
    assert completed["candidates"][0]["ineligible_reason"] == "baseline_equivalent"
    assert service.get_active_pipeline_version(kb_id)["version_id"] == base_version_id


def test_tuner_capabilities_publish_known_winner_evidence_version(
    tuning_runtime,
) -> None:
    _, _, _, _, tuner = tuning_runtime

    validation = tuner.capabilities()["validation"]
    assert validation["known_winner_fixture_version"] == KNOWN_WINNER_FIXTURE_VERSION
    assert validation["known_winner_scenarios"] == [
        "threshold_recovery",
        "already_optimal_control",
    ]
