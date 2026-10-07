from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, get_args

from pydantic import BaseModel, ValidationError

from .write_delivery import CONTEXT_FIELD, source_task_input
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
        DATA_TABLE_WRITE_KINDS,
        DataTableInsertPlannerConfig,
        DataTableUpdatePlannerConfig,
        DataTableDeletePlannerConfig,
        DataTableWriteGrant,
        validate_controlled_write_authority,
        DatasetComparePlannerConfig,
        JsonDeserializePlannerConfig,
        JsonSerializePlannerConfig,
        KnowledgeRetrievalPlannerConfig,
        MultiRoutePlannerConfig,
        NODE_CONTRACT_VERSION,
        TerminateErrorPlannerConfig,
        VariableAggregatorPlannerConfig,
        VisionModelBindingSnapshot,
        VisionUnderstandingPlannerConfig,
        WorkflowValueSchema,
        canonical_checksum,
        vision_understanding_result_schema,
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
        DATA_TABLE_WRITE_KINDS,
        DataTableInsertPlannerConfig,
        DataTableUpdatePlannerConfig,
        DataTableDeletePlannerConfig,
        DataTableWriteGrant,
        validate_controlled_write_authority,
        DatasetComparePlannerConfig,
        JsonDeserializePlannerConfig,
        JsonSerializePlannerConfig,
        KnowledgeRetrievalPlannerConfig,
        MultiRoutePlannerConfig,
        NODE_CONTRACT_VERSION,
        TerminateErrorPlannerConfig,
        VariableAggregatorPlannerConfig,
        VisionModelBindingSnapshot,
        VisionUnderstandingPlannerConfig,
        WorkflowValueSchema,
        canonical_checksum,
        vision_understanding_result_schema,
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
    vision_model_snapshot: dict[str, Any] | None = None
    write_grant: dict[str, Any] | None = None
    write_request_context: str = ""


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

    def model_binding_contract(self, *, available_kinds: set[str] | None = None) -> dict[str, Any] | None:
        """Project table-dependent bindings without pretending family ports are concrete."""
        if self.resource_kind != "data_table":
            return None
        fields = self.config_model.model_fields
        input_contract = _data_table_input_contract(self.kind)
        from .write_contract import write_value_source_contract
        value_contract = write_value_source_contract(self.kind, available_kinds)
        if value_contract and not value_contract["input_available"]:
            input_contract = {**input_contract, "when_value_source_input": []}
        input_rules = [
            "inputs 必须恰好包含按当前 config 计算的端口，每个端口绑定一次；不得添加其他输入。",
            "仅使用 config_field_contract.allowed；required 不能遗漏。Query 使用 limit，写节点的 max_affected_rows 不得放进 Query；Update/Delete 必须提供非空 filter。",
        ]
        if input_contract["when_value_source_input"]:
            input_rules.append(
                "仅当 config.value_source=input 时绑定 values；literal 时业务值放入 config.values，不能再绑定 values 输入。"
            )
        if input_contract["filter_input_port_template"]:
            input_rules.extend([
                "递归检查 config.filter 中每条 predicate：仅 value_source=input 生成 predicate_{ref} 端口，ref 必须来自该谓词；literal/none 不生成输入。裸 predicate 不是合法端口。",
                "例如谓词 ref=selected、value_source=input 对应 predicate_selected；改为 literal 后必须删除该输入。",
                "动态谓词输入类型必须匹配固定表字段；in 使用字段类型的数组，其他运算使用字段本身类型。",
            ])
        if input_contract["always"]:
            input_rules.append(
                "records 始终必填，必须直接绑定同表 Query/Insert 的整个 result；运行时收据已校验 record_id/revision，"
                "不需要再把整个 records 接入身份或 revision 标量谓词。非空 filter 仅表达目标要求的业务条件，"
                "只缩小该真实记录集合，不替代 records，不扩展目标；不得虚构身份字面值或删除必要的业务条件。"
            )
            input_rules.append(
                "records.value_schema 按来源的 output_binding_contract 选择形状，不复制全部字段；"
                "first 必须保留 nullable。通用 any_of 表示受限联合，不等于裸 any。"
            )
        output: dict[str, Any] = {
            "port": "result",
            "field_projection_supported": False,
            "rules": [
                "result 是整个结果值；record_id 等对象属性不是独立输出端口或变量。",
                "绑定必须引用真实上游的整个输出端口；不得虚构 insert_id 或使用属性路径。",
                "对象或数组交给 Agent 时须经已授权的 json_serialize 转为字符串；不能改标结果类型。",
            ],
            "delivery_rules": [
                "最终 Agent 只能依据显式接入其 Prompt 的证据汇总；原生执行不会自动读取上游配置或其他输出。Planner 编译器对显式消费 Update 回执的 Agent 附带受限请求证据，不代表它知道整个工作流。",
            ],
        }
        if self.kind == "data_table_query":
            output.update(
                shape="record_list_or_nullable_record",
                value_schema_by_return_mode={
                    mode: self.authoritative_output_schema(
                        "result", self.config_model(return_mode=mode),
                    ).model_dump(mode="json", exclude_defaults=True)
                    for mode in get_args(fields["return_mode"].annotation)
                },
                rules=[*output["rules"],
                       "return_mode=list 输出记录对象数组；first 输出单条记录对象或 null。",
                       "字段由固定 Schema 和 select_fields 推导；自动包含 record_id/revision/created_at/updated_at。"],
                delivery_rules=[*output["delivery_rules"],
                    "Query 是查询时点的记录快照；后续写入不会修改此前 result。写后查询状态不能单独证明某个写节点实际执行，效果汇总还需对应执行回执。"],
            )
        elif self.kind == "data_table_insert":
            output.update(
                shape="record",
                value_schema=self.authoritative_output_schema(
                    "result", self.config_model(),
                ).model_dump(mode="json", exclude_defaults=True),
                rules=[*output["rules"],
                       "Insert 只有一个 result 输出，值是完整新记录对象，包含业务字段及 record_id/revision/created_at/updated_at，不是单独的 ID 字符串。"],
                delivery_rules=[*output["delivery_rules"],
                    "Insert result 可证明本次插入时的记录；后续若还有写入，不能把插入时的字段值宣称为最终状态。没有额外状态要求时无需重复查询。"],
            )
        else:
            output.update(
                shape="affected_counts",
                value_schema=_data_table_affected_schema().model_dump(mode="json"),
                rules=[*output["rules"],
                       "Update/Delete 只输出 matched/affected 计数，不输出记录；再次修改必须重新查询取得新 revision。"],
                delivery_rules=[*output["delivery_rules"],
                    "汇总须区分请求修改值、实际执行回执和查询时点状态；affected=0 或节点未执行不能宣称修改成功，计数不能还原记录字段。",
                    "若目标要求说明修改内容，literal 在显式回执引用下由 Planner 编译器携带同一请求值；input 仍须引用用于写入的同一 JSON Deserialize V2 输出，并同时接入写回执，不自动补连线。请求值不是写后状态证据。"
                    if self.kind == "data_table_update" else
                    "删除说明使用本次选中记录和 Delete 回执；删除前记录不是删除后状态，不得把 affected=0 说成已删除。",
                    "若目标要求核对写后实际状态，须在已有 Query 授权下显式安排写后查询并接入所需字段和写回执；保留查询时点，不暗示跨节点事务。写权限不隐含读权限；无读取授权时明确证据限制，不编造状态或扩大授权。"],
            )
        return {
            **({"value_source_contract": value_contract} if value_contract else {}),
            "config_field_contract": {
                "allowed": sorted(fields),
                "required": sorted(name for name, field in fields.items() if field.is_required()),
            },
            "input_binding_contract": {
                **input_contract,
                **({"records_schema_from": "source.output_binding_contract"} if input_contract["always"] else {}),
                "rules": input_rules,
            },
            "output_binding_contract": output,
        }

    def configured_input_ports(self, parsed: BaseModel) -> set[str] | None:
        """Exact table bindings from the same function used by shape validation."""
        if self.resource_kind != "data_table":
            return None
        return _data_table_input_ports(self.kind, parsed)

    def input_port_counts(self, parsed: BaseModel | None = None) -> dict[str, tuple[int, int | None]]:
        """Project counts from NodeContract and existing config-dependent bindings."""
        if self.resource_kind == "data_table":
            if parsed is None:
                raise ValueError("动态表输入必须先解析配置。")
            return {port: (1, 1) for port in sorted(self.configured_input_ports(parsed))}
        if isinstance(parsed, VariableAggregatorPlannerConfig):
            count = len(parsed.output_fields)
            return {"values": (count, count)}
        return {
            port.name: (int(port.required), None if port.cardinality == "many" else 1)
            for port in self.intent_port_contracts("input")
        }

    def input_binding_state(self, node: GraphIntentNodeV3, parsed: BaseModel) -> dict[str, Any]:
        """Observe input cardinality without selecting sources or changing validation."""
        counts = self.input_port_counts(parsed)
        indices: dict[str, list[int]] = {port: [] for port in counts}
        unexpected = []
        for index, binding in enumerate(node.inputs):
            port = next((name for name in counts if (
                binding.port == name if self.resource_kind == "data_table"
                else _port_matches(binding.port, name)
            )), None)
            if port is None:
                unexpected.append(index)
            else:
                indices[port].append(index)
        ports = [
            {"port": name, "minimum": minimum, "maximum": maximum,
             "actual": len(indices[name]), "input_indices": indices[name]}
            for name, (minimum, maximum) in counts.items()
        ]
        return {
            "ports": ports, "unexpected_input_indices": unexpected,
            "valid": not unexpected and all(
                item["actual"] >= item["minimum"]
                and (item["maximum"] is None or item["actual"] <= item["maximum"])
                for item in ports
            ),
        }

    def resolved_predicate_input_schemas(
        self, parsed: BaseModel, resource_snapshot: dict[str, Any],
    ) -> dict[str, WorkflowValueSchema]:
        """Trusted scalar operands; records/values are separate input contracts."""
        if self.resource_kind != "data_table":
            return {}
        fields = _table_snapshot_fields(resource_snapshot)
        return {
            f"predicate_{predicate.ref}": _predicate_operand_schema(predicate, fields, index)
            for index, predicate in enumerate(_table_predicates(getattr(parsed, "filter", None)))
            if predicate.value_source == "input"
        }

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


