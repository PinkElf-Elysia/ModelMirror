from __future__ import annotations

import base64
import json
import socket

import pytest

from server.mcp import database_proxy
from server.sandbox_sidecar import database_contracts
from server.sandbox_sidecar.database_contracts import (
    DATABASE_ADAPTERS,
    bounded_rows,
    resolve_allowed_addresses,
    validate_configuration,
    validate_document,
    validate_readonly_sql,
)
from server.sandbox_sidecar.database_mcp import ADAPTER_TOOL_NAMES


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
