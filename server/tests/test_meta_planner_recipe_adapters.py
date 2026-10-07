"""Current-protocol coverage of existing pure, binding, knowledge and vision adapters."""
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from server.meta_agent.generation_recipe import lower_generation_recipe, recipe_schema
from server.meta_agent.graph_ir_v3 import decompile_candidate_to_graph_intent, workflow_semantic_checksum, v2_to_graph_intent
from server.meta_agent.meta_planner_v2 import compile_xpert_candidate
from server.tests.meta_planner_recipe_fixtures import from_intent, step
from server.tests import test_meta_planner_pure_nodes as pure
from server.tests import test_meta_planner_v2 as base
from server.tests import test_meta_planner_read_resources as read
from server.tests import test_meta_planner_vision_graph as vision


@pytest.mark.parametrize("case", ["aggregate", "pack", "compare", "knowledge", "vision", "bindings"])
def test_adapter_authority_roundtrips_without_model_type_declarations(case):
    if case in {"aggregate", "pack", "compare"}:
        graph = getattr(pure, f"_{case}_intent")()
        snap, req, plan = base._snapshot(), pure._authorized_request(), base._plan()
        if case == "pack":
            plan.tasks = [task for task in plan.tasks if task.task_id == "deliver"]
            plan.tasks[0].depends_on = []
    elif case == "knowledge":
        graph, snap, plan = read._knowledge_intent(), read._snapshot(), read._plan()
        req = read._request(snap)
    elif case == "vision":
        graph, snap, plan = vision.vision_intent(), vision.vision_snapshot(), read._plan()
        req = vision.vision_request(snap)
    else:
        graph, _ = v2_to_graph_intent(base._typed_blueprint())
        snap, req, plan = base._snapshot(), base._request(), base._plan()
    raw = from_intent(graph)
    Draft202012Validator(recipe_schema(req, snap)).validate(raw)
    lowered = lower_generation_recipe(raw, req, snap)
    candidate = compile_xpert_candidate(request=req, plan=plan, blueprint=lowered, snapshot=snap, target=None)
    restored = decompile_candidate_to_graph_intent(candidate)
    again = compile_xpert_candidate(request=req, plan=plan, blueprint=restored, snapshot=snap, target=None)
    assert workflow_semantic_checksum(candidate) == workflow_semantic_checksum(again)
    if case == "pack":
        node = next(node for node in lowered.nodes if node.ref == "pack_inputs")
        assert [item.source_port for item in node.inputs] == ["user_input", "conversation_history"]
        assert node.config["output_fields"] == ["request", "history"]
    if case == "vision":
        root = next(node for node in candidate["draft"]["workflow"]["nodes"] if node["id"] == "input")
        assert root["data"]["plannerAttachmentInputV1"] == vision.VISION_ATTACHMENT_CONTRACT


@pytest.mark.parametrize("source", ["user_input", "conversation_history"])
def test_recipe_cannot_replace_trusted_attachment_with_text(source):
    snap = vision.vision_snapshot()
    req = vision.vision_request(snap)
    raw = from_intent(vision.vision_intent())
    raw["nodes"][0]["inputs"][0]["source_port"] = source
    with pytest.raises(ValueError):
        graph = lower_generation_recipe(raw, req, snap)
        compile_xpert_candidate(request=req, plan=read._plan(), blueprint=graph, snapshot=snap, target=None)


@pytest.mark.parametrize("mutation", ["size", "depth", "flow_depth"])
def test_recipe_resource_bounds_fail_closed(mutation):
    graph, _ = v2_to_graph_intent(base._typed_blueprint())
    raw = from_intent(graph)
    if mutation == "size":
        raw["nodes"][0]["config"]["role_prompt"] = "x" * (1024 * 1024)
    elif mutation == "depth":
        nested = {}
        raw["extra"] = nested
        for _ in range(33):
            nested["child"] = {}
            nested = nested["child"]
    else:
        flow = [step(graph.nodes[0].ref)]
        for _ in range(9):
            flow = [{"type": "parallel", "paths": [flow, [step(graph.nodes[1].ref)]]}]
        raw["control_flow"] = flow
    before = deepcopy(raw)
    with pytest.raises(ValueError):
        lower_generation_recipe(raw, base._request(), base._snapshot())
    assert raw == before
