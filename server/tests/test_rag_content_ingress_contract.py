from __future__ import annotations

import hashlib
import io
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import UploadFile

from server.file_assets.analysis import (
    FileAnalysisArtifact,
    FileAnalysisMode,
    FileAnalysisSection,
)
from server.rag import api as rag_api
from server.rag import rag_service as rag_service_module
from server.rag.rag_service import (
    KnowledgeBaseDeletionError,
    PipelineContentContractError,
    RagService,
)


class _ForbiddenSplitter:
    chunk_size = 500
    chunk_overlap = 50

    def split_text(self, _text: str) -> list[str]:
        raise AssertionError("content ingress must not split before a pipeline run")


class _ForbiddenEmbedder:
    async def embed_texts(self, _texts: list[str]) -> list[list[float]]:
        raise AssertionError("content ingress must not call the embedder")

    def embed_texts_locally(self, _texts: list[str]) -> list[list[float]]:
        raise AssertionError("content ingress must not call the local embedder")


class _ForbiddenVectorStore:
    def add_chunks(self, _chunks) -> None:
        raise AssertionError("content ingress must not write the vector index")

    def delete_document(self, _document_id: str) -> None:
        raise AssertionError("a source-only ingress must not clean an unwritten vector index")


class _ForbiddenLexicalStore:
    def add_chunks(self, _chunks) -> None:
        raise AssertionError("content ingress must not write the lexical index")

    def delete_document(self, _document_id: str) -> None:
        raise AssertionError("a source-only ingress must not clean an unwritten lexical index")


class _AssetService:
    mode = "native"

    def __init__(self) -> None:
        self.uploaded: list[bytes] = []
        self.deleted: list[str] = []
        self.after_upload: Callable[[], None] | None = None

    def upload(self, stream, **_kwargs):
        self.uploaded.append(stream.read())
        if self.after_upload is not None:
            self.after_upload()
        return SimpleNamespace(asset_id=f"asset_{len(self.uploaded)}")

    def delete_asset(self, asset_id: str, **_kwargs) -> bool:
        self.deleted.append(asset_id)
        return True


class _AnalysisAssetService(_AssetService):
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
        return FileAnalysisArtifact(
            asset_id=asset_id,
            source_filename="scan.pdf",
            source_sha256="a" * 64,
            format="pdf",
            mode=FileAnalysisMode.VISION,
            target_id="target_exact",
            connection_name="Exact connection",
            model_id="vendor/vision",
            selected_pages=(1, 3),
            sections=(
                FileAnalysisSection(kind="visual_summary", page=1, text="第一页摘要"),
                FileAnalysisSection(kind="ocr_text", page=3, text="第三页文字"),
            ),
            warnings=("analysis warning",),
            processed_pages=2,
            failed_pages=(),
            extracted_chars=10,
            truncated=False,
        )


class _OutputService:
    def read_output(self, output_id: str, *, purpose, scope_id: str):
        assert (output_id, purpose.value, scope_id) == (
            "output_public",
            "chat",
            "chat-scope",
        )
        return (
            SimpleNamespace(
                id=output_id,
                display_name="report.md",
                media_type="text/markdown",
                preview_kind="text",
                format_id="markdown",
                producer_kind="chat_tool",
                source_run_id="run-public",
                source_message_id="assistant-public",
                source_node_id=None,
            ),
            b"# Public report\n\nPipeline indexing is required.",
        )

    def get_output(self, *_args, **_kwargs):
        return SimpleNamespace(warnings=("output warning",))


def _service(tmp_path: Path) -> RagService:
    return RagService(
        storage_dir=tmp_path / "storage",
        uploads_dir=tmp_path / "uploads",
        embedder=_ForbiddenEmbedder(),
        vector_store=_ForbiddenVectorStore(),
        lexical_store=_ForbiddenLexicalStore(),
        splitter=_ForbiddenSplitter(),
        llm_enabled=False,
    )


@pytest.mark.asyncio
async def test_public_upload_endpoint_forces_pipeline_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    class _UploadService:
        async def upload_document(self, *args, **kwargs):
            calls.append({"args": args, "kwargs": kwargs})
            return {"ok": True}

    monkeypatch.setattr(rag_api, "get_rag_service", lambda: _UploadService())
    upload = UploadFile(filename="public.txt", file=io.BytesIO(b"public source"))

    assert await rag_api.upload_document("kb_public", upload) == {"ok": True}
    assert calls[0]["kwargs"]["pipeline_only"] is True


@pytest.mark.asyncio
async def test_upload_document_persists_only_source_and_pipeline_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assets = _AssetService()
    monkeypatch.setenv("FILE_ASSET_STORE_MODE", "native")
    monkeypatch.setattr(rag_service_module, "get_file_asset_service", lambda: assets)
    service = _service(tmp_path)
    kb = service.create_knowledge_base("source-only upload")
    content = b"This source must wait for an explicit pipeline run."

    payload = await service.upload_document(kb["id"], "source.txt", content)

    assert payload["ingestion_status"] == "pipeline_required"
    assert payload["chunk_count"] == 0
    metadata = service._read_metadata()  # noqa: SLF001 - ingress contract evidence
    document = metadata["documents"][payload["id"]]
    assert Path(document["stored_path"]).read_bytes() == content
    assert document["asset_id"] == "asset_1"
    assert document["content_hash"] == hashlib.sha256(content).hexdigest()
    assert assets.uploaded == [content]

    with pytest.raises(PipelineContentContractError) as blocked:
        await service.upload_document(
            kb["id"],
            "legacy-direct.txt",
            b"legacy direct indexing must stay read-only",
            pipeline_only=False,
        )
    assert blocked.value.code == "rag_content_contract_legacy_read_only"


