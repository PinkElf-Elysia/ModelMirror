from __future__ import annotations

import asyncio
import io
import sqlite3
import threading
from pathlib import Path

import httpx
import pytest

from server.file_assets.repository import FileAssetRepositoryError
from server.file_assets.service import FileAssetService
from server.main import app
from server.rag.api import set_rag_service_for_tests
from server.rag.embedder import EmbeddingClient
from server.rag.rag_service import KnowledgeBaseDeletionError, RagService
from server.rag.vector_store import LocalJsonVectorStore, VectorChunk


def build_service(tmp_path: Path) -> RagService:
    return RagService(
        storage_dir=tmp_path / "storage",
        uploads_dir=tmp_path / "uploads",
        embedder=EmbeddingClient(api_key="", dimension=128),
        vector_store=LocalJsonVectorStore(tmp_path / "storage" / "vectors.json"),
        llm_enabled=False,
    )


def legacy_assets(tmp_path: Path) -> FileAssetService:
    return FileAssetService(
        storage_dir=tmp_path / "file-assets",
        mode="legacy",
        tenant_id="local",
    )


def shadow_assets(tmp_path: Path) -> FileAssetService:
    return FileAssetService(
        storage_dir=tmp_path / "file-assets",
        mode="shadow",
        tenant_id="local",
    )


def test_empty_legacy_knowledge_base_delete_is_restart_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = build_service(tmp_path)
    monkeypatch.setattr(
        "server.rag.rag_service.get_file_asset_service",
        lambda: legacy_assets(tmp_path),
    )
    kb = service.create_knowledge_base("empty")

    service.delete_knowledge_base(kb["id"])
    service.delete_knowledge_base(kb["id"])

    metadata = service._read_metadata()
    assert kb["id"] not in metadata["knowledge_bases"]
    assert metadata["knowledge_base_deletions"][kb["id"]]["status"] == "deleted"
    restarted = build_service(tmp_path)
    restarted.delete_knowledge_base(kb["id"])


@pytest.mark.asyncio
async def test_delete_keeps_shared_asset_and_blocks_new_rag_bindings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset_service = shadow_assets(tmp_path)
    monkeypatch.setenv("FILE_ASSET_STORE_MODE", "shadow")
    monkeypatch.setattr(
        "server.rag.rag_service.get_file_asset_service", lambda: asset_service
    )
    service = build_service(tmp_path)
    kb = service.create_knowledge_base("shared")
    document = await service.upload_document(kb["id"], "shared.txt", b"shared")
    asset_id = service._read_metadata()["documents"][document["id"]]["asset_id"]
    asset_service.repository.add_binding(
        "local", asset_id, purpose="agent", scope_id="agent-project"
    )
    record = asset_service.repository.get_asset("local", asset_id)
    assert record is not None
    blob_path = asset_service.blob_store.storage_dir / record.storage_key

    service.delete_knowledge_base(kb["id"])

    retained = asset_service.repository.get_asset("local", asset_id)
    assert retained is not None and retained.reference_count == 1
    assert blob_path.is_file()
    assert not asset_service.repository.binding_exists(
        "local", asset_id, purpose="rag", scope_id=kb["id"]
    )
    assert asset_service.repository.binding_exists(
        "local", asset_id, purpose="agent", scope_id="agent-project"
    )
    with pytest.raises(FileAssetRepositoryError, match="file_scope_blocked"):
        asset_service.repository.add_binding(
            "local", asset_id, purpose="rag", scope_id=kb["id"]
        )


