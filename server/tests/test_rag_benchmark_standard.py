from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from server.main import app
from server.benchmarks.catalog import BenchmarkCatalog
from server.benchmarks.knowledge_executor import (
    KnowledgeBenchmarkInstantiationError,
    KnowledgeBenchmarkProvisioner,
)
from server.benchmarks.store import BenchmarkJobStore
from server.rag.embedder import EmbeddingClient
from server.rag.evaluation import KnowledgeEvaluationStore
from server.rag.pipeline_executor import KnowledgePipelineExecutor
from server.rag.api import set_rag_service_for_tests
from server.rag.rag_service import KnowledgeBaseLockedError, RagService
from server.rag.vector_store import LocalJsonVectorStore


@pytest.mark.asyncio
async def test_managed_rag_benchmark_fails_before_writes_until_content_contract_is_current(
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
    provisioner = KnowledgeBenchmarkProvisioner(
        catalog=BenchmarkCatalog(),
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

    reason_code = KnowledgeBenchmarkProvisioner.CONTENT_CONTRACT_BLOCKING_REASON
    with pytest.raises(KnowledgeBenchmarkInstantiationError, match=reason_code):
        await provisioner.run(claimed)

    blocked = job_store.require_job(created["job_id"])
    assert blocked["status"] == "generating"
    assert blocked["provisioning"]["phase"] == "blocked"
    assert blocked["provisioning"]["blocking_reason_code"] == reason_code
    blocked_contract = blocked["provisioning"]["content_index_contract"]
    assert blocked_contract["status"] != "current"
    assert set(blocked_contract["components"]) == {"chunker", "lexical", "parser"}
    assert all(
        status != "current" for status in blocked_contract["components"].values()
    )

    # Admission runs before KB creation, document import, candidate creation or
    # evaluation publication. A blocked attempt is therefore safe to retry in 4C.
    assert service.list_knowledge_bases(include_provisioning=True) == []
    assert service.list_pipeline_jobs() == []
    metadata = service._read_metadata()
    assert metadata["documents"] == {}
    assert metadata["pipeline_versions"] == {}
    assert metadata["pipeline_active_versions"] == {}


@pytest.mark.asyncio
async def test_existing_managed_benchmark_corpus_remains_write_locked(
    tmp_path: Path,
) -> None:
    service = RagService(
        storage_dir=tmp_path / "rag-storage",
        uploads_dir=tmp_path / "rag-uploads",
        embedder=EmbeddingClient(api_key="", dimension=32),
        vector_store=LocalJsonVectorStore(tmp_path / "rag-storage" / "vectors.json"),
        llm_enabled=False,
    )
    kb = service.create_knowledge_base(
        "Existing managed benchmark",
        origin="benchmark_catalog",
        corpus_locked=True,
        provisioning_status="ready",
    )
    document = await service.upload_document(
        kb["id"],
        "fixture.md",
        b"# Locked\n\nExisting benchmark evidence.",
        declared_media_type="text/markdown",
        allow_locked=True,
        pipeline_only=True,
    )

    with pytest.raises(KnowledgeBaseLockedError):
        await service.upload_document(
            kb["id"],
            "forbidden.md",
            b"locked corpus",
            declared_media_type="text/markdown",
        )

    set_rag_service_for_tests(service)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            blocked_upload = await client.post(
                f"/api/rag/knowledge_bases/{kb['id']}/documents",
                files={"file": ("forbidden.md", b"locked", "text/markdown")},
            )
            assert blocked_upload.status_code == 409
            assert blocked_upload.json()["detail"]["code"] == "rag_benchmark_corpus_locked"

            blocked_delete = await client.delete(
                f"/api/rag/documents/{document['id']}"
            )
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
