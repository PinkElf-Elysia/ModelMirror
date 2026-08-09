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
    "workspace_write", "command", "dependency_install", "service", "network"
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
