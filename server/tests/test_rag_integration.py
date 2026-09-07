from __future__ import annotations

import io
import zipfile
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from openpyxl import Workbook

from server.main import app
from server.rag.api import (
    set_pipeline_executor_for_tests,
    set_rag_service_for_tests,
)
from server.rag.embedder import EmbeddingClient
from server.rag.pipeline_executor import KnowledgePipelineExecutor
from server.rag.rag_service import RagService
from server.rag.vector_store import LocalJsonVectorStore


@pytest_asyncio.fixture
async def client(tmp_path: Path):
    service = RagService(
        storage_dir=tmp_path / "storage",
        uploads_dir=tmp_path / "uploads",
        embedder=EmbeddingClient(api_key="", dimension=128),
        vector_store=LocalJsonVectorStore(tmp_path / "storage" / "vectors.json"),
        llm_enabled=False,
    )
    set_rag_service_for_tests(service)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client
    set_rag_service_for_tests(None)


@pytest_asyncio.fixture
async def pipeline_runtime(tmp_path: Path):
    service = RagService(
        storage_dir=tmp_path / "storage",
        uploads_dir=tmp_path / "uploads",
        embedder=EmbeddingClient(api_key="", dimension=128),
        vector_store=LocalJsonVectorStore(tmp_path / "storage" / "vectors.json"),
        llm_enabled=False,
    )
    executor = KnowledgePipelineExecutor(service, poll_interval=0.01)
    set_rag_service_for_tests(service)
    set_pipeline_executor_for_tests(executor)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client, service, executor
    set_pipeline_executor_for_tests(None)
    set_rag_service_for_tests(None)


async def create_kb(client: httpx.AsyncClient, name: str = "测试知识库") -> str:
    response = await client.post("/api/rag/knowledge_bases", json={"name": name})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _xlsx_bytes() -> bytes:
    workbook = Workbook()
    sales = workbook.active
    sales.title = "销售数据"
    sales.append(["城市", "数量"])
    sales.append(["上海", 42])
    notes = workbook.create_sheet("说明")
    notes.append(["口径", "已确认订单"])
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def _unsafe_xlsx(kind: str) -> bytes:
    source_bytes = _xlsx_bytes()
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(source_bytes), "r") as source, zipfile.ZipFile(
        output, "w", zipfile.ZIP_DEFLATED
    ) as target:
        external_rewritten = False
        for entry in source.infolist():
            content = source.read(entry.filename)
            if kind == "external" and entry.filename == "xl/_rels/workbook.xml.rels":
                relationship = (
                    b'<Relationship Id="external" Type="urn:modelmirror:test" '
                    b'Target="https://example.invalid/book.xlsx" TargetMode="External"/>'
                )
                updated = content.replace(
                    b"</Relationships>",
                    relationship + b"</Relationships>",
                )
                assert updated != content
                content = updated
                external_rewritten = True
            target.writestr(entry, content)
        if kind == "macro":
            target.writestr("xl/vbaProject.bin", b"must-not-be-persisted")
        elif kind == "bomb":
            target.writestr("xl/media/compression-bomb.bin", b"A" * (4 * 1024 * 1024))
        elif kind == "external":
            assert external_rewritten
        else:  # pragma: no cover - fixture guard
            raise ValueError(kind)
    return output.getvalue()


