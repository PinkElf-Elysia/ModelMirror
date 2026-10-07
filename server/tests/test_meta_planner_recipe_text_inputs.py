"""Opt-in Recipe text lowering; never evidence of real-provider success."""
from copy import deepcopy
import json

from jsonschema import Draft202012Validator
import pytest

from server.meta_agent.generation_diagnostics import GenerationDiagnostics, RecipeTemplateError
from server.meta_agent.generation_recipe import lower_generation_recipe, recipe_schema
from server.meta_agent.graph_ir_v3 import decompile_candidate_to_graph_intent, resolve_graph_intent
from server.tests.meta_planner_recipe_fixtures import from_intent, step
from server.tests.test_meta_planner_controlled_writes import compile_case, intent, request, snapshot
from server.tests.test_meta_planner_recipe_matrix import guarded_recipe
from server.tests.test_meta_planner_branch_effect_closeout import _isolated_main_runtime


def text_recipe(raw):
    raw = deepcopy(raw)
    raw["nodes"] = [node for node in raw["nodes"] if node["ref"] != "encode"]

    def remove_encode(steps):
        result = []
        for item in steps:
            if item.get("node_ref") == "encode":
                continue
            for branch in item.get("branches", []):
                branch["steps"] = remove_encode(branch["steps"])
            if "paths" in item:
                item["paths"] = [remove_encode(path) for path in item["paths"]]
            result.append(item)
        return result

    raw["control_flow"] = remove_encode(raw["control_flow"])
    for node in raw["nodes"]:
        if node["kind"] == "workflow_agent":
            node["inputs"] = None
            node["config"]["role_prompt"] = "根据实际记录汇总：{{lookup.result}}。"
            node["config"]["task_input"] = (
                "核对 {{lookup.result}} 和 {{write.result}}；只报告真实效果 {{write.result}}。"
                if node["ref"] == "answer" else "说明为何无需修改：{{lookup.result}}。"
            )
    return raw


@pytest.mark.parametrize("domain", ["quality", "incident"])
@pytest.mark.parametrize("error_output", [False, True])
def test_derived_text_inputs_preserve_business_paths_and_native_roundtrip(domain, error_output):
    original, req, snap = guarded_recipe(domain, error_output)
    raw = text_recipe(original)
    before = deepcopy(raw)
    graph = lower_generation_recipe(raw, req, snap)
    assert raw == before
    helpers = [node for node in graph.nodes if node.kind == "json_serialize"]
    assert len(helpers) == 3
    assert all(node.config == {"format": "compact"} and not node.task_ids for node in helpers)
    agents = {node.ref: node for node in graph.nodes if node.kind == "workflow_agent"}
    assert len(agents["answer"].inputs) == 2
    assert len(agents["unchanged"].inputs) == 1
    assert all(binding.value_schema.type == "string" for node in agents.values() for binding in node.inputs)
    write_text = next(node for node in helpers if node.inputs[0].source_ref == "write")
    assert agents["answer"].config["task_input"].count("{{" + write_text.outputs[0].variable + "}}") == 2
    assert all(node.inputs[0].value_schema.type in {"object", "array"} for node in helpers)
    ir = resolve_graph_intent(graph, snap, default_agent_model_id=req.default_agent_model_id,
                              data_table_write_grants=req.scope.data_table_write_grants)
    assert len(ir.control_flow_report["scenarios"]) == 3 + int(error_output)
    for case in ir.control_flow_report["scenarios"]:
        assert ("write" in case["reached"]) == (case["success_sources"] == ["answer"])
        for agent in agents.values():
            assert all((binding.source_ref in case["reached"]) == (agent.ref in case["reached"])
                       for binding in agent.inputs)
    candidate = compile_case(graph, snap, req)
    rebuilt = compile_case(decompile_candidate_to_graph_intent(candidate), snap, req)
    assert candidate["draft"]["workflow"] == rebuilt["draft"]["workflow"]
    native_helpers = [node for node in candidate["draft"]["workflow"]["nodes"]
                      if node["data"].get("plannerRef") in {item.ref for item in helpers}]
    assert len(native_helpers) == 3
    assert all(node["data"]["contractVersion"] == 2 for node in native_helpers)
    assert lower_generation_recipe(raw, req, snap) == graph


