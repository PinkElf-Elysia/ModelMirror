from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
from typing import Any, BinaryIO


MAX_MCP_FRAME_BYTES = 64 * 1024
MCP_PROTOCOL_VERSION = "2024-11-05"
TOOL_NAME = "run_project_command"


class RunnerMcpError(RuntimeError):
    pass


def _tool_definition() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "description": (
            "Request one structured Python or Node project check. The user must approve "
            "each request before it runs in an isolated offline copy."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "argv": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 64,
                },
                "cwd": {"type": "string"},
                "purpose": {"type": "string", "minLength": 1, "maxLength": 240},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 300},
            },
            "required": ["argv", "cwd", "purpose", "timeout_seconds"],
            "additionalProperties": False,
        },
    }


class RunnerMcpServer:
    def __init__(self, *, socket_path: str, token: str) -> None:
        if not socket_path or not token:
            raise RunnerMcpError("Runner bridge is not configured")
        self._socket_path = socket_path
        self._token = token

    async def dispatch(self, frame: dict[str, Any]) -> dict[str, Any] | None:
        if frame.get("jsonrpc") != "2.0" or not isinstance(frame.get("method"), str):
            raise RunnerMcpError("Invalid MCP frame")
        request_id = frame.get("id")
        method = frame["method"]
        params = frame.get("params", {})
        if method == "notifications/initialized" and "id" not in frame:
            return None
        if "id" not in frame:
            return None
        if method == "initialize":
            return self._result(
                request_id,
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "modelmirror-runner", "version": "1.0.0"},
                },
            )
        if method == "ping":
            return self._result(request_id, {})
        if method == "tools/list":
            return self._result(request_id, {"tools": [_tool_definition()]})
        if method == "tools/call":
            if not isinstance(params, dict) or params.get("name") != TOOL_NAME:
                return self._error(request_id, -32602, "Unknown tool")
            arguments = params.get("arguments")
            if not isinstance(arguments, dict):
                return self._error(request_id, -32602, "Invalid tool arguments")
            try:
                payload = await self._forward(arguments)
            except (OSError, TimeoutError, RunnerMcpError):
                payload = {
                    "state": "failed",
                    "result": {
                        "status": "runner_unavailable",
                        "exit_code": None,
                        "output": "The requested check could not be sent for confirmation.",
                        "duration_seconds": 0.0,
                    },
                }
            text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            return self._result(
                request_id,
                {"content": [{"type": "text", "text": text}], "isError": False},
            )
        return self._error(request_id, -32601, "Method not found")

    async def _forward(self, arguments: dict[str, Any]) -> dict[str, Any]:
        reader, writer = await asyncio.open_unix_connection(
            self._socket_path,
            limit=MAX_MCP_FRAME_BYTES + 1,
        )
        try:
            encoded = json.dumps(
                {"token": self._token, "arguments": arguments},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8") + b"\n"
            if len(encoded) > MAX_MCP_FRAME_BYTES:
                raise RunnerMcpError("Runner request is too large")
            writer.write(encoded)
            await writer.drain()
            raw = await reader.readline()
            if not raw or len(raw) > MAX_MCP_FRAME_BYTES:
                raise RunnerMcpError("Runner response is invalid")
            payload = json.loads(raw.decode("utf-8", errors="strict"))
            if not isinstance(payload, dict):
                raise RunnerMcpError("Runner response is invalid")
            return payload
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise RunnerMcpError("Runner response is invalid") from exc
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()

    @staticmethod
    def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }


async def _run(stdin: BinaryIO, stdout: BinaryIO) -> None:
    socket_path = os.environ.get("MODELMIRROR_RUNNER_SOCKET", "")
    token = os.environ.get("MODELMIRROR_RUNNER_TOKEN", "")
    server = RunnerMcpServer(socket_path=socket_path, token=token)
    os.environ.clear()
    os.environ.update({"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"})
    while True:
        raw = await asyncio.to_thread(stdin.readline)
        if not raw:
            return
        if len(raw) > MAX_MCP_FRAME_BYTES:
            raise RunnerMcpError("MCP frame is too large")
        try:
            frame = json.loads(raw.decode("utf-8", errors="strict"))
            if not isinstance(frame, dict):
                raise RunnerMcpError("Invalid MCP frame")
            response = await server.dispatch(frame)
        except (UnicodeError, json.JSONDecodeError, RunnerMcpError):
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Invalid MCP request"},
            }
        if response is not None:
            encoded = json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            ) + b"\n"
            await asyncio.to_thread(stdout.write, encoded)
            await asyncio.to_thread(stdout.flush)


def main() -> None:
    asyncio.run(_run(sys.stdin.buffer, sys.stdout.buffer))


if __name__ == "__main__":
    main()
