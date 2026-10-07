"""Bounded semantic repair, with no provider calls or business Store access."""
from copy import deepcopy
import json

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from server.meta_agent.generation_recipe import lower_generation_recipe, parse_generation_recipe
from server.meta_agent.recipe_edits import apply_recipe_edits, recipe_edit_contract, recipe_edit_schema
from server.tests.meta_planner_recipe_fixtures import step
from server.tests.test_meta_planner_controlled_writes import compile_case
from server.tests.test_meta_planner_recipe_source_feedback import broken_common_consumer


def split_consumer_edits(raw):
    flow = deepcopy(raw["control_flow"])
    exists = flow[0]["branches"][0]["steps"][0] if flow[0].get("branches") else flow[1]
    found = next(b for b in exists["branches"] if b["outcome_ref"] == "unmatched")
    gate = found["steps"][0]
    found["steps"] = [gate]
    next(b for b in gate["branches"] if b["outcome_ref"] == "matched")["steps"].append(step("answer"))
    next(b for b in gate["branches"] if b["outcome_ref"] == "unmatched")["steps"] = [step("no_change")]
    return {"operations": [
        {"op": "clone_agent", "source_ref": "answer", "ref": "no_change", "title": "无需修改的结论",
         "task_input": "根据查询结果说明无需修改：{{lookup.result}}"},
        {"op": "replace_control_flow", "control_flow": flow},
        {"op": "set_final_output", "final_output": {"sources": [
            {"node_ref": "answer", "port": "result"}, {"node_ref": "no_change", "port": "result"}]}},
    ]}


@pytest.mark.parametrize("domain", ["quality", "incident"])
@pytest.mark.parametrize("read_error", [False, True])
def test_branch_local_repair_compiles_and_preserves_every_original_node(domain, read_error):
    raw, request, snapshot = broken_common_consumer(domain, read_error)
    base = parse_generation_recipe(raw)
    frozen = base.model_dump(mode="json")
    edits = split_consumer_edits(raw)
    schema = recipe_edit_schema(base, request, snapshot)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(edits)
    result = apply_recipe_edits(base, edits, request, snapshot)
    graph = lower_generation_recipe(result, request, snapshot)
    compile_case(graph, snapshot, request)
    assert base.model_dump(mode="json") == frozen
    assert [n.model_dump(mode="json") for n in result.nodes[:-1]] == frozen["nodes"]
    assert result.nodes[-1].task_ids == next(n.task_ids for n in base.nodes if n.ref == "answer")
    assert result.resources == base.resources and result.middleware == base.middleware
    assert result.prompt_profile_ids == base.prompt_profile_ids


def test_updates_are_field_explicit_not_whole_recipe_replacement():
    raw, request, snapshot = broken_common_consumer()
    base = parse_generation_recipe(raw)
    fixed = apply_recipe_edits(base, {"operations": [{"op": "update_node", "node_ref": "answer", "title": "新的结论标题"}]}, request, snapshot)
    before = base.model_dump(mode="json")
    expected = deepcopy(before)
    next(n for n in expected["nodes"] if n["ref"] == "answer")["title"] = "新的结论标题"
    assert fixed.model_dump(mode="json") == expected
    assert base.model_dump(mode="json") == before


