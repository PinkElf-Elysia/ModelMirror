from __future__ import annotations

import asyncio
import io
import json
import shutil
import threading
from pathlib import Path

import httpx
import pytest

from server.file_assets.service import FileAssetService, FileAssetServiceError
from server.main import app
from server.rag.api import set_rag_service_for_tests
from server.rag.embedder import EmbeddingClient
from server.rag.pipeline_executor import KnowledgePipelineExecutor
from server.rag.rag_service import (
    DocumentDeletionError,
    PipelineJobStateError,
    RagService,
)
from server.rag.vector_store import LocalJsonVectorStore


def build_service(tmp_path: Path) -> RagService:
    return RagService(
        storage_dir=tmp_path / "storage",
        uploads_dir=tmp_path / "uploads",
        embedder=EmbeddingClient(api_key="", dimension=128),
        vector_store=LocalJsonVectorStore(tmp_path / "storage" / "vectors.json"),
        llm_enabled=False,
    )


def configure_vector_draft(service: RagService, kb_id: str) -> dict[str, object]:
    """Keep deletion fixtures on the only buildable Round 4A retrieval path."""
    return service.update_pipeline_draft(
        kb_id,
        {},
        retrieval_profile={"mode": "vector"},
    )


def mark_version_as_previously_active(service: RagService, version_id: str) -> None:
    """Model a historical active version without authorizing first activation."""
    with service._metadata_lock:  # noqa: SLF001 - historical deletion fixture.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        version = metadata["pipeline_versions"][version_id]
        kb_id = str(version["kb_id"])
        previous_id = metadata["pipeline_active_versions"].get(kb_id)
        if previous_id and previous_id in metadata["pipeline_versions"]:
            metadata["pipeline_versions"][previous_id]["status"] = "ready"
        version["status"] = "active"
        version["activated_at"] = 1.0
        metadata["pipeline_active_versions"][kb_id] = version_id
        service._write_metadata_unlocked(metadata)  # noqa: SLF001


