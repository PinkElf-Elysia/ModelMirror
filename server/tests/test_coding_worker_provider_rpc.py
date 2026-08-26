from __future__ import annotations

import asyncio
import contextlib
import hashlib
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from server.coding_worker.broker_rpc import BrokerRPCServer
from server.coding_worker.adapters import LegacyHarnessDriver
from server.coding_worker.contracts import PolicyProfile, TaskBudget
from server.coding_worker.harness_contracts import HarnessEventKind, HarnessOpenRequest
from server.coding_worker.ports import CodingSubstrateError
from server.coding_worker.provider import (
    FakeCodingAgentProvider,
    ProviderCapabilities,
    ProviderEvent,
    ProviderEventKind,
    ProviderOpenRequest,
    ProviderSession,
)
from server.coding_worker.provider_rpc import (
    MAX_PROVIDER_RPC_BYTES,
    ProviderRPCError,
    ProviderRPCRequest,
    ProviderRPCServer,
    ProviderSidecarClientPool,
)
from server.coding_worker.harness_v3 import (
    PROVIDER_HARNESS_CODE_FILES,
    harness_code_bundle_sha256,
)
from server.coding_worker.harness_protocol import (
    HarnessBinding,
    HarnessCapabilityState,
    HarnessDescriptor,
    HarnessPersistenceLevel,
    HarnessToolOwnership,
)
from server.coding_worker.service import CodingWorkerService
from server.coding_worker.executor import (
    ExecutorRPCError,
    ExecutorRPCServer,
    ExecutorSidecarClientPool,
    SidecarExecutor,
)
from server.coding_worker.store import CodingWorkerStore
from server.coding_worker.tool_broker import ToolBroker
from server.coding_worker.workspace import WorkspaceBroker


def _request(task_id: str, workspace_id: str) -> ProviderOpenRequest:
    return ProviderOpenRequest(
        task_id=task_id,
        workspace_id=workspace_id,
        objective="Fix and test the project",
        model_route="coding/default",
        policy_profile=PolicyProfile.DEVELOP,
        budget=TaskBudget(),
    )


class _AbortTrackingProvider(FakeCodingAgentProvider):
    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()
        self.cancel_count = 0

    async def message(self, session, text):
        yield ProviderEvent(
            kind=ProviderEventKind.MESSAGE,
            data={"text": "provider turn started"},
        )
        await self.release.wait()
        yield ProviderEvent(kind=ProviderEventKind.TURN_COMPLETED)

    async def cancel(self, session):
        self.cancel_count += 1
        self.release.set()
        return True


class _CloseRequiresQuiescentProvider(FakeCodingAgentProvider):
    def __init__(self) -> None:
        super().__init__()
        self.message_started = asyncio.Event()
        self.release_message = asyncio.Event()
        self.active_messages = 0

    async def message(self, session, text):
        self.active_messages += 1
        self.message_started.set()
        try:
            await self.release_message.wait()
        finally:
            self.active_messages -= 1
        if False:  # pragma: no cover - keeps this an async generator
            yield ProviderEvent(kind=ProviderEventKind.TURN_COMPLETED)

    async def cancel(self, session):
        return True

    async def close(self, session):
        if self.active_messages:
            raise ValueError("provider message stream is still active")
        await super().close(session)


class _TerminalTrackingProvider(FakeCodingAgentProvider):
    def __init__(self) -> None:
        super().__init__()
        self.cancel_count = 0
        self.message_count = 0

    async def message(self, session, text):
        self.message_count += 1
        yield ProviderEvent(kind=ProviderEventKind.TURN_COMPLETED)

    async def cancel(self, session):
        self.cancel_count += 1
        return True


class _ManualProviderStream:
    def __init__(self) -> None:
        self._sent = False
        self._release = asyncio.Event()
        self.close_count = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._sent:
            self._sent = True
            return ProviderEvent(
                kind=ProviderEventKind.MESSAGE,
                data={"text": "provider turn started"},
            )
        await self._release.wait()
        raise StopAsyncIteration

    async def aclose(self) -> None:
        self.close_count += 1
        self._release.set()


class _ManualStreamProvider(FakeCodingAgentProvider):
    def __init__(self) -> None:
        super().__init__()
        self.stream = _ManualProviderStream()
        self.interrupt_count = 0

    def message(self, session, text):
        return self.stream

    async def interrupt_turn(self, session):
        self.interrupt_count += 1
        return True


class _NoShellProvider(FakeCodingAgentProvider):
    async def capabilities(self) -> ProviderCapabilities:
        capabilities = await super().capabilities()
        return capabilities.model_copy(
            update={
                "tool_names": tuple(
                    item for item in capabilities.tool_names if item != "run_shell"
                )
            }
        )


class _ConstantSessionProvider(FakeCodingAgentProvider):
    async def open(self, request: ProviderOpenRequest) -> ProviderSession:
        session = ProviderSession(
            session_id="shared-session",
            task_id=request.task_id,
            provider_capabilities=await self.capabilities(),
        )
        self._requests[session.session_id] = request
        return session


class _BoundaryFailureProvider(FakeCodingAgentProvider):
    def __init__(self) -> None:
        super().__init__()
        self.failure_action: str | None = None
        self.failure: Exception | None = None

    def _fail(self, action: str) -> None:
        if self.failure_action == action and self.failure is not None:
            raise self.failure

    async def open(self, request: ProviderOpenRequest) -> ProviderSession:
        self._fail("open")
        return await super().open(request)

    async def restore(self, request, checkpoint):
        self._fail("restore")
        return await super().restore(request, checkpoint)

    async def message(self, session, text):
        self._fail("message")
        async for event in super().message(session, text):
            yield event

    async def checkpoint(self, session):
        self._fail("checkpoint")
        return await super().checkpoint(session)

    async def interrupt_turn(self, session):
        self._fail("interrupt_turn")
        return await super().interrupt_turn(session)

    async def close(self, session):
        self._fail("close")
        await super().close(session)


