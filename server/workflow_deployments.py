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
from typing import Any, Callable, Literal
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator

try:
    from server.workflow_native.node_contracts import (
        canonical_checksum,
        workflow_node_contract_registry,
    )
    from server.workflow_native.schemas import (
        NativeWorkflowDefinition,
        workflow_variable_value_matches_type,
    )
    from server.workflow_native.validate import node_kind, validate_workflow_graph
    from server.workflow_native.secure_http import (
        WorkflowHttpRequestError,
        is_http_request_v2,
        validate_http_request_credential,
    )
    from server.workflow_native.r20_nodes import (
        WorkflowR20NodeError,
        contract_version as r20_contract_version,
        validate_mcp_tool_v2_config,
    )
    from server.workflow_native.r23_iteration import is_workflow_map
    from server.workflow_native.content_parser import document_extractor_uses_file_asset
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
    from workflow_native.schemas import (
        NativeWorkflowDefinition,
        workflow_variable_value_matches_type,
    )
    from workflow_native.validate import node_kind, validate_workflow_graph
    from workflow_native.secure_http import (
        WorkflowHttpRequestError,
        is_http_request_v2,
        validate_http_request_credential,
    )
    from workflow_native.r20_nodes import (
        WorkflowR20NodeError,
        contract_version as r20_contract_version,
        validate_mcp_tool_v2_config,
    )
    from workflow_native.r23_iteration import is_workflow_map
    from workflow_native.content_parser import document_extractor_uses_file_asset
    from xpert_runtime.automation_store import (
        AutomationStore,
        AutomationTrigger,
        AutomationValidationError,
    )


WorkflowTriggerKind = Literal["manual", "schedule", "http", "failure", "call"]
WorkflowTriggerExecutionStatus = Literal[
    "pending", "running", "waiting", "completed", "failed", "skipped", "cancelled"
]
ENTRY_NODE_KINDS = {
    "input",
    "scheduled_start",
    "http_event_entry",
    "failure_event_entry",
    "workflow_call_entry",
}
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
    parent_execution_id: str | None = None
    root_execution_id: str | None = None
    source_execution_id: str | None = None
    call_node_id: str | None = None
    batch_index: int | None = None
    test_mode: bool = False
    lease_owner: str | None = None
    lease_token: str | None = None
    lease_expires_at: float = 0.0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    completed_at: float | None = None


@dataclass(slots=True)
class WorkflowFailureSubscription:
    source_project_id: str
    handler_project_id: str
    handler_version: int
    handler_deployment_id: str
    activated_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class WorkflowSubworkflowRelation:
    occurrence_key: str
    parent_execution_id: str
    child_execution_id: str
    root_execution_id: str
    call_node_id: str
    depth: int
    task_id: str
    batch_occurrence_key: str | None = None
    batch_index: int | None = None
    input_digest: str | None = None
    created_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class WorkflowSubworkflowBatch:
    occurrence_key: str
    parent_execution_id: str
    root_execution_id: str
    call_node_id: str
    target_project_id: str
    target_version: int
    item_count: int
    input_digest: str
    created_at: float = field(default_factory=time.time)


