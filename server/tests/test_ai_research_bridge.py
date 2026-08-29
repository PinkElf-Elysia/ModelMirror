from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.model_router.ai_research_bridge import router, stable_service


MODEL_ID = "provider/fixed-research-model"
TOKEN = "test-ai-research-service-token"


@dataclass
class FakeDispatch:
    run_id: str = "chatrun_test"
    target: object = None
    authorized: object = None


class FakeTransport:
    def __init__(self, handler) -> None:
        self.handler = handler

    def client_kwargs(self) -> dict[str, object]:
        return {
            "transport": httpx.MockTransport(self.handler),
            "trust_env": False,
            "follow_redirects": False,
        }

    def build_authorized_stream_request(
        self,
        client: httpx.AsyncClient,
        target: object,
        authorized: object,
        payload: dict[str, Any],
        *,
        headers: dict[str, str],
    ) -> httpx.Request:
        return client.build_request(
            "POST", "https://provider.example/v1/chat/completions", json=payload, headers=headers
        )

    async def send_authorized_stream(
        self, client: httpx.AsyncClient, request: httpx.Request
    ) -> httpx.Response:
        return await client.send(request, stream=True)


class FakeStable:
    def __init__(self, handler) -> None:
        self.transport = FakeTransport(handler)
        self.completed: list[dict[str, Any]] = []
        self.dispatched = 0
        self.capabilities: list[str] = []
        self.ready = True
        self.readiness_reason: str | None = None
        self.readiness_calls: list[tuple[str, str]] = []

    def readiness(self, model_id: str, capability: str):
        assert model_id == MODEL_ID
        self.readiness_calls.append((model_id, capability))
        return self.ready, self.readiness_reason

    async def begin(self, model_id: str, capability: str):
        assert model_id == MODEL_ID
        self.capabilities.append(capability)
        return SimpleNamespace(
            intercepted=True,
            dispatch=FakeDispatch(),
            error_code=None,
        )

    def mark_dispatched(self, dispatch: FakeDispatch) -> None:
        self.dispatched += 1

    def complete(self, dispatch: FakeDispatch, **fields: Any) -> None:
        self.completed.append(fields)

    @staticmethod
    def classify_http_failure(status_code: int) -> tuple[str, str, bool]:
        return "transient_failure", f"provider_chat_http_{status_code}", False


def app_for(stable: FakeStable) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[stable_service] = lambda: stable
    return app


def enable(monkeypatch) -> None:
    monkeypatch.setenv("AI_RESEARCH_S2S_ENABLED", "true")
    monkeypatch.setenv("AI_RESEARCH_S2S_TOKEN", TOKEN)
    monkeypatch.setenv("AI_RESEARCH_LITERATURE_MODEL_ID", MODEL_ID)


def headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def valid_payload(*, stream: bool = False) -> dict[str, Any]:
    return {
        "model": MODEL_ID,
        "messages": [
            {"role": "system", "content": "Write a literature synthesis."},
            {"role": "user", "content": "What makes agent evaluation reproducible?"},
        ],
        "temperature": 0.2,
        "max_tokens": 512,
        "stream": stream,
        **({"stream_options": {"include_usage": True}} if stream else {}),
    }


