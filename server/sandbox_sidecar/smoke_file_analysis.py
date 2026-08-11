"""Contract and real offline UDS smoke for staged Wave 18B file facades."""

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
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .file_analysis import (
    WAVE18B_BUILDERS,
    WAVE18B_SCHEMA_SHA256,
    WAVE18B_TOOL_NAMES,
)
from .file_mcp import opaque_file_id
from .smoke_file_artifacts import _base_env, _start_file_server, _stop_process


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
    for adapter_id, builder in sorted(WAVE18B_BUILDERS.items()):
        tools = await builder(object()).list_tools()
        names = {tool.name for tool in tools}
        digest = _digest(tools)
        if names != set(WAVE18B_TOOL_NAMES[adapter_id]):
            raise RuntimeError("wave18b_tool_contract_drift")
        if digest != WAVE18B_SCHEMA_SHA256[adapter_id]:
            raise RuntimeError("wave18b_schema_contract_drift")
        print(
            f"adapter={adapter_id} tools={len(names)} schema_sha256={digest}",
            flush=True,
        )
    print("wave18b_file_contract_smoke=ok", flush=True)


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


def _proxy(adapter_id: str) -> int:
    if adapter_id not in WAVE18B_BUILDERS:
        return 64
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(5)
        sock.connect(os.environ["MCP_FILES_SOCKET_PATH"])
        sock.sendall(
            json.dumps(
                {
                    "action": "mcp_stdio",
                    "adapter_id": adapter_id,
                    "workspace_id": os.environ["MCP_FILE_WORKSPACE_ID"],
                },
                separators=(",", ":"),
            ).encode()
            + b"\n"
        )
        response = json.loads(sock.makefile("rb", buffering=0).readline(4097).decode())
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


def _write_fixture(adapter_id: str, input_root: Path) -> tuple[str, str]:
    if adapter_id == "cyberchitta-llm-context-py":
        path = input_root / "src" / "app.py"
        path.parent.mkdir(parents=True)
        path.write_text(
            "class Wave18B:\n    pass\n\ndef analyze(value: str) -> str:\n    return value\n",
            encoding="utf-8",
        )
    elif adapter_id == "haris-musa-excel-mcp-server":
        from openpyxl import Workbook

        path = input_root / "source.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Data"
        sheet.append(["name", "value"])
        sheet.append(["alpha", 2])
        workbook.save(path)
        workbook.close()
    else:
        path = input_root / "records.jsonl"
        path.write_text(
            '{"content":"good text"}\n'
            '{"content":""}\n'
            '{"content":"unfinished:"}\n',
            encoding="utf-8",
        )
    relative = path.relative_to(input_root).as_posix()
    return opaque_file_id(input_root.name, relative), relative


async def _call_ok(session: ClientSession, name: str, arguments: dict[str, Any]) -> None:
    result = await session.call_tool(name, arguments)
    if result.isError:
        raise RuntimeError(f"wave18b_representative_call_failed:{name}")


def _adapter_process_count(adapter_id: str) -> int:
    count = 0
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            command = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if b"sandbox_sidecar.file_mcp\0" in command and adapter_id.encode() + b"\0" in command:
            count += 1
    return count


async def _one_round(adapter_id: str, root: Path, round_number: int) -> dict[str, bytes]:
    workspace_id = "mcpws_" + hashlib.sha256(
        f"wave18b:{adapter_id}:{round_number}".encode()
    ).hexdigest()[:32]
    input_root = root / "inputs" / workspace_id
    output_root = root / "outputs" / workspace_id
    memory_root = root / "memory" / workspace_id
    input_root.mkdir(parents=True)
    output_root.mkdir(parents=True)
    memory_root.mkdir(parents=True)
    file_id, source_name = _write_fixture(adapter_id, input_root)
    source_hash = hashlib.sha256((input_root / source_name).read_bytes()).hexdigest()
    process, socket_path = await _start_file_server(root, allowed_adapters=adapter_id)
    env = _base_env(root, workspace_id)
    env["MCP_FILES_SOCKET_PATH"] = str(socket_path)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "sandbox_sidecar.smoke_file_analysis", "--proxy", adapter_id],
        env=env,
        cwd=Path(env["TMPDIR"]),
    )
    try:
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                listed = await session.list_tools()
                if {tool.name for tool in listed.tools} != set(WAVE18B_TOOL_NAMES[adapter_id]):
                    raise RuntimeError("wave18b_runtime_tool_drift")
                if _digest(listed.tools) != WAVE18B_SCHEMA_SHA256[adapter_id]:
                    raise RuntimeError("wave18b_runtime_schema_drift")
                if adapter_id == "cyberchitta-llm-context-py":
                    await _call_ok(session, "lc_preview", {})
                    await _call_ok(
                        session,
                        "lc_outlines",
                        {"artifact_name": "outlines.md"},
                    )
                    blocked = await session.call_tool("lc_preview", {"root_path": "/etc"})
                elif adapter_id == "haris-musa-excel-mcp-server":
                    await _call_ok(
                        session,
                        "get_workbook_metadata",
                        {"file_id": file_id, "include_ranges": True},
                    )
                    await _call_ok(
                        session,
                        "read_data_from_excel",
                        {
                            "file_id": file_id,
                            "sheet_name": "Data",
                            "start_cell": "A1",
                            "end_cell": "B2",
                        },
                    )
                    await _call_ok(
                        session,
                        "write_data_to_excel",
                        {
                            "file_id": file_id,
                            "sheet_name": "Data",
                            "start_cell": "B2",
                            "data": [[7]],
                            "artifact_name": "updated.xlsx",
                        },
                    )
                    blocked = await session.call_tool(
                        "write_data_to_excel",
                        {
                            "file_id": file_id,
                            "sheet_name": "Data",
                            "data": [["=WEBSERVICE(\"https://example.com\")"]],
                            "filepath": "/tmp/escape.xlsx",
                        },
                    )
                else:
                    await _call_ok(
                        session,
                        "list_dingo_components",
                        {"component_type": "rule_groups", "include_details": True},
                    )
                    await _call_ok(
                        session,
                        "run_dingo_evaluation",
                        {"file_id": file_id, "artifact_name": "report.json"},
                    )
                    blocked = await session.call_tool(
                        "run_dingo_evaluation",
                        {"file_id": file_id, "evaluation_type": "llm"},
                    )
                if not blocked.isError:
                    raise RuntimeError("wave18b_open_surface_exposed")
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and _adapter_process_count(adapter_id):
            await asyncio.sleep(0.05)
        if _adapter_process_count(adapter_id):
            raise RuntimeError("wave18b_adapter_process_leaked")
    finally:
        await _stop_process(process)
    if hashlib.sha256((input_root / source_name).read_bytes()).hexdigest() != source_hash:
        raise RuntimeError("wave18b_source_mutated")
    artifacts = {
        path.name: path.read_bytes()
        for path in sorted(output_root.iterdir())
        if path.is_file() and not path.is_symlink()
    }
    if not artifacts or any(len(data) > 32 * 1024 * 1024 for data in artifacts.values()):
        raise RuntimeError("wave18b_artifact_missing_or_too_large")
    return artifacts


