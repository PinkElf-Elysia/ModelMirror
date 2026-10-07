from __future__ import annotations

import copy
import hashlib
import itertools
from dataclasses import dataclass
from typing import Any, Callable

from .control_data import WorkflowTerminationError
from .node_contracts import (
    DATA_TABLE_WRITE_KINDS,
    WorkflowValueSchema,
    canonical_checksum,
    validate_controlled_write_authority,
)


MAX_PRIVATE_WRITE_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class EvaluationWriteBackendContext:
    backend: Any
    run_id: str
    item_id: str
    fixture_checksum: str
    target_id: str
    case_id: str
    workflow_checksum: str
    receipt_callback: Callable[[dict[str, Any]], None] | None = None
    dispatch_guard: Callable[[str, str, str], None] | None = None


def node_kind(node: dict[str, Any]) -> str:
    return str((node.get("data") or {}).get("kind") or node.get("type") or "")


def has_controlled_writes(workflow: dict[str, Any]) -> bool:
    return any(node_kind(node) in DATA_TABLE_WRITE_KINDS and (node.get("data") or {}).get("contractVersion") == 2 for node in workflow.get("nodes", []))


def write_contract_checksum(node: dict[str, Any]) -> str:
    data = node.get("data") or {}
    return canonical_checksum({"node_id": node["id"], "kind": node_kind(node), "data": {key: value for key, value in data.items() if key not in {"title", "description"}}})


def write_workflow_checksum(workflow: dict[str, Any]) -> str:
    return canonical_checksum({
        "nodes": [{"id": node["id"], "kind": node_kind(node), "data": node.get("data") or {}} for node in workflow.get("nodes", [])],
        "edges": [{key: edge.get(key) for key in ("id", "source", "target", "sourceHandle", "targetHandle")} for edge in workflow.get("edges", [])],
    })


def validate_write_execution_context(workflow: dict[str, Any], *, run_type: str, metadata: dict[str, Any], context: EvaluationWriteBackendContext | None) -> None:
    writes = [node for node in workflow.get("nodes", []) if node_kind(node) in DATA_TABLE_WRITE_KINDS]
    if run_type == "xpert_evaluation" and writes:
        if any(node.get("data", {}).get("contractVersion") != 2 for node in writes):
            raise ValueError("评测禁止旧版或混合版本写节点，不能使用业务 Backend。")
        if not isinstance(context, EvaluationWriteBackendContext):
            raise ValueError("受控写入评测必须使用服务端固定的私有隔离表，禁止回退业务表。")
    if context is not None:
        if run_type != "xpert_evaluation" or not isinstance(context, EvaluationWriteBackendContext):
            raise ValueError("隔离 Backend 只能用于服务端评测执行。")
        identity = (metadata.get("evaluation_run_id"), metadata.get("evaluation_item_id"), metadata.get("evaluation_target_id"), metadata.get("evaluation_case_id"))
        if identity != (context.run_id, context.item_id, context.target_id, context.case_id) or write_workflow_checksum(workflow) != context.workflow_checksum:
            raise ValueError("隔离执行的目标、用例或工作流与固定实例不匹配。")
    if has_controlled_writes(workflow):
        if run_type not in {"workflow", "xpert", "goal", "handoff", "xpert_evaluation"}:
            raise ValueError("当前运行入口不允许受控数据表写入。")
        validate_native_write_sources(workflow)


