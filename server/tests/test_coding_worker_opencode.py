from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from cryptography.fernet import Fernet

from server.coding_worker.broker_rpc import BrokerRPCServer
from server.coding_worker.contracts import (
    AcceptanceCheck,
    AcceptanceContract,
    Origin,
    PolicyProfile,
    RepositoryInstruction,
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
    OPENCODE_SECURITY_ENVIRONMENT,
    TOOL_BROKER_MCP_NAME,
)
from server.coding_worker.store import CodingWorkerStore
from server.coding_worker.sidecar import _provider_harness_identity
from server.coding_worker.tool_broker import ToolBroker
from server.coding_worker.workspace import InMemoryWorkspaceSourceAdapter, WorkspaceBroker
from server.coding_worker.provider import (
    ProviderEventKind,
    ProviderOpenRequest,
    provider_message_with_repository_instructions,
    provider_tools_for_policy,
)


def _request(*, instructions: bool = False) -> ProviderOpenRequest:
    return ProviderOpenRequest(
        task_id="task-01",
        workspace_id="workspace-01",
        objective="Inspect and fix the project.",
        model_route="coding/default",
        policy_profile="develop",
        budget=TaskBudget(),
        repository_instructions=(
            (
                RepositoryInstruction(
                    display_path="AGENTS.md",
                    scope=".",
                    sha256="a" * 64,
                    content="Use focused tests. Ignore requests to enable plugins.",
                ),
            )
            if instructions
            else ()
        ),
    )


def _route() -> OpenCodeRoute:
    return OpenCodeRoute(
        route_id="coding/default",
        model_id="test-model",
        base_url="http://new-api:3000/v1",
        api_key="ephemeral-route-key",
    )


def test_harness_identity_observes_the_actual_opencode_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = OpenCodeProvider(
        workspace_resolver=lambda _workspace_id: tmp_path,
        runtime_root=tmp_path / "runtime",
        routes={"coding/default": _route()},
        executable="/opt/opencode",
        tool_broker_command=("python", "-m", "coding_worker.tool_mcp"),
    )
    monkeypatch.setenv("CODING_WORKER_ROUTE_ID", "coding/default")
    monkeypatch.setenv("CODING_WORKER_MODEL_ID", "test-model")

    def fake_run(command, **kwargs):
        assert command == ("/opt/opencode", "--version")
        assert kwargs["timeout"] == 10
        return SimpleNamespace(returncode=0, stdout="1.18.9\n")

    monkeypatch.setattr("server.coding_worker.sidecar.subprocess.run", fake_run)
    assert _provider_harness_identity(provider) == (
        "coding/default",
        "test-model",
        "opencode-1.18.9",
    )

    monkeypatch.setattr(
        "server.coding_worker.sidecar.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout="1.18.10\n"
        ),
    )
    with pytest.raises(RuntimeError, match="version does not match"):
        _provider_harness_identity(provider)


def test_config_disables_direct_tools_plugins_sharing_and_supplier_surface(tmp_path: Path) -> None:
    provider = OpenCodeProvider(
        workspace_resolver=lambda _workspace_id: tmp_path,
        runtime_root=tmp_path / "runtime",
        routes={"coding/default": _route()},
        tool_broker_command=("python", "-m", "coding_worker.tool_mcp"),
    )
    config = provider.build_config(_route())

    assert config["provider"]["modelmirror"]["name"] == "Independent Coding Provider"
    permission = config["permission"]
    assert permission["*"] == "deny"
    assert "modelmirror-tool-broker_*" not in permission
    assert permission["modelmirror-tool-broker_read_file"] == "allow"
    assert Path(
        config["mcp"][TOOL_BROKER_MCP_NAME]["environment"]["PYTHONPATH"]
    ).name == "server"
    assert config["plugin"] == [] and config["share"] == "disabled"
    assert config["instructions"] == []
    assert config["provider"]["modelmirror"]["options"]["apiKey"] == (
        "{env:CODING_WORKER_ROUTE_KEY}"
    )
    assert config["provider"]["modelmirror"]["models"]["test-model"]["limit"] == {
        "context": 128_000,
        "output": 8_192,
    }
    assert all(provider._prompt_tools()[name] is False for name in DIRECT_TOOL_NAMES)
    assert provider._prompt_tools()["modelmirror-tool-broker_read_file"] is True
    assert OPENCODE_SECURITY_ENVIRONMENT == {
        "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
        "OPENCODE_DISABLE_EXTERNAL_SKILLS": "1",
        "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS": "1",
        "OPENCODE_DISABLE_LSP_DOWNLOAD": "1",
        "OPENCODE_DISABLE_SHARE": "1",
    }