def _validate_vision_understanding_shape(
    node: MetaPlannerIRNode | GraphIntentNodeV3,
    _parsed: BaseModel,
) -> None:
    if node.task_ids:
        raise ValueError(f"节点 {node.ref} 不得绑定规划任务。")
    if node.resource_ref is not None:
        raise ValueError(
            f"节点 {node.ref} 使用运行时文件输入，不得携带资源引用。"
        )
    if (
        len(node.inputs) != 1
        or node.inputs[0].port != "asset_id"
        or node.inputs[0].variable != "selected_file_asset_id"
    ):
        raise ValueError(
            f"节点 {node.ref} 必须且只能绑定变量 selected_file_asset_id 的 asset_id 输入。"
        )
    if (
        len(node.outputs) != 1
        or node.outputs[0].port != "result"
    ):
        raise ValueError(f"节点 {node.ref} 必须且只能声明一个 result 输出。")


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
    expected = _data_table_input_ports(node.kind, config)
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


_WRITE_CONFIG_MODELS = {
    "data_table_insert": DataTableInsertPlannerConfig,
    "data_table_update": DataTableUpdatePlannerConfig,
    "data_table_delete": DataTableDeletePlannerConfig,
}


def _data_table_input_contract(kind: str) -> dict[str, Any]:
    model = DataTableQueryPlannerConfig if kind == "data_table_query" else _WRITE_CONFIG_MODELS[kind]
    return {
        "matching": "exactly_once",
        "always": ["records"] if kind in {"data_table_update", "data_table_delete"} else [],
        "when_value_source_input": ["values"] if "value_source" in model.model_fields else [],
        "filter_input_port_template": "predicate_{ref}" if "filter" in model.model_fields else None,
    }


