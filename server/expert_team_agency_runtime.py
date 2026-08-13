from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

try:
    from server.expert_team_agency import AGENCY_UPSTREAM_PROJECT
    from server.meta_agent.schemas import MetaPlannerTask, MetaPlannerTaskPlan
    from server.orchestration_worker import (
        AGENCY_EXECUTION_PROTOCOL,
        AgencyAgentDefinition,
        AgencyExecutionClient,
        AgencyModelRequest,
        AgencyModelResponse,
        AgencySkillDefinition,
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
        AgencySkillDefinition,
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
    method_skill_digests: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_method_skill_digests(self) -> "ExpertTeamDagRunRequest":
        if len(self.method_skill_digests) > 3:
            raise ValueError("method_skill_digests cannot contain more than 3 Skills")
        for skill_id, digest in self.method_skill_digests.items():
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,159}", skill_id):
                raise ValueError("method_skill_digests contains an invalid Skill id")
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("method_skill_digests contains an invalid digest")
        return self


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
    supports_retry: bool = True
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
    skills: list[AgencySkillDefinition]
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
        if len(task.method_skill_ids) != len(set(task.method_skill_ids)):
            raise AgencyExecutionValidationError(
                f"任务 {task.task_id} 包含重复工作方法。"
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


def _ancestor_task_ids(
    tasks: Mapping[str, MetaPlannerTask], task_id: str
) -> set[str]:
    """Return every direct and transitive dependency of a task."""

    ancestors: set[str] = set()
    pending = list(tasks[task_id].depends_on)
    while pending:
        dependency = pending.pop()
        if dependency in ancestors:
            continue
        ancestors.add(dependency)
        pending.extend(tasks[dependency].depends_on)
    return ancestors


def prepare_agency_execution(
    *,
    plan: MetaPlannerTaskPlan,
    workflow: NativeWorkflowDefinition,
    expert_records: Iterable[Any],
    method_skills: Mapping[str, AgencySkillDefinition] | None = None,
) -> PreparedAgencyExecution:
    """Validate the native preview and compile a constrained upstream DAG."""

    tasks = _task_map(plan)
    _assert_acyclic(tasks)
    records = {_record_id(record): record for record in expert_records}
    method_skills = dict(method_skills or {})
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
        raw_method_skill_ids = node.data.get("methodSkillIds") or []
        if not isinstance(raw_method_skill_ids, list):
            raise AgencyExecutionValidationError(
                f"任务 {task_id} 的工作方法配置无效。"
            )
        node_method_skill_ids = [str(item).strip() for item in raw_method_skill_ids]
        if node_method_skill_ids != task.method_skill_ids:
            raise AgencyExecutionValidationError(
                f"任务 {task_id} 的工作方法与计划不一致。"
            )
        unavailable_skills = [
            skill_id
            for skill_id in task.method_skill_ids
            if skill_id not in method_skills
        ]
        if unavailable_skills:
            raise AgencyExecutionValidationError(
                f"任务 {task_id} 引用了不可用工作方法：{', '.join(unavailable_skills)}。",
                code="agency_method_skill_changed",
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
        if task.depends_on:
            required = {outputs[dependency] for dependency in task.depends_on}
            allowed = {
                outputs[dependency]
                for dependency in _ancestor_task_ids(tasks, task_id)
            }
        else:
            required = {"user_input"}
            allowed = {"user_input"}
        if not task.objective.strip() or task.objective.strip() not in task_input:
            raise AgencyExecutionValidationError(
                f"任务 {task_id} 的执行输入未包含当前目标。"
            )
        missing = required - references
        unexpected = references - allowed
        if missing or unexpected:
            details = []
            if missing:
                details.append(f"缺少 {', '.join(sorted(missing))}")
            if unexpected:
                details.append(f"非上游变量 {', '.join(sorted(unexpected))}")
            raise AgencyExecutionValidationError(
                f"任务 {task_id} 的输入变量与依赖不一致：{'；'.join(details)}。"
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
        task_text = task.objective.strip()
        referenced_variables = set(TEMPLATE_PATTERN.findall(task_text))
        dependencies = [
            f"{dependency}: {{{{{outputs[dependency]}}}}}"
            for dependency in task.depends_on
            if outputs[dependency] not in referenced_variables
        ]
        if dependencies:
            task_text += "\n\n依赖结果：\n" + "\n".join(dependencies)
        elif not task.depends_on and "user_input" not in referenced_variables:
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
                "skills": list(task.method_skill_ids),
            }
        )
    selected_skill_ids = list(
        dict.fromkeys(
            skill_id
            for task in plan.tasks
            for skill_id in task.method_skill_ids
        )
    )
    return PreparedAgencyExecution(
        workflow={
            "name": workflow.title,
            "description": plan.summary,
            "steps": upstream_steps,
        },
        agents=agents,
        skills=[method_skills[skill_id] for skill_id in selected_skill_ids],
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
        resume: Mapping[str, Any] | None = None,
        resumed_from_task_id: str | None = None,
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
                    "resumed_from_task_id": resumed_from_task_id,
                    "initial_model_calls": int(
                        (resume or {}).get("prior_model_calls") or 0
                    ),
                    "initial_usage": dict(
                        (resume or {}).get("prior_usage") or {}
                    ),
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
                    "method_skill_digests": {
                        skill.skill_id: skill.digest for skill in prepared.skills
                    },
                    "resumed_from_task_id": resumed_from_task_id,
                    "initial_model_calls": int(
                        (resume or {}).get("prior_model_calls") or 0
                    ),
                    "initial_usage": dict(
                        (resume or {}).get("prior_usage") or {}
                    ),
                },
            )
            task = asyncio.create_task(
                self._run(
                    task_id=task_id,
                    goal=goal,
                    model_id=model_id,
                    prepared=prepared,
                    resume=resume,
                ),
                name=f"expert-team-agency:{task_id}",
            )
            self._tasks[task_id] = task
            task.add_done_callback(lambda _task: self._tasks.pop(task_id, None))
            return self.serialize(item)

    async def retry(
        self,
        *,
        source_task_id: str,
        prepared: PreparedAgencyExecution,
    ) -> dict[str, Any]:
        source = self.store.require(source_task_id)
        if source.source_kind != "expert_team_agency" or source.status != "failed":
            raise AgencyExecutionValidationError(
                "只有失败的专家团 DAG 可以续跑。",
                code="agency_execution_not_retryable",
            )
        existing_retry = next(
            (
                item
                for item in self.store.list_items(limit=1_000)
                if item.source_kind == "expert_team_agency"
                and item.status not in TERMINAL_STATUSES
                and str(
                    item.runtime_metadata.get("resumed_from_task_id") or ""
                )
                == source_task_id
            ),
            None,
        )
        if existing_retry is not None:
            return self.serialize(existing_retry)
        serialized = self.serialize(source)
        if not serialized.get("retryable"):
            raise AgencyExecutionValidationError(
                "该任务没有可安全复用的已完成步骤，或累计调用额度已耗尽。",
                code="agency_execution_not_retryable",
            )
        workflow_steps = {
            str(step.get("id") or ""): step
            for step in (
                source.workflow.get("steps", [])
                if isinstance(source.workflow, dict)
                else []
            )
            if isinstance(step, dict)
        }
        completed_steps: list[dict[str, Any]] = []
        for event in serialized.get("steps", []):
            if event.get("status") != "completed" or not event.get("output"):
                continue
            task_id = str(event.get("task_id") or "")
            definition = workflow_steps.get(task_id)
            if definition is None:
                raise AgencyExecutionValidationError(
                    "已完成步骤与冻结工作流不一致，不能续跑。",
                    code="agency_execution_not_retryable",
                )
            completed_steps.append(
                {
                    "task_id": task_id,
                    "output": str(event["output"])[: 64 * 1024],
                    "output_variable": str(definition.get("output") or ""),
                    "acceptance": str(
                        event.get("acceptance")
                        or definition.get("acceptance")
                        or ""
                    )[:4_000],
                }
            )
        if not completed_steps:
            raise AgencyExecutionValidationError(
                "没有可复用的已完成步骤，不能执行安全续跑。",
                code="agency_execution_not_retryable",
            )
        resume = {
            "source_task_id": source_task_id,
            "completed_steps": completed_steps,
            "prior_model_calls": int(serialized.get("model_calls") or 0),
            "prior_usage": dict(serialized.get("usage") or {}),
        }
        metadata = source.runtime_metadata
        return await self.start(
            goal=str(source.inputs.get("goal") or ""),
            model_id=str(metadata.get("model_id") or ""),
            prepared=prepared,
            capability_snapshot_version=str(
                metadata.get("capability_snapshot_version") or ""
            ),
            capability_snapshot_hash=str(
                metadata.get("capability_snapshot_hash") or ""
            ),
            upstream_revision=str(metadata.get("upstream_revision") or ""),
            resume=resume,
            resumed_from_task_id=source_task_id,
        )

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
        resume: Mapping[str, Any] | None = None,
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
                skills=prepared.skills,
                resume=resume,
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
            if isinstance(event.get("model_calls"), (int, float)):
                summary["model_calls"] = max(
                    0, int(event.get("model_calls") or 0)
                )
            cumulative_usage = event.get("cumulative_usage")
            if isinstance(cumulative_usage, dict):
                summary["usage"] = dict(cumulative_usage)
            if event_name in {
                "agency.run.completed",
                "agency.run.failed",
                "agency.run.cancelled",
            }:
                summary.update(event)
        completed_outputs = [
            event
            for event in latest_steps.values()
            if event.get("status") == "completed" and event.get("output")
        ]
        retryable_codes = {
            "agency_execution_step_failed",
            "model_output_truncated",
            "model_response_empty",
            "model_response_invalid",
            "model_gateway_timeout",
            "model_gateway_failed",
        }
        retryable = (
            item.status == "failed"
            and str(item.error or "") in retryable_codes
            and bool(completed_outputs)
            and int(summary.get("model_calls") or 0) < MAX_EXECUTION_MODEL_CALLS
        )
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
                "method_skill_ids": [
                    str(value)[:160]
                    for value in (
                        step.get("skills") if isinstance(step.get("skills"), list) else []
                    )[:1]
                ],
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
            "model_calls": int(
                summary.get("model_calls")
                or item.runtime_metadata.get("initial_model_calls")
                or 0
            ),
            "usage": summary.get("usage")
            or item.runtime_metadata.get("initial_usage")
            or {},
            "estimated_cost": None,
            "error_code": item.error,
            "error_message": str(
                summary.get("message") or summary.get("error") or ""
            )[:4_000] or None,
            "retryable": retryable,
            "resumed_from_task_id": str(
                item.runtime_metadata.get("resumed_from_task_id") or ""
            ) or None,
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
