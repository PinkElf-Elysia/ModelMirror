"""Offline observation guards through the real collectors and generation service."""
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from server import main
from server.meta_agent import generation_evidence as evidence_module
from server.meta_agent.generation_evidence import GenerationEvidence, GenerationEvidenceCall
from server.meta_agent.meta_planner_v2 import MetaPlannerV2Service
from server.tests.meta_planner_recipe_fixtures import from_intent
from server.tests.test_meta_planner_generation_boundary_audit import (
    MODEL_ID, _first_record_chain, _mutate, _plan, _request, _transport,
)
from server.tests.test_meta_planner_write_generation_failures import _forbid_records
from server.tests.test_meta_planner_write_headless import fixture


def _difference_paths(left, right, path="request"):
    if left == right:
        return []
    if isinstance(left, str) and isinstance(right, str):
        try:
            parsed_left, parsed_right = json.loads(left), json.loads(right)
        except (TypeError, ValueError):
            return [path]
        return _difference_paths(parsed_left, parsed_right, path) or [path + ":json_key_order"]
    if isinstance(left, dict) and isinstance(right, dict) and left.keys() == right.keys():
        return [item for key in left for item in _difference_paths(left[key], right[key], path + "." + key)]
    if isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
        return [item for index, (a, b) in enumerate(zip(left, right)) for item in _difference_paths(a, b, path + f"[{index}]")]
    return [path]


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["legacy", "managed"])
@pytest.mark.parametrize("invalid", [False, True])
async def test_observation_off_or_faulted_cannot_change_generation(tmp_path, monkeypatch, route, invalid):
    _forbid_records(monkeypatch)
    headless, authoring, _, snap = fixture(tmp_path, monkeypatch)
    req = _request(snap)
    req.planner_model_id = MODEL_ID
    graph = from_intent(_first_record_chain())
    if invalid:
        next(node for node in graph["nodes"] if node["ref"] == "update_ticket")["inputs"] = []
    plan = _plan().model_dump_json()
    replies = [plan, json.dumps(graph)] if not invalid else ['{}', plan, json.dumps(graph)]
    reports, requests = [], []
    for mode in ("on", "off", "fault"):
        recorder = GenerationEvidence()
        with monkeypatch.context() as patch:
            if mode == "off":
                patch.setattr(GenerationEvidenceCall, "_observe", lambda *_args: None)
            elif mode == "fault":
                def fail(*_args):
                    raise PermissionError("PRIVATE_OBSERVER_PATH")
                patch.setattr(evidence_module, "_structure", fail)
            complete, sent, _, managed = await _transport(route, tmp_path / mode, patch, replies)
            service = MetaPlannerV2Service(
                authoring_service=authoring, preflight=headless.planner_service.preflight,
                completion=complete, generation_evidence=recorder,
            )
            result = await service.generate(req.model_copy(deep=True), snap)
            assert result.validation["valid"] is (not invalid)
            proposal = authoring.proposal_store.require(result.proposal_id)
            assert proposal.status == "pending" and proposal.applied_resource_id is None
            report = deepcopy(proposal.payload["meta_planner_report"])
            observed = report.pop("generation_evidence")
            if mode == "on":
                assert all(call["provider_to_collector"] == "same_structure" for call in observed["calls"])
            else:
                assert all(call["provider_to_collector"] == "unavailable" for call in observed["calls"])
            assert "PRIVATE_OBSERVER_PATH" not in json.dumps(observed)
            reports.append(report)
            requests.append(sent)
            if managed is not None:
                managed.finish("failed" if invalid else "passed")
    assert reports[0] == reports[1] == reports[2]
    differences = _difference_paths(requests[0], requests[1]) + _difference_paths(requests[1], requests[2])
    assert not differences, differences
    assert len(requests[0]) == (3 if invalid else 2)
    assert authoring.xpert_store.list_xperts() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["legacy", "managed"])
