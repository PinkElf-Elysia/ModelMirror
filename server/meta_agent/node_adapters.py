from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel

from .schemas import (
    GraphIntentNodeV3,
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


META_PLANNER_IR_VERSION = 3
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
    decompile_node_v3: Callable[[NativeWorkflowNode], GraphIntentNodeV3]
    contract_version: int = NODE_CONTRACT_VERSION

    def validate_config(self, node: MetaPlannerIRNode) -> BaseModel:
        return self.config_model.model_validate(node.config)

    def validate_authoring_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """Normalize editor/model config through the same compiler contract."""

        unknown = sorted(set(config) - set(self.config_model.model_fields))
        if unknown:
            raise ValueError(
                f"Node kind {self.kind} has undeclared Adapter config fields: "
                + ", ".join(unknown)
            )
        return self.config_model.model_validate(config).model_dump(mode="json")

    def default_intent_config(self) -> dict[str, Any]:
        """Return the contract-owned authoring seed for a newly added node."""

        contract = workflow_node_contract_registry.require(self.kind)
        raw_default = dict(contract.planner.default_data or {})
        field_map = {
            "rolePrompt": "role_prompt",
            "taskInput": "task_input",
            "modelId": "model_id",
            "sourceAgentId": "source_agent_id",
            "methodSkillIds": "method_skill_ids",
        }
        normalized = {
            field_map.get(key, key): value
            for key, value in raw_default.items()
            if field_map.get(key, key) in self.config_model.model_fields
        }
        if self.kind == "workflow_agent":
            normalized.setdefault(
                "role_prompt", "Complete the assigned plan task accurately."
            )
            normalized.setdefault("task_input", "{{user_input}}")
        return self.validate_authoring_config(normalized)

    def editor_config(self, node: NativeWorkflowNode) -> dict[str, Any]:
        """Convert a native editor node into validated Adapter config only."""

        restored = self.decompile_node_v3(node)
        return self.validate_authoring_config(restored.config)

    @property
    def config_schema_checksum(self) -> str:
        return canonical_checksum(self.config_model.model_json_schema())

    @property
    def adapter_checksum(self) -> str:
        contract = workflow_node_contract_registry.require(self.kind)
        return canonical_checksum(
            {
                "kind": self.kind,
                "ir_version": META_PLANNER_IR_VERSION,
                "adapter_version": META_PLANNER_ADAPTER_VERSION,
                "config_schema_checksum": self.config_schema_checksum,
                "compiler_checksum": contract.compiler_checksum,
            }
        )

    @property
    def authoring_checksum(self) -> str:
        contract = workflow_node_contract_registry.require(self.kind)
        return canonical_checksum(
            {
                "kind": self.kind,
                "authoring_protocol_version": 1,
                "adapter_checksum": self.adapter_checksum,
                "config_schema_checksum": self.config_schema_checksum,
                "default_intent_config": self.default_intent_config(),
                "compiler_checksum": contract.compiler_checksum,
            }
        )


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


def _decompile_workflow_agent_v3(node: NativeWorkflowNode) -> GraphIntentNodeV3:
    legacy = _decompile_workflow_agent(node)
    data = node.data if isinstance(node.data, dict) else {}
    if int(data.get("plannerIRVersion") or 0) != META_PLANNER_IR_VERSION:
        raise ValueError("Workflow Agent does not carry Graph IR V3 metadata.")
    inputs = data.get("plannerInputsV3")
    outputs = data.get("plannerOutputsV3")
    if not isinstance(inputs, list) or not isinstance(outputs, list):
        raise ValueError("Workflow Agent is missing Graph IR V3 port metadata.")
    return GraphIntentNodeV3.model_validate(
        {
            "ref": legacy.ref,
            "kind": legacy.kind,
            "title": legacy.title,
            "description": legacy.description,
            "task_ids": legacy.task_ids,
            "inputs": inputs,
            "outputs": outputs,
            "config": legacy.config,
        }
    )


PLANNER_NODE_ADAPTERS: dict[str, PlannerNodeAdapter] = {
    "workflow_agent": PlannerNodeAdapter(
        kind="workflow_agent",
        config_model=MetaPlannerWorkflowAgentConfig,
        compile_node=_compile_workflow_agent,
        decompile_node=_decompile_workflow_agent,
        decompile_node_v3=_decompile_workflow_agent_v3,
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


def decompile_planner_node_v3(node: NativeWorkflowNode) -> GraphIntentNodeV3:
    kind = str((node.data or {}).get("kind") or node.type or "")
    adapter = get_planner_node_adapter(kind)
    if adapter is None:
        raise ValueError(f"Node kind {kind} has no compiler adapter.")
    return adapter.decompile_node_v3(node)


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
    adapter = get_planner_node_adapter(kind)
    adapter_checksum = (
        adapter.adapter_checksum
        if adapter is not None
        else canonical_checksum(
            {
                "kind": kind,
                "ir_version": META_PLANNER_IR_VERSION,
                "adapter_version": META_PLANNER_ADAPTER_VERSION,
                "support": support,
                "compiler_checksum": contract.compiler_checksum,
                "config_schema_checksum": "compiler-managed",
            }
        )
    )
    return {
        "compilable": True,
        "support": support,
        "ir_version": META_PLANNER_IR_VERSION,
        "adapter_version": META_PLANNER_ADAPTER_VERSION,
        "contract_version": NODE_CONTRACT_VERSION,
        "contract_checksum": contract.checksum,
        "compiler_checksum": contract.compiler_checksum,
        "adapter_checksum": adapter_checksum,
        "authoring_checksum": (
            adapter.authoring_checksum
            if adapter is not None
            else canonical_checksum(
                {
                    "kind": kind,
                    "authoring_protocol_version": 1,
                    "adapter_checksum": adapter_checksum,
                    "support": support,
                    "compiler_checksum": contract.compiler_checksum,
                }
            )
        ),
    }
