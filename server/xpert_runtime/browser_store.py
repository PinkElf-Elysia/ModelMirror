from __future__ import annotations

import json
import mimetypes
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit


BrowserOperationStatus = Literal["running", "completed", "failed"]


class BrowserStoreError(RuntimeError):
    """Base error for browser runtime metadata."""


class BrowserNotFoundError(BrowserStoreError):
    """Raised when a browser session, operation, or artifact is missing."""


class BrowserValidationError(BrowserStoreError):
    """Raised for invalid browser metadata."""


@dataclass(slots=True)
class BrowserDomainGrant:
    domain: str
    operator: str = "local-operator"
    created_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class BrowserSession:
    session_id: str
    scope_type: str
    scope_id: str
    node_id: str
    status: str = "active"
    current_url: str = ""
    current_domain: str = ""
    page_title: str = ""
    action_count: int = 0
    max_actions: int = 100
    grants: list[BrowserDomainGrant] = field(default_factory=list)
    artifact_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_active_at: float = field(default_factory=time.time)
    expires_at: float | None = None


@dataclass(slots=True)
class BrowserOperation:
    operation_id: str
    session_id: str
    tool_name: str
    status: BrowserOperationStatus
    domain: str = ""
    page_title: str = ""
    output_length: int = 0
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    completed_at: float | None = None


@dataclass(slots=True)
class BrowserArtifact:
    artifact_id: str
    session_id: str
    filename: str
    relative_path: str
    content_type: str
    size_bytes: int
    kind: str
    source_run_id: str | None = None
    source_node_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