def _data_table_input_ports(kind: str, parsed: BaseModel) -> set[str]:
    contract = _data_table_input_contract(kind)
    ports = set(contract["always"])
    if getattr(parsed, "value_source", None) == "input":
        ports.update(contract["when_value_source_input"])
    if template := contract["filter_input_port_template"]:
        ports.update(
            template.format(ref=item.ref)
            for item in _table_predicates(parsed.filter)
            if item.value_source == "input"
        )
    return ports


class PlannerWriteInputContractError(ValueError):
    def __init__(self, node_ref: str, expected: set[str], actual: list[str]) -> None:
        counts = Counter(actual)
        missing = sorted(expected - counts.keys())
        duplicates = sorted(port for port in expected if counts[port] > 1)
        unexpected = [index for index, port in enumerate(actual) if port not in expected]
        self.input_diagnostic = {
            "code": "write_input_contract_mismatch",
            "node_ref": node_ref,
            "expected_ports": sorted(expected),
            "missing_ports": missing,
            "duplicate_ports": duplicates,
            "unexpected_input_indices": unexpected,
        }
        # Unknown port names may contain injected content; identify their positions only.
        super().__init__(
            f"节点 {node_ref} 的写入输入端口不符：应有 {sorted(expected)}；"
            f"缺少 {missing}；重复 {duplicates}；多余输入位置 {unexpected}（从 0 计）。"
        )


