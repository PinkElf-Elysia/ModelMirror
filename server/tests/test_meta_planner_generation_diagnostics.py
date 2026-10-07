from __future__ import annotations

import json

import httpx
import pytest

from server.meta_agent.meta_planner_v2 import MetaPlannerV2Service
from server.tests.meta_planner_legacy_replay import LegacyGraphReplayService
from server.tests.test_meta_planner_controlled_writes import _plan, intent, request
from server.tests.test_meta_planner_write_generation_failures import _forbid_records
from server.tests.test_meta_planner_write_headless import fixture


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    _forbid_records(monkeypatch)

    def forbidden(*args, **kwargs):
        pytest.fail("诊断测试禁止 Provider、HTTP 和业务记录访问")

    monkeypatch.setattr(httpx.Client, "send", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)


def record_edge():
    return {
        "op": "connect_data", "source_ref": "lookup", "source_port": "result",
        "target_ref": "write", "target_port": "records",
    }


async def generate(tmp_path, monkeypatch, operations, *, malformed=None, plan=None, legacy_replay=True):
    headless, authoring, _, snapshot = fixture(tmp_path, monkeypatch, "update")
    generation_request = request(snapshot, "update")
    generation_request.goal = "查询并受控更新合成记录，然后汇总真实结果。"
    if malformed is None:
        malformed = intent("update").model_dump(mode="json")
        malformed["nodes"][1]["inputs"] = []
    if plan is None:
        responses = [_plan().model_dump_json(), json.dumps(malformed), json.dumps({"operations": operations})]
    else:
        responses = [json.dumps(plan), _plan().model_dump_json(), json.dumps(malformed)]
    calls = []

    async def completion(*args):
        calls.append(args)
        assert len(calls) <= 3
        return responses[len(calls) - 1]

    service_class = LegacyGraphReplayService if legacy_replay else MetaPlannerV2Service
    service = service_class(
        authoring_service=authoring, preflight=headless.planner_service.preflight,
        completion=completion,
    )
    result = await service.generate(generation_request, snapshot)
    proposal = authoring.proposal_store.require(result.proposal_id)
    assert proposal.status == "pending" and proposal.revision == 1
    assert authoring.xpert_store.list_xperts() == []
    assert len(calls) == 3
    return result, proposal.payload["meta_planner_report"], authoring, calls


@pytest.mark.asyncio
async def test_duplicate_control_records_exact_normalized_position_without_recompile(tmp_path, monkeypatch):
    operations = [
        {"op": "set_xpert_metadata", "name": "PRIVATE_PROMPT_CANARY"},
        {"op": "connect_data", "source_ref": "write", "source_port": "result",
         "target_ref": "encode", "target_port": "value"},
        record_edge(),
        {"op": "connect_control", "source_ref": "lookup", "target_ref": "write"},
    ]
    result, report, _, calls = await generate(tmp_path, monkeypatch, operations)
    assert not result.validation["valid"]
    assert "Control edge already exists." in json.dumps(result.validation)
    diagnostics = report["generation_diagnostics"]
    first, repair = diagnostics[-2:]
    assert first["stage"] == "capability_compile" and first["issues"]
    assert any(item["category"] == "node_config" for item in first["issues"])
    assert repair["stage"] == "graph_patch_v1"
    assert repair["failed_phase"] == "patch_apply"
    assert repair["recompile_executed"] is False
    patch = repair["patch"]
    assert patch["before_operation_count"] == 4
    assert patch["after_operation_count"] == 3
    assert patch["failed_operation_index"] == 2
    assert patch["original_operation_indices"] == [3]
    assert patch["operation"] == {"op": "connect_control", "source_ref": "lookup", "target_ref": "write", "outcome_ref": "success"}
    assert repair["issues"][-1]["code"] == "PATCH_CONTROL_EDGE_EXISTS"
    for key in ("before_checksum", "after_checksum"):
        assert len(patch[key]) == 64
    assert "PRIVATE_PROMPT_CANARY" not in json.dumps(diagnostics)
    assert calls[-1][3] == 0


