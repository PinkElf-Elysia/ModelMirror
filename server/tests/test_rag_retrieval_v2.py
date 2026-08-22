from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import httpx
import pytest

from server.rag import rag_service as rag_service_module
from server.rag import retrieval as retrieval_module
from server.rag.embedder import EmbeddingClient
from server.rag.lexical_store import LexicalChunk, SqliteLexicalStore, tokenize_for_search
from server.rag.pipeline_executor import KnowledgePipelineExecutor
from server.rag.rag_service import RagService
from server.rag.rag_service import PipelineDraftValidationError
from server.rag.reranker import RerankDocument, RerankItem, RerankOutcome, RerankService
from server.rag.retrieval import RetrievalCandidate, RetrievalConfig, fuse_rankings
from server.rag.splitter import ParentChildTextSplitter, TextSplitter
from server.rag.vector_store import ChromaVectorStore, LocalJsonVectorStore, VectorChunk


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


@pytest.mark.asyncio
async def test_query_embedder_is_not_reused_across_dimension_contracts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = build_service(tmp_path)
    service.embedder.api_key = "test-key"
    service.embedder.embedding_mode = ""
    service.embedder.model = "same-model"
    service.embedder.dimension = 64

    async def unexpected_default_embedder(_texts):
        raise AssertionError("dimension-mismatched default embedder was reused")

    monkeypatch.setattr(service.embedder, "embed_texts", unexpected_default_embedder)
    monkeypatch.setattr(service, "_ensure_embedding_profile_ready", lambda _profile: None)
    created: list[object] = []

    class ProfileEmbedder:
        def __init__(self, *, api_base, api_key, model, dimension):
            self.dimension = dimension
            self.closed = False
            created.append(self)

        async def embed_texts(self, _texts):
            return [[0.0] * self.dimension]

        async def aclose(self) -> None:
            self.closed = True

    monkeypatch.setattr(rag_service_module, "EmbeddingClient", ProfileEmbedder)

    vector = await service._embed_query(
        "dimension-specific query",
        {
            "effective": {
                "ready": True,
                "provider": "openai_compatible",
                "model": "same-model",
                "dimension": 8,
            }
        },
    )

    assert len(vector) == 8
    assert len(created) == 1
    assert created[0].closed is True


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


def test_short_english_segments_are_not_fragmented_by_large_overlap() -> None:
    text = "MDR-44 backups begin at 02:30 UTC."
    recursive = TextSplitter(400, 50)
    parent_child = ParentChildTextSplitter(
        parent_chunk_size=1500,
        parent_chunk_overlap=100,
        child_chunk_size=400,
        child_chunk_overlap=50,
    )

    assert [item.text for item in recursive.split_segments(text)] == [text]
    children = parent_child.split_segments(text)
    assert len(children) == 1
    assert children[0].text == text
    assert children[0].start_char == 0
    assert children[0].end_char == len(text)


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
                sheet="发布计划",
                row_range="A1:B4",
            )
        ]
    )
    assert "蓝鲸" in tokenize_for_search("蓝鲸计划")
    result = store.query("kb-v2", "蓝鲸", 5)[0]
    assert result.chunk_id == "c1"
    assert result.sheet == "发布计划"
    assert result.row_range == "A1:B4"
    assert store.query("kb-v2", "production deployment", 5)[0].document_name == "guide.txt"
    assert store.query("other", "蓝鲸", 5) == []


def test_sqlite_fts5_uses_query_confidence_without_reordering_bm25(
    tmp_path: Path,
) -> None:
    store = SqliteLexicalStore(tmp_path / "lexical-confidence.sqlite3")
    store.add_chunks(
        [
            LexicalChunk(
                chunk_id="exact",
                namespace="kb-v2",
                doc_id="d1",
                document_name="warranty.md",
                text="ZEP-91 quantum battery warranty lasts eighteen months.",
                chunk_index=0,
            ),
            LexicalChunk(
                chunk_id="generic",
                namespace="kb-v2",
                doc_id="d2",
                document_name="policy.md",
                text="The policy defines a standard warranty period.",
                chunk_index=1,
            ),
        ]
    )

    exact = store.query("kb-v2", "ZEP-91 quantum battery warranty", 5)
    weak = store.query("kb-v2", "VTX-88 satellite warranty window", 5)

    assert exact[0].chunk_id == "exact"
    assert exact[0].score > weak[0].score
    assert weak[0].score < 1.0


