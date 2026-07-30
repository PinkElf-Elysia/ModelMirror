from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class CodingEventKind(StrEnum):
    """Stable event names exposed by the Coding Runtime boundary."""

    SESSION_STARTED = "session_started"
    TURN_STARTED = "turn_started"
    PLAN = "plan"
    ANSWER_DELTA = "answer_delta"
    TOOL_STATUS = "tool_status"
    TURN_COMPLETED = "turn_completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    HEARTBEAT = "heartbeat"


class CodingSessionState(StrEnum):
    STARTING = "starting"
    READY = "ready"
    RUNNING = "running"
    CANCELLING = "cancelling"
    FAILED = "failed"
    CLOSED = "closed"


class InvalidCodingSessionTransition(RuntimeError):
    """Raised when a caller attempts an impossible session transition."""


_ALLOWED_TRANSITIONS: dict[CodingSessionState, frozenset[CodingSessionState]] = {
    CodingSessionState.STARTING: frozenset(
        {
            CodingSessionState.READY,
            CodingSessionState.FAILED,
            CodingSessionState.CLOSED,
        }
    ),
    CodingSessionState.READY: frozenset(
        {
            CodingSessionState.RUNNING,
            CodingSessionState.FAILED,
            CodingSessionState.CLOSED,
        }
    ),
    CodingSessionState.RUNNING: frozenset(
        {
            CodingSessionState.READY,
            CodingSessionState.CANCELLING,
            CodingSessionState.FAILED,
            CodingSessionState.CLOSED,
        }
    ),
    CodingSessionState.CANCELLING: frozenset(
        {
            CodingSessionState.READY,
            CodingSessionState.FAILED,
            CodingSessionState.CLOSED,
        }
    ),
    CodingSessionState.FAILED: frozenset({CodingSessionState.CLOSED}),
    CodingSessionState.CLOSED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class CodingEvent:
    session_id: str
    seq: int
    kind: CodingEventKind
    created_at: float
    turn_id: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "seq": self.seq,
            "type": self.kind.value,
            "created_at": self.created_at,
            "turn_id": self.turn_id,
            "data": dict(self.data),
        }


@dataclass(slots=True)
class CodingSession:
    """In-memory lifecycle and monotonic event sequence for one coding session."""

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    state: CodingSessionState = CodingSessionState.STARTING
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    active_turn_id: str | None = None
    _next_seq: int = 1

    def transition(self, target: CodingSessionState) -> None:
        if target == self.state:
            return
        if target not in _ALLOWED_TRANSITIONS[self.state]:
            raise InvalidCodingSessionTransition(
                f"Cannot transition coding session from {self.state.value} "
                f"to {target.value}"
            )
        self.state = target
        self.updated_at = time.time()

    def begin_turn(self) -> str:
        if self.state is not CodingSessionState.READY:
            raise InvalidCodingSessionTransition(
                f"Cannot begin turn while session is {self.state.value}"
            )
        turn_id = str(uuid.uuid4())
        self.active_turn_id = turn_id
        self.transition(CodingSessionState.RUNNING)
        return turn_id

    def finish_turn(self) -> None:
        if self.state not in {
            CodingSessionState.RUNNING,
            CodingSessionState.CANCELLING,
        }:
            raise InvalidCodingSessionTransition(
                f"Cannot finish turn while session is {self.state.value}"
            )
        self.active_turn_id = None
        self.transition(CodingSessionState.READY)

    def request_cancel(self) -> bool:
        if self.state is CodingSessionState.CANCELLING:
            return False
        if self.state is not CodingSessionState.RUNNING:
            return False
        self.transition(CodingSessionState.CANCELLING)
        return True

    def append_event(
        self,
        kind: CodingEventKind,
        *,
        turn_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> CodingEvent:
        event = CodingEvent(
            session_id=self.session_id,
            seq=self._next_seq,
            kind=kind,
            created_at=time.time(),
            turn_id=turn_id,
            data=dict(data or {}),
        )
        self._next_seq += 1
        self.updated_at = event.created_at
        return event
