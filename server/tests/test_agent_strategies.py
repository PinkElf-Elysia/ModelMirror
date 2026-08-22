from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from server.xpert_runtime import RuntimeTool, RuntimeToolError, RuntimeToolResult
from server.xpert_runtime.interrupts import RuntimeMiddlewareFatalError
from server.xpert_runtime.agent_strategy import (
    AgentModelError,
    AgentModelTurn,
    AgentStrategyError,
    AgentStrategyRunner,
    AgentToolCall,
    AgentUsage,
    OpenAICompatibleAgentModelClient,
    build_tool_bindings,
    parse_chat_completion,
    parse_react_decision,
    summarize_arguments,
    truncate_observation,
)


class FakeModelClient:
    def __init__(self, responses: list[AgentModelTurn | Exception]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    async def complete(self, **kwargs: Any) -> AgentModelTurn:
        self.requests.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def runtime_tool(name: str = "fetch") -> RuntimeTool:
    return RuntimeTool(
        name=name,
        description="Fetch a document",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    )


def tool_turn(
    *calls: tuple[str, str, str],
    usage: AgentUsage | None = None,
) -> AgentModelTurn:
    return AgentModelTurn(
        tool_calls=[
            AgentToolCall(call_id=call_id, name=name, raw_arguments=arguments)
            for call_id, name, arguments in calls
        ],
        finish_reason="tool_calls",
        usage=usage or AgentUsage(),
    )


def answer_turn(answer: str, usage: AgentUsage | None = None) -> AgentModelTurn:
    return AgentModelTurn(
        content=answer,
        finish_reason="stop",
        usage=usage or AgentUsage(),
    )


def make_runner(
    model_client: FakeModelClient,
    tool_executor,
    **kwargs: Any,
) -> AgentStrategyRunner:
    return AgentStrategyRunner(
        model_client=model_client,
        tool_executor=tool_executor,
        tools=kwargs.pop("tools", [runtime_tool()]),
        model_id="test-model",
        system_prompt="You are a test agent.",
        user_prompt="Find the answer.",
        max_iterations=kwargs.pop("max_iterations", 3),
        **kwargs,
    )


def test_parse_chat_completion_preserves_tool_calls_and_usage() -> None:
    turn = parse_chat_completion(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "fetch",
                                    "arguments": '{"query":"docs"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 4,
                "total_tokens": 14,
            },
        }
    )

    assert turn.tool_calls[0].call_id == "call_1"
    assert turn.tool_calls[0].raw_arguments == '{"query":"docs"}'
    assert turn.usage.total_tokens == 14