class WorkflowDeploymentStore:
    """Atomic single-instance store for workflow drafts, releases and safe summaries."""

    def __init__(
        self,
        storage_dir: str | Path | None = None,
        *,
        credential_validator: Callable[[str], Any] | None = None,
        mcp_tool_validator: Callable[[dict[str, Any]], Any] | None = None,
        xpert_target_validator: Callable[[str, int], Any] | None = None,
    ) -> None:
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
        self._failure_subscriptions: dict[str, WorkflowFailureSubscription] = {}
        self._subworkflow_relations: dict[str, WorkflowSubworkflowRelation] = {}
        self._subworkflow_batches: dict[str, WorkflowSubworkflowBatch] = {}
        self._credential_validator = credential_validator
        self._mcp_tool_validator = mcp_tool_validator
        self._xpert_target_validator = xpert_target_validator
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

    def list_projects(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        active_only: bool = False,
        trigger_kind: WorkflowTriggerKind | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        clean_limit = max(1, min(int(limit), 100))
        clean_offset = max(0, int(offset))
        with self._lock:
            summaries: list[dict[str, Any]] = []
            for project in self._projects.values():
                active = next(
                    (
                        item
                        for item in self._deployments.values()
                        if item.project_id == project.project_id and item.active
                    ),
                    None,
                )
                if active_only and active is None:
                    continue
                if trigger_kind is not None and (
                    active is None or active.trigger_kind != trigger_kind
                ):
                    continue
                summaries.append(
                    {
                        "project_id": project.project_id,
                        "title": project.title,
                        "active_version": active.version if active else None,
                        "active_trigger_kind": active.trigger_kind if active else None,
                        "updated_at": project.updated_at,
                    }
                )
        summaries.sort(
            key=lambda item: (float(item["updated_at"]), str(item["project_id"])),
            reverse=True,
        )
        return summaries[clean_offset : clean_offset + clean_limit], len(summaries)

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
            trigger_kind, entry_node_id = validate_publishable_workflow(
                project.draft,
                credential_validator=self._credential_validator,
                mcp_tool_validator=self._mcp_tool_validator,
                xpert_target_validator=self._xpert_target_validator,
            )
            if trigger_kind == "failure":
                self._validate_failure_sources_unlocked(
                    project_id,
                    self._failure_source_project_ids(project.draft, entry_node_id),
                )
            self._validate_subworkflow_targets_unlocked(
                project_id,
                project.draft,
            )
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
        failure_triggers_enabled: bool = False,
        subworkflows_enabled: bool = False,
        http_requests_enabled: bool = False,
        workflow_file_assets_enabled: bool = False,
        file_output_assets_enabled: bool = False,
        mcp_tools_enabled: bool = False,
        handoff_executor_enabled: bool = False,
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
            if release.trigger_kind == "failure" and not failure_triggers_enabled:
                raise WorkflowDeploymentConflictError(
                    "Workflow failure triggers are disabled."
                )
            http_v2_nodes = [
                node
                for node in release.workflow.get("nodes", [])
                if isinstance(node, dict)
                and _raw_node_kind(node) == "http_request"
                and is_http_request_v2(dict(node.get("data") or {}))
            ]
            if http_v2_nodes and not http_requests_enabled:
                raise WorkflowDeploymentConflictError(
                    "Secure workflow HTTP requests are disabled."
                )
            for node in http_v2_nodes:
                try:
                    validate_http_request_credential(
                        dict(node.get("data") or {}),
                        self._credential_validator,
                    )
                except WorkflowHttpRequestError as exc:
                    raise WorkflowDeploymentConflictError(exc.safe_message) from exc
            nodes = [
                node
                for node in release.workflow.get("nodes", [])
                if isinstance(node, dict)
            ]
            mcp_v2_nodes = [
                node
                for node in nodes
                if _raw_node_kind(node) == "mcp_tool"
                and r20_contract_version(dict(node.get("data") or {})) == 2
            ]
            if mcp_v2_nodes and not mcp_tools_enabled:
                raise WorkflowDeploymentConflictError(
                    "Workflow MCP tools are disabled."
                )
            for node in mcp_v2_nodes:
                if self._mcp_tool_validator is None:
                    raise WorkflowDeploymentConflictError(
                        "Workflow MCP tool registry validation is unavailable."
                    )
                try:
                    self._mcp_tool_validator(dict(node.get("data") or {}))
                except WorkflowR20NodeError as exc:
                    raise WorkflowDeploymentConflictError(exc.safe_message) from exc
            collaboration_nodes = [
                node
                for node in nodes
                if _raw_node_kind(node) in {"agent_handoff", "handoff_router"}
                and r20_contract_version(dict(node.get("data") or {})) == 2
            ]
            automatic_handoffs = [
                node
                for node in collaboration_nodes
                if str(dict(node.get("data") or {}).get("targetMode") or "")
                == "xpert"
            ]
            if automatic_handoffs and not handoff_executor_enabled:
                raise WorkflowDeploymentConflictError(
                    "Automatic Xpert Handoffs are disabled."
                )
            for node in automatic_handoffs:
                data = dict(node.get("data") or {})
                if self._xpert_target_validator is None:
                    raise WorkflowDeploymentConflictError(
                        "Xpert Handoff target validation is unavailable."
                    )
                try:
                    self._xpert_target_validator(
                        str(data.get("targetXpertId") or ""),
                        int(data.get("targetVersion") or 0),
                    )
                except Exception as exc:
                    raise WorkflowDeploymentConflictError(
                        "The fixed Xpert Handoff target is unavailable."
                    ) from exc
            if any(_raw_node_kind(node) == "file_output" for node in nodes):
                if not file_output_assets_enabled:
                    raise WorkflowDeploymentConflictError(
                        "Workflow file output is disabled."
                    )
            if any(
                _raw_node_kind(node) == "document_extractor"
                and document_extractor_uses_file_asset(dict(node.get("data") or {}))
                for node in nodes
            ):
                if not workflow_file_assets_enabled:
                    raise WorkflowDeploymentConflictError(
                        "Workflow file assets are disabled."
                    )
            has_workflow_calls = any(
                _raw_node_kind(node) == "invoke_workflow"
                or (
                    _raw_node_kind(node) == "iteration"
                    and is_workflow_map(dict(node.get("data") or {}))
                )
                for node in release.workflow.get("nodes", [])
                if isinstance(node, dict)
            )
            if (
                release.trigger_kind == "call" or has_workflow_calls
            ) and not subworkflows_enabled:
                raise WorkflowDeploymentConflictError(
                    "Workflow subworkflows are disabled."
                )
            self._validate_subworkflow_targets_unlocked(
                project_id,
                release.workflow,
            )
            failure_sources: list[str] = []
            if release.trigger_kind == "failure":
                failure_sources = self._failure_source_project_ids(
                    release.workflow,
                    release.entry_node_id,
                )
                self._validate_failure_sources_unlocked(project_id, failure_sources)
                for source_project_id in failure_sources:
                    existing = self._failure_subscriptions.get(source_project_id)
                    if existing is not None and existing.handler_project_id != project_id:
                        raise WorkflowDeploymentConflictError(
                            "A selected workflow already has an active failure handler."
                        )
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
            self._remove_failure_subscriptions_for_handler_unlocked(project_id)
            if release.trigger_kind == "failure":
                for source_project_id in failure_sources:
                    self._failure_subscriptions[source_project_id] = (
                        WorkflowFailureSubscription(
                            source_project_id=source_project_id,
                            handler_project_id=project_id,
                            handler_version=version,
                            handler_deployment_id=deployment.deployment_id,
                            activated_at=current,
                        )
                    )
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
            self._remove_failure_subscriptions_for_deployment_unlocked(
                deployment.deployment_id
            )
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

    def renew_execution_lease(
        self,
        execution_id: str,
        *,
        lease_token: str,
        lease_seconds: float = 60.0,
        now: float | None = None,
    ) -> WorkflowTriggerExecution:
        current = time.time() if now is None else float(now)
        with self._lock:
            item = self._require_execution_unlocked(execution_id)
            self._require_execution_lease_unlocked(item, lease_token)
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
        expected_lease_token: str | None = None,
    ) -> WorkflowTriggerExecution:
        with self._lock:
            item = self._require_execution_unlocked(execution_id)
            if expected_lease_token is not None:
                self._require_execution_lease_unlocked(item, expected_lease_token)
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
        expected_lease_token: str | None = None,
    ) -> WorkflowTriggerExecution:
        with self._lock:
            item = self._require_execution_unlocked(execution_id)
            if expected_lease_token is not None:
                self._require_execution_lease_unlocked(item, expected_lease_token)
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

    def fail_execution(
        self,
        execution_id: str,
        *,
        error: str,
        task_id: str | None = None,
        run_id: str | None = None,
        dispatch_failures: bool = True,
        failed_node_id: str | None = None,
        failed_node_title: str | None = None,
        expected_lease_token: str | None = None,
    ) -> WorkflowTriggerExecution:
        with self._lock:
            item = self._require_execution_unlocked(execution_id)
            if item.status in TERMINAL_EXECUTION_STATUSES:
                return item
            if expected_lease_token is not None:
                self._require_execution_lease_unlocked(item, expected_lease_token)
            item.status = "failed"
            item.task_id = str(task_id or item.task_id or "") or None
            item.run_id = str(run_id or item.run_id or "") or None
            item.error_summary = (
                "Workflow hook execution failed."
                if item.trigger_kind == "http"
                else _safe_error_summary(error)
            )
            item.completed_at = time.time()
            item.updated_at = item.completed_at
            self._clear_lease(item)
            if dispatch_failures:
                self._materialize_failure_execution_unlocked(
                    item,
                    failed_node_id=failed_node_id,
                    failed_node_title=failed_node_title,
                )
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

    def failure_subscription(
        self,
        source_project_id: str,
    ) -> WorkflowFailureSubscription | None:
        with self._lock:
            return self._failure_subscriptions.get(source_project_id)

    def _reserved_descendant_slots_unlocked(self, root_execution_id: str) -> int:
        reserved = 0
        for batch in self._subworkflow_batches.values():
            if batch.root_execution_id != root_execution_id:
                continue
            materialized = sum(
                1
                for relation in self._subworkflow_relations.values()
                if relation.batch_occurrence_key == batch.occurrence_key
            )
            reserved += max(0, batch.item_count - materialized)
        return reserved

    def reserve_subworkflow_batch(
        self,
        *,
        parent_execution_id: str,
        root_execution_id: str,
        call_node_id: str,
        target_project_id: str,
        target_version: int,
        item_count: int,
        input_digest: str,
        now: float | None = None,
    ) -> tuple[WorkflowSubworkflowBatch, bool]:
        current = time.time() if now is None else float(now)
        clean_parent = str(parent_execution_id).strip()
        clean_root = str(root_execution_id).strip() or clean_parent
        clean_node = str(call_node_id).strip()
        clean_digest = str(input_digest).strip()
        if (
            not clean_parent
            or not clean_node
            or not re.fullmatch(r"[a-f0-9]{64}", clean_digest)
            or type(item_count) is not int
            or not 0 <= item_count <= 32
        ):
            raise WorkflowDeploymentValidationError(
                "Subworkflow batch reservation is invalid."
            )
        occurrence_key = f"batch:{clean_parent}:{clean_node}"
        with self._lock:
            existing = self._subworkflow_batches.get(occurrence_key)
            if existing is not None:
                if (
                    existing.parent_execution_id != clean_parent
                    or existing.root_execution_id != clean_root
                    or existing.call_node_id != clean_node
                    or existing.target_project_id != target_project_id
                    or existing.target_version != int(target_version)
                    or existing.item_count != item_count
                    or not secrets.compare_digest(existing.input_digest, clean_digest)
                ):
                    raise WorkflowDeploymentConflictError(
                        "Subworkflow batch input or fixed target changed during recovery."
                    )
                return existing, False
            release = self._require_version_unlocked(
                target_project_id,
                int(target_version),
            )
            deployment = self._active_deployment_for_version_unlocked(
                target_project_id,
                int(target_version),
            )
            if release.trigger_kind != "call" or deployment.trigger_kind != "call":
                raise WorkflowDeploymentConflictError(
                    "Target version is not an active callable workflow."
                )
            descendants = sum(
                1
                for relation in self._subworkflow_relations.values()
                if relation.root_execution_id == clean_root
            )
            reserved = self._reserved_descendant_slots_unlocked(clean_root)
            if descendants + reserved + item_count > 32:
                raise WorkflowDeploymentConflictError(
                    "A root workflow execution cannot reserve more than 32 subworkflow calls."
                )
            batch = WorkflowSubworkflowBatch(
                occurrence_key=occurrence_key,
                parent_execution_id=clean_parent,
                root_execution_id=clean_root,
                call_node_id=clean_node,
                target_project_id=target_project_id,
                target_version=int(target_version),
                item_count=item_count,
                input_digest=clean_digest,
                created_at=current,
            )
            self._subworkflow_batches[occurrence_key] = batch
            self._persist_unlocked()
            return batch, True

    def materialize_subworkflow_execution(
        self,
        *,
        parent_execution_id: str,
        root_execution_id: str,
        parent_depth: int,
        call_node_id: str,
        target_project_id: str,
        target_version: int,
        test_mode: bool,
        suppress_failure_dispatch: bool,
        batch_occurrence_key: str | None = None,
        batch_index: int | None = None,
        input_digest: str | None = None,
        now: float | None = None,
    ) -> tuple[WorkflowTriggerExecution, bool]:
        current = time.time() if now is None else float(now)
        clean_parent = str(parent_execution_id).strip()
        clean_root = str(root_execution_id).strip() or clean_parent
        clean_node = str(call_node_id).strip()
        if not clean_parent or not clean_node:
            raise WorkflowDeploymentValidationError(
                "Subworkflow calls need parent execution and call node IDs."
            )
        depth = int(parent_depth) + 1
        if depth > 8:
            raise WorkflowDeploymentConflictError(
                "Subworkflow call depth cannot exceed 8."
            )
        clean_batch_key = str(batch_occurrence_key or "").strip()
        clean_input_digest = str(input_digest or "").strip()
        if clean_batch_key:
            if type(batch_index) is not int or batch_index < 0:
                raise WorkflowDeploymentValidationError(
                    "Subworkflow batch index is invalid."
                )
            occurrence_key = f"call:{clean_parent}:{clean_node}:{batch_index}"
        else:
            if batch_index is not None or clean_input_digest:
                raise WorkflowDeploymentValidationError(
                    "Subworkflow batch metadata is incomplete."
                )
            occurrence_key = f"call:{clean_parent}:{clean_node}"
        with self._lock:
            batch: WorkflowSubworkflowBatch | None = None
            if clean_batch_key:
                batch = self._subworkflow_batches.get(clean_batch_key)
                if (
                    batch is None
                    or batch.parent_execution_id != clean_parent
                    or batch.root_execution_id != clean_root
                    or batch.call_node_id != clean_node
                    or batch.target_project_id != target_project_id
                    or batch.target_version != int(target_version)
                    or batch_index is None
                    or batch_index >= batch.item_count
                    or not secrets.compare_digest(
                        batch.input_digest,
                        clean_input_digest,
                    )
                ):
                    raise WorkflowDeploymentConflictError(
                        "Subworkflow batch reservation does not match this item."
                    )
            existing_relation = self._subworkflow_relations.get(occurrence_key)
            if existing_relation is not None:
                child = self._require_execution_unlocked(
                    existing_relation.child_execution_id
                )
                if (
                    child.project_id != target_project_id
                    or child.version != int(target_version)
                    or existing_relation.batch_occurrence_key
                    != (clean_batch_key or None)
                    or existing_relation.batch_index != batch_index
                    or existing_relation.input_digest
                    != (clean_input_digest or None)
                ):
                    raise WorkflowDeploymentConflictError(
                        "Existing subworkflow occurrence does not match the requested call."
                    )
                return (
                    child,
                    False,
                )
            descendants = sum(
                1
                for relation in self._subworkflow_relations.values()
                if relation.root_execution_id == clean_root
            )
            reserved = self._reserved_descendant_slots_unlocked(clean_root)
            if not clean_batch_key and descendants + reserved >= 32:
                raise WorkflowDeploymentConflictError(
                    "A root workflow execution cannot create more than 32 subworkflow calls."
                )
            release = self._require_version_unlocked(
                target_project_id,
                int(target_version),
            )
            deployment = self._active_deployment_for_version_unlocked(
                target_project_id,
                int(target_version),
            )
            if release.trigger_kind != "call" or deployment.trigger_kind != "call":
                raise WorkflowDeploymentConflictError(
                    "Target version is not an active callable workflow."
                )
            child_execution_id = f"wfx_{uuid.uuid4().hex}"
            task_id = "wft_" + hashlib.sha256(
                occurrence_key.encode("utf-8")
            ).hexdigest()[:32]
            item = WorkflowTriggerExecution(
                execution_id=child_execution_id,
                project_id=target_project_id,
                version=int(target_version),
                deployment_id=deployment.deployment_id,
                trigger_kind="call",
                occurrence_key=occurrence_key,
                scheduled_at=current,
                task_id=task_id,
                trigger_summary={
                    "suppress_failure_dispatch": bool(suppress_failure_dispatch),
                    "test_mode": bool(test_mode),
                    "depth": depth,
                },
                parent_execution_id=clean_parent,
                root_execution_id=clean_root,
                call_node_id=clean_node,
                batch_index=batch_index,
                test_mode=bool(test_mode),
                created_at=current,
                updated_at=current,
            )
            relation = WorkflowSubworkflowRelation(
                occurrence_key=occurrence_key,
                parent_execution_id=clean_parent,
                child_execution_id=child_execution_id,
                root_execution_id=clean_root,
                call_node_id=clean_node,
                depth=depth,
                task_id=task_id,
                batch_occurrence_key=clean_batch_key or None,
                batch_index=batch_index,
                input_digest=clean_input_digest or None,
                created_at=current,
            )
            self._executions[item.execution_id] = item
            self._subworkflow_relations[occurrence_key] = relation
            self._persist_unlocked()
            return item, True

    def subworkflow_relation_for_child(
        self,
        child_execution_id: str,
    ) -> WorkflowSubworkflowRelation | None:
        with self._lock:
            return next(
                (
                    relation
                    for relation in self._subworkflow_relations.values()
                    if relation.child_execution_id == child_execution_id
                ),
                None,
            )

    def cancel_execution(
        self,
        execution_id: str,
        *,
        error: str = "Subworkflow execution was cancelled.",
    ) -> WorkflowTriggerExecution:
        with self._lock:
            item = self._require_execution_unlocked(execution_id)
            if item.status in TERMINAL_EXECUTION_STATUSES:
                return item
            item.status = "cancelled"
            item.error_summary = _safe_error_summary(error)
            item.completed_at = time.time()
            item.updated_at = item.completed_at
            self._clear_lease(item)
            self._persist_unlocked()
            return item

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
        source_execution_id = item.source_execution_id or item.trigger_summary.get(
            "source_execution_id"
        )
        payload["parent_execution_id"] = item.parent_execution_id
        payload["root_execution_id"] = (
            item.root_execution_id or source_execution_id or item.execution_id
        )
        payload["source_execution_id"] = source_execution_id
        payload["call_node_id"] = item.call_node_id
        payload["test_mode"] = bool(
            item.test_mode or item.trigger_summary.get("test_mode", False)
        )
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

    def _active_deployment_for_version_unlocked(
        self,
        project_id: str,
        version: int,
    ) -> WorkflowDeployment:
        item = next(
            (
                deployment
                for deployment in self._deployments.values()
                if deployment.project_id == project_id
                and deployment.version == int(version)
                and deployment.active
            ),
            None,
        )
        if item is None:
            raise WorkflowDeploymentConflictError(
                "Target workflow version is not currently active."
            )
        return item

    def _validate_subworkflow_targets_unlocked(
        self,
        source_project_id: str,
        workflow: dict[str, Any],
    ) -> None:
        call_nodes = [
            node
            for node in workflow.get("nodes", [])
            if isinstance(node, dict)
            and (
                _raw_node_kind(node) == "invoke_workflow"
                or (
                    _raw_node_kind(node) == "iteration"
                    and is_workflow_map(dict(node.get("data") or {}))
                )
            )
        ]
        for node in call_nodes:
            data = dict(node.get("data") or {})
            batch_call = _raw_node_kind(node) == "iteration"
            target_project_id = str(data.get("targetProjectId") or "").strip()
            try:
                target_version = int(data.get("targetVersion") or 0)
            except (TypeError, ValueError) as exc:
                raise WorkflowDeploymentValidationError(
                    "Workflow calls need a fixed target version."
                ) from exc
            if target_project_id == source_project_id:
                raise WorkflowDeploymentConflictError(
                    "A workflow cannot call itself."
                )
            target = self._require_version_unlocked(
                target_project_id,
                target_version,
            )
            self._active_deployment_for_version_unlocked(
                target_project_id,
                target_version,
            )
            if target.trigger_kind != "call":
                raise WorkflowDeploymentConflictError(
                    "Target version must use a subworkflow entry."
                )
            waiting_node = next(
                (
                    target_node
                    for target_node in target.workflow.get("nodes", [])
                    if isinstance(target_node, dict)
                    and workflow_node_contract_registry.require(
                        _raw_node_kind(target_node)
                    ).execution.can_wait
                ),
                None,
            )
            if waiting_node is not None:
                raise WorkflowDeploymentConflictError(
                    "Callable workflows cannot contain waiting nodes."
                )
            self._validate_call_bindings_unlocked(
                data,
                target,
                batch_call=batch_call,
            )
            if self._release_reaches_project_unlocked(
                target,
                source_project_id,
                visited=set(),
            ):
                raise WorkflowDeploymentConflictError(
                    "Subworkflow dependency cycle detected."
                )

    @staticmethod
    def _validate_call_bindings_unlocked(
        data: dict[str, Any],
        target: WorkflowVersion,
        *,
        batch_call: bool = False,
    ) -> None:
        bindings = data.get("inputBindings")
        if not isinstance(bindings, dict):
            raise WorkflowDeploymentValidationError(
                "Workflow call inputBindings must be an object."
            )
        declarations = {
            str(item.get("name") or ""): item
            for item in target.workflow.get("variables", [])
            if isinstance(item, dict) and item.get("kind") == "input"
        }
        unknown = sorted(set(bindings) - set(declarations))
        if unknown:
            raise WorkflowDeploymentValidationError(
                f"Workflow call contains unknown input '{unknown[0]}'."
            )
        missing = sorted(
            name
            for name, declaration in declarations.items()
            if "defaultValue" not in declaration and name not in bindings
        )
        if missing:
            raise WorkflowDeploymentValidationError(
                f"Workflow call is missing required input '{missing[0]}'."
            )
        for name, binding in bindings.items():
            if not isinstance(binding, dict):
                raise WorkflowDeploymentValidationError(
                    f"Workflow call input '{name}' has an invalid binding."
                )
            source = str(binding.get("source") or "")
            if batch_call and source == "item":
                continue
            if batch_call and source == "index":
                declaration = declarations[name]
                if not workflow_variable_value_matches_type(
                    str(declaration.get("valueType") or "json"),
                    0,
                ):
                    raise WorkflowDeploymentValidationError(
                        f"Workflow call index for '{name}' has the wrong type."
                    )
                continue
            if source == "variable":
                if not re.fullmatch(
                    r"[A-Za-z_][A-Za-z0-9_]{0,63}",
                    str(binding.get("variable") or ""),
                ):
                    raise WorkflowDeploymentValidationError(
                        f"Workflow call input '{name}' needs a variable identifier."
                    )
                continue
            if source != "literal" or "value" not in binding:
                raise WorkflowDeploymentValidationError(
                    f"Workflow call input '{name}' has an invalid binding source."
                )
            declaration = declarations[name]
            if not workflow_variable_value_matches_type(
                str(declaration.get("valueType") or "json"),
                binding.get("value"),
            ):
                raise WorkflowDeploymentValidationError(
                    f"Workflow call literal for '{name}' has the wrong type."
                )

    def _release_reaches_project_unlocked(
        self,
        release: WorkflowVersion,
        target_project_id: str,
        *,
        visited: set[tuple[str, int]],
    ) -> bool:
        key = (release.project_id, release.version)
        if key in visited:
            return False
        visited.add(key)
        for node in release.workflow.get("nodes", []):
            if not isinstance(node, dict):
                continue
            data = dict(node.get("data") or {})
            if not (
                _raw_node_kind(node) == "invoke_workflow"
                or (
                    _raw_node_kind(node) == "iteration"
                    and is_workflow_map(data)
                )
            ):
                continue
            project_id = str(data.get("targetProjectId") or "")
            if project_id == target_project_id:
                return True
            try:
                version = int(data.get("targetVersion") or 0)
            except (TypeError, ValueError):
                continue
            dependency = self._versions.get((project_id, version))
            if dependency is not None and self._release_reaches_project_unlocked(
                dependency,
                target_project_id,
                visited=visited,
            ):
                return True
        return False

    def _require_execution_unlocked(self, execution_id: str) -> WorkflowTriggerExecution:
        item = self._executions.get(execution_id)
        if item is None:
            raise WorkflowDeploymentNotFoundError("Workflow trigger execution not found.")
        return item

    @staticmethod
    def _failure_source_project_ids(
        workflow: dict[str, Any],
        entry_node_id: str,
    ) -> list[str]:
        entry = next(
            (
                node
                for node in workflow.get("nodes", [])
                if str(node.get("id") or "") == entry_node_id
            ),
            None,
        )
        if not isinstance(entry, dict):
            raise WorkflowDeploymentValidationError(
                "Failure entry node was not found in the published workflow."
            )
        values = entry.get("data", {}).get("sourceProjectIds")
        if not isinstance(values, list):
            raise WorkflowDeploymentValidationError(
                "Failure entry needs source workflow projects."
            )
        return [str(value) for value in values]

    def _validate_failure_sources_unlocked(
        self,
        handler_project_id: str,
        source_project_ids: list[str],
    ) -> None:
        if not 1 <= len(source_project_ids) <= 50:
            raise WorkflowDeploymentValidationError(
                "Failure entry needs 1 to 50 source workflow projects."
            )
        if len(source_project_ids) != len(set(source_project_ids)):
            raise WorkflowDeploymentValidationError(
                "Failure entry source workflow projects must be unique."
            )
        if handler_project_id in source_project_ids:
            raise WorkflowDeploymentConflictError(
                "A failure handler cannot subscribe to itself."
            )
        missing = [
            project_id
            for project_id in source_project_ids
            if project_id not in self._projects
        ]
        if missing:
            raise WorkflowDeploymentConflictError(
                "A selected failure source workflow does not exist."
            )

    def _remove_failure_subscriptions_for_handler_unlocked(
        self,
        handler_project_id: str,
    ) -> None:
        for source_project_id in [
            source_id
            for source_id, subscription in self._failure_subscriptions.items()
            if subscription.handler_project_id == handler_project_id
        ]:
            self._failure_subscriptions.pop(source_project_id, None)

    def _remove_failure_subscriptions_for_deployment_unlocked(
        self,
        deployment_id: str,
    ) -> None:
        for source_project_id in [
            source_id
            for source_id, subscription in self._failure_subscriptions.items()
            if subscription.handler_deployment_id == deployment_id
        ]:
            self._failure_subscriptions.pop(source_project_id, None)

    def _materialize_failure_execution_unlocked(
        self,
        source: WorkflowTriggerExecution,
        *,
        failed_node_id: str | None,
        failed_node_title: str | None,
    ) -> WorkflowTriggerExecution | None:
        if (
            source.trigger_kind == "failure"
            or bool(source.trigger_summary.get("suppress_failure_dispatch"))
            or bool(source.trigger_summary.get("test_mode"))
        ):
            return None
        subscription = self._failure_subscriptions.get(source.project_id)
        if subscription is None:
            return None
        deployment = self._deployments.get(subscription.handler_deployment_id)
        if deployment is None or not deployment.active or deployment.trigger_kind != "failure":
            return None
        occurrence_key = (
            f"failure:{source.execution_id}:{subscription.handler_deployment_id}"
        )
        existing = next(
            (
                item
                for item in self._executions.values()
                if item.occurrence_key == occurrence_key
            ),
            None,
        )
        if existing is not None:
            return existing
        failed_at = float(source.completed_at or time.time())
        summary: dict[str, Any] = {
            "source_project_id": source.project_id,
            "source_version": source.version,
            "source_deployment_id": source.deployment_id,
            "source_execution_id": source.execution_id,
            "source_task_id": source.task_id,
            "source_run_id": source.run_id,
            "source_trigger_kind": source.trigger_kind,
            "failed_at": failed_at,
            "error_summary": source.error_summary,
            "occurrence_key": occurrence_key,
            "suppress_failure_dispatch": True,
            "test_mode": False,
        }
        if failed_node_id:
            summary["failed_node_id"] = _safe_error_summary(
                str(failed_node_id)
            )[:128]
        if failed_node_title:
            summary["failed_node_title"] = _safe_error_summary(
                str(failed_node_title)
            )[:120]
        item = WorkflowTriggerExecution(
            execution_id=f"wfx_{uuid.uuid4().hex}",
            project_id=subscription.handler_project_id,
            version=subscription.handler_version,
            deployment_id=subscription.handler_deployment_id,
            trigger_kind="failure",
            occurrence_key=occurrence_key,
            scheduled_at=failed_at,
            trigger_summary=summary,
            created_at=failed_at,
            updated_at=failed_at,
        )
        self._executions[item.execution_id] = item
        return item

    @staticmethod
    def _clear_lease(item: WorkflowTriggerExecution) -> None:
        item.lease_owner = None
        item.lease_token = None
        item.lease_expires_at = 0.0

    @staticmethod
    def _require_execution_lease_unlocked(
        item: WorkflowTriggerExecution,
        lease_token: str,
    ) -> None:
        if item.status != "running" or not secrets.compare_digest(
            str(item.lease_token or ""),
            str(lease_token or ""),
        ):
            raise WorkflowDeploymentConflictError(
                "Trigger execution lease is no longer owned by this worker."
            )

    def _persist_unlocked(self) -> None:
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": "workflow-deployments-v2",
            "projects": [asdict(item) for item in self._projects.values()],
            "versions": [asdict(item) for item in self._versions.values()],
            "deployments": [asdict(item) for item in self._deployments.values()],
            "executions": [asdict(item) for item in self._executions.values()],
            "failure_subscriptions": [
                asdict(item) for item in self._failure_subscriptions.values()
            ],
            "subworkflow_relations": [
                asdict(item) for item in self._subworkflow_relations.values()
            ],
            "subworkflow_batches": [
                asdict(item) for item in self._subworkflow_batches.values()
            ],
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
                raw.setdefault("parent_execution_id", None)
                raw.setdefault("root_execution_id", None)
                raw.setdefault("source_execution_id", None)
                raw.setdefault("call_node_id", None)
                raw.setdefault("batch_index", None)
                raw.setdefault("test_mode", False)
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
            seen_failure_sources: set[str] = set()
            for raw in payload.get("failure_subscriptions", []):
                item = WorkflowFailureSubscription(**raw)
                if item.source_project_id in seen_failure_sources:
                    raise ValueError("Duplicate workflow failure subscription source.")
                seen_failure_sources.add(item.source_project_id)
                self._failure_subscriptions[item.source_project_id] = item
            for raw in payload.get("subworkflow_batches", []):
                batch = WorkflowSubworkflowBatch(**raw)
                if batch.occurrence_key in self._subworkflow_batches:
                    raise ValueError("Duplicate subworkflow batch occurrence key.")
                if (
                    batch.occurrence_key
                    != f"batch:{batch.parent_execution_id}:{batch.call_node_id}"
                    or not batch.parent_execution_id
                    or not batch.root_execution_id
                    or not batch.call_node_id
                    or not re.fullmatch(r"[a-f0-9]{64}", batch.input_digest)
                    or type(batch.item_count) is not int
                    or not 0 <= batch.item_count <= 32
                    or (batch.target_project_id, batch.target_version)
                    not in self._versions
                ):
                    raise ValueError("Subworkflow batch reservation is invalid.")
                self._subworkflow_batches[batch.occurrence_key] = batch
            for raw in payload.get("subworkflow_relations", []):
                raw.setdefault("batch_occurrence_key", None)
                raw.setdefault("batch_index", None)
                raw.setdefault("input_digest", None)
                relation = WorkflowSubworkflowRelation(**raw)
                if relation.occurrence_key in self._subworkflow_relations:
                    raise ValueError("Duplicate subworkflow occurrence key.")
                child = self._executions.get(relation.child_execution_id)
                if child is None:
                    raise ValueError("Subworkflow relation child execution is missing.")
                if (
                    child.trigger_kind != "call"
                    or child.occurrence_key != relation.occurrence_key
                    or child.parent_execution_id != relation.parent_execution_id
                    or child.root_execution_id != relation.root_execution_id
                    or child.call_node_id != relation.call_node_id
                    or child.batch_index != relation.batch_index
                    or child.task_id != relation.task_id
                    or not 1 <= relation.depth <= 8
                ):
                    raise ValueError("Subworkflow relation does not match its child execution.")
                if relation.batch_occurrence_key is not None:
                    batch = self._subworkflow_batches.get(
                        relation.batch_occurrence_key
                    )
                    if (
                        batch is None
                        or relation.parent_execution_id
                        != batch.parent_execution_id
                        or relation.root_execution_id != batch.root_execution_id
                        or relation.call_node_id != batch.call_node_id
                        or type(relation.batch_index) is not int
                        or not 0 <= relation.batch_index < batch.item_count
                        or relation.occurrence_key
                        != (
                            f"call:{relation.parent_execution_id}:"
                            f"{relation.call_node_id}:{relation.batch_index}"
                        )
                        or relation.input_digest != batch.input_digest
                        or child.project_id != batch.target_project_id
                        or child.version != batch.target_version
                    ):
                        raise ValueError(
                            "Subworkflow batch relation does not match its reservation."
                        )
                elif relation.batch_index is not None or relation.input_digest is not None:
                    raise ValueError("Subworkflow relation batch metadata is incomplete.")
                self._subworkflow_relations[relation.occurrence_key] = relation
            self._validate_loaded_failure_subscriptions_unlocked()
        except Exception as exc:
            raise WorkflowDeploymentValidationError(
                "Workflow deployment snapshot is invalid; refusing to start with empty state."
            ) from exc

    def _validate_loaded_failure_subscriptions_unlocked(self) -> None:
        for source_project_id, subscription in self._failure_subscriptions.items():
            if source_project_id == subscription.handler_project_id:
                raise ValueError("A workflow cannot subscribe to its own failures.")
            if source_project_id not in self._projects:
                raise ValueError("Workflow failure subscription source is missing.")
            handler = self._projects.get(subscription.handler_project_id)
            release = self._versions.get(
                (subscription.handler_project_id, subscription.handler_version)
            )
            deployment = self._deployments.get(subscription.handler_deployment_id)
            if (
                handler is None
                or handler.active_version != subscription.handler_version
                or release is None
                or release.trigger_kind != "failure"
                or deployment is None
                or not deployment.active
                or deployment.project_id != subscription.handler_project_id
                or deployment.version != subscription.handler_version
                or deployment.trigger_kind != "failure"
            ):
                raise ValueError("Workflow failure subscription handler is invalid.")


