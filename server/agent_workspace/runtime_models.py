from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from .models import AGENT_ID_PATTERN, SKILL_ID_PATTERN, StrictModel


ApprovalMode = Literal["always-ask", "read-only", "allow-all", "deny-all"]
ThinkingLevel = Literal["low", "medium", "high", "xhigh"]
DEFAULT_AGENT_BUILDER_MODEL_ID = "deepseek/deepseek-v4-flash-0731"
SessionStatus = Literal["idle", "running", "waiting_approval", "failed"]
TaskStatus = Literal[
    "pending",
    "running",
    "waiting_approval",
    "completed",
    "failed",
    "stopped",
]
TaskKind = Literal["chat", "generate_agent"]
MessageRole = Literal["system", "user", "assistant", "tool"]
ApprovalStatus = Literal["pending", "approved", "rejected", "cancelled"]


class SessionCreateRequest(StrictModel):
    agent_id: str = Field(pattern=AGENT_ID_PATTERN)
    model_id: str = Field(min_length=1, max_length=256)
    thinking_level: ThinkingLevel = "medium"
    approval_mode: ApprovalMode = "always-ask"
    skillset_id: str = Field(
        default="general-agent-default",
        min_length=1,
        max_length=120,
        pattern=SKILL_ID_PATTERN,
    )
    title: str = Field(default="新会话", min_length=1, max_length=160)


class SessionUpdateRequest(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    approval_mode: ApprovalMode | None = None

    @model_validator(mode="after")
    def require_change(self) -> "SessionUpdateRequest":
        if self.title is None and self.approval_mode is None:
            raise ValueError("title or approval_mode is required")
        return self


class TaskCreateRequest(StrictModel):
    prompt: str = Field(min_length=1, max_length=1_048_576)
    model_id: str | None = Field(default=None, min_length=1, max_length=256)
    thinking_level: ThinkingLevel | None = None
    approval_mode: ApprovalMode | None = None

    @field_validator("prompt")
    @classmethod
    def reject_blank_prompt(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt cannot be blank")
        return value


class GenerateAgentRequest(StrictModel):
    prompt: str = Field(min_length=1, max_length=20_000)
    model_id: str = Field(
        default=DEFAULT_AGENT_BUILDER_MODEL_ID,
        min_length=1,
        max_length=256,
    )
    thinking_level: ThinkingLevel = "medium"
    approval_mode: ApprovalMode = "always-ask"

    @field_validator("prompt")
    @classmethod
    def reject_blank_prompt(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt cannot be blank")
        return value


class ApprovalDecisionRequest(StrictModel):
    decision: Literal["approve", "reject"]
    message: str = Field(default="", max_length=4_000)


class SessionRecord(StrictModel):
    session_id: str
    agent_id: str
    workspace_id: str
    title: str
    model_id: str
    thinking_level: ThinkingLevel
    approval_mode: ApprovalMode
    skillset_id: str
    status: SessionStatus
    parent_session_id: str | None = None
    depth: int = 0
    created_at: float
    updated_at: float


class MessageRecord(StrictModel):
    message_id: str
    session_id: str
    task_id: str | None = None
    sequence: int
    role: MessageRole
    content: str
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    created_at: float


class TaskRecord(StrictModel):
    task_id: str
    session_id: str
    kind: TaskKind
    prompt: str
    model_id: str
    thinking_level: ThinkingLevel
    approval_mode: ApprovalMode
    status: TaskStatus
    output: str = ""
    error: str = ""
    created_at: float
    updated_at: float
    started_at: float | None = None
    finished_at: float | None = None


class RuntimeEvent(StrictModel):
    sequence: int
    session_id: str
    task_id: str | None = None
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: float


class ApprovalRecord(StrictModel):
    approval_id: str
    session_id: str
    task_id: str
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any]
    status: ApprovalStatus
    decision_message: str = ""
    created_at: float
    decided_at: float | None = None


class SessionDetail(StrictModel):
    session: SessionRecord
    messages: list[MessageRecord]
    tasks: list[TaskRecord]
    approvals: list[ApprovalRecord]
    last_event_sequence: int = 0


class WorkspaceEntry(StrictModel):
    name: str
    path: str
    kind: Literal["file", "directory"]
    size: int = 0
    modified_at: float
