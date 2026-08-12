from __future__ import annotations

import base64
import json
import socket
from pathlib import Path

import pytest

from server.mcp import database_proxy
from server.sandbox_sidecar import database_contracts
from server.sandbox_sidecar.database_contracts import (
    DATABASE_ADAPTERS,
    GRAPH_DATA_SERVICE_ADAPTERS,
    REMOTE_DATA_SERVICE_ADAPTERS,
    STAGED_DATABASE_ADAPTERS,
    bounded_rows,
    install_pinned_getaddrinfo,
    resolve_allowed_addresses,
    validate_configuration,
    validate_document,
    validate_readonly_sql,
)
from server.sandbox_sidecar.database_mcp import ADAPTER_TOOL_NAMES
from server.sandbox_sidecar import database_server


EXPECTED_TOOLS = {
    "dbhub": {"list_schemas", "list_tables", "describe_table", "execute_sql"},
    "mongodb-mcp": {
        "list_collections",
        "collection_schema",
        "collection_indexes",
        "find",
        "aggregate",
        "count_documents",
    },
    "clickhouse-mcp": {"list_databases", "list_tables", "run_query"},
    "redis-mcp": {
        "scan_keys",
        "get_value",
        "get_type",
        "get_ttl",
        "hash_get_all",
        "list_range",
        "set_members",
        "sorted_set_range",
    },
    "duckdb-mcp": {"list_schemas", "list_tables", "describe_table", "query"},
    "supabase-mcp": {"list_tables", "list_extensions", "execute_sql"},
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
    "zilliztech-mcp-server-milvus": {
        "list_collections",
        "describe_collection",
        "get_entities",
        "search_vectors",
    },
    "neo4j-contrib-mcp-neo4j": {"get_schema", "read_cypher"},
    "arcadedata-arcadedb": {"list_types", "describe_type", "read_query"},
    "greptimeteam-greptimedb-mcp-server": {
        "describe_table",
        "query_range",
        "health_check",
    },
}


def dbhub_configuration() -> dict[str, object]:
    return {
        "settings": {
            "engine": "postgresql",
            "host": "database.example.com",
            "port": 5432,
            "database": "analytics",
            "tls_mode": "verify-full",
            "username": "readonly",
        },
        "credentials": {"password": "secret"},
        "workspace_id": None,
    }


def test_database_contract_and_proxy_allowlists_are_exact() -> None:
    assert set(DATABASE_ADAPTERS) == set(EXPECTED_TOOLS)
    assert database_proxy.ALLOWED_ADAPTERS == set(EXPECTED_TOOLS)
    assert {
        adapter_id: set(tool_names)
        for adapter_id, tool_names in ADAPTER_TOOL_NAMES.items()
    } == EXPECTED_TOOLS
    assert STAGED_DATABASE_ADAPTERS == {"greptimeteam-greptimedb-mcp-server"}
    assert GRAPH_DATA_SERVICE_ADAPTERS == {
        "zilliztech-mcp-server-milvus",
        "neo4j-contrib-mcp-neo4j",
        "arcadedata-arcadedb",
    }
    assert REMOTE_DATA_SERVICE_ADAPTERS == {
        "pab1it0-prometheus-mcp-server",
        "qdrant-mcp-server-qdrant",
        "cr7258-elasticsearch-mcp-server",
        *GRAPH_DATA_SERVICE_ADAPTERS,
        *STAGED_DATABASE_ADAPTERS,
    }
    assert database_server.ALLOWED_ADAPTERS == set(EXPECTED_TOOLS) - set(STAGED_DATABASE_ADAPTERS)


