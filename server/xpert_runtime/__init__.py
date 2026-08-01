"""Xpert-aligned runtime foundation for ModelMirror.

The package intentionally stays framework-agnostic. FastAPI routes, workflow
nodes, MCP tools, and future multi-agent runners can depend on these small
runtime primitives without adding more orchestration logic to ``server/main.py``.
"""

from .capabilities import CapabilityRegistry, RuntimeCapability
from .agent_tasks import (
    AUTO_XPERT_TARGET_PREFIX,
    AgentHandoff,
    AgentTask,
    AgentTaskStore,
)
from .handoff_executor import (
    HandoffBusyError,
    HandoffExecutionResult,
    HandoffExecutor,
    HandoffExecutorError,
    HandoffPermanentError,
)
from .goal_coordinator import GoalCoordinator, GoalPlan, PinnedXpert
from .goals import (
    ConversationGoal,
    GoalConflictError,
    GoalNotFoundError,
    GoalStatus,
    GoalStep,
    GoalStepStatus,
    GoalStore,
    GoalStoreError,
    GoalValidationError,
    goal_to_payload,
    validate_goal_plan,
)
from .defaults import create_default_runtime, event_recorder, system_prompt_injector
from .core_middlewares import (
    RuntimeMiddlewareSpec,
    bound_middleware_specs,
    bound_resource_nodes,
    build_context_compression_middleware,
    control_flow_edges,
    estimate_messages_tokens,
    estimate_text_tokens,
    is_non_control_binding_edge,
    middleware_config_int,
    middleware_config_schema,
    middleware_spec,
    middleware_spec_from_node,
    select_runtime_tools,
    todo_planning_instruction,
    validate_structured_output,
)
from .external_xpert_toolset import (
    ExternalXpertToolsetProvider,
    register_external_xpert_toolset_capability,
)
from .events import RuntimeEventStore
from .middleware import AgentMiddleware, MiddlewarePipeline
from .interrupts import RuntimeInterrupt, RuntimeMiddlewareFatalError
from .approval_store import (
    RuntimeApprovalConflictError,
    RuntimeApprovalNotFoundError,
    RuntimeApprovalRequest,
    RuntimeApprovalStore,
    RuntimeApprovalValidationError,
)
from .execution_store import (
    WorkflowExecution,
    WorkflowExecutionConflictError,
    WorkflowExecutionNotFoundError,
    WorkflowExecutionStore,
)
from .approval_coordinator import ApprovalCoordinator
from .approval_api import (
    configure_approval_decision_validator,
    configure_approval_coordinator,
    configure_runtime_approvals,
    router as runtime_approval_router,
)
from .hitl_middleware import (
    build_human_in_the_loop_middleware,
    create_final_output_approval,
    human_in_the_loop_final_confirmation,
)
from .middleware_registry import (
    RuntimeMiddlewareField,
    RuntimeMiddlewareNode,
    RuntimeMiddlewareRegistry,
    register_builtin_middleware_nodes,
    runtime_middleware_registry,
)
from .memory_toolset import MemoryToolsetProvider, register_memory_toolset_capability
from .file_memory_middleware import build_xpert_file_memory_middleware
from .todo_api import configure_runtime_todo_store, router as runtime_todo_router
from .todo_store import (
    RuntimeTodoConflictError,
    RuntimeTodoItem,
    RuntimeTodoNotFoundError,
    RuntimeTodoStore,
    RuntimeTodoValidationError,
)
from .todo_toolset import TodoToolsetProvider, register_todo_toolset_capability
from .knowledge_toolset import (
    KnowledgeToolsetProvider,
    register_knowledge_toolset_capability,
)
from .workflow_node_registry import (
    KnowledgePipelinePalette,
    WorkflowNodeRegistry,
    WorkflowPaletteItem,
    WorkflowPalettePlaceholder,
    WorkflowPaletteSection,
    WorkflowPaletteTab,
    register_builtin_workflow_nodes,
    workflow_node_registry,
)
from .models import (
    MiddlewareContext,
    ModelCallRequest,
    ModelCallResponse,
    RuntimeEvent,
    RuntimeTask,
    ToolCallRequest,
    ToolCallResponse,
)
from .run_registry import (
    RunRegistry,
    RuntimeRun,
    RuntimeRunCheckpoint,
    RuntimeRunStatus,
    RuntimeRunType,
)
from .tool_policy import (
    InMemoryToolAuditStore,
    ToolAuditRecord,
    ToolAuditStatus,
    ToolPermissionPolicy,
)
from .toolset import (
    MCPToolsetProvider,
    RuntimeTool,
    RuntimeToolCall,
    RuntimeToolError,
    RuntimeToolResult,
    ToolsetProvider,
    register_mcp_toolset_capability,
)
from .tool_runner import run_tool_with_runtime
from .sandbox_api import configure_runtime_sandbox, router as runtime_sandbox_router
from .sandbox_client import (
    LocalSandboxClient,
    SandboxClientError,
    SandboxSidecarClient,
)
from .sandbox_store import (
    RuntimeArtifact,
    SandboxNotFoundError,
    SandboxOperation,
    SandboxValidationError,
    SandboxWorkspace,
    SandboxWorkspaceStore,
)
from .sandbox_toolset import (
    SANDBOX_TOOL_NAMES,
    SKILL_TOOL_NAMES,
    SandboxToolsetProvider,
    register_sandbox_toolset_capability,
)
from .browser_api import configure_runtime_browser, router as runtime_browser_router
from .browser_client import BrowserClientError, BrowserSidecarClient
from .browser_store import (
    BrowserArtifact,
    BrowserDomainGrant,
    BrowserNotFoundError,
    BrowserOperation,
    BrowserSession,
    BrowserSessionStore,
    BrowserValidationError,
)
from .browser_toolset import (
    BROWSER_MUTATING_TOOLS,
    BROWSER_TOOL_NAMES,
    BrowserToolsetProvider,
    register_browser_toolset_capability,
)
from .client_tool_store import (
    MUTATING_CLIENT_TOOLS,
    ClientHost,
    ClientHostPairing,
    ClientToolArtifact,
    ClientToolAuthenticationError,
    ClientToolConflictError,
    ClientToolNotFoundError,
    ClientToolRequest,
    ClientToolStore,
)
from .client_toolset import (
    CLIENT_TOOLS,
    CLIENT_TOOL_NAMES,
    ClientToolsetProvider,
    client_tool_schema_hash,
    register_client_toolset_capability,
)
from .office_toolset import (
    OFFICE_DELETE_TOOL_NAMES,
    OFFICE_EXCEL_TOOL_NAMES,
    OFFICE_MUTATING_TOOL_NAMES,
    OFFICE_POWERPOINT_TOOL_NAMES,
    OFFICE_TOOLS,
    OFFICE_TOOL_NAMES,
    OFFICE_TOOL_REQUIREMENTS,
    OFFICE_WORD_TOOL_NAMES,
    OfficeToolsetProvider,
    register_office_toolset_capability,
)
from .client_tool_coordinator import (
    ClientToolConnectionManager,
    ClientToolCoordinator,
)
from .client_tool_api import (
    configure_client_tool_coordinator,
    configure_runtime_client_tools,
    router as runtime_client_tool_router,
)
from .automation_store import (
    AutomationBudget,
    AutomationConflictError,
    AutomationDefinition,
    AutomationError,
    AutomationExecution,
    AutomationNotFoundError,
    AutomationStore,
    AutomationTrigger,
    AutomationValidationError,
    CronSchedule,
)
from .automation_coordinator import (
    AutomationCoordinator,
    AutomationTargetResult,
)
from .automation_toolset import (
    AUTOMATION_TOOL_NAMES,
    AutomationToolsetProvider,
    register_automation_toolset_capability,
)
from .automation_api import (
    configure_runtime_automations,
    router as runtime_automation_router,
)
from .ralph_loop import RalphLoopResult, run_ralph_loop
from .plugin_hooks import build_plugin_hooks_middleware
from .authoring_store import (
    AuthoringProposal,
    AuthoringProposalConflictError,
    AuthoringProposalError,
    AuthoringProposalNotFoundError,
    AuthoringProposalStore,
    AuthoringProposalValidationError,
)
from .authoring_service import AuthoringService
from .authoring_toolset import (
    AuthoringToolsetProvider,
    register_authoring_toolset_capabilities,
)
from .authoring_api import (
    configure_runtime_authoring,
    router as runtime_authoring_router,
)

