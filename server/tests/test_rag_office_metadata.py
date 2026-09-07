from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

import httpx
import pytest

from server.file_assets.document_parser import (
    LocalDocumentParseError,
    ParsedDocument,
    ParsedSection,
)
from server.main import app
from server.rag import document_processor as processor_module
from server.rag import document_parser as parser_module
from server.rag import rag_service as service_module
from server.rag.api import (
    CitationAnchorPayload,
    DocumentPayload,
    RagSourcePayload,
    set_rag_service_for_tests,
)
from server.rag.document_parser import DocumentParseError
from server.rag.document_processor import (
    DocumentBlock,
    ProcessedDocument,
    StructuredDocumentProcessor,
)
from server.rag.embedder import EmbeddingClient
from server.rag.lexical_store import LexicalChunk, SqliteLexicalStore
from server.rag.pipeline_executor import KnowledgePipelineExecutor
from server.rag.rag_service import RagService
from server.rag.source_metadata import (
    MAX_HEADING_PATH_CHARS,
    MAX_HEADING_PATH_LEVELS,
    MAX_HEADING_SEGMENT_CHARS,
    normalize_heading_path,
)
from server.rag.vector_store import ChromaVectorStore, LocalJsonVectorStore, VectorChunk


def _pptx_document(*, slide: int = 3) -> ParsedDocument:
    return ParsedDocument(
        format="pptx",
        title="季度回顾",
        sections=(
            ParsedSection(
                text="收入同比增长 42%。",
                slide=slide,
                line_range="1-1",
                heading_path=("季度回顾",),
            ),
        ),
        warnings=("图片仅保留占位符。",),
        extracted_chars=11,
    )


def _service(tmp_path: Path, *, processor: Any | None = None) -> RagService:
    storage = tmp_path / "storage"
    return RagService(
        storage_dir=storage,
        uploads_dir=tmp_path / "uploads",
        embedder=EmbeddingClient(api_key="", dimension=64),
        vector_store=LocalJsonVectorStore(storage / "vectors.json"),
        lexical_store=SqliteLexicalStore(storage / "lexical.sqlite3"),
        document_processor=processor,
        llm_enabled=False,
    )


