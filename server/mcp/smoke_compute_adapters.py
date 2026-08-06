"""Docker smoke harness for the three wave-1 compute catalog adapters."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from server.mcp.manager import MCPClientManager


PROXY_PATH = Path(__file__).resolve().with_name("sandbox_proxy.py")
EXPECTED_TOOLS = {
    "calculator-mcp": {"add", "sub", "mul", "div", "mod", "sqrt"},
    "time-mcp": {"get_current_time", "convert_time"},
    "vegalite-mcp": {"save_data", "visualize_data"},
}


def _command(project_id: str) -> list[str]:
    return [sys.executable, str(PROXY_PATH), project_id]


async def _connect_and_exercise(
    manager: MCPClientManager,
    project_id: str,
) -> tuple[dict[str, Any], str]:
    session_id = await manager.connect_profile(
        transport="stdio",
        server_command=_command(project_id),
        network_policy="disabled",
        reconnect_attempts=1,
        operation_timeout=10,
    )
    try:
        tools = await manager.list_tools(session_id)
        tool_names = {tool.name for tool in tools}
        if tool_names != EXPECTED_TOOLS[project_id]:
            raise RuntimeError(
                f"schema drift for {project_id}: {sorted(tool_names)}"
            )
        if project_id == "calculator-mcp":
            result = await manager.call_tool(
                session_id,
                "add",
                {"a": 20, "b": 22},
            )
        elif project_id == "time-mcp":
            result = await manager.call_tool(
                session_id,
                "convert_time",
                {
                    "source_timezone": "Asia/Shanghai",
                    "time": "09:30",
                    "target_timezone": "UTC",
                },
            )
        else:
            await manager.call_tool(
                session_id,
                "save_data",
                {"name": "smoke", "data": [{"x": "A", "y": 1}]},
            )
            result = await manager.call_tool(
                session_id,
                "visualize_data",
                {
                    "data_name": "smoke",
                    "vegalite_specification": json.dumps(
                        {
                            "mark": "bar",
                            "encoding": {
                                "x": {"field": "x", "type": "nominal"},
                                "y": {"field": "y", "type": "quantitative"},
                            },
                        }
                    ),
                },
            )
        serialized = result.model_dump(mode="json", exclude_none=True)
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
    manager = MCPClientManager(operation_timeout=10, idle_timeout_seconds=60)
    results: list[dict[str, Any]] = []
    active_sessions: list[str] = []
    try:
        for project_id in EXPECTED_TOOLS:
            result, session_id = await _connect_and_exercise(manager, project_id)
            results.append(result)
            active_sessions.append(session_id)
        if len(await manager.get_sessions_summary()) != len(EXPECTED_TOOLS):
            raise RuntimeError("concurrent sandbox residency smoke failed")
        for session_id in reversed(active_sessions):
            await manager.disconnect(session_id)
        active_sessions.clear()

        reconnect, reconnect_session = await _connect_and_exercise(
            manager,
            "calculator-mcp",
        )
        active_sessions.append(reconnect_session)
        if reconnect["representative_call"] != "passed":
            raise RuntimeError("reconnect smoke failed")
        await manager.disconnect(reconnect_session)
        active_sessions.clear()
        if await manager.get_sessions_summary():
            raise RuntimeError("sandbox sessions were not cleaned")
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
