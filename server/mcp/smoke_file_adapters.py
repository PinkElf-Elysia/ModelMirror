"""Docker smoke harness for Wave 3 file-backed catalog adapters.

Run this from the server container while the ``mcp-files`` sidecar and its
shared volumes are mounted.  The harness creates disposable, server-owned
workspaces and talks to every adapter through the same fixed stdio proxy used
by the catalog service.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

try:
    from server.mcp.manager import MCPClientManager
    from server.mcp.workspace import MCPCatalogWorkspaceStore
except ModuleNotFoundError as error:
    if error.name != "server":
        raise
    # The production image copies ``server/mcp`` to ``/app/mcp`` and starts
    # Python from ``/app``. Keep the same harness runnable in that layout.
    from mcp.manager import MCPClientManager
    from mcp.workspace import MCPCatalogWorkspaceStore


PROXY_PATH = Path(__file__).resolve().with_name("file_proxy.py")
EXPECTED_TOOLS = {
    "basic-memory-mcp": {
        "read_note", "read_content", "view_note", "search_notes", "search",
        "fetch", "recent_activity", "list_directory", "build_context",
        "basic_memory_diagnostics", "write_note", "edit_note", "move_note",
    },
    "excel-mcp-server": {
        "read_excel", "get_excel_info", "get_sheet_names", "analyze_excel",
        "filter_excel", "pivot_table", "data_summary", "export_chart",
        "write_excel", "update_excel",
    },
    "git-mcp": {
        "git_status", "git_diff_unstaged", "git_diff_staged", "git_diff",
        "git_log", "git_show", "git_branch",
    },
    "markitdown-mcp": {"convert_to_markdown"},
}
EXPECTED_SCHEMA_MARKERS = {
    "excel-mcp-server": {
        "read_excel": {"file_id": "workspace-file"},
        "export_chart": {
            "file_id": "workspace-file",
            "artifact_name": "artifact-name",
        },
        "update_excel": {
            "file_id": "workspace-file",
            "artifact_name": "artifact-name",
        },
    },
    "markitdown-mcp": {
        "convert_to_markdown": {
            "file_id": "workspace-file",
            "artifact_name": "artifact-name",
        },
    },
}


def _serialized(result: Any) -> dict[str, Any]:
    value = result.model_dump(mode="json", exclude_none=True)
    if value.get("isError") or value.get("is_error"):
        raise RuntimeError(json.dumps(value, ensure_ascii=False)[:1_000])
    return value


def _upload_tree(
    store: MCPCatalogWorkspaceStore,
    project_id: str,
    workspace_id: str,
    root: Path,
) -> None:
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            relative = path.relative_to(root).as_posix()
            store.add_upload(
                project_id,
                workspace_id,
                filename=path.name,
                relative_path=relative,
                content=path.read_bytes(),
            )


def _prepare(store: MCPCatalogWorkspaceStore) -> dict[str, tuple[str, str | None]]:
    prepared: dict[str, tuple[str, str | None]] = {}

    memory = store.create("basic-memory-mcp", display_name="wave3-smoke-memory")
    store.seal(memory.project_id, memory.workspace_id)
    prepared[memory.project_id] = (memory.workspace_id, None)

    excel = store.create("excel-mcp-server", display_name="wave3-smoke-excel")
    [excel_file] = store.add_upload(
        excel.project_id,
        excel.workspace_id,
        filename="sales.csv",
        relative_path="sales.csv",
        content=b"region,amount\nNorth,12\nSouth,18\n",
    )
    store.seal(excel.project_id, excel.workspace_id)
    prepared[excel.project_id] = (excel.workspace_id, excel_file.file_id)

    converted = store.create("markitdown-mcp", display_name="wave3-smoke-markdown")
    [converted_file] = store.add_upload(
        converted.project_id,
        converted.workspace_id,
        filename="source.txt",
        relative_path="source.txt",
        content="Wave 3 本地转换 smoke".encode("utf-8"),
    )
    store.seal(converted.project_id, converted.workspace_id)
    prepared[converted.project_id] = (converted.workspace_id, converted_file.file_id)

    repository = store.create("git-mcp", display_name="wave3-smoke-git")
    with tempfile.TemporaryDirectory(prefix="wave3-git-") as temporary:
        root = Path(temporary)
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "smoke@modelmirror.local"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "ModelMirror Smoke"], cwd=root, check=True)
        (root / "README.md").write_text("# Wave 3\n", encoding="utf-8")
        (root / ".gitattributes").write_text("*.md diff=evil\n", encoding="utf-8")
        subprocess.run(["git", "config", "diff.evil.command", "false"], cwd=root, check=True)
        subprocess.run(["git", "add", "README.md", ".gitattributes"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "smoke baseline"], cwd=root, check=True)
        (root / ".git" / "hooks" / "post-checkout").write_text(
            "#!/bin/sh\nexit 99\n", encoding="utf-8"
        )
        (root / "README.md").write_text("# Wave 3\nread only\n", encoding="utf-8")
        _upload_tree(store, repository.project_id, repository.workspace_id, root)
    store.seal(repository.project_id, repository.workspace_id)
    prepared[repository.project_id] = (repository.workspace_id, None)
    return prepared


async def _connect(
    manager: MCPClientManager,
    project_id: str,
    workspace_id: str,
) -> str:
    session_id = await manager.connect_profile(
        transport="stdio",
        server_command=[sys.executable, str(PROXY_PATH), project_id],
        environment={"MCP_FILE_WORKSPACE_ID": workspace_id},
        network_policy="catalog-files-none",
        reconnect_attempts=1,
        operation_timeout=60,
    )
    tools = await manager.list_tools(session_id)
    names = {tool.name for tool in tools}
    if names != EXPECTED_TOOLS[project_id]:
        raise RuntimeError(f"schema drift for {project_id}: {sorted(names)}")
    by_name = {tool.name: tool for tool in tools}
    for tool_name, markers in EXPECTED_SCHEMA_MARKERS.get(project_id, {}).items():
        properties = by_name[tool_name].inputSchema.get("properties", {})
        for property_name, marker in markers.items():
            actual = properties.get(property_name, {}).get("x-modelmirror-input")
            if actual != marker:
                raise RuntimeError(
                    f"schema marker drift for {project_id}.{tool_name}."
                    f"{property_name}: {actual!r}"
                )
    return session_id


async def main() -> None:
    store = MCPCatalogWorkspaceStore()
    prepared = _prepare(store)
    manager = MCPClientManager(operation_timeout=60, idle_timeout_seconds=120)
    sessions: dict[str, str] = {}
    results: list[dict[str, Any]] = []
    source_hash = ""
    try:
        for project_id, (workspace_id, _) in prepared.items():
            sessions[project_id] = await _connect(manager, project_id, workspace_id)
        if len(await manager.get_sessions_summary()) != 4:
            raise RuntimeError("four-session residency smoke failed")

        memory_session = sessions["basic-memory-mcp"]
        _serialized(await manager.call_tool(memory_session, "write_note", {
            "title": "smoke-note", "content": "第 3 批持久记忆",
        }))
        search = _serialized(await manager.call_tool(memory_session, "search_notes", {
            "query": "持久记忆",
        }))
        if "smoke-note" not in json.dumps(search, ensure_ascii=False):
            raise RuntimeError("Basic Memory search did not find the written note")

        excel_workspace, excel_file = prepared["excel-mcp-server"]
        assert excel_file is not None
        source_path = store.file_path("excel-mcp-server", excel_workspace, excel_file)
        source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
        _serialized(await manager.call_tool(sessions["excel-mcp-server"], "read_excel", {
            "file_id": excel_file,
        }))
        _serialized(await manager.call_tool(sessions["excel-mcp-server"], "export_chart", {
            "file_id": excel_file,
            "x_column": "region",
            "y_column": "amount",
            "artifact_name": "sales-chart.png",
        }))
        _serialized(await manager.call_tool(sessions["excel-mcp-server"], "write_excel", {
            "data": [{"region": "North", "amount": 12}],
            "artifact_name": "sales-copy.xlsx",
        }))
        if hashlib.sha256(source_path.read_bytes()).hexdigest() != source_hash:
            raise RuntimeError("Excel source file changed")
        if len(store.discover_artifacts("excel-mcp-server", excel_workspace)) < 2:
            raise RuntimeError("Excel artifacts were not discovered")

        git_session = sessions["git-mcp"]
        for tool_name, arguments in (
            ("git_status", {}),
            ("git_diff_unstaged", {}),
            ("git_diff_staged", {}),
            ("git_log", {"max_count": 5}),
            ("git_show", {"revision": "HEAD"}),
            ("git_branch", {}),
        ):
            _serialized(await manager.call_tool(git_session, tool_name, arguments))

        markdown_workspace, markdown_file = prepared["markitdown-mcp"]
        assert markdown_file is not None
        conversion = _serialized(await manager.call_tool(
            sessions["markitdown-mcp"], "convert_to_markdown", {
                "file_id": markdown_file,
                "artifact_name": "converted.md",
            },
        ))
        if "Wave 3" not in json.dumps(conversion, ensure_ascii=False):
            raise RuntimeError("MarkItDown conversion output is missing expected text")
        rejected = await manager.call_tool(
            sessions["markitdown-mcp"], "convert_to_markdown", {
                "file_id": "https://example.com/secret",
            },
        )
        rejected_value = rejected.model_dump(mode="json", exclude_none=True)
        if not (rejected_value.get("isError") or rejected_value.get("is_error")):
            raise RuntimeError("MarkItDown accepted a network URI")
        if not store.discover_artifacts("markitdown-mcp", markdown_workspace):
            raise RuntimeError("MarkItDown artifact was not discovered")

        for project_id, session_id in list(sessions.items()):
            await manager.disconnect(session_id)
            sessions.pop(project_id, None)

        memory_workspace = prepared["basic-memory-mcp"][0]
        reconnect = await _connect(manager, "basic-memory-mcp", memory_workspace)
        sessions["basic-memory-mcp"] = reconnect
        persisted = _serialized(await manager.call_tool(reconnect, "read_note", {
            "note": "smoke-note",
        }))
        if "第 3 批持久记忆" not in json.dumps(persisted, ensure_ascii=False):
            raise RuntimeError("Basic Memory did not persist across reconnect")

        results = [
            {"project_id": project_id, "tools": sorted(EXPECTED_TOOLS[project_id])}
            for project_id in EXPECTED_TOOLS
        ]
    finally:
        for session_id in list(sessions.values()):
            try:
                await manager.disconnect(session_id)
            except Exception:
                pass
        await manager.close_all()
        for project_id, (workspace_id, _) in prepared.items():
            try:
                store.delete(project_id, workspace_id)
            except Exception:
                pass
    if await manager.get_sessions_summary():
        raise RuntimeError("file MCP sessions were not cleaned")
    print(json.dumps({
        "ok": True,
        "adapters": results,
        "concurrent_residency": "passed",
        "reconnect_persistence": "passed",
        "source_immutability": "passed",
        "network_uri_rejection": "passed",
        "cleanup": "passed",
    }, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
