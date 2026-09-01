from __future__ import annotations

import copy
import json
import math
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal


WorkflowExecutionStatus = Literal[
    "running",
    "waiting",
    "ready",
    "completed",
    "failed",
    "cancelled",
    "rejected",
]
WorkflowExecutionSourceKind = Literal[
    "workflow_classic",
    "workflow_deployment",
    "xpert_chat",
    "xpert_app",
    "expert_team_agency",
]
_MAX_RUN_ID_HISTORY = 64
_DUE_WAIT_KINDS = frozenset({"timer", "node_retry"})
_IDEMPOTENT_ATTEMPT_EVENTS = frozenset(
    {
        "node_retry_scheduled",
        "node_retry_started",
        "node_error_routed",
        "workflow_cancelled",
    }
)


class WorkflowExecutionError(Exception):
    """Base error for durable workflow execution state."""


class WorkflowExecutionNotFoundError(WorkflowExecutionError):
    """Raised when an execution does not exist."""


class WorkflowExecutionConflictError(WorkflowExecutionError):
    """Raised when a lease or revision check fails."""


@dataclass(slots=True)
class WorkflowExecution:
    task_id: str
    run_id: str
    run_type: str
    status: WorkflowExecutionStatus
    workflow: dict[str, Any]
    inputs: dict[str, Any]
    previous_run_ids: list[str] = field(default_factory=list)
    source_kind: WorkflowExecutionSourceKind | None = None
    runtime_metadata: dict[str, Any] = field(default_factory=dict)
    continuation: dict[str, Any] = field(default_factory=dict)
    wait_kind: str | None = None
    wait_id: str | None = None
    resume_at: float | None = None
    approval_id: str | None = None
    result: str | None = None
    error: str | None = None
    revision: int = 1
    sequence: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)
    lease_owner: str | None = None
    lease_token: str | None = None
    lease_expires_at: float = 0.0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    completed_at: float | None = None


