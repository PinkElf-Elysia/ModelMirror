from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet

from server.coding_worker.broker_rpc import BrokerRPCServer
from server.coding_worker.contracts import (
    AcceptanceCheck,
    AcceptanceContract,
    Origin,
    TaskBudget,
    TaskSpec,
    TaskState,
    WorkspaceSource,
)
from server.coding_worker.opencode_provider import (
    DIRECT_TOOL_NAMES,
    OpenCodeProvider,
    OpenCodeRoute,
    OpenCodeServerHandle,
    TOOL_BROKER_MCP_NAME,
)
from server.coding_worker.store import CodingWorkerStore
from server.coding_worker.tool_broker import ToolBroker
from server.coding_worker.workspace import InMemoryWorkspaceSourceAdapter, WorkspaceBroker
from server.coding_worker.provider import ProviderEventKind, ProviderOpenRequest


def _request() -> ProviderOpenRequest:
    return ProviderOpenRequest(
        task_id="task-01",
        workspace_id="workspace-01",
        objective="Inspect and fix the project.",
        model_route="coding/default",
        policy_profile="develop",
        budget=TaskBudget(),
    )


def _route() -> OpenCodeRoute:
    return OpenCodeRoute(
        route_id="coding/default",
        model_id="test-model",
        base_url="http://new-api:3000/v1",
        api_key="ephemeral-route-key",
    )


def test_config_disables_direct_tools_plugins_sharing_and_supplier_surface(tmp_path: Path) -> None:
    provider = OpenCodeProvider(
        workspace_resolver=lambda _workspace_id: tmp_path,
        runtime_root=tmp_path / "runtime",
        routes={"coding/default": _route()},
        tool_broker_command=("python", "-m", "coding_worker.tool_mcp"),
    )
    config = provider.build_config(_route())
    permission = config["permission"]
    assert permission["*"] == "deny"
    assert permission["modelmirror-tool-broker_*"] == "allow"
    assert Path(
        config["mcp"][TOOL_BROKER_MCP_NAME]["environment"]["PYTHONPATH"]
    ).name == "server"
    assert config["plugin"] == [] and config["share"] == "disabled"
    assert config["instructions"] == []
    assert config["provider"]["modelmirror"]["options"]["apiKey"] == (
        "{env:CODING_WORKER_ROUTE_KEY}"
    )
    assert all(provider._prompt_tools()[name] is False for name in DIRECT_TOOL_NAMES)


