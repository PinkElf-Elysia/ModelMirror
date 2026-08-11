from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from server.mcp import database_proxy
from server.mcp.catalog_expansion_v2 import CATALOG_EXPANSION_V2_ADAPTERS
from server.sandbox_sidecar import database_graph_services as services
from server.sandbox_sidecar import database_server
from server.sandbox_sidecar.database_contracts import (
    DATABASE_ADAPTERS,
    GRAPH_DATA_SERVICE_ADAPTERS,
    STAGED_DATABASE_ADAPTERS,
    validate_configuration,
)
from server.sandbox_sidecar.database_mcp import BUILDERS, PREFLIGHTS


WAVE19B = {
    "zilliztech-mcp-server-milvus",
    "neo4j-contrib-mcp-neo4j",
    "arcadedata-arcadedb",
}

EXPECTED_TOOLS = {
    "zilliztech-mcp-server-milvus": {
        "list_collections",
        "describe_collection",
        "get_entities",
        "search_vectors",
    },
    "neo4j-contrib-mcp-neo4j": {"get_schema", "read_cypher"},
    "arcadedata-arcadedb": {"list_types", "describe_type", "read_query"},
}


def _configuration(adapter_id: str) -> dict[str, object]:
    settings: dict[str, object] = {
        "host": "graph.example.com",
        "port": 443,
        "tls_mode": "verify-full",
        "database": "project_graph",
        "username": "modelmirror_reader",
    }
    if adapter_id == "zilliztech-mcp-server-milvus":
        settings.update(
            collection="project_vectors",
            vector_field="embedding",
            output_fields="id,title,category",
        )
    return {"settings": settings, "credentials": {"password": "read-password"}}


def _context(adapter_id: str) -> SimpleNamespace:
    validated = validate_configuration(adapter_id, _configuration(adapter_id))
    return SimpleNamespace(
        adapter_id=adapter_id,
        settings=validated.settings,
        credentials=validated.credentials,
    )


@pytest.mark.asyncio
async def test_wave19b_contracts_are_exact_read_only_and_default_off() -> None:
    assert WAVE19B == GRAPH_DATA_SERVICE_ADAPTERS == STAGED_DATABASE_ADAPTERS
    assert WAVE19B <= set(DATABASE_ADAPTERS)
    assert WAVE19B <= database_proxy.ALLOWED_ADAPTERS
    assert WAVE19B <= set(BUILDERS) == set(PREFLIGHTS)
    assert WAVE19B.isdisjoint(database_server.ALLOWED_ADAPTERS)
    for adapter_id, expected in EXPECTED_TOOLS.items():
        tools = await BUILDERS[adapter_id](_context(adapter_id)).list_tools()
        assert {tool.name for tool in tools} == expected
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
        assert digest == services.WAVE19B_SCHEMA_SHA256[adapter_id]
        for tool in tools:
            assert tool.annotations is not None
            assert tool.annotations.readOnlyHint is True
            schema = json.dumps(tool.inputSchema, sort_keys=True)
            for forbidden in ("url", "dsn", "headers", "environment", "command", "cwd", "procedure"):
                assert f'"{forbidden}"' not in schema


def test_wave19b_upstream_identity_is_frozen() -> None:
    assert services.WAVE19B_UPSTREAM_LOCKS == {
        "zilliztech-mcp-server-milvus": {
            "version": "0.1.1",
            "commit": "a7e624f3057a0d739528bca3ed92504943224ceb",
            "license": "Apache-2.0",
        },
        "neo4j-contrib-mcp-neo4j": {
            "version": "mcp-neo4j-cypher-v0.6.0",
            "commit": "dbc01ba78f171851f2d57dcd125b028c29912fd1",
            "license": "MIT",
        },
        "arcadedata-arcadedb": {
            "version": "26.8.1",
            "commit": "87bdc67f1f0331fa2d07e932a550064c118eae70",
            "license": "Apache-2.0",
        },
    }


def test_wave19b_configuration_binds_resource_and_rejects_connection_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for adapter_id in WAVE19B:
        validated = validate_configuration(adapter_id, _configuration(adapter_id))
        assert validated.settings["host"] == "graph.example.com"
        assert validated.settings["database"] == "project_graph"
        assert validated.settings["username"] == "modelmirror_reader"
        for key in ("url", "dsn", "headers", "environment", "command", "cwd"):
            invalid = json.loads(json.dumps(_configuration(adapter_id)))
            invalid["settings"][key] = "denied"  # type: ignore[index]
            with pytest.raises(ValueError):
                validate_configuration(adapter_id, invalid)

        plaintext = json.loads(json.dumps(_configuration(adapter_id)))
        plaintext["settings"]["tls_mode"] = "test-only-plaintext"  # type: ignore[index]
        monkeypatch.delenv("MCP_DATABASE_TEST_ALLOW_PLAINTEXT", raising=False)
        with pytest.raises(ValueError, match="invalid_tls_mode"):
            validate_configuration(adapter_id, plaintext)
        monkeypatch.setenv("MCP_DATABASE_TEST_ALLOW_PLAINTEXT", "true")
        assert validate_configuration(adapter_id, plaintext).settings["tls_mode"] == "test-only-plaintext"

    invalid_fields = _configuration("zilliztech-mcp-server-milvus")
    invalid_fields["settings"]["output_fields"] = "id,id"  # type: ignore[index]
    with pytest.raises(ValueError, match="invalid_output_fields"):
        validate_configuration("zilliztech-mcp-server-milvus", invalid_fields)