def tool_payload(*, stream: bool = False) -> dict[str, Any]:
    payload = valid_payload(stream=stream)
    payload["tools"] = [
        {
            "type": "function",
            "function": {
                "name": "search_openalex",
                "description": "Search public academic metadata.",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        }
    ]
    payload["tool_choice"] = "auto"
    payload["parallel_tool_calls"] = False
    return payload


def test_disabled_wrong_token_and_models_fail_closed(monkeypatch) -> None:
    stable = FakeStable(lambda request: httpx.Response(500))
    api = TestClient(app_for(stable))
    assert api.get("/api/ai-research/v1/models").status_code == 503

    enable(monkeypatch)
    assert api.get(
        "/api/ai-research/v1/models", headers={"Authorization": "Bearer wrong"}
    ).status_code == 401
    models = api.get("/api/ai-research/v1/models", headers=headers())
    assert models.status_code == 200
    assert [item["id"] for item in models.json()["data"]] == [MODEL_ID]
    assert stable.readiness_calls == [(MODEL_ID, "chat_tools")]

    stable.ready = False
    stable.readiness_reason = "provider_chat_hard_failure_recertification_required"
    not_ready = api.get("/api/ai-research/v1/models", headers=headers())
    assert not_ready.status_code == 503
    assert not_ready.json()["detail"] == (
        "provider_chat_hard_failure_recertification_required"
    )


def test_rejects_wrong_model_multimodal_and_unknown_fields(monkeypatch) -> None:
    enable(monkeypatch)
    stable = FakeStable(lambda request: httpx.Response(500))
    with TestClient(app_for(stable)) as api:
        wrong = valid_payload()
        wrong["model"] = "provider/other"
        assert api.post(
            "/api/ai-research/v1/chat/completions", json=wrong, headers=headers()
        ).status_code == 422
        for field, value in (
            ("response_format", {"type": "json_object"}),
            ("user", "caller-selected-user"),
        ):
            invalid = valid_payload()
            invalid[field] = value
            assert api.post(
                "/api/ai-research/v1/chat/completions",
                json=invalid,
                headers=headers(),
            ).status_code == 422
        multimodal = valid_payload()
        multimodal["messages"][1]["content"] = [
            {"type": "image_url", "image_url": {"url": "https://example.test/x"}}
        ]
        assert api.post(
            "/api/ai-research/v1/chat/completions",
            json=multimodal,
            headers=headers(),
        ).status_code == 422
    assert stable.dispatched == 0


def test_tool_request_uses_qualified_tools_route_and_preserves_contract(monkeypatch) -> None:
    enable(monkeypatch)

    def upstream(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["tools"][0]["function"]["name"] == "search_openalex"
        assert body["tool_choice"] == "auto"
        assert body["parallel_tool_calls"] is False
        return httpx.Response(
            200,
            json={
                "model": MODEL_ID,
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_openalex",
                                    "type": "function",
                                    "function": {
                                        "name": "search_openalex",
                                        "arguments": '{"query":"AgentBench"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            },
        )

    stable = FakeStable(upstream)
    with TestClient(app_for(stable)) as api:
        response = api.post(
            "/api/ai-research/v1/chat/completions",
            json=tool_payload(),
            headers=headers(),
        )
    assert response.status_code == 200
    assert stable.capabilities == ["chat_tools"]
    assert stable.completed[0]["status"] == "succeeded"


def test_tool_followup_requires_declared_matched_calls(monkeypatch) -> None:
    enable(monkeypatch)
    stable = FakeStable(lambda request: httpx.Response(500))
    valid = tool_payload()
    valid["messages"].extend(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_openalex",
                        "type": "function",
                        "function": {
                            "name": "search_openalex",
                            "arguments": '{"query":"AgentBench"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_openalex",
                "content": "One public result",
            },
        ]
    )
    invalid_cases = []
    undeclared = tool_payload()
    undeclared["messages"].append(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_bad",
                    "type": "function",
                    "function": {"name": "shell", "arguments": "{}"},
                }
            ],
        }
    )
    invalid_cases.append(undeclared)
    orphan = tool_payload()
    orphan["messages"].append(
        {"role": "tool", "tool_call_id": "call_missing", "content": "x"}
    )
    invalid_cases.append(orphan)
    bad_schema = tool_payload()
    bad_schema["tools"][0]["function"]["parameters"] = {"type": "string"}
    invalid_cases.append(bad_schema)

    def upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": MODEL_ID,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Synthesis"},
                    }
                ],
            },
        )

    stable.transport.handler = upstream
    with TestClient(app_for(stable)) as api:
        accepted = api.post(
            "/api/ai-research/v1/chat/completions", json=valid, headers=headers()
        )
        assert accepted.status_code == 200
        for invalid in invalid_cases:
            assert api.post(
                "/api/ai-research/v1/chat/completions",
                json=invalid,
                headers=headers(),
            ).status_code == 422
    assert stable.dispatched == 1


