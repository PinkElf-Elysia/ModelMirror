from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from server.coding_runtime.runner_mcp import RunnerMcpServer
from server.coding_runtime.worker import CodingWorkerError, build_opencode_config


def _payload(response: dict) -> dict:
    return json.loads(response["result"]["content"][0]["text"])


async def _call(
    server: RunnerMcpServer,
    name: str,
    arguments: dict[str, object],
) -> dict:
    response = await server.dispatch(
        {
            "jsonrpc": "2.0",
            "id": "random-k7m4",
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )
    assert response is not None
    return response


@pytest.mark.asyncio
async def test_file_only_mcp_lists_delete_and_move_tools(tmp_path: Path) -> None:
    server = RunnerMcpServer(
        workspace_root=str(tmp_path),
        file_operations_enabled=True,
    )

    response = await server.dispatch(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    )

    assert response is not None
    tools = response["result"]["tools"]
    assert [tool["name"] for tool in tools] == [
        "delete_text_file",
        "move_text_file",
    ]
    assert all(
        tool["inputSchema"]["additionalProperties"] is False for tool in tools
    )


@pytest.mark.asyncio
async def test_command_and_file_tools_share_only_the_internal_mcp(tmp_path: Path) -> None:
    server = RunnerMcpServer(
        socket_path="/tmp/modelmirror-runner-test.sock",
        token="r" * 32,
        workspace_root=str(tmp_path),
        file_operations_enabled=True,
    )

    response = await server.dispatch(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    )

    assert response is not None
    assert [tool["name"] for tool in response["result"]["tools"]] == [
        "run_project_command",
        "delete_text_file",
        "move_text_file",
    ]


@pytest.mark.asyncio
async def test_delete_and_move_only_change_temporary_workspace(tmp_path: Path) -> None:
    removable = tmp_path / "remove-q7m2.txt"
    removable.write_text("delete random q7m2\n", encoding="utf-8")
    movable = tmp_path / "move-r8v3.txt"
    movable.write_text("move random r8v3\n", encoding="utf-8")
    server = RunnerMcpServer(
        workspace_root=str(tmp_path),
        file_operations_enabled=True,
    )

    deleted = await _call(
        server,
        "delete_text_file",
        {"path": "/workspace/remove-q7m2.txt"},
    )
    moved = await _call(
        server,
        "move_text_file",
        {"source": "move-r8v3.txt", "destination": "nested/moved-r8v3.txt"},
    )

    assert deleted["result"]["isError"] is False
    assert _payload(deleted)["result"] == {
        "path": "remove-q7m2.txt",
        "status": "deleted",
    }
    assert moved["result"]["isError"] is False
    assert _payload(moved)["result"]["status"] == "moved"
    assert not removable.exists()
    assert not movable.exists()
    assert (tmp_path / "nested" / "moved-r8v3.txt").read_text(
        encoding="utf-8"
    ) == "move random r8v3\n"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("delete_text_file", {"path": "../outside.txt"}),
        ("delete_text_file", {"path": "/tmp/outside.txt"}),
        ("delete_text_file", {"path": "C:/outside.txt"}),
        ("delete_text_file", {"path": ".env"}),
        ("delete_text_file", {"path": "folder"}),
        (
            "move_text_file",
            {"source": "safe.txt", "destination": "existing.txt"},
        ),
        (
            "move_text_file",
            {"source": "safe.txt", "destination": "safe.txt"},
        ),
    ],
)
async def test_file_tools_reject_unsafe_paths_directories_and_overwrite(
    tmp_path: Path,
    tool: str,
    arguments: dict[str, str],
) -> None:
    (tmp_path / "safe.txt").write_text("safe\n", encoding="utf-8")
    (tmp_path / "existing.txt").write_text("keep\n", encoding="utf-8")
    (tmp_path / "folder").mkdir()
    server = RunnerMcpServer(
        workspace_root=str(tmp_path),
        file_operations_enabled=True,
    )

    response = await _call(server, tool, arguments)

    assert response["result"]["isError"] is True
    assert _payload(response)["result"]["status"] == "file_operation_rejected"
    assert (tmp_path / "safe.txt").read_text(encoding="utf-8") == "safe\n"
    assert (tmp_path / "existing.txt").read_text(encoding="utf-8") == "keep\n"


@pytest.mark.asyncio
async def test_file_tools_reject_binary_and_symlink_sources(tmp_path: Path) -> None:
    (tmp_path / "binary.bin").write_bytes(b"safe\x00unsafe")
    target = tmp_path / "target.txt"
    target.write_text("target\n", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        os.symlink(target, link)
    except (NotImplementedError, OSError):
        pytest.skip("This host does not allow unprivileged symbolic links")
    server = RunnerMcpServer(
        workspace_root=str(tmp_path),
        file_operations_enabled=True,
    )

    binary = await _call(server, "delete_text_file", {"path": "binary.bin"})
    symlink = await _call(server, "delete_text_file", {"path": "link.txt"})

    assert binary["result"]["isError"] is True
    assert symlink["result"]["isError"] is True
    assert (tmp_path / "binary.bin").exists()
    assert target.read_text(encoding="utf-8") == "target\n"


def test_opencode_config_exposes_file_tools_only_in_draft_mode() -> None:
    config = build_opencode_config(
        "provider/model",
        "draft",
        file_operations_enabled=True,
    )

    assert config["permission"]["modelmirror-runner_*"] == "allow"
    assert config["permission"]["bash"] == "deny"
    assert config["mcp"]["modelmirror-runner"]["environment"] == {
        "MODELMIRROR_WORKSPACE": "/workspace",
        "MODELMIRROR_FILE_OPERATIONS": "1",
    }

    with pytest.raises(CodingWorkerError) as error:
        build_opencode_config(
            "provider/model",
            "readonly",
            file_operations_enabled=True,
        )
    assert error.value.code == "not_configured"
