from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from server.main import app
from server.model_router.admin_auth import reset_provider_admin_auth
from server.rag.chunking_receipt import (
    CHUNKING_RECEIPT_VERSION,
    candidate_namespace_fingerprint,
    chunker_profile_fingerprint,
)
from server.rag.api import (
    _resolve_evaluation_reproducibility,
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
    paired_primary_confidence_report,
    qualify_formal_evidence,
    qualify_promotion_evidence,
    validate_formal_run_admission,
    _case_review_checksum,
    _qualification_anchor_checksum,
    _published_gold_checksum,
)
from server.rag.evaluation_executor import KnowledgeEvaluationExecutor, _execution_slots
from server.rag.pipeline_executor import KnowledgePipelineExecutor
from server.rag.rag_service import RagService
from server.rag.vector_store import LocalJsonVectorStore
from server.xpert_runtime.run_registry import RunRegistry


def test_anchor_gold_requires_retrieved_chunk_to_cover_fixed_span() -> None:
    expected_ref = {
        "reference_id": "ref_anchor",
        "document_id": "doc_1",
        "source_block_id": "block_1",
        "source_block_hash": "a" * 64,
        "anchor_start": 120,
        "anchor_end": 148,
        "anchor_hash": "b" * 64,
        "match_mode": "source_block",
        "relevance": 3,
    }
    same_block_wrong_span = evaluate_retrieval_case(
        [
            {
                "chunk_id": "chunk_wrong",
                "source_document_id": "doc_1",
                "source_block_id": "block_1",
                "start_char": 0,
                "end_char": 100,
            }
        ],
        [expected_ref],
        expected_no_result=False,
        latency_ms=1,
        ks=[5],
    )
    covering_span = evaluate_retrieval_case(
        [
            {
                "chunk_id": "chunk_covering",
                "source_document_id": "doc_1",
                "source_block_id": "block_1",
                "start_char": 110,
                "end_char": 160,
            }
        ],
        [expected_ref],
        expected_no_result=False,
        latency_ms=1,
        ks=[5],
    )

    assert same_block_wrong_span["metrics"]["recall_at_5"] == 0.0
    assert covering_span["metrics"]["recall_at_5"] == 1.0


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
    evaluation_store = KnowledgeEvaluationStore(
        service.storage_dir / "evaluations.json",
        reproducibility_resolver=_resolve_evaluation_reproducibility,
    )
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
    if str((draft.get("retrieval_profile") or {}).get("mode") or "") != "vector":
        updated = await client.patch(
            f"/api/rag/pipeline/draft/{kb_id}",
            json={"retrieval_profile": {"mode": "vector"}},
        )
        assert updated.status_code == 200, updated.text
        draft = updated.json()
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


def _mark_pipeline_version_as_previously_active(
    service: RagService,
    version_id: str,
) -> None:
    """Model a historical active version without authorizing first activation."""

    with service._metadata_lock:  # noqa: SLF001 - compatibility fixture.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        version = metadata["pipeline_versions"][version_id]
        version["status"] = "active"
        version["activated_at"] = 1.0
        metadata["pipeline_active_versions"][str(version["kb_id"])] = version_id
        service._write_metadata_unlocked(metadata)  # noqa: SLF001