def test_sqlite_fts5_migrates_old_source_schema_with_optional_defaults(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy-lexical.sqlite3"
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
                end_char INTEGER NOT NULL,
                page_number INTEGER,
                visual_kind TEXT,
                source_block_id TEXT
            )
            """
        )

    store = SqliteLexicalStore(path)
    with sqlite3.connect(path) as connection:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(rag_chunks)")
        }
    assert {"sheet", "row_range"}.issubset(columns)

    store.add_chunks(
        [
            LexicalChunk(
                chunk_id="legacy-compatible",
                namespace="legacy",
                doc_id="doc",
                document_name="legacy.txt",
                text="legacy metadata remains queryable",
                chunk_index=0,
            )
        ]
    )
    result = store.query("legacy", "metadata", 1)[0]
    assert result.sheet is None
    assert result.row_range is None


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


@pytest.mark.asyncio
async def test_version_query_uses_pinned_hash_embedder_not_process_default(
    tmp_path: Path,
) -> None:
    service = build_service(tmp_path)
    kb = service.create_knowledge_base("version-pinned embedding")
    document = await service.upload_document(
        kb["id"],
        "pinned.txt",
        "PINNED-ORBIT requires a version-scoped embedding query.".encode(),
    )
    draft = service.update_pipeline_draft(
        kb["id"],
        {},
        retrieval_profile={"mode": "vector", "top_k": 2},
    )
    job = service.create_pipeline_job(
        kb["id"],
        draft_version=draft["version"],
        source_document_ids=[document["id"]],
    )
    assert await KnowledgePipelineExecutor(service).run_once() is True
    version = service.get_pipeline_version(job["candidate_version_id"])

    wrong_process_embedder = EmbeddingClient(
        api_base="https://wrong-embedding.test/v1",
        api_key="configured-but-wrong",
        model="wrong-process-model",
        dimension=32,
    )

    async def reject_process_default(_texts: list[str]) -> list[list[float]]:
        raise AssertionError("query used the process-level embedder")

    wrong_process_embedder.embed_texts = reject_process_default  # type: ignore[method-assign]
    service.embedder = wrong_process_embedder

    result = await service.query_pipeline_version(
        version["version_id"],
        "PINNED-ORBIT",
        retrieval={"mode": "vector", "top_k": 2},
        generate_answer=False,
    )

    assert result["sources"]
    assert result["retrieval"]["embedding_provider"] == "hash"
    assert result["retrieval"]["embedding_model"] == "deterministic-hash-v1"
    assert result["retrieval"]["embedding_dimension"] == 64
    assert result["retrieval"]["external_call_count"] == 0
    assert result["retrieval"]["embedding_external_call_count"] == 0
    stage_timings = {
        key: result["retrieval"][key]
        for key in (
            "embedding_elapsed_ms",
            "vector_search_elapsed_ms",
            "fulltext_search_elapsed_ms",
            "fusion_elapsed_ms",
        )
    }
    assert all(isinstance(value, (int, float)) for value in stage_timings.values())
    assert all(value >= 0 for value in stage_timings.values())
    assert result["retrieval"]["retrieval_elapsed_ms"] >= max(
        stage_timings.values()
    )


@pytest.mark.asyncio
async def test_hybrid_query_starts_lexical_lookup_while_embedding_is_in_flight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = build_service(tmp_path)
    kb = service.create_knowledge_base("hybrid overlap")
    lexical_started = asyncio.Event()
    loop = asyncio.get_running_loop()

    async def embed_after_lexical_started(_question, _profile):
        await asyncio.wait_for(lexical_started.wait(), timeout=0.2)
        return [0.0] * 64

    def lexical_query(_namespace, _question, _top_k):
        loop.call_soon_threadsafe(lexical_started.set)
        return []

    monkeypatch.setattr(service, "_embed_query", embed_after_lexical_started)
    monkeypatch.setattr(service.lexical_store, "query", lexical_query)

    result = await service._query_namespace(
        kb["id"],
        "hybrid-overlap-v1",
        "ORION-417 and LGT-531",
        config=RetrievalConfig.from_mapping(
            {"mode": "hybrid", "top_k": 5, "rerank_enabled": False}
        ),
        lexical_ready=True,
        generate_answer=False,
    )

    assert lexical_started.is_set()
    assert result["retrieval"]["external_call_count"] == 0


@pytest.mark.asyncio
async def test_real_embedding_version_records_actual_dimension_and_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = tmp_path / "real-storage"
    embedder = EmbeddingClient(
        api_base="https://embedding.test/v1",
        api_key="configured",
        model="real-model",
        dimension=64,
    )

    async def fake_real_embeddings(texts: list[str]) -> list[list[float]]:
        return [[1.0, float(len(text) % 7), 0.25] for text in texts]

    embedder.embed_texts = fake_real_embeddings  # type: ignore[method-assign]
    service = RagService(
        storage_dir=storage,
        uploads_dir=tmp_path / "real-uploads",
        embedder=embedder,
        vector_store=LocalJsonVectorStore(storage / "vectors.json"),
        lexical_store=SqliteLexicalStore(storage / "lexical.sqlite3"),
        llm_enabled=False,
    )
    kb = service.create_knowledge_base("real embedding identity")
    document = await service.upload_document(
        kb["id"],
        "real.txt",
        "REAL-VECTOR-IDENTITY must remain pinned.".encode(),
    )
    draft = service.update_pipeline_draft(
        kb["id"],
        {},
        embedding_profile={
            "provider": "openai_compatible",
            "model": "real-model",
        },
        retrieval_profile={"mode": "vector", "top_k": 2},
    )
    job = service.create_pipeline_job(
        kb["id"],
        draft_version=draft["version"],
        source_document_ids=[document["id"]],
    )
    assert await KnowledgePipelineExecutor(service).run_once() is True
    version = service.get_pipeline_version(job["candidate_version_id"])
    assert version["embedding_profile"]["effective"]["dimension"] == 3
    assert version["embedding_profile"]["dimension"] == 3
    evidence_before = service.pipeline_version_evidence(version["version_id"])

    class VersionDimensionEmbedder:
        def __init__(self, *, api_base, api_key, model, dimension):
            assert dimension == 3
            self.dimension = dimension

        async def embed_texts(self, texts):
            return [[1.0, float(len(text) % 7), 0.25] for text in texts]

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        rag_service_module,
        "EmbeddingClient",
        VersionDimensionEmbedder,
    )

    query_result = await service.query_pipeline_version(
        version["version_id"],
        "REAL-VECTOR-IDENTITY",
        retrieval={"mode": "vector"},
        generate_answer=False,
    )
    assert query_result["retrieval"]["external_call_count"] == 1
    assert query_result["retrieval"]["embedding_external_call_count"] == 1

    embedder.api_key = ""
    evidence_after = service.pipeline_version_evidence(version["version_id"])
    assert evidence_after["version_fingerprint"] == evidence_before["version_fingerprint"]
    assert evidence_after["embedding"]["effective"]["provider"] == (
        "openai_compatible"
    )
    with pytest.raises(PipelineDraftValidationError, match="unavailable"):
        await service.query_pipeline_version(
            version["version_id"],
            "REAL-VECTOR-IDENTITY",
            retrieval={"mode": "vector"},
            generate_answer=False,
        )


def test_chroma_isolates_namespaces_with_different_dimensions(tmp_path: Path) -> None:
    pytest.importorskip("chromadb")
    store = ChromaVectorStore(tmp_path / "chroma")
    store.add_chunks(
        [
            VectorChunk(
                id="chunk-two",
                kb_id="kb::version-two",
                doc_id="doc-two",
                document_name="two.txt",
                text="two dimensional vector",
                embedding=[1.0, 0.0],
                chunk_index=0,
            )
        ]
    )
    store.add_chunks(
        [
            VectorChunk(
                id="chunk-three",
                kb_id="kb::version-three",
                doc_id="doc-three",
                document_name="three.txt",
                text="three dimensional vector",
                embedding=[1.0, 0.0, 0.0],
                chunk_index=0,
            )
        ]
    )

    assert store.query("kb::version-two", [1.0, 0.0], 1)[0].chunk_id == "chunk-two"
    assert (
        store.query("kb::version-three", [1.0, 0.0, 0.0], 1)[0].chunk_id
        == "chunk-three"
    )
    store.delete_knowledge_base("kb::version-two")
    assert store.query("kb::version-two", [1.0, 0.0], 1) == []
    assert store.query("kb::version-three", [1.0, 0.0, 0.0], 1)


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


def test_explicit_abstention_uses_raw_vector_evidence_instead_of_rrf_rank() -> None:
    config = RetrievalConfig.from_mapping(
        {
            "mode": "hybrid",
            "abstention_enabled": True,
            "abstention_score_domain": "vector_score",
            "abstention_threshold": 0.7,
        }
    )
    near_context = _retrieval_candidate(
        "near-context",
        "doc-near",
        vector_score=0.55,
        fulltext_score=8.0,
        fused_score=1.0,
    )
    accepted, decision = retrieval_module.apply_abstention([near_context], config)

    assert accepted == []
    assert decision == {
        "abstention_enabled": True,
        "abstention_applied": True,
        "abstained": True,
        "abstention_score_domain": "vector_score",
        "abstention_threshold": 0.7,
        "abstention_score": 0.55,
        "abstention_input_count": 1,
        "abstention_reason": "below_threshold",
    }

    answer = _retrieval_candidate(
        "answer",
        "doc-answer",
        vector_score=0.82,
        fused_score=0.8,
    )
    accepted, decision = retrieval_module.apply_abstention([answer], config)
    assert accepted == [answer]
    assert decision["abstained"] is False
    assert decision["abstention_reason"] == "accepted"

    with pytest.raises(ValueError, match="vector_score"):
        RetrievalConfig.from_mapping(
            {
                "abstention_enabled": True,
                "abstention_score_domain": "fused_score",
                "abstention_threshold": 0.7,
            }
        )


def _retrieval_candidate(
    chunk_id: str,
    doc_id: str,
    *,
    vector_score: float | None = None,
    fulltext_score: float | None = None,
    fused_score: float = 0.0,
    rerank_score: float | None = None,
    parent_chunk_id: str | None = None,
) -> RetrievalCandidate:
    return RetrievalCandidate(
        chunk_id=chunk_id,
        doc_id=doc_id,
        document_name=f"{doc_id}.txt",
        matched_text=chunk_id,
        context_text=chunk_id,
        vector_score=vector_score,
        fulltext_score=fulltext_score,
        fused_score=fused_score,
        rerank_score=rerank_score,
        parent_chunk_id=parent_chunk_id,
    )


def test_weighted_rrf_score_is_candidate_pool_invariant() -> None:
    config = RetrievalConfig.from_mapping(
        {
            "mode": "hybrid",
            "vector_weight": 0.7,
            "fulltext_weight": 0.3,
        }
    )
    base = fuse_rankings(
        [
            _retrieval_candidate("shared", "doc-a", vector_score=0.9),
            _retrieval_candidate("vector-only", "doc-b", vector_score=0.8),
        ],
        [_retrieval_candidate("shared", "doc-a", fulltext_score=1.0)],
        config,
    )
    expanded = fuse_rankings(
        [
            _retrieval_candidate("shared", "doc-a", vector_score=0.9),
            _retrieval_candidate("vector-only", "doc-b", vector_score=0.8),
            _retrieval_candidate("tail", "doc-c", vector_score=0.1),
        ],
        [_retrieval_candidate("shared", "doc-a", fulltext_score=1.0)],
        config,
    )

    base_scores = {item.chunk_id: item.fused_score for item in base}
    expanded_scores = {item.chunk_id: item.fused_score for item in expanded}
    assert expanded_scores["shared"] == pytest.approx(base_scores["shared"])
    assert expanded_scores["vector-only"] == pytest.approx(
        base_scores["vector-only"]
    )
    assert 0 < expanded_scores["tail"] < expanded_scores["vector-only"] < 1


def test_candidate_selection_uses_fused_threshold_then_parent_dedupe() -> None:
    candidates = [
        _retrieval_candidate(
            "a-parent-best",
            "doc-a",
            fused_score=0.95,
            rerank_score=0.1,
            parent_chunk_id="parent-a",
        ),
        _retrieval_candidate(
            "a-parent-duplicate",
            "doc-a",
            fused_score=0.94,
            rerank_score=0.99,
            parent_chunk_id="parent-a",
        ),
        _retrieval_candidate("a-second", "doc-a", fused_score=0.93),
        _retrieval_candidate("b-first", "doc-b", fused_score=0.92),
        _retrieval_candidate("c-below-threshold", "doc-c", fused_score=0.79),
    ]

    selected = retrieval_module.select_candidates(
        candidates,
        score_threshold=0.8,
        top_k=3,
    )

    assert [item.chunk_id for item in selected] == [
        "a-parent-best",
        "a-second",
        "b-first",
    ]


def test_candidate_selection_does_not_push_high_score_gold_below_top_five() -> None:
    candidates = [
        _retrieval_candidate("a-best", "doc-a", fused_score=1.0),
        _retrieval_candidate("a-second", "doc-a", fused_score=0.99),
        _retrieval_candidate("a-third", "doc-a", fused_score=0.98),
        _retrieval_candidate("a-fourth", "doc-a", fused_score=0.97),
        _retrieval_candidate("a-gold", "doc-a", fused_score=0.96),
        _retrieval_candidate("b-lower", "doc-b", fused_score=0.95),
        _retrieval_candidate("c-lower", "doc-c", fused_score=0.94),
        _retrieval_candidate("d-lower", "doc-d", fused_score=0.93),
    ]

    selected = retrieval_module.select_candidates(
        candidates,
        score_threshold=0.0,
        top_k=5,
    )

    assert [item.chunk_id for item in selected] == [
        "a-best",
        "a-second",
        "a-third",
        "a-fourth",
        "a-gold",
    ]


@pytest.mark.asyncio
async def test_successful_rerank_top_n_does_not_restore_unranked_tail(
    tmp_path: Path,
) -> None:
    class OversizedReranker:
        async def rerank(self, _query, documents, **_kwargs):
            return RerankOutcome(
                items=[
                    RerankItem(document.chunk_id, 1 - index / 100)
                    for index, document in enumerate(documents)
                ],
                provider="api",
                model="bounded-reranker",
                requested_input_count=len(documents),
                input_count=len(documents),
                input_char_count=sum(len(item.text) for item in documents),
                output_count=len(documents),
                timeout_budget_ms=5_000,
                elapsed_ms=12.5,
                attempted_provider="api",
                attempted_model="bounded-reranker",
                provider_target="rerank_api",
                attempted_targets=("rerank_api",),
            )

    service = build_service(tmp_path, reranker=OversizedReranker())
    kb = service.create_knowledge_base("rerank top-n")
    documents = [
        await service.upload_document(
            kb["id"],
            f"candidate-{index}.txt",
            f"ORBIT-RERANK candidate evidence number {index}.".encode("utf-8"),
        )
        for index in range(8)
    ]
    draft = service.update_pipeline_draft(
        kb["id"],
        {},
        retrieval_profile={"mode": "fulltext", "top_k": 8},
    )
    job = service.create_pipeline_job(
        kb["id"],
        draft_version=draft["version"],
        source_document_ids=[item["id"] for item in documents],
    )
    executor = KnowledgePipelineExecutor(service)
    assert await executor.run_once() is True
    version_id = service.get_pipeline_job(job["job_id"])["candidate_version_id"]

    result = await service.query_pipeline_version(
        version_id,
        "ORBIT-RERANK",
        retrieval={
            "mode": "fulltext",
            "top_k": 8,
            "candidate_multiplier": 1,
            "rerank_enabled": True,
            "rerank_provider": "api",
            "rerank_top_n": 2,
        },
        generate_answer=False,
    )

    assert len(result["sources"]) == 2
    assert result["retrieval"]["rerank_input_count"] == 8
    assert result["retrieval"]["rerank_output_count"] == 2
    assert result["retrieval"]["rerank_tail_dropped"] == 6
    assert result["retrieval"]["threshold_score_domain"] == "fused_score"
    assert result["retrieval"]["rerank_requested_input_count"] == 8
    assert result["retrieval"]["rerank_timeout_budget_ms"] == 5_000
    assert result["retrieval"]["rerank_elapsed_ms"] == 12.5
    assert result["retrieval"]["rerank_provider_target_used"] == "rerank_api"
    assert result["retrieval"]["rerank_attempted_targets"] == "rerank_api"
    assert result["retrieval"]["rerank_target_attempt_count"] == 1

    class FailedReranker:
        async def rerank(self, _query, documents, **_kwargs):
            return RerankOutcome(
                items=[],
                provider="none",
                warning="Rerank unavailable; fused ranking was used.",
                requested_input_count=len(documents),
                input_count=len(documents),
                input_char_count=sum(len(item.text) for item in documents),
                attempted_provider="api",
                attempted_model="bounded-reranker",
                fallback_reason="api:http_status_503",
            )

    service.reranker = FailedReranker()
    fallback = await service.query_pipeline_version(
        version_id,
        "ORBIT-RERANK",
        retrieval={
            "mode": "fulltext",
            "top_k": 8,
            "candidate_multiplier": 1,
            "rerank_enabled": True,
            "rerank_provider": "api",
            "rerank_top_n": 2,
        },
        generate_answer=False,
    )

    assert len(fallback["sources"]) == 8
    assert fallback["retrieval"]["rerank_provider_used"] == "none"
    assert fallback["retrieval"]["rerank_fallback_reason"] == "api:http_status_503"


@pytest.mark.asyncio
async def test_llm_rerank_returns_explicit_evidence_sufficiency_verdict(
    monkeypatch: pytest.MonkeyPatch,
    ) -> None:
    service = RerankService()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    async def post(self, url, **kwargs):
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "answerable": False,
                                    "support_score": 0.18,
                                    "reason_code": "requested_fact_absent",
                                    "results": [{"index": 0, "score": 0.92}],
                                }
                            )
                        }
                    }
                ]
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    outcome = await service.rerank(
        "Which certification body is named?",
        [RerankDocument("near-context", "The policy requires a security review.")],
        provider="llm",
        model="test-llm",
        top_n=1,
        max_provider_attempts=1,
    )

    assert outcome.provider == "llm"
    assert outcome.evidence_verdict == "abstain"
    assert outcome.support_score == 0.18
    assert outcome.evidence_reason_code == "requested_fact_absent"
    assert outcome.external_call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("results_fragment", [{"results": []}, {}])
async def test_evidence_verifier_accepts_explicit_abstain_without_ranked_items(
    monkeypatch: pytest.MonkeyPatch,
    results_fragment: dict,
) -> None:
    service = RerankService()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    async def post(self, url, **kwargs):
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "answerable": False,
                                    "support_score": 0.08,
                                    "reason_code": "requested_fact_absent",
                                    **results_fragment,
                                }
                            )
                        }
                    }
                ]
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    outcome = await service.rerank(
        "Which external certification body is named?",
        [RerankDocument("near-context", "The policy requires a security review.")],
        provider="llm",
        model="test-llm",
        top_n=1,
        max_provider_attempts=1,
        require_evidence_verdict=True,
    )

    assert outcome.provider == "llm"
    assert outcome.items == []
    assert outcome.evidence_verdict == "abstain"
    assert outcome.support_score == 0.08
    assert outcome.evidence_reason_code == "requested_fact_absent"
    assert outcome.fallback_reason is None
    assert outcome.external_call_count == 1

    ordinary_rerank = await service.rerank(
        "Which external certification body is named?",
        [RerankDocument("near-context", "The policy requires a security review.")],
        provider="llm",
        model="test-llm",
        top_n=1,
        max_provider_attempts=1,
    )
    assert ordinary_rerank.provider == "none"
    assert ordinary_rerank.fallback_reason == (
        "llm:openrouter:invalid_ranked_items"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response_kind", "expected_reason"),
    [
        ("invalid_json", "invalid_json_response"),
        ("missing_verdict", "missing_evidence_verdict"),
        ("invalid_verdict", "invalid_evidence_verdict"),
        ("invalid_reason", "invalid_reason_code"),
        ("invalid_ranked_items", "invalid_ranked_items"),
        ("timeout", "provider_timeout"),
    ],
)
async def test_llm_verifier_failure_receipts_use_stable_sanitized_codes(
    monkeypatch: pytest.MonkeyPatch,
    response_kind: str,
    expected_reason: str,
) -> None:
    service = RerankService()
    monkeypatch.delenv("LLM_GATEWAY_URL", raising=False)
    monkeypatch.delenv("LLM_GATEWAY_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-test-key")

    async def post(self, url, **kwargs):
        request = httpx.Request("POST", url)
        if response_kind == "timeout":
            raise httpx.ReadTimeout("secret-response-token", request=request)
        if response_kind == "invalid_json":
            content = "{secret-response-token"
        elif response_kind == "missing_verdict":
            content = json.dumps({"results": [{"index": 0, "score": 0.9}]})
        elif response_kind == "invalid_verdict":
            content = json.dumps(
                {
                    "answerable": "secret-response-token",
                    "support_score": 0.9,
                    "reason_code": "supported",
                    "results": [{"index": 0, "score": 0.9}],
                }
            )
        elif response_kind == "invalid_reason":
            content = json.dumps(
                {
                    "answerable": False,
                    "support_score": 0.1,
                    "reason_code": "secret-response-token",
                    "results": [],
                }
            )
        else:
            content = json.dumps(
                {
                    "answerable": True,
                    "support_score": 0.9,
                    "reason_code": "supported",
                    "results": [
                        {"index": "secret-response-token", "score": "invalid"}
                    ],
                }
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}}]},
            request=request,
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    outcome = await service.rerank(
        "sensitive-query-marker",
        [RerankDocument("a", "sensitive-document-marker")],
        provider="llm",
        model="test-llm",
        top_n=1,
        max_provider_attempts=1,
        require_evidence_verdict=True,
    )

    assert outcome.provider == "none"
    assert outcome.fallback_reason == f"llm:openrouter:{expected_reason}"
    serialized = f"{outcome.warning} {outcome.fallback_reason}".lower()
    assert "secret-test-key" not in serialized
    assert "secret-response-token" not in serialized
    assert "sensitive-query-marker" not in serialized
    assert "sensitive-document-marker" not in serialized


@pytest.mark.asyncio
async def test_evidence_verification_abstains_when_fact_is_absent(
    tmp_path: Path,
) -> None:
    class VerifyingReranker:
        async def rerank(self, _query, documents, **_kwargs):
            return RerankOutcome(
                items=[RerankItem(documents[0].chunk_id, 0.94)],
                provider="llm",
                model="support-verifier",
                evidence_verdict="abstain",
                support_score=0.12,
                evidence_reason_code="requested_fact_absent",
                external_call_count=1,
                attempted_provider="llm",
                attempted_model="support-verifier",
                provider_target="llm_gateway",
                attempted_targets=("llm_gateway",),
            )

    service = build_service(tmp_path, reranker=VerifyingReranker())
    kb = service.create_knowledge_base("evidence verdict")
    document = await service.upload_document(
        kb["id"],
        "policy.txt",
        b"The policy requires a security review before vendor approval.",
    )
    draft = service.update_pipeline_draft(
        kb["id"],
        {},
        retrieval_profile={"mode": "fulltext", "top_k": 5},
    )
    job = service.create_pipeline_job(
        kb["id"],
        draft_version=draft["version"],
        source_document_ids=[document["id"]],
    )
    executor = KnowledgePipelineExecutor(service)
    assert await executor.run_once() is True
    version_id = service.get_pipeline_job(job["job_id"])["candidate_version_id"]

    result = await service.query_pipeline_version(
        version_id,
        "Which external certification body performs the review?",
        retrieval={
            "mode": "fulltext",
            "top_k": 5,
            "rerank_enabled": True,
            "rerank_provider": "llm",
            "rerank_top_n": 5,
            "evidence_verification_enabled": True,
        },
        generate_answer=False,
    )

    assert result["sources"] == []
    assert result["retrieval"]["abstention_enabled"] is False
    assert result["retrieval"]["abstention_applied"] is True
    assert result["retrieval"]["abstention_score_domain"] == "evidence_verdict_v1"
    assert result["retrieval"]["abstained"] is True
    assert result["retrieval"]["abstention_reason"] == "requested_fact_absent"
    assert result["retrieval"]["evidence_verification_applied"] is True
    assert result["retrieval"]["evidence_verdict"] == "abstain"
    assert result["retrieval"]["evidence_support_score"] == 0.12


@pytest.mark.asyncio
async def test_evidence_verification_preserves_verifier_input_for_empty_abstain_ranking(
    tmp_path: Path,
) -> None:
    class EmptyAbstainReranker:
        async def rerank(self, _query, documents, **_kwargs):
            return RerankOutcome(
                items=[],
                provider="llm",
                model="support-verifier",
                requested_input_count=len(documents),
                input_count=len(documents),
                evidence_verdict="abstain",
                support_score=0.08,
                evidence_reason_code="requested_fact_absent",
                external_call_count=1,
                attempted_provider="llm",
                attempted_model="support-verifier",
                provider_target="llm_gateway",
                attempted_targets=("llm_gateway",),
            )

    service = build_service(tmp_path, reranker=EmptyAbstainReranker())
    kb = service.create_knowledge_base("empty abstain ranking")
    document = await service.upload_document(
        kb["id"],
        "policy.txt",
        b"The policy requires a security review before vendor approval.",
    )
    draft = service.update_pipeline_draft(
        kb["id"],
        {},
        retrieval_profile={"mode": "fulltext", "top_k": 5},
    )
    job = service.create_pipeline_job(
        kb["id"],
        draft_version=draft["version"],
        source_document_ids=[document["id"]],
    )
    executor = KnowledgePipelineExecutor(service)
    assert await executor.run_once() is True
    version_id = service.get_pipeline_job(job["job_id"])["candidate_version_id"]

    result = await service.query_pipeline_version(
        version_id,
        "Which external certification body performs the review?",
        retrieval={
            "mode": "fulltext",
            "top_k": 5,
            "rerank_enabled": True,
            "rerank_provider": "llm",
            "rerank_top_n": 5,
            "evidence_verification_enabled": True,
        },
        generate_answer=False,
    )

    assert result["sources"] == []
    assert result["retrieval"]["rerank_input_count"] == 1
    assert result["retrieval"]["rerank_output_count"] == 0
    assert result["retrieval"]["abstention_input_count"] == 1
    assert result["retrieval"]["abstention_reason"] == "requested_fact_absent"
    assert result["retrieval"]["evidence_verification_applied"] is True
    assert result["retrieval"]["evidence_verdict"] == "abstain"


@pytest.mark.asyncio
async def test_bounded_evidence_probe_uses_one_provider_call_and_no_answer(
    tmp_path: Path,
) -> None:
    captured: dict = {}

    class BoundedReranker:
        async def rerank(self, _query, documents, **kwargs):
            captured.update(kwargs)
            return RerankOutcome(
                items=[RerankItem(documents[0].chunk_id, 0.97)],
                provider="llm",
                model="support-verifier",
                evidence_verdict="answerable",
                support_score=0.91,
                evidence_reason_code="supported",
                external_call_count=1,
                candidate_limit=10,
                input_char_limit=12_000,
                max_output_tokens=300,
                attempted_provider="llm",
                attempted_model="support-verifier",
                provider_target="llm_gateway",
                attempted_targets=("llm_gateway",),
                provider_http_elapsed_ms=123.4,
                provider_prompt_tokens=321,
                provider_completion_tokens=45,
                provider_total_tokens=366,
                provider_response_char_count=108,
            )

    service = build_service(tmp_path, reranker=BoundedReranker())
    kb = service.create_knowledge_base("bounded probe")
    document = await service.upload_document(
        kb["id"], "policy.txt", b"Control MM-2042 requires an owner and reviewer."
    )
    draft = service.update_pipeline_draft(
        kb["id"], {}, retrieval_profile={"mode": "fulltext", "top_k": 5}
    )
    job = service.create_pipeline_job(
        kb["id"],
        draft_version=draft["version"],
        source_document_ids=[document["id"]],
    )
    executor = KnowledgePipelineExecutor(service)
    assert await executor.run_once() is True
    version_id = service.get_pipeline_job(job["job_id"])["candidate_version_id"]

    result = await service.query_pipeline_version(
        version_id,
        "What does MM-2042 require?",
        retrieval={
            "mode": "fulltext",
            "top_k": 5,
            "rerank_enabled": True,
            "rerank_provider": "llm",
            "rerank_top_n": 5,
            "evidence_verification_enabled": True,
        },
        generate_answer=False,
        external_call_limit=1,
        allow_vector_fallback=False,
    )

    assert result["answer"] == ""
    assert captured["max_provider_attempts"] == 1
    assert result["retrieval"]["external_call_limit"] == 1
    assert result["retrieval"]["external_call_count"] == 1
    assert result["retrieval"]["embedding_external_call_count"] == 0
    assert result["retrieval"]["rerank_external_call_count"] == 1
    assert result["retrieval"]["answer_external_call_count"] == 0
    assert result["retrieval"]["rerank_candidate_limit"] == 10
    assert result["retrieval"]["rerank_input_char_limit"] == 12_000
    assert result["retrieval"]["rerank_max_output_tokens"] == 300
    assert result["retrieval"]["rerank_provider_http_elapsed_ms"] == 123.4
    assert result["retrieval"]["rerank_provider_prompt_tokens"] == 321
    assert result["retrieval"]["rerank_provider_completion_tokens"] == 45
    assert result["retrieval"]["rerank_provider_total_tokens"] == 366
    assert result["retrieval"]["rerank_provider_response_char_count"] == 108


@pytest.mark.asyncio
async def test_full_chain_diagnostic_uses_exactly_embedding_and_verifier_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    class FullChainReranker:
        async def rerank(self, _query, documents, **kwargs):
            captured.update(kwargs)
            return RerankOutcome(
                items=[RerankItem(documents[0].chunk_id, 0.97)],
                provider="llm",
                model="support-verifier",
                evidence_verdict="answerable",
                support_score=0.91,
                evidence_reason_code="supported",
                external_call_count=1,
                candidate_limit=10,
                input_char_limit=12_000,
                max_output_tokens=300,
                attempted_provider="llm",
                attempted_model="support-verifier",
                provider_target="llm_gateway",
                attempted_targets=("llm_gateway",),
            )

    service = build_service(tmp_path, reranker=FullChainReranker())
    kb = service.create_knowledge_base("full chain diagnostic")
    document = await service.upload_document(
        kb["id"], "policy.txt", b"Control MM-2042 requires an owner and reviewer."
    )
    draft = service.update_pipeline_draft(
        kb["id"], {}, retrieval_profile={"mode": "hybrid", "top_k": 5}
    )
    job = service.create_pipeline_job(
        kb["id"],
        draft_version=draft["version"],
        source_document_ids=[document["id"]],
    )
    executor = KnowledgePipelineExecutor(service)
    assert await executor.run_once() is True
    version_id = service.get_pipeline_job(job["job_id"])["candidate_version_id"]
    original_embed_query = service._embed_query

    monkeypatch.setattr(
        service,
        "_resolved_embedding_profile_for_query",
        lambda _profile: {
            "effective": {
                "ready": True,
                "provider": "openai_compatible",
                "model": "test-embedding",
                "dimension": 64,
            }
        },
    )

    async def fake_external_embed_query(question, _profile):
        return await original_embed_query(
            question,
            {
                "effective": {
                    "ready": True,
                    "provider": "hash",
                    "model": "deterministic-hash-v1",
                    "dimension": 64,
                }
            },
        )

    monkeypatch.setattr(service, "_embed_query", fake_external_embed_query)

    result = await service.query_pipeline_version(
        version_id,
        "What does MM-2042 require?",
        retrieval={
            "mode": "hybrid",
            "top_k": 5,
            "rerank_enabled": True,
            "rerank_provider": "llm",
            "rerank_top_n": 5,
            "evidence_verification_enabled": True,
        },
        generate_answer=False,
        external_call_limit=2,
        allow_vector_fallback=False,
    )

    assert result["answer"] == ""
    assert captured["max_provider_attempts"] == 1
    assert result["retrieval"]["external_call_limit"] == 2
    assert result["retrieval"]["external_call_count"] == 2
    assert result["retrieval"]["embedding_external_call_count"] == 1
    assert result["retrieval"]["rerank_external_call_count"] == 1
    assert result["retrieval"]["answer_external_call_count"] == 0


@pytest.mark.asyncio
async def test_enabled_evidence_verifier_fails_closed_without_valid_verdict(
    tmp_path: Path,
) -> None:
    class MissingVerdictReranker:
        async def rerank(self, _query, documents, **_kwargs):
            return RerankOutcome(
                items=[RerankItem(documents[0].chunk_id, 0.99)],
                provider="llm",
                model="support-verifier",
                external_call_count=1,
                attempted_targets=("llm_gateway",),
            )

    service = build_service(tmp_path, reranker=MissingVerdictReranker())
    kb = service.create_knowledge_base("missing verdict")
    document = await service.upload_document(
        kb["id"], "policy.txt", b"The policy mentions vendor security review."
    )
    draft = service.update_pipeline_draft(
        kb["id"], {}, retrieval_profile={"mode": "fulltext", "top_k": 5}
    )
    job = service.create_pipeline_job(
        kb["id"],
        draft_version=draft["version"],
        source_document_ids=[document["id"]],
    )
    executor = KnowledgePipelineExecutor(service)
    assert await executor.run_once() is True
    version_id = service.get_pipeline_job(job["job_id"])["candidate_version_id"]

    result = await service.query_pipeline_version(
        version_id,
        "Who performs the review?",
        retrieval={
            "mode": "fulltext",
            "top_k": 5,
            "rerank_enabled": True,
            "rerank_provider": "llm",
            "evidence_verification_enabled": True,
        },
        generate_answer=False,
    )

    assert result["sources"] == []
    assert result["retrieval"]["abstention_reason"] == "verifier_unavailable"
    assert result["retrieval"]["evidence_verification_applied"] is False
    assert any("verifier" in warning.lower() for warning in result["warnings"])


@pytest.mark.asyncio
async def test_evidence_verification_preserves_no_candidates_without_calling_verifier(
    tmp_path: Path,
) -> None:
    class UnexpectedReranker:
        async def rerank(self, *_args, **_kwargs):
            raise AssertionError("Verifier must not run without retrieval candidates.")

    service = build_service(tmp_path, reranker=UnexpectedReranker())
    kb = service.create_knowledge_base("empty evidence")
    document = await service.upload_document(
        kb["id"], "policy.txt", b"Control MM-2042 requires an owner and reviewer."
    )
    draft = service.update_pipeline_draft(
        kb["id"], {}, retrieval_profile={"mode": "fulltext", "top_k": 5}
    )
    job = service.create_pipeline_job(
        kb["id"],
        draft_version=draft["version"],
        source_document_ids=[document["id"]],
    )
    executor = KnowledgePipelineExecutor(service)
    assert await executor.run_once() is True
    version_id = service.get_pipeline_job(job["job_id"])["candidate_version_id"]

    result = await service.query_pipeline_version(
        version_id,
        "ZXQ-9999 imaginary certification email",
        retrieval={
            "mode": "fulltext",
            "top_k": 5,
            "rerank_enabled": True,
            "rerank_provider": "llm",
            "rerank_top_n": 5,
            "evidence_verification_enabled": True,
        },
        generate_answer=False,
        allow_vector_fallback=False,
    )

    assert result["sources"] == []
    assert result["retrieval"]["abstention_enabled"] is False
    assert result["retrieval"]["abstention_applied"] is True
    assert result["retrieval"]["abstained"] is True
    assert result["retrieval"]["abstention_input_count"] == 0
    assert result["retrieval"]["abstention_reason"] == "no_candidates"
    assert result["retrieval"]["evidence_verification_applied"] is False
    assert result["retrieval"]["evidence_verdict"] == "unavailable"
    assert not any("verifier" in warning.lower() for warning in result["warnings"])


def test_evidence_verification_requires_llm_rerank() -> None:
    with pytest.raises(ValueError, match="requires rerank_provider=llm"):
        retrieval_module.RetrievalConfig.from_mapping(
            {
                "mode": "hybrid",
                "rerank_enabled": True,
                "rerank_provider": "api",
                "evidence_verification_enabled": True,
            }
        )


def test_rerank_capabilities_require_a_dedicated_evidence_verifier_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = RerankService()
    monkeypatch.setenv("LLM_GATEWAY_URL", "https://gateway.test/v1/chat/completions")
    monkeypatch.setenv("LLM_GATEWAY_KEY", "test-key")
    monkeypatch.setenv("RAG_RERANK_LLM_MODEL", "openai/slow-general-model")
    monkeypatch.delenv("RAG_EVIDENCE_VERIFIER_LLM_MODEL", raising=False)

    without_dedicated_model = service.capabilities()
    assert without_dedicated_model["llm_configured"] is True
    assert without_dedicated_model["evidence_verifier_configured"] is False
    assert without_dedicated_model["evidence_verifier_model"] == ""

    monkeypatch.setenv(
        "RAG_EVIDENCE_VERIFIER_LLM_MODEL", "openai/fast-evidence-verifier"
    )
    dedicated = service.capabilities()
    assert dedicated["evidence_verifier_configured"] is True
    assert dedicated["evidence_verifier_model"] == "openai/fast-evidence-verifier"


@pytest.mark.asyncio
async def test_new_pipeline_job_pins_dedicated_evidence_verifier_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = build_service(tmp_path)
    monkeypatch.setattr(
        service.reranker,
        "capabilities",
        lambda: {
            "evidence_verifier_configured": True,
            "evidence_verifier_model": "openai/pinned-evidence-verifier",
        },
    )
    kb = service.create_knowledge_base("pinned evidence verifier")
    document = await service.upload_document(
        kb["id"],
        "policy.txt",
        b"Control MER-209 requires an approved deletion manifest.",
    )

    draft = service.update_pipeline_draft(
        kb["id"],
        {},
        retrieval_profile={
            "mode": "vector",
            "top_k": 5,
            "rerank_enabled": True,
            "rerank_provider": "llm",
            "rerank_model": "openai/unverified-general-model",
            "evidence_verification_enabled": True,
        },
    )

    assert draft["retrieval_profile"]["rerank_model"] == (
        "openai/pinned-evidence-verifier"
    )
    job = service.create_pipeline_job(
        kb["id"],
        draft_version=draft["version"],
        source_document_ids=[document["id"]],
    )
    stored_job = service.get_pipeline_job(job["job_id"])
    assert stored_job["config_snapshot"]["retrieval_profile"]["rerank_model"] == (
        "openai/pinned-evidence-verifier"
    )


def test_formal_evidence_verifier_identity_rejects_model_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = build_service(tmp_path)
    monkeypatch.setattr(
        service.reranker,
        "capabilities",
        lambda: {
            "evidence_verifier_configured": True,
            "evidence_verifier_model": "openai/pinned-evidence-verifier",
        },
    )
    drifted = {
        "mode": "vector",
        "top_k": 5,
        "rerank_enabled": True,
        "rerank_provider": "llm",
        "rerank_model": "openai/different-model",
        "evidence_verification_enabled": True,
    }

    with pytest.raises(PipelineDraftValidationError, match="pinned verifier model"):
        service.validate_evidence_verifier_identity(drifted)

    assert service.validate_evidence_verifier_identity(
        {**drifted, "rerank_model": "openai/pinned-evidence-verifier"}
    )["rerank_model"] == "openai/pinned-evidence-verifier"


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
async def test_rerank_input_is_deterministically_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = RerankService()
    documents = [
        RerankDocument(f"chunk-{index}", str(index) * 2_000)
        for index in range(25)
    ]
    captured: dict = {}
    monkeypatch.setenv("RERANK_API_URL", "https://rerank.test/v1/rerank")
    monkeypatch.setenv("RERANK_API_KEY", "secret-test-key")
    monkeypatch.setenv("RERANK_MODEL", "test-reranker")

    async def post(self, url, **kwargs):
        captured.update(kwargs["json"])
        return httpx.Response(
            200,
            json={"results": [{"index": 0, "relevance_score": 0.9}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    outcome = await service.rerank("bounded query", documents, provider="api", top_n=5)

    assert outcome.provider == "api"
    assert outcome.requested_input_count == 25
    assert outcome.input_count == 20
    assert len(captured["documents"]) == 20
    serialized_chars = len(json.dumps(captured, ensure_ascii=False))
    assert serialized_chars <= 24_000
    assert outcome.input_char_count == serialized_chars
    assert outcome.input_char_count <= 24_000
    assert outcome.candidate_limit == 20
    assert outcome.input_char_limit == 24_000
    assert outcome.timeout_budget_ms == 5_000


@pytest.mark.asyncio
async def test_llm_rerank_budget_includes_prompt_and_serialization_overhead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = RerankService()
    documents = [
        RerankDocument(f"chunk-{index}", ('\\"\n' + str(index)) * 1_000)
        for index in range(20)
    ]
    captured: dict = {}
    monkeypatch.setenv("LLM_GATEWAY_URL", "https://gateway.test/v1")
    monkeypatch.setenv("LLM_GATEWAY_KEY", "gateway-key")
    monkeypatch.setenv("RAG_RERANK_LLM_MODEL", "test-llm")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    async def post(self, url, **kwargs):
        captured.update(kwargs["json"])
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
    outcome = await service.rerank("q" * 4_000, documents, provider="llm", top_n=5)

    serialized_chars = len(json.dumps(captured, ensure_ascii=False))
    assert outcome.provider == "llm"
    assert serialized_chars <= 24_000
    assert outcome.input_char_count == serialized_chars


@pytest.mark.asyncio
async def test_evidence_verifier_uses_dedicated_small_request_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = RerankService()
    documents = [
        RerankDocument(f"chunk-{index}", f"evidence-{index}-" + "x" * 2_000)
        for index in range(20)
    ]
    captured: dict = {}
    monkeypatch.setenv("LLM_GATEWAY_URL", "https://gateway.test/v1")
    monkeypatch.setenv("LLM_GATEWAY_KEY", "gateway-key")
    monkeypatch.setenv(
        "RAG_EVIDENCE_VERIFIER_LLM_MODEL", "openai/fast-evidence-verifier"
    )
    monkeypatch.delenv("RAG_EVIDENCE_VERIFIER_MAX_CANDIDATES", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    async def post(self, url, **kwargs):
        captured.update(kwargs["json"])
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"answerable":true,"support_score":0.9,'
                                '"reason_code":"supported",'
                                '"results":[{"index":0,"score":0.9}]}'
                            )
                        }
                    }
                ]
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    outcome = await service.rerank(
        "Which exact fact is supported?",
        documents,
        provider="llm",
        model="openai/fast-evidence-verifier",
        top_n=5,
        require_evidence_verdict=True,
    )

    user_payload = json.loads(captured["messages"][1]["content"])
    assert outcome.provider == "llm"
    assert len(user_payload["documents"]) == 20
    assert len(json.dumps(captured, ensure_ascii=False)) <= 12_000
    assert captured["max_tokens"] == 300
    assert outcome.candidate_limit == 20
    assert outcome.input_char_limit == 12_000
    assert outcome.max_output_tokens == 300


@pytest.mark.asyncio
async def test_evidence_verifier_records_sanitized_provider_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = RerankService()
    content = (
        '{"answerable":true,"support_score":0.9,'
        '"reason_code":"supported",'
        '"results":[{"index":0,"score":0.9}]}'
    )
    monkeypatch.setenv("LLM_GATEWAY_URL", "https://gateway.test/v1")
    monkeypatch.setenv("LLM_GATEWAY_KEY", "gateway-key")
    monkeypatch.setenv(
        "RAG_EVIDENCE_VERIFIER_LLM_MODEL", "openai/fast-evidence-verifier"
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    async def post(self, url, **_kwargs):
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {
                    "prompt_tokens": 321,
                    "completion_tokens": 45,
                    "total_tokens": 366,
                    "raw_private_detail": "must-not-be-retained",
                },
                "raw_private_response": "must-not-be-retained",
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    outcome = await service.rerank(
        "Which exact fact is supported?",
        [RerankDocument("chunk-0", "bounded evidence")],
        provider="llm",
        model="openai/fast-evidence-verifier",
        top_n=1,
        require_evidence_verdict=True,
    )

    assert outcome.provider_prompt_tokens == 321
    assert outcome.provider_completion_tokens == 45
    assert outcome.provider_total_tokens == 366
    assert outcome.provider_response_char_count == len(content)
    assert outcome.provider_http_elapsed_ms >= 0
    assert "private" not in str(outcome).lower()


@pytest.mark.asyncio
async def test_evidence_verifier_output_budget_scales_for_large_top_n(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = RerankService()
    captured: dict = {}
    monkeypatch.setenv("LLM_GATEWAY_URL", "https://gateway.test/v1")
    monkeypatch.setenv("LLM_GATEWAY_KEY", "gateway-key")
    monkeypatch.setenv(
        "RAG_EVIDENCE_VERIFIER_LLM_MODEL", "openai/fast-evidence-verifier"
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    async def post(self, url, **kwargs):
        captured.update(kwargs["json"])
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"answerable":true,"support_score":0.9,'
                                '"reason_code":"supported",'
                                '"results":[{"index":0,"score":0.9}]}'
                            )
                        }
                    }
                ]
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    outcome = await service.rerank(
        "large result contract",
        [RerankDocument(f"chunk-{index}", f"evidence {index}") for index in range(50)],
        provider="llm",
        model="openai/fast-evidence-verifier",
        top_n=50,
        require_evidence_verdict=True,
    )

    assert captured["max_tokens"] >= 900
    assert outcome.max_output_tokens == captured["max_tokens"]
    assert outcome.max_output_tokens <= 1_200


@pytest.mark.asyncio
async def test_rerank_service_reuses_connection_pool_until_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instances: list[object] = []

    class FakeClient:
        def __init__(self, **_kwargs):
            self.closed = False
            instances.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            self.closed = True

        async def post(self, url, **_kwargs):
            return httpx.Response(
                200,
                json={"results": [{"index": 0, "relevance_score": 0.9}]},
                request=httpx.Request("POST", url),
            )

        async def aclose(self) -> None:
            self.closed = True

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setenv("RERANK_API_URL", "https://rerank.test/v1/rerank")
    monkeypatch.setenv("RERANK_API_KEY", "test-key")
    monkeypatch.setenv("RERANK_MODEL", "test-reranker")
    service = RerankService()
    documents = [RerankDocument("chunk-a", "alpha")]

    await service.rerank("alpha", documents, provider="api", top_n=1)
    await service.rerank("alpha again", documents, provider="api", top_n=1)

    assert len(instances) == 1
    assert instances[0].closed is False

    await service.aclose()

    assert instances[0].closed is True


@pytest.mark.asyncio
async def test_llm_rerank_budget_drops_tail_when_empty_payload_exceeds_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = RerankService()
    documents = [
        RerankDocument(f"chunk-{index}", "x" * 100)
        for index in range(100)
    ]
    captured: dict = {}
    monkeypatch.setenv("LLM_GATEWAY_URL", "https://gateway.test/v1")
    monkeypatch.setenv("LLM_GATEWAY_KEY", "gateway-key")
    monkeypatch.setenv("RAG_RERANK_LLM_MODEL", "test-llm")
    monkeypatch.setenv("RAG_RERANK_MAX_CANDIDATES", "100")
    monkeypatch.setenv("RAG_RERANK_MAX_INPUT_CHARS", "1000")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    async def post(self, url, **kwargs):
        captured.update(kwargs["json"])
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
    outcome = await service.rerank("q" * 4_000, documents, provider="llm", top_n=5)

    serialized_chars = len(json.dumps(captured, ensure_ascii=False))
    user_payload = json.loads(captured["messages"][1]["content"])
    assert 0 < outcome.input_count < 100
    assert len(user_payload["documents"]) == outcome.input_count
    assert serialized_chars <= 1_000
    assert outcome.input_char_count == serialized_chars


@pytest.mark.asyncio
async def test_auto_rerank_timeout_does_not_start_llm_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = RerankService()
    calls: list[str] = []
    monkeypatch.setenv("RERANK_API_URL", "https://rerank.test/v1/rerank")
    monkeypatch.setenv("RERANK_API_KEY", "secret-test-key")
    monkeypatch.setenv("RERANK_MODEL", "test-reranker")
    monkeypatch.setenv("LLM_GATEWAY_URL", "https://gateway.test/v1")
    monkeypatch.setenv("LLM_GATEWAY_KEY", "secret-gateway-key")
    monkeypatch.setenv("RAG_RERANK_LLM_MODEL", "test-llm")
    monkeypatch.setenv("RAG_RERANK_TIMEOUT_SECONDS", "0.02")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    async def post(self, url, **kwargs):
        calls.append(url)
        await asyncio.sleep(0.1)
        raise AssertionError("shared timeout failed")

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    outcome = await service.rerank(
        "timeout query",
        [RerankDocument("a", "alpha")],
        provider="auto",
        top_n=1,
    )

    assert outcome.provider == "none"
    assert outcome.attempted_provider == "api"
    assert outcome.fallback_reason == "timeout_budget_exhausted"
    assert outcome.timeout_budget_ms == 20
    assert calls == ["https://rerank.test/v1/rerank"]
    assert "secret" not in str(outcome.warning).lower()
    assert "https://" not in str(outcome.warning).lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("response_kind", ["invalid_json", "empty_results"])
async def test_invalid_or_empty_rerank_response_falls_back_safely(
    monkeypatch: pytest.MonkeyPatch,
    response_kind: str,
) -> None:
    service = RerankService()
    monkeypatch.setenv("RERANK_API_URL", "https://rerank.test/private/rerank")
    monkeypatch.setenv("RERANK_API_KEY", "secret-test-key")
    monkeypatch.setenv("RERANK_MODEL", "test-reranker")

    async def post(self, url, **kwargs):
        if response_kind == "invalid_json":
            return httpx.Response(
                200,
                content=b"{not-json",
                request=httpx.Request("POST", url),
            )
        return httpx.Response(
            200,
            json={"results": []},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    outcome = await service.rerank(
        "safe fallback",
        [RerankDocument("a", "alpha")],
        provider="api",
        top_n=1,
    )

    assert outcome.provider == "none"
    assert outcome.fallback_reason == (
        "api:invalid_json_response"
        if response_kind == "invalid_json"
        else "api:invalid_ranked_items"
    )
    serialized = f"{outcome.warning} {outcome.fallback_reason}".lower()
    assert "secret-test-key" not in serialized
    assert "rerank.test" not in serialized


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
    assert outcome.provider_target == "openrouter"
    assert outcome.attempted_targets == ("llm_gateway", "openrouter")
    assert outcome.fallback_reason == "llm_gateway:http_status_502"
    assert "llm_gateway:http_status_502" in str(outcome.warning)


@pytest.mark.asyncio
async def test_auto_rerank_keeps_api_model_out_of_llm_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    documents = [RerankDocument("a", "alpha"), RerankDocument("b", "beta")]
    service = RerankService()
    payloads: list[tuple[str, dict]] = []
    monkeypatch.setenv("RERANK_API_URL", "https://rerank.test/v1/rerank")
    monkeypatch.setenv("RERANK_API_KEY", "rerank-key")
    monkeypatch.setenv("RERANK_MODEL", "default-reranker")
    monkeypatch.setenv("LLM_GATEWAY_URL", "https://gateway.test/v1")
    monkeypatch.setenv("LLM_GATEWAY_KEY", "gateway-key")
    monkeypatch.setenv("RAG_RERANK_LLM_MODEL", "deepseek/deepseek-chat")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    async def post(self, url, **kwargs):
        payloads.append((url, dict(kwargs["json"])))
        if url == "https://rerank.test/v1/rerank":
            return httpx.Response(
                503,
                json={"error": "rerank unavailable"},
                request=httpx.Request("POST", url),
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": '{"results":[{"index":1,"score":0.9}]}'}}
                ]
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    outcome = await service.rerank(
        "beta",
        documents,
        provider="auto",
        model="cohere/rerank-4-fast",
        top_n=1,
    )

    assert outcome.provider == "llm"
    assert outcome.model == "deepseek/deepseek-chat"
    assert payloads[0][1]["model"] == "cohere/rerank-4-fast"
    assert payloads[1][1]["model"] == "deepseek/deepseek-chat"


@pytest.mark.asyncio
async def test_rerank_provider_results_are_sorted_by_validated_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    documents = [RerankDocument("a", "alpha"), RerankDocument("b", "beta")]
    service = RerankService()
    monkeypatch.setenv("RERANK_API_URL", "https://rerank.test/v1/rerank")
    monkeypatch.setenv("RERANK_API_KEY", "test-key")
    monkeypatch.setenv("RERANK_MODEL", "test-reranker")

    async def post(self, url, **kwargs):
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 0, "relevance_score": 0.2},
                    {"index": 1, "relevance_score": 0.9},
                ]
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    outcome = await service.rerank("beta", documents, provider="api", top_n=2)

    assert [item.chunk_id for item in outcome.items] == ["b", "a"]
    assert outcome.model == "test-reranker"


@pytest.mark.asyncio
async def test_explicit_llm_rerank_rejects_reranker_only_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = RerankService()
    calls: list[str] = []
    monkeypatch.setenv("LLM_GATEWAY_URL", "https://gateway.test/v1")
    monkeypatch.setenv("LLM_GATEWAY_KEY", "gateway-key")
    monkeypatch.setenv("RAG_RERANK_LLM_MODEL", "deepseek/deepseek-chat")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    async def post(self, url, **kwargs):
        calls.append(url)
        raise AssertionError("reranker-only model reached chat completions")

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    outcome = await service.rerank(
        "alpha",
        [RerankDocument("a", "alpha")],
        provider="llm",
        model="cohere/rerank-4-fast",
        top_n=1,
    )

    assert outcome.provider == "none"
    assert outcome.model == ""
    assert "reranker_model_not_chat_compatible" in str(outcome.warning)
    assert calls == []
