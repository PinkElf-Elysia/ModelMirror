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
from .evidence import HarnessRunner
from .network_policy import EgressPolicy, NetworkPolicyError
from .process_manager import BackgroundProcessManager, ManagedProcess, ProcessManagerError
from .opencode_provider import OpenCodeProvider
from .service import CodingWorkerService
from .store import CodingWorkerStore
from .tool_broker import FrozenCheck, ToolBroker, ToolBrokerError, ToolResult
from .workspace import WorkspaceBroker

__all__ = [
    "AcceptanceContract",
    "CapabilityLease",
    "CodingAgentProvider",
    "CodingWorkerService",
    "CodingWorkerStore",
    "FrozenCheck",
    "HarnessRunner",
    "EgressPolicy",
    "NetworkPolicyError",
    "BackgroundProcessManager",
    "ManagedProcess",
    "ProcessManagerError",
    "ToolBroker",
    "ToolBrokerError",
    "ToolResult",
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
