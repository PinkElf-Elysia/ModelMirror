from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

try:
    from server.workflow_native.node_contracts import (
        WorkflowValueSchema,
        canonical_checksum,
        workflow_node_contract_registry,
    )
except ModuleNotFoundError:
    from workflow_native.node_contracts import (
        WorkflowValueSchema,
        canonical_checksum,
        workflow_node_contract_registry,
    )

from .node_adapters import get_planner_node_adapter
from .schemas import (
    GraphIntentControlEdgeV3,
    GraphIntentFinalOutputV3,
    GraphIntentInputBindingV3,
    GraphIntentNodeV3,
    GraphIntentOutputBindingV3,
    GraphIntentV3,
    MetaPlannerIRMiddlewareBinding,
    MetaPlannerIRResourceBinding,
)


GRAPH_PATCH_PROTOCOL_VERSION = 1
GRAPH_PATCH_MAX_OPERATIONS = 64
GRAPH_PATCH_MAX_REQUEST_BYTES = 2 * 1024 * 1024
GRAPH_PATCH_MAX_JSON_DEPTH = 32
GRAPH_PATCH_REF_PATTERN = r"^[a-z][a-z0-9_-]{0,63}$"
GRAPH_PATCH_PORT_PATTERN = r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$"
GRAPH_PATCH_VARIABLE_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]{0,127}$"
GRAPH_PATCH_CHECKSUM_PATTERN = r"^[a-f0-9]{64}$"


class GraphPatchModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SetXpertMetadataOperation(GraphPatchModel):
    op: Literal["set_xpert_metadata"] = "set_xpert_metadata"
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2_000)
    tags: list[str] | None = Field(default=None, max_length=20)
    starters: list[str] | None = Field(default=None, max_length=8)

    @model_validator(mode="after")
    def require_change(self) -> "SetXpertMetadataOperation":
        if all(
            value is None
            for value in (self.name, self.description, self.tags, self.starters)
        ):
            raise ValueError("set_xpert_metadata needs at least one field")
        return self


class AddNodeOperation(GraphPatchModel):
    op: Literal["add_node"] = "add_node"
    ref: str = Field(
        pattern=GRAPH_PATCH_REF_PATTERN,
        description=(
            "A new semantic node ref. The compiler-managed refs input and output "
            "are forbidden."
        ),
        json_schema_extra={"not": {"enum": ["input", "output"]}},
    )
    kind: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2_000)
    task_ids: list[str] = Field(default_factory=list, max_length=8)
    config: dict[str, Any] = Field(default_factory=dict)
    output_variables: dict[str, str] = Field(default_factory=dict, max_length=16)

    @field_validator("ref")
    @classmethod
    def reject_compiler_managed_ref(cls, value: str) -> str:
        if value in {"input", "output"}:
            raise ValueError(f"{value} is a compiler-managed ref and cannot be added")
        return value


class UpdateNodeOperation(GraphPatchModel):
    op: Literal["update_node"] = "update_node"
    ref: str = Field(pattern=GRAPH_PATCH_REF_PATTERN)
    title: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2_000)
    task_ids: list[str] | None = Field(default=None, max_length=8)
    config: dict[str, Any] | None = None

    @model_validator(mode="after")
    def require_change(self) -> "UpdateNodeOperation":
        if all(
            value is None
            for value in (self.title, self.description, self.task_ids, self.config)
        ):
            raise ValueError("update_node needs at least one field")
        return self


class RemoveNodeOperation(GraphPatchModel):
    op: Literal["remove_node"] = "remove_node"
    ref: str = Field(pattern=GRAPH_PATCH_REF_PATTERN)


class ConnectControlOperation(GraphPatchModel):
    op: Literal["connect_control"] = "connect_control"
    source_ref: str = Field(pattern=GRAPH_PATCH_REF_PATTERN)
    target_ref: str = Field(pattern=GRAPH_PATCH_REF_PATTERN)


class DisconnectControlOperation(GraphPatchModel):
    op: Literal["disconnect_control"] = "disconnect_control"
    source_ref: str = Field(pattern=GRAPH_PATCH_REF_PATTERN)
    target_ref: str = Field(pattern=GRAPH_PATCH_REF_PATTERN)


