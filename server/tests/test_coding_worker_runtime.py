from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from server.coding_worker.contracts import (
    AcceptanceCheck,
    AcceptanceContract,
    Origin,
    RuntimeProtocol,
    TaskCreateRequest,
    TaskSpec,
    TaskState,
    WorkspaceSource,
)
from server.coding_worker.executor import ExecutorSidecarClientPool
from server.coding_worker.harness_protocol import (
    HarnessCapabilityState,
    HarnessDescriptor,
    HarnessPersistenceLevel,
    HarnessToolOwnership,
)
from server.coding_worker.provider import FakeCodingAgentProvider
from server.coding_worker.provider_rpc import ProviderRPCServer
from server.coding_worker.runtime import (
    CodingWorkerRuntime,
    CodingWorkerRuntimeError,
    _load_harness_v3_fixtures,
    _route_slots_from_environment,
    _schedulable_route_slots,
    configured_model_routes_from_environment,
)
from server.coding_worker.store import WorkerConflictError
from server.coding_worker.tool_broker import FrozenCheck, frozen_approval_request_sha256
from server.coding_worker.workspace import InMemoryWorkspaceSourceAdapter


_PROVIDER_TOKENS = {"slot-a": "a" * 48, "slot-b": "b" * 48}
_V17_PREREQUISITE_FLAGS = (
    "CODING_WORKER_V16_ENABLED",
    "CODING_WORKER_INTERACTION_ENABLED",
    "CODING_WORKER_SESSION_CONTROLS_ENABLED",
    "CODING_WORKER_SUBAGENTS_ENABLED",
    "CODING_WORKER_V17_ENABLED",
)
_V20_REQUIRED_CAPABILITIES = {
    name: HarnessCapabilityState(supported=True, available=True)
    for name in (
        "cancel",
        "checkpoint",
        "interrupt",
        "restore",
        "streaming",
        "tool_boundaries",
        "usage",
    )
}


def test_harness_v3_loader_freezes_visible_and_scenario_approvals() -> None:
    bundle = (
        Path(__file__).resolve().parents[2]
        / "benchmarks/coding-worker-v18/fixture-bundle.json"
    )
    _, _, rules = _load_harness_v3_fixtures(bundle)
    key = (
        "builtin",
        "v18-session-restart-command-reconcile",
        "a75b3fdf3e5f4fd76f38c5a0252d0e43d2297c52bdfbc239f930b98f5b0b3d89",
    )
    observed = {rule.request_sha256 for rule in rules[key]}
    assert frozen_approval_request_sha256(
        "run_shell",
        {
            "script": "python -m build_index",
            "cwd": ".",
            "mode": "mutate",
            "timeout_seconds": 120,
        },
    ) in observed
    assert frozen_approval_request_sha256(
        "run_command",
        {
            "argv": [
                "python",
                "-m",
                "unittest",
                "discover",
                "-s",
                "visible_tests",
                "-v",
            ],
            "timeout_seconds": 180,
        },
    ) in observed


def test_harness_v3_loader_rejects_tampered_scenario(tmp_path: Path) -> None:
    benchmark = (
        Path(__file__).resolve().parents[2] / "benchmarks/coding-worker-v18"
    )
    copied = tmp_path / "coding-worker-v18"
    shutil.copytree(benchmark, copied)
    scenario = copied / "tasks/session-restart-command-reconcile/scenario.json"
    scenario.write_text(
        scenario.read_text(encoding="utf-8").replace(
            "python -m build_index", "python -m untrusted"
        ),
        encoding="utf-8",
    )

    with pytest.raises(CodingWorkerRuntimeError) as invalid:
        _load_harness_v3_fixtures(copied / "fixture-bundle.json")
    assert invalid.value.code == "coding_worker_config_invalid"


def _v20_descriptor() -> HarnessDescriptor:
    return HarnessDescriptor(
        protocol_id="modelmirror-provider-v4",
        protocol_version="4",
        implementation_version="fake-runtime-v20",
        schema_sha256="d" * 64,
        tool_ownership=HarnessToolOwnership.BROKER_ONLY,
        persistence=HarnessPersistenceLevel.SESSION_RESUME,
        capabilities=_V20_REQUIRED_CAPABILITIES,
    )