@pytest.mark.asyncio
async def test_delete_purges_active_historical_and_pipeline_payloads(
    tmp_path: Path,
) -> None:
    service = build_service(tmp_path)
    kb = service.create_knowledge_base("deletion closure")
    deleted = await service.upload_document(
        kb["id"],
        "secret.txt",
        b"DELETION-CANARY-7319 must disappear from every index.",
        pipeline_only=True,
    )
    survivor = await service.upload_document(
        kb["id"],
        "survivor.txt",
        b"SURVIVOR-CANARY-4421 remains searchable.",
        pipeline_only=True,
    )
    draft = configure_vector_draft(service, kb["id"])
    job_payload = service.create_pipeline_job(
        kb["id"],
        draft_version=draft["version"],
        source_document_ids=[deleted["id"], survivor["id"]],
    )
    executor = KnowledgePipelineExecutor(service)
    assert await executor.run_once() is True
    completed = service.get_pipeline_job(job_payload["job_id"])
    version = service.get_pipeline_version(completed["candidate_version_id"])
    original_receipt = json.loads(json.dumps(version["chunking_receipt"]))
    mark_version_as_previously_active(service, version["version_id"])

    deleted_result = next(
        item
        for item in completed["document_results"]
        if item["source_id"] == deleted["id"]
    )
    deleted_source = next(
        item
        for item in completed["sources"]
        if item["source_id"] == deleted["id"]
    )
    original_path = Path(
        service._read_metadata()["documents"][deleted["id"]]["stored_path"]
    )
    snapshot_path = service._pipeline_snapshot_path(deleted_source["snapshot_key"])
    processed_path = service._pipeline_processed_path(deleted_result["artifact_key"])
    vision_path = service._pipeline_vision_path(deleted_result["vision_artifact_key"])
    vision_path.parent.mkdir(parents=True, exist_ok=True)
    vision_path.write_text('{"text":"DELETION-CANARY-7319"}', encoding="utf-8")
    page_dir = vision_path.parent / f"{vision_path.stem}_pages"
    page_dir.mkdir(parents=True, exist_ok=True)
    (page_dir / "page_1.json").write_text(
        '{"text":"DELETION-CANARY-7319"}', encoding="utf-8"
    )
    assert original_path.is_file()
    assert snapshot_path.is_file()
    assert processed_path.is_file()

    service.delete_document(deleted["id"])

    assert not original_path.exists()
    assert not snapshot_path.exists()
    assert not processed_path.exists()
    assert not vision_path.exists()
    assert not page_dir.exists()
    assert service.vector_store.list_document_chunks(deleted["id"]) == []
    assert service.vector_store.list_document_chunks(
        f"{version['version_id']}_{deleted['id']}"
    ) == []
    assert service.lexical_store.query(kb["id"], "DELETION CANARY 7319", 10) == []
    historical_lexical = service.lexical_store.query(
        version["namespace"], "DELETION CANARY 7319", 10
    )
    assert all(
        item.doc_id != f"{version['version_id']}_{deleted['id']}"
        and "DELETION-CANARY-7319" not in item.text
        for item in historical_lexical
    )

    active = await service.search_knowledge(kb["id"], "DELETION-CANARY-7319")
    historical = await service.query_pipeline_version(
        version["version_id"],
        "DELETION-CANARY-7319",
        generate_answer=False,
    )
    for result in (active, historical):
        serialized = json.dumps(result, ensure_ascii=False)
        assert deleted["id"] not in serialized
        assert "DELETION-CANARY-7319" not in serialized

    metadata = service._read_metadata()
    assert deleted["id"] not in metadata["documents"]
    audit = metadata["document_deletions"][deleted["id"]]
    assert set(audit) == {
        "tenant_id",
        "content_hash",
        "requested_at",
        "deleted_at",
        "status",
        "error_code",
    }
    assert audit["tenant_id"] == "local"
    assert audit["status"] == "deleted"
    assert audit["content_hash"]
    assert "secret.txt" not in json.dumps(audit)
    updated_version = service.get_pipeline_version(version["version_id"])
    assert all(
        item.get("source_id") != deleted["id"]
        for item in updated_version["source_summary"]
    )
    assert all(
        item.get("source_id") != deleted["id"]
        for item in updated_version["document_results"]
    )
    assert updated_version["document_count"] == 1
    assert updated_version["lineage_status"] == "invalidated"
    assert updated_version["lineage_reason_codes"] == ["source_deleted"]
    assert updated_version["chunking_receipt"] == original_receipt
    assert service._read_metadata()["pipeline_active_versions"][kb["id"]] == version[
        "version_id"
    ]
    evidence = service.pipeline_version_evidence(version["version_id"])
    assert evidence["lineage_status"] == "invalidated"
    assert evidence["lineage_reason_codes"] == ["source_deleted"]
    assert evidence["chunking_receipt_status"] == "lineage_invalidated"
    with pytest.raises(PipelineJobStateError, match="source was deleted"):
        service.pipeline_corpus_snapshot(version["version_id"])
    with pytest.raises(PipelineJobStateError, match="source was deleted"):
        service.activate_pipeline_version(version["version_id"])
    assert [item["id"] for item in service.list_documents(kb["id"])] == [
        survivor["id"]
    ]
    service.delete_document(deleted["id"])


@pytest.mark.asyncio
async def test_failed_cleanup_isolated_immediately_and_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = build_service(tmp_path)
    kb = service.create_knowledge_base("retry deletion")
    document = await service.upload_document(
        kb["id"],
        "retry.txt",
        b"RETRY-DELETE-CANARY-9137",
        pipeline_only=True,
    )
    original_delete = service.vector_store.delete_document
    attempts = 0

    def fail_once(doc_id: str) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("simulated storage failure")
        original_delete(doc_id)

    monkeypatch.setattr(service.vector_store, "delete_document", fail_once)
    with pytest.raises(DocumentDeletionError):
        service.delete_document(document["id"])

    assert service.list_documents(kb["id"]) == []
    isolated = await service.query(kb["id"], "RETRY-DELETE-CANARY-9137")
    assert isolated["sources"] == []
    metadata = service._read_metadata()
    assert metadata["documents"][document["id"]]["deletion_status"] == "failed"
    assert metadata["document_deletions"][document["id"]]["status"] == "failed"
    assert (
        metadata["document_deletions"][document["id"]]["error_code"]
        == "rag_document_cleanup_failed"
    )

    service.delete_document(document["id"])
    assert service._read_metadata()["document_deletions"][document["id"]]["status"] == "deleted"
    assert service.vector_store.list_document_chunks(document["id"]) == []


