from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import os
import re
import sqlite3
import stat
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from .contracts import FilePurpose
from .document_parser import LocalDocumentParseError, parse_chat_document
from .output_contracts import (
    FileOutputCapabilitiesResponse,
    FileOutputFormatCapability,
    FileOutputLimits,
    FileOutputListResponse,
    FileOutputPreviewResponse,
    FileOutputResponse,
    FileOutputReuseConfirmResponse,
)
from .output_renderer import (
    FileOutputRenderer,
    OutputRenderError,
    OutputRenderSpec,
    validate_render_spec,
)
from .registry import FILE_FORMAT_REGISTRY_VERSION
from .repository import FileOutputRecord
from .service import FileAssetService, FileAssetServiceError, get_file_asset_service


MIB = 1024 * 1024
MAX_OUTPUT_BYTES = 50 * MIB
MAX_OUTPUT_TOTAL_BYTES = 100 * MIB
MAX_OUTPUTS_PER_TURN = 5
MAX_OUTPUT_SPEC_BYTES = 2 * MIB
MAX_OUTPUT_SPEC_CHARS = 500_000
OUTPUT_HARD_TTL_SECONDS = 7 * 24 * 60 * 60
OUTPUT_CONFIRMATION_TTL_SECONDS = 10 * 60
OUTPUT_RETRY_SPEC_TTL_SECONDS = 60 * 60
OUTPUT_REUSE_INPUT_TTL_SECONDS = 30 * 60

_PURPOSES = {FilePurpose.CHAT, FilePurpose.AGENT, FilePurpose.WORKFLOW}
_PRODUCER_KINDS = {
    "chat_tool",
    "chat_image",
    "chat_audio",
    "chat_video",
    "sandbox",
    "browser",
    "mcp",
    "workflow_node",
}
_DANGEROUS_SUFFIXES = {
    ".exe", ".dll", ".msi", ".com", ".scr", ".bat", ".cmd", ".ps1",
    ".vbs", ".jar", ".app", ".dmg", ".deb", ".rpm", ".apk",
}


@dataclass(frozen=True, slots=True)
class OutputFormatDefinition:
    format_id: str
    extensions: tuple[str, ...]
    media_types: tuple[str, ...]
    preview_kind: Literal["text", "document", "image", "audio", "video", "none"]
    generation_kind: Literal["text", "document", "workbook", "presentation", "captured"]
    save_rag: bool


