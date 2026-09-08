from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.rag.embedder import EmbeddingClient
from server.rag.pipeline_executor import KnowledgePipelineExecutor
from server.rag.rag_service import RagService
from server.rag.vector_store import LocalJsonVectorStore


class DimensionDiscoveringEmbeddingClient(EmbeddingClient):
    """Strict local fake whose returned vectors reveal a different dimension."""

    def __init__(self) -> None:
        super().__init__(
            api_base="https://embedding.invalid/v1",
            api_key="test-key",
            model="test-embedding-model",
            dimension=8,
        )
        self.call_count = 0

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.call_count += 1
        return [[1.0, *([0.0] * 11)] for _text in texts]


@pytest.mark.asyncio
async def test_sealed_4a_vector_job_replays_without_lexical_identity_upgrade(
    tmp_path: Path,
) -> None:
    embedder = DimensionDiscoveringEmbeddingClient()
    vector_store = LocalJsonVectorStore(tmp_path / "storage" / "vectors.json")
    service = RagService(
        storage_dir=tmp_path / "storage",
        uploads_dir=tmp_path / "uploads",
        embedder=embedder,
        vector_store=vector_store,
        llm_enabled=False,
    )
    kb = service.create_knowledge_base("sealed 4A vector replay")
    document = await service.upload_document(
        kb["id"],
        "legacy-vector.txt",
        b"A sealed 4A vector-only job must retain its historical lexical identity.",
        pipeline_only=True,
    )
    draft = service.update_pipeline_draft(
        kb["id"],
        {},
        retrieval_profile={"mode": "vector"},
    )
    created = service.create_pipeline_job(
        kb["id"],
        draft_version=draft["version"],
        source_document_ids=[document["id"]],
    )

    # Model an authentic sealed 4A job before any worker claims it. The old
    # snapshot had no lexical_profile, and its seal covered that exact identity.
    with service._metadata_lock:  # noqa: SLF001 - historical compatibility fixture.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        job = metadata["pipeline_jobs"][created["job_id"]]
        snapshot = job["config_snapshot"]
        snapshot.pop("lexical_profile", None)
        job.pop("lexical_profile", None)
        legacy_index_contract = service._index_contract(  # noqa: SLF001
            index_schema_version=int(snapshot["index_schema_version"]),
            retrieval_profile=dict(snapshot["retrieval_profile"]),
            embedding_profile=dict(snapshot["embedding_profile"]),
            lexical_profile=None,
        )
        snapshot["index_contract"] = json.loads(json.dumps(legacy_index_contract))
        job["index_contract"] = json.loads(json.dumps(legacy_index_contract))
        legacy_content_contract = service._content_index_contract(  # noqa: SLF001
            snapshot["stages"],
            snapshot.get("content_index_contract"),
            index_contract=legacy_index_contract,
        )
        snapshot["content_index_contract"] = json.loads(
            json.dumps(legacy_content_contract)
        )
        job["content_index_contract"] = json.loads(
            json.dumps(legacy_content_contract)
        )
        job["config_snapshot_fingerprint"] = service._mapping_sha256(snapshot)  # noqa: SLF001
        sealed_namespace = str(job["candidate_namespace"])
        assert "lexical_profile" not in snapshot
        assert legacy_index_contract["lexical"] == {
            "required": False,
            "backend": "sqlite_fts5",
        }
        assert legacy_content_contract["lexical_contract_version"] == (
            "sqlite-fts5-lexical-v1"
        )
        service._write_metadata_unlocked(metadata)  # noqa: SLF001

    claimed = service.claim_next_pipeline_job()
    assert claimed is not None
    assert claimed["job_id"] == created["job_id"]
    assert claimed["status"] == "running"
    with service._metadata_lock:  # noqa: SLF001 - verify the sealed stored identity.
        running = service._read_metadata_unlocked()["pipeline_jobs"][created["job_id"]]  # noqa: SLF001
        assert "lexical_profile" not in running["config_snapshot"]

    assert service.recover_pipeline_jobs() == 1
    assert service.get_pipeline_job(created["job_id"])["status"] == "queued"
    assert await KnowledgePipelineExecutor(service).run_once() is True

    completed = service.get_pipeline_job(created["job_id"])
    assert completed["status"] == "succeeded"
    assert completed["attempt"] == 2
    assert embedder.call_count == 1
    version_id = str(completed["candidate_version_id"])
    version = service.get_pipeline_version(version_id)
    rebound_namespace = str(version["namespace"])
    assert rebound_namespace != sealed_namespace
    assert "::dim-12::" in rebound_namespace

    with service._metadata_lock:  # noqa: SLF001 - assert persisted compatibility identity.
        stored = service._read_metadata_unlocked()  # noqa: SLF001
        stored_job = stored["pipeline_jobs"][created["job_id"]]
        stored_version = stored["pipeline_versions"][version_id]
        job_snapshot = stored_job["config_snapshot"]
        version_snapshot = stored_version["config_snapshot"]

    assert "lexical_profile" not in job_snapshot
    assert "lexical_profile" not in version_snapshot
    expected_legacy_lexical = {"required": False, "backend": "sqlite_fts5"}
    assert stored_job["index_contract"]["lexical"] == expected_legacy_lexical
    assert stored_version["index_contract"]["lexical"] == expected_legacy_lexical
    assert job_snapshot["index_contract"]["lexical"] == expected_legacy_lexical
    assert version_snapshot["index_contract"]["lexical"] == expected_legacy_lexical
    assert stored_version["content_index_contract"]["lexical_contract_version"] == (
        "sqlite-fts5-lexical-v1"
    )
    assert stored_version["content_index_contract"]["components"]["lexical"] == (
        "legacy_read_only"
    )
    assert stored_version["embedding_profile"]["effective"]["dimension"] == 12
    assert job_snapshot["embedding_profile"]["effective"]["dimension"] == 12
    assert stored_job["config_snapshot_fingerprint"] == service._mapping_sha256(  # noqa: SLF001
        job_snapshot
    )
    assert rebound_namespace == service._candidate_namespace(  # noqa: SLF001
        str(kb["id"]),
        version_id,
        stored_job["index_contract"],
    )
    assert stored_version["lexical_index_ready"] is False
    assert stored_job["lexical_index_receipt"] == {}
    assert stored_version["lexical_index_receipt"] == {}
    assert service.lexical_store.count_namespace(rebound_namespace) == 0
    assert vector_store.count_namespace(sealed_namespace) == 0
    assert vector_store.count_namespace(rebound_namespace) == version["chunk_count"]
