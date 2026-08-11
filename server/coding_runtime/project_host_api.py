from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import secrets
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import APIRouter, Header, HTTPException, Request, Response, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, ConfigDict, Field

from .applier_client import _receipt_from_response, _receipt_to_payload
from .apply_models import ApplyReceipt
from .committer_client import (
    _commit_receipt_from_response,
    _commit_receipt_to_payload,
)
from .commit_models import CommitReceipt
from .host_snapshot import MAX_HOST_ARCHIVE_BYTES
from .project_host import (
    PROJECT_HOST_PLATFORM,
    PROJECT_HOST_PROTOCOL_V2,
    PROJECT_HOST_V2_CAPABILITIES,
    SUPPORTED_PROJECT_HOST_PROTOCOLS,
    PROJECT_ID_PATTERN,
    ProjectHostError,
    ProjectHostStore,
)
from .project_writer_client import ProjectWriterClientError


PROJECT_HOST_REQUEST_TIMEOUT_SECONDS = 150.0
PROJECT_HOST_MAX_MESSAGE_BYTES = 256 * 1024
UPLOAD_ROOT_MARKER = ".modelmirror-coding-project-uploads"
SAFE_TRANSFER_ID = re.compile(r"^[a-f0-9]{32}$")
SAFE_PAYLOAD_ID = re.compile(r"^phop_[a-f0-9]{32}$")
SAFE_OPERATION_ID = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
SAFE_OPERATION_ACTIONS = frozenset({"apply", "revert", "commit", "undo", "reconcile"})
OPERATION_PAYLOAD_TTL_SECONDS = 90.0
MAX_OPERATION_PAYLOAD_BYTES = 1200 * 1024
logger = logging.getLogger(__name__)


class PairingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="本地项目助手", min_length=1, max_length=80)


class ProjectRenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)


@dataclass(slots=True)
class _Transfer:
    transfer_id: str
    request_id: str
    host_id: str
    project_id: str
    status: str
    created_at: float
    size: int = 0
    sha256: str | None = None
    expected_head: str | None = None
    expected_branch: str | None = None
    managed_operation_id: str | None = None


@dataclass(slots=True)
class _OperationPayload:
    payload_id: str
    host_id: str
    project_id: str
    operation_id: str
    action: str
    created_at: float
    expires_at: float
    sha256: str
    size: int
    body: bytes | None
    consumed: bool = False


@dataclass(frozen=True, slots=True)
class _ManagedOperation:
    project_id: str
    operation_id: str
    kind: str
    branch: str
    expected_head: str


