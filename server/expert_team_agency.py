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
EXPERT_TEAM_AGENCY_MAX_STEPS = 8


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
    missing_sink_acceptance = [
        str(step.get("id") or "")
        for step in raw_steps
        if isinstance(step, Mapping)
        and str(step.get("id") or "") not in depended_on_ids
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
    for raw_step in raw_steps:
        assert isinstance(raw_step, Mapping)
        raw_id = str(raw_step.get("id") or "")
        task_id = task_ids[raw_id]
        source_agent_id = str(raw_step.get("role") or "").strip()
        expert = experts.get(source_agent_id)
        if expert is None:
            raise ValueError(
                f"Agency Orchestrator selected unknown expert {source_agent_id}."
            )
        if source_agent_id not in selected_ids:
            selected_ids.append(source_agent_id)
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
        objective = str(raw_step.get("task") or "").strip()
        if not objective:
            raise ValueError(f"Task {raw_id} has no objective.")
        acceptance = str(raw_step.get("acceptance") or "").strip()
        output_variable = output_variables[task_id]
        title = str(
            raw_step.get("name") or _field(expert, "name") or task_id
        ).strip()
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
                agent_id=source_agent_id,
                acceptance=acceptance[:2_000],
                method_skill_ids=(
                    [method_skill_id] if method_skill_id else []
                ),
            )
        )
        agents.append(
            MetaPlannerAgentBlueprint(
                task_id=task_id,
                name=title[:120],
                role_prompt=_field(expert, "prompt")[:20_000],
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
