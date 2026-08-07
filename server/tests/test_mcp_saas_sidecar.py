from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from server.sandbox_sidecar import saas_mcp, saas_server
from server.sandbox_sidecar.saas_contracts import (
    SAAS_ADAPTERS,
    SAAS_SCHEMA_SHA256,
    validate_configuration,
)
from server.sandbox_sidecar.safe_http import NetworkPolicyError


EXPECTED_TOOLS = {
    "airtable-mcp": {
        "list_tables",
        "list_records",
        "get_record",
        "create_record",
        "update_record",
    },
    "asana-mcp": {
        "list_projects",
        "list_tasks",
        "get_task",
        "create_task",
        "update_task",
        "add_comment",
    },
    "gitlab-mcp": {
        "list_issues",
        "get_issue",
        "list_merge_requests",
        "get_merge_request",
        "get_repository_file",
        "create_issue",
        "update_issue",
        "add_issue_note",
    },
    "notion-mcp-server": {
        "query_data_source",
        "retrieve_page",
        "create_page",
        "update_page_properties",
    },
}


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


def test_contracts_are_fixed_and_have_no_terminal_or_delete_tools() -> None:
    assert set(SAAS_ADAPTERS) == set(EXPECTED_TOOLS)
    assert set(SAAS_SCHEMA_SHA256) == set(SAAS_ADAPTERS)
    assert all(SAAS_SCHEMA_SHA256.values())
    for adapter_id, contract in SAAS_ADAPTERS.items():
        assert set(contract.tools) == EXPECTED_TOOLS[adapter_id]
        assert {policy.effect for policy in contract.tools.values()} <= {"read", "state-write"}
        assert not any("delete" in name.lower() for name in contract.tools)
        assert contract.host in {
            "api.airtable.com",
            "app.asana.com",
            "gitlab.com",
            "api.notion.com",
        }


@pytest.mark.parametrize(
    ("adapter_id", "configuration", "expected_settings"),
    [
        (
            "airtable-mcp",
            {
                "credentials": {"personal_access_token": "secret"},
                "settings": {"base_id": "app12345678901234"},
            },
            {"base_id": "app12345678901234"},
        ),
        (
            "asana-mcp",
            {
                "credentials": {"personal_access_token": "secret"},
                "settings": {"workspace_gid": "12001", "project_gid": "12002"},
            },
            {"workspace_gid": "12001", "project_gid": "12002"},
        ),
        (
            "gitlab-mcp",
            {
                "credentials": {"personal_access_token": "secret"},
                "settings": {"project_id": "42"},
            },
            {"project_id": "42"},
        ),
        (
            "notion-mcp-server",
            {
                "credentials": {"integration_token": "secret"},
                "settings": {"data_source_id": "01234567-89ab-cdef-0123-456789abcdef"},
            },
            {"data_source_id": "0123456789abcdef0123456789abcdef"},
        ),
    ],
)
def test_configuration_is_exact_and_normalized(
    adapter_id: str,
    configuration: dict[str, Any],
    expected_settings: dict[str, str],
) -> None:
    _, credentials, settings = validate_configuration(adapter_id, configuration)
    assert set(credentials) == set(SAAS_ADAPTERS[adapter_id].credential_fields)
    assert settings == expected_settings


def test_configuration_rejects_extra_fields_urls_and_wrong_scope() -> None:
    with pytest.raises(ValueError, match="configuration_contract_mismatch"):
        validate_configuration(
            "gitlab-mcp",
            {
                "credentials": {"personal_access_token": "secret"},
                "settings": {"project_id": "42", "url": "https://evil.example"},
            },
        )
    with pytest.raises(ValueError, match="invalid_project_id"):
        validate_configuration(
            "gitlab-mcp",
            {
                "credentials": {"personal_access_token": "secret"},
                "settings": {"project_id": "https://gitlab.example/project"},
            },
        )
    with pytest.raises(ValueError, match="configuration_contract_mismatch"):
        validate_configuration(
            "notion-mcp-server",
            {
                "credentials": {"integration_token": "secret"},
                "settings": {
                    "scope_type": "page",
                    "scope_id": "0123456789abcdef0123456789abcdef",
                },
            },
        )