class _DelayedSecondTurnProvider(FakeCodingAgentProvider):
    def __init__(self) -> None:
        super().__init__()
        self.turn_count = 0
        self.first_stream_closed = asyncio.Event()
        self.second_turn_started = asyncio.Event()
        self.release_second_turn = asyncio.Event()

    async def message(self, session, text):
        self.turn_count += 1
        if self.turn_count == 1:
            try:
                yield ProviderEvent(kind=ProviderEventKind.TURN_COMPLETED)
            finally:
                self.first_stream_closed.set()
            return
        self.second_turn_started.set()
        await self.release_second_turn.wait()
        yield ProviderEvent(
            kind=ProviderEventKind.MESSAGE,
            data={"text": "second turn remained active"},
        )
        yield ProviderEvent(kind=ProviderEventKind.TURN_COMPLETED)


def _harness_descriptor() -> HarnessDescriptor:
    return HarnessDescriptor(
        protocol_id="modelmirror-provider-v4",
        protocol_version="4",
        implementation_version="fake-1",
        schema_sha256="d" * 64,
        tool_ownership=HarnessToolOwnership.BROKER_ONLY,
        persistence=HarnessPersistenceLevel.SESSION_RESUME,
        capabilities={
            "checkpoint": HarnessCapabilityState(supported=True, available=True)
        },
    )


def _harness_binding(task_id: str) -> HarnessBinding:
    return HarnessBinding(
        task_id=task_id,
        route_id="coding/default",
        slot_id="slot-a",
        binding_sha256="b" * 64,
        driver_generation=1,
        descriptor=_harness_descriptor(),
    )


@pytest.mark.asyncio
async def test_v20_driver_replaces_supplier_tool_ids_before_public_events() -> None:
    raw_operation_id = "toolu_supplier_private_1"
    provider = FakeCodingAgentProvider(
        script=(
            ProviderEvent(
                kind=ProviderEventKind.TOOL_STARTED,
                data={
                    "operation_id": raw_operation_id,
                    "tool_name": "run_shell",
                    "summary": "Tool execution started.",
                },
            ),
            ProviderEvent(
                kind=ProviderEventKind.TOOL_COMPLETED,
                data={
                    "operation_id": raw_operation_id,
                    "tool_name": "run_shell",
                    "summary": "Tool execution completed.",
                    "success": True,
                    "artifact_id": None,
                },
            ),
            ProviderEvent(kind=ProviderEventKind.TURN_COMPLETED),
        )
    )
    task_id = "task_private_tool_id"
    driver = LegacyHarnessDriver(provider)
    request = HarnessOpenRequest.model_validate(
        _request(task_id, "workspace_private_tool_id").model_dump(mode="json")
    )
    session = await driver.open(request, binding=_harness_binding(task_id))

    events = [
        event
        async for event in driver.message(
            session, "continue", turn_id="turn_private_tool_id"
        )
    ]

    public_ids = [
        str(event.data["operation_id"])
        for event in events
        if event.kind in {
            HarnessEventKind.TOOL_STARTED,
            HarnessEventKind.TOOL_COMPLETED,
        }
    ]
    assert len(public_ids) == 2
    assert public_ids[0] == public_ids[1]
    assert public_ids[0].startswith("harness_call_")
    assert raw_operation_id not in repr(events)
    await driver.close(session)


@pytest.mark.asyncio
async def test_stale_stream_cleanup_cannot_interrupt_the_next_harness_turn() -> None:
    task_id = "task_turn_cleanup"
    provider = _DelayedSecondTurnProvider()
    driver = LegacyHarnessDriver(provider)
    request = HarnessOpenRequest.model_validate(
        _request(task_id, "workspace_turn_cleanup").model_dump(mode="json")
    )
    session = await driver.open(request, binding=_harness_binding(task_id))

    first_stream = driver.message(session, "first", turn_id="turn_first")
    first_terminal = await anext(first_stream)
    assert first_terminal.kind is HarnessEventKind.TURN_COMPLETED

    second_stream = driver.message(session, "second", turn_id="turn_second")
    second_event = asyncio.create_task(anext(second_stream))
    await asyncio.wait_for(provider.second_turn_started.wait(), timeout=1)

    # Reproduce the service boundary: the first generator is finalized only
    # after the successor has already registered its turn.
    await first_stream.aclose()
    assert provider.first_stream_closed.is_set()
    provider.release_second_turn.set()

    assert (await asyncio.wait_for(second_event, timeout=1)).data == {
        "text": "second turn remained active"
    }
    assert (await anext(second_stream)).kind is HarnessEventKind.TURN_COMPLETED
    await second_stream.aclose()
    await driver.close(session)


@pytest.mark.asyncio
async def test_harness_interrupt_deterministically_closes_nested_provider_stream() -> None:
    task_id = "task_nested_stream_close"
    provider = _ManualStreamProvider()
    driver = LegacyHarnessDriver(provider)
    request = HarnessOpenRequest.model_validate(
        _request(task_id, "workspace_nested_stream_close").model_dump(mode="json")
    )
    session = await driver.open(request, binding=_harness_binding(task_id))
    stream = driver.message(session, "continue", turn_id="turn_nested_stream_close")

    assert (await anext(stream)).kind is HarnessEventKind.MESSAGE
    await stream.aclose()
    assert provider.stream.close_count == 0

    assert await driver.interrupt_turn(session) is True
    assert provider.interrupt_count == 1
    assert provider.stream.close_count == 1

    await driver.close(session)
    assert provider.stream.close_count == 1


