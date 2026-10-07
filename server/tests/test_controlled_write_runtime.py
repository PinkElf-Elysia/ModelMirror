from copy import deepcopy

import pytest

from server.data_tables.store import AgentTableStore
from server.evaluations.resource_fixtures import _resolve_filter
from server.workflow_native.controlled_writes import ControlledWriteRuntime, validate_native_write_sources, validate_write_execution_context
from server.workflow_native.control_data import WorkflowTerminationError
from server.workflow_native.schemas import NativeWorkflowDefinition
from server.workflow_native.validate import validate_workflow_graph
from server.xpert_runtime.execution_store import WorkflowExecutionStore


def setup_table(tmp_path):
    backend = AgentTableStore(tmp_path / "tables")
    table = backend.create_table(name="合成事件", fields=[{"name": "title", "data_type": "string", "required": True}, {"name": "status", "data_type": "string", "has_default": True, "default_value": "open"}])
    schema = backend.publish_table(table.table_id, revision=table.draft_revision)
    return backend, table.table_id, schema


def workflow(table_id, schema, operation="update", *, inputs=False):
    common = {"tableId": table_id, "versionPolicy": "pinned", "pinnedSchemaVersion": schema.version, "pinnedSchemaChecksum": schema.checksum}
    write = {**common, "kind": f"data_table_{operation}", "contractVersion": 2, "plannerRef": "write", "writeGrant": {"table_id": table_id, "operations": [operation], "writable_fields": ["title", "status"] if operation != "delete" else [], "max_affected_rows": 1}, "maxAffectedRows": 1, "failureAction": "stop", "retryMode": "none", "outputVariable": "written"}
    nodes = [{"id": "input", "type": "input", "data": {"kind": "input", "variableName": "user_input"}}]
    if operation != "insert":
        nodes.append({"id": "query", "type": "data_table_query", "data": {**common, "kind": "data_table_query", "plannerRef": "query", "outputVariable": "rows", "returnMode": "list", "limit": 20}})
        write.update(recordsVariable="rows", filter={"field": "title", "operator": "eq", "value": {"source": "literal", "value": "first"}})
    if operation != "delete":
        if inputs:
            nodes.append({"id": "decode", "type": "json_deserialize", "data": {"kind": "json_deserialize", "contractVersion": 2, "inputVariable": "user_input", "outputVariable": "values", "expectedSchema": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}}})
            write.update(valueSource="input", valuesVariable="values")
        else:
            write.update(valueSource="literal", literalValues={"title": "first"} if operation == "insert" else {"status": "closed"})
    nodes.append({"id": "write", "type": write["kind"], "data": write})
    nodes.append({"id": "output", "type": "output", "data": {"kind": "output", "outputVariable": "written"}})
    for index, node in enumerate(nodes):
        node["position"] = {"x": index * 200, "y": 0}
    return {"id": "controlled", "title": "受控写入", "nodes": nodes, "edges": [{"id": f"e{index}", "source": a["id"], "target": b["id"]} for index, (a, b) in enumerate(zip(nodes, nodes[1:]))]}


def runtime(tmp_path, backend, graph, *, isolated=False):
    store = WorkflowExecutionStore(tmp_path / "execution.json")
    store.create(task_id="task-fixed", run_id="run", run_type="workflow", workflow=graph, inputs={})
    return ControlledWriteRuntime(task_id="task-fixed", workflow=graph, backend=backend, execution_store=store, isolated=isolated), store


def seed(backend, table_id, schema, title="first"):
    return backend.create_record_for_schema(table_id, schema_version=schema.version, data={"title": title}, operation_id=f"seed-{title}")


def test_fabricated_or_replaced_records_cannot_write(tmp_path):
    backend, table_id, schema = setup_table(tmp_path)
    record = seed(backend, table_id, schema)
    graph = workflow(table_id, schema)
    runner, _ = runtime(tmp_path, backend, graph)
    with pytest.raises(ValueError, match="尚未真实执行"):
        runner.execute("write", {"rows": [record]}, resolve_filter=_resolve_filter)
    rows, _ = runner.query("query", filter_tree=None)
    tampered = deepcopy(rows)
    tampered[0]["revision"] += 1
    with pytest.raises(ValueError, match="已被替换"):
        runner.execute("write", {"rows": tampered}, resolve_filter=_resolve_filter)
    assert backend.query_records(table_id, schema_version=1)[0]["status"] == "open"


