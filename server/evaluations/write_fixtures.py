from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

try:
    from server.workflow_native.controlled_writes import (
        EvaluationWriteBackendContext, has_controlled_writes, node_kind,
        validate_native_write_sources, write_contract_checksum, write_workflow_checksum,
    )
    from server.workflow_native.node_contracts import validate_controlled_write_authority
except ModuleNotFoundError:
    from workflow_native.controlled_writes import (
        EvaluationWriteBackendContext, has_controlled_writes, node_kind,
        validate_native_write_sources, write_contract_checksum, write_workflow_checksum,
    )
    from workflow_native.node_contracts import validate_controlled_write_authority


MAX_WRITE_FIXTURE_BYTES = 16 * 1024 * 1024
WRITE_KINDS = {"data_table_insert", "data_table_update", "data_table_delete"}
SYSTEM_FIELDS = {"record_id", "revision", "created_at", "updated_at"}


def encoded(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def checksum(value: Any) -> str:
    return hashlib.sha256(encoded(value)).hexdigest()


class EvaluationSeedRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    data: dict[str, JsonValue]

    @model_validator(mode="after")
    def bounded_record(self) -> "EvaluationSeedRecord":
        if len(self.data) > 50 or any(name in SYSTEM_FIELDS or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name) for name in self.data):
            raise ValueError("初始化记录只能包含最多 50 个业务字段，不能构造系统身份。")
        if len(encoded(self.data)) > 256 * 1024:
            raise ValueError("单条初始化记录不能超过 256 KiB。")
        return self


class EvaluationTableInitialization(BaseModel):
    model_config = ConfigDict(extra="forbid")
    table_id: str = Field(min_length=1, max_length=200)
    schema_version: int = Field(ge=1, strict=True)
    source: Literal["manual", "synthetic"] = "manual"
    records: list[EvaluationSeedRecord] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def unique_refs(self) -> "EvaluationTableInitialization":
        refs = [item.ref for item in self.records]
        if len(set(refs)) != len(refs):
            raise ValueError("同表初始化记录的局部 ref 必须唯一。")
        return self


