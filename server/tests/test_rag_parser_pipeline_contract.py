"""4C admission/qualification falsifiers, independent of provider quality."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from server.rag.document_parser import DocumentParseError
from server.rag.document_processor import ProcessedDocument, StructuredDocumentProcessor
from server.rag.pipeline_executor import KnowledgePipelineExecutor
from server.rag.rag_service import PipelineContentContractError, PipelineJobStateError
from server.tests.test_rag_chunking_contract import _service


PARSER = "canonical-structured-parser-v2"
RECEIPT = "rag-document-transform-receipt-v1"
FIXTURES = Path(__file__).parent / "fixtures" / "rag_parser_v2"


async def build(tmp_path, *, names=("structured.md",), processor=None, vision=None):
    service = _service(tmp_path)
    if processor is not None:
        service.document_processor = processor
    if vision is not None:
        service.vision_processor = vision
    async def forbidden(*args, **kwargs):
        pytest.fail("Fulltext 4C pipeline must not dispatch embedding")
    service.embedder.embed_texts = forbidden
    kb = service.create_knowledge_base("Parser fixture")
    docs = [await service.upload_document(kb["id"], name, (FIXTURES / name).read_bytes()) for name in names]
    stages = {} if vision is None else {"stage_image_understanding": {"config": {
        "enabled": True, "vision_model_id": "fixture/strict-fake-vision", "failure_policy": "strict",
    }}}
    draft = service.update_pipeline_draft(kb["id"], stages, retrieval_profile={"mode": "fulltext", "rerank_enabled": False})
    job = service.create_pipeline_job(kb["id"], draft_version=draft["version"], source_document_ids=[d["id"] for d in docs])
    assert await KnowledgePipelineExecutor(service).run_once()
    return service, service.get_pipeline_job(job["job_id"]), kb, docs


def test_new_draft_declares_current_parser_contract(tmp_path):
    service = _service(tmp_path)
    kb = service.create_knowledge_base("Parser")
    draft = service.get_pipeline_draft(kb["id"])
    processor = next(stage["config"] for stage in draft["stages"] if stage["id"] == "stage_processor")
    assert processor["parser_contract_version"] == PARSER
    assert processor["processing_receipt_version"] == RECEIPT
    assert draft["content_index_contract"]["components"]["parser"] == "current"


@pytest.mark.asyncio
async def test_legacy_parser_draft_cannot_build_or_get_silently_upgraded(tmp_path):
    service = _service(tmp_path)
    kb = service.create_knowledge_base("Legacy parser")
    doc = await service.upload_document(kb["id"], "text.txt", b"legacy parser body")
    draft = service.update_pipeline_draft(kb["id"], {}, retrieval_profile={"mode": "fulltext"})
    metadata = service._read_metadata()
    old = metadata["pipeline_drafts"][kb["id"]]
    old["stages"]["stage_processor"].pop("parser_contract_version", None)
    old["stages"]["stage_processor"].pop("processing_receipt_version", None)
    metadata["pipeline_graphs"].pop(kb["id"], None)
    service._write_metadata(metadata)
    before = service.metadata_path.read_bytes()
    with pytest.raises(PipelineContentContractError):
        service.create_pipeline_job(kb["id"], draft_version=draft["version"], source_document_ids=[doc["id"]])
    assert service.metadata_path.read_bytes() == before
    assert service.list_pipeline_jobs(kb_id=kb["id"]) == []


@pytest.mark.asyncio
async def test_valid_processing_receipts_bind_job_version_artifact_and_evidence(tmp_path):
    service, job, _, _ = await build(tmp_path)
    assert job["status"] == "succeeded", job.get("error")
    version = service.get_pipeline_version(job["candidate_version_id"])
    result = job["document_results"][0]
    assert result["processing_receipt"]["status"] == "complete"
    assert version["document_results"][0]["processing_receipt"] == result["processing_receipt"]
    assert version["content_index_contract"]["status"] == "current"
    evidence = service.pipeline_version_evidence(version["version_id"])
    assert evidence["processing_receipt_status"] == "current"
    assert evidence["processing_receipt_fingerprint"]


@pytest.mark.asyncio
async def test_api_result_schema_preserves_safe_parser_receipt_and_failure_code(tmp_path):
    from server.rag.api import PipelineDocumentResultPayload
    service, job, _, _ = await build(tmp_path, names=("ambiguous_columns.pdf",))
    result = service.pipeline_job_payload(job)["document_results"][0]
    payload = PipelineDocumentResultPayload.model_validate(result).model_dump()
    assert payload["parser_contract_version"] == PARSER
    assert payload["processing_receipt"] == result["processing_receipt"]
    assert payload["error_code"] == "rag_layout_degraded"
    contaminated = copy.deepcopy(result)
    contaminated["processing_receipt"]["removed_text"] = "do not expose source text"
    assert PipelineDocumentResultPayload.model_validate(contaminated).model_dump()["processing_receipt"] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["structured_processor", "recursive_chunker"])
async def test_graph_node_previews_keep_processing_degradation_evidence(tmp_path, kind):
    service, job, kb, docs = await build(tmp_path, names=("ambiguous_columns.pdf",))
    graph = service.get_pipeline_graph(kb["id"])["graph"]
    node = next(node for node in graph["nodes"] if node["kind"] == kind)
    result = await service.preview_pipeline_graph_node(kb["id"], graph=graph, node_id=node["id"], document_id=docs[0]["id"])
    assert result["metadata"]["processing_receipt"]["status"] == "degraded"
    assert result["metadata"]["error_code"] == "rag_layout_degraded"
    assert "rag_layout_degraded" in result["warnings"]


@pytest.mark.asyncio
async def test_strict_fake_vision_pipeline_preserves_scan_page_without_real_provider(tmp_path, monkeypatch):
    from server.rag.vision_processor import VisionUnderstandingService
    import httpx
    monkeypatch.delenv("LLM_GATEWAY_URL", raising=False)
    monkeypatch.delenv("LLM_GATEWAY_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture-not-a-real-key")
    async def no_network(*args, **kwargs):
        pytest.fail("Parser fixture must not call a real Provider")
    monkeypatch.setattr(httpx.AsyncClient, "request", no_network)
    calls = []
    def fake_request(url, key, payload):
        assert payload["model"] == "fixture/strict-fake-vision"
        calls.append(payload["model"])
        return {"choices": [{"message": {"content": json.dumps({
            "ocr_text": "Harbor scanned record", "visual_summary": "", "tables": [], "charts": [], "language": "en", "warnings": [],
        })}}]}
    vision = VisionUnderstandingService(request_override=fake_request, max_concurrency=1)
    service, job, _, _ = await build(tmp_path, names=("scanned.pdf",), vision=vision)
    assert job["status"] == "succeeded", job.get("error")
    assert calls == ["fixture/strict-fake-vision"]
    receipt = job["document_results"][0]["processing_receipt"]
    assert receipt["status"] == "complete" and receipt["processed_pages"] == [1]
    assert service.pipeline_version_evidence(job["candidate_version_id"])["processing_receipt_status"] == "current"
    result = await service.query_pipeline_version(job["candidate_version_id"], "Harbor", generate_answer=False)
    assert result["sources"][0]["page_number"] == 1


@pytest.mark.asyncio
async def test_degraded_layout_is_diagnostic_only_not_first_activation(tmp_path):
    service, job, _, _ = await build(tmp_path, names=("ambiguous_columns.pdf",))
    assert job["status"] == "succeeded", job.get("error")
    result = job["document_results"][0]
    assert result["error_code"] == "rag_layout_degraded"
    evidence = service.pipeline_version_evidence(job["candidate_version_id"])
    assert evidence["processing_receipt_status"] == "degraded"
    assert evidence["content_index_contract"]["status"] != "current"
    with pytest.raises(PipelineContentContractError):
        service.activate_pipeline_version(job["candidate_version_id"])
    response = await service.query_pipeline_version(job["candidate_version_id"], "evidence alpha", generate_answer=False)
    assert response["sources"]


class EmptyProcessor:
    def process(self, path, *, filename, source_id, **kwargs):
        return ProcessedDocument(source_id=source_id, filename=filename, title="Empty", text="", blocks=[])


@pytest.mark.asyncio
async def test_custom_empty_processor_is_a_document_failure_not_completed(tmp_path):
    service, job, kb, _ = await build(tmp_path, processor=EmptyProcessor())
    assert job["status"] == "failed"
    assert job["document_results"][0]["status"] == "failed"
    assert job["document_results"][0]["error_code"] == "rag_document_empty"
    assert service.list_pipeline_versions(kb["id"]) == []


class PartialProcessor:
    def process(self, path, **kwargs):
        if kwargs["filename"] == "structured.md":
            raise DocumentParseError("Injected page failure", error_code="pdf_parse_failed")
        return StructuredDocumentProcessor().process(path, **kwargs)


@pytest.mark.asyncio
async def test_continue_on_error_retains_failed_source_and_cannot_promote(tmp_path):
    service, job, _, _ = await build(tmp_path, names=("structured.md", "multiple_sheets.xlsx"), processor=PartialProcessor())
    assert job["status"] == "succeeded", job.get("error")
    assert job["document_results"][0]["error_code"] == "pdf_parse_failed"
    evidence = service.pipeline_version_evidence(job["candidate_version_id"])
    assert evidence["processing_receipt_status"] == "degraded"
    with pytest.raises(PipelineContentContractError):
        service.activate_pipeline_version(job["candidate_version_id"], promotion=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("structure_hash", "f" * 64), ("source_content_hash", "e" * 64), ("block_count", 0)])
async def test_processing_receipt_tamper_revokes_evidence_without_rewriting_history(tmp_path, field, value):
    service, job, _, _ = await build(tmp_path)
    metadata = service._read_metadata()
    version_id = job["candidate_version_id"]
    version = metadata["pipeline_versions"][version_id]
    version["document_results"][0]["processing_receipt"][field] = value
    service._write_metadata(metadata)
    before = service.metadata_path.read_bytes()
    assert service.pipeline_version_evidence(version_id)["processing_receipt_status"] == "mismatch"
    with pytest.raises(PipelineContentContractError):
        service.activate_pipeline_version(version_id)
    assert service.metadata_path.read_bytes() == before


@pytest.mark.asyncio
async def test_pdf_table_page_and_range_reach_query_citation_metadata(tmp_path):
    service, job, _, _ = await build(tmp_path, names=("ruled_table.pdf",))
    assert job["status"] == "succeeded", job.get("error")
    response = await service.query_pipeline_version(job["candidate_version_id"], "Harbor", generate_answer=False)
    assert response["sources"]
    assert response["sources"][0]["page_number"] == 1
    assert response["sources"][0]["row_range"] == "A1:B3"
    assert response["sources"][0]["source_block_id"]


@pytest.mark.parametrize("field,value", [("receipt_status", "degraded"), ("receipt_status", "legacy_read_only"), ("receipt_fingerprint", None)])
def test_formal_admission_rejects_missing_or_degraded_processing_evidence(monkeypatch, field, value):
    from server.rag.evaluation import validate_formal_run_admission
    from server.tests.test_rag_evaluation_integrity import _gold, _synthetic_future_formal_target
    monkeypatch.setattr("server.rag.evaluation.qualify_formal_evidence", lambda _: {"qualified": True, "status": "qualified"})
    targets = [_synthetic_future_formal_target(name) for name in ("baseline", "candidate")]
    for target in targets:
        target["version_evidence"]["processor"].update(receipt_status="current", receipt_fingerprint="b" * 64)
    targets[1]["version_evidence"]["processor"][field] = value
    with pytest.raises(ValueError, match="processing receipt"):
        validate_formal_run_admission(_gold(), targets, baseline_version_id="baseline")


def test_resealed_formal_manifest_with_old_parser_receipt_cannot_promote(monkeypatch):
    from server.rag.evaluation import formal_execution_preflight_reasons, seal_execution_manifest, validate_formal_run_admission
    from server.tests.test_rag_evaluation_integrity import _gold, _synthetic_future_formal_target, _synthetic_future_formal_run, _valid_formal_case_result
    monkeypatch.setattr("server.rag.evaluation.qualify_formal_evidence", lambda _: {"qualified": True, "status": "qualified"})
    targets = [_synthetic_future_formal_target(name) for name in ("baseline", "candidate")]
    for target in targets:
        target["version_evidence"]["processor"].update(receipt_status="current", receipt_fingerprint="b" * 64)
    admission = validate_formal_run_admission(_gold(), targets, baseline_version_id="baseline")
    run = _synthetic_future_formal_run(targets, admission, _valid_formal_case_result("vector"))
    run["execution_manifest"]["targets"][1]["processor"].pop("receipt_fingerprint")
    run["execution_manifest"] = seal_execution_manifest(run["execution_manifest"])
    assert "formal_processor_receipt_invalid" in formal_execution_preflight_reasons(run)