def test_commit_replay_survives_checkpoint_loss_and_schema_advance(tmp_path):
    backend, table_id, schema = setup_table(tmp_path)
    graph = workflow(table_id, schema, "insert")
    runner, store = runtime(tmp_path, backend, graph)
    original = runner.execute("write", {}, resolve_filter=_resolve_filter)
    table = backend.get_table(table_id)
    backend.update_table(table_id, revision=table.draft_revision, patch={"fields": [field.model_dump(mode="json") for field in table.fields] + [{"name": "extra", "data_type": "string"}]})
    backend.publish_table(table_id, revision=backend.get_table(table_id).draft_revision)
    restored_store = WorkflowExecutionStore(tmp_path / "execution.json")
    restored = ControlledWriteRuntime(task_id="task-fixed", workflow=graph, backend=AgentTableStore(tmp_path / "tables"), execution_store=restored_store)
    replay = restored.execute("write", {}, resolve_filter=_resolve_filter)
    assert replay["replayed"] is True
    assert replay["output"] == original["output"]
    assert len(backend.query_records(table_id, schema_version=1)) == 1
    assert "private_write_journal" not in store.serialize_public(store.require("task-fixed"))


def test_first_mode_never_updates_all_query_rows(tmp_path):
    backend, table_id, schema = setup_table(tmp_path)
    seed(backend, table_id, schema)
    seed(backend, table_id, schema, "second")
    graph = workflow(table_id, schema)
    graph["nodes"][1]["data"]["returnMode"] = "first"
    graph["nodes"][1]["data"]["sort"] = [{"field": "title", "direction": "asc"}]
    runner, _ = runtime(tmp_path, backend, graph)
    rows, _ = runner.query("query", filter_tree=None)
    result = runner.execute("write", {"rows": rows[0]}, resolve_filter=_resolve_filter)
    assert result["receipt"]["affected_count"] == 1
    assert sum(record["status"] == "open" for record in backend.query_records(table_id, schema_version=1)) == 1


def test_validated_business_object_must_match_real_json_execution(tmp_path):
    backend, table_id, schema = setup_table(tmp_path)
    graph = workflow(table_id, schema, "insert", inputs=True)
    runner, _ = runtime(tmp_path, backend, graph)
    with pytest.raises(ValueError, match="尚未通过"):
        runner.execute("write", {"values": {"title": "first"}}, resolve_filter=_resolve_filter)
    runner.capture_validated_value("decode", {"title": "first"})
    with pytest.raises(ValueError, match="已被替换"):
        runner.execute("write", {"values": {"title": "other"}}, resolve_filter=_resolve_filter)
    assert runner.execute("write", {"values": {"title": "first"}}, resolve_filter=_resolve_filter)["receipt"]["affected_count"] == 1


def test_json_proof_rejects_undeclared_but_authorized_business_field(tmp_path):
    backend, table_id, schema = setup_table(tmp_path)
    graph = workflow(table_id, schema, "insert", inputs=True)
    runner, _ = runtime(tmp_path, backend, graph)
    with pytest.raises(ValueError, match="未声明"):
        runner.capture_validated_value("decode", {"title": "first", "status": "closed"})
    assert backend.query_records(table_id, schema_version=1) == []


def test_non_write_json_object_keeps_existing_open_schema_semantics(tmp_path):
    backend, table_id, schema = setup_table(tmp_path)
    graph = workflow(table_id, schema, "insert", inputs=True)
    graph["nodes"][-2]["data"].update(valueSource="literal", literalValues={"title": "first"})
    graph["nodes"][-2]["data"].pop("valuesVariable")
    runner, _ = runtime(tmp_path, backend, graph)
    runner.capture_validated_value("decode", {"title": "first", "unrelated": True})
    assert runner.execute("write", {}, resolve_filter=_resolve_filter)["receipt"]["affected_count"] == 1


