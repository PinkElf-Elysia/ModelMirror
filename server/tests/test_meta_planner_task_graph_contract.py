"""Task responsibility and execution branches share one bounded planning contract."""

import json

import pytest

from server.meta_agent.control_flow import analyze_control_flow
from server.meta_agent.generation_contract import generation_task_plan_schema
from server.meta_agent.graph_ir_v3 import (
    decompile_candidate_to_graph_intent,
    workflow_semantic_checksum,
)
from server.tests.meta_planner_recipe_fixtures import from_intent, step
from server.meta_agent.meta_planner_v2 import (
    MetaPlannerV2Service,
    compile_xpert_candidate,
    validate_blueprint_authorization,
    validate_task_plan,
)
from server.meta_agent.schemas import MetaPlannerTask, MetaPlannerTaskPlan
from server.skills.draft_store import WorkspaceSkillDraftStore
from server.tests.test_meta_planner_control_flow import _branch_intent, _route_error_intent
from server.tests.test_meta_planner_v2 import _request, _snapshot
from server.xpert_runtime.authoring_service import AuthoringService
from server.xpert_runtime.authoring_store import AuthoringProposalStore
from server.xperts.store import XpertStore
from server.xperts.validation import validate_xpert_definition


def _plan(entries):
    return MetaPlannerTaskPlan(
        summary="合成审核任务，仅用于离线契约验证。",
        tasks=[MetaPlannerTask(
            task_id=ref, title="形成审核结论", objective="根据已验证证据说明审核结论。",
            depends_on=dependencies, input_contract=["已验证证据"],
            output_contract="中文审核结论", acceptance="结论必须与实际执行证据一致。",
        ) for ref, dependencies in entries],
    )


def _context():
    request, snapshot = _request(), _snapshot()
    request.max_agents = 2
    request.scope.allowed_node_kinds = [
        "input", "output", "workflow_agent", "condition", "multi_route", "terminate_error",
    ]
    return request, snapshot


def _repair_prompt(plan):
    request, snapshot = _context()
    return json.loads(MetaPlannerV2Service._plan_repair_prompt(
        request, snapshot, plan.model_dump_json(), validate_task_plan(plan, max_agents=2),
    ))


def test_initial_and_repair_share_one_terminal_responsibility_contract():
    request, snapshot = _context()
    initial = json.loads(MetaPlannerV2Service._plan_prompt(request, snapshot))
    repair = _repair_prompt(_plan([("accepted", []), ("rejected", [])]))
    assert initial["task_planning_contract"] == repair["task_planning_contract"]
    contract = initial["task_planning_contract"]["task_graph_contract"]
    assert contract["terminal_task_count"] == 1
    assert "其他任务依赖" in contract["terminal_definition"]
    guidance = json.dumps(contract, ensure_ascii=False)
    for required in ("互斥", "共同覆盖", "独立职责", "伪造", "虚假", "辅助节点"):
        assert required in guidance
    assert "不能把所有任务压成一个" not in guidance
    assert len(guidance) < 1_800
    assert initial["required_schema"] == repair["required_schema"] == generation_task_plan_schema()
    assert "task_graph_contract" not in initial["required_schema"]["properties"]


@pytest.mark.parametrize("entries,terminals,code", [
    ([("answer", [])], ["answer"], None),
    ([("extract", []), ("answer", ["extract"])], ["answer"], None),
    ([("a", []), ("b", []), ("answer", ["a", "b"])], ["answer"], None),
    ([("accepted", []), ("rejected", [])], ["accepted", "rejected"], "TASK_TERMINAL_COUNT_INVALID"),
    ([("a", []), ("b", ["a"]), ("c", ["a"])], ["b", "c"], "TASK_TERMINAL_COUNT_INVALID"),
    ([("a", ["b"]), ("b", ["a"])], [], "TASK_DEPENDENCY_GRAPH_INVALID"),
    ([("a", ["unknown"])], ["a"], "TASK_DEPENDENCY_UNKNOWN"),
    ([("a", ["a"])], ["a"], "TASK_DEPENDENCY_SELF"),
    ([("a", []), ("a", [])], ["a", "a"], "TASK_DEPENDENCY_GRAPH_INVALID"),
])
def test_repair_topology_matches_validation_without_changing_the_plan(entries, terminals, code):
    plan = _plan(entries)
    before = plan.model_dump(mode="json")
    issues = validate_task_plan(plan, max_agents=2)
    prompt = _repair_prompt(plan)
    diagnostics = prompt["task_graph_diagnostics"]
    assert diagnostics["status"] == "available"
    assert diagnostics["task_count"] == len(entries)
    assert diagnostics["terminal_task_ids"] == terminals
    assert diagnostics["terminal_task_count"] == len(terminals)
    assert diagnostics["dependencies"] == [
        {"task_id": ref, "depends_on": sorted(dep for dep in dependencies if dep in {item[0] for item in entries})}
        for ref, dependencies in sorted(entries)
    ]
    assert prompt["validation_issues"] == issues
    assert json.loads(prompt["invalid_task_plan"]) == before == plan.model_dump(mode="json")
    if code:
        assert code in diagnostics["issue_codes"]
        assert issues
    else:
        assert diagnostics["issue_codes"] == [] and issues == []
    fields = list(prompt)
    assert fields.index("task_graph_diagnostics") < fields.index("invalid_task_plan")