@pytest.mark.asyncio
async def test_open_waits_for_connected_broker_before_creating_session(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    calls = {"mcp": 0, "session": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/mcp":
            calls["mcp"] += 1
            status = "connecting" if calls["mcp"] == 1 else "connected"
            return httpx.Response(
                200,
                json={TOOL_BROKER_MCP_NAME: {"status": status}},
            )
        if request.url.path == "/session" and request.method == "POST":
            calls["session"] += 1
            return httpx.Response(200, json={"id": "ses_ready"})
        return httpx.Response(404)

    async def factory(
        request: ProviderOpenRequest, resolved: Path, route: OpenCodeRoute
    ) -> OpenCodeServerHandle:
        client = httpx.AsyncClient(
            base_url="http://127.0.0.1:4096",
            transport=httpx.MockTransport(handler),
        )

        async def close() -> None:
            await client.aclose()

        return OpenCodeServerHandle(
            task_id=request.task_id,
            workspace=resolved,
            state_root=tmp_path / "state",
            client=client,
            close_callback=close,
        )

    provider = OpenCodeProvider(
        workspace_resolver=lambda _workspace_id: workspace,
        runtime_root=tmp_path / "runtime",
        routes={"coding/default": _route()},
        tool_broker_command=("python", "-m", "coding_worker.broker_mcp"),
        server_factory=factory,
    )
    session = await provider.open(_request())
    assert session.session_id == "ses_ready"
    assert calls == {"mcp": 2, "session": 1}
    await provider.close(session)


@pytest.mark.asyncio
async def test_headless_adapter_maps_events_cancel_and_checkpoint_without_public_frames(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    closed: list[bool] = []
    state = {"session": {"id": "ses_test"}, "messages": []}

    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/session" and request.method == "POST":
            body = json.loads(request.content)
            assert body["model"] == {"providerID": "modelmirror", "id": "test-model"}
            return httpx.Response(200, json=state["session"])
        if path == "/event":
            content = (
                'data: {"type":"message.part.updated","properties":{"sessionID":"ses_test","part":{"type":"text","text":"done"}}}\n\n'
                'data: {"type":"session.idle","properties":{"sessionID":"ses_test"}}\n\n'
            )
            return httpx.Response(200, text=content, headers={"content-type": "text/event-stream"})
        if path.endswith("/prompt_async"):
            body = json.loads(request.content)
            assert body["tools"]["bash"] is False
            return httpx.Response(204)
        if path.endswith("/abort"):
            return httpx.Response(200, json=True)
        if path.endswith("/message"):
            return httpx.Response(200, json=state["messages"])
        if path == "/session/ses_test":
            return httpx.Response(200, json=state["session"])
        return httpx.Response(404)

    async def factory(
        request: ProviderOpenRequest, resolved: Path, route: OpenCodeRoute
    ) -> OpenCodeServerHandle:
        assert request.task_id == "task-01" and resolved == workspace.resolve()
        assert route.route_id == "coding/default"
        client = httpx.AsyncClient(
            base_url="http://127.0.0.1:4096",
            transport=httpx.MockTransport(handler),
        )

        async def close() -> None:
            closed.append(True)
            await client.aclose()

        return OpenCodeServerHandle(
            task_id=request.task_id,
            workspace=resolved,
            state_root=tmp_path / "state",
            client=client,
            close_callback=close,
        )

    provider = OpenCodeProvider(
        workspace_resolver=lambda _workspace_id: workspace,
        runtime_root=tmp_path / "runtime",
        routes={"coding/default": _route()},
        server_factory=factory,
    )
    session = await provider.open(_request())
    events = [event async for event in provider.message(session, "continue")]
    assert [event.kind for event in events] == [
        ProviderEventKind.MESSAGE,
        ProviderEventKind.TURN_COMPLETED,
    ]
    assert await provider.cancel(session) is True
    checkpoint = await provider.checkpoint(session)
    assert checkpoint.payload == {
        "engine": "opencode-1.18.9",
        "task_id": "task-01",
        "public_output": "done",
    }
    assert "http" not in checkpoint.payload and "port" not in checkpoint.payload
    assert "session" not in checkpoint.payload and "messages" not in checkpoint.payload
    await provider.close(session)
    restored = await provider.restore(_request(), checkpoint)
    assert restored.task_id == session.task_id
    await provider.close(restored)
    assert closed == [True, True]


def test_provider_session_is_excluded_from_public_task_record() -> None:
    from server.coding_worker.contracts import (
        AcceptanceCheck,
        AcceptanceContract,
        Origin,
        TaskRecord,
        TaskSpec,
        TaskState,
        WorkspaceSource,
    )

    record = TaskRecord(
        task_id="task-01",
        spec=TaskSpec(
            client_task_id="client-01",
            origin=Origin(module="test", object_id="one"),
            objective="work",
            workspace_source=WorkspaceSource(
                kind="manifest", source_id="source-01", revision="revision-01"
            ),
            acceptance=AcceptanceContract(
                contract_id="contract-01",
                required_checks=(
                    AcceptanceCheck(check_id="check", label="check", kind="command"),
                ),
            ),
            model_route="coding/default",
        ),
        state=TaskState.RUNNING,
        provider_session_id="ses_private",
        created_at=1,
        updated_at=1,
        expires_at=2,
    )
    assert record.provider_session_id == "ses_private"
    assert "provider_session_id" not in record.model_dump(mode="json")


def test_raw_permission_frame_never_enters_provider_neutral_event() -> None:
    event = OpenCodeProvider._map_event(
        {
            "type": "permission.asked",
            "properties": {
                "sessionID": "ses_test",
                "path": "C:/private/project",
                "port": 47123,
                "vendorRequest": {"tool": "bash"},
            },
        },
        "ses_test",
    )
    assert event is not None
    assert event.kind is ProviderEventKind.APPROVAL_REQUIRED
    assert event.data == {"capability": "provider_permission"}


@pytest.mark.asyncio
async def test_provider_gives_mcp_only_a_revocable_task_broker_binding(
    tmp_path: Path,
) -> None:
    source = WorkspaceSource(kind="manifest", source_id="broker", revision="h0")
    workspace_broker = WorkspaceBroker(
        tmp_path / "workspace-broker",
        {"manifest": InMemoryWorkspaceSourceAdapter({("broker", "h0"): {"a.py": b""}})},
        id_key=b"o" * 32,
    )
    prepared = await workspace_broker.prepare(source)
    store = CodingWorkerStore(tmp_path / "store", master_key=Fernet.generate_key())
    spec = TaskSpec(
        client_task_id="broker-binding",
        origin=Origin(module="tests", object_id="broker-binding"),
        objective="inspect",
        workspace_source=source,
        acceptance=AcceptanceContract(
            contract_id="contract",
            required_checks=(
                AcceptanceCheck(check_id="check", label="check", kind="command"),
            ),
        ),
        model_route="coding/default",
    )
    task = store.create_task(spec)
    store.transition(task.task_id, TaskState.PREPARING)
    store.transition(task.task_id, TaskState.RUNNING, workspace_id=prepared.workspace_id)
    rpc = BrokerRPCServer(ToolBroker(store=store, workspace_broker=workspace_broker))
    await rpc.start_tcp_for_tests()
    provider = OpenCodeProvider(
        workspace_resolver=lambda _workspace_id: workspace,
        runtime_root=tmp_path / "runtime",
        routes={"coding/default": _route()},
        broker_rpc=rpc,
    )
    environment, revoke = provider._broker_environment(task.task_id)
    assert environment["CODING_WORKER_TASK_ID"] == task.task_id
    assert "coding_worker.broker_mcp" in " ".join(provider._tool_broker_command or ())
    assert "CODING_WORKER_ROUTE_KEY" not in environment
    revoke()
    assert task.task_id not in rpc._tokens
    await rpc.close()


def test_opencode_provider_accepts_sidecar_broker_binding(tmp_path: Path) -> None:
    provider = OpenCodeProvider(
        workspace_resolver=lambda _workspace_id: tmp_path,
        runtime_root=tmp_path / "runtime",
        routes={"coding/default": _route()},
        tool_broker_command=("python", "-m", "coding_worker.broker_mcp"),
    )
    provider.bind_broker("task_sidecar", "unix:/run/broker.sock", "t" * 48)
    environment, revoke = provider._broker_environment("task_sidecar")
    assert environment == {
        "CODING_WORKER_BROKER_ENDPOINT": "unix:/run/broker.sock",
        "CODING_WORKER_BROKER_TOKEN": "t" * 48,
        "CODING_WORKER_TASK_ID": "task_sidecar",
    }
    revoke()
    assert "task_sidecar" not in provider._broker_bindings