def validate_publishable_workflow(
    workflow: dict[str, Any],
    *,
    credential_validator: Callable[[str], Any] | None = None,
    mcp_tool_validator: Callable[[dict[str, Any]], Any] | None = None,
    xpert_target_validator: Callable[[str, int], Any] | None = None,
) -> tuple[WorkflowTriggerKind, str]:
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
        else "failure" if entry_kind == "failure_event_entry"
        else "call" if entry_kind == "workflow_call_entry"
        else "manual"
    )
    if entry.id in {edge.target for edge in definition.edges}:
        raise WorkflowDeploymentValidationError("The entry node cannot have incoming edges.")
    for node in definition.nodes:
        kind = node_kind(node)
        contract = workflow_node_contract_registry.require(kind)
        if kind == "knowledge_citation":
            raise WorkflowDeploymentValidationError(
                "Legacy knowledge citation nodes must be migrated to knowledge_retrieval before publishing."
            )
        if kind == "template_transform":
            raise WorkflowDeploymentValidationError(
                "Legacy template transform nodes must be migrated to variable_assign before publishing."
            )
        if kind == "code" and r20_contract_version(node.data) != 2:
            raise WorkflowDeploymentValidationError(
                "Legacy code nodes must be explicitly migrated before publishing."
            )
        if kind == "variable_aggregator" and r20_contract_version(node.data) != 2:
            raise WorkflowDeploymentValidationError(
                "Legacy variable aggregator nodes must be explicitly migrated before publishing."
            )
        if kind == "iteration" and not is_workflow_map(node.data):
            if node.data.get("contractVersion") != 2:
                raise WorkflowDeploymentValidationError(
                    "Legacy iteration nodes must be explicitly migrated before publishing."
                )
        if kind in {"agent_task", "agent_handoff", "handoff_router"}:
            if r20_contract_version(node.data) != 2:
                raise WorkflowDeploymentValidationError(
                    f"Legacy {kind} nodes must be explicitly migrated before publishing."
                )
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
        if trigger_kind == "http" and kind in {"human_intervention", "mcp_tool"}:
            raise WorkflowDeploymentValidationError(
                "HTTP deployments cannot contain interactive waiting nodes."
            )
        if (
            trigger_kind == "http"
            and kind in {"agent_handoff", "handoff_router"}
            and bool(node.data.get("waitForCompletion"))
        ):
            raise WorkflowDeploymentValidationError(
                "HTTP deployments cannot wait for an Agent Handoff result."
            )
        if (
            kind in {"agent_handoff", "handoff_router"}
            and r20_contract_version(node.data) == 2
            and str(node.data.get("targetMode") or "") == "xpert"
        ):
            if xpert_target_validator is None:
                raise WorkflowDeploymentValidationError(
                    "Xpert Handoff target validation is unavailable."
                )
            try:
                xpert_target_validator(
                    str(node.data.get("targetXpertId") or ""),
                    int(node.data.get("targetVersion") or 0),
                )
            except Exception as exc:
                raise WorkflowDeploymentValidationError(
                    "The fixed Xpert Handoff target is unavailable."
                ) from exc
        if trigger_kind == "call" and contract.execution.can_wait:
            raise WorkflowDeploymentValidationError(
                "Callable workflows cannot contain waiting nodes."
            )
        if kind == "http_request":
            if not is_http_request_v2(node.data):
                raise WorkflowDeploymentValidationError(
                    "Legacy HTTP request nodes must be explicitly migrated before publishing."
                )
            try:
                validate_http_request_credential(
                    node.data,
                    credential_validator,
                )
            except WorkflowHttpRequestError as exc:
                raise WorkflowDeploymentValidationError(exc.safe_message) from exc
        if kind in {"human_intervention", "mcp_tool", "variable_assign"}:
            if r20_contract_version(node.data) != 2:
                raise WorkflowDeploymentValidationError(
                    f"Legacy {kind} nodes must be explicitly migrated before publishing."
                )
        if kind == "mcp_tool":
            if mcp_tool_validator is None:
                raise WorkflowDeploymentValidationError(
                    "Workflow MCP tool registry validation is unavailable."
                )
            try:
                mcp_tool_validator(node.data)
            except WorkflowR20NodeError as exc:
                raise WorkflowDeploymentValidationError(exc.safe_message) from exc
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


