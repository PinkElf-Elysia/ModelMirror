from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

try:
    from server.meta_agent.schemas import (
        MetaPlannerAgentBlueprint,
        MetaPlannerBlueprint,
        MetaPlannerTask,
        MetaPlannerTaskPlan,
    )
except ModuleNotFoundError:
    from meta_agent.schemas import (
        MetaPlannerAgentBlueprint,
        MetaPlannerBlueprint,
        MetaPlannerTask,
        MetaPlannerTaskPlan,
    )


AGENCY_UPSTREAM_PROJECT = "jnMetaCode/agency-orchestrator"
EXPERT_TEAM_AGENCY_MAX_STEPS = 6
_WORKFLOW_MOUSTACHE_PATTERN = re.compile(
    r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}"
)
_WORKFLOW_FORMAT_PATTERN = re.compile(
    r"(?<!\{)\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}(?!\})"
)


def _literalize_unbound_role_placeholders(
    role_prompt: str,
    *,
    available_variables: set[str],
) -> str:
    """Keep catalog prompt examples literal unless the DAG actually provides them."""

    sanitized = _WORKFLOW_MOUSTACHE_PATTERN.sub(
        lambda match: (
            match.group(0)
            if match.group(1) in available_variables
            else "[" + match.group(1) + "]"
        ),
        role_prompt,
    )
    # Catalog prompts contain Python/SQL-style examples such as ``{city}``.
    # ModelMirror only renders moustache variables at runtime, while the static
    # workflow validator also recognizes Python formatter fields.  Keep those
    # catalog examples readable but outside the workflow variable namespace.
    return _WORKFLOW_FORMAT_PATTERN.sub(
        lambda match: "[" + match.group(1) + "]",
        sanitized,
    )


class ExpertTeamAssetTeamWriteRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1_000)
    agent_ids: list[str] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def validate_agent_ids(self) -> "ExpertTeamAssetTeamWriteRequest":
        if len(self.agent_ids) != len(set(self.agent_ids)):
            raise ValueError("agent_ids must be unique")
        return self


class ExpertTeamAssetTemplateWriteRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=10, max_length=20_000)
    note: str = Field(default="", max_length=500)


class ExpertTeamPlanPreviewRequest(BaseModel):
    goal: str = Field(min_length=10, max_length=20_000)
    planner_model_id: str = Field(min_length=1, max_length=300)
    default_agent_model_id: str = Field(min_length=1, max_length=300)
    mode: Literal["auto", "pinned"] = "auto"
    pinned_agent_ids: list[str] = Field(default_factory=list, max_length=6)
    max_agents: int = Field(default=5, ge=1, le=6)
    temperature: float = Field(default=0.2, ge=0, le=1)
    knowledge_base_id: str | None = Field(default=None, min_length=1, max_length=160)
    allow_knowledge_context: bool = False
    method_skill_id: str | None = Field(default=None, min_length=1, max_length=160)

    @model_validator(mode="after")
    def validate_lineup(self) -> "ExpertTeamPlanPreviewRequest":
        if len(self.pinned_agent_ids) != len(set(self.pinned_agent_ids)):
            raise ValueError("pinned_agent_ids must be unique")
        if self.mode == "pinned" and not self.pinned_agent_ids:
            raise ValueError("pinned mode requires pinned_agent_ids")
        if (
            self.mode == "pinned"
            and len(self.pinned_agent_ids) > self.max_agents
        ):
            raise ValueError("pinned_agent_ids cannot exceed max_agents")
        if self.mode == "auto" and self.pinned_agent_ids:
            raise ValueError("auto mode cannot set pinned_agent_ids")
        if self.knowledge_base_id and not self.allow_knowledge_context:
            raise ValueError(
                "knowledge_base_id requires explicit allow_knowledge_context consent"
            )
        if self.allow_knowledge_context and not self.knowledge_base_id:
            raise ValueError(
                "allow_knowledge_context requires knowledge_base_id"
            )
        return self


