from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.agent_upstream.models import EngineShadowRunCreate
from server.agent_upstream.port import (
    EngineExecutionResult,
    EngineRequest,
    EngineShadowRunSpec,
)
from server.agent_upstream.service import EngineShadowService, EngineShadowServiceError
from server.agent_upstream.store import EngineShadowStore
from server.agent_workspace.tools import ToolExecutionError
from server.agent_workspace.gateway import GatewayTurn


def _catalog(*, available: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        models=[
            SimpleNamespace(
                invocation_id="deepseek/deepseek-v4-flash-0731",
                profile_id="deepseek-v4-flash-0731",
                root="deepseek-v4-flash-0731",
                name="DeepSeek V4 Flash 0731",
                invocable=available,
                availability="live" if available else "offline",
                operations=("chat",),
                interaction_status="ready",
                context_length=1_048_576,
                max_output_tokens=32_000,
            )
        ]
    )


class _FakeGateway:
    def __init__(self) -> None:
        self.messages: list[list[dict[str, object]]] = []

    async def stream_turn(self, *, messages, **_kwargs) -> GatewayTurn:
        self.messages.append(messages)
        return GatewayTurn(
            content="private-model-output-that-must-stay-in-memory",
            tool_calls=(),
            finish_reason="stop",
            model_id="deepseek/deepseek-v4-flash-0731",
        )


class _CandidatePort:
    def __init__(self) -> None:
        self.stopped: list[str] = []

    async def start_run(self, spec, *, on_event, execute_model, execute_tool):
        await on_event(
            "run.progress",
            {"kind": "goal_round", "rounds": 1, "tokens_used": 0},
        )
        await execute_model(
            EngineRequest(
                run_id=spec.run_id,
                request_id="model-1",
                payload={
                    "thinking_level": "medium",
                    "new_messages": [
                        {
                            "type": "model_msg",
                            "payload": {
                                "type": "text",
                                "role": "user",
                                "text": "private-user-message-that-must-stay-in-memory",
                            },
                        }
                    ],
                },
            )
        )
        await execute_tool(
            EngineRequest(
                run_id=spec.run_id,
                request_id="tool-1",
                payload={
                    "name": "write_file",
                    "arguments": {
                        "file_path": "index.html",
                        "content": "<!doctype html><title>Shadow candidate</title>",
                    },
                },
            )
        )
        goal = (spec.workspace_dir / ".modelmirror" / "GOAL.yaml").read_text(
            encoding="utf-8"
        )
        await execute_tool(
            EngineRequest(
                run_id=spec.run_id,
                request_id="tool-2",
                payload={
                    "name": "write_file",
                    "arguments": {
                        "file_path": ".modelmirror/GOAL.yaml",
                        "content": goal.replace("status: active", "status: complete"),
                    },
                },
            )
        )
        return EngineExecutionResult(
            status="candidate_ready",
            goal_round=1,
            tokens_used=1234,
            model_turns=1,
            tool_calls=2,
        )

    async def stop_run(self, run_id: str) -> None:
        self.stopped.append(run_id)

    async def shutdown(self) -> None:
        return None


class _BlockingPort:
    def __init__(self) -> None:
        self.release = asyncio.Event()

    async def start_run(self, spec, **_kwargs):
        await self.release.wait()
        return EngineExecutionResult(status="stopped")

    async def stop_run(self, _run_id: str) -> None:
        self.release.set()

    async def shutdown(self) -> None:
        self.release.set()


class _ProgressPreservingPort(_CandidatePort):
    async def start_run(self, spec, *, on_event, execute_model, execute_tool):
        await on_event(
            "run.progress",
            {"kind": "token_usage", "tokens_used": 4321},
        )
        return await super().start_run(
            spec,
            on_event=on_event,
            execute_model=execute_model,
            execute_tool=execute_tool,
        )


class _FailingToolBridge:
    async def execute(self, **_kwargs):
        raise ToolExecutionError(
            "file_path secret-token/private.txt was not found"
        )


class _ToolFailurePort:
    async def start_run(self, spec, *, execute_tool, **_kwargs):
        with pytest.raises(ToolExecutionError):
            await execute_tool(
                EngineRequest(
                    run_id=spec.run_id,
                    request_id="tool-failure",
                    payload={
                        "name": "read_file",
                        "arguments": {"file_path": "secret-token/private.txt"},
                    },
                )
            )
        return EngineExecutionResult(
            status="failed",
            model_turns=1,
            tool_calls=1,
        )

    async def stop_run(self, _run_id: str) -> None:
        return None

    async def shutdown(self) -> None:
        return None


