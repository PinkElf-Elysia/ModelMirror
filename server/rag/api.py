from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from .rag_service import (
    DocumentNotFoundError,
    KnowledgeBaseNotFoundError,
    KnowledgeWriteProposalConflictError,
    KnowledgeWriteProposalNotFoundError,
    PipelineDraftValidationError,
    PipelineGraphRevisionError,
    PipelineJobNotFoundError,
    PipelineJobStateError,
    PipelineVersionNotFoundError,
    RagService,
    UnsupportedDocumentError,
)
from .pipeline_graph import PipelineGraphValidationError
from .pipeline_executor import KnowledgePipelineExecutor
from .processor_generator import ProcessorGenerationError
from .evaluation import (
    EvaluationPromotionError,
    EvaluationRevisionError,
    EvaluationRunNotFoundError,
    EvaluationSetNotFoundError,
    EvaluationStateError,
    KnowledgeEvaluationStore,
)
from .evaluation_executor import KnowledgeEvaluationExecutor


MAX_UPLOAD_BYTES = 10 * 1024 * 1024

router = APIRouter(prefix="/api/rag", tags=["rag"])
_rag_service: RagService | None = None
_pipeline_executor: KnowledgePipelineExecutor | None = None
_evaluation_store: KnowledgeEvaluationStore | None = None
_evaluation_executor: KnowledgeEvaluationExecutor | None = None


class KnowledgeBaseCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class KnowledgeBasePayload(BaseModel):
    id: str
    name: str
    document_count: int
    created_at: float
    updated_at: float


class KnowledgeBaseListResponse(BaseModel):
    knowledge_bases: list[KnowledgeBasePayload]


class DocumentPayload(BaseModel):
    id: str
    kb_id: str
    filename: str
    size: int
    chunk_count: int
    content_type: str = "application/octet-stream"
    ingestion_status: str = "indexed_legacy"
    visual_candidate: bool = False
    created_at: float


class DocumentListResponse(BaseModel):
    documents: list[DocumentPayload]


class RagSourcePayload(BaseModel):
    chunk_id: str
    doc_id: str
    source_document_id: str | None = None
    document_name: str
    text: str
    score: float
    matched_text: str | None = None
    vector_score: float | None = None
    fulltext_score: float | None = None
    fused_score: float | None = None
    rerank_score: float | None = None
    parent_chunk_id: str | None = None
    parent_lifted: bool = False
    chunk_type: str = "standard"
    start_char: int = 0
    end_char: int = 0
    page_number: int | None = None
    visual_kind: str | None = None
    source_block_id: str | None = None


class RetrievalOptionsPayload(BaseModel):
    mode: str | None = None
    vector_weight: float | None = None
    fulltext_weight: float | None = None
    top_k: int | None = None
    score_threshold: float | None = None
    candidate_multiplier: int | None = None
    rerank_enabled: bool | None = None
    rerank_provider: str | None = None
    rerank_model: str | None = None
    rerank_top_n: int | None = None


class RagQueryRequest(BaseModel):
    kb_id: str = Field(min_length=1, max_length=160)
    question: str = Field(min_length=1, max_length=20_000)
    top_k: int | None = Field(default=None, ge=1, le=50)
    retrieval: RetrievalOptionsPayload | None = None


class RagQueryResponse(BaseModel):
    answer: str
    sources: list[RagSourcePayload]
    warnings: list[str] = Field(default_factory=list)
    retrieval: dict[str, Any] = Field(default_factory=dict)


class FileAssetPayload(BaseModel):
    file_asset_id: str
    document_id: str
    knowledge_base_id: str
    filename: str
    size: int
    extension: str
    mime_type: str | None = None
    ingestion_status: str = "indexed_legacy"
    visual_candidate: bool = False
    created_at: float


class FileAssetListResponse(BaseModel):
    assets: list[FileAssetPayload]
    asset_count: int


class ArtifactPayload(BaseModel):
    artifact_id: str
    file_asset_id: str
    document_id: str
    knowledge_base_id: str
    title: str
    chunk_count: int
    status: str = "indexed_legacy"
    visual_candidate: bool = False
    created_at: float


class ArtifactListResponse(BaseModel):
    artifacts: list[ArtifactPayload]
    artifact_count: int


class KnowledgeChunkPayload(BaseModel):
    chunk_id: str
    artifact_id: str
    knowledge_base_id: str
    document_id: str
    index: int
    text_preview: str
    text_length: int


class KnowledgeChunkListResponse(BaseModel):
    artifact_id: str
    chunks: list[KnowledgeChunkPayload]
    chunk_count: int


class PipelineDraftStagePayload(BaseModel):
    id: str
    kind: str
    title: str
    status: str
    item_count: int
    summary: str
    config: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PipelineDraftResponse(BaseModel):
    kb_id: str
    draft_id: str
    version: int
    updated_at: float
    editable: bool
    index_schema_version: int = 2
    embedding_profile: dict[str, Any] = Field(default_factory=dict)
    retrieval_profile: dict[str, Any] = Field(default_factory=dict)
    stages: list[PipelineDraftStagePayload]
    stage_count: int


class PipelineDraftStageUpdate(BaseModel):
    config: dict[str, Any] = Field(default_factory=dict)


class PipelineDraftUpdateRequest(BaseModel):
    stages: dict[str, PipelineDraftStageUpdate] = Field(default_factory=dict)
    embedding_profile: dict[str, Any] | None = None
    retrieval_profile: RetrievalOptionsPayload | None = None