def test_write_idempotency_key_is_private_one_shot_and_stripped() -> None:
    contract = SAAS_ADAPTERS["airtable-mcp"]
    used: set[str] = set()
    key = "mcpidem_" + "a" * 32
    arguments, returned = saas_server._prepare_tool_call(
        contract,
        "create_record",
        {
            "table_id": "tbl12345678901234",
            "fields": {"Name": "Ada"},
            "__modelmirror_idempotency_key": key,
        },
        used,
    )
    assert returned == key
    assert key in used
    assert "__modelmirror_idempotency_key" not in arguments
    with pytest.raises(ValueError, match="idempotency_key_replayed"):
        saas_server._prepare_tool_call(
            contract,
            "create_record",
            {
                "table_id": "tbl12345678901234",
                "fields": {"Name": "Ada"},
                "__modelmirror_idempotency_key": key,
            },
            used,
        )
    with pytest.raises(ValueError, match="invalid_idempotency_key"):
        saas_server._prepare_tool_call(
            contract,
            "update_record",
            {"table_id": "tbl12345678901234", "record_id": "rec12345678901234"},
            used,
        )


def test_reserved_fields_are_rejected_recursively() -> None:
    with pytest.raises(ValueError, match="reserved_argument_field"):
        saas_server._prepare_tool_call(
            SAAS_ADAPTERS["gitlab-mcp"],
            "list_issues",
            {"filter": {"__modelmirror_header": "PRIVATE-TOKEN"}},
            set(),
        )


class FakeClient:
    def __init__(self, responses: list[saas_mcp.HttpResult]) -> None:
        self.responses = responses
        self.calls = 0

    def request(self, *args: Any, **kwargs: Any) -> saas_mcp.HttpResult:
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return response


def test_read_retries_only_reviewed_statuses_and_caps_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waits: list[float] = []
    monkeypatch.setattr(saas_mcp.time, "sleep", waits.append)
    client = FakeClient(
        [
            saas_mcp.HttpResult(429, {"retry-after": "99"}, b"{}"),
            saas_mcp.HttpResult(503, {}, b"{}"),
            saas_mcp.HttpResult(200, {}, b'{"ok":true}'),
        ]
    )
    result = saas_mcp._request_json(  # type: ignore[arg-type]
        client,
        "test",
        "/fixed",
        headers={"Authorization": "Bearer secret"},
        read_operation=True,
    )
    assert result == {"ok": True}
    assert client.calls == 3
    assert waits[0] == saas_mcp.MAX_RETRY_AFTER_SECONDS


def test_write_never_retries() -> None:
    client = FakeClient([saas_mcp.HttpResult(503, {"retry-after": "1"}, b"{}")])
    with pytest.raises(
        saas_mcp.UnknownWriteOutcome,
        match=saas_mcp.UNKNOWN_WRITE_OUTCOME_MARKER,
    ):
        saas_mcp._request_json(  # type: ignore[arg-type]
            client,
            "test",
            "/fixed",
            method="POST",
            headers={"Authorization": "Bearer secret"},
            payload={"value": 1},
            read_operation=False,
        )
    assert client.calls == 1


def test_definite_write_rejection_is_not_retried_or_marked_unknown() -> None:
    client = FakeClient([saas_mcp.HttpResult(400, {}, b"{}")])
    with pytest.raises(RuntimeError, match="test_http_400"):
        saas_mcp._request_json(  # type: ignore[arg-type]
            client,
            "test",
            "/fixed",
            method="POST",
            headers={"Authorization": "Bearer secret"},
            payload={"value": 1},
            read_operation=False,
        )
    assert client.calls == 1


def test_write_network_failure_is_an_unknown_outcome() -> None:
    class FailingClient:
        @staticmethod
        def request(*args: Any, **kwargs: Any) -> saas_mcp.HttpResult:
            raise NetworkPolicyError("saas_https_failed")

    with pytest.raises(
        saas_mcp.UnknownWriteOutcome,
        match=saas_mcp.UNKNOWN_WRITE_OUTCOME_MARKER,
    ):
        saas_mcp._request_json(  # type: ignore[arg-type]
            FailingClient(),
            "test",
            "/fixed",
            method="POST",
            headers={"Authorization": "Bearer secret"},
            payload={"value": 1},
            read_operation=False,
        )


