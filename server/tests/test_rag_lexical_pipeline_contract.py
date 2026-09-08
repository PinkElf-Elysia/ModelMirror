from __future__ import annotations

import json
import sqlite3
from io import BytesIO

import pytest

from server.rag.pipeline_executor import KnowledgePipelineExecutor
from server.rag.rag_service import PipelineContentContractError, PipelineJobStateError
from server.tests.test_rag_chunking_contract import _service


@pytest.mark.asyncio
async def test_fulltext_v2_build_query_are_local_and_diagnostic(tmp_path, monkeypatch):
    service = _service(tmp_path)

    async def forbidden(*args, **kwargs):
        pytest.fail("Fulltext must never call an embedding Provider")

    monkeypatch.setattr(service.embedder, "embed_texts", forbidden)
    kb = service.create_knowledge_base("lexical v2")
    document = await service.upload_document(kb["id"], "fixture.txt", b"ZX-4812 red blue deployment instructions.")
    draft = service.update_pipeline_draft(kb["id"], {}, retrieval_profile={"mode": "fulltext", "rerank_enabled": False})
    assert draft["index_contract"]["lexical"]["contract_version"] == "sqlite-fts5-lexical-v2"
    assert draft["content_index_contract"]["components"]["lexical"] == "current"
    job = service.create_pipeline_job(kb["id"], draft_version=draft["version"], source_document_ids=[document["id"]])
    assert await KnowledgePipelineExecutor(service).run_once()
    completed = service.get_pipeline_job(job["job_id"])
    assert completed["status"] == "succeeded", completed.get("error")
    version = service.get_pipeline_version(completed["candidate_version_id"])
    assert version["content_index_contract"]["components"]["lexical"] == "current"
    assert version["content_index_contract"]["components"]["parser"] == "legacy_read_only"
    assert version["lexical_index_receipt"]["chunk_sequence_hash"] == version["chunking_receipt"]["chunk_sequence_hash"]
    evidence = service.pipeline_version_evidence(version["version_id"])
    assert evidence["lexical_index_receipt_status"] == "current"
    assert evidence["chunking_receipt_status"] == "current"
    assert service.vector_store.count_namespace(service._validated_pipeline_version_namespace(version)) == 0
    result = await service.query_pipeline_version(version["version_id"], 'ZX-4812 "red blue"', generate_answer=False)
    assert result["sources"]
    assert result["retrieval"]["lexical_receipt"]["contract_version"] == "sqlite-fts5-lexical-v2"
    assert result["retrieval"]["lexical_receipt"]["candidate_limit"] == min(version["retrieval_profile"]["top_k"] * 8, 500)
    with pytest.raises(PipelineContentContractError):
        service.activate_pipeline_version(version["version_id"])


@pytest.mark.asyncio
@pytest.mark.parametrize("question,reason", [
    ("ZX-9999", "mandatory_or_terms_not_matched"),
    ("red absentword", "minimum_should_match_not_met"),
    ('"unfinished', "query_syntax_invalid"),
])
async def test_empty_fulltext_preview_retains_rejection_receipt(tmp_path, monkeypatch, question, reason):
    from server.rag.evaluation import _safe_retrieval_receipt
    from server.rag.lexical_contract import query_fingerprint

    service = _service(tmp_path)

    async def forbidden(*args, **kwargs):
        pytest.fail("Fulltext must never call an embedding Provider")

    monkeypatch.setattr(service.embedder, "embed_texts", forbidden)
    kb = service.create_knowledge_base("empty receipt")
    document = await service.upload_document(kb["id"], "fixture.txt", b"ZX-4812 red blue instructions.")
    draft = service.update_pipeline_draft(kb["id"], {}, retrieval_profile={"mode": "fulltext", "rerank_enabled": False})
    job = service.create_pipeline_job(kb["id"], draft_version=draft["version"], source_document_ids=[document["id"]])
    assert await KnowledgePipelineExecutor(service).run_once()
    version_id = service.get_pipeline_job(job["job_id"])["candidate_version_id"]
    assert (await service.query_pipeline_version(version_id, "ZX-4812", generate_answer=False))["sources"]
    response = await service.query_pipeline_version(version_id, question, generate_answer=False)
    assert response["sources"] == []
    receipt = response["retrieval"]["lexical_receipt"]
    assert receipt["final_count"] == 0
    assert receipt["rejection_reason"] == reason
    assert receipt["query_fingerprint"] == query_fingerprint(question)
    assert _safe_retrieval_receipt(response["retrieval"])["lexical_receipt"]["rejection_reason"] == reason


@pytest.mark.asyncio
async def test_hybrid_second_index_failure_discards_both_namespaces(tmp_path, monkeypatch):
    service = _service(tmp_path)
    kb = service.create_knowledge_base("atomic lexical failure")
    document = await service.upload_document(kb["id"], "fixture.txt", b"alpha beta gamma deployment guide.")
    draft = service.update_pipeline_draft(kb["id"], {}, retrieval_profile={"mode": "hybrid", "rerank_enabled": False})
    job = service.create_pipeline_job(kb["id"], draft_version=draft["version"], source_document_ids=[document["id"]])
    namespace = service.validate_pipeline_job_execution_contract(job["job_id"])["candidate_namespace"]
    original = service.lexical_store.add_chunks

    def fail_after_write(chunks):
        original(chunks)
        assert service.vector_store.count_namespace(namespace) > 0
        raise RuntimeError("synthetic lexical write failure")

    monkeypatch.setattr(service.lexical_store, "add_chunks", fail_after_write)
    assert await KnowledgePipelineExecutor(service).run_once()
    assert service.get_pipeline_job(job["job_id"])["status"] == "failed"
    assert service.lexical_store.count_namespace(namespace) == 0
    assert service.vector_store.count_namespace(namespace) == 0
    assert service.list_pipeline_versions(kb["id"]) == []


