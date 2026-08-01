from __future__ import annotations

import json
import mimetypes
import os
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

try:
    from server.rag.document_parser import DocumentParseError, parse_document, supported_extensions
except ModuleNotFoundError:
    from rag.document_parser import DocumentParseError, parse_document, supported_extensions

from .file_memory import (
    FileMemoryConflictError,
    FileMemoryError,
    FileMemoryNotFoundError,
    FileMemoryRecord,
    XpertFileMemoryStore,
)


MemoryScope = Literal["conversation", "xpert"]
CandidateStatus = Literal["pending", "approved", "rejected", "conflict"]
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_FILES_PER_CONVERSATION = 20
MAX_SELECTED_FILES = 5
MAX_EXTRACTED_CHARS = 500_000
MAX_CONVERSATION_MESSAGES = 200
MAX_TOOL_MEMORY_CHARS = 8_000
MAX_TOOL_MEMORIES_PER_CONVERSATION = 100


class XpertContextError(Exception):
    """Base error for Xpert conversation, file, and memory storage."""


class XpertContextNotFoundError(XpertContextError):
    """Raised when an Xpert context resource does not exist."""


class XpertContextValidationError(XpertContextError):
    """Raised when an Xpert context request is invalid."""


class XpertContextConflictError(XpertContextError):
    """Raised when a context resource revision has changed."""


@dataclass(slots=True)
class XpertConversationMessage:
    message_id: str
    role: Literal["user", "assistant"]
    content: str
    version: int | None = None
    suggestions: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class XpertConversation:
    conversation_id: str
    xpert_id: str
    title: str
    messages: list[XpertConversationMessage] = field(default_factory=list)
    file_asset_ids: list[str] = field(default_factory=list)
    summary: str = ""
    summary_through_message_id: str | None = None
    summary_revision: int = 0
    summary_model_id: str | None = None
    summary_updated_at: float | None = None
    memory_detail_chars_used: int = 0
    archived: bool = False
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class XpertFileAsset:
    asset_id: str
    artifact_id: str
    xpert_id: str
    conversation_id: str
    filename: str
    size_bytes: int
    extension: str
    mime_type: str
    status: Literal["ready", "archived"] = "ready"
    character_count: int = 0
    extracted_truncated: bool = False
    storage_key: str = ""
    text_key: str = ""
    created_at: float = field(default_factory=time.time)
    archived_at: float | None = None


@dataclass(slots=True)
class XpertMemoryRecord:
    memory_id: str
    xpert_id: str
    scope: MemoryScope
    content: str
    conversation_id: str | None = None
    tags: list[str] = field(default_factory=list)
    source_type: str = "user"
    source_id: str | None = None
    status: Literal["active", "archived", "conflict"] = "active"
    memory_type: Literal["user", "feedback", "project", "reference"] = "project"
    title: str = ""
    summary: str = ""
    revision: int = 1
    canonical_ref: str = ""
    source_refs: list[str] = field(default_factory=list)
    confidence: float | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class MemoryWriteCandidate:
    candidate_id: str
    xpert_id: str
    scope: MemoryScope
    content: str
    conversation_id: str | None = None
    tags: list[str] = field(default_factory=list)
    source_run_id: str | None = None
    status: CandidateStatus = "pending"
    created_at: float = field(default_factory=time.time)
    decided_at: float | None = None
    memory_id: str | None = None
    revision: int = 1
    action: Literal["create", "update"] = "create"
    memory_type: Literal["user", "feedback", "project", "reference"] = "project"
    title: str = ""
    summary: str = ""
    target_memory_id: str | None = None
    base_revision: int | None = None
    source_refs: list[str] = field(default_factory=list)
    confidence: float | None = None
    error: str | None = None