class ConnectDataOperation(GraphPatchModel):
    op: Literal["connect_data"] = "connect_data"
    source_ref: str = Field(pattern=GRAPH_PATCH_REF_PATTERN)
    source_port: str = Field(pattern=GRAPH_PATCH_PORT_PATTERN)
    target_ref: str = Field(pattern=GRAPH_PATCH_REF_PATTERN)
    target_port: str = Field(pattern=GRAPH_PATCH_PORT_PATTERN)


class DisconnectDataOperation(GraphPatchModel):
    op: Literal["disconnect_data"] = "disconnect_data"
    source_ref: str = Field(pattern=GRAPH_PATCH_REF_PATTERN)
    source_port: str = Field(pattern=GRAPH_PATCH_PORT_PATTERN)
    target_ref: str = Field(pattern=GRAPH_PATCH_REF_PATTERN)
    target_port: str = Field(pattern=GRAPH_PATCH_PORT_PATTERN)


class SetOutputVariableOperation(GraphPatchModel):
    op: Literal["set_output_variable"] = "set_output_variable"
    node_ref: str = Field(pattern=GRAPH_PATCH_REF_PATTERN)
    port: str = Field(pattern=GRAPH_PATCH_PORT_PATTERN)
    variable: str = Field(pattern=GRAPH_PATCH_VARIABLE_PATTERN)


class BindResourceOperation(GraphPatchModel):
    op: Literal["bind_resource"] = "bind_resource"
    target_ref: str = Field(pattern=GRAPH_PATCH_REF_PATTERN)
    kind: Literal[
        "external_xpert",
        "knowledge_base",
        "toolset_resource",
        "plugin_resource",
    ]
    resource_id: str = Field(min_length=1, max_length=200)
    tool_name: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=1_000)
    top_k: int = Field(default=5, ge=1, le=50)
    score_threshold: float = Field(default=0.0, ge=0, le=1)


class UnbindResourceOperation(GraphPatchModel):
    op: Literal["unbind_resource"] = "unbind_resource"
    target_ref: str = Field(pattern=GRAPH_PATCH_REF_PATTERN)
    kind: Literal[
        "external_xpert",
        "knowledge_base",
        "toolset_resource",
        "plugin_resource",
    ]
    resource_id: str = Field(min_length=1, max_length=200)


class BindMiddlewareOperation(GraphPatchModel):
    op: Literal["bind_middleware"] = "bind_middleware"
    target_ref: str = Field(pattern=GRAPH_PATCH_REF_PATTERN)
    middleware_id: str = Field(min_length=1, max_length=160)
    priority: int = Field(default=100, ge=0, le=10_000)
    config: dict[str, Any] = Field(default_factory=dict)


class UnbindMiddlewareOperation(GraphPatchModel):
    op: Literal["unbind_middleware"] = "unbind_middleware"
    target_ref: str = Field(pattern=GRAPH_PATCH_REF_PATTERN)
    middleware_id: str = Field(min_length=1, max_length=160)


class BindPromptProfileOperation(GraphPatchModel):
    op: Literal["bind_prompt_profile"] = "bind_prompt_profile"
    profile_id: str = Field(min_length=1, max_length=200)


class UnbindPromptProfileOperation(GraphPatchModel):
    op: Literal["unbind_prompt_profile"] = "unbind_prompt_profile"
    profile_id: str = Field(min_length=1, max_length=200)


class SetFinalOutputOperation(GraphPatchModel):
    op: Literal["set_final_output"] = "set_final_output"
    node_ref: str = Field(pattern=GRAPH_PATCH_REF_PATTERN)
    port: str = Field(default="result", pattern=GRAPH_PATCH_PORT_PATTERN)


class MoveNodeOperation(GraphPatchModel):
    op: Literal["move_node"] = "move_node"
    ref: str = Field(pattern=GRAPH_PATCH_REF_PATTERN)
    x: float = Field(ge=-100_000, le=100_000)
    y: float = Field(ge=-100_000, le=100_000)


GraphPatchOperation = Annotated[
    Union[
        SetXpertMetadataOperation,
        AddNodeOperation,
        UpdateNodeOperation,
        RemoveNodeOperation,
        ConnectControlOperation,
        DisconnectControlOperation,
        ConnectDataOperation,
        DisconnectDataOperation,
        SetOutputVariableOperation,
        BindResourceOperation,
        UnbindResourceOperation,
        BindMiddlewareOperation,
        UnbindMiddlewareOperation,
        BindPromptProfileOperation,
        UnbindPromptProfileOperation,
        SetFinalOutputOperation,
        MoveNodeOperation,
    ],
    Field(discriminator="op"),
]