@pytest.mark.asyncio
async def test_case_review_requires_authenticated_server_evidence(
    evaluation_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, _, _, _ = evaluation_runtime
    pairing_secret = "rag-review-test-secret-at-least-32-characters"
    monkeypatch.setenv(
        "MODEL_MIRROR_PROVIDER_ADMIN_PAIRING_SECRET", pairing_secret
    )
    reset_provider_admin_auth()
    try:
        kb_id = await _create_kb(client, "review-auth")
        created = (
            await client.post(
                "/api/rag/evaluation-sets",
                json={"kb_id": kb_id, "name": "review-auth"},
            )
        ).json()
        injected = await client.post(
            f"/api/rag/evaluation-sets/{created['eval_set_id']}/cases",
            json={
                "expected_revision": created["revision"],
                "case": {
                    "query": "Which source contains the answer?",
                    "expected_refs": [{"document_id": "doc-a"}],
                    "review_status": "approved",
                },
            },
        )
        assert injected.status_code == 422

        added = (
            await client.post(
                f"/api/rag/evaluation-sets/{created['eval_set_id']}/cases",
                json={
                    "expected_revision": created["revision"],
                    "case": {
                        "query": "Which source contains the answer?",
                        "expected_refs": [{"document_id": "doc-a"}],
                    },
                },
            )
        ).json()
        case_id = added["cases"][0]["case_id"]
        review_url = (
            f"/api/rag/evaluation-sets/{created['eval_set_id']}"
            f"/cases/{case_id}/review"
        )
        review_payload = {
            "expected_revision": added["revision"],
            "decision": "approved",
            "reason": "Source evidence checked in the review workbench.",
        }
        unauthenticated = await client.post(review_url, json=review_payload)
        assert unauthenticated.status_code == 401
        assert unauthenticated.json()["detail"]["code"] == "admin_session_required"

        paired = await client.post(
            "/api/router/admin/session",
            headers={"Origin": "http://localhost"},
            json={"pairing_secret": pairing_secret},
        )
        assert paired.status_code == 200
        missing_csrf = await client.post(review_url, json=review_payload)
        assert missing_csrf.status_code == 403

        reviewed = await client.post(
            review_url,
            headers={"X-ModelMirror-CSRF": paired.json()["csrf_token"]},
            json=review_payload,
        )
        assert reviewed.status_code == 200, reviewed.text
        reviewed_case = reviewed.json()["cases"][0]
        assert reviewed_case["review_status"] == "approved"
        evidence = reviewed_case["review_evidence"]
        assert evidence["decision"] == "approved"
        assert evidence["source"] == "authenticated_ui"
        assert evidence["reviewer"] == {
            "tenant_id": "local",
            "role": "provider_admin",
        }
        assert evidence["dataset_revision"] == reviewed.json()["revision"]
        assert len(evidence["case_checksum"]) == 64
        assert isinstance(evidence["reviewed_at"], float)

        edited = await client.patch(
            f"/api/rag/evaluation-sets/{created['eval_set_id']}/cases/{case_id}",
            json={
                "expected_revision": reviewed.json()["revision"],
                "case": {
                    "query": "Which exact source contains the answer?",
                    "expected_refs": [{"document_id": "doc-a"}],
                },
            },
        )
        assert edited.status_code == 200, edited.text
        edited_case = edited.json()["cases"][0]
        assert edited_case["review_status"] == "not_required"
        assert "review_evidence" not in edited_case
    finally:
        reset_provider_admin_auth()


@pytest.mark.asyncio
async def test_4a_fulltext_formal_fixture_is_blocked_until_lexical_v2(
    evaluation_runtime,
) -> None:
    client, _, _, _, _ = evaluation_runtime
    kb_id = await _create_kb(client, "formal-lexical-v2-gate")
    document_id = await _upload_text(
        client,
        kb_id,
        "formal.txt",
        "Fixed local evidence for the deferred fulltext Formal fixture.",
    )
    draft = await client.patch(
        f"/api/rag/pipeline/draft/{kb_id}",
        json={"retrieval_profile": {"mode": "fulltext"}},
    )
    assert draft.status_code == 200, draft.text

    blocked = await client.post(
        f"/api/rag/pipeline/draft/{kb_id}/execute",
        json={
            "draft_version": draft.json()["version"],
            "source_document_ids": [document_id],
            "xpert_file_refs": [],
        },
    )

    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["detail"]["code"] == (
        "rag_content_contract_legacy_read_only"
    )


@pytest.mark.asyncio
async def test_formal_api_requires_qualified_target_identity_after_gold_checks(
    evaluation_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, service, pipeline_executor, evaluation_executor, _ = evaluation_runtime
    pairing_secret = "formal-api-test-secret-at-least-32-characters"
    monkeypatch.setenv(
        "MODEL_MIRROR_PROVIDER_ADMIN_PAIRING_SECRET", pairing_secret
    )
    reset_provider_admin_auth()
    try:
        kb_id = await _create_kb(client, "formal-api")
        document_ids = []
        for document_index in range(3):
            paragraphs = [
                (
                    f"Document {document_index} evidence block {block_index}. "
                    f"Stable marker D{document_index}B{block_index} and supporting detail."
                )
                for block_index in range(20)
            ]
            document_ids.append(
                await _upload_text(
                    client,
                    kb_id,
                    f"formal-{document_index}.txt",
                    "\n\n".join(paragraphs),
                )
            )
        fulltext_draft = await client.patch(
            f"/api/rag/pipeline/draft/{kb_id}",
            json={
                "retrieval_profile": {
                    "mode": "fulltext",
                    "no_result_policy": "absolute_relevance_v1",
                    "min_lexical_confidence": 0.5,
                }
            },
        )
        assert fulltext_draft.status_code == 200, fulltext_draft.text
        baseline_job = await _execute_draft(
            client, pipeline_executor, kb_id, document_ids
        )
        candidate_job = await _execute_draft(
            client, pipeline_executor, kb_id, document_ids
        )
        baseline_id = str(baseline_job["candidate_version_id"])
        candidate_id = str(candidate_job["candidate_version_id"])
        corpus = service.pipeline_corpus_snapshot(baseline_id)
        corpus_evidence = service.pipeline_corpus_evidence(baseline_id)
        blocks_by_document = {
            str(document["document_id"]): list(document["source_blocks"])
            for document in corpus_evidence["documents"]
        }
        assert all(len(blocks) >= 14 for blocks in blocks_by_document.values())

        chunk_by_block: dict[tuple[str, str], str] = {}
        for document_id in document_ids:
            indexed_id = f"{baseline_id}_{document_id}"
            for chunk in service.vector_store.list_document_chunks(indexed_id):
                key = (document_id, str(chunk.source_block_id or ""))
                if key[1] and key not in chunk_by_block:
                    chunk_by_block[key] = str(chunk.chunk_id)

        cases: list[dict] = []
        ordered_documents = sorted(blocks_by_document)
        for document_offset, document_id in enumerate(ordered_documents):
            blocks = blocks_by_document[document_id]
            for local_index in range(10):
                global_index = document_offset * 10 + local_index
                block = blocks[local_index]
                block_id = str(block["source_block_id"])
                anchor_start = int(block["start_char"])
                anchor_end = min(
                    int(block["end_char"]),
                    anchor_start + max(8, min(20, len(str(block["text"])))),
                )
                anchor_payload = {
                    "contract_version": "rag-anchor-v1",
                    "document_id": document_id,
                    "source_block_id": block_id,
                    "block_hash": str(block["block_hash"]),
                    "anchor_start": anchor_start,
                    "anchor_end": anchor_end,
                }
                cases.append(
                    {
                        "query": f"Stable answer query {global_index}",
                        "expected_refs": [
                            {
                                "document_id": document_id,
                                "source_block_id": block_id,
                                "source_block_hash": str(block["block_hash"]),
                                "anchor_start": anchor_start,
                                "anchor_end": anchor_end,
                                "anchor_hash": hashlib.sha256(
                                    json.dumps(
                                        anchor_payload,
                                        ensure_ascii=False,
                                        sort_keys=True,
                                        separators=(",", ":"),
                                    ).encode("utf-8")
                                ).hexdigest(),
                                "match_mode": "source_block",
                            }
                        ],
                        "expected_no_result": False,
                        "tags": ["positive"],
                        "targeting": {
                            "locale": "zh" if global_index % 2 == 0 else "en",
                            "query_type": [
                                "factual_lookup",
                                "paraphrase",
                                "section_context",
                                "cross_language",
                                "multi_evidence",
                                "confusable_content",
                            ][global_index // 5],
                        },
                    }
                )
            for local_index in range(4):
                global_index = document_offset * 4 + local_index
                block = blocks[10 + local_index]
                block_id = str(block["source_block_id"])
                query = f"Absent nearby policy {global_index}"
                normalized_query = "".join(
                    character
                    for character in query.casefold()
                    if character.isalnum() or character == "_"
                )
                query_hash = hashlib.sha256(
                    json.dumps(
                        normalized_query,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                cases.append(
                    {
                        "query": query,
                        "expected_refs": [],
                        "expected_no_result": True,
                        "review_status": "pending",
                        "tags": ["hard_negative", "corpus_near"],
                        "targeting": {
                            "locale": "zh" if global_index % 2 == 0 else "en",
                            "context_refs": [
                                {
                                    "document_id": document_id,
                                    "source_block_id": block_id,
                                    "source_block_hash": str(block["block_hash"]),
                                    "chunk_id": chunk_by_block[
                                        (document_id, block_id)
                                    ],
                                }
                            ],
                            "full_corpus_verification": {
                                "contract_version": "rag-no-result-verification-v1",
                                "completed": True,
                                "method": "full_corpus_lexical_scan_v1",
                                "query_hash": query_hash,
                                "corpus_snapshot_checksum": corpus["checksum"],
                                "scanned_document_count": len(blocks_by_document),
                                "scanned_source_block_count": sum(
                                    len(items) for items in blocks_by_document.values()
                                ),
                                "top_matches": [],
                            },
                        },
                    }
                )
        store = evaluation_executor.store
        evaluation_set = store.create_generated_set(
            kb_id,
            "Formal reviewed Gold",
            "",
            cases=cases,
            provenance={
                "generator": "modelmirror-targeted-rag-benchmark-v3",
                "generation_job_id": "local-formal-e2e",
                "generator_model_id": "local-no-network-fixture",
                "target_checksum": "d" * 64,
                "source_summary_hash": "e" * 64,
                "evidence_hash": "f" * 64,
                "blueprint_hash": "1" * 64,
                "prompt_contract_hash": "2" * 64,
                "seed": 11,
                "repair_used": False,
                "generation_attempts": [
                    {"attempt": "initial", "error_code": None, "diagnostics": {}}
                ],
                "pipeline_version_id": baseline_id,
            },
            coverage={},
            calibration={"status": "calibrated", "dataset_revision": 1},
            benchmark_role="held_out_qualification",
        )
        for case in evaluation_set["cases"]:
            evaluation_set = store.review_case(
                evaluation_set["eval_set_id"],
                case["case_id"],
                expected_revision=evaluation_set["revision"],
                decision="approved",
                reason="Fixed local test evidence reviewed.",
                reviewer={"tenant_id": "local", "role": "provider_admin"},
            )

        published = await client.post(
            f"/api/rag/evaluation-sets/{evaluation_set['eval_set_id']}/publish",
            json={"expected_revision": evaluation_set["revision"]},
        )
        assert published.status_code == 200, published.text
        assert published.json()["benchmark_contract_version"] == "rag-gold-v3"
        assert published.json()["qualification_manifest"]["status"] == "qualified"
        assert "Stable marker" not in published.text

        unauthenticated = await client.post(
            "/api/rag/evaluation-runs",
            json={
                "eval_set_id": evaluation_set["eval_set_id"],
                "eval_set_version": 1,
                "targets": [
                    {"version_id": baseline_id},
                    {"version_id": candidate_id},
                ],
                "baseline_version_id": baseline_id,
                "run_mode": "formal",
            },
        )
        assert unauthenticated.status_code == 401
        paired = await client.post(
            "/api/router/admin/session",
            headers={"Origin": "http://localhost"},
            json={"pairing_secret": pairing_secret},
        )
        assert paired.status_code == 200
        created = await client.post(
            "/api/rag/evaluation-runs",
            headers={"X-ModelMirror-CSRF": paired.json()["csrf_token"]},
            json={
                "eval_set_id": evaluation_set["eval_set_id"],
                "eval_set_version": 1,
                "targets": [
                    {"version_id": baseline_id},
                    {"version_id": candidate_id},
                ],
                "baseline_version_id": baseline_id,
                "run_mode": "formal",
            },
        )
        assert created.status_code == 400, created.text
        assert "ready production embedding identity" in created.text
    finally:
        reset_provider_admin_auth()


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


def _formal_gold_snapshot() -> dict:
    from hashlib import sha256
    import json

    def digest(value: object) -> str:
        return sha256(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    cases: list[dict] = []
    query_types = [
        "factual_lookup",
        "paraphrase",
        "section_context",
        "cross_language",
        "multi_evidence",
        "confusable_content",
    ]
    for index in range(30):
        anchor_start = index * 100
        anchor_end = anchor_start + 20
        document_id = f"doc-{index % 3}"
        source_block_id = f"answer-block-{index}"
        block_hash = "b" * 64
        case = {
            "case_id": f"positive-{index}",
            "query": f"Answerable question {index}",
            "expected_refs": [
                {
                    "reference_id": f"ref-{index}",
                    "document_id": document_id,
                    "source_block_id": source_block_id,
                    "source_block_hash": block_hash,
                    "anchor_start": anchor_start,
                    "anchor_end": anchor_end,
                    "anchor_hash": digest(
                        {
                            "contract_version": "rag-anchor-v1",
                            "document_id": document_id,
                            "source_block_id": source_block_id,
                            "block_hash": block_hash,
                            "anchor_start": anchor_start,
                            "anchor_end": anchor_end,
                        }
                    ),
                    "match_mode": "source_block",
                    "relevance": 1,
                }
            ],
            "expected_no_result": False,
            "review_status": "approved",
            "tags": ["positive"],
            "targeting": {
                "locale": "en" if index % 2 else "zh",
                "query_type": query_types[index // 5],
            },
        }
        case["review_evidence"] = {
            "decision": "approved",
            "reviewed_at": 1_000.0 + index,
            "dataset_revision": index + 2,
            "source": "authenticated_ui",
            "reviewer": {"tenant_id": "local", "role": "provider_admin"},
            "reason": "reviewed",
            "case_checksum": _case_review_checksum(case),
        }
        cases.append(case)
    for index in range(12):
        case = {
            "case_id": f"negative-{index}",
            "query": f"Unanswerable nearby question {index}",
            "expected_refs": [],
            "expected_no_result": True,
            "review_status": "approved",
            "tags": ["hard_negative", "corpus_near"],
            "targeting": {
                "locale": "en" if index % 2 else "zh",
                "context_refs": [
                    {
                        "document_id": f"doc-{index % 3}",
                        "source_block_id": f"context-block-{index}",
                        "source_block_hash": "c" * 64,
                    }
                ],
            },
        }
        case["review_evidence"] = {
            "decision": "approved",
            "reviewed_at": 2_000.0 + index,
            "dataset_revision": index + 32,
            "source": "authenticated_ui",
            "reviewer": {"tenant_id": "local", "role": "provider_admin"},
            "reason": "answer absence checked",
            "case_checksum": _case_review_checksum(case),
        }
        cases.append(case)
    corpus = {
        "contract_version": "rag-corpus-snapshot-v1",
        "knowledge_base_hash": "a" * 64,
        "documents": [
            {
                "document_id": f"doc-{document_index}",
                "content_hash": f"{document_index + 1}" * 64,
                "document_hash": f"{document_index + 4}" * 64,
                "source_blocks": [
                    *[
                        {
                            "source_block_id": f"answer-block-{case_index}",
                            "block_hash": "b" * 64,
                        }
                        for case_index in range(30)
                        if case_index % 3 == document_index
                    ],
                    *[
                        {
                            "source_block_id": f"context-block-{case_index}",
                            "block_hash": "c" * 64,
                        }
                        for case_index in range(12)
                        if case_index % 3 == document_index
                    ],
                ],
            }
            for document_index in range(3)
        ],
    }
    corpus["checksum"] = sha256(
        json.dumps(
            corpus, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    for case in cases[30:]:
        case["targeting"]["full_corpus_verification"] = {
            "contract_version": "rag-no-result-verification-v1",
            "completed": True,
            "method": "full_corpus_lexical_scan_v1",
            "query_hash": digest(
                "".join(
                    character
                    for character in case["query"].casefold()
                    if character.isalnum() or character == "_"
                )
            ),
            "corpus_snapshot_checksum": corpus["checksum"],
            "scanned_document_count": 3,
            "scanned_source_block_count": 42,
            "top_matches": [],
        }
        case["review_evidence"]["case_checksum"] = _case_review_checksum(case)
    snapshot = {
        "kb_id": "kb-formal",
        "version_id": "evalsetver-formal",
        "published_at": 3_000.0,
        "benchmark_contract_version": "rag-gold-v3",
        "benchmark_role": "held_out_qualification",
        "cases": cases,
        "coverage": {},
        "provenance": {
            "generator": "modelmirror-targeted-rag-benchmark-v3",
            "generation_job_id": "benchmark-job-formal",
            "generator_model_id": "test-generator",
            "target_checksum": "d" * 64,
            "source_summary_hash": "e" * 64,
            "evidence_hash": "f" * 64,
            "blueprint_hash": "1" * 64,
            "prompt_contract_hash": "2" * 64,
            "seed": 7,
            "repair_used": False,
            "generation_attempts": [
                {"attempt": "initial", "error_code": None, "diagnostics": {}}
            ],
        },
        "calibration": {},
        "corpus_snapshot": corpus,
    }
    snapshot["qualification_manifest"] = {
        "contract_version": "rag-gold-qualification-v3",
        "dataset_role": "held_out_qualification",
        "corpus_checksum": corpus["checksum"],
        "anchor_checksum": _qualification_anchor_checksum(cases),
        "tuner_usage_lineage": [],
    }
    snapshot["checksum"] = _published_gold_checksum(snapshot)
    return snapshot


def test_formal_evidence_fails_closed_on_tampering_or_legacy_contract() -> None:
    snapshot = _formal_gold_snapshot()
    qualified = qualify_formal_evidence(snapshot)
    assert qualified["qualified"] is True
    assert qualified["status"] == "qualified"

    snapshot["cases"][0]["query"] = "tampered after review"
    tampered = qualify_formal_evidence(snapshot)
    assert tampered["qualified"] is False
    assert {
        check["id"] for check in tampered["checks"] if not check["passed"]
    } >= {"published_checksum", "trusted_case_reviews"}

    provenance_tampered = _formal_gold_snapshot()
    provenance_tampered["provenance"]["prompt_contract_hash"] = "0" * 64
    assert qualify_formal_evidence(provenance_tampered)["qualified"] is False

    legacy = _formal_gold_snapshot()
    legacy["benchmark_contract_version"] = "rag-gold-v2"
    legacy["benchmark_role"] = "promotion_evidence"
    legacy["checksum"] = _published_gold_checksum(legacy)
    rejected = qualify_formal_evidence(legacy)
    assert rejected["qualified"] is False
    assert next(
        check for check in rejected["checks"] if check["id"] == "gold_contract_v3"
    )["passed"] is False


def test_formal_evidence_rejects_coverage_imbalance_and_near_duplicates() -> None:
    imbalanced = _formal_gold_snapshot()
    for case in imbalanced["cases"][:30]:
        case["targeting"]["query_type"] = "factual_lookup"
        case["review_evidence"]["case_checksum"] = _case_review_checksum(case)
    imbalanced["checksum"] = _published_gold_checksum(imbalanced)
    qualification = qualify_formal_evidence(imbalanced)
    assert qualification["qualified"] is False
    assert next(
        check
        for check in qualification["checks"]
        if check["id"] == "positive_query_type_balance"
    )["passed"] is False

    duplicated = _formal_gold_snapshot()
    duplicated["cases"][0]["query"] = (
        "one two three four five six seven eight nine alpha"
    )
    duplicated["cases"][1]["query"] = (
        "one two three four five six seven eight nine beta"
    )
    for case in duplicated["cases"][:2]:
        case["review_evidence"]["case_checksum"] = _case_review_checksum(case)
    duplicated["checksum"] = _published_gold_checksum(duplicated)
    qualification = qualify_formal_evidence(duplicated)
    assert qualification["qualified"] is False
    assert next(
        check for check in qualification["checks"] if check["id"] == "unique_queries"
    )["passed"] is False


def test_leakage_warning_approval_requires_a_human_reason(tmp_path: Path) -> None:
    store = KnowledgeEvaluationStore(tmp_path / "evaluation.json")
    evaluation_set = store.create_generated_set(
        "kb-a",
        "leakage warning",
        "",
        cases=[
            {
                "query": "semantic query",
                "expected_refs": [
                    {
                        "document_id": "doc-a",
                        "source_block_id": "block-a",
                        "match_mode": "source_block",
                    }
                ],
                "targeting": {
                    "query_type": "paraphrase",
                    "locale": "en",
                    "leakage_warning": {"threshold": 12},
                },
            }
        ],
        provenance={},
        coverage={},
        calibration={"status": "calibrated", "dataset_revision": 1},
    )
    with pytest.raises(ValueError, match="explicit human review reason"):
        store.review_case(
            evaluation_set["eval_set_id"],
            evaluation_set["cases"][0]["case_id"],
            expected_revision=1,
            decision="approved",
            reason="",
            reviewer={"tenant_id": "local", "role": "provider_admin"},
        )


def test_synthetic_future_formal_admission_schema_requires_same_corpus_and_complete_target_identity() -> None:
    """Exercise schema admission only; this does not qualify a runtime index."""

    snapshot = _formal_gold_snapshot()
    corpus_checksum = snapshot["corpus_snapshot"]["checksum"]

    def target(version_id: str, fingerprint: str) -> dict:
        chunker_profile = {
            "strategy": "recursive_estimated_token",
            "chunk_size": 500,
            "chunk_overlap": 50,
            "size_unit": "estimated_tokens",
            "token_estimator": "mixed_cjk_latin_v1",
            "chunk_contract_version": "rag-chunker-estimated-token-v1",
        }
        chunking_receipt = {
            "receipt_version": CHUNKING_RECEIPT_VERSION,
            "contract_version": "rag-chunker-estimated-token-v1",
            "strategy": "recursive_estimated_token",
            "size_unit": "estimated_tokens",
            "token_estimator": "mixed_cjk_latin_v1",
            "chunker_profile_fingerprint": chunker_profile_fingerprint(
                chunker_profile
            ),
            "candidate_version_id": version_id,
            "candidate_namespace_fingerprint": candidate_namespace_fingerprint(
                f"kb-formal::v3::{version_id}"
            ),
            "raw_candidate_count": 3,
            "heading_block_count": 0,
            "heading_prefix_truncated_count": 0,
            "heading_overlap_policy": "structural_prefix_floor_v1",
            "max_heading_prefix_tokens": 0,
            "prefix_exceeds_configured_overlap_count": 0,
            "max_effective_index_overlap_budget_tokens": 50,
            "max_effective_context_overlap_budget_tokens": 50,
            "generated_item_count": 0,
            "generated_item_chunk_count": 0,
            "generated_item_rejected_count": 0,
            "generated_item_rejection_reasons": {},
            "deduplicated_chunk_count": 0,
            "final_chunk_count": 3,
            "chunk_sequence_hash": "f" * 64,
        }
        chunking_receipt_fingerprint = hashlib.sha256(
            json.dumps(
                chunking_receipt,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        return {
            "target_id": version_id,
            "version_id": version_id,
            "retrieval": {},
            "corpus_snapshot_hash": corpus_checksum,
            "version_evidence": {
                "schema_version": "rag-version-evidence-v1",
                "kb_id": "kb-formal",
                "version_id": version_id,
                "chunk_count": 3,
                "version_fingerprint": fingerprint,
                "configuration_fingerprint": fingerprint[::-1],
                "source_manifest_fingerprint": "d" * 64,
                "chunking_receipt": chunking_receipt,
                "chunking_receipt_fingerprint": chunking_receipt_fingerprint,
                "chunking_receipt_status": "current",
                "chunker": {
                    "profile": chunker_profile,
                    "fingerprint": chunking_receipt[
                        "chunker_profile_fingerprint"
                    ],
                },
                "index_owner_version_id": version_id,
                "candidate_namespace_fingerprint": chunking_receipt[
                    "candidate_namespace_fingerprint"
                ],
                "processor": {
                    "mode": "general",
                    "vision_enabled": False,
                    "fingerprint": "c" * 64,
                },
                "embedding": {
                    "effective": {
                        "provider": "openai_compatible",
                        "model": "bge-m3",
                        "dimension": 1024,
                        "degraded": False,
                        "ready": True,
                        "reason": None,
                        "access_mode": "managed",
                        "status": "ready",
                        "embedding_space_fingerprint": "e" * 64,
                    }
                },
                "retrieval": {
                    "mode": "hybrid",
                    "top_k": 5,
                    "rerank_enabled": False,
                    "rerank_provider": "none",
                    "rerank_model": "",
                    "rerank_top_n": 0,
                },
                "index_contract": {
                    "contract_version": "rag-index-contract-v3",
                    "index_schema_version": 3,
                    "retrieval_mode": "hybrid",
                    "vector": {
                        "required": True,
                        "embedding_space_fingerprint": "e" * 64,
                        "dimension": 1024,
                        "distance_contract": "cosine_v1",
                    },
                    "lexical": {"required": True, "backend": "sqlite_fts5"},
                },
                "content_index_contract": {
                    "contract_version": "rag-content-index-contract-v1",
                    "chunker_contract_version": "rag-chunker-estimated-token-v1",
                    "lexical_contract_version": "sqlite-fts5-lexical-v2",
                    "parser_contract_version": "canonical-structured-parser-v2",
                    "status": "current",
                    "components": {
                        "chunker": "current",
                        "lexical": "current",
                        "parser": "current",
                    },
                },
                "vector_backend_readiness": {
                    "configured_backend": "chroma",
                    "effective_backend": "chroma",
                    "ready": True,
                    "reason_code": None,
                    "distance_contract": "cosine_v1",
                },
                "runtime_vector_backend_readiness": {
                    "configured_backend": "chroma",
                    "effective_backend": "chroma",
                    "ready": True,
                    "reason_code": None,
                    "distance_contract": "cosine_v1",
                },
            },
        }

    baseline = target("pipeline-baseline", "a" * 64)
    candidate = target("pipeline-candidate", "b" * 64)
    admitted = validate_formal_run_admission(
        snapshot,
        [baseline, candidate],
        baseline_version_id="pipeline-baseline",
    )
    assert admitted["comparability"]["comparable"] is True
    assert admitted["execution_manifest"]["contract_version"] == "rag-eval-v2"
    assert len(admitted["execution_manifest"]["execution_seed"]) == 64
    assert admitted["execution_manifest"]["targets"][0]["processor"]["mode"] == "general"
    assert "rerank" in admitted["execution_manifest"]["targets"][0]
    assert admitted["execution_manifest"]["targets"][0][
        "content_index_contract"
    ]["status"] == "current"

    candidate["retrieval"] = {"top_k": 3}
    with pytest.raises(ValueError, match="per-run retrieval overrides"):
        validate_formal_run_admission(
            snapshot,
            [baseline, candidate],
            baseline_version_id="pipeline-baseline",
        )
    candidate["retrieval"] = {}

    candidate["corpus_snapshot_hash"] = "f" * 64
    with pytest.raises(ValueError, match="same immutable corpus"):
        validate_formal_run_admission(
            snapshot,
            [baseline, candidate],
            baseline_version_id="pipeline-baseline",
        )

    candidate = target("pipeline-candidate", "b" * 64)
    candidate["version_evidence"].pop("configuration_fingerprint")
    with pytest.raises(ValueError, match="target identity"):
        validate_formal_run_admission(
            snapshot,
            [baseline, candidate],
            baseline_version_id="pipeline-baseline",
        )


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
        "reviewed_hard_negative": 0,
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


def test_failed_cases_remain_in_quality_and_latency_denominators() -> None:
    positive = evaluate_retrieval_case(
        [{"chunk_id": "answer", "source_document_id": "doc-a", "score": 0.9}],
        [{"document_id": "doc-a"}],
        ks=[1, 5, 10],
        latency_ms=10.0,
    )
    negative = evaluate_retrieval_case(
        [],
        [],
        ks=[1, 5, 10],
        expected_no_result=True,
        latency_ms=20.0,
    )
    failed_positive = {
        "status": "failed",
        "metrics": {},
        "latency_ms": 2_000.0,
        "expected_no_result": False,
        "no_result": True,
        "warning_count": 0,
    }
    failed_negative = {
        "status": "failed",
        "metrics": {},
        "latency_ms": 3_000.0,
        "expected_no_result": True,
        "no_result": True,
        "warning_count": 0,
    }

    aggregate = aggregate_target_metrics(
        [positive, failed_positive, negative, failed_negative],
        ks=[1, 5, 10],
    )

    assert aggregate["case_count"] == 4
    assert aggregate["completed_case_count"] == 2
    assert aggregate["failed_case_count"] == 2
    assert aggregate["positive_case_count"] == 2
    assert aggregate["completed_positive_case_count"] == 1
    assert aggregate["failed_positive_case_count"] == 1
    assert aggregate["no_result_case_count"] == 2
    assert aggregate["completed_no_result_case_count"] == 1
    assert aggregate["failed_no_result_case_count"] == 1
    assert aggregate["positive_quality_denominator"] == 2
    assert aggregate["no_result_quality_denominator"] == 2
    assert aggregate["recall_at_5"] == 0.5
    assert aggregate["citation_precision_at_5"] == 0.1
    assert aggregate["no_result_accuracy"] == 0.5
    assert aggregate["false_positive_rate"] == 0.5
    assert aggregate["p95_latency_ms"] == 3_000.0


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


def test_evaluation_store_persists_revisions_runs_and_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [1_000.0]
    monkeypatch.setattr("server.rag.evaluation.time.time", lambda: clock[0])
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
    case_id = updated["cases"][0]["case_id"]
    assert store.claim_case_slot(run["run_id"], "version-a", case_id) is True
    clock[0] += 2.0

    reloaded = KnowledgeEvaluationStore(path)
    assert reloaded.get_set(evaluation_set["eval_set_id"])["cases"][0]["query"].startswith("Where")
    assert reloaded.recover_runs() == 1
    recovered = reloaded.get_run(run["run_id"])
    assert recovered["status"] == "queued"
    interrupted = recovered["case_results"]["version-a"][case_id]
    assert interrupted["status"] == "failed"
    assert interrupted["error"] == "Interrupted evaluation call was not retried."
    assert interrupted["latency_ms"] == 2_000.0
    assert recovered["inflight_slots"] == {}


def test_formal_execution_slots_are_deterministic_and_case_paired() -> None:
    run = {
        "run_mode": "formal",
        "execution_manifest": {"execution_seed": "a" * 64},
        "targets": [
            {"target_id": "baseline"},
            {"target_id": "candidate"},
        ],
        "eval_set_snapshot": {
            "cases": [
                {"case_id": "case-a"},
                {"case_id": "case-b"},
                {"case_id": "case-c"},
            ]
        },
    }
    first = _execution_slots(run)
    second = _execution_slots(run)

    assert first == second
    assert len(first) == 6
    for index in range(0, len(first), 2):
        assert first[index][1]["case_id"] == first[index + 1][1]["case_id"]
        assert {
            first[index][0]["target_id"], first[index + 1][0]["target_id"]
        } == {"baseline", "candidate"}


def test_paired_confidence_gate_rejects_uncertain_non_inferiority() -> None:
    cases = [
        {
            "case_id": f"case-{index}",
            "expected_no_result": index >= 30,
            "targeting": {"locale": "zh" if index % 2 == 0 else "en"},
        }
        for index in range(42)
    ]

    def result(case: dict, score: float) -> dict:
        return {
            "case_id": case["case_id"],
            "status": "completed",
            "expected_no_result": case["expected_no_result"],
            "metrics": (
                {"no_result_accuracy": score}
                if case["expected_no_result"]
                else {"recall_at_5": score}
            ),
        }

    baseline = [result(case, 1.0) for case in cases]
    candidate = [result(case, 0.0 if index == 0 else 1.0) for index, case in enumerate(cases)]
    first = paired_primary_confidence_report(
        baseline, candidate, cases=cases, seed="b" * 64
    )
    second = paired_primary_confidence_report(
        baseline, candidate, cases=cases, seed="b" * 64
    )
    assert first == second
    assert first["point_delta"] >= -0.03
    assert first["confidence_interval"]["lower"] < -0.03
    assert first["passed"] is False

    metrics = {
        "recall_at_5": 1.0,
        "mrr_at_10": 1.0,
        "citation_precision_at_5": 0.2,
        "citation_coverage": 1.0,
        "no_result_accuracy": 1.0,
        "positive_no_result_rate": 0.0,
        "p95_latency_ms": 10.0,
        "error_count": 0,
    }
    gate = evaluate_promotion_gate(
        metrics,
        baseline=metrics,
        evidence_qualification={"qualified": True, "status": "qualified"},
        paired_confidence=first,
        comparability={"comparable": True, "same_corpus": True},
        run_mode="formal",
    )
    assert gate["passed"] is False
    paired_check = next(
        check for check in gate["checks"] if check["id"] == "paired_non_inferiority"
    )
    assert paired_check["passed"] is False


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
    client, service, pipeline_executor, evaluation_executor, registry = evaluation_runtime
    kb_id = await _create_kb(client)
    baseline_doc = await _upload_text(
        client,
        kb_id,
        "baseline.txt",
        "The legacy handbook discusses office access badges.",
    )
    baseline_job = await _execute_draft(client, pipeline_executor, kb_id, [baseline_doc])
    baseline_version = str(baseline_job["candidate_version_id"])
    _mark_pipeline_version_as_previously_active(service, baseline_version)

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
    assert evidence["processor"]["mode"] == "general"
    assert len(evidence["processor"]["fingerprint"]) == 64
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
