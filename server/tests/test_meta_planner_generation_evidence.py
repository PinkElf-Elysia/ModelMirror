import asyncio
from copy import deepcopy
import json

import pytest

from server.meta_agent import generation_evidence as evidence_module
from server.meta_agent.generation_evidence import (
    GenerationEvidence, MAX_REPORT_CHARS, MAX_TEXT_CHARS,
    observe_generation_request, observe_generation_response,
)


def request_payload(schema=None):
    schema = schema or {"type": "object", "$defs": {"ModelNode_json_serialize": {}}, "required": ["nodes"]}
    return {"model": "fixture/planner", "messages": [{"role": "user", "content": json.dumps({"required_schema": schema, "goal": "PRIVATE_GOAL"})}]}


def graph():
    return {"ir_version": 3, "nodes": [{
        "ref": "PRIVATE_NODE_REF", "kind": "json_serialize", "config": {"format": "compact"},
        "inputs": [], "outputs": [{"port": "json", "variable": "PRIVATE_VARIABLE", "value_schema": {"type": "string"}}],
    }]}


def response(payload):
    return {"choices": [{"message": {"content": json.dumps(payload), "reasoning": "PRIVATE_REASONING"}}]}


def observe(recorder, payload, *, schema=None, provider=None, validator=None):
    sent = request_payload(schema)
    with recorder.capture("capability_compile", sent["messages"][0]["content"]) as call:
        observe_generation_request(sent, "legacy")
        observe_generation_response(response(provider if provider is not None else payload))
        call.text("collector", json.dumps(payload))
    call.validator(validator if validator is not None else payload)
    return call


def test_triplet_is_same_request_and_does_not_mutate_inputs():
    recorder = GenerationEvidence()
    payload = graph()
    before = deepcopy(payload)
    observe(recorder, payload)
    report = recorder.as_dict()
    item = report["calls"][0]
    assert item["request"]["contract_matches_intended"] is True
    assert item["provider_to_collector"] == "same_structure"
    assert item["collector_to_validator"] == "same_structure"
    assert item["provider"]["body_checksum"] == item["validator"]["body_checksum"]
    assert item["observation_counts"] == {"request": 1, "provider": 1, "collector": 1, "validator": 1}
    assert report["full_payload_replay"] is False
    assert payload == before
    assert "PRIVATE_" not in json.dumps(report)


@pytest.mark.parametrize("boundary", ["provider", "validator"])
@pytest.mark.parametrize("mutation", ["filter", "port", "type"])
def test_first_structural_divergence_is_located_without_claiming_a_root_cause(boundary, mutation):
    before = graph()
    after = deepcopy(before)
    if mutation == "filter":
        before["nodes"][0]["config"]["filter"] = {"ref": "PRIVATE_REF", "operator": "eq", "value": "PRIVATE_VALUE"}
        after["nodes"][0]["config"]["filter"] = []
    elif mutation == "port":
        after["nodes"][0]["outputs"][0]["port"] = "result"
    else:
        after["nodes"][0]["outputs"][0]["value_schema"]["type"] = "object"
    recorder = GenerationEvidence()
    observe(recorder, before, **{boundary: after})
    item = recorder.as_dict()["calls"][0]
    assert item["provider_to_collector"] == ("different_structure" if boundary == "provider" else "same_structure")
    assert item["collector_to_validator"] == ("different_structure" if boundary == "validator" else "same_structure")
    assert "PRIVATE_" not in json.dumps(item)


def test_changed_sent_contract_is_distinct_from_provider_noncompliance():
    recorder = GenerationEvidence()
    intended = request_payload()
    actual = request_payload({"type": "object", "required": ["tasks"]})
    with recorder.capture("capability_compile", intended["messages"][0]["content"]) as call:
        observe_generation_request(actual, "managed")
        observe_generation_response(response(graph()))
        call.text("collector", json.dumps(graph()))
        call.validator(graph())
    item = recorder.as_dict()["calls"][0]
    assert item["request"]["contract_matches_intended"] is False
    assert item["provider"]["schema_valid"] is False
    assert item["provider_to_collector"] == "same_structure"


def test_observer_failure_preserves_application_result_and_original_exception(monkeypatch):
    recorder = GenerationEvidence()
    def fail(*_args, **_kwargs):
        raise PermissionError("PRIVATE_PATH")
    monkeypatch.setattr(evidence_module, "_structure", fail)
    observe(recorder, graph())
    assert recorder.as_dict()["calls"][0]["provider_to_collector"] == "unavailable"
    assert "PRIVATE_PATH" not in json.dumps(recorder.as_dict())
    with pytest.raises(RuntimeError, match="original"):
        with recorder.capture("task_plan", "{}"):
            raise RuntimeError("original")
    count = len(recorder.calls)
    observe_generation_request(request_payload(), "legacy")
    assert len(recorder.calls) == count


