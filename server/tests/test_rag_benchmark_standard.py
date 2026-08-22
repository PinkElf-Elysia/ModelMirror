from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from server.main import app
from server.benchmarks.catalog import BenchmarkCatalog
from server.benchmarks.knowledge_executor import KnowledgeBenchmarkProvisioner
from server.benchmarks.store import BenchmarkJobStore
from server.rag.embedder import EmbeddingClient
from server.rag.evaluation import KnowledgeEvaluationStore
from server.rag.pipeline_executor import KnowledgePipelineExecutor
from server.rag.api import set_rag_service_for_tests
from server.rag.rag_service import KnowledgeBaseLockedError, RagService
from server.rag.vector_store import LocalJsonVectorStore


@pytest.mark.asyncio
async def test_managed_rag_benchmark_builds_real_indexes_and_immutable_gold(
    tmp_path: Path,
) -> None:
    service = RagService(
        storage_dir=tmp_path / "rag-storage",
        uploads_dir=tmp_path / "rag-uploads",
        embedder=EmbeddingClient(api_key="", dimension=64),
        vector_store=LocalJsonVectorStore(tmp_path / "rag-storage" / "vectors.json"),
        llm_enabled=False,
    )
    pipeline_executor = KnowledgePipelineExecutor(service, poll_interval=0.01)
    evaluation_store = KnowledgeEvaluationStore(
        tmp_path / "rag-storage" / "evaluations.json"
    )
    job_store = BenchmarkJobStore(tmp_path / "benchmark-storage")
    catalog = BenchmarkCatalog()
    provisioner = KnowledgeBenchmarkProvisioner(
        catalog=catalog,
        store=job_store,
        rag_service=service,
        pipeline_executor=pipeline_executor,
        evaluation_store=evaluation_store,
        poll_seconds=0.01,
    )
    created = job_store.create_job(
        kind="knowledge_instantiation",
        request={
            "pack_id": "modelmirror-rag-foundation-bilingual-v1",
            "name": "Managed RAG Benchmark",
        },
    )
    claimed = job_store.claim_next_job()
    assert claimed is not None

    pipeline_executor.start()
    try:
        await provisioner.run(claimed)
    finally:
        await pipeline_executor.stop()

    completed = job_store.require_job(created["job_id"])
    assert completed["status"] == "completed"
    state = completed["provisioning"]
    assert state["phase"] == "completed"
    assert state["uploaded_document_count"] == 12
    assert state["resolved_case_count"] == 40
    assert state["version_evidence"]["version_id"] == state["version_id"]
    assert state["version_evidence"]["version_fingerprint"]
    assert state["version_evidence"]["embedding"]["effective"]["model"] == (
        "deterministic-hash-v1"
    )

    kb_id = str(state["kb_id"])
    knowledge_base = next(item for item in service.list_knowledge_bases() if item["id"] == kb_id)
    assert knowledge_base["origin"] == "benchmark_catalog"
    assert knowledge_base["corpus_locked"] is True
    assert knowledge_base["provisioning_status"] == "ready"
    assert len(service.list_documents(kb_id)) == 12
    active = service.get_active_pipeline_version(kb_id)
    assert active is not None
    assert active["version_id"] == state["version_id"]
    assert active["vector_index_ready"] is True
    assert active["lexical_index_ready"] is True
    assert active["embedding_profile"]["provider"] == "hash"
    assert active["retrieval_profile"]["mode"] == "fulltext"
    assert active["processor_profile"]["mode"] == "general"

    evaluation_set = evaluation_store.get_set(state["eval_set_id"])
    published = evaluation_store.get_set_version(
        state["eval_set_id"], state["eval_set_version"]
    )
    assert len(evaluation_set["cases"]) == 40
    assert len(published["cases"]) == 40
    assert sum(bool(case["expected_no_result"]) for case in published["cases"]) == 6
    positive = [case for case in published["cases"] if not case["expected_no_result"]]
    assert all(case["expected_refs"] for case in positive)
    assert all(
        reference["match_mode"] == "source_block"
        and reference["document_id"]
        and reference["chunk_id"]
        and reference["source_block_id"]
        for case in positive
        for reference in case["expected_refs"]
    )

    gate = evaluation_store.get_gate_policy(kb_id)
    assert gate["mode"] == "advisory"
    assert gate["min_recall_at_5"] == 0.70
    assert gate["min_citation_coverage"] == 0.70
    assert gate["min_no_result_accuracy"] == 0.80

    with pytest.raises(KnowledgeBaseLockedError):
        await service.upload_document(
            kb_id,
            "forbidden.md",
            b"locked corpus",
            declared_media_type="text/markdown",
        )

    set_rag_service_for_tests(service)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            blocked_upload = await client.post(
                f"/api/rag/knowledge_bases/{kb_id}/documents",
                files={"file": ("forbidden.md", b"locked", "text/markdown")},
            )
            assert blocked_upload.status_code == 409
            assert blocked_upload.json()["detail"]["code"] == "rag_benchmark_corpus_locked"

            document_id = service.list_documents(kb_id)[0]["id"]
            blocked_delete = await client.delete(f"/api/rag/documents/{document_id}")
            assert blocked_delete.status_code == 409
            assert blocked_delete.json()["detail"]["code"] == "rag_benchmark_corpus_locked"
    finally:
        set_rag_service_for_tests(None)


@pytest.mark.asyncio
async def test_pipeline_only_upload_skips_legacy_index(tmp_path: Path) -> None:
    service = RagService(
        storage_dir=tmp_path / "rag-storage",
        uploads_dir=tmp_path / "rag-uploads",
        embedder=EmbeddingClient(api_key="", dimension=32),
        vector_store=LocalJsonVectorStore(tmp_path / "rag-storage" / "vectors.json"),
        llm_enabled=False,
    )
    kb = service.create_knowledge_base("pipeline-only")
    document = await service.upload_document(
        kb["id"],
        "source.md",
        b"# Source\n\nDeterministic content.",
        declared_media_type="text/markdown",
        pipeline_only=True,
    )

    assert document["ingestion_status"] == "pipeline_required"
    assert document["chunk_count"] == 0
    assert service.vector_store.list_document_chunks(document["id"]) == []

    api_kb = service.create_knowledge_base("pipeline-only-api")
    set_rag_service_for_tests(service)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/api/rag/knowledge_bases/{api_kb['id']}/documents?pipeline_only=true",
                files={
                    "file": (
                        "source.md",
                        b"# Source\n\nDeterministic API content.",
                        "text/markdown",
                    )
                },
            )
    finally:
        set_rag_service_for_tests(None)

    assert response.status_code == 200
    assert response.json()["ingestion_status"] == "pipeline_required"
    assert response.json()["chunk_count"] == 0
    assert service.vector_store.list_document_chunks(response.json()["id"]) == []