@pytest.mark.asyncio
async def test_success_and_post_validation_failure_do_not_blame_last_operation(tmp_path, monkeypatch):
    result, report, _, _ = await generate(tmp_path / "success", monkeypatch, [record_edge()])
    assert result.validation["valid"], result.validation
    repair = report["generation_diagnostics"][-1]
    assert repair["recompile_executed"] is True
    assert repair["issues"] == [] and repair["failed_phase"] is None
    assert repair["patch"]["failed_operation_index"] is None

    result, report, _, _ = await generate(tmp_path / "failure", monkeypatch, [
        {"op": "set_xpert_metadata", "name": "仅改名称仍应拒绝"},
    ])
    assert not result.validation["valid"]
    repair = report["generation_diagnostics"][-1]
    assert repair["failed_phase"] == "patch_apply"
    assert repair["patch"]["phase"] == "validation"
    assert repair["patch"]["failed_operation_index"] is None
    assert repair["recompile_executed"] is False


@pytest.mark.asyncio
async def test_unparseable_patch_has_no_fake_operation_index_and_redacts_fields(tmp_path, monkeypatch):
    result, report, _, _ = await generate(tmp_path, monkeypatch, [
        {**record_edge(), "PRIVATE_FIELD_CANARY": "C:/private/credential.txt"},
    ])
    assert not result.validation["valid"]
    repair = report["generation_diagnostics"][-1]
    assert repair["failed_phase"] == "patch_parse"
    assert repair["recompile_executed"] is False
    assert "patch" not in repair
    assert any(item["code"] == "SCHEMA_VALIDATION_FAILED" for item in repair["issues"])
    serialized = json.dumps(report["generation_diagnostics"])
    assert "PRIVATE_FIELD_CANARY" not in serialized
    assert "credential.txt" not in serialized
    assert "C:/private" not in serialized


@pytest.mark.asyncio
async def test_plan_repair_is_retained_without_spending_a_second_repair(tmp_path, monkeypatch):
    plan = _plan().model_dump(mode="json")
    plan["PRIVATE_FIELD_CANARY"] = "PRIVATE_PROMPT_CANARY"
    plan["tasks"] = []
    result, report, _, _ = await generate(tmp_path, monkeypatch, [], plan=plan)
    assert not result.validation["valid"]
    assert report["repair_protocol"] == "task_plan_v1"
    stages = report["generation_diagnostics"]
    assert [item["stage"] for item in stages] == ["task_plan", "task_plan_v1", "capability_compile"]
    assert stages[0]["issues"] and not stages[1]["issues"]
    assert "PRIVATE_" not in json.dumps(stages)


def test_stage_diagnostics_preserve_24_distinct_errors_and_bound_output():
    from server.meta_agent.generation_diagnostics import GenerationDiagnostics

    stage = GenerationDiagnostics("capability_compile")
    stage.enter("authorization")
    messages = [f"PRIVATE_BUSINESS_VALUE_{index}" for index in range(100)]
    stage.messages(messages[:24], category="type_ports", node_index=1)
    assert len(stage.as_dict()["issues"]) == 24
    assert len({item["fingerprint"] for item in stage.as_dict()["issues"]}) == 24
    stage.messages(messages[24:], category="control_flow")
    output = stage.as_dict()
    assert output["issue_count"] == 100 and output["omitted_issue_count"] == 36
    assert len(output["issues"]) == 64
    assert len(json.dumps(output)) < 64 * 1024
    assert "PRIVATE_BUSINESS_VALUE" not in json.dumps(output)
    assert output["issues"][0]["node_index"] == 1
    assert output["issues"][-1]["category"] == "control_flow"


def test_same_schema_error_on_two_nodes_keeps_both_locations():
    from pydantic import ValidationError
    from server.meta_agent.generation_diagnostics import GenerationDiagnostics
    from server.meta_agent.schemas import GraphIntentV3

    with pytest.raises(ValidationError) as error:
        GraphIntentV3.model_validate({"nodes": []})
    stage = GenerationDiagnostics("capability_compile")
    stage.enter("authorization")
    stage.exception(error.value, category="node_config", node_index=0)
    stage.exception(error.value, category="node_config", node_index=1)
    assert {item["node_index"] for item in stage.as_dict()["issues"]} == {0, 1}


