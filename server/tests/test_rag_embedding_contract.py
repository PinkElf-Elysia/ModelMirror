from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from server.main import app
from server.rag.api import (
    set_pipeline_executor_for_tests,
    set_rag_service_for_tests,
)
from server.rag.embedder import EmbeddingClient
from server.rag.lexical_store import LexicalChunk
from server.rag.pipeline_executor import KnowledgePipelineExecutor
from server.rag.rag_service import (
    PipelineContentContractError,
    PipelineDraftValidationError,
    PipelineJobStateError,
    RagRetrievalContractError,
    RagRetrievalUnavailableError,
    RagService,
)
from server.rag.vector_store import (
    ChromaVectorStore,
    LocalJsonVectorStore,
    UnavailableVectorStore,
    VectorChunk,
    VectorStoreContractError,
    VectorStoreUnavailableError,
    create_vector_store,
)


class CountingEmbeddingClient(EmbeddingClient):
    def __init__(self, *, dimension: int = 8) -> None:
        super().__init__(
            api_base="https://embedding.invalid/v1",
            api_key="test-key",
            model="test-embedding-model",
            dimension=dimension,
        )
        self.call_count = 0

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.call_count += 1
        return [
            [1.0, *([0.0] * (self.dimension - 1))]
            for _ in texts
        ]


@pytest_asyncio.fixture
async def contract_runtime(tmp_path: Path):
    embedder = CountingEmbeddingClient()
    vector_store = LocalJsonVectorStore(tmp_path / "storage" / "vectors.json")
    service = RagService(
        storage_dir=tmp_path / "storage",
        uploads_dir=tmp_path / "uploads",
        embedder=embedder,
        vector_store=vector_store,
        llm_enabled=False,
    )
    executor = KnowledgePipelineExecutor(service, poll_interval=0.01)
    set_rag_service_for_tests(service)
    set_pipeline_executor_for_tests(executor)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        yield client, service, executor, embedder, vector_store
    set_pipeline_executor_for_tests(None)
    set_rag_service_for_tests(None)


async def _create_pipeline_source(client: httpx.AsyncClient) -> tuple[str, str]:
    kb_response = await client.post(
        "/api/rag/knowledge_bases",
        json={"name": "P0 embedding contract"},
    )
    assert kb_response.status_code == 200, kb_response.text
    kb_id = str(kb_response.json()["id"])
    document_response = await client.post(
        f"/api/rag/knowledge_bases/{kb_id}/documents",
        files={
            "file": (
                "contract.txt",
                b"A full-text-only pipeline must never dispatch an embedding request.",
                "text/plain",
            )
        },
    )
    assert document_response.status_code == 200, document_response.text
    return kb_id, str(document_response.json()["id"])


async def _execute_draft(
    client: httpx.AsyncClient,
    executor: KnowledgePipelineExecutor,
    kb_id: str,
    document_id: str,
) -> dict:
    draft = (await client.get(f"/api/rag/pipeline/draft?kb_id={kb_id}")).json()
    created = await client.post(
        f"/api/rag/pipeline/draft/{kb_id}/execute",
        json={
            "draft_version": draft["version"],
            "source_document_ids": [document_id],
            "xpert_file_refs": [],
        },
    )
    assert created.status_code == 200, created.text
    assert await executor.run_once() is True
    job = await client.get(
        f"/api/rag/pipeline/jobs/{created.json()['job_id']}"
    )
    assert job.status_code == 200, job.text
    assert job.json()["status"] == "succeeded", job.text
    return job.json()


async def _configure_vector_draft(
    client: httpx.AsyncClient,
    kb_id: str,
) -> dict:
    response = await client.patch(
        f"/api/rag/pipeline/draft/{kb_id}",
        json={"retrieval_profile": {"mode": "vector"}},
    )
    assert response.status_code == 200, response.text
    draft = response.json()
    assert draft["retrieval_profile"]["mode"] == "vector"
    return draft