@dataclass(slots=True)
class XpertToolMemoryRecord:
    memory_id: str
    xpert_id: str
    conversation_id: str
    tool_name: str
    provider: str
    summary: str
    parameter_summary: dict[str, Any] = field(default_factory=dict)
    source_run_id: str | None = None
    status: Literal["active", "archived"] = "active"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class XpertContextStore:
    """Atomic file-backed context store for published Xpert conversations."""

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        fallback = os.getenv("AGENT_TASK_STORAGE_DIR", "").strip()
        package_dir = Path(__file__).resolve().parent
        self.storage_dir = Path(
            storage_dir
            or os.getenv("XPERT_CONTEXT_STORAGE_DIR", "").strip()
            or fallback
            or package_dir / "storage"
        )
        self.context_dir = self.storage_dir / "xpert_context"
        self.files_dir = self.context_dir / "files"
        self.snapshot_path = self.context_dir / "context.json"
        self.file_memory_store = XpertFileMemoryStore(self.context_dir / "file_memory")
        self._lock = threading.RLock()
        self._conversations: dict[str, XpertConversation] = {}
        self._assets: dict[str, XpertFileAsset] = {}
        self._memories: dict[str, XpertMemoryRecord] = {}
        self._candidates: dict[str, MemoryWriteCandidate] = {}
        self._tool_memories: dict[str, XpertToolMemoryRecord] = {}
        self._load()

    def create_conversation(self, xpert_id: str, *, title: str = "") -> XpertConversation:
        conversation = XpertConversation(
            conversation_id=str(uuid.uuid4()),
            xpert_id=self._required_text(xpert_id, "xpert_id", 200),
            title=(title.strip()[:120] or "New conversation"),
        )
        with self._lock:
            self._conversations[conversation.conversation_id] = conversation
            self._persist_unlocked()
        return conversation

    def get_conversation(self, xpert_id: str, conversation_id: str) -> XpertConversation:
        with self._lock:
            conversation = self._conversations.get(conversation_id)
            if conversation is None or conversation.xpert_id != xpert_id:
                raise XpertContextNotFoundError("Xpert conversation not found.")
            return conversation

    def list_conversations(self, xpert_id: str, *, limit: int = 50) -> list[XpertConversation]:
        with self._lock:
            items = [
                item
                for item in self._conversations.values()
                if item.xpert_id == xpert_id and not item.archived
            ]
        items.sort(key=lambda item: item.updated_at, reverse=True)
        return items[: max(1, min(limit, 200))]

    def append_message(
        self,
        xpert_id: str,
        conversation_id: str,
        *,
        role: Literal["user", "assistant"],
        content: str,
        version: int | None = None,
        suggestions: list[str] | None = None,
    ) -> XpertConversationMessage:
        clean_content = self._required_text(content, "content", 100_000)
        message = XpertConversationMessage(
            message_id=str(uuid.uuid4()),
            role=role,
            content=clean_content,
            version=version,
            suggestions=self._clean_values(
                suggestions,
                limit=6,
                max_length=500,
            ),
        )
        with self._lock:
            conversation = self._require_conversation_unlocked(xpert_id, conversation_id)
            conversation.messages.append(message)
            if len(conversation.messages) > MAX_CONVERSATION_MESSAGES:
                conversation.messages = conversation.messages[-MAX_CONVERSATION_MESSAGES:]
            if conversation.title == "New conversation" and role == "user":
                conversation.title = clean_content.replace("\n", " ")[:60]
            conversation.updated_at = time.time()
            self._persist_unlocked()
        return message

    def update_conversation_title(
        self,
        xpert_id: str,
        conversation_id: str,
        *,
        title: str,
    ) -> XpertConversation:
        clean_title = self._required_text(title, "title", 120)
        with self._lock:
            conversation = self._require_conversation_unlocked(xpert_id, conversation_id)
            conversation.title = clean_title
            conversation.updated_at = time.time()
            self._persist_unlocked()
            return conversation

    def update_conversation_summary(
        self,
        xpert_id: str,
        conversation_id: str,
        *,
        summary: str,
        model_id: str,
        through_message_id: str | None,
    ) -> XpertConversation:
        clean_summary = self._required_text(summary, "summary", 40_000)
        clean_model_id = self._required_text(model_id, "model_id", 300)
        with self._lock:
            conversation = self._require_conversation_unlocked(xpert_id, conversation_id)
            if through_message_id and not any(
                message.message_id == through_message_id
                for message in conversation.messages
            ):
                raise XpertContextValidationError(
                    "Conversation summary boundary message was not found."
                )
            conversation.summary = clean_summary
            conversation.summary_through_message_id = through_message_id
            conversation.summary_revision += 1
            conversation.summary_model_id = clean_model_id
            conversation.summary_updated_at = time.time()
            conversation.updated_at = time.time()
            self._persist_unlocked()
            return conversation

    def claim_memory_detail_budget(
        self,
        xpert_id: str,
        conversation_id: str,
        *,
        requested_chars: int,
        session_limit: int,
    ) -> int:
        """Atomically reserve durable-memory detail characters for a conversation."""

        requested = max(0, int(requested_chars))
        limit = max(0, int(session_limit))
        with self._lock:
            conversation = self._require_conversation_unlocked(xpert_id, conversation_id)
            remaining = max(0, limit - int(conversation.memory_detail_chars_used or 0))
            granted = min(requested, remaining)
            if granted:
                conversation.memory_detail_chars_used += granted
                conversation.updated_at = time.time()
                self._persist_unlocked()
            return granted

    def add_file(
        self,
        xpert_id: str,
        conversation_id: str,
        *,
        filename: str,
        content: bytes,
    ) -> XpertFileAsset:
        clean_filename = self._safe_filename(filename)
        extension = Path(clean_filename).suffix.lower()
        if extension not in supported_extensions():
            raise XpertContextValidationError(
                f"Unsupported file type: {extension or '<none>'}."
            )
        if not content:
            raise XpertContextValidationError("The uploaded file is empty.")
        if len(content) > MAX_FILE_BYTES:
            raise XpertContextValidationError("The uploaded file exceeds the 10 MB limit.")

        with self._lock:
            conversation = self._require_conversation_unlocked(xpert_id, conversation_id)
            active_count = sum(
                1
                for asset_id in conversation.file_asset_ids
                if (asset := self._assets.get(asset_id)) is not None and asset.status == "ready"
            )
            if active_count >= MAX_FILES_PER_CONVERSATION:
                raise XpertContextValidationError(
                    "A conversation can contain at most 20 active files."
                )

            asset_id = str(uuid.uuid4())
            artifact_id = str(uuid.uuid4())
            relative_dir = Path(xpert_id) / conversation_id
            raw_key = relative_dir / f"{asset_id}{extension}"
            text_key = relative_dir / f"{artifact_id}.txt"
            raw_path = self.files_dir / raw_key
            text_path = self.files_dir / text_key
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = raw_path.with_suffix(raw_path.suffix + ".tmp")
            temporary.write_bytes(content)
            os.replace(temporary, raw_path)
            try:
                extracted = parse_document(raw_path, clean_filename)
            except DocumentParseError as exc:
                raw_path.unlink(missing_ok=True)
                raise XpertContextValidationError(str(exc)) from exc
            truncated = len(extracted) > MAX_EXTRACTED_CHARS
            stored_text = extracted[:MAX_EXTRACTED_CHARS]
            text_path.write_text(stored_text, encoding="utf-8")
            asset = XpertFileAsset(
                asset_id=asset_id,
                artifact_id=artifact_id,
                xpert_id=xpert_id,
                conversation_id=conversation_id,
                filename=clean_filename,
                size_bytes=len(content),
                extension=extension,
                mime_type=mimetypes.guess_type(clean_filename)[0] or "application/octet-stream",
                character_count=len(stored_text),
                extracted_truncated=truncated,
                storage_key=raw_key.as_posix(),
                text_key=text_key.as_posix(),
            )
            self._assets[asset_id] = asset
            conversation.file_asset_ids.append(asset_id)
            conversation.updated_at = time.time()
            self._persist_unlocked()
            return asset

    def get_file(
        self,
        xpert_id: str,
        asset_id: str,
        *,
        conversation_id: str | None = None,
        include_archived: bool = False,
    ) -> XpertFileAsset:
        with self._lock:
            asset = self._assets.get(asset_id)
            if asset is None or asset.xpert_id != xpert_id:
                raise XpertContextNotFoundError("Xpert file asset not found.")
            if conversation_id is not None and asset.conversation_id != conversation_id:
                raise XpertContextNotFoundError("Xpert file asset not found.")
            if asset.status == "archived" and not include_archived:
                raise XpertContextNotFoundError("Xpert file asset is archived.")
            return asset

    def list_files(
        self,
        xpert_id: str,
        conversation_id: str,
        *,
        include_archived: bool = False,
    ) -> list[XpertFileAsset]:
        with self._lock:
            conversation = self._require_conversation_unlocked(xpert_id, conversation_id)
            items = [self._assets[item] for item in conversation.file_asset_ids if item in self._assets]
        if not include_archived:
            items = [item for item in items if item.status == "ready"]
        items.sort(key=lambda item: item.created_at, reverse=True)
        return items

    def archive_file(self, xpert_id: str, conversation_id: str, asset_id: str) -> XpertFileAsset:
        with self._lock:
            asset = self.get_file(xpert_id, asset_id, conversation_id=conversation_id)
            asset.status = "archived"
            asset.archived_at = time.time()
            self._persist_unlocked()
            return asset

    def read_file_text(self, asset: XpertFileAsset) -> str:
        path = self.files_dir / asset.text_key
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise XpertContextError("Xpert file artifact is unavailable.") from exc

    def read_file_bytes(self, asset: XpertFileAsset) -> bytes:
        """Read the original attachment for explicit Sandbox staging."""

        path = (self.files_dir / asset.storage_key).resolve(strict=False)
        root = self.files_dir.resolve()
        if root not in path.parents or path.is_symlink():
            raise XpertContextError("Xpert file asset path is unsafe.")
        try:
            return path.read_bytes()
        except OSError as exc:
            raise XpertContextError("Xpert file asset is unavailable.") from exc

    def build_file_context(
        self,
        xpert_id: str,
        asset_ids: list[str],
        *,
        conversation_id: str | None = None,
        include_archived: bool = False,
        per_file_chars: int = 10_000,
        total_chars: int = 30_000,
    ) -> tuple[str, list[XpertFileAsset]]:
        unique_ids = list(dict.fromkeys(asset_ids))
        if len(unique_ids) > MAX_SELECTED_FILES:
            raise XpertContextValidationError("At most 5 files can be selected per run.")
        sections: list[str] = []
        selected: list[XpertFileAsset] = []
        used = 0
        for asset_id in unique_ids:
            asset = self.get_file(
                xpert_id,
                asset_id,
                conversation_id=conversation_id,
                include_archived=include_archived,
            )
            remaining = total_chars - used
            if remaining <= 0:
                break
            text = self.read_file_text(asset)
            excerpt = text[: min(per_file_chars, remaining)]
            truncated = len(excerpt) < len(text)
            header = (
                f"[File: {asset.filename}; asset_id={asset.asset_id}; "
                f"artifact_id={asset.artifact_id}; truncated={str(truncated).lower()}]"
            )
            section = f"{header}\n{excerpt}"
            if len(section) > remaining:
                section = section[:remaining]
            sections.append(section)
            selected.append(asset)
            used += len(section)
        return "\n\n".join(sections), selected

    def create_memory(
        self,
        xpert_id: str,
        *,
        content: str,
        scope: MemoryScope = "xpert",
        conversation_id: str | None = None,
        tags: list[str] | None = None,
        source_type: str = "user",
        source_id: str | None = None,
        memory_type: str = "project",
        title: str = "",
        summary: str = "",
        source_refs: list[str] | None = None,
        confidence: float | None = None,
    ) -> XpertMemoryRecord:
        clean_content = self._required_text(content, "content", 20_000)
        self._validate_memory_scope(xpert_id, scope, conversation_id)
        if scope == "xpert":
            try:
                item = self.file_memory_store.create_memory(
                    xpert_id,
                    content=clean_content,
                    memory_type=memory_type,
                    title=title,
                    summary=summary,
                    tags=tags,
                    source_type=source_type,
                    source_id=source_id,
                    source_refs=source_refs,
                    confidence=confidence,
                )
                self.file_memory_store.record_signal(
                    xpert_id,
                    "explicit_write",
                    memory_id=item.memory_id,
                    source_ref=source_id,
                )
                return self._file_memory_record(item)
            except FileMemoryError as exc:
                raise XpertContextValidationError(str(exc)) from exc
        record = XpertMemoryRecord(
            memory_id=str(uuid.uuid4()),
            xpert_id=xpert_id,
            scope=scope,
            conversation_id=conversation_id if scope == "conversation" else None,
            content=clean_content,
            tags=self._clean_tags(tags),
            source_type=source_type[:80] or "user",
            source_id=source_id,
            memory_type=self._memory_type(memory_type),
            title=(title.strip()[:120] or self._memory_title(clean_content)),
            summary=(summary.strip()[:500] or self._memory_summary(clean_content)),
        )
        with self._lock:
            self._memories[record.memory_id] = record
            self._persist_unlocked()
        return record

    def get_memory(
        self,
        xpert_id: str,
        memory_id: str,
        *,
        record_detail_read: bool = False,
        conversation_id: str | None = None,
    ) -> XpertMemoryRecord:
        with self._lock:
            record = self._memories.get(memory_id)
            if record is not None:
                if record.xpert_id != xpert_id:
                    raise XpertContextNotFoundError("Xpert memory not found.")
                if record.scope == "conversation":
                    return record
            self._migrate_xpert_memories_unlocked(xpert_id)
        try:
            return self._file_memory_record(
                self.file_memory_store.get_memory(
                    xpert_id,
                    memory_id,
                    record_detail_read=record_detail_read,
                    conversation_id=conversation_id,
                )
            )
        except FileMemoryNotFoundError as exc:
            raise XpertContextNotFoundError(str(exc)) from exc
        except FileMemoryError as exc:
            raise XpertContextError(str(exc)) from exc

    def list_memories(
        self,
        xpert_id: str,
        *,
        scope: Literal["conversation", "xpert", "both"] = "both",
        conversation_id: str | None = None,
        search: str = "",
        memory_type: str | None = None,
        status: str = "active",
        limit: int = 50,
    ) -> list[XpertMemoryRecord]:
        query = search.strip().lower()
        with self._lock:
            if scope in {"xpert", "both"}:
                self._migrate_xpert_memories_unlocked(xpert_id)
            conversation_items = [
                item
                for item in self._memories.values()
                if item.xpert_id == xpert_id
                and item.scope == "conversation"
                and (scope in {"conversation", "both"})
                and item.status == status
                and conversation_id is not None
                and item.conversation_id == conversation_id
                and (
                    not query
                    or query in item.title.lower()
                    or query in item.summary.lower()
                    or query in item.content.lower()
                    or any(query in tag.lower() for tag in item.tags)
                )
            ]
        file_items: list[XpertMemoryRecord] = []
        if scope in {"xpert", "both"}:
            try:
                file_items = [
                    self._file_memory_record(item)
                    for item in self.file_memory_store.list_memories(
                        xpert_id,
                        search=search,
                        memory_type=memory_type,
                        status=status,
                        limit=max(limit, 200),
                    )
                ]
            except FileMemoryError as exc:
                raise XpertContextError(str(exc)) from exc
        items = [*conversation_items, *file_items]
        items.sort(key=lambda item: item.updated_at, reverse=True)
        return items[: max(1, min(limit, 200))]

    def search_memories(
        self,
        xpert_id: str,
        query: str,
        *,
        scope: Literal["conversation", "xpert", "both"] = "both",
        conversation_id: str | None = None,
        limit: int = 10,
        record_recall: bool = True,
    ) -> list[XpertMemoryRecord]:
        selected: list[XpertMemoryRecord] = []
        if scope in {"xpert", "both"}:
            with self._lock:
                self._migrate_xpert_memories_unlocked(xpert_id)
            try:
                selected.extend(
                    self._file_memory_record(item)
                    for item in self.file_memory_store.search_memories(
                        xpert_id,
                        query,
                        limit=limit,
                        conversation_id=conversation_id,
                        record_recall=record_recall,
                    )
                )
            except FileMemoryError as exc:
                raise XpertContextError(str(exc)) from exc
        candidates = self.list_memories(
            xpert_id,
            scope="conversation" if scope in {"conversation", "both"} else "conversation",
            conversation_id=conversation_id,
            limit=200,
        ) if scope in {"conversation", "both"} and conversation_id else []
        clean_query = query.strip().lower()
        if not clean_query:
            conversation_selected = candidates[:limit]
            combined = [*selected, *conversation_selected]
            combined.sort(key=lambda item: item.updated_at, reverse=True)
            return combined[:limit]
        query_terms = self._search_terms(clean_query)
        now = time.time()

        def score(record: XpertMemoryRecord) -> float:
            haystack = f"{record.content} {' '.join(record.tags)}".lower()
            value = 10.0 if clean_query in haystack else 0.0
            value += sum(2.0 for term in query_terms if term in haystack)
            value += max(0.0, 1.0 - (now - record.updated_at) / (90 * 86400))
            return value

        ranked = [(score(item), item) for item in candidates]
        ranked.sort(key=lambda pair: (pair[0], pair[1].updated_at), reverse=True)
        selected.extend(item for value, item in ranked if value > 0)
        unique = {item.memory_id: item for item in selected}
        combined = list(unique.values())
        combined.sort(
            key=lambda item: (
                float(item.usage.get("usefulness_score") or 0),
                item.updated_at,
            ),
            reverse=True,
        )
        return combined[: max(1, min(limit, 20))]

    def update_memory(
        self,
        xpert_id: str,
        memory_id: str,
        *,
        revision: int,
        content: str | None = None,
        memory_type: str | None = None,
        title: str | None = None,
        summary: str | None = None,
        tags: list[str] | None = None,
        source_refs: list[str] | None = None,
        confidence: float | None = None,
    ) -> XpertMemoryRecord:
        with self._lock:
            record = self._memories.get(memory_id)
            if record is not None and record.xpert_id == xpert_id and record.scope == "conversation":
                if record.revision != int(revision):
                    raise XpertContextConflictError("Xpert memory revision conflict.")
                if content is not None:
                    record.content = self._required_text(content, "content", 20_000)
                if memory_type is not None:
                    record.memory_type = self._memory_type(memory_type)
                if title is not None:
                    record.title = title.strip()[:120] or self._memory_title(record.content)
                if summary is not None:
                    record.summary = summary.strip()[:500] or self._memory_summary(record.content)
                if tags is not None:
                    record.tags = self._clean_tags(tags)
                if source_refs is not None:
                    record.source_refs = self._clean_values(source_refs, limit=20, max_length=300)
                if confidence is not None:
                    record.confidence = max(0.0, min(float(confidence), 1.0))
                record.revision += 1
                record.updated_at = time.time()
                self._persist_unlocked()
                return record
            self._migrate_xpert_memories_unlocked(xpert_id)
        try:
            return self._file_memory_record(
                self.file_memory_store.update_memory(
                    xpert_id,
                    memory_id,
                    revision=revision,
                    content=content,
                    memory_type=memory_type,
                    title=title,
                    summary=summary,
                    tags=tags,
                    source_refs=source_refs,
                    confidence=confidence,
                    source_type="manual",
                )
            )
        except FileMemoryConflictError as exc:
            raise XpertContextConflictError(str(exc)) from exc
        except FileMemoryNotFoundError as exc:
            raise XpertContextNotFoundError(str(exc)) from exc
        except FileMemoryError as exc:
            raise XpertContextValidationError(str(exc)) from exc

    def archive_memory(
        self,
        xpert_id: str,
        memory_id: str,
        *,
        revision: int | None = None,
    ) -> XpertMemoryRecord:
        with self._lock:
            record = self._memories.get(memory_id)
            if record is not None and record.xpert_id == xpert_id and record.scope == "conversation":
                if revision is not None and record.revision != int(revision):
                    raise XpertContextConflictError("Xpert memory revision conflict.")
                record.status = "archived"
                record.revision += 1
                record.updated_at = time.time()
                self._persist_unlocked()
                return record
            self._migrate_xpert_memories_unlocked(xpert_id)
        try:
            return self._file_memory_record(
                self.file_memory_store.archive_memory(
                    xpert_id,
                    memory_id,
                    revision=revision,
                )
            )
        except FileMemoryConflictError as exc:
            raise XpertContextConflictError(str(exc)) from exc
        except FileMemoryNotFoundError as exc:
            raise XpertContextNotFoundError(str(exc)) from exc

    def create_candidate(
        self,
        xpert_id: str,
        *,
        content: str,
        scope: MemoryScope,
        conversation_id: str | None = None,
        tags: list[str] | None = None,
        source_run_id: str | None = None,
        action: str = "create",
        memory_type: str = "project",
        title: str = "",
        summary: str = "",
        target_memory_id: str | None = None,
        base_revision: int | None = None,
        source_refs: list[str] | None = None,
        confidence: float | None = None,
    ) -> MemoryWriteCandidate:
        clean_content = self._required_text(content, "content", 20_000)
        self._validate_memory_scope(xpert_id, scope, conversation_id)
        clean_action = str(action or "create").strip()
        if clean_action not in {"create", "update"}:
            raise XpertContextValidationError("Memory candidate action must be create or update.")
        clean_type = self._memory_type(memory_type)
        if clean_action == "update" and not target_memory_id:
            raise XpertContextValidationError("target_memory_id is required for update candidates.")
        with self._lock:
            for existing in self._candidates.values():
                if (
                    existing.xpert_id == xpert_id
                    and existing.status == "pending"
                    and existing.content.casefold() == clean_content.casefold()
                    and existing.scope == scope
                    and existing.conversation_id == conversation_id
                    and existing.source_run_id == source_run_id
                ):
                    return existing
            candidate = MemoryWriteCandidate(
                candidate_id=str(uuid.uuid4()),
                xpert_id=xpert_id,
                scope=scope,
                conversation_id=conversation_id if scope == "conversation" else None,
                content=clean_content,
                tags=self._clean_tags(tags),
                source_run_id=source_run_id,
                action=clean_action,  # type: ignore[arg-type]
                memory_type=clean_type,
                title=(title.strip()[:120] or self._memory_title(clean_content)),
                summary=(summary.strip()[:500] or self._memory_summary(clean_content)),
                target_memory_id=target_memory_id,
                base_revision=base_revision,
                source_refs=self._clean_values(source_refs, limit=20, max_length=300),
                confidence=(
                    max(0.0, min(float(confidence), 1.0))
                    if confidence is not None
                    else None
                ),
            )
            self._candidates[candidate.candidate_id] = candidate
            self._persist_unlocked()
        if scope == "xpert":
            self.file_memory_store.record_signal(
                xpert_id,
                "candidate_created",
                memory_id=target_memory_id,
                source_ref=source_run_id,
                metadata={"action": clean_action, "candidate_id": candidate.candidate_id},
            )
        return candidate

    def update_candidate(
        self,
        xpert_id: str,
        candidate_id: str,
        *,
        revision: int,
        content: str | None = None,
        memory_type: str | None = None,
        title: str | None = None,
        summary: str | None = None,
        tags: list[str] | None = None,
        action: str | None = None,
        target_memory_id: str | None = None,
        base_revision: int | None = None,
        source_refs: list[str] | None = None,
        confidence: float | None = None,
    ) -> MemoryWriteCandidate:
        with self._lock:
            candidate = self._candidates.get(candidate_id)
            if candidate is None or candidate.xpert_id != xpert_id:
                raise XpertContextNotFoundError("Memory candidate not found.")
            if candidate.status != "pending":
                raise XpertContextValidationError("Only pending memory candidates can be edited.")
            if candidate.revision != int(revision):
                raise XpertContextConflictError("Memory candidate revision conflict.")
            if content is not None:
                candidate.content = self._required_text(content, "content", 20_000)
            if memory_type is not None:
                candidate.memory_type = self._memory_type(memory_type)
            if title is not None:
                candidate.title = title.strip()[:120] or self._memory_title(candidate.content)
            if summary is not None:
                candidate.summary = summary.strip()[:500] or self._memory_summary(candidate.content)
            if tags is not None:
                candidate.tags = self._clean_tags(tags)
            if action is not None:
                if action not in {"create", "update"}:
                    raise XpertContextValidationError("Memory candidate action must be create or update.")
                candidate.action = action  # type: ignore[assignment]
            if target_memory_id is not None:
                candidate.target_memory_id = target_memory_id or None
            if base_revision is not None:
                candidate.base_revision = int(base_revision)
            if source_refs is not None:
                candidate.source_refs = self._clean_values(source_refs, limit=20, max_length=300)
            if confidence is not None:
                candidate.confidence = max(0.0, min(float(confidence), 1.0))
            if candidate.action == "update" and not candidate.target_memory_id:
                raise XpertContextValidationError("target_memory_id is required for update candidates.")
            candidate.revision += 1
            self._persist_unlocked()
            return candidate

    def list_candidates(
        self,
        xpert_id: str,
        *,
        status: CandidateStatus | None = None,
        conversation_id: str | None = None,
        limit: int = 50,
    ) -> list[MemoryWriteCandidate]:
        with self._lock:
            items = [
                item
                for item in self._candidates.values()
                if item.xpert_id == xpert_id
                and (status is None or item.status == status)
                and (conversation_id is None or item.conversation_id == conversation_id)
            ]
        items.sort(key=lambda item: item.created_at, reverse=True)
        return items[: max(1, min(limit, 200))]

    def decide_candidate(
        self,
        xpert_id: str,
        candidate_id: str,
        *,
        approve: bool,
        revision: int | None = None,
    ) -> MemoryWriteCandidate:
        with self._lock:
            candidate = self._candidates.get(candidate_id)
            if candidate is None or candidate.xpert_id != xpert_id:
                raise XpertContextNotFoundError("Memory candidate not found.")
            if candidate.status != "pending":
                raise XpertContextValidationError("Memory candidate was already decided.")
            if revision is not None and candidate.revision != int(revision):
                raise XpertContextConflictError("Memory candidate revision conflict.")
            if approve:
                try:
                    if candidate.action == "update":
                        if not candidate.target_memory_id or candidate.base_revision is None:
                            raise XpertContextValidationError(
                                "Update candidate needs target_memory_id and base_revision."
                            )
                        memory = self.update_memory(
                            xpert_id,
                            candidate.target_memory_id,
                            revision=candidate.base_revision,
                            content=candidate.content,
                            memory_type=candidate.memory_type,
                            title=candidate.title,
                            summary=candidate.summary,
                            tags=candidate.tags,
                            source_refs=candidate.source_refs,
                            confidence=candidate.confidence,
                        )
                    else:
                        memory = self.create_memory(
                            xpert_id,
                            content=candidate.content,
                            scope=candidate.scope,
                            conversation_id=candidate.conversation_id,
                            tags=candidate.tags,
                            source_type="candidate",
                            source_id=candidate.candidate_id,
                            memory_type=candidate.memory_type,
                            title=candidate.title,
                            summary=candidate.summary,
                            source_refs=candidate.source_refs,
                            confidence=candidate.confidence,
                        )
                    candidate.status = "approved"
                    candidate.memory_id = memory.memory_id
                    candidate.error = None
                except XpertContextConflictError as exc:
                    if "revision conflict" not in str(exc).lower():
                        raise
                    candidate.status = "conflict"
                    candidate.error = str(exc)[:300]
            else:
                candidate.status = "rejected"
            candidate.decided_at = time.time()
            candidate.revision += 1
            self._persist_unlocked()
            return candidate

    def create_tool_memory(
        self,
        xpert_id: str,
        conversation_id: str,
        *,
        tool_name: str,
        provider: str,
        summary: str,
        arguments: dict[str, Any] | None = None,
        source_run_id: str | None = None,
    ) -> XpertToolMemoryRecord:
        clean_summary = self._required_text(
            str(summary or "")[:MAX_TOOL_MEMORY_CHARS],
            "summary",
            MAX_TOOL_MEMORY_CHARS,
        )
        record = XpertToolMemoryRecord(
            memory_id=f"toolmem_{uuid.uuid4().hex}",
            xpert_id=self._required_text(xpert_id, "xpert_id", 200),
            conversation_id=self._required_text(
                conversation_id,
                "conversation_id",
                200,
            ),
            tool_name=self._required_text(tool_name, "tool_name", 200),
            provider=str(provider or "runtime").strip()[:120] or "runtime",
            summary=clean_summary,
            parameter_summary=self._redact_tool_arguments(arguments or {}),
            source_run_id=(str(source_run_id).strip()[:200] if source_run_id else None),
        )
        with self._lock:
            self._require_conversation_unlocked(xpert_id, conversation_id)
            active = sorted(
                (
                    item
                    for item in self._tool_memories.values()
                    if item.xpert_id == xpert_id
                    and item.conversation_id == conversation_id
                    and item.status == "active"
                ),
                key=lambda item: item.created_at,
            )
            for stale in active[
                : max(0, len(active) - MAX_TOOL_MEMORIES_PER_CONVERSATION + 1)
            ]:
                stale.status = "archived"
                stale.updated_at = time.time()
            self._tool_memories[record.memory_id] = record
            self._persist_unlocked()
        return record

    def list_tool_memories(
        self,
        xpert_id: str,
        conversation_id: str,
        *,
        limit: int = 50,
    ) -> list[XpertToolMemoryRecord]:
        with self._lock:
            self._require_conversation_unlocked(xpert_id, conversation_id)
            items = [
                item
                for item in self._tool_memories.values()
                if item.xpert_id == xpert_id
                and item.conversation_id == conversation_id
                and item.status == "active"
            ]
        items.sort(key=lambda item: (-item.created_at, item.memory_id))
        return items[: max(1, min(int(limit), 100))]

    def archive_tool_memory(
        self,
        xpert_id: str,
        conversation_id: str,
        memory_id: str,
    ) -> XpertToolMemoryRecord:
        with self._lock:
            item = self._tool_memories.get(memory_id)
            if (
                item is None
                or item.xpert_id != xpert_id
                or item.conversation_id != conversation_id
            ):
                raise XpertContextNotFoundError("Tool memory not found.")
            item.status = "archived"
            item.updated_at = time.time()
            self._persist_unlocked()
            return item

    @staticmethod
    def conversation_payload(item: XpertConversation, *, include_messages: bool) -> dict[str, Any]:
        payload = asdict(item)
        if not include_messages:
            payload.pop("messages", None)
            payload["message_count"] = len(item.messages)
        return payload

    @staticmethod
    def file_payload(item: XpertFileAsset) -> dict[str, Any]:
        payload = asdict(item)
        payload.pop("storage_key", None)
        payload.pop("text_key", None)
        return payload

    @staticmethod
    def memory_payload(item: XpertMemoryRecord) -> dict[str, Any]:
        payload = asdict(item)
        payload["type"] = payload.get("memory_type", "project")
        return payload

    @staticmethod
    def candidate_payload(item: MemoryWriteCandidate) -> dict[str, Any]:
        payload = asdict(item)
        payload["type"] = payload.get("memory_type", "project")
        return payload

    @staticmethod
    def tool_memory_payload(item: XpertToolMemoryRecord) -> dict[str, Any]:
        return asdict(item)

    def file_memory_index(self, xpert_id: str) -> dict[str, Any]:
        with self._lock:
            self._migrate_xpert_memories_unlocked(xpert_id)
        try:
            return {
                **self.file_memory_store.status(xpert_id),
                "content": self.file_memory_store.read_index(xpert_id),
            }
        except FileMemoryError as exc:
            raise XpertContextError(str(exc)) from exc

    def file_memory_signals(
        self,
        xpert_id: str,
        *,
        memory_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self._lock:
            self._migrate_xpert_memories_unlocked(xpert_id)
        try:
            return [
                asdict(item)
                for item in self.file_memory_store.list_signals(
                    xpert_id,
                    memory_id=memory_id,
                    limit=limit,
                )
            ]
        except FileMemoryError as exc:
            raise XpertContextError(str(exc)) from exc

    def record_file_memory_signal(
        self,
        xpert_id: str,
        signal_type: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            return asdict(
                self.file_memory_store.record_signal(
                    xpert_id,
                    signal_type,  # type: ignore[arg-type]
                    **kwargs,
                )
            )
        except FileMemoryError as exc:
            raise XpertContextError(str(exc)) from exc

    def _validate_memory_scope(
        self,
        xpert_id: str,
        scope: str,
        conversation_id: str | None,
    ) -> None:
        if scope not in {"conversation", "xpert"}:
            raise XpertContextValidationError("Memory scope must be conversation or xpert.")
        if scope == "conversation":
            if not conversation_id:
                raise XpertContextValidationError(
                    "conversation_id is required for conversation memory."
                )
            self.get_conversation(xpert_id, conversation_id)

    def _require_conversation_unlocked(
        self,
        xpert_id: str,
        conversation_id: str,
    ) -> XpertConversation:
        conversation = self._conversations.get(conversation_id)
        if conversation is None or conversation.xpert_id != xpert_id:
            raise XpertContextNotFoundError("Xpert conversation not found.")
        if conversation.archived:
            raise XpertContextValidationError("Xpert conversation is archived.")
        return conversation

    def _migrate_xpert_memories_unlocked(self, xpert_id: str) -> None:
        legacy = [
            item
            for item in self._memories.values()
            if item.xpert_id == xpert_id and item.scope == "xpert"
        ]
        if not legacy:
            return
        migrated_ids: list[str] = []
        try:
            for item in legacy:
                tags = list(item.tags)
                if "legacy-import" not in tags:
                    tags.append("legacy-import")
                self.file_memory_store.create_memory(
                    xpert_id,
                    memory_id=item.memory_id,
                    content=item.content,
                    memory_type=item.memory_type or "project",
                    title=item.title or self._memory_title(item.content),
                    summary=item.summary or self._memory_summary(item.content),
                    tags=tags,
                    source_type="legacy-import",
                    source_id=item.source_id,
                    source_refs=item.source_refs,
                    confidence=item.confidence,
                    created_at=item.created_at,
                    updated_at=item.updated_at,
                )
                if item.status == "archived":
                    restored = self.file_memory_store.get_memory(xpert_id, item.memory_id)
                    self.file_memory_store.archive_memory(
                        xpert_id,
                        item.memory_id,
                        revision=restored.revision,
                    )
                migrated_ids.append(item.memory_id)
        except FileMemoryError as exc:
            raise XpertContextError(f"Failed to migrate Xpert file memory: {exc}") from exc
        for memory_id in migrated_ids:
            self._memories.pop(memory_id, None)
        self._persist_unlocked()

    @staticmethod
    def _file_memory_record(item: FileMemoryRecord) -> XpertMemoryRecord:
        return XpertMemoryRecord(
            memory_id=item.memory_id,
            xpert_id=item.xpert_id,
            scope="xpert",
            content=item.content,
            tags=list(item.tags),
            source_type=item.source_type,
            source_id=item.source_id,
            status=item.status,
            memory_type=item.memory_type,
            title=item.title,
            summary=item.summary,
            revision=item.revision,
            canonical_ref=item.canonical_ref,
            source_refs=list(item.source_refs),
            confidence=item.confidence,
            usage=asdict(item.usage),
            created_at=item.created_at,
            updated_at=item.updated_at,
        )

    def _load(self) -> None:
        if not self.snapshot_path.exists():
            return
        try:
            payload = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
            for raw in payload.get("conversations", []):
                messages = [XpertConversationMessage(**item) for item in raw.pop("messages", [])]
                conversation = XpertConversation(messages=messages, **raw)
                self._conversations[conversation.conversation_id] = conversation
            for raw in payload.get("assets", []):
                asset = XpertFileAsset(**raw)
                self._assets[asset.asset_id] = asset
            for raw in payload.get("memories", []):
                memory = XpertMemoryRecord(**raw)
                self._memories[memory.memory_id] = memory
            for raw in payload.get("candidates", []):
                candidate = MemoryWriteCandidate(**raw)
                self._candidates[candidate.candidate_id] = candidate
            for raw in payload.get("tool_memories", []):
                memory = XpertToolMemoryRecord(**raw)
                self._tool_memories[memory.memory_id] = memory
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise XpertContextError(f"Failed to load Xpert context store: {exc}") from exc

    def _persist_unlocked(self) -> None:
        self.context_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": "xpert-context-v1",
            "conversations": [asdict(item) for item in self._conversations.values()],
            "assets": [asdict(item) for item in self._assets.values()],
            "memories": [asdict(item) for item in self._memories.values()],
            "candidates": [asdict(item) for item in self._candidates.values()],
            "tool_memories": [
                asdict(item) for item in self._tool_memories.values()
            ],
        }
        temporary = self.snapshot_path.with_suffix(
            f".{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, self.snapshot_path)

    @staticmethod
    def _required_text(value: str, field_name: str, max_length: int) -> str:
        clean = str(value or "").strip()
        if not clean:
            raise XpertContextValidationError(f"{field_name} is required.")
        return clean[:max_length]

    @staticmethod
    def _safe_filename(filename: str) -> str:
        clean = str(filename or "").strip()
        if not clean or "/" in clean or "\\" in clean or clean in {".", ".."}:
            raise XpertContextValidationError("The uploaded filename is invalid.")
        if Path(clean).name != clean:
            raise XpertContextValidationError("The uploaded filename is invalid.")
        return clean[:240]

    @staticmethod
    def _clean_tags(tags: list[str] | None) -> list[str]:
        result: list[str] = []
        for value in tags or []:
            clean = str(value).strip()[:80]
            if clean and clean not in result:
                result.append(clean)
            if len(result) >= 10:
                break
        return result

    @staticmethod
    def _clean_values(
        values: list[str] | None,
        *,
        limit: int,
        max_length: int,
    ) -> list[str]:
        result: list[str] = []
        for value in values or []:
            clean = str(value).strip()[:max_length]
            if clean and clean not in result:
                result.append(clean)
            if len(result) >= limit:
                break
        return result

    @staticmethod
    def _memory_type(value: str) -> Literal["user", "feedback", "project", "reference"]:
        clean = str(value or "project").strip().lower()
        if clean not in {"user", "feedback", "project", "reference"}:
            raise XpertContextValidationError(
                "Memory type must be user, feedback, project, or reference."
            )
        return clean  # type: ignore[return-value]

    @staticmethod
    def _memory_title(content: str) -> str:
        return next(
            (line.strip().lstrip("#").strip()[:120] for line in content.splitlines() if line.strip()),
            "Memory",
        )

    @staticmethod
    def _memory_summary(content: str) -> str:
        return re.sub(r"\s+", " ", content).strip()[:500]

    @classmethod
    def _redact_tool_arguments(cls, arguments: dict[str, Any]) -> dict[str, Any]:
        sensitive = re.compile(
            r"(api.?key|token|secret|password|authorization|cookie|credential)",
            re.IGNORECASE,
        )

        def clean(value: Any, key: str = "") -> Any:
            if key and sensitive.search(key):
                return "[redacted]"
            if isinstance(value, dict):
                return {
                    str(child_key)[:120]: clean(child_value, str(child_key))
                    for child_key, child_value in list(value.items())[:40]
                }
            if isinstance(value, list):
                return [clean(item) for item in value[:20]]
            if isinstance(value, str):
                return value[:300]
            if value is None or isinstance(value, (bool, int, float)):
                return value
            return str(value)[:300]

        return clean(arguments)

    @staticmethod
    def _search_terms(value: str) -> set[str]:
        terms = {item for item in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", value) if item}
        for item in list(terms):
            if re.fullmatch(r"[\u4e00-\u9fff]+", item) and len(item) > 1:
                terms.update(item[index : index + 2] for index in range(len(item) - 1))
        return terms
