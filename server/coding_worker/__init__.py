"""Provider-neutral task kernel for the ModelMirror Coding Worker."""

from .contracts import (
    AcceptanceContract,
    CapabilityLease,
    ContextReference,
    Origin,
    PolicyProfile,
    TaskBudget,
    TaskCreateRequest,
    TaskRecord,
    TaskSpec,
    TaskState,
    WorkspaceSource,
)
from .provider import CodingAgentProvider, FakeCodingAgentProvider
from .opencode_provider import OpenCodeProvider
from .service import CodingWorkerService
from .store import CodingWorkerStore
from .workspace import WorkspaceBroker

__all__ = [
    "AcceptanceContract",
    "CapabilityLease",
    "CodingAgentProvider",
    "CodingWorkerService",
    "CodingWorkerStore",
    "ContextReference",
    "FakeCodingAgentProvider",
    "OpenCodeProvider",
    "Origin",
    "PolicyProfile",
    "TaskBudget",
    "TaskCreateRequest",
    "TaskRecord",
    "TaskSpec",
    "TaskState",
    "WorkspaceSource",
    "WorkspaceBroker",
]
