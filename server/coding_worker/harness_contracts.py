from __future__ import annotations

from enum import StrEnum
from typing import Any, ClassVar

from pydantic import Field, field_validator, model_validator

from .contracts import (
    PolicyProfile,
    RepositoryInstruction,
    SAFE_ID,
    StrictModel,
    TaskBudget,
)


# Keep the existing persisted capability value. This is the normalized private
# contract revision, not a concrete supplier protocol version.
HARNESS_CONTRACT_VERSION = 4
HARNESS_CHECKPOINT_FORMAT_VERSION = 2
HARNESS_TOOL_NAMES = (
    "list_files",
    "read_file",
    "read_file_range",
    "glob_files",
    "search_text",
    "search_regex",
    "workspace_diff",
    "read_operation_output",
    "code_symbols",
    "code_definition",
    "code_references",
    "code_hover",
    "code_diagnostics",
    "write_file",
    "delete_file",
    "apply_changeset",
    "list_acceptance_checks",
    "run_check",
    "run_command",
    "run_shell",
    "install_dependencies",
    "query_documentation",
    "start_service",
    "service_status",
    "service_input",
    "stop_service",
    "create_subtask",
    "merge_subtask",
    "update_plan",
    "update_todo",
    "request_user_input",
    "compact_context",
)
INSPECT_HARNESS_TOOLS = HARNESS_TOOL_NAMES[:13] + (
    "list_acceptance_checks",
    "create_subtask",
)


class HarnessEventKind(StrEnum):
    SESSION_OPENED = "session_opened"
    MESSAGE = "message"
    PLAN = "plan"
    TODO = "todo"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    APPROVAL_REQUIRED = "approval_required"
    QUESTION = "question"
    COMPACTION = "compaction"
    CHECKPOINT = "checkpoint"
    TURN_COMPLETED = "turn_completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    USAGE = "usage"


class HarnessFailureKind(StrEnum):
    UNAVAILABLE = "unavailable"
    AUTHENTICATION = "authentication"
    RATE_LIMITED = "rate_limited"
    INVALID_RESPONSE = "invalid_response"
    POLICY = "policy"
    BUDGET = "budget"
    INTERRUPTED = "interrupted"


class HarnessUsage(StrictModel):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    cost_microusd: int | None = Field(default=None, ge=0)


class HarnessUsageEventData(StrictModel):
    usage: HarnessUsage


class HarnessPlanItem(StrictModel):
    step: str = Field(min_length=1, max_length=4096)
    status: str = Field(pattern=r"^(pending|in_progress|completed)$")


class HarnessPlanEventData(StrictModel):
    explanation: str | None = Field(default=None, max_length=16_384)
    items: tuple[HarnessPlanItem, ...] = Field(min_length=1, max_length=128)


