from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO, Iterator, Literal, Sequence

from .blob_store import BlobValidationError, BlobWriteReceipt, FileBlobStore
from .analysis import (
    FileAnalysisArtifact,
    FileAnalysisConfirmRequest,
    FileAnalysisConfirmResponse,
    FileAnalysisCreateRequest,
    FileAnalysisError,
    FileAnalysisExecutor,
    FileAnalysisJobListResponse,
    FileAnalysisJobResponse,
    FileAnalysisMode,
    FileAnalysisPreflightRequest,
    FileAnalysisPreflightResponse,
    FileAnalysisTargetResolver,
    FileAnalysisTargetsResponse,
    analysis_digests,
    inspect_analysis_source,
)
from .contracts import (
    FileAssetListResponse,
    FileAssetResponse,
    FileInputKind,
    FileInteractionStatus,
    FilePurpose,
)
from .document_parser import (
    LocalDocumentParseError,
    ParsedDocument,
    ParsedDocumentPreview,
    parse_chat_document,
)
from .lifecycle import FileAssetLifecycle
from .registry import FileFormatRegistry, get_file_format_registry
from .repository import (
    FileArtifactRecord,
    FileAnalysisJobRecord,
    FileAssetRecord,
    FileAssetRepositoryError,
    SQLiteFileAssetRepository,
)
from .validation import FileUploadValidator, FileValidationError, ValidatedFile


_UPLOAD_CHUNK_BYTES = 1024 * 1024
_MAINTENANCE_INTERVAL_SECONDS = 30.0
_MAINTENANCE_LIMIT = 100
_ENABLED_MODES = {"shadow", "native"}
_CHAT_ORIGINAL_TTL_SECONDS = 30 * 60
_PARSED_ARTIFACT_IDLE_TTL_SECONDS = 2 * 60 * 60
_PARSED_ARTIFACT_HARD_TTL_SECONDS = 24 * 60 * 60
_PARSED_DOCUMENT_ARTIFACT_KIND = "chat_parsed_document_v1"
_ANALYSIS_ARTIFACT_KIND = "chat_visual_analysis_v1"
_ANALYSIS_CONFIRMATION_TTL_SECONDS = 5 * 60
_ANALYSIS_EXECUTION_CONCURRENCY = 2
_PURPOSE_INPUT_KIND = {
    FilePurpose.CHAT: FileInputKind.DOCUMENT,
    FilePurpose.RAG: FileInputKind.DOCUMENT,
    FilePurpose.AGENT: FileInputKind.DOCUMENT,
    FilePurpose.DATAX: FileInputKind.DATA_SOURCE,
    FilePurpose.WORKFLOW: FileInputKind.DOCUMENT,
}


