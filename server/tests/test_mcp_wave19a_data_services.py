from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from server.mcp import database_proxy
from server.mcp.catalog import CATALOG_ADAPTERS
from server.sandbox_sidecar import database_data_services as services
from server.sandbox_sidecar import database_server
from server.sandbox_sidecar.database_contracts import (
    DATABASE_ADAPTERS,
    STAGED_DATABASE_ADAPTERS,
    validate_configuration,
)
from server.sandbox_sidecar.database_mcp import BUILDERS, PREFLIGHTS


WAVE19A = {
    "pab1it0-prometheus-mcp-server",
    "qdrant-mcp-server-qdrant",
    "cr7258-elasticsearch-mcp-server",
}

EXPECTED_TOOLS = {
    "pab1it0-prometheus-mcp-server": {
        "execute_query",
        "execute_range_query",
        "list_metrics",
        "get_metric_metadata",
        "get_targets",
    },
    "qdrant-mcp-server-qdrant": {
        "get_collection_info",
        "scroll_points",
        "query_points",
    },
    "cr7258-elasticsearch-mcp-server": {
        "get_cluster_health",
        "get_index",
        "search_documents",
        "get_document",
    },
}


def _config(adapter_id: str) -> dict[str, object]:
    settings: dict[str, object] = {
        "host": "data.example.com",
        "port": 443,
        "tls_mode": "verify-full",
    }
    credentials: dict[str, str] = {}
    if adapter_id == "pab1it0-prometheus-mcp-server":
        credentials = {"bearer_token": "read-token"}
    elif adapter_id == "qdrant-mcp-server-qdrant":
        settings["collection"] = "project_vectors"
        credentials = {"api_key": "read-key"}
    else:
        settings.update(index="project-events", search_field="message", username="readonly")
        credentials = {"password": "read-password"}
    return {"settings": settings, "credentials": credentials}


def _context(adapter_id: str) -> SimpleNamespace:
    validated = validate_configuration(adapter_id, _config(adapter_id))
    return SimpleNamespace(
        adapter_id=adapter_id,
        settings=validated.settings,
        credentials=validated.credentials,
    )


@pytest.mark.asyncio
async def test_wave19a_contracts_are_exact_and_read_only() -> None:
    assert WAVE19A.isdisjoint(STAGED_DATABASE_ADAPTERS)
    assert WAVE19A <= set(DATABASE_ADAPTERS)
    assert WAVE19A <= database_proxy.ALLOWED_ADAPTERS
    assert WAVE19A <= set(BUILDERS) == set(PREFLIGHTS)
    for adapter_id, expected in EXPECTED_TOOLS.items():
        tools = await BUILDERS[adapter_id](_context(adapter_id)).list_tools()
        assert {tool.name for tool in tools} == expected
        for tool in tools:
            assert tool.annotations is not None
            assert tool.annotations.readOnlyHint is True
            schema = json.dumps(tool.inputSchema, sort_keys=True)
            for forbidden in ("url", "dsn", "headers", "environment", "command", "cwd"):
                assert f'"{forbidden}"' not in schema
    assert "qdrant-store" not in EXPECTED_TOOLS["qdrant-mcp-server-qdrant"]
    assert "general_api_request" not in EXPECTED_TOOLS["cr7258-elasticsearch-mcp-server"]


def test_wave19a_configuration_is_fixed_and_plaintext_is_test_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for adapter_id in WAVE19A:
        validated = validate_configuration(adapter_id, _config(adapter_id))
        assert validated.settings["host"] == "data.example.com"
        assert validated.settings["tls_mode"] == "verify-full"
        for key in ("url", "dsn", "headers", "environment", "command", "cwd"):
            invalid = json.loads(json.dumps(_config(adapter_id)))
            invalid["settings"][key] = "denied"  # type: ignore[index]
            with pytest.raises(ValueError):
                validate_configuration(adapter_id, invalid)

        plaintext = json.loads(json.dumps(_config(adapter_id)))
        plaintext["settings"]["tls_mode"] = "test-only-plaintext"  # type: ignore[index]
        monkeypatch.delenv("MCP_DATABASE_TEST_ALLOW_PLAINTEXT", raising=False)
        with pytest.raises(ValueError, match="invalid_tls_mode"):
            validate_configuration(adapter_id, plaintext)
        monkeypatch.setenv("MCP_DATABASE_TEST_ALLOW_PLAINTEXT", "true")
        assert validate_configuration(adapter_id, plaintext).settings["tls_mode"] == "test-only-plaintext"


def test_provider_timeout_precedes_the_gateway_hard_deadline() -> None:
    assert services.PROVIDER_TIMEOUT_SECONDS == 12.0
    assert services.PROVIDER_TIMEOUT_SECONDS < database_server.TOOL_CALL_TIMEOUT_SECONDS


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (429, "database_rate_limited"),
        (503, "database_upstream_unavailable"),
        (403, "database_provider_rejected"),
    ],
)
def test_provider_failures_map_to_fixed_codes(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    expected: str,
) -> None:
    class Response:
        status_code = status
        headers: dict[str, str] = {}

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def iter_bytes(self) -> list[bytes]:
            return []

    class Client:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> "Client":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def stream(self, *_args: object, **_kwargs: object) -> Response:
            return Response()

    monkeypatch.setattr(services.httpx, "Client", Client)
    with pytest.raises(ValueError, match=expected):
        services._request_json(_context("pab1it0-prometheus-mcp-server"), "GET", "/fixed")


