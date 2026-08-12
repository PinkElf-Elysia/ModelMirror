from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from server.mcp import database_proxy
from server.mcp.catalog import CATALOG_ADAPTERS
from server.mcp.catalog_expansion_v3 import CATALOG_EXPANSION_V3_ADAPTERS
from server.sandbox_sidecar import database_server
from server.sandbox_sidecar import database_wave27 as services
from server.sandbox_sidecar.database_contracts import (
    DATABASE_ADAPTERS,
    STAGED_DATABASE_ADAPTERS,
    WAVE_TWENTYSEVEN_DATA_SERVICE_ADAPTERS,
    validate_configuration,
)
from server.sandbox_sidecar.database_mcp import BUILDERS, PREFLIGHTS


ADAPTER_ID = "greptimeteam-greptimedb-mcp-server"
EXPECTED_TOOLS = {"describe_table", "query_range", "health_check"}


def _configuration() -> dict[str, object]:
    return {
        "settings": {
            "host": "greptime.example.com",
            "port": 443,
            "database": "public",
            "table": "project_metrics",
            "time_column": "observed_at",
            "value_column": "metric_value",
            "tls_mode": "verify-full",
            "username": "modelmirror_ro",
        },
        "credentials": {"password": "read-password"},
    }


def _context() -> SimpleNamespace:
    validated = validate_configuration(ADAPTER_ID, _configuration())
    return SimpleNamespace(
        adapter_id=ADAPTER_ID,
        settings=validated.settings,
        credentials=validated.credentials,
    )


@pytest.mark.asyncio
async def test_wave27_is_compiled_but_staged_and_not_catalog_executable() -> None:
    assert WAVE_TWENTYSEVEN_DATA_SERVICE_ADAPTERS == {ADAPTER_ID}
    assert STAGED_DATABASE_ADAPTERS == {ADAPTER_ID}
    assert ADAPTER_ID in DATABASE_ADAPTERS
    assert ADAPTER_ID in database_proxy.ALLOWED_ADAPTERS
    assert ADAPTER_ID in BUILDERS
    assert ADAPTER_ID in PREFLIGHTS
    assert ADAPTER_ID not in database_server.ALLOWED_ADAPTERS
    manifest = CATALOG_ADAPTERS[ADAPTER_ID]
    assert manifest.availability == "planned"
    assert manifest.executable is False
    assert manifest.server_command == ()
    assert manifest.tool_policies == {}

    catalog = {item.project_id: item for item in CATALOG_EXPANSION_V3_ADAPTERS}
    assert catalog[ADAPTER_ID].availability == "planned"
    assert catalog[ADAPTER_ID].decision_reason_code == "planned-wave27-native-readonly-data-service"

    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    assert f"MCP_DATABASE_ALLOWED_ADAPTERS: {ADAPTER_ID}" not in compose
    allowlist_line = next(
        line for line in compose.splitlines() if "MCP_DATABASE_ALLOWED_ADAPTERS:" in line
    )
    assert ADAPTER_ID not in allowlist_line


