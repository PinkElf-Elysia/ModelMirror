from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.coding_worker.harness_protocol import (
    HarnessBinding,
    HarnessCapabilityMaturity,
    HarnessCapabilityState,
    HarnessCheckpoint,
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
from server.coding_worker.harness_driver import (
    HarnessDriverProtocolError,
    ProviderV4HarnessTranslator,
)
from server.coding_worker.provider import (
    ProviderCapabilities,
    ProviderEvent,
    ProviderEventKind,
    ProviderSession,
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
    binding_sha256: str = "b" * 64,
) -> HarnessSessionRef:
    return HarnessSessionRef(
        binding=HarnessBinding(
            task_id=task_id,
            route_id="coding/default",
            slot_id="slot_a",
            binding_sha256=binding_sha256,
            driver_generation=generation,
            descriptor=descriptor,
        ),
        session_id=session_id,
    )


def test_provider_v4_translator_fences_session_turn_and_sequence() -> None:
    binding = HarnessBinding(
        task_id="task_fixture",
        route_id="coding/default",
        slot_id="slot_a",
        binding_sha256="b" * 64,
        driver_generation=3,
        descriptor=_descriptor(),
    )
    session = ProviderSession(
        session_id="session_fixture",
        task_id="task_fixture",
        provider_capabilities=ProviderCapabilities(),
    )
    translator = ProviderV4HarnessTranslator(binding, session)
    translator.start_turn("turn_fixture")

    first = translator.accept(
        ProviderEvent(kind=ProviderEventKind.MESSAGE, data={"text": "inspect"}),
        turn_id="turn_fixture",
    )
    completed = translator.accept(
        ProviderEvent(kind=ProviderEventKind.TURN_COMPLETED),
        turn_id="turn_fixture",
    )

    assert first.sequence == 1
    assert first.kind is HarnessEventKind.MESSAGE
    assert completed.sequence == 2
    assert completed.kind is HarnessEventKind.TURN_COMPLETED
    with pytest.raises(HarnessDriverProtocolError, match="active harness turn"):
        translator.accept(
            ProviderEvent(kind=ProviderEventKind.MESSAGE, data={"text": "late"}),
            turn_id="turn_fixture",
        )
    translator.close()


def test_provider_v4_translator_rejects_cross_task_session() -> None:
    binding = HarnessBinding(
        task_id="task_fixture",
        route_id="coding/default",
        slot_id="slot_a",
        binding_sha256="b" * 64,
        driver_generation=3,
        descriptor=_descriptor(),
    )
    with pytest.raises(HarnessDriverProtocolError, match="another harness task"):
        ProviderV4HarnessTranslator(
            binding,
            ProviderSession(
                session_id="session_foreign",
                task_id="task_foreign",
                provider_capabilities=ProviderCapabilities(),
            ),
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
    acp, codex = fixture["traces"]
    assert acp["schema_release"] == "schema-v1.19.0"
    assert acp["schema_commit"] == "a213df5240048f96d2b23f644984bb20c188a234"
    assert codex["package"] == "@openai/codex"
    assert codex["package_version"] == "0.149.0"
    assert codex["schema_bundle_sha256"] == (
        "02a4c63a638fdae4a5f6c3ad32a41a377b642c66f3abc84f6fc47c7f3d6074df"
    )
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
    command_approval = next(
        action["source_frame"]
        for action in codex["actions"]
        if action["source_frame"].get("method")
        == "item/commandExecution/requestApproval"
    )
    assert set(command_approval["params"]) == {
        "threadId",
        "turnId",
        "itemId",
        "startedAtMs",
    }


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


def test_resume_rejects_events_from_the_previous_driver_generation() -> None:
    descriptor = _descriptor()
    original = _session(descriptor)
    original_turn = HarnessTurnRef(session=original, turn_id="turn_original")
    resumed = _session(descriptor, generation=2)
    kernel = HarnessLifecycleKernel()
    kernel.initialize(descriptor)
    kernel.open_session(original)
    kernel.start_turn(original_turn)
    kernel.interrupt(original_turn)
    kernel.resume_session(original, resumed)

    with pytest.raises(HarnessProtocolError, match="stale or cross-task"):
        kernel.accept_event(
            HarnessEventEnvelope(
                event_id="event_late",
                sequence=1,
                session=original,
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


def test_event_and_request_deduplication_is_scoped_to_the_exact_binding() -> None:
    descriptor = _descriptor()
    first_session = _session(
        descriptor,
        task_id="task_first",
        binding_sha256="b" * 64,
    )
    second_session = _session(
        descriptor,
        task_id="task_second",
        binding_sha256="c" * 64,
    )
    first_turn = HarnessTurnRef(session=first_session, turn_id="turn_shared")
    second_turn = HarnessTurnRef(session=second_session, turn_id="turn_shared")
    first_request = HarnessRequestRef(
        turn=first_turn,
        request_id="request_shared",
        kind=HarnessRequestKind.APPROVAL,
    )
    second_request = HarnessRequestRef(
        turn=second_turn,
        request_id="request_shared",
        kind=HarnessRequestKind.APPROVAL,
    )
    kernel = HarnessLifecycleKernel()
    kernel.initialize(descriptor)

    for session, turn, request in (
        (first_session, first_turn, first_request),
        (second_session, second_turn, second_request),
    ):
        kernel.open_session(session)
        kernel.start_turn(turn)
        kernel.accept_event(
            HarnessEventEnvelope(
                event_id="event_shared",
                sequence=1,
                session=session,
                turn=turn,
                request=request,
                kind=HarnessEventKind.REQUEST,
            )
        )
        kernel.resolve_request(
            HarnessResponse(
                ref=request,
                outcome=HarnessResponseOutcome.APPROVED,
            )
        )


def test_capabilities_and_checkpoint_fail_closed() -> None:
    descriptor = _descriptor()
    missing = descriptor.capability("experimental_vendor_feature")
    assert not missing.supported
    assert not missing.available
    assert missing.maturity is HarnessCapabilityMaturity.EXPERIMENTAL
    assert missing.reason == "capability was not declared"

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
    with pytest.raises(ValueError, match="non-persistent"):
        HarnessCheckpoint(
            checkpoint_id="checkpoint_none",
            session=_session(descriptor),
            persistence=HarnessPersistenceLevel.NONE,
            workspace_tree_hash="d" * 64,
        )


def test_unknown_events_and_supplier_experimental_fields_are_rejected() -> None:
    descriptor = _descriptor()
    session = _session(descriptor)
    with pytest.raises(ValueError):
        HarnessEventEnvelope.model_validate(
            {
                "event_id": "event_unknown",
                "sequence": 1,
                "session": session.model_dump(mode="json"),
                "kind": "supplier_private_event",
            }
        )
    with pytest.raises(ValueError):
        HarnessDescriptor.model_validate(
            {
                **descriptor.model_dump(mode="json"),
                "experimentalSupplierField": True,
            }
        )
