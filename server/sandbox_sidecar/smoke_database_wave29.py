"""Contract and real VictoriaMetrics smoke for the Wave 30 database adapter."""

from __future__ import annotations

import argparse
import asyncio
import base64
import datetime as dt
import hashlib
import json
import os
import socket
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .database_wave29 import (
    VICTORIA_ADAPTER_ID,
    WAVE29_DATABASE_SCHEMA_SHA256,
    build_victoriametrics,
)


MAX_HANDSHAKE_BYTES = 4 * 1024
VICTORIA_TEST_SERVICE_IMAGE = (
    "victoriametrics/victoria-metrics:v1.148.0@"
    "sha256:407013e902f9a0ba1d4b2d4c077c47bbaf917c893c52ff39b19efe83a654afda"
)


def _load_mcp_stdio() -> tuple[Any, Any, Any]:
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    return ClientSession, StdioServerParameters, stdio_client


def _digest(tools: list[Any]) -> str:
    reviewed = [
        {"name": tool.name, "inputSchema": tool.inputSchema}
        for tool in sorted(tools, key=lambda item: item.name)
    ]
    return hashlib.sha256(
        json.dumps(
            reviewed,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


async def contract_only() -> None:
    context = SimpleNamespace(
        adapter_id=VICTORIA_ADAPTER_ID,
        settings={
            "host": "metrics.example.com",
            "port": 443,
            "tls_mode": "verify-full",
            "metric": "process_cpu_cores_available",
        },
        credentials={},
    )
    tools = await build_victoriametrics(context).list_tools()
    digest = _digest(tools)
    if {tool.name for tool in tools} != {"metrics", "labels", "query", "query_range"}:
        raise RuntimeError("wave30_victoriametrics_tool_contract_drift")
    if digest != WAVE29_DATABASE_SCHEMA_SHA256[VICTORIA_ADAPTER_ID]:
        raise RuntimeError("wave30_victoriametrics_schema_contract_drift")
    print(
        f"adapter={VICTORIA_ADAPTER_ID} tools=4 schema_sha256={digest}",
        flush=True,
    )
    print("wave30_database_contract_smoke=ok", flush=True)


def _copy_stdin(sock: socket.socket) -> None:
    try:
        while True:
            chunk = os.read(sys.stdin.fileno(), 64 * 1024)
            if not chunk:
                break
            sock.sendall(chunk)
    except (BrokenPipeError, ConnectionError, OSError):
        pass
    finally:
        try:
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def _proxy(socket_path: Path) -> int:
    encoded = os.environ.pop("MCP_DATABASE_SMOKE_HANDSHAKE_B64", "")
    try:
        configuration = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError):
        return 78
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(10)
        sock.connect(str(socket_path))
        sock.sendall(
            json.dumps(
                {
                    "action": "mcp_stdio",
                    "adapter_id": VICTORIA_ADAPTER_ID,
                    "configuration": configuration,
                },
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        configuration = {}
        raw = sock.makefile("rb", buffering=0).readline(MAX_HANDSHAKE_BYTES + 1)
        response = json.loads(raw.decode("utf-8"))
        if not isinstance(response, dict) or response.get("ok") is not True:
            return 69
        sock.settimeout(None)
        threading.Thread(target=_copy_stdin, args=(sock,), daemon=True).start()
        while True:
            chunk = sock.recv(64 * 1024)
            if not chunk:
                break
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
        return 0
    except (ConnectionError, OSError, ValueError, json.JSONDecodeError):
        return 69
    finally:
        sock.close()


def _parameters(socket_path: Path, host: str, port: int) -> Any:
    _client, parameters_type, _runtime = _load_mcp_stdio()
    configuration = {
        "settings": {
            "host": host,
            "port": port,
            "tls_mode": "test-only-plaintext",
            "metric": "process_cpu_cores_available",
        },
        "credentials": {},
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(configuration, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    return parameters_type(
        command=sys.executable,
        args=["-m", "sandbox_sidecar.smoke_database_wave29", "--proxy", str(socket_path)],
        env={
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "PYTHONPATH": os.environ.get("PYTHONPATH", "/opt/modelmirror"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONUNBUFFERED": "1",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TZ": "UTC",
            "MCP_DATABASE_SMOKE_HANDSHAKE_B64": encoded,
        },
        cwd=Path("/tmp"),
    )


def _decode(result: Any) -> dict[str, Any]:
    if result.isError:
        raise RuntimeError("wave30_victoriametrics_tool_failed")
    if isinstance(result.structuredContent, dict):
        value = result.structuredContent.get("result", result.structuredContent)
        if isinstance(value, dict):
            return value
    raise RuntimeError("wave30_victoriametrics_result_invalid")


async def _denied(session: Any, tool_name: str, arguments: dict[str, Any]) -> bool:
    try:
        result = await session.call_tool(tool_name, arguments)
    except Exception:
        return True
    return bool(result.isError)


async def _representative(socket_path: Path, host: str, port: int) -> None:
    client_type, _parameters_type, runtime = _load_mcp_stdio()
    async with runtime(_parameters(socket_path, host, port)) as streams:
        async with client_type(*streams) as session:
            await session.initialize()
            listed = await session.list_tools()
            digest = _digest(listed.tools)
            if digest != WAVE29_DATABASE_SCHEMA_SHA256[VICTORIA_ADAPTER_ID]:
                raise RuntimeError("wave30_victoriametrics_runtime_schema_drift")
            metrics = _decode(await session.call_tool("metrics", {}))
            labels = _decode(await session.call_tool("labels", {}))
            instant = _decode(await session.call_tool("query", {}))
            now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
            ranged = _decode(
                await session.call_tool(
                    "query_range",
                    {
                        "start": (now - dt.timedelta(minutes=2)).isoformat().replace("+00:00", "Z"),
                        "end": now.isoformat().replace("+00:00", "Z"),
                        "step_seconds": 10,
                    },
                )
            )
            if not await _denied(session, "query", {"query": "up"}):
                raise RuntimeError("wave30_victoriametrics_arbitrary_query_exposed")
            if not await _denied(session, "write", {}):
                raise RuntimeError("wave30_victoriametrics_write_surface_exposed")
            if (
                "process_cpu_cores_available" not in metrics.get("items", [])
                or not labels.get("items")
                or instant.get("result_type") != "vector"
                or instant.get("series_count", 0) < 1
                or ranged.get("result_type") != "matrix"
            ):
                raise RuntimeError("wave30_victoriametrics_representative_result_invalid")
            print("input.victoriametrics.metric=process_cpu_cores_available", flush=True)
            print(f"result.victoriametrics.metric_count={metrics.get('count', 0)}", flush=True)
            print(f"result.victoriametrics.label_count={labels.get('count', 0)}", flush=True)
            print(f"result.victoriametrics.instant_series={instant.get('series_count', 0)}", flush=True)
            print(f"result.victoriametrics.range_series={ranged.get('series_count', 0)}", flush=True)


async def _timeout(socket_path: Path, host: str, port: int) -> None:
    client_type, _parameters_type, runtime = _load_mcp_stdio()
    async with runtime(_parameters(socket_path, host, port)) as streams:
        async with client_type(*streams) as session:
            await session.initialize()
            now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
            try:
                await asyncio.wait_for(
                    session.call_tool(
                        "query_range",
                        {
                            "start": (now - dt.timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
                            "end": now.isoformat().replace("+00:00", "Z"),
                            "step_seconds": 2,
                        },
                    ),
                    timeout=0.000001,
                )
            except asyncio.TimeoutError:
                print("timeout_probe=cancelled_and_session_closed", flush=True)
                return
    raise RuntimeError("wave30_victoriametrics_timeout_probe_failed")


async def runtime_smoke(socket_path: Path, host: str, port: int) -> None:
    if not socket_path.is_absolute() or not host or port < 1 or port > 65535:
        raise RuntimeError("wave30_victoriametrics_smoke_arguments_invalid")
    await _representative(socket_path, host, port)
    await _timeout(socket_path, host, port)
    print(
        "wave30_database_runtime_smoke=ok adapter=" + VICTORIA_ADAPTER_ID
        + " readonly=true blocked_write=denied cleanup=session-closed",
        flush=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract-only", action="store_true")
    parser.add_argument("--proxy")
    parser.add_argument("--socket")
    parser.add_argument("--host", default="mm-wave30-vm")
    parser.add_argument("--port", type=int, default=8428)
    args = parser.parse_args()
    if args.proxy:
        return _proxy(Path(args.proxy))
    if args.contract_only:
        asyncio.run(contract_only())
        return 0
    if not args.socket:
        parser.error("--socket is required")
    asyncio.run(runtime_smoke(Path(args.socket), args.host, args.port))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
