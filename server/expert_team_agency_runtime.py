from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

try:
    from server.expert_team_agency import (
        AGENCY_UPSTREAM_PROJECT,
        _literalize_unbound_role_placeholders,
    )
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
    from server.xpert_runtime import (
        RunRegistry,
        RuntimeApprovalRequest,
        RuntimeApprovalStore,
        WorkflowExecutionStore,
    )
except ModuleNotFoundError:
    from expert_team_agency import (
        AGENCY_UPSTREAM_PROJECT,
        _literalize_unbound_role_placeholders,
    )
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
    from xpert_runtime import (
        RunRegistry,
        RuntimeApprovalRequest,
        RuntimeApprovalStore,
        WorkflowExecutionStore,
    )


MAX_EXECUTION_STEPS = 6
MAX_EXECUTION_CONCURRENCY = 2
MAX_EXECUTION_MODEL_CALLS = 10
MAX_EXECUTION_TOKENS = 4096
MAX_EXECUTION_SECONDS = 900
MAX_ACTIVE_EXECUTIONS = 2
MAX_REVISION_FEEDBACK_CHARS = 4_000
MAX_HITL_INTERACTIONS = 2
MAX_HITL_INPUT_CHARS = 20_000
HITL_WAIT_SECONDS = 86_400
AGENCY_HITL_PROTOCOL = "mm-agency-bridge/v3"
TERMINAL_STATUSES = {"completed", "failed", "cancelled", "rejected"}
VARIABLE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
TEMPLATE_PATTERN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")

AgencyModelRunner = Callable[
    [AgencyModelRequest], Awaitable[AgencyModelResponse | str]
]


class AgencyManagedRun(Protocol):
    async def complete(self, request: AgencyModelRequest) -> AgencyModelResponse: ...

    def finish(
        self,
        status: Literal["passed", "failed", "uncertain", "cancelled"],
        *,
        reason_code: str | None = None,
    ) -> dict[str, Any]: ...

    def receipt_summary(self) -> dict[str, Any]: ...


AgencyManagedRunFactory = Callable[[str, str], AgencyManagedRun | None]


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


class ExpertTeamDagRevisionRequest(StrictModel):
    target_task_id: str = Field(min_length=1, max_length=64)
    feedback: str = Field(min_length=10, max_length=MAX_REVISION_FEEDBACK_CHARS)

    @model_validator(mode="after")
    def normalize_feedback(self) -> "ExpertTeamDagRevisionRequest":
        self.target_task_id = self.target_task_id.strip()
        self.feedback = self.feedback.strip()
        if not self.target_task_id or len(self.feedback) < 10:
            raise ValueError("Revision target and feedback are required")
        return self


class AgencyRevisionCapabilities(BaseModel):
    enabled: bool = False
    supports_feedback: bool = True
    supports_intermediate_steps: bool = True
    max_feedback_chars: int = MAX_REVISION_FEEDBACK_CHARS
    max_model_calls: int = MAX_EXECUTION_MODEL_CALLS
    budget_mode: Literal["fresh"] = "fresh"


class AgencyHitlCapabilities(BaseModel):
    enabled: bool = False
    protocol: str = AGENCY_HITL_PROTOCOL
    supports_human_input: bool = True
    supports_approval: bool = True
    max_interactions: int = MAX_HITL_INTERACTIONS
    max_input_chars: int = MAX_HITL_INPUT_CHARS
    wait_timeout_seconds: int = HITL_WAIT_SECONDS
    supports_reopen: bool = True
    supports_restart_wait: bool = True
    auto_insert_policy: Literal["conservative"] = "conservative"


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
    revision: AgencyRevisionCapabilities = Field(
        default_factory=AgencyRevisionCapabilities
    )
    hitl: AgencyHitlCapabilities = Field(default_factory=AgencyHitlCapabilities)


class AgencyExecutionValidationError(ValueError):
    def __init__(self, message: str, *, code: str = "agency_execution_plan_invalid"):
        super().__init__(message)
        self.code = code