class ExpertTeamAgencyCapabilities(BaseModel):
    enabled: bool
    worker_available: bool
    upstream_project: str = AGENCY_UPSTREAM_PROJECT
    upstream_revision: str
    supported_modes: list[Literal["auto", "pinned"]] = Field(
        default_factory=lambda: ["auto", "pinned"]
    )
    max_agents: int = 6
    max_steps: int = EXPERT_TEAM_AGENCY_MAX_STEPS
    execution: dict[str, Any] | None = None


class ExpertTeamPlanPreviewResponse(BaseModel):
    plan: MetaPlannerTaskPlan
    candidate: dict[str, Any]
    workflow: dict[str, Any]
    validation: dict[str, Any]
    selected_agents: list[dict[str, Any]]
    baseline_matches: list[dict[str, Any]]
    knowledge_context: dict[str, Any] | None = None
    method_skill: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)
    repair_used: bool = False
    model_calls: int = Field(default=0, ge=0, le=3)
    usage: dict[str, int] = Field(default_factory=dict)
    capability_snapshot_version: str
    capability_snapshot_hash: str
    upstream_project: str = AGENCY_UPSTREAM_PROJECT
    upstream_revision: str


def _field(record: Any, name: str, default: str = "") -> str:
    if isinstance(record, Mapping):
        value = record.get(name, default)
    else:
        value = getattr(record, name, default)
    return str(value or default).strip()


def _task_identifier(value: Any) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "_", str(value or "").strip().lower())
    normalized = re.sub(r"_+", "_", normalized).strip("_-")
    if not normalized or not normalized[0].isalpha():
        normalized = f"task_{normalized}" if normalized else "task"
    return normalized[:48]


def _output_identifier(value: Any, fallback: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_]+", "_", str(value or "").strip())
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    if not normalized or normalized[0].isdigit():
        normalized = f"{fallback}_{normalized}" if normalized else fallback
    return normalized[:128]