def test_distinct_schema_reasons_keep_distinct_fingerprints_without_plaintext():
    from pydantic import BaseModel, ValidationError, field_validator
    from server.meta_agent.generation_diagnostics import GenerationDiagnostics

    class RejectedConfig(BaseModel):
        value: int

        @field_validator("value")
        @classmethod
        def reject(cls, value):
            raise ValueError(f"PRIVATE_REASON_{value}")

    stage = GenerationDiagnostics("capability_compile")
    stage.enter("authorization")
    for value in (1, 2, 1):
        with pytest.raises(ValidationError) as error:
            RejectedConfig(value=value)
        stage.exception(error.value, category="node_config", node_index=0)
    output = stage.as_dict()
    assert output["issue_count"] == 2
    assert len({item["fingerprint"] for item in output["issues"]}) == 2
    assert "PRIVATE_REASON_" not in json.dumps(output)


@pytest.mark.parametrize("message", [
    "PRIVATE_PROMPT_CANARY", "C:/private/credential.txt",
    "https://user:password@example.invalid/path", "Bearer PRIVATE_AUTH_CANARY",
])
def test_exception_summary_never_persists_message_or_context(message):
    from server.meta_agent.generation_diagnostics import GenerationDiagnostics

    stage = GenerationDiagnostics("graph_patch_v1")
    stage.enter("patch_apply")
    stage.exception(ValueError(message))
    output = stage.as_dict()
    assert message not in json.dumps(output)
    assert output["issues"][0]["code"] == "CONTRACT_CHECK_FAILED"
    assert len(output["issues"][0]["fingerprint"]) == 64


@pytest.mark.asyncio
async def test_diagnostics_survive_store_reopen_and_do_not_change_prompts(tmp_path, monkeypatch):
    result, report, authoring, calls = await generate(tmp_path, monkeypatch, [record_edge()])
    from server.xpert_runtime.authoring_store import AuthoringProposalStore

    reloaded = AuthoringProposalStore(storage_dir=authoring.proposal_store.storage_dir)
    assert reloaded.require(result.proposal_id).payload["meta_planner_report"]["generation_diagnostics"] == report["generation_diagnostics"]
    for call in calls:
        assert "generation_diagnostics" not in call[2]
    assert [item["stage"] for item in report["generation_attempts"]] == ["capability_compile", "graph_patch_v1"]


@pytest.mark.parametrize("operation", [
    record_edge(),
    {"op": "disconnect_data", "source_ref": "lookup", "source_port": "result", "target_ref": "write", "target_port": "records"},
    {"op": "connect_control", "source_ref": "lookup", "target_ref": "write"},
    {"op": "disconnect_control", "source_ref": "lookup", "target_ref": "write"},
    {"op": "remove_node", "ref": "write"},
    {"op": "set_xpert_metadata", "name": "受限元数据"},
    {"op": "update_node", "ref": "write", "config": {"PRIVATE_FIELD_CANARY": True}},
])
def test_patch_progress_is_observational_only(operation):
    from server.meta_agent.generation_diagnostics import GraphPatchProgress
    from server.meta_agent.graph_patch import GraphPatchEnvelopeV1, apply_graph_patch

    graph = intent("update")
    before = graph.model_dump(mode="json")
    patch = GraphPatchEnvelopeV1(
        proposal_revision=1, expected_graph_checksum="a" * 64,
        expected_candidate_checksum="b" * 64, operations=[operation],
    )
    outcomes = []
    for progress in (None, GraphPatchProgress()):
        try:
            result = apply_graph_patch(graph, patch, plan_task_ids={task.task_id for task in _plan().tasks}, progress=progress)
        except Exception as error:
            outcomes.append((type(error), str(error)))
        else:
            outcomes.append(result.intent.model_dump(mode="json"))
    assert outcomes[0] == outcomes[1]
    assert graph.model_dump(mode="json") == before