@pytest.mark.parametrize(
    "query",
    ["", "sum((up)", "x" * 4097, "sum(" * 65 + "up" + ")" * 65],
)
def test_prometheus_query_policy_rejects_invalid_or_unbounded_input(query: str) -> None:
    with pytest.raises(ValueError):
        services.validate_promql(query)


def test_prometheus_range_and_target_policy_is_bounded() -> None:
    assert services.validate_prometheus_range(
        "2026-01-01T00:00:00Z",
        "2026-01-01T01:00:00Z",
        "15s",
    ) == ("2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z", "15s")
    with pytest.raises(ValueError, match="too_dense"):
        services.validate_prometheus_range("0", "86400", "15s")
    with pytest.raises(ValueError, match="target_state_denied"):
        services.validate_data_service_arguments(
            "pab1it0-prometheus-mcp-server", "get_targets", {"state": "dropped"}
        )


def test_qdrant_vector_and_elasticsearch_inputs_are_bounded() -> None:
    assert services.validate_vector([0, 1.25, -2]) == [0.0, 1.25, -2.0]
    for vector in ([], [float("nan")], [0.0] * 4097):
        with pytest.raises(ValueError, match="invalid_qdrant_vector"):
            services.validate_vector(vector)
    with pytest.raises(ValueError, match="invalid_document_id"):
        services.validate_data_service_arguments(
            "cr7258-elasticsearch-mcp-server", "get_document", {"id": "../../secret"}
        )


@pytest.mark.asyncio
async def test_wave19a_representative_calls_use_only_fixed_provider_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str, Any]] = []

    def fake_request(
        context: Any,
        method: str,
        path: str,
        *,
        params: Any = None,
        payload: Any = None,
    ) -> Any:
        calls.append((context.adapter_id, method, path, payload))
        if context.adapter_id == "pab1it0-prometheus-mcp-server":
            return {"status": "success", "data": {"resultType": "vector", "result": []}}
        if context.adapter_id == "qdrant-mcp-server-qdrant":
            return {"status": "ok", "result": {"points": [], "next_page_offset": None}}
        return {"hits": {"hits": []}}

    monkeypatch.setattr(services, "_request_json", fake_request)
    assert await BUILDERS["pab1it0-prometheus-mcp-server"](
        _context("pab1it0-prometheus-mcp-server")
    ).call_tool("execute_query", {"query": "up"})
    assert await BUILDERS["qdrant-mcp-server-qdrant"](
        _context("qdrant-mcp-server-qdrant")
    ).call_tool("query_points", {"vector": [0.1, 0.2], "limit": 5})
    assert await BUILDERS["cr7258-elasticsearch-mcp-server"](
        _context("cr7258-elasticsearch-mcp-server")
    ).call_tool("search_documents", {"query": "error", "max_rows": 5})
    assert calls == [
        ("pab1it0-prometheus-mcp-server", "GET", "/api/v1/query", None),
        (
            "qdrant-mcp-server-qdrant",
            "POST",
            "/collections/project_vectors/points/query",
            {"query": [0.1, 0.2], "limit": 5, "with_payload": True, "with_vector": False},
        ),
        (
            "cr7258-elasticsearch-mcp-server",
            "POST",
            "/project-events/_search",
            {
                "size": 5,
                "track_total_hits": False,
                "query": {"match": {"message": {"query": "error"}}},
            },
        ),
    ]


def test_wave19a_is_ready_in_catalog_and_exact_default_allowlist() -> None:
    root = Path(__file__).resolve().parents[2]
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    configured = compose.split("MCP_DATABASE_ALLOWED_ADAPTERS:", 1)[1].splitlines()[0]
    assert all(adapter_id in configured for adapter_id in WAVE19A)
    generated = (root / "server" / "mcp" / "catalog_expansion_v2.py").read_text(encoding="utf-8")
    for adapter_id in WAVE19A:
        block = generated.split(f"project_id='{adapter_id}'", 1)[1].split("CatalogExpansionV2Adapter(", 1)[0]
        assert "availability='ready'" in block
        assert "ready-isolated-readonly-data-service-facade" in block
        manifest = CATALOG_ADAPTERS[adapter_id]
        assert manifest.wave == 19
        assert manifest.availability == "ready"
        assert manifest.enabled_by_default is True
        assert manifest.executable is True
        assert manifest.runtime_image == "modelmirror-mcp-database:wave5-v1"
        assert set(manifest.tool_policies) == EXPECTED_TOOLS[adapter_id]
        assert all(policy.read_only for policy in manifest.tool_policies.values())
