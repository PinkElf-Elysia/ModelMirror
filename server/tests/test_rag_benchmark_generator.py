from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import server.benchmarks.api as benchmark_api
import server.rag.api as rag_api
from server.benchmarks.executor import BenchmarkJobExecutor
from server.benchmarks.knowledge_generation import (
    KNOWLEDGE_COVERAGE,
    KnowledgeBenchmarkGenerationService,
)
from server.benchmarks.models import BenchmarkGenerationRequest
from server.benchmarks.service import BenchmarkGenerationError
from server.benchmarks.store import BenchmarkJobStore
from server.main import app
from server.rag.document_processor import StructuredDocumentProcessor
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
            "locales": ["zh-CN", "en-US"],
            "coverage": list(KNOWLEDGE_COVERAGE),
        }
    )

    assert request.case_count - request.no_result_count == 30
    assert request.no_result_count == 12


@pytest.mark.parametrize(
    "override",
    [
        {"case_count": 43},
        {"no_result_count": 0},
        {"locales": ["en-US"]},
        {"coverage": ["factual_lookup"]},
    ],
)
def test_strategy_tuning_generation_requires_exact_gold_v2_matrix(
    override: dict,
) -> None:
    values = {
        "target": {
            "kind": "knowledge_version",
            "kb_id": "kb_target",
            "pipeline_version_id": "pipeline_v2",
        },
        "generator_model_id": "test-model",
        "generation_purpose": "strategy_tuning",
        "case_count": 42,
        "no_result_count": 12,
        "locales": ["zh-CN", "en-US"],
        "coverage": list(KNOWLEDGE_COVERAGE),
    }
    values.update(override)

    with pytest.raises(ValueError, match="rag-gold-v2"):
        BenchmarkGenerationRequest.model_validate(values)


@pytest.mark.parametrize(
    ("case_count", "no_result_count"),
    [
        (41, 12),
        (35, 5),
    ],
)
def test_strategy_tuning_generation_rejects_unqualified_counts(
    case_count: int,
    no_result_count: int,
) -> None:
    with pytest.raises(ValueError, match="rag-gold-v2"):
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
                "locales": ["zh-CN", "en-US"],
                "coverage": list(KNOWLEDGE_COVERAGE),
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
    def __init__(self, *, formal: bool = False) -> None:
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
        if formal:
            self.version["document_results"] = [
                {
                    "source_id": document_id,
                    "filename": f"{document_id}.md",
                    "status": "completed",
                    "content_hash": marker * 64,
                    "chunk_count": 6,
                    "block_count": 6,
                }
                for document_id, marker in (
                    ("doc_alpha", "a"),
                    ("doc_beta", "b"),
                    ("doc_gamma", "c"),
                )
            ]
            for document_id in ("doc_alpha", "doc_beta", "doc_gamma"):
                existing = list(
                    self.vector_store.chunks.get(f"{version_id}_{document_id}", [])
                )
                for index in range(len(existing) + 1, 7):
                    existing.append(
                        _chunk(
                            f"{document_id.removeprefix('doc_')}_{index}",
                            document_id,
                            f"{document_id}.md",
                            f"block_{document_id.removeprefix('doc_')}_{index}",
                            f"Stable {document_id} evidence block {index} contains a distinct policy fact for formal benchmark allocation.",
                        )
                    )
                self.vector_store.chunks[f"{version_id}_{document_id}"] = existing

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

    def get_knowledge_source_block(
        self,
        kb_id: str,
        document_id: str,
        source_block_id: str,
        *,
        version_id: str | None = None,
    ) -> dict:
        assert kb_id == "kb_target"
        assert version_id == "pipeline_v2"
        for chunks in self.vector_store.chunks.values():
            for chunk in chunks:
                resolved_document_id = chunk.doc_id.removeprefix("pipeline_v2_")
                if (
                    resolved_document_id == document_id
                    and chunk.source_block_id == source_block_id
                ):
                    return {
                        "document_id": resolved_document_id,
                        "document_name": chunk.document_name,
                        "chunk_id": chunk.chunk_id,
                        "source_block_id": chunk.source_block_id,
                        "page_number": chunk.page_number,
                        "heading_path": list(chunk.heading_path),
                        "visual_kind": chunk.visual_kind,
                        "text": chunk.text,
                    }
        raise RuntimeError("missing source block")


