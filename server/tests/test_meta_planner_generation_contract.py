from copy import deepcopy
import json

from jsonschema import Draft202012Validator
import pytest

from server.meta_agent.meta_planner_v2 import (
    MetaPlannerV2Service,
    validate_blueprint_authorization,
)
from server.meta_agent.schemas import GraphIntentV3, MetaPlannerTaskPlan
from server.meta_agent.generation_contract import graph_generation_schema, parse_generation_task_plan
from server.meta_agent.node_adapters import get_planner_node_adapter
from server.tests.test_meta_planner_control_flow import _branch_intent, _route_error_intent, _merge_intent
from server.tests.test_meta_planner_pure_nodes import _aggregate_intent, _pack_intent, _compare_intent
from server.tests.test_meta_planner_read_resources import _knowledge_intent, _table_intent
from server.tests.test_meta_planner_vision_graph import vision_intent, vision_request, vision_snapshot
from server.tests.test_meta_planner_controlled_writes import (
    _plan, intent, request, snapshot,
)


def model_prompts():
    snap = snapshot()
    req = request(snap)
    req.scope.data_table_write_grants[0].operations = ["insert", "update", "delete"]
    return req, snap, [json.loads(raw) for raw in (
        MetaPlannerV2Service._plan_prompt(req, snap),
        MetaPlannerV2Service._plan_repair_prompt(req, snap, "{}", ["tasks missing"]),
        MetaPlannerV2Service._blueprint_prompt(req, _plan(), snap, None),
        MetaPlannerV2Service._repair_prompt(req, _plan(), snap, "{}", ["invalid"]),
        MetaPlannerV2Service._patch_repair_prompt(req, _plan(), snap, intent(), ["invalid"]),
    )]


def accepts(schema, payload):
    Draft202012Validator.check_schema(schema)
    return not list(Draft202012Validator(schema).iter_errors(payload))


def test_task_first_and_repair_share_restricted_schema():
    _, _, prompts = model_prompts()
    schema = prompts[0]["required_schema"]
    assert schema == prompts[1]["required_schema"]
    assert accepts(schema, _plan().model_dump(mode="json"))
    for kind in ("human_input", "approval"):
        bad = _plan().model_dump(mode="json")
        bad["tasks"][0].update(task_type=kind, interaction_prompt="合成确认", output_variable="confirmed")
        assert not accepts(schema, bad)
    for location in ("root", "task"):
        bad = _plan().model_dump(mode="json")
        (bad if location == "root" else bad["tasks"][0])["PRIVATE_EXTRA"] = "canary"
        assert not accepts(schema, bad)


def test_private_task_parser_rejects_unknown_fields_without_changing_legacy_reader():
    from pydantic import ValidationError

    legacy_schema = deepcopy(MetaPlannerTaskPlan.model_json_schema())
    raw = _plan().model_dump(mode="json")
    assert parse_generation_task_plan(raw) == _plan()
    raw["PRIVATE_EXTRA"] = "canary"
    assert MetaPlannerTaskPlan.model_validate(raw) == _plan()
    with pytest.raises(ValidationError):
        parse_generation_task_plan(raw)
    assert MetaPlannerTaskPlan.model_json_schema() == legacy_schema


def test_task_table_summaries_defer_binding_details_to_graph_compilation():
    _, _, prompts = model_prompts()
    graph_contract = prompts[2]["graph_intent_contract"]["node_roles"]["executable_node_contracts"]
    for prompt in prompts[:2]:
        summaries = {item["kind"]: item for item in prompt["task_planning_contract"]["auxiliary_node_contracts"]}
        for kind in ("data_table_query", "data_table_insert", "data_table_update", "data_table_delete"):
            summary = summaries[kind]
            assert "input_binding_contract" not in summary
            assert "output_binding_contract" not in summary
            assert summary["config_deferred_to_graph_compilation"] is True
            assert summary["task_binding"] == "forbidden"
            assert graph_contract[kind]["input_binding_contract"]
            assert graph_contract[kind]["output_binding_contract"]
            assert summary["inputs"] == []  # Config-dependent family names are not real ports.
        output = graph_contract["data_table_update"]["output_binding_contract"]
        assert set(output["value_schema"]["properties"]) == {"matched", "affected"}
    assert prompts[0]["task_planning_contract"] == prompts[1]["task_planning_contract"]


@pytest.mark.parametrize("operation", ["insert", "update", "delete"])
def test_model_schema_keeps_valid_controlled_write_graphs(operation):
    req, snap, prompts = model_prompts()
    raw = intent(operation).model_dump(mode="json")
    for prompt in prompts[2:4]:
        assert accepts(prompt["required_schema"], raw)
    assert validate_blueprint_authorization(req, _plan(), GraphIntentV3.model_validate(raw), snap) == []


