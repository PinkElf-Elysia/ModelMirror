import json

import pytest
from pydantic import ValidationError

from server.meta_agent.generation_contract import generation_task_plan_schema, parse_generation_task_plan
from server.meta_agent.graph_ir_v3 import resolve_node_resource_snapshot
from server.meta_agent.meta_planner_v2 import MetaPlannerV2Service
from server.meta_agent.node_adapters import get_planner_node_adapter
from server.tests.test_meta_planner_controlled_writes import _plan, intent, request, snapshot
from server.tests.test_meta_planner_repair_context import offline_only
from server.tests.test_meta_planner_table_prompt_contract import _contracts
from server.workflow_native.node_contracts import WorkflowValueSchema


@pytest.mark.parametrize("repair", [False, True])
def test_task_field_fragments_are_short_and_derived_from_the_task_schema(repair):
    snap = snapshot()
    req = request(snap)
    raw = (MetaPlannerV2Service._plan_repair_prompt(req, snap, "{}", ["invalid"]) if repair
           else MetaPlannerV2Service._plan_prompt(req, snap))
    fields = json.loads(raw)["task_planning_contract"]["task_field_shapes"]
    properties = generation_task_plan_schema()["$defs"]["GenerationTask"]["properties"]
    assert set(fields) == {"input_contract", "output_contract"}
    assert fields["input_contract"]["type"] == properties["input_contract"]["type"] == "array"
    assert fields["output_contract"]["type"] == properties["output_contract"]["type"] == "string"
    assert isinstance(fields["input_contract"]["example"], list)
    assert isinstance(fields["output_contract"]["example"], str)
    assert len(json.dumps(fields, ensure_ascii=False)) < 300
    for invalid in ("not a list", None, {}):
        task = _plan().model_dump(mode="json")
        task["tasks"][0]["input_contract"] = invalid
        with pytest.raises(ValidationError):
            parse_generation_task_plan(task)


def _shape(schema):
    return {"type": schema.type, "nullable": schema.nullable,
            **({"items": _shape(schema.items)} if schema.items else {})}


@pytest.mark.parametrize("kind,config,expected", [
    ("data_table_query", {"return_mode": "first"}, {"type": "object", "nullable": True}),
    ("data_table_query", {"return_mode": "list"}, {"type": "array", "nullable": False,
        "items": {"type": "object", "nullable": False}}),
    ("data_table_insert", {"value_source": "literal", "values": {}}, {"type": "object", "nullable": False}),
])
def test_adapter_knows_result_shape_before_fields_are_resolved(kind, config, expected):
    adapter = get_planner_node_adapter(kind)
    parsed = adapter.config_model.model_validate(config)
    schema = adapter.authoritative_output_schema("result", parsed)
    assert _shape(schema) == expected and not schema.any_of
    assert not schema.properties
    graph = intent("insert" if kind == "data_table_insert" else "update")
    node = next(node for node in graph.nodes if node.kind == kind)
    node.config = config
    resource = resolve_node_resource_snapshot(node, snapshot()).model_dump(mode="json")
    resolved = adapter.authoritative_output_schema("result", parsed, resource)
    assert _shape(schema) == _shape(resolved)
    assert (resolved.items or resolved).properties


@pytest.mark.parametrize("stage", ["generate", "full_repair", "patch_repair"])
def test_all_model_stages_share_exact_adapter_shape_fragments(stage):
    contracts = _contracts(stage)
    query = contracts["data_table_query"]["output_binding_contract"]
    assert set(query["value_schema_by_return_mode"]) == {"first", "list"}
    adapter = get_planner_node_adapter("data_table_query")
    for mode, shape in query["value_schema_by_return_mode"].items():
        assert WorkflowValueSchema.model_validate(shape) == adapter.authoritative_output_schema(
            "result", adapter.config_model(return_mode=mode),
        )
    insert = contracts["data_table_insert"]["output_binding_contract"]
    assert insert["value_schema"] == {"type": "object"}
    for kind in ("data_table_query", "data_table_insert", "data_table_update", "data_table_delete"):
        ports = contracts[kind]["ports"]
        assert [(port["name"], port["direction"]) for port in ports] == [("result", "output")]
        authoritative = get_planner_node_adapter(kind).intent_port_contracts("output")[0]
        assert WorkflowValueSchema.model_validate(ports[0]["value_schema"]) == authoritative.value_schema
        assert ports[0]["value_schema"] == authoritative.value_schema.model_dump(mode="json", exclude_defaults=True)
    for kind in ("data_table_update", "data_table_delete"):
        assert contracts[kind]["input_binding_contract"]["records_schema_from"] == "source.output_binding_contract"
        assert "any_of" in " ".join(contracts[kind]["input_binding_contract"]["rules"])