def test_revision_conflict_preserves_prior_commit_and_private_evidence(tmp_path):
    backend, table_id, schema = setup_table(tmp_path)
    seed(backend, table_id, schema)
    graph = workflow(table_id, schema)
    second = deepcopy(graph["nodes"][-2])
    second["id"] = "again"
    second["data"].update(plannerRef="again", outputVariable="again_result")
    graph["nodes"].insert(-1, second)
    graph["edges"][-1]["target"] = "again"
    graph["edges"].append({"id": "last", "source": "again", "target": "output"})
    runner, _ = runtime(tmp_path, backend, graph, isolated=True)
    rows, _ = runner.query("query", filter_tree=None)
    runner.execute("write", {"rows": rows}, resolve_filter=_resolve_filter)
    with pytest.raises(WorkflowTerminationError) as error:
        runner.execute("again", {"rows": rows}, resolve_filter=_resolve_filter)
    assert error.value.code == "DATA_TABLE_REVISION_CONFLICT"
    assert [item["status"] for item in runner.effects] == ["applied", "conflict"]
    assert backend.query_records(table_id, schema_version=1)[0]["status"] == "closed"


def test_native_unordered_access_is_rejected(tmp_path):
    _, table_id, schema = setup_table(tmp_path)
    graph = workflow(table_id, schema)
    graph["edges"] = [{"id": "iq", "source": "input", "target": "query"}, {"id": "iw", "source": "input", "target": "write"}, {"id": "wo", "source": "write", "target": "output"}]
    with pytest.raises(ValueError, match="控制先后"):
        validate_native_write_sources(graph)


@pytest.mark.parametrize("ordered", [False, True])
def test_control_roots_without_input_cannot_bypass_write_ordering(tmp_path, ordered):
    backend, table_id, schema = setup_table(tmp_path)
    graph = workflow(table_id, schema, "insert")
    graph["nodes"] = graph["nodes"][1:]
    second = deepcopy(graph["nodes"][0])
    second["id"] = "second"
    second["data"].update(plannerRef="second", outputVariable="second_result")
    graph["nodes"].insert(1, second)
    graph["nodes"].insert(0, {
        "id": "root", "type": "json_serialize", "position": {"x": 0, "y": 0},
        "data": {"kind": "json_serialize", "contractVersion": 2,
                 "inputVariable": "user_input", "outputVariable": "initial"},
    })
    graph["edges"] = [
        {"id": "root-first", "source": "root", "target": "write"},
        {"id": "first", "source": "write", "target": "second" if ordered else "output"},
        {"id": "second", "source": "second", "target": "output"},
    ]
    if not ordered:
        graph["edges"].append({"id": "root-second", "source": "root", "target": "second"})
    if ordered:
        validate_native_write_sources(graph)
    else:
        with pytest.raises(ValueError, match="控制先后"):
            validate_native_write_sources(graph)
    assert backend.query_records(table_id, schema_version=schema.version) == []


def test_v2_validator_does_not_require_legacy_value_bindings(tmp_path):
    _, table_id, schema = setup_table(tmp_path)
    result = validate_workflow_graph(NativeWorkflowDefinition.model_validate(workflow(table_id, schema, "insert")))
    assert not any(issue.code in {"missing_data_table_value_bindings", "controlled_write_contract_invalid", "controlled_write_authority_invalid"} for issue in result.issues), result.issues


@pytest.mark.parametrize("version", [None, 1, "2", True, 3])
@pytest.mark.parametrize("operation", ["insert", "update", "delete"])
def test_evaluation_rejects_all_legacy_write_variants_before_backend_access(version, operation):
    graph = {"nodes": [{"id": "write", "type": f"data_table_{operation}", "data": {"contractVersion": version}}]}
    with pytest.raises(ValueError, match="评测禁止旧版"):
        validate_write_execution_context(graph, run_type="xpert_evaluation", metadata={}, context=None)


def test_duplicate_write_ref_rejected_before_any_transaction(tmp_path):
    backend, table_id, schema = setup_table(tmp_path)
    graph = workflow(table_id, schema, "insert")
    second = deepcopy(graph["nodes"][1])
    second["id"] = "another"
    second["data"]["outputVariable"] = "another_result"
    graph["nodes"].insert(-1, second)
    with pytest.raises(ValueError, match="plannerRef 不能重复"):
        runtime(tmp_path, backend, graph, isolated=True)
    assert backend.query_records(table_id, schema_version=1) == []
