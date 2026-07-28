from __future__ import annotations

import json
import time
from typing import Any

import pytest

from server.context_engine import (
    estimate_messages_tokens,
    optimize_context,
)


@pytest.mark.asyncio
async def test_deterministic_compression_preserves_protected_content() -> None:
    protected_code = "```python\nprint('keep exactly')\n```"
    protected_json = json.dumps(
        {"items": [{"id": index, "value": "x" * 20} for index in range(80)]}
    )
    latest_user = (
        "请检查 https://example.test/file 并保留引用 [12]\n" + protected_code
    )
    repeated_tool = "\n".join(["plain diagnostic output " * 12] * 100)
    messages = [
        {"role": "system", "content": "Never change this system instruction."},
        {"role": "tool", "content": repeated_tool, "tool_call_id": "call-1"},
        {"role": "tool", "content": protected_json, "tool_call_id": "call-2"},
        {"role": "user", "content": latest_user},
    ]

    result = await optimize_context(
        messages,
        profile="strong",
        max_context_tokens=16_000,
        max_output_tokens=100,
        max_tool_output_chars=1_000,
    )

    serialized = json.dumps(result.messages, ensure_ascii=False)
    assert result.report.applied is True
    assert result.report.saved_ratio >= 0.10
    assert result.report.fidelity_status == "passed"
    assert messages[0]["content"] in serialized
    assert any(
        message.get("content") == protected_json for message in result.messages
    )
    assert any(
        message.get("content") == latest_user for message in result.messages
    )
    assert any(
        protected_code in str(message.get("content") or "")
        for message in result.messages
    )


@pytest.mark.asyncio
async def test_context_engine_summarizes_old_plain_history_and_is_idempotent() -> None:
    calls: list[list[dict[str, Any]]] = []

    async def summarize(
        _model_id: str,
        messages: list[dict[str, Any]],
        _max_tokens: int,
    ) -> str:
        calls.append(messages)
        return "用户已决定采用 Aurora，并要求保留预算上限。"

    old = "Earlier plain discussion about Aurora and the budget. " * 500
    messages = [
        {"role": "system", "content": "Stay precise."},
        {"role": "user", "content": old, "message_id": "m1"},
        {"role": "assistant", "content": old, "message_id": "m2"},
        {"role": "user", "content": "What is next?", "message_id": "m3"},
    ]
    first = await optimize_context(
        messages,
        profile="auto",
        max_context_tokens=3_000,
        max_output_tokens=300,
        keep_recent_messages=1,
        summarizer=summarize,
        summary_model_id="summary-model",
    )
    second = await optimize_context(
        first.messages,
        profile="standard",
        max_context_tokens=3_000,
        max_output_tokens=300,
        keep_recent_messages=1,
        summarizer=summarize,
        summary_model_id="summary-model",
    )

    assert len(calls) == 1
    assert first.report.summarized_messages == 2
    assert first.report.saved_ratio >= 0.15
    assert first.messages[-1]["content"] == "What is next?"
    assert second.messages == first.messages


@pytest.mark.asyncio
async def test_small_savings_fall_back_to_original() -> None:
    messages = [
        {"role": "assistant", "content": "same sentence. same sentence."},
        {"role": "user", "content": "latest"},
    ]
    result = await optimize_context(
        messages,
        profile="strong",
        max_context_tokens=128_000,
    )
    assert result.messages == messages
    assert result.report.applied is False
    assert result.report.fidelity_status in {"not_needed", "fallback"}


@pytest.mark.asyncio
async def test_deterministic_rules_p95_budget_smoke() -> None:
    messages = [
        {
            "role": "tool",
            "tool_call_id": "call",
            "content": ("diagnostic line without structure " * 1000),
        },
        {"role": "user", "content": "latest"},
    ]
    durations = []
    for _ in range(30):
        started = time.perf_counter()
        result = await optimize_context(
            messages,
            profile="standard",
            max_context_tokens=128_000,
            max_tool_output_chars=1_000,
        )
        durations.append((time.perf_counter() - started) * 1000)
        assert estimate_messages_tokens(result.messages) < estimate_messages_tokens(
            messages
        )
    durations.sort()
    assert durations[int(len(durations) * 0.95) - 1] < 20
