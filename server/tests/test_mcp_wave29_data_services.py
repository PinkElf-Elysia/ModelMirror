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
from server.sandbox_sidecar import database_wave29 as services
from server.sandbox_sidecar.database_contracts import validate_configuration
from server.sandbox_sidecar.database_mcp import BUILDERS, PREFLIGHTS
from server.sandbox_sidecar.smoke_database_wave29 import VICTORIA_TEST_SERVICE_IMAGE


ADAPTER_ID = services.VICTORIA_ADAPTER_ID
EXPECTED_TOOLS = {"metrics", "labels", "query", "query_range"}


def _configuration() -> dict[str, object]:
    return {
        "settings": {
            "host": "metrics.example.com",
            "port": 443,
            "tls_mode": "verify-full",
            "metric": "modelmirror_requests_total",
        },
        "credentials": {"bearer_token": "read-token"},
    }


def _context() -> SimpleNamespace:
    validated = validate_configuration(ADAPTER_ID, _configuration())
    return SimpleNamespace(
        adapter_id=ADAPTER_ID,
        settings=validated.settings,
        credentials=validated.credentials,
    )


def _digest(tools: list[object]) -> str:
    reviewed = [
        {"name": tool.name, "inputSchema": tool.inputSchema}
        for tool in sorted(tools, key=lambda item: item.name)
    ]
    return hashlib.sha256(
        json.dumps(reviewed, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@pytest.mark.asyncio
async def test_wave30_victoriametrics_contract_is_exact_and_read_only() -> None:
    assert ADAPTER_ID in database_proxy.ALLOWED_ADAPTERS
    assert ADAPTER_ID in database_server.ALLOWED_ADAPTERS
    assert ADAPTER_ID in BUILDERS
    assert ADAPTER_ID in PREFLIGHTS
    tools = await BUILDERS[ADAPTER_ID](_context()).list_tools()
    assert {tool.name for tool in tools} == EXPECTED_TOOLS
    assert _digest(tools) == services.WAVE29_DATABASE_SCHEMA_SHA256[ADAPTER_ID]
    for tool in tools:
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is True
        schema = json.dumps(tool.inputSchema, sort_keys=True).lower()
        for forbidden in ("query", "promql", "url", "dsn", "header", "environment", "command", "host"):
            assert f'"{forbidden}"' not in schema


def test_wave30_victoriametrics_identity_configuration_and_catalog_are_frozen() -> None:
    assert services.WAVE29_DATABASE_UPSTREAM_LOCKS[ADAPTER_ID] == {
        "version": "v1.20.2",
        "commit": "28a8c2319a8893d30a8b023b0c62734d31a5fe4e",
        "license": "Apache-2.0",
        "repository": "VictoriaMetrics/mcp-victoriametrics",
    }
    validated = validate_configuration(ADAPTER_ID, _configuration())
    assert validated.settings["metric"] == "modelmirror_requests_total"
    assert validated.credentials == {"bearer_token": "read-token"}
    for key in ("url", "dsn", "headers", "environment", "command", "query"):
        invalid = json.loads(json.dumps(_configuration()))
        invalid["settings"][key] = "denied"  # type: ignore[index]
        with pytest.raises(ValueError):
            validate_configuration(ADAPTER_ID, invalid)

    expansion = {item.project_id: item for item in CATALOG_EXPANSION_V3_ADAPTERS}
    assert expansion[ADAPTER_ID].availability == "ready"
    assert expansion[ADAPTER_ID].decision_reason_code == "ready-wave30-victoriametrics-readonly"
    manifest = CATALOG_ADAPTERS[ADAPTER_ID]
    assert manifest.wave == 30
    assert manifest.availability == "ready"
    assert manifest.executable is True
    assert manifest.server_command[-1] == ADAPTER_ID
    assert set(manifest.tool_policies) == EXPECTED_TOOLS
    assert all(policy.effect == "read" for policy in manifest.tool_policies.values())


def test_wave30_victoriametrics_arguments_and_results_are_bounded() -> None:
    services.validate_wave29_database_arguments(ADAPTER_ID, "metrics", {})
    services.validate_wave29_database_arguments(ADAPTER_ID, "query", {})
    services.validate_wave29_database_arguments(
        ADAPTER_ID,
        "query_range",
        {
            "start": "2026-08-01T00:00:00Z",
            "end": "2026-08-01T01:00:00Z",
            "step_seconds": 60,
        },
    )
    for invalid in (
        {"start": "2026-08-01T00:00:00Z", "end": "2026-08-02T00:00:01Z"},
        {"start": "2026-08-01T01:00:00Z", "end": "2026-08-01T00:00:00Z"},
        {"start": "2026-08-01T00:00:00Z", "end": "2026-08-01T01:00:00Z", "query": "up"},
        {"start": "2026-08-01T00:00:00Z", "end": "2026-08-01T01:00:00Z", "step_seconds": 0},
    ):
        with pytest.raises(ValueError):
            services.validate_wave29_database_arguments(ADAPTER_ID, "query_range", invalid)

    result = services._bounded_query(
        {
            "status": "success",
            "data": {
                "resultType": "vector",
                "result": [
                    {"metric": {"__name__": "modelmirror_requests_total"}, "value": [1, "2"]}
                ],
            },
        },
        range_query=False,
    )
    assert result["series_count"] == 1
    assert result["series"][0]["metric"]["__name__"] == "modelmirror_requests_total"
    database_server._validate_tool_arguments(ADAPTER_ID, "query", {})
    with pytest.raises(ValueError):
        database_server._validate_tool_arguments(
            ADAPTER_ID,
            "query",
            {"query": "up"},
        )


@pytest.mark.asyncio
async def test_wave30_victoriametrics_tools_use_only_the_configured_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, str | int]]] = []

    def fake_request(_context: Any, path: str, params: dict[str, str | int]) -> Any:
        calls.append((path, params))
        if path.endswith("/values") or path.endswith("/labels"):
            return {"status": "success", "data": ["modelmirror_requests_total"]}
        sample_key = "values" if path.endswith("query_range") else "value"
        sample = [[1, "2"]] if sample_key == "values" else [1, "2"]
        return {
            "status": "success",
            "data": {
                "resultType": "matrix" if sample_key == "values" else "vector",
                "result": [{"metric": {"__name__": "modelmirror_requests_total"}, sample_key: sample}],
            },
        }

    monkeypatch.setattr(services, "_request", fake_request)
    mcp = BUILDERS[ADAPTER_ID](_context())
    assert await mcp.call_tool("metrics", {})
    assert await mcp.call_tool("labels", {})
    assert await mcp.call_tool("query", {})
    assert await mcp.call_tool(
        "query_range",
        {
            "start": "2026-08-01T00:00:00Z",
            "end": "2026-08-01T00:05:00Z",
            "step_seconds": 60,
        },
    )
    assert calls[2][1]["query"] == "modelmirror_requests_total"
    assert calls[3][1]["query"] == "modelmirror_requests_total"


