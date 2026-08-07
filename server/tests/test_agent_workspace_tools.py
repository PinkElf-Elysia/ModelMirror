from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from PIL import Image

from server.agent_workspace.gateway import OpenAICompatibleGateway
from server.agent_workspace.tools import (
    BuiltinToolRunner,
    ProcessRegistry,
    ToolExecutionError,
)


def runner(*, commands: bool = False) -> BuiltinToolRunner:
    return BuiltinToolRunner(
        gateway=OpenAICompatibleGateway(gateway_url="https://unused", gateway_key="unused"),
        process_registry=ProcessRegistry(
            allow_commands=commands,
            command_prefix=[] if commands else None,
        ),
    )


class VisionGateway:
    async def describe_image(self, **kwargs):
        assert kwargs["data_url"].startswith("data:image/png;base64,")
        return "A blue 16 by 9 image."


@pytest.mark.asyncio
async def test_file_tools_are_atomic_and_workspace_scoped(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tools = runner()

    written = await tools.execute(
        tool_name="write_file",
        arguments={"file_path": "notes/result.txt", "content": "alpha beta"},
        session_id="session-a",
        workspace=workspace,
        timeout_ms=30_000,
        max_output_length=16_000,
    )
    assert json.loads(written.output)["path"] == "notes/result.txt"

    edited = await tools.execute(
        tool_name="edit_file",
        arguments={
            "file_path": "notes/result.txt",
            "old_text": "beta",
            "new_text": "gamma",
        },
        session_id="session-a",
        workspace=workspace,
        timeout_ms=30_000,
        max_output_length=16_000,
    )
    assert json.loads(edited.output)["replacements"] == 1
    read = await tools.execute(
        tool_name="read_file",
        arguments={"file_path": "notes/result.txt"},
        session_id="session-a",
        workspace=workspace,
        timeout_ms=30_000,
        max_output_length=64_000,
    )
    assert "alpha gamma" in read.output

    with pytest.raises(ToolExecutionError, match="Workspace-relative"):
        await tools.execute(
            tool_name="read_file",
            arguments={"file_path": "../outside.txt"},
            session_id="session-a",
            workspace=workspace,
            timeout_ms=30_000,
            max_output_length=64_000,
        )


@pytest.mark.asyncio
async def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("Windows symlink creation requires elevated privileges")
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    (workspace / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ToolExecutionError, match="escape"):
        await runner().execute(
            tool_name="read_file",
            arguments={"file_path": "escape/secret.txt"},
            session_id="session-a",
            workspace=workspace,
            timeout_ms=30_000,
            max_output_length=64_000,
        )


@pytest.mark.asyncio
async def test_read_image_returns_verified_metadata(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    Image.new("RGB", (16, 9), color=(20, 120, 220)).save(workspace / "sample.png")

    result = await runner().execute(
        tool_name="read_image",
        arguments={"file_path": "sample.png"},
        session_id="session-a",
        workspace=workspace,
        timeout_ms=60_000,
        max_output_length=16_000,
    )
    payload = json.loads(result.output)
    assert payload["width"] == 16
    assert payload["height"] == 9
    assert payload["data_url"].startswith("data:image/png;base64,")

    vision_tools = BuiltinToolRunner(
        gateway=VisionGateway(),  # type: ignore[arg-type]
        process_registry=ProcessRegistry(allow_commands=False),
    )
    described = await vision_tools.execute(
        tool_name="describe_image",
        arguments={"file_path": "sample.png", "prompt": "What is shown?"},
        session_id="session-a",
        workspace=workspace,
        timeout_ms=90_000,
        max_output_length=16_000,
    )
    assert described.output == "A blue 16 by 9 image."


@pytest.mark.asyncio
async def test_process_registry_enforces_session_ownership(tmp_path: Path) -> None:
    if os.name != "posix":
        pytest.skip("Command runtime is Linux-container only")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tools = runner(commands=True)
    started = await tools.execute(
        tool_name="exec_command",
        arguments={"command": "sleep 1; printf done", "yield_time_ms": 250},
        session_id="session-a",
        workspace=workspace,
        timeout_ms=120_000,
        max_output_length=16_000,
    )
    process_id = json.loads(started.output)["process_id"]
    assert process_id

    with pytest.raises(ToolExecutionError, match="does not belong"):
        await tools.execute(
            tool_name="input_command",
            arguments={"process_id": process_id},
            session_id="session-b",
            workspace=workspace,
            timeout_ms=130_000,
            max_output_length=16_000,
        )

    await tools.process_registry.terminate_session("session-a")
