from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
import json
import os
import socket
import time
from pathlib import Path
from types import MethodType
from typing import Any

import pytest

from server.mcp import browser_proxy
from server.sandbox_sidecar import browser_mcp, browser_server, smoke_browser_adapters
from server.sandbox_sidecar.browser_contracts import (
    BROWSER_ADAPTERS,
    BROWSER_LIMITS,
    BROWSER_SCHEMA_SHA256,
    CONTRACT_VERSION,
    MAX_OUTPUT_BYTES,
    UPSTREAM_SCHEMA_SHA256,
    BrowserPolicyError,
    _schema_digest,
    assert_schema_snapshots,
    resolve_pinned_addresses,
    validate_browser_url,
)


EXPECTED_TOOLS = {
    "chrome-devtools-mcp": {
        "browser_session_status",
        "navigate_page",
        "take_snapshot",
        "click",
        "fill",
        "take_screenshot",
    },
    "playwright-mcp": {
        "browser_session_status",
        "browser_navigate",
        "browser_snapshot",
        "browser_click",
        "browser_fill_form",
        "browser_take_screenshot",
    },
}


class MemoryWriter:
    def __init__(self) -> None:
        self.data = bytearray()
        self.closed = False

    def write(self, value: bytes | bytearray) -> None:
        self.data.extend(value)

    async def drain(self) -> None:
        return None

    def write_eof(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class FakeEgress:
    def __init__(self) -> None:
        self.authorized: list[str] = []
        self.revocation_reason = ""

    async def authorize(self, url: str) -> None:
        self.authorized.append(url)

    async def open_tunnel(self, url: str) -> tuple[asyncio.StreamReader, MemoryWriter]:
        raise AssertionError(f"unexpected tunnel: {url}")


def _handshake(adapter_id: str) -> dict[str, object]:
    return {
        "project_id": adapter_id,
        "contract_version": CONTRACT_VERSION,
        "tool_schema_sha256": BROWSER_SCHEMA_SHA256[adapter_id],
        "limits": BROWSER_LIMITS,
    }


def _snapshot_payload(text: str) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": "snapshot",
        "result": {"content": [{"type": "text", "text": text}]},
    }


def test_fixed_public_tools_and_schema_snapshots() -> None:
    assert_schema_snapshots()
    assert set(BROWSER_ADAPTERS) == set(EXPECTED_TOOLS)
    assert set(BROWSER_SCHEMA_SHA256) == set(BROWSER_ADAPTERS)
    assert set(UPSTREAM_SCHEMA_SHA256) == set(BROWSER_ADAPTERS)
    forbidden = {
        "evaluate",
        "evaluate_script",
        "browser_evaluate",
        "upload_file",
        "browser_file_upload",
        "press_key",
        "browser_press_key",
        "browser_tabs",
        "list_pages",
    }
    for adapter_id, contract in BROWSER_ADAPTERS.items():
        assert set(contract.tools) == EXPECTED_TOOLS[adapter_id]
        assert _schema_digest(contract) == BROWSER_SCHEMA_SHA256[adapter_id]
        assert not set(contract.tools) & forbidden
        for name in set(contract.tools) & {
            "click",
            "fill",
            "browser_click",
            "browser_fill_form",
        }:
            schema = contract.tools[name].input_schema
            properties = schema["properties"]
            assert properties["ref"]["x-modelmirror-input"] == "browser-ref"
            assert not {"generation", "page_revision", "page_digest"} & set(properties)
            assert schema["x-modelmirror-ref-proof"] == "sidecar-bound"
        screenshot = next(
            tool for name, tool in contract.tools.items() if "screenshot" in name
        )
        assert set(screenshot.input_schema["properties"]) == {"full_page"}


def test_browser_seccomp_profile_preserves_default_deny_and_capability_gates() -> None:
    profile_path = Path(browser_server.__file__).with_name(
        "seccomp_profile.browser.json"
    )
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    assert profile["defaultAction"] == "SCMP_ACT_ERRNO"
    rules = profile["syscalls"]
    assert rules[0]["action"] == "SCMP_ACT_ALLOW"
    assert rules[0]["names"] == ["chroot", "clone", "setns", "unshare"]
    assert rules[0].get("includes") == {}

    ptrace_syscalls = {"ptrace", "process_vm_readv", "process_vm_writev"}
    ptrace_rules = [
        rule
        for rule in rules
        if rule.get("action") == "SCMP_ACT_ALLOW"
        and ptrace_syscalls.intersection(rule.get("names", []))
    ]
    assert ptrace_rules
    for rule in ptrace_rules:
        includes = rule.get("includes") or {}
        assert "CAP_SYS_PTRACE" in set(includes.get("caps") or [])
        assert "minKernel" not in includes

    capability_only = {
        "mount",
        "mount_setattr",
        "open_by_handle_at",
        "pidfd_getfd",
        "pivot_root",
        "bpf",
        "keyctl",
        "userfaultfd",
        "io_uring_setup",
        "io_uring_enter",
        "io_uring_register",
    }
    for rule in rules:
        if rule.get("action") != "SCMP_ACT_ALLOW":
            continue
        covered = capability_only.intersection(rule.get("names", []))
        if not covered:
            continue
        includes = rule.get("includes") or {}
        assert includes.get("caps"), f"unconditional allow for {sorted(covered)}"
        assert "minKernel" not in includes


def test_browser_downloader_is_integrity_locked_and_pruned_from_release() -> None:
    sidecar_dir = Path(browser_server.__file__).parent
    lock = json.loads(
        (sidecar_dir / "browser_requirements.lock").read_text(encoding="utf-8")
    )
    root = lock["packages"][""]
    assert root["devDependencies"] == {"@puppeteer/browsers": "3.0.6"}
    downloader = lock["packages"]["node_modules/@puppeteer/browsers"]
    assert downloader["version"] == "3.0.6"
    assert downloader["resolved"] == (
        "https://registry.npmjs.org/@puppeteer/browsers/-/browsers-3.0.6.tgz"
    )
    assert downloader["integrity"] == (
        "sha512-B/gKoqlFkzhvzsI6jo9K1cZz9o5ypviVv/xu8CwA4grZzyVwN+"
        "XfkT+tu8T1zrauuEXv6VhS2oGX+6NL95WcKA=="
    )
    assert downloader["dev"] is True

    dockerfile = (sidecar_dir / "Dockerfile.browser").read_text(encoding="utf-8")
    assert "npm ci --include=dev --ignore-scripts --no-audit --no-fund" in dockerfile
    assert "./node_modules/.bin/browsers install" in dockerfile
    assert "npx --yes" not in dockerfile
    assert "npm prune --omit=dev --ignore-scripts --no-audit --no-fund" in dockerfile
    assert "test ! -e ./node_modules/@puppeteer/browsers" in dockerfile
    assert "test ! -e ./node_modules/.bin/browsers" in dockerfile
    assert "EncryptedClientHelloEnabled:false" in dockerfile

def test_proxy_handshake_is_exact_and_size_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter_id = "chrome-devtools-mcp"
    encoded = base64.urlsafe_b64encode(
        json.dumps(_handshake(adapter_id), separators=(",", ":")).encode()
    ).decode()
    monkeypatch.setenv("MCP_BROWSER_HANDSHAKE_B64", encoded)
    assert browser_proxy._load_handshake(adapter_id) == _handshake(adapter_id)
    assert "MCP_BROWSER_HANDSHAKE_B64" not in __import__("os").environ

    wrong = _handshake(adapter_id)
    wrong["limits"] = {**BROWSER_LIMITS, "max_actions": 51}
    monkeypatch.setenv(
        "MCP_BROWSER_HANDSHAKE_B64",
        base64.urlsafe_b64encode(json.dumps(wrong).encode()).decode(),
    )
    with pytest.raises(ValueError, match="limits_mismatch"):
        browser_proxy._load_handshake(adapter_id)


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("file:///etc/passwd", "browser_scheme_denied"),
        ("https://user@example.com/", "browser_userinfo_denied"),
        ("https://127.0.0.1/", "browser_ip_literal_denied"),
        ("https://localhost/", "browser_single_label_host_denied"),
        ("https://metadata.google.internal/", "browser_internal_host_denied"),
        ("https://example.com:8443/", "browser_port_denied"),
        ("https://login.example.com/", "browser_external_login_denied"),
        ("https://example.com/login.html", "browser_external_login_denied"),
        ("https://example.com/%6cogin", "browser_external_login_denied"),
        ("https://example.com/%256cogin", "browser_external_login_denied"),
        ("https://example.com/sign-in", "browser_external_login_denied"),
        ("https://example.com/sign_in", "browser_external_login_denied"),
        ("https://example.com/log-in", "browser_external_login_denied"),
        ("https://example.com/log_in", "browser_external_login_denied"),
        ("https://example.com/sign/in", "browser_external_login_denied"),
        ("https://example.com/user-sign-in", "browser_external_login_denied"),
        ("https://example.com/%73ign%2Din", "browser_external_login_denied"),
        ("https://example.com/%2573ign%252Din", "browser_external_login_denied"),
        ("https://example.com/log%255Fin", "browser_external_login_denied"),
        ("https://sign-in.example.com/", "browser_external_login_denied"),
        ("https://sign.in.example.com/", "browser_external_login_denied"),
        ("https://example.com/?action=login", "browser_external_login_denied"),
        ("https://accounts.example.com/", "browser_external_login_denied"),
        ("https://example.com/session/new", "browser_external_login_denied"),
        ("https://example.com/consent", "browser_external_login_denied"),
        ("https://example.com/saml/callback", "browser_external_login_denied"),
        ("https://example.com/oidc", "browser_external_login_denied"),
    ],
)
def test_url_policy_blocks_ssrf_and_login_bypasses(url: str, reason: str) -> None:
    with pytest.raises(BrowserPolicyError, match=reason):
        validate_browser_url(url)