@pytest.mark.parametrize("attack,code,index", [
    ("unknown", "RECIPE_EDIT_UNKNOWN_NODE", 1),
    ("clone", "RECIPE_EDIT_CLONED_NODE_IMMUTABLE", 1),
    ("unauthorized", "RECIPE_EDIT_NODE_UNAUTHORIZED", 1),
])
def test_edit_target_failure_is_located_and_atomic(attack, code, index):
    from server.meta_agent.generation_diagnostics import GenerationDiagnostics, diagnostic_checksum

    raw, request, snapshot = broken_common_consumer()
    base = parse_generation_recipe(raw)
    frozen = base.model_dump(mode="json")
    operations = [{"op": "update_node", "node_ref": "answer", "title": "不得部分保存"}]
    ref = "private_unrecognized_ref"
    if attack == "clone":
        operations = [split_consumer_edits(raw)["operations"][0]]
        ref = "no_change"
    elif attack == "unauthorized":
        request.scope.allowed_node_kinds.remove("condition")
        ref = "gate"
    operations.append({"op": "update_node", "node_ref": ref, "title": "失败操作"})
    observer = GenerationDiagnostics("recipe_edits_v1")
    observer.enter("patch_apply")
    with pytest.raises(ValueError) as failure:
        apply_recipe_edits(base, {"operations": operations}, request, snapshot)
    observer.exception(failure.value)
    issue = observer.as_dict()["issues"][0]
    assert issue["code"] == code
    assert issue["location"] == ["operations", index, "node_ref"]
    assert issue["edit_detail"] == {
        "operation_index": index, "op": "update_node", "node_ref_checksum": diagnostic_checksum(ref),
    }
    assert ref not in json.dumps(issue)
    assert base.model_dump(mode="json") == frozen


def test_unknown_edit_ref_does_not_disclose_secret_like_input():
    from server.meta_agent.generation_diagnostics import GenerationDiagnostics

    raw, request, snapshot = broken_common_consumer()
    observer = GenerationDiagnostics("recipe_edits_v1")
    secret = "sk_private_canary_never_disclose"
    with pytest.raises(ValueError) as failure:
        apply_recipe_edits(parse_generation_recipe(raw), {"operations": [
            {"op": "update_node", "node_ref": secret, "title": "不可泄露"},
        ]}, request, snapshot)
    observer.exception(failure.value)
    assert observer.as_dict()["issues"][0]["code"] == "RECIPE_EDIT_UNKNOWN_NODE"
    assert secret not in json.dumps(observer.as_dict()) + str(failure.value)


@pytest.mark.parametrize("domain", ["quality", "incident"])
def test_edit_prompt_projects_same_original_ref_boundary_as_schema_and_kernel(domain):
    from server.meta_agent.meta_planner_v2 import MetaPlannerV2Service
    from server.tests.test_meta_planner_controlled_writes import _plan

    raw, request, snapshot = broken_common_consumer(domain)
    request.scope.allowed_node_kinds.remove("condition")
    base = parse_generation_recipe(raw)
    with pytest.raises(ValidationError):
        MetaPlannerV2Service._recipe_edit_prompt(request, _plan(), snapshot, None, recipe=base)
    contract = recipe_edit_contract(base, request, snapshot)
    assert contract["update_node_refs"] == sorted(node.ref for node in base.nodes if node.kind != "condition")
    assert contract["cloneable_agent_refs"] == ["answer"]
    assert contract["cloned_nodes_editable"] is False
    assert contract["clone_required_fields"] == ["source_ref", "ref", "title", "task_input"]
    validator = Draft202012Validator(recipe_edit_schema(base, request, snapshot))
    for node in base.nodes:
        edits = {"operations": [{"op": "update_node", "node_ref": node.ref, "title": "更正显示标题"}]}
        expected = node.ref in contract["update_node_refs"]
        assert validator.is_valid(edits) is expected
        if expected:
            apply_recipe_edits(base, edits, request, snapshot)
        else:
            with pytest.raises(ValueError):
                apply_recipe_edits(base, edits, request, snapshot)
    request.scope.allowed_node_kinds.append("condition")
    prompt = json.loads(MetaPlannerV2Service._recipe_edit_prompt(request, _plan(), snapshot, None, recipe=base))
    assert prompt["edit_contract"] == recipe_edit_contract(base, request, snapshot)
    assert prompt["required_schema"] == recipe_edit_schema(base, request, snapshot)
    assert "类型替换须使用新 ref" not in json.dumps(prompt, ensure_ascii=False)
    assert any("同批克隆" in line for line in prompt["rules"])
    assert any("clone_agent" in line and "task_input" in line for line in prompt["rules"])