def test_model_schema_allows_null_only_for_agent_and_preserves_explicit_arrays():
    raw, req, snap = guarded_recipe()
    validator = Draft202012Validator(recipe_schema(req, snap))
    assert validator.is_valid(raw)
    derived = text_recipe(raw)
    assert validator.is_valid(derived)
    derived["nodes"][0]["inputs"] = None
    assert not validator.is_valid(derived)
    with pytest.raises(ValueError):
        lower_generation_recipe(derived, req, snap)


def test_string_input_derivation_is_identical_to_explicit_legacy_graph():
    raw = from_intent(intent("update"))
    snap = snapshot()
    req = request(snap, "update")
    explicit = lower_generation_recipe(raw, req, snap)
    next(node for node in raw["nodes"] if node["ref"] == "answer")["inputs"] = None
    assert lower_generation_recipe(raw, req, snap) == explicit


@pytest.mark.parametrize("schema", [
    {"type": "integer"}, {"type": "number"}, {"type": "boolean"}, {"type": "null"},
    {"type": "array", "items": {"type": "string"}}, {"type": "object"},
    {"type": "string", "nullable": True}, {"type": "any"},
    {"type": "any", "any_of": [{"type": "string"}, {"type": "number"}]},
])
def test_non_string_schema_is_serialized_not_narrowed(schema):
    snap = snapshot()
    req = request(snap, "update")
    raw = from_intent(intent("update"))
    answer = next(node for node in raw["nodes"] if node["ref"] == "answer")
    answer["inputs"] = None
    answer["config"].update(role_prompt="汇总真实输入。", task_input="{{parse.value}}")
    raw["nodes"] = [{"ref": "parse", "kind": "json_deserialize", "title": "解析输入",
                     "config": {"expected_schema": schema},
                     "inputs": [{"port": "json", "source_ref": "input", "source_port": "user_input"}]}, answer]
    raw["control_flow"] = [step("parse"), step("answer")]
    graph = lower_generation_recipe(raw, req, snap)
    producer = next(node for node in graph.nodes if node.ref == "parse")
    helper = next(node for node in graph.nodes if node.kind == "json_serialize")
    assert helper.inputs[0].value_schema == producer.outputs[0].value_schema
    assert helper.outputs[0].value_schema.type == "string"
    compile_case(graph, snap, req)


@pytest.mark.parametrize("reference", ["foreign.result", "lookup.unknown", "lookup.result.status", "v_native"])
def test_derived_sources_are_not_guessed(reference):
    raw, req, snap = guarded_recipe()
    raw = text_recipe(raw)
    next(node for node in raw["nodes"] if node["ref"] == "answer")["config"]["task_input"] = "{{" + reference + "}}"
    with pytest.raises(RecipeTemplateError) as raised:
        lower_generation_recipe(raw, req, snap)
    expected = "RECIPE_TEMPLATE_SOURCE_UNKNOWN" if reference in {"foreign.result", "lookup.unknown"} else "RECIPE_TEMPLATE_REFERENCE_INVALID"
    assert raised.value.code == expected


@pytest.mark.parametrize("mutation", ["unauthorized", "branch_only", "orphan", "no_sources", "legacy_type", "legacy_missing"])
def test_text_lowering_never_repairs_business_or_authorization_errors(mutation):
    raw, req, snap = guarded_recipe()
    raw = text_recipe(raw)
    answer = next(node for node in raw["nodes"] if node["ref"] == "answer")
    if mutation == "unauthorized":
        req.scope.allowed_node_kinds.remove("json_serialize")
    elif mutation == "branch_only":
        next(node for node in raw["nodes"] if node["ref"] == "unchanged")["config"]["task_input"] = "{{write.result}}"
    elif mutation == "orphan":
        raw["nodes"].append({"ref": "unused", "kind": "json_serialize", "title": "未放置节点", "inputs": [], "config": {}})
    elif mutation == "no_sources":
        answer["config"].update(role_prompt="汇总实际结果。", task_input="不要编造结果。")
    elif mutation == "legacy_type":
        answer["inputs"] = [{"port": "task", "source_ref": "lookup", "source_port": "result"},
                            {"port": "task", "source_ref": "write", "source_port": "result"}]
    else:
        answer["inputs"] = []
    before = deepcopy(raw)
    with pytest.raises(ValueError):
        compile_case(lower_generation_recipe(raw, req, snap), snap, req)
    assert raw == before


