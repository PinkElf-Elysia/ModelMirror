from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

try:
    from server.expert_team_agency import AGENCY_UPSTREAM_PROJECT
    from server.meta_agent.schemas import MetaPlannerTask, MetaPlannerTaskPlan
    from server.orchestration_worker import (
        AGENCY_EXECUTION_PROTOCOL,
        AgencyAgentDefinition,
        AgencyExecutionClient,
        AgencyModelRequest,
        AgencyModelResponse,
        AgencyWorkerError,
        adapt_expert_catalog,
    )
    from server.workflow_native.schemas import NativeWorkflowDefinition
    from server.workflow_native.validate import node_kind, validate_workflow_graph
    from server.xpert_runtime import RunRegistry, WorkflowExecutionStore
except ModuleNotFoundError:
    from expert_team_agency import AGENCY_UPSTREAM_PROJECT
    from meta_agent.schemas import MetaPlannerTask, MetaPlannerTaskPlan
    from orchestration_worker import (
        AGENCY_EXECUTION_PROTOCOL,
        AgencyAgentDefinition,
        AgencyExecutionClient,
        AgencyModelRequest,
        AgencyModelResponse,
        AgencyWorkerError,
        adapt_expert_catalog,
    )
    from workflow_native.schemas import NativeWorkflowDefinition
    from workflow_native.validate import node_kind, validate_workflow_graph
    from xpert_runtime import RunRegistry, WorkflowExecutionStore


MAX_EXECUTION_STEPS = 6
MAX_EXECUTION_CONCURRENCY = 2
MAX_EXECUTION_MODEL_CALLS = 10
MAX_EXECUTION_TOKENS = 4096
MAX_EXECUTION_SECONDS = 900
MAX_ACTIVE_EXECUTIONS = 2
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
VARIABLE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
TEMPLATE_PATTERN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")