@pytest.mark.parametrize(
    "query",
    [
        "MATCH (n) DELETE n RETURN n",
        "MATCH (n) CALL db.labels() RETURN n",
        "MATCH (n) RETURN n; MATCH (m) RETURN m",
        "LOAD CSV FROM 'https://example.com/data.csv' AS row RETURN row",
        "MATCH (n) RETURN apoc.convert.toJson(n)",
        "CREATE (n) RETURN n",
    ],
)
def test_neo4j_query_policy_denies_writes_procedures_and_external_access(query: str) -> None:
    with pytest.raises(ValueError, match="neo4j_query_denied"):
        services.validate_neo4j_cypher(query)


def test_neo4j_query_policy_allows_bounded_parameterized_reads() -> None:
    assert services.validate_neo4j_cypher(
        "MATCH (n:Person) WHERE n.name = $name RETURN n.name LIMIT 10"
    ).startswith("MATCH")
    assert services.validate_neo4j_cypher(
        "MATCH (n) WHERE n.text = 'DELETE is text' RETURN n"
    ).startswith("MATCH")
    assert services.validate_query_parameters({"name": "Ada", "ids": [1, 2]}) == {
        "name": "Ada",
        "ids": [1, 2],
    }
    with pytest.raises(ValueError):
        services.validate_query_parameters({"bad-key": "value"})


@pytest.mark.parametrize(
    "query",
    [
        "UPDATE Person SET active = false",
        "DELETE FROM Person",
        "SELECT read('file:///etc/passwd')",
        "SELECT FROM Person; DROP TYPE Person",
        "PROFILE SELECT FROM Person",
        "IMPORT DATABASE https://example.com/db.zip",
    ],
)
def test_arcadedb_query_policy_denies_writes_admin_and_external_access(query: str) -> None:
    with pytest.raises(ValueError):
        services.validate_arcade_query(query)


def test_arcadedb_query_policy_allows_read_forms() -> None:
    assert services.validate_arcade_query("SELECT name FROM Person WHERE active = :active LIMIT 10")
    assert services.validate_arcade_query("MATCH {type: Person, as: p} RETURN $paths")
    assert services.validate_arcade_query("TRAVERSE out() FROM :rid MAXDEPTH 2")


def test_milvus_entity_and_vector_inputs_are_bounded() -> None:
    services.validate_graph_service_arguments(
        "zilliztech-mcp-server-milvus", "get_entities", {"ids": [1, "person-2"]}
    )
    services.validate_graph_service_arguments(
        "zilliztech-mcp-server-milvus", "search_vectors", {"vector": [0.1, 0.2], "limit": 5}
    )
    for ids in ([], [True], ["x"] * 101):
        with pytest.raises(ValueError, match="invalid_entity_ids"):
            services.validate_graph_service_arguments(
                "zilliztech-mcp-server-milvus", "get_entities", {"ids": ids}
            )


@pytest.mark.asyncio
async def test_wave19b_representative_calls_use_only_fixed_provider_paths(
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
        del params
        calls.append((context.adapter_id, method, path, payload))
        if context.adapter_id == "zilliztech-mcp-server-milvus":
            return {"code": 0, "data": []}
        if context.adapter_id == "neo4j-contrib-mcp-neo4j":
            return {"data": {"fields": ["name"], "values": [["Ada"]]}, "queryType": "r"}
        return {"result": [{"name": "Person"}]}

    monkeypatch.setattr(services, "_request_json", fake_request)
    assert await BUILDERS["zilliztech-mcp-server-milvus"](
        _context("zilliztech-mcp-server-milvus")
    ).call_tool("search_vectors", {"vector": [0.1, 0.2], "limit": 5})
    assert await BUILDERS["neo4j-contrib-mcp-neo4j"](
        _context("neo4j-contrib-mcp-neo4j")
    ).call_tool("read_cypher", {"query": "MATCH (n) RETURN n.name AS name", "max_rows": 5})
    assert await BUILDERS["arcadedata-arcadedb"](
        _context("arcadedata-arcadedb")
    ).call_tool("list_types", {})

    assert calls[0] == (
        "zilliztech-mcp-server-milvus",
        "POST",
        "/v2/vectordb/entities/search",
        {
            "dbName": "project_graph",
            "collectionName": "project_vectors",
            "data": [[0.1, 0.2]],
            "annsField": "embedding",
            "outputFields": ["id", "title", "category"],
            "limit": 5,
        },
    )
    assert calls[1][0:3] == (
        "neo4j-contrib-mcp-neo4j",
        "POST",
        "/db/project_graph/query/v2",
    )
    assert calls[1][3]["statement"].startswith("CALL { MATCH")
    assert calls[2] == (
        "arcadedata-arcadedb",
        "POST",
        "/api/v1/query/project_graph",
        {
            "language": "sql",
            "command": "SELECT name, type, records FROM schema:types ORDER BY name",
            "params": {},
            "limit": 101,
            "serializer": "record",
        },
    )


def test_provider_query_type_drift_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        services,
        "_request_json",
        lambda *_args, **_kwargs: {
            "data": {"fields": ["n"], "values": []},
            "queryType": "rw",
        },
    )
    with pytest.raises(ValueError, match="database_readonly_violation"):
        services._neo4j_query(_context("neo4j-contrib-mcp-neo4j"), "RETURN 1")


def test_wave19b_stays_planned_and_absent_from_default_compose_allowlist() -> None:
    by_id = {adapter.project_id: adapter for adapter in CATALOG_EXPANSION_V2_ADAPTERS}
    for adapter_id in WAVE19B:
        adapter = by_id[adapter_id]
        assert adapter.availability == "planned"
        assert adapter.decision_reason_code == "planned-read-only-data-facade"
    root = Path(__file__).resolve().parents[2]
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    configured = compose.split("MCP_DATABASE_ALLOWED_ADAPTERS:", 1)[1].splitlines()[0]
    assert all(adapter_id not in configured for adapter_id in WAVE19B)
