from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


STRUCTURE_MUTATION_OPERATIONS = (
    "add_control_node",
    "remove_control_node",
    "replace_control_node",
    "add_control_edge",
    "remove_control_edge",
    "bind_resource",
    "unbind_resource",
    "bind_middleware",
    "unbind_middleware",
)


class EvolutionBudget(BaseModel):
    repetitions: int = Field(default=1, ge=1, le=3)
    max_concurrency: int = Field(default=2, ge=1, le=4)
    case_timeout_seconds: int = Field(default=120, ge=10, le=600)
    max_model_calls: int = Field(default=16, ge=1, le=64)
    max_tool_calls: int = Field(default=24, ge=0, le=100)
    max_estimated_tokens: int = Field(default=64_000, ge=1_000, le=500_000)
    max_output_chars: int = Field(default=20_000, ge=1_000, le=20_000)


class EvolutionStructureScope(BaseModel):
    allowed_node_kinds: list[str] = Field(default_factory=list, max_length=40)
    external_xpert_ids: list[str] = Field(default_factory=list, max_length=20)
    knowledge_base_ids: list[str] = Field(default_factory=list, max_length=20)
    toolset_ids: list[str] = Field(default_factory=list, max_length=20)
    plugin_ids: list[str] = Field(default_factory=list, max_length=20)
    middleware_ids: list[str] = Field(default_factory=list, max_length=30)


class EvolutionMutationPolicy(BaseModel):
    allowed_operations: list[
        Literal[
            "add_control_node",
            "remove_control_node",
            "replace_control_node",
            "add_control_edge",
            "remove_control_edge",
            "bind_resource",
            "unbind_resource",
            "bind_middleware",
            "unbind_middleware",
        ]
    ] = Field(default_factory=lambda: list(STRUCTURE_MUTATION_OPERATIONS))
    max_operations_per_candidate: int = Field(default=4, ge=1, le=8)
    max_added_nodes: int = Field(default=4, ge=0, le=4)
    max_removed_nodes: int = Field(default=4, ge=0, le=4)

    @model_validator(mode="after")
    def validate_operations(self) -> "EvolutionMutationPolicy":
        if len(set(self.allowed_operations)) != len(self.allowed_operations):
            raise ValueError("Structure mutation operations must be unique.")
        if not self.allowed_operations:
            raise ValueError("At least one structure mutation operation is required.")
        return self


class EvolutionStructureGate(BaseModel):
    min_score_delta: float = Field(default=0.01, ge=0, le=1)
    max_metric_regression: float = Field(default=0.02, ge=0, le=1)
    max_model_call_increase_ratio: float = Field(default=1.0, ge=0, le=10)
    max_token_increase_ratio: float = Field(default=1.0, ge=0, le=10)
    max_p95_latency_increase_ratio: float = Field(default=1.0, ge=0, le=10)


class StructureMutation(BaseModel):
    op: Literal[
        "add_control_node",
        "remove_control_node",
        "replace_control_node",
        "add_control_edge",
        "remove_control_edge",
        "bind_resource",
        "unbind_resource",
        "bind_middleware",
        "unbind_middleware",
    ]
    ref: str | None = Field(default=None, max_length=80)
    node_id: str | None = Field(default=None, max_length=128)
    edge_id: str | None = Field(default=None, max_length=128)
    kind: str | None = Field(default=None, max_length=80)
    source: str | None = Field(default=None, max_length=128)
    target: str | None = Field(default=None, max_length=128)
    source_handle: str | None = Field(default=None, max_length=80)
    target_handle: str | None = Field(default=None, max_length=80)
    agent_node_id: str | None = Field(default=None, max_length=128)
    resource_id: str | None = Field(default=None, max_length=200)
    middleware_id: str | None = Field(default=None, max_length=160)
    priority: int = Field(default=100, ge=0, le=10_000)
    data: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)


class EvolutionRunRequest(BaseModel):
    evolution_kind: Literal["prompt", "structure"] = "prompt"
    target_kind: Literal["xpert", "prompt_profile"]
    target_id: str = Field(min_length=1, max_length=200)
    target_revision: int = Field(ge=1)
    prompt_fields: list[str] = Field(default_factory=list, max_length=3)
    host_xpert_id: str | None = Field(default=None, max_length=200)
    host_xpert_version: int | None = Field(default=None, ge=1)
    dataset_id: str = Field(min_length=1, max_length=200)
    dataset_version: int = Field(ge=1)
    optimizer_model_id: str = Field(min_length=1, max_length=240)
    model_policy: Literal["snapshot", "override"] = "snapshot"
    override_model_id: str | None = Field(default=None, max_length=240)
    judge_model_id: str | None = Field(default=None, max_length=240)
    seed: int = Field(default=42, ge=0, le=2_147_483_647)
    generations: int = Field(default=2, ge=1, le=3)
    population_size: int = Field(default=4, ge=2, le=5)
    min_score_delta: float = Field(default=0.01, ge=0, le=1)
    max_metric_regression: float = Field(default=0.02, ge=0, le=1)
    budget: EvolutionBudget = Field(default_factory=EvolutionBudget)
    default_agent_model_id: str | None = Field(default=None, max_length=300)
    scope: EvolutionStructureScope = Field(default_factory=EvolutionStructureScope)
    mutation_policy: EvolutionMutationPolicy = Field(
        default_factory=EvolutionMutationPolicy
    )
    gate: EvolutionStructureGate = Field(default_factory=EvolutionStructureGate)

    @model_validator(mode="after")
    def validate_target(self) -> "EvolutionRunRequest":
        if self.model_policy == "override" and not self.override_model_id:
            raise ValueError("override_model_id is required for override model policy.")
        if self.evolution_kind == "structure":
            if self.target_kind != "xpert":
                raise ValueError("Structure evolution only supports Xpert drafts.")
            if self.prompt_fields:
                raise ValueError("Structure evolution does not accept prompt_fields.")
            if self.host_xpert_id or self.host_xpert_version:
                raise ValueError("Structure evolution does not use a host Xpert.")
            return self
        if self.target_kind == "xpert":
            if not self.prompt_fields:
                raise ValueError("Xpert evolution requires at least one Prompt field.")
            if len(set(self.prompt_fields)) != len(self.prompt_fields):
                raise ValueError("Prompt fields must be unique.")
            if self.host_xpert_id or self.host_xpert_version:
                raise ValueError("Xpert evolution does not use a host Xpert.")
        else:
            if self.prompt_fields:
                raise ValueError("Prompt Profile evolution does not accept prompt_fields.")
            if not self.host_xpert_id or not self.host_xpert_version:
                raise ValueError(
                    "Prompt Profile evolution requires a published host Xpert version."
                )
        return self


class EvolutionPreflightRequest(EvolutionRunRequest):
    pass
