"""Fixed public contracts and security policy for Wave 7 browser adapters.

The two public adapter IDs are backed by the real, version-locked upstream
MCP packages.  This module describes the smaller reviewed surface that the
ModelMirror Unix-socket gateway is allowed to expose.  Upstream tool schemas
are verified independently before this reviewed schema is returned to a
client.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final
from urllib.parse import SplitResult, unquote, urlsplit, urlunsplit


CONTRACT_VERSION: Final = "modelmirror-browser-wave7-v1"
MAX_ARGUMENT_BYTES: Final = 256 * 1024
MAX_OUTPUT_BYTES: Final = 256 * 1024
MAX_ARTIFACT_BYTES: Final = 32 * 1024 * 1024
MAX_ACTIONS: Final = 50
MAX_PAGES: Final = 1
# A single execution session is the fail-closed first release.  Same-UID
# Chromium processes cannot keep a loopback proxy capability secret from a
# concurrently compromised sibling through /proc.  The first release is
# therefore explicitly single-session.
MAX_SESSIONS: Final = 1
SESSION_TTL_SECONDS: Final = 15 * 60
IDLE_TTL_SECONDS: Final = 5 * 60
NAVIGATION_TIMEOUT_SECONDS: Final = 20
TOOL_CALL_TIMEOUT_SECONDS: Final = 30
SYNTHETIC_DNS_NETWORK: Final = ipaddress.ip_network("198.18.0.0/15")

BROWSER_LIMITS: Final[dict[str, int]] = {
    "max_actions": MAX_ACTIONS,
    "max_artifact_bytes": MAX_ARTIFACT_BYTES,
    "max_output_bytes": MAX_OUTPUT_BYTES,
    "max_pages": MAX_PAGES,
    "navigation_timeout_seconds": NAVIGATION_TIMEOUT_SECONDS,
    "session_ttl_seconds": SESSION_TTL_SECONDS,
    "idle_ttl_seconds": IDLE_TTL_SECONDS,
    "tool_call_timeout_seconds": TOOL_CALL_TIMEOUT_SECONDS,
    "max_sessions": MAX_SESSIONS,
    "max_tunnels_per_session": 12,
    "max_egress_bytes_per_session": 64 * 1024 * 1024,
    "egress_tunnel_idle_seconds": 30,
    "egress_tunnel_ttl_seconds": 120,
}

METADATA_HOSTS: Final = frozenset(
    {
        "metadata.google.internal",
        "metadata.google",
        "metadata.azure.internal",
        "instance-data",
        "instance-data.ec2.internal",
    }
)
BLOCKED_HOST_SUFFIXES: Final = (
    ".internal",
    ".local",
    ".localhost",
    ".home.arpa",
)
LOGIN_PATH_SEGMENTS: Final = frozenset(
    {
        "auth",
        "account",
        "accounts",
        "authorize",
        "callback",
        "consent",
        "login",
        "log-in",
        "oauth",
        "oauth2",
        "oidc",
        "saml",
        "session",
        "signin",
        "sign-in",
        "sso",
    }
)
LOGIN_COMPONENT_BOUNDARIES: Final = re.compile(r"[/&=?#]+")
HOST_COMPONENT_BOUNDARIES: Final = re.compile(r"[.]+")
QUERY_COMPONENT_BOUNDARIES: Final = re.compile(r"[&;?#]+")
SENSITIVE_QUERY_KEYS: Final = frozenset(
    {
        "accesstoken",
        "apikey",
        "authorization",
        "auth",
        "bearer",
        "code",
        "credential",
        "idtoken",
        "jwt",
        "key",
        "keypairid",
        "oauth",
        "oauthtoken",
        "password",
        "passwd",
        "refreshtoken",
        "secret",
        "session",
        "sessionid",
        "sig",
        "signature",
        "token",
    }
)
SENSITIVE_QUERY_PREFIXES: Final = ("oauth", "xamz", "xgoog")
REF_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
DIGEST_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
GENERATION_PATTERN: Final = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
SENSITIVE_ELEMENT_PATTERN: Final = re.compile(
    r"(?:password|passcode|passwd|secret|api[ _-]?key|access[ _-]?token|"
    r"authorization|cookie|credit[ _-]?card|card[ _-]?number|cvv|cvc|"
    r"social[ _-]?security|one[ _-]?time[ _-]?(?:code|password)|otp|"
    r"log[ -]?in|sign[ -]?in|oauth|授权|认证|登录|登陆|密码|口令|令牌|密钥|"
    r"验证码|信用卡|银行卡)",
    re.IGNORECASE,
)
SENSITIVE_VALUE_PATTERNS: Final = (
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
    re.compile(r"\b(?:gh[pousr]|github_pat)_[A-Za-z0-9_]{16,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
)


def _object_schema(properties: dict[str, object], required: tuple[str, ...] = ()) -> dict[str, object]:
    schema: dict[str, object] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = list(required)
    return schema


PROOF_PROPERTIES: Final[dict[str, object]] = {
    "generation": {
        "type": "string",
        "pattern": GENERATION_PATTERN.pattern,
        "description": "来自最近一次受控快照的会话代次。",
    },
    "page_revision": {
        "type": "integer",
        "minimum": 0,
        "description": "来自最近一次受控快照的页面修订号。",
    },
    "page_digest": {
        "type": "string",
        "pattern": DIGEST_PATTERN.pattern,
        "description": "来自最近一次受控快照的页面摘要。",
    },
}
PROOF_REQUIRED: Final = ("generation", "page_revision", "page_digest")
REF_PROPERTY: Final[dict[str, object]] = {
    "type": "string",
    "pattern": REF_PATTERN.pattern,
    "x-modelmirror-input": "browser-ref",
    "description": "只能选择最近一次受控快照返回的不透明元素引用。",
}

STATUS_SCHEMA: Final = _object_schema({})
NAVIGATE_SCHEMA: Final = _object_schema(
    {
        "url": {
            "type": "string",
            "minLength": 1,
            "maxLength": 16_384,
            "format": "uri",
            "x-modelmirror-input": "browser-url",
            "description": "仅允许公网 HTTP/HTTPS 80/443，且会话锁定单一 origin。",
        }
    },
    ("url",),
)
SNAPSHOT_SCHEMA: Final = _object_schema({})
CLICK_SCHEMA: Final = {
    **_object_schema({"ref": REF_PROPERTY}, ("ref",)),
    # The proof is deliberately not client supplied.  browser_server binds the
    # ref to its private generation/revision/digest tuple and re-snapshots the
    # page immediately before forwarding the action upstream.
    "x-modelmirror-ref-proof": "sidecar-bound",
}
FILL_SCHEMA: Final = {
    **_object_schema(
        {
            "ref": REF_PROPERTY,
            "value": {"type": "string", "maxLength": 4096},
        },
        ("ref", "value"),
    ),
    "x-modelmirror-ref-proof": "sidecar-bound",
}
FILL_FORM_SCHEMA: Final = {
    **_object_schema(
        {
            "ref": REF_PROPERTY,
            "value": {"type": "string", "maxLength": 4096},
        },
        ("ref", "value"),
    ),
    "x-modelmirror-ref-proof": "sidecar-bound",
}
SCREENSHOT_SCHEMA: Final = _object_schema(
    {
        "full_page": {
            "type": "boolean",
            "default": False,
            "description": "是否截取完整滚动页面；文件名和存储位置由服务端生成。",
        }
    }
)


@dataclass(frozen=True, slots=True)
class BrowserToolContract:
    upstream_name: str | None
    effect: str
    description: str
    input_schema: dict[str, object]


@dataclass(frozen=True, slots=True)
class BrowserAdapterContract:
    package_name: str
    package_version: str
    upstream_server_name: str
    upstream_server_version: str
    tools: dict[str, BrowserToolContract]


def _tool(
    upstream_name: str | None,
    effect: str,
    description: str,
    schema: dict[str, object],
) -> BrowserToolContract:
    return BrowserToolContract(upstream_name, effect, description, schema)


BROWSER_ADAPTERS: Final[dict[str, BrowserAdapterContract]] = {
    "chrome-devtools-mcp": BrowserAdapterContract(
        package_name="chrome-devtools-mcp",
        package_version="1.6.0",
        upstream_server_name="chrome_devtools",
        upstream_server_version="1.6.0",
        tools={
            "browser_session_status": _tool(None, "read", "查看受控浏览器会话状态。", STATUS_SCHEMA),
            "navigate_page": _tool("navigate_page", "state-write", "导航到受策略保护的公网页面。", NAVIGATE_SCHEMA),
            "take_snapshot": _tool("take_snapshot", "read", "获取带不透明元素引用的受控页面快照。", SNAPSHOT_SCHEMA),
            "click": _tool("click", "state-write", "点击最近快照中的非敏感元素。", CLICK_SCHEMA),
            "fill": _tool("fill", "state-write", "填写最近快照中的非敏感字段。", FILL_SCHEMA),
            "take_screenshot": _tool("take_screenshot", "artifact-create", "生成服务端 PNG 截图产物。", SCREENSHOT_SCHEMA),
        },
    ),
    "playwright-mcp": BrowserAdapterContract(
        package_name="@playwright/mcp",
        package_version="0.0.79",
        upstream_server_name="Playwright",
        upstream_server_version="1.63.0-alpha-2026-08-05",
        tools={
            "browser_session_status": _tool(None, "read", "查看受控浏览器会话状态。", STATUS_SCHEMA),
            "browser_navigate": _tool("browser_navigate", "state-write", "导航到受策略保护的公网页面。", NAVIGATE_SCHEMA),
            "browser_snapshot": _tool("browser_snapshot", "read", "获取带不透明元素引用的受控页面快照。", SNAPSHOT_SCHEMA),
            "browser_click": _tool("browser_click", "state-write", "点击最近快照中的非敏感元素。", CLICK_SCHEMA),
            "browser_fill_form": _tool("browser_fill_form", "state-write", "填写最近快照中的非敏感字段。", FILL_FORM_SCHEMA),
            "browser_take_screenshot": _tool("browser_take_screenshot", "artifact-create", "生成服务端 PNG 截图产物。", SCREENSHOT_SCHEMA),
        },
    ),
}


def _schema_digest(contract: BrowserAdapterContract) -> str:
    reviewed = [
        {"name": name, "inputSchema": tool.input_schema}
        for name, tool in sorted(contract.tools.items())
    ]
    encoded = json.dumps(
        reviewed, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


# Literal release snapshots.  smoke_browser_adapters.py recomputes and checks
# these values, so an accidental public schema change blocks the image build.
BROWSER_SCHEMA_SHA256: Final[dict[str, str]] = {
    "chrome-devtools-mcp": "74e74ee147c7293035b969af687248b05934052973d7798ecdc651208a7739c3",
    "playwright-mcp": "efafd6dabf2e78173423ed2172092eb9865d82b8571fe5f687b9658ce9caaadc",
}

# Filled after real upstream tools/list capture.  This digest covers the exact
# upstream schemas for the five forwarded tools (the status tool is local).
UPSTREAM_SCHEMA_SHA256: Final[dict[str, str]] = {
    "chrome-devtools-mcp": "a1e53699ad871492ab71a34b8f6aefced24317a6d4851cf1e0e7f6951a1d4c1d",
    "playwright-mcp": "68b833cff0a90f00d3b94bd9979855f83888dbd46ba539d968521949d5268b02",
}


def assert_schema_snapshots() -> None:
    for adapter_id, contract in BROWSER_ADAPTERS.items():
        if BROWSER_SCHEMA_SHA256.get(adapter_id) != _schema_digest(contract):
            raise RuntimeError(f"{adapter_id} reviewed browser schema drifted")


class BrowserPolicyError(ValueError):
    """Stable fail-closed browser policy error."""


def _normalized_host(hostname: str | None) -> str:
    raw = str(hostname or "").strip().rstrip(".").lower()
    if not raw or len(raw) > 253:
        raise BrowserPolicyError("browser_host_invalid")
    try:
        host = raw.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise BrowserPolicyError("browser_host_invalid") from exc
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise BrowserPolicyError("browser_ip_literal_denied")
    if "." not in host:
        raise BrowserPolicyError("browser_single_label_host_denied")
    if host in METADATA_HOSTS or host.endswith(BLOCKED_HOST_SUFFIXES):
        raise BrowserPolicyError("browser_internal_host_denied")
    return host


def canonical_browser_login_tokens(
    value: str, *, host: bool = False
) -> frozenset[str]:
    """Return ordinary and punctuation-folded tokens for login-route checks."""

    boundaries = HOST_COMPONENT_BOUNDARIES if host else LOGIN_COMPONENT_BOUNDARIES
    tokens: set[str] = set()
    ordinary_sequence: list[str] = []
    for component in boundaries.split(value.lower()):
        ordinary = re.findall(r"[a-z0-9]+", component)
        tokens.update(ordinary)
        ordinary_sequence.extend(ordinary)
        compact = "".join(ordinary)
        if compact:
            tokens.add(compact)
    tokens.update(
        left + right
        for left, right in zip(ordinary_sequence, ordinary_sequence[1:])
    )
    return frozenset(tokens)


def browser_query_has_sensitive_key(query: str) -> bool:
    """Reject bearer/signing query parameters, including nested encoded URLs."""

    decoded = query
    for _ in range(2):
        decoded = unquote(decoded)
    for component in QUERY_COMPONENT_BOUNDARIES.split(decoded):
        raw_key = component.partition("=")[0]
        key = "".join(re.findall(r"[a-z0-9]+", raw_key.lower()))
        if not key:
            continue
        if (
            key in SENSITIVE_QUERY_KEYS
            or key.startswith(SENSITIVE_QUERY_PREFIXES)
            or key.endswith(("secret", "signature", "token"))
        ):
            return True
    return False


def validate_browser_url(value: object, *, allow_login_path: bool = False) -> tuple[str, str, str, int]:
    clean = str(value or "").strip()
    if not clean or len(clean) > 16_384:
        raise BrowserPolicyError("browser_url_invalid")
    parsed = urlsplit(clean)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise BrowserPolicyError("browser_scheme_denied")
    if parsed.username is not None or parsed.password is not None:
        raise BrowserPolicyError("browser_userinfo_denied")
    host = _normalized_host(parsed.hostname)
    try:
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise BrowserPolicyError("browser_port_denied") from exc
    if (scheme, port) not in {("http", 80), ("https", 443)}:
        raise BrowserPolicyError("browser_port_denied")
    if browser_query_has_sensitive_key(parsed.query):
        raise BrowserPolicyError("browser_sensitive_query_denied")
    decoded_target = f"{parsed.path}?{parsed.query}"
    # Decode twice so `%256cogin` cannot become a login route after a second
    # server-side decoding pass.  Tokenization treats punctuation as a boundary
    # (`login.html`, `action=login`) without matching benign words like author.
    for _ in range(2):
        decoded_target = unquote(decoded_target)
    target_tokens = canonical_browser_login_tokens(decoded_target)
    host_tokens = canonical_browser_login_tokens(host, host=True)
    if not allow_login_path and (
        target_tokens & LOGIN_PATH_SEGMENTS or host_tokens & LOGIN_PATH_SEGMENTS
    ):
        raise BrowserPolicyError("browser_external_login_denied")
    normalized_parts = SplitResult(
        scheme,
        host if port in {80, 443} else f"{host}:{port}",
        parsed.path or "/",
        parsed.query,
        "",
    )
    normalized = urlunsplit(normalized_parts)
    origin = f"{scheme}://{host}"
    return normalized, origin, host, port


def validate_pinned_addresses(addresses: object) -> tuple[str, ...]:
    if not isinstance(addresses, (list, tuple)):
        raise BrowserPolicyError("browser_dns_failed")
    if not all(isinstance(address, str) and address for address in addresses):
        raise BrowserPolicyError("browser_dns_answer_invalid")
    addresses = tuple(dict.fromkeys(addresses))
    if not addresses:
        raise BrowserPolicyError("browser_dns_failed")
    parsed: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for raw in addresses:
        try:
            parsed.append(ipaddress.ip_address(raw))
        except ValueError as exc:
            raise BrowserPolicyError("browser_dns_answer_invalid") from exc
    synthetic = [address in SYNTHETIC_DNS_NETWORK for address in parsed]
    allow_synthetic = os.getenv("MCP_BROWSER_ALLOW_SYNTHETIC_DNS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if any(synthetic):
        # Synthetic DNS can only be enabled for an all-synthetic answer set.
        # Literal URLs and mixed public/synthetic answers are never accepted.
        if not allow_synthetic or not all(synthetic):
            raise BrowserPolicyError("browser_dns_mixed_or_synthetic_denied")
    elif not all(address.is_global for address in parsed):
        raise BrowserPolicyError("browser_private_dns_denied")
    return tuple(str(address) for address in parsed)


def validate_pinned_records(records: object) -> tuple[str, ...]:
    if not isinstance(records, (list, tuple)):
        raise BrowserPolicyError("browser_dns_failed")
    try:
        addresses = tuple(str(record[4][0]) for record in records)
    except (IndexError, TypeError) as exc:
        raise BrowserPolicyError("browser_dns_answer_invalid") from exc
    return validate_pinned_addresses(addresses)


def resolve_pinned_addresses(host: str, port: int) -> tuple[str, ...]:
    try:
        records = socket.getaddrinfo(
            host,
            port,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except socket.gaierror as exc:
        raise BrowserPolicyError("browser_dns_failed") from exc
    return validate_pinned_records(records)


def validate_snapshot_proof(arguments: dict[str, object]) -> tuple[str, int, str]:
    generation = str(arguments.get("generation") or "").strip().lower()
    revision = arguments.get("page_revision")
    digest = str(arguments.get("page_digest") or "").strip().lower()
    if not GENERATION_PATTERN.fullmatch(generation):
        raise BrowserPolicyError("browser_generation_invalid")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise BrowserPolicyError("browser_revision_invalid")
    if not DIGEST_PATTERN.fullmatch(digest):
        raise BrowserPolicyError("browser_digest_invalid")
    return generation, revision, digest


def validate_ref(value: object) -> str:
    ref = str(value or "").strip()
    if not REF_PATTERN.fullmatch(ref):
        raise BrowserPolicyError("browser_ref_invalid")
    return ref


def assert_non_sensitive_interaction(context: str, value: object | None = None) -> None:
    if SENSITIVE_ELEMENT_PATTERN.search(str(context or "")):
        raise BrowserPolicyError("browser_sensitive_field_denied")
    if value is None:
        return
    text = str(value)
    if len(text) > 4096:
        raise BrowserPolicyError("browser_value_too_large")
    if any(pattern.search(text) for pattern in SENSITIVE_VALUE_PATTERNS):
        raise BrowserPolicyError("browser_sensitive_value_denied")


def session_expiry(started_at: datetime) -> float:
    """Return a finite UTC epoch timestamp for the catalog status contract."""

    return (started_at + timedelta(seconds=SESSION_TTL_SECONDS)).astimezone(UTC).timestamp()
