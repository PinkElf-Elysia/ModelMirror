"""Security gateway helpers for the real, locked Wave 7 upstream MCPs.

This module is intentionally not an MCP facade.  ``browser_server`` launches
the actual npm package for the selected adapter and uses these helpers only to
verify its identity/schema and translate the reviewed public arguments to the
upstream package's fixed arguments.
"""

from __future__ import annotations

import hashlib
import ctypes
import json
import os
import platform
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .browser_contracts import (
    BROWSER_ADAPTERS,
    MAX_ARTIFACT_BYTES,
    NAVIGATION_TIMEOUT_SECONDS,
    TOOL_CALL_TIMEOUT_SECONDS,
)


LANDLOCK_CREATE_RULESET_VERSION = 1
LANDLOCK_RULE_PATH_BENEATH = 1
PR_SET_NO_NEW_PRIVS = 38
ACCESS_FS_EXECUTE = 1 << 0
ACCESS_FS_WRITE_FILE = 1 << 1
ACCESS_FS_READ_FILE = 1 << 2
ACCESS_FS_READ_DIR = 1 << 3
ACCESS_FS_REMOVE_DIR = 1 << 4
ACCESS_FS_REMOVE_FILE = 1 << 5
ACCESS_FS_MAKE_CHAR = 1 << 6
ACCESS_FS_MAKE_DIR = 1 << 7
ACCESS_FS_MAKE_REG = 1 << 8
ACCESS_FS_MAKE_SOCK = 1 << 9
ACCESS_FS_MAKE_FIFO = 1 << 10
ACCESS_FS_MAKE_BLOCK = 1 << 11
ACCESS_FS_MAKE_SYM = 1 << 12
ACCESS_FS_REFER = 1 << 13
ACCESS_FS_TRUNCATE = 1 << 14
READ_EXECUTE = ACCESS_FS_EXECUTE | ACCESS_FS_READ_FILE | ACCESS_FS_READ_DIR
PROC_USERNS_ACCESS = READ_EXECUTE | ACCESS_FS_WRITE_FILE
WRITABLE = (
    READ_EXECUTE
    | ACCESS_FS_WRITE_FILE
    | ACCESS_FS_REMOVE_DIR
    | ACCESS_FS_REMOVE_FILE
    | ACCESS_FS_MAKE_DIR
    | ACCESS_FS_MAKE_REG
    | ACCESS_FS_MAKE_SOCK
    | ACCESS_FS_MAKE_FIFO
    | ACCESS_FS_MAKE_SYM
    | ACCESS_FS_REFER
    | ACCESS_FS_TRUNCATE
)
HANDLED = WRITABLE | ACCESS_FS_MAKE_CHAR | ACCESS_FS_MAKE_BLOCK
DEVICE_FILE_ACCESS = ACCESS_FS_READ_FILE | ACCESS_FS_WRITE_FILE


class _RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class _PathBeneathAttr(ctypes.Structure):
    _fields_ = [
        ("allowed_access", ctypes.c_uint64),
        ("parent_fd", ctypes.c_int32),
        ("reserved", ctypes.c_uint32),
    ]


def _landlock_syscalls() -> tuple[int, int, int]:
    if platform.machine().lower() in {"x86_64", "amd64", "aarch64", "arm64"}:
        return 444, 445, 446
    raise RuntimeError("browser_landlock_architecture_unsupported")