def test_repair_diagnostics_are_bounded_and_do_not_repeat_free_text():
    plan = _plan([(f"task_{i}", ["private-value-" * 1_000] * 8) for i in range(8)])
    plan.summary = "private-summary-marker"
    plan.tasks[0].objective = "private-objective-marker"
    diagnostics = _repair_prompt(plan)["task_graph_diagnostics"]
    encoded = json.dumps(diagnostics, ensure_ascii=False)
    assert len(encoded) < 4_000
    assert "private-" not in encoded
    assert diagnostics["omitted_unknown_dependency_count"] == 64
    assert len(diagnostics["dependencies"]) == 8
    assert diagnostics["terminal_task_count"] == 8


@pytest.mark.parametrize("raw", [
    "not json", "[]", '{"tasks":[]}',
    json.dumps({**_plan([("answer", [])]).model_dump(mode="json"), "graph": {"nodes": []}}),
])
def test_invalid_task_schema_has_no_guessed_topology(raw):
    request, snapshot = _context()
    prompt = json.loads(MetaPlannerV2Service._plan_repair_prompt(request, snapshot, raw, ["invalid plan"]))
    assert prompt["task_graph_diagnostics"] == {
        "status": "unavailable", "issue_codes": ["TASK_PLAN_PARSE_INVALID"],
    }
    assert prompt["invalid_task_plan"] == raw


@pytest.mark.parametrize("factory,scenarios", [(_branch_intent, 2), (_route_error_intent, 3)])
def test_shared_terminal_task_preserves_real_exclusive_outputs(factory, scenarios):
    request, snapshot = _context()
    plan, intent = _plan([("answer", [])]), factory()
    assert validate_blueprint_authorization(request, plan, intent, snapshot) == []
    report = analyze_control_flow(intent)
    assert report["scenario_count"] == scenarios and report["final_source_count"] == 2
    assert all(len(item["success_sources"]) + len(item["error_sources"]) == 1 for item in report["scenarios"])
    compiled = compile_xpert_candidate(request=request, plan=plan, blueprint=intent, snapshot=snapshot, target=None)
    rebuilt = compile_xpert_candidate(
        request=request, plan=plan, blueprint=decompile_candidate_to_graph_intent(compiled), snapshot=snapshot, target=None,
    )
    assert workflow_semantic_checksum(compiled) == workflow_semantic_checksum(rebuilt)


@pytest.mark.parametrize("phantom", [False, True])
def test_fabricating_a_single_terminal_does_not_bypass_graph_validation(phantom):
    request, snapshot = _context()
    entries = [("accepted", []), ("rejected", [] if phantom else ["accepted"])]
    if phantom:
        entries.append(("phantom_final", ["accepted", "rejected"]))
    plan, intent = _plan(entries), _branch_intent()
    intent.nodes[1].task_ids, intent.nodes[2].task_ids = ["accepted"], ["rejected"]
    assert validate_task_plan(plan, max_agents=2) == []
    issues = validate_blueprint_authorization(request, plan, intent, snapshot)
    assert ("Planned task phantom_final is not covered by any IR node." if phantom else
            "Task dependency accepted->rejected is not represented by the control graph.") in issues


@pytest.mark.asyncio
@pytest.mark.parametrize("repair_valid", [False, True])
async def test_task_repair_retains_shared_call_budget_and_no_automatic_draft(tmp_path, repair_valid):
    request, snapshot = _context()
    def preflight(candidate):
        return validate_xpert_definition(candidate), candidate.draft.workflow, []

    authoring = AuthoringService(
        AuthoringProposalStore(tmp_path / "proposals"), XpertStore(tmp_path / "xperts"),
        WorkspaceSkillDraftStore(tmp_path / "skills"), xpert_preflight=preflight,
    )
    bad, good = _plan([("accepted", []), ("rejected", [])]), _plan([("answer", [])])
    recipe = from_intent(_branch_intent(), [step("router", {
        "matched": [step("approved")], "unmatched": [step("rejected")],
    })])
    replies = [bad.model_dump_json(), (good if repair_valid else bad).model_dump_json(), json.dumps(recipe)]
    prompts = []

    async def complete(_model, _system, prompt, temperature, _max_tokens):
        prompts.append(json.loads(prompt))
        assert len(prompts) <= 3
        if len(prompts) == 2:
            assert temperature == 0
            assert prompts[-1]["task_graph_diagnostics"]["terminal_task_ids"] == ["accepted", "rejected"]
        return replies[len(prompts) - 1]

    service = MetaPlannerV2Service(authoring_service=authoring, preflight=preflight, completion=complete)
    if repair_valid:
        response = await service.generate(request, snapshot)
        proposal = authoring.proposal_store.require(response.proposal_id)
        assert len(prompts) == 3 and response.validation["valid"] is True
        assert proposal.status == "pending" and proposal.revision == 1
        assert proposal.payload["meta_planner_report"]["repair_protocol"] == "task_plan_v1"
    else:
        with pytest.raises(ValueError, match="Task-plan repair failed: Task plan must have exactly one terminal task; found 2"):
            await service.generate(request, snapshot)
        assert len(prompts) == 2 and authoring.proposal_store.list() == []
    assert authoring.xpert_store.list_xperts() == []