def _chunk(
    chunk_id: str,
    source_id: str,
    name: str,
    block_id: str,
    text: str,
    *,
    chunk_type: str = "child",
) -> StoredVectorChunk:
    return StoredVectorChunk(
        chunk_id=chunk_id,
        kb_id="kb_target",
        doc_id=f"pipeline_v2_{source_id}",
        document_name=name,
        text=text,
        chunk_index=0,
        chunk_type=chunk_type,
        heading_path=("Policy",),
        source_block_id=block_id,
    )


def _service(
    tmp_path: Path, *, formal: bool = False
) -> tuple[KnowledgeBenchmarkGenerationService, KnowledgeEvaluationStore]:
    store = KnowledgeEvaluationStore(tmp_path / "evaluations.json")
    return KnowledgeBenchmarkGenerationService(
        rag_service=_RagService(formal=formal), evaluation_store=store
    ), store


def test_formal_preflight_blocks_insufficient_source_distribution(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)

    preflight = service.preflight(
        target_reference={
            "kind": "knowledge_version",
            "kb_id": "kb_target",
            "pipeline_version_id": "pipeline_v2",
        },
        requested_coverage=list(KNOWLEDGE_COVERAGE),
        locales=["zh-CN", "en-US"],
        generation_purpose="strategy_tuning",
        case_count=42,
        no_result_count=12,
    )

    assert preflight["valid"] is False
    assert {item["code"] for item in preflight["issues"]} >= {
        "formal_document_count",
        "formal_evidence_capacity",
    }


def test_formal_blueprints_are_publishable_by_construction(tmp_path: Path) -> None:
    service, _ = _service(tmp_path, formal=True)
    snapshot, _ = service.snapshot_target(
        {
            "kind": "knowledge_version",
            "kb_id": "kb_target",
            "pipeline_version_id": "pipeline_v2",
        }
    )

    context = service.prepare_generation(
        snapshot=snapshot,
        case_count=42,
        locales=["zh-CN", "en-US"],
        requested_coverage=list(KNOWLEDGE_COVERAGE),
        no_result_count=12,
        seed=31,
        generation_purpose="strategy_tuning",
    )

    positive = [
        item for item in context["blueprints"] if not item["expected_no_result"]
    ]
    negative = [
        item for item in context["blueprints"] if item["expected_no_result"]
    ]
    reference_counts: dict[str, int] = {}
    document_case_counts: dict[str, int] = {}
    evidence_by_id = context["evidence_by_id"]
    for blueprint in positive:
        documents = set()
        for evidence_id in blueprint["required_evidence_ids"]:
            reference_counts[evidence_id] = reference_counts.get(evidence_id, 0) + 1
            documents.add(evidence_by_id[evidence_id]["document_id"])
        for document_id in documents:
            document_case_counts[document_id] = document_case_counts.get(document_id, 0) + 1

    assert max(reference_counts.values()) <= 2
    assert set(document_case_counts) == {"doc_alpha", "doc_beta", "doc_gamma"}
    assert max(document_case_counts.values()) <= 12
    assert len({item["context_evidence_ids"][0] for item in negative}) == 12


def test_formal_evidence_catalog_excludes_heading_only_chunks(tmp_path: Path) -> None:
    service, _ = _service(tmp_path, formal=True)
    service.rag_service.vector_store.chunks["pipeline_v2_doc_alpha"].insert(
        0,
        _chunk(
            "alpha_heading",
            "doc_alpha",
            "alpha.md",
            "block_alpha_heading",
            "Policy handbook > Renewal\n## Renewal",
            chunk_type="heading",
        ),
    )

    snapshot, _ = service.snapshot_target(
        {
            "kind": "knowledge_version",
            "kb_id": "kb_target",
            "pipeline_version_id": "pipeline_v2",
        }
    )

    assert "block_alpha_heading" not in {
        item["source_block_id"] for item in snapshot["_evidence"]
    }
    assert all(item["chunk_type"] != "heading" for item in snapshot["_evidence"])