async def _build_vector_candidate(
    service: RagService,
    kb_id: str,
    document_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    draft = service.update_pipeline_draft(
        kb_id,
        {},
        retrieval_profile={"mode": "vector"},
    )
    job = service.create_pipeline_job(
        kb_id,
        draft_version=int(draft["version"]),
        source_document_ids=[document_id],
    )
    assert await KnowledgePipelineExecutor(service).run_once() is True
    completed = service.get_pipeline_job(job["job_id"])
    assert completed["status"] == "succeeded"
    version = service.get_pipeline_version(str(completed["candidate_version_id"]))
    return completed, version


def _mark_pipeline_version_as_previously_active(
    service: RagService,
    version_id: str,
) -> None:
    """Model a historical active index without authorizing a new activation."""

    with service._metadata_lock:  # noqa: SLF001 - historical compatibility fixture.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        version = metadata["pipeline_versions"][version_id]
        kb_id = str(version["kb_id"])
        version["status"] = "active"
        version["activated_at"] = 1.0
        metadata["pipeline_active_versions"][kb_id] = version_id
        service._write_metadata_unlocked(metadata)  # noqa: SLF001


def _allow_mock_office(
    monkeypatch: pytest.MonkeyPatch,
    parsed: ParsedDocument,
) -> None:
    monkeypatch.setattr(service_module, "supported_extensions", lambda: {".pptx", ".docx"})
    monkeypatch.setattr(
        service_module.FileUploadValidator,
        "validate_stream",
        lambda self, stream, **kwargs: None,
    )
    monkeypatch.setattr(service_module, "parse_document_structured", lambda *args: parsed)
    monkeypatch.setattr(processor_module, "parse_document_structured", lambda *args: parsed)


def test_structured_office_blocks_keep_real_slide_and_heading_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "review.pptx"
    source.write_bytes(b"mock-office-container")
    monkeypatch.setattr(
        processor_module,
        "parse_document_structured",
        lambda *args: _pptx_document(slide=7),
    )

    result = StructuredDocumentProcessor().process(
        source,
        filename="review.pptx",
        source_id="doc-office",
    )

    assert result.warnings == ["图片仅保留占位符。"]
    assert result.blocks[0].heading_path == ["季度回顾"]
    assert result.blocks[0].metadata == {
        "slide": 7,
        "line_range": "1-1",
        "heading_path": ["季度回顾"],
    }
    assert result.blocks[0].page_number is None


@pytest.mark.asyncio
async def test_office_source_only_upload_requires_vector_candidate_for_slide_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_mock_office(monkeypatch, _pptx_document(slide=3))
    service = _service(tmp_path)
    kb = service.create_knowledge_base("Office direct upload")

    document = await service.upload_document(
        kb["id"],
        "review.pptx",
        b"mock",
        pipeline_only=True,
    )
    assert document["ingestion_status"] == "pipeline_required"
    assert document["chunk_count"] == 0
    assert service.vector_store.list_document_chunks(document["id"]) == []
    assert service.list_pipeline_artifact_chunks(f"artifact_{document['id']}") == []

    _, version = await _build_vector_candidate(service, kb["id"], document["id"])
    stored = service.vector_store.list_document_chunks(
        f"{version['version_id']}_{document['id']}"
    )

    assert len(stored) == 1
    assert stored[0].slide == 3
    assert stored[0].page_number is None
    assert stored[0].heading_path == ("季度回顾",)
    assert stored[0].text.startswith("季度回顾\n")

    _mark_pipeline_version_as_previously_active(service, version["version_id"])
    result = await service.query(kb["id"], "收入增长", top_k=1)
    source = RagSourcePayload.model_validate(result["sources"][0])
    assert source.slide == 3
    assert source.page_number is None
    assert source.heading_path == ["季度回顾"]

    citations = await service.create_pipeline_citations(kb["id"], "收入增长", top_k=1)
    citation = CitationAnchorPayload.model_validate(citations[0])
    assert citation.slide == 3
    assert citation.page_number is None
    assert citation.heading_path == ["季度回顾"]


@pytest.mark.asyncio
async def test_office_pipeline_parsing_does_not_block_event_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed = _pptx_document()
    _allow_mock_office(monkeypatch, parsed)
    entered = threading.Event()
    release = threading.Event()

    def blocking_parse(*args: Any, **kwargs: Any) -> ParsedDocument:
        entered.set()
        if not release.wait(0.5):
            raise AssertionError("event loop could not release the Office parser")
        return parsed

    monkeypatch.setattr(service_module, "parse_document_structured", blocking_parse)
    monkeypatch.setattr(processor_module, "parse_document_structured", blocking_parse)
    service = _service(tmp_path)
    kb = service.create_knowledge_base("Office heartbeat")
    document = await service.upload_document(
        kb["id"],
        "heartbeat.pptx",
        b"mock",
        pipeline_only=True,
    )
    assert entered.is_set() is False
    draft = service.update_pipeline_draft(
        kb["id"],
        {},
        retrieval_profile={"mode": "vector"},
    )
    service.create_pipeline_job(
        kb["id"],
        draft_version=int(draft["version"]),
        source_document_ids=[document["id"]],
    )
    asyncio.get_running_loop().call_later(0.05, release.set)

    assert await asyncio.wait_for(
        KnowledgePipelineExecutor(service).run_once(),
        timeout=1.0,
    ) is True

    assert entered.is_set()
    assert document["chunk_count"] == 0
    assert service.list_pipeline_versions(kb["id"])[0]["chunk_count"] == 1


@pytest.mark.asyncio
async def test_office_warnings_are_bounded_persisted_and_returned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warnings = (
        "  Tracked revisions may be incomplete.\n",
        "Tracked revisions may be incomplete.",
        "api_key=top-secret must not survive",
        *(f"warning-{index}-" + ("x" * 700) for index in range(30)),
    )
    parsed = _pptx_document().model_copy(update={"warnings": warnings})
    _allow_mock_office(monkeypatch, parsed)
    service = _service(tmp_path)
    kb = service.create_knowledge_base("Office warnings")

    uploaded = await service.upload_document(
        kb["id"],
        "warnings.pptx",
        b"mock",
        pipeline_only=True,
    )
    completed, _ = await _build_vector_candidate(service, kb["id"], uploaded["id"])
    listed = service.list_documents(kb["id"])[0]
    persisted = json.loads(service.metadata_path.read_text(encoding="utf-8"))[
        "documents"
    ][uploaded["id"]]["warnings"]
    pipeline_warnings = completed["document_results"][0]["warnings"]

    assert uploaded["warnings"] == listed["warnings"] == persisted == []
    assert DocumentPayload.model_validate(uploaded).warnings == []
    assert len(pipeline_warnings) <= service_module.MAX_DOCUMENT_WARNINGS
    assert all(
        len(item) <= service_module.MAX_DOCUMENT_WARNING_CHARACTERS
        for item in pipeline_warnings
    )
    assert sum(map(len, pipeline_warnings)) <= service_module.MAX_DOCUMENT_WARNINGS_CHARACTERS
    assert all("\n" not in item and "\x00" not in item for item in pipeline_warnings)
    assert all("top-secret" not in item for item in pipeline_warnings)


def test_rag_parser_preserves_local_parser_status_and_error_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "review.pptx"
    source.write_bytes(b"mock")
    monkeypatch.setattr(
        parser_module.FileUploadValidator,
        "validate_path",
        lambda self, *args, **kwargs: type("Validated", (), {"format_id": "pptx"})(),
    )

    def unavailable(*args: Any, **kwargs: Any) -> ParsedDocument:
        raise LocalDocumentParseError(
            "office_parser_unavailable",
            "Office 隔离解析暂不可用，请稍后重试。",
            status_code=503,
        )

    monkeypatch.setattr(parser_module, "parse_chat_document", unavailable)

    with pytest.raises(DocumentParseError) as captured:
        parser_module.parse_document_structured(source, "review.pptx")

    assert captured.value.status_code == 503
    assert captured.value.error_code == "office_parser_unavailable"
    assert "隔离解析暂不可用" in captured.value.message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "error_code"),
    (
        (422, "office_parse_failed"),
        (503, "office_parser_unavailable"),
    ),
)
async def test_rag_pipeline_surfaces_office_parser_failure_after_source_only_upload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    error_code: str,
) -> None:
    _allow_mock_office(monkeypatch, _pptx_document())

    def fail_parse(*args: Any, **kwargs: Any) -> ParsedDocument:
        raise DocumentParseError(
            "Office 文件无法安全解析。",
            error_code=error_code,
            status_code=status_code,
        )

    monkeypatch.setattr(service_module, "parse_document_structured", fail_parse)
    monkeypatch.setattr(processor_module, "parse_document_structured", fail_parse)
    service = _service(tmp_path)
    kb = service.create_knowledge_base("Office API errors")
    set_rag_service_for_tests(service)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            response = await client.post(
                f"/api/rag/knowledge_bases/{kb['id']}/documents",
                files={
                    "file": (
                        "review.pptx",
                        b"mock",
                        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                    )
                },
            )
            listed = await client.get(
                f"/api/rag/knowledge_bases/{kb['id']}/documents"
            )
            configured = await client.patch(
                f"/api/rag/pipeline/draft/{kb['id']}",
                json={"retrieval_profile": {"mode": "vector"}},
            )
            assert configured.status_code == 200, configured.text
            queued = await client.post(
                f"/api/rag/pipeline/draft/{kb['id']}/execute",
                json={
                    "draft_version": configured.json()["version"],
                    "source_document_ids": [response.json()["id"]],
                },
            )
            assert queued.status_code == 200, queued.text
            assert await KnowledgePipelineExecutor(service).run_once() is True
            failed = service.get_pipeline_job(queued.json()["job_id"])
    finally:
        set_rag_service_for_tests(None)

    assert response.status_code == 200
    assert response.json()["ingestion_status"] == "pipeline_required"
    assert response.json()["chunk_count"] == 0
    assert listed.status_code == 200
    assert len(listed.json()["documents"]) == 1
    assert failed["status"] == "failed"
    assert failed["error"] == "All source documents failed during processing."
    assert failed["document_results"][0]["status"] == "failed"
    assert failed["document_results"][0]["error"] == "Office 文件无法安全解析。"
    assert service.list_pipeline_versions(kb["id"]) == []
    assert service.get_active_pipeline_version(kb["id"]) is None
    assert any(path.is_file() for path in (tmp_path / "uploads").rglob("*"))