def _enable_v20(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODING_WORKER_HARNESS_V20_ENABLED", "true")
    for name in _V17_PREREQUISITE_FLAGS:
        monkeypatch.setenv(name, "true")


async def _start_provider_servers(
    blockers: dict[str, asyncio.Event],
    *,
    limited_slots: frozenset[str] = frozenset(),
) -> tuple[dict[str, ProviderRPCServer], dict[str, str]]:
    class LimitedFakeProvider(FakeCodingAgentProvider):
        async def capabilities(self):
            value = await super().capabilities()
            return value.model_copy(update={"supports_steering": False})

    servers = {
        slot: ProviderRPCServer(
            (
                LimitedFakeProvider(block=blockers[slot])
                if slot in limited_slots
                else FakeCodingAgentProvider(block=blockers[slot])
            ),
            token=_PROVIDER_TOKENS[slot],
            harness_descriptor=_v20_descriptor(),
        )
        for slot in blockers
    }
    endpoints = {
        slot: await server.start_tcp_for_tests() for slot, server in servers.items()
    }
    return servers, endpoints


def _runtime(
    tmp_path: Path,
    endpoints: dict[str, str],
    *,
    route_slots: dict[str, tuple[str, ...]],
    schedulable_route_slots: dict[str, tuple[str, ...]],
    disabled_model_routes: tuple[str, ...] = (),
    disabled_slot_ids: tuple[str, ...] = (),
    max_active_tasks: int = 2,
) -> CodingWorkerRuntime:
    return CodingWorkerRuntime(
        storage_root=tmp_path / "control",
        slot_roots={
            "slot-a": tmp_path / "slot-a",
            "slot-b": tmp_path / "slot-b",
        },
        source_adapters={
            "manifest": InMemoryWorkspaceSourceAdapter(
                {("source", "h0"): {"main.py": b"print('ok')\n"}}
            )
        },
        frozen_checks={
            "syntax": FrozenCheck(
                check_id="syntax", argv=("python", "-m", "py_compile", "main.py")
            )
        },
        provider_endpoints=endpoints,
        provider_tokens=_PROVIDER_TOKENS,
        broker_socket_path=None,
        sidecar_uid=-1,
        sidecar_gid=-1,
        max_active_tasks=max_active_tasks,
        route_slots=route_slots,
        schedulable_route_slots=schedulable_route_slots,
        new_task_model_routes=tuple(schedulable_route_slots),
        disabled_model_routes=disabled_model_routes,
        disabled_slot_ids=disabled_slot_ids,
        capability_route_slots=schedulable_route_slots,
    )


def _request(client_task_id: str, model_route: str) -> TaskCreateRequest:
    return TaskCreateRequest(
        client_task_id=client_task_id,
        objective="Keep the task running for deterministic route inspection.",
        workspace_source=WorkspaceSource(
            kind="manifest", source_id="source", revision="h0"
        ),
        acceptance=AcceptanceContract(
            contract_id="syntax",
            required_checks=(
                AcceptanceCheck(check_id="syntax", label="syntax", kind="command"),
            ),
        ),
        model_route=model_route,
    )


async def _wait_running(runtime: CodingWorkerRuntime, task_id: str):
    task = await runtime.service.wait_for(
        task_id,
        lambda item: item.state
        not in {TaskState.QUEUED, TaskState.PREPARING},
    )
    assert task.state is TaskState.RUNNING, (task.state, task.reason)
    return task


@pytest.mark.asyncio
async def test_runtime_connects_two_dedicated_provider_slots(tmp_path: Path) -> None:
    blockers = {slot: asyncio.Event() for slot in ("slot-a", "slot-b")}
    servers = {
        slot: ProviderRPCServer(
            FakeCodingAgentProvider(block=blockers[slot]), token=token
        )
        for slot, token in {"slot-a": "a" * 48, "slot-b": "b" * 48}.items()
    }
    endpoints = {
        slot: await server.start_tcp_for_tests() for slot, server in servers.items()
    }
    runtime = CodingWorkerRuntime(
        storage_root=tmp_path / "control",
        slot_roots={
            "slot-a": tmp_path / "slot-a",
            "slot-b": tmp_path / "slot-b",
        },
        source_adapters={
            "manifest": InMemoryWorkspaceSourceAdapter(
                {("source", "h0"): {"main.py": b"print('ok')\n"}}
            )
        },
        frozen_checks={
            "syntax": FrozenCheck(
                check_id="syntax", argv=("python", "-m", "py_compile", "main.py")
            )
        },
        provider_endpoints=endpoints,
        provider_tokens={"slot-a": "a" * 48, "slot-b": "b" * 48},
        broker_socket_path=None,
        sidecar_uid=-1,
        sidecar_gid=-1,
    )
    assert runtime.tool_broker.executor is runtime.execution_backend
    assert runtime.substrate.harness_supervisor is runtime.harness_supervisor
    assert runtime.substrate.harness_driver is runtime.harness_driver
    assert runtime.substrate.execution_backend is runtime.execution_backend
    assert runtime.harness_driver is not runtime.execution_backend
    assert runtime.harness_supervisor is not runtime.harness_driver
    await runtime.start()
    source = WorkspaceSource(kind="manifest", source_id="source", revision="h0")
    tasks = [
        await runtime.service.create_task(
            Origin(module="test", object_id=str(index)),
            TaskCreateRequest(
                client_task_id=f"runtime-{index}",
                objective="inspect",
                workspace_source=source,
                acceptance=AcceptanceContract(
                    contract_id="syntax",
                    required_checks=(
                        AcceptanceCheck(
                            check_id="syntax", label="syntax", kind="command"
                        ),
                    ),
                ),
                model_route="coding/default",
            ),
        )
        for index in range(2)
    ]
    for task in tasks:
        running = await runtime.service.wait_for(
            task.task_id, lambda item: item.state is TaskState.RUNNING
        )
        assert running.workspace_id is not None
    assert {
        runtime.workspace_broker.workspace_slot(
            runtime.store.get_task(task.task_id).workspace_id or ""
        )
        for task in tasks
    } == {"slot-a", "slot-b"}
    for task in tasks:
        await runtime.service.cancel(task.task_id)
    await runtime.close()
    for server in servers.values():
        await server.close()


def test_runtime_routes_v15_tools_to_dedicated_executor_pool(
    tmp_path: Path,
) -> None:
    runtime = CodingWorkerRuntime(
        storage_root=tmp_path / "control",
        slot_roots={"slot-a": tmp_path / "slot-a"},
        source_adapters={},
        frozen_checks={},
        provider_endpoints={"slot-a": "tcp:127.0.0.1:18001"},
        provider_tokens={"slot-a": "p" * 48},
        executor_endpoints={"slot-a": "tcp:127.0.0.1:18002"},
        executor_tokens={"slot-a": "x" * 48},
        broker_socket_path=None,
        sidecar_uid=-1,
        sidecar_gid=-1,
    )

    assert isinstance(runtime.executor_pool, ExecutorSidecarClientPool)
    assert runtime.tool_broker.executor is runtime.execution_backend


def test_route_slot_catalog_is_strict_and_provider_neutral(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "CODING_WORKER_ROUTE_SLOTS_JSON",
        '{"coding/default":["slot-a"],"coding/quality":["slot-b"]}',
    )
    assert _route_slots_from_environment(("slot-a", "slot-b")) == {
        "coding/default": ("slot-a",),
        "coding/quality": ("slot-b",),
    }

    monkeypatch.setenv(
        "CODING_WORKER_ROUTE_SLOTS_JSON",
        '{"coding/quality":["missing-slot"]}',
    )
    with pytest.raises(CodingWorkerRuntimeError) as caught:
        _route_slots_from_environment(("slot-a", "slot-b"))
    assert caught.value.code == "coding_worker_config_invalid"


@pytest.mark.parametrize(
    ("route_slots", "expected"),
    (
        (
            '{"coding/default":["slot-a"],"coding/quality":["slot-b"]}',
            ("coding/default",),
        ),
        (
            '{"coding/default":["slot-b"],"coding/quality":["slot-a"]}',
            ("coding/quality",),
        ),
        (
            '{"coding/default":["slot-a","slot-b"],"coding/quality":["slot-b"]}',
            ("coding/default",),
        ),
    ),
)
def test_disabled_provider_routes_are_derived_from_declared_slots(
    monkeypatch: pytest.MonkeyPatch, route_slots: str, expected: tuple[str, ...]
) -> None:
    monkeypatch.setenv(
        "CODING_WORKER_MODEL_ROUTES", "coding/default,coding/quality"
    )
    monkeypatch.setenv("CODING_WORKER_ROUTE_SLOTS_JSON", route_slots)
    monkeypatch.setenv("CODING_WORKER_CLAUDE_SLOT_IDS", "slot-b")
    monkeypatch.setenv("CODING_WORKER_CLAUDE_ENABLED", "false")
    assert configured_model_routes_from_environment() == expected
    monkeypatch.delenv("CODING_WORKER_ROUTE_SLOTS_JSON")
    assert configured_model_routes_from_environment() == ()
    monkeypatch.setenv("CODING_WORKER_CLAUDE_ENABLED", "true")
    assert configured_model_routes_from_environment() == (
        "coding/default",
        "coding/quality",
    )


@pytest.mark.asyncio
async def test_disabled_claude_only_gates_new_tasks_and_background_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "CODING_WORKER_MODEL_ROUTES", "coding/default,coding/quality"
    )
    monkeypatch.setenv(
        "CODING_WORKER_ROUTE_SLOTS_JSON",
        '{"coding/default":["slot-a"],"coding/quality":["slot-b"]}',
    )
    monkeypatch.setenv("CODING_WORKER_CLAUDE_SLOT_IDS", "slot-b")
    monkeypatch.setenv("CODING_WORKER_CLAUDE_ENABLED", "false")
    route_slots = _route_slots_from_environment(("slot-a", "slot-b"))
    assert route_slots == {
        "coding/default": ("slot-a",),
        "coding/quality": ("slot-b",),
    }
    admitted = configured_model_routes_from_environment()
    capability_routes = {"coding/default": ("slot-a",)}

    blockers = {slot: asyncio.Event() for slot in ("slot-a", "slot-b")}
    servers = {
        slot: ProviderRPCServer(
            FakeCodingAgentProvider(block=blockers[slot]), token=token
        )
        for slot, token in {"slot-a": "a" * 48, "slot-b": "b" * 48}.items()
    }
    endpoints = {
        slot: await server.start_tcp_for_tests() for slot, server in servers.items()
    }

    runtime = CodingWorkerRuntime(
        storage_root=tmp_path / "control",
        slot_roots={
            "slot-a": tmp_path / "slot-a",
            "slot-b": tmp_path / "slot-b",
        },
        source_adapters={
            "manifest": InMemoryWorkspaceSourceAdapter(
                {("source", "h0"): {"main.py": b"print('ok')\n"}}
            )
        },
        frozen_checks={
            "syntax": FrozenCheck(
                check_id="syntax", argv=("python", "-m", "py_compile", "main.py")
            )
        },
        provider_endpoints=endpoints,
        provider_tokens={"slot-a": "a" * 48, "slot-b": "b" * 48},
        broker_socket_path=None,
        sidecar_uid=-1,
        sidecar_gid=-1,
        route_slots=route_slots,
        new_task_model_routes=admitted,
        disabled_model_routes=("coding/quality",),
        capability_route_slots=capability_routes,
    )
    observed: list[tuple[str, ...]] = []
    original_capabilities = runtime.harness_supervisor.capabilities_for_slots
    original_descriptors = runtime.harness_supervisor.harness_descriptors_for_slots

    async def capabilities(slot_ids: tuple[str, ...]) -> object:
        observed.append(tuple(slot_ids))
        return await original_capabilities(slot_ids)

    async def descriptors(slot_ids: tuple[str, ...]) -> object:
        observed.append(tuple(slot_ids))
        return await original_descriptors(slot_ids)

    monkeypatch.setattr(
        runtime.harness_supervisor, "capabilities_for_slots", capabilities
    )
    monkeypatch.setattr(
        runtime.harness_supervisor, "harness_descriptors_for_slots", descriptors
    )
    source = WorkspaceSource(kind="manifest", source_id="source", revision="h0")
    historical_request = TaskCreateRequest(
        client_task_id="historical-quality",
        objective="resume historical quality task",
        workspace_source=source,
        acceptance=AcceptanceContract(
            contract_id="syntax",
            required_checks=(
                AcceptanceCheck(check_id="syntax", label="syntax", kind="command"),
            ),
        ),
        model_route="coding/quality",
    )
    origin = Origin(module="test", object_id="route-gate")
    historical = runtime.store.create_task(
        TaskSpec(**historical_request.model_dump(), origin=origin)
    )

    try:
        retried = await runtime.substrate.control_plane.create_task(
            origin, historical_request
        )
        assert retried.task_id == historical.task_id
        with pytest.raises(WorkerConflictError) as rejected:
            await runtime.substrate.control_plane.create_task(
                origin,
                historical_request.model_copy(
                    update={"client_task_id": "new-quality"}
                ),
            )
        assert rejected.value.code == "model_route_unavailable"

        await runtime.start()
        parked_historical = await runtime.service.wait_for(
            historical.task_id, lambda item: item.state is TaskState.INTERRUPTED
        )
        assert parked_historical.reason == "model_route_disabled"
        assert parked_historical.workspace_id is None

        default_task = await runtime.substrate.control_plane.create_task(
            origin,
            historical_request.model_copy(
                update={
                    "client_task_id": "new-default",
                    "model_route": "coding/default",
                }
            ),
        )
        running_default = await runtime.service.wait_for(
            default_task.task_id, lambda item: item.state is TaskState.RUNNING
        )
        assert running_default.workspace_id is not None
        assert (
            runtime.workspace_broker.workspace_slot(running_default.workspace_id)
            == "slot-a"
        )
        assert observed
        assert all(slot_ids == ("slot-a",) for slot_ids in observed)
        await runtime.service.cancel(default_task.task_id)
    finally:
        await runtime.close()

    monkeypatch.setenv("CODING_WORKER_CLAUDE_ENABLED", "true")
    enabled_runtime = CodingWorkerRuntime(
        storage_root=tmp_path / "control",
        slot_roots={
            "slot-a": tmp_path / "slot-a",
            "slot-b": tmp_path / "slot-b",
        },
        source_adapters={
            "manifest": InMemoryWorkspaceSourceAdapter(
                {("source", "h0"): {"main.py": b"print('ok')\n"}}
            )
        },
        frozen_checks={
            "syntax": FrozenCheck(
                check_id="syntax", argv=("python", "-m", "py_compile", "main.py")
            )
        },
        provider_endpoints=endpoints,
        provider_tokens={"slot-a": "a" * 48, "slot-b": "b" * 48},
        broker_socket_path=None,
        sidecar_uid=-1,
        sidecar_gid=-1,
        route_slots=route_slots,
        new_task_model_routes=("coding/default", "coding/quality"),
        capability_route_slots=route_slots,
    )
    try:
        await enabled_runtime.start()
        resumed = await enabled_runtime.substrate.control_plane.resume(
            historical.task_id
        )
        assert resumed.state is TaskState.QUEUED
        running_historical = await enabled_runtime.service.wait_for(
            historical.task_id, lambda item: item.state is TaskState.RUNNING
        )
        assert running_historical.workspace_id is not None
        assert (
            enabled_runtime.workspace_broker.workspace_slot(
                running_historical.workspace_id
            )
            == "slot-b"
        )
        await enabled_runtime.service.cancel(historical.task_id)
    finally:
        await enabled_runtime.close()
        for server in servers.values():
            await server.close()