class PipelineGraphNodePayload(BaseModel):
    id: str = Field(min_length=1, max_length=160)
    kind: str = Field(min_length=1, max_length=80)
    title: str = Field(default="", max_length=120)
    position: dict[str, float]
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class PipelineGraphEdgePayload(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    source: str = Field(min_length=1, max_length=160)
    target: str = Field(min_length=1, max_length=160)
    source_port: str | None = None
    target_port: str | None = None


class PipelineGraphPayload(BaseModel):
    version: str = "knowledge-pipeline-graph-v1"
    kb_id: str = ""
    nodes: list[PipelineGraphNodePayload] = Field(default_factory=list, max_length=50)
    edges: list[PipelineGraphEdgePayload] = Field(default_factory=list, max_length=100)


class PipelineGraphIssuePayload(BaseModel):
    code: str
    message: str
    node_id: str | None = None
    edge_id: str | None = None


class PipelineGraphResponse(BaseModel):
    kb_id: str
    graph_id: str
    graph_revision: int
    compiled_draft_version: int
    updated_at: float
    valid: bool
    issues: list[PipelineGraphIssuePayload] = Field(default_factory=list)
    graph: PipelineGraphPayload
    compiled: dict[str, Any] | None = None


class PipelineGraphSaveRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    graph: PipelineGraphPayload


class PipelineGraphValidateRequest(BaseModel):
    graph: PipelineGraphPayload


class PipelineGraphValidationResponse(BaseModel):
    kb_id: str
    valid: bool
    issues: list[PipelineGraphIssuePayload] = Field(default_factory=list)
    compiled: dict[str, Any] | None = None


class PipelineGraphPreviewRequest(BaseModel):
    graph: PipelineGraphPayload
    node_id: str = Field(min_length=1, max_length=160)
    document_id: str | None = Field(default=None, max_length=200)


class PipelineGraphPreviewResponse(BaseModel):
    node_id: str
    kind: str
    preview_type: str
    item_count: int
    items: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    truncated: bool = False


class PipelineGraphExecuteRequest(BaseModel):
    graph_revision: int = Field(ge=1)
    draft_version: int = Field(ge=1)
    source_document_ids: list[str] | None = None


class ProcessorPreviewRequest(BaseModel):
    document_id: str = Field(min_length=1, max_length=200)
    processor: dict[str, Any] | None = None


class ProcessorPreviewResponse(BaseModel):
    kb_id: str
    document_id: str
    filename: str
    title: str
    config: dict[str, Any]
    character_count: int
    block_count: int
    block_counts: dict[str, int]
    generated_count: int
    warnings: list[str]
    blocks: list[dict[str, Any]]
    generated_items: list[dict[str, Any]]


class PipelinePreflightStagePayload(BaseModel):
    id: str
    kind: str
    title: str
    status: str
    severity: str
    summary: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class PipelineDraftPreflightResponse(BaseModel):
    kb_id: str
    draft_id: str
    ready: bool
    warnings: list[str]
    stage_checks: list[PipelinePreflightStagePayload]
    document_count: int
    artifact_count: int
    chunk_count: int


class XpertFileReference(BaseModel):
    xpert_id: str = Field(min_length=1, max_length=200)
    conversation_id: str = Field(min_length=1, max_length=200)
    asset_id: str = Field(min_length=1, max_length=200)


class PipelineExecuteRequest(BaseModel):
    draft_version: int = Field(ge=1)
    source_document_ids: list[str] | None = None
    xpert_file_refs: list[XpertFileReference] = Field(default_factory=list, max_length=5)


class PipelineJobStagePayload(BaseModel):
    id: str
    title: str
    status: str
    progress: int
    item_count: int | None = None
    started_at: float | None = None
    completed_at: float | None = None
    error: str | None = None


class PipelineJobSourcePayload(BaseModel):
    source_id: str
    source_kind: str
    filename: str
    size: int
    content_mode: str
    xpert_id: str | None = None
    conversation_id: str | None = None
    asset_id: str | None = None


class PipelineDocumentResultPayload(BaseModel):
    source_id: str
    filename: str
    status: str
    attempt: int = 0
    block_count: int = 0
    generated_count: int = 0
    chunk_count: int = 0
    qa_count: int = 0
    summary_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    duration_ms: float | None = None
    vision_status: str = "skipped"
    vision_attempt: int = 0
    vision_page_count: int = 0
    vision_selected_page_count: int = 0
    vision_processed_page_count: int = 0
    vision_failed_page_count: int = 0
    vision_block_count: int = 0
    vision_warnings: list[str] = Field(default_factory=list)
    vision_error: str | None = None


class PipelineJobPayload(BaseModel):
    job_id: str
    kb_id: str
    draft_id: str
    draft_version: int
    graph_id: str | None = None
    graph_revision: int | None = None
    status: str
    stages: list[PipelineJobStagePayload]
    sources: list[PipelineJobSourcePayload]
    document_results: list[PipelineDocumentResultPayload] = Field(default_factory=list)
    source_count: int
    candidate_version_id: str
    candidate_version: int
    run_id: str | None = None
    attempt: int
    cancel_requested: bool
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)
    created_at: float
    updated_at: float
    started_at: float | None = None
    completed_at: float | None = None


class PipelineJobListResponse(BaseModel):
    jobs: list[PipelineJobPayload]
    job_count: int


class PipelineVersionPayload(BaseModel):
    version_id: str
    kb_id: str
    version: int
    status: str
    active: bool
    draft_id: str
    draft_version: int
    source_summary: list[PipelineJobSourcePayload]
    document_count: int
    chunk_count: int
    block_count: int = 0
    qa_count: int = 0
    summary_count: int = 0
    processor_profile: dict[str, Any] = Field(default_factory=dict)
    document_results: list[PipelineDocumentResultPayload] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    job_id: str
    created_at: float
    activated_at: float | None = None
    index_schema_version: int = 1
    embedding_profile: dict[str, Any] = Field(default_factory=dict)
    retrieval_profile: dict[str, Any] = Field(default_factory=dict)
    vector_index_ready: bool = True
    lexical_index_ready: bool = False
    vision_profile: dict[str, Any] = Field(default_factory=dict)
    vision_page_count: int = 0
    vision_processed_page_count: int = 0
    vision_failed_page_count: int = 0
    vision_block_count: int = 0