def test_generated_helpers_count_towards_node_budget_and_cannot_collide():
    raw, req, snap = guarded_recipe()
    raw = text_recipe(raw)
    helper = next(node for node in lower_generation_recipe(raw, req, snap).nodes if node.kind == "json_serialize")
    collision = deepcopy(raw)
    collision["nodes"].append({"ref": helper.ref, "kind": "json_serialize", "title": "冲突", "config": {}})
    with pytest.raises(ValueError, match="冲突"):
        lower_generation_recipe(collision, req, snap)
    while len(raw["nodes"]) < 24:
        ref = "extra_" + str(len(raw["nodes"]))
        raw["nodes"].append({"ref": ref, "kind": "json_serialize", "title": "预算节点", "config": {},
                             "inputs": [{"port": "value", "source_ref": "input", "source_port": "user_input"}]})
        raw["control_flow"].insert(0, step(ref))
    with pytest.raises(ValueError, match="24"):
        lower_generation_recipe(raw, req, snap)


@pytest.mark.asyncio
@pytest.mark.parametrize("derived", [False, True], ids=["legacy", "derived"])
async def test_generation_retains_opt_in_recipe_and_uses_no_extra_calls(tmp_path, monkeypatch, derived):
    from server.tests.test_meta_planner_recipe_integration import generate
    from server.meta_agent.graph_patch import GraphPatchApplyRequest, GraphPatchEditorDiffRequest, GraphPatchEnvelopeV1
    raw = from_intent(intent("update"))
    if derived:
        raw = text_recipe(raw)
    headless, authoring, result, calls, _ = await generate(tmp_path, monkeypatch, initial=raw)
    assert len(calls) == 2 and not result.repair_used
    assert result.validation["valid"], result.validation
    stored = authoring.proposal_store.require(result.proposal_id)
    assert stored.status == "pending" and stored.revision == 1
    assert authoring.xpert_store.list_xperts() == []
    state = headless.proposal_state(result.proposal_id)
    definition = deepcopy(state["candidate"]["draft"]["workflow"])
    no_op = headless.editor_diff(result.proposal_id, GraphPatchEditorDiffRequest(proposal_revision=1, definition=definition))
    assert no_op["empty"], json.dumps(no_op["patch"]["operations"], ensure_ascii=False)
    edited_filter = deepcopy(definition)
    write = next(node for node in edited_filter["nodes"] if node["data"].get("plannerRef") == "write")
    write["data"]["filter"]["value"]["value"] = "OTHER"
    changed = headless.editor_diff(result.proposal_id, GraphPatchEditorDiffRequest(proposal_revision=1, definition=edited_filter))
    operation = next(op for op in changed["patch"]["operations"] if op["op"] == "update_node" and op["ref"] == "write")
    assert operation["config"]["filter"]["value"] == "OTHER"
    assert authoring.proposal_store.require(result.proposal_id).revision == 1
    if not derived:
        return
    helper = next(node for node in definition["nodes"] if node["data"].get("plannerRef", "").startswith("text_"))
    helper["position"]["x"] += 80
    diff = headless.editor_diff(result.proposal_id, GraphPatchEditorDiffRequest(proposal_revision=1, definition=definition))
    patch = GraphPatchEnvelopeV1.model_validate(diff["patch"])
    preview = headless.preview(result.proposal_id, patch)
    assert preview["can_apply"], preview["diagnostics"]
    assert authoring.proposal_store.require(result.proposal_id).payload == stored.payload
    headless.apply(result.proposal_id, GraphPatchApplyRequest(patch=patch, preview_checksum=preview["preview_checksum"]))
    assert authoring.proposal_store.require(result.proposal_id).revision == 2
    assert authoring.xpert_store.list_xperts() == []
    assert headless.proposal_state(result.proposal_id)["graph_checksum"] == state["graph_checksum"]
    prompt = json.loads(calls[1][2])
    assert Draft202012Validator(prompt["required_schema"]).is_valid(raw)
    assert prompt["node_contracts"]["workflow_agent"]["text_inputs"]["mode"] == "template_sources"


