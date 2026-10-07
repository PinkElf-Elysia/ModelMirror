"""Exercise the real generation/repair/proposal path without external calls."""
import json
from dataclasses import asdict

from jsonschema import Draft202012Validator
import pytest

from server.meta_agent.meta_planner_v2 import MetaPlannerV2Service, RECIPE_SYSTEM_PROMPT
from server.meta_agent.recipe_edits import RECIPE_EDIT_PROTOCOL, RECIPE_EDIT_SYSTEM_PROMPT
from server.meta_agent.generation_evidence import observe_generation_request
from server.meta_agent.generation_recipe import parse_generation_recipe, recipe_schema
from server.meta_agent.generation_contract import graph_generation_schema
from server.tests.test_meta_planner_controlled_writes import intent, request, _plan
from server.tests.meta_planner_recipe_fixtures import from_intent
from server.tests.test_meta_planner_write_generation_failures import _forbid_records
from server.tests.test_meta_planner_write_headless import fixture


async def generate(tmp_path, monkeypatch, *, malformed=None, repaired=None, initial=None):
    _forbid_records(monkeypatch)
    headless, authoring, _, snap = fixture(tmp_path, monkeypatch, "update")
    req = request(snap, "update")
    req.goal = "查询并按固定条件更新合成记录，汇总真实结果。"
    raw = from_intent(intent("update")) if initial is None else initial
    if malformed:
        malformed(raw)
    responses = [_plan().model_dump_json(), json.dumps(raw, ensure_ascii=False)]
    if repaired is not None:
        responses.append(json.dumps(repaired, ensure_ascii=False))
    calls = []

    async def completion(*args):
        calls.append(args)
        assert len(calls) <= 3
        observe_generation_request({"model": args[0], "messages": [{"role": "user", "content": args[2]}]}, "offline")
        return responses[len(calls) - 1]

    planner = MetaPlannerV2Service(authoring_service=authoring,
        preflight=headless.planner_service.preflight, completion=completion)
    compiled_formats = []
    original_compile = planner._compile_and_validate

    def observe_compile(**kwargs):
        compiled_formats.append(kwargs.get("require_recipe", False))
        return original_compile(**kwargs)

    monkeypatch.setattr(planner, "_compile_and_validate", observe_compile)
    result = await planner.generate(req, snap)
    report = authoring.proposal_store.require(result.proposal_id).payload["meta_planner_report"]
    expected = [True]
    if result.repair_used:
        if report["repair_protocol"] == "generation_recipe_v1" or (
            report["repair_protocol"] == RECIPE_EDIT_PROTOCOL and report["generation_diagnostics"][-1]["recompile_executed"]
        ):
            expected.append(True)
        elif report["repair_protocol"] == "graph_patch_v1" and report["generation_diagnostics"][-1]["recompile_executed"]:
            expected.append(False)
    assert compiled_formats == expected
    headless.planner_service = planner
    return headless, authoring, result, calls, raw


@pytest.mark.asyncio
async def test_production_entry_requests_recipe_and_stores_only_existing_graph_ir(tmp_path, monkeypatch):
    headless, authoring, result, calls, raw = await generate(tmp_path, monkeypatch)
    assert len(calls) == 2 and result.validation["valid"]
    assert calls[1][1] == RECIPE_SYSTEM_PROMPT
    prompt = json.loads(calls[1][2])
    assert prompt["generation_protocol_version"] == 1
    Draft202012Validator(prompt["required_schema"]).validate(raw)
    assert "graph_intent_contract" not in prompt
    assert "canonical_minimal_example" not in prompt
    for outputs in ([], [{"port": "result", "variable": "forged", "value_schema": {"type": "any"}}]):
        injected = json.loads(json.dumps(raw))
        next(node for node in injected["nodes"] if node["kind"] == "data_table_update")["outputs"] = outputs
        assert not Draft202012Validator(prompt["required_schema"]).is_valid(injected)
        with pytest.raises(ValueError):
            parse_generation_recipe(injected)
    assert set(prompt["required_schema"]["$defs"]["RecipeInput"]["properties"]) == {"port", "source_ref", "source_port"}
    stored = authoring.proposal_store.require(result.proposal_id)
    assert stored.status == "pending" and stored.revision == 1
    report = stored.payload["meta_planner_report"]
    assert report["ir_version"] == 3 and report["graph_ir_status"] == "current"
    assert report["generation_config"]["generation_protocol_version"] == 1
    assert not result.repair_used
    assert authoring.xpert_store.list_xperts() == []
    state = headless.proposal_state(result.proposal_id)
    assert state["candidate"]["draft"]["workflow"]
    observed = report["generation_evidence"]["calls"][1]
    assert observed["collector_to_validator"] == "same_structure"
    assert observed["validator"]["schema_valid"] is True