class _OfficePipelineProcessor:
    def process(self, path: Path, **kwargs: Any) -> ProcessedDocument:
        text = "发布摘要\n代号 AURORA-42 已批准。"
        block = DocumentBlock(
            block_id="block-slide-4",
            kind="paragraph",
            text=text,
            start_char=0,
            end_char=len(text),
            heading_path=["发布摘要"],
            page_number=None,
            metadata={
                "slide": 4,
                "heading_path": ["发布摘要"],
                "line_range": "1-2",
            },
        )
        return ProcessedDocument(
            source_id=str(kwargs["source_id"]),
            filename=str(kwargs["filename"]),
            title="发布摘要",
            text=text,
            blocks=[block],
        )


@pytest.mark.asyncio
async def test_pipeline_propagates_slide_to_vector_and_api_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_mock_office(monkeypatch, _pptx_document(slide=4))
    service = _service(tmp_path, processor=_OfficePipelineProcessor())
    kb = service.create_knowledge_base("Office pipeline")
    document = await service.upload_document(
        kb["id"],
        "release.pptx",
        b"mock",
        pipeline_only=True,
    )
    draft = service.update_pipeline_draft(
        kb["id"],
        {},
        retrieval_profile={"mode": "vector"},
    )
    job = service.create_pipeline_job(
        kb["id"],
        draft_version=int(draft["version"]),
        source_document_ids=[document["id"]],
    )

    assert await KnowledgePipelineExecutor(service).run_once() is True
    completed = service.get_pipeline_job(job["job_id"])
    version = service.get_pipeline_version(str(completed["candidate_version_id"]))
    vector_chunks = service.vector_store.list_document_chunks(
        f"{version['version_id']}_{document['id']}"
    )
    assert vector_chunks[0].slide == 4
    assert vector_chunks[0].page_number is None
    assert vector_chunks[0].heading_path == ("发布摘要",)

    assert service.lexical_store.count_namespace(version["namespace"]) == 0

    result = await service.query_pipeline_version(
        version["version_id"],
        "AURORA-42",
        top_k=1,
        retrieval={"mode": "vector"},
    )
    source = RagSourcePayload.model_validate(result["sources"][0])
    assert source.slide == 4
    assert source.page_number is None
    assert source.heading_path == ["发布摘要"]