async def _wait_terminal(service: EngineShadowService, run_id: str):
    for _ in range(200):
        record = service.get_detail(run_id).run
        if record.status != "running":
            return record
        await asyncio.sleep(0.01)
    raise AssertionError("shadow run did not reach a terminal state")


@pytest.mark.asyncio
async def test_shadow_service_produces_host_verified_candidate_without_transcript_persistence(
    tmp_path: Path,
) -> None:
    root = tmp_path / "agent-workspace"
    gateway = _FakeGateway()
    service = EngineShadowService(
        store=EngineShadowStore(root),
        port=_CandidatePort(),
        gateway=gateway,
        catalog_provider=_catalog,
    )

    created = await service.create_run(
        EngineShadowRunCreate(objective="Build a one-file offline interaction")
    )
    finished = await _wait_terminal(service, created.run_id)

    assert finished.status == "candidate_ready"
    assert len(finished.candidate_sha256) == 64
    assert finished.goal_round == 1
    assert finished.token_total == 1234
    assert finished.tool_calls == 2
    assert (service.store.workspace(created.run_id) / "index.html").is_file()
    assert gateway.messages

    database = root / "agent_workspace.sqlite3"
    raw_database = database.read_bytes()
    assert b"private-user-message-that-must-stay-in-memory" not in raw_database
    assert b"private-model-output-that-must-stay-in-memory" not in raw_database
    events = service.list_events(created.run_id)
    assert all("content" not in str(event.payload).lower() for event in events)
    await service.shutdown()


@pytest.mark.asyncio
async def test_shadow_service_preserves_higher_streamed_token_total(
    tmp_path: Path,
) -> None:
    service = EngineShadowService(
        store=EngineShadowStore(tmp_path / "agent-workspace"),
        port=_ProgressPreservingPort(),
        gateway=_FakeGateway(),
        catalog_provider=_catalog,
    )

    created = await service.create_run(
        EngineShadowRunCreate(objective="Preserve cumulative token usage")
    )
    finished = await _wait_terminal(service, created.run_id)

    assert finished.status == "candidate_ready"
    assert finished.token_total == 4321
    await service.shutdown()


@pytest.mark.asyncio
async def test_shadow_service_persists_only_sanitized_tool_failure_metadata(
    tmp_path: Path,
) -> None:
    root = tmp_path / "agent-workspace"
    service = EngineShadowService(
        store=EngineShadowStore(root),
        port=_ToolFailurePort(),
        gateway=_FakeGateway(),
        catalog_provider=_catalog,
        tool_bridge=_FailingToolBridge(),
    )

    created = await service.create_run(
        EngineShadowRunCreate(objective="Exercise a safe tool error")
    )
    finished = await _wait_terminal(service, created.run_id)

    assert finished.status == "failed"
    assert finished.tool_failures == 1
    failed_event = next(
        event
        for event in service.list_events(created.run_id)
        if event.type == "tool_completed"
    )
    assert failed_event.payload == {
        "name": "read_file",
        "ok": False,
        "error_category": "not_found",
    }
    assert b"secret-token/private.txt" not in (
        root / "agent_workspace.sqlite3"
    ).read_bytes()
    await service.shutdown()


@pytest.mark.asyncio
async def test_shadow_service_stop_and_restart_reconciliation_are_truthful(
    tmp_path: Path,
) -> None:
    root = tmp_path / "agent-workspace"
    port = _BlockingPort()
    service = EngineShadowService(
        store=EngineShadowStore(root),
        port=port,
        gateway=_FakeGateway(),
        catalog_provider=_catalog,
    )
    created = await service.create_run(EngineShadowRunCreate(objective="Long task"))

    stopped = await service.stop_run(created.run_id)
    assert stopped.status == "stopped"
    await service.shutdown()

    with sqlite3.connect(root / "agent_workspace.sqlite3") as connection:
        connection.execute(
            "UPDATE agent_upstream_shadow_runs SET status='running', finished_at=NULL WHERE run_id=?",
            (created.run_id,),
        )
        connection.commit()
    reloaded = EngineShadowStore(root)
    assert reloaded.get_run(created.run_id).status == "interrupted"


@pytest.mark.asyncio
async def test_shadow_service_rejects_unavailable_registered_model(tmp_path: Path) -> None:
    service = EngineShadowService(
        store=EngineShadowStore(tmp_path / "agent-workspace"),
        port=_CandidatePort(),
        gateway=_FakeGateway(),
        catalog_provider=lambda: _catalog(available=False),
    )

    with pytest.raises(EngineShadowServiceError) as captured:
        await service.create_run(EngineShadowRunCreate(objective="No channel"))

    assert captured.value.code == "model_unavailable"
    assert service.list_runs() == []