def test_url_policy_does_not_confuse_author_with_auth() -> None:
    normalized, origin, host, port = validate_browser_url(
        "https://authors.example.com/author/profile"
    )
    assert normalized == "https://authors.example.com/author/profile"
    assert (origin, host, port) == (
        "https://authors.example.com",
        "authors.example.com",
        443,
    )
    validate_browser_url("https://example.com/design/inspiration")


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/report?token=opaque",
        "https://example.com/report?access_token=opaque",
        "https://example.com/report?%2561pi%255Fkey=opaque",
        "https://example.com/report?X-Amz-Signature=opaque",
        "https://example.com/report?x-goog-credential=opaque",
        "https://example.com/report?sig=opaque",
        (
            "https://example.com/report?redirect="
            "https%253A%252F%252Fother.example%252Fcallback"
            "%253Foauth_token%253Dopaque"
        ),
    ],
)
def test_url_policy_rejects_sensitive_query_keys_after_nested_decoding(
    url: str,
) -> None:
    with pytest.raises(BrowserPolicyError, match="browser_sensitive_query_denied"):
        validate_browser_url(url)


def test_url_policy_allows_ordinary_pagination_and_sort_query() -> None:
    normalized, origin, host, port = validate_browser_url(
        "https://example.com/report?page=2&sort=recent"
    )
    assert normalized == "https://example.com/report?page=2&sort=recent"
    assert (origin, host, port) == (
        "https://example.com",
        "example.com",
        443,
    )


def _dns_record(address: str) -> tuple[Any, ...]:
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    sockaddr: tuple[Any, ...] = (address, 443, 0, 0) if family == socket.AF_INET6 else (address, 443)
    return (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr)


def _doh_response(payload: dict[str, object]) -> bytes:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: application/dns-json\r\n"
        + f"Content-Length: {len(body)}\r\n".encode("ascii")
        + b"Connection: close\r\n\r\n"
        + body
    )


def _memory_reader(payload: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(payload)
    reader.feed_eof()
    return reader


def test_dns_policy_rejects_private_mixed_and_rebinding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [_dns_record("93.184.216.34"), _dns_record("10.0.0.1")],
    )
    with pytest.raises(BrowserPolicyError, match="private_dns_denied"):
        resolve_pinned_addresses("example.com", 443)

    monkeypatch.setenv("MCP_BROWSER_ALLOW_SYNTHETIC_DNS", "true")
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [_dns_record("93.184.216.34"), _dns_record("198.18.0.9")],
    )
    with pytest.raises(BrowserPolicyError, match="mixed_or_synthetic_denied"):
        resolve_pinned_addresses("example.com", 443)

    answers = iter(
        [
            [_dns_record("93.184.216.34")],
            [_dns_record("169.254.169.254")],
        ]
    )
    monkeypatch.delenv("MCP_BROWSER_ALLOW_SYNTHETIC_DNS", raising=False)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: next(answers))
    assert resolve_pinned_addresses("example.com", 443) == ("93.184.216.34",)
    with pytest.raises(BrowserPolicyError, match="private_dns_denied"):
        resolve_pinned_addresses("example.com", 443)


@pytest.mark.asyncio
async def test_production_egress_uses_fixed_tls_doh_and_pins_all_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MCP_BROWSER_ALLOW_SYNTHETIC_DNS", raising=False)
    responses = iter(
        [
            _doh_response(
                {
                    "Status": 0,
                    "Answer": [
                        {"name": "example.com.", "type": 1, "data": "93.184.216.34"},
                    ],
                }
            ),
            _doh_response(
                {
                    "Status": 0,
                    "Answer": [
                        {"name": "example.com.", "type": 28, "data": "2606:2800:220:1:248:1893:25c8:1946"},
                    ],
                }
            ),
        ]
    )
    writers: list[MemoryWriter] = []

    async def fixed_open_connection(
        address: str,
        port: int,
        **kwargs: object,
    ) -> tuple[asyncio.StreamReader, MemoryWriter]:
        assert (address, port) == ("1.1.1.1", 443)
        assert kwargs.get("server_hostname") == "cloudflare-dns.com"
        assert kwargs.get("ssl") is not None
        writer = MemoryWriter()
        writers.append(writer)
        return _memory_reader(next(responses)), writer

    async def forbidden_system_dns(*args: object, **kwargs: object) -> object:
        raise AssertionError("production browser egress must not use host DNS")

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", forbidden_system_dns)
    monkeypatch.setattr(asyncio, "open_connection", fixed_open_connection)

    service = browser_server.BrowserEgressService("k" * 64)
    assert await service.resolve("example.com", 443) == (
        "93.184.216.34",
        "2606:2800:220:1:248:1893:25c8:1946",
    )
    assert len(writers) == 2
    assert b"GET /dns-query?name=example.com&type=A HTTP/1.1" in writers[0].data
    assert b"GET /dns-query?name=example.com&type=AAAA HTTP/1.1" in writers[1].data
    assert all(b"Accept: application/dns-json" in writer.data for writer in writers)


@pytest.mark.asyncio
async def test_synthetic_fixture_mode_keeps_system_dns_isolated_from_doh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_BROWSER_ALLOW_SYNTHETIC_DNS", "true")
    loop = asyncio.get_running_loop()

    async def fixture_dns(*args: object, **kwargs: object) -> object:
        return [_dns_record("198.18.0.8")]

    async def forbidden_doh(host: str) -> tuple[str, ...]:
        raise AssertionError(f"fixture DNS unexpectedly used DoH for {host}")

    monkeypatch.setattr(loop, "getaddrinfo", fixture_dns)
    monkeypatch.setattr(browser_server, "_resolve_fixed_doh", forbidden_doh)
    service = browser_server.BrowserEgressService("k" * 64)
    assert await service.resolve("fixture.wave7", 80) == ("198.18.0.8",)


@pytest.mark.asyncio
async def test_doh_rejects_private_or_malformed_answer_sets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MCP_BROWSER_ALLOW_SYNTHETIC_DNS", raising=False)
    responses = iter(
        [
            _doh_response(
                {
                    "Status": 0,
                    "Answer": [
                        {"name": "example.com.", "type": 1, "data": "169.254.169.254"},
                    ],
                }
            ),
            _doh_response({"Status": 0, "Answer": []}),
        ]
    )

    async def fixed_open_connection(
        *args: object, **kwargs: object
    ) -> tuple[asyncio.StreamReader, MemoryWriter]:
        return _memory_reader(next(responses)), MemoryWriter()

    monkeypatch.setattr(asyncio, "open_connection", fixed_open_connection)
    with pytest.raises(BrowserPolicyError, match="browser_private_dns_denied"):
        await browser_server.BrowserEgressService("k" * 64).resolve(
            "example.com", 443
        )


@pytest.mark.asyncio
async def test_single_origin_is_immutable_after_first_navigation() -> None:
    egress = FakeEgress()
    proxy = browser_server.LoopbackBrowserProxy(egress)  # type: ignore[arg-type]
    assert await proxy.authorize("https://example.com/a") == "https://example.com/a"
    assert await proxy.authorize("https://example.com/b") == "https://example.com/b"
    with pytest.raises(BrowserPolicyError, match="browser_cross_origin_denied"):
        await proxy.authorize("https://other.example/b")
    assert len(egress.authorized) == 2


@pytest.mark.asyncio
async def test_http_proxy_rejects_mismatched_or_duplicate_host() -> None:
    for headers in (
        b"Host: evil.example\r\n\r\n",
        b"Host: example.com\r\nHost: example.com\r\n\r\n",
    ):
        reader = asyncio.StreamReader()
        reader.feed_data(b"GET http://example.com/ HTTP/1.1\r\n" + headers)
        reader.feed_eof()
        writer = MemoryWriter()
        proxy = browser_server.LoopbackBrowserProxy(FakeEgress())  # type: ignore[arg-type]
        proxy.origin, proxy.host, proxy.port = (
            "http://example.com",
            "example.com",
            80,
        )
        await proxy.handle(reader, writer)  # type: ignore[arg-type]
        assert bytes(writer.data).startswith(b"HTTP/1.1 403")
        assert proxy.last_violation == "browser_http_host_mismatch"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        b"Upgrade: websocket\r\nHost: example.com\r\n\r\n",
        b"uPgRaDe: websocket\r\nHost: example.com\r\nUpgrade: websocket\r\n\r\n",
        b"Connection: keep-alive, Upgrade\r\nHost: example.com\r\n\r\n",
        b"Proxy-Authorization: Basic opaque\r\nHost: example.com\r\n\r\n",
    ],
)
async def test_http_proxy_rejects_upgrade_and_proxy_auth_in_any_header_position(
    headers: bytes,
) -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(b"GET http://example.com/ HTTP/1.1\r\n" + headers)
    reader.feed_eof()
    writer = MemoryWriter()
    proxy = browser_server.LoopbackBrowserProxy(FakeEgress())  # type: ignore[arg-type]
    proxy.origin, proxy.host, proxy.port = (
        "http://example.com",
        "example.com",
        80,
    )
    await proxy.handle(reader, writer)  # type: ignore[arg-type]
    assert bytes(writer.data).startswith(b"HTTP/1.1 403")
    assert proxy.last_violation == "browser_websocket_or_proxy_auth_denied"