async def _invoke_driver_boundary(
    provider: _BoundaryFailureProvider,
    action: str,
    failure: Exception,
    *,
    v20: bool,
) -> None:
    task_id = f"task_{action}"
    request = HarnessOpenRequest.model_validate(
        _request(task_id, f"workspace_{action}").model_dump(mode="json")
    )
    driver = LegacyHarnessDriver(provider)
    binding = _harness_binding(task_id) if v20 else None
    if action == "open":
        provider.failure_action = action
        provider.failure = failure
        await driver.open(request, binding=binding)
        return

    session = await driver.open(request, binding=binding)
    checkpoint = await driver.checkpoint(session) if action == "restore" else None
    provider.failure_action = action
    provider.failure = failure
    if action == "restore":
        assert checkpoint is not None
        await driver.restore(request, checkpoint, binding=binding)
    elif action == "message":
        await anext(driver.message(session, "continue", turn_id="turn_test"))
    elif action == "checkpoint":
        await driver.checkpoint(session)
    elif action == "interrupt_turn":
        await driver.interrupt_turn(session)
    elif action == "close":
        await driver.close(session)
    else:  # pragma: no cover - test helper invariant
        raise AssertionError(f"unsupported action: {action}")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action", ("open", "restore", "message", "checkpoint", "interrupt_turn", "close")
)
@pytest.mark.parametrize(
    ("failure", "expected_code"),
    (
        (ConnectionResetError("reset"), "harness_transport_unavailable"),
        (ValueError("bad provider frame"), "harness_protocol_invalid"),
        (RuntimeError("driver bug"), "harness_driver_internal"),
    ),
)
async def test_v20_driver_lifecycle_classifies_boundary_failures(
    action: str, failure: Exception, expected_code: str
) -> None:
    provider = _BoundaryFailureProvider()
    with pytest.raises(CodingSubstrateError) as rejected:
        await _invoke_driver_boundary(provider, action, failure, v20=True)
    assert rejected.value.code == expected_code


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action", ("open", "restore", "message", "checkpoint", "interrupt_turn", "close")
)
async def test_legacy_driver_lifecycle_preserves_provider_reason(action: str) -> None:
    provider = _BoundaryFailureProvider()
    failure = ProviderRPCError("legacy failure", code="provider_failed")
    with pytest.raises(ProviderRPCError) as rejected:
        await _invoke_driver_boundary(provider, action, failure, v20=False)
    assert rejected.value is failure
    assert rejected.value.code == "provider_failed"


@pytest.mark.asyncio
async def test_provider_rpc_response_frames_distinguish_transport_and_protocol() -> None:
    reader = asyncio.StreamReader(limit=MAX_PROVIDER_RPC_BYTES)
    reader.feed_eof()
    with pytest.raises(ProviderRPCError) as eof:
        await ProviderSidecarClientPool._read(reader)
    assert eof.value.code == "provider_transport_unavailable"

    reader = asyncio.StreamReader(limit=MAX_PROVIDER_RPC_BYTES)
    reader.feed_data(b"{invalid-json}\n")
    with pytest.raises(ProviderRPCError) as malformed:
        await ProviderSidecarClientPool._read(reader)
    assert malformed.value.code == "provider_invalid_response"

    reader = asyncio.StreamReader(limit=MAX_PROVIDER_RPC_BYTES)
    reader.feed_data(b"x" * (MAX_PROVIDER_RPC_BYTES + 1) + b"\n")
    with pytest.raises(ProviderRPCError) as oversize:
        await ProviderSidecarClientPool._read(reader)
    assert oversize.value.code == "provider_response_too_large"


@pytest.mark.asyncio
async def test_provider_rpc_first_frame_connection_reset_is_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = CodingWorkerStore(tmp_path / "control", master_key=Fernet.generate_key())
    workspace = WorkspaceBroker(tmp_path / "workspace", {}, id_key=b"r" * 32)
    broker_rpc = BrokerRPCServer(ToolBroker(store=store, workspace_broker=workspace))
    pool = ProviderSidecarClientPool(
        endpoints={"slot-a": "tcp:127.0.0.1:1"},
        tokens={"slot-a": "r" * 48},
        workspace_slot_resolver=lambda _workspace_id: "slot-a",
        broker_rpc=broker_rpc,
    )

    class _ResetReader:
        async def readline(self) -> bytes:
            raise ConnectionResetError("first frame reset")

    class _Writer:
        def write(self, _data: bytes) -> None:
            return None

        async def drain(self) -> None:
            return None

        def close(self) -> None:
            return None

        async def wait_closed(self) -> None:
            return None

    async def reset_connection(_slot_id: str):
        return _ResetReader(), _Writer()

    monkeypatch.setattr(pool, "_connect", reset_connection)
    with pytest.raises(ProviderRPCError) as reset:
        await pool._call("slot-a", "capabilities", {})
    assert reset.value.code == "provider_transport_unavailable"


def test_checkpoint_failure_preserves_v20_driver_attribution() -> None:
    failure = CodingSubstrateError(
        "checkpoint transport failed",
        code="harness_transport_unavailable",
        status=503,
    )
    assert CodingWorkerService._checkpoint_failure_reason(
        failure,
        fallback="checkpoint_failed",
        v20=True,
    ) == "harness_transport_unavailable"
    assert CodingWorkerService._checkpoint_failure_reason(
        failure,
        fallback="checkpoint_failed",
        v20=False,
    ) == "checkpoint_failed"


def test_harness_v3_maps_driver_internal_failure_to_harness_stage() -> None:
    from scripts.coding_worker_harbor_agent import ModelMirrorWorkerAgent

    assert ModelMirrorWorkerAgent._failure_stage("harness_driver_internal") == "harness"