@pytest.mark.asyncio
async def test_new_job_lexical_identity_is_sealed(tmp_path):
    service = _service(tmp_path)
    kb = service.create_knowledge_base("lexical seal")
    document = await service.upload_document(kb["id"], "fixture.txt", b"alpha beta gamma")
    draft = service.update_pipeline_draft(kb["id"], {}, retrieval_profile={"mode": "fulltext"})
    job = service.create_pipeline_job(kb["id"], draft_version=draft["version"], source_document_ids=[document["id"]])
    stored = service.validate_pipeline_job_execution_contract(job["job_id"])
    mutated = json.loads(json.dumps(stored))
    mutated["config_snapshot"]["lexical_profile"]["query_policy"] = "legacy_or_v1"
    with pytest.raises(PipelineJobStateError):
        service._assert_pipeline_job_execution_contract(mutated, verify_sources=False)


def test_legacy_index_contract_keeps_exact_old_shape(tmp_path):
    service = _service(tmp_path)
    contract = service._index_contract(index_schema_version=3, retrieval_profile={"mode": "fulltext"}, embedding_profile={})
    assert contract["lexical"] == {"required": True, "backend": "sqlite_fts5"}
    content = service._content_index_contract(service._default_pipeline_draft_stages(), index_contract=contract)
    assert content["components"]["lexical"] == "legacy_read_only"


@pytest.mark.asyncio
async def test_xlsx_fulltext_roundtrip_preserves_sheet_and_exact_cell_coordinates(tmp_path):
    from openpyxl import Workbook

    book = Workbook()
    book.active.title = "Operations"
    book.active.append(["Identifier", "Instructions"])
    book.active.append(["ZX-4812", "deployment window"])
    content = BytesIO()
    book.save(content)
    book.close()
    service = _service(tmp_path)
    kb = service.create_knowledge_base("spreadsheet lexical evidence")
    document = await service.upload_document(kb["id"], "operations.xlsx", content.getvalue())
    draft = service.update_pipeline_draft(kb["id"], {}, retrieval_profile={"mode": "fulltext", "rerank_enabled": False})
    job = service.create_pipeline_job(kb["id"], draft_version=draft["version"], source_document_ids=[document["id"]])
    assert await KnowledgePipelineExecutor(service).run_once()
    completed = service.get_pipeline_job(job["job_id"])
    assert completed["status"] == "succeeded", completed.get("error")
    result = await service.query_pipeline_version(completed["candidate_version_id"], "deployment window", generate_answer=False)
    assert result["sources"]
    assert all(item["sheet"] == "Operations" and item["row_range"] == "A1:B2" for item in result["sources"])


@pytest.mark.asyncio
async def test_lexical_tokens_tampered_after_write_cannot_publish_version(tmp_path, monkeypatch):
    service = _service(tmp_path)
    kb = service.create_knowledge_base("token integrity")
    document = await service.upload_document(kb["id"], "fixture.txt", b"alpha beta gamma")
    draft = service.update_pipeline_draft(kb["id"], {}, retrieval_profile={"mode": "fulltext"})
    job = service.create_pipeline_job(kb["id"], draft_version=draft["version"], source_document_ids=[document["id"]])
    original = service.lexical_store.add_chunks

    def tamper(chunks):
        original(chunks)
        with sqlite3.connect(service.lexical_store.path) as connection:
            table = connection.execute("SELECT name FROM sqlite_master WHERE name LIKE 'rag_fts_v2_%' AND sql LIKE 'CREATE VIRTUAL TABLE%'").fetchone()[0]
            connection.execute(f"UPDATE {table} SET tokens = ?", ("unrelated fabricated tokens",))

    monkeypatch.setattr(service.lexical_store, "add_chunks", tamper)
    assert await KnowledgePipelineExecutor(service).run_once()
    completed = service.get_pipeline_job(job["job_id"])
    assert completed["status"] == "failed"
    assert "Stored lexical chunks" in completed["error"]
    assert service.list_pipeline_versions(kb["id"]) == []


def test_diagnostic_evaluation_receipt_does_not_store_query_or_identifier_text(tmp_path):
    from server.rag.lexical_store import LexicalChunk, SqliteLexicalStore
    from server.rag.evaluation import _safe_retrieval_receipt

    store = SqliteLexicalStore(tmp_path / "receipt.sqlite3")
    store.add_chunks([LexicalChunk("opaque_id", "new", "doc", "test.txt", "ZX-4812 secret", 0)])
    receipt = store.query_with_receipt("new", "ZX-4812 secret", 5).receipt
    receipt["raw_query"] = "ZX-4812 secret"
    receipt["candidates"][0]["text"] = "ZX-4812 secret"
    safe = _safe_retrieval_receipt({"lexical_receipt": receipt})["lexical_receipt"]
    assert safe["query_policy"] == "minimum_should_match_auto_v1"
    assert safe["final_count"] == 1
    assert "ZX-4812" not in json.dumps(safe)
    assert "secret" not in json.dumps(safe)
