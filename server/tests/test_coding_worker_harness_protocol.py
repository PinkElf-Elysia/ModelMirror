from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.coding_worker.harness_protocol import (
    HarnessBinding,
    HarnessCapabilityState,
    HarnessDescriptor,
    HarnessEventEnvelope,
    HarnessEventKind,
    HarnessLifecycleKernel,
    HarnessPersistenceLevel,
    HarnessProtocolError,
    HarnessRequestKind,
    HarnessRequestRef,
    HarnessResponse,
    HarnessResponseOutcome,
    HarnessSessionRef,
    HarnessToolOwnership,
    HarnessTurnRef,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = (
    ROOT / "server/tests/fixtures/coding_worker_v20_harness_lifecycles.json"
)


def _descriptor() -> HarnessDescriptor:
    return HarnessDescriptor(
        protocol_id="modelmirror-harness",
        protocol_version="20.0",
        implementation_version="fixture-1",
        schema_sha256="a" * 64,
        tool_ownership=HarnessToolOwnership.BROKER_ONLY,
        persistence=HarnessPersistenceLevel.SESSION_RESUME,
        capabilities={
            "steering": HarnessCapabilityState(supported=True, available=True),
            "usage": HarnessCapabilityState(
                supported=False,
                available=False,
                reason="fixture does not emit usage",
            ),
        },
    )


def _session(
    descriptor: HarnessDescriptor,
    *,
    generation: int = 1,
    task_id: str = "task_fixture",
    session_id: str = "session_fixture",
) -> HarnessSessionRef:
    return HarnessSessionRef(
        binding=HarnessBinding(
            task_id=task_id,
            route_id="coding/default",
            slot_id="slot_a",
            binding_sha256="b" * 64,
            driver_generation=generation,
            descriptor=descriptor,
        ),
        session_id=session_id,
    )


def _exercise_trace(actions: list[dict[str, object]]) -> None:
    kernel = HarnessLifecycleKernel()
    descriptor = _descriptor()
    session = _session(descriptor)
    turn = HarnessTurnRef(session=session, turn_id="turn_fixture")
    request = HarnessRequestRef(
        turn=turn,
        request_id="request_fixture",
        kind=HarnessRequestKind.APPROVAL,
    )
    sequence = 0
    for item in actions:
        action = item["action"]
        if action == "initialize":
            kernel.initialize(descriptor)
        elif action == "open":
            kernel.open_session(session)
        elif action == "start_turn":
            kernel.start_turn(turn)
        elif action == "event":
            sequence += 1
            kernel.accept_event(
                HarnessEventEnvelope(
                    event_id=f"event_{sequence}",
                    sequence=sequence,
                    session=session,
                    turn=turn,
                    kind=HarnessEventKind.MESSAGE,
                    payload={"text": "fixture"},
                )
            )
        elif action == "request":
            sequence += 1
            kernel.accept_event(
                HarnessEventEnvelope(
                    event_id=f"event_{sequence}",
                    sequence=sequence,
                    session=session,
                    turn=turn,
                    request=request,
                    kind=HarnessEventKind.REQUEST,
                )
            )
        elif action == "reply":
            kernel.resolve_request(
                HarnessResponse(
                    ref=request,
                    outcome=HarnessResponseOutcome.DECLINED,
                )
            )
        elif action == "steer":
            kernel.steer(turn)
        elif action == "interrupt":
            kernel.interrupt(turn)
        elif action == "resume":
            resumed = _session(descriptor, generation=2)
            kernel.resume_session(session, resumed)
            session = resumed
        elif action == "close":
            kernel.close_session(session)
        else:  # pragma: no cover - fixture validation reports the exact action
            raise AssertionError(f"unsupported fixture action: {action}")


def test_current_acp_and_codex_lifecycles_replay_through_the_kernel() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert fixture["protocol"] == "modelmirror-harness-lifecycle-traces/v2"
    assert {trace["name"] for trace in fixture["traces"]} == {
        "acp-v1.19",
        "codex-app-server-0.149.0",
    }
    methods: set[str] = set()
    for trace in fixture["traces"]:
        actions = trace["actions"]
        assert actions[0]["action"] == "initialize"
        assert actions[-1]["action"] == "close"
        for action in actions:
            source = action["source_frame"]
            method = source.get("method")
            if method:
                methods.add(method)
        _exercise_trace(actions)

    assert "session/load" not in methods
    assert "thread/close" not in methods
    assert "session/resume" in methods
    assert "thread/unsubscribe" in methods


def test_kernel_rejects_stale_turn_cross_task_and_noncontiguous_events() -> None:
    descriptor = _descriptor()
    session = _session(descriptor)
    turn = HarnessTurnRef(session=session, turn_id="turn_current")
    kernel = HarnessLifecycleKernel()
    kernel.initialize(descriptor)
    kernel.open_session(session)
    kernel.start_turn(turn)

    stale_turn = HarnessTurnRef(session=session, turn_id="turn_stale")
    with pytest.raises(HarnessProtocolError, match="stale or not active"):
        kernel.steer(stale_turn)

    cross_task_session = _session(
        descriptor,
        task_id="task_other",
        session_id=session.session_id,
    )
    with pytest.raises(HarnessProtocolError, match="stale or cross-task"):
        kernel.interrupt(
            HarnessTurnRef(session=cross_task_session, turn_id=turn.turn_id)
        )

    with pytest.raises(HarnessProtocolError, match="not contiguous"):
        kernel.accept_event(
            HarnessEventEnvelope(
                event_id="event_gap",
                sequence=2,
                session=session,
                turn=turn,
                kind=HarnessEventKind.MESSAGE,
            )
        )


def test_kernel_settles_each_request_exactly_once() -> None:
    descriptor = _descriptor()
    session = _session(descriptor)
    turn = HarnessTurnRef(session=session, turn_id="turn_current")
    request = HarnessRequestRef(
        turn=turn,
        request_id="request_once",
        kind=HarnessRequestKind.USER_INPUT,
    )
    kernel = HarnessLifecycleKernel()
    kernel.initialize(descriptor)
    kernel.open_session(session)
    kernel.start_turn(turn)
    kernel.accept_event(
        HarnessEventEnvelope(
            event_id="event_request",
            sequence=1,
            session=session,
            turn=turn,
            request=request,
            kind=HarnessEventKind.REQUEST,
        )
    )
    response = HarnessResponse(
        ref=request,
        outcome=HarnessResponseOutcome.COMPLETED,
        payload={"answer": "continue"},
    )
    kernel.resolve_request(response)

    with pytest.raises(HarnessProtocolError, match="not pending"):
        kernel.resolve_request(response)
    with pytest.raises(HarnessProtocolError, match="replayed"):
        kernel.accept_event(
            HarnessEventEnvelope(
                event_id="event_request_again",
                sequence=2,
                session=session,
                turn=turn,
                request=request,
                kind=HarnessEventKind.REQUEST,
            )
        )


def test_capabilities_and_checkpoint_fail_closed() -> None:
    with pytest.raises(ValueError, match="must be supported"):
        HarnessCapabilityState(
            supported=False,
            available=True,
        )
    with pytest.raises(ValueError, match="requires a reason"):
        HarnessCapabilityState(
            supported=True,
            available=False,
        )
