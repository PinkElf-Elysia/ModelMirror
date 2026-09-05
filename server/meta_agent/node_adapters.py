from __future__ import annotations

import re
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
        ConditionPlannerConfig,
        DataAggregatePlannerConfig,
        DataMergePlannerConfig,
        DataTableQueryFilterPlannerConfig,
        DataTableQueryPlannerConfig,
        DataTableQueryPredicatePlannerConfig,
        DatasetComparePlannerConfig,
        JsonDeserializePlannerConfig,
        JsonSerializePlannerConfig,
        KnowledgeRetrievalPlannerConfig,
        MultiRoutePlannerConfig,
        NODE_CONTRACT_VERSION,
        TerminateErrorPlannerConfig,
        VariableAggregatorPlannerConfig,
        WorkflowValueSchema,
        canonical_checksum,
        workflow_node_contract_registry,
    )
    from server.workflow_native.schemas import NativeWorkflowNode, WorkflowPosition
except ModuleNotFoundError:
    from workflow_native.node_contracts import (
        ConditionPlannerConfig,
        DataAggregatePlannerConfig,
        DataMergePlannerConfig,
        DataTableQueryFilterPlannerConfig,
        DataTableQueryPlannerConfig,
        DataTableQueryPredicatePlannerConfig,
        DatasetComparePlannerConfig,
        JsonDeserializePlannerConfig,
        JsonSerializePlannerConfig,
        KnowledgeRetrievalPlannerConfig,
        MultiRoutePlannerConfig,
        NODE_CONTRACT_VERSION,
        TerminateErrorPlannerConfig,
        VariableAggregatorPlannerConfig,
        WorkflowValueSchema,
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
    resource_snapshot: dict[str, Any] | None = None


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
    referenced_variables: Callable[[BaseModel], set[str]] | None = None
    output_schema: Callable[
        [str, BaseModel, dict[str, Any] | None], WorkflowValueSchema
    ] | None = None
    validate_node_shape: Callable[
        [MetaPlannerIRNode | GraphIntentNodeV3, BaseModel], None
    ] | None = None
    native_config: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    editor_config_projector: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    native_inputs: Callable[
        [dict[str, Any], BaseModel], list[tuple[str, str]]
    ] | None = None
    native_outputs: Callable[
        [dict[str, Any], BaseModel], dict[str, str]
    ] | None = None
    resource_kind: str | None = None
    validate_resource: Callable[
        [GraphIntentNodeV3, BaseModel, dict[str, Any]], None
    ] | None = None
    control_only_output_ports: tuple[str, ...] = ()
    contract_version: int = NODE_CONTRACT_VERSION

    def intent_port_contracts(self, direction: str) -> tuple[Any, ...]:
        contract = workflow_node_contract_registry.require(self.kind)
        return tuple(
            port
            for port in contract.ports
            if port.direction == direction
            and not (
                direction == "output"
                and port.name in self.control_only_output_ports
            )
        )

    def validate_config(self, node: MetaPlannerIRNode) -> BaseModel:
        parsed = self.config_model.model_validate(node.config)
        if self.validate_node_shape is not None:
            self.validate_node_shape(node, parsed)
        return parsed

    def validate_intent_node(self, node: GraphIntentNodeV3) -> BaseModel:
        parsed = self.config_model.model_validate(node.config)
        if self.validate_node_shape is not None:
            self.validate_node_shape(node, parsed)
        return parsed

    def referenced_input_variables(self, parsed: BaseModel) -> set[str]:
        if self.referenced_variables is None:
            return set()
        return set(self.referenced_variables(parsed))

    def authoritative_output_schema(
        self,
        port: str,
        parsed: BaseModel,
        resource_snapshot: dict[str, Any] | None = None,
    ) -> WorkflowValueSchema:
        if self.output_schema is not None:
            return self.output_schema(port, parsed, resource_snapshot)
        contract = workflow_node_contract_registry.require(self.kind)
        match = next(
            (
                item
                for item in contract.ports
                if item.direction == "output" and item.name == port
            ),
            None,
        )
        if match is None:
            raise ValueError(f"Node kind {self.kind} has no output port {port}.")
        return match.value_schema

    def validate_resolved_resource(
        self,
        node: GraphIntentNodeV3,
        parsed: BaseModel,
        resource_snapshot: dict[str, Any] | None,
    ) -> None:
        if self.resource_kind is None:
            if node.resource_ref is not None or resource_snapshot is not None:
                raise ValueError(
                    f"Node kind {self.kind} cannot carry a node resource reference."
                )
            return
        if node.resource_ref is None or resource_snapshot is None:
            raise ValueError(f"Node kind {self.kind} requires a resource reference.")
        if resource_snapshot.get("kind") != self.resource_kind:
            raise ValueError(
                f"Node kind {self.kind} requires resource kind {self.resource_kind}."
            )
        if self.validate_resource is not None:
            self.validate_resource(node, parsed, resource_snapshot)

    def authoring_config_from_native(self, data: dict[str, Any]) -> BaseModel:
        projector = self.editor_config_projector or self.native_config
        if projector is None:
            raise ValueError(
                f"Node kind {self.kind} cannot be projected from editor data."
            )
        return self.config_model.model_validate(projector(data))

    def editor_input_variables(
        self,
        data: dict[str, Any],
        parsed: BaseModel,
    ) -> list[tuple[str, str]]:
        if self.native_inputs is None:
            raise ValueError(f"Node kind {self.kind} has no editor input projection.")
        return list(self.native_inputs(data, parsed))

    def editor_output_variables(
        self,
        data: dict[str, Any],
        parsed: BaseModel,
    ) -> dict[str, str]:
        if self.native_outputs is None:
            raise ValueError(f"Node kind {self.kind} has no editor output projection.")
        return dict(self.native_outputs(data, parsed))

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


def _port_matches(actual: str, expected: str) -> bool:
    return actual == expected or actual.startswith(f"{expected}_")


def _input_bindings(
    node: MetaPlannerIRNode | GraphIntentNodeV3,
    port: str,
) -> list[Any]:
    return [item for item in node.inputs if _port_matches(item.port, port)]


def _require_single_input(
    node: MetaPlannerIRNode | GraphIntentNodeV3,
    port: str,
) -> Any:
    matches = _input_bindings(node, port)
    if len(matches) != 1:
        raise ValueError(f"Node {node.ref} requires exactly one {port} input.")
    return matches[0]


def _require_single_output(
    node: MetaPlannerIRNode | GraphIntentNodeV3,
    port: str,
) -> Any:
    matches = [item for item in node.outputs if item.port == port]
    if len(matches) != 1 or len(node.outputs) != 1:
        raise ValueError(f"Node {node.ref} requires exactly one {port} output.")
    return matches[0]


def _validate_json_serialize_shape(
    node: MetaPlannerIRNode | GraphIntentNodeV3,
    _parsed: BaseModel,
) -> None:
    _require_single_input(node, "value")
    _require_single_output(node, "json")


def _validate_json_deserialize_shape(
    node: MetaPlannerIRNode | GraphIntentNodeV3,
    _parsed: BaseModel,
) -> None:
    _require_single_input(node, "json")
    _require_single_output(node, "value")


def _validate_variable_aggregator_shape(
    node: MetaPlannerIRNode | GraphIntentNodeV3,
    parsed: BaseModel,
) -> None:
    config = VariableAggregatorPlannerConfig.model_validate(parsed)
    inputs = _input_bindings(node, "values")
    if not inputs or len(inputs) != len(node.inputs):
        raise ValueError(f"Node {node.ref} accepts only values inputs.")
    if len(inputs) != len(config.output_fields):
        raise ValueError(
            f"Node {node.ref} output_fields must map one-to-one to values inputs."
        )
    _require_single_output(node, "result")


def _validate_data_aggregate_shape(
    node: MetaPlannerIRNode | GraphIntentNodeV3,
    _parsed: BaseModel,
) -> None:
    _require_single_input(node, "rows")
    _require_single_output(node, "result")


def _validate_dataset_compare_shape(
    node: MetaPlannerIRNode | GraphIntentNodeV3,
    _parsed: BaseModel,
) -> None:
    _require_single_input(node, "left")
    _require_single_input(node, "right")
    if len(node.inputs) != 2:
        raise ValueError(f"Node {node.ref} accepts only left and right inputs.")
    _require_single_output(node, "result")


def _validate_router_shape(
    node: MetaPlannerIRNode | GraphIntentNodeV3,
    _parsed: BaseModel,
) -> None:
    _require_single_input(node, "value")
    if node.outputs:
        raise ValueError(f"Node {node.ref} cannot declare data outputs.")


def _validate_terminate_shape(
    node: MetaPlannerIRNode | GraphIntentNodeV3,
    _parsed: BaseModel,
) -> None:
    if node.inputs or node.outputs:
        raise ValueError(f"Node {node.ref} cannot declare data ports.")


def _validate_data_merge_shape(
    node: MetaPlannerIRNode | GraphIntentNodeV3,
    _parsed: BaseModel,
) -> None:
    _require_single_input(node, "left")
    _require_single_input(node, "right")
    if len(node.inputs) != 2:
        raise ValueError(f"Node {node.ref} accepts only left and right inputs.")
    _require_single_output(node, "result")


def _validate_knowledge_retrieval_shape(
    node: MetaPlannerIRNode | GraphIntentNodeV3,
    _parsed: BaseModel,
) -> None:
    _require_single_input(node, "query")
    if len(node.inputs) != 1:
        raise ValueError(f"Node {node.ref} accepts only one query input.")
    _require_single_output(node, "result")


def _table_predicates(
    item: DataTableQueryFilterPlannerConfig
    | DataTableQueryPredicatePlannerConfig
    | None,
) -> list[DataTableQueryPredicatePlannerConfig]:
    if item is None:
        return []
    if isinstance(item, DataTableQueryPredicatePlannerConfig):
        return [item]
    return [
        predicate
        for child in item.items
        for predicate in _table_predicates(child)
    ]


def _validate_data_table_query_shape(
    node: MetaPlannerIRNode | GraphIntentNodeV3,
    parsed: BaseModel,
) -> None:
    config = DataTableQueryPlannerConfig.model_validate(parsed)
    expected = {
        f"predicate_{item.ref}"
        for item in _table_predicates(config.filter)
        if item.value_source == "input"
    }
    actual = [item.port for item in node.inputs]
    if set(actual) != expected or len(actual) != len(expected):
        raise ValueError(
            f"Node {node.ref} dynamic predicate inputs must exactly match "
            "the configured predicate refs."
        )
    _require_single_output(node, "result")


def _field_value_schema(data_type: str, *, required: bool = True) -> WorkflowValueSchema:
    value_type = {
        "string": "string",
        "integer": "integer",
        "number": "number",
        "boolean": "boolean",
        "datetime": "string",
        "json": "any",
    }.get(data_type)
    if value_type is None:
        raise ValueError(f"Unsupported Agent Table field type {data_type}.")
    return WorkflowValueSchema(type=value_type, nullable=not required)


def _table_snapshot_fields(
    resource_snapshot: dict[str, Any],
) -> dict[str, WorkflowValueSchema]:
    fields = resource_snapshot.get("fields")
    if not isinstance(fields, dict):
        raise ValueError("Agent Table resource snapshot has no trusted field schema.")
    return {
        str(name): WorkflowValueSchema.model_validate(schema)
        for name, schema in fields.items()
    }


def _validate_data_table_resource(
    node: GraphIntentNodeV3,
    parsed: BaseModel,
    resource_snapshot: dict[str, Any],
) -> None:
    config = DataTableQueryPlannerConfig.model_validate(parsed)
    fields = _table_snapshot_fields(resource_snapshot)
    business_fields = {
        name for name in fields if name not in {
            "record_id", "created_at", "updated_at", "revision"
        }
    }
    unknown_selected = sorted(set(config.select_fields) - business_fields)
    unknown_sort = sorted({item.field for item in config.sort} - set(fields))
    if unknown_selected:
        raise ValueError(
            "Agent Table query selects unknown fields: "
            + ", ".join(unknown_selected)
        )
    if unknown_sort:
        raise ValueError(
            "Agent Table query sorts unknown fields: " + ", ".join(unknown_sort)
        )
    inputs = {item.port: item for item in node.inputs}
    for predicate in _table_predicates(config.filter):
        field_schema = fields.get(predicate.field)
        if field_schema is None:
            raise ValueError(
                f"Agent Table predicate {predicate.ref} uses unknown field "
                f"{predicate.field}."
            )
        if predicate.operator == "contains" and field_schema.type != "string":
            raise ValueError(
                f"Agent Table predicate {predicate.ref} contains requires a string field."
            )
        expected_schema = (
            WorkflowValueSchema(type="array", items=field_schema.model_copy(
                update={"nullable": False}
            ))
            if predicate.operator == "in"
            else field_schema.model_copy(update={"nullable": False})
        )
        if predicate.value_source == "input":
            binding = inputs[f"predicate_{predicate.ref}"]
            if canonical_checksum(
                binding.value_schema.model_dump(mode="json")
            ) != canonical_checksum(expected_schema.model_dump(mode="json")):
                raise ValueError(
                    f"Agent Table predicate {predicate.ref} input type does not "
                    "match its fixed SchemaVersion."
                )
        elif predicate.value_source == "literal":
            expected_schema.assert_value(
                predicate.value,
                path=f"$.filter.{predicate.ref}.value",
            )


def _knowledge_output_schema(
    port: str,
    parsed: BaseModel,
    _resource_snapshot: dict[str, Any] | None,
) -> WorkflowValueSchema:
    if port != "result":
        raise ValueError(f"Knowledge retrieval has no output port {port}.")
    config = KnowledgeRetrievalPlannerConfig.model_validate(parsed)
    if config.return_mode == "context":
        return WorkflowValueSchema(type="string")
    return WorkflowValueSchema(
        type="object",
        properties={
            "knowledge_base_id": WorkflowValueSchema(type="string"),
            "version_id": WorkflowValueSchema(type="string"),
            "context": WorkflowValueSchema(type="string"),
            "context_truncated": WorkflowValueSchema(type="boolean"),
            "sources": WorkflowValueSchema(
                type="array", items=WorkflowValueSchema(type="object")
            ),
            "citations": WorkflowValueSchema(
                type="array", items=WorkflowValueSchema(type="object")
            ),
            "citation_count": WorkflowValueSchema(type="integer"),
            "retrieval": WorkflowValueSchema(type="object"),
            "warnings": WorkflowValueSchema(
                type="array", items=WorkflowValueSchema(type="string")
            ),
        },
        required=(
            "knowledge_base_id",
            "version_id",
            "context",
            "sources",
            "citations",
            "citation_count",
            "retrieval",
            "warnings",
        ),
    )


def _data_table_output_schema(
    port: str,
    parsed: BaseModel,
    resource_snapshot: dict[str, Any] | None,
) -> WorkflowValueSchema:
    if port != "result":
        raise ValueError(f"Agent Table query has no output port {port}.")
    if resource_snapshot is None:
        contract = workflow_node_contract_registry.require("data_table_query")
        return next(
            item.value_schema
            for item in contract.ports
            if item.direction == "output" and item.name == port
        )
    config = DataTableQueryPlannerConfig.model_validate(parsed)
    fields = _table_snapshot_fields(resource_snapshot)
    selected = config.select_fields or [
        name
        for name in fields
        if name not in {"record_id", "created_at", "updated_at", "revision"}
    ]
    selected_set = {
        "record_id", "created_at", "updated_at", "revision", *selected
    }
    properties = {
        name: schema for name, schema in fields.items() if name in selected_set
    }
    required = tuple(
        name
        for name in ("record_id", "created_at", "updated_at", "revision", *selected)
        if name in properties and not properties[name].nullable
    )
    item_schema = WorkflowValueSchema(
        type="object",
        properties=properties,
        required=required,
    )
    if config.return_mode == "first":
        return item_schema.model_copy(update={"nullable": True})
    return WorkflowValueSchema(type="array", items=item_schema)


def _workflow_agent_references(parsed: BaseModel) -> set[str]:
    config = MetaPlannerWorkflowAgentConfig.model_validate(parsed)
    referenced: set[str] = set()
    for template in (config.role_prompt, config.task_input):
        for match in re.finditer(r"\{\{\s*(.*?)\s*\}\}", template, re.DOTALL):
            expression = match.group(1).strip()
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", expression):
                raise ValueError(
                    "Template contains an unsupported template expression."
                )
            referenced.add(expression)
    return referenced


def _workflow_agent_config_from_native(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "role_prompt": str(data.get("rolePrompt") or "").strip(),
        "task_input": str(data.get("taskInput") or "").strip(),
        "model_id": str(data.get("modelId") or "").strip() or None,
        "source_agent_id": str(data.get("sourceAgentId") or "").strip() or None,
        "method_skill_ids": (
            list(data.get("methodSkillIds") or [])
            if isinstance(data.get("methodSkillIds"), list)
            else []
        ),
    }


def _workflow_agent_native_inputs(
    _data: dict[str, Any], parsed: BaseModel
) -> list[tuple[str, str]]:
    return [
        ("task", variable)
        for variable in sorted(_workflow_agent_references(parsed))
    ]


def _single_native_input(field: str, port: str) -> Callable[
    [dict[str, Any], BaseModel], list[tuple[str, str]]
]:
    def project(data: dict[str, Any], _parsed: BaseModel) -> list[tuple[str, str]]:
        return [(port, str(data.get(field) or "").strip())]

    return project


def _single_native_output(field: str, port: str) -> Callable[
    [dict[str, Any], BaseModel], dict[str, str]
]:
    def project(data: dict[str, Any], _parsed: BaseModel) -> dict[str, str]:
        return {port: str(data.get(field) or "").strip()}

    return project


def _variable_aggregator_native_inputs(
    data: dict[str, Any], _parsed: BaseModel
) -> list[tuple[str, str]]:
    bindings = data.get("bindings")
    if not isinstance(bindings, list):
        raise ValueError("Planner variable pack node is missing bindings.")
    return [
        ("values", str(item.get("sourceVariable") or "").strip())
        for item in bindings
        if isinstance(item, dict)
    ]


def _dataset_compare_native_inputs(
    data: dict[str, Any], _parsed: BaseModel
) -> list[tuple[str, str]]:
    return [
        ("left", str(data.get("leftVariable") or "").strip()),
        ("right", str(data.get("rightVariable") or "").strip()),
    ]


def _data_table_filter_inputs_from_native(
    configured: DataTableQueryFilterPlannerConfig
    | DataTableQueryPredicatePlannerConfig
    | None,
    native: Any,
) -> list[tuple[str, str]]:
    if configured is None:
        if native not in (None, {}):
            raise ValueError("Planner Agent Table filter has drifted.")
        return []
    if not isinstance(native, dict):
        raise ValueError("Planner Agent Table filter is missing.")
    if isinstance(configured, DataTableQueryFilterPlannerConfig):
        if set(native) != {"logic", "items"}:
            raise ValueError("Planner Agent Table filter group has drifted.")
        items = native.get("items")
        if native.get("logic") != configured.logic or not isinstance(items, list):
            raise ValueError("Planner Agent Table filter group has drifted.")
        if len(items) != len(configured.items):
            raise ValueError("Planner Agent Table filter item count has drifted.")
        return [
            binding
            for child, native_child in zip(configured.items, items, strict=True)
            for binding in _data_table_filter_inputs_from_native(
                child, native_child
            )
        ]
    expected_keys = {"field", "operator"}
    if configured.operator != "is_null":
        expected_keys.add("value")
    if set(native) != expected_keys:
        raise ValueError(
            f"Planner Agent Table predicate {configured.ref} has drifted."
        )
    if (
        native.get("field") != configured.field
        or native.get("operator") != configured.operator
    ):
        raise ValueError(
            f"Planner Agent Table predicate {configured.ref} has drifted."
        )
    if configured.operator == "is_null":
        return []
    binding = native.get("value")
    if not isinstance(binding, dict):
        raise ValueError(
            f"Planner Agent Table predicate {configured.ref} has no value binding."
        )
    if configured.value_source == "literal":
        expected = {"source": "literal", "value": configured.value}
        if canonical_checksum(binding) != canonical_checksum(expected):
            raise ValueError(
                f"Planner Agent Table predicate {configured.ref} literal has drifted."
            )
        return []
    variable = str(binding.get("variable") or "").strip()
    if set(binding) != {"source", "variable"} or binding.get("source") != "variable":
        raise ValueError(
            f"Planner Agent Table predicate {configured.ref} binding has drifted."
        )
    if not variable:
        raise ValueError(
            f"Planner Agent Table predicate {configured.ref} variable is missing."
        )
    return [(f"predicate_{configured.ref}", variable)]


def _data_table_query_native_inputs(
    data: dict[str, Any], parsed: BaseModel
) -> list[tuple[str, str]]:
    config = DataTableQueryPlannerConfig.model_validate(parsed)
    return _data_table_filter_inputs_from_native(config.filter, data.get("filter"))


def _no_native_inputs(
    _data: dict[str, Any], _parsed: BaseModel
) -> list[tuple[str, str]]:
    return []


def _no_native_outputs(
    _data: dict[str, Any], _parsed: BaseModel
) -> dict[str, str]:
    return {}


def _data_merge_native_inputs(
    data: dict[str, Any], _parsed: BaseModel
) -> list[tuple[str, str]]:
    return [
        ("left", str(data.get("leftVariable") or "").strip()),
        ("right", str(data.get("rightVariable") or "").strip()),
    ]


def _json_deserialize_output_schema(
    port: str,
    parsed: BaseModel,
    _resource_snapshot: dict[str, Any] | None,
) -> WorkflowValueSchema:
    if port != "value":
        raise ValueError(f"JSON deserialize has no output port {port}.")
    return JsonDeserializePlannerConfig.model_validate(parsed).expected_schema


def _planner_metadata(node: MetaPlannerIRNode, *, kind: str) -> dict[str, Any]:
    contract = workflow_node_contract_registry.require(kind)
    return {
        "plannerContractVersion": NODE_CONTRACT_VERSION,
        "plannerCompilerChecksum": contract.compiler_checksum,
        "plannerRef": node.ref,
        "plannerTaskIds": list(node.task_ids),
        "plannerInputs": [item.model_dump(mode="json") for item in node.inputs],
        "plannerOutputs": [item.model_dump(mode="json") for item in node.outputs],
    }


def _base_pure_node_data(node: MetaPlannerIRNode) -> dict[str, Any]:
    return {
        "kind": node.kind,
        "title": node.title,
        "description": node.description,
        "plannerOutcomeMapV1": {"success": ""},
        **_planner_metadata(node, kind=node.kind),
    }


def _compile_json_serialize(
    node: MetaPlannerIRNode,
    parsed: BaseModel,
    context: PlannerNodeCompileContext,
) -> NativeWorkflowNode:
    config = JsonSerializePlannerConfig.model_validate(parsed)
    source = _require_single_input(node, "value")
    output = _require_single_output(node, "json")
    return NativeWorkflowNode(
        id=context.node_id,
        type="json_serialize",
        position=context.position,
        data={
            **_base_pure_node_data(node),
            "contractVersion": 2,
            "inputVariable": source.variable,
            "outputVariable": output.variable,
            "format": config.format,
        },
    )


def _compile_json_deserialize(
    node: MetaPlannerIRNode,
    parsed: BaseModel,
    context: PlannerNodeCompileContext,
) -> NativeWorkflowNode:
    config = JsonDeserializePlannerConfig.model_validate(parsed)
    source = _require_single_input(node, "json")
    output = _require_single_output(node, "value")
    return NativeWorkflowNode(
        id=context.node_id,
        type="json_deserialize",
        position=context.position,
        data={
            **_base_pure_node_data(node),
            "contractVersion": 2,
            "inputVariable": source.variable,
            "outputVariable": output.variable,
            "expectedSchema": config.expected_schema.model_dump(mode="json"),
        },
    )


def _compile_variable_aggregator(
    node: MetaPlannerIRNode,
    parsed: BaseModel,
    context: PlannerNodeCompileContext,
) -> NativeWorkflowNode:
    config = VariableAggregatorPlannerConfig.model_validate(parsed)
    inputs = _input_bindings(node, "values")
    output = _require_single_output(node, "result")
    bindings = []
    for index, (source, output_field) in enumerate(
        zip(inputs, config.output_fields, strict=True)
    ):
        binding_checksum = canonical_checksum(
            {"ref": node.ref, "index": index, "field": output_field}
        )
        binding_id = f"binding_{binding_checksum[:16]}"
        bindings.append(
            {
                "id": binding_id,
                "sourceVariable": source.variable,
                "outputField": output_field,
            }
        )
    return NativeWorkflowNode(
        id=context.node_id,
        type="variable_aggregator",
        position=context.position,
        data={
            **_base_pure_node_data(node),
            "contractVersion": 2,
            "bindings": bindings,
            "outputVariable": output.variable,
        },
    )


def _compile_data_aggregate(
    node: MetaPlannerIRNode,
    parsed: BaseModel,
    context: PlannerNodeCompileContext,
) -> NativeWorkflowNode:
    config = DataAggregatePlannerConfig.model_validate(parsed)
    source = _require_single_input(node, "rows")
    output = _require_single_output(node, "result")
    return NativeWorkflowNode(
        id=context.node_id,
        type="data_aggregate",
        position=context.position,
        data={
            **_base_pure_node_data(node),
            "inputVariable": source.variable,
            "outputVariable": output.variable,
            "groupByFields": list(config.group_by_fields),
            "measures": [
                {
                    "outputField": item.output_field,
                    "operation": item.operation,
                    "sourceField": item.source_field,
                }
                for item in config.measures
            ],
        },
    )


def _compile_dataset_compare(
    node: MetaPlannerIRNode,
    parsed: BaseModel,
    context: PlannerNodeCompileContext,
) -> NativeWorkflowNode:
    config = DatasetComparePlannerConfig.model_validate(parsed)
    left = _require_single_input(node, "left")
    right = _require_single_input(node, "right")
    output = _require_single_output(node, "result")
    return NativeWorkflowNode(
        id=context.node_id,
        type="dataset_compare",
        position=context.position,
        data={
            **_base_pure_node_data(node),
            "leftVariable": left.variable,
            "rightVariable": right.variable,
            "keyFields": list(config.key_fields),
            "includeUnchanged": config.include_unchanged,
            "outputVariable": output.variable,
        },
    )


def _compile_condition(
    node: MetaPlannerIRNode,
    parsed: BaseModel,
    context: PlannerNodeCompileContext,
) -> NativeWorkflowNode:
    config = ConditionPlannerConfig.model_validate(parsed)
    source = _require_single_input(node, "value")
    data: dict[str, Any] = {
        **_base_pure_node_data(node),
        "contractVersion": 2,
        "inputVariable": source.variable,
        "field": config.field,
        "operator": config.operator,
        "valueType": config.value_type,
        "plannerOutcomeMapV1": {"matched": "true", "unmatched": "false"},
    }
    if config.operator != "is_null":
        data["value"] = config.value
    return NativeWorkflowNode(
        id=context.node_id,
        type="condition",
        position=context.position,
        data=data,
    )


def _compile_multi_route(
    node: MetaPlannerIRNode,
    parsed: BaseModel,
    context: PlannerNodeCompileContext,
) -> NativeWorkflowNode:
    config = MultiRoutePlannerConfig.model_validate(parsed)
    source = _require_single_input(node, "value")
    routes: list[dict[str, Any]] = []
    outcome_map: dict[str, str] = {}
    for index, rule in enumerate(config.routes, start=1):
        native_id = f"route_{index}"
        outcome_map[f"case_{index}"] = native_id
        item: dict[str, Any] = {
            "id": native_id,
            "label": rule.label,
            "operator": rule.operator,
            "valueType": rule.value_type,
        }
        if rule.operator != "is_null":
            item["value"] = rule.value
        routes.append(item)
    outcome_map["default"] = "default"
    return NativeWorkflowNode(
        id=context.node_id,
        type="multi_route",
        position=context.position,
        data={
            **_base_pure_node_data(node),
            "inputVariable": source.variable,
            "routes": routes,
            "plannerOutcomeMapV1": outcome_map,
        },
    )


def _compile_data_merge(
    node: MetaPlannerIRNode,
    parsed: BaseModel,
    context: PlannerNodeCompileContext,
) -> NativeWorkflowNode:
    config = DataMergePlannerConfig.model_validate(parsed)
    left = _require_single_input(node, "left")
    right = _require_single_input(node, "right")
    output = _require_single_output(node, "result")
    return NativeWorkflowNode(
        id=context.node_id,
        type="data_merge",
        position=context.position,
        data={
            **_base_pure_node_data(node),
            "contractVersion": 1,
            "mergeMode": config.merge_mode,
            "leftVariable": left.variable,
            "rightVariable": right.variable,
            "outputVariable": output.variable,
            "keyFields": list(config.key_fields),
            "plannerOutcomeMapV1": {"success": ""},
        },
    )


def _resource_compile_snapshot(
    context: PlannerNodeCompileContext,
    *,
    kind: str,
) -> dict[str, Any]:
    snapshot = context.resource_snapshot
    if not isinstance(snapshot, dict) or snapshot.get("kind") != kind:
        raise ValueError(f"Planner compiler requires a trusted {kind} snapshot.")
    return snapshot


def _planner_error_variable(node: MetaPlannerIRNode) -> str:
    return f"planner_error_{canonical_checksum({'ref': node.ref})[:16]}"


def _compile_knowledge_retrieval(
    node: MetaPlannerIRNode,
    parsed: BaseModel,
    context: PlannerNodeCompileContext,
) -> NativeWorkflowNode:
    config = KnowledgeRetrievalPlannerConfig.model_validate(parsed)
    source = _require_single_input(node, "query")
    output = _require_single_output(node, "result")
    resource = _resource_compile_snapshot(context, kind="knowledge_base")
    outcome_map = (
        {"success": "", "error": "error"}
        if config.failure_action == "error_output"
        else {"success": ""}
    )
    return NativeWorkflowNode(
        id=context.node_id,
        type="knowledge_retrieval",
        position=context.position,
        data={
            **_base_pure_node_data(node),
            "plannerOutcomeMapV1": {"success": ""},
            **(
                {"plannerOutcomeMapV2": outcome_map}
                if len(outcome_map) > 1
                else {}
            ),
            "plannerAdapterConfigV1": config.model_dump(mode="json"),
            "plannerResourceSnapshotChecksum": resource["snapshot_checksum"],
            "contractVersion": 2,
            "knowledgeBaseId": resource["resource_id"],
            "observedActiveVersionId": resource.get("observed_version_id"),
            "queryVariable": source.variable,
            "top_k": config.top_k,
            "returnMode": config.return_mode,
            "outputVariable": output.variable,
            "failureAction": config.failure_action,
            **(
                {"errorVariable": _planner_error_variable(node)}
                if config.failure_action == "error_output"
                else {}
            ),
            "retryMode": config.retry_mode,
            "maxAttempts": config.max_attempts,
        },
    )


def _compile_data_table_filter(
    item: DataTableQueryFilterPlannerConfig | DataTableQueryPredicatePlannerConfig,
    node: MetaPlannerIRNode,
) -> dict[str, Any]:
    if isinstance(item, DataTableQueryFilterPlannerConfig):
        return {
            "logic": item.logic,
            "items": [
                _compile_data_table_filter(child, node) for child in item.items
            ],
        }
    payload: dict[str, Any] = {
        "field": item.field,
        "operator": item.operator,
    }
    if item.operator == "is_null":
        return payload
    if item.value_source == "literal":
        payload["value"] = {"source": "literal", "value": item.value}
        return payload
    binding = _require_single_input(node, f"predicate_{item.ref}")
    payload["value"] = {
        "source": "variable",
        "variable": binding.variable,
    }
    return payload


def _compile_data_table_query(
    node: MetaPlannerIRNode,
    parsed: BaseModel,
    context: PlannerNodeCompileContext,
) -> NativeWorkflowNode:
    config = DataTableQueryPlannerConfig.model_validate(parsed)
    output = _require_single_output(node, "result")
    resource = _resource_compile_snapshot(context, kind="data_table")
    filter_tree = (
        _compile_data_table_filter(config.filter, node)
        if config.filter is not None
        else None
    )
    outcome_map = (
        {"success": "", "error": "error"}
        if config.failure_action == "error_output"
        else {"success": ""}
    )
    return NativeWorkflowNode(
        id=context.node_id,
        type="data_table_query",
        position=context.position,
        data={
            **_base_pure_node_data(node),
            "plannerOutcomeMapV1": {"success": ""},
            **(
                {"plannerOutcomeMapV2": outcome_map}
                if len(outcome_map) > 1
                else {}
            ),
            "plannerAdapterConfigV1": config.model_dump(mode="json"),
            "plannerResourceSnapshotChecksum": resource["snapshot_checksum"],
            "tableId": resource["resource_id"],
            "versionPolicy": "pinned",
            "pinnedSchemaVersion": resource["pinned_schema_version"],
            "pinnedSchemaChecksum": resource["schema_checksum"],
            "selectFields": list(config.select_fields),
            "filter": filter_tree,
            "sort": [
                {"field": item.field, "direction": item.direction}
                for item in config.sort
            ],
            "limit": config.limit,
            "returnMode": config.return_mode,
            "outputVariable": output.variable,
            "failureAction": config.failure_action,
            **(
                {"errorVariable": _planner_error_variable(node)}
                if config.failure_action == "error_output"
                else {}
            ),
            "retryMode": config.retry_mode,
            "maxAttempts": config.max_attempts,
        },
    )


def _compile_terminate_error(
    node: MetaPlannerIRNode,
    parsed: BaseModel,
    context: PlannerNodeCompileContext,
) -> NativeWorkflowNode:
    config = TerminateErrorPlannerConfig.model_validate(parsed)
    return NativeWorkflowNode(
        id=context.node_id,
        type="terminate_error",
        position=context.position,
        data={
            **_base_pure_node_data(node),
            "errorCode": config.error_code,
            "message": config.message,
            "plannerOutcomeMapV1": {},
        },
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
            "plannerOutcomeMapV1": {"success": ""},
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


def _knowledge_editor_config_from_native(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "top_k": int(data.get("top_k") or 0),
        "return_mode": str(data.get("returnMode") or ""),
        "failure_action": str(data.get("failureAction") or "stop"),
        "retry_mode": str(data.get("retryMode") or "none"),
        "max_attempts": int(data.get("maxAttempts") or 2),
    }


def _table_editor_filter_from_native(
    raw: Any,
    *,
    path: tuple[int, ...] = (),
) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("Planner Agent Table filter must be an object.")
    if "items" in raw:
        items = raw.get("items")
        if not isinstance(items, list):
            raise ValueError("Planner Agent Table filter group must contain items.")
        return {
            "kind": "group",
            "logic": str(raw.get("logic") or "and"),
            "items": [
                _table_editor_filter_from_native(item, path=(*path, index))
                for index, item in enumerate(items)
            ],
        }
    field = str(raw.get("field") or "")
    operator = str(raw.get("operator") or "")
    ref = "p_" + canonical_checksum(
        {"path": path, "field": field, "operator": operator}
    )[:12]
    result: dict[str, Any] = {
        "kind": "predicate",
        "ref": ref,
        "field": field,
        "operator": operator,
    }
    if operator == "is_null":
        result["value_source"] = "none"
        return result
    value = raw.get("value")
    if not isinstance(value, dict):
        raise ValueError("Planner Agent Table predicate value is invalid.")
    source = str(value.get("source") or "")
    if source == "literal":
        result.update({"value_source": "literal", "value": value.get("value")})
        return result
    if source == "variable" and str(value.get("variable") or "").strip():
        result.update({"value_source": "input", "value": None})
        return result
    raise ValueError("Planner Agent Table predicate source is invalid.")


def _data_table_editor_config_from_native(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "select_fields": list(data.get("selectFields") or []),
        "filter": _table_editor_filter_from_native(data.get("filter")),
        "sort": list(data.get("sort") or []),
        "limit": int(data.get("limit") or 0),
        "return_mode": str(data.get("returnMode") or ""),
        "failure_action": str(data.get("failureAction") or "stop"),
        "retry_mode": str(data.get("retryMode") or "none"),
        "max_attempts": int(data.get("maxAttempts") or 2),
    }


def _pure_config_from_native(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    if kind == "knowledge_retrieval":
        payload = data.get("plannerAdapterConfigV1")
        if not isinstance(payload, dict):
            raise ValueError("Planner knowledge retrieval is missing Adapter config.")
        config = KnowledgeRetrievalPlannerConfig.model_validate(payload)
        expected = {
            "top_k": int(data.get("top_k") or 0),
            "return_mode": str(data.get("returnMode") or ""),
            "failure_action": str(data.get("failureAction") or "stop"),
            "retry_mode": str(data.get("retryMode") or "none"),
            "max_attempts": int(data.get("maxAttempts") or 2),
        }
        if config.model_dump(mode="json") != expected:
            raise ValueError("Planner knowledge retrieval native config has drifted.")
        return config.model_dump(mode="json")
    if kind == "data_table_query":
        payload = data.get("plannerAdapterConfigV1")
        if not isinstance(payload, dict):
            raise ValueError("Planner Agent Table query is missing Adapter config.")
        config = DataTableQueryPlannerConfig.model_validate(payload)
        expected_scalars = {
            "select_fields": list(data.get("selectFields") or []),
            "sort": list(data.get("sort") or []),
            "limit": int(data.get("limit") or 0),
            "return_mode": str(data.get("returnMode") or ""),
            "failure_action": str(data.get("failureAction") or "stop"),
            "retry_mode": str(data.get("retryMode") or "none"),
            "max_attempts": int(data.get("maxAttempts") or 2),
        }
        actual = config.model_dump(mode="json")
        if any(actual[key] != value for key, value in expected_scalars.items()):
            raise ValueError("Planner Agent Table native config has drifted.")
        _data_table_filter_inputs_from_native(config.filter, data.get("filter"))
        return actual
    if kind == "condition":
        if int(data.get("contractVersion") or 0) != 2:
            raise ValueError("Planner condition nodes require contractVersion 2.")
        payload: dict[str, Any] = {
            "field": str(data.get("field") or ""),
            "operator": str(data.get("operator") or ""),
            "value_type": str(data.get("valueType") or "null"),
        }
        if payload["operator"] != "is_null":
            if "value" not in data:
                raise ValueError("Planner condition node is missing value.")
            payload["value"] = data.get("value")
        return payload
    if kind == "multi_route":
        routes = data.get("routes")
        if not isinstance(routes, list):
            raise ValueError("Planner multi route node is missing routes.")
        restored = []
        for index, item in enumerate(routes, start=1):
            if not isinstance(item, dict) or item.get("id") != f"route_{index}":
                raise ValueError("Planner multi route ids are not compiler-owned.")
            rule: dict[str, Any] = {
                "label": str(item.get("label") or ""),
                "operator": str(item.get("operator") or ""),
                "value_type": str(item.get("valueType") or "null"),
            }
            if rule["operator"] != "is_null":
                if "value" not in item:
                    raise ValueError("Planner multi route rule is missing value.")
                rule["value"] = item.get("value")
            restored.append(rule)
        return {"routes": restored}
    if kind == "data_merge":
        if int(data.get("contractVersion") or 0) != 1:
            raise ValueError("Planner data merge nodes require contractVersion 1.")
        return {
            "merge_mode": str(data.get("mergeMode") or ""),
            "key_fields": list(data.get("keyFields") or []),
        }
    if kind == "terminate_error":
        return {
            "error_code": str(data.get("errorCode") or ""),
            "message": str(data.get("message") or ""),
        }
    if kind == "json_serialize":
        if int(data.get("contractVersion") or 0) != 2:
            raise ValueError("Planner JSON serialize nodes require contractVersion 2.")
        return {"format": str(data.get("format") or "")}
    if kind == "json_deserialize":
        if int(data.get("contractVersion") or 0) != 2:
            raise ValueError("Planner JSON deserialize nodes require contractVersion 2.")
        return {"expected_schema": data.get("expectedSchema")}
    if kind == "variable_aggregator":
        if int(data.get("contractVersion") or 0) != 2:
            raise ValueError("Planner variable pack nodes require contractVersion 2.")
        bindings = data.get("bindings")
        if not isinstance(bindings, list):
            raise ValueError("Planner variable pack node is missing bindings.")
        return {
            "output_fields": [
                str(item.get("outputField") or "")
                for item in bindings
                if isinstance(item, dict)
            ]
        }
    if kind == "data_aggregate":
        measures = data.get("measures")
        if not isinstance(measures, list):
            raise ValueError("Planner data aggregate node is missing measures.")
        return {
            "group_by_fields": list(data.get("groupByFields") or []),
            "measures": [
                {
                    "output_field": str(item.get("outputField") or ""),
                    "operation": str(item.get("operation") or ""),
                    "source_field": str(item.get("sourceField") or ""),
                }
                for item in measures
                if isinstance(item, dict)
            ],
        }
    if kind == "dataset_compare":
        return {
            "key_fields": list(data.get("keyFields") or []),
            "include_unchanged": bool(data.get("includeUnchanged", False)),
        }
    raise ValueError(f"Node kind {kind} is not a pure Planner node.")


def _resource_ref_from_native(kind: str, data: dict[str, Any]) -> dict[str, str]:
    field = {
        "knowledge_retrieval": "knowledgeBaseId",
        "data_table_query": "tableId",
    }.get(kind)
    if field is None:
        raise ValueError(f"Node kind {kind} has no node-owned resource.")
    resource_id = str(data.get(field) or "").strip()
    if not resource_id:
        raise ValueError(f"Planner {kind} is missing its resource ID.")
    if not str(data.get("plannerResourceSnapshotChecksum") or "").strip():
        raise ValueError(f"Planner {kind} is missing its resource snapshot marker.")
    return {"resource_id": resource_id}


def _decompile_pure_node(
    node: NativeWorkflowNode,
    *,
    kind: str,
    graph_ir_v3: bool,
) -> MetaPlannerIRNode | GraphIntentNodeV3:
    data = node.data if isinstance(node.data, dict) else {}
    contract = workflow_node_contract_registry.require(kind)
    if int(data.get("plannerContractVersion") or 0) != NODE_CONTRACT_VERSION:
        raise ValueError(f"{kind} does not carry a NodeContract V3 marker.")
    if str(data.get("plannerCompilerChecksum") or "") != contract.compiler_checksum:
        raise ValueError(f"{kind} compiler contract has drifted.")
    if graph_ir_v3 and int(data.get("plannerIRVersion") or 0) != META_PLANNER_IR_VERSION:
        raise ValueError(f"{kind} does not carry Graph IR V3 metadata.")
    node_ref = str(data.get("plannerRef") or "").strip()
    task_ids = data.get("plannerTaskIds")
    inputs = data.get("plannerInputsV3" if graph_ir_v3 else "plannerInputs")
    outputs = data.get("plannerOutputsV3" if graph_ir_v3 else "plannerOutputs")
    if not node_ref or not isinstance(task_ids, list):
        raise ValueError(f"{kind} is missing planner round-trip metadata.")
    if not isinstance(inputs, list) or not isinstance(outputs, list):
        raise ValueError(f"{kind} is missing planner port metadata.")
    payload = {
        "ref": node_ref,
        "kind": kind,
        "title": str(data.get("title") or node_ref),
        "description": str(data.get("description") or ""),
        "task_ids": task_ids,
        "inputs": inputs,
        "outputs": outputs,
        "config": _pure_config_from_native(kind, data),
    }
    if kind in {"knowledge_retrieval", "data_table_query"}:
        payload["resource_ref"] = _resource_ref_from_native(kind, data)
    model: type[MetaPlannerIRNode] | type[GraphIntentNodeV3] = (
        GraphIntentNodeV3 if graph_ir_v3 else MetaPlannerIRNode
    )
    return model.model_validate(payload)


def _pure_decompilers(
    kind: str,
) -> tuple[
    Callable[[NativeWorkflowNode], MetaPlannerIRNode],
    Callable[[NativeWorkflowNode], GraphIntentNodeV3],
]:
    def legacy(node: NativeWorkflowNode) -> MetaPlannerIRNode:
        restored = _decompile_pure_node(node, kind=kind, graph_ir_v3=False)
        assert isinstance(restored, MetaPlannerIRNode)
        return restored

    def v3(node: NativeWorkflowNode) -> GraphIntentNodeV3:
        restored = _decompile_pure_node(node, kind=kind, graph_ir_v3=True)
        assert isinstance(restored, GraphIntentNodeV3)
        return restored

    return legacy, v3


_decompile_json_serialize, _decompile_json_serialize_v3 = _pure_decompilers(
    "json_serialize"
)
_decompile_json_deserialize, _decompile_json_deserialize_v3 = _pure_decompilers(
    "json_deserialize"
)
_decompile_variable_aggregator, _decompile_variable_aggregator_v3 = (
    _pure_decompilers("variable_aggregator")
)
_decompile_data_aggregate, _decompile_data_aggregate_v3 = _pure_decompilers(
    "data_aggregate"
)
_decompile_dataset_compare, _decompile_dataset_compare_v3 = _pure_decompilers(
    "dataset_compare"
)
_decompile_condition, _decompile_condition_v3 = _pure_decompilers("condition")
_decompile_multi_route, _decompile_multi_route_v3 = _pure_decompilers(
    "multi_route"
)
_decompile_data_merge, _decompile_data_merge_v3 = _pure_decompilers("data_merge")
_decompile_terminate_error, _decompile_terminate_error_v3 = _pure_decompilers(
    "terminate_error"
)
_decompile_knowledge_retrieval, _decompile_knowledge_retrieval_v3 = (
    _pure_decompilers("knowledge_retrieval")
)
_decompile_data_table_query, _decompile_data_table_query_v3 = _pure_decompilers(
    "data_table_query"
)


PLANNER_NODE_ADAPTERS: dict[str, PlannerNodeAdapter] = {
    "workflow_agent": PlannerNodeAdapter(
        kind="workflow_agent",
        config_model=MetaPlannerWorkflowAgentConfig,
        compile_node=_compile_workflow_agent,
        decompile_node=_decompile_workflow_agent,
        decompile_node_v3=_decompile_workflow_agent_v3,
        referenced_variables=_workflow_agent_references,
        native_config=_workflow_agent_config_from_native,
        native_inputs=_workflow_agent_native_inputs,
        native_outputs=_single_native_output("outputVariable", "result"),
    ),
    "knowledge_retrieval": PlannerNodeAdapter(
        kind="knowledge_retrieval",
        config_model=KnowledgeRetrievalPlannerConfig,
        compile_node=_compile_knowledge_retrieval,
        decompile_node=_decompile_knowledge_retrieval,
        decompile_node_v3=_decompile_knowledge_retrieval_v3,
        output_schema=_knowledge_output_schema,
        validate_node_shape=_validate_knowledge_retrieval_shape,
        native_config=lambda data: _pure_config_from_native(
            "knowledge_retrieval", data
        ),
        editor_config_projector=_knowledge_editor_config_from_native,
        native_inputs=_single_native_input("queryVariable", "query"),
        native_outputs=_single_native_output("outputVariable", "result"),
        resource_kind="knowledge_base",
        control_only_output_ports=("error",),
    ),
    "data_table_query": PlannerNodeAdapter(
        kind="data_table_query",
        config_model=DataTableQueryPlannerConfig,
        compile_node=_compile_data_table_query,
        decompile_node=_decompile_data_table_query,
        decompile_node_v3=_decompile_data_table_query_v3,
        output_schema=_data_table_output_schema,
        validate_node_shape=_validate_data_table_query_shape,
        native_config=lambda data: _pure_config_from_native(
            "data_table_query", data
        ),
        editor_config_projector=_data_table_editor_config_from_native,
        native_inputs=_data_table_query_native_inputs,
        native_outputs=_single_native_output("outputVariable", "result"),
        resource_kind="data_table",
        validate_resource=_validate_data_table_resource,
        control_only_output_ports=("error",),
    ),
    "json_serialize": PlannerNodeAdapter(
        kind="json_serialize",
        config_model=JsonSerializePlannerConfig,
        compile_node=_compile_json_serialize,
        decompile_node=_decompile_json_serialize,
        decompile_node_v3=_decompile_json_serialize_v3,
        validate_node_shape=_validate_json_serialize_shape,
        native_config=lambda data: _pure_config_from_native("json_serialize", data),
        native_inputs=_single_native_input("inputVariable", "value"),
        native_outputs=_single_native_output("outputVariable", "json"),
    ),
    "json_deserialize": PlannerNodeAdapter(
        kind="json_deserialize",
        config_model=JsonDeserializePlannerConfig,
        compile_node=_compile_json_deserialize,
        decompile_node=_decompile_json_deserialize,
        decompile_node_v3=_decompile_json_deserialize_v3,
        output_schema=_json_deserialize_output_schema,
        validate_node_shape=_validate_json_deserialize_shape,
        native_config=lambda data: _pure_config_from_native("json_deserialize", data),
        native_inputs=_single_native_input("inputVariable", "json"),
        native_outputs=_single_native_output("outputVariable", "value"),
    ),
    "variable_aggregator": PlannerNodeAdapter(
        kind="variable_aggregator",
        config_model=VariableAggregatorPlannerConfig,
        compile_node=_compile_variable_aggregator,
        decompile_node=_decompile_variable_aggregator,
        decompile_node_v3=_decompile_variable_aggregator_v3,
        validate_node_shape=_validate_variable_aggregator_shape,
        native_config=lambda data: _pure_config_from_native(
            "variable_aggregator", data
        ),
        native_inputs=_variable_aggregator_native_inputs,
        native_outputs=_single_native_output("outputVariable", "result"),
    ),
    "data_aggregate": PlannerNodeAdapter(
        kind="data_aggregate",
        config_model=DataAggregatePlannerConfig,
        compile_node=_compile_data_aggregate,
        decompile_node=_decompile_data_aggregate,
        decompile_node_v3=_decompile_data_aggregate_v3,
        validate_node_shape=_validate_data_aggregate_shape,
        native_config=lambda data: _pure_config_from_native("data_aggregate", data),
        native_inputs=_single_native_input("inputVariable", "rows"),
        native_outputs=_single_native_output("outputVariable", "result"),
    ),
    "dataset_compare": PlannerNodeAdapter(
        kind="dataset_compare",
        config_model=DatasetComparePlannerConfig,
        compile_node=_compile_dataset_compare,
        decompile_node=_decompile_dataset_compare,
        decompile_node_v3=_decompile_dataset_compare_v3,
        validate_node_shape=_validate_dataset_compare_shape,
        native_config=lambda data: _pure_config_from_native("dataset_compare", data),
        native_inputs=_dataset_compare_native_inputs,
        native_outputs=_single_native_output("outputVariable", "result"),
    ),
    "condition": PlannerNodeAdapter(
        kind="condition",
        config_model=ConditionPlannerConfig,
        compile_node=_compile_condition,
        decompile_node=_decompile_condition,
        decompile_node_v3=_decompile_condition_v3,
        validate_node_shape=_validate_router_shape,
        native_config=lambda data: _pure_config_from_native("condition", data),
        native_inputs=_single_native_input("inputVariable", "value"),
        native_outputs=_no_native_outputs,
    ),
    "multi_route": PlannerNodeAdapter(
        kind="multi_route",
        config_model=MultiRoutePlannerConfig,
        compile_node=_compile_multi_route,
        decompile_node=_decompile_multi_route,
        decompile_node_v3=_decompile_multi_route_v3,
        validate_node_shape=_validate_router_shape,
        native_config=lambda data: _pure_config_from_native("multi_route", data),
        native_inputs=_single_native_input("inputVariable", "value"),
        native_outputs=_no_native_outputs,
    ),
    "data_merge": PlannerNodeAdapter(
        kind="data_merge",
        config_model=DataMergePlannerConfig,
        compile_node=_compile_data_merge,
        decompile_node=_decompile_data_merge,
        decompile_node_v3=_decompile_data_merge_v3,
        validate_node_shape=_validate_data_merge_shape,
        native_config=lambda data: _pure_config_from_native("data_merge", data),
        native_inputs=_data_merge_native_inputs,
        native_outputs=_single_native_output("outputVariable", "result"),
    ),
    "terminate_error": PlannerNodeAdapter(
        kind="terminate_error",
        config_model=TerminateErrorPlannerConfig,
        compile_node=_compile_terminate_error,
        decompile_node=_decompile_terminate_error,
        decompile_node_v3=_decompile_terminate_error_v3,
        validate_node_shape=_validate_terminate_shape,
        native_config=lambda data: _pure_config_from_native("terminate_error", data),
        native_inputs=_no_native_inputs,
        native_outputs=_no_native_outputs,
    ),
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
        "task_binding": contract.planner.task_binding,
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
