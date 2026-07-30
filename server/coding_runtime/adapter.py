from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Protocol

from .models import CodingEvent, CodingEventKind, CodingSession, CodingSessionState


class CodingAgentAdapter(Protocol):
    """Minimal supplier-neutral boundary needed by the first read-only slice."""

    async def open(self, session: CodingSession) -> CodingEvent: ...

    def prompt(
        self,
        session: CodingSession,
        prompt: str,
    ) -> AsyncIterator[CodingEvent]: ...

    async def cancel(self, session: CodingSession) -> bool: ...

    async def close(self, session: CodingSession) -> None: ...


class FakeCodingAgentAdapter:
    """Deterministic adapter used to validate the contract without ACP or a model."""

    def __init__(
        self,
        *,
        script: Sequence[tuple[CodingEventKind, dict[str, object]]] | None = None,
    ) -> None:
        self._script = tuple(
            script
            or (
                (CodingEventKind.PLAN, {"text": "Inspect relevant files"}),
                (
                    CodingEventKind.TOOL_STATUS,
                    {"tool": "read", "status": "completed"},
                ),
                (CodingEventKind.ANSWER_DELTA, {"text": "The answer."}),
            )
        )
        self._cancelled_sessions: set[str] = set()

    async def open(self, session: CodingSession) -> CodingEvent:
        session.transition(CodingSessionState.READY)
        return session.append_event(CodingEventKind.SESSION_STARTED)

    async def prompt(
        self,
        session: CodingSession,
        prompt: str,
    ) -> AsyncIterator[CodingEvent]:
        if not prompt.strip():
            raise ValueError("Prompt must not be empty")

        turn_id = session.begin_turn()
        yield session.append_event(CodingEventKind.TURN_STARTED, turn_id=turn_id)

        for kind, data in self._script:
            await asyncio.sleep(0)
            if (
                session.state is CodingSessionState.CANCELLING
                or session.session_id in self._cancelled_sessions
            ):
                self._cancelled_sessions.discard(session.session_id)
                yield session.append_event(CodingEventKind.CANCELLED, turn_id=turn_id)
                session.finish_turn()
                return
            yield session.append_event(kind, turn_id=turn_id, data=data)

        yield session.append_event(CodingEventKind.TURN_COMPLETED, turn_id=turn_id)
        session.finish_turn()

    async def cancel(self, session: CodingSession) -> bool:
        accepted = session.request_cancel()
        if accepted:
            self._cancelled_sessions.add(session.session_id)
        return accepted

    async def close(self, session: CodingSession) -> None:
        session.active_turn_id = None
        session.transition(CodingSessionState.CLOSED)
        self._cancelled_sessions.discard(session.session_id)