@pytest.mark.parametrize("key,value", [
    ("kind", "data_table_delete"), ("ref", "other"), ("task_ids", ["forged"]),
    ("resource_ref", {"resource_id": "other"}), ("sourceHandle", "route_1"),
    ("checksum", "a" * 64), ("policy", {"allow": True}),
])
def test_update_rejects_identity_authority_and_native_injection(key, value):
    raw, request, snapshot = broken_common_consumer()
    base = parse_generation_recipe(raw)
    edits = {"operations": [{"op": "update_node", "node_ref": "answer", key: value}]}
    with pytest.raises(ValueError):
        apply_recipe_edits(base, edits, request, snapshot)


@pytest.mark.parametrize("attack", ["empty", "no_change", "unknown", "duplicate", "too_many", "bad_config", "model_swap", "clone_write", "clone_unknown", "clone_collision", "new_table", "whole_recipe"])
def test_atomic_rejection_preserves_input(attack):
    raw, request, snapshot = broken_common_consumer()
    base = parse_generation_recipe(raw)
    frozen = base.model_dump(mode="json")
    operations = [{"op": "update_node", "node_ref": "answer", "title": "合法但不能部分保存"}]
    if attack == "empty":
        operations = []
    elif attack == "no_change":
        operations = [{"op": "update_node", "node_ref": "answer", "title": next(n.title for n in base.nodes if n.ref == "answer")}]
    elif attack == "unknown":
        operations.append({"op": "update_node", "node_ref": "unknown", "title": "未知"})
    elif attack == "duplicate":
        operations *= 2
    elif attack == "too_many":
        operations *= 17
    elif attack in {"bad_config", "model_swap", "new_table"}:
        node_ref = "write" if attack == "new_table" else "answer"
        config = deepcopy(next(n.config for n in base.nodes if n.ref == node_ref))
        config[{"bad_config": "sourceHandle", "model_swap": "model_id", "new_table": "tableId"}[attack]] = "forged"
        operations = [{"op": "update_node", "node_ref": node_ref, "config": config}]
    else:
        operations.append({"op": "clone_agent", "source_ref": {"clone_write": "write", "clone_unknown": "unknown"}.get(attack, "answer"),
                           "ref": "answer" if attack == "clone_collision" else "cloned", "title": "分支结论", "task_input": "只读证据"})
    edits = frozen if attack == "whole_recipe" else {"operations": operations}
    with pytest.raises(ValueError):
        apply_recipe_edits(base, edits, request, snapshot)
    assert base.model_dump(mode="json") == frozen


def test_kernel_does_not_guess_missing_branches_or_make_bad_graph_valid():
    raw, request, snapshot = broken_common_consumer()
    base = parse_generation_recipe(raw)
    result = apply_recipe_edits(base, {"operations": [{"op": "update_node", "node_ref": "answer", "title": "仅改标题"}]}, request, snapshot)
    with pytest.raises(ValueError):
        compile_case(lower_generation_recipe(result, request, snapshot), snapshot, request)
    assert result.control_flow == base.control_flow


def test_schema_only_exposes_existing_refs_configs_and_semantic_edits():
    raw, request, snapshot = broken_common_consumer()
    schema = recipe_edit_schema(parse_generation_recipe(raw), request, snapshot)
    text = json.dumps(schema)
    assert "data_table_insert" not in text and "vision_understanding" not in text
    assert set(schema["properties"]) == {"operations"}
    assert schema["properties"]["operations"]["maxItems"] == 16
    for field in ("tableId", "sourceHandle", "pinnedSchemaVersion", "expected_candidate_checksum"):
        assert field not in text


@pytest.mark.parametrize("field,value", [("source_agent_id", "another-agent"), ("method_skill_ids", ["another-skill"])])
def test_node_edit_cannot_replace_agent_identity_or_skill(field, value):
    raw, request, snapshot = broken_common_consumer()
    base = parse_generation_recipe(raw)
    config = deepcopy(next(node.config for node in base.nodes if node.ref == "answer"))
    config[field] = value
    with pytest.raises(ValueError, match="身份|技能"):
        apply_recipe_edits(base, {"operations": [{"op": "update_node", "node_ref": "answer", "config": config}]}, request, snapshot)