@pytest.mark.asyncio
async def test_openai_adapter_sends_standard_tool_call_fields() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json={
                "model": "provider/actual-model",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "done"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5},
            },
        )

    client = OpenAICompatibleAgentModelClient(
        endpoint="https://gateway.test/v1/chat/completions",
        headers={"Authorization": "Bearer test"},
        client_kwargs={"transport": httpx.MockTransport(handler)},
    )
    tools = [
        {
            "type": "function",
            "function": {
                "name": "fetch",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    turn = await client.complete(
        model_id="test-model",
        messages=[{"role": "user", "content": "hello"}],
        temperature=0.2,
        max_tokens=100,
        tools=tools,
        tool_choice="auto",
        parallel_tool_calls=True,
    )

    assert captured["tools"] == tools
    assert turn.raw["model"] == "provider/actual-model"
    assert captured["tool_choice"] == "auto"
    assert captured["parallel_tool_calls"] is True
    assert captured["stream"] is False
    assert turn.content == "done"
    assert turn.usage.total_tokens == 5


def test_tool_binding_aliases_invalid_function_name_and_rejects_bad_schema() -> None:
    binding = build_tool_bindings([runtime_tool("server/fetch.page")])[0]
    assert binding.alias.startswith("tool_1_")
    assert binding.tool.name == "server/fetch.page"

    with pytest.raises(AgentStrategyError) as exc_info:
        build_tool_bindings(
            [
                RuntimeTool(
                    name="bad",
                    input_schema={"type": "array", "items": {"type": "string"}},
                )
            ]
        )
    assert exc_info.value.code == "invalid_tool_schema"


@pytest.mark.asyncio
async def test_function_calling_round_trip_preserves_tool_call_id_and_usage() -> None:
    model = FakeModelClient(
        [
            tool_turn(
                ("call_1", "fetch", '{"query":"agent"}'),
                usage=AgentUsage(prompt_tokens=10, completion_tokens=3, total_tokens=13),
            ),
            answer_turn(
                "final answer",
                usage=AgentUsage(prompt_tokens=15, completion_tokens=2, total_tokens=17),
            ),
        ]
    )
    calls: list[tuple[str, dict[str, Any], str, int]] = []

    async def execute(name: str, arguments: dict[str, Any], call_id: str, iteration: int):
        calls.append((name, arguments, call_id, iteration))
        return RuntimeToolResult(output="tool output", metadata={"content_types": ["text"]})

    result = await make_runner(model, execute, strategy="function_calling").run()

    assert result.answer == "final answer"
    assert result.usage.total_tokens == 30
    assert result.events[-1].metadata["usage"]["total_tokens"] == 30
    assert result.events[1].metadata["usage"]["total_tokens"] == 13
    assert calls == [("fetch", {"query": "agent"}, "call_1", 1)]
    second_messages = model.requests[1]["messages"]
    assert second_messages[-2]["tool_calls"][0]["id"] == "call_1"
    assert second_messages[-1] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "name": "fetch",
        "content": "tool output",
    }


@pytest.mark.asyncio
async def test_argument_validation_is_recoverable_without_invoking_tool() -> None:
    model = FakeModelClient(
        [
            tool_turn(("call_bad", "fetch", "{}")),
            tool_turn(("call_good", "fetch", '{"query":"fixed"}')),
            answer_turn("fixed answer"),
        ]
    )
    calls: list[dict[str, Any]] = []

    async def execute(name: str, arguments: dict[str, Any], call_id: str, iteration: int):
        calls.append(arguments)
        return RuntimeToolResult(output="ok")

    result = await make_runner(model, execute, strategy="function_calling").run()

    assert result.answer == "fixed answer"
    assert calls == [{"query": "fixed"}]
    assert any(event.status == "rejected" for event in result.events)


@pytest.mark.asyncio
async def test_tool_execution_error_is_a_recoverable_observation() -> None:
    model = FakeModelClient(
        [
            tool_turn(("call_1", "fetch", '{"query":"fail"}')),
            answer_turn("recovered answer"),
        ]
    )

    async def execute(name: str, arguments: dict[str, Any], call_id: str, iteration: int):
        raise RuntimeToolError(name, "temporary failure", code="tool_execution_error")

    result = await make_runner(model, execute, strategy="function_calling").run()

    assert result.answer == "recovered answer"
    assert result.tool_calls_attempted == 1
    assert result.tool_calls_executed == 0
    assert "temporary failure" in model.requests[1]["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_auto_falls_back_to_react_only_for_unsupported_tools() -> None:
    model = FakeModelClient(
        [
            AgentModelError(
                "unknown parameter: tools",
                status_code=400,
                param="tools",
            ),
            answer_turn("FinalAnswer: react answer"),
        ]
    )

    async def execute(*args: Any):
        raise AssertionError("tool should not be called")

    result = await make_runner(model, execute, strategy="auto").run()

    assert result.strategy == "react"
    assert result.answer == "react answer"
    assert any(event.event_type == "strategy_fallback" for event in result.events)
    assert "tools" in model.requests[0]
    assert "tools" not in model.requests[1]


@pytest.mark.asyncio
async def test_auto_does_not_fallback_for_auth_error() -> None:
    model = FakeModelClient(
        [AgentModelError("unauthorized", status_code=401, code="unauthorized")]
    )

    async def execute(*args: Any):
        raise AssertionError("tool should not be called")

    with pytest.raises(AgentStrategyError) as exc_info:
        await make_runner(model, execute, strategy="auto").run()

    assert exc_info.value.code == "model_error"
    assert len(model.requests) == 1


@pytest.mark.asyncio
async def test_react_hides_thought_and_invokes_tool() -> None:
    model = FakeModelClient(
        [
            answer_turn(
                '<think>private</think>Thought: use tool\nAction: '
                '{"action":"fetch","action_input":{"query":"docs"}}'
            ),
            answer_turn("FinalAnswer: complete"),
        ]
    )
    calls: list[str] = []

    async def execute(name: str, arguments: dict[str, Any], call_id: str, iteration: int):
        calls.append(name)
        return RuntimeToolResult(output="observation")

    result = await make_runner(model, execute, strategy="react").run()

    assert result.answer == "complete"
    assert calls == ["fetch"]
    assert all("private" not in event.message for event in result.events)


@pytest.mark.asyncio
async def test_react_recovers_from_malformed_action_json() -> None:
    model = FakeModelClient(
        [
            answer_turn('Action: {"action":"fetch","action_input":'),
            answer_turn("FinalAnswer: corrected"),
        ]
    )

    async def execute(*args: Any):
        raise AssertionError("malformed action must not invoke a tool")

    result = await make_runner(model, execute, strategy="react").run()

    assert result.answer == "corrected"
    assert result.tool_calls_attempted == 0
    assert any(
        event.event_type == "model_round" and event.status == "warning"
        for event in result.events
    )


@pytest.mark.asyncio
async def test_parallel_tool_calls_execute_concurrently_but_keep_message_order() -> None:
    model = FakeModelClient(
        [
            tool_turn(
                ("call_1", "first", '{"query":"one"}'),
                ("call_2", "second", '{"query":"two"}'),
            ),
            answer_turn("done"),
        ]
    )
    active = 0
    max_active = 0

    async def execute(name: str, arguments: dict[str, Any], call_id: str, iteration: int):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return RuntimeToolResult(output=name)

    result = await make_runner(
        model,
        execute,
        strategy="function_calling",
        parallel_tool_calls=True,
        tools=[runtime_tool("first"), runtime_tool("second")],
    ).run()

    assert result.answer == "done"
    assert max_active == 2
    tool_messages = [
        message for message in model.requests[1]["messages"] if message["role"] == "tool"
    ]
    assert [message["tool_call_id"] for message in tool_messages] == ["call_1", "call_2"]


@pytest.mark.asyncio
async def test_parallel_batch_preflight_blocks_every_tool_before_execution() -> None:
    model = FakeModelClient(
        [
            tool_turn(
                ("call_1", "first", '{"query":"one"}'),
                ("call_2", "second", '{"query":"two"}'),
            )
        ]
    )
    executed: list[str] = []
    preflight_calls: list[list[tuple[str, dict[str, Any], str]]] = []

    async def execute(name: str, arguments: dict[str, Any], call_id: str, iteration: int):
        executed.append(name)
        return RuntimeToolResult(output=name)

    failure = RuntimeMiddlewareFatalError("batch rejected")

    async def preflight(
        calls: list[tuple[str, dict[str, Any], str]], iteration: int
    ) -> None:
        assert iteration == 1
        preflight_calls.append(calls)
        raise failure

    with pytest.raises(RuntimeMiddlewareFatalError) as exc_info:
        await make_runner(
            model,
            execute,
            strategy="function_calling",
            parallel_tool_calls=True,
            tool_batch_preflight=preflight,
            tools=[runtime_tool("first"), runtime_tool("second")],
        ).run()

    assert exc_info.value is failure
    assert executed == []
    assert preflight_calls == [
        [
            ("first", {"query": "one"}, "call_1"),
            ("second", {"query": "two"}, "call_2"),
        ]
    ]


@pytest.mark.asyncio
async def test_iteration_limit_runs_one_answer_only_summary_call() -> None:
    model = FakeModelClient(
        [
            tool_turn(("call_1", "fetch", '{"query":"one"}')),
            answer_turn("summary after limit"),
        ]
    )

    async def execute(name: str, arguments: dict[str, Any], call_id: str, iteration: int):
        return RuntimeToolResult(output="tool result")

    result = await make_runner(
        model,
        execute,
        strategy="function_calling",
        max_iterations=1,
    ).run()

    assert result.answer == "summary after limit"
    assert model.requests[-1]["tool_choice"] == "none"
    assert model.requests[-1]["parallel_tool_calls"] is False
    assert any(event.metadata.get("final_summary") for event in result.events)


@pytest.mark.asyncio
async def test_terminal_tool_result_finishes_without_another_model_call() -> None:
    model = FakeModelClient(
        [tool_turn(("call_terminal", "finish", '{"query":"done"}'))]
    )

    async def execute(name: str, arguments: dict[str, Any], call_id: str, iteration: int):
        return RuntimeToolResult(output="terminal result")

    terminal_tool = runtime_tool("finish")
    terminal_tool.terminal = True
    result = await make_runner(
        model,
        execute,
        strategy="function_calling",
        tools=[terminal_tool],
    ).run()

    assert result.answer == "terminal result"
    assert result.tool_calls_executed == 1
    assert len(model.requests) == 1


@pytest.mark.asyncio
async def test_policy_denial_is_fatal_and_not_retry_safe() -> None:
    model = FakeModelClient([tool_turn(("call_1", "fetch", '{"query":"x"}'))])

    async def execute(name: str, arguments: dict[str, Any], call_id: str, iteration: int):
        raise RuntimeToolError(name, "denied", code="tool_denied")

    with pytest.raises(AgentStrategyError) as exc_info:
        await make_runner(model, execute, strategy="function_calling").run()

    assert exc_info.value.code == "tool_denied"
    assert exc_info.value.retry_safe is False


def test_capability_and_schema_errors_are_not_retry_safe() -> None:
    assert AgentStrategyError(
        "missing tool",
        code="capability_not_found",
    ).retry_safe is False
    assert AgentStrategyError(
        "bad schema",
        code="invalid_tool_schema",
    ).retry_safe is False


def test_argument_summary_redacts_secrets_and_observation_truncates() -> None:
    summary = summarize_arguments(
        {"query": "docs", "api_key": "secret", "nested": {"password": "hidden"}}
    )
    assert "docs" in summary
    assert "secret" not in summary
    assert "hidden" not in summary
    assert "***" in summary

    truncated = truncate_observation("x" * 20, limit=10)
    assert truncated.startswith("x" * 10)
    assert "已截断" in truncated


def test_parse_react_decision_rejects_thought_only() -> None:
    decision = parse_react_decision("Thought: still thinking")
    assert decision.kind == "invalid"
