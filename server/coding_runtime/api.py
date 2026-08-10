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
from .project_source_client import (
    CodingProjectSourceClient,
    ProjectSourceClientError,
)
from .project_host import PROJECT_ID_PATTERN, ProjectHostError
from .project_host_api import (
    ProjectHostRuntime,
    create_project_host_runtime,
    project_host_router,
)
from .project_writer_client import (
    CodingProjectWriterClient,
    ProjectWriterClientError,
)
from .projects import (
    MAX_PROJECTS,
    ProjectFeatures,
    ProjectKind,
    ProjectState,
    ProjectSummary,
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
    RecoveryProjectContext,
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

    async def create_session(
        self,
        source: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    async def restore_session(
        self,
        *,
        revision: int,
        patch: str,
        paths: list[str],
        snapshot_fingerprint: str,
        verification: dict[str, Any] | None = None,
        source: dict[str, Any] | None = None,
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

    async def verification_confirm(
        self,
        session_id: str,
        revision: int,
        confirmation_id: str,
    ) -> dict[str, Any]: ...

    async def command_pending(self, session_id: str) -> dict[str, Any] | None: ...

    async def command_decision(
        self,
        session_id: str,
        request_id: str,
        decision: str,
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


class ProjectWriterClient(Protocol):
    async def health(self) -> dict[str, Any]: ...

    async def apply(self, **kwargs: Any) -> ApplyReceipt: ...

    async def revert(self, **kwargs: Any) -> ApplyReceipt: ...

    async def commit(self, **kwargs: Any) -> CommitReceipt: ...

    async def undo(self, **kwargs: Any) -> CommitReceipt: ...

    async def reconcile_apply(
        self,
        **kwargs: Any,
    ) -> tuple[str, ApplyReceipt | None]: ...

    async def reconcile_commit(
        self,
        **kwargs: Any,
    ) -> tuple[str, ApplyReceipt, CommitReceipt | None]: ...


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


class ProjectSourceClient(Protocol):
    async def health(self) -> dict[str, Any]: ...

    async def list_projects(self) -> list[dict[str, Any]]: ...

    async def check(self, project_id: str, expected_head: str) -> dict[str, Any]: ...

    async def acquire(
        self,
        project_id: str,
        *,
        expected_head: str | None = None,
    ) -> dict[str, Any]: ...

    async def import_uploaded(
        self,
        *,
        upload_id: str,
        archive_sha256: str,
        project_id: str,
        name: str,
        branch: str,
        head: str,
    ) -> dict[str, Any]: ...

    async def release(self, project_id: str, lease_id: str) -> bool: ...


class CodingSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(
        default="modelmirror",
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    )


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
    status: Literal["added", "modified", "deleted"]
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


class VerificationConfirmRequest(VerificationRevisionRequest):
    confirmation_id: str = Field(
        min_length=20,
        max_length=80,
        pattern=r"^[A-Za-z0-9_-]+$",
    )


class CommandDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["allow_once", "reject"]


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


class VerificationCommandPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: str = Field(alias="id", min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=240)
    kind: Literal["test", "build", "lint", "typecheck", "custom"]
    argv: list[str] = Field(min_length=1, max_length=64)
    cwd: str = Field(min_length=1, max_length=500)
    timeout_seconds: int = Field(ge=1, le=300)


class VerificationStepPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(alias="id", min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=240)
    command: VerificationCommandPayload | None = None
    state: Literal["not_started", "running", "completed", "cancelled"]
    result: Literal["not_run", "passed", "failed", "not_applicable"]
    duration_ms: int | None = Field(default=None, ge=0, le=600_000)
    summary: str = Field(default="", max_length=500)
    details: str = Field(default="", max_length=16_000)
    truncated: bool = False


class VerificationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    revision: int = Field(ge=0)
    state: Literal[
        "not_started",
        "awaiting_confirmation",
        "running",
        "completed",
        "cancelled",
    ]
    result: Literal["not_run", "passed", "failed", "not_applicable"]
    stale: bool
    reason: str | None = Field(default=None, max_length=64)
    started_at: float | None = Field(default=None, ge=0)
    finished_at: float | None = Field(default=None, ge=0)
    confirmation_id: str | None = Field(default=None, min_length=20, max_length=80)
    plan_fingerprint: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern=r"^[a-f0-9]{64}$",
    )
    steps: list[VerificationStepPayload] = Field(max_length=16)

    @model_validator(mode="after")
    def state_and_result_must_be_consistent(self) -> VerificationPayload:
        terminal = self.state in {"completed", "cancelled"}
        if self.state == "running" and self.started_at is None:
            raise ValueError("Running verification must have a start time")
        if terminal and self.finished_at is None:
            raise ValueError("Terminal verification must have a finish time")
        if self.state in {"not_started", "awaiting_confirmation"} and self.result != "not_run":
            raise ValueError("Unstarted verification result is inconsistent")
        if self.state == "awaiting_confirmation" and self.confirmation_id is None:
            raise ValueError("Verification confirmation is missing")
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
    project: dict[str, Any] = field(
        default_factory=lambda: ProjectSummary.builtin().to_public_dict()
    )
    project_source: dict[str, Any] | None = None
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
        project_source: ProjectSourceClient | None = None,
        project_host: ProjectHostRuntime | None = None,
        project_writer: ProjectWriterClient | None = None,
        projects_enabled: bool | None = None,
        projects_reason: str | None = None,
        project_host_enabled: bool = False,
        project_host_reason: str | None = None,
        recovery_store: CodingRecoveryStore | None = None,
        recovery_enabled: bool = False,
        recovery_reason: str | None = None,
        incremental_enabled: bool | None = None,
        publish_enabled: bool | None = None,
        commands_enabled: bool | None = None,
        project_writeback_enabled: bool | None = None,
        ttl_seconds: float = SESSION_TTL_SECONDS,
        mode: str | None = None,
    ) -> None:
        self.enabled = enabled
        self.worker = worker
        self.applier = applier
        self.committer = committer
        self.publisher = publisher
        self.project_source = project_source
        self.project_host = project_host
        self.project_writer = project_writer
        self.projects_enabled = (
            os.getenv("CODING_PROJECTS_ENABLED", "false").strip().lower()
            in {"1", "true", "yes", "on"}
            if projects_enabled is None
            else projects_enabled
        )
        self.projects_reason = projects_reason
        self.project_host_enabled = project_host_enabled
        self.project_host_reason = project_host_reason
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
        self.commands_enabled = (
            os.getenv("CODING_PROJECT_COMMANDS_ENABLED", "false").strip().lower()
            in {"1", "true", "yes", "on"}
            if commands_enabled is None
            else commands_enabled
        )
        self.project_writeback_enabled = (
            os.getenv("CODING_PROJECT_WRITEBACK_ENABLED", "false").strip().lower()
            in {"1", "true", "yes", "on"}
            if project_writeback_enabled is None
            else project_writeback_enabled
        )
        self.ttl_seconds = ttl_seconds
        self.mode = _normalize_mode(
            mode if mode is not None else os.getenv("CODING_AGENT_MODE", "readonly")
        )
        self._sessions: dict[str, CodingApiSession] = {}
        self._lock = asyncio.Lock()
        self._create_lock = asyncio.Lock()

    async def capabilities(self) -> dict[str, Any]:
        recovery, projects, project_writeback = await asyncio.gather(
            self.recovery_status(check_worker=False),
            self._projects_capability(),
            self._project_writeback_capability(),
        )
        response = {
            "enabled": self.enabled,
            "available": False,
            "mode": self.mode,
            "workspace": "ModelMirror",
            "projects": projects,
            "project_host": (
                self.project_host.capability(
                    enabled=self.project_host_enabled,
                    reason=self.project_host_reason,
                )
                if self.project_host is not None
                else {
                    "enabled": self.project_host_enabled,
                    "paired": False,
                    "available": False,
                    "platform": "windows",
                    "selection": True,
                    "remembers_projects": True,
                    "direct_writeback": False,
                    "reason": self.project_host_reason
                    or (
                        "project_host_not_configured"
                        if self.project_host_enabled
                        else "project_host_disabled"
                    ),
                }
            ),
            "project_writeback": project_writeback,
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
            "commands": {
                "enabled": self.commands_enabled,
                "available": False,
                "confirmation": "always",
                "execution": "isolated_copy",
                "network": False,
                "persists_output": False,
                "max_commands_per_turn": 20,
                "max_duration_seconds": 300,
                "reason": (
                    "commands_unavailable"
                    if self.commands_enabled
                    else "commands_disabled"
                ),
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
        worker_commands = health.get("commands")
        if self.mode == "draft" and isinstance(worker_commands, dict):
            if (
                self.commands_enabled
                and worker_commands.get("enabled") is True
                and worker_commands.get("available") is True
                and worker_commands.get("confirmation") == "always"
                and worker_commands.get("execution") == "isolated_copy"
                and worker_commands.get("network") is False
                and worker_commands.get("persists_output") is False
            ):
                response["commands"] = {
                    **response["commands"],
                    "available": True,
                }
                response["commands"].pop("reason", None)
            else:
                response["commands"]["reason"] = _safe_code(
                    worker_commands.get("reason") or "commands_unavailable"
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

    async def project_catalog(self) -> dict[str, Any]:
        builtin = ProjectSummary.builtin().to_public_dict()
        builtin["features"]["commands"] = False
        capability = await self._projects_capability()
        writeback = await self._project_writeback_capability()
        projects = [builtin]
        commands_available = False
        if self.commands_enabled:
            with contextlib.suppress(Exception):
                health = await self.worker.health()
                command_health = health.get("commands")
                commands_available = bool(
                    isinstance(command_health, dict)
                    and command_health.get("available") is True
                )
        if capability["available"] is True and self.project_source is not None:
            try:
                local_projects = await self.project_source.list_projects()
                for project in local_projects:
                    if not isinstance(project, dict):
                        continue
                    features = project.get("features")
                    if isinstance(features, dict):
                        features["commands"] = commands_available
                        features["verification"] = commands_available
                        if features.get("apply") is True and writeback["available"] is not True:
                            features["apply"] = False
                            features["commit"] = False
                            project["writeback_reason"] = writeback.get(
                                "reason",
                                "project_writer_unavailable",
                            )
                    projects.append(project)
                self.projects_reason = None
            except ProjectSourceClientError as exc:
                capability = {
                    **capability,
                    "available": False,
                    "reason": _safe_code(exc.code),
                }
                self.projects_reason = _safe_code(exc.code)
            except Exception:
                capability = {
                    **capability,
                    "available": False,
                    "reason": "project_source_unavailable",
                }
                self.projects_reason = "project_source_unavailable"
        if self.project_host is not None:
            known_ids = {
                item.get("id") for item in projects if isinstance(item, dict)
            }
            for project in self.project_host.list_projects():
                if project.get("id") in known_ids:
                    continue
                features = project.get("features")
                if isinstance(features, dict):
                    features["commands"] = commands_available
                    features["verification"] = commands_available
                projects.append(project)
        return {**capability, "projects": projects}

    async def _project_writeback_capability(self) -> dict[str, Any]:
        capability: dict[str, Any] = {
            "enabled": self.project_writeback_enabled,
            "configured": False,
            "available": False,
            "target": "selected_local_repository",
            "supports_delete": True,
            "supports_move": True,
            "supports_revert": True,
            "supports_commit": True,
            "remote_operations": False,
        }
        if not self.project_writeback_enabled:
            return {**capability, "reason": "project_writeback_disabled"}
        if self.project_writer is None:
            return {**capability, "reason": "project_writer_not_configured"}
        try:
            health = await self.project_writer.health()
        except ProjectWriterClientError as exc:
            return {**capability, "reason": _safe_code(exc.code)}
        except Exception:
            return {**capability, "reason": "project_writer_unavailable"}
        configured = health.get("configured") is True
        response = {**capability, "configured": configured}
        if not configured or health.get("available") is not True:
            return {
                **response,
                "reason": _safe_code(
                    health.get("reason") or "project_writer_unavailable"
                ),
            }
        if health.get("target") != "selected_local_repository":
            return {**response, "reason": "project_writer_mismatch"}
        return {**response, "available": True}

    async def _projects_capability(self) -> dict[str, Any]:
        capability: dict[str, Any] = {
            "enabled": self.projects_enabled,
            "configured": False,
            "available": False,
            "selection": True,
            "default_project_id": "modelmirror",
            "max_projects": MAX_PROJECTS,
        }
        if not self.projects_enabled:
            return {**capability, "reason": "projects_disabled"}
        if self.project_source is None:
            return {
                **capability,
                "reason": self.projects_reason or "project_source_not_configured",
            }
        try:
            health = await self.project_source.health()
        except ProjectSourceClientError as exc:
            self.projects_reason = _safe_code(exc.code)
            return {**capability, "reason": self.projects_reason}
        except Exception:
            self.projects_reason = "project_source_unavailable"
            return {**capability, "reason": self.projects_reason}
        configured = health.get("configured") is True
        response = {**capability, "configured": configured}
        if not configured or health.get("available") is not True:
            reason = _safe_code(
                health.get("reason") or "project_source_not_configured"
            )
            self.projects_reason = reason
            return {**response, "reason": reason}
        self.projects_reason = None
        return {**response, "available": True}

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
        try:
            project = await self._load_recovery_project_context(record)
            recovery_preview = (
                self._recovery_operation_preview(record, project)
                if project.kind is ProjectKind.HOST_GIT
                else None
            )
        except HTTPException:
            return {
                **base,
                **record.to_public(
                    can_resume=False,
                    reason=self.recovery_reason or "recovery_data_corrupt",
                ),
                "available": False,
                "project": None,
            }
        can_resume = self.enabled and self.mode == "draft"
        reason: str | None = None
        if not self.enabled:
            can_resume = False
            reason = "disabled"
        elif self.mode != "draft":
            can_resume = False
            reason = "draft_unavailable"
        elif project.kind is ProjectKind.LOCAL_CLONE and (
            not self.projects_enabled or self.project_source is None
        ):
            can_resume = False
            reason = "project_source_unavailable"
        elif project.kind is ProjectKind.HOST_GIT and (
            not self.projects_enabled
            or self.project_source is None
            or self.project_host is None
            or project.head is None
            or project.branch is None
        ):
            can_resume = False
            reason = self.project_host_reason or "project_host_unavailable"
        elif check_worker:
            try:
                health = await self.worker.health()
                fingerprint = health.get("snapshot_fingerprint")
                if (
                    health.get("ok") is not True
                    or health.get("configured") is not True
                    or (
                        project.kind is ProjectKind.BUILTIN
                        and fingerprint != record.snapshot_fingerprint
                    )
                ):
                    can_resume = False
                    reason = (
                        "snapshot_mismatch"
                        if isinstance(fingerprint, str)
                        else "worker_unavailable"
                    )
                elif project.kind is ProjectKind.LOCAL_CLONE:
                    assert self.project_source is not None and project.head is not None
                    await self.project_source.check(project.project_id, project.head)
                elif project.kind is ProjectKind.HOST_GIT:
                    assert (
                        self.project_host is not None
                        and project.head is not None
                        and project.branch is not None
                        and recovery_preview is not None
                    )
                    if (
                        recovery_preview.apply_operation_id is None
                        and not recovery_preview.cycle_history.cycles
                    ):
                        self.project_host.check_project(
                            project.project_id,
                            project.head,
                            project.branch,
                        )
                    else:
                        host_health = await self.project_host.health()
                        if host_health.get("available") is not True:
                            raise ProjectHostError(
                                _safe_code(
                                    host_health.get("reason")
                                    or "project_host_writeback_unavailable"
                                )
                            )
            except ProjectSourceClientError as exc:
                can_resume = False
                reason = _safe_code(exc.code)
            except ProjectHostError as exc:
                can_resume = False
                reason = _safe_code(exc.code)
            except Exception:
                can_resume = False
                reason = "worker_unavailable"
        return {
            **base,
            **record.to_public(can_resume=can_resume, reason=reason),
            "project": project.to_public(),
        }

    async def recovery_patch(self) -> tuple[int, str]:
        record = await self._require_recovery_record()
        return record.revision, _safe_diff(record.payload.patch)

    async def discard_recovery(self) -> dict[str, bool]:
        await self._prune_missing_worker_sessions()
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
            await self._close_worker_and_release(conflict_record, required=False)
            async with self._lock:
                self._sessions.pop(conflict_record.session_id, None)
        return {"discarded": True}

    async def create_session(
        self,
        project_id: str = "modelmirror",
    ) -> CodingApiSession:
        async with self._create_lock:
            await self._require_available()
            await self.cleanup_expired()
            if await self._load_recovery_record() is not None:
                raise _http_error(status.HTTP_409_CONFLICT, "recovery_pending")
            async with self._lock:
                if any(
                    record.state in ACTIVE_STATES
                    for record in self._sessions.values()
                ):
                    raise _http_error(
                        status.HTTP_409_CONFLICT,
                        "concurrency_limit",
                    )
            source: dict[str, Any] | None = None
            project = ProjectSummary.builtin().to_public_dict()
            if project_id != "modelmirror":
                if not self.projects_enabled or self.project_source is None:
                    raise _http_error(
                        status.HTTP_503_SERVICE_UNAVAILABLE,
                        "project_source_unavailable",
                    )
                if PROJECT_ID_PATTERN.fullmatch(project_id) is not None:
                    source, project = await self._acquire_host_project(project_id)
                else:
                    try:
                        source = await self.project_source.acquire(project_id)
                        project = await self.project_source.check(
                            project_id,
                            str(source["head"]),
                        )
                    except ProjectSourceClientError as exc:
                        await self._release_project_source(source)
                        raise _project_source_http_error(exc) from exc
                    except Exception:
                        await self._release_project_source(source)
                        raise
            try:
                result = (
                    await self.worker.create_session(source)
                    if source is not None
                    else await self.worker.create_session()
                )
            except CodingWorkerError as exc:
                await self._release_project_source(source)
                raise _worker_http_error(exc) from exc
            except Exception:
                await self._release_project_source(source)
                raise
            session_id = result.get("session_id")
            worker_mode = result.get("mode")
            event_data = result.get("event")
            if (
                not isinstance(session_id, str)
                or not SAFE_IDENTIFIER.fullmatch(session_id)
                or worker_mode != self.mode
                or not isinstance(event_data, dict)
                or (
                    source is not None
                    and not _worker_project_matches(result.get("project"), project)
                )
            ):
                if isinstance(session_id, str) and session_id:
                    await self._close_worker_session_and_release(
                        session_id,
                        source,
                        required=False,
                    )
                else:
                    await self._release_project_source(source)
                raise _http_error(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "invalid_worker_response",
                )
            record = CodingApiSession(
                session_id=session_id,
                worker_session_id=session_id,
                project=project,
                project_source=source,
            )
            initial = _event_from_payload(event_data)
            if (
                initial.kind is not CodingEventKind.SESSION_STARTED
                or initial.seq != 1
                or initial.session_id != session_id
            ):
                await self._close_worker_session_and_release(
                    session_id,
                    source,
                    required=False,
                )
                raise _http_error(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "invalid_worker_response",
                )
            await self._append_event(record, initial)
            async with self._lock:
                if any(
                    existing.state in ACTIVE_STATES
                    for existing in self._sessions.values()
                ):
                    await self._close_worker_session_and_release(
                        session_id,
                        source,
                        required=False,
                    )
                    raise _http_error(
                        status.HTTP_409_CONFLICT,
                        "concurrency_limit",
                    )
                self._sessions[session_id] = record
            return record

    async def adopt_worker_patch(
        self,
        *,
        project_id: str,
        expected_head: str,
        patch: str,
        paths: list[str],
    ) -> CodingApiSession:
        """Import a completed V14 patch into the existing v13 writeback chain."""

        async with self._create_lock:
            return await self._adopt_worker_patch_locked(
                project_id=project_id,
                expected_head=expected_head,
                patch=patch,
                paths=paths,
            )

    async def _adopt_worker_patch_locked(
        self,
        *,
        project_id: str,
        expected_head: str,
        patch: str,
        paths: list[str],
    ) -> CodingApiSession:
        if "GIT binary patch" in patch or "Binary files " in patch:
            raise _http_error(
                status.HTTP_409_CONFLICT, "worker_writeback_patch_unsupported"
            )

        await self._require_available()
        if self.mode != "draft":
            raise _http_error(status.HTTP_409_CONFLICT, "draft_unavailable")
        if not self.recovery_enabled or self.recovery_store is None:
            raise _http_error(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                self.recovery_reason or "recovery_unavailable",
            )
        safe_patch = _safe_diff(patch)
        safe_paths = _diff_paths(safe_patch)
        if not safe_patch or safe_paths != paths:
            raise _http_error(
                status.HTTP_409_CONFLICT, "worker_writeback_patch_unsupported"
            )
        await self.cleanup_expired()
        if await self._load_recovery_record() is not None:
            raise _http_error(status.HTTP_409_CONFLICT, "recovery_pending")
        async with self._lock:
            if self._sessions:
                raise _http_error(status.HTTP_409_CONFLICT, "concurrency_limit")

        source: dict[str, Any] | None = None
        try:
            source, project = await self._acquire_host_project(
                project_id,
                expected_head=expected_head,
            )
            fingerprint = source.get("fingerprint")
            if (
                not isinstance(fingerprint, str)
                or SNAPSHOT_FINGERPRINT_PATTERN.fullmatch(fingerprint) is None
            ):
                raise _http_error(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "invalid_project_source_response",
                )
            result = await self.worker.restore_session(
                revision=1,
                patch=safe_patch,
                paths=safe_paths,
                snapshot_fingerprint=fingerprint,
                verification=None,
                source=source,
            )
        except CodingWorkerError as exc:
            await self._release_project_source(source)
            raise _worker_http_error(exc) from exc
        except Exception:
            await self._release_project_source(source)
            raise

        session_id = result.get("session_id")
        event_data = result.get("event")
        restored_changes = _public_changes(result.get("changes"))
        if (
            not isinstance(session_id, str)
            or SAFE_IDENTIFIER.fullmatch(session_id) is None
            or result.get("mode") != self.mode
            or not isinstance(event_data, dict)
            or not _worker_project_matches(result.get("project"), project)
            or restored_changes["revision"] != 1
            or [item["path"] for item in restored_changes["files"]] != safe_paths
            or restored_changes["can_download"] is not True
        ):
            if isinstance(session_id, str) and session_id:
                await self._close_worker_session_and_release(
                    session_id, source, required=False
                )
            else:
                await self._release_project_source(source)
            raise _http_error(
                status.HTTP_503_SERVICE_UNAVAILABLE, "invalid_worker_response"
            )
        record = CodingApiSession(
            session_id=session_id,
            worker_session_id=session_id,
            project=project,
            project_source=source,
        )
        initial = _event_from_payload(event_data)
        if (
            initial.kind is not CodingEventKind.SESSION_STARTED
            or initial.seq != 1
            or initial.session_id != session_id
        ):
            await self._close_worker_session_and_release(
                session_id, source, required=False
            )
            raise _http_error(
                status.HTTP_503_SERVICE_UNAVAILABLE, "invalid_worker_response"
            )
        await self._append_event(record, initial)
        try:
            persisted = await self._persist_recovery(record, required=True)
            if not persisted:
                raise _http_error(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "recovery_storage_unavailable",
                )
            async with self._lock:
                if self._sessions:
                    raise _http_error(
                        status.HTTP_409_CONFLICT, "concurrency_limit"
                    )
                self._sessions[session_id] = record
        except Exception:
            await self._close_worker_session_and_release(
                session_id, source, required=False
            )
            raise
        return record

    async def _acquire_host_project(
        self,
        project_id: str,
        *,
        expected_head: str | None = None,
        expected_branch: str | None = None,
        managed_operation_id: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if self.project_host is None or self.project_source is None:
            raise _http_error(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                self.project_host_reason or "project_host_unavailable",
            )
        transfer_id: str | None = None
        try:
            transfer = await self.project_host.request_snapshot(
                project_id,
                expected_head=expected_head,
                expected_branch=expected_branch,
                managed_operation_id=managed_operation_id,
            )
            transfer_id = str(transfer["upload_id"])
            project = transfer["project"]
            source = await self.project_source.import_uploaded(
                upload_id=transfer_id,
                archive_sha256=str(transfer["archive_sha256"]),
                project_id=project_id,
                name=str(project["name"]),
                branch=str(project["branch"]),
                head=str(project["head"]),
            )
        except ProjectHostError as exc:
            raise _project_host_http_error(exc) from exc
        except ProjectSourceClientError as exc:
            raise _project_source_http_error(exc) from exc
        except (KeyError, TypeError, ValueError) as exc:
            raise _http_error(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "invalid_project_host_response",
            ) from exc
        finally:
            if transfer_id is not None:
                self.project_host.finish_transfer(transfer_id)
        public_project = self.project_host.public_project(project_id)
        if managed_operation_id is not None:
            managed_project = _project_from_source(source)
            capability = self.project_host.capability()
            writeback_available = capability.get("direct_writeback") is True
            features = managed_project.get("features")
            if isinstance(features, dict):
                managed_project["features"] = {
                    **features,
                    "apply": writeback_available,
                    "commit": writeback_available,
                }
            managed_project["state"] = ProjectState.AVAILABLE.value
            managed_project["reason"] = None
            managed_project["writeback_reason"] = (
                None
                if writeback_available
                else _safe_code(
                    capability.get("writeback_reason")
                    or "project_host_writeback_unavailable"
                )
            )
            public_project = managed_project
        if (
            source.get("kind") != ProjectKind.HOST_GIT.value
            or public_project.get("id") != project_id
            or public_project.get("branch") != source.get("branch")
            or (
                managed_operation_id is None
                and public_project.get("head")
                != str(source.get("head") or "")[:12]
            )
            or (expected_head is not None and source.get("head") != expected_head)
            or (
                expected_branch is not None
                and source.get("branch") != expected_branch
            )
        ):
            await self._release_project_source(source)
            raise _http_error(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "invalid_project_source_response",
            )
        return source, public_project

    def _recovery_operation_preview(
        self,
        recovery: RecoveryRecord,
        project: RecoveryProjectContext,
    ) -> CodingApiSession:
        source = (
            {
                "kind": project.kind.value,
                "project_id": project.project_id,
                "name": project.name,
                "head": project.head,
                "branch": project.branch,
                "fingerprint": recovery.snapshot_fingerprint,
            }
            if project.kind is not ProjectKind.BUILTIN
            else None
        )
        preview = CodingApiSession(
            session_id="recovery-preview",
            worker_session_id="recovery-preview",
            project=project.to_public(),
            project_source=source,
            cycle_number=len(recovery.payload.cycles) + 1,
            cycle_history=CodingCycleHistory(recovery.payload.cycles),
        )
        self._hydrate_recovered_session(preview, recovery)
        return preview

    def _bind_host_recovery_operations(
        self,
        recovery: RecoveryRecord,
        project: RecoveryProjectContext,
        preview: CodingApiSession,
    ) -> str | None:
        if (
            self.project_host is None
            or project.head is None
            or project.branch is None
        ):
            raise _http_error(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "project_host_unavailable",
            )
        try:
            current_parent, lineage = self._host_cycle_lineage(
                preview,
                base_head=project.head,
                branch=project.branch,
                fingerprint=recovery.snapshot_fingerprint,
            )
            anchor_operation_id: str | None = None
            seen_operations: set[str] = set()
            for parent, apply_receipt, commit_receipt in lineage:
                if anchor_operation_id is None:
                    anchor_operation_id = apply_receipt.apply_id
                seen_operations.update(
                    {apply_receipt.apply_id, commit_receipt.commit_id}
                )
                self.project_host.bind_recovery_operations(
                    project_id=project.project_id,
                    expected_head=parent,
                    expected_branch=project.branch,
                    apply_operation_id=apply_receipt.apply_id,
                    commit_operation_id=commit_receipt.commit_id,
                    apply_receipt=apply_receipt,
                    commit_receipt=commit_receipt,
                )

            active_apply_id = preview.apply_operation_id
            active_commit_id = preview.commit_operation_id
            active_apply = preview.apply_receipt
            active_commit = preview.commit_receipt
            if active_apply_id is not None:
                if (
                    active_apply_id in seen_operations
                    or active_commit_id in seen_operations
                    or (
                        active_apply is not None
                        and active_apply.snapshot_fingerprint
                        != recovery.snapshot_fingerprint
                    )
                    or (
                        active_commit is not None
                        and (
                            active_apply is None
                            or active_commit.apply_id != active_apply.apply_id
                            or active_commit.parent_sha != current_parent
                            or active_commit.branch != project.branch
                        )
                    )
                ):
                    raise ValueError("Active host cycle lineage is invalid")
                self.project_host.bind_recovery_operations(
                    project_id=project.project_id,
                    expected_head=current_parent,
                    expected_branch=project.branch,
                    apply_operation_id=active_apply_id,
                    commit_operation_id=active_commit_id,
                    apply_receipt=active_apply,
                    commit_receipt=active_commit,
                )
                anchor_operation_id = anchor_operation_id or active_apply_id
            elif active_commit_id is not None:
                raise ValueError("Host commit has no active application")
            if lineage and anchor_operation_id is None:
                raise ValueError("Host recovery has no snapshot anchor")
            return anchor_operation_id
        except ProjectHostError as exc:
            raise _project_host_http_error(exc) from exc
        except (TypeError, ValueError) as exc:
            raise _http_error(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "recovery_data_corrupt",
            ) from exc

    async def resume_recovery(self) -> CodingApiSession:
        health = await self._require_available()
        if self.mode != "draft":
            raise _http_error(status.HTTP_409_CONFLICT, "draft_unavailable")
        await self.cleanup_expired()
        await self._prune_missing_worker_sessions()
        recovery = await self._require_recovery_record()
        project_context = await self._load_recovery_project_context(recovery)
        async with self._lock:
            if self._sessions:
                raise _http_error(status.HTTP_409_CONFLICT, "concurrency_limit")
        source: dict[str, Any] | None = None
        if project_context.kind is ProjectKind.BUILTIN and (
            health.get("snapshot_fingerprint") != recovery.snapshot_fingerprint
        ):
            raise _http_error(status.HTTP_409_CONFLICT, "snapshot_mismatch")
        if project_context.kind is ProjectKind.LOCAL_CLONE:
            if (
                not self.projects_enabled
                or self.project_source is None
                or project_context.head is None
            ):
                raise _http_error(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "project_source_unavailable",
                )
            try:
                source = await self.project_source.acquire(
                    project_context.project_id,
                    expected_head=project_context.head,
                )
            except ProjectSourceClientError as exc:
                raise _project_source_http_error(exc) from exc
        elif project_context.kind is ProjectKind.HOST_GIT:
            if project_context.head is None or project_context.branch is None:
                raise _http_error(
                    status.HTTP_409_CONFLICT,
                    "recovery_data_corrupt",
                )
            if self.project_host is None:
                raise _http_error(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    self.project_host_reason or "project_host_unavailable",
                )
            recovery_preview = self._recovery_operation_preview(
                recovery,
                project_context,
            )
            has_managed_operations = bool(
                recovery_preview.apply_operation_id is not None
                or recovery_preview.cycle_history.cycles
            )
            managed_operation_id: str | None = None
            if has_managed_operations:
                try:
                    host_health = await self.project_host.health()
                    if host_health.get("available") is not True:
                        raise ProjectHostError(
                            _safe_code(
                                host_health.get("reason")
                                or "project_host_writeback_unavailable"
                            )
                        )
                except ProjectHostError as exc:
                    raise _project_host_http_error(exc) from exc
                managed_operation_id = self._bind_host_recovery_operations(
                    recovery,
                    project_context,
                    recovery_preview,
                )
            source, resumed_host_project = await self._acquire_host_project(
                project_context.project_id,
                expected_head=project_context.head,
                expected_branch=project_context.branch,
                managed_operation_id=managed_operation_id,
            )
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
            restore_arguments: dict[str, Any] = {
                "revision": recovery.revision,
                "patch": active_patch,
                "paths": paths,
                "base_patch": recovery.payload.base_patch,
                "base_paths": base_paths,
                "snapshot_fingerprint": recovery.snapshot_fingerprint,
                "verification": verification,
            }
            if source is not None:
                restore_arguments["source"] = source
            result = await self.worker.restore_session(**restore_arguments)
        except CodingWorkerError as exc:
            await self._release_project_source(source)
            raise _worker_http_error(exc) from exc
        except Exception:
            await self._release_project_source(source)
            raise
        session_id = result.get("session_id")
        event_data = result.get("event")
        restored_changes = result.get("changes")
        if (
            not isinstance(session_id, str)
            or SAFE_IDENTIFIER.fullmatch(session_id) is None
            or result.get("mode") != self.mode
            or not isinstance(event_data, dict)
            or (
                source is not None
                and not _worker_project_matches(
                    result.get("project"),
                    project_context.to_public(),
                )
            )
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
                await self._close_worker_session_and_release(
                    session_id,
                    source,
                    required=False,
                )
            else:
                await self._release_project_source(source)
            raise _http_error(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "invalid_worker_response",
            )
        resumed_project = project_context.to_public()
        if project_context.kind is ProjectKind.LOCAL_CLONE:
            if self.project_source is not None:
                with contextlib.suppress(Exception):
                    projects = await self.project_source.list_projects()
                    match = next(
                        (
                            item
                            for item in projects
                            if isinstance(item, dict)
                            and item.get("id") == project_context.project_id
                        ),
                        None,
                    )
                    if match is not None:
                        resumed_project = match
            if recovery.payload.apply is not None:
                features = resumed_project.get("features")
                if isinstance(features, dict):
                    features["apply"] = True
                    features["commit"] = True
                resumed_project["state"] = ProjectState.AVAILABLE.value
                resumed_project["reason"] = None
                resumed_project["writeback_reason"] = None
        elif project_context.kind is ProjectKind.HOST_GIT and source is not None:
            resumed_project = resumed_host_project
        record = CodingApiSession(
            session_id=session_id,
            worker_session_id=session_id,
            project=resumed_project,
            project_source=source,
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
            await self._close_worker_session_and_release(
                session_id,
                source,
                required=False,
            )
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
            await self._close_worker_session_and_release(
                session_id,
                source,
                required=False,
            )
            raise
        async with self._lock:
            if self._sessions:
                await self._close_worker_session_and_release(
                    session_id,
                    source,
                    required=False,
                )
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

    async def session_status(self, session_id: str) -> dict[str, Any]:
        await self.cleanup_expired()
        record = self._get_session(session_id)
        response: dict[str, Any] = {"state": record.state}
        if not self._is_builtin_project(record):
            response["project"] = record.project
        return response

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
                and (
                    self._is_builtin_project(record)
                    or (
                        self._is_host_project(record)
                        and isinstance(record.project.get("features"), dict)
                        and record.project["features"].get("commit") is True
                    )
                )
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
        if not (
            self._is_builtin_project(record)
            or self._is_host_project(record)
        ):
            raise _http_error(
                status.HTTP_409_CONFLICT,
                "project_operation_unavailable",
            )
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
            if self._is_host_project(record):
                _project_id, expected_parent, _fingerprint, branch = (
                    self._host_writer_context(record)
                )
                if (
                    receipt.parent_sha != expected_parent
                    or receipt.branch != branch
                ):
                    raise _http_error(
                        status.HTTP_409_CONFLICT,
                        "cycle_lineage_invalid",
                    )
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
        self._require_project_verification_enabled(record)
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
        self._require_project_verification_enabled(record)
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
        self._require_project_verification_enabled(record)
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

    async def verification_confirm(
        self,
        session_id: str,
        revision: int,
        confirmation_id: str,
    ) -> dict[str, Any]:
        record = await self._review_record(session_id)
        if self._is_builtin_project(record):
            raise _http_error(
                status.HTTP_409_CONFLICT,
                "project_operation_unavailable",
            )
        if record.apply_lock.locked():
            raise _http_error(status.HTTP_409_CONFLICT, "apply_in_progress")
        async with record.apply_lock:
            self._require_mutable(record)
            try:
                payload = await self.worker.verification_confirm(
                    record.worker_session_id,
                    revision,
                    confirmation_id,
                )
            except CodingWorkerError as exc:
                raise _worker_http_error(exc) from exc
            record.updated_at = time.time()
        result = _verification_from_worker(payload)
        result["accepted"] = payload.get("accepted") is True
        return result

    async def command_pending(
        self,
        session_id: str,
    ) -> dict[str, Any]:
        await self.cleanup_expired()
        record = self._get_session(session_id)
        if self._is_builtin_project(record):
            raise _http_error(
                status.HTTP_409_CONFLICT,
                "project_operation_unavailable",
            )
        try:
            pending = await self.worker.command_pending(record.worker_session_id)
        except CodingWorkerError as exc:
            raise _worker_http_error(exc) from exc
        return {"pending": _public_command_request(pending)}

    async def command_decision(
        self,
        session_id: str,
        request_id: str,
        decision: str,
    ) -> dict[str, Any]:
        await self.cleanup_expired()
        record = self._get_session(session_id)
        if self._is_builtin_project(record):
            raise _http_error(
                status.HTTP_409_CONFLICT,
                "project_operation_unavailable",
            )
        if SAFE_IDENTIFIER.fullmatch(request_id) is None:
            raise _http_error(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "invalid_request",
            )
        try:
            resolved = await self.worker.command_decision(
                record.worker_session_id,
                request_id,
                decision,
            )
        except CodingWorkerError as exc:
            raise _worker_http_error(exc) from exc
        return {"request": _public_command_request(resolved)}

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
            writer_context: tuple[str, str, str] | None = None
            host_context: tuple[str, str, str, str] | None = None
            host_retry_unknown = bool(
                self._is_host_project(record)
                and record.apply_state is ApplyState.FAILED
                and record.apply_operation_id is not None
                and record.apply_reason == "operation_result_unknown"
            )
            if self._is_builtin_project(record):
                expected_fingerprint = await self._require_apply_available()
            elif self._is_host_project(record):
                host_context = await self._require_host_writer_available(
                    record,
                    require_clean=not host_retry_unknown,
                )
                expected_fingerprint = host_context[2]
            else:
                writer_context = await self._require_project_writer_available(record)
                expected_fingerprint = writer_context[2]
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
                if host_context is not None:
                    assert self.project_host is not None
                    try:
                        if host_retry_unknown:
                            self.project_host.bind_recovery_operations(
                                project_id=host_context[0],
                                expected_head=host_context[1],
                                expected_branch=host_context[3],
                                apply_operation_id=operation_id,
                                apply_receipt=record.apply_receipt,
                                commit_receipt=None,
                            )
                        else:
                            self.project_host.bind_persisted_intent(
                                project_id=host_context[0],
                                expected_head=host_context[1],
                                expected_branch=host_context[3],
                                operation_id=operation_id,
                                kind="apply",
                            )
                    except ProjectHostError as exc:
                        raise ProjectWriterClientError(
                            "Host project intent was rejected.",
                            code=_safe_code(exc.code),
                        ) from exc
                    reconciled_state = "not_applied"
                    receipt = None
                    if host_retry_unknown:
                        reconciled_state, receipt = (
                            await self.project_host.reconcile_apply(
                                project_id=host_context[0],
                                expected_head=host_context[1],
                                expected_branch=host_context[3],
                                operation_id=operation_id,
                                revision=revision,
                                patch=patch,
                                paths=paths,
                                expected_fingerprint=expected_fingerprint,
                            )
                        )
                        if reconciled_state == "conflict":
                            raise ProjectWriterClientError(
                                "Host application reconciliation conflicted.",
                                code="operation_conflict",
                            )
                    if reconciled_state == "not_applied":
                        receipt = await self.project_host.apply(
                            project_id=host_context[0],
                            expected_head=host_context[1],
                            expected_branch=host_context[3],
                            operation_id=operation_id,
                            revision=revision,
                            patch=patch,
                            paths=paths,
                            expected_fingerprint=expected_fingerprint,
                        )
                    if receipt is None:
                        raise ProjectWriterClientError(
                            "Host application reconciliation was incomplete.",
                            code="operation_result_unknown",
                        )
                elif writer_context is None:
                    assert self.applier is not None
                    receipt = await self.applier.apply(
                        operation_id=operation_id,
                        revision=revision,
                        patch=patch,
                        paths=paths,
                        expected_fingerprint=expected_fingerprint,
                    )
                else:
                    assert self.project_writer is not None
                    receipt = await self.project_writer.apply(
                        project_id=writer_context[0],
                        expected_head=writer_context[1],
                        operation_id=operation_id,
                        revision=revision,
                        patch=patch,
                        paths=paths,
                        expected_fingerprint=expected_fingerprint,
                    )
                if (
                    receipt.apply_id != operation_id
                    or receipt.revision != revision
                    or receipt.snapshot_fingerprint != expected_fingerprint
                    or [item.path for item in receipt.files] != paths
                ):
                    if host_context is not None:
                        raise ProjectWriterClientError(
                            "Host application result must be reconciled.",
                            code="operation_result_unknown",
                        )
                    try:
                        if writer_context is None:
                            assert self.applier is not None
                            await self.applier.revert(receipt)
                        else:
                            assert self.project_writer is not None
                            await self.project_writer.revert(
                                project_id=writer_context[0],
                                expected_head=writer_context[1],
                                receipt=receipt,
                            )
                    except (ApplierClientError, ProjectWriterClientError) as revert_exc:
                        raise ApplierClientError(
                            "Coding application response could not be recovered.",
                            code="rollback_failed",
                        ) from revert_exc
                    raise ApplierClientError(
                        "Coding application receipt does not match the request.",
                        code="invalid_response",
                    )
            except (ApplierClientError, ProjectWriterClientError) as exc:
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
            host_retry_unknown = bool(
                self._is_host_project(record)
                and retrying_unknown_result
                and record.commit_reason == "operation_result_unknown"
            )
            host_context: tuple[str, str, str, str] | None = None
            host_patch: str | None = None
            if self._is_host_project(record):
                host_context = await self._require_host_writer_available(
                    record,
                    require_clean=False,
                )
                if host_context[2] != apply_receipt.snapshot_fingerprint:
                    raise _http_error(status.HTTP_409_CONFLICT, "snapshot_mismatch")
                if host_retry_unknown:
                    try:
                        host_patch = _safe_diff(
                            await self.worker.patch(
                                record.worker_session_id,
                                revision,
                            )
                        )
                    except CodingWorkerError as exc:
                        raise _worker_http_error(exc) from exc
            elif not retrying_unknown_result:
                await self._require_commit_available(
                    record,
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
                if self._is_builtin_project(record):
                    assert self.committer is not None
                    receipt = await self.committer.commit(
                        operation_id=operation_id,
                        apply_receipt=apply_receipt,
                        message=message,
                    )
                elif self._is_host_project(record):
                    assert host_context is not None
                    project_id, expected_head, _, expected_branch = host_context
                    assert self.project_host is not None
                    try:
                        if host_retry_unknown:
                            self.project_host.bind_recovery_operations(
                                project_id=project_id,
                                expected_head=expected_head,
                                expected_branch=expected_branch,
                                apply_operation_id=apply_receipt.apply_id,
                                commit_operation_id=operation_id,
                                apply_receipt=apply_receipt,
                                commit_receipt=record.commit_receipt,
                            )
                        else:
                            self.project_host.bind_persisted_intent(
                                project_id=project_id,
                                expected_head=expected_head,
                                expected_branch=expected_branch,
                                operation_id=operation_id,
                                kind="commit",
                                parent_operation_id=apply_receipt.apply_id,
                            )
                    except ProjectHostError as exc:
                        raise ProjectWriterClientError(
                            "Host commit intent was rejected.",
                            code=_safe_code(exc.code),
                        ) from exc
                    reconciled_state = "not_committed"
                    receipt = None
                    if host_retry_unknown:
                        assert host_patch is not None
                        reconciled_state, restored_apply, receipt = (
                            await self.project_host.reconcile_commit(
                                project_id=project_id,
                                expected_head=expected_head,
                                expected_branch=expected_branch,
                                operation_id=apply_receipt.apply_id,
                                revision=revision,
                                patch=host_patch,
                                paths=[item.path for item in apply_receipt.files],
                                expected_fingerprint=(
                                    apply_receipt.snapshot_fingerprint
                                ),
                                apply_receipt=apply_receipt,
                                commit_operation_id=operation_id,
                                message=message,
                            )
                        )
                        if restored_apply != apply_receipt:
                            raise ProjectWriterClientError(
                                "Host commit reconciliation was not bound.",
                                code="operation_result_unknown",
                            )
                        if reconciled_state == "undone" and receipt is not None:
                            record.commit_receipt = receipt
                            record.commit_state = CommitState.UNDONE
                            record.commit_reason = None
                            record.commit_finished_at = time.time()
                            record.updated_at = record.commit_finished_at
                            await self._persist_recovery(record, required=True)
                            return self._public_commit(record, revision)
                    if reconciled_state == "not_committed":
                        receipt = await self.project_host.commit(
                            project_id=project_id,
                            expected_head=expected_head,
                            expected_branch=expected_branch,
                            operation_id=operation_id,
                            apply_receipt=apply_receipt,
                            message=message,
                        )
                    elif reconciled_state != "committed":
                        raise ProjectWriterClientError(
                            "Host commit reconciliation conflicted.",
                            code="operation_conflict",
                        )
                    if receipt is None:
                        raise ProjectWriterClientError(
                            "Host commit reconciliation was incomplete.",
                            code="operation_result_unknown",
                        )
                else:
                    project_id, expected_head, _ = self._project_writer_context(record)
                    assert self.project_writer is not None
                    receipt = await self.project_writer.commit(
                        project_id=project_id,
                        expected_head=expected_head,
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
                    or (
                        self._is_host_project(record)
                        and (
                            receipt.branch != self._host_writer_context(record)[3]
                            or receipt.parent_sha != self._host_writer_context(record)[1]
                        )
                    )
                ):
                    if self._is_host_project(record):
                        raise ProjectWriterClientError(
                            "Host commit result must be reconciled.",
                            code="operation_result_unknown",
                        )
                    try:
                        if self._is_builtin_project(record):
                            assert self.committer is not None
                            await self.committer.undo(receipt, apply_receipt)
                        elif not self._is_host_project(record):
                            project_id, expected_head, _ = self._project_writer_context(record)
                            assert self.project_writer is not None
                            await self.project_writer.undo(
                                project_id=project_id,
                                expected_head=expected_head,
                                apply_receipt=apply_receipt,
                                commit_receipt=receipt,
                            )
                    except (CommitterClientError, ProjectWriterClientError) as undo_exc:
                        raise CommitterClientError(
                            "Invalid commit response could not be recovered.",
                            code="rollback_failed",
                        ) from undo_exc
                    raise CommitterClientError(
                        "Commit receipt does not match the request.",
                        code="invalid_response",
                    )
            except (CommitterClientError, ProjectWriterClientError) as exc:
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
        record = self._get_session(session_id)
        return self._public_commit(record, revision)

    async def publish(
        self,
        session_id: str,
        revision: int,
        commit_id: str,
        title: str,
        body: str,
    ) -> dict[str, Any]:
        record = await self._review_record(session_id)
        self._require_builtin_project(record)
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
        record = self._get_session(session_id)
        self._require_builtin_project(record)
        return self._public_publish(record, revision)

    async def mark_publish_ready(
        self,
        session_id: str,
        revision: int,
        publish_id: str,
    ) -> dict[str, Any]:
        await self.cleanup_expired()
        record = self._get_session(session_id)
        self._require_builtin_project(record)
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
            host_context: tuple[str, str, str, str] | None = None
            host_retry_unknown = bool(
                self._is_host_project(record)
                and record.commit_reason == "operation_result_unknown"
            )
            host_patch: str | None = None
            if self._is_builtin_project(record):
                if self.committer is None:
                    raise _http_error(status.HTTP_503_SERVICE_UNAVAILABLE, "committer_unavailable")
            elif self._is_host_project(record):
                host_context = await self._require_host_writer_available(
                    record,
                    require_clean=False,
                )
                if host_retry_unknown:
                    try:
                        host_patch = _safe_diff(
                            await self.worker.patch(
                                record.worker_session_id,
                                revision,
                            )
                        )
                    except CodingWorkerError as exc:
                        raise _worker_http_error(exc) from exc
            elif self.project_writer is None:
                raise _http_error(status.HTTP_503_SERVICE_UNAVAILABLE, "project_writer_unavailable")
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
                if self._is_builtin_project(record):
                    assert self.committer is not None
                    undone = await self.committer.undo(receipt, apply_receipt)
                elif host_context is not None:
                    assert self.project_host is not None
                    if host_retry_unknown:
                        try:
                            self.project_host.bind_recovery_operations(
                                project_id=host_context[0],
                                expected_head=host_context[1],
                                expected_branch=host_context[3],
                                apply_operation_id=apply_receipt.apply_id,
                                commit_operation_id=receipt.commit_id,
                                apply_receipt=apply_receipt,
                                commit_receipt=receipt,
                            )
                        except ProjectHostError as exc:
                            raise ProjectWriterClientError(
                                "Host undo intent was rejected.",
                                code=_safe_code(exc.code),
                            ) from exc
                        assert host_patch is not None
                        state, restored_apply, restored_commit = (
                            await self.project_host.reconcile_commit(
                                project_id=host_context[0],
                                expected_head=host_context[1],
                                expected_branch=host_context[3],
                                operation_id=apply_receipt.apply_id,
                                revision=revision,
                                patch=host_patch,
                                paths=[item.path for item in apply_receipt.files],
                                expected_fingerprint=(
                                    apply_receipt.snapshot_fingerprint
                                ),
                                apply_receipt=apply_receipt,
                                commit_operation_id=receipt.commit_id,
                                message=receipt.message,
                            )
                        )
                        if restored_apply != apply_receipt:
                            raise ProjectWriterClientError(
                                "Host undo reconciliation was not bound.",
                                code="operation_result_unknown",
                            )
                        if state == "undone" and restored_commit == receipt:
                            undone = receipt
                        elif state == "committed" and restored_commit == receipt:
                            undone = await self.project_host.undo(
                                project_id=host_context[0],
                                expected_head=host_context[1],
                                expected_branch=host_context[3],
                                apply_receipt=apply_receipt,
                                commit_receipt=receipt,
                            )
                        else:
                            raise ProjectWriterClientError(
                                "Host undo reconciliation conflicted.",
                                code="operation_conflict",
                            )
                    else:
                        undone = await self.project_host.undo(
                            project_id=host_context[0],
                            expected_head=host_context[1],
                            expected_branch=host_context[3],
                            apply_receipt=apply_receipt,
                            commit_receipt=receipt,
                        )
                else:
                    project_id, expected_head, _ = self._project_writer_context(record)
                    assert self.project_writer is not None
                    undone = await self.project_writer.undo(
                        project_id=project_id,
                        expected_head=expected_head,
                        apply_receipt=apply_receipt,
                        commit_receipt=receipt,
                    )
                if undone != receipt:
                    raise CommitterClientError(
                        "Commit undo receipt does not match.",
                        code="invalid_response",
                    )
            except (CommitterClientError, ProjectWriterClientError) as exc:
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
                if record.apply_state is ApplyState.FAILED and not (
                    self._is_host_project(record)
                    and record.apply_reason == "operation_result_unknown"
                ):
                    return self._public_apply(record, revision)
                if record.apply_state is not ApplyState.FAILED:
                    raise _http_error(status.HTTP_409_CONFLICT, "apply_not_revertible")
            host_context: tuple[str, str, str, str] | None = None
            host_retry_unknown = bool(
                self._is_host_project(record)
                and record.apply_state is ApplyState.FAILED
                and record.apply_reason == "operation_result_unknown"
            )
            host_patch: str | None = None
            if self._is_builtin_project(record):
                if self.applier is None:
                    raise _http_error(
                        status.HTTP_503_SERVICE_UNAVAILABLE,
                        "applier_unavailable",
                    )
            elif self._is_host_project(record):
                host_context = await self._require_host_writer_available(
                    record,
                    require_clean=False,
                )
                if host_retry_unknown:
                    try:
                        host_patch = _safe_diff(
                            await self.worker.patch(
                                record.worker_session_id,
                                revision,
                            )
                        )
                    except CodingWorkerError as exc:
                        raise _worker_http_error(exc) from exc
            elif self.project_writer is None:
                raise _http_error(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "project_writer_unavailable",
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
                if self._is_builtin_project(record):
                    assert self.applier is not None
                    reverted = await self.applier.revert(receipt)
                elif host_context is not None:
                    assert self.project_host is not None
                    if host_retry_unknown:
                        try:
                            self.project_host.bind_recovery_operations(
                                project_id=host_context[0],
                                expected_head=host_context[1],
                                expected_branch=host_context[3],
                                apply_operation_id=receipt.apply_id,
                                apply_receipt=receipt,
                                commit_receipt=None,
                            )
                        except ProjectHostError as exc:
                            raise ProjectWriterClientError(
                                "Host revert intent was rejected.",
                                code=_safe_code(exc.code),
                            ) from exc
                        assert host_patch is not None
                        state, restored = await self.project_host.reconcile_apply(
                            project_id=host_context[0],
                            expected_head=host_context[1],
                            expected_branch=host_context[3],
                            operation_id=receipt.apply_id,
                            revision=revision,
                            patch=host_patch,
                            paths=[item.path for item in receipt.files],
                            expected_fingerprint=receipt.snapshot_fingerprint,
                        )
                        if state == "not_applied":
                            reverted = receipt
                        elif state == "applied" and restored == receipt:
                            reverted = await self.project_host.revert(
                                project_id=host_context[0],
                                expected_head=host_context[1],
                                expected_branch=host_context[3],
                                receipt=receipt,
                            )
                        else:
                            raise ProjectWriterClientError(
                                "Host revert reconciliation conflicted.",
                                code="operation_conflict",
                            )
                    else:
                        reverted = await self.project_host.revert(
                            project_id=host_context[0],
                            expected_head=host_context[1],
                            expected_branch=host_context[3],
                            receipt=receipt,
                        )
                else:
                    project_id, expected_head, _ = self._project_writer_context(record)
                    assert self.project_writer is not None
                    reverted = await self.project_writer.revert(
                        project_id=project_id,
                        expected_head=expected_head,
                        receipt=receipt,
                    )
                if reverted != receipt:
                    raise ApplierClientError(
                        "Coding revert receipt does not match.",
                        code="invalid_response",
                    )
            except (ApplierClientError, ProjectWriterClientError) as exc:
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
        if record.state in {"running", "cancelling"}:
            raise _http_error(status.HTTP_409_CONFLICT, "turn_in_progress")
        if record.state not in {"applied", "published", "reverted"}:
            changes = await self._current_changes(record)
            if changes["files"]:
                raise _http_error(status.HTTP_409_CONFLICT, "session_has_draft")
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
            await self._close_worker_and_release(record, required=True)
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
            await self._close_worker_and_release(record, required=False)
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
                await self._close_worker_and_release(record, required=False)

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
            if not _worker_project_matches(snapshot.get("project"), record.project):
                raise CodingRecoveryError(
                    "Coding recovery project is inconsistent.",
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
                publish=(
                    _publish_storage_payload(record)
                    if self._is_builtin_project(record)
                    else None
                ),
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
            project_context = _recovery_project_context(record, recovery_id)
            await asyncio.to_thread(
                self.recovery_store.save,
                recovery,
                project_context,
            )
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

    async def _load_recovery_project_context(
        self,
        record: RecoveryRecord,
    ) -> RecoveryProjectContext:
        if self.recovery_store is None:
            return RecoveryProjectContext.builtin(record.recovery_id)
        try:
            context = await asyncio.to_thread(
                self.recovery_store.load_project_context,
                record.recovery_id,
            )
        except CodingRecoveryError as exc:
            self.recovery_reason = _safe_code(exc.code)
            raise _recovery_http_error(exc) from exc
        return context or RecoveryProjectContext.builtin(record.recovery_id)

    async def _release_project_source(
        self,
        source: dict[str, Any] | None,
    ) -> bool:
        if source is None or source.get("kind") not in {
            ProjectKind.LOCAL_CLONE.value,
            ProjectKind.HOST_GIT.value,
        }:
            return True
        if self.project_source is None:
            self.projects_reason = "project_source_unavailable"
            return False
        project_id = source.get("project_id")
        lease_id = source.get("lease_id")
        if not isinstance(project_id, str) or not isinstance(lease_id, str):
            self.projects_reason = "invalid_project_source_response"
            return False
        try:
            released = await self.project_source.release(project_id, lease_id)
        except ProjectSourceClientError as exc:
            self.projects_reason = _safe_code(exc.code)
            return False
        except Exception:
            self.projects_reason = "project_source_unavailable"
            return False
        if not released:
            self.projects_reason = "project_lease_changed"
            return False
        self.projects_reason = None
        return True

    async def project_locked(self, project_id: str) -> bool:
        async with self._lock:
            if any(
                record.project.get("id") == project_id
                for record in self._sessions.values()
            ):
                return True
        recovery = await self._load_recovery_record(required=False)
        if recovery is None:
            return False
        try:
            context = await self._load_recovery_project_context(recovery)
        except HTTPException:
            return True
        return context.project_id == project_id

    async def any_host_project_locked(self) -> bool:
        async with self._lock:
            if any(
                record.project.get("kind") == ProjectKind.HOST_GIT.value
                for record in self._sessions.values()
            ):
                return True
        recovery = await self._load_recovery_record(required=False)
        if recovery is None:
            return False
        try:
            context = await self._load_recovery_project_context(recovery)
        except HTTPException:
            return True
        return context.kind is ProjectKind.HOST_GIT

    async def _close_worker_and_release(
        self,
        record: CodingApiSession,
        *,
        required: bool,
    ) -> bool:
        released = await self._close_worker_session_and_release(
            record.worker_session_id,
            record.project_source,
            required=required,
        )
        if released:
            record.project_source = None
        return released

    async def _prune_missing_worker_sessions(self) -> int:
        async with self._lock:
            records = list(self._sessions.values())
        removed = 0
        for record in records:
            try:
                await self.worker.session_status(record.worker_session_id)
            except CodingWorkerError as exc:
                if exc.code != "session_not_found":
                    continue
                released = await self._release_project_source(record.project_source)
                if not released:
                    continue
                async with self._lock:
                    if self._sessions.get(record.session_id) is record:
                        self._sessions.pop(record.session_id, None)
                        record.project_source = None
                        removed += 1
            except Exception:
                continue
        return removed

    async def _close_worker_session_and_release(
        self,
        worker_session_id: str,
        source: dict[str, Any] | None,
        *,
        required: bool,
    ) -> bool:
        try:
            await self.worker.close(worker_session_id)
        except CodingWorkerError as exc:
            if required:
                raise _worker_http_error(exc) from exc
            return False
        except Exception:
            if required:
                raise _http_error(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "worker_unavailable",
                )
            return False
        released = await self._release_project_source(source)
        if required and not released:
            raise _http_error(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                self.projects_reason or "project_source_unavailable",
            )
        return released

    @staticmethod
    def _is_builtin_project(record: CodingApiSession) -> bool:
        return record.project.get("kind") == ProjectKind.BUILTIN.value

    @staticmethod
    def _is_host_project(record: CodingApiSession) -> bool:
        return record.project.get("kind") == ProjectKind.HOST_GIT.value

    def _require_builtin_project(self, record: CodingApiSession) -> None:
        if not self._is_builtin_project(record):
            raise _http_error(
                status.HTTP_409_CONFLICT,
                "project_operation_unavailable",
            )

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
        if self._is_host_project(record):
            await self._reconcile_recovered_host_writeback(
                record,
                patch,
                paths,
                fingerprint,
            )
            return
        if not self._is_builtin_project(record):
            await self._reconcile_recovered_project_writeback(
                record,
                patch,
                paths,
                fingerprint,
            )
            return
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

    async def _reconcile_recovered_host_writeback(
        self,
        record: CodingApiSession,
        patch: str,
        paths: list[str],
        fingerprint: str,
    ) -> None:
        if self.project_host is None:
            self._mark_recovery_conflict(record, "project_host_unavailable")
            return
        try:
            project_id, expected_head, expected_fingerprint, expected_branch = (
                self._host_writer_context(record)
            )
            source = record.project_source
            if not isinstance(source, dict) or not isinstance(source.get("head"), str):
                raise ValueError("Host project baseline is missing")
            _current_parent, lineage = self._host_cycle_lineage(
                record,
                base_head=source["head"],
                branch=expected_branch,
                fingerprint=fingerprint,
            )
        except HTTPException:
            self._mark_recovery_conflict(record, "project_changed")
            return
        except (TypeError, ValueError):
            self._mark_recovery_conflict(record, "cycle_lineage_invalid")
            return
        if expected_fingerprint != fingerprint:
            self._mark_recovery_conflict(record, "snapshot_mismatch")
            return
        apply_operation = record.apply_operation_id
        apply_receipt = record.apply_receipt
        if lineage and apply_operation is None:
            if (
                record.apply_state is not ApplyState.NOT_APPLIED
                or record.commit_operation_id is not None
                or record.commit_state is not CommitState.NOT_COMMITTED
            ):
                self._mark_recovery_conflict(record, "cycle_lineage_invalid")
                return
            last_cycle = record.cycle_history.cycles[-1]
            parent, archived_apply, archived_commit = lineage[-1]
            try:
                state, restored_apply, restored_commit = (
                    await self.project_host.reconcile_commit(
                        project_id=project_id,
                        expected_head=parent,
                        expected_branch=expected_branch,
                        operation_id=archived_apply.apply_id,
                        revision=last_cycle.revision,
                        patch=last_cycle.patch,
                        paths=[item.path for item in archived_apply.files],
                        expected_fingerprint=fingerprint,
                        apply_receipt=archived_apply,
                        commit_operation_id=archived_commit.commit_id,
                        message=archived_commit.message,
                    )
                )
            except ProjectWriterClientError as exc:
                self._mark_recovery_conflict(record, _safe_code(exc.code))
                return
            if (
                state != "committed"
                or restored_apply != archived_apply
                or restored_commit != archived_commit
            ):
                self._mark_recovery_conflict(record, "commit_recovery_conflict")
            return
        if (
            apply_operation is not None
            and apply_receipt is not None
            and record.commit_operation_id is not None
            and record.commit_message is not None
            and record.commit_state is not CommitState.NOT_COMMITTED
        ):
            try:
                state, restored_apply, commit_receipt = (
                    await self.project_host.reconcile_commit(
                        project_id=project_id,
                        expected_head=expected_head,
                        expected_branch=expected_branch,
                        operation_id=apply_operation,
                        revision=record.apply_revision or 0,
                        patch=patch,
                        paths=paths,
                        expected_fingerprint=fingerprint,
                        apply_receipt=apply_receipt,
                        commit_operation_id=record.commit_operation_id,
                        message=record.commit_message,
                    )
                )
            except ProjectWriterClientError as exc:
                self._mark_recovery_conflict(record, _safe_code(exc.code))
                return
            record.apply_receipt = restored_apply
            record.apply_state = ApplyState.APPLIED
            if state == "committed" and commit_receipt is not None:
                record.commit_receipt = commit_receipt
                record.commit_state = CommitState.COMMITTED
                record.apply_reason = None
                record.commit_reason = None
                record.commit_finished_at = time.time()
                record.state = "applied"
                return
            if state == "undone" and commit_receipt is not None:
                record.commit_receipt = commit_receipt
                record.commit_state = CommitState.UNDONE
                record.apply_reason = None
                record.commit_reason = None
                record.commit_finished_at = time.time()
                record.state = "applied"
                return
            if state == "not_committed" and record.commit_receipt is None:
                record.commit_state = CommitState.FAILED
                record.commit_reason = "commit_not_completed"
                record.state = "applied"
                return
            self._mark_recovery_conflict(record, "commit_recovery_conflict")
            return

        if apply_operation is None or record.apply_state is ApplyState.NOT_APPLIED:
            return
        intended_revert = (
            apply_receipt is not None
            and record.apply_state in {ApplyState.REVERTING, ApplyState.FAILED}
        )
        try:
            state, receipt = await self.project_host.reconcile_apply(
                project_id=project_id,
                expected_head=expected_head,
                expected_branch=expected_branch,
                operation_id=apply_operation,
                revision=record.apply_revision or 0,
                patch=patch,
                paths=paths,
                expected_fingerprint=fingerprint,
            )
        except ProjectWriterClientError as exc:
            self._mark_recovery_conflict(record, _safe_code(exc.code))
            return
        if state == "conflict":
            self._mark_recovery_conflict(record, "apply_recovery_conflict")
        elif state == "applied" and receipt is not None:
            record.apply_receipt = receipt
            record.apply_state = ApplyState.APPLIED
            record.apply_reason = None
            record.apply_finished_at = time.time()
            record.state = "applied"
        elif state == "not_applied" and intended_revert:
            record.apply_state = ApplyState.REVERTED
            record.apply_reason = None
            record.apply_finished_at = time.time()
            record.state = "reverted"
        elif state == "not_applied" and record.apply_state is ApplyState.REVERTED:
            record.apply_reason = None
            record.state = "reverted"
        elif state == "not_applied" and record.apply_state in {
            ApplyState.APPLYING,
            ApplyState.FAILED,
        }:
            record.apply_state = ApplyState.NOT_APPLIED
            record.apply_operation_id = None
            record.apply_receipt = None
            record.apply_reason = None
            record.apply_finished_at = time.time()
            record.state = "ready"
        else:
            self._mark_recovery_conflict(record, "apply_recovery_conflict")

    async def _reconcile_recovered_project_writeback(
        self,
        record: CodingApiSession,
        patch: str,
        paths: list[str],
        fingerprint: str,
    ) -> None:
        if self.project_writer is None:
            self._mark_recovery_conflict(record, "project_writer_unavailable")
            return
        try:
            project_id, expected_head, expected_fingerprint = (
                self._project_writer_context(record)
            )
        except HTTPException:
            self._mark_recovery_conflict(record, "project_changed")
            return
        if expected_fingerprint != fingerprint:
            self._mark_recovery_conflict(record, "snapshot_mismatch")
            return
        apply_operation = record.apply_operation_id
        apply_receipt = record.apply_receipt
        if (
            apply_operation is not None
            and apply_receipt is not None
            and record.commit_operation_id is not None
            and record.commit_message is not None
            and record.commit_state is not CommitState.NOT_COMMITTED
        ):
            try:
                state, restored_apply, commit_receipt = (
                    await self.project_writer.reconcile_commit(
                        project_id=project_id,
                        expected_head=expected_head,
                        operation_id=apply_operation,
                        revision=record.apply_revision or 0,
                        patch=patch,
                        paths=paths,
                        expected_fingerprint=fingerprint,
                        apply_receipt=apply_receipt,
                        commit_operation_id=record.commit_operation_id,
                        message=record.commit_message,
                    )
                )
            except ProjectWriterClientError as exc:
                self._mark_recovery_conflict(record, _safe_code(exc.code))
                return
            record.apply_receipt = restored_apply
            record.apply_state = ApplyState.APPLIED
            if state == "committed" and commit_receipt is not None:
                record.commit_receipt = commit_receipt
                record.commit_state = CommitState.COMMITTED
                record.state = "applied"
                return
            if state == "undone" and commit_receipt is not None:
                record.commit_receipt = commit_receipt
                record.commit_state = CommitState.UNDONE
                record.state = "applied"
                return
            if state == "not_committed" and record.commit_receipt is None:
                record.commit_state = CommitState.FAILED
                record.commit_reason = "commit_not_completed"
                record.state = "applied"
                return
            self._mark_recovery_conflict(record, "commit_recovery_conflict")
            return

        if apply_operation is None or record.apply_state is ApplyState.NOT_APPLIED:
            return
        intended_revert = (
            apply_receipt is not None
            and record.apply_state in {ApplyState.REVERTING, ApplyState.FAILED}
        )
        try:
            state, receipt = await self.project_writer.reconcile_apply(
                project_id=project_id,
                expected_head=expected_head,
                operation_id=apply_operation,
                revision=record.apply_revision or 0,
                patch=patch,
                paths=paths,
                expected_fingerprint=fingerprint,
            )
        except ProjectWriterClientError as exc:
            self._mark_recovery_conflict(record, _safe_code(exc.code))
            return
        if state == "conflict":
            self._mark_recovery_conflict(record, "apply_recovery_conflict")
        elif state == "applied" and receipt is not None:
            record.apply_receipt = receipt
            record.apply_state = ApplyState.APPLIED
            record.state = "applied"
        elif state == "not_applied" and intended_revert:
            record.apply_state = ApplyState.REVERTED
            record.state = "reverted"
        elif state == "not_applied" and record.apply_state is ApplyState.REVERTED:
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

    async def _require_commit_available(
        self,
        record: CodingApiSession,
        expected_fingerprint: str,
    ) -> None:
        if self._is_host_project(record):
            _, _, fingerprint, _ = await self._require_host_writer_available(
                record,
                require_clean=False,
            )
            if fingerprint != expected_fingerprint:
                raise _http_error(status.HTTP_409_CONFLICT, "snapshot_mismatch")
            return
        if not self._is_builtin_project(record):
            _, _, fingerprint = await self._require_project_writer_available(record)
            if fingerprint != expected_fingerprint:
                raise _http_error(status.HTTP_409_CONFLICT, "snapshot_mismatch")
            return
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

    async def _require_project_writer_available(
        self,
        record: CodingApiSession,
    ) -> tuple[str, str, str]:
        if record.project.get("kind") != ProjectKind.LOCAL_CLONE.value:
            raise _http_error(
                status.HTTP_409_CONFLICT,
                "project_operation_unavailable",
            )
        features = record.project.get("features")
        if not isinstance(features, dict) or features.get("apply") is not True:
            raise _http_error(
                status.HTTP_409_CONFLICT,
                _safe_code(record.project.get("writeback_reason") or "project_operation_unavailable"),
            )
        capability = await self._project_writeback_capability()
        if capability["available"] is not True:
            reason = str(capability.get("reason") or "project_writer_unavailable")
            raise _http_error(status.HTTP_503_SERVICE_UNAVAILABLE, reason)
        return self._project_writer_context(record)

    async def _require_host_writer_available(
        self,
        record: CodingApiSession,
        *,
        require_clean: bool,
    ) -> tuple[str, str, str, str]:
        if not self._is_host_project(record) or self.project_host is None:
            raise _http_error(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "project_host_writeback_unavailable",
            )
        features = record.project.get("features")
        if not isinstance(features, dict) or features.get("apply") is not True:
            raise _http_error(
                status.HTTP_409_CONFLICT,
                _safe_code(
                    record.project.get("writeback_reason")
                    or "project_operation_unavailable"
                ),
            )
        try:
            health = await self.project_host.health()
        except ProjectHostError as exc:
            raise _project_host_http_error(exc) from exc
        if health.get("available") is not True:
            raise _http_error(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                _safe_code(
                    health.get("reason") or "project_host_writeback_unavailable"
                ),
            )
        project_id, head, fingerprint, branch = self._host_writer_context(record)
        if require_clean:
            try:
                self.project_host.check_project(project_id, head, branch)
            except ProjectHostError as exc:
                raise _project_host_http_error(exc) from exc
        return project_id, head, fingerprint, branch

    @staticmethod
    def _project_writer_context(
        record: CodingApiSession,
    ) -> tuple[str, str, str]:
        source = record.project_source
        if not isinstance(source, dict):
            raise _http_error(status.HTTP_409_CONFLICT, "project_changed")
        project_id = source.get("project_id")
        head = source.get("head")
        fingerprint = source.get("fingerprint")
        if (
            not isinstance(project_id, str)
            or record.project.get("id") != project_id
            or not isinstance(head, str)
            or re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", head) is None
            or not isinstance(fingerprint, str)
            or SNAPSHOT_FINGERPRINT_PATTERN.fullmatch(fingerprint) is None
        ):
            raise _http_error(status.HTTP_409_CONFLICT, "project_changed")
        return project_id, head, fingerprint

    @staticmethod
    def _host_cycle_lineage(
        record: CodingApiSession,
        *,
        base_head: str,
        branch: str,
        fingerprint: str,
    ) -> tuple[str, tuple[tuple[str, ApplyReceipt, CommitReceipt], ...]]:
        parent = base_head
        seen_operations: set[str] = set()
        lineage: list[tuple[str, ApplyReceipt, CommitReceipt]] = []
        for cycle in record.cycle_history.cycles:
            if (
                cycle.state is not CycleState.COMMITTED
                or cycle.apply.get("state") != ApplyState.APPLIED.value
                or cycle.commit.get("state") != CommitState.COMMITTED.value
            ):
                raise ValueError("Host cycle is not committed")
            apply_receipt = _apply_receipt_from_storage(
                cycle.apply.get("receipt")
            )
            commit_receipt = _commit_receipt_from_storage(
                cycle.commit.get("receipt")
            )
            paths = tuple(item.path for item in apply_receipt.files)
            try:
                patch_paths = tuple(_diff_paths(cycle.patch))
            except HTTPException as exc:
                raise ValueError("Host cycle Patch is invalid") from exc
            change_files = cycle.changes.get("files")
            if not isinstance(change_files, list) or any(
                not isinstance(item, dict) or not isinstance(item.get("path"), str)
                for item in change_files
            ):
                raise ValueError("Host cycle changes are invalid")
            change_paths = tuple(item["path"] for item in change_files)
            if (
                apply_receipt.revision != cycle.revision
                or apply_receipt.snapshot_fingerprint != fingerprint
                or patch_paths != paths
                or change_paths != paths
                or commit_receipt.revision != cycle.revision
                or commit_receipt.apply_id != apply_receipt.apply_id
                or commit_receipt.files != paths
                or commit_receipt.branch != branch
                or commit_receipt.parent_sha != parent
                or commit_receipt.commit_sha == parent
                or apply_receipt.apply_id in seen_operations
                or commit_receipt.commit_id in seen_operations
            ):
                raise ValueError("Host cycle lineage is invalid")
            seen_operations.update(
                {apply_receipt.apply_id, commit_receipt.commit_id}
            )
            lineage.append((parent, apply_receipt, commit_receipt))
            parent = commit_receipt.commit_sha
        return parent, tuple(lineage)

    @staticmethod
    def _host_writer_context(
        record: CodingApiSession,
    ) -> tuple[str, str, str, str]:
        if record.project.get("kind") != ProjectKind.HOST_GIT.value:
            raise _http_error(status.HTTP_409_CONFLICT, "project_changed")
        project_id, head, fingerprint = CodingService._project_writer_context(record)
        source = record.project_source
        branch = source.get("branch") if isinstance(source, dict) else None
        if not isinstance(branch, str) or not branch:
            raise _http_error(status.HTTP_409_CONFLICT, "project_changed")
        try:
            parent, _lineage = CodingService._host_cycle_lineage(
                record,
                base_head=head,
                branch=branch,
                fingerprint=fingerprint,
            )
        except (TypeError, ValueError) as exc:
            raise _http_error(
                status.HTTP_409_CONFLICT,
                "cycle_lineage_invalid",
            ) from exc
        return project_id, parent, fingerprint, branch

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
        if not self._is_builtin_project(record) and not self.commands_enabled:
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
            verification["state"] in {"awaiting_confirmation", "running"}
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

    def _require_project_verification_enabled(
        self,
        record: CodingApiSession,
    ) -> None:
        if not self._is_builtin_project(record) and not self.commands_enabled:
            raise _http_error(
                status.HTTP_409_CONFLICT,
                "project_operation_unavailable",
            )


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
        projects_enabled = os.getenv(
            "CODING_PROJECTS_ENABLED",
            "false",
        ).strip().lower() in {"1", "true", "yes", "on"}
        project_host_enabled = os.getenv(
            "CODING_PROJECT_HOST_ENABLED",
            "false",
        ).strip().lower() in {"1", "true", "yes", "on"}
        project_host: ProjectHostRuntime | None = None
        project_host_reason: str | None = None
        if project_host_enabled:
            try:
                project_host = create_project_host_runtime()
            except ProjectHostError as exc:
                project_host_reason = _safe_code(exc.code)
            except (OSError, TypeError, ValueError):
                project_host_reason = "project_host_not_configured"
        any_projects_enabled = projects_enabled or project_host_enabled
        project_source_socket_path = os.getenv(
            "CODING_PROJECT_SOURCE_SOCKET_PATH",
            "/run/modelmirror-coding-projects/source.sock",
        )
        project_writeback_enabled = os.getenv(
            "CODING_PROJECT_WRITEBACK_ENABLED",
            "false",
        ).strip().lower() in {"1", "true", "yes", "on"}
        project_writer_socket_path = os.getenv(
            "CODING_PROJECT_WRITER_SOCKET_PATH",
            "/run/modelmirror-coding-writeback/writer.sock",
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
            project_source=(
                CodingProjectSourceClient(Path(project_source_socket_path))
                if any_projects_enabled
                else None
            ),
            project_host=project_host,
            project_writer=(
                CodingProjectWriterClient(Path(project_writer_socket_path))
                if project_writeback_enabled
                else None
            ),
            projects_enabled=any_projects_enabled,
            project_host_enabled=project_host_enabled,
            project_host_reason=project_host_reason,
            project_writeback_enabled=project_writeback_enabled,
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
router.include_router(project_host_router)


@router.get("/capabilities")
async def coding_capabilities(response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return await get_coding_service().capabilities()


@router.get("/projects")
async def coding_projects(response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return await get_coding_service().project_catalog()


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
        "project": record.project,
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
async def create_coding_session(
    payload: CodingSessionRequest | None = None,
) -> dict[str, Any]:
    record = await get_coding_service().create_session(
        payload.project_id if payload is not None else "modelmirror"
    )
    return {
        "id": record.session_id,
        "status": record.state,
        "project": record.project,
    }


@router.post(
    "/worker-tasks/{task_id}/handoff",
    status_code=status.HTTP_201_CREATED,
)
async def handoff_coding_worker_task(task_id: str) -> dict[str, Any]:
    try:
        try:
            from server.coding_worker.api import get_coding_worker_service
            from server.coding_worker.contracts import TaskState
        except ModuleNotFoundError:
            from coding_worker.api import get_coding_worker_service
            from coding_worker.contracts import TaskState

        worker_service = get_coding_worker_service()
        task = worker_service.store.get_task(task_id)
        if (
            task.state is not TaskState.COMPLETED
            or task.workspace_id is None
            or task.spec.workspace_source.kind != "host_snapshot"
        ):
            raise _http_error(
                status.HTTP_409_CONFLICT, "worker_task_not_writeback_ready"
            )
        if (
            worker_service.harness_runner is None
            or not worker_service.harness_runner.acceptance_satisfied(task_id)
        ):
            raise _http_error(
                status.HTTP_409_CONFLICT, "worker_acceptance_invalidated"
            )
        patch_bytes = worker_service.workspace_broker.diff(task.workspace_id)
        try:
            patch = patch_bytes.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise _http_error(
                status.HTTP_409_CONFLICT, "worker_writeback_patch_unsupported"
            ) from exc
        if not worker_service.harness_runner.acceptance_satisfied(task_id):
            raise _http_error(
                status.HTTP_409_CONFLICT, "worker_acceptance_invalidated"
            )
        paths = _diff_paths(patch)
        if not paths:
            raise _http_error(status.HTTP_409_CONFLICT, "draft_is_empty")
        record = await get_coding_service().adopt_worker_patch(
            project_id=task.spec.workspace_source.source_id,
            expected_head=task.spec.workspace_source.revision,
            patch=patch,
            paths=paths,
        )
        changes = await get_coding_service().changes(record.session_id)
        return {
            "id": record.session_id,
            "status": record.state,
            "project": record.project,
            "revision": changes["revision"],
            "task_id": task_id,
        }
    except HTTPException:
        raise
    except Exception as exc:
        code = getattr(exc, "code", "worker_handoff_failed")
        http_status = (
            status.HTTP_404_NOT_FOUND
            if code in {"task_not_found", "workspace_not_found"}
            else status.HTTP_409_CONFLICT
        )
        raise _http_error(http_status, _safe_code(code)) from exc


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
) -> dict[str, Any]:
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


@router.post("/sessions/{session_id}/verification/confirm")
async def confirm_coding_verification(
    session_id: str,
    payload: VerificationConfirmRequest,
    response: Response,
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return await get_coding_service().verification_confirm(
        session_id,
        payload.revision,
        payload.confirmation_id,
    )


@router.get("/sessions/{session_id}/commands/pending")
async def pending_coding_command(
    session_id: str,
    response: Response,
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return await get_coding_service().command_pending(session_id)


@router.post("/sessions/{session_id}/commands/{request_id}/decision")
async def decide_coding_command(
    session_id: str,
    request_id: str,
    payload: CommandDecisionRequest,
    response: Response,
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return await get_coding_service().command_decision(
        session_id,
        request_id,
        payload.decision,
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


def _project_from_source(source: dict[str, Any] | None) -> dict[str, Any]:
    if source is None:
        return ProjectSummary.builtin().to_public_dict()
    try:
        kind = ProjectKind(source["kind"])
        if kind not in {ProjectKind.LOCAL_CLONE, ProjectKind.HOST_GIT}:
            raise ValueError("unsupported project source")
        summary = ProjectSummary(
            project_id=str(source["project_id"]),
            name=str(source["name"]),
            kind=kind,
            state=ProjectState.AVAILABLE,
            reason=None,
            branch=str(source["branch"]),
            head=str(source["head"]),
            features=(
                ProjectFeatures.host_git()
                if kind is ProjectKind.HOST_GIT
                else ProjectFeatures.local_draft()
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise _http_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "invalid_project_source_response",
        ) from exc
    return summary.to_public_dict()


def _worker_project_matches(value: Any, expected: dict[str, Any]) -> bool:
    if value is None and expected.get("kind") == ProjectKind.BUILTIN.value:
        return True
    if not isinstance(value, dict):
        return False
    for key in ("id", "name", "kind", "head"):
        if value.get(key) != expected.get(key):
            return False
    expected_branch = expected.get("branch")
    return expected_branch is None or value.get("branch") == expected_branch


def _recovery_project_context(
    record: CodingApiSession,
    recovery_id: str,
) -> RecoveryProjectContext:
    if record.project.get("kind") == ProjectKind.BUILTIN.value:
        return RecoveryProjectContext.builtin(recovery_id)
    source = record.project_source
    if not isinstance(source, dict):
        raise CodingRecoveryError(
            "Coding recovery project source is missing.",
            code="recovery_snapshot_invalid",
        )
    try:
        return RecoveryProjectContext(
            recovery_id=recovery_id,
            project_id=str(source["project_id"]),
            kind=ProjectKind(source["kind"]),
            name=str(source["name"]),
            head=str(source["head"]),
            branch=(
                str(source["branch"])
                if source.get("kind") == ProjectKind.HOST_GIT.value
                else None
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CodingRecoveryError(
            "Coding recovery project source is invalid.",
            code="recovery_snapshot_invalid",
        ) from exc


def _project_source_http_error(exc: ProjectSourceClientError) -> HTTPException:
    code = _safe_code(exc.code)
    if code == "project_not_found":
        return _http_error(status.HTTP_404_NOT_FOUND, code)
    if code.startswith("project_") and code not in {
        "project_source_unavailable",
        "project_source_not_configured",
        "project_source_internal_error",
        "project_source_timeout",
    }:
        return _http_error(status.HTTP_409_CONFLICT, code)
    if code == "snapshot_limit_exceeded":
        return _http_error(status.HTTP_409_CONFLICT, code)
    return _http_error(status.HTTP_503_SERVICE_UNAVAILABLE, code)


def _project_host_http_error(exc: ProjectHostError) -> HTTPException:
    code = _safe_code(exc.code)
    if code in {"project_not_found", "project_host_not_found"}:
        return _http_error(status.HTTP_404_NOT_FOUND, code)
    if code in {
        "project_changed",
        "project_active",
        "project_host_request_mismatch",
    }:
        return _http_error(status.HTTP_409_CONFLICT, code)
    return _http_error(status.HTTP_503_SERVICE_UNAVAILABLE, code)


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
    elif event.kind is CodingEventKind.COMMAND_REQUESTED:
        public_data = {
            "request_id": _safe_identifier(data.get("request_id")),
            "command": _public_command(data.get("command")),
            "expires_at": _safe_timestamp(data.get("expires_at")),
        }
    elif event.kind is CodingEventKind.COMMAND_RESOLVED:
        result = data.get("result")
        public_data = {
            "request_id": _safe_identifier(data.get("request_id")),
            "state": _safe_code(data.get("state")),
            "result": (
                None
                if not isinstance(result, dict)
                else {
                    "status": _safe_code(result.get("status")),
                    "exit_code": (
                        result.get("exit_code")
                        if type(result.get("exit_code")) is int
                        else None
                    ),
                    "output": sanitize_verification_output(
                        result.get("output", ""), limit=16_000
                    ).text,
                    "duration_seconds": _safe_duration(
                        result.get("duration_seconds")
                    ),
                }
            ),
        }
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


def _safe_identifier(value: Any) -> str:
    text = str(value or "")
    return text if SAFE_IDENTIFIER.fullmatch(text) is not None else "invalid"


def _safe_timestamp(value: Any) -> float | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        return None
    return float(value)


def _safe_duration(value: Any) -> float:
    duration = _safe_timestamp(value)
    return 0.0 if duration is None else min(duration, 600.0)


def _public_command(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _http_error(status.HTTP_503_SERVICE_UNAVAILABLE, "invalid_worker_response")
    try:
        validated = VerificationCommandPayload.model_validate(value)
    except (TypeError, ValueError) as exc:
        raise _http_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "invalid_worker_response",
        ) from exc
    result = validated.model_dump(by_alias=True)
    result["id"] = _safe_identifier(result["id"])
    result["name"] = sanitize_verification_output(
        result["name"], limit=240, keep_tail=False
    ).text
    result["argv"] = [
        sanitize_verification_output(item, limit=1_000, keep_tail=False).text
        for item in result["argv"]
    ]
    result["cwd"] = _sanitize_text(result["cwd"], 500)
    return result


def _public_command_request(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _http_error(status.HTTP_503_SERVICE_UNAVAILABLE, "invalid_worker_response")
    request_id = _safe_identifier(value.get("request_id"))
    if request_id == "invalid":
        raise _http_error(status.HTTP_503_SERVICE_UNAVAILABLE, "invalid_worker_response")
    result = value.get("result")
    return {
        "request_id": request_id,
        "command": _public_command(value.get("command")),
        "state": _safe_code(value.get("state")),
        "created_at": _safe_timestamp(value.get("created_at")),
        "expires_at": _safe_timestamp(value.get("expires_at")),
        "result": (
            None
            if not isinstance(result, dict)
            else {
                "status": _safe_code(result.get("status")),
                "exit_code": (
                    result.get("exit_code")
                    if type(result.get("exit_code")) is int
                    else None
                ),
                "output": sanitize_verification_output(
                    result.get("output", ""), limit=16_000
                ).text,
                "duration_seconds": _safe_duration(result.get("duration_seconds")),
            }
        ),
    }


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
    if isinstance(verification, dict):
        verification = {
            key: value
            for key, value in verification.items()
            if not str(key).startswith("_")
        }
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
        step["id"] = _safe_identifier(step["id"])
        step["label"] = _sanitize_text(step["label"], 240)
        if step.get("command") is not None:
            step["command"] = _public_command(step["command"])
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
        "apply_conflict",
        "branch_changed",
        "head_changed",
        "operation_conflict",
        "patch_apply_failed",
        "project_changed",
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
        "branch_changed",
        "commit_already_exists",
        "commit_conflict",
        "dirty_index",
        "head_changed",
        "index_changed",
        "operation_conflict",
        "project_changed",
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
