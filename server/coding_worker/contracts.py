from __future__ import annotations

import math
import re
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
SAFE_ROUTE = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,127}$")
TERMINAL_STATES: frozenset["TaskState"]
CapabilityName = Literal[
    "workspace_write",
    "command",
    "dependency_install",
    "documentation_query",
    "service",
    "network",
    "shell",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TaskState(StrEnum):
    QUEUED = "queued"
    PREPARING = "preparing"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_INPUT = "waiting_input"
    WAITING_SUBTASKS = "waiting_subtasks"
    PAUSED = "paused"
    TESTING = "testing"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BUDGET_LIMITED = "budget_limited"
    EXPIRED = "expired"


BUDGET_ACTIVE_STATES = frozenset(
    {TaskState.PREPARING, TaskState.RUNNING, TaskState.TESTING}
)


TERMINAL_STATES = frozenset(
    {
        TaskState.COMPLETED,
        TaskState.BLOCKED,
        TaskState.FAILED,
        TaskState.CANCELLED,
        TaskState.BUDGET_LIMITED,
        TaskState.EXPIRED,
    }
)


_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.QUEUED: frozenset(
        {TaskState.PREPARING, TaskState.PAUSED, TaskState.CANCELLED, TaskState.EXPIRED}
    ),
    TaskState.PREPARING: frozenset(
        {
            TaskState.RUNNING,
            TaskState.WAITING_APPROVAL,
            TaskState.INTERRUPTED,
            TaskState.BLOCKED,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.RUNNING: frozenset(
        {
            TaskState.WAITING_APPROVAL,
            TaskState.WAITING_INPUT,
            TaskState.WAITING_SUBTASKS,
            TaskState.PAUSED,
            TaskState.TESTING,
            TaskState.INTERRUPTED,
            TaskState.BLOCKED,
            TaskState.FAILED,
            TaskState.CANCELLED,
            TaskState.BUDGET_LIMITED,
        }
    ),
    TaskState.WAITING_APPROVAL: frozenset(
        {
            TaskState.RUNNING,
            TaskState.PAUSED,
            TaskState.INTERRUPTED,
            TaskState.BLOCKED,
            TaskState.CANCELLED,
            TaskState.EXPIRED,
        }
    ),
    TaskState.WAITING_INPUT: frozenset(
        {
            TaskState.QUEUED,
            TaskState.PAUSED,
            TaskState.INTERRUPTED,
            TaskState.CANCELLED,
            TaskState.EXPIRED,
        }
    ),
    TaskState.WAITING_SUBTASKS: frozenset(
        {
            TaskState.QUEUED,
            TaskState.PAUSED,
            TaskState.INTERRUPTED,
            TaskState.BLOCKED,
            TaskState.CANCELLED,
            TaskState.EXPIRED,
        }
    ),
    TaskState.PAUSED: frozenset(
        {TaskState.QUEUED, TaskState.CANCELLED, TaskState.EXPIRED}
    ),
    TaskState.TESTING: frozenset(
        {
            TaskState.RUNNING,
            TaskState.WAITING_APPROVAL,
            TaskState.PAUSED,
            TaskState.INTERRUPTED,
            TaskState.COMPLETED,
            TaskState.BLOCKED,
            TaskState.FAILED,
            TaskState.CANCELLED,
            TaskState.BUDGET_LIMITED,
        }
    ),
    TaskState.INTERRUPTED: frozenset(
        {TaskState.QUEUED, TaskState.PAUSED, TaskState.CANCELLED, TaskState.EXPIRED}
    ),
    TaskState.COMPLETED: frozenset({TaskState.PAUSED, TaskState.EXPIRED}),
    TaskState.BLOCKED: frozenset(
        {TaskState.QUEUED, TaskState.PAUSED, TaskState.CANCELLED, TaskState.EXPIRED}
    ),
    TaskState.FAILED: frozenset(
        {TaskState.QUEUED, TaskState.PAUSED, TaskState.CANCELLED, TaskState.EXPIRED}
    ),
    TaskState.CANCELLED: frozenset({TaskState.EXPIRED}),
    TaskState.BUDGET_LIMITED: frozenset(
        {TaskState.QUEUED, TaskState.PAUSED, TaskState.CANCELLED, TaskState.EXPIRED}
    ),
    TaskState.EXPIRED: frozenset(),
}


def require_transition(current: TaskState, target: TaskState) -> None:
    if current == target:
        return
    if target not in _TRANSITIONS[current]:
        raise ValueError(f"invalid task transition: {current.value} -> {target.value}")


class PolicyProfile(StrEnum):
    INSPECT = "inspect"
    DEVELOP = "develop"
    DEVELOP_NETWORKED = "develop_networked"


class ShellMode(StrEnum):
    INSPECT = "inspect"
    MUTATE = "mutate"


def _normalized_workspace_relative(value: str, *, allow_root: bool = False) -> str:
    normalized = value.replace("\\", "/")
    parts = normalized.split("/")
    if (
        normalized.startswith("/")
        or re.match(r"^[A-Za-z]:", normalized)
        or any(part in {"", ".."} for part in parts)
        or any(part == "." for part in parts[1:])
        or (normalized == "." and not allow_root)
    ):
        raise ValueError("path must be normalized and workspace-relative")
    return normalized


class ShellApprovalScope(StrictModel):
    """Exact, single-operation approval binding for a V15 shell invocation."""

    operation_id: str
    script_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    cwd: str = Field(default=".", min_length=1, max_length=1024)
    mode: ShellMode
    timeout_seconds: int = Field(ge=1, le=3600)
    network_scope_sha256: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$"
    )

    @field_validator("operation_id")
    @classmethod
    def validate_operation_id(cls, value: str) -> str:
        if SAFE_ID.fullmatch(value) is None:
            raise ValueError("operation id must be opaque")
        return value

    @field_validator("cwd")
    @classmethod
    def validate_relative_cwd(cls, value: str) -> str:
        return _normalized_workspace_relative(value, allow_root=True)


class Origin(StrictModel):
    """Server-owned caller identity. It is never accepted from a public request."""

    module: str = Field(min_length=1, max_length=64)
    object_id: str = Field(min_length=1, max_length=128)

    @field_validator("module", "object_id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if SAFE_ID.fullmatch(value) is None:
            raise ValueError("origin identifiers must be opaque safe ids")
        return value


class WorkspaceSource(StrictModel):
    kind: Literal["builtin", "manifest", "host_snapshot"]
    source_id: str = Field(min_length=1, max_length=128)
    revision: str = Field(min_length=1, max_length=128)

    @field_validator("source_id", "revision")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if SAFE_ID.fullmatch(value) is None:
            raise ValueError("workspace source values must be opaque safe ids")
        return value


class RepositoryInstruction(StrictModel):
    display_path: str = Field(min_length=1, max_length=1024)
    scope: str = Field(min_length=1, max_length=1024)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    content: str = Field(max_length=16_384)

    @field_validator("display_path")
    @classmethod
    def validate_instruction_path(cls, value: str) -> str:
        return _normalized_workspace_relative(value)

    @field_validator("scope")
    @classmethod
    def validate_instruction_scope(cls, value: str) -> str:
        return _normalized_workspace_relative(value, allow_root=True)


class AcceptanceCheck(StrictModel):
    check_id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=200)
    kind: Literal["command", "diff", "artifact", "custom"]
    required: Literal[True] = True

    @field_validator("check_id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if SAFE_ID.fullmatch(value) is None:
            raise ValueError("check id must be opaque")
        return value


class AcceptanceArtifact(StrictModel):
    artifact_id: str = Field(min_length=1, max_length=128)
    media_type: str = Field(min_length=1, max_length=120)

    @field_validator("artifact_id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if SAFE_ID.fullmatch(value) is None:
            raise ValueError("artifact id must be opaque")
        return value


class AcceptanceContract(StrictModel):
    contract_id: str = Field(min_length=1, max_length=128)
    required_checks: tuple[AcceptanceCheck, ...] = Field(min_length=1, max_length=64)
    required_artifacts: tuple[AcceptanceArtifact, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def unique_requirements(self) -> "AcceptanceContract":
        check_ids = [item.check_id for item in self.required_checks]
        artifact_ids = [item.artifact_id for item in self.required_artifacts]
        if len(check_ids) != len(set(check_ids)) or len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("acceptance requirement ids must be unique")
        if SAFE_ID.fullmatch(self.contract_id) is None:
            raise ValueError("contract id must be opaque")
        return self


class TaskBudget(StrictModel):
    max_seconds: int = Field(default=3600, ge=30, le=86_400)
    max_turns: int = Field(default=64, ge=1, le=256)
    max_tool_calls: int = Field(default=256, ge=1, le=2048)
    max_output_bytes: int = Field(default=8 * 1024 * 1024, ge=1024, le=64 * 1024 * 1024)


class WorkerBudgetUsage(StrictModel):
    active_seconds: float = Field(ge=0)
    turns_started: int = Field(ge=0)
    tool_calls: int = Field(ge=0)


class ContextReference(StrictModel):
    ref_id: str = Field(min_length=1, max_length=128)
    kind: Literal["artifact", "resource", "file", "image"]

    @field_validator("ref_id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if SAFE_ID.fullmatch(value) is None:
            raise ValueError("context reference must be opaque")
        return value


class TaskCreateRequest(StrictModel):
    client_task_id: str = Field(min_length=1, max_length=128)
    objective: str = Field(min_length=1, max_length=1_048_576)
    workspace_source: WorkspaceSource
    acceptance: AcceptanceContract
    policy_profile: PolicyProfile = PolicyProfile.INSPECT
    model_route: str = Field(min_length=1, max_length=128)
    budget: TaskBudget = Field(default_factory=TaskBudget)
    context_refs: tuple[ContextReference, ...] = Field(default=(), max_length=128)

    @field_validator("client_task_id")
    @classmethod
    def validate_task_id(cls, value: str) -> str:
        if SAFE_ID.fullmatch(value) is None:
            raise ValueError("client task id must be opaque")
        return value

    @field_validator("objective")
    @classmethod
    def reject_blank_objective(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("objective cannot be blank")
        return value

    @field_validator("model_route")
    @classmethod
    def validate_model_route(cls, value: str) -> str:
        if SAFE_ROUTE.fullmatch(value) is None or value.startswith(("http:", "https:")):
            raise ValueError("model route must be a controlled catalog id")
        return value

    @model_validator(mode="after")
    def unique_context_refs(self) -> "TaskCreateRequest":
        ids = [item.ref_id for item in self.context_refs]
        if len(ids) != len(set(ids)):
            raise ValueError("context refs must be unique")
        return self


class TaskSpec(TaskCreateRequest):
    origin: Origin


class WorkerCapabilities(StrictModel):
    api_version: Literal["v1"] = "v1"
    task_runtime: bool
    professional_file_tools: bool
    shell: bool
    operation_output: bool
    changesets: bool
    code_intelligence: bool
    structured_plan: bool = False
    user_questions: bool = False
    context_compaction: bool = False
    turn_history: bool = False
    subtasks: bool = False


class SubtaskKind(StrEnum):
    EXPLORE = "explore"
    IMPLEMENT = "implement"
    REVIEW = "review"


class SubtaskMergeState(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    PENDING = "pending"
    READY = "ready"
    MERGED = "merged"
    CONFLICTED = "conflicted"
    FAILED = "failed"


class OperationOutputStream(StrEnum):
    STDOUT = "stdout"
    STDERR = "stderr"
    SYSTEM = "system"


class OperationOutputChunk(StrictModel):
    task_id: str
    operation_id: str
    sequence: int = Field(ge=1)
    stream: OperationOutputStream
    text: str = Field(max_length=65_536)
    created_at: float
    truncated: bool = False

    @field_validator("task_id", "operation_id")
    @classmethod
    def validate_output_id(cls, value: str) -> str:
        if SAFE_ID.fullmatch(value) is None:
            raise ValueError("operation output identifier is invalid")
        return value


class ChangesetState(StrEnum):
    PREPARED = "prepared"
    APPLIED = "applied"
    CONFLICT = "conflict"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class ChangeKind(StrEnum):
    ADD = "add"
    MODIFY = "modify"
    DELETE = "delete"
    MOVE = "move"


class ChangesetEntry(StrictModel):
    entry_id: str
    kind: ChangeKind
    display_path: str = Field(min_length=1, max_length=1024)
    destination_display_path: str | None = Field(default=None, max_length=1024)
    preimage_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    postimage_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    binary: bool = False

    @field_validator("entry_id")
    @classmethod
    def validate_entry_id(cls, value: str) -> str:
        if SAFE_ID.fullmatch(value) is None:
            raise ValueError("changeset entry id must be opaque")
        return value

    @field_validator("display_path", "destination_display_path")
    @classmethod
    def validate_display_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalized_workspace_relative(value)

    @model_validator(mode="after")
    def validate_hashes(self) -> "ChangesetEntry":
        if self.kind is ChangeKind.ADD and self.preimage_sha256 is not None:
            raise ValueError("added entries cannot have a preimage")
        if self.kind is ChangeKind.DELETE and self.postimage_sha256 is not None:
            raise ValueError("deleted entries cannot have a postimage")
        if self.kind is ChangeKind.MOVE and not self.destination_display_path:
            raise ValueError("moved entries require a destination")
        if (
            self.kind is not ChangeKind.MOVE
            and self.destination_display_path is not None
        ):
            raise ValueError("only moved entries have a destination")
        return self


class WorkerChangeset(StrictModel):
    changeset_id: str
    task_id: str
    operation_id: str
    base_tree_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    result_tree_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    state: ChangesetState
    entries: tuple[ChangesetEntry, ...] = Field(max_length=4096)
    artifact_id: str | None = None
    created_at: float
    updated_at: float

    @field_validator("changeset_id", "task_id", "operation_id", "artifact_id")
    @classmethod
    def validate_changeset_id(cls, value: str | None) -> str | None:
        if value is not None and SAFE_ID.fullmatch(value) is None:
            raise ValueError("changeset identifier is invalid")
        return value


class CodePosition(StrictModel):
    line: int = Field(ge=0)
    character: int = Field(ge=0)


class CodeRange(StrictModel):
    start: CodePosition
    end: CodePosition

    @model_validator(mode="after")
    def validate_order(self) -> "CodeRange":
        if (self.end.line, self.end.character) < (
            self.start.line,
            self.start.character,
        ):
            raise ValueError("code range end precedes start")
        return self


class CodeLocation(StrictModel):
    entry_id: str
    range: CodeRange

    @field_validator("entry_id")
    @classmethod
    def validate_location_entry(cls, value: str) -> str:
        if SAFE_ID.fullmatch(value) is None:
            raise ValueError("code location entry id must be opaque")
        return value


class DiagnosticSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFORMATION = "information"
    HINT = "hint"


class WorkerDiagnostic(StrictModel):
    diagnostic_id: str
    task_id: str
    entry_id: str
    workspace_tree_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    range: CodeRange
    severity: DiagnosticSeverity
    code: str | None = Field(default=None, max_length=128)
    message: str = Field(min_length=1, max_length=16_384)
    created_at: float

    @field_validator("diagnostic_id", "task_id", "entry_id")
    @classmethod
    def validate_diagnostic_id(cls, value: str) -> str:
        if SAFE_ID.fullmatch(value) is None:
            raise ValueError("diagnostic identifier is invalid")
        return value


class CodeIntelligenceSnapshot(StrictModel):
    task_id: str
    operation_id: str
    entry_id: str
    operation: Literal["symbols", "definition", "references", "hover", "diagnostics"]
    language: Literal[
        "python", "typescript", "typescriptreact", "javascript", "javascriptreact"
    ]
    workspace_tree_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    current_tree_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    stale: bool
    result: dict[str, Any]

    @field_validator("task_id", "operation_id", "entry_id")
    @classmethod
    def validate_snapshot_id(cls, value: str) -> str:
        if SAFE_ID.fullmatch(value) is None:
            raise ValueError("code intelligence identifier is invalid")
        return value

    @model_validator(mode="after")
    def validate_snapshot_binding(self) -> "CodeIntelligenceSnapshot":
        expected_key = {
            "symbols": "symbols",
            "definition": "locations",
            "references": "locations",
            "hover": "hover",
            "diagnostics": "diagnostics",
        }[self.operation]
        if set(self.result) != {expected_key}:
            raise ValueError("code intelligence result kind is invalid")
        if self.stale != (self.workspace_tree_hash != self.current_tree_hash):
            raise ValueError("code intelligence stale state is invalid")
        return self


class CodeDiagnosticsSnapshot(StrictModel):
    task_id: str
    operation_id: str
    entry_id: str
    language: Literal[
        "python", "typescript", "typescriptreact", "javascript", "javascriptreact"
    ]
    workspace_tree_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    current_tree_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    stale: bool
    diagnostics: tuple[WorkerDiagnostic, ...] = Field(max_length=2000)

    @field_validator("task_id", "operation_id", "entry_id")
    @classmethod
    def validate_diagnostics_snapshot_id(cls, value: str) -> str:
        if SAFE_ID.fullmatch(value) is None:
            raise ValueError("diagnostics snapshot identifier is invalid")
        return value

    @model_validator(mode="after")
    def validate_diagnostics_binding(self) -> "CodeDiagnosticsSnapshot":
        if self.stale != (self.workspace_tree_hash != self.current_tree_hash):
            raise ValueError("diagnostics stale state is invalid")
        if any(
            item.task_id != self.task_id
            or item.entry_id != self.entry_id
            or item.workspace_tree_hash != self.workspace_tree_hash
            for item in self.diagnostics
        ):
            raise ValueError("diagnostics snapshot binding is invalid")
        return self


class CapabilityLease(StrictModel):
    lease_id: str
    task_id: str
    capability: CapabilityName
    scope: dict[str, Any] = Field(default_factory=dict)
    issued_at: float
    expires_at: float
    operation_limit: int = Field(default=1, ge=1, le=1024)

    @model_validator(mode="after")
    def validate_lease(self) -> "CapabilityLease":
        if SAFE_ID.fullmatch(self.lease_id) is None or SAFE_ID.fullmatch(self.task_id) is None:
            raise ValueError("lease identifiers are invalid")
        if not all(math.isfinite(value) and value >= 0 for value in (self.issued_at, self.expires_at)):
            raise ValueError("lease timestamps are invalid")
        if self.expires_at <= self.issued_at:
            raise ValueError("lease expiry must be after issuance")
        if self.capability == "shell":
            ShellApprovalScope.model_validate(self.scope)
            if self.operation_limit != 1:
                raise ValueError("shell approval is always single-operation")
        return self


class TaskRecord(StrictModel):
    task_id: str
    spec: TaskSpec
    state: TaskState
    workspace_id: str | None = None
    provider_session_id: str | None = Field(default=None, exclude=True, repr=False)
    created_at: float
    updated_at: float
    expires_at: float
    pinned: bool = False
    last_event_sequence: int = 0
    reason: str | None = None

    @field_validator("task_id", "workspace_id", "provider_session_id")
    @classmethod
    def validate_optional_id(cls, value: str | None) -> str | None:
        if value is not None and SAFE_ID.fullmatch(value) is None:
            raise ValueError("record identifier is invalid")
        return value


class SubtaskRequest(StrictModel):
    client_subtask_id: str
    kind: SubtaskKind
    objective: str = Field(min_length=1, max_length=65_536)

    @field_validator("client_subtask_id")
    @classmethod
    def validate_client_subtask_id(cls, value: str) -> str:
        if SAFE_ID.fullmatch(value) is None:
            raise ValueError("subtask id is invalid")
        return value

    @field_validator("objective")
    @classmethod
    def reject_blank_subtask_objective(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("subtask objective cannot be blank")
        return value


class SubtaskRecord(StrictModel):
    parent_task_id: str
    child_task_id: str
    client_subtask_id: str
    kind: SubtaskKind
    objective: str
    base_tree_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    merge_state: SubtaskMergeState
    result_tree_hash: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$"
    )
    changed_paths: tuple[str, ...] = Field(default=(), max_length=4096)
    summary: str | None = Field(default=None, max_length=65_536)
    created_at: float
    updated_at: float

    @field_validator("parent_task_id", "child_task_id", "client_subtask_id")
    @classmethod
    def validate_subtask_id(cls, value: str) -> str:
        if SAFE_ID.fullmatch(value) is None:
            raise ValueError("subtask identifier is invalid")
        return value

    @field_validator("changed_paths")
    @classmethod
    def validate_changed_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(_normalized_workspace_relative(item) for item in value)
        if normalized != value or len(value) != len(set(value)):
            raise ValueError("subtask changed paths are invalid")
        return value


class WorkerEvent(StrictModel):
    sequence: int = Field(ge=1)
    task_id: str
    type: str = Field(min_length=1, max_length=80)
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: float


class WorkerMessage(StrictModel):
    message_id: str
    task_id: str
    sequence: int = Field(ge=1)
    role: Literal["user", "assistant", "tool", "system"]
    content: str
    created_at: float


class WorkerPlanItem(StrictModel):
    step: str = Field(min_length=1, max_length=4096)
    status: Literal["pending", "in_progress", "completed"]


class WorkerPlan(StrictModel):
    task_id: str
    sequence: int = Field(ge=1)
    turn_id: str
    explanation: str | None = Field(default=None, max_length=16_384)
    items: tuple[WorkerPlanItem, ...] = Field(min_length=1, max_length=128)
    updated_at: float

    @field_validator("task_id", "turn_id")
    @classmethod
    def validate_plan_id(cls, value: str) -> str:
        if SAFE_ID.fullmatch(value) is None:
            raise ValueError("plan identifier is invalid")
        return value


class QuestionStatus(StrEnum):
    PENDING = "pending"
    RESOLVED = "resolved"


class WorkerQuestionOption(StrictModel):
    option_id: str
    label: str = Field(min_length=1, max_length=200)

    @field_validator("option_id")
    @classmethod
    def validate_option_id(cls, value: str) -> str:
        if SAFE_ID.fullmatch(value) is None:
            raise ValueError("question option id is invalid")
        return value


class WorkerQuestionAnswer(StrictModel):
    answer: str | None = Field(default=None, min_length=1, max_length=16_384)
    option_id: str | None = None

    @field_validator("option_id")
    @classmethod
    def validate_answer_option_id(cls, value: str | None) -> str | None:
        if value is not None and SAFE_ID.fullmatch(value) is None:
            raise ValueError("question option id is invalid")
        return value

    @model_validator(mode="after")
    def validate_single_answer(self) -> "WorkerQuestionAnswer":
        if (self.answer is None) == (self.option_id is None):
            raise ValueError("exactly one question answer is required")
        return self


class WorkerQuestion(StrictModel):
    task_id: str
    question_id: str
    turn_id: str
    status: QuestionStatus
    prompt: str = Field(min_length=1, max_length=16_384)
    options: tuple[WorkerQuestionOption, ...] = Field(max_length=16)
    answer: str | None = Field(default=None, max_length=16_384)
    selected_option_id: str | None = None
    created_at: float
    resolved_at: float | None = None

    @field_validator("task_id", "question_id", "turn_id", "selected_option_id")
    @classmethod
    def validate_question_id(cls, value: str | None) -> str | None:
        if value is not None and SAFE_ID.fullmatch(value) is None:
            raise ValueError("question identifier is invalid")
        return value

    @model_validator(mode="after")
    def validate_resolution(self) -> "WorkerQuestion":
        resolved = self.status is QuestionStatus.RESOLVED
        if resolved != (self.resolved_at is not None):
            raise ValueError("question resolution timestamp is invalid")
        if resolved != (self.answer is not None or self.selected_option_id is not None):
            raise ValueError("question resolution payload is invalid")
        return self


class SessionLedgerKind(StrEnum):
    PUBLIC_MESSAGE = "public_message"
    PLAN = "plan"
    TODO = "todo"
    TOOL_STARTED = "tool_started"
    TOOL_FINISHED = "tool_finished"
    CHECK_EVIDENCE = "check_evidence"
    TURN_STARTED = "turn_started"
    TURN_FINISHED = "turn_finished"
    QUESTION = "question"
    COMPACTION = "compaction"


_LEDGER_PAYLOAD_KEYS: dict[SessionLedgerKind, frozenset[str]] = {
    SessionLedgerKind.PUBLIC_MESSAGE: frozenset({"role", "text"}),
    SessionLedgerKind.PLAN: frozenset({"explanation", "items"}),
    SessionLedgerKind.TODO: frozenset({"items"}),
    SessionLedgerKind.TOOL_STARTED: frozenset({"tool_name", "summary"}),
    SessionLedgerKind.TOOL_FINISHED: frozenset(
        {"tool_name", "summary", "result_state", "artifact_id"}
    ),
    SessionLedgerKind.CHECK_EVIDENCE: frozenset(
        {
            "check_id",
            "evidence_id",
            "status",
            "exit_code",
            "artifact_id",
            "workspace_tree_hash",
        }
    ),
    SessionLedgerKind.TURN_STARTED: frozenset(),
    SessionLedgerKind.TURN_FINISHED: frozenset({"result_state"}),
    SessionLedgerKind.QUESTION: frozenset({"question_id", "prompt", "options"}),
    SessionLedgerKind.COMPACTION: frozenset({"summary", "boundary_sequence"}),
}
_LEDGER_FORBIDDEN_KEYS = frozenset(
    {
        "chain_of_thought",
        "hidden_reasoning",
        "reasoning",
        "thinking",
        "thought",
        "raw_frame",
        "provider_frame",
        "provider_session_id",
        "endpoint",
        "port",
    }
)


class WorkerSessionLedgerEntry(StrictModel):
    ledger_id: str
    task_id: str
    sequence: int = Field(ge=1)
    kind: SessionLedgerKind
    turn_id: str | None = None
    operation_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: float

    @field_validator("ledger_id", "task_id", "turn_id", "operation_id")
    @classmethod
    def validate_ledger_id(cls, value: str | None) -> str | None:
        if value is not None and SAFE_ID.fullmatch(value) is None:
            raise ValueError("session ledger identifier is invalid")
        return value

    @model_validator(mode="after")
    def validate_normalized_payload(self) -> "WorkerSessionLedgerEntry":
        if set(self.payload) != _LEDGER_PAYLOAD_KEYS[self.kind]:
            raise ValueError("session ledger payload is not canonical")
        self._reject_hidden_fields(self.payload)
        if self.kind in {
            SessionLedgerKind.TURN_STARTED,
            SessionLedgerKind.TURN_FINISHED,
            SessionLedgerKind.TOOL_STARTED,
            SessionLedgerKind.TOOL_FINISHED,
        } and self.turn_id is None:
            raise ValueError("session ledger turn binding is required")
        if self.kind in {
            SessionLedgerKind.TOOL_STARTED,
            SessionLedgerKind.TOOL_FINISHED,
        } and self.operation_id is None:
            raise ValueError("session ledger operation binding is required")
        if self.kind not in {
            SessionLedgerKind.TOOL_STARTED,
            SessionLedgerKind.TOOL_FINISHED,
        } and self.operation_id is not None:
            raise ValueError("session ledger operation binding is unexpected")
        if self.kind is SessionLedgerKind.PUBLIC_MESSAGE:
            if self.payload.get("role") not in {"user", "assistant", "tool", "system"}:
                raise ValueError("session ledger message role is invalid")
            self._require_text(self.payload.get("text"), 1_048_576)
        elif self.kind in {SessionLedgerKind.TOOL_STARTED, SessionLedgerKind.TOOL_FINISHED}:
            tool_name = self.payload.get("tool_name")
            if not isinstance(tool_name, str) or re.fullmatch(
                r"[a-z][a-z0-9_]{0,63}", tool_name
            ) is None:
                raise ValueError("session ledger tool name is invalid")
            self._require_text(self.payload.get("summary"), 4096)
            if self.kind is SessionLedgerKind.TOOL_FINISHED:
                if self.payload.get("result_state") not in {"succeeded", "failed", "unknown"}:
                    raise ValueError("session ledger tool result is invalid")
                artifact_id = self.payload.get("artifact_id")
                if artifact_id is not None and (
                    not isinstance(artifact_id, str) or SAFE_ID.fullmatch(artifact_id) is None
                ):
                    raise ValueError("session ledger artifact id is invalid")
        elif self.kind is SessionLedgerKind.TURN_FINISHED:
            if self.payload.get("result_state") not in {
                "completed",
                "cancelled",
                "failed",
                "interrupted",
                "waiting_input",
            }:
                raise ValueError("session ledger turn result is invalid")
        elif self.kind is SessionLedgerKind.PLAN:
            explanation = self.payload.get("explanation")
            if explanation is not None and (
                not isinstance(explanation, str) or len(explanation) > 16_384
            ):
                raise ValueError("session ledger plan explanation is invalid")
            items = self.payload.get("items")
            if not isinstance(items, list) or not 1 <= len(items) <= 128:
                raise ValueError("session ledger plan is invalid")
            for item in items:
                if not isinstance(item, dict) or set(item) != {"step", "status"}:
                    raise ValueError("session ledger plan item is invalid")
                self._require_text(item.get("step"), 4096)
                if item.get("status") not in {"pending", "in_progress", "completed"}:
                    raise ValueError("session ledger plan status is invalid")
        elif self.kind is SessionLedgerKind.TODO:
            items = self.payload.get("items")
            if not isinstance(items, list) or len(items) > 256:
                raise ValueError("session ledger todo list is invalid")
            for item in items:
                if not isinstance(item, dict) or set(item) != {
                    "todo_id",
                    "content",
                    "status",
                }:
                    raise ValueError("session ledger todo item is invalid")
                todo_id = item.get("todo_id")
                if not isinstance(todo_id, str) or SAFE_ID.fullmatch(todo_id) is None:
                    raise ValueError("session ledger todo id is invalid")
                self._require_text(item.get("content"), 4096)
                if item.get("status") not in {
                    "pending",
                    "in_progress",
                    "completed",
                    "cancelled",
                }:
                    raise ValueError("session ledger todo status is invalid")
        elif self.kind is SessionLedgerKind.QUESTION:
            question_id = self.payload.get("question_id")
            if not isinstance(question_id, str) or SAFE_ID.fullmatch(question_id) is None:
                raise ValueError("session ledger question id is invalid")
            self._require_text(self.payload.get("prompt"), 16_384)
            options = self.payload.get("options")
            if not isinstance(options, list) or len(options) > 16:
                raise ValueError("session ledger question options are invalid")
            for option in options:
                if not isinstance(option, dict) or set(option) != {"option_id", "label"}:
                    raise ValueError("session ledger question option is invalid")
                option_id = option.get("option_id")
                if not isinstance(option_id, str) or SAFE_ID.fullmatch(option_id) is None:
                    raise ValueError("session ledger question option id is invalid")
                self._require_text(option.get("label"), 200)
        elif self.kind is SessionLedgerKind.COMPACTION:
            self._require_text(self.payload.get("summary"), 65_536)
            boundary = self.payload.get("boundary_sequence")
            if not isinstance(boundary, int) or isinstance(boundary, bool) or boundary < 1:
                raise ValueError("session ledger compaction boundary is invalid")
        elif self.kind is SessionLedgerKind.CHECK_EVIDENCE:
            if self.payload.get("status") not in {"passed", "failed", "invalidated"}:
                raise ValueError("session ledger evidence status is invalid")
            if not isinstance(self.payload.get("exit_code"), int):
                raise ValueError("session ledger evidence exit code is invalid")
            tree_hash = self.payload.get("workspace_tree_hash")
            if not isinstance(tree_hash, str) or re.fullmatch(r"[a-f0-9]{64}", tree_hash) is None:
                raise ValueError("session ledger evidence tree hash is invalid")
            for key in ("check_id", "evidence_id", "artifact_id"):
                value = self.payload.get(key)
                if not isinstance(value, str) or SAFE_ID.fullmatch(value) is None:
                    raise ValueError("session ledger evidence binding is invalid")
        return self

    @classmethod
    def _reject_hidden_fields(cls, value: Any) -> None:
        if isinstance(value, dict):
            if any(str(key).lower() in _LEDGER_FORBIDDEN_KEYS for key in value):
                raise ValueError("hidden provider data is not allowed in the session ledger")
            for item in value.values():
                cls._reject_hidden_fields(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                cls._reject_hidden_fields(item)

    @staticmethod
    def _require_text(value: Any, maximum: int) -> None:
        if not isinstance(value, str) or not value or len(value) > maximum:
            raise ValueError("session ledger text is invalid")


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class WorkerApproval(StrictModel):
    approval_id: str
    task_id: str
    operation_id: str
    capability: CapabilityName
    status: ApprovalStatus
    request: dict[str, Any] = Field(default_factory=dict)
    lease: CapabilityLease | None = None
    created_at: float
    decided_at: float | None = None


class WorkerCheckpoint(StrictModel):
    checkpoint_id: str
    task_id: str
    workspace_tree_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: float


class WorkerTurnCheckpoint(StrictModel):
    checkpoint_id: str
    task_id: str
    ordinal: int = Field(ge=1)
    turn_id: str
    before_tree_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    before_tree_oid: str = Field(pattern=r"^[a-f0-9]{40}$")
    after_tree_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    after_tree_oid: str = Field(pattern=r"^[a-f0-9]{40}$")
    ledger_sequence: int = Field(ge=1)
    before_public_context: dict[str, Any] = Field(
        default_factory=dict, exclude=True, repr=False
    )
    after_public_context: dict[str, Any] = Field(
        default_factory=dict, exclude=True, repr=False
    )
    created_at: float

    @field_validator("checkpoint_id", "task_id", "turn_id")
    @classmethod
    def validate_turn_checkpoint_id(cls, value: str) -> str:
        if SAFE_ID.fullmatch(value) is None:
            raise ValueError("turn checkpoint identifier is invalid")
        return value


class WorkerTurnHistory(StrictModel):
    task_id: str
    cursor: int = Field(ge=0)
    checkpoints: tuple[WorkerTurnCheckpoint, ...]
    pending_action: Literal["undo", "redo"] | None = None


class WorkerTaskExport(StrictModel):
    export_version: Literal["v1"] = "v1"
    task: TaskRecord
    public_context: dict[str, Any]
    session_ledger: tuple[WorkerSessionLedgerEntry, ...]
    questions: tuple[WorkerQuestion, ...]
    turn_history: WorkerTurnHistory
    evidence: tuple[WorkerEvidence, ...]
    artifact_index: tuple[dict[str, Any], ...]
    workspace_tree_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    workspace_diff_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    workspace_diff_base64: str = Field(max_length=3 * 1024 * 1024)
    created_at: float


class WorkerArtifact(StrictModel):
    artifact_id: str
    task_id: str
    media_type: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size: int = Field(ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: float


class EvidenceStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    INVALIDATED = "invalidated"


class WorkerEvidence(StrictModel):
    evidence_id: str
    task_id: str
    check_id: str
    operation_id: str
    workspace_tree_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: EvidenceStatus
    exit_code: int
    artifact_id: str
    created_at: float


class OperationState(StrEnum):
    PREPARED = "prepared"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "unknown"


class WorkerOperation(StrictModel):
    operation_id: str
    task_id: str
    tool_name: str
    intent_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    state: OperationState
    request: dict[str, Any]
    result: dict[str, Any] | None = None
    created_at: float
    updated_at: float
