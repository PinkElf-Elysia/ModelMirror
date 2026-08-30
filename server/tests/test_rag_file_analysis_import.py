from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from server.file_assets.analysis import FileAnalysisArtifact, FileAnalysisMode, FileAnalysisSection
from server.rag import rag_service as rag_service_module
from server.rag.api import router as rag_router, set_rag_service_for_tests
from server.rag.embedder import EmbeddingClient
from server.rag.rag_service import KnowledgeBaseDeletionError, RagService
from server.rag.vector_store import LocalJsonVectorStore


app = FastAPI()
app.include_router(rag_router)


class _AnalysisAssetService:
    mode = "shadow"

    def __init__(self) -> None:
        self.resolve_calls = 0
        self.uploaded: list[bytes] = []
        self.deleted: list[str] = []

    def resolve_analysis_artifact(
        self,
        asset_id: str,
        artifact_id: str,
        *,
        scope_id: str,
    ) -> FileAnalysisArtifact:
        assert (asset_id, artifact_id, scope_id) == (
            "asset_chat",
            "artifact_analysis",
            "chat-scope",
        )
        self.resolve_calls += 1
        return FileAnalysisArtifact(
            asset_id=asset_id,
            source_filename="公开合成扫描样本.pdf",
            source_sha256="a" * 64,
            format="pdf",
            mode=FileAnalysisMode.VISION,
            target_id="target_exact",
            connection_name="Exact connection",
            model_id="vendor/vision",
            selected_pages=(1, 3),
            sections=(
                FileAnalysisSection(kind="visual_summary", page=1, text="第一页图表摘要"),
                FileAnalysisSection(kind="ocr_text", page=3, text="第三页识别文字"),
            ),
            warnings=("第 2 页未处理。",),
            processed_pages=2,
            failed_pages=(),
            extracted_chars=14,
            truncated=False,
        )

    def upload(self, stream, **_kwargs):
        self.uploaded.append(stream.read())
        return SimpleNamespace(asset_id="asset_rag_derived")

    def delete_asset(self, asset_id: str, **_kwargs) -> bool:
        self.deleted.append(asset_id)
        return False


@pytest.mark.asyncio
async def test_file_analysis_import_is_structured_idempotent_and_original_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assets = _AnalysisAssetService()
    monkeypatch.setattr(rag_service_module, "get_file_asset_service", lambda: assets)
    service = RagService(
        storage_dir=tmp_path / "storage",
        uploads_dir=tmp_path / "uploads",
        embedder=EmbeddingClient(api_key="", dimension=32),
        vector_store=LocalJsonVectorStore(tmp_path / "storage" / "vectors.json"),
        llm_enabled=False,
    )
    set_rag_service_for_tests(service)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            kb = (await client.post("/api/rag/knowledge_bases", json={"name": "分析派生"})).json()
            body = {
                "asset_id": "asset_chat",
                "analysis_artifact_id": "artifact_analysis",
                "chat_scope_id": "chat-scope",
            }
            first = await client.post(
                f"/api/rag/knowledge_bases/{kb['id']}/documents/from-file-analysis",
                json=body,
            )
            second = await client.post(
                f"/api/rag/knowledge_bases/{kb['id']}/documents/from-file-analysis",
                json=body,
            )
            assert first.status_code == second.status_code == 200
            assert first.json()["id"] == second.json()["id"]
            assert first.json()["analysis_artifact_id"] == "artifact_analysis"
            assert first.json()["ingestion_status"] == "pipeline_required"
            assert first.json()["chunk_count"] == 0
            assert first.json()["analysis_source"] == {
                "source_filename": "公开合成扫描样本.pdf",
                "source_sha256": "a" * 64,
                "selected_pages": [1, 3],
                "mode": "vision",
                "connection_name": "Exact connection",
                "model_id": "vendor/vision",
                "failed_pages": [],
                "truncated": False,
            }
            assert assets.resolve_calls == 1
            assert len(assets.uploaded) == 1
            assert b"%PDF" not in assets.uploaded[0]
            assert "第一页图表摘要" in assets.uploaded[0].decode("utf-8")

            assert service.vector_store.list_document_chunks(first.json()["id"]) == []
            assert service.lexical_store.list_document_chunks(first.json()["id"]) == []
    finally:
        set_rag_service_for_tests(None)


@pytest.mark.asyncio
async def test_file_analysis_import_rejects_deleting_knowledge_base_before_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assets = _AnalysisAssetService()
    monkeypatch.setattr(rag_service_module, "get_file_asset_service", lambda: assets)
    service = RagService(
        storage_dir=tmp_path / "storage",
        uploads_dir=tmp_path / "uploads",
        embedder=EmbeddingClient(api_key="", dimension=32),
        vector_store=LocalJsonVectorStore(tmp_path / "storage" / "vectors.json"),
        llm_enabled=False,
    )
    kb = service.create_knowledge_base("即将删除")
    with service._metadata_lock:  # noqa: SLF001 - deterministic race fixture
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        metadata["knowledge_bases"][kb["id"]]["deletion_status"] = "cleanup_pending"
        service._write_metadata_unlocked(metadata)  # noqa: SLF001
    with pytest.raises(KnowledgeBaseDeletionError, match="isolated"):
        await service.import_file_analysis(
            kb["id"],
            asset_id="asset_chat",
            analysis_artifact_id="artifact_analysis",
            chat_scope_id="chat-scope",
        )
    assert assets.resolve_calls == 0
    assert assets.uploaded == []