@pytest.mark.parametrize("series_count", [0, 1])
def test_wave30_victoriametrics_preflight_requires_a_representative_series(
    monkeypatch: pytest.MonkeyPatch,
    series_count: int,
) -> None:
    monkeypatch.setattr(
        services,
        "_request",
        lambda *_args, **_kwargs: {
            "status": "success",
            "data": {
                "resultType": "vector",
                "result": [
                    {
                        "metric": {"__name__": "modelmirror_requests_total"},
                        "value": [1, "1"],
                    }
                ][:series_count],
            },
        },
    )
    if series_count:
        services.preflight_victoriametrics(_context())
    else:
        with pytest.raises(RuntimeError, match="database_preflight_failed"):
            services.preflight_victoriametrics(_context())


@pytest.mark.parametrize(
    ("status", "expected"),
    [(429, "database_rate_limited"), (503, "database_upstream_unavailable"), (403, "database_provider_rejected")],
)
def test_wave30_victoriametrics_provider_failures_are_fixed(status: int, expected: str) -> None:
    response = type(
        "Response",
        (),
        {"status_code": status, "headers": {}, "iter_bytes": lambda self: []},
    )()
    with pytest.raises(ValueError, match=expected):
        services._bounded_response(response)


def test_wave30_victoriametrics_default_allowlist_is_exact() -> None:
    root = Path(__file__).resolve().parents[2]
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    proxy = (root / "server/mcp/database_proxy.py").read_text(encoding="utf-8")
    dockerfile = (root / "server/sandbox_sidecar/Dockerfile.database").read_text(
        encoding="utf-8"
    )
    assert ADAPTER_ID in compose
    assert ADAPTER_ID in proxy
    assert "database_wave29.py" in dockerfile
    assert "smoke_database_wave29.py" in dockerfile
    assert "smoke_database_wave29 --contract-only" in dockerfile
    assert VICTORIA_TEST_SERVICE_IMAGE == (
        "victoriametrics/victoria-metrics:v1.148.0@"
        "sha256:407013e902f9a0ba1d4b2d4c077c47bbaf917c893c52ff39b19efe83a654afda"
    )