@pytest.mark.asyncio
async def test_wave27_tool_contract_is_exact_read_only_and_digest_locked() -> None:
    tools = await BUILDERS[ADAPTER_ID](_context()).list_tools()
    assert {tool.name for tool in tools} == EXPECTED_TOOLS
    reviewed = [
        {"name": tool.name, "inputSchema": tool.inputSchema}
        for tool in sorted(tools, key=lambda item: item.name)
    ]
    digest = hashlib.sha256(
        json.dumps(
            reviewed,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert digest == services.WAVE27_SCHEMA_SHA256[ADAPTER_ID]

    for tool in tools:
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.destructiveHint is False
        schema = json.dumps(tool.inputSchema, sort_keys=True).lower()
        for forbidden in (
            "query",
            "sql",
            "tql",
            "url",
            "dsn",
            "header",
            "environment",
            "command",
            "table",
            "database",
            "host",
            "path",
        ):
            assert f'"{forbidden}"' not in schema


def test_wave27_upstream_identity_is_frozen() -> None:
    assert services.WAVE27_UPSTREAM_LOCKS == {
        ADAPTER_ID: {
            "version": "v0.5.1",
            "commit": "ba3b732fe2113378f41c391da880b9ab75f2d862",
            "license": "MIT",
            "repository": "GreptimeTeam/greptimedb-mcp-server",
        }
    }


def test_wave27_configuration_binds_target_and_plaintext_is_test_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validated = validate_configuration(ADAPTER_ID, _configuration())
    assert validated.settings["table"] == "project_metrics"
    assert validated.settings["time_column"] == "observed_at"
    assert validated.settings["value_column"] == "metric_value"
    assert validated.credentials == {"password": "read-password"}

    for key in ("url", "dsn", "headers", "environment", "command", "cwd", "query"):
        invalid = json.loads(json.dumps(_configuration()))
        invalid["settings"][key] = "denied"  # type: ignore[index]
        with pytest.raises(ValueError):
            validate_configuration(ADAPTER_ID, invalid)

    plaintext = json.loads(json.dumps(_configuration()))
    plaintext["settings"]["tls_mode"] = "test-only-plaintext"  # type: ignore[index]
    monkeypatch.delenv("MCP_DATABASE_TEST_ALLOW_PLAINTEXT", raising=False)
    with pytest.raises(ValueError, match="invalid_tls_mode"):
        validate_configuration(ADAPTER_ID, plaintext)
    monkeypatch.setenv("MCP_DATABASE_TEST_ALLOW_PLAINTEXT", "true")
    assert validate_configuration(ADAPTER_ID, plaintext).settings["tls_mode"] == "test-only-plaintext"


def test_wave27_arguments_are_structured_and_bounded() -> None:
    services.validate_wave27_arguments(ADAPTER_ID, "describe_table", {})
    services.validate_wave27_arguments(ADAPTER_ID, "health_check", {})
    services.validate_wave27_arguments(
        ADAPTER_ID,
        "query_range",
        {"start": "2026-08-01T00:00:00Z", "end": "2026-08-01T01:00:00Z", "limit": 25},
    )
    for invalid in (
        {"start": "2026-08-01T00:00:00Z", "end": "2026-08-02T00:00:01Z"},
        {"start": "2026-08-01T01:00:00Z", "end": "2026-08-01T00:00:00Z"},
        {"start": "now()", "end": "2026-08-01T01:00:00Z"},
        {"start": "2026-08-01T00:00:00Z", "end": "2026-08-01T01:00:00Z", "limit": 201},
        {"start": "2026-08-01T00:00:00Z", "end": "2026-08-01T01:00:00Z", "query": "DROP TABLE x"},
    ):
        with pytest.raises(ValueError):
            services.validate_wave27_arguments(ADAPTER_ID, "query_range", invalid)


def _greptime_payload(rows: list[list[Any]], columns: list[str]) -> dict[str, Any]:
    return {
        "code": 0,
        "output": [
            {
                "records": {
                    "schema": {"column_schemas": [{"name": name} for name in columns]},
                    "rows": rows,
                }
            }
        ],
    }


@pytest.mark.asyncio
async def test_wave27_calls_only_server_generated_sql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statements: list[str] = []

    def fake_sql(_context: Any, sql: str) -> dict[str, Any]:
        statements.append(sql)
        if "information_schema.columns" in sql:
            return _greptime_payload(
                [["observed_at", "TimestampMillisecond", "TIMESTAMP", "NO"]],
                ["column_name", "data_type", "semantic_type", "is_nullable"],
            )
        if "modelmirror_readonly" in sql:
            return _greptime_payload([[1]], ["modelmirror_readonly"])
        return _greptime_payload(
            [["2026-08-01T00:00:00Z", 1.5]],
            ["observed_at", "metric_value"],
        )

    monkeypatch.setattr(services, "_greptime_sql", fake_sql)
    mcp = BUILDERS[ADAPTER_ID](_context())
    assert await mcp.call_tool("describe_table", {})
    assert await mcp.call_tool(
        "query_range",
        {"start": "2026-08-01T00:00:00+00:00", "end": "2026-08-01T01:00:00Z", "limit": 10},
    )
    assert await mcp.call_tool("health_check", {})

    assert len(statements) == 3
    assert "`project_metrics`" in statements[1]
    assert "`observed_at`" in statements[1]
    assert "`metric_value`" in statements[1]
    assert "2026-08-01T00:00:00Z" in statements[1]
    assert statements[1].endswith("LIMIT 11")
    assert all("DROP" not in sql and ";" not in sql for sql in statements)


def test_wave27_record_parser_is_bounded_and_fail_closed() -> None:
    payload = _greptime_payload([[1], [2], [3]], ["value"])
    assert services._greptime_records(payload, limit=2) == {
        "columns": ["value"],
        "rows": [[1], [2]],
        "returned_count": 2,
        "truncated": True,
    }
    for invalid in (
        {},
        {"code": 1, "output": []},
        {"code": 0, "output": []},
        _greptime_payload([[1, 2]], ["value"]),
    ):
        with pytest.raises(ValueError, match="greptime_response_invalid"):
            services._greptime_records(invalid, limit=10)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (429, "database_rate_limited"),
        (503, "database_upstream_unavailable"),
        (403, "database_provider_rejected"),
    ],
)
def test_wave27_provider_failures_map_to_fixed_codes(status: int, expected: str) -> None:
    class Response:
        status_code = status
        headers: dict[str, str] = {}

        def iter_bytes(self) -> list[bytes]:
            return []

    with pytest.raises(ValueError, match=expected):
        services._bounded_json_response(Response())  # type: ignore[arg-type]