def test_clone_refuses_agent_with_bound_resource():
    raw, request, snapshot = broken_common_consumer()
    raw["resources"] = [{"kind": "toolset_resource", "resource_id": "tools", "target_ref": "answer"}]
    base = parse_generation_recipe(raw)
    with pytest.raises(ValueError, match="资源绑定"):
        apply_recipe_edits(base, split_consumer_edits(raw), request, snapshot)


def test_edit_byte_budget_and_depth_are_enforced_before_merge():
    raw, request, snapshot = broken_common_consumer()
    base = parse_generation_recipe(raw)
    for invalid in ("x" * (65 * 1024), [[[["x"]]]]):
        config = {"unknown": invalid}
        if isinstance(invalid, list):
            for _ in range(33):
                config = {"nested": config}
        with pytest.raises(ValueError, match="64 KiB|32 层"):
            apply_recipe_edits(base, {"operations": [{"op": "update_node", "node_ref": "answer", "config": config}]}, request, snapshot)


@pytest.mark.asyncio
@pytest.mark.parametrize("repair_valid", [False, True])
async def test_generation_repairs_edits_not_recopied_graph_and_keeps_pending(tmp_path, monkeypatch, repair_valid):
    from server.tests import test_meta_planner_write_headless
    from server.tests.test_meta_planner_recipe_integration import generate
    from server.meta_agent.recipe_edits import RECIPE_EDIT_PROTOCOL, RECIPE_EDIT_SYSTEM_PROMPT
    raw, _, snapshot = broken_common_consumer()
    monkeypatch.setattr(test_meta_planner_write_headless, "snapshot", lambda: snapshot)
    payload = split_consumer_edits(raw) if repair_valid else {"operations": []}
    _, authoring, result, calls, _ = await generate(tmp_path, monkeypatch, initial=raw, repaired=payload)
    assert len(calls) == 3
    assert calls[-1][1] == RECIPE_EDIT_SYSTEM_PROMPT
    assert result.validation["valid"] is repair_valid, result.validation
    proposal = authoring.proposal_store.require(result.proposal_id)
    assert proposal.status == "pending" and proposal.revision == 1
    assert authoring.xpert_store.list_xperts() == []
    report = proposal.payload["meta_planner_report"]
    assert report["repair_protocol"] == RECIPE_EDIT_PROTOCOL
    observed = report["generation_evidence"]["calls"][-1]
    assert observed["stage"] == RECIPE_EDIT_PROTOCOL
    assert observed["collector"]["body_checksum"] == observed["validator"]["body_checksum"]
    assert observed["collector_to_validator"] == "same_structure"
    assert observed["validator"]["schema_valid"] is True
    assert "node_count" not in observed["validator"]["structure"]
    assert observed["validator"]["structure"]["operation_count"] == len(payload["operations"])
    if not repair_valid:
        assert any(issue["code"] == "RECIPE_REPAIR_UNCHANGED"
                   for issue in report["generation_diagnostics"][-1]["issues"])


@pytest.mark.asyncio
async def test_generation_preserves_safe_rejected_edit_location_without_extra_call(tmp_path, monkeypatch):
    from server.tests import test_meta_planner_write_headless
    from server.tests.test_meta_planner_recipe_integration import generate

    raw, _, snapshot = broken_common_consumer()
    monkeypatch.setattr(test_meta_planner_write_headless, "snapshot", lambda: snapshot)
    edits = split_consumer_edits(raw)
    edits["operations"].insert(1, {"op": "update_node", "node_ref": "no_change", "title": "不应执行"})
    _, authoring, result, calls, _ = await generate(tmp_path, monkeypatch, initial=raw, repaired=edits)
    assert len(calls) == 3 and result.validation["valid"] is False
    proposal = authoring.proposal_store.require(result.proposal_id)
    assert proposal.status == "pending" and proposal.revision == 1
    assert authoring.xpert_store.list_xperts() == []
    report = proposal.payload["meta_planner_report"]
    issue = report["generation_diagnostics"][-1]["issues"][0]
    assert issue["code"] == "RECIPE_EDIT_CLONED_NODE_IMMUTABLE"
    assert issue["location"] == ["operations", 1, "node_ref"]
    assert issue["edit_detail"]["operation_index"] == 1
    assert "no_change" not in json.dumps(issue)
    assert report["generation_evidence"]["calls"][-1]["validator"]["schema_valid"] is False


