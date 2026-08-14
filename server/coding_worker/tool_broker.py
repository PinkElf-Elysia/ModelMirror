from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from time import time
from typing import Any, Protocol
from urllib.parse import quote, urlsplit

import httpx

from pydantic import Field, ValidationError

from .contracts import (
    ApprovalStatus,
    CapabilityLease,
    CapabilityName,
    CodeLocation,
    CodeRange,
    DiagnosticSeverity,
    OperationState,
    PolicyProfile,
    RuntimeProtocol,
    SessionLedgerKind,
    ShellApprovalScope,
    ShellMode,
    StrictModel,
    SubtaskKind,
    SubtaskMergeState,
    SubtaskRecord,
    SubtaskRequest,
    TaskState,
    TurnBarrier,
    TurnTransactionState,
    WorkerDiagnostic,
    WorkerPlanItem,
    WorkerQuestionOption,
    WorkerTodoItem,
)
from .changeset import ChangesetEngine, ChangesetError
from .process_manager import BackgroundProcessManager, ProcessManagerError
from .network_policy import EgressPolicy, NetworkPolicyError
from .store import CodingWorkerStore, WorkerConflictError
from .workspace import WorkspaceBroker, WorkspaceError


MAX_TOOL_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_WRITE_BYTES = 8 * 1024 * 1024
SAFE_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
ALWAYS_DENIED_EXECUTABLES = frozenset(
    {
        "bash",
        "cmd",
        "curl",
        "docker",
        "mount",
        "nc",
        "netcat",
        "powershell",
        "pwsh",
        "scp",
        "sh",
        "ssh",
        "sudo",
        "wget",
    }
)
DENIED_GIT_SUBCOMMANDS = frozenset(
    {"clone", "fetch", "ls-remote", "pull", "push", "remote", "submodule"}
)
CODE_INTELLIGENCE_TO_OPERATION = {
    "code_symbols": "symbols",
    "code_definition": "definition",
    "code_references": "references",
    "code_hover": "hover",
    "code_diagnostics": "diagnostics",
}


class ToolBrokerError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class FrozenCheck(StrictModel):
    check_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    argv: tuple[str, ...] = Field(min_length=1, max_length=64)
    timeout_seconds: int = Field(default=300, ge=1, le=1800)


class ToolResult(StrictModel):
    operation_id: str
    tool_name: str
    state: OperationState
    data: dict[str, Any] = Field(default_factory=dict)


class ToolExecutor(Protocol):
    async def run_process(self, *, task_id: str, workspace_id: str, argv: Sequence[str], timeout_seconds: int, isolated: bool, environment_overrides: Mapping[str, str] | None = None) -> dict[str, Any]: ...
    async def start_service(self, *, task_id: str, workspace_id: str, argv: Sequence[str], ttl_seconds: int, preview_port: int | None = None) -> dict[str, Any]: ...
    async def service_status(self, *, task_id: str, workspace_id: str, service_id: str) -> dict[str, Any]: ...
    async def service_input(self, *, task_id: str, workspace_id: str, service_id: str, data: str) -> dict[str, Any]: ...
    async def stop_service(self, *, task_id: str, workspace_id: str, service_id: str) -> dict[str, Any]: ...
    async def run_shell(self, *, task_id: str, workspace_id: str, operation_id: str, script: str, cwd: str, mode: str, timeout_seconds: int, output_callback: Any = None) -> dict[str, Any]: ...
    async def code_intelligence(self, *, task_id: str, workspace_id: str, operation_id: str, operation: str, path: str, line: int, character: int) -> dict[str, Any]: ...