@pytest.mark.asyncio
async def test_capabilities_for_slots_probes_only_requested_slots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = CodingWorkerStore(tmp_path / "control", master_key=Fernet.generate_key())
    workspace = WorkspaceBroker(tmp_path / "workspace", {}, id_key=b"c" * 32)
    pool = ProviderSidecarClientPool(
        endpoints={
            "slot-a": "tcp:127.0.0.1:1",
            "slot-b": "tcp:127.0.0.1:2",
        },
        tokens={"slot-a": "a" * 48, "slot-b": "b" * 48},
        workspace_slot_resolver=lambda _workspace_id: "slot-a",
        broker_rpc=BrokerRPCServer(
            ToolBroker(store=store, workspace_broker=workspace)
        ),
    )
    calls: list[str] = []

    async def record(slot_id: str, _action: str, _payload: dict[str, object]):
        calls.append(slot_id)
        return (await FakeCodingAgentProvider().capabilities()).model_dump(mode="json")

    monkeypatch.setattr(pool, "_call", record)
    result = await pool.capabilities_for_slots(("slot-a",))

    assert result["slot-a"] is not None
    assert calls == ["slot-a"]


@pytest.mark.asyncio
async def test_provider_descriptor_is_sidecar_generation_bound_and_fail_closed(
    tmp_path: Path,
) -> None:
    store = CodingWorkerStore(
        tmp_path / "control", master_key=Fernet.generate_key()
    )
    workspace = WorkspaceBroker(tmp_path / "workspace", {}, id_key=b"d" * 32)
    broker_rpc = BrokerRPCServer(ToolBroker(store=store, workspace_broker=workspace))
    await broker_rpc.start_tcp_for_tests()
    available = ProviderRPCServer(
        FakeCodingAgentProvider(),
        token="a" * 48,
        harness_descriptor=_harness_descriptor(),
    )
    unavailable = ProviderRPCServer(FakeCodingAgentProvider(), token="b" * 48)
    endpoints = {
        "slot-a": await available.start_tcp_for_tests(),
        "slot-b": await unavailable.start_tcp_for_tests(),
    }
    pool = ProviderSidecarClientPool(
        endpoints=endpoints,
        tokens={"slot-a": "a" * 48, "slot-b": "b" * 48},
        workspace_slot_resolver=lambda _workspace_id: "slot-a",
        broker_rpc=broker_rpc,
    )

    observations = await pool.harness_descriptors_for_slots(
        ("slot-a", "slot-b", "missing")
    )
    observed = observations["slot-a"]
    assert observed is not None
    assert observed.descriptor == _harness_descriptor()
    assert len(observed.sidecar_generation) == 32
    assert observations["slot-b"] is None
    assert observations["missing"] is None

    await available.close()
    await unavailable.close()
    await broker_rpc.close()


@pytest.mark.asyncio
async def test_provider_attestation_uses_sidecar_environment_and_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = ProviderRPCServer(FakeCodingAgentProvider(), token="a" * 48)
    request = ProviderRPCRequest(
        token="a" * 48,
        action="harness_attestation",
    )
    with pytest.raises(ProviderRPCError) as disabled:
        await server._dispatch(request)
    assert disabled.value.code == "harness_attestation_disabled"

    monkeypatch.setenv("CODING_WORKER_HARNESS_V3_ENABLED", "true")
    with pytest.raises(ProviderRPCError) as unavailable:
        await server._dispatch(request)
    assert unavailable.value.code == "harness_attestation_unavailable"

    server = ProviderRPCServer(
        FakeCodingAgentProvider(),
        token="a" * 48,
        harness_identity=(
            "coding/default",
            "openrouter/example-model",
            "opencode-1.18.9",
        ),
    )
    result = await server._dispatch(request)
    assert result == {
        "route_id": "coding/default",
        "model_identity_sha256": hashlib.sha256(
            b"openrouter/example-model"
        ).hexdigest(),
        "engine": "opencode-1.18.9",
        "sidecar_generation": result["sidecar_generation"],
        "code_bundle_sha256": harness_code_bundle_sha256(
            Path(__file__).parents[1] / "coding_worker",
            PROVIDER_HARNESS_CODE_FILES,
        ),
    }
    assert len(result["sidecar_generation"]) == 32


@pytest.mark.asyncio
async def test_provider_pool_observes_slots_independently_and_fails_closed(
    tmp_path: Path,
) -> None:
    store = CodingWorkerStore(
        tmp_path / "control", master_key=Fernet.generate_key()
    )
    workspace = WorkspaceBroker(tmp_path / "workspace", {}, id_key=b"c" * 32)
    broker_rpc = BrokerRPCServer(ToolBroker(store=store, workspace_broker=workspace))
    await broker_rpc.start_tcp_for_tests()
    first = ProviderRPCServer(FakeCodingAgentProvider(), token="a" * 48)
    second = ProviderRPCServer(_NoShellProvider(), token="b" * 48)
    first_endpoint = await first.start_tcp_for_tests()
    second_endpoint = await second.start_tcp_for_tests()
    pool = ProviderSidecarClientPool(
        endpoints={"slot-a": first_endpoint, "slot-b": second_endpoint},
        tokens={"slot-a": "a" * 48, "slot-b": "b" * 48},
        workspace_slot_resolver=lambda _workspace_id: "slot-a",
        broker_rpc=broker_rpc,
    )

    observations = await pool.slot_capabilities()
    first_capabilities = observations["slot-a"]
    second_capabilities = observations["slot-b"]
    assert first_capabilities is not None
    assert second_capabilities is not None
    assert "run_shell" in first_capabilities.tool_names
    assert "run_shell" not in second_capabilities.tool_names
    with pytest.raises(ProviderRPCError) as mismatch:
        await pool.capabilities()
    assert mismatch.value.code == "provider_capability_mismatch"

    await first.close()
    await second.close()
    await broker_rpc.close()


