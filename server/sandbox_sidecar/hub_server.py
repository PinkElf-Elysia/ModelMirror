"""Isolated Streamable HTTP MCP bridge and exact-host egress.

The ``remote`` process has no network interface.  It runs the official MCP
Python SDK and reaches one server-owned target through the separate ``egress``
process.  The egress process accepts target authorization only from UID 0
(the backend container) and gives the remote process an exact-host capability.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import re
import secrets
import socket
import stat
import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx


REMOTE_SOCKET = Path(os.getenv("MCP_HUB_REMOTE_SOCKET_PATH", "/run/modelmirror-hub-mcp/hub-mcp.sock"))
EGRESS_SOCKET = Path(os.getenv("MCP_HUB_EGRESS_SOCKET_PATH", "/run/modelmirror-hub-egress/hub-egress.sock"))
MAX_REQUEST_BYTES = 40 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
MAX_TOOL_COUNT = 50
MAX_SCHEMA_BYTES = 32 * 1024
MAX_TOTAL_SCHEMA_BYTES = 256 * 1024
MAX_SESSIONS = 2
MAX_ACTIONS = 50
SESSION_IDLE_SECONDS = 5 * 60
SESSION_TTL_SECONDS = 15 * 60
CALL_TIMEOUT_SECONDS = 20.0
TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
DNS_TIMEOUT_SECONDS = 5.0
CONNECT_TIMEOUT_SECONDS = 10.0
TUNNEL_IDLE_SECONDS = 60.0
TUNNEL_TTL_SECONDS = 15 * 60
MAX_TUNNELS_PER_SESSION = 8
MAX_BYTES_PER_SESSION = 8 * 1024 * 1024
SESSION_ID_RE = re.compile(r"^hubsession_[0-9a-f]{32}$")
CANDIDATE_ID_RE = re.compile(r"^mcphub_[0-9a-f]{32}$")
CAPABILITY_RE = re.compile(r"^[0-9a-f]{64}$")
SESSION_OWNER_RE = re.compile(r"^hub:[A-Za-z0-9._%~-]{1,240}:[A-Za-z0-9._%~-]{1,240}:mcphub_[0-9a-f]{32}$")
AUTH_BINDING_RE = re.compile(r"^mcpra_[0-9a-f]{32}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
HEADER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,63}$")
DENIED_AUTH_HEADERS = frozenset(
    {
        "accept",
        "accept-encoding",
        "connection",
        "content-encoding",
        "content-length",
        "content-type",
        "cookie",
        "expect",
        "forwarded",
        "from",
        "host",
        "mcp-protocol-version",
        "origin",
        "proxy-authorization",
        "proxy-connection",
        "referer",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "user-agent",
        "via",
        "x-http-method-override",
    }
)
DENIED_AUTH_HEADER_PREFIXES = (
    "proxy-",
    "sec-",
    "x-forwarded-",
    "x-original-",
    "x-rewrite-",
)
SAFE_PREFLIGHT_RETRY_CODES = frozenset(
    {
        "hub_upstream_connect_failed",
        "hub_upstream_rate_limited",
        "hub_upstream_timeout",
        "hub_upstream_transport_failed",
        "hub_upstream_unavailable",
    }
)


class HubSidecarError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fixed_preflight_error(error: BaseException, *, authenticated: bool = False) -> str:
    """Reduce SDK/HTTP failures to bounded codes without retaining content."""

    pending: list[BaseException] = [error]
    seen: set[int] = set()
    flattened: list[BaseException] = []
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        flattened.append(current)
        nested = getattr(current, "exceptions", None)
        if isinstance(nested, tuple):
            pending.extend(item for item in nested if isinstance(item, BaseException))
        cause = current.__cause__
        context = current.__context__
        if isinstance(cause, BaseException):
            pending.append(cause)
        if isinstance(context, BaseException):
            pending.append(context)

    for current in flattened:
        if isinstance(current, HubSidecarError):
            return current.code
    for current in flattened:
        if not isinstance(current, httpx.HTTPStatusError):
            continue
        status = int(current.response.status_code)
        if status == 401 and authenticated:
            return "mcp_remote_auth_unauthorized"
        if status == 403 and authenticated:
            return "mcp_remote_auth_forbidden"
        if status in {401, 403}:
            return "hub_upstream_auth_required"
        if status == 404:
            return "hub_upstream_endpoint_not_found"
        if status == 405:
            return "hub_upstream_method_denied"
        if status == 429:
            return "hub_upstream_rate_limited"
        if 300 <= status < 400:
            return "hub_upstream_redirect_denied"
        if status in {400, 406, 415, 422}:
            return "hub_upstream_protocol_rejected"
        if 500 <= status < 600:
            return "hub_upstream_unavailable"
        return "hub_upstream_http_failed"
    if any(
        isinstance(current, (asyncio.TimeoutError, httpx.TimeoutException))
        for current in flattened
    ):
        return "hub_upstream_timeout"
    if any(isinstance(current, httpx.ConnectError) for current in flattened):
        return "hub_upstream_connect_failed"
    if any(isinstance(current, httpx.TransportError) for current in flattened):
        return "hub_upstream_transport_failed"
    return "hub_upstream_preflight_failed"


def _load_mcp_http() -> tuple[Any, Any]:
    """Load the official SDK only in the isolated remote execution path."""

    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    return ClientSession, streamable_http_client


async def _discard_untrusted_server_message(_message: Any) -> None:
    """Drop non-tool notifications without retaining remote content."""

    await asyncio.sleep(0)


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _normalize_target(value: Any) -> tuple[str, str]:
    raw = str(value or "").strip()
    if not raw or len(raw) > 4096 or any(token in raw for token in ("{", "}")):
        raise HubSidecarError("hub_target_invalid")
    parsed = urlsplit(raw)
    if (
        parsed.scheme.lower() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise HubSidecarError("hub_target_invalid")
    host = str(parsed.hostname or "").strip().rstrip(".").lower()
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise HubSidecarError("hub_target_invalid") from exc
    if not host or host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".home.arpa")):
        raise HubSidecarError("hub_target_invalid")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise HubSidecarError("hub_target_ip_literal_denied")
    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise HubSidecarError("hub_target_invalid") from exc
    if port != 443:
        raise HubSidecarError("hub_target_port_denied")
    return f"https://{host}{parsed.path or '/'}", host


def _validated_auth_envelope(
    value: Any,
    *,
    candidate_id: str,
    normalized_url: str,
) -> tuple[str, str, str, int, str] | None:
    if value is None:
        return None
    if isinstance(value, dict) and value.get("auth_mode") == "oauth_authorization_code_pkce":
        required = {
            "auth_mode",
            "header_value",
            "origin",
            "policy_fingerprint",
            "protocol_version",
            "resource_digest",
            "scope_digest",
            "target_id",
            "token_revision_digest",
        }
        if set(value) != required or value.get("target_id") != candidate_id:
            raise HubSidecarError("mcp_remote_oauth_scope_denied")
        normalized_origin = f"https://{urlsplit(normalized_url).hostname}"
        digests = (
            str(value.get("policy_fingerprint") or ""),
            str(value.get("resource_digest") or ""),
            str(value.get("scope_digest") or ""),
            str(value.get("token_revision_digest") or ""),
        )
        header_value = str(value.get("header_value") or "")
        expected_resource_digest = hashlib.sha256(
            normalized_url.encode("utf-8")
        ).hexdigest()
        if (
            str(value.get("origin") or "") != normalized_origin
            or str(value.get("protocol_version") or "") != "2025-11-25"
            or any(HEX64_RE.fullmatch(item) is None for item in digests)
            or digests[1] != expected_resource_digest
            or not header_value.startswith("Bearer ")
            or not header_value[7:]
            or len(header_value) > 20_007
            or any(
                ord(character) < 0x20 or ord(character) == 0x7F
                for character in header_value
            )
        ):
            raise HubSidecarError("mcp_remote_oauth_policy_ineligible")
        context_digest = hashlib.sha256(
            ":".join(digests).encode("ascii")
        ).hexdigest()
        return "Authorization", header_value, digests[0], 0, context_digest
    if not isinstance(value, dict) or set(value) != {
        "binding_id",
        "binding_revision",
        "header_name",
        "header_value",
        "origin",
        "policy_fingerprint",
        "target_id",
    }:
        raise HubSidecarError("mcp_remote_auth_policy_ineligible")
    if value.get("target_id") != candidate_id:
        raise HubSidecarError("mcp_remote_auth_scope_denied")
    binding_id = str(value.get("binding_id") or "")
    policy_fingerprint = str(value.get("policy_fingerprint") or "")
    revision = value.get("binding_revision")
    if (
        AUTH_BINDING_RE.fullmatch(binding_id) is None
        or HEX64_RE.fullmatch(policy_fingerprint) is None
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 1
    ):
        raise HubSidecarError("mcp_remote_auth_policy_ineligible")
    parsed = urlsplit(normalized_url)
    expected_origin = f"https://{parsed.hostname}"
    if str(value.get("origin") or "") != expected_origin:
        raise HubSidecarError("mcp_remote_auth_scope_denied")
    header_name = str(value.get("header_name") or "").strip()
    header_value = str(value.get("header_value") or "")
    lower_name = header_name.lower()
    if (
        HEADER_RE.fullmatch(header_name) is None
        or lower_name in DENIED_AUTH_HEADERS
        or lower_name.startswith(DENIED_AUTH_HEADER_PREFIXES)
        or not header_value
        or len(header_value) > 20_007
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in header_value)
    ):
        raise HubSidecarError("mcp_remote_auth_policy_ineligible")
    if lower_name == "authorization":
        if not header_value.startswith("Bearer ") or not header_value[7:]:
            raise HubSidecarError("mcp_remote_auth_policy_ineligible")
        header_name = "Authorization"
    else:
        header_name = lower_name
    context_digest = hashlib.sha256(
        f"{policy_fingerprint}:{revision}".encode("ascii")
    ).hexdigest()
    return header_name, header_value, policy_fingerprint, revision, context_digest


def _peer_uid(writer: asyncio.StreamWriter) -> int:
    if not sys.platform.startswith("linux") or not hasattr(socket, "SO_PEERCRED"):
        raise HubSidecarError("hub_peer_credentials_unavailable")
    sock = writer.get_extra_info("socket")
    if sock is None:
        raise HubSidecarError("hub_peer_credentials_unavailable")
    try:
        raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        _pid, uid, _gid = struct.unpack("3i", raw)
    except (OSError, struct.error) as exc:
        raise HubSidecarError("hub_peer_credentials_unavailable") from exc
    return int(uid)


async def _read_request(reader: asyncio.StreamReader) -> dict[str, Any]:
    try:
        raw = await asyncio.wait_for(reader.readline(), timeout=5)
    except asyncio.TimeoutError as exc:
        raise HubSidecarError("hub_request_timeout") from exc
    if not raw or len(raw) > MAX_REQUEST_BYTES:
        raise HubSidecarError("hub_request_invalid")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise HubSidecarError("hub_request_invalid") from exc
    if not isinstance(value, dict):
        raise HubSidecarError("hub_request_invalid")
    return value


async def _write_response(writer: asyncio.StreamWriter, value: dict[str, Any]) -> None:
    encoded = _json_bytes(value) + b"\n"
    if len(encoded) > MAX_RESPONSE_BYTES + 4096:
        encoded = b'{"ok":false,"code":"hub_response_too_large"}\n'
    try:
        writer.write(encoded)
        await asyncio.wait_for(writer.drain(), timeout=2)
    except (asyncio.TimeoutError, ConnectionError, OSError):
        pass


async def _close_writer(writer: asyncio.StreamWriter) -> None:
    writer.close()
    try:
        await asyncio.wait_for(writer.wait_closed(), timeout=1)
    except (asyncio.TimeoutError, ConnectionError, OSError):
        pass


def _validate_dns_records(records: list[tuple[Any, ...]]) -> tuple[str, ...]:
    addresses = tuple(dict.fromkeys(str(record[4][0]) for record in records))
    if not addresses:
        raise HubSidecarError("hub_dns_failed")
    for value in addresses:
        try:
            address = ipaddress.ip_address(value)
        except ValueError as exc:
            raise HubSidecarError("hub_dns_answer_invalid") from exc
        if not address.is_global:
            raise HubSidecarError("hub_dns_private_or_synthetic_denied")
    return addresses


async def _resolve_public(host: str) -> tuple[str, ...]:
    try:
        records = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(
                host,
                443,
                type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP,
            ),
            timeout=DNS_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        raise HubSidecarError("hub_dns_timeout") from exc
    except socket.gaierror as exc:
        raise HubSidecarError("hub_dns_failed") from exc
    return _validate_dns_records(records)


def _client_hello_sni(data: bytes) -> str:
    hello = memoryview(data)
    if len(hello) < 42 or hello[0] != 1:
        raise HubSidecarError("hub_tls_client_hello_invalid")
    handshake_length = int.from_bytes(hello[1:4], "big")
    if handshake_length + 4 > len(hello):
        raise HubSidecarError("hub_tls_client_hello_invalid")
    offset = 4 + 2 + 32
    if offset >= len(hello):
        raise HubSidecarError("hub_tls_client_hello_invalid")
    session_length = int(hello[offset])
    offset += 1 + session_length
    if offset + 2 > len(hello):
        raise HubSidecarError("hub_tls_client_hello_invalid")
    cipher_length = int.from_bytes(hello[offset : offset + 2], "big")
    offset += 2 + cipher_length
    if offset >= len(hello):
        raise HubSidecarError("hub_tls_client_hello_invalid")
    compression_length = int(hello[offset])
    offset += 1 + compression_length
    if offset + 2 > len(hello):
        raise HubSidecarError("hub_tls_client_hello_invalid")
    extensions_length = int.from_bytes(hello[offset : offset + 2], "big")
    offset += 2
    end = offset + extensions_length
    if end > len(hello):
        raise HubSidecarError("hub_tls_client_hello_invalid")
    found_sni: str | None = None
    seen_sni = False
    while offset + 4 <= end:
        extension_type = int.from_bytes(hello[offset : offset + 2], "big")
        extension_length = int.from_bytes(hello[offset + 2 : offset + 4], "big")
        offset += 4
        extension_end = offset + extension_length
        if extension_end > end:
            raise HubSidecarError("hub_tls_client_hello_invalid")
        if extension_type == 0xFE0D:
            raise HubSidecarError("hub_tls_ech_denied")
        if extension_type == 0:
            if seen_sni or extension_length < 5:
                raise HubSidecarError("hub_tls_sni_denied")
            seen_sni = True
            names_length = int.from_bytes(hello[offset : offset + 2], "big")
            cursor = offset + 2
            names_end = cursor + names_length
            if names_end != extension_end:
                raise HubSidecarError("hub_tls_sni_denied")
            while cursor + 3 <= names_end:
                name_type = int(hello[cursor])
                name_length = int.from_bytes(hello[cursor + 1 : cursor + 3], "big")
                cursor += 3
                raw_name = bytes(hello[cursor : cursor + name_length])
                cursor += name_length
                if name_type == 0:
                    if found_sni is not None:
                        raise HubSidecarError("hub_tls_sni_denied")
                    try:
                        found_sni = raw_name.decode("ascii").lower().rstrip(".")
                    except UnicodeError as exc:
                        raise HubSidecarError("hub_tls_sni_denied") from exc
            if cursor != names_end:
                raise HubSidecarError("hub_tls_sni_denied")
        offset = extension_end
    if offset != end or found_sni is None:
        raise HubSidecarError("hub_tls_sni_denied")
    return found_sni


@dataclass(slots=True)
class EgressGrant:
    candidate_id: str
    url: str
    host: str
    pinned_addresses: tuple[str, ...]
    expires_at: float
    active_tunnels: int = 0
    transferred_bytes: int = 0
    writers: set[asyncio.StreamWriter] = field(default_factory=set)


class HubEgressService:
    def __init__(self) -> None:
        self.grants: dict[str, EgressGrant] = {}
        self.lock = asyncio.Lock()

    async def authorize(self, candidate_id: str, url: str) -> str:
        normalized, host = _normalize_target(url)
        addresses = await _resolve_public(host)
        async with self.lock:
            self._purge()
            if len(self.grants) >= MAX_SESSIONS:
                raise HubSidecarError("hub_session_limit")
            capability = secrets.token_hex(32)
            self.grants[capability] = EgressGrant(
                candidate_id=candidate_id,
                url=normalized,
                host=host,
                pinned_addresses=addresses,
                expires_at=time.monotonic() + SESSION_TTL_SECONDS,
            )
            return capability

    async def revoke(self, capability: str) -> None:
        async with self.lock:
            grant = self.grants.pop(capability, None)
        if grant:
            for writer in tuple(grant.writers):
                await _close_writer(writer)

    async def reset(self) -> None:
        async with self.lock:
            grants = list(self.grants.values())
            self.grants.clear()
        for grant in grants:
            for writer in tuple(grant.writers):
                await _close_writer(writer)

    def _purge(self) -> None:
        now = time.monotonic()
        for capability in [key for key, value in self.grants.items() if value.expires_at <= now]:
            self.grants.pop(capability, None)

    def _grant(self, capability: str) -> EgressGrant:
        self._purge()
        grant = self.grants.get(capability)
        if grant is None:
            raise HubSidecarError("hub_egress_capability_denied")
        return grant

    async def consume(self, capability: str, amount: int) -> None:
        async with self.lock:
            grant = self._grant(capability)
            if amount < 0 or grant.transferred_bytes + amount > MAX_BYTES_PER_SESSION:
                self.grants.pop(capability, None)
                raise HubSidecarError("hub_egress_byte_budget")
            grant.transferred_bytes += amount

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        tunnel = False
        remote_writer: asyncio.StreamWriter | None = None
        capability = ""
        try:
            request = await _read_request(reader)
            action = str(request.get("action") or "")
            uid = _peer_uid(writer)
            if action == "health":
                await _write_response(writer, {"ok": True, "protocol": "modelmirror-mcp-hub-egress-v1", "active_grants": len(self.grants)})
            elif action == "authorize":
                if uid != 0:
                    raise HubSidecarError("hub_egress_control_denied")
                candidate_id = str(request.get("candidate_id") or "")
                if CANDIDATE_ID_RE.fullmatch(candidate_id) is None:
                    raise HubSidecarError("hub_candidate_invalid")
                capability = await self.authorize(candidate_id, str(request.get("url") or ""))
                await _write_response(writer, {"ok": True, "protocol": "modelmirror-mcp-hub-egress-v1", "capability": capability})
            elif action == "revoke":
                if uid != 0:
                    raise HubSidecarError("hub_egress_control_denied")
                capability = str(request.get("capability") or "")
                await self.revoke(capability)
                await _write_response(writer, {"ok": True, "protocol": "modelmirror-mcp-hub-egress-v1"})
            elif action == "reset":
                if uid != 0:
                    raise HubSidecarError("hub_egress_control_denied")
                await self.reset()
                await _write_response(writer, {"ok": True, "protocol": "modelmirror-mcp-hub-egress-v1"})
            elif action == "tunnel":
                capability = str(request.get("capability") or "")
                if uid != 65532 or CAPABILITY_RE.fullmatch(capability) is None:
                    raise HubSidecarError("hub_egress_capability_denied")
                host = str(request.get("host") or "").strip().lower()
                async with self.lock:
                    grant = self._grant(capability)
                    if host != grant.host or int(request.get("port") or 0) != 443:
                        raise HubSidecarError("hub_egress_target_mismatch")
                    if grant.active_tunnels >= MAX_TUNNELS_PER_SESSION:
                        raise HubSidecarError("hub_egress_tunnel_limit")
                    grant.active_tunnels += 1
                    grant.writers.add(writer)
                addresses = await _resolve_public(host)
                async with self.lock:
                    current = self._grant(capability)
                    if set(addresses) != set(current.pinned_addresses):
                        self.grants.pop(capability, None)
                        raise HubSidecarError("hub_dns_rebinding_denied")
                remote_reader, remote_writer = await asyncio.wait_for(
                    asyncio.open_connection(current.pinned_addresses[0], 443),
                    timeout=CONNECT_TIMEOUT_SECONDS,
                )
                async with self.lock:
                    current = self._grant(capability)
                    if current.host != host:
                        raise HubSidecarError("hub_egress_target_mismatch")
                    current.writers.add(remote_writer)
                writer.write(b'{"ok":true,"protocol":"modelmirror-mcp-hub-egress-v1"}\n')
                await asyncio.wait_for(writer.drain(), timeout=2)
                tunnel = True
                deadline = time.monotonic() + TUNNEL_TTL_SECONDS
                tasks = [
                    asyncio.create_task(self._relay_client_tls(reader, remote_writer, capability, host, deadline)),
                    asyncio.create_task(self._relay(remote_reader, writer, capability, deadline)),
                ]
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                results = await asyncio.gather(*done, *pending, return_exceptions=True)
                for result in results:
                    if isinstance(result, HubSidecarError):
                        raise result
            else:
                raise HubSidecarError("hub_action_denied")
        except HubSidecarError as exc:
            if not tunnel:
                await _write_response(writer, {"ok": False, "code": exc.code})
        except (asyncio.TimeoutError, ConnectionError, OSError, ValueError):
            if not tunnel:
                await _write_response(writer, {"ok": False, "code": "hub_egress_unavailable"})
        finally:
            if capability and tunnel:
                async with self.lock:
                    grant = self.grants.get(capability)
                    if grant:
                        grant.active_tunnels = max(0, grant.active_tunnels - 1)
                        grant.writers.discard(writer)
                        if remote_writer:
                            grant.writers.discard(remote_writer)
            if remote_writer:
                await _close_writer(remote_writer)
            await _close_writer(writer)

    async def _relay_client_tls(self, source: asyncio.StreamReader, destination: asyncio.StreamWriter, capability: str, expected_sni: str, deadline: float) -> None:
        records = bytearray()
        handshake = bytearray()
        expected_length = 0
        for _ in range(4):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HubSidecarError("hub_egress_tunnel_deadline")
            try:
                header = await asyncio.wait_for(source.readexactly(5), timeout=min(TUNNEL_IDLE_SECONDS, remaining))
                record_length = int.from_bytes(header[3:5], "big")
                if header[0] != 22 or record_length <= 0 or record_length > 18 * 1024:
                    raise HubSidecarError("hub_tls_client_hello_invalid")
                body = await asyncio.wait_for(source.readexactly(record_length), timeout=min(TUNNEL_IDLE_SECONDS, remaining))
            except (asyncio.IncompleteReadError, asyncio.TimeoutError) as exc:
                raise HubSidecarError("hub_tls_client_hello_invalid") from exc
            records.extend(header)
            records.extend(body)
            handshake.extend(body)
            if len(records) > 64 * 1024:
                raise HubSidecarError("hub_tls_client_hello_invalid")
            if len(handshake) >= 4:
                expected_length = 4 + int.from_bytes(handshake[1:4], "big")
                if expected_length <= len(handshake):
                    break
        if not expected_length or expected_length > len(handshake):
            raise HubSidecarError("hub_tls_client_hello_invalid")
        if _client_hello_sni(bytes(handshake[:expected_length])) != expected_sni:
            raise HubSidecarError("hub_tls_sni_denied")
        await self.consume(capability, len(records))
        destination.write(records)
        await destination.drain()
        await self._relay(source, destination, capability, deadline)

    async def _relay(self, source: asyncio.StreamReader, destination: asyncio.StreamWriter, capability: str, deadline: float) -> None:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HubSidecarError("hub_egress_tunnel_deadline")
            try:
                chunk = await asyncio.wait_for(source.read(64 * 1024), timeout=min(TUNNEL_IDLE_SECONDS, remaining))
            except asyncio.TimeoutError as exc:
                raise HubSidecarError("hub_egress_tunnel_idle") from exc
            if not chunk:
                return
            await self.consume(capability, len(chunk))
            destination.write(chunk)
            await destination.drain()


class LoopbackHubProxy:
    def __init__(self, capability: str, host: str) -> None:
        self.capability = capability
        self.host = host
        self.server: asyncio.AbstractServer | None = None
        self.port = 0

    async def start(self) -> None:
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        sock = next(iter(self.server.sockets or []), None)
        if sock is None:
            raise HubSidecarError("hub_proxy_unavailable")
        self.port = int(sock.getsockname()[1])

    async def close(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        egress_writer: asyncio.StreamWriter | None = None
        try:
            header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
            if len(header) > 8192:
                raise HubSidecarError("hub_proxy_request_invalid")
            lines = header.decode("iso-8859-1").split("\r\n")
            parts = lines[0].split(" ")
            if len(parts) != 3 or parts[0].upper() != "CONNECT" or parts[1].lower() != f"{self.host}:443":
                raise HubSidecarError("hub_proxy_target_denied")
            for line in lines[1:]:
                if not line:
                    continue
                name, separator, _value = line.partition(":")
                if not separator or name.strip().lower() in {"proxy-authorization", "authorization"}:
                    raise HubSidecarError("hub_proxy_header_denied")
            egress_reader, egress_writer = await asyncio.wait_for(asyncio.open_unix_connection(EGRESS_SOCKET), timeout=3)
            egress_writer.write(_json_bytes({"action": "tunnel", "capability": self.capability, "host": self.host, "port": 443}) + b"\n")
            await egress_writer.drain()
            response_raw = await asyncio.wait_for(egress_reader.readline(), timeout=CONNECT_TIMEOUT_SECONDS + 2)
            response = json.loads(response_raw.decode("utf-8"))
            if not isinstance(response, dict) or not response.get("ok"):
                raise HubSidecarError(str(response.get("code") if isinstance(response, dict) else "hub_egress_unavailable"))
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            tasks = [
                asyncio.create_task(_plain_relay(reader, egress_writer)),
                asyncio.create_task(_plain_relay(egress_reader, writer)),
            ]
            _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        except (HubSidecarError, asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError, OSError, UnicodeError, json.JSONDecodeError):
            try:
                writer.write(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
                await writer.drain()
            except (ConnectionError, OSError):
                pass
        finally:
            if egress_writer:
                await _close_writer(egress_writer)
            await _close_writer(writer)


async def _plain_relay(source: asyncio.StreamReader, destination: asyncio.StreamWriter) -> None:
    while True:
        chunk = await source.read(64 * 1024)
        if not chunk:
            return
        destination.write(chunk)
        await destination.drain()


@dataclass(slots=True)
class RemoteSession:
    session_id: str
    candidate_id: str
    session_owner: str
    url: str
    host: str
    capability: str
    auth_header_name: str
    auth_header_value: str = field(repr=False)
    auth_policy_fingerprint: str
    auth_binding_revision: int
    auth_context_digest: str
    tools: list[dict[str, Any]]
    created_at: float
    last_activity: float
    action_count: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class HubRemoteService:
    def __init__(self) -> None:
        self.sessions: dict[str, RemoteSession] = {}
        self.lock = asyncio.Lock()
        self.cleanup_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self.cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def shutdown(self) -> None:
        if self.cleanup_task:
            self.cleanup_task.cancel()
            await asyncio.gather(self.cleanup_task, return_exceptions=True)
        for session_id in list(self.sessions):
            await self.close(session_id)

    async def reset(self) -> None:
        for session_id in list(self.sessions):
            await self.close(session_id)

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(15)
            now = time.monotonic()
            expired = [
                item.session_id
                for item in self.sessions.values()
                if now - item.last_activity >= SESSION_IDLE_SECONDS
                or now - item.created_at >= SESSION_TTL_SECONDS
            ]
            for session_id in expired:
                await self.close(session_id)

    async def open(
        self,
        candidate_id: str,
        url: str,
        capability: str,
        session_owner: str,
        auth: Any = None,
    ) -> dict[str, Any]:
        if (
            CANDIDATE_ID_RE.fullmatch(candidate_id) is None
            or CAPABILITY_RE.fullmatch(capability) is None
            or SESSION_OWNER_RE.fullmatch(session_owner) is None
            or not session_owner.endswith(":" + candidate_id)
        ):
            raise HubSidecarError("hub_open_contract_invalid")
        normalized, host = _normalize_target(url)
        auth_envelope = _validated_auth_envelope(
            auth,
            candidate_id=candidate_id,
            normalized_url=normalized,
        )
        async with self.lock:
            if len(self.sessions) >= MAX_SESSIONS:
                raise HubSidecarError("hub_session_limit")
        last_error: HubSidecarError | None = None
        for _attempt in range(2):
            try:
                tools, _result = await self._exchange(
                    normalized,
                    capability,
                    expected_tools=None,
                    tool_name=None,
                    arguments=None,
                    auth_header=(auth_envelope[0], auth_envelope[1]) if auth_envelope else None,
                )
                now = time.monotonic()
                session_id = "hubsession_" + uuid_hex()
                session = RemoteSession(
                    session_id=session_id,
                    candidate_id=candidate_id,
                    session_owner=session_owner,
                    url=normalized,
                    host=host,
                    capability=capability,
                    auth_header_name=auth_envelope[0] if auth_envelope else "",
                    auth_header_value=auth_envelope[1] if auth_envelope else "",
                    auth_policy_fingerprint=auth_envelope[2] if auth_envelope else "",
                    auth_binding_revision=auth_envelope[3] if auth_envelope else 0,
                    auth_context_digest=auth_envelope[4] if auth_envelope else "",
                    tools=tools,
                    created_at=now,
                    last_activity=now,
                )
                async with self.lock:
                    self.sessions[session_id] = session
                return {"session_id": session_id, "tools": tools}
            except HubSidecarError as exc:
                last_error = exc
                if exc.code not in SAFE_PREFLIGHT_RETRY_CODES:
                    break
        # Keep the final bounded sidecar error code so Review Factory evidence
        # can distinguish policy, schema and transport failures.  No upstream
        # response body or exception text crosses the sidecar boundary.
        if last_error is not None:
            raise HubSidecarError(last_error.code) from None
        raise HubSidecarError("hub_upstream_preflight_failed")

    @staticmethod
    def _clear_sdk_cancellation() -> None:
        task = asyncio.current_task()
        if task is None:
            return
        while task.cancelling():
            task.uncancel()

    async def _exchange(
        self,
        url: str,
        capability: str,
        *,
        expected_tools: list[dict[str, Any]] | None,
        tool_name: str | None,
        arguments: dict[str, Any] | None,
        auth_header: tuple[str, str] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        """Run one complete SDK exchange without crossing asyncio tasks.

        The official SDK owns an anyio task group and may surface transport
        failures as ``CancelledError``/``BaseExceptionGroup``.  Keeping entry,
        use and exit in this task lets us convert those failures to a fixed
        sidecar code without leaking response bodies or abandoning cleanup.
        """

        _normalized, host = _normalize_target(url)
        proxy = LoopbackHubProxy(capability, host)
        failure: BaseException | None = None
        output: tuple[list[dict[str, Any]], dict[str, Any] | None] | None = None
        try:
            await proxy.start()
            request_headers = {
                "User-Agent": "ModelMirror-MCP-Hub/1.0",
                "Accept": "application/json, text/event-stream",
            }
            if auth_header is not None:
                request_headers[auth_header[0]] = auth_header[1]
            async with httpx.AsyncClient(
                proxy=f"http://127.0.0.1:{proxy.port}",
                timeout=httpx.Timeout(
                    connect=10,
                    read=CALL_TIMEOUT_SECONDS,
                    write=CALL_TIMEOUT_SECONDS,
                    pool=10,
                ),
                follow_redirects=False,
                trust_env=False,
                headers=request_headers,
            ) as http_client:
                client_session_type, streamable_http = _load_mcp_http()
                async with streamable_http(
                    url,
                    http_client=http_client,
                    terminate_on_close=True,
                ) as transport:
                    async with client_session_type(
                        transport[0],
                        transport[1],
                        logging_callback=_discard_untrusted_server_message,
                        message_handler=_discard_untrusted_server_message,
                    ) as client:
                        initialized = await asyncio.wait_for(
                            client.initialize(), timeout=CALL_TIMEOUT_SECONDS
                        )
                        self._validate_capabilities(initialized)
                        tools = await self._list_tools(client)
                        if expected_tools is not None and tools != expected_tools:
                            raise HubSidecarError("hub_schema_drift")
                        result: dict[str, Any] | None = None
                        if tool_name is not None:
                            try:
                                called = await asyncio.wait_for(
                                    client.call_tool(tool_name, arguments or {}),
                                    timeout=CALL_TIMEOUT_SECONDS,
                                )
                                result = called.model_dump(
                                    mode="json",
                                    by_alias=True,
                                    exclude_none=True,
                                )
                                if len(_json_bytes(result)) > MAX_RESPONSE_BYTES:
                                    raise HubSidecarError("hub_result_too_large")
                            except HubSidecarError:
                                raise
                            except BaseException as exc:
                                raise HubSidecarError(
                                    "hub_upstream_unknown_outcome"
                                ) from exc
                        output = (tools, result)
        except HubSidecarError as exc:
            failure = exc
        except BaseException as exc:
            failure = exc
        try:
            await proxy.close()
        except BaseException as exc:
            failure = failure or exc
        if failure is not None:
            self._clear_sdk_cancellation()
            if isinstance(failure, HubSidecarError):
                raise failure
            code = (
                "hub_upstream_unknown_outcome"
                if tool_name is not None
                else _fixed_preflight_error(
                    failure, authenticated=auth_header is not None
                )
            )
            raise HubSidecarError(code) from failure
        if output is None:
            raise HubSidecarError("hub_upstream_preflight_failed")
        return output

    @staticmethod
    def _validate_capabilities(initialized: Any) -> None:
        capabilities = getattr(initialized, "capabilities", None)
        if capabilities is None or not hasattr(capabilities, "model_dump"):
            raise HubSidecarError("hub_server_capabilities_invalid")
        dumped = capabilities.model_dump(
            mode="json", by_alias=True, exclude_none=True
        )
        if not isinstance(dumped, dict) or "tools" not in dumped:
            raise HubSidecarError("hub_tools_capability_required")
        allowed_capabilities = {"tools", "prompts", "resources"}
        if any(key not in allowed_capabilities for key in dumped):
            raise HubSidecarError("hub_non_tool_capability_denied")
        for name, allowed_flags in (
            ("prompts", {"listChanged", "list_changed"}),
            ("resources", {"listChanged", "list_changed", "subscribe"}),
        ):
            capability = dumped.get(name)
            if capability is None:
                continue
            if (
                not isinstance(capability, dict)
                or any(key not in allowed_flags for key in capability)
                or any(not isinstance(value, bool) for value in capability.values())
                or (name == "resources" and bool(capability.get("subscribe")))
            ):
                raise HubSidecarError("hub_non_tool_capability_denied")
        tools = dumped.get("tools")
        if (
            not isinstance(tools, dict)
            or any(key not in {"listChanged", "list_changed"} for key in tools)
            or any(not isinstance(value, bool) for value in tools.values())
        ):
            raise HubSidecarError("hub_dynamic_tools_denied")

    async def _list_tools(self, client: Any) -> list[dict[str, Any]]:
        tools: list[Any] = []
        cursor: str | None = None
        for page in range(10):
            result = await asyncio.wait_for(
                client.list_tools() if page == 0 else client.list_tools(cursor=cursor),
                timeout=CALL_TIMEOUT_SECONDS,
            )
            tools.extend(result.tools)
            if len(tools) > MAX_TOOL_COUNT:
                raise HubSidecarError("hub_tool_count_denied")
            cursor = str(result.nextCursor or "") or None
            if cursor is None:
                break
        else:
            raise HubSidecarError("hub_tool_pagination_denied")
        normalized: list[dict[str, Any]] = []
        names: set[str] = set()
        total = 0
        for tool in tools:
            name = str(tool.name or "").strip()
            schema = tool.inputSchema
            if TOOL_NAME_RE.fullmatch(name) is None or name in names or not isinstance(schema, dict):
                raise HubSidecarError("hub_tool_contract_denied")
            encoded = _json_bytes(schema)
            if len(encoded) > MAX_SCHEMA_BYTES:
                raise HubSidecarError("hub_tool_schema_denied")
            total += len(encoded)
            names.add(name)
            normalized.append({"name": name, "description": str(tool.description or "")[:4000], "input_schema": schema})
        if not normalized or total > MAX_TOTAL_SCHEMA_BYTES:
            raise HubSidecarError("hub_tool_contract_denied")
        normalized.sort(key=lambda item: item["name"])
        return normalized

    async def call(self, session_id: str, tool_name: str, arguments: Any) -> dict[str, Any]:
        session = self.sessions.get(session_id)
        if session is None:
            raise HubSidecarError("hub_session_not_found")
        if not isinstance(arguments, dict) or len(_json_bytes(arguments)) > 32 * 1024:
            raise HubSidecarError("hub_arguments_denied")
        if tool_name not in {item["name"] for item in session.tools}:
            raise HubSidecarError("hub_tool_not_found")
        async with session.lock:
            now = time.monotonic()
            if (
                now - session.created_at >= SESSION_TTL_SECONDS
                or now - session.last_activity >= SESSION_IDLE_SECONDS
                or session.action_count >= MAX_ACTIONS
            ):
                await self.close(session_id)
                raise HubSidecarError("hub_session_expired")
            session.action_count += 1
            session.last_activity = now
            try:
                _tools, result = await self._exchange(
                    session.url,
                    session.capability,
                    expected_tools=session.tools,
                    tool_name=tool_name,
                    arguments=arguments,
                    auth_header=(
                        (session.auth_header_name, session.auth_header_value)
                        if session.auth_header_name
                        else None
                    ),
                )
                if result is None:
                    raise HubSidecarError("hub_upstream_unknown_outcome")
                return result
            except HubSidecarError:
                removed = self.sessions.pop(session_id, None)
                if removed is not None:
                    removed.auth_header_value = ""
                raise

    async def list_tools(self, session_id: str) -> list[dict[str, Any]]:
        session = self.sessions.get(session_id)
        if session is None:
            raise HubSidecarError("hub_session_not_found")
        async with session.lock:
            now = time.monotonic()
            if (
                now - session.created_at >= SESSION_TTL_SECONDS
                or now - session.last_activity >= SESSION_IDLE_SECONDS
            ):
                await self.close(session_id)
                raise HubSidecarError("hub_session_expired")
            last_error: Exception | None = None
            for _attempt in range(2):
                try:
                    tools, _result = await self._exchange(
                        session.url,
                        session.capability,
                        expected_tools=None,
                        tool_name=None,
                        arguments=None,
                        auth_header=(
                            (session.auth_header_name, session.auth_header_value)
                            if session.auth_header_name
                            else None
                        ),
                    )
                    session.tools = tools
                    session.last_activity = time.monotonic()
                    return tools
                except HubSidecarError as exc:
                    last_error = exc
                    if exc.code not in SAFE_PREFLIGHT_RETRY_CODES:
                        break
            raise HubSidecarError("hub_tool_recheck_failed") from last_error

    async def close(self, session_id: str) -> None:
        async with self.lock:
            session = self.sessions.pop(session_id, None)
            if session is not None:
                session.auth_header_value = ""

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            uid = _peer_uid(writer)
            if uid not in {0, 65532}:
                raise HubSidecarError("hub_peer_denied")
            request = await _read_request(reader)
            action = str(request.get("action") or "")
            if action == "health":
                response = {
                    "ok": True,
                    "protocol": "modelmirror-mcp-hub-remote-v1",
                    "active_sessions": len(self.sessions),
                }
            elif uid != 0:
                raise HubSidecarError("hub_peer_denied")
            elif action == "open":
                result = await self.open(
                    str(request.get("candidate_id") or ""),
                    str(request.get("url") or ""),
                    str(request.get("capability") or ""),
                    str(request.get("session_owner") or ""),
                    request.get("auth"),
                )
                response = {"ok": True, **result}
            elif action == "call":
                result = await self.call(str(request.get("session_id") or ""), str(request.get("tool_name") or ""), request.get("arguments"))
                response = {"ok": True, "result": result}
            elif action == "list_tools":
                tools = await self.list_tools(str(request.get("session_id") or ""))
                response = {"ok": True, "tools": tools}
            elif action == "close":
                await self.close(str(request.get("session_id") or ""))
                response = {"ok": True}
            elif action == "reset":
                await self.reset()
                response = {"ok": True}
            else:
                raise HubSidecarError("hub_action_denied")
        except HubSidecarError as exc:
            response = {"ok": False, "code": exc.code}
        except Exception:
            response = {"ok": False, "code": "hub_sidecar_internal_error"}
        await _write_response(writer, response)
        await _close_writer(writer)


def uuid_hex() -> str:
    return secrets.token_hex(16)


def _prepare_socket(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(metadata.st_mode):
        raise RuntimeError("hub_socket_path_unsafe")
    path.unlink()


async def run_egress() -> None:
    _prepare_socket(EGRESS_SOCKET)
    service = HubEgressService()
    server = await asyncio.start_unix_server(service.handle, path=str(EGRESS_SOCKET))
    os.chmod(EGRESS_SOCKET, 0o660)
    async with server:
        await server.serve_forever()


async def run_remote() -> None:
    _prepare_socket(REMOTE_SOCKET)
    service = HubRemoteService()
    await service.start()
    server = await asyncio.start_unix_server(service.handle, path=str(REMOTE_SOCKET))
    os.chmod(REMOTE_SOCKET, 0o660)
    try:
        async with server:
            await server.serve_forever()
    finally:
        await service.shutdown()


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "remote"
    if mode not in {"remote", "egress"}:
        raise SystemExit("usage: hub_server.py [remote|egress]")
    asyncio.run(run_egress() if mode == "egress" else run_remote())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