__all__ = [
    "AgentMiddleware",
    "ApprovalCoordinator",
    "AUTO_XPERT_TARGET_PREFIX",
    "AgentHandoff",
    "AgentTask",
    "AgentTaskStore",
    "CapabilityRegistry",
    "configure_runtime_todo_store",
    "create_default_runtime",
    "event_recorder",
    "HandoffBusyError",
    "HandoffExecutionResult",
    "HandoffExecutor",
    "HandoffExecutorError",
    "HandoffPermanentError",
    "ConversationGoal",
    "GoalConflictError",
    "GoalCoordinator",
    "GoalNotFoundError",
    "GoalPlan",
    "GoalStatus",
    "GoalStep",
    "GoalStepStatus",
    "GoalStore",
    "GoalStoreError",
    "GoalValidationError",
    "PinnedXpert",
    "goal_to_payload",
    "validate_goal_plan",
    "InMemoryToolAuditStore",
    "MCPToolsetProvider",
    "KnowledgeToolsetProvider",
    "MemoryToolsetProvider",
    "build_xpert_file_memory_middleware",
    "TodoToolsetProvider",
    "MiddlewareContext",
    "MiddlewarePipeline",
    "ModelCallRequest",
    "ModelCallResponse",
    "RuntimeCapability",
    "RuntimeMiddlewareSpec",
    "RuntimeEvent",
    "RuntimeEventStore",
    "RuntimeInterrupt",
    "RuntimeMiddlewareFatalError",
    "RuntimeApprovalConflictError",
    "RuntimeApprovalNotFoundError",
    "RuntimeApprovalRequest",
    "RuntimeApprovalStore",
    "RuntimeApprovalValidationError",
    "WorkflowExecution",
    "WorkflowExecutionConflictError",
    "WorkflowExecutionNotFoundError",
    "WorkflowExecutionStore",
    "RuntimeMiddlewareField",
    "RuntimeMiddlewareNode",
    "RuntimeMiddlewareRegistry",
    "WorkflowNodeRegistry",
    "WorkflowPaletteItem",
    "WorkflowPalettePlaceholder",
    "WorkflowPaletteSection",
    "WorkflowPaletteTab",
    "KnowledgePipelinePalette",
    "RuntimeRun",
    "RuntimeRunCheckpoint",
    "RuntimeRunStatus",
    "RuntimeRunType",
    "RuntimeTask",
    "RuntimeTodoConflictError",
    "RuntimeTodoItem",
    "RuntimeTodoNotFoundError",
    "RuntimeTodoStore",
    "RuntimeTodoValidationError",
    "RuntimeTool",
    "RuntimeToolCall",
    "RuntimeToolError",
    "RuntimeToolResult",
    "run_tool_with_runtime",
    "runtime_todo_router",
    "runtime_approval_router",
    "RunRegistry",
    "runtime_middleware_registry",
    "workflow_node_registry",
    "system_prompt_injector",
    "ToolCallRequest",
    "ToolCallResponse",
    "ToolAuditRecord",
    "ToolAuditStatus",
    "ToolsetProvider",
    "ToolPermissionPolicy",
    "register_builtin_middleware_nodes",
    "register_builtin_workflow_nodes",
    "register_mcp_toolset_capability",
    "register_knowledge_toolset_capability",
    "register_memory_toolset_capability",
    "register_todo_toolset_capability",
    "build_context_compression_middleware",
    "bound_middleware_specs",
    "bound_resource_nodes",
    "control_flow_edges",
    "is_non_control_binding_edge",
    "ExternalXpertToolsetProvider",
    "register_external_xpert_toolset_capability",
    "estimate_messages_tokens",
    "estimate_text_tokens",
    "middleware_config_int",
    "middleware_config_schema",
    "middleware_spec",
    "middleware_spec_from_node",
    "select_runtime_tools",
    "todo_planning_instruction",
    "validate_structured_output",
    "build_human_in_the_loop_middleware",
    "create_final_output_approval",
    "human_in_the_loop_final_confirmation",
    "configure_approval_coordinator",
    "configure_approval_decision_validator",
    "configure_runtime_approvals",
    "configure_runtime_sandbox",
    "runtime_sandbox_router",
    "LocalSandboxClient",
    "SandboxClientError",
    "SandboxSidecarClient",
    "RuntimeArtifact",
    "SandboxNotFoundError",
    "SandboxOperation",
    "SandboxValidationError",
    "SandboxWorkspace",
    "SandboxWorkspaceStore",
    "SANDBOX_TOOL_NAMES",
    "SKILL_TOOL_NAMES",
    "SandboxToolsetProvider",
    "register_sandbox_toolset_capability",
    "configure_runtime_browser",
    "runtime_browser_router",
    "BrowserClientError",
    "BrowserSidecarClient",
    "BrowserArtifact",
    "BrowserDomainGrant",
    "BrowserNotFoundError",
    "BrowserOperation",
    "BrowserSession",
    "BrowserSessionStore",
    "BrowserValidationError",
    "BROWSER_MUTATING_TOOLS",
    "BROWSER_TOOL_NAMES",
    "BrowserToolsetProvider",
    "register_browser_toolset_capability",
    "MUTATING_CLIENT_TOOLS",
    "ClientHost",
    "ClientHostPairing",
    "ClientToolArtifact",
    "ClientToolAuthenticationError",
    "ClientToolConflictError",
    "ClientToolNotFoundError",
    "ClientToolRequest",
    "ClientToolStore",
    "CLIENT_TOOLS",
    "CLIENT_TOOL_NAMES",
    "ClientToolsetProvider",
    "client_tool_schema_hash",
    "register_client_toolset_capability",
    "OFFICE_DELETE_TOOL_NAMES",
    "OFFICE_EXCEL_TOOL_NAMES",
    "OFFICE_MUTATING_TOOL_NAMES",
    "OFFICE_POWERPOINT_TOOL_NAMES",
    "OFFICE_TOOLS",
    "OFFICE_TOOL_NAMES",
    "OFFICE_TOOL_REQUIREMENTS",
    "OFFICE_WORD_TOOL_NAMES",
    "OfficeToolsetProvider",
    "register_office_toolset_capability",
    "ClientToolConnectionManager",
    "ClientToolCoordinator",
    "configure_client_tool_coordinator",
    "configure_runtime_client_tools",
    "runtime_client_tool_router",
    "AutomationBudget",
    "AutomationConflictError",
    "AutomationCoordinator",
    "AutomationDefinition",
    "AutomationError",
    "AutomationExecution",
    "AutomationNotFoundError",
    "AutomationStore",
    "AutomationTargetResult",
    "AutomationToolsetProvider",
    "AutomationTrigger",
    "AutomationValidationError",
    "AUTOMATION_TOOL_NAMES",
    "CronSchedule",
    "configure_runtime_automations",
    "register_automation_toolset_capability",
    "runtime_automation_router",
    "RalphLoopResult",
    "run_ralph_loop",
    "build_plugin_hooks_middleware",
    "AuthoringProposal",
    "AuthoringProposalConflictError",
    "AuthoringProposalError",
    "AuthoringProposalNotFoundError",
    "AuthoringProposalStore",
    "AuthoringProposalValidationError",
    "AuthoringService",
    "AuthoringToolsetProvider",
    "register_authoring_toolset_capabilities",
    "configure_runtime_authoring",
    "runtime_authoring_router",
]