_FORMATS = (
    OutputFormatDefinition("plain_text", (".txt",), ("text/plain",), "text", "text", True),
    OutputFormatDefinition("markdown", (".md", ".markdown"), ("text/markdown", "text/plain"), "text", "text", True),
    OutputFormatDefinition("json", (".json",), ("application/json", "text/json"), "text", "text", True),
    OutputFormatDefinition("csv", (".csv",), ("text/csv",), "text", "text", True),
    OutputFormatDefinition("pdf", (".pdf",), ("application/pdf",), "document", "document", True),
    OutputFormatDefinition("docx", (".docx",), ("application/vnd.openxmlformats-officedocument.wordprocessingml.document",), "document", "document", True),
    OutputFormatDefinition("xlsx", (".xlsx",), ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",), "document", "workbook", True),
    OutputFormatDefinition("pptx", (".pptx",), ("application/vnd.openxmlformats-officedocument.presentationml.presentation",), "document", "presentation", True),
    OutputFormatDefinition("jpeg", (".jpg", ".jpeg"), ("image/jpeg", "image/jpg"), "image", "captured", False),
    OutputFormatDefinition("png", (".png",), ("image/png",), "image", "captured", False),
    OutputFormatDefinition("webp", (".webp",), ("image/webp",), "image", "captured", False),
    OutputFormatDefinition("wav", (".wav",), ("audio/wav", "audio/x-wav"), "audio", "captured", False),
    OutputFormatDefinition("mp3", (".mp3",), ("audio/mpeg", "audio/mp3"), "audio", "captured", False),
    OutputFormatDefinition("flac", (".flac",), ("audio/flac", "audio/x-flac"), "audio", "captured", False),
    OutputFormatDefinition("m4a", (".m4a",), ("audio/mp4", "audio/x-m4a"), "audio", "captured", False),
    OutputFormatDefinition("ogg", (".ogg",), ("audio/ogg", "application/ogg"), "audio", "captured", False),
    OutputFormatDefinition("audio_webm", (".webm",), ("audio/webm",), "audio", "captured", False),
    OutputFormatDefinition("mp4", (".mp4",), ("video/mp4",), "video", "captured", False),
    OutputFormatDefinition("mpeg", (".mpeg", ".mpg"), ("video/mpeg",), "video", "captured", False),
    OutputFormatDefinition("mov", (".mov",), ("video/quicktime",), "video", "captured", False),
    OutputFormatDefinition("video_webm", (".webm",), ("video/webm",), "video", "captured", False),
)
_FORMAT_MAP = {item.format_id: item for item in _FORMATS}


class FileOutputService:
    def __init__(
        self,
        file_service: FileAssetService | None = None,
        *,
        renderer: FileOutputRenderer | None = None,
    ) -> None:
        self.file_service = file_service or get_file_asset_service()
        self.renderer = renderer or FileOutputRenderer()

    @property
    def enabled(self) -> bool:
        return _env_enabled("FILE_OUTPUT_ASSETS_ENABLED")

    def capabilities(
        self,
        *,
        purpose: FilePurpose | str,
        model_id: str | None,
        verified_chat_tool: bool | None = None,
    ) -> FileOutputCapabilitiesResponse:
        clean_purpose = FilePurpose(purpose)
        master_enabled = self.enabled
        store_enabled = self.file_service.mode in {"shadow", "native"}
        chat_tool_enabled = _env_enabled("CHAT_FILE_OUTPUT_TOOL_ENABLED")
        purpose_supported = clean_purpose in _PURPOSES
        ready = master_enabled and store_enabled and purpose_supported and (
            clean_purpose is not FilePurpose.CHAT
            or (chat_tool_enabled and verified_chat_tool is True)
        )
        if ready:
            status: Literal["ready", "planned", "disabled"] = "ready"
            reason = None
        elif not master_enabled:
            status = "disabled"
            reason = "Unified output assets are disabled by configuration."
        elif not store_enabled:
            status = "disabled"
            reason = "The unified file asset store is disabled."
        elif not purpose_supported:
            status = "disabled"
            reason = "This module is outside the output-asset closure scope."
        elif clean_purpose is FilePurpose.CHAT and not chat_tool_enabled:
            status = "disabled"
            reason = "The allowlisted Chat file-output tool is disabled."
        else:
            status = "planned"
            reason = "The exact Chat model is not currently verified for tool calling."
        formats = tuple(
            FileOutputFormatCapability(
                format_id=item.format_id,
                media_types=item.media_types,
                preview_kind=item.preview_kind,
                actions=tuple(
                    action
                    for action in ("preview", "download", "reuse", "save_rag", "delete")
                    if action != "preview" or item.preview_kind != "none"
                    if action != "reuse"
                    or item.preview_kind in {"text", "document"}
                    or (
                        clean_purpose is FilePurpose.CHAT
                        and item.preview_kind in {"image", "audio", "video"}
                    )
                    if action != "save_rag" or item.save_rag
                ),
                generation_kind=item.generation_kind,
                interaction_status=status,
                status_reason=reason,
            )
            for item in _FORMATS
        )
        return FileOutputCapabilitiesResponse(
            registry_version=FILE_FORMAT_REGISTRY_VERSION,
            requested_purpose=clean_purpose,
            requested_model_id=str(model_id).strip() if model_id else None,
            model_specific=(
                clean_purpose is FilePurpose.CHAT
                and bool(model_id)
                and verified_chat_tool is not None
            ),
            interaction_status=status,
            status_reason=reason,
            limits=FileOutputLimits(
                max_files_per_turn=MAX_OUTPUTS_PER_TURN,
                max_bytes_per_file=MAX_OUTPUT_BYTES,
                max_total_bytes_per_turn=MAX_OUTPUT_TOTAL_BYTES,
                max_spec_bytes=MAX_OUTPUT_SPEC_BYTES,
                max_spec_chars=MAX_OUTPUT_SPEC_CHARS,
                hard_ttl_seconds=OUTPUT_HARD_TTL_SECONDS,
            ),
            formats=formats,
        )

    def register_bytes(
        self,
        content: bytes,
        *,
        purpose: FilePurpose | str,
        scope_id: str,
        producer_kind: str,
        producer_artifact_id: str,
        filename: str,
        format_id: str,
        media_type: str,
        source_run_id: str | None = None,
        source_message_id: str | None = None,
        source_node_id: str | None = None,
        warnings: tuple[str, ...] = (),
    ) -> FileOutputResponse:
        self._ensure_enabled()
        clean_purpose = FilePurpose(purpose)
        if clean_purpose not in _PURPOSES:
            raise FileAssetServiceError(422, "output_purpose_not_supported", "This module cannot publish output assets.")
        clean_scope = _identifier(scope_id, "scope_id")
        clean_producer = str(producer_kind or "").strip().lower()
        if clean_producer not in _PRODUCER_KINDS:
            raise FileAssetServiceError(422, "output_producer_not_supported", "The output producer is not allowlisted.")
        clean_producer_id = _identifier(producer_artifact_id, "producer_artifact_id")
        turn_key = _identifier(
            source_message_id or source_run_id or clean_producer_id,
            "source_message_id",
        )
        existing = self.file_service.repository.get_output_by_producer(
            self.file_service.tenant_id,
            producer_kind=clean_producer,
            producer_artifact_id=clean_producer_id,
        )
        if existing is not None:
            if existing.purpose != clean_purpose.value or existing.scope_id != clean_scope:
                raise FileAssetServiceError(404, "file_output_not_found", "The output file was not found in this scope.")
            return self._public(existing)
        definition, clean_name, clean_media = _validate_output(
            content,
            filename=filename,
            format_id=format_id,
            media_type=media_type,
        )
        siblings = self.file_service.repository.list_output_records(
            self.file_service.tenant_id,
            purpose=clean_purpose,
            scope_id=clean_scope,
        )
        turn_items = tuple(
            item for item in siblings
            if item.source_message_id == turn_key and item.status not in {"deleted", "expired"}
        )
        if len(turn_items) >= MAX_OUTPUTS_PER_TURN:
            raise FileAssetServiceError(413, "output_count_limit_exceeded", "A turn can publish at most five output files.")
        if sum(item.byte_size for item in turn_items) + len(content) > MAX_OUTPUT_TOTAL_BYTES:
            raise FileAssetServiceError(413, "output_total_size_exceeded", "The output files for this turn exceed 100 MiB.")
        output_id = f"output_{uuid.uuid4().hex}"
        try:
            record = self.file_service.repository.create_output_record(
                self.file_service.tenant_id,
                purpose=clean_purpose,
                scope_id=clean_scope,
                producer_kind=clean_producer,
                producer_artifact_id=clean_producer_id,
                display_name=clean_name,
                format_id=definition.format_id,
                media_type=clean_media,
                preview_kind=definition.preview_kind,
                status="running",
                source_run_id=source_run_id,
                source_message_id=turn_key,
                source_node_id=source_node_id,
                output_id=output_id,
            )
        except sqlite3.IntegrityError:
            current = self.file_service.repository.get_output_by_producer(
                self.file_service.tenant_id,
                producer_kind=clean_producer,
                producer_artifact_id=clean_producer_id,
            )
            if current is None or current.purpose != clean_purpose.value or current.scope_id != clean_scope:
                raise FileAssetServiceError(409, "output_registration_conflict", "The output registration conflicted with another request.")
            return self._public(current)
        return self._persist_output_record(
            record,
            content=content,
            definition=definition,
            filename=clean_name,
            media_type=clean_media,
            warnings=warnings,
        )

    def register_local_artifact(
        self,
        path: str | Path,
        *,
        trusted_root: str | Path,
        purpose: FilePurpose | str,
        scope_id: str,
        producer_kind: str,
        producer_artifact_id: str,
        filename: str,
        media_type: str,
        expected_size: int | None = None,
        expected_sha256: str | None = None,
        source_run_id: str | None = None,
        source_message_id: str | None = None,
        source_node_id: str | None = None,
        warnings: tuple[str, ...] = (),
    ) -> FileOutputResponse:
        """Copy one explicitly published local artifact into the opaque blob store."""

        content = _read_local_artifact(
            path,
            trusted_root=trusted_root,
            expected_size=expected_size,
            expected_sha256=expected_sha256,
        )
        format_id = _infer_output_format(filename, media_type)
        return self.register_bytes(
            content,
            purpose=purpose,
            scope_id=scope_id,
            producer_kind=producer_kind,
            producer_artifact_id=producer_artifact_id,
            filename=filename,
            format_id=format_id,
            media_type=media_type,
            source_run_id=source_run_id,
            source_message_id=source_message_id,
            source_node_id=source_node_id,
            warnings=warnings,
        )

    def register_runtime_artifact(
        self,
        path: str | Path,
        *,
        trusted_root: str | Path,
        producer_kind: Literal["sandbox", "browser", "mcp"],
        producer_artifact_id: str,
        filename: str,
        media_type: str,
        runtime_metadata: dict[str, object],
        expected_size: int | None = None,
        expected_sha256: str | None = None,
    ) -> FileOutputResponse | None:
        """Register an explicit runtime artifact for an Xpert conversation or Workflow."""

        if not self.enabled:
            return None
        xpert_id = str(runtime_metadata.get("xpert_id") or "").strip()
        conversation_id = str(runtime_metadata.get("conversation_id") or "").strip()
        workflow_id = str(runtime_metadata.get("workflow_id") or "").strip()
        if xpert_id and conversation_id:
            purpose = FilePurpose.AGENT
            scope_id = f"xpert:{xpert_id}:{conversation_id}"
        elif workflow_id:
            purpose = FilePurpose.WORKFLOW
            scope_id = f"workflow:{workflow_id}"
        else:
            return None
        run_id = str(runtime_metadata.get("run_id") or "").strip() or None
        node_id = str(runtime_metadata.get("node_id") or "").strip() or None
        return self.register_local_artifact(
            path,
            trusted_root=trusted_root,
            purpose=purpose,
            scope_id=scope_id,
            producer_kind=producer_kind,
            producer_artifact_id=producer_artifact_id,
            filename=filename,
            media_type=media_type,
            expected_size=expected_size,
            expected_sha256=expected_sha256,
            source_run_id=run_id,
            source_message_id=run_id or producer_artifact_id,
            source_node_id=node_id,
        )

    def register_mcp_embedded_artifacts(
        self,
        content_items: list[dict[str, Any]],
        *,
        runtime_metadata: dict[str, object],
        tool_name: str,
    ) -> tuple[dict[str, object], ...]:
        """Register only explicitly marked embedded MCP blobs already held locally.

        Resource links, URLs, filesystem paths, and unmarked blobs are ignored.  A
        malformed marked artifact becomes a failed output card without failing the
        original MCP tool result.
        """

        if not self.enabled:
            return ()
        explicit = tuple(
            item for item in content_items
            if item.get("type") == "resource"
            and isinstance(item.get("resource"), dict)
            and isinstance(item.get("_meta"), dict)
            and isinstance(item["_meta"].get("modelmirror/outputArtifact"), dict)
        )
        results: list[dict[str, object]] = []
        for item in explicit[:MAX_OUTPUTS_PER_TURN]:
            marker = dict(item["_meta"]["modelmirror/outputArtifact"])
            resource = dict(item["resource"])
            artifact_id = str(marker.get("artifact_id") or "").strip()
            filename = str(marker.get("filename") or "").strip()
            media_type = str(resource.get("mimeType") or resource.get("mime_type") or "").strip()
            encoded = resource.get("blob")
            stable_id = "mcp_" + hashlib.sha256(
                "\x00".join(
                    (
                        str(runtime_metadata.get("run_id") or ""),
                        str(tool_name or ""),
                        artifact_id,
                    )
                ).encode("utf-8")
            ).hexdigest()[:32]
            try:
                _identifier(artifact_id, "mcp_artifact_id")
                if not isinstance(encoded, str) or len(encoded) > ((MAX_OUTPUT_BYTES + 2) // 3) * 4 + 4:
                    raise FileAssetServiceError(
                        413,
                        "output_size_limit_exceeded",
                        "The embedded MCP artifact exceeds 50 MiB.",
                    )
                try:
                    content = base64.b64decode(encoded, validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise FileAssetServiceError(
                        422,
                        "output_mcp_blob_invalid",
                        "The embedded MCP artifact is invalid.",
                    ) from exc
                scope = self._runtime_scope(runtime_metadata)
                if scope is None:
                    continue
                purpose, scope_id = scope
                response = self.register_bytes(
                    content,
                    purpose=purpose,
                    scope_id=scope_id,
                    producer_kind="mcp",
                    producer_artifact_id=stable_id,
                    filename=filename,
                    format_id=_infer_output_format(filename, media_type),
                    media_type=media_type,
                    source_run_id=str(runtime_metadata.get("run_id") or "") or None,
                    source_message_id=str(runtime_metadata.get("run_id") or "") or stable_id,
                    source_node_id=str(runtime_metadata.get("node_id") or "") or None,
                )
                results.append(response.model_dump(mode="json"))
            except Exception as exc:
                results.append(
                    {
                        "status": "failed",
                        "producer_kind": "mcp",
                        "producer_artifact_id": stable_id,
                        "display_name": Path(filename).name[:255],
                        "error_code": str(
                            getattr(exc, "error_code", "output_registration_failed")
                        ),
                    }
                )
        return tuple(results)

    @staticmethod
    def _runtime_scope(
        runtime_metadata: dict[str, object],
    ) -> tuple[FilePurpose, str] | None:
        xpert_id = str(runtime_metadata.get("xpert_id") or "").strip()
        conversation_id = str(runtime_metadata.get("conversation_id") or "").strip()
        workflow_id = str(runtime_metadata.get("workflow_id") or "").strip()
        if xpert_id and conversation_id:
            return FilePurpose.AGENT, f"xpert:{xpert_id}:{conversation_id}"
        if workflow_id:
            return FilePurpose.WORKFLOW, f"workflow:{workflow_id}"
        return None

    def render_spec(
        self,
        payload: object,
        *,
        purpose: FilePurpose | str,
        scope_id: str,
        producer_kind: str,
        producer_artifact_id: str,
        source_run_id: str | None = None,
        source_message_id: str | None = None,
        source_node_id: str | None = None,
    ) -> FileOutputResponse:
        self._ensure_enabled()
        clean_purpose = FilePurpose(purpose)
        if clean_purpose not in _PURPOSES:
            raise FileAssetServiceError(422, "output_purpose_not_supported", "This module cannot publish output assets.")
        clean_scope = _identifier(scope_id, "scope_id")
        clean_producer = str(producer_kind or "").strip().lower()
        if clean_producer not in _PRODUCER_KINDS:
            raise FileAssetServiceError(422, "output_producer_not_supported", "The output producer is not allowlisted.")
        clean_producer_id = _identifier(producer_artifact_id, "producer_artifact_id")
        turn_key = _identifier(
            source_message_id or source_run_id or clean_producer_id,
            "source_message_id",
        )
        existing = self.file_service.repository.get_output_by_producer(
            self.file_service.tenant_id,
            producer_kind=clean_producer,
            producer_artifact_id=clean_producer_id,
        )
        if existing is not None:
            if existing.purpose != clean_purpose.value or existing.scope_id != clean_scope:
                raise FileAssetServiceError(404, "file_output_not_found", "The output file was not found in this scope.")
            return self._public(existing)
        try:
            spec = validate_render_spec(payload)
        except OutputRenderError as exc:
            raise FileAssetServiceError(exc.status_code, exc.error_code, exc.message) from exc
        definition = _FORMAT_MAP[spec.format_id]
        siblings = self.file_service.repository.list_output_records(
            self.file_service.tenant_id,
            purpose=clean_purpose,
            scope_id=clean_scope,
        )
        turn_items = tuple(
            item for item in siblings
            if item.source_message_id == turn_key and item.status not in {"deleted", "expired"}
        )
        if len(turn_items) >= MAX_OUTPUTS_PER_TURN:
            raise FileAssetServiceError(413, "output_count_limit_exceeded", "A turn can publish at most five output files.")
        spec_bytes = _canonical_spec_bytes(spec)
        output_id = f"output_{uuid.uuid4().hex}"
        try:
            record = self.file_service.repository.create_output_record(
                self.file_service.tenant_id,
                purpose=clean_purpose,
                scope_id=clean_scope,
                producer_kind=clean_producer,
                producer_artifact_id=clean_producer_id,
                display_name=spec.filename,
                format_id=definition.format_id,
                media_type=definition.media_types[0],
                preview_kind=definition.preview_kind,
                status="queued",
                source_run_id=source_run_id,
                source_message_id=turn_key,
                source_node_id=source_node_id,
                output_id=output_id,
            )
        except sqlite3.IntegrityError:
            current = self.file_service.repository.get_output_by_producer(
                self.file_service.tenant_id,
                producer_kind=clean_producer,
                producer_artifact_id=clean_producer_id,
            )
            if current is None or current.purpose != clean_purpose.value or current.scope_id != clean_scope:
                raise FileAssetServiceError(409, "output_registration_conflict", "The output registration conflicted with another request.")
            return self._public(current)
        retry_receipt = None
        task = None
        try:
            retry_receipt = self.file_service.blob_store.write_bytes(
                spec_bytes, max_bytes=MAX_OUTPUT_SPEC_BYTES
            )
            task = self.file_service.repository.create_output_task(
                self.file_service.tenant_id,
                record.id,
                status="queued",
                spec_storage_key=retry_receipt.storage_key,
                spec_sha256=retry_receipt.sha256,
                spec_byte_size=retry_receipt.byte_size,
                spec_expires_at=datetime.now(UTC) + timedelta(seconds=OUTPUT_RETRY_SPEC_TTL_SECONDS),
            )
            self.file_service.repository.update_output_task(
                self.file_service.tenant_id, task.id, status="running"
            )
            self.file_service.repository.update_output_record(
                self.file_service.tenant_id, record.id, status="running"
            )
            rendered = self.renderer.render(spec)
            result = self._persist_output_record(
                record,
                content=rendered.content,
                definition=definition,
                filename=rendered.filename,
                media_type=rendered.media_type,
                warnings=rendered.warnings,
            )
            self.file_service.blob_store.delete(retry_receipt.storage_key)
            self.file_service.repository.update_output_task(
                self.file_service.tenant_id,
                task.id,
                status="completed",
                clear_spec=True,
            )
            return result
        except OutputRenderError as exc:
            self.file_service.repository.update_output_record(
                self.file_service.tenant_id,
                record.id,
                status="failed",
                error_code=exc.error_code,
            )
            if task is not None:
                self.file_service.repository.update_output_task(
                    self.file_service.tenant_id,
                    task.id,
                    status="failed",
                    error_code=exc.error_code,
                )
            return self._public(
                self.file_service.repository.get_output_record(
                    self.file_service.tenant_id, record.id
                ) or record
            )
        except Exception as exc:
            if retry_receipt is not None and task is None:
                try:
                    self.file_service.blob_store.delete(retry_receipt.storage_key)
                except Exception:
                    pass
            self.file_service.repository.update_output_record(
                self.file_service.tenant_id,
                record.id,
                status="failed",
                error_code="output_render_failed",
            )
            if task is not None:
                self.file_service.repository.update_output_task(
                    self.file_service.tenant_id,
                    task.id,
                    status="failed",
                    error_code="output_render_failed",
                )
            if isinstance(exc, FileAssetServiceError):
                return self._public(
                    self.file_service.repository.get_output_record(
                        self.file_service.tenant_id, record.id
                    ) or record
                )
            return self._public(
                self.file_service.repository.get_output_record(
                    self.file_service.tenant_id, record.id
                ) or record
            )

    def _persist_output_record(
        self,
        record: FileOutputRecord,
        *,
        content: bytes,
        definition: OutputFormatDefinition,
        filename: str,
        media_type: str,
        warnings: tuple[str, ...],
    ) -> FileOutputResponse:
        _validated_definition, clean_name, clean_media = _validate_output(
            content,
            filename=filename,
            format_id=definition.format_id,
            media_type=media_type,
        )
        turn_items = tuple(
            item
            for item in self.file_service.repository.list_output_records(
                self.file_service.tenant_id,
                purpose=record.purpose,
                scope_id=record.scope_id,
            )
            if item.id != record.id
            and item.source_message_id == record.source_message_id
            and item.status not in {"deleted", "expired"}
        )
        if sum(item.byte_size for item in turn_items) + len(content) > MAX_OUTPUT_TOTAL_BYTES:
            self.file_service.repository.update_output_record(
                self.file_service.tenant_id,
                record.id,
                status="failed",
                error_code="output_total_size_exceeded",
            )
            raise FileAssetServiceError(
                413,
                "output_total_size_exceeded",
                "The output files for this turn exceed 100 MiB.",
            )
        receipt = None
        asset = None
        expires_at = datetime.now(UTC) + timedelta(seconds=OUTPUT_HARD_TTL_SECONDS)
        try:
            receipt = self.file_service.blob_store.write_bytes(
                content, max_bytes=MAX_OUTPUT_BYTES
            )
            asset = self.file_service.repository.create_asset(
                self.file_service.tenant_id,
                purpose=record.purpose,
                scope_id=record.scope_id,
                display_name=clean_name,
                format_id=definition.format_id,
                media_type=clean_media,
                storage_key=receipt.storage_key,
                sha256=receipt.sha256,
                byte_size=receipt.byte_size,
                status="ready",
                expires_at=expires_at,
                create_initial_binding=True,
            )
            updated = self.file_service.repository.update_output_record(
                self.file_service.tenant_id,
                record.id,
                status="completed",
                asset_id=asset.id,
                byte_size=receipt.byte_size,
                expires_at=expires_at,
                warnings=warnings,
            )
            assert updated is not None
            self.file_service.repository.record_audit_event(
                self.file_service.tenant_id,
                asset_id=asset.id,
                event_type="output_published",
                sha256=receipt.sha256,
                format_id=definition.format_id,
                byte_size=receipt.byte_size,
                status="ready",
            )
            return self._public(updated)
        except Exception as exc:
            if asset is not None:
                try:
                    self.file_service.delete_asset(
                        asset.id, purpose=record.purpose, scope_id=record.scope_id
                    )
                except Exception:
                    pass
            elif receipt is not None:
                try:
                    self.file_service.blob_store.delete(receipt.storage_key)
                except Exception:
                    pass
            self.file_service.repository.update_output_record(
                self.file_service.tenant_id,
                record.id,
                status="failed",
                error_code="output_persistence_failed",
            )
            if isinstance(exc, FileAssetServiceError):
                raise
            raise FileAssetServiceError(
                500,
                "output_persistence_failed",
                "The output file could not be stored safely.",
            ) from exc

    def list_outputs(self, *, purpose: FilePurpose | str, scope_id: str) -> FileOutputListResponse:
        self._ensure_enabled()
        self._maintenance()
        records = self.file_service.repository.list_output_records(
            self.file_service.tenant_id,
            purpose=FilePurpose(purpose),
            scope_id=_identifier(scope_id, "scope_id"),
        )
        items = tuple(self._public(item) for item in records)
        return FileOutputListResponse(items=items, total=len(items))

    def get_output(self, output_id: str, *, purpose: FilePurpose | str, scope_id: str) -> FileOutputResponse:
        return self._public(self._scoped(output_id, purpose=purpose, scope_id=scope_id))

    def preview_output(self, output_id: str, *, purpose: FilePurpose | str, scope_id: str) -> FileOutputPreviewResponse:
        record, content = self.read_output(output_id, purpose=purpose, scope_id=scope_id)
        warnings = _decode_warnings(record.warnings_json)
        if record.preview_kind == "text":
            try:
                text = content.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise FileAssetServiceError(422, "output_preview_invalid_utf8", "The output text is not valid UTF-8.") from exc
            truncated = len(text) > MAX_OUTPUT_SPEC_CHARS
            return FileOutputPreviewResponse(
                output_id=record.id,
                preview_kind="text",
                text=text[:MAX_OUTPUT_SPEC_CHARS],
                truncated=truncated,
                warnings=warnings,
            )
        if record.preview_kind == "document":
            asset = self.file_service.repository.get_asset(self.file_service.tenant_id, record.asset_id or "missing")
            if asset is None:
                raise FileAssetServiceError(410, "file_output_expired", "The output file expired.")
            try:
                document = parse_chat_document(
                    self.file_service.blob_store.storage_dir / asset.storage_key,
                    format_id=record.format_id,
                    title=record.display_name,
                )
            except LocalDocumentParseError as exc:
                raise FileAssetServiceError(exc.status_code, exc.error_code, exc.message) from exc
            return FileOutputPreviewResponse(
                output_id=record.id,
                preview_kind="document",
                document=document.model_dump(mode="json"),
                truncated=document.truncated,
                warnings=tuple(dict.fromkeys((*warnings, *document.warnings))),
            )
        return FileOutputPreviewResponse(
            output_id=record.id,
            preview_kind="none",
            warnings=warnings,
        )

    def read_output(self, output_id: str, *, purpose: FilePurpose | str, scope_id: str) -> tuple[FileOutputRecord, bytes]:
        record = self._scoped(output_id, purpose=purpose, scope_id=scope_id)
        if record.status != "completed" or not record.asset_id:
            raise FileAssetServiceError(409, "file_output_not_ready", "The output file is not ready.")
        asset = self.file_service.repository.get_bound_asset(
            self.file_service.tenant_id,
            record.asset_id,
            purpose=FilePurpose(purpose),
            scope_id=_identifier(scope_id, "scope_id"),
        )
        if asset is None or asset.status != "ready":
            raise FileAssetServiceError(410, "file_output_expired", "The output file expired.")
        try:
            content = self.file_service.blob_store.read_bytes(asset.storage_key)
        except Exception as exc:
            raise FileAssetServiceError(410, "file_output_unavailable", "The output file is unavailable.") from exc
        if len(content) != asset.byte_size or hashlib.sha256(content).hexdigest() != asset.sha256:
            raise FileAssetServiceError(409, "file_output_integrity_failed", "The output file failed its integrity check.")
        return record, content

    def confirm_reuse(
        self,
        output_id: str,
        *,
        purpose: FilePurpose | str,
        scope_id: str,
        handling: Literal["native", "extract"],
        target_id: str,
        gateway: Literal["default"],
    ) -> FileOutputReuseConfirmResponse:
        clean_target_id = _model_identifier(target_id)
        clean_purpose = FilePurpose(purpose)
        record, content = self.read_output(
            output_id, purpose=clean_purpose, scope_id=scope_id
        )
        if record.status != "completed" or not record.asset_id:
            raise FileAssetServiceError(409, "file_output_not_ready", "The output file is not ready.")
        definition = _FORMAT_MAP.get(record.format_id)
        if definition is None or definition.preview_kind == "none":
            raise FileAssetServiceError(
                422,
                "output_reuse_not_supported",
                "This output format is not reusable through an existing input path.",
            )
        media_reuse = definition.preview_kind in {"image", "audio", "video"}
        if media_reuse and clean_purpose is not FilePurpose.CHAT:
            raise FileAssetServiceError(
                422,
                "output_reuse_not_supported",
                "Media output reuse is only available through the existing Chat media inputs.",
            )
        if handling == "native" and record.format_id != "pdf":
            raise FileAssetServiceError(422, "native_file_handling_not_available", "Only PDF output can use native file handling.")
        if media_reuse and handling != "extract":
            raise FileAssetServiceError(
                422,
                "native_file_handling_not_available",
                "Media output reuse uses the existing Chat media input path.",
            )
        if clean_purpose is not FilePurpose.CHAT and handling != "extract":
            raise FileAssetServiceError(
                422,
                "native_file_handling_not_available",
                "Agent and Workflow output reuse uses local extraction only.",
            )

        reuse_asset_id: str | None = None
        input_confirmation_revision: int | None = None
        cleanup = None
        try:
            if media_reuse:
                reuse_asset_id = record.asset_id
            elif clean_purpose is FilePurpose.CHAT:
                receipt = self.file_service.blob_store.write_bytes(
                    content, max_bytes=MAX_OUTPUT_BYTES
                )
                reuse_asset = None
                try:
                    reuse_asset = self.file_service.repository.create_asset(
                        self.file_service.tenant_id,
                        purpose=FilePurpose.CHAT,
                        scope_id=record.scope_id,
                        display_name=record.display_name,
                        format_id=record.format_id,
                        media_type=record.media_type,
                        storage_key=receipt.storage_key,
                        sha256=receipt.sha256,
                        byte_size=receipt.byte_size,
                        status="ready",
                        expires_at=datetime.now(UTC)
                        + timedelta(seconds=OUTPUT_REUSE_INPUT_TTL_SECONDS),
                        create_initial_binding=True,
                    )
                    reuse_asset_id = reuse_asset.id
                    self.file_service.parse_asset(
                        reuse_asset.id,
                        purpose=FilePurpose.CHAT,
                        scope_id=record.scope_id,
                    )
                    input_confirmation_revision = self.file_service.confirm_chat_input(
                        reuse_asset.id,
                        scope_id=record.scope_id,
                        handling=handling,
                    )[0]
                except Exception:
                    if reuse_asset is not None:
                        self.file_service.delete_asset(
                            reuse_asset.id,
                            purpose=FilePurpose.CHAT,
                            scope_id=record.scope_id,
                        )
                    else:
                        self.file_service.blob_store.delete(receipt.storage_key)
                    raise
                cleanup = lambda: self.file_service.delete_asset(
                    reuse_asset_id or "",
                    purpose=FilePurpose.CHAT,
                    scope_id=record.scope_id,
                )
            elif clean_purpose is FilePurpose.WORKFLOW:
                expected_scope = f"workflow:{clean_target_id}"
                if record.scope_id != expected_scope:
                    raise FileAssetServiceError(
                        409,
                        "output_reuse_confirmation_required",
                        "The Workflow output scope or target changed.",
                    )
                uploaded = self.file_service.upload(
                    io.BytesIO(content),
                    purpose=FilePurpose.WORKFLOW,
                    scope_id=record.scope_id,
                    filename=record.display_name,
                    declared_media_type=record.media_type,
                )
                reuse_asset_id = uploaded.asset_id
                cleanup = lambda: self.file_service.delete_asset(
                    reuse_asset_id or "",
                    purpose=FilePurpose.WORKFLOW,
                    scope_id=record.scope_id,
                )
            else:
                prefix = f"xpert:{clean_target_id}:"
                if not record.scope_id.startswith(prefix):
                    raise FileAssetServiceError(
                        409,
                        "output_reuse_confirmation_required",
                        "The Agent output scope or target changed.",
                    )
                conversation_id = record.scope_id[len(prefix):]
                if not conversation_id:
                    raise FileAssetServiceError(
                        409,
                        "output_reuse_confirmation_required",
                        "The Agent conversation scope is invalid.",
                    )
                try:
                    from server.xperts.api import get_xpert_context_store
                except ModuleNotFoundError:
                    from xperts.api import get_xpert_context_store
                store = get_xpert_context_store()
                legacy_asset = store.add_file(
                    clean_target_id,
                    conversation_id,
                    filename=record.display_name,
                    content=content,
                )
                reuse_asset_id = legacy_asset.asset_id
                cleanup = lambda: store.purge_file(
                    clean_target_id,
                    conversation_id,
                    reuse_asset_id or "",
                )

            assert reuse_asset_id is not None
            digest = _reuse_digest(
                output_id=record.id,
                asset_id=reuse_asset_id,
                purpose=record.purpose,
                scope_id=record.scope_id,
                handling=handling,
                target_id=clean_target_id,
                gateway=gateway,
            )
            expires_at = datetime.now(UTC) + timedelta(
                seconds=OUTPUT_CONFIRMATION_TTL_SECONDS
            )
            confirmation = self.file_service.repository.confirm_output_reuse(
                self.file_service.tenant_id,
                record.id,
                purpose=record.purpose,
                scope_id=record.scope_id,
                handling=handling,
                target_id=clean_target_id,
                config_digest=digest,
                expires_at=expires_at,
            )
            if confirmation is None:
                raise FileAssetServiceError(
                    404,
                    "file_output_not_found",
                    "The output file was not found in this scope.",
                )
            return FileOutputReuseConfirmResponse(
                output_id=record.id,
                asset_id=reuse_asset_id,
                handling=handling,
                target_id=clean_target_id,
                confirmation_revision=input_confirmation_revision
                or confirmation.revision,
                output_confirmation_revision=confirmation.revision,
                expires_at=confirmation.expires_at,
                confirmed_at=confirmation.confirmed_at,
            )
        except Exception:
            if cleanup is not None:
                try:
                    cleanup()
                except Exception:
                    pass
            raise

    def resolve_media_reuse(
        self,
        output_id: str,
        *,
        asset_id: str,
        scope_id: str,
        target_id: str,
        gateway: Literal["default"],
        output_confirmation_revision: int,
        expected_kind: Literal["image", "audio", "video"],
    ) -> tuple[FileOutputRecord, bytes]:
        """Resolve confirmed media bytes before any model/provider call."""

        self.validate_reuse_confirmation(
            output_id,
            asset_id=asset_id,
            purpose=FilePurpose.CHAT,
            scope_id=scope_id,
            handling="extract",
            target_id=target_id,
            gateway=gateway,
            output_confirmation_revision=output_confirmation_revision,
        )
        record, content = self.read_output(
            output_id,
            purpose=FilePurpose.CHAT,
            scope_id=scope_id,
        )
        if record.asset_id != _identifier(asset_id, "asset_id"):
            raise FileAssetServiceError(
                409,
                "output_reuse_confirmation_required",
                "The output asset changed. Confirm reuse again.",
            )
        if record.preview_kind != expected_kind:
            raise FileAssetServiceError(
                422,
                "output_reuse_not_supported",
                "The output does not match the selected Chat media input.",
            )
        return record, content

    def validate_reuse_confirmation(
        self,
        output_id: str,
        *,
        asset_id: str,
        purpose: FilePurpose | str,
        scope_id: str,
        handling: Literal["native", "extract"],
        target_id: str,
        gateway: Literal["default"],
        output_confirmation_revision: int,
    ) -> None:
        clean_target_id = _model_identifier(target_id)
        record = self._scoped(output_id, purpose=purpose, scope_id=scope_id)
        confirmation = self.file_service.repository.get_output_confirmation(
            self.file_service.tenant_id,
            record.id,
            purpose=purpose,
            scope_id=scope_id,
        )
        expected = _reuse_digest(
            output_id=record.id,
            asset_id=_identifier(asset_id, "asset_id"),
            purpose=record.purpose,
            scope_id=record.scope_id,
            handling=handling,
            target_id=clean_target_id,
            gateway=gateway,
        )
        if (
            confirmation is None
            or confirmation.revision != int(output_confirmation_revision)
            or confirmation.handling != handling
            or confirmation.target_id != clean_target_id
            or confirmation.config_digest != expected
            or datetime.fromisoformat(confirmation.expires_at) <= datetime.now(UTC)
        ):
            raise FileAssetServiceError(
                409,
                "output_reuse_confirmation_required",
                "The output reuse target or handling changed. Confirm reuse again.",
            )

    def delete_output(self, output_id: str, *, purpose: FilePurpose | str, scope_id: str) -> bool:
        record = self._scoped(output_id, purpose=purpose, scope_id=scope_id, allow_terminal=True)
        if record.status == "deleted":
            return False
        if not record.asset_id:
            self._discard_retry_spec(record.id)
            self.file_service.repository.update_output_record(
                self.file_service.tenant_id, record.id, status="deleted"
            )
            return False
        if record.status in {"deleting", "expired"}:
            complete = self.file_service.asset_cleanup_complete(record.asset_id)
            self.file_service.repository.update_output_record(
                self.file_service.tenant_id,
                record.id,
                status="deleted" if complete else "deleting",
                error_code=None if complete else "cleanup_pending",
            )
            return not complete
        pending = self.file_service.delete_asset(
            record.asset_id,
            purpose=FilePurpose(purpose),
            scope_id=_identifier(scope_id, "scope_id"),
        )
        self.file_service.repository.update_output_record(
            self.file_service.tenant_id,
            record.id,
            status="deleting" if pending else "deleted",
            error_code="cleanup_pending" if pending else None,
        )
        return pending

    def retry_output(self, output_id: str, *, purpose: FilePurpose | str, scope_id: str) -> FileOutputResponse:
        record = self._scoped(output_id, purpose=purpose, scope_id=scope_id, allow_terminal=True)
        if record.status not in {"failed", "interrupted"}:
            raise FileAssetServiceError(409, "output_retry_not_available", "This output is not waiting for retry.")
        task = self.file_service.repository.latest_output_task(
            self.file_service.tenant_id, record.id
        )
        if (
            task is None
            or not task.spec_storage_key
            or not task.spec_sha256
            or task.spec_byte_size <= 0
            or not task.spec_expires_at
            or datetime.fromisoformat(task.spec_expires_at) <= datetime.now(UTC)
        ):
            raise FileAssetServiceError(409, "output_retry_source_expired", "The private retry source is unavailable. Ask the model to generate the file again.")
        try:
            raw = self.file_service.blob_store.read_bytes(task.spec_storage_key)
        except Exception as exc:
            raise FileAssetServiceError(409, "output_retry_source_expired", "The private retry source is unavailable. Ask the model to generate the file again.") from exc
        if (
            len(raw) != task.spec_byte_size
            or len(raw) > MAX_OUTPUT_SPEC_BYTES
            or hashlib.sha256(raw).hexdigest() != task.spec_sha256
        ):
            raise FileAssetServiceError(409, "output_retry_integrity_failed", "The private retry source failed its integrity check.")
        try:
            payload = json.loads(raw.decode("utf-8"))
            spec = validate_render_spec(payload)
        except (UnicodeError, json.JSONDecodeError, OutputRenderError) as exc:
            raise FileAssetServiceError(409, "output_retry_integrity_failed", "The private retry source failed its integrity check.") from exc
        if spec.format_id != record.format_id or spec.filename != record.display_name:
            raise FileAssetServiceError(409, "output_retry_integrity_failed", "The private retry source does not match this output.")
        self.file_service.repository.update_output_task(
            self.file_service.tenant_id, task.id, status="running"
        )
        self.file_service.repository.update_output_record(
            self.file_service.tenant_id, record.id, status="running"
        )
        try:
            rendered = self.renderer.render(spec)
            result = self._persist_output_record(
                record,
                content=rendered.content,
                definition=_FORMAT_MAP[spec.format_id],
                filename=rendered.filename,
                media_type=rendered.media_type,
                warnings=rendered.warnings,
            )
            self.file_service.blob_store.delete(task.spec_storage_key)
            self.file_service.repository.update_output_task(
                self.file_service.tenant_id,
                task.id,
                status="completed",
                clear_spec=True,
            )
            return result
        except OutputRenderError as exc:
            error_code = exc.error_code
        except Exception:
            error_code = "output_render_failed"
        self.file_service.repository.update_output_task(
            self.file_service.tenant_id,
            task.id,
            status="failed",
            error_code=error_code,
        )
        updated = self.file_service.repository.update_output_record(
            self.file_service.tenant_id,
            record.id,
            status="failed",
            error_code=error_code,
        )
        assert updated is not None
        return self._public(updated)

    def _scoped(
        self,
        output_id: str,
        *,
        purpose: FilePurpose | str,
        scope_id: str,
        allow_terminal: bool = False,
    ) -> FileOutputRecord:
        self._ensure_enabled()
        self._maintenance()
        record = self.file_service.repository.get_output_record(
            self.file_service.tenant_id, _identifier(output_id, "output_id")
        )
        if record is None or record.purpose != FilePurpose(purpose).value or record.scope_id != _identifier(scope_id, "scope_id"):
            raise FileAssetServiceError(404, "file_output_not_found", "The output file was not found in this scope.")
        if not allow_terminal and record.status in {"deleted", "expired"}:
            raise FileAssetServiceError(410, "file_output_expired", "The output file expired.")
        return record

    def _maintenance(self) -> None:
        for storage_key in self.file_service.repository.detach_expired_output_task_specs(
            self.file_service.tenant_id
        ):
            try:
                self.file_service.blob_store.delete(storage_key)
            except Exception:
                pass
        expired = self.file_service.repository.expire_due_output_records()
        for record in expired:
            if not record.asset_id:
                continue
            try:
                self.file_service.delete_asset(
                    record.asset_id,
                    purpose=record.purpose,
                    scope_id=record.scope_id,
                )
            except FileAssetServiceError as exc:
                if exc.error_code != "file_asset_not_found":
                    continue

    def _discard_retry_spec(self, output_id: str) -> None:
        task = self.file_service.repository.latest_output_task(
            self.file_service.tenant_id, output_id
        )
        if task is None or not task.spec_storage_key:
            return
        storage_key = task.spec_storage_key
        self.file_service.repository.update_output_task(
            self.file_service.tenant_id,
            task.id,
            status=task.status,
            error_code=task.error_code,
            clear_spec=True,
        )
        try:
            self.file_service.blob_store.delete(storage_key)
        except Exception:
            pass

    def _ensure_enabled(self) -> None:
        if not self.enabled:
            raise FileAssetServiceError(503, "file_output_assets_disabled", "Unified output assets are disabled.")
        if self.file_service.mode not in {"shadow", "native"}:
            raise FileAssetServiceError(503, "file_asset_store_disabled", "The unified file asset store is disabled.")
        _ = self.file_service.repository

    @staticmethod
    def _public(record: FileOutputRecord) -> FileOutputResponse:
        return FileOutputResponse(
            output_id=record.id,
            asset_id=record.asset_id,
            purpose=record.purpose,
            scope_id=record.scope_id,
            producer_kind=record.producer_kind,
            display_name=record.display_name,
            format=record.format_id,
            media_type=record.media_type,
            byte_size=record.byte_size,
            preview_kind=record.preview_kind,
            status=record.status,
            expires_at=record.expires_at,
            warnings=_decode_warnings(record.warnings_json),
            error_code=record.error_code,
            source_run_id=record.source_run_id,
            source_message_id=record.source_message_id,
            source_node_id=record.source_node_id,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


def _validate_output(
    content: bytes, *, filename: str, format_id: str, media_type: str
) -> tuple[OutputFormatDefinition, str, str]:
    if not content or len(content) > MAX_OUTPUT_BYTES:
        raise FileAssetServiceError(413, "output_size_limit_exceeded", "An output file must be non-empty and no larger than 50 MiB.")
    clean_name = str(filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not clean_name or len(clean_name) > 255 or any(ord(char) < 32 for char in clean_name):
        raise FileAssetServiceError(422, "output_filename_invalid", "The output filename is invalid.")
    suffix = Path(clean_name).suffix.lower()
    if suffix in _DANGEROUS_SUFFIXES:
        raise FileAssetServiceError(422, "output_executable_not_allowed", "Executable output files are not allowed.")
    definition = _FORMAT_MAP.get(str(format_id or "").strip().lower())
    clean_media = str(media_type or "").split(";", 1)[0].strip().lower()
    if definition is None:
        raise FileAssetServiceError(415, "output_format_not_supported", "The output format is not supported.")
    if suffix not in definition.extensions or clean_media not in definition.media_types:
        raise FileAssetServiceError(415, "output_type_mismatch", "The output filename, format, and media type do not match.")
    _verify_signature(content, definition.format_id)
    return definition, clean_name, clean_media


def _infer_output_format(filename: str, media_type: str) -> str:
    clean_name = str(filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    suffix = Path(clean_name).suffix.lower()
    clean_media = str(media_type or "").split(";", 1)[0].strip().lower()
    matches = tuple(
        item
        for item in _FORMATS
        if suffix in item.extensions and clean_media in item.media_types
    )
    if len(matches) != 1:
        raise FileAssetServiceError(
            415,
            "output_type_mismatch",
            "The output filename and media type do not identify one supported format.",
        )
    return matches[0].format_id


def _read_local_artifact(
    path: str | Path,
    *,
    trusted_root: str | Path,
    expected_size: int | None,
    expected_sha256: str | None,
) -> bytes:
    try:
        root = Path(trusted_root).resolve(strict=True)
        candidate = Path(path)
        relative = candidate.relative_to(root)
    except (OSError, ValueError) as exc:
        raise FileAssetServiceError(
            422,
            "output_source_scope_invalid",
            "The published artifact is outside its trusted storage scope.",
        ) from exc
    current = root
    for part in relative.parts:
        current = current / part
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise FileAssetServiceError(
                410,
                "output_source_unavailable",
                "The published artifact is unavailable.",
            ) from exc
        if stat.S_ISLNK(metadata.st_mode) or _has_reparse_attribute(metadata):
            raise FileAssetServiceError(
                422,
                "output_source_link_denied",
                "Linked output artifacts are not allowed.",
            )
    resolved = candidate.resolve(strict=True)
    if resolved != root and root not in resolved.parents:
        raise FileAssetServiceError(
            422,
            "output_source_scope_invalid",
            "The published artifact is outside its trusted storage scope.",
        )
    try:
        with resolved.open("rb") as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise FileAssetServiceError(
                    422,
                    "output_source_not_regular",
                    "The published artifact is not a regular file.",
                )
            if before.st_size < 1 or before.st_size > MAX_OUTPUT_BYTES:
                raise FileAssetServiceError(
                    413,
                    "output_size_limit_exceeded",
                    "An output file must be non-empty and no larger than 50 MiB.",
                )
            content = handle.read(MAX_OUTPUT_BYTES + 1)
            after = os.fstat(handle.fileno())
    except FileAssetServiceError:
        raise
    except OSError as exc:
        raise FileAssetServiceError(
            410,
            "output_source_unavailable",
            "The published artifact is unavailable.",
        ) from exc
    if len(content) > MAX_OUTPUT_BYTES:
        raise FileAssetServiceError(
            413,
            "output_size_limit_exceeded",
            "An output file must be non-empty and no larger than 50 MiB.",
        )
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity_before != identity_after or len(content) != before.st_size:
        raise FileAssetServiceError(
            409,
            "output_source_changed",
            "The published artifact changed while it was being registered.",
        )
    digest = hashlib.sha256(content).hexdigest()
    if expected_size is not None and int(expected_size) != len(content):
        raise FileAssetServiceError(
            409,
            "output_source_changed",
            "The published artifact size no longer matches its receipt.",
        )
    clean_expected_digest = str(expected_sha256 or "").strip().lower()
    if clean_expected_digest and (
        re.fullmatch(r"[0-9a-f]{64}", clean_expected_digest) is None
        or clean_expected_digest != digest
    ):
        raise FileAssetServiceError(
            409,
            "output_source_changed",
            "The published artifact hash no longer matches its receipt.",
        )
    return content


def _has_reparse_attribute(metadata: os.stat_result) -> bool:
    attributes = int(getattr(metadata, "st_file_attributes", 0) or 0)
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return bool(attributes & reparse_flag)


def _verify_signature(content: bytes, format_id: str) -> None:
    if format_id == "pdf" and not content.startswith(b"%PDF-"):
        raise FileAssetServiceError(422, "output_signature_invalid", "The PDF signature is invalid.")
    if format_id in {"docx", "xlsx", "pptx"} and not content.startswith(b"PK\x03\x04"):
        raise FileAssetServiceError(422, "output_signature_invalid", "The Office container signature is invalid.")
    valid = True
    if format_id == "png":
        valid = content.startswith(b"\x89PNG\r\n\x1a\n")
    elif format_id == "jpeg":
        valid = content.startswith(b"\xff\xd8\xff")
    elif format_id == "webp":
        valid = (
            len(content) >= 12
            and content.startswith(b"RIFF")
            and content[8:12] == b"WEBP"
        )
    elif format_id == "wav":
        valid = (
            len(content) >= 12
            and content.startswith(b"RIFF")
            and content[8:12] == b"WAVE"
        )
    elif format_id == "mp3":
        valid = content.startswith(b"ID3") or (
            len(content) >= 2
            and content[0] == 0xFF
            and content[1] & 0xE0 == 0xE0
        )
    elif format_id == "flac":
        valid = content.startswith(b"fLaC")
    elif format_id == "ogg":
        valid = content.startswith(b"OggS")
    elif format_id in {"m4a", "mp4", "mov"}:
        valid = len(content) >= 12 and content[4:8] == b"ftyp"
    elif format_id in {"audio_webm", "video_webm"}:
        valid = content.startswith(b"\x1a\x45\xdf\xa3")
    elif format_id == "mpeg":
        valid = (
            content.startswith((b"\x00\x00\x01\xba", b"\x00\x00\x01\xb3"))
            or (len(content) >= 188 and content[0] == 0x47)
        )
    if not valid:
        raise FileAssetServiceError(
            422,
            "output_signature_invalid",
            "The media signature is invalid.",
        )


def _decode_warnings(value: str) -> tuple[str, ...]:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(str(item)[:500] for item in parsed[:20] if str(item).strip())


def _canonical_spec_bytes(spec: OutputRenderSpec) -> bytes:
    try:
        encoded = json.dumps(
            spec.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise FileAssetServiceError(
            422, "output_spec_invalid", "The output file specification is invalid."
        ) from exc
    if len(encoded) > MAX_OUTPUT_SPEC_BYTES:
        raise FileAssetServiceError(
            413, "output_spec_too_large", "The output file specification exceeds 2 MiB."
        )
    return encoded


def _reuse_digest(
    *,
    output_id: str,
    asset_id: str,
    purpose: str,
    scope_id: str,
    handling: str,
    target_id: str,
    gateway: str,
) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "output_id": output_id,
                "asset_id": asset_id,
                "purpose": purpose,
                "scope_id": scope_id,
                "handling": handling,
                "target_id": target_id,
                "gateway": gateway,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _identifier(value: object, field: str) -> str:
    clean = str(value or "").strip()
    if not clean or len(clean) > 256 or not re.fullmatch(r"[A-Za-z0-9._:-]+", clean):
        raise FileAssetServiceError(422, f"{field}_invalid", f"{field} is invalid.")
    return clean


def _model_identifier(value: object) -> str:
    clean = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._:/-]{1,256}", clean):
        raise FileAssetServiceError(
            422, "target_id_invalid", "target_id is invalid."
        )
    return clean


def _env_enabled(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "on"}


_default_output_lock = threading.Lock()
_default_output_key: tuple[str, str, str, str] | None = None
_default_output_service: FileOutputService | None = None


def get_file_output_service() -> FileOutputService:
    global _default_output_key, _default_output_service
    file_service = get_file_asset_service()
    key = (
        file_service.mode,
        str(file_service.storage_dir),
        file_service.tenant_id,
        os.getenv("FILE_OUTPUT_ASSETS_ENABLED", "false"),
    )
    if _default_output_service is not None and _default_output_key == key:
        return _default_output_service
    with _default_output_lock:
        if _default_output_service is None or _default_output_key != key:
            _default_output_service = FileOutputService(file_service)
            _default_output_key = key
    return _default_output_service