class PipelineVersionListResponse(BaseModel):
    versions: list[PipelineVersionPayload]
    version_count: int
    active_version_id: str | None = None


class PipelineVersionQueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=20_000)
    top_k: int | None = Field(default=None, ge=1, le=50)
    retrieval: RetrievalOptionsPayload | None = None


class PipelineVersionQueryResponse(BaseModel):
    version_id: str
    version: int
    answer: str
    sources: list[RagSourcePayload]
    warnings: list[str] = Field(default_factory=list)
    retrieval: dict[str, Any] = Field(default_factory=dict)


class CitationAnchorPayload(BaseModel):
    citation_id: str
    chunk_id: str
    artifact_id: str
    document_id: str
    document_name: str
    score: float
    snippet: str
    page_number: int | None = None
    visual_kind: str | None = None
    source_block_id: str | None = None


class CitationAnchorRequest(BaseModel):
    kb_id: str = Field(min_length=1, max_length=160)
    question: str = Field(min_length=1, max_length=20_000)
    top_k: int = Field(default=4, ge=1, le=50)
    retrieval: RetrievalOptionsPayload | None = None


class CitationAnchorResponse(BaseModel):
    kb_id: str
    citations: list[CitationAnchorPayload]
    citation_count: int


class EvaluationReferenceInput(BaseModel):
    reference_id: str | None = Field(default=None, max_length=200)
    document_id: str = Field(min_length=1, max_length=200)
    chunk_id: str | None = Field(default=None, max_length=240)
    source_block_id: str | None = Field(default=None, max_length=240)
    page_number: int | None = Field(default=None, ge=1, le=100_000)
    relevance: int = Field(default=1, ge=1, le=3)


class EvaluationCaseInput(BaseModel):
    query: str = Field(min_length=1, max_length=20_000)
    expected_refs: list[EvaluationReferenceInput] = Field(min_length=1, max_length=50)
    tags: list[str] = Field(default_factory=list, max_length=20)
    notes: str = Field(default="", max_length=1000)


class EvaluationSetCreateRequest(BaseModel):
    kb_id: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=1000)


class EvaluationSetUpdateRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=1000)
    status: str | None = None


class EvaluationCasesRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    cases: list[EvaluationCaseInput] = Field(min_length=1, max_length=500)


class EvaluationCaseUpdateRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    case: EvaluationCaseInput


class EvaluationTargetInput(BaseModel):
    version_id: str = Field(min_length=1, max_length=200)
    label: str | None = Field(default=None, max_length=120)
    retrieval: RetrievalOptionsPayload | None = None


class EvaluationRunCreateRequest(BaseModel):
    eval_set_id: str = Field(min_length=1, max_length=200)
    targets: list[EvaluationTargetInput] = Field(min_length=1, max_length=5)
    baseline_version_id: str | None = Field(default=None, max_length=200)
    ks: list[int] = Field(default_factory=lambda: [1, 3, 5, 10], min_length=1, max_length=8)


class EvaluationGateUpdateRequest(BaseModel):
    mode: str
    min_recall_at_5: float = Field(ge=0, le=1)
    max_mrr_regression: float = Field(ge=0, le=1)
    max_citation_hit_regression: float = Field(ge=0, le=1)
    max_no_result_increase: float = Field(ge=0, le=1)
    max_p95_latency_ratio: float = Field(ge=1, le=10)
    require_zero_errors: bool = True


class PipelineActivationRequest(BaseModel):
    evaluation_run_id: str | None = Field(default=None, max_length=200)


class KnowledgeWriteProposalUpdateRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=160)
    content: str | None = Field(default=None, min_length=1, max_length=20_000)
    tags: list[str] | None = None


class KnowledgeWriteProposalDecisionRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    reason: str = Field(default="", max_length=500)


def get_rag_service() -> RagService:
    """Return the process-wide RAG service."""

    global _rag_service
    if _rag_service is None:
        _rag_service = RagService()
    return _rag_service


def set_rag_service_for_tests(service: RagService | None) -> None:
    """Replace the global RAG service in tests."""

    global _rag_service, _pipeline_executor, _evaluation_store, _evaluation_executor
    _rag_service = service
    _pipeline_executor = None
    _evaluation_store = None
    _evaluation_executor = None


def configure_pipeline_executor(*, run_registry: Any | None = None) -> KnowledgePipelineExecutor:
    """Configure the process-wide pipeline executor after shared runtime setup."""

    global _pipeline_executor
    _pipeline_executor = KnowledgePipelineExecutor(
        get_rag_service(),
        run_registry=run_registry,
    )
    return _pipeline_executor


def get_pipeline_executor() -> KnowledgePipelineExecutor:
    global _pipeline_executor
    if _pipeline_executor is None:
        _pipeline_executor = KnowledgePipelineExecutor(get_rag_service())
    return _pipeline_executor


def set_pipeline_executor_for_tests(executor: KnowledgePipelineExecutor | None) -> None:
    global _pipeline_executor
    _pipeline_executor = executor


def get_evaluation_store() -> KnowledgeEvaluationStore:
    global _evaluation_store
    if _evaluation_store is None:
        _evaluation_store = KnowledgeEvaluationStore(
            get_rag_service().storage_dir / "evaluations.json"
        )
    return _evaluation_store


def configure_evaluation_executor(*, run_registry: Any | None = None) -> KnowledgeEvaluationExecutor:
    global _evaluation_executor
    _evaluation_executor = KnowledgeEvaluationExecutor(
        get_rag_service(),
        get_evaluation_store(),
        run_registry=run_registry,
    )
    return _evaluation_executor


def get_evaluation_executor() -> KnowledgeEvaluationExecutor:
    global _evaluation_executor
    if _evaluation_executor is None:
        _evaluation_executor = KnowledgeEvaluationExecutor(
            get_rag_service(),
            get_evaluation_store(),
        )
    return _evaluation_executor