def test_schedulable_route_slots_remove_only_disabled_slots() -> None:
    assert _schedulable_route_slots(
        {
            "coding/default": ("slot-a", "slot-b"),
            "coding/quality": ("slot-b",),
        },
        frozenset({"slot-b"}),
    ) == {"coding/default": ("slot-a",)}


@pytest.mark.asyncio
async def test_disabled_slot_never_runs_mixed_route_and_parks_bound_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_v20(monkeypatch)
    blockers = {"slot-a": asyncio.Event(), "slot-b": asyncio.Event()}
    servers, endpoints = await _start_provider_servers(
        blockers, limited_slots=frozenset({"slot-b"})
    )
    full_routes = {
        "coding/occupy": ("slot-a",),
        "coding/mixed": ("slot-a", "slot-b"),
        "coding/quality": ("slot-b",),
    }
    origin = Origin(module="test", object_id="mixed-route")
    enabled = _runtime(
        tmp_path,
        endpoints,
        route_slots=full_routes,
        schedulable_route_slots=full_routes,
    )
    try:
        await enabled.start()
        selected_superset = await enabled.substrate.control_plane.create_task(
            origin, _request("mixed-selected-superset", "coding/mixed")
        )
        selected_superset = await _wait_running(
            enabled, selected_superset.task_id
        )
        assert selected_superset.workspace_id is not None
        assert (
            enabled.workspace_broker.workspace_slot(selected_superset.workspace_id)
            == "slot-a"
        )
        assert selected_superset.task_id in enabled.service._active
        await enabled.service.cancel(selected_superset.task_id)
        assert enabled.harness_driver._sessions == {}
        assert enabled.provider._sessions == {}
        assert servers["slot-a"]._active_task_id is None
        assert servers["slot-a"]._active_session is None
        assert servers["slot-a"]._message_tasks == {}

        occupy = await enabled.substrate.control_plane.create_task(
            origin, _request("occupy-enabled", "coding/occupy")
        )
        await _wait_running(enabled, occupy.task_id)
        historical = await enabled.substrate.control_plane.create_task(
            origin, _request("mixed-history", "coding/mixed")
        )
        historical = await _wait_running(enabled, historical.task_id)
        assert historical.workspace_id is not None
        assert (
            enabled.workspace_broker.workspace_slot(historical.workspace_id)
            == "slot-b"
        )
        snapshot = enabled.store.get_task_capability_snapshot(historical.task_id)
        assert snapshot is not None
        assert snapshot.snapshot["harness_protocol"] == "v20"

    finally:
        await enabled.close()

    enabled.store.transition(historical.task_id, TaskState.QUEUED)
    await servers.pop("slot-b").close()
    active_routes = {
        "coding/occupy": ("slot-a",),
        "coding/mixed": ("slot-a",),
    }
    disabled = _runtime(
        tmp_path,
        endpoints,
        route_slots=full_routes,
        schedulable_route_slots=active_routes,
        disabled_model_routes=("coding/quality",),
        disabled_slot_ids=("slot-b",),
    )
    try:
        await disabled.start()
        historical = await disabled.service.wait_for(
            historical.task_id, lambda item: item.state is TaskState.INTERRUPTED
        )
        assert historical.reason == "model_route_disabled"
        assert historical.workspace_id is not None

        occupy = await disabled.substrate.control_plane.create_task(
            origin, _request("occupy-disabled", "coding/occupy")
        )
        await _wait_running(disabled, occupy.task_id)
        mixed = await disabled.substrate.control_plane.create_task(
            origin, _request("mixed-disabled", "coding/mixed")
        )
        await asyncio.sleep(0.1)
        assert disabled.store.get_task(mixed.task_id).state is TaskState.QUEUED
        assert disabled.store.get_task(mixed.task_id).workspace_id is None

        await disabled.service.cancel(occupy.task_id)
        mixed = await _wait_running(disabled, mixed.task_id)
        assert mixed.workspace_id is not None
        assert disabled.workspace_broker.workspace_slot(mixed.workspace_id) == "slot-a"
        descriptors = disabled.store.get_task_capability_snapshot(
            mixed.task_id
        ).snapshot["harness_descriptors"]
        assert [item["slot_id"] for item in descriptors] == ["slot-a"]
        await disabled.service.cancel(mixed.task_id)
    finally:
        await disabled.close()
        for server in servers.values():
            await server.close()


