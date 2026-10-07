from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from server import main
from server.meta_agent.completion_contract import (
    PlannerCompletionError,
    validate_planner_completion,
)
from server.meta_agent.managed_gateway import (
    ManagedMetaAgentGateway,
    ManagedMetaAgentRoutingError,
)
from server.tests.test_meta_agent_managed_gateway import (
    MODEL_ID,
    _activate_policy,
    _qualified_router,
)
from server.tests.test_meta_planner_v2 import _plan, _request, _snapshot


def _response(content, reason="stop", *, model="model/planner", tokens=8192):
    return {
        "model": model,
        "choices": [{"finish_reason": reason, "message": {"content": content}}],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": tokens,
            "total_tokens": tokens + 100,
            "completion_tokens_details": {"reasoning_tokens": min(tokens, 7000)},
        },
    }


@pytest.mark.parametrize("reason", ["length", None, "content_filter"])
@pytest.mark.parametrize("content", ['{"nodes": []}', '{"nodes": [', ""])
@pytest.mark.parametrize("failed_call", [1, 2, 3])
def test_legacy_incomplete_response_stops_before_compile_repair_or_proposal(monkeypatch, content, reason, failed_call):
    monkeypatch.setattr(main, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(main, "get_model_router_service", lambda: None)
    monkeypatch.setattr(main.ManagedMetaAgentGateway, "for_router", staticmethod(
        lambda _router: SimpleNamespace(routing_mode=lambda: "legacy")
    ))
    monkeypatch.setattr(main, "get_llm_gateway_config", lambda: ("https://offline.invalid", "unused"))
    monkeypatch.setattr(main, "build_meta_planner_capability_snapshot", _snapshot)
    registry = SimpleNamespace(
        create_run=AsyncMock(return_value=SimpleNamespace(run_id="run_completion_test")),
        record_checkpoint=AsyncMock(), update_run=AsyncMock(),
    )
    monkeypatch.setattr(main, "run_registry", registry)
    create = Mock(side_effect=AssertionError("Proposal creation is forbidden"))
    monkeypatch.setattr(main, "authoring_service", SimpleNamespace(
        proposal_store=SimpleNamespace(create=create),
    ))
    compiled = []
    original_compile = main.MetaPlannerV2Service._compile_and_validate

    def compile_spy(self, **kwargs):
        compiled.append(True)
        return original_compile(self, **kwargs)

    monkeypatch.setattr(main.MetaPlannerV2Service, "_compile_and_validate", compile_spy)
    original_collect = main.collect_chat_completion_text
    requests = []

    async def sender(_client, payload):
        requests.append(payload)
        if len(requests) > failed_call:
            raise RuntimeError("PREVIEW_APPROVED_BUDGET_EXHAUSTED")
        if len(requests) == failed_call:
            return httpx.Response(200, json=_response(content, reason, tokens=payload["max_tokens"]))
        return httpx.Response(200, json=(
            _response(_plan().model_dump_json()) if len(requests) == 1
            else _response('{}')
        ))

    async def collect(*args, **kwargs):
        return await original_collect(
            *args, **kwargs, gateway_url="https://offline.invalid", gateway_key="unused",
            response_sender=sender,
            client_kwargs_override={"transport": httpx.MockTransport(lambda _r: pytest.fail("Unexpected HTTP")), "trust_env": False},
        )

    monkeypatch.setattr(main, "collect_chat_completion_text", collect)
    monkeypatch.setattr(main, "optimize_context", AsyncMock(side_effect=(
        lambda messages, **_kwargs: SimpleNamespace(messages=messages)
    )))
    response = TestClient(main.app, raise_server_exceptions=False).post(
        "/api/meta-agent/generate-xpert-candidate", json=_request().model_dump(mode="json"),
    )
    assert response.status_code == 502, response.text
    body = response.json()
    assert body["code"] == (
        "meta_planner_output_truncated" if reason == "length" else "meta_planner_completion_incomplete"
    )
    assert "未自动修复或重试" in body["error"]
    assert "PREVIEW_APPROVED_BUDGET_EXHAUSTED" not in response.text
    assert [r["max_tokens"] for r in requests] == [4096, 8192, 8192][:failed_call]
    assert all(r["reasoning"] == {"effort": "none", "exclude": True} for r in requests)
    assert compiled == ([True] if failed_call == 3 else [])
    create.assert_not_called()
    receipt = body["completion_receipts"][-1]
    assert receipt["call_sequence"] == failed_call
    assert receipt["finish_reason"] == (reason or "missing")
    expected_limit = 4096 if failed_call == 1 else 8192
    assert receipt["requested_max_tokens"] == expected_limit
    assert receipt["completion_tokens"] == expected_limit
    assert receipt["reasoning_tokens"] == min(expected_limit, 7000)
    assert receipt["content_chars"] == len(content)
    assert registry.update_run.call_args.kwargs["metadata"]["completion_receipts"] == body["completion_receipts"]


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["length", None, "content_filter"])
@pytest.mark.parametrize("content", ['{"result": "ok"}', '{"result":', ""])
async def test_managed_incomplete_response_is_failed_with_usage_not_passed_or_uncertain(tmp_path, monkeypatch, content, reason):
    service, connection_id = await _qualified_router(tmp_path)
    monkeypatch.setenv("MODEL_CONTROL_META_AGENT_ENABLED", "true")
    _activate_policy(service, connection_id)
    posts = []

    def handler(request):
        posts.append(request)
        return httpx.Response(200, json=_response(content, reason, model=MODEL_ID))

    gateway = ManagedMetaAgentGateway.for_router(service, client_factory=lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(handler), trust_env=False,
    ))
    run = gateway.start_run(parent_run_reference="offline-completion")
    observed = []
    with pytest.raises(ManagedMetaAgentRoutingError) as failed:
        await run.complete_json(
            logical_call_key="blueprint", call_sequence=1, model_id=MODEL_ID,
            system_prompt="private synthetic prompt", user_prompt="private synthetic goal",
            temperature=0.2, max_tokens=8192,
            completion_observer=observed.append,
        )
    expected = "meta_planner_output_truncated" if reason == "length" else "meta_planner_completion_incomplete"
    assert failed.value.code == expected
    assert len(posts) == 1
    receipt = run.receipt_summary().calls[0]
    assert receipt.status == "failed"
    assert receipt.completion_tokens == 8192
    assert receipt.total_tokens == 8292
    stored = service.repository.list_workload_receipts("local")["calls"][0]
    assert stored["status"] == "failed"
    assert stored["error_code"] == expected
    assert stored["completion_tokens"] == 8192
    assert len(observed) == 1
    assert observed[0]["reasoning_tokens"] == 7000
    assert observed[0]["content_chars"] == len(content)
    database = service.repository.database_path.read_bytes()
    assert b"private synthetic" not in database
    if content:
        assert content.encode() not in database


