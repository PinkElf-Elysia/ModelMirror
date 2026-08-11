"""Sealed-workspace GoGraph facade for catalog Wave 20."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import signal
from pathlib import Path, PurePosixPath
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import ConfigDict, Field


GOGRAPH_ADAPTER_ID = "ozgurcd-gograph"
GOGRAPH_VERSION = "1.5.6"
GOGRAPH_COMMIT = "aa4d6d549e64f35c492664263630ba1350c66920"
GOGRAPH_BINARY = Path("/usr/local/bin/gograph")
GOGRAPH_AMD64_SHA256 = (
    "1ef375a88cc8825ca7879b1170720352702e59723d1e3b06d33101a50a6f7030"
)
GOGRAPH_ARM64_SHA256 = (
    "c8b6d8a42326264858f14c7819200f47d00d0fcd58520b6c6d1e1b16b022a6b5"
)
GOGRAPH_GO_VERSION = "1.26.5"
GOGRAPH_GO_IMAGE_DIGEST = (
    "sha256:53eeac89074db483fdf0ab3be1df32bf6e47562263d2d0d6baa7f26acb4957dd"
)
GOGRAPH_UPSTREAM_SCHEMA_SHA256 = (
    "a2c8f2fcf028067f2e080d018e482a52bd7ba8c3546ac92ba254b6b8b3fca25f"
)

# Filled from FastMCP's canonical public tools/list contract.  The contract
# smoke and focused tests fail closed when this digest drifts.
WAVE20_SCHEMA_SHA256 = {
    GOGRAPH_ADAPTER_ID: (
        "b2f18ca952f7d555b29a460af5261e3f9ab1b81d187d884af778ca3360fae981"
    )
}

UPSTREAM_TOOL_NAMES = (
    "gograph_callers",
    "gograph_context",
    "gograph_query",
    "gograph_source",
    "gograph_stats",
    "gograph_summary",
)
MAX_UPSTREAM_MESSAGE_BYTES = 4 * 1024 * 1024
MAX_RESULT_BYTES = 240_000
INDEX_TIMEOUT_SECONDS = 55
QUERY_TIMEOUT_SECONDS = 12
CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
PATH_KEYS = {"file", "file_path", "path", "repository", "repository_path"}
HIDDEN_PATH_KEYS = {
    "artifact_directory",
    "cache_dir",
    "cache_root",
    "cwd",
    "graph_root",
    "repo_path",
    "root",
    "root_path",
    "workspace_root",
}

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
STATE_WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=False,
)

SearchQuery = Annotated[
    str,
    Field(
        min_length=1,
        max_length=160,
        description="Structural keyword; regular expressions and paths are not accepted.",
    ),
]
SymbolName = Annotated[
    str,
    Field(
        min_length=1,
        max_length=240,
        description="Go symbol selected from a result in this sealed workspace.",
    ),
]


def _freeze_strict_tool_contract(mcp: FastMCP) -> FastMCP:
    for tool in mcp._tool_manager._tools.values():
        argument_model = tool.fn_metadata.arg_model
        argument_model.model_config = ConfigDict(
            **dict(argument_model.model_config),
            extra="forbid",
        )
        argument_model.model_rebuild(force=True)
        tool.parameters = argument_model.model_json_schema(by_alias=True)
    return mcp


def _safe_text(value: str, *, field: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned or CONTROL_CHARACTERS.search(cleaned):
        raise ValueError(f"{field}_invalid")
    if "/" in cleaned or "\\" in cleaned or WINDOWS_ABSOLUTE_PATH.match(cleaned):
        raise ValueError(f"{field}_path_denied")
    return cleaned


class GoGraphRuntime:
    """Run one pinned upstream MCP process against one sealed Go workspace."""

    def __init__(self, context: Any) -> None:
        self.context = context
        self.input_root = Path(context.input_root).resolve()
        self.temp_root = Path(os.environ.get("TMPDIR", "/tmp")).resolve()
        self.go_cache = (self.temp_root / "go-build-cache").resolve()
        self.go_mod_cache = (self.temp_root / "go-module-cache").resolve()
        self.go_cache.mkdir(parents=True, exist_ok=True)
        self.go_mod_cache.mkdir(parents=True, exist_ok=True)
        self.indexed = False
        self._lock = asyncio.Lock()
        self._process: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._request_id = 0

    def _environment(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update(
            {
                "PATH": "/usr/local/go/bin:/usr/local/bin:/usr/bin:/bin",
                "CGO_ENABLED": "0",
                "GOFLAGS": "-mod=readonly",
                "GOCACHE": str(self.go_cache),
                "GOMODCACHE": str(self.go_mod_cache),
                "GOPROXY": "off",
                "GOSUMDB": "off",
                "GOTOOLCHAIN": "local",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_TERMINAL_PROMPT": "0",
                "HOME": str(self.temp_root),
            }
        )
        for key in (
            "ALL_PROXY",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "all_proxy",
            "http_proxy",
            "https_proxy",
        ):
            env.pop(key, None)
        return env

    async def _drain_stderr(self, stream: asyncio.StreamReader) -> None:
        while await stream.read(4096):
            pass

    async def _terminate(self) -> None:
        process = self._process
        self._process = None
        self.indexed = False
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            await asyncio.gather(self._stderr_task, return_exceptions=True)
            self._stderr_task = None
        if process is None or process.returncode is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            process.kill()
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except asyncio.TimeoutError:
            pass

    async def _write_message(self, payload: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.returncode is not None:
            raise ValueError("code_index_runtime_unavailable")
        raw = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
        process.stdin.write(raw)
        await process.stdin.drain()

    async def _request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout: int,
    ) -> dict[str, Any]:
        process = self._process
        if process is None or process.stdout is None:
            raise ValueError("code_index_runtime_unavailable")
        self._request_id += 1
        request_id = self._request_id
        await self._write_message(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
        )
        try:
            for _ in range(64):
                raw = await asyncio.wait_for(process.stdout.readline(), timeout=timeout)
                if not raw or len(raw) > MAX_UPSTREAM_MESSAGE_BYTES:
                    raise ValueError("code_index_upstream_output_invalid")
                try:
                    response = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError("code_index_upstream_output_invalid") from exc
                if not isinstance(response, dict) or response.get("jsonrpc") != "2.0":
                    raise ValueError("code_index_upstream_output_invalid")
                if response.get("id") != request_id:
                    continue
                if "error" in response:
                    raise ValueError("code_index_upstream_failed")
                result = response.get("result")
                if not isinstance(result, dict):
                    raise ValueError("code_index_upstream_output_invalid")
                return result
        except asyncio.TimeoutError as exc:
            await self._terminate()
            raise ValueError("code_index_timeout") from exc
        raise ValueError("code_index_upstream_output_invalid")

    @staticmethod
    def _upstream_digest(tools: list[dict[str, Any]]) -> str:
        selected = [
            {"name": tool["name"], "inputSchema": tool["inputSchema"]}
            for tool in tools
            if tool.get("name") in UPSTREAM_TOOL_NAMES
        ]
        selected.sort(key=lambda item: item["name"])
        raw = json.dumps(
            selected,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    async def _start(self) -> None:
        if self._process is not None:
            return
        if not GOGRAPH_BINARY.is_file():
            raise ValueError("code_index_runtime_unavailable")
        process = await asyncio.create_subprocess_exec(
            str(GOGRAPH_BINARY),
            "mcp",
            str(self.input_root),
            cwd=self.temp_root,
            env=self._environment(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            limit=MAX_UPSTREAM_MESSAGE_BYTES + 1,
        )
        self._process = process
        if process.stderr is not None:
            self._stderr_task = asyncio.create_task(self._drain_stderr(process.stderr))
        try:
            initialized = await self._request(
                "initialize",
                {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "modelmirror-wave20",
                        "version": "1",
                    },
                },
                timeout=INDEX_TIMEOUT_SECONDS,
            )
            server_info = initialized.get("serverInfo")
            if server_info != {"name": "gograph", "version": GOGRAPH_VERSION}:
                raise ValueError("code_index_upstream_identity_drift")
            await self._write_message(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {},
                }
            )
            listed = await self._request("tools/list", {}, timeout=QUERY_TIMEOUT_SECONDS)
            tools = listed.get("tools")
            if not isinstance(tools, list):
                raise ValueError("code_index_upstream_schema_drift")
            names = {str(tool.get("name") or "") for tool in tools if isinstance(tool, dict)}
            if not set(UPSTREAM_TOOL_NAMES).issubset(names):
                raise ValueError("code_index_upstream_schema_drift")
            if self._upstream_digest(tools) != GOGRAPH_UPSTREAM_SCHEMA_SHA256:
                raise ValueError("code_index_upstream_schema_drift")
        except Exception:
            await self._terminate()
            raise

    def _sanitize(self, value: Any, *, key: str = "") -> Any:
        folded = key.casefold()
        if folded in HIDDEN_PATH_KEYS:
            return None
        if isinstance(value, dict):
            return {
                str(item_key): self._sanitize(item_value, key=str(item_key))
                for item_key, item_value in value.items()
                if str(item_key).casefold() not in HIDDEN_PATH_KEYS
            }
        if isinstance(value, list):
            return [self._sanitize(item, key=key) for item in value]
        if isinstance(value, str):
            text = value
            normalized = text.replace("\\", "/")
            if folded in PATH_KEYS and (
                PurePosixPath(normalized).is_absolute()
                or WINDOWS_ABSOLUTE_PATH.match(text)
            ):
                candidate = Path(text).resolve()
                if candidate == self.input_root:
                    return "workspace"
                if self.input_root not in candidate.parents:
                    raise ValueError("code_index_path_disclosure")
                return candidate.relative_to(self.input_root).as_posix()
            return text.replace(str(self.input_root), "workspace")
        if value is None or isinstance(value, (bool, int, float)):
            return value
        raise ValueError("code_index_upstream_output_invalid")

    def _extract_tool_payload(self, result: dict[str, Any]) -> Any:
        if result.get("isError") is True:
            raise ValueError("code_index_upstream_failed")
        structured = result.get("structuredContent")
        payload: Any
        if structured is not None:
            payload = structured
        else:
            content = result.get("content")
            if not isinstance(content, list) or len(content) != 1:
                raise ValueError("code_index_upstream_output_invalid")
            item = content[0]
            if not isinstance(item, dict) or item.get("type") != "text":
                raise ValueError("code_index_upstream_output_invalid")
            text = item.get("text")
            if not isinstance(text, str):
                raise ValueError("code_index_upstream_output_invalid")
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                payload = {"text": text}
        sanitized = self._sanitize(payload)
        raw = json.dumps(
            sanitized,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(raw) > MAX_RESULT_BYTES:
            raise ValueError("code_index_result_too_large")
        return sanitized

    async def _call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        timeout: int = QUERY_TIMEOUT_SECONDS,
    ) -> Any:
        result = await self._request(
            "tools/call",
            {"name": tool_name, "arguments": arguments},
            timeout=timeout,
        )
        return self._extract_tool_payload(result)

    async def index_repository(self) -> dict[str, Any]:
        async with self._lock:
            if self.indexed:
                raise ValueError("code_index_already_prepared")
            await self._start()
            stats = await self._call_tool("gograph_stats", {}, timeout=QUERY_TIMEOUT_SECONDS)
            self.indexed = True
            return {
                "engine": "gograph",
                "version": GOGRAPH_VERSION,
                "status": "indexed",
                "language": "go",
                "persistence": "session-memory-only",
                "stats": stats,
            }

    async def query(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            if not self.indexed:
                raise ValueError("code_index_not_prepared")
            if tool_name not in UPSTREAM_TOOL_NAMES or tool_name == "gograph_stats":
                raise ValueError("code_index_tool_denied")
            result = await self._call_tool(tool_name, arguments)
            return {
                "engine": "gograph",
                "version": GOGRAPH_VERSION,
                "result": result,
            }


def build_gograph(context: Any) -> FastMCP:
    """Expose six bounded tools backed by the pinned upstream MCP server."""

    runtime = GoGraphRuntime(context)
    mcp = FastMCP("ModelMirror GoGraph")

    @mcp.tool(name="index_repository", annotations=STATE_WRITE)
    async def index_repository() -> dict[str, Any]:
        """Build one non-persistent in-memory index for the sealed Go workspace."""

        return await runtime.index_repository()

    @mcp.tool(name="search_symbols", annotations=READ_ONLY)
    async def search_symbols(query: SearchQuery) -> dict[str, Any]:
        """Search Go symbols, packages, files and imports by one bounded keyword."""

        return await runtime.query(
            "gograph_query",
            {"term": _safe_text(query, field="query")},
        )

    @mcp.tool(name="get_symbol_context", annotations=READ_ONLY)
    async def get_symbol_context(symbol: SymbolName) -> dict[str, Any]:
        """Return source, callers, callees and tests for one exact Go symbol."""

        return await runtime.query(
            "gograph_context",
            {"symbol": _safe_text(symbol, field="symbol"), "exact": True},
        )

    @mcp.tool(name="get_source", annotations=READ_ONLY)
    async def get_source(symbol: SymbolName) -> dict[str, Any]:
        """Return repository-confined source for one Go symbol."""

        return await runtime.query(
            "gograph_source",
            {"symbol": _safe_text(symbol, field="symbol")},
        )

    @mcp.tool(name="get_callers", annotations=READ_ONLY)
    async def get_callers(
        symbol: SymbolName,
        depth: Annotated[int, Field(ge=1, le=3)] = 1,
    ) -> dict[str, Any]:
        """Return a bounded upstream call graph for one exact Go symbol."""

        return await runtime.query(
            "gograph_callers",
            {
                "function": _safe_text(symbol, field="symbol"),
                "depth": depth,
                "exact": True,
                "no_tests": True,
                "mermaid": False,
            },
        )

    @mcp.tool(name="get_repository_summary", annotations=READ_ONLY)
    async def get_repository_summary() -> dict[str, Any]:
        """Return a bounded structural briefing for the current Go repository."""

        return await runtime.query("gograph_summary", {})

    return _freeze_strict_tool_contract(mcp)


WAVE20_BUILDERS = {GOGRAPH_ADAPTER_ID: build_gograph}
WAVE20_TOOL_NAMES = {
    GOGRAPH_ADAPTER_ID: (
        "index_repository",
        "search_symbols",
        "get_symbol_context",
        "get_source",
        "get_callers",
        "get_repository_summary",
    )
}