@pytest.mark.asyncio
async def test_v20_disabled_route_reopens_same_store_on_original_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_v20(monkeypatch)
    monkeypatch.setenv("CODING_WORKER_CLAUDE_ENABLED", "true")
    blockers = {"slot-a": asyncio.Event(), "slot-b": asyncio.Event()}
    servers, endpoints = await _start_provider_servers(blockers)
    full_routes = {
        "coding/default": ("slot-a",),
        "coding/quality": ("slot-b",),
    }
    origin = Origin(module="test", object_id="v20-route-resume")
    request = _request("quality-history-v20", "coding/quality")
    enabled = _runtime(
        tmp_path,
        endpoints,
        route_slots=full_routes,
        schedulable_route_slots=full_routes,
    )
    try:
        await enabled.start()
        task = await enabled.substrate.control_plane.create_task(origin, request)
        task = await _wait_running(enabled, task.task_id)
        assert task.workspace_id is not None
        workspace_id = task.workspace_id
        assert enabled.workspace_broker.workspace_slot(workspace_id) == "slot-b"
        before = enabled.store.get_task_capability_snapshot(task.task_id)
        assert before is not None
        before_generation = before.snapshot["harness_descriptors"][0][
            "observation"
        ]["sidecar_generation"]
    finally:
        await enabled.close()

    old_slot_b = servers.pop("slot-b")
    await old_slot_b.close()
    monkeypatch.setenv("CODING_WORKER_CLAUDE_ENABLED", "false")
    disabled = _runtime(
        tmp_path,
        endpoints,
        route_slots=full_routes,
        schedulable_route_slots={"coding/default": ("slot-a",)},
        disabled_model_routes=("coding/quality",),
        disabled_slot_ids=("slot-b",),
    )
    try:
        await disabled.start()
        assert disabled.store.get_task(task.task_id).state is TaskState.INTERRUPTED
        assert (
            await disabled.substrate.control_plane.create_task(origin, request)
        ).task_id == task.task_id
        with pytest.raises(WorkerConflictError) as new_task:
            await disabled.substrate.control_plane.create_task(
                origin, _request("new-quality-disabled", "coding/quality")
            )
        assert new_task.value.code == "model_route_unavailable"
        with pytest.raises(WorkerConflictError) as disabled_resume:
            await disabled.substrate.control_plane.resume(task.task_id)
        assert disabled_resume.value.code == "harness_v20_route_unavailable"
        assert disabled.store.get_task(task.task_id).state is TaskState.INTERRUPTED
    finally:
        await disabled.close()

    replacement = ProviderRPCServer(
        FakeCodingAgentProvider(block=asyncio.Event()),
        token=_PROVIDER_TOKENS["slot-b"],
        harness_descriptor=_v20_descriptor(),
    )
    endpoints = dict(endpoints)
    endpoints["slot-b"] = await replacement.start_tcp_for_tests()
    servers["slot-b"] = replacement
    monkeypatch.setenv("CODING_WORKER_CLAUDE_ENABLED", "true")
    restored = _runtime(
        tmp_path,
        endpoints,
        route_slots=full_routes,
        schedulable_route_slots=full_routes,
    )
    try:
        await restored.start()
        resumed = await restored.substrate.control_plane.resume(task.task_id)
        assert resumed.state is TaskState.QUEUED
        refreshed = restored.store.get_task_capability_snapshot(task.task_id)
        assert refreshed is not None
        after_generation = refreshed.snapshot["harness_descriptors"][0][
            "observation"
        ]["sidecar_generation"]
        assert after_generation != before_generation
        assert refreshed.binding_sha256 != before.binding_sha256

        running = await _wait_running(restored, task.task_id)
        assert running.workspace_id == workspace_id
        assert restored.workspace_broker.workspace_slot(workspace_id) == "slot-b"
        await restored.service.cancel(task.task_id)
    finally:
        await restored.close()
        for server in servers.values():
            await server.close()