def apply_browser_landlock(profile_dir: Path, staging_dir: Path) -> None:
    """Restrict one upstream and all Chromium descendants to its own dirs."""

    create_nr, add_nr, restrict_nr = _landlock_syscalls()
    libc = ctypes.CDLL(None, use_errno=True)
    abi = libc.syscall(create_nr, 0, 0, LANDLOCK_CREATE_RULESET_VERSION)
    if abi < 1:
        raise RuntimeError("browser_landlock_unavailable")
    supported = HANDLED
    if abi < 2:
        supported &= ~ACCESS_FS_REFER
    if abi < 3:
        supported &= ~ACCESS_FS_TRUNCATE
    attr = _RulesetAttr(supported)
    ruleset_fd = libc.syscall(create_nr, ctypes.byref(attr), ctypes.sizeof(attr), 0)
    if ruleset_fd < 0:
        raise RuntimeError("browser_landlock_create_failed")

    def add(path: Path, access: int) -> None:
        if not path.exists():
            return
        descriptor = os.open(path, os.O_PATH | os.O_CLOEXEC)
        try:
            rule = _PathBeneathAttr(access & supported, descriptor, 0)
            if libc.syscall(
                add_nr,
                ruleset_fd,
                LANDLOCK_RULE_PATH_BENEATH,
                ctypes.byref(rule),
                0,
            ) < 0:
                raise RuntimeError("browser_landlock_rule_failed")
        finally:
            os.close(descriptor)

    try:
        for path in (
            Path("/usr"),
            Path("/bin"),
            Path("/lib"),
            Path("/lib64"),
            Path("/etc"),
            Path("/opt/browser-upstream"),
            Path("/opt/modelmirror-browsers"),
            Path("/opt/modelmirror"),
            Path("/sys/devices/system"),
        ):
            add(path, READ_EXECUTE)
        # Chromium's namespace sandbox writes uid_map/gid_map/setgroups through
        # procfs after clone(CLONE_NEWUSER). Restore only WRITE_FILE here: the
        # container still masks or mounts sensitive procfs trees read-only,
        # carries no capabilities, and Landlock continues to deny create,
        # remove, truncate, refer, or socket operations below /proc.
        add(Path("/proc"), PROC_USERNS_ACCESS)
        for path in (Path("/dev/null"), Path("/dev/urandom"), Path("/dev/random")):
            add(path, DEVICE_FILE_ACCESS)
        add(profile_dir.resolve(strict=True), WRITABLE)
        add(staging_dir.resolve(strict=True), WRITABLE)
        if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            raise RuntimeError("browser_no_new_privs_failed")
        if libc.syscall(restrict_nr, ruleset_fd, 0) != 0:
            raise RuntimeError("browser_landlock_restrict_failed")
    finally:
        os.close(ruleset_fd)


def landlock_exec(profile_dir: Path, staging_dir: Path, command: list[str]) -> int:
    if not command:
        return 64
    apply_browser_landlock(profile_dir, staging_dir)
    os.chdir(profile_dir)
    os.execvpe(command[0], command, os.environ)
    return 127


REF_VALUE = r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}"
JSON_STRING = r'"(?:\\["\\/bfnrt]|\\u[0-9A-Fa-f]{4}|[^"\\\x00-\x1f])*"'
SNAPSHOT_REF_TOKEN = re.compile(rf"(?<![A-Za-z0-9_])(?:uid|ref)=({REF_VALUE})\b")
CHROME_SNAPSHOT_LINE = re.compile(
    rf"^(?P<indent>(?:  )*)uid=(?P<ref>{REF_VALUE}) "
    r"(?P<role>ignored|[A-Za-z][A-Za-z0-9_-]{0,63})"
    rf"(?P<name> {JSON_STRING})?"
    rf"(?P<attrs>(?: [A-Za-z][A-Za-z0-9_-]{{0,63}}(?:={JSON_STRING})?)*)"
    r"(?: \[selected in the DevTools Elements panel\])?$"
)
PLAYWRIGHT_SNAPSHOT_KEY = re.compile(
    rf"^(?P<role>[a-z][a-z0-9]{{0,63}})"
    rf"(?P<name> {JSON_STRING})?"
    rf"(?P<attrs>(?: \[[a-z][a-z0-9_-]{{0,31}}(?:=[^\]\s]+)?\])*)"
    rf"(?P<suffix>:(?: .*)?)?$"
)
INTERACTIVE_SNAPSHOT_ROLES = frozenset(
    {
        "button",
        "checkbox",
        "combobox",
        "link",
        "menuitem",
        "option",
        "radio",
        "searchbox",
        "slider",
        "spinbutton",
        "switch",
        "tab",
        "textbox",
        "treeitem",
    }
)
PLAYWRIGHT_PAGE_URL = re.compile(
    r"^- Page URL:[ \t]*(?P<url>\S(?:[^\r\n]*\S)?)[ \t]*$", re.MULTILINE
)
PLAYWRIGHT_PAGE_TITLE = re.compile(
    r"^- Page Title:[ \t]*(?P<title>[^\r\n]*?)[ \t]*$", re.MULTILINE
)


