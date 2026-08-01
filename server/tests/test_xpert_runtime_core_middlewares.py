from __future__ import annotations

from typing import Any

import pytest

from server.xpert_runtime.core_middlewares import (
    RuntimeMiddlewareSpec,
    build_context_compression_middleware,
    select_runtime_tools,
    validate_structured_output,
)
from server.xpert_runtime.middleware import MiddlewarePipeline
from server.xpert_runtime.models import MiddlewareContext, ModelCallRequest
from server.xpert_runtime.toolset import RuntimeTool


@pytest.mark.asyncio
async def test_context_compression_summarizes_old_messages_and_persists_boundary() -> None:
    summary_calls: list[tuple[str, list[dict[str, Any]], int]] = []
    persisted: list[tuple[str, str, str | None]] = []

    async def summarize(
        model_id: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
    ) -> str:
        summary_calls.append((model_id, messages, max_tokens))
        return "The user selected project Aurora."

    async def persist(summary: str, model_id: str, message_id: str | None) -> None:
        persisted.append((summary, model_id, message_id))

    middleware = build_context_compression_middleware(
        RuntimeMiddlewareSpec(
            node_id="compress",
            middleware_id="context_compression",
            config={
                "max_context_tokens": 2048,
                "trigger_ratio": 0.5,
                "keep_recent_messages": 2,
                "summary_model_id": "summary-model",
                "summary_max_tokens": 512,
            },
        )
    )
    pipeline = MiddlewarePipeline([middleware])
    older = "Earlier context " * 500
    request = ModelCallRequest(
        model_id="answer-model",
        messages=[
            {"role": "system", "content": "Stay concise."},
            {"role": "user", "content": older, "message_id": "m1"},
            {"role": "assistant", "content": older, "message_id": "m2"},
            {"role": "user", "content": "Latest question", "message_id": "m3"},
            {"role": "assistant", "content": "Latest answer", "message_id": "m4"},
        ],
    )
    context = MiddlewareContext(
        metadata={
            "middleware_model_text": summarize,
            "persist_conversation_summary": persist,
        }
    )

    prepared = await pipeline.before_model(request, context)

    assert summary_calls[0][0] == "summary-model"
    assert persisted == [("The user selected project Aurora.", "summary-model", "m2")]
    assert [item["message_id"] for item in prepared.messages[-2:]] == ["m3", "m4"]
    assert "Conversation summary (derived)" in prepared.messages[1]["content"]
    assert context.metadata["context_compression"]["summarized_messages"] == 2


@pytest.mark.asyncio
async def test_context_compression_fails_open_to_recent_messages() -> None:
    async def fail_summary(*_args: Any) -> str:
        raise RuntimeError("summary unavailable")

    pipeline = MiddlewarePipeline(
        [
            build_context_compression_middleware(
                RuntimeMiddlewareSpec(
                    node_id="compress",
                    middleware_id="context_compression",
                    config={
                        "max_context_tokens": 2048,
                        "trigger_ratio": 0.5,
                        "keep_recent_messages": 2,
                    },
                )
            )
        ]
    )
    request = ModelCallRequest(
        model_id="model",
        messages=[
            {"role": "user", "content": "old " * 3000},
            {"role": "assistant", "content": "old answer " * 1000},
            {"role": "user", "content": "recent"},
            {"role": "assistant", "content": "recent answer"},
        ],
    )
    context = MiddlewareContext(metadata={"middleware_model_text": fail_summary})

    prepared = await pipeline.before_model(request, context)

    assert [item["content"] for item in prepared.messages] == ["recent", "recent answer"]
    assert "summary unavailable" in context.metadata["middleware_warnings"][0]