@pytest.mark.asyncio
async def test_provider_sidecar_pool_streams_neutral_events_and_revokes_broker_token(
    tmp_path: Path,
) -> None:
    store = CodingWorkerStore(tmp_path / "control", master_key=Fernet.generate_key())
    workspace = WorkspaceBroker(tmp_path / "workspace", {}, id_key=b"w" * 32)
    broker_rpc = BrokerRPCServer(ToolBroker(store=store, workspace_broker=workspace))
    await broker_rpc.start_tcp_for_tests()
    server = ProviderRPCServer(FakeCodingAgentProvider(), token="s" * 48)
    endpoint = await server.start_tcp_for_tests()
    pool = ProviderSidecarClientPool(
        endpoints={"slot-a": endpoint},
        tokens={"slot-a": "s" * 48},
        workspace_slot_resolver=lambda _workspace_id: "slot-a",
        broker_rpc=broker_rpc,
    )
    # Broker tokens bind to persisted tasks, just as the production service does.
    from server.tests.test_coding_worker_service import _request as task_request
    from server.coding_worker.contracts import Origin, TaskSpec

    task_id = store.create_task(
        TaskSpec(**task_request("rpc-task").model_dump(), origin=Origin(module="test", object_id="rpc"))
    ).task_id
    session = await pool.open(_request(task_id, "workspace_fake"))
    events = [event async for event in pool.message(session, "continue")]
    assert [event.kind for event in events] == [
        ProviderEventKind.PLAN,
        ProviderEventKind.TURN_COMPLETED,
    ]
    checkpoint = await pool.checkpoint(session)
    assert checkpoint.payload["fake_session"] == session.session_id
    assert task_id in broker_rpc._tokens
    await pool.close(session)
    assert task_id not in broker_rpc._tokens
    await server.close()
    await broker_rpc.close()


@pytest.mark.asyncio
async def test_provider_pool_scopes_equal_private_session_ids_by_task(
    tmp_path: Path,
) -> None:
    store = CodingWorkerStore(tmp_path / "control", master_key=Fernet.generate_key())
    workspace = WorkspaceBroker(tmp_path / "workspace", {}, id_key=b"k" * 32)
    broker_rpc = BrokerRPCServer(ToolBroker(store=store, workspace_broker=workspace))
    await broker_rpc.start_tcp_for_tests()
    first = ProviderRPCServer(_ConstantSessionProvider(), token="a" * 48)
    second = ProviderRPCServer(_ConstantSessionProvider(), token="b" * 48)
    endpoints = {
        "slot-a": await first.start_tcp_for_tests(),
        "slot-b": await second.start_tcp_for_tests(),
    }
    pool = ProviderSidecarClientPool(
        endpoints=endpoints,
        tokens={"slot-a": "a" * 48, "slot-b": "b" * 48},
        workspace_slot_resolver=lambda workspace_id: (
            "slot-a" if workspace_id == "workspace-a" else "slot-b"
        ),
        broker_rpc=broker_rpc,
    )
    from server.tests.test_coding_worker_service import _request as task_request
    from server.coding_worker.contracts import Origin, TaskSpec

    task_ids = [
        store.create_task(
            TaskSpec(
                **task_request(f"session-scope-{index}").model_dump(),
                origin=Origin(module="test", object_id=f"session-scope-{index}"),
            )
        ).task_id
        for index in range(2)
    ]
    first_session = await pool.open(_request(task_ids[0], "workspace-a"))
    second_session = await pool.open(_request(task_ids[1], "workspace-b"))

    assert first_session.session_id == second_session.session_id
    assert (await pool.checkpoint(first_session)).compatibility.task_id == task_ids[0]
    assert (await pool.checkpoint(second_session)).compatibility.task_id == task_ids[1]

    await pool.close(first_session)
    await pool.close(second_session)
    await first.close()
    await second.close()
    await broker_rpc.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_interrupt", (False, True))
async def test_closing_provider_stream_aborts_the_unfinished_sidecar_turn(
    tmp_path: Path, explicit_interrupt: bool,
) -> None:
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    unhandled: list[dict[str, object]] = []
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
    store = CodingWorkerStore(tmp_path / "control", master_key=Fernet.generate_key())
    workspace = WorkspaceBroker(tmp_path / "workspace", {}, id_key=b"z" * 32)
    broker_rpc = BrokerRPCServer(ToolBroker(store=store, workspace_broker=workspace))
    await broker_rpc.start_tcp_for_tests()
    provider = _AbortTrackingProvider()
    server = ProviderRPCServer(provider, token="z" * 48)
    endpoint = await server.start_tcp_for_tests()
    pool = ProviderSidecarClientPool(
        endpoints={"slot-a": endpoint},
        tokens={"slot-a": "z" * 48},
        workspace_slot_resolver=lambda _workspace_id: "slot-a",
        broker_rpc=broker_rpc,
    )
    from server.tests.test_coding_worker_service import _request as task_request
    from server.coding_worker.contracts import Origin, TaskSpec

    task_id = store.create_task(
        TaskSpec(
            **task_request("rpc-abort").model_dump(),
            origin=Origin(module="test", object_id="rpc-abort"),
        )
    ).task_id
    session = await pool.open(_request(task_id, "workspace_abort"))
    stream = pool.message(session, "continue")
    first = await anext(stream)
    assert first.kind is ProviderEventKind.MESSAGE

    if explicit_interrupt:
        assert await pool.interrupt_turn(session) is True
    await stream.aclose()
    for _ in range(100):
        if provider.cancel_count == 1:
            break
        await asyncio.sleep(0.01)
    assert provider.cancel_count == 1

    await pool.close(session)
    await server.close()
    await broker_rpc.close()
    loop.set_exception_handler(previous_handler)
    assert unhandled == []