@pytest.mark.asyncio
async def test_gc_failure_stays_pending_and_restart_retries_asset_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset_service = shadow_assets(tmp_path)
    monkeypatch.setenv("FILE_ASSET_STORE_MODE", "shadow")
    monkeypatch.setattr(
        "server.rag.rag_service.get_file_asset_service", lambda: asset_service
    )
    service = build_service(tmp_path)
    kb = service.create_knowledge_base("gc retry")
    document = await service.upload_document(kb["id"], "secret.txt", b"secret")
    asset_id = service._read_metadata()["documents"][document["id"]]["asset_id"]

    def fail_delete(_storage_key: str) -> bool:
        raise OSError("private storage failure detail")

    monkeypatch.setattr(asset_service.blob_store, "delete", fail_delete)
    with pytest.raises(KnowledgeBaseDeletionError, match="cleanup is incomplete"):
        service.delete_knowledge_base(kb["id"])

    pending = service._read_metadata()
    assert pending["knowledge_bases"][kb["id"]]["deletion_status"] == "cleanup_pending"
    deletion = pending["knowledge_base_deletions"][kb["id"]]
    assert deletion["status"] == "cleanup_pending"
    assert deletion["asset_ids"] == [asset_id]
    assert "private storage failure detail" not in str(deletion)
    assert asset_service.repository.scope_cleanup_asset_ids(
        "local", purpose="rag", scope_id=kb["id"]
    ) == (asset_id,)

    restarted_assets = shadow_assets(tmp_path)
    restarted = build_service(tmp_path)
    monkeypatch.setattr(
        "server.rag.rag_service.get_file_asset_service", lambda: restarted_assets
    )
    restarted.delete_knowledge_base(kb["id"])
    assert restarted._read_metadata()["knowledge_base_deletions"][kb["id"]][
        "status"
    ] == "deleted"
    assert restarted_assets.repository.get_asset("local", asset_id) is None


@pytest.mark.asyncio
async def test_upload_late_write_is_tombstoned_and_removed_on_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = build_service(tmp_path)
    asset_service = legacy_assets(tmp_path)
    monkeypatch.setattr(
        "server.rag.rag_service.get_file_asset_service", lambda: asset_service
    )
    kb = service.create_knowledge_base("upload race")
    entered = asyncio.Event()
    release = asyncio.Event()
    original_embed = service.embedder.embed_texts

    async def slow_embed(texts: list[str]) -> list[list[float]]:
        entered.set()
        await release.wait()
        return await original_embed(texts)

    monkeypatch.setattr(service.embedder, "embed_texts", slow_embed)
    upload = asyncio.create_task(
        service.upload_document(kb["id"], "late.txt", b"LATE-WRITE-CANARY")
    )
    await entered.wait()
    with pytest.raises(KnowledgeBaseDeletionError):
        await asyncio.to_thread(service.delete_knowledge_base, kb["id"])
    with pytest.raises(KnowledgeBaseDeletionError, match="isolated"):
        release.set()
        await upload

    pending = service._read_metadata()
    late_ids = [
        doc_id
        for doc_id, item in pending["documents"].items()
        if item.get("kb_id") == kb["id"]
    ]
    assert len(late_ids) == 1
    assert pending["documents"][late_ids[0]]["deletion_status"] == "deleting"
    service.delete_knowledge_base(kb["id"])
    assert kb["id"] not in service._read_metadata()["knowledge_bases"]


@pytest.mark.asyncio
async def test_running_pipeline_late_candidate_is_purged_before_final_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = build_service(tmp_path)
    monkeypatch.setattr(
        "server.rag.rag_service.get_file_asset_service",
        lambda: legacy_assets(tmp_path),
    )
    kb = service.create_knowledge_base("pipeline race")
    document = await service.upload_document(kb["id"], "source.txt", b"pipeline")
    draft = service.get_pipeline_draft(kb["id"])
    created = service.create_pipeline_job(
        kb["id"],
        draft_version=draft["version"],
        source_document_ids=[document["id"]],
    )
    running = service.claim_next_pipeline_job()
    assert running is not None and running["job_id"] == created["job_id"]
    namespace = service._read_metadata()["pipeline_jobs"][created["job_id"]][
        "candidate_namespace"
    ]

    with pytest.raises(KnowledgeBaseDeletionError):
        service.delete_knowledge_base(kb["id"])
    invalidated = service._read_metadata()["pipeline_jobs"][created["job_id"]]
    assert invalidated["status"] == "running"
    assert invalidated["cancel_requested"] is True
    assert invalidated["deletion_invalidated"] is True

    service.vector_store.add_chunks(
        [
            VectorChunk(
                id="late_chunk",
                kb_id=namespace,
                doc_id=f"{created['candidate_version_id']}_{document['id']}",
                document_name="late.txt",
                text="PIPELINE-LATE-WRITE",
                embedding=[0.0] * 128,
                chunk_index=0,
            )
        ]
    )
    service.cancel_running_pipeline_job(created["job_id"])
    service.delete_knowledge_base(kb["id"])

    assert service.vector_store.list_document_chunks(
        f"{created['candidate_version_id']}_{document['id']}"
    ) == []
    metadata = service._read_metadata()
    assert created["job_id"] not in metadata["pipeline_jobs"]
    assert metadata["knowledge_base_deletions"][kb["id"]]["status"] == "deleted"