@pytest.mark.asyncio
async def test_bounded_android_gcm_probes_are_blocked_without_taint_or_egress() -> None:
    proxy = browser_server.LoopbackBrowserProxy(FakeEgress())  # type: ignore[arg-type]
    proxy.origin, proxy.host, proxy.port = "https://example.com", "example.com", 443
    proxy.forwarded_requests = 1
    proxy.authorized_at = time.monotonic()

    def request() -> asyncio.StreamReader:
        reader = asyncio.StreamReader()
        reader.feed_data(
            b"CONNECT android.clients.google.com:443 HTTP/1.1\r\n"
            b"Host: android.clients.google.com:443\r\n\r\n"
        )
        reader.feed_eof()
        return reader

    for expected_count in range(1, 5):
        blocked_writer = MemoryWriter()
        await proxy.handle(request(), blocked_writer)  # type: ignore[arg-type]
        assert bytes(blocked_writer.data).startswith(b"HTTP/1.1 403")
        assert proxy.last_violation == ""
        assert proxy.suppressed_android_client_probes == expected_count

    fifth_writer = MemoryWriter()
    await proxy.handle(request(), fifth_writer)  # type: ignore[arg-type]
    assert bytes(fifth_writer.data).startswith(b"HTTP/1.1 403")
    assert proxy.last_violation == "browser_cross_origin_denied"
    assert (
        proxy.last_violation_event
        == "proxy_policy_cross_origin_android_client_after_forward"
    )


def _client_hello(host: str, *, ech_after_sni: bool = False, duplicate_sni: bool = False) -> bytes:
    raw_host = host.encode("ascii")
    name = b"\x00" + len(raw_host).to_bytes(2, "big") + raw_host
    sni_body = len(name).to_bytes(2, "big") + name
    sni = b"\x00\x00" + len(sni_body).to_bytes(2, "big") + sni_body
    extensions = sni + (sni if duplicate_sni else b"")
    if ech_after_sni:
        extensions += b"\xfe\x0d\x00\x01\x00"
    body = (
        b"\x03\x03"
        + b"\x00" * 32
        + b"\x00"
        + b"\x00\x02\x13\x01"
        + b"\x01\x00"
        + len(extensions).to_bytes(2, "big")
        + extensions
    )
    return b"\x01" + len(body).to_bytes(3, "big") + body


def _tls_record(payload: bytes) -> bytes:
    return b"\x16\x03\x03" + len(payload).to_bytes(2, "big") + payload


def test_sni_parser_rejects_ech_after_sni_and_duplicate_sni() -> None:
    assert browser_server._client_hello_sni(_client_hello("example.com")) == "example.com"
    with pytest.raises(BrowserPolicyError, match="browser_tls_ech_denied"):
        browser_server._client_hello_sni(
            _client_hello("example.com", ech_after_sni=True)
        )
    with pytest.raises(BrowserPolicyError, match="browser_tls_sni_denied"):
        browser_server._client_hello_sni(
            _client_hello("example.com", duplicate_sni=True)
        )


@pytest.mark.asyncio
async def test_fragmented_client_hello_is_reassembled_and_sni_checked() -> None:
    hello = _client_hello("example.com")
    midpoint = len(hello) // 2
    wire = _tls_record(hello[:midpoint]) + _tls_record(hello[midpoint:])
    reader = asyncio.StreamReader()
    for index in range(0, len(wire), 3):
        reader.feed_data(wire[index : index + 3])
    reader.feed_eof()
    writer = MemoryWriter()

    class Service:
        consumed = 0
        revoked = ""

        async def consume(self, capability: str, amount: int) -> None:
            self.consumed += amount

        async def revoke(self, capability: str, reason: str) -> None:
            self.revoked = reason

    service = Service()
    await browser_server._relay_limited(  # type: ignore[arg-type]
        reader,
        writer,
        service,
        "c" * 64,
        asyncio.get_running_loop().time() + 5,
        expected_sni="example.com",
    )
    assert bytes(writer.data) == wire
    assert service.consumed == len(wire)
    assert not service.revoked


@pytest.mark.asyncio
async def test_sni_mismatch_revokes_tunnel() -> None:
    wire = _tls_record(_client_hello("evil.example"))
    reader = asyncio.StreamReader()
    reader.feed_data(wire)
    reader.feed_eof()
    writer = MemoryWriter()

    class Service:
        revoked = ""

        async def consume(self, capability: str, amount: int) -> None:
            raise AssertionError("mismatched SNI must not be forwarded")

        async def revoke(self, capability: str, reason: str) -> None:
            self.revoked = reason

    service = Service()
    with pytest.raises(BrowserPolicyError, match="browser_tls_sni_denied"):
        await browser_server._relay_limited(  # type: ignore[arg-type]
            reader,
            writer,
            service,
            "c" * 64,
            asyncio.get_running_loop().time() + 5,
            expected_sni="example.com",
        )
    assert service.revoked == "browser_tls_sni_denied"
    assert not writer.data


@pytest.mark.asyncio
async def test_ref_binding_rejects_dom_drift(tmp_path: Path) -> None:
    state = browser_server.BrowserSessionState("chrome-devtools-mcp", "a" * 32)
    state.page_revision = 4
    state.page_digest = "1" * 64
    state.refs["1_2"] = browser_server.RefBinding(
        state.generation,
        4,
        "1" * 64,
        'button "Continue" uid=1_2',
        "button",
        "Continue",
    )
    gateway = browser_server.BrowserGatewaySession(
        "chrome-devtools-mcp",
        state,
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        tmp_path,
        tmp_path,
        tmp_path,
    )

    async def observe(self: Any) -> tuple[str, dict[str, str], str, str, str]:
        return "changed", {"1_2": "button"}, "", "", "2" * 64

    gateway._observe = MethodType(observe, gateway)  # type: ignore[method-assign]
    with pytest.raises(BrowserPolicyError, match="browser_state_drift"):
        await gateway._verify_bound_refs(["1_2"])
    assert state.page_revision == 5
    assert not state.refs


