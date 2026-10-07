"""Scope-derived resource choices shared by generation syntaxes."""
from __future__ import annotations

from typing import Any, get_args

from .node_adapters import get_planner_node_adapter
from .schemas import MetaPlannerIRResourceBinding
from .write_contract import WRITE_KINDS


def resource_generation_contract(request: Any, snapshot: Any) -> dict[str, Any]:
    catalogs = {
        "knowledge_base": (snapshot.knowledge_bases, request.scope.knowledge_base_ids),
        "data_table": (snapshot.data_tables, request.scope.data_table_ids),
        "external_xpert": (snapshot.external_xperts, request.scope.external_xpert_ids),
        "toolset_resource": (snapshot.toolsets, request.scope.toolset_ids),
        "plugin_resource": (snapshot.plugins, request.scope.plugin_ids),
    }
    binding_kinds = set(get_args(MetaPlannerIRResourceBinding.model_fields["kind"].annotation))
    available = {str(item.get("kind") or "") for item in snapshot.nodes}
    owned, bound = [], []
    for kind in sorted(set(request.scope.allowed_node_kinds) & available):
        adapter = get_planner_node_adapter(kind)
        resource_kind = adapter.resource_kind if adapter else kind
        if resource_kind not in catalogs:
            continue
        catalog, authorized = catalogs[resource_kind]
        if adapter is not None and adapter.resource_kind == "knowledge_base":
            catalog = [item for item in catalog
                       if str((item.get("metadata") or {}).get("active_version_id") or "").strip()]
        if kind in WRITE_KINDS:
            authorized = [grant.table_id for grant in request.scope.data_table_write_grants
                          if kind.removeprefix("data_table_") in grant.operations]
        ids = sorted({str(item.get("id") or "") for item in catalog} & set(authorized))
        if not ids:
            continue
        if adapter is not None and adapter.resource_kind:
            owned.append({"kind": kind, "resource_ids": ids,
                          "resource_ref": {"resource_id": ids[0]}})
        elif kind in binding_kinds:
            bound.append({"kind": kind, "resource_ids": ids,
                          "binding": MetaPlannerIRResourceBinding(
                              target_ref="xpert_agent", kind=kind, resource_id=ids[0],
                          ).model_dump(mode="json", exclude_defaults=True)})
    return {
        "examples_are_fragments": True,
        "node_owned_resources": owned,
        "agent_bound_resources": bound,
        "rules": [
            "节点自有资源只写在 nodes[].resource_ref={resource_id:授权ID}；类型由 Adapter 决定，不添加 kind/target_ref。",
            "顶层 resources 只用于向 workflow_agent 绑定 agent_bound_resources 中的资源；该目录为空时必须使用空数组。Agent Table 不属于绑定资源。",
            "目录是授权选择范围，不是必须使用的资源；示例 ref 必须替换为实际 Agent ref，不能据此增加任务。",
            "查询与写操作授权互不隐含；写入的字段、操作和行数仍须遵守 data_table_write_grants。",
        ],
    }


def scope_resource_schema(schema: dict[str, Any], request: Any, snapshot: Any) -> None:
    """Constrain model choices, without replacing server-side authorization."""
    contract = resource_generation_contract(request, snapshot)
    bindings = contract["agent_bound_resources"]
    schema["properties"]["resources"] = {
        "type": "array", "maxItems": 40 if bindings else 0,
        "items": {"oneOf": [{"allOf": [
            {"$ref": "#/$defs/MetaPlannerIRResourceBinding"},
            {"properties": {"kind": {"const": item["kind"]},
                            "resource_id": {"enum": item["resource_ids"]}}},
        ]} for item in bindings]} if bindings else False,
    }
    choices = {item["kind"]: item["resource_ids"] for item in contract["node_owned_resources"]}
    for name, node in schema["$defs"].items():
        if not name.startswith("ModelNode_"):
            continue
        kind = name.removeprefix("ModelNode_")
        adapter = get_planner_node_adapter(kind)
        if adapter is None or not adapter.resource_kind:
            continue
        # GraphIntent uses allOf; Recipe keeps its complete node shape inline.
        target = node["allOf"][-1] if "allOf" in node else node
        target["properties"]["resource_ref"] = {"allOf": [
            {"$ref": "#/$defs/GraphIntentNodeResourceRefV3"},
            {"properties": {"resource_id": {"enum": choices.get(kind, [])}}},
        ]} if choices.get(kind) else False


def generation_resource_ids(request: Any, snapshot: Any) -> set[str]:
    contract = resource_generation_contract(request, snapshot)
    return {resource_id for group in ("node_owned_resources", "agent_bound_resources")
            for item in contract[group] for resource_id in item["resource_ids"]}