@pytest.mark.asyncio
async def test_live_recipe_entry_separates_owned_resources_and_forbids_ungranted_bindings(tmp_path, monkeypatch):
    _, _, result, calls, raw = await generate(tmp_path, monkeypatch)
    assert result.validation["valid"]
    prompt = json.loads(calls[1][2])
    contract = prompt["resource_contract"]
    assert contract["agent_bound_resources"] == []
    assert {item["kind"] for item in contract["node_owned_resources"]} == {"data_table_query", "data_table_update"}
    schema = prompt["required_schema"]
    assert schema["properties"]["resources"] == {"type": "array", "maxItems": 0, "items": False}
    validator = Draft202012Validator(schema)
    assert validator.is_valid(raw)
    injected = json.loads(json.dumps(raw))
    injected["resources"] = [{"target_ref": "answer", "kind": "toolset_resource", "resource_id": "foreign"}]
    assert not validator.is_valid(injected)
    injected = json.loads(json.dumps(raw))
    injected["nodes"][0]["resource_ref"]["resource_id"] = "foreign"
    assert not validator.is_valid(injected)


def test_resource_schemas_exclude_unindexed_retrieval_but_keep_legacy_agent_binding():
    from server.tests.test_meta_planner_controlled_writes import snapshot
    from server.meta_agent.resource_generation_contract import resource_generation_contract
    snap = snapshot()
    req = request(snap, "update")
    snap.knowledge_bases = [{"id": "kb-empty", "metadata": {}}]
    req.scope.knowledge_base_ids = ["kb-empty"]
    contract = resource_generation_contract(req, snap)
    assert not any(item["kind"] == "knowledge_retrieval" for item in contract["node_owned_resources"])
    assert any(item["kind"] == "knowledge_base" for item in contract["agent_bound_resources"])
    for build in (recipe_schema, graph_generation_schema):
        schema = build(req, snap)
        Draft202012Validator.check_schema(schema)
        node = schema["$defs"]["ModelNode_knowledge_retrieval"]
        shape = node["allOf"][-1] if "allOf" in node else node
        assert shape["properties"]["resource_ref"] is False
    snap.knowledge_bases[0]["metadata"]["active_version_id"] = "index-ready"
    contract = resource_generation_contract(req, snap)
    assert any(item["kind"] == "knowledge_retrieval" for item in contract["node_owned_resources"])


@pytest.mark.parametrize("operation,read", [("insert", False), ("update", True), ("delete", False)])
def test_generation_resource_choices_do_not_inherit_read_or_other_write_grants(operation, read):
    from server.tests.test_meta_planner_controlled_writes import snapshot
    from server.meta_agent.resource_generation_contract import resource_generation_contract
    snap = snapshot()
    req = request(snap, operation, read=read)
    owned = {item["kind"] for item in resource_generation_contract(req, snap)["node_owned_resources"]}
    assert owned == {f"data_table_{operation}"} | ({"data_table_query"} if read else set())


def test_generation_syntaxes_share_scoped_binding_schema(tmp_path, monkeypatch):
    _, _, _, snap = fixture(tmp_path, monkeypatch, "update")
    req = request(snap, "update")
    req.scope.allowed_node_kinds.append("toolset_resource")
    req.scope.toolset_ids = ["tools_allowed"]
    snap.toolsets = [{"id": "tools_allowed"}, {"id": "tools_foreign"}]
    for build in (recipe_schema, graph_generation_schema):
        schema = build(req, snap)
        binding_schema = {"$defs": schema["$defs"], **schema["properties"]["resources"]}
        validator = Draft202012Validator(binding_schema)
        allowed = {"target_ref": "answer", "kind": "toolset_resource", "resource_id": "tools_allowed"}
        assert validator.is_valid([allowed])
        assert not validator.is_valid([{**allowed, "resource_id": "tools_foreign"}])
        assert not validator.is_valid([{**allowed, "kind": "data_table"}])
        assert not validator.is_valid([{**allowed, "kind": "plugin_resource"}])
        snap.toolsets = []
        assert build(req, snap)["properties"]["resources"]["maxItems"] == 0
        snap.toolsets = [{"id": "tools_allowed"}, {"id": "tools_foreign"}]


@pytest.mark.asyncio
async def test_lowered_semantic_failure_uses_bounded_edits_once(tmp_path, monkeypatch):
    def omit_records(raw):
        raw["nodes"][1]["inputs"] = []

    corrected = from_intent(intent("update"))
    edits = {"operations": [{"op": "update_node", "node_ref": "write", "inputs": corrected["nodes"][1]["inputs"]}]}
    _, authoring, result, calls, _ = await generate(tmp_path, monkeypatch, malformed=omit_records, repaired=edits)
    assert len(calls) == 3 and result.repair_used
    assert result.validation["valid"], result.validation
    report = authoring.proposal_store.require(result.proposal_id).payload["meta_planner_report"]
    assert report["repair_protocol"] == RECIPE_EDIT_PROTOCOL
    repair_prompt = json.loads(calls[2][2])
    assert calls[2][1] == RECIPE_EDIT_SYSTEM_PROMPT
    assert set(repair_prompt["required_schema"]["properties"]) == {"operations"}
    assert "control_edges" not in repair_prompt["invalid_generation"]
    assert "graph_intent_contract" not in repair_prompt and "base_intent" not in repair_prompt
    assert "semantic_feedback" in repair_prompt
    assert authoring.xpert_store.list_xperts() == []