def test_chroma_initialization_failure_is_not_silently_local(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_VECTOR_STORE", "chroma")

    def fail_chroma(_: Path) -> object:
        raise RuntimeError("synthetic chroma initialization failure")

    monkeypatch.setattr("server.rag.vector_store.ChromaVectorStore", fail_chroma)
    store = create_vector_store(tmp_path)

    assert not isinstance(store, LocalJsonVectorStore)
    assert store.readiness()["ready"] is False
    assert store.readiness()["reason_code"] == "vector_backend_initialization_failed"


def test_explicit_local_backend_is_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_VECTOR_STORE", "local")
    service = RagService(
        storage_dir=tmp_path / "storage",
        uploads_dir=tmp_path / "uploads",
        embedder=CountingEmbeddingClient(),
        llm_enabled=False,
    )

    capabilities = service.retrieval_capabilities()
    assert capabilities["index_schema_version"] == 3
    assert capabilities["vector"]["configured_backend"] == "local"
    assert capabilities["vector"]["effective_backend"] == "local_json"
    assert capabilities["vector"]["ready"] is True
    assert capabilities["vector"]["distance_contract"] == "cosine_v1"


def test_v3_chroma_and_local_share_clamped_cosine_score_contract(tmp_path: Path) -> None:
    namespace = f"kb_contract::v3::kpv_contract::{'a' * 64}::dim-2::cosine_v1"

    class FakeCollection:
        metadata: dict = {}

        def count(self) -> int:
            return 1

        def query(self, **_kwargs: object) -> dict:
            return {
                "ids": [["chunk"]],
                "documents": [["contract text"]],
                "metadatas": [[{"doc_id": "doc", "document_name": "doc.txt"}]],
                "distances": [[0.25]],
            }

    chroma = object.__new__(ChromaVectorStore)
    collection = FakeCollection()
    chroma._namespace_collection = lambda *_args, **_kwargs: collection  # type: ignore[method-assign]
    assert chroma.query(namespace, [1.0, 0.0], 1)[0].score == pytest.approx(0.75)

    local = LocalJsonVectorStore(tmp_path / "vectors.json")
    local.add_chunks(
        [
            VectorChunk(
                id="negative",
                kb_id=namespace,
                doc_id="doc",
                document_name="doc.txt",
                text="opposite",
                embedding=[-1.0, 0.0],
                chunk_index=0,
            )
        ]
    )
    assert local.query(namespace, [1.0, 0.0], 1)[0].score == 0.0


@pytest.mark.parametrize(
    "stored_metadata, expected_message",
    [
        (
            {
                "modelmirror_namespace": "different-namespace",
                "modelmirror_schema_version": 3,
                "modelmirror_dimension": 2,
                "modelmirror_distance_contract": "cosine_v1",
            },
            "identity collision",
        ),
        (
            {
                "modelmirror_schema_version": 3,
                "modelmirror_dimension": 3,
                "modelmirror_distance_contract": "cosine_v1",
            },
            "contract mismatch",
        ),
        (
            {
                "hnsw:space": "l2",
                "modelmirror_schema_version": 3,
                "modelmirror_dimension": 2,
                "modelmirror_distance_contract": "cosine_v1",
            },
            "contract mismatch",
        ),
    ],
)
def test_v3_chroma_rejects_existing_collection_identity_conflicts(
    stored_metadata: dict,
    expected_message: str,
) -> None:
    namespace = f"kb_contract::v3::kpv_contract::{'a' * 64}::dim-2::cosine_v1"
    stored_metadata = {
        "modelmirror_namespace": namespace,
        "hnsw:space": "cosine",
        **stored_metadata,
    }

    class FakeCollection:
        metadata = stored_metadata

    class FakeClient:
        def get_or_create_collection(self, _name: str, *, metadata: dict) -> FakeCollection:
            assert metadata["hnsw:space"] == "cosine"
            return FakeCollection()

    chroma = object.__new__(ChromaVectorStore)
    chroma._client = FakeClient()
    with pytest.raises(RuntimeError, match=expected_message):
        chroma._namespace_collection(namespace, create=True)


def test_v3_chroma_rejects_persisted_contract_conflict_on_read() -> None:
    namespace = f"kb_contract::v3::kpv_contract::{'a' * 64}::dim-2::cosine_v1"

    class FakeCollection:
        metadata = {
            "modelmirror_namespace": namespace,
            "hnsw:space": "l2",
            "modelmirror_schema_version": 3,
            "modelmirror_dimension": 2,
            "modelmirror_distance_contract": "cosine_v1",
        }

        def count(self) -> int:
            return 1

    class FakeClient:
        def get_collection(self, _name: str) -> FakeCollection:
            return FakeCollection()

    chroma = object.__new__(ChromaVectorStore)
    chroma._client = FakeClient()
    with pytest.raises(RuntimeError, match="contract mismatch"):
        chroma.count_namespace(namespace)


def test_v3_chroma_refuses_delete_when_persisted_identity_conflicts() -> None:
    namespace = f"kb_contract::v3::kpv_contract::{'a' * 64}::dim-2::cosine_v1"

    class FakeCollection:
        metadata = {
            "modelmirror_namespace": "different-namespace",
            "hnsw:space": "cosine",
            "modelmirror_schema_version": 3,
            "modelmirror_dimension": 2,
            "modelmirror_distance_contract": "cosine_v1",
        }

        def delete(self, **_kwargs: object) -> None:
            return None

    class FakeClient:
        deleted: list[str] = []

        def get_collection(self, _name: str) -> FakeCollection:
            return FakeCollection()

        def delete_collection(self, name: str) -> None:
            self.deleted.append(name)

    chroma = object.__new__(ChromaVectorStore)
    chroma._client = FakeClient()
    chroma._collection = FakeCollection()
    with pytest.raises(RuntimeError, match="identity collision"):
        chroma.delete_knowledge_base(namespace)
    assert chroma._client.deleted == []


def test_v3_local_store_rejects_query_and_persisted_dimension_mismatch(
    tmp_path: Path,
) -> None:
    namespace = f"kb_contract::v3::kpv_contract::{'a' * 64}::dim-2::cosine_v1"
    local = LocalJsonVectorStore(tmp_path / "vectors.json")
    local.add_chunks(
        [
            VectorChunk(
                id="chunk",
                kb_id=namespace,
                doc_id="doc",
                document_name="doc.txt",
                text="contract text",
                embedding=[1.0, 0.0],
                chunk_index=0,
            )
        ]
    )

    with pytest.raises(VectorStoreContractError, match="query.*dimension contract"):
        local.query(namespace, [1.0, 0.0, 0.0], 1)

    records = local._read_records()
    records[0]["embedding"] = [1.0, 0.0, 0.0]
    local._write_records(records)
    with pytest.raises(VectorStoreContractError, match="stored vector.*dimension contract"):
        local.query(namespace, [1.0, 0.0], 1)


def test_vector_unavailable_and_contract_errors_remain_distinct() -> None:
    unavailable = UnavailableVectorStore("chroma", "backend_down")
    with pytest.raises(VectorStoreUnavailableError, match="backend_down"):
        unavailable.query("kb", [1.0], 1)

    service = object.__new__(RagService)
    service._vector_backend_readiness = lambda: {
        "ready": True,
        "distance_contract": "dot_product_v1",
    }
    with pytest.raises(RagRetrievalContractError) as mismatch:
        service._ensure_vector_backend_ready()
    assert mismatch.value.code == "rag_vector_distance_contract_mismatch"


def test_real_chroma_v3_collection_persists_cosine_contract(tmp_path: Path) -> None:
    namespace = f"kb_contract::v3::kpv_contract::{'b' * 64}::dim-2::cosine_v1"
    store = ChromaVectorStore(tmp_path / "chroma")
    store.add_chunks(
        [
            VectorChunk(
                id="chunk",
                kb_id=namespace,
                doc_id="doc",
                document_name="doc.txt",
                text="contract text",
                embedding=[1.0, 0.0],
                chunk_index=0,
            )
        ]
    )

    collection = store._namespace_collection(namespace, create=False)
    assert collection is not None
    assert collection.metadata["hnsw:space"] == "cosine"
    assert collection.metadata["modelmirror_dimension"] == 2
    assert collection.metadata["modelmirror_distance_contract"] == "cosine_v1"
    assert store.count_namespace(namespace) == 1
    assert store.query(namespace, [1.0, 0.0], 1)[0].score == pytest.approx(1.0)

    reopened = ChromaVectorStore(tmp_path / "chroma")
    assert reopened.count_namespace(namespace) == 1
    assert reopened.query(namespace, [1.0, 0.0], 1)[0].chunk_id == "chunk"
    reopened.delete_knowledge_base(namespace)
    assert reopened.count_namespace(namespace) == 0


@pytest.mark.asyncio
async def test_unavailable_vector_backend_and_legacy_fulltext_fail_closed(
    tmp_path: Path,
) -> None:
    embedder = CountingEmbeddingClient()
    service = RagService(
        storage_dir=tmp_path / "storage",
        uploads_dir=tmp_path / "uploads",
        embedder=embedder,
        vector_store=UnavailableVectorStore(
            "chroma",
            "vector_backend_initialization_failed",
        ),
        llm_enabled=False,
    )
    kb = service.create_knowledge_base("backend readiness")
    document = await service.upload_document(
        kb["id"],
        "source.txt",
        b"A fulltext pipeline remains independent of the vector backend.",
        pipeline_only=True,
    )
    draft = service.update_pipeline_draft(
        kb["id"],
        {},
        retrieval_profile={"mode": "vector"},
    )
    with pytest.raises(RagRetrievalUnavailableError) as blocked:
        service.create_pipeline_job(
            kb["id"],
            draft_version=draft["version"],
            source_document_ids=[document["id"]],
        )
    assert blocked.value.code == "rag_vector_backend_unavailable"

    fulltext = service.update_pipeline_draft(
        kb["id"],
        {},
        retrieval_profile={"mode": "fulltext"},
    )
    with pytest.raises(PipelineContentContractError) as legacy_blocked:
        service.create_pipeline_job(
            kb["id"],
            draft_version=fulltext["version"],
            source_document_ids=[document["id"]],
        )
    assert legacy_blocked.value.code == "rag_content_contract_legacy_read_only"
    assert embedder.call_count == 0
    assert service.list_pipeline_jobs(kb_id=kb["id"]) == []


@pytest.mark.asyncio
async def test_fulltext_pipeline_recovery_is_not_created_before_lexical_v2(
    tmp_path: Path,
) -> None:
    service = RagService(
        storage_dir=tmp_path / "storage",
        uploads_dir=tmp_path / "uploads",
        embedder=CountingEmbeddingClient(),
        vector_store=UnavailableVectorStore(
            "chroma",
            "vector_backend_initialization_failed",
        ),
        llm_enabled=False,
    )
    kb = service.create_knowledge_base("fulltext recovery")
    document = await service.upload_document(
        kb["id"],
        "recovery.txt",
        b"Fulltext recovery must remain independent of vector readiness.",
        pipeline_only=True,
    )
    draft = service.update_pipeline_draft(
        kb["id"],
        {},
        retrieval_profile={"mode": "fulltext"},
    )
    with pytest.raises(PipelineContentContractError) as blocked:
        service.create_pipeline_job(
            kb["id"],
            draft_version=draft["version"],
            source_document_ids=[document["id"]],
        )

    assert blocked.value.code == "rag_content_contract_legacy_read_only"
    assert service.recover_pipeline_jobs() == 0
    assert service.embedder.call_count == 0


@pytest.mark.asyncio
async def test_fulltext_pipeline_cleanup_has_no_partial_job_before_lexical_v2(
    tmp_path: Path,
) -> None:
    service = RagService(
        storage_dir=tmp_path / "storage",
        uploads_dir=tmp_path / "uploads",
        embedder=CountingEmbeddingClient(),
        vector_store=UnavailableVectorStore(
            "chroma",
            "vector_backend_initialization_failed",
        ),
        llm_enabled=False,
    )
    kb = service.create_knowledge_base("fulltext cleanup")
    document = await service.upload_document(
        kb["id"],
        "cleanup.txt",
        b"Fulltext cleanup must remain independent of vector readiness.",
        pipeline_only=True,
    )
    draft = service.update_pipeline_draft(
        kb["id"],
        {},
        retrieval_profile={"mode": "fulltext"},
    )
    with pytest.raises(PipelineContentContractError) as blocked:
        service.create_pipeline_job(
            kb["id"],
            draft_version=draft["version"],
            source_document_ids=[document["id"]],
        )

    assert blocked.value.code == "rag_content_contract_legacy_read_only"
    assert service.list_pipeline_jobs(kb_id=kb["id"]) == []
    assert service.embedder.call_count == 0


@pytest.mark.asyncio
async def test_vector_pipeline_does_not_publish_when_backend_drops_vectors(
    tmp_path: Path,
) -> None:
    class DroppingVectorStore(LocalJsonVectorStore):
        def add_chunks(self, chunks: list[VectorChunk]) -> None:
            return None

    embedder = CountingEmbeddingClient()
    vector_store = DroppingVectorStore(tmp_path / "storage" / "vectors.json")
    service = RagService(
        storage_dir=tmp_path / "storage",
        uploads_dir=tmp_path / "uploads",
        embedder=embedder,
        vector_store=vector_store,
        llm_enabled=False,
    )
    kb = service.create_knowledge_base("vector write verification")
    document = await service.upload_document(
        kb["id"],
        "source.txt",
        b"A ready vector version must prove that every chunk was stored.",
        pipeline_only=True,
    )
    draft = service.update_pipeline_draft(
        kb["id"],
        {},
        retrieval_profile={"mode": "vector"},
    )
    job = service.create_pipeline_job(
        kb["id"],
        draft_version=draft["version"],
        source_document_ids=[document["id"]],
    )

    assert await KnowledgePipelineExecutor(service).run_once() is True
    completed = service.get_pipeline_job(job["job_id"])
    assert completed["status"] == "failed"
    assert "Vector index count" in str(completed["error"])
    assert service.list_pipeline_versions(kb["id"]) == []


@pytest.mark.asyncio
async def test_fulltext_pipeline_build_is_blocked_before_embedding_or_vector_write(
    contract_runtime,
) -> None:
    client, service, executor, embedder, vector_store = contract_runtime
    kb_id, document_id = await _create_pipeline_source(client)
    embedder.call_count = 0
    draft_response = await client.patch(
        f"/api/rag/pipeline/draft/{kb_id}",
        json={"retrieval_profile": {"mode": "fulltext"}},
    )
    assert draft_response.status_code == 200, draft_response.text
    draft = draft_response.json()
    assert draft["index_schema_version"] == 3
    assert draft["embedding_profile"]["effective"]["status"] == "not_applicable"
    assert draft["index_contract"]["vector"]["required"] is False

    created = await client.post(
        f"/api/rag/pipeline/draft/{kb_id}/execute",
        json={
            "draft_version": draft["version"],
            "source_document_ids": [document_id],
            "xpert_file_refs": [],
        },
    )
    assert created.status_code == 409, created.text
    assert created.json()["detail"]["code"] == "rag_content_contract_legacy_read_only"
    assert embedder.call_count == 0
    assert vector_store._read_records() == []
    assert service.list_pipeline_jobs(kb_id=kb_id) == []
    assert service.list_pipeline_versions(kb_id) == []


@pytest.mark.asyncio
async def test_active_v2_fulltext_remains_readable_and_missing_index_fails_closed(
    contract_runtime,
) -> None:
    client, service, _executor, embedder, _ = contract_runtime
    kb_id, document_id = await _create_pipeline_source(client)
    embedder.call_count = 0
    version_id = "kpv_legacy_fulltext_active"
    namespace = kb_id
    profile = service._default_embedding_profile()  # noqa: SLF001
    version = {
        "version_id": version_id,
        "kb_id": kb_id,
        "version": 1,
        "status": "active",
        "namespace": namespace,
        "draft_id": f"draft_{kb_id}",
        "draft_version": 1,
        "index_schema_version": 2,
        "embedding_profile": profile,
        "embedding_space_fingerprint": profile["embedding_space_fingerprint"],
        "retrieval_profile": {
            "mode": "fulltext",
            "top_k": 2,
            "score_threshold": 0.0,
            "rerank_enabled": False,
            "rerank_provider": "none",
        },
        "vector_index_ready": False,
        "lexical_index_ready": True,
        "source_summary": [
            {
                "source_id": document_id,
                "document_id": document_id,
                "filename": "contract.txt",
            }
        ],
        "document_count": 1,
        "chunk_count": 1,
        "job_id": "legacy-fulltext-job",
        "created_at": 1.0,
        "activated_at": 1.0,
    }
    with service._metadata_lock:  # noqa: SLF001 - historical V2 fixture.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        metadata["pipeline_versions"][version_id] = version
        metadata["pipeline_active_versions"][kb_id] = version_id
        service._write_metadata_unlocked(metadata)  # noqa: SLF001
    service.lexical_store.add_chunks(
        [
            LexicalChunk(
                chunk_id=f"{version_id}_{document_id}_chunk_0",
                namespace=namespace,
                doc_id=f"{version_id}_{document_id}",
                document_name="contract.txt",
                text=(
                    "A full-text-only pipeline must never dispatch an "
                    "embedding request."
                ),
                chunk_index=0,
                source_block_id="legacy-source-block",
            )
        ]
    )

    readable = await service.query_pipeline_version(
        version_id,
        "full-text-only embedding request",
        generate_answer=False,
    )
    assert readable["sources"]
    assert embedder.call_count == 0

    for requested_mode in ("vector", "hybrid"):
        with pytest.raises(RagRetrievalUnavailableError) as unavailable:
            await service.query_pipeline_version(
                version_id,
                "full-text-only embedding request",
                retrieval={"mode": requested_mode},
                generate_answer=False,
            )
        assert unavailable.value.code == "rag_retrieval_mode_unavailable"
        assert embedder.call_count == 0

    version_override = await client.post(
        f"/api/rag/pipeline/versions/{version_id}/query",
        json={
            "question": "full-text-only embedding request",
            "retrieval": {"mode": "vector"},
        },
    )
    assert version_override.status_code == 409, version_override.text
    assert version_override.json()["detail"]["code"] == (
        "rag_retrieval_mode_unavailable"
    )
    active_override = await client.post(
        "/api/rag/query",
        json={
            "kb_id": kb_id,
            "question": "full-text-only embedding request",
            "retrieval": {"mode": "hybrid"},
        },
    )
    assert active_override.status_code == 409, active_override.text
    assert active_override.json()["detail"]["code"] == (
        "rag_retrieval_mode_unavailable"
    )
    assert embedder.call_count == 0

    with service._metadata_lock:  # noqa: SLF001 - stale readiness compatibility fixture.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        metadata["pipeline_versions"][version_id]["lexical_index_ready"] = False
        service._write_metadata_unlocked(metadata)  # noqa: SLF001
    empty = await service.query_pipeline_version(
        version_id,
        "qzv987654nomatch",
        generate_answer=False,
    )
    assert empty["sources"] == []
    assert embedder.call_count == 0

    service.lexical_store.delete_namespace(namespace)
    response = await client.post(
        f"/api/rag/pipeline/versions/{version_id}/query",
        json={"question": "full-text-only embedding request"},
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "rag_fulltext_index_unavailable"
    assert embedder.call_count == 0


@pytest.mark.asyncio
async def test_v3_vector_namespace_binds_embedding_space_and_distance(
    contract_runtime,
) -> None:
    client, service, executor, embedder, _ = contract_runtime
    kb_id, document_id = await _create_pipeline_source(client)
    embedder.call_count = 0
    await _configure_vector_draft(client, kb_id)
    job = await _execute_draft(client, executor, kb_id, document_id)
    version = service.get_pipeline_version(str(job["candidate_version_id"]))
    effective = version["embedding_profile"]["effective"]

    assert embedder.call_count == 1
    assert version["index_schema_version"] == 3
    assert version["index_contract"]["vector"] == {
        "required": True,
        "embedding_space_fingerprint": version["embedding_space_fingerprint"],
        "dimension": effective["dimension"],
        "distance_contract": "cosine_v1",
    }
    namespace = str(version["namespace"])
    assert "::v3::" in namespace
    assert version["embedding_space_fingerprint"] in namespace
    assert f"::dim-{effective['dimension']}::" in namespace
    assert namespace.endswith("::cosine_v1")

    before_override = embedder.call_count
    with pytest.raises(RagRetrievalUnavailableError) as unavailable:
        await service.query_pipeline_version(
            str(job["candidate_version_id"]),
            "contract identity",
            retrieval={"mode": "fulltext"},
            generate_answer=False,
        )
    assert unavailable.value.code == "rag_retrieval_mode_unavailable"
    assert embedder.call_count == before_override


@pytest.mark.asyncio
async def test_model_endpoint_and_dimension_changes_get_distinct_namespaces(
    contract_runtime,
) -> None:
    client, service, executor, embedder, _ = contract_runtime
    kb_id, document_id = await _create_pipeline_source(client)
    embedder.call_count = 0

    await _configure_vector_draft(client, kb_id)
    first_job = await _execute_draft(client, executor, kb_id, document_id)
    first = service.get_pipeline_version(str(first_job["candidate_version_id"]))

    embedder.model = "test-embedding-model-v2"
    model_draft = await client.patch(
        f"/api/rag/pipeline/draft/{kb_id}",
        json={
            "embedding_profile": {
                "provider": "openai_compatible",
                "model": "test-embedding-model-v2",
            }
        },
    )
    assert model_draft.status_code == 200, model_draft.text
    second_job = await _execute_draft(client, executor, kb_id, document_id)
    second = service.get_pipeline_version(str(second_job["candidate_version_id"]))

    embedder.api_base = "https://other-embedding.invalid/v1"
    endpoint_draft = await client.patch(
        f"/api/rag/pipeline/draft/{kb_id}",
        json={},
    )
    assert endpoint_draft.status_code == 200, endpoint_draft.text
    third_job = await _execute_draft(client, executor, kb_id, document_id)
    third = service.get_pipeline_version(str(third_job["candidate_version_id"]))

    embedder.dimension = 16
    dimension_draft = await client.patch(
        f"/api/rag/pipeline/draft/{kb_id}",
        json={},
    )
    assert dimension_draft.status_code == 200, dimension_draft.text
    fourth_job = await _execute_draft(client, executor, kb_id, document_id)
    fourth = service.get_pipeline_version(str(fourth_job["candidate_version_id"]))

    assert len(
        {first["namespace"], second["namespace"], third["namespace"], fourth["namespace"]}
    ) == 4
    assert len(
        {
            first["embedding_space_fingerprint"],
            second["embedding_space_fingerprint"],
            third["embedding_space_fingerprint"],
            fourth["embedding_space_fingerprint"],
        }
    ) == 4
    assert {
        first["embedding_profile"]["effective"]["dimension"],
        second["embedding_profile"]["effective"]["dimension"],
        third["embedding_profile"]["effective"]["dimension"],
        fourth["embedding_profile"]["effective"]["dimension"],
    } == {8, 16}

    conflicting = dict(third["index_contract"])
    conflicting["vector"] = dict(conflicting["vector"])
    conflicting["vector"]["distance_contract"] = "dot_product_v1"
    with pytest.raises(PipelineDraftValidationError):
        service._candidate_namespace(kb_id, "kpv_conflict", conflicting)


@pytest.mark.asyncio
async def test_active_v2_remains_readable_but_cannot_be_rebuilt_or_newly_activated(
    contract_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, service, _executor, _embedder, _ = contract_runtime
    kb_id, document_id = await _create_pipeline_source(client)
    profile = service._default_embedding_profile()
    legacy_id = "kpv_legacy_active"
    blocked_id = "kpv_legacy_blocked"
    base = {
        "version_id": legacy_id,
        "kb_id": kb_id,
        "version": 1,
        "status": "active",
        "namespace": kb_id,
        "draft_id": f"draft_{kb_id}",
        "draft_version": 1,
        "index_schema_version": 2,
        "embedding_profile": profile,
        "embedding_space_fingerprint": profile["embedding_space_fingerprint"],
        "retrieval_profile": {"mode": "vector", "top_k": 2},
        "vector_index_ready": True,
        "lexical_index_ready": False,
        "source_summary": [],
        "document_count": 1,
        "chunk_count": 1,
        "job_id": "legacy-job",
        "created_at": 1.0,
        "activated_at": 1.0,
    }
    with service._metadata_lock:
        metadata = service._read_metadata_unlocked()
        metadata["pipeline_versions"][legacy_id] = dict(base)
        metadata["pipeline_versions"][blocked_id] = {
            **base,
            "version_id": blocked_id,
            "version": 2,
            "status": "ready",
            "activated_at": None,
        }
        metadata["pipeline_active_versions"][kb_id] = legacy_id
        service._write_metadata_unlocked(metadata)

    service.vector_store.add_chunks(
        [
            VectorChunk(
                id=f"{legacy_id}_chunk_0",
                kb_id=kb_id,
                doc_id=f"{legacy_id}_{document_id}",
                document_name="contract.txt",
                text="A full-text-only pipeline must never dispatch an embedding request.",
                embedding=[1.0, *([0.0] * (service.embedder.dimension - 1))],
                chunk_index=0,
            )
        ]
    )

    result = await service.query_pipeline_version(
        legacy_id,
        "full-text-only pipeline",
        generate_answer=False,
    )
    assert result["sources"]
    assert service.activate_pipeline_version(legacy_id)["active"] is True
    with pytest.raises(PipelineJobStateError, match="Legacy V2"):
        service.activate_pipeline_version(blocked_id)

    class AllowingEvaluationStore:
        def assert_promotion_allowed(self, **_kwargs: object) -> None:
            return None

    monkeypatch.setattr(
        "server.rag.api.get_evaluation_store",
        lambda: AllowingEvaluationStore(),
    )
    activation = await client.post(
        f"/api/rag/pipeline/versions/{legacy_id}/activate",
    )
    assert activation.status_code == 200, activation.text
    promotion = await client.post(
        f"/api/rag/pipeline/versions/{legacy_id}/promote",
        json={"evaluation_run_id": "eval_synthetic_pass"},
    )
    assert promotion.status_code == 409, promotion.text
    assert "Legacy V2" in str(promotion.json()["detail"])

    draft = service.update_pipeline_draft(
        kb_id,
        {},
        retrieval_profile={"mode": "vector"},
    )
    with pytest.raises(PipelineDraftValidationError, match="read-only"):
        service.create_pipeline_job(
            kb_id,
            draft_version=draft["version"],
            source_document_ids=[document_id],
            base_version_id=legacy_id,
        )