def test_lexical_store_migrates_slide_without_breaking_legacy_rows(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE rag_chunks (
                chunk_id TEXT PRIMARY KEY,
                namespace TEXT NOT NULL,
                doc_id TEXT NOT NULL,
                document_name TEXT NOT NULL,
                text TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                parent_chunk_id TEXT,
                parent_text TEXT,
                chunk_type TEXT NOT NULL,
                start_char INTEGER NOT NULL,
                end_char INTEGER NOT NULL
            )
            """
        )

    store = SqliteLexicalStore(path)
    with sqlite3.connect(path) as connection:
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(rag_chunks)")}
    assert {"slide", "heading_path_json"}.issubset(columns)

    store.add_chunks(
        [
            LexicalChunk(
                chunk_id="legacy-compatible",
                namespace="office",
                doc_id="doc",
                document_name="review.pptx",
                text="历史索引兼容，新片段来自第五张幻灯片。",
                chunk_index=0,
                slide=5,
                heading_path=("季度", "发布"),
            )
        ]
    )
    result = store.query("office", "第五张幻灯片", 1)[0]
    assert result.slide == 5
    assert result.page_number is None
    assert result.heading_path == ("季度", "发布")


def test_heading_path_is_bounded_before_storage_or_api_serialization() -> None:
    raw = [f" {index}-" + ("x" * 500) for index in range(30)]
    normalized = normalize_heading_path(raw)

    assert len(normalized) <= MAX_HEADING_PATH_LEVELS
    assert all(len(item) <= MAX_HEADING_SEGMENT_CHARS for item in normalized)
    assert sum(len(item) for item in normalized) <= MAX_HEADING_PATH_CHARS

    payload = RagSourcePayload.model_validate(
        {
            "chunk_id": "chunk",
            "doc_id": "doc",
            "document_name": "bounded.pptx",
            "text": "content",
            "score": 1.0,
            "heading_path": raw,
        }
    )
    assert tuple(payload.heading_path) == normalized


class _FakeChromaCollection:
    def __init__(self) -> None:
        self.metadata: dict[str, Any] = {}

    def upsert(self, **kwargs: Any) -> None:
        self.metadata = dict(kwargs["metadatas"][0])

    def query(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "ids": [["chunk"]],
            "documents": [["content"]],
            "metadatas": [[self.metadata]],
            "distances": [[0.0]],
        }


def test_chroma_heading_path_metadata_round_trips_as_bounded_json() -> None:
    collection = _FakeChromaCollection()
    store = object.__new__(ChromaVectorStore)
    store._collection = collection
    raw = tuple("x" * 500 for _ in range(20))
    store.add_chunks(
        [
            VectorChunk(
                id="chunk",
                kb_id="kb",
                doc_id="doc",
                document_name="review.pptx",
                text="content",
                embedding=[1.0],
                chunk_index=0,
                heading_path=raw,
            )
        ]
    )

    assert "heading_path_json" in collection.metadata
    result = store.query("kb", [1.0], 1)[0]
    assert result.heading_path == normalize_heading_path(raw)


def test_legacy_local_vector_without_heading_path_falls_back_to_empty(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy-vectors.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": "legacy-chunk",
                    "kb_id": "kb",
                    "doc_id": "doc",
                    "document_name": "legacy.txt",
                    "text": "legacy content",
                    "embedding": [1.0],
                    "chunk_index": 0,
                }
            ]
        ),
        encoding="utf-8",
    )

    chunk = LocalJsonVectorStore(path).list_document_chunks("doc")[0]
    assert chunk.heading_path == ()
