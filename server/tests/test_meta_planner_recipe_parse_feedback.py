"""Parse failures must be actionable without executing or repairing the graph."""
import json

import pytest

from server.meta_agent.generation_diagnostics import GenerationDiagnostics, schema_issue_projection
from server.meta_agent.meta_planner_v2 import MetaPlannerV2Service
from server.tests.meta_planner_recipe_fixtures import from_intent
from server.tests.test_meta_planner_controlled_writes import intent, request, snapshot, _plan


@pytest.mark.parametrize("failure", ["protocol_and_null", "null", "string", "missing_config", "native_handle", "resource_kind"])
def test_parse_issues_enter_repair_focus_before_blocked_path_checks(failure):
    snap = snapshot()
    req = request(snap, "update")
    raw = from_intent(intent("update"))
    if failure in {"protocol_and_null", "null"}:
        raw["nodes"][0]["inputs"] = None
        if failure == "protocol_and_null":
            del raw["generation_protocol_version"]
    elif failure == "string":
        raw["nodes"][0]["inputs"] = "private-input-value"
    elif failure == "missing_config":
        del raw["nodes"][0]["config"]
    elif failure == "native_handle":
        raw["nodes"][0]["sourceHandle"] = "private-handle-value"
    else:
        raw["resources"] = [{"kind": "data_table", "resource_id": "table-orders", "target_ref": "answer"}]
    observer = GenerationDiagnostics("capability_compile")
    planner = MetaPlannerV2Service(authoring_service=None, preflight=lambda *_a, **_kw: pytest.fail("no preflight"))
    text = json.dumps(raw)
    graph, _, validation, errors, _, _ = planner._compile_and_validate(
        request=req, plan=_plan(), raw_blueprint=text, snapshot=snap, target=None,
        diagnostics=observer, require_recipe=True,
    )
    assert not validation["valid"] and graph is None
    prompt = json.loads(planner._recipe_repair_prompt(
        req, _plan(), snap, None, recipe=None, invalid_blueprint=text, issues=errors,
        recipe_diagnostics=observer.as_dict(),
    ))
    focus = prompt["repair_focus"]
    assert focus["primary_layer"] == "protocol_or_schema"
    assert focus["issue_count"] == observer.issue_count > 0
    assert focus["path_proof_status"] == "blocked"
    assert not focus["branch_repair_required"]
    assert prompt["invalid_generation"] == text
    assert "private-input-value" not in json.dumps(focus)
    assert "private-handle-value" not in json.dumps(focus)
    if failure == "protocol_and_null":
        assert {item["code"] for item in focus["issues"]} == {"GENERATION_PROTOCOL_REQUIRED", "RECIPE_INPUT_MODE_INVALID"}
        item = next(item for item in focus["issues"] if item["code"] == "RECIPE_INPUT_MODE_INVALID")
        assert item["location"] == ["nodes", 0, "inputs"]
        assert item["schema_detail"] == {"expected": "array", "actual": "null", "input_mode": "explicit"}


def test_untrusted_validator_message_cannot_forge_input_mode_diagnostic():
    detail = schema_issue_projection({
        "type": "value_error", "loc": ("nodes", 0),
        "ctx": {"error": ValueError("只有 workflow_agent 可用 inputs=null 从模板派生文本输入。")},
    })
    assert detail["code"] == "SCHEMA_VALIDATION_FAILED"
    assert detail["schema_detail"] is None
