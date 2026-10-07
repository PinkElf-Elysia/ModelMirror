from copy import deepcopy
from itertools import permutations
import json
from types import SimpleNamespace

import pytest

from server.meta_agent.generation_recipe import GenerationRecipeV1, lower_generation_recipe
from server.meta_agent.graph_patch import apply_graph_patch
from server.meta_agent.headless_authoring import HeadlessAuthoringError
from server.meta_agent.meta_planner_v2 import MetaPlannerV2Service, compile_xpert_candidate
from server.meta_agent.model_repair import ExplicitModelRepairService, ModelRepairPreflightRequest
from server.meta_agent.recipe_repair import _ordered_input_operations, recipe_repair_patch
from server.meta_agent.schemas import GraphIntentInputBindingV3
from server.tests.test_meta_planner_controlled_writes import intent, request, snapshot, _plan
from server.tests.test_meta_planner_model_repair import ROUTE, consent
from server.tests.test_meta_planner_recipe_integration import generate
from server.tests.test_meta_planner_semantic_repair_contract import compound_recipe
from server.tests.test_meta_planner_recipe_matrix import guarded_recipe
from server.tests.meta_planner_recipe_fixtures import from_intent
from server.workflow_native.node_contracts import canonical_checksum


def state_for(raw, req, snap, plan=None):
    graph = lower_generation_recipe(raw, req, snap)
    return SimpleNamespace(intent=graph, request=req, scope=req.scope, snapshot=snap, plan=plan or _plan(),
        proposal=SimpleNamespace(revision=1), graph_checksum=canonical_checksum(graph.model_dump(mode="json")), candidate_checksum="a" * 64)


@pytest.mark.parametrize("domain", ["quality", "incident"])
def test_compound_repair_stays_semantic_and_derives_exact_patch(domain):
    broken, req, snap = compound_recipe(domain)
    fixed, _, _ = guarded_recipe(domain)
    state = state_for(broken, req, snap)
    prompt = json.loads(MetaPlannerV2Service._recipe_repair_prompt(req, _plan(), snap, None,
        recipe=GenerationRecipeV1.model_validate(broken)))
    assert prompt["invalid_generation"]["control_flow"]
    assert "base_intent" not in prompt and "graph_intent_contract" not in prompt
    phases = {item["id"]: item["status"] for item in prompt["lowering_diagnostics"]["phase_results"]}
    assert phases["intent_parse"] == phases["recipe_lowering"] == "passed"
    assert phases["authorization"] == "failed" and phases["compile"] == "blocked"
    checks = {item["id"]: item["status"] for item in prompt["lowering_diagnostics"]["control_proof_checks"]}
    if domain == "quality":
        assert checks["predicate_domains"] == "failed"
        assert checks["data_availability"] == "blocked"
    else:
        # Text equality is defined for the complete value, but cannot reach the matched branch.
        assert checks["predicate_domains"] == "passed"
        assert checks["outcome_coverage"] == "failed"
        assert checks["data_availability"] == "failed"
    patch = recipe_repair_patch(state, fixed)
    replay = apply_graph_patch(state.intent, patch, plan_task_ids={task.task_id for task in state.plan.tasks},
        allowed_node_kinds=set(req.scope.allowed_node_kinds)).intent
    compiled = compile_xpert_candidate(request=req, plan=_plan(), blueprint=replay, snapshot=snap, target=None)
    assert compiled["draft"]["workflow"]
    assert "disconnect_control" in {item.op for item in patch.operations}
    assert len(patch.operations) <= 64
    assert recipe_repair_patch(state, fixed) == patch


def test_derived_patch_handles_resource_output_shape_change():
    snap = snapshot()
    req = request(snap, "update")
    raw = from_intent(intent("update"))
    state = state_for(raw, req, snap)
    fixed = deepcopy(raw)
    fixed["nodes"][0]["config"]["select_fields"] = ["sku"]
    patch = recipe_repair_patch(state, fixed)
    replay = apply_graph_patch(state.intent, patch, plan_task_ids={task.task_id for task in state.plan.tasks},
        allowed_node_kinds=set(req.scope.allowed_node_kinds)).intent
    assert compile_xpert_candidate(request=req, plan=_plan(), blueprint=replay, snapshot=snap, target=None)