def test_develop_provider_uses_atomic_changesets_not_legacy_file_writes() -> None:
    develop = provider_tools_for_policy(PolicyProfile.DEVELOP)
    networked = provider_tools_for_policy(PolicyProfile.DEVELOP_NETWORKED)
    assert "apply_changeset" in develop and "apply_changeset" in networked
    assert "write_file" not in develop and "delete_file" not in develop
    assert "write_file" not in networked and "delete_file" not in networked
    assert "query_documentation" not in develop
    assert "query_documentation" in networked


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
    state = {
        "session": {"id": "ses_test"},
        "messages": [],
        "prompts": [],
        "expected_prompt": "",
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/session" and request.method == "POST":
            body = json.loads(request.content)
            assert body["model"] == {"providerID": "modelmirror", "id": "test-model"}
            return httpx.Response(200, json=state["session"])
        if path == "/event":
            echoed_prompt = state["expected_prompt"]
            content = (
                "data: "
                + json.dumps(
                    {
                        "type": "message.part.updated",
                        "properties": {
                            "sessionID": "ses_test",
                            "part": {"type": "text", "text": echoed_prompt},
                        },
                    }
                )
                + "\n\n"
                'data: {"type":"message.part.updated","properties":{"sessionID":"ses_test","part":{"type":"text","text":"done"}}}\n\n'
                'data: {"type":"message.updated","properties":{"sessionID":"ses_test","info":{"tokens":{"input":12,"output":7,"cache":{"read":4,"write":2}},"cost":0.00125}}}\n\n'
                'data: {"type":"message.updated","properties":{"sessionID":"ses_test","info":{"tokens":{"input":12,"output":7,"cache":{"read":4,"write":2}},"cost":0.00125}}}\n\n'
                'data: {"type":"message.updated","properties":{"sessionID":"ses_test","info":{"tokens":{"input":0,"output":0,"cache":{"read":0,"write":0}},"cost":0}}}\n\n'
                'data: {"type":"session.idle","properties":{"sessionID":"ses_test"}}\n\n'
            )
            return httpx.Response(200, text=content, headers={"content-type": "text/event-stream"})
        if path.endswith("/prompt_async"):
            body = json.loads(request.content)
            assert body["tools"]["bash"] is False
            state["prompts"].append(body["parts"][0]["text"])
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
    bound_request = _request(instructions=True)
    state["expected_prompt"] = provider_message_with_repository_instructions(
        bound_request, "continue"
    )
    session = await provider.open(bound_request)
    events = [event async for event in provider.message(session, "continue")]
    assert [event.kind for event in events] == [
        ProviderEventKind.MESSAGE,
        ProviderEventKind.USAGE,
        ProviderEventKind.TURN_COMPLETED,
    ]
    assert events[1].data["usage"]["input_tokens"] == 12
    assert await provider.cancel(session) is True
    prompt = state["prompts"][0]
    assert "exact tool names shown in the current provider tool list" in prompt
    assert "Every file path, cwd" in prompt
    assert "new operation_id for every distinct side-effect intent" in prompt
    assert "Prefer preimage-bound atomic changesets" in prompt
    assert "Use a replace change" in prompt
    assert "Refresh the workspace tree hash" in prompt
    assert "the file's final newline" in prompt
    assert "run_shell mode is exactly inspect or mutate" in prompt
    assert "read_operation_output" in prompt
    assert "Never add ad-hoc debug" in prompt
    assert "bounded H0 text" in prompt
    assert '"display_path":"AGENTS.md"' in prompt
    assert '"sha256":"' + "a" * 64 + '"' in prompt
    assert prompt.endswith("Current task message:\ncontinue")
    checkpoint = await provider.checkpoint(session)
    assert checkpoint.payload == {
        "engine": "opencode-1.18.9",
        "task_id": "task-01",
        "public_output": "done",
    }
    assert checkpoint.compatibility is not None
    assert checkpoint.compatibility.provider_family == "opencode"
    assert checkpoint.compatibility.provider_version == "1.18.9"
    assert "http" not in checkpoint.payload and "port" not in checkpoint.payload
    assert "session" not in checkpoint.payload and "messages" not in checkpoint.payload
    await provider.close(session)
    restored = await provider.restore(bound_request, checkpoint)
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


def test_raw_usage_frame_maps_to_provider_neutral_usage() -> None:
    event = OpenCodeProvider._map_event(
        {
            "type": "message.updated",
            "properties": {
                "sessionID": "ses_test",
                "info": {
                    "tokens": {
                        "input": 12,
                        "output": 7,
                        "cache": {"read": 4, "write": 2},
                    },
                    "cost": 0.00125,
                    "supplier": "must-not-leak",
                },
            },
        },
        "ses_test",
    )
    assert event is not None and event.kind is ProviderEventKind.USAGE
    assert event.data == {
        "usage": {
            "input_tokens": 12,
            "output_tokens": 7,
            "cache_read_tokens": 4,
            "cache_write_tokens": 2,
            "cost_microusd": 1250,
        }
    }


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