def set_evaluation_executor_for_tests(executor: KnowledgeEvaluationExecutor | None) -> None:
    global _evaluation_executor
    _evaluation_executor = executor


def _require_knowledge_base(kb_id: str) -> None:
    if not any(item["id"] == kb_id for item in get_rag_service().list_knowledge_bases()):
        raise HTTPException(status_code=404, detail="Knowledge base not found.")


@router.get("/retrieval-capabilities")
async def get_retrieval_capabilities() -> dict[str, Any]:
    return get_rag_service().retrieval_capabilities()


@router.get("/processor-capabilities")
async def get_processor_capabilities() -> dict[str, Any]:
    return get_rag_service().processor_capabilities()


@router.get("/vision-capabilities")
async def get_vision_capabilities() -> dict[str, Any]:
    return get_rag_service().vision_capabilities()


@router.post("/knowledge_bases", response_model=KnowledgeBasePayload)
async def create_knowledge_base(payload: KnowledgeBaseCreateRequest) -> dict[str, Any]:
    try:
        return get_rag_service().create_knowledge_base(payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/knowledge_bases", response_model=KnowledgeBaseListResponse)
async def list_knowledge_bases() -> KnowledgeBaseListResponse:
    return KnowledgeBaseListResponse(
        knowledge_bases=[
            KnowledgeBasePayload.model_validate(item)
            for item in get_rag_service().list_knowledge_bases()
        ]
    )


@router.delete("/knowledge_bases/{kb_id}")
async def delete_knowledge_base(kb_id: str) -> dict[str, bool]:
    try:
        get_rag_service().delete_knowledge_base(kb_id)
        return {"ok": True}
    except KnowledgeBaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/knowledge_bases/{kb_id}/documents", response_model=DocumentPayload)
async def upload_document(kb_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
    filename = file.filename or "document.txt"
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="文件过大，请上传 10MB 以内的文档。")

    extension = Path(filename).suffix.lower()
    declared_type = str(file.content_type or "").lower().strip()
    expected_types = {
        ".png": {"image/png"},
        ".jpg": {"image/jpeg"},
        ".jpeg": {"image/jpeg"},
        ".webp": {"image/webp"},
        ".pdf": {"application/pdf"},
    }
    if (
        extension in expected_types
        and declared_type
        and declared_type != "application/octet-stream"
        and declared_type not in expected_types[extension]
    ):
        raise HTTPException(
            status_code=400,
            detail="The uploaded MIME type does not match the file extension.",
        )

    try:
        return await get_rag_service().upload_document(kb_id, filename, content)
    except KnowledgeBaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except UnsupportedDocumentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/knowledge_bases/{kb_id}/documents", response_model=DocumentListResponse)
async def list_documents(kb_id: str) -> DocumentListResponse:
    try:
        return DocumentListResponse(
            documents=[
                DocumentPayload.model_validate(item)
                for item in get_rag_service().list_documents(kb_id)
            ]
        )
    except KnowledgeBaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str) -> dict[str, bool]:
    try:
        get_rag_service().delete_document(doc_id)
        return {"ok": True}
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/pipeline/assets", response_model=FileAssetListResponse)
async def list_pipeline_assets(kb_id: str | None = None) -> FileAssetListResponse:
    try:
        assets = get_rag_service().list_pipeline_assets(kb_id=kb_id)
        return FileAssetListResponse(
            assets=[FileAssetPayload.model_validate(item) for item in assets],
            asset_count=len(assets),
        )
    except KnowledgeBaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/pipeline/artifacts", response_model=ArtifactListResponse)
async def list_pipeline_artifacts(kb_id: str | None = None) -> ArtifactListResponse:
    try:
        artifacts = get_rag_service().list_pipeline_artifacts(kb_id=kb_id)
        return ArtifactListResponse(
            artifacts=[ArtifactPayload.model_validate(item) for item in artifacts],
            artifact_count=len(artifacts),
        )
    except KnowledgeBaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/pipeline/artifacts/{artifact_id}/chunks", response_model=KnowledgeChunkListResponse)
async def list_pipeline_artifact_chunks(artifact_id: str) -> KnowledgeChunkListResponse:
    try:
        chunks = get_rag_service().list_pipeline_artifact_chunks(artifact_id)
        return KnowledgeChunkListResponse(
            artifact_id=artifact_id,
            chunks=[KnowledgeChunkPayload.model_validate(item) for item in chunks],
            chunk_count=len(chunks),
        )
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/pipeline/draft", response_model=PipelineDraftResponse)
async def get_pipeline_draft(kb_id: str) -> PipelineDraftResponse:
    try:
        draft = get_rag_service().get_pipeline_draft(kb_id)
        stages = [
            PipelineDraftStagePayload.model_validate(item)
            for item in draft.get("stages", [])
        ]
        return PipelineDraftResponse(
            kb_id=str(draft["kb_id"]),
            draft_id=str(draft["draft_id"]),
            version=int(draft["version"]),
            updated_at=float(draft["updated_at"]),
            editable=bool(draft["editable"]),
            index_schema_version=int(draft.get("index_schema_version", 2)),
            embedding_profile=dict(draft.get("embedding_profile") or {}),
            retrieval_profile=dict(draft.get("retrieval_profile") or {}),
            stages=stages,
            stage_count=len(stages),
        )
    except KnowledgeBaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/pipeline/graph", response_model=PipelineGraphResponse)
async def get_pipeline_graph(kb_id: str) -> PipelineGraphResponse:
    try:
        return PipelineGraphResponse.model_validate(
            get_rag_service().get_pipeline_graph(kb_id)
        )
    except KnowledgeBaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/pipeline/graph/{kb_id}", response_model=PipelineGraphResponse)