def freeze_case_tables(cases: list[dict[str, Any]], backend: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for case in cases:
        seeds = [EvaluationTableInitialization.model_validate(item) for item in case.get("table_initializations", [])]
        if not seeds:
            continue
        if backend is None:
            raise ValueError("数据表 Schema 服务尚未配置，不能发布初始化夹具。")
        if len(seeds) > 20 or len({item.table_id for item in seeds}) != len(seeds):
            raise ValueError("每条用例最多初始化 20 张表，表 ID 不能重复。")
        tables = []
        for seed in sorted(seeds, key=lambda item: item.table_id):
            schema = backend.resolve_schema_version(seed.table_id, version_policy="pinned", pinned_version=seed.schema_version, write=False)
            table = backend.get_table(seed.table_id)
            if table.status == "archived":
                raise ValueError("归档表不能用于新的初始化夹具。")
            records = [{"ref": item.ref, "data": backend.validate_record_for_schema(seed.table_id, schema_version=schema.version, data=item.data)} for item in sorted(seed.records, key=lambda item: item.ref)]
            tables.append({"table_id": seed.table_id, "name": table.name, "schema_version": schema.version, "schema_checksum": schema.checksum, "fields": [field.model_dump(mode="json") for field in schema.fields], "records": records})
        result[case["case_id"]] = {"tables": tables, "checksum": checksum(tables)}
    if len(encoded(result)) > MAX_WRITE_FIXTURE_BYTES:
        raise ValueError("冻结的表初始化夹具总量超过 16 MiB。")
    return result


def inspect_write_node(node: Any, *, backend: Any, nested: bool) -> dict[str, Any]:
    raw = node.model_dump() if hasattr(node, "model_dump") else node
    data = raw["data"]
    if nested:
        raise ValueError("本轮不允许嵌套 Xpert 写入。")
    grant = validate_controlled_write_authority(node_kind(raw), data)
    if backend is None:
        raise ValueError("受控写入缺少权威 Schema Backend。")
    schema = backend.resolve_schema_version(grant.table_id, version_policy="pinned", pinned_version=data["pinnedSchemaVersion"], write=True)
    if schema.checksum != data["pinnedSchemaChecksum"]:
        raise ValueError("写节点固定 Schema 校验和已漂移。")
    backend.validate_workflow_node_contract(grant.table_id, schema_version=schema.version, kind=node_kind(raw), data=data)
    ref = str(data.get("plannerRef") or "")
    if not ref:
        raise ValueError("写入评测需要明确的 Planner ref，不能猜测物理节点 ID。")
    return {"node_id": raw["id"], "node_ref": ref, "table_id": grant.table_id, "operation": node_kind(raw).removeprefix("data_table_"), "schema_version": schema.version, "schema_checksum": schema.checksum, "contract_checksum": write_contract_checksum(raw), "writable_fields": grant.writable_fields, "schema_fields": [field.name for field in schema.fields], "max_affected_rows": data["maxAffectedRows"]}


def validate_write_dataset(targets: list[dict[str, Any]], cases: list[dict[str, Any]], *, dataset: dict[str, Any] | None) -> bool:
    isolated = any(has_controlled_writes(target.get("workflow") or {}) for target in targets)
    if not isolated:
        if any(case.get("table_initializations") for case in cases):
            raise ValueError("表初始化夹具仅用于含受控写入目标的隔离评测。")
        return False
    if not dataset or not cases:
        raise ValueError("受控写入评测必须选择带初始化夹具的已发布评测版本。")
    fixtures = dataset.get("_write_fixtures") or {}
    if dataset.get("table_fixture_checksum") != checksum(fixtures):
        raise ValueError("已发布表初始化夹具的校验和无效。")
    from .resource_fixtures import assert_agent_table_dependencies
    try:
        from server.workflow_native.schemas import NativeWorkflowDefinition
    except ModuleNotFoundError:
        from workflow_native.schemas import NativeWorkflowDefinition
    for target in targets:
        workflow = target.get("workflow") or {}
        validate_native_write_sources(workflow)
        native = NativeWorkflowDefinition.model_validate(workflow)
        query_nodes = [node for node in native.nodes if node_kind(node.model_dump()) == "data_table_query"]
        if assert_agent_table_dependencies(native, query_nodes):
            raise ValueError("隔离评测的查询条件不能依赖前置 Agent 输出。")
        contracts = (target.get("resources") or {}).get("write_contracts") or []
        write_nodes = [node for node in workflow.get("nodes", []) if node_kind(node) in WRITE_KINDS]
        if len(contracts) != len(write_nodes):
            raise ValueError("写入目标缺少完整的配置级效果契约。")
        by_node = {contract["node_id"]: contract for contract in contracts}
        refs = [contract["node_ref"] for contract in contracts]
        if len(by_node) != len(contracts) or len(set(refs)) != len(refs):
            raise ValueError("写入效果契约必须与唯一节点、唯一 ref 一一对应。")
        for node in write_nodes:
            contract = by_node.get(node["id"], {})
            data = node["data"]
            if (contract.get("node_ref"), contract.get("table_id"), contract.get("operation"), contract.get("schema_version"), contract.get("schema_checksum"), contract.get("contract_checksum")) != (data.get("plannerRef"), data.get("tableId"), node_kind(node).removeprefix("data_table_"), data.get("pinnedSchemaVersion"), data.get("pinnedSchemaChecksum"), write_contract_checksum(node)):
                raise ValueError("写入效果契约与固定节点配置不一致。")
        for case in cases:
            fixed = fixtures.get(case["case_id"])
            if not isinstance(fixed, dict) or fixed.get("checksum") != checksum(fixed.get("tables")):
                raise ValueError("固定用例缺少有效表初始化夹具。")
            tables = {table["table_id"]: table for table in fixed["tables"]}
            for node in workflow.get("nodes", []):
                if node_kind(node) not in WRITE_KINDS | {"data_table_query"}:
                    continue
                data = node["data"]
                fixed_table = tables.get(data.get("tableId"))
                if fixed_table is None or (fixed_table["schema_version"], fixed_table["schema_checksum"]) != (data.get("pinnedSchemaVersion") or data.get("evaluationPinnedSchemaVersion"), data.get("pinnedSchemaChecksum") or data.get("evaluationPinnedSchemaChecksum")):
                    raise ValueError("目标使用的每张表都必须有同 Schema 的固定初始化，不能回退活表。")
            expectations = {item["node_ref"]: item for item in case.get("effects", [])}
            for contract in contracts:
                expected = expectations.get(contract["node_ref"])
                if expected is None or (expected["table_id"], expected["operation"]) != (contract["table_id"], contract["operation"]):
                    raise ValueError("每个写节点必须配置同表、同操作的效果断言。")
    return True


def create_isolated_write_context(store: Any, *, run_id: str, item_id: str, target: dict[str, Any], case: dict[str, Any]) -> EvaluationWriteBackendContext | None:
    run = store.require_run(run_id)
    if not run.get("write_isolation"):
        return None
    items = [item for item in run["items"] if item["item_id"] == item_id]
    if len(items) != 1 or run.get("cancel_requested") or items[0]["status"] not in {"running", "pending"}:
        raise ValueError("隔离评测项不可派发或已取消。")
    fixed_target = next((entry for entry in run["targets"] if entry["target_id"] == items[0]["target_id"]), None)
    fixed_case = next((entry for entry in run["dataset"]["cases"] if entry["case_id"] == items[0]["case_id"]), None)
    if fixed_target != target or fixed_case != case:
        raise ValueError("隔离实例与所执行目标或用例不一致，禁止交叉复用。")
    fixed = copy.deepcopy((run.get("_write_fixtures") or {}).get(items[0]["case_id"]))
    if not isinstance(fixed, dict) or fixed.get("checksum") != checksum(fixed.get("tables")):
        raise ValueError("隔离表夹具丢失或被篡改，禁止重新读取业务表。")
    try:
        from server.data_tables.store import AgentTableStore
    except ModuleNotFoundError:
        from data_tables.store import AgentTableStore
    directory = store.storage_dir / "write_instances" / checksum({"run_id": run_id, "item_id": item_id})
    require_existing = store.reserve_write_instance(run_id, item_id, fixed["checksum"])
    # The backend is born empty here; it never receives a business database path.
    backend = AgentTableStore(storage_dir=directory)
    backend.initialize_evaluation_tables(fixed["tables"], initialization_checksum=fixed["checksum"], require_existing=require_existing)
    store.mark_write_instance_ready(run_id, item_id, fixed["checksum"])
    return EvaluationWriteBackendContext(backend=backend, run_id=run_id, item_id=item_id, fixture_checksum=fixed["checksum"], target_id=target["target_id"], case_id=case["case_id"], workflow_checksum=write_workflow_checksum(target["workflow"]), receipt_callback=lambda receipt: store.record_write_receipt(run_id, item_id, receipt), dispatch_guard=lambda ref, operation, request_checksum: store.mark_write_dispatch(run_id, item_id, ref, operation, request_checksum))