@pytest.mark.asyncio
async def test_24_input_errors_remain_locatable_after_patch_fails(tmp_path, monkeypatch):
    graph = intent("update").model_dump(mode="json")
    answer = graph["nodes"][-1]
    answer["inputs"].extend([
        {**answer["inputs"][0], "variable": f"private_missing_{index}"}
        for index in range(24)
    ])
    result, report, _, _ = await generate(tmp_path, monkeypatch, [
        {"op": "connect_control", "source_ref": "lookup", "target_ref": "write"},
    ], malformed=graph)
    assert not result.validation["valid"]
    first, repair = report["generation_diagnostics"][-2:]
    missing = [item for item in first["issues"] if item["code"] == "DATA_UNKNOWN_VARIABLE"]
    assert len(missing) == 24
    assert {item["node_ref"] for item in missing} == {"answer"}
    assert {item["location"][-1] for item in missing} == set(range(1, 25))
    assert first["omitted_issue_count"] == 0
    assert repair["issues"][-1]["code"] == "PATCH_CONTROL_EDGE_EXISTS"
    assert not repair["recompile_executed"]
    assert "private_missing_" not in json.dumps(report["generation_diagnostics"])


def test_unknown_control_refs_and_native_fields_do_not_leak_into_receipt():
    from server.meta_agent.generation_diagnostics import GenerationDiagnostics, GraphPatchProgress
    from server.meta_agent.graph_patch import GraphPatchEnvelopeV1

    graph = intent("update")
    patch = GraphPatchEnvelopeV1(
        proposal_revision=1, expected_graph_checksum="a" * 64,
        expected_candidate_checksum="b" * 64, operations=[{
            "op": "connect_control", "source_ref": "private_unknown_ref",
            "target_ref": "write",
        }],
    )
    stage = GenerationDiagnostics("graph_patch_v1")
    stage.enter("patch_apply")
    stage.patch_receipt(patch, patch, GraphPatchProgress("operations", 0), graph, failed=True)
    operation = stage.as_dict()["patch"]["operation"]
    assert "source_ref" not in operation
    assert operation["target_ref"] == "write"
    assert "private_unknown_ref" not in json.dumps(stage.as_dict())


def test_control_diagnostics_are_from_the_validator_not_message_guessing():
    from server.meta_agent.generation_diagnostics import GenerationDiagnostics
    from server.meta_agent.meta_planner_v2 import validate_blueprint_authorization
    from server.tests.test_meta_planner_controlled_writes import snapshot

    graph = intent("update")
    graph.control_edges.append(graph.control_edges[0].model_copy(deep=True))
    snap = snapshot()
    baseline = validate_blueprint_authorization(request(snap, "update"), _plan(), graph, snap)
    diagnostics = GenerationDiagnostics("capability_compile")
    diagnostics.enter("authorization")
    diagnostics.bind_graph(graph)
    actual = validate_blueprint_authorization(request(snap, "update"), _plan(), graph, snap, diagnostics=diagnostics)
    assert actual == baseline
    issue = next(item for item in diagnostics.as_dict()["issues"] if item["code"] == "CONTROL_DUPLICATE_EDGE")
    assert issue["location"] == ["control_edges", 3]
    assert issue["source_ref"] == "lookup" and issue["target_ref"] == "write"


@pytest.mark.asyncio
async def test_full_recipe_repair_records_pipeline_entry_even_if_parse_fails(tmp_path, monkeypatch):
    graph = intent("update").model_dump(mode="json")
    graph["ir_version"] = 999
    result, report, _, _ = await generate(tmp_path, monkeypatch, [], malformed=graph, legacy_replay=False)
    assert not result.validation["valid"]
    assert report["repair_protocol"] == "generation_recipe_v1"
    repair = report["generation_diagnostics"][-1]
    assert repair["recompile_executed"] is True
    assert repair["checks_executed"] == ["intent_parse"]
    assert repair["failed_phase"] == "intent_parse"


