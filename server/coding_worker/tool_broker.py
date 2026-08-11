from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, Protocol
from urllib.parse import urlsplit

from pydantic import Field

from .contracts import (
    CapabilityLease,
    CapabilityName,
    OperationState,
    PolicyProfile,
    StrictModel,
    TaskState,
)
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


class ToolBroker:
    """The sole side-effect boundary exposed to a coding provider.

    Requests contain only task/workspace-relative values. Commands are argv arrays,
    never shell strings, and execute with a minimal credential-free environment.
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
        operation = self.store.create_operation(
            task_id=task_id,
            operation_id=operation_id,
            tool_name=tool_name,
            intent_sha256=intent_sha256,
            request=request,
        )
        if operation.state is OperationState.COMPLETED:
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
                tool_name,
                request["arguments"],
                network_lease=network_lease,
            )
            completed = self.store.transition_operation(
                operation_id,
                OperationState.COMPLETED,
                result=data,
                expected_state=OperationState.RUNNING,
            )
            return ToolResult(
                operation_id=operation_id,
                tool_name=tool_name,
                state=completed.state,
                data=completed.result or {},
            )
        except (
            ToolBrokerError,
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
            if not awaiting_approval and current.state in {
                OperationState.PREPARED,
                OperationState.RUNNING,
            }:
                self.store.transition_operation(
                    operation_id,
                    OperationState.FAILED,
                    result={"code": getattr(exc, "code", "tool_failed")},
                    expected_state=current.state,
                )
            if isinstance(exc, ToolBrokerError):
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
        readonly = {
            "list_files",
            "read_file",
            "search_text",
            "diff",
            "list_acceptance_checks",
            "service_status",
        }
        if tool_name in readonly:
            return None
        if tool_name in {
            "write_file",
            "delete_file",
            "run_check",
            "service_input",
            "stop_service",
        }:
            if profile not in {PolicyProfile.DEVELOP, PolicyProfile.DEVELOP_NETWORKED}:
                raise ToolBrokerError("Task policy is read-only.", code="approval_required")
            return None
        capability: CapabilityName
        if tool_name == "run_command":
            capability = "command"
        elif tool_name == "start_service":
            capability = "service"
        elif tool_name == "install_dependencies":
            if profile is not PolicyProfile.DEVELOP_NETWORKED:
                raise ToolBrokerError(
                    "Dependency installation requires networked development policy.",
                    code="approval_required",
                )
            capability = "dependency_install"
        else:
            raise ToolBrokerError("Tool is not available.", code="tool_not_allowed")
        network_scope: dict[str, object] | None = None
        if tool_name == "install_dependencies":
            policy = self._require_egress_policy()
            network_scope = policy.approval_scope(
                domains=("registry.npmjs.org",), purpose="dependency-install"
            )
        if lease_id is None:
            self.store.create_approval(
                task_id=task_id,
                operation_id=operation_id,
                capability=capability,
                request=request["arguments"],
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
            if task.state is TaskState.RUNNING:
                self.store.transition(task_id, TaskState.WAITING_APPROVAL)
            raise ToolBrokerError("Tool requires approval.", code="approval_required")
        lease = self.store.consume_lease(lease_id, task_id=task_id, capability=capability)
        if lease.scope != request["arguments"]:
            raise ToolBrokerError("Approval does not match the request.", code="lease_scope_mismatch")
        consumed_network_lease: CapabilityLease | None = None
        if network_scope is not None:
            consumed_network_lease = self.store.consume_lease(
                str(network_lease_id), task_id=task_id, capability="network"
            )
            self._require_egress_policy().validate_lease_scope(
                lease=consumed_network_lease,
                domains=("registry.npmjs.org",),
                purpose="dependency-install",
            )
        return consumed_network_lease

    async def _dispatch(
        self,
        task_id: str,
        workspace_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        network_lease: CapabilityLease | None,
    ) -> dict[str, Any]:
        if tool_name == "list_files":
            return {
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
                }
            return {"path": str(arguments["path"]), "binary": False, "content": text}
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
        if tool_name == "diff":
            value = self.workspace_broker.diff(workspace_id, max_bytes=self.max_output_bytes)
            return {"diff": value.decode("utf-8", errors="replace")}
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
        if tool_name == "write_file":
            return self._write_file(workspace_id, arguments)
        if tool_name == "delete_file":
            return self._delete_file(workspace_id, arguments)
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
        if tool_name == "run_command":
            argv = arguments.get("argv")
            timeout = arguments.get("timeout_seconds", 300)
            if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
                raise ToolBrokerError("Command argv is invalid.", code="tool_input_invalid")
            if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 1800:
                raise ToolBrokerError("Command timeout is invalid.", code="tool_input_invalid")
            return await self._run_process(task_id, workspace_id, tuple(argv), timeout)
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
            if arguments != {"manager": "npm", "action": "ci"}:
                raise ToolBrokerError(
                    "Only frozen npm lockfile installation is supported.",
                    code="dependency_plan_invalid",
                )
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
                ("npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"),
                1800,
                environment_overrides={
                    "HTTPS_PROXY": authenticated_proxy,
                    "HTTP_PROXY": authenticated_proxy,
                    "NO_PROXY": "",
                    "npm_config_registry": "https://registry.npmjs.org/",
                    "npm_config_ignore_scripts": "true",
                    "npm_config_audit": "false",
                    "npm_config_fund": "false",
                },
            )
            source = {
                "manager": "npm",
                "action": "ci",
                "registry_domains": ["registry.npmjs.org"],
                "exit_code": result["exit_code"],
            }
            artifact = self.store.create_artifact(
                task_id=task_id,
                media_type="application/json",
                content=json.dumps(source, sort_keys=True, separators=(",", ":")).encode(),
                metadata={"kind": "dependency_source"},
            )
            return {**result, "source_artifact_id": artifact.artifact_id}
        raise ToolBrokerError("Tool is not available.", code="tool_not_allowed")

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
