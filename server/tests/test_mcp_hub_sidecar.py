from __future__ import annotations

import asyncio
import ipaddress
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from server.sandbox_sidecar import hub_server


def dns_record(address: str) -> tuple[Any, ...]:
    family = 10 if ipaddress.ip_address(address).version == 6 else 2
    return (family, 1, 6, "", (address, 443))


def client_hello(host: str, *, ech: bool = False) -> bytes:
    name = host.encode("ascii")
    server_name = b"\x00" + len(name).to_bytes(2, "big") + name
    sni_payload = len(server_name).to_bytes(2, "big") + server_name
    extensions = b"\x00\x00" + len(sni_payload).to_bytes(2, "big") + sni_payload
    if ech:
        extensions += b"\xfe\x0d\x00\x00"
    body = (
        b"\x03\x03"
        + b"0" * 32
        + b"\x00"
        + b"\x00\x02\x13\x01"
        + b"\x01\x00"
        + len(extensions).to_bytes(2, "big")
        + extensions
    )
    return b"\x01" + len(body).to_bytes(3, "big") + body


def test_target_and_dns_policy_rejects_private_mixed_and_synthetic_answers() -> None:
    assert hub_server._normalize_target("https://mcp.example.com/mcp") == (
        "https://mcp.example.com/mcp",
        "mcp.example.com",
    )
    for target in (
        "http://mcp.example.com/mcp",
        "https://mcp.example.com:8443/mcp",
        "https://127.0.0.1/mcp",
        "https://mcp.example.com/mcp?x=1",
    ):
        with pytest.raises(hub_server.HubSidecarError):
            hub_server._normalize_target(target)

    assert hub_server._validate_dns_records(
        [dns_record("93.184.216.34"), dns_record("2606:2800:220:1:248:1893:25c8:1946")]
    ) == ("93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946")
    for records in (
        [dns_record("127.0.0.1")],
        [dns_record("93.184.216.34"), dns_record("10.0.0.1")],
        [dns_record("198.18.0.10")],
        [dns_record("169.254.169.254")],
    ):
        with pytest.raises(
            hub_server.HubSidecarError,
            match="hub_dns_private_or_synthetic_denied",
        ):
            hub_server._validate_dns_records(records)


def test_tls_client_hello_requires_exact_sni_and_rejects_ech() -> None:
    assert hub_server._client_hello_sni(client_hello("mcp.example.com")) == "mcp.example.com"
    with pytest.raises(hub_server.HubSidecarError, match="hub_tls_ech_denied"):
        hub_server._client_hello_sni(client_hello("mcp.example.com", ech=True))


class Capabilities:
    def __init__(self, value: dict[str, Any]) -> None:
        self.value = value

    def model_dump(self, **_kwargs: Any) -> dict[str, Any]:
        return self.value


class Initialized:
    def __init__(self, value: dict[str, Any]) -> None:
        self.capabilities = Capabilities(value)


def test_only_static_tools_capability_is_accepted() -> None:
    hub_server.HubRemoteService._validate_capabilities(Initialized({"tools": {}}))
    for denied in (
        {"resources": {}, "tools": {}},
        {"prompts": {}, "tools": {}},
        {"sampling": {}, "tools": {}},
        {"tools": {"listChanged": True}},
        {},
    ):
        with pytest.raises(hub_server.HubSidecarError):
            hub_server.HubRemoteService._validate_capabilities(Initialized(denied))


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            httpx.HTTPStatusError(
                "not used",
                request=httpx.Request("POST", "https://mcp.example.com/mcp"),
                response=httpx.Response(401),
            ),
            "hub_upstream_auth_required",
        ),
        (
            ExceptionGroup(
                "not used",
                [
                    httpx.HTTPStatusError(
                        "not used",
                        request=httpx.Request("POST", "https://mcp.example.com/mcp"),
                        response=httpx.Response(429),
                    )
                ],
            ),
            "hub_upstream_rate_limited",
        ),
        (httpx.ReadTimeout("not used"), "hub_upstream_timeout"),
        (
            hub_server.HubSidecarError("hub_non_tool_capability_denied"),
            "hub_non_tool_capability_denied",
        ),
    ],
)
def test_preflight_errors_are_reduced_to_fixed_codes(
    error: BaseException, expected: str
) -> None:
    assert hub_server._fixed_preflight_error(error) == expected