@pytest.mark.asyncio
async def test_concurrent_delete_has_single_cleanup_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = build_service(tmp_path)
    kb = service.create_knowledge_base("concurrent deletion")
    document = await service.upload_document(
        kb["id"],
        "race.txt",
        b"RACE-CANARY",
        pipeline_only=True,
    )
    entered = threading.Event()
    release = threading.Event()
    original_purge = service._purge_document_payloads

    def blocking_purge(*args, **kwargs) -> None:
        entered.set()
        assert release.wait(timeout=5)
        original_purge(*args, **kwargs)

    monkeypatch.setattr(service, "_purge_document_payloads", blocking_purge)
    first_error: list[Exception] = []

    def first_delete() -> None:
        try:
            service.delete_document(document["id"])
        except Exception as exc:  # pragma: no cover - diagnostic collection
            first_error.append(exc)

    thread = threading.Thread(target=first_delete)
    thread.start()
    assert entered.wait(timeout=5)
    with pytest.raises(DocumentDeletionError, match="already in progress"):
        service.delete_document(document["id"])
    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert first_error == []
    assert service._read_metadata()["document_deletions"][document["id"]]["status"] == "deleted"


@pytest.mark.asyncio
async def test_delete_unbinds_real_asset_and_invalidates_queued_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = build_service(tmp_path)
    asset_service = FileAssetService(
        storage_dir=tmp_path / "file-assets",
        mode="shadow",
        tenant_id="local",
    )
    monkeypatch.setenv("FILE_ASSET_STORE_MODE", "shadow")
    monkeypatch.setattr(
        "server.rag.rag_service.get_file_asset_service",
        lambda: asset_service,
    )
    kb = service.create_knowledge_base("shared asset")
    document = await service.upload_document(
        kb["id"],
        "shared.txt",
        b"SHARED-ASSET",
        pipeline_only=True,
    )
    draft = configure_vector_draft(service, kb["id"])
    job = service.create_pipeline_job(
        kb["id"],
        draft_version=draft["version"],
        source_document_ids=[document["id"]],
    )
    asset_id = service._read_metadata()["documents"][document["id"]]["asset_id"]
    assert service.list_pipeline_assets(kb_id=kb["id"])[0]["file_asset_id"] == asset_id
    asset_service.repository.add_binding(
        "local",
        asset_id,
        purpose="agent",
        scope_id="project-a",
    )
    stored_asset = asset_service.repository.get_asset("local", asset_id)
    assert stored_asset is not None
    blob_path = asset_service.blob_store.storage_dir / stored_asset.storage_key
    assert blob_path.is_file()
    service.delete_document(document["id"])

    shared_asset = asset_service.repository.get_asset("local", asset_id)
    assert shared_asset is not None
    assert shared_asset.reference_count == 1
    assert blob_path.is_file()
    assert not asset_service.repository.binding_exists(
        "local", asset_id, purpose="rag", scope_id=kb["id"]
    )
    assert asset_service.repository.binding_exists(
        "local", asset_id, purpose="agent", scope_id="project-a"
    )
    stored_job = service.get_pipeline_job(job["job_id"])
    assert stored_job["status"] == "cancelled"
    assert stored_job["sources"] == []
    with pytest.raises(PipelineJobStateError, match="no remaining source"):
        service.retry_pipeline_job(job["job_id"])