def test_database_configuration_is_structured_and_tls_is_strict() -> None:
    validated = validate_configuration("dbhub", dbhub_configuration())
    assert validated.settings["host"] == "database.example.com"
    assert validated.settings["tls_mode"] == "verify-full"
    assert validated.credentials == {"password": "secret"}

    for key, value in (
        ("dsn", "postgresql://readonly:secret@database.example.com/app"),
        ("url", "https://database.example.com"),
        ("environment", {"PGPASSWORD": "secret"}),
        ("cwd", "/host"),
    ):
        invalid = dbhub_configuration()
        assert isinstance(invalid["settings"], dict)
        invalid["settings"][key] = value  # type: ignore[index]
        with pytest.raises(ValueError):
            validate_configuration("dbhub", invalid)

    for weak_mode in ("disable", "require", "verify-ca"):
        invalid = dbhub_configuration()
        assert isinstance(invalid["settings"], dict)
        invalid["settings"]["tls_mode"] = weak_mode  # type: ignore[index]
        with pytest.raises(ValueError, match="invalid_tls_mode"):
            validate_configuration("dbhub", invalid)

    unsupported = dbhub_configuration()
    assert isinstance(unsupported["settings"], dict)
    unsupported["settings"]["engine"] = "sqlserver"  # type: ignore[index]
    with pytest.raises(ValueError, match="invalid_engine"):
        validate_configuration("dbhub", unsupported)


def test_database_configuration_rejects_ip_literals_and_private_dns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    literal = dbhub_configuration()
    assert isinstance(literal["settings"], dict)
    literal["settings"]["host"] = "203.0.113.10"  # type: ignore[index]
    with pytest.raises(ValueError, match="ip_literal_denied"):
        validate_configuration("dbhub", literal)

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.20.30.40", 5432))
        ],
    )
    monkeypatch.delenv("MCP_DATABASE_PRIVATE_HOST_ALLOWLIST", raising=False)
    with pytest.raises(ValueError, match="database_host_private"):
        resolve_allowed_addresses("database.internal", 5432)
    monkeypatch.setenv(
        "MCP_DATABASE_PRIVATE_HOST_ALLOWLIST",
        "database.internal",
    )
    assert resolve_allowed_addresses("database.internal", 5432) == ("10.20.30.40",)


def test_pinned_dns_accepts_ascii_byte_hosts_used_by_http_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = socket.getaddrinfo
    monkeypatch.setattr(socket, "getaddrinfo", original)
    install_pinned_getaddrinfo("data.example.com", ("203.0.113.20",))

    records = socket.getaddrinfo(b"data.example.com", 443, type=socket.SOCK_STREAM)
    assert records[0][4] == ("203.0.113.20", 443)
    with pytest.raises(socket.gaierror, match="database DNS target denied"):
        socket.getaddrinfo(b"other.example.com", 443, type=socket.SOCK_STREAM)
    with pytest.raises(socket.gaierror, match="database DNS target denied"):
        socket.getaddrinfo(b"data.example.com\xff", 443, type=socket.SOCK_STREAM)


def test_database_landlock_does_not_apply_cross_container_nproc_limit() -> None:
    server_root = Path(__file__).parents[1]
    source = (
        server_root
        / "sandbox_sidecar"
        / "database_landlock_exec.py"
    ).read_text(encoding="utf-8")
    assert "setrlimit(resource.RLIMIT_NPROC" not in source
    assert "Docker pids cgroup is the authoritative per-container limit" in source
    compose = (server_root.parent / "docker-compose.yml").read_text(encoding="utf-8")
    remote_service = compose.split("  mcp-database:\n", 1)[1].split(
        "  mcp-database-local:\n", 1
    )[0]
    local_service = compose.split("  mcp-database-local:\n", 1)[1].split(
        "  omniroute:\n", 1
    )[0]
    assert "pids_limit: 128" in remote_service
    assert "pids_limit: 96" in local_service