class WorkflowExecutionStore:
    """Atomic file-backed workflow continuation and safe event journal."""

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        package_dir = Path(__file__).resolve().parent
        self.storage_dir = Path(
            storage_dir
            or os.getenv("AGENT_TASK_STORAGE_DIR", "").strip()
            or package_dir / "storage"
        )
        self.snapshot_path = self.storage_dir / "workflow_executions.json"
        self._lock = threading.RLock()
        self._items: dict[str, WorkflowExecution] = {}
        self._load()

    def create(
        self,
        *,
        task_id: str,
        run_id: str,
        run_type: str,
        workflow: dict[str, Any],
        inputs: dict[str, Any],
        source_kind: WorkflowExecutionSourceKind | None = None,
        runtime_metadata: dict[str, Any] | None = None,
    ) -> WorkflowExecution:
        with self._lock:
            existing = self._items.get(task_id)
            if existing is not None:
                return existing
            is_skill_evaluation = str(run_type) == "skill_evaluation"
            safe_runtime_metadata = dict(runtime_metadata or {})
            is_skill_creator = bool(
                str(run_type) == "workflow"
                and safe_runtime_metadata.get("assistant_agent_id")
                == "skill-creator-assistant-v1"
                and safe_runtime_metadata.get("creator_workflow_version")
                == "skill-creator-workflow-v1"
                and str(safe_runtime_metadata.get("creator_session_id") or "").strip()
            )
            is_skill_experience_distillation = (
                self._is_skill_experience_distillation_runtime(
                    str(run_type), safe_runtime_metadata
                )
            )
            if is_skill_evaluation:
                allowed_metadata = {
                    "runtime_run_type",
                    "skill_evaluation_workflow_version",
                    "skill_evaluation_profile",
                    "skill_evaluation_run_id",
                    "skill_evaluation_item_id",
                    "skill_evaluation_pair_id",
                    "skill_evaluation_case_id",
                    "skill_evaluation_target",
                    "skill_evaluation_overlay_id",
                    "skill_evaluation_workspace_id",
                    "skill_evaluation_frozen_digest",
                    "skill_evaluation_required_resource_paths",
                }
                safe_runtime_metadata = {
                    key: value
                    for key, value in safe_runtime_metadata.items()
                    if key in allowed_metadata
                }
                workflow = {
                    "id": str(workflow.get("id") or "skill-evaluation"),
                    "title": "Skill Creator isolated evaluation",
                }
                inputs = {
                    "case_id": safe_runtime_metadata.get(
                        "skill_evaluation_case_id"
                    )
                }
            elif is_skill_creator:
                allowed_metadata = {
                    "creator_session_id",
                    "creator_session_revision",
                    "assistant_agent_id",
                    "creator_workflow_version",
                    "creator_requirement_ids",
                }
                safe_runtime_metadata = {
                    key: value
                    for key, value in safe_runtime_metadata.items()
                    if key in allowed_metadata
                }
                workflow = {
                    "id": str(workflow.get("id") or "skill-creator"),
                    "title": "Skill Creator generation",
                }
                inputs = {
                    "creator_session_id": safe_runtime_metadata.get(
                        "creator_session_id"
                    )
                }
            elif is_skill_experience_distillation:
                allowed_metadata = {
                    "experience_analysis_key",
                    "experience_workflow_version",
                    "experience_phase",
                }
                safe_runtime_metadata = {
                    key: value
                    for key, value in safe_runtime_metadata.items()
                    if key in allowed_metadata
                }
                workflow = {
                    "id": str(workflow.get("id") or "skill-experience-distillation"),
                    "title": "Skill experience distillation",
                }
                inputs = {
                    "experience_analysis_key": safe_runtime_metadata.get(
                        "experience_analysis_key"
                    )
                }
            item = WorkflowExecution(
                task_id=str(task_id),
                run_id=str(run_id),
                run_type=str(run_type),
                status="running",
                workflow=dict(workflow),
                inputs=dict(inputs),
                source_kind=self._validated_source_kind(
                    source_kind,
                    run_type=str(run_type),
                    strict=True,
                ),
                runtime_metadata=safe_runtime_metadata,
            )
            self._items[item.task_id] = item
            self._persist_unlocked()
            return item

    def get(self, task_id: str) -> WorkflowExecution | None:
        with self._lock:
            return self._items.get(task_id)

    def require(self, task_id: str) -> WorkflowExecution:
        item = self.get(task_id)
        if item is None:
            raise WorkflowExecutionNotFoundError("Workflow execution not found.")
        return item

    def find_by_run_id(self, run_id: str) -> WorkflowExecution | None:
        clean_run_id = str(run_id or "").strip()
        if not clean_run_id:
            return None
        with self._lock:
            matches = [
                item
                for item in self._items.values()
                if item.run_id == clean_run_id
                or clean_run_id in item.previous_run_ids
            ]
        if not matches:
            return None
        matches.sort(key=lambda item: (item.updated_at, item.task_id), reverse=True)
        return matches[0]

    def list_items(
        self,
        *,
        status: str | None = None,
        limit: int = 200,
    ) -> list[WorkflowExecution]:
        with self._lock:
            items = list(self._items.values())
        if status:
            items = [item for item in items if item.status == status]
        items.sort(key=lambda item: (item.updated_at, item.task_id), reverse=True)
        return items[: max(1, min(int(limit), 1000))]

    def list_terminal_deployment_executions(
        self,
        *,
        limit: int = 100,
    ) -> list[WorkflowExecution]:
        """Return terminal durable tasks that still have a deployment projection."""

        with self._lock:
            items = [
                copy.deepcopy(item)
                for item in self._items.values()
                if item.status in {"completed", "failed", "cancelled", "rejected"}
                and str(
                    item.runtime_metadata.get("workflow_deployment_execution_id")
                    or ""
                ).strip()
                and not bool(
                    item.runtime_metadata.get(
                        "deployment_projection_terminal_reconciled"
                    )
                )
            ]
        items.sort(key=lambda item: (item.completed_at or item.updated_at, item.task_id))
        return items[: max(1, min(int(limit), 1000))]

    def mark_deployment_projection_reconciled(
        self,
        task_id: str,
    ) -> WorkflowExecution:
        """Exclude a terminal task from future deployment reconciliation scans."""

        with self._lock:
            item = self._require_unlocked(task_id)
            if item.status not in {"completed", "failed", "cancelled", "rejected"}:
                raise WorkflowExecutionConflictError(
                    "Only terminal workflow executions can be reconciled."
                )
            if not bool(
                item.runtime_metadata.get(
                    "deployment_projection_terminal_reconciled"
                )
            ):
                item.runtime_metadata[
                    "deployment_projection_terminal_reconciled"
                ] = True
                item.revision += 1
                item.updated_at = time.time()
                self._persist_unlocked()
            return copy.deepcopy(item)

    def suspend(
        self,
        task_id: str,
        *,
        approval_id: str | None = None,
        wait_kind: str = "approval",
        wait_id: str | None = None,
        continuation: dict[str, Any],
        safe_event: dict[str, Any] | None = None,
        resume_at: float | None = None,
        expected_lease_token: str | None = None,
    ) -> WorkflowExecution:
        with self._lock:
            item = self._require_unlocked(task_id)
            if item.status != "running":
                raise WorkflowExecutionConflictError(
                    f"Workflow execution cannot wait from {item.status}."
                )
            self._require_optional_lease_unlocked(item, expected_lease_token)
            resolved_wait_id = str(wait_id or approval_id or "").strip()
            if not resolved_wait_id:
                raise WorkflowExecutionConflictError("A wait identifier is required.")
            resolved_wait_kind = str(wait_kind or "approval").strip() or "approval"
            resolved_resume_at: float | None = None
            if resume_at is not None:
                if (
                    not isinstance(resume_at, (int, float))
                    or isinstance(resume_at, bool)
                    or not math.isfinite(float(resume_at))
                ):
                    raise WorkflowExecutionConflictError(
                        "Workflow wait resume time is invalid."
                    )
                resolved_resume_at = float(resume_at)
            if resolved_wait_kind in _DUE_WAIT_KINDS and resolved_resume_at is None:
                raise WorkflowExecutionConflictError(
                    "A durable due wait requires a resume time."
                )
            if (
                item.run_type == "skill_evaluation"
                or self._is_skill_experience_distillation(item)
            ):
                raise WorkflowExecutionConflictError(
                    "Private Skill analysis cannot enter an interactive wait state."
                )
            item.status = "waiting"
            item.wait_kind = resolved_wait_kind
            item.wait_id = resolved_wait_id
            item.approval_id = (
                resolved_wait_id if item.wait_kind == "approval" else None
            )
            item.resume_at = resolved_resume_at
            item.continuation = dict(continuation)
            item.lease_owner = None
            item.lease_token = None
            item.lease_expires_at = 0.0
            item.updated_at = time.time()
            item.revision += 1
            if safe_event is not None:
                self._append_event_unlocked(item, safe_event)
            self._persist_unlocked()
            return item

    def mark_ready(
        self,
        task_id: str,
        *,
        approval_id: str | None = None,
        wait_kind: str = "approval",
        wait_id: str | None = None,
    ) -> WorkflowExecution:
        with self._lock:
            item = self._require_unlocked(task_id)
            if item.status not in {"waiting", "ready"}:
                raise WorkflowExecutionConflictError(
                    f"Workflow execution cannot resume from {item.status}."
                )
            resolved_wait_id = str(wait_id or approval_id or "").strip()
            if item.wait_kind != wait_kind or item.wait_id != resolved_wait_id:
                raise WorkflowExecutionConflictError("Wait target does not match execution.")
            item.status = "ready"
            item.updated_at = time.time()
            item.revision += 1
            self._persist_unlocked()
            return item

    def list_due_waits(
        self,
        *,
        now: float | None = None,
        limit: int = 100,
        wait_kinds: set[str] | frozenset[str] | tuple[str, ...] | None = None,
    ) -> list[WorkflowExecution]:
        current = time.time() if now is None else float(now)
        allowed_kinds = frozenset(wait_kinds or _DUE_WAIT_KINDS)
        if not allowed_kinds or not allowed_kinds.issubset(_DUE_WAIT_KINDS):
            raise WorkflowExecutionConflictError("Due wait kind is invalid.")
        with self._lock:
            items: list[WorkflowExecution] = []
            invalidated = False
            for item in self._items.values():
                if item.wait_kind not in allowed_kinds or item.status not in {
                    "waiting",
                    "ready",
                    "running",
                }:
                    continue
                if not self._valid_due_wait_target(item):
                    self._invalidate_due_wait_unlocked(item)
                    invalidated = True
                    continue
                resume_at = float(item.resume_at)
                if resume_at > current or (
                    item.status == "running"
                    and item.lease_expires_at > current
                ):
                    continue
                items.append(copy.deepcopy(item))
            if invalidated:
                self._persist_unlocked()
        items.sort(key=lambda item: (item.resume_at or 0.0, item.task_id))
        return items[: max(1, min(int(limit), 1000))]

    def list_due_timers(
        self,
        *,
        now: float | None = None,
        limit: int = 100,
    ) -> list[WorkflowExecution]:
        """Compatibility wrapper for callers that only understand timer waits."""

        return self.list_due_waits(
            now=now,
            limit=limit,
            wait_kinds=("timer",),
        )

    def claim_due_wait(
        self,
        task_id: str,
        *,
        wait_kind: str,
        wait_id: str,
        worker_id: str,
        lease_seconds: float = 60.0,
        now: float | None = None,
    ) -> WorkflowExecution:
        """Atomically verify a due wait and acquire its execution lease."""

        current = time.time() if now is None else float(now)
        clean_kind = str(wait_kind or "").strip()
        clean_wait_id = str(wait_id or "").strip()
        if clean_kind not in _DUE_WAIT_KINDS or not clean_wait_id:
            raise WorkflowExecutionConflictError("Due wait target is invalid.")
        with self._lock:
            item = self._require_unlocked(task_id)
            if item.status not in {"waiting", "ready", "running"}:
                raise WorkflowExecutionConflictError(
                    f"Workflow execution cannot be claimed from {item.status}."
                )
            if not self._valid_due_wait_target(item):
                self._invalidate_due_wait_unlocked(item)
                self._persist_unlocked()
                raise WorkflowExecutionConflictError(
                    "Workflow due wait state is invalid."
                )
            if item.wait_kind != clean_kind or item.wait_id != clean_wait_id:
                raise WorkflowExecutionConflictError(
                    "Wait target does not match execution."
                )
            if item.resume_at is None or item.resume_at > current:
                raise WorkflowExecutionConflictError("Workflow wait is not due.")
            if item.status == "running" and item.lease_expires_at > current:
                raise WorkflowExecutionConflictError(
                    "Workflow execution is already leased."
                )
            item.status = "running"
            item.lease_owner = str(worker_id)
            item.lease_token = uuid.uuid4().hex
            item.lease_expires_at = current + max(1.0, float(lease_seconds))
            item.updated_at = current
            item.revision += 1
            self._persist_unlocked()
            return copy.deepcopy(item)

    def claim(
        self,
        task_id: str,
        *,
        worker_id: str,
        lease_seconds: float = 60.0,
        now: float | None = None,
    ) -> WorkflowExecution:
        current = time.time() if now is None else float(now)
        with self._lock:
            item = self._require_unlocked(task_id)
            if item.status == "running" and item.lease_expires_at > current:
                raise WorkflowExecutionConflictError("Workflow execution is already leased.")
            if item.status not in {"ready", "running"}:
                raise WorkflowExecutionConflictError(
                    f"Workflow execution cannot be claimed from {item.status}."
                )
            item.status = "running"
            item.lease_owner = str(worker_id)
            item.lease_token = uuid.uuid4().hex
            item.lease_expires_at = current + max(1.0, float(lease_seconds))
            item.updated_at = current
            item.revision += 1
            self._persist_unlocked()
            return copy.deepcopy(item)

    def refresh_lease(
        self,
        task_id: str,
        *,
        lease_token: str,
        lease_seconds: float = 60.0,
    ) -> WorkflowExecution:
        with self._lock:
            item = self._require_unlocked(task_id)
            if item.status != "running":
                raise WorkflowExecutionConflictError(
                    f"Workflow execution cannot renew from {item.status}."
                )
            clean_token = str(lease_token or "").strip()
            if not clean_token:
                raise WorkflowExecutionConflictError(
                    "Workflow execution lease token is required."
                )
            self._require_optional_lease_unlocked(item, clean_token)
            if (
                not isinstance(lease_seconds, (int, float))
                or isinstance(lease_seconds, bool)
                or not math.isfinite(float(lease_seconds))
            ):
                raise WorkflowExecutionConflictError(
                    "Workflow execution lease duration is invalid."
                )
            current = time.time()
            item.lease_expires_at = current + max(1.0, float(lease_seconds))
            item.updated_at = current
            self._persist_unlocked()
            return item

    def assert_lease(self, task_id: str, *, lease_token: str) -> WorkflowExecution:
        """Verify that a resumed worker still owns a live execution lease."""

        with self._lock:
            item = self._require_unlocked(task_id)
            if item.status != "running":
                raise WorkflowExecutionConflictError(
                    f"Workflow execution lease is unavailable from {item.status}."
                )
            clean_token = str(lease_token or "").strip()
            if not clean_token:
                raise WorkflowExecutionConflictError(
                    "Workflow execution lease token is required."
                )
            self._require_optional_lease_unlocked(item, clean_token)
            return copy.deepcopy(item)

    def release_ready(
        self,
        task_id: str,
        *,
        expected_lease_token: str,
    ) -> WorkflowExecution:
        """Release a claimed continuation so a bounded coordinator can retry it."""
        with self._lock:
            item = self._require_unlocked(task_id)
            if item.status != "running" or not item.wait_id:
                raise WorkflowExecutionConflictError(
                    "Only a claimed waiting continuation can be deferred."
                )
            clean_token = str(expected_lease_token or "").strip()
            if not clean_token:
                raise WorkflowExecutionConflictError(
                    "Workflow execution lease token is required."
                )
            # Deferral is a fencing operation, not another external side effect.
            # The worker may discover `defer_resume` just after its lease expires;
            # it may still release the item only while its token remains current.
            # A reclaim replaces the token under the same Store lock, so a stale
            # worker can never clear the newer owner's lease.
            if str(item.lease_token or "") != clean_token:
                raise WorkflowExecutionConflictError(
                    "Workflow execution lease changed."
                )
            item.status = "ready"
            item.lease_owner = None
            item.lease_token = None
            item.lease_expires_at = 0.0
            item.updated_at = time.time()
            item.revision += 1
            self._persist_unlocked()
            return copy.deepcopy(item)

    def append_event(
        self,
        task_id: str,
        event: dict[str, Any],
        *,
        expected_lease_token: str | None = None,
    ) -> WorkflowExecution:
        with self._lock:
            item = self._require_unlocked(task_id)
            self._require_optional_lease_unlocked(item, expected_lease_token)
            self._append_event_unlocked(item, event)
            item.updated_at = time.time()
            self._persist_unlocked()
            return item

    def update_run_id(
        self,
        task_id: str,
        *,
        run_id: str,
        expected_lease_token: str | None = None,
    ) -> WorkflowExecution:
        with self._lock:
            item = self._require_unlocked(task_id)
            if expected_lease_token is not None:
                if item.status != "running":
                    raise WorkflowExecutionConflictError(
                        f"Workflow execution cannot rebind its run from {item.status}."
                    )
                self._require_optional_lease_unlocked(item, expected_lease_token)
            clean_run_id = str(run_id or "").strip()
            if not clean_run_id or len(clean_run_id) > 200:
                raise WorkflowExecutionConflictError("Workflow run ID is invalid.")
            if item.run_id == clean_run_id:
                return item
            history = [
                value
                for value in (*item.previous_run_ids, item.run_id)
                if value and value != clean_run_id
            ]
            item.previous_run_ids = list(dict.fromkeys(history))[-_MAX_RUN_ID_HISTORY:]
            item.run_id = clean_run_id
            item.updated_at = time.time()
            item.revision += 1
            self._persist_unlocked()
            return item

    def bind_skill_versions(
        self,
        task_id: str,
        *,
        bindings: dict[str, str],
    ) -> WorkflowExecution:
        """Persist immutable Skill version bindings for restart-safe execution."""

        clean = {
            str(skill_id).strip(): str(version_id).strip()
            for skill_id, version_id in dict(bindings or {}).items()
            if str(skill_id).strip() and str(version_id).strip()
        }
        if len(clean) > 200 or any(
            len(skill_id) > 200 or len(version_id) > 80
            for skill_id, version_id in clean.items()
        ):
            raise WorkflowExecutionConflictError(
                "Skill version bindings are invalid."
            )
        with self._lock:
            item = self._require_unlocked(task_id)
            current = item.runtime_metadata.get("skill_version_bindings")
            current = dict(current) if isinstance(current, dict) else {}
            for skill_id, version_id in clean.items():
                existing = str(current.get(skill_id) or "").strip()
                if existing and existing != version_id:
                    raise WorkflowExecutionConflictError(
                        "A running workflow cannot change its bound Skill version."
                    )
                current[skill_id] = version_id
            if item.runtime_metadata.get("skill_version_bindings") == current:
                return item
            item.runtime_metadata["skill_version_bindings"] = dict(
                sorted(current.items())
            )
            item.updated_at = time.time()
            item.revision += 1
            self._persist_unlocked()
            return item

    def complete(
        self,
        task_id: str,
        *,
        result: str,
        expected_lease_token: str | None = None,
    ) -> WorkflowExecution:
        return self._finish(
            task_id,
            status="completed",
            result=result,
            expected_lease_token=expected_lease_token,
        )

    def fail(
        self,
        task_id: str,
        *,
        error: str,
        expected_lease_token: str | None = None,
    ) -> WorkflowExecution:
        return self._finish(
            task_id,
            status="failed",
            error=error,
            expected_lease_token=expected_lease_token,
        )

    def cancel(self, task_id: str, *, error: str = "cancelled") -> WorkflowExecution:
        with self._lock:
            item = self._require_unlocked(task_id)
            if item.status in {"completed", "failed", "cancelled", "rejected"}:
                return item
            return self._finish(task_id, status="cancelled", error=error)

    def reject(self, task_id: str, *, error: str = "rejected") -> WorkflowExecution:
        return self._finish(task_id, status="rejected", error=error)

    def _finish(
        self,
        task_id: str,
        *,
        status: WorkflowExecutionStatus,
        result: str | None = None,
        error: str | None = None,
        expected_lease_token: str | None = None,
    ) -> WorkflowExecution:
        with self._lock:
            item = self._require_unlocked(task_id)
            # A caller that presents a lease is a fenced worker, even when a
            # newer worker has already made the execution terminal. Validate
            # before the idempotent terminal return so a stale worker cannot
            # continue emitting terminal events after losing ownership.
            if expected_lease_token is not None:
                self._require_optional_lease_unlocked(item, expected_lease_token)
            if (
                expected_lease_token is None
                and item.status == "completed"
                and status == "failed"
            ):
                # Keep the terminal result monotonic while retaining the legacy
                # signal used by trusted-source consumers when a completed run is
                # invalidated after capture. The original error is intentionally
                # not persisted.
                if item.runtime_metadata.get("terminal_source_invalidated") is not True:
                    item.runtime_metadata["terminal_source_invalidated"] = True
                    item.updated_at = time.time()
                    item.revision += 1
                    self._persist_unlocked()
                return item
            # A stale worker must never overwrite an already terminal decision.
            if item.status in {"completed", "failed", "cancelled", "rejected"}:
                return item
            if status not in {"cancelled", "rejected"} and expected_lease_token is None:
                self._require_optional_lease_unlocked(item, expected_lease_token)
            item.status = status
            if (
                item.run_type == "skill_evaluation"
                or self._is_skill_creator(item)
                or self._is_skill_experience_distillation(item)
            ):
                item.result = None
                item.error = (
                    (
                        "skill_evaluation_failed"
                        if item.run_type == "skill_evaluation"
                        else (
                            "skill_experience_analysis_failed"
                            if self._is_skill_experience_distillation(item)
                            else "skill_creator_generation_failed"
                        )
                    )
                    if error is not None
                    else None
                )
            else:
                item.result = str(result or "")[:200_000] if result is not None else item.result
                item.error = str(error or "")[:4_000] if error is not None else None
            item.approval_id = None
            item.wait_kind = None
            item.wait_id = None
            item.resume_at = None
            item.continuation = {}
            item.lease_owner = None
            item.lease_token = None
            item.lease_expires_at = 0.0
            item.updated_at = time.time()
            item.completed_at = item.updated_at
            item.revision += 1
            self._persist_unlocked()
            return item

    @staticmethod
    def serialize_public(item: WorkflowExecution) -> dict[str, Any]:
        return {
            "task_id": item.task_id,
            "run_id": item.run_id,
            "run_type": item.run_type,
            "source_kind": item.source_kind,
            "status": item.status,
            "approval_id": item.approval_id,
            "wait_kind": item.wait_kind,
            "wait_id": item.wait_id,
            "resume_at": item.resume_at,
            "result": item.result,
            "error": item.error,
            "revision": item.revision,
            "sequence": item.sequence,
            "events": list(item.events),
            "created_at": item.created_at,
            "updated_at": item.updated_at,
            "completed_at": item.completed_at,
        }

    def _append_event_unlocked(
        self,
        item: WorkflowExecution,
        event: dict[str, Any],
    ) -> None:
        clean = self._safe_event(
            event,
            agency_execution=item.source_kind == "expert_team_agency",
        )
        if clean.get("event") in _IDEMPOTENT_ATTEMPT_EVENTS:
            attempt = clean.get("attempt")
            if any(
                existing.get("event") == clean.get("event")
                and existing.get("node_id") == clean.get("node_id")
                and existing.get("attempt") == attempt
                for existing in item.events
            ):
                return
        item.sequence += 1
        if (
            item.run_type == "skill_evaluation"
            or self._is_skill_creator(item)
            or self._is_skill_experience_distillation(item)
        ):
            clean.pop("message", None)
            clean.pop("final_output", None)
            clean.pop("variable", None)
        clean["sequence"] = item.sequence
        item.events.append(clean)
        if len(item.events) > 500:
            item.events = item.events[-500:]

    @staticmethod
    def _safe_event(
        event: dict[str, Any],
        *,
        agency_execution: bool = False,
    ) -> dict[str, Any]:
        allowed = {
            "event",
            "task_id",
            "run_id",
            "node_id",
            "node_title",
            "node_type",
            "approval_id",
            "wait_kind",
            "wait_id",
            "resume_at",
            "approval_status",
            "request_type",
            "request_id",
            "request_status",
            "host_id",
            "session_id",
            "code",
            "error_code",
            "tool_name",
            "status",
            "skill_id",
            "skill_version_id",
            "requirement",
            "required_skill_ids",
            "available_skill_ids",
            "resource_count",
            "resource_paths",
            "candidate_id",
            "activated_skill_id",
            "source_ref",
            "result_count",
            "message",
            "final_output",
            "variable",
            "provider_route_receipts",
            "attempt",
            "max_attempts",
            "classification",
            "terminal",
            "exhausted",
        }
        if agency_execution:
            allowed.update(
                {
                    "agent_id",
                    "depends_on",
                    "acceptance",
                    "output",
                    "error",
                    "verification",
                    "usage",
                    "cumulative_usage",
                    "warnings",
                    "model_calls",
                    "reused",
                    "reused_task_ids",
                    "resumed_from_task_id",
                    "revision_parent_task_id",
                    "revision_target_task_id",
                    "quality_status",
                    "task_ids",
                    "step_count",
                    "model_id",
                }
            )
        clean = {key: value for key, value in event.items() if key in allowed}
        for key in ("session_id", "code", "error_code"):
            if key in clean:
                clean[key] = str(clean[key] or "")[:200]
        for key in ("attempt", "max_attempts"):
            if key in clean:
                value = clean[key]
                clean[key] = (
                    min(3, max(1, int(value)))
                    if isinstance(value, (int, float)) and not isinstance(value, bool)
                    else 1
                )
        if "classification" in clean:
            value = str(clean["classification"] or "")
            clean["classification"] = (
                value if value in {"transient", "permanent"} else "permanent"
            )
        for key in ("terminal", "exhausted"):
            if key in clean:
                clean[key] = clean[key] is True
        if "provider_route_receipts" in clean:
            clean["provider_route_receipts"] = (
                WorkflowExecutionStore._safe_provider_route_receipt(
                    clean["provider_route_receipts"]
                )
            )
        for key in ("skill_id", "skill_version_id"):
            if key in clean:
                clean[key] = str(clean[key] or "")[:200]
        if "requirement" in clean:
            requirement = str(clean["requirement"] or "").strip()
            clean["requirement"] = (
                requirement if requirement in {"required", "available"} else ""
            )
        for key in ("required_skill_ids", "available_skill_ids"):
            if key in clean:
                values = clean[key] if isinstance(clean[key], list) else []
                clean[key] = [str(value)[:200] for value in values[:200]]
        if "resource_count" in clean:
            value = clean["resource_count"]
            clean["resource_count"] = (
                min(2_000, max(0, int(value)))
                if isinstance(value, (int, float)) and not isinstance(value, bool)
                else 0
            )
        if "resource_paths" in clean:
            values = (
                clean["resource_paths"]
                if isinstance(clean["resource_paths"], list)
                else []
            )
            safe_paths: list[str] = []
            for value in values[:100]:
                raw_path = str(value or "").strip().replace("\\", "/")
                path = PurePosixPath(raw_path)
                if (
                    not raw_path
                    or "\x00" in raw_path
                    or len(raw_path) > 240
                    or path.is_absolute()
                    or any(part in {"", ".", ".."} for part in path.parts)
                    or (path.parts and ":" in path.parts[0])
                ):
                    continue
                normalized = path.as_posix()
                if normalized not in safe_paths:
                    safe_paths.append(normalized)
                if len(safe_paths) == 12:
                    break
            clean["resource_paths"] = safe_paths
        for key in ("message", "final_output"):
            if key in clean:
                clean[key] = str(clean[key] or "")[:200_000]
        for key, limit in (("output", 64 * 1024), ("error", 4_000), ("acceptance", 4_000)):
            if key in clean:
                clean[key] = str(clean[key] or "")[:limit]
        for key in ("depends_on", "task_ids", "reused_task_ids"):
            if key in clean:
                values = clean[key] if isinstance(clean[key], list) else []
                clean[key] = [str(value)[:128] for value in values[:6]]
        if "warnings" in clean:
            values = clean["warnings"] if isinstance(clean["warnings"], list) else []
            clean["warnings"] = [str(value)[:500] for value in values[:10]]
        for usage_key in ("usage", "cumulative_usage"):
            if usage_key in clean:
                raw_usage = (
                    clean[usage_key]
                    if isinstance(clean[usage_key], dict)
                    else {}
                )
                clean[usage_key] = {
                    key: max(0, int(value))
                    for key, value in raw_usage.items()
                    if key in {"input_tokens", "output_tokens"}
                    and isinstance(value, (int, float))
                    and not isinstance(value, bool)
                }
        if "reused" in clean:
            clean["reused"] = bool(clean["reused"])
        if "resumed_from_task_id" in clean:
            clean["resumed_from_task_id"] = str(
                clean["resumed_from_task_id"] or ""
            )[:200]
        for key in ("revision_parent_task_id", "revision_target_task_id"):
            if key in clean:
                clean[key] = str(clean[key] or "")[:200]
        if "verification" in clean:
            raw_verification = (
                clean["verification"]
                if isinstance(clean["verification"], dict)
                else {}
            )
            failed = raw_verification.get("failed")
            clean["verification"] = {
                "pass": bool(raw_verification.get("pass")),
                "failed": [str(value)[:500] for value in failed[:20]]
                if isinstance(failed, list)
                else [],
                "reworked": bool(raw_verification.get("reworked")),
            }
        return clean

    @staticmethod
    def _safe_provider_route_receipt(value: Any) -> dict[str, Any]:
        raw = value if isinstance(value, dict) else {}

        def bounded_integer(
            candidate: Any,
            *,
            default: int,
            minimum: int,
            maximum: int,
        ) -> int:
            if isinstance(candidate, bool):
                return default
            try:
                parsed = int(candidate)
            except (TypeError, ValueError, OverflowError):
                return default
            return max(minimum, min(parsed, maximum))

        allowed_entries = {
            "workflow_interactive_llm",
            "workflow_deployment_llm",
            "workflow_interactive_agent",
            "workflow_deployment_agent",
            "xpert",
            "xpert_app",
            "expert_team_planner",
            "expert_team_dag",
        }
        allowed_statuses = {
            "running",
            "passed",
            "failed",
            "uncertain",
            "cancelled",
        }
        entry_id = str(raw.get("entry_id") or "")
        status = str(raw.get("status") or "failed")
        calls: list[dict[str, Any]] = []
        raw_calls = raw.get("calls")
        for item in raw_calls[:10] if isinstance(raw_calls, list) else []:
            if not isinstance(item, dict):
                continue
            call_status = str(item.get("status") or "failed")
            clean_call: dict[str, Any] = {
                "call_sequence": bounded_integer(
                    item.get("call_sequence"),
                    default=1,
                    minimum=1,
                    maximum=100,
                ),
                "model_id": str(item.get("model_id") or "")[:300],
                "actual_model": (
                    str(item.get("actual_model") or "")[:300] or None
                ),
                "dispatched": bool(item.get("dispatched")),
                "status": (
                    call_status if call_status in allowed_statuses else "failed"
                ),
                "error_code": str(item.get("error_code") or "")[:200] or None,
            }
            for token_key in (
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
            ):
                token_value = item.get(token_key)
                clean_call[token_key] = (
                    min(1_000_000_000, max(0, int(token_value)))
                    if isinstance(token_value, (int, float))
                    and not isinstance(token_value, bool)
                    else None
                )
            calls.append(clean_call)
        reason_codes = raw.get("reason_codes")
        return {
            "contract_version": "modelmirror-provider-workload-routing-v1",
            "entry_id": entry_id if entry_id in allowed_entries else "",
            "routing_mode": "managed_required",
            "run_reference": str(raw.get("run_reference") or "")[:200],
            "status": status if status in allowed_statuses else "failed",
            "call_count": min(
                10,
                sum(1 for item in calls if item["dispatched"]),
            ),
            "reason_codes": [
                str(item)[:200]
                for item in (
                    reason_codes[:20] if isinstance(reason_codes, list) else []
                )
            ],
            "calls": calls,
        }

    @staticmethod
    def _is_skill_creator(item: WorkflowExecution) -> bool:
        metadata = item.runtime_metadata
        return bool(
            item.run_type == "workflow"
            and metadata.get("assistant_agent_id") == "skill-creator-assistant-v1"
            and metadata.get("creator_workflow_version")
            == "skill-creator-workflow-v1"
            and str(metadata.get("creator_session_id") or "").strip()
        )

    @staticmethod
    def _is_skill_experience_distillation_runtime(
        run_type: str,
        metadata: dict[str, Any],
    ) -> bool:
        analysis_key = (
            str(metadata.get("experience_analysis_key") or "").strip().lower()
        )
        return bool(
            run_type == "workflow"
            and len(analysis_key) == 64
            and all(character in "0123456789abcdef" for character in analysis_key)
            and metadata.get("experience_workflow_version")
            == "skill-experience-distillation-v1"
            and metadata.get("experience_phase") == "distillation"
        )

    @classmethod
    def _is_skill_experience_distillation(cls, item: WorkflowExecution) -> bool:
        return cls._is_skill_experience_distillation_runtime(
            item.run_type,
            item.runtime_metadata,
        )

    def _require_unlocked(self, task_id: str) -> WorkflowExecution:
        item = self._items.get(task_id)
        if item is None:
            raise WorkflowExecutionNotFoundError("Workflow execution not found.")
        return item

    @staticmethod
    def _require_optional_lease_unlocked(
        item: WorkflowExecution,
        expected_lease_token: str | None,
    ) -> None:
        current = str(item.lease_token or "")
        expected = str(expected_lease_token or "")
        if not expected:
            return
        if current != expected:
            raise WorkflowExecutionConflictError("Workflow execution lease changed.")
        expires_at = item.lease_expires_at
        if (
            not isinstance(expires_at, (int, float))
            or isinstance(expires_at, bool)
            or not math.isfinite(float(expires_at))
            or float(expires_at) <= time.time()
        ):
            raise WorkflowExecutionConflictError("Workflow execution lease expired.")

    @staticmethod
    def _valid_due_wait_target(item: WorkflowExecution) -> bool:
        resume_at = item.resume_at
        lease_expires_at = item.lease_expires_at
        valid_lease = (
            isinstance(lease_expires_at, (int, float))
            and not isinstance(lease_expires_at, bool)
            and math.isfinite(float(lease_expires_at))
        )
        return bool(
            item.wait_kind in _DUE_WAIT_KINDS
            and isinstance(item.wait_id, str)
            and item.wait_id.strip()
            and isinstance(resume_at, (int, float))
            and not isinstance(resume_at, bool)
            and math.isfinite(float(resume_at))
            and valid_lease
        )

    def _invalidate_due_wait_unlocked(self, item: WorkflowExecution) -> None:
        if item.status in {"completed", "failed", "cancelled", "rejected"}:
            return
        item.status = "failed"
        item.error = "WORKFLOW_WAIT_STATE_INVALID"
        item.wait_kind = None
        item.wait_id = None
        item.resume_at = None
        item.continuation = {}
        item.lease_owner = None
        item.lease_token = None
        item.lease_expires_at = 0.0
        item.completed_at = time.time()
        item.updated_at = item.completed_at
        item.revision += 1
        self._append_event_unlocked(
            item,
            {
                "event": "error",
                "task_id": item.task_id,
                "run_id": item.run_id,
                "code": "WORKFLOW_WAIT_STATE_INVALID",
                "message": "Workflow durable wait state is invalid.",
            },
        )

    @staticmethod
    def _validated_source_kind(
        source_kind: str | None,
        *,
        run_type: str,
        strict: bool,
    ) -> WorkflowExecutionSourceKind | None:
        clean = str(source_kind or "").strip()
        if not clean:
            return None
        expected_run_types = {
            "workflow_classic": {"workflow"},
            "workflow_deployment": {"workflow"},
            "xpert_chat": {"xpert"},
            # A root App run uses xpert_app; a server-controlled child Xpert
            # keeps its xpert run type while inheriting the App control plane.
            "xpert_app": {"xpert_app", "xpert"},
            "expert_team_agency": {"expert_team"},
        }
        if clean not in expected_run_types or str(run_type) not in expected_run_types[clean]:
            if strict:
                raise WorkflowExecutionConflictError(
                    "Workflow execution source kind does not match its run type."
                )
            return None
        return clean  # type: ignore[return-value]

    def _persist_unlocked(self) -> None:
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": "workflow-executions-v1",
            "items": [asdict(item) for item in self._items.values()],
        }
        temporary = self.snapshot_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, self.snapshot_path)

    def _load(self) -> None:
        if not self.snapshot_path.exists():
            return
        try:
            payload = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
        except Exception:
            self._items = {}
            return
        raw_items = payload.get("items", []) if isinstance(payload, dict) else []
        if not isinstance(raw_items, list):
            return
        repaired = False
        for raw_value in raw_items:
            if not isinstance(raw_value, dict):
                repaired = True
                continue
            raw = dict(raw_value)
            try:
                raw["source_kind"] = self._validated_source_kind(
                    raw.get("source_kind"),
                    run_type=str(raw.get("run_type") or ""),
                    strict=False,
                )
                history = raw.get("previous_run_ids") or []
                if not isinstance(history, list):
                    history = []
                clean_history: list[str] = []
                for value in history[-_MAX_RUN_ID_HISTORY:]:
                    clean_value = str(value or "").strip()
                    if (
                        clean_value
                        and len(clean_value) <= 200
                        and clean_value != str(raw.get("run_id") or "")
                        and clean_value not in clean_history
                    ):
                        clean_history.append(clean_value)
                raw["previous_run_ids"] = clean_history
                if not raw.get("wait_kind") and raw.get("approval_id"):
                    raw["wait_kind"] = "approval"
                    raw["wait_id"] = raw.get("approval_id")
                item = WorkflowExecution(**raw)
                if (
                    item.wait_kind in _DUE_WAIT_KINDS
                    and item.status in {"waiting", "ready", "running"}
                    and not self._valid_due_wait_target(item)
                ):
                    self._invalidate_due_wait_unlocked(item)
                    repaired = True
                if item.status == "running" and item.source_kind != "expert_team_agency":
                    item.status = "ready" if item.wait_id is None else "waiting"
                    item.lease_owner = None
                    item.lease_token = None
                    item.lease_expires_at = 0.0
                self._items[item.task_id] = item
            except Exception:
                repaired = True
                continue
        if repaired:
            self._persist_unlocked()