@pytest.mark.asyncio
async def test_unparsed_recipe_uses_one_full_recipe_repair(tmp_path, monkeypatch):
    corrected = from_intent(intent("update"))
    _, authoring, result, calls, _ = await generate(tmp_path, monkeypatch,
        malformed=lambda raw: raw.update(control_edges=[]), repaired=corrected)
    assert len(calls) == 3 and result.validation["valid"]
    assert calls[2][1] == RECIPE_SYSTEM_PROMPT
    prompt = json.loads(calls[2][2])
    assert "invalid_generation" in prompt
    assert prompt["required_schema"]["properties"]["generation_protocol_version"]["const"] == 1
    report = authoring.proposal_store.require(result.proposal_id).payload["meta_planner_report"]
    assert report["repair_protocol"] == "generation_recipe_v1"
    assert report["generation_diagnostics"][-1]["stage"] == "generation_recipe_v1"
    assert report["generation_evidence"]["calls"][-1]["stage"] == "generation_recipe_v1"
    assert authoring.xpert_store.list_xperts() == []


@pytest.mark.asyncio
async def test_new_generation_cannot_downgrade_to_full_graph_intent(tmp_path, monkeypatch):
    legacy = intent("update").model_dump(mode="json")
    _, authoring, result, calls, _ = await generate(tmp_path, monkeypatch,
        initial=legacy, repaired=legacy)
    assert len(calls) == 3
    assert result.validation["valid"] is False
    report = authoring.proposal_store.require(result.proposal_id).payload["meta_planner_report"]
    assert report["candidate_origin"] == "server_synthesized_fallback"
    assert report["generation_input_format"] == "graph_intent_v3_rejected"
    assert report["repair_protocol"] == "generation_recipe_v1"
    assert "GENERATION_PROTOCOL_REQUIRED" in json.dumps(report["validation"])
    assert authoring.xpert_store.list_xperts() == []


@pytest.mark.asyncio
async def test_failed_recipe_retains_lowered_original_for_manual_recovery(tmp_path, monkeypatch):
    headless, authoring, result, calls, _ = await generate(tmp_path, monkeypatch,
        malformed=lambda raw: raw["nodes"][1].update(inputs=[]), repaired={"operations": []})
    assert len(calls) == 3 and not result.validation["valid"]
    state = headless.proposal_state(result.proposal_id)
    assert state["mode"] == "recovery" and state["can_approve"] is False
    assert state["recovery"]["executable"] is False
    assert authoring.xpert_store.list_xperts() == []
    from server.meta_agent.failed_artifacts import load_failed_generation_artifact
    from server.meta_agent.generation_recipe import lower_generation_recipe
    from server.workflow_native.node_contracts import canonical_checksum
    artifact = load_failed_generation_artifact(authoring.proposal_store.require(result.proposal_id))
    first = artifact["attempts"][0]
    assert first["source_recipe"] is not None and first["intent"] is not None
    assert first["recipe"] is None
    assert first["source_recipe_checksum"] == canonical_checksum(first["source_recipe"])
    snapshot = headless.capability_snapshot_builder()
    restored = lower_generation_recipe(first["source_recipe"], request(snapshot, "update"), snapshot)
    assert first["intent_checksum"] == canonical_checksum(restored.model_dump(mode="json"))


@pytest.mark.asyncio
async def test_unparseable_repair_is_not_retried_or_presented_as_recoverable(tmp_path, monkeypatch):
    _, authoring, result, calls, _ = await generate(tmp_path, monkeypatch,
        malformed=lambda raw: raw.update(control_edges=[]), repaired={"control_flow": []})
    assert len(calls) == 3 and not result.validation["valid"]
    report = authoring.proposal_store.require(result.proposal_id).payload["meta_planner_report"]
    assert report["candidate_origin"] == "server_synthesized_fallback"
    assert authoring.xpert_store.list_xperts() == []


def test_old_graph_intent_remains_readable_through_default_compiler(tmp_path, monkeypatch):
    headless, authoring, _, snap = fixture(tmp_path, monkeypatch, "update")
    planner = MetaPlannerV2Service(authoring_service=authoring, preflight=headless.planner_service.preflight)
    before = [asdict(item) for item in authoring.proposal_store.list()]
    result = planner._compile_and_validate(request=request(snap, "update"), plan=_plan(),
        raw_blueprint=intent("update").model_dump_json(), snapshot=snap, target=None)
    assert result[2]["valid"] and result[0] is not None
    assert [asdict(item) for item in authoring.proposal_store.list()] == before