async def save_pipeline_graph(
    kb_id: str,
    payload: PipelineGraphSaveRequest,
) -> PipelineGraphResponse:
    try:
        graph = payload.graph.model_dump()
        graph["kb_id"] = kb_id
        return PipelineGraphResponse.model_validate(
            get_rag_service().save_pipeline_graph(
                kb_id,
                graph,
                expected_revision=payload.expected_revision,
            )
        )
    except KnowledgeBaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PipelineGraphRevisionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PipelineGraphValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "message": str(exc),
                "issues": [issue.payload() for issue in exc.issues],
            },
        ) from exc


@router.post(
    "/pipeline/graph/{kb_id}/validate",
    response_model=PipelineGraphValidationResponse,
)
async def validate_pipeline_graph_endpoint(
    kb_id: str,
    payload: PipelineGraphValidateRequest,
) -> PipelineGraphValidationResponse:
    try:
        graph = payload.graph.model_dump()
        graph["kb_id"] = kb_id
        return PipelineGraphValidationResponse.model_validate(
            get_rag_service().validate_pipeline_graph_config(kb_id, graph)
        )
    except KnowledgeBaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/pipeline/graph/{kb_id}/preview-node",
    response_model=PipelineGraphPreviewResponse,
)
async def preview_pipeline_graph_node(
    kb_id: str,
    payload: PipelineGraphPreviewRequest,
) -> PipelineGraphPreviewResponse:
    try:
        graph = payload.graph.model_dump()
        graph["kb_id"] = kb_id
        result = await get_rag_service().preview_pipeline_graph_node(
            kb_id,
            graph=graph,
            node_id=payload.node_id,
            document_id=payload.document_id,
        )
        return PipelineGraphPreviewResponse.model_validate(result)
    except (KnowledgeBaseNotFoundError, DocumentNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PipelineGraphValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "message": str(exc),
                "issues": [issue.payload() for issue in exc.issues],
            },
        ) from exc
    except (PipelineDraftValidationError, ProcessorGenerationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/pipeline/graph/{kb_id}/execute",
    response_model=PipelineJobPayload,
)
async def execute_pipeline_graph(
    kb_id: str,
    payload: PipelineGraphExecuteRequest,
) -> PipelineJobPayload:
    try:
        job = get_rag_service().create_pipeline_job(
            kb_id,
            draft_version=payload.draft_version,
            graph_revision=payload.graph_revision,
            source_document_ids=payload.source_document_ids,
        )
        get_pipeline_executor().notify()
        return PipelineJobPayload.model_validate(job)
    except KnowledgeBaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (PipelineJobStateError, PipelineGraphRevisionError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PipelineGraphValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "message": str(exc),
                "issues": [issue.payload() for issue in exc.issues],
            },
        ) from exc
    except PipelineDraftValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/pipeline/draft/{kb_id}", response_model=PipelineDraftResponse)
