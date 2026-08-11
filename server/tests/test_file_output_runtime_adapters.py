from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from server.file_assets.output_service import FileOutputService
from server.file_assets.service import FileAssetService
from server.sandbox_sidecar.engine import SandboxEngine
from server.xpert_runtime import (
    BrowserSessionStore,
    BrowserToolsetProvider,
    LocalSandboxClient,
    MCPToolsetProvider,
    RuntimeApprovalStore,
    RuntimeToolCall,
    SandboxToolsetProvider,
    SandboxWorkspaceStore,
)


class _Skills:
    def list_installed_skills(self) -> list[object]:
        return []


class _BrowserClient:
    def __init__(self, root: Path) -> None:
        self.root = root

    async def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("action") == "ensure_session":
            return {"ok": True, "page": {}}
        artifact_id = str(payload["artifact_id"])
        relative = f"files/{artifact_id}.png"
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\x89PNG\r\n\x1a\noutput")
        return {
            "ok": True,
            "page": {"url": "", "domain": "", "title": ""},
            "artifact": {
                "artifact_id": artifact_id,
                "relative_path": relative,
                "filename": "capture.png",
                "size_bytes": target.stat().st_size,
                "content_type": "image/png",
            },
        }


class _ToolRegistry:
    async def list_tools(self) -> list[dict[str, object]]:
        return [
            {
                "name": "publish_report",
                "description": "Publish one explicit report.",
                "input_schema": {"type": "object"},
                "session_id": "session-1",
                "server_id": "server-1",
            }
        ]


class _MCPManager:
    async def call_tool(
        self, session_id: str, tool_name: str, arguments: dict[str, object]
    ) -> object:
        assert session_id == "session-1"
        assert tool_name == "publish_report"
        return SimpleNamespace(
            content=[
                {
                    "type": "resource",
                    "resource": {
                        "blob": base64.b64encode(b"MCP report\n").decode("ascii"),
                        "mimeType": "text/plain",
                    },
                    "_meta": {
                        "modelmirror/outputArtifact": {
                            "artifact_id": "artifact-1",
                            "filename": "report.txt",
                        }
                    },
                },
                {
                    "type": "resource_link",
                    "uri": "https://example.com/not-fetched.txt",
                },
            ]
        )


def _output_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> FileOutputService:
    monkeypatch.setenv("FILE_ASSET_STORE_MODE", "native")
    monkeypatch.setenv("FILE_OUTPUT_ASSETS_ENABLED", "true")
    service = FileOutputService(
        FileAssetService(storage_dir=tmp_path / "file-assets", mode="native")
    )
    monkeypatch.setattr(
        "server.file_assets.output_service.get_file_output_service", lambda: service
    )
    return service


@pytest.mark.asyncio
async def test_sandbox_publish_registers_one_scoped_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_service = _output_service(tmp_path, monkeypatch)
    workspace_root = tmp_path / "workspaces"
    engine = SandboxEngine(workspace_root, require_landlock=False)
    provider = SandboxToolsetProvider(
        SandboxWorkspaceStore(tmp_path / "sandbox", workspace_root=workspace_root),
        LocalSandboxClient(engine),
        skill_manager=_Skills(),
    )
    metadata = {
        "xpert_id": "xpert-1",
        "conversation_id": "conversation-1",
        "run_id": "run-1",
        "node_id": "node-1",
        "iteration": 1,
    }
    await provider.call_tool(
        RuntimeToolCall(
            "sandbox_write_file",
            {"path": "work/report.md", "content": "# Report"},
            metadata,
        )
    )
    published = await provider.call_tool(
        RuntimeToolCall(
            "sandbox_publish_artifact",
            {"path": "work/report.md"},
            {**metadata, "iteration": 2},
        )
    )
    assert published.metadata["file_output"]["status"] == "completed"
    assert published.metadata["file_output"]["scope_id"] == (
        "xpert:xpert-1:conversation-1"
    )
    assert output_service.list_outputs(
        purpose="agent", scope_id="xpert:xpert-1:conversation-1"
    ).total == 1


@pytest.mark.asyncio
async def test_browser_screenshot_registers_only_held_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_service = _output_service(tmp_path, monkeypatch)
    data_root = tmp_path / "browser-data"
    provider = BrowserToolsetProvider(
        BrowserSessionStore(tmp_path / "browser", data_root=data_root),
        _BrowserClient(data_root),
        RuntimeApprovalStore(tmp_path / "approvals"),
    )
    result = await provider.call_tool(
        RuntimeToolCall(
            "browser_screenshot",
            {},
            {
                "workflow_id": "workflow-1",
                "task_id": "task-1",
                "run_id": "run-1",
                "node_id": "node-1",
                "iteration": 1,
            },
        )
    )
    assert result.metadata["file_output"]["status"] == "completed"
    assert result.metadata["file_output"]["scope_id"] == "workflow:workflow-1"
    assert output_service.list_outputs(
        purpose="workflow", scope_id="workflow:workflow-1"
    ).total == 1


@pytest.mark.asyncio
async def test_mcp_registers_marked_embedded_blob_but_not_resource_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_service = _output_service(tmp_path, monkeypatch)
    provider = MCPToolsetProvider(_ToolRegistry(), _MCPManager())
    result = await provider.call_tool(
        RuntimeToolCall(
            "publish_report",
            {},
            {
                "workflow_id": "workflow-1",
                "run_id": "run-1",
                "node_id": "node-1",
            },
        )
    )
    assert len(result.metadata["file_outputs"]) == 1
    assert result.metadata["file_outputs"][0]["status"] == "completed"
    assert output_service.list_outputs(
        purpose="workflow", scope_id="workflow:workflow-1"
    ).total == 1


@pytest.mark.asyncio
async def test_disabled_flag_preserves_legacy_sandbox_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FILE_OUTPUT_ASSETS_ENABLED", "false")
    workspace_root = tmp_path / "workspaces"
    provider = SandboxToolsetProvider(
        SandboxWorkspaceStore(tmp_path / "sandbox", workspace_root=workspace_root),
        LocalSandboxClient(SandboxEngine(workspace_root, require_landlock=False)),
        skill_manager=_Skills(),
    )
    metadata = {"task_id": "task-1", "run_id": "run-1", "node_id": "node-1"}
    await provider.call_tool(
        RuntimeToolCall(
            "sandbox_write_file",
            {"path": "work/report.txt", "content": "report"},
            metadata,
        )
    )
    result = await provider.call_tool(
        RuntimeToolCall(
            "sandbox_publish_artifact", {"path": "work/report.txt"}, metadata
        )
    )
    assert "file_output" not in result.metadata
