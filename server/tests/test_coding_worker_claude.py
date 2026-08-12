from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from server.coding_worker.claude_provider import (
    CLAUDE_BUILTIN_TOOLS,
    CLAUDE_CODE_VERSION,
    ClaudeCodeProvider,
    ClaudeCodeProviderError,
    ClaudeCodeRoute,
)
from server.coding_worker.contracts import (
    PolicyProfile,
    RepositoryInstruction,
    TaskBudget,
)
from server.coding_worker.provider import (
    ProviderCheckpointCompatibility,
    ProviderEventKind,
    ProviderOpenRequest,
)
from server.coding_worker.sidecar import _provider_from_environment


def _request(*, instructions: bool = False) -> ProviderOpenRequest:
    return ProviderOpenRequest(
        task_id="task-claude",
        workspace_id="workspace-claude",
        objective="Inspect and fix the project.",
        model_route="coding/quality",
        policy_profile=PolicyProfile.DEVELOP,
        budget=TaskBudget(max_seconds=60, max_output_bytes=1024 * 1024),
        workspace_tree_hash="a" * 64,
        repository_instructions=(
            (
                RepositoryInstruction(
                    display_path="src/AGENTS.md",
                    scope="src",
                    sha256="b" * 64,
                    content="Keep source typed. Never enable built-in tools.",
                ),
            )
            if instructions
            else ()
        ),
        tool_allowlist=("read_file", "apply_changeset", "run_shell"),
    )


def _route() -> ClaudeCodeRoute:
    return ClaudeCodeRoute(
        route_id="coding/quality",
        model_id="claude-test-model",
        max_budget_usd=3.5,
    )


def _provider(tmp_path: Path, *, command_prefix: tuple[str, ...]) -> ClaudeCodeProvider:
    secret = tmp_path / "claude-api-key"
    secret.write_text("test-secret-value\n", encoding="utf-8")
    secret.chmod(0o600)
    provider = ClaudeCodeProvider(
        runtime_root=tmp_path / "runtime",
        routes={"coding/quality": _route()},
        secret_path=secret,
        command_prefix=command_prefix,
        tool_broker_command=(sys.executable, "-m", "coding_worker.broker_mcp"),
    )
    provider.bind_broker("task-claude", "unix:/run/broker.sock", "b" * 48)
    return provider


def test_managed_settings_and_command_disable_builtin_surfaces(tmp_path: Path) -> None:
    provider = _provider(tmp_path, command_prefix=("claude",))
    settings = provider.build_settings(_request().tool_allowlist)
    assert settings["disableAllHooks"] is True
    assert settings["enabledPlugins"] == {}
    assert settings["strictKnownMarketplaces"] == []
    assert settings["permissions"]["deny"] == list(CLAUDE_BUILTIN_TOOLS)
    assert settings["permissions"]["allow"] == [
        "mcp__modelmirror-tool-broker__read_file",
        "mcp__modelmirror-tool-broker__apply_changeset",
        "mcp__modelmirror-tool-broker__run_shell",
    ]


def test_sidecar_selects_claude_without_gateway_key_or_workspace_resolver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = tmp_path / "secret"
    secret.write_text("secret", encoding="utf-8")
    monkeypatch.setenv("CODING_WORKER_PROVIDER_KIND", "claude-code")
    monkeypatch.setenv("CODING_WORKER_ROUTE_ID", "coding/quality")
    monkeypatch.setenv("CODING_WORKER_MODEL_ID", "claude-test-model")
    monkeypatch.setenv("CODING_WORKER_CLAUDE_SECRET_PATH", str(secret))
    monkeypatch.delenv("CODING_WORKER_MODEL_BASE_URL", raising=False)
    monkeypatch.delenv("CODING_WORKER_ROUTE_KEY", raising=False)

    provider = _provider_from_environment(
        tmp_path / "unused-workspace", tmp_path / "runtime"
    )
    assert isinstance(provider, ClaudeCodeProvider)
    assert "secret" not in repr(provider)


