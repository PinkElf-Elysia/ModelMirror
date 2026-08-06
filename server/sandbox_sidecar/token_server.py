"""Isolated Unix-socket runtime and read-only JSON-RPC gateway for Wave 4."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

from .engine import MAX_REQUEST_BYTES, SandboxEngineError
from .safe_http import NetworkPolicyError, resolve_public_addresses, validate_public_https_url
from .token_contracts import TOKEN_ADAPTERS, TokenAdapterContract, validate_configuration


SOCKET_PATH = Path(os.getenv("MCP_TOKEN_SOCKET_PATH", "/run/modelmirror-token-mcp/token-mcp.sock"))
WORKSPACE_ROOT = Path(os.getenv("MCP_TOKEN_WORKSPACE_ROOT", "/workspaces"))
MAX_MCP_MESSAGE_BYTES = 256 * 1024
MAX_TOKEN_SESSIONS = max(1, min(int(os.getenv("MCP_TOKEN_MAX_SESSIONS", "6")), 12))
TOKEN_SEMAPHORE = asyncio.Semaphore(MAX_TOKEN_SESSIONS)


def _rpc_error(request_id: object, code: int, message: str) -> bytes:
    return (
        json.dumps(
            {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _walk_url_values(value: object, *, key: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for child_key, child in value.items():
            found.extend(_walk_url_values(child, key=str(child_key).lower()))
    elif isinstance(value, list):
        for child in value:
            found.extend(_walk_url_values(child, key=key))
    elif isinstance(value, str) and ("url" in key or "uri" in key):
        found.append(value)
    return found


def _validate_argument_targets(arguments: object) -> None:
    if not isinstance(arguments, dict):
        raise NetworkPolicyError("工具参数必须是对象。")
    encoded = json.dumps(arguments, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > 128 * 1024:
        raise NetworkPolicyError("工具参数超过 128 KiB 上限。")
    for target in _walk_url_values(arguments):
        _, host, port, _ = validate_public_https_url(target)
        resolve_public_addresses(host, port)


async def _client_to_child(
    source: asyncio.StreamReader,
    destination: asyncio.StreamWriter,
    client: asyncio.StreamWriter,
    contract: TokenAdapterContract,
    list_requests: set[object],
    write_lock: asyncio.Lock,
) -> None:
    while True:
        raw = await source.readline()
        if not raw:
            break
        if len(raw) > MAX_MCP_MESSAGE_BYTES:
            raise SandboxEngineError("MCP message exceeds 256 KiB.", code="mcp_message_too_large")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        method = payload.get("method")
        request_id = payload.get("id")
        if method == "tools/list" and request_id is not None:
            list_requests.add(request_id)
        if method == "tools/call":
            params = payload.get("params")
            tool_name = params.get("name") if isinstance(params, dict) else None
            arguments = params.get("arguments", {}) if isinstance(params, dict) else {}
            if tool_name not in contract.tools:
                async with write_lock:
                    client.write(_rpc_error(request_id, -32601, "该工具未通过只读策略审核。"))
                    await client.drain()
                continue
            try:
                _validate_argument_targets(arguments)
            except NetworkPolicyError:
                async with write_lock:
                    client.write(_rpc_error(request_id, -32602, "URL 参数未通过公网 HTTPS 与 DNS 安全校验。"))
                    await client.drain()
                continue
        destination.write(raw)
        await destination.drain()


async def _child_to_client(
    source: asyncio.StreamReader,
    destination: asyncio.StreamWriter,
    contract: TokenAdapterContract,
    list_requests: set[object],
    write_lock: asyncio.Lock,
) -> None:
    while True:
        raw = await source.readline()
        if not raw:
            break
        if len(raw) > MAX_MCP_MESSAGE_BYTES:
            raise SandboxEngineError("MCP child output exceeds 256 KiB.", code="mcp_output_too_large")
        output = raw
        try:
            payload = json.loads(raw.decode("utf-8"))
            request_id = payload.get("id") if isinstance(payload, dict) else None
            if request_id in list_requests:
                list_requests.discard(request_id)
                result = payload.get("result")
                tools = result.get("tools") if isinstance(result, dict) else None
                if isinstance(tools, list):
                    result["tools"] = [
                        item for item in tools
                        if isinstance(item, dict) and item.get("name") in contract.tools
                    ]
                    output = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
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


def _child_environment(
    contract: TokenAdapterContract,
    credentials: dict[str, str],
    settings: dict[str, str],
    workspace: Path,
) -> dict[str, str]:
    hosts = set(contract.allowed_hosts)
    if "stack_slug" in settings:
        hosts.add(f"{settings['stack_slug']}.grafana.net")
    if "assistant_host" in settings:
        hosts.add(settings["assistant_host"])
    env = {
        "PATH": "/opt/modelmirror/node_modules/.bin:/usr/local/bin:/usr/bin:/bin",
        "PYTHONPATH": "/opt/modelmirror",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONUNBUFFERED": "1",
        "HOME": str(workspace / "work"),
        "TMPDIR": str(workspace / "work"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "DO_NOT_TRACK": "1",
        "FRAMELINK_TELEMETRY": "off",
        "DISABLE_TELEMETRY": "true",
        "NO_PROXY": "*",
        "no_proxy": "*",
        "MCP_ALLOWED_HOSTS": ",".join(sorted(hosts)),
        "NODE_OPTIONS": "--require=/opt/modelmirror/sandbox_sidecar/network_guard.cjs",
    }
    for slot, environment_name in contract.credential_environment:
        env[environment_name] = credentials[slot]
    for key, environment_name in contract.setting_environment:
        env[environment_name] = settings[key]
    return env


async def _handle_mcp_stdio(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    request: dict[str, Any],
) -> None:
    adapter_id = str(request.get("adapter_id") or "").strip()
    try:
        contract, credentials, settings = validate_configuration(
            adapter_id, request.get("configuration")
        )
    except ValueError as exc:
        raise SandboxEngineError("Token adapter configuration denied.", code=str(exc)) from exc
    request["configuration"] = None

    async with TOKEN_SEMAPHORE:
        workspace = (WORKSPACE_ROOT / f"mcp-token-{uuid.uuid4().hex}").resolve()
        if workspace.parent != WORKSPACE_ROOT.resolve():
            raise SandboxEngineError("Unsafe Token MCP workspace.", code="unsafe_workspace")
        (workspace / "work").mkdir(parents=True, exist_ok=False)
        command = [
            sys.executable,
            str(Path(__file__).with_name("landlock_exec.py")),
            str(workspace),
            "--",
            *contract.command,
        ]
        env = _child_environment(contract, credentials, settings, workspace)
        credentials.clear()
        settings.clear()
        process: asyncio.subprocess.Process | None = None
        tasks: list[asyncio.Task[None]] = []
        handshake_sent = False
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(workspace / "work"),
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                limit=MAX_MCP_MESSAGE_BYTES + 1,
            )
            env.clear()
            if process.stdin is None or process.stdout is None:
                raise SandboxEngineError("MCP Token stdio unavailable.", code="mcp_stdio_unavailable")
            writer.write(json.dumps({"ok": True, "adapter_id": adapter_id, "protocol": "modelmirror-mcp-token-stdio-v1"}, separators=(",", ":")).encode("utf-8") + b"\n")
            await writer.drain()
            handshake_sent = True
            list_requests: set[object] = set()
            write_lock = asyncio.Lock()
            tasks = [
                asyncio.create_task(_client_to_child(reader, process.stdin, writer, contract, list_requests, write_lock)),
                asyncio.create_task(_child_to_client(process.stdout, writer, contract, list_requests, write_lock)),
                asyncio.create_task(_drain_stderr(process.stderr)),
            ]
            done, pending = await asyncio.wait(tasks[:2], return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            for task in pending:
                task.cancel()
        except Exception as exc:
            if not handshake_sent:
                raise
            print(f"MCP Token session ended: adapter={adapter_id} error_type={type(exc).__name__}", file=sys.stderr)
        finally:
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
            shutil.rmtree(workspace, ignore_errors=True)
            if handshake_sent:
                writer.close()
                await writer.wait_closed()


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        raw = await reader.readline()
        if not raw or len(raw) > MAX_REQUEST_BYTES:
            raise SandboxEngineError("Token sidecar request is empty or too large.", code="invalid_request")
        request = json.loads(raw.decode("utf-8"))
        if not isinstance(request, dict):
            raise SandboxEngineError("Token sidecar request must be an object.", code="invalid_request")
        action = str(request.get("action") or "").strip()
        if action == "mcp_stdio":
            await _handle_mcp_stdio(reader, writer, request)
            return
        if action != "health":
            raise SandboxEngineError("Token sidecar action denied.", code="action_denied")
        response = {"ok": True, "mcp_token_adapters": sorted(TOKEN_ADAPTERS), "mcp_token_max_sessions": MAX_TOKEN_SESSIONS, "mcp_message_limit_bytes": MAX_MCP_MESSAGE_BYTES}
    except SandboxEngineError as exc:
        response = {"ok": False, "error": str(exc), "code": exc.code}
    except Exception as exc:
        response = {"ok": False, "error": type(exc).__name__, "code": "token_sidecar_internal_error"}
    writer.write(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")
    await writer.drain()
    writer.close()
    await writer.wait_closed()


async def main() -> None:
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    SOCKET_PATH.unlink(missing_ok=True)
    server = await asyncio.start_unix_server(handle_client, path=str(SOCKET_PATH), limit=MAX_REQUEST_BYTES + 1)
    os.chmod(SOCKET_PATH, 0o660)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