@pytest.mark.parametrize("factory", [
    _branch_intent, _route_error_intent, _merge_intent, _aggregate_intent,
    _pack_intent, _compare_intent, _knowledge_intent, _table_intent, vision_intent,
])
def test_private_schema_preserves_existing_node_packs(factory):
    snap = vision_snapshot()
    req = vision_request(snap)
    graph = factory()
    for node in graph.nodes:
        if node.resource_ref is None:
            continue
        resource_id = node.resource_ref.resource_id
        adapter = get_planner_node_adapter(node.kind)
        if adapter.resource_kind == "knowledge_base":
            req.scope.knowledge_base_ids.append(resource_id)
            snap.knowledge_bases.append({"id": resource_id, "metadata": {"active_version_id": "index-synthetic"}})
        elif adapter.resource_kind == "data_table":
            req.scope.data_table_ids.append(resource_id)
            snap.data_tables.append({"id": resource_id})
    schema = graph_generation_schema(req, snap)
    for node in graph.nodes:
        get_planner_node_adapter(node.kind).validate_intent_node(node)
    assert accepts(schema, graph.model_dump(mode="json"))


def test_private_schema_relocates_nested_defs_and_is_deterministic():
    snap = snapshot()
    req = request(snap)
    first = graph_generation_schema(req, snap)
    req.scope.allowed_node_kinds.reverse()
    snap.nodes.reverse()
    assert graph_generation_schema(req, snap) == first
    definitions = first["$defs"]
    nested = definitions["ModelConfig_data_table_update"]["properties"]["filter"]
    assert "ModelConfig_data_table_update__" in json.dumps(nested)
    raw = intent("update").model_dump(mode="json")
    raw["nodes"][1]["config"]["filter"] = {
        "kind": "group", "logic": "and", "items": [{
            "kind": "predicate", "ref": "selected", "field": "sku",
            "operator": "eq", "value": "DEMO", "sourceHandle": "forged",
        }],
    }
    assert not accepts(first, raw)


@pytest.mark.parametrize("mutation", [
    "insert_authority", "missing_filter", "bare_predicate", "agent_object", "unknown_kind",
])
def test_model_schema_rejects_r11_error_classes_before_semantic_validation(mutation):
    req, snap, prompts = model_prompts()
    raw = intent("update" if mutation in {"missing_filter", "bare_predicate"} else "insert").model_dump(mode="json")
    if mutation == "insert_authority":
        raw["nodes"][0]["config"].update(max_affected_rows=1, writable_fields=["sku"])
    elif mutation == "missing_filter":
        raw["nodes"][1]["config"].pop("filter")
    elif mutation == "bare_predicate":
        raw["nodes"][0]["config"]["filter"] = {"ref": "key", "field": "sku", "operator": "eq", "value_source": "input"}
        raw["nodes"][0]["inputs"] = [{"port": "predicate", "variable": "user_input", "source_ref": "input", "source_port": "user_input", "value_schema": {"type": "string"}}]
    elif mutation == "agent_object":
        raw["nodes"][-1]["outputs"][0]["value_schema"] = {"type": "object"}
    else:
        raw["nodes"][0]["kind"] = "http_request"
    assert validate_blueprint_authorization(req, _plan(), GraphIntentV3.model_validate(raw), snap)
    for prompt in prompts[2:4]:
        assert not accepts(prompt["required_schema"], raw)


def test_private_schema_is_scope_bound_and_does_not_change_public_ir():
    public = deepcopy(GraphIntentV3.model_json_schema())
    req, snap, _ = model_prompts()
    req.scope.allowed_node_kinds = ["input", "output", "workflow_agent"]
    prompt = json.loads(MetaPlannerV2Service._blueprint_prompt(req, _plan(), snap, None))
    assert not accepts(prompt["required_schema"], intent().model_dump(mode="json"))
    assert GraphIntentV3.model_json_schema() == public


def test_named_variable_alias_still_requires_semantic_rejection():
    req, snap, _ = model_prompts()
    graph = intent()
    graph.nodes[-1].inputs[0].variable = "consumer_alias"
    assert validate_blueprint_authorization(req, _plan(), graph, snap)


def test_patch_config_schema_uses_adapter_for_add_and_existing_ref():
    _, _, prompts = model_prompts()
    schema = prompts[4]["required_schema"]
    valid = {"op": "update_node", "ref": "write", "config": {"value_source": "literal", "values": {"sku": "DEMO"}}}
    assert accepts(schema, {"operations": [valid]})
    invalid = deepcopy(valid)
    invalid["config"]["writeGrant"] = {}
    assert not accepts(schema, {"operations": [invalid]})
    add = {"op": "add_node", "ref": "helper", "kind": "data_table_insert", "title": "合成写入", "config": valid["config"]}
    assert accepts(schema, {"operations": [add]})
    add["config"] = {"max_affected_rows": 1}
    assert not accepts(schema, {"operations": [add]})
    valid["config"] = {"filter": {"ref": "selected", "field": "sku", "operator": "eq", "value": "DEMO"}}
    assert not accepts(schema, {"operations": [valid]})  # An Insert ref cannot use Delete config.