AgencyModelRunner = Callable[
    [AgencyModelRequest], Awaitable[AgencyModelResponse | str]
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExpertTeamDagRunRequest(StrictModel):
    goal: str = Field(min_length=10, max_length=20_000)
    plan: MetaPlannerTaskPlan
    workflow: NativeWorkflowDefinition
    model_id: str = Field(min_length=1, max_length=300)
    capability_snapshot_version: str = Field(min_length=1, max_length=200)
    capability_snapshot_hash: str = Field(min_length=1, max_length=256)
    upstream_revision: str = Field(min_length=7, max_length=80)


class AgencyExecutionCapabilities(BaseModel):
    enabled: bool = False
    worker_available: bool = False
    protocol: str = AGENCY_EXECUTION_PROTOCOL
    max_steps: int = MAX_EXECUTION_STEPS
    max_concurrency: int = MAX_EXECUTION_CONCURRENCY
    max_model_calls: int = MAX_EXECUTION_MODEL_CALLS
    max_tokens_per_call: int = MAX_EXECUTION_TOKENS
    timeout_seconds: int = MAX_EXECUTION_SECONDS
    supports_replay: bool = True
    supports_cancel: bool = True
    supports_restart_resume: bool = False


class AgencyExecutionValidationError(ValueError):
    def __init__(self, message: str, *, code: str = "agency_execution_plan_invalid"):
        super().__init__(message)
        self.code = code


class AgencyExecutionCapacityError(RuntimeError):
    pass


@dataclass(slots=True)
class PreparedAgencyExecution:
    workflow: dict[str, Any]
    agents: list[AgencyAgentDefinition]
    sink_task_id: str
    selected_agent_ids: list[str]


def _record_id(record: Any) -> str:
    if isinstance(record, Mapping):
        return str(record.get("id") or "").strip()
    return str(getattr(record, "id", "") or "").strip()


def _record_field(record: Any, name: str) -> str:
    if isinstance(record, Mapping):
        return str(record.get(name) or "").strip()
    return str(getattr(record, name, "") or "").strip()


def _task_map(plan: MetaPlannerTaskPlan) -> dict[str, MetaPlannerTask]:
    if not 1 <= len(plan.tasks) <= MAX_EXECUTION_STEPS:
        raise AgencyExecutionValidationError(
            f"DAG Beta 只支持 1-{MAX_EXECUTION_STEPS} 个任务。"
        )
    tasks = {task.task_id: task for task in plan.tasks}
    if len(tasks) != len(plan.tasks):
        raise AgencyExecutionValidationError("任务 ID 不得重复。")
    for task in plan.tasks:
        if not task.agent_id:
            raise AgencyExecutionValidationError(
                f"任务 {task.task_id} 未绑定当前专家。"
            )
        if len(task.depends_on) != len(set(task.depends_on)):
            raise AgencyExecutionValidationError(
                f"任务 {task.task_id} 存在重复依赖。"
            )
        unknown = [item for item in task.depends_on if item not in tasks]
        if unknown or task.task_id in task.depends_on:
            raise AgencyExecutionValidationError(
                f"任务 {task.task_id} 包含未知依赖或自依赖。"
            )
    depended_on = {dependency for task in plan.tasks for dependency in task.depends_on}
    sinks = [task for task in plan.tasks if task.task_id not in depended_on]
    if len(sinks) != 1:
        raise AgencyExecutionValidationError("执行计划必须恰好有一个最终汇点。")
    if not sinks[0].acceptance.strip():
        raise AgencyExecutionValidationError("最终汇点必须包含验收标准。")
    return tasks


def _assert_acyclic(tasks: dict[str, MetaPlannerTask]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visiting:
            raise AgencyExecutionValidationError("执行计划存在循环依赖。")
        if task_id in visited:
            return
        visiting.add(task_id)
        for dependency in tasks[task_id].depends_on:
            visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in tasks:
        visit(task_id)


def prepare_agency_execution(
    *,
    plan: MetaPlannerTaskPlan,
    workflow: NativeWorkflowDefinition,
    expert_records: Iterable[Any],
) -> PreparedAgencyExecution:
    """Validate the native preview and compile a constrained upstream DAG."""

    tasks = _task_map(plan)
    _assert_acyclic(tasks)
    records = {_record_id(record): record for record in expert_records}
    validation = validate_workflow_graph(workflow)
    if not validation.valid:
        messages = "; ".join(issue.message for issue in validation.issues[:6])
        raise AgencyExecutionValidationError(
            f"工作流未通过静态校验：{messages or 'unknown validation error'}"
        )

    nodes = {node.id: node for node in workflow.nodes}
    input_nodes = [node for node in workflow.nodes if node_kind(node) == "input"]
    output_nodes = [node for node in workflow.nodes if node_kind(node) == "output"]
    task_nodes = [
        node for node in workflow.nodes if node_kind(node) == "workflow_agent"
    ]
    if (
        len(input_nodes) != 1
        or len(output_nodes) != 1
        or len(task_nodes) != len(tasks)
        or len(workflow.nodes) != len(tasks) + 2
    ):
        raise AgencyExecutionValidationError(
            "DAG Beta 仅接受一个输入、一个输出和普通专家任务节点。"
        )

    outputs: dict[str, str] = {}
    for task_id, task in tasks.items():
        node = nodes.get(f"agent_{task_id}")
        if node is None or node_kind(node) != "workflow_agent":
            raise AgencyExecutionValidationError(
                f"工作流缺少任务节点 agent_{task_id}。"
            )
        source_agent_id = str(node.data.get("sourceAgentId") or "").strip()
        if source_agent_id != task.agent_id:
            raise AgencyExecutionValidationError(
                f"任务 {task_id} 的专家绑定与计划不一致。"
            )
        expert = records.get(source_agent_id)
        if expert is None:
            raise AgencyExecutionValidationError(
                f"未找到执行专家：{source_agent_id}", code="unknown_agent"
            )
        expected_role_prompt = _record_field(expert, "prompt")[:20_000]
        if str(node.data.get("rolePrompt") or "").strip() != expected_role_prompt:
            raise AgencyExecutionValidationError(
                f"任务 {task_id} 的角色提示词不是当前专家目录版本。"
            )
        if str(node.data.get("toolMode") or "none").strip() != "none" or str(
            node.data.get("toolNames") or ""
        ).strip():
            raise AgencyExecutionValidationError(
                f"任务 {task_id} 包含 DAG Beta 不支持的工具配置。"
            )
        forbidden_runtime_fields = {
            "provider",
            "apiKey",
            "api_key",
            "baseUrl",
            "base_url",
            "llm",
            "modelOverride",
            "model_override",
        }
        if any(node.data.get(field) is not None for field in forbidden_runtime_fields):
            raise AgencyExecutionValidationError(
                f"任务 {task_id} 包含不允许的 Provider 或模型覆盖。"
            )
        if str(node.data.get("description") or "").strip() != task.objective.strip():
            raise AgencyExecutionValidationError(
                f"任务 {task_id} 的目标与计划不一致。"
            )
        if str(node.data.get("acceptanceCriteria") or "").strip() != task.acceptance.strip():
            raise AgencyExecutionValidationError(
                f"任务 {task_id} 的验收标准与计划不一致。"
            )
        output = str(node.data.get("outputVariable") or "").strip()
        if not VARIABLE_PATTERN.fullmatch(output) or output in outputs.values():
            raise AgencyExecutionValidationError(
                f"任务 {task_id} 的输出变量无效或重复。"
            )
        outputs[task_id] = output

    for task_id, task in tasks.items():
        node = nodes[f"agent_{task_id}"]
        task_input = str(node.data.get("taskInput") or "")
        references = set(TEMPLATE_PATTERN.findall(task_input))
        allowed = (
            {outputs[dependency] for dependency in task.depends_on}
            if task.depends_on
            else {"user_input"}
        )
        if not task.objective.strip() or task.objective.strip() not in task_input:
            raise AgencyExecutionValidationError(
                f"任务 {task_id} 的执行输入未包含当前目标。"
            )
        if references != allowed:
            raise AgencyExecutionValidationError(
                f"任务 {task_id} 的输入变量与依赖不一致。"
            )

    depended_on = {dependency for task in plan.tasks for dependency in task.depends_on}
    sink = next(task for task in plan.tasks if task.task_id not in depended_on)
    expected_edges = {
        *{
            (f"agent_{dependency}", f"agent_{task.task_id}")
            for task in plan.tasks
            for dependency in task.depends_on
        },
        *{
            (input_nodes[0].id, f"agent_{task.task_id}")
            for task in plan.tasks
            if not task.depends_on
        },
        (f"agent_{sink.task_id}", output_nodes[0].id),
    }
    actual_edges = {(edge.source, edge.target) for edge in workflow.edges}
    if actual_edges != expected_edges or len(actual_edges) != len(workflow.edges):
        raise AgencyExecutionValidationError("工作流连线与计划依赖不一致。")
    final_output = str(output_nodes[0].data.get("outputVariable") or "").strip()
    if final_output != outputs[sink.task_id]:
        raise AgencyExecutionValidationError("输出节点未指向最终汇点结果。")

    selected_agent_ids: list[str] = []
    for task in plan.tasks:
        assert task.agent_id is not None
        if task.agent_id not in records:
            raise AgencyExecutionValidationError(
                f"未找到执行专家：{task.agent_id}", code="unknown_agent"
            )
        if task.agent_id not in selected_agent_ids:
            selected_agent_ids.append(task.agent_id)
    selected_records = [records[agent_id] for agent_id in selected_agent_ids]
    agents = adapt_expert_catalog(
        selected_records, max_system_prompt_chars=16_000
    )

    upstream_steps = []
    for task in plan.tasks:
        dependencies = [
            f"{dependency}: {{{{{outputs[dependency]}}}}}"
            for dependency in task.depends_on
        ]
        task_text = task.objective.strip()
        if dependencies:
            task_text += "\n\n依赖结果：\n" + "\n".join(dependencies)
        else:
            task_text += "\n\n用户任务：\n{{user_input}}"
        upstream_steps.append(
            {
                "id": task.task_id,
                "role": task.agent_id,
                "name": task.title,
                "task": task_text,
                "acceptance": task.acceptance.strip(),
                "output": outputs[task.task_id],
                "depends_on": list(task.depends_on),
                "type": "normal",
            }
        )
    return PreparedAgencyExecution(
        workflow={
            "name": workflow.title,
            "description": plan.summary,
            "steps": upstream_steps,
        },
        agents=agents,
        sink_task_id=sink.task_id,
        selected_agent_ids=selected_agent_ids,
    )


class AgencyExecutionCoordinator:
    def __init__(
        self,
        *,
        store: WorkflowExecutionStore,
        run_registry: RunRegistry,
        model_runner: AgencyModelRunner,
        worker_entry: str | None = None,
        enabled: bool = False,
        client_factory: Callable[[], AgencyExecutionClient] | None = None,
    ) -> None:
        self.store = store
        self.run_registry = run_registry
        self.model_runner = model_runner
        self.worker_entry = worker_entry
        self.enabled = enabled
        self.client_factory = client_factory
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    def worker_available(self) -> bool:
        client = self._client()
        return client.worker_entry.is_file()

    async def start(
        self,
        *,
        goal: str,
        model_id: str,
        prepared: PreparedAgencyExecution,
        capability_snapshot_version: str,
        capability_snapshot_hash: str,
        upstream_revision: str,
    ) -> dict[str, Any]:
        async with self._lock:
            self._prune_finished_unlocked()
            if len(self._tasks) >= MAX_ACTIVE_EXECUTIONS:
                raise AgencyExecutionCapacityError(
                    "当前已有两个 DAG 正在执行，请等待其中一个结束。"
                )
            run = await self.run_registry.create_run(
                "expert_team",
                f"Expert Team DAG: {goal[:80]}",
                status="running",
                source_id="expert_team",
                metadata={
                    "surface": "expert_team",
                    "backend": "agency_orchestrator",
                    "upstream_project": AGENCY_UPSTREAM_PROJECT,
                    "upstream_revision": upstream_revision,
                    "model_id": model_id,
                    "capability_snapshot_hash": capability_snapshot_hash,
                    "max_steps": MAX_EXECUTION_STEPS,
                    "max_concurrency": MAX_EXECUTION_CONCURRENCY,
                    "max_model_calls": MAX_EXECUTION_MODEL_CALLS,
                },
            )
            task_id = f"agency_dag_{uuid.uuid4().hex}"
            item = self.store.create(
                task_id=task_id,
                run_id=run.run_id,
                run_type="expert_team",
                workflow=prepared.workflow,
                inputs={"goal": goal},
                source_kind="expert_team_agency",
                runtime_metadata={
                    "protocol": AGENCY_EXECUTION_PROTOCOL,
                    "model_id": model_id,
                    "upstream_revision": upstream_revision,
                    "capability_snapshot_version": capability_snapshot_version,
                    "capability_snapshot_hash": capability_snapshot_hash,
                    "sink_task_id": prepared.sink_task_id,
                    "selected_agent_ids": prepared.selected_agent_ids,
                },
            )
            task = asyncio.create_task(
                self._run(
                    task_id=task_id,
                    goal=goal,
                    model_id=model_id,
                    prepared=prepared,
                ),
                name=f"expert-team-agency:{task_id}",
            )
            self._tasks[task_id] = task
            task.add_done_callback(lambda _task: self._tasks.pop(task_id, None))
            return self.serialize(item)

    async def cancel(self, task_id: str) -> dict[str, Any]:
        item = self.store.require(task_id)
        if item.source_kind != "expert_team_agency":
            raise AgencyExecutionValidationError("任务类型不匹配。")
        if item.status in TERMINAL_STATUSES:
            return self.serialize(item)
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is not None and not task.done():
                task.cancel()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        current = self.store.require(task_id)
        if current.status not in TERMINAL_STATUSES:
            self._append_terminal_event(
                task_id,
                {"event": "agency.run.cancelled", "status": "cancelled"},
            )
            current = self.store.cancel(task_id, error="agency_execution_cancelled")
            await self._safe_update_run(
                current.run_id,
                status="cancelled",
                error="agency_execution_cancelled",
            )
        return self.serialize(current)

    def get(self, task_id: str) -> dict[str, Any]:
        item = self.store.require(task_id)
        if item.source_kind != "expert_team_agency":
            raise AgencyExecutionValidationError("任务类型不匹配。")
        return self.serialize(item)

    def recover_interrupted(self) -> int:
        recovered = 0
        for item in self.store.list_items(limit=1_000):
            if (
                item.source_kind == "expert_team_agency"
                and item.status not in TERMINAL_STATUSES
            ):
                self._append_terminal_event(
                    item.task_id,
                    {
                        "event": "agency.run.failed",
                        "status": "failed",
                        "error": "agency_execution_interrupted",
                    },
                )
                self.store.fail(
                    item.task_id, error="agency_execution_interrupted"
                )
                recovered += 1
        return recovered

    async def _run(
        self,
        *,
        task_id: str,
        goal: str,
        model_id: str,
        prepared: PreparedAgencyExecution,
    ) -> None:
        item = self.store.require(task_id)

        async def on_event(event: dict[str, Any]) -> None:
            current = self.store.require(task_id)
            if current.status not in TERMINAL_STATUSES:
                self.store.append_event(task_id, event)
                if (
                    event.get("event") == "agency.run.completed"
                    and str(event.get("final_output") or "")
                ):
                    # The completion event is the worker's durable commit point.
                    # Mark it before yielding back to the event loop so a late
                    # cancel cannot overwrite a finished, already-billed run.
                    self.store.complete(
                        task_id,
                        result=str(event["final_output"]),
                    )

        try:
            result = await self._client().execute(
                goal=goal,
                model_id=model_id,
                workflow=prepared.workflow,
                agents=prepared.agents,
                on_event=on_event,
            )
            payload = result.payload
            final_output = str(payload.get("final_output") or "")
            if not final_output:
                raise AgencyWorkerError(
                    "Agency execution returned no final output.",
                    code="agency_execution_plan_invalid",
                )
            current = self.store.require(task_id)
            if current.status not in TERMINAL_STATUSES:
                self.store.complete(task_id, result=final_output)
                current = self.store.require(task_id)
            if current.status == "completed":
                await self._safe_update_run(
                    item.run_id,
                    status="completed",
                    metadata={
                        "model_calls": int(payload.get("model_calls") or 0),
                        "usage": payload.get("usage") or {},
                        "quality_status": payload.get("quality_status"),
                    },
                )
        except asyncio.CancelledError:
            current = self.store.require(task_id)
            if current.status not in TERMINAL_STATUSES:
                self._append_terminal_event(
                    task_id,
                    {"event": "agency.run.cancelled", "status": "cancelled"},
                )
                self.store.cancel(task_id, error="agency_execution_cancelled")
                await self._safe_update_run(
                    item.run_id,
                    status="cancelled",
                    error="agency_execution_cancelled",
                )
            raise
        except Exception as exc:
            code = getattr(exc, "code", "agency_execution_failed")
            current = self.store.require(task_id)
            if current.status not in TERMINAL_STATUSES:
                self._append_terminal_event(
                    task_id,
                    {
                        "event": "agency.run.failed",
                        "status": "failed",
                        "error": str(code),
                        "message": str(exc)[:4_000],
                    },
                )
                self.store.fail(task_id, error=str(code))
                await self._safe_update_run(
                    item.run_id, status="failed", error=f"{code}: {exc}"[:500]
                )

    def _client(self) -> AgencyExecutionClient:
        if self.client_factory is not None:
            return self.client_factory()
        return AgencyExecutionClient(
            model_runner=self.model_runner,
            worker_entry=self.worker_entry,
        )

    async def _safe_update_run(self, run_id: str, **kwargs: Any) -> None:
        try:
            await self.run_registry.update_run(run_id, **kwargs)
        except KeyError:
            return

    def _append_terminal_event(
        self, task_id: str, event: dict[str, Any]
    ) -> None:
        item = self.store.require(task_id)
        if item.events and item.events[-1].get("event") == event.get("event"):
            return
        self.store.append_event(task_id, event)

    def _prune_finished_unlocked(self) -> None:
        for task_id, task in list(self._tasks.items()):
            if task.done():
                self._tasks.pop(task_id, None)

    @staticmethod
    def serialize(item: Any) -> dict[str, Any]:
        public = WorkflowExecutionStore.serialize_public(item)
        latest_steps: dict[str, dict[str, Any]] = {}
        summary: dict[str, Any] = {}
        for event in item.events:
            event_name = str(event.get("event") or "")
            step_id = str(event.get("task_id") or "")
            if step_id and event_name.startswith("agency.step."):
                latest_steps[step_id] = dict(event)
            if event_name in {
                "agency.run.completed",
                "agency.run.failed",
                "agency.run.cancelled",
            }:
                summary.update(event)
        raw_steps = item.workflow.get("steps") if isinstance(item.workflow, dict) else []
        def dependencies(step: dict[str, Any]) -> list[str]:
            raw = step.get("depends_on")
            if not isinstance(raw, list):
                return []
            return [str(value)[:64] for value in raw[:6]]

        task_definitions = [
            {
                "task_id": str(step.get("id") or "")[:64],
                "title": str(step.get("name") or step.get("id") or "")[:120],
                "objective": str(step.get("task") or "")[:20_000],
                "depends_on": dependencies(step),
                "agent_id": str(step.get("role") or "")[:160],
                "acceptance": str(step.get("acceptance") or "")[:4_000],
            }
            for step in (raw_steps if isinstance(raw_steps, list) else [])[:6]
            if isinstance(step, dict)
        ]
        return {
            **public,
            "steps": list(latest_steps.values()),
            "final_output": item.result,
            "quality_status": summary.get("quality_status"),
            "warnings": summary.get("warnings") or [],
            "model_calls": int(summary.get("model_calls") or 0),
            "usage": summary.get("usage") or {},
            "estimated_cost": None,
            "error_code": item.error,
            "task_definitions": task_definitions,
            "model_id": str(item.runtime_metadata.get("model_id") or "")[:300],
            "goal": str(item.inputs.get("goal") or "")[:20_000],
            "team_name": str(item.workflow.get("name") or "")[:200]
            if isinstance(item.workflow, dict)
            else "",
            "selected_agent_ids": [
                str(agent_id)[:160]
                for agent_id in (
                    item.runtime_metadata.get("selected_agent_ids")
                    if isinstance(
                        item.runtime_metadata.get("selected_agent_ids"), list
                    )
                    else []
                )[:6]
            ],
        }
