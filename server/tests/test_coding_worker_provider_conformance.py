from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from server.coding_worker.claude_provider import (
    ClaudeCodeProvider,
    ClaudeCodeProviderError,
    ClaudeCodeRoute,
)
from server.coding_worker.contracts import PolicyProfile, TaskBudget
from server.coding_worker.opencode_provider import (
    OpenCodeProvider,
    OpenCodeProviderError,
    OpenCodeRoute,
    OpenCodeServerHandle,
)
from server.coding_worker.provider import (
    CodingAgentProvider,
    FakeCodingAgentProvider,
    PROVIDER_CHECKPOINT_FORMAT_VERSION,
    PROVIDER_CONTRACT_VERSION,
    ProviderCapabilities,
    ProviderEventKind,
    ProviderEvent,
    ProviderOpenRequest,
)
from server.coding_worker.harness_protocol import (
    HarnessPersistenceLevel,
    HarnessToolOwnership,
)


def _request(route_id: str) -> ProviderOpenRequest:
    return ProviderOpenRequest(
        task_id="task-conformance",
        workspace_id="workspace-conformance",
        objective="Inspect and fix the project.",
        model_route=route_id,
        policy_profile=PolicyProfile.DEVELOP,
        budget=TaskBudget(max_seconds=60, max_output_bytes=1024 * 1024),
        workspace_tree_hash="a" * 64,
        tool_allowlist=("read_file",),
    )


def _fake_provider(_tmp_path: Path) -> tuple[CodingAgentProvider, ProviderOpenRequest]:
    return FakeCodingAgentProvider(), _request("coding/default")


def _opencode_provider(
    tmp_path: Path,
) -> tuple[CodingAgentProvider, ProviderOpenRequest]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state: dict[str, Any] = {"counter": 0, "session_id": ""}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/session" and request.method == "POST":
            state["counter"] += 1
            state["session_id"] = f"ses_conformance_{state['counter']}"
            return httpx.Response(200, json={"id": state["session_id"]})
        if request.url.path == "/event":
            body = (
                "data: "
                + json.dumps(
                    {
                        "type": "session.idle",
                        "properties": {"sessionID": state["session_id"]},
                    }
                )
                + "\n\n"
            )
            return httpx.Response(
                200, text=body, headers={"content-type": "text/event-stream"}
            )
        if request.url.path.endswith("/prompt_async"):
            return httpx.Response(204)
        if request.url.path == "/mcp":
            return httpx.Response(200, json={})
        return httpx.Response(404)

    async def factory(
        request: ProviderOpenRequest, resolved: Path, route: OpenCodeRoute
    ) -> OpenCodeServerHandle:
        assert request.task_id == "task-conformance"
        assert resolved == workspace.resolve()
        assert route.route_id == "coding/default"
        client = httpx.AsyncClient(
            base_url="http://127.0.0.1:4096",
            transport=httpx.MockTransport(handler),
        )

        async def close() -> None:
            await client.aclose()

        return OpenCodeServerHandle(
            task_id=request.task_id,
            workspace=resolved,
            state_root=tmp_path / "opencode-state",
            client=client,
            close_callback=close,
        )

    route = OpenCodeRoute(
        route_id="coding/default",
        model_id="test-model",
        base_url="http://new-api:3000/v1",
        api_key="test-route-key",
    )
    return (
        OpenCodeProvider(
            workspace_resolver=lambda _workspace_id: workspace,
            runtime_root=tmp_path / "runtime",
            routes={route.route_id: route},
            server_factory=factory,
        ),
        _request(route.route_id),
    )