class BrowserSessionStore:
    """Atomic metadata store for private browser sessions and artifacts."""

    def __init__(
        self,
        storage_dir: str | Path | None = None,
        *,
        data_root: str | Path | None = None,
    ) -> None:
        package_dir = Path(__file__).resolve().parent
        self.storage_dir = Path(
            storage_dir
            or os.getenv("AGENT_TASK_STORAGE_DIR", "").strip()
            or package_dir / "storage"
        )
        self.data_root = Path(
            data_root
            or os.getenv("BROWSER_DATA_ROOT", "").strip()
            or self.storage_dir / "browser-data"
        )
        self.snapshot_path = self.storage_dir / "browser_runtime.json"
        self._lock = threading.RLock()
        self._sessions: dict[str, BrowserSession] = {}
        self._operations: dict[str, BrowserOperation] = {}
        self._artifacts: dict[str, BrowserArtifact] = {}
        self._load()

    def get_or_create_session(
        self,
        *,
        scope_type: str,
        scope_id: str,
        node_id: str,
        max_actions: int = 100,
        expires_at: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> BrowserSession:
        clean_scope_type = self._required(scope_type, "scope_type", 80)
        clean_scope_id = self._required(scope_id, "scope_id", 400)
        clean_node_id = self._required(node_id, "node_id", 200)
        with self._lock:
            self._expire_sessions_unlocked()
            existing = next(
                (
                    item
                    for item in self._sessions.values()
                    if item.scope_type == clean_scope_type
                    and item.scope_id == clean_scope_id
                    and item.node_id == clean_node_id
                    and item.status == "active"
                ),
                None,
            )
            if existing is not None:
                existing.max_actions = max(1, min(int(max_actions), 100))
                existing.updated_at = time.time()
                if metadata:
                    existing.metadata.update(self._safe_metadata(metadata))
                self._persist_unlocked()
                return existing
            item = BrowserSession(
                session_id=f"browser_{uuid.uuid4().hex}",
                scope_type=clean_scope_type,
                scope_id=clean_scope_id,
                node_id=clean_node_id,
                max_actions=max(1, min(int(max_actions), 100)),
                expires_at=expires_at,
                metadata=self._safe_metadata(metadata or {}),
            )
            self._sessions[item.session_id] = item
            self._persist_unlocked()
            return item

    def get_session(self, session_id: str) -> BrowserSession:
        with self._lock:
            item = self._sessions.get(session_id)
            if item is None:
                raise BrowserNotFoundError("Browser session not found.")
            return item

    def list_sessions(
        self,
        *,
        scope_type: str | None = None,
        scope_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[BrowserSession]:
        with self._lock:
            changed = self._expire_sessions_unlocked()
            items = list(self._sessions.values())
            if changed:
                self._persist_unlocked()
        if scope_type:
            items = [item for item in items if item.scope_type == scope_type]
        if scope_id:
            items = [item for item in items if item.scope_id == scope_id]
        if status:
            items = [item for item in items if item.status == status]
        items.sort(key=lambda item: (item.updated_at, item.session_id), reverse=True)
        return items[: max(1, min(int(limit), 500))]

    def update_page(
        self,
        session_id: str,
        *,
        url: str = "",
        domain: str = "",
        title: str = "",
    ) -> BrowserSession:
        with self._lock:
            item = self.get_session(session_id)
            safe_url, safe_domain = self._safe_url(url)
            item.current_url = safe_url
            item.current_domain = safe_domain or str(domain or "")[:253]
            item.page_title = str(title or "")[:500]
            item.last_active_at = time.time()
            item.updated_at = item.last_active_at
            self._persist_unlocked()
            return item

    def grant_domain(
        self, session_id: str, domain: str, *, operator: str = "local-operator"
    ) -> BrowserSession:
        clean = self._required(domain, "domain", 253).lower().rstrip(".")
        with self._lock:
            item = self.get_session(session_id)
            if all(grant.domain != clean for grant in item.grants):
                item.grants.append(
                    BrowserDomainGrant(domain=clean, operator=str(operator or "local-operator")[:200])
                )
            item.updated_at = time.time()
            self._persist_unlocked()
            return item

    def revoke_domain(self, session_id: str, domain: str) -> BrowserSession:
        clean = str(domain or "").strip().lower().rstrip(".")
        with self._lock:
            item = self.get_session(session_id)
            item.grants = [grant for grant in item.grants if grant.domain != clean]
            item.updated_at = time.time()
            self._persist_unlocked()
            return item

    def has_domain_grant(self, session_id: str, domain: str) -> bool:
        clean = str(domain or "").strip().lower().rstrip(".")
        item = self.get_session(session_id)
        return any(grant.domain == clean for grant in item.grants)

    def start_operation(
        self,
        operation_id: str,
        *,
        session_id: str,
        tool_name: str,
        domain: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> BrowserOperation:
        with self._lock:
            existing = self._operations.get(operation_id)
            if existing is not None:
                return existing
            session = self.get_session(session_id)
            if session.action_count >= session.max_actions:
                raise BrowserValidationError("Browser action limit reached.")
            session.action_count += 1
            session.updated_at = time.time()
            item = BrowserOperation(
                operation_id=self._required(operation_id, "operation_id", 240),
                session_id=session_id,
                tool_name=self._required(tool_name, "tool_name", 200),
                status="running",
                domain=str(domain or "")[:253],
                metadata=self._safe_metadata(metadata or {}),
            )
            self._operations[item.operation_id] = item
            self._persist_unlocked()
            return item

    def complete_operation(
        self,
        operation_id: str,
        *,
        output_length: int = 0,
        page_title: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> BrowserOperation:
        with self._lock:
            item = self._require_operation(operation_id)
            if item.status == "completed":
                return item
            item.status = "completed"
            item.output_length = max(0, int(output_length))
            item.page_title = str(page_title or "")[:500]
            item.error = None
            if metadata:
                item.metadata.update(self._safe_metadata(metadata))
            item.updated_at = time.time()
            item.completed_at = item.updated_at
            self._persist_unlocked()
            return item

    def fail_operation(self, operation_id: str, *, error: str) -> BrowserOperation:
        with self._lock:
            item = self._require_operation(operation_id)
            if item.status == "completed":
                return item
            item.status = "failed"
            item.error = str(error or "")[:1000]
            item.updated_at = time.time()
            item.completed_at = item.updated_at
            self._persist_unlocked()
            return item

    def list_operations(self, session_id: str, *, limit: int = 100) -> list[BrowserOperation]:
        self.get_session(session_id)
        with self._lock:
            items = [item for item in self._operations.values() if item.session_id == session_id]
        items.sort(key=lambda item: (item.created_at, item.operation_id), reverse=True)
        return items[: max(1, min(int(limit), 500))]

    def register_artifact(
        self,
        *,
        artifact_id: str,
        session_id: str,
        filename: str,
        relative_path: str,
        size_bytes: int,
        content_type: str,
        kind: str,
        source_run_id: str | None = None,
        source_node_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> BrowserArtifact:
        with self._lock:
            existing = self._artifacts.get(artifact_id)
            if existing is not None:
                return existing
            session = self.get_session(session_id)
            item = BrowserArtifact(
                artifact_id=self._required(artifact_id, "artifact_id", 240),
                session_id=session_id,
                filename=Path(filename).name[:200],
                relative_path=self._safe_relative_path(relative_path),
                content_type=str(content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream")[:200],
                size_bytes=max(0, int(size_bytes)),
                kind=str(kind or "download")[:80],
                source_run_id=str(source_run_id or "")[:200] or None,
                source_node_id=str(source_node_id or "")[:200] or None,
                metadata=self._safe_metadata(metadata or {}),
            )
            self._artifacts[item.artifact_id] = item
            if item.artifact_id not in session.artifact_ids:
                session.artifact_ids.append(item.artifact_id)
            session.updated_at = time.time()
            self._persist_unlocked()
            return item

    def get_artifact(self, artifact_id: str) -> BrowserArtifact:
        with self._lock:
            item = self._artifacts.get(artifact_id)
            if item is None:
                raise BrowserNotFoundError("Browser artifact not found.")
            return item

    def list_artifacts(self, session_id: str) -> list[BrowserArtifact]:
        self.get_session(session_id)
        with self._lock:
            return [item for item in self._artifacts.values() if item.session_id == session_id]

    def artifact_path(self, artifact_id: str) -> Path:
        item = self.get_artifact(artifact_id)
        base = self.data_root.resolve()
        target = (base / item.relative_path).resolve(strict=False)
        if base not in target.parents or target.is_symlink():
            raise BrowserValidationError("Unsafe browser artifact path.")
        return target

    def close_session(self, session_id: str) -> BrowserSession:
        with self._lock:
            item = self.get_session(session_id)
            item.status = "closed"
            item.updated_at = time.time()
            self._persist_unlocked()
            return item

    @staticmethod
    def session_payload(item: BrowserSession) -> dict[str, Any]:
        payload = asdict(item)
        payload["grants"] = [asdict(grant) for grant in item.grants]
        return payload

    @staticmethod
    def operation_payload(item: BrowserOperation) -> dict[str, Any]:
        return asdict(item)

    @staticmethod
    def artifact_payload(item: BrowserArtifact) -> dict[str, Any]:
        payload = asdict(item)
        payload.pop("relative_path", None)
        return payload

    def _require_operation(self, operation_id: str) -> BrowserOperation:
        item = self._operations.get(operation_id)
        if item is None:
            raise BrowserNotFoundError("Browser operation not found.")
        return item

    def _expire_sessions_unlocked(self) -> bool:
        now = time.time()
        changed = False
        for item in self._sessions.values():
            if (
                item.status == "active"
                and item.expires_at is not None
                and item.expires_at <= now
            ):
                item.status = "expired"
                item.updated_at = now
                changed = True
        return changed

    def _persist_unlocked(self) -> None:
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": "browser-runtime-v1",
            "sessions": [asdict(item) for item in self._sessions.values()],
            "operations": [asdict(item) for item in self._operations.values()],
            "artifacts": [asdict(item) for item in self._artifacts.values()],
        }
        temporary = self.snapshot_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.snapshot_path)

    def _load(self) -> None:
        if not self.snapshot_path.exists():
            return
        try:
            payload = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
            for raw in payload.get("sessions", []):
                raw = dict(raw)
                raw["grants"] = [BrowserDomainGrant(**grant) for grant in raw.get("grants", [])]
                item = BrowserSession(**raw)
                self._sessions[item.session_id] = item
            for raw in payload.get("operations", []):
                item = BrowserOperation(**raw)
                if item.status == "running":
                    item.status = "failed"
                    item.error = "Browser operation was interrupted by a service restart."
                    item.completed_at = time.time()
                self._operations[item.operation_id] = item
            for raw in payload.get("artifacts", []):
                item = BrowserArtifact(**raw)
                self._artifacts[item.artifact_id] = item
        except Exception:
            self._sessions = {}
            self._operations = {}
            self._artifacts = {}

    @staticmethod
    def _required(value: Any, name: str, limit: int) -> str:
        clean = str(value or "").strip()
        if not clean:
            raise BrowserValidationError(f"{name} is required.")
        if len(clean) > limit:
            raise BrowserValidationError(f"{name} exceeds {limit} characters.")
        return clean

    @staticmethod
    def _safe_relative_path(value: str) -> str:
        clean = str(value or "").replace("\\", "/").strip("/")
        parts = Path(clean).parts
        if not clean or any(part in {"", ".", ".."} for part in parts):
            raise BrowserValidationError("Unsafe relative path.")
        return "/".join(parts)

    @staticmethod
    def _safe_url(value: str) -> tuple[str, str]:
        raw = str(value or "").strip()
        if not raw:
            return "", ""
        try:
            parsed = urlsplit(raw)
            port = parsed.port
        except ValueError:
            return "", ""
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return "", ""
        hostname = parsed.hostname.lower().rstrip(".")
        default_port = (parsed.scheme == "http" and port == 80) or (
            parsed.scheme == "https" and port == 443
        )
        netloc = hostname if port is None or default_port else f"{hostname}:{port}"
        safe = urlunsplit((parsed.scheme, netloc, parsed.path or "/", "", ""))
        return safe[:2000], hostname[:253]

    @classmethod
    def _safe_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        safe: dict[str, Any] = {}
        for key, inner in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(token in normalized for token in ("password", "secret", "token", "api_key", "authorization", "cookie", "storage", "path", "content")):
                continue
            if inner is None or isinstance(inner, (bool, int, float)):
                safe[str(key)] = inner
            elif isinstance(inner, str):
                safe[str(key)] = inner[:1000]
            elif isinstance(inner, list):
                safe[str(key)] = [str(item)[:300] for item in inner[:100]]
        return safe
