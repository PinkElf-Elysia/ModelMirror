from __future__ import annotations

import json

import pytest

from server.xpert_runtime import run_ralph_loop


@pytest.mark.asyncio
async def test_ralph_loop_accepts_verified_initial_output() -> None:
    calls: list[str] = []

    async def model_text(model_id, messages, max_tokens):
        calls.append(model_id)
        assert max_tokens == 800
        assert "Candidate" in messages[0]["content"]
        return json.dumps(
            {"complete": True, "reason": "All requirements are met.", "feedback": ""}
        )

    async def continue_agent(_instruction, _iteration):
        raise AssertionError("verified output must not continue")

    result = await run_ralph_loop(
        "complete answer",
        objective="Produce a complete answer",
        model_id="primary",
        verifier_model_id="verifier",
        max_iterations=3,
        max_output_chars=1000,
        model_text=model_text,
        continue_agent=continue_agent,
    )

    assert result.verified is True
    assert result.output == "complete answer"
    assert result.iterations == 1
    assert calls == ["verifier"]


@pytest.mark.asyncio
async def test_ralph_loop_continues_until_verified() -> None:
    decisions = iter(
        [
            {"complete": False, "reason": "Missing evidence", "feedback": "Add evidence"},
            {"complete": True, "reason": "Evidence added", "feedback": ""},
        ]
    )
    checkpoints: list[str] = []

    async def model_text(_model_id, _messages, _max_tokens):
        return json.dumps(next(decisions))

    async def continue_agent(instruction, iteration):
        assert "Add evidence" in instruction
        assert iteration == 1
        return "answer with evidence"

    async def checkpoint(event_type, _summary, _metadata):
        checkpoints.append(event_type)

    result = await run_ralph_loop(
        "draft",
        objective="Answer with evidence",
        model_id="primary",
        verifier_model_id="",
        max_iterations=4,
        max_output_chars=1000,
        model_text=model_text,
        continue_agent=continue_agent,
        checkpoint=checkpoint,
    )

    assert result.verified is True
    assert result.output == "answer with evidence"
    assert result.iterations == 2
    assert checkpoints == ["ralph.continue", "ralph.verified"]


@pytest.mark.asyncio
async def test_ralph_loop_stops_on_no_progress() -> None:
    async def model_text(_model_id, _messages, _max_tokens):
        return '{"complete":false,"reason":"incomplete","feedback":"continue"}'

    async def continue_agent(_instruction, _iteration):
        return "same answer"

    result = await run_ralph_loop(
        "same answer",
        objective="Improve the answer",
        model_id="primary",
        verifier_model_id="",
        max_iterations=5,
        max_output_chars=1000,
        model_text=model_text,
        continue_agent=continue_agent,
    )

    assert result.verified is False
    assert result.iterations == 2
    assert result.reason.startswith("No progress")


@pytest.mark.asyncio
async def test_ralph_loop_fails_closed_on_budget_or_invalid_verifier() -> None:
    async def model_text(_model_id, _messages, _max_tokens):
        return "not-json"

    async def continue_agent(_instruction, _iteration):
        return "x" * 30

    result = await run_ralph_loop(
        "x" * 30,
        objective="Long result",
        model_id="primary",
        verifier_model_id="",
        max_iterations=3,
        max_output_chars=20,
        model_text=model_text,
        continue_agent=continue_agent,
    )

    assert result.verified is False
    assert result.budget_limited is True
    assert result.reason == "Ralph output budget reached."