def build_meta_planner_inputs(
    worker_result: Mapping[str, Any],
    expert_records: Iterable[Any],
    *,
    default_agent_model_id: str,
    goal: str,
    method_skill_id: str | None = None,
) -> tuple[MetaPlannerTaskPlan, MetaPlannerBlueprint, list[dict[str, Any]]]:
    """Convert the pinned Agency DAG into existing Meta Planner V2 contracts."""

    validation = worker_result.get("validation")
    if not isinstance(validation, Mapping) or validation.get("valid") is not True:
        raw_errors = validation.get("errors") if isinstance(validation, Mapping) else []
        details = [
            str(item).replace("\r", " ").replace("\n", " ")[:500]
            for item in (raw_errors if isinstance(raw_errors, list) else [])[:6]
        ]
        suffix = f": {'; '.join(details)}" if details else "."
        raise ValueError(
            f"Agency Orchestrator did not return a valid workflow{suffix}"
        )
    workflow = validation.get("workflow")
    if not isinstance(workflow, Mapping):
        raise ValueError("Agency Orchestrator workflow payload is missing.")
    raw_inputs = workflow.get("inputs") or []
    if raw_inputs:
        input_names = [
            str(item.get("name") or "").strip()
            for item in raw_inputs
            if isinstance(item, Mapping)
        ]
        detail = ", ".join(name for name in input_names if name) or "unnamed"
        raise ValueError(
            "Agency Orchestrator workflow contains unsupported top-level "
            f"inputs: {detail}."
        )
    raw_steps = workflow.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError("Agency Orchestrator workflow has no steps.")

    experts = {_field(item, "id"): item for item in expert_records}
    raw_ids = [
        str(step.get("id") or "")
        for step in raw_steps
        if isinstance(step, Mapping)
    ]
    if len(raw_ids) != len(raw_steps) or len(raw_ids) != len(set(raw_ids)):
        raise ValueError("Agency Orchestrator task IDs are missing or duplicated.")
    depended_on_ids = {
        str(dependency)
        for step in raw_steps
        if isinstance(step, Mapping)
        for dependency in (step.get("depends_on") or [])
    }
    sink_steps = [
        step
        for step in raw_steps
        if isinstance(step, Mapping)
        and str(step.get("id") or "") not in depended_on_ids
    ]
    if len(sink_steps) == 1 and str(
        sink_steps[0].get("type") or "normal"
    ).strip() in {"human_input", "approval"}:
        raise ValueError(
            "Agency Orchestrator final task must be an expert task, not a HITL interaction."
        )
    missing_sink_acceptance = [
        str(step.get("id") or "")
        for step in sink_steps
        if str(step.get("type") or "normal").strip() == "normal"
        and not str(step.get("acceptance") or "").strip()
    ]
    if missing_sink_acceptance:
        raise ValueError(
            "Agency Orchestrator final tasks are missing acceptance criteria: "
            + ", ".join(missing_sink_acceptance)
        )
    task_ids = {raw_id: _task_identifier(raw_id) for raw_id in raw_ids}
    if len(set(task_ids.values())) != len(task_ids):
        raise ValueError("Agency Orchestrator task IDs collide after normalization.")
    output_variables: dict[str, str] = {}
    for raw_step in raw_steps:
        assert isinstance(raw_step, Mapping)
        raw_id = str(raw_step.get("id") or "")
        task_id = task_ids[raw_id]
        output_variables[task_id] = _output_identifier(
            raw_step.get("output"), f"{task_id}_output"
        )

    tasks: list[MetaPlannerTask] = []
    agents: list[MetaPlannerAgentBlueprint] = []
    selected_ids: list[str] = []
    interaction_count = 0
    for raw_step in raw_steps:
        assert isinstance(raw_step, Mapping)
        raw_id = str(raw_step.get("id") or "")
        task_id = task_ids[raw_id]
        raw_type = str(raw_step.get("type") or "normal").strip()
        if raw_type not in {"normal", "human_input", "approval"}:
            raise ValueError(f"Task {raw_id} has unsupported type {raw_type}.")
        task_type = "expert" if raw_type == "normal" else raw_type
        source_agent_id = str(raw_step.get("role") or "").strip()
        expert = experts.get(source_agent_id) if task_type == "expert" else None
        if task_type == "expert":
            if expert is None:
                raise ValueError(
                    f"Agency Orchestrator selected unknown expert {source_agent_id}."
                )
            if source_agent_id not in selected_ids:
                selected_ids.append(source_agent_id)
        else:
            interaction_count += 1
            if interaction_count > 2:
                raise ValueError("Agency Orchestrator generated more than two HITL steps.")
        raw_dependencies = raw_step.get("depends_on") or []
        if not isinstance(raw_dependencies, list):
            raise ValueError(f"Task {raw_id} has invalid dependencies.")
        unknown_dependencies = [
            str(item) for item in raw_dependencies if str(item) not in task_ids
        ]
        if unknown_dependencies:
            raise ValueError(
                f"Task {raw_id} references unknown dependencies: "
                + ", ".join(unknown_dependencies)
            )
        depends_on = [task_ids[str(item)] for item in raw_dependencies]
        objective = str(raw_step.get("task") or raw_step.get("prompt") or "").strip()
        if not objective:
            raise ValueError(f"Task {raw_id} has no objective.")
        acceptance = str(raw_step.get("acceptance") or "").strip()
        output_variable = output_variables[task_id]
        title = str(raw_step.get("name") or (
            _field(expert, "name") if expert is not None else (
                "人工输入" if task_type == "human_input" else "人工审批"
            )
        ) or task_id).strip()
        interaction_prompt = (
            str(raw_step.get("prompt") or objective).strip()[:4_000]
            if task_type != "expert"
            else ""
        )
        tasks.append(
            MetaPlannerTask(
                task_id=task_id,
                title=title[:120],
                objective=objective[:4_000],
                depends_on=depends_on,
                input_contract=(
                    [
                        output_variables[task_ids[str(item)]]
                        for item in raw_dependencies
                    ]
                    if raw_dependencies
                    else ["user_input"]
                ),
                output_contract=(
                    f"Produce {output_variable}. "
                    + (acceptance or "Deliver the assigned expert result.")
                )[:1_000],
                agent_id=source_agent_id if task_type == "expert" else None,
                acceptance=acceptance[:2_000] if task_type == "expert" else "",
                method_skill_ids=(
                    [method_skill_id]
                    if method_skill_id and task_type == "expert"
                    else []
                ),
                task_type=task_type,
                interaction_prompt=interaction_prompt,
                output_variable=output_variable,
            )
        )
        if expert is not None:
            role_variables = {
                "user_input",
                "conversation_history",
                *(output_variables[task_ids[str(item)]] for item in raw_dependencies),
            }
            agents.append(
                MetaPlannerAgentBlueprint(
                    task_id=task_id,
                    name=title[:120],
                    role_prompt=_literalize_unbound_role_placeholders(
                        _field(expert, "prompt")[:20_000],
                        available_variables=role_variables,
                    ),
                    task_input=objective[:8_000],
                    output_variable=output_variable,
                    model_id=default_agent_model_id,
                    source_agent_id=source_agent_id,
                )
            )

    selected = [
        {
            "id": agent_id,
            "name": _field(experts[agent_id], "name"),
            "department": _field(experts[agent_id], "department", "未分类"),
            "expertise": _field(experts[agent_id], "expertise"),
            "scenarios": _field(experts[agent_id], "scenarios"),
            "emoji": _field(experts[agent_id], "emoji") or None,
        }
        for agent_id in selected_ids
    ]
    plan = MetaPlannerTaskPlan(
        summary=str(
            workflow.get("description") or workflow.get("name") or goal
        ).strip()[:4_000],
        assumptions=[
            "The preview uses the current ModelMirror expert catalog.",
            "The preview does not start execution automatically.",
            "输入未明确的日期、市场、容量、流量与基础设施必须标记为待确认假设，不得作为事实。",
        ],
        tasks=tasks,
    )
    blueprint = MetaPlannerBlueprint(
        name=str(workflow.get("name") or "专家团智能组队")[:120],
        description=str(workflow.get("description") or goal)[:2_000],
        tags=["expert-team", "agency-orchestrator"],
        starters=[goal[:1_000]],
        agents=agents,
    )
    return plan, blueprint, selected