async def _build_vector_candidate(
    client: httpx.AsyncClient,
    executor: KnowledgePipelineExecutor,
    kb_id: str,
    document_id: str,
) -> dict:
    configured = await client.patch(
        f"/api/rag/pipeline/draft/{kb_id}",
        json={"retrieval_profile": {"mode": "vector"}},
    )
    assert configured.status_code == 200, configured.text
    queued = await client.post(
        f"/api/rag/pipeline/draft/{kb_id}/execute",
        json={
            "draft_version": configured.json()["version"],
            "source_document_ids": [document_id],
        },
    )
    assert queued.status_code == 200, queued.text
    assert await executor.run_once() is True
    completed = await client.get(
        f"/api/rag/pipeline/jobs/{queued.json()['job_id']}"
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "succeeded"
    return completed.json()


@pytest.mark.asyncio
async def test_create_upload_pipeline_query_and_cleanup(pipeline_runtime) -> None:
    client, service, executor = pipeline_runtime
    kb_id = await create_kb(client)

    upload_response = await client.post(
        f"/api/rag/knowledge_bases/{kb_id}/documents",
        files={
            "file": (
                "测试文档.txt",
                "模镜是一个AI平台。它支持多种模型，包括 OpenAI 和 Anthropic。",
                "text/plain",
            )
        },
    )
    assert upload_response.status_code == 200, upload_response.text
    document = upload_response.json()
    assert document["chunk_count"] == 0
    assert document["ingestion_status"] == "pipeline_required"
    completed = await _build_vector_candidate(client, executor, kb_id, document["id"])
    version_id = str(completed["candidate_version_id"])
    version = service.get_pipeline_version(version_id)
    assert service.get_active_pipeline_version(kb_id) is None

    query_response = await client.post(
        f"/api/rag/pipeline/versions/{version_id}/query",
        json={"question": "什么是模镜？", "top_k": 5},
    )
    assert query_response.status_code == 200, query_response.text
    data = query_response.json()
    assert data["sources"]
    assert data["sources"][0]["document_name"] == "测试文档.txt"
    assert "AI平台" in data["sources"][0]["matched_text"]

    delete_doc_response = await client.delete(f"/api/rag/documents/{document['id']}")
    assert delete_doc_response.status_code == 200

    list_docs_response = await client.get(f"/api/rag/knowledge_bases/{kb_id}/documents")
    assert list_docs_response.status_code == 200
    assert list_docs_response.json()["documents"] == []

    delete_kb_response = await client.delete(f"/api/rag/knowledge_bases/{kb_id}")
    assert delete_kb_response.status_code == 200
    assert service.vector_store.count_namespace(version["namespace"]) == 0


@pytest.mark.asyncio
async def test_xlsx_upload_persists_sheet_and_cell_range_in_real_index(
    pipeline_runtime,
) -> None:
    client, service, executor = pipeline_runtime
    kb_id = await create_kb(client, "XLSX 来源元数据")
    upload_response = await client.post(
        f"/api/rag/knowledge_bases/{kb_id}/documents",
        files={
            "file": (
                "销售.xlsx",
                _xlsx_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert upload_response.status_code == 200, upload_response.text
    document = upload_response.json()
    assert document["chunk_count"] == 0
    assert document["ingestion_status"] == "pipeline_required"
    completed = await _build_vector_candidate(client, executor, kb_id, document["id"])
    version_id = str(completed["candidate_version_id"])
    assert service.get_active_pipeline_version(kb_id) is None

    query_response = await client.post(
        f"/api/rag/pipeline/versions/{version_id}/query",
        json={"question": "上海的数量是多少？", "top_k": 10},
    )
    assert query_response.status_code == 200, query_response.text
    sources = query_response.json()["sources"]
    indexed = next(
        item
        for item in sources
        if item["sheet"] == "销售数据" and "上海" in item["matched_text"]
    )
    assert indexed["row_range"] == "A1:B2"
    assert indexed["chunk_type"] == "table"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "expected_code"),
    (
        ("macro", "unsupported_xlsx_feature"),
        ("external", "unsupported_xlsx_feature"),
        ("bomb", "xlsx_complexity_limit_exceeded"),
    ),
)
async def test_rag_api_rejects_unsafe_xlsx_before_persistence(
    client: httpx.AsyncClient,
    tmp_path: Path,
    kind: str,
    expected_code: str,
) -> None:
    kb_id = await create_kb(client, f"unsafe xlsx {kind}")
    response = await client.post(
        f"/api/rag/knowledge_bases/{kb_id}/documents",
        files={
            "file": (
                "unsafe.xlsx",
                _unsafe_xlsx(kind),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == expected_code

    documents = await client.get(f"/api/rag/knowledge_bases/{kb_id}/documents")
    assert documents.status_code == 200
    assert documents.json()["documents"] == []
    artifacts = await client.get(f"/api/rag/pipeline/artifacts?kb_id={kb_id}")
    assert artifacts.status_code == 200
    assert artifacts.json()["artifacts"] == []
    uploads = tmp_path / "uploads"
    assert not uploads.exists() or not any(path.is_file() for path in uploads.rglob("*"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "content_type"),
    (
        ("notes.txt", "text/plain; charset=utf-8"),
        ("notes.md", "text/markdown; charset=UTF-8"),
    ),
)
async def test_upload_accepts_mime_parameters(
    client: httpx.AsyncClient,
    filename: str,
    content_type: str,
) -> None:
    kb_id = await create_kb(client)
    response = await client.post(
        f"/api/rag/knowledge_bases/{kb_id}/documents",
        files={"file": (filename, b"ModelMirror file input", content_type)},
    )

    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_rag_api_rejects_mime_mismatch_before_persistence(
    client: httpx.AsyncClient,
    tmp_path: Path,
) -> None:
    kb_id = await create_kb(client, "MIME mismatch")
    response = await client.post(
        f"/api/rag/knowledge_bases/{kb_id}/documents",
        files={"file": ("notes.txt", b"must not be persisted", "application/pdf")},
    )

    assert response.status_code == 415, response.text
    assert response.json()["detail"]["code"] == "mime_type_mismatch"
    documents = await client.get(f"/api/rag/knowledge_bases/{kb_id}/documents")
    assert documents.status_code == 200
    assert documents.json()["documents"] == []
    artifacts = await client.get(f"/api/rag/pipeline/artifacts?kb_id={kb_id}")
    assert artifacts.status_code == 200
    assert artifacts.json()["artifacts"] == []
    uploads = tmp_path / "uploads"
    assert not uploads.exists() or not any(path.is_file() for path in uploads.rglob("*"))


@pytest.mark.asyncio
async def test_query_after_delete_returns_404(client: httpx.AsyncClient) -> None:
    kb_id = await create_kb(client, "待删除知识库")
    delete_response = await client.delete(f"/api/rag/knowledge_bases/{kb_id}")
    assert delete_response.status_code == 200

    query_response = await client.post(
        "/api/rag/query",
        json={"kb_id": kb_id, "question": "还有内容吗？"},
    )
    assert query_response.status_code == 404


@pytest.mark.asyncio
async def test_unsupported_file_type_returns_400(client: httpx.AsyncClient) -> None:
    kb_id = await create_kb(client, "格式测试")
    response = await client.post(
        f"/api/rag/knowledge_bases/{kb_id}/documents",
        files={"file": ("bad.exe", b"not a document", "application/octet-stream")},
    )
    assert response.status_code == 400
    assert "暂不支持" in response.text


@pytest.mark.asyncio
async def test_empty_knowledge_base_query_returns_hint(client: httpx.AsyncClient) -> None:
    kb_id = await create_kb(client, "空知识库")
    response = await client.post(
        "/api/rag/query",
        json={"kb_id": kb_id, "question": "这里有什么？"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["sources"] == []
    assert "没有" in data["answer"]
