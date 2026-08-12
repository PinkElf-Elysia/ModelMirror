"""Contract and real offline smoke for staged Wave 26A file adapters."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mcp.client.session import ClientSession

from .file_mcp import opaque_file_id
from .file_wave26 import (
    CALCULATOR_ADAPTER_ID,
    IMAGESORCERY_ADAPTER_ID,
    WAVE26_BUILDERS,
    WAVE26_SCHEMA_SHA256,
    WAVE26_TOOL_NAMES,
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
    for adapter_id, builder in sorted(WAVE26_BUILDERS.items()):
        tools = await builder(object()).list_tools()
        names = {tool.name for tool in tools}
        digest = _digest(tools)
        if names != set(WAVE26_TOOL_NAMES[adapter_id]):
            raise RuntimeError("wave26_tool_contract_drift")
        if digest != WAVE26_SCHEMA_SHA256[adapter_id]:
            raise RuntimeError("wave26_schema_contract_drift")
        print(
            f"adapter={adapter_id} tools={len(names)} schema_sha256={digest}",
            flush=True,
        )
    print("wave26_file_contract_smoke=ok", flush=True)


def _base_env(root: Path, workspace_id: str) -> dict[str, str]:
    temp_root = root / "tmp"
    temp_root.mkdir(parents=True, exist_ok=True)
    return {
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
        "MCP_FILE_WORKSPACE_ID": workspace_id,
        "MCP_FILE_ADAPTER_ID": "",
        "MCP_FILE_INPUT_ROOT": str(root / "inputs"),
        "MCP_FILE_OUTPUT_ROOT": str(root / "outputs"),
        "MCP_FILE_MEMORY_ROOT": str(root / "memory"),
    }


def _write_fixture(adapter_id: str, input_root: Path) -> tuple[str | None, str]:
    if adapter_id == CALCULATOR_ADAPTER_ID:
        return None, ""
    from PIL import Image

    path = input_root / "source.png"
    image = Image.new("RGB", (96, 64), color=(20, 40, 80))
    for x in range(16, 80):
        for y in range(12, 52):
            image.putpixel((x, y), (220, 140, 30))
    image.save(path, format="PNG", optimize=False, compress_level=9)
    image.close()
    return opaque_file_id(input_root.name, path.name), path.name


async def _call_ok(session: ClientSession, name: str, arguments: dict[str, Any]) -> Any:
    result = await session.call_tool(name, arguments)
    if result.isError:
        raise RuntimeError(f"wave26_representative_call_failed:{name}")
    return result


async def _one_round(adapter_id: str, root: Path, round_number: int) -> dict[str, bytes]:
    client_session_type, stdio_parameters_type, stdio_client_runtime = _load_mcp_stdio()
    workspace_id = "mcpws_" + hashlib.sha256(
        f"{adapter_id}:{round_number}".encode()
    ).hexdigest()[:32]
    input_root = root / "inputs" / workspace_id
    output_root = root / "outputs" / workspace_id
    memory_root = root / "memory" / workspace_id
    input_root.mkdir(parents=True)
    output_root.mkdir(parents=True)
    memory_root.mkdir(parents=True)
    file_id, source_name = _write_fixture(adapter_id, input_root)
    source_hash = (
        hashlib.sha256((input_root / source_name).read_bytes()).hexdigest()
        if source_name
        else ""
    )
    server, socket_path = await _start_file_server(
        root,
        allowed_adapters=adapter_id,
    )
    env = _base_env(root, workspace_id)
    env["MCP_FILES_SOCKET_PATH"] = str(socket_path)
    params = stdio_parameters_type(
        command=sys.executable,
        args=[
            "-m",
            "sandbox_sidecar.smoke_file_wave26",
            "--proxy",
            adapter_id,
        ],
        env=env,
        cwd=Path(env["TMPDIR"]),
    )
    try:
        async with stdio_client_runtime(params) as (read_stream, write_stream):
            async with client_session_type(read_stream, write_stream) as session:
                await session.initialize()
                listed = await session.list_tools()
                if {tool.name for tool in listed.tools} != set(WAVE26_TOOL_NAMES[adapter_id]):
                    raise RuntimeError("wave26_runtime_tool_drift")
                if _digest(listed.tools) != WAVE26_SCHEMA_SHA256[adapter_id]:
                    raise RuntimeError("wave26_runtime_schema_drift")
                if adapter_id == CALCULATOR_ADAPTER_ID:
                    for expression, expected in (
                        ("2 + 3 * 4", "14"),
                        ("sqrt(81) + sin(pi / 2)", "10.0"),
                    ):
                        result = await _call_ok(
                            session,
                            "calculate",
                            {"expression": expression},
                        )
                        structured = result.structuredContent or {}
                        if structured.get("numeric_result") is None:
                            raise RuntimeError("wave26_calculator_result_missing")
                        if str(structured.get("result")) != expected:
                            raise RuntimeError("wave26_calculator_result_drift")
                    for expression in (
                        "__import__('os').system('id')",
                        "(1).__class__",
                        "9**999999",
                    ):
                        denied = await session.call_tool(
                            "calculate",
                            {"expression": expression},
                        )
                        if not denied.isError:
                            raise RuntimeError("wave26_calculator_code_execution_exposed")
                else:
                    assert file_id is not None
                    await _call_ok(session, "get_metainfo", {"file_id": file_id})
                    await _call_ok(
                        session,
                        "resize",
                        {
                            "file_id": file_id,
                            "width": 48,
                            "interpolation": "area",
                            "artifact_name": "resized.png",
                        },
                    )
                    await _call_ok(
                        session,
                        "crop",
                        {
                            "file_id": file_id,
                            "x1": 8,
                            "y1": 8,
                            "x2": 72,
                            "y2": 48,
                            "artifact_name": "cropped.png",
                        },
                    )
                    await _call_ok(
                        session,
                        "rotate",
                        {
                            "file_id": file_id,
                            "angle": 90,
                            "artifact_name": "rotated.png",
                        },
                    )
                    for blocked_name, arguments in (
                        ("detect", {"input_path": "/inputs/source.png"}),
                        ("ocr", {"input_path": "https://example.com/a.png"}),
                        ("resize", {"file_id": file_id, "width": 32, "output_path": "/tmp/out.png"}),
                        ("resize", {"file_id": "file:///etc/passwd", "width": 32}),
                    ):
                        denied = await session.call_tool(blocked_name, arguments)
                        if not denied.isError:
                            raise RuntimeError("wave26_imagesorcery_unsafe_surface_exposed")
    finally:
        await _stop_process(server)
    if source_name and hashlib.sha256((input_root / source_name).read_bytes()).hexdigest() != source_hash:
        raise RuntimeError("wave26_source_mutated")
    artifacts = {
        path.name: path.read_bytes()
        for path in sorted(output_root.iterdir())
        if path.is_file() and not path.is_symlink()
    }
    if adapter_id == IMAGESORCERY_ADAPTER_ID:
        if set(artifacts) != {"resized.png", "cropped.png", "rotated.png"}:
            raise RuntimeError("wave26_artifact_set_drift")
        for data in artifacts.values():
            if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                raise RuntimeError("wave26_png_invalid")
            if len(data) > 32 * 1024 * 1024:
                raise RuntimeError("wave26_artifact_too_large")
    elif artifacts:
        raise RuntimeError("wave26_calculator_created_artifact")
    return artifacts


async def _wait_for_socket(socket_path: Path) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if socket_path.is_socket():
            return
        await asyncio.sleep(0.05)
    raise RuntimeError("wave26_file_server_start_timeout")


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=3)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()


async def _start_file_server(
    root: Path,
    *,
    allowed_adapters: str | None,
) -> tuple[asyncio.subprocess.Process, Path]:
    socket_path = root / "run" / "files-mcp.sock"
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    socket_path.unlink(missing_ok=True)
    env = _base_env(root, "mcpws_" + "f" * 32)
    env.update(
        {
            "MCP_FILES_SOCKET_PATH": str(socket_path),
            "MCP_FILES_MAX_SESSIONS": "1",
        }
    )
    if allowed_adapters is not None:
        env["MCP_FILE_ALLOWED_ADAPTERS"] = allowed_adapters
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "sandbox_sidecar.file_server",
        cwd=Path(env["TMPDIR"]),
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await _wait_for_socket(socket_path)
    except Exception:
        await _stop_process(process)
        raise
    return process, socket_path


async def _default_deny_probe(root: Path) -> None:
    workspace_id = "mcpws_" + "d" * 32
    (root / "inputs" / workspace_id).mkdir(parents=True, exist_ok=True)
    process, socket_path = await _start_file_server(
        root,
        allowed_adapters=None,
    )
    try:
        for adapter_id in sorted(WAVE26_BUILDERS):
            reader, writer = await asyncio.open_unix_connection(str(socket_path))
            writer.write(
                json.dumps(
                    {
                        "action": "mcp_stdio",
                        "adapter_id": adapter_id,
                        "workspace_id": workspace_id,
                    },
                    separators=(",", ":"),
                ).encode()
                + b"\n"
            )
            await writer.drain()
            response = json.loads(
                (await asyncio.wait_for(reader.readline(), timeout=3)).decode()
            )
            writer.close()
            await writer.wait_closed()
            if response.get("ok") is not False or response.get("code") != "mcp_adapter_denied":
                raise RuntimeError("wave26_default_deny_failed")
    finally:
        await _stop_process(process)


def _wave26_child_count() -> int:
    count = 0
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            command = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if b"sandbox_sidecar.file_mcp\0" in command and IMAGESORCERY_ADAPTER_ID.encode() in command:
            count += 1
    return count


async def _timeout_probe(root: Path) -> None:
    from PIL import Image

    client_session_type, stdio_parameters_type, stdio_client_runtime = _load_mcp_stdio()
    workspace_id = "mcpws_" + "e" * 32
    input_root = root / "inputs" / workspace_id
    input_root.mkdir(parents=True, exist_ok=True)
    source = input_root / "large.png"
    image = Image.new("RGB", (3_000, 3_000), color=(24, 48, 96))
    image.save(source, format="PNG", optimize=False, compress_level=9)
    image.close()
    file_id = opaque_file_id(workspace_id, source.name)
    process, socket_path = await _start_file_server(
        root,
        allowed_adapters=IMAGESORCERY_ADAPTER_ID,
    )
    proxy_env = _base_env(root, workspace_id)
    proxy_env["MCP_FILES_SOCKET_PATH"] = str(socket_path)
    params = stdio_parameters_type(
        command=sys.executable,
        args=[
            "-m",
            "sandbox_sidecar.smoke_file_wave26",
            "--proxy",
            IMAGESORCERY_ADAPTER_ID,
        ],
        env=proxy_env,
        cwd=Path(proxy_env["TMPDIR"]),
    )
    started = time.monotonic()
    try:
        async with stdio_client_runtime(params) as (read_stream, write_stream):
            async with client_session_type(read_stream, write_stream) as session:
                await session.initialize()
                try:
                    await asyncio.wait_for(
                        session.call_tool(
                            "rotate",
                            {
                                "file_id": file_id,
                                "angle": 33,
                                "artifact_name": "timeout.png",
                            },
                        ),
                        timeout=0.001,
                    )
                except asyncio.TimeoutError:
                    pass
                else:
                    raise RuntimeError("wave26_timeout_not_triggered")
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and _wave26_child_count():
            await asyncio.sleep(0.05)
        if _wave26_child_count():
            raise RuntimeError("wave26_timeout_process_leaked")
        if time.monotonic() - started > 5:
            raise RuntimeError("wave26_timeout_cleanup_slow")
    finally:
        await _stop_process(process)


def _copy_proxy_stdin(sock: socket.socket) -> None:
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


def _proxy(adapter_id: str) -> int:
    if adapter_id not in WAVE26_BUILDERS:
        return 64
    socket_path = Path(os.environ["MCP_FILES_SOCKET_PATH"])
    workspace_id = os.environ["MCP_FILE_WORKSPACE_ID"]
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(5)
        sock.connect(str(socket_path))
        sock.sendall(
            json.dumps(
                {
                    "action": "mcp_stdio",
                    "adapter_id": adapter_id,
                    "workspace_id": workspace_id,
                },
                separators=(",", ":"),
            ).encode()
            + b"\n"
        )
        response = json.loads(sock.makefile("rb", buffering=0).readline(4097).decode())
        if response.get("ok") is not True:
            return 69
        sock.settimeout(None)
        threading.Thread(target=_copy_proxy_stdin, args=(sock,), daemon=True).start()
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


async def runtime_smoke() -> None:
    base = Path(tempfile.mkdtemp(prefix="wave26-file-smoke-"))
    try:
        await _default_deny_probe(base / "default-deny")
        for adapter_id in sorted(WAVE26_BUILDERS):
            first = await asyncio.wait_for(
                _one_round(adapter_id, base / "round-1", 1), timeout=30
            )
            second = await asyncio.wait_for(
                _one_round(adapter_id, base / "round-2", 2), timeout=30
            )
            if first != second:
                raise RuntimeError("wave26_artifact_not_deterministic")
            print(
                f"adapter={adapter_id} rounds=2 artifacts={len(second)}",
                flush=True,
            )
        await _timeout_probe(base / "timeout")
    finally:
        shutil.rmtree(base, ignore_errors=False)
    if base.exists():
        raise RuntimeError("wave26_cleanup_failed")
    print(
        "wave26_file_runtime_smoke=ok network=none source_immutable=true "
        "deterministic=true default_deny=true timeout=verified cleanup=verified",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract-only", action="store_true")
    parser.add_argument("--proxy", choices=tuple(sorted(WAVE26_BUILDERS)))
    args = parser.parse_args()
    if args.proxy:
        raise SystemExit(_proxy(args.proxy))
    asyncio.run(contract_only() if args.contract_only else runtime_smoke())


if __name__ == "__main__":
    main()