def test_query_input_diagnostics_use_configured_ports_and_do_not_store_dynamic_refs():
    from server.meta_agent.generation_diagnostics import GenerationDiagnostics, diagnostic_checksum

    graph = intent("update")
    query = graph.nodes[0]
    query.config["filter"] = {
        "ref": "private_business_ref", "field": "sku", "operator": "eq", "value_source": "input",
    }
    query.inputs = [graph.nodes[1].inputs[0].model_copy(update={
        "port": "predicate", "variable": "user_input", "source_ref": "input", "source_port": "user_input",
    })]
    diagnostics = GenerationDiagnostics("capability_compile")
    diagnostics.bind_graph(graph)
    output = diagnostics.as_dict()
    missing = next(item for item in output["input_contract_issues"] if item["code"] == "INPUT_PORT_COUNT_MISMATCH")
    assert missing == {
        "node_index": 0, "code": "INPUT_PORT_COUNT_MISMATCH", "port": "predicate",
        "port_checksum": diagnostic_checksum("predicate_private_business_ref"),
        "expected_count": 1, "actual_count": 0,
    }
    extra = next(item for item in output["input_contract_issues"] if item["code"] == "INPUT_PORT_UNEXPECTED")
    assert extra["node_index"] == 0 and extra["input_index"] == 0
    assert "private_business_ref" not in json.dumps(output)


def test_binding_diagnostics_distinguish_alias_source_port_and_declared_type():
    from server.meta_agent.generation_diagnostics import GenerationDiagnostics

    graph = intent("update")
    graph.nodes[-1].inputs[0].variable = "PRIVATE_VARIABLE_CANARY"
    diagnostics = GenerationDiagnostics("capability_compile")
    diagnostics.bind_graph(graph)
    output = diagnostics.as_dict()
    binding = next(item for item in output["data_bindings"] if item["node_index"] == 3)
    assert binding["source_node_index"] == 2
    assert binding["source_port"] == "json"
    assert binding["source_output_count"] == 1
    assert binding["variable_matches_source"] is False
    assert binding["declared_source_type"] == binding["declared_input_type"] == "string"
    assert "PRIVATE_VARIABLE_CANARY" not in json.dumps(output)
    before = output["data_graph_checksum"]
    graph.nodes[-1].inputs[0].source_port = "PRIVATE_PORT_CANARY"
    diagnostics.bind_graph(graph)
    output = diagnostics.as_dict()
    binding = next(item for item in output["data_bindings"] if item["node_index"] == 3)
    assert binding["source_output_count"] == 0
    assert binding["variable_matches_source"] is None
    assert "source_port" not in binding
    assert output["data_graph_checksum"] != before
    assert "PRIVATE_" not in json.dumps(output)


def test_invalid_table_config_does_not_report_a_fabricated_empty_input_contract():
    from server.meta_agent.generation_diagnostics import GenerationDiagnostics

    graph = intent("update")
    graph.nodes[1].config.pop("filter")
    diagnostics = GenerationDiagnostics("capability_compile")
    diagnostics.bind_graph(graph)
    issues = [item for item in diagnostics.as_dict()["input_contract_issues"] if item["node_index"] == 1]
    assert issues == [{"node_index": 1, "code": "INPUT_CONTRACT_CONFIG_INVALID"}]


def test_binding_diagnostics_are_bounded_and_observational():
    from server.meta_agent.generation_diagnostics import GenerationDiagnostics
    from server.meta_agent.meta_planner_v2 import validate_blueprint_authorization
    from server.tests.test_meta_planner_controlled_writes import snapshot

    graph = intent("update")
    for node in graph.nodes:
        source = graph.nodes[-1].inputs[0]
        node.inputs = [source.model_copy(update={"port": f"private_{i}", "variable": f"private_missing_{i}"}) for i in range(50)]
    before = graph.model_dump(mode="json")
    snap = snapshot()
    expected = validate_blueprint_authorization(request(snap, "update"), _plan(), graph, snap)
    diagnostics = GenerationDiagnostics("capability_compile")
    diagnostics.bind_graph(graph)
    diagnostics.enter("authorization")
    actual = validate_blueprint_authorization(request(snap, "update"), _plan(), graph, snap, diagnostics=diagnostics)
    output = diagnostics.as_dict()
    assert actual == expected and graph.model_dump(mode="json") == before
    assert output["data_binding_count"] == 200
    assert len(output["data_bindings"]) + len(output["input_contract_issues"]) <= 64
    assert output["omitted_binding_detail_count"] > 0
    assert len(json.dumps(output)) < 64 * 1024
    assert "private_missing_" not in json.dumps(output)
    assert "private_" not in json.dumps(output)