class ProjectHostRuntime:
    def __init__(
        self,
        store: ProjectHostStore,
        upload_root: Path,
        *,
        writeback_enabled: bool = False,
    ) -> None:
        self.store = store
        self.upload_root = Path(upload_root)
        self.writeback_enabled = bool(writeback_enabled)
        self._prepare_upload_root()
        self._connections: dict[str, WebSocket] = {}
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._pending_hosts: dict[str, str] = {}
        self._pending_operations: dict[str, tuple[str, str, str]] = {}
        self._transfers: dict[str, _Transfer] = {}
        self._operation_payloads: dict[str, _OperationPayload] = {}
        self._uncertain_operations: set[tuple[str, str, str]] = set()
        self._managed_operations: dict[tuple[str, str], _ManagedOperation] = {}
        self._request_tombstones: dict[str, float] = {}
        self._lock = asyncio.Lock()

    def capability(self, *, enabled: bool = True, reason: str | None = None) -> dict[str, Any]:
        status_payload = self.store.host_status()
        direct_writeback = bool(
            enabled
            and self.writeback_enabled
            and status_payload["available"]
            and status_payload.get("direct_writeback") is True
        )
        writeback_reason: str | None = None
        if not self.writeback_enabled:
            writeback_reason = "project_host_writeback_disabled"
        elif status_payload.get("direct_writeback") is not True:
            writeback_reason = "project_host_protocol_readonly"
        elif not status_payload["available"]:
            writeback_reason = reason or "project_host_offline"
        return {
            "enabled": enabled,
            "paired": status_payload["paired"],
            "available": status_payload["available"],
            "platform": PROJECT_HOST_PLATFORM,
            "selection": True,
            "remembers_projects": True,
            "direct_writeback": direct_writeback,
            "writeback_available": direct_writeback,
            **({"writeback_reason": writeback_reason} if writeback_reason else {}),
            **({"reason": reason or "project_host_offline"} if not status_payload["available"] else {}),
        }

    def status(self) -> dict[str, Any]:
        status_payload = self.store.host_status()
        capability = self.capability()
        return {
            **status_payload,
            "direct_writeback": capability["direct_writeback"],
            "writeback_available": capability["writeback_available"],
            **(
                {"writeback_reason": capability["writeback_reason"]}
                if capability.get("writeback_reason") is not None
                else {}
            ),
        }

    def list_projects(self) -> list[dict[str, Any]]:
        return [self._writeback_project(item) for item in self.store.list_projects()]

    def public_project(self, project_id: str) -> dict[str, Any]:
        project = self.store.require_project(project_id)
        host = self.store.require_host(project.host_id)
        return self._writeback_project(
            project.to_public_dict(host_online=host.status == "online")
        )

    def worker_source(self, project_id: str) -> dict[str, str]:
        project = self.store.require_project(project_id)
        host = self.store.require_host(project.host_id)
        if host.status != "online":
            raise ProjectHostError("project_host_offline")
        if project.state != "available":
            raise ProjectHostError(project.reason or "project_changed")
        return {
            "source_id": project.project_id,
            "name": project.name,
            "branch": project.branch,
            "revision": project.head,
        }

    def check_project(self, project_id: str, head: str, branch: str | None) -> dict[str, Any]:
        project = self.store.require_project(project_id)
        host = self.store.require_host(project.host_id)
        if host.status != "online":
            raise ProjectHostError("project_host_offline")
        if project.state != "available" or project.head != head or (branch is not None and project.branch != branch):
            raise ProjectHostError("project_changed")
        return self._writeback_project(project.to_public_dict(host_online=True))

    async def health(self) -> dict[str, Any]:
        capability = self.capability()
        return {
            "configured": self.writeback_enabled,
            "available": capability["direct_writeback"],
            "target": "selected_local_repository",
            "supports_delete": True,
            "supports_move": True,
            "supports_revert": True,
            "supports_commit": True,
            "remote_operations": False,
            **(
                {"reason": capability["writeback_reason"]}
                if capability.get("writeback_reason") is not None
                else {}
            ),
        }

    def bind_recovery_operations(
        self,
        *,
        project_id: str,
        expected_head: str,
        expected_branch: str,
        apply_operation_id: str | None = None,
        commit_operation_id: str | None = None,
        apply_receipt: ApplyReceipt | None,
        commit_receipt: CommitReceipt | None,
    ) -> None:
        """Rehydrate only receipts already authenticated by encrypted recovery."""
        if apply_receipt is None and commit_receipt is not None:
            raise ProjectHostError("recovery_snapshot_invalid")
        bound_apply_id = apply_operation_id or (
            apply_receipt.apply_id if apply_receipt is not None else None
        )
        if apply_receipt is not None and bound_apply_id != apply_receipt.apply_id:
            raise ProjectHostError("recovery_snapshot_invalid")
        if bound_apply_id is not None:
            self._remember_managed_operation(
                project_id=project_id,
                operation_id=bound_apply_id,
                kind="apply",
                branch=expected_branch,
                expected_head=expected_head,
            )
        if commit_receipt is not None:
            if (
                apply_receipt is None
                or commit_receipt.apply_id != apply_receipt.apply_id
                or commit_receipt.revision != apply_receipt.revision
                or commit_receipt.files
                != tuple(item.path for item in apply_receipt.files)
                or commit_receipt.branch != expected_branch
                or commit_receipt.parent_sha != expected_head
            ):
                raise ProjectHostError("recovery_snapshot_invalid")
            bound_commit_id = commit_operation_id or commit_receipt.commit_id
            if bound_commit_id != commit_receipt.commit_id:
                raise ProjectHostError("recovery_snapshot_invalid")
            self._remember_managed_operation(
                project_id=project_id,
                operation_id=bound_commit_id,
                kind="commit",
                branch=expected_branch,
                expected_head=expected_head,
            )
        elif commit_operation_id is not None:
            if bound_apply_id is None:
                raise ProjectHostError("recovery_snapshot_invalid")
            self._remember_managed_operation(
                project_id=project_id,
                operation_id=commit_operation_id,
                kind="commit",
                branch=expected_branch,
                expected_head=expected_head,
            )

    def bind_persisted_intent(
        self,
        *,
        project_id: str,
        expected_head: str,
        expected_branch: str,
        operation_id: str,
        kind: str,
        parent_operation_id: str | None = None,
    ) -> None:
        """Bind an intent only after CodingService durably stored it."""
        project = self.store.require_project(project_id)
        host = self.store.require_host(project.host_id)
        if (
            not self.writeback_enabled
            or not host.supports_writeback
            or host.status != "online"
            or project.branch != expected_branch
        ):
            raise ProjectHostError("project_changed")
        if kind == "commit":
            if parent_operation_id is None:
                raise ProjectHostError("operation_request_invalid")
            parent = self._require_managed_operation(
                project_id=project_id,
                operation_id=parent_operation_id,
                branch=expected_branch,
                expected_head=expected_head,
            )
            if parent.kind != "apply":
                raise ProjectHostError("operation_conflict")
        elif kind == "apply":
            if (
                parent_operation_id is not None
                or project.state != "available"
                or project.head != expected_head
            ):
                raise ProjectHostError("project_changed")
        else:
            raise ProjectHostError("operation_request_invalid")
        self._remember_managed_operation(
            project_id=project_id,
            operation_id=operation_id,
            kind=kind,
            branch=expected_branch,
            expected_head=expected_head,
        )

    async def apply(
        self,
        *,
        project_id: str,
        expected_head: str,
        operation_id: str,
        revision: int,
        patch: str,
        paths: list[str],
        expected_fingerprint: str,
        expected_branch: str,
    ) -> ApplyReceipt:
        intent = self._require_managed_operation(
            project_id=project_id,
            operation_id=operation_id,
            branch=expected_branch,
            expected_head=expected_head,
        )
        if intent.kind != "apply":
            raise _writer_error("operation_conflict")
        payload = {
            "kind": "apply",
            "revision": revision,
            "expected_head": expected_head,
            "snapshot_fingerprint": expected_fingerprint,
            "patch": patch,
            "paths": paths,
        }
        try:
            result = await self._execute_operation(
                project_id=project_id,
                operation_id=operation_id,
                action="apply",
                payload=payload,
                expected_head=expected_head,
                expected_branch=expected_branch,
                managed_operation_id=operation_id,
            )
        except ProjectWriterClientError as exc:
            if exc.code != "operation_result_unknown":
                self._forget_managed_operation(project_id, operation_id)
            raise
        try:
            receipt = _operation_apply_receipt(result, expected_state="applied")
            valid_receipt = bool(
                receipt.apply_id == operation_id
                and receipt.revision == revision
                and receipt.snapshot_fingerprint == expected_fingerprint
                and tuple(item.path for item in receipt.files) == tuple(paths)
            )
        except ProjectWriterClientError:
            valid_receipt = False
            receipt = None
        if not valid_receipt or receipt is None:
            self._uncertain_operations.add((project_id, operation_id, "apply"))
            raise _writer_error("operation_result_unknown")
        self._remember_managed_operation(
            project_id=project_id,
            operation_id=operation_id,
            kind="apply",
            branch=expected_branch,
            expected_head=expected_head,
        )
        self._update_project_catalog(
            project_id=project_id,
            expected_branch=expected_branch,
            allowed_heads={expected_head},
            head=expected_head,
            state="unavailable",
            reason="git_repository_dirty",
            operation_id=operation_id,
            action="apply",
        )
        return receipt

    async def revert(
        self,
        *,
        project_id: str,
        expected_head: str,
        expected_branch: str,
        receipt: ApplyReceipt,
    ) -> ApplyReceipt:
        operation_id = _followup_operation_id("revert", receipt.apply_id)
        result = await self._execute_operation(
            project_id=project_id,
            operation_id=operation_id,
            action="revert",
            payload={
                "kind": "revert",
                "expected_head": expected_head,
                "apply_receipt": _receipt_to_payload(receipt),
            },
            expected_head=expected_head,
            expected_branch=expected_branch,
            managed_operation_id=receipt.apply_id,
        )
        try:
            reverted = _operation_apply_receipt(result, expected_state="reverted")
        except ProjectWriterClientError:
            reverted = None
        if reverted != receipt:
            self._uncertain_operations.add((project_id, operation_id, "revert"))
            raise _writer_error("operation_result_unknown")
        self._update_project_catalog(
            project_id=project_id,
            expected_branch=expected_branch,
            allowed_heads={expected_head},
            head=expected_head,
            state="available",
            reason=None,
            operation_id=operation_id,
            action="revert",
        )
        return reverted

    async def commit(
        self,
        *,
        project_id: str,
        expected_head: str,
        operation_id: str,
        apply_receipt: ApplyReceipt,
        message: str,
        expected_branch: str,
    ) -> CommitReceipt:
        intent = self._require_managed_operation(
            project_id=project_id,
            operation_id=operation_id,
            branch=expected_branch,
            expected_head=expected_head,
        )
        if intent.kind != "commit":
            raise _writer_error("operation_conflict")
        try:
            result = await self._execute_operation(
                project_id=project_id,
                operation_id=operation_id,
                action="commit",
                payload={
                    "kind": "commit",
                    "expected_head": expected_head,
                    "apply_receipt": _receipt_to_payload(apply_receipt),
                    "message": message,
                },
                expected_head=expected_head,
                expected_branch=expected_branch,
                managed_operation_id=apply_receipt.apply_id,
            )
        except ProjectWriterClientError as exc:
            if exc.code != "operation_result_unknown":
                self._forget_managed_operation(project_id, operation_id)
            raise
        try:
            receipt = _operation_commit_receipt(result, expected_state="committed")
            valid_receipt = _commit_receipt_matches(
                receipt,
                operation_id=operation_id,
                apply_receipt=apply_receipt,
                expected_head=expected_head,
                expected_branch=expected_branch,
                message=message,
            )
        except ProjectWriterClientError:
            valid_receipt = False
            receipt = None
        if not valid_receipt or receipt is None:
            self._uncertain_operations.add((project_id, operation_id, "commit"))
            raise _writer_error("operation_result_unknown")
        self._remember_managed_operation(
            project_id=project_id,
            operation_id=operation_id,
            kind="commit",
            branch=expected_branch,
            expected_head=expected_head,
        )
        self._update_project_catalog(
            project_id=project_id,
            expected_branch=expected_branch,
            allowed_heads={expected_head, receipt.commit_sha},
            head=receipt.commit_sha,
            state="available",
            reason=None,
            operation_id=operation_id,
            action="commit",
        )
        return receipt

    async def undo(
        self,
        *,
        project_id: str,
        expected_head: str,
        apply_receipt: ApplyReceipt,
        commit_receipt: CommitReceipt,
        expected_branch: str,
    ) -> CommitReceipt:
        operation_id = _followup_operation_id("undo", commit_receipt.commit_id)
        result = await self._execute_operation(
            project_id=project_id,
            operation_id=operation_id,
            action="undo",
            payload={
                "kind": "undo",
                "expected_head": expected_head,
                "apply_receipt": _receipt_to_payload(apply_receipt),
                "commit_receipt": _commit_receipt_to_payload(commit_receipt),
            },
            expected_head=expected_head,
            expected_branch=expected_branch,
            managed_operation_id=commit_receipt.commit_id,
        )
        try:
            undone = _operation_commit_receipt(result, expected_state="undone")
        except ProjectWriterClientError:
            undone = None
        if undone != commit_receipt:
            self._uncertain_operations.add((project_id, operation_id, "undo"))
            raise _writer_error("operation_result_unknown")
        self._update_project_catalog(
            project_id=project_id,
            expected_branch=expected_branch,
            allowed_heads={expected_head, commit_receipt.commit_sha},
            head=expected_head,
            state="unavailable",
            reason="git_repository_dirty",
            operation_id=operation_id,
            action="undo",
        )
        return undone

    async def reconcile_apply(
        self,
        *,
        project_id: str,
        expected_head: str,
        operation_id: str,
        revision: int,
        patch: str,
        paths: list[str],
        expected_fingerprint: str,
        expected_branch: str,
    ) -> tuple[str, ApplyReceipt | None]:
        result = await self._execute_operation(
            project_id=project_id,
            operation_id=operation_id,
            action="reconcile",
            payload={
                "kind": "apply",
                "revision": revision,
                "expected_head": expected_head,
                "snapshot_fingerprint": expected_fingerprint,
                "patch_sha256": hashlib.sha256(patch.encode("utf-8")).hexdigest(),
                "paths": paths,
            },
            expected_head=expected_head,
            expected_branch=expected_branch,
            managed_operation_id=operation_id,
        )
        state = result.get("state")
        if state not in {"not_applied", "applied", "conflict"}:
            raise _writer_error("invalid_response")
        receipt_payload = result.get("receipt")
        if state == "applied":
            receipt = _operation_apply_receipt(result, expected_state=state)
            if (
                receipt.apply_id != operation_id
                or receipt.revision != revision
                or receipt.snapshot_fingerprint != expected_fingerprint
                or tuple(item.path for item in receipt.files) != tuple(paths)
            ):
                raise _writer_error("operation_result_unknown")
            self._remember_managed_operation(
                project_id=project_id,
                operation_id=operation_id,
                kind="apply",
                branch=expected_branch,
                expected_head=expected_head,
            )
            self._update_project_catalog(
                project_id=project_id,
                expected_branch=expected_branch,
                allowed_heads={expected_head},
                head=expected_head,
                state="unavailable",
                reason="git_repository_dirty",
            )
            self._clear_apply_uncertain(project_id, operation_id)
            return state, receipt
        if receipt_payload is not None:
            raise _writer_error("invalid_response")
        if state == "not_applied":
            self._update_project_catalog(
                project_id=project_id,
                expected_branch=expected_branch,
                allowed_heads={expected_head},
                head=expected_head,
                state="available",
                reason=None,
            )
            self._clear_apply_uncertain(project_id, operation_id)
        return state, None

    async def reconcile_commit(
        self,
        *,
        project_id: str,
        expected_head: str,
        operation_id: str,
        revision: int,
        patch: str,
        paths: list[str],
        expected_fingerprint: str,
        apply_receipt: ApplyReceipt,
        commit_operation_id: str,
        message: str,
        expected_branch: str,
    ) -> tuple[str, ApplyReceipt, CommitReceipt | None]:
        result = await self._execute_operation(
            project_id=project_id,
            operation_id=commit_operation_id,
            action="reconcile",
            payload={
                "kind": "commit",
                "apply_operation_id": operation_id,
                "revision": revision,
                "expected_head": expected_head,
                "snapshot_fingerprint": expected_fingerprint,
                "patch_sha256": hashlib.sha256(patch.encode("utf-8")).hexdigest(),
                "paths": paths,
                "apply_receipt": _receipt_to_payload(apply_receipt),
                "message": message,
            },
            expected_head=expected_head,
            expected_branch=expected_branch,
            managed_operation_id=apply_receipt.apply_id,
        )
        state = result.get("state")
        if state not in {"not_committed", "committed", "undone"}:
            raise _writer_error("invalid_response")
        restored_apply = _operation_apply_receipt(
            {"state": "applied", "receipt": result.get("apply_receipt")},
            expected_state="applied",
        )
        if restored_apply != apply_receipt:
            raise _writer_error("operation_result_unknown")
        commit_payload = result.get("commit_receipt")
        if state == "not_committed":
            if commit_payload is not None:
                raise _writer_error("invalid_response")
            self._update_project_catalog(
                project_id=project_id,
                expected_branch=expected_branch,
                allowed_heads={expected_head},
                head=expected_head,
                state="unavailable",
                reason="git_repository_dirty",
            )
            self._clear_commit_uncertain(project_id, commit_operation_id)
            return state, restored_apply, None
        commit_receipt = _operation_commit_receipt(
            {"state": state, "receipt": commit_payload},
            expected_state=state,
        )
        if not _commit_receipt_matches(
            commit_receipt,
            operation_id=commit_operation_id,
            apply_receipt=apply_receipt,
            expected_head=expected_head,
            expected_branch=expected_branch,
            message=message,
        ):
            raise _writer_error("operation_result_unknown")
        self._remember_managed_operation(
            project_id=project_id,
            operation_id=commit_operation_id,
            kind="commit",
            branch=expected_branch,
            expected_head=expected_head,
        )
        if state == "committed":
            self._update_project_catalog(
                project_id=project_id,
                expected_branch=expected_branch,
                allowed_heads={expected_head, commit_receipt.commit_sha},
                head=commit_receipt.commit_sha,
                state="available",
                reason=None,
            )
        else:
            self._update_project_catalog(
                project_id=project_id,
                expected_branch=expected_branch,
                allowed_heads={expected_head, commit_receipt.commit_sha},
                head=expected_head,
                state="unavailable",
                reason="git_repository_dirty",
            )
        self._clear_commit_uncertain(project_id, commit_operation_id)
        return state, restored_apply, commit_receipt

    async def create_selection(self) -> dict[str, Any]:
        selection = self.store.create_selection()
        await self._send(
            selection.host_id,
            {"type": "select_project", "request_id": selection.request_id},
        )
        return selection.to_public_dict()

    async def reconnect(self) -> dict[str, Any]:
        status_payload = self.store.host_status()
        host_id = status_payload.get("host_id")
        if not isinstance(host_id, str) or not host_id:
            raise ProjectHostError("project_host_not_found")
        async with self._lock:
            connection = self._connections.get(host_id)
        if connection is None:
            raise ProjectHostError("project_host_offline")
        try:
            await connection.send_json({"type": "heartbeat"})
        except Exception as exc:
            raise ProjectHostError("project_host_offline") from exc
        for _attempt in range(10):
            await asyncio.sleep(0.1)
            refreshed = self.store.host_status()
            if refreshed.get("available") is True:
                return refreshed
        raise ProjectHostError("project_host_offline")

    def selection(self, request_id: str) -> dict[str, Any]:
        return self.store.require_selection(request_id).to_public_dict()

    async def rename_project(self, project_id: str, name: str) -> dict[str, Any]:
        project = self.store.require_project(project_id)
        await self._command(
            project.host_id,
            {"type": "rename_project", "project_id": project_id, "name": name},
        )
        updated = self.store.rename_project(project_id, name)
        return updated.to_public_dict(host_online=True)

    async def remove_project(self, project_id: str) -> dict[str, Any]:
        project = self.store.require_project(project_id)
        await self._command(
            project.host_id,
            {"type": "remove_project", "project_id": project_id},
        )
        self.store.remove_project(project_id)
        return {"removed": True}

    async def request_snapshot(
        self,
        project_id: str,
        *,
        expected_head: str | None = None,
        expected_branch: str | None = None,
        managed_operation_id: str | None = None,
    ) -> dict[str, Any]:
        project = self.store.require_project(project_id)
        host = self.store.require_host(project.host_id)
        if (
            managed_operation_id is not None
            and SAFE_OPERATION_ID.fullmatch(managed_operation_id) is None
        ):
            raise ProjectHostError("operation_request_invalid")
        if host.status != "online":
            raise ProjectHostError("project_host_offline")
        if managed_operation_id is not None:
            binding = self._require_managed_operation(
                project_id=project_id,
                operation_id=managed_operation_id,
                branch=expected_branch,
                expected_head=expected_head,
            )
            if binding.kind != "apply" or project.branch != expected_branch:
                raise ProjectHostError("operation_conflict")
        else:
            if project.state != "available":
                raise ProjectHostError(project.reason or "project_changed")
            if expected_head is not None and project.head != expected_head:
                raise ProjectHostError("project_changed")
            if expected_branch is not None and project.branch != expected_branch:
                raise ProjectHostError("project_changed")
        request_id = f"phreq_{uuid.uuid4().hex}"
        transfer_id = secrets.token_hex(16)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        transfer = _Transfer(
            transfer_id=transfer_id,
            request_id=request_id,
            host_id=host.host_id,
            project_id=project_id,
            status="awaiting_upload",
            created_at=time.time(),
            expected_head=expected_head,
            expected_branch=expected_branch,
            managed_operation_id=managed_operation_id,
        )
        async with self._lock:
            self._pending[request_id] = future
            self._pending_hosts[request_id] = host.host_id
            self._transfers[transfer_id] = transfer
        completed = False
        try:
            await self._send(
                host.host_id,
                {
                    "type": "snapshot_project",
                    "request_id": request_id,
                    "project_id": project_id,
                    "transfer_id": transfer_id,
                    **(
                        {"expected_head": expected_head}
                        if expected_head is not None
                        else {}
                    ),
                    **(
                        {"expected_branch": expected_branch}
                        if expected_branch is not None
                        else {}
                    ),
                    **(
                        {"managed_operation_id": managed_operation_id}
                        if managed_operation_id is not None
                        else {}
                    ),
                },
            )
            result = await asyncio.wait_for(
                asyncio.shield(future),
                timeout=PROJECT_HOST_REQUEST_TIMEOUT_SECONDS,
            )
            completed = True
            return result
        except TimeoutError as exc:
            raise ProjectHostError("project_host_snapshot_timeout") from exc
        finally:
            async with self._lock:
                self._pending.pop(request_id, None)
                self._pending_hosts.pop(request_id, None)
                if not future.done():
                    future.cancel()
            if not completed:
                self.finish_transfer(transfer_id)

    def finish_transfer(self, transfer_id: str) -> None:
        transfer = self._transfers.pop(transfer_id, None)
        if transfer is not None:
            (self.upload_root / f"{transfer_id}.uploading").unlink(missing_ok=True)
            (self.upload_root / f"{transfer_id}.tar.gz").unlink(missing_ok=True)

    async def consume_operation_payload(
        self,
        *,
        payload_id: str,
        host_id: str,
        token: str,
        project_id: str,
        operation_id: str,
        action: str,
    ) -> bytes:
        self.store.authenticate(host_id, token)
        if (
            SAFE_PAYLOAD_ID.fullmatch(payload_id) is None
            or PROJECT_ID_PATTERN.fullmatch(project_id) is None
            or SAFE_OPERATION_ID.fullmatch(operation_id) is None
            or action not in SAFE_OPERATION_ACTIONS
        ):
            raise ProjectHostError("operation_payload_invalid")
        now = time.time()
        async with self._lock:
            self._expire_operation_payloads_unlocked(now)
            payload = self._operation_payloads.get(payload_id)
            if payload is None:
                raise ProjectHostError("operation_payload_unavailable")
            if (
                payload.host_id != host_id
                or payload.project_id != project_id
                or payload.operation_id != operation_id
                or payload.action != action
            ):
                raise ProjectHostError("operation_payload_mismatch")
            if payload.consumed or payload.body is None:
                raise ProjectHostError("operation_payload_consumed")
            body = payload.body
            payload.body = None
            payload.consumed = True
        return body

    async def _execute_operation(
        self,
        *,
        project_id: str,
        operation_id: str,
        action: str,
        payload: dict[str, Any],
        expected_head: str | None = None,
        expected_branch: str | None = None,
        managed_operation_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            if (
                action != "reconcile"
                and (project_id, operation_id, action) in self._uncertain_operations
            ):
                raise ProjectHostError("operation_result_unknown")
            project, host = self._operation_context(
                project_id,
                operation_id,
                action,
                expected_head=expected_head,
                expected_branch=expected_branch,
                managed_operation_id=managed_operation_id,
            )
            bound_head = expected_head or project.head
            bound_branch = expected_branch or project.branch
            body = json.dumps(
                {
                    "version": 1,
                    "host_id": host.host_id,
                    "project_id": project_id,
                    "operation_id": operation_id,
                    "action": action,
                    "branch": bound_branch,
                    "head": bound_head,
                    "payload": payload,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            if not 0 < len(body) <= MAX_OPERATION_PAYLOAD_BYTES:
                raise ProjectHostError("operation_payload_too_large")
            payload_id = f"phop_{uuid.uuid4().hex}"
            digest = hashlib.sha256(body).hexdigest()
            now = time.time()
            item = _OperationPayload(
                payload_id=payload_id,
                host_id=host.host_id,
                project_id=project_id,
                operation_id=operation_id,
                action=action,
                created_at=now,
                expires_at=now + OPERATION_PAYLOAD_TTL_SECONDS,
                sha256=digest,
                size=len(body),
                body=body,
            )
            async with self._lock:
                self._expire_operation_payloads_unlocked(now)
                self._operation_payloads[payload_id] = item
            try:
                result = await self._command(
                    host.host_id,
                    {
                        "type": "execute_operation",
                        "project_id": project_id,
                        "operation_id": operation_id,
                        "action": action,
                        "payload_id": payload_id,
                        "payload_sha256": digest,
                        "payload_size": len(body),
                        "payload_expires_at": item.expires_at,
                    },
                    timeout=PROJECT_HOST_REQUEST_TIMEOUT_SECONDS,
                )
            except ProjectHostError as exc:
                if exc.code in {
                    "project_host_offline",
                    "project_host_request_timeout",
                    "project_host_request_unknown",
                    "operation_result_unknown",
                }:
                    self._uncertain_operations.add((project_id, operation_id, action))
                    raise ProjectHostError("operation_result_unknown") from exc
                raise
            finally:
                async with self._lock:
                    self._operation_payloads.pop(payload_id, None)
            if (
                result.get("type") != "operation_result"
                or result.get("project_id") != project_id
                or result.get("operation_id") != operation_id
                or result.get("action") != action
                or not isinstance(result.get("result"), dict)
            ):
                self._uncertain_operations.add((project_id, operation_id, action))
                raise ProjectHostError("operation_result_unknown")
            self._uncertain_operations.discard((project_id, operation_id, action))
            return result["result"]
        except ProjectWriterClientError:
            raise
        except ProjectHostError as exc:
            raise _writer_error(exc.code) from exc

    def _operation_context(
        self,
        project_id: str,
        operation_id: str,
        action: str,
        *,
        expected_head: str | None,
        expected_branch: str | None,
        managed_operation_id: str | None = None,
    ) -> tuple[Any, Any]:
        if (
            PROJECT_ID_PATTERN.fullmatch(project_id) is None
            or SAFE_OPERATION_ID.fullmatch(operation_id) is None
            or action not in SAFE_OPERATION_ACTIONS
        ):
            raise ProjectHostError("operation_request_invalid")
        project = self.store.require_project(project_id)
        host = self.store.require_host(project.host_id)
        if not self.writeback_enabled:
            raise ProjectHostError("project_host_writeback_disabled")
        if not host.supports_writeback:
            raise ProjectHostError("project_host_protocol_readonly")
        if host.status != "online":
            raise ProjectHostError("project_host_offline")
        if expected_branch is not None and project.branch != expected_branch:
            raise ProjectHostError("project_changed")
        if action == "apply" and expected_head is not None and project.head != expected_head:
            raise ProjectHostError("project_changed")
        if managed_operation_id is not None:
            self._require_managed_operation(
                project_id=project_id,
                operation_id=managed_operation_id,
                branch=expected_branch,
                expected_head=expected_head,
            )
        elif project.state != "available":
            raise ProjectHostError(project.reason or "project_changed")
        return project, host

    def _remember_managed_operation(
        self,
        *,
        project_id: str,
        operation_id: str,
        kind: str,
        branch: str,
        expected_head: str,
    ) -> None:
        if (
            PROJECT_ID_PATTERN.fullmatch(project_id) is None
            or SAFE_OPERATION_ID.fullmatch(operation_id) is None
            or kind not in {"apply", "commit"}
            or not branch
            or re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", expected_head)
            is None
        ):
            raise ProjectHostError("operation_request_invalid")
        binding = _ManagedOperation(
            project_id=project_id,
            operation_id=operation_id,
            kind=kind,
            branch=branch,
            expected_head=expected_head,
        )
        key = (project_id, operation_id)
        existing = self._managed_operations.get(key)
        if existing is not None and existing != binding:
            raise ProjectHostError("operation_conflict")
        self._managed_operations[key] = binding

    def _require_managed_operation(
        self,
        *,
        project_id: str,
        operation_id: str,
        branch: str | None,
        expected_head: str | None,
    ) -> _ManagedOperation:
        binding = self._managed_operations.get((project_id, operation_id))
        if (
            binding is None
            or branch is None
            or expected_head is None
            or binding.branch != branch
            or binding.expected_head != expected_head
        ):
            raise ProjectHostError("operation_conflict")
        return binding

    def _clear_apply_uncertain(self, project_id: str, operation_id: str) -> None:
        self._uncertain_operations.discard((project_id, operation_id, "apply"))
        self._uncertain_operations.discard(
            (project_id, _followup_operation_id("revert", operation_id), "revert")
        )

    def _clear_commit_uncertain(self, project_id: str, operation_id: str) -> None:
        self._uncertain_operations.discard((project_id, operation_id, "commit"))
        self._uncertain_operations.discard(
            (project_id, _followup_operation_id("undo", operation_id), "undo")
        )

    def _forget_managed_operation(self, project_id: str, operation_id: str) -> None:
        self._managed_operations.pop((project_id, operation_id), None)

    def _update_project_catalog(
        self,
        *,
        project_id: str,
        expected_branch: str,
        allowed_heads: set[str],
        head: str,
        state: str,
        reason: str | None,
        operation_id: str | None = None,
        action: str | None = None,
    ) -> None:
        """Advance only catalog state proven by an authenticated operation result."""
        try:
            project = self.store.require_project(project_id)
            if project.branch != expected_branch or project.head not in allowed_heads:
                raise ProjectHostError("project_changed")
            self.store.register_project(
                project.host_id,
                {
                    "project_id": project.project_id,
                    "name": project.name,
                    "branch": expected_branch,
                    "head": head,
                    "state": state,
                    "reason": reason,
                },
            )
        except (OSError, ProjectHostError) as exc:
            if operation_id is not None and action is not None:
                self._uncertain_operations.add((project_id, operation_id, action))
            raise _writer_error("operation_result_unknown") from exc

    def _writeback_project(self, value: dict[str, Any]) -> dict[str, Any]:
        project_id = value.get("id")
        available = False
        reason = "project_host_writeback_not_available"
        if isinstance(project_id, str):
            try:
                project = self.store.require_project(project_id)
                host = self.store.require_host(project.host_id)
                available = bool(
                    self.writeback_enabled
                    and host.status == "online"
                    and host.supports_writeback
                    and project.state == "available"
                )
                if not self.writeback_enabled:
                    reason = "project_host_writeback_disabled"
                elif not host.supports_writeback:
                    reason = "project_host_protocol_readonly"
                elif host.status != "online":
                    reason = "project_host_offline"
                elif project.state != "available":
                    reason = project.reason or "project_changed"
            except ProjectHostError:
                pass
        features = value.get("features")
        if isinstance(features, dict):
            value = {**value, "features": {**features, "apply": available, "commit": available}}
        value["writeback_reason"] = None if available else reason
        return value

    def _expire_operation_payloads_unlocked(self, now: float) -> None:
        expired = [
            payload_id
            for payload_id, payload in self._operation_payloads.items()
            if payload.expires_at <= now
        ]
        for payload_id in expired:
            self._operation_payloads.pop(payload_id, None)

    async def receive_transfer(
        self,
        *,
        host_id: str,
        token: str,
        transfer_id: str,
        content_length: int,
        body: AsyncIterator[bytes],
    ) -> dict[str, Any]:
        self.store.authenticate(host_id, token)
        if SAFE_TRANSFER_ID.fullmatch(transfer_id) is None or not 0 < content_length <= MAX_HOST_ARCHIVE_BYTES:
            raise ProjectHostError("snapshot_upload_invalid")
        async with self._lock:
            transfer = self._transfers.get(transfer_id)
            if transfer is None or transfer.host_id != host_id or transfer.status != "awaiting_upload":
                raise ProjectHostError("snapshot_transfer_unavailable")
            transfer.status = "uploading"
        temporary = self.upload_root / f"{transfer_id}.uploading"
        final = self.upload_root / f"{transfer_id}.tar.gz"
        digest = hashlib.sha256()
        total = 0
        try:
            with temporary.open("xb") as stream:
                async for chunk in body:
                    if not isinstance(chunk, bytes):
                        raise ProjectHostError("snapshot_upload_invalid")
                    total += len(chunk)
                    if total > content_length or total > MAX_HOST_ARCHIVE_BYTES:
                        raise ProjectHostError("snapshot_archive_too_large")
                    digest.update(chunk)
                    stream.write(chunk)
            if total != content_length:
                raise ProjectHostError("snapshot_upload_truncated")
            os.replace(temporary, final)
            async with self._lock:
                transfer.status = "ready"
                transfer.size = total
                transfer.sha256 = digest.hexdigest()
            return {"received": True, "size": total}
        except BaseException:
            temporary.unlink(missing_ok=True)
            final.unlink(missing_ok=True)
            async with self._lock:
                transfer.status = "failed"
            raise

    async def connection_loop(self, websocket: WebSocket) -> None:
        await websocket.accept()
        connection_id = f"phconn_{uuid.uuid4().hex}"
        host_id = ""
        try:
            first = await asyncio.wait_for(self._receive(websocket), timeout=15)
            if not isinstance(first, dict):
                raise ProjectHostError("project_host_protocol_mismatch")
            protocol = str(first.get("protocol") or "")
            if protocol not in SUPPORTED_PROJECT_HOST_PROTOCOLS:
                raise ProjectHostError("project_host_protocol_mismatch")
            capabilities = first.get("capabilities")
            if protocol == PROJECT_HOST_PROTOCOL_V2:
                if capabilities != list(PROJECT_HOST_V2_CAPABILITIES):
                    raise ProjectHostError("project_host_capabilities_invalid")
            elif capabilities is not None:
                raise ProjectHostError("project_host_capabilities_invalid")
            message_type = first.get("type")
            if message_type == "pair":
                host, token = self.store.consume_pairing(
                    str(first.get("pairing_code") or ""),
                    device_id=str(first.get("device_id") or ""),
                    version=str(first.get("version") or ""),
                    platform=str(first.get("platform") or ""),
                    protocol=protocol,
                )
                host_id = host.host_id
                self.store.connect(
                    host_id,
                    token,
                    connection_id=connection_id,
                    version=host.version,
                    protocol=protocol,
                )
                await websocket.send_json(
                    {
                        "type": "welcome",
                        "protocol": protocol,
                        "capabilities": self._welcome_capabilities(protocol),
                        "direct_writeback": bool(
                            protocol == PROJECT_HOST_PROTOCOL_V2
                            and self.writeback_enabled
                        ),
                        "paired": True,
                        "host_id": host_id,
                        "host_token": token,
                        "heartbeat_seconds": 20,
                    }
                )
            elif message_type == "authenticate":
                host_id = str(first.get("host_id") or "")
                token = str(first.get("host_token") or "")
                host = self.store.connect(
                    host_id,
                    token,
                    connection_id=connection_id,
                    version=str(first.get("version") or ""),
                    protocol=protocol,
                )
                if first.get("device_id") != host.device_id or first.get("platform") != host.platform:
                    raise ProjectHostError("project_host_identity_mismatch")
                await websocket.send_json(
                    {
                        "type": "welcome",
                        "protocol": protocol,
                        "capabilities": self._welcome_capabilities(protocol),
                        "direct_writeback": bool(
                            protocol == PROJECT_HOST_PROTOCOL_V2
                            and self.writeback_enabled
                        ),
                        "paired": False,
                        "host_id": host_id,
                        "heartbeat_seconds": 20,
                    }
                )
            else:
                raise ProjectHostError("project_host_handshake_failed")
            async with self._lock:
                previous = self._connections.get(host_id)
                self._connections[host_id] = websocket
            if previous is not None and previous is not websocket:
                with contextlib.suppress(Exception):
                    await previous.close(code=4001)
            pending = self.store.next_selection(host_id)
            if pending is not None:
                await websocket.send_json({"type": "select_project", "request_id": pending.request_id})
            while True:
                message = await self._receive(websocket)
                await self._incoming(host_id, connection_id, message)
        except WebSocketDisconnect:
            logger.info("Coding project host connection closed by peer")
        except Exception as exc:
            logger.warning(
                "Coding project host connection failed: %s",
                getattr(exc, "code", type(exc).__name__),
            )
            with contextlib.suppress(Exception):
                await websocket.send_json({"type": "error", "code": getattr(exc, "code", "project_host_internal_error")})
                await websocket.close(code=4003)
        finally:
            if host_id:
                was_current = False
                with contextlib.suppress(ProjectHostError):
                    was_current = (
                        self.store.require_host(host_id).connection_id
                        == connection_id
                    )
                async with self._lock:
                    if self._connections.get(host_id) is websocket:
                        self._connections.pop(host_id, None)
                if was_current:
                    self.store.disconnect(host_id, connection_id)
                    async with self._lock:
                        disconnected = [
                            self._pending[request_id]
                            for request_id, pending_host_id in self._pending_hosts.items()
                            if pending_host_id == host_id
                            and request_id in self._pending
                        ]
                    for future in disconnected:
                        if not future.done():
                            future.set_exception(ProjectHostError("project_host_offline"))

    @staticmethod
    async def _receive(websocket: WebSocket) -> dict[str, Any]:
        raw = await websocket.receive_text()
        if len(raw.encode("utf-8")) > PROJECT_HOST_MAX_MESSAGE_BYTES:
            raise ProjectHostError("project_host_message_too_large")
        try:
            message = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProjectHostError("project_host_message_invalid") from exc
        if not isinstance(message, dict):
            raise ProjectHostError("project_host_message_invalid")
        return message

    async def _incoming(self, host_id: str, connection_id: str, message: dict[str, Any]) -> None:
        message_type = message.get("type")
        if message_type == "heartbeat":
            self.store.heartbeat(host_id, connection_id)
            return
        if message_type == "inventory":
            projects = message.get("projects")
            if not isinstance(projects, list) or len(projects) > 50:
                raise ProjectHostError("invalid_project_host_response")
            for project in projects:
                if isinstance(project, dict) and project.get("state") == "unavailable":
                    project_id = project.get("project_id")
                    if isinstance(project_id, str):
                        with contextlib.suppress(ProjectHostError):
                            known = self.store.require_project(project_id)
                            if (
                                known.host_id == host_id
                                and project.get("branch") == known.branch
                            ):
                                project = {**project, "head": known.head}
                self.store.register_project(host_id, project)
            return
        request_id = str(message.get("request_id") or "")
        if message_type == "selection_result":
            self.store.complete_selection(host_id, request_id, project=message.get("project"))
            return
        if message_type == "snapshot_result":
            transfer_id = str(message.get("transfer_id") or "")
            project_payload = message.get("project")
            async with self._lock:
                transfer = self._transfers.get(transfer_id)
                future = self._pending.get(request_id)
            if (
                transfer is None
                or future is None
                or transfer.request_id != request_id
                or transfer.host_id != host_id
                or transfer.status != "ready"
                or transfer.sha256 is None
                or not isinstance(project_payload, dict)
                or project_payload.get("project_id") != transfer.project_id
            ):
                raise ProjectHostError("snapshot_transfer_mismatch")
            if transfer.managed_operation_id is not None:
                if (
                    project_payload.get("head") != transfer.expected_head
                    or project_payload.get("branch") != transfer.expected_branch
                ):
                    raise ProjectHostError("snapshot_transfer_mismatch")
                project = self.store.require_project(transfer.project_id)
            else:
                project = self.store.register_project(host_id, project_payload)
            if not future.done():
                future.set_result(
                    {
                        "upload_id": transfer_id,
                        "archive_sha256": transfer.sha256,
                        "project": {
                            "project_id": project.project_id,
                            "name": str(project_payload.get("name") or project.name),
                            "branch": str(project_payload.get("branch") or project.branch),
                            "head": str(project_payload.get("head") or project.head),
                        },
                    }
                )
            return
        if message_type == "request_result":
            async with self._lock:
                future = self._pending.get(request_id)
            if future is None:
                raise ProjectHostError("project_host_request_not_found")
            if not future.done():
                future.set_result({"ok": True})
            return
        if message_type == "operation_result":
            async with self._lock:
                self._expire_request_tombstones_unlocked(time.time())
                future = self._pending.get(request_id)
                tombstoned = request_id in self._request_tombstones
                expected_host = self._pending_hosts.get(request_id)
                expected_operation = self._pending_operations.get(request_id)
            if future is None:
                if tombstoned:
                    return
                raise ProjectHostError("project_host_request_not_found")
            if (
                expected_host != host_id
                or expected_operation is None
                or expected_operation
                != (
                    str(message.get("project_id") or ""),
                    str(message.get("operation_id") or ""),
                    str(message.get("action") or ""),
                )
            ):
                if not future.done():
                    future.set_exception(
                        ProjectHostError("invalid_project_host_response")
                    )
                return
            if not future.done():
                future.set_result(message)
            return
        if message_type == "request_error":
            error = str(message.get("error") or "project_host_request_failed")
            with contextlib.suppress(ProjectHostError):
                selection = self.store.require_selection(request_id)
                if selection.host_id == host_id:
                    self.store.complete_selection(host_id, request_id, error=error)
                    return
            async with self._lock:
                self._expire_request_tombstones_unlocked(time.time())
                future = self._pending.get(request_id)
                tombstoned = request_id in self._request_tombstones
                expected_host = self._pending_hosts.get(request_id)
            if future is not None and expected_host != host_id:
                if not future.done():
                    future.set_exception(
                        ProjectHostError("invalid_project_host_response")
                    )
                return
            if future is not None and not future.done():
                future.set_exception(ProjectHostError(error))
                return
            if tombstoned:
                return
        raise ProjectHostError("project_host_message_unsupported")

    async def _command(
        self,
        host_id: str,
        payload: dict[str, Any],
        *,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        request_id = f"phreq_{uuid.uuid4().hex}"
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        async with self._lock:
            self._pending[request_id] = future
            self._pending_hosts[request_id] = host_id
            if payload.get("type") == "execute_operation":
                self._pending_operations[request_id] = (
                    str(payload.get("project_id") or ""),
                    str(payload.get("operation_id") or ""),
                    str(payload.get("action") or ""),
                )
        try:
            await self._send(host_id, {**payload, "request_id": request_id})
            return await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
        except TimeoutError as exc:
            async with self._lock:
                self._request_tombstones[request_id] = time.time() + 300.0
            raise ProjectHostError("project_host_request_timeout") from exc
        finally:
            async with self._lock:
                self._pending.pop(request_id, None)
                self._pending_hosts.pop(request_id, None)
                self._pending_operations.pop(request_id, None)
                if not future.done():
                    future.cancel()

    def _welcome_capabilities(self, protocol: str) -> list[str]:
        if protocol != PROJECT_HOST_PROTOCOL_V2 or not self.writeback_enabled:
            return ["snapshot"]
        return list(PROJECT_HOST_V2_CAPABILITIES)

    def _expire_request_tombstones_unlocked(self, now: float) -> None:
        for request_id in tuple(self._request_tombstones):
            if self._request_tombstones[request_id] <= now:
                self._request_tombstones.pop(request_id, None)

    async def _send(self, host_id: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            connection = self._connections.get(host_id)
        if connection is None:
            raise ProjectHostError("project_host_offline")
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > PROJECT_HOST_MAX_MESSAGE_BYTES:
            raise ProjectHostError("project_host_message_too_large")
        await connection.send_text(encoded)

    def _prepare_upload_root(self) -> None:
        if not self.upload_root.is_absolute() or self.upload_root.is_symlink():
            raise ProjectHostError("snapshot_upload_root_unsafe")
        self.upload_root.mkdir(parents=True, exist_ok=True)
        marker = self.upload_root / UPLOAD_ROOT_MARKER
        if not marker.exists():
            if any(self.upload_root.iterdir()):
                raise ProjectHostError("snapshot_upload_root_unsafe")
            marker.write_text("v1\n", encoding="ascii")
        if marker.is_symlink() or marker.read_text(encoding="ascii") != "v1\n":
            raise ProjectHostError("snapshot_upload_root_unsafe")
        for child in self.upload_root.glob("*.uploading"):
            if SAFE_TRANSFER_ID.fullmatch(child.name.removesuffix(".uploading")) and child.is_file():
                child.unlink()


def _followup_operation_id(action: str, operation_id: str) -> str:
    digest = hashlib.sha256(f"{action}\0{operation_id}".encode("utf-8")).hexdigest()
    return f"{action}_{digest[:40]}"


def _writer_error(code: str) -> ProjectWriterClientError:
    safe_code = code if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code) else "project_writer_error"
    return ProjectWriterClientError("Project host operation failed.", code=safe_code)


def _operation_apply_receipt(
    result: dict[str, Any],
    *,
    expected_state: str,
) -> ApplyReceipt:
    if result.get("state") != expected_state or not isinstance(result.get("receipt"), dict):
        raise _writer_error("invalid_response")
    try:
        return _receipt_from_response({"receipt": result["receipt"]})
    except Exception as exc:
        raise _writer_error("invalid_response") from exc


def _operation_commit_receipt(
    result: dict[str, Any],
    *,
    expected_state: str,
) -> CommitReceipt:
    if result.get("state") != expected_state or not isinstance(result.get("receipt"), dict):
        raise _writer_error("invalid_response")
    try:
        return _commit_receipt_from_response({"receipt": result["receipt"]})
    except Exception as exc:
        raise _writer_error("invalid_response") from exc


def _commit_receipt_matches(
    receipt: CommitReceipt,
    *,
    operation_id: str,
    apply_receipt: ApplyReceipt,
    expected_head: str,
    expected_branch: str,
    message: str,
) -> bool:
    return bool(
        receipt.commit_id == operation_id
        and receipt.revision == apply_receipt.revision
        and receipt.apply_id == apply_receipt.apply_id
        and receipt.parent_sha == expected_head
        and receipt.branch == expected_branch
        and receipt.message == message
        and receipt.files == tuple(item.path for item in apply_receipt.files)
    )


def create_project_host_runtime(
    *,
    writeback_enabled: bool | None = None,
) -> ProjectHostRuntime:
    state_path = Path(
        os.getenv(
            "CODING_PROJECT_HOST_STATE_PATH",
            "/var/lib/modelmirror/coding-project-host/state.json",
        )
    )
    upload_root = Path(os.getenv("CODING_PROJECT_UPLOAD_ROOT", "/project-uploads"))
    if not state_path.is_absolute():
        raise ProjectHostError("project_host_state_not_configured")
    enabled = (
        os.getenv("CODING_PROJECT_HOST_WRITEBACK_ENABLED", "false").strip().lower()
        in {"1", "true", "yes", "on"}
        if writeback_enabled is None
        else bool(writeback_enabled)
    )
    return ProjectHostRuntime(
        ProjectHostStore(state_path),
        upload_root,
        writeback_enabled=enabled,
    )


project_host_router = APIRouter()


def _service() -> Any:
    from .api import get_coding_service

    return get_coding_service()


def _runtime() -> ProjectHostRuntime:
    runtime = _service().project_host
    if runtime is None:
        raise HTTPException(status_code=503, detail={"code": "project_host_unavailable"})
    return runtime


def _raise(exc: ProjectHostError) -> None:
    code = exc.code
    if code in {"project_not_found", "project_host_not_found", "project_host_request_not_found"}:
        http_status = status.HTTP_404_NOT_FOUND
    elif code in {"project_host_authentication_failed", "project_host_unavailable"}:
        http_status = status.HTTP_401_UNAUTHORIZED
    elif code.endswith("offline") or code.endswith("unavailable"):
        http_status = status.HTTP_503_SERVICE_UNAVAILABLE
    else:
        http_status = status.HTTP_409_CONFLICT
    raise HTTPException(status_code=http_status, detail={"code": code}) from exc


@project_host_router.get("/project-host")
async def project_host_status(response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return _runtime().status()


@project_host_router.post("/project-host/pairings")
async def create_pairing(payload: PairingRequest, response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    pairing, code = _runtime().store.create_pairing(payload.name)
    return {
        "pairing_id": pairing.pairing_id,
        "pairing_code": code,
        "expires_at": pairing.expires_at,
        "single_use": True,
    }


@project_host_router.post("/project-host/reconnect")
async def reconnect_project_host(response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await _runtime().reconnect()
    except ProjectHostError as exc:
        _raise(exc)


@project_host_router.delete("/project-host/{host_id}")
async def revoke_project_host(host_id: str, response: Response) -> dict[str, bool]:
    response.headers["Cache-Control"] = "no-store"
    service = _service()
    if await service.any_host_project_locked():
        raise HTTPException(status_code=409, detail={"code": "project_active"})
    try:
        _runtime().store.revoke(host_id)
        return {"revoked": True}
    except ProjectHostError as exc:
        _raise(exc)


@project_host_router.post("/projects/selections", status_code=202)
async def select_project(response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await _runtime().create_selection()
    except ProjectHostError as exc:
        _raise(exc)


@project_host_router.get("/projects/selections/{request_id}")
async def project_selection_status(request_id: str, response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    try:
        return _runtime().selection(request_id)
    except ProjectHostError as exc:
        _raise(exc)


@project_host_router.patch("/projects/{project_id}")
async def rename_project(project_id: str, payload: ProjectRenameRequest, response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    if await _service().project_locked(project_id):
        raise HTTPException(status_code=409, detail={"code": "project_active"})
    try:
        return await _runtime().rename_project(project_id, payload.name)
    except ProjectHostError as exc:
        _raise(exc)


@project_host_router.delete("/projects/{project_id}")
async def remove_project(project_id: str, response: Response) -> dict[str, bool]:
    response.headers["Cache-Control"] = "no-store"
    if await _service().project_locked(project_id):
        raise HTTPException(status_code=409, detail={"code": "project_active"})
    try:
        return await _runtime().remove_project(project_id)
    except ProjectHostError as exc:
        _raise(exc)


@project_host_router.websocket("/project-host/connect")
async def connect_project_host(websocket: WebSocket) -> None:
    try:
        runtime = _runtime()
    except HTTPException:
        await websocket.close(code=1013)
        return
    await runtime.connection_loop(websocket)


@project_host_router.put("/project-host/transfers/{transfer_id}")
async def upload_project_snapshot(
    transfer_id: str,
    request: Request,
    response: Response,
    authorization: str | None = Header(default=None),
    host_id: str | None = Header(default=None, alias="X-ModelMirror-Project-Host-Id"),
    content_length: int | None = Header(default=None),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    token = authorization.removeprefix("Bearer ").strip() if authorization and authorization.startswith("Bearer ") else ""
    if not token or not host_id or content_length is None:
        raise HTTPException(status_code=401, detail={"code": "project_host_authentication_failed"})
    try:
        return await _runtime().receive_transfer(
            host_id=host_id,
            token=token,
            transfer_id=transfer_id,
            content_length=content_length,
            body=request.stream(),
        )
    except ProjectHostError as exc:
        _raise(exc)


@project_host_router.get("/project-host/operations/{payload_id}")
async def download_project_operation(
    payload_id: str,
    authorization: str | None = Header(default=None),
    host_id: str | None = Header(default=None, alias="X-ModelMirror-Project-Host-Id"),
    project_id: str | None = Header(default=None, alias="X-ModelMirror-Project-Id"),
    operation_id: str | None = Header(default=None, alias="X-ModelMirror-Operation-Id"),
    action: str | None = Header(default=None, alias="X-ModelMirror-Operation-Action"),
) -> Response:
    token = (
        authorization.removeprefix("Bearer ").strip()
        if authorization and authorization.startswith("Bearer ")
        else ""
    )
    if not all((token, host_id, project_id, operation_id, action)):
        raise HTTPException(
            status_code=401,
            detail={"code": "project_host_authentication_failed"},
        )
    try:
        body = await _runtime().consume_operation_payload(
            payload_id=payload_id,
            host_id=host_id,
            token=token,
            project_id=project_id,
            operation_id=operation_id,
            action=action,
        )
    except ProjectHostError as exc:
        _raise(exc)
    return Response(
        content=body,
        media_type="application/json",
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "Content-Security-Policy": "default-src 'none'",
        },
    )
