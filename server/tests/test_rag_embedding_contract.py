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
from server.rag.pipeline_executor import KnowledgePipelineExecutor
from server.rag.rag_service import (
    PipelineDraftValidationError,
    PipelineJobStateError,
    RagRetrievalUnavailableError,
    RagService,
)
from server.rag.vector_store import (
    ChromaVectorStore,
    LocalJsonVectorStore,
    UnavailableVectorStore,
    VectorChunk,
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

    with pytest.raises(RuntimeError, match="query.*dimension contract"):
        local.query(namespace, [1.0, 0.0, 0.0], 1)

    records = local._read_records()
    records[0]["embedding"] = [1.0, 0.0, 0.0]
    local._write_records(records)
    with pytest.raises(RuntimeError, match="stored vector.*dimension contract"):
        local.query(namespace, [1.0, 0.0], 1)


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
async def test_unavailable_vector_backend_blocks_vector_but_not_fulltext_pipeline(
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
    draft = service.get_pipeline_draft(kb["id"])
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
    job = service.create_pipeline_job(
        kb["id"],
        draft_version=fulltext["version"],
        source_document_ids=[document["id"]],
    )
    assert await KnowledgePipelineExecutor(service).run_once() is True
    completed = service.get_pipeline_job(job["job_id"])
    assert completed["status"] == "succeeded"
    assert embedder.call_count == 0


@pytest.mark.asyncio
async def test_fulltext_pipeline_recovery_does_not_touch_unavailable_vector_backend(
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
    job = service.create_pipeline_job(
        kb["id"],
        draft_version=draft["version"],
        source_document_ids=[document["id"]],
    )
    service._update_pipeline_job(
        job["job_id"],
        lambda current: current.update({"status": "running"}),
    )

    assert service.recover_pipeline_jobs() == 1
    assert service.get_pipeline_job(job["job_id"])["status"] == "queued"


@pytest.mark.asyncio
async def test_fulltext_pipeline_cleanup_does_not_touch_unavailable_vector_backend(
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
    job = service.create_pipeline_job(
        kb["id"],
        draft_version=draft["version"],
        source_document_ids=[document["id"]],
    )
    service._update_pipeline_job(
        job["job_id"],
        lambda current: current.update(
            {
                "status": "cancelled",
                "deletion_invalidated": True,
            }
        ),
    )

    service.cleanup_invalidated_pipeline_job(job["job_id"])

    cleaned = service.get_pipeline_job(job["job_id"])
    assert cleaned["deletion_artifacts_purged"] is True
    assert cleaned["deletion_cleanup_error"] is None


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
    draft = service.get_pipeline_draft(kb["id"])
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
async def test_fulltext_pipeline_builds_without_embedding_or_vector_namespace(
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

    job = await _execute_draft(client, executor, kb_id, document_id)
    version_id = str(job["candidate_version_id"])
    version = service.get_pipeline_version(version_id)

    assert embedder.call_count == 0
    assert all(
        record.get("kb_id") != version["namespace"]
        for record in vector_store._read_records()
    )
    assert version["index_schema_version"] == 3
    assert version["vector_index_ready"] is False
    assert version["lexical_index_ready"] is True
    assert version["embedding_profile"]["effective"]["status"] == "not_applicable"
    assert version["index_contract"]["vector"]["required"] is False
    assert service.lexical_store.count_namespace(str(version["namespace"])) > 0

    query_response = await client.post(
        f"/api/rag/pipeline/versions/{version_id}/query",
        json={"question": "full-text-only pipeline"},
    )
    assert query_response.status_code == 200, query_response.text
    assert query_response.json()["sources"]
    assert embedder.call_count == 0

    service.activate_pipeline_version(version_id)
    active_query = await client.post(
        "/api/rag/query",
        json={"kb_id": kb_id, "question": "full-text-only pipeline"},
    )
    assert active_query.status_code == 200, active_query.text
    assert active_query.json()["sources"]
    citations = await client.post(
        "/api/rag/pipeline/citations",
        json={"kb_id": kb_id, "question": "full-text-only pipeline"},
    )
    assert citations.status_code == 200, citations.text
    assert citations.json()["citation_count"] > 0
    assert embedder.call_count == 0


@pytest.mark.asyncio
async def test_fulltext_missing_index_fails_closed_without_embedding(
    contract_runtime,
) -> None:
    client, service, executor, embedder, _ = contract_runtime
    kb_id, document_id = await _create_pipeline_source(client)
    embedder.call_count = 0
    draft_response = await client.patch(
        f"/api/rag/pipeline/draft/{kb_id}",
        json={"retrieval_profile": {"mode": "fulltext"}},
    )
    assert draft_response.status_code == 200, draft_response.text
    job = await _execute_draft(client, executor, kb_id, document_id)
    version_id = str(job["candidate_version_id"])
    version = service.get_pipeline_version(version_id)
    service.lexical_store.delete_namespace(str(version["namespace"]))

    response = await client.post(
        f"/api/rag/pipeline/versions/{version_id}/query",
        json={"question": "What must never be dispatched?"},
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


@pytest.mark.asyncio
async def test_model_endpoint_and_dimension_changes_get_distinct_namespaces(
    contract_runtime,
) -> None:
    client, service, executor, embedder, _ = contract_runtime
    kb_id, document_id = await _create_pipeline_source(client)
    embedder.call_count = 0

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

    draft = service.get_pipeline_draft(kb_id)
    with pytest.raises(PipelineDraftValidationError, match="read-only"):
        service.create_pipeline_job(
            kb_id,
            draft_version=draft["version"],
            source_document_ids=[document_id],
            base_version_id=legacy_id,
        )
