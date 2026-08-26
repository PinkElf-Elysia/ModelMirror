from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import server.benchmarks.api as benchmark_api
import server.rag.api as rag_api
from server.benchmarks.executor import BenchmarkJobExecutor
from server.benchmarks.knowledge_generation import KnowledgeBenchmarkGenerationService
from server.benchmarks.models import BenchmarkGenerationRequest
from server.benchmarks.service import BenchmarkGenerationError
from server.benchmarks.store import BenchmarkJobStore
from server.main import app
from server.rag.evaluation import (
    EvaluationStateError,
    KnowledgeEvaluationStore,
    qualify_promotion_evidence,
)
from server.rag.vector_store import StoredVectorChunk


def test_strategy_tuning_generation_supports_qualified_positive_and_negative_counts() -> None:
    request = BenchmarkGenerationRequest.model_validate(
        {
            "target": {
                "kind": "knowledge_version",
                "kb_id": "kb_target",
                "pipeline_version_id": "pipeline_v2",
            },
            "generator_model_id": "test-model",
            "generation_purpose": "strategy_tuning",
            "case_count": 42,
            "no_result_count": 12,
        }
    )

    assert request.case_count - request.no_result_count == 30
    assert request.no_result_count == 12


@pytest.mark.parametrize(
    ("case_count", "no_result_count", "message"),
    [
        (41, 12, "at least 30 answerable cases"),
        (35, 5, "either 0 or at least 12 hard-negative cases"),
    ],
)
def test_strategy_tuning_generation_rejects_unqualified_counts(
    case_count: int,
    no_result_count: int,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        BenchmarkGenerationRequest.model_validate(
            {
                "target": {
                    "kind": "knowledge_version",
                    "kb_id": "kb_target",
                    "pipeline_version_id": "pipeline_v2",
                },
                "generator_model_id": "test-model",
                "generation_purpose": "strategy_tuning",
                "case_count": case_count,
                "no_result_count": no_result_count,
            }
        )


def test_general_generation_preserves_legacy_limits() -> None:
    with pytest.raises(ValueError, match="general generation cannot exceed 30 cases"):
        BenchmarkGenerationRequest.model_validate(
            {
                "target": {
                    "kind": "knowledge_version",
                    "kb_id": "kb_target",
                    "pipeline_version_id": "pipeline_v2",
                },
                "generator_model_id": "test-model",
                "case_count": 31,
            }
        )


class _VectorStore:
    def __init__(self, chunks: dict[str, list[StoredVectorChunk]]) -> None:
        self.chunks = chunks

    def list_document_chunks(self, doc_id: str) -> list[StoredVectorChunk]:
        return list(self.chunks.get(doc_id, []))


class _RagService:
    def __init__(self) -> None:
        version_id = "pipeline_v2"
        self.version = {
            "version_id": version_id,
            "kb_id": "kb_target",
            "version": 2,
            "status": "active",
            "vector_index_ready": True,
            "lexical_index_ready": True,
            "retrieval_profile": {"mode": "hybrid", "top_k": 10},
            "processor_profile": {"mode": "general"},
            "embedding_profile": {"provider": "hash", "dimension": 64},
            "document_results": [
                {
                    "source_id": "doc_alpha",
                    "filename": "alpha.md",
                    "status": "completed",
                    "content_hash": "a" * 64,
                    "chunk_count": 2,
                    "block_count": 2,
                },
                {
                    "source_id": "doc_beta",
                    "filename": "beta.md",
                    "status": "completed",
                    "content_hash": "b" * 64,
                    "chunk_count": 2,
                    "block_count": 2,
                },
            ],
        }
        self.vector_store = _VectorStore(
            {
                f"{version_id}_doc_alpha": [
                    _chunk("alpha_1", "doc_alpha", "alpha.md", "block_alpha_1", "The Aurora policy requires a seven day review window for every release."),
                    _chunk("alpha_2", "doc_alpha", "alpha.md", "block_alpha_2", "The Nimbus exception applies only to archived research records after approval."),
                ],
                f"{version_id}_doc_beta": [
                    _chunk("beta_1", "doc_beta", "beta.md", "block_beta_1", "Project Meridian assigns the final audit to the compliance lead in Shanghai."),
                    _chunk("beta_2", "doc_beta", "beta.md", "block_beta_2", "The quarterly threshold is 420 units and excludes internal transfer volume."),
                ],
            }
        )

    def get_pipeline_version(self, version_id: str) -> dict:
        if version_id != self.version["version_id"]:
            raise RuntimeError("missing")
        return json.loads(json.dumps(self.version))

    def get_knowledge_chunk(
        self, kb_id: str, chunk_id: str, *, version_id: str | None = None
    ) -> dict:
        assert kb_id == "kb_target"
        assert version_id == "pipeline_v2"
        for chunks in self.vector_store.chunks.values():
            for chunk in chunks:
                if chunk.chunk_id == chunk_id:
                    return {
                        "document_id": chunk.doc_id.removeprefix("pipeline_v2_"),
                        "document_name": chunk.document_name,
                        "chunk_id": chunk.chunk_id,
                        "source_block_id": chunk.source_block_id,
                        "page_number": chunk.page_number,
                        "heading_path": list(chunk.heading_path),
                        "visual_kind": chunk.visual_kind,
                        "text": chunk.text,
                    }
        raise RuntimeError("missing chunk")


def _chunk(chunk_id: str, source_id: str, name: str, block_id: str, text: str) -> StoredVectorChunk:
    return StoredVectorChunk(
        chunk_id=chunk_id,
        kb_id="kb_target",
        doc_id=f"pipeline_v2_{source_id}",
        document_name=name,
        text=text,
        chunk_index=0,
        chunk_type="child",
        heading_path=("Policy",),
        source_block_id=block_id,
    )


def _service(tmp_path: Path) -> tuple[KnowledgeBenchmarkGenerationService, KnowledgeEvaluationStore]:
    store = KnowledgeEvaluationStore(tmp_path / "evaluations.json")
    return KnowledgeBenchmarkGenerationService(rag_service=_RagService(), evaluation_store=store), store


def _generated_knowledge_payload(user: str) -> str:
    blueprint_text = user.split("Server blueprints:\n", 1)[1].split(
        "\n\nSampled evidence:\n", 1
    )[0]
    evidence_text = user.split("\n\nSampled evidence:\n", 1)[1].split(
        "\n\nJSON contract:\n", 1
    )[0]
    blueprints = json.loads(blueprint_text)
    evidence = {item["evidence_id"]: item for item in json.loads(evidence_text)}
    cases = []
    for index, blueprint in enumerate(blueprints):
        evidence_ids = list(blueprint.get("required_evidence_ids") or [])
        markers = [
            group[0]
            for group in blueprint.get("required_query_marker_groups") or []
            if group
        ]
        cases.append(
            {
                "blueprint_id": blueprint["blueprint_id"],
                "query": (
                    f"How does {' and '.join(markers)} apply in fixed case {index + 1}?"
                    if evidence_ids
                    else f"Which absent corpus-near exception applies in fixed case {index + 1}?"
                ),
                "evidence_ids": evidence_ids,
                "anchor_quotes": [
                    {
                        "evidence_id": evidence_id,
                        "quote": evidence[evidence_id]["text"][:20],
                    }
                    for evidence_id in evidence_ids
                ],
                "rationale": "Fixed evidence coverage.",
            }
        )
    return json.dumps({"dataset": {"name": "Generated", "cases": cases}})


def test_knowledge_snapshot_is_fixed_and_exposes_only_bounded_evidence(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    snapshot, warnings = service.snapshot_target(
        {
            "kind": "knowledge_version",
            "kb_id": "kb_target",
            "pipeline_version_id": "pipeline_v2",
            "document_ids": ["doc_alpha"],
        }
    )

    assert warnings == ["The selected scope exposes fewer than six stable evidence blocks."]
    assert snapshot["pipeline_version_id"] == "pipeline_v2"
    assert snapshot["document_count"] == 1
    assert snapshot["evidence_count"] == 2
    assert all(item["source_block_id"] for item in snapshot["_evidence"])
    public = service.public_target(snapshot)
    assert "_evidence" not in public
    assert "text" not in json.dumps(public)


def test_generation_contract_fixes_source_block_gold_and_reviews_no_result(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    snapshot, _ = service.snapshot_target(
        {"kind": "knowledge_version", "kb_id": "kb_target", "pipeline_version_id": "pipeline_v2"}
    )
    context = service.prepare_generation(
        snapshot=snapshot,
        case_count=6,
        locales=["zh-CN", "en-US"],
        requested_coverage=["factual_lookup", "multi_evidence"],
        no_result_count=1,
        seed=7,
    )
    cases = []
    for index, blueprint in enumerate(context["blueprints"]):
        evidence_ids = list(blueprint.get("required_evidence_ids") or [])
        markers = [
            group[0]
            for group in blueprint.get("required_query_marker_groups") or []
            if group
        ]
        cases.append(
            {
                "blueprint_id": blueprint["blueprint_id"],
                "query": f"What does {' and '.join(markers) or 'the corpus'} specify in case {index + 1}?",
                "evidence_ids": evidence_ids,
                "anchor_quotes": [
                    {
                        "evidence_id": evidence_id,
                        "quote": context["evidence_by_id"][evidence_id]["text"][:20],
                    }
                    for evidence_id in evidence_ids
                ],
                "rationale": "Checks fixed evidence.",
            }
        )
    generated = service.parse_generated_cases(
        json.dumps({"dataset": {"name": "Targeted", "cases": cases}}),
        snapshot=snapshot,
        context=context,
        expected_count=6,
    )

    positive = [item for item in generated["cases"] if not item["expected_no_result"]]
    negative = [item for item in generated["cases"] if item["expected_no_result"]]
    assert all(item["review_status"] == "pending" for item in generated["cases"])
    assert all(
        reference["match_mode"] == "source_block"
        and reference["chunk_id"]
        and reference["source_block_id"]
        for item in positive
        for reference in item["expected_refs"]
    )
    assert negative[0]["review_status"] == "pending"
    assert {"corpus_near", "hard_negative"}.issubset(negative[0]["tags"])
    assert negative[0]["targeting"]["context_refs"][0]["source_block_id"]


def test_cross_language_generation_does_not_require_source_marker_copy(
    tmp_path: Path,
) -> None:
    service, _ = _service(tmp_path)
    snapshot, _ = service.snapshot_target(
        {
            "kind": "knowledge_version",
            "kb_id": "kb_target",
            "pipeline_version_id": "pipeline_v2",
        }
    )
    context = service.prepare_generation(
        snapshot=snapshot,
        case_count=1,
        locales=["zh", "en"],
        requested_coverage=["cross_language"],
        no_result_count=0,
        seed=11,
    )
    blueprint = context["blueprints"][0]
    evidence_id = blueprint["required_evidence_ids"][0]
    evidence = context["evidence_by_id"][evidence_id]
    generated = service.parse_generated_cases(
        json.dumps(
            {
                "dataset": {
                    "cases": [
                        {
                            "blueprint_id": blueprint["blueprint_id"],
                            "query": "这项规定要求多长的发布审查周期？",
                            "evidence_ids": [evidence_id],
                            "anchor_quotes": [
                                {
                                    "evidence_id": evidence_id,
                                    "quote": evidence["text"][:20],
                                }
                            ],
                        }
                    ]
                }
            }
        ),
        snapshot=snapshot,
        context=context,
        expected_count=1,
    )

    assert generated["cases"][0]["targeting"]["query_type"] == "cross_language"


@pytest.mark.asyncio
async def test_strategy_tuning_generation_waits_for_hard_negative_review(
    tmp_path: Path,
) -> None:
    knowledge_service, evaluation_store = _service(tmp_path)

    async def generator_runner(
        model_id: str,
        system: str,
        user: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        return _generated_knowledge_payload(user)

    class _RagExecutor:
        def __init__(self) -> None:
            self.notifications = 0

        def notify(self) -> None:
            self.notifications += 1

    job_store = BenchmarkJobStore(tmp_path / "benchmark-jobs")
    rag_executor = _RagExecutor()
    executor = BenchmarkJobExecutor(
        job_store,
        service=SimpleNamespace(),
        generator_runner=generator_runner,
        evaluation_store=SimpleNamespace(),
        evaluation_service=SimpleNamespace(),
        evaluation_executor=SimpleNamespace(),
        knowledge_service=knowledge_service,
        rag_evaluation_store=evaluation_store,
        rag_evaluation_executor=rag_executor,
    )
    created = job_store.create_job(
        kind="generation",
        request={
            "target": {
                "kind": "knowledge_version",
                "kb_id": "kb_target",
                "pipeline_version_id": "pipeline_v2",
            },
            "generator_model_id": "test/model",
            "generation_purpose": "strategy_tuning",
            "case_count": 42,
            "locales": ["en-US"],
            "coverage": ["factual_lookup"],
            "no_result_count": 12,
            "seed": 4,
        },
    )
    claimed = job_store.claim_next_job()
    assert claimed is not None

    await executor._run_knowledge_generation(claimed)

    completed = job_store.require_job(created["job_id"])
    dataset = evaluation_store.get_set(str(completed["dataset_id"]))
    positives = [item for item in dataset["cases"] if not item["expected_no_result"]]
    negatives = [item for item in dataset["cases"] if item["expected_no_result"]]
    assert completed["status"] == "completed"
    assert completed["calibration"]["status"] == "awaiting_review"
    assert completed["calibration"]["pending_review_count"] == 42
    assert completed["evaluation_run_id"] is None
    assert rag_executor.notifications == 0
    assert dataset["benchmark_role"] == "promotion_evidence"
    assert dataset["provenance"]["generator"] == "modelmirror-targeted-rag-benchmark-v2"
    assert len(dataset["provenance"]["prompt_contract_hash"]) == 64
    assert dataset["provenance"]["repair_used"] is False
    assert len(dataset["provenance"]["generation_attempts"]) == 1
    assert len(positives) == 30
    assert len(negatives) == 12
    assert all(case["review_status"] == "pending" for case in dataset["cases"])
    assert all(
        reference["match_mode"] == "source_block"
        and reference["source_block_id"]
        for case in positives
        for reference in case["expected_refs"]
    )
    assert all(
        case["review_status"] == "pending"
        and {"corpus_near", "hard_negative"}.issubset(case["tags"])
        and case["targeting"]["context_refs"]
        for case in negatives
    )
    assert qualify_promotion_evidence(dataset)["status"] == "diagnostic_only"
    reviewed = json.loads(json.dumps(dataset))
    for case in reviewed["cases"]:
        if case["expected_no_result"]:
            case["review_status"] = "approved"
    assert qualify_promotion_evidence(reviewed)["status"] == "diagnostic_only"


def test_generation_rejects_unknown_evidence_and_quote_drift(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    snapshot, _ = service.snapshot_target(
        {"kind": "knowledge_version", "kb_id": "kb_target", "pipeline_version_id": "pipeline_v2"}
    )
    context = service.prepare_generation(
        snapshot=snapshot,
        case_count=6,
        locales=["en-US"],
        requested_coverage=["factual_lookup"],
        no_result_count=0,
        seed=1,
    )
    payload = []
    for index, blueprint in enumerate(context["blueprints"]):
        evidence_id = blueprint["required_evidence_ids"][0]
        marker = blueprint["required_query_marker_groups"][0][0]
        payload.append(
            {
                "blueprint_id": blueprint["blueprint_id"],
                "query": f"What does {marker} require for {blueprint['blueprint_id']}?",
                "evidence_ids": [evidence_id],
                "anchor_quotes": [{"evidence_id": evidence_id, "quote": "not in evidence"}],
            }
        )
    with pytest.raises(BenchmarkGenerationError, match="anchor quote"):
        service.parse_generated_cases(
            json.dumps({"dataset": {"cases": payload}}),
            snapshot=snapshot,
            context=context,
            expected_count=6,
        )


def test_generation_rejects_generic_query_and_preflight_respects_locale(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    snapshot, _ = service.snapshot_target(
        {"kind": "knowledge_version", "kb_id": "kb_target", "pipeline_version_id": "pipeline_v2"}
    )
    preflight = service.preflight(
        target_reference={
            "kind": "knowledge_version",
            "kb_id": "kb_target",
            "pipeline_version_id": "pipeline_v2",
        },
        requested_coverage=[],
        locales=["en-US"],
    )
    assert "cross_language" not in preflight["coverage"]["available"]

    context = service.prepare_generation(
        snapshot=snapshot,
        case_count=6,
        locales=["en-US"],
        requested_coverage=["factual_lookup"],
        no_result_count=0,
        seed=2,
    )
    payload = []
    for index, blueprint in enumerate(context["blueprints"]):
        evidence_id = blueprint["required_evidence_ids"][0]
        payload.append(
            {
                "blueprint_id": blueprint["blueprint_id"],
                "query": f"What does the document say about this topic in case {index + 1}?",
                "evidence_ids": [evidence_id],
                "anchor_quotes": [
                    {
                        "evidence_id": evidence_id,
                        "quote": context["evidence_by_id"][evidence_id]["text"][:20],
                    }
                ],
            }
        )
    with pytest.raises(BenchmarkGenerationError, match="not specific"):
        service.parse_generated_cases(
            json.dumps({"dataset": {"cases": payload}}),
            snapshot=snapshot,
            context=context,
            expected_count=6,
        )


def test_generated_set_publish_requires_calibration_ack_and_no_result_review(tmp_path: Path) -> None:
    _, store = _service(tmp_path)
    item = store.create_generated_set(
        "kb_target",
        "Generated",
        "",
        cases=[
            {
                "query": "Absent policy?",
                "expected_refs": [],
                "expected_no_result": True,
                "review_status": "pending",
            }
        ],
        provenance={"pipeline_version_id": "pipeline_v2"},
        coverage={},
        calibration={"status": "warning", "dataset_revision": 1},
    )
    with pytest.raises(EvaluationStateError, match="warnings"):
        store.publish_set(item["eval_set_id"], expected_revision=1)
    with pytest.raises(EvaluationStateError, match="explicit review"):
        store.publish_set(
            item["eval_set_id"],
            expected_revision=1,
            acknowledge_calibration_warnings=True,
        )
    updated = store.update_case(
        item["eval_set_id"],
        item["cases"][0]["case_id"],
        expected_revision=1,
        values={"review_status": "approved"},
    )
    assert updated["calibration"]["status"] == "stale"


def test_calibration_buckets_rank_and_warning_thresholds() -> None:
    dataset = {
        "cases": [
            {"case_id": f"case_{index}", "expected_no_result": False}
            for index in range(5)
        ]
    }
    case_results = {
        f"case_{index}": {
            "status": "completed",
            "ranking": [{"rank": 1, "relevance": 3}],
            "metrics": {},
        }
        for index in range(5)
    }
    result = BenchmarkJobExecutor._knowledge_calibration_result(
        dataset=dataset,
        run={
            "run_id": "run_1",
            "status": "succeeded",
            "case_results": {"pipeline_v2": case_results},
        },
        job={
            "dataset_revision": 1,
            "target": {"pipeline_version_id": "pipeline_v2", "checksum": "fixed"},
        },
    )
    assert result["status"] == "warning"
    assert result["counts"]["too_easy"] == 5


@pytest.mark.asyncio
async def test_knowledge_preflight_and_evidence_api_are_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, store = _service(tmp_path)
    monkeypatch.setattr(benchmark_api, "_knowledge_service", service)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        preflight = await client.post(
            "/api/benchmarks/generations/preflight",
            json={
                "target": {
                    "kind": "knowledge_version",
                    "kb_id": "kb_target",
                    "pipeline_version_id": "pipeline_v2",
                },
                "coverage": ["factual_lookup"],
                "locales": ["en-US"],
            },
        )
        assert preflight.status_code == 200
        payload = preflight.json()
        assert payload["valid"] is True
        assert "cross_language" not in payload["coverage"]["available"]
        serialized = json.dumps(payload)
        assert "Aurora policy requires" not in serialized
        assert "stored_path" not in serialized

        generated = store.create_generated_set(
            "kb_target",
            "Generated API set",
            "",
            cases=[
                {
                    "query": "What does Aurora require?",
                    "expected_refs": [
                        {
                            "document_id": "doc_alpha",
                            "chunk_id": "alpha_1",
                            "source_block_id": "block_alpha_1",
                            "match_mode": "source_block",
                            "relevance": 3,
                        }
                    ],
                    "expected_no_result": False,
                }
            ],
            provenance={
                "pipeline_version_id": "pipeline_v2",
                "target_reference": {
                    "kind": "knowledge_version",
                    "kb_id": "kb_target",
                    "pipeline_version_id": "pipeline_v2",
                    "document_ids": [],
                },
            },
            coverage={},
            calibration={"status": "pending", "dataset_revision": 1},
        )
        generated = store.review_case(
            generated["eval_set_id"],
            generated["cases"][0]["case_id"],
            expected_revision=generated["revision"],
            decision="approved",
            reason="Fixed evidence reviewed for calibration.",
            reviewer={"tenant_id": "local", "role": "provider_admin"},
        )
        benchmark_jobs = BenchmarkJobStore(tmp_path / "api-benchmark-jobs")
        monkeypatch.setattr(benchmark_api, "_job_store", benchmark_jobs)
        monkeypatch.setattr(
            benchmark_api, "_executor", SimpleNamespace(wake=lambda: None)
        )
        calibration = await client.post(
            "/api/benchmarks/calibrations",
            json={
                "dataset_id": generated["eval_set_id"],
                "dataset_revision": generated["revision"],
            },
        )
        assert calibration.status_code == 200
        calibration_job = benchmark_jobs.require_job(calibration.json()["job_id"])
        assert calibration_job["request"]["target"]["kind"] == "knowledge_version"
        assert calibration_job["calibration_runtime"] is None

        monkeypatch.setattr(rag_api, "_evaluation_store", store)
        monkeypatch.setattr(rag_api, "_rag_service", service.rag_service)
        case_id = generated["cases"][0]["case_id"]
        evidence = await client.get(
            f"/api/rag/evaluation-sets/{generated['eval_set_id']}/cases/{case_id}/evidence"
        )
        assert evidence.status_code == 200
        evidence_payload = evidence.json()
        assert evidence_payload["evidence_count"] == 1
        assert len(evidence_payload["evidence"][0]["text"]) <= 2000
        assert "stored_path" not in json.dumps(evidence_payload)

        pending = store.create_generated_set(
            "kb_target",
            "Pending hard-negative review",
            "",
            cases=[
                {
                    "query": "Does the Aurora policy require biometric escrow?",
                    "expected_refs": [],
                    "expected_no_result": True,
                    "review_status": "pending",
                    "tags": ["corpus_near", "hard_negative"],
                    "targeting": {
                        "blueprint_id": "negative-1",
                        "query_type": "no_result",
                        "context_refs": [
                            {
                                "document_id": "doc_alpha",
                                "chunk_id": "alpha_1",
                                "source_block_id": "block_alpha_1",
                            }
                        ],
                    },
                }
            ],
            provenance={
                "pipeline_version_id": "pipeline_v2",
                "target_reference": {
                    "kind": "knowledge_version",
                    "kb_id": "kb_target",
                    "pipeline_version_id": "pipeline_v2",
                    "document_ids": [],
                },
            },
            coverage={},
            calibration={"status": "awaiting_review", "dataset_revision": 1},
            benchmark_role="promotion_evidence",
        )
        blocked_calibration = await client.post(
            "/api/benchmarks/calibrations",
            json={"dataset_id": pending["eval_set_id"], "dataset_revision": 1},
        )
        assert blocked_calibration.status_code == 400
        assert "approve every generated case" in blocked_calibration.text.lower()
        assert len(benchmark_jobs.list_jobs()) == 1

        negative_case_id = pending["cases"][0]["case_id"]
        negative_evidence = await client.get(
            f"/api/rag/evaluation-sets/{pending['eval_set_id']}/cases/"
            f"{negative_case_id}/evidence"
        )
        assert negative_evidence.status_code == 200
        negative_payload = negative_evidence.json()
        assert negative_payload["evidence_role"] == "corpus_near_context"
        assert negative_payload["evidence_count"] == 1
        assert negative_payload["evidence"][0]["source_block_id"] == "block_alpha_1"


@pytest.mark.asyncio
async def test_generation_restart_reuses_dataset_without_second_model_call(
    tmp_path: Path,
) -> None:
    knowledge_service, evaluation_store = _service(tmp_path)
    calls = 0

    async def generator_runner(
        model_id: str,
        system: str,
        user: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        nonlocal calls
        calls += 1
        blueprint_text = user.split("Server blueprints:\n", 1)[1].split(
            "\n\nSampled evidence:\n", 1
        )[0]
        evidence_text = user.split("\n\nSampled evidence:\n", 1)[1].split(
            "\n\nJSON contract:\n", 1
        )[0]
        blueprints = json.loads(blueprint_text)
        evidence = {item["evidence_id"]: item for item in json.loads(evidence_text)}
        cases = []
        for index, blueprint in enumerate(blueprints):
            evidence_ids = list(blueprint.get("required_evidence_ids") or [])
            markers = [
                group[0]
                for group in blueprint.get("required_query_marker_groups") or []
                if group
            ]
            cases.append(
                {
                    "blueprint_id": blueprint["blueprint_id"],
                    "query": (
                        f"How does {' and '.join(markers)} apply in case {index + 1}?"
                        if evidence_ids
                        else f"Which absent exception applies in case {index + 1}?"
                    ),
                    "evidence_ids": evidence_ids,
                    "anchor_quotes": [
                        {
                            "evidence_id": evidence_id,
                            "quote": evidence[evidence_id]["text"][:20],
                        }
                        for evidence_id in evidence_ids
                    ],
                    "rationale": "Fixed evidence coverage.",
                }
            )
        return json.dumps({"dataset": {"name": "Generated", "cases": cases}})

    class _RagExecutor:
        def __init__(self) -> None:
            self.notifications = 0

        def notify(self) -> None:
            self.notifications += 1

    job_store = BenchmarkJobStore(tmp_path / "benchmark-jobs")
    rag_executor = _RagExecutor()
    executor = BenchmarkJobExecutor(
        job_store,
        service=SimpleNamespace(),
        generator_runner=generator_runner,
        evaluation_store=SimpleNamespace(),
        evaluation_service=SimpleNamespace(),
        evaluation_executor=SimpleNamespace(),
        knowledge_service=knowledge_service,
        rag_evaluation_store=evaluation_store,
        rag_evaluation_executor=rag_executor,
    )
    created = job_store.create_job(
        kind="generation",
        request={
            "target": {
                "kind": "knowledge_version",
                "kb_id": "kb_target",
                "pipeline_version_id": "pipeline_v2",
            },
            "generator_model_id": "test/model",
            "case_count": 6,
            "locales": ["en-US"],
            "coverage": ["factual_lookup"],
            "no_result_count": 0,
            "seed": 4,
        },
    )
    claimed = job_store.claim_next_job()
    assert claimed is not None
    await executor._run_knowledge_generation(claimed)
    first = job_store.require_job(created["job_id"])
    assert first["status"] == "completed"
    assert first["calibration"]["status"] == "awaiting_review"
    assert first["calibration"]["pending_review_count"] == 6
    assert first["dataset_id"]
    assert calls == 1

    await executor._run_knowledge_generation(first)
    second = job_store.require_job(created["job_id"])
    assert second["status"] == "completed"
    assert second["calibration"]["status"] == "awaiting_review"
    assert second["dataset_id"] == first["dataset_id"]
    assert calls == 1
    assert rag_executor.notifications == 0