class GraphPatchEnvelopeV1(GraphPatchModel):
    protocol_version: Literal[1] = GRAPH_PATCH_PROTOCOL_VERSION
    proposal_revision: int = Field(ge=1)
    expected_graph_checksum: str = Field(pattern=GRAPH_PATCH_CHECKSUM_PATTERN)
    expected_candidate_checksum: str = Field(pattern=GRAPH_PATCH_CHECKSUM_PATTERN)
    operations: list[GraphPatchOperation] = Field(
        default_factory=list,
        max_length=GRAPH_PATCH_MAX_OPERATIONS,
    )


class GraphPatchApplyRequest(GraphPatchModel):
    patch: GraphPatchEnvelopeV1
    preview_checksum: str = Field(pattern=GRAPH_PATCH_CHECKSUM_PATTERN)


class GraphPatchEditorDiffRequest(GraphPatchModel):
    proposal_revision: int = Field(ge=1)
    definition: dict[str, Any]


@dataclass(frozen=True, slots=True)
class GraphPatchResult:
    intent: GraphIntentV3
    layout: dict[str, dict[str, float]]
    operation_types: tuple[str, ...]


def graph_patch_schema() -> dict[str, Any]:
    return GraphPatchEnvelopeV1.model_json_schema()


def graph_patch_checksum(patch: GraphPatchEnvelopeV1) -> str:
    return canonical_checksum(patch.model_dump(mode="json"))


def _port_schema(kind: str, port_name: str, direction: str) -> WorkflowValueSchema:
    contract = workflow_node_contract_registry.require(kind)
    for port in contract.ports:
        if port.name == port_name and port.direction == direction:
            return port.value_schema
    raise ValueError(f"Node kind {kind} has no {direction} port {port_name}.")


def _source_output(
    intent: GraphIntentV3,
    source_ref: str,
    source_port: str,
) -> GraphIntentOutputBindingV3:
    if source_ref == "input":
        inputs = {
            "user_input": GraphIntentOutputBindingV3(
                port="user_input",
                variable="user_input",
                value_schema=WorkflowValueSchema(type="string"),
            ),
            "conversation_history": GraphIntentOutputBindingV3(
                port="conversation_history",
                variable="conversation_history",
                value_schema=WorkflowValueSchema(
                    type="array", items=WorkflowValueSchema(type="object")
                ),
            ),
        }
        binding = inputs.get(source_port)
        if binding is None:
            raise ValueError(f"Input has no output port {source_port}.")
        return binding
    source = next((node for node in intent.nodes if node.ref == source_ref), None)
    if source is None:
        raise ValueError(f"Unknown source node ref {source_ref}.")
    binding = next((item for item in source.outputs if item.port == source_port), None)
    if binding is None:
        raise ValueError(f"Node {source_ref} has no output port {source_port}.")
    return binding


def _node(intent: GraphIntentV3, ref: str) -> GraphIntentNodeV3:
    if ref in {"input", "output"}:
        raise ValueError(f"Compiler-managed node {ref} cannot be patched.")
    node = next((item for item in intent.nodes if item.ref == ref), None)
    if node is None:
        raise ValueError(f"Unknown node ref {ref}.")
    return node


def _validate_task_ids(
    kind: str,
    task_ids: list[str],
    plan_task_ids: set[str],
) -> None:
    unknown = sorted(set(task_ids) - plan_task_ids)
    if unknown:
        raise ValueError("Patch references unknown plan tasks: " + ", ".join(unknown))
    contract = workflow_node_contract_registry.require(kind)
    if contract.planner.task_binding == "required" and not task_ids:
        raise ValueError(f"Node kind {kind} must cover at least one plan task.")
    if contract.planner.task_binding == "forbidden" and task_ids:
        raise ValueError(f"Node kind {kind} cannot cover plan tasks.")


def _replace_node(intent: GraphIntentV3, replacement: GraphIntentNodeV3) -> None:
    intent.nodes = [
        replacement if node.ref == replacement.ref else node for node in intent.nodes
    ]


