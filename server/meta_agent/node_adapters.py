from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel

from .schemas import (
    MetaPlannerIRNode,
    MetaPlannerWorkflowAgentConfig,
)

try:
    from server.workflow_native.node_contracts import (
        NODE_CONTRACT_VERSION,
        canonical_checksum,
        workflow_node_contract_registry,
    )
    from server.workflow_native.schemas import NativeWorkflowNode, WorkflowPosition
except ModuleNotFoundError:
    from workflow_native.node_contracts import (
        NODE_CONTRACT_VERSION,
        canonical_checksum,
        workflow_node_contract_registry,
    )
    from workflow_native.schemas import NativeWorkflowNode, WorkflowPosition


META_PLANNER_IR_VERSION = 2
META_PLANNER_ADAPTER_VERSION = "node-contract-v3"
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
    decompile_node: Callable[[NativeWorkflowNode], MetaPlannerIRNode]
    contract_version: int = NODE_CONTRACT_VERSION

    def validate_config(self, node: MetaPlannerIRNode) -> BaseModel:
        return self.config_model.model_validate(node.config)

    @property
    def config_schema_checksum(self) -> str:
        return canonical_checksum(self.config_model.model_json_schema())


def _compile_workflow_agent(
    node: MetaPlannerIRNode,
    parsed: BaseModel,
    context: PlannerNodeCompileContext,
) -> NativeWorkflowNode:
    config = MetaPlannerWorkflowAgentConfig.model_validate(parsed)
    contract = workflow_node_contract_registry.require("workflow_agent")
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
            "plannerContractVersion": NODE_CONTRACT_VERSION,
            "plannerCompilerChecksum": contract.compiler_checksum,
            "plannerRef": node.ref,
            "plannerTaskIds": list(node.task_ids),
            "plannerInputs": [item.model_dump(mode="json") for item in node.inputs],
            "plannerOutputs": [item.model_dump(mode="json") for item in node.outputs],
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
            **(
                {"methodSkillIds": config.method_skill_ids}
                if config.method_skill_ids
                else {}
            ),
        },
    )


def _decompile_workflow_agent(node: NativeWorkflowNode) -> MetaPlannerIRNode:
    data = node.data if isinstance(node.data, dict) else {}
    contract = workflow_node_contract_registry.require("workflow_agent")
    if int(data.get("plannerContractVersion") or 0) != NODE_CONTRACT_VERSION:
        raise ValueError("Workflow Agent does not carry a NodeContract V3 marker.")
    if str(data.get("plannerCompilerChecksum") or "") != contract.compiler_checksum:
        raise ValueError("Workflow Agent compiler contract has drifted.")
    node_ref = str(data.get("plannerRef") or "").strip()
    task_ids = data.get("plannerTaskIds")
    inputs = data.get("plannerInputs")
    outputs = data.get("plannerOutputs")
    if not node_ref or not isinstance(task_ids, list) or not task_ids:
        raise ValueError("Workflow Agent is missing planner round-trip metadata.")
    return MetaPlannerIRNode.model_validate(
        {
            "ref": node_ref,
            "kind": "workflow_agent",
            "title": str(data.get("title") or data.get("agentName") or node_ref),
            "description": str(data.get("description") or ""),
            "task_ids": task_ids,
            "inputs": inputs if isinstance(inputs, list) else [],
            "outputs": outputs if isinstance(outputs, list) else [],
            "config": {
                "role_prompt": str(data.get("rolePrompt") or ""),
                "task_input": str(data.get("taskInput") or ""),
                "model_id": str(data.get("modelId") or "") or None,
                "source_agent_id": str(data.get("sourceAgentId") or "") or None,
                "method_skill_ids": (
                    data.get("methodSkillIds")
                    if isinstance(data.get("methodSkillIds"), list)
                    else []
                ),
            },
        }
    )


PLANNER_NODE_ADAPTERS: dict[str, PlannerNodeAdapter] = {
    "workflow_agent": PlannerNodeAdapter(
        kind="workflow_agent",
        config_model=MetaPlannerWorkflowAgentConfig,
        compile_node=_compile_workflow_agent,
        decompile_node=_decompile_workflow_agent,
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


def decompile_planner_node(node: NativeWorkflowNode) -> MetaPlannerIRNode:
    kind = str((node.data or {}).get("kind") or node.type or "")
    adapter = get_planner_node_adapter(kind)
    if adapter is None:
        raise ValueError(f"Node kind {kind} has no compiler adapter.")
    return adapter.decompile_node(node)


def planner_capability_metadata(kind: str) -> dict[str, Any] | None:
    contract = workflow_node_contract_registry.get(kind)
    if (
        kind not in META_PLANNER_COMPILABLE_NODE_KINDS
        or contract is None
        or contract.contract_status != "complete"
        or not contract.planner.enabled
    ):
        return None
    if kind in META_PLANNER_COMPILER_MANAGED_KINDS:
        support = "compiler_managed"
    elif kind in META_PLANNER_BINDING_KINDS:
        support = "binding_only"
    else:
        adapter = get_planner_node_adapter(kind)
        if adapter is None or adapter.contract_version != NODE_CONTRACT_VERSION:
            return None
        contract_schema_checksum = canonical_checksum(
            contract.planner.ir_config_schema
        )
        if adapter.config_schema_checksum != contract_schema_checksum:
            return None
        support = "full"
    return {
        "compilable": True,
        "support": support,
        "ir_version": META_PLANNER_IR_VERSION,
        "adapter_version": META_PLANNER_ADAPTER_VERSION,
        "contract_version": NODE_CONTRACT_VERSION,
        "contract_checksum": contract.checksum,
        "compiler_checksum": contract.compiler_checksum,
    }