@pytest.mark.parametrize("reason", ["error", "tool_calls", "", {}, [], True, "secret-finish-marker"])
def test_unknown_or_nonterminal_status_never_exposes_raw_provider_fields(reason):
    payload = _response("private-content-marker", reason)
    payload["choices"][0]["message"]["reasoning"] = "private-reasoning-marker"
    payload["choices"][0]["error"] = {"message": "private-error-marker"}
    observed = []
    with pytest.raises(PlannerCompletionError) as rejected:
        validate_planner_completion(payload, max_tokens=8192, observer=observed.append)
    assert len(observed) == 1
    serialized = json.dumps(rejected.value.diagnostics)
    assert "private-" not in serialized
    assert "secret-finish-marker" not in serialized
    assert rejected.value.code == "meta_planner_completion_incomplete"
    assert observed[0]["provider_error"] is True


@pytest.mark.parametrize("at_choice", [False, True])
def test_stop_with_provider_error_is_not_accepted(at_choice):
    payload = _response('{"ok": true}')
    target = payload["choices"][0] if at_choice else payload
    target["error"] = {"message": "private-provider-failure"}
    with pytest.raises(PlannerCompletionError):
        validate_planner_completion(payload, max_tokens=8192)


@pytest.mark.parametrize("value", [None, -1, True, 1.5, "99", {}, []])
def test_invalid_usage_remains_unknown_not_estimated(value):
    payload = _response('{"ok": true}')
    payload["usage"] = {"completion_tokens": value, "completion_tokens_details": {"reasoning_tokens": value}}
    receipt = validate_planner_completion(payload, max_tokens=8192)
    assert receipt["completion_tokens"] is None
    assert receipt["reasoning_tokens"] is None
    assert receipt["prompt_tokens"] is None


def test_completed_response_preserves_exact_safe_usage():
    receipt = validate_planner_completion(_response('{"ok": true}'), max_tokens=8192)
    assert receipt == {
        "finish_reason": "stop", "requested_max_tokens": 8192, "content_chars": 12,
        "provider_error": False, "prompt_tokens": 100, "completion_tokens": 8192,
        "total_tokens": 8292, "reasoning_tokens": 7000,
    }


@pytest.mark.asyncio
async def test_nonplanner_collector_keeps_existing_truncated_text_behavior(monkeypatch):
    monkeypatch.setattr(main, "optimize_context", AsyncMock(side_effect=(
        lambda messages, **_kwargs: SimpleNamespace(messages=messages)
    )))
    sender = AsyncMock(return_value=httpx.Response(200, json=_response('{"partial":', "length")))
    result = await main.collect_chat_completion_text(
        "model/planner", [main.ChatMessage(role="user", content="offline")],
        gateway_url="https://offline.invalid", gateway_key="unused", response_sender=sender,
        client_kwargs_override={"transport": httpx.MockTransport(lambda _r: pytest.fail("Unexpected HTTP")), "trust_env": False},
    )
    assert result == '{"partial":'
    sender.assert_awaited_once()
