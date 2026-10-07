"""Protocol and input mode failures share one strict generation boundary."""
from copy import deepcopy
import json

from jsonschema import Draft202012Validator
import pytest

from server.meta_agent import generation_recipe
from server.meta_agent.failed_artifacts import parse_recipe_input_draft
from server.meta_agent.generation_diagnostics import GenerationDiagnostics
from server.meta_agent.meta_planner_v2 import MetaPlannerV2Service
from server.tests.meta_planner_recipe_fixtures import from_intent
from server.tests.test_meta_planner_controlled_writes import intent, request, snapshot, _plan


def test_input_mode_projection_matches_parser_and_model_schema():
    snap = snapshot()
    req = request(snap, "update")
    contracts = generation_recipe.recipe_node_contracts(req, snap)
    schema = Draft202012Validator(generation_recipe.recipe_schema(req, snap))
    for kind, contract in contracts.items():
        assert contract["input_mode"]["null_allowed"] is (kind == "workflow_agent")
        assert contract["input_mode"]["no_bindings"] == []
    raw = from_intent(intent("update"))
    for index, node in enumerate(raw["nodes"]):
        changed = deepcopy(raw)
        changed["nodes"][index]["inputs"] = None
        expected = contracts[node["kind"]]["input_mode"]["null_allowed"]
        assert schema.is_valid(changed) is expected
        if expected:
            generation_recipe.parse_generation_recipe(changed)
        else:
            with pytest.raises(ValueError):
                generation_recipe.parse_generation_recipe(changed)


def test_missing_protocol_does_not_hide_independent_input_errors():
    snap = snapshot()
    req = request(snap, "update")
    raw = from_intent(intent("update"))
    del raw["generation_protocol_version"]
    raw["nodes"][0]["inputs"] = None
    before = deepcopy(raw)
    observer = GenerationDiagnostics("capability_compile")
    planner = MetaPlannerV2Service(
        authoring_service=None,
        preflight=lambda *_args, **_kwargs: pytest.fail("must not reach publish preflight"),
    )
    graph, candidate, validation, errors, ir, _ = planner._compile_and_validate(
        request=req, plan=_plan(), raw_blueprint=json.dumps(raw), snapshot=snap,
        target=None, diagnostics=observer, require_recipe=True,
    )
    assert graph is None and candidate == {} and ir is None
    assert not validation["valid"] and errors
    locations = [item.get("location") for item in observer.as_dict()["issues"]]
    assert ["generation_protocol_version"] in locations
    assert ["nodes", 0] in locations or ["nodes", 0, "inputs"] in locations
    assert raw == before and observer.parsed_recipe is None


@pytest.mark.parametrize("protocol", [None, 0, 2, "1"])
def test_new_generation_never_accepts_wrong_protocol(protocol):
    raw = from_intent(intent("update"))
    raw["generation_protocol_version"] = protocol
    with pytest.raises(ValueError):
        generation_recipe.parse_generation_recipe(raw)


@pytest.mark.parametrize("protocol", [True, False])
def test_protocol_boolean_cannot_cross_the_json_schema_boundary(protocol):
    snap = snapshot()
    raw = from_intent(intent("update"))
    raw["generation_protocol_version"] = protocol
    assert not Draft202012Validator(generation_recipe.recipe_schema(request(snap, "update"), snap)).is_valid(raw)
    with pytest.raises(ValueError):
        generation_recipe.parse_generation_recipe(raw)
    raw["nodes"][0]["inputs"] = None
    assert parse_recipe_input_draft(raw, allowed_resource_ids={"table-orders"}) is None


def test_native_graph_cannot_be_reinterpreted_as_recipe():
    raw = intent("update").model_dump(mode="json")
    with pytest.raises(ValueError):
        generation_recipe.parse_generation_recipe(raw)