def test_hidden_reasoning_cannot_supply_missing_provider_content():
    recorder = GenerationEvidence()
    sent = request_payload()
    with recorder.capture("capability_compile", sent["messages"][0]["content"]) as call:
        observe_generation_request(sent, "legacy")
        observe_generation_response({"choices": [{"message": {"reasoning": json.dumps(graph())}}]})
        call.text("collector", json.dumps(graph()))
    assert recorder.as_dict()["calls"][0]["provider_to_collector"] == "unavailable"


@pytest.mark.parametrize("literal", [
    {"message": "PRIVATE_VALUE", "operator": "eq"},
    [{"kind": "predicate", "value_source": "input"}],
    {"items": [{"logic": "and", "value": {"type": "string"}}]},
])
def test_business_json_literals_are_opaque_even_with_schema_keyword_collisions(literal):
    payload = graph()
    payload["nodes"][0]["config"]["filter"] = {"kind": "predicate", "value": literal}
    recorder = GenerationEvidence()
    observe(recorder, payload)
    summary = recorder.as_dict()["calls"][0]
    for boundary in ("provider", "collector", "validator"):
        fields = summary[boundary]["structure"]["nodes"][0]["filter"]["fields"]
        observed = next(item["shape"] for item in fields if item["key"] == {"value": "value"})
        assert observed == {"type": "array" if isinstance(literal, list) else "object"}


def test_omitted_fourth_call_cannot_reassign_validator_to_third_call():
    recorder = GenerationEvidence()
    for _ in range(3):
        observe(recorder, graph())
    before = recorder.as_dict()
    with recorder.capture("capability_compile", request_payload()["messages"][0]["content"]):
        pass
    recorder.last_call.validator({"nodes": []})
    after = recorder.as_dict()
    assert after["calls"] == before["calls"]
    assert after["omitted_calls"] == 1


@pytest.mark.parametrize("mutation", ["nodes", "filter_depth", "bindings", "root_fields"])
def test_truncation_never_claims_complete_pairing(mutation):
    payload = graph()
    if mutation == "nodes":
        payload["nodes"] *= 40
    elif mutation == "filter_depth":
        child = {"value": "PRIVATE_VALUE"}
        for _ in range(6):
            child = {"items": [child]}
        payload["nodes"][0]["config"]["filter"] = child
    elif mutation == "bindings":
        payload["nodes"][0]["outputs"] *= 100
    else:
        payload.update({f"PRIVATE_KEY_{index}": "PRIVATE_VALUE" for index in range(50)})
    recorder = GenerationEvidence()
    observe(recorder, payload)
    result = recorder.as_dict()
    assert result["calls"][0]["provider_to_collector"] == "incomplete"
    assert len(json.dumps(result, ensure_ascii=True)) <= MAX_REPORT_CHARS
    assert "PRIVATE_" not in json.dumps(result)


def test_oversized_content_and_nonlocal_schema_never_perform_io(monkeypatch):
    recorder = GenerationEvidence()
    payload = request_payload({"$ref": "https://private.invalid/schema"})
    def forbidden(*_args, **_kwargs):
        raise AssertionError("REMOTE_SCHEMA_RESOLUTION_FORBIDDEN")
    monkeypatch.setattr(evidence_module.Draft202012Validator, "is_valid", forbidden)
    with recorder.capture("capability_compile", payload["messages"][0]["content"]) as call:
        observe_generation_request(payload, "legacy")
        call.text("collector", "x" * (MAX_TEXT_CHARS + 1))
    item = recorder.as_dict()["calls"][0]
    assert item["request"]["status"] == "unavailable"
    assert item["collector"]["status"] == "unavailable"
    assert "private.invalid" not in json.dumps(item)


def test_duplicate_observations_are_ambiguous_and_calls_are_bounded():
    recorder = GenerationEvidence()
    for _ in range(5):
        call = observe(recorder, graph())
        call.provider(response(graph()))
    report = recorder.as_dict()
    assert len(report["calls"]) == 3 and report["omitted_calls"] == 2
    assert all(item["provider_to_collector"] == "ambiguous" for item in report["calls"])


def test_large_triplets_deduplicate_before_omitting_structural_evidence():
    recorder = GenerationEvidence()
    payload = graph()
    payload["nodes"] *= 13
    for _ in range(3):
        observe(recorder, deepcopy(payload))
    report = recorder.as_dict()
    assert len(json.dumps(report, ensure_ascii=True)) <= MAX_REPORT_CHARS
    for call in report["calls"]:
        assert call["provider_to_collector"] == "same_structure"
        for boundary in ("provider", "collector", "validator"):
            observed = call[boundary]
            shape = observed.get("structure") or report["structures"][observed["structure_ref"]]
            assert len(shape["nodes"]) == 13
    assert "PRIVATE_" not in json.dumps(report)
    assert report == recorder.as_dict()


