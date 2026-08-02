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
    ApplyFileReceipt,
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
from .cycles import MAX_INCREMENTAL_CYCLES, CodingCycle, CodingCycleHistory, CycleState
from .models import CodingEvent, CodingEventKind
from .patch_policy import SNAPSHOT_FINGERPRINT_PATTERN
from .publisher_client import (
    CodingPublisherClient,
    PublisherClientError,
)
from .publish_models import (
    MAX_PR_BODY_CHARS,
    MAX_PR_TITLE_CHARS,
    CodingPublishError,
    PublishCommit,
    PublishManifest,
    PublishReceipt,
    PublishState,
    normalize_pr_body,
    normalize_pr_title,
)
from .recovery import (
    DEFAULT_RECOVERY_RETENTION_SECONDS,
    MAX_RECOVERY_RETENTION_SECONDS,
    MIN_RECOVERY_RETENTION_SECONDS,
    CodingRecoveryError,
    CodingRecoveryStore,
    RecoveryPayload,
    RecoveryRecord,
    RecoveryState,
)
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
    "published",
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

    async def restore_session(
        self,
        *,
        revision: int,
        patch: str,
        paths: list[str],
        snapshot_fingerprint: str,
        verification: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    async def recovery_snapshot(self, session_id: str) -> dict[str, Any]: ...

    def prompt(
        self,
        session_id: str,
        prompt: str,
    ) -> AsyncIterator[CodingEvent]: ...

    async def cancel(self, session_id: str) -> bool: ...

    async def close(self, session_id: str) -> None: ...

    async def changes(self, session_id: str) -> dict[str, Any]: ...

    async def diff(self, session_id: str, path: str, revision: int) -> str: ...

    async def patch(
        self, session_id: str, revision: int, *, scope: str = "current"
    ) -> str: ...

    async def checkpoint_cycle(
        self, session_id: str, revision: int
    ) -> dict[str, Any]: ...

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

    async def reconcile(
        self,
        *,
        operation_id: str,
        revision: int,
        patch: str,
        paths: list[str],
        expected_fingerprint: str,
    ) -> tuple[str, ApplyReceipt | None]: ...


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

    async def reconcile(
        self,
        *,
        operation_id: str,
        apply_receipt: ApplyReceipt,
        message: str,
    ) -> tuple[str, CommitReceipt | None]: ...


class PublisherClient(Protocol):
    async def health(self) -> dict[str, Any]: ...

    async def publish(self, manifest: PublishManifest) -> PublishReceipt: ...

    async def reconcile(
        self,
        manifest: PublishManifest,
    ) -> tuple[str, PublishReceipt | None]: ...

    async def mark_ready(
        self,
        manifest: PublishManifest,
        receipt: PublishReceipt,
    ) -> PublishReceipt: ...


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


class ApplyRequest(VerificationRevisionRequest):
    confirm_quality_risks: bool = Field(default=False, strict=True)


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


class ContinueCycleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=0)
    commit_id: str = Field(min_length=20, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class PublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=1)
    commit_id: str = Field(min_length=20, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    title: str = Field(min_length=1, max_length=MAX_PR_TITLE_CHARS)
    body: str = Field(default="", max_length=MAX_PR_BODY_CHARS)

    @field_validator("title")
    @classmethod
    def title_must_be_safe(cls, value: str) -> str:
        try:
            return normalize_pr_title(value)
        except ValueError as exc:
            raise ValueError("Pull request title is invalid") from exc

    @field_validator("body")
    @classmethod
    def body_must_be_safe(cls, value: str) -> str:
        try:
            return normalize_pr_body(value)
        except ValueError as exc:
            raise ValueError("Pull request body is invalid") from exc


class PublishReadyRequest(VerificationRevisionRequest):
    revision: int = Field(ge=1)
    publish_id: str = Field(min_length=20, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


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
    publish_state: PublishState = PublishState.NOT_PUBLISHED
    publish_revision: int | None = None
    publish_manifest: PublishManifest | None = None
    publish_receipt: PublishReceipt | None = None
    publish_reason: str | None = None
    publish_started_at: float | None = None
    publish_finished_at: float | None = None
    publish_task: asyncio.Task[None] | None = None
    recovery_id: str | None = None
    recovery_created_at: float | None = None
    recovery_conflict: str | None = None
    cycle_number: int = 1
    cycle_history: CodingCycleHistory = field(default_factory=CodingCycleHistory)


class CodingService:
    """Ephemeral single-session API state around the isolated Coding Worker."""

    def __init__(
        self,
        *,
        enabled: bool,
        worker: WorkerClient,
        applier: ApplierClient | None = None,
        committer: CommitterClient | None = None,
        publisher: PublisherClient | None = None,
        recovery_store: CodingRecoveryStore | None = None,
        recovery_enabled: bool = False,
        recovery_reason: str | None = None,
        incremental_enabled: bool | None = None,
        publish_enabled: bool | None = None,
        ttl_seconds: float = SESSION_TTL_SECONDS,
        mode: str | None = None,
    ) -> None:
        self.enabled = enabled
        self.worker = worker
        self.applier = applier
        self.committer = committer
        self.publisher = publisher
        self.recovery_store = recovery_store
        self.recovery_enabled = recovery_enabled
        self.recovery_reason = recovery_reason
        self.incremental_enabled = (
            os.getenv("CODING_INCREMENTAL_ENABLED", "false").strip().lower()
            in {"1", "true", "yes", "on"}
            if incremental_enabled is None
            else incremental_enabled
        )
        self.publish_enabled = (
            os.getenv("CODING_GITHUB_PUBLISH_ENABLED", "false").strip().lower()
            in {"1", "true", "yes", "on"}
            if publish_enabled is None
            else publish_enabled
        )
        self.ttl_seconds = ttl_seconds
        self.mode = _normalize_mode(
            mode if mode is not None else os.getenv("CODING_AGENT_MODE", "readonly")
        )
        self._sessions: dict[str, CodingApiSession] = {}
        self._lock = asyncio.Lock()

    async def capabilities(self) -> dict[str, Any]:
        recovery = await self.recovery_status(check_worker=False)
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
            "recovery": {
                "enabled": recovery["enabled"],
                "available": recovery["available"],
                "pending": recovery["pending"],
                "retention_seconds": recovery["retention_seconds"],
                "restores_conversation": False,
                **(
                    {"reason": recovery["reason"]}
                    if recovery.get("reason") is not None
                    else {}
                ),
            },
            "incremental": {
                "enabled": self.incremental_enabled,
                "available": False,
                "max_cycles": MAX_INCREMENTAL_CYCLES,
                "requires_recovery": True,
                "commit_strategy": "linear",
                "undo_scope": "latest",
            },
            "publish": {
                "enabled": self.publish_enabled,
                "configured": False,
                "available": False,
                "provider": "github",
                "target": "fixed_repository",
                "default_pr_state": "draft",
                "supports_mark_ready": True,
                "requires_exact_base": True,
                "remote_merge": False,
                "reason": (
                    "publisher_not_configured"
                    if self.publish_enabled
                    else "publish_disabled"
                ),
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
                "requires_verification": False,
                "allows_quality_risk_confirmation": True,
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
            apply_capability, commit_capability, publish_capability = (
                await asyncio.gather(
                    self._apply_capability(health),
                    self._commit_capability(health),
                    self._publish_capability(),
                )
            )
            response["apply"] = apply_capability
            response["host_apply"] = apply_capability["available"] is True
            response["commit"] = commit_capability
            response["publish"] = publish_capability
            response["incremental"]["available"] = bool(
                self.incremental_enabled
                and recovery["available"]
                and response["apply"].get("available") is True
                and response["commit"].get("available") is True
            )
            if self.incremental_enabled and not response["incremental"]["available"]:
                response["incremental"]["reason"] = (
                    "incremental_dependencies_unavailable"
                )
        response["available"] = True
        return response

    async def recovery_status(
        self,
        *,
        check_worker: bool = True,
    ) -> dict[str, Any]:
        retention = (
            self.recovery_store.retention_seconds
            if self.recovery_store is not None
            else DEFAULT_RECOVERY_RETENTION_SECONDS
        )
        base: dict[str, Any] = {
            "enabled": self.recovery_enabled,
            "available": self.recovery_store is not None,
            "pending": False,
            "retention_seconds": retention,
            "restores_conversation": False,
        }
        if not self.recovery_enabled:
            return {**base, "reason": "recovery_disabled"}
        if self.recovery_store is None:
            return {
                **base,
                "reason": _safe_code(
                    self.recovery_reason or "recovery_storage_unavailable"
                ),
            }
        record = await self._load_recovery_record(required=False)
        if record is None:
            if self.recovery_reason is not None:
                return {**base, "available": False, "reason": self.recovery_reason}
            return base
        can_resume = self.enabled and self.mode == "draft"
        reason: str | None = None
        if not self.enabled:
            can_resume = False
            reason = "disabled"
        elif self.mode != "draft":
            can_resume = False
            reason = "draft_unavailable"
        elif check_worker:
            try:
                health = await self.worker.health()
                fingerprint = health.get("snapshot_fingerprint")
                if (
                    health.get("ok") is not True
                    or health.get("configured") is not True
                    or fingerprint != record.snapshot_fingerprint
                ):
                    can_resume = False
                    reason = (
                        "snapshot_mismatch"
                        if isinstance(fingerprint, str)
                        else "worker_unavailable"
                    )
            except Exception:
                can_resume = False
                reason = "worker_unavailable"
        return {
            **base,
            **record.to_public(can_resume=can_resume, reason=reason),
        }

    async def recovery_patch(self) -> tuple[int, str]:
        record = await self._require_recovery_record()
        return record.revision, _safe_diff(record.payload.patch)

    async def discard_recovery(self) -> dict[str, bool]:
        conflict_record: CodingApiSession | None = None
        async with self._lock:
            active = list(self._sessions.values())
            if active and not (
                len(active) == 1 and active[0].state == "conflict"
            ):
                raise _http_error(status.HTTP_409_CONFLICT, "session_active")
            if active:
                conflict_record = active[0]
        record = await self._require_recovery_record()
        if record.state is RecoveryState.PUBLISHED:
            raise _http_error(
                status.HTTP_409_CONFLICT,
                "published_recovery_requires_resume",
            )
        assert self.recovery_store is not None
        try:
            discarded = await asyncio.to_thread(
                self.recovery_store.discard,
                recovery_id=record.recovery_id,
            )
        except CodingRecoveryError as exc:
            self.recovery_reason = _safe_code(exc.code)
            raise _recovery_http_error(exc) from exc
        if not discarded:
            raise _http_error(status.HTTP_409_CONFLICT, "recovery_changed")
        if conflict_record is not None:
            with contextlib.suppress(Exception):
                await self.worker.close(conflict_record.worker_session_id)
            async with self._lock:
                self._sessions.pop(conflict_record.session_id, None)
        return {"discarded": True}

    async def create_session(self) -> CodingApiSession:
        await self._require_available()
        await self.cleanup_expired()
        if await self._load_recovery_record() is not None:
            raise _http_error(status.HTTP_409_CONFLICT, "recovery_pending")
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

    async def resume_recovery(self) -> CodingApiSession:
        health = await self._require_available()
        if self.mode != "draft":
            raise _http_error(status.HTTP_409_CONFLICT, "draft_unavailable")
        await self.cleanup_expired()
        recovery = await self._require_recovery_record()
        async with self._lock:
            if self._sessions:
                raise _http_error(status.HTTP_409_CONFLICT, "concurrency_limit")
        if health.get("snapshot_fingerprint") != recovery.snapshot_fingerprint:
            raise _http_error(status.HTTP_409_CONFLICT, "snapshot_mismatch")
        cumulative_changes = _public_changes(recovery.payload.changes)
        active_changes = (
            _public_changes(recovery.payload.active_changes)
            if recovery.payload.active_changes is not None
            else None
        )
        active_patch = recovery.payload.active_patch
        paths = (
            [item["path"] for item in active_changes["files"]]
            if active_changes is not None
            else []
        )
        base_paths = _diff_paths(recovery.payload.base_patch)
        verification = (
            _verification_from_worker(
                {"verification": recovery.payload.verification}
            )
            if recovery.payload.verification is not None
            else None
        )
        try:
            result = await self.worker.restore_session(
                revision=recovery.revision,
                patch=active_patch,
                paths=paths,
                base_patch=recovery.payload.base_patch,
                base_paths=base_paths,
                snapshot_fingerprint=recovery.snapshot_fingerprint,
                verification=verification,
            )
        except CodingWorkerError as exc:
            raise _worker_http_error(exc) from exc
        session_id = result.get("session_id")
        event_data = result.get("event")
        restored_changes = result.get("changes")
        if (
            not isinstance(session_id, str)
            or SAFE_IDENTIFIER.fullmatch(session_id) is None
            or result.get("mode") != self.mode
            or not isinstance(event_data, dict)
            or (
                active_changes is not None
                and _public_changes(restored_changes) != active_changes
            )
            or (
                active_changes is None
                and bool(_public_changes(restored_changes)["files"])
            )
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
            recovery_id=recovery.recovery_id,
            recovery_created_at=recovery.created_at,
            cycle_number=len(recovery.payload.cycles) + 1,
            cycle_history=CodingCycleHistory(recovery.payload.cycles),
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
        try:
            self._hydrate_recovered_session(record, recovery)
            await self._reconcile_recovered_operations(
                record,
                active_patch,
                paths,
                recovery.snapshot_fingerprint,
            )
            await self._persist_recovery(record, required=True)
        except Exception:
            with contextlib.suppress(Exception):
                await self.worker.close(session_id)
            raise
        async with self._lock:
            if self._sessions:
                with contextlib.suppress(Exception):
                    await self.worker.close(session_id)
                raise _http_error(status.HTTP_409_CONFLICT, "concurrency_limit")
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

    async def patch(
        self,
        session_id: str,
        revision: int,
        *,
        scope: str = "current",
    ) -> str:
        record = await self._review_record(session_id)
        if scope not in {"current", "cumulative"}:
            raise _http_error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_scope")
        try:
            patch = await self.worker.patch(
                record.worker_session_id,
                revision,
                scope=scope,
            )
        except CodingWorkerError as exc:
            raise _worker_http_error(exc) from exc
        return _safe_diff(patch)

    async def history(self, session_id: str) -> dict[str, Any]:
        record = await self._review_record(session_id)
        latest_commit = (
            self._public_commit(record, record.commit_revision)
            if record.commit_revision is not None
            and record.commit_state is CommitState.COMMITTED
            else None
        )
        return {
            "active_cycle": record.cycle_number,
            "completed_count": len(record.cycle_history.cycles),
            "max_cycles": MAX_INCREMENTAL_CYCLES,
            "can_continue": bool(
                self.incremental_enabled
                and latest_commit is not None
                and record.cycle_number < MAX_INCREMENTAL_CYCLES
                and record.recovery_conflict is None
                and record.publish_manifest is None
            ),
            "current_commit": latest_commit,
            "cycles": record.cycle_history.to_public(),
        }

    async def continue_cycle(
        self,
        session_id: str,
        revision: int,
        commit_id: str,
    ) -> dict[str, Any]:
        record = await self._review_record(session_id)
        async with record.apply_lock:
            if record.publish_manifest is not None:
                raise _http_error(status.HTTP_409_CONFLICT, "session_published")
            if not self.incremental_enabled or self.recovery_store is None:
                raise _http_error(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "incremental_unavailable",
                )
            if record.cycle_number >= MAX_INCREMENTAL_CYCLES:
                raise _http_error(status.HTTP_409_CONFLICT, "cycle_limit_reached")
            receipt = record.commit_receipt
            if (
                receipt is None
                or record.commit_state is not CommitState.COMMITTED
                or record.commit_revision != revision
                or receipt.commit_id != commit_id
            ):
                raise _http_error(status.HTTP_409_CONFLICT, "commit_mismatch")
            changes = await self._current_changes(record)
            if changes["revision"] != revision or not changes["files"]:
                raise _http_error(status.HTTP_409_CONFLICT, "stale_revision")
            verification = await self._current_verification(record, revision)
            try:
                patch = _safe_diff(
                    await self.worker.patch(
                        record.worker_session_id,
                        revision,
                        scope="current",
                    )
                )
                cycle = CodingCycle(
                    number=record.cycle_number,
                    revision=revision,
                    state=CycleState.COMMITTED,
                    patch=patch,
                    changes=changes,
                    verification=verification,
                    apply=_apply_storage_payload(record),
                    commit=_commit_storage_payload(record),
                    created_at=record.apply_started_at or record.updated_at,
                    updated_at=time.time(),
                )
                next_history = record.cycle_history.append(cycle)
                next_changes = await self.worker.checkpoint_cycle(
                    record.worker_session_id,
                    revision,
                )
            except (CodingWorkerError, ValueError) as exc:
                if isinstance(exc, CodingWorkerError):
                    raise _worker_http_error(exc) from exc
                raise _http_error(
                    status.HTTP_409_CONFLICT,
                    "cycle_transition_invalid",
                ) from exc
            record.cycle_history = next_history
            record.cycle_number += 1
            record.state = "ready"
            record.apply_state = ApplyState.NOT_APPLIED
            record.apply_revision = None
            record.apply_operation_id = None
            record.apply_receipt = None
            record.apply_reason = None
            record.apply_started_at = None
            record.apply_finished_at = None
            record.commit_state = CommitState.NOT_COMMITTED
            record.commit_revision = None
            record.commit_operation_id = None
            record.commit_message = None
            record.commit_receipt = None
            record.commit_reason = None
            record.commit_started_at = None
            record.commit_finished_at = None
            record.updated_at = time.time()
            if _public_changes(next_changes)["files"]:
                record.state = "conflict"
                record.recovery_conflict = "checkpoint_not_empty"
                raise _http_error(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "checkpoint_not_empty",
                )
            await self._persist_recovery(record, required=True)
            return await self.history(session_id)

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
            await self._persist_recovery(record, required=True)
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
            await self._persist_recovery(record, required=True)
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
            await self._persist_recovery(record, required=True)
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
        result = _verification_from_worker(payload)
        if result["state"] == "completed":
            await self._persist_recovery(record, required=True)
        return result

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
            await self._persist_recovery(record, required=True)
        result = _verification_from_worker(payload)
        result["accepted"] = payload.get("accepted") is True
        return result

    async def apply(
        self,
        session_id: str,
        revision: int,
        *,
        confirm_quality_risks: bool = False,
    ) -> dict[str, Any]:
        record = await self._review_record(session_id)
        async with record.apply_lock:
            self._require_not_recovery_conflict(record)
            if record.apply_revision == revision and (
                record.apply_state in {ApplyState.APPLIED, ApplyState.REVERTED}
                or (
                    record.apply_state is ApplyState.FAILED
                    and record.apply_receipt is not None
                )
            ):
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
            ) and not confirm_quality_risks:
                raise _http_error(status.HTTP_409_CONFLICT, "validation_failed")
            verification = await self._current_verification(record, revision)
            paths = [item["path"] for item in changes["files"]]
            if verification["state"] == "running":
                raise _http_error(
                    status.HTTP_409_CONFLICT,
                    "verification_in_progress",
                )
            if (
                not _verification_allows_apply(verification, paths)
                and not confirm_quality_risks
            ):
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
            operation_id = record.apply_operation_id or secrets.token_urlsafe(18)
            record.apply_state = ApplyState.APPLYING
            record.apply_revision = revision
            record.apply_operation_id = operation_id
            record.apply_reason = None
            record.apply_started_at = time.time()
            record.apply_finished_at = None
            record.updated_at = time.time()
            try:
                await self._persist_recovery(record, required=True)
            except HTTPException:
                record.apply_state = ApplyState.FAILED
                record.apply_reason = "recovery_storage_unavailable"
                record.apply_finished_at = time.time()
                raise
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
                await self._persist_recovery(record, required=False)
                raise _applier_http_error(exc) from exc
            record.apply_receipt = receipt
            record.apply_state = ApplyState.APPLIED
            record.apply_finished_at = time.time()
            record.state = "applied"
            record.updated_at = record.apply_finished_at
            await self._persist_recovery(record, required=True)
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
            self._require_not_recovery_conflict(record)
            if record.publish_manifest is not None:
                raise _http_error(status.HTTP_409_CONFLICT, "session_published")
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
                await self._persist_recovery(record, required=True)
            except HTTPException:
                record.commit_state = CommitState.FAILED
                record.commit_reason = "recovery_storage_unavailable"
                record.commit_finished_at = time.time()
                raise
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
                await self._persist_recovery(record, required=False)
                raise _committer_http_error(exc) from exc
            record.commit_receipt = receipt
            record.commit_state = CommitState.COMMITTED
            record.commit_finished_at = time.time()
            record.updated_at = record.commit_finished_at
            await self._persist_recovery(record, required=True)
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

    async def publish(
        self,
        session_id: str,
        revision: int,
        commit_id: str,
        title: str,
        body: str,
    ) -> dict[str, Any]:
        record = await self._review_record(session_id)
        async with record.apply_lock:
            self._require_not_recovery_conflict(record)
            existing = record.publish_manifest
            if existing is not None:
                if (
                    existing.revision != revision
                    or existing.commits[-1].commit_id != commit_id
                    or existing.title != title
                    or existing.body != body
                ):
                    raise _http_error(status.HTTP_409_CONFLICT, "publish_already_started")
                if record.publish_state in {PublishState.DRAFT, PublishState.READY}:
                    return self._public_publish(record, revision)
                if (
                    record.publish_state in {
                        PublishState.PUBLISHING,
                        PublishState.MARKING_READY,
                    }
                    and record.publish_task is not None
                    and not record.publish_task.done()
                ):
                    return self._public_publish(record, revision)
                if record.publish_state is PublishState.CONFLICT:
                    return self._public_publish(record, revision)
                await self._require_publish_available()
                manifest = existing
            else:
                receipt = record.commit_receipt
                if (
                    receipt is None
                    or record.commit_state is not CommitState.COMMITTED
                    or record.commit_revision != revision
                    or receipt.commit_id != commit_id
                ):
                    raise _http_error(status.HTTP_409_CONFLICT, "commit_mismatch")
                await self._require_publish_available()
                try:
                    manifest = self._build_publish_manifest(
                        record,
                        revision=revision,
                        publish_id=secrets.token_urlsafe(18),
                        title=title,
                        body=body,
                    )
                except (CodingPublishError, TypeError, ValueError) as exc:
                    raise _http_error(
                        status.HTTP_409_CONFLICT,
                        "publish_manifest_invalid",
                    ) from exc
                record.publish_manifest = manifest
                record.publish_revision = revision
                record.publish_started_at = time.time()
            record.publish_state = PublishState.PUBLISHING
            record.publish_reason = None
            record.publish_finished_at = None
            record.updated_at = time.time()
            await self._persist_recovery(record, required=True)
            record.publish_task = asyncio.create_task(
                self._run_publish(record, manifest)
            )
            return self._public_publish(record, revision)

    async def publish_status(
        self,
        session_id: str,
        revision: int,
    ) -> dict[str, Any]:
        await self.cleanup_expired()
        if self.mode != "draft":
            raise _http_error(status.HTTP_409_CONFLICT, "draft_unavailable")
        return self._public_publish(self._get_session(session_id), revision)

    async def mark_publish_ready(
        self,
        session_id: str,
        revision: int,
        publish_id: str,
    ) -> dict[str, Any]:
        await self.cleanup_expired()
        record = self._get_session(session_id)
        async with record.apply_lock:
            self._require_not_recovery_conflict(record)
            manifest = record.publish_manifest
            receipt = record.publish_receipt
            if (
                manifest is None
                or receipt is None
                or manifest.publish_id != publish_id
                or manifest.revision != revision
                or receipt.publish_id != publish_id
            ):
                raise _http_error(status.HTTP_409_CONFLICT, "publish_mismatch")
            if record.publish_state is PublishState.READY:
                return self._public_publish(record, revision)
            if (
                record.publish_state is PublishState.MARKING_READY
                and record.publish_task is not None
                and not record.publish_task.done()
            ):
                return self._public_publish(record, revision)
            if record.publish_state is PublishState.MARKING_READY:
                record.publish_state = PublishState.DRAFT
            if receipt.state is PublishState.READY:
                record.publish_state = PublishState.READY
                record.publish_reason = None
                record.publish_finished_at = time.time()
                record.state = "published"
                await self._persist_recovery(record, required=True)
                return self._public_publish(record, revision)
            if record.publish_state not in {PublishState.DRAFT, PublishState.FAILED}:
                raise _http_error(status.HTTP_409_CONFLICT, "publish_not_ready")
            await self._require_publish_available()
            record.publish_state = PublishState.MARKING_READY
            record.publish_reason = None
            record.updated_at = time.time()
            await self._persist_recovery(record, required=True)
            record.publish_task = asyncio.create_task(
                self._run_mark_ready(record, manifest, receipt)
            )
            return self._public_publish(record, revision)

    async def _run_publish(
        self,
        record: CodingApiSession,
        manifest: PublishManifest,
    ) -> None:
        try:
            assert self.publisher is not None
            receipt = await self.publisher.publish(manifest)
        except asyncio.CancelledError:
            raise
        except PublisherClientError as exc:
            async with record.apply_lock:
                self._record_publish_failure(record, exc.code)
                await self._persist_recovery(record, required=False)
            return
        except Exception:
            async with record.apply_lock:
                self._record_publish_failure(record, "publisher_internal_error")
                await self._persist_recovery(record, required=False)
            return
        async with record.apply_lock:
            if (
                receipt.publish_id != manifest.publish_id
                or receipt.revision != manifest.revision
                or receipt.branch != manifest.branch
                or receipt.head_sha != manifest.head_sha
            ):
                self._record_publish_failure(record, "invalid_response")
                await self._persist_recovery(record, required=False)
                return
            record.publish_receipt = receipt
            if receipt.state is not PublishState.DRAFT:
                self._mark_recovery_conflict(record, "remote_pr_conflict")
                record.publish_state = PublishState.CONFLICT
                await self._persist_recovery(record, required=False)
                return
            try:
                await self._persist_recovery(record, required=True)
                record.publish_state = PublishState.DRAFT
                record.publish_reason = None
                record.publish_finished_at = time.time()
                record.state = "published"
                record.updated_at = record.publish_finished_at
                await self._persist_recovery(record, required=True)
            except HTTPException:
                self._record_publish_failure(record, "recovery_storage_unavailable")

    async def _run_mark_ready(
        self,
        record: CodingApiSession,
        manifest: PublishManifest,
        receipt: PublishReceipt,
    ) -> None:
        try:
            assert self.publisher is not None
            ready = await self.publisher.mark_ready(manifest, receipt)
        except asyncio.CancelledError:
            raise
        except PublisherClientError as exc:
            async with record.apply_lock:
                self._record_publish_failure(record, exc.code)
                await self._persist_recovery(record, required=False)
            return
        except Exception:
            async with record.apply_lock:
                self._record_publish_failure(record, "publisher_internal_error")
                await self._persist_recovery(record, required=False)
            return
        async with record.apply_lock:
            if (
                ready.publish_id != manifest.publish_id
                or ready.revision != manifest.revision
                or ready.pr_number != receipt.pr_number
                or ready.state is not PublishState.READY
            ):
                self._record_publish_failure(record, "invalid_response")
                await self._persist_recovery(record, required=False)
                return
            record.publish_receipt = ready
            try:
                await self._persist_recovery(record, required=True)
                record.publish_state = PublishState.READY
                record.publish_reason = None
                record.publish_finished_at = time.time()
                record.state = "published"
                record.updated_at = record.publish_finished_at
                await self._persist_recovery(record, required=True)
            except HTTPException:
                self._record_publish_failure(record, "recovery_storage_unavailable")

    def _record_publish_failure(
        self,
        record: CodingApiSession,
        reason: str,
    ) -> None:
        safe_reason = _safe_code(reason)
        record.publish_state = (
            PublishState.CONFLICT
            if _publish_error_is_conflict(safe_reason)
            else PublishState.FAILED
        )
        record.publish_reason = safe_reason
        record.publish_finished_at = time.time()
        record.updated_at = record.publish_finished_at
        if record.publish_state is PublishState.CONFLICT:
            record.state = "conflict"
            record.recovery_conflict = safe_reason

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
            self._require_not_recovery_conflict(record)
            if record.publish_manifest is not None:
                raise _http_error(status.HTTP_409_CONFLICT, "session_published")
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
                await self._persist_recovery(record, required=True)
            except HTTPException:
                record.commit_state = CommitState.COMMITTED
                record.commit_reason = "recovery_storage_unavailable"
                raise
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
                await self._persist_recovery(record, required=False)
                raise _committer_http_error(exc) from exc
            record.commit_state = CommitState.UNDONE
            record.commit_reason = None
            record.commit_finished_at = time.time()
            record.updated_at = record.commit_finished_at
            await self._persist_recovery(record, required=True)
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
            self._require_not_recovery_conflict(record)
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
                await self._persist_recovery(record, required=True)
            except HTTPException:
                record.apply_state = ApplyState.APPLIED
                record.apply_reason = "recovery_storage_unavailable"
                raise
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
                await self._persist_recovery(record, required=False)
                raise _applier_http_error(exc) from exc
            record.apply_state = ApplyState.REVERTED
            record.apply_finished_at = time.time()
            record.state = "reverted"
            record.updated_at = record.apply_finished_at
            await self._persist_recovery(record, required=True)
            return self._public_apply(record, revision)

    async def close_applied_session(self, session_id: str) -> dict[str, bool]:
        await self.cleanup_expired()
        record = self._get_session(session_id)
        if record.state not in {"applied", "published", "reverted"}:
            raise _http_error(status.HTTP_409_CONFLICT, "session_not_frozen")
        async with record.apply_lock:
            if record.recovery_id is not None and self.recovery_store is not None:
                try:
                    discarded = await asyncio.to_thread(
                        self.recovery_store.discard,
                        recovery_id=record.recovery_id,
                    )
                except CodingRecoveryError as exc:
                    raise _recovery_http_error(exc) from exc
                if not discarded:
                    raise _http_error(
                        status.HTTP_409_CONFLICT,
                        "recovery_changed",
                    )
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
                and record.publish_state
                not in {PublishState.PUBLISHING, PublishState.MARKING_READY}
                and current - record.updated_at >= self.ttl_seconds
            ]
            for record in expired:
                self._sessions.pop(record.session_id, None)
        for record in expired:
            await self._persist_recovery(record, required=False)
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
                if record.publish_task is not None and not record.publish_task.done():
                    record.publish_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await record.publish_task
                await self._persist_recovery(record, required=False)
                with contextlib.suppress(Exception):
                    await self.worker.close(record.worker_session_id)

    async def _run_turn(self, record: CodingApiSession, prompt: str) -> None:
        try:
            terminal_event: CodingEvent | None = None
            async for event in self.worker.prompt(record.worker_session_id, prompt):
                if event.kind in {
                    CodingEventKind.TURN_COMPLETED,
                    CodingEventKind.CANCELLED,
                    CodingEventKind.FAILED,
                }:
                    terminal_event = event
                else:
                    await self._append_event(record, event)
            if terminal_event is not None:
                if self.recovery_store is not None and self.mode == "draft":
                    saved = await self._persist_recovery(record, required=False)
                    if not saved:
                        record.state = "ready"
                        await self._append_generated_failure(
                            record,
                            self.recovery_reason or "recovery_storage_unavailable",
                        )
                        return
                await self._append_event(record, terminal_event)
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

    async def _persist_recovery(
        self,
        record: CodingApiSession,
        *,
        required: bool,
    ) -> bool:
        if self.recovery_store is None or self.mode != "draft":
            return False
        try:
            snapshot = await self.worker.recovery_snapshot(
                record.worker_session_id
            )
            active_changes = _public_changes(snapshot.get("changes"))
            cumulative_changes = _public_changes(
                snapshot.get("cumulative_changes", snapshot.get("changes"))
            )
            if not cumulative_changes["files"]:
                if record.recovery_id is not None:
                    discarded = await asyncio.to_thread(
                        self.recovery_store.discard,
                        recovery_id=record.recovery_id,
                    )
                    if not discarded:
                        raise CodingRecoveryError(
                            "Coding recovery record changed unexpectedly.",
                            code="recovery_changed",
                        )
                record.recovery_id = None
                record.recovery_created_at = None
                return True
            active_patch = _safe_diff(snapshot.get("patch")) if active_changes["files"] else ""
            cumulative_patch = _safe_diff(
                snapshot.get("cumulative_patch", snapshot.get("patch"))
            )
            base_patch = snapshot.get("base_patch", "")
            if not isinstance(base_patch, str):
                raise CodingRecoveryError(
                    "Coding recovery checkpoint is invalid.",
                    code="recovery_snapshot_invalid",
                )
            base_patch = _safe_diff(base_patch) if base_patch else ""
            fingerprint = snapshot.get("snapshot_fingerprint")
            if (
                not isinstance(fingerprint, str)
                or SNAPSHOT_FINGERPRINT_PATTERN.fullmatch(fingerprint) is None
                or cumulative_changes["patch_bytes"]
                != len(cumulative_patch.encode("utf-8"))
                or active_changes["patch_bytes"] != len(active_patch.encode("utf-8"))
            ):
                raise CodingRecoveryError(
                    "Coding recovery snapshot is inconsistent.",
                    code="recovery_snapshot_invalid",
                )
            raw_verification = snapshot.get("verification")
            verification = (
                _verification_from_worker({"verification": raw_verification})
                if raw_verification is not None
                else None
            )
            recovery_id = record.recovery_id or secrets.token_urlsafe(18)
            payload = RecoveryPayload(
                patch=cumulative_patch,
                changes=cumulative_changes,
                verification=verification,
                apply=_apply_storage_payload(record),
                commit=_commit_storage_payload(record),
                operation=_operation_storage_payload(record),
                publish=_publish_storage_payload(record),
                base_patch=base_patch,
                base_changes=(
                    {"paths": _diff_paths(base_patch)} if base_patch else None
                ),
                active_patch=active_patch,
                active_changes=(active_changes if active_changes["files"] else None),
                cycles=record.cycle_history.cycles,
            )
            recovery = self.recovery_store.create_record(
                recovery_id=recovery_id,
                state=_recovery_state(record),
                revision=cumulative_changes["revision"],
                snapshot_fingerprint=fingerprint,
                payload=payload,
                created_at=record.recovery_created_at,
            )
            await asyncio.to_thread(self.recovery_store.save, recovery)
            record.recovery_id = recovery.recovery_id
            record.recovery_created_at = recovery.created_at
            self.recovery_reason = None
            return True
        except CodingWorkerError as exc:
            error = CodingRecoveryError(
                "Coding worker could not provide a recovery snapshot.",
                code=_safe_code(exc.code),
            )
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            error = CodingRecoveryError(
                "Coding recovery snapshot was rejected.",
                code=_safe_code(detail.get("code") or "recovery_snapshot_invalid"),
            )
        except CodingRecoveryError as exc:
            error = exc
        except Exception:
            error = CodingRecoveryError(
                "Coding recovery storage is unavailable.",
                code="recovery_storage_unavailable",
            )
        self.recovery_reason = _safe_code(error.code)
        if required:
            raise _recovery_http_error(error) from error
        return False

    async def _load_recovery_record(
        self,
        *,
        required: bool = True,
    ) -> RecoveryRecord | None:
        if self.recovery_store is None:
            return None
        try:
            record = await asyncio.to_thread(self.recovery_store.load)
        except CodingRecoveryError as exc:
            self.recovery_reason = _safe_code(exc.code)
            if required:
                raise _recovery_http_error(exc) from exc
            return None
        self.recovery_reason = None
        return record

    async def _require_recovery_record(self) -> RecoveryRecord:
        if not self.recovery_enabled or self.recovery_store is None:
            raise _http_error(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                self.recovery_reason or "recovery_unavailable",
            )
        record = await self._load_recovery_record()
        if record is None:
            raise _http_error(status.HTTP_404_NOT_FOUND, "recovery_not_found")
        return record

    def _hydrate_recovered_session(
        self,
        record: CodingApiSession,
        recovery: RecoveryRecord,
    ) -> None:
        _restore_apply_payload(record, recovery.payload.apply)
        _restore_commit_payload(record, recovery.payload.commit)
        _restore_publish_payload(record, recovery.payload.publish)
        _validate_operation_storage_payload(record, recovery.payload.operation)
        if record.publish_manifest is not None:
            manifest = record.publish_manifest
            try:
                rebuilt = self._build_publish_manifest(
                    record,
                    revision=manifest.revision,
                    publish_id=manifest.publish_id,
                    title=manifest.title,
                    body=manifest.body,
                )
            except (TypeError, ValueError) as exc:
                raise _http_error(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "recovery_data_corrupt",
                ) from exc
            if rebuilt != manifest:
                raise _http_error(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "recovery_data_corrupt",
                )
        if (
            record.apply_revision not in {None, recovery.revision}
            or record.commit_revision not in {None, recovery.revision}
            or record.publish_revision not in {None, recovery.revision}
            or (
                record.commit_receipt is not None
                and record.apply_receipt is not None
                and record.commit_receipt.apply_id
                != record.apply_receipt.apply_id
            )
        ):
            raise _http_error(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "recovery_data_corrupt",
            )
        legacy_repository_failure = (
            recovery.state is RecoveryState.CONFLICT
            and record.publish_state is PublishState.CONFLICT
            and record.publish_reason == "repository_not_ready"
            and record.publish_receipt is None
        )
        corrected_base_configuration = (
            recovery.state is RecoveryState.CONFLICT
            and record.publish_state is PublishState.CONFLICT
            and record.publish_reason == "base_branch_changed"
            and record.publish_receipt is None
        )
        legacy_apply_reconcile_failure = (
            recovery.state is RecoveryState.CONFLICT
            and record.apply_state is ApplyState.APPLIED
            and record.apply_receipt is not None
            and record.apply_reason == "apply_recovery_conflict"
            and record.commit_reason == "apply_recovery_conflict"
            and record.publish_reason == "apply_recovery_conflict"
        )
        if (
            legacy_repository_failure
            or corrected_base_configuration
            or legacy_apply_reconcile_failure
        ):
            # v8 initially classified a local Publisher preflight failure as a
            # remote conflict, and an in-process Applier reconciliation as an
            # application conflict. A fixed base may also be corrected by the
            # deployer. Preserve these tasks and reconcile again without writing.
            if legacy_repository_failure or corrected_base_configuration:
                record.publish_state = PublishState.FAILED
            record.apply_reason = None
            record.commit_reason = None
            record.publish_reason = None
            record.recovery_conflict = None
            record.state = (
                "applied"
                if record.apply_state is ApplyState.APPLIED
                else "ready"
            )
        elif recovery.state is RecoveryState.CONFLICT:
            record.state = "conflict"
            record.recovery_conflict = "recovery_conflict"
        elif record.publish_state in {PublishState.DRAFT, PublishState.READY}:
            record.state = "published"
        elif record.apply_state is ApplyState.REVERTED:
            record.state = "reverted"
        elif record.apply_state is ApplyState.APPLIED:
            record.state = "applied"
        else:
            record.state = "ready"

    async def _reconcile_recovered_operations(
        self,
        record: CodingApiSession,
        patch: str,
        paths: list[str],
        fingerprint: str,
    ) -> None:
        commit_reconciled = False
        if (
            record.apply_state is ApplyState.APPLIED
            and record.commit_state in {CommitState.COMMITTED, CommitState.UNDONE}
        ):
            commit_reconciled = await self._reconcile_recovered_commit(record)
            if record.state == "conflict":
                return

        operation = record.apply_operation_id
        if (
            not commit_reconciled
            and operation is not None
            and record.apply_state is not ApplyState.NOT_APPLIED
        ):
            if self.applier is None:
                self._mark_recovery_conflict(record, "applier_unavailable")
                return
            intended_revert = (
                record.apply_receipt is not None
                and record.apply_state in {ApplyState.REVERTING, ApplyState.FAILED}
            )
            try:
                state, receipt = await self.applier.reconcile(
                    operation_id=operation,
                    revision=record.apply_revision or 0,
                    patch=patch,
                    paths=paths,
                    expected_fingerprint=fingerprint,
                )
            except ApplierClientError as exc:
                self._mark_recovery_conflict(record, _safe_code(exc.code))
                return
            if state == "conflict":
                self._mark_recovery_conflict(record, "apply_recovery_conflict")
                return
            if state == "applied" and receipt is not None:
                record.apply_receipt = receipt
                record.apply_state = ApplyState.APPLIED
                record.state = "applied"
            elif state == "not_applied" and intended_revert:
                record.apply_state = ApplyState.REVERTED
                record.state = "reverted"
            elif (
                state == "not_applied"
                and record.apply_state is ApplyState.REVERTED
            ):
                record.state = "reverted"
            elif state == "not_applied" and record.apply_state in {
                ApplyState.APPLYING,
                ApplyState.FAILED,
            }:
                record.apply_state = ApplyState.NOT_APPLIED
                record.apply_operation_id = None
                record.apply_receipt = None
                record.state = "ready"
            else:
                self._mark_recovery_conflict(record, "apply_recovery_conflict")
                return

        if not commit_reconciled:
            commit_reconciled = await self._reconcile_recovered_commit(record)
            if record.state == "conflict":
                return
        if not commit_reconciled:
            return

        manifest = record.publish_manifest
        if manifest is None:
            return
        if self.publisher is None:
            record.publish_state = PublishState.FAILED
            record.publish_reason = "publisher_unavailable"
            return
        try:
            publish_state, publish_receipt = await self.publisher.reconcile(manifest)
        except PublisherClientError as exc:
            reason = _safe_code(exc.code)
            if _publish_error_is_conflict(reason):
                self._mark_recovery_conflict(record, reason)
                record.publish_state = PublishState.CONFLICT
            else:
                record.publish_state = PublishState.FAILED
                record.publish_reason = reason
            return
        if publish_state == PublishState.CONFLICT.value:
            self._mark_recovery_conflict(record, "remote_branch_conflict")
            record.publish_state = PublishState.CONFLICT
            return
        if publish_state in {
            PublishState.NOT_PUBLISHED.value,
            "branch_pushed",
        }:
            if record.publish_receipt is not None:
                self._mark_recovery_conflict(record, "publish_recovery_conflict")
                record.publish_state = PublishState.CONFLICT
            else:
                record.publish_state = PublishState.FAILED
                record.publish_reason = (
                    "publish_not_completed"
                    if publish_state == PublishState.NOT_PUBLISHED.value
                    else "publish_incomplete"
                )
            return
        if publish_receipt is None:
            self._mark_recovery_conflict(record, "publish_recovery_conflict")
            record.publish_state = PublishState.CONFLICT
            return
        if (
            publish_receipt.state is PublishState.READY
            and record.publish_state
            not in {PublishState.MARKING_READY, PublishState.READY}
        ):
            self._mark_recovery_conflict(record, "remote_pr_conflict")
            record.publish_state = PublishState.CONFLICT
            return
        record.publish_receipt = publish_receipt
        record.publish_state = publish_receipt.state
        record.publish_reason = None
        record.publish_finished_at = time.time()
        record.state = "published"

    async def _reconcile_recovered_commit(
        self,
        record: CodingApiSession,
    ) -> bool:
        commit_operation = record.commit_operation_id
        apply_receipt = record.apply_receipt
        if (
            commit_operation is None
            or apply_receipt is None
            or record.commit_state is CommitState.NOT_COMMITTED
            or (
                record.apply_state is ApplyState.REVERTED
                and record.commit_state is CommitState.UNDONE
            )
        ):
            return False
        if self.committer is None or record.commit_message is None:
            self._mark_recovery_conflict(record, "committer_unavailable")
            return False
        try:
            state, receipt = await self.committer.reconcile(
                operation_id=commit_operation,
                apply_receipt=apply_receipt,
                message=record.commit_message,
            )
        except CommitterClientError as exc:
            self._mark_recovery_conflict(record, _safe_code(exc.code))
            return False
        if state == "committed" and receipt is not None:
            record.commit_receipt = receipt
            record.commit_state = CommitState.COMMITTED
            if record.publish_manifest is not None:
                expected = record.publish_manifest.commits[-1]
                if (
                    receipt.commit_id != expected.commit_id
                    or receipt.commit_sha != expected.commit_sha
                    or receipt.parent_sha != expected.parent_sha
                    or receipt.message != expected.message
                    or tuple(sorted(receipt.files)) != expected.files
                ):
                    self._mark_recovery_conflict(
                        record,
                        "commit_recovery_conflict",
                    )
                    return False
        elif state == "undone" and receipt is not None:
            record.commit_receipt = receipt
            record.commit_state = CommitState.UNDONE
        elif state == "not_committed" and record.commit_receipt is None:
            record.commit_state = CommitState.FAILED
            record.commit_reason = "commit_not_completed"
        else:
            self._mark_recovery_conflict(record, "commit_recovery_conflict")
            return False
        return True

    @staticmethod
    def _mark_recovery_conflict(record: CodingApiSession, reason: str) -> None:
        record.state = "conflict"
        record.recovery_conflict = _safe_code(reason)
        record.apply_reason = record.recovery_conflict
        record.commit_reason = record.recovery_conflict
        record.publish_reason = record.recovery_conflict

    async def _require_available(self) -> dict[str, Any]:
        if not self.enabled:
            raise _http_error(status.HTTP_503_SERVICE_UNAVAILABLE, "disabled")
        try:
            health = await self.worker.health()
        except CodingWorkerError as exc:
            raise _worker_http_error(exc) from exc
        except Exception as exc:
            raise _http_error(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "worker_unavailable",
            ) from exc
        if health.get("ok") is not True:
            raise _http_error(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "worker_unavailable",
            )
        if health.get("configured") is not True:
            raise _http_error(status.HTTP_503_SERVICE_UNAVAILABLE, "not_configured")
        worker_mode = health.get("mode")
        if worker_mode not in {"readonly", "draft"} or worker_mode != self.mode:
            raise _http_error(status.HTTP_503_SERVICE_UNAVAILABLE, "mode_mismatch")
        return health

    async def _apply_capability(
        self,
        worker_health: dict[str, Any],
    ) -> dict[str, Any]:
        capability: dict[str, Any] = {
            "configured": False,
            "available": False,
            "target": "dedicated_worktree",
            "requires_verification": False,
            "allows_quality_risk_confirmation": True,
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

    async def _publish_capability(self) -> dict[str, Any]:
        capability: dict[str, Any] = {
            "enabled": self.publish_enabled,
            "configured": False,
            "available": False,
            "provider": "github",
            "target": "fixed_repository",
            "default_pr_state": "draft",
            "supports_mark_ready": True,
            "requires_exact_base": True,
            "remote_merge": False,
        }
        if not self.publish_enabled:
            return {**capability, "reason": "publish_disabled"}
        if not self.recovery_enabled or self.recovery_store is None:
            return {**capability, "reason": "recovery_unavailable"}
        if self.publisher is None:
            return {**capability, "reason": "publisher_not_configured"}
        try:
            health = await self.publisher.health()
        except PublisherClientError as exc:
            return {**capability, "reason": _safe_code(exc.code)}
        except Exception:
            return {**capability, "reason": "publisher_unavailable"}
        configured = health.get("configured") is True
        response = {**capability, "configured": configured}
        if not configured:
            return {
                **response,
                "reason": _safe_code(
                    health.get("reason") or "publisher_not_configured"
                ),
            }
        if (
            health.get("available") is not True
            or health.get("provider") != "github"
            or health.get("target") != "fixed_repository"
        ):
            return {
                **response,
                "reason": _safe_code(health.get("reason") or "publisher_unavailable"),
            }
        return {**response, "available": True}

    async def _require_publish_available(self) -> None:
        capability = await self._publish_capability()
        if capability["available"] is True:
            return
        reason = str(capability.get("reason") or "publisher_unavailable")
        raise _http_error(status.HTTP_503_SERVICE_UNAVAILABLE, reason)

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
        CodingService._require_not_recovery_conflict(record)
        if record.state in {"applied", "published", "reverted"} or (
            record.publish_manifest is not None
        ):
            raise _http_error(status.HTTP_409_CONFLICT, "session_frozen")
        if record.apply_state in {ApplyState.APPLYING, ApplyState.REVERTING}:
            raise _http_error(status.HTTP_409_CONFLICT, "apply_in_progress")

    @staticmethod
    def _require_not_recovery_conflict(record: CodingApiSession) -> None:
        if record.state == "conflict":
            raise _http_error(status.HTTP_409_CONFLICT, "recovery_conflict")

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

    @staticmethod
    def _build_publish_manifest(
        record: CodingApiSession,
        *,
        revision: int,
        publish_id: str,
        title: str,
        body: str,
    ) -> PublishManifest:
        current = record.commit_receipt
        apply_receipt = record.apply_receipt
        if (
            current is None
            or apply_receipt is None
            or record.recovery_id is None
            or current.revision != revision
        ):
            raise ValueError("Publish task is incomplete")
        receipts: list[CommitReceipt] = []
        for cycle in record.cycle_history.cycles:
            if (
                cycle.state is not CycleState.COMMITTED
                or not isinstance(cycle.commit, dict)
                or cycle.commit.get("state") != CommitState.COMMITTED.value
            ):
                raise ValueError("Publish history is incomplete")
            receipts.append(_commit_receipt_from_storage(cycle.commit.get("receipt")))
        receipts.append(current)
        commits = tuple(
            PublishCommit(
                commit_id=receipt.commit_id,
                commit_sha=receipt.commit_sha,
                parent_sha=receipt.parent_sha,
                message=receipt.message,
                files=tuple(sorted(receipt.files)),
            )
            for receipt in receipts
        )
        return PublishManifest(
            publish_id=publish_id,
            task_id=record.recovery_id,
            revision=revision,
            snapshot_fingerprint=apply_receipt.snapshot_fingerprint,
            base_sha=commits[0].parent_sha,
            head_sha=commits[-1].commit_sha,
            commits=commits,
            title=title,
            body=body,
        )

    @staticmethod
    def _public_publish(
        record: CodingApiSession,
        revision: int,
    ) -> dict[str, Any]:
        manifest = record.publish_manifest
        receipt = record.publish_receipt
        if manifest is None or record.publish_revision != revision:
            return {
                "state": PublishState.NOT_PUBLISHED.value,
                "revision": revision,
                "publish_id": None,
                "title": "",
                "body": "",
                "pr_number": None,
                "pr_url": None,
                "file_count": 0,
                "commit_count": 0,
                "started_at": None,
                "finished_at": None,
                "reason": None,
                "can_mark_ready": False,
            }
        return {
            "state": record.publish_state.value,
            "revision": revision,
            "publish_id": manifest.publish_id,
            "title": manifest.title,
            "body": manifest.body,
            "pr_number": receipt.pr_number if receipt is not None else None,
            "pr_url": receipt.pr_url if receipt is not None else None,
            "file_count": len(manifest.files),
            "commit_count": len(manifest.commits),
            "started_at": record.publish_started_at,
            "finished_at": record.publish_finished_at,
            "reason": record.publish_reason,
            "can_mark_ready": bool(
                record.publish_state in {PublishState.DRAFT, PublishState.FAILED}
                and receipt is not None
                and receipt.state in {PublishState.DRAFT, PublishState.READY}
            ),
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
        publisher_socket_path = os.getenv(
            "CODING_PUBLISHER_SOCKET_PATH",
            "/run/modelmirror-coding-publish/publisher.sock",
        )
        recovery_enabled = os.getenv(
            "CODING_RECOVERY_ENABLED",
            "false",
        ).strip().lower() in {"1", "true", "yes", "on"}
        recovery_store: CodingRecoveryStore | None = None
        recovery_reason: str | None = None
        if recovery_enabled:
            storage_path = Path(
                os.getenv(
                    "CODING_RECOVERY_STORAGE_DIR",
                    "/var/lib/modelmirror/coding-recovery",
                )
            )
            try:
                retention = int(
                    os.getenv(
                        "CODING_RECOVERY_RETENTION_SECONDS",
                        str(DEFAULT_RECOVERY_RETENTION_SECONDS),
                    )
                )
                if (
                    not storage_path.is_absolute()
                    or not MIN_RECOVERY_RETENTION_SECONDS
                    <= retention
                    <= MAX_RECOVERY_RETENTION_SECONDS
                ):
                    raise ValueError("Recovery configuration is invalid")
                recovery_store = CodingRecoveryStore(
                    storage_path,
                    retention_seconds=retention,
                )
            except CodingRecoveryError as exc:
                recovery_reason = _safe_code(exc.code)
            except (OSError, TypeError, ValueError):
                recovery_reason = "recovery_not_configured"
        _service = CodingService(
            enabled=enabled,
            worker=CodingWorkerClient(Path(socket_path)),
            applier=CodingApplierClient(Path(applier_socket_path)),
            committer=CodingCommitterClient(Path(committer_socket_path)),
            publisher=CodingPublisherClient(Path(publisher_socket_path)),
            recovery_store=recovery_store,
            recovery_enabled=recovery_enabled,
            recovery_reason=recovery_reason,
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
async def coding_capabilities(response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return await get_coding_service().capabilities()


@router.get("/recovery")
async def coding_recovery_status(response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return await get_coding_service().recovery_status()


@router.post("/recovery/resume")
async def resume_coding_recovery(response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    record = await get_coding_service().resume_recovery()
    return {
        "id": record.session_id,
        "status": record.state,
        "conversation_restored": False,
        "conflict": record.recovery_conflict,
    }


@router.post("/recovery/discard")
async def discard_coding_recovery(response: Response) -> dict[str, bool]:
    response.headers["Cache-Control"] = "no-store"
    return await get_coding_service().discard_recovery()


@router.get("/recovery/patch")
async def download_coding_recovery_patch() -> Response:
    revision, patch = await get_coding_service().recovery_patch()
    return Response(
        content=patch,
        media_type="text/x-diff",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": (
                f'attachment; filename="modelmirror-recovered-r{revision}.patch"'
            ),
            "X-Content-Type-Options": "nosniff",
        },
    )


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
    scope: Literal["current", "cumulative"] = Query(default="current"),
) -> Response:
    patch = await get_coding_service().patch(
        session_id,
        revision,
        scope=scope,
    )
    return Response(
        content=patch,
        media_type="text/x-diff",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": (
                f'attachment; filename="modelmirror-'
                f'{"changes" if scope == "current" else "cumulative"}'
                f'-r{revision}.patch"'
            ),
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/sessions/{session_id}/history")
async def coding_session_history(
    session_id: str,
    response: Response,
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return await get_coding_service().history(session_id)


@router.post("/sessions/{session_id}/continue")
async def continue_coding_session(
    session_id: str,
    payload: ContinueCycleRequest,
    response: Response,
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return await get_coding_service().continue_cycle(
        session_id,
        payload.revision,
        payload.commit_id,
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
    payload: ApplyRequest,
    response: Response,
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return await get_coding_service().apply(
        session_id,
        payload.revision,
        confirm_quality_risks=payload.confirm_quality_risks,
    )


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


@router.post(
    "/sessions/{session_id}/publish",
    status_code=status.HTTP_202_ACCEPTED,
)
async def publish_coding_session(
    session_id: str,
    payload: PublishRequest,
    response: Response,
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return await get_coding_service().publish(
        session_id,
        payload.revision,
        payload.commit_id,
        payload.title,
        payload.body,
    )


@router.get("/sessions/{session_id}/publish")
async def coding_publish_status(
    session_id: str,
    response: Response,
    revision: int = Query(ge=1),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return await get_coding_service().publish_status(session_id, revision)


@router.post(
    "/sessions/{session_id}/publish/ready",
    status_code=status.HTTP_202_ACCEPTED,
)
async def mark_coding_publish_ready(
    session_id: str,
    payload: PublishReadyRequest,
    response: Response,
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return await get_coding_service().mark_publish_ready(
        session_id,
        payload.revision,
        payload.publish_id,
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


def _recovery_state(record: CodingApiSession) -> RecoveryState:
    if record.state == "conflict":
        return RecoveryState.CONFLICT
    if record.publish_state in {PublishState.DRAFT, PublishState.READY}:
        return RecoveryState.PUBLISHED
    if record.commit_state is CommitState.COMMITTED:
        return RecoveryState.COMMITTED
    if record.commit_state is CommitState.UNDONE:
        return RecoveryState.UNDONE
    if record.apply_state is ApplyState.APPLIED:
        return RecoveryState.APPLIED
    if record.apply_state is ApplyState.REVERTED:
        return RecoveryState.REVERTED
    return RecoveryState.DRAFT


def _apply_storage_payload(record: CodingApiSession) -> dict[str, Any]:
    return {
        "state": record.apply_state.value,
        "revision": record.apply_revision,
        "operation_id": record.apply_operation_id,
        "receipt": (
            _apply_receipt_to_storage(record.apply_receipt)
            if record.apply_receipt is not None
            else None
        ),
        "reason": record.apply_reason,
        "started_at": record.apply_started_at,
        "finished_at": record.apply_finished_at,
    }


def _commit_storage_payload(record: CodingApiSession) -> dict[str, Any]:
    return {
        "state": record.commit_state.value,
        "revision": record.commit_revision,
        "operation_id": record.commit_operation_id,
        "message": record.commit_message,
        "receipt": (
            _commit_receipt_to_storage(record.commit_receipt)
            if record.commit_receipt is not None
            else None
        ),
        "reason": record.commit_reason,
        "started_at": record.commit_started_at,
        "finished_at": record.commit_finished_at,
    }


def _publish_storage_payload(record: CodingApiSession) -> dict[str, Any] | None:
    if record.publish_manifest is None:
        return None
    return {
        "state": record.publish_state.value,
        "revision": record.publish_revision,
        "manifest": record.publish_manifest.to_dict(),
        "receipt": (
            record.publish_receipt.to_dict()
            if record.publish_receipt is not None
            else None
        ),
        "reason": record.publish_reason,
        "started_at": record.publish_started_at,
        "finished_at": record.publish_finished_at,
    }


def _operation_storage_payload(record: CodingApiSession) -> dict[str, Any] | None:
    if record.commit_operation_id is not None and record.commit_state in {
        CommitState.COMMITTING,
        CommitState.UNDOING,
        CommitState.FAILED,
    }:
        return {
            "kind": (
                "commit_undo"
                if record.commit_receipt is not None
                else "commit"
            ),
            "state": record.commit_state.value,
            "operation_id": record.commit_operation_id,
        }
    if record.apply_operation_id is not None and record.apply_state in {
        ApplyState.APPLYING,
        ApplyState.REVERTING,
        ApplyState.FAILED,
    }:
        return {
            "kind": (
                "apply_revert"
                if record.apply_receipt is not None
                else "apply"
            ),
            "state": record.apply_state.value,
            "operation_id": record.apply_operation_id,
        }
    return None


def _validate_operation_storage_payload(
    record: CodingApiSession,
    value: dict[str, Any] | None,
) -> None:
    expected = _operation_storage_payload(record)
    if value is None and expected is None:
        return
    if (
        not isinstance(value, dict)
        or set(value) != {"kind", "state", "operation_id"}
        or value != expected
    ):
        raise _http_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "recovery_data_corrupt",
        )


def _restore_apply_payload(
    record: CodingApiSession,
    value: dict[str, Any] | None,
) -> None:
    if value is None:
        return
    if not isinstance(value, dict) or set(value) != {
        "state",
        "revision",
        "operation_id",
        "receipt",
        "reason",
        "started_at",
        "finished_at",
    }:
        raise _http_error(status.HTTP_503_SERVICE_UNAVAILABLE, "recovery_data_corrupt")
    try:
        apply_state = ApplyState(value["state"])
        revision = value["revision"]
        operation_id = value["operation_id"]
        reason = value["reason"]
        receipt = (
            _apply_receipt_from_storage(value["receipt"])
            if value["receipt"] is not None
            else None
        )
        if (
            (revision is not None and (
                isinstance(revision, bool)
                or not isinstance(revision, int)
                or revision < 0
            ))
            or (operation_id is not None and (
                not isinstance(operation_id, str)
                or SAFE_IDENTIFIER.fullmatch(operation_id) is None
            ))
            or (reason is not None and (
                not isinstance(reason, str) or len(reason) > 64
            ))
            or not _valid_optional_timestamp(value["started_at"])
            or not _valid_optional_timestamp(value["finished_at"])
            or (apply_state is ApplyState.NOT_APPLIED and any(
                item is not None for item in (revision, operation_id, receipt)
            ))
            or (apply_state is not ApplyState.NOT_APPLIED and (
                revision is None or operation_id is None
            ))
            or (apply_state in {
                ApplyState.APPLIED,
                ApplyState.REVERTING,
                ApplyState.REVERTED,
            } and receipt is None)
            or (receipt is not None and (
                receipt.revision != revision
                or receipt.apply_id != operation_id
            ))
        ):
            raise ValueError("Apply recovery is inconsistent")
    except (TypeError, ValueError) as exc:
        raise _http_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "recovery_data_corrupt",
        ) from exc
    record.apply_state = apply_state
    record.apply_revision = revision
    record.apply_operation_id = operation_id
    record.apply_receipt = receipt
    record.apply_reason = _safe_code(reason) if reason is not None else None
    record.apply_started_at = value["started_at"]
    record.apply_finished_at = value["finished_at"]


def _restore_commit_payload(
    record: CodingApiSession,
    value: dict[str, Any] | None,
) -> None:
    if value is None:
        return
    if not isinstance(value, dict) or set(value) != {
        "state",
        "revision",
        "operation_id",
        "message",
        "receipt",
        "reason",
        "started_at",
        "finished_at",
    }:
        raise _http_error(status.HTTP_503_SERVICE_UNAVAILABLE, "recovery_data_corrupt")
    try:
        commit_state = CommitState(value["state"])
        revision = value["revision"]
        operation_id = value["operation_id"]
        message = value["message"]
        reason = value["reason"]
        receipt = (
            _commit_receipt_from_storage(value["receipt"])
            if value["receipt"] is not None
            else None
        )
        if (
            (revision is not None and (
                isinstance(revision, bool)
                or not isinstance(revision, int)
                or revision < 0
            ))
            or (operation_id is not None and (
                not isinstance(operation_id, str)
                or SAFE_IDENTIFIER.fullmatch(operation_id) is None
            ))
            or (message is not None and normalize_commit_message(message) != message)
            or (reason is not None and (
                not isinstance(reason, str) or len(reason) > 64
            ))
            or not _valid_optional_timestamp(value["started_at"])
            or not _valid_optional_timestamp(value["finished_at"])
            or (commit_state is CommitState.NOT_COMMITTED and any(
                item is not None
                for item in (revision, operation_id, message, receipt)
            ))
            or (commit_state is not CommitState.NOT_COMMITTED and (
                revision is None or operation_id is None or message is None
            ))
            or (commit_state in {
                CommitState.COMMITTED,
                CommitState.UNDOING,
                CommitState.UNDONE,
            } and receipt is None)
            or (receipt is not None and (
                receipt.revision != revision
                or receipt.commit_id != operation_id
                or receipt.message != message
            ))
        ):
            raise ValueError("Commit recovery is inconsistent")
    except (TypeError, ValueError) as exc:
        raise _http_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "recovery_data_corrupt",
        ) from exc
    record.commit_state = commit_state
    record.commit_revision = revision
    record.commit_operation_id = operation_id
    record.commit_message = message
    record.commit_receipt = receipt
    record.commit_reason = _safe_code(reason) if reason is not None else None
    record.commit_started_at = value["started_at"]
    record.commit_finished_at = value["finished_at"]


def _restore_publish_payload(
    record: CodingApiSession,
    value: dict[str, Any] | None,
) -> None:
    if value is None:
        return
    if not isinstance(value, dict) or set(value) != {
        "state",
        "revision",
        "manifest",
        "receipt",
        "reason",
        "started_at",
        "finished_at",
    }:
        raise _http_error(status.HTTP_503_SERVICE_UNAVAILABLE, "recovery_data_corrupt")
    try:
        publish_state = PublishState(value["state"])
        revision = value["revision"]
        manifest = PublishManifest.from_dict(value["manifest"])
        receipt = (
            PublishReceipt.from_dict(value["receipt"])
            if value["receipt"] is not None
            else None
        )
        reason = value["reason"]
        if (
            publish_state is PublishState.NOT_PUBLISHED
            or isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision < 1
            or manifest.revision != revision
            or (reason is not None and (
                not isinstance(reason, str)
                or len(reason) > 64
                or _safe_code(reason) != reason
            ))
            or not _valid_optional_timestamp(value["started_at"])
            or not _valid_optional_timestamp(value["finished_at"])
            or (receipt is not None and (
                receipt.publish_id != manifest.publish_id
                or receipt.revision != revision
                or receipt.branch != manifest.branch
                or receipt.head_sha != manifest.head_sha
            ))
            or (publish_state in {PublishState.DRAFT, PublishState.READY} and (
                receipt is None or receipt.state is not publish_state
            ))
            or (publish_state is PublishState.MARKING_READY and (
                receipt is None or receipt.state is not PublishState.DRAFT
            ))
        ):
            raise ValueError("Publish recovery is inconsistent")
    except (TypeError, ValueError) as exc:
        raise _http_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "recovery_data_corrupt",
        ) from exc
    record.publish_state = publish_state
    record.publish_revision = revision
    record.publish_manifest = manifest
    record.publish_receipt = receipt
    record.publish_reason = reason
    record.publish_started_at = value["started_at"]
    record.publish_finished_at = value["finished_at"]


def _apply_receipt_to_storage(receipt: ApplyReceipt) -> dict[str, Any]:
    return {
        "apply_id": receipt.apply_id,
        "revision": receipt.revision,
        "snapshot_fingerprint": receipt.snapshot_fingerprint,
        "applied_at": receipt.applied_at,
        "files": [
            {
                "path": item.path,
                "existed_before": item.existed_before,
                "before_sha256": item.before_sha256,
                "after_sha256": item.after_sha256,
            }
            for item in receipt.files
        ],
    }


def _apply_receipt_from_storage(value: Any) -> ApplyReceipt:
    if not isinstance(value, dict) or set(value) != {
        "apply_id",
        "revision",
        "snapshot_fingerprint",
        "applied_at",
        "files",
    } or not isinstance(value["files"], list):
        raise ValueError("Apply receipt is invalid")
    files = tuple(
        ApplyFileReceipt(**item)
        for item in value["files"]
        if isinstance(item, dict)
        and set(item)
        == {"path", "existed_before", "before_sha256", "after_sha256"}
    )
    if len(files) != len(value["files"]):
        raise ValueError("Apply receipt files are invalid")
    return ApplyReceipt(**{**value, "files": files})


def _commit_receipt_to_storage(receipt: CommitReceipt) -> dict[str, Any]:
    return {
        "commit_id": receipt.commit_id,
        "revision": receipt.revision,
        "apply_id": receipt.apply_id,
        "commit_sha": receipt.commit_sha,
        "parent_sha": receipt.parent_sha,
        "tree_sha": receipt.tree_sha,
        "message": receipt.message,
        "files": list(receipt.files),
        "branch": receipt.branch,
        "committed_at": receipt.committed_at,
    }


def _commit_receipt_from_storage(value: Any) -> CommitReceipt:
    if not isinstance(value, dict) or set(value) != {
        "commit_id",
        "revision",
        "apply_id",
        "commit_sha",
        "parent_sha",
        "tree_sha",
        "message",
        "files",
        "branch",
        "committed_at",
    } or not isinstance(value["files"], list):
        raise ValueError("Commit receipt is invalid")
    return CommitReceipt(**{**value, "files": tuple(value["files"])})


def _valid_optional_timestamp(value: Any) -> bool:
    return value is None or (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and value >= 0
    )


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


def _diff_paths(diff: str) -> list[str]:
    if not diff:
        return []
    paths: list[str] = []
    for line in diff.splitlines():
        if not line.startswith("diff --git "):
            continue
        match = SAFE_DIFF_HEADER.fullmatch(line)
        if match is None or match.group(1) != match.group(2):
            raise _http_error(status.HTTP_503_SERVICE_UNAVAILABLE, "unsafe_diff")
        try:
            paths.append(DraftWorkspace.normalize_relative_path(match.group(1)))
        except DraftPolicyError as exc:
            raise _http_error(status.HTTP_503_SERVICE_UNAVAILABLE, "unsafe_diff") from exc
    if not paths or paths != sorted(set(paths)):
        raise _http_error(status.HTTP_503_SERVICE_UNAVAILABLE, "unsafe_diff")
    return paths


def _safe_code(value: Any) -> str:
    code = re.sub(r"[^a-z0-9_-]", "_", str(value or "unknown").lower())
    return code[:64] or "unknown"


def _publish_error_is_conflict(code: str) -> bool:
    return code in {
        "base_branch_changed",
        "commit_mismatch",
        "remote_branch_conflict",
        "remote_pr_conflict",
        "repository_has_remote",
        "repository_mismatch",
        "repository_not_independent",
        "unsafe_repository",
        "wrong_branch",
    }


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


def _recovery_http_error(exc: CodingRecoveryError) -> HTTPException:
    if exc.code in {
        "recovery_changed",
        "recovery_conflict",
        "snapshot_mismatch",
    }:
        return _http_error(status.HTTP_409_CONFLICT, exc.code)
    if exc.code in {"invalid_recovery_payload", "recovery_snapshot_invalid"}:
        return _http_error(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.code)
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