def test_repair_prompt_is_smaller_and_contains_no_unrelated_node_catalog():
    from server.meta_agent.meta_planner_v2 import MetaPlannerV2Service
    from server.tests.test_meta_planner_controlled_writes import _plan
    raw, request, snapshot = broken_common_consumer()
    recipe = parse_generation_recipe(raw)
    full = MetaPlannerV2Service._recipe_repair_prompt(request, _plan(), snapshot, None, recipe=recipe)
    focused = MetaPlannerV2Service._recipe_edit_prompt(request, _plan(), snapshot, None, recipe=recipe)
    payload = json.loads(focused)
    assert len(focused) < len(full) * 0.8
    assert set(payload["node_contracts"]) == {node.kind for node in recipe.nodes}
    assert payload["repair_focus"] == json.loads(full)["repair_focus"]
    assert payload["invalid_generation"] == recipe.model_dump(mode="json")
    assert "capability_snapshot" not in payload and "target_xpert" not in payload
    table = payload["fixed_table_schemas"][0]
    assert set(table) == {"id", "schema_versions"}
    assert table["id"] == snapshot.data_tables[0]["id"]
    assert all(set(field) == {"name", "data_type", "required"}
               for version in table["schema_versions"] for field in version["fields"])


def test_edit_command_fields_are_schema_derived_and_precede_failed_recipe():
    from server.meta_agent.meta_planner_v2 import MetaPlannerV2Service
    from server.tests.test_meta_planner_controlled_writes import _plan
    raw, request, snapshot = broken_common_consumer()
    prompt = MetaPlannerV2Service._recipe_edit_prompt(request, _plan(), snapshot, None,
                                                    recipe=parse_generation_recipe(raw))
    payload = json.loads(prompt)
    commands = payload["edit_contract"]["operations"]
    schema = payload["required_schema"]
    mapping = schema["properties"]["operations"]["items"]["discriminator"]["mapping"]
    for op, ref in mapping.items():
        definition = schema["$defs"][ref.rsplit("/", 1)[-1]]
        assert commands[op]["required_fields"] == definition["required"]
        assert set(commands[op]["optional_fields"]) == set(definition["properties"]) - set(definition["required"])
    assert commands["replace_control_flow"]["required_fields"] == ["op", "control_flow"]
    assert commands["update_node"]["required_fields"] == ["op", "node_ref"]
    assert "config" not in commands["replace_control_flow"]["optional_fields"]
    assert prompt.index('"edit_contract"') < prompt.index('"invalid_generation"')


@pytest.mark.asyncio
async def test_invalid_edit_protocol_does_not_report_unchecked_issues_as_fixed(tmp_path, monkeypatch):
    from server.tests import test_meta_planner_write_headless
    from server.tests.test_meta_planner_recipe_integration import generate
    raw, _, snapshot = broken_common_consumer()
    raw["control_flow"].append(step("answer"))
    monkeypatch.setattr(test_meta_planner_write_headless, "snapshot", lambda: snapshot)
    malformed = {"operations": [{"op": "replace_control_flow", "config": {"control_flow": []}},
                                {"op": "update_node", "config": {}}]}
    headless, authoring, result, calls, _ = await generate(tmp_path, monkeypatch, initial=raw, repaired=malformed)
    assert len(calls) == 3 and not result.validation["valid"]
    report = authoring.proposal_store.require(result.proposal_id).payload["meta_planner_report"]
    diagnostics = report["generation_diagnostics"][-1]
    progress = diagnostics["recipe_repair_progress"]
    assert not diagnostics["recompile_executed"]
    assert progress["assessment"] == "not_rechecked"
    assert progress["persisting_issue_count"] is None
    assert progress["no_longer_observed_issue_count"] is None
    assert progress["control_flow_before_checksum"] == progress["control_flow_after_checksum"]
    public = headless.proposal_state(result.proposal_id)["recovery"]["attempts"][-1]["diagnostics"]["recipe_repair_progress"]
    assert public["assessment"] == "not_rechecked" and public["no_longer_observed_issue_count"] is None


