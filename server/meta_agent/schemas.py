from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

try:
    from server.workflow_native.node_contracts import WorkflowAgentPlannerConfig
except ModuleNotFoundError:
    from workflow_native.node_contracts import WorkflowAgentPlannerConfig


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


class ProviderRouteCallReceipt(BaseModel):
    call_sequence: int = Field(ge=1)
    model_id: str
    actual_model: str | None = None
    dispatched: bool = True
    status: Literal["passed", "failed", "uncertain", "cancelled"]
    error_code: str | None = None
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class ProviderRouteReceiptSummary(BaseModel):
    contract_version: str
    entry_id: Literal["meta_agent"] = "meta_agent"
    routing_mode: Literal["managed_required"] = "managed_required"
    run_reference: str
    status: Literal["running", "passed", "failed", "uncertain", "cancelled"]
    call_count: int = Field(ge=0)
    reason_codes: list[str] = Field(default_factory=list, max_length=20)
    calls: list[ProviderRouteCallReceipt] = Field(default_factory=list, max_length=8)


class MetaAgentGenerateResponse(BaseModel):
    goal: str
    plan: MetaAgentPlan
    workflow: dict[str, Any]
    warnings: list[str] = Field(default_factory=list)
    validation: dict[str, Any]
    run_id: str | None = None
    provider_route_receipts: ProviderRouteReceiptSummary | None = None


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
    method_skill_ids: list[str] = Field(default_factory=list, max_length=1)
    task_type: Literal["expert", "human_input", "approval"] = "expert"
    interaction_prompt: str = Field(default="", max_length=4_000)
    output_variable: str | None = Field(
        default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$"
    )

    @field_validator("method_skill_ids")
    @classmethod
    def validate_method_skill_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("method_skill_ids must be unique")
        if any(not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,159}", item) for item in value):
            raise ValueError("method_skill_ids contains an invalid Skill id")
        return value

    @model_validator(mode="after")
    def validate_task_type(self) -> "MetaPlannerTask":
        if self.task_type == "expert":
            if self.interaction_prompt:
                raise ValueError("Expert tasks cannot define interaction_prompt")
            return self
        if self.agent_id:
            raise ValueError("HITL tasks cannot bind agent_id")
        if self.method_skill_ids:
            raise ValueError("HITL tasks cannot bind method Skills")
        if self.acceptance:
            raise ValueError("HITL tasks cannot define acceptance")
        if not self.interaction_prompt.strip():
            raise ValueError("HITL tasks require interaction_prompt")
        if not self.output_variable:
            raise ValueError("HITL tasks require output_variable")
        return self


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


MetaPlannerValueType = Literal[
    "any",
    "null",
    "string",
    "number",
    "boolean",
    "object",
    "array",
]


class MetaPlannerIRInputBinding(BaseModel):
    port: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
    variable: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
    value_type: MetaPlannerValueType = "any"


class MetaPlannerIROutputBinding(BaseModel):
    port: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
    variable: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
    value_type: MetaPlannerValueType = "any"


class MetaPlannerIRNode(BaseModel):
    ref: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    kind: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2_000)
    task_ids: list[str] = Field(min_length=1, max_length=8)
    inputs: list[MetaPlannerIRInputBinding] = Field(default_factory=list, max_length=16)
    outputs: list[MetaPlannerIROutputBinding] = Field(
        default_factory=list, max_length=16
    )
    config: dict[str, Any] = Field(default_factory=dict)


class MetaPlannerIRControlEdge(BaseModel):
    source_ref: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    target_ref: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")


class MetaPlannerIRResourceBinding(BaseModel):
    target_ref: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
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


class MetaPlannerIRMiddlewareBinding(BaseModel):
    target_ref: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    middleware_id: str = Field(min_length=1, max_length=160)
    priority: int = Field(default=100, ge=0, le=10_000)
    config: dict[str, Any] = Field(default_factory=dict)


class MetaPlannerIRFinalOutput(BaseModel):
    node_ref: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    variable: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


MetaPlannerWorkflowAgentConfig = WorkflowAgentPlannerConfig


class MetaPlannerTypedBlueprintV2(BaseModel):
    ir_version: Literal[2] = 2
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2_000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    starters: list[str] = Field(default_factory=list, max_length=8)
    nodes: list[MetaPlannerIRNode] = Field(min_length=1, max_length=24)
    control_edges: list[MetaPlannerIRControlEdge] = Field(
        default_factory=list, max_length=40
    )
    resources: list[MetaPlannerIRResourceBinding] = Field(
        default_factory=list, max_length=40
    )
    middleware: list[MetaPlannerIRMiddlewareBinding] = Field(
        default_factory=list, max_length=40
    )
    prompt_profile_ids: list[str] = Field(default_factory=list, max_length=20)
    final_output: MetaPlannerIRFinalOutput


class MetaPlannerCapabilitySnapshot(BaseModel):
    version: str
    ir_version: Literal[2] = 2
    # Persisted V2 proposals did not carry NodeContract metadata. Keep them
    # readable; newly built snapshots always set the V3 values explicitly.
    contract_version: int = 2
    contract_checksum: str = ""
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
    run_id: str | None = None
    provider_route_receipts: ProviderRouteReceiptSummary | None = None
