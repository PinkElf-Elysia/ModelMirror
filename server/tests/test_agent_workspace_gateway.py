from __future__ import annotations

import json

import httpx
import pytest

from server.agent_workspace.gateway import (
    GatewayCapabilityError,
    OpenAICompatibleGateway,
)


@pytest.mark.asyncio
async def test_stream_reconstructs_native_tool_call_deltas() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        body = "\n".join(
            [
                'data: {"choices":[{"delta":{"reasoning_content":"inspect"},"finish_reason":null}]}',
                'data: {"choices":[{"delta":{"content":"I will read it."},"finish_reason":null}]}',
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"read_file","arguments":"{\\"file_"}}]},"finish_reason":null}]}',
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"path\\":\\"README.md\\"}"}}]},"finish_reason":"tool_calls"}]}',
                "data: [DONE]",
                "",
            ]
        )
        return httpx.Response(200, text=body, headers={"Content-Type": "text/event-stream"})

    events: list[tuple[str, dict[str, object]]] = []
    gateway = OpenAICompatibleGateway(
        gateway_url="https://gateway.test/v1/chat/completions",
        gateway_key="test-key",
        transport=httpx.MockTransport(handler),
    )
    turn = await gateway.stream_turn(
        model_id="test/model",
        messages=[{"role": "user", "content": "read it"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "read",
                    "parameters": {"type": "object"},
                },
            }
        ],
        max_tokens=1000,
        thinking_level="medium",
        timeout_ms=30_000,
        on_delta=lambda kind, payload: events.append((kind, payload)),
    )

    assert turn.content == "I will read it."
    assert turn.finish_reason == "tool_calls"
    assert turn.tool_calls[0].call_id == "call_1"
    assert turn.tool_calls[0].name == "read_file"
    assert json.loads(turn.tool_calls[0].arguments) == {"file_path": "README.md"}
    assert [event[0] for event in events] == [
        "thinking_delta",
        "text_delta",
        "tool_call_delta",
        "tool_call_delta",
    ]
    assert captured["payload"]["parallel_tool_calls"] is False  # type: ignore[index]
    assert captured["payload"]["tool_choice"] == "auto"  # type: ignore[index]


@pytest.mark.asyncio
async def test_explicit_unsupported_tools_error_is_a_capability_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"message": "This model does not support tool calls"}},
        )

    gateway = OpenAICompatibleGateway(
        gateway_url="https://gateway.test/v1/chat/completions",
        gateway_key="test-key",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(GatewayCapabilityError, match="不支持原生 Tool Calling"):
        await gateway.stream_turn(
            model_id="text-only",
            messages=[{"role": "user", "content": "hello"}],
            tools=[],
            max_tokens=100,
            thinking_level="low",
            timeout_ms=30_000,
            on_delta=lambda *_: None,
        )
