from __future__ import annotations

from typing import Any, Callable
from pydantic import BaseModel, ConfigDict, Field

from .schemas import DataTableWriteGrant, GraphIntentV3, ResolvedGraphNodeV3, WorkflowValueSchema

WRITE_KINDS = frozenset({"data_table_insert", "data_table_update", "data_table_delete"})
SYSTEM_FIELDS = frozenset({"record_id", "created_at", "updated_at", "revision"})
WRITE_VALUES_PRODUCER_KIND = "json_deserialize"


def write_value_source_contract(kind: str, available_kinds: set[str] | None = None) -> dict[str, Any] | None:
    if kind not in WRITE_KINDS or kind == "data_table_delete":
        return None
    input_available = available_kinds is None or WRITE_VALUES_PRODUCER_KIND in available_kinds
    return {
        "allowed": ["input", "literal"] if input_available else ["literal"],
        "input_producer_kind": WRITE_VALUES_PRODUCER_KIND,
        "input_available": input_available,
        "rules": [
            "所有 input 业务值必须直接来自 JSON Deserialize V2 的已验证对象输出，不限于 Agent 生成的数据。",
            "literal 必须由目标明确支持并在 config.values 中显式提供；不能为消除错误擅自替换业务值或增加权限。",
        ],
    }


class WriteEditorInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    port: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
    source_ref: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    source_port: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")


def write_value_input_issue(node: Any, binding: Any, producer: Any) -> dict[str, Any] | None:
    if (node.kind not in WRITE_KINDS or node.kind == "data_table_delete"
            or node.config.get("value_source", "input") != "input" or binding.port != "values"):
        return None
    if producer is not None and producer.kind == WRITE_VALUES_PRODUCER_KIND:
        return None
    return {
        "code": "WRITE_VALUES_PRODUCER_INVALID", "node_ref": node.ref, "port": "values",
        "source_ref": binding.source_ref, "source_port": binding.source_port,
        "required_producer_kind": WRITE_VALUES_PRODUCER_KIND,
        "message": f"节点 {node.ref} 的业务值输入必须经过 JSON Deserialize V2 字段校验。",
    }


class WriteEditorIntent(BaseModel):
    """Untrusted canvas request, never a native execution configuration."""
    model_config = ConfigDict(extra="forbid")
    resource_id: str = Field(min_length=1, max_length=200)
    config: dict[str, Any]
    inputs: list[WriteEditorInput] = Field(default_factory=list, max_length=22)


def resolve_write_grant_scope(node: Any, grants: list[DataTableWriteGrant]) -> DataTableWriteGrant:
    """Resolve table/operation authority without consuming unvalidated config."""
    resource_id = node.resource_ref.resource_id if node.resource_ref else ""
    matches = [grant for grant in grants if grant.table_id == resource_id]
    operation = node.kind.removeprefix("data_table_")
    if len(matches) != 1 or operation not in matches[0].operations:
        raise ValueError(f"节点 {node.ref} 未获该表的 {operation} 显式授权。")
    return matches[0]


def resolve_write_grant(node: Any, grants: list[DataTableWriteGrant]) -> DataTableWriteGrant:
    grant = resolve_write_grant_scope(node, grants)
    if node.config.get("max_affected_rows", 1) > grant.max_affected_rows:
        raise ValueError(f"节点 {node.ref} 的影响行数超过用户授权。")
    if set(node.config.get("values") or {}) - set(grant.writable_fields):
        raise ValueError(f"节点 {node.ref} 包含未授权写入字段。")
    return grant


def validate_write_graph(
    intent: GraphIntentV3,
    resolved: dict[str, ResolvedGraphNodeV3],
    outputs: dict[tuple[str, str], tuple[str, WorkflowValueSchema]],
    ancestors: dict[str, set[str]],
    scenarios: list[dict[str, Any]],
    compatible: Callable[[WorkflowValueSchema, WorkflowValueSchema], bool],
) -> None:
    nodes = {node.ref: node for node in intent.nodes}

    def unchecked_agent(ref: str, seen: frozenset[str] = frozenset()) -> bool:
        if ref in seen:
            raise ValueError("业务值来源存在循环。")
        node = nodes.get(ref)
        if node is None:
            return False
        if node.kind == "json_deserialize":
            return False
        if node.kind == "workflow_agent":
            return True
        return any(unchecked_agent(item.source_ref, seen | {ref}) for item in node.inputs)

    table_nodes = [node for node in intent.nodes if node.kind in WRITE_KINDS | {"data_table_query"}]
    for node in table_nodes:
        if node.kind not in WRITE_KINDS:
            continue
        actual = resolved[node.ref]
        resource = actual.resource_snapshot
        grant = actual.write_grant
        if resource is None or grant is None:
            raise ValueError("写节点缺少服务端资源与操作授权。")
        inputs = {item.port: item for item in node.inputs}
        if node.kind != "data_table_insert":
            binding = inputs["records"]
            source = nodes.get(binding.source_ref)
            if source is None or source.kind not in {"data_table_query", "data_table_insert"} or binding.source_port != "result":
                raise ValueError(f"节点 {node.ref} 的记录只能直接来自真实查询或插入节点。")
            source_resource = resolved[source.ref].resource_snapshot
            if source_resource is None or (source_resource.resource_id, source_resource.pinned_schema_version, source_resource.schema_checksum) != (resource.resource_id, resource.pinned_schema_version, resource.schema_checksum):
                raise ValueError(f"节点 {node.ref} 的记录来源必须是同表、同 Schema 的查询或插入。")
        if node.kind != "data_table_delete" and node.config.get("value_source", "input") == "input":
            binding = inputs["values"]
            producer = nodes.get(binding.source_ref)
            source_issue = write_value_input_issue(node, binding, producer)
            if source_issue:
                raise ValueError(source_issue["message"])
            source_schema = outputs[(binding.source_ref, binding.source_port)][1]
            if source_schema.type != "object" or source_schema.nullable or source_schema.any_of or not source_schema.properties:
                raise ValueError(f"节点 {node.ref} 需要明确声明业务字段的对象输入，不能使用未验证的 any。")
            if set(source_schema.properties) - set(grant.writable_fields):
                raise ValueError(f"节点 {node.ref} 的业务值输入包含未授权字段。")
            for name, value_schema in source_schema.properties.items():
                if name in SYSTEM_FIELDS or name not in resource.fields or not compatible(value_schema, resource.fields[name]):
                    raise ValueError(f"节点 {node.ref} 的业务字段 {name} 与固定 Schema 类型不符。")
            if unchecked_agent(binding.source_ref):
                raise ValueError(f"节点 {node.ref} 的 Agent 业务数据必须先通过 JSON Deserialize V2 验证。")
    for index, left in enumerate(table_nodes):
        for right in table_nodes[index + 1:]:
            if left.kind not in WRITE_KINDS and right.kind not in WRITE_KINDS:
                continue
            if left.resource_ref.resource_id != right.resource_ref.resource_id:
                continue
            co_reachable = any(left.ref in scenario["reached"] and right.ref in scenario["reached"] for scenario in scenarios)
            if co_reachable and left.ref not in ancestors[right.ref] and right.ref not in ancestors[left.ref]:
                raise ValueError(f"同表冲突读写 {left.ref} 与 {right.ref} 必须显式配置控制先后。")
