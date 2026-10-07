from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from server.meta_agent.generation_contract import graph_generation_schema
from server.meta_agent.graph_ir_v3 import _schemas_compatible
from server.meta_agent.node_adapters import get_planner_node_adapter
from server.meta_agent.schemas import GraphIntentV3
from server.tests.test_meta_planner_controlled_writes import intent, request, snapshot
from server.workflow_native.node_contracts import WorkflowValueSchema


SCHEMAS = [
    {"type": "object"},
    {"type": "object", "nullable": True},
    {"type": "object", "properties": {"revision": {"type": "integer"}}, "required": ["revision"]},
    {"type": "array", "items": {"type": "object"}},
    {"type": "array", "items": {"type": "object"}, "nullable": True},
    {"type": "null"},
    {"any_of": [{"type": "object"}, {"type": "array", "items": {"type": "object"}}]},
    {"any_of": [{"any_of": [{"type": "object"}, {"type": "null"}]}]},
    {"type": "string"},
    {"type": "any"},
    {"type": "integer"},
    {"type": "boolean"},
    {"type": "array"},
    {"type": "array", "items": {"type": "string"}},
    {"type": "array", "items": {"type": "any"}},
    {"type": "array", "items": {"type": "object", "nullable": True}},
    {"any_of": [{"type": "object"}, {"type": "string"}]},
    {"any_of": [{"type": "object"}, {"type": "any"}]},
]


@pytest.mark.parametrize("operation", ["update", "delete"])
@pytest.mark.parametrize("declared", SCHEMAS)
def test_records_projection_matches_authoritative_type_envelope(operation, declared):
    snap = snapshot()
    req = request(snap)
    req.scope.data_table_write_grants[0].operations = [operation]
    adapter = get_planner_node_adapter(f"data_table_{operation}")
    target = next(port.value_schema for port in adapter.intent_port_contracts("input") if port.name == "records")
    value = WorkflowValueSchema.model_validate(declared)
    expected = _schemas_compatible(value, target)
    raw = intent(operation).model_dump(mode="json")
    raw["nodes"][1]["inputs"][0]["value_schema"] = deepcopy(declared)
    schema = graph_generation_schema(req, snap)
    Draft202012Validator.check_schema(schema)
    accepted = not list(Draft202012Validator(schema).iter_errors(raw))
    assert accepted is expected


def test_records_projection_keeps_public_ir_and_contract_unchanged():
    public = deepcopy(GraphIntentV3.model_json_schema())
    adapter = get_planner_node_adapter("data_table_update")
    ports = deepcopy(adapter.intent_port_contracts("input"))
    snap = snapshot()
    graph_generation_schema(request(snap), snap)
    assert GraphIntentV3.model_json_schema() == public
    assert adapter.intent_port_contracts("input") == ports