def validate_native_write_sources(workflow: dict[str, Any]) -> None:
    nodes = list(workflow.get("nodes") or [])
    if not has_controlled_writes(workflow):
        return
    refs = [node.get("data", {}).get("plannerRef") for node in nodes if node_kind(node) in DATA_TABLE_WRITE_KINDS and node.get("data", {}).get("plannerRef")]
    if len(set(refs)) != len(refs):
        raise ValueError("写节点的 plannerRef 不能重复，必须在事务前唯一确定效果归属。")
    ancestors, scenarios = _control_paths(workflow)
    table_nodes = [node for node in nodes if node_kind(node) in DATA_TABLE_WRITE_KINDS | {"data_table_query"}]
    for index, left in enumerate(table_nodes):
        for right in table_nodes[index + 1:]:
            if node_kind(left) == node_kind(right) == "data_table_query":
                continue
            if left["data"].get("tableId") != right["data"].get("tableId"):
                continue
            if any(left["id"] in reached and right["id"] in reached for reached in scenarios):
                if left["id"] not in ancestors[right["id"]] and right["id"] not in ancestors[left["id"]]:
                    raise ValueError("同一路径上的同表冲突读写必须有明确控制先后。")
    for node in nodes:
        kind = node_kind(node)
        data = node.get("data") or {}
        if kind not in DATA_TABLE_WRITE_KINDS or data.get("contractVersion") != 2:
            continue
        validate_controlled_write_authority(kind, data)
        if kind != "data_table_insert":
            sources = [item for item in nodes if (item.get("data") or {}).get("outputVariable") == data.get("recordsVariable")]
            if len(sources) != 1 or node_kind(sources[0]) not in {"data_table_query", "data_table_insert"}:
                raise ValueError("更新或删除的记录必须直接来自同一工作流的唯一查询或插入节点。")
            source_data = sources[0].get("data") or {}
            if node_kind(sources[0]) == "data_table_insert" and source_data.get("contractVersion") != 2:
                raise ValueError("受控写入的插入来源必须使用 V2 回执契约。")
            _require_source_path(sources[0]["id"], node["id"], ancestors, scenarios)
            if source_data.get("tableId") != data.get("tableId"):
                raise ValueError("受控写入不能使用跨表记录。")
            if source_data.get("versionPolicy") == "pinned" and source_data.get("pinnedSchemaVersion") != data.get("pinnedSchemaVersion"):
                raise ValueError("受控写入与记录来源必须固定同一 Schema。")
        if kind != "data_table_delete" and data.get("valueSource") == "input":
            sources = [item for item in nodes if (item.get("data") or {}).get("outputVariable") == data.get("valuesVariable")]
            if len(sources) != 1 or node_kind(sources[0]) != "json_deserialize" or (sources[0].get("data") or {}).get("contractVersion") != 2:
                raise ValueError("业务值对象必须来自已执行的 JSON Deserialize V2 校验节点。")
            _require_source_path(sources[0]["id"], node["id"], ancestors, scenarios)
            schema = WorkflowValueSchema.model_validate(sources[0]["data"].get("expectedSchema"))
            if schema.type != "object" or schema.nullable or not schema.properties:
                raise ValueError("写入业务值需要明确业务字段的非空对象 Schema。")
            grant = validate_controlled_write_authority(kind, data)
            if set(schema.properties) - set(grant.writable_fields):
                raise ValueError("业务值 Schema 包含未授权字段。")


def _require_source_path(source: str, target: str, ancestors: dict[str, set[str]], scenarios: list[set[str]]) -> None:
    if source not in ancestors[target] or any(target in reached and source not in reached for reached in scenarios):
        raise ValueError("写入输入来源必须在每条到达写节点的路径中先执行。")


def _assert_business_object(schema: WorkflowValueSchema, value: Any) -> None:
    schema.assert_value(value)
    if not isinstance(value, dict) or set(value) - set(schema.properties or {}):
        raise ValueError("写入业务对象包含 JSON Schema 未声明的字段。")