def test_patch_operation_changes_are_visible_without_exposing_values():
    before = {"operations": [{"op": "update_node", "ref": "PRIVATE_REF", "title": "PRIVATE_TITLE"}]}
    after = {"operations": [{"op": "PRIVATE_INVALID_OP", "ref": "PRIVATE_REF", "input_sources": []}]}
    recorder = GenerationEvidence()
    observe(recorder, before, validator=after)
    call = recorder.as_dict()["calls"][0]
    assert call["collector_to_validator"] == "different_structure"
    operations = call["validator"]["structure"]["operations"]
    assert len(operations) == 1 and operations[0]["operation_index"] == 0
    assert operations[0]["op"]["type"] == "string"
    assert "PRIVATE_" not in json.dumps(call)


def test_patch_projection_has_a_hard_operation_limit_and_opaque_config_values():
    payload = {"operations": [{"op": "add_node", "ref": "PRIVATE_REF", "kind": "json_serialize",
        "title": "PRIVATE_TITLE", "config": {"values": {"filter": {"operator": "PRIVATE_VALUE"}}},
        "PRIVATE_EXTRA": "PRIVATE_SECRET"}] * 65}
    recorder = GenerationEvidence()
    observe(recorder, payload)
    report = recorder.as_dict()
    observed = report["calls"][0]["provider"]
    shape = observed.get("structure") or report["structures"][observed["structure_ref"]]
    assert shape["operation_count"] == 65
    assert len(shape["operations"]) == 64
    assert report["calls"][0]["provider_to_collector"] == "incomplete"
    assert len(json.dumps(report, ensure_ascii=True)) <= MAX_REPORT_CHARS
    assert "PRIVATE_" not in json.dumps(report)


def test_unknown_observation_keys_cannot_escape_or_exceed_report_limit():
    recorder = GenerationEvidence()
    call = observe(recorder, graph())
    before = recorder.as_dict()
    call.text("PRIVATE_KEY" * 10_000, "{}")
    after = recorder.as_dict()
    assert after == before
    assert len(json.dumps(after, ensure_ascii=True)) <= MAX_REPORT_CHARS


def test_patch_projection_does_not_publish_per_operation_content_hashes():
    recorder = GenerationEvidence()
    observe(recorder, {"operations": [{"op": "bind_prompt_profile", "profile_id": "PRIVATE_PROFILE"}]})
    entry = recorder.as_dict()["calls"][0]["provider"]["structure"]["operations"][0]
    assert "operation_checksum" not in entry
    assert "PRIVATE_PROFILE" not in json.dumps(entry)


def test_empty_or_failed_observation_does_not_claim_patch_execution(monkeypatch):
    recorder = GenerationEvidence()
    assert "patch_operations_observed" not in recorder.as_dict()
    call = observe(recorder, graph())
    def fail():
        raise RuntimeError("PRIVATE_ERROR")
    monkeypatch.setattr(call, "summary", fail)
    report = recorder.as_dict()
    assert report["status"] == "unavailable"
    assert "patch_operations_observed" not in report


def test_many_unique_large_shapes_remain_bounded_and_downgrade_only_omitted_pairs():
    recorder = GenerationEvidence()
    for call_index in range(3):
        variants = []
        for boundary_index in range(3):
            payload = graph()
            payload["nodes"] = [deepcopy(payload["nodes"][0]) for _ in range(32)]
            for node_index, node in enumerate(payload["nodes"]):
                node["outputs"][0]["variable"] = f"PRIVATE_{call_index}_{boundary_index}_{node_index}"
                node["inputs"] = deepcopy(node["outputs"])
            variants.append(payload)
        observe(recorder, variants[1], provider=variants[0], validator=variants[2])
    report = recorder.as_dict()
    assert len(json.dumps(report, ensure_ascii=True)) <= MAX_REPORT_CHARS
    assert any(shape.get("details_omitted") for shape in report["structures"].values())
    for call in report["calls"]:
        for first, second in (("provider", "collector"), ("collector", "validator")):
            shapes = [report["structures"][call[key]["structure_ref"]] for key in (first, second)]
            expected = "incomplete" if any(shape.get("details_omitted") for shape in shapes) else "different_structure"
            assert call[f"{first}_to_{second}"] == expected
    assert "PRIVATE_" not in json.dumps(report)


@pytest.mark.asyncio
async def test_concurrent_requests_do_not_share_active_evidence():
    entered = asyncio.Event()
    count = 0
    async def run(index):
        nonlocal count
        recorder = GenerationEvidence()
        payload = graph()
        payload["nodes"][0]["outputs"][0]["variable"] = f"PRIVATE_{index}"
        sent = request_payload()
        with recorder.capture("capability_compile", sent["messages"][0]["content"]) as call:
            count += 1
            if count == 2:
                entered.set()
            await entered.wait()
            observe_generation_request(sent, "legacy")
            observe_generation_response(response(payload))
            call.text("collector", json.dumps(payload))
        call.validator(payload)
        return recorder.as_dict()["calls"][0]
    left, right = await asyncio.gather(run(1), run(2))
    assert left["provider"]["body_checksum"] != right["provider"]["body_checksum"]
    assert left["provider_to_collector"] == right["provider_to_collector"] == "same_structure"