def test_blocked_scope_rejects_new_upload_and_retains_cleanup_handle(
    tmp_path: Path,
) -> None:
    assets = shadow_assets(tmp_path)
    first = assets.upload(
        io.BytesIO(b"scope"),
        purpose="rag",
        scope_id="kb-blocked",
        filename="scope.txt",
        declared_media_type="text/plain",
    )
    affected, _pending = assets.block_and_delete_rag_scope("kb-blocked")
    assert affected == (first.asset_id,)
    assert assets.repository.scope_cleanup_asset_ids(
        "local", purpose="rag", scope_id="kb-blocked"
    ) == (first.asset_id,)
    with pytest.raises(Exception) as captured:
        assets.upload(
            io.BytesIO(b"late"),
            purpose="rag",
            scope_id="kb-blocked",
            filename="late.txt",
            declared_media_type="text/plain",
        )
    assert getattr(captured.value, "error_code", None) == "file_scope_blocked"


def test_create_binding_and_scope_block_are_serialized_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assets = shadow_assets(tmp_path)
    creator = assets.repository
    blocker = type(creator)(creator.storage_dir)
    receipt = assets.blob_store.write_bytes(b"atomic scope race")
    checked = threading.Event()
    release = threading.Event()

    class PausingConnection(sqlite3.Connection):
        def execute(self, sql: str, parameters=()):  # type: ignore[no-untyped-def]
            cursor = super().execute(sql, parameters)
            if "SELECT 1 FROM file_scope_tombstones" in sql:
                checked.set()
                assert release.wait(timeout=5)
            return cursor

    def paused_connect() -> sqlite3.Connection:
        connection = sqlite3.connect(
            creator.database_path,
            timeout=15,
            factory=PausingConnection,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    monkeypatch.setattr(creator, "_connect", paused_connect)
    created: list[object] = []
    errors: list[BaseException] = []

    def create_binding() -> None:
        try:
            created.append(
                creator.create_asset(
                    "local",
                    purpose="rag",
                    scope_id="kb-race",
                    display_name="race.txt",
                    format_id="plain_text",
                    media_type="text/plain",
                    storage_key=receipt.storage_key,
                    sha256=receipt.sha256,
                    byte_size=receipt.byte_size,
                    status="ready",
                    create_initial_binding=True,
                )
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    creator_thread = threading.Thread(target=create_binding)
    creator_thread.start()
    assert checked.wait(timeout=5)
    blocked: list[tuple[str, ...]] = []

    def block_scope() -> None:
        blocked.append(
            blocker.block_scope_and_remove_bindings(
                "local",
                purpose="rag",
                scope_id="kb-race",
                expire_if_unreferenced=True,
            )
        )

    blocker_thread = threading.Thread(target=block_scope)
    blocker_thread.start()
    release.set()
    creator_thread.join(timeout=5)
    blocker_thread.join(timeout=5)

    assert not creator_thread.is_alive() and not blocker_thread.is_alive()
    assert errors == [] and len(created) == 1
    asset_id = str(getattr(created[0], "id"))
    assert blocked == [(asset_id,)]
    assert blocker.scope_is_blocked(
        "local", purpose="rag", scope_id="kb-race"
    )
    assert blocker.list_bound_assets(
        "local", purpose="rag", scope_id="kb-race"
    ) == ()
    assert blocker.scope_cleanup_asset_ids(
        "local", purpose="rag", scope_id="kb-race"
    ) == (asset_id,)


def test_proposal_document_late_write_is_durable_and_retry_removes_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = build_service(tmp_path)
    monkeypatch.setattr(
        "server.rag.rag_service.get_file_asset_service",
        lambda: legacy_assets(tmp_path),
    )
    kb = service.create_knowledge_base("proposal race")
    proposal = service.create_knowledge_write_proposal(
        kb["id"], title="late proposal", content="PROPOSAL-LATE-WRITE"
    )
    entered = threading.Event()
    release = threading.Event()
    original_create = service._create_managed_proposal_document_claimed

    def delayed_create(item: dict[str, object]) -> dict[str, object]:
        entered.set()
        assert release.wait(timeout=5)
        return original_create(item)

    monkeypatch.setattr(
        service, "_create_managed_proposal_document_claimed", delayed_create
    )
    errors: list[Exception] = []

    def create_document() -> None:
        try:
            service._create_managed_proposal_document(proposal)
        except Exception as exc:
            errors.append(exc)

    writer = threading.Thread(target=create_document)
    writer.start()
    assert entered.wait(timeout=5)
    with pytest.raises(KnowledgeBaseDeletionError):
        service.delete_knowledge_base(kb["id"])
    release.set()
    writer.join(timeout=5)
    assert not writer.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], KnowledgeBaseDeletionError)

    metadata = service._read_metadata()
    pending_documents = [
        item
        for item in metadata["documents"].values()
        if item.get("kb_id") == kb["id"]
    ]
    assert len(pending_documents) == 1
    stored_path = Path(str(pending_documents[0]["stored_path"]))
    assert stored_path.is_file()
    assert pending_documents[0]["deletion_status"] == "deleting"

    service.delete_knowledge_base(kb["id"])
    assert not stored_path.exists()
    assert kb["id"] not in service._read_metadata()["knowledge_bases"]


