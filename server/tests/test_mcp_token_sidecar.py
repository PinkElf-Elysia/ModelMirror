from __future__ import annotations

import asyncio
import base64
import json

import pytest

from server.mcp import token_proxy
from server.mcp.catalog import CATALOG_ADAPTERS, WAVE_FOUR_ADAPTERS
from server.sandbox_sidecar import token_server
from server.sandbox_sidecar.safe_http import NetworkPolicyError
from server.sandbox_sidecar.token_contracts import (
    TOKEN_ADAPTERS,
    TOKEN_SCHEMA_SHA256,
    validate_configuration,
)


class MemoryWriter:
    def __init__(self) -> None:
        self.data = bytearray()

    def write(self, value: bytes) -> None:
        self.data.extend(value)

    async def drain(self) -> None:
        return None


def reader_for(*messages: dict[str, object]) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    for message in messages:
        reader.feed_data(json.dumps(message, separators=(",", ":")).encode() + b"\n")
    reader.feed_eof()
    return reader


def test_runtime_contracts_match_catalog_and_never_include_snyk() -> None:
    assert set(TOKEN_ADAPTERS) == set(WAVE_FOUR_ADAPTERS)
    assert set(TOKEN_SCHEMA_SHA256) == set(TOKEN_ADAPTERS)
    assert set(token_proxy.ALLOWED_ADAPTERS) == set(WAVE_FOUR_ADAPTERS)
    assert "snyk-mcp" not in TOKEN_ADAPTERS
    for project_id, contract in TOKEN_ADAPTERS.items():
        assert contract.tools == frozenset(CATALOG_ADAPTERS[project_id].tool_policies)
        assert contract.command
        assert all("npx" not in item for item in contract.command)


def test_configuration_contract_rejects_missing_and_extra_fields() -> None:
    contract, credentials, settings = validate_configuration(
        "grafana-mcp",
        {
            "credentials": {"service_token": "secret"},
            "settings": {"stack_slug": "my-stack"},
        },
    )
    assert contract.builtin is True
    assert credentials == {"service_token": "secret"}
    assert settings == {"stack_slug": "my-stack"}

    with pytest.raises(ValueError, match="configuration_contract_mismatch"):
        validate_configuration(
            "grafana-mcp",
            {
                "credentials": {"service_token": "secret", "admin": "secret"},
                "settings": {"stack_slug": "my-stack"},
            },
        )
    with pytest.raises(ValueError, match="invalid_setting"):
        validate_configuration(
            "pinecone-assistant-mcp",
            {
                "credentials": {"api_key": "secret"},
                "settings": {"assistant_host": "localhost", "assistant_name": "docs"},
            },
        )


def test_proxy_configuration_is_removed_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"settings": {}, "credentials": {"api_key": "private-value"}}
    monkeypatch.setenv(
        "MCP_TOKEN_HANDSHAKE_B64",
        base64.urlsafe_b64encode(json.dumps(payload).encode()).decode(),
    )
    assert token_proxy._load_configuration() == payload
    assert "MCP_TOKEN_HANDSHAKE_B64" not in token_proxy.os.environ


@pytest.mark.asyncio
async def test_gateway_rejects_unlisted_tool_before_child() -> None:
    contract = TOKEN_ADAPTERS["agentql-mcp"]
    child = MemoryWriter()
    client = MemoryWriter()
    await token_server._client_to_child(  # type: ignore[arg-type]
        reader_for(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "delete_everything", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "extract-web-data", "arguments": {"prompt": "title"}}},
        ),
        child,
        client,
        contract,
        set(),
        asyncio.Lock(),
    )
    assert b"delete_everything" not in child.data
    assert b"extract-web-data" in child.data
    assert "未通过只读策略审核" in client.data.decode("utf-8")


@pytest.mark.asyncio
async def test_gateway_filters_tool_discovery() -> None:
    contract = TOKEN_ADAPTERS["agentql-mcp"]
    output = MemoryWriter()
    await token_server._child_to_client(  # type: ignore[arg-type]
        reader_for(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "result": {
                    "tools": [
                        {"name": "extract-web-data", "inputSchema": {}},
                        {"name": "unsafe-write", "inputSchema": {}},
                    ]
                },
            }
        ),
        output,
        contract,
        {7},
        asyncio.Lock(),
    )
    payload = json.loads(bytes(output.data).decode("utf-8"))
    assert [item["name"] for item in payload["result"]["tools"]] == ["extract-web-data"]


def test_gateway_url_preflight_requires_public_https(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(token_server, "resolve_public_addresses", lambda host, port: ("93.184.216.34",))
    token_server._validate_argument_targets({"url": "https://example.com/page"})
    with pytest.raises(NetworkPolicyError):
        token_server._validate_argument_targets({"url": "http://example.com/page"})
    with pytest.raises(NetworkPolicyError):
        token_server._validate_argument_targets({"url": "file:///etc/passwd"})