def _control_paths(workflow: dict[str, Any]) -> tuple[dict[str, set[str]], list[set[str]]]:
    nodes = {node["id"]: node for node in workflow.get("nodes", [])}
    bindings = {"expert", "knowledge", "toolset", "plugin", "middleware"}
    edges = [edge for edge in workflow.get("edges", []) if edge.get("targetHandle") not in bindings]
    parents: dict[str, set[str]] = {key: set() for key in nodes}
    for edge in edges:
        if edge.get("source") not in nodes or edge.get("target") not in nodes:
            raise ValueError("受控写入的控制边存在未知节点。")
        parents[edge["target"]].add(edge["source"])
    ancestors: dict[str, set[str]] = {}
    for key in nodes:
        pending = list(parents[key])
        seen: set[str] = set()
        while pending:
            current = pending.pop()
            if current == key:
                raise ValueError("受控写入不允许控制循环。")
            if current not in seen:
                seen.add(current)
                pending.extend(parents[current])
        ancestors[key] = seen
    choices: dict[str, list[str]] = {}
    for key, node in nodes.items():
        kind, data = node_kind(node), node.get("data") or {}
        if kind == "condition":
            choices[key] = ["true", "false"]
        elif kind == "multi_route":
            choices[key] = [str(route.get("id")) for route in data.get("routes", [])] + ["default"]
        elif data.get("failureStrategy") == "error_output" or data.get("failureAction") == "error_output":
            choices[key] = ["", "error"]
    scenario_count = 1
    for outcomes in choices.values():
        scenario_count *= len(outcomes)
    if len(choices) > 8 or scenario_count > 256:
        raise ValueError("受控写入路径分析超过 8 个路由或 256 个场景。")
    # Classic graphs may start at an ordinary root instead of an input node.
    roots = {key for key in nodes if not parents[key]}
    scenarios = []
    for values in itertools.product(*choices.values()):
        outcomes = dict(zip(choices, values))
        reached = set(roots)
        changed = True
        while changed:
            changed = False
            for edge in edges:
                source, target = edge["source"], edge["target"]
                handle = str(edge.get("sourceHandle") or "")
                if source in reached and target not in reached and (source not in outcomes or outcomes[source] == handle):
                    reached.add(target)
                    changed = True
        scenarios.append(reached)
    return ancestors, scenarios


