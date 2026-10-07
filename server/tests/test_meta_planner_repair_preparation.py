"""Repair preparation must be JSON-safe and cannot invent a paid attempt."""
from copy import deepcopy
import json

import pytest

from server.meta_agent.failed_artifacts import load_failed_generation_artifact
from server.meta_agent.generation_diagnostics import MAX_DIAGNOSTIC_ISSUES
from server.meta_agent.generation_evidence import GenerationEvidence, observe_generation_request
from server.meta_agent.generation_recipe import lower_generation_recipe, parse_generation_recipe
from server.meta_agent.meta_planner_v2 import MetaPlannerV2Service, validate_blueprint_authorization
from server.tests.meta_planner_recipe_fixtures import from_intent
from server.tests.test_meta_planner_controlled_writes import intent, request, _plan
from server.tests.test_meta_planner_recipe_matrix import guarded_recipe
from server.tests.test_meta_planner_write_generation_failures import _forbid_records
from server.tests.test_meta_planner_write_headless import fixture
from server.workflow_native.node_contracts import canonical_checksum
from server.xpert_runtime.authoring_store import AuthoringProposalStore, AuthoringProposalValidationError


def invalid_type(raw):
    answer = next(node for node in raw["nodes"] if node["ref"] == "answer")
    answer["inputs"] = [{"port": "task", "source_ref": "lookup", "source_port": "result"}]
    answer["config"]["task_input"] = "{{lookup.result}}"
    return raw


@pytest.mark.parametrize("domain", ["quality", "incident"])
def test_recipe_repair_uses_real_type_diagnostic_safe_projection(domain):
    raw, req, snap = guarded_recipe(domain)
    invalid_type(raw)
    before = deepcopy(raw)
    graph = lower_generation_recipe(raw, req, snap)
    types = []
    issues = validate_blueprint_authorization(req, _plan(), graph, snap, input_type_issues=types)
    assert types  # Independent type diagnostics are collected before resolution.
    payload = json.loads(MetaPlannerV2Service._recipe_repair_prompt(
        req, _plan(), snap, None, recipe=parse_generation_recipe(raw), issues=issues,
    ))
    detail = payload["semantic_feedback"]["input_type_issues"]
    assert detail == [item.repair_detail() for item in types]
    assert detail[0]["node_ref"] == "answer"
    assert detail[0]["source_schema"]["type"] == "object"
    assert detail[0]["target_schema"]["type"] == "string"
    assert detail[0]["source_assignable"] is False
    assert '"properties"' not in json.dumps(detail)
    assert raw == before


def preparation_fixture(tmp_path, monkeypatch, *, unparsed=False):
    _forbid_records(monkeypatch)
    headless, authoring, _, snap = fixture(tmp_path, monkeypatch, "update")
    req = request(snap, "update")
    raw = invalid_type(from_intent(intent("update")))
    if unparsed:
        raw = {"unexpected": "no_valid_recipe"}
    corrected = from_intent(intent("update"))
    answer = next(node for node in corrected["nodes"] if node["ref"] == "answer")
    edits = {"operations": [{"op": "update_node", "node_ref": "answer", "inputs": answer["inputs"], "config": answer["config"]}]}
    responses = [_plan().model_dump_json(), json.dumps(raw), json.dumps(corrected if unparsed else edits)]
    calls = []

    async def completion(*args):
        calls.append(args)
        assert len(calls) <= 3
        observe_generation_request({"model": args[0], "messages": [{"role": "user", "content": args[2]}]}, "offline")
        return responses[len(calls) - 1]

    evidence = GenerationEvidence()
    planner = MetaPlannerV2Service(authoring_service=authoring,
        preflight=headless.planner_service.preflight, completion=completion, generation_evidence=evidence)
    headless.planner_service = planner
    return headless, authoring, planner, req, snap, raw, responses, calls, evidence


@pytest.mark.asyncio
async def test_nonempty_type_diagnostic_reaches_exactly_one_recipe_repair(tmp_path, monkeypatch):
    headless, authoring, planner, req, snap, _, _, calls, _ = preparation_fixture(tmp_path, monkeypatch)
    result = await planner.generate(req, snap)
    assert len(calls) == 3 and result.repair_used and result.validation["valid"]
    repair = json.loads(calls[2][2])
    assert repair["semantic_feedback"]["input_type_issues"]
    proposal = authoring.proposal_store.require(result.proposal_id)
    assert proposal.status == "pending" and proposal.revision == 1
    assert authoring.xpert_store.list_xperts() == []
    assert headless.proposal_state(result.proposal_id)["candidate"]


