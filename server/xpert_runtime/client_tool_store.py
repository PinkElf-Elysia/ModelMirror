from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import uuid
from copy import deepcopy
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator, Literal


ClientToolRequestStatus = Literal[
    "pending",
    "dispatched",
    "running",
    "completed",
    "failed",
    "cancelled",
    "expired",
    "uncertain",
]

TERMINAL_REQUEST_STATUSES = {
    "completed",
    "failed",
    "cancelled",
    "expired",
    "uncertain",
}

MUTATING_CLIENT_TOOLS = {
    "host_page_click",
    "host_page_fill",
    "host_page_select",
    "host_page_press",
    "host_page_navigate",
    "office_powerpoint_add_slide",
    "office_powerpoint_delete_slide",
    "office_powerpoint_add_text_box",
    "office_powerpoint_add_shape",
    "office_powerpoint_update_shape",
    "office_powerpoint_delete_shape",
    "office_powerpoint_insert_image",
    "office_word_insert_text",
    "office_word_replace_selection",
    "office_word_insert_heading",
    "office_word_insert_table",
    "office_excel_set_range_values",
    "office_excel_add_worksheet",
    "office_excel_delete_worksheet",
    "office_excel_autofit_range",
    "office_excel_add_table",
}


class ClientToolError(Exception):
    """Base error for the client host bridge."""


class ClientToolNotFoundError(ClientToolError):
    """Raised when a host, request, pairing, or artifact is unavailable."""


class ClientToolConflictError(ClientToolError):
    """Raised for stale revisions or incompatible state transitions."""


class ClientToolAuthenticationError(ClientToolError):
    """Raised when a pairing code or host token is invalid."""


@dataclass(slots=True)
class ClientHost:
    host_id: str
    name: str
    token_hash: str
    token_prefix: str
    status: str = "offline"
    version: str = ""
    protocol_version: str = "modelmirror-client-tools-v1"
    capabilities: list[dict[str, Any]] = field(default_factory=list)
    schema_hashes: dict[str, str] = field(default_factory=dict)
    bound_tab: dict[str, Any] = field(default_factory=dict)
    host_type: str = "chrome"
    office_app: str = ""
    document_binding: dict[str, Any] = field(default_factory=dict)
    requirement_sets: list[str] = field(default_factory=list)
    revoked: bool = False
    connection_id: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_heartbeat_at: float | None = None


@dataclass(slots=True)
class ClientHostPairing:
    pairing_id: str
    code_hash: str
    name: str
    status: str
    created_at: float
    expires_at: float
    host_type: str = "chrome"
    consumed_at: float | None = None
    host_id: str | None = None


@dataclass(slots=True)
class ClientToolRequest:
    request_id: str
    operation_id: str
    tool_call_id: str
    host_id: str
    task_id: str
    run_id: str
    node_id: str
    scope_type: str
    scope_id: str
    tool_name: str
    arguments: dict[str, Any]
    schema_hash: str
    mutating: bool
    timeout_seconds: int
    status: ClientToolRequestStatus = "pending"
    result: str = ""
    result_metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    dispatch_count: int = 0
    dispatched_connection_id: str | None = None
    revision: int = 1
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    dispatched_at: float | None = None
    started_at: float | None = None
    completed_at: float | None = None


@dataclass(slots=True)
class ClientToolArtifact:
    artifact_id: str
    request_id: str
    host_id: str
    filename: str
    relative_path: str
    content_type: str
    size_bytes: int
    created_at: float = field(default_factory=time.time)


