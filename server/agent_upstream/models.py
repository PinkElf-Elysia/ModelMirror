from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


UPSTREAM_REVISION = "047505dccc0cc16ad92be11011347d635f33ceb0"
ENGINE_PROTOCOL = "modelmirror.upstream-workbench/1"
DEFAULT_MODEL_BASE_ID = "deepseek-v4-pro-0813"

ThinkingLevel = Literal["low", "medium", "high", "xhigh"]
EngineShadowStatus = Literal[
    "pending",
    "running",
    "candidate_ready",
    "blocked",
    "budget_limited",
    "stopped",
    "interrupted",
    "failed",
]
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        "candidate_ready",
        "blocked",
        "budget_limited",
        "stopped",
        "interrupted",
        "failed",
    }
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EngineShadowRunCreate(StrictModel):
    objective: str = Field(min_length=1, max_length=100_000)
    model_base_id: str = Field(
        default=DEFAULT_MODEL_BASE_ID, min_length=1, max_length=256
    )
    thinking_level: ThinkingLevel = "medium"
    token_budget: int = Field(default=750_000, ge=100_000, le=1_000_000)
    max_goal_rounds: int = Field(default=12, ge=1, le=24)
    max_task_turns: int = Field(default=100, ge=1, le=200)

    @field_validator("objective")
    @classmethod
    def reject_blank_objective(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("objective cannot be blank")
        return clean


class EngineShadowRunRecord(StrictModel):
    run_id: str
    session_id: str
    status: EngineShadowStatus
    objective: str
    model_base_id: str
    resolved_model_id: str
    thinking_level: ThinkingLevel
    token_budget: int
    max_goal_rounds: int
    max_task_turns: int
    goal_round: int = 0
    model_turns: int = 0
    retry_count: int = 0
    token_total: int = 0
    usage_source: Literal["provider", "estimated", "none"] = "none"
    tool_calls: int = 0
    tool_failures: int = 0
    candidate_sha256: str = ""
    error_code: str = ""
    public_error: str = ""
    upstream_revision: str = UPSTREAM_REVISION
    protocol: str = ENGINE_PROTOCOL
    created_at: float
    updated_at: float
    started_at: float | None = None
    finished_at: float | None = None


class EngineShadowEvent(StrictModel):
    sequence: int
    run_id: str
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: float


class EngineShadowRunDetail(StrictModel):
    run: EngineShadowRunRecord
    last_event_sequence: int = 0


class EngineShadowWorkspaceEntry(StrictModel):
    name: str
    path: str
    kind: Literal["file", "directory"]
    size: int = 0
    modified_at: float


class ResolvedShadowModel(StrictModel):
    requested_base_id: str
    invocation_id: str
    context_window: int
    max_output_tokens: int