def test_authenticated_401_and_403_have_fixed_non_retryable_codes() -> None:
    request = httpx.Request("POST", "https://mcp.example.com/mcp")
    assert hub_server._fixed_preflight_error(
        httpx.HTTPStatusError(
            "not used", request=request, response=httpx.Response(401)
        ),
        authenticated=True,
    ) == "mcp_remote_auth_unauthorized"
    assert hub_server._fixed_preflight_error(
        httpx.HTTPStatusError(
            "not used", request=request, response=httpx.Response(403)
        ),
        authenticated=True,
    ) == "mcp_remote_auth_forbidden"


@pytest.mark.asyncio
async def test_remote_auth_envelope_is_scope_bound_and_cleared_on_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = hub_server.HubRemoteService()
    observed: list[tuple[str, str] | None] = []
    tools = [
        {
            "name": "search",
            "description": "read",
            "input_schema": {"type": "object"},
        }
    ]

    async def exchange(*_args: Any, **kwargs: Any) -> Any:
        observed.append(kwargs.get("auth_header"))
        return tools, ({"content": []} if kwargs.get("tool_name") else None)

    monkeypatch.setattr(service, "_exchange", exchange)
    candidate_id = "mcphub_" + "1" * 32
    envelope = {
        "binding_id": "mcpra_" + "2" * 32,
        "binding_revision": 3,
        "header_name": "Authorization",
        "header_value": "Bearer sidecar-test-secret",
        "origin": "https://mcp.example.com",
        "policy_fingerprint": "3" * 64,
        "target_id": candidate_id,
    }
    opened = await service.open(
        candidate_id,
        "https://mcp.example.com/mcp",
        "a" * 64,
        f"hub:local:local:{candidate_id}",
        envelope,
    )
    session = service.sessions[opened["session_id"]]
    assert observed == [("Authorization", "Bearer sidecar-test-secret")]
    assert "sidecar-test-secret" not in repr(session)
    await service.call(opened["session_id"], "search", {})
    assert observed[-1] == ("Authorization", "Bearer sidecar-test-secret")
    await service.close(opened["session_id"])
    assert session.auth_header_value == ""

    crossed = dict(envelope, target_id="mcphub_" + "4" * 32)
    with pytest.raises(hub_server.HubSidecarError) as denied:
        await service.open(
            candidate_id,
            "https://mcp.example.com/mcp",
            "a" * 64,
            f"hub:local:local:{candidate_id}",
            crossed,
        )
    assert denied.value.code == "mcp_remote_auth_scope_denied"

    for denied_header in (
        "User-Agent",
        "MCP-Protocol-Version",
        "X-Forwarded-Host",
        "X-Original-URL",
    ):
        with pytest.raises(hub_server.HubSidecarError) as denied_control:
            await service.open(
                candidate_id,
                "https://mcp.example.com/mcp",
                "a" * 64,
                f"hub:local:local:{candidate_id}",
                dict(envelope, header_name=denied_header),
            )
        assert denied_control.value.code == "mcp_remote_auth_policy_ineligible"


@pytest.mark.asyncio
async def test_sidecar_rejects_tool_names_outside_the_mcp_contract() -> None:
    class Client:
        async def list_tools(self, **_kwargs: Any) -> Any:
            return SimpleNamespace(
                tools=[
                    SimpleNamespace(
                        name="bad name\nwith control",
                        description="untrusted",
                        inputSchema={"type": "object"},
                    )
                ],
                nextCursor=None,
            )

    with pytest.raises(hub_server.HubSidecarError) as captured:
        await hub_server.HubRemoteService()._list_tools(Client())
    assert str(captured.value) == "hub_tool_contract_denied"