@pytest.mark.asyncio
@pytest.mark.parametrize("exception_type", [TypeError, RuntimeError])
async def test_preparation_failure_retains_original_without_dispatch_or_approval(tmp_path, monkeypatch, exception_type):
    headless, authoring, planner, req, snap, raw, _, calls, _ = preparation_fixture(tmp_path, monkeypatch)
    private_error = "PREPARATION_PRIVATE_SENTINEL password=SYNTHETIC_ONLY /private/runtime/path"

    def fail(*args, **kwargs):
        raise exception_type(private_error)

    monkeypatch.setattr(planner, "_recipe_edit_prompt", fail)
    result = await planner.generate(req, snap)
    assert len(calls) == 2 and result.repair_used is False
    assert not result.validation["valid"]
    assert any("未派发" in warning for warning in result.warnings)
    proposal = authoring.proposal_store.require(result.proposal_id)
    report = proposal.payload["meta_planner_report"]
    assert report["repair_protocol"] == "none"
    assert report["repair_preparation"] == {
        "status": "failed", "protocol": "recipe_edits_v1",
        "completion_started": False, "reason_code": "REPAIR_PREPARATION_FAILED",
    }
    assert len(report["generation_evidence"]["calls"]) == 2
    assert len(report["generation_attempts"]) == 1
    prepare = report["generation_diagnostics"][-1]
    assert prepare["stage"] == prepare["failed_phase"] == "repair_preparation"
    assert prepare["recompile_executed"] is False
    assert prepare["issues"][0]["code"] == "REPAIR_PREPARATION_FAILED"
    assert prepare["phase_results"] == [{
        "id": "repair_preparation", "status": "failed", "blocked_by": None,
    }]
    artifact = load_failed_generation_artifact(proposal)
    assert len(artifact["attempts"]) == 1
    original = artifact["attempts"][0]
    parsed = parse_generation_recipe(raw).model_dump(mode="json")
    assert original["source_recipe"] == parsed
    assert original["source_recipe_checksum"] == canonical_checksum(parsed)
    assert original["intent"] is not None and original["stage"] == "capability_compile"
    for with_payload in (False, True):
        public = json.dumps(AuthoringProposalStore.serialize(proposal, include_payload=with_payload))
        assert "PREPARATION_PRIVATE_SENTINEL" not in public
        assert "SYNTHETIC_ONLY" not in public and "/private/runtime/path" not in public
        assert "_meta_planner_generation_artifact" not in public
    with pytest.raises(AuthoringProposalValidationError):
        authoring.approve(result.proposal_id, revision=1)
    authoring.proposal_store = AuthoringProposalStore(authoring.proposal_store.storage_dir)
    restored = load_failed_generation_artifact(authoring.proposal_store.require(result.proposal_id))
    assert restored == artifact
    state = headless.proposal_state(result.proposal_id)
    assert state["mode"] == "recovery" and not state["can_approve"]
    assert state["recovery"]["status"] == "editable" and not state["recovery"]["executable"]
    assert authoring.xpert_store.list_xperts() == [] and len(calls) == 2


@pytest.mark.asyncio
async def test_preparation_failure_does_not_invent_unparsed_candidate(tmp_path, monkeypatch):
    _, authoring, planner, req, snap, _, _, calls, _ = preparation_fixture(tmp_path, monkeypatch, unparsed=True)

    def fail(*args, **kwargs):
        raise TypeError("internal preparation failure")

    monkeypatch.setattr(planner, "_recipe_repair_prompt", fail)
    result = await planner.generate(req, snap)
    proposal = authoring.proposal_store.require(result.proposal_id)
    report = proposal.payload["meta_planner_report"]
    assert len(calls) == 2 and not result.repair_used and not result.validation["valid"]
    assert not report["failure_artifact"]["recoverable"]
    assert report["candidate_origin"] == "server_synthesized_fallback"
    artifact = load_failed_generation_artifact(proposal)
    assert len(artifact["attempts"]) == 1
    assert artifact["attempts"][0]["retention_status"] == "unparsed"
    with pytest.raises(AuthoringProposalValidationError):
        authoring.approve(result.proposal_id, revision=1)


@pytest.mark.asyncio
async def test_after_completion_started_error_is_not_misreported_as_preparation(tmp_path, monkeypatch):
    _, authoring, planner, req, snap, _, responses, calls, evidence = preparation_fixture(tmp_path, monkeypatch)
    # Omit a record binding to reach repair without relying on the type projection fix.
    raw = from_intent(intent("update"))
    raw["nodes"][1]["inputs"] = []
    responses[1] = json.dumps(raw)
    original = planner.completion

    async def uncertain(*args):
        result = await original(*args)
        if len(calls) == 3:
            raise RuntimeError("provider result uncertain")
        return result

    planner.completion = uncertain
    before = len(authoring.proposal_store.list())
    with pytest.raises(RuntimeError, match="provider result uncertain"):
        await planner.generate(req, snap)
    assert len(calls) == 3 and len(evidence.as_dict()["calls"]) == 3
    assert len(authoring.proposal_store.list()) == before


