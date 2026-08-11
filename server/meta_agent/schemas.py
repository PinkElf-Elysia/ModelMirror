from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class MetaAgentGenerateRequest(BaseModel):
    goal: str = Field(min_length=10, max_length=20_000)
    model_id: str = Field(default="deepseek/deepseek-chat", min_length=1, max_length=256)
    temperature: float = Field(default=0.3, ge=0, le=2)
    max_tasks: int = Field(default=5, ge=1, le=8)


class MetaAgentParameter(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    type: str = Field(default="string", max_length=40)
    description: str = Field(default="", max_length=1000)
    required: bool = True


class MetaAgentGeneratedAgent(BaseModel):
    name: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=1000)
    prompt: str = Field(default="", max_length=20_000)
    tool_names: list[str] | None = Field(default=None, max_length=16)


class MetaAgentSubTask(BaseModel):
    name: str = Field(min_length=1, max_length=96)
    description: str = Field(min_length=1, max_length=1600)
    reason: str | None = Field(default=None, max_length=1600)
    inputs: list[MetaAgentParameter] = Field(default_factory=list, max_length=16)
    outputs: list[MetaAgentParameter] = Field(default_factory=list, max_length=16)
    agent: MetaAgentGeneratedAgent | None = None
    agents: list[MetaAgentGeneratedAgent] | None = Field(default=None, max_length=4)


class MetaAgentPlan(BaseModel):
    thought: str = Field(default="", max_length=4000)
    sub_tasks: list[MetaAgentSubTask] = Field(default_factory=list, max_length=8)


class MetaAgentGenerateResponse(BaseModel):
    goal: str
    plan: MetaAgentPlan
    workflow: dict[str, Any]
    warnings: list[str] = Field(default_factory=list)
    validation: dict[str, Any]


class MetaPlannerScope(BaseModel):
    allowed_node_kinds: list[str] = Field(default_factory=list, max_length=40)
    external_xpert_ids: list[str] = Field(default_factory=list, max_length=20)
    knowledge_base_ids: list[str] = Field(default_factory=list, max_length=20)
    toolset_ids: list[str] = Field(default_factory=list, max_length=20)
    plugin_ids: list[str] = Field(default_factory=list, max_length=20)
    prompt_profile_ids: list[str] = Field(default_factory=list, max_length=20)
    middleware_ids: list[str] = Field(default_factory=list, max_length=30)
    agent_ids: list[str] = Field(default_factory=list, max_length=512)


class MetaPlannerGenerateRequest(BaseModel):
    goal: str = Field(min_length=10, max_length=20_000)
    mode: Literal["create", "update"] = "create"
    target_xpert_id: str | None = Field(default=None, max_length=160)
    planner_model_id: str = Field(min_length=1, max_length=300)
    default_agent_model_id: str = Field(min_length=1, max_length=300)
    temperature: float = Field(default=0.2, ge=0, le=1)
    max_agents: int = Field(default=5, ge=1, le=8)
    scope: MetaPlannerScope = Field(default_factory=MetaPlannerScope)


class MetaPlannerTask(BaseModel):
    task_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,47}$")
    title: str = Field(min_length=1, max_length=120)
    objective: str = Field(min_length=1, max_length=4_000)
    depends_on: list[str] = Field(default_factory=list, max_length=8)
    input_contract: list[str] = Field(default_factory=list, max_length=12)
    output_contract: str = Field(min_length=1, max_length=1_000)
    agent_id: str | None = Field(default=None, min_length=1, max_length=160)
    acceptance: str = Field(default="", max_length=2_000)


class MetaPlannerTaskPlan(BaseModel):
    summary: str = Field(min_length=1, max_length=4_000)
    assumptions: list[str] = Field(default_factory=list, max_length=12)
    tasks: list[MetaPlannerTask] = Field(min_length=1, max_length=8)


class MetaPlannerResourceBinding(BaseModel):
    task_id: str
    kind: Literal[
        "external_xpert",
        "knowledge_base",
        "toolset_resource",
        "plugin_resource",
    ]
    resource_id: str = Field(min_length=1, max_length=200)
    tool_name: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=1_000)
    top_k: int = Field(default=5, ge=1, le=50)
    score_threshold: float = Field(default=0, ge=0, le=1)


class MetaPlannerMiddlewareBinding(BaseModel):
    task_id: str
    middleware_id: str = Field(min_length=1, max_length=160)
    priority: int = Field(default=100, ge=0, le=10_000)
    config: dict[str, Any] = Field(default_factory=dict)


class MetaPlannerAgentBlueprint(BaseModel):
    task_id: str
    name: str = Field(min_length=1, max_length=120)
    role_prompt: str = Field(min_length=1, max_length=20_000)
    task_input: str = Field(min_length=1, max_length=8_000)
    output_variable: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
    model_id: str | None = Field(default=None, max_length=300)
    source_agent_id: str | None = Field(default=None, min_length=1, max_length=160)


class MetaPlannerBlueprint(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2_000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    starters: list[str] = Field(default_factory=list, max_length=8)
    agents: list[MetaPlannerAgentBlueprint] = Field(min_length=1, max_length=8)
    resources: list[MetaPlannerResourceBinding] = Field(
        default_factory=list, max_length=40
    )
    middleware: list[MetaPlannerMiddlewareBinding] = Field(
        default_factory=list, max_length=40
    )
    prompt_profile_ids: list[str] = Field(default_factory=list, max_length=20)


class MetaPlannerCapabilitySnapshot(BaseModel):
    version: str
    snapshot_hash: str
    generated_at: float
    node_registry_version: str
    nodes: list[dict[str, Any]]
    middleware: list[dict[str, Any]]
    external_xperts: list[dict[str, Any]]
    knowledge_bases: list[dict[str, Any]]
    toolsets: list[dict[str, Any]]
    plugins: list[dict[str, Any]]
    prompt_profiles: list[dict[str, Any]]
    models: list[dict[str, Any]]
    agents: list[dict[str, Any]] = Field(default_factory=list)
    default_scope: MetaPlannerScope


class MetaPlannerPreviewResponse(BaseModel):
    plan: MetaPlannerTaskPlan
    candidate: dict[str, Any]
    validation: dict[str, Any]
    warnings: list[str] = Field(default_factory=list)
    repair_used: bool = False
    capability_snapshot_version: str
    capability_snapshot_hash: str


class MetaPlannerGenerateResponse(BaseModel):
    proposal_id: str
    proposal_revision: int
    mode: Literal["create", "update"]
    target_xpert_id: str | None = None
    base_revision: int | None = None
    plan: MetaPlannerTaskPlan
    candidate: dict[str, Any]
    validation: dict[str, Any]
    warnings: list[str] = Field(default_factory=list)
    repair_used: bool = False
    capability_snapshot_version: str
    capability_snapshot_hash: str
