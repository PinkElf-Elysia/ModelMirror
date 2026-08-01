from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from server.rag.embedder import EmbeddingClient
from server.rag.lexical_store import LexicalChunk, SqliteLexicalStore, tokenize_for_search
from server.rag.pipeline_executor import KnowledgePipelineExecutor
from server.rag.rag_service import RagService
from server.rag.reranker import RerankDocument, RerankService
from server.rag.retrieval import RetrievalConfig
from server.rag.splitter import ParentChildTextSplitter, TextSplitter
from server.rag.vector_store import LocalJsonVectorStore


def build_service(tmp_path: Path, *, reranker=None) -> RagService:
    storage = tmp_path / "storage"
    return RagService(
        storage_dir=storage,
        uploads_dir=tmp_path / "uploads",
        embedder=EmbeddingClient(api_key="", dimension=64),
        vector_store=LocalJsonVectorStore(storage / "vectors.json"),
        lexical_store=SqliteLexicalStore(storage / "lexical.sqlite3"),
        reranker=reranker,
        llm_enabled=False,
    )


def test_recursive_and_parent_child_splitters_preserve_offsets() -> None:
    text = "第一部分：部署规则。\n\n第二部分：蓝鲸计划需要人工批准。" * 12
    recursive = TextSplitter(120, 20, ["\n\n", "。", ""])
    segments = recursive.split_segments(text)
    assert len(segments) > 1
    assert all(text[item.start_char : item.end_char] == item.text for item in segments)

    parent_child = ParentChildTextSplitter(
        parent_chunk_size=300,
        parent_chunk_overlap=20,
        child_chunk_size=120,
        child_chunk_overlap=10,
        parent_separators=["\n\n", "。", ""],
        child_separators=["。", ""],
    )
    children = parent_child.split_segments(text)
    assert children
    assert all(item.chunk_type == "child" for item in children)
    assert all(item.parent_chunk_id and item.parent_text for item in children)
    assert all(text[item.start_char : item.end_char] == item.text for item in children)


def test_sqlite_fts5_indexes_mixed_chinese_and_english(tmp_path: Path) -> None:
    store = SqliteLexicalStore(tmp_path / "lexical.sqlite3")
    store.add_chunks(
        [
            LexicalChunk(
                chunk_id="c1",
                namespace="kb-v2",
                doc_id="d1",
                document_name="guide.txt",
                text="蓝鲸计划 uses manual approval before production deployment.",
                chunk_index=0,
            )
        ]
    )
    assert "蓝鲸" in tokenize_for_search("蓝鲸计划")
    assert store.query("kb-v2", "蓝鲸", 5)[0].chunk_id == "c1"
    assert store.query("kb-v2", "production deployment", 5)[0].document_name == "guide.txt"
    assert store.query("other", "蓝鲸", 5) == []


