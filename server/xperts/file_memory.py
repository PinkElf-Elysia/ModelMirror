from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal


FileMemoryType = Literal["user", "feedback", "project", "reference"]
FileMemoryStatus = Literal["active", "archived", "conflict"]
SignalType = Literal[
    "recall_hit",
    "detail_read",
    "explicit_write",
    "candidate_created",
    "correction",
    "index_issue",
]

MEMORY_TYPES = {"user", "feedback", "project", "reference"}
MAX_MEMORY_BODY_CHARS = 20_000
MAX_MEMORY_SIGNALS = 10_000


class FileMemoryError(Exception):
    """Base error for durable Xpert file memory."""


class FileMemoryNotFoundError(FileMemoryError):
    """Raised when a file memory is absent or belongs to another Xpert."""


class FileMemoryConflictError(FileMemoryError):
    """Raised when an optimistic revision check fails."""


@dataclass(slots=True)
class FileMemoryUsage:
    recall_count: int = 0
    detail_read_count: int = 0
    explicit_write_count: int = 0
    candidate_count: int = 0
    correction_count: int = 0
    usefulness_score: float = 0.0
    last_recalled_at: float | None = None
    last_detail_read_at: float | None = None


@dataclass(slots=True)
class FileMemoryRecord:
    memory_id: str
    xpert_id: str
    memory_type: FileMemoryType
    title: str
    summary: str
    content: str
    tags: list[str] = field(default_factory=list)
    status: FileMemoryStatus = "active"
    revision: int = 1
    source_type: str = "user"
    source_id: str | None = None
    source_refs: list[str] = field(default_factory=list)
    confidence: float | None = None
    usage: FileMemoryUsage = field(default_factory=FileMemoryUsage)
    body_key: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    archived_at: float | None = None

    @property
    def canonical_ref(self) -> str:
        return f"memory://xpert/{self.memory_id}"


@dataclass(slots=True)
class FileMemorySignal:
    signal_id: str
    xpert_id: str
    signal_type: SignalType
    memory_id: str | None = None
    conversation_id: str | None = None
    query_hash: str | None = None
    source_ref: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


