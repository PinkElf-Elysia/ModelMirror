import json

from jsonschema import Draft202012Validator

from server.meta_agent.generation_contract import compact_generation_schema
from server.meta_agent.meta_planner_v2 import MetaPlannerV2Service
from server.tests.test_meta_planner_controlled_writes import _plan, intent, request, snapshot


def test_schema_compaction_only_removes_annotations_not_business_properties():
    original = {"title": "Displayed title", "type": "object", "properties": {
        "title": {"title": "Displayed title", "type": "string", "const": "业务标题"},
        "payload": {"type": "object", "const": {"title": "固定值"}},
    }, "required": ["title"], "additionalProperties": False,
        "default": {"title": "不能删除的默认值"}, "examples": [{"title": "不能删除的示例"}]}
    projected = compact_generation_schema(original)
    assert "title" not in projected and "title" in projected["properties"]
    assert projected["default"] == original["default"] and projected["examples"] == original["examples"]
    for case in ({}, {"title": 5}, {"title": "业务标题"}, {"title": "业务标题", "payload": {"title": "固定值"}},
                 {"title": "业务标题", "payload": {}}, {"title": "业务标题", "extra": 1}):
        assert Draft202012Validator(original).is_valid(case) == Draft202012Validator(projected).is_valid(case)


def test_repair_is_one_patch_protocol_without_conflicting_complete_graph_instruction():
    snap = snapshot()
    req = request(snap)
    graph = intent("update")
    graph.nodes[1].inputs.clear()
    prompt = json.loads(MetaPlannerV2Service._patch_repair_prompt(req, _plan(), snap, graph, ["invalid"]))
    assert "typed_ir_constraints" not in prompt
    assert "existing_data_edges" not in prompt["repair_contract"]
    assert "capability_snapshot_hash" not in prompt
    assert prompt["authorized_scope"] == req.scope.model_dump(mode="json")
    assert prompt["base_graph_intent"] == graph.model_dump(mode="json")
    assert prompt["required_schema"]["properties"]["operations"]
    assert prompt["input_role_contract"]["response_root_fields"] == ["operations"]
    assert "Return a complete typed blueprint, not a patch" not in json.dumps(prompt)
    assert prompt["validation_frontier"]["failed_phase"] == "authorization"
    assert any(item["status"] == "blocked" for item in prompt["validation_frontier"]["phase_results"])


def test_full_repair_does_not_repeat_resource_catalog_or_snapshot_identity():
    snap = snapshot()
    prompt = json.loads(MetaPlannerV2Service._repair_prompt(request(snap), _plan(), snap, "{}", ["invalid"]))
    assert "available_resources" not in prompt and "capability_snapshot_hash" not in prompt
    assert prompt["capability_snapshot"]["resources"]["data_tables"]
    assert "authorized" not in prompt["graph_intent_contract"]
    assert "data_table_write_grants" not in prompt["graph_intent_contract"]
    assert prompt["authorized_scope"]["data_table_write_grants"]


def test_task_planner_does_not_receive_graph_config_responsibility():
    snap = snapshot()
    prompt = json.loads(MetaPlannerV2Service._plan_prompt(request(snap), snap))
    contracts = prompt["task_planning_contract"]["auxiliary_node_contracts"]
    assert contracts
    assert all(contract["task_binding"] == "forbidden" for contract in contracts)
    assert all(contract["config_deferred_to_graph_compilation"] for contract in contracts)
    assert all("config_field_contract" not in contract and "input_binding_contract" not in contract for contract in contracts)