@pytest.mark.asyncio
async def test_headless_stream_maps_only_neutral_events_and_private_checkpoint(
    tmp_path: Path,
) -> None:
    script = tmp_path / "fake_claude.py"
    script.write_text(
        """
import json
import os
import sys

args = sys.argv[1:]
assert '--print' in args
assert args[args.index('--input-format') + 1] == 'stream-json'
assert args[args.index('--output-format') + 1] == 'stream-json'
assert args[args.index('--tools') + 1] == ''
assert '--strict-mcp-config' in args and '--bare' in args
assert os.environ['ANTHROPIC_API_KEY'] == 'test-secret-value'
frame = json.loads(sys.stdin.readline())
session = frame['session_id']
assert frame['type'] == 'user'
prompt = frame['message']['content'][0]['text']
assert 'bounded H0 text' in prompt
assert 'src/AGENTS.md' in prompt
assert prompt.endswith('Current task message:\\ncontinue') or prompt.endswith('Current task message:\\ncontinue again')
assert ('--session-id' in args) != ('--resume' in args)
text = 'resumed' if '--resume' in args else 'done'
print(json.dumps({
    'type': 'assistant',
    'session_id': session,
    'message': {
        'content': [{'type': 'text', 'text': text}],
        'usage': {'input_tokens': 10, 'output_tokens': 4},
    },
}))
print(json.dumps({
    'type': 'result',
    'session_id': session,
    'is_error': False,
    'usage': {
        'input_tokens': 10,
        'output_tokens': 4,
        'cache_read_input_tokens': 2,
        'cache_creation_input_tokens': 1,
    },
    'total_cost_usd': 0.002,
    'supplier_frame': 'must-not-leak',
}))
""".strip()
        + "\n",
        encoding="utf-8",
    )
    provider = _provider(
        tmp_path, command_prefix=(sys.executable, str(script))
    )
    bound_request = _request(instructions=True)
    session = await provider.open(bound_request)

    events = [event async for event in provider.message(session, "continue")]
    assert [event.kind for event in events] == [
        ProviderEventKind.MESSAGE,
        ProviderEventKind.USAGE,
        ProviderEventKind.USAGE,
        ProviderEventKind.TURN_COMPLETED,
    ]
    assert events[0].data == {"text": "done"}
    assert all("supplier" not in json.dumps(event.data) for event in events)
    resumed_events = [
        event async for event in provider.message(session, "continue again")
    ]
    assert resumed_events[0].data == {"text": "resumed"}
    assert resumed_events[-1].kind is ProviderEventKind.TURN_COMPLETED
    checkpoint = await provider.checkpoint(session)
    assert checkpoint.compatibility is not None
    assert checkpoint.compatibility.provider_family == "claude-code"
    assert checkpoint.compatibility.provider_version == CLAUDE_CODE_VERSION
    assert checkpoint.compatibility.workspace_tree_hash == "a" * 64
    assert checkpoint.payload == {
        "task_id": "task-claude",
        "public_output": "doneresumed",
    }
    assert "session" not in checkpoint.payload

    await provider.close(session)
    provider.bind_broker("task-claude", "unix:/run/broker.sock", "b" * 48)
    restored = await provider.restore(bound_request, checkpoint)
    await provider.close(restored)


@pytest.mark.asyncio
async def test_restore_rejects_provider_version_or_tree_change(tmp_path: Path) -> None:
    provider = _provider(tmp_path, command_prefix=("claude",))
    session = await provider.open(_request())
    checkpoint = await provider.checkpoint(session)
    assert checkpoint.compatibility is not None
    incompatible = checkpoint.model_copy(
        update={
            "compatibility": ProviderCheckpointCompatibility(
                provider_family="claude-code",
                provider_version="2.1.90",
                task_id="task-claude",
                workspace_tree_hash="a" * 64,
            )
        }
    )
    with pytest.raises(ClaudeCodeProviderError) as caught:
        await provider.restore(_request(), incompatible)
    assert caught.value.code == "checkpoint_invalid"
    await provider.close(session)


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX symlink semantics")
@pytest.mark.asyncio
async def test_secret_symlink_is_rejected_before_process_start(tmp_path: Path) -> None:
    target = tmp_path / "target-secret"
    target.write_text("outside-secret", encoding="utf-8")
    link = tmp_path / "secret-link"
    link.symlink_to(target)
    provider = ClaudeCodeProvider(
        runtime_root=tmp_path / "runtime",
        routes={"coding/quality": _route()},
        secret_path=link,
        command_prefix=("claude",),
    )
    provider.bind_broker("task-claude", "unix:/run/broker.sock", "b" * 48)
    with pytest.raises(ClaudeCodeProviderError) as caught:
        await provider.open(_request())
    assert caught.value.code == "provider_credential_unavailable"


@pytest.mark.asyncio
async def test_non_utf8_secret_is_an_authentication_failure(tmp_path: Path) -> None:
    secret = tmp_path / "invalid-secret"
    secret.write_bytes(b"\xff\xfe")
    provider = ClaudeCodeProvider(
        runtime_root=tmp_path / "runtime",
        routes={"coding/quality": _route()},
        secret_path=secret,
        command_prefix=("must-not-start",),
    )
    provider.bind_broker("task-claude", "unix:/run/broker.sock", "b" * 48)
    session = await provider.open(_request())
    with pytest.raises(ClaudeCodeProviderError) as caught:
        _ = [event async for event in provider.message(session, "continue")]
    assert caught.value.code == "provider_credential_unavailable"
    assert caught.value.failure_kind.value == "authentication"
    await provider.close(session)