def _raw_node_kind(node: dict[str, Any]) -> str:
    data = node.get("data")
    raw = (
        data.get("kind")
        if isinstance(data, dict) and data.get("kind")
        else node.get("type")
    )
    return str(raw or "").strip().replace("-", "_")


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
    raw = str(value or "Workflow trigger execution failed.")
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    text = lines[-1] if lines else "Workflow trigger execution failed."
    text = re.sub(
        r"(?i)\b(?:Bearer|Basic|Token|ApiKey)\s+\S+",
        "[redacted]",
        text,
    )
    text = re.sub(
        r"(?i)\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,}|AKIA[A-Z0-9]{16})\b",
        "[redacted]",
        text,
    )
    text = re.sub(
        r"(?i)(https?://)[^/\s:@]+:[^@\s/]+@",
        r"\1[redacted]@",
        text,
    )
    text = re.sub(
        r"(?i)(?<![A-Za-z0-9])"
        r"([\"']?(?:proxy[-_]?authorization|authorization|set[-_]?cookie|cookie)"
        r"[\"']?\s*[:=]\s*).*$",
        r"\1[redacted]",
        text,
    )
    text = re.sub(
        r"(?i)(?<![A-Za-z0-9])"
        r"([\"']?(?:credential|password|passwd|client[-_]?secret|secret|"
        r"webhook[-_]?key|api[-_]?key|access[-_]?token|refresh[-_]?token|"
        r"private[-_]?key|token)[\"']?\s*[:=]\s*)"
        r"(?:\"[^\"]*\"|'[^']*'|[^\s,;&}\]]+)",
        r"\1[redacted]",
        text,
    )
    text = re.sub(
        r"(?i)\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b",
        "[redacted]",
        text,
    )
    text = re.sub(r"(?i)\b[0-9a-f]{64}\b", "[redacted]", text)
    text = re.sub(
        r"(?i)-----BEGIN\s+(?:RSA\s+|EC\s+|OPENSSH\s+)?PRIVATE\s+KEY-----.*$",
        "[redacted]",
        text,
    )
    return text[:1_000]
