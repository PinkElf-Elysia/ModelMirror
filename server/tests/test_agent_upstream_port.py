from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import os
import sys
import time
from pathlib import Path

import pytest

from server.agent_upstream.models import ENGINE_PROTOCOL, UPSTREAM_REVISION
from server.agent_upstream.port import (
    EngineProtocolError,
    EngineShadowRunSpec,
    EngineUnavailableError,
    NodeUpstreamEnginePort,
)
from server.agent_upstream.tools import SHADOW_TOOL_DEFINITIONS, UpstreamShadowToolBridge


def _spec(tmp_path: Path, *, run_id: str = "run-port") -> EngineShadowRunSpec:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    goal = workspace / ".modelmirror" / "GOAL.yaml"
    goal.parent.mkdir()
    goal.write_text("{}", encoding="utf-8")
    return EngineShadowRunSpec(
        run_id=run_id,
        session_id="session-port",
        objective="Build a static candidate",
        workspace_dir=workspace,
        goal_file_path=goal,
        system_prompt="system",
        thinking_level="medium",
        token_budget=100_000,
        max_goal_rounds=12,
        max_task_turns=100,
        model_base_id="test-model",
        model_context_window=128_000,
        tools=(),
        watchdog_seconds=10,
        max_prestart_retries=0,
    )