@pytest.mark.asyncio
async def test_closing_session_quiesces_sidecar_stream_before_reusing_slot(
    tmp_path: Path,
) -> None:
    store = CodingWorkerStore(tmp_path / "control", master_key=Fernet.generate_key())
    workspace = WorkspaceBroker(tmp_path / "workspace", {}, id_key=b"q" * 32)
    broker_rpc = BrokerRPCServer(ToolBroker(store=store, workspace_broker=workspace))
    await broker_rpc.start_tcp_for_tests()
    provider = _CloseRequiresQuiescentProvider()
    server = ProviderRPCServer(provider, token="q" * 48)
    endpoint = await server.start_tcp_for_tests()
    pool = ProviderSidecarClientPool(
        endpoints={"slot-a": endpoint},
        tokens={"slot-a": "q" * 48},
        workspace_slot_resolver=lambda _workspace_id: "slot-a",
        broker_rpc=broker_rpc,
    )
    from server.tests.test_coding_worker_service import _request as task_request
    from server.coding_worker.contracts import Origin, TaskSpec

    task_ids = [
        store.create_task(
            TaskSpec(
                **task_request(f"rpc-quiesce-{index}").model_dump(),
                origin=Origin(module="test", object_id=f"rpc-quiesce-{index}"),
            )
        ).task_id
        for index in range(2)
    ]
    first = await pool.open(_request(task_ids[0], "workspace_one"))
    stream = pool.message(first, "continue")
    pending = asyncio.create_task(anext(stream))
    await asyncio.wait_for(provider.message_started.wait(), timeout=1)

    assert await pool.cancel(first) is True
    pending.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await pending
    await stream.aclose()
    await pool.close(first)

    second = await pool.open(_request(task_ids[1], "workspace_two"))
    await pool.close(second)
    await server.close()
    await broker_rpc.close()

    assert provider.active_messages == 0


@pytest.mark.asyncio
async def test_closing_provider_stream_after_terminal_does_not_abort_next_turn(
    tmp_path: Path,
) -> None:
    store = CodingWorkerStore(tmp_path / "control", master_key=Fernet.generate_key())
    workspace = WorkspaceBroker(tmp_path / "workspace", {}, id_key=b"t" * 32)
    broker_rpc = BrokerRPCServer(ToolBroker(store=store, workspace_broker=workspace))
    await broker_rpc.start_tcp_for_tests()
    provider = _TerminalTrackingProvider()
    server = ProviderRPCServer(provider, token="t" * 48)
    endpoint = await server.start_tcp_for_tests()
    pool = ProviderSidecarClientPool(
        endpoints={"slot-a": endpoint},
        tokens={"slot-a": "t" * 48},
        workspace_slot_resolver=lambda _workspace_id: "slot-a",
        broker_rpc=broker_rpc,
    )
    from server.tests.test_coding_worker_service import _request as task_request
    from server.coding_worker.contracts import Origin, TaskSpec

    task_id = store.create_task(
        TaskSpec(
            **task_request("rpc-terminal").model_dump(),
            origin=Origin(module="test", object_id="rpc-terminal"),
        )
    ).task_id
    session = await pool.open(_request(task_id, "workspace_terminal"))

    first = pool.message(session, "first")
    assert (await anext(first)).kind is ProviderEventKind.TURN_COMPLETED
    await first.aclose()
    second = [event async for event in pool.message(session, "second")]

    assert [event.kind for event in second] == [ProviderEventKind.TURN_COMPLETED]
    assert provider.message_count == 2
    assert provider.cancel_count == 0

    await pool.close(session)
    await server.close()
    await broker_rpc.close()


@pytest.mark.asyncio
async def test_provider_sidecar_rejects_wrong_token_and_second_active_task(
    tmp_path: Path,
) -> None:
    store = CodingWorkerStore(tmp_path / "control", master_key=Fernet.generate_key())
    workspace = WorkspaceBroker(tmp_path / "workspace", {}, id_key=b"x" * 32)
    broker_rpc = BrokerRPCServer(ToolBroker(store=store, workspace_broker=workspace))
    await broker_rpc.start_tcp_for_tests()
    server = ProviderRPCServer(FakeCodingAgentProvider(), token="a" * 48)
    endpoint = await server.start_tcp_for_tests()
    bad = ProviderSidecarClientPool(
        endpoints={"slot-a": endpoint},
        tokens={"slot-a": "b" * 48},
        workspace_slot_resolver=lambda _workspace_id: "slot-a",
        broker_rpc=broker_rpc,
    )
    with pytest.raises(ProviderRPCError) as unauthorized:
        await bad.capabilities()
    assert unauthorized.value.code == "provider_unauthorized"

    from server.tests.test_coding_worker_service import _request as task_request
    from server.coding_worker.contracts import Origin, TaskSpec

    task_ids = [
        store.create_task(
            TaskSpec(
                **task_request(f"rpc-busy-{index}").model_dump(),
                origin=Origin(module="test", object_id=f"rpc-{index}"),
            )
        ).task_id
        for index in range(2)
    ]
    pool = ProviderSidecarClientPool(
        endpoints={"slot-a": endpoint},
        tokens={"slot-a": "a" * 48},
        workspace_slot_resolver=lambda _workspace_id: "slot-a",
        broker_rpc=broker_rpc,
    )
    first = await pool.open(_request(task_ids[0], "workspace_one"))
    with pytest.raises(ProviderRPCError) as busy:
        await pool.open(_request(task_ids[1], "workspace_two"))
    assert busy.value.code == "provider_slot_busy"
    await pool.close(first)
    await server.close()
    await broker_rpc.close()