class PlannerResourceContractError(ValueError):
    MESSAGES = {
        "TABLE_SELECT_FIELD_UNKNOWN": "查询选择了固定 Schema 中不存在的业务字段。",
        "TABLE_SORT_FIELD_UNKNOWN": "排序使用了固定 Schema 中不存在的字段。",
        "TABLE_PREDICATE_FIELD_UNKNOWN": "筛选谓词使用了固定 Schema 中不存在的字段。",
        "TABLE_PREDICATE_OPERATOR_INVALID": "contains 运算只允许字符串字段。",
        "TABLE_PREDICATE_INPUT_TYPE_MISMATCH": "筛选谓词输入类型不符合固定 Schema；记录对象不能直接作为字段标量。",
        "TABLE_PREDICATE_LITERAL_TYPE_MISMATCH": "筛选谓词字面值不符合固定字段类型。",
        "TABLE_WRITE_FIELD_INVALID": "写入包含未知字段或系统字段。",
        "TABLE_WRITE_VALUE_TYPE_MISMATCH": "写入字面值不符合固定字段类型。",
    }

    def __init__(
        self, code: str, *, predicate_index: int | None = None,
        input_index: int | None = None, field_index: int | None = None,
        expected: WorkflowValueSchema | None = None,
        actual: WorkflowValueSchema | None = None,
    ) -> None:
        message = self.MESSAGES[code]
        self.code = code
        self.detail: dict[str, Any] = {
            key: value for key, value in (
                ("predicate_index", predicate_index), ("input_index", input_index),
                ("field_index", field_index),
            ) if type(value) is int and 0 <= value <= 100_000
        }
        for label, schema in (("expected", expected), ("actual", actual)):
            if schema is not None:
                self.detail[f"{label}_type"] = schema.type
                self.detail[f"{label}_schema_checksum"] = canonical_checksum(schema.model_dump(mode="json"))
        super().__init__(f"{code}：{message}")


def _validate_data_table_write_shape(node: MetaPlannerIRNode | GraphIntentNodeV3, parsed: BaseModel) -> None:
    expected = _data_table_input_ports(node.kind, parsed)
    actual = [item.port for item in node.inputs]
    if set(actual) != expected or len(actual) != len(expected):
        raise PlannerWriteInputContractError(node.ref, expected, actual)
    if node.task_ids:
        raise ValueError("受控写入节点不能承担计划任务。")
    _require_single_output(node, "result")


def _validate_data_table_write_resource(node: GraphIntentNodeV3, parsed: BaseModel, resource: dict[str, Any]) -> None:
    query = DataTableQueryPlannerConfig(filter=getattr(parsed, "filter", None))
    _validate_data_table_resource(node, query, resource)
    fields = _table_snapshot_fields(resource)
    if getattr(parsed, "value_source", None) == "literal":
        for index, (name, value) in enumerate((parsed.values or {}).items()):
            if name not in fields or name in {"record_id", "created_at", "updated_at", "revision"}:
                raise PlannerResourceContractError("TABLE_WRITE_FIELD_INVALID", field_index=index)
            try:
                fields[name].assert_value(value, path="$.values")
            except ValueError:
                raise PlannerResourceContractError(
                    "TABLE_WRITE_VALUE_TYPE_MISMATCH", field_index=index, expected=fields[name],
                ) from None


def _data_table_affected_schema() -> WorkflowValueSchema:
    return WorkflowValueSchema(type="object", properties={"matched": WorkflowValueSchema(type="integer"), "affected": WorkflowValueSchema(type="integer")}, required=("matched", "affected"))