async def test_proposal_storage_failure_is_not_hidden_or_retried(tmp_path, monkeypatch, route):
    _forbid_records(monkeypatch)
    headless, authoring, _, snap = fixture(tmp_path, monkeypatch)
    req = _request(snap)
    req.planner_model_id = MODEL_ID
    complete, sent, _, managed = await _transport(route, tmp_path, monkeypatch, [
        _plan().model_dump_json(), json.dumps(from_intent(_first_record_chain())),
    ])
    failure = PermissionError("original_store_failure")
    create = Mock(side_effect=failure)
    monkeypatch.setattr(authoring.proposal_store, "create", create)
    recorder = GenerationEvidence()
    service = MetaPlannerV2Service(
        authoring_service=authoring, preflight=headless.planner_service.preflight,
        completion=complete, generation_evidence=recorder,
    )
    with pytest.raises(PermissionError) as rejected:
        await service.generate(req, snap)
    assert rejected.value is failure
    create.assert_called_once()
    assert len(sent) == 2
    assert all(call["provider_to_collector"] == "same_structure" for call in recorder.as_dict()["calls"])
    assert authoring.xpert_store.list_xperts() == []
    if managed is not None:
        managed.finish("failed")


@pytest.mark.parametrize("failure", ["invalid_plan", "length", "timeout"])
def test_failed_api_retains_safe_evidence_before_proposal(monkeypatch, failure):
    from server.tests.test_meta_planner_v2 import _request as api_request, _snapshot
    _forbid_records(monkeypatch)
    monkeypatch.setattr(main, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(main, "get_model_router_service", lambda: None)
    monkeypatch.setattr(main.ManagedMetaAgentGateway, "for_router", staticmethod(
        lambda _router: SimpleNamespace(routing_mode=lambda: "legacy")
    ))
    monkeypatch.setattr(main, "get_llm_gateway_config", lambda: ("https://offline.invalid", "unused"))
    monkeypatch.setattr(main, "build_meta_planner_capability_snapshot", _snapshot)
    registry = SimpleNamespace(
        create_run=AsyncMock(return_value=SimpleNamespace(run_id="run_evidence_test")),
        record_checkpoint=AsyncMock(), update_run=AsyncMock(),
    )
    monkeypatch.setattr(main, "run_registry", registry)
    create = Mock(side_effect=AssertionError("Proposal creation is forbidden"))
    monkeypatch.setattr(main, "authoring_service", SimpleNamespace(proposal_store=SimpleNamespace(create=create)))
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        assert len(sent) <= (2 if failure == "invalid_plan" else 1)
        if failure == "timeout":
            raise httpx.ReadTimeout("synthetic-timeout")
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "length" if failure == "length" else "stop", "message": {
                "content": '{"type":"object","properties":{"PRIVATE_FIELD":"PRIVATE_VALUE"}}',
                "reasoning": "PRIVATE_REASONING",
            }}], "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        })

    original_collect = main.collect_chat_completion_text

    async def collect(*args, **kwargs):
        return await original_collect(*args, **kwargs, gateway_url="https://offline.invalid", gateway_key="unused",
            client_kwargs_override={"transport": httpx.MockTransport(handler), "trust_env": False})

    monkeypatch.setattr(main, "collect_chat_completion_text", collect)
    response = TestClient(main.app, raise_server_exceptions=False).post(
        "/api/meta-agent/generate-xpert-candidate", json=api_request().model_dump(mode="json"),
    )
    assert response.status_code == {"invalid_plan": 422, "length": 502, "timeout": 500}[failure]
    evidence = response.json()["generation_evidence"]
    assert len(evidence["calls"]) == len(sent) == (2 if failure == "invalid_plan" else 1)
    assert all(call["request"]["contract_matches_intended"] for call in evidence["calls"])
    if failure == "invalid_plan":
        assert all(call["collector_to_validator"] == "same_structure" for call in evidence["calls"])
        assert all(call["provider"]["schema_valid"] is False for call in evidence["calls"])
    else:
        assert evidence["calls"][0]["collector_to_validator"] == "unavailable"
    assert "PRIVATE_" not in json.dumps(evidence)
    assert registry.update_run.call_args.kwargs["metadata"]["generation_evidence"] == evidence
    create.assert_not_called()


def test_summary_failure_is_passive_and_does_not_expose_exception(monkeypatch):
    recorder = GenerationEvidence()
    with recorder.capture("task_plan", "{}"):
        pass
    def fail(_self):
        raise PermissionError("PRIVATE_SUMMARY_PATH")
    monkeypatch.setattr(GenerationEvidenceCall, "summary", fail)
    summary = recorder.as_dict()
    assert summary["status"] == "unavailable"
    assert summary["calls"] == []
    assert "PRIVATE_" not in json.dumps(summary)
