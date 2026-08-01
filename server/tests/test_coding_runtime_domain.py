from __future__ import annotations

import pytest

from server.coding_runtime import (
    CodingEventKind,
    CodingSession,
    CodingSessionState,
    FakeCodingAgentAdapter,
    InvalidCodingSessionTransition,
)


@pytest.mark.asyncio
async def test_fake_adapter_emits_normal_lifecycle_with_monotonic_sequence() -> None:
    adapter = FakeCodingAgentAdapter()
    session = CodingSession(session_id="session-1")

    events = [await adapter.open(session)]
    events.extend([event async for event in adapter.prompt(session, "Explain routing")])

    assert [event.kind for event in events] == [
        CodingEventKind.SESSION_STARTED,
        CodingEventKind.TURN_STARTED,
        CodingEventKind.PLAN,
        CodingEventKind.TOOL_STATUS,
        CodingEventKind.ANSWER_DELTA,
        CodingEventKind.TURN_COMPLETED,
    ]
    assert [event.seq for event in events] == [1, 2, 3, 4, 5, 6]
    assert {event.session_id for event in events} == {"session-1"}
    assert session.state is CodingSessionState.READY
    assert session.active_turn_id is None


def test_illegal_session_transition_fails_closed() -> None:
    session = CodingSession()

    with pytest.raises(InvalidCodingSessionTransition):
        session.transition(CodingSessionState.RUNNING)

    assert session.state is CodingSessionState.STARTING


@pytest.mark.asyncio
async def test_cancel_is_idempotent_and_emits_one_terminal_event() -> None:
    adapter = FakeCodingAgentAdapter()
    session = CodingSession()
    await adapter.open(session)
    stream = adapter.prompt(session, "Inspect the repository")

    first_event = await anext(stream)
    assert first_event.kind is CodingEventKind.TURN_STARTED
    assert await adapter.cancel(session) is True
    assert await adapter.cancel(session) is False

    remaining = [event async for event in stream]

    assert [event.kind for event in remaining] == [CodingEventKind.CANCELLED]
    assert session.state is CodingSessionState.READY
    assert session.active_turn_id is None


@pytest.mark.asyncio
async def test_closed_session_rejects_new_turn() -> None:
    adapter = FakeCodingAgentAdapter()
    session = CodingSession()
    await adapter.open(session)
    await adapter.close(session)

    with pytest.raises(InvalidCodingSessionTransition):
        await anext(adapter.prompt(session, "Cannot run"))
