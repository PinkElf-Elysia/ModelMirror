from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import asdict

import pytest
from fastapi.testclient import TestClient

import server.main as main_module
from server.data_tables.store import SQLiteAgentTableBackend
from server.meta_agent.headless_authoring import HeadlessAuthoringError
from server.meta_agent.meta_planner_v2 import MetaPlannerV2Service
from server.tests.meta_planner_legacy_replay import LegacyGraphReplayService
from server.meta_agent.node_adapters import get_planner_node_adapter
from server.tests.test_meta_planner_write_headless import fixture
from server.tests.test_meta_planner_controlled_writes import intent, request, _plan


def _forbid_records(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("候选诊断不得读取或修改业务记录")

    for name in (
        "query_records", "execute_controlled_write", "create_record_for_schema",
        "create_record", "update_records", "delete_records", "update_record",
        "delete_record",
    ):
        monkeypatch.setattr(SQLiteAgentTableBackend, name, forbidden)


async def _failed_generation(tmp_path, monkeypatch, *, operations=None):
    _forbid_records(monkeypatch)
    headless, authoring, _, snapshot = fixture(tmp_path, monkeypatch, "update")
    generation_request = request(snapshot, "update")
    generation_request.goal = "查询 table-orders 并受控更新，然后汇总真实结果。"
    malformed = intent("update")
    malformed.nodes[1].inputs = []
    responses = [
        _plan().model_dump_json(),
        malformed.model_dump_json(),
        json.dumps({"operations": operations or []}),
    ]
    calls = []

    async def completion(*args):
        calls.append(args)
        assert len(calls) <= 3, "不得增加修复次数"
        return responses[len(calls) - 1]

    planner = LegacyGraphReplayService(
        authoring_service=authoring,
        preflight=headless.planner_service.preflight,
        completion=completion,
    )
    result = await planner.generate(generation_request, snapshot)
    headless.planner_service = planner
    return headless, authoring, result, calls


@pytest.mark.asyncio
async def test_failed_write_fallback_headless_returns_diagnostic_not_500(
    tmp_path, monkeypatch,
):
    headless, authoring, result, calls = await _failed_generation(tmp_path, monkeypatch)
    assert len(calls) == 3
    assert result.repair_used and not result.validation["valid"]
    proposal_id = result.proposal_id
    before = asdict(authoring.proposal_store.require(proposal_id))
    monkeypatch.setattr(main_module, "get_headless_authoring_service", lambda: headless)

    response = TestClient(main_module.app, raise_server_exceptions=False).get(
        f"/api/meta-agent/authoring/proposals/{proposal_id}"
    )

    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail["mode"] == "recovery"
    assert detail["can_approve"] is False
    assert detail["recovery"]["executable"] is False
    assert "data_table_query:table-orders" in detail["diagnostics"][0]["message"]
    assert "workflow" not in detail and "candidate" not in detail
    assert asdict(authoring.proposal_store.require(proposal_id)) == before
    assert authoring.xpert_store.list_xperts() == []
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_failed_write_fallback_cannot_enter_patch_or_editor(
    tmp_path, monkeypatch,
):
    headless, authoring, result, calls = await _failed_generation(tmp_path, monkeypatch)
    before = deepcopy(authoring.proposal_store.require(result.proposal_id).payload)
    # Each entry point must reject the stored candidate before consuming a patch.
    for action in (headless.preview, headless.apply, headless.editor_diff):
        with pytest.raises(HeadlessAuthoringError) as error:
            action(result.proposal_id, None)
        assert error.value.code == "recovery_required"
    assert authoring.proposal_store.require(result.proposal_id).payload == before
    assert len(calls) == 3


@pytest.mark.parametrize(
    ("operation", "mutation", "expected", "missing", "duplicates", "unexpected"),
    [
        ("insert", "input_missing", ["values"], ["values"], [], []),
        ("insert", "literal_extra", [], [], [], [0]),
        ("update", "missing_records", ["records"], ["records"], [], []),
        ("update", "duplicate_records", ["records"], [], ["records"], []),
        ("update", "literal_extra", ["records"], [], [], [1]),
        ("update", "input_missing", ["records", "values"], ["values"], [], []),
        ("update", "predicate_missing", ["predicate_selected", "records"], ["predicate_selected"], [], []),
        ("delete", "missing_records", ["records"], ["records"], [], []),
        ("delete", "literal_extra", ["records"], [], [], [1]),
    ],
)
def test_write_input_diagnostic_identifies_exact_shape_without_values(
    operation, mutation, expected, missing, duplicates, unexpected,
):
    node = next(node for node in intent(operation).nodes if node.ref == "write")
    record_binding = intent("update").nodes[1].inputs[0]
    if mutation == "input_missing":
        node.config.update(value_source="input", values=None)
    elif mutation == "literal_extra":
        extra = record_binding.model_copy(deep=True, update={"port": "values"})
        node.inputs.append(extra)
    elif mutation == "missing_records":
        node.inputs.clear()
    elif mutation == "duplicate_records":
        node.inputs.append(node.inputs[0].model_copy(deep=True))
    elif mutation == "predicate_missing":
        node.config["filter"].update(value_source="input")
        node.config["filter"].pop("value")

    with pytest.raises(ValueError) as error:
        get_planner_node_adapter(node.kind).validate_intent_node(node)

    diagnostic = error.value.input_diagnostic
    assert diagnostic["code"] == "write_input_contract_mismatch"
    assert diagnostic["node_ref"] == "write"
    assert diagnostic["expected_ports"] == expected
    assert diagnostic["missing_ports"] == missing
    assert diagnostic["duplicate_ports"] == duplicates
    assert diagnostic["unexpected_input_indices"] == unexpected
    assert "values" not in diagnostic
    assert "config" not in diagnostic
    assert "source_ref" not in diagnostic
    assert "DEMO" not in json.dumps(diagnostic)


def test_write_input_diagnostic_does_not_echo_unknown_port_or_business_data():
    node = intent("insert").nodes[0]
    node.config["values"] = {"sku": "private-business-value"}
    node.inputs = [intent("update").nodes[1].inputs[0].model_copy(
        update={"port": "sk-not-a-real-secret-123456789"}
    )]
    with pytest.raises(ValueError) as error:
        get_planner_node_adapter(node.kind).validate_intent_node(node)
    payload = json.dumps(error.value.input_diagnostic)
    assert error.value.input_diagnostic["unexpected_input_indices"] == [0]
    for value in ("sk-not-a-real-secret", "private-business-value", "sku", "rows"):
        assert value not in payload
        assert value not in str(error.value)


@pytest.mark.asyncio
async def test_repair_receives_exact_ports_and_report_keeps_both_attempts(
    tmp_path, monkeypatch,
):
    _, authoring, result, calls = await _failed_generation(tmp_path, monkeypatch)
    repair = json.loads(calls[2][2])
    diagnostics = repair["repair_contract"]["input_contract_issues"]
    assert len(diagnostics) == 1
    assert diagnostics[0]["node_ref"] == "write"
    assert diagnostics[0]["missing_ports"] == ["records"]
    report = authoring.proposal_store.require(result.proposal_id).payload["meta_planner_report"]
    attempts = report["generation_attempts"]
    assert [attempt["stage"] for attempt in attempts] == ["capability_compile", "graph_patch_v1"]
    assert all(not attempt["valid"] for attempt in attempts)
    assert all(attempt["input_contract_issues"] == diagnostics for attempt in attempts)
    assert all(set(attempt) == {"stage", "valid", "issue_count", "input_contract_issues"} for attempt in attempts)
    assert "private-business-value" not in json.dumps(attempts)
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_explicit_record_binding_repair_succeeds_without_auto_connect(
    tmp_path, monkeypatch,
):
    _, authoring, result, calls = await _failed_generation(
        tmp_path, monkeypatch,
        operations=[{
            "op": "connect_data", "source_ref": "lookup", "source_port": "result",
            "target_ref": "write", "target_port": "records",
        }],
    )
    assert len(calls) == 3
    assert result.repair_used and result.validation["valid"], result.validation
    report = authoring.proposal_store.require(result.proposal_id).payload["meta_planner_report"]
    attempts = report["generation_attempts"]
    assert not attempts[0]["valid"]
    assert attempts[0]["input_contract_issues"][0]["missing_ports"] == ["records"]
    assert attempts[1] == {
        "stage": "graph_patch_v1", "valid": True, "issue_count": 0,
        "input_contract_issues": [],
    }
    assert authoring.proposal_store.require(result.proposal_id).status == "pending"
    assert authoring.xpert_store.list_xperts() == []