def _write_worker(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")


def _command(script: Path):
    return lambda _spec: [sys.executable, "-u", str(script)]


@pytest.mark.asyncio
async def test_stop_waits_for_worker_terminal_acknowledgement(tmp_path: Path) -> None:
    script = tmp_path / "worker.py"
    _write_worker(
        script,
        f'''import json, sys, time
protocol = {ENGINE_PROTOCOL!r}
revision = {UPSTREAM_REVISION!r}
seq = 1
def send(kind, payload):
    global seq
    print(json.dumps({{"protocol": protocol, "seq": seq, "type": kind, "payload": payload}}), flush=True)
    seq += 1
send("worker.hello", {{"node_version": "v24.19.0", "upstream_revision": revision, "capabilities": ["read_file", "write_file", "edit_file"]}})
for line in sys.stdin:
    frame = json.loads(line)
    if frame["type"] == "run.start":
        run_id = frame["payload"]["run_id"]
        send("run.started", {{"run_id": run_id}})
    elif frame["type"] == "run.cancel":
        time.sleep(0.15)
        send("run.finished", {{"status": "stopped", "goal": {{}}, "stats": {{}}}})
    elif frame["type"] == "run.shutdown":
        break
''',
    )
    port = NodeUpstreamEnginePort(
        package_root=tmp_path,
        command_factory=_command(script),
    )
    spec = _spec(tmp_path)
    started = asyncio.Event()

    async def on_event(kind: str, _payload: dict[str, object]) -> None:
        if kind == "worker_started":
            started.set()

    run = asyncio.create_task(
        port.start_run(
            spec,
            on_event=on_event,
            execute_model=lambda _request: {},
            execute_tool=lambda _request: {},
        )
    )
    await asyncio.wait_for(started.wait(), 2)
    before = time.monotonic()
    await port.stop_run(spec.run_id)
    elapsed = time.monotonic() - before
    result = await asyncio.wait_for(run, 2)

    assert elapsed >= 0.10
    assert result.status == "stopped"


@pytest.mark.asyncio
async def test_protocol_sequence_gap_fails_closed(tmp_path: Path) -> None:
    script = tmp_path / "bad-worker.py"
    _write_worker(
        script,
        f'''import json
print(json.dumps({{"protocol": {ENGINE_PROTOCOL!r}, "seq": 2, "type": "worker.hello", "payload": {{"node_version": "v24.19.0", "upstream_revision": {UPSTREAM_REVISION!r}, "capabilities": ["read_file", "write_file", "edit_file"]}}}}), flush=True)
''',
    )
    port = NodeUpstreamEnginePort(
        package_root=tmp_path,
        command_factory=_command(script),
    )

    with pytest.raises(EngineProtocolError, match="sequence mismatch"):
        await port.start_run(
            _spec(tmp_path, run_id="bad-seq"),
            on_event=lambda _kind, _payload: None,
            execute_model=lambda _request: {},
            execute_tool=lambda _request: {},
        )


@pytest.mark.asyncio
async def test_started_worker_crash_after_model_request_is_never_restarted(
    tmp_path: Path,
) -> None:
    script = tmp_path / "post-then-crash-worker.py"
    marker = tmp_path / "worker-starts.txt"
    _write_worker(
        script,
        f'''import json, pathlib, sys
protocol = {ENGINE_PROTOCOL!r}
revision = {UPSTREAM_REVISION!r}
marker = pathlib.Path({str(marker)!r})
marker.write_text(marker.read_text() + "start\\n" if marker.exists() else "start\\n")
seq = 1
def send(kind, payload):
    global seq
    print(json.dumps({{"protocol": protocol, "seq": seq, "type": kind, "payload": payload}}), flush=True)
    seq += 1
send("worker.hello", {{"node_version": "v24.19.0", "upstream_revision": revision, "capabilities": ["read_file", "write_file", "edit_file"]}})
for line in sys.stdin:
    frame = json.loads(line)
    if frame["type"] == "run.start":
        run_id = frame["payload"]["run_id"]
        send("run.started", {{"run_id": run_id}})
        send("model.request", {{"run_id": run_id, "request_id": "model-once", "new_messages": []}})
    elif frame["type"] == "model.response":
        raise SystemExit(23)
''',
    )
    port = NodeUpstreamEnginePort(
        package_root=tmp_path,
        command_factory=_command(script),
    )
    spec = replace(_spec(tmp_path, run_id="post-then-crash"), max_prestart_retries=3)
    model_requests: list[str] = []

    async def execute_model(request):
        model_requests.append(request.request_id)
        return {"segments": [], "usage": {}, "outcome": {"status": "completed"}}

    with pytest.raises(EngineUnavailableError):
        await port.start_run(
            spec,
            on_event=lambda _kind, _payload: None,
            execute_model=execute_model,
            execute_tool=lambda _request: {},
        )

    assert model_requests == ["model-once"]
    assert marker.read_text(encoding="utf-8").splitlines() == ["start"]


def test_worker_environment_excludes_gateway_and_service_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", "safe-path")
    monkeypatch.setenv("LLM_GATEWAY_KEY", "must-not-cross")
    monkeypatch.setenv("OPENROUTER_API_KEY", "must-not-cross")
    monkeypatch.setenv("MODEL_MIRROR_CREDENTIAL_MASTER_KEY", "must-not-cross")
    monkeypatch.setenv("DATABASE_URL", "must-not-cross")

    environment = NodeUpstreamEnginePort()._minimal_environment()

    assert environment["PATH"] == "safe-path"
    assert "LLM_GATEWAY_KEY" not in environment
    assert "OPENROUTER_API_KEY" not in environment
    assert "MODEL_MIRROR_CREDENTIAL_MASTER_KEY" not in environment
    assert "DATABASE_URL" not in environment


@pytest.mark.asyncio
async def test_python_port_runs_the_deployed_node24_worker_and_upstream_goal(
    tmp_path: Path,
) -> None:
    node_executable = Path(os.getenv("AGENT_UPSTREAM_NODE_EXECUTABLE", ""))
    worker_path = Path(os.getenv("AGENT_UPSTREAM_WORKER_PATH", ""))
    package_root = Path("/app/agent_upstream")
    if not node_executable.is_file() or not worker_path.is_file() or not package_root.is_dir():
        pytest.skip("deployed Node 24 upstream worker is unavailable")

    spec = _spec(tmp_path, run_id="real-node-worker")
    spec = EngineShadowRunSpec(
        run_id=spec.run_id,
        session_id=spec.session_id,
        objective="Build index.html and mark the Goal complete.",
        workspace_dir=spec.workspace_dir,
        goal_file_path=spec.goal_file_path,
        system_prompt="Use the host file tools. Verify the candidate before completing the Goal.",
        thinking_level=spec.thinking_level,
        token_budget=spec.token_budget,
        max_goal_rounds=4,
        max_task_turns=10,
        model_base_id=spec.model_base_id,
        model_context_window=spec.model_context_window,
        tools=SHADOW_TOOL_DEFINITIONS,
        watchdog_seconds=30,
        max_prestart_retries=0,
    )
    bridge = UpstreamShadowToolBridge()
    model_turn = 0
    events: list[str] = []

    async def execute_model(_request):
        nonlocal model_turn
        model_turn += 1
        if model_turn == 1:
            segments = [
                {
                    "type": "tool_call",
                    "name": "write_file",
                    "arguments": json.dumps(
                        {
                            "file_path": "index.html",
                            "content": "<!doctype html><title>Ready</title>",
                        }
                    ),
                    "tool_call_id": "tool-1",
                }
            ]
        elif model_turn == 2:
            segments = [
                {
                    "type": "tool_call",
                    "name": "edit_file",
                    "arguments": json.dumps(
                        {
                            "file_path": ".modelmirror/GOAL.yaml",
                            "old_string": "status: active",
                            "new_string": "status: complete",
                        }
                    ),
                    "tool_call_id": "tool-2",
                }
            ]
        else:
            segments = [{"type": "text", "text": "Candidate verified and ready."}]
        return {
            "segments": segments,
            "usage": {"cache_read": 0, "cache_write": 0, "output": 64, "total": 64},
            "outcome": {"status": "completed"},
        }

    async def execute_tool(request):
        result = await bridge.execute(
            tool_name=request.payload["name"],
            arguments=request.payload["arguments"],
            workspace=spec.workspace_dir,
        )
        return {"output": result.output, "metadata": result.metadata}

    port = NodeUpstreamEnginePort(
        package_root=package_root,
        node_executable=str(node_executable),
    )
    result = await port.start_run(
        spec,
        on_event=lambda kind, _payload: events.append(kind),
        execute_model=execute_model,
        execute_tool=execute_tool,
    )

    assert result.status == "candidate_ready"
    assert model_turn >= 2
    assert "worker_started" in events
    assert (spec.workspace_dir / "index.html").read_text(encoding="utf-8") == (
        "<!doctype html><title>Ready</title>"
    )
    assert "status: complete" in spec.goal_file_path.read_text(encoding="utf-8")
