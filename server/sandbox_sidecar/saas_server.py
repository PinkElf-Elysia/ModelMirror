"""Unix-socket gateway for fixed Wave 6 stateful SaaS adapters."""

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

from .engine import SandboxEngineError
from .saas_contracts import (
    MAX_ARGUMENT_BYTES,
    MAX_OUTPUT_BYTES,
    SAAS_ADAPTERS,
    SaaSAdapterContract,
    validate_configuration,
    validate_idempotency_key,
)


SOCKET_PATH = Path(os.getenv("MCP_SAAS_SOCKET_PATH", "/run/modelmirror-saas-mcp/saas-mcp.sock"))
MAX_REQUEST_BYTES = 256 * 1024
MAX_MCP_MESSAGE_BYTES = 256 * 1024
TOOL_CALL_TIMEOUT_SECONDS = 20
MAX_SESSIONS = max(1, min(int(os.getenv("MCP_SAAS_MAX_SESSIONS", "6")), 6))
SEMAPHORE = asyncio.Semaphore(MAX_SESSIONS)
PRIVATE_IDEMPOTENCY_FIELD = "__modelmirror_idempotency_key"
PRIVATE_FIELD_PREFIX = "__modelmirror_"
UNKNOWN_WRITE_OUTCOME_MARKER = "modelmirror_unknown_write_outcome"


def _allowed_adapters() -> frozenset[str]:
    raw = os.getenv("MCP_SAAS_ALLOWED_ADAPTERS", "").strip()
    if not raw:
        return frozenset(SAAS_ADAPTERS)
    requested = {item.strip() for item in raw.split(",") if item.strip()}
    return frozenset(requested & set(SAAS_ADAPTERS))


ALLOWED_ADAPTERS = _allowed_adapters()


