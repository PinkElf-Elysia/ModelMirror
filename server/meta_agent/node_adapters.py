from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel

from .schemas import (
    MetaPlannerIRNode,
    MetaPlannerWorkflowAgentConfig,
)

try:
    from server.workflow_native.schemas import NativeWorkflowNode, WorkflowPosition
except ModuleNotFoundError:
    from workflow_native.schemas import NativeWorkflowNode, WorkflowPosition


META_PLANNER_IR_VERSION = 2
META_PLANNER_ADAPTER_VERSION = "typed-ir-v2"
META_PLANNER_COMPILER_MANAGED_KINDS = frozenset({"input", "output"})
META_PLANNER_BINDING_KINDS = frozenset(
    {
        "external_xpert",
        "knowledge_base",
        "toolset_resource",
        "plugin_resource",
    }
)


@dataclass(frozen=True, slots=True)
class PlannerNodeCompileContext:
    node_id: str
    position: WorkflowPosition
    default_agent_model_id: str
    output_variable: str
    acceptance_criteria: str
    has_runtime_resources: bool
    requires_runtime_mode: bool


@dataclass(frozen=True, slots=True)
class PlannerNodeAdapter:
    kind: str
    config_model: type[BaseModel]
    compile_node: Callable[
        [MetaPlannerIRNode, BaseModel, PlannerNodeCompileContext],
        NativeWorkflowNode,
    ]

    def validate_config(self, node: MetaPlannerIRNode) -> BaseModel:
        return self.config_model.model_validate(node.config)


def _compile_workflow_agent(
    node: MetaPlannerIRNode,
    parsed: BaseModel,
    context: PlannerNodeCompileContext,
) -> NativeWorkflowNode:
    config = MetaPlannerWorkflowAgentConfig.model_validate(parsed)
    return NativeWorkflowNode(
        id=context.node_id,
        type="workflow_agent",
        position=context.position,
        data={
            "kind": "workflow_agent",
            "title": node.title,
            "description": node.description,
            "agentName": node.title,
            "modelId": config.model_id or context.default_agent_model_id,
            "rolePrompt": config.role_prompt,
            "taskInput": config.task_input,
            "toolMode": (
                "mcp_tools"
                if context.has_runtime_resources or context.requires_runtime_mode
                else "none"
            ),
            "toolNames": "",
            "maxIterations": "6",
            "parallelToolCalls": "false",
            "maxToolConcurrency": "2",
            "maxToolCalls": "12",
            "maxToolDepth": "4",
            "outputVariable": context.output_variable,
            "exceptionHandling": "fail",
            **(
                {"sourceAgentId": config.source_agent_id}
                if config.source_agent_id
                else {}
            ),
            **(
                {"acceptanceCriteria": context.acceptance_criteria}
                if context.acceptance_criteria
                else {}
            ),
        },
    )


PLANNER_NODE_ADAPTERS: dict[str, PlannerNodeAdapter] = {
    "workflow_agent": PlannerNodeAdapter(
        kind="workflow_agent",
        config_model=MetaPlannerWorkflowAgentConfig,
        compile_node=_compile_workflow_agent,
    )
}

META_PLANNER_ADAPTER_KINDS = frozenset(PLANNER_NODE_ADAPTERS)
META_PLANNER_COMPILABLE_NODE_KINDS = frozenset(
    META_PLANNER_COMPILER_MANAGED_KINDS
    | META_PLANNER_BINDING_KINDS
    | META_PLANNER_ADAPTER_KINDS
)


def get_planner_node_adapter(kind: str) -> PlannerNodeAdapter | None:
    return PLANNER_NODE_ADAPTERS.get(kind)


def planner_capability_metadata(kind: str) -> dict[str, Any] | None:
    if kind not in META_PLANNER_COMPILABLE_NODE_KINDS:
        return None
    if kind in META_PLANNER_COMPILER_MANAGED_KINDS:
        support = "compiler_managed"
    elif kind in META_PLANNER_BINDING_KINDS:
        support = "binding_only"
    else:
        support = "full"
    return {
        "compilable": True,
        "support": support,
        "ir_version": META_PLANNER_IR_VERSION,
        "adapter_version": META_PLANNER_ADAPTER_VERSION,
    }
