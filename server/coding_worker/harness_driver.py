from __future__ import annotations

import hashlib
import json

from .harness_protocol import (
    HarnessBinding,
    HarnessEventEnvelope,
    HarnessEventKind,
    HarnessLifecycleKernel,
    HarnessSessionRef,
    HarnessTurnRef,
)
from .provider import ProviderEvent, ProviderEventKind, ProviderSession


_EVENT_KINDS = {
    ProviderEventKind.SESSION_OPENED: HarnessEventKind.MESSAGE,
    ProviderEventKind.MESSAGE: HarnessEventKind.MESSAGE,
    ProviderEventKind.PLAN: HarnessEventKind.PLAN,
    ProviderEventKind.TODO: HarnessEventKind.TODO,
    ProviderEventKind.USAGE: HarnessEventKind.USAGE,
    ProviderEventKind.TOOL_STARTED: HarnessEventKind.TOOL_STARTED,
    ProviderEventKind.TOOL_COMPLETED: HarnessEventKind.TOOL_COMPLETED,
    ProviderEventKind.TURN_COMPLETED: HarnessEventKind.TURN_COMPLETED,
    ProviderEventKind.FAILED: HarnessEventKind.ERROR,
    ProviderEventKind.CANCELLED: HarnessEventKind.ERROR,
    ProviderEventKind.APPROVAL_REQUIRED: HarnessEventKind.MESSAGE,
    ProviderEventKind.QUESTION: HarnessEventKind.MESSAGE,
    ProviderEventKind.COMPACTION: HarnessEventKind.MESSAGE,
    ProviderEventKind.CHECKPOINT: HarnessEventKind.MESSAGE,
}


class ProviderV4HarnessTranslator:
    """Correlate Provider-v4 frames through the V20 lifecycle kernel.

    Tool side effects remain outside this translator and can only arrive over
    the ModelMirror Broker RPC. The translator neither executes nor retries a
    request; it only fences the private session/turn event lifecycle.
    """

    def __init__(self, binding: HarnessBinding, session: ProviderSession) -> None:
        if session.task_id != binding.task_id:
            raise ValueError("provider session belongs to another harness task")
        self._kernel = HarnessLifecycleKernel()
        self._kernel.initialize(binding.descriptor)
        self.session = HarnessSessionRef(
            binding=binding,
            session_id=session.session_id,
        )
        self._kernel.open_session(self.session)
        self._turn: HarnessTurnRef | None = None
        self._sequence = 0

    def start_turn(self, turn_id: str) -> HarnessTurnRef:
        turn = HarnessTurnRef(session=self.session, turn_id=turn_id)
        self._kernel.start_turn(turn)
        self._turn = turn
        return turn

    def accept(self, event: ProviderEvent, *, turn_id: str) -> HarnessEventEnvelope:
        if self._turn is None or self._turn.turn_id != turn_id:
            raise ValueError("provider event does not match the active harness turn")
        kind = _EVENT_KINDS.get(event.kind)
        if kind is None:
            raise ValueError("provider event kind is unavailable to the V20 harness")
        self._sequence += 1
        payload = {"provider_kind": event.kind.value, "data": event.data}
        digest = hashlib.sha256(
            json.dumps(
                {
                    "task_id": self.session.binding.task_id,
                    "session_id": self.session.session_id,
                    "turn_id": turn_id,
                    "sequence": self._sequence,
                    "payload": payload,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        envelope = HarnessEventEnvelope(
            event_id=f"event_{digest[:32]}",
            sequence=self._sequence,
            session=self.session,
            turn=self._turn,
            kind=kind,
            payload=payload,
        )
        self._kernel.accept_event(envelope)
        if kind is HarnessEventKind.TURN_COMPLETED:
            self._turn = None
        return envelope

    def interrupt_turn(self, *, turn_id: str) -> None:
        if self._turn is None:
            return
        if self._turn.turn_id != turn_id:
            raise ValueError("harness interrupt targets another turn")
        self._kernel.interrupt(self._turn)
        self._turn = None

    def close(self) -> None:
        if self._turn is not None:
            self._kernel.interrupt(self._turn)
            self._turn = None
        self._kernel.close_session(self.session)
