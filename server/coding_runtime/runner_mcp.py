from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from .draft_workspace import DraftLimits, DraftPolicyError, DraftWorkspace


MAX_MCP_FRAME_BYTES = 64 * 1024
MCP_PROTOCOL_VERSION = "2024-11-05"
COMMAND_TOOL_NAME = "run_project_command"
DELETE_TOOL_NAME = "delete_text_file"
MOVE_TOOL_NAME = "move_text_file"


class RunnerMcpError(RuntimeError):
    pass


class FileOperationError(RunnerMcpError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _command_tool_definition() -> dict[str, Any]:
    return {
        "name": COMMAND_TOOL_NAME,
        "description": (
            "Request one structured Python or Node project check. The user must approve "
            "each request before it runs in an isolated offline copy. Set cwd to '.' or "
            "a project-relative directory; '/workspace' is accepted as the project root."
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
                "cwd": {
                    "type": "string",
                    "description": (
                        "Project-relative directory. Use '.' or '/workspace' for the "
                        "project root; do not use any other absolute path."
                    ),
                },
                "purpose": {"type": "string", "minLength": 1, "maxLength": 240},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 300},
            },
            "required": ["argv", "cwd", "purpose", "timeout_seconds"],
            "additionalProperties": False,
        },
    }


def _file_tool_definitions() -> list[dict[str, Any]]:
    path_schema = {
        "type": "string",
        "minLength": 1,
        "maxLength": 1024,
        "description": (
            "Project-relative UTF-8 text file path. '/workspace/<path>' is also "
            "accepted; no other absolute path is allowed."
        ),
    }
    return [
        {
            "name": DELETE_TOOL_NAME,
            "description": (
                "Delete one UTF-8 text file from the temporary change draft. "
                "This does not modify the user's local project."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"path": path_schema},
                "required": ["path"],
                "additionalProperties": False,
            },
        },
        {
            "name": MOVE_TOOL_NAME,
            "description": (
                "Move one UTF-8 text file inside the temporary change draft without "
                "overwriting an existing destination. This does not modify the user's "
                "local project."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "source": path_schema,
                    "destination": path_schema,
                },
                "required": ["source", "destination"],
                "additionalProperties": False,
            },
        },
    ]


class RunnerMcpServer:
    def __init__(
        self,
        *,
        socket_path: str = "",
        token: str = "",
        workspace_root: str = "",
        file_operations_enabled: bool = False,
    ) -> None:
        self._command_enabled = bool(socket_path and token)
        if bool(socket_path) != bool(token):
            raise RunnerMcpError("Runner bridge is not configured")
        if file_operations_enabled:
            workspace = Path(workspace_root)
            if (
                not workspace.is_absolute()
                or workspace.is_symlink()
                or not workspace.is_dir()
            ):
                raise RunnerMcpError("File operations are not configured")
            self._workspace_root = workspace.resolve()
        else:
            self._workspace_root = None
        if not self._command_enabled and self._workspace_root is None:
            raise RunnerMcpError("Project tools are not configured")
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
            tools: list[dict[str, Any]] = []
            if self._command_enabled:
                tools.append(_command_tool_definition())
            if self._workspace_root is not None:
                tools.extend(_file_tool_definitions())
            return self._result(request_id, {"tools": tools})
        if method == "tools/call":
            if not isinstance(params, dict):
                return self._error(request_id, -32602, "Unknown tool")
            tool_name = params.get("name")
            arguments = params.get("arguments")
            if not isinstance(arguments, dict):
                return self._error(request_id, -32602, "Invalid tool arguments")
            if tool_name in {DELETE_TOOL_NAME, MOVE_TOOL_NAME}:
                return self._file_operation_result(request_id, tool_name, arguments)
            if tool_name != COMMAND_TOOL_NAME or not self._command_enabled:
                return self._error(request_id, -32602, "Unknown tool")
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

    def _file_operation_result(
        self,
        request_id: Any,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if self._workspace_root is None:
            return self._error(request_id, -32602, "Unknown tool")
        try:
            if tool_name == DELETE_TOOL_NAME:
                if set(arguments) != {"path"}:
                    raise FileOperationError("invalid_arguments")
                path = self._normalize_path(arguments["path"])
                self._delete_text_file(path)
                result = {"status": "deleted", "path": path}
            else:
                if set(arguments) != {"source", "destination"}:
                    raise FileOperationError("invalid_arguments")
                source = self._normalize_path(arguments["source"])
                destination = self._normalize_path(arguments["destination"])
                self._move_text_file(source, destination)
                result = {
                    "status": "moved",
                    "source": source,
                    "destination": destination,
                }
            payload = {"state": "completed", "result": result}
            is_error = False
        except (DraftPolicyError, FileOperationError, OSError):
            payload = {
                "state": "failed",
                "result": {"status": "file_operation_rejected"},
            }
            is_error = True
        text = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return self._result(
            request_id,
            {"content": [{"type": "text", "text": text}], "isError": is_error},
        )

    @staticmethod
    def _normalize_path(value: Any) -> str:
        if not isinstance(value, str) or not value or len(value) > 1024:
            raise FileOperationError("invalid_path")
        if value.startswith("/workspace/"):
            value = value.removeprefix("/workspace/")
        elif value.startswith("/") or ":" in PurePosixPath(value).parts[0]:
            raise FileOperationError("invalid_path")
        return DraftWorkspace.normalize_relative_path(value)

    def _resolve_path(self, relative: str, *, create_parents: bool = False) -> Path:
        assert self._workspace_root is not None
        current = self._workspace_root
        parts = PurePosixPath(relative).parts
        for index, part in enumerate(parts):
            current = current / part
            is_leaf = index == len(parts) - 1
            if current.is_symlink():
                raise FileOperationError("symlink_not_allowed")
            if not is_leaf:
                if current.exists() and not current.is_dir():
                    raise FileOperationError("invalid_path")
                if create_parents and not current.exists():
                    current.mkdir()
        return current

    @staticmethod
    def _validate_text_file(path: Path) -> None:
        if path.is_symlink() or not path.is_file():
            raise FileOperationError("text_file_required")
        content = path.read_bytes()
        if len(content) > DraftLimits().max_file_bytes or b"\x00" in content:
            raise FileOperationError("text_file_required")
        try:
            content.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise FileOperationError("text_file_required") from exc

    def _delete_text_file(self, relative: str) -> None:
        target = self._resolve_path(relative)
        self._validate_text_file(target)
        target.unlink()

    def _move_text_file(self, source: str, destination: str) -> None:
        if source == destination:
            raise FileOperationError("same_path")
        source_path = self._resolve_path(source)
        self._validate_text_file(source_path)
        destination_path = self._resolve_path(destination, create_parents=True)
        if destination_path.exists() or destination_path.is_symlink():
            raise FileOperationError("destination_exists")
        os.link(source_path, destination_path, follow_symlinks=False)
        try:
            source_path.unlink()
        except OSError:
            destination_path.unlink(missing_ok=True)
            raise

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
    workspace_root = os.environ.get("MODELMIRROR_WORKSPACE", "")
    file_operations_enabled = os.environ.get("MODELMIRROR_FILE_OPERATIONS") == "1"
    server = RunnerMcpServer(
        socket_path=socket_path,
        token=token,
        workspace_root=workspace_root,
        file_operations_enabled=file_operations_enabled,
    )
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