@pytest.mark.asyncio
async def test_file_analysis_import_persists_only_derived_asset_and_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assets = _AnalysisAssetService()
    monkeypatch.setattr(rag_service_module, "get_file_asset_service", lambda: assets)
    service = _service(tmp_path)
    kb = service.create_knowledge_base("analysis source")

    payload = await service.import_file_analysis(
        kb["id"],
        asset_id="asset_chat",
        analysis_artifact_id="artifact_analysis",
        chat_scope_id="chat-scope",
    )

    assert payload["ingestion_status"] == "pipeline_required"
    assert payload["chunk_count"] == 0
    assert payload["analysis_artifact_id"] == "artifact_analysis"
    assert payload["analysis_source"] == {
        "source_filename": "scan.pdf",
        "source_sha256": "a" * 64,
        "selected_pages": [1, 3],
        "mode": "vision",
        "connection_name": "Exact connection",
        "model_id": "vendor/vision",
        "failed_pages": [],
        "truncated": False,
    }
    assert len(assets.uploaded) == 1
    assert "第一页摘要" in assets.uploaded[0].decode("utf-8")
    metadata = service._read_metadata()  # noqa: SLF001 - ingress contract evidence
    document = metadata["documents"][payload["id"]]
    assert Path(document["stored_path"]).read_bytes() == assets.uploaded[0]
    assert document["asset_id"] == "asset_1"


@pytest.mark.asyncio
async def test_file_output_import_persists_only_derived_asset_and_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outputs = _OutputService()
    assets = _AssetService()
    monkeypatch.setattr(rag_service_module, "get_file_output_service", lambda: outputs)
    monkeypatch.setattr(rag_service_module, "get_file_asset_service", lambda: assets)
    service = _service(tmp_path)
    kb = service.create_knowledge_base("output source")

    payload = await service.import_file_output(
        kb["id"],
        output_id="output_public",
        output_purpose="chat",
        output_scope_id="chat-scope",
    )

    assert payload["ingestion_status"] == "pipeline_required"
    assert payload["chunk_count"] == 0
    assert payload["file_output_id"] == "output_public"
    assert payload["file_output_source"]["producer_kind"] == "chat_tool"
    assert payload["file_output_source"]["sections"] == [
        {
            "page_number": None,
            "slide": None,
            "sheet": None,
            "line_range": "1-3",
            "row_range": None,
            "heading_path": [],
            "time_range": None,
        }
    ]
    expected = b"# Public report\n\nPipeline indexing is required."
    assert assets.uploaded == [expected]
    metadata = service._read_metadata()  # noqa: SLF001 - ingress contract evidence
    document = metadata["documents"][payload["id"]]
    assert Path(document["stored_path"]).read_bytes() == expected
    assert document["asset_id"] == "asset_1"


@pytest.mark.asyncio
@pytest.mark.parametrize("ingress_kind", ["analysis", "output"])
async def test_derived_import_deletion_race_cleans_local_and_asset_copies(
    ingress_kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    kb = service.create_knowledge_base("deletion race")
    assets: _AssetService
    if ingress_kind == "analysis":
        assets = _AnalysisAssetService()
    else:
        assets = _AssetService()
        monkeypatch.setattr(
            rag_service_module,
            "get_file_output_service",
            lambda: _OutputService(),
        )
    monkeypatch.setattr(rag_service_module, "get_file_asset_service", lambda: assets)

    def isolate_knowledge_base() -> None:
        with service._metadata_lock:  # noqa: SLF001 - deterministic race fixture
            metadata = service._read_metadata_unlocked()  # noqa: SLF001
            metadata["knowledge_bases"][kb["id"]]["deletion_status"] = (
                "cleanup_pending"
            )
            service._write_metadata_unlocked(metadata)  # noqa: SLF001

    assets.after_upload = isolate_knowledge_base
    with pytest.raises(KnowledgeBaseDeletionError, match="isolated"):
        if ingress_kind == "analysis":
            await service.import_file_analysis(
                kb["id"],
                asset_id="asset_chat",
                analysis_artifact_id="artifact_analysis",
                chat_scope_id="chat-scope",
            )
        else:
            await service.import_file_output(
                kb["id"],
                output_id="output_public",
                output_purpose="chat",
                output_scope_id="chat-scope",
            )

    assert assets.deleted == ["asset_1"]
    assert list((tmp_path / "uploads" / kb["id"]).iterdir()) == []
    assert service._read_metadata()["documents"] == {}  # noqa: SLF001
