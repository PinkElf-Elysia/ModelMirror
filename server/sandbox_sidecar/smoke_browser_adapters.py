"""Real upstream initialization/schema/representative smoke for Wave 7."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .browser_contracts import (
    BROWSER_ADAPTERS,
    BROWSER_LIMITS,
    BROWSER_SCHEMA_SHA256,
    CONTRACT_VERSION,
    UPSTREAM_SCHEMA_SHA256,
    _schema_digest,
    assert_schema_snapshots,
)
from .browser_mcp import (
    extract_snapshot,
    result_failed,
    result_text,
    upstream_command,
    upstream_schema_digest,
)
from .browser_server import (
    ARTIFACT_ROOT,
    PROFILE_ROOT,
    SOCKET_PATH,
    UpstreamRpc,
    _page_count,
    _terminate_process_group,
)


FORBIDDEN_CHROMIUM_ARGUMENTS = (
    "--no-sandbox",
    "--disable-web-security",
    "--remote-debugging-address=0.0.0.0",
)


def _safe_failure_diagnostic(payload: object) -> str:
    text = result_text(payload)
    lowered = text.lower()
    category = "upstream_error"
    for token, label in (
        ("sandbox", "chromium_sandbox"),
        ("failed to launch", "browser_launch"),
        ("browser process", "browser_process"),
        ("target closed", "target_closed"),
        ("not connected", "browser_not_connected"),
        ("permission denied", "permission_denied"),
        ("enoent", "path_missing"),
    ):
        if token in lowered:
            category = label
            break
    digest = __import__("hashlib").sha256(text.encode("utf-8")).hexdigest()
    return f"category={category},text_bytes={len(text.encode('utf-8'))},sha256={digest}"


async def _deny_proxy(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    try:
        await asyncio.wait_for(reader.read(64 * 1024), timeout=2)
    except (asyncio.TimeoutError, ConnectionError, OSError):
        pass
    writer.write(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
    try:
        await writer.drain()
    except (ConnectionError, OSError):
        pass
    writer.close()
    try:
        await writer.wait_closed()
    except (ConnectionError, OSError):
        pass


def _package_version(root: Path, package_name: str) -> str:
    path = root / "node_modules"
    for segment in package_name.split("/"):
        path /= segment
    payload = json.loads((path / "package.json").read_text(encoding="utf-8"))
    return str(payload.get("version") or "")


def _chromium_version(adapter_id: str) -> str:
    if adapter_id == "chrome-devtools-mcp":
        executable = os.getenv(
            "MCP_BROWSER_CDP_CHROMIUM_PATH",
            "/opt/modelmirror-browsers/cdp/chrome/linux-150.0.7871.24/"
            "chrome-linux64/chrome",
        )
    elif adapter_id == "playwright-mcp":
        executable = os.getenv(
            "MCP_BROWSER_PLAYWRIGHT_CHROMIUM_PATH",
            "/opt/modelmirror-browsers/playwright/chromium-1237/"
            "chrome-linux64/chrome",
        )
    else:
        raise RuntimeError("browser adapter denied")
    completed = subprocess.run(
        [executable, "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    output = completed.stdout.strip()
    if not output:
        raise RuntimeError("locked Chromium executable did not report a version")
    return output


async def discover(
    adapter_id: str,
    *,
    representative: bool = True,
) -> tuple[set[str], str, str]:
    contract = BROWSER_ADAPTERS[adapter_id]
    with tempfile.TemporaryDirectory(prefix=f"browser-smoke-{adapter_id[:10]}-") as root:
        session = Path(root)
        profile = session / "profile"
        artifacts = session / "artifacts"
        runtime_tmp = profile / "tmp"
        profile.mkdir(mode=0o700)
        artifacts.mkdir(mode=0o700)
        runtime_tmp.mkdir(mode=0o700)
        proxy = await asyncio.start_server(_deny_proxy, "127.0.0.1", 0)
        proxy_port = int(proxy.sockets[0].getsockname()[1])
        proxy_url = f"http://127.0.0.1:{proxy_port}"
        command = upstream_command(
            adapter_id,
            profile_dir=profile,
            artifact_dir=artifacts,
            proxy_url=proxy_url,
        )
        if any(forbidden in argument for argument in command for forbidden in FORBIDDEN_CHROMIUM_ARGUMENTS):
            raise RuntimeError(f"{adapter_id} contains forbidden Chromium argv")
        command = [
            sys.executable,
            "-m",
            "sandbox_sidecar.browser_mcp",
            "landlock",
            str(profile),
            str(artifacts),
            "--",
            *command,
        ]
        env = {
            "PATH": "/opt/browser-upstream/node_modules/.bin:/usr/local/bin:/usr/bin:/bin",
            "PYTHONPATH": "/opt/modelmirror",
            "HOME": str(profile),
            "TMPDIR": str(runtime_tmp),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TZ": "UTC",
            "CI": "1",
            "CHROME_DEVTOOLS_MCP_NO_USAGE_STATISTICS": "1",
            "NODE_DISABLE_COMPILE_CACHE": "1",
            "DO_NOT_TRACK": "1",
            "NO_PROXY": "",
            "no_proxy": "",
            "HTTP_PROXY": proxy_url,
            "HTTPS_PROXY": proxy_url,
        }
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(profile),
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        try:
            rpc = UpstreamRpc(process, artifacts)
            initialized = await rpc.request(
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"roots": {"listChanged": False}},
                    "clientInfo": {"name": "ModelMirror build smoke", "version": "wave7-v1"},
                },
            )
            info = initialized.get("result", {}).get("serverInfo", {})
            if (info.get("name"), info.get("version")) != (
                contract.upstream_server_name,
                contract.upstream_server_version,
            ):
                raise RuntimeError(f"{adapter_id} upstream identity drifted: {info}")
            await rpc.notify("notifications/initialized", {})
            listing = await rpc.request("tools/list", {})
            tools = listing.get("result", {}).get("tools")
            digest = upstream_schema_digest(adapter_id, tools)
            if digest != UPSTREAM_SCHEMA_SHA256[adapter_id]:
                raise RuntimeError(f"{adapter_id} upstream schema drifted: {digest}")
            upstream_names = {
                item["name"] for item in tools if isinstance(item, dict) and isinstance(item.get("name"), str)
            }
            if representative:
                snapshot_name = (
                    "take_snapshot"
                    if adapter_id == "chrome-devtools-mcp"
                    else "browser_snapshot"
                )
                snapshot = await rpc.request(
                    "tools/call",
                    {"name": snapshot_name, "arguments": {}},
                    timeout=30,
                )
                if result_failed(snapshot):
                    raise RuntimeError(
                        f"{adapter_id} representative snapshot failed: "
                        f"{_safe_failure_diagnostic(snapshot)}"
                    )
                _, _, observed_url, _ = extract_snapshot(adapter_id, snapshot)
                if observed_url != "about:blank":
                    raise RuntimeError(
                        f"{adapter_id} representative snapshot origin drifted"
                    )
                if adapter_id == "chrome-devtools-mcp":
                    pages = await rpc.request(
                        "tools/call", {"name": "list_pages", "arguments": {}}
                    )
                else:
                    pages = await rpc.request(
                        "tools/call",
                        {"name": "browser_tabs", "arguments": {"action": "list"}},
                    )
                if result_failed(pages) or _page_count(adapter_id, pages) != 1:
                    raise RuntimeError(f"{adapter_id} did not keep exactly one page")
            return upstream_names, digest, str(info.get("version") or "")
        except Exception as exc:
            await _terminate_process_group(process)
            diagnostics = b""
            if process.stderr is not None:
                diagnostics = await process.stderr.read(16 * 1024)
            safe_diagnostics = diagnostics.decode("utf-8", errors="replace")[-4096:]
            raise RuntimeError(
                f"{adapter_id} upstream smoke failed: {safe_diagnostics}"
            ) from exc
        finally:
            await _terminate_process_group(process)
            proxy.close()
            await proxy.wait_closed()


async def upstream_smoke(*, representative: bool = True) -> None:
    assert_schema_snapshots()
    upstream_root = Path(os.getenv("MCP_BROWSER_UPSTREAM_ROOT", "/opt/browser-upstream"))
    for adapter_id, contract in BROWSER_ADAPTERS.items():
        chromium = _chromium_version(adapter_id)
        print(f"{adapter_id}: chromium_runtime={chromium}", flush=True)
        installed = _package_version(upstream_root, contract.package_name)
        if installed != contract.package_version:
            raise RuntimeError(
                f"{adapter_id} package drift: expected={contract.package_version} installed={installed}"
            )
        names, upstream_digest, server_version = await asyncio.wait_for(
            discover(adapter_id, representative=representative), timeout=60
        )
        public_digest = _schema_digest(contract)
        if public_digest != BROWSER_SCHEMA_SHA256[adapter_id]:
            raise RuntimeError(f"{adapter_id} public schema drifted")
        print(
            f"{adapter_id}: package={contract.package_name}@{installed}, "
            f"server={server_version}, upstream_tools={len(names)}, "
            f"forwarded_tools={len(contract.tools) - 1}, "
            f"upstream_schema_sha256={upstream_digest}, "
            f"public_schema_sha256={public_digest}, "
            f"representative={'snapshot' if representative else 'runtime-gated'}",
            flush=True,
        )


async def _gateway_request(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    request_id: int,
    method: str,
    params: dict[str, object],
    *,
    allow_error: bool = False,
) -> dict[str, object]:
    writer.write(
        json.dumps(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )
    await writer.drain()
    raw = await asyncio.wait_for(reader.readline(), timeout=60)
    response = json.loads(raw.decode("utf-8"))
    if not isinstance(response, dict) or response.get("id") != request_id:
        raise RuntimeError(f"gateway response invalid for {method}")
    if "error" in response and not allow_error:
        raise RuntimeError(f"gateway {method} failed: {response['error']}")
    return response


def _assert_timeout_outcome(
    response: dict[str, object], elapsed_seconds: float,
) -> None:
    error = response.get("error")
    if not isinstance(error, dict):
        raise RuntimeError("gateway timeout response did not contain an error")
    if error.get("code") != -32008:
        raise RuntimeError("gateway timeout error code drifted")
    data = error.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("gateway timeout error data missing")
    if data.get("reason") != "unknown_outcome":
        raise RuntimeError("gateway timeout reason was not unknown_outcome")
    if data.get("retryable") is not False:
        raise RuntimeError("gateway timeout unexpectedly became retryable")

    reviewed_timeout = float(BROWSER_LIMITS["navigation_timeout_seconds"])
    if not reviewed_timeout - 2 <= elapsed_seconds <= reviewed_timeout + 8:
        raise RuntimeError("gateway navigation timeout elapsed window drifted")


def _tool_result(response: dict[str, object]) -> dict[str, object]:
    result = response.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("gateway tool result missing")
    return result


def _element_ref(result: dict[str, object], role: str) -> str:
    structured = result.get("structuredContent")
    if not isinstance(structured, dict):
        raise RuntimeError("gateway snapshot structuredContent missing")
    elements = structured.get("elements")
    if not isinstance(elements, list):
        raise RuntimeError("gateway snapshot elements missing")
    for element in elements:
        if (
            isinstance(element, dict)
            and element.get("role") == role
            and isinstance(element.get("ref"), str)
        ):
            return str(element["ref"])
    raise RuntimeError(f"gateway snapshot did not expose a safe {role} ref")


async def _wait_for_session_cleanup(session_id: str) -> None:
    for _ in range(100):
        profile = PROFILE_ROOT / session_id
        artifacts = ARTIFACT_ROOT / session_id
        residual_process = False
        proc = Path("/proc")
        if proc.exists():
            for entry in proc.iterdir():
                if not entry.name.isdigit():
                    continue
                try:
                    command = (entry / "cmdline").read_bytes()
                except OSError:
                    continue
                if session_id.encode("ascii") in command:
                    residual_process = True
                    break
        if not profile.exists() and not artifacts.exists() and not residual_process:
            return
        await asyncio.sleep(0.1)
    raise RuntimeError(f"gateway session cleanup incomplete: {session_id}")


async def gateway_smoke(
    adapter_id: str, url: str, *, expect_timeout: bool = False,
) -> None:
    contract = BROWSER_ADAPTERS[adapter_id]
    reader, writer = await asyncio.open_unix_connection(str(SOCKET_PATH))
    session_id = ""
    try:
        writer.write(
            json.dumps(
                {
                    "action": "mcp_stdio",
                    "adapter_id": adapter_id,
                    "configuration": {
                        "project_id": adapter_id,
                        "contract_version": CONTRACT_VERSION,
                        "tool_schema_sha256": BROWSER_SCHEMA_SHA256[adapter_id],
                        "limits": BROWSER_LIMITS,
                    },
                },
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        await writer.drain()
        handshake = json.loads((await asyncio.wait_for(reader.readline(), timeout=60)).decode("utf-8"))
        if not isinstance(handshake, dict) or handshake.get("ok") is not True:
            raise RuntimeError(f"gateway handshake failed: {handshake}")
        upstream = handshake.get("upstream")
        if not isinstance(upstream, dict) or (
            upstream.get("package"), upstream.get("version"), upstream.get("verified")
        ) != (contract.package_name, contract.package_version, True):
            raise RuntimeError(f"gateway upstream identity invalid: {upstream}")

        request_id = 1
        await _gateway_request(
            reader,
            writer,
            request_id,
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "ModelMirror gateway smoke", "version": "wave7-v1"},
            },
        )
        request_id += 1
        listed = _tool_result(
            await _gateway_request(reader, writer, request_id, "tools/list", {})
        )
        names = {
            item.get("name")
            for item in listed.get("tools", [])
            if isinstance(item, dict)
        }
        if names != set(contract.tools):
            raise RuntimeError(f"gateway public tools drifted: {names}")

        request_id += 1
        initial_status = _tool_result(
            await _gateway_request(
                reader,
                writer,
                request_id,
                "tools/call",
                {"name": "browser_session_status", "arguments": {}},
            )
        ).get("structuredContent")
        if not isinstance(initial_status, dict) or (
            initial_status.get("status") != "active"
            or initial_status.get("tainted") is not False
            or initial_status.get("current_origin") != ""
            or initial_status.get("action_count") != 0
        ):
            raise RuntimeError(
                f"gateway initial browser status invalid: {initial_status}"
            )

        navigate = "navigate_page" if adapter_id == "chrome-devtools-mcp" else "browser_navigate"
        snapshot = "take_snapshot" if adapter_id == "chrome-devtools-mcp" else "browser_snapshot"
        fill = "fill" if adapter_id == "chrome-devtools-mcp" else "browser_fill_form"
        click = "click" if adapter_id == "chrome-devtools-mcp" else "browser_click"
        screenshot = "take_screenshot" if adapter_id == "chrome-devtools-mcp" else "browser_take_screenshot"

        request_id += 1
        if expect_timeout:
            started = asyncio.get_running_loop().time()
            timeout_response = await _gateway_request(
                reader,
                writer,
                request_id,
                "tools/call",
                {"name": navigate, "arguments": {"url": url}},
                allow_error=True,
            )
            elapsed_seconds = asyncio.get_running_loop().time() - started
            _assert_timeout_outcome(timeout_response, elapsed_seconds)
            print(
                f"{adapter_id}: timeout=unknown_outcome navigation_timeout=20s "
                "elapsed_window=ok retryable=false",
                flush=True,
            )
            return

        await _gateway_request(
            reader,
            writer,
            request_id,
            "tools/call",
            {"name": navigate, "arguments": {"url": url}},
        )
        request_id += 1
        first_snapshot = _tool_result(
            await _gateway_request(
                reader, writer, request_id, "tools/call", {"name": snapshot, "arguments": {}}
            )
        )
        textbox_ref = _element_ref(first_snapshot, "textbox")
        request_id += 1
        await _gateway_request(
            reader,
            writer,
            request_id,
            "tools/call",
            {"name": fill, "arguments": {"ref": textbox_ref, "value": "wave7-safe"}},
        )
        request_id += 1
        second_snapshot = _tool_result(
            await _gateway_request(
                reader, writer, request_id, "tools/call", {"name": snapshot, "arguments": {}}
            )
        )
        button_ref = _element_ref(second_snapshot, "button")
        request_id += 1
        await _gateway_request(
            reader,
            writer,
            request_id,
            "tools/call",
            {"name": click, "arguments": {"ref": button_ref}},
        )
        request_id += 1
        outcome_snapshot = _tool_result(
            await _gateway_request(
                reader,
                writer,
                request_id,
                "tools/call",
                {"name": snapshot, "arguments": {}},
            )
        )
        outcome_text = "\n".join(
            str(item.get("text") or "")
            for item in outcome_snapshot.get("content", [])
            if isinstance(item, dict) and item.get("type") == "text"
        )
        if "clicked:wave7-safe" not in outcome_text:
            raise RuntimeError("gateway click/fill outcome was not observed")
        request_id += 1
        screenshot_result = _tool_result(
            await _gateway_request(
                reader,
                writer,
                request_id,
                "tools/call",
                {"name": screenshot, "arguments": {"full_page": False}},
            )
        )
        artifact = screenshot_result.get("structuredContent")
        if not isinstance(artifact, dict):
            raise RuntimeError("gateway screenshot metadata missing")
        relative_path = str(artifact.get("relative_path") or "")
        segments = Path(relative_path).parts
        if len(segments) != 3 or segments[1] != "registered":
            raise RuntimeError(f"gateway artifact locator invalid: {relative_path}")
        session_id = segments[0]
        artifact_path = ARTIFACT_ROOT / relative_path
        payload = artifact_path.read_bytes()
        if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
            raise RuntimeError("gateway screenshot is not PNG")
        if int(artifact.get("size") or 0) != len(payload):
            raise RuntimeError("gateway screenshot size mismatch")
        request_id += 1
        status = _tool_result(
            await _gateway_request(
                reader,
                writer,
                request_id,
                "tools/call",
                {"name": "browser_session_status", "arguments": {}},
            )
        ).get("structuredContent")
        if not isinstance(status, dict) or status.get("tainted") is not False:
            raise RuntimeError(f"gateway session unexpectedly tainted: {status}")
        print(
            f"{adapter_id}: gateway=ok navigate=ok snapshot=ok fill=ok click=ok "
            f"outcome=ok screenshot=png artifact={relative_path}",
            flush=True,
        )
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, OSError):
            pass
    if session_id:
        await _wait_for_session_cleanup(session_id)
        # The trusted helper shares the artifact volume, but not the browser
        # container's private /profiles tmpfs or PID namespace.  Full process
        # and profile cleanup is therefore asserted by smoke_browser_runtime
        # after Docker automatically restarts the one-shot pair.
        print(
            f"{adapter_id}: artifact_cleanup=ok session={session_id}",
            flush=True,
        )


async def main() -> None:
    if sys.argv[1:] == ["--contract-only"]:
        await upstream_smoke(representative=False)
        return
    if (
        len(sys.argv) in {5, 6}
        and sys.argv[1] == "--gateway-url"
        and sys.argv[3] == "--adapter"
        and sys.argv[4] in BROWSER_ADAPTERS
        and (len(sys.argv) == 5 or sys.argv[5] == "--expect-timeout")
    ):
        # Run from the trusted UID 0 helper/container.  The orchestrator starts
        # a fresh browser+egress container pair for each adapter so no session
        # or Chromium descendant can cross adapter lifecycles.
        await gateway_smoke(
            sys.argv[4], sys.argv[2], expect_timeout=len(sys.argv) == 6,
        )
        return
    if len(sys.argv) != 1:
        raise SystemExit(
            "usage: smoke_browser_adapters.py "
            "[--contract-only | --gateway-url URL --adapter ADAPTER_ID "
            "[--expect-timeout]]"
        )
    await upstream_smoke(representative=True)


if __name__ == "__main__":
    asyncio.run(main())