def test_derived_patch_preserves_ordered_aggregator_inputs():
    from server.tests import test_meta_planner_pure_nodes as pure
    from server.tests import test_meta_planner_v2 as base
    raw = from_intent(pure._pack_intent())
    plan = base._plan()
    plan.tasks = [task for task in plan.tasks if task.task_id == "deliver"]
    plan.tasks[0].depends_on = []
    snap, req = base._snapshot(), pure._authorized_request()
    state = state_for(raw, req, snap, plan)
    fixed = deepcopy(raw)
    node = next(item for item in fixed["nodes"] if item["ref"] == "pack_inputs")
    node["inputs"].reverse()
    node["config"]["output_fields"].reverse()
    patch = recipe_repair_patch(state, fixed)
    replay = apply_graph_patch(state.intent, patch, plan_task_ids={"deliver"}, allowed_node_kinds=set(req.scope.allowed_node_kinds)).intent
    pack = next(item for item in replay.nodes if item.ref == "pack_inputs")
    assert [item.source_port for item in pack.inputs] == ["conversation_history", "user_input"]
    assert compile_xpert_candidate(request=req, plan=plan, blueprint=replay, snapshot=snap, target=None)


def test_derived_patch_can_remove_duplicate_inputs_from_retained_invalid_recipe():
    snap = snapshot()
    req = request(snap, "update")
    fixed = from_intent(intent("update"))
    broken = deepcopy(fixed)
    broken["nodes"][1]["inputs"].append(deepcopy(broken["nodes"][1]["inputs"][0]))
    state = state_for(broken, req, snap)
    patch = recipe_repair_patch(state, fixed)
    replay = apply_graph_patch(state.intent, patch, plan_task_ids={task.task_id for task in state.plan.tasks},
        allowed_node_kinds=set(req.scope.allowed_node_kinds)).intent
    assert len([op for op in patch.operations if op.op == "disconnect_data"]) == 1
    assert compile_xpert_candidate(request=req, plan=_plan(), blueprint=replay, snapshot=snap, target=None)


def test_ordered_input_derivation_preserves_all_small_permutations_and_duplicates():
    bindings = [GraphIntentInputBindingV3(port="values", variable=f"v{index}", source_ref=f"source{index}",
        source_port="result", value_schema={"type": "string"}) for index in range(4)]
    for size in range(1, 5):
        for target in permutations(bindings[:size]):
            for source in (bindings[:size], [*bindings[:size], bindings[0]]):
                before = deepcopy(source)
                removed, added = _ordered_input_operations("pack", source, list(target))
                replay = list(source)
                for op in removed:
                    replay = [b for b in replay if (b.source_ref, b.source_port, b.port) != (op.source_ref, op.source_port, op.target_port)]
                for op in added:
                    replay.append(next(b for b in target if (b.source_ref, b.source_port, b.port) == (op.source_ref, op.source_port, op.target_port)))
                assert replay == list(target) and source == before
                assert len({(op.source_ref, op.source_port, op.target_port) for op in removed}) == len(removed)


def test_tail_reordering_reuses_prefix_instead_of_rebuilding_all_inputs():
    bindings = [GraphIntentInputBindingV3(port="values", variable=f"v{index}", source_ref=f"source{index}",
        source_port="result", value_schema={"type": "string"}) for index in range(50)]
    target = [*bindings[:-2], bindings[-1], bindings[-2]]
    removed, added = _ordered_input_operations("pack", bindings, target)
    assert len(removed) == len(added) == 1


@pytest.mark.asyncio
async def test_unrepresentable_patch_is_distinct_from_invalid_model_output(tmp_path, monkeypatch):
    from server.meta_agent.graph_patch import GraphPatchLimitError
    import server.meta_agent.model_repair as module
    service, authoring, proposal_id, req, calls = await setup_recipe(tmp_path, monkeypatch)
    def limited(*_):
        raise GraphPatchLimitError(65)
    monkeypatch.setattr(module, "recipe_repair_patch", limited)
    approved = consent(req, service.preflight(proposal_id, req))
    result = await service.execute(proposal_id, approved)
    assert result["status"] == "invalid" and result["reason_code"] == "repair_patch_unrepresentable"
    assert "patch" not in result and len(calls) == 1
    assert authoring.proposal_store.require(proposal_id).revision == 1
    assert await service.execute(proposal_id, approved) == result


async def setup_recipe(tmp_path, monkeypatch, response=None):
    raw = from_intent(intent("update"))
    raw["nodes"][1]["inputs"] = []
    headless, authoring, result, _, _ = await generate(tmp_path, monkeypatch, initial=raw, repaired=raw)
    assert not result.validation["valid"]
    calls = []

    async def completion(*args):
        calls.append(args)
        edits = {"operations": [{"op": "update_node", "node_ref": "write", "inputs": from_intent(intent("update"))["nodes"][1]["inputs"]}]}
        return {"text": json.dumps(edits if response is None else response, ensure_ascii=False),
                "receipt": {"provider_dispatched": True, "response_received": True, "total_tokens": 123}}

    service = ExplicitModelRepairService(headless, route_projection=lambda _: deepcopy(ROUTE), completion=completion)
    state = headless.proposal_state(result.proposal_id)
    req = ModelRepairPreflightRequest(proposal_revision=1, artifact_checksum=state["recovery"]["artifact_checksum"],
        expected_graph_checksum=state["graph_checksum"], expected_candidate_checksum=state["candidate_checksum"], model_id="test/repair")
    return service, authoring, result.proposal_id, req, calls