def test_file_asset_cleanup_status_retries_after_binding_is_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset_service = FileAssetService(
        storage_dir=tmp_path / "file-assets",
        mode="shadow",
        tenant_id="local",
    )
    asset = asset_service.upload(
        io.BytesIO(b"GC-RETRY-CANARY"),
        purpose="rag",
        scope_id="kb-gc",
        filename="gc.txt",
        declared_media_type="text/plain",
    )
    blob_store = asset_service.blob_store
    original_delete = blob_store.delete

    def fail_blob_delete(storage_key: str) -> bool:
        raise OSError("simulated blob delete failure")

    monkeypatch.setattr(blob_store, "delete", fail_blob_delete)
    assert asset_service.delete_asset(
        asset.asset_id,
        purpose="rag",
        scope_id="kb-gc",
    ) is True
    assert not asset_service.repository.binding_exists(
        "local",
        asset.asset_id,
        purpose="rag",
        scope_id="kb-gc",
    )
    assert asset_service.asset_cleanup_complete(asset.asset_id) is False
    assert asset_service.repository.get_asset("local", asset.asset_id) is not None

    monkeypatch.setattr(blob_store, "delete", original_delete)
    assert asset_service.asset_cleanup_complete(asset.asset_id) is True
    assert asset_service.repository.get_asset("local", asset.asset_id) is None


@pytest.mark.asyncio
async def test_asset_cleanup_pending_survives_reload_until_gc_completes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = build_service(tmp_path)
    kb = service.create_knowledge_base("asset cleanup retry")
    document = await service.upload_document(
        kb["id"],
        "pending.txt",
        b"ASSET-CLEANUP-PENDING-CANARY",
        pipeline_only=True,
    )
    with service._metadata_lock:
        metadata = service._read_metadata_unlocked()
        metadata["documents"][document["id"]]["asset_id"] = "asset_pending_1"
        service._write_metadata_unlocked(metadata)

    class DeferredAssetCleanup:
        def __init__(self) -> None:
            self.delete_calls = 0
            self.status_checks = 0

        def delete_asset(self, *args, **kwargs) -> bool:
            self.delete_calls += 1
            if self.delete_calls > 1:
                raise FileAssetServiceError(
                    404,
                    "file_asset_not_found",
                    "File asset not found.",
                )
            return True

        def asset_cleanup_complete(self, asset_id: str) -> bool:
            assert asset_id == "asset_pending_1"
            self.status_checks += 1
            return self.status_checks >= 2

    asset_cleanup = DeferredAssetCleanup()
    monkeypatch.setattr(
        "server.rag.rag_service.get_file_asset_service",
        lambda: asset_cleanup,
    )

    with pytest.raises(DocumentDeletionError, match="still pending"):
        service.delete_document(document["id"])
    metadata = service._read_metadata()
    assert metadata["documents"][document["id"]]["deletion_status"] == "cleanup_pending"
    assert metadata["documents"][document["id"]]["asset_binding_removed"] is True
    assert metadata["document_deletions"][document["id"]]["status"] == "cleanup_pending"
    assert metadata["document_deletions"][document["id"]]["deleted_at"] is None

    reloaded = build_service(tmp_path)
    pending = reloaded.list_pending_document_deletions(kb["id"])
    assert pending == [
        {
            "document_id": document["id"],
            "filename": "pending.txt",
            "status": "cleanup_pending",
            "error_code": "file_asset_cleanup_pending",
            "requested_at": metadata["document_deletions"][document["id"]]["requested_at"],
        }
    ]
    assert "ASSET-CLEANUP-PENDING-CANARY" not in json.dumps(pending)

    # Simulate a process exit after the binding was removed but before the RAG
    # tombstone persisted its asset_binding_removed marker.
    with reloaded._metadata_lock:
        crashed = reloaded._read_metadata_unlocked()
        crashed_document = crashed["documents"][document["id"]]
        crashed_document.pop("asset_binding_removed", None)
        crashed_document["deletion_status"] = "deleting"
        crashed["document_deletions"][document["id"]]["status"] = "deleting"
        reloaded._write_metadata_unlocked(crashed)

    with pytest.raises(DocumentDeletionError, match="still pending"):
        reloaded.delete_document(document["id"])
    assert reloaded._read_metadata()["document_deletions"][document["id"]]["status"] == "cleanup_pending"
    reloaded.delete_document(document["id"])
    assert reloaded._read_metadata()["document_deletions"][document["id"]]["status"] == "deleted"
    assert asset_cleanup.delete_calls == 2
    assert asset_cleanup.status_checks == 2


