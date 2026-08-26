from __future__ import annotations

import asyncio
import hashlib
import io
import json
import mimetypes
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

try:
    from server.context_engine import optimize_context
except ModuleNotFoundError:
    from context_engine import optimize_context

try:
    from server.file_assets.contracts import FileInputKind, FilePurpose
    from server.file_assets.output_service import get_file_output_service
    from server.file_assets.service import FileAssetServiceError, get_file_asset_service
    from server.file_assets.validation import FileUploadValidator
except ModuleNotFoundError:
    from file_assets.contracts import FileInputKind, FilePurpose
    from file_assets.output_service import get_file_output_service
    from file_assets.service import FileAssetServiceError, get_file_asset_service
    from file_assets.validation import FileUploadValidator

from .document_parser import (
    DocumentParseError,
    parse_document,
    parse_document_structured,
    supported_extensions,
)
from .document_processor import ProcessedDocument, StructuredDocumentProcessor
from .embedder import EmbeddingClient, EmbeddingError
from .lexical_store import LexicalChunk, LexicalSearchResult, SqliteLexicalStore
from .reranker import RerankDocument, RerankService
from .retrieval import (
    RetrievalCandidate,
    RetrievalConfig,
    fuse_rankings,
    select_candidates,
)
from .source_metadata import normalize_heading_path
from .processor_generator import (
    ProcessorGenerationError,
    ProcessorGenerationOutcome,
    ProcessorGenerationService,
)
from .pipeline_graph import (
    GraphValidationIssue,
    KnowledgePipelineCompileResult,
    PipelineGraphValidationError,
    compile_pipeline_graph,
    default_pipeline_graph,
    sync_graph_from_draft,
    validate_pipeline_graph,
)
from .splitter import DEFAULT_SEPARATORS, ParentChildTextSplitter, TextSplitter
from .vector_store import SearchResult, VectorChunk, VectorStore, create_vector_store
from .vision_processor import (
    SUPPORTED_IMAGE_EXTENSIONS,
    VisionProcessingError,
    VisionUnderstandingService,
)