def test_fixed_http_client_rejects_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        status = 302

        @staticmethod
        def getheaders() -> list[tuple[str, str]]:
            return [("Location", "https://evil.example/")]

        @staticmethod
        def read(_: int) -> bytes:
            return b""

    class Connection:
        def request(self, *args: Any, **kwargs: Any) -> None:
            return None

        @staticmethod
        def getresponse() -> Response:
            return Response()

        @staticmethod
        def close() -> None:
            return None

    monkeypatch.setattr(saas_mcp, "resolve_public_addresses", lambda host, port: ("93.184.216.34",))
    monkeypatch.setattr(saas_mcp, "_PinnedHTTPSConnection", lambda *args, **kwargs: Connection())
    client = saas_mcp.FixedHTTPSClient("api.airtable.com", minimum_interval_seconds=0)
    with pytest.raises(NetworkPolicyError, match="saas_redirect_denied"):
        client.request("/v0/meta/whoami", method="GET", headers={"Authorization": "Bearer secret"})


def test_fixed_http_client_rejects_unreviewed_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        saas_mcp,
        "resolve_public_addresses",
        lambda host, port: ("93.184.216.34",),
    )
    client = saas_mcp.FixedHTTPSClient("api.airtable.com", minimum_interval_seconds=0)
    with pytest.raises(NetworkPolicyError, match="saas_http_header_denied"):
        client.request(
            "/v0/meta/whoami",
            method="GET",
            headers={"X-Request-Id": "client-controlled"},
        )


@pytest.mark.asyncio
async def test_gateway_filters_unreviewed_tool_discovery() -> None:
    output = MemoryWriter()
    await saas_server._child_to_client(  # type: ignore[arg-type]
        reader_for(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "result": {
                    "tools": [
                        {"name": "list_tables", "inputSchema": {}},
                        {"name": "delete_everything", "inputSchema": {}},
                    ]
                },
            }
        ),
        output,
        SAAS_ADAPTERS["airtable-mcp"],
        {7},
        set(),
        set(),
        {},
        set(),
        asyncio.Lock(),
    )
    payload = json.loads(bytes(output.data).decode("utf-8"))
    assert [tool["name"] for tool in payload["result"]["tools"]] == ["list_tables"]


@pytest.mark.asyncio
async def test_gateway_maps_ambiguous_write_failure_to_unknown_outcome() -> None:
    output = MemoryWriter()
    await saas_server._child_to_client(  # type: ignore[arg-type]
        reader_for(
            {
                "jsonrpc": "2.0",
                "id": 8,
                "result": {
                    "isError": True,
                    "content": [
                        {
                            "type": "text",
                            "text": saas_mcp.UNKNOWN_WRITE_OUTCOME_MARKER,
                        }
                    ],
                },
            }
        ),
        output,
        SAAS_ADAPTERS["airtable-mcp"],
        set(),
        {8},
        {8},
        {},
        set(),
        asyncio.Lock(),
    )
    payload = json.loads(bytes(output.data).decode("utf-8"))
    assert payload["error"]["code"] == -32008
    assert payload["error"]["data"] == {
        "reason": "unknown_outcome",
        "retryable": False,
    }


@pytest.mark.asyncio
async def test_gateway_maps_definite_write_failure_to_json_rpc_error() -> None:
    output = MemoryWriter()
    await saas_server._child_to_client(  # type: ignore[arg-type]
        reader_for(
            {
                "jsonrpc": "2.0",
                "id": 10,
                "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": "airtable_http_429"}],
                },
            }
        ),
        output,
        SAAS_ADAPTERS["airtable-mcp"],
        set(),
        {10},
        {10},
        {},
        set(),
        asyncio.Lock(),
    )
    payload = json.loads(bytes(output.data).decode("utf-8"))
    assert payload["error"]["code"] == -32009
    assert payload["error"]["data"] == {
        "reason": "rate_limited",
        "retryable": False,
    }


def test_timeout_error_has_machine_readable_unknown_outcome() -> None:
    payload = json.loads(
        saas_server._rpc_error(
            9,
            -32008,
            "timeout",
            data={"reason": "unknown_outcome", "retryable": False},
        )
    )
    assert payload["error"]["data"] == {"reason": "unknown_outcome", "retryable": False}