@pytest.mark.asyncio
async def test_explicit_recipe_repair_is_one_call_patch_suggestion_without_apply(tmp_path, monkeypatch):
    service, authoring, proposal_id, req, calls = await setup_recipe(tmp_path, monkeypatch)
    before = authoring.proposal_store.require(proposal_id)
    preflight = service.preflight(proposal_id, req)
    assert preflight["repair_protocol"] == "recipe_edits_v1" and not calls
    body = json.loads(preflight["outbound"]["messages"][1]["content"])
    assert set(body["required_schema"]["properties"]) == {"operations"}
    approved = consent(req, preflight)
    result = await service.execute(proposal_id, approved)
    assert result["status"] == "suggested", result
    assert result["repair_protocol"] == "recipe_edits_v1"
    assert result["patch"]["operations"] and result["preview_summary"]["can_apply"]
    assert not result["automatically_applied"] and len(calls) == 1
    current = authoring.proposal_store.require(proposal_id)
    assert current.payload == before.payload and current.revision == 1
    assert authoring.xpert_store.list_xperts() == []
    assert await service.execute(proposal_id, approved) == result
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("attack", ["empty", "whole_recipe", "foreign_resource", "version_injection", "source_forgery", "unchanged"])
async def test_explicit_recipe_attacks_never_apply_or_retry(tmp_path, monkeypatch, attack):
    node = from_intent(intent("update"))["nodes"][1]
    raw = {"operations": [{"op": "update_node", "node_ref": "write", "inputs": node["inputs"]}]}
    if attack == "empty":
        raw = {"operations": []}
    elif attack == "whole_recipe":
        raw = from_intent(intent("update"))
    elif attack == "foreign_resource":
        raw["operations"][0]["resource_ref"] = {"resource_id": "foreign"}
    elif attack == "version_injection":
        raw["operations"][0]["config"] = {**node["config"], "pinnedSchemaVersion": 999}
    elif attack == "source_forgery":
        raw["operations"][0]["inputs"][0].update(source_ref="input", source_port="user_input")
    else:
        raw["operations"][0]["inputs"] = []
    service, authoring, proposal_id, req, calls = await setup_recipe(tmp_path, monkeypatch, raw)
    result = await service.execute(proposal_id, consent(req, service.preflight(proposal_id, req)))
    assert result["status"] == "invalid", result
    assert not result.get("preview_summary", {}).get("can_apply")
    assert len(calls) == 1 and authoring.proposal_store.require(proposal_id).revision == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("attack", ["checksum", "mismatched_intent", "schema_drift", "late_edit"])
async def test_recipe_basis_checksums_and_toctou_block_stale_suggestions(tmp_path, monkeypatch, attack):
    service, authoring, proposal_id, req, calls = await setup_recipe(tmp_path, monkeypatch)
    approved = consent(req, service.preflight(proposal_id, req))
    if attack == "late_edit":
        complete = service.completion
        async def late(*args):
            authoring.update_pending(proposal_id, revision=1, title="用户最新修改")
            return await complete(*args)
        service.completion = late
        result = await service.execute(proposal_id, approved)
        assert result["status"] == "stale" and "patch" not in result and len(calls) == 1
        return
    if attack == "schema_drift":
        snap = service.recovery.headless.capability_snapshot_builder().model_copy(deep=True)
        snap.data_tables[0]["schema_versions"][0]["fields"].pop()
        service.recovery.headless.capability_snapshot_builder = lambda: snap
    else:
        proposal = authoring.proposal_store._items[proposal_id]
        artifact = proposal.payload["_meta_planner_generation_artifact"]
        selected = artifact["attempts"][-1]
        if attack == "checksum":
            selected["source_recipe_checksum"] = "b" * 64
        else:
            selected["source_recipe"]["name"] = "与原图不同"
            selected["source_recipe_checksum"] = canonical_checksum(selected["source_recipe"])
        artifact["checksum"] = canonical_checksum({key: value for key, value in artifact.items() if key != "checksum"})
    with pytest.raises(HeadlessAuthoringError):
        await service.execute(proposal_id, approved)
    assert not calls
