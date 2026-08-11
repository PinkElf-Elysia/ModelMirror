from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from server.file_assets.service import FileAssetServiceError
from server.rag import rag_service as rag_service_module
from server.rag.api import router as rag_router, set_rag_service_for_tests
from server.rag.embedder import EmbeddingClient
from server.rag.rag_service import RagService
from server.rag.vector_store import LocalJsonVectorStore


app = FastAPI()
app.include_router(rag_router)


class _OutputService:
    def __init__(self) -> None:
        self.read_calls = 0

    def read_output(self, output_id: str, *, purpose, scope_id: str):
        if (output_id, str(purpose.value), scope_id) != (
            "output_public",
            "chat",
            "chat-scope",
        ):
            raise FileAssetServiceError(
                404,
                "file_output_not_found",
                "The output file was not found in this scope.",
            )
        self.read_calls += 1
        return (
            SimpleNamespace(
                id=output_id,
                display_name="公开报告.md",
                media_type="text/markdown",
                preview_kind="text",
                format_id="markdown",
                producer_kind="chat_tool",
                source_run_id="run-public",
                source_message_id="assistant-public",
                source_node_id=None,
            ),
            b"# Public report\n\nNo provider call is needed.",
        )

    def get_output(self, *_args, **_kwargs):
        return SimpleNamespace(warnings=("output warning",))


class _AssetService:
    mode = "shadow"

    def __init__(self) -> None:
        self.uploaded: list[bytes] = []
        self.deleted: list[str] = []

    def upload(self, stream, **_kwargs):
        self.uploaded.append(stream.read())
        return SimpleNamespace(asset_id="asset_rag_output")

    def delete_asset(self, asset_id: str, **_kwargs) -> bool:
        self.deleted.append(asset_id)
        return False


class _NoExternalEmbedding(EmbeddingClient):
    async def embed_texts(self, _texts):  # pragma: no cover - must remain unreachable
        raise AssertionError("file-output import must not call an external embedder")


@pytest.mark.asyncio
async def test_file_output_import_is_scoped_local_idempotent_and_independent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outputs = _OutputService()
    assets = _AssetService()
    monkeypatch.setattr(rag_service_module, "get_file_output_service", lambda: outputs)
    monkeypatch.setattr(rag_service_module, "get_file_asset_service", lambda: assets)
    service = RagService(
        storage_dir=tmp_path / "storage",
        uploads_dir=tmp_path / "uploads",
        embedder=_NoExternalEmbedding(api_key="", dimension=32),
        vector_store=LocalJsonVectorStore(tmp_path / "storage" / "vectors.json"),
        llm_enabled=False,
    )
    set_rag_service_for_tests(service)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            kb = (await client.post("/api/rag/knowledge_bases", json={"name": "输出派生"})).json()
            body = {
                "output_id": "output_public",
                "purpose": "chat",
                "scope_id": "chat-scope",
            }
            first = await client.post(
                f"/api/rag/knowledge_bases/{kb['id']}/documents/from-file-output",
                json=body,
            )
            second = await client.post(
                f"/api/rag/knowledge_bases/{kb['id']}/documents/from-file-output",
                json=body,
            )
            assert first.status_code == second.status_code == 200
            payload = first.json()
            assert payload["id"] == second.json()["id"]
            assert payload["file_output_id"] == "output_public"
            assert payload["ingestion_status"] == "indexed_file_output"
            assert payload["file_output_source"] == {
                "source_filename": "公开报告.md",
                "source_sha256": "1592d186ae184fcf44c1ad4a450b4658f509896f916157d966b1e170df764258",
                "purpose": "chat",
                "producer_kind": "chat_tool",
                "format": "markdown",
                "source_run_id": "run-public",
                "source_message_id": "assistant-public",
                "source_node_id": None,
                "sections": [
                    {
                        "page_number": None,
                        "slide": None,
                        "sheet": None,
                        "line_range": "1-3",
                        "row_range": None,
                        "heading_path": [],
                        "time_range": None,
                    }
                ],
            }
            assert outputs.read_calls == 1
            assert assets.uploaded == [b"# Public report\n\nNo provider call is needed."]
            assert service.vector_store.list_document_chunks(payload["id"])[0].source_block_id == "output_public"

            wrong_scope = await client.post(
                f"/api/rag/knowledge_bases/{kb['id']}/documents/from-file-output",
                json={**body, "scope_id": "other-scope", "output_id": "output_other"},
            )
            assert wrong_scope.status_code == 404
            assert outputs.read_calls == 1
    finally:
        set_rag_service_for_tests(None)


@pytest.mark.asyncio
async def test_file_output_import_rejects_deleting_knowledge_base_before_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outputs = _OutputService()
    assets = _AssetService()
    monkeypatch.setattr(rag_service_module, "get_file_output_service", lambda: outputs)
    monkeypatch.setattr(rag_service_module, "get_file_asset_service", lambda: assets)
    service = RagService(
        storage_dir=tmp_path / "storage",
        uploads_dir=tmp_path / "uploads",
        embedder=_NoExternalEmbedding(api_key="", dimension=32),
        vector_store=LocalJsonVectorStore(tmp_path / "storage" / "vectors.json"),
        llm_enabled=False,
    )
    kb = service.create_knowledge_base("待删除")
    with service._metadata_lock:  # noqa: SLF001 - deterministic deletion fixture
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        metadata["knowledge_bases"][kb["id"]]["deletion_status"] = "cleanup_pending"
        service._write_metadata_unlocked(metadata)  # noqa: SLF001

    with pytest.raises(Exception, match="isolated"):
        await service.import_file_output(
            kb["id"],
            output_id="output_public",
            output_purpose="chat",
            output_scope_id="chat-scope",
        )
    assert outputs.read_calls == 0
    assert assets.uploaded == []