@pytest.mark.asyncio
async def test_unbound_v20_resume_requires_unchanged_frozen_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_v20(monkeypatch)
    blockers = {"slot-a": asyncio.Event(), "slot-b": asyncio.Event()}
    servers, endpoints = await _start_provider_servers(blockers)
    routes = {
        "coding/occupy-a": ("slot-a",),
        "coding/occupy-b": ("slot-b",),
        "coding/mixed": ("slot-a", "slot-b"),
    }
    origin = Origin(module="test", object_id="unbound-v20")
    runtime = _runtime(
        tmp_path,
        endpoints,
        route_slots=routes,
        schedulable_route_slots=routes,
    )
    try:
        await runtime.start()
        occupy_a = await runtime.substrate.control_plane.create_task(
            origin, _request("unbound-occupy-a", "coding/occupy-a")
        )
        occupy_b = await runtime.substrate.control_plane.create_task(
            origin, _request("unbound-occupy-b", "coding/occupy-b")
        )
        await _wait_running(runtime, occupy_a.task_id)
        await _wait_running(runtime, occupy_b.task_id)
        unbound = await runtime.substrate.control_plane.create_task(
            origin, _request("unbound-mixed", "coding/mixed")
        )
        assert unbound.state is TaskState.QUEUED and unbound.workspace_id is None
        assert (await runtime.service.pause(unbound.task_id)).state is TaskState.PAUSED
        assert (await runtime.service.resume(unbound.task_id)).state is TaskState.QUEUED
        assert (await runtime.service.pause(unbound.task_id)).state is TaskState.PAUSED
    finally:
        await runtime.close()

    changed_routes = {
        "coding/occupy-a": ("slot-a",),
        "coding/occupy-b": ("slot-b",),
        "coding/mixed": ("slot-a",),
    }
    changed = _runtime(
        tmp_path,
        endpoints,
        route_slots=changed_routes,
        schedulable_route_slots=changed_routes,
    )
    try:
        await changed.start()
        with pytest.raises(WorkerConflictError) as rejected:
            await changed.service.resume(unbound.task_id)
        assert rejected.value.code == "harness_binding_changed"
        assert changed.store.get_task(unbound.task_id).state is TaskState.PAUSED
    finally:
        await changed.close()
        for server in servers.values():
            await server.close()