def _claude_provider(
    tmp_path: Path,
) -> tuple[CodingAgentProvider, ProviderOpenRequest]:
    script = tmp_path / "fake_claude.py"
    script.write_text(
        """
import json
import sys
frame = json.loads(sys.stdin.readline())
print(json.dumps({
    'type': 'result',
    'session_id': frame['session_id'],
    'is_error': False,
    'usage': {'input_tokens': 1, 'output_tokens': 1},
}))
""".strip()
        + "\n",
        encoding="utf-8",
    )
    secret = tmp_path / "claude-secret"
    secret.write_text("test-secret", encoding="utf-8")
    route = ClaudeCodeRoute(
        route_id="coding/quality", model_id="claude-test-model"
    )
    provider = ClaudeCodeProvider(
        runtime_root=tmp_path / "claude-runtime",
        routes={route.route_id: route},
        secret_path=secret,
        command_prefix=(sys.executable, str(script)),
    )
    provider.bind_broker(
        "task-conformance", "unix:/run/broker.sock", "b" * 48
    )
    return provider, _request(route.route_id)


ProviderFactory = Callable[
    [Path], tuple[CodingAgentProvider, ProviderOpenRequest]
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "factory", [_fake_provider, _opencode_provider, _claude_provider]
)
async def test_provider_v4_conformance(
    factory: ProviderFactory, tmp_path: Path
) -> None:
    provider, request = factory(tmp_path)
    capabilities = await provider.capabilities()
    assert capabilities.contract_version == PROVIDER_CONTRACT_VERSION
    assert capabilities.supports_streaming is True
    assert capabilities.supports_checkpoint is True
    assert capabilities.supports_restore is True
    assert capabilities.supports_tool_boundaries is True
    assert capabilities.supports_turn_interrupt is True
    assert capabilities.tool_names
    if isinstance(provider, FakeCodingAgentProvider):
        assert capabilities.supports_structured_plan is True
        assert capabilities.supports_todo is True
        assert capabilities.supports_questions is True
        assert capabilities.supports_compaction is True
    else:
        assert capabilities.supports_structured_plan is False
        assert capabilities.supports_todo is False
        assert capabilities.supports_questions is False
        assert capabilities.supports_compaction is False

    session = await provider.open(request)
    events = [event async for event in provider.message(session, "continue")]
    assert events[-1].kind is ProviderEventKind.TURN_COMPLETED
    assert all("supplier" not in json.dumps(event.data) for event in events)
    checkpoint = await provider.checkpoint(session)
    compatibility = checkpoint.compatibility
    assert compatibility is not None
    assert compatibility.contract_version == PROVIDER_CONTRACT_VERSION
    assert compatibility.format_version == PROVIDER_CHECKPOINT_FORMAT_VERSION
    assert compatibility.task_id == request.task_id
    assert compatibility.workspace_tree_hash == request.workspace_tree_hash
    await provider.close(session)

    if isinstance(provider, ClaudeCodeProvider):
        provider.bind_broker(
            "task-conformance", "unix:/run/broker.sock", "b" * 48
        )
    restored = await provider.restore(request, checkpoint)
    await provider.close(restored)

    changed = request.model_copy(update={"workspace_tree_hash": "b" * 64})
    if isinstance(provider, ClaudeCodeProvider):
        provider.bind_broker(
            "task-conformance", "unix:/run/broker.sock", "b" * 48
        )
    with pytest.raises(
        (ValueError, OpenCodeProviderError, ClaudeCodeProviderError)
    ):
        await provider.restore(changed, checkpoint)


def test_provider_v4_rejects_raw_or_malformed_event_data() -> None:
    with pytest.raises(ValueError, match="Extra inputs|canonical"):
        ProviderEvent(
            kind=ProviderEventKind.FAILED,
            data={"failure_kind": "unavailable", "raw_frame": "secret"},
        )
    with pytest.raises(ValueError, match="message event"):
        ProviderEvent(
            kind=ProviderEventKind.MESSAGE,
            data={"text": "ok", "session_id": "supplier-session"},
        )