class XpertFileMemoryStore:
    """Markdown-backed, atomic durable memory store scoped to one Xpert."""

    def __init__(self, storage_dir: str | Path) -> None:
        self.storage_dir = Path(storage_dir)
        self._lock = threading.RLock()

    def create_memory(
        self,
        xpert_id: str,
        *,
        content: str,
        memory_type: str = "project",
        title: str = "",
        summary: str = "",
        tags: list[str] | None = None,
        source_type: str = "user",
        source_id: str | None = None,
        source_refs: list[str] | None = None,
        confidence: float | None = None,
        memory_id: str | None = None,
        created_at: float | None = None,
        updated_at: float | None = None,
    ) -> FileMemoryRecord:
        clean_xpert_id = self._required_text(xpert_id, "xpert_id", 200)
        clean_content = self._required_text(content, "content", MAX_MEMORY_BODY_CHARS)
        clean_type = self._memory_type(memory_type)
        clean_id = self._required_text(memory_id or str(uuid.uuid4()), "memory_id", 200)
        now = time.time()
        record = FileMemoryRecord(
            memory_id=clean_id,
            xpert_id=clean_xpert_id,
            memory_type=clean_type,
            title=self._title(title, clean_content),
            summary=self._summary(summary, clean_content),
            content=clean_content,
            tags=self._clean_list(tags, limit=20, item_limit=80),
            source_type=str(source_type or "user")[:80],
            source_id=str(source_id)[:200] if source_id else None,
            source_refs=self._clean_list(source_refs, limit=20, item_limit=300),
            confidence=self._confidence(confidence),
            body_key=self._body_key(clean_type, clean_id),
            created_at=float(created_at or now),
            updated_at=float(updated_at or now),
        )
        with self._lock:
            payload = self._load_manifest(clean_xpert_id)
            if any(item.get("memory_id") == clean_id for item in payload["records"]):
                return self.get_memory(clean_xpert_id, clean_id, include_archived=True)
            self._write_body(clean_xpert_id, record)
            payload["records"].append(self._record_manifest(record))
            self._persist_manifest(clean_xpert_id, payload)
            self._write_index(clean_xpert_id, payload)
        return record

    def get_memory(
        self,
        xpert_id: str,
        memory_id: str,
        *,
        include_archived: bool = True,
        record_detail_read: bool = False,
        conversation_id: str | None = None,
    ) -> FileMemoryRecord:
        with self._lock:
            payload = self._load_manifest(xpert_id)
            raw = next(
                (item for item in payload["records"] if item.get("memory_id") == memory_id),
                None,
            )
            if raw is None:
                raise FileMemoryNotFoundError("Xpert file memory not found.")
            record = self._record_from_manifest(xpert_id, raw)
            if record.status == "archived" and not include_archived:
                raise FileMemoryNotFoundError("Xpert file memory is archived.")
            if record_detail_read and record.status == "active":
                self._record_signal_unlocked(
                    xpert_id,
                    payload,
                    "detail_read",
                    memory_id=memory_id,
                    conversation_id=conversation_id,
                )
                record = self._record_from_manifest(
                    xpert_id,
                    next(item for item in payload["records"] if item.get("memory_id") == memory_id),
                )
        return record

    def list_memories(
        self,
        xpert_id: str,
        *,
        search: str = "",
        memory_type: str | None = None,
        status: str = "active",
        limit: int = 200,
    ) -> list[FileMemoryRecord]:
        clean_type = self._memory_type(memory_type) if memory_type else None
        query = search.strip().casefold()
        with self._lock:
            payload = self._load_manifest(xpert_id)
            records = [self._record_from_manifest(xpert_id, raw) for raw in payload["records"]]
        records = [
            item
            for item in records
            if (not status or item.status == status)
            and (clean_type is None or item.memory_type == clean_type)
            and (
                not query
                or query in item.title.casefold()
                or query in item.summary.casefold()
                or query in item.content.casefold()
                or any(query in tag.casefold() for tag in item.tags)
            )
        ]
        records.sort(
            key=lambda item: (item.usage.usefulness_score, item.updated_at, item.memory_id),
            reverse=True,
        )
        return records[: max(1, min(int(limit), 500))]

    def search_memories(
        self,
        xpert_id: str,
        query: str,
        *,
        limit: int = 10,
        conversation_id: str | None = None,
        record_recall: bool = True,
    ) -> list[FileMemoryRecord]:
        clean_query = query.strip().casefold()
        candidates = self.list_memories(xpert_id, limit=500)
        if not clean_query:
            selected = candidates[: max(1, min(limit, 20))]
        else:
            terms = self._search_terms(clean_query)
            now = time.time()

            def score(record: FileMemoryRecord) -> float:
                header = f"{record.title} {record.summary} {' '.join(record.tags)}".casefold()
                body = record.content.casefold()
                value = 12.0 if clean_query in header else 0.0
                value += 7.0 if clean_query in body else 0.0
                value += sum(3.0 for term in terms if term in header)
                value += sum(1.0 for term in terms if term in body)
                value += min(record.usage.usefulness_score, 20.0) * 0.25
                value += max(0.0, 1.0 - (now - record.updated_at) / (180 * 86400))
                return value

            ranked = [(score(item), item) for item in candidates]
            ranked.sort(key=lambda pair: (pair[0], pair[1].updated_at, pair[1].memory_id), reverse=True)
            selected = [item for value, item in ranked if value > 0][
                : max(1, min(limit, 20))
            ]
        if selected and record_recall:
            query_hash = hashlib.sha256(clean_query.encode("utf-8")).hexdigest()[:16]
            with self._lock:
                payload = self._load_manifest(xpert_id)
                for item in selected:
                    self._record_signal_unlocked(
                        xpert_id,
                        payload,
                        "recall_hit",
                        memory_id=item.memory_id,
                        conversation_id=conversation_id,
                        query_hash=query_hash,
                        persist=False,
                    )
                self._persist_manifest(xpert_id, payload)
                self._write_index(xpert_id, payload)
            selected = [self.get_memory(xpert_id, item.memory_id) for item in selected]
        return selected

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
        source_type: str | None = None,
        source_id: str | None = None,
    ) -> FileMemoryRecord:
        with self._lock:
            payload = self._load_manifest(xpert_id)
            index, raw = self._require_raw(payload, memory_id)
            current = self._record_from_manifest(xpert_id, raw)
            if current.revision != int(revision):
                raise FileMemoryConflictError("Xpert file memory revision conflict.")
            next_content = (
                self._required_text(content, "content", MAX_MEMORY_BODY_CHARS)
                if content is not None
                else current.content
            )
            next_type = self._memory_type(memory_type) if memory_type else current.memory_type
            current.content = next_content
            current.memory_type = next_type
            current.title = self._title(title if title is not None else current.title, next_content)
            current.summary = self._summary(
                summary if summary is not None else current.summary,
                next_content,
            )
            if tags is not None:
                current.tags = self._clean_list(tags, limit=20, item_limit=80)
            if source_refs is not None:
                current.source_refs = self._clean_list(source_refs, limit=20, item_limit=300)
            if confidence is not None:
                current.confidence = self._confidence(confidence)
            if source_type is not None:
                current.source_type = str(source_type)[:80]
            if source_id is not None:
                current.source_id = str(source_id)[:200]
            current.status = "active"
            current.archived_at = None
            current.revision += 1
            current.updated_at = time.time()
            current.body_key = self._body_key(next_type, memory_id)
            old_body_key = str(raw.get("body_key") or "")
            self._write_body(xpert_id, current)
            payload["records"][index] = self._record_manifest(current)
            self._record_signal_unlocked(
                xpert_id,
                payload,
                "correction",
                memory_id=memory_id,
                persist=False,
            )
            self._persist_manifest(xpert_id, payload)
            self._write_index(xpert_id, payload)
            if old_body_key and old_body_key != current.body_key:
                self._safe_body_path(xpert_id, old_body_key).unlink(missing_ok=True)
        return self.get_memory(xpert_id, memory_id)

    def archive_memory(
        self,
        xpert_id: str,
        memory_id: str,
        *,
        revision: int | None = None,
    ) -> FileMemoryRecord:
        with self._lock:
            payload = self._load_manifest(xpert_id)
            index, raw = self._require_raw(payload, memory_id)
            record = self._record_from_manifest(xpert_id, raw)
            if revision is not None and record.revision != int(revision):
                raise FileMemoryConflictError("Xpert file memory revision conflict.")
            record.status = "archived"
            record.revision += 1
            record.updated_at = time.time()
            record.archived_at = record.updated_at
            payload["records"][index] = self._record_manifest(record)
            self._persist_manifest(xpert_id, payload)
            self._write_index(xpert_id, payload)
        return record

    def record_signal(
        self,
        xpert_id: str,
        signal_type: SignalType,
        *,
        memory_id: str | None = None,
        conversation_id: str | None = None,
        query: str | None = None,
        source_ref: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> FileMemorySignal:
        query_hash = (
            hashlib.sha256(query.encode("utf-8")).hexdigest()[:16] if query else None
        )
        with self._lock:
            payload = self._load_manifest(xpert_id)
            return self._record_signal_unlocked(
                xpert_id,
                payload,
                signal_type,
                memory_id=memory_id,
                conversation_id=conversation_id,
                query_hash=query_hash,
                source_ref=source_ref,
                metadata=metadata,
            )

    def list_signals(
        self,
        xpert_id: str,
        *,
        memory_id: str | None = None,
        limit: int = 100,
    ) -> list[FileMemorySignal]:
        with self._lock:
            payload = self._load_manifest(xpert_id)
            items = [
                FileMemorySignal(**raw)
                for raw in payload["signals"]
                if memory_id is None or raw.get("memory_id") == memory_id
            ]
        items.sort(key=lambda item: item.created_at, reverse=True)
        return items[: max(1, min(int(limit), 500))]

    def read_index(self, xpert_id: str) -> str:
        with self._lock:
            payload = self._load_manifest(xpert_id)
            path = self._index_path(xpert_id)
            if not path.exists():
                self._write_index(xpert_id, payload)
            return path.read_text(encoding="utf-8")

    def status(self, xpert_id: str) -> dict[str, Any]:
        with self._lock:
            payload = self._load_manifest(xpert_id)
        counts = {memory_type: 0 for memory_type in sorted(MEMORY_TYPES)}
        active = 0
        for raw in payload["records"]:
            if raw.get("status") == "active":
                active += 1
                memory_type = str(raw.get("memory_type") or "project")
                counts[memory_type] = counts.get(memory_type, 0) + 1
        return {
            "version": payload["version"],
            "active_count": active,
            "archived_count": sum(
                1 for raw in payload["records"] if raw.get("status") == "archived"
            ),
            "type_counts": counts,
            "signal_count": len(payload["signals"]),
            "index_revision": int(payload.get("index_revision") or 0),
            "updated_at": payload.get("updated_at"),
        }

    @staticmethod
    def payload(record: FileMemoryRecord) -> dict[str, Any]:
        result = asdict(record)
        result.pop("body_key", None)
        result["type"] = result.pop("memory_type")
        result["scope"] = "xpert"
        result["conversation_id"] = None
        result["canonical_ref"] = record.canonical_ref
        return result

    def _scope_dir(self, xpert_id: str) -> Path:
        digest = hashlib.sha256(xpert_id.encode("utf-8")).hexdigest()[:32]
        return self.storage_dir / digest

    def _manifest_path(self, xpert_id: str) -> Path:
        return self._scope_dir(xpert_id) / "manifest.json"

    def _index_path(self, xpert_id: str) -> Path:
        return self._scope_dir(xpert_id) / "MEMORY.md"

    @staticmethod
    def _body_key(memory_type: str, memory_id: str) -> str:
        filename = hashlib.sha256(memory_id.encode("utf-8")).hexdigest() + ".md"
        return f"{memory_type}/{filename}"

    def _safe_body_path(self, xpert_id: str, body_key: str) -> Path:
        root = self._scope_dir(xpert_id).resolve()
        path = (root / body_key).resolve(strict=False)
        if root not in path.parents or path.is_symlink():
            raise FileMemoryError("Xpert file memory path is unsafe.")
        return path

    def _load_manifest(self, xpert_id: str) -> dict[str, Any]:
        path = self._manifest_path(xpert_id)
        if not path.exists():
            return {
                "version": "xpert-file-memory-v1",
                "xpert_id": xpert_id,
                "index_revision": 0,
                "records": [],
                "signals": [],
                "updated_at": None,
            }
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise FileMemoryError(f"Failed to load Xpert file memory: {exc}") from exc
        if payload.get("xpert_id") != xpert_id:
            raise FileMemoryError("Xpert file memory scope does not match manifest.")
        payload.setdefault("records", [])
        payload.setdefault("signals", [])
        payload.setdefault("index_revision", 0)
        return payload

    def _persist_manifest(self, xpert_id: str, payload: dict[str, Any]) -> None:
        scope_dir = self._scope_dir(xpert_id)
        scope_dir.mkdir(parents=True, exist_ok=True)
        payload["updated_at"] = time.time()
        path = self._manifest_path(xpert_id)
        temporary = path.with_suffix(f".{os.getpid()}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)

    def _write_body(self, xpert_id: str, record: FileMemoryRecord) -> None:
        path = self._safe_body_path(xpert_id, record.body_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{os.getpid()}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(record.content, encoding="utf-8")
        os.replace(temporary, path)

    def _write_index(self, xpert_id: str, payload: dict[str, Any]) -> None:
        records = [
            self._record_from_manifest(xpert_id, raw)
            for raw in payload["records"]
            if raw.get("status") == "active"
        ]
        records.sort(key=lambda item: (item.memory_type, -item.updated_at, item.memory_id))
        lines = ["# Xpert Memory Index", "", "This index is derived from approved durable memories.", ""]
        for memory_type in ("user", "feedback", "project", "reference"):
            typed = [item for item in records if item.memory_type == memory_type]
            lines.extend([f"## {memory_type}", ""])
            if not typed:
                lines.extend(["- None", ""])
                continue
            for item in typed:
                tags = f" tags={','.join(item.tags)}" if item.tags else ""
                lines.append(
                    f"- [{item.title}]({item.canonical_ref}) rev={item.revision}{tags}: {item.summary}"
                )
            lines.append("")
        payload["index_revision"] = int(payload.get("index_revision") or 0) + 1
        self._persist_manifest(xpert_id, payload)
        path = self._index_path(xpert_id)
        temporary = path.with_suffix(f".{os.getpid()}.{uuid.uuid4().hex}.tmp")
        temporary.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
        os.replace(temporary, path)

    def _record_from_manifest(self, xpert_id: str, raw: dict[str, Any]) -> FileMemoryRecord:
        body_key = str(raw.get("body_key") or "")
        try:
            content = self._safe_body_path(xpert_id, body_key).read_text(encoding="utf-8")
        except OSError as exc:
            raise FileMemoryError("Xpert file memory body is unavailable.") from exc
        usage_raw = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
        values = dict(raw)
        values.pop("usage", None)
        values.pop("content", None)
        return FileMemoryRecord(
            content=content,
            usage=FileMemoryUsage(**usage_raw),
            **values,
        )

    @staticmethod
    def _record_manifest(record: FileMemoryRecord) -> dict[str, Any]:
        payload = asdict(record)
        payload.pop("content", None)
        return payload

    @staticmethod
    def _require_raw(payload: dict[str, Any], memory_id: str) -> tuple[int, dict[str, Any]]:
        for index, raw in enumerate(payload["records"]):
            if raw.get("memory_id") == memory_id:
                return index, raw
        raise FileMemoryNotFoundError("Xpert file memory not found.")

    def _record_signal_unlocked(
        self,
        xpert_id: str,
        payload: dict[str, Any],
        signal_type: SignalType,
        *,
        memory_id: str | None = None,
        conversation_id: str | None = None,
        query_hash: str | None = None,
        source_ref: str | None = None,
        metadata: dict[str, Any] | None = None,
        persist: bool = True,
    ) -> FileMemorySignal:
        if signal_type not in {
            "recall_hit",
            "detail_read",
            "explicit_write",
            "candidate_created",
            "correction",
            "index_issue",
        }:
            raise FileMemoryError("Invalid Xpert file memory signal type.")
        signal = FileMemorySignal(
            signal_id=str(uuid.uuid4()),
            xpert_id=xpert_id,
            signal_type=signal_type,
            memory_id=memory_id,
            conversation_id=str(conversation_id)[:200] if conversation_id else None,
            query_hash=query_hash,
            source_ref=str(source_ref)[:300] if source_ref else None,
            metadata=self._safe_metadata(metadata),
        )
        payload["signals"].append(asdict(signal))
        payload["signals"] = payload["signals"][-MAX_MEMORY_SIGNALS:]
        if memory_id:
            try:
                index, raw = self._require_raw(payload, memory_id)
            except FileMemoryNotFoundError:
                index = -1
            if index >= 0:
                usage = dict(raw.get("usage") or {})
                if signal_type == "recall_hit":
                    usage["recall_count"] = int(usage.get("recall_count") or 0) + 1
                    usage["last_recalled_at"] = signal.created_at
                elif signal_type == "detail_read":
                    usage["detail_read_count"] = int(usage.get("detail_read_count") or 0) + 1
                    usage["last_detail_read_at"] = signal.created_at
                elif signal_type == "explicit_write":
                    usage["explicit_write_count"] = int(usage.get("explicit_write_count") or 0) + 1
                elif signal_type == "candidate_created":
                    usage["candidate_count"] = int(usage.get("candidate_count") or 0) + 1
                elif signal_type == "correction":
                    usage["correction_count"] = int(usage.get("correction_count") or 0) + 1
                usage["usefulness_score"] = round(
                    int(usage.get("recall_count") or 0)
                    + int(usage.get("detail_read_count") or 0) * 2
                    + int(usage.get("explicit_write_count") or 0) * 2
                    + int(usage.get("correction_count") or 0) * 1.5,
                    3,
                )
                raw["usage"] = usage
                payload["records"][index] = raw
        if persist:
            self._persist_manifest(xpert_id, payload)
            self._write_index(xpert_id, payload)
        return signal

    @staticmethod
    def _safe_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in (metadata or {}).items():
            clean_key = str(key)[:80]
            if isinstance(value, bool | int | float) or value is None:
                result[clean_key] = value
            elif isinstance(value, str):
                result[clean_key] = value[:300]
        return result

    @staticmethod
    def _memory_type(value: str) -> FileMemoryType:
        clean = str(value or "").strip().lower()
        if clean not in MEMORY_TYPES:
            raise FileMemoryError("Memory type must be user, feedback, project, or reference.")
        return clean  # type: ignore[return-value]

    @staticmethod
    def _required_text(value: str, field_name: str, max_length: int) -> str:
        clean = str(value or "").strip()
        if not clean:
            raise FileMemoryError(f"{field_name} is required.")
        if len(clean) > max_length:
            raise FileMemoryError(f"{field_name} exceeds the {max_length} character limit.")
        return clean

    @staticmethod
    def _title(value: str, content: str) -> str:
        clean = str(value or "").strip()
        if not clean:
            clean = next((line.strip("# ") for line in content.splitlines() if line.strip()), "Memory")
        return clean[:120]

    @staticmethod
    def _summary(value: str, content: str) -> str:
        clean = str(value or "").strip()
        if not clean:
            clean = re.sub(r"\s+", " ", content).strip()
        return clean[:500]

    @staticmethod
    def _clean_list(values: list[str] | None, *, limit: int, item_limit: int) -> list[str]:
        result: list[str] = []
        for value in values or []:
            clean = str(value).strip()[:item_limit]
            if clean and clean not in result:
                result.append(clean)
            if len(result) >= limit:
                break
        return result

    @staticmethod
    def _confidence(value: float | None) -> float | None:
        if value is None:
            return None
        return round(max(0.0, min(float(value), 1.0)), 4)

    @staticmethod
    def _search_terms(value: str) -> set[str]:
        terms = {item for item in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", value) if item}
        for item in list(terms):
            if re.fullmatch(r"[\u4e00-\u9fff]+", item) and len(item) > 1:
                terms.update(item[index : index + 2] for index in range(len(item) - 1))
        return terms