@pytest.mark.parametrize(
    "query",
    [
        "SELECT 1; DELETE FROM users",
        "WITH removed AS (DELETE FROM users RETURNING *) SELECT * FROM removed",
        "SELECT * FROM read_csv('https://example.com/private.csv')",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT * INTO copied FROM users",
        "SELECT pg_advisory_lock(1)",
        'SELECT "pg_sleep"(1)',
        r'SELECT U&"pg\005fsleep"(1)',
        "SELECT `get_lock`('modelmirror', 15)",
        "SELECT /*!50000 SLEEP(1) */ 1",
        "SELECT /*M!100100 SLEEP(1) */ 1",
        "SELECT /*+ MAX_EXECUTION_TIME(999999) */ 1",
        "SELECT pg_advisory_xact_lock(1)",
        "SELECT pg_try_advisory_xact_lock(1)",
        "SELECT dblink_connect('host=external.example.com')",
        "SELECT * FROM OPENQUERY(remote, 'UPDATE t SET value=1')",
        "SELECT GET_LOCK('modelmirror', 15)",
        "COPY users TO '/tmp/users.csv'",
        "INSTALL httpfs",
    ],
)
def test_database_sql_policy_denies_mutation_and_external_access(query: str) -> None:
    with pytest.raises(ValueError):
        validate_readonly_sql(query, dialect="postgres")


def test_database_sql_and_document_policy_allow_bounded_reads() -> None:
    query = "WITH recent AS (SELECT id FROM events) SELECT * FROM recent"
    try:
        import sqlglot  # noqa: F401
    except ImportError:
        # The general server image deliberately does not carry database-sidecar
        # dependencies.  Missing the locked parser must fail closed; the
        # database image smoke covers the positive parsing and execution path.
        with pytest.raises(ValueError, match="query_parser_unavailable"):
            validate_readonly_sql(query, dialect="postgres")
    else:
        assert validate_readonly_sql(query, dialect="postgres").startswith("WITH")
    assert validate_document({"status": "active", "count": {"$gte": 1}}) == {
        "status": "active",
        "count": {"$gte": 1},
    }
    for dangerous in (
        [{"$out": "copied"}],
        [{"$merge": {"into": "copied"}}],
        [{"$project": {"value": {"$function": {"body": "return 1"}}}}],
    ):
        with pytest.raises(ValueError, match="dangerous_mongo_operator"):
            validate_document(dangerous, pipeline=True)
    rows, truncated = bounded_rows(list(range(1100)), max_rows=1000)
    assert len(rows) == 1000
    assert truncated is True


def test_duckdb_configuration_requires_only_an_opaque_workspace() -> None:
    value = validate_configuration(
        "duckdb-mcp",
        {
            "settings": {},
            "credentials": {},
            "workspace_id": "mcpws_0123456789abcdef0123456789abcdef",
        },
    )
    assert value.workspace_id == "mcpws_0123456789abcdef0123456789abcdef"
    for forbidden in ("/host/database.duckdb", "mcpws_bad", None):
        with pytest.raises(ValueError):
            validate_configuration(
                "duckdb-mcp",
                {"settings": {}, "credentials": {}, "workspace_id": forbidden},
            )


def test_supabase_project_ref_is_exactly_twenty_lowercase_letters() -> None:
    valid = {
        "settings": {"project_ref": "abcdefghijklmnopqrst"},
        "credentials": {"access_token": "secret"},
    }
    assert validate_configuration("supabase-mcp", valid).settings["project_ref"] == (
        "abcdefghijklmnopqrst"
    )
    for project_ref in ("abcdefghijklmnopqrs1", "ABCDEFGHIJKLMNOPQRST", "short"):
        invalid = json.loads(json.dumps(valid))
        invalid["settings"]["project_ref"] = project_ref
        with pytest.raises(ValueError, match="invalid_project_ref"):
            validate_configuration("supabase-mcp", invalid)


def test_database_proxy_consumes_one_shot_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = json.dumps(dbhub_configuration(), separators=(",", ":")).encode("utf-8")
    monkeypatch.setenv(
        "MCP_DATABASE_HANDSHAKE_B64",
        base64.urlsafe_b64encode(raw).decode("ascii"),
    )
    assert database_proxy._load_configuration() == dbhub_configuration()
    assert "MCP_DATABASE_HANDSHAKE_B64" not in database_proxy.os.environ