@pytest.mark.asyncio
async def test_vision_page_cache_delete_failure_stays_pending_and_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = build_service(tmp_path)
    kb = service.create_knowledge_base("vision cache retry")
    document = await service.upload_document(
        kb["id"],
        "vision.txt",
        b"VISION-PAGE-CACHE-CANARY",
        pipeline_only=True,
    )
    draft = configure_vector_draft(service, kb["id"])
    job_payload = service.create_pipeline_job(
        kb["id"],
        draft_version=draft["version"],
        source_document_ids=[document["id"]],
    )
    executor = KnowledgePipelineExecutor(service)
    assert await executor.run_once() is True
    job = service.get_pipeline_job(job_payload["job_id"])
    result = job["document_results"][0]
    vision_path = service._pipeline_vision_path(result["vision_artifact_key"])
    page_dir = vision_path.parent / f"{vision_path.stem}_pages"
    page_dir.mkdir(parents=True, exist_ok=True)
    (page_dir / "page_1.json").write_text(
        '{"text":"VISION-PAGE-CACHE-CANARY"}',
        encoding="utf-8",
    )
    original_rmtree = shutil.rmtree

    def fail_page_cache(path: str | Path, *args, **kwargs) -> None:
        if Path(path) == page_dir:
            raise OSError("simulated vision cache failure")
        original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr("server.rag.rag_service.shutil.rmtree", fail_page_cache)
    with pytest.raises(DocumentDeletionError):
        service.delete_document(document["id"])
    assert page_dir.is_dir()
    pending = service.list_pending_document_deletions(kb["id"])
    assert pending[0]["document_id"] == document["id"]
    assert pending[0]["status"] == "failed"

    monkeypatch.setattr("server.rag.rag_service.shutil.rmtree", original_rmtree)
    service.delete_document(document["id"])
    assert not page_dir.exists()
    assert service.list_pending_document_deletions(kb["id"]) == []


@pytest.mark.asyncio
async def test_complete_pipeline_job_rechecks_cancel_state_under_metadata_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = build_service(tmp_path)
    kb = service.create_knowledge_base("completion race")
    document = await service.upload_document(
        kb["id"],
        "race.txt",
        b"RACE",
        pipeline_only=True,
    )
    draft = configure_vector_draft(service, kb["id"])
    job_payload = service.create_pipeline_job(
        kb["id"],
        draft_version=draft["version"],
        source_document_ids=[document["id"]],
    )
    claimed = service.claim_next_pipeline_job()
    assert claimed is not None
    service.request_pipeline_job_cancel(job_payload["job_id"])

    # Stored chunk integrity has dedicated contract coverage.  Make that earlier
    # guard neutral so this test reaches the cancellation recheck under the
    # metadata lock instead of weakening its original concurrency assertion.
    monkeypatch.setattr(
        service,
        "_stored_vector_chunk_sequence_hash",
        lambda **_kwargs: "",
    )

    with pytest.raises(PipelineJobStateError, match="cannot publish"):
        service.complete_pipeline_job(
            job_payload["job_id"],
            document_count=1,
            chunk_count=1,
        )

    stored = service.get_pipeline_job(job_payload["job_id"])
    assert stored["status"] == "running"
    assert stored["cancel_requested"] is True
    assert job_payload["candidate_version_id"] not in service._read_metadata()["pipeline_versions"]


