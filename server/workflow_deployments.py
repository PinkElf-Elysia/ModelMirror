from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator

try:
    from server.workflow_native.node_contracts import (
        canonical_checksum,
        workflow_node_contract_registry,
    )
    from server.workflow_native.schemas import NativeWorkflowDefinition
    from server.workflow_native.validate import node_kind, validate_workflow_graph
    from server.xpert_runtime.automation_store import (
        AutomationStore,
        AutomationTrigger,
        AutomationValidationError,
    )
except ModuleNotFoundError:
    from workflow_native.node_contracts import (
        canonical_checksum,
        workflow_node_contract_registry,
    )
    from workflow_native.schemas import NativeWorkflowDefinition
    from workflow_native.validate import node_kind, validate_workflow_graph
    from xpert_runtime.automation_store import (
        AutomationStore,
        AutomationTrigger,
        AutomationValidationError,
    )


WorkflowTriggerKind = Literal["manual", "schedule", "http"]
WorkflowTriggerExecutionStatus = Literal[
    "pending", "running", "waiting", "completed", "failed", "skipped", "cancelled"
]
ENTRY_NODE_KINDS = {"input", "scheduled_start", "http_event_entry"}
TERMINAL_EXECUTION_STATUSES = {"completed", "failed", "skipped", "cancelled"}
_SENSITIVE_KEY = re.compile(
    r"(?:^|[_-])(?:authorization|cookie|credential|password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|private[_-]?key)(?:$|[_-])",
    re.IGNORECASE,
)
_SENSITIVE_VALUE = re.compile(
    r"^(?:Bearer\s+\S+|sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,}|-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----)",
    re.IGNORECASE,
)


class WorkflowDeploymentError(Exception):
    """Base error for independent workflow publication state."""


class WorkflowDeploymentNotFoundError(WorkflowDeploymentError):
    pass


class WorkflowDeploymentConflictError(WorkflowDeploymentError):
    pass