@pytest.mark.asyncio
async def test_new_server_controller_reclaims_stale_provider_and_executor_bindings(
    tmp_path: Path,
) -> None:
    store = CodingWorkerStore(tmp_path / "control", master_key=Fernet.generate_key())
    workspace = WorkspaceBroker(tmp_path / "workspace", {}, id_key=b"r" * 32)
    broker_rpc = BrokerRPCServer(ToolBroker(store=store, workspace_broker=workspace))
    await broker_rpc.start_tcp_for_tests()
    provider = FakeCodingAgentProvider()
    provider_server = ProviderRPCServer(provider, token="p" * 48)
    provider_endpoint = await provider_server.start_tcp_for_tests()
    from server.tests.test_coding_worker_service import _request as task_request
    from server.coding_worker.contracts import Origin, TaskSpec

    task_ids = [
        store.create_task(
            TaskSpec(
                **task_request(f"restart-{index}").model_dump(),
                origin=Origin(module="test", object_id=f"restart-{index}"),
            )
        ).task_id
        for index in range(4)
    ]
    old_generation = store.allocate_controller_generation()
    new_generation = store.allocate_controller_generation()
    assert new_generation == old_generation + 1
    old_provider_pool = ProviderSidecarClientPool(
        endpoints={"slot-a": provider_endpoint},
        tokens={"slot-a": "p" * 48},
        workspace_slot_resolver=lambda _workspace_id: "slot-a",
        broker_rpc=broker_rpc,
        controller_id="controller_old",
        controller_generation=old_generation,
    )
    new_provider_pool = ProviderSidecarClientPool(
        endpoints={"slot-a": provider_endpoint},
        tokens={"slot-a": "p" * 48},
        workspace_slot_resolver=lambda _workspace_id: "slot-a",
        broker_rpc=broker_rpc,
        controller_id="controller_new",
        controller_generation=new_generation,
    )
    old_session = await old_provider_pool.open(
        _request(task_ids[0], "workspace_one")
    )
    new_session = await new_provider_pool.open(
        _request(task_ids[1], "workspace_two")
    )
    assert old_session.session_id in provider._closed
    with pytest.raises(ProviderRPCError) as stale_provider:
        await old_provider_pool.checkpoint(old_session)
    assert stale_provider.value.code == "session_not_found"
    assert (await new_provider_pool.checkpoint(new_session)).payload
    with pytest.raises(ProviderRPCError) as stale_provider_reopen:
        await old_provider_pool.open(_request(task_ids[2], "workspace_three"))
    assert stale_provider_reopen.value.code == "provider_controller_stale"
    assert (await new_provider_pool.checkpoint(new_session)).payload

    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "main.py").write_text("print('rebound')\n", encoding="utf-8")
    executor_server = ExecutorRPCServer(
        SidecarExecutor(lambda _workspace_id: repository, runtime_root=tmp_path / "run"),
        token="e" * 48,
    )
    executor_endpoint = await executor_server.start_tcp_for_tests()
    old_executor_pool = ExecutorSidecarClientPool(
        endpoints={"slot-a": executor_endpoint},
        tokens={"slot-a": "e" * 48},
        workspace_slot_resolver=lambda _workspace_id: "slot-a",
        controller_id="controller_old",
        controller_generation=old_generation,
        auto_rebind=True,
    )
    new_executor_pool = ExecutorSidecarClientPool(
        endpoints={"slot-a": executor_endpoint},
        tokens={"slot-a": "e" * 48},
        workspace_slot_resolver=lambda _workspace_id: "slot-a",
        controller_id="controller_new",
        controller_generation=new_generation,
    )
    await old_executor_pool.bind_task("task_one", "workspace_one")
    running = asyncio.create_task(
        old_executor_pool.run_process(
            task_id="task_one",
            workspace_id="workspace_one",
            argv=("python", "-c", "import time; time.sleep(30)"),
            timeout_seconds=60,
            isolated=True,
        )
    )
    for _ in range(200):
        if executor_server.executor._processes.get("task_one"):
            break
        await asyncio.sleep(0.01)
    assert executor_server.executor._processes.get("task_one")
    await new_executor_pool.bind_task("task_two", "workspace_two")
    with pytest.raises(ExecutorRPCError) as stopped:
        await asyncio.wait_for(running, timeout=5)
    assert stopped.value.code == "executor_controller_stale"
    with pytest.raises(ExecutorRPCError) as stale_executor:
        await old_executor_pool.run_process(
            task_id="task_one",
            workspace_id="workspace_one",
            argv=("python", "main.py"),
            timeout_seconds=10,
            isolated=False,
        )
    assert stale_executor.value.code == "executor_controller_stale"
    result = await new_executor_pool.run_process(
        task_id="task_two",
        workspace_id="workspace_two",
        argv=("python", "main.py"),
        timeout_seconds=10,
        isolated=False,
    )
    assert result["exit_code"] == 0

    await new_provider_pool.close(new_session)
    await new_executor_pool.close_task("task_two", "workspace_two")
    with pytest.raises(ProviderRPCError) as stale_provider_after_close:
        await old_provider_pool.open(_request(task_ids[3], "workspace_four"))
    assert stale_provider_after_close.value.code == "provider_controller_stale"
    with pytest.raises(ExecutorRPCError) as stale_executor_after_close:
        await old_executor_pool.bind_task("task_three", "workspace_three")
    assert stale_executor_after_close.value.code == "executor_controller_stale"
    await provider_server.close()
    await executor_server.close()
    await broker_rpc.close()