def _data_table_write_output_schema(port: str, parsed: BaseModel, resource: dict[str, Any] | None) -> WorkflowValueSchema:
    if port != "result":
        raise ValueError("受控写入只有 result 输出端口。")
    if type(parsed) is DataTableInsertPlannerConfig:
        return _data_table_output_schema(port, DataTableQueryPlannerConfig(return_mode="first"), resource).model_copy(update={"nullable": False})
    return _data_table_affected_schema()


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
        raise PlannerResourceContractError(
            "TABLE_SELECT_FIELD_UNKNOWN", field_index=next(i for i, name in enumerate(config.select_fields) if name not in business_fields),
        )
    if unknown_sort:
        raise PlannerResourceContractError(
            "TABLE_SORT_FIELD_UNKNOWN", field_index=next(i for i, item in enumerate(config.sort) if item.field not in fields),
        )
    inputs = {item.port: (index, item) for index, item in enumerate(node.inputs)}
    for index, predicate in enumerate(_table_predicates(config.filter)):
        expected_schema = _predicate_operand_schema(predicate, fields, index)
        if predicate.value_source == "input":
            input_index, binding = inputs[f"predicate_{predicate.ref}"]
            if canonical_checksum(
                binding.value_schema.model_dump(mode="json")
            ) != canonical_checksum(expected_schema.model_dump(mode="json")):
                raise PlannerResourceContractError(
                    "TABLE_PREDICATE_INPUT_TYPE_MISMATCH", predicate_index=index,
                    input_index=input_index, expected=expected_schema, actual=binding.value_schema,
                )
        elif predicate.value_source == "literal":
            try:
                expected_schema.assert_value(predicate.value, path="$.filter.value")
            except ValueError:
                raise PlannerResourceContractError(
                    "TABLE_PREDICATE_LITERAL_TYPE_MISMATCH", predicate_index=index, expected=expected_schema,
                ) from None


def _predicate_operand_schema(
    predicate: DataTableQueryPredicatePlannerConfig,
    fields: dict[str, WorkflowValueSchema], index: int,
) -> WorkflowValueSchema:
    field_schema = fields.get(predicate.field)
    if field_schema is None:
        raise PlannerResourceContractError("TABLE_PREDICATE_FIELD_UNKNOWN", predicate_index=index)
    if predicate.operator == "contains" and field_schema.type != "string":
        raise PlannerResourceContractError("TABLE_PREDICATE_OPERATOR_INVALID", predicate_index=index)
    nonnullable = field_schema.model_copy(update={"nullable": False})
    return WorkflowValueSchema(type="array", items=nonnullable) if predicate.operator == "in" else nonnullable


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


def _vision_understanding_output_schema(
    port: str,
    _parsed: BaseModel,
    _resource_snapshot: dict[str, Any] | None,
) -> WorkflowValueSchema:
    if port != "result":
        raise ValueError(f"视觉理解节点没有输出端口 {port}。")
    return vision_understanding_result_schema()


def _data_table_output_schema(
    port: str,
    parsed: BaseModel,
    resource_snapshot: dict[str, Any] | None,
) -> WorkflowValueSchema:
    if port != "result":
        raise ValueError(f"Agent Table query has no output port {port}.")
    config = DataTableQueryPlannerConfig.model_validate(parsed)
    # Config determines the shape even before authorized resource fields are resolved.
    fields = _table_snapshot_fields(resource_snapshot) if resource_snapshot is not None else {}
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
        "task_input": source_task_input(data).strip(),
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


def _data_table_write_native_inputs(data: dict[str, Any], parsed: BaseModel) -> list[tuple[str, str]]:
    result = []
    if getattr(parsed, "value_source", None) == "input":
        result.append(("values", str(data.get("valuesVariable") or "")))
    if hasattr(parsed, "filter"):
        result.append(("records", str(data.get("recordsVariable") or "")))
        result.extend(_data_table_filter_inputs_from_native(parsed.filter, data.get("filter")))
    return result


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


def _vision_model_compile_snapshot(
    context: PlannerNodeCompileContext,
) -> dict[str, Any]:
    snapshot = context.vision_model_snapshot
    if not isinstance(snapshot, dict):
        raise ValueError("Planner 编译器需要可信的视觉模型 Managed Binding 快照。")
    try:
        binding = VisionModelBindingSnapshot.model_validate(snapshot)
    except ValidationError as exc:
        raise ValueError("视觉模型 Managed Binding 快照不符合严格契约。") from exc
    if binding.entry_id != "xpert_vision":
        raise ValueError("视觉模型 Managed Binding 必须属于 xpert_vision 入口。")
    if binding.execution_shape != "vision_json_unary":
        raise ValueError("视觉模型 Managed Binding 必须使用 vision_json_unary 执行形态。")
    return binding.model_dump(mode="json")


