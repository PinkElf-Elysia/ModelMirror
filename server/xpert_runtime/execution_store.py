from __future__ import annotations

import json
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
    ) -> WorkflowExecution:
        with self._lock:
            item = self._require_unlocked(task_id)
            if item.run_type == "skill_evaluation":
                raise WorkflowExecutionConflictError(
                    "Skill evaluation cannot enter an interactive wait state."
                )
            item.status = "waiting"
            resolved_wait_id = str(wait_id or approval_id or "").strip()
            if not resolved_wait_id:
                raise WorkflowExecutionConflictError("A wait identifier is required.")
            item.wait_kind = str(wait_kind or "approval")
            item.wait_id = resolved_wait_id
            item.approval_id = (
                resolved_wait_id if item.wait_kind == "approval" else None
            )
            item.resume_at = float(resume_at) if resume_at is not None else None
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

    def list_due_timers(
        self,
        *,
        now: float | None = None,
        limit: int = 100,
    ) -> list[WorkflowExecution]:
        current = time.time() if now is None else float(now)
        with self._lock:
            items = [
                item
                for item in self._items.values()
                if item.status == "waiting"
                and item.wait_kind == "timer"
                and item.resume_at is not None
                and item.resume_at <= current
            ]
        items.sort(key=lambda item: (item.resume_at or 0.0, item.task_id))
        return items[: max(1, min(int(limit), 1000))]

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
            return item

    def refresh_lease(
        self,
        task_id: str,
        *,
        lease_token: str,
        lease_seconds: float = 60.0,
    ) -> WorkflowExecution:
        with self._lock:
            item = self._require_unlocked(task_id)
            if item.lease_token != lease_token:
                raise WorkflowExecutionConflictError("Workflow execution lease changed.")
            item.lease_expires_at = time.time() + max(1.0, float(lease_seconds))
            item.updated_at = time.time()
            self._persist_unlocked()
            return item

    def release_ready(self, task_id: str) -> WorkflowExecution:
        """Release a claimed continuation so a bounded coordinator can retry it."""
        with self._lock:
            item = self._require_unlocked(task_id)
            if item.status != "running" or not item.wait_id:
                raise WorkflowExecutionConflictError(
                    "Only a claimed waiting continuation can be deferred."
                )
            item.status = "ready"
            item.lease_owner = None
            item.lease_token = None
            item.lease_expires_at = 0.0
            item.updated_at = time.time()
            item.revision += 1
            self._persist_unlocked()
            return item

    def append_event(self, task_id: str, event: dict[str, Any]) -> WorkflowExecution:
        with self._lock:
            item = self._require_unlocked(task_id)
            self._append_event_unlocked(item, event)
            item.updated_at = time.time()
            self._persist_unlocked()
            return item

    def update_run_id(self, task_id: str, *, run_id: str) -> WorkflowExecution:
        with self._lock:
            item = self._require_unlocked(task_id)
            item.run_id = str(run_id)
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

    def complete(self, task_id: str, *, result: str) -> WorkflowExecution:
        return self._finish(task_id, status="completed", result=result)

    def fail(self, task_id: str, *, error: str) -> WorkflowExecution:
        return self._finish(task_id, status="failed", error=error)

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
    ) -> WorkflowExecution:
        with self._lock:
            item = self._require_unlocked(task_id)
            # Cancellation is terminal. A worker that finishes slightly later must
            # not publish a completed/failed state over the user's cancellation.
            if item.status == "cancelled" and status != "cancelled":
                return item
            item.status = status
            if item.run_type == "skill_evaluation" or self._is_skill_creator(item):
                item.result = None
                item.error = (
                    (
                        "skill_evaluation_failed"
                        if item.run_type == "skill_evaluation"
                        else "skill_creator_generation_failed"
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
        item.sequence += 1
        clean = self._safe_event(
            event,
            agency_execution=item.source_kind == "expert_team_agency",
        )
        if item.run_type == "skill_evaluation" or self._is_skill_creator(item):
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

    def _require_unlocked(self, task_id: str) -> WorkflowExecution:
        item = self._items.get(task_id)
        if item is None:
            raise WorkflowExecutionNotFoundError("Workflow execution not found.")
        return item

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
            for raw in payload.get("items", []):
                if not isinstance(raw, dict):
                    continue
                raw["source_kind"] = self._validated_source_kind(
                    raw.get("source_kind"),
                    run_type=str(raw.get("run_type") or ""),
                    strict=False,
                )
                if not raw.get("wait_kind") and raw.get("approval_id"):
                    raw["wait_kind"] = "approval"
                    raw["wait_id"] = raw.get("approval_id")
                item = WorkflowExecution(**raw)
                if item.status == "running" and item.source_kind != "expert_team_agency":
                    item.status = "ready" if item.wait_id is None else "waiting"
                    item.lease_owner = None
                    item.lease_token = None
                    item.lease_expires_at = 0.0
                self._items[item.task_id] = item
        except Exception:
            self._items = {}
