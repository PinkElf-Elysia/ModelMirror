"""Private Unix-socket gateway for fixed Wave-5 database MCP adapters."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from .database_contracts import (
    DATABASE_ADAPTERS,
    FORBIDDEN_CONFIGURATION_KEYS,
    GRAPH_DATA_SERVICE_ADAPTERS,
    MAX_ARGUMENT_BYTES,
    MAX_OUTPUT_BYTES,
    REMOTE_DATA_SERVICE_ADAPTERS,
    STAGED_DATABASE_ADAPTERS,
    WORKSPACE_PATTERN,
    resolve_allowed_addresses,
    validate_configuration,
    validate_document,
    validate_readonly_sql,
)
from .database_data_services import validate_data_service_arguments
from .database_graph_services import validate_graph_service_arguments
from .engine import SandboxEngineError


SOCKET_PATH = Path(
    os.getenv("MCP_DATABASE_SOCKET_PATH", "/run/modelmirror-database-mcp/database-mcp.sock")
)
INPUT_ROOT = Path(os.getenv("MCP_DATABASE_INPUT_ROOT", "/inputs"))
MAX_REQUEST_BYTES = 128 * 1024
MAX_MCP_MESSAGE_BYTES = 256 * 1024
TOOL_CALL_TIMEOUT_SECONDS = 15
MAX_SESSIONS = max(1, min(int(os.getenv("MCP_DATABASE_MAX_SESSIONS", "6")), 12))
SEMAPHORE = asyncio.Semaphore(MAX_SESSIONS)


def _allowed_adapters() -> frozenset[str]:
    raw = os.getenv("MCP_DATABASE_ALLOWED_ADAPTERS", "").strip()
    if not raw:
        return frozenset(set(DATABASE_ADAPTERS) - set(STAGED_DATABASE_ADAPTERS))
    requested = {item.strip() for item in raw.split(",") if item.strip()}
    # Unknown administrator entries never broaden the compiled allowlist.
    return frozenset(requested & set(DATABASE_ADAPTERS))


ALLOWED_ADAPTERS = _allowed_adapters()


def _rpc_error(request_id: object, code: int, message: str) -> bytes:
    return (
        json.dumps(
            {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _walk_argument_keys(value: object, *, depth: int = 0) -> None:
    if depth > 12:
        raise ValueError("arguments_too_deep")
    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = str(raw_key).strip().lower()
            if key in FORBIDDEN_CONFIGURATION_KEYS:
                raise ValueError("forbidden_argument_field")
            _walk_argument_keys(child, depth=depth + 1)
    elif isinstance(value, list):
        for child in value:
            _walk_argument_keys(child, depth=depth + 1)


def _validate_tool_arguments(adapter_id: str, tool_name: str, arguments: object) -> None:
    if not isinstance(arguments, dict):
        raise ValueError("invalid_tool_arguments")
    encoded = json.dumps(arguments, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_ARGUMENT_BYTES:
        raise ValueError("arguments_too_large")
    _walk_argument_keys(arguments)
    if tool_name in {"execute_sql", "run_query", "query"}:
        dialect = {
            "dbhub": "postgres",
            "clickhouse-mcp": "clickhouse",
            "duckdb-mcp": "duckdb",
            "supabase-mcp": "postgres",
        }[adapter_id]
        # DBHub repeats the gate with the exact selected engine in the child.
        validate_readonly_sql(arguments.get("query"), dialect=dialect)
    if adapter_id == "mongodb-mcp":
        if tool_name == "aggregate":
            validate_document(arguments.get("pipeline"), pipeline=True)
        for field in ("filter", "projection", "sort"):
            if field in arguments and arguments[field] is not None:
                validate_document(arguments[field])
    if adapter_id in GRAPH_DATA_SERVICE_ADAPTERS:
        validate_graph_service_arguments(adapter_id, tool_name, arguments)
    elif adapter_id in REMOTE_DATA_SERVICE_ADAPTERS:
        validate_data_service_arguments(adapter_id, tool_name, arguments)


async def _terminate_timed_out_call(
    request_id: object,
    process: asyncio.subprocess.Process,
    client: asyncio.StreamWriter,
    write_lock: asyncio.Lock,
    call_requests: set[object],
    call_deadlines: dict[object, asyncio.Task[None]],
    suppressed_requests: set[object],
    *,
    timeout_seconds: float = TOOL_CALL_TIMEOUT_SECONDS,
) -> None:
    """Fail one timed-out call and terminate its entire isolated child session."""

    await asyncio.sleep(timeout_seconds)
    current = asyncio.current_task()
    async with write_lock:
        if current is None or call_deadlines.get(request_id) is not current:
            return
        call_deadlines.pop(request_id, None)
        call_requests.discard(request_id)
        suppressed_requests.add(request_id)
        client.write(
            _rpc_error(
                request_id,
                -32001,
                "数据库只读调用超过 15 秒，已终止会话。",
            )
        )
        await client.drain()
    if process.returncode is None:
        process.kill()
        await process.wait()


async def _client_to_child(
    source: asyncio.StreamReader,
    destination: asyncio.StreamWriter,
    client: asyncio.StreamWriter,
    adapter_id: str,
    list_requests: set[object],
    call_requests: set[object],
    call_deadlines: dict[object, asyncio.Task[None]],
    suppressed_requests: set[object],
    process: asyncio.subprocess.Process,
    write_lock: asyncio.Lock,
) -> None:
    contract = DATABASE_ADAPTERS[adapter_id]
    while True:
        raw = await source.readline()
        if not raw:
            break
        if len(raw) > MAX_MCP_MESSAGE_BYTES:
            raise SandboxEngineError("MCP request exceeds 256 KiB.", code="mcp_message_too_large")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        request_id = payload.get("id")
        method = payload.get("method")
        if method == "tools/list" and request_id is not None:
            if not isinstance(request_id, (str, int)) or isinstance(request_id, bool):
                async with write_lock:
                    client.write(_rpc_error(None, -32600, "MCP 请求 ID 无效。"))
                    await client.drain()
                continue
            list_requests.add(request_id)
        elif method == "tools/call":
            if not isinstance(request_id, (str, int)) or isinstance(request_id, bool):
                async with write_lock:
                    client.write(_rpc_error(None, -32600, "工具调用必须提供有效请求 ID。"))
                    await client.drain()
                continue
            if request_id in list_requests or request_id in call_requests:
                async with write_lock:
                    client.write(_rpc_error(request_id, -32600, "MCP 请求 ID 重复。"))
                    await client.drain()
                continue
            params = payload.get("params")
            tool_name = params.get("name") if isinstance(params, dict) else None
            arguments = params.get("arguments", {}) if isinstance(params, dict) else {}
            if tool_name not in contract.tools:
                async with write_lock:
                    client.write(_rpc_error(request_id, -32601, "该工具未通过数据库只读策略审核。"))
                    await client.drain()
                continue
            try:
                _validate_tool_arguments(adapter_id, str(tool_name), arguments)
            except ValueError:
                async with write_lock:
                    client.write(_rpc_error(request_id, -32602, "工具参数未通过数据库安全策略。"))
                    await client.drain()
                continue
            call_requests.add(request_id)
            deadline = asyncio.create_task(
                _terminate_timed_out_call(
                    request_id,
                    process,
                    client,
                    write_lock,
                    call_requests,
                    call_deadlines,
                    suppressed_requests,
                )
            )
            call_deadlines[request_id] = deadline
        destination.write(raw)
        await destination.drain()


async def _child_to_client(
    source: asyncio.StreamReader,
    destination: asyncio.StreamWriter,
    adapter_id: str,
    list_requests: set[object],
    call_requests: set[object],
    call_deadlines: dict[object, asyncio.Task[None]],
    suppressed_requests: set[object],
    write_lock: asyncio.Lock,
) -> None:
    contract = DATABASE_ADAPTERS[adapter_id]
    while True:
        raw = await source.readline()
        if not raw:
            break
        if len(raw) > MAX_MCP_MESSAGE_BYTES or len(raw) > MAX_OUTPUT_BYTES + 16 * 1024:
            raise SandboxEngineError("MCP output exceeds 256 KiB.", code="mcp_output_too_large")
        output = raw
        try:
            payload = json.loads(raw.decode("utf-8"))
            request_id = payload.get("id") if isinstance(payload, dict) else None
            if request_id in suppressed_requests:
                suppressed_requests.discard(request_id)
                continue
            if request_id in list_requests:
                list_requests.discard(request_id)
                result = payload.get("result")
                tools = result.get("tools") if isinstance(result, dict) else None
                if isinstance(tools, list):
                    result["tools"] = [
                        item
                        for item in tools
                        if isinstance(item, dict) and item.get("name") in contract.tools
                    ]
                    output = (
                        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                        + b"\n"
                    )
            elif request_id in call_requests:
                call_requests.discard(request_id)
                deadline = call_deadlines.pop(request_id, None)
                if deadline is not None:
                    deadline.cancel()
                if isinstance(payload, dict) and "error" in payload:
                    error = payload.get("error")
                    code = error.get("code", -32603) if isinstance(error, dict) else -32603
                    payload["error"] = {"code": code, "message": "数据库只读调用失败。"}
                    output = (
                        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                        + b"\n"
                    )
                elif isinstance(payload, dict):
                    result = payload.get("result")
                    if isinstance(result, dict) and (
                        result.get("isError") is True or result.get("is_error") is True
                    ):
                        result.pop("structuredContent", None)
                        result.pop("structured_content", None)
                        result["content"] = [
                            {"type": "text", "text": "数据库只读调用失败。"}
                        ]
                        output = (
                            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                            + b"\n"
                        )
        except (UnicodeError, json.JSONDecodeError):
            pass
        async with write_lock:
            destination.write(output)
            await destination.drain()


async def _drain_stderr(stream: asyncio.StreamReader | None) -> None:
    if stream is None:
        return
    while await stream.read(4096):
        pass


async def _stdio(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    request: dict[str, Any],
) -> None:
    adapter_id = str(request.get("adapter_id") or "").strip()
    if adapter_id not in ALLOWED_ADAPTERS:
        raise SandboxEngineError("Database adapter is disabled in this sidecar.", code="mcp_adapter_denied")
    try:
        validated = validate_configuration(adapter_id, request.get("configuration"))
    except ValueError as exc:
        raise SandboxEngineError("Database adapter configuration denied.", code=str(exc)) from exc
    request["configuration"] = None
    if "host" in validated.settings:
        try:
            addresses = resolve_allowed_addresses(
                str(validated.settings["host"]), int(validated.settings["port"])
            )
        except ValueError as exc:
            raise SandboxEngineError("Database target denied.", code=str(exc)) from exc
    else:
        addresses = resolve_allowed_addresses("api.supabase.com", 443) if adapter_id == "supabase-mcp" else ()

    if adapter_id == "duckdb-mcp":
        assert validated.workspace_id is not None
        input_root = (INPUT_ROOT / validated.workspace_id).resolve()
        if input_root.parent != INPUT_ROOT.resolve() or not input_root.is_dir() or input_root.is_symlink():
            raise SandboxEngineError("DuckDB workspace unavailable.", code="workspace_unavailable")
        if not any(
            path.is_file() and not path.is_symlink() and path.suffix.lower() == ".duckdb"
            for path in input_root.rglob("*")
        ):
            raise SandboxEngineError("DuckDB workspace has no reviewed database file.", code="duckdb_file_missing")

    async with SEMAPHORE:
        temp_root = Path(tempfile.mkdtemp(prefix=f"mcp-database-{adapter_id[:12]}-"))
        payload = {
            "settings": dict(validated.settings),
            "credentials": dict(validated.credentials),
        }
        if validated.workspace_id is not None:
            payload["workspace_id"] = validated.workspace_id
        encoded_configuration = base64.urlsafe_b64encode(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).decode("ascii")
        payload = {}
        validated.clear_secrets()
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "PYTHONPATH": "/opt/modelmirror",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONUNBUFFERED": "1",
            "HOME": str(temp_root),
            "TMPDIR": str(temp_root),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TZ": "UTC",
            "NO_PROXY": "*",
            "no_proxy": "*",
            "DO_NOT_TRACK": "1",
            "MCP_DATABASE_ADAPTER_ID": adapter_id,
            "MCP_DATABASE_WORKSPACE_ID": validated.workspace_id or "",
            "MCP_DATABASE_INPUT_ROOT": str(INPUT_ROOT),
            "MCP_DATABASE_CHILD_CONFIGURATION_B64": encoded_configuration,
            "MCP_DATABASE_PINNED_DNS_B64": base64.urlsafe_b64encode(
                json.dumps(
                    {
                        "host": (
                            str(validated.settings["host"])
                            if "host" in validated.settings
                            else "api.supabase.com" if adapter_id == "supabase-mcp" else None
                        ),
                        "addresses": list(addresses),
                    },
                    separators=(",", ":"),
                ).encode("utf-8")
            ).decode("ascii"),
        }
        if os.getenv("MCP_DATABASE_TEST_ALLOW_PLAINTEXT") == "true":
            env["MCP_DATABASE_TEST_ALLOW_PLAINTEXT"] = "true"
        encoded_configuration = ""
        command = [
            sys.executable,
            "-m",
            "sandbox_sidecar.database_landlock_exec",
            "--",
            sys.executable,
            "-m",
            "sandbox_sidecar.database_mcp",
            adapter_id,
        ]
        process: asyncio.subprocess.Process | None = None
        tasks: list[asyncio.Task[None]] = []
        handshake_sent = False
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(temp_root),
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                limit=MAX_MCP_MESSAGE_BYTES + 1,
            )
            env.clear()
            if process.stdin is None or process.stdout is None:
                raise SandboxEngineError("Database MCP stdio unavailable.", code="mcp_stdio_unavailable")
            writer.write(
                json.dumps(
                    {
                        "ok": True,
                        "adapter_id": adapter_id,
                        "protocol": "modelmirror-mcp-database-stdio-v1",
                        "read_only": True,
                        "tools": sorted(validated.contract.tools),
                        "target_policy": "sealed-workspace" if adapter_id == "duckdb-mcp" else "resolved-public-or-admin-allowlist",
                        "resolved_address_count": len(addresses),
                        "limits": {
                            "max_rows": 1_000,
                            "default_rows": 200,
                            "timeout_seconds": 15,
                            "output_bytes": MAX_OUTPUT_BYTES,
                        },
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                + b"\n"
            )
            await writer.drain()
            handshake_sent = True
            list_requests: set[object] = set()
            call_requests: set[object] = set()
            call_deadlines: dict[object, asyncio.Task[None]] = {}
            suppressed_requests: set[object] = set()
            write_lock = asyncio.Lock()
            tasks = [
                asyncio.create_task(
                    _client_to_child(
                        reader,
                        process.stdin,
                        writer,
                        adapter_id,
                        list_requests,
                        call_requests,
                        call_deadlines,
                        suppressed_requests,
                        process,
                        write_lock,
                    )
                ),
                asyncio.create_task(
                    _child_to_client(
                        process.stdout,
                        writer,
                        adapter_id,
                        list_requests,
                        call_requests,
                        call_deadlines,
                        suppressed_requests,
                        write_lock,
                    )
                ),
                asyncio.create_task(_drain_stderr(process.stderr)),
            ]
            done, pending = await asyncio.wait(tasks[:2], return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            for task in pending:
                task.cancel()
        finally:
            if "call_deadlines" in locals():
                for deadline in call_deadlines.values():
                    deadline.cancel()
                if call_deadlines:
                    await asyncio.gather(*call_deadlines.values(), return_exceptions=True)
                call_deadlines.clear()
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if process is not None and process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=2)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
            shutil.rmtree(temp_root, ignore_errors=True)
            if handshake_sent:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), timeout=1)
                except (asyncio.TimeoutError, ConnectionError, OSError):
                    pass


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        raw = await reader.readline()
        if not raw or len(raw) > MAX_REQUEST_BYTES:
            raise SandboxEngineError("Database sidecar request invalid.", code="invalid_request")
        request = json.loads(raw.decode("utf-8"))
        if not isinstance(request, dict):
            raise SandboxEngineError("Database sidecar request must be an object.", code="invalid_request")
        action = str(request.get("action") or "").strip()
        if action == "mcp_stdio":
            await _stdio(reader, writer, request)
            return
        if action != "health":
            raise SandboxEngineError("Database sidecar action denied.", code="action_denied")
        response = {
            "ok": True,
            "protocol": "modelmirror-mcp-database-stdio-v1",
            "mcp_database_adapters": sorted(ALLOWED_ADAPTERS),
            "mcp_database_max_sessions": MAX_SESSIONS,
            "read_only": True,
            "mcp_message_limit_bytes": MAX_MCP_MESSAGE_BYTES,
        }
    except SandboxEngineError as exc:
        response = {"ok": False, "error": "database_sidecar_rejected", "code": exc.code}
    except Exception:
        response = {"ok": False, "error": "database_sidecar_internal_error", "code": "database_sidecar_internal_error"}
    writer.write(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")
    await writer.drain()
    writer.close()
    try:
        await asyncio.wait_for(writer.wait_closed(), timeout=1)
    except (asyncio.TimeoutError, ConnectionError, OSError):
        pass


async def main() -> None:
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    SOCKET_PATH.unlink(missing_ok=True)
    server = await asyncio.start_unix_server(
        handle, path=str(SOCKET_PATH), limit=MAX_MCP_MESSAGE_BYTES + 1
    )
    os.chmod(SOCKET_PATH, 0o660)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