@pytest.mark.asyncio
async def test_v20_creation_requires_complete_v17_protocol_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_v20(monkeypatch)
    blockers = {"slot-a": asyncio.Event(), "slot-b": asyncio.Event()}
    servers, endpoints = await _start_provider_servers(blockers)
    routes = {
        "coding/default": ("slot-a",),
        "coding/quality": ("slot-b",),
    }
    runtime = _runtime(
        tmp_path,
        endpoints,
        route_slots=routes,
        schedulable_route_slots=routes,
    )
    origin = Origin(module="test", object_id="v20-prerequisites")
    illegal_request = _request("historical-v20-v16", "coding/default")
    illegal = runtime.store.create_task(
        TaskSpec(**illegal_request.model_dump(), origin=origin),
        capability_binding_sha256="a" * 64,
        capability_snapshot={"harness_protocol": "v20"},
        capability_observed_at=1.0,
        capability_expires_at=2.0,
        runtime_protocol=RuntimeProtocol.V16,
    )
    try:
        original_admit = runtime.workspace_broker.admit
        original_capabilities = runtime.harness_supervisor.capabilities_for_slots
        original_descriptors = runtime.harness_supervisor.harness_descriptors_for_slots
        calls = {"admit": 0, "supervisor": 0}

        async def tracked_admit(source):
            calls["admit"] += 1
            return await original_admit(source)

        async def tracked_capabilities(slot_ids):
            calls["supervisor"] += 1
            return await original_capabilities(slot_ids)

        async def tracked_descriptors(slot_ids):
            calls["supervisor"] += 1
            return await original_descriptors(slot_ids)

        monkeypatch.setattr(runtime.workspace_broker, "admit", tracked_admit)
        monkeypatch.setattr(
            runtime.harness_supervisor, "capabilities_for_slots", tracked_capabilities
        )
        monkeypatch.setattr(
            runtime.harness_supervisor,
            "harness_descriptors_for_slots",
            tracked_descriptors,
        )
        monkeypatch.setenv(_V17_PREREQUISITE_FLAGS[0], "false")
        with pytest.raises(WorkerConflictError) as preflight:
            await runtime.substrate.control_plane.create_task(
                origin, _request("preflight-before-io", "coding/default")
            )
        assert preflight.value.code == "harness_v20_prerequisites_disabled"
        assert calls == {"admit": 0, "supervisor": 0}
        monkeypatch.setattr(runtime.workspace_broker, "admit", original_admit)
        monkeypatch.setattr(
            runtime.harness_supervisor,
            "capabilities_for_slots",
            original_capabilities,
        )
        monkeypatch.setattr(
            runtime.harness_supervisor,
            "harness_descriptors_for_slots",
            original_descriptors,
        )
        for name in _V17_PREREQUISITE_FLAGS:
            monkeypatch.setenv(name, "true")
        await runtime.start()
        illegal = await runtime.service.wait_for(
            illegal.task_id, lambda item: item.state is TaskState.INTERRUPTED
        )
        assert illegal.reason == "harness_v20_prerequisites_disabled"
        with pytest.raises(WorkerConflictError) as historical_resume:
            await runtime.service.resume(illegal.task_id)
        assert historical_resume.value.code == "harness_v20_prerequisites_disabled"

        for index, missing in enumerate(_V17_PREREQUISITE_FLAGS):
            for name in _V17_PREREQUISITE_FLAGS:
                monkeypatch.setenv(name, "true")
            monkeypatch.setenv(missing, "false")
            with pytest.raises(WorkerConflictError) as rejected:
                await runtime.substrate.control_plane.create_task(
                    origin, _request(f"missing-prerequisite-{index}", "coding/default")
                )
            assert rejected.value.code == "harness_v20_prerequisites_disabled"

        for name in _V17_PREREQUISITE_FLAGS:
            monkeypatch.setenv(name, "true")
        existing_request = _request("v20-idempotent-prerequisite", "coding/default")
        existing = await runtime.substrate.control_plane.create_task(
            origin, existing_request
        )
        await _wait_running(runtime, existing.task_id)
        monkeypatch.setenv("CODING_WORKER_INTERACTION_ENABLED", "false")
        retried = await runtime.substrate.control_plane.create_task(
            origin, existing_request
        )
        assert retried.task_id == existing.task_id
        await runtime.service.cancel(existing.task_id)
    finally:
        await runtime.close()
        for server in servers.values():
            await server.close()