def test_repair_table_context_does_not_disclose_records_defaults_or_descriptions():
    from server.meta_agent.meta_planner_v2 import MetaPlannerV2Service
    from server.tests.test_meta_planner_controlled_writes import _plan
    raw, request, snapshot = broken_common_consumer()
    table = snapshot.data_tables[0]
    table.update(records=[{"secret": "PRIVATE_RECORD_CANARY"}], description="PRIVATE_DESCRIPTION_CANARY")
    for field in table["schema_versions"][0]["fields"]:
        field.update(default="PRIVATE_DEFAULT_CANARY", description="PRIVATE_FIELD_CANARY")
    prompt = MetaPlannerV2Service._recipe_edit_prompt(request, _plan(), snapshot, None, recipe=parse_generation_recipe(raw))
    assert "PRIVATE_" not in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("read_error", [False, True])
async def test_explicit_branch_repair_uses_same_edits_then_existing_patch_preview(tmp_path, monkeypatch, read_error):
    from server.meta_agent.model_repair import ExplicitModelRepairService, ModelRepairPreflightRequest
    from server.tests import test_meta_planner_recipe_integration, test_meta_planner_write_headless
    from server.tests.test_meta_planner_recipe_integration import generate
    from server.tests.test_meta_planner_model_repair import ROUTE, consent
    from server.tests.test_meta_planner_recipe_matrix import guarded_recipe
    raw, _, snapshot = broken_common_consumer("incident", read_error)
    initial, initial_request, _ = guarded_recipe("incident", read_error)
    monkeypatch.setattr(test_meta_planner_write_headless, "snapshot", lambda: snapshot)
    monkeypatch.setattr(test_meta_planner_write_headless, "intent",
                        lambda _: lower_generation_recipe(initial, initial_request, snapshot))
    for helper in (test_meta_planner_write_headless, test_meta_planner_recipe_integration):
        monkeypatch.setattr(helper, "request", lambda *_: initial_request.model_copy(deep=True))
    headless, authoring, generated, _, _ = await generate(tmp_path, monkeypatch, initial=raw, repaired={"operations": []})
    before = authoring.proposal_store.require(generated.proposal_id)
    calls = []

    async def completion(*args):
        calls.append(args)
        return {"text": json.dumps(split_consumer_edits(raw)),
                "receipt": {"provider_dispatched": True, "response_received": True, "total_tokens": 100}}

    service = ExplicitModelRepairService(headless, route_projection=lambda _: deepcopy(ROUTE), completion=completion)
    state = headless.proposal_state(generated.proposal_id)
    request = ModelRepairPreflightRequest(proposal_revision=1, artifact_checksum=state["recovery"]["artifact_checksum"],
        expected_graph_checksum=state["graph_checksum"], expected_candidate_checksum=state["candidate_checksum"], model_id="test/repair")
    approved = consent(request, service.preflight(generated.proposal_id, request))
    assert not calls
    result = await service.execute(generated.proposal_id, approved)
    assert result["status"] == "suggested", result
    assert result["preview_summary"]["can_apply"] and result["repair_protocol"] == "recipe_edits_v1"
    assert len(calls) == 1 and not result["automatically_applied"]
    assert authoring.proposal_store.require(generated.proposal_id).payload == before.payload
    assert authoring.proposal_store.require(generated.proposal_id).revision == 1
    assert authoring.xpert_store.list_xperts() == []
    assert await service.execute(generated.proposal_id, approved) == result
    assert len(calls) == 1