class HarnessTodoItem(StrictModel):
    todo_id: str
    content: str = Field(min_length=1, max_length=4096)
    status: str = Field(pattern=r"^(pending|in_progress|completed|cancelled)$")

    @field_validator("todo_id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if SAFE_ID.fullmatch(value) is None:
            raise ValueError("todo id is invalid")
        return value


class HarnessTodoEventData(StrictModel):
    items: tuple[HarnessTodoItem, ...] = Field(max_length=256)


class HarnessToolStartedData(StrictModel):
    operation_id: str
    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    summary: str = Field(min_length=1, max_length=4096)

    @field_validator("operation_id")
    @classmethod
    def validate_operation_id(cls, value: str) -> str:
        if SAFE_ID.fullmatch(value) is None:
            raise ValueError("operation id is invalid")
        return value


class HarnessToolCompletedData(StrictModel):
    operation_id: str
    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    summary: str = Field(min_length=1, max_length=4096)
    success: bool
    artifact_id: str | None = None

    @field_validator("operation_id", "artifact_id")
    @classmethod
    def validate_optional_id(cls, value: str | None) -> str | None:
        if value is not None and SAFE_ID.fullmatch(value) is None:
            raise ValueError("tool event id is invalid")
        return value


class HarnessQuestionOption(StrictModel):
    option_id: str
    label: str = Field(min_length=1, max_length=200)

    @field_validator("option_id")
    @classmethod
    def validate_option_id(cls, value: str) -> str:
        if SAFE_ID.fullmatch(value) is None:
            raise ValueError("question option id is invalid")
        return value


class HarnessQuestionEventData(StrictModel):
    question_id: str
    prompt: str = Field(min_length=1, max_length=16_384)
    options: tuple[HarnessQuestionOption, ...] = Field(default=(), max_length=16)

    @field_validator("question_id")
    @classmethod
    def validate_question_id(cls, value: str) -> str:
        if SAFE_ID.fullmatch(value) is None:
            raise ValueError("question id is invalid")
        return value


class HarnessCompactionEventData(StrictModel):
    summary: str = Field(min_length=1, max_length=65_536)
    boundary_sequence: int = Field(ge=1)


class HarnessFailureEventData(StrictModel):
    failure_kind: HarnessFailureKind


_EVENT_DATA_MODELS: dict[HarnessEventKind, type[StrictModel]] = {
    HarnessEventKind.PLAN: HarnessPlanEventData,
    HarnessEventKind.TODO: HarnessTodoEventData,
    HarnessEventKind.TOOL_STARTED: HarnessToolStartedData,
    HarnessEventKind.TOOL_COMPLETED: HarnessToolCompletedData,
    HarnessEventKind.QUESTION: HarnessQuestionEventData,
    HarnessEventKind.COMPACTION: HarnessCompactionEventData,
    HarnessEventKind.USAGE: HarnessUsageEventData,
    HarnessEventKind.FAILED: HarnessFailureEventData,
}


class HarnessCapabilities(StrictModel):
    contract_version: int = HARNESS_CONTRACT_VERSION
    supports_streaming: bool = False
    supports_cancel: bool = False
    supports_checkpoint: bool = False
    supports_restore: bool = False
    supports_steering: bool = False
    supports_usage: bool = False
    supports_structured_plan: bool = False
    supports_todo: bool = False
    supports_questions: bool = False
    supports_compaction: bool = False
    supports_tool_boundaries: bool = False
    supports_turn_interrupt: bool = False
    tool_names: tuple[str, ...] = ()


class HarnessOpenRequest(StrictModel):
    _VALID_TOOLS: ClassVar[frozenset[str]] = frozenset(HARNESS_TOOL_NAMES)

    task_id: str
    workspace_id: str
    objective: str = Field(min_length=1, max_length=1_048_576)
    model_route: str
    policy_profile: PolicyProfile
    budget: TaskBudget
    workspace_tree_hash: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    repository_instructions: tuple[RepositoryInstruction, ...] = Field(
        default=(), max_length=16
    )
    tool_allowlist: tuple[str, ...] = HARNESS_TOOL_NAMES

    @field_validator("tool_allowlist")
    @classmethod
    def validate_tool_allowlist(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)) or any(
            tool not in cls._VALID_TOOLS for tool in value
        ):
            raise ValueError("harness tool allowlist is invalid")
        return value


class HarnessSession(StrictModel):
    session_id: str
    task_id: str
    capabilities: HarnessCapabilities


class HarnessEvent(StrictModel):
    kind: HarnessEventKind
    data: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_normalized_data(self) -> "HarnessEvent":
        model = _EVENT_DATA_MODELS.get(self.kind)
        if model is None:
            if self.kind in {
                HarnessEventKind.SESSION_OPENED,
                HarnessEventKind.TURN_COMPLETED,
                HarnessEventKind.CANCELLED,
                HarnessEventKind.CHECKPOINT,
            } and self.data:
                raise ValueError("boundary event data must be empty")
            if self.kind is HarnessEventKind.MESSAGE:
                text = self.data.get("text")
                if set(self.data) != {"text"} or not isinstance(text, str) or not text:
                    raise ValueError("message event data is invalid")
            if self.kind is HarnessEventKind.APPROVAL_REQUIRED:
                capability = self.data.get("capability")
                if (
                    set(self.data) != {"capability"}
                    or not isinstance(capability, str)
                    or SAFE_ID.fullmatch(capability) is None
                ):
                    raise ValueError("approval event data is invalid")
            return self
        normalized = model.model_validate(self.data).model_dump(mode="json")
        if normalized != self.data:
            raise ValueError("harness event data is not canonical")
        return self


class HarnessCheckpointCompatibility(StrictModel):
    """Stable private checkpoint binding; field names preserve Provider-v4 JSON."""

    contract_version: int = HARNESS_CONTRACT_VERSION
    format_version: int = HARNESS_CHECKPOINT_FORMAT_VERSION
    provider_family: str = Field(pattern=r"^[a-z][a-z0-9-]{1,31}$")
    provider_version: str = Field(min_length=1, max_length=64)
    task_id: str
    workspace_tree_hash: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )


class HarnessCheckpoint(StrictModel):
    checkpoint_id: str
    compatibility: HarnessCheckpointCompatibility | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


def harness_tools_for_policy(policy: PolicyProfile) -> tuple[str, ...]:
    if policy is PolicyProfile.INSPECT:
        return INSPECT_HARNESS_TOOLS
    if policy is PolicyProfile.DEVELOP:
        excluded = {"write_file", "delete_file", "query_documentation"}
        return tuple(item for item in HARNESS_TOOL_NAMES if item not in excluded)
    return tuple(
        item for item in HARNESS_TOOL_NAMES if item not in {"write_file", "delete_file"}
    )
