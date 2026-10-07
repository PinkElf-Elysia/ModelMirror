from copy import deepcopy
import json

import pytest
from jsonschema import Draft202012Validator

from server.meta_agent.meta_planner_v2 import MetaPlannerV2Service
from server.meta_agent.node_adapters import get_planner_node_adapter
from server.tests.test_meta_planner_controlled_writes import _plan, intent, request, snapshot
from server.tests.test_meta_planner_repair_context import offline_only


def prompts(*, authorized=False, available=True, operation="update"):
    snap = snapshot()
    req = request(snap, operation)
    if not authorized:
        req.scope.allowed_node_kinds.remove("json_deserialize")
    if not available:
        snap.nodes = [node for node in snap.nodes if node["kind"] != "json_deserialize"]
    return req, snap, [json.loads(raw) for raw in (
        MetaPlannerV2Service._blueprint_prompt(req, _plan(), snap, None),
        MetaPlannerV2Service._repair_prompt(req, _plan(), snap, "{}", ["invalid"]),
        MetaPlannerV2Service._patch_repair_prompt(req, _plan(), snap, intent(operation), ["invalid"]),
    )]


@pytest.mark.parametrize("operation", ["insert", "update"])
@pytest.mark.parametrize("authorized,available", [(False, True), (True, False)])
def test_unavailable_write_producer_requires_explicit_literal_in_every_model_path(operation, authorized, available):
    _, _, payloads = prompts(authorized=authorized, available=available, operation=operation)
    kind = f"data_table_{operation}"
    config = next(node.config for node in intent(operation).nodes if node.kind == kind)
    for payload in payloads:
        schema = payload["required_schema"]
        scoped = {"$defs": schema["$defs"], "$ref": f"#/$defs/ModelConfig_{kind}"}
        validator = Draft202012Validator(scoped)
        assert not list(validator.iter_errors(config))
        for mode in (None, "input"):
            invalid = deepcopy(config)
            if mode is None:
                invalid.pop("value_source")
            else:
                invalid.update(value_source=mode, values=None)
            assert list(validator.iter_errors(invalid)), (operation, mode)
        contract = payload["graph_intent_contract"]["node_roles"]["executable_node_contracts"][kind]
        assert contract["value_source_contract"]["allowed"] == ["literal"]
        assert contract["input_binding_contract"]["when_value_source_input"] == []


def test_available_authorized_deserializer_preserves_input_mode_and_legacy_default():
    adapter = get_planner_node_adapter("data_table_insert")
    before = deepcopy(adapter.config_model.model_json_schema())
    _, _, payloads = prompts(authorized=True, operation="insert")
    for payload in payloads:
        schema = payload["required_schema"]
        scoped = {"$defs": schema["$defs"], "$ref": "#/$defs/ModelConfig_data_table_insert"}
        assert not list(Draft202012Validator(scoped).iter_errors({"value_source": "input"}))
        contract = payload["graph_intent_contract"]["node_roles"]["executable_node_contracts"]["data_table_insert"]
        assert contract["value_source_contract"]["allowed"] == ["input", "literal"]
        assert contract["value_source_contract"]["input_producer_kind"] == "json_deserialize"
    prompts(authorized=False, operation="insert")
    assert adapter.config_model.model_json_schema() == before
    assert adapter.config_model.model_validate({}).value_source == "input"


def test_patch_add_and_update_cannot_reintroduce_unavailable_input_mode():
    _, _, payloads = prompts(operation="insert")
    validator = Draft202012Validator(payloads[-1]["required_schema"])
    for op in (
        {"op": "update_node", "ref": "write", "config": {"value_source": "input"}},
        {"op": "add_node", "ref": "new_write", "kind": "data_table_insert", "title": "合成写入", "config": {"value_source": "input"}},
    ):
        assert list(validator.iter_errors({"operations": [op]}))


def test_scoped_projection_is_order_independent_and_keeps_request_unchanged():
    req, snap, payloads = prompts()
    before = req.model_dump(mode="json")
    req.scope.allowed_node_kinds.reverse()
    snap.nodes.reverse()
    after = json.loads(MetaPlannerV2Service._blueprint_prompt(req, _plan(), snap, None))
    assert after["required_schema"] == payloads[0]["required_schema"]
    req.scope.allowed_node_kinds.reverse()
    assert req.model_dump(mode="json") == before
