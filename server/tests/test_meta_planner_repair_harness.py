from __future__ import annotations

from copy import deepcopy
import json

from jsonschema import Draft202012Validator
import pytest

from server.meta_agent.graph_patch import UpdateNodeOperation
from server.meta_agent.meta_planner_v2 import (
    MetaPlannerV2Service, _normalize_adapter_outputs_for_repair,
)
from server.meta_agent.node_adapters import PlannerWriteInputContractError
from server.tests.test_meta_planner_controlled_writes import _plan, compile_case, request, snapshot
from server.tests.test_meta_planner_patch_change_evidence import (
    _apply, _graph, _literal_config, _operations, _patch, offline_only,
)
from server.tests.test_meta_planner_table_prompt_contract import _contracts


def _prompt():
    snap, graph = snapshot(), _graph()
    return MetaPlannerV2Service._patch_repair_prompt(
        request(snap, "update"), _plan(), snap, graph,
        ["TABLE_PREDICATE_INPUT_TYPE_MISMATCH：完整记录不能作为标量。"],
    )


def test_update_config_semantics_come_from_the_operation_schema():
    payload = json.loads(_prompt())
    semantics = UpdateNodeOperation.model_json_schema()["properties"]["config"]["x-authoring-update"]
    assert semantics == {"mode": "replace", "when_absent": "unchanged", "when_null": "unchanged"}
    assert payload["patch_command_contract"]["operation_fields"]["update_node"]["config_update"] == semantics
    assert "完整替换" in UpdateNodeOperation.model_fields["config"].description


def test_repair_diagnostics_precede_reference_material_without_dropping_authority():
    raw = _prompt()
    payload = json.loads(raw)
    keys = list(payload)
    assert keys.index("validation_issues") < keys.index("capability_snapshot")
    assert keys.index("repair_contract") < keys.index("capability_snapshot")
    sequence = payload["repair_contract"]["config_edit_sequence"]
    assert len(sequence) == 3
    assert "完整" in sequence[0] and "config" in sequence[0]
    assert "最终 config" in sequence[1] and "输入" in sequence[1]
    assert "整批" in sequence[2]
    assert json.loads(raw)["base_graph_intent"] == _graph().model_dump(mode="json")
    assert payload["input_role_contract"]["response_root_fields"] == ["operations"]
    assert payload["authorized_scope"]["data_table_write_grants"][0]["max_affected_rows"] == 1
    assert raw == json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


@pytest.mark.parametrize("stage", ["generate", "full_repair", "patch_repair"])
def test_identity_and_business_filter_guidance_is_shared_not_a_repair_only_exception(stage):
    contracts = _contracts(stage)
    for kind in ("data_table_update", "data_table_delete"):
        rules = " ".join(contracts[kind]["input_binding_contract"]["rules"])
        assert "record_id/revision" in rules and "收据" in rules
        assert "业务条件" in rules and "records 始终必填" in rules
        assert "非空 filter" in rules
    assert "收据" not in " ".join(contracts["data_table_query"]["input_binding_contract"]["rules"])


def test_whole_config_and_inputs_must_be_repaired_together():
    graph = _graph()
    initial = deepcopy(graph.model_dump(mode="json"))
    payload = json.loads(_prompt())
    operations = _operations(_literal_config())
    Draft202012Validator(payload["required_schema"]).validate({"operations": operations})
    result = _apply(graph, _patch(operations), None).intent
    snap = snapshot()
    result, _ = _normalize_adapter_outputs_for_repair(result, snap)
    compile_case(result, snap, request(snap, "update"))
    assert graph.model_dump(mode="json") == initial
    for incomplete in (operations[:1], operations[1:]):
        with pytest.raises(PlannerWriteInputContractError):
            _apply(graph, _patch(incomplete), None)


@pytest.mark.parametrize("bad_config", [
    {"filter": None}, {}, {"filter": {}},
    {**_literal_config(), "recordsVariable": "forged"},
    {**_literal_config(), "writeGrant": {"max_affected_rows": 100}},
])
def test_clarified_harness_does_not_make_invalid_config_valid(bad_config):
    graph = _graph()
    with pytest.raises(ValueError):
        _apply(graph, _patch(_operations(bad_config)), None)


def test_partial_config_still_means_replace_not_an_implicit_merge():
    with pytest.raises(PlannerWriteInputContractError) as caught:
        _apply(_graph(), _patch(_operations({"filter": _literal_config()["filter"]})), None)
    assert caught.value.input_diagnostic["missing_ports"] == ["values"]