class ClientToolStore:
    """File-backed hosts, pairings, requests, and screenshot artifacts."""

    def __init__(
        self,
        storage_dir: str | Path | None = None,
        artifact_root: str | Path | None = None,
    ) -> None:
        package_dir = Path(__file__).resolve().parent
        self.storage_dir = Path(
            storage_dir
            or os.getenv("AGENT_TASK_STORAGE_DIR", "").strip()
            or package_dir / "storage"
        )
        self.snapshot_path = self.storage_dir / "client_tools.json"
        self.artifact_root = Path(
            artifact_root
            or os.getenv("CLIENT_TOOL_ARTIFACT_ROOT", "").strip()
            or self.storage_dir / "client_tool_artifacts"
        )
        self._lock = threading.RLock()
        self._hosts: dict[str, ClientHost] = {}
        self._pairings: dict[str, ClientHostPairing] = {}
        self._requests: dict[str, ClientToolRequest] = {}
        self._requests_by_operation: dict[str, str] = {}
        self._artifacts: dict[str, ClientToolArtifact] = {}
        self._load()

    @staticmethod
    def _hash_secret(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def create_pairing(
        self,
        *,
        name: str = "Chrome Host",
        host_type: str = "chrome",
        ttl_seconds: int = 300,
    ) -> tuple[ClientHostPairing, str]:
        code = f"{secrets.randbelow(100_000_000):08d}"
        now = time.time()
        normalized_host_type = self._host_type(host_type)
        pairing = ClientHostPairing(
            pairing_id=f"pair_{uuid.uuid4().hex}",
            code_hash=self._hash_secret(code),
            name=str(name or "Chrome Host")[:100],
            status="pending",
            created_at=now,
            expires_at=now + max(30, min(int(ttl_seconds), 300)),
            host_type=normalized_host_type,
        )
        with self._lock:
            self._expire_pairings_unlocked(now)
            self._pairings[pairing.pairing_id] = pairing
            self._persist_unlocked()
        return pairing, code

    def consume_pairing(
        self,
        code: str,
        *,
        version: str = "",
        capabilities: list[dict[str, Any]] | None = None,
        schema_hashes: dict[str, str] | None = None,
        host_type: str | None = None,
        office_app: str = "",
        document_binding: dict[str, Any] | None = None,
        requirement_sets: list[str] | None = None,
    ) -> tuple[ClientHost, str]:
        now = time.time()
        code_hash = self._hash_secret(str(code).strip())
        with self._lock:
            self._expire_pairings_unlocked(now)
            pairing = next(
                (
                    item
                    for item in self._pairings.values()
                    if item.status == "pending"
                    and hmac.compare_digest(item.code_hash, code_hash)
                ),
                None,
            )
            if pairing is None:
                raise ClientToolAuthenticationError(
                    "Pairing code is invalid or expired."
                )
            normalized_host_type = self._host_type(host_type or pairing.host_type)
            if normalized_host_type != pairing.host_type:
                raise ClientToolAuthenticationError(
                    "Pairing code belongs to another client host type."
                )
            token = secrets.token_urlsafe(48)
            host = ClientHost(
                host_id=f"host_{uuid.uuid4().hex}",
                name=pairing.name,
                token_hash=self._hash_secret(token),
                token_prefix=token[:8],
                version=str(version or "")[:80],
                capabilities=self._safe_capabilities(capabilities or []),
                schema_hashes={
                    str(key): str(value)
                    for key, value in dict(schema_hashes or {}).items()
                },
                status="online",
                last_heartbeat_at=now,
                host_type=normalized_host_type,
                office_app=self._office_app(office_app, normalized_host_type),
                document_binding=self._safe_document_binding(
                    document_binding or {}, normalized_host_type
                ),
                requirement_sets=self._safe_requirement_sets(requirement_sets or []),
            )
            pairing.status = "consumed"
            pairing.consumed_at = now
            pairing.host_id = host.host_id
            self._hosts[host.host_id] = host
            self._persist_unlocked()
            return host, token

    def authenticate(self, host_id: str, token: str) -> ClientHost:
        with self._lock:
            host = self._hosts.get(host_id)
            if host is None or host.revoked:
                raise ClientToolAuthenticationError("Client host is unavailable.")
            if not hmac.compare_digest(host.token_hash, self._hash_secret(token)):
                raise ClientToolAuthenticationError("Client host token is invalid.")
            return host

    def connect_host(
        self,
        host_id: str,
        *,
        connection_id: str,
        version: str,
        capabilities: list[dict[str, Any]],
        schema_hashes: dict[str, str],
        bound_tab: dict[str, Any] | None = None,
        host_type: str | None = None,
        office_app: str = "",
        document_binding: dict[str, Any] | None = None,
        requirement_sets: list[str] | None = None,
    ) -> ClientHost:
        with self._lock:
            host = self.require_host(host_id)
            now = time.time()
            host.status = "online"
            host.connection_id = connection_id
            host.version = str(version or "")[:80]
            host.capabilities = self._safe_capabilities(capabilities)
            host.schema_hashes = {
                str(key): str(value) for key, value in schema_hashes.items()
            }
            normalized_host_type = self._host_type(host_type or host.host_type)
            if normalized_host_type != host.host_type:
                raise ClientToolConflictError("Client host type cannot change after pairing.")
            host.bound_tab = self._safe_bound_tab(bound_tab or {})
            host.office_app = self._office_app(office_app, normalized_host_type)
            host.document_binding = self._safe_document_binding(
                document_binding or {}, normalized_host_type
            )
            host.requirement_sets = self._safe_requirement_sets(requirement_sets or [])
            host.last_heartbeat_at = now
            host.updated_at = now
            self._persist_unlocked()
            return host

    def heartbeat(
        self,
        host_id: str,
        *,
        connection_id: str,
        bound_tab: dict[str, Any] | None = None,
        document_binding: dict[str, Any] | None = None,
        office_app: str | None = None,
        requirement_sets: list[str] | None = None,
    ) -> ClientHost:
        with self._lock:
            host = self.require_host(host_id)
            if host.connection_id != connection_id:
                raise ClientToolConflictError("Client host connection was replaced.")
            host.status = "online"
            host.last_heartbeat_at = time.time()
            host.updated_at = host.last_heartbeat_at
            if bound_tab is not None:
                host.bound_tab = self._safe_bound_tab(bound_tab)
            if document_binding is not None:
                host.document_binding = self._safe_document_binding(
                    document_binding, host.host_type
                )
            if office_app is not None:
                host.office_app = self._office_app(office_app, host.host_type)
            if requirement_sets is not None:
                host.requirement_sets = self._safe_requirement_sets(requirement_sets)
            self._persist_unlocked()
            return host

    def unbind_host(self, host_id: str) -> ClientHost:
        with self._lock:
            host = self.require_host(host_id)
            if host.host_type == "office":
                host.document_binding = {}
                host.office_app = ""
            else:
                host.bound_tab = self._safe_bound_tab({})
            host.updated_at = time.time()
            self._persist_unlocked()
            return host

    def disconnect_host(self, host_id: str, *, connection_id: str) -> None:
        with self._lock:
            host = self._hosts.get(host_id)
            if host is None or host.connection_id != connection_id:
                return
            original_host = host
            originals: dict[str, ClientToolRequest] = {}
            host = deepcopy(host)
            host.status = "offline"
            host.connection_id = None
            host.updated_at = time.time()
            for request in list(self._requests.values()):
                if request.host_id != host_id or request.status not in {
                    "dispatched",
                    "running",
                }:
                    continue
                originals[request.request_id] = request
                request = deepcopy(request)
                if request.mutating:
                    self._set_request_status_unlocked(
                        request,
                        "uncertain",
                        error="修改请求派发后连接中断，执行结果不确定，禁止自动重发。",
                    )
                else:
                    request.status = "pending"
                    request.updated_at = time.time()
                    request.revision += 1
                self._requests[request.request_id] = request
            self._hosts[host_id] = host
            try:
                self._persist_unlocked()
            except BaseException:
                self._hosts[host_id] = original_host
                self._requests.update(originals)
                raise

    def revoke_host(self, host_id: str) -> ClientHost:
        with self._lock:
            host = self.require_host(host_id)
            host.revoked = True
            host.status = "revoked"
            host.connection_id = None
            host.updated_at = time.time()
            self._persist_unlocked()
            return host

    def require_host(self, host_id: str) -> ClientHost:
        host = self._hosts.get(host_id)
        if host is None:
            raise ClientToolNotFoundError("Client host not found.")
        return host

    def list_hosts(self) -> list[ClientHost]:
        with self._lock:
            items = list(self._hosts.values())
        return sorted(items, key=lambda item: (item.updated_at, item.host_id), reverse=True)

    def create_request(
        self,
        *,
        operation_id: str,
        tool_call_id: str,
        host_id: str,
        task_id: str,
        run_id: str,
        node_id: str,
        scope_type: str,
        scope_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        schema_hash: str,
        timeout_seconds: int,
    ) -> ClientToolRequest:
        with self._lock:
            host = self.require_host(host_id)
            if host.revoked:
                raise ClientToolConflictError("Client host has been revoked.")
            existing_id = self._requests_by_operation.get(operation_id)
            if existing_id:
                return self._requests[existing_id]
            now = time.time()
            request = ClientToolRequest(
                request_id=f"ctr_{uuid.uuid4().hex}",
                operation_id=str(operation_id),
                tool_call_id=str(tool_call_id),
                host_id=host_id,
                task_id=str(task_id),
                run_id=str(run_id),
                node_id=str(node_id),
                scope_type=str(scope_type),
                scope_id=str(scope_id),
                tool_name=str(tool_name),
                arguments=dict(arguments),
                schema_hash=str(schema_hash),
                mutating=tool_name in MUTATING_CLIENT_TOOLS,
                timeout_seconds=max(30, min(int(timeout_seconds), 86400)),
                expires_at=now + max(30, min(int(timeout_seconds), 86400)),
            )
            self._requests[request.request_id] = request
            self._requests_by_operation[operation_id] = request.request_id
            self._persist_unlocked()
            return request

    def get_request(self, request_id: str) -> ClientToolRequest | None:
        with self._lock:
            return self._requests.get(request_id)

    def require_request(self, request_id: str) -> ClientToolRequest:
        request = self.get_request(request_id)
        if request is None:
            raise ClientToolNotFoundError("Client tool request not found.")
        return request

    def list_requests(
        self,
        *,
        status: str | None = None,
        host_id: str | None = None,
        task_id: str | None = None,
        scope_type: str | None = None,
        scope_id: str | None = None,
        limit: int = 200,
    ) -> list[ClientToolRequest]:
        with self._lock:
            items = list(self._requests.values())
        if status:
            items = [item for item in items if item.status == status]
        if host_id:
            items = [item for item in items if item.host_id == host_id]
        if task_id:
            items = [item for item in items if item.task_id == task_id]
        if scope_type:
            items = [item for item in items if item.scope_type == scope_type]
        if scope_id:
            items = [item for item in items if item.scope_id == scope_id]
        items.sort(key=lambda item: (item.updated_at, item.request_id), reverse=True)
        return items[: max(1, min(int(limit), 1000))]

    @contextmanager
    def request_local_operation(self, *, expected_request: ClientToolRequest) -> Iterator[None]:
        """Fence a synchronous decision after acquiring the parent execution lock.

        Never await or send to a host here. This protects a read/one-Store-write
        decision, not an atomic commit across different Stores.
        """
        with self._lock:
            current = self.require_request(expected_request.request_id)
            if current != expected_request:
                raise ClientToolConflictError("客户端请求已变化，请重新核对操作许可。")
            yield

    def mark_dispatched(
        self, request_id: str, *, host_id: str | None = None,
        connection_id: str | None = None, expected_revision: int | None = None,
    ) -> ClientToolRequest:
        with self._lock:
            request = self.require_request(request_id)
            if request.status != "pending" or request.expires_at <= time.time():
                raise ClientToolConflictError("客户端请求已派发、已终止或已过期，不能再次派发。")
            if expected_revision is not None and request.revision != expected_revision:
                raise ClientToolConflictError("客户端请求已变化，请重新核对待发请求。")
            if host_id is not None or connection_id is not None:
                host = self.require_host(request.host_id)
                if (host_id != request.host_id or not connection_id
                        or host.connection_id != connection_id
                        or host.status != "online" or host.revoked):
                    raise ClientToolConflictError("客户端派发连接已失效或不属于此请求。")
            original = request
            request = deepcopy(request)
            request.status = "dispatched"
            request.dispatched_at = time.time()
            request.dispatch_count += 1
            request.dispatched_connection_id = connection_id
            if request.tool_name == "host_page_screenshot":
                # A read reconnect starts a fresh capture, retaining old artifacts
                # for historical reads but not as this dispatch's upload receipt.
                request.result_metadata.pop("artifact_id", None)
            request.updated_at = request.dispatched_at
            request.revision += 1
            self._requests[request_id] = request
            try:
                self._persist_unlocked()
            except BaseException:
                self._requests[request_id] = original
                raise
            return deepcopy(request)

    def mark_running(
        self, request_id: str, *, host_id: str,
        connection_id: str | None = None, dispatch_count: int | None = None,
    ) -> ClientToolRequest:
        with self._lock:
            request = self._require_owned_request(request_id, host_id)
            self._validate_receipt_dispatch(request, connection_id, dispatch_count)
            if request.status == "completed":
                return deepcopy(request)
            if request.status not in {"dispatched", "running"}:
                raise ClientToolConflictError(
                    f"Client request cannot run from {request.status}."
                )
            original = request
            request = deepcopy(request)
            request.status = "running"
            request.started_at = request.started_at or time.time()
            request.updated_at = time.time()
            request.revision += 1
            self._requests[request_id] = request
            try:
                self._persist_unlocked()
            except BaseException:
                self._requests[request_id] = original
                raise
            return deepcopy(request)

    def complete_request(
        self,
        request_id: str,
        *,
        host_id: str,
        operation_id: str,
        tool_call_id: str,
        result: str,
        metadata: dict[str, Any] | None = None,
        connection_id: str | None = None,
        dispatch_count: int | None = None,
    ) -> ClientToolRequest:
        with self._lock:
            request = self._require_owned_request(request_id, host_id)
            self._validate_result_identity(request, operation_id, tool_call_id)
            self._validate_receipt_dispatch(request, connection_id, dispatch_count)
            if request.status == "completed":
                return deepcopy(request)
            if request.status not in {"dispatched", "running"}:
                raise ClientToolConflictError(
                    f"Client request cannot complete from {request.status}."
                )
            original = request
            request = deepcopy(request)
            request.result = str(result or "")[:64_000]
            request.result_metadata = deepcopy(self._safe_result_metadata(metadata or {}))
            self._set_request_status_unlocked(request, "completed")
            self._requests[request_id] = request
            try:
                self._persist_unlocked()
            except BaseException:
                self._requests[request_id] = original
                raise
            return deepcopy(request)

    def fail_request(
        self,
        request_id: str,
        *,
        host_id: str,
        operation_id: str,
        tool_call_id: str,
        error: str,
        connection_id: str | None = None,
        dispatch_count: int | None = None,
    ) -> ClientToolRequest:
        with self._lock:
            request = self._require_owned_request(request_id, host_id)
            self._validate_result_identity(request, operation_id, tool_call_id)
            self._validate_receipt_dispatch(request, connection_id, dispatch_count)
            if request.status in TERMINAL_REQUEST_STATUSES:
                return deepcopy(request)
            if request.status not in {"dispatched", "running"}:
                raise ClientToolConflictError("客户端请求尚未派发，不能提交失败回执。")
            original = request
            request = deepcopy(request)
            self._set_request_status_unlocked(request, "failed", error=error)
            self._requests[request_id] = request
            try:
                self._persist_unlocked()
            except BaseException:
                self._requests[request_id] = original
                raise
            return deepcopy(request)

    def retry_request(self, request_id: str) -> ClientToolRequest:
        with self._lock:
            request = self.require_request(request_id)
            blocked = self._retry_blocked_reason(request)
            if blocked:
                raise ClientToolConflictError(blocked)
            original = request
            request = deepcopy(request)
            request.status = "pending"
            request.result = ""
            request.result_metadata = {}
            request.error = None
            request.started_at = None
            request.completed_at = None
            request.expires_at = time.time() + request.timeout_seconds
            request.updated_at = time.time()
            request.revision += 1
            self._requests[request_id] = request
            try:
                self._persist_unlocked()
            except BaseException:
                self._requests[request_id] = original
                raise
            return deepcopy(request)

    def cancel_request(self, request_id: str) -> ClientToolRequest:
        with self._lock:
            request = self.require_request(request_id)
            if request.status in TERMINAL_REQUEST_STATUSES:
                return deepcopy(request)
            original = request
            request = deepcopy(request)
            if request.mutating and self._may_have_dispatched(request):
                self._set_request_status_unlocked(request, "uncertain", error=(
                    "已停止等待；修改请求已经派发，无法确认客户端是否执行或撤销。请先核对实际结果，不得按原请求重派。"
                ))
            else:
                self._set_request_status_unlocked(request, "cancelled", error="已由本地操作者取消请求。")
            self._requests[request_id] = request
            try:
                self._persist_unlocked()
            except BaseException:
                self._requests[request_id] = original
                raise
            return deepcopy(request)

    @staticmethod
    def _may_have_dispatched(request: ClientToolRequest) -> bool:
        return (
            request.dispatch_count > 0
            or request.dispatched_at is not None
            or request.started_at is not None
            or request.status in {"dispatched", "running", "failed", "uncertain"}
        )

    @classmethod
    def _retry_blocked_reason(cls, request: ClientToolRequest) -> str | None:
        if request.status not in {"failed", "cancelled", "expired", "uncertain"}:
            return "当前请求状态不允许重新派发。"
        if request.mutating and cls._may_have_dispatched(request):
            return "修改请求可能已经执行，不能按原请求重新派发。请先核对客户端实际结果。"
        return None

    def expire_due(self, now: float | None = None) -> list[ClientToolRequest]:
        current = time.time() if now is None else float(now)
        expired: list[ClientToolRequest] = []
        with self._lock:
            originals: dict[str, ClientToolRequest] = {}
            for request in self._requests.values():
                if request.status in TERMINAL_REQUEST_STATUSES:
                    continue
                if request.expires_at > current:
                    continue
                originals[request.request_id] = request
                request = deepcopy(request)
                self._set_request_status_unlocked(
                    request, "expired", error="Client tool request expired."
                )
                self._requests[request.request_id] = request
                expired.append(request)
            if expired:
                try:
                    self._persist_unlocked()
                except BaseException:
                    self._requests.update(originals)
                    raise
        return deepcopy(expired)

    def register_artifact(
        self,
        *,
        request_id: str,
        host_id: str,
        filename: str,
        content_type: str,
        data: bytes,
        connection_id: str | None = None,
        dispatch_count: int | None = None,
    ) -> ClientToolArtifact:
        if len(data) > 5 * 1024 * 1024:
            raise ClientToolConflictError("Client tool screenshot exceeds 5 MB.")
        if content_type not in {"image/png", "image/jpeg", "image/webp"}:
            raise ClientToolConflictError("Unsupported client tool artifact MIME type.")
        safe_name = Path(filename or "client-screenshot.png").name
        if safe_name in {"", ".", ".."}:
            safe_name = "client-screenshot.png"
        artifact_id = f"cta_{uuid.uuid4().hex}"
        suffix = Path(safe_name).suffix.lower() or ".png"
        relative_path = f"{host_id}/{artifact_id}{suffix}"
        target = (self.artifact_root / relative_path).resolve()
        root = self.artifact_root.resolve()
        if root not in target.parents:
            raise ClientToolConflictError("Invalid client tool artifact path.")
        artifact = ClientToolArtifact(
            artifact_id=artifact_id,
            request_id=request_id,
            host_id=host_id,
            filename=safe_name[:180],
            relative_path=relative_path,
            content_type=content_type,
            size_bytes=len(data),
        )
        with self._lock:
            request = self._require_owned_request(request_id, host_id)
            if (request.tool_name != "host_page_screenshot"
                    or request.status not in {"dispatched", "running"}
                    or request.expires_at <= time.time() or not connection_id):
                raise ClientToolConflictError("截图请求未有效派发、已结束或已过期，不能登记产物。")
            self._validate_receipt_dispatch(request, connection_id, dispatch_count)
            previous_id = request.result_metadata.get("artifact_id")
            if previous_id:
                previous = self.require_artifact(previous_id)
                if (previous.request_id != request_id or previous.host_id != host_id
                        or previous.content_type != content_type or previous.size_bytes != len(data)
                        or self.artifact_path(previous).read_bytes() != data):
                    raise ClientToolConflictError("本次派发已有不同截图，不能覆盖已登记产物。")
                return deepcopy(previous)
            original = request
            request = deepcopy(request)
            request.result_metadata["artifact_id"] = artifact.artifact_id
            request.updated_at = time.time()
            request.revision += 1
            temporary = target.with_suffix(target.suffix + ".tmp")
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary.write_bytes(data)
                os.replace(temporary, target)
                self._artifacts[artifact.artifact_id] = artifact
                self._requests[request_id] = request
                self._persist_unlocked()
            except BaseException:
                self._requests[request_id] = original
                self._artifacts.pop(artifact.artifact_id, None)
                # Only this new upload is cleaned up. Crash leftovers are never
                # visible without a committed Store entry, nor scanned on load.
                for path in (temporary, target):
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
                raise
        return deepcopy(artifact)

    def require_artifact(self, artifact_id: str) -> ClientToolArtifact:
        with self._lock:
            artifact = self._artifacts.get(artifact_id)
            if artifact is None:
                raise ClientToolNotFoundError("Client tool artifact not found.")
            return artifact

    def artifact_path(self, artifact: ClientToolArtifact) -> Path:
        root = self.artifact_root.resolve()
        target = (root / artifact.relative_path).resolve()
        if root not in target.parents or not target.is_file():
            raise ClientToolNotFoundError("Client tool artifact file is unavailable.")
        return target

    @staticmethod
    def serialize_host(host: ClientHost) -> dict[str, Any]:
        return {
            "host_id": host.host_id,
            "name": host.name,
            "token_prefix": host.token_prefix,
            "status": host.status,
            "version": host.version,
            "protocol_version": host.protocol_version,
            "capabilities": list(host.capabilities),
            "schema_hashes": dict(host.schema_hashes),
            "bound_tab": dict(host.bound_tab),
            "host_type": host.host_type,
            "office_app": host.office_app,
            "document_binding": dict(host.document_binding),
            "requirement_sets": list(host.requirement_sets),
            "revoked": host.revoked,
            "created_at": host.created_at,
            "updated_at": host.updated_at,
            "last_heartbeat_at": host.last_heartbeat_at,
        }

    @classmethod
    def serialize_request(
        cls,
        request: ClientToolRequest,
        *,
        include_result: bool = False,
    ) -> dict[str, Any]:
        retry_blocked_reason = cls._retry_blocked_reason(request)
        payload = {
            "request_id": request.request_id,
            "operation_id": request.operation_id,
            "tool_call_id": request.tool_call_id,
            "host_id": request.host_id,
            "task_id": request.task_id,
            "run_id": request.run_id,
            "node_id": request.node_id,
            "scope_type": request.scope_type,
            "scope_id": request.scope_id,
            "tool_name": request.tool_name,
            "arguments": cls._redact_arguments(request.arguments),
            "mutating": request.mutating,
            "status": request.status,
            "retryable": retry_blocked_reason is None,
            "retry_blocked_reason": retry_blocked_reason,
            "error": request.error,
            "dispatch_count": request.dispatch_count,
            "revision": request.revision,
            "result_length": len(request.result),
            "result_metadata": cls._safe_result_metadata(request.result_metadata),
            "created_at": request.created_at,
            "updated_at": request.updated_at,
            "expires_at": request.expires_at,
            "dispatched_at": request.dispatched_at,
            "started_at": request.started_at,
            "completed_at": request.completed_at,
        }
        if include_result:
            payload["result"] = request.result[:24_000]
        return payload

    @staticmethod
    def serialize_artifact(artifact: ClientToolArtifact) -> dict[str, Any]:
        return {
            "artifact_id": artifact.artifact_id,
            "request_id": artifact.request_id,
            "host_id": artifact.host_id,
            "filename": artifact.filename,
            "content_type": artifact.content_type,
            "size_bytes": artifact.size_bytes,
            "created_at": artifact.created_at,
        }

    def _set_request_status_unlocked(
        self,
        request: ClientToolRequest,
        status: ClientToolRequestStatus,
        *,
        error: str | None = None,
    ) -> None:
        request.status = status
        request.error = str(error or "")[:2000] or None
        request.updated_at = time.time()
        request.revision += 1
        if status in TERMINAL_REQUEST_STATUSES:
            request.completed_at = request.updated_at

    def _require_owned_request(
        self, request_id: str, host_id: str
    ) -> ClientToolRequest:
        request = self.require_request(request_id)
        if request.host_id != host_id:
            raise ClientToolConflictError("Client request belongs to another host.")
        return request

    def _validate_receipt_dispatch(
        self, request: ClientToolRequest, connection_id: str | None,
        dispatch_count: int | None,
    ) -> None:
        # Legacy in-process callers have no transport identity. WebSockets always do.
        if connection_id is None and request.dispatched_connection_id is None:
            if dispatch_count is None:
                return
        host = self.require_host(request.host_id)
        if (not connection_id or request.dispatched_connection_id != connection_id
                or host.connection_id != connection_id
                or host.status != "online" or host.revoked):
            raise ClientToolConflictError("客户端回执连接已失效或不属于本次派发。")
        # Missing generations are safe only for the first dispatch on its bound socket.
        if dispatch_count is None and request.dispatch_count == 1:
            return
        if (type(dispatch_count) is not int or dispatch_count < 1
                or dispatch_count != request.dispatch_count):
            raise ClientToolConflictError("客户端回执派发代次不匹配；重派需要支持代次校验的客户端。")

    @staticmethod
    def _validate_result_identity(
        request: ClientToolRequest, operation_id: str, tool_call_id: str
    ) -> None:
        if request.operation_id != operation_id or request.tool_call_id != tool_call_id:
            raise ClientToolConflictError("Client result identity does not match request.")

    @staticmethod
    def _safe_capabilities(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        safe: list[dict[str, Any]] = []
        for item in items[:100]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            safe.append(
                {
                    "name": name[:120],
                    "description": str(item.get("description") or "")[:300],
                    "mutating": bool(item.get("mutating")),
                    "schema_hash": str(item.get("schema_hash") or "")[:128],
                }
            )
        return safe

    @staticmethod
    def _safe_bound_tab(value: dict[str, Any]) -> dict[str, Any]:
        return {
            "bound": bool(value.get("bound")),
            "tab_id": str(value.get("tab_id") or value.get("tabId") or "")[:40],
            "origin": str(value.get("origin") or "")[:500],
            "title": str(value.get("title") or "")[:300],
            "revision": int(
                value.get("revision") or value.get("snapshotRevision") or 0
            ),
        }

    @staticmethod
    def _host_type(value: Any) -> str:
        normalized = str(value or "chrome").strip().lower()
        if normalized not in {"chrome", "office"}:
            raise ClientToolConflictError("host_type must be chrome or office.")
        return normalized

    @staticmethod
    def _office_app(value: Any, host_type: str) -> str:
        if host_type != "office":
            return ""
        normalized = str(value or "").strip().lower()
        if normalized and normalized not in {"word", "excel", "powerpoint"}:
            raise ClientToolConflictError("Unsupported Office host application.")
        return normalized

    @staticmethod
    def _safe_document_binding(value: dict[str, Any], host_type: str) -> dict[str, Any]:
        if host_type != "office" or not bool(value.get("bound")):
            return {}
        return {
            "bound": True,
            "binding_id": str(
                value.get("binding_id") or value.get("documentBindingId") or ""
            )[:120],
            "title": str(value.get("title") or "")[:300],
            "revision": max(0, int(value.get("revision") or 0)),
        }

    @staticmethod
    def _safe_requirement_sets(values: list[Any]) -> list[str]:
        return sorted(
            {
                str(value).strip()[:80]
                for value in values[:40]
                if str(value).strip()
            }
        )

    @staticmethod
    def _safe_result_metadata(value: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "artifact_id",
            "content_type",
            "truncated",
            "url",
            "origin",
            "title",
            "element_count",
            "office_app",
            "document_binding_id",
            "operation_receipt",
            "result_length",
        }
        return {key: value[key] for key in allowed if key in value}

    @staticmethod
    def _redact_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
        sensitive = {"value", "text", "password", "token", "secret", "otp", "code"}
        return {
            str(key): ("[redacted]" if str(key).lower() in sensitive else value)
            for key, value in arguments.items()
        }

    def _expire_pairings_unlocked(self, now: float) -> None:
        for pairing in self._pairings.values():
            if pairing.status == "pending" and pairing.expires_at <= now:
                pairing.status = "expired"

    def _persist_unlocked(self) -> None:
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": "modelmirror-client-tools-v1",
            "hosts": [asdict(item) for item in self._hosts.values()],
            "pairings": [asdict(item) for item in self._pairings.values()],
            "requests": [asdict(item) for item in self._requests.values()],
            "artifacts": [asdict(item) for item in self._artifacts.values()],
        }
        temporary = self.snapshot_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, self.snapshot_path)

    def _load(self) -> None:
        if not self.snapshot_path.exists():
            return
        try:
            payload = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
            for raw in payload.get("hosts", []):
                raw.setdefault("host_type", "chrome")
                raw.setdefault("office_app", "")
                raw.setdefault("document_binding", {})
                raw.setdefault("requirement_sets", [])
                item = ClientHost(**raw)
                item.status = "revoked" if item.revoked else "offline"
                item.connection_id = None
                self._hosts[item.host_id] = item
            for raw in payload.get("pairings", []):
                raw.setdefault("host_type", "chrome")
                item = ClientHostPairing(**raw)
                self._pairings[item.pairing_id] = item
            for raw in payload.get("requests", []):
                item = ClientToolRequest(**raw)
                if item.status in {"dispatched", "running"}:
                    item.status = "uncertain" if item.mutating else "pending"
                    item.error = (
                        "Service restarted during a mutating client operation."
                        if item.mutating
                        else None
                    )
                self._requests[item.request_id] = item
                self._requests_by_operation[item.operation_id] = item.request_id
            for raw in payload.get("artifacts", []):
                item = ClientToolArtifact(**raw)
                self._artifacts[item.artifact_id] = item
        except Exception:
            self._hosts = {}
            self._pairings = {}
            self._requests = {}
            self._requests_by_operation = {}
            self._artifacts = {}