def test_streaming_tool_call_is_a_valid_terminal(monkeypatch) -> None:
    enable(monkeypatch)
    events = (
        f'data: {{"model":"{MODEL_ID}","choices":[{{"delta":{{"tool_calls":[{{"index":0,"id":"call_openalex","type":"function","function":{{"name":"search_openalex","arguments":"{{\\"query\\":\\"AgentBench\\"}}"}}}}]}},"finish_reason":null}}]}}\n\n'
        f'data: {{"model":"{MODEL_ID}","choices":[{{"delta":{{}},"finish_reason":"tool_calls"}}]}}\n\n'
        "data: [DONE]\n\n"
    )
    stable = FakeStable(
        lambda request: httpx.Response(
            200,
            content=events.encode("utf-8"),
            headers={"content-type": "text/event-stream"},
        )
    )
    with TestClient(app_for(stable)) as api:
        response = api.post(
            "/api/ai-research/v1/chat/completions",
            json=tool_payload(stream=True),
            headers=headers(),
        )
    assert response.status_code == 200
    assert response.text.endswith("data: [DONE]\n\n")
    assert stable.capabilities == ["chat_tools"]
    assert stable.completed[0]["status"] == "succeeded"


def test_completion_token_alias_is_bounded_normalized_and_exclusive(monkeypatch) -> None:
    enable(monkeypatch)

    def upstream(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["max_tokens"] == 30_000
        assert "max_completion_tokens" not in body
        return httpx.Response(
            200,
            json={
                "model": MODEL_ID,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Review"},
                    }
                ],
            },
        )

    stable = FakeStable(upstream)
    with TestClient(app_for(stable)) as api:
        alias_payload = valid_payload()
        alias_payload.pop("max_tokens")
        alias_payload["max_completion_tokens"] = 30_000
        assert api.post(
            "/api/ai-research/v1/chat/completions",
            json=alias_payload,
            headers=headers(),
        ).status_code == 200

        both_payload = valid_payload()
        both_payload["max_completion_tokens"] = 512
        assert api.post(
            "/api/ai-research/v1/chat/completions",
            json=both_payload,
            headers=headers(),
        ).status_code == 422

        oversized_payload = valid_payload()
        oversized_payload.pop("max_tokens")
        oversized_payload["max_completion_tokens"] = 32_769
        assert api.post(
            "/api/ai-research/v1/chat/completions",
            json=oversized_payload,
            headers=headers(),
        ).status_code == 422
    assert stable.dispatched == 1


def test_non_streaming_request_uses_fixed_control_and_records_usage(monkeypatch) -> None:
    enable(monkeypatch)

    def upstream(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == MODEL_ID
        assert set(body) <= {
            "model",
            "messages",
            "temperature",
            "max_tokens",
            "top_p",
            "stop",
            "stream",
            "stream_options",
        }
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "model": MODEL_ID,
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "Review"}}
                ],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 2,
                    "total_tokens": 13,
                },
            },
        )

    stable = FakeStable(upstream)
    with TestClient(app_for(stable)) as api:
        response = api.post(
            "/api/ai-research/v1/chat/completions",
            json=valid_payload(),
            headers=headers(),
        )
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "Review"
    assert response.headers["cache-control"] == "no-store"
    assert stable.dispatched == 1
    assert stable.completed[0]["status"] == "succeeded"
    assert stable.completed[0]["total_tokens"] == 13


def test_non_streaming_response_with_other_model_fails_closed(monkeypatch) -> None:
    enable(monkeypatch)
    stable = FakeStable(
        lambda request: httpx.Response(
            200,
            json={
                "model": "provider/unexpected-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "wrong route"},
                    }
                ],
            },
        )
    )
    with TestClient(app_for(stable)) as api:
        response = api.post(
            "/api/ai-research/v1/chat/completions",
            json=valid_payload(),
            headers=headers(),
        )
    assert response.status_code == 502
    assert "wrong route" not in response.text
    assert stable.completed[0]["status"] == "failed"
    assert stable.completed[0]["result_class"] == "hard_failure"
    assert stable.completed[0]["error_code"] == "ai_research_bridge_model_mismatch"


def test_non_streaming_invalid_responses_store_only_contract_category(monkeypatch) -> None:
    enable(monkeypatch)
    cases = (
        (b"not-json", "ai_research_bridge_invalid_json"),
        (
            json.dumps({"model": MODEL_ID, "choices": []}).encode("utf-8"),
            "ai_research_bridge_missing_choices",
        ),
        (
            json.dumps(
                {
                    "model": MODEL_ID,
                    "choices": [{"message": {"role": "assistant", "content": ""}}],
                }
            ).encode("utf-8"),
            "ai_research_bridge_empty_completion",
        ),
    )
    for content, expected_code in cases:
        stable = FakeStable(lambda request, value=content: httpx.Response(200, content=value))
        with TestClient(app_for(stable)) as api:
            response = api.post(
                "/api/ai-research/v1/chat/completions",
                json=valid_payload(),
                headers=headers(),
            )
        assert response.status_code == 502
        assert response.json()["detail"] == "fixed model returned an invalid response"
        assert stable.completed[0]["error_code"] == expected_code