class WorkflowDeploymentValidationError(WorkflowDeploymentError):
    def __init__(self, message: str, *, issues: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.issues = list(issues or [])


@dataclass(slots=True)
class WorkflowProject:
    project_id: str
    title: str
    draft: dict[str, Any]
    draft_revision: int = 1
    active_version: int | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class WorkflowVersion:
    project_id: str
    version: int
    workflow: dict[str, Any]
    node_contract_checksum: str
    definition_checksum: str
    trigger_kind: WorkflowTriggerKind
    entry_node_id: str
    published_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class WorkflowDeployment:
    deployment_id: str
    project_id: str
    version: int
    trigger_kind: WorkflowTriggerKind
    active: bool = False
    hook_id: str | None = None
    webhook_key_hash: str | None = None
    webhook_key_prefix: str | None = None
    next_run_at: float | None = None
    activated_at: float | None = None
    deactivated_at: float | None = None
    updated_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class WorkflowTriggerExecution:
    execution_id: str
    project_id: str
    version: int
    deployment_id: str
    trigger_kind: WorkflowTriggerKind
    occurrence_key: str
    status: WorkflowTriggerExecutionStatus = "pending"
    scheduled_at: float | None = None
    actual_started_at: float | None = None
    task_id: str | None = None
    run_id: str | None = None
    wait_kind: str | None = None
    wait_id: str | None = None
    resume_at: float | None = None
    result_summary: str | None = None
    error_summary: str | None = None
    webhook_reply: dict[str, Any] | None = None
    idempotency_hash: str | None = None
    trigger_summary: dict[str, Any] = field(default_factory=dict)
    lease_owner: str | None = None
    lease_token: str | None = None
    lease_expires_at: float = 0.0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    completed_at: float | None = None


class WorkflowDeploymentStore:
    """Atomic single-instance store for workflow drafts, releases and safe summaries."""

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        package_dir = Path(__file__).resolve().parent
        self.storage_dir = Path(
            storage_dir
            or os.getenv("AGENT_TASK_STORAGE_DIR", "").strip()
            or package_dir / "xpert_runtime" / "storage"
        )
        self.snapshot_path = self.storage_dir / "workflow_deployments.json"
        self._lock = threading.RLock()
        self._projects: dict[str, WorkflowProject] = {}
        self._versions: dict[tuple[str, int], WorkflowVersion] = {}
        self._deployments: dict[str, WorkflowDeployment] = {}
        self._executions: dict[str, WorkflowTriggerExecution] = {}
        self._load()

    def create_project(self, workflow: dict[str, Any]) -> WorkflowProject:
        normalized = self._normalize_workflow(workflow)
        project_id = f"wf_{uuid.uuid4().hex}"
        normalized["id"] = project_id
        now = time.time()
        item = WorkflowProject(
            project_id=project_id,
            title=str(normalized.get("title") or "未命名工作流")[:120],
            draft=normalized,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._projects[project_id] = item
            self._persist_unlocked()
        return item

    def get_project(self, project_id: str) -> WorkflowProject | None:
        with self._lock:
            return self._projects.get(project_id)

    def require_project(self, project_id: str) -> WorkflowProject:
        item = self.get_project(project_id)
        if item is None:
            raise WorkflowDeploymentNotFoundError("Workflow project not found.")
        return item

    def save_draft(
        self,
        project_id: str,
        *,
        expected_revision: int,
        workflow: dict[str, Any],
    ) -> WorkflowProject:
        normalized = self._normalize_workflow(workflow)
        normalized["id"] = project_id
        with self._lock:
            item = self._require_project_unlocked(project_id)
            if item.draft_revision != int(expected_revision):
                raise WorkflowDeploymentConflictError(
                    f"Draft revision changed; current revision is {item.draft_revision}."
                )
            item.draft = normalized
            item.title = str(normalized.get("title") or item.title)[:120]
            item.draft_revision += 1
            item.updated_at = time.time()
            self._persist_unlocked()
            return item

    def publish(self, project_id: str) -> WorkflowVersion:
        with self._lock:
            project = self._require_project_unlocked(project_id)
            trigger_kind, entry_node_id = validate_publishable_workflow(project.draft)
            versions = [number for key, number in self._versions if key == project_id]
            next_version = max(versions or [0]) + 1
            snapshot = json.loads(json.dumps(project.draft, ensure_ascii=False))
            item = WorkflowVersion(
                project_id=project_id,
                version=next_version,
                workflow=snapshot,
                node_contract_checksum=workflow_node_contract_registry.checksum,
                definition_checksum=canonical_checksum(snapshot),
                trigger_kind=trigger_kind,
                entry_node_id=entry_node_id,
            )
            self._versions[(project_id, next_version)] = item
            project.updated_at = time.time()
            self._persist_unlocked()
            return item

    def list_versions(self, project_id: str) -> list[WorkflowVersion]:
        self.require_project(project_id)
        with self._lock:
            items = [item for (key, _), item in self._versions.items() if key == project_id]
        return sorted(items, key=lambda item: item.version, reverse=True)

    def require_version(self, project_id: str, version: int) -> WorkflowVersion:
        with self._lock:
            item = self._versions.get((project_id, int(version)))
        if item is None:
            raise WorkflowDeploymentNotFoundError("Workflow version not found.")
        return item

    def activate(
        self,
        project_id: str,
        version: int,
        *,
        webhooks_enabled: bool,
        now: float | None = None,
    ) -> tuple[WorkflowDeployment, str | None]:
        current = time.time() if now is None else float(now)
        with self._lock:
            project = self._require_project_unlocked(project_id)
            release = self._require_version_unlocked(project_id, version)
            if release.node_contract_checksum != workflow_node_contract_registry.checksum:
                raise WorkflowDeploymentConflictError(
                    "NodeContract checksum changed; republish before activation."
                )
            if release.trigger_kind == "http" and not webhooks_enabled:
                raise WorkflowDeploymentConflictError("Workflow webhooks are disabled.")
            for deployment in self._deployments.values():
                if deployment.project_id == project_id and deployment.active:
                    deployment.active = False
                    deployment.deactivated_at = current
                    deployment.next_run_at = None
                    deployment.updated_at = current
            deployment_id = f"wfd_{project_id[3:]}_{version}"
            deployment = self._deployments.get(deployment_id)
            if deployment is None:
                deployment = WorkflowDeployment(
                    deployment_id=deployment_id,
                    project_id=project_id,
                    version=version,
                    trigger_kind=release.trigger_kind,
                    hook_id=(f"hook_{secrets.token_hex(16)}" if release.trigger_kind == "http" else None),
                )
                self._deployments[deployment_id] = deployment
            plaintext_key: str | None = None
            if release.trigger_kind == "http":
                plaintext_key = self._set_new_webhook_key_unlocked(deployment)
            deployment.active = True
            deployment.activated_at = current
            deployment.deactivated_at = None
            deployment.updated_at = current
            deployment.next_run_at = (
                self._initial_schedule_time(release, current)
                if release.trigger_kind == "schedule"
                else None
            )
            project.active_version = version
            project.updated_at = current
            self._persist_unlocked()
            return deployment, plaintext_key

    def deactivate(self, project_id: str, version: int) -> WorkflowDeployment:
        with self._lock:
            project = self._require_project_unlocked(project_id)
            deployment = self._require_deployment_for_version_unlocked(project_id, version)
            deployment.active = False
            deployment.next_run_at = None
            deployment.deactivated_at = time.time()
            deployment.updated_at = deployment.deactivated_at
            if project.active_version == version:
                project.active_version = None
                project.updated_at = deployment.updated_at
            self._persist_unlocked()
            return deployment

    def rotate_webhook_key(
        self,
        project_id: str,
        version: int,
        *,
        webhooks_enabled: bool,
    ) -> tuple[WorkflowDeployment, str]:
        if not webhooks_enabled:
            raise WorkflowDeploymentConflictError("Workflow webhooks are disabled.")
        with self._lock:
            deployment = self._require_deployment_for_version_unlocked(project_id, version)
            if deployment.trigger_kind != "http":
                raise WorkflowDeploymentConflictError("Only HTTP deployments have webhook keys.")
            plaintext = self._set_new_webhook_key_unlocked(deployment)
            deployment.updated_at = time.time()
            self._persist_unlocked()
            return deployment, plaintext

    def authenticate_hook(self, hook_id: str, plaintext_key: str) -> WorkflowDeployment:
        supplied_hash = hashlib.sha256(str(plaintext_key).encode("utf-8")).hexdigest()
        with self._lock:
            deployment = next(
                (item for item in self._deployments.values() if item.hook_id == hook_id),
                None,
            )
            if (
                deployment is None
                or not deployment.active
                or deployment.trigger_kind != "http"
                or not deployment.webhook_key_hash
                or not secrets.compare_digest(deployment.webhook_key_hash, supplied_hash)
            ):
                raise WorkflowDeploymentNotFoundError("Workflow hook not found.")
            return deployment

    def create_webhook_execution(
        self,
        deployment: WorkflowDeployment,
        *,
        idempotency_key: str,
        content_type: str,
        body_size: int,
        body_sha256: str,
        now: float | None = None,
    ) -> tuple[WorkflowTriggerExecution, bool]:
        clean_key = str(idempotency_key).strip()
        if not clean_key or len(clean_key) > 200:
            raise WorkflowDeploymentValidationError(
                "Idempotency-Key must contain 1 to 200 characters."
            )
        idempotency_hash = hashlib.sha256(
            f"{deployment.hook_id}:{clean_key}".encode("utf-8")
        ).hexdigest()
        current = time.time() if now is None else float(now)
        with self._lock:
            existing = next(
                (
                    item for item in self._executions.values()
                    if item.deployment_id == deployment.deployment_id
                    and item.idempotency_hash == idempotency_hash
                ),
                None,
            )
            if existing is not None:
                return existing, False
            item = WorkflowTriggerExecution(
                execution_id=f"wfx_{uuid.uuid4().hex}",
                project_id=deployment.project_id,
                version=deployment.version,
                deployment_id=deployment.deployment_id,
                trigger_kind="http",
                occurrence_key=f"http:{idempotency_hash[:32]}",
                scheduled_at=current,
                idempotency_hash=idempotency_hash,
                trigger_summary={
                    "content_type": str(content_type)[:100],
                    "body_size": max(0, int(body_size)),
                    "body_sha256": str(body_sha256)[:64],
                },
                created_at=current,
                updated_at=current,
            )
            self._executions[item.execution_id] = item
            self._persist_unlocked()
            return item, True

    def materialize_due_schedules(
        self,
        *,
        now: float | None = None,
    ) -> list[WorkflowTriggerExecution]:
        current = time.time() if now is None else float(now)
        created: list[WorkflowTriggerExecution] = []
        with self._lock:
            for deployment in sorted(self._deployments.values(), key=lambda item: item.deployment_id):
                if (
                    not deployment.active
                    or deployment.trigger_kind != "schedule"
                    or deployment.next_run_at is None
                    or deployment.next_run_at > current
                ):
                    continue
                release = self._require_version_unlocked(deployment.project_id, deployment.version)
                trigger = self._schedule_trigger(release)
                scheduled_at = deployment.next_run_at
                cursor = scheduled_at
                while cursor is not None and cursor <= current:
                    scheduled_at = cursor
                    cursor = AutomationStore.next_occurrence(trigger, cursor)
                deployment.next_run_at = cursor
                occurrence_key = self._occurrence_key(deployment.deployment_id, scheduled_at)
                if any(item.occurrence_key == occurrence_key for item in self._executions.values()):
                    continue
                active_execution = any(
                    item.deployment_id == deployment.deployment_id
                    and item.status not in TERMINAL_EXECUTION_STATUSES
                    for item in self._executions.values()
                )
                summary = {
                    "scheduled_time": scheduled_at,
                    "actual_start_time": current,
                    "timezone": trigger.timezone,
                    "occurrence_key": occurrence_key,
                }
                item = WorkflowTriggerExecution(
                    execution_id=f"wfx_{uuid.uuid4().hex}",
                    project_id=deployment.project_id,
                    version=deployment.version,
                    deployment_id=deployment.deployment_id,
                    trigger_kind="schedule",
                    occurrence_key=occurrence_key,
                    status="skipped" if active_execution else "pending",
                    scheduled_at=scheduled_at,
                    trigger_summary=summary,
                    error_summary="Previous occurrence is still active." if active_execution else None,
                    completed_at=current if active_execution else None,
                    created_at=current,
                    updated_at=current,
                )
                self._executions[item.execution_id] = item
                created.append(item)
                if trigger.type == "once":
                    deployment.active = False
                    deployment.next_run_at = None
                    project = self._require_project_unlocked(deployment.project_id)
                    if project.active_version == deployment.version:
                        project.active_version = None
                deployment.updated_at = current
            self._persist_unlocked()
        return created

    def claimable_executions(
        self,
        *,
        now: float | None = None,
        limit: int = 20,
    ) -> list[WorkflowTriggerExecution]:
        current = time.time() if now is None else float(now)
        with self._lock:
            items = [
                item for item in self._executions.values()
                if item.status == "pending"
                or (item.status == "running" and item.lease_expires_at <= current)
            ]
        items.sort(key=lambda item: (item.scheduled_at or item.created_at, item.execution_id))
        return items[: max(1, min(int(limit), 100))]

    def claim_execution(
        self,
        execution_id: str,
        *,
        worker_id: str,
        lease_seconds: float = 60.0,
        now: float | None = None,
    ) -> WorkflowTriggerExecution:
        current = time.time() if now is None else float(now)
        with self._lock:
            item = self._require_execution_unlocked(execution_id)
            if item.status == "running" and item.lease_expires_at > current:
                raise WorkflowDeploymentConflictError("Trigger execution is already leased.")
            if item.status not in {"pending", "running"}:
                raise WorkflowDeploymentConflictError(
                    f"Trigger execution cannot be claimed from {item.status}."
                )
            if item.trigger_kind == "http" and item.actual_started_at is not None:
                item.status = "failed"
                item.error_summary = "HTTP request body is not persisted and cannot be replayed."
                item.completed_at = current
                item.updated_at = current
                self._persist_unlocked()
                raise WorkflowDeploymentConflictError(item.error_summary)
            item.status = "running"
            item.actual_started_at = item.actual_started_at or current
            item.lease_owner = str(worker_id)
            item.lease_token = uuid.uuid4().hex
            item.lease_expires_at = current + max(5.0, float(lease_seconds))
            item.updated_at = current
            self._persist_unlocked()
            return item

    def mark_execution_waiting(
        self,
        execution_id: str,
        *,
        task_id: str,
        run_id: str,
        wait_kind: str,
        wait_id: str,
        resume_at: float | None = None,
    ) -> WorkflowTriggerExecution:
        with self._lock:
            item = self._require_execution_unlocked(execution_id)
            item.status = "waiting"
            item.task_id = str(task_id)
            item.run_id = str(run_id)
            item.wait_kind = str(wait_kind)
            item.wait_id = str(wait_id)
            item.resume_at = float(resume_at) if resume_at is not None else None
            self._clear_lease(item)
            item.updated_at = time.time()
            self._persist_unlocked()
            return item

    def complete_execution(
        self,
        execution_id: str,
        *,
        task_id: str | None = None,
        run_id: str | None = None,
        result: str = "",
        webhook_reply: dict[str, Any] | None = None,
    ) -> WorkflowTriggerExecution:
        with self._lock:
            item = self._require_execution_unlocked(execution_id)
            item.status = "completed"
            item.task_id = str(task_id or item.task_id or "") or None
            item.run_id = str(run_id or item.run_id or "") or None
            item.result_summary = _safe_result_summary(result)
            item.error_summary = None
            item.webhook_reply = _safe_webhook_reply(webhook_reply)
            item.wait_kind = None
            item.wait_id = None
            item.resume_at = None
            item.completed_at = time.time()
            item.updated_at = item.completed_at
            self._clear_lease(item)
            self._persist_unlocked()
            return item

    def fail_execution(self, execution_id: str, *, error: str) -> WorkflowTriggerExecution:
        with self._lock:
            item = self._require_execution_unlocked(execution_id)
            item.status = "failed"
            item.error_summary = (
                "Workflow hook execution failed."
                if item.trigger_kind == "http"
                else _safe_error_summary(error)
            )
            item.completed_at = time.time()
            item.updated_at = item.completed_at
            self._clear_lease(item)
            self._persist_unlocked()
            return item

    def get_execution(self, execution_id: str) -> WorkflowTriggerExecution | None:
        with self._lock:
            return self._executions.get(execution_id)

    def list_executions(
        self,
        project_id: str,
        *,
        limit: int = 100,
    ) -> list[WorkflowTriggerExecution]:
        self.require_project(project_id)
        with self._lock:
            items = [item for item in self._executions.values() if item.project_id == project_id]
        items.sort(key=lambda item: (item.created_at, item.execution_id), reverse=True)
        return items[: max(1, min(int(limit), 1000))]

    def active_deployment(self, project_id: str) -> WorkflowDeployment | None:
        with self._lock:
            return next(
                (item for item in self._deployments.values() if item.project_id == project_id and item.active),
                None,
            )

    @staticmethod
    def serialize_project(item: WorkflowProject) -> dict[str, Any]:
        return asdict(item)

    @staticmethod
    def serialize_version(item: WorkflowVersion, *, include_workflow: bool = False) -> dict[str, Any]:
        payload = asdict(item)
        if not include_workflow:
            payload.pop("workflow", None)
        return payload

    @staticmethod
    def serialize_deployment(item: WorkflowDeployment) -> dict[str, Any]:
        payload = asdict(item)
        payload.pop("webhook_key_hash", None)
        return payload

    @staticmethod
    def serialize_execution(item: WorkflowTriggerExecution) -> dict[str, Any]:
        payload = asdict(item)
        payload.pop("idempotency_hash", None)
        payload.pop("lease_owner", None)
        payload.pop("lease_token", None)
        payload.pop("lease_expires_at", None)
        return payload

    def _normalize_workflow(self, workflow: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(workflow, dict):
            raise WorkflowDeploymentValidationError("Workflow must be an object.")
        payload = json.loads(json.dumps(workflow, ensure_ascii=False))
        payload.setdefault("id", "draft")
        payload.setdefault("title", "未命名工作流")
        payload.setdefault("nodes", [])
        payload.setdefault("edges", [])
        payload.setdefault("variables", [])
        try:
            NativeWorkflowDefinition.model_validate(
                {
                    **payload,
                    "version": str(payload.get("version") or "classic-draft"),
                    "source": "classic",
                }
            )
        except Exception as exc:
            raise WorkflowDeploymentValidationError(
                f"Workflow draft is invalid: {str(exc)[:500]}"
            ) from exc
        return payload

    def _initial_schedule_time(self, release: WorkflowVersion, now: float) -> float | None:
        trigger = self._schedule_trigger(release)
        if trigger.type == "once":
            return trigger.once_at
        return AutomationStore.next_occurrence(trigger, now)

    @staticmethod
    def _schedule_trigger(release: WorkflowVersion) -> AutomationTrigger:
        node = next(
            node for node in release.workflow.get("nodes", [])
            if str(node.get("type") or node.get("data", {}).get("kind") or "")
            .strip()
            .replace("-", "_") == "scheduled_start"
        )
        data = dict(node.get("data") or {})
        schedule_type = str(data.get("scheduleType") or "")
        raw: dict[str, Any] = {
            "type": schedule_type,
            "timezone": str(data.get("timezone") or "UTC"),
        }
        if schedule_type == "once":
            parsed_once = datetime.fromisoformat(
                str(data.get("onceAt") or "").replace("Z", "+00:00")
            )
            if parsed_once.tzinfo is None:
                parsed_once = parsed_once.replace(
                    tzinfo=ZoneInfo(str(data.get("timezone") or "UTC"))
                )
            raw["once_at"] = parsed_once.timestamp()
        elif schedule_type == "interval":
            raw["interval_seconds"] = data.get("intervalSeconds")
        else:
            raw["cron"] = data.get("cronExpression")
        try:
            return AutomationStore.validate_trigger(raw)
        except (AutomationValidationError, ValueError) as exc:
            raise WorkflowDeploymentValidationError(str(exc)) from exc

    @staticmethod
    def _occurrence_key(deployment_id: str, scheduled_at: float) -> str:
        raw = f"{deployment_id}:{scheduled_at:.6f}".encode("utf-8")
        return f"occ_{hashlib.sha256(raw).hexdigest()[:32]}"

    @staticmethod
    def _set_new_webhook_key_unlocked(deployment: WorkflowDeployment) -> str:
        plaintext = secrets.token_urlsafe(32)
        deployment.webhook_key_hash = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
        deployment.webhook_key_prefix = plaintext[:8]
        return plaintext

    def _require_project_unlocked(self, project_id: str) -> WorkflowProject:
        item = self._projects.get(project_id)
        if item is None:
            raise WorkflowDeploymentNotFoundError("Workflow project not found.")
        return item

    def _require_version_unlocked(self, project_id: str, version: int) -> WorkflowVersion:
        item = self._versions.get((project_id, int(version)))
        if item is None:
            raise WorkflowDeploymentNotFoundError("Workflow version not found.")
        return item

    def _require_deployment_for_version_unlocked(
        self,
        project_id: str,
        version: int,
    ) -> WorkflowDeployment:
        item = next(
            (
                deployment for deployment in self._deployments.values()
                if deployment.project_id == project_id and deployment.version == int(version)
            ),
            None,
        )
        if item is None:
            raise WorkflowDeploymentNotFoundError("Workflow deployment not found.")
        return item

    def _require_execution_unlocked(self, execution_id: str) -> WorkflowTriggerExecution:
        item = self._executions.get(execution_id)
        if item is None:
            raise WorkflowDeploymentNotFoundError("Workflow trigger execution not found.")
        return item

    @staticmethod
    def _clear_lease(item: WorkflowTriggerExecution) -> None:
        item.lease_owner = None
        item.lease_token = None
        item.lease_expires_at = 0.0

    def _persist_unlocked(self) -> None:
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": "workflow-deployments-v1",
            "projects": [asdict(item) for item in self._projects.values()],
            "versions": [asdict(item) for item in self._versions.values()],
            "deployments": [asdict(item) for item in self._deployments.values()],
            "executions": [asdict(item) for item in self._executions.values()],
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
            for raw in payload.get("projects", []):
                item = WorkflowProject(**raw)
                self._projects[item.project_id] = item
            for raw in payload.get("versions", []):
                item = WorkflowVersion(**raw)
                self._versions[(item.project_id, item.version)] = item
            for raw in payload.get("deployments", []):
                item = WorkflowDeployment(**raw)
                self._deployments[item.deployment_id] = item
            for raw in payload.get("executions", []):
                item = WorkflowTriggerExecution(**raw)
                if item.status == "running":
                    if item.trigger_kind == "http":
                        item.status = "failed"
                        item.error_summary = "HTTP request body was not persisted; execution was not replayed."
                        item.completed_at = time.time()
                    else:
                        item.status = "pending"
                    self._clear_lease(item)
                self._executions[item.execution_id] = item
        except Exception as exc:
            raise WorkflowDeploymentValidationError(
                "Workflow deployment snapshot is invalid; refusing to start with empty state."
            ) from exc


def validate_publishable_workflow(workflow: dict[str, Any]) -> tuple[WorkflowTriggerKind, str]:
    try:
        definition = NativeWorkflowDefinition.model_validate(
            {
                **workflow,
                "version": str(workflow.get("version") or "classic-draft"),
                "source": "classic",
            }
        )
    except Exception as exc:
        raise WorkflowDeploymentValidationError(
            f"Workflow draft is invalid: {str(exc)[:500]}"
        ) from exc
    result = validate_workflow_graph(definition)
    if not result.valid:
        raise WorkflowDeploymentValidationError(
            "Workflow static validation failed.",
            issues=[issue.model_dump(mode="json") for issue in result.issues],
        )
    entries = [node for node in definition.nodes if node_kind(node) in ENTRY_NODE_KINDS]
    if len(entries) != 1:
        raise WorkflowDeploymentValidationError(
            "A published workflow must contain exactly one entry node."
        )
    entry = entries[0]
    entry_kind = node_kind(entry)
    trigger_kind: WorkflowTriggerKind = (
        "schedule" if entry_kind == "scheduled_start"
        else "http" if entry_kind == "http_event_entry"
        else "manual"
    )
    if entry.id in {edge.target for edge in definition.edges}:
        raise WorkflowDeploymentValidationError("The entry node cannot have incoming edges.")
    for node in definition.nodes:
        kind = node_kind(node)
        contract = workflow_node_contract_registry.require(kind)
        if contract.contract_status != "complete":
            raise WorkflowDeploymentValidationError(
                f"Node '{node.id}' does not have a complete NodeContract."
            )
        errors = sorted(
            Draft202012Validator(contract.config_schema).iter_errors(node.data),
            key=lambda error: list(error.path),
        )
        if errors:
            raise WorkflowDeploymentValidationError(
                f"Node '{node.id}' does not satisfy its NodeContract: {errors[0].message}"
            )
        if trigger_kind == "http" and kind == "runtime_middleware":
            raise WorkflowDeploymentValidationError(
                "HTTP deployments cannot contain runtime middleware in R1."
            )
    sensitive_path = _find_sensitive_value(workflow)
    if sensitive_path:
        raise WorkflowDeploymentValidationError(
            f"Published workflows cannot contain plaintext credentials ({sensitive_path})."
        )
    reply_nodes = [node for node in definition.nodes if node_kind(node) == "http_event_reply"]
    if reply_nodes and trigger_kind != "http":
        raise WorkflowDeploymentValidationError(
            "HTTP event reply nodes require an HTTP event entry."
        )
    if any(node.id in {edge.source for edge in definition.edges} for node in reply_nodes):
        raise WorkflowDeploymentValidationError("HTTP event reply must be a terminal node.")
    if reply_nodes:
        parents: dict[str, set[str]] = {}
        for edge in definition.edges:
            parents.setdefault(edge.target, set()).add(edge.source)
        nodes_by_id = {node.id: node for node in definition.nodes}
        for reply in reply_nodes:
            stack = list(parents.get(reply.id, set()))
            visited: set[str] = set()
            while stack:
                current = stack.pop()
                if current in visited:
                    continue
                visited.add(current)
                if node_kind(nodes_by_id[current]) == "suspend_wait":
                    raise WorkflowDeploymentValidationError(
                        "HTTP event reply cannot have a suspend wait upstream."
                    )
                stack.extend(parents.get(current, set()))
    if trigger_kind == "schedule":
        WorkflowDeploymentStore._schedule_trigger(
            WorkflowVersion(
                project_id="validation",
                version=1,
                workflow=workflow,
                node_contract_checksum="",
                definition_checksum="",
                trigger_kind="schedule",
                entry_node_id=entry.id,
            )
        )
    return trigger_kind, entry.id


def _find_sensitive_value(value: Any, *, path: str = "$") -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{path}.{key}"
            if _SENSITIVE_KEY.search(str(key)) and item not in {None, "", False}:
                return child_path
            found = _find_sensitive_value(item, path=child_path)
            if found:
                return found
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found = _find_sensitive_value(item, path=f"{path}[{index}]")
            if found:
                return found
    elif isinstance(value, str) and _SENSITIVE_VALUE.match(value.strip()):
        return path
    return None


def _safe_webhook_reply(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    try:
        status_code = int(value.get("status_code") or 200)
    except (TypeError, ValueError):
        status_code = 500
    content_type = str(value.get("content_type") or "text/plain")
    if content_type not in {"text/plain", "application/json"}:
        content_type = "text/plain"
    body = value.get("body")
    if content_type == "application/json":
        encoded = json.dumps(body, ensure_ascii=False)
        if len(encoded.encode("utf-8")) > 1_048_576:
            body = {"error": "response_too_large"}
            status_code = 500
    else:
        body = str(body or "")[:1_048_576]
    return {
        "status_code": max(200, min(status_code, 599)),
        "content_type": content_type,
        "body": body,
    }


def _safe_result_summary(value: str) -> str:
    encoded = str(value or "").encode("utf-8")
    return (
        f"completed output_bytes={len(encoded)} "
        f"sha256={hashlib.sha256(encoded).hexdigest()}"
    )


def _safe_error_summary(value: str) -> str:
    text = str(value or "Workflow trigger execution failed.")
    text = re.sub(
        r"(?i)\b(authorization|cookie|password|secret|api[_-]?key|access[_-]?token)"
        r"\s*[:=]\s*[^\s,;]+",
        r"\1=[redacted]",
        text,
    )
    text = re.sub(
        r"(?i)\b(?:Bearer\s+\S+|sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,})",
        "[redacted]",
        text,
    )
    return text[:1_000]
