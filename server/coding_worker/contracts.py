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
        {TaskState.QUEUED, TaskState.CANCELLED, TaskState.EXPIRED}
    ),
    TaskState.COMPLETED: frozenset({TaskState.EXPIRED}),
    TaskState.BLOCKED: frozenset({TaskState.QUEUED, TaskState.CANCELLED, TaskState.EXPIRED}),
    TaskState.FAILED: frozenset({TaskState.QUEUED, TaskState.CANCELLED, TaskState.EXPIRED}),
    TaskState.CANCELLED: frozenset({TaskState.EXPIRED}),
    TaskState.BUDGET_LIMITED: frozenset({TaskState.QUEUED, TaskState.CANCELLED, TaskState.EXPIRED}),
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