def _compile_vision_understanding(
    node: MetaPlannerIRNode,
    parsed: BaseModel,
    context: PlannerNodeCompileContext,
) -> NativeWorkflowNode:
    config = VisionUnderstandingPlannerConfig.model_validate(parsed)
    source = _require_single_input(node, "asset_id")
    output = _require_single_output(node, "result")
    model_binding = _vision_model_compile_snapshot(context)
    model_id = str(model_binding["model_id"])
    return NativeWorkflowNode(
        id=context.node_id,
        type="vision_understanding",
        position=context.position,
        data={
            **_base_pure_node_data(node),
            "plannerAdapterConfigV1": config.model_dump(mode="json"),
            "plannerVisionModelBindingChecksum": canonical_checksum(model_binding),
            "contractVersion": 2,
            "visionModelId": model_id,
            "visionModelBinding": model_binding,
            "assetIdVariable": source.variable,
            "pdfPageStrategy": config.pdf_page_strategy,
            "maxPages": config.max_pages,
            "maxImageEdge": config.max_image_edge,
            "failurePolicy": config.failure_policy,
            "outputVariable": output.variable,
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


def _compile_data_table_write(node: MetaPlannerIRNode, parsed: BaseModel, context: PlannerNodeCompileContext) -> NativeWorkflowNode:
    resource = _resource_compile_snapshot(context, kind="data_table")
    grant = DataTableWriteGrant.model_validate(context.write_grant)
    output = _require_single_output(node, "result")
    data = {
        **_base_pure_node_data(node),
        "plannerAdapterConfigV1": parsed.model_dump(mode="json"),
        "plannerResourceSnapshotChecksum": resource["snapshot_checksum"],
        "contractVersion": 2,
        "tableId": resource["resource_id"],
        "versionPolicy": "pinned",
        "pinnedSchemaVersion": resource["pinned_schema_version"],
        "pinnedSchemaChecksum": resource["schema_checksum"],
        "writeGrant": grant.model_dump(mode="json"),
        "maxAffectedRows": getattr(parsed, "max_affected_rows", 1),
        "failureAction": "stop",
        "retryMode": "none",
        "outputVariable": output.variable,
    }
    if node.kind != "data_table_delete":
        data["valueSource"] = parsed.value_source
        if parsed.value_source == "literal":
            data["literalValues"] = parsed.values
        else:
            data["valuesVariable"] = _require_single_input(node, "values").variable
    if node.kind != "data_table_insert":
        data["recordsVariable"] = _require_single_input(node, "records").variable
        data["filter"] = _compile_data_table_filter(parsed.filter, node)
    validate_controlled_write_authority(node.kind, data)
    return NativeWorkflowNode(id=context.node_id, type=node.kind, position=context.position, data=data)


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
            "taskInput": config.task_input + context.write_request_context,
            **({CONTEXT_FIELD: context.write_request_context} if context.write_request_context else {}),
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
                "task_input": source_task_input(data),
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


def _vision_editor_config_from_native(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "pdf_page_strategy": data.get("pdfPageStrategy"),
        "max_pages": data.get("maxPages"),
        "max_image_edge": data.get("maxImageEdge"),
        "failure_policy": data.get("failurePolicy"),
    }


def _vision_config_from_native(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("contractVersion") != 2:
        raise ValueError("Planner 视觉理解节点必须使用 contractVersion 2。")
    payload = data.get("plannerAdapterConfigV1")
    if not isinstance(payload, dict):
        raise ValueError("Planner 视觉理解节点缺少 Adapter 配置。")
    config = VisionUnderstandingPlannerConfig.model_validate(payload)
    expected = _vision_editor_config_from_native(data)
    if config.model_dump(mode="json") != expected:
        raise ValueError("Planner 视觉理解节点的原生配置已发生漂移。")

    raw_binding = data.get("visionModelBinding")
    if not isinstance(raw_binding, dict):
        raise ValueError("Planner 视觉理解节点缺少视觉模型 Managed Binding。")
    try:
        binding = VisionModelBindingSnapshot.model_validate(raw_binding)
    except ValidationError as exc:
        raise ValueError("Planner 视觉模型 Managed Binding 不符合严格契约。") from exc
    if binding.entry_id != "xpert_vision":
        raise ValueError("Planner 视觉模型 Managed Binding 不属于 xpert_vision 入口。")
    if binding.execution_shape != "vision_json_unary":
        raise ValueError(
            "Planner 视觉模型 Managed Binding 不是 vision_json_unary 执行形态。"
        )
    binding_payload = binding.model_dump(mode="json")
    if raw_binding != binding_payload:
        raise ValueError("Planner 视觉模型 Managed Binding 不是完整标准快照。")
    native_model_id = data.get("visionModelId")
    if native_model_id != binding.model_id:
        raise ValueError("Planner 视觉模型与 visionModelId 已发生漂移。")
    expected_binding_checksum = canonical_checksum(raw_binding)
    if data.get("plannerVisionModelBindingChecksum") != expected_binding_checksum:
        raise ValueError("Planner 视觉模型 Managed Binding 摘要已发生漂移。")
    return config.model_dump(mode="json")


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


def _table_editor_filter_from_data(data: dict[str, Any]) -> dict[str, Any] | None:
    raw = data.get("filter")
    payload = data.get("plannerAdapterConfigV1")
    if isinstance(payload, dict):
        try:
            configured = DataTableQueryPlannerConfig.model_validate({"filter": payload.get("filter")}).filter
            _data_table_filter_inputs_from_native(configured, raw)
        except ValueError:
            pass
        else:
            # A native no-op must preserve semantic predicate refs and input ports.
            return configured.model_dump(mode="json") if configured is not None else None
    return _table_editor_filter_from_native(raw)


def _data_table_editor_config_from_native(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "select_fields": list(data.get("selectFields") or []),
        "filter": _table_editor_filter_from_data(data),
        "sort": list(data.get("sort") or []),
        "limit": int(data.get("limit") or 0),
        "return_mode": str(data.get("returnMode") or ""),
        "failure_action": str(data.get("failureAction") or "stop"),
        "retry_mode": str(data.get("retryMode") or "none"),
        "max_attempts": int(data.get("maxAttempts") or 2),
    }


def _pure_config_from_native(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    if kind in DATA_TABLE_WRITE_KINDS:
        validate_controlled_write_authority(kind, data)
        payload = data.get("plannerAdapterConfigV1")
        if not isinstance(payload, dict):
            raise ValueError("受控写节点缺少 Adapter 配置。")
        parsed = _WRITE_CONFIG_MODELS[kind].model_validate(payload)
        actual = parsed.model_dump(mode="json")
        if kind != "data_table_delete" and (data.get("valueSource") != parsed.value_source or data.get("literalValues") != parsed.values):
            raise ValueError("受控写节点的业务值配置已漂移。")
        if kind != "data_table_insert":
            if data.get("maxAffectedRows") != parsed.max_affected_rows:
                raise ValueError("受控写节点的影响上限已漂移。")
            _data_table_filter_inputs_from_native(parsed.filter, data.get("filter"))
        return actual
    if kind == "vision_understanding":
        return _vision_config_from_native(data)
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
        "data_table_insert": "tableId",
        "data_table_update": "tableId",
        "data_table_delete": "tableId",
    }.get(kind)
    if field is None:
        raise ValueError(f"Node kind {kind} has no node-owned resource.")
    resource_id = str(data.get(field) or "").strip()
    if not resource_id:
        raise ValueError(f"Planner {kind} is missing its resource ID.")
    if not str(data.get("plannerResourceSnapshotChecksum") or "").strip():
        raise ValueError(f"Planner {kind} is missing its resource snapshot marker.")
    return {"resource_id": resource_id}


def _validate_vision_round_trip(
    restored: MetaPlannerIRNode | GraphIntentNodeV3,
    data: dict[str, Any],
) -> None:
    parsed = VisionUnderstandingPlannerConfig.model_validate(restored.config)
    _validate_vision_understanding_shape(restored, parsed)
    source = restored.inputs[0]
    output = restored.outputs[0]
    if data.get("assetIdVariable") != source.variable:
        raise ValueError("Planner 视觉节点的附件变量元数据已发生漂移。")
    if data.get("outputVariable") != output.variable:
        raise ValueError("Planner 视觉节点的输出变量元数据已发生漂移。")
    if isinstance(restored, GraphIntentNodeV3):
        expected_input_schema = WorkflowValueSchema(type="string")
        if canonical_checksum(source.value_schema) != canonical_checksum(
            expected_input_schema
        ):
            raise ValueError("Planner 视觉节点的输入类型元数据已发生漂移。")
        if canonical_checksum(output.value_schema) != canonical_checksum(
            vision_understanding_result_schema()
        ):
            raise ValueError("Planner 视觉节点的输出类型元数据已发生漂移。")
    elif source.value_type != "string" or output.value_type != "object":
        raise ValueError("Planner 视觉节点的旧版端口类型元数据已发生漂移。")


def _decompile_pure_node(
    node: NativeWorkflowNode,
    *,
    kind: str,
    graph_ir_v3: bool,
) -> MetaPlannerIRNode | GraphIntentNodeV3:
    data = node.data if isinstance(node.data, dict) else {}
    contract = workflow_node_contract_registry.require(kind)
    if kind == "vision_understanding" and node.type != kind:
        raise ValueError("Planner 视觉节点的原生节点类型已发生漂移。")
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
    if kind in {"knowledge_retrieval", "data_table_query"} | DATA_TABLE_WRITE_KINDS:
        payload["resource_ref"] = _resource_ref_from_native(kind, data)
    model: type[MetaPlannerIRNode] | type[GraphIntentNodeV3] = (
        GraphIntentNodeV3 if graph_ir_v3 else MetaPlannerIRNode
    )
    restored = model.model_validate(payload)
    if kind == "vision_understanding":
        _validate_vision_round_trip(restored, data)
    if kind in DATA_TABLE_WRITE_KINDS:
        parsed = _WRITE_CONFIG_MODELS[kind].model_validate(restored.config)
        _validate_data_table_write_shape(restored, parsed)
        expected_inputs = sorted(_data_table_write_native_inputs(data, parsed))
        if expected_inputs != sorted((item.port, item.variable) for item in restored.inputs) or data.get("outputVariable") != restored.outputs[0].variable:
            raise ValueError("受控写节点的原生变量与端口元数据不一致。")
    return restored


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
_decompile_vision_understanding, _decompile_vision_understanding_v3 = (
    _pure_decompilers("vision_understanding")
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
    "vision_understanding": PlannerNodeAdapter(
        kind="vision_understanding",
        config_model=VisionUnderstandingPlannerConfig,
        compile_node=_compile_vision_understanding,
        decompile_node=_decompile_vision_understanding,
        decompile_node_v3=_decompile_vision_understanding_v3,
        output_schema=_vision_understanding_output_schema,
        validate_node_shape=_validate_vision_understanding_shape,
        native_config=lambda data: _pure_config_from_native(
            "vision_understanding", data
        ),
        editor_config_projector=_vision_editor_config_from_native,
        native_inputs=_single_native_input("assetIdVariable", "asset_id"),
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

def _write_editor_config(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if kind != "data_table_delete":
        result.update(value_source=data.get("valueSource"), values=data.get("literalValues"))
    if kind != "data_table_insert":
        result.update(filter=_table_editor_filter_from_data(data), max_affected_rows=data.get("maxAffectedRows", 1))
    return result


def _write_adapter(kind: str) -> PlannerNodeAdapter:
    legacy, v3 = _pure_decompilers(kind)
    return PlannerNodeAdapter(
        kind=kind, config_model=_WRITE_CONFIG_MODELS[kind], compile_node=_compile_data_table_write,
        decompile_node=legacy, decompile_node_v3=v3, output_schema=_data_table_write_output_schema,
        validate_node_shape=_validate_data_table_write_shape,
        native_config=lambda data: _pure_config_from_native(kind, data),
        editor_config_projector=lambda data: _write_editor_config(kind, data),
        native_inputs=_data_table_write_native_inputs,
        native_outputs=_single_native_output("outputVariable", "result"),
        resource_kind="data_table", validate_resource=_validate_data_table_write_resource,
    )


PLANNER_NODE_ADAPTERS.update({kind: _write_adapter(kind) for kind in sorted(DATA_TABLE_WRITE_KINDS)})
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