class AgencyExecutionCapacityError(RuntimeError):
    defer_resume = True


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
        if task.task_type == "expert" and not task.agent_id:
            raise AgencyExecutionValidationError(
                f"任务 {task.task_id} 未绑定当前专家。"
            )
        if task.task_type != "expert" and (
            task.agent_id
            or task.method_skill_ids
            or task.acceptance.strip()
            or not task.interaction_prompt.strip()
            or not task.output_variable
        ):
            raise AgencyExecutionValidationError(
                f"HITL 任务 {task.task_id} 的字段不符合受控执行约束。"
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
    if sinks[0].task_type != "expert" or not sinks[0].acceptance.strip():
        raise AgencyExecutionValidationError("最终汇点必须包含验收标准。")
    interactions = [task for task in plan.tasks if task.task_type != "expert"]
    if len(interactions) > MAX_HITL_INTERACTIONS:
        raise AgencyExecutionValidationError("HITL 节点最多允许两个。")
    return tasks


def _assert_hitl_barriers(tasks: Mapping[str, MetaPlannerTask]) -> None:
    children: dict[str, list[str]] = {task_id: [] for task_id in tasks}
    for task in tasks.values():
        for dependency in task.depends_on:
            children[dependency].append(task.task_id)

    def walk(initial: Iterable[str], next_ids: Callable[[str], Iterable[str]]) -> set[str]:
        seen: set[str] = set()
        pending = list(initial)
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            pending.extend(next_ids(current))
        return seen

    for task in tasks.values():
        if task.task_type == "expert":
            continue
        ancestors = walk(task.depends_on, lambda item: tasks[item].depends_on)
        descendants = walk(children[task.task_id], lambda item: children[item])
        parallel = next(
            (
                other_id
                for other_id in tasks
                if other_id != task.task_id
                and other_id not in ancestors
                and other_id not in descendants
            ),
            None,
        )
        if parallel:
            raise AgencyExecutionValidationError(
                f"HITL 任务 {task.task_id} 必须是完整 DAG 屏障。"
            )


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


def _task_nodes_by_plan_task(
    tasks: Mapping[str, MetaPlannerTask],
    task_nodes: list[Any],
) -> dict[str, Any]:
    """Bind plan tasks to compiled nodes without guessing normalized node IDs."""

    has_planner_task_ids = ["plannerTaskIds" in node.data for node in task_nodes]
    if any(has_planner_task_ids):
        if not all(has_planner_task_ids):
            raise AgencyExecutionValidationError(
                "工作流任务节点混用了不同版本的规划元数据。"
            )
        mapped: dict[str, Any] = {}
        for node in task_nodes:
            raw_task_ids = node.data.get("plannerTaskIds")
            if not isinstance(raw_task_ids, list) or len(raw_task_ids) != 1:
                raise AgencyExecutionValidationError(
                    f"工作流任务节点 {node.id} 的规划任务绑定无效。"
                )
            task_id = raw_task_ids[0]
            if not isinstance(task_id, str) or task_id not in tasks:
                raise AgencyExecutionValidationError(
                    f"工作流任务节点 {node.id} 引用了未知规划任务。"
                )
            if task_id in mapped:
                raise AgencyExecutionValidationError(
                    f"工作流包含重复的任务节点：{task_id}。"
                )
            expected_ref = (
                f"agent_{task_id}"
                if tasks[task_id].task_type == "expert"
                else f"hitl_{task_id}"
            )
            if str(node.data.get("plannerRef") or "").strip() != expected_ref:
                raise AgencyExecutionValidationError(
                    f"工作流任务节点 {node.id} 的规划引用与任务 {task_id} 不一致。"
                )
            mapped[task_id] = node
        missing = [task_id for task_id in tasks if task_id not in mapped]
        if missing:
            raise AgencyExecutionValidationError(
                f"工作流缺少任务节点 {missing[0]}。"
            )
        return mapped

    if any(task.task_type != "expert" for task in tasks.values()):
        raise AgencyExecutionValidationError("HITL 工作流缺少规划往返元数据。")
    # Backward compatibility for previews compiled before planner round-trip
    # metadata was added. The old path remains exact and does not normalize IDs.
    nodes_by_id = {node.id: node for node in task_nodes}
    mapped = {}
    for task_id in tasks:
        expected_node_id = f"agent_{task_id}"
        node = nodes_by_id.get(expected_node_id)
        if node is None:
            raise AgencyExecutionValidationError(
                f"工作流缺少任务节点 {expected_node_id}。"
            )
        mapped[task_id] = node
    return mapped


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
    _assert_hitl_barriers(tasks)
    records = {_record_id(record): record for record in expert_records}
    method_skills = dict(method_skills or {})
    validation = validate_workflow_graph(workflow)
    if not validation.valid:
        messages = "; ".join(issue.message for issue in validation.issues[:6])
        raise AgencyExecutionValidationError(
            f"工作流未通过静态校验：{messages or 'unknown validation error'}"
        )

    input_nodes = [node for node in workflow.nodes if node_kind(node) == "input"]
    output_nodes = [node for node in workflow.nodes if node_kind(node) == "output"]
    task_nodes = [
        node for node in workflow.nodes if node_kind(node) == "workflow_agent"
    ]
    hitl_nodes = [
        node for node in workflow.nodes if node_kind(node) == "human_intervention"
    ]
    if (
        len(input_nodes) != 1
        or len(output_nodes) != 1
        or len(task_nodes) + len(hitl_nodes) != len(tasks)
        or len(workflow.nodes) != len(tasks) + 2
    ):
        raise AgencyExecutionValidationError(
            "DAG Beta 仅接受一个输入、一个输出、专家任务和受控 HITL 节点。"
        )

    task_nodes_by_id = _task_nodes_by_plan_task(tasks, [*task_nodes, *hitl_nodes])

    outputs: dict[str, str] = {}
    for task_id, task in tasks.items():
        node = task_nodes_by_id[task_id]
        output = str(node.data.get("outputVariable") or "").strip()
        expected_output = task.output_variable
        if expected_output and output != expected_output:
            raise AgencyExecutionValidationError(
                f"任务 {task_id} 的输出变量与计划不一致。"
            )
        if not VARIABLE_PATTERN.fullmatch(output) or output in outputs.values():
            raise AgencyExecutionValidationError(
                f"任务 {task_id} 的输出变量无效或重复。"
            )
        outputs[task_id] = output
        if task.task_type != "expert":
            expected_mode = "approval" if task.task_type == "approval" else "input"
            if (
                node_kind(node) != "human_intervention"
                or str(node.data.get("interactionMode") or "input") != expected_mode
                or str(node.data.get("prompt") or "").strip()
                != task.interaction_prompt.strip()
            ):
                raise AgencyExecutionValidationError(
                    f"HITL 任务 {task_id} 与计划不一致。"
                )
            continue
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
        role_variables = {
            "user_input",
            "conversation_history",
            *(
                str(
                    task_nodes_by_id[dependency].data.get("outputVariable") or ""
                ).strip()
                for dependency in task.depends_on
            ),
        }
        expected_role_prompt = _literalize_unbound_role_placeholders(
            _record_field(expert, "prompt")[:20_000],
            available_variables=role_variables,
        ).strip()
        actual_role_prompt = str(node.data.get("rolePrompt") or "").strip()
        if actual_role_prompt != expected_role_prompt:
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

    for task_id, task in tasks.items():
        node = task_nodes_by_id[task_id]
        task_input = str(
            node.data.get("taskInput")
            if task.task_type == "expert"
            else node.data.get("prompt")
            or ""
        )
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
        if task.task_type == "expert" and (
            not task.objective.strip() or task.objective.strip() not in task_input
        ):
            raise AgencyExecutionValidationError(
                f"任务 {task_id} 的执行输入未包含当前目标。"
            )
        missing = required - references if task.task_type == "expert" else set()
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
            (
                task_nodes_by_id[dependency].id,
                task_nodes_by_id[task.task_id].id,
            )
            for task in plan.tasks
            for dependency in task.depends_on
        },
        *{
            (input_nodes[0].id, task_nodes_by_id[task.task_id].id)
            for task in plan.tasks
            if not task.depends_on
        },
        (task_nodes_by_id[sink.task_id].id, output_nodes[0].id),
    }
    actual_edges = {(edge.source, edge.target) for edge in workflow.edges}
    if actual_edges != expected_edges or len(actual_edges) != len(workflow.edges):
        raise AgencyExecutionValidationError("工作流连线与计划依赖不一致。")
    final_output = str(output_nodes[0].data.get("outputVariable") or "").strip()
    if final_output != outputs[sink.task_id]:
        raise AgencyExecutionValidationError("输出节点未指向最终汇点结果。")

    selected_agent_ids: list[str] = []
    for task in plan.tasks:
        if task.task_type != "expert":
            continue
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
        if task.task_type != "expert":
            upstream_steps.append(
                {
                    "id": task.task_id,
                    "role": "",
                    "name": task.title,
                    "task": task.objective.strip(),
                    "prompt": task.interaction_prompt.strip(),
                    "output": outputs[task.task_id],
                    "depends_on": list(task.depends_on),
                    "type": task.task_type,
                    "skills": [],
                }
            )
            continue
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
            if task.task_type == "expert"
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
        approval_store: RuntimeApprovalStore | None = None,
        worker_entry: str | None = None,
        enabled: bool = False,
        client_factory: Callable[[], AgencyExecutionClient] | None = None,
        managed_run_factory: AgencyManagedRunFactory | None = None,
    ) -> None:
        self.store = store
        self.run_registry = run_registry
        self.model_runner = model_runner
        self.approval_store = approval_store
        self.worker_entry = worker_entry
        self.enabled = enabled
        self.client_factory = client_factory
        self.managed_run_factory = managed_run_factory
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
        revision: Mapping[str, Any] | None = None,
        revision_metadata: Mapping[str, Any] | None = None,
        resumed_from_task_id: str | None = None,
        managed_provider_required: bool = False,
    ) -> dict[str, Any]:
        if resume is not None and revision is not None:
            raise AgencyExecutionValidationError(
                "Agency execution cannot combine retry and revision."
            )
        revision_metadata = dict(revision_metadata or {})
        async with self._lock:
            self._prune_finished_unlocked()
            revision_parent_task_id = str(
                revision_metadata.get("revision_parent_task_id") or ""
            )
            if revision_parent_task_id:
                active_revision = next(
                    (
                        existing
                        for existing in self.store.list_items(limit=1_000)
                        if existing.source_kind == "expert_team_agency"
                        and existing.status not in TERMINAL_STATUSES
                        and str(
                            existing.runtime_metadata.get(
                                "revision_parent_task_id"
                            )
                            or ""
                        )
                        == revision_parent_task_id
                    ),
                    None,
                )
                if active_revision is not None:
                    if str(
                        active_revision.runtime_metadata.get(
                            "revision_request_digest"
                        )
                        or ""
                    ) == str(
                        revision_metadata.get("revision_request_digest") or ""
                    ):
                        return self.serialize(active_revision)
                    raise AgencyExecutionValidationError(
                        "该源任务已有另一项返工正在执行，请等待完成或先取消。",
                        code="agency_revision_in_progress",
                    )
            if len(self._tasks) >= MAX_ACTIVE_EXECUTIONS:
                raise AgencyExecutionCapacityError(
                    "当前已有两个 DAG 正在执行，请等待其中一个结束。"
                )
            public_revision_metadata = {
                key: value
                for key, value in revision_metadata.items()
                if key
                not in {
                    "revision_feedback",
                    "revision_feedback_preview",
                    "revision_request_digest",
                }
            }
            task_id = f"agency_dag_{uuid.uuid4().hex}"
            managed_run = self._managed_run(
                task_id,
                "initial",
                required=managed_provider_required,
            )
            try:
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
                        **public_revision_metadata,
                        "initial_model_calls": int(
                            (resume or {}).get("prior_model_calls") or 0
                        ),
                        "initial_usage": dict(
                            (resume or {}).get("prior_usage") or {}
                        ),
                        "provider_control_mode": (
                            "managed_required"
                            if managed_provider_required
                            else "legacy"
                        ),
                    },
                )
                item = self.store.create(
                    task_id=task_id,
                    run_id=run.run_id,
                    run_type="expert_team",
                    workflow=prepared.workflow,
                    inputs={"goal": goal},
                    source_kind="expert_team_agency",
                    runtime_metadata={
                        "protocol": (
                            AGENCY_HITL_PROTOCOL
                            if any(
                                str(step.get("type") or "normal")
                                in {"human_input", "approval"}
                                for step in prepared.workflow.get("steps", [])
                                if isinstance(step, dict)
                            )
                            else AGENCY_EXECUTION_PROTOCOL
                        ),
                        "model_id": model_id,
                        "upstream_revision": upstream_revision,
                        "capability_snapshot_version": (
                            capability_snapshot_version
                        ),
                        "capability_snapshot_hash": capability_snapshot_hash,
                        "sink_task_id": prepared.sink_task_id,
                        "selected_agent_ids": prepared.selected_agent_ids,
                        "method_skill_digests": {
                            skill.skill_id: skill.digest
                            for skill in prepared.skills
                        },
                        "resumed_from_task_id": resumed_from_task_id,
                        **revision_metadata,
                        "initial_model_calls": int(
                            (resume or {}).get("prior_model_calls") or 0
                        ),
                        "initial_usage": dict(
                            (resume or {}).get("prior_usage") or {}
                        ),
                        "provider_control_mode": (
                            "managed_required"
                            if managed_provider_required
                            else "legacy"
                        ),
                    },
                )
            except Exception:
                if managed_run is not None:
                    managed_run.finish(
                        "failed",
                        reason_code="agency_execution_bootstrap_failed",
                    )
                raise
            task = asyncio.create_task(
                self._run(
                    task_id=task_id,
                    goal=goal,
                    model_id=model_id,
                    prepared=prepared,
                    resume=resume,
                    revision=revision,
                    interaction_resume=None,
                    managed_run=managed_run,
                ),
                name=f"expert-team-agency:{task_id}",
            )
            self._tasks[task_id] = task
            task.add_done_callback(lambda _task: self._tasks.pop(task_id, None))
            return self.serialize(item)

    async def resume_interaction(
        self,
        *,
        execution: Any,
        approval: RuntimeApprovalRequest,
        prepared: PreparedAgencyExecution,
    ) -> None:
        if self.approval_store is None:
            raise AgencyExecutionValidationError(
                "Agency HITL approval store is unavailable.",
                code="agency_worker_unavailable",
            )
        if (
            execution.source_kind != "expert_team_agency"
            or execution.status != "running"
            or execution.wait_id != approval.approval_id
        ):
            raise AgencyExecutionValidationError(
                "Agency interaction is no longer pending.",
                code="agency_interaction_not_pending",
            )
        continuation = dict(execution.continuation or {})
        if (
            str(continuation.get("step_id") or "") != approval.node_id
            or str(continuation.get("kind") or "") != approval.request_type
        ):
            raise AgencyExecutionValidationError(
                "Agency interaction checkpoint does not match the approval.",
                code="agency_interaction_invalid",
            )
        if approval.request_type == "execution_gate" and approval.decision == "reject":
            await self.reject_interaction(execution.task_id, approval)
            return
        if approval.request_type == "execution_gate" and approval.decision == "approve":
            value = "approved"
            kind = "approval"
        elif approval.request_type == "manual_input" and approval.decision == "replace":
            value = str(approval.replacement_text or "").strip()
            kind = "human_input"
        else:
            raise AgencyExecutionValidationError(
                "Agency interaction decision is invalid.",
                code="agency_interaction_invalid",
            )
        if not value or len(value) > MAX_HITL_INPUT_CHARS:
            raise AgencyExecutionValidationError(
                "Agency interaction input must contain 1-20000 characters.",
                code="agency_interaction_invalid",
            )
        interaction_resume = {
            "source_task_id": execution.task_id,
            "step_id": approval.node_id,
            "kind": kind,
            "value": value,
            "completed_steps": list(continuation.get("completed_steps") or []),
            "prior_model_calls": int(continuation.get("prior_model_calls") or 0),
            "prior_usage": dict(continuation.get("prior_usage") or {}),
            "prior_active_duration_ms": int(
                continuation.get("prior_active_duration_ms") or 0
            ),
        }
        async with self._lock:
            self._prune_finished_unlocked()
            if len(self._tasks) >= MAX_ACTIVE_EXECUTIONS:
                raise AgencyExecutionCapacityError(
                    "当前已有两个 DAG 正在执行，HITL 任务将在有容量后继续。"
                )
            managed_run = self._managed_run(
                execution.task_id,
                f"interaction:{approval.approval_id}:{approval.revision}",
                required=(
                    execution.runtime_metadata.get("provider_control_mode")
                    == "managed_required"
                ),
            )
            task = asyncio.create_task(
                self._run(
                    task_id=execution.task_id,
                    goal=str(execution.inputs.get("goal") or ""),
                    model_id=str(execution.runtime_metadata.get("model_id") or ""),
                    prepared=prepared,
                    resume=None,
                    revision=None,
                    interaction_resume=interaction_resume,
                    managed_run=managed_run,
                ),
                name=f"expert-team-agency-hitl:{execution.task_id}",
            )
            self._tasks[execution.task_id] = task
            task.add_done_callback(
                lambda _task: self._tasks.pop(execution.task_id, None)
            )

    async def reject_interaction(
        self,
        task_id: str,
        approval: RuntimeApprovalRequest,
    ) -> dict[str, Any]:
        item = self.store.require(task_id)
        if item.source_kind != "expert_team_agency":
            raise AgencyExecutionValidationError("任务类型不匹配。")
        completed_ids = {
            str(event.get("task_id") or "")
            for event in item.events
            if event.get("event") == "agency.step.completed"
        }
        steps = item.workflow.get("steps") if isinstance(item.workflow, dict) else []
        self.store.append_event(
            task_id,
            {
                "event": "agency.interaction.rejected",
                "task_id": approval.node_id,
                "approval_id": approval.approval_id,
                "status": "rejected",
            },
        )
        for step in steps if isinstance(steps, list) else []:
            if not isinstance(step, dict):
                continue
            step_id = str(step.get("id") or "")
            if step_id and step_id not in completed_ids and step_id != approval.node_id:
                self.store.append_event(
                    task_id,
                    {
                        "event": "agency.step.skipped",
                        "task_id": step_id,
                        "agent_id": str(step.get("role") or ""),
                        "status": "skipped",
                        "error": "agency_interaction_rejected",
                    },
                )
        self._append_terminal_event(
            task_id,
            {"event": "agency.run.rejected", "status": "rejected"},
        )
        current = self.store.reject(task_id, error="agency_interaction_rejected")
        await self._safe_update_run(
            current.run_id,
            status="failed",
            error="agency_interaction_rejected",
            metadata={"terminal_status": "rejected"},
        )
        return self.serialize(current)

    async def retry(
        self,
        *,
        source_task_id: str,
        prepared: PreparedAgencyExecution,
        managed_provider_required: bool = False,
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
            managed_provider_required=managed_provider_required,
        )

    async def revise(
        self,
        *,
        source_task_id: str,
        target_task_id: str,
        feedback: str,
        prepared: PreparedAgencyExecution,
        managed_provider_required: bool = False,
    ) -> dict[str, Any]:
        source = self.store.require(source_task_id)
        if (
            source.source_kind != "expert_team_agency"
            or source.status not in {"completed", "failed", "rejected"}
        ):
            raise AgencyExecutionValidationError(
                "只有已完成、失败或用户拒绝的专家团 DAG 可以返工。",
                code="agency_execution_not_revisable",
            )
        normalized_feedback = feedback.strip()
        if not 10 <= len(normalized_feedback) <= MAX_REVISION_FEEDBACK_CHARS:
            raise AgencyExecutionValidationError(
                "返工意见必须为 10-4000 个字符。",
                code="agency_execution_revision_invalid",
            )
        signature = hashlib.sha256(
            f"{source_task_id}\0{target_task_id}\0{normalized_feedback}".encode(
                "utf-8"
            )
        ).hexdigest()
        active_revision = next(
            (
                item
                for item in self.store.list_items(limit=1_000)
                if item.source_kind == "expert_team_agency"
                and item.status not in TERMINAL_STATUSES
                and str(
                    item.runtime_metadata.get("revision_parent_task_id") or ""
                )
                == source_task_id
            ),
            None,
        )
        if active_revision is not None:
            if str(
                active_revision.runtime_metadata.get("revision_request_digest")
                or ""
            ) == signature:
                return self.serialize(active_revision)
            raise AgencyExecutionValidationError(
                "该源任务已有另一项返工正在执行，请等待完成或先取消。",
                code="agency_revision_in_progress",
            )

        serialized = self.serialize(source)
        workflow_steps = [
            step
            for step in (
                source.workflow.get("steps", [])
                if isinstance(source.workflow, dict)
                else []
            )
            if isinstance(step, dict)
        ]
        steps_by_id = {
            str(step.get("id") or ""): step for step in workflow_steps
        }
        if target_task_id not in steps_by_id:
            raise AgencyExecutionValidationError(
                "返工目标步骤不在冻结工作流中。",
                code="agency_execution_revision_invalid",
            )
        completed_events = {
            str(event.get("task_id") or ""): event
            for event in serialized.get("steps", [])
            if event.get("status") == "completed"
            and str(event.get("output") or "").strip()
        }
        target_event = completed_events.get(target_task_id)
        if target_event is None:
            raise AgencyExecutionValidationError(
                "返工目标步骤尚未完成或没有可复用输出。",
                code="agency_execution_not_revisable",
            )

        affected_ids = {target_task_id}
        changed = True
        while changed:
            changed = False
            for step in workflow_steps:
                step_id = str(step.get("id") or "")
                dependencies = step.get("depends_on")
                dependencies = dependencies if isinstance(dependencies, list) else []
                if step_id not in affected_ids and any(
                    str(dependency) in affected_ids for dependency in dependencies
                ):
                    affected_ids.add(step_id)
                    changed = True
        # Previously incomplete steps must execute even when they are not downstream
        # of the feedback target. Every other completed step remains reusable.
        affected_ids.update(
            step_id for step_id in steps_by_id if step_id not in completed_events
        )
        completed_steps: list[dict[str, Any]] = []
        for step in workflow_steps:
            task_id = str(step.get("id") or "")
            event = completed_events.get(task_id)
            if event is None or task_id in affected_ids:
                continue
            restored_output = str(event["output"])
            if len(restored_output.encode("utf-8")) > 64 * 1024:
                raise AgencyExecutionValidationError(
                    f"步骤 {task_id} 的历史输出超过 64 KiB，不能安全返工。",
                    code="agency_execution_revision_invalid",
                )
            completed_steps.append(
                {
                    "task_id": task_id,
                    "output": restored_output,
                    "output_variable": str(step.get("output") or ""),
                    "acceptance": str(
                        event.get("acceptance") or step.get("acceptance") or ""
                    )[:4_000],
                    "agent_name": str(event.get("agent_name") or "")[:200],
                    "agent_emoji": str(event.get("agent_emoji") or "")[:16],
                }
            )
        previous_output = str(target_event["output"])
        if len(previous_output.encode("utf-8")) > 64 * 1024:
            raise AgencyExecutionValidationError(
                f"步骤 {target_task_id} 的上一版输出超过 64 KiB，不能安全返工。",
                code="agency_execution_revision_invalid",
            )
        revision = {
            "source_task_id": source_task_id,
            "target_task_id": target_task_id,
            "feedback": normalized_feedback,
            "previous_output": previous_output,
            "completed_steps": completed_steps,
        }
        source_metadata = source.runtime_metadata
        root_task_id = str(
            source_metadata.get("revision_root_task_id") or source_task_id
        )
        revision_index = int(
            source_metadata.get("revision_index") or 0
        ) + 1
        source_lineage_calls = int(
            serialized.get("lineage_model_calls")
            or serialized.get("model_calls")
            or 0
        )
        source_lineage_usage = serialized.get("lineage_usage") or serialized.get(
            "usage"
        ) or {}
        revision_metadata = {
            "revision_parent_task_id": source_task_id,
            "revision_root_task_id": root_task_id,
            "revision_index": revision_index,
            "revision_target_task_id": target_task_id,
            "revision_feedback": normalized_feedback,
            "revision_feedback_preview": normalized_feedback[:160],
            "revision_affected_task_ids": [
                str(step.get("id") or "")
                for step in workflow_steps
                if str(step.get("id") or "") in affected_ids
            ],
            "revision_request_digest": signature,
            "revision_lineage_model_calls_before": source_lineage_calls,
            "revision_lineage_usage_before": {
                "input_tokens": int(source_lineage_usage.get("input_tokens") or 0),
                "output_tokens": int(source_lineage_usage.get("output_tokens") or 0),
            },
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
            revision=revision,
            revision_metadata=revision_metadata,
            managed_provider_required=managed_provider_required,
        )

    async def cancel(self, task_id: str) -> dict[str, Any]:
        item = self.store.require(task_id)
        if item.source_kind != "expert_team_agency":
            raise AgencyExecutionValidationError("任务类型不匹配。")
        if item.status in TERMINAL_STATUSES:
            return self.serialize(item)
        if self.approval_store is not None and item.wait_id:
            approval = self.approval_store.get(item.wait_id)
            if approval is not None and approval.status in {"pending", "expired"}:
                try:
                    self.approval_store.cancel(
                        approval.approval_id,
                        revision=approval.revision,
                        message="agency_execution_cancelled",
                    )
                except Exception:
                    pass
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
        valid_wait_ids: set[str] = set()
        for item in self.store.list_items(limit=1_000):
            if (
                item.source_kind != "expert_team_agency"
                or item.status in TERMINAL_STATUSES
            ):
                continue
            approval = (
                self.approval_store.get(item.wait_id)
                if self.approval_store is not None and item.wait_id
                else None
            )
            continuation = dict(item.continuation or {})
            expected_request_type = str(continuation.get("kind") or "")
            checkpoint_matches = bool(
                item.status in {"waiting", "ready"}
                and item.wait_kind == "approval"
                and item.wait_id
                and approval is not None
                and approval.scope_type == "expert_team_agency"
                and approval.scope_id == item.task_id
                and approval.task_id == item.task_id
                and approval.run_id == item.run_id
                and approval.node_id == str(continuation.get("step_id") or "")
                and approval.request_type == expected_request_type
                and (
                    (item.status == "waiting" and approval.status in {"pending", "expired", "decided"})
                    or (item.status == "ready" and approval.status == "decided")
                )
            )
            if checkpoint_matches:
                valid_wait_ids.add(str(item.wait_id))
                continue

            code = (
                "agency_execution_interrupted"
                if item.status == "running"
                else "agency_interaction_invalid"
            )
            self._append_terminal_event(
                item.task_id,
                {
                    "event": "agency.run.failed",
                    "status": "failed",
                    "error": code,
                },
            )
            self.store.fail(item.task_id, error=code)
            recovered += 1

        if self.approval_store is not None:
            for approval in self.approval_store.list_requests(
                scope_type="expert_team_agency", limit=1_000
            ):
                if (
                    approval.status not in {"pending", "expired"}
                    or approval.approval_id in valid_wait_ids
                ):
                    continue
                try:
                    self.approval_store.cancel(
                        approval.approval_id,
                        revision=approval.revision,
                        operator="agency-recovery",
                        message="agency_interaction_orphaned",
                    )
                except Exception:
                    pass
        return recovered

    async def _run(
        self,
        *,
        task_id: str,
        goal: str,
        model_id: str,
        prepared: PreparedAgencyExecution,
        resume: Mapping[str, Any] | None = None,
        revision: Mapping[str, Any] | None = None,
        interaction_resume: Mapping[str, Any] | None = None,
        managed_run: AgencyManagedRun | None = None,
    ) -> None:
        item = self.store.require(task_id)
        managed_run_finished = False

        def finish_managed_run(
            status: Literal["passed", "failed", "uncertain", "cancelled"],
            *,
            reason_code: str | None = None,
        ) -> None:
            nonlocal managed_run_finished
            if managed_run_finished:
                return
            try:
                self._finish_managed_run(
                    managed_run,
                    task_id,
                    status,
                    reason_code=reason_code,
                )
            finally:
                managed_run_finished = True

        async def on_event(event: dict[str, Any]) -> None:
            current = self.store.require(task_id)
            if current.status not in TERMINAL_STATUSES:
                self.store.append_event(task_id, event)
                if (
                    event.get("event") == "agency.run.completed"
                    and str(event.get("final_output") or "")
                ):
                    # The completion event is the worker's durable commit point.
                    # Persist the Provider receipt before exposing completion so
                    # a reader never observes a billed run without its audit.
                    finish_managed_run("passed")
                    self.store.complete(
                        task_id,
                        result=str(event["final_output"]),
                    )

        try:
            result = await self._client(
                model_runner=(managed_run.complete if managed_run is not None else None)
            ).execute(
                goal=goal,
                model_id=model_id,
                workflow=prepared.workflow,
                agents=prepared.agents,
                skills=prepared.skills,
                resume=resume,
                revision=revision,
                interaction_resume=interaction_resume,
                on_event=on_event,
            )
            payload = result.payload
            if payload.get("status") == "waiting":
                finish_managed_run("passed")
                await self._suspend_interaction(task_id, payload)
                return
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
                finish_managed_run("passed")
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
            finish_managed_run(
                "cancelled",
                reason_code="agency_execution_cancelled",
            )
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
            managed_status: Literal["failed", "uncertain"] = "failed"
            if managed_run is not None and any(
                call.get("status") == "uncertain"
                for call in managed_run.receipt_summary().get("calls", [])
                if isinstance(call, dict)
            ):
                managed_status = "uncertain"
            finish_managed_run(
                managed_status,
                reason_code=str(code),
            )
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

    async def _suspend_interaction(
        self,
        task_id: str,
        payload: Mapping[str, Any],
    ) -> None:
        if self.approval_store is None:
            raise AgencyExecutionValidationError(
                "Agency HITL approval store is unavailable.",
                code="agency_worker_unavailable",
            )
        item = self.store.require(task_id)
        wait = payload.get("wait")
        wait = dict(wait) if isinstance(wait, Mapping) else {}
        step_id = str(wait.get("step_id") or "").strip()
        kind = str(wait.get("kind") or "").strip()
        prompt = str(wait.get("prompt") or "").strip()
        if kind not in {"human_input", "approval"} or not step_id or not prompt:
            raise AgencyExecutionValidationError(
                "Agency Worker returned an invalid HITL checkpoint.",
                code="agency_interaction_invalid",
            )
        request_type = "manual_input" if kind == "human_input" else "execution_gate"
        allowed_decisions = ["replace"] if kind == "human_input" else ["approve", "reject"]
        approval = self.approval_store.create_request(
            action_key=f"agency-hitl:{task_id}:{step_id}",
            request_type=request_type,
            task_id=task_id,
            run_id=item.run_id,
            node_id=step_id,
            node_title=next(
                (
                    str(step.get("name") or step_id)
                    for step in item.workflow.get("steps", [])
                    if isinstance(step, dict) and str(step.get("id") or "") == step_id
                ),
                step_id,
            ),
            scope_type="expert_team_agency",
            scope_id=task_id,
            timeout_seconds=HITL_WAIT_SECONDS,
            allowed_decisions=allowed_decisions,
            description=prompt,
            content_preview=str(wait.get("content_preview") or "")[:8_000],
            metadata={
                "interaction_kind": kind,
                "output_variable": str(wait.get("output_variable") or "")[:128],
                "upstream_revision": str(
                    item.runtime_metadata.get("upstream_revision") or ""
                ),
                "capability_snapshot_hash": str(
                    item.runtime_metadata.get("capability_snapshot_hash") or ""
                ),
            },
        )
        completed_steps = payload.get("completed_steps")
        continuation = {
            "step_id": step_id,
            "kind": request_type,
            "completed_steps": (
                list(completed_steps) if isinstance(completed_steps, list) else []
            ),
            "prior_model_calls": int(payload.get("model_calls") or 0),
            "prior_usage": dict(payload.get("usage") or {}),
            "prior_active_duration_ms": int(
                payload.get("active_duration_ms") or 0
            ),
        }
        self.store.suspend(
            task_id,
            approval_id=approval.approval_id,
            continuation=continuation,
            safe_event={
                "event": "agency.interaction.pending",
                "task_id": step_id,
                "approval_id": approval.approval_id,
                "request_type": request_type,
                "status": "waiting",
                "model_calls": continuation["prior_model_calls"],
                "cumulative_usage": continuation["prior_usage"],
            },
        )
        await self._safe_update_run(
            item.run_id,
            status="waiting",
            metadata={
                "approval_id": approval.approval_id,
                "interaction_step_id": step_id,
                "model_calls": continuation["prior_model_calls"],
                "usage": continuation["prior_usage"],
            },
        )

    def _client(
        self, *, model_runner: AgencyModelRunner | None = None
    ) -> AgencyExecutionClient:
        if self.client_factory is not None:
            client = self.client_factory()
            if model_runner is not None:
                client.model_runner = model_runner
            return client
        return AgencyExecutionClient(
            model_runner=model_runner or self.model_runner,
            worker_entry=self.worker_entry,
        )

    def _managed_run(
        self,
        task_id: str,
        segment_key: str,
        *,
        required: bool,
    ) -> AgencyManagedRun | None:
        if not required:
            return None
        if self.managed_run_factory is None:
            raise AgencyExecutionValidationError(
                "专家团 Managed Provider 执行器不可用，当前调用失败关闭。",
                code="provider_workload_policy_not_active",
            )
        managed_run = self.managed_run_factory(task_id, segment_key)
        if managed_run is None:
            raise AgencyExecutionValidationError(
                "专家团 Managed Provider 策略已变化，当前调用失败关闭。",
                code="provider_workload_policy_not_active",
            )
        return managed_run

    def _finish_managed_run(
        self,
        managed_run: AgencyManagedRun | None,
        task_id: str,
        status: Literal["passed", "failed", "uncertain", "cancelled"],
        *,
        reason_code: str | None = None,
    ) -> None:
        if managed_run is None:
            return
        receipt = managed_run.finish(status, reason_code=reason_code)
        self.store.append_event(
            task_id,
            {
                "event": "agency.provider.receipt",
                "status": status,
                "provider_route_receipts": receipt,
            },
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

    def serialize(self, item: Any) -> dict[str, Any]:
        public = WorkflowExecutionStore.serialize_public(item)
        latest_steps: dict[str, dict[str, Any]] = {}
        summary: dict[str, Any] = {}
        provider_route_receipts: list[dict[str, Any]] = []
        for event in item.events:
            event_name = str(event.get("event") or "")
            step_id = str(event.get("task_id") or "")
            if step_id and event_name.startswith("agency.step."):
                latest_steps[step_id] = dict(event)
            elif step_id and event_name == "agency.interaction.rejected":
                latest_steps[step_id] = {**event, "status": "rejected"}
            if isinstance(event.get("model_calls"), (int, float)):
                summary["model_calls"] = max(
                    0, int(event.get("model_calls") or 0)
                )
            cumulative_usage = event.get("cumulative_usage")
            if isinstance(cumulative_usage, dict):
                summary["usage"] = dict(cumulative_usage)
            provider_receipt = event.get("provider_route_receipts")
            if event_name == "agency.provider.receipt" and isinstance(
                provider_receipt, dict
            ):
                provider_route_receipts.append(dict(provider_receipt))
            if event_name in {
                "agency.run.completed",
                "agency.run.failed",
                "agency.run.cancelled",
                "agency.run.rejected",
            }:
                summary.update(event)
        completed_outputs = [
            event
            for event in latest_steps.values()
            if event.get("status") == "completed"
            and str(event.get("output") or "").strip()
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
                "task_type": (
                    "expert"
                    if str(step.get("type") or "normal") == "normal"
                    else str(step.get("type"))
                ),
                "interaction_prompt": str(step.get("prompt") or "")[:4_000],
                "output_variable": str(step.get("output") or "")[:128],
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
        current_model_calls = int(
            summary.get("model_calls")
            or item.runtime_metadata.get("initial_model_calls")
            or 0
        )
        current_usage = summary.get("usage") or item.runtime_metadata.get(
            "initial_usage"
        ) or {}
        lineage_calls_before = int(
            item.runtime_metadata.get("revision_lineage_model_calls_before") or 0
        )
        lineage_usage_before = item.runtime_metadata.get(
            "revision_lineage_usage_before"
        )
        lineage_usage_before = (
            lineage_usage_before
            if isinstance(lineage_usage_before, dict)
            else {}
        )
        lineage_usage = {
            "input_tokens": int(lineage_usage_before.get("input_tokens") or 0)
            + int(current_usage.get("input_tokens") or 0),
            "output_tokens": int(lineage_usage_before.get("output_tokens") or 0)
            + int(current_usage.get("output_tokens") or 0),
        }
        revision_parent_task_id = str(
            item.runtime_metadata.get("revision_parent_task_id") or ""
        )
        revision = None
        if revision_parent_task_id:
            revision = {
                "parent_task_id": revision_parent_task_id,
                "root_task_id": str(
                    item.runtime_metadata.get("revision_root_task_id") or ""
                ),
                "revision_index": int(
                    item.runtime_metadata.get("revision_index") or 0
                ),
                "target_task_id": str(
                    item.runtime_metadata.get("revision_target_task_id") or ""
                ),
                "feedback": str(
                    item.runtime_metadata.get("revision_feedback") or ""
                )[:MAX_REVISION_FEEDBACK_CHARS],
                "feedback_preview": str(
                    item.runtime_metadata.get("revision_feedback_preview") or ""
                )[:160],
                "affected_task_ids": [
                    str(value)[:64]
                    for value in (
                        item.runtime_metadata.get("revision_affected_task_ids")
                        if isinstance(
                            item.runtime_metadata.get("revision_affected_task_ids"),
                            list,
                        )
                        else []
                    )[:MAX_EXECUTION_STEPS]
                ],
            }
        approval_history: list[dict[str, Any]] = []
        pending_interaction = None
        if self.approval_store is not None:
            approvals = list(
                reversed(
                    self.approval_store.list_requests(
                        task_id=item.task_id, limit=MAX_HITL_INTERACTIONS + 2
                    )
                )
            )
            for approval in approvals:
                interaction = {
                    "approval_id": approval.approval_id,
                    "step_id": approval.node_id,
                    "kind": (
                        "human_input"
                        if approval.request_type == "manual_input"
                        else "approval"
                    ),
                    "prompt": approval.description,
                    "content_preview": approval.content_preview,
                    "allowed_decisions": list(approval.allowed_decisions),
                    "revision": approval.revision,
                    "status": approval.status,
                    "decision": approval.decision,
                    "input": (
                        approval.replacement_text
                        if approval.request_type == "manual_input"
                        and approval.status == "decided"
                        else None
                    ),
                    "message": (
                        approval.message
                        if approval.decision == "reject"
                        else None
                    ),
                    "created_at": approval.created_at,
                    "updated_at": approval.updated_at,
                    "expires_at": approval.expires_at,
                }
                approval_history.append(interaction)
                if approval.approval_id == item.wait_id:
                    pending_interaction = interaction
        return {
            **public,
            "steps": list(latest_steps.values()),
            "final_output": item.result,
            "quality_status": summary.get("quality_status"),
            "warnings": summary.get("warnings") or [],
            "model_calls": current_model_calls,
            "usage": current_usage,
            "lineage_model_calls": lineage_calls_before + current_model_calls,
            "lineage_usage": lineage_usage,
            "estimated_cost": None,
            "provider_route_receipts": provider_route_receipts,
            "error_code": item.error,
            "error_message": str(
                summary.get("message") or summary.get("error") or ""
            )[:4_000] or None,
            "retryable": retryable,
            "revisable": (
                item.status in {"completed", "failed", "rejected"}
                and bool(completed_outputs)
            ),
            "pending_interaction": pending_interaction,
            "interaction_history": approval_history,
            "revision": revision,
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