def _safe_env_int(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return max(minimum, default)


MAX_DOCUMENT_WARNINGS = 20
MAX_DOCUMENT_WARNING_CHARACTERS = 500
MAX_DOCUMENT_WARNINGS_CHARACTERS = 4_000
MAX_FILE_OUTPUT_SECTION_SOURCES = 2_000
HASH_EMBEDDING_MODEL = "deterministic-hash-v1"
HASH_EMBEDDING_MODEL_ALIASES = {
    "hash",
    "modelmirror-hash-v1",
    HASH_EMBEDDING_MODEL,
}
EMBEDDING_PROVIDER_HASH = "hash"
EMBEDDING_PROVIDER_OPENAI_COMPATIBLE = "openai_compatible"
EMBEDDING_PROVIDER_UNAVAILABLE = "unavailable"
EMBEDDING_SPACE_CONTRACT_VERSION = "modelmirror-provider-embedding-space-v1"
_WARNING_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _bounded_document_warnings(values: Any) -> list[str]:
    """Return deduplicated, single-line parser warnings safe for metadata/API use."""

    if not isinstance(values, (list, tuple)):
        return []
    result: list[str] = []
    used_characters = 0
    for value in values:
        if not isinstance(value, str):
            continue
        clean = _WARNING_CONTROL_CHARACTERS.sub(" ", value)
        clean = " ".join(clean.split())
        clean = re.sub(
            r"(?i)(bearer\s+|api[_-]?key[=:]\s*)\S+",
            r"\1[redacted]",
            clean,
        )
        if not clean:
            continue
        if len(clean) > MAX_DOCUMENT_WARNING_CHARACTERS:
            clean = clean[: MAX_DOCUMENT_WARNING_CHARACTERS - 1].rstrip() + "…"
        remaining = MAX_DOCUMENT_WARNINGS_CHARACTERS - used_characters
        if remaining <= 0:
            break
        if len(clean) > remaining:
            if remaining <= 1:
                break
            clean = clean[: remaining - 1].rstrip() + "…"
        if clean in result:
            continue
        result.append(clean)
        used_characters += len(clean)
        if len(result) >= MAX_DOCUMENT_WARNINGS:
            break
    return result


class RagError(RuntimeError):
    """Base error for local RAG operations."""


class KnowledgeBaseNotFoundError(RagError):
    """Raised when a knowledge base does not exist."""


class KnowledgeBaseDeletionError(KnowledgeBaseNotFoundError):
    """Raised when a tombstoned knowledge base still needs cleanup retry."""


class KnowledgeBaseLockedError(RagError):
    """Raised when a managed benchmark corpus rejects a mutation."""


class DocumentNotFoundError(RagError):
    """Raised when a document does not exist."""


class DocumentDeletionError(RagError):
    """Raised when a tombstoned document still needs cleanup retry."""


class UnsupportedDocumentError(RagError):
    """Raised when an uploaded file cannot be parsed."""


class PipelineDraftValidationError(RagError):
    """Raised when a knowledge pipeline draft config is invalid."""


class PipelineJobNotFoundError(RagError):
    """Raised when a knowledge pipeline job does not exist."""


class PipelineVersionNotFoundError(RagError):
    """Raised when a knowledge index version does not exist."""


class PipelineJobStateError(RagError):
    """Raised when a pipeline job operation is invalid for its current state."""


class PipelineGraphRevisionError(RagError):
    """Raised when a graph save uses a stale optimistic revision."""


class ManagedEmbeddingRouteError(EmbeddingError):
    """Raised with a stable, redacted Managed Embedding failure code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ManagedRagGenerationRouteError(RagError):
    """Stable, redacted failure for a managed RAG generation operation."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        receipt: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.receipt = receipt


class KnowledgeWriteProposalNotFoundError(RagError):
    """Raised when a knowledge write proposal does not exist."""


class KnowledgeWriteProposalConflictError(RagError):
    """Raised when a proposal revision or state no longer matches."""


PIPELINE_STAGE_IDS = {
    "data_source": "stage_data_source",
    "processor": "stage_processor",
    "chunker": "stage_chunker",
    "image_understanding": "stage_image_understanding",
}

PIPELINE_JOB_STAGES = (
    ("load", "读取来源"),
    ("vision", "视觉理解"),
    ("process", "解析文档"),
    ("chunk", "生成分块"),
    ("embed", "生成向量"),
    ("store", "写入候选索引"),
)


class RagService:
    """Local knowledge-base service with parsing, splitting, embedding and RAG query."""

    def __init__(
        self,
        *,
        storage_dir: Path | None = None,
        uploads_dir: Path | None = None,
        embedder: EmbeddingClient | None = None,
        vector_store: VectorStore | None = None,
        lexical_store: SqliteLexicalStore | None = None,
        reranker: RerankService | None = None,
        splitter: TextSplitter | None = None,
        document_processor: StructuredDocumentProcessor | None = None,
        processor_generator: ProcessorGenerationService | None = None,
        vision_processor: VisionUnderstandingService | None = None,
        managed_embedding_gateway: Any | None = None,
        managed_generation_gateway: Any | None = None,
        llm_enabled: bool | None = None,
    ) -> None:
        root = Path(__file__).resolve().parent
        self.storage_dir = storage_dir or Path(os.getenv("RAG_STORAGE_DIR", str(root / "storage")))
        self.uploads_dir = uploads_dir or Path(os.getenv("RAG_UPLOAD_DIR", str(root / "uploads")))
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_path = self.storage_dir / "metadata.json"
        self.pipeline_sources_dir = self.storage_dir / "pipeline_sources"
        self.pipeline_sources_dir.mkdir(parents=True, exist_ok=True)
        self.pipeline_processed_dir = self.storage_dir / "pipeline_processed"
        self.pipeline_processed_dir.mkdir(parents=True, exist_ok=True)
        self.pipeline_vision_dir = self.storage_dir / "pipeline_vision"
        self.pipeline_vision_dir.mkdir(parents=True, exist_ok=True)
        self._metadata_lock = threading.RLock()
        self._document_delete_claims: set[str] = set()
        self._knowledge_base_delete_claims: set[str] = set()
        self._knowledge_base_write_claims: dict[str, int] = {}
        self._analysis_import_claims: set[tuple[str, str]] = set()
        self._output_import_claims: set[tuple[str, str]] = set()
        self.embedder = embedder or EmbeddingClient()
        self.vector_store = vector_store or create_vector_store(self.storage_dir)
        self.lexical_store = lexical_store or SqliteLexicalStore(
            self.storage_dir / "lexical_index.sqlite3"
        )
        self.reranker = reranker or RerankService()
        self.document_processor = document_processor or StructuredDocumentProcessor()
        self.processor_generator = processor_generator or ProcessorGenerationService()
        self.vision_processor = vision_processor or VisionUnderstandingService()
        self.managed_embedding_gateway = managed_embedding_gateway
        self.managed_generation_gateway = managed_generation_gateway
        self.splitter = splitter or TextSplitter(
            chunk_size=int(os.getenv("RAG_CHUNK_SIZE", "500")),
            chunk_overlap=int(os.getenv("RAG_CHUNK_OVERLAP", "50")),
        )
        if llm_enabled is None:
            self.llm_enabled = os.getenv("RAG_DISABLE_LLM", "").lower() not in {"1", "true", "yes"}
        else:
            self.llm_enabled = llm_enabled

    def create_knowledge_base(
        self,
        name: str,
        *,
        origin: str = "manual",
        catalog_ref: dict[str, Any] | None = None,
        corpus_locked: bool = False,
        provisioning_status: str = "ready",
    ) -> dict[str, Any]:
        """Create a knowledge base and return its metadata."""

        clean_name = name.strip()
        if not clean_name:
            raise ValueError("知识库名称不能为空。")

        metadata = self._read_metadata()
        kb_id = f"kb_{uuid.uuid4().hex}"
        item = {
            "id": kb_id,
            "name": clean_name,
            "origin": str(origin or "manual")[:80],
            "catalog_ref": json.loads(json.dumps(catalog_ref or {})),
            "corpus_locked": bool(corpus_locked),
            "provisioning_status": str(provisioning_status or "ready")[:40],
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        metadata["knowledge_bases"][kb_id] = item
        self._write_metadata(metadata)
        (self.uploads_dir / kb_id).mkdir(parents=True, exist_ok=True)
        return self._kb_payload(item, metadata)

    def list_knowledge_bases(self, *, include_provisioning: bool = False) -> list[dict[str, Any]]:
        """Return all knowledge bases with document counts."""

        metadata = self._read_metadata()
        items = [
            self._kb_payload(item, metadata)
            for item in metadata["knowledge_bases"].values()
            if include_provisioning or item.get("provisioning_status", "ready") == "ready"
        ]
        return sorted(items, key=lambda item: item["created_at"], reverse=True)

    def delete_knowledge_base(self, kb_id: str) -> None:
        """Durably isolate, strictly purge, then forget one knowledge base."""

        with self._metadata_lock:
            metadata = self._read_metadata_unlocked()
            deletion = metadata["knowledge_base_deletions"].get(kb_id)
            if kb_id not in metadata["knowledge_bases"]:
                if isinstance(deletion, dict) and deletion.get("status") == "deleted":
                    return
                raise KnowledgeBaseNotFoundError("Knowledge base not found.")
            if kb_id in self._knowledge_base_delete_claims:
                raise KnowledgeBaseDeletionError(
                    "Knowledge base cleanup is already in progress; retry shortly."
                )
            self._knowledge_base_delete_claims.add(kb_id)
        try:
            self._delete_knowledge_base_claimed(kb_id)
        finally:
            with self._metadata_lock:
                self._knowledge_base_delete_claims.discard(kb_id)

    def _delete_knowledge_base_claimed(self, kb_id: str) -> None:
        requested_at = time.time()
        with self._metadata_lock:
            metadata = self._read_metadata_unlocked()
            knowledge_base = metadata["knowledge_bases"].get(kb_id)
            deletion = metadata["knowledge_base_deletions"].get(kb_id)
            if not isinstance(knowledge_base, dict):
                if isinstance(deletion, dict) and deletion.get("status") == "deleted":
                    return
                raise KnowledgeBaseNotFoundError("Knowledge base not found.")

            deletion = metadata["knowledge_base_deletions"].setdefault(
                kb_id,
                {
                    "tenant_id": "local",
                    "requested_at": requested_at,
                    "deleted_at": None,
                    "status": "deleting",
                    "error_code": None,
                    "document_ids": [],
                    "asset_ids": [],
                },
            )
            deletion["status"] = "deleting"
            deletion["error_code"] = None
            deletion["deleted_at"] = None
            knowledge_base["deletion_status"] = "deleting"
            knowledge_base["deletion_error_code"] = None
            knowledge_base["updated_at"] = requested_at

            document_ids = {
                str(doc_id)
                for doc_id, document in metadata["documents"].items()
                if isinstance(document, dict) and str(document.get("kb_id")) == kb_id
            }
            document_ids.update(str(item) for item in deletion.get("document_ids", []))
            deletion["document_ids"] = sorted(document_ids)
            for doc_id in document_ids:
                document = metadata["documents"].get(doc_id)
                if not isinstance(document, dict):
                    continue
                document["deletion_status"] = "deleting"
                document_deletion = metadata["document_deletions"].setdefault(doc_id, {})
                document_deletion.update(
                    {
                        "tenant_id": "local",
                        "content_hash": str(document.get("content_hash") or ""),
                        "requested_at": float(
                            document_deletion.get("requested_at") or requested_at
                        ),
                        "deleted_at": None,
                        "status": "deleting",
                        "error_code": None,
                    }
                )

            for job in metadata["pipeline_jobs"].values():
                if not isinstance(job, dict) or str(job.get("kb_id")) != kb_id:
                    continue
                job["deletion_invalidated"] = True
                job["deletion_artifacts_purged"] = False
                job["deletion_cleanup_error"] = None
                if job.get("status") == "running":
                    job["cancel_requested"] = True
                elif job.get("status") == "queued":
                    job["status"] = "cancelled"
                    job["cancel_requested"] = True
                    job["completed_at"] = requested_at
                    job["error"] = "Cancelled because the knowledge base was deleted."
            self._write_metadata_unlocked(metadata)

        cleanup_pending = False
        error_code: str | None = None
        asset_service = get_file_asset_service()
        asset_metadata = self._read_metadata()
        deletion_asset_ids = {
            str(item)
            for item in asset_metadata["knowledge_base_deletions"]
            .get(kb_id, {})
            .get("asset_ids", [])
        }
        deletion_asset_ids.update(
            str(document.get("asset_id"))
            for document in asset_metadata["documents"].values()
            if isinstance(document, dict)
            and str(document.get("kb_id")) == kb_id
            and str(document.get("asset_id") or "")
        )
        file_assets_enabled = asset_service.mode in {"shadow", "native"}
        if not file_assets_enabled and deletion_asset_ids:
            cleanup_pending = True
            error_code = "file_asset_store_unavailable"
        elif file_assets_enabled:
            try:
                asset_ids, assets_pending = asset_service.block_and_delete_rag_scope(kb_id)
                with self._metadata_lock:
                    metadata = self._read_metadata_unlocked()
                    current = metadata["knowledge_base_deletions"].setdefault(kb_id, {})
                    current["asset_ids"] = sorted(
                        set(str(item) for item in current.get("asset_ids", []))
                        | set(str(item) for item in asset_ids)
                    )
                    current["asset_scope_blocked"] = True
                    self._write_metadata_unlocked(metadata)
                cleanup_pending = cleanup_pending or assets_pending
                if assets_pending:
                    error_code = "file_asset_cleanup_pending"
            except Exception:
                cleanup_pending = True
                error_code = "file_asset_scope_cleanup_failed"

        current_metadata = self._read_metadata()
        current_document_ids = [
            str(doc_id)
            for doc_id, document in current_metadata["documents"].items()
            if isinstance(document, dict) and str(document.get("kb_id")) == kb_id
        ]
        for doc_id in current_document_ids:
            try:
                self.delete_document(doc_id, allow_locked=True)
            except (DocumentDeletionError, DocumentNotFoundError):
                cleanup_pending = True
                error_code = error_code or "rag_document_cleanup_pending"
            except Exception:
                cleanup_pending = True
                error_code = error_code or "rag_document_cleanup_failed"

        pipeline_pending = self._knowledge_base_pipeline_cleanup_pending(kb_id)
        if pipeline_pending:
            cleanup_pending = True
            error_code = error_code or "rag_pipeline_cleanup_pending"

        with self._metadata_lock:
            active_writes = self._knowledge_base_write_claims.get(kb_id, 0)
        if active_writes:
            cleanup_pending = True
            error_code = error_code or "rag_knowledge_base_write_pending"

        if not active_writes and not pipeline_pending:
            try:
                self._purge_knowledge_base_namespaces_and_uploads(kb_id)
            except Exception:
                cleanup_pending = True
                error_code = error_code or "rag_knowledge_base_cleanup_failed"

        if file_assets_enabled:
            try:
                if not asset_service.rag_scope_cleanup_complete(kb_id):
                    cleanup_pending = True
                    error_code = error_code or "file_asset_cleanup_pending"
            except Exception:
                cleanup_pending = True
                error_code = error_code or "file_asset_cleanup_failed"

        with self._metadata_lock:
            metadata = self._read_metadata_unlocked()
            remaining_documents = [
                doc_id
                for doc_id, document in metadata["documents"].items()
                if isinstance(document, dict) and str(document.get("kb_id")) == kb_id
            ]
            remaining_jobs = [
                job_id
                for job_id, job in metadata["pipeline_jobs"].items()
                if isinstance(job, dict)
                and str(job.get("kb_id")) == kb_id
                and (
                    job.get("status") == "running"
                    or not bool(job.get("deletion_artifacts_purged"))
                )
            ]
            if (
                remaining_documents
                or remaining_jobs
                or self._knowledge_base_write_claims.get(kb_id, 0)
            ):
                cleanup_pending = True
                error_code = error_code or "rag_knowledge_base_cleanup_pending"

            if cleanup_pending:
                knowledge_base = metadata["knowledge_bases"].get(kb_id)
                if isinstance(knowledge_base, dict):
                    knowledge_base["deletion_status"] = "cleanup_pending"
                    knowledge_base["deletion_error_code"] = error_code
                    knowledge_base["updated_at"] = time.time()
                deletion = metadata["knowledge_base_deletions"].setdefault(kb_id, {})
                deletion.update(
                    {
                        "tenant_id": "local",
                        "deleted_at": None,
                        "status": "cleanup_pending",
                        "error_code": error_code
                        or "rag_knowledge_base_cleanup_pending",
                    }
                )
                self._write_metadata_unlocked(metadata)
                raise KnowledgeBaseDeletionError(
                    "Knowledge base was isolated, but cleanup is incomplete; retry deletion."
                )

            metadata["pipeline_drafts"].pop(kb_id, None)
            metadata["pipeline_graphs"].pop(kb_id, None)
            metadata["pipeline_active_versions"].pop(kb_id, None)
            metadata["rag_strategy_recommendations"] = {
                recommendation_id: item
                for recommendation_id, item in metadata[
                    "rag_strategy_recommendations"
                ].items()
                if not isinstance(item, dict) or str(item.get("kb_id")) != kb_id
            }
            metadata["knowledge_write_proposals"] = {
                proposal_id: item
                for proposal_id, item in metadata["knowledge_write_proposals"].items()
                if not isinstance(item, dict) or str(item.get("kb_id")) != kb_id
            }
            metadata["pipeline_versions"] = {
                version_id: item
                for version_id, item in metadata["pipeline_versions"].items()
                if not isinstance(item, dict) or str(item.get("kb_id")) != kb_id
            }
            metadata["pipeline_jobs"] = {
                job_id: item
                for job_id, item in metadata["pipeline_jobs"].items()
                if not isinstance(item, dict) or str(item.get("kb_id")) != kb_id
            }
            metadata["knowledge_bases"].pop(kb_id, None)
            deletion = metadata["knowledge_base_deletions"].setdefault(kb_id, {})
            deletion.update(
                {
                    "tenant_id": "local",
                    "deleted_at": time.time(),
                    "status": "deleted",
                    "error_code": None,
                }
            )
            self._write_metadata_unlocked(metadata)

    def _knowledge_base_pipeline_cleanup_pending(self, kb_id: str) -> bool:
        metadata = self._read_metadata()
        job_ids = [
            str(job_id)
            for job_id, job in metadata["pipeline_jobs"].items()
            if isinstance(job, dict) and str(job.get("kb_id")) == kb_id
        ]
        pending = False
        for job_id in job_ids:
            job = self.get_pipeline_job(job_id)
            if job.get("status") == "running":
                pending = True
                continue
            if job.get("deletion_artifacts_purged"):
                continue
            try:
                self.cleanup_invalidated_pipeline_job(job_id)
            except Exception:
                pending = True
        refreshed = self._read_metadata()
        return pending or any(
            isinstance(refreshed["pipeline_jobs"].get(job_id), dict)
            and (
                refreshed["pipeline_jobs"][job_id].get("status") == "running"
                or not refreshed["pipeline_jobs"][job_id].get(
                    "deletion_artifacts_purged"
                )
            )
            for job_id in job_ids
        )

    def _purge_knowledge_base_namespaces_and_uploads(self, kb_id: str) -> None:
        metadata = self._read_metadata()
        namespaces = {
            kb_id,
            *(
                str(version.get("namespace") or "")
                for version in metadata["pipeline_versions"].values()
                if isinstance(version, dict) and str(version.get("kb_id")) == kb_id
            ),
        }
        for namespace in namespaces:
            if not namespace:
                continue
            self.vector_store.delete_knowledge_base(namespace)
            self.lexical_store.delete_namespace(namespace)
        upload_dir = self.uploads_dir / kb_id
        if upload_dir.exists():
            shutil.rmtree(upload_dir)

    async def upload_document(
        self,
        kb_id: str,
        filename: str,
        content: bytes,
        declared_media_type: str | None = None,
        allow_locked: bool = False,
        pipeline_only: bool = False,
    ) -> dict[str, Any]:
        """Hold a write claim so knowledge-base deletion cannot finalize mid-upload."""

        with self._metadata_lock:
            metadata = self._read_metadata_unlocked()
            self._ensure_kb_exists(metadata, kb_id)
            self._assert_corpus_mutable(metadata, kb_id, allow_locked=allow_locked)
            self._knowledge_base_write_claims[kb_id] = (
                self._knowledge_base_write_claims.get(kb_id, 0) + 1
            )
        try:
            return await self._upload_document_claimed(
                kb_id,
                filename,
                content,
                declared_media_type=declared_media_type,
                pipeline_only=pipeline_only,
            )
        finally:
            with self._metadata_lock:
                remaining = self._knowledge_base_write_claims.get(kb_id, 0) - 1
                if remaining > 0:
                    self._knowledge_base_write_claims[kb_id] = remaining
                else:
                    self._knowledge_base_write_claims.pop(kb_id, None)

    async def _upload_document_claimed(
        self,
        kb_id: str,
        filename: str,
        content: bytes,
        declared_media_type: str | None = None,
        pipeline_only: bool = False,
    ) -> dict[str, Any]:
        """Save, parse, split, embed and index an uploaded document."""

        metadata = self._read_metadata()
        self._ensure_kb_exists(metadata, kb_id)
        if kb_id not in metadata["knowledge_bases"]:
            raise KnowledgeBaseNotFoundError("知识库不存在。")

        extension = Path(filename).suffix.lower()
        if extension not in {*supported_extensions(), *SUPPORTED_IMAGE_EXTENSIONS}:
            raise UnsupportedDocumentError(f"暂不支持该文件格式：{extension or filename}")

        is_image = extension in SUPPORTED_IMAGE_EXTENSIONS
        visual_metadata: dict[str, Any] = {}
        if is_image:
            try:
                visual_metadata = self.vision_processor.validate_image_bytes(content, filename)
            except VisionProcessingError as exc:
                raise UnsupportedDocumentError(str(exc)) from exc
            expected_image_media_types = {
                ".png": {"image/png"},
                ".jpg": {"image/jpeg"},
                ".jpeg": {"image/jpeg"},
                ".webp": {"image/webp"},
            }[extension]
            if (
                declared_media_type
                and declared_media_type not in expected_image_media_types
            ):
                raise UnsupportedDocumentError(
                    "Image MIME type does not match the selected file extension."
                )
        else:
            FileUploadValidator().validate_stream(
                io.BytesIO(content),
                purpose=FilePurpose.RAG,
                input_kind=FileInputKind.DOCUMENT,
                filename=filename,
                declared_media_type=declared_media_type,
            )

        doc_id = f"doc_{uuid.uuid4().hex}"
        safe_name = _safe_filename(filename)
        target_dir = self.uploads_dir / kb_id
        target_dir.mkdir(parents=True, exist_ok=True)
        stored_path = target_dir / f"{doc_id}_{safe_name}"
        stored_path.write_bytes(content)

        pipeline_required = is_image or pipeline_only
        chunks: list[str] = []
        chunk_sources: list[dict[str, Any]] = []
        embeddings: list[list[float]] = []
        document_warnings: list[str] = []
        if not is_image and not pipeline_only:
            try:
                if extension in {".xlsx", ".docx", ".pptx"}:
                    if extension in {".docx", ".pptx"}:
                        parsed = await asyncio.to_thread(
                            parse_document_structured,
                            stored_path,
                            filename,
                        )
                    else:
                        parsed = parse_document_structured(stored_path, filename)
                    document_warnings = _bounded_document_warnings(parsed.warnings)
                    for section in parsed.sections:
                        heading_path = list(
                            normalize_heading_path(section.heading_path)
                        )
                        heading_prefix = " > ".join(heading_path)
                        section_text = section.text
                        if (
                            heading_prefix
                            and section.text.strip() != heading_path[-1]
                            and not section.text.lstrip().startswith(heading_prefix)
                        ):
                            section_text = f"{heading_prefix}\n{section.text}"
                        section_chunks = self.splitter.split_text(section_text)
                        chunks.extend(section_chunks)
                        chunk_sources.extend(
                            {
                                "page_number": section.page,
                                "slide": section.slide,
                                "sheet": section.sheet,
                                "row_range": section.row_range,
                                "heading_path": heading_path,
                            }
                            for _chunk in section_chunks
                        )
                else:
                    text = parse_document(stored_path, filename)
                    chunks = self.splitter.split_text(text)
                    chunk_sources = [
                        {
                            "page_number": None,
                            "slide": None,
                            "sheet": None,
                            "row_range": None,
                            "heading_path": [],
                        }
                        for _chunk in chunks
                    ]
                if not chunks:
                    raise UnsupportedDocumentError("文档没有可索引的文本片段。")
                embeddings = await self.embedder.embed_texts(chunks)
            except DocumentParseError as exc:
                if extension != ".pdf":
                    stored_path.unlink(missing_ok=True)
                    raise
                try:
                    visual_metadata = self.vision_processor.validate_pdf_bytes(content)
                except VisionProcessingError:
                    stored_path.unlink(missing_ok=True)
                    raise UnsupportedDocumentError(str(exc)) from exc
                pipeline_required = True
            except UnsupportedDocumentError as exc:
                if extension != ".pdf":
                    stored_path.unlink(missing_ok=True)
                    raise
                try:
                    visual_metadata = self.vision_processor.validate_pdf_bytes(content)
                except VisionProcessingError:
                    stored_path.unlink(missing_ok=True)
                    raise UnsupportedDocumentError(str(exc)) from exc
                pipeline_required = True
            except EmbeddingError as exc:
                stored_path.unlink(missing_ok=True)
                raise UnsupportedDocumentError(str(exc)) from exc

        vector_chunks = [
            VectorChunk(
                id=f"{doc_id}_chunk_{index}",
                kb_id=kb_id,
                doc_id=doc_id,
                document_name=filename,
                text=chunk,
                embedding=embeddings[index],
                chunk_index=index,
                chunk_type=("table" if extension == ".xlsx" else "standard"),
                page_number=chunk_sources[index]["page_number"],
                slide=chunk_sources[index]["slide"],
                heading_path=tuple(chunk_sources[index]["heading_path"]),
                sheet=chunk_sources[index]["sheet"],
                row_range=chunk_sources[index]["row_range"],
            )
            for index, chunk in enumerate(chunks)
        ]
        if vector_chunks:
            self.vector_store.add_chunks(vector_chunks)

        asset_id: str | None = None
        asset_store_mode = os.getenv("FILE_ASSET_STORE_MODE", "legacy").strip().lower()
        if asset_store_mode in {"shadow", "native"}:
            try:
                registered = get_file_asset_service().upload(
                    io.BytesIO(content),
                    purpose=FilePurpose.RAG,
                    scope_id=kb_id,
                    filename=filename,
                    declared_media_type=declared_media_type,
                )
                asset_id = registered.asset_id
            except FileAssetServiceError:
                document_warnings = _bounded_document_warnings(
                    [
                        *document_warnings,
                        "Unified file asset registration failed; legacy RAG storage remains active.",
                    ]
                )

        document = {
            "id": doc_id,
            "kb_id": kb_id,
            "filename": filename,
            "stored_path": str(stored_path),
            "size": len(content),
            "chunk_count": len(chunks),
            "content_type": mimetypes.guess_type(filename)[0] or "application/octet-stream",
            "ingestion_status": "pipeline_required" if pipeline_required else "indexed_legacy",
            "visual_candidate": is_image or bool(visual_metadata),
            "visual_metadata": visual_metadata,
            "warnings": document_warnings,
            "content_hash": hashlib.sha256(content).hexdigest(),
            "created_at": time.time(),
        }
        if asset_id:
            document["asset_id"] = asset_id
        with self._metadata_lock:
            latest = self._read_metadata_unlocked()
            knowledge_base = latest["knowledge_bases"].get(kb_id)
            if not isinstance(knowledge_base, dict) or knowledge_base.get(
                "deletion_status"
            ):
                document["deletion_status"] = "deleting"
                latest["documents"][doc_id] = document
                deletion = latest["knowledge_base_deletions"].setdefault(
                    kb_id,
                    {
                        "tenant_id": "local",
                        "requested_at": time.time(),
                        "deleted_at": None,
                        "status": "cleanup_pending",
                        "error_code": "rag_knowledge_base_write_pending",
                        "document_ids": [],
                        "asset_ids": [],
                    },
                )
                deletion["document_ids"] = sorted(
                    set(str(item) for item in deletion.get("document_ids", []))
                    | {doc_id}
                )
                if asset_id:
                    deletion["asset_ids"] = sorted(
                        set(str(item) for item in deletion.get("asset_ids", []))
                        | {asset_id}
                    )
                latest["document_deletions"][doc_id] = {
                    "tenant_id": "local",
                    "content_hash": str(document.get("content_hash") or ""),
                    "requested_at": time.time(),
                    "deleted_at": None,
                    "status": "deleting",
                    "error_code": None,
                }
                self._write_metadata_unlocked(latest)
                raise KnowledgeBaseDeletionError(
                    "Knowledge base deletion started during upload; uploaded data was isolated."
                )
            latest["documents"][doc_id] = document
            knowledge_base["updated_at"] = time.time()
            self._write_metadata_unlocked(latest)
        return self._document_payload(document)

    async def import_file_analysis(
        self,
        kb_id: str,
        *,
        asset_id: str,
        analysis_artifact_id: str,
        chat_scope_id: str,
    ) -> dict[str, Any]:
        """Persist one confirmed Chat analysis result without copying its original."""

        clean_artifact = str(analysis_artifact_id or "").strip()
        if not clean_artifact:
            raise UnsupportedDocumentError("识别结果标识无效。")
        claim = (kb_id, clean_artifact)
        with self._metadata_lock:
            metadata = self._read_metadata_unlocked()
            self._ensure_kb_exists(metadata, kb_id)
            existing = next(
                (
                    document
                    for document in metadata["documents"].values()
                    if isinstance(document, dict)
                    and str(document.get("kb_id")) == kb_id
                    and str(document.get("analysis_artifact_id")) == clean_artifact
                    and not document.get("deletion_status")
                ),
                None,
            )
            if existing is not None:
                return self._document_payload(existing)
            if claim in self._analysis_import_claims:
                raise KnowledgeBaseDeletionError(
                    "The same analysis result is already being saved; retry shortly."
                )
            self._analysis_import_claims.add(claim)
            self._knowledge_base_write_claims[kb_id] = (
                self._knowledge_base_write_claims.get(kb_id, 0) + 1
            )

        doc_id = "doc_analysis_" + hashlib.sha256(
            f"{kb_id}\0{clean_artifact}".encode("utf-8")
        ).hexdigest()[:24]
        stored_path: Path | None = None
        derived_asset_id: str | None = None
        indexed = False
        try:
            analysis = await asyncio.to_thread(
                get_file_asset_service().resolve_analysis_artifact,
                asset_id,
                clean_artifact,
                scope_id=chat_scope_id,
            )
            if not analysis.sections:
                raise UnsupportedDocumentError("识别结果没有可保存的文字内容。")

            filename = _safe_filename(analysis.source_filename or "analysis.txt")
            derived_name = f"{Path(filename).stem or 'analysis'}.analysis.txt"
            chunks: list[str] = []
            sources: list[tuple[int, str]] = []
            persistent_sections: list[str] = []
            for section in analysis.sections:
                source_label = (
                    f"识别来源：第 {section.page} 页 · {analysis.mode.value} · "
                    f"{analysis.connection_name}/{analysis.model_id}"
                )
                persistent_sections.append(f"[{source_label}]\n{section.text}")
                section_chunks = self.splitter.split_text(section.text)
                chunks.extend(section_chunks)
                sources.extend((section.page, section.kind) for _ in section_chunks)
            if not chunks:
                raise UnsupportedDocumentError("识别结果没有可索引的文字片段。")

            embeddings = await self.embedder.embed_texts(chunks)
            vector_chunks = [
                VectorChunk(
                    id=f"{doc_id}_chunk_{index}",
                    kb_id=kb_id,
                    doc_id=doc_id,
                    document_name=filename,
                    text=text,
                    embedding=embeddings[index],
                    chunk_index=index,
                    chunk_type="visual_analysis",
                    page_number=sources[index][0],
                    visual_kind=sources[index][1],
                    source_block_id=clean_artifact,
                )
                for index, text in enumerate(chunks)
            ]
            lexical_chunks = [
                LexicalChunk(
                    chunk_id=item.id,
                    namespace=kb_id,
                    doc_id=doc_id,
                    document_name=filename,
                    text=item.text,
                    chunk_index=item.chunk_index,
                    chunk_type=item.chunk_type,
                    page_number=item.page_number,
                    visual_kind=item.visual_kind,
                    source_block_id=item.source_block_id,
                )
                for item in vector_chunks
            ]
            derived_bytes = "\n\n".join(persistent_sections).encode("utf-8")
            target_dir = self.uploads_dir / kb_id
            target_dir.mkdir(parents=True, exist_ok=True)
            stored_path = target_dir / f"{doc_id}_{derived_name}"
            stored_path.write_bytes(derived_bytes)

            registered = await asyncio.to_thread(
                get_file_asset_service().upload,
                io.BytesIO(derived_bytes),
                purpose=FilePurpose.RAG,
                scope_id=kb_id,
                filename=derived_name,
                declared_media_type="text/plain",
            )
            derived_asset_id = registered.asset_id
            self.vector_store.add_chunks(vector_chunks)
            self.lexical_store.add_chunks(lexical_chunks)
            indexed = True

            now = time.time()
            document = {
                "id": doc_id,
                "kb_id": kb_id,
                "filename": filename,
                "stored_path": str(stored_path),
                "size": len(derived_bytes),
                "chunk_count": len(chunks),
                "content_type": "text/plain",
                "ingestion_status": "indexed_file_analysis",
                "visual_candidate": False,
                "warnings": _bounded_document_warnings(analysis.warnings),
                "content_hash": hashlib.sha256(derived_bytes).hexdigest(),
                "asset_id": derived_asset_id,
                "analysis_artifact_id": clean_artifact,
                "analysis_source": {
                    "source_filename": filename,
                    "source_sha256": analysis.source_sha256,
                    "selected_pages": list(analysis.selected_pages),
                    "mode": analysis.mode.value,
                    "connection_name": analysis.connection_name,
                    "model_id": analysis.model_id,
                    "failed_pages": list(analysis.failed_pages),
                    "truncated": analysis.truncated,
                },
                "created_at": now,
            }
            with self._metadata_lock:
                latest = self._read_metadata_unlocked()
                self._ensure_kb_exists(latest, kb_id)
                existing = next(
                    (
                        item
                        for item in latest["documents"].values()
                        if isinstance(item, dict)
                        and str(item.get("kb_id")) == kb_id
                        and str(item.get("analysis_artifact_id")) == clean_artifact
                        and not item.get("deletion_status")
                    ),
                    None,
                )
                if existing is not None:
                    raise KnowledgeBaseDeletionError(
                        "The analysis result was saved concurrently; retry to read it."
                    )
                latest["documents"][doc_id] = document
                latest["knowledge_bases"][kb_id]["updated_at"] = now
                self._write_metadata_unlocked(latest)
            return self._document_payload(document)
        except Exception:
            if indexed:
                self.vector_store.delete_document(doc_id)
                self.lexical_store.delete_document(doc_id)
            if stored_path is not None:
                stored_path.unlink(missing_ok=True)
            if derived_asset_id:
                try:
                    get_file_asset_service().delete_asset(
                        derived_asset_id,
                        purpose=FilePurpose.RAG,
                        scope_id=kb_id,
                    )
                except Exception:
                    pass
            raise
        finally:
            with self._metadata_lock:
                self._analysis_import_claims.discard(claim)
                remaining = self._knowledge_base_write_claims.get(kb_id, 0) - 1
                if remaining > 0:
                    self._knowledge_base_write_claims[kb_id] = remaining
                else:
                    self._knowledge_base_write_claims.pop(kb_id, None)

    async def import_file_output(
        self,
        kb_id: str,
        *,
        output_id: str,
        output_purpose: FilePurpose | str,
        output_scope_id: str,
    ) -> dict[str, Any]:
        """Copy one scoped output into RAG without any external model call."""

        clean_output_id = str(output_id or "").strip()
        clean_scope_id = str(output_scope_id or "").strip()
        if not clean_output_id or not clean_scope_id:
            raise UnsupportedDocumentError("文件输出标识或作用域无效。")
        purpose = FilePurpose(output_purpose)
        if purpose not in {FilePurpose.CHAT, FilePurpose.AGENT, FilePurpose.WORKFLOW}:
            raise UnsupportedDocumentError("该模块的文件输出不能保存到资料库。")

        claim = (kb_id, clean_output_id)
        with self._metadata_lock:
            metadata = self._read_metadata_unlocked()
            self._ensure_kb_exists(metadata, kb_id)
            existing = next(
                (
                    document
                    for document in metadata["documents"].values()
                    if isinstance(document, dict)
                    and str(document.get("kb_id")) == kb_id
                    and str(document.get("file_output_id")) == clean_output_id
                    and not document.get("deletion_status")
                ),
                None,
            )
            if existing is not None:
                return self._document_payload(existing)
            if claim in self._output_import_claims:
                raise KnowledgeBaseDeletionError(
                    "The same file output is already being saved; retry shortly."
                )
            self._output_import_claims.add(claim)
            self._knowledge_base_write_claims[kb_id] = (
                self._knowledge_base_write_claims.get(kb_id, 0) + 1
            )

        doc_id = "doc_output_" + hashlib.sha256(
            f"{kb_id}\0{clean_output_id}".encode("utf-8")
        ).hexdigest()[:24]
        stored_path: Path | None = None
        derived_asset_id: str | None = None
        indexed = False
        try:
            output_service = get_file_output_service()
            record, content = await asyncio.to_thread(
                output_service.read_output,
                clean_output_id,
                purpose=purpose,
                scope_id=clean_scope_id,
            )
            output_metadata = output_service.get_output(
                clean_output_id,
                purpose=purpose,
                scope_id=clean_scope_id,
            )
            if record.preview_kind not in {"text", "document"}:
                raise UnsupportedDocumentError(
                    "该输出格式不能直接保存到资料库；请在资料库入口另行确认处理。"
                )

            filename = _safe_filename(record.display_name)
            target_dir = self.uploads_dir / kb_id
            target_dir.mkdir(parents=True, exist_ok=True)
            stored_path = target_dir / f"{doc_id}_{filename}"
            stored_path.write_bytes(content)
            parsed = await asyncio.to_thread(
                parse_document_structured,
                stored_path,
                filename,
            )
            chunks: list[str] = []
            chunk_sources: list[dict[str, Any]] = []
            section_sources: list[dict[str, Any]] = []
            for section in parsed.sections:
                heading_path = list(normalize_heading_path(section.heading_path))
                heading_prefix = " > ".join(heading_path)
                section_text = section.text
                if (
                    heading_prefix
                    and section.text.strip() != heading_path[-1]
                    and not section.text.lstrip().startswith(heading_prefix)
                ):
                    section_text = f"{heading_prefix}\n{section.text}"
                section_chunks = self.splitter.split_text(section_text)
                chunks.extend(section_chunks)
                source = {
                    "page_number": section.page,
                    "slide": section.slide,
                    "sheet": section.sheet,
                    "line_range": section.line_range,
                    "row_range": section.row_range,
                    "heading_path": heading_path,
                    "time_range": section.time_range,
                }
                if len(section_sources) < MAX_FILE_OUTPUT_SECTION_SOURCES:
                    section_sources.append(source)
                chunk_sources.extend(source for _chunk in section_chunks)
            if not chunks:
                raise UnsupportedDocumentError("文件输出没有可索引的文本片段。")

            embeddings = await asyncio.to_thread(
                self.embedder.embed_texts_locally,
                chunks,
            )
            vector_chunks = [
                VectorChunk(
                    id=f"{doc_id}_chunk_{index}",
                    kb_id=kb_id,
                    doc_id=doc_id,
                    document_name=filename,
                    text=text,
                    embedding=embeddings[index],
                    chunk_index=index,
                    chunk_type=("table" if chunk_sources[index]["sheet"] else "standard"),
                    page_number=chunk_sources[index]["page_number"],
                    slide=chunk_sources[index]["slide"],
                    heading_path=tuple(chunk_sources[index]["heading_path"]),
                    sheet=chunk_sources[index]["sheet"],
                    row_range=chunk_sources[index]["row_range"],
                    source_block_id=clean_output_id,
                )
                for index, text in enumerate(chunks)
            ]
            lexical_chunks = [
                LexicalChunk(
                    chunk_id=item.id,
                    namespace=kb_id,
                    doc_id=doc_id,
                    document_name=filename,
                    text=item.text,
                    chunk_index=item.chunk_index,
                    chunk_type=item.chunk_type,
                    page_number=item.page_number,
                    slide=item.slide,
                    heading_path=item.heading_path,
                    sheet=item.sheet,
                    row_range=item.row_range,
                    source_block_id=item.source_block_id,
                )
                for item in vector_chunks
            ]

            registered = await asyncio.to_thread(
                get_file_asset_service().upload,
                io.BytesIO(content),
                purpose=FilePurpose.RAG,
                scope_id=kb_id,
                filename=filename,
                declared_media_type=record.media_type,
            )
            derived_asset_id = registered.asset_id
            self.vector_store.add_chunks(vector_chunks)
            self.lexical_store.add_chunks(lexical_chunks)
            indexed = True

            now = time.time()
            document = {
                "id": doc_id,
                "kb_id": kb_id,
                "filename": filename,
                "stored_path": str(stored_path),
                "size": len(content),
                "chunk_count": len(chunks),
                "content_type": record.media_type,
                "ingestion_status": "indexed_file_output",
                "visual_candidate": False,
                "warnings": _bounded_document_warnings(
                    [
                        *output_metadata.warnings,
                        *parsed.warnings,
                        *(
                            ["文件来源区段过多，资料库元数据仅保留前 2000 个区段。"]
                            if len(parsed.sections) > MAX_FILE_OUTPUT_SECTION_SOURCES
                            else []
                        ),
                    ]
                ),
                "content_hash": hashlib.sha256(content).hexdigest(),
                "asset_id": derived_asset_id,
                "file_output_id": clean_output_id,
                "file_output_source": {
                    "source_filename": filename,
                    "source_sha256": hashlib.sha256(content).hexdigest(),
                    "purpose": purpose.value,
                    "producer_kind": record.producer_kind,
                    "format": record.format_id,
                    "source_run_id": record.source_run_id,
                    "source_message_id": record.source_message_id,
                    "source_node_id": record.source_node_id,
                    "sections": section_sources,
                },
                "created_at": now,
            }
            with self._metadata_lock:
                latest = self._read_metadata_unlocked()
                self._ensure_kb_exists(latest, kb_id)
                existing = next(
                    (
                        item
                        for item in latest["documents"].values()
                        if isinstance(item, dict)
                        and str(item.get("kb_id")) == kb_id
                        and str(item.get("file_output_id")) == clean_output_id
                        and not item.get("deletion_status")
                    ),
                    None,
                )
                if existing is not None:
                    raise KnowledgeBaseDeletionError(
                        "The file output was saved concurrently; retry to read it."
                    )
                latest["documents"][doc_id] = document
                latest["knowledge_bases"][kb_id]["updated_at"] = now
                self._write_metadata_unlocked(latest)
            return self._document_payload(document)
        except Exception:
            if indexed:
                self.vector_store.delete_document(doc_id)
                self.lexical_store.delete_document(doc_id)
            if stored_path is not None:
                stored_path.unlink(missing_ok=True)
            if derived_asset_id:
                get_file_asset_service().delete_asset(
                    derived_asset_id,
                    purpose=FilePurpose.RAG,
                    scope_id=kb_id,
                )
            raise
        finally:
            with self._metadata_lock:
                self._output_import_claims.discard(claim)
                remaining = self._knowledge_base_write_claims.get(kb_id, 0) - 1
                if remaining > 0:
                    self._knowledge_base_write_claims[kb_id] = remaining
                else:
                    self._knowledge_base_write_claims.pop(kb_id, None)

    def list_documents(self, kb_id: str) -> list[dict[str, Any]]:
        """List documents belonging to a knowledge base."""

        metadata = self._read_metadata()
        self._ensure_kb_exists(metadata, kb_id)
        if kb_id not in metadata["knowledge_bases"]:
            raise KnowledgeBaseNotFoundError("知识库不存在。")
        documents = [
            self._document_payload(document)
            for document in metadata["documents"].values()
            if document["kb_id"] == kb_id
            and not document.get("deletion_status")
        ]
        return sorted(documents, key=lambda item: item["created_at"], reverse=True)

    def list_pending_document_deletions(
        self,
        kb_id: str,
    ) -> list[dict[str, Any]]:
        """Return restart-safe deletion retries scoped to one local tenant/KB."""

        metadata = self._read_metadata()
        self._ensure_kb_exists(metadata, kb_id)
        if kb_id not in metadata["knowledge_bases"]:
            raise KnowledgeBaseNotFoundError("知识库不存在。")
        pending: list[dict[str, Any]] = []
        for doc_id, document in metadata["documents"].items():
            if not isinstance(document, dict) or str(document.get("kb_id")) != kb_id:
                continue
            status = str(document.get("deletion_status") or "")
            if status not in {"deleting", "cleanup_pending", "failed"}:
                continue
            deletion = metadata["document_deletions"].get(doc_id)
            if not isinstance(deletion, dict):
                deletion = {}
            pending.append(
                {
                    "document_id": str(doc_id),
                    "filename": str(document.get("filename") or "document"),
                    "status": status,
                    "error_code": str(deletion.get("error_code") or "") or None,
                    "requested_at": float(
                        deletion.get("requested_at") or document.get("created_at") or 0
                    ),
                }
            )
        return sorted(
            pending,
            key=lambda item: (item["requested_at"], item["document_id"]),
        )

    def create_knowledge_write_proposal(
        self,
        kb_id: str,
        *,
        title: str,
        content: str,
        tags: list[str] | None = None,
        source_xpert_id: str | None = None,
        source_conversation_id: str | None = None,
        source_goal_id: str | None = None,
        source_handoff_id: str | None = None,
        source_run_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a durable write proposal without mutating the active index."""

        clean_title = self._required_proposal_text(title, "title", 160)
        clean_content = self._required_proposal_text(content, "content", 20_000)
        clean_tags = self._proposal_tags(tags)
        content_hash = hashlib.sha256(clean_content.encode("utf-8")).hexdigest()
        with self._metadata_lock:
            metadata = self._read_metadata_unlocked()
            self._ensure_kb_exists(metadata, kb_id)
            self._assert_corpus_mutable(metadata, kb_id)
            for existing in metadata["knowledge_write_proposals"].values():
                if (
                    existing.get("kb_id") == kb_id
                    and existing.get("status") == "pending"
                    and existing.get("content_hash") == content_hash
                    and str(existing.get("source_run_id") or "")
                    == str(source_run_id or "")
                ):
                    return self._knowledge_write_proposal_payload(existing, metadata)
            now = time.time()
            proposal = {
                "proposal_id": f"kwp_{uuid.uuid4().hex}",
                "kb_id": kb_id,
                "title": clean_title,
                "content": clean_content,
                "tags": clean_tags,
                "content_hash": content_hash,
                "source_xpert_id": self._optional_proposal_text(source_xpert_id, 200),
                "source_conversation_id": self._optional_proposal_text(source_conversation_id, 200),
                "source_goal_id": self._optional_proposal_text(source_goal_id, 200),
                "source_handoff_id": self._optional_proposal_text(source_handoff_id, 200),
                "source_run_id": self._optional_proposal_text(source_run_id, 200),
                "status": "pending",
                "revision": 1,
                "approval_in_progress": False,
                "document_id": None,
                "job_id": None,
                "candidate_version_id": None,
                "last_error": None,
                "decision_reason": None,
                "created_at": now,
                "updated_at": now,
                "decided_at": None,
            }
            metadata["knowledge_write_proposals"][proposal["proposal_id"]] = proposal
            self._write_metadata_unlocked(metadata)
            return self._knowledge_write_proposal_payload(proposal, metadata)

    def list_knowledge_write_proposals(
        self,
        *,
        kb_id: str | None = None,
        status: str | None = None,
        source_xpert_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        metadata = self._read_metadata()
        if kb_id is not None:
            self._ensure_kb_exists(metadata, kb_id)
        if status is not None and status not in {"pending", "approved", "rejected"}:
            raise ValueError("Invalid knowledge write proposal status.")
        items = [
            item
            for item in metadata["knowledge_write_proposals"].values()
            if (kb_id is None or item.get("kb_id") == kb_id)
            and (status is None or item.get("status") == status)
            and (
                source_xpert_id is None
                or item.get("source_xpert_id") == source_xpert_id
            )
        ]
        items.sort(key=lambda item: float(item.get("created_at", 0)), reverse=True)
        return [
            self._knowledge_write_proposal_payload(item, metadata)
            for item in items[: max(1, min(limit, 200))]
        ]

    def get_knowledge_write_proposal(self, proposal_id: str) -> dict[str, Any]:
        metadata = self._read_metadata()
        proposal = metadata["knowledge_write_proposals"].get(proposal_id)
        if not isinstance(proposal, dict):
            raise KnowledgeWriteProposalNotFoundError("Knowledge write proposal not found.")
        return self._knowledge_write_proposal_payload(proposal, metadata)

    def update_knowledge_write_proposal(
        self,
        proposal_id: str,
        *,
        expected_revision: int,
        title: str | None = None,
        content: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        with self._metadata_lock:
            metadata = self._read_metadata_unlocked()
            proposal = self._knowledge_write_proposal_or_raise(metadata, proposal_id)
            self._assert_pending_proposal(proposal, expected_revision)
            if title is not None:
                proposal["title"] = self._required_proposal_text(title, "title", 160)
            if content is not None:
                proposal["content"] = self._required_proposal_text(content, "content", 20_000)
                proposal["content_hash"] = hashlib.sha256(
                    proposal["content"].encode("utf-8")
                ).hexdigest()
            if tags is not None:
                proposal["tags"] = self._proposal_tags(tags)
            proposal["revision"] = int(proposal.get("revision", 1)) + 1
            proposal["updated_at"] = time.time()
            proposal["last_error"] = None
            self._write_metadata_unlocked(metadata)
            return self._knowledge_write_proposal_payload(proposal, metadata)

    def reject_knowledge_write_proposal(
        self,
        proposal_id: str,
        *,
        expected_revision: int,
        reason: str = "",
    ) -> dict[str, Any]:
        with self._metadata_lock:
            metadata = self._read_metadata_unlocked()
            proposal = self._knowledge_write_proposal_or_raise(metadata, proposal_id)
            self._assert_pending_proposal(proposal, expected_revision)
            now = time.time()
            proposal.update(
                {
                    "status": "rejected",
                    "decision_reason": str(reason or "").strip()[:500] or None,
                    "revision": int(proposal.get("revision", 1)) + 1,
                    "updated_at": now,
                    "decided_at": now,
                }
            )
            self._write_metadata_unlocked(metadata)
            return self._knowledge_write_proposal_payload(proposal, metadata)

    def approve_knowledge_write_proposal(
        self,
        proposal_id: str,
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        """Materialize a proposal and queue a non-active candidate version build."""

        with self._metadata_lock:
            metadata = self._read_metadata_unlocked()
            proposal = self._knowledge_write_proposal_or_raise(metadata, proposal_id)
            self._assert_pending_proposal(proposal, expected_revision)
            self._assert_corpus_mutable(metadata, str(proposal["kb_id"]))
            if proposal.get("approval_in_progress"):
                raise KnowledgeWriteProposalConflictError("Proposal approval is already running.")
            proposal["approval_in_progress"] = True
            proposal["last_error"] = None
            proposal["updated_at"] = time.time()
            self._write_metadata_unlocked(metadata)

        document_id: str | None = None
        try:
            draft = self.get_pipeline_draft(str(proposal["kb_id"]))
            graph = self.get_pipeline_graph(str(proposal["kb_id"]))
            document = self._create_managed_proposal_document(proposal)
            document_id = str(document["id"])
            active = self.get_active_pipeline_version(str(proposal["kb_id"]))
            job = self.create_pipeline_job(
                str(proposal["kb_id"]),
                draft_version=int(draft["version"]),
                graph_revision=int(graph["graph_revision"]),
                source_document_ids=([document_id] if active else None),
                base_version_id=(str(active["version_id"]) if active else None),
                origin={
                    "kind": "knowledge_write_proposal",
                    "proposal_id": proposal_id,
                    "promotion_required": True,
                },
            )
        except Exception as exc:
            if document_id:
                try:
                    self.delete_document(document_id)
                except Exception:
                    pass
            with self._metadata_lock:
                metadata = self._read_metadata_unlocked()
                stored = self._knowledge_write_proposal_or_raise(metadata, proposal_id)
                stored["approval_in_progress"] = False
                stored["last_error"] = self._safe_pipeline_error(exc)
                stored["updated_at"] = time.time()
                self._write_metadata_unlocked(metadata)
            raise

        with self._metadata_lock:
            metadata = self._read_metadata_unlocked()
            stored = self._knowledge_write_proposal_or_raise(metadata, proposal_id)
            now = time.time()
            stored.update(
                {
                    "status": "approved",
                    "approval_in_progress": False,
                    "document_id": document_id,
                    "job_id": str(job["job_id"]),
                    "candidate_version_id": str(job["candidate_version_id"]),
                    "revision": int(stored.get("revision", 1)) + 1,
                    "updated_at": now,
                    "decided_at": now,
                }
            )
            self._write_metadata_unlocked(metadata)
            return self._knowledge_write_proposal_payload(stored, metadata)

    def list_pipeline_assets(self, kb_id: str | None = None) -> list[dict[str, Any]]:
        """Return FileAsset views derived from uploaded documents."""

        metadata = self._read_metadata()
        self._ensure_kb_exists(metadata, kb_id)
        assets = [
            self._file_asset_payload(document)
            for document in metadata["documents"].values()
            if (kb_id is None or document["kb_id"] == kb_id)
            and not document.get("deletion_status")
        ]
        return sorted(assets, key=lambda item: item["created_at"], reverse=True)

    def list_pipeline_artifacts(self, kb_id: str | None = None) -> list[dict[str, Any]]:
        """Return Artifact views derived from uploaded documents."""

        metadata = self._read_metadata()
        self._ensure_kb_exists(metadata, kb_id)
        artifacts = [
            self._artifact_payload(document)
            for document in metadata["documents"].values()
            if (kb_id is None or document["kb_id"] == kb_id)
            and not document.get("deletion_status")
        ]
        return sorted(artifacts, key=lambda item: item["created_at"], reverse=True)

    def get_pipeline_draft(self, kb_id: str) -> dict[str, Any]:
        """Return a read-only Xpert-style pipeline draft for one knowledge base."""

        metadata = self._read_metadata()
        self._ensure_kb_exists(metadata, kb_id)
        documents = [
            document
            for document in metadata["documents"].values()
            if document["kb_id"] == kb_id
        ]
        assets = [self._file_asset_payload(document) for document in documents]
        artifacts = [self._artifact_payload(document) for document in documents]
        visual_documents = [
            document for document in documents if bool(document.get("visual_candidate"))
        ]
        chunk_count = sum(int(document.get("chunk_count", 0)) for document in documents)
        draft = self._pipeline_draft_record(metadata, kb_id)
        configs = draft["stages"]

        payload = {
            "kb_id": kb_id,
            "draft_id": draft["draft_id"],
            "version": int(draft.get("version", 1)),
            "updated_at": float(draft.get("updated_at", metadata["knowledge_bases"][kb_id]["updated_at"])),
            "editable": True,
            "index_schema_version": int(draft.get("index_schema_version", 2)),
            "embedding_profile": json.loads(json.dumps(draft["embedding_profile"])),
            "retrieval_profile": json.loads(json.dumps(draft["retrieval_profile"])),
            "stages": [
                {
                    "id": "stage_data_source",
                    "kind": "data_source",
                    "title": "数据源",
                    "status": "ready" if assets else "empty",
                    "item_count": len(assets),
                    "summary": "上传文件已映射为 FileAsset 元数据。",
                    "metadata": {
                        "asset_count": len(assets),
                        "document_count": len(documents),
                    },
                },
                {
                    "id": "stage_processor",
                    "kind": "processor",
                    "title": "处理器",
                    "status": "ready" if artifacts else "empty",
                    "item_count": len(artifacts),
                    "summary": "本地解析器已将文档映射为 Artifact。",
                    "metadata": {
                        "artifact_count": len(artifacts),
                        "parser": configs["stage_processor"].get(
                            "parser", "structured_local_parser"
                        ),
                        "mode": configs["stage_processor"].get("mode", "general"),
                    },
                },
                {
                    "id": "stage_chunker",
                    "kind": "chunker",
                    "title": "分块器",
                    "status": "ready" if chunk_count else "empty",
                    "item_count": chunk_count,
                    "summary": "当前使用本地文本分块结果作为 KnowledgeChunk。",
                    "metadata": {
                        "chunk_count": chunk_count,
                        "strategy": configs["stage_chunker"].get(
                            "strategy", "recursive_character"
                        ),
                    },
                },
                {
                    "id": "stage_image_understanding",
                    "kind": "image_understanding",
                    "title": "图像理解",
                    "status": (
                        "ready"
                        if configs["stage_image_understanding"].get("enabled")
                        else "disabled"
                    ),
                    "item_count": len(visual_documents),
                    "summary": (
                        "Visual sources will be rendered and analyzed before structured processing."
                        if configs["stage_image_understanding"].get("enabled")
                        else "Image understanding is optional and currently disabled."
                    ),
                    "metadata": {
                        "enabled": bool(configs["stage_image_understanding"].get("enabled")),
                        "visual_document_count": len(visual_documents),
                    },
                },
            ],
        }
        for stage in payload["stages"]:
            stage["config"] = configs.get(str(stage["id"]), {})
        return payload

    def get_pipeline_graph(self, kb_id: str) -> dict[str, Any]:
        metadata = self._read_metadata()
        self._ensure_kb_exists(metadata, kb_id)
        draft = self._pipeline_draft_record(metadata, kb_id)
        record = self._pipeline_graph_record(metadata, kb_id, draft)
        issues, compiled = self._validate_and_compile_pipeline_graph(
            kb_id,
            record["graph"],
            draft,
        )
        return {
            "kb_id": kb_id,
            "graph_id": str(record["graph_id"]),
            "graph_revision": int(record["graph_revision"]),
            "compiled_draft_version": int(record["compiled_draft_version"]),
            "updated_at": float(record["updated_at"]),
            "valid": not issues,
            "issues": [issue.payload() for issue in issues],
            "graph": json.loads(json.dumps(record["graph"])),
            "compiled": compiled.payload() if compiled else None,
        }

    def validate_pipeline_graph_config(
        self,
        kb_id: str,
        graph: dict[str, Any],
    ) -> dict[str, Any]:
        metadata = self._read_metadata()
        self._ensure_kb_exists(metadata, kb_id)
        draft = self._pipeline_draft_record(metadata, kb_id)
        issues, compiled = self._validate_and_compile_pipeline_graph(kb_id, graph, draft)
        return {
            "kb_id": kb_id,
            "valid": not issues,
            "issues": [issue.payload() for issue in issues],
            "compiled": compiled.payload() if compiled else None,
        }

    def save_pipeline_graph(
        self,
        kb_id: str,
        graph: dict[str, Any],
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        with self._metadata_lock:
            metadata = self._read_metadata_unlocked()
            self._ensure_kb_exists(metadata, kb_id)
            draft = self._pipeline_draft_record(metadata, kb_id)
            current = self._pipeline_graph_record(metadata, kb_id, draft)
            if int(expected_revision) != int(current["graph_revision"]):
                raise PipelineGraphRevisionError(
                    "Knowledge pipeline graph changed; reload before saving."
                )
            issues, compiled = self._validate_and_compile_pipeline_graph(kb_id, graph, draft)
            if issues or compiled is None:
                raise PipelineGraphValidationError(issues)

            now = time.time()
            next_draft_version = int(draft["version"]) + 1
            metadata["pipeline_drafts"][kb_id] = {
                "draft_id": str(draft["draft_id"]),
                "version": next_draft_version,
                "updated_at": now,
                "index_schema_version": 2,
                "embedding_profile": json.loads(json.dumps(compiled.embedding_profile)),
                "retrieval_profile": json.loads(json.dumps(compiled.retrieval_profile)),
                "stages": json.loads(json.dumps(compiled.stage_updates)),
            }
            metadata["pipeline_graphs"][kb_id] = {
                "graph_id": str(current["graph_id"]),
                "graph_revision": int(current["graph_revision"]) + 1,
                "compiled_draft_version": next_draft_version,
                "updated_at": now,
                "graph": json.loads(json.dumps(compiled.graph)),
            }
            self._write_metadata_unlocked(metadata)
        return self.get_pipeline_graph(kb_id)

    async def preview_pipeline_graph_node(
        self,
        kb_id: str,
        *,
        graph: dict[str, Any],
        node_id: str,
        document_id: str | None = None,
    ) -> dict[str, Any]:
        validation = self.validate_pipeline_graph_config(kb_id, graph)
        if not validation["valid"]:
            issues = [
                GraphValidationIssue(
                    str(item.get("code") or "invalid_graph"),
                    str(item.get("message") or "Invalid graph."),
                    node_id=item.get("node_id"),
                    edge_id=item.get("edge_id"),
                )
                for item in validation["issues"]
            ]
            raise PipelineGraphValidationError(issues)
        node = next(
            (
                item
                for item in graph.get("nodes", [])
                if isinstance(item, dict) and str(item.get("id")) == node_id
            ),
            None,
        )
        if not isinstance(node, dict):
            raise PipelineGraphValidationError(
                [GraphValidationIssue("node_not_found", "Graph node not found.", node_id=node_id)]
            )
        kind = str(node.get("kind") or "")
        config = dict(node.get("config") or {})
        if kind == "data_source":
            documents = self.list_documents(kb_id)
            return {
                "node_id": node_id,
                "kind": kind,
                "preview_type": "source_summary",
                "item_count": len(documents),
                "items": [
                    {
                        "document_id": item["id"],
                        "filename": item["filename"],
                        "size": item["size"],
                    }
                    for item in documents[:20]
                ],
                "truncated": len(documents) > 20,
            }
        if kind == "structured_processor":
            if not document_id:
                raise PipelineDraftValidationError("Processor preview requires document_id.")
            compiled = validation["compiled"] or {}
            vision = dict(
                (compiled.get("stage_updates") or {}).get("stage_image_understanding") or {}
            )
            result = await self.preview_pipeline_processor(
                kb_id,
                document_id,
                config,
                vision_override=vision if bool(vision.get("enabled")) else None,
            )
            return {
                "node_id": node_id,
                "kind": kind,
                "preview_type": "processor",
                "item_count": int(result["generated_count"] or result["block_count"]),
                "items": list(result["generated_items"] or result["blocks"])[:20],
                "warnings": list(result["warnings"]),
                "truncated": int(result["generated_count"] or result["block_count"]) > 20,
            }
        if kind in {"recursive_chunker", "parent_child_chunker"}:
            if not document_id:
                raise PipelineDraftValidationError("Chunker preview requires document_id.")
            compiled = validation["compiled"] or {}
            processor = dict(
                (compiled.get("stage_updates") or {}).get("stage_processor") or {}
            )
            vision = dict(
                (compiled.get("stage_updates") or {}).get("stage_image_understanding") or {}
            )
            processed = await self.preview_pipeline_processor(
                kb_id,
                document_id,
                processor,
                vision_override=vision if bool(vision.get("enabled")) else None,
            )
            chunks = self._preview_pipeline_chunks(processed, config, kind=kind)
            return {
                "node_id": node_id,
                "kind": kind,
                "preview_type": "chunks",
                "item_count": len(chunks),
                "items": chunks[:20],
                "truncated": len(chunks) > 20,
            }
        if kind == "image_understanding":
            if not document_id:
                raise PipelineDraftValidationError("Image preview requires document_id.")
            result = await self.preview_pipeline_vision(kb_id, document_id, config)
            items = list(result.get("blocks") or [])
            return {
                "node_id": node_id,
                "kind": kind,
                "preview_type": "vision_blocks",
                "item_count": len(items),
                "items": items[:20],
                "warnings": list(result.get("warnings") or []),
                "metadata": {
                    "page_count": result.get("page_count", 0),
                    "selected_page_count": result.get("selected_page_count", 0),
                    "processed_page_count": result.get("processed_page_count", 0),
                    "failed_page_count": result.get("failed_page_count", 0),
                },
                "truncated": len(items) > 20,
            }
        if kind == "embedding":
            profile = self._validated_embedding_profile(config, None)
            return {
                "node_id": node_id,
                "kind": kind,
                "preview_type": "capability",
                "item_count": 0,
                "items": [],
                "metadata": profile,
            }
        if kind == "dual_index":
            return {
                "node_id": node_id,
                "kind": kind,
                "preview_type": "capability",
                "item_count": 2,
                "items": [
                    {"id": "vector", "backend": self.vector_store.__class__.__name__, "enabled": True},
                    {"id": "fulltext", "backend": "sqlite_fts5", "enabled": True},
                ],
            }
        if kind == "retrieval":
            return {
                "node_id": node_id,
                "kind": kind,
                "preview_type": "retrieval_profile",
                "item_count": 1,
                "items": [RetrievalConfig.from_mapping(config).payload()],
            }
        raise PipelineDraftValidationError("This graph node cannot be previewed.")

    def update_pipeline_draft(
        self,
        kb_id: str,
        stage_updates: dict[str, Any],
        *,
        retrieval_profile: dict[str, Any] | None = None,
        embedding_profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist safe editable draft config without changing ingestion behavior."""

        metadata = self._read_metadata()
        self._ensure_kb_exists(metadata, kb_id)
        draft = self._pipeline_draft_record(metadata, kb_id)
        configs = {
            stage_id: dict(config)
            for stage_id, config in draft["stages"].items()
        }
        try:
            next_retrieval = RetrievalConfig.from_mapping(
                retrieval_profile,
                base=RetrievalConfig.from_mapping(draft.get("retrieval_profile")),
            ).payload()
        except ValueError as exc:
            raise PipelineDraftValidationError(str(exc)) from exc
        next_embedding = self._validated_embedding_profile(
            draft.get("embedding_profile"),
            embedding_profile,
        )

        if not isinstance(stage_updates, dict):
            raise PipelineDraftValidationError("pipeline draft stages must be an object.")

        for raw_stage_id, raw_update in stage_updates.items():
            stage_id = self._normalize_pipeline_stage_id(str(raw_stage_id))
            if stage_id is None:
                raise PipelineDraftValidationError(f"Unknown pipeline stage: {raw_stage_id}")
            if not isinstance(raw_update, dict):
                raise PipelineDraftValidationError(f"Stage update for {raw_stage_id} must be an object.")
            raw_config = raw_update.get("config", raw_update)
            if not isinstance(raw_config, dict):
                raise PipelineDraftValidationError(f"Stage config for {raw_stage_id} must be an object.")
            configs[stage_id] = self._validated_pipeline_stage_config(
                stage_id,
                configs[stage_id],
                raw_config,
            )

        now = time.time()
        with self._metadata_lock:
            latest = self._read_metadata_unlocked()
            self._ensure_kb_exists(latest, kb_id)
            current = self._pipeline_draft_record(latest, kb_id)
            next_draft = {
                "draft_id": current["draft_id"],
                "version": int(current.get("version", 1)) + 1,
                "updated_at": now,
                "index_schema_version": 2,
                "embedding_profile": next_embedding,
                "retrieval_profile": next_retrieval,
                "stages": configs,
            }
            latest["pipeline_drafts"][kb_id] = next_draft
            existing_graph = latest["pipeline_graphs"].get(kb_id)
            if isinstance(existing_graph, dict):
                current_graph = self._pipeline_graph_record(latest, kb_id, current)
                latest["pipeline_graphs"][kb_id] = {
                    "graph_id": str(current_graph["graph_id"]),
                    "graph_revision": int(current_graph["graph_revision"]) + 1,
                    "compiled_draft_version": int(next_draft["version"]),
                    "updated_at": now,
                    "graph": sync_graph_from_draft(
                        current_graph["graph"],
                        next_draft,
                        kb_id=kb_id,
                    ),
                }
            self._write_metadata_unlocked(latest)
        return self.get_pipeline_draft(kb_id)

    def preflight_pipeline_draft(self, kb_id: str) -> dict[str, Any]:
        """Return a safe preflight summary for the draft without executing it."""

        draft = self.get_pipeline_draft(kb_id)
        stages = {stage["id"]: stage for stage in draft["stages"]}
        warnings: list[str] = []
        stage_checks: list[dict[str, Any]] = []

        document_count = int(stages["stage_data_source"]["metadata"].get("document_count", 0))
        artifact_count = int(stages["stage_processor"]["metadata"].get("artifact_count", 0))
        chunk_count = int(stages["stage_chunker"]["metadata"].get("chunk_count", 0))
        processor_config = dict(stages["stage_processor"].get("config") or {})
        processor_mode = str(processor_config.get("mode") or "general")
        processor_capabilities = self._processor_generation_capabilities(
            str(processor_config.get("model_id") or "")
        )
        vision_stage = stages["stage_image_understanding"]
        vision_config = dict(vision_stage.get("config") or {})
        vision_capabilities = self.vision_processor.capabilities()
        embedding_profile = dict(draft.get("embedding_profile") or {})
        embedding_effective = dict(embedding_profile.get("effective") or {})
        visual_document_count = int(
            vision_stage.get("metadata", {}).get("visual_document_count", 0)
        )

        if document_count == 0:
            warnings.append("当前知识库还没有上传文档，流水线只能预检配置。")
        if artifact_count == 0:
            warnings.append("当前没有可检索 Artifact，上传文档后处理器才会产生结果。")
        if chunk_count == 0:
            warnings.append("当前没有 KnowledgeChunk，RAG 检索不会返回引用片段。")
        if not bool(embedding_effective.get("ready")):
            requested = dict(embedding_profile.get("requested") or {})
            warnings.append(
                "Embedding provider is unavailable for the requested model "
                f"{str(requested.get('model') or '(unset)')[:200]}; configure "
                "EMBEDDING_API_KEY before executing this pipeline."
            )
        if processor_mode in {"qa", "summary"} and not processor_capabilities.get(
            "llm_configured"
        ):
            warnings.append("生成式处理模式需要先配置可用的模型网关。")
        if visual_document_count and not bool(vision_config.get("enabled")):
            warnings.append(
                "Image or scanned PDF sources require an enabled image understanding stage."
            )
        if bool(vision_config.get("enabled")):
            if not str(vision_config.get("vision_model_id") or "").strip():
                warnings.append("Image understanding requires an explicit vision model.")
            if not bool(vision_capabilities.get("configured")):
                warnings.append(
                    "Image understanding requires PDF/image rendering and a configured model gateway."
                )

        for stage in draft["stages"]:
            severity = "info"
            status = stage["status"]
            summary = stage["summary"]
            if stage["id"] == "stage_data_source" and document_count == 0:
                severity = "warning"
                status = "empty"
                summary = "数据源配置有效，但当前知识库没有上传文件。"
            elif stage["id"] == "stage_processor" and artifact_count == 0:
                severity = "warning"
                status = "empty"
                summary = "处理器配置有效，但当前没有解析产物。"
            elif (
                stage["id"] == "stage_processor"
                and processor_mode in {"qa", "summary"}
                and not processor_capabilities.get("llm_configured")
            ):
                severity = "warning"
                status = "blocked"
                summary = "生成式处理器配置有效，但当前没有可用模型网关。"
            elif stage["id"] == "stage_chunker" and chunk_count == 0:
                severity = "warning"
                status = "empty"
                summary = "分块器草稿配置有效，但当前没有已索引 chunk。"
            elif stage["id"] == "stage_image_understanding":
                if visual_document_count and not bool(vision_config.get("enabled")):
                    severity = "warning"
                    status = "blocked"
                    summary = "Visual sources are waiting for an enabled image understanding stage."
                elif bool(vision_config.get("enabled")) and not bool(
                    vision_capabilities.get("configured")
                ):
                    severity = "warning"
                    status = "blocked"
                    summary = "The renderer or model gateway required by image understanding is unavailable."
                else:
                    status = "ready" if bool(vision_config.get("enabled")) else "disabled"
                    summary = str(stage.get("summary") or "")

            stage_checks.append(
                {
                    "id": stage["id"],
                    "kind": stage["kind"],
                    "title": stage["title"],
                    "status": status,
                    "severity": severity,
                    "summary": summary,
                    "metadata": {
                        "item_count": stage["item_count"],
                        "config": stage.get("config", {}),
                    },
                }
            )

        return {
            "kb_id": kb_id,
            "draft_id": draft["draft_id"],
            "ready": not warnings,
            "warnings": warnings,
            "stage_checks": stage_checks,
            "document_count": document_count,
            "artifact_count": artifact_count,
            "chunk_count": chunk_count,
        }

    def processor_capabilities(self) -> dict[str, Any]:
        generation = self._processor_generation_capabilities()
        return {
            "version": "rag-processor-capabilities-v1",
            "parser": "structured_local_parser",
            "modes": ["general", "qa", "summary"],
            "failure_policies": ["continue_on_error", "strict"],
            "supported_extensions": sorted(supported_extensions()),
            "block_types": [
                "heading",
                "paragraph",
                "list",
                "table",
                "code",
                "page",
            ],
            "llm_configured": bool(generation.get("llm_configured")),
            "model_label": str(generation.get("model") or ""),
            "generation_targets": list(generation.get("targets") or []),
            "limits": {
                "max_generated_items": 50,
                "preview_items": 20,
                "preview_text_characters": 600,
            },
        }

    def _processor_generation_capabilities(
        self,
        model_id: str | None = None,
    ) -> dict[str, Any]:
        gateway = self._managed_generation_gateway()
        if gateway is None or str(
            gateway.routing_mode("rag_processor_generate")
        ) == "legacy":
            return self.processor_generator.capabilities()
        if str(gateway.routing_mode("rag_processor_generate")) != "managed_required":
            return {
                "llm_configured": False,
                "model": str(model_id or ""),
                "targets": ["managed_degraded"],
            }
        if model_id is not None and not str(model_id).strip():
            return {
                "llm_configured": False,
                "model": "",
                "targets": ["managed"],
            }
        try:
            exact_model = gateway.exact_model_id(
                "rag_processor_generate",
                "chat_json_object",
                requested_model=model_id,
            )
        except Exception:
            return {
                "llm_configured": False,
                "model": str(model_id or ""),
                "targets": ["managed"],
            }
        return {
            "llm_configured": True,
            "model": exact_model,
            "targets": ["managed"],
        }

    def vision_capabilities(self) -> dict[str, Any]:
        return self.vision_processor.capabilities()

    async def preview_pipeline_vision(
        self,
        kb_id: str,
        document_id: str,
        vision_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        metadata = self._read_metadata()
        self._ensure_kb_exists(metadata, kb_id)
        document = metadata["documents"].get(document_id)
        if not isinstance(document, dict) or document.get("kb_id") != kb_id:
            raise DocumentNotFoundError("Document not found.")
        draft = self._pipeline_draft_record(metadata, kb_id)
        config = self._validated_pipeline_stage_config(
            "stage_image_understanding",
            dict(draft["stages"]["stage_image_understanding"]),
            {**dict(vision_override or {}), "enabled": True},
        )
        path = Path(str(document.get("stored_path") or ""))
        if not path.is_file():
            raise DocumentNotFoundError("Document source file is unavailable.")
        try:
            result = await self.vision_processor.analyze_source(
                path=path,
                filename=str(document["filename"]),
                source_id=str(document["id"]),
                config=config,
            )
        except VisionProcessingError as exc:
            raise PipelineDraftValidationError(self._safe_pipeline_error(exc)) from exc
        payload = result.payload(max_text=600)
        payload.update({"kb_id": kb_id, "document_id": document_id, "config": config})
        return payload

    async def preview_pipeline_processor(
        self,
        kb_id: str,
        document_id: str,
        processor_override: dict[str, Any] | None = None,
        vision_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        metadata = self._read_metadata()
        self._ensure_kb_exists(metadata, kb_id)
        document_record = metadata["documents"].get(document_id)
        if not isinstance(document_record, dict) or document_record.get("kb_id") != kb_id:
            raise DocumentNotFoundError("文档不存在。")
        draft = self._pipeline_draft_record(metadata, kb_id)
        current = dict(draft["stages"]["stage_processor"])
        config = self._validated_pipeline_stage_config(
            "stage_processor",
            current,
            dict(processor_override or {}),
        )
        path = Path(str(document_record.get("stored_path") or ""))
        if not path.is_file():
            raise DocumentNotFoundError("文档源文件不可用。")
        extra_blocks: list[dict[str, Any]] = []
        if vision_override and bool(vision_override.get("enabled")):
            visual = await self.preview_pipeline_vision(kb_id, document_id, vision_override)
            extra_blocks = [
                dict(item)
                for item in visual.get("blocks", [])
                if isinstance(item, dict)
            ]
        try:
            processed = await asyncio.to_thread(
                self.document_processor.process,
                path,
                filename=str(document_record["filename"]),
                source_id=str(document_record["id"]),
                config=config,
                extra_blocks=extra_blocks,
            )
        except (DocumentParseError, OSError, UnicodeError) as exc:
            raise PipelineDraftValidationError(self._safe_pipeline_error(exc)) from exc
        generation = await self._generate_processor_items(
            processed,
            mode=str(config.get("mode") or "general"),
            model_id=str(config.get("model_id") or ""),
            max_items=min(20, int(config.get("max_generated_items", 20))),
            parent_run_reference=(
                f"rag_processor:preview:{kb_id}:{document_id}:{uuid.uuid4().hex}"
            ),
            stable=False,
        )
        generated = generation.items
        return {
            "kb_id": kb_id,
            "document_id": document_id,
            "filename": str(document_record["filename"]),
            "title": processed.title,
            "config": config,
            "character_count": len(processed.text),
            "block_count": len(processed.blocks),
            "block_counts": processed.block_counts,
            "generated_count": len(generated),
            "warnings": list(processed.warnings),
            "blocks": [
                block.payload(max_text=600) for block in processed.blocks[:20]
            ],
            "generated_items": [
                item.payload(max_text=600) for item in generated[:20]
            ],
            "execution_mode": generation.execution_mode,
            "provider_route_receipts": generation.provider_route_receipts,
        }

    def create_pipeline_job(
        self,
        kb_id: str,
        *,
        draft_version: int,
        graph_revision: int | None = None,
        source_document_ids: list[str] | None = None,
        xpert_sources: list[dict[str, Any]] | None = None,
        base_version_id: str | None = None,
        origin: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a durable job with immutable source and draft snapshots."""

        with self._metadata_lock:
            metadata = self._read_metadata_unlocked()
            self._ensure_kb_exists(metadata, kb_id)
            draft = self._pipeline_draft_record(metadata, kb_id)
            if int(draft["version"]) != int(draft_version):
                raise PipelineJobStateError(
                    f"Pipeline draft changed. Expected v{draft_version}, current v{draft['version']}."
                )
            self._ensure_embedding_profile_ready(draft["embedding_profile"])
            graph = self._pipeline_graph_record(metadata, kb_id, draft)
            if graph_revision is not None and int(graph_revision) != int(graph["graph_revision"]):
                raise PipelineGraphRevisionError(
                    "Knowledge pipeline graph changed; reload before executing."
                )
            issues, compiled = self._validate_and_compile_pipeline_graph(
                kb_id,
                graph["graph"],
                draft,
            )
            if issues or compiled is None:
                raise PipelineGraphValidationError(issues)

            documents = [
                item
                for item in metadata["documents"].values()
                if item["kb_id"] == kb_id
                and not item.get("deletion_status")
            ]
            if source_document_ids is not None:
                requested = list(dict.fromkeys(str(item) for item in source_document_ids))
                by_id = {str(item["id"]): item for item in documents}
                missing = [item for item in requested if item not in by_id]
                if missing:
                    raise DocumentNotFoundError(
                        f"Pipeline source document not found: {missing[0]}"
                    )
                documents = [by_id[item] for item in requested]

            inherited_sources: list[dict[str, Any]] = []
            if base_version_id:
                base_version = metadata["pipeline_versions"].get(base_version_id)
                if not isinstance(base_version, dict) or base_version.get("kb_id") != kb_id:
                    raise PipelineVersionNotFoundError(
                        "Base knowledge pipeline version was not found for this knowledge base."
                    )
                base_job = metadata["pipeline_jobs"].get(str(base_version.get("job_id") or ""))
                if not isinstance(base_job, dict):
                    raise PipelineJobNotFoundError(
                        "Base knowledge pipeline source snapshot is unavailable."
                    )
                inherited_sources = [
                    json.loads(json.dumps(item))
                    for item in base_job.get("sources", [])
                    if isinstance(item, dict)
                ]
                if not inherited_sources:
                    raise PipelineDraftValidationError(
                        "Base knowledge pipeline version has no reusable source snapshot."
                    )
            if any(bool(item.get("visual_candidate")) for item in documents) and not bool(
                compiled.stage_updates.get("stage_image_understanding", {}).get("enabled")
            ):
                raise PipelineDraftValidationError(
                    "Image and scanned PDF sources require an enabled image understanding node."
                )

            job_id = f"kpjob_{uuid.uuid4().hex}"
            source_dir = self.pipeline_sources_dir / job_id
            source_dir.mkdir(parents=True, exist_ok=True)
            manifest: list[dict[str, Any]] = []
            try:
                seen_source_ids: set[str] = set()
                for index, source in enumerate(inherited_sources):
                    source_id = str(source.get("source_id") or "")
                    snapshot_key = str(source.get("snapshot_key") or "")
                    source_path = self.storage_dir / snapshot_key
                    if not source_id or not source_path.is_file():
                        raise DocumentNotFoundError(
                            "Base knowledge pipeline source snapshot is unavailable."
                        )
                    suffix = source_path.suffix or ".txt"
                    snapshot = source_dir / f"base_{index}{suffix.lower()}"
                    shutil.copyfile(source_path, snapshot)
                    copied = json.loads(json.dumps(source))
                    copied["snapshot_key"] = snapshot.relative_to(self.storage_dir).as_posix()
                    copied["content_hash"] = self._file_sha256(snapshot)
                    manifest.append(copied)
                    seen_source_ids.add(source_id)

                for index, document in enumerate(documents):
                    if str(document["id"]) in seen_source_ids:
                        continue
                    source_path = Path(str(document.get("stored_path") or ""))
                    if not source_path.is_file():
                        raise DocumentNotFoundError(
                            f"Pipeline source file is unavailable: {document['id']}"
                        )
                    suffix = source_path.suffix or Path(str(document["filename"])).suffix or ".txt"
                    snapshot = source_dir / f"document_{index}{suffix.lower()}"
                    shutil.copyfile(source_path, snapshot)
                    manifest.append(
                        {
                            "source_id": str(document["id"]),
                            "source_kind": "knowledge_document",
                            "filename": str(document["filename"]),
                            "size": int(document.get("size", snapshot.stat().st_size)),
                            "snapshot_key": snapshot.relative_to(self.storage_dir).as_posix(),
                            "content_hash": self._file_sha256(snapshot),
                            "content_mode": "document",
                        }
                    )
                    seen_source_ids.add(str(document["id"]))

                seen_external: set[tuple[str, str, str]] = set()
                for index, source in enumerate(xpert_sources or []):
                    key = (
                        str(source.get("xpert_id") or ""),
                        str(source.get("conversation_id") or ""),
                        str(source.get("asset_id") or ""),
                    )
                    if not all(key) or key in seen_external:
                        continue
                    seen_external.add(key)
                    text = str(source.get("text") or "")
                    if not text.strip():
                        continue
                    snapshot = source_dir / f"xpert_file_{index}.txt"
                    source_id = f"xpert_{key[2]}"
                    if source_id in seen_source_ids:
                        continue
                    snapshot.write_text(text, encoding="utf-8")
                    manifest.append(
                        {
                            "source_id": source_id,
                            "source_kind": "xpert_file",
                            "filename": str(source.get("filename") or f"attachment_{index}.txt"),
                            "size": len(text.encode("utf-8")),
                            "snapshot_key": snapshot.relative_to(self.storage_dir).as_posix(),
                            "content_hash": self._file_sha256(snapshot),
                            "content_mode": "extracted_text",
                            "xpert_id": key[0],
                            "conversation_id": key[1],
                            "asset_id": key[2],
                        }
                    )
                    seen_source_ids.add(source_id)
                if not manifest:
                    raise PipelineDraftValidationError(
                        "A knowledge pipeline job requires at least one document or Xpert file."
                    )
            except Exception:
                shutil.rmtree(source_dir, ignore_errors=True)
                raise

            reserved_numbers = [
                int(item.get("version", 0))
                for item in metadata["pipeline_versions"].values()
                if item.get("kb_id") == kb_id
            ] + [
                int(item.get("candidate_version", 0))
                for item in metadata["pipeline_jobs"].values()
                if item.get("kb_id") == kb_id
            ]
            candidate_version = max(reserved_numbers, default=0) + 1
            candidate_version_id = f"kpv_{uuid.uuid4().hex}"
            now = time.time()
            processor_profile = json.loads(
                json.dumps(draft["stages"]["stage_processor"])
            )
            processor_config_hash = self._mapping_sha256(processor_profile)
            vision_profile = json.loads(
                json.dumps(draft["stages"]["stage_image_understanding"])
            )
            vision_config_hash = self._mapping_sha256(vision_profile)
            document_results = [
                {
                    "source_id": str(source["source_id"]),
                    "filename": str(source["filename"]),
                    "status": "pending",
                    "content_hash": str(source["content_hash"]),
                    "processor_config_hash": processor_config_hash,
                    "vision_config_hash": vision_config_hash,
                    "attempt": 0,
                    "block_count": 0,
                    "generated_count": 0,
                    "chunk_count": 0,
                    "qa_count": 0,
                    "summary_count": 0,
                    "warnings": [],
                    "error": None,
                    "duration_ms": None,
                    "vision_status": "pending" if vision_profile.get("enabled") else "skipped",
                    "vision_page_count": 0,
                    "vision_selected_page_count": 0,
                    "vision_processed_page_count": 0,
                    "vision_failed_page_count": 0,
                    "vision_block_count": 0,
                    "vision_warnings": [],
                    "vision_error": None,
                    "vision_artifact_key": (
                        self.pipeline_vision_dir
                        / job_id
                        / f"source_{index}.json"
                    ).relative_to(self.storage_dir).as_posix(),
                    "artifact_key": (
                        self.pipeline_processed_dir
                        / job_id
                        / f"source_{index}.json"
                    ).relative_to(self.storage_dir).as_posix(),
                }
                for index, source in enumerate(manifest)
            ]
            embedding_metadata = self._embedding_job_metadata(
                draft["embedding_profile"]
            )
            job = {
                "job_id": job_id,
                "kb_id": kb_id,
                "draft_id": str(draft["draft_id"]),
                "draft_version": int(draft["version"]),
                "graph_id": str(graph["graph_id"]),
                "graph_revision": int(graph["graph_revision"]),
                "config_snapshot": {
                    "index_schema_version": int(draft.get("index_schema_version", 2)),
                    "graph_id": str(graph["graph_id"]),
                    "graph_revision": int(graph["graph_revision"]),
                    "stages": json.loads(json.dumps(draft["stages"])),
                    "processor_profile": processor_profile,
                    "vision_profile": vision_profile,
                    "embedding_profile": json.loads(json.dumps(draft["embedding_profile"])),
                    "retrieval_profile": json.loads(json.dumps(draft["retrieval_profile"])),
                },
                "origin": self._safe_pipeline_origin(origin),
                "base_version_id": base_version_id,
                "sources": manifest,
                "document_results": document_results,
                "status": "queued",
                "stages": self._new_pipeline_job_stages(),
                "candidate_version_id": candidate_version_id,
                "candidate_version": candidate_version,
                "candidate_namespace": f"{kb_id}::{candidate_version_id}",
                "run_id": None,
                "attempt": 0,
                "cancel_requested": False,
                "error": None,
                "warnings": [],
                "processor_error": None,
                **embedding_metadata,
                "created_at": now,
                "updated_at": now,
                "started_at": None,
                "completed_at": None,
            }
            metadata["pipeline_jobs"][job_id] = job
            self._write_metadata_unlocked(metadata)
            return self.pipeline_job_payload(job)

    def create_strategy_tuning_pipeline_job(
        self,
        kb_id: str,
        *,
        base_version_id: str,
        chunker_profile: dict[str, Any],
        retrieval_profile: dict[str, Any],
        tuning_run_id: str,
        trial: bool,
    ) -> dict[str, Any]:
        """Create an isolated tuning job from one immutable version snapshot.

        This deliberately does not reuse the editable draft path. Only chunking
        and retrieval fields may differ from the fixed base version.
        """

        with self._metadata_lock:
            metadata = self._read_metadata_unlocked()
            self._ensure_kb_exists(metadata, kb_id)
            base_version = metadata["pipeline_versions"].get(base_version_id)
            if (
                not isinstance(base_version, dict)
                or base_version.get("kb_id") != kb_id
                or base_version.get("status") not in {"ready", "active"}
            ):
                raise PipelineVersionNotFoundError(
                    "A ready or active base knowledge version is required for tuning."
                )
            if int(base_version.get("index_schema_version") or 1) < 2:
                raise PipelineDraftValidationError(
                    "RAG strategy tuning requires an index schema v2 base version."
                )
            base_job = metadata["pipeline_jobs"].get(str(base_version.get("job_id") or ""))
            if not isinstance(base_job, dict) or not base_job.get("sources"):
                raise PipelineJobNotFoundError(
                    "Base knowledge pipeline source snapshot is unavailable."
                )

            config_snapshot = json.loads(json.dumps(base_job.get("config_snapshot") or {}))
            stages = config_snapshot.get("stages")
            if not isinstance(stages, dict):
                raise PipelineDraftValidationError(
                    "Base knowledge pipeline configuration snapshot is unavailable."
                )
            if not isinstance(chunker_profile, dict) or not isinstance(retrieval_profile, dict):
                raise PipelineDraftValidationError(
                    "Tuning chunker and retrieval profiles must be objects."
                )
            stages["stage_chunker"] = self._validated_pipeline_stage_config(
                "stage_chunker",
                dict(stages.get("stage_chunker") or {}),
                chunker_profile,
            )
            try:
                config_snapshot["retrieval_profile"] = RetrievalConfig.from_mapping(
                    retrieval_profile,
                    base=RetrievalConfig.from_mapping(
                        config_snapshot.get("retrieval_profile")
                        if isinstance(config_snapshot.get("retrieval_profile"), dict)
                        else None
                    ),
                ).payload()
            except ValueError as exc:
                raise PipelineDraftValidationError(str(exc)) from exc
            processor_profile = json.loads(
                json.dumps(stages.get("stage_processor") or {})
            )
            vision_profile = json.loads(
                json.dumps(stages.get("stage_image_understanding") or {})
            )
            config_snapshot["processor_profile"] = processor_profile
            config_snapshot["vision_profile"] = vision_profile

            job_id = f"kpjob_{uuid.uuid4().hex}"
            source_dir = self.pipeline_sources_dir / job_id
            source_dir.mkdir(parents=True, exist_ok=True)
            manifest: list[dict[str, Any]] = []
            try:
                for index, source in enumerate(base_job.get("sources", [])):
                    if not isinstance(source, dict):
                        continue
                    source_id = str(source.get("source_id") or "")
                    source_path = self.storage_dir / str(source.get("snapshot_key") or "")
                    if not source_id or not source_path.is_file():
                        raise DocumentNotFoundError(
                            "Base knowledge pipeline source snapshot is unavailable."
                        )
                    snapshot = source_dir / f"base_{index}{(source_path.suffix or '.txt').lower()}"
                    shutil.copyfile(source_path, snapshot)
                    copied = json.loads(json.dumps(source))
                    copied["snapshot_key"] = snapshot.relative_to(self.storage_dir).as_posix()
                    copied["content_hash"] = self._file_sha256(snapshot)
                    manifest.append(copied)
                if not manifest:
                    raise PipelineDraftValidationError(
                        "A strategy tuning job requires a reusable source snapshot."
                    )
            except Exception:
                shutil.rmtree(source_dir, ignore_errors=True)
                raise

            reserved_numbers = [
                int(item.get("version", 0))
                for item in metadata["pipeline_versions"].values()
                if item.get("kb_id") == kb_id
            ] + [
                int(item.get("candidate_version", 0))
                for item in metadata["pipeline_jobs"].values()
                if item.get("kb_id") == kb_id
            ]
            candidate_version = max(reserved_numbers, default=0) + 1
            candidate_version_id = f"kpv_{uuid.uuid4().hex}"
            now = time.time()
            processor_hash = self._mapping_sha256(processor_profile)
            vision_hash = self._mapping_sha256(vision_profile)
            document_results = [
                {
                    "source_id": str(source["source_id"]),
                    "filename": str(source["filename"]),
                    "status": "pending",
                    "content_hash": str(source["content_hash"]),
                    "processor_config_hash": processor_hash,
                    "vision_config_hash": vision_hash,
                    "attempt": 0,
                    "block_count": 0,
                    "generated_count": 0,
                    "chunk_count": 0,
                    "qa_count": 0,
                    "summary_count": 0,
                    "warnings": [],
                    "error": None,
                    "duration_ms": None,
                    "vision_status": "pending" if vision_profile.get("enabled") else "skipped",
                    "vision_page_count": 0,
                    "vision_selected_page_count": 0,
                    "vision_processed_page_count": 0,
                    "vision_failed_page_count": 0,
                    "vision_block_count": 0,
                    "vision_warnings": [],
                    "vision_error": None,
                    "vision_artifact_key": (
                        self.pipeline_vision_dir / job_id / f"source_{index}.json"
                    ).relative_to(self.storage_dir).as_posix(),
                    "artifact_key": (
                        self.pipeline_processed_dir / job_id / f"source_{index}.json"
                    ).relative_to(self.storage_dir).as_posix(),
                }
                for index, source in enumerate(manifest)
            ]
            origin = {
                "kind": "rag_strategy_tuner_trial" if trial else "rag_strategy_tuner",
                "promotion_required": True,
                "source_run_id": str(tuning_run_id)[:200],
            }
            embedding_metadata = self._embedding_job_metadata(
                config_snapshot.get("embedding_profile")
            )
            job = {
                "job_id": job_id,
                "kb_id": kb_id,
                "draft_id": str(base_version.get("draft_id") or base_job.get("draft_id") or ""),
                "draft_version": int(base_version.get("draft_version") or base_job.get("draft_version") or 1),
                "graph_id": str(config_snapshot.get("graph_id") or base_job.get("graph_id") or ""),
                "graph_revision": int(config_snapshot.get("graph_revision") or base_job.get("graph_revision") or 1),
                "config_snapshot": config_snapshot,
                "origin": origin,
                "base_version_id": base_version_id,
                "sources": manifest,
                "document_results": document_results,
                "status": "queued",
                "stages": self._new_pipeline_job_stages(),
                "candidate_version_id": candidate_version_id,
                "candidate_version": candidate_version,
                "candidate_namespace": f"{kb_id}::{candidate_version_id}",
                "run_id": None,
                "attempt": 0,
                "cancel_requested": False,
                "error": None,
                "warnings": [],
                "processor_error": None,
                **embedding_metadata,
                "created_at": now,
                "updated_at": now,
                "started_at": None,
                "completed_at": None,
            }
            metadata["pipeline_jobs"][job_id] = job
            self._write_metadata_unlocked(metadata)
            return self.pipeline_job_payload(job)

    def list_pipeline_jobs(
        self,
        *,
        kb_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        metadata = self._read_metadata()
        items = list(metadata["pipeline_jobs"].values())
        if kb_id is not None:
            self._ensure_kb_exists(metadata, kb_id)
            items = [item for item in items if item.get("kb_id") == kb_id]
        if status is not None:
            items = [item for item in items if item.get("status") == status]
        items.sort(key=lambda item: float(item.get("created_at", 0)), reverse=True)
        return [self.pipeline_job_payload(item) for item in items[: max(1, min(limit, 200))]]

    def get_pipeline_job(self, job_id: str) -> dict[str, Any]:
        metadata = self._read_metadata()
        job = metadata["pipeline_jobs"].get(job_id)
        if not isinstance(job, dict):
            raise PipelineJobNotFoundError("Knowledge pipeline job not found.")
        return json.loads(json.dumps(job))

    def pipeline_job_payload(self, job: dict[str, Any]) -> dict[str, Any]:
        sources = [
            {
                key: value
                for key, value in source.items()
                if key not in {"snapshot_key", "content_hash"}
            }
            for source in job.get("sources", [])
        ]
        document_results = [
            {
                key: json.loads(json.dumps(value))
                for key, value in result.items()
                if key
                not in {
                    "artifact_key",
                    "vision_artifact_key",
                    "content_hash",
                    "processor_config_hash",
                    "vision_config_hash",
                }
            }
            for result in job.get("document_results", [])
            if isinstance(result, dict)
        ]
        return {
            key: json.loads(json.dumps(value))
            for key, value in job.items()
            if key
            not in {
                "candidate_namespace",
                "config_snapshot",
                "sources",
                "document_results",
                "processor_error",
                "deletion_invalidated",
                "deletion_artifacts_purged",
            }
        } | {
            "sources": sources,
            "source_count": len(sources),
            "document_results": document_results,
        }

    def claim_next_pipeline_job(self) -> dict[str, Any] | None:
        with self._metadata_lock:
            metadata = self._read_metadata_unlocked()
            queued = [
                item
                for item in metadata["pipeline_jobs"].values()
                if item.get("status") == "queued"
            ]
            if not queued:
                return None
            queued.sort(key=lambda item: float(item.get("created_at", 0)))
            job = queued[0]
            now = time.time()
            job["status"] = "running"
            job["attempt"] = int(job.get("attempt", 0)) + 1
            job["started_at"] = now
            job["updated_at"] = now
            job["error"] = None
            job["cancel_requested"] = False
            self._write_metadata_unlocked(metadata)
            return json.loads(json.dumps(job))

    def recover_pipeline_jobs(self) -> int:
        with self._metadata_lock:
            metadata = self._read_metadata_unlocked()
            recovered = 0
            for job in metadata["pipeline_jobs"].values():
                if job.get("status") != "running":
                    continue
                namespace = str(job.get("candidate_namespace") or "")
                self.vector_store.delete_knowledge_base(namespace)
                self.lexical_store.delete_namespace(namespace)
                if str(job.get("embedding_execution_mode") or "") == "managed":
                    try:
                        gateway = self._managed_embedding_gateway()
                        run_status = (
                            gateway.index_run_status(str(job.get("job_id") or ""))
                            if gateway is not None
                            else None
                        )
                    except Exception:
                        run_status = "uncertain"
                    if run_status is not None:
                        now = time.time()
                        job["status"] = "failed"
                        job["error_code"] = (
                            "provider_embedding_dispatch_uncertain"
                            if run_status in {"running", "uncertain"}
                            else "provider_embedding_run_already_recorded"
                        )
                        job["error"] = (
                            "Managed Embedding dispatch is uncertain after process restart; "
                            "create a new pipeline job."
                        )
                        job["completed_at"] = now
                        job["updated_at"] = now
                        for stage in job.get("stages", []):
                            if stage.get("status") in {"pending", "running"}:
                                stage["status"] = "failed"
                                stage["error"] = job["error"]
                        recovered += 1
                        continue
                if job.get("deletion_invalidated"):
                    job["status"] = "cancelled"
                    job["cancel_requested"] = True
                    job["completed_at"] = time.time()
                    job["error"] = "Cancelled because a source document was deleted."
                    job["updated_at"] = time.time()
                    for stage in job.get("stages", []):
                        if stage.get("status") in {"pending", "running"}:
                            stage["status"] = "cancelled"
                    recovered += 1
                    continue
                job["status"] = "queued"
                job["error"] = "Recovered after process restart."
                job["updated_at"] = time.time()
                job["stages"] = self._new_pipeline_job_stages()
                for result in job.get("document_results", []):
                    if isinstance(result, dict) and result.get("status") == "processing":
                        result["status"] = "pending"
                        result["error"] = "Recovered after process restart."
                job["processor_error"] = None
                recovered += 1
            if recovered:
                self._write_metadata_unlocked(metadata)
            return recovered

    def set_pipeline_job_run_id(self, job_id: str, run_id: str) -> None:
        self._update_pipeline_job(job_id, lambda job: job.update({"run_id": run_id}))

    def start_pipeline_job_stage(self, job_id: str, stage_id: str) -> None:
        def update(job: dict[str, Any]) -> None:
            stage = self._pipeline_stage(job, stage_id)
            stage.update(
                {
                    "status": "running",
                    "progress": 10,
                    "started_at": time.time(),
                    "completed_at": None,
                    "error": None,
                }
            )

        self._update_pipeline_job(job_id, update)

    def complete_pipeline_job_stage(
        self,
        job_id: str,
        stage_id: str,
        *,
        item_count: int | None = None,
    ) -> None:
        def update(job: dict[str, Any]) -> None:
            stage = self._pipeline_stage(job, stage_id)
            stage.update(
                {
                    "status": "completed",
                    "progress": 100,
                    "completed_at": time.time(),
                }
            )
            if item_count is not None:
                stage["item_count"] = item_count

        self._update_pipeline_job(job_id, update)

    def load_pipeline_job_sources(self, job_id: str) -> list[dict[str, Any]]:
        job = self.get_pipeline_job(job_id)
        loaded: list[dict[str, Any]] = []
        for source in job["sources"]:
            path = self._pipeline_snapshot_path(str(source["snapshot_key"]))
            if not path.is_file():
                raise PipelineJobStateError(
                    f"Pipeline source snapshot is unavailable: {source['source_id']}"
                )
            loaded.append({**source, "snapshot_exists": True})
        return loaded

    def parse_pipeline_job_sources(self, job_id: str) -> list[dict[str, Any]]:
        job = self.get_pipeline_job(job_id)
        parsed: list[dict[str, Any]] = []
        for source in job["sources"]:
            path = self._pipeline_snapshot_path(str(source["snapshot_key"]))
            if source.get("content_mode") == "extracted_text":
                text = path.read_text(encoding="utf-8")
            else:
                text = parse_document(path, str(source["filename"]))
            if text.strip():
                parsed.append({**source, "text": text})
        if not parsed:
            raise PipelineJobStateError("No pipeline sources produced readable text.")
        return parsed

    async def process_pipeline_job_vision(self, job_id: str) -> list[dict[str, Any]]:
        """Run optional visual understanding with source/page-level durable reuse."""

        job = self.get_pipeline_job(job_id)
        snapshot = job.get("config_snapshot", {})
        profile = dict(
            snapshot.get("vision_profile")
            or snapshot.get("stages", {}).get("stage_image_understanding")
            or self._default_pipeline_draft_stages()["stage_image_understanding"]
        )
        if not bool(profile.get("enabled")):
            return []

        config_hash = self._mapping_sha256(profile)
        results_by_source = {
            str(item.get("source_id")): item
            for item in job.get("document_results", [])
            if isinstance(item, dict)
        }
        completed: list[dict[str, Any]] = []
        failed_sources: list[str] = []
        for source in job.get("sources", []):
            source_id = str(source["source_id"])
            result = results_by_source.get(source_id)
            if result is None:
                raise PipelineJobStateError(
                    f"Vision result state is missing for source: {source_id}"
                )
            artifact_path = self._pipeline_vision_path(str(result["vision_artifact_key"]))
            reusable = (
                result.get("vision_status") == "completed"
                and result.get("content_hash") == source.get("content_hash")
                and result.get("vision_config_hash") == config_hash
                and artifact_path.is_file()
            )
            if reusable:
                try:
                    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
                    completed.append({**source, **payload, "reused": True})
                    continue
                except (OSError, json.JSONDecodeError):
                    pass

            source_path = self._pipeline_snapshot_path(str(source["snapshot_key"]))
            page_dir = artifact_path.parent / f"{artifact_path.stem}_pages"
            page_dir.mkdir(parents=True, exist_ok=True)

            def cache_get(page_number: int) -> dict[str, Any] | None:
                page_path = page_dir / f"page_{page_number}.json"
                if not page_path.is_file():
                    return None
                try:
                    cached = json.loads(page_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    return None
                if (
                    cached.get("content_hash") != source.get("content_hash")
                    or cached.get("vision_config_hash") != config_hash
                    or cached.get("status") != "completed"
                ):
                    return None
                return dict(cached.get("result") or {})

            def cache_set(page_number: int, page_result: dict[str, Any]) -> None:
                page_path = page_dir / f"page_{page_number}.json"
                self._atomic_json_write(
                    page_path,
                    {
                        "content_hash": str(source.get("content_hash") or ""),
                        "vision_config_hash": config_hash,
                        "status": str(page_result.get("status") or "failed"),
                        "result": page_result,
                    },
                )

            self._update_pipeline_document_result(
                job_id,
                source_id,
                {
                    "vision_status": "processing",
                    "vision_attempt": int(result.get("vision_attempt", 0)) + 1,
                    "vision_error": None,
                    "vision_warnings": [],
                },
            )
            try:
                vision_result = await self.vision_processor.analyze_source(
                    path=source_path,
                    filename=str(source["filename"]),
                    source_id=source_id,
                    config=profile,
                    cache_get=cache_get,
                    cache_set=cache_set,
                    cancel_check=lambda: self.pipeline_job_cancel_requested(job_id),
                )
                payload = vision_result.payload(max_text=None)
                self._atomic_json_write(artifact_path, payload)
                failed_pages = int(payload.get("failed_page_count", 0))
                vision_status = "failed" if failed_pages else "completed"
                if failed_pages:
                    failed_sources.append(source_id)
                self._update_pipeline_document_result(
                    job_id,
                    source_id,
                    {
                        "vision_status": vision_status,
                        "vision_config_hash": config_hash,
                        "vision_page_count": int(payload.get("page_count", 0)),
                        "vision_selected_page_count": int(payload.get("selected_page_count", 0)),
                        "vision_processed_page_count": int(payload.get("processed_page_count", 0)),
                        "vision_failed_page_count": failed_pages,
                        "vision_block_count": len(payload.get("blocks") or []),
                        "vision_warnings": list(payload.get("warnings") or []),
                        "vision_error": (
                            f"{failed_pages} visual page(s) failed."
                            if failed_pages
                            else None
                        ),
                    },
                )
                completed.append({**source, **payload, "reused": False})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error = self._safe_pipeline_error(exc)
                failed_sources.append(source_id)
                self._update_pipeline_document_result(
                    job_id,
                    source_id,
                    {"vision_status": "failed", "vision_error": error},
                )

        if failed_sources and str(profile.get("failure_policy")) == "strict":
            raise PipelineJobStateError(
                f"Strict vision policy blocked the candidate after {len(failed_sources)} source failure(s)."
            )
        if failed_sources:
            def add_warning(job_record: dict[str, Any]) -> None:
                warnings = list(job_record.get("warnings") or [])
                warnings.append(
                    f"{len(failed_sources)} source(s) had visual processing failures."
                )
                job_record["warnings"] = list(dict.fromkeys(warnings))

            self._update_pipeline_job(job_id, add_warning)
        return completed

    async def process_pipeline_job_sources(self, job_id: str) -> list[dict[str, Any]]:
        """Process each immutable source independently and reuse completed artifacts."""

        job = self.get_pipeline_job(job_id)
        snapshot = job.get("config_snapshot", {})
        profile = dict(
            snapshot.get("processor_profile")
            or snapshot.get("stages", {}).get("stage_processor")
            or self._default_pipeline_draft_stages()["stage_processor"]
        )
        config_hash = self._mapping_sha256(profile)
        results_by_source = {
            str(item.get("source_id")): item
            for item in job.get("document_results", [])
            if isinstance(item, dict)
        }
        completed: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []

        for source in job.get("sources", []):
            source_id = str(source["source_id"])
            result = results_by_source.get(source_id)
            if result is None:
                raise PipelineJobStateError(
                    f"Processor result state is missing for source: {source_id}"
                )
            artifact_path = self._pipeline_processed_path(str(result["artifact_key"]))
            reusable = (
                result.get("status") == "completed"
                and result.get("vision_status") in {"completed", "skipped"}
                and result.get("content_hash") == source.get("content_hash")
                and result.get("processor_config_hash") == config_hash
                and artifact_path.is_file()
            )
            if reusable:
                try:
                    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
                    completed.append({**source, **artifact, "reused": True})
                    continue
                except (OSError, json.JSONDecodeError):
                    reusable = False

            started = time.perf_counter()
            self._update_pipeline_document_result(
                job_id,
                source_id,
                {
                    "status": "processing",
                    "attempt_increment": True,
                    "error": None,
                    "warnings": [],
                },
            )
            try:
                source_path = self._pipeline_snapshot_path(str(source["snapshot_key"]))
                extracted_text = (
                    source_path.read_text(encoding="utf-8")
                    if source.get("content_mode") == "extracted_text"
                    else None
                )
                visual_blocks: list[dict[str, Any]] = []
                vision_artifact_key = str(result.get("vision_artifact_key") or "")
                if vision_artifact_key:
                    vision_path = self._pipeline_vision_path(vision_artifact_key)
                    if vision_path.is_file():
                        visual_payload = json.loads(vision_path.read_text(encoding="utf-8"))
                        visual_blocks = [
                            dict(item)
                            for item in visual_payload.get("blocks", [])
                            if isinstance(item, dict)
                        ]
                document = await asyncio.to_thread(
                    self.document_processor.process,
                    source_path,
                    filename=str(source["filename"]),
                    source_id=source_id,
                    config=profile,
                    extracted_text=extracted_text,
                    extra_blocks=visual_blocks,
                )
                if not isinstance(document, ProcessedDocument):
                    raise PipelineJobStateError(
                        "Structured processor returned an unsupported document payload."
                    )
                mode = str(profile.get("mode") or "general")
                generation = await self._generate_processor_items(
                    document,
                    mode=mode,
                    model_id=str(profile.get("model_id") or ""),
                    max_items=int(profile.get("max_generated_items", 20)),
                    parent_run_reference=f"rag_processor:job:{job_id}:{source_id}",
                    stable=True,
                )
                generated = generation.items
                artifact = {
                    "processed_document": document.payload(
                        include_text=True,
                        max_block_text=None,
                    ),
                    "generated_items": [item.payload(max_text=None) for item in generated],
                }
                artifact_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = artifact_path.with_suffix(artifact_path.suffix + ".tmp")
                temporary.write_text(
                    json.dumps(artifact, ensure_ascii=False),
                    encoding="utf-8",
                )
                os.replace(temporary, artifact_path)
                duration_ms = round((time.perf_counter() - started) * 1000, 2)
                generated_count = len(generated)
                result_values = {
                    "status": "completed",
                    "content_hash": str(source.get("content_hash") or ""),
                    "processor_config_hash": config_hash,
                    "block_count": len(document.blocks),
                    "generated_count": generated_count,
                    "qa_count": generated_count if mode == "qa" else 0,
                    "summary_count": generated_count if mode == "summary" else 0,
                    "warnings": list(document.warnings),
                    "error": None,
                    "duration_ms": duration_ms,
                    "execution_mode": generation.execution_mode,
                    "provider_route_receipts": (
                        generation.provider_route_receipts
                    ),
                }
                self._update_pipeline_document_result(
                    job_id,
                    source_id,
                    result_values,
                )
                completed.append({**source, **artifact, "reused": False})
            except Exception as exc:
                error = self._safe_pipeline_error(exc)
                duration_ms = round((time.perf_counter() - started) * 1000, 2)
                self._update_pipeline_document_result(
                    job_id,
                    source_id,
                    {
                        "status": "failed",
                        "error": error,
                        "duration_ms": duration_ms,
                        "execution_mode": (
                            "managed"
                            if isinstance(getattr(exc, "receipt", None), dict)
                            else "legacy"
                        ),
                        "provider_route_receipts": (
                            getattr(exc, "receipt", None)
                            if isinstance(getattr(exc, "receipt", None), dict)
                            else None
                        ),
                    },
                )
                failed.append({"source_id": source_id, "error": error})

        failure_policy = str(profile.get("failure_policy") or "continue_on_error")
        processor_error: str | None = None
        if not completed:
            processor_error = "All source documents failed during processing."
        elif failed and failure_policy == "strict":
            processor_error = (
                f"Strict processor policy blocked the candidate after {len(failed)} "
                "document failure(s)."
            )
        warnings = [
            f"{len(failed)} document(s) failed and were excluded from this candidate."
        ] if failed and processor_error is None else []

        def finish(job_record: dict[str, Any]) -> None:
            job_record["processor_error"] = processor_error
            existing = list(job_record.get("warnings") or [])
            job_record["warnings"] = list(dict.fromkeys([*existing, *warnings]))

        self._update_pipeline_job(job_id, finish)
        return completed

    def processor_gate_error(self, job_id: str) -> str | None:
        value = self.get_pipeline_job(job_id).get("processor_error")
        return str(value) if value else None

    def pipeline_job_cancel_requested(self, job_id: str) -> bool:
        return bool(self.get_pipeline_job(job_id).get("cancel_requested"))

    def request_pipeline_job_cancel(self, job_id: str) -> dict[str, Any]:
        def update(job: dict[str, Any]) -> None:
            status = str(job.get("status"))
            if status not in {"queued", "running"}:
                raise PipelineJobStateError("Only queued or running jobs can be cancelled.")
            if status == "queued":
                job["status"] = "cancelled"
                job["completed_at"] = time.time()
            else:
                job["cancel_requested"] = True

        job = self._update_pipeline_job(job_id, update)
        return self.pipeline_job_payload(job)

    def cancel_running_pipeline_job(self, job_id: str) -> None:
        def update(job: dict[str, Any]) -> None:
            job["status"] = "cancelled"
            job["completed_at"] = time.time()
            job["error"] = "Cancelled by user."
            for stage in job["stages"]:
                if stage["status"] in {"pending", "running"}:
                    stage["status"] = "cancelled"

        self._update_pipeline_job(job_id, update)

    def retry_pipeline_job(self, job_id: str) -> dict[str, Any]:
        def update(job: dict[str, Any]) -> None:
            if job.get("status") not in {"failed", "cancelled"}:
                raise PipelineJobStateError("Only failed or cancelled jobs can be retried.")
            if not job.get("sources"):
                raise PipelineJobStateError(
                    "This job has no remaining source documents and cannot be retried."
                )
            if job.get("deletion_invalidated"):
                raise PipelineJobStateError(
                    "This job was invalidated by source deletion and cannot be retried."
                )
            if str(job.get("embedding_execution_mode") or "") == "managed":
                raise PipelineJobStateError(
                    "Managed embedding evidence is immutable; create a new pipeline job "
                    "instead of retrying this job."
                )
            if any(
                isinstance(item, dict)
                and str(item.get("execution_mode") or "") == "managed"
                and isinstance(item.get("provider_route_receipts"), dict)
                for item in job.get("document_results", [])
            ):
                raise PipelineJobStateError(
                    "Managed processor evidence is immutable; create a new pipeline job "
                    "instead of retrying this job."
                )
            job.update(
                {
                    "status": "queued",
                    "stages": self._new_pipeline_job_stages(),
                    "cancel_requested": False,
                    "error": None,
                    "started_at": None,
                    "completed_at": None,
                    "processor_error": None,
                    "warnings": [],
                }
            )
            for result in job.get("document_results", []):
                if not isinstance(result, dict):
                    continue
                if result.get("vision_status") == "failed":
                    result.update(
                        {
                            "vision_status": "pending",
                            "vision_error": None,
                            "vision_failed_page_count": 0,
                            "status": "pending",
                        }
                    )
                if result.get("status") != "completed":
                    result.update(
                        {
                            "status": "pending",
                            "error": None,
                            "duration_ms": None,
                        }
                    )

        job = self._update_pipeline_job(job_id, update)
        return self.pipeline_job_payload(job)

    def cleanup_invalidated_pipeline_job(self, job_id: str) -> None:
        """Strictly purge an invalidated run, then persist its cleanup ack."""

        job = self.get_pipeline_job(job_id)
        if not job.get("deletion_invalidated"):
            return
        if job.get("status") == "running":
            raise PipelineJobStateError(
                "Running pipeline jobs cannot acknowledge deletion cleanup."
            )
        namespace = str(job.get("candidate_namespace") or "")
        try:
            if namespace:
                self.vector_store.delete_knowledge_base(namespace)
                self.lexical_store.delete_namespace(namespace)
            for path in (
                self.pipeline_sources_dir / job_id,
                self.pipeline_processed_dir / job_id,
                self.pipeline_vision_dir / job_id,
            ):
                if path.exists():
                    shutil.rmtree(path)
        except Exception:
            def mark_pending(current: dict[str, Any]) -> None:
                current["deletion_artifacts_purged"] = False
                current["deletion_cleanup_error"] = "rag_pipeline_cleanup_failed"

            self._update_pipeline_job(job_id, mark_pending)
            raise

        def mark_purged(current: dict[str, Any]) -> None:
            current["deletion_artifacts_purged"] = True
            current["deletion_cleanup_error"] = None

        self._update_pipeline_job(job_id, mark_purged)

    def fail_pipeline_job(self, job_id: str, error: str) -> None:
        def update(job: dict[str, Any]) -> None:
            job["status"] = "failed"
            job["error"] = str(error)[:500]
            job["completed_at"] = time.time()
            for stage in job["stages"]:
                if stage["status"] == "running":
                    stage["status"] = "failed"
                    stage["error"] = str(error)[:500]
                elif stage["status"] == "pending":
                    stage["status"] = "blocked"

        self._update_pipeline_job(job_id, update)

    def complete_pipeline_job(
        self,
        job_id: str,
        *,
        document_count: int,
        chunk_count: int,
    ) -> dict[str, Any]:
        version_holder: dict[str, Any] = {}

        def update(metadata: dict[str, Any], job: dict[str, Any]) -> None:
            if (
                job.get("status") != "running"
                or bool(job.get("cancel_requested"))
                or bool(job.get("deletion_invalidated"))
            ):
                raise PipelineJobStateError(
                    "Cancelled or deletion-invalidated pipeline jobs cannot publish a candidate version."
                )
            now = time.time()
            processor_profile = json.loads(
                json.dumps(
                    job["config_snapshot"].get("processor_profile")
                    or job["config_snapshot"].get("stages", {}).get(
                        "stage_processor", {}
                    )
                )
            )
            document_results = [
                {
                    key: json.loads(json.dumps(value))
                    for key, value in result.items()
                    if key
                    not in {
                        "artifact_key",
                        "vision_artifact_key",
                        "content_hash",
                        "processor_config_hash",
                        "vision_config_hash",
                    }
                }
                for result in job.get("document_results", [])
                if isinstance(result, dict)
            ]
            version = {
                "version_id": str(job["candidate_version_id"]),
                "kb_id": str(job["kb_id"]),
                "version": int(job["candidate_version"]),
                "status": "ready",
                "namespace": str(job["candidate_namespace"]),
                "draft_id": str(job["draft_id"]),
                "draft_version": int(job["draft_version"]),
                "config_snapshot": json.loads(json.dumps(job["config_snapshot"])),
                "index_schema_version": int(job["config_snapshot"].get("index_schema_version", 1)),
                "embedding_profile": json.loads(
                    json.dumps(job["config_snapshot"].get("embedding_profile", {}))
                ),
                "embedding_space_fingerprint": str(
                    job.get("embedding_space_fingerprint") or ""
                ),
                "embedding_execution_mode": str(
                    job.get("embedding_execution_mode") or "legacy"
                ),
                "provider_route_receipts": json.loads(
                    json.dumps(job.get("provider_route_receipts"))
                ),
                "retrieval_profile": json.loads(
                    json.dumps(job["config_snapshot"].get("retrieval_profile", {}))
                ),
                "vector_index_ready": True,
                "lexical_index_ready": True,
                "source_summary": [
                    {
                        key: value
                        for key, value in source.items()
                        if key not in {"snapshot_key", "content_hash"}
                    }
                    for source in job["sources"]
                ],
                "processor_profile": processor_profile,
                "vision_profile": json.loads(
                    json.dumps(
                        job["config_snapshot"].get("vision_profile")
                        or job["config_snapshot"].get("stages", {}).get(
                            "stage_image_understanding", {}
                        )
                    )
                ),
                "document_results": document_results,
                "document_count": document_count,
                "chunk_count": chunk_count,
                "block_count": sum(
                    int(item.get("block_count", 0)) for item in document_results
                ),
                "qa_count": sum(
                    int(item.get("qa_count", 0)) for item in document_results
                ),
                "summary_count": sum(
                    int(item.get("summary_count", 0)) for item in document_results
                ),
                "vision_page_count": sum(
                    int(item.get("vision_page_count", 0)) for item in document_results
                ),
                "vision_processed_page_count": sum(
                    int(item.get("vision_processed_page_count", 0)) for item in document_results
                ),
                "vision_failed_page_count": sum(
                    int(item.get("vision_failed_page_count", 0)) for item in document_results
                ),
                "vision_block_count": sum(
                    int(item.get("vision_block_count", 0)) for item in document_results
                ),
                "warnings": list(job.get("warnings") or []),
                "origin": json.loads(json.dumps(job.get("origin") or {})),
                "promotion_required": bool(
                    (job.get("origin") or {}).get("promotion_required")
                ),
                "base_version_id": job.get("base_version_id"),
                "job_id": job_id,
                "created_at": now,
                "activated_at": None,
            }
            metadata["pipeline_versions"][version["version_id"]] = version
            job["status"] = "succeeded"
            job["completed_at"] = now
            job["updated_at"] = now
            job["error"] = None
            version_holder.update(version)

        self._update_pipeline_job_with_metadata(job_id, update)
        return self.pipeline_version_payload(version_holder)

    def list_pipeline_versions(self, kb_id: str) -> list[dict[str, Any]]:
        metadata = self._read_metadata()
        self._ensure_kb_exists(metadata, kb_id)
        active_id = metadata["pipeline_active_versions"].get(kb_id)
        items = [
            self.pipeline_version_payload(item, active_id=active_id)
            for item in metadata["pipeline_versions"].values()
            if item.get("kb_id") == kb_id
            and str((item.get("origin") or {}).get("kind") or "")
            != "rag_strategy_tuner_trial"
        ]
        items.sort(key=lambda item: int(item["version"]), reverse=True)
        return items

    def get_pipeline_version(self, version_id: str) -> dict[str, Any]:
        metadata = self._read_metadata()
        version = metadata["pipeline_versions"].get(version_id)
        if not isinstance(version, dict):
            raise PipelineVersionNotFoundError("Knowledge pipeline version not found.")
        self._ensure_kb_exists(metadata, str(version.get("kb_id") or ""))
        return json.loads(json.dumps(version))

    def pipeline_version_evidence(self, version_id: str) -> dict[str, Any]:
        """Return a credential-free identity receipt for one immutable index."""

        version = self.get_pipeline_version(version_id)
        stored_embedding = version.get("embedding_profile")
        if (
            isinstance(stored_embedding, dict)
            and isinstance(stored_embedding.get("requested"), dict)
            and isinstance(stored_embedding.get("effective"), dict)
        ):
            embedding = json.loads(json.dumps(stored_embedding))
        else:
            embedding = self._resolved_embedding_profile_for_query(stored_embedding)
        requested = dict(embedding.get("requested") or {})
        effective = dict(embedding.get("effective") or {})
        safe_embedding = {
            "requested": {
                "provider": str(requested.get("provider") or ""),
                "model": str(requested.get("model") or ""),
            },
            "effective": {
                "provider": str(effective.get("provider") or ""),
                "model": str(effective.get("model") or ""),
                "dimension": int(effective.get("dimension") or 0),
                "degraded": bool(effective.get("degraded")),
                "ready": bool(effective.get("ready")),
                "reason": str(effective.get("reason") or "") or None,
                "access_mode": str(effective.get("access_mode") or "legacy"),
                "embedding_space_fingerprint": str(
                    version.get("embedding_space_fingerprint")
                    or embedding.get("embedding_space_fingerprint")
                    or ""
                ),
            },
        }
        retrieval = self._retrieval_config_for_version(
            version,
            None,
            top_k=None,
        ).payload()
        source_manifest_fingerprint = self._mapping_sha256(
            {"sources": list(version.get("source_summary") or [])}
        )
        configuration_fingerprint = self._mapping_sha256(
            {
                "index_schema_version": int(version.get("index_schema_version") or 1),
                "embedding": safe_embedding,
                "retrieval": retrieval,
                "processor": dict(version.get("processor_profile") or {}),
                "vision": dict(version.get("vision_profile") or {}),
            }
        )
        version_fingerprint = self._mapping_sha256(
            {
                "version_id": version_id,
                "version": int(version.get("version") or 0),
                "source_manifest_fingerprint": source_manifest_fingerprint,
                "configuration_fingerprint": configuration_fingerprint,
                "document_count": int(version.get("document_count") or 0),
                "chunk_count": int(version.get("chunk_count") or 0),
            }
        )
        return {
            "schema_version": "rag-version-evidence-v1",
            "version_id": version_id,
            "version": int(version.get("version") or 0),
            "index_schema_version": int(version.get("index_schema_version") or 1),
            "document_count": int(version.get("document_count") or 0),
            "chunk_count": int(version.get("chunk_count") or 0),
            "embedding": safe_embedding,
            "retrieval": retrieval,
            "source_manifest_fingerprint": source_manifest_fingerprint,
            "configuration_fingerprint": configuration_fingerprint,
            "version_fingerprint": version_fingerprint,
        }

    def pipeline_version_payload(
        self,
        version: dict[str, Any],
        *,
        active_id: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            key: json.loads(json.dumps(value))
            for key, value in version.items()
            if key not in {"namespace", "config_snapshot"}
        }
        payload["active"] = str(active_id or "") == str(version.get("version_id"))
        return payload

    def activate_pipeline_version(self, version_id: str) -> dict[str, Any]:
        with self._metadata_lock:
            metadata = self._read_metadata_unlocked()
            version = metadata["pipeline_versions"].get(version_id)
            if not isinstance(version, dict):
                raise PipelineVersionNotFoundError("Knowledge pipeline version not found.")
            if str((version.get("origin") or {}).get("kind") or "") == "rag_strategy_tuner_trial":
                raise PipelineJobStateError(
                    "Strategy tuning trial versions cannot be activated."
                )
            kb_id = str(version["kb_id"])
            self._ensure_kb_exists(metadata, kb_id)
            previous_id = metadata["pipeline_active_versions"].get(kb_id)
            if previous_id and previous_id in metadata["pipeline_versions"]:
                metadata["pipeline_versions"][previous_id]["status"] = "ready"
            version["status"] = "active"
            version["activated_at"] = time.time()
            metadata["pipeline_active_versions"][kb_id] = version_id
            metadata["knowledge_bases"][kb_id]["updated_at"] = time.time()
            self._write_metadata_unlocked(metadata)
            return self.pipeline_version_payload(version, active_id=version_id)

    def pipeline_version_cost_summary(self, version_id: str) -> dict[str, Any]:
        """Return deterministic, explicitly estimated index cost metadata."""

        version = self.get_pipeline_version(version_id)
        chunk_count = max(0, int(version.get("chunk_count") or 0))
        embedding_profile = version.get("embedding_profile") or {}
        dimension = max(1, int(embedding_profile.get("dimension") or 1))
        estimated_vector_bytes = chunk_count * dimension * 4
        estimated_lexical_bytes = chunk_count * 256
        job = self.get_pipeline_job(str(version.get("job_id") or ""))
        started_at = job.get("started_at")
        completed_at = job.get("completed_at")
        build_duration_ms = None
        if isinstance(started_at, (int, float)) and isinstance(completed_at, (int, float)):
            build_duration_ms = max(0.0, round((completed_at - started_at) * 1000, 2))
        return {
            "chunk_count": chunk_count,
            "embedding_dimension": dimension,
            "estimated_vector_bytes": estimated_vector_bytes,
            "estimated_lexical_bytes": estimated_lexical_bytes,
            "estimated_index_bytes": estimated_vector_bytes + estimated_lexical_bytes,
            "build_duration_ms": build_duration_ms,
            "size_is_estimated": True,
        }

    def cleanup_strategy_tuning_trial_version(self, version_id: str) -> None:
        """Remove an isolated tuning trial after its safe statistics are persisted."""

        with self._metadata_lock:
            metadata = self._read_metadata_unlocked()
            version = metadata["pipeline_versions"].get(version_id)
            if not isinstance(version, dict):
                return
            if str((version.get("origin") or {}).get("kind") or "") != "rag_strategy_tuner_trial":
                raise PipelineJobStateError(
                    "Only strategy tuning trial versions can be cleaned up here."
                )
            if metadata["pipeline_active_versions"].get(str(version.get("kb_id") or "")) == version_id:
                raise PipelineJobStateError("An active knowledge version cannot be cleaned up.")
            job_id = str(version.get("job_id") or "")
            namespace = str(version.get("namespace") or "")
            job = metadata["pipeline_jobs"].get(job_id)
            if isinstance(job, dict) and str(job.get("status") or "") not in {
                "succeeded",
                "failed",
                "cancelled",
            }:
                raise PipelineJobStateError(
                    "A running strategy tuning trial cannot be cleaned up."
                )

        if namespace:
            self.vector_store.delete_knowledge_base(namespace)
            self.lexical_store.delete_namespace(namespace)
        for path in (
            self.pipeline_sources_dir / job_id,
            self.pipeline_processed_dir / job_id,
            self.pipeline_vision_dir / job_id,
        ):
            if path.exists():
                shutil.rmtree(path)

        with self._metadata_lock:
            metadata = self._read_metadata_unlocked()
            current = metadata["pipeline_versions"].get(version_id)
            if isinstance(current, dict):
                metadata["pipeline_versions"].pop(version_id, None)
                metadata["pipeline_jobs"].pop(job_id, None)
                self._write_metadata_unlocked(metadata)

    def cleanup_strategy_tuning_trial_job(self, job_id: str) -> None:
        """Remove a terminal trial job that did not publish a version."""

        with self._metadata_lock:
            metadata = self._read_metadata_unlocked()
            job = metadata["pipeline_jobs"].get(job_id)
            if not isinstance(job, dict):
                return
            if str((job.get("origin") or {}).get("kind") or "") != "rag_strategy_tuner_trial":
                raise PipelineJobStateError(
                    "Only strategy tuning trial jobs can be cleaned up here."
                )
            if str(job.get("status") or "") not in {"succeeded", "failed", "cancelled"}:
                raise PipelineJobStateError(
                    "A running strategy tuning trial cannot be cleaned up."
                )
            version_id = str(job.get("candidate_version_id") or "")
            if isinstance(metadata["pipeline_versions"].get(version_id), dict):
                pass
            else:
                namespace = str(job.get("candidate_namespace") or "")
                version_id = ""

        if version_id:
            self.cleanup_strategy_tuning_trial_version(version_id)
            return
        if namespace:
            self.vector_store.delete_knowledge_base(namespace)
            self.lexical_store.delete_namespace(namespace)
        for path in (
            self.pipeline_sources_dir / job_id,
            self.pipeline_processed_dir / job_id,
            self.pipeline_vision_dir / job_id,
        ):
            if path.exists():
                shutil.rmtree(path)
        with self._metadata_lock:
            metadata = self._read_metadata_unlocked()
            metadata["pipeline_jobs"].pop(job_id, None)
            self._write_metadata_unlocked(metadata)

    async def query_pipeline_version(
        self,
        version_id: str,
        question: str,
        *,
        top_k: int | None = None,
        retrieval: dict[str, Any] | None = None,
        generate_answer: bool = True,
    ) -> dict[str, Any]:
        version = self.get_pipeline_version(version_id)
        profile = self._retrieval_config_for_version(version, retrieval, top_k=top_k)
        result = await self._query_namespace(
            str(version["kb_id"]),
            str(version["namespace"]),
            question,
            config=profile,
            lexical_ready=bool(version.get("lexical_index_ready")),
            embedding_profile=version.get("embedding_profile"),
            generate_answer=generate_answer,
        )
        result = self._with_source_document_ids(result, version_id)
        return {
            "version_id": version_id,
            "version": int(version["version"]),
            **result,
        }

    def get_active_pipeline_version(self, kb_id: str) -> dict[str, Any] | None:
        metadata = self._read_metadata()
        self._ensure_kb_exists(metadata, kb_id)
        version_id = metadata["pipeline_active_versions"].get(kb_id)
        if not version_id:
            return None
        version = metadata["pipeline_versions"].get(version_id)
        return self.pipeline_version_payload(version, active_id=version_id) if isinstance(version, dict) else None

    def _new_pipeline_job_stages(self) -> list[dict[str, Any]]:
        return [
            {
                "id": stage_id,
                "title": title,
                "status": "pending",
                "progress": 0,
                "item_count": None,
                "started_at": None,
                "completed_at": None,
                "error": None,
            }
            for stage_id, title in PIPELINE_JOB_STAGES
        ]

    def _pipeline_stage(self, job: dict[str, Any], stage_id: str) -> dict[str, Any]:
        for stage in job["stages"]:
            if stage["id"] == stage_id:
                return stage
        raise PipelineJobStateError(f"Unknown pipeline job stage: {stage_id}")

    def _update_pipeline_job(
        self,
        job_id: str,
        update: Any,
    ) -> dict[str, Any]:
        def wrapped(metadata: dict[str, Any], job: dict[str, Any]) -> None:
            update(job)
            job["updated_at"] = time.time()

        return self._update_pipeline_job_with_metadata(job_id, wrapped)

    def _update_pipeline_job_with_metadata(
        self,
        job_id: str,
        update: Any,
    ) -> dict[str, Any]:
        with self._metadata_lock:
            metadata = self._read_metadata_unlocked()
            job = metadata["pipeline_jobs"].get(job_id)
            if not isinstance(job, dict):
                raise PipelineJobNotFoundError("Knowledge pipeline job not found.")
            update(metadata, job)
            job["updated_at"] = time.time()
            self._write_metadata_unlocked(metadata)
            return json.loads(json.dumps(job))

    def _pipeline_snapshot_path(self, snapshot_key: str) -> Path:
        path = (self.storage_dir / snapshot_key).resolve()
        root = self.pipeline_sources_dir.resolve()
        if path != root and root not in path.parents:
            raise PipelineJobStateError("Invalid pipeline source snapshot path.")
        return path

    def _pipeline_processed_path(self, artifact_key: str) -> Path:
        path = (self.storage_dir / artifact_key).resolve()
        root = self.pipeline_processed_dir.resolve()
        if path != root and root not in path.parents:
            raise PipelineJobStateError("Invalid pipeline processor artifact path.")
        return path

    def _pipeline_vision_path(self, artifact_key: str) -> Path:
        path = (self.storage_dir / artifact_key).resolve()
        root = self.pipeline_vision_dir.resolve()
        if path != root and root not in path.parents:
            raise PipelineJobStateError("Invalid pipeline vision artifact path.")
        return path

    def _atomic_json_write(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(temporary, path)

    def _update_pipeline_document_result(
        self,
        job_id: str,
        source_id: str,
        values: dict[str, Any],
    ) -> None:
        def update(job: dict[str, Any]) -> None:
            result = next(
                (
                    item
                    for item in job.get("document_results", [])
                    if isinstance(item, dict) and str(item.get("source_id")) == source_id
                ),
                None,
            )
            if result is None:
                raise PipelineJobStateError(
                    f"Processor result state is missing for source: {source_id}"
                )
            attempt_increment = bool(values.get("attempt_increment"))
            result.update(
                {
                    key: value
                    for key, value in values.items()
                    if key != "attempt_increment"
                }
            )
            if attempt_increment:
                result["attempt"] = int(result.get("attempt", 0)) + 1

        self._update_pipeline_job(job_id, update)

    def update_pipeline_document_chunk_counts(
        self,
        job_id: str,
        counts: dict[str, int],
    ) -> None:
        def update(job: dict[str, Any]) -> None:
            for result in job.get("document_results", []):
                if not isinstance(result, dict):
                    continue
                source_id = str(result.get("source_id") or "")
                if source_id in counts:
                    result["chunk_count"] = int(counts[source_id])

        self._update_pipeline_job(job_id, update)

    def update_pipeline_embedding_dimension(
        self,
        job_id: str,
        dimension: int,
    ) -> None:
        if dimension <= 0:
            raise PipelineJobStateError("Embedding dimension must be positive.")

        def update(job: dict[str, Any]) -> None:
            snapshot = job.get("config_snapshot")
            if not isinstance(snapshot, dict):
                raise PipelineJobStateError("Pipeline embedding snapshot is missing.")
            profile = snapshot.get("embedding_profile")
            if not isinstance(profile, dict):
                raise PipelineJobStateError("Pipeline embedding profile is missing.")
            effective = profile.get("effective")
            if not isinstance(effective, dict) or not bool(effective.get("ready")):
                raise PipelineJobStateError("Pipeline embedding profile is unavailable.")
            access_mode = str(effective.get("access_mode") or "legacy")
            if access_mode == "managed" and int(
                effective.get("vector_dimension") or effective.get("dimension") or 0
            ) != int(dimension):
                raise PipelineJobStateError(
                    "Managed Embedding dimension differs from its certified space."
                )
            if access_mode == "local_hash":
                identity = self._embedding_space_identity(
                    provider_kind=EMBEDDING_PROVIDER_HASH,
                    endpoint="local://deterministic-hash-v1",
                    model_id=HASH_EMBEDDING_MODEL,
                    vector_dimension=dimension,
                )
            elif access_mode == "legacy":
                base = self.embedder.api_base or "https://api.openai.com/v1"
                identity = self._embedding_space_identity(
                    provider_kind=EMBEDDING_PROVIDER_OPENAI_COMPATIBLE,
                    endpoint=f"{base.rstrip('/')}/embeddings",
                    model_id=str(effective.get("model") or self.embedder.model),
                    vector_dimension=dimension,
                )
            else:
                identity = None
            effective["dimension"] = int(dimension)
            profile["effective"] = effective
            profile["dimension"] = int(dimension)
            if identity is not None:
                profile["embedding_space_fingerprint"] = str(identity["fingerprint"])
            job["embedding_space_fingerprint"] = str(
                profile.get("embedding_space_fingerprint") or ""
            )

        self._update_pipeline_job(job_id, update)

    async def embed_managed_pipeline_chunks(
        self,
        job_id: str,
        texts: list[str],
    ) -> list[list[float]]:
        job = self.get_pipeline_job(job_id)
        snapshot = job.get("config_snapshot")
        profile = (
            snapshot.get("embedding_profile")
            if isinstance(snapshot, dict)
            else None
        )
        effective = (
            dict(profile.get("effective") or {})
            if isinstance(profile, dict)
            else {}
        )
        if str(effective.get("access_mode") or "") != "managed":
            raise PipelineJobStateError(
                "Pipeline embedding profile is not managed."
            )
        gateway = self._managed_embedding_gateway()
        if gateway is None or str(gateway.routing_mode()) != "managed_required":
            raise PipelineDraftValidationError(
                "Managed Embedding policy is not active."
            )
        model_id = str(effective.get("model") or "")
        expected_fingerprint = str(
            profile.get("embedding_space_fingerprint") or ""
        )
        if not model_id or not expected_fingerprint:
            raise PipelineJobStateError(
                "Managed Embedding snapshot is missing its exact space identity."
            )
        try:
            run = gateway.start_index_run(job_id)
        except Exception as exc:
            receipt = getattr(exc, "receipt", None)
            if isinstance(receipt, dict):
                self._update_pipeline_embedding_evidence(
                    job_id,
                    identity=None,
                    receipt=receipt,
                )
            code = str(
                getattr(exc, "code", "provider_embedding_preflight_failed")
            )
            raise ManagedEmbeddingRouteError(
                code,
                f"Managed Embedding failed: {code}",
            ) from exc

        vectors: list[list[float]] = []
        configured_batch_size = min(
            256,
            _safe_env_int("RAG_MANAGED_EMBEDDING_BATCH_SIZE", 64),
        )
        batch_size = gateway.response_bounded_batch_size(
            vector_dimension=int(effective.get("dimension") or 0),
            requested_batch_size=configured_batch_size,
        )
        identity_payload: dict[str, Any] | None = None
        try:
            for offset in range(0, len(texts), batch_size):
                batch_index = offset // batch_size
                result = await run.embed(
                    texts[offset : offset + batch_size],
                    model_id=model_id,
                    logical_call_key=f"embedding_batch:{batch_index}",
                    call_sequence=batch_index + 1,
                    expected_space_fingerprint=expected_fingerprint,
                )
                vectors.extend(result.vectors)
                identity_payload = dict(result.identity.payload())
            receipt = run.finish_success()
        except Exception as exc:
            receipt = getattr(exc, "receipt", None)
            self._update_pipeline_embedding_evidence(
                job_id,
                identity=identity_payload,
                receipt=(receipt if isinstance(receipt, dict) else run.receipt_summary()),
            )
            code = str(getattr(exc, "code", "provider_embedding_failed"))
            raise ManagedEmbeddingRouteError(
                code,
                f"Managed Embedding failed: {code}",
            ) from exc
        self._update_pipeline_embedding_evidence(
            job_id,
            identity=identity_payload,
            receipt=receipt,
        )
        return vectors

    def _update_pipeline_embedding_evidence(
        self,
        job_id: str,
        *,
        identity: dict[str, Any] | None,
        receipt: dict[str, Any] | None,
    ) -> None:
        safe_receipt = json.loads(json.dumps(receipt)) if receipt is not None else None

        def update(job: dict[str, Any]) -> None:
            job["provider_route_receipts"] = safe_receipt
            if not identity:
                return
            snapshot = job.get("config_snapshot")
            profile = (
                snapshot.get("embedding_profile")
                if isinstance(snapshot, dict)
                else None
            )
            effective = (
                profile.get("effective")
                if isinstance(profile, dict)
                else None
            )
            if not isinstance(effective, dict):
                raise PipelineJobStateError(
                    "Pipeline embedding profile is missing."
                )
            expected = str(profile.get("embedding_space_fingerprint") or "")
            actual = str(identity.get("fingerprint") or "")
            if not expected or actual != expected:
                raise PipelineJobStateError(
                    "Managed Embedding space changed while building the index."
                )
            effective["dimension"] = int(identity["vector_dimension"])
            profile["effective"] = effective
            profile["dimension"] = int(identity["vector_dimension"])
            profile["embedding_space_fingerprint"] = actual
            snapshot["embedding_profile"] = profile
            job["embedding_space_fingerprint"] = actual

        self._update_pipeline_job(job_id, update)

    def _file_sha256(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _mapping_sha256(self, value: dict[str, Any]) -> str:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _safe_pipeline_error(self, exc: Exception) -> str:
        value = str(exc).strip() or exc.__class__.__name__
        for root in (self.storage_dir, self.uploads_dir):
            value = value.replace(str(root), "[local-path]")
            value = value.replace(str(root.resolve()), "[local-path]")
        value = re.sub(r"(?i)(bearer\s+|api[_-]?key[=:]\s*)\S+", r"\1[redacted]", value)
        return value[:500]

    def _safe_pipeline_origin(self, origin: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(origin, dict):
            return {}
        return {
            key: value
            for key, value in origin.items()
            if key in {"kind", "proposal_id", "promotion_required", "source_run_id"}
            and isinstance(value, (str, bool, int, float, type(None)))
        }

    def _required_proposal_text(self, value: Any, field_name: str, limit: int) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"Knowledge write proposal {field_name} is required.")
        if len(text) > limit:
            raise ValueError(
                f"Knowledge write proposal {field_name} must be at most {limit} characters."
            )
        return text

    def _optional_proposal_text(self, value: Any, limit: int) -> str | None:
        text = str(value or "").strip()
        return text[:limit] if text else None

    def _proposal_tags(self, tags: list[str] | None) -> list[str]:
        if tags is None:
            return []
        if not isinstance(tags, list) or len(tags) > 20:
            raise ValueError("Knowledge write proposal tags must contain at most 20 items.")
        result: list[str] = []
        for value in tags:
            tag = str(value or "").strip()[:50]
            if tag and tag not in result:
                result.append(tag)
        return result

    def _knowledge_write_proposal_or_raise(
        self,
        metadata: dict[str, Any],
        proposal_id: str,
    ) -> dict[str, Any]:
        proposal = metadata["knowledge_write_proposals"].get(proposal_id)
        if not isinstance(proposal, dict):
            raise KnowledgeWriteProposalNotFoundError("Knowledge write proposal not found.")
        return proposal

    def _assert_pending_proposal(
        self,
        proposal: dict[str, Any],
        expected_revision: int,
    ) -> None:
        if proposal.get("status") != "pending":
            raise KnowledgeWriteProposalConflictError(
                "Only pending knowledge write proposals can be changed."
            )
        if int(proposal.get("revision", 0)) != int(expected_revision):
            raise KnowledgeWriteProposalConflictError(
                "Knowledge write proposal changed; reload before continuing."
            )

    def _knowledge_write_proposal_payload(
        self,
        proposal: dict[str, Any],
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        payload = json.loads(json.dumps(proposal))
        job_id = str(proposal.get("job_id") or "")
        job = metadata["pipeline_jobs"].get(job_id) if job_id else None
        payload["build_status"] = (
            str(job.get("status") or "unknown") if isinstance(job, dict) else None
        )
        candidate_id = str(proposal.get("candidate_version_id") or "")
        version = metadata["pipeline_versions"].get(candidate_id) if candidate_id else None
        payload["candidate_ready"] = isinstance(version, dict)
        payload["candidate_active"] = (
            isinstance(version, dict)
            and metadata["pipeline_active_versions"].get(str(proposal.get("kb_id")))
            == candidate_id
        )
        return payload

    def _create_managed_proposal_document(
        self,
        proposal: dict[str, Any],
    ) -> dict[str, Any]:
        kb_id = str(proposal["kb_id"])
        with self._metadata_lock:
            metadata = self._read_metadata_unlocked()
            self._ensure_kb_exists(metadata, kb_id)
            self._knowledge_base_write_claims[kb_id] = (
                self._knowledge_base_write_claims.get(kb_id, 0) + 1
            )
        try:
            return self._create_managed_proposal_document_claimed(proposal)
        finally:
            with self._metadata_lock:
                remaining = self._knowledge_base_write_claims.get(kb_id, 0) - 1
                if remaining > 0:
                    self._knowledge_base_write_claims[kb_id] = remaining
                else:
                    self._knowledge_base_write_claims.pop(kb_id, None)

    def _create_managed_proposal_document_claimed(
        self,
        proposal: dict[str, Any],
    ) -> dict[str, Any]:
        kb_id = str(proposal["kb_id"])
        doc_id = f"doc_{uuid.uuid4().hex}"
        filename = f"knowledge_proposal_{proposal['proposal_id']}.md"
        target_dir = self.uploads_dir / kb_id
        target_dir.mkdir(parents=True, exist_ok=True)
        stored_path = target_dir / f"{doc_id}_{filename}"
        title = str(proposal["title"])
        tags = [str(item) for item in proposal.get("tags", [])]
        tag_line = f"\n\nTags: {', '.join(tags)}" if tags else ""
        body = f"# {title}\n\n{proposal['content']}{tag_line}\n"
        stored_path.write_text(body, encoding="utf-8")
        now = time.time()
        document = {
            "id": doc_id,
            "kb_id": kb_id,
            "filename": filename,
            "stored_path": str(stored_path),
            "size": len(body.encode("utf-8")),
            "chunk_count": 0,
            "content_type": "text/markdown",
            "ingestion_status": "pipeline_required",
            "visual_candidate": False,
            "visual_metadata": {},
            "managed_origin": "knowledge_write_proposal",
            "proposal_id": str(proposal["proposal_id"]),
            "created_at": now,
        }
        with self._metadata_lock:
            metadata = self._read_metadata_unlocked()
            knowledge_base = metadata["knowledge_bases"].get(kb_id)
            if not isinstance(knowledge_base, dict) or knowledge_base.get(
                "deletion_status"
            ):
                document["deletion_status"] = "deleting"
                metadata["documents"][doc_id] = document
                deletion = metadata["knowledge_base_deletions"].setdefault(
                    kb_id,
                    {
                        "tenant_id": "local",
                        "requested_at": now,
                        "deleted_at": None,
                        "status": "cleanup_pending",
                        "error_code": "rag_knowledge_base_write_pending",
                        "document_ids": [],
                        "asset_ids": [],
                    },
                )
                deletion["document_ids"] = sorted(
                    set(str(item) for item in deletion.get("document_ids", []))
                    | {doc_id}
                )
                metadata["document_deletions"][doc_id] = {
                    "tenant_id": "local",
                    "content_hash": self._file_sha256(stored_path),
                    "requested_at": now,
                    "deleted_at": None,
                    "status": "deleting",
                    "error_code": None,
                }
                self._write_metadata_unlocked(metadata)
                raise KnowledgeBaseDeletionError(
                    "Knowledge base deletion started during proposal approval; generated data was isolated."
                )
            metadata["documents"][doc_id] = document
            knowledge_base["updated_at"] = now
            self._write_metadata_unlocked(metadata)
        return self._document_payload(document)

    async def search_knowledge(
        self,
        kb_id: str,
        question: str,
        *,
        top_k: int = 5,
        retrieval: dict[str, Any] | None = None,
        version_id: str | None = None,
    ) -> dict[str, Any]:
        """Retrieve from an active or explicitly fixed namespace."""

        metadata = self._read_metadata()
        self._ensure_kb_exists(metadata, kb_id)
        active_version_id = (
            str(version_id)
            if version_id
            else metadata["pipeline_active_versions"].get(kb_id)
        )
        version = (
            metadata["pipeline_versions"].get(active_version_id)
            if active_version_id
            else None
        )
        if version_id and (
            not isinstance(version, dict) or str(version.get("kb_id") or "") != kb_id
        ):
            raise PipelineVersionNotFoundError(
                "Fixed knowledge pipeline version was not found."
            )
        namespace = str(version.get("namespace") or kb_id) if isinstance(version, dict) else kb_id
        config = self._retrieval_config_for_version(
            version if isinstance(version, dict) else None,
            retrieval,
            top_k=max(1, min(int(top_k), 10)),
        )
        result = await self._query_namespace(
            kb_id,
            namespace,
            question,
            config=config,
            lexical_ready=bool(isinstance(version, dict) and version.get("lexical_index_ready")),
            embedding_profile=(
                version.get("embedding_profile") if isinstance(version, dict) else None
            ),
            generate_answer=False,
        )
        version_id = str(version.get("version_id") or "") if isinstance(version, dict) else None
        result = self._with_source_document_ids(result, version_id)
        result["version_id"] = version_id
        return result

    def get_knowledge_chunk(
        self,
        kb_id: str,
        chunk_id: str,
        *,
        version_id: str | None = None,
    ) -> dict[str, Any]:
        """Read one chunk from an active or explicitly fixed namespace."""

        metadata = self._read_metadata()
        self._ensure_kb_exists(metadata, kb_id)
        active_version_id = (
            str(version_id)
            if version_id
            else metadata["pipeline_active_versions"].get(kb_id)
        )
        version = (
            metadata["pipeline_versions"].get(active_version_id)
            if active_version_id
            else None
        )
        if version_id and (
            not isinstance(version, dict) or str(version.get("kb_id") or "") != kb_id
        ):
            raise PipelineVersionNotFoundError(
                "Fixed knowledge pipeline version was not found."
            )
        namespace = str(version.get("namespace") or kb_id) if isinstance(version, dict) else kb_id
        chunk = self.vector_store.get_chunk(namespace, chunk_id)
        if chunk is None:
            raise DocumentNotFoundError("Knowledge chunk was not found in the active version.")
        if self._indexed_document_is_deleted(
            str(chunk.doc_id),
            self._deleted_document_ids(),
        ):
            raise DocumentNotFoundError("Knowledge chunk was deleted.")
        indexed_document_id = str(chunk.doc_id)
        version_id = str(version.get("version_id") or "") if isinstance(version, dict) else ""
        prefix = f"{version_id}_" if version_id else ""
        source_document_id = (
            indexed_document_id[len(prefix) :]
            if prefix and indexed_document_id.startswith(prefix)
            else indexed_document_id
        )
        return {
            "kb_id": kb_id,
            "version_id": version_id or None,
            "chunk_id": chunk.chunk_id,
            "document_id": source_document_id,
            "document_name": chunk.document_name,
            "text": chunk.text[:8000],
            "text_length": len(chunk.text),
            "truncated": len(chunk.text) > 8000,
            "chunk_index": chunk.chunk_index,
            "parent_chunk_id": chunk.parent_chunk_id,
            "chunk_type": chunk.chunk_type,
            "page_number": chunk.page_number,
            "slide": chunk.slide,
            "heading_path": list(chunk.heading_path),
            "sheet": chunk.sheet,
            "row_range": chunk.row_range,
            "visual_kind": chunk.visual_kind,
            "source_block_id": chunk.source_block_id,
        }

    def list_pipeline_artifact_chunks(self, artifact_id: str) -> list[dict[str, Any]]:
        """Return chunk metadata for one artifact without exposing embeddings."""

        document = self._document_for_artifact_id(artifact_id)
        chunks = self.vector_store.list_document_chunks(document["id"])
        return [
            {
                "chunk_id": chunk.chunk_id,
                "artifact_id": self._artifact_id(document["id"]),
                "knowledge_base_id": chunk.kb_id or document["kb_id"],
                "document_id": document["id"],
                "index": chunk.chunk_index,
                "text_preview": _preview_text(chunk.text),
                "text_length": len(chunk.text),
                "slide": chunk.slide,
                "heading_path": list(chunk.heading_path),
                "sheet": chunk.sheet,
                "row_range": chunk.row_range,
            }
            for chunk in chunks
        ]

    async def create_pipeline_citations(
        self,
        kb_id: str,
        question: str,
        *,
        top_k: int = 4,
        retrieval: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Return citation anchors using the existing RAG retrieval path."""

        result = await self.search_knowledge(
            kb_id,
            question,
            top_k=top_k,
            retrieval=retrieval,
        )
        return self.citation_anchors_from_search_result(result)

    def citation_anchors_from_search_result(
        self,
        result: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Build stable citation anchors without issuing a second retrieval."""

        citations: list[dict[str, Any]] = []
        for source in result.get("sources", []):
            if not isinstance(source, dict):
                continue
            chunk_id = str(source.get("chunk_id", ""))
            doc_id = str(source.get("source_document_id") or source.get("doc_id", ""))
            citations.append(
                {
                    "citation_id": f"citation_{chunk_id}" if chunk_id else f"citation_{len(citations)}",
                    "chunk_id": chunk_id,
                    "artifact_id": self._artifact_id(doc_id) if doc_id else "",
                    "document_id": doc_id,
                    "document_name": str(source.get("document_name", "")),
                    "score": float(source.get("score", 0.0)),
                    "snippet": _preview_text(
                        str(source.get("matched_text") or source.get("text", ""))
                    ),
                    "page_number": source.get("page_number"),
                    "slide": source.get("slide"),
                    "heading_path": source.get("heading_path", []),
                    "sheet": source.get("sheet"),
                    "row_range": source.get("row_range"),
                    "visual_kind": source.get("visual_kind"),
                    "source_block_id": source.get("source_block_id"),
                }
            )
        return citations

    def _with_source_document_ids(
        self,
        result: dict[str, Any],
        version_id: str | None,
    ) -> dict[str, Any]:
        if not version_id:
            return result
        prefix = f"{version_id}_"
        for source in result.get("sources", []):
            indexed_document_id = str(source.get("doc_id") or "")
            source["source_document_id"] = (
                indexed_document_id[len(prefix) :]
                if indexed_document_id.startswith(prefix)
                else indexed_document_id
            )
        return result

    def _deleted_document_ids(self, metadata: dict[str, Any] | None = None) -> set[str]:
        current = metadata if metadata is not None else self._read_metadata()
        return {
            str(doc_id)
            for doc_id, deletion in current.get("document_deletions", {}).items()
            if isinstance(deletion, dict)
            and deletion.get("status")
            in {"deleting", "cleanup_pending", "failed", "deleted"}
        }

    def _indexed_document_is_deleted(
        self,
        indexed_doc_id: str,
        deleted_document_ids: set[str],
    ) -> bool:
        return any(
            indexed_doc_id == doc_id or indexed_doc_id.endswith(f"_{doc_id}")
            for doc_id in deleted_document_ids
        )

    def delete_document(self, doc_id: str, *, allow_locked: bool = False) -> None:
        """Claim one in-process delete while allowing restart recovery."""

        with self._metadata_lock:
            metadata = self._read_metadata_unlocked()
            document = metadata["documents"].get(doc_id)
            if isinstance(document, dict):
                self._assert_corpus_mutable(
                    metadata,
                    str(document.get("kb_id") or ""),
                    allow_locked=allow_locked,
                )
            if doc_id in self._document_delete_claims:
                raise DocumentDeletionError("Document cleanup is already in progress.")
            self._document_delete_claims.add(doc_id)
        try:
            self._delete_document_claimed(doc_id)
        finally:
            with self._metadata_lock:
                self._document_delete_claims.discard(doc_id)

    def _delete_document_claimed(self, doc_id: str) -> None:
        """Tombstone one document before purging every RAG-derived payload.

        The tombstone is committed before physical cleanup. Retrieval paths use
        it as a deny-list, so a failed cleanup can never expose the document
        again. A repeated DELETE retries failed cleanup safely.
        """

        with self._metadata_lock:
            metadata = self._read_metadata_unlocked()
            document = metadata["documents"].get(doc_id)
            deletion = metadata["document_deletions"].get(doc_id)
            if not isinstance(document, dict):
                if isinstance(deletion, dict) and deletion.get("status") == "deleted":
                    return
                raise DocumentNotFoundError("Document not found.")

            stored_path = Path(str(document.get("stored_path") or ""))
            content_hash = str(document.get("content_hash") or "")
            if not content_hash and self._is_managed_upload_path(stored_path) and stored_path.is_file():
                content_hash = self._file_sha256(stored_path)
            if content_hash:
                document["content_hash"] = content_hash
            requested_at = time.time()
            document["deletion_status"] = "deleting"
            metadata["document_deletions"][doc_id] = {
                "tenant_id": "local",
                "content_hash": content_hash,
                "requested_at": requested_at,
                "deleted_at": None,
                "status": "deleting",
                "error_code": None,
            }
            for job in metadata["pipeline_jobs"].values():
                if not isinstance(job, dict) or not self._job_references_document(job, doc_id):
                    continue
                if job.get("status") == "running":
                    job["cancel_requested"] = True
                    job["deletion_invalidated"] = True
                elif job.get("status") == "queued":
                    job["status"] = "cancelled"
                    job["deletion_invalidated"] = True
                    job["completed_at"] = requested_at
                    job["error"] = "Cancelled because a source document was deleted."
            kb_id = str(document["kb_id"])
            if kb_id in metadata["knowledge_bases"]:
                metadata["knowledge_bases"][kb_id]["updated_at"] = requested_at
            cleanup_snapshot = json.loads(json.dumps(metadata))
            document_snapshot = json.loads(json.dumps(document))
            self._write_metadata_unlocked(metadata)

        try:
            asset_cleanup_pending = self._purge_document_payloads(
                doc_id,
                document_snapshot,
                cleanup_snapshot,
            )
            pipeline_cleanup_pending = (
                self._invalidated_pipeline_cleanup_pending(doc_id)
            )
        except Exception as exc:
            with self._metadata_lock:
                metadata = self._read_metadata_unlocked()
                current = metadata["documents"].get(doc_id)
                if isinstance(current, dict):
                    current["deletion_status"] = "failed"
                deletion = metadata["document_deletions"].setdefault(doc_id, {})
                deletion.update(
                    {
                        "tenant_id": "local",
                        "content_hash": str(
                            deletion.get("content_hash")
                            or document_snapshot.get("content_hash")
                            or ""
                        ),
                        "deleted_at": None,
                        "status": "failed",
                        "error_code": "rag_document_cleanup_failed",
                    }
                )
                self._write_metadata_unlocked(metadata)
            raise DocumentDeletionError(
                "Document was isolated, but cleanup is incomplete; retry deletion."
            ) from exc

        if asset_cleanup_pending or pipeline_cleanup_pending:
            with self._metadata_lock:
                metadata = self._read_metadata_unlocked()
                current = metadata["documents"].get(doc_id)
                if isinstance(current, dict):
                    current["deletion_status"] = "cleanup_pending"
                    if asset_cleanup_pending:
                        current["asset_binding_removed"] = True
                deletion = metadata["document_deletions"].setdefault(doc_id, {})
                deletion.update(
                    {
                        "tenant_id": "local",
                        "content_hash": str(
                            deletion.get("content_hash")
                            or document_snapshot.get("content_hash")
                            or ""
                        ),
                        "deleted_at": None,
                        "status": "cleanup_pending",
                        "error_code": (
                            "rag_pipeline_cleanup_pending"
                            if pipeline_cleanup_pending
                            else "file_asset_cleanup_pending"
                        ),
                    }
                )
                self._write_metadata_unlocked(metadata)
            raise DocumentDeletionError(
                "Document was isolated, but pipeline or file asset cleanup is still pending."
            )

        with self._metadata_lock:
            metadata = self._read_metadata_unlocked()
            metadata["documents"].pop(doc_id, None)
            self._remove_document_references(metadata, doc_id)
            deletion = metadata["document_deletions"].setdefault(doc_id, {})
            deletion.update(
                {
                    "tenant_id": "local",
                    "content_hash": str(
                        deletion.get("content_hash")
                        or document_snapshot.get("content_hash")
                        or ""
                    ),
                    "deleted_at": time.time(),
                    "status": "deleted",
                    "error_code": None,
                }
            )
            self._write_metadata_unlocked(metadata)

    def _invalidated_pipeline_cleanup_pending(self, doc_id: str) -> bool:
        """Wait for running writers and strictly purge every invalidated run."""

        metadata = self._read_metadata()
        job_ids = [
            str(job_id)
            for job_id, job in metadata["pipeline_jobs"].items()
            if isinstance(job, dict)
            and job.get("deletion_invalidated")
            and self._job_references_document(job, doc_id)
        ]
        pending = False
        for job_id in job_ids:
            job = self.get_pipeline_job(job_id)
            if job.get("status") == "running":
                pending = True
                continue
            if job.get("deletion_artifacts_purged"):
                continue
            try:
                self.cleanup_invalidated_pipeline_job(job_id)
            except Exception:
                pending = True

        refreshed = self._read_metadata()
        for job_id in job_ids:
            job = refreshed["pipeline_jobs"].get(job_id)
            if not isinstance(job, dict):
                continue
            if job.get("status") == "running" or not job.get(
                "deletion_artifacts_purged"
            ):
                pending = True
        return pending

    def _purge_document_payloads(
        self,
        doc_id: str,
        document: dict[str, Any],
        metadata: dict[str, Any],
    ) -> bool:
        stored_path = Path(str(document.get("stored_path") or ""))
        if stored_path and self._is_managed_upload_path(stored_path):
            stored_path.unlink(missing_ok=True)

        self.vector_store.delete_document(doc_id)
        self.lexical_store.delete_document(doc_id)

        indexed_ids: set[str] = set()
        for version_id, version in metadata["pipeline_versions"].items():
            if isinstance(version, dict) and version.get("kb_id") == document.get("kb_id"):
                indexed_ids.add(f"{version_id}_{doc_id}")
        for job in metadata["pipeline_jobs"].values():
            if not isinstance(job, dict) or not self._job_references_document(job, doc_id):
                continue
            candidate_id = str(job.get("candidate_version_id") or "")
            if candidate_id:
                indexed_ids.add(f"{candidate_id}_{doc_id}")
            matching_results = [
                item
                for item in job.get("document_results", [])
                if isinstance(item, dict) and str(item.get("source_id")) == doc_id
            ]
            for source in job.get("sources", []):
                if not isinstance(source, dict) or str(source.get("source_id")) != doc_id:
                    continue
                key = str(source.get("snapshot_key") or "")
                if key:
                    self._pipeline_snapshot_path(key).unlink(missing_ok=True)
            for result in matching_results:
                processed_key = str(result.get("artifact_key") or "")
                if processed_key:
                    self._pipeline_processed_path(processed_key).unlink(missing_ok=True)
                vision_key = str(result.get("vision_artifact_key") or "")
                if vision_key:
                    vision_path = self._pipeline_vision_path(vision_key)
                    vision_path.unlink(missing_ok=True)
                    page_dir = vision_path.parent / f"{vision_path.stem}_pages"
                    if page_dir.exists():
                        shutil.rmtree(page_dir)
        for indexed_id in indexed_ids:
            self.vector_store.delete_document(indexed_id)
            self.lexical_store.delete_document(indexed_id)

        asset_id = str(document.get("asset_id") or "").strip()
        if asset_id:
            asset_service = get_file_asset_service()
            if document.get("asset_binding_removed"):
                return not asset_service.asset_cleanup_complete(asset_id)
            try:
                return asset_service.delete_asset(
                    asset_id,
                    purpose=FilePurpose.RAG,
                    scope_id=str(document["kb_id"]),
                )
            except FileAssetServiceError as exc:
                if exc.error_code != "file_asset_not_found":
                    raise
                return not asset_service.asset_cleanup_complete(asset_id)
        return False

    def _remove_document_references(
        self,
        metadata: dict[str, Any],
        doc_id: str,
    ) -> None:
        metadata["knowledge_write_proposals"] = {
            proposal_id: item
            for proposal_id, item in metadata["knowledge_write_proposals"].items()
            if not isinstance(item, dict) or str(item.get("document_id") or "") != doc_id
        }
        for job in metadata["pipeline_jobs"].values():
            if not isinstance(job, dict):
                continue
            job["sources"] = [
                item
                for item in job.get("sources", [])
                if not isinstance(item, dict) or str(item.get("source_id")) != doc_id
            ]
            removed_results = [
                item
                for item in job.get("document_results", [])
                if isinstance(item, dict) and str(item.get("source_id")) == doc_id
            ]
            job["document_results"] = [
                item
                for item in job.get("document_results", [])
                if not isinstance(item, dict) or str(item.get("source_id")) != doc_id
            ]
            if removed_results and not job["sources"] and job.get("status") in {
                "queued",
                "running",
                "failed",
                "cancelled",
            }:
                job["status"] = "cancelled"
                job["cancel_requested"] = True
                job["completed_at"] = time.time()
                job["error"] = "Source document deleted; this job cannot be retried."

        for version in metadata["pipeline_versions"].values():
            if not isinstance(version, dict):
                continue
            removed_results = [
                item
                for item in version.get("document_results", [])
                if isinstance(item, dict) and str(item.get("source_id")) == doc_id
            ]
            version["source_summary"] = [
                item
                for item in version.get("source_summary", [])
                if not isinstance(item, dict) or str(item.get("source_id")) != doc_id
            ]
            version["document_results"] = [
                item
                for item in version.get("document_results", [])
                if not isinstance(item, dict) or str(item.get("source_id")) != doc_id
            ]
            if removed_results:
                version["document_count"] = max(
                    0,
                    int(version.get("document_count", 0)) - len(removed_results),
                )
                version["chunk_count"] = max(
                    0,
                    int(version.get("chunk_count", 0))
                    - sum(int(item.get("chunk_count", 0)) for item in removed_results),
                )
                for field in (
                    "block_count",
                    "qa_count",
                    "summary_count",
                    "vision_page_count",
                    "vision_processed_page_count",
                    "vision_failed_page_count",
                    "vision_block_count",
                ):
                    version[field] = max(
                        0,
                        int(version.get(field, 0))
                        - sum(int(item.get(field, 0)) for item in removed_results),
                    )

    def _job_references_document(self, job: dict[str, Any], doc_id: str) -> bool:
        return any(
            isinstance(item, dict) and str(item.get("source_id")) == doc_id
            for item in job.get("sources", [])
        )

    def _is_managed_upload_path(self, path: Path) -> bool:
        if not str(path):
            return False
        try:
            resolved = path.resolve()
            root = self.uploads_dir.resolve()
        except OSError:
            return False
        return resolved != root and root in resolved.parents

    async def query(
        self,
        kb_id: str,
        question: str,
        *,
        top_k: int | None = 4,
        retrieval: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run retrieval and generate an answer from the retrieved context."""

        metadata = self._read_metadata()
        self._ensure_kb_exists(metadata, kb_id)
        if kb_id not in metadata["knowledge_bases"]:
            raise KnowledgeBaseNotFoundError("Knowledge base not found.")
        active_version_id = metadata["pipeline_active_versions"].get(kb_id)
        namespace = kb_id
        version: dict[str, Any] | None = None
        if active_version_id:
            stored_version = metadata["pipeline_versions"].get(active_version_id)
            if isinstance(stored_version, dict):
                version = stored_version
                namespace = str(version.get("namespace") or kb_id)
        config = self._retrieval_config_for_version(version, retrieval, top_k=top_k)
        result = await self._query_namespace(
            kb_id,
            namespace,
            question,
            config=config,
            lexical_ready=bool(version and version.get("lexical_index_ready")),
            embedding_profile=version.get("embedding_profile") if version else None,
        )
        return self._with_source_document_ids(
            result,
            str(version["version_id"]) if version else None,
        )

    async def _query_namespace(
        self,
        kb_id: str,
        namespace: str,
        question: str,
        *,
        config: RetrievalConfig,
        lexical_ready: bool,
        embedding_profile: dict[str, Any] | None = None,
        generate_answer: bool = True,
    ) -> dict[str, Any]:
        """Query one explicit index namespace while preserving public KB identity."""

        clean_question = question.strip()
        if not clean_question:
            raise ValueError("问题不能为空。")
        metadata = self._read_metadata()
        self._ensure_kb_exists(metadata, kb_id)
        if kb_id not in metadata["knowledge_bases"]:
            raise KnowledgeBaseNotFoundError("知识库不存在。")

        candidate_count = min(200, config.top_k * config.candidate_multiplier)
        warnings: list[str] = []
        fallback_reason_codes: list[str] = []
        vector_results: list[SearchResult] = []
        lexical_results: list[LexicalSearchResult] = []
        provider_route_receipts: dict[str, Any] | None = None
        execution_mode = "local_non_model"
        resolved_embedding_profile = self._resolved_embedding_profile_for_query(
            embedding_profile
        )

        async def query_vector_candidates() -> list[SearchResult]:
            nonlocal provider_route_receipts, execution_mode
            query_embedding, provider_route_receipts, execution_mode = await self._embed_query(
                clean_question,
                resolved_embedding_profile,
                version_reference=namespace,
            )
            return self.vector_store.query(namespace, query_embedding, candidate_count)

        if config.mode in {"vector", "hybrid"}:
            vector_results = await query_vector_candidates()
        if config.mode in {"fulltext", "hybrid"}:
            if lexical_ready or self.lexical_store.count_namespace(namespace) > 0:
                lexical_results = self.lexical_store.query(namespace, clean_question, candidate_count)
            else:
                warnings.append("Full-text index is unavailable for this legacy version; vector retrieval was used.")

        deleted_document_ids = self._deleted_document_ids()
        if deleted_document_ids:
            vector_results = [
                item
                for item in vector_results
                if not self._indexed_document_is_deleted(
                    str(item.doc_id), deleted_document_ids
                )
            ]
            lexical_results = [
                item
                for item in lexical_results
                if not self._indexed_document_is_deleted(
                    str(item.doc_id), deleted_document_ids
                )
            ]

        vector_candidates = [self._candidate_from_vector(item) for item in vector_results]
        lexical_candidates = [self._candidate_from_lexical(item) for item in lexical_results]
        effective_config = config
        if config.mode == "fulltext" and not lexical_candidates and not lexical_ready:
            effective_config = RetrievalConfig.from_mapping(
                {**config.payload(), "mode": "vector", "rerank_enabled": config.rerank_enabled}
            )
            if not vector_candidates:
                vector_results = await query_vector_candidates()
                vector_results = [
                    item
                    for item in vector_results
                    if not self._indexed_document_is_deleted(
                        str(item.doc_id), deleted_document_ids
                    )
                ]
                vector_candidates = [self._candidate_from_vector(item) for item in vector_results]
        fused = fuse_rankings(vector_candidates, lexical_candidates, effective_config)
        fused = [
            item for item in fused if item.fused_score >= config.score_threshold
        ]

        rerank_provider = "none"
        rerank_model = ""
        rerank_input_count = 0
        rerank_output_count = 0
        rerank_tail_dropped = 0
        rerank_details: dict[str, Any] = {}
        if config.rerank_enabled and fused:
            fused_before_rerank = list(fused)
            outcome = await self.reranker.rerank(
                clean_question,
                [RerankDocument(chunk_id=item.chunk_id, text=item.matched_text) for item in fused],
                provider=config.rerank_provider,
                model=config.rerank_model,
                top_n=min(config.rerank_top_n, len(fused)),
            )
            rerank_provider = outcome.provider
            rerank_model = outcome.model
            rerank_input_count = int(outcome.input_count or len(fused_before_rerank))
            rerank_details = {
                "rerank_requested_input_count": int(outcome.requested_input_count),
                "rerank_input_char_count": int(outcome.input_char_count),
                "rerank_candidate_limit": int(outcome.candidate_limit),
                "rerank_input_char_limit": int(outcome.input_char_limit),
                "rerank_timeout_budget_ms": int(outcome.timeout_budget_ms),
                "rerank_elapsed_ms": round(float(outcome.elapsed_ms), 3),
                "rerank_attempted_provider": str(outcome.attempted_provider or "none"),
                "rerank_attempted_model": str(outcome.attempted_model or ""),
                "rerank_fallback_reason": str(outcome.fallback_reason or "") or None,
                "rerank_provider_target_used": str(outcome.provider_target or "") or None,
                "rerank_attempted_targets": ";".join(outcome.attempted_targets) or None,
                "rerank_target_attempt_count": len(outcome.attempted_targets),
            }
            if outcome.warning:
                warnings.append(outcome.warning)
            by_id = {item.chunk_id: item for item in fused_before_rerank}
            reranked: list[RetrievalCandidate] = []
            for ranked in outcome.items[: config.rerank_top_n]:
                candidate = by_id.pop(ranked.chunk_id, None)
                if candidate is None:
                    continue
                candidate.rerank_score = ranked.score
                reranked.append(candidate)
            if outcome.provider != "none":
                # A successful provider response is authoritative for Top-N.
                # Restoring the unranked tail makes rerank_top_n ineffective and
                # systematically lowers fixed-cutoff citation precision.
                fused = reranked
                rerank_output_count = len(reranked)
                rerank_tail_dropped = max(
                    0, len(fused_before_rerank) - rerank_output_count
                )
            else:
                # Provider failure is explicitly fail-open: preserve the full
                # fused ranking and surface the warning/receipt to the caller.
                fused = fused_before_rerank
                rerank_output_count = len(fused_before_rerank)

        deleted_document_ids = self._deleted_document_ids()
        results = select_candidates(
            [
                item
                for item in fused
                if not self._indexed_document_is_deleted(
                    str(item.doc_id), deleted_document_ids
                )
            ],
            score_threshold=config.score_threshold,
            top_k=config.top_k,
        )
        if not results:
            return {
                "answer": "没有在该知识库中找到相关内容，请尝试换一种问法或上传更多资料。",
                "sources": [],
                "warnings": warnings,
                "fallback_reason_codes": fallback_reason_codes,
                "execution_mode": execution_mode,
                "provider_route_receipts": provider_route_receipts,
                "retrieval": self._retrieval_diagnostics(
                    config,
                    vector_count=len(vector_results),
                    fulltext_count=len(lexical_results),
                    rerank_provider=rerank_provider,
                    rerank_model=rerank_model,
                    rerank_input_count=rerank_input_count,
                    rerank_output_count=rerank_output_count,
                    rerank_tail_dropped=rerank_tail_dropped,
                    rerank_details=rerank_details,
                    embedding_profile=resolved_embedding_profile,
                ),
            }

        if generate_answer:
            (
                answer,
                generation_receipt,
                generation_mode,
                fallback_reason_codes,
            ) = await self._generate_answer_with_control(
                kb_id,
                namespace,
                clean_question,
                results,
            )
            provider_route_receipts = self._merge_provider_route_receipts(
                provider_route_receipts,
                generation_receipt,
            )
            if generation_mode is not None:
                execution_mode = generation_mode
        else:
            answer = ""
        return {
            "answer": answer,
            "sources": [
                {
                    "chunk_id": result.chunk_id,
                    "doc_id": result.doc_id,
                    "document_name": result.document_name,
                    "text": result.context_text,
                    "matched_text": result.matched_text,
                    "score": round(result.score, 4),
                    "vector_score": _rounded_optional(result.vector_score),
                    "fulltext_score": _rounded_optional(result.fulltext_score),
                    "fused_score": round(result.fused_score, 4),
                    "rerank_score": _rounded_optional(result.rerank_score),
                    "parent_chunk_id": result.parent_chunk_id,
                    "parent_lifted": bool(result.parent_chunk_id),
                    "chunk_type": result.chunk_type,
                    "start_char": result.start_char,
                    "end_char": result.end_char,
                    "page_number": result.page_number,
                    "slide": result.slide,
                    "heading_path": list(result.heading_path),
                    "sheet": result.sheet,
                    "row_range": result.row_range,
                    "visual_kind": result.visual_kind,
                    "source_block_id": result.source_block_id,
                }
                for result in results
            ],
            "warnings": warnings,
            "fallback_reason_codes": fallback_reason_codes,
            "execution_mode": execution_mode,
            "provider_route_receipts": provider_route_receipts,
            "retrieval": self._retrieval_diagnostics(
                config,
                vector_count=len(vector_results),
                fulltext_count=len(lexical_results),
                rerank_provider=rerank_provider,
                rerank_model=rerank_model,
                rerank_input_count=rerank_input_count,
                rerank_output_count=rerank_output_count,
                rerank_tail_dropped=rerank_tail_dropped,
                rerank_details=rerank_details,
                embedding_profile=resolved_embedding_profile,
            ),
        }

    async def _generate_answer_with_control(
        self,
        kb_id: str,
        namespace: str,
        question: str,
        results: list[RetrievalCandidate],
    ) -> tuple[str, dict[str, Any] | None, str | None, list[str]]:
        gateway = self._managed_generation_gateway()
        if gateway is None or str(
            gateway.routing_mode("rag_query_generate")
        ) == "legacy":
            return await self._generate_answer(question, results), None, None, []
        if str(gateway.routing_mode("rag_query_generate")) != "managed_required":
            code = "provider_workload_policy_not_active"
            raise ManagedRagGenerationRouteError(
                code,
                "RAG Query 的 Managed Provider 策略已退化并失败关闭。",
                receipt=gateway.blocked_receipt("rag_query_generate", code),
            )

        run: Any | None = None
        try:
            model_id = gateway.exact_model_id(
                "rag_query_generate",
                "chat_text_unary",
            )
            run = gateway.start_run(
                "rag_query_generate",
                parent_run_reference=(
                    f"rag_query:{kb_id}:{namespace[:120]}:{uuid.uuid4().hex}"
                ),
                stable=False,
            )
            messages, temperature, max_tokens = await self._answer_request(
                question,
                results,
            )
            answer = await run.complete_text_unary(
                logical_call_key="rag_query_answer:0",
                call_sequence=1,
                model_id=model_id,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return answer.strip(), run.finish_success(), "managed", []
        except asyncio.CancelledError:
            if run is not None:
                run.finish_cancelled()
            raise
        except Exception as exc:
            code = str(getattr(exc, "code", "rag_query_generation_failed"))
            receipt = getattr(exc, "receipt", None)
            if run is not None:
                receipt = run.finish_failure(code)
            if not isinstance(receipt, dict):
                receipt = gateway.blocked_receipt("rag_query_generate", code)
            if gateway.local_fallback_mode("rag_query_generate") == "extractive":
                return (
                    self._extractive_answer(results),
                    receipt,
                    "local_non_model",
                    [code, "local_non_model_fallback"],
                )
            raise ManagedRagGenerationRouteError(
                code,
                "RAG Query 的 Managed Provider 调用失败，系统未重试或切换目标。",
                receipt=receipt,
            ) from exc

    @staticmethod
    def _merge_provider_route_receipts(
        first: dict[str, Any] | None,
        second: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        receipts = [item for item in (first, second) if isinstance(item, dict)]
        if not receipts:
            return None
        if len(receipts) == 1:
            return receipts[0]
        calls = [
            dict(call)
            for receipt in receipts
            for call in receipt.get("calls", [])
            if isinstance(call, dict)
        ]
        reason_codes = list(
            dict.fromkeys(
                str(code)
                for receipt in receipts
                for code in receipt.get("reason_codes", [])
                if str(code)
            )
        )
        statuses = {str(receipt.get("status") or "") for receipt in receipts}
        status = (
            "uncertain"
            if "uncertain" in statuses
            else "failed"
            if "failed" in statuses
            else "passed"
        )
        return {
            "contract_version": "modelmirror-provider-rag-route-receipts-v1",
            "routing_mode": "composed",
            "status": status,
            "call_count": sum(
                max(0, int(receipt.get("call_count") or 0)) for receipt in receipts
            ),
            "reason_codes": reason_codes,
            "calls": calls,
            "components": receipts,
        }

    async def _generate_answer(
        self,
        question: str,
        results: list[RetrievalCandidate],
    ) -> str:
        api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        if not self.llm_enabled or not api_key:
            return self._extractive_answer(results)

        messages, temperature, max_tokens = await self._answer_request(
            question,
            results,
        )
        payload = {
            "model": os.getenv("RAG_LLM_MODEL", os.getenv("OPENROUTER_TEXT_FALLBACK_MODEL", "deepseek/deepseek-chat")),
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": os.getenv("OPENROUTER_HTTP_REFERER", "http://localhost:5173"),
            "X-Title": os.getenv("OPENROUTER_APP_TITLE", "ModelMirror"),
        }
        proxy = os.getenv("OPENROUTER_PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
        client_kwargs: dict[str, Any] = {"timeout": httpx.Timeout(45.0, connect=15.0)}
        if proxy:
            client_kwargs["proxy"] = proxy

        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                response = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except Exception:
            return self._extractive_answer(results)

        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message") if isinstance(choices[0], dict) else None
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, str) and content.strip():
                return content.strip()
        return self._extractive_answer(results)

    async def _answer_request(
        self,
        question: str,
        results: list[RetrievalCandidate],
    ) -> tuple[list[dict[str, str]], float, int]:
        context = "\n\n".join(
            f"[来源：{result.document_name}]\n{result.context_text}" for result in results
        )
        configured_profile = (
            os.getenv("RAG_CONTEXT_COMPRESSION_MODE", "auto").strip().lower()
        )
        context_optimization = await optimize_context(
            [
                {
                    "role": "assistant",
                    "content": context,
                    "metadata": {"kind": "rag_context"},
                }
            ],
            profile=(
                configured_profile
                if configured_profile in {"auto", "off", "standard", "strong"}
                else "auto"
            ),
            max_context_tokens=_safe_env_int(
                "RAG_CONTEXT_MAX_TOKENS", 32_000, minimum=2_048
            ),
            max_output_tokens=_safe_env_int(
                "RAG_MAX_TOKENS", 1_200
            ),
        )
        context = str(
            context_optimization.messages[0].get("content") or context
        )
        prompt = (
            "请仅依据<context>中的资料回答用户问题。如果资料不足，请明确说明不知道。"
            "回答后用一句话概括引用来源。\n\n"
            f"<context>\n{context}\n</context>\n\n"
            f"用户问题：{question}"
        )
        return (
            [
                {"role": "system", "content": "你是模镜的知识库问答助手，严谨、简洁，只基于给定资料回答。"},
                {"role": "user", "content": prompt},
            ],
            float(os.getenv("RAG_TEMPERATURE", "0.2")),
            int(os.getenv("RAG_MAX_TOKENS", "1200")),
        )

    def _extractive_answer(self, results: list[RetrievalCandidate]) -> str:
        best = results[0]
        return f"根据知识库资料：{best.context_text}"

    def retrieval_capabilities(self) -> dict[str, Any]:
        rerank = self.reranker.capabilities()
        return {
            "version": "rag-retrieval-capabilities-v2",
            "index_schema_version": 2,
            "vector": {
                "available": True,
                "backend": self.vector_store.__class__.__name__,
            },
            "fulltext": {
                "available": True,
                "backend": self.lexical_store.backend,
            },
            "embedding": self._default_embedding_profile(),
            "rerank": rerank,
            "modes": ["vector", "fulltext", "hybrid"],
        }

    def _retrieval_config_for_version(
        self,
        version: dict[str, Any] | None,
        override: dict[str, Any] | None,
        *,
        top_k: int | None,
    ) -> RetrievalConfig:
        if version and int(version.get("index_schema_version", 1)) >= 2:
            base = RetrievalConfig.from_mapping(version.get("retrieval_profile"))
        else:
            base = RetrievalConfig.from_mapping(
                {
                    "mode": "vector",
                    "top_k": 4,
                    "rerank_enabled": False,
                    "rerank_provider": "none",
                }
            )
        merged = dict(override or {})
        if top_k is not None:
            merged["top_k"] = top_k
        return RetrievalConfig.from_mapping(merged, base=base)

    def _candidate_from_vector(self, item: SearchResult) -> RetrievalCandidate:
        return RetrievalCandidate(
            chunk_id=item.chunk_id,
            doc_id=item.doc_id,
            document_name=item.document_name,
            matched_text=item.text,
            context_text=item.parent_text or item.text,
            parent_chunk_id=item.parent_chunk_id,
            chunk_type=item.chunk_type,
            start_char=item.start_char,
            end_char=item.end_char,
            page_number=item.page_number,
            slide=item.slide,
            heading_path=normalize_heading_path(item.heading_path),
            sheet=item.sheet,
            row_range=item.row_range,
            visual_kind=item.visual_kind,
            source_block_id=item.source_block_id,
            vector_score=item.score,
        )

    def _candidate_from_lexical(self, item: LexicalSearchResult) -> RetrievalCandidate:
        return RetrievalCandidate(
            chunk_id=item.chunk_id,
            doc_id=item.doc_id,
            document_name=item.document_name,
            matched_text=item.text,
            context_text=item.parent_text or item.text,
            parent_chunk_id=item.parent_chunk_id,
            chunk_type=item.chunk_type,
            start_char=item.start_char,
            end_char=item.end_char,
            page_number=item.page_number,
            slide=item.slide,
            heading_path=normalize_heading_path(item.heading_path),
            sheet=item.sheet,
            row_range=item.row_range,
            visual_kind=item.visual_kind,
            source_block_id=item.source_block_id,
            fulltext_score=item.score,
        )

    def _retrieval_diagnostics(
        self,
        config: RetrievalConfig,
        *,
        vector_count: int,
        fulltext_count: int,
        rerank_provider: str,
        rerank_model: str,
        rerank_input_count: int,
        rerank_output_count: int,
        rerank_tail_dropped: int,
        rerank_details: dict[str, Any],
        embedding_profile: dict[str, Any],
    ) -> dict[str, Any]:
        effective = dict(embedding_profile.get("effective") or {})
        return {
            **config.payload(),
            "vector_candidate_count": vector_count,
            "fulltext_candidate_count": fulltext_count,
            "rerank_provider_used": rerank_provider,
            "rerank_model_used": rerank_model,
            "rerank_applied": rerank_provider != "none",
            "rerank_input_count": max(0, int(rerank_input_count)),
            "rerank_output_count": max(0, int(rerank_output_count)),
            "rerank_tail_dropped": max(0, int(rerank_tail_dropped)),
            **rerank_details,
            "threshold_score_domain": "fused_score",
            "embedding_provider": str(effective.get("provider") or ""),
            "embedding_model": str(effective.get("model") or ""),
            "embedding_dimension": int(effective.get("dimension") or 0),
            "embedding_space_fingerprint": str(
                embedding_profile.get("embedding_space_fingerprint") or ""
            ),
        }

    def _resolved_embedding_profile_for_query(
        self,
        profile: dict[str, Any] | None,
    ) -> dict[str, Any]:
        profile_is_stored = isinstance(profile, dict)
        stored_profile = (
            profile if isinstance(profile, dict) else self._default_embedding_profile()
        )
        stored_effective = stored_profile.get("effective")
        stored_access_mode = (
            str(stored_effective.get("access_mode") or "")
            if isinstance(stored_effective, dict)
            else ""
        )
        managed_gateway = self._managed_embedding_gateway()
        managed_mode = (
            str(managed_gateway.routing_mode())
            if managed_gateway is not None
            else "legacy"
        )
        requested = self._requested_embedding_profile(stored_profile)
        stored_fingerprint = str(
            stored_profile.get("embedding_space_fingerprint") or ""
        )
        if (
            managed_mode == "managed_required"
            and (
                not profile_is_stored
                or requested["provider"] != EMBEDDING_PROVIDER_HASH
            )
            and (stored_access_mode != "managed" or not stored_fingerprint)
        ):
            blocked = self._validated_embedding_profile(stored_profile, None)
            blocked_effective = dict(blocked.get("effective") or {})
            blocked_effective.update(
                {
                    "ready": False,
                    "reason": "provider_embedding_index_rebuild_required",
                    "access_mode": "managed",
                }
            )
            blocked["effective"] = blocked_effective
            blocked["ready"] = False
            blocked["reason"] = "provider_embedding_index_rebuild_required"
            blocked["embedding_space_fingerprint"] = ""
            return blocked
        if (
            isinstance(stored_effective, dict)
            and stored_access_mode == "managed"
        ):
            return json.loads(json.dumps(stored_profile))
        resolved = self._validated_embedding_profile(stored_profile, None)
        stored_effective = stored_profile.get("effective")
        if not isinstance(stored_effective, dict):
            stored_effective = stored_profile
        resolved_effective = dict(resolved.get("effective") or {})
        stored_dimension = int(stored_effective.get("dimension") or 0)
        if (
            bool(resolved_effective.get("ready"))
            and stored_dimension > 0
            and str(stored_effective.get("provider") or "")
            == str(resolved_effective.get("provider") or "")
            and str(stored_effective.get("model") or "")
            == str(resolved_effective.get("model") or "")
        ):
            resolved_effective["dimension"] = stored_dimension
            resolved["dimension"] = stored_dimension
            resolved["effective"] = resolved_effective
        return resolved

    async def _embed_query(
        self,
        text: str,
        profile: dict[str, Any],
        *,
        version_reference: str,
    ) -> tuple[list[float], dict[str, Any] | None, str]:
        effective = dict(profile.get("effective") or {})
        if str(effective.get("reason") or "") == (
            "provider_embedding_index_rebuild_required"
        ):
            raise ManagedEmbeddingRouteError(
                "provider_embedding_index_rebuild_required",
                "Managed Embedding requires an explicit index rebuild before use.",
            )
        self._ensure_embedding_profile_ready(profile)
        provider = str(effective.get("provider") or "")
        model = str(effective.get("model") or "")
        dimension = int(effective.get("dimension") or 0)
        if dimension <= 0:
            raise EmbeddingError("Embedding profile has no valid vector dimension.")

        access_mode = str(effective.get("access_mode") or "legacy")
        if access_mode == "managed":
            gateway = self._managed_embedding_gateway()
            if gateway is None or str(gateway.routing_mode()) != "managed_required":
                raise PipelineDraftValidationError(
                    "Managed Embedding policy is not active for this index version."
                )
            run = gateway.start_query_run(version_reference)
            try:
                result = await run.embed(
                    [text],
                    model_id=model,
                    logical_call_key="embedding_query:0",
                    call_sequence=1,
                    expected_space_fingerprint=str(
                        profile.get("embedding_space_fingerprint") or ""
                    ),
                )
                receipt = run.finish_success()
            except Exception as exc:
                code = str(getattr(exc, "code", "provider_embedding_failed"))
                raise ManagedEmbeddingRouteError(
                    code,
                    f"Managed Embedding query failed: {code}",
                ) from exc
            if len(result.vectors) != 1:
                raise EmbeddingError(
                    "Managed Embedding query returned an invalid vector count."
                )
            return result.vectors[0], receipt, "managed"

        if provider == EMBEDDING_PROVIDER_HASH:
            embedder = EmbeddingClient(
                api_base="",
                api_key="",
                model=HASH_EMBEDDING_MODEL,
                dimension=dimension,
            )
            embedder.api_key = ""
            embedder.embedding_mode = "hash"
        else:
            if model == self.embedder.model:
                embedder = self.embedder
            else:
                embedder = EmbeddingClient(
                    api_base=self.embedder.api_base,
                    api_key=self.embedder.api_key,
                    model=model,
                    dimension=dimension,
                )
        vectors = await embedder.embed_texts([text])
        if len(vectors) != 1 or len(vectors[0]) != dimension:
            actual = len(vectors[0]) if vectors else 0
            raise EmbeddingError(
                "Embedding query dimension mismatch: "
                f"expected {dimension}, received {actual}."
            )
        return (
            vectors[0],
            None,
            "local_non_model"
            if provider == EMBEDDING_PROVIDER_HASH
            else "legacy",
        )

    def _default_embedding_profile(self) -> dict[str, Any]:
        use_hash = self.embedder.embedding_mode == "hash" or not self.embedder.api_key
        return self._embedding_profile_from_request(
            provider=(
                EMBEDDING_PROVIDER_HASH
                if use_hash
                else EMBEDDING_PROVIDER_OPENAI_COMPATIBLE
            ),
            model=HASH_EMBEDDING_MODEL if use_hash else self.embedder.model,
        )

    def _validated_embedding_profile(
        self,
        current: dict[str, Any] | None,
        patch: dict[str, Any] | None,
    ) -> dict[str, Any]:
        requested = self._requested_embedding_profile(current)
        if patch:
            unknown = set(patch) - {"model", "provider"}
            if unknown:
                raise PipelineDraftValidationError(
                    f"Unsupported embedding profile field: {sorted(unknown)[0]}"
                )
            provider_supplied = "provider" in patch and bool(
                str(patch.get("provider") or "").strip()
            )
            model_supplied = "model" in patch and bool(
                str(patch.get("model") or "").strip()
            )
            provider = str(
                patch.get("provider") if provider_supplied else requested["provider"]
            ).strip()
            model = str(
                patch.get("model") if model_supplied else requested["model"]
            ).strip()
            if provider not in {
                EMBEDDING_PROVIDER_HASH,
                EMBEDDING_PROVIDER_OPENAI_COMPATIBLE,
            }:
                raise PipelineDraftValidationError(
                    "embedding_profile.provider must be hash or openai_compatible."
                )
            if (
                model_supplied
                and not provider_supplied
                and provider == EMBEDDING_PROVIDER_HASH
                and model not in HASH_EMBEDDING_MODEL_ALIASES
            ):
                provider = EMBEDDING_PROVIDER_OPENAI_COMPATIBLE
            if provider == EMBEDDING_PROVIDER_HASH:
                if model_supplied and model not in HASH_EMBEDDING_MODEL_ALIASES:
                    raise PipelineDraftValidationError(
                        "hash embedding does not accept a semantic model label; use "
                        "provider=openai_compatible for that model."
                    )
                model = HASH_EMBEDDING_MODEL
            elif not model or model in HASH_EMBEDDING_MODEL_ALIASES or len(model) > 200:
                raise PipelineDraftValidationError("embedding_profile.model is invalid.")
            requested = {"provider": provider, "model": model}
        return self._embedding_profile_from_request(
            provider=str(requested["provider"]),
            model=str(requested["model"]),
        )

    def _requested_embedding_profile(
        self,
        current: dict[str, Any] | None,
    ) -> dict[str, str]:
        profile = dict(current or {})
        nested = profile.get("requested")
        if isinstance(nested, dict):
            provider = str(nested.get("provider") or "").strip()
            model = str(nested.get("model") or "").strip()
        else:
            defaults = self._default_embedding_profile()["requested"]
            provider = str(profile.get("provider") or defaults["provider"]).strip()
            model = str(profile.get("model") or defaults["model"]).strip()
            if provider == EMBEDDING_PROVIDER_UNAVAILABLE:
                provider = EMBEDDING_PROVIDER_OPENAI_COMPATIBLE
            if (
                provider == EMBEDDING_PROVIDER_HASH
                and model not in {"", *HASH_EMBEDDING_MODEL_ALIASES}
            ):
                # Legacy profiles retained the requested semantic model label after
                # silently downgrading the effective provider to hash.
                provider = EMBEDDING_PROVIDER_OPENAI_COMPATIBLE

        if provider not in {
            EMBEDDING_PROVIDER_HASH,
            EMBEDDING_PROVIDER_OPENAI_COMPATIBLE,
        }:
            defaults = self._default_embedding_profile()["requested"]
            return {
                "provider": str(defaults["provider"]),
                "model": str(defaults["model"]),
            }
        if provider == EMBEDDING_PROVIDER_HASH:
            model = HASH_EMBEDDING_MODEL
        elif not model or model in HASH_EMBEDDING_MODEL_ALIASES:
            model = self.embedder.model
        return {"provider": provider, "model": model[:200]}

    def _embedding_profile_from_request(
        self,
        *,
        provider: str,
        model: str,
    ) -> dict[str, Any]:
        requested = {"provider": provider, "model": model}
        identity: dict[str, Any] | None = None
        if provider == EMBEDDING_PROVIDER_HASH:
            identity = self._embedding_space_identity(
                provider_kind=EMBEDDING_PROVIDER_HASH,
                endpoint="local://deterministic-hash-v1",
                model_id=HASH_EMBEDDING_MODEL,
                vector_dimension=self.embedder.dimension,
            )
            effective = {
                "provider": EMBEDDING_PROVIDER_HASH,
                "model": HASH_EMBEDDING_MODEL,
                "dimension": self.embedder.dimension,
                "degraded": True,
                "ready": True,
                "reason": None,
            }
        else:
            reason: str | None = None
            managed_gateway = self._managed_embedding_gateway()
            managed_mode = (
                str(managed_gateway.routing_mode())
                if managed_gateway is not None
                else "legacy"
            )
            managed_identity: dict[str, Any] | None = None
            if managed_mode == "managed_required":
                try:
                    managed_identity = dict(
                        managed_gateway.qualification(model).payload()
                    )
                except Exception as exc:
                    reason = str(
                        getattr(exc, "code", "provider_embedding_qualification_unavailable")
                    )
            elif managed_mode == "degraded_required":
                reason = "provider_workload_policy_not_active"
            elif not self.embedder.api_key:
                reason = "embedding_credentials_missing"
            elif self.embedder.embedding_mode == "hash":
                reason = "embedding_hash_mode_forced"
            if reason or (managed_mode == "managed_required" and managed_identity is None):
                effective = {
                    "provider": EMBEDDING_PROVIDER_UNAVAILABLE,
                    "model": "",
                    "dimension": 0,
                    "degraded": False,
                    "ready": False,
                    "reason": reason or "provider_embedding_qualification_unavailable",
                    "access_mode": (
                        "managed" if managed_mode != "legacy" else "legacy"
                    ),
                }
            elif managed_identity is not None:
                effective = {
                    "provider": EMBEDDING_PROVIDER_OPENAI_COMPATIBLE,
                    "model": model,
                    "dimension": int(managed_identity["vector_dimension"]),
                    "degraded": False,
                    "ready": True,
                    "reason": None,
                    "access_mode": "managed",
                }
                identity = managed_identity
            else:
                base = self.embedder.api_base or "https://api.openai.com/v1"
                identity = self._embedding_space_identity(
                    provider_kind=EMBEDDING_PROVIDER_OPENAI_COMPATIBLE,
                    endpoint=f"{base.rstrip('/')}/embeddings",
                    model_id=model,
                    vector_dimension=self.embedder.dimension,
                )
                effective = {
                    "provider": EMBEDDING_PROVIDER_OPENAI_COMPATIBLE,
                    "model": model,
                    "dimension": self.embedder.dimension,
                    "degraded": False,
                    "ready": True,
                    "reason": None,
                }
        return {
            "provider": effective["provider"],
            "model": effective["model"],
            "dimension": effective["dimension"],
            "degraded": effective["degraded"],
            "ready": effective["ready"],
            "reason": effective["reason"],
            "requested": requested,
            "effective": effective,
            "embedding_space_fingerprint": str(
                (identity or {}).get("fingerprint") or ""
            ),
        }

    def _managed_generation_gateway(self) -> Any | None:
        if self.managed_generation_gateway is not None:
            return self.managed_generation_gateway
        enabled_values = (
            os.getenv("MODEL_CONTROL_RAG_QUERY_ENABLED", ""),
            os.getenv("MODEL_CONTROL_RAG_PROCESSOR_ENABLED", ""),
        )
        if not any(
            value.strip().casefold() not in {"", "0", "false", "no", "off"}
            for value in enabled_values
        ):
            return None
        try:
            try:
                from server.model_router import get_model_router_service
                from server.model_router.rag_generation_gateway import (
                    ManagedRagGenerationGateway,
                )
            except ModuleNotFoundError:
                from model_router import get_model_router_service
                from model_router.rag_generation_gateway import (
                    ManagedRagGenerationGateway,
                )

            self.managed_generation_gateway = ManagedRagGenerationGateway.for_router(
                get_model_router_service()
            )
        except Exception as exc:
            raise PipelineDraftValidationError(
                "Managed RAG generation control plane is unavailable."
            ) from exc
        return self.managed_generation_gateway

    async def _generate_processor_items(
        self,
        document: ProcessedDocument,
        *,
        mode: str,
        model_id: str,
        max_items: int,
        parent_run_reference: str,
        stable: bool,
    ) -> ProcessorGenerationOutcome:
        if mode not in {"qa", "summary"}:
            return ProcessorGenerationOutcome(
                await self.processor_generator.generate(
                    document,
                    mode=mode,
                    model_id=model_id,
                    max_items=max_items,
                ),
                "legacy",
                None,
            )
        gateway = self._managed_generation_gateway()
        if gateway is None or str(
            gateway.routing_mode("rag_processor_generate")
        ) == "legacy":
            return ProcessorGenerationOutcome(
                await self.processor_generator.generate(
                    document,
                    mode=mode,
                    model_id=model_id,
                    max_items=max_items,
                ),
                "legacy",
                None,
            )
        if str(gateway.routing_mode("rag_processor_generate")) != "managed_required":
            code = "provider_workload_policy_not_active"
            raise ProcessorGenerationError(
                "Managed RAG Processor policy is degraded and fails closed.",
                code=code,
                receipt=gateway.blocked_receipt("rag_processor_generate", code),
            )
        try:
            clean_model_id = str(model_id or "").strip()
            if not clean_model_id:
                code = "provider_workload_model_required"
                raise ProcessorGenerationError(
                    "Managed RAG Processor requires an exact Draft model ID.",
                    code=code,
                    receipt=gateway.blocked_receipt(
                        "rag_processor_generate",
                        code,
                    ),
                )
            exact_model = gateway.exact_model_id(
                "rag_processor_generate",
                "chat_json_object",
                requested_model=clean_model_id,
            )
            run = gateway.start_run(
                "rag_processor_generate",
                parent_run_reference=parent_run_reference,
                stable=stable,
            )
            return await self.processor_generator.generate_managed(
                document,
                mode=mode,
                model_id=exact_model,
                max_items=max_items,
                managed_run=run,
            )
        except ProcessorGenerationError:
            raise
        except Exception as exc:
            code = str(getattr(exc, "code", "rag_processor_generation_failed"))
            receipt = getattr(exc, "receipt", None)
            raise ProcessorGenerationError(
                "Managed RAG Processor failed before dispatch.",
                code=code,
                receipt=(
                    receipt
                    if isinstance(receipt, dict)
                    else gateway.blocked_receipt("rag_processor_generate", code)
                ),
            ) from exc

    def _managed_embedding_gateway(self) -> Any | None:
        if self.managed_embedding_gateway is not None:
            return self.managed_embedding_gateway
        if os.getenv("MODEL_CONTROL_RAG_EMBEDDING_ENABLED", "").strip().casefold() in {
            "",
            "0",
            "false",
            "no",
            "off",
        }:
            return None
        try:
            try:
                from server.model_router import get_model_router_service
                from server.model_router.rag_embedding_gateway import (
                    ManagedRagEmbeddingGateway,
                )
            except ModuleNotFoundError:
                from model_router import get_model_router_service
                from model_router.rag_embedding_gateway import (
                    ManagedRagEmbeddingGateway,
                )

            self.managed_embedding_gateway = ManagedRagEmbeddingGateway.for_router(
                get_model_router_service()
            )
        except Exception as exc:
            raise PipelineDraftValidationError(
                "Managed Embedding control plane is unavailable."
            ) from exc
        return self.managed_embedding_gateway

    @staticmethod
    def _embedding_space_identity(
        *,
        provider_kind: str,
        endpoint: str,
        model_id: str,
        vector_dimension: int,
    ) -> dict[str, Any]:
        endpoint_digest = hashlib.sha256(endpoint.encode("utf-8")).hexdigest()
        material = {
            "contract_version": EMBEDDING_SPACE_CONTRACT_VERSION,
            "provider_kind": provider_kind,
            "endpoint_identity_sha256": endpoint_digest,
            "model_id": model_id,
            "vector_dimension": int(vector_dimension),
            "provider_operation_contract_version": "modelmirror-provider-operation-v1",
        }
        return {
            **material,
            "fingerprint": hashlib.sha256(
                json.dumps(material, sort_keys=True, separators=(",", ":")).encode(
                    "utf-8"
                )
            ).hexdigest(),
        }

    @staticmethod
    def _embedding_job_metadata(profile: Any) -> dict[str, Any]:
        effective = (
            dict(profile.get("effective") or {})
            if isinstance(profile, dict)
            else {}
        )
        access_mode = str(effective.get("access_mode") or "")
        if not access_mode:
            access_mode = (
                "local_hash"
                if str(effective.get("provider") or "") == EMBEDDING_PROVIDER_HASH
                else "legacy"
            )
        execution_mode = (
            "managed"
            if access_mode == "managed"
            else "local_non_model"
            if access_mode == "local_hash"
            else "legacy"
        )
        return {
            "embedding_execution_mode": execution_mode,
            "embedding_space_fingerprint": str(
                profile.get("embedding_space_fingerprint")
                if isinstance(profile, dict)
                else ""
            ) or "",
            "provider_route_receipts": None,
        }

    @staticmethod
    def _ensure_embedding_profile_ready(profile: dict[str, Any]) -> None:
        effective = profile.get("effective")
        if isinstance(effective, dict) and bool(effective.get("ready")):
            return
        requested = profile.get("requested")
        requested_model = (
            str(requested.get("model") or "")
            if isinstance(requested, dict)
            else str(profile.get("model") or "")
        )
        raise PipelineDraftValidationError(
            "Embedding provider is unavailable for the requested model "
            f"{requested_model[:200] or '(unset)'}; configure EMBEDDING_API_KEY "
            "before creating a pipeline job."
        )

    def _default_pipeline_draft_stages(self) -> dict[str, dict[str, Any]]:
        return {
            "stage_data_source": {
                "source_mode": "uploaded_files",
                "allowed_extensions": sorted({*supported_extensions(), *SUPPORTED_IMAGE_EXTENSIONS}),
            },
            "stage_processor": {
                "parser": "structured_local_parser",
                "mode": "general",
                "model_id": self.processor_generator.default_model(),
                "failure_policy": "continue_on_error",
                "extract_title": True,
                "preserve_tables": True,
                "preserve_code_blocks": True,
                "remove_repeated_headers_footers": True,
                "max_generated_items": 20,
            },
            "stage_chunker": {
                "strategy": "recursive_character",
                "chunk_size": self.splitter.chunk_size,
                "chunk_overlap": self.splitter.chunk_overlap,
                "separators": list(DEFAULT_SEPARATORS),
                "parent_chunk_size": 1500,
                "parent_chunk_overlap": 100,
                "child_chunk_size": 400,
                "child_chunk_overlap": 50,
                "parent_separators": list(DEFAULT_SEPARATORS),
                "child_separators": list(DEFAULT_SEPARATORS),
            },
            "stage_image_understanding": {
                "enabled": False,
                "provider": "openai_compatible_vlm",
                "vision_model_id": "",
                "pdf_page_strategy": "auto",
                "render_dpi": 144,
                "max_pages": 100,
                "max_image_edge": 2048,
                "failure_policy": "continue_on_error",
            },
        }

    def _pipeline_draft_record(
        self,
        metadata: dict[str, Any],
        kb_id: str,
    ) -> dict[str, Any]:
        defaults = self._default_pipeline_draft_stages()
        draft = metadata["pipeline_drafts"].get(kb_id)
        if not isinstance(draft, dict):
            return {
                "draft_id": f"draft_{kb_id}",
                "version": 1,
                "updated_at": metadata["knowledge_bases"][kb_id]["updated_at"],
                "index_schema_version": 2,
                "embedding_profile": self._default_embedding_profile(),
                "retrieval_profile": RetrievalConfig().payload(),
                "stages": defaults,
            }

        stages = {
            stage_id: dict(config)
            for stage_id, config in defaults.items()
        }
        raw_stages = draft.get("stages")
        if isinstance(raw_stages, dict):
            for raw_stage_id, raw_config in raw_stages.items():
                stage_id = self._normalize_pipeline_stage_id(str(raw_stage_id))
                if stage_id is None or not isinstance(raw_config, dict):
                    continue
                try:
                    stages[stage_id] = self._validated_pipeline_stage_config(
                        stage_id,
                        stages[stage_id],
                        raw_config,
                    )
                except PipelineDraftValidationError:
                    continue

        return {
            "draft_id": str(draft.get("draft_id") or f"draft_{kb_id}"),
            "version": int(draft.get("version") or 1),
            "updated_at": float(draft.get("updated_at") or metadata["knowledge_bases"][kb_id]["updated_at"]),
            "index_schema_version": 2,
            "embedding_profile": self._validated_embedding_profile(
                draft.get("embedding_profile") if isinstance(draft.get("embedding_profile"), dict) else None,
                None,
            ),
            "retrieval_profile": RetrievalConfig.from_mapping(
                draft.get("retrieval_profile") if isinstance(draft.get("retrieval_profile"), dict) else None
            ).payload(),
            "stages": stages,
        }

    def _pipeline_graph_record(
        self,
        metadata: dict[str, Any],
        kb_id: str,
        draft: dict[str, Any],
    ) -> dict[str, Any]:
        record = metadata["pipeline_graphs"].get(kb_id)
        if not isinstance(record, dict) or not isinstance(record.get("graph"), dict):
            return {
                "graph_id": f"kpgraph_{kb_id}",
                "graph_revision": 1,
                "compiled_draft_version": int(draft["version"]),
                "updated_at": float(draft["updated_at"]),
                "graph": default_pipeline_graph(kb_id, draft),
            }
        graph = json.loads(json.dumps(record["graph"]))
        graph["kb_id"] = kb_id
        return {
            "graph_id": str(record.get("graph_id") or f"kpgraph_{kb_id}"),
            "graph_revision": max(1, int(record.get("graph_revision") or 1)),
            "compiled_draft_version": int(
                record.get("compiled_draft_version") or draft["version"]
            ),
            "updated_at": float(record.get("updated_at") or draft["updated_at"]),
            "graph": graph,
        }

    def _validate_and_compile_pipeline_graph(
        self,
        kb_id: str,
        graph: dict[str, Any],
        draft: dict[str, Any],
    ) -> tuple[list[GraphValidationIssue], KnowledgePipelineCompileResult | None]:
        issues = validate_pipeline_graph(graph)
        if issues:
            return issues, None
        try:
            compiled = compile_pipeline_graph(graph)
            stages: dict[str, dict[str, Any]] = {}
            for stage_id, patch in compiled.stage_updates.items():
                current = dict(draft["stages"].get(stage_id) or {})
                stages[stage_id] = self._validated_pipeline_stage_config(
                    stage_id,
                    current,
                    patch,
                )
            embedding = self._validated_embedding_profile(
                draft.get("embedding_profile"),
                compiled.embedding_profile,
            )
            retrieval = RetrievalConfig.from_mapping(
                compiled.retrieval_profile,
                base=RetrievalConfig.from_mapping(draft.get("retrieval_profile")),
            ).payload()
            processor = stages["stage_processor"]
            vision = stages["stage_image_understanding"]
            if bool(vision.get("enabled")):
                capabilities = self.vision_processor.capabilities()
                vision_node = next(
                    (
                        str(item.get("id"))
                        for item in graph.get("nodes", [])
                        if isinstance(item, dict)
                        and item.get("kind") == "image_understanding"
                    ),
                    None,
                )
                renderer = capabilities.get("renderer")
                if not isinstance(renderer, dict) or not bool(renderer.get("ready")) or not bool(capabilities.get("image_decoder_ready")):
                    return [
                        GraphValidationIssue(
                            "vision_renderer_unavailable",
                            "Image understanding requires pypdfium2 and Pillow.",
                            node_id=vision_node,
                        )
                    ], None
                if not bool(capabilities.get("targets")):
                    return [
                        GraphValidationIssue(
                            "vision_model_unavailable",
                            "Image understanding requires a configured model gateway.",
                            node_id=vision_node,
                        )
                    ], None
            if str(processor.get("mode") or "general") in {"qa", "summary"}:
                capabilities = self._processor_generation_capabilities(
                    str(processor.get("model_id") or "")
                )
                if not bool(capabilities.get("llm_configured")):
                    processor_node = next(
                        (
                            str(item.get("id"))
                            for item in graph.get("nodes", [])
                            if isinstance(item, dict)
                            and item.get("kind") == "structured_processor"
                        ),
                        None,
                    )
                    return [
                        GraphValidationIssue(
                            "processor_model_unavailable",
                            "QA and Summary modes require a configured model gateway.",
                            node_id=processor_node,
                        )
                    ], None
            normalized_graph = json.loads(json.dumps(compiled.graph))
            normalized_graph["kb_id"] = kb_id
            return [], KnowledgePipelineCompileResult(
                graph=normalized_graph,
                stage_updates=stages,
                embedding_profile=embedding,
                retrieval_profile=retrieval,
            )
        except (PipelineGraphValidationError, PipelineDraftValidationError, ValueError) as exc:
            if isinstance(exc, PipelineGraphValidationError):
                return list(exc.issues), None
            return [GraphValidationIssue("invalid_node_config", str(exc))], None

    def _preview_pipeline_chunks(
        self,
        processed: dict[str, Any],
        config: dict[str, Any],
        *,
        kind: str,
    ) -> list[dict[str, Any]]:
        generated = processed.get("generated_items")
        if isinstance(generated, list) and generated:
            return [
                {
                    "index": index,
                    "chunk_type": str(item.get("item_type") or "generated"),
                    "text_preview": str(item.get("index_text") or "")[:600],
                    "context_preview": str(item.get("context_text") or "")[:600],
                    "source_block_ids": list(item.get("source_block_ids") or []),
                    "truncated": bool(item.get("truncated")),
                }
                for index, item in enumerate(generated)
                if isinstance(item, dict)
            ]

        if kind == "parent_child_chunker":
            splitter: TextSplitter | ParentChildTextSplitter = ParentChildTextSplitter(
                parent_chunk_size=int(config.get("parent_chunk_size", 1500)),
                parent_chunk_overlap=int(config.get("parent_chunk_overlap", 100)),
                child_chunk_size=int(config.get("child_chunk_size", 400)),
                child_chunk_overlap=int(config.get("child_chunk_overlap", 50)),
                parent_separators=list(config.get("parent_separators") or DEFAULT_SEPARATORS),
                child_separators=list(config.get("child_separators") or DEFAULT_SEPARATORS),
            )
        else:
            splitter = TextSplitter(
                chunk_size=int(config.get("chunk_size", self.splitter.chunk_size)),
                chunk_overlap=int(config.get("chunk_overlap", self.splitter.chunk_overlap)),
                separators=list(config.get("separators") or DEFAULT_SEPARATORS),
            )
        chunks: list[dict[str, Any]] = []
        for block in processed.get("blocks", []):
            if not isinstance(block, dict):
                continue
            text = str(block.get("text") or "").strip()
            if not text:
                continue
            for segment in splitter.split_segments(text):
                chunks.append(
                    {
                        "index": len(chunks),
                        "chunk_type": segment.chunk_type,
                        "text_preview": segment.text[:600],
                        "parent_preview": (segment.parent_text or "")[:600] or None,
                        "parent_chunk_id": segment.parent_chunk_id,
                        "start_char": int(block.get("start_char", 0)) + segment.start_char,
                        "end_char": int(block.get("start_char", 0)) + segment.end_char,
                        "truncated": len(segment.text) > 600,
                    }
                )
        return chunks

    def _normalize_pipeline_stage_id(self, value: str) -> str | None:
        if value in self._default_pipeline_draft_stages():
            return value
        return PIPELINE_STAGE_IDS.get(value)

    def _validated_pipeline_stage_config(
        self,
        stage_id: str,
        current: dict[str, Any],
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        config = dict(current)
        if stage_id == "stage_data_source":
            source_mode = str(patch.get("source_mode", config.get("source_mode", "uploaded_files")))
            if source_mode != "uploaded_files":
                raise PipelineDraftValidationError("data_source.source_mode must be uploaded_files.")
            config["source_mode"] = source_mode
            config["allowed_extensions"] = sorted({*supported_extensions(), *SUPPORTED_IMAGE_EXTENSIONS})
            return config

        if stage_id == "stage_processor":
            parser = str(
                patch.get("parser", config.get("parser", "structured_local_parser"))
            )
            if parser == "local_document_parser":
                parser = "structured_local_parser"
            if parser != "structured_local_parser":
                raise PipelineDraftValidationError(
                    "processor.parser must be structured_local_parser."
                )
            mode = str(patch.get("mode", config.get("mode", "general"))).strip()
            if mode not in {"general", "qa", "summary"}:
                raise PipelineDraftValidationError(
                    "processor.mode must be general, qa, or summary."
                )
            failure_policy = str(
                patch.get(
                    "failure_policy",
                    config.get("failure_policy", "continue_on_error"),
                )
            ).strip()
            if failure_policy not in {"continue_on_error", "strict"}:
                raise PipelineDraftValidationError(
                    "processor.failure_policy must be continue_on_error or strict."
                )
            model_id = str(
                patch.get(
                    "model_id",
                    config.get("model_id", self.processor_generator.default_model()),
                )
                or ""
            ).strip()
            if len(model_id) > 200 or (mode in {"qa", "summary"} and not model_id):
                raise PipelineDraftValidationError("processor.model_id is invalid.")
            max_generated_items = self._coerce_int(
                patch.get(
                    "max_generated_items",
                    config.get("max_generated_items", 20),
                ),
                "processor.max_generated_items",
            )
            if not 1 <= max_generated_items <= 50:
                raise PipelineDraftValidationError(
                    "processor.max_generated_items must be between 1 and 50."
                )
            bool_fields = (
                "extract_title",
                "preserve_tables",
                "preserve_code_blocks",
                "remove_repeated_headers_footers",
            )
            bool_values: dict[str, bool] = {}
            for field_name in bool_fields:
                value = patch.get(field_name, config.get(field_name, True))
                if not isinstance(value, bool):
                    raise PipelineDraftValidationError(
                        f"processor.{field_name} must be a boolean."
                    )
                bool_values[field_name] = value
            config.update(
                {
                    "parser": parser,
                    "mode": mode,
                    "model_id": model_id,
                    "failure_policy": failure_policy,
                    "max_generated_items": max_generated_items,
                    **bool_values,
                }
            )
            return config

        if stage_id == "stage_chunker":
            strategy = str(
                patch.get(
                    "strategy",
                    config.get("strategy", "recursive_character"),
                )
            )
            if strategy == "local_recursive_character_chunks":
                strategy = "recursive_character"
            if strategy not in {"recursive_character", "parent_child"}:
                raise PipelineDraftValidationError(
                    "chunker.strategy must be recursive_character or parent_child."
                )
            chunk_size = self._coerce_int(
                patch.get("chunk_size", config.get("chunk_size", self.splitter.chunk_size)),
                "chunker.chunk_size",
            )
            chunk_overlap = self._coerce_int(
                patch.get("chunk_overlap", config.get("chunk_overlap", self.splitter.chunk_overlap)),
                "chunker.chunk_overlap",
            )
            if chunk_size < 100 or chunk_size > 4000:
                raise PipelineDraftValidationError("chunker.chunk_size must be between 100 and 4000.")
            if chunk_overlap < 0 or chunk_overlap >= chunk_size:
                raise PipelineDraftValidationError(
                    "chunker.chunk_overlap must be non-negative and smaller than chunk_size."
                )
            separators = self._validated_separators(
                patch.get("separators", config.get("separators", DEFAULT_SEPARATORS)),
                "chunker.separators",
            )
            parent_size = self._coerce_int(
                patch.get("parent_chunk_size", config.get("parent_chunk_size", 1500)),
                "chunker.parent_chunk_size",
            )
            parent_overlap = self._coerce_int(
                patch.get("parent_chunk_overlap", config.get("parent_chunk_overlap", 100)),
                "chunker.parent_chunk_overlap",
            )
            child_size = self._coerce_int(
                patch.get("child_chunk_size", config.get("child_chunk_size", 400)),
                "chunker.child_chunk_size",
            )
            child_overlap = self._coerce_int(
                patch.get("child_chunk_overlap", config.get("child_chunk_overlap", 50)),
                "chunker.child_chunk_overlap",
            )
            if not 200 <= parent_size <= 8000:
                raise PipelineDraftValidationError(
                    "chunker.parent_chunk_size must be between 200 and 8000."
                )
            if not 100 <= child_size < parent_size:
                raise PipelineDraftValidationError(
                    "chunker.child_chunk_size must be between 100 and parent_chunk_size."
                )
            if parent_overlap < 0 or parent_overlap >= parent_size:
                raise PipelineDraftValidationError(
                    "chunker.parent_chunk_overlap must be smaller than parent_chunk_size."
                )
            if child_overlap < 0 or child_overlap >= child_size:
                raise PipelineDraftValidationError(
                    "chunker.child_chunk_overlap must be smaller than child_chunk_size."
                )
            config.update(
                {
                    "strategy": strategy,
                    "chunk_size": chunk_size,
                    "chunk_overlap": chunk_overlap,
                    "separators": separators,
                    "parent_chunk_size": parent_size,
                    "parent_chunk_overlap": parent_overlap,
                    "child_chunk_size": child_size,
                    "child_chunk_overlap": child_overlap,
                    "parent_separators": self._validated_separators(
                        patch.get(
                            "parent_separators",
                            config.get("parent_separators", DEFAULT_SEPARATORS),
                        ),
                        "chunker.parent_separators",
                    ),
                    "child_separators": self._validated_separators(
                        patch.get(
                            "child_separators",
                            config.get("child_separators", DEFAULT_SEPARATORS),
                        ),
                        "chunker.child_separators",
                    ),
                }
            )
            return config

        if stage_id == "stage_image_understanding":
            enabled = patch.get("enabled", config.get("enabled", False))
            if not isinstance(enabled, bool):
                raise PipelineDraftValidationError("image_understanding.enabled must be boolean.")
            model_id = str(
                patch.get("vision_model_id", config.get("vision_model_id", "")) or ""
            ).strip()
            if len(model_id) > 200 or (enabled and not model_id):
                raise PipelineDraftValidationError(
                    "image_understanding.vision_model_id is required when enabled."
                )
            strategy = str(
                patch.get("pdf_page_strategy", config.get("pdf_page_strategy", "auto"))
            ).strip()
            if strategy not in {"auto", "all"}:
                raise PipelineDraftValidationError(
                    "image_understanding.pdf_page_strategy must be auto or all."
                )
            render_dpi = self._coerce_int(
                patch.get("render_dpi", config.get("render_dpi", 144)),
                "image_understanding.render_dpi",
            )
            max_pages = self._coerce_int(
                patch.get("max_pages", config.get("max_pages", 100)),
                "image_understanding.max_pages",
            )
            max_image_edge = self._coerce_int(
                patch.get("max_image_edge", config.get("max_image_edge", 2048)),
                "image_understanding.max_image_edge",
            )
            if not 72 <= render_dpi <= 300:
                raise PipelineDraftValidationError(
                    "image_understanding.render_dpi must be between 72 and 300."
                )
            if not 1 <= max_pages <= 200:
                raise PipelineDraftValidationError(
                    "image_understanding.max_pages must be between 1 and 200."
                )
            if not 512 <= max_image_edge <= 4096:
                raise PipelineDraftValidationError(
                    "image_understanding.max_image_edge must be between 512 and 4096."
                )
            failure_policy = str(
                patch.get(
                    "failure_policy",
                    config.get("failure_policy", "continue_on_error"),
                )
            ).strip()
            if failure_policy not in {"continue_on_error", "strict"}:
                raise PipelineDraftValidationError(
                    "image_understanding.failure_policy must be continue_on_error or strict."
                )
            config.update(
                {
                    "enabled": enabled,
                    "provider": "openai_compatible_vlm",
                    "vision_model_id": model_id,
                    "pdf_page_strategy": strategy,
                    "render_dpi": render_dpi,
                    "max_pages": max_pages,
                    "max_image_edge": max_image_edge,
                    "failure_policy": failure_policy,
                }
            )
            return config

        raise PipelineDraftValidationError(f"Unknown pipeline stage: {stage_id}")

    def _coerce_int(self, value: Any, field_name: str) -> int:
        if isinstance(value, bool):
            raise PipelineDraftValidationError(f"{field_name} must be an integer.")
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise PipelineDraftValidationError(f"{field_name} must be an integer.") from exc

    def _validated_separators(self, value: Any, field_name: str) -> list[str]:
        if not isinstance(value, list) or not value or len(value) > 20:
            raise PipelineDraftValidationError(
                f"{field_name} must contain between 1 and 20 strings."
            )
        result: list[str] = []
        for item in value:
            if not isinstance(item, str) or len(item) > 20:
                raise PipelineDraftValidationError(
                    f"{field_name} entries must be strings up to 20 characters."
                )
            if item not in result:
                result.append(item)
        if "" not in result:
            result.append("")
        return result

    def _empty_metadata(self) -> dict[str, dict[str, Any]]:
        return {
            "knowledge_bases": {},
            "knowledge_base_deletions": {},
            "documents": {},
            "document_deletions": {},
            "pipeline_drafts": {},
            "pipeline_graphs": {},
            "pipeline_jobs": {},
            "pipeline_versions": {},
            "pipeline_active_versions": {},
            "rag_strategy_recommendations": {},
            "knowledge_write_proposals": {},
        }

    def _read_metadata(self) -> dict[str, dict[str, Any]]:
        with self._metadata_lock:
            return self._read_metadata_unlocked()

    def _read_metadata_unlocked(self) -> dict[str, dict[str, Any]]:
        if not self.metadata_path.exists():
            return self._empty_metadata()
        try:
            data = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return self._empty_metadata()
        if not isinstance(data, dict):
            return self._empty_metadata()
        metadata = {
            "knowledge_bases": data.get("knowledge_bases") if isinstance(data.get("knowledge_bases"), dict) else {},
            "knowledge_base_deletions": data.get("knowledge_base_deletions") if isinstance(data.get("knowledge_base_deletions"), dict) else {},
            "documents": data.get("documents") if isinstance(data.get("documents"), dict) else {},
            "document_deletions": data.get("document_deletions") if isinstance(data.get("document_deletions"), dict) else {},
            "pipeline_drafts": data.get("pipeline_drafts") if isinstance(data.get("pipeline_drafts"), dict) else {},
            "pipeline_graphs": data.get("pipeline_graphs") if isinstance(data.get("pipeline_graphs"), dict) else {},
            "pipeline_jobs": data.get("pipeline_jobs") if isinstance(data.get("pipeline_jobs"), dict) else {},
            "pipeline_versions": data.get("pipeline_versions") if isinstance(data.get("pipeline_versions"), dict) else {},
            "pipeline_active_versions": data.get("pipeline_active_versions") if isinstance(data.get("pipeline_active_versions"), dict) else {},
            "rag_strategy_recommendations": data.get("rag_strategy_recommendations") if isinstance(data.get("rag_strategy_recommendations"), dict) else {},
            "knowledge_write_proposals": data.get("knowledge_write_proposals") if isinstance(data.get("knowledge_write_proposals"), dict) else {},
        }
        for document in metadata["documents"].values():
            if not isinstance(document, dict):
                continue
            document.setdefault("content_type", mimetypes.guess_type(str(document.get("filename") or ""))[0] or "application/octet-stream")
            document.setdefault("ingestion_status", "indexed_legacy")
            document.setdefault("visual_candidate", False)
            document["warnings"] = _bounded_document_warnings(
                document.get("warnings", [])
            )
        for job in metadata["pipeline_jobs"].values():
            if not isinstance(job, dict):
                continue
            stages = job.get("stages")
            if isinstance(stages, list) and not any(
                isinstance(item, dict) and item.get("id") == "vision" for item in stages
            ):
                insert_at = next(
                    (
                        index + 1
                        for index, item in enumerate(stages)
                        if isinstance(item, dict) and item.get("id") == "load"
                    ),
                    0,
                )
                stages.insert(insert_at, self._new_pipeline_job_stages()[1])
            for result in job.get("document_results", []):
                if not isinstance(result, dict):
                    continue
                result.setdefault("vision_status", "skipped")
                result.setdefault("vision_page_count", 0)
                result.setdefault("vision_selected_page_count", 0)
                result.setdefault("vision_processed_page_count", 0)
                result.setdefault("vision_failed_page_count", 0)
                result.setdefault("vision_block_count", 0)
                result.setdefault("vision_warnings", [])
                result.setdefault("vision_error", None)
        return metadata

    def _write_metadata(self, metadata: dict[str, Any]) -> None:
        with self._metadata_lock:
            self._write_metadata_unlocked(metadata)

    def _write_metadata_unlocked(self, metadata: dict[str, Any]) -> None:
        temporary = self.metadata_path.with_suffix(self.metadata_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, self.metadata_path)

    def _kb_payload(self, item: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
        doc_count = sum(
            1
            for document in metadata["documents"].values()
            if document["kb_id"] == item["id"]
            and not document.get("deletion_status")
        )
        return {
            **item,
            "document_count": doc_count,
            "deletion_status": str(item.get("deletion_status") or "active"),
            "deletion_error_code": str(item.get("deletion_error_code") or "")
            or None,
        }

    def _document_payload(self, document: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": document["id"],
            "kb_id": document["kb_id"],
            "filename": document["filename"],
            "size": document["size"],
            "chunk_count": document["chunk_count"],
            "content_type": document.get("content_type")
            or mimetypes.guess_type(str(document["filename"]))[0]
            or "application/octet-stream",
            "ingestion_status": document.get("ingestion_status", "indexed_legacy"),
            "visual_candidate": bool(document.get("visual_candidate", False)),
            "warnings": _bounded_document_warnings(document.get("warnings", [])),
            "analysis_artifact_id": str(document.get("analysis_artifact_id") or "")
            or None,
            "analysis_source": document.get("analysis_source")
            if isinstance(document.get("analysis_source"), dict)
            else None,
            "file_output_id": str(document.get("file_output_id") or "") or None,
            "file_output_source": document.get("file_output_source")
            if isinstance(document.get("file_output_source"), dict)
            else None,
            "created_at": document["created_at"],
        }

    def _ensure_kb_exists(self, metadata: dict[str, Any], kb_id: str | None) -> None:
        if kb_id is None:
            return
        knowledge_base = metadata["knowledge_bases"].get(kb_id)
        if not isinstance(knowledge_base, dict):
            raise KnowledgeBaseNotFoundError("知识库不存在。")

        if knowledge_base.get("deletion_status") or (
            isinstance(metadata["knowledge_base_deletions"].get(kb_id), dict)
            and metadata["knowledge_base_deletions"][kb_id].get("status")
            in {"deleting", "cleanup_pending", "failed"}
        ):
            raise KnowledgeBaseDeletionError(
                "Knowledge base is isolated for deletion and no longer accepts reads or writes."
            )

    def _assert_corpus_mutable(
        self,
        metadata: dict[str, Any],
        kb_id: str,
        *,
        allow_locked: bool = False,
    ) -> None:
        item = metadata["knowledge_bases"].get(kb_id)
        if (
            isinstance(item, dict)
            and bool(item.get("corpus_locked"))
            and not allow_locked
        ):
            raise KnowledgeBaseLockedError(
                "This managed Benchmark corpus is locked; pipeline versions remain editable."
            )

    def complete_benchmark_provisioning(self, kb_id: str) -> dict[str, Any]:
        with self._metadata_lock:
            metadata = self._read_metadata_unlocked()
            self._ensure_kb_exists(metadata, kb_id)
            item = metadata["knowledge_bases"][kb_id]
            item["provisioning_status"] = "ready"
            item["updated_at"] = time.time()
            self._write_metadata_unlocked(metadata)
            return self._kb_payload(item, metadata)

    def _document_for_artifact_id(self, artifact_id: str) -> dict[str, Any]:
        doc_id = artifact_id.removeprefix("artifact_")
        metadata = self._read_metadata()
        document = metadata["documents"].get(doc_id)
        if not document or document.get("deletion_status"):
            raise DocumentNotFoundError("文档不存在。")
        return document

    def _file_asset_payload(self, document: dict[str, Any]) -> dict[str, Any]:
        extension = Path(document["filename"]).suffix.lower()
        mime_type, _ = mimetypes.guess_type(document["filename"])
        return {
            "file_asset_id": str(
                document.get("asset_id") or self._file_asset_id(document["id"])
            ),
            "document_id": document["id"],
            "knowledge_base_id": document["kb_id"],
            "filename": document["filename"],
            "size": document["size"],
            "extension": extension,
            "mime_type": mime_type,
            "ingestion_status": document.get("ingestion_status", "indexed_legacy"),
            "visual_candidate": bool(document.get("visual_candidate", False)),
            "created_at": document["created_at"],
        }

    def _artifact_payload(self, document: dict[str, Any]) -> dict[str, Any]:
        return {
            "artifact_id": self._artifact_id(document["id"]),
            "file_asset_id": str(
                document.get("asset_id") or self._file_asset_id(document["id"])
            ),
            "document_id": document["id"],
            "knowledge_base_id": document["kb_id"],
            "title": document["filename"],
            "chunk_count": document["chunk_count"],
            "status": document.get("ingestion_status", "indexed_legacy"),
            "visual_candidate": bool(document.get("visual_candidate", False)),
            "created_at": document["created_at"],
        }

    def _artifact_id(self, document_id: str) -> str:
        return f"artifact_{document_id}"

    def _file_asset_id(self, document_id: str) -> str:
        return f"file_{document_id}"


def _safe_filename(filename: str) -> str:
    cleaned = Path(filename).name.strip() or "document.txt"
    return re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", cleaned)


def _preview_text(text: str, limit: int = 240) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit].rstrip()}..."


def _rounded_optional(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None