def test_derived_serializers_use_existing_patch_kernel():
    from server.meta_agent.graph_patch import apply_graph_patch
    from server.meta_agent.recipe_repair import recipe_repair_patch
    from server.tests.test_meta_planner_recipe_model_repair import state_for
    raw, req, snap = guarded_recipe()
    fixed = text_recipe(raw)
    state = state_for(raw, req, snap)
    patch = recipe_repair_patch(state, fixed)
    replay = apply_graph_patch(state.intent, patch, plan_task_ids={task.task_id for task in state.plan.tasks},
                               allowed_node_kinds=set(req.scope.allowed_node_kinds)).intent
    actual = compile_case(replay, snap, req)["draft"]["workflow"]
    expected = compile_case(lower_generation_recipe(fixed, req, snap), snap, req)["draft"]["workflow"]
    for workflow in (actual, expected):
        workflow["edges"].sort(key=lambda edge: edge["id"])
    assert actual == expected
    assert len(patch.operations) <= 64
    assert recipe_repair_patch(state, fixed) == patch


def test_text_bridge_control_budget_and_outcome_preservation():
    from server.meta_agent.generation_recipe import _bridge_control_edges
    edges = [{"source_ref": f"source_{index}", "outcome_ref": "error" if index == 0 else "success", "target_ref": "agent"}
             for index in range(40)]
    with pytest.raises(ValueError, match="40"):
        _bridge_control_edges(edges, {"agent": ["text_helper"]})
    bridged = _bridge_control_edges(edges[:39], {"agent": ["text_helper"]})
    assert len(bridged) == 40
    assert {"source_ref": "source_0", "outcome_ref": "error", "target_ref": "text_helper"} in bridged


@pytest.mark.asyncio
async def test_opt_in_failed_recipe_survives_reload_and_only_one_repair(tmp_path, monkeypatch):
    from server.tests.test_meta_planner_recipe_integration import generate
    from server.meta_agent.failed_recovery import FailedDraftRecovery
    from server.meta_agent.recipe_repair import retained_recipe
    from server.xpert_runtime.authoring_store import AuthoringProposalStore
    raw = text_recipe(from_intent(intent("update")))
    raw["nodes"][1]["inputs"] = []
    headless, authoring, result, calls, _ = await generate(tmp_path, monkeypatch, initial=raw, repaired=raw)
    assert len(calls) == 3 and not result.validation["valid"]
    store = authoring.proposal_store
    reloaded = AuthoringProposalStore(storage_dir=store.storage_dir)
    assert reloaded.require(result.proposal_id).payload == store.require(result.proposal_id).payload
    authoring.proposal_store = reloaded
    state, _, selected = FailedDraftRecovery(headless)._state(result.proposal_id)
    artifacts = headless.proposal_state(result.proposal_id)["recovery"]
    assert artifacts["artifact_checksum"]
    assert retained_recipe(selected, state).model_dump(mode="json")["nodes"][-1]["inputs"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_score", [None, 42, 75], ids=["missing", "update", "no-write"])
async def test_generated_serializers_execute_with_isolated_real_effects(tmp_path, monkeypatch, initial_score):
    from server.tests import test_meta_planner_branch_effect_closeout as execution
    original_compile = execution.compile_case

    def compile_text_recipe(graph, snap, req):
        if not any(node.ref == "encode" for node in graph.nodes):
            return original_compile(graph, snap, req)
        flow = [step("lookup"), step("found", {"matched": [step("stop")], "unmatched": [step("score_gate", {
            "matched": [step("write"), step("encode"), step("answer")], "unmatched": [step("unchanged")],
        })]})]
        raw = text_recipe(from_intent(graph, flow))
        return original_compile(lower_generation_recipe(raw, req, snap), snap, req)

    monkeypatch.setattr(execution, "compile_case", compile_text_recipe)
    await execution.test_branch_compiler_runner_effects_and_business_isolation(tmp_path, monkeypatch, initial_score)