@pytest.mark.parametrize("factory", [_opencode_provider, _claude_provider])
def test_production_provider_harness_descriptors_are_broker_only_and_honest(
    factory: ProviderFactory, tmp_path: Path
) -> None:
    provider, _request_value = factory(tmp_path)
    assert isinstance(provider, (OpenCodeProvider, ClaudeCodeProvider))

    descriptor = provider.harness_descriptor()

    assert descriptor.protocol_id == "modelmirror-provider-v4"
    assert descriptor.protocol_version == "4"
    assert descriptor.tool_ownership is HarnessToolOwnership.BROKER_ONLY
    assert descriptor.persistence is HarnessPersistenceLevel.SESSION_RESUME
    assert descriptor.capability("checkpoint").available is True
    assert descriptor.capability("restore").available is True
    assert descriptor.capability("steering").available is False
    assert descriptor.capability("steering").reason is not None
    encoded = descriptor.model_dump_json()
    assert "test-route-key" not in encoded
    assert "test-secret" not in encoded


def test_provider_capabilities_fail_closed_until_explicitly_declared() -> None:
    capabilities = ProviderCapabilities()
    assert capabilities.model_dump(mode="json") == {
        "contract_version": PROVIDER_CONTRACT_VERSION,
        "supports_streaming": False,
        "supports_cancel": False,
        "supports_checkpoint": False,
        "supports_restore": False,
        "supports_steering": False,
        "supports_usage": False,
        "supports_structured_plan": False,
        "supports_todo": False,
        "supports_questions": False,
        "supports_compaction": False,
        "supports_tool_boundaries": False,
        "supports_turn_interrupt": False,
        "tool_names": [],
    }


def test_provider_v4_structured_event_vocabulary_is_canonical() -> None:
    events = (
        ProviderEvent(
            kind=ProviderEventKind.PLAN,
            data={"explanation": None, "items": [{"step": "inspect", "status": "in_progress"}]},
        ),
        ProviderEvent(
            kind=ProviderEventKind.TODO,
            data={"items": [{"todo_id": "todo_1", "content": "run tests", "status": "pending"}]},
        ),
        ProviderEvent(
            kind=ProviderEventKind.QUESTION,
            data={"question_id": "question_1", "prompt": "Choose scope", "options": []},
        ),
        ProviderEvent(
            kind=ProviderEventKind.COMPACTION,
            data={"summary": "Preserved public context.", "boundary_sequence": 7},
        ),
    )
    assert [event.kind.value for event in events] == [
        "plan",
        "todo",
        "question",
        "compaction",
    ]


def test_real_provider_frames_normalize_tool_boundaries_without_raw_payloads() -> None:
    opencode_started = OpenCodeProvider._map_event(
        {
            "type": "message.part.updated",
            "properties": {
                "sessionID": "ses_1",
                "part": {
                    "type": "tool",
                    "callID": "operation_1",
                    "tool": "read_file",
                    "state": {"status": "running", "input": {"path": "secret.py"}},
                },
            },
        },
        "ses_1",
    )
    assert opencode_started == ProviderEvent(
        kind=ProviderEventKind.TOOL_STARTED,
        data={
            "operation_id": "operation_1",
            "tool_name": "read_file",
            "summary": "Tool execution started.",
        },
    )

    claude_started = ClaudeCodeProvider.map_stream_frame(
        json.dumps(
            {
                "type": "assistant",
                "session_id": "claude_1",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "operation_2",
                            "name": "mcp__modelmirror-tool-broker__read_file",
                            "input": {"path": "secret.py"},
                        }
                    ]
                },
            }
        ).encode(),
        "claude_1",
    )
    assert claude_started == (
        ProviderEvent(
            kind=ProviderEventKind.TOOL_STARTED,
            data={
                "operation_id": "operation_2",
                "tool_name": "read_file",
                "summary": "Tool execution started.",
            },
        ),
    )
    assert "secret.py" not in json.dumps(
        [opencode_started.model_dump(mode="json"), claude_started[0].model_dump(mode="json")]
    )