def _resource_key(binding: MetaPlannerIRResourceBinding) -> tuple[str, str, str]:
    return binding.target_ref, binding.kind, binding.resource_id


def _middleware_key(binding: MetaPlannerIRMiddlewareBinding) -> tuple[str, str]:
    return binding.target_ref, binding.middleware_id


def _data_key(binding: GraphIntentInputBindingV3, target_ref: str) -> tuple[str, str, str, str]:
    return binding.source_ref, binding.source_port, target_ref, binding.port


def apply_graph_patch(
    intent: GraphIntentV3,
    patch: GraphPatchEnvelopeV1,
    *,
    plan_task_ids: set[str],
    layout: dict[str, dict[str, float]] | None = None,
    allowed_node_kinds: set[str] | None = None,
    movable_refs: set[str] | None = None,
) -> GraphPatchResult:
    """Apply an ordered semantic patch without resolving runtime-owned facts."""

    result = intent.model_copy(deep=True)
    result._pinned_resource_versions = dict(intent._pinned_resource_versions)
    result._pinned_prompt_profile_versions = dict(
        intent._pinned_prompt_profile_versions
    )
    original_resource_pins = dict(intent._pinned_resource_versions)
    next_layout = deepcopy(layout or {})
    pending_removals: set[str] = set()

    for operation in patch.operations:
        if isinstance(operation, SetXpertMetadataOperation):
            updates: dict[str, Any] = {}
            for field_name in ("name", "description", "tags", "starters"):
                value = getattr(operation, field_name)
                if value is not None:
                    updates[field_name] = list(dict.fromkeys(value)) if isinstance(value, list) else value
            result = result.model_copy(update=updates)
            continue

        if isinstance(operation, AddNodeOperation):
            if operation.ref in {"input", "output"}:
                raise ValueError("Compiler-managed refs cannot be added.")
            if any(node.ref == operation.ref for node in result.nodes):
                raise ValueError(f"Node ref {operation.ref} already exists.")
            if allowed_node_kinds is not None and operation.kind not in allowed_node_kinds:
                raise ValueError(f"Node kind {operation.kind} is not authorized.")
            adapter = get_planner_node_adapter(operation.kind)
            if adapter is None:
                raise ValueError(f"Node kind {operation.kind} has no authoring adapter.")
            _validate_task_ids(operation.kind, operation.task_ids, plan_task_ids)
            parsed_config = adapter.validate_authoring_config(operation.config)
            parsed_model = adapter.config_model.model_validate(parsed_config)
            contract = workflow_node_contract_registry.require(operation.kind)
            output_ports = {
                port.name: port
                for port in contract.ports
                if port.direction == "output"
            }
            unknown_ports = sorted(set(operation.output_variables) - set(output_ports))
            if unknown_ports:
                raise ValueError(
                    f"Node {operation.ref} declares unknown output ports: "
                    + ", ".join(unknown_ports)
                )
            outputs = [
                GraphIntentOutputBindingV3(
                    port=port_name,
                    variable=variable,
                    value_schema=adapter.authoritative_output_schema(
                        port_name,
                        parsed_model,
                    ),
                )
                for port_name, variable in sorted(operation.output_variables.items())
            ]
            added_node = GraphIntentNodeV3(
                ref=operation.ref,
                kind=operation.kind,
                title=operation.title,
                description=operation.description,
                task_ids=operation.task_ids,
                config=parsed_config,
                outputs=outputs,
            )
            result.nodes.append(added_node)
            continue

        if isinstance(operation, UpdateNodeOperation):
            current = _node(result, operation.ref)
            updates: dict[str, Any] = {}
            if operation.title is not None:
                updates["title"] = operation.title
            if operation.description is not None:
                updates["description"] = operation.description
            if operation.task_ids is not None:
                _validate_task_ids(
                    current.kind,
                    operation.task_ids,
                    plan_task_ids,
                )
                updates["task_ids"] = operation.task_ids
            if operation.config is not None:
                adapter = get_planner_node_adapter(current.kind)
                if adapter is None:
                    raise ValueError(
                        f"Node kind {current.kind} has no authoring adapter."
                    )
                normalized_config = adapter.validate_authoring_config(operation.config)
                parsed_model = adapter.config_model.model_validate(normalized_config)
                updates["config"] = normalized_config
                updates["outputs"] = [
                    output.model_copy(
                        update={
                            "value_schema": adapter.authoritative_output_schema(
                                output.port,
                                parsed_model,
                            )
                        }
                    )
                    for output in current.outputs
                ]
            updated_node = current.model_copy(update=updates)
            adapter = get_planner_node_adapter(current.kind)
            assert adapter is not None
            adapter.validate_intent_node(updated_node)
            _replace_node(result, updated_node)
            continue

        if isinstance(operation, RemoveNodeOperation):
            _node(result, operation.ref)
            pending_removals.add(operation.ref)
            continue

        if isinstance(operation, ConnectControlOperation):
            _node(result, operation.source_ref)
            _node(result, operation.target_ref)
            key = (operation.source_ref, operation.target_ref)
            if any(
                (edge.source_ref, edge.target_ref) == key
                for edge in result.control_edges
            ):
                raise ValueError("Control edge already exists.")
            result.control_edges.append(
                GraphIntentControlEdgeV3(
                    source_ref=operation.source_ref,
                    target_ref=operation.target_ref,
                )
            )
            continue

        if isinstance(operation, DisconnectControlOperation):
            before = len(result.control_edges)
            result.control_edges = [
                edge
                for edge in result.control_edges
                if (edge.source_ref, edge.target_ref)
                != (operation.source_ref, operation.target_ref)
            ]
            if len(result.control_edges) == before:
                raise ValueError("Control edge does not exist.")
            continue

        if isinstance(operation, ConnectDataOperation):
            target = _node(result, operation.target_ref)
            _port_schema(target.kind, operation.target_port, "input")
            source = _source_output(result, operation.source_ref, operation.source_port)
            if any(
                _data_key(binding, target.ref)
                == (
                    operation.source_ref,
                    operation.source_port,
                    operation.target_ref,
                    operation.target_port,
                )
                for binding in target.inputs
            ):
                raise ValueError("Data edge already exists.")
            updated = target.model_copy(
                update={
                    "inputs": [
                        *target.inputs,
                        GraphIntentInputBindingV3(
                            port=operation.target_port,
                            variable=source.variable,
                            source_ref=operation.source_ref,
                            source_port=operation.source_port,
                            value_schema=source.value_schema,
                        ),
                    ]
                }
            )
            _replace_node(result, updated)
            continue

        if isinstance(operation, DisconnectDataOperation):
            target = _node(result, operation.target_ref)
            key = (
                operation.source_ref,
                operation.source_port,
                operation.target_ref,
                operation.target_port,
            )
            filtered = [
                binding
                for binding in target.inputs
                if _data_key(binding, target.ref) != key
            ]
            if len(filtered) == len(target.inputs):
                raise ValueError("Data edge does not exist.")
            _replace_node(result, target.model_copy(update={"inputs": filtered}))
            continue

        if isinstance(operation, SetOutputVariableOperation):
            target = _node(result, operation.node_ref)
            selected = next(
                (item for item in target.outputs if item.port == operation.port), None
            )
            if selected is None:
                selected = GraphIntentOutputBindingV3(
                    port=operation.port,
                    variable=operation.variable,
                    value_schema=_port_schema(target.kind, operation.port, "output"),
                )
                old_variable = ""
                updated_outputs = [*target.outputs, selected]
            else:
                old_variable = selected.variable
                updated_outputs = [
                    item.model_copy(update={"variable": operation.variable})
                    if item.port == operation.port
                    else item
                    for item in target.outputs
                ]
            _replace_node(result, target.model_copy(update={"outputs": updated_outputs}))
            updated_nodes: list[GraphIntentNodeV3] = []
            for node in result.nodes:
                updated_inputs = [
                    binding.model_copy(update={"variable": operation.variable})
                    if binding.source_ref == operation.node_ref
                    and binding.source_port == operation.port
                    and binding.variable == old_variable
                    else binding
                    for binding in node.inputs
                ]
                updated_nodes.append(node.model_copy(update={"inputs": updated_inputs}))
            result.nodes = updated_nodes
            if (
                result.final_output.node_ref == operation.node_ref
                and result.final_output.port == operation.port
            ):
                result.final_output = result.final_output.model_copy(
                    update={"variable": operation.variable}
                )
            continue

        if isinstance(operation, BindResourceOperation):
            _node(result, operation.target_ref)
            candidate = MetaPlannerIRResourceBinding(
                target_ref=operation.target_ref,
                kind=operation.kind,
                resource_id=operation.resource_id,
                tool_name=operation.tool_name,
                description=operation.description,
                top_k=operation.top_k,
                score_threshold=operation.score_threshold,
            )
            if any(_resource_key(item) == _resource_key(candidate) for item in result.resources):
                raise ValueError("Resource binding already exists.")
            result.resources.append(candidate)
            pin_key = (
                operation.kind,
                operation.resource_id,
                operation.target_ref,
            )
            if pin_key in original_resource_pins:
                result._pinned_resource_versions[pin_key] = original_resource_pins[
                    pin_key
                ]
            continue

        if isinstance(operation, UnbindResourceOperation):
            key = (operation.target_ref, operation.kind, operation.resource_id)
            before = len(result.resources)
            result.resources = [
                item for item in result.resources if _resource_key(item) != key
            ]
            if len(result.resources) == before:
                raise ValueError("Resource binding does not exist.")
            result._pinned_resource_versions.pop(
                (operation.kind, operation.resource_id, operation.target_ref), None
            )
            continue

        if isinstance(operation, BindMiddlewareOperation):
            _node(result, operation.target_ref)
            candidate = MetaPlannerIRMiddlewareBinding(
                target_ref=operation.target_ref,
                middleware_id=operation.middleware_id,
                priority=operation.priority,
                config=operation.config,
            )
            if any(
                _middleware_key(item) == _middleware_key(candidate)
                for item in result.middleware
            ):
                raise ValueError("Middleware binding already exists.")
            result.middleware.append(candidate)
            continue

        if isinstance(operation, UnbindMiddlewareOperation):
            key = (operation.target_ref, operation.middleware_id)
            before = len(result.middleware)
            result.middleware = [
                item for item in result.middleware if _middleware_key(item) != key
            ]
            if len(result.middleware) == before:
                raise ValueError("Middleware binding does not exist.")
            continue

        if isinstance(operation, BindPromptProfileOperation):
            if operation.profile_id in result.prompt_profile_ids:
                raise ValueError("Prompt Profile binding already exists.")
            result.prompt_profile_ids.append(operation.profile_id)
            continue

        if isinstance(operation, UnbindPromptProfileOperation):
            if operation.profile_id not in result.prompt_profile_ids:
                raise ValueError("Prompt Profile binding does not exist.")
            result.prompt_profile_ids = [
                item for item in result.prompt_profile_ids if item != operation.profile_id
            ]
            result._pinned_prompt_profile_versions.pop(operation.profile_id, None)
            continue

        if isinstance(operation, SetFinalOutputOperation):
            target = _node(result, operation.node_ref)
            if target.kind != "workflow_agent":
                raise ValueError("Final output must come from a workflow_agent.")
            output = next(
                (item for item in target.outputs if item.port == operation.port), None
            )
            if output is None:
                raise ValueError(
                    f"Node {operation.node_ref} has no output port {operation.port}."
                )
            result.final_output = GraphIntentFinalOutputV3(
                node_ref=operation.node_ref,
                port=operation.port,
                variable=output.variable,
            )
            continue

        if isinstance(operation, MoveNodeOperation):
            if movable_refs is not None:
                if operation.ref not in movable_refs:
                    raise ValueError(f"Unknown movable node ref {operation.ref}.")
            elif operation.ref not in {"input", "output"}:
                _node(result, operation.ref)
            next_layout[operation.ref] = {"x": operation.x, "y": operation.y}
            continue

        raise ValueError("Unsupported Graph Patch operation.")

    for ref in sorted(pending_removals):
        incidents: list[str] = []
        incidents.extend(
            f"control:{edge.source_ref}->{edge.target_ref}"
            for edge in result.control_edges
            if ref in {edge.source_ref, edge.target_ref}
        )
        for node in result.nodes:
            incidents.extend(
                f"data:{binding.source_ref}.{binding.source_port}->{node.ref}.{binding.port}"
                for binding in node.inputs
                if ref in {binding.source_ref, node.ref}
            )
        incidents.extend(
            f"resource:{item.kind}:{item.resource_id}"
            for item in result.resources
            if item.target_ref == ref
        )
        incidents.extend(
            f"middleware:{item.middleware_id}"
            for item in result.middleware
            if item.target_ref == ref
        )
        if result.final_output.node_ref == ref:
            incidents.append("final_output")
        if incidents:
            raise ValueError(
                f"Node {ref} still has dependencies; explicitly detach them in the same patch: "
                + ", ".join(incidents[:12])
            )
        result.nodes = [node for node in result.nodes if node.ref != ref]
        next_layout.pop(ref, None)

    for node in result.nodes:
        adapter = get_planner_node_adapter(node.kind)
        if adapter is None:
            raise ValueError(f"Node kind {node.kind} has no authoring adapter.")
        adapter.validate_intent_node(node)

    covered_tasks = {
        task_id
        for node in result.nodes
        if workflow_node_contract_registry.require(
            node.kind
        ).planner.task_binding
        == "required"
        for task_id in node.task_ids
    }
    missing_tasks = sorted(plan_task_ids - covered_tasks)
    if missing_tasks:
        raise ValueError(
            "Graph Patch cannot change the fixed plan; uncovered tasks: "
            + ", ".join(missing_tasks)
        )
    if not result.nodes:
        raise ValueError("Graph Patch cannot remove every executable node.")
    validated = GraphIntentV3.model_validate(result.model_dump(mode="json"))
    validated._pinned_resource_versions = dict(result._pinned_resource_versions)
    validated._pinned_prompt_profile_versions = dict(
        result._pinned_prompt_profile_versions
    )
    return GraphPatchResult(
        intent=validated,
        layout=next_layout,
        operation_types=tuple(operation.op for operation in patch.operations),
    )


