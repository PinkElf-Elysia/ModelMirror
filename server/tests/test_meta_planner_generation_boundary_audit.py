"""Synthetic boundary probes, not a replay of the unavailable R12 response."""
from copy import deepcopy
import json

import httpx
from jsonschema import Draft202012Validator
import pytest

from server import main
from server.meta_agent import meta_planner_v2 as planner_module
from server.meta_agent.completion_contract import validate_planner_completion
from server.meta_agent.generation_diagnostics import GenerationDiagnostics
from server.meta_agent.managed_gateway import ManagedMetaAgentGateway
from server.meta_agent.meta_planner_v2 import MetaPlannerV2Service, _json_payload
from server.tests.meta_planner_recipe_fixtures import from_intent
from server.meta_agent.schemas import GraphIntentV3
from server.tests.test_meta_agent_managed_gateway import (
    MODEL_ID, _activate_policy, _qualified_router,
)
from server.tests.test_meta_planner_control_contract_alignment import _chain, _request
from server.tests.test_meta_planner_controlled_writes import _plan, snapshot
from server.tests.test_meta_planner_headless_authoring import _preflight
from server.tests.test_meta_planner_write_generation_failures import _forbid_records
from server.tests.test_meta_planner_write_headless import fixture
from server.workflow_native.node_contracts import WorkflowValueSchema


def _first_record_chain():
    graph = _chain("stop")
    record = WorkflowValueSchema(type="object", nullable=True)
    for query_ref, write_ref in (
        ("query_after_insert", "update_ticket"),
        ("query_after_update", "delete_ticket"),
    ):
        query = next(node for node in graph.nodes if node.ref == query_ref)
        query.config["return_mode"] = "first"
        query.outputs[0].value_schema = record.model_copy(deep=True)
        writer = next(node for node in graph.nodes if node.ref == write_ref)
        writer.inputs[0].value_schema = record.model_copy(deep=True)
    return graph


def _fields(graph):
    raw = graph.model_dump(mode="json") if isinstance(graph, GraphIntentV3) else graph
    return [{key: deepcopy(node[key]) for key in (
        "ref", "kind", "config", "inputs", "outputs", "resource_ref",
    )} for node in raw["nodes"]]


def _mutate(raw, defect):
    by_ref = {node["ref"]: node for node in raw["nodes"]}
    if defect.startswith("filter_"):
        shape, ref = defect.removeprefix("filter_").split(":")
        by_ref[ref]["config"]["filter"] = [] if shape == "array" else "synthetic"
    elif defect == "serialize_port":
        by_ref["encode_insert_ticket"]["outputs"][0]["port"] = "result"
    elif defect == "serialize_missing":
        by_ref["encode_insert_ticket"]["outputs"] = []
    elif defect == "record_string":
        by_ref["update_ticket"]["inputs"][0]["value_schema"] = (
            WorkflowValueSchema(type="string").model_dump(mode="json")
        )
    elif defect != "valid":
        raise AssertionError(defect)


