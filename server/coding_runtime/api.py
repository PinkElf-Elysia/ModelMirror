from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import re
import secrets
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import Response, StreamingResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from .applier_client import (
    ApplierClientError,
    CodingApplierClient,
)
from .apply_models import (
    ApplyReceipt,
    ApplyState,
    not_applied_payload,
)
from .committer_client import (
    CodingCommitterClient,
    CommitterClientError,
)
from .commit_models import (
    MAX_COMMIT_MESSAGE_CHARS,
    CodingCommitError,
    CommitReceipt,
    CommitState,
    normalize_commit_message,
    not_committed_payload,
    suggest_commit_message,
)
from .draft_workspace import DraftLimits, DraftPolicyError, DraftWorkspace
from .models import CodingEvent, CodingEventKind
from .patch_policy import SNAPSHOT_FINGERPRINT_PATTERN
from .verification import sanitize_verification_output, select_verification_plan
from .worker import CodingWorkerClient, CodingWorkerError

MAX_PROMPT_CHARS = 20_000
SESSION_TTL_SECONDS = 30 * 60
EVENT_BUFFER_SIZE = 1024
HEARTBEAT_SECONDS = 15.0
DEFAULT_DRAFT_LIMITS = DraftLimits()
TERMINAL_EVENT_TYPES = {
    CodingEventKind.TURN_COMPLETED.value,
    CodingEventKind.FAILED.value,
    CodingEventKind.CANCELLED.value,
}
ACTIVE_STATES = {
    "starting",
    "ready",
    "running",
    "cancelling",
    "applied",
    "reverted",
}
WINDOWS_ABSOLUTE_PATH = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/][^\s\"'<>]+")
CONTAINER_ABSOLUTE_PATH = re.compile(
    r"(?<![A-Za-z0-9_])/(?:home|run|opt|tmp|usr|etc|var)/[^\s\"'<>]+"
)
SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
SAFE_DIFF_HEADER = re.compile(r"^diff --git a/(.+) b/(.+)$")


class WorkerClient(Protocol):
    async def health(self) -> dict[str, Any]: ...

    async def create_session(self) -> dict[str, Any]: ...

    def prompt(
        self,
        session_id: str,
        prompt: str,
    ) -> AsyncIterator[CodingEvent]: ...

    async def cancel(self, session_id: str) -> bool: ...

    async def close(self, session_id: str) -> None: ...

    async def changes(self, session_id: str) -> dict[str, Any]: ...

    async def diff(self, session_id: str, path: str, revision: int) -> str: ...

    async def patch(self, session_id: str, revision: int) -> str: ...

    async def validate(self, session_id: str) -> dict[str, Any]: ...

    async def discard(self, session_id: str) -> dict[str, Any]: ...

    async def verification_start(
        self,
        session_id: str,
        revision: int,
    ) -> dict[str, Any]: ...

    async def verification_status(
        self,
        session_id: str,
        revision: int,
    ) -> dict[str, Any]: ...

    async def verification_cancel(
        self,
        session_id: str,
        revision: int,
    ) -> dict[str, Any]: ...


class ApplierClient(Protocol):
    async def health(self) -> dict[str, Any]: ...

    async def apply(
        self,
        *,
        operation_id: str,
        revision: int,
        patch: str,
        paths: list[str],
        expected_fingerprint: str,
    ) -> ApplyReceipt: ...

    async def revert(self, receipt: ApplyReceipt) -> ApplyReceipt: ...


class CommitterClient(Protocol):
    async def health(self) -> dict[str, Any]: ...

    async def commit(
        self,
        *,
        operation_id: str,
        apply_receipt: ApplyReceipt,
        message: str,
    ) -> CommitReceipt: ...

    async def undo(
        self,
        receipt: CommitReceipt,
        apply_receipt: ApplyReceipt,
    ) -> CommitReceipt: ...


class CodingTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS)

    @field_validator("prompt")
    @classmethod
    def prompt_must_have_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Prompt must not be empty")
        return value


class DraftFilePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=500)
    status: Literal["added", "modified"]
    additions: int = Field(ge=0, le=1_000_000)
    deletions: int = Field(ge=0, le=1_000_000)

    @field_validator("path")
    @classmethod
    def path_must_be_safe(cls, value: str) -> str:
        try:
            return DraftWorkspace.normalize_relative_path(value)
        except DraftPolicyError as exc:
            raise ValueError("Draft path is invalid") from exc


class DraftCheckPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    check_id: str = Field(alias="id", min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=100)
    status: Literal["passed", "failed"]
    message: str = Field(min_length=1, max_length=500)


class DraftChangesPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=0)
    files: list[DraftFilePayload] = Field(
        max_length=DEFAULT_DRAFT_LIMITS.max_changed_files
    )
    file_count: int = Field(
        ge=0,
        le=DEFAULT_DRAFT_LIMITS.max_changed_files,
    )
    additions: int = Field(ge=0, le=1_000_000)
    deletions: int = Field(ge=0, le=1_000_000)
    patch_bytes: int = Field(
        ge=0,
        le=DEFAULT_DRAFT_LIMITS.max_patch_bytes,
    )
    validation_status: Literal["passed", "failed"]
    can_download: bool
    checks: list[DraftCheckPayload] = Field(max_length=20)

    @model_validator(mode="after")
    def totals_must_match(self) -> DraftChangesPayload:
        if self.file_count != len(self.files):
            raise ValueError("Draft file count is inconsistent")
        if self.additions != sum(item.additions for item in self.files):
            raise ValueError("Draft addition count is inconsistent")
        if self.deletions != sum(item.deletions for item in self.files):
            raise ValueError("Draft deletion count is inconsistent")
        expected_download = bool(self.files) and self.validation_status == "passed"
        if self.can_download is not expected_download:
            raise ValueError("Draft download state is inconsistent")
        failed_checks = any(check.status == "failed" for check in self.checks)
        if (self.validation_status == "failed") is not failed_checks:
            raise ValueError("Draft validation checks are inconsistent")
        return self


class VerificationRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=0)