def compile_expert_team_hitl_candidate(
    *,
    plan: MetaPlannerTaskPlan,
    blueprint: MetaPlannerBlueprint,
    default_agent_model_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Compile HITL only for the Expert Team surface.

    The generic Meta Planner compiler intentionally keeps human_intervention
    unavailable to public Xpert and evaluation surfaces. This scoped compiler
    uses the same native node and validation contracts without widening them.
    """

    task_by_id = {task.task_id: task for task in plan.tasks}
    agent_by_task = {agent.task_id: agent for agent in blueprint.agents}
    children = {task.task_id: [] for task in plan.tasks}
    indegree = {task.task_id: len(task.depends_on) for task in plan.tasks}
    for task in plan.tasks:
        for dependency in task.depends_on:
            if dependency not in children:
                raise ValueError(f"Task {task.task_id} references unknown dependency.")
            children[dependency].append(task.task_id)
    pending = sorted(task_id for task_id, count in indegree.items() if count == 0)
    order: list[str] = []
    levels: dict[str, int] = {}
    while pending:
        task_id = pending.pop(0)
        order.append(task_id)
        task = task_by_id[task_id]
        levels[task_id] = max(
            (levels[dependency] for dependency in task.depends_on), default=-1
        ) + 1
        for child in sorted(children[task_id]):
            indegree[child] -= 1
            if indegree[child] == 0:
                pending.append(child)
                pending.sort()
    if len(order) != len(plan.tasks):
        raise ValueError("Task plan contains a dependency cycle.")
    sinks = [task_id for task_id in order if not children[task_id]]
    if len(sinks) != 1 or task_by_id[sinks[0]].task_type != "expert":
        raise ValueError("HITL plan requires one final expert sink.")

    nodes: list[dict[str, Any]] = [
        {
            "id": "input",
            "type": "input",
            "position": {"x": 40, "y": 160},
            "data": {
                "kind": "input",
                "title": "Conversation input",
                "variableName": "user_input",
                "historyVariable": "conversation_history",
            },
        }
    ]
    edges: list[dict[str, Any]] = []
    node_ids: dict[str, str] = {}
    outputs: dict[str, str] = {}
    level_rows: dict[int, int] = {}
    for task_id in order:
        task = task_by_id[task_id]
        level = levels[task_id]
        row = level_rows.get(level, 0)
        level_rows[level] = row + 1
        output_variable = task.output_variable or f"{task_id}_output"
        outputs[task_id] = output_variable
        node_id = (
            f"agent_{task_id}"
            if task.task_type == "expert"
            else f"hitl_{task_id}"
        )
        node_ids[task_id] = node_id
        if task.task_type == "expert":
            agent = agent_by_task.get(task_id)
            if agent is None:
                raise ValueError(f"Expert task {task_id} has no blueprint agent.")
            role_variables = {
                "user_input",
                "conversation_history",
                *(outputs[dependency] for dependency in task.depends_on),
            }
            task_input = agent.task_input.strip()
            if not task.depends_on and "{{user_input}}" not in task_input:
                task_input += "\n\nUser request:\n{{user_input}}"
            missing = [
                outputs[dependency]
                for dependency in task.depends_on
                if f"{{{{{outputs[dependency]}}}}}" not in task_input
            ]
            if missing:
                task_input += "\n\nDependency results:\n" + "\n".join(
                    f"- {variable}: {{{{{variable}}}}}" for variable in missing
                )
            data: dict[str, Any] = {
                "kind": "workflow_agent",
                "title": agent.name,
                "description": task.objective,
                "agentName": agent.name,
                "modelId": agent.model_id or default_agent_model_id,
                "rolePrompt": _literalize_unbound_role_placeholders(
                    agent.role_prompt,
                    available_variables=role_variables,
                ),
                "taskInput": task_input,
                "toolMode": "none",
                "toolNames": "",
                "maxIterations": "6",
                "parallelToolCalls": "false",
                "maxToolConcurrency": "2",
                "maxToolCalls": "12",
                "maxToolDepth": "4",
                "outputVariable": output_variable,
                "exceptionHandling": "fail",
                "sourceAgentId": agent.source_agent_id,
                "acceptanceCriteria": task.acceptance,
                "methodSkillIds": task.method_skill_ids,
                "plannerRef": f"agent_{task_id}",
                "plannerTaskIds": [task_id],
            }
            node_type = "workflow_agent"
        else:
            data = {
                "kind": "human_intervention",
                "title": task.title,
                "description": task.objective,
                "prompt": task.interaction_prompt,
                "interactionMode": (
                    "approval" if task.task_type == "approval" else "input"
                ),
                "outputVariable": output_variable,
                "plannerRef": f"hitl_{task_id}",
                "plannerTaskIds": [task_id],
            }
            node_type = "human_intervention"
        nodes.append(
            {
                "id": node_id,
                "type": node_type,
                "position": {"x": 300 + level * 340, "y": 80 + row * 260},
                "data": data,
            }
        )
        if task.depends_on:
            for dependency in task.depends_on:
                edges.append(
                    {
                        "id": f"edge_{dependency}_{task_id}",
                        "source": node_ids[dependency],
                        "target": node_id,
                    }
                )
        else:
            edges.append(
                {"id": f"edge_input_{task_id}", "source": "input", "target": node_id}
            )

    output_task_id = sinks[0]
    nodes.append(
        {
            "id": "output",
            "type": "output",
            "position": {"x": 300 + (levels[output_task_id] + 1) * 340, "y": 160},
            "data": {
                "kind": "output",
                "title": "Final output",
                "outputVariable": outputs[output_task_id],
            },
        }
    )
    edges.append(
        {
            "id": f"edge_{output_task_id}_output",
            "source": node_ids[output_task_id],
            "target": "output",
        }
    )
    workflow = {
        "id": "expert_team_agency_hitl",
        "title": blueprint.name,
        "version": "1.0.0",
        "source": "workflow-native",
        "nodes": nodes,
        "edges": edges,
    }
    candidate = {
        "name": blueprint.name,
        "description": blueprint.description,
        "tags": list(dict.fromkeys(blueprint.tags)),
        "starters": list(dict.fromkeys(blueprint.starters)),
        "draft": {"workflow": workflow},
    }
    return candidate, workflow