@pytest.mark.asyncio
async def test_remote_open_preserves_final_fixed_preflight_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = hub_server.HubRemoteService()
    attempts = 0

    async def denied(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal attempts
        attempts += 1
        raise hub_server.HubSidecarError("hub_non_tool_capability_denied")

    monkeypatch.setattr(service, "_exchange", denied)
    with pytest.raises(hub_server.HubSidecarError) as captured:
        await service.open(
            "mcphub_" + "1" * 32,
            "https://mcp.example.com/mcp",
            "a" * 64,
            "hub:local:owner:mcphub_" + "1" * 32,
        )

    assert attempts == 1
    assert captured.value.code == "hub_non_tool_capability_denied"


@pytest.mark.asyncio
async def test_remote_health_is_read_only_for_sidecar_uid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hub_server, "_peer_uid", lambda _writer: 65532)
    service = hub_server.HubRemoteService()
    server = await asyncio.start_server(service.handle, "127.0.0.1", 0)
    port = int(server.sockets[0].getsockname()[1])
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b'{"action":"health"}\n')
        await writer.drain()
        response = await reader.readline()
        assert response == (
            b'{"active_sessions":0,"ok":true,'
            b'"protocol":"modelmirror-mcp-hub-remote-v1"}\n'
        )
        writer.close()
        await writer.wait_closed()

        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b'{"action":"reset"}\n')
        await writer.drain()
        assert await reader.readline() == b'{"code":"hub_peer_denied","ok":false}\n'
        writer.close()
        await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_authorize_pins_all_dns_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    async def resolved(_host: str) -> tuple[str, ...]:
        return ("93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946")

    monkeypatch.setattr(hub_server, "_resolve_public", resolved)
    service = hub_server.HubEgressService()
    capability = await service.authorize(
        "mcphub_" + "1" * 32,
        "https://mcp.example.com/mcp",
    )
    assert service.grants[capability].pinned_addresses == (
        "93.184.216.34",
        "2606:2800:220:1:248:1893:25c8:1946",
    )
    await service.reset()
    assert service.grants == {}


def test_compose_keeps_remote_offline_and_legacy_entry_disabled_by_default() -> None:
    compose = (Path(__file__).parents[2] / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    assert "mcp-hub-remote:" in compose
    assert "network_mode: none" in compose
    assert "mcp-hub-egress:" in compose
    assert "MCP_HUB_ENABLED: ${MCP_HUB_ENABLED:-false}" in compose
    assert "MCP_HUB_REMOTE_ENABLED: ${MCP_HUB_REMOTE_ENABLED:-false}" in compose
    assert "MCP_REMOTE_AUTH_ENABLED: ${MCP_REMOTE_AUTH_ENABLED:-false}" in compose
    assert (
        "MCP_REMOTE_STATIC_TOKEN_ENABLED: "
        "${MCP_REMOTE_STATIC_TOKEN_ENABLED:-false}"
    ) in compose
    assert (
        "MCP_LEGACY_UNRESTRICTED_CONNECT_ENABLED: "
        "${MCP_LEGACY_UNRESTRICTED_CONNECT_ENABLED:-false}"
    ) in compose
    assert compose.count("cap_drop:\n      - ALL") >= 2
    assert compose.count("no-new-privileges:true") >= 2


def test_hub_image_uses_a_dedicated_complete_hash_lock() -> None:
    root = Path(__file__).parents[2] / "server" / "sandbox_sidecar"
    dockerfile = (root / "Dockerfile.hub").read_text(encoding="utf-8")
    lock = (root / "requirements.hub.lock").read_text(encoding="utf-8")
    assert "COPY requirements.hub.lock" in dockerfile
    assert "--require-hashes -r requirements.hub.lock" in dockerfile
    assert "COPY requirements.txt" not in dockerfile
    assert "mcp==1.27.2" in lock
    assert "tzdata==2026.3" in lock
    assert lock.count("--hash=sha256:") > 20
    assert "--index-url" not in "\n".join(
        line for line in lock.splitlines() if not line.startswith("#")
    )


def test_hub_sidecar_defers_official_sdk_import_until_runtime() -> None:
    assert not hasattr(hub_server, "ClientSession")
    assert not hasattr(hub_server, "streamable_http_client")
    client_session, streamable_http = hub_server._load_mcp_http()
    assert client_session.__module__ == "mcp.client.session"
    assert streamable_http.__module__ == "mcp.client.streamable_http"


def test_hub_timeout_fixture_and_smoke_are_fixed_and_bounded() -> None:
    root = Path(__file__).parents[2]
    smoke = (root / "server/tests/smoke_mcp_hub_gateway.py").read_text(
        encoding="utf-8"
    )
    fixture = (root / "server/tests/fixture_mcp_hub_timeout.py").read_text(
        encoding="utf-8"
    )

    assert '"https://hub-timeout.modelmirror.test/mcp"' in smoke
    assert '"timeout-call"' in smoke
    assert '"static-token-call"' in smoke
    assert "auth_revoke_disconnect=" in smoke
    assert "18 <= elapsed <= 28" in smoke
    assert "FIXTURE_DELAY_SECONDS = 25" in fixture
    assert "stateless=True" in fixture
    assert "StreamableHTTPSessionManager" in fixture
    assert "types.ServerCapabilities(" in fixture
    assert 'Route("/mcp"' in fixture
    assert 'parser.add_argument("--require-bearer", action="store_true")' in fixture
    assert "headers.get(b\"authorization\") != expected" in fixture