@pytest.mark.asyncio
async def test_running_pipeline_must_ack_stop_and_strict_cleanup_before_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = build_service(tmp_path)
    kb = service.create_knowledge_base("running pipeline handshake")
    document = await service.upload_document(
        kb["id"],
        "running.txt",
        b"RUNNING",
        pipeline_only=True,
    )
    draft = configure_vector_draft(service, kb["id"])
    job_payload = service.create_pipeline_job(
        kb["id"],
        draft_version=draft["version"],
        source_document_ids=[document["id"]],
    )
    executor = KnowledgePipelineExecutor(service)
    entered = asyncio.Event()
    release = asyncio.Event()
    late_paths = (
        service.pipeline_sources_dir / job_payload["job_id"] / "late.json",
        service.pipeline_processed_dir / job_payload["job_id"] / "late.json",
        service.pipeline_vision_dir / job_payload["job_id"] / "late.json",
    )

    async def delayed_load(job_id: str) -> list[dict[str, object]]:
        entered.set()
        await release.wait()
        for late_path in late_paths:
            late_path.parent.mkdir(parents=True, exist_ok=True)
            late_path.write_text('{"text":"LATE-WRITE-CANARY"}', encoding="utf-8")
        return []

    monkeypatch.setattr(executor, "_load_sources", delayed_load)
    run_task = asyncio.create_task(executor.run_once())
    await asyncio.wait_for(entered.wait(), timeout=5)

    with pytest.raises(DocumentDeletionError, match="still pending"):
        service.delete_document(document["id"])
    pending = service._read_metadata()
    assert pending["documents"][document["id"]]["deletion_status"] == "cleanup_pending"
    assert pending["document_deletions"][document["id"]]["status"] == "cleanup_pending"
    assert pending["document_deletions"][document["id"]]["deleted_at"] is None
    running_job = service.get_pipeline_job(job_payload["job_id"])
    assert running_job["status"] == "running"
    assert running_job["cancel_requested"] is True
    assert running_job["deletion_invalidated"] is True
    assert not running_job.get("deletion_artifacts_purged")

    release.set()
    assert await asyncio.wait_for(run_task, timeout=5) is True
    acknowledged = service.get_pipeline_job(job_payload["job_id"])
    assert acknowledged["status"] == "cancelled"
    assert acknowledged["deletion_artifacts_purged"] is True
    assert all(not late_path.exists() for late_path in late_paths)
    assert document["id"] in service._read_metadata()["documents"]

    service.delete_document(document["id"])
    assert document["id"] not in service._read_metadata()["documents"]
    assert service._read_metadata()["document_deletions"][document["id"]]["status"] == "deleted"


@pytest.mark.asyncio
async def test_running_pipeline_cleanup_failure_keeps_retryable_tombstone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = build_service(tmp_path)
    kb = service.create_knowledge_base("running cleanup retry")
    document = await service.upload_document(
        kb["id"],
        "retry.txt",
        b"RETRY",
        pipeline_only=True,
    )
    draft = configure_vector_draft(service, kb["id"])
    job_payload = service.create_pipeline_job(
        kb["id"],
        draft_version=draft["version"],
        source_document_ids=[document["id"]],
    )
    executor = KnowledgePipelineExecutor(service)
    entered = asyncio.Event()
    release = asyncio.Event()
    late_dir = service.pipeline_processed_dir / job_payload["job_id"]
    late_path = late_dir / "late.json"

    async def delayed_load(job_id: str) -> list[dict[str, object]]:
        entered.set()
        await release.wait()
        late_dir.mkdir(parents=True, exist_ok=True)
        late_path.write_text('{"text":"CLEANUP-FAILURE-CANARY"}', encoding="utf-8")
        return []

    monkeypatch.setattr(executor, "_load_sources", delayed_load)
    run_task = asyncio.create_task(executor.run_once())
    await asyncio.wait_for(entered.wait(), timeout=5)
    with pytest.raises(DocumentDeletionError):
        service.delete_document(document["id"])

    original_rmtree = shutil.rmtree

    def fail_late_dir(path: str | Path, *args, **kwargs) -> None:
        if Path(path) == late_dir:
            raise OSError("simulated invalidated-job cleanup failure")
        original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr("server.rag.rag_service.shutil.rmtree", fail_late_dir)
    release.set()
    assert await asyncio.wait_for(run_task, timeout=5) is True
    failed_job = service.get_pipeline_job(job_payload["job_id"])
    assert failed_job["status"] == "failed"
    assert failed_job["deletion_artifacts_purged"] is False
    assert failed_job["deletion_cleanup_error"] == "rag_pipeline_cleanup_failed"
    assert late_path.is_file()
    assert service._read_metadata()["document_deletions"][document["id"]]["status"] == "cleanup_pending"

    with pytest.raises(DocumentDeletionError):
        service.delete_document(document["id"])
    assert document["id"] in service._read_metadata()["documents"]
    assert late_path.is_file()

    monkeypatch.setattr("server.rag.rag_service.shutil.rmtree", original_rmtree)
    service.delete_document(document["id"])
    assert not late_path.exists()
    assert service._read_metadata()["document_deletions"][document["id"]]["status"] == "deleted"