@pytest.mark.asyncio
async def test_preparation_does_not_swallow_cancellation(tmp_path, monkeypatch):
    import asyncio

    _, authoring, planner, req, snap, _, _, calls, _ = preparation_fixture(tmp_path, monkeypatch)

    def cancelled(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(planner, "_recipe_edit_prompt", cancelled)
    before = len(authoring.proposal_store.list())
    with pytest.raises(asyncio.CancelledError):
        await planner.generate(req, snap)
    assert len(calls) == 2 and len(authoring.proposal_store.list()) == before


def test_type_feedback_is_bounded_without_losing_omitted_count():
    raw, req, snap = guarded_recipe()
    invalid_type(raw)
    answer = next(node for node in raw["nodes"] if node["ref"] == "answer")
    answer["inputs"] *= 40
    extra = deepcopy(answer)
    extra["ref"] = "extra_answer"
    raw["nodes"].append(extra)
    raw["control_flow"].append({"type": "node", "node_ref": "extra_answer"})
    graph = lower_generation_recipe(raw, req, snap)
    types = []
    validate_blueprint_authorization(req, _plan(), graph, snap, input_type_issues=types)
    assert len(types) > MAX_DIAGNOSTIC_ISSUES
    payload = json.loads(MetaPlannerV2Service._recipe_repair_prompt(
        req, _plan(), snap, None, recipe=parse_generation_recipe(raw),
    ))
    feedback = payload["semantic_feedback"]
    assert len(feedback["input_type_issues"]) == MAX_DIAGNOSTIC_ISSUES
    assert feedback["input_type_issue_count"] == len(types)
    assert feedback["omitted_input_type_issue_count"] == len(types) - MAX_DIAGNOSTIC_ISSUES


@pytest.mark.asyncio
async def test_preparation_failure_preserves_recipe_when_lowering_is_blocked(tmp_path, monkeypatch):
    headless, authoring, planner, req, snap, _, responses, calls, _ = preparation_fixture(tmp_path, monkeypatch)
    raw = from_intent(intent("update"))
    raw["control_flow"][0]["node_ref"] = "not_declared"
    responses[1] = json.dumps(raw)

    def fail(*args, **kwargs):
        raise TypeError("preparation failed after lowering failed")

    monkeypatch.setattr(planner, "_recipe_edit_prompt", fail)
    result = await planner.generate(req, snap)
    assert len(calls) == 2 and not result.repair_used and not result.validation["valid"]
    artifact = load_failed_generation_artifact(authoring.proposal_store.require(result.proposal_id))
    original = artifact["attempts"][0]
    assert len(artifact["attempts"]) == 1 and original["intent"] is None
    assert original["recipe"] == parse_generation_recipe(raw).model_dump(mode="json")
    authoring.proposal_store = AuthoringProposalStore(authoring.proposal_store.storage_dir)
    state = headless.proposal_state(result.proposal_id)
    assert state["recovery"]["source_format"] == "recipe_v1"
    assert state["recovery"]["status"] == "editable" and not state["can_approve"]
    assert not state["recovery"]["executable"] and len(calls) == 2


@pytest.mark.asyncio
async def test_legacy_patch_preparation_uses_same_predispatch_boundary(tmp_path, monkeypatch):
    from server.tests.meta_planner_legacy_replay import LegacyGraphReplayService

    headless, authoring, _, req, snap, _, responses, calls, evidence = preparation_fixture(tmp_path, monkeypatch)
    graph = intent("update")
    graph.nodes[1].inputs = []
    responses[1] = graph.model_dump_json()
    planner = LegacyGraphReplayService(authoring_service=authoring, preflight=headless.planner_service.preflight,
        completion=headless.planner_service.completion, generation_evidence=evidence)

    def fail(*args, **kwargs):
        raise TypeError("legacy prompt preparation failed")

    monkeypatch.setattr(planner, "_patch_repair_prompt", fail)
    result = await planner.generate(req, snap)
    proposal = authoring.proposal_store.require(result.proposal_id)
    report = proposal.payload["meta_planner_report"]
    assert not result.validation["valid"] and not result.repair_used and len(calls) == 2
    assert report["repair_preparation"]["protocol"] == "graph_patch_v1"
    artifact = load_failed_generation_artifact(proposal)
    assert len(artifact["attempts"]) == 1
    assert artifact["attempts"][0]["intent"] == graph.model_dump(mode="json")


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_prompt", [None, {}, ""])
async def test_invalid_prompt_return_does_not_start_completion(tmp_path, monkeypatch, bad_prompt):
    _, authoring, planner, req, snap, _, _, calls, _ = preparation_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(planner, "_recipe_edit_prompt", lambda *args, **kwargs: bad_prompt)
    result = await planner.generate(req, snap)
    report = authoring.proposal_store.require(result.proposal_id).payload["meta_planner_report"]
    assert len(calls) == 2 and not result.repair_used and not result.validation["valid"]
    assert report["repair_preparation"]["completion_started"] is False