class ControlledWriteRuntime:
    """Internal execution context; never deserialized from an HTTP request."""

    def __init__(self, *, task_id: str, workflow: dict[str, Any], backend: Any, execution_store: Any, isolated: bool = False, receipt_callback: Callable[[dict[str, Any]], None] | None = None, dispatch_guard: Callable[[str, str, str], None] | None = None) -> None:
        validate_native_write_sources(workflow)
        self.task_id = task_id
        self.workflow = copy.deepcopy(workflow)
        self.nodes = {node["id"]: node for node in self.workflow.get("nodes", [])}
        self.backend = backend
        self.execution_store = execution_store
        self.isolated = isolated
        self.receipt_callback = receipt_callback
        self.dispatch_guard = dispatch_guard
        self.effects: list[dict[str, Any]] = []
        self.receipts: list[dict[str, Any]] = []
        self.workflow_checksum = write_workflow_checksum(self.workflow)

    def _read(self, key: str) -> dict[str, Any] | None:
        entry = self.execution_store.read_private_write_entry(self.task_id, key)
        if entry is not None and entry.get("workflow_checksum") != self.workflow_checksum:
            raise ValueError("受控写入恢复时工作流快照已发生变化。")
        return entry

    def _freeze(self, key: str, entry: dict[str, Any]) -> dict[str, Any]:
        return self.execution_store.freeze_private_write_entry(self.task_id, key, {"workflow_checksum": self.workflow_checksum, **entry})

    def _producer(self, variable: str) -> dict[str, Any]:
        matches = [node for node in self.nodes.values() if (node.get("data") or {}).get("outputVariable") == variable]
        if len(matches) != 1:
            raise ValueError("业务值或记录变量没有唯一可信生产节点。")
        return matches[0]

    def capture_validated_value(self, node_id: str, value: Any) -> None:
        node = self.nodes[node_id]
        data = node.get("data") or {}
        if node_kind(node) != "json_deserialize" or data.get("contractVersion") != 2:
            raise ValueError("只有 JSON Deserialize V2 可以登记业务值验证结果。")
        schema = WorkflowValueSchema.model_validate(data.get("expectedSchema"))
        schema.assert_value(value)
        if any(node_kind(target) in DATA_TABLE_WRITE_KINDS and target.get("data", {}).get("valueSource") == "input" and target["data"].get("valuesVariable") == data.get("outputVariable") for target in self.nodes.values()):
            _assert_business_object(schema, value)
        self._freeze("value:" + node_id, {"value_checksum": canonical_checksum(value), "schema_checksum": canonical_checksum(schema), "node_contract_checksum": write_contract_checksum(node)})

    def query(self, node_id: str, *, filter_tree: dict[str, Any] | None) -> tuple[list[dict[str, Any]], Any]:
        node = self.nodes[node_id]
        data = node["data"]
        if node_kind(node) != "data_table_query":
            raise ValueError("只有真实查询节点可以登记查询回执。")
        schema = self.backend.resolve_schema_version(data["tableId"], version_policy=str(data.get("versionPolicy") or "latest"), pinned_version=data.get("pinnedSchemaVersion"), write=False)
        if data.get("pinnedSchemaChecksum") and data["pinnedSchemaChecksum"] != schema.checksum:
            raise ValueError("查询 Schema 校验和已发生变化。")
        contract = {"node_contract_checksum": write_contract_checksum(node), "table_id": data["tableId"], "schema_version": schema.version, "schema_checksum": schema.checksum, "filter": filter_tree}
        prior = self._read("source:" + node_id)
        if prior is not None:
            if prior.get("contract") != contract:
                raise ValueError("已执行查询的固定契约发生变化，不能重新读取活表。")
            return copy.deepcopy(prior["records"]), schema
        if self.dispatch_guard is not None:
            self.dispatch_guard(str(data.get("plannerRef") or ""), "query", canonical_checksum(contract))
        records = self.backend.query_records(data["tableId"], schema_version=schema.version, fields=list(data.get("selectFields") or []), filter_tree=filter_tree, sort=list(data.get("sort") or []), limit=int(data.get("limit") or 20))
        output = records[0] if data.get("returnMode") == "first" and records else (None if data.get("returnMode") == "first" else records)
        self._freeze("source:" + node_id, {"contract": contract, "records": records, "value_checksum": canonical_checksum(output)})
        return records, schema

    def _request(self, node: dict[str, Any], variables: dict[str, Any], resolve_filter: Callable[..., Any]) -> dict[str, Any]:
        data = node["data"]
        kind = node_kind(node)
        grant = validate_controlled_write_authority(kind, data)
        expected = []
        if kind != "data_table_insert":
            producer = self._producer(data["recordsVariable"])
            source = self._read("source:" + producer["id"])
            if source is None or source.get("value_checksum") != canonical_checksum(variables.get(data["recordsVariable"])):
                raise ValueError("记录来源尚未真实执行或输出已被替换。")
            contract = source["contract"]
            if (contract["table_id"], contract["schema_version"], contract["schema_checksum"]) != (data["tableId"], data["pinnedSchemaVersion"], data["pinnedSchemaChecksum"]):
                raise ValueError("记录回执与写入目标的表或 Schema 不匹配。")
            if len(source["records"]) > 200:
                raise ValueError("可信记录输入超过 200 行。")
            selected = variables.get(data["recordsVariable"])
            selected_records = selected if isinstance(selected, list) else ([] if selected is None else [selected])
            expected = [{"record_id": item["record_id"], "revision": item["revision"]} for item in selected_records]
        values = None
        if kind != "data_table_delete":
            if data["valueSource"] == "literal":
                values = copy.deepcopy(data["literalValues"])
            else:
                producer = self._producer(data["valuesVariable"])
                proof = self._read("value:" + producer["id"])
                values = copy.deepcopy(variables.get(data["valuesVariable"]))
                if proof is None or proof["value_checksum"] != canonical_checksum(values) or proof["node_contract_checksum"] != write_contract_checksum(producer):
                    raise ValueError("业务值尚未通过真实 JSON V2 校验或校验后已被替换。")
                _assert_business_object(WorkflowValueSchema.model_validate(producer["data"]["expectedSchema"]), values)
            if not isinstance(values, dict) or set(values) - set(grant.writable_fields):
                raise ValueError("业务值必须是仅包含授权字段的对象。")
        return {"operation": kind.removeprefix("data_table_"), "table_id": data["tableId"], "schema_version": data["pinnedSchemaVersion"], "schema_checksum": data["pinnedSchemaChecksum"], "data": values, "filter": resolve_filter(data.get("filter"), variables), "expected_records": expected, "writable_fields": list(grant.writable_fields) if kind != "data_table_delete" else [], "max_affected_rows": data.get("maxAffectedRows", 1)}

    def execute(self, node_id: str, variables: dict[str, Any], *, resolve_filter: Callable[..., Any]) -> dict[str, Any]:
        try:
            from server.data_tables.store import controlled_write_request_checksum
        except ModuleNotFoundError:
            from data_tables.store import controlled_write_request_checksum
        node = self.nodes[node_id]
        kind = node_kind(node)
        data = node["data"]
        validate_controlled_write_authority(kind, data)
        contract_checksum = write_contract_checksum(node)
        journal = self._read("request:" + node_id)
        if journal is None:
            request = self._request(node, variables, resolve_filter)
            journal = self._freeze("request:" + node_id, {"contract_checksum": contract_checksum, "request_checksum": controlled_write_request_checksum(request), "request": request})
        if journal["contract_checksum"] != contract_checksum or journal["request_checksum"] != controlled_write_request_checksum(journal["request"]):
            raise ValueError("恢复的写入请求或契约校验和不匹配。")
        request = journal["request"]
        operation_id = "workflow_" + hashlib.sha256(f"{self.task_id}:{node_id}".encode("utf-8")).hexdigest()
        result = self.backend.get_controlled_operation(request["table_id"], operation_id, request_checksum=journal["request_checksum"])
        if result is None:
            if self.dispatch_guard is not None:
                self.dispatch_guard(str(data.get("plannerRef") or ""), request["operation"], journal["request_checksum"])
            result = self.backend.execute_controlled_write(request["table_id"], operation=request["operation"], schema_version=request["schema_version"], schema_checksum=request["schema_checksum"], data=request["data"], filter_tree=request["filter"], expected_records=request["expected_records"], writable_fields=request["writable_fields"], max_affected_rows=request["max_affected_rows"], operation_id=operation_id, request_checksum=journal["request_checksum"], capture_effects=self.isolated, max_logical_bytes=MAX_PRIVATE_WRITE_BYTES if self.isolated else None)
        receipt = result.get("receipt") or {}
        for key in ("table_id", "operation", "schema_version", "schema_checksum"):
            if receipt.get(key) != request[key]:
                raise ValueError("Backend 写入回执与固定请求不符。")
        if receipt.get("request_checksum") != journal["request_checksum"] or receipt.get("operation_id") != operation_id:
            raise ValueError("Backend 写入回执身份不符。")
        self.receipts.append(copy.deepcopy(receipt))
        if self.isolated:
            self._capture_effect(node, result, journal["request_checksum"], contract_checksum)
        if result.get("error_code"):
            raise WorkflowTerminationError(str(result["error_code"]), "记录 revision 已变化，当前写节点未提交；先前成功写入保留。", node_id=node_id)
        if kind == "data_table_insert":
            self._freeze("source:" + node_id, {"contract": {"node_contract_checksum": contract_checksum, "table_id": request["table_id"], "schema_version": request["schema_version"], "schema_checksum": request["schema_checksum"], "filter": None}, "records": [result["output"]], "value_checksum": canonical_checksum(result["output"])})
        return result

    def _capture_effect(self, node: dict[str, Any], result: dict[str, Any], request_checksum: str, contract_checksum: str) -> None:
        receipt = result["receipt"]
        code = result.get("error_code")
        count = receipt["affected_count"]
        safe = {"node_ref": str(node["data"].get("plannerRef") or ""), "table_id": receipt["table_id"], "operation": receipt["operation"], "schema_version": receipt["schema_version"], "contract_checksum": contract_checksum, "request_checksum": request_checksum, "status": "conflict" if code else ("applied" if count else "noop"), "affected_count": count, "replayed": bool(result.get("replayed")), "error_code": code}
        if any(item["node_ref"] == safe["node_ref"] for item in self.effects):
            raise ValueError("同一写节点不能重复产生效果计分。")
        if not isinstance(result.get("private_effect"), dict):
            raise ValueError("隔离写入缺少可恢复的私有 Backend 效果证据。")
        self.effects.append({**safe, "receipt": dict(safe), "output": copy.deepcopy(result.get("output")), "private_effect": copy.deepcopy(result["private_effect"])})
        if self.receipt_callback is not None:
            self.receipt_callback(safe)