@pytest.mark.asyncio
async def test_restart_recovery_never_requeues_deletion_invalidated_job(
    tmp_path: Path,
) -> None:
    service = build_service(tmp_path)
    kb = service.create_knowledge_base("restart invalidation")
    document = await service.upload_document(
        kb["id"],
        "restart.txt",
        b"RESTART",
        pipeline_only=True,
    )
    draft = configure_vector_draft(service, kb["id"])
    job_payload = service.create_pipeline_job(
        kb["id"],
        draft_version=draft["version"],
        source_document_ids=[document["id"]],
    )
    assert service.claim_next_pipeline_job() is not None
    with service._metadata_lock:
        metadata = service._read_metadata_unlocked()
        running = metadata["pipeline_jobs"][job_payload["job_id"]]
        running["cancel_requested"] = True
        running["deletion_invalidated"] = True
        service._write_metadata_unlocked(metadata)

    reloaded = build_service(tmp_path)
    assert reloaded.recover_pipeline_jobs() == 1
    recovered = reloaded.get_pipeline_job(job_payload["job_id"])
    assert recovered["status"] == "cancelled"
    assert recovered["cancel_requested"] is True
    assert recovered["deletion_invalidated"] is True
    assert not recovered.get("deletion_artifacts_purged")
    assert reloaded.claim_next_pipeline_job() is None


@pytest.mark.asyncio
async def test_pending_deletion_get_is_tenant_and_knowledge_base_scoped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = build_service(tmp_path)
    target_kb = service.create_knowledge_base("target")
    other_kb = service.create_knowledge_base("other")
    document = await service.upload_document(
        target_kb["id"],
        "pending-api.txt",
        b"PENDING-API-BODY-CANARY",
        pipeline_only=True,
    )

    def fail_cleanup(doc_id: str) -> None:
        raise OSError("simulated cleanup failure")

    monkeypatch.setattr(service.vector_store, "delete_document", fail_cleanup)
    with pytest.raises(DocumentDeletionError):
        service.delete_document(document["id"])

    set_rag_service_for_tests(service)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            response = await client.get(
                f"/api/rag/knowledge_bases/{target_kb['id']}/pending-deletions"
            )
            other_response = await client.get(
                f"/api/rag/knowledge_bases/{other_kb['id']}/pending-deletions"
            )
    finally:
        set_rag_service_for_tests(None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["tenant_id"] == "local"
    assert payload["knowledge_base_id"] == target_kb["id"]
    assert payload["deletions"] == [
        {
            "document_id": document["id"],
            "filename": "pending-api.txt",
            "status": "failed",
            "error_code": "rag_document_cleanup_failed",
            "requested_at": payload["deletions"][0]["requested_at"],
        }
    ]
    serialized = json.dumps(payload)
    assert "PENDING-API-BODY-CANARY" not in serialized
    assert "stored_path" not in serialized
    assert other_response.status_code == 200
    assert other_response.json()["deletions"] == []