@pytest.mark.asyncio
async def test_context_compression_reuses_summary_without_resummarizing_boundary() -> None:
    async def unexpected_summary(*_args: Any) -> str:
        raise AssertionError("already summarized messages must not be summarized again")

    pipeline = MiddlewarePipeline(
        [
            build_context_compression_middleware(
                RuntimeMiddlewareSpec(
                    node_id="compress",
                    middleware_id="context_compression",
                    config={
                        "max_context_tokens": 2048,
                        "trigger_ratio": 0.5,
                        "keep_recent_messages": 2,
                    },
                )
            )
        ]
    )
    old = "already summarized " * 1000
    request = ModelCallRequest(
        model_id="model",
        messages=[
            {"role": "user", "content": old, "message_id": "m1"},
            {"role": "assistant", "content": old, "message_id": "m2"},
            {"role": "user", "content": "recent", "message_id": "m3"},
            {"role": "assistant", "content": "recent answer", "message_id": "m4"},
        ],
    )
    context = MiddlewareContext(
        metadata={
            "middleware_model_text": unexpected_summary,
            "conversation_summary": "Project Aurora remains active.",
            "conversation_summary_through_message_id": "m2",
        }
    )

    prepared = await pipeline.before_model(request, context)

    assert "Project Aurora remains active." in prepared.messages[0]["content"]
    assert [item["message_id"] for item in prepared.messages[-2:]] == ["m3", "m4"]
    assert context.metadata["context_compression"]["reused_summary"] is True


@pytest.mark.asyncio
async def test_structured_output_repairs_once_and_returns_canonical_json() -> None:
    repairs: list[str] = []

    async def repair(
        _model_id: str,
        messages: list[dict[str, Any]],
        _max_tokens: int,
    ) -> str:
        repairs.append(messages[0]["content"])
        return '{"status":"ok","count":2}'

    output = await validate_structured_output(
        "not-json",
        schema={
            "type": "object",
            "required": ["status", "count"],
            "properties": {
                "status": {"const": "ok"},
                "count": {"type": "integer"},
            },
            "additionalProperties": False,
        },
        model_id="repair-model",
        repair_attempts=1,
        model_text=repair,
    )

    assert output == '{"status":"ok","count":2}'
    assert len(repairs) == 1


@pytest.mark.asyncio
async def test_structured_output_raises_after_failed_repair() -> None:
    async def invalid(*_args: Any) -> str:
        return '{"count":"wrong"}'

    with pytest.raises(ValueError, match="Structured output validation failed"):
        await validate_structured_output(
            "{}",
            schema={
                "type": "object",
                "required": ["count"],
                "properties": {"count": {"type": "integer"}},
            },
            model_id="model",
            repair_attempts=1,
            model_text=invalid,
        )


@pytest.mark.asyncio
async def test_tool_selector_preserves_required_tools_and_filters_unknown_names() -> None:
    tools = [
        RuntimeTool(name="todo_list", provider="todo"),
        RuntimeTool(name="weather", description="Get a weather forecast"),
        RuntimeTool(name="search", description="Search the web"),
    ]

    async def choose(*_args: Any) -> str:
        return '{"tools":["not-allowed","weather","search"]}'

    selected, metadata = await select_runtime_tools(
        tools,
        user_prompt="What is the weather?",
        model_id="selector",
        max_selected_tools=2,
        required_tools={"todo_list"},
        model_text=choose,
    )

    assert [tool.name for tool in selected] == ["todo_list", "weather"]
    assert metadata["mode"] == "llm"


@pytest.mark.asyncio
async def test_tool_selector_uses_deterministic_fallback() -> None:
    tools = [
        RuntimeTool(name="weather", description="Get a weather forecast"),
        RuntimeTool(name="search", description="Search documents"),
    ]

    async def invalid(*_args: Any) -> str:
        return "invalid-json"

    selected, metadata = await select_runtime_tools(
        tools,
        user_prompt="weather forecast for Phoenix",
        model_id="selector",
        max_selected_tools=1,
        required_tools=set(),
        model_text=invalid,
    )

    assert [tool.name for tool in selected] == ["weather"]
    assert metadata["mode"] == "lexical_fallback"
    assert metadata["warning"]