@pytest.mark.asyncio
async def test_credential_free_executor_requires_exact_task_workspace_binding(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "slots" / "workspaces" / "workspace_one" / "repo"
    repository.mkdir(parents=True)
    (repository / "main.py").write_text("print('isolated')\n")
    executor = SidecarExecutor(
        lambda workspace_id: repository if workspace_id == "workspace_one" else tmp_path / "missing",
        runtime_root=tmp_path / "runtime",
    )
    server = ExecutorRPCServer(executor, token="e" * 48)
    endpoint = await server.start_tcp_for_tests()
    pool = ExecutorSidecarClientPool(
        endpoints={"slot-a": endpoint},
        tokens={"slot-a": "e" * 48},
        workspace_slot_resolver=lambda _workspace_id: "slot-a",
    )
    with pytest.raises(ExecutorRPCError) as unbound:
        await pool.run_process(
            task_id="task_one",
            workspace_id="workspace_one",
            argv=("python", "main.py"),
            timeout_seconds=10,
            isolated=False,
        )
    assert unbound.value.code == "executor_binding_invalid"
    await pool.bind_task("task_one", "workspace_one")
    result = await pool.run_process(
        task_id="task_one",
        workspace_id="workspace_one",
        argv=("python", "main.py"),
        timeout_seconds=10,
        isolated=False,
    )
    assert result["exit_code"] == 0 and str(result["output"]).splitlines() == [
        "isolated"
    ]
    with pytest.raises(ExecutorRPCError) as foreign:
        await pool.run_process(
            task_id="task_two",
            workspace_id="workspace_one",
            argv=("python", "main.py"),
            timeout_seconds=10,
            isolated=False,
        )
    assert foreign.value.code == "executor_binding_invalid"
    await pool.close_task("task_one", "workspace_one")
    await server.close()


@pytest.mark.asyncio
async def test_runtime_executor_pool_rebinds_exact_task_after_executor_restart(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "slots" / "workspaces" / "workspace_one" / "repo"
    repository.mkdir(parents=True)
    (repository / "main.py").write_text("print('isolated')\n")
    server = ExecutorRPCServer(
        SidecarExecutor(
            lambda workspace_id: (
                repository if workspace_id == "workspace_one" else tmp_path / "missing"
            ),
            runtime_root=tmp_path / "runtime",
        ),
        token="e" * 48,
    )
    endpoint = await server.start_tcp_for_tests()
    pool = ExecutorSidecarClientPool(
        endpoints={"slot-a": endpoint},
        tokens={"slot-a": "e" * 48},
        workspace_slot_resolver=lambda _workspace_id: "slot-a",
        auto_rebind=True,
    )

    result = await pool.run_process(
        task_id="task_one",
        workspace_id="workspace_one",
        argv=("python", "main.py"),
        timeout_seconds=10,
        isolated=False,
    )
    assert result["exit_code"] == 0
    assert str(result["output"]).splitlines() == ["isolated"]

    with pytest.raises(ExecutorRPCError) as foreign:
        await pool.run_process(
            task_id="task_two",
            workspace_id="workspace_one",
            argv=("python", "main.py"),
            timeout_seconds=10,
            isolated=False,
        )
    assert foreign.value.code == "executor_slot_busy"
    await pool.close_task("task_one", "workspace_one")
    await server.close()


@pytest.mark.asyncio
async def test_provider_pool_binds_and_closes_separate_executor(tmp_path: Path) -> None:
    store = CodingWorkerStore(tmp_path / "control", master_key=Fernet.generate_key())
    workspace = WorkspaceBroker(tmp_path / "workspace", {}, id_key=b"z" * 32)
    broker_rpc = BrokerRPCServer(ToolBroker(store=store, workspace_broker=workspace))
    await broker_rpc.start_tcp_for_tests()
    provider_server = ProviderRPCServer(FakeCodingAgentProvider(), token="p" * 48)
    provider_endpoint = await provider_server.start_tcp_for_tests()
    repository = tmp_path / "repo"
    repository.mkdir()
    executor_server = ExecutorRPCServer(
        SidecarExecutor(lambda _workspace_id: repository, runtime_root=tmp_path / "run"),
        token="x" * 48,
    )
    executor_endpoint = await executor_server.start_tcp_for_tests()
    executor_pool = ExecutorSidecarClientPool(
        endpoints={"slot-a": executor_endpoint},
        tokens={"slot-a": "x" * 48},
        workspace_slot_resolver=lambda _workspace_id: "slot-a",
    )
    pool = ProviderSidecarClientPool(
        endpoints={"slot-a": provider_endpoint},
        tokens={"slot-a": "p" * 48},
        workspace_slot_resolver=lambda _workspace_id: "slot-a",
        broker_rpc=broker_rpc,
        executor_pool=executor_pool,
    )
    from server.tests.test_coding_worker_service import _request as task_request
    from server.coding_worker.contracts import Origin, TaskSpec

    task_id = store.create_task(
        TaskSpec(**task_request("split-slot").model_dump(), origin=Origin(module="test", object_id="split"))
    ).task_id
    session = await pool.open(_request(task_id, "workspace_split"))
    result = await pool.run_process(
        task_id=task_id,
        workspace_id="workspace_split",
        argv=("python", "-c", "print('executor-only')"),
        timeout_seconds=10,
        isolated=False,
    )
    assert result["exit_code"] == 0
    await pool.close(session)
    with pytest.raises(ExecutorRPCError) as closed:
        await executor_pool.run_process(
            task_id=task_id,
            workspace_id="workspace_split",
            argv=("python", "-c", "print('no')"),
            timeout_seconds=10,
            isolated=False,
        )
    assert closed.value.code == "executor_binding_invalid"
    await provider_server.close()
    await executor_server.close()
    await broker_rpc.close()