class SnapshotStructureError(ValueError):
    """The locked upstream emitted an ambiguous or spoofable ref structure."""


@dataclass(frozen=True, slots=True)
class SnapshotElement:
    """A ref whose role, name and attributes came from structural positions."""

    role: str
    name: str
    attributes: str

    @property
    def context(self) -> str:
        return " ".join(
            part for part in (self.role, self.name, self.attributes) if part
        )


def _snapshot_name(raw_name: str | None) -> str:
    if not raw_name:
        return ""
    try:
        value = json.loads(raw_name.strip())
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SnapshotStructureError("browser_snapshot_ref_structure_invalid") from exc
    if not isinstance(value, str):
        raise SnapshotStructureError("browser_snapshot_ref_structure_invalid")
    return value


def _playwright_snapshot_key(line: str) -> str | None:
    match = re.fullmatch(r"(?P<indent>(?:  )*)- (?P<body>.*)", line)
    if match is None:
        return None
    body = match.group("body")
    if not body.startswith("'"):
        return body
    # Playwright's YAML key escaper uses single-quoted scalars and doubles a
    # literal apostrophe.  Parse the wrapper before applying the fixed key
    # grammar so a page label cannot manufacture a structural ref line.
    output: list[str] = []
    index = 1
    while index < len(body):
        if body[index] != "'":
            output.append(body[index])
            index += 1
            continue
        if index + 1 < len(body) and body[index + 1] == "'":
            output.append("'")
            index += 2
            continue
        remainder = body[index + 1 :]
        if remainder and not remainder.startswith(":"):
            raise SnapshotStructureError("browser_snapshot_ref_structure_invalid")
        return "".join(output) + remainder
    raise SnapshotStructureError("browser_snapshot_ref_structure_invalid")