async def update_pipeline_draft(
    kb_id: str,
    payload: PipelineDraftUpdateRequest,
) -> PipelineDraftResponse:
    try:
        draft = get_rag_service().update_pipeline_draft(
            kb_id,
            {
                stage_id: stage_update.model_dump()
                for stage_id, stage_update in payload.stages.items()
            },
            retrieval_profile=(
                payload.retrieval_profile.model_dump(exclude_none=True)
                if payload.retrieval_profile
                else None
            ),
            embedding_profile=payload.embedding_profile,
        )
        stages = [
            PipelineDraftStagePayload.model_validate(item)
            for item in draft.get("stages", [])
        ]
        return PipelineDraftResponse(
            kb_id=str(draft["kb_id"]),
            draft_id=str(draft["draft_id"]),
            version=int(draft["version"]),
            updated_at=float(draft["updated_at"]),
            editable=bool(draft["editable"]),
            index_schema_version=int(draft.get("index_schema_version", 2)),
            embedding_profile=dict(draft.get("embedding_profile") or {}),
            retrieval_profile=dict(draft.get("retrieval_profile") or {}),
            stages=stages,
            stage_count=len(stages),
        )
    except KnowledgeBaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PipelineDraftValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/pipeline/draft/{kb_id}/processor-preview",
    response_model=ProcessorPreviewResponse,
)
async def preview_pipeline_processor(
    kb_id: str,
    payload: ProcessorPreviewRequest,
) -> ProcessorPreviewResponse:
    try:
        result = await get_rag_service().preview_pipeline_processor(
            kb_id,
            payload.document_id,
            payload.processor,
        )
        return ProcessorPreviewResponse.model_validate(result)
    except (KnowledgeBaseNotFoundError, DocumentNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (PipelineDraftValidationError, ProcessorGenerationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/pipeline/draft/{kb_id}/preflight", response_model=PipelineDraftPreflightResponse)
async def preflight_pipeline_draft(kb_id: str) -> PipelineDraftPreflightResponse:
    try:
        preflight = get_rag_service().preflight_pipeline_draft(kb_id)
        return PipelineDraftPreflightResponse(
            kb_id=str(preflight["kb_id"]),
            draft_id=str(preflight["draft_id"]),
            ready=bool(preflight["ready"]),
            warnings=list(preflight["warnings"]),
            stage_checks=[
                PipelinePreflightStagePayload.model_validate(item)
                for item in preflight["stage_checks"]
            ],
            document_count=int(preflight["document_count"]),
            artifact_count=int(preflight["artifact_count"]),
            chunk_count=int(preflight["chunk_count"]),
        )
    except KnowledgeBaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/pipeline/draft/{kb_id}/execute", response_model=PipelineJobPayload)
async def execute_pipeline_draft(
    kb_id: str,
    payload: PipelineExecuteRequest,
) -> PipelineJobPayload:
    xpert_sources: list[dict[str, Any]] = []
    try:
        if payload.xpert_file_refs:
            try:
                from server.xperts import get_xpert_context_store
            except ModuleNotFoundError:
                from xperts import get_xpert_context_store

            context_store = get_xpert_context_store()
            seen: set[tuple[str, str, str]] = set()
            for reference in payload.xpert_file_refs:
                key = (reference.xpert_id, reference.conversation_id, reference.asset_id)
                if key in seen:
                    continue
                seen.add(key)
                asset = context_store.get_file(
                    reference.xpert_id,
                    reference.asset_id,
                    conversation_id=reference.conversation_id,
                    include_archived=True,
                )
                xpert_sources.append(
                    {
                        "xpert_id": reference.xpert_id,
                        "conversation_id": reference.conversation_id,
                        "asset_id": reference.asset_id,
                        "filename": asset.filename,
                        "text": context_store.read_file_text(asset),
                    }
                )

        job = get_rag_service().create_pipeline_job(
            kb_id,
            draft_version=payload.draft_version,
            source_document_ids=payload.source_document_ids,
            xpert_sources=xpert_sources,
        )
        get_pipeline_executor().notify()
        return PipelineJobPayload.model_validate(job)
    except KnowledgeBaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PipelineJobStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PipelineDraftValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        if exc.__class__.__name__ in {
            "XpertContextNotFoundError",
            "XpertContextValidationError",
        }:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        raise


@router.get("/pipeline/jobs", response_model=PipelineJobListResponse)
async def list_pipeline_jobs(
    kb_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> PipelineJobListResponse:
    valid_statuses = {"queued", "running", "succeeded", "failed", "cancelled"}
    if status is not None and status not in valid_statuses:
        raise HTTPException(status_code=400, detail="Invalid pipeline job status.")
    try:
        jobs = get_rag_service().list_pipeline_jobs(
            kb_id=kb_id,
            status=status,
            limit=limit,
        )
        return PipelineJobListResponse(
            jobs=[PipelineJobPayload.model_validate(item) for item in jobs],
            job_count=len(jobs),
        )
    except KnowledgeBaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/pipeline/jobs/{job_id}", response_model=PipelineJobPayload)
async def get_pipeline_job(job_id: str) -> PipelineJobPayload:
    try:
        job = get_rag_service().get_pipeline_job(job_id)
        return PipelineJobPayload.model_validate(get_rag_service().pipeline_job_payload(job))
    except PipelineJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/pipeline/jobs/{job_id}/cancel", response_model=PipelineJobPayload)
async def cancel_pipeline_job(job_id: str) -> PipelineJobPayload:
    try:
        job = get_rag_service().request_pipeline_job_cancel(job_id)
        get_pipeline_executor().notify()
        return PipelineJobPayload.model_validate(job)
    except PipelineJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PipelineJobStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/pipeline/jobs/{job_id}/retry", response_model=PipelineJobPayload)
async def retry_pipeline_job(job_id: str) -> PipelineJobPayload:
    try:
        job = get_rag_service().retry_pipeline_job(job_id)
        get_pipeline_executor().notify()
        return PipelineJobPayload.model_validate(job)
    except PipelineJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PipelineJobStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/knowledge-write-proposals")
async def list_knowledge_write_proposals(
    kb_id: str | None = None,
    status: str | None = None,
    source_xpert_id: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    try:
        items = get_rag_service().list_knowledge_write_proposals(
            kb_id=kb_id,
            status=status,
            source_xpert_id=source_xpert_id,
            limit=limit,
        )
        return {"proposals": items, "proposal_count": len(items)}
    except KnowledgeBaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/knowledge-write-proposals/{proposal_id}")
async def get_knowledge_write_proposal(proposal_id: str) -> dict[str, Any]:
    try:
        return get_rag_service().get_knowledge_write_proposal(proposal_id)
    except KnowledgeWriteProposalNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/knowledge-write-proposals/{proposal_id}")
async def update_knowledge_write_proposal(
    proposal_id: str,
    payload: KnowledgeWriteProposalUpdateRequest,
) -> dict[str, Any]:
    try:
        return get_rag_service().update_knowledge_write_proposal(
            proposal_id,
            expected_revision=payload.expected_revision,
            title=payload.title,
            content=payload.content,
            tags=payload.tags,
        )
    except KnowledgeWriteProposalNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KnowledgeWriteProposalConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/knowledge-write-proposals/{proposal_id}/approve")
async def approve_knowledge_write_proposal(
    proposal_id: str,
    payload: KnowledgeWriteProposalDecisionRequest,
) -> dict[str, Any]:
    try:
        proposal = get_rag_service().approve_knowledge_write_proposal(
            proposal_id,
            expected_revision=payload.expected_revision,
        )
        get_pipeline_executor().notify()
        return proposal
    except KnowledgeWriteProposalNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KnowledgeWriteProposalConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (
        KnowledgeBaseNotFoundError,
        PipelineDraftValidationError,
        PipelineGraphRevisionError,
        PipelineJobNotFoundError,
        PipelineJobStateError,
        PipelineVersionNotFoundError,
        DocumentNotFoundError,
    ) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/knowledge-write-proposals/{proposal_id}/reject")
async def reject_knowledge_write_proposal(
    proposal_id: str,
    payload: KnowledgeWriteProposalDecisionRequest,
) -> dict[str, Any]:
    try:
        return get_rag_service().reject_knowledge_write_proposal(
            proposal_id,
            expected_revision=payload.expected_revision,
            reason=payload.reason,
        )
    except KnowledgeWriteProposalNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KnowledgeWriteProposalConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/evaluation-sets")
async def list_evaluation_sets(kb_id: str) -> dict[str, Any]:
    _require_knowledge_base(kb_id)
    items = get_evaluation_store().list_sets(kb_id)
    return {"evaluation_sets": items, "evaluation_set_count": len(items)}


@router.post("/evaluation-sets")
async def create_evaluation_set(payload: EvaluationSetCreateRequest) -> dict[str, Any]:
    _require_knowledge_base(payload.kb_id)
    return get_evaluation_store().create_set(
        payload.kb_id,
        payload.name,
        payload.description,
    )


@router.get("/evaluation-sets/{eval_set_id}")
async def get_evaluation_set(eval_set_id: str) -> dict[str, Any]:
    try:
        return get_evaluation_store().get_set(eval_set_id)
    except EvaluationSetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/evaluation-sets/{eval_set_id}")
async def update_evaluation_set(
    eval_set_id: str,
    payload: EvaluationSetUpdateRequest,
) -> dict[str, Any]:
    try:
        return get_evaluation_store().update_set(
            eval_set_id,
            expected_revision=payload.expected_revision,
            name=payload.name,
            description=payload.description,
            status=payload.status,
        )
    except EvaluationSetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except EvaluationRevisionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/evaluation-sets/{eval_set_id}/cases")
async def add_evaluation_case(
    eval_set_id: str,
    payload: EvaluationCaseUpdateRequest,
) -> dict[str, Any]:
    try:
        return get_evaluation_store().add_cases(
            eval_set_id,
            expected_revision=payload.expected_revision,
            cases=[payload.case.model_dump()],
        )
    except EvaluationSetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except EvaluationRevisionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/evaluation-sets/{eval_set_id}/cases/{case_id}")
async def update_evaluation_case(
    eval_set_id: str,
    case_id: str,
    payload: EvaluationCaseUpdateRequest,
) -> dict[str, Any]:
    try:
        return get_evaluation_store().update_case(
            eval_set_id,
            case_id,
            expected_revision=payload.expected_revision,
            values=payload.case.model_dump(),
        )
    except EvaluationSetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except EvaluationRevisionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/evaluation-sets/{eval_set_id}/cases/{case_id}")
async def delete_evaluation_case(
    eval_set_id: str,
    case_id: str,
    expected_revision: int,
) -> dict[str, Any]:
    try:
        return get_evaluation_store().delete_case(
            eval_set_id,
            case_id,
            expected_revision=expected_revision,
        )
    except EvaluationSetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except EvaluationRevisionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/evaluation-sets/{eval_set_id}/import")
async def import_evaluation_cases(
    eval_set_id: str,
    expected_revision: int,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    content = await file.read(1024 * 1024 + 1)
    if len(content) > 1024 * 1024:
        raise HTTPException(status_code=413, detail="Evaluation import is limited to 1 MB.")
    try:
        text = content.decode("utf-8-sig")
        if Path(file.filename or "").suffix.lower() == ".json":
            raw = json.loads(text)
            rows = raw.get("cases") if isinstance(raw, dict) else raw
        else:
            rows = []
            for row in csv.DictReader(io.StringIO(text)):
                references = json.loads(str(row.get("expected_refs") or "[]"))
                tags = [item.strip() for item in str(row.get("tags") or "").split(",") if item.strip()]
                rows.append(
                    {
                        "query": row.get("query"),
                        "expected_refs": references,
                        "tags": tags,
                        "notes": row.get("notes") or "",
                    }
                )
        if not isinstance(rows, list):
            raise ValueError("Evaluation import must contain a list of cases.")
        validated = [EvaluationCaseInput.model_validate(item).model_dump() for item in rows]
        return get_evaluation_store().add_cases(
            eval_set_id,
            expected_revision=expected_revision,
            cases=validated,
        )
    except EvaluationSetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except EvaluationRevisionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/evaluation-gate/{kb_id}")
async def get_evaluation_gate(kb_id: str) -> dict[str, Any]:
    _require_knowledge_base(kb_id)
    return get_evaluation_store().get_gate_policy(kb_id)


@router.patch("/evaluation-gate/{kb_id}")
async def update_evaluation_gate(
    kb_id: str,
    payload: EvaluationGateUpdateRequest,
) -> dict[str, Any]:
    _require_knowledge_base(kb_id)
    try:
        return get_evaluation_store().set_gate_policy(kb_id, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/evaluation-runs")
async def create_evaluation_run(payload: EvaluationRunCreateRequest) -> dict[str, Any]:
    try:
        evaluation_set = get_evaluation_store().get_set(payload.eval_set_id)
        if evaluation_set.get("status") != "active" or not evaluation_set.get("cases"):
            raise ValueError("Evaluation set must be active and contain at least one case.")
        version_ids = [target.version_id for target in payload.targets]
        if len(set(version_ids)) != len(version_ids):
            raise ValueError("Evaluation targets must use distinct version IDs.")
        if any(k < 1 or k > 50 for k in payload.ks):
            raise ValueError("Evaluation ks must contain values between 1 and 50.")
        evaluation_ks = sorted({*payload.ks, 5, 10})
        if payload.baseline_version_id and payload.baseline_version_id not in version_ids:
            raise ValueError("baseline_version_id must be included in targets.")
        targets: list[dict[str, Any]] = []
        for target in payload.targets:
            version = get_rag_service().get_pipeline_version(target.version_id)
            if version.get("kb_id") != evaluation_set.get("kb_id"):
                raise ValueError("All evaluation versions must belong to the evaluation knowledge base.")
            if version.get("status") not in {"ready", "active"}:
                raise ValueError("Only ready or active knowledge versions can be evaluated.")
            targets.append(
                {
                    "target_id": target.version_id,
                    "version_id": target.version_id,
                    "version": int(version["version"]),
                    "label": target.label or f"v{version['version']}",
                    "retrieval": target.retrieval.model_dump(exclude_none=True) if target.retrieval else {},
                }
            )
        run = get_evaluation_store().create_run(
            evaluation_set=evaluation_set,
            targets=targets,
            baseline_version_id=payload.baseline_version_id,
            ks=evaluation_ks,
            gate_policy=get_evaluation_store().get_gate_policy(str(evaluation_set["kb_id"])),
        )
        get_evaluation_executor().notify()
        return run
    except EvaluationSetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PipelineVersionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/evaluation-runs")
async def list_evaluation_runs(
    kb_id: str,
    status: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    _require_knowledge_base(kb_id)
    runs = get_evaluation_store().list_runs(kb_id, status=status, limit=min(max(limit, 1), 100))
    return {"evaluation_runs": runs, "evaluation_run_count": len(runs)}


@router.get("/evaluation-runs/{run_id}")
async def get_evaluation_run(run_id: str) -> dict[str, Any]:
    try:
        return get_evaluation_store().get_run(run_id)
    except EvaluationRunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/evaluation-runs/{run_id}/cancel")
async def cancel_evaluation_run(run_id: str) -> dict[str, Any]:
    try:
        run = get_evaluation_store().request_cancel(run_id)
        get_evaluation_executor().notify()
        return run
    except EvaluationRunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except EvaluationStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/pipeline/versions", response_model=PipelineVersionListResponse)
async def list_pipeline_versions(kb_id: str) -> PipelineVersionListResponse:
    try:
        versions = get_rag_service().list_pipeline_versions(kb_id)
        active = next((item["version_id"] for item in versions if item["active"]), None)
        return PipelineVersionListResponse(
            versions=[PipelineVersionPayload.model_validate(item) for item in versions],
            version_count=len(versions),
            active_version_id=active,
        )
    except KnowledgeBaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/pipeline/versions/{version_id}", response_model=PipelineVersionPayload)
async def get_pipeline_version(version_id: str) -> PipelineVersionPayload:
    try:
        version = get_rag_service().get_pipeline_version(version_id)
        active = get_rag_service().get_active_pipeline_version(str(version["kb_id"]))
        return PipelineVersionPayload.model_validate(
            get_rag_service().pipeline_version_payload(
                version,
                active_id=active["version_id"] if active else None,
            )
        )
    except PipelineVersionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/pipeline/versions/{version_id}/query",
    response_model=PipelineVersionQueryResponse,
)
async def query_pipeline_version(
    version_id: str,
    payload: PipelineVersionQueryRequest,
) -> PipelineVersionQueryResponse:
    try:
        result = await get_rag_service().query_pipeline_version(
            version_id,
            payload.question,
            top_k=payload.top_k,
            retrieval=(
                payload.retrieval.model_dump(exclude_none=True)
                if payload.retrieval
                else None
            ),
        )
        version = get_rag_service().get_pipeline_version(version_id)
        await get_pipeline_executor().record_job_event(
            str(version["job_id"]),
            event_type="knowledge_pipeline.version_previewed",
            title="Candidate index previewed",
            summary=f"Preview returned {len(result.get('sources', []))} sources.",
            metadata={"version_id": version_id, "source_count": len(result.get("sources", []))},
        )
        return PipelineVersionQueryResponse.model_validate(result)
    except PipelineVersionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/pipeline/versions/{version_id}/activate", response_model=PipelineVersionPayload)
async def activate_pipeline_version(
    version_id: str,
    payload: PipelineActivationRequest | None = None,
) -> PipelineVersionPayload:
    try:
        stored = get_rag_service().get_pipeline_version(version_id)
        get_evaluation_store().assert_promotion_allowed(
            kb_id=str(stored["kb_id"]),
            version_id=version_id,
            evaluation_run_id=payload.evaluation_run_id if payload else None,
            require_passed_run=bool(stored.get("promotion_required")),
        )
        version = get_rag_service().activate_pipeline_version(version_id)
        await get_pipeline_executor().record_job_event(
            str(stored["job_id"]),
            event_type="knowledge_pipeline.version_activated",
            title="Knowledge index activated",
            summary=f"Version v{version['version']} is now active.",
            metadata={"version_id": version_id, "version": version["version"]},
        )
        return PipelineVersionPayload.model_validate(version)
    except PipelineVersionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except EvaluationPromotionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/pipeline/versions/{version_id}/promote", response_model=PipelineVersionPayload)
async def promote_pipeline_version(
    version_id: str,
    payload: PipelineActivationRequest,
) -> PipelineVersionPayload:
    try:
        stored = get_rag_service().get_pipeline_version(version_id)
        get_evaluation_store().assert_promotion_allowed(
            kb_id=str(stored["kb_id"]),
            version_id=version_id,
            evaluation_run_id=payload.evaluation_run_id,
            require_passed_run=True,
        )
        version = get_rag_service().activate_pipeline_version(version_id)
        await get_pipeline_executor().record_job_event(
            str(stored["job_id"]),
            event_type="knowledge_pipeline.version_promoted",
            title="Evaluated knowledge index promoted",
            summary=f"Version v{version['version']} passed the evaluation gate and is active.",
            metadata={
                "version_id": version_id,
                "version": version["version"],
                "evaluation_run_id": payload.evaluation_run_id,
            },
        )
        return PipelineVersionPayload.model_validate(version)
    except PipelineVersionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except EvaluationPromotionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/pipeline/citations", response_model=CitationAnchorResponse)
async def create_pipeline_citations(payload: CitationAnchorRequest) -> CitationAnchorResponse:
    try:
        citations = await get_rag_service().create_pipeline_citations(
            payload.kb_id,
            payload.question,
            top_k=payload.top_k,
            retrieval=(
                payload.retrieval.model_dump(exclude_none=True)
                if payload.retrieval
                else None
            ),
        )
        return CitationAnchorResponse(
            kb_id=payload.kb_id,
            citations=[CitationAnchorPayload.model_validate(item) for item in citations],
            citation_count=len(citations),
        )
    except KnowledgeBaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/query", response_model=RagQueryResponse)
async def query_knowledge_base(payload: RagQueryRequest) -> RagQueryResponse:
    try:
        result = await get_rag_service().query(
            payload.kb_id,
            payload.question,
            top_k=payload.top_k,
            retrieval=(
                payload.retrieval.model_dump(exclude_none=True)
                if payload.retrieval
                else None
            ),
        )
        return RagQueryResponse.model_validate(result)
    except KnowledgeBaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