class ApplyRevertRequest(VerificationRevisionRequest):
    apply_id: str = Field(min_length=20, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class CommitRequest(ApplyRevertRequest):
    message: str = Field(min_length=1, max_length=MAX_COMMIT_MESSAGE_CHARS)

    @field_validator("message")
    @classmethod
    def message_must_be_safe(cls, value: str) -> str:
        try:
            return normalize_commit_message(value)
        except ValueError as exc:
            raise ValueError("Commit message is invalid") from exc


class CommitUndoRequest(ApplyRevertRequest):
    commit_id: str = Field(min_length=20, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class VerificationStepPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: Literal[
        "backend_tests",
        "backend_baseline_tests",
        "backend_draft_tests",
        "frontend_build",
    ] = Field(alias="id")
    label: str = Field(min_length=1, max_length=100)
    state: Literal["not_started", "running", "completed", "cancelled"]
    result: Literal["not_run", "passed", "failed", "not_applicable"]
    duration_ms: int | None = Field(default=None, ge=0, le=600_000)
    summary: str = Field(default="", max_length=500)
    details: str = Field(default="", max_length=16_000)
    truncated: bool = False


class VerificationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    revision: int = Field(ge=0)
    state: Literal["not_started", "running", "completed", "cancelled"]
    result: Literal["not_run", "passed", "failed", "not_applicable"]
    stale: bool
    reason: str | None = Field(default=None, max_length=64)
    started_at: float | None = Field(default=None, ge=0)
    finished_at: float | None = Field(default=None, ge=0)
    steps: list[VerificationStepPayload] = Field(max_length=4)

    @model_validator(mode="after")
    def state_and_result_must_be_consistent(self) -> VerificationPayload:
        terminal = self.state in {"completed", "cancelled"}
        if self.state == "running" and self.started_at is None:
            raise ValueError("Running verification must have a start time")
        if terminal and self.finished_at is None:
            raise ValueError("Terminal verification must have a finish time")
        if self.state == "not_started" and self.result != "not_run":
            raise ValueError("Unstarted verification result is inconsistent")
        if self.state == "cancelled" and self.result != "not_run":
            raise ValueError("Cancelled verification result is inconsistent")
        if self.result in {"passed", "failed", "not_applicable"} and (
            self.state != "completed"
        ):
            raise ValueError("Verification result is inconsistent")
        return self


@dataclass(slots=True)
class CodingApiSession:
    session_id: str
    worker_session_id: str
    state: str = "ready"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    events: deque[dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=EVENT_BUFFER_SIZE)
    )
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    turn_task: asyncio.Task[None] | None = None
    last_seq: int = 0
    apply_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    apply_state: ApplyState = ApplyState.NOT_APPLIED
    apply_revision: int | None = None
    apply_operation_id: str | None = None
    apply_receipt: ApplyReceipt | None = None
    apply_reason: str | None = None
    apply_started_at: float | None = None
    apply_finished_at: float | None = None
    commit_state: CommitState = CommitState.NOT_COMMITTED
    commit_revision: int | None = None
    commit_operation_id: str | None = None
    commit_message: str | None = None
    commit_receipt: CommitReceipt | None = None
    commit_reason: str | None = None
    commit_started_at: float | None = None
    commit_finished_at: float | None = None


class CodingService:
    """Ephemeral single-session API state around the isolated Coding Worker."""

    def __init__(
        self,
        *,
        enabled: bool,
        worker: WorkerClient,
        applier: ApplierClient | None = None,
        committer: CommitterClient | None = None,
        ttl_seconds: float = SESSION_TTL_SECONDS,
        mode: str | None = None,
    ) -> None:
        self.enabled = enabled
        self.worker = worker
        self.applier = applier
        self.committer = committer
        self.ttl_seconds = ttl_seconds
        self.mode = _normalize_mode(
            mode if mode is not None else os.getenv("CODING_AGENT_MODE", "readonly")
        )
        self._sessions: dict[str, CodingApiSession] = {}
        self._lock = asyncio.Lock()

    async def capabilities(self) -> dict[str, Any]:
        response = {
            "enabled": self.enabled,
            "available": False,
            "mode": self.mode,
            "workspace": "ModelMirror",
            "limits": {
                "max_prompt_chars": MAX_PROMPT_CHARS,
                "max_concurrency": 1,
                "session_ttl_seconds": int(self.ttl_seconds),
            },
            "verification": {
                "available": False,
                "strategy": "adaptive",
                "required_for_patch": False,
                "max_duration_seconds": 600,
            },
        }
        if self.mode == "draft":
            response["limits"].update(
                {
                    "max_changed_files": DEFAULT_DRAFT_LIMITS.max_changed_files,
                    "max_file_bytes": DEFAULT_DRAFT_LIMITS.max_file_bytes,
                    "max_patch_bytes": DEFAULT_DRAFT_LIMITS.max_patch_bytes,
                }
            )
            response["host_apply"] = False
            response["apply"] = {
                "configured": False,
                "available": False,
                "target": "dedicated_worktree",
                "requires_verification": True,
                "allows_not_applicable": True,
                "supports_revert": True,
                "reason": "applier_not_configured",
            }
            response["commit"] = {
                "configured": False,
                "available": False,
                "target": "isolated_local_repository",
                "requires_apply": True,
                "supports_undo": True,
                "remote_operations": False,
                "max_message_chars": MAX_COMMIT_MESSAGE_CHARS,
                "reason": "committer_not_configured",
            }
        if not self.enabled:
            response["reason"] = "disabled"
            return response
        try:
            health = await self.worker.health()
        except Exception:
            response["reason"] = "worker_unavailable"
            return response
        if health.get("ok") is not True:
            response["reason"] = "worker_unavailable"
            return response
        if health.get("configured") is not True:
            response["reason"] = "not_configured"
            return response
        worker_mode = health.get("mode")
        if worker_mode not in {"readonly", "draft"} or worker_mode != self.mode:
            response["reason"] = "mode_mismatch"
            return response
        worker_verification = health.get("verification")
        if self.mode == "draft" and isinstance(worker_verification, dict):
            if (
                worker_verification.get("available") is True
                and worker_verification.get("strategy") == "adaptive"
                and worker_verification.get("required_for_patch") is False
                and worker_verification.get("max_duration_seconds") == 600
            ):
                response["verification"]["available"] = True
            else:
                response["verification"]["reason"] = _safe_code(
                    worker_verification.get("reason")
                )
        if self.mode == "draft":
            apply_capability = await self._apply_capability(health)
            response["apply"] = apply_capability
            response["host_apply"] = apply_capability["available"] is True
            response["commit"] = await self._commit_capability(health)
        response["available"] = True
        return response

    async def create_session(self) -> CodingApiSession:
        await self._require_available()
        await self.cleanup_expired()
        async with self._lock:
            if any(record.state in ACTIVE_STATES for record in self._sessions.values()):
                raise _http_error(
                    status.HTTP_409_CONFLICT,
                    "concurrency_limit",
                )
        try:
            result = await self.worker.create_session()
        except CodingWorkerError as exc:
            raise _worker_http_error(exc) from exc
        session_id = result.get("session_id")
        worker_mode = result.get("mode")
        event_data = result.get("event")
        if (
            not isinstance(session_id, str)
            or not SAFE_IDENTIFIER.fullmatch(session_id)
            or worker_mode != self.mode
            or not isinstance(event_data, dict)
        ):
            if isinstance(session_id, str) and session_id:
                with contextlib.suppress(Exception):
                    await self.worker.close(session_id)
            raise _http_error(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "invalid_worker_response",
            )
        record = CodingApiSession(
            session_id=session_id,
            worker_session_id=session_id,
        )
        initial = _event_from_payload(event_data)
        if (
            initial.kind is not CodingEventKind.SESSION_STARTED
            or initial.seq != 1
            or initial.session_id != session_id
        ):
            with contextlib.suppress(Exception):
                await self.worker.close(session_id)
            raise _http_error(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "invalid_worker_response",
            )
        await self._append_event(record, initial)
        async with self._lock:
            if any(existing.state in ACTIVE_STATES for existing in self._sessions.values()):
                with contextlib.suppress(Exception):
                    await self.worker.close(session_id)
                raise _http_error(
                    status.HTTP_409_CONFLICT,
                    "concurrency_limit",
                )
            self._sessions[session_id] = record
        return record

    async def start_turn(self, session_id: str, prompt: str) -> CodingApiSession:
        await self.cleanup_expired()
        record = self._get_session(session_id)
        if record.apply_lock.locked():
            raise _http_error(status.HTTP_409_CONFLICT, "apply_in_progress")
        async with record.apply_lock:
            self._require_mutable(record)
            if record.state != "ready" or (
                record.turn_task is not None and not record.turn_task.done()
            ):
                raise _http_error(status.HTTP_409_CONFLICT, "turn_in_progress")
            await self._require_verification_idle(record)
            record.state = "running"
            record.updated_at = time.time()
            record.turn_task = asyncio.create_task(self._run_turn(record, prompt))
        return record

    async def session_status(self, session_id: str) -> dict[str, str]:
        await self.cleanup_expired()
        record = self._get_session(session_id)
        return {"state": record.state}

    async def cancel(self, session_id: str) -> bool:
        await self.cleanup_expired()
        record = self._get_session(session_id)
        task = record.turn_task
        if task is None or task.done() or record.state not in {"running", "cancelling"}:
            return False
        if record.state == "cancelling":
            return False
        try:
            accepted = await self.worker.cancel(record.worker_session_id)
        except CodingWorkerError as exc:
            raise _worker_http_error(exc) from exc
        if accepted:
            record.state = "cancelling"
            record.updated_at = time.time()
        return accepted

    async def changes(self, session_id: str) -> dict[str, Any]:
        record = await self._review_record(session_id)
        try:
            payload = await self.worker.changes(record.worker_session_id)
        except CodingWorkerError as exc:
            raise _worker_http_error(exc) from exc
        return _public_changes(payload)

    async def diff(self, session_id: str, path: str, revision: int) -> str:
        record = await self._review_record(session_id)
        try:
            safe_path = DraftWorkspace.normalize_relative_path(path)
        except DraftPolicyError as exc:
            raise _http_error(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "invalid_path",
            ) from exc
        try:
            diff = await self.worker.diff(
                record.worker_session_id,
                safe_path,
                revision,
            )
        except CodingWorkerError as exc:
            raise _worker_http_error(exc) from exc
        return _safe_diff(diff, expected_path=safe_path)

    async def patch(self, session_id: str, revision: int) -> str:
        record = await self._review_record(session_id)
        try:
            patch = await self.worker.patch(record.worker_session_id, revision)
        except CodingWorkerError as exc:
            raise _worker_http_error(exc) from exc
        return _safe_diff(patch)

    async def validate(self, session_id: str) -> dict[str, Any]:
        record = await self._review_record(session_id)
        if record.apply_lock.locked():
            raise _http_error(status.HTTP_409_CONFLICT, "apply_in_progress")
        async with record.apply_lock:
            self._require_mutable(record)
            try:
                payload = await self.worker.validate(record.worker_session_id)
            except CodingWorkerError as exc:
                raise _worker_http_error(exc) from exc
        return _public_changes(payload)

    async def discard(self, session_id: str) -> dict[str, Any]:
        record = await self._review_record(session_id)
        if record.apply_lock.locked():
            raise _http_error(status.HTTP_409_CONFLICT, "apply_in_progress")
        async with record.apply_lock:
            self._require_mutable(record)
            await self._require_verification_idle(record)
            try:
                payload = await self.worker.discard(record.worker_session_id)
            except CodingWorkerError as exc:
                raise _worker_http_error(exc) from exc
            record.updated_at = time.time()
        return _public_changes(payload)

    async def verification_start(
        self,
        session_id: str,
        revision: int,
    ) -> dict[str, Any]:
        record = await self._review_record(session_id)
        if record.apply_lock.locked():
            raise _http_error(status.HTTP_409_CONFLICT, "apply_in_progress")
        async with record.apply_lock:
            self._require_mutable(record)
            try:
                payload = await self.worker.verification_start(
                    record.worker_session_id,
                    revision,
                )
            except CodingWorkerError as exc:
                raise _worker_http_error(exc) from exc
            record.updated_at = time.time()
        return _verification_from_worker(payload)

    async def verification_status(
        self,
        session_id: str,
        revision: int,
    ) -> dict[str, Any]:
        await self.cleanup_expired()
        if self.mode != "draft":
            raise _http_error(status.HTTP_409_CONFLICT, "draft_unavailable")
        record = self._get_session(session_id)
        try:
            payload = await self.worker.verification_status(
                record.worker_session_id,
                revision,
            )
        except CodingWorkerError as exc:
            raise _worker_http_error(exc) from exc
        record.updated_at = time.time()
        return _verification_from_worker(payload)

    async def verification_cancel(
        self,
        session_id: str,
        revision: int,
    ) -> dict[str, Any]:
        await self.cleanup_expired()
        if self.mode != "draft":
            raise _http_error(status.HTTP_409_CONFLICT, "draft_unavailable")
        record = self._get_session(session_id)
        if record.apply_lock.locked():
            raise _http_error(status.HTTP_409_CONFLICT, "apply_in_progress")
        async with record.apply_lock:
            self._require_mutable(record)
            try:
                payload = await self.worker.verification_cancel(
                    record.worker_session_id,
                    revision,
                )
            except CodingWorkerError as exc:
                raise _worker_http_error(exc) from exc
            record.updated_at = time.time()
        result = _verification_from_worker(payload)
        result["accepted"] = payload.get("accepted") is True
        return result

    async def apply(
        self,
        session_id: str,
        revision: int,
    ) -> dict[str, Any]:
        record = await self._review_record(session_id)
        async with record.apply_lock:
            if record.apply_revision == revision and record.apply_state in {
                ApplyState.APPLIED,
                ApplyState.REVERTED,
                ApplyState.FAILED,
            }:
                return self._public_apply(record, revision)
            self._require_mutable(record)
            changes = await self._current_changes(record)
            if changes["revision"] != revision:
                raise _http_error(status.HTTP_409_CONFLICT, "stale_revision")
            if not changes["files"]:
                raise _http_error(status.HTTP_409_CONFLICT, "draft_is_empty")
            if (
                changes["validation_status"] != "passed"
                or changes["can_download"] is not True
            ):
                raise _http_error(status.HTTP_409_CONFLICT, "validation_failed")
            verification = await self._current_verification(record, revision)
            paths = [item["path"] for item in changes["files"]]
            if not _verification_allows_apply(verification, paths):
                raise _http_error(
                    status.HTTP_409_CONFLICT,
                    _verification_apply_reason(verification),
                )
            expected_fingerprint = await self._require_apply_available()
            try:
                patch = _safe_diff(
                    await self.worker.patch(record.worker_session_id, revision)
                )
            except CodingWorkerError as exc:
                raise _worker_http_error(exc) from exc
            operation_id = secrets.token_urlsafe(18)
            record.apply_state = ApplyState.APPLYING
            record.apply_revision = revision
            record.apply_operation_id = operation_id
            record.apply_reason = None
            record.apply_started_at = time.time()
            record.apply_finished_at = None
            record.updated_at = time.time()
            try:
                assert self.applier is not None
                receipt = await self.applier.apply(
                    operation_id=operation_id,
                    revision=revision,
                    patch=patch,
                    paths=paths,
                    expected_fingerprint=expected_fingerprint,
                )
                if (
                    receipt.revision != revision
                    or receipt.snapshot_fingerprint != expected_fingerprint
                    or [item.path for item in receipt.files] != paths
                ):
                    try:
                        await self.applier.revert(receipt)
                    except ApplierClientError as revert_exc:
                        raise ApplierClientError(
                            "Coding application response could not be recovered.",
                            code="rollback_failed",
                        ) from revert_exc
                    raise ApplierClientError(
                        "Coding application receipt does not match the request.",
                        code="invalid_response",
                    )
            except ApplierClientError as exc:
                record.apply_state = ApplyState.FAILED
                record.apply_reason = _safe_code(exc.code)
                record.apply_finished_at = time.time()
                record.updated_at = record.apply_finished_at
                raise _applier_http_error(exc) from exc
            record.apply_receipt = receipt
            record.apply_state = ApplyState.APPLIED
            record.apply_finished_at = time.time()
            record.state = "applied"
            record.updated_at = record.apply_finished_at
            return self._public_apply(record, revision)

    async def apply_status(
        self,
        session_id: str,
        revision: int,
    ) -> dict[str, Any]:
        await self.cleanup_expired()
        if self.mode != "draft":
            raise _http_error(status.HTTP_409_CONFLICT, "draft_unavailable")
        record = self._get_session(session_id)
        return self._public_apply(record, revision)

    async def commit(
        self,
        session_id: str,
        revision: int,
        apply_id: str,
        message: str,
    ) -> dict[str, Any]:
        await self.cleanup_expired()
        if self.mode != "draft":
            raise _http_error(status.HTTP_409_CONFLICT, "draft_unavailable")
        record = self._get_session(session_id)
        async with record.apply_lock:
            apply_receipt = record.apply_receipt
            if (
                apply_receipt is None
                or record.apply_revision != revision
                or apply_receipt.apply_id != apply_id
            ):
                raise _http_error(status.HTTP_409_CONFLICT, "apply_mismatch")
            if record.apply_state is not ApplyState.APPLIED:
                raise _http_error(status.HTTP_409_CONFLICT, "apply_not_committable")
            if record.commit_state is CommitState.COMMITTED:
                if record.commit_message != message:
                    raise _http_error(status.HTTP_409_CONFLICT, "commit_already_exists")
                return self._public_commit(record, revision)
            if record.commit_state in {CommitState.COMMITTING, CommitState.UNDOING}:
                raise _http_error(status.HTTP_409_CONFLICT, "commit_in_progress")
            if (
                record.commit_state is CommitState.FAILED
                and record.commit_message is not None
                and record.commit_message != message
            ):
                raise _http_error(
                    status.HTTP_409_CONFLICT,
                    "commit_retry_message_mismatch",
                )
            if record.commit_state is CommitState.UNDONE:
                record.commit_operation_id = None
                record.commit_receipt = None
            retrying_unknown_result = (
                record.commit_state is CommitState.FAILED
                and record.commit_operation_id is not None
            )
            if not retrying_unknown_result:
                await self._require_commit_available(
                    apply_receipt.snapshot_fingerprint
                )
            operation_id = record.commit_operation_id or secrets.token_urlsafe(18)
            record.commit_state = CommitState.COMMITTING
            record.commit_revision = revision
            record.commit_operation_id = operation_id
            record.commit_message = message
            record.commit_reason = None
            record.commit_started_at = time.time()
            record.commit_finished_at = None
            record.updated_at = record.commit_started_at
            try:
                assert self.committer is not None
                receipt = await self.committer.commit(
                    operation_id=operation_id,
                    apply_receipt=apply_receipt,
                    message=message,
                )
                expected_files = tuple(item.path for item in apply_receipt.files)
                if (
                    receipt.commit_id != operation_id
                    or receipt.revision != revision
                    or receipt.apply_id != apply_id
                    or receipt.message != message
                    or receipt.files != expected_files
                ):
                    try:
                        await self.committer.undo(receipt, apply_receipt)
                    except CommitterClientError as undo_exc:
                        raise CommitterClientError(
                            "Invalid commit response could not be recovered.",
                            code="rollback_failed",
                        ) from undo_exc
                    raise CommitterClientError(
                        "Commit receipt does not match the request.",
                        code="invalid_response",
                    )
            except CommitterClientError as exc:
                record.commit_state = CommitState.FAILED
                record.commit_reason = _safe_code(exc.code)
                record.commit_finished_at = time.time()
                record.updated_at = record.commit_finished_at
                raise _committer_http_error(exc) from exc
            record.commit_receipt = receipt
            record.commit_state = CommitState.COMMITTED
            record.commit_finished_at = time.time()
            record.updated_at = record.commit_finished_at
            return self._public_commit(record, revision)

    async def commit_status(
        self,
        session_id: str,
        revision: int,
    ) -> dict[str, Any]:
        await self.cleanup_expired()
        if self.mode != "draft":
            raise _http_error(status.HTTP_409_CONFLICT, "draft_unavailable")
        return self._public_commit(self._get_session(session_id), revision)

    async def undo_commit(
        self,
        session_id: str,
        revision: int,
        apply_id: str,
        commit_id: str,
    ) -> dict[str, Any]:
        await self.cleanup_expired()
        if self.mode != "draft":
            raise _http_error(status.HTTP_409_CONFLICT, "draft_unavailable")
        record = self._get_session(session_id)
        async with record.apply_lock:
            apply_receipt = record.apply_receipt
            receipt = record.commit_receipt
            if (
                apply_receipt is None
                or receipt is None
                or record.commit_revision != revision
                or apply_receipt.apply_id != apply_id
                or receipt.apply_id != apply_id
                or receipt.commit_id != commit_id
            ):
                raise _http_error(status.HTTP_409_CONFLICT, "commit_mismatch")
            if record.commit_state is CommitState.UNDONE:
                return self._public_commit(record, revision)
            if record.commit_state is not CommitState.COMMITTED:
                raise _http_error(status.HTTP_409_CONFLICT, "commit_not_undoable")
            if self.committer is None:
                raise _http_error(status.HTTP_503_SERVICE_UNAVAILABLE, "committer_unavailable")
            record.commit_state = CommitState.UNDOING
            record.commit_reason = None
            record.updated_at = time.time()
            try:
                undone = await self.committer.undo(receipt, apply_receipt)
                if undone != receipt:
                    raise CommitterClientError(
                        "Commit undo receipt does not match.",
                        code="invalid_response",
                    )
            except CommitterClientError as exc:
                record.commit_state = CommitState.COMMITTED
                record.commit_reason = _safe_code(exc.code)
                record.commit_finished_at = time.time()
                record.updated_at = record.commit_finished_at
                raise _committer_http_error(exc) from exc
            record.commit_state = CommitState.UNDONE
            record.commit_reason = None
            record.commit_finished_at = time.time()
            record.updated_at = record.commit_finished_at
            return self._public_commit(record, revision)

    async def revert_apply(
        self,
        session_id: str,
        revision: int,
        apply_id: str,
    ) -> dict[str, Any]:
        await self.cleanup_expired()
        if self.mode != "draft":
            raise _http_error(status.HTTP_409_CONFLICT, "draft_unavailable")
        record = self._get_session(session_id)
        async with record.apply_lock:
            receipt = record.apply_receipt
            if (
                receipt is None
                or record.apply_revision != revision
                or receipt.apply_id != apply_id
            ):
                raise _http_error(status.HTTP_409_CONFLICT, "apply_mismatch")
            if (
                record.commit_operation_id is not None
                and record.commit_state is not CommitState.UNDONE
            ):
                raise _http_error(
                    status.HTTP_409_CONFLICT,
                    "commit_must_be_undone",
                )
            if record.apply_state is ApplyState.REVERTED:
                return self._public_apply(record, revision)
            if record.apply_state is not ApplyState.APPLIED:
                if record.apply_state is ApplyState.FAILED:
                    return self._public_apply(record, revision)
                raise _http_error(status.HTTP_409_CONFLICT, "apply_not_revertible")
            if self.applier is None:
                raise _http_error(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "applier_unavailable",
                )
            record.apply_state = ApplyState.REVERTING
            record.apply_reason = None
            record.updated_at = time.time()
            try:
                reverted = await self.applier.revert(receipt)
                if reverted != receipt:
                    raise ApplierClientError(
                        "Coding revert receipt does not match.",
                        code="invalid_response",
                    )
            except ApplierClientError as exc:
                record.apply_state = ApplyState.FAILED
                record.apply_reason = _safe_code(exc.code)
                record.apply_finished_at = time.time()
                record.updated_at = record.apply_finished_at
                raise _applier_http_error(exc) from exc
            record.apply_state = ApplyState.REVERTED
            record.apply_finished_at = time.time()
            record.state = "reverted"
            record.updated_at = record.apply_finished_at
            return self._public_apply(record, revision)

    async def close_applied_session(self, session_id: str) -> dict[str, bool]:
        await self.cleanup_expired()
        record = self._get_session(session_id)
        if record.state not in {"applied", "reverted"}:
            raise _http_error(status.HTTP_409_CONFLICT, "session_not_frozen")
        async with record.apply_lock:
            try:
                await self.worker.close(record.worker_session_id)
            except CodingWorkerError as exc:
                raise _worker_http_error(exc) from exc
            async with self._lock:
                self._sessions.pop(record.session_id, None)
        return {"closed": True}

    async def stream_events(
        self,
        session_id: str,
        *,
        after: int,
        request: Request,
    ) -> AsyncIterator[str]:
        await self.cleanup_expired()
        record = self._get_session(session_id)
        cursor = after
        while True:
            if await request.is_disconnected():
                return
            pending = [event for event in tuple(record.events) if event["seq"] > cursor]
            terminal_seen = False
            for event in pending:
                cursor = event["seq"]
                terminal_seen = terminal_seen or event["type"] in TERMINAL_EVENT_TYPES
                yield _encode_sse(event)
            if terminal_seen:
                return
            if (
                record.turn_task is not None
                and record.turn_task.done()
                and cursor >= record.last_seq
            ):
                return
            try:
                async with record.condition:
                    await asyncio.wait_for(
                        record.condition.wait(),
                        timeout=HEARTBEAT_SECONDS,
                    )
            except TimeoutError:
                yield ": heartbeat\n\n"

    async def cleanup_expired(self, *, now: float | None = None) -> int:
        current = time.time() if now is None else now
        async with self._lock:
            expired = [
                record
                for record in self._sessions.values()
                if record.state not in {"running", "cancelling"}
                and record.apply_state
                not in {ApplyState.APPLYING, ApplyState.REVERTING}
                and current - record.updated_at >= self.ttl_seconds
            ]
            for record in expired:
                self._sessions.pop(record.session_id, None)
        for record in expired:
            with contextlib.suppress(Exception):
                await self.worker.close(record.worker_session_id)
        return len(expired)

    async def shutdown(self) -> None:
        async with self._lock:
            records = list(self._sessions.values())
            self._sessions.clear()
        for record in records:
            async with record.apply_lock:
                if record.turn_task is not None and not record.turn_task.done():
                    with contextlib.suppress(Exception):
                        await self.worker.cancel(record.worker_session_id)
                    record.turn_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await record.turn_task
                with contextlib.suppress(Exception):
                    await self.worker.close(record.worker_session_id)

    async def _run_turn(self, record: CodingApiSession, prompt: str) -> None:
        try:
            async for event in self.worker.prompt(record.worker_session_id, prompt):
                await self._append_event(record, event)
                if event.kind in {
                    CodingEventKind.TURN_COMPLETED,
                    CodingEventKind.CANCELLED,
                    CodingEventKind.FAILED,
                }:
                    record.state = "ready"
            if record.state in {"running", "cancelling"}:
                record.state = "ready"
        except asyncio.CancelledError:
            raise
        except CodingWorkerError as exc:
            record.state = "failed"
            await self._append_generated_failure(record, exc.code)
        except Exception:
            record.state = "failed"
            await self._append_generated_failure(record, "agent_turn_failed")
        finally:
            record.updated_at = time.time()
            async with record.condition:
                record.condition.notify_all()

    async def _append_generated_failure(
        self,
        record: CodingApiSession,
        code: str,
    ) -> None:
        event = CodingEvent(
            session_id=record.session_id,
            seq=record.last_seq + 1,
            kind=CodingEventKind.FAILED,
            created_at=time.time(),
            data={"code": _safe_code(code)},
        )
        await self._append_event(record, event)

    async def _append_event(
        self,
        record: CodingApiSession,
        event: CodingEvent,
    ) -> None:
        if event.session_id != record.worker_session_id:
            raise CodingWorkerError(
                "Coding worker event referenced another session.",
                code="invalid_worker_response",
            )
        if (
            event.turn_id is not None
            and not SAFE_IDENTIFIER.fullmatch(event.turn_id)
        ) or not math.isfinite(event.created_at):
            raise CodingWorkerError(
                "Coding worker event identity is invalid.",
                code="invalid_worker_response",
            )
        if event.seq <= record.last_seq:
            raise CodingWorkerError(
                "Coding worker event sequence is invalid.",
                code="invalid_worker_response",
            )
        public_event = _public_event(event)
        record.events.append(public_event)
        record.last_seq = event.seq
        record.updated_at = time.time()
        async with record.condition:
            record.condition.notify_all()

    async def _require_available(self) -> None:
        capabilities = await self.capabilities()
        if capabilities["available"] is not True:
            reason = str(capabilities.get("reason") or "unavailable")
            raise _http_error(status.HTTP_503_SERVICE_UNAVAILABLE, reason)

    async def _apply_capability(
        self,
        worker_health: dict[str, Any],
    ) -> dict[str, Any]:
        capability: dict[str, Any] = {
            "configured": False,
            "available": False,
            "target": "dedicated_worktree",
            "requires_verification": True,
            "allows_not_applicable": True,
            "supports_revert": True,
        }
        worker_fingerprint = worker_health.get("snapshot_fingerprint")
        if (
            not isinstance(worker_fingerprint, str)
            or SNAPSHOT_FINGERPRINT_PATTERN.fullmatch(worker_fingerprint) is None
        ):
            return {**capability, "reason": "snapshot_unavailable"}
        if self.applier is None:
            return {**capability, "reason": "applier_not_configured"}
        try:
            health = await self.applier.health()
        except ApplierClientError as exc:
            return {**capability, "reason": _safe_code(exc.code)}
        except Exception:
            return {**capability, "reason": "applier_unavailable"}
        configured = health.get("configured") is True
        response = {**capability, "configured": configured}
        if not configured:
            return {
                **response,
                "reason": _safe_code(
                    health.get("reason") or "applier_not_configured"
                ),
            }
        applier_fingerprint = health.get("snapshot_fingerprint")
        if (
            not isinstance(applier_fingerprint, str)
            or applier_fingerprint != worker_fingerprint
        ):
            return {**response, "reason": "snapshot_mismatch"}
        if health.get("available") is not True:
            return {
                **response,
                "reason": _safe_code(health.get("reason") or "target_not_ready"),
            }
        return {**response, "available": True}

    async def _require_apply_available(self) -> str:
        if self.applier is None:
            raise _http_error(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "applier_unavailable",
            )
        try:
            worker_health = await self.worker.health()
        except CodingWorkerError as exc:
            raise _worker_http_error(exc) from exc
        capability = await self._apply_capability(worker_health)
        if capability["available"] is not True:
            reason = str(capability.get("reason") or "applier_unavailable")
            status_code = (
                status.HTTP_503_SERVICE_UNAVAILABLE
                if reason in {
                    "applier_not_configured",
                    "applier_unavailable",
                    "applier_timeout",
                    "snapshot_unavailable",
                }
                else status.HTTP_409_CONFLICT
            )
            raise _http_error(status_code, reason)
        fingerprint = worker_health.get("snapshot_fingerprint")
        assert isinstance(fingerprint, str)
        return fingerprint

    async def _commit_capability(
        self,
        worker_health: dict[str, Any],
    ) -> dict[str, Any]:
        capability: dict[str, Any] = {
            "configured": False,
            "available": False,
            "target": "isolated_local_repository",
            "requires_apply": True,
            "supports_undo": True,
            "remote_operations": False,
            "max_message_chars": MAX_COMMIT_MESSAGE_CHARS,
        }
        worker_fingerprint = worker_health.get("snapshot_fingerprint")
        if (
            not isinstance(worker_fingerprint, str)
            or SNAPSHOT_FINGERPRINT_PATTERN.fullmatch(worker_fingerprint) is None
        ):
            return {**capability, "reason": "snapshot_unavailable"}
        if self.committer is None:
            return {**capability, "reason": "committer_not_configured"}
        try:
            health = await self.committer.health()
        except CommitterClientError as exc:
            return {**capability, "reason": _safe_code(exc.code)}
        except Exception:
            return {**capability, "reason": "committer_unavailable"}
        configured = health.get("configured") is True
        response = {**capability, "configured": configured}
        if not configured:
            return {
                **response,
                "reason": _safe_code(
                    health.get("reason") or "committer_not_configured"
                ),
            }
        committer_fingerprint = health.get("snapshot_fingerprint")
        if (
            not isinstance(committer_fingerprint, str)
            or committer_fingerprint != worker_fingerprint
        ):
            return {**response, "reason": "snapshot_mismatch"}
        if health.get("available") is not True:
            return {
                **response,
                "reason": _safe_code(health.get("reason") or "repository_not_ready"),
            }
        return {**response, "available": True}

    async def _require_commit_available(self, expected_fingerprint: str) -> None:
        capability = await self._commit_capability(
            {"snapshot_fingerprint": expected_fingerprint}
        )
        if capability["available"] is True:
            return
        reason = str(capability.get("reason") or "committer_unavailable")
        status_code = (
            status.HTTP_503_SERVICE_UNAVAILABLE
            if reason in {
                "committer_not_configured",
                "committer_unavailable",
                "committer_timeout",
                "snapshot_unavailable",
            }
            else status.HTTP_409_CONFLICT
        )
        raise _http_error(status_code, reason)

    async def _current_changes(
        self,
        record: CodingApiSession,
    ) -> dict[str, Any]:
        try:
            payload = await self.worker.changes(record.worker_session_id)
        except CodingWorkerError as exc:
            raise _worker_http_error(exc) from exc
        return _public_changes(payload)

    async def _current_verification(
        self,
        record: CodingApiSession,
        revision: int,
    ) -> dict[str, Any]:
        try:
            payload = await self.worker.verification_status(
                record.worker_session_id,
                revision,
            )
        except CodingWorkerError as exc:
            raise _worker_http_error(exc) from exc
        return _verification_from_worker(payload)

    @staticmethod
    def _require_mutable(record: CodingApiSession) -> None:
        if record.state in {"applied", "reverted"}:
            raise _http_error(status.HTTP_409_CONFLICT, "session_frozen")
        if record.apply_state in {ApplyState.APPLYING, ApplyState.REVERTING}:
            raise _http_error(status.HTTP_409_CONFLICT, "apply_in_progress")

    @staticmethod
    def _public_apply(
        record: CodingApiSession,
        revision: int,
    ) -> dict[str, Any]:
        if record.apply_revision != revision:
            return {
                **not_applied_payload(revision),
                "started_at": None,
                "finished_at": None,
                "reason": None,
            }
        if record.apply_receipt is not None:
            payload = record.apply_receipt.to_public(state=record.apply_state)
        else:
            payload = {
                **not_applied_payload(revision),
                "state": record.apply_state.value,
                "file_count": 0,
            }
        result = {
            **payload,
            "started_at": record.apply_started_at,
            "finished_at": record.apply_finished_at,
            "reason": record.apply_reason,
        }
        if (
            record.commit_operation_id is not None
            and record.commit_state is not CommitState.UNDONE
        ):
            result["can_revert"] = False
        return result

    @staticmethod
    def _public_commit(
        record: CodingApiSession,
        revision: int,
    ) -> dict[str, Any]:
        paths = (
            tuple(item.path for item in record.apply_receipt.files)
            if record.apply_receipt is not None
            and record.apply_revision == revision
            else ("server/placeholder.py",)
        )
        try:
            suggestion = suggest_commit_message(paths)
        except (CodingCommitError, ValueError):
            suggestion = "feature: 更新项目功能"
        if record.commit_revision != revision:
            payload = not_committed_payload(
                revision,
                suggested_message=suggestion,
            )
        elif record.commit_receipt is not None:
            payload = {
                **record.commit_receipt.to_public(state=record.commit_state),
                "suggested_message": suggestion,
            }
        else:
            payload = not_committed_payload(
                revision,
                suggested_message=suggestion,
                state=record.commit_state,
                reason=record.commit_reason,
            )
            if record.commit_message is not None:
                payload["message"] = record.commit_message
        return {
            **payload,
            "started_at": record.commit_started_at,
            "finished_at": record.commit_finished_at,
            "reason": record.commit_reason,
        }

    async def _review_record(self, session_id: str) -> CodingApiSession:
        await self.cleanup_expired()
        if self.mode != "draft":
            raise _http_error(status.HTTP_409_CONFLICT, "draft_unavailable")
        record = self._get_session(session_id)
        if record.state in {"running", "cancelling"} or (
            record.turn_task is not None and not record.turn_task.done()
        ):
            raise _http_error(status.HTTP_409_CONFLICT, "turn_in_progress")
        return record

    async def _require_verification_idle(
        self,
        record: CodingApiSession,
    ) -> None:
        if self.mode != "draft":
            return
        try:
            changes = _public_changes(
                await self.worker.changes(record.worker_session_id)
            )
            payload = await self.worker.verification_status(
                record.worker_session_id,
                changes["revision"],
            )
        except CodingWorkerError as exc:
            raise _worker_http_error(exc) from exc
        verification = _verification_from_worker(payload)
        if (
            verification["state"] == "running"
            and verification["stale"] is False
        ):
            raise _http_error(
                status.HTTP_409_CONFLICT,
                "verification_in_progress",
            )

    def _get_session(self, session_id: str) -> CodingApiSession:
        record = self._sessions.get(session_id)
        if record is None:
            raise _http_error(status.HTTP_404_NOT_FOUND, "session_not_found")
        return record


_service: CodingService | None = None


def configure_coding_service(service: CodingService | None) -> None:
    global _service
    _service = service


def get_coding_service() -> CodingService:
    global _service
    if _service is None:
        enabled = os.getenv("CODING_AGENT_ENABLED", "false").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        socket_path = os.getenv(
            "CODING_AGENT_SOCKET_PATH",
            "/run/modelmirror-coding/coding-runtime.sock",
        )
        applier_socket_path = os.getenv(
            "CODING_APPLIER_SOCKET_PATH",
            "/run/modelmirror-coding-apply/applier.sock",
        )
        committer_socket_path = os.getenv(
            "CODING_COMMITTER_SOCKET_PATH",
            "/run/modelmirror-coding-commit/committer.sock",
        )
        _service = CodingService(
            enabled=enabled,
            worker=CodingWorkerClient(Path(socket_path)),
            applier=CodingApplierClient(Path(applier_socket_path)),
            committer=CodingCommitterClient(Path(committer_socket_path)),
        )
    return _service


@asynccontextmanager
async def _coding_lifespan(_: object) -> AsyncIterator[None]:
    try:
        yield
    finally:
        if _service is not None:
            await _service.shutdown()


router = APIRouter(
    prefix="/api/coding",
    tags=["coding"],
    lifespan=_coding_lifespan,
)


@router.get("/capabilities")
async def coding_capabilities() -> dict[str, Any]:
    return await get_coding_service().capabilities()


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_coding_session() -> dict[str, Any]:
    record = await get_coding_service().create_session()
    return {"id": record.session_id, "status": record.state}


@router.post(
    "/sessions/{session_id}/turns",
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_coding_turn(
    session_id: str,
    payload: CodingTurnRequest,
) -> dict[str, Any]:
    record = await get_coding_service().start_turn(session_id, payload.prompt)
    return {"accepted": True, "status": record.state}


@router.get("/sessions/{session_id}")
async def coding_session_status(
    session_id: str,
    response: Response,
) -> dict[str, str]:
    response.headers["Cache-Control"] = "no-store"
    return await get_coding_service().session_status(session_id)


@router.get("/sessions/{session_id}/events")
async def coding_session_events(
    session_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
) -> StreamingResponse:
    stream = get_coding_service().stream_events(
        session_id,
        after=after,
        request=request,
    )
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/sessions/{session_id}/cancel")
async def cancel_coding_session(session_id: str) -> dict[str, Any]:
    accepted = await get_coding_service().cancel(session_id)
    return {"accepted": accepted}


@router.post(
    "/sessions/{session_id}/verification",
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_coding_verification(
    session_id: str,
    payload: VerificationRevisionRequest,
    response: Response,
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return await get_coding_service().verification_start(
        session_id,
        payload.revision,
    )


@router.get("/sessions/{session_id}/verification")
async def coding_verification_status(
    session_id: str,
    response: Response,
    revision: int = Query(ge=0),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return await get_coding_service().verification_status(
        session_id,
        revision,
    )


@router.post("/sessions/{session_id}/verification/cancel")
async def cancel_coding_verification(
    session_id: str,
    payload: VerificationRevisionRequest,
    response: Response,
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return await get_coding_service().verification_cancel(
        session_id,
        payload.revision,
    )


@router.get("/sessions/{session_id}/changes")
async def coding_session_changes(session_id: str) -> dict[str, Any]:
    return await get_coding_service().changes(session_id)


@router.get("/sessions/{session_id}/diff")
async def coding_session_diff(
    session_id: str,
    path: str = Query(min_length=1, max_length=500),
    revision: int = Query(ge=0),
) -> Response:
    diff = await get_coding_service().diff(session_id, path, revision)
    return Response(
        content=diff,
        media_type="text/x-diff",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/sessions/{session_id}/patch")
async def coding_session_patch(
    session_id: str,
    revision: int = Query(ge=0),
) -> Response:
    patch = await get_coding_service().patch(session_id, revision)
    return Response(
        content=patch,
        media_type="text/x-diff",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": (
                f'attachment; filename="modelmirror-changes-r{revision}.patch"'
            ),
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/sessions/{session_id}/validate")
async def validate_coding_session(session_id: str) -> dict[str, Any]:
    return await get_coding_service().validate(session_id)


@router.post("/sessions/{session_id}/discard")
async def discard_coding_session(session_id: str) -> dict[str, Any]:
    return await get_coding_service().discard(session_id)


@router.post("/sessions/{session_id}/apply")
async def apply_coding_session(
    session_id: str,
    payload: VerificationRevisionRequest,
    response: Response,
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return await get_coding_service().apply(session_id, payload.revision)


@router.get("/sessions/{session_id}/apply")
async def coding_apply_status(
    session_id: str,
    response: Response,
    revision: int = Query(ge=0),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return await get_coding_service().apply_status(session_id, revision)


@router.post("/sessions/{session_id}/apply/revert")
async def revert_coding_session_apply(
    session_id: str,
    payload: ApplyRevertRequest,
    response: Response,
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return await get_coding_service().revert_apply(
        session_id,
        payload.revision,
        payload.apply_id,
    )


@router.post("/sessions/{session_id}/commit")
async def commit_coding_session(
    session_id: str,
    payload: CommitRequest,
    response: Response,
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return await get_coding_service().commit(
        session_id,
        payload.revision,
        payload.apply_id,
        payload.message,
    )


@router.get("/sessions/{session_id}/commit")
async def coding_commit_status(
    session_id: str,
    response: Response,
    revision: int = Query(ge=0),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return await get_coding_service().commit_status(session_id, revision)


@router.post("/sessions/{session_id}/commit/undo")
async def undo_coding_session_commit(
    session_id: str,
    payload: CommitUndoRequest,
    response: Response,
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return await get_coding_service().undo_commit(
        session_id,
        payload.revision,
        payload.apply_id,
        payload.commit_id,
    )


@router.post("/sessions/{session_id}/close")
async def close_applied_coding_session(
    session_id: str,
    response: Response,
) -> dict[str, bool]:
    response.headers["Cache-Control"] = "no-store"
    return await get_coding_service().close_applied_session(session_id)


def _event_from_payload(payload: dict[str, Any]) -> CodingEvent:
    try:
        session_id = payload["session_id"]
        event_type = payload["type"]
        if (
            not isinstance(session_id, str)
            or not SAFE_IDENTIFIER.fullmatch(session_id)
            or not isinstance(event_type, str)
        ):
            raise ValueError("invalid event identity")
        turn_id = payload.get("turn_id")
        if turn_id is not None and (
            not isinstance(turn_id, str) or not SAFE_IDENTIFIER.fullmatch(turn_id)
        ):
            raise ValueError("invalid turn identity")
        data = payload.get("data")
        return CodingEvent(
            session_id=session_id,
            seq=int(payload["seq"]),
            kind=CodingEventKind(event_type),
            created_at=float(payload["created_at"]),
            turn_id=turn_id,
            data=dict(data) if isinstance(data, dict) else {},
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise _http_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "invalid_worker_response",
        ) from exc


def _public_event(event: CodingEvent) -> dict[str, Any]:
    data = event.data
    public_data: dict[str, Any]
    if event.kind is CodingEventKind.PLAN:
        entries = data.get("entries")
        public_entries = []
        if isinstance(entries, list):
            for entry in entries[:50]:
                if not isinstance(entry, dict):
                    continue
                public_entries.append(
                    {
                        "content": _sanitize_text(entry.get("content"), 1_000),
                        "priority": _sanitize_text(entry.get("priority"), 32),
                        "status": _sanitize_text(entry.get("status"), 32),
                    }
                )
        public_data = {"entries": public_entries}
    elif event.kind is CodingEventKind.ANSWER_DELTA:
        public_data = {"text": _sanitize_text(data.get("text"), 16_000)}
    elif event.kind is CodingEventKind.TOOL_STATUS:
        public_data = {
            "tool_call_id": _sanitize_text(data.get("tool_call_id"), 128),
            "title": _sanitize_text(data.get("title"), 200),
            "kind": _sanitize_text(data.get("kind"), 32),
            "status": _sanitize_text(data.get("status"), 32),
        }
    elif event.kind is CodingEventKind.TURN_COMPLETED:
        public_data = {
            "stop_reason": _safe_code(data.get("stop_reason")),
        }
    elif event.kind is CodingEventKind.FAILED:
        public_data = {"code": _safe_code(data.get("code"))}
    else:
        public_data = {}
    return {
        "session_id": event.session_id,
        "seq": event.seq,
        "type": event.kind.value,
        "created_at": event.created_at,
        "turn_id": event.turn_id,
        "data": public_data,
    }


def _sanitize_text(value: Any, limit: int) -> str:
    text = str(value or "")
    text = text.replace("\\workspace", "[workspace]")
    text = text.replace("/workspace", "[workspace]")
    text = WINDOWS_ABSOLUTE_PATH.sub("[redacted-path]", text)
    text = CONTAINER_ABSOLUTE_PATH.sub("[redacted-path]", text)
    return text[:limit]


def _public_changes(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        validated = DraftChangesPayload.model_validate(payload)
    except (TypeError, ValueError) as exc:
        raise _http_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "invalid_worker_response",
        ) from exc
    result = validated.model_dump(by_alias=True)
    for check in result["checks"]:
        check["id"] = _safe_code(check["id"])
        check["label"] = _sanitize_text(check["label"], 100)
        check["message"] = _sanitize_text(check["message"], 500)
    return result


def _verification_from_worker(payload: dict[str, Any]) -> dict[str, Any]:
    verification = payload.get("verification")
    try:
        validated = VerificationPayload.model_validate(verification)
    except (TypeError, ValueError) as exc:
        raise _http_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "invalid_worker_response",
        ) from exc
    result = validated.model_dump(by_alias=True)
    result["reason"] = (
        _safe_code(result["reason"])
        if result["reason"] is not None
        else None
    )
    for step in result["steps"]:
        step["label"] = _sanitize_text(step["label"], 100)
        step["summary"] = sanitize_verification_output(
            step["summary"],
            limit=500,
            keep_tail=False,
        ).text
        step["details"] = sanitize_verification_output(
            step["details"],
            limit=16_000,
        ).text
    return result


def _safe_diff(diff: str, *, expected_path: str | None = None) -> str:
    try:
        encoded = diff.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise _http_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "invalid_worker_response",
        ) from exc
    if (
        not diff
        or len(encoded) > DEFAULT_DRAFT_LIMITS.max_patch_bytes
        or "\x00" in diff
    ):
        raise _http_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "invalid_worker_response",
        )
    paths: list[str] = []
    for line in diff.splitlines():
        if not line.startswith("diff --git "):
            continue
        match = SAFE_DIFF_HEADER.fullmatch(line)
        if match is None or match.group(1) != match.group(2):
            raise _http_error(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "invalid_worker_response",
            )
        try:
            paths.append(DraftWorkspace.normalize_relative_path(match.group(1)))
        except DraftPolicyError as exc:
            raise _http_error(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "invalid_worker_response",
            ) from exc
    if (
        not paths
        or len(paths) > DEFAULT_DRAFT_LIMITS.max_changed_files
        or len(set(paths)) != len(paths)
        or (expected_path is not None and paths != [expected_path])
    ):
        raise _http_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "invalid_worker_response",
        )
    return diff


def _safe_code(value: Any) -> str:
    code = re.sub(r"[^a-z0-9_-]", "_", str(value or "unknown").lower())
    return code[:64] or "unknown"


def _verification_allows_apply(
    verification: dict[str, Any],
    paths: list[str],
) -> bool:
    if (
        verification["state"] != "completed"
        or verification["stale"] is not False
    ):
        return False
    if verification["result"] == "passed":
        return True
    return (
        verification["result"] == "not_applicable"
        and verification["reason"] == "documentation_only"
        and select_verification_plan(paths).reason == "documentation_only"
    )


def _verification_apply_reason(verification: dict[str, Any]) -> str:
    if verification["stale"] is True:
        return "verification_stale"
    if verification["state"] == "running":
        return "verification_in_progress"
    if verification["state"] == "cancelled":
        return "verification_cancelled"
    if verification["result"] == "failed":
        return "verification_failed"
    if verification["reason"] == "dependency_change_unsupported":
        return "dependency_change_unsupported"
    return "verification_required"


def _normalize_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in {"readonly", "draft"} else "invalid"


def _encode_sse(event: dict[str, Any]) -> str:
    return (
        f"id: {event['seq']}\n"
        f"data: {json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


def _http_error(status_code: int, code: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": _safe_code(code)},
        headers={"Cache-Control": "no-store"},
    )


def _worker_http_error(exc: CodingWorkerError) -> HTTPException:
    if exc.code in {
        "concurrency_limit",
        "turn_in_progress",
        "draft_busy",
        "draft_unavailable",
        "stale_revision",
        "validation_failed",
        "draft_is_empty",
        "verification_in_progress",
    }:
        return _http_error(status.HTTP_409_CONFLICT, exc.code)
    if exc.code in {
        "invalid_prompt",
        "prompt_too_long",
        "invalid_request",
        "invalid_path",
    }:
        return _http_error(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.code)
    if exc.code in {
        "session_not_found",
        "change_not_found",
        "verification_not_found",
    }:
        return _http_error(status.HTTP_404_NOT_FOUND, exc.code)
    return _http_error(status.HTTP_503_SERVICE_UNAVAILABLE, exc.code)


def _applier_http_error(exc: ApplierClientError) -> HTTPException:
    if exc.code in {
        "already_reverted",
        "operation_conflict",
        "patch_apply_failed",
        "revert_conflict",
        "snapshot_mismatch",
        "target_changed",
        "target_not_ready",
        "unsafe_workspace_root",
    }:
        return _http_error(status.HTTP_409_CONFLICT, exc.code)
    if exc.code in {"invalid_patch", "invalid_path", "invalid_request"}:
        return _http_error(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.code)
    return _http_error(status.HTTP_503_SERVICE_UNAVAILABLE, exc.code)


def _committer_http_error(exc: CommitterClientError) -> HTTPException:
    if exc.code in {
        "already_undone",
        "baseline_mismatch",
        "commit_already_exists",
        "commit_conflict",
        "dirty_index",
        "operation_conflict",
        "repository_has_remote",
        "repository_not_independent",
        "shared_git_directory",
        "snapshot_mismatch",
        "target_changed",
        "undo_conflict",
        "unsafe_repository",
        "wrong_branch",
    }:
        return _http_error(status.HTTP_409_CONFLICT, exc.code)
    if exc.code in {"invalid_author", "invalid_message", "invalid_request"}:
        return _http_error(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.code)
    return _http_error(status.HTTP_503_SERVICE_UNAVAILABLE, exc.code)