def upstream_command(
    adapter_id: str,
    *,
    profile_dir: Path,
    artifact_dir: Path,
    proxy_url: str,
) -> list[str]:
    """Return the complete, fixed argv for one real upstream MCP process."""

    if adapter_id not in BROWSER_ADAPTERS:
        raise ValueError("mcp_adapter_denied")
    chromium_env = (
        "MCP_BROWSER_CDP_CHROMIUM_PATH"
        if adapter_id == "chrome-devtools-mcp"
        else "MCP_BROWSER_PLAYWRIGHT_CHROMIUM_PATH"
    )
    chromium_default = (
        "/opt/modelmirror-browsers/cdp/chrome/linux-150.0.7871.24/"
        "chrome-linux64/chrome"
        if adapter_id == "chrome-devtools-mcp"
        else "/opt/modelmirror-browsers/playwright/chromium-1237/"
        "chrome-linux64/chrome"
    )
    chromium = os.getenv(chromium_env, chromium_default)
    upstream_root = Path(os.getenv("MCP_BROWSER_UPSTREAM_ROOT", "/opt/browser-upstream"))
    local_state_path = profile_dir / "Local State"
    local_state_path.write_text(
        json.dumps({"ssl": {"ech_enabled": False}}, separators=(",", ":")),
        encoding="utf-8",
    )
    local_state_path.chmod(0o600)
    if adapter_id == "chrome-devtools-mcp":
        executable = upstream_root / "node_modules" / ".bin" / "chrome-devtools-mcp"
        return [
            str(executable),
            "--headless",
            f"--executablePath={chromium}",
            f"--userDataDir={profile_dir}",
            f"--proxyServer={proxy_url}",
            "--viewport=1280x720",
            "--no-usage-statistics",
            "--no-performance-crux",
            "--no-category-emulation",
            "--no-category-performance",
            "--no-category-network",
            "--screenshotFormat=png",
            "--screenshotMaxWidth=4096",
            "--screenshotMaxHeight=16384",
            "--ignoreDefaultChromeArg=--disable-popup-blocking",
            "--blockedUrlPattern=ws://*/*",
            "--blockedUrlPattern=wss://*/*",
            "--blockedUrlPattern=file://*/*",
            "--blockedUrlPattern=ftp://*/*",
            "--blockedUrlPattern=data:*",
            "--blockedUrlPattern=blob:*",
            "--chromeArg=--disable-dev-shm-usage",
            "--chromeArg=--disable-quic",
            "--chromeArg=--disable-sync",
            "--chromeArg=--disable-background-networking",
            "--chromeArg=--disable-component-update",
            "--chromeArg=--disable-domain-reliability",
            "--chromeArg=--disable-blink-features=PushMessaging,PushMessagingSubscriptionChange",
            "--chromeArg=--disable-preconnect",
            "--chromeArg=--incognito",
            "--chromeArg=--disable-features=kAutofillServerCommunication,EncryptedClientHello,MediaRouter,NetworkTimeServiceQuerying,OptimizationHints,PreconnectToSearch,WebOTP,WebRtcHideLocalIpsWithMdns",
            "--chromeArg=--enable-features=NoSearchDomainCheck",
            "--chromeArg=--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
            "--chromeArg=--proxy-bypass-list=<-loopback>",
        ]
    executable = upstream_root / "node_modules" / ".bin" / "playwright-mcp"
    config_path = profile_dir / "modelmirror-playwright-config.json"
    config_path.write_text(
        json.dumps(
            {
                "browser": {
                    "launchOptions": {
                        "args": [
                            "--disable-dev-shm-usage",
                            "--disable-quic",
                            "--disable-sync",
                            "--disable-background-networking",
                            "--disable-component-update",
                            "--disable-domain-reliability",
                            "--disable-blink-features=PushMessaging,PushMessagingSubscriptionChange",
                            "--disable-preconnect",
                            "--incognito",
                            "--disable-features=kAutofillServerCommunication,EncryptedClientHello,MediaRouter,NetworkTimeServiceQuerying,OptimizationHints,PreconnectToSearch,WebOTP,WebRtcHideLocalIpsWithMdns",
                            "--enable-features=NoSearchDomainCheck",
                            "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                            "--proxy-bypass-list=<-loopback>",
                        ]
                    },
                    "contextOptions": {
                        "acceptDownloads": False,
                        "serviceWorkers": "block",
                    },
                },
                "snapshot": {"mode": "none"},
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    config_path.chmod(0o400)
    return [
        str(executable),
        f"--config={config_path}",
        "--headless",
        "--sandbox",
        f"--executable-path={chromium}",
        f"--user-data-dir={profile_dir}",
        "--block-service-workers",
        "--image-responses=omit",
        "--snapshot-mode=none",
        "--codegen=none",
        f"--timeout-action={TOOL_CALL_TIMEOUT_SECONDS * 1000}",
        f"--timeout-navigation={NAVIGATION_TIMEOUT_SECONDS * 1000}",
        "--timeout-settle=500",
        "--viewport-size=1280x720",
        f"--output-dir={artifact_dir}",
        f"--output-max-size={MAX_ARTIFACT_BYTES}",
        f"--proxy-server={proxy_url}",
        "--proxy-bypass=<-loopback>",
    ]


def result_text(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    result = payload.get("result")
    if not isinstance(result, dict):
        return ""
    content = result.get("content")
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def result_failed(payload: object) -> bool:
    if not isinstance(payload, dict):
        return True
    if "error" in payload:
        return True
    result = payload.get("result")
    return not isinstance(result, dict) or result.get("isError") is True or result.get("is_error") is True


def extract_snapshot(
    adapter_id: str, payload: object
) -> tuple[str, dict[str, SnapshotElement], str, str]:
    """Return text, ref-to-context mapping, observed URL and title."""

    text = result_text(payload)
    refs: dict[str, SnapshotElement] = {}
    seen_refs: set[str] = set()
    observed_url = ""
    title = ""
    chrome_root_seen = False
    for line in text.splitlines():
        tokens = SNAPSHOT_REF_TOKEN.findall(line)
        if not tokens:
            continue
        if len(tokens) != 1:
            raise SnapshotStructureError("browser_snapshot_ref_structure_invalid")
        if adapter_id == "chrome-devtools-mcp":
            match = CHROME_SNAPSHOT_LINE.fullmatch(line)
            if match is None:
                raise SnapshotStructureError("browser_snapshot_ref_structure_invalid")
            ref = match.group("ref")
            role = match.group("role").lower()
            name = _snapshot_name(match.group("name"))
            attributes = (match.group("attrs") or "").strip()
            if role == "rootwebarea" and not match.group("indent"):
                if chrome_root_seen:
                    raise SnapshotStructureError(
                        "browser_snapshot_ref_structure_invalid"
                    )
                chrome_root_seen = True
                url_values = re.findall(
                    rf"(?:^| )url=({JSON_STRING})(?= |$)", attributes
                )
                if len(url_values) != 1:
                    raise SnapshotStructureError(
                        "browser_snapshot_ref_structure_invalid"
                    )
                observed_url = _snapshot_name(url_values[0])
                if not observed_url:
                    raise SnapshotStructureError(
                        "browser_snapshot_ref_structure_invalid"
                    )
                title = name
        elif adapter_id == "playwright-mcp":
            key = _playwright_snapshot_key(line)
            if key is None:
                raise SnapshotStructureError("browser_snapshot_ref_structure_invalid")
            match = PLAYWRIGHT_SNAPSHOT_KEY.fullmatch(key)
            if match is None:
                raise SnapshotStructureError("browser_snapshot_ref_structure_invalid")
            attrs = match.group("attrs") or ""
            ref_attributes = re.findall(rf" \[ref=({REF_VALUE})\]", attrs)
            if ref_attributes != tokens:
                raise SnapshotStructureError("browser_snapshot_ref_structure_invalid")
            ref = tokens[0]
            role = match.group("role").lower()
            name = _snapshot_name(match.group("name"))
            attributes = re.sub(
                rf"(?:^| )\[ref={re.escape(ref)}\](?= |$)",
                "",
                attrs.strip(),
                count=1,
            ).strip()
        else:
            raise SnapshotStructureError("browser_snapshot_adapter_invalid")
        if ref != tokens[0] or ref in seen_refs:
            raise SnapshotStructureError("browser_snapshot_ref_structure_invalid")
        seen_refs.add(ref)
        if role in INTERACTIVE_SNAPSHOT_ROLES:
            refs[ref] = SnapshotElement(role, name, attributes)
    if adapter_id == "chrome-devtools-mcp":
        if not chrome_root_seen:
            raise SnapshotStructureError("browser_snapshot_ref_structure_invalid")
    else:
        url_matches = PLAYWRIGHT_PAGE_URL.findall(text)
        title_matches = PLAYWRIGHT_PAGE_TITLE.findall(text)
        if len(url_matches) != 1 or len(title_matches) > 1:
            raise SnapshotStructureError("browser_snapshot_ref_structure_invalid")
        observed_url = url_matches[0].strip()
        if not observed_url:
            raise SnapshotStructureError("browser_snapshot_ref_structure_invalid")
        if title_matches:
            title = title_matches[0].strip()
        elif observed_url == "about:blank":
            # Locked Playwright MCP 0.0.79 omits the Page Title metadata line
            # for its initial about:blank page when snapshot-mode is none.
            # Keep every navigated page fail-closed: only this non-networked
            # bootstrap state may use the unambiguous empty-title fallback.
            title = ""
        else:
            raise SnapshotStructureError("browser_snapshot_ref_structure_invalid")
    return text, refs, observed_url, title


def page_digest(origin: str, observed_url: str, title: str, snapshot_text: str) -> str:
    encoded = json.dumps(
        {
            "origin": origin,
            "url": observed_url[:16_384],
            "title": title[:512],
            "snapshot": snapshot_text,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def upstream_schema_digest(adapter_id: str, tools: object) -> str:
    contract = BROWSER_ADAPTERS[adapter_id]
    wanted = {
        tool.upstream_name
        for tool in contract.tools.values()
        if tool.upstream_name is not None
    }
    if not isinstance(tools, list):
        raise ValueError("browser_upstream_tools_invalid")
    selected: list[dict[str, object]] = []
    for item in tools:
        if not isinstance(item, dict) or item.get("name") not in wanted:
            continue
        schema = item.get("inputSchema")
        if not isinstance(schema, dict):
            raise ValueError("browser_upstream_schema_invalid")
        selected.append({"name": item["name"], "inputSchema": schema})
    if {item["name"] for item in selected} != wanted:
        raise ValueError("browser_upstream_tool_missing")
    encoded = json.dumps(
        sorted(selected, key=lambda item: str(item["name"])),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def public_tools(adapter_id: str) -> list[dict[str, object]]:
    contract = BROWSER_ADAPTERS[adapter_id]
    output: list[dict[str, object]] = []
    for name, tool in contract.tools.items():
        read_only = tool.effect == "read"
        output.append(
            {
                "name": name,
                "description": tool.description,
                "inputSchema": tool.input_schema,
                "annotations": {
                    "title": tool.description,
                    "readOnlyHint": read_only,
                    "destructiveHint": False,
                    "idempotentHint": read_only,
                    "openWorldHint": True,
                },
            }
        )
    return output


CHROME_FILL_ROLES = frozenset({"textbox", "searchbox", "spinbutton", "combobox"})
PLAYWRIGHT_FILL_ROLE_TYPES = {
    "textbox": "textbox",
    "searchbox": "textbox",
    "spinbutton": "textbox",
    "checkbox": "checkbox",
    "switch": "checkbox",
    "radio": "radio",
    "combobox": "combobox",
    "slider": "slider",
}


def playwright_field_type(role: str) -> str:
    try:
        return PLAYWRIGHT_FILL_ROLE_TYPES[role]
    except KeyError as exc:
        raise ValueError("browser_fill_role_denied") from exc


def to_upstream_arguments(
    adapter_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    ref_roles: dict[str, str],
    artifact_path: Path | None = None,
) -> dict[str, object]:
    """Translate the reviewed schema without forwarding proof/private fields."""

    if tool_name in {"navigate_page", "browser_navigate"}:
        if adapter_id == "chrome-devtools-mcp":
            return {
                "type": "url",
                "url": arguments["url"],
                "timeout": NAVIGATION_TIMEOUT_SECONDS * 1000,
            }
        return {"url": arguments["url"]}
    if tool_name in {"take_snapshot", "browser_snapshot"}:
        return {}
    if tool_name in {"click", "browser_click"}:
        ref = str(arguments["ref"])
        if adapter_id == "chrome-devtools-mcp":
            return {"uid": ref, "includeSnapshot": False}
        return {"target": ref, "element": "受控快照元素"}
    if tool_name == "fill":
        return {
            "uid": str(arguments["ref"]),
            "value": str(arguments["value"]),
            "includeSnapshot": False,
        }
    if tool_name == "browser_fill_form":
        ref = str(arguments["ref"])
        role = ref_roles[ref]
        return {
            "fields": [
                {
                    "target": ref,
                    "name": "受控字段",
                    "type": playwright_field_type(role),
                    "value": str(arguments["value"]),
                }
            ]
        }
    if tool_name in {"take_screenshot", "browser_take_screenshot"}:
        if artifact_path is None:
            raise ValueError("browser_artifact_path_missing")
        full_page = bool(arguments.get("full_page", False))
        if adapter_id == "chrome-devtools-mcp":
            return {
                "format": "png",
                "fullPage": full_page,
                "filePath": str(artifact_path),
            }
        return {
            "type": "png",
            "filename": artifact_path.name,
            "fullPage": full_page,
            "scale": "css",
        }
    raise ValueError("browser_tool_denied")


def main() -> int:
    if len(sys.argv) < 5 or sys.argv[1] != "landlock" or "--" not in sys.argv[4:]:
        print(
            "usage: browser_mcp.py landlock PROFILE_DIR STAGING_DIR -- COMMAND [ARGS...]",
            file=sys.stderr,
        )
        return 64
    separator = sys.argv.index("--", 4)
    if separator != 4:
        return 64
    return landlock_exec(
        Path(sys.argv[2]).resolve(strict=True),
        Path(sys.argv[3]).resolve(strict=True),
        sys.argv[separator + 1 :],
    )


if __name__ == "__main__":
    raise SystemExit(main())