def test_unregistered_download_is_deleted_before_it_can_persist(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    registered = tmp_path / "registered"
    staging.mkdir()
    registered.mkdir()
    download = staging / "page-triggered-download.bin"
    download.write_bytes(b"untrusted")
    gateway = browser_server.BrowserGatewaySession(
        "playwright-mcp",
        browser_server.BrowserSessionState("playwright-mcp", "b" * 32),
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        tmp_path,
        staging,
        registered,
    )
    assert gateway._purge_unregistered_artifacts() is True
    assert gateway.unregistered_artifact_event == "unregistered_artifact_download"
    assert not download.exists()


def test_unregistered_artifact_diagnostics_are_fixed_and_content_free(
    tmp_path: Path,
) -> None:
    profile = tmp_path / "profile"
    staging = tmp_path / "artifacts" / "staging"
    registered = tmp_path / "artifacts" / "registered"
    for path in (profile, staging, registered):
        path.mkdir(parents=True, exist_ok=True)
    gateway = browser_server.BrowserGatewaySession(
        "playwright-mcp",
        browser_server.BrowserSessionState("playwright-mcp", "d" * 32),
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        staging.parent,
        staging,
        registered,
    )
    console_log = staging / "console-2026-08-08T12-34-56-000Z.log"
    console_log.write_text("https://secret.invalid/?token=hidden", encoding="utf-8")
    assert gateway._purge_unregistered_artifacts() is True
    assert gateway.unregistered_artifact_event == "unregistered_artifact_console_log"
    assert "secret" not in gateway.unregistered_artifact_event
    assert not console_log.exists()


def _artifact_gateway(tmp_path: Path) -> tuple[browser_server.BrowserGatewaySession, Path]:
    staging = tmp_path / "artifacts" / "staging"
    registered = tmp_path / "artifacts" / "registered"
    staging.mkdir(parents=True)
    registered.mkdir()
    return (
        browser_server.BrowserGatewaySession(
            "playwright-mcp",
            browser_server.BrowserSessionState("playwright-mcp", "e" * 32),
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            staging.parent,
            staging,
            registered,
        ),
        staging,
    )


def test_unregistered_nested_directory_is_not_silently_ignored(tmp_path: Path) -> None:
    gateway, staging = _artifact_gateway(tmp_path)
    nested = staging / "nested"
    nested.mkdir()
    (nested / "payload.bin").write_bytes(b"untrusted")

    assert gateway._purge_unregistered_artifacts() is True
    assert gateway.unregistered_artifact_event == "unregistered_artifact_other"


def test_unregistered_symlink_is_unlinked_without_touching_target(tmp_path: Path) -> None:
    gateway, staging = _artifact_gateway(tmp_path)
    target = tmp_path / "outside.txt"
    target.write_text("keep", encoding="utf-8")
    link = staging / "download.bin"
    link.symlink_to(target)

    assert gateway._purge_unregistered_artifacts() is True
    assert gateway.unregistered_artifact_event == "unregistered_artifact_other"
    assert not link.exists()
    assert target.read_text(encoding="utf-8") == "keep"


def test_unregistered_hardlink_is_rejected_and_source_survives(tmp_path: Path) -> None:
    gateway, staging = _artifact_gateway(tmp_path)
    source = tmp_path / "outside.bin"
    source.write_bytes(b"keep")
    linked = staging / "download.bin"
    os.link(source, linked)

    assert gateway._purge_unregistered_artifacts() is True
    assert gateway.unregistered_artifact_event == "unregistered_artifact_download"
    assert not linked.exists()
    assert source.read_bytes() == b"keep"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO requires POSIX")
def test_unregistered_fifo_is_rejected_and_removed(tmp_path: Path) -> None:
    gateway, staging = _artifact_gateway(tmp_path)
    fifo = staging / "browser.pipe"
    os.mkfifo(fifo)

    assert gateway._purge_unregistered_artifacts() is True
    assert gateway.unregistered_artifact_event == "unregistered_artifact_other"
    assert not fifo.exists()


@pytest.mark.asyncio
async def test_egress_byte_budget_revokes_capability() -> None:
    service = browser_server.BrowserEgressService("k" * 64)
    capability = "c" * 64
    service.grants[capability] = browser_server.EgressGrant(
        origin="https://example.com",
        host="example.com",
        port=443,
        expires_at=asyncio.get_running_loop().time() + 60,
        transferred_bytes=browser_server.MAX_EGRESS_BYTES_PER_SESSION - 1,
    )
    with pytest.raises(BrowserPolicyError, match="browser_egress_byte_budget"):
        await service.consume(capability, 2)
    assert capability not in service.grants


@pytest.mark.asyncio
async def test_async_dns_timeout_does_not_block_revocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_BROWSER_ALLOW_SYNTHETIC_DNS", "true")
    loop = asyncio.get_running_loop()

    async def stalled_getaddrinfo(*args: object, **kwargs: object) -> object:
        await asyncio.Future()

    monkeypatch.setattr(loop, "getaddrinfo", stalled_getaddrinfo)
    monkeypatch.setattr(browser_server, "DNS_TIMEOUT_SECONDS", 0.01)
    service = browser_server.BrowserEgressService("k" * 64)
    capability = "7" * 64
    service.grants[capability] = browser_server.EgressGrant(
        expires_at=loop.time() + 60
    )
    resolving = asyncio.create_task(service.resolve("example.com", 443))
    await asyncio.wait_for(service.revoke(capability, "test_revoke"), timeout=0.1)
    assert capability not in service.grants
    with pytest.raises(BrowserPolicyError, match="browser_dns_timeout"):
        await resolving


def test_commands_are_real_locked_upstreams_with_no_sandbox_bypass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_BROWSER_CDP_CHROMIUM_PATH", "/locked/chrome-150")
    monkeypatch.setenv(
        "MCP_BROWSER_PLAYWRIGHT_CHROMIUM_PATH", "/locked/chromium-152"
    )
    for adapter_id, contract in BROWSER_ADAPTERS.items():
        profile = tmp_path / adapter_id
        artifacts = profile / "artifacts"
        artifacts.mkdir(parents=True)
        command = browser_mcp.upstream_command(
            adapter_id,
            profile_dir=profile,
            artifact_dir=artifacts,
            proxy_url="http://127.0.0.1:54321",
        )
        joined = " ".join(command)
        assert contract.package_name.split("/")[-1] in joined
        expected_browser = (
            "/locked/chrome-150"
            if adapter_id == "chrome-devtools-mcp"
            else "/locked/chromium-152"
        )
        assert expected_browser in joined
        assert (
            "/locked/chromium-152"
            if adapter_id == "chrome-devtools-mcp"
            else "/locked/chrome-150"
        ) not in joined
        assert "--no-sandbox" not in joined
        assert "--disable-web-security" not in joined
        assert "--remote-debugging-address=0.0.0.0" not in joined
        assert "--disable-dev-shm-usage" in joined or adapter_id == "playwright-mcp"
        if adapter_id == "playwright-mcp":
            assert "--sandbox" in command
            assert "--no-sandbox" not in command
            assert "--snapshot-mode=none" in command
            assert "--snapshot-mode=full" not in command
            config = json.loads((profile / "modelmirror-playwright-config.json").read_text())
            launch_args = config["browser"]["launchOptions"]["args"]
            assert "--disable-dev-shm-usage" in launch_args
            assert config["browser"]["contextOptions"]["acceptDownloads"] is False
            assert config["snapshot"] == {"mode": "none"}
            feature_surface = " ".join(launch_args)
        else:
            feature_surface = joined
        local_state = json.loads((profile / "Local State").read_text())
        assert local_state == {"ssl": {"ech_enabled": False}}
        assert "kAutofillServerCommunication" in feature_surface
        assert "PushMessaging,PushMessagingSubscriptionChange" in feature_surface
        assert "--incognito" in feature_surface
        assert "NetworkTimeServiceQuerying" in feature_surface
        assert "PreconnectToSearch" in feature_surface
        assert "--disable-preconnect" in feature_surface
        assert "NoSearchDomainCheck" in feature_surface


def test_landlock_does_not_grant_egress_socket_or_shared_dev_shm() -> None:
    source = Path(browser_mcp.__file__).read_text(encoding="utf-8")
    assert 'Path("/run")' not in source
    assert 'Path("/run/modelmirror-browser-egress")' not in source
    assert 'add(Path("/dev/shm")' not in source
    assert 'Path("/opt/modelmirror-browsers")' in source
    assert 'Path("/ms-playwright")' not in source
    assert "apply_browser_landlock(profile_dir, staging_dir)" in source


def test_landlock_proc_access_restores_only_the_write_file_category() -> None:
    assert browser_mcp.PROC_USERNS_ACCESS == (
        browser_mcp.READ_EXECUTE | browser_mcp.ACCESS_FS_WRITE_FILE
    )
    assert browser_mcp.PROC_USERNS_ACCESS & browser_mcp.ACCESS_FS_WRITE_FILE
    assert not browser_mcp.PROC_USERNS_ACCESS & browser_mcp.ACCESS_FS_MAKE_REG
    assert not browser_mcp.PROC_USERNS_ACCESS & browser_mcp.ACCESS_FS_REMOVE_FILE
    assert not browser_mcp.PROC_USERNS_ACCESS & browser_mcp.ACCESS_FS_TRUNCATE
    source = Path(browser_mcp.__file__).read_text(encoding="utf-8")
    assert 'add(Path("/proc"), PROC_USERNS_ACCESS)' in source
    assert 'Path("/run/modelmirror-browser-egress")' not in source


def test_browser_runtime_diagnostics_are_fixed_enums_without_payloads() -> None:
    assert "rpc_timeout" in browser_server.BROWSER_RUNTIME_EVENT_CODES
    assert "proxy_policy_violation" in browser_server.BROWSER_RUNTIME_EVENT_CODES
    assert (
        browser_server._proxy_runtime_event("browser_cross_origin_denied")
        == "proxy_policy_cross_origin"
    )
    assert (
        browser_server._proxy_runtime_event("browser_dns_failed")
        == "proxy_policy_dns_failed"
    )
    assert (
        browser_server._proxy_runtime_event("browser_tls_sni_denied")
        == "proxy_policy_tls_sni_denied"
    )
    assert (
        browser_server._proxy_runtime_event("https://secret.invalid/?token=hidden")
        == "proxy_policy_violation"
    )
    assert "egress_revoked" in browser_server.BROWSER_RUNTIME_EVENT_CODES
    assert "browser_policy_snapshot_structure" in browser_server.BROWSER_RUNTIME_EVENT_CODES
    assert all("http" not in item for item in browser_server.BROWSER_RUNTIME_EVENT_CODES)
    source = inspect.getsource(browser_server._emit_browser_runtime_event)
    assert "browser_runtime_event" in source
    assert "arguments" not in source
    assert "result_text" not in source


def test_cross_origin_runtime_diagnostic_only_reports_relation_and_phase() -> None:
    proxy = browser_server.LoopbackBrowserProxy(FakeEgress())  # type: ignore[arg-type]
    proxy.origin, proxy.host, proxy.port = "http://example.com", "example.com", 80
    assert (
        proxy._cross_origin_event("GET", "http://other.example/path?secret=value")
        == "proxy_policy_cross_origin_external_host_before_forward"
    )
    assert (
        proxy._cross_origin_event("GET", "http://clients2.google.com/private")
        == "proxy_policy_cross_origin_network_time_before_forward"
    )
    assert (
        proxy._cross_origin_event("CONNECT", "clients1.google.com:443")
        == "proxy_policy_cross_origin_clients1_before_forward"
    )
    assert (
        proxy._cross_origin_event("GET", "https://www.google.com/searchdomaincheck")
        == "proxy_policy_cross_origin_search_domain_before_forward"
    )
    assert (
        proxy._cross_origin_event("GET", "https://www.google.com/opaque/path")
        == "proxy_policy_cross_origin_google_home_other_before_forward"
    )
    assert (
        proxy._cross_origin_event("CONNECT", "www.google.com:443")
        == "proxy_policy_cross_origin_google_home_connect_before_forward"
    )
    assert (
        proxy._cross_origin_event("CONNECT", "unknown.googleapis.com:443")
        == "proxy_policy_cross_origin_google_api_before_forward"
    )
    assert (
        proxy._cross_origin_event("CONNECT", "unknown.google.com:443")
        == "proxy_policy_cross_origin_google_subdomain_before_forward"
    )
    proxy.forwarded_requests = 1
    assert (
        proxy._cross_origin_event("CONNECT", "example.com:443")
        == "proxy_policy_cross_origin_scheme_after_forward"
    )


def test_browser_tmpdir_is_inside_writable_profile_not_artifact_parent() -> None:
    source = Path(browser_server.__file__).read_text(encoding="utf-8")
    assert 'runtime_tmp_dir = profile_dir / "tmp"' in source
    assert '"TMPDIR": str(runtime_tmp_dir)' in source
    assert '"TMPDIR": str(artifact_dir)' not in source
    assert '"NODE_DISABLE_COMPILE_CACHE": "1"' in source
    smoke_source = Path(smoke_browser_adapters.__file__).read_text(encoding="utf-8")
    assert '"NODE_DISABLE_COMPILE_CACHE": "1"' in smoke_source


def test_status_contract_uses_finite_epoch_and_internal_ref_metadata() -> None:
    state = browser_server.BrowserSessionState("chrome-devtools-mcp", "c" * 32)
    status = state.status()
    assert status["status"] == "active"
    assert status["tainted"] is False
    assert isinstance(status["expires_at"], float)
    assert status["expires_at"] > 0
    safe = browser_server._safe_element(
        "1_2",
        browser_mcp.SnapshotElement("button", "Continue", ""),
        state.page_digest,
    )
    assert safe == {
        "ref": "1_2",
        "role": "button",
        "label": "Continue",
        "page_digest": state.page_digest,
    }
    assert browser_server._safe_element(
        "1_3",
        browser_mcp.SnapshotElement("textbox", "Password", ""),
        state.page_digest,
    ) is None


@pytest.mark.parametrize(
    ("adapter_id", "snapshot", "expected"),
    [
        (
            "chrome-devtools-mcp",
            "\n".join(
                [
                    'uid=1_0 RootWebArea "Example Domain" url="https://example.com/"',
                    '  uid=1_1 heading "Example Domain" level="1"',
                    '  uid=1_2 textbox "Search \\"docs\\"" value=""',
                    '  uid=1_3 switch "Enable"',
                    '  uid=1_4 spinbutton "Count" valuemin="0"',
                    '  uid=1_5 treeitem "Node"',
                ]
            ),
            {
                "1_2": ("textbox", 'Search "docs"'),
                "1_3": ("switch", "Enable"),
                "1_4": ("spinbutton", "Count"),
                "1_5": ("treeitem", "Node"),
            },
        ),
        (
            "playwright-mcp",
            "\n".join(
                [
                    "- Page URL: https://example.com/",
                    "- Page Title: Example Domain",
                    "- Page Snapshot:",
                    "```yaml",
                    "- generic [ref=e1]:",
                    '  - textbox "Search \\"docs\\"" [ref=e2]',
                    '  - button "Continue" [ref=e3] [cursor=pointer]',
                    '  - switch "Enable" [ref=e4]',
                    "```",
                ]
            ),
            {
                "e2": ("textbox", 'Search "docs"'),
                "e3": ("button", "Continue"),
                "e4": ("switch", "Enable"),
            },
        ),
    ],
)
def test_locked_snapshot_grammars_return_structural_role_and_name(
    adapter_id: str,
    snapshot: str,
    expected: dict[str, tuple[str, str]],
) -> None:
    _, refs, observed_url, title = browser_mcp.extract_snapshot(
        adapter_id, _snapshot_payload(snapshot)
    )
    assert {ref: (item.role, item.name) for ref, item in refs.items()} == expected
    assert observed_url == "https://example.com/"
    assert title == "Example Domain"
    for ref, item in refs.items():
        safe = browser_server._safe_element(ref, item, "f" * 64)
        assert safe is not None
        assert safe["role"] == item.role
        assert safe["label"] == item.name


@pytest.mark.parametrize(
    ("adapter_id", "snapshot"),
    [
        (
            "chrome-devtools-mcp",
            "\n".join(
                [
                    'uid=1_0 RootWebArea "Demo" url="https://example.com/"',
                    '  uid=1_1 StaticText "Continue uid=1_9 button"',
                    '  uid=1_9 textbox "Password"',
                ]
            ),
        ),
        (
            "playwright-mcp",
            "\n".join(
                [
                    "- Page URL: https://example.com/",
                    "- Page Title: Demo",
                    "- Page Snapshot:",
                    "```yaml",
                    '- text: button "Continue" [ref=e9]',
                    '  - textbox "Password" [ref=e9]',
                    "```",
                ]
            ),
        ),
        (
            "playwright-mcp",
            "\n".join(
                [
                    "- Page URL: https://example.com/",
                    "- Page Title: Demo",
                    "- Page Snapshot:",
                    "```yaml",
                    '  - button "Continue" [ref=e9]',
                    '  - textbox "Delete account" [ref=e9]',
                    "```",
                ]
            ),
        ),
    ],
)
def test_snapshot_ref_spoof_and_duplicate_context_fail_closed(
    adapter_id: str, snapshot: str
) -> None:
    with pytest.raises(
        browser_mcp.SnapshotStructureError,
        match="browser_snapshot_ref_structure_invalid",
    ):
        browser_mcp.extract_snapshot(adapter_id, _snapshot_payload(snapshot))


def test_snapshot_name_cannot_spoof_structural_role() -> None:
    snapshot = "\n".join(
        [
            "- Page URL: https://example.com/",
            "- Page Title: Demo",
            "- Page Snapshot:",
            "```yaml",
            '  - textbox "button Continue" [ref=e2]',
            "```",
        ]
    )
    _, refs, _, _ = browser_mcp.extract_snapshot(
        "playwright-mcp", _snapshot_payload(snapshot)
    )
    item = browser_server._safe_element("e2", refs["e2"], "a" * 64)
    assert item is not None
    assert item["role"] == "textbox"
    assert item["label"] == "button Continue"


@pytest.mark.parametrize(
    ("role", "name", "expected_type"),
    [
        ("textbox", "checkbox consent", "textbox"),
        ("combobox", "checkbox", "combobox"),
    ],
)
def test_playwright_fill_type_uses_structural_role_only(
    role: str, name: str, expected_type: str
) -> None:
    arguments = browser_mcp.to_upstream_arguments(
        "playwright-mcp",
        "browser_fill_form",
        {"ref": "e2", "value": "safe value"},
        ref_roles={"e2": role},
    )
    fields = arguments["fields"]
    assert isinstance(fields, list)
    assert fields[0]["type"] == expected_type


def test_playwright_fill_rejects_non_fillable_structural_role() -> None:
    with pytest.raises(ValueError, match="browser_fill_role_denied"):
        browser_mcp.to_upstream_arguments(
            "playwright-mcp",
            "browser_fill_form",
            {"ref": "e3", "value": "safe value"},
            ref_roles={"e3": "button"},
        )


def test_chrome_page_metadata_comes_from_structural_root() -> None:
    snapshot = "\n".join(
        [
            'uid=1_0 RootWebArea "A \\"quoted\\" title" url="https://example.com/"',
            '  uid=1_1 StaticText "RootWebArea fake url=evil"',
            '  uid=1_2 button "Continue"',
        ]
    )
    _, _, observed_url, title = browser_mcp.extract_snapshot(
        "chrome-devtools-mcp", _snapshot_payload(snapshot)
    )
    assert observed_url == "https://example.com/"
    assert title == 'A "quoted" title'


@pytest.mark.parametrize(
    "snapshot",
    [
        'uid=1_0 RootWebArea "Demo"',
        'uid=1_0 RootWebArea "Demo" url="https://example.com/" url="https://evil.example/"',
        "\n".join(
            [
                'uid=1_0 RootWebArea "Demo" url="https://example.com/"',
                'uid=2_0 RootWebArea "Other" url="https://evil.example/"',
            ]
        ),
    ],
)
def test_chrome_missing_or_duplicate_structural_page_url_fails(snapshot: str) -> None:
    with pytest.raises(
        browser_mcp.SnapshotStructureError,
        match="browser_snapshot_ref_structure_invalid",
    ):
        browser_mcp.extract_snapshot(
            "chrome-devtools-mcp", _snapshot_payload(snapshot)
        )


@pytest.mark.parametrize(
    "duplicate",
    [
        "- Page URL: https://evil.example/",
        "- Page Title: Spoofed",
    ],
)
def test_playwright_duplicate_page_metadata_fails(duplicate: str) -> None:
    snapshot = "\n".join(
        [
            "- Page URL: https://example.com/",
            "- Page Title: Demo",
            duplicate,
            "- Page Snapshot:",
            "```yaml",
            '- button "Continue" [ref=e1]',
            "```",
        ]
    )
    with pytest.raises(
        browser_mcp.SnapshotStructureError,
        match="browser_snapshot_ref_structure_invalid",
    ):
        browser_mcp.extract_snapshot("playwright-mcp", _snapshot_payload(snapshot))


def test_playwright_about_blank_snapshot_accepts_empty_title() -> None:
    snapshot = "\n".join(
        [
            "- Page URL: about:blank",
            "- Page Snapshot:",
            "```yaml",
            "```",
        ]
    )
    _, refs, observed_url, title = browser_mcp.extract_snapshot(
        "playwright-mcp", _snapshot_payload(snapshot)
    )
    assert refs == {}
    assert observed_url == "about:blank"
    assert title == ""


def test_playwright_nonblank_snapshot_still_requires_unique_title() -> None:
    snapshot = "\n".join(
        [
            "- Page URL: https://example.com/",
            "- Page Snapshot:",
            "```yaml",
            "```",
        ]
    )
    with pytest.raises(
        browser_mcp.SnapshotStructureError,
        match="browser_snapshot_ref_structure_invalid",
    ):
        browser_mcp.extract_snapshot("playwright-mcp", _snapshot_payload(snapshot))


@pytest.mark.asyncio
async def test_malformed_snapshot_taints_session_and_cannot_be_used(
    tmp_path: Path,
) -> None:
    staging = tmp_path / "staging"
    registered = tmp_path / "registered"
    staging.mkdir()
    registered.mkdir()
    adversarial = _snapshot_payload(
        "\n".join(
            [
                'uid=1_0 RootWebArea "Demo" url="https://example.com/"',
                '  uid=1_1 StaticText "Continue uid=1_9 button"',
                '  uid=1_9 textbox "Password"',
            ]
        )
    )

    class Process:
        returncode = None

    class Rpc:
        calls = 0

        async def request(self, *args: object, **kwargs: object) -> dict[str, object]:
            self.calls += 1
            return adversarial

    class Egress:
        revocation_reason = ""

    class Proxy:
        egress = Egress()
        last_violation = ""

    rpc = Rpc()
    state = browser_server.BrowserSessionState("chrome-devtools-mcp", "7" * 32)
    gateway = browser_server.BrowserGatewaySession(
        "chrome-devtools-mcp",
        state,
        Process(),  # type: ignore[arg-type]
        rpc,  # type: ignore[arg-type]
        Proxy(),  # type: ignore[arg-type]
        tmp_path,
        staging,
        registered,
    )

    async def taint_without_process(self: Any, reason: str) -> None:
        self.state.taint(reason)

    gateway._taint = MethodType(taint_without_process, gateway)  # type: ignore[method-assign]
    with pytest.raises(BrowserPolicyError, match="browser_snapshot_ref_structure_invalid"):
        await gateway.call("take_snapshot", {})
    assert state.tainted is True
    assert state.refs == {}
    assert rpc.calls == 1
    with pytest.raises(BrowserPolicyError, match="browser_snapshot_ref_structure_invalid"):
        await gateway.call("click", {"ref": "1_9"})
    assert rpc.calls == 1


def test_supervisor_startup_removes_strict_crash_residue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profiles = tmp_path / "profiles"
    artifacts = tmp_path / "artifacts"
    profiles.mkdir()
    artifacts.mkdir()
    session_id = "d" * 32
    profile = profiles / session_id
    staging = artifacts / session_id / "staging"
    registered = artifacts / session_id / "registered"
    profile.mkdir()
    (profile / "Preferences").write_text("stale", encoding="utf-8")
    staging.mkdir(parents=True)
    registered.mkdir()
    (staging / "download.tmp").write_bytes(b"stale")
    (registered / f"browser_{'e' * 32}.png").write_bytes(b"stale")
    monkeypatch.setattr(browser_server, "PROFILE_ROOT", profiles)
    monkeypatch.setattr(browser_server, "ARTIFACT_ROOT", artifacts)

    browser_server._cleanup_stale_runtime_roots()

    assert list(profiles.iterdir()) == []
    assert list(artifacts.iterdir()) == []


def test_supervisor_cleanup_fails_closed_on_malformed_or_linked_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profiles = tmp_path / "profiles"
    artifacts = tmp_path / "artifacts"
    profiles.mkdir()
    artifacts.mkdir()
    monkeypatch.setattr(browser_server, "PROFILE_ROOT", profiles)
    monkeypatch.setattr(browser_server, "ARTIFACT_ROOT", artifacts)
    (profiles / "not-a-session").mkdir()
    with pytest.raises(RuntimeError, match="unexpected_entry"):
        browser_server._cleanup_stale_runtime_roots()
    (profiles / "not-a-session").rmdir()

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "canary").write_text("do-not-follow", encoding="utf-8")
    try:
        os.symlink(outside, profiles / ("a" * 32), target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlink creation is unavailable")
    with pytest.raises(RuntimeError, match="unsafe_entry"):
        browser_server._cleanup_stale_runtime_roots()
    assert (outside / "canary").read_text(encoding="utf-8") == "do-not-follow"


@pytest.mark.asyncio
async def test_restart_readiness_waits_before_arm_and_armed_exit_is_immediate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [100.0]
    sleeps: list[float] = []

    async def advance(delay: float) -> None:
        sleeps.append(delay)
        now[0] += delay

    monkeypatch.setattr(browser_server.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(browser_server.asyncio, "sleep", advance)
    gate = browser_server.RestartPolicyReadinessGate(now[0])

    await gate.wait_before_arm()
    assert sleeps == [browser_server.DOCKER_RESTART_ARM_SECONDS]
    assert gate.remaining() == 0
    gate.arm()
    await gate.hold_early_exit()
    assert sleeps == [browser_server.DOCKER_RESTART_ARM_SECONDS]


@pytest.mark.asyncio
async def test_restart_readiness_early_exit_survives_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(browser_server, "DOCKER_RESTART_ARM_SECONDS", 0.05)
    gate = browser_server.RestartPolicyReadinessGate(time.monotonic())
    hold = asyncio.create_task(gate.hold_early_exit())
    await asyncio.sleep(0)
    hold.cancel()

    await hold

    assert gate.remaining() == 0
    assert gate.armed is False


def test_restart_readiness_precedes_browser_socket_and_egress_control_key() -> None:
    source = Path(browser_server.__file__).read_text(encoding="utf-8")
    browser_source = source[source.index("async def browser_main()") : source.index("async def egress_main()")]
    egress_source = source[source.index("async def egress_main()") : source.index("def main()")]

    assert browser_source.index("await readiness.wait_before_arm()") < browser_source.index(
        "await asyncio.start_unix_server("
    )
    assert browser_source.index("readiness.arm()") < browser_source.index(
        "await asyncio.start_unix_server("
    )
    assert egress_source.index("await readiness.wait_before_arm()") < egress_source.index(
        "_publish_control_key(control_key)"
    )
    assert egress_source.index("await server.start_serving()") < egress_source.index(
        "_publish_control_key(control_key)"
    )
    assert egress_source.index("_publish_control_key(control_key)") < egress_source.index(
        "_enforce_egress_watch_bootstrap(service)"
    )


@pytest.mark.asyncio
async def test_one_shot_control_key_requires_paired_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = tmp_path / "browser-egress.control"
    monkeypatch.setattr(browser_server, "EGRESS_CONTROL_PATH", control)
    key = "1" * 64
    control.write_text(key, encoding="ascii")
    assert await browser_server._wait_for_control_key() == key
    assert not control.exists()

    async def no_wait(_: float) -> None:
        return None

    monkeypatch.setattr(browser_server.asyncio, "sleep", no_wait)
    with pytest.raises(RuntimeError, match="browser_egress_control_unavailable"):
        await browser_server._wait_for_control_key()

    replacement = "2" * 64
    control.write_text(replacement, encoding="ascii")
    assert await browser_server._wait_for_control_key() == replacement
    old_service = browser_server.BrowserEgressService(key)
    new_service = browser_server.BrowserEgressService(replacement)
    await old_service._authenticate_control({"control_key": key})
    with pytest.raises(browser_server.SandboxEngineError, match="Egress control denied"):
        await new_service._authenticate_control({"control_key": key})


@pytest.mark.asyncio
async def test_egress_restart_closes_watcher_rejects_old_capability_and_pair_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not hasattr(socket, "AF_UNIX"):
        pytest.skip("AF_UNIX is unavailable")
    uds = tmp_path / "egress.sock"
    monkeypatch.setattr(browser_server, "EGRESS_SOCKET_PATH", uds)

    async def wait_for_watcher(service: browser_server.BrowserEgressService) -> None:
        for _ in range(100):
            if service.watchers:
                return
            await asyncio.sleep(0.01)
        raise AssertionError("watcher did not connect")

    old_key = "3" * 64
    old_service = browser_server.BrowserEgressService(old_key)
    old_server = await asyncio.start_unix_server(old_service.handle, path=str(uds))
    old_client = browser_server.BrowserEgressClient(old_key)
    await old_client.register()
    old_capability = old_client.capability
    assert old_service._grant(old_capability)
    old_ready = asyncio.Event()
    old_watch = asyncio.create_task(browser_server._watch_egress(old_key, old_ready))
    await wait_for_watcher(old_service)
    await asyncio.wait_for(old_ready.wait(), timeout=1)

    for watcher in tuple(old_service.watchers):
        await browser_server._close_writer(watcher)
    with pytest.raises(RuntimeError, match="browser_egress_restarted"):
        await asyncio.wait_for(old_watch, timeout=1)
    old_server.close()
    await old_server.wait_closed()
    uds.unlink(missing_ok=True)

    new_key = "4" * 64
    new_service = browser_server.BrowserEgressService(new_key)
    with pytest.raises(browser_server.SandboxEngineError, match="capability denied"):
        new_service._grant(old_capability)
    new_server = await asyncio.start_unix_server(new_service.handle, path=str(uds))
    new_client = browser_server.BrowserEgressClient(new_key)
    await new_client.register()
    assert new_service._grant(new_client.capability)
    new_ready = asyncio.Event()
    new_watch = asyncio.create_task(browser_server._watch_egress(new_key, new_ready))
    await wait_for_watcher(new_service)
    await asyncio.wait_for(new_ready.wait(), timeout=1)
    assert not new_watch.done()

    await new_client.revoke()
    for watcher in tuple(new_service.watchers):
        await browser_server._close_writer(watcher)
    with pytest.raises(RuntimeError, match="browser_egress_restarted"):
        await asyncio.wait_for(new_watch, timeout=1)
    new_server.close()
    await new_server.wait_closed()
    browser_server.EGRESS_CLIENTS.pop(old_capability, None)


def test_dumpability_guard_fails_closed_on_set_or_get_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeLibc:
        def __init__(self, values: list[int]) -> None:
            self.values = iter(values)

        def prctl(self, *_: object) -> int:
            return next(self.values)

    monkeypatch.setattr(browser_server.os, "name", "posix")
    monkeypatch.setattr(browser_server.ctypes, "CDLL", lambda *a, **k: FakeLibc([1]))
    with pytest.raises(RuntimeError, match="browser_pr_set_dumpable_failed"):
        browser_server._disable_process_dumping()
    monkeypatch.setattr(
        browser_server.ctypes, "CDLL", lambda *a, **k: FakeLibc([0, 1])
    )
    with pytest.raises(RuntimeError, match="browser_pr_get_dumpable_failed"):
        browser_server._disable_process_dumping()


def test_preflight_diagnostics_expose_only_reviewed_machine_codes() -> None:
    for code in browser_server.BROWSER_PREFLIGHT_ERROR_CODES:
        assert browser_server._safe_preflight_error_code(RuntimeError(code)) == code
    assert (
        browser_server._safe_preflight_error_code(asyncio.TimeoutError())
        == "browser_upstream_preflight_timeout"
    )
    assert browser_server._safe_preflight_error_code(
        RuntimeError("secret upstream stderr https://private.example/token")
    ) is None

    target_closed = {
        "result": {
            "isError": True,
            "content": [
                {
                    "type": "text",
                    "text": "Target closed https://private.example/?token=secret",
                }
            ],
        }
    }
    category = browser_server._classify_upstream_failure(target_closed)
    assert category == "target_closed"
    code = f"browser_upstream_representative_{category}"
    assert code in browser_server.BROWSER_PREFLIGHT_ERROR_CODES
    assert "private" not in code and "token" not in code
    assert browser_server._classify_upstream_failure(
        {"result": {"isError": True, "content": [{"type": "text", "text": "opaque secret"}]}}
    ) == "unclassified"


@pytest.mark.asyncio
async def test_stderr_classifier_streams_fixed_enum_without_retaining_text() -> None:
    classifier = browser_server.SafeStderrClassifier()
    stream = asyncio.StreamReader()
    stream.feed_data(
        b"x" * 20_000
        + b" https://private.example/?token=secret BAD SYS"
    )
    stream.feed_data(b"TEM CALL bearer-value")
    stream.feed_eof()

    await browser_server._drain_stderr(stream, classifier)

    assert classifier.primary() == "seccomp_denied"
    assert classifier.categories == {"seccomp_denied"}
    assert len(classifier._overlap) <= classifier._overlap_bytes
    assert b"private.example" not in classifier._overlap
    code = (
        "browser_upstream_representative_target_closed_"
        f"{classifier.primary()}"
    )
    assert code in browser_server.BROWSER_PREFLIGHT_ERROR_CODES
    assert "token" not in code and "bearer" not in code


def test_kernel_peer_credentials_distinguish_catalog_and_sandbox_uid() -> None:
    class CredSocket:
        def __init__(self, uid: int) -> None:
            self.uid = uid

        def getsockopt(self, level: int, option: int, size: int) -> bytes:
            assert (level, option, size) == (
                socket.SOL_SOCKET,
                socket.SO_PEERCRED,
                browser_server.struct.calcsize("3i"),
            )
            return browser_server.struct.pack("3i", 123, self.uid, self.uid)

    class PeerWriter:
        def __init__(self, uid: int) -> None:
            self.peer = CredSocket(uid)

        def get_extra_info(self, name: str) -> object:
            assert name == "socket"
            return self.peer

    assert browser_server._trusted_peer_uid(PeerWriter(0)) == 0  # type: ignore[arg-type]
    assert browser_server._trusted_peer_uid(PeerWriter(65532)) == 65532  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_one_shot_lifecycle_rejects_successor_without_queueing() -> None:
    lifecycle = browser_server.BrowserOneShotLifecycle()
    lifecycle.claim()
    assert lifecycle.claimed is True
    with pytest.raises(browser_server.SandboxEngineError) as exc_info:
        lifecycle.claim()
    assert exc_info.value.code == "browser_container_session_consumed"
    assert lifecycle.finished.is_set() is False
    lifecycle.finish()
    await asyncio.wait_for(lifecycle.finished.wait(), timeout=0.1)


@pytest.mark.asyncio
async def test_untrusted_peer_is_rejected_before_request_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unexpected_read(*_: object, **__: object) -> dict[str, Any]:
        raise AssertionError("untrusted peer must be rejected before reading")

    monkeypatch.setattr(browser_server, "TRUSTED_CLIENT_UID", 0)
    monkeypatch.setattr(browser_server, "_trusted_peer_uid", lambda writer: 12345)
    monkeypatch.setattr(browser_server, "_read_json_line", unexpected_read)
    lifecycle = browser_server.BrowserOneShotLifecycle()
    writer = MemoryWriter()
    await browser_server.handle_browser_client(  # type: ignore[arg-type]
        asyncio.StreamReader(), writer, "4" * 64, lifecycle
    )
    assert json.loads(bytes(writer.data))["code"] == "browser_peer_denied"
    assert lifecycle.claimed is False


@pytest.mark.asyncio
async def test_second_trusted_session_is_rejected_while_first_is_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def held_session(*_: object) -> None:
        started.set()
        await release.wait()

    monkeypatch.setattr(browser_server, "TRUSTED_CLIENT_UID", 0)
    monkeypatch.setattr(browser_server, "_trusted_peer_uid", lambda writer: 0)
    monkeypatch.setattr(browser_server, "_browser_stdio", held_session)
    lifecycle = browser_server.BrowserOneShotLifecycle()

    def request_reader() -> asyncio.StreamReader:
        reader = asyncio.StreamReader()
        reader.feed_data(b'{"action":"mcp_stdio","adapter_id":"test","configuration":{}}\n')
        reader.feed_eof()
        return reader

    first_writer = MemoryWriter()
    first = asyncio.create_task(
        browser_server.handle_browser_client(  # type: ignore[arg-type]
            request_reader(), first_writer, "5" * 64, lifecycle
        )
    )
    await asyncio.wait_for(started.wait(), timeout=0.2)
    second_writer = MemoryWriter()
    await asyncio.wait_for(
        browser_server.handle_browser_client(  # type: ignore[arg-type]
            request_reader(), second_writer, "5" * 64, lifecycle
        ),
        timeout=0.2,
    )
    assert json.loads(bytes(second_writer.data))["code"] == "browser_container_session_consumed"
    assert lifecycle.finished.is_set() is False
    release.set()
    await asyncio.wait_for(first, timeout=0.2)
    assert lifecycle.finished.is_set() is True


@pytest.mark.asyncio
async def test_claimed_session_exception_still_finishes_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failed_session(*_: object) -> None:
        raise RuntimeError("simulated cleanup failure")

    monkeypatch.setattr(browser_server, "TRUSTED_CLIENT_UID", 0)
    monkeypatch.setattr(browser_server, "_trusted_peer_uid", lambda writer: 0)
    monkeypatch.setattr(browser_server, "_browser_stdio", failed_session)
    lifecycle = browser_server.BrowserOneShotLifecycle()
    reader = asyncio.StreamReader()
    reader.feed_data(b'{"action":"mcp_stdio","adapter_id":"test","configuration":{}}\n')
    reader.feed_eof()
    writer = MemoryWriter()
    await browser_server.handle_browser_client(  # type: ignore[arg-type]
        reader, writer, "5" * 64, lifecycle
    )
    assert json.loads(bytes(writer.data))["code"] == "browser_sidecar_internal_error"
    assert lifecycle.finished.is_set() is True


def test_browser_supervisor_requires_pid1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(browser_server.sys, "platform", "linux")
    monkeypatch.setattr(browser_server.os, "getpid", lambda: 2)
    with pytest.raises(RuntimeError, match="browser_supervisor_must_be_pid1"):
        browser_server._require_supervisor_pid1()
    monkeypatch.setattr(browser_server.os, "getpid", lambda: 1)
    browser_server._require_supervisor_pid1()


@pytest.mark.asyncio
async def test_egress_watch_bootstrap_timeout_shuts_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = browser_server.BrowserEgressService("7" * 64)
    monkeypatch.setattr(browser_server, "EGRESS_WATCH_BOOTSTRAP_SECONDS", 0.01)
    await browser_server._enforce_egress_watch_bootstrap(service)
    assert service.shutdown_event.is_set() is True
    assert service._shutting_down is True


@pytest.mark.asyncio
async def test_mcp_stdio_rejects_sandbox_peer_before_handshake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(browser_server, "TRUSTED_CLIENT_UID", 0)
    for uid, expected in ((65532, "browser_peer_denied"), (0, "mcp_adapter_denied")):
        monkeypatch.setattr(browser_server, "_trusted_peer_uid", lambda writer, value=uid: value)
        reader = asyncio.StreamReader()
        reader.feed_data(
            b'{"action":"mcp_stdio","adapter_id":"not-allowed","configuration":{}}\n'
        )
        reader.feed_eof()
        writer = MemoryWriter()
        await browser_server.handle_browser_client(  # type: ignore[arg-type]
            reader,
            writer,
            "5" * 64,
            browser_server.BrowserOneShotLifecycle(),
        )
        response = json.loads(bytes(writer.data).decode("utf-8"))
        assert response["code"] == expected


@pytest.mark.asyncio
async def test_tunnel_deadline_revokes_capability() -> None:
    reader = asyncio.StreamReader()
    writer = MemoryWriter()

    class Service:
        revoked = ""

        async def consume(self, capability: str, amount: int) -> None:
            raise AssertionError("expired tunnel must not transfer bytes")

        async def revoke(self, capability: str, reason: str) -> None:
            self.revoked = reason

    service = Service()
    with pytest.raises(BrowserPolicyError, match="browser_egress_tunnel_deadline"):
        await browser_server._relay_limited(  # type: ignore[arg-type]
            reader,
            writer,
            service,
            "f" * 64,
            asyncio.get_running_loop().time() - 1,
        )
    assert service.revoked == "browser_egress_tunnel_deadline"


@pytest.mark.asyncio
async def test_idle_tunnel_closes_without_revoking_session_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(browser_server, "EGRESS_TUNNEL_IDLE_SECONDS", 0.01)
    reader = asyncio.StreamReader()
    writer = MemoryWriter()

    class Service:
        revoked = ""

        async def consume(self, capability: str, amount: int) -> None:
            raise AssertionError("idle tunnel must not transfer bytes")

        async def revoke(self, capability: str, reason: str) -> None:
            self.revoked = reason

    service = Service()
    with pytest.raises(BrowserPolicyError, match="browser_egress_tunnel_idle"):
        await browser_server._relay_limited(  # type: ignore[arg-type]
            reader,
            writer,
            service,
            "f" * 64,
            asyncio.get_running_loop().time() + 1,
        )
    assert service.revoked == ""


@pytest.mark.asyncio
async def test_process_group_cleanup_uses_term_then_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signals: list[int] = []

    class Process:
        pid = 4242
        returncode = None

        def __init__(self) -> None:
            self.exited = asyncio.Event()

        async def wait(self) -> int:
            await self.exited.wait()
            return int(self.returncode or 0)

    process = Process()
    monkeypatch.setattr(browser_server.os, "name", "posix")
    def killpg(pid: int, sig: int) -> None:
        signals.append(sig)
        if sig == browser_server.signal.SIGKILL:
            process.returncode = -int(sig)
            process.exited.set()

    monkeypatch.setattr(browser_server.os, "killpg", killpg)
    original_wait_for = browser_server.asyncio.wait_for

    async def quick_wait_for(awaitable: Any, timeout: float) -> Any:
        if timeout == 3:
            awaitable.close()
            raise asyncio.TimeoutError
        return await original_wait_for(awaitable, timeout)

    monkeypatch.setattr(browser_server.asyncio, "wait_for", quick_wait_for)
    await browser_server._terminate_process_group(process)  # type: ignore[arg-type]
    assert signals == [browser_server.signal.SIGTERM, browser_server.signal.SIGKILL]


@pytest.mark.asyncio
async def test_status_polling_does_not_extend_idle_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Process:
        returncode = 0

    class Egress:
        revocation_reason = ""

    class Proxy:
        egress = Egress()

    state = browser_server.BrowserSessionState("chrome-devtools-mcp", "9" * 32)
    state.last_activity = 100.0
    gateway = browser_server.BrowserGatewaySession(
        "chrome-devtools-mcp",
        state,
        Process(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        Proxy(),  # type: ignore[arg-type]
        tmp_path,
        tmp_path,
        tmp_path,
    )
    monkeypatch.setattr(browser_server.time, "monotonic", lambda: 120.0)
    await gateway.status()
    await gateway.status()
    assert state.last_activity == 100.0
    monkeypatch.setattr(
        browser_server.time,
        "monotonic",
        lambda: 100.0 + browser_server.IDLE_TTL_SECONDS,
    )
    with pytest.raises(BrowserPolicyError, match="browser_session_idle_expired"):
        await gateway.status()


def test_png_registration_rejects_hardlink_and_symlink(
    tmp_path: Path,
) -> None:
    staging = tmp_path / "staging"
    registered = tmp_path / "registered"
    staging.mkdir()
    registered.mkdir()
    artifact = staging / f".modelmirror-browser_{'a' * 32}.png"
    artifact.write_bytes(browser_server.PNG_MAGIC + b"payload")
    hardlink = tmp_path / "hardlink.png"
    os.link(artifact, hardlink)
    with pytest.raises(BrowserPolicyError, match="browser_artifact_metadata_denied"):
        browser_server._register_png_artifact(artifact, registered)
    hardlink.unlink()
    artifact.unlink()

    target = tmp_path / "target.png"
    target.write_bytes(browser_server.PNG_MAGIC + b"outside")
    try:
        os.symlink(target, artifact)
    except (OSError, NotImplementedError):
        pytest.skip("file symlink creation is unavailable")
    with pytest.raises(BrowserPolicyError, match="browser_artifact_metadata_denied"):
        browser_server._register_png_artifact(artifact, registered)
    assert target.read_bytes().endswith(b"outside")


def test_png_registration_rejects_path_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging = tmp_path / "staging"
    registered = tmp_path / "registered"
    staging.mkdir()
    registered.mkdir()
    artifact = staging / f".modelmirror-browser_{'b' * 32}.png"
    displaced = staging / "displaced.png"
    artifact.write_bytes(browser_server.PNG_MAGIC + b"first")
    original_read = browser_server.os.read
    swapped = False

    def swapping_read(descriptor: int, amount: int) -> bytes:
        nonlocal swapped
        chunk = original_read(descriptor, amount)
        if not swapped:
            swapped = True
            artifact.replace(displaced)
            artifact.write_bytes(browser_server.PNG_MAGIC + b"replacement")
        return chunk

    monkeypatch.setattr(browser_server.os, "read", swapping_read)
    with pytest.raises(BrowserPolicyError, match="browser_artifact_race_denied"):
        browser_server._register_png_artifact(artifact, registered)
    assert not list(registered.iterdir())


def test_png_registration_copies_away_from_retained_source_write_fd(
    tmp_path: Path,
) -> None:
    staging = tmp_path / "staging"
    registered = tmp_path / "registered"
    staging.mkdir()
    registered.mkdir()
    artifact = staging / f".modelmirror-browser_{'c' * 32}.png"
    original = browser_server.PNG_MAGIC + b"trusted-payload"
    artifact.write_bytes(original)
    attacker_descriptor = os.open(
        artifact,
        os.O_WRONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
    )
    source_inode = os.fstat(attacker_descriptor).st_ino
    try:
        registered_path, digest, size = browser_server._register_png_artifact(
            artifact, registered
        )
        assert not artifact.exists()
        assert registered_path.stat().st_ino != source_inode
        assert registered_path.read_bytes() == original
        assert digest == hashlib.sha256(original).hexdigest()
        assert size == len(original)

        os.lseek(attacker_descriptor, 0, os.SEEK_SET)
        os.write(attacker_descriptor, b"attacker-controlled")
        os.ftruncate(attacker_descriptor, 3)
        os.fsync(attacker_descriptor)

        assert registered_path.read_bytes() == original
        assert hashlib.sha256(registered_path.read_bytes()).hexdigest() == digest
        assert registered_path.stat().st_size == size
    finally:
        os.close(attacker_descriptor)


@pytest.mark.asyncio
async def test_artifact_timeout_taints_session_and_late_file_cannot_recover(
    tmp_path: Path,
) -> None:
    staging = tmp_path / "staging"
    registered = tmp_path / "registered"
    staging.mkdir()
    registered.mkdir()

    class Process:
        returncode = None

    class Rpc:
        async def request(self, *args: object, **kwargs: object) -> dict[str, object]:
            raise asyncio.TimeoutError

    class Egress:
        revocation_reason = ""

    class Proxy:
        egress = Egress()
        last_violation = ""

    state = browser_server.BrowserSessionState("chrome-devtools-mcp", "8" * 32)
    gateway = browser_server.BrowserGatewaySession(
        "chrome-devtools-mcp",
        state,
        Process(),  # type: ignore[arg-type]
        Rpc(),  # type: ignore[arg-type]
        Proxy(),  # type: ignore[arg-type]
        tmp_path,
        staging,
        registered,
    )

    async def taint_without_process(self: Any, reason: str) -> None:
        self.state.taint(reason)

    gateway._taint = MethodType(taint_without_process, gateway)  # type: ignore[method-assign]
    with pytest.raises(BrowserPolicyError, match="browser_artifact_outcome_unknown"):
        await gateway.call("take_screenshot", {})
    assert state.tainted is True
    assert state.taint_reason == "browser_artifact_outcome_unknown"

    # A late browser write cannot make a tainted session look healthy again.
    (staging / "late.png").write_bytes(browser_server.PNG_MAGIC + b"late")
    with pytest.raises(BrowserPolicyError, match="browser_artifact_outcome_unknown"):
        await gateway.call("browser_session_status", {})
    assert state.tainted is True
    assert gateway._purge_unregistered_artifacts() is True
    assert not (staging / "late.png").exists()


@pytest.mark.asyncio
async def test_snapshot_login_redirect_immediately_taints_and_stops_session(
    tmp_path: Path,
) -> None:
    staging = tmp_path / "staging"
    registered = tmp_path / "registered"
    staging.mkdir()
    registered.mkdir()

    class Process:
        returncode = None

    class Rpc:
        async def request(self, *args: object, **kwargs: object) -> dict[str, object]:
            return {
                "jsonrpc": "2.0",
                "id": "internal",
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": 'uid=1_0 RootWebArea "Account" url="https://example.com/accounts"',
                        }
                    ]
                },
            }

    class Egress:
        revocation_reason = ""

    class Proxy:
        egress = Egress()
        last_violation = ""

    state = browser_server.BrowserSessionState("chrome-devtools-mcp", "6" * 32)
    state.current_origin = "https://example.com"
    gateway = browser_server.BrowserGatewaySession(
        "chrome-devtools-mcp",
        state,
        Process(),  # type: ignore[arg-type]
        Rpc(),  # type: ignore[arg-type]
        Proxy(),  # type: ignore[arg-type]
        tmp_path,
        staging,
        registered,
    )

    async def taint_without_process(self: Any, reason: str) -> None:
        self.state.taint(reason)

    gateway._taint = MethodType(taint_without_process, gateway)  # type: ignore[method-assign]
    with pytest.raises(BrowserPolicyError, match="browser_external_login_denied"):
        await gateway.call("take_snapshot", {})
    assert state.tainted is True
    assert state.taint_reason == "browser_external_login_denied"