class ToolBroker:
    """The sole side-effect boundary exposed to a coding provider.

    Requests contain only task/workspace-relative values. Legacy commands remain
    argv arrays; V15 shell strings require an exact single-operation approval and
    execute only in a disposable, credential-free clone.
    """

    def __init__(
        self,
        *,
        store: CodingWorkerStore,
        workspace_broker: WorkspaceBroker,
        frozen_checks: Mapping[str, FrozenCheck] | None = None,
        process_manager: BackgroundProcessManager | None = None,
        egress_policy: EgressPolicy | None = None,
        egress_proxy_url: str | None = None,
        max_output_bytes: int = MAX_TOOL_OUTPUT_BYTES,
        executor: ToolExecutor | None = None,
        documentation_resources: Mapping[str, str] | None = None,
        subtask_handler: Callable[
            [str, SubtaskRequest], Awaitable[SubtaskRecord]
        ]
        | None = None,
        subtask_merge_handler: Callable[
            [str, str, str], Awaitable[SubtaskRecord]
        ]
        | None = None,
    ) -> None:
        if not 1024 <= max_output_bytes <= 16 * 1024 * 1024:
            raise ValueError("tool output limit is invalid")
        self.store = store
        self.workspace_broker = workspace_broker
        self.frozen_checks = dict(frozen_checks or {})
        self.process_manager = process_manager
        self.egress_policy = egress_policy
        self.egress_proxy_url = self._validate_proxy_url(egress_proxy_url)
        self.max_output_bytes = max_output_bytes
        self.executor = executor
        self.documentation_resources = self._validate_documentation_resources(
            documentation_resources or {}
        )
        self.subtask_handler = subtask_handler
        self.subtask_merge_handler = subtask_merge_handler
        self.changesets = ChangesetEngine(workspace_broker)

    async def execute(
        self,
        *,
        task_id: str,
        operation_id: str,
        tool_name: str,
        arguments: Mapping[str, Any],
        lease_id: str | None = None,
        network_lease_id: str | None = None,
    ) -> ToolResult:
        if SAFE_TOOL_NAME.fullmatch(tool_name) is None:
            raise ToolBrokerError("Tool name is invalid.", code="tool_not_allowed")
        task = self.store.get_task(task_id)
        if task.workspace_id is None:
            raise ToolBrokerError("Task workspace is unavailable.", code="workspace_unavailable")
        request = {
            "arguments": self._json_object(arguments),
            "workspace_id": task.workspace_id,
        }
        intent_sha256 = self._intent_sha256(tool_name, request)
        current_turn = (
            self.store.current_turn_transaction(task_id)
            if task.runtime_protocol is RuntimeProtocol.V17
            else None
        )
        if task.state is TaskState.WAITING_APPROVAL:
            existing_operation_ids = {
                operation.operation_id
                for operation in self.store.list_operations(task_id)
            }
            if operation_id not in existing_operation_ids:
                raise ToolBrokerError(
                    "Task is parked for another exact approval.",
                    code="task_state_conflict",
                )
        try:
            operation = self.store.create_operation(
                task_id=task_id,
                operation_id=operation_id,
                tool_name=tool_name,
                intent_sha256=intent_sha256,
                request=request,
                turn_id=current_turn.turn_id if current_turn is not None else None,
            )
        except WorkerConflictError as exc:
            message = "Tool operation was rejected."
            if exc.code == "operation_intent_conflict":
                message = (
                    "This operation_id is already bound to different tool input. "
                    "Use a new operation_id for the changed intent; reuse the old id "
                    "only with the exact original tool and arguments."
                )
            raise ToolBrokerError(message, code=exc.code) from exc
        if operation.state is OperationState.COMPLETED:
            if self._result_has_changeset(operation.tool_name, operation.result):
                self.changesets.finalize(
                    task_id=task_id,
                    workspace_id=task.workspace_id,
                    operation_id=operation_id,
                )
            return ToolResult(
                operation_id=operation_id,
                tool_name=tool_name,
                state=operation.state,
                data=operation.result or {},
            )
        if operation.state is OperationState.UNKNOWN:
            return self.reconcile(operation_id)
        if operation.state is not OperationState.PREPARED:
            raise ToolBrokerError(
                "Tool operation cannot be replayed.", code="operation_not_replayable"
            )
        try:
            task_state = self.store.get_task(task_id).state
            if task_state is TaskState.WAITING_SUBTASKS:
                raise ToolBrokerError(
                    "Task is parked while controlled subtasks run.",
                    code="task_state_conflict",
                )
            if task_state is TaskState.WAITING_APPROVAL:
                pending_approvals = tuple(
                    approval
                    for approval in self.store.list_approvals(task_id)
                    if approval.status is ApprovalStatus.PENDING
                )
                if pending_approvals and not any(
                    approval.operation_id == operation_id
                    for approval in pending_approvals
                ):
                    raise ToolBrokerError(
                        "Task is parked for another exact approval.",
                        code="task_state_conflict",
                    )
            ready_implementation = any(
                relation.kind is SubtaskKind.IMPLEMENT
                and relation.merge_state is SubtaskMergeState.READY
                for relation in self.store.list_subtasks(task_id)
            )
            direct_workspace_mutation = tool_name in {
                "write_file",
                "delete_file",
                "apply_changeset",
            } or (
                tool_name == "run_shell"
                and request["arguments"].get("mode") == ShellMode.MUTATE.value
            )
            if ready_implementation and direct_workspace_mutation:
                raise ToolBrokerError(
                    "Merge or resolve the ready implementation subtask before "
                    "modifying the parent Workspace.",
                    code="subtask_merge_required",
                )
            network_lease = self._authorize(
                task.spec.policy_profile,
                tool_name,
                lease_id,
                task_id,
                operation_id,
                request,
                network_lease_id,
            )
            self.store.transition_operation(
                operation_id,
                OperationState.RUNNING,
                expected_state=OperationState.PREPARED,
            )
            data = await self._dispatch(
                task_id,
                task.workspace_id,
                operation_id,
                tool_name,
                request["arguments"],
                network_lease=network_lease,
            )
            try:
                completed = self.store.transition_operation(
                    operation_id,
                    OperationState.COMPLETED,
                    result=data,
                    expected_state=OperationState.RUNNING,
                )
            except Exception as exc:
                durable_result = (
                    self._tool_can_publish_changeset(tool_name)
                    and self.changesets.is_applied(
                        task_id=task_id,
                        workspace_id=task.workspace_id,
                        operation_id=operation_id,
                    )
                ) or (
                    tool_name == "create_subtask"
                    and self.store.get_subtask(
                        task_id, str(arguments.get("client_subtask_id", ""))
                    )
                    is not None
                ) or (
                    tool_name == "merge_subtask"
                    and self._subtask_merge_is_settled(
                        task_id,
                        str(arguments.get("child_task_id", "")),
                        operation_id,
                    )
                )
                if durable_result:
                    raise ToolBrokerError(
                        "Durable tool result must be reconciled.",
                        code="operation_result_unknown",
                    ) from exc
                raise
            if self._result_has_changeset(tool_name, completed.result):
                self.changesets.finalize(
                    task_id=task_id,
                    workspace_id=task.workspace_id,
                    operation_id=operation_id,
                )
            return ToolResult(
                operation_id=operation_id,
                tool_name=tool_name,
                state=completed.state,
                data=completed.result or {},
            )
        except (
            ToolBrokerError,
            ChangesetError,
            WorkerConflictError,
            WorkspaceError,
            ProcessManagerError,
            NetworkPolicyError,
            OSError,
            asyncio.TimeoutError,
        ) as exc:
            current = self.store.get_operation(operation_id)
            awaiting_approval = (
                isinstance(exc, ToolBrokerError)
                and exc.code == "approval_required"
                and current.state is OperationState.PREPARED
            )
            unknown_result = (
                isinstance(exc, ToolBrokerError)
                and exc.code == "operation_result_unknown"
            )
            if unknown_result and current.state in {
                OperationState.PREPARED,
                OperationState.RUNNING,
            }:
                self.store.transition_operation(
                    operation_id,
                    OperationState.UNKNOWN,
                    result={"code": "operation_result_unknown"},
                    expected_state=current.state,
                )
                if task.runtime_protocol is RuntimeProtocol.V17 and current_turn is not None:
                    self.store.begin_turn_parking(
                        task_id=task_id,
                        turn_id=current_turn.turn_id,
                        barrier=TurnBarrier.OPERATION_UNKNOWN,
                    )
            elif not awaiting_approval and current.state in {
                OperationState.PREPARED,
                OperationState.RUNNING,
            }:
                self.store.transition_operation(
                    operation_id,
                    OperationState.FAILED,
                    result={"code": getattr(exc, "code", "tool_failed")},
                    expected_state=current.state,
                )
            if isinstance(exc, (ToolBrokerError, ChangesetError)):
                if isinstance(exc, ChangesetError):
                    raise ToolBrokerError(
                        "Changeset operation failed.", code=exc.code
                    ) from exc
                raise
            raise ToolBrokerError("Tool operation failed.", code=getattr(exc, "code", "tool_failed")) from exc
        except Exception as exc:
            current = self.store.get_operation(operation_id)
            if current.state in {OperationState.PREPARED, OperationState.RUNNING}:
                self.store.transition_operation(
                    operation_id,
                    OperationState.FAILED,
                    result={"code": getattr(exc, "code", "tool_failed")},
                    expected_state=current.state,
                )
            raise ToolBrokerError(
                "Tool operation failed.", code=getattr(exc, "code", "tool_failed")
            ) from exc

    def reconcile(self, operation_id: str) -> ToolResult:
        operation = self.store.get_operation(operation_id)
        if operation.state is OperationState.COMPLETED:
            if self._result_has_changeset(operation.tool_name, operation.result):
                self.changesets.finalize(
                    task_id=operation.task_id,
                    workspace_id=str(operation.request["workspace_id"]),
                    operation_id=operation_id,
                )
            return ToolResult(
                operation_id=operation_id,
                tool_name=operation.tool_name,
                state=operation.state,
                data=operation.result or {},
            )
        if operation.state is not OperationState.UNKNOWN:
            raise ToolBrokerError(
                "Tool operation is not awaiting reconciliation.",
                code="operation_not_reconcilable",
            )
        workspace_id = str(operation.request["workspace_id"])
        arguments = self._json_object(operation.request["arguments"])
        if operation.tool_name == "write_file":
            target = self._target(workspace_id, str(arguments.get("path", "")))
            expected = str(arguments.get("content_sha256", ""))
            if target.is_file() and not target.is_symlink():
                actual = hashlib.sha256(target.read_bytes()).hexdigest()
                if actual == expected:
                    resolved = self.store.transition_operation(
                        operation_id,
                        OperationState.COMPLETED,
                        result={"path": str(arguments["path"]), "sha256": actual},
                        expected_state=OperationState.UNKNOWN,
                    )
                    return ToolResult(
                        operation_id=operation_id,
                        tool_name=operation.tool_name,
                        state=resolved.state,
                        data=resolved.result or {},
                    )
        elif operation.tool_name == "delete_file":
            target = self._target(workspace_id, str(arguments.get("path", "")))
            if not target.exists() and not target.is_symlink():
                resolved = self.store.transition_operation(
                    operation_id,
                    OperationState.COMPLETED,
                    result={"path": str(arguments["path"]), "deleted": True},
                    expected_state=OperationState.UNKNOWN,
                )
                return ToolResult(
                    operation_id=operation_id,
                    tool_name=operation.tool_name,
                    state=resolved.state,
                    data=resolved.result or {},
                )
        elif operation.tool_name == "apply_changeset":
            try:
                outcome = self.changesets.reconcile(
                    task_id=operation.task_id,
                    workspace_id=workspace_id,
                    operation_id=operation_id,
                )
            except ChangesetError as exc:
                if exc.code == "changeset_rolled_back":
                    self.store.transition_operation(
                        operation_id,
                        OperationState.FAILED,
                        result={"code": exc.code},
                        expected_state=OperationState.UNKNOWN,
                    )
                raise ToolBrokerError(
                    "Changeset reconciliation failed.", code=exc.code
                ) from exc
            resolved = self.store.transition_operation(
                operation_id,
                OperationState.COMPLETED,
                result={"changeset": outcome.model_dump(mode="json")},
                expected_state=OperationState.UNKNOWN,
            )
            self.changesets.finalize(
                task_id=operation.task_id,
                workspace_id=workspace_id,
                operation_id=operation_id,
            )
            return ToolResult(
                operation_id=operation_id,
                tool_name=operation.tool_name,
                state=resolved.state,
                data=resolved.result or {},
            )
        elif operation.tool_name == "run_shell":
            return self._reconcile_shell(operation_id)
        elif operation.tool_name == "create_subtask":
            relation = self.store.get_subtask(
                operation.task_id,
                str(arguments.get("client_subtask_id", "")),
            )
            if relation is not None:
                resolved = self.store.transition_operation(
                    operation_id,
                    OperationState.COMPLETED,
                    result={"subtask": relation.model_dump(mode="json")},
                    expected_state=OperationState.UNKNOWN,
                )
                return ToolResult(
                    operation_id=operation_id,
                    tool_name=operation.tool_name,
                    state=resolved.state,
                    data=resolved.result or {},
                )
        elif operation.tool_name == "merge_subtask":
            relation = self.store.subtask_for_child(
                str(arguments.get("child_task_id", ""))
            )
            if (
                relation is None
                or relation.parent_task_id != operation.task_id
                or relation.merge_operation_id != operation_id
                or relation.merge_state
                not in {SubtaskMergeState.MERGED, SubtaskMergeState.CONFLICTED}
            ):
                raise ToolBrokerError(
                    "Subtask merge result remains unknown.",
                    code="operation_result_unknown",
                )
            resolved = self.store.transition_operation(
                operation_id,
                OperationState.COMPLETED,
                result={"subtask": relation.model_dump(mode="json")},
                expected_state=OperationState.UNKNOWN,
            )
            return ToolResult(
                operation_id=operation_id,
                tool_name=operation.tool_name,
                state=resolved.state,
                data=resolved.result or {},
            )
        raise ToolBrokerError(
            "Tool result is unknown and must not be replayed.",
            code="operation_result_unknown",
        )

    def _authorize(
        self,
        profile: PolicyProfile,
        tool_name: str,
        lease_id: str | None,
        task_id: str,
        operation_id: str,
        request: dict[str, Any],
        network_lease_id: str | None,
    ) -> CapabilityLease | None:
        v15_tools = {
            "read_file_range",
            "glob_files",
            "search_regex",
            "read_operation_output",
            "apply_changeset",
            "run_shell",
            "code_symbols",
            "code_definition",
            "code_references",
            "code_hover",
            "code_diagnostics",
        }
        if tool_name in {"create_subtask", "merge_subtask"}:
            if (
                os.getenv("CODING_WORKER_V16_ENABLED", "false").strip().lower()
                not in {"1", "true", "yes", "on"}
                or os.getenv("CODING_WORKER_SUBAGENTS_ENABLED", "false")
                .strip()
                .lower()
                not in {"1", "true", "yes", "on"}
            ):
                raise ToolBrokerError(
                    "Controlled subtasks are disabled.", code="tool_not_allowed"
                )
            if tool_name == "create_subtask":
                try:
                    subtask = SubtaskRequest.model_validate(request["arguments"])
                except ValidationError as exc:
                    raise ToolBrokerError(
                        "Subtask input is invalid.", code="tool_input_invalid"
                    ) from exc
                requires_write = subtask.kind is SubtaskKind.IMPLEMENT
            else:
                child_task_id = request["arguments"].get("child_task_id")
                if (
                    set(request["arguments"]) != {"child_task_id"}
                    or not isinstance(child_task_id, str)
                    or re.fullmatch(r"task_[a-f0-9]{32}", child_task_id) is None
                ):
                    raise ToolBrokerError(
                        "Subtask merge input is invalid.", code="tool_input_invalid"
                    )
                requires_write = True
            if requires_write and profile not in {
                    PolicyProfile.DEVELOP,
                    PolicyProfile.DEVELOP_NETWORKED,
                }:
                raise ToolBrokerError(
                    "Read-only tasks cannot modify or merge implementation.",
                    code="task_policy_readonly",
                )
            return None
        if tool_name == "query_documentation" and (
            os.getenv("CODING_WORKER_V16_ENABLED", "false").strip().lower()
            not in {"1", "true", "yes", "on"}
            or os.getenv(
                "CODING_WORKER_DOCUMENTATION_EGRESS_ENABLED", "false"
            ).strip().lower()
            not in {"1", "true", "yes", "on"}
        ):
            raise ToolBrokerError(
                "Controlled documentation egress is disabled.",
                code="tool_not_allowed",
            )
        if tool_name in v15_tools and os.getenv(
            "CODING_WORKER_V15_ENABLED", "false"
        ).strip().lower() not in {"1", "true", "yes", "on"}:
            raise ToolBrokerError("V15 tooling is disabled.", code="tool_not_allowed")
        if tool_name == "run_shell" and os.getenv(
            "CODING_WORKER_SHELL_ENABLED", "false"
        ).strip().lower() not in {"1", "true", "yes", "on"}:
            raise ToolBrokerError("V15 shell is disabled.", code="tool_not_allowed")
        if tool_name.startswith("code_") and os.getenv(
            "CODING_WORKER_CODE_INTELLIGENCE_ENABLED", "false"
        ).strip().lower() not in {"1", "true", "yes", "on"}:
            raise ToolBrokerError(
                "Code intelligence is disabled.", code="tool_not_allowed"
            )
        readonly = {
            "list_files",
            "read_file",
            "read_file_range",
            "glob_files",
            "search_text",
            "search_regex",
            "diff",
            "read_operation_output",
            "list_acceptance_checks",
            "service_status",
            "code_symbols",
            "code_definition",
            "code_references",
            "code_hover",
            "code_diagnostics",
            "update_plan",
            "update_todo",
            "request_user_input",
            "compact_context",
        }
        if tool_name in readonly:
            return None
        if tool_name in {
            "write_file",
            "delete_file",
            "apply_changeset",
            "run_check",
            "service_input",
            "stop_service",
        }:
            if profile not in {PolicyProfile.DEVELOP, PolicyProfile.DEVELOP_NETWORKED}:
                raise ToolBrokerError("Task policy is read-only.", code="approval_required")
            return None
        capability: CapabilityName
        approval_scope = request["arguments"]
        if tool_name == "run_shell":
            shell_scope = self._shell_approval_scope(
                operation_id, request["arguments"]
            )
            if (
                shell_scope.mode is ShellMode.MUTATE
                and profile not in {
                    PolicyProfile.DEVELOP,
                    PolicyProfile.DEVELOP_NETWORKED,
                }
            ):
                raise ToolBrokerError(
                    "Task policy is read-only.", code="task_policy_readonly"
                )
            capability = "shell"
            approval_scope = shell_scope.model_dump(mode="json")
        elif tool_name == "run_command":
            capability = "command"
        elif tool_name == "start_service":
            capability = "service"
        elif tool_name == "install_dependencies":
            if profile is not PolicyProfile.DEVELOP_NETWORKED:
                raise ToolBrokerError(
                    "Dependency installation requires networked development policy.",
                    code="approval_required",
                )
            policy = self._require_egress_policy()
            capability = "dependency_install"
            plan = self._dependency_plan(
                str(request["workspace_id"]), request["arguments"]
            )
            approval_scope = plan["approval_scope"]
        elif tool_name == "query_documentation":
            if profile is not PolicyProfile.DEVELOP_NETWORKED:
                raise ToolBrokerError(
                    "Documentation queries require networked development policy.",
                    code="approval_required",
                )
            capability = "documentation_query"
            plan = self._documentation_plan(request["arguments"])
            approval_scope = plan["approval_scope"]
        else:
            raise ToolBrokerError("Tool is not available.", code="tool_not_allowed")
        network_scope: dict[str, object] | None = None
        if tool_name == "install_dependencies":
            network_scope = policy.approval_scope(
                domains=plan["domains"], purpose="dependency-install"
            )
        elif tool_name == "query_documentation":
            policy = self._require_egress_policy()
            network_scope = policy.approval_scope(
                domains=plan["domains"], purpose="documentation-query"
            )
        if lease_id is None:
            self.store.create_approval(
                task_id=task_id,
                operation_id=operation_id,
                capability=capability,
                request=approval_scope,
            )
        if network_scope is not None and network_lease_id is None:
            network_operation_id = "network_" + hashlib.sha256(
                operation_id.encode("utf-8")
            ).hexdigest()[:32]
            self.store.create_approval(
                task_id=task_id,
                operation_id=network_operation_id,
                capability="network",
                request=network_scope,
            )
        if lease_id is None or (network_scope is not None and network_lease_id is None):
            task = self.store.get_task(task_id)
            if task.runtime_protocol is RuntimeProtocol.V17:
                current_turn = self.store.current_turn_transaction(task_id)
                if current_turn is None:
                    raise ToolBrokerError(
                        "V17 approval has no current turn.",
                        code="operation_turn_required",
                    )
                self.store.begin_turn_parking(
                    task_id=task_id,
                    turn_id=current_turn.turn_id,
                    barrier=TurnBarrier.APPROVAL,
                )
            elif task.state is TaskState.RUNNING:
                self.store.transition(task_id, TaskState.WAITING_APPROVAL)
            raise ToolBrokerError("Tool requires approval.", code="approval_required")
        lease = self.store.consume_lease(lease_id, task_id=task_id, capability=capability)
        if lease.scope != approval_scope:
            raise ToolBrokerError("Approval does not match the request.", code="lease_scope_mismatch")
        consumed_network_lease: CapabilityLease | None = None
        if network_scope is not None:
            consumed_network_lease = self.store.consume_lease(
                str(network_lease_id), task_id=task_id, capability="network"
            )
            self._require_egress_policy().validate_lease_scope(
                lease=consumed_network_lease,
                domains=network_scope["domains"],
                purpose=str(network_scope["purpose"]),
            )
        return consumed_network_lease

    async def _dispatch(
        self,
        task_id: str,
        workspace_id: str,
        operation_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        network_lease: CapabilityLease | None,
    ) -> dict[str, Any]:
        if tool_name in {
            "update_plan",
            "update_todo",
            "request_user_input",
            "compact_context",
        }:
            return self._dispatch_platform_interaction(
                task_id=task_id,
                operation_id=operation_id,
                tool_name=tool_name,
                arguments=arguments,
            )
        if tool_name == "create_subtask":
            if self.subtask_handler is None:
                raise ToolBrokerError(
                    "Subtask scheduler is unavailable.", code="subtask_unavailable"
                )
            try:
                request = SubtaskRequest.model_validate(arguments)
                relation = await self.subtask_handler(task_id, request)
            except ValidationError as exc:
                raise ToolBrokerError(
                    "Subtask input is invalid.", code="tool_input_invalid"
                ) from exc
            return {"subtask": relation.model_dump(mode="json")}
        if tool_name == "merge_subtask":
            if self.subtask_merge_handler is None:
                raise ToolBrokerError(
                    "Subtask merge scheduler is unavailable.",
                    code="subtask_unavailable",
                )
            relation = await self.subtask_merge_handler(
                task_id, str(arguments["child_task_id"]), operation_id
            )
            return {"subtask": relation.model_dump(mode="json")}
        if tool_name == "list_files":
            return {
                "tree_hash": self.workspace_broker.current_tree_hash(workspace_id),
                "entries": [
                    entry.model_dump(mode="json")
                    for entry in self.workspace_broker.tree(workspace_id)
                ]
            }
        if tool_name == "read_file":
            target = self._target(workspace_id, str(arguments.get("path", "")))
            content = target.read_bytes()
            if len(content) > self.max_output_bytes:
                raise ToolBrokerError("File is too large.", code="tool_output_too_large")
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                return {
                    "path": str(arguments["path"]),
                    "binary": True,
                    "size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "tree_hash": self.workspace_broker.current_tree_hash(workspace_id),
                }
            return {
                "path": str(arguments["path"]),
                "binary": False,
                "content": text,
                "sha256": hashlib.sha256(content).hexdigest(),
                "tree_hash": self.workspace_broker.current_tree_hash(workspace_id),
            }
        if tool_name == "read_file_range":
            return self._read_file_range(workspace_id, arguments)
        if tool_name == "glob_files":
            return self._glob_files(workspace_id, arguments)
        if tool_name == "search_text":
            needle = str(arguments.get("query", ""))
            if not needle or len(needle) > 512:
                raise ToolBrokerError("Search query is invalid.", code="tool_input_invalid")
            matches: list[dict[str, Any]] = []
            for entry in self.workspace_broker.tree(workspace_id):
                if entry.kind != "file" or entry.size > 1024 * 1024:
                    continue
                target = self._target(workspace_id, entry.display_path)
                try:
                    lines = target.read_text(encoding="utf-8").splitlines()
                except UnicodeDecodeError:
                    continue
                for number, line in enumerate(lines, 1):
                    if needle in line:
                        matches.append({"path": entry.display_path, "line": number, "text": line[:1000]})
                        if len(matches) >= 1000:
                            return {"matches": matches, "truncated": True}
            return {"matches": matches, "truncated": False}
        if tool_name == "search_regex":
            return self._search_regex(workspace_id, arguments)
        if tool_name == "diff":
            value = self.workspace_broker.diff(workspace_id, max_bytes=self.max_output_bytes)
            return {
                "diff": value.decode("utf-8", errors="replace"),
                "tree_hash": self.workspace_broker.current_tree_hash(workspace_id),
            }
        if tool_name == "read_operation_output":
            return self._read_operation_output(task_id, arguments)
        if tool_name == "list_acceptance_checks":
            task = self.store.get_task(task_id)
            return {
                "checks": [
                    {
                        "check_id": check.check_id,
                        "label": check.label,
                        "kind": check.kind,
                        "required": check.required,
                    }
                    for check in task.spec.acceptance.required_checks
                ]
            }
        if tool_name in {
            "code_symbols",
            "code_definition",
            "code_references",
            "code_hover",
            "code_diagnostics",
        }:
            return await self._run_code_intelligence(
                task_id=task_id,
                workspace_id=workspace_id,
                operation_id=operation_id,
                tool_name=tool_name,
                arguments=arguments,
            )
        if tool_name == "write_file":
            return self._write_file(workspace_id, arguments)
        if tool_name == "delete_file":
            return self._delete_file(workspace_id, arguments)
        if tool_name == "apply_changeset":
            outcome = self.changesets.apply(
                task_id=task_id,
                workspace_id=workspace_id,
                operation_id=operation_id,
                arguments=arguments,
            )
            return {"changeset": outcome.model_dump(mode="json")}
        if tool_name == "run_check":
            check_id = str(arguments.get("check_id", ""))
            task = self.store.get_task(task_id)
            if check_id not in {
                check.check_id for check in task.spec.acceptance.required_checks
            }:
                raise ToolBrokerError(
                    "Check is not in the task acceptance contract.",
                    code="check_not_allowed",
                )
            check = self.frozen_checks.get(check_id)
            if check is None:
                raise ToolBrokerError("Check is not registered.", code="check_not_found")
            return await self._run_process(
                task_id,
                workspace_id,
                check.argv,
                check.timeout_seconds,
                trusted=True,
                isolated=True,
            )
        if tool_name == "run_shell":
            return await self._run_shell(
                task_id=task_id,
                workspace_id=workspace_id,
                operation_id=operation_id,
                arguments=arguments,
            )
        if tool_name == "run_command":
            argv = arguments.get("argv")
            timeout = arguments.get("timeout_seconds", 300)
            if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
                raise ToolBrokerError("Command argv is invalid.", code="tool_input_invalid")
            if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 1800:
                raise ToolBrokerError("Command timeout is invalid.", code="tool_input_invalid")
            # Commands are observational compatibility tools. Execute them in a
            # disposable clone so test caches, formatter output, or failed
            # commands cannot mutate the authoritative Workspace outside the
            # atomic changeset path.
            return await self._run_process(
                task_id,
                workspace_id,
                tuple(argv),
                timeout,
                isolated=True,
            )
        if tool_name == "start_service":
            argv = arguments.get("argv")
            ttl = arguments.get("ttl_seconds", 900)
            preview_port = arguments.get("preview_port")
            if (
                not isinstance(argv, list)
                or not all(isinstance(item, str) for item in argv)
                or isinstance(ttl, bool)
                or not isinstance(ttl, int)
                or (
                    preview_port is not None
                    and (
                        isinstance(preview_port, bool)
                        or not isinstance(preview_port, int)
                        or not 1024 <= preview_port <= 65535
                    )
                )
            ):
                raise ToolBrokerError("Service input is invalid.", code="tool_input_invalid")
            if self.executor is not None:
                result = await self.executor.start_service(
                    task_id=task_id,
                    workspace_id=workspace_id,
                    argv=self._validate_argv(argv),
                    ttl_seconds=ttl,
                    preview_port=preview_port,
                )
                if preview_port is not None:
                    result["preview_url"] = (
                        f"/api/coding-worker/v1/tasks/{task_id}/services/"
                        f"{result['service_id']}/preview/"
                    )
                return result
            record = await self._require_process_manager().start(
                task_id=task_id,
                workspace_id=workspace_id,
                argv=self._validate_argv(argv),
                ttl_seconds=ttl,
            )
            return record.model_dump(mode="json")
        if tool_name == "service_status":
            if self.executor is not None:
                result = await self.executor.service_status(
                    task_id=task_id,
                    workspace_id=workspace_id,
                    service_id=str(arguments.get("service_id", "")),
                )
                return self._archive_service_output(task_id, result)
            record = self._require_process_manager().status(
                task_id=task_id,
                service_id=str(arguments.get("service_id", "")),
            )
            return record.model_dump(mode="json")
        if tool_name == "service_input":
            service_id = str(arguments.get("service_id", ""))
            data = arguments.get("data")
            if not isinstance(data, str):
                raise ToolBrokerError("Service input is invalid.", code="tool_input_invalid")
            if self.executor is not None:
                return await self.executor.service_input(
                    task_id=task_id,
                    workspace_id=workspace_id,
                    service_id=service_id,
                    data=data,
                )
            await self._require_process_manager().send_input(
                task_id=task_id,
                service_id=service_id,
                data=data.encode("utf-8"),
            )
            return {"service_id": service_id, "accepted": True}
        if tool_name == "stop_service":
            if self.executor is not None:
                result = await self.executor.stop_service(
                    task_id=task_id,
                    workspace_id=workspace_id,
                    service_id=str(arguments.get("service_id", "")),
                )
                return self._archive_service_output(task_id, result)
            record = await self._require_process_manager().interrupt(
                task_id=task_id,
                service_id=str(arguments.get("service_id", "")),
            )
            return record.model_dump(mode="json")
        if tool_name == "install_dependencies":
            plan = self._dependency_plan(workspace_id, arguments)
            if self.egress_proxy_url is None:
                raise ToolBrokerError("Egress proxy is unavailable.", code="network_disabled")
            if network_lease is None:
                raise ToolBrokerError(
                    "Network lease is unavailable.", code="network_lease_invalid"
                )
            authenticated_proxy = self._require_egress_policy().proxy_url(
                base_url=self.egress_proxy_url,
                lease=network_lease,
                task_id=task_id,
                purpose="dependency-install",
            )
            result = await self._run_process(
                task_id,
                workspace_id,
                plan["argv"],
                1800,
                environment_overrides={
                    "HTTPS_PROXY": authenticated_proxy,
                    "HTTP_PROXY": authenticated_proxy,
                    "NO_PROXY": "",
                    **plan["environment"],
                },
            )
            source = {
                "manager": plan["manager"],
                "action": plan["action"],
                "lock_sha256": plan["lock_sha256"],
                "registry_domains": list(plan["domains"]),
                "exit_code": result["exit_code"],
            }
            artifact = self.store.create_artifact(
                task_id=task_id,
                media_type="application/json",
                content=json.dumps(source, sort_keys=True, separators=(",", ":")).encode(),
                metadata={"kind": "dependency_source"},
            )
            return {**result, "source_artifact_id": artifact.artifact_id}
        if tool_name == "query_documentation":
            plan = self._documentation_plan(arguments)
            if self.egress_proxy_url is None or network_lease is None:
                raise ToolBrokerError(
                    "Documentation network lease is unavailable.",
                    code="network_lease_invalid",
                )
            authenticated_proxy = self._require_egress_policy().proxy_url(
                base_url=self.egress_proxy_url,
                lease=network_lease,
                task_id=task_id,
                purpose="documentation-query",
            )
            fetched = await self._fetch_documentation(
                plan["url"], proxy=authenticated_proxy
            )
            receipt = {
                "resource_id": plan["resource_id"],
                "document_path": plan["document_path"],
                "domain": plan["domains"][0],
                "sha256": hashlib.sha256(fetched["content"]).hexdigest(),
            }
            artifact = self.store.create_artifact(
                task_id=task_id,
                media_type="application/json",
                content=json.dumps(
                    receipt, sort_keys=True, separators=(",", ":")
                ).encode("utf-8"),
                metadata={"kind": "documentation_source"},
            )
            return {
                "resource_id": plan["resource_id"],
                "document_path": plan["document_path"],
                "content": fetched["content"].decode("utf-8", errors="replace"),
                "content_sha256": receipt["sha256"],
                "source_artifact_id": artifact.artifact_id,
            }
        raise ToolBrokerError("Tool is not available.", code="tool_not_allowed")

    def _dispatch_platform_interaction(
        self,
        *,
        task_id: str,
        operation_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if task.runtime_protocol is not RuntimeProtocol.V17:
            raise ToolBrokerError(
                "Platform interaction tools require V17.",
                code="tool_not_allowed",
            )
        turn = self.store.current_turn_transaction(task_id)
        if turn is None or turn.state not in {
            TurnTransactionState.OPEN,
            TurnTransactionState.RESUMING,
        }:
            raise ToolBrokerError("Turn is parked.", code="turn_parked")
        if tool_name == "update_plan":
            try:
                items = tuple(
                    WorkerPlanItem.model_validate(item)
                    for item in arguments.get("items", ())
                )
                explanation = arguments.get("explanation")
                if not items or len(items) > 128 or (
                    explanation is not None
                    and (not isinstance(explanation, str) or len(explanation) > 16_384)
                ):
                    raise ValueError
            except (TypeError, ValueError, ValidationError) as exc:
                raise ToolBrokerError(
                    "Plan input is invalid.", code="tool_input_invalid"
                ) from exc
            payload = {
                "explanation": explanation,
                "items": [item.model_dump(mode="json") for item in items],
            }
            entry = self.store.append_session_ledger(
                task_id,
                kind=SessionLedgerKind.PLAN,
                turn_id=turn.turn_id,
                payload=payload,
            )
            self.store.append_event(
                task_id,
                "plan_updated",
                {"turn_id": turn.turn_id, "sequence": entry.sequence},
            )
            return {"plan": payload, "sequence": entry.sequence}
        if tool_name == "update_todo":
            try:
                items = tuple(
                    WorkerTodoItem.model_validate(item)
                    for item in arguments.get("items", ())
                )
                if len(items) > 256 or len({item.todo_id for item in items}) != len(items):
                    raise ValueError
            except (TypeError, ValueError, ValidationError) as exc:
                raise ToolBrokerError(
                    "Todo input is invalid.", code="tool_input_invalid"
                ) from exc
            payload = {"items": [item.model_dump(mode="json") for item in items]}
            entry = self.store.append_session_ledger(
                task_id,
                kind=SessionLedgerKind.TODO,
                turn_id=turn.turn_id,
                payload=payload,
            )
            self.store.append_event(
                task_id,
                "todo_updated",
                {"turn_id": turn.turn_id, "sequence": entry.sequence},
            )
            return {"todo": payload, "sequence": entry.sequence}
        if tool_name == "request_user_input":
            try:
                question_id = str(arguments["question_id"])
                prompt = str(arguments["prompt"])
                options = tuple(
                    WorkerQuestionOption.model_validate(item)
                    for item in arguments.get("options", ())
                )
            except (KeyError, TypeError, ValueError, ValidationError) as exc:
                raise ToolBrokerError(
                    "Question input is invalid.", code="tool_input_invalid"
                ) from exc
            question = self.store.create_question(
                task_id=task_id,
                question_id=question_id,
                turn_id=turn.turn_id,
                prompt=prompt,
                options=options,
            )
            self.store.begin_turn_parking(
                task_id=task_id,
                turn_id=turn.turn_id,
                barrier=TurnBarrier.INPUT,
            )
            return {
                "control": "turn_parking",
                "barrier": TurnBarrier.INPUT.value,
                "question_id": question.question_id,
            }
        note = arguments.get("note")
        if note is not None and (not isinstance(note, str) or len(note) > 16_384):
            raise ToolBrokerError(
                "Compaction note is invalid.", code="tool_input_invalid"
            )
        self.store.begin_turn_parking(
            task_id=task_id,
            turn_id=turn.turn_id,
            barrier=TurnBarrier.COMPACTION,
        )
        return {
            "control": "turn_parking",
            "barrier": TurnBarrier.COMPACTION.value,
        }

    def _dependency_plan(
        self, workspace_id: str, arguments: Mapping[str, Any]
    ) -> dict[str, Any]:
        manager = arguments.get("manager")
        action = arguments.get("action")
        if arguments == {"manager": "npm", "action": "ci"}:
            lockfiles = ("package.json", "package-lock.json")
            argv = ("npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund")
            domains = ("registry.npmjs.org",)
            environment = {
                "npm_config_registry": "https://registry.npmjs.org/",
                "npm_config_ignore_scripts": "true",
                "npm_config_audit": "false",
                "npm_config_fund": "false",
            }
        elif arguments == {"manager": "uv", "action": "sync"}:
            lockfiles = ("pyproject.toml", "uv.lock")
            argv = ("uv", "sync", "--frozen")
            domains = ("pypi.org", "files.pythonhosted.org")
            environment = {
                "UV_DEFAULT_INDEX": "https://pypi.org/simple",
                "UV_NO_PROGRESS": "1",
            }
        elif arguments == {
            "manager": "pip",
            "action": "install",
            "requirements": "requirements.txt",
        }:
            lockfiles = ("requirements.txt",)
            argv = (
                "python",
                "-m",
                "pip",
                "install",
                "--require-hashes",
                "-r",
                "requirements.txt",
            )
            domains = ("pypi.org", "files.pythonhosted.org")
            environment = {
                "PIP_INDEX_URL": "https://pypi.org/simple",
                "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                "PIP_NO_INPUT": "1",
                "PIP_REQUIRE_HASHES": "1",
            }
        else:
            raise ToolBrokerError(
                "Dependency plan is not frozen.", code="dependency_plan_invalid"
            )
        contents: list[bytes] = []
        for path in lockfiles:
            try:
                target = self._target(workspace_id, path)
                content = target.read_bytes()
            except (OSError, WorkspaceError) as exc:
                raise ToolBrokerError(
                    "Dependency lock input is unavailable.",
                    code="dependency_plan_invalid",
                ) from exc
            if len(content) > 8 * 1024 * 1024:
                raise ToolBrokerError(
                    "Dependency lock input is too large.",
                    code="dependency_plan_invalid",
                )
            contents.append(content)
        if manager == "pip":
            self._validate_hashed_requirements(contents[0])
        digest = hashlib.sha256()
        for path, content in zip(lockfiles, contents, strict=True):
            digest.update(path.encode("utf-8") + b"\0")
            digest.update(hashlib.sha256(content).digest())
        return {
            "manager": manager,
            "action": action,
            "argv": argv,
            "domains": domains,
            "environment": environment,
            "lock_sha256": digest.hexdigest(),
            "approval_scope": {
                **dict(arguments),
                "lock_sha256": digest.hexdigest(),
            },
        }

    @staticmethod
    def _validate_hashed_requirements(content: bytes) -> None:
        try:
            text = content.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ToolBrokerError(
                "Requirements file is not UTF-8 text.",
                code="dependency_plan_invalid",
            ) from exc
        blocks: list[str] = []
        current = ""
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            current += (" " if current else "") + line.rstrip("\\").strip()
            if not line.endswith("\\"):
                blocks.append(current)
                current = ""
        if current or not blocks:
            raise ToolBrokerError(
                "Requirements file is not a complete frozen plan.",
                code="dependency_plan_invalid",
            )
        pattern = re.compile(
            r"^[A-Za-z0-9_.-]+==[^\s;]+(?:\s+--hash=sha256:[a-fA-F0-9]{64})+$"
        )
        if any(pattern.fullmatch(block) is None for block in blocks):
            raise ToolBrokerError(
                "Every Python requirement must be pinned with SHA-256 hashes.",
                code="dependency_plan_invalid",
            )

    def _documentation_plan(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if set(arguments) != {"resource_id", "document_path"}:
            raise ToolBrokerError(
                "Documentation query is invalid.", code="tool_input_invalid"
            )
        resource_id = arguments.get("resource_id")
        document_path = arguments.get("document_path")
        if not isinstance(resource_id, str) or not isinstance(document_path, str):
            raise ToolBrokerError(
                "Documentation query is invalid.", code="tool_input_invalid"
            )
        base = self.documentation_resources.get(resource_id)
        path = PurePosixPath(document_path)
        if (
            base is None
            or not document_path
            or len(document_path) > 1024
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
            or "?" in document_path
            or "#" in document_path
        ):
            raise ToolBrokerError(
                "Documentation resource is unavailable.",
                code="documentation_resource_unavailable",
            )
        parsed = urlsplit(base)
        assert parsed.hostname is not None
        encoded_path = "/".join(quote(part, safe="-._~") for part in path.parts)
        url = base.rstrip("/") + "/" + encoded_path
        return {
            "resource_id": resource_id,
            "document_path": path.as_posix(),
            "url": url,
            "domains": (parsed.hostname.lower(),),
            "approval_scope": {
                "resource_id": resource_id,
                "document_path": path.as_posix(),
            },
        }

    @staticmethod
    def _validate_documentation_resources(
        resources: Mapping[str, str]
    ) -> dict[str, str]:
        validated: dict[str, str] = {}
        for resource_id, base in resources.items():
            parsed = urlsplit(base)
            if (
                re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", resource_id)
                is None
                or parsed.scheme != "https"
                or parsed.hostname is None
                or parsed.username is not None
                or parsed.password is not None
                or parsed.port not in {None, 443}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("documentation resource catalog is invalid")
            validated[resource_id] = base.rstrip("/")
        return validated

    @staticmethod
    async def _fetch_documentation(url: str, *, proxy: str) -> dict[str, bytes]:
        timeout = httpx.Timeout(30.0, connect=10.0)
        async with httpx.AsyncClient(
            proxy=proxy,
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            async with client.stream(
                "GET",
                url,
                headers={
                    "Accept": "text/html,text/plain,application/json,application/xml",
                    "User-Agent": "ModelMirror-Coding-Worker/1",
                },
            ) as response:
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                if response.status_code != 200 or not (
                    content_type.startswith("text/")
                    or content_type
                    in {"application/json", "application/xml", "application/xhtml+xml"}
                ):
                    raise ToolBrokerError(
                        "Documentation response is unavailable.",
                        code="documentation_response_invalid",
                    )
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                    size += len(chunk)
                    if size > 1024 * 1024:
                        raise ToolBrokerError(
                            "Documentation response is too large.",
                            code="tool_output_too_large",
                        )
                    chunks.append(chunk)
        return {"content": b"".join(chunks)}

    async def _run_code_intelligence(
        self,
        *,
        task_id: str,
        workspace_id: str,
        operation_id: str,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        operation = CODE_INTELLIGENCE_TO_OPERATION[tool_name]
        positional = operation in {"definition", "references", "hover"}
        expected_keys = (
            {"entry_id", "line", "character"}
            if positional
            else {"entry_id"}
        )
        if set(arguments) != expected_keys:
            raise ToolBrokerError(
                "Code intelligence input is invalid.", code="tool_input_invalid"
            )
        entry_id = arguments.get("entry_id")
        line = arguments.get("line", 0)
        character = arguments.get("character", 0)
        if (
            not isinstance(entry_id, str)
            or isinstance(line, bool)
            or isinstance(character, bool)
            or not isinstance(line, int)
            or not isinstance(character, int)
            or not 0 <= line <= 10_000_000
            or not 0 <= character <= 10_000_000
        ):
            raise ToolBrokerError(
                "Code intelligence input is invalid.", code="tool_input_invalid"
            )
        entry, _ = self.workspace_broker.resolve_entry(
            workspace_id, entry_id, require_file=True
        )
        suffix = PurePosixPath(entry.display_path).suffix.lower()
        expected_language = {
            ".py": "python",
            ".ts": "typescript",
            ".tsx": "typescriptreact",
            ".js": "javascript",
            ".jsx": "javascriptreact",
        }.get(suffix)
        if expected_language is None:
            raise ToolBrokerError(
                "Code entry language is unsupported.",
                code="code_intelligence_unsupported",
            )
        if self.executor is None:
            raise ToolBrokerError(
                "Code intelligence executor is unavailable.",
                code="code_intelligence_unavailable",
            )
        tree_hash = self.workspace_broker.current_tree_hash(workspace_id)
        raw = await self.executor.code_intelligence(
            task_id=task_id,
            workspace_id=workspace_id,
            operation_id=operation_id,
            operation=operation,
            path=entry.display_path,
            line=line,
            character=character,
        )
        if self.workspace_broker.current_tree_hash(workspace_id) != tree_hash:
            raise ToolBrokerError(
                "Workspace changed during code intelligence.",
                code="workspace_tree_changed",
            )
        result = self._normalize_code_intelligence_result(
            task_id=task_id,
            workspace_id=workspace_id,
            entry_id=entry_id,
            display_path=entry.display_path,
            tree_hash=tree_hash,
            operation=operation,
            expected_language=expected_language,
            raw=raw,
        )
        if len(
            json.dumps(result, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ) > self.max_output_bytes:
            raise ToolBrokerError(
                "Code intelligence output is too large.",
                code="tool_output_too_large",
            )
        return result

    def _normalize_code_intelligence_result(
        self,
        *,
        task_id: str,
        workspace_id: str,
        entry_id: str,
        display_path: str,
        tree_hash: str,
        operation: str,
        expected_language: str,
        raw: Mapping[str, Any],
    ) -> dict[str, Any]:
        value_key = {
            "symbols": "symbols",
            "definition": "locations",
            "references": "locations",
            "hover": "hover",
            "diagnostics": "diagnostics",
        }[operation]
        if (
            not isinstance(raw, Mapping)
            or set(raw) != {"language", "path", value_key}
            or raw.get("language") != expected_language
            or raw.get("path") != display_path
        ):
            raise ToolBrokerError(
                "Code intelligence response is invalid.",
                code="code_intelligence_invalid_response",
            )
        result: dict[str, Any] = {
            "task_id": task_id,
            "entry_id": entry_id,
            "workspace_tree_hash": tree_hash,
            "operation": operation,
            "language": expected_language,
        }
        if operation == "symbols":
            symbols = raw.get("symbols")
            if not isinstance(symbols, list) or len(symbols) > 2000:
                raise self._invalid_code_response()
            result["symbols"] = [self._code_symbol(item) for item in symbols]
        elif operation in {"definition", "references"}:
            locations = raw.get("locations")
            if not isinstance(locations, list) or len(locations) > 2000:
                raise self._invalid_code_response()
            entries_by_path = {
                item.display_path: item.entry_id
                for item in self.workspace_broker.tree(workspace_id)
                if item.kind == "file"
            }
            normalized_locations = []
            for item in locations:
                if not isinstance(item, Mapping) or set(item) != {"path", "range"}:
                    raise self._invalid_code_response()
                location_entry_id = entries_by_path.get(item.get("path"))
                if location_entry_id is None:
                    raise self._invalid_code_response()
                try:
                    location = CodeLocation(
                        entry_id=location_entry_id,
                        range=CodeRange.model_validate(item.get("range")),
                    )
                except ValidationError as exc:
                    raise self._invalid_code_response() from exc
                normalized_locations.append(location.model_dump(mode="json"))
            result["locations"] = normalized_locations
        elif operation == "hover":
            hover = raw.get("hover")
            if hover is None:
                result["hover"] = None
            elif (
                isinstance(hover, Mapping)
                and set(hover) == {"text", "range"}
                and isinstance(hover.get("text"), str)
                and len(hover["text"]) <= 65_536
            ):
                range_value = hover.get("range")
                try:
                    normalized_range = (
                        CodeRange.model_validate(range_value).model_dump(mode="json")
                        if range_value is not None
                        else None
                    )
                except ValidationError as exc:
                    raise self._invalid_code_response() from exc
                result["hover"] = {
                    "text": hover["text"],
                    "range": normalized_range,
                }
            else:
                raise self._invalid_code_response()
        else:
            diagnostics = raw.get("diagnostics")
            if not isinstance(diagnostics, list) or len(diagnostics) > 2000:
                raise self._invalid_code_response()
            result["diagnostics"] = [
                self._worker_diagnostic(
                    task_id=task_id,
                    entry_id=entry_id,
                    tree_hash=tree_hash,
                    value=item,
                ).model_dump(mode="json")
                for item in diagnostics
            ]
        return result

    def _code_symbol(self, value: Any) -> dict[str, Any]:
        if (
            not isinstance(value, Mapping)
            or set(value)
            != {"name", "kind", "range", "selection_range", "container_name"}
            or not isinstance(value.get("name"), str)
            or not value["name"]
            or len(value["name"]) > 1024
            or isinstance(value.get("kind"), bool)
            or not isinstance(value.get("kind"), int)
            or (
                value.get("container_name") is not None
                and (
                    not isinstance(value.get("container_name"), str)
                    or len(value["container_name"]) > 1024
                )
            )
        ):
            raise self._invalid_code_response()
        try:
            range_value = CodeRange.model_validate(value.get("range"))
            selection = CodeRange.model_validate(value.get("selection_range"))
        except ValidationError as exc:
            raise self._invalid_code_response() from exc
        return {
            "name": value["name"],
            "kind": value["kind"],
            "range": range_value.model_dump(mode="json"),
            "selection_range": selection.model_dump(mode="json"),
            "container_name": value.get("container_name"),
        }

    def _worker_diagnostic(
        self,
        *,
        task_id: str,
        entry_id: str,
        tree_hash: str,
        value: Any,
    ) -> WorkerDiagnostic:
        if (
            not isinstance(value, Mapping)
            or set(value) != {"range", "severity", "code", "message"}
            or isinstance(value.get("severity"), bool)
            or not isinstance(value.get("severity"), int)
            or value["severity"] not in {1, 2, 3, 4}
            or not isinstance(value.get("message"), str)
            or not value["message"]
            or len(value["message"]) > 16_384
            or (
                value.get("code") is not None
                and (
                    not isinstance(value.get("code"), str)
                    or len(value["code"]) > 128
                )
            )
        ):
            raise self._invalid_code_response()
        try:
            code_range = CodeRange.model_validate(value.get("range"))
        except ValidationError as exc:
            raise self._invalid_code_response() from exc
        severity = {
            1: DiagnosticSeverity.ERROR,
            2: DiagnosticSeverity.WARNING,
            3: DiagnosticSeverity.INFORMATION,
            4: DiagnosticSeverity.HINT,
        }[value["severity"]]
        identity = json.dumps(
            {
                "entry_id": entry_id,
                "tree_hash": tree_hash,
                "range": code_range.model_dump(mode="json"),
                "severity": severity.value,
                "code": value.get("code"),
                "message": value["message"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return WorkerDiagnostic(
            diagnostic_id="diagnostic_"
            + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32],
            task_id=task_id,
            entry_id=entry_id,
            workspace_tree_hash=tree_hash,
            range=code_range,
            severity=severity,
            code=value.get("code"),
            message=value["message"],
            created_at=time(),
        )

    @staticmethod
    def _invalid_code_response() -> ToolBrokerError:
        return ToolBrokerError(
            "Code intelligence response is invalid.",
            code="code_intelligence_invalid_response",
        )

    @staticmethod
    def _shell_approval_scope(
        operation_id: str, arguments: Mapping[str, Any]
    ) -> ShellApprovalScope:
        if set(arguments) != {"script", "cwd", "mode", "timeout_seconds"}:
            raise ToolBrokerError("Shell input is invalid.", code="tool_input_invalid")
        script = arguments.get("script")
        if (
            not isinstance(script, str)
            or not script
            or "\x00" in script
            or len(script.encode("utf-8")) > 64 * 1024
        ):
            raise ToolBrokerError("Shell input is invalid.", code="tool_input_invalid")
        try:
            return ShellApprovalScope(
                operation_id=operation_id,
                script_sha256=hashlib.sha256(script.encode("utf-8")).hexdigest(),
                cwd=arguments.get("cwd"),
                mode=arguments.get("mode"),
                timeout_seconds=arguments.get("timeout_seconds"),
                network_scope_sha256=None,
            )
        except Exception as exc:
            raise ToolBrokerError(
                "Shell input is invalid.", code="tool_input_invalid"
            ) from exc

    async def _run_shell(
        self,
        *,
        task_id: str,
        workspace_id: str,
        operation_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if self.executor is None:
            raise ToolBrokerError(
                "Shell executor is unavailable.", code="shell_unavailable"
            )
        scope = self._shell_approval_scope(operation_id, arguments)
        base_tree_hash = self.workspace_broker.current_tree_hash(workspace_id)
        output = bytearray()

        async def persist_output(stream: str, chunk: bytes) -> None:
            if stream not in {"stdout", "stderr"} or not isinstance(chunk, bytes):
                raise ToolBrokerError(
                    "Shell output is invalid.", code="executor_invalid_response"
                )
            if len(output) + len(chunk) > self.max_output_bytes:
                raise ToolBrokerError(
                    "Shell output is too large.", code="tool_output_too_large"
                )
            output.extend(chunk)
            self.store.append_event(
                task_id,
                "operation_output",
                {
                    "operation_id": operation_id,
                    "stream": stream,
                    "text": chunk.decode("utf-8", errors="replace"),
                    "truncated": False,
                },
            )

        try:
            raw_result = await self.executor.run_shell(
                task_id=task_id,
                workspace_id=workspace_id,
                operation_id=operation_id,
                script=str(arguments["script"]),
                cwd=scope.cwd,
                mode=scope.mode.value,
                timeout_seconds=scope.timeout_seconds,
                output_callback=persist_output,
            )
        except Exception:
            if output:
                self._archive_shell_output(
                    task_id, operation_id, bytes(output), state="interrupted"
                )
            raise
        result = self._validate_shell_result(
            raw_result,
            expected_mode=scope.mode,
            expected_base_tree_hash=base_tree_hash,
            streamed_output=bytes(output),
        )
        output_artifact = self._archive_shell_output(
            task_id, operation_id, bytes(output), state="completed"
        )
        changes = result.pop("changes")
        changeset_expected = bool(result["changeset_eligible"] and changes)
        public_result = {
            **result,
            "output_artifact_id": output_artifact.artifact_id,
        }
        result_payload = {
            "changeset_expected": changeset_expected,
            "changes": changes if changeset_expected else [],
            "public_result": public_result,
        }
        result_artifact = self.store.create_artifact(
            task_id=task_id,
            media_type="application/json",
            content=json.dumps(
                result_payload, sort_keys=True, separators=(",", ":")
            ).encode("utf-8"),
            metadata={
                "kind": "shell_result",
                "operation_id": operation_id,
            },
        )
        public_result["result_artifact_id"] = result_artifact.artifact_id
        if changeset_expected:
            outcome = self.changesets.apply(
                task_id=task_id,
                workspace_id=workspace_id,
                operation_id=operation_id,
                arguments={
                    "base_tree_hash": base_tree_hash,
                    "changes": changes,
                },
            )
            public_result["changeset"] = outcome.model_dump(mode="json")
        return public_result

    def _validate_shell_result(
        self,
        value: Mapping[str, Any] | Any,
        *,
        expected_mode: ShellMode,
        expected_base_tree_hash: str,
        streamed_output: bytes,
    ) -> dict[str, Any]:
        result = self._json_object(value)
        required = {
            "mode",
            "exit_code",
            "reason",
            "base_tree_hash",
            "clone_tree_hash",
            "workspace_changed",
            "changeset_eligible",
            "changes",
            "change_summary",
            "output",
        }
        exit_code = result.get("exit_code")
        reason = result.get("reason")
        output = result.get("output")
        changes = result.get("changes")
        summary = result.get("change_summary")
        if (
            set(result) != required
            or result.get("mode") != expected_mode.value
            or isinstance(exit_code, bool)
            or not isinstance(exit_code, int)
            or not -255 <= exit_code <= 255
            or (reason is not None and (not isinstance(reason, str) or len(reason) > 128))
            or result.get("base_tree_hash") != expected_base_tree_hash
            or re.fullmatch(r"[a-f0-9]{64}", str(result.get("clone_tree_hash", "")))
            is None
            or not isinstance(result.get("workspace_changed"), bool)
            or not isinstance(result.get("changeset_eligible"), bool)
            or not isinstance(changes, list)
            or len(changes) > 128
            or not isinstance(summary, dict)
            or not isinstance(output, str)
            or output != streamed_output.decode("utf-8", errors="replace")
            or (
                result.get("changeset_eligible") is True
                and (
                    expected_mode is not ShellMode.MUTATE
                    or exit_code != 0
                    or reason is not None
                )
            )
            or (result.get("changeset_eligible") is not True and bool(changes))
        ):
            raise ToolBrokerError(
                "Shell executor response is invalid.",
                code="executor_invalid_response",
            )
        summary_paths = self._validate_shell_change_summary(
            summary, changes, eligible=result["changeset_eligible"] is True
        )
        if result["workspace_changed"] != bool(summary_paths):
            raise ToolBrokerError(
                "Shell executor response is invalid.",
                code="executor_invalid_response",
            )
        result.pop("output")
        return result

    @staticmethod
    def _validate_shell_change_summary(
        summary: dict[str, Any],
        changes: list[Any],
        *,
        eligible: bool,
    ) -> set[str]:
        if set(summary) != {"added", "deleted", "modified", "violations"}:
            raise ToolBrokerError(
                "Shell change summary is invalid.", code="executor_invalid_response"
            )
        paths: dict[str, list[str]] = {}
        for kind in ("added", "deleted", "modified"):
            value = summary.get(kind)
            if (
                not isinstance(value, list)
                or len(value) > 256
                or not all(isinstance(path, str) and path for path in value)
                or len(value) != len(set(value))
            ):
                raise ToolBrokerError(
                    "Shell change summary is invalid.",
                    code="executor_invalid_response",
                )
            paths[kind] = value
        violations = summary.get("violations")
        if not isinstance(violations, list) or len(violations) > 128:
            raise ToolBrokerError(
                "Shell change summary is invalid.", code="executor_invalid_response"
            )
        summary_paths = set(paths["added"]) | set(paths["deleted"]) | set(
            paths["modified"]
        )
        if any(
            set(paths[left]) & set(paths[right])
            for left, right in (
                ("added", "deleted"),
                ("added", "modified"),
                ("deleted", "modified"),
            )
        ):
            raise ToolBrokerError(
                "Shell change summary is invalid.", code="executor_invalid_response"
            )
        if not eligible:
            return summary_paths | {
                str(item.get("path"))
                for item in violations
                if isinstance(item, dict) and isinstance(item.get("path"), str)
            }
        if violations:
            raise ToolBrokerError(
                "Shell executor response is invalid.",
                code="executor_invalid_response",
            )
        change_paths: set[str] = set()
        for change in changes:
            if not isinstance(change, dict) or change.get("kind") not in {
                "write",
                "delete",
            }:
                raise ToolBrokerError(
                    "Shell executor response is invalid.",
                    code="executor_invalid_response",
                )
            path = change.get("path")
            if not isinstance(path, str) or not path or path in change_paths:
                raise ToolBrokerError(
                    "Shell executor response is invalid.",
                    code="executor_invalid_response",
                )
            change_paths.add(path)
        if change_paths != summary_paths:
            raise ToolBrokerError(
                "Shell executor response is invalid.",
                code="executor_invalid_response",
            )
        return summary_paths

    def _archive_shell_output(
        self,
        task_id: str,
        operation_id: str,
        output: bytes,
        *,
        state: str,
    ) -> Any:
        return self.store.create_artifact(
            task_id=task_id,
            media_type="text/plain; charset=utf-8",
            content=output,
            metadata={
                "kind": "shell_output",
                "operation_id": operation_id,
                "state": state,
            },
        )

    def _reconcile_shell(self, operation_id: str) -> ToolResult:
        operation = self.store.get_operation(operation_id)
        artifacts = [
            item
            for item in self.store.list_artifacts(operation.task_id)
            if item.metadata.get("kind") == "shell_result"
            and item.metadata.get("operation_id") == operation_id
        ]
        if not artifacts:
            failed = self.store.transition_operation(
                operation_id,
                OperationState.FAILED,
                result={"code": "shell_result_unavailable"},
                expected_state=OperationState.UNKNOWN,
            )
            return ToolResult(
                operation_id=operation_id,
                tool_name=operation.tool_name,
                state=failed.state,
                data=failed.result or {},
            )
        if len(artifacts) != 1:
            raise ToolBrokerError(
                "Shell result binding is ambiguous.",
                code="operation_result_unknown",
            )
        try:
            payload = json.loads(
                self.store.read_artifact(
                    artifacts[0].artifact_id, task_id=operation.task_id
                )
            )
            public_result = self._json_object(payload["public_result"])
            changeset_expected = payload["changeset_expected"] is True
        except Exception as exc:
            raise ToolBrokerError(
                "Shell result is unavailable.", code="operation_result_unknown"
            ) from exc
        public_result["result_artifact_id"] = artifacts[0].artifact_id
        workspace_id = str(operation.request["workspace_id"])
        if changeset_expected:
            if not self.changesets.has_transaction(
                workspace_id=workspace_id, operation_id=operation_id
            ):
                failed = self.store.transition_operation(
                    operation_id,
                    OperationState.FAILED,
                    result={"code": "shell_changeset_not_applied"},
                    expected_state=OperationState.UNKNOWN,
                )
                return ToolResult(
                    operation_id=operation_id,
                    tool_name=operation.tool_name,
                    state=failed.state,
                    data=failed.result or {},
                )
            try:
                outcome = self.changesets.reconcile(
                    task_id=operation.task_id,
                    workspace_id=workspace_id,
                    operation_id=operation_id,
                )
            except ChangesetError as exc:
                if exc.code == "changeset_rolled_back":
                    self.store.transition_operation(
                        operation_id,
                        OperationState.FAILED,
                        result={"code": exc.code},
                        expected_state=OperationState.UNKNOWN,
                    )
                raise ToolBrokerError(
                    "Shell changeset reconciliation failed.", code=exc.code
                ) from exc
            public_result["changeset"] = outcome.model_dump(mode="json")
        resolved = self.store.transition_operation(
            operation_id,
            OperationState.COMPLETED,
            result=public_result,
            expected_state=OperationState.UNKNOWN,
        )
        if changeset_expected:
            self.changesets.finalize(
                task_id=operation.task_id,
                workspace_id=workspace_id,
                operation_id=operation_id,
            )
        return ToolResult(
            operation_id=operation_id,
            tool_name=operation.tool_name,
            state=resolved.state,
            data=resolved.result or {},
        )

    @staticmethod
    def _tool_can_publish_changeset(tool_name: str) -> bool:
        return tool_name in {"apply_changeset", "run_shell"}

    def _subtask_merge_is_settled(
        self, task_id: str, child_task_id: str, operation_id: str
    ) -> bool:
        relation = self.store.subtask_for_child(child_task_id)
        return (
            relation is not None
            and relation.parent_task_id == task_id
            and relation.merge_operation_id == operation_id
            and relation.merge_state
            in {SubtaskMergeState.MERGED, SubtaskMergeState.CONFLICTED}
        )

    @staticmethod
    def _result_has_changeset(
        tool_name: str, result: Mapping[str, Any] | None
    ) -> bool:
        return tool_name in {"apply_changeset", "run_shell"} and isinstance(
            (result or {}).get("changeset"), dict
        )

    def _require_process_manager(self) -> BackgroundProcessManager:
        if self.process_manager is None:
            raise ToolBrokerError("Service manager is unavailable.", code="service_unavailable")
        return self.process_manager

    def _archive_service_output(
        self, task_id: str, result: dict[str, Any]
    ) -> dict[str, Any]:
        output = result.pop("output", None)
        if not isinstance(output, str):
            return result
        service_id = str(result.get("service_id", ""))
        existing = next(
            (
                item
                for item in self.store.list_artifacts(task_id)
                if item.metadata.get("kind") == "service_output"
                and item.metadata.get("service_id") == service_id
            ),
            None,
        )
        artifact = existing or self.store.create_artifact(
            task_id=task_id,
            media_type="text/plain; charset=utf-8",
            content=output.encode("utf-8"),
            metadata={"kind": "service_output", "service_id": service_id},
        )
        return {**result, "output_artifact_id": artifact.artifact_id}

    def _require_egress_policy(self) -> EgressPolicy:
        if self.egress_policy is None:
            raise NetworkPolicyError("Worker network access is disabled.", code="network_disabled")
        return self.egress_policy

    def _read_file_range(
        self, workspace_id: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        path = str(arguments.get("path", ""))
        start = arguments.get("start_line", 1)
        end = arguments.get("end_line", 200)
        if (
            isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, int)
            or not isinstance(end, int)
            or not 1 <= start <= end
            or end - start >= 1000
        ):
            raise ToolBrokerError("Line range is invalid.", code="tool_input_invalid")
        target = self._target(workspace_id, path)
        if not target.is_file() or target.is_symlink():
            raise ToolBrokerError("Target is not a regular file.", code="workspace_changed")
        content = target.read_bytes()
        if len(content) > MAX_WRITE_BYTES:
            raise ToolBrokerError("File is too large.", code="tool_output_too_large")
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ToolBrokerError(
                "Binary file ranges are unavailable.", code="preview_unavailable"
            ) from exc
        lines = text.splitlines(keepends=True)
        selected = "".join(lines[start - 1 : end])
        if len(selected.encode("utf-8")) > self.max_output_bytes:
            raise ToolBrokerError("Range output is too large.", code="tool_output_too_large")
        return {
            "path": path,
            "start_line": start,
            "end_line": min(end, len(lines)),
            "total_lines": len(lines),
            "content": selected,
            "sha256": hashlib.sha256(content).hexdigest(),
        }

    def _read_operation_output(
        self, task_id: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        operation_id = arguments.get("operation_id")
        after = arguments.get("after", 0)
        if (
            not isinstance(operation_id, str)
            or not operation_id
            or len(operation_id) > 128
            or isinstance(after, bool)
            or not isinstance(after, int)
            or after < 0
        ):
            raise ToolBrokerError(
                "Operation output input is invalid.", code="tool_input_invalid"
            )
        try:
            operation = self.store.get_operation(operation_id)
        except Exception as exc:
            raise ToolBrokerError(
                "Operation output was not found.", code="operation_not_found"
            ) from exc
        if operation.task_id != task_id:
            raise ToolBrokerError(
                "Operation output was not found.", code="operation_not_found"
            )
        chunks: list[dict[str, Any]] = []
        cursor = after
        scanned = 0
        output_size = 0
        truncated = False
        while len(chunks) < 256 and scanned < 10_000:
            events = self.store.list_events(task_id, after=cursor, limit=1000)
            if not events:
                break
            cursor = events[-1].sequence
            scanned += len(events)
            for event in events:
                if (
                    event.type != "operation_output"
                    or event.payload.get("operation_id") != operation_id
                ):
                    continue
                text = event.payload.get("text")
                stream = event.payload.get("stream")
                if not isinstance(text, str) or stream not in {"stdout", "stderr"}:
                    continue
                encoded_size = len(text.encode("utf-8"))
                if output_size + encoded_size > self.max_output_bytes:
                    truncated = True
                    break
                output_size += encoded_size
                chunks.append(
                    {
                        "sequence": event.sequence,
                        "stream": stream,
                        "text": text,
                    }
                )
                if len(chunks) >= 256:
                    truncated = True
                    break
            if truncated or len(events) < 1000:
                break
        return {
            "operation_id": operation_id,
            "chunks": chunks,
            "next_after": chunks[-1]["sequence"] if chunks else after,
            "truncated": truncated,
        }

    def _glob_files(
        self, workspace_id: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        pattern = self._validate_glob(str(arguments.get("pattern", "")))
        matches = [
            entry.model_dump(mode="json")
            for entry in self.workspace_broker.tree(workspace_id)
            if self._glob_matches(entry.display_path, pattern)
        ]
        return {"entries": matches[:2000], "truncated": len(matches) > 2000}

    def _search_regex(
        self, workspace_id: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        pattern = str(arguments.get("pattern", ""))
        file_glob = self._validate_glob(str(arguments.get("glob", "**/*")))
        case_sensitive = arguments.get("case_sensitive", True)
        if (
            not pattern
            or len(pattern) > 256
            or not isinstance(case_sensitive, bool)
            or re.search(r"\\[1-9]|\(\?", pattern)
            or re.search(r"\([^)]*(?:[*+{]|\|)[^)]*\)[*+{]", pattern)
        ):
            raise ToolBrokerError("Regex pattern is unsafe.", code="tool_input_invalid")
        try:
            expression = re.compile(pattern, 0 if case_sensitive else re.IGNORECASE)
        except re.error as exc:
            raise ToolBrokerError(
                "Regex pattern is invalid.", code="tool_input_invalid"
            ) from exc
        matches: list[dict[str, Any]] = []
        for entry in self.workspace_broker.tree(workspace_id):
            if (
                entry.kind != "file"
                or entry.size > 1024 * 1024
                or not self._glob_matches(entry.display_path, file_glob)
            ):
                continue
            target = self._target(workspace_id, entry.display_path)
            try:
                lines = target.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                continue
            for number, line in enumerate(lines, 1):
                bounded = line[:16_384]
                match = expression.search(bounded)
                if match is None:
                    continue
                matches.append(
                    {
                        "path": entry.display_path,
                        "line": number,
                        "start": match.start(),
                        "end": match.end(),
                        "text": bounded[:1000],
                    }
                )
                if len(matches) >= 1000:
                    return {"matches": matches, "truncated": True}
        return {"matches": matches, "truncated": False}

    @staticmethod
    def _validate_glob(value: str) -> str:
        path = PurePosixPath(value)
        if (
            not value
            or len(value) > 256
            or "\\" in value
            or path.is_absolute()
            or ".." in path.parts
            or ".git" in path.parts
            or "\x00" in value
        ):
            raise ToolBrokerError("Glob pattern is invalid.", code="tool_input_invalid")
        return value

    @staticmethod
    def _glob_matches(path: str, pattern: str) -> bool:
        return fnmatch.fnmatchcase(path, pattern) or (
            pattern.startswith("**/")
            and fnmatch.fnmatchcase(path, pattern.removeprefix("**/"))
        )

    def _write_file(self, workspace_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
        path = str(arguments.get("path", ""))
        content = arguments.get("content")
        expected = str(arguments.get("content_sha256", ""))
        if not isinstance(content, str):
            raise ToolBrokerError("File content must be text.", code="tool_input_invalid")
        encoded = content.encode("utf-8")
        actual = hashlib.sha256(encoded).hexdigest()
        if len(encoded) > MAX_WRITE_BYTES or expected != actual:
            raise ToolBrokerError("File content binding is invalid.", code="tool_input_invalid")
        target = self._target(workspace_id, path, create_parents=True)
        if target.exists() and (not target.is_file() or target.is_symlink()):
            raise ToolBrokerError("Target is not a regular file.", code="workspace_changed")
        repository = self.workspace_broker.repository_path(workspace_id)
        stage_root = repository.parent / "broker-stage"
        stage_root.mkdir(exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="write-", dir=stage_root)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            self.workspace_broker.apply_slot_owner(workspace_id, Path(temporary))
            os.replace(temporary, target)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
        return {"path": path, "sha256": actual, "size": len(encoded)}

    def _delete_file(self, workspace_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
        path = str(arguments.get("path", ""))
        target = self._target(workspace_id, path)
        expected = str(arguments.get("expected_sha256", ""))
        if not target.is_file() or target.is_symlink():
            raise ToolBrokerError("Target is not a regular file.", code="workspace_changed")
        if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            raise ToolBrokerError("Target changed before deletion.", code="workspace_changed")
        target.unlink()
        return {"path": path, "deleted": True}

    async def _run_process(
        self,
        task_id: str,
        workspace_id: str,
        argv: Sequence[str],
        timeout_seconds: int,
        *,
        trusted: bool = False,
        isolated: bool = False,
        environment_overrides: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        normalized = self._validate_argv(argv, trusted=trusted)
        if self.executor is not None:
            return await self.executor.run_process(
                task_id=task_id,
                workspace_id=workspace_id,
                argv=normalized,
                timeout_seconds=timeout_seconds,
                isolated=isolated,
                environment_overrides=environment_overrides,
            )
        repository = self.workspace_broker.repository_path(workspace_id)
        execution_root: Path | None = None
        execution_repository = repository
        try:
            if isolated:
                self.workspace_broker.tree(workspace_id)
                execution_root = (
                    self.workspace_broker.root
                    / "harness-runs"
                    / f"run_{uuid.uuid4().hex}"
                )
                execution_root.parent.mkdir(exist_ok=True)
                shutil.copytree(
                    repository,
                    execution_root,
                    ignore=shutil.ignore_patterns(".git"),
                )
                execution_repository = execution_root
            environment = self._safe_environment(execution_repository)
            environment.update(environment_overrides or {})
            process = await asyncio.create_subprocess_exec(
                *normalized,
                cwd=execution_repository,
                env=environment,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                output = await asyncio.wait_for(
                    self._collect_bounded_output(process), timeout=timeout_seconds
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                raise ToolBrokerError("Command timed out.", code="command_timeout")
            except asyncio.CancelledError:
                process.kill()
                await process.wait()
                raise
            return {
                "argv": list(normalized),
                "exit_code": int(process.returncode or 0),
                "output": output.decode("utf-8", errors="replace"),
            }
        finally:
            if execution_root is not None:
                self.workspace_broker._remove_tree(execution_root)

    async def _collect_bounded_output(
        self, process: asyncio.subprocess.Process
    ) -> bytes:
        if process.stdout is None:
            raise ToolBrokerError("Command output is unavailable.", code="command_failed")
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = await process.stdout.read(64 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > self.max_output_bytes:
                process.kill()
                await process.wait()
                raise ToolBrokerError(
                    "Command output is too large.", code="tool_output_too_large"
                )
            chunks.append(chunk)
        await process.wait()
        return b"".join(chunks)

    def _target(self, workspace_id: str, value: str, *, create_parents: bool = False) -> Path:
        path = PurePosixPath(value)
        if (
            not value
            or "\\" in value
            or path.is_absolute()
            or any(part in {"", ".", "..", ".git"} for part in path.parts)
        ):
            raise ToolBrokerError("Workspace path is invalid.", code="workspace_path_invalid")
        repository = self.workspace_broker.repository_path(workspace_id)
        parent = repository
        for part in path.parts[:-1]:
            parent = parent / part
            if parent.exists():
                if not parent.is_dir() or parent.is_symlink():
                    raise ToolBrokerError("Workspace path changed.", code="workspace_changed")
            elif create_parents:
                parent.mkdir()
                self.workspace_broker.apply_slot_owner(workspace_id, parent)
            else:
                raise ToolBrokerError("Workspace path was not found.", code="entry_not_found")
        target = parent / path.name
        if target.parent.resolve() != parent.resolve() or repository not in target.parents:
            raise ToolBrokerError("Workspace path escaped the task.", code="workspace_path_invalid")
        return target

    @staticmethod
    def _validate_argv(argv: Sequence[str], *, trusted: bool = False) -> tuple[str, ...]:
        if not argv or len(argv) > 64 or any(not item or "\x00" in item for item in argv):
            raise ToolBrokerError("Command argv is invalid.", code="tool_input_invalid")
        executable = Path(argv[0]).name.lower()
        if executable.endswith(".exe"):
            executable = executable[:-4]
        if executable in ALWAYS_DENIED_EXECUTABLES or (
            Path(argv[0]).is_absolute() and not trusted
        ):
            raise ToolBrokerError("Command is always denied.", code="command_denied")
        if executable == "git" and len(argv) > 1 and argv[1].lower() in DENIED_GIT_SUBCOMMANDS:
            raise ToolBrokerError("Git remote operations are denied.", code="command_denied")
        for item in argv[1:]:
            path = PurePosixPath(item.replace("\\", "/"))
            if Path(item).is_absolute() or ".." in path.parts:
                raise ToolBrokerError("Command contains a host path.", code="command_denied")
        return tuple(argv)

    @staticmethod
    def _safe_environment(repository: Path) -> dict[str, str]:
        environment = {
            "HOME": str(repository.parent / "home"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
        }
        if os.name == "nt":
            environment["PATH"] = os.environ.get("PATH", "")
            environment["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "C:\\Windows")
        Path(environment["HOME"]).mkdir(exist_ok=True)
        return environment

    @staticmethod
    def _validate_proxy_url(value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if (
            parsed.scheme != "http"
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("egress proxy URL is invalid")
        return value.rstrip("/")

    @staticmethod
    def _intent_sha256(tool_name: str, request: dict[str, Any]) -> str:
        encoded = json.dumps(
            {"tool": tool_name, "request": request},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _json_object(value: Mapping[str, Any] | Any) -> dict[str, Any]:
        try:
            encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            decoded = json.loads(encoded)
        except (TypeError, ValueError) as exc:
            raise ToolBrokerError("Tool input is not JSON.", code="tool_input_invalid") from exc
        if not isinstance(decoded, dict):
            raise ToolBrokerError("Tool input must be an object.", code="tool_input_invalid")
        return decoded