def test_rag_p0_r3_content_fixture_satisfies_formal_reference_capacity() -> None:
    root = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "task-cards"
        / "fixtures"
        / "rag-p0-r3-content-v2"
    )
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    processor = StructuredDocumentProcessor()
    content_counts: list[int] = []
    for item in manifest["files"]:
        path = root / item["file"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
        document = processor.process(
            path,
            filename=path.name,
            source_id=f"doc_{path.stem}",
        )
        content_counts.append(
            sum(block.kind != "heading" for block in document.blocks)
        )

    qualification = manifest["qualification"]
    assert content_counts == qualification["content_source_block_distribution"]
    assert sum(content_counts) == qualification["content_source_block_count"] == 18
    assert sum(min(count * 2, 12) for count in content_counts) >= qualification[
        "required_positive_reference_count"
    ]
    assert sum(content_counts) >= qualification["required_unique_negative_contexts"]


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


def test_corpus_snapshot_hash_ignores_pipeline_retrieval_implementation(tmp_path: Path) -> None:
    rag_service = _RagService()
    store = KnowledgeEvaluationStore(tmp_path / "evaluations.json")
    service = KnowledgeBenchmarkGenerationService(
        rag_service=rag_service,
        evaluation_store=store,
    )
    reference = {
        "kind": "knowledge_version",
        "kb_id": "kb_target",
        "pipeline_version_id": "pipeline_v2",
    }

    before, _ = service.snapshot_target(reference)
    rag_service.version["retrieval_profile"] = {"mode": "semantic", "top_k": 5}
    rag_service.version["embedding_profile"] = {
        "provider": "remote",
        "model": "different-model",
        "dimension": 1536,
    }
    after, _ = service.snapshot_target(reference)

    assert before["checksum"] != after["checksum"]
    assert before["corpus_snapshot_hash"] == after["corpus_snapshot_hash"]
    assert before["corpus_snapshot"] == after["corpus_snapshot"]


def test_gold_v2_generation_does_not_force_query_markers_and_requires_all_reviews(
    tmp_path: Path,
) -> None:
    service, _ = _service(tmp_path)
    snapshot, _ = service.snapshot_target(
        {"kind": "knowledge_version", "kb_id": "kb_target", "pipeline_version_id": "pipeline_v2"}
    )
    context = service.prepare_generation(
        snapshot=snapshot,
        case_count=6,
        locales=["zh-CN", "en-US"],
        requested_coverage=["paraphrase", "cross_language"],
        no_result_count=1,
        seed=11,
    )
    _, prompt = service.generation_prompt(
        snapshot=snapshot,
        context=context,
        case_count=6,
        locales=["zh-CN", "en-US"],
        seed=11,
    )

    assert all(
        "required_query_marker_groups" not in blueprint
        for blueprint in context["blueprints"]
    )
    assert "exact token" not in prompt

    generated = service.parse_generated_cases(
        _generated_knowledge_payload(prompt),
        snapshot=snapshot,
        context=context,
        expected_count=6,
    )
    assert all(case["review_status"] == "pending" for case in generated["cases"])


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
    assert all(
        reference["match_mode"] == "source_block"
        and reference["chunk_id"]
        and reference["source_block_id"]
        for item in positive
        for reference in item["expected_refs"]
    )
    assert all(item["review_status"] == "pending" for item in positive)
    assert negative[0]["review_status"] == "pending"
    assert {"corpus_near", "hard_negative"}.issubset(negative[0]["tags"])
    assert negative[0]["targeting"]["context_refs"][0]["source_block_id"]


@pytest.mark.asyncio
async def test_strategy_tuning_generation_waits_for_hard_negative_review(
    tmp_path: Path,
) -> None:
    knowledge_service, evaluation_store = _service(tmp_path, formal=True)

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
            "locales": ["zh-CN", "en-US"],
            "coverage": list(KNOWLEDGE_COVERAGE),
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
    assert completed["calibration"]["status"] == "review_required"
    assert completed["evaluation_run_id"] is None
    assert rag_executor.notifications == 0
    assert dataset["benchmark_role"] == "promotion_sealed"
    assert dataset["provenance"]["evidence_policy_version"] == "content-source-block-v1"
    assert dataset["calibration"]["status"] == "not_required"
    assert len(positives) == 30
    assert len(negatives) == 12
    assert all(
        reference["match_mode"] == "source_block"
        and reference["source_block_id"]
        for case in positives
        for reference in case["expected_refs"]
    )
    assert all(case["review_status"] == "pending" for case in positives)
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
        payload.append(
            {
                "blueprint_id": blueprint["blueprint_id"],
                "query": f"Grounded quote validation case {index + 1}?",
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


def test_generation_blocks_long_source_copy_and_preflight_respects_locale(tmp_path: Path) -> None:
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
        evidence_text = context["evidence_by_id"][evidence_id]["text"]
        payload.append(
            {
                "blueprint_id": blueprint["blueprint_id"],
                "query": f"{evidence_text[:45]} case {index + 1}?",
                "evidence_ids": [evidence_id],
                "anchor_quotes": [
                    {
                        "evidence_id": evidence_id,
                        "quote": context["evidence_by_id"][evidence_id]["text"][:20],
                    }
                ],
            }
        )
    with pytest.raises(BenchmarkGenerationError, match="copies at least 32"):
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

        pending_positive = store.create_generated_set(
            "kb_target",
            "Pending Gold v2 positive review",
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
                    "review_status": "pending",
                }
            ],
            provenance={
                "benchmark_contract_version": "rag-gold-v2",
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
        benchmark_jobs = BenchmarkJobStore(tmp_path / "api-benchmark-jobs")
        monkeypatch.setattr(benchmark_api, "_job_store", benchmark_jobs)
        monkeypatch.setattr(
            benchmark_api, "_executor", SimpleNamespace(wake=lambda: None)
        )
        blocked_positive_calibration = await client.post(
            "/api/benchmarks/calibrations",
            json={
                "dataset_id": pending_positive["eval_set_id"],
                "dataset_revision": 1,
            },
        )
        assert blocked_positive_calibration.status_code == 400
        assert "single formal run" in blocked_positive_calibration.text.lower()
        calibration = await client.post(
            "/api/benchmarks/calibrations",
            json={"dataset_id": generated["eval_set_id"], "dataset_revision": 1},
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
        assert "approve every corpus-near" in blocked_calibration.text.lower()
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
async def test_gold_v2_review_api_rejects_structurally_invalid_drafts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, store = _service(tmp_path)
    dataset = store.create_generated_set(
        "kb_target",
        "Structurally invalid Gold v2",
        "",
        cases=[
            {
                "query": "Which review window does Aurora require?",
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
                "review_status": "pending",
                "targeting": {
                    "query_type": "factual_lookup",
                    "locale": "en-US",
                },
            }
        ],
        provenance={
            "benchmark_contract_version": "rag-gold-v2",
            "pipeline_version_id": "pipeline_v2",
        },
        coverage={},
        calibration={"status": "awaiting_review", "dataset_revision": 1},
    )
    monkeypatch.setattr(rag_api, "_evaluation_store", store)
    monkeypatch.setattr(rag_api, "_rag_service", service.rag_service)
    case_id = dataset["cases"][0]["case_id"]

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/rag/evaluation-sets/{dataset['eval_set_id']}/cases/{case_id}/review",
            json={
                "expected_revision": 1,
                "decision": "rejected",
                "reason": "The draft itself is structurally invalid.",
            },
        )

    assert response.status_code == 400
    assert "manual review is blocked by structural qualification failures" in response.text
    unchanged = store.get_set(dataset["eval_set_id"])["cases"][0]
    assert unchanged["review_status"] == "pending"
    assert unchanged["review_evidence"] == {}


@pytest.mark.asyncio
async def test_gold_v2_review_api_records_server_evidence_for_positive_cases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, store = _service(tmp_path)
    dataset = store.create_generated_set(
        "kb_target",
        "Gold v2 review API",
        "",
        cases=[
            {
                "query": "Which review window does Aurora require?",
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
                "review_status": "pending",
                "targeting": {
                    "query_type": "paraphrase",
                    "locale": "en-US",
                    "leakage": {
                        "max_normalized_copy": 12,
                        "warning_threshold": 12,
                        "warning": True,
                        "blocked": False,
                    },
                },
            }
        ],
        provenance={
            "benchmark_contract_version": "rag-gold-v2",
            "pipeline_version_id": "pipeline_v2",
        },
        coverage={},
        calibration={"status": "awaiting_review", "dataset_revision": 1},
    )
    monkeypatch.setattr(rag_api, "_evaluation_store", store)
    monkeypatch.setattr(rag_api, "_rag_service", service.rag_service)
    monkeypatch.setattr(rag_api, "gold_v2_review_admission_blockers", lambda _: [])
    case_id = dataset["cases"][0]["case_id"]

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        missing_reason = await client.post(
            f"/api/rag/evaluation-sets/{dataset['eval_set_id']}/cases/{case_id}/review",
            json={"expected_revision": 1, "decision": "approved", "reason": ""},
        )
        assert missing_reason.status_code == 400

        approved = await client.post(
            f"/api/rag/evaluation-sets/{dataset['eval_set_id']}/cases/{case_id}/review",
            json={
                "expected_revision": 1,
                "decision": "approved",
                "reason": "The overlap is a necessary policy term, not an answer leak.",
            },
        )

    assert approved.status_code == 200, approved.text
    reviewed = approved.json()["cases"][0]
    assert reviewed["review_status"] == "approved"
    assert reviewed["review_evidence"]["source"] == "manual_ui"
    assert reviewed["review_evidence"]["decision"] == "approved"
    assert reviewed["review_evidence"]["dataset_revision"] == 1
    assert reviewed["review_evidence"]["reviewed_at"] > 0
    assert "reviewer" not in reviewed["review_evidence"]


@pytest.mark.asyncio
async def test_gold_v2_blocking_leakage_can_be_rejected_but_not_approved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, store = _service(tmp_path)
    dataset = store.create_generated_set(
        "kb_target",
        "Blocked leakage review",
        "",
        cases=[
            {
                "query": "Copied source wording",
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
                "review_status": "pending",
                "targeting": {
                    "query_type": "factual_lookup",
                    "locale": "en-US",
                    "leakage": {
                        "max_normalized_copy": 32,
                        "warning_threshold": 24,
                        "warning": True,
                        "blocked": True,
                    },
                },
            }
        ],
        provenance={
            "benchmark_contract_version": "rag-gold-v2",
            "pipeline_version_id": "pipeline_v2",
        },
        coverage={},
        calibration={"status": "awaiting_review", "dataset_revision": 1},
    )
    monkeypatch.setattr(rag_api, "_evaluation_store", store)
    monkeypatch.setattr(rag_api, "_rag_service", service.rag_service)
    monkeypatch.setattr(rag_api, "gold_v2_review_admission_blockers", lambda _: [])
    case_id = dataset["cases"][0]["case_id"]

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        approved = await client.post(
            f"/api/rag/evaluation-sets/{dataset['eval_set_id']}/cases/{case_id}/review",
            json={"expected_revision": 1, "decision": "approved", "reason": ""},
        )
        rejected = await client.post(
            f"/api/rag/evaluation-sets/{dataset['eval_set_id']}/cases/{case_id}/review",
            json={
                "expected_revision": 1,
                "decision": "rejected",
                "reason": "Source wording is copied verbatim.",
            },
        )

    assert approved.status_code == 400
    assert rejected.status_code == 200, rejected.text
    reviewed = rejected.json()["cases"][0]
    assert reviewed["review_status"] == "rejected"
    assert reviewed["review_evidence"]["decision"] == "rejected"


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
            "no_result_count": 1,
            "seed": 4,
        },
    )
    claimed = job_store.claim_next_job()
    assert claimed is not None
    await executor._run_knowledge_generation(claimed)
    first = job_store.require_job(created["job_id"])
    assert first["status"] == "completed"
    assert first["calibration"]["status"] == "awaiting_review"
    assert first["dataset_id"]
    assert calls == 1
    generated_set = evaluation_store.get_set(str(first["dataset_id"]))
    assert generated_set["provenance"]["benchmark_contract_version"] == "rag-gold-v1"
    assert all(
        case["review_status"] == "not_required"
        for case in generated_set["cases"]
        if not case["expected_no_result"]
    )

    await executor._run_knowledge_generation(first)
    second = job_store.require_job(created["job_id"])
    assert second["status"] == "completed"
    assert second["calibration"]["status"] == "awaiting_review"
    assert second["dataset_id"] == first["dataset_id"]
    assert calls == 1
    assert rag_executor.notifications == 0


@pytest.mark.asyncio
async def test_strategy_tuning_gold_generation_does_not_auto_repair_invalid_output(
    tmp_path: Path,
) -> None:
    knowledge_service, evaluation_store = _service(tmp_path, formal=True)
    calls = 0

    async def invalid_generator(*_args, **_kwargs) -> str:
        nonlocal calls
        calls += 1
        return "{}"

    job_store = BenchmarkJobStore(tmp_path / "benchmark-jobs")
    executor = BenchmarkJobExecutor(
        job_store,
        service=SimpleNamespace(),
        generator_runner=invalid_generator,
        evaluation_store=SimpleNamespace(),
        evaluation_service=SimpleNamespace(),
        evaluation_executor=SimpleNamespace(),
        knowledge_service=knowledge_service,
        rag_evaluation_store=evaluation_store,
        rag_evaluation_executor=SimpleNamespace(notify=lambda: None),
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
            "locales": ["zh-CN", "en-US"],
            "coverage": list(KNOWLEDGE_COVERAGE),
            "no_result_count": 12,
            "seed": 23,
        },
    )
    claimed = job_store.claim_next_job()
    assert claimed is not None

    with pytest.raises(BenchmarkGenerationError, match="missing dataset"):
        await executor._run_knowledge_generation(claimed)

    assert calls == 1
    attempts = job_store.require_job(created["job_id"])["generation_attempts"]
    assert [item["attempt"] for item in attempts] == ["initial"]