async def _exact_allowlist_deny(root: Path) -> None:
    workspace_id = "mcpws_" + "d" * 32
    (root / "inputs" / workspace_id).mkdir(parents=True)
    process, socket_path = await _start_file_server(root, allowed_adapters="markitdown-mcp")
    try:
        reader, writer = await asyncio.open_unix_connection(str(socket_path))
        writer.write(
            json.dumps(
                {
                    "action": "mcp_stdio",
                    "adapter_id": "dataeval-dingo",
                    "workspace_id": workspace_id,
                },
                separators=(",", ":"),
            ).encode()
            + b"\n"
        )
        await writer.drain()
        response = json.loads((await asyncio.wait_for(reader.readline(), timeout=3)).decode())
        writer.close()
        await writer.wait_closed()
        if response.get("ok") is not False or response.get("code") != "mcp_adapter_denied":
            raise RuntimeError("wave18b_exact_allowlist_deny_failed")
    finally:
        await _stop_process(process)


async def _timeout_cleanup(root: Path) -> None:
    adapter_id = "cyberchitta-llm-context-py"
    workspace_id = "mcpws_" + "e" * 32
    input_root = root / "inputs" / workspace_id
    input_root.mkdir(parents=True)
    source = input_root / "large.py"
    source.write_text("\n".join(f"def symbol_{index}(): pass" for index in range(50_000)), encoding="utf-8")
    process, socket_path = await _start_file_server(root, allowed_adapters=adapter_id)
    env = _base_env(root, workspace_id)
    env["MCP_FILES_SOCKET_PATH"] = str(socket_path)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "sandbox_sidecar.smoke_file_analysis", "--proxy", adapter_id],
        env=env,
        cwd=Path(env["TMPDIR"]),
    )
    try:
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                try:
                    await asyncio.wait_for(
                        session.call_tool("lc_outlines", {"artifact_name": "timeout.md"}),
                        timeout=0.001,
                    )
                except asyncio.TimeoutError:
                    pass
                else:
                    raise RuntimeError("wave18b_timeout_not_triggered")
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and _adapter_process_count(adapter_id):
            await asyncio.sleep(0.05)
        if _adapter_process_count(adapter_id):
            raise RuntimeError("wave18b_timeout_process_leaked")
    finally:
        await _stop_process(process)


async def runtime_smoke() -> None:
    base = Path(tempfile.mkdtemp(prefix="wave18b-file-smoke-"))
    try:
        await _exact_allowlist_deny(base / "deny")
        for adapter_id in sorted(WAVE18B_BUILDERS):
            first = await asyncio.wait_for(
                _one_round(adapter_id, base / "round-1", 1),
                timeout=45,
            )
            second = await asyncio.wait_for(
                _one_round(adapter_id, base / "round-2", 2),
                timeout=45,
            )
            if first != second:
                raise RuntimeError("wave18b_artifact_not_deterministic")
            print(
                f"adapter={adapter_id} rounds=2 artifacts={len(second)} "
                f"sha256={','.join(hashlib.sha256(data).hexdigest() for data in second.values())}",
                flush=True,
            )
        await _timeout_cleanup(base / "timeout")
    finally:
        shutil.rmtree(base, ignore_errors=False)
    if base.exists():
        raise RuntimeError("wave18b_cleanup_failed")
    print(
        "wave18b_file_runtime_smoke=ok network=none source_immutable=true "
        "deterministic=true exact_allowlist_deny=true timeout=verified cleanup=verified",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract-only", action="store_true")
    parser.add_argument("--proxy", choices=tuple(sorted(WAVE18B_BUILDERS)))
    args = parser.parse_args()
    if args.proxy:
        raise SystemExit(_proxy(args.proxy))
    asyncio.run(contract_only() if args.contract_only else runtime_smoke())


if __name__ == "__main__":
    main()
