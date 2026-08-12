"""Contract and real offline UDS smoke for staged Wave 20 code indexing."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters

from .file_code_index import (
    GOGRAPH_ADAPTER_ID,
    GOGRAPH_BINARY,
    GOGRAPH_VERSION,
    GoGraphRuntime,
    WAVE20_BUILDERS,
    WAVE20_SCHEMA_SHA256,
    WAVE20_TOOL_NAMES,
)
from .smoke_file_artifacts import _base_env, _start_file_server, _stop_process


def _load_mcp_stdio() -> tuple[Any, Any, Any]:
    """Load the official SDK only inside isolated runtime smoke paths."""

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
    with tempfile.TemporaryDirectory(prefix="wave20-contract-") as temporary:
        root = Path(temporary)
        input_root = root / "input"
        input_root.mkdir()
        previous_tmpdir = os.environ.get("TMPDIR")
        os.environ["TMPDIR"] = str(root / "tmp")
        try:
            tools = await WAVE20_BUILDERS[GOGRAPH_ADAPTER_ID](
                SimpleNamespace(input_root=input_root)
            ).list_tools()
        finally:
            if previous_tmpdir is None:
                os.environ.pop("TMPDIR", None)
            else:
                os.environ["TMPDIR"] = previous_tmpdir
    names = {tool.name for tool in tools}
    digest = _digest(tools)
    if names != set(WAVE20_TOOL_NAMES[GOGRAPH_ADAPTER_ID]):
        raise RuntimeError("wave20_tool_contract_drift")
    if digest != WAVE20_SCHEMA_SHA256[GOGRAPH_ADAPTER_ID]:
        raise RuntimeError("wave20_schema_contract_drift")
    if GOGRAPH_BINARY.is_file():
        version = subprocess.run(
            [str(GOGRAPH_BINARY), "version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        if version != f"gograph version v{GOGRAPH_VERSION}":
            raise RuntimeError("wave20_binary_version_drift")
    print(
        f"adapter={GOGRAPH_ADAPTER_ID} tools={len(names)} "
        f"schema_sha256={digest}",
        flush=True,
    )
    print("wave20_code_index_contract_smoke=ok", flush=True)


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


def _proxy() -> int:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(5)
        sock.connect(os.environ["MCP_FILES_SOCKET_PATH"])
        sock.sendall(
            json.dumps(
                {
                    "action": "mcp_stdio",
                    "adapter_id": GOGRAPH_ADAPTER_ID,
                    "workspace_id": os.environ["MCP_FILE_WORKSPACE_ID"],
                },
                separators=(",", ":"),
            ).encode()
            + b"\n"
        )
        response = json.loads(
            sock.makefile("rb", buffering=0).readline(4097).decode("utf-8")
        )
        if response.get("ok") is not True:
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


def _write_fixture(input_root: Path) -> None:
    (input_root / "go.mod").write_text(
        "module example.com/wave20\n\ngo 1.26.0\n", encoding="utf-8"
    )
    internal = input_root / "internal" / "handler.go"
    internal.parent.mkdir(parents=True)
    internal.write_text(
        "package internal\n\n"
        "func NormalizeWave20(value string) string { return value }\n\n"
        "func Wave20Handler(value string) string { return NormalizeWave20(value) }\n",
        encoding="utf-8",
    )
    main = input_root / "cmd" / "server" / "main.go"
    main.parent.mkdir(parents=True)
    main.write_text(
        "package main\n\n"
        "import (\n\t\"fmt\"\n\t\"example.com/wave20/internal\"\n)\n\n"
        "func main() { fmt.Println(internal.Wave20Handler(\"ready\")) }\n",
        encoding="utf-8",
    )


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise RuntimeError("wave20_fixture_shape_invalid")
        if path.is_dir():
            continue
        if not path.is_file():
            raise RuntimeError("wave20_fixture_shape_invalid")
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _structured(result: Any) -> dict[str, Any]:
    value = result.structuredContent
    if not isinstance(value, dict):
        raise RuntimeError("wave20_structured_result_missing")
    return value


def _find_symbol(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("id", "qualified_name", "symbol", "name"):
            candidate = value.get(key)
            if isinstance(candidate, str) and "Wave20Handler" in candidate:
                return candidate
        for child in value.values():
            found = _find_symbol(child)
            if found:
                return found
    if isinstance(value, list):
        for child in value:
            found = _find_symbol(child)
            if found:
                return found
    return None


def _runtime_process_count() -> int:
    count = 0
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            command = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if b"/usr/local/bin/gograph\0" in command or b"/usr/local/go/bin/go\0" in command:
            count += 1
    return count


async def _call_ok(
    session: ClientSession, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    result = await session.call_tool(name, arguments)
    if result.isError:
        allowed_codes = (
            "code_index_runtime_unavailable",
            "code_index_timeout",
            "code_index_upstream_failed",
            "code_index_upstream_output_invalid",
            "code_index_upstream_identity_drift",
            "code_index_upstream_schema_drift",
            "code_index_not_prepared",
            "code_index_result_too_large",
            "code_index_path_disclosure",
        )
        rendered = " ".join(str(getattr(item, "text", "")) for item in result.content)
        code = next((item for item in allowed_codes if item in rendered), "fixed_unknown")
        raise RuntimeError(f"wave20_representative_call_failed:{name}:{code}")
    return _structured(result)


async def _one_round(root: Path, round_number: int) -> dict[str, Any]:
    client_session_type, stdio_parameters_type, stdio_client_runtime = (
        _load_mcp_stdio()
    )
    workspace_id = "mcpws_" + hashlib.sha256(
        f"wave20:{round_number}".encode("utf-8")
    ).hexdigest()[:32]
    input_root = root / "inputs" / workspace_id
    output_root = root / "outputs" / workspace_id
    memory_root = root / "memory" / workspace_id
    input_root.mkdir(parents=True)
    output_root.mkdir(parents=True)
    memory_root.mkdir(parents=True)
    _write_fixture(input_root)
    source_digest = _tree_digest(input_root)
    process, socket_path = await _start_file_server(
        root, allowed_adapters=GOGRAPH_ADAPTER_ID
    )
    env = _base_env(root, workspace_id)
    env["MCP_FILES_SOCKET_PATH"] = str(socket_path)
    params = stdio_parameters_type(
        command=sys.executable,
        args=["-m", "sandbox_sidecar.smoke_file_code_index", "--proxy"],
        env=env,
        cwd=Path(env["TMPDIR"]),
    )
    try:
        async with stdio_client_runtime(params) as (read_stream, write_stream):
            async with client_session_type(read_stream, write_stream) as session:
                await session.initialize()
                listed = await session.list_tools()
                if {tool.name for tool in listed.tools} != set(
                    WAVE20_TOOL_NAMES[GOGRAPH_ADAPTER_ID]
                ):
                    raise RuntimeError("wave20_runtime_tool_drift")
                if _digest(listed.tools) != WAVE20_SCHEMA_SHA256[GOGRAPH_ADAPTER_ID]:
                    raise RuntimeError("wave20_runtime_schema_drift")
                before = await session.call_tool(
                    "search_symbols", {"query": "Wave20Handler"}
                )
                if not before.isError:
                    raise RuntimeError("wave20_unprepared_query_allowed")
                indexed = await _call_ok(session, "index_repository", {})
                if indexed.get("status") != "indexed" or indexed.get(
                    "persistence"
                ) != "session-memory-only":
                    raise RuntimeError("wave20_index_contract_failed")
                search = await _call_ok(
                    session, "search_symbols", {"query": "Wave20Handler"}
                )
                symbol = _find_symbol(search) or "Wave20Handler"
                await _call_ok(
                    session, "get_symbol_context", {"symbol": symbol}
                )
                await _call_ok(session, "get_source", {"symbol": symbol})
                await _call_ok(
                    session, "get_callers", {"symbol": symbol, "depth": 2}
                )
                await _call_ok(session, "get_repository_summary", {})
                forbidden = await session.call_tool(
                    "search_symbols",
                    {"query": "Wave20", "path": "/etc", "git_ref": "HEAD"},
                )
                if not forbidden.isError:
                    raise RuntimeError("wave20_open_surface_exposed")
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and _runtime_process_count():
            await asyncio.sleep(0.05)
        if _runtime_process_count():
            raise RuntimeError("wave20_runtime_process_leaked")
    finally:
        await _stop_process(process)
    if _tree_digest(input_root) != source_digest:
        raise RuntimeError("wave20_source_mutated")
    if any(output_root.iterdir()) or any(memory_root.iterdir()):
        raise RuntimeError("wave20_unexpected_persistent_output")
    return {"symbol": symbol}


async def _timeout_probe(root: Path) -> None:
    input_root = root / "input"
    input_root.mkdir(parents=True)
    _write_fixture(input_root)
    temp_root = root / "tmp"
    temp_root.mkdir()
    previous_tmpdir = os.environ.get("TMPDIR")
    os.environ["TMPDIR"] = str(temp_root)
    runtime = GoGraphRuntime(SimpleNamespace(input_root=input_root))
    try:
        await runtime._start()
        try:
            await runtime._call_tool("gograph_summary", {}, timeout=0.000001)
        except ValueError as exc:
            if str(exc) != "code_index_timeout":
                raise
        else:
            raise RuntimeError("wave20_timeout_not_triggered")
    finally:
        await runtime._terminate()
        if previous_tmpdir is None:
            os.environ.pop("TMPDIR", None)
        else:
            os.environ["TMPDIR"] = previous_tmpdir
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and _runtime_process_count():
        await asyncio.sleep(0.05)
    if _runtime_process_count():
        raise RuntimeError("wave20_timeout_process_leaked")


async def runtime_smoke() -> None:
    base = Path(tempfile.mkdtemp(prefix="wave20-code-index-smoke-"))
    try:
        first = await asyncio.wait_for(_one_round(base / "round-1", 1), timeout=70)
        second = await asyncio.wait_for(_one_round(base / "round-2", 2), timeout=70)
        if first != second:
            raise RuntimeError("wave20_index_result_not_deterministic")
        await asyncio.wait_for(_timeout_probe(base / "timeout"), timeout=70)
    finally:
        shutil.rmtree(base, ignore_errors=False)
    if base.exists():
        raise RuntimeError("wave20_cleanup_failed")
    print(
        "wave20_code_index_runtime_smoke=ok network=none source_immutable=true "
        "rounds=2 timeout=verified cleanup=verified",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract-only", action="store_true")
    parser.add_argument("--proxy", action="store_true")
    args = parser.parse_args()
    if args.proxy:
        raise SystemExit(_proxy())
    asyncio.run(contract_only() if args.contract_only else runtime_smoke())


if __name__ == "__main__":
    main()