@pytest.mark.asyncio
async def test_api_reports_pending_then_only_reports_success_after_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = build_service(tmp_path)
    monkeypatch.setattr(
        "server.rag.rag_service.get_file_asset_service",
        lambda: legacy_assets(tmp_path),
    )
    kb = service.create_knowledge_base("API cleanup contract")
    original_purge = service._purge_knowledge_base_namespaces_and_uploads
    failures = 1

    def fail_once(kb_id: str) -> None:
        nonlocal failures
        if failures:
            failures -= 1
            raise OSError("private path must not escape")
        original_purge(kb_id)

    monkeypatch.setattr(
        service, "_purge_knowledge_base_namespaces_and_uploads", fail_once
    )
    set_rag_service_for_tests(service)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            pending = await client.delete(
                f"/api/rag/knowledge_bases/{kb['id']}"
            )
            assert pending.status_code == 409
            assert pending.json() == {
                "detail": {
                    "code": "rag_knowledge_base_cleanup_pending",
                    "message": "Knowledge base was isolated, but cleanup is incomplete; retry deletion.",
                }
            }
            listing = await client.get("/api/rag/knowledge_bases")
            listed = next(
                item
                for item in listing.json()["knowledge_bases"]
                if item["id"] == kb["id"]
            )
            assert listed["deletion_status"] == "cleanup_pending"
            assert listed["deletion_error_code"] == "rag_knowledge_base_cleanup_failed"
            blocked_upload = await client.post(
                f"/api/rag/knowledge_bases/{kb['id']}/documents",
                files={"file": ("late.txt", b"late", "text/plain")},
            )
            assert blocked_upload.status_code == 409
            assert blocked_upload.json()["detail"]["code"] == "rag_knowledge_base_deleting"
            blocked_query = await client.post(
                "/api/rag/query",
                json={"kb_id": kb["id"], "question": "must stay isolated"},
            )
            assert blocked_query.status_code == 404

            completed = await client.delete(
                f"/api/rag/knowledge_bases/{kb['id']}"
            )
            assert completed.status_code == 200
            assert completed.json() == {"ok": True}
            repeated = await client.delete(
                f"/api/rag/knowledge_bases/{kb['id']}"
            )
            assert repeated.status_code == 200
            assert all(
                item["id"] != kb["id"]
                for item in (
                    await client.get("/api/rag/knowledge_bases")
                ).json()["knowledge_bases"]
            )
    finally:
        set_rag_service_for_tests(None)