def test_rejects_total_text_over_bridge_limit_before_dispatch(monkeypatch) -> None:
    enable(monkeypatch)
    stable = FakeStable(lambda request: httpx.Response(500))
    payload = valid_payload()
    payload["messages"] = [
        {"role": "user", "content": "x" * 32_768} for _ in range(4)
    ]
    with TestClient(app_for(stable)) as api:
        response = api.post(
            "/api/ai-research/v1/chat/completions",
            json=payload,
            headers=headers(),
        )
    assert response.status_code == 422
    assert stable.dispatched == 0


def test_accepts_single_ldr_synthesis_message_over_legacy_limit(monkeypatch) -> None:
    enable(monkeypatch)
    stable = FakeStable(
        lambda request: httpx.Response(
            200,
            json={
                "id": "chatcmpl-ldr-synthesis",
                "object": "chat.completion",
                "model": MODEL_ID,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Review"},
                    }
                ],
            },
        )
    )
    payload = valid_payload()
    payload["messages"] = [{"role": "user", "content": "x" * 32_769}]
    with TestClient(app_for(stable)) as api:
        response = api.post(
            "/api/ai-research/v1/chat/completions",
            json=payload,
            headers=headers(),
        )
    assert response.status_code == 200
    assert stable.dispatched == 1


def test_streaming_request_preserves_sse_and_records_terminal(monkeypatch) -> None:
    enable(monkeypatch)
    events = (
        f'data: {{"model":"{MODEL_ID}","choices":[{{"delta":{{"content":"Review"}},"finish_reason":null}}]}}\n\n'
        f'data: {{"model":"{MODEL_ID}","choices":[{{"delta":{{}},"finish_reason":"stop"}}],"usage":{{"prompt_tokens":11,"completion_tokens":2,"total_tokens":13}}}}\n\n'
        "data: [DONE]\n\n"
    )
    stable = FakeStable(
        lambda request: httpx.Response(
            200, content=events.encode("utf-8"), headers={"content-type": "text/event-stream"}
        )
    )
    with TestClient(app_for(stable)) as api:
        response = api.post(
            "/api/ai-research/v1/chat/completions",
            json=valid_payload(stream=True),
            headers=headers(),
        )
    assert response.status_code == 200
    assert response.text.endswith("data: [DONE]\n\n")
    assert response.headers["x-modelmirror-route-run-id"] == "chatrun_test"
    assert stable.completed[0]["status"] == "succeeded"
    assert stable.completed[0]["total_tokens"] == 13


def test_upstream_failure_is_sanitized_and_finalized(monkeypatch) -> None:
    enable(monkeypatch)
    stable = FakeStable(
        lambda request: httpx.Response(
            500, json={"error": "provider-secret-should-not-pass-through"}
        )
    )
    with TestClient(app_for(stable)) as api:
        response = api.post(
            "/api/ai-research/v1/chat/completions",
            json=valid_payload(),
            headers=headers(),
        )
    assert response.status_code == 503
    assert "provider-secret" not in response.text
    assert stable.completed[0]["status"] == "failed"


def test_unexpected_dispatch_failure_is_sanitized_and_finalized(monkeypatch) -> None:
    enable(monkeypatch)

    def fail_before_response(request: httpx.Request) -> httpx.Response:
        raise RuntimeError("provider-secret-in-unexpected-error")

    stable = FakeStable(fail_before_response)
    with TestClient(app_for(stable), raise_server_exceptions=False) as api:
        response = api.post(
            "/api/ai-research/v1/chat/completions",
            json=valid_payload(),
            headers=headers(),
        )
    assert response.status_code == 503
    assert "provider-secret" not in response.text
    assert stable.completed[0]["status"] == "failed"
    assert stable.completed[0]["error_code"] == "ai_research_bridge_dispatch_failed"