def _rpc_error(
    request_id: object,
    code: int,
    message: str,
    *,
    data: dict[str, object] | None = None,
) -> bytes:
    error: dict[str, object] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return (
        json.dumps(
            {"jsonrpc": "2.0", "id": request_id, "error": error},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _reject_private_fields(value: object, *, depth: int = 0) -> None:
    if depth > 12:
        raise ValueError("arguments_too_deep")
    if isinstance(value, dict):
        for raw_key, child in value.items():
            if str(raw_key).lower().startswith(PRIVATE_FIELD_PREFIX):
                raise ValueError("reserved_argument_field")
            _reject_private_fields(child, depth=depth + 1)
    elif isinstance(value, list):
        for child in value:
            _reject_private_fields(child, depth=depth + 1)


def _prepare_tool_call(
    contract: SaaSAdapterContract,
    tool_name: object,
    arguments: object,
    used_idempotency_keys: set[str],
) -> tuple[dict[str, Any], str | None]:
    if not isinstance(tool_name, str) or tool_name not in contract.tools:
        raise ValueError("tool_denied")
    if not isinstance(arguments, dict):
        raise ValueError("invalid_tool_arguments")
    encoded = json.dumps(arguments, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_ARGUMENT_BYTES:
        raise ValueError("arguments_too_large")
    clean_arguments = dict(arguments)
    effect = contract.tools[tool_name].effect
    key: str | None = None
    if effect == "state-write":
        key = validate_idempotency_key(clean_arguments.pop(PRIVATE_IDEMPOTENCY_FIELD, None))
        if key in used_idempotency_keys:
            raise ValueError("idempotency_key_replayed")
    elif PRIVATE_IDEMPOTENCY_FIELD in clean_arguments:
        raise ValueError("unexpected_idempotency_key")
    _reject_private_fields(clean_arguments)
    if key is not None:
        # Reserve before execution.  An ambiguous upstream result must never be
        # retried automatically under the same approval/execution identity.
        used_idempotency_keys.add(key)
    return clean_arguments, key


async def _terminate_timed_out_call(
    request_id: object,
    process: asyncio.subprocess.Process,
    client: asyncio.StreamWriter,
    write_lock: asyncio.Lock,
    call_requests: set[object],
    call_deadlines: dict[object, asyncio.Task[None]],
    suppressed_requests: set[object],
    write_call: bool,
) -> None:
    await asyncio.sleep(TOOL_CALL_TIMEOUT_SECONDS)
    current = asyncio.current_task()
    async with write_lock:
        if current is None or call_deadlines.get(request_id) is not current:
            return
        call_deadlines.pop(request_id, None)
        call_requests.discard(request_id)
        suppressed_requests.add(request_id)
        if write_call:
            message = "SaaS 写入调用超时；远端结果未知且不得自动重试。"
            data = {"reason": "unknown_outcome", "retryable": False}
        else:
            message = "SaaS 读取调用超时，会话已安全终止。"
            data = {"reason": "timeout", "retryable": False}
        client.write(_rpc_error(request_id, -32008, message, data=data))
        await client.drain()
    if process.returncode is None:
        process.kill()
        await process.wait()


async def _client_to_child(
    source: asyncio.StreamReader,
    destination: asyncio.StreamWriter,
    client: asyncio.StreamWriter,
    contract: SaaSAdapterContract,
    list_requests: set[object],
    call_requests: set[object],
    write_call_requests: set[object],
    call_deadlines: dict[object, asyncio.Task[None]],
    suppressed_requests: set[object],
    used_idempotency_keys: set[str],
    process: asyncio.subprocess.Process,
    write_lock: asyncio.Lock,
) -> None:
    while True:
        raw = await source.readline()
        if not raw:
            break
        if len(raw) > MAX_MCP_MESSAGE_BYTES:
            raise SandboxEngineError("MCP request too large.", code="mcp_message_too_large")
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
            try:
                clean_arguments, _ = _prepare_tool_call(
                    contract, tool_name, arguments, used_idempotency_keys
                )
            except ValueError as exc:
                code = str(exc)
                message = (
                    "写入执行标识无效或已使用，未执行远程操作。"
                    if code in {"invalid_idempotency_key", "idempotency_key_replayed"}
                    else "工具或参数未通过 SaaS 适配器策略。"
                )
                async with write_lock:
                    client.write(_rpc_error(request_id, -32602, message))
                    await client.drain()
                continue
            assert isinstance(params, dict)
            params["arguments"] = clean_arguments
            raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
            call_requests.add(request_id)
            write_call = contract.tools[tool_name].effect == "state-write"
            if write_call:
                write_call_requests.add(request_id)
            deadline = asyncio.create_task(
                _terminate_timed_out_call(
                    request_id,
                    process,
                    client,
                    write_lock,
                    call_requests,
                    call_deadlines,
                    suppressed_requests,
                    write_call,
                )
            )
            call_deadlines[request_id] = deadline
        destination.write(raw)
        await destination.drain()


async def _child_to_client(
    source: asyncio.StreamReader,
    destination: asyncio.StreamWriter,
    contract: SaaSAdapterContract,
    list_requests: set[object],
    call_requests: set[object],
    write_call_requests: set[object],
    call_deadlines: dict[object, asyncio.Task[None]],
    suppressed_requests: set[object],
    write_lock: asyncio.Lock,
) -> None:
    while True:
        raw = await source.readline()
        if not raw:
            break
        if len(raw) > MAX_MCP_MESSAGE_BYTES or len(raw) > MAX_OUTPUT_BYTES + 16 * 1024:
            raise SandboxEngineError("MCP output too large.", code="mcp_output_too_large")
        output = raw
        try:
            payload = json.loads(raw.decode("utf-8"))
            request_id = payload.get("id") if isinstance(payload, dict) else None
            if request_id in suppressed_requests:
                suppressed_requests.discard(request_id)
                continue
            if request_id in list_requests:
                list_requests.discard(request_id)
                result = payload.get("result") if isinstance(payload, dict) else None
                tools = result.get("tools") if isinstance(result, dict) else None
                if isinstance(tools, list):
                    result["tools"] = [
                        item
                        for item in tools
                        if isinstance(item, dict) and item.get("name") in contract.tools
                    ]
                    output = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
            elif request_id in call_requests:
                call_requests.discard(request_id)
                write_call = request_id in write_call_requests
                write_call_requests.discard(request_id)
                deadline = call_deadlines.pop(request_id, None)
                if deadline is not None:
                    deadline.cancel()
                result = payload.get("result") if isinstance(payload, dict) else None
                failed_result = isinstance(result, dict) and (
                    result.get("isError") is True or result.get("is_error") is True
                )
                unknown_write_outcome = write_call and (
                    (isinstance(payload, dict) and "error" in payload) or failed_result
                ) and UNKNOWN_WRITE_OUTCOME_MARKER.encode("ascii") in raw
                if unknown_write_outcome:
                    output = _rpc_error(
                        request_id,
                        -32008,
                        "SaaS 写入结果未知且不得自动重试。",
                        data={"reason": "unknown_outcome", "retryable": False},
                    )
                elif write_call and (
                    (isinstance(payload, dict) and "error" in payload) or failed_result
                ):
                    reason = (
                        "rate_limited"
                        if b"_http_429" in raw
                        else "provider_rejected"
                    )
                    output = _rpc_error(
                        request_id,
                        -32009,
                        "SaaS 写入未完成，远端已拒绝请求。",
                        data={"reason": reason, "retryable": False},
                    )
                elif isinstance(payload, dict) and "error" in payload:
                    error = payload.get("error")
                    code = error.get("code", -32603) if isinstance(error, dict) else -32603
                    payload["error"] = {"code": code, "message": "SaaS 目录操作失败。"}
                    output = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
                elif isinstance(payload, dict):
                    if failed_result:
                        result.pop("structuredContent", None)
                        result.pop("structured_content", None)
                        result["content"] = [{"type": "text", "text": "SaaS 目录操作失败。"}]
                        output = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        except (UnicodeError, json.JSONDecodeError):
            pass
        async with write_lock:
            destination.write(output)
            await destination.drain()


async def _wait_for_child_ready(
    stream: asyncio.StreamReader | None,
    process: asyncio.subprocess.Process,
    adapter_id: str,
) -> None:
    if stream is None:
        raise SandboxEngineError("SaaS child stderr unavailable.", code="preflight_failed")
    expected = f"MODELMIRROR_SAAS_READY:{adapter_id}"
    for _ in range(8):
        try:
            raw = await asyncio.wait_for(stream.readline(), timeout=15)
        except asyncio.TimeoutError as exc:
            raise SandboxEngineError("SaaS preflight timed out.", code="preflight_timeout") from exc
        if not raw:
            break
        line = raw.decode("utf-8", errors="replace").strip()
        if line == expected:
            return
        if line.startswith("MODELMIRROR_SAAS_FAILED:"):
            break
    if process.returncode is None:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=1)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
    raise SandboxEngineError("SaaS credential or scope preflight failed.", code="preflight_failed")


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
        raise SandboxEngineError("SaaS adapter disabled.", code="mcp_adapter_denied")
    try:
        contract, credentials, settings = validate_configuration(
            adapter_id, request.get("configuration")
        )
    except ValueError as exc:
        raise SandboxEngineError("SaaS adapter configuration denied.", code=str(exc)) from exc
    request["configuration"] = None

    async with SEMAPHORE:
        temp_root = Path(tempfile.mkdtemp(prefix=f"mcp-saas-{adapter_id[:12]}-"))
        work_dir = temp_root / "work"
        work_dir.mkdir(mode=0o700)
        encoded_configuration = base64.urlsafe_b64encode(
            json.dumps(
                {"credentials": credentials, "settings": settings},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).decode("ascii")
        credentials.clear()
        settings.clear()
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "PYTHONPATH": "/opt/modelmirror",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONUNBUFFERED": "1",
            "HOME": str(work_dir),
            "TMPDIR": str(work_dir),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TZ": "UTC",
            "NO_PROXY": "*",
            "no_proxy": "*",
            "DO_NOT_TRACK": "1",
            "MCP_SAAS_HANDSHAKE_B64": encoded_configuration,
        }
        encoded_configuration = ""
        command = [
            sys.executable,
            str(Path(__file__).with_name("landlock_exec.py")),
            str(temp_root),
            "--",
            sys.executable,
            "-m",
            "sandbox_sidecar.saas_mcp",
            adapter_id,
        ]
        process: asyncio.subprocess.Process | None = None
        tasks: list[asyncio.Task[None]] = []
        handshake_sent = False
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(work_dir),
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                limit=MAX_MCP_MESSAGE_BYTES + 1,
            )
            env.clear()
            if process.stdin is None or process.stdout is None:
                raise SandboxEngineError("SaaS MCP stdio unavailable.", code="mcp_stdio_unavailable")
            await _wait_for_child_ready(process.stderr, process, adapter_id)
            writer.write(
                json.dumps(
                    {
                        "ok": True,
                        "adapter_id": adapter_id,
                        "protocol": "modelmirror-mcp-saas-stdio-v1",
                        "preflight": "verified",
                        "tools": sorted(contract.tools),
                        "effects": {name: policy.effect for name, policy in contract.tools.items()},
                        "limits": {
                            "timeout_seconds": TOOL_CALL_TIMEOUT_SECONDS,
                            "argument_bytes": MAX_ARGUMENT_BYTES,
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
            write_call_requests: set[object] = set()
            call_deadlines: dict[object, asyncio.Task[None]] = {}
            suppressed_requests: set[object] = set()
            used_idempotency_keys: set[str] = set()
            write_lock = asyncio.Lock()
            tasks = [
                asyncio.create_task(
                    _client_to_child(
                        reader,
                        process.stdin,
                        writer,
                        contract,
                        list_requests,
                        call_requests,
                        write_call_requests,
                        call_deadlines,
                        suppressed_requests,
                        used_idempotency_keys,
                        process,
                        write_lock,
                    )
                ),
                asyncio.create_task(
                    _child_to_client(
                        process.stdout,
                        writer,
                        contract,
                        list_requests,
                        call_requests,
                        write_call_requests,
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
            raise SandboxEngineError("SaaS sidecar request invalid.", code="invalid_request")
        request = json.loads(raw.decode("utf-8"))
        if not isinstance(request, dict):
            raise SandboxEngineError("SaaS sidecar request must be an object.", code="invalid_request")
        action = str(request.get("action") or "").strip()
        if action == "mcp_stdio":
            await _stdio(reader, writer, request)
            return
        if action != "health":
            raise SandboxEngineError("SaaS sidecar action denied.", code="action_denied")
        response = {
            "ok": True,
            "protocol": "modelmirror-mcp-saas-stdio-v1",
            "mcp_saas_adapters": sorted(ALLOWED_ADAPTERS),
            "mcp_saas_max_sessions": MAX_SESSIONS,
            "mcp_message_limit_bytes": MAX_MCP_MESSAGE_BYTES,
        }
    except SandboxEngineError as exc:
        response = {"ok": False, "error": "saas_sidecar_rejected", "code": exc.code}
    except Exception:
        response = {
            "ok": False,
            "error": "saas_sidecar_internal_error",
            "code": "saas_sidecar_internal_error",
        }
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
