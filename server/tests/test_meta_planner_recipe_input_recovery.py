"""Retention-only input drafts never become an execution authority."""
from copy import deepcopy

import pytest

from server.meta_agent import failed_artifacts
from server.meta_agent.generation_recipe import GenerationRecipeV1, lower_generation_recipe
from server.tests.meta_planner_recipe_fixtures import from_intent
from server.tests.test_meta_planner_controlled_writes import intent, request, snapshot


def input_failure(*, missing_protocol=False):
    raw = from_intent(intent("update"))
    raw["nodes"][0]["inputs"] = None
    if missing_protocol:
        del raw["generation_protocol_version"]
    return raw


@pytest.mark.parametrize("missing_protocol", [False, True])
def test_retained_input_draft_is_not_a_valid_recipe(missing_protocol):
    raw = input_failure(missing_protocol=missing_protocol)
    before = deepcopy(raw)
    draft = failed_artifacts.parse_recipe_input_draft(raw, allowed_resource_ids={"table-orders"})
    assert draft is not None
    kept, status = failed_artifacts._retained_recipe(draft)
    assert status == "retained" and kept["nodes"][0]["inputs"] is None
    assert kept["generation_protocol_version"] == (None if missing_protocol else 1)
    assert failed_artifacts.recipe_source_format(draft) == "recipe_input_draft_v1"
    with pytest.raises(ValueError):
        GenerationRecipeV1.model_validate(kept)
    snap = snapshot()
    with pytest.raises(ValueError):
        lower_generation_recipe(draft, request(snap, "update"), snap)
    assert raw == before


@pytest.mark.parametrize("attack", ["node_field", "input_field", "top_field", "config", "unknown_kind", "foreign_resource", "secret", "bad_protocol", "string_inputs", "missing_config", "oversize"])
def test_retention_remains_bounded_and_rejects_injection(attack):
    raw = input_failure()
    if attack == "node_field": raw["nodes"][0]["sourceHandle"] = "private"
    elif attack == "input_field": raw["nodes"][1]["inputs"][0]["value_schema"] = {"type": "any"}
    elif attack == "top_field": raw["raw_response"] = "private"
    elif attack == "config": raw["nodes"][0]["config"]["tableId"] = "private"
    elif attack == "unknown_kind": raw["nodes"][0]["kind"] = "unregistered"
    elif attack == "foreign_resource": raw["nodes"][0]["resource_ref"]["resource_id"] = "foreign"
    elif attack == "secret": raw["nodes"][-1]["config"]["role_prompt"] = "OPENROUTER_API_KEY=sk-or-v1-" + "a" * 64
    elif attack == "bad_protocol": raw["generation_protocol_version"] = 2
    elif attack == "string_inputs": raw["nodes"][0]["inputs"] = "private-inputs"
    elif attack == "missing_config": del raw["nodes"][0]["config"]
    else: raw["nodes"][1]["config"]["values"] = {"sku": "A" * 300_000}
    draft = failed_artifacts.parse_recipe_input_draft(raw, allowed_resource_ids={"table-orders"})
    kept, _ = failed_artifacts._retained_recipe(draft)
    assert kept is None
