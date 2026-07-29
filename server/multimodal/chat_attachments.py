from __future__ import annotations

import os
import secrets
import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from .stt import MAX_AUDIO_BYTES, MultimodalServiceError, TranscriptionService
from .video_analysis import (
    MAX_VIDEO_BYTES,
    VIDEO_FORMATS,
    VideoAnalysisService,
)


ATTACHMENT_TTL_SECONDS = 30 * 60
RETRY_TTL_SECONDS = 10 * 60
AttachmentKind = Literal["audio", "video"]


class ChatAttachmentResponse(BaseModel):
    attachment_id: str
    kind: AttachmentKind
    mime_type: str
    format: str
    bytes: int
    expires_at: str


class ChatAttachmentDeleteResponse(BaseModel):
    attachment_id: str
    deleted: bool


@dataclass(frozen=True)
class ClaimedChatAttachment:
    attachment_id: str
    kind: AttachmentKind
    mime_type: str
    format: str
    content: bytes
    expires_at: str


@dataclass
class _StoredAttachment:
    attachment_id: str
    tenant_id: str
    kind: AttachmentKind
    mime_type: str
    media_format: str
    size_bytes: int
    path: Path
    expires_at_epoch: float
    state: Literal["pending", "in_use"] = "pending"


class ChatAttachmentStore:
    def __init__(
        self,
        *,
        tenant_id: str = "local",
        root: Path | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.tenant_id = str(tenant_id or "local")
        self._clock = clock
        self._records: dict[str, _StoredAttachment] = {}
        self._lock = threading.RLock()
        self._owns_root = root is None
        if root is None:
            self._cleanup_stale_roots()
            self.root = Path(
                tempfile.mkdtemp(prefix="modelmirror-chat-media-")
            ).resolve()
        else:
            self.root = Path(root).resolve()
            self.root.mkdir(parents=True, exist_ok=True)

    def create(
        self,
        *,
        kind: AttachmentKind,
        filename: str,
        content_type: str | None,
        content: bytes,
        tenant_id: str | None = None,
    ) -> ChatAttachmentResponse:
        self._ensure_enabled(kind)
        owner = str(tenant_id or self.tenant_id)
        self.cleanup_expired()
        media_format, mime_type = self._validate(
            kind=kind,
            filename=filename,
            content_type=content_type,
            content=content,
        )
        attachment_id = f"att_{secrets.token_urlsafe(24)}"
        path = self._path(attachment_id)
        path.write_bytes(content)
        expires_at_epoch = self._clock() + ATTACHMENT_TTL_SECONDS
        record = _StoredAttachment(
            attachment_id=attachment_id,
            tenant_id=owner,
            kind=kind,
            mime_type=mime_type,
            media_format=media_format,
            size_bytes=len(content),
            path=path,
            expires_at_epoch=expires_at_epoch,
        )
        with self._lock:
            self._records[attachment_id] = record
        return self._response(record)

    def claim(
        self,
        attachment_id: str,
        *,
        tenant_id: str | None = None,
        expected_kind: AttachmentKind | None = None,
    ) -> ClaimedChatAttachment:
        owner = str(tenant_id or self.tenant_id)
        with self._lock:
            record = self._get_record(attachment_id, owner)
            if expected_kind is not None and record.kind != expected_kind:
                raise MultimodalServiceError(
                    "attachment_kind_mismatch",
                    "附件类型与本次请求不一致，请重新选择文件。",
                    status_code=422,
                )
            if record.state != "pending":
                raise MultimodalServiceError(
                    "attachment_already_in_use",
                    "该附件正在处理中，请等待当前请求完成。",
                    status_code=409,
                )
            record.state = "in_use"
            try:
                content = record.path.read_bytes()
            except OSError as exc:
                record.state = "pending"
                raise MultimodalServiceError(
                    "attachment_unavailable",
                    "临时附件已不可用，请重新上传。",
                    status_code=410,
                ) from exc
            return ClaimedChatAttachment(
                attachment_id=record.attachment_id,
                kind=record.kind,
                mime_type=record.mime_type,
                format=record.media_format,
                content=content,
                expires_at=self._iso(record.expires_at_epoch),
            )

    def release_for_retry(
        self,
        attachment_id: str,
        *,
        tenant_id: str | None = None,
    ) -> None:
        owner = str(tenant_id or self.tenant_id)
        with self._lock:
            record = self._get_record(attachment_id, owner)
            record.state = "pending"
            record.expires_at_epoch = min(
                record.expires_at_epoch,
                self._clock() + RETRY_TTL_SECONDS,
            )

    def complete(
        self,
        attachment_id: str,
        *,
        tenant_id: str | None = None,
    ) -> None:
        self.delete(attachment_id, tenant_id=tenant_id)

    def delete(
        self,
        attachment_id: str,
        *,
        tenant_id: str | None = None,
    ) -> ChatAttachmentDeleteResponse:
        owner = str(tenant_id or self.tenant_id)
        with self._lock:
            record = self._get_record(attachment_id, owner)
            self._records.pop(record.attachment_id, None)
            self._unlink(record.path)
        return ChatAttachmentDeleteResponse(
            attachment_id=record.attachment_id,
            deleted=True,
        )

    def cleanup_expired(self) -> int:
        now = self._clock()
        removed = 0
        with self._lock:
            expired = [
                record
                for record in self._records.values()
                if record.expires_at_epoch <= now
            ]
            for record in expired:
                self._records.pop(record.attachment_id, None)
                self._unlink(record.path)
                removed += 1
        return removed

    def close(self) -> None:
        with self._lock:
            records = list(self._records.values())
            self._records.clear()
            for record in records:
                self._unlink(record.path)
        if self._owns_root:
            shutil.rmtree(self.root, ignore_errors=True)

    def _get_record(
        self,
        attachment_id: str,
        tenant_id: str,
    ) -> _StoredAttachment:
        self.cleanup_expired()
        record = self._records.get(str(attachment_id or ""))
        if record is None or record.tenant_id != tenant_id:
            raise MultimodalServiceError(
                "attachment_not_found",
                "临时附件不存在或已过期，请重新上传。",
                status_code=404,
            )
        return record

    def _path(self, attachment_id: str) -> Path:
        path = (self.root / attachment_id).resolve()
        if path.parent != self.root:
            raise MultimodalServiceError(
                "invalid_attachment_id",
                "附件标识无效，请重新上传。",
                status_code=422,
            )
        return path

    @staticmethod
    def _validate(
        *,
        kind: AttachmentKind,
        filename: str,
        content_type: str | None,
        content: bytes,
    ) -> tuple[str, str]:
        if kind == "audio":
            _, media_format = TranscriptionService._validate_audio(
                filename,
                content_type,
                content,
            )
            normalized = ChatAttachmentStore._normalized_type(content_type)
            mime_type = (
                ALLOWED_AUDIO_MIME[media_format]
                if normalized == "application/octet-stream"
                else normalized
            )
            return media_format, mime_type

        if not content:
            raise MultimodalServiceError(
                "empty_video",
                "视频文件为空，请重新选择文件。",
                status_code=422,
            )
        if len(content) > MAX_VIDEO_BYTES:
            raise MultimodalServiceError(
                "video_too_large",
                "视频文件不能超过 20 MiB，请压缩或缩短后重试。",
                status_code=413,
            )
        media_format = Path(filename or "").suffix.lower().lstrip(".")
        profile = VIDEO_FORMATS.get(media_format)
        if profile is None:
            raise MultimodalServiceError(
                "unsupported_video_format",
                "仅支持 MP4、MPEG、MOV 和 WebM 视频。",
                status_code=415,
            )
        canonical_mime, allowed_mimes = profile
        normalized = ChatAttachmentStore._normalized_type(content_type)
        if (
            normalized not in allowed_mimes
            or not VideoAnalysisService._magic_matches(
                media_format,
                content,
            )
        ):
            raise MultimodalServiceError(
                "invalid_video_file",
                "文件内容与视频格式不匹配，请选择有效的视频文件。",
                status_code=422,
            )
        return media_format, (
            canonical_mime
            if normalized == "application/octet-stream"
            else normalized
        )

    @staticmethod
    def _normalized_type(content_type: str | None) -> str:
        return (
            str(content_type or "application/octet-stream")
            .split(";", 1)[0]
            .strip()
            .lower()
        )

    @staticmethod
    def _response(record: _StoredAttachment) -> ChatAttachmentResponse:
        return ChatAttachmentResponse(
            attachment_id=record.attachment_id,
            kind=record.kind,
            mime_type=record.mime_type,
            format=record.media_format,
            bytes=record.size_bytes,
            expires_at=ChatAttachmentStore._iso(record.expires_at_epoch),
        )

    @staticmethod
    def _iso(epoch: float) -> str:
        return datetime.fromtimestamp(epoch, tz=UTC).isoformat()

    @staticmethod
    def _unlink(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _enabled(name: str) -> bool:
        return os.getenv(name, "false").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    def _ensure_enabled(self, kind: AttachmentKind) -> None:
        flag = (
            "MULTIMODAL_CHAT_AUDIO_ENABLED"
            if kind == "audio"
            else "MULTIMODAL_CHAT_VIDEO_ENABLED"
        )
        if self._enabled(flag):
            return
        label = "音频" if kind == "audio" else "视频"
        raise MultimodalServiceError(
            f"chat_{kind}_disabled",
            f"Chat {label}附件当前未启用。",
            status_code=503,
        )

    @staticmethod
    def _cleanup_stale_roots() -> None:
        parent = Path(tempfile.gettempdir()).resolve()
        cutoff = time.time() - ATTACHMENT_TTL_SECONDS
        for path in parent.glob("modelmirror-chat-media-*"):
            try:
                resolved = path.resolve()
                if (
                    resolved.parent == parent
                    and resolved.is_dir()
                    and resolved.stat().st_mtime <= cutoff
                ):
                    shutil.rmtree(resolved, ignore_errors=True)
            except OSError:
                continue


ALLOWED_AUDIO_MIME = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "flac": "audio/flac",
    "m4a": "audio/mp4",
    "ogg": "audio/ogg",
    "webm": "audio/webm",
    "aac": "audio/aac",
}