def diff_graph_intents(
    source: GraphIntentV3,
    target: GraphIntentV3,
    *,
    proposal_revision: int,
    expected_graph_checksum: str,
    expected_candidate_checksum: str,
    source_layout: dict[str, dict[str, float]] | None = None,
    target_layout: dict[str, dict[str, float]] | None = None,
) -> GraphPatchEnvelopeV1:
    operations: list[GraphPatchOperation] = []
    metadata: dict[str, Any] = {}
    for field_name in ("name", "description", "tags", "starters"):
        if getattr(source, field_name) != getattr(target, field_name):
            metadata[field_name] = getattr(target, field_name)
    if metadata:
        operations.append(SetXpertMetadataOperation(**metadata))

    source_nodes = {node.ref: node for node in source.nodes}
    target_nodes = {node.ref: node for node in target.nodes}
    source_data = {
        _data_key(binding, node.ref)
        for node in source.nodes
        for binding in node.inputs
    }
    target_data = {
        _data_key(binding, node.ref)
        for node in target.nodes
        for binding in node.inputs
    }
    source_control = {
        (edge.source_ref, edge.target_ref) for edge in source.control_edges
    }
    target_control = {
        (edge.source_ref, edge.target_ref) for edge in target.control_edges
    }
    source_resources = {_resource_key(item): item for item in source.resources}
    target_resources = {_resource_key(item): item for item in target.resources}
    source_middleware = {_middleware_key(item): item for item in source.middleware}
    target_middleware = {_middleware_key(item): item for item in target.middleware}

    for key in sorted(source_data - target_data):
        operations.append(
            DisconnectDataOperation(
                source_ref=key[0],
                source_port=key[1],
                target_ref=key[2],
                target_port=key[3],
            )
        )
    for source_ref, target_ref in sorted(source_control - target_control):
        operations.append(
            DisconnectControlOperation(
                source_ref=source_ref, target_ref=target_ref
            )
        )
    for key in sorted(set(source_resources) - set(target_resources)):
        operations.append(
            UnbindResourceOperation(
                target_ref=key[0], kind=key[1], resource_id=key[2]
            )
        )
    for key in sorted(set(source_middleware) - set(target_middleware)):
        operations.append(
            UnbindMiddlewareOperation(
                target_ref=key[0], middleware_id=key[1]
            )
        )

    for ref in sorted(set(source_nodes) - set(target_nodes)):
        operations.append(RemoveNodeOperation(ref=ref))
    for ref in sorted(set(target_nodes) - set(source_nodes)):
        node = target_nodes[ref]
        operations.append(
            AddNodeOperation(
                ref=node.ref,
                kind=node.kind,
                title=node.title,
                description=node.description,
                task_ids=node.task_ids,
                config=node.config,
                output_variables={item.port: item.variable for item in node.outputs},
            )
        )

    for ref in sorted(set(source_nodes) & set(target_nodes)):
        before = source_nodes[ref]
        after = target_nodes[ref]
        if before.kind != after.kind:
            raise ValueError(
                f"Editor change for {ref} replaces its node kind and is not expressible."
            )
        updates: dict[str, Any] = {"ref": ref}
        for field_name in ("title", "description", "task_ids", "config"):
            if getattr(before, field_name) != getattr(after, field_name):
                updates[field_name] = getattr(after, field_name)
        if len(updates) > 1:
            operations.append(UpdateNodeOperation(**updates))
        before_outputs = {item.port: item for item in before.outputs}
        after_outputs = {item.port: item for item in after.outputs}
        if set(before_outputs) != set(after_outputs):
            raise ValueError(
                f"Editor change for {ref} changes contract-owned output ports."
            )
        for port in sorted(before_outputs):
            if before_outputs[port].value_schema != after_outputs[port].value_schema:
                raise ValueError(
                    f"Editor change for {ref}.{port} changes a contract-owned schema."
                )
            if before_outputs[port].variable != after_outputs[port].variable:
                operations.append(
                    SetOutputVariableOperation(
                        node_ref=ref,
                        port=port,
                        variable=after_outputs[port].variable,
                    )
                )

    for source_ref, target_ref in sorted(target_control - source_control):
        operations.append(
            ConnectControlOperation(source_ref=source_ref, target_ref=target_ref)
        )
    for key in sorted(target_data - source_data):
        operations.append(
            ConnectDataOperation(
                source_ref=key[0],
                source_port=key[1],
                target_ref=key[2],
                target_port=key[3],
            )
        )
    for key in sorted(set(target_resources) - set(source_resources)):
        item = target_resources[key]
        operations.append(BindResourceOperation(**item.model_dump(mode="json")))
    for key in sorted(set(target_middleware) - set(source_middleware)):
        item = target_middleware[key]
        operations.append(BindMiddlewareOperation(**item.model_dump(mode="json")))

    for key in sorted(set(source_resources) & set(target_resources)):
        if source_resources[key] != target_resources[key]:
            before = source_resources[key]
            after = target_resources[key]
            operations.extend(
                [
                    UnbindResourceOperation(
                        target_ref=before.target_ref,
                        kind=before.kind,
                        resource_id=before.resource_id,
                    ),
                    BindResourceOperation(**after.model_dump(mode="json")),
                ]
            )
    for key in sorted(set(source_middleware) & set(target_middleware)):
        if source_middleware[key] != target_middleware[key]:
            before = source_middleware[key]
            after = target_middleware[key]
            operations.extend(
                [
                    UnbindMiddlewareOperation(
                        target_ref=before.target_ref,
                        middleware_id=before.middleware_id,
                    ),
                    BindMiddlewareOperation(**after.model_dump(mode="json")),
                ]
            )

    for profile_id in sorted(
        set(source.prompt_profile_ids) - set(target.prompt_profile_ids)
    ):
        operations.append(UnbindPromptProfileOperation(profile_id=profile_id))
    for profile_id in sorted(
        set(target.prompt_profile_ids) - set(source.prompt_profile_ids)
    ):
        operations.append(BindPromptProfileOperation(profile_id=profile_id))
    if source.final_output != target.final_output:
        operations.append(
            SetFinalOutputOperation(
                node_ref=target.final_output.node_ref,
                port=target.final_output.port,
            )
        )

    before_layout = source_layout or {}
    after_layout = target_layout or {}
    for ref in sorted(set(after_layout)):
        if before_layout.get(ref) != after_layout.get(ref):
            point = after_layout[ref]
            operations.append(MoveNodeOperation(ref=ref, x=point["x"], y=point["y"]))

    if len(operations) > GRAPH_PATCH_MAX_OPERATIONS:
        raise ValueError(
            f"Editor diff requires {len(operations)} operations; the limit is "
            f"{GRAPH_PATCH_MAX_OPERATIONS}."
        )
    return GraphPatchEnvelopeV1(
        proposal_revision=proposal_revision,
        expected_graph_checksum=expected_graph_checksum,
        expected_candidate_checksum=expected_candidate_checksum,
        operations=operations,
    )