async def _transport(route, tmp_path, monkeypatch, replies):
    sent, returned = [], []

    def handler(request):
        assert len(sent) < len(replies), "No unplanned model request is allowed"
        sent.append(json.loads(request.content))
        content = replies[len(sent) - 1]
        payload = {
            "model": MODEL_ID,
            "choices": [{"finish_reason": "stop", "message": {"content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        }
        returned.append(deepcopy(payload))
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    managed_run = None
    if route == "managed":
        router, connection_id = await _qualified_router(tmp_path / "router")
        monkeypatch.setenv("MODEL_CONTROL_META_AGENT_ENABLED", "true")
        _activate_policy(router, connection_id)
        gateway = ManagedMetaAgentGateway.for_router(router, client_factory=lambda: httpx.AsyncClient(
            transport=transport, trust_env=False,
        ))
        managed_run = gateway.start_run(parent_run_reference="synthetic-boundary-audit")

    async def complete(model_id, system_prompt, user_prompt, temperature, max_tokens):
        if managed_run is not None:
            return await managed_run.complete_json(
                logical_call_key=f"synthetic-{len(sent) + 1}", call_sequence=len(sent) + 1,
                model_id=model_id, system_prompt=system_prompt, user_prompt=user_prompt,
                temperature=temperature, max_tokens=max_tokens,
            )
        return await main.collect_chat_completion_text(
            model_id,
            [main.ChatMessage(role="system", content=system_prompt),
             main.ChatMessage(role="user", content=user_prompt)],
            temperature=temperature, max_tokens=max_tokens,
            gateway_url="https://offline.invalid/v1/chat/completions", gateway_key="unused",
            response_format={"type": "json_object"},
            reasoning={"effort": "none", "exclude": True},
            allow_json_reasoning_fallback=True,
            response_observer=lambda data: validate_planner_completion(data, max_tokens=max_tokens),
            client_kwargs_override={"transport": transport, "trust_env": False},
        )

    return complete, sent, returned, managed_run


DEFECTS = ["valid", "serialize_port", "serialize_missing", "record_string"] + [
    f"filter_{shape}:{ref}" for shape in ("array", "string") for ref in (
        "query_after_insert", "update_ticket", "query_after_update", "delete_ticket",
    )
]


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["legacy", "managed"])
@pytest.mark.parametrize("defect", DEFECTS)
async def test_same_request_constraint_response_and_validator_pairing(tmp_path, monkeypatch, route, defect):
    _forbid_records(monkeypatch)
    snap = snapshot()
    req = _request(snap)
    req.planner_model_id = MODEL_ID
    raw = _first_record_chain().model_dump(mode="json")
    _mutate(raw, defect)
    before = _fields(raw)
    content = json.dumps(raw, ensure_ascii=False)
    complete, sent, returned, run = await _transport(route, tmp_path, monkeypatch, [content])
    prompt = MetaPlannerV2Service._blueprint_prompt(req, _plan(), snap, None)
    collected = await complete(MODEL_ID, planner_module.BLUEPRINT_SYSTEM_PROMPT, prompt, 0, 8192)

    assert len(sent) == 1
    actual_prompt = json.loads(sent[0]["messages"][-1]["content"])
    assert actual_prompt["required_schema"] == json.loads(prompt)["required_schema"]
    assert sent[0]["response_format"] == {"type": "json_object"}
    assert _fields(json.loads(returned[0]["choices"][0]["message"]["content"])) == before
    assert _fields(_json_payload(collected)) == before

    observed = []
    original = planner_module.validate_blueprint_authorization

    def observe(request, plan, graph, snapshot, **kwargs):
        observed.append(_fields(graph))
        result = original(request, plan, graph, snapshot, **kwargs)
        assert _fields(graph) == observed[-1], "Authorization must not rewrite the input"
        return result

    monkeypatch.setattr(planner_module, "validate_blueprint_authorization", observe)
    service = MetaPlannerV2Service(authoring_service=None, preflight=_preflight, completion=complete)
    diagnostics = GenerationDiagnostics("capability_compile")
    result = service._compile_and_validate(
        request=req, plan=_plan(), raw_blueprint=collected, snapshot=snap, target=None,
        diagnostics=diagnostics,
    )
    assert observed == [before]
    assert _fields(raw) == before
    schema_errors = list(Draft202012Validator(actual_prompt["required_schema"]).iter_errors(raw))
    if defect == "valid":
        assert not schema_errors
        assert result[2]["valid"] is True, result[3]
        assert result[4] is not None
    else:
        assert result[3] and not result[2]["valid"]
        assert result[4] is None
        if defect == "record_string":
            # V3 rejection comes from the full resolver rather than the lossy V2 check.
            assert schema_errors
            stage = diagnostics.as_dict()
            index = next(i for i, node in enumerate(raw["nodes"]) if node["ref"] == "update_ticket")
            assert stage["failed_phase"] == "resolve"
            assert stage["category_counts"]["type_ports"] == 1
            assert stage["issues"][0]["code"] == "DATA_TYPE_MISMATCH"
            assert stage["issues"][0]["location"] == ["nodes", index, "inputs", 0]
            assert "compile" not in stage["checks_executed"]
            assert result[1] == {}
        else:
            assert schema_errors
    if run is not None:
        run.finish("passed" if defect == "valid" else "failed")


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["legacy", "managed"])
@pytest.mark.parametrize("mode", ["valid", "task_repair", "repair_spent", "recipe_repair", "schema_injection", "patch_downgrade"])
async def test_full_synthetic_generation_locates_failure_without_writes(tmp_path, monkeypatch, route, mode):
    _forbid_records(monkeypatch)
    headless, authoring, _, snap = fixture(tmp_path, monkeypatch)
    req = _request(snap)
    req.planner_model_id = MODEL_ID
    graph = from_intent(_first_record_chain())
    bad = deepcopy(graph)
    next(node for node in bad["nodes"] if node["ref"] == "update_ticket")["inputs"] = []
    plan = _plan().model_dump_json()
    invalid_plan = json.dumps({"type": "object", "properties": {"tasks": {"type": "array"}}})
    edge = {
        "source_ref": "query_after_insert", "source_port": "result",
        "target_ref": "update_ticket", "target_port": "records",
    }
    old_patch = json.dumps({"operations": [
        {"op": "connect_data", **edge},
    ]})
    recipe_edits = {"operations": [{"op": "update_node", "node_ref": "update_ticket",
        "inputs": deepcopy(next(node for node in graph["nodes"] if node["ref"] == "update_ticket")["inputs"])}]}
    schema_injection = deepcopy(recipe_edits)
    schema_injection["operations"][0]["inputs"][0]["value_schema"] = {"type": "object"}
    replies = {
        "valid": [plan, json.dumps(graph)],
        "task_repair": [invalid_plan, plan, json.dumps(graph)],
        "repair_spent": [invalid_plan, plan, json.dumps(bad)],
        "recipe_repair": [plan, json.dumps(bad), json.dumps(recipe_edits)],
        "schema_injection": [plan, json.dumps(bad), json.dumps(schema_injection)],
        "patch_downgrade": [plan, json.dumps(bad), old_patch],
    }[mode]
    complete, sent, _, managed_run = await _transport(route, tmp_path, monkeypatch, replies)
    service = MetaPlannerV2Service(
        authoring_service=authoring, preflight=headless.planner_service.preflight, completion=complete,
    )
    result = await service.generate(req, snap)
    assert len(sent) == (2 if mode == "valid" else 3)
    assert [item["max_tokens"] for item in sent] == (
        [4096, 8192] if mode == "valid" else
        [4096, 8192, 8192] if mode in {"recipe_repair", "schema_injection", "patch_downgrade"} else [4096, 4096, 8192]
    )
    expected_valid = mode not in {"repair_spent", "schema_injection", "patch_downgrade"}
    assert result.validation["valid"] is expected_valid, json.dumps(result.validation, ensure_ascii=False)
    proposal = authoring.proposal_store.require(result.proposal_id)
    assert proposal.status == "pending" and proposal.revision == 1
    assert proposal.applied_resource_id is None
    assert authoring.xpert_store.list_xperts() == []
    report = proposal.payload["meta_planner_report"]
    evidence = report["generation_evidence"]
    assert evidence["version"] == 1
    assert len(evidence["calls"]) == len(sent)
    for call in evidence["calls"]:
        assert call["request"]["route"] == route
        assert call["request"]["contract_matches_intended"] is True
        assert call["provider_to_collector"] == "same_structure"
        assert call["collector_to_validator"] == "same_structure"
        assert call["provider"]["body_checksum"] == call["collector"]["body_checksum"]
    expected_repair = "none" if mode == "valid" else "recipe_edits_v1" if mode in {"recipe_repair", "schema_injection", "patch_downgrade"} else "task_plan_v1"
    assert report["repair_protocol"] == expected_repair
    if mode == "repair_spent":
        stages = report["generation_diagnostics"]
        assert [item["stage"] for item in stages] == ["task_plan", "task_plan_v1", "capability_compile"]
        assert stages[-1]["failed_phase"] == "authorization"
        assert stages[-1]["issue_count"] >= 1
        assert "records" in json.dumps(report["validation"])
        assert "compile" not in stages[-1]["checks_executed"]
        assert stages[-1]["recompile_executed"] is False
    if mode in {"schema_injection", "patch_downgrade"}:
        last = report["generation_diagnostics"][-1]
        assert last["failed_phase"] == "patch_apply"
        assert last["recompile_executed"] is False
        assert last["checks_executed"] == ["patch_parse", "patch_apply"]
        assert next(item for item in last["phase_results"] if item["id"] == "compile")["status"] == "blocked"
    if managed_run is not None:
        managed_run.finish("passed" if expected_valid else "failed")


@pytest.mark.parametrize("raw_filter", [[], "synthetic", True, 7])
def test_intent_parser_never_repairs_invalid_filter_shapes(raw_filter):
    raw = _first_record_chain().model_dump(mode="json")
    raw["nodes"][1]["config"]["filter"] = raw_filter
    parsed = GraphIntentV3.model_validate(_json_payload(json.dumps(raw)))
    assert type(parsed.nodes[1].config["filter"]) is type(raw_filter)
    assert parsed.nodes[1].config["filter"] == raw_filter


def test_model_schema_wrapper_is_not_mistaken_for_a_task_plan():
    from pydantic import ValidationError
    from server.meta_agent.generation_contract import parse_generation_task_plan

    wrapped = {"type": "object", "properties": {"tasks": {"type": "array"}}}
    assert _json_payload(json.dumps(wrapped)) == wrapped
    with pytest.raises(ValidationError) as failed:
        parse_generation_task_plan(wrapped)
    errors = {(tuple(item["loc"]), item["type"]) for item in failed.value.errors()}
    assert (("tasks",), "missing") in errors
    assert (("type",), "extra_forbidden") in errors
    assert wrapped == {"type": "object", "properties": {"tasks": {"type": "array"}}}