@pytest.mark.asyncio
async def test_v2_candidate_builds_dual_index_and_lifts_parent_context(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    kb = service.create_knowledge_base("advanced retrieval")
    text = (
        "部署手册第一章介绍环境准备。\n\n"
        "蓝鲸发布口令是 CELESTIAL-ORCA，生产部署必须经过人工批准。\n\n"
        "最后一章介绍回滚和审计。"
    ) * 8
    document = await service.upload_document(kb["id"], "manual.txt", text.encode("utf-8"))
    draft = service.update_pipeline_draft(
        kb["id"],
        {
            "stage_chunker": {
                "config": {
                    "strategy": "parent_child",
                    "parent_chunk_size": 500,
                    "parent_chunk_overlap": 50,
                    "child_chunk_size": 160,
                    "child_chunk_overlap": 20,
                    "parent_separators": ["\n\n", "。", ""],
                    "child_separators": ["。", ""],
                }
            }
        },
        retrieval_profile={"mode": "hybrid", "top_k": 3},
    )
    job = service.create_pipeline_job(
        kb["id"],
        draft_version=draft["version"],
        source_document_ids=[document["id"]],
    )
    executor = KnowledgePipelineExecutor(service)
    assert await executor.run_once() is True
    completed = service.get_pipeline_job(job["job_id"])
    version = service.get_pipeline_version(completed["candidate_version_id"])
    assert version["index_schema_version"] == 2
    assert version["vector_index_ready"] is True
    assert version["lexical_index_ready"] is True
    assert service.lexical_store.count_namespace(version["namespace"]) == version["chunk_count"]

    result = await service.query_pipeline_version(
        version["version_id"],
        "CELESTIAL-ORCA 发布口令",
        retrieval={"mode": "hybrid", "top_k": 3},
    )
    assert result["sources"]
    source = result["sources"][0]
    assert source["parent_lifted"] is True
    assert "CELESTIAL-ORCA" in source["matched_text"]
    assert len(source["text"]) >= len(source["matched_text"])
    exact = service.vector_store.get_chunk(version["namespace"], source["chunk_id"])
    assert exact is not None
    assert exact.chunk_id == source["chunk_id"]
    assert service.vector_store.get_chunk("other-namespace", source["chunk_id"]) is None
    assert result["retrieval"]["vector_candidate_count"] > 0
    assert result["retrieval"]["fulltext_candidate_count"] > 0


def test_retrieval_config_validates_weights_and_limits() -> None:
    config = RetrievalConfig.from_mapping(
        {"mode": "hybrid", "vector_weight": 0.7, "fulltext_weight": 0.3, "top_k": 10}
    )
    assert config.vector_weight == 0.7
    assert config.fulltext_weight == 0.3
    with pytest.raises(ValueError):
        RetrievalConfig.from_mapping({"top_k": 51})
    with pytest.raises(ValueError):
        RetrievalConfig.from_mapping({"score_threshold": 1.1})


@pytest.mark.asyncio
async def test_dedicated_rerank_api_and_llm_json_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    documents = [
        RerankDocument("a", "alpha"),
        RerankDocument("b", "beta"),
    ]
    service = RerankService()

    monkeypatch.setenv("RERANK_API_URL", "https://rerank.test/v1/rerank")
    monkeypatch.setenv("RERANK_API_KEY", "test-key")
    monkeypatch.setenv("RERANK_MODEL", "test-reranker")

    async def api_post(self, url, **kwargs):
        return httpx.Response(
            200,
            json={"results": [{"index": 1, "relevance_score": 0.98}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", api_post)
    api_outcome = await service.rerank("beta", documents, provider="api", top_n=1)
    assert api_outcome.provider == "api"
    assert api_outcome.items[0].chunk_id == "b"

    monkeypatch.delenv("RERANK_API_KEY")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("RAG_RERANK_LLM_MODEL", "test-llm")

    async def llm_post(self, url, **kwargs):
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": '{"results":[{"index":0,"score":0.91}]}'}}
                ]
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", llm_post)
    llm_outcome = await service.rerank("alpha", documents, provider="auto", top_n=1)
    assert llm_outcome.provider == "llm"
    assert llm_outcome.items[0].chunk_id == "a"


@pytest.mark.asyncio
async def test_llm_rerank_preserves_complete_gateway_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    documents = [RerankDocument("a", "alpha")]
    service = RerankService()
    calls: list[str] = []

    monkeypatch.setenv(
        "LLM_GATEWAY_URL",
        "http://gateway.test/v1/chat/completions",
    )
    monkeypatch.setenv("LLM_GATEWAY_KEY", "gateway-key")
    monkeypatch.setenv("RAG_RERANK_LLM_MODEL", "test-llm")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    async def post(self, url, **kwargs):
        calls.append(url)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": '{"results":[{"index":0,"score":0.9}]}'}}
                ]
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    outcome = await service.rerank("alpha", documents, provider="llm", top_n=1)

    assert outcome.provider == "llm"
    assert calls == ["http://gateway.test/v1/chat/completions"]


@pytest.mark.asyncio
async def test_llm_rerank_falls_back_from_gateway_to_openrouter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    documents = [RerankDocument("a", "alpha")]
    service = RerankService()
    calls: list[str] = []

    monkeypatch.setenv("LLM_GATEWAY_URL", "http://gateway.test/v1")
    monkeypatch.setenv("LLM_GATEWAY_KEY", "gateway-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-key")
    monkeypatch.setenv("RAG_RERANK_LLM_MODEL", "test-llm")

    async def post(self, url, **kwargs):
        calls.append(url)
        if url == "http://gateway.test/v1/chat/completions":
            return httpx.Response(
                502,
                json={"error": "gateway unavailable"},
                request=httpx.Request("POST", url),
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": '{"results":[{"index":0,"score":0.95}]}'}}
                ]
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    outcome = await service.rerank("alpha", documents, provider="auto", top_n=1)

    assert outcome.provider == "llm"
    assert calls == [
        "http://gateway.test/v1/chat/completions",
        "https://openrouter.ai/api/v1/chat/completions",
    ]