class FileAssetServiceError(Exception):
    """Stable API-facing error without upstream, path, or body details."""

    def __init__(self, status_code: int, error_code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.message = message


@dataclass(frozen=True, slots=True)
class ChatFileSelection:
    asset_id: str
    handling: Literal["native", "extract"]
    confirmation_revision: int
    analysis_artifact_id: str | None = None
    analysis_prompt: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedChatFile:
    asset_id: str
    scope_id: str
    display_name: str
    format_id: str
    media_type: str
    byte_size: int
    handling: Literal["native", "extract"]
    native_content: bytes | None = None
    parsed_document: ParsedDocument | None = None
    analysis_artifact: FileAnalysisArtifact | None = None
    analysis_prompt: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedWorkflowVisualAsset:
    asset_id: str
    scope_id: str
    display_name: str
    format_id: str
    media_type: str
    byte_size: int
    content: bytes


class FileAssetService:
    def __init__(
        self,
        *,
        storage_dir: str | Path,
        mode: str = "legacy",
        tenant_id: str = "local",
        registry: FileFormatRegistry | None = None,
        repository: SQLiteFileAssetRepository | None = None,
        blob_store: FileBlobStore | None = None,
        validator: FileUploadValidator | None = None,
        analysis_target_resolver: FileAnalysisTargetResolver | None = None,
        analysis_executor: FileAnalysisExecutor | None = None,
        analysis_concurrency_limit: int = _ANALYSIS_EXECUTION_CONCURRENCY,
    ) -> None:
        self.storage_dir = Path(storage_dir)
        self.mode = str(mode or "legacy").strip().lower()
        self.tenant_id = _identifier(tenant_id, "tenant_id")
        self.registry = registry or get_file_format_registry()
        self._repository = repository
        self._blob_store = blob_store
        self._validator = validator or FileUploadValidator(self.registry)
        self._analysis_target_resolver = (
            analysis_target_resolver or FileAnalysisTargetResolver()
        )
        self._analysis_executor = analysis_executor or FileAnalysisExecutor()
        self._analysis_execution_slots = threading.BoundedSemaphore(
            max(1, int(analysis_concurrency_limit))
        )
        self._lifecycle: FileAssetLifecycle | None = None
        self._startup_lock = threading.Lock()
        self._maintenance_lock = threading.Lock()
        self._parse_lock = threading.RLock()
        self._asset_claim_lock = threading.Lock()
        self._asset_read_claims: dict[str, int] = {}
        self._asset_delete_claims: set[str] = set()
        self._maintenance_due = False
        self._last_maintenance_at = 0.0
        self._started = False

    @classmethod
    def from_environment(cls) -> "FileAssetService":
        package_dir = Path(__file__).resolve().parent
        return cls(
            storage_dir=(
                os.getenv("FILE_ASSET_STORAGE_DIR", "").strip()
                or package_dir / "storage"
            ),
            mode=os.getenv("FILE_ASSET_STORE_MODE", "legacy"),
            tenant_id=os.getenv("MODELMIRROR_DEFAULT_TENANT_ID", "local"),
        )

    @property
    def repository(self) -> SQLiteFileAssetRepository:
        self._ensure_ready()
        assert self._repository is not None
        return self._repository

    @property
    def blob_store(self) -> FileBlobStore:
        self._ensure_ready()
        assert self._blob_store is not None
        return self._blob_store

    def upload(
        self,
        stream: BinaryIO,
        *,
        purpose: FilePurpose | str,
        scope_id: str,
        filename: str,
        declared_media_type: str | None,
        input_kind: FileInputKind | str | None = None,
    ) -> FileAssetResponse:
        self._ensure_ready()
        self._run_maintenance_if_due()
        clean_purpose = FilePurpose(purpose)
        clean_scope = _identifier(scope_id, "scope_id")
        input_kind, max_bytes = self._ready_upload_policy(
            clean_purpose, input_kind=input_kind
        )
        asset_id = f"file_{uuid.uuid4().hex}"
        receipt: BlobWriteReceipt | None = None
        validated: ValidatedFile | None = None

        try:
            receipt = self.blob_store.write_stream(
                _read_chunks(stream),
                namespace="blobs",
                max_bytes=max_bytes,
            )
            blob_path = self.blob_store.storage_dir / receipt.storage_key
            validated = self._validator.validate_path(
                blob_path,
                purpose=clean_purpose,
                input_kind=input_kind,
                filename=filename,
                declared_media_type=declared_media_type,
            )
            record = self.repository.create_asset(
                self.tenant_id,
                purpose=clean_purpose,
                scope_id=clean_scope,
                display_name=filename,
                format_id=validated.format_id,
                media_type=validated.media_type,
                storage_key=receipt.storage_key,
                sha256=receipt.sha256,
                byte_size=receipt.byte_size,
                status="ready",
                expires_at=(
                    datetime.now(UTC)
                    + timedelta(seconds=_CHAT_ORIGINAL_TTL_SECONDS)
                    if clean_purpose == FilePurpose.CHAT
                    else None
                ),
                asset_id=asset_id,
                create_initial_binding=True,
            )
            try:
                self.repository.record_audit_event(
                    self.tenant_id,
                    asset_id=asset_id,
                    event_type="upload_completed",
                    sha256=receipt.sha256,
                    format_id=validated.format_id,
                    byte_size=receipt.byte_size,
                    status="ready",
                )
            except Exception as exc:
                self._rollback_created_asset(record)
                raise FileAssetServiceError(
                    500,
                    "file_asset_persistence_failed",
                    "文件元数据未能安全保存，请重新上传。",
                ) from exc
            return _public_asset(record)
        except FileValidationError as exc:
            self._cleanup_failed_upload(
                asset_id=asset_id,
                receipt=receipt,
                validated=validated,
                error_code=exc.error_code,
            )
            raise FileAssetServiceError(
                exc.status_code, exc.error_code, exc.message
            ) from exc
        except BlobValidationError as exc:
            error_code, status_code, message = _blob_error(exc)
            self._audit_upload_failure(asset_id, None, None, error_code)
            raise FileAssetServiceError(status_code, error_code, message) from exc
        except FileAssetRepositoryError as exc:
            if str(exc) == "file_scope_blocked":
                self._cleanup_failed_upload(
                    asset_id=asset_id,
                    receipt=receipt,
                    validated=validated,
                    error_code="file_scope_blocked",
                )
                raise FileAssetServiceError(
                    409,
                    "file_scope_blocked",
                    "The file scope is being deleted and no longer accepts uploads.",
                ) from exc
            raise
        except FileAssetServiceError:
            raise
        except Exception as exc:
            self._cleanup_failed_upload(
                asset_id=asset_id,
                receipt=receipt,
                validated=validated,
                error_code="file_asset_persistence_failed",
            )
            raise FileAssetServiceError(
                500,
                "file_asset_persistence_failed",
                "文件元数据未能安全保存，请重新上传。",
            ) from exc

    def get_asset(
        self,
        asset_id: str,
        *,
        purpose: FilePurpose | str,
        scope_id: str,
    ) -> FileAssetResponse:
        record = self._scoped_record(asset_id, purpose=purpose, scope_id=scope_id)
        if record.status == "expired":
            raise FileAssetServiceError(410, "file_asset_expired", "文件已过期，请重新上传。")
        if record.status != "ready":
            raise FileAssetServiceError(
                409,
                "file_asset_state_conflict",
                "文件当前尚未就绪，请稍后重试。",
            )
        return _public_asset(record)

    def list_assets(
        self,
        *,
        purpose: FilePurpose | str,
        scope_id: str,
    ) -> FileAssetListResponse:
        """List ready assets without exposing private storage metadata."""

        self._ensure_ready()
        self._run_maintenance_if_due()
        clean_purpose = FilePurpose(purpose)
        clean_scope = _identifier(scope_id, "scope_id")
        self._ready_upload_policy(clean_purpose)
        assert self._lifecycle is not None
        ready: list[FileAssetResponse] = []
        for record in self.repository.list_bound_assets(
            self.tenant_id,
            purpose=clean_purpose,
            scope_id=clean_scope,
        ):
            reconciled = self._lifecycle.reconcile_original(record)
            if reconciled is not None and reconciled.status == "ready":
                ready.append(_public_asset(reconciled))
        return FileAssetListResponse(items=tuple(ready), total=len(ready))

    async def list_analysis_targets(self) -> FileAnalysisTargetsResponse:
        self._ensure_ready()
        self._ready_upload_policy(
            FilePurpose.CHAT, input_kind=FileInputKind.VISUAL_ANALYSIS
        )
        return FileAnalysisTargetsResponse(
            items=await self._analysis_target_resolver.list_targets()
        )

    async def preflight_analysis(
        self,
        asset_id: str,
        request: FileAnalysisPreflightRequest,
    ) -> FileAnalysisPreflightResponse:
        self._ensure_ready()
        self._ready_upload_policy(
            FilePurpose.CHAT, input_kind=FileInputKind.VISUAL_ANALYSIS
        )
        record = self._scoped_record(
            asset_id,
            purpose=FilePurpose.CHAT,
            scope_id=request.scope_id,
        )
        target = next(
            (
                item
                for item in await self._analysis_target_resolver.list_targets()
                if item.target_id == request.target_id and item.mode == request.mode
            ),
            None,
        )
        if target is None:
            raise FileAssetServiceError(
                409,
                "analysis_target_unavailable",
                "The selected analysis connection or model is not currently available.",
            )
        if request.mode == FileAnalysisMode.PROVIDER_OCR and record.format_id != "pdf":
            raise FileAssetServiceError(
                422,
                "ocr_requires_pdf",
                "OpenRouter mistral-ocr is only available for PDF files.",
            )
        try:
            content = self.blob_store.read_bytes(record.storage_key)
            page_count, selected_pages = await _to_thread_analysis(
                inspect_analysis_source,
                content,
                format_id=record.format_id,
                selected_pages=request.selected_pages,
            )
        except FileAnalysisError as exc:
            raise _analysis_service_error(exc) from exc
        except Exception as exc:
            raise FileAssetServiceError(
                409,
                "analysis_source_unavailable",
                "The selected file cannot be inspected for one-shot analysis.",
            ) from exc
        config_digest, prompt_sha256 = analysis_digests(
            asset_sha256=record.sha256,
            format_id=record.format_id,
            mode=request.mode,
            target_id=request.target_id,
            selected_pages=selected_pages,
            prompt=request.prompt,
        )
        return FileAnalysisPreflightResponse(
            asset_id=record.id,
            mode=request.mode,
            target=target,
            format=record.format_id,
            page_count=page_count,
            selected_pages=selected_pages,
            prompt_sha256=prompt_sha256,
            config_digest=config_digest,
            paid_confirmation_required=target.paid,
            cost_disclosure=target.cost_disclosure,
            privacy_disclosure=(
                "Only the selected rendered pages are sent to the exact connection and model."
                if request.mode == FileAnalysisMode.VISION
                else (
                    "Only the locally selected PDF pages are sent to the official OpenRouter "
                    "endpoint with mistral-ocr. Embedded annotation images are discarded."
                )
            ),
        )

    async def confirm_analysis(
        self,
        asset_id: str,
        request: FileAnalysisConfirmRequest,
    ) -> FileAnalysisConfirmResponse:
        preflight = await self.preflight_analysis(asset_id, request)
        if preflight.paid_confirmation_required and not request.paid_acknowledged:
            raise FileAssetServiceError(
                422,
                "analysis_paid_confirmation_required",
                "Confirm the OpenRouter OCR charge before continuing.",
            )
        expires_at = datetime.now(UTC) + timedelta(
            seconds=_ANALYSIS_CONFIRMATION_TTL_SECONDS
        )
        try:
            confirmation = self.repository.confirm_analysis(
                self.tenant_id,
                asset_id,
                scope_id=request.scope_id,
                mode=request.mode.value,
                target_id=request.target_id,
                config_digest=preflight.config_digest,
                prompt_sha256=preflight.prompt_sha256,
                paid_acknowledged=request.paid_acknowledged,
                expires_at=expires_at,
            )
        except FileAssetRepositoryError as exc:
            raise FileAssetServiceError(
                409,
                "analysis_confirmation_conflict",
                "The file changed before analysis confirmation. Review it again.",
            ) from exc
        if confirmation is None:
            raise _not_found()
        return FileAnalysisConfirmResponse(
            asset_id=asset_id,
            mode=request.mode,
            target_id=request.target_id,
            config_digest=preflight.config_digest,
            prompt_sha256=preflight.prompt_sha256,
            confirmation_revision=confirmation.revision,
            confirmed_at=confirmation.confirmed_at,
            expires_at=confirmation.expires_at,
        )

    async def create_analysis(
        self,
        asset_id: str,
        request: FileAnalysisCreateRequest,
    ) -> FileAnalysisJobResponse:
        preflight = await self.preflight_analysis(asset_id, request)
        try:
            job = self.repository.create_analysis_job(
                self.tenant_id,
                asset_id,
                scope_id=request.scope_id,
                mode=request.mode.value,
                target_id=request.target_id,
                config_digest=preflight.config_digest,
                prompt_sha256=preflight.prompt_sha256,
                paid_acknowledged=request.paid_acknowledged,
                confirmation_revision=request.confirmation_revision,
                selected_pages=preflight.selected_pages,
            )
        except FileAssetRepositoryError as exc:
            if str(exc) == "analysis_job_already_active":
                raise FileAssetServiceError(
                    409,
                    "analysis_job_already_active",
                    "This file already has an unfinished analysis task.",
                ) from exc
            raise
        if job is None:
            raise FileAssetServiceError(
                409,
                "analysis_confirmation_invalid",
                "The analysis confirmation expired or no longer matches this request.",
            )
        return self._analysis_job_response(job, include_result=False)

    async def run_analysis(
        self,
        analysis_id: str,
        *,
        prompt: str,
    ) -> None:
        slot_acquired = False
        try:
            slot_acquired = await self._wait_for_analysis_slot(analysis_id)
            if not slot_acquired:
                return
            job = self.repository.claim_analysis_job(self.tenant_id, analysis_id)
            if job is None:
                return
            record = self._scoped_record(
                job.asset_id,
                purpose=FilePurpose.CHAT,
                scope_id=job.scope_id,
            )
            target = await self._analysis_target_resolver.resolve(job.target_id)
            if target.public.mode.value != job.mode:
                raise FileAnalysisError(
                    409,
                    "analysis_target_mismatch",
                    "The selected analysis target no longer matches this task.",
                )
            selected_pages = _decode_analysis_pages(job.selected_pages)
            content = self.blob_store.read_bytes(record.storage_key)

            def progress(processed_pages: int) -> None:
                self.repository.update_analysis_progress(
                    self.tenant_id,
                    analysis_id,
                    processed_pages=processed_pages,
                )

            def cancelled() -> bool:
                current = self.repository.get_analysis_job(
                    self.tenant_id, analysis_id
                )
                return bool(
                    current is None
                    or current.status in {"cancel_requested", "cancelled"}
                )

            artifact, actual_cost = await self._analysis_executor.execute(
                content=content,
                format_id=record.format_id,
                source_filename=record.display_name,
                source_sha256=record.sha256,
                selected_pages=selected_pages,
                prompt=prompt,
                target=target,
                asset_id=record.id,
                progress=progress,
                cancelled=cancelled,
            )
            if cancelled():
                self.repository.acknowledge_analysis_cancel(
                    self.tenant_id, analysis_id
                )
                return
            encoded = artifact.model_dump_json().encode("utf-8")
            receipt = self.blob_store.write_bytes(encoded, namespace="artifacts")
            try:
                stored = self.repository.create_artifact(
                    self.tenant_id,
                    record.id,
                    kind=_ANALYSIS_ARTIFACT_KIND,
                    storage_key=receipt.storage_key,
                    sha256=receipt.sha256,
                    byte_size=receipt.byte_size,
                    status="ready",
                    expires_at=datetime.now(UTC)
                    + timedelta(seconds=_PARSED_ARTIFACT_IDLE_TTL_SECONDS),
                )
            except Exception:
                try:
                    self.blob_store.delete(receipt.storage_key)
                except Exception:
                    self._maintenance_due = True
                raise
            completed = self.repository.complete_analysis_job(
                self.tenant_id,
                analysis_id,
                result_artifact_id=stored.id,
                actual_cost_usd=actual_cost,
            )
            if completed is None:
                self._maintenance_due = True
                self.repository.acknowledge_analysis_cancel(
                    self.tenant_id, analysis_id
                )
        except asyncio.CancelledError:
            current = self.repository.get_analysis_job(self.tenant_id, analysis_id)
            if current is not None and current.status == "cancel_requested":
                self.repository.acknowledge_analysis_cancel(
                    self.tenant_id, analysis_id
                )
            else:
                self.repository.interrupt_analysis_job(
                    self.tenant_id, analysis_id
                )
        except asyncio.TimeoutError:
            self.repository.fail_analysis_job(
                self.tenant_id,
                analysis_id,
                error_code="analysis_timeout",
            )
        except FileAnalysisError as exc:
            self.repository.fail_analysis_job(
                self.tenant_id,
                analysis_id,
                error_code=exc.error_code,
            )
        except FileAssetServiceError as exc:
            self.repository.fail_analysis_job(
                self.tenant_id,
                analysis_id,
                error_code=exc.error_code,
            )
        except Exception:
            self.repository.fail_analysis_job(
                self.tenant_id,
                analysis_id,
                error_code="analysis_internal_error",
            )
        finally:
            if slot_acquired:
                self._analysis_execution_slots.release()

    async def _wait_for_analysis_slot(self, analysis_id: str) -> bool:
        """Bound billable provider concurrency without binding to one event loop."""

        while not self._analysis_execution_slots.acquire(blocking=False):
            current = self.repository.get_analysis_job(self.tenant_id, analysis_id)
            if current is None:
                return False
            if current.status == "cancel_requested":
                self.repository.acknowledge_analysis_cancel(
                    self.tenant_id, analysis_id
                )
                return False
            if current.status not in {"queued", "running"}:
                return False
            await asyncio.sleep(0.05)
        return True

    def get_analysis(
        self,
        asset_id: str,
        analysis_id: str,
        *,
        scope_id: str,
    ) -> FileAnalysisJobResponse:
        self._ensure_ready()
        job = self.repository.get_analysis_job(
            self.tenant_id,
            analysis_id,
            scope_id=_identifier(scope_id, "scope_id"),
        )
        if job is None or job.asset_id != _identifier(asset_id, "asset_id"):
            raise _not_found()
        if not self.repository.binding_exists(
            self.tenant_id,
            job.asset_id,
            purpose=FilePurpose.CHAT,
            scope_id=job.scope_id,
        ):
            raise _not_found()
        return self._analysis_job_response(job, include_result=True)

    def list_analyses(self, *, scope_id: str) -> FileAnalysisJobListResponse:
        self._ensure_ready()
        clean_scope = _identifier(scope_id, "scope_id")
        items = tuple(
            self._analysis_job_response(item, include_result=True)
            for item in self.repository.list_analysis_jobs(
                self.tenant_id, scope_id=clean_scope
            )
            if self.repository.binding_exists(
                self.tenant_id,
                item.asset_id,
                purpose=FilePurpose.CHAT,
                scope_id=clean_scope,
            )
        )
        return FileAnalysisJobListResponse(items=items, total=len(items))

    def resolve_analysis_artifact(
        self,
        asset_id: str,
        artifact_id: str,
        *,
        scope_id: str,
    ) -> FileAnalysisArtifact:
        """Read a completed Chat analysis result without exposing blob keys."""

        clean_asset = _identifier(asset_id, "asset_id")
        clean_artifact = _identifier(artifact_id, "artifact_id")
        clean_scope = _identifier(scope_id, "scope_id")
        self._artifact_scoped_record(
            clean_asset,
            purpose=FilePurpose.CHAT,
            scope_id=clean_scope,
        )
        job = self.repository.analysis_job_for_artifact(
            self.tenant_id,
            clean_asset,
            scope_id=clean_scope,
            artifact_id=clean_artifact,
        )
        if job is None:
            raise _not_found()
        artifact = self.repository.get_artifact(
            self.tenant_id,
            clean_asset,
            clean_artifact,
        )
        if artifact is None or artifact.kind != _ANALYSIS_ARTIFACT_KIND:
            raise _not_found()
        artifact = self.repository.touch_artifact(
            self.tenant_id,
            clean_asset,
            clean_artifact,
            idle_seconds=_PARSED_ARTIFACT_IDLE_TTL_SECONDS,
            hard_seconds=_PARSED_ARTIFACT_HARD_TTL_SECONDS,
        )
        if artifact is None or artifact.status != "ready":
            raise FileAssetServiceError(
                410,
                "analysis_artifact_expired",
                "识别结果已过期，请重新处理文件。",
            )
        try:
            result = FileAnalysisArtifact.model_validate_json(
                self.blob_store.read_bytes(artifact.storage_key)
            )
        except Exception as exc:
            raise FileAssetServiceError(
                409,
                "analysis_artifact_unavailable",
                "识别结果暂时不可用，请重新处理文件。",
            ) from exc
        if result.asset_id != clean_asset:
            raise FileAssetServiceError(
                409,
                "analysis_artifact_mismatch",
                "识别结果与当前文件不一致。",
            )
        return result

    def cancel_analysis(
        self,
        asset_id: str,
        analysis_id: str,
        *,
        scope_id: str,
    ) -> FileAnalysisJobResponse:
        current = self.get_analysis(
            asset_id, analysis_id, scope_id=scope_id
        )
        if current.status in {"completed", "failed", "cancelled", "interrupted"}:
            return current
        job = self.repository.request_analysis_cancel(
            self.tenant_id, analysis_id
        )
        if job is None:
            raise _not_found()
        return self._analysis_job_response(job, include_result=False)

    def interrupt_stale_analyses(self, *, stale_seconds: int = 300) -> int:
        self._ensure_ready()
        return self.repository.interrupt_stale_analysis_jobs(
            stale_before=datetime.now(UTC)
            - timedelta(seconds=max(1, int(stale_seconds)))
        )

    def _analysis_job_response(
        self,
        job: FileAnalysisJobRecord,
        *,
        include_result: bool,
    ) -> FileAnalysisJobResponse:
        result: FileAnalysisArtifact | None = None
        artifact_id = job.result_artifact_id
        if include_result and artifact_id:
            artifact = self.repository.get_artifact(
                self.tenant_id, job.asset_id, artifact_id
            )
            if artifact is not None and artifact.status == "ready":
                artifact = self.repository.touch_artifact(
                    self.tenant_id,
                    job.asset_id,
                    artifact.id,
                    idle_seconds=_PARSED_ARTIFACT_IDLE_TTL_SECONDS,
                    hard_seconds=_PARSED_ARTIFACT_HARD_TTL_SECONDS,
                )
            if artifact is not None:
                try:
                    result = FileAnalysisArtifact.model_validate_json(
                        self.blob_store.read_bytes(artifact.storage_key)
                    )
                except Exception:
                    result = None
        return FileAnalysisJobResponse(
            analysis_id=job.id,
            asset_id=job.asset_id,
            scope_id=job.scope_id,
            mode=FileAnalysisMode(job.mode),
            target_id=job.target_id,
            selected_pages=_decode_analysis_pages(job.selected_pages),
            page_count=job.page_count,
            processed_pages=job.processed_pages,
            status=job.status,  # type: ignore[arg-type]
            result_artifact_id=artifact_id,
            result=result,
            actual_cost_usd=job.actual_cost_usd,
            error_code=job.error_code,
            created_at=job.created_at,
            updated_at=job.updated_at,
            completed_at=job.completed_at,
        )

    def parse_asset(
        self,
        asset_id: str,
        *,
        purpose: FilePurpose | str,
        scope_id: str,
    ) -> ParsedDocumentPreview:
        """Create or reuse a scoped local Chat parsing artifact."""

        clean_purpose = FilePurpose(purpose)
        if clean_purpose != FilePurpose.CHAT:
            raise FileAssetServiceError(
                501,
                "file_parse_not_implemented",
                "当前模块的统一解析将在对应格式批次开放。",
            )
        with self._parse_lock:
            record = self._artifact_scoped_record(
                asset_id, purpose=clean_purpose, scope_id=scope_id
            )
            existing = self._ready_parsed_artifact(record.id, touch=True)
            if existing is not None:
                return self._artifact_preview(record.id, existing)
            record = self._scoped_record(
                asset_id, purpose=clean_purpose, scope_id=scope_id
            )

            source_path = self.blob_store.storage_dir / record.storage_key
            try:
                parsed = parse_chat_document(
                    source_path,
                    format_id=record.format_id,
                    title=record.display_name,
                )
            except LocalDocumentParseError as exc:
                self._audit_parse_failure(record, exc.error_code)
                raise FileAssetServiceError(
                    exc.status_code,
                    exc.error_code,
                    exc.message,
                ) from exc

            content = parsed.model_dump_json().encode("utf-8")
            receipt: BlobWriteReceipt | None = None
            try:
                receipt = self.blob_store.write_bytes(
                    content,
                    namespace="artifacts",
                    max_bytes=2 * 1024 * 1024,
                )
                artifact = self.repository.create_artifact(
                    self.tenant_id,
                    record.id,
                    kind=_PARSED_DOCUMENT_ARTIFACT_KIND,
                    storage_key=receipt.storage_key,
                    sha256=receipt.sha256,
                    byte_size=receipt.byte_size,
                    status="ready",
                    expires_at=datetime.now(UTC)
                    + timedelta(seconds=_PARSED_ARTIFACT_IDLE_TTL_SECONDS),
                )
            except Exception as exc:
                if receipt is not None:
                    try:
                        self.blob_store.delete(receipt.storage_key)
                    except Exception:
                        self._maintenance_due = True
                self._audit_parse_failure(record, "file_parse_persistence_failed")
                raise FileAssetServiceError(
                    500,
                    "file_parse_persistence_failed",
                    "解析结果未能安全保存，请稍后重试。",
                ) from exc

            try:
                self.repository.record_audit_event(
                    self.tenant_id,
                    asset_id=record.id,
                    event_type="parse_completed",
                    sha256=record.sha256,
                    format_id=record.format_id,
                    byte_size=record.byte_size,
                    status="ready",
                )
            except Exception:
                # The artifact remains tenant/scope protected; maintenance and
                # deletion still own its lifecycle even if telemetry is down.
                pass
            return self._artifact_preview(record.id, artifact, parsed=parsed)

    def preview_asset(
        self,
        asset_id: str,
        *,
        purpose: FilePurpose | str,
        scope_id: str,
    ) -> ParsedDocumentPreview:
        clean_purpose = FilePurpose(purpose)
        if clean_purpose != FilePurpose.CHAT:
            raise FileAssetServiceError(
                501,
                "file_preview_not_implemented",
                "当前模块的统一预览将在对应格式批次开放。",
            )
        record = self._artifact_scoped_record(
            asset_id, purpose=clean_purpose, scope_id=scope_id
        )
        artifact = self._ready_parsed_artifact(record.id, touch=True)
        if artifact is None:
            if record.status == "expired":
                raise FileAssetServiceError(
                    410,
                    "file_preview_expired",
                    "文件预览已过期，请重新上传并解析。",
                )
            raise FileAssetServiceError(
                409,
                "file_preview_not_ready",
                "请先完成本地内容提取，再查看预览。",
            )
        return self._artifact_preview(record.id, artifact)

    def resolve_chat_inputs(
        self,
        selections: Sequence[ChatFileSelection],
        *,
        scope_id: str,
        native_pdf_verified: bool = False,
    ) -> tuple[ResolvedChatFile, ...]:
        """Resolve 1..5 Chat files without exposing storage paths to /api/chat."""

        self._ensure_ready()
        clean_scope = _identifier(scope_id, "scope_id")
        requested = tuple(selections)
        analysis_requested = any(
            item.analysis_artifact_id is not None for item in requested
        )
        if analysis_requested:
            if len(requested) != 1 or any(
                item.analysis_artifact_id is None for item in requested
            ):
                raise FileAssetServiceError(
                    422,
                    "analysis_file_count_invalid",
                    "Each one-shot visual or OCR task sends exactly one file result.",
                )
            self._ready_upload_policy(
                FilePurpose.CHAT,
                input_kind=FileInputKind.VISUAL_ANALYSIS,
            )
        else:
            self._ready_upload_policy(FilePurpose.CHAT)
        if not 1 <= len(requested) <= 5:
            raise FileAssetServiceError(
                422,
                "invalid_chat_file_count",
                "每轮聊天请选择 1 到 5 个文件。",
            )
        asset_ids = [_identifier(item.asset_id, "asset_id") for item in requested]
        if len(asset_ids) != len(set(asset_ids)):
            raise FileAssetServiceError(
                422,
                "duplicate_chat_file",
                "同一文件不能在一轮消息中重复添加。",
            )

        records = [
            self._artifact_scoped_record(
                asset_id,
                purpose=FilePurpose.CHAT,
                scope_id=clean_scope,
            )
            for asset_id in asset_ids
        ]
        for selection, record in zip(requested, records, strict=True):
            if selection.analysis_artifact_id:
                prompt_sha256 = hashlib.sha256(
                    str(selection.analysis_prompt or "").encode("utf-8")
                ).hexdigest()
                confirmed = self.repository.analysis_send_confirmation_matches(
                    self.tenant_id,
                    record.id,
                    scope_id=clean_scope,
                    artifact_id=selection.analysis_artifact_id,
                    prompt_sha256=prompt_sha256,
                    revision=selection.confirmation_revision,
                )
            else:
                confirmed = self.repository.binding_confirmation_matches(
                    self.tenant_id,
                    record.id,
                    purpose=FilePurpose.CHAT,
                    scope_id=clean_scope,
                    handling=selection.handling,
                    revision=selection.confirmation_revision,
                )
            if not confirmed:
                raise FileAssetServiceError(
                    409,
                    "chat_file_confirmation_required",
                    "文件确认已失效，请重新预览并点击“确认用于本轮”。",
                )
        if sum(record.byte_size for record in records) > 25 * 1024 * 1024:
            raise FileAssetServiceError(
                413,
                "chat_files_too_large",
                "本轮文件合计超过 25 MiB，请减少文件数量或大小。",
            )

        resolved: list[ResolvedChatFile] = []
        for selection, record in zip(requested, records, strict=True):
            handling = str(selection.handling or "").strip().lower()
            if selection.analysis_artifact_id:
                if handling != "extract":
                    raise FileAssetServiceError(
                        422,
                        "analysis_file_handling_invalid",
                        "Analysis results must be sent as extracted user data.",
                    )
                artifact = self.repository.get_artifact(
                    self.tenant_id,
                    record.id,
                    selection.analysis_artifact_id,
                )
                if artifact is None or artifact.kind != _ANALYSIS_ARTIFACT_KIND:
                    raise FileAssetServiceError(
                        409,
                        "analysis_artifact_unavailable",
                        "The confirmed analysis result is no longer available.",
                    )
                artifact = self.repository.touch_artifact(
                    self.tenant_id,
                    record.id,
                    artifact.id,
                    idle_seconds=_PARSED_ARTIFACT_IDLE_TTL_SECONDS,
                    hard_seconds=_PARSED_ARTIFACT_HARD_TTL_SECONDS,
                )
                if artifact is None:
                    raise FileAssetServiceError(
                        410,
                        "analysis_artifact_expired",
                        "The analysis result expired. Run the analysis again.",
                    )
                try:
                    analyzed = FileAnalysisArtifact.model_validate_json(
                        self.blob_store.read_bytes(artifact.storage_key)
                    )
                except Exception as exc:
                    raise FileAssetServiceError(
                        409,
                        "analysis_artifact_unavailable",
                        "The confirmed analysis result cannot be read.",
                    ) from exc
                resolved.append(
                    ResolvedChatFile(
                        asset_id=record.id,
                        scope_id=clean_scope,
                        display_name=record.display_name,
                        format_id=record.format_id,
                        media_type=record.media_type,
                        byte_size=record.byte_size,
                        handling="extract",
                        analysis_artifact=analyzed,
                        analysis_prompt=str(selection.analysis_prompt or ""),
                    )
                )
                continue
            if handling == "native":
                if not native_pdf_verified or record.format_id != "pdf":
                    raise FileAssetServiceError(
                        422,
                        "native_file_handling_not_available",
                        "当前模型未通过 PDF 原生读取能力确认，请改用“提取内容后发送”。",
                    )
                ready_record = self._scoped_record(
                    record.id,
                    purpose=FilePurpose.CHAT,
                    scope_id=clean_scope,
                )
                preview = self.parse_asset(
                    record.id,
                    purpose=FilePurpose.CHAT,
                    scope_id=clean_scope,
                )
                parsed_document = ParsedDocument.model_validate(
                    preview.model_dump(
                        exclude={
                            "asset_id",
                            "artifact_id",
                            "artifact_expires_at",
                        }
                    )
                )
                try:
                    native_content = self.blob_store.read_bytes(
                        ready_record.storage_key
                    )
                except Exception as exc:
                    raise FileAssetServiceError(
                        409,
                        "file_content_unavailable",
                        "文件原件暂时无法读取，请重新上传。",
                    ) from exc
                resolved.append(
                    ResolvedChatFile(
                        asset_id=record.id,
                        scope_id=clean_scope,
                        display_name=record.display_name,
                        format_id=record.format_id,
                        media_type=record.media_type,
                        byte_size=record.byte_size,
                        handling="native",
                        native_content=native_content,
                        parsed_document=parsed_document,
                    )
                )
                continue
            if handling != "extract":
                raise FileAssetServiceError(
                    422,
                    "invalid_file_handling",
                    "文件处理方式必须是原生读取或提取内容。",
                )
            preview = self.parse_asset(
                record.id,
                purpose=FilePurpose.CHAT,
                scope_id=clean_scope,
            )
            document = ParsedDocument.model_validate(
                preview.model_dump(
                    exclude={"asset_id", "artifact_id", "artifact_expires_at"}
                )
            )
            resolved.append(
                ResolvedChatFile(
                    asset_id=record.id,
                    scope_id=clean_scope,
                    display_name=record.display_name,
                    format_id=record.format_id,
                    media_type=record.media_type,
                    byte_size=record.byte_size,
                    handling="extract",
                    parsed_document=document,
                )
            )
        return tuple(resolved)

    def confirm_chat_input(
        self,
        asset_id: str,
        *,
        scope_id: str,
        handling: Literal["native", "extract"],
        analysis_artifact_id: str | None = None,
        analysis_prompt: str | None = None,
    ) -> tuple[int, str]:
        """Persist a user confirmation on the tenant-scoped Chat binding."""

        clean_scope = _identifier(scope_id, "scope_id")
        clean_handling = str(handling or "").strip().lower()
        if clean_handling not in {"native", "extract"}:
            raise FileAssetServiceError(
                422,
                "invalid_file_handling",
                "文件处理方式必须是原生读取或提取内容。",
            )
        record = self._artifact_scoped_record(
            asset_id,
            purpose=FilePurpose.CHAT,
            scope_id=clean_scope,
        )
        if analysis_artifact_id is not None:
            if clean_handling != "extract":
                raise FileAssetServiceError(
                    422,
                    "analysis_file_handling_invalid",
                    "Analysis results must be sent as extracted user data.",
                )
            clean_artifact = _identifier(
                analysis_artifact_id, "artifact_id"
            )
            prompt = str(analysis_prompt or "")
            if len(prompt) > 2_000 or "\x00" in prompt:
                raise FileAssetServiceError(
                    422,
                    "analysis_prompt_invalid",
                    "The one-shot prompt exceeds the 2,000-character limit.",
                )
            job = self.repository.analysis_job_for_artifact(
                self.tenant_id,
                record.id,
                scope_id=clean_scope,
                artifact_id=clean_artifact,
            )
            prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
            if job is None or job.prompt_sha256 != prompt_sha256:
                raise FileAssetServiceError(
                    409,
                    "analysis_artifact_confirmation_required",
                    "The analysis result or one-shot prompt changed. Review it again.",
                )
            artifact = self.repository.touch_artifact(
                self.tenant_id,
                record.id,
                clean_artifact,
                idle_seconds=_PARSED_ARTIFACT_IDLE_TTL_SECONDS,
                hard_seconds=_PARSED_ARTIFACT_HARD_TTL_SECONDS,
            )
            if artifact is None or artifact.kind != _ANALYSIS_ARTIFACT_KIND:
                raise FileAssetServiceError(
                    410,
                    "analysis_artifact_expired",
                    "The analysis result expired. Run the analysis again.",
                )
            confirmed = self.repository.confirm_analysis_send(
                self.tenant_id,
                record.id,
                scope_id=clean_scope,
                artifact_id=clean_artifact,
                prompt_sha256=prompt_sha256,
            )
            if confirmed is None:
                raise _not_found()
            return confirmed.revision, confirmed.confirmed_at
        if self._ready_parsed_artifact(record.id, touch=True) is None:
            raise FileAssetServiceError(
                409,
                "file_preview_not_ready",
                "请先完成本地内容提取并查看预览，再确认用于本轮。",
            )
        if clean_handling == "native":
            if record.format_id != "pdf":
                raise FileAssetServiceError(
                    422,
                    "native_file_handling_not_available",
                    "只有 PDF 可选择由模型原生读取，请改用“提取内容后发送”。",
                )
            self._scoped_record(
                record.id,
                purpose=FilePurpose.CHAT,
                scope_id=clean_scope,
            )
        confirmed = self.repository.confirm_binding(
            self.tenant_id,
            record.id,
            purpose=FilePurpose.CHAT,
            scope_id=clean_scope,
            handling=clean_handling,  # type: ignore[arg-type]
        )
        if confirmed is None:  # pragma: no cover - scoped check invariant
            raise _not_found()
        return confirmed

    def finalize_chat_inputs(
        self,
        files: Sequence[ResolvedChatFile],
        *,
        success: bool,
    ) -> bool:
        """Return True only when every successful-turn original is absent."""

        if not success:
            return False
        originals_removed = True
        for item in files:
            self.repository.clear_binding_confirmation(
                self.tenant_id,
                item.asset_id,
                purpose=FilePurpose.CHAT,
                scope_id=item.scope_id,
            )
            self.repository.clear_analysis_send_confirmation(
                self.tenant_id,
                item.asset_id,
                scope_id=item.scope_id,
            )
            record = self.repository.get_asset(self.tenant_id, item.asset_id)
            if record is None:
                continue
            try:
                self.blob_store.delete(record.storage_key)
                original_exists = self.blob_store.exists(record.storage_key)
            except Exception:
                self._maintenance_due = True
                try:
                    original_exists = self.blob_store.exists(record.storage_key)
                except Exception:
                    original_exists = True
            if original_exists:
                originals_removed = False
                self._maintenance_due = True
                continue
            if record.status not in {"expired", "deleting", "deleted"}:
                self.repository.set_asset_status(
                    self.tenant_id, record.id, "expired"
                )
            try:
                self.repository.record_audit_event(
                    self.tenant_id,
                    asset_id=record.id,
                    event_type="chat_original_consumed",
                    sha256=record.sha256,
                    format_id=record.format_id,
                    byte_size=record.byte_size,
                    status="expired",
                )
            except Exception:
                pass
        return originals_removed

    def delete_asset(
        self,
        asset_id: str,
        *,
        purpose: FilePurpose | str,
        scope_id: str,
    ) -> bool:
        """Remove the requested binding and report whether physical GC is pending."""

        clean_asset = _identifier(asset_id, "asset_id")
        with self._claim_asset_delete(clean_asset):
            record = self._scoped_record(
                clean_asset,
                purpose=purpose,
                scope_id=scope_id,
                allow_expired=True,
            )
            clean_purpose = FilePurpose(purpose)
            clean_scope = _identifier(scope_id, "scope_id")
            removed = self.repository.remove_binding(
                self.tenant_id,
                record.id,
                purpose=clean_purpose,
                scope_id=clean_scope,
                expire_if_unreferenced=True,
            )
            if not removed:
                raise _not_found()
            self._run_maintenance_if_due(force=True)
            remaining = self.repository.get_asset(self.tenant_id, record.id)
            cleanup_pending = bool(
                remaining is not None and remaining.reference_count == 0
            )
            if cleanup_pending:
                self._maintenance_due = True
            return cleanup_pending

    def asset_cleanup_complete(self, asset_id: str) -> bool:
        """Retry tenant-scoped GC after a binding was already removed.

        A missing row means physical cleanup completed. A row with remaining
        bindings is intentionally retained for another module, so the caller's
        unbind is also complete. Only an unreferenced row that still exists is
        considered pending and remains a stable retry handle by asset ID.
        """

        self._ensure_ready()
        clean_asset = _identifier(asset_id, "asset_id")
        self._run_maintenance_if_due(force=True)
        remaining = self.repository.get_asset(self.tenant_id, clean_asset)
        if remaining is None or remaining.reference_count > 0:
            return True
        self._maintenance_due = True
        return False

    def delete_scope(
        self,
        *,
        purpose: FilePurpose | str,
        scope_id: str,
    ) -> bool:
        """Idempotently remove a Chat scope and report pending physical GC."""

        self._ensure_ready()
        clean_purpose = FilePurpose(purpose)
        if clean_purpose is not FilePurpose.CHAT:
            raise FileAssetServiceError(
                422,
                "file_scope_cleanup_not_supported",
                "本批次仅支持清理聊天会话文件，请在对应模块中删除其他文件。",
            )
        clean_scope = _identifier(scope_id, "scope_id")
        affected_ids = self.repository.remove_scope_bindings(
            self.tenant_id,
            purpose=clean_purpose,
            scope_id=clean_scope,
            expire_if_unreferenced=True,
            preserve_active_output_assets=True,
        )
        # Always retry maintenance so a repeated DELETE can complete a prior
        # cleanup_pending response without requiring another upload.
        self._run_maintenance_if_due(force=True)
        cleanup_pending = self.repository.scope_cleanup_pending(
            self.tenant_id,
            purpose=clean_purpose,
            scope_id=clean_scope,
        ) or any(
            (
                remaining := self.repository.get_asset(
                    self.tenant_id, asset_id
                )
            )
            is not None
            and remaining.reference_count == 0
            for asset_id in affected_ids
        )
        if cleanup_pending:
            self._maintenance_due = True
        return cleanup_pending

    def block_and_delete_rag_scope(self, scope_id: str) -> tuple[tuple[str, ...], bool]:
        """Durably block and detach exactly one local RAG knowledge-base scope.

        The repository retains the affected asset IDs across restarts. Shared
        assets remain alive when another binding exists; unreferenced assets
        must disappear physically before cleanup is acknowledged.
        """

        self._ensure_ready()
        clean_scope = _identifier(scope_id, "scope_id")
        affected_ids = self.repository.block_scope_and_remove_bindings(
            self.tenant_id,
            purpose=FilePurpose.RAG,
            scope_id=clean_scope,
            expire_if_unreferenced=True,
        )
        self._run_maintenance_if_due(force=True)
        cleanup_pending = any(
            (
                remaining := self.repository.get_asset(self.tenant_id, asset_id)
            )
            is not None
            and remaining.reference_count == 0
            for asset_id in affected_ids
        )
        if cleanup_pending:
            self._maintenance_due = True
        return affected_ids, cleanup_pending

    def rag_scope_cleanup_complete(self, scope_id: str) -> bool:
        """Retry and verify physical cleanup for a previously blocked RAG scope."""

        self._ensure_ready()
        clean_scope = _identifier(scope_id, "scope_id")
        if not self.repository.scope_is_blocked(
            self.tenant_id,
            purpose=FilePurpose.RAG,
            scope_id=clean_scope,
        ):
            return False
        self._run_maintenance_if_due(force=True)
        affected_ids = self.repository.scope_cleanup_asset_ids(
            self.tenant_id,
            purpose=FilePurpose.RAG,
            scope_id=clean_scope,
        )
        complete = all(
            (
                remaining := self.repository.get_asset(self.tenant_id, asset_id)
            )
            is None
            or remaining.reference_count > 0
            for asset_id in affected_ids
        )
        if not complete:
            self._maintenance_due = True
        return complete

    def require_scoped_ready(
        self,
        asset_id: str,
        *,
        purpose: FilePurpose | str,
        scope_id: str,
    ) -> None:
        self.get_asset(asset_id, purpose=purpose, scope_id=scope_id)

    def resolve_workflow_document(
        self,
        asset_id: str,
        *,
        scope_id: str,
    ) -> ParsedDocument:
        """Parse one ready Workflow asset without exposing its storage path."""

        self._ready_upload_policy(FilePurpose.WORKFLOW)
        clean_asset = _identifier(asset_id, "asset_id")
        with self._claim_asset_read(clean_asset):
            record = self._scoped_record(
                clean_asset,
                purpose=FilePurpose.WORKFLOW,
                scope_id=scope_id,
            )
            if record.status != "ready":
                raise FileAssetServiceError(
                    409,
                    "file_asset_state_conflict",
                    "文件当前尚未就绪，请刷新后重试。",
                )
            source_path = self.blob_store.storage_dir / record.storage_key
            try:
                return parse_chat_document(
                    source_path,
                    format_id=record.format_id,
                    title=record.display_name,
                )
            except LocalDocumentParseError as exc:
                self._audit_parse_failure(record, exc.error_code)
                raise FileAssetServiceError(
                    exc.status_code,
                    exc.error_code,
                    exc.message,
                ) from exc

    def resolve_workflow_visual_asset(
        self,
        asset_id: str,
        *,
        scope_id: str,
    ) -> ResolvedWorkflowVisualAsset:
        """Read one scoped visual asset without exposing its storage key."""

        self._ready_upload_policy(
            FilePurpose.WORKFLOW,
            input_kind=FileInputKind.VISUAL_ANALYSIS,
        )
        clean_asset = _identifier(asset_id, "asset_id")
        clean_scope = _identifier(scope_id, "scope_id")
        with self._claim_asset_read(clean_asset):
            record = self._scoped_record(
                clean_asset,
                purpose=FilePurpose.WORKFLOW,
                scope_id=clean_scope,
            )
            if record.status != "ready":
                raise FileAssetServiceError(
                    409,
                    "file_asset_state_conflict",
                    "文件当前尚未就绪，请刷新后重试。",
                )
            if record.format_id not in {"pdf", "jpeg", "png", "webp"}:
                raise FileAssetServiceError(
                    422,
                    "workflow_visual_asset_unsupported",
                    "该工作流文件不能用于视觉理解。",
                )
            return ResolvedWorkflowVisualAsset(
                asset_id=record.id,
                scope_id=clean_scope,
                display_name=record.display_name,
                format_id=record.format_id,
                media_type=record.media_type,
                byte_size=record.byte_size,
                content=self.blob_store.read_bytes(record.storage_key),
            )

    @contextmanager
    def _claim_asset_read(self, asset_id: str) -> Iterator[None]:
        """Hold a process-local run claim until parsing finishes."""

        with self._asset_claim_lock:
            if asset_id in self._asset_delete_claims:
                raise _asset_in_use()
            self._asset_read_claims[asset_id] = (
                self._asset_read_claims.get(asset_id, 0) + 1
            )
        try:
            yield
        finally:
            with self._asset_claim_lock:
                remaining = self._asset_read_claims.get(asset_id, 0) - 1
                if remaining > 0:
                    self._asset_read_claims[asset_id] = remaining
                else:
                    self._asset_read_claims.pop(asset_id, None)

    @contextmanager
    def _claim_asset_delete(self, asset_id: str) -> Iterator[None]:
        """Reject deletion while a workflow run owns the asset."""

        with self._asset_claim_lock:
            if (
                self._asset_read_claims.get(asset_id, 0) > 0
                or asset_id in self._asset_delete_claims
            ):
                raise _asset_in_use()
            self._asset_delete_claims.add(asset_id)
        try:
            yield
        finally:
            with self._asset_claim_lock:
                self._asset_delete_claims.discard(asset_id)

    def _scoped_record(
        self,
        asset_id: str,
        *,
        purpose: FilePurpose | str,
        scope_id: str,
        allow_expired: bool = False,
    ) -> FileAssetRecord:
        self._ensure_ready()
        self._run_maintenance_if_due()
        clean_asset = _identifier(asset_id, "asset_id")
        clean_purpose = FilePurpose(purpose)
        clean_scope = _identifier(scope_id, "scope_id")
        if not self.repository.binding_exists(
            self.tenant_id,
            clean_asset,
            purpose=clean_purpose,
            scope_id=clean_scope,
        ):
            raise _not_found()
        record = self.repository.get_asset(self.tenant_id, clean_asset)
        if record is None:
            raise _not_found()
        assert self._lifecycle is not None
        reconciled = self._lifecycle.reconcile_original(record)
        if reconciled is None:
            raise FileAssetServiceError(
                409,
                "file_asset_state_conflict",
                "文件状态已发生变化，请刷新后重试。",
            )
        record = reconciled
        if record.status == "expired" and not allow_expired:
            raise FileAssetServiceError(410, "file_asset_expired", "文件已过期，请重新上传。")
        if record.status in {"deleting", "deleted"}:
            raise FileAssetServiceError(
                409,
                "file_asset_state_conflict",
                "文件正在删除，无法执行当前操作。",
            )
        return record

    def _artifact_scoped_record(
        self,
        asset_id: str,
        *,
        purpose: FilePurpose,
        scope_id: str,
    ) -> FileAssetRecord:
        """Authorize a parsed result even after its short-lived original expires."""

        self._ensure_ready()
        self._run_maintenance_if_due()
        clean_asset = _identifier(asset_id, "asset_id")
        clean_scope = _identifier(scope_id, "scope_id")
        if not self.repository.binding_exists(
            self.tenant_id,
            clean_asset,
            purpose=purpose,
            scope_id=clean_scope,
        ):
            raise _not_found()
        record = self.repository.get_asset(self.tenant_id, clean_asset)
        if record is None:
            raise _not_found()
        if record.status in {"deleting", "deleted"}:
            raise FileAssetServiceError(
                409,
                "file_asset_state_conflict",
                "文件正在删除，无法读取解析结果。",
            )
        return record

    def _ready_parsed_artifact(
        self, asset_id: str, *, touch: bool
    ) -> FileArtifactRecord | None:
        artifact = self.repository.latest_artifact(
            self.tenant_id,
            asset_id,
            kind=_PARSED_DOCUMENT_ARTIFACT_KIND,
        )
        if artifact is None or artifact.status != "ready":
            return None
        if not touch:
            return artifact
        return self.repository.touch_artifact(
            self.tenant_id,
            asset_id,
            artifact.id,
            idle_seconds=_PARSED_ARTIFACT_IDLE_TTL_SECONDS,
            hard_seconds=_PARSED_ARTIFACT_HARD_TTL_SECONDS,
        )

    def _artifact_preview(
        self,
        asset_id: str,
        artifact: FileArtifactRecord,
        *,
        parsed: ParsedDocument | None = None,
    ) -> ParsedDocumentPreview:
        try:
            document = parsed or ParsedDocument.model_validate_json(
                self.blob_store.read_bytes(artifact.storage_key)
            )
        except Exception as exc:
            self._maintenance_due = True
            raise FileAssetServiceError(
                409,
                "file_preview_unavailable",
                "解析结果暂时无法读取，请重新上传并解析。",
            ) from exc
        if artifact.expires_at is None:  # pragma: no cover - creation invariant
            raise FileAssetServiceError(
                409,
                "file_preview_unavailable",
                "解析结果缺少有效期限，请重新解析。",
            )
        return ParsedDocumentPreview(
            asset_id=asset_id,
            artifact_id=artifact.id,
            artifact_expires_at=artifact.expires_at,
            **document.model_dump(),
        )

    def _audit_parse_failure(
        self, record: FileAssetRecord, error_code: str
    ) -> None:
        try:
            self.repository.record_audit_event(
                self.tenant_id,
                asset_id=record.id,
                event_type="parse_failed",
                sha256=record.sha256,
                format_id=record.format_id,
                byte_size=record.byte_size,
                status="failed",
                error_code=error_code,
            )
        except Exception:
            pass

    def _ready_upload_policy(
        self,
        purpose: FilePurpose,
        *,
        input_kind: FileInputKind | str | None = None,
    ) -> tuple[FileInputKind, int]:
        if purpose == FilePurpose.AGENT:
            raise FileAssetServiceError(
                422,
                "file_input_not_ready",
                "Agent 现有文件入口仍使用 Xpert 会话存储；统一文件资产 binding 尚未接通。",
            )
        default_input_kind = _PURPOSE_INPUT_KIND.get(purpose)
        if default_input_kind is None:
            raise FileAssetServiceError(
                422,
                "file_input_not_supported",
                "本批文件入口仅开放资料库、Data X 和智能体的既有安全格式。",
            )
        requested_input_kind = (
            FileInputKind(input_kind) if input_kind is not None else default_input_kind
        )
        if input_kind is not None and not (
            requested_input_kind == FileInputKind.VISUAL_ANALYSIS
            and purpose in {FilePurpose.CHAT, FilePurpose.WORKFLOW}
        ):
            raise FileAssetServiceError(
                422,
                "file_input_not_supported",
                "The requested file input type is not available on this upload route.",
            )
        policy = next(
            (
                item
                for item in self.registry.policies_for(purpose)
                if item.input_kind == requested_input_kind
            ),
            None,
        )
        readiness = next(
            (
                item
                for item in self.registry.capabilities_response(
                    purpose=purpose
                ).capabilities
                if item.input_kind == requested_input_kind
            ),
            None,
        )
        if (
            policy is None
            or readiness is None
            or readiness.interaction_status != FileInteractionStatus.READY
        ):
            raise FileAssetServiceError(
                422,
                "file_input_not_ready",
                "当前模块的文件入口尚未启用。",
            )
        return requested_input_kind, policy.max_bytes_per_file

    def _ensure_ready(self) -> None:
        if self.mode not in _ENABLED_MODES:
            raise FileAssetServiceError(
                503,
                "file_asset_store_disabled",
                "统一文件资产服务当前未启用。",
            )
        if self._started:
            return
        with self._startup_lock:
            if self._started:
                return
            self._blob_store = self._blob_store or FileBlobStore(self.storage_dir)
            self._repository = self._repository or SQLiteFileAssetRepository(
                self.storage_dir
            )
            # Billable one-shot jobs are never replayed after a process restart.
            # Persisted non-terminal rows are made visibly interrupted instead.
            self._repository.interrupt_stale_analysis_jobs(
                stale_before=datetime.now(UTC) + timedelta(seconds=1)
            )
            # Output publication is never replayed after a process restart.
            # A queued or running row remains visible and explicitly retryable.
            self._repository.interrupt_active_output_records()
            self._repository.interrupt_active_output_tasks()
            self._lifecycle = FileAssetLifecycle(
                self._repository, self._blob_store
            )
            self._lifecycle.cleanup_startup()
            result = self._lifecycle.garbage_collect(limit=_MAINTENANCE_LIMIT)
            self._purge_expired_payloads()
            self._maintenance_due = self._maintenance_due or bool(
                result.failed or result.claimed >= _MAINTENANCE_LIMIT
            )
            self._last_maintenance_at = time.monotonic()
            self._started = True

    def _run_maintenance_if_due(self, *, force: bool = False) -> None:
        assert self._lifecycle is not None
        now = time.monotonic()
        if (
            not force
            and not self._maintenance_due
            and now - self._last_maintenance_at < _MAINTENANCE_INTERVAL_SECONDS
        ):
            return
        with self._maintenance_lock:
            now = time.monotonic()
            if (
                not force
                and not self._maintenance_due
                and now - self._last_maintenance_at
                < _MAINTENANCE_INTERVAL_SECONDS
            ):
                return
            try:
                result = self._lifecycle.garbage_collect(
                    limit=_MAINTENANCE_LIMIT
                )
                self._purge_expired_payloads()
            except Exception:
                self._maintenance_due = True
            else:
                self._maintenance_due = self._maintenance_due or bool(
                    result.failed or result.claimed >= _MAINTENANCE_LIMIT
                )
            self._last_maintenance_at = now

    def _purge_expired_payloads(self) -> None:
        """Physically enforce Chat-original and parsed-artifact retention."""

        assert self._repository is not None
        assert self._blob_store is not None
        cleanup_failed = False
        for artifact in self._repository.expire_due_artifacts():
            try:
                self._blob_store.delete(artifact.storage_key)
            except Exception:
                cleanup_failed = True
        for asset in self._repository.list_expired_referenced_assets(
            purpose=FilePurpose.CHAT
        ):
            try:
                self._blob_store.delete(asset.storage_key)
            except Exception:
                cleanup_failed = True
        if cleanup_failed:
            self._maintenance_due = True

    def _cleanup_failed_upload(
        self,
        *,
        asset_id: str,
        receipt: BlobWriteReceipt | None,
        validated: ValidatedFile | None,
        error_code: str,
    ) -> None:
        cleanup_failed = False
        if receipt is not None:
            try:
                self.blob_store.delete(receipt.storage_key)
            except Exception:
                cleanup_failed = True
        self._audit_upload_failure(asset_id, receipt, validated, error_code)
        if cleanup_failed:
            self._audit_upload_failure(
                asset_id, receipt, validated, "blob_cleanup_failed"
            )

    def _rollback_created_asset(self, record: FileAssetRecord) -> None:
        try:
            self.repository.remove_binding(
                self.tenant_id,
                record.id,
                purpose=record.purpose,
                scope_id=record.scope_id,
                expire_if_unreferenced=True,
            )
            assert self._lifecycle is not None
            self._lifecycle.garbage_collect()
        except Exception:
            # Startup reconciliation will remove a blob only after its grace window.
            pass

    def _audit_upload_failure(
        self,
        asset_id: str,
        receipt: BlobWriteReceipt | None,
        validated: ValidatedFile | None,
        error_code: str,
    ) -> None:
        try:
            self.repository.record_audit_event(
                self.tenant_id,
                asset_id=asset_id,
                event_type="upload_failed",
                sha256=receipt.sha256 if receipt else None,
                format_id=validated.format_id if validated else None,
                byte_size=receipt.byte_size if receipt else None,
                status="failed",
                error_code=error_code,
            )
        except Exception:
            pass


_default_lock = threading.Lock()
_default_service: FileAssetService | None = None
_default_key: tuple[str, str, str] | None = None


def get_file_asset_service() -> FileAssetService:
    global _default_key, _default_service
    package_dir = Path(__file__).resolve().parent
    key = (
        os.getenv("FILE_ASSET_STORE_MODE", "legacy").strip().lower(),
        os.getenv("FILE_ASSET_STORAGE_DIR", "").strip()
        or str(package_dir / "storage"),
        os.getenv("MODELMIRROR_DEFAULT_TENANT_ID", "local").strip() or "local",
    )
    if _default_service is not None and _default_key == key:
        return _default_service
    with _default_lock:
        if _default_service is None or _default_key != key:
            _default_service = FileAssetService(
                mode=key[0], storage_dir=key[1], tenant_id=key[2]
            )
            _default_key = key
    return _default_service


def _read_chunks(stream: BinaryIO) -> Iterator[bytes]:
    while True:
        chunk = stream.read(_UPLOAD_CHUNK_BYTES)
        if not chunk:
            return
        if not isinstance(chunk, bytes):
            raise BlobValidationError("blob_chunk_must_be_bytes")
        yield chunk


def _public_asset(record: FileAssetRecord) -> FileAssetResponse:
    return FileAssetResponse(
        asset_id=record.id,
        purpose=record.purpose,
        scope_id=record.scope_id,
        display_name=record.display_name,
        format=record.format_id,
        media_type=record.media_type,
        byte_size=record.byte_size,
        status=record.status,
        expires_at=record.expires_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _blob_error(
    exc: BlobValidationError,
) -> tuple[str, int, str]:
    code = str(exc)
    if code == "blob_too_large":
        return "file_too_large", 413, "文件超过当前入口的大小限制。"
    if code == "empty_blob":
        return "empty_file", 422, "文件为空，请选择包含内容的文件。"
    return "invalid_file_stream", 422, "文件流无法安全读取，请重新上传。"


def _asset_in_use() -> FileAssetServiceError:
    return FileAssetServiceError(
        409,
        "file_asset_in_use",
        "文件正在被工作流使用，请等待当前运行结束后再删除。",
    )


def _not_found() -> FileAssetServiceError:
    return FileAssetServiceError(
        404,
        "file_asset_not_found",
        "未找到当前范围内的文件。",
    )


async def _to_thread_analysis(
    function: Any, *args: Any, **kwargs: Any
) -> Any:
    return await asyncio.to_thread(function, *args, **kwargs)


def _analysis_service_error(exc: FileAnalysisError) -> FileAssetServiceError:
    return FileAssetServiceError(exc.status_code, exc.error_code, exc.message)


def _decode_analysis_pages(value: str) -> tuple[int, ...]:
    try:
        pages = tuple(int(item) for item in value.split(",") if item)
    except ValueError as exc:  # pragma: no cover - repository invariant
        raise FileAssetServiceError(
            409,
            "analysis_state_invalid",
            "The saved analysis page selection is invalid.",
        ) from exc
    if not pages or len(pages) > 20 or pages != tuple(sorted(set(pages))):
        raise FileAssetServiceError(
            409,
            "analysis_state_invalid",
            "The saved analysis page selection is invalid.",
        )
    return pages


def _identifier(value: object, field: str) -> str:
    clean = str(value or "").strip()
    if not clean or len(clean) > 256 or any(ord(character) < 32 for character in clean):
        raise FileAssetServiceError(422, f"invalid_{field}", f"{field} 无效。")
    if field == "scope_id" and re.fullmatch(r"[A-Za-z0-9._:-]+", clean) is None:
        raise FileAssetServiceError(
            422,
            "invalid_scope_id",
            "scope_id 仅可包含字母、数字、点、下划线、冒号和连字符。",
        )
    return clean
