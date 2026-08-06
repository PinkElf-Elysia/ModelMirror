"""Docker smoke harness for wave-2 public-network catalog adapters."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from server.mcp.manager import MCPClientManager


PROXY_PATH = Path(__file__).resolve().with_name("public_proxy.py")
EXPECTED_TOOLS = {
    "fetch-mcp": {"fetch"},
    "quickchart-mcp": {"generate_chart"},
    "geowire-mcp": {
        "search_places",
        "geocode_address",
        "reverse_geocode",
        "get_directions",
        "distance_matrix",
        "list_geo_providers",
    },
}


def _command(project_id: str) -> list[str]:
    return [sys.executable, str(PROXY_PATH), project_id]


def _representative_call(project_id: str) -> tuple[str, dict[str, Any]]:
    if project_id == "fetch-mcp":
        return "fetch", {
            "url": "https://example.com/",
            "max_length": 1_000,
        }
    if project_id == "quickchart-mcp":
        return "generate_chart", {
            "type": "bar",
            "labels": ["A", "B"],
            "datasets": [{"label": "smoke", "data": [1, 2]}],
        }
    return "geocode_address", {
        "address": "Eiffel Tower, Paris",
        "limit": 1,
    }


async def _connect_and_exercise(
    manager: MCPClientManager,
    project_id: str,
) -> tuple[dict[str, Any], str]:
    session_id = await manager.connect_profile(
        transport="stdio",
        server_command=_command(project_id),
        network_policy="catalog-public-policy",
        reconnect_attempts=1,
        operation_timeout=45,
    )
    try:
        tools = await manager.list_tools(session_id)
        tool_names = {tool.name for tool in tools}
        if tool_names != EXPECTED_TOOLS[project_id]:
            raise RuntimeError(
                f"schema drift for {project_id}: {sorted(tool_names)}"
            )
        tool_name, arguments = _representative_call(project_id)
        result = await manager.call_tool(
            session_id,
            tool_name,
            arguments,
        )
        serialized = result.model_dump(mode="json", exclude_none=True)
        if serialized.get("isError") or serialized.get("is_error"):
            raise RuntimeError(
                f"representative call failed for {project_id}: "
                f"{json.dumps(serialized, ensure_ascii=False)[:800]}"
            )
        if len(json.dumps(serialized, ensure_ascii=False).encode("utf-8")) > 128 * 1024:
            raise RuntimeError(f"output limit exceeded for {project_id}")
        return (
            {
                "project_id": project_id,
                "tools": sorted(tool_names),
                "representative_call": "passed",
            },
            session_id,
        )
    except Exception:
        await manager.disconnect(session_id)
        raise


async def main() -> None:
    manager = MCPClientManager(operation_timeout=45, idle_timeout_seconds=120)
    results: list[dict[str, Any]] = []
    active_sessions: list[str] = []
    try:
        for project_id in EXPECTED_TOOLS:
            result, session_id = await _connect_and_exercise(manager, project_id)
            results.append(result)
            active_sessions.append(session_id)
        if len(await manager.get_sessions_summary()) != len(EXPECTED_TOOLS):
            raise RuntimeError("concurrent public-sidecar residency smoke failed")
        for session_id in reversed(active_sessions):
            await manager.disconnect(session_id)
        active_sessions.clear()

        reconnect, reconnect_session = await _connect_and_exercise(
            manager,
            "fetch-mcp",
        )
        active_sessions.append(reconnect_session)
        if reconnect["representative_call"] != "passed":
            raise RuntimeError("reconnect smoke failed")
        await manager.disconnect(reconnect_session)
        active_sessions.clear()
        if await manager.get_sessions_summary():
            raise RuntimeError("public MCP sessions were not cleaned")
    finally:
        for session_id in reversed(active_sessions):
            try:
                await manager.disconnect(session_id)
            except Exception:
                pass
        await manager.close_all()
    print(
        json.dumps(
            {
                "ok": True,
                "adapters": results,
                "concurrent_residency": "passed",
                "reconnect": "passed",
                "cleanup": "passed",
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
