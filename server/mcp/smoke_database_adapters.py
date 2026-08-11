"""Offline contract and policy smoke for Wave-5 database adapters.

This harness deliberately needs no database credentials or reachable database.
Live adapter smoke is performed separately against disposable containers; this
file guards the fixed tool surface and the fail-closed input policy first.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import socket
import tempfile
from pathlib import Path

try:
    from server.sandbox_sidecar.database_contracts import (
        DATABASE_ADAPTERS,
        bounded_rows,
        install_pinned_getaddrinfo,
        resolve_allowed_addresses,
        validate_configuration,
        validate_document,
        validate_readonly_sql,
    )
except ModuleNotFoundError as error:
    if error.name != "server":
        raise
    from sandbox_sidecar.database_contracts import (
        DATABASE_ADAPTERS,
        bounded_rows,
        install_pinned_getaddrinfo,
        resolve_allowed_addresses,
        validate_configuration,
        validate_document,
        validate_readonly_sql,
    )


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
        "execute_query", "execute_range_query", "list_metrics",
        "get_metric_metadata", "get_targets",
    },
    "qdrant-mcp-server-qdrant": {
        "get_collection_info", "scroll_points", "query_points",
    },
    "cr7258-elasticsearch-mcp-server": {
        "get_cluster_health", "get_index", "search_documents", "get_document",
    },
    "zilliztech-mcp-server-milvus": {
        "list_collections", "describe_collection", "get_entities", "search_vectors",
    },
    "neo4j-contrib-mcp-neo4j": {"get_schema", "read_cypher"},
    "arcadedata-arcadedb": {"list_types", "describe_type", "read_query"},
}

EXPECTED_SCHEMA_SHA256 = {
    "dbhub": "ea513fa4c4e822da4f057d1a598f7cbb959c7cbbfc074c459d4b11c641828c05",
    "mongodb-mcp": "131babe9cc1bce134d5a03bea187879190a0e3174d11a649fd00d4e3e2ba5f22",
    "clickhouse-mcp": "dcc776fb7a9f04f0ee74edffe03df72411f009e5ae5404eb00cb73d17fd1a4b2",
    "redis-mcp": "c92964bae88277fbd6b007afcacd42813db5f98ac44efe82389613870fb89b9f",
    "duckdb-mcp": "bcbc5ab6f79efe935694536b71e6558c7dce1e06a594ddb654c1b1b06bb13285",
    "supabase-mcp": "18bb488393b8a255825639a1dbbf4b77e70fca2398f6838210869caca172bbba",
    "pab1it0-prometheus-mcp-server": "44265e2144474e895d58010f2a80cb61efb381112978db81b180f2a960e46ff4",
    "qdrant-mcp-server-qdrant": "45b1380288c0f842e4a1487b1470f3231cbdb7158bae5829d8b3aadacdf53e44",
    "cr7258-elasticsearch-mcp-server": "d1ed0ec28c75c7faeb16ba461c07a508b5a4f7ecf0711b8f83ac2a4c29d09064",
}


VALID_CONFIGURATIONS = {
    "dbhub": {
        "settings": {
            "engine": "postgresql",
            "host": "db.example.com",
            "port": 5432,
            "database": "analytics",
            "tls_mode": "verify-full",
            "username": "readonly",
        },
        "credentials": {"password": "dummy"},
    },
    "mongodb-mcp": {
        "settings": {
            "host": "mongo.example.com",
            "port": 27017,
            "database": "analytics",
            "tls_mode": "verify-full",
            "username": "readonly",
            "auth_source": "admin",
        },
        "credentials": {"password": "dummy"},
    },
    "clickhouse-mcp": {
        "settings": {
            "host": "clickhouse.example.com",
            "port": 8443,
            "database": "analytics",
            "tls_mode": "verify-full",
            "username": "readonly",
        },
        "credentials": {"password": "dummy"},
    },
    "redis-mcp": {
        "settings": {
            "host": "redis.example.com",
            "port": 6380,
            "database": 0,
            "tls_mode": "verify-full",
        },
        "credentials": {"password": "dummy"},
    },
    "duckdb-mcp": {
        "settings": {},
        "credentials": {},
        "workspace_id": "mcpws_0123456789abcdef0123456789abcdef",
    },
    "supabase-mcp": {
        "settings": {"project_ref": "abcdefghijklmnopqrst"},
        "credentials": {"access_token": "dummy"},
    },
    "pab1it0-prometheus-mcp-server": {
        "settings": {
            "host": "prometheus.example.com",
            "port": 443,
            "tls_mode": "verify-full",
        },
        "credentials": {"bearer_token": "dummy"},
    },
    "qdrant-mcp-server-qdrant": {
        "settings": {
            "host": "qdrant.example.com",
            "port": 443,
            "tls_mode": "verify-full",
            "collection": "reviewed_vectors",
        },
        "credentials": {"api_key": "dummy"},
    },
    "cr7258-elasticsearch-mcp-server": {
        "settings": {
            "host": "elasticsearch.example.com",
            "port": 443,
            "tls_mode": "verify-full",
            "index": "reviewed-events",
            "search_field": "message",
            "username": "readonly",
        },
        "credentials": {"password": "dummy"},
    },
    "zilliztech-mcp-server-milvus": {
        "settings": {
            "host": "milvus.example.com",
            "port": 443,
            "tls_mode": "verify-full",
            "database": "project_graph",
            "collection": "project_vectors",
            "vector_field": "embedding",
            "output_fields": "id,title,category",
            "username": "readonly",
        },
        "credentials": {"password": "dummy"},
    },
    "neo4j-contrib-mcp-neo4j": {
        "settings": {
            "host": "neo4j.example.com",
            "port": 443,
            "tls_mode": "verify-full",
            "database": "project_graph",
            "username": "readonly",
        },
        "credentials": {"password": "dummy"},
    },
    "arcadedata-arcadedb": {
        "settings": {
            "host": "arcadedb.example.com",
            "port": 443,
            "tls_mode": "verify-full",
            "database": "project_graph",
            "username": "readonly",
        },
        "credentials": {"password": "dummy"},
    },
}


def _must_reject(function: object, *args: object, **kwargs: object) -> None:
    try:
        function(*args, **kwargs)  # type: ignore[operator]
    except ValueError:
        return
    raise RuntimeError(f"policy bypass was accepted: {getattr(function, '__name__', function)}")


async def _schema_smoke() -> None:
    try:
        from server.sandbox_sidecar.database_mcp import BUILDERS
        from server.sandbox_sidecar.database_graph_services import WAVE19B_SCHEMA_SHA256
    except ModuleNotFoundError as error:
        if error.name != "server":
            raise
        from sandbox_sidecar.database_mcp import BUILDERS
        from sandbox_sidecar.database_graph_services import WAVE19B_SCHEMA_SHA256

    for adapter_id, builder in BUILDERS.items():
        tools = await builder(None).list_tools()
        if adapter_id in WAVE19B_SCHEMA_SHA256:
            reviewed = [
                {"name": tool.name, "inputSchema": tool.inputSchema}
                for tool in sorted(tools, key=lambda item: item.name)
            ]
            digest = hashlib.sha256(
                json.dumps(reviewed, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            expected_digest = WAVE19B_SCHEMA_SHA256[adapter_id]
        else:
            schemas = {tool.name: tool.inputSchema for tool in tools}
            digest = hashlib.sha256(
                json.dumps(schemas, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            expected_digest = EXPECTED_SCHEMA_SHA256[adapter_id]
        if digest != expected_digest:
            raise RuntimeError(
                f"inputSchema drift for {adapter_id}: expected "
                f"{expected_digest}, got {digest}"
            )
        for tool in tools:
            annotations = tool.annotations
            if annotations is None or annotations.readOnlyHint is not True:
                raise RuntimeError(f"non-read-only annotation for {adapter_id}.{tool.name}")
            if annotations.destructiveHint is not False or annotations.openWorldHint is not False:
                raise RuntimeError(f"unsafe annotation drift for {adapter_id}.{tool.name}")
        if adapter_id == "duckdb-mcp":
            for tool in tools:
                marker = (
                    tool.inputSchema.get("properties", {})
                    .get("database_file_id", {})
                    .get("x-modelmirror-input")
                )
                if marker != "workspace-file":
                    raise RuntimeError(f"DuckDB workspace selector drift for {tool.name}")


async def _duckdb_adapter_smoke() -> dict[str, object]:
    try:
        from server.sandbox_sidecar.database_mcp import (
            DatabaseContext,
            _preflight_duckdb,
            build_duckdb,
        )
    except ModuleNotFoundError as error:
        if error.name != "server":
            raise
        from sandbox_sidecar.database_mcp import (
            DatabaseContext,
            _preflight_duckdb,
            build_duckdb,
        )

    import duckdb

    workspace_id = "mcpws_0123456789abcdef0123456789abcdef"
    environment_keys = (
        "MCP_DATABASE_CHILD_CONFIGURATION_B64",
        "MCP_DATABASE_PINNED_DNS_B64",
        "MCP_DATABASE_INPUT_ROOT",
    )
    previous_environment = {key: os.environ.get(key) for key in environment_keys}
    original_getaddrinfo = socket.getaddrinfo
    try:
        with tempfile.TemporaryDirectory(prefix="modelmirror-duckdb-smoke-") as temporary:
            input_root = Path(temporary)
            workspace_root = input_root / workspace_id
            workspace_root.mkdir()
            database_path = workspace_root / "reviewed.duckdb"
            connection = duckdb.connect(str(database_path))
            try:
                connection.execute("CREATE TABLE smoke(id INTEGER, label VARCHAR)")
                connection.execute("INSERT INTO smoke VALUES (1, 'ok'), (2, 'bounded')")
            finally:
                connection.close()

            configuration = {
                "settings": {},
                "credentials": {},
                "workspace_id": workspace_id,
            }
            os.environ["MCP_DATABASE_CHILD_CONFIGURATION_B64"] = base64.urlsafe_b64encode(
                json.dumps(configuration, separators=(",", ":")).encode("utf-8")
            ).decode("ascii")
            os.environ["MCP_DATABASE_PINNED_DNS_B64"] = base64.urlsafe_b64encode(
                json.dumps({"host": None, "addresses": []}, separators=(",", ":")).encode(
                    "utf-8"
                )
            ).decode("ascii")
            os.environ["MCP_DATABASE_INPUT_ROOT"] = str(input_root)

            context = DatabaseContext("duckdb-mcp")
            _preflight_duckdb(context)
            database_file_id = next(iter(context._duckdb_files))
            result = await build_duckdb(context).call_tool(
                "query",
                {
                    "database_file_id": database_file_id,
                    "query": "SELECT id, label FROM smoke ORDER BY id",
                    "max_rows": 1,
                    "timeout_seconds": 15,
                },
            )
            structured = (
                result[1]
                if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], dict)
                else result
            )
            if not isinstance(structured, dict):
                raise RuntimeError(f"DuckDB tool did not return structured content: {result!r}")
            if (
                structured.get("columns") != ["id", "label"]
                or structured.get("rows") != [{"id": 1, "label": "ok"}]
                or structured.get("row_count") != 1
                or structured.get("truncated") is not True
            ):
                raise RuntimeError(f"DuckDB actual tool call drift: {result!r}")
            return structured
    finally:
        socket.getaddrinfo = original_getaddrinfo  # type: ignore[assignment]
        for key, value in previous_environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


async def _gateway_redaction_smoke() -> None:
    try:
        from server.sandbox_sidecar.database_server import (
            _child_to_client,
            _terminate_timed_out_call,
        )
    except ModuleNotFoundError as error:
        if error.name != "server":
            raise
        from sandbox_sidecar.database_server import (
            _child_to_client,
            _terminate_timed_out_call,
        )

    class Writer:
        def __init__(self) -> None:
            self.output = bytearray()

        def write(self, value: bytes) -> None:
            self.output.extend(value)

        async def drain(self) -> None:
            return None

    reader = asyncio.StreamReader()
    reader.feed_data(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "error": {
                    "code": -32603,
                    "message": "password=secret host=/inputs/private.duckdb",
                },
            }
        ).encode("utf-8")
        + b"\n"
    )
    reader.feed_eof()
    writer = Writer()
    await _child_to_client(
        reader,
        writer,  # type: ignore[arg-type]
        "dbhub",
        set(),
        {7},
        {},
        set(),
        asyncio.Lock(),
    )
    output = writer.output.decode("utf-8")
    decoded = json.loads(output)
    if (
        "secret" in output
        or "/inputs/" in output
        or decoded.get("error", {}).get("code") != -32603
    ):
        raise RuntimeError("database gateway error redaction failed")

    class Process:
        def __init__(self) -> None:
            self.returncode: int | None = None
            self.killed = False

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

        async def wait(self) -> int:
            assert self.returncode is not None
            return self.returncode

    timeout_writer = Writer()
    process = Process()
    call_requests: set[object] = {11}
    deadlines: dict[object, asyncio.Task[None]] = {}
    suppressed: set[object] = set()
    timeout_task = asyncio.create_task(
        _terminate_timed_out_call(
            11,
            process,  # type: ignore[arg-type]
            timeout_writer,  # type: ignore[arg-type]
            asyncio.Lock(),
            call_requests,
            deadlines,
            suppressed,
            timeout_seconds=0.001,
        )
    )
    deadlines[11] = timeout_task
    await timeout_task
    timeout_payload = json.loads(timeout_writer.output.decode("utf-8"))
    if (
        not process.killed
        or process.returncode != -9
        or call_requests
        or 11 not in suppressed
        or timeout_payload.get("error", {}).get("code") != -32001
    ):
        raise RuntimeError("database gateway hard timeout did not terminate the child")


def _pinned_dns_smoke() -> None:
    original = socket.getaddrinfo
    try:
        install_pinned_getaddrinfo("db.example.com", ("93.184.216.34",))
        records = socket.getaddrinfo("db.example.com", 5432, type=socket.SOCK_STREAM)
        if {record[4][0] for record in records} != {"93.184.216.34"}:
            raise RuntimeError("pinned DNS did not preserve the reviewed address")
        try:
            socket.getaddrinfo("rebound.example.com", 5432, type=socket.SOCK_STREAM)
        except socket.gaierror:
            pass
        else:
            raise RuntimeError("pinned DNS allowed a different hostname")
    finally:
        socket.getaddrinfo = original  # type: ignore[assignment]


def _private_dns_smoke() -> None:
    original_getaddrinfo = socket.getaddrinfo
    previous_allowlist = os.environ.pop("MCP_DATABASE_PRIVATE_HOST_ALLOWLIST", None)
    try:
        socket.getaddrinfo = lambda *args, **kwargs: [  # type: ignore[assignment]
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.20.30.40", 5432))
        ]
        _must_reject(resolve_allowed_addresses, "db.example.com", 5432)

        os.environ["MCP_DATABASE_PRIVATE_HOST_ALLOWLIST"] = "db.example.com"
        if resolve_allowed_addresses("db.example.com", 5432) != ("10.20.30.40",):
            raise RuntimeError("administrator RFC1918 allowlist did not apply exactly")

        for denied_address in (
            "127.0.0.1",
            "169.254.169.254",
            "0.0.0.0",
            "::1",
            "fe80::1",
            "ff02::1",
        ):
            family = socket.AF_INET6 if ":" in denied_address else socket.AF_INET
            socket.getaddrinfo = lambda *args, _address=denied_address, _family=family, **kwargs: [  # type: ignore[assignment]
                (
                    _family,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (_address, 5432, 0, 0) if _family == socket.AF_INET6 else (_address, 5432),
                )
            ]
            try:
                resolve_allowed_addresses("db.example.com", 5432)
            except ValueError:
                pass
            else:
                raise RuntimeError(
                    f"administrator private allowlist accepted forbidden address: {denied_address}"
                )

        socket.getaddrinfo = lambda *args, **kwargs: [  # type: ignore[assignment]
            (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("fd00::123", 5432, 0, 0))
        ]
        if resolve_allowed_addresses("db.example.com", 5432) != ("fd00::123",):
            raise RuntimeError("administrator IPv6 ULA allowlist did not apply exactly")
    finally:
        socket.getaddrinfo = original_getaddrinfo  # type: ignore[assignment]
        if previous_allowlist is not None:
            os.environ["MCP_DATABASE_PRIVATE_HOST_ALLOWLIST"] = previous_allowlist


def main() -> None:
    if set(DATABASE_ADAPTERS) != set(EXPECTED_TOOLS):
        raise RuntimeError("Wave-5 database adapter ID drift")
    for adapter_id, expected in EXPECTED_TOOLS.items():
        actual = set(DATABASE_ADAPTERS[adapter_id].tools)
        if actual != expected:
            raise RuntimeError(f"tool schema drift for {adapter_id}: {sorted(actual)}")
        validate_configuration(adapter_id, VALID_CONFIGURATIONS[adapter_id])

    sqlserver_configuration = json.loads(json.dumps(VALID_CONFIGURATIONS["dbhub"]))
    sqlserver_configuration["settings"]["engine"] = "sqlserver"
    _must_reject(validate_configuration, "dbhub", sqlserver_configuration)

    for adapter_id in (
        "dbhub",
        "mongodb-mcp",
        "clickhouse-mcp",
        "redis-mcp",
        "pab1it0-prometheus-mcp-server",
        "qdrant-mcp-server-qdrant",
        "cr7258-elasticsearch-mcp-server",
        "zilliztech-mcp-server-milvus",
        "neo4j-contrib-mcp-neo4j",
        "arcadedata-arcadedb",
    ):
        unsafe_tls = json.loads(json.dumps(VALID_CONFIGURATIONS[adapter_id]))
        unsafe_tls["settings"]["tls_mode"] = "require"
        _must_reject(validate_configuration, adapter_id, unsafe_tls)
        unsafe_ip = json.loads(json.dumps(VALID_CONFIGURATIONS[adapter_id]))
        unsafe_ip["settings"]["host"] = "203.0.113.8"
        _must_reject(validate_configuration, adapter_id, unsafe_ip)

    unsafe_configuration = json.loads(json.dumps(VALID_CONFIGURATIONS["dbhub"]))
    unsafe_configuration["settings"]["dsn"] = "postgresql://user:secret@host/db"
    _must_reject(validate_configuration, "dbhub", unsafe_configuration)
    unsafe_configuration = json.loads(json.dumps(VALID_CONFIGURATIONS["redis-mcp"]))
    unsafe_configuration["environment"] = {"REDIS_URL": "redis://host"}
    _must_reject(validate_configuration, "redis-mcp", unsafe_configuration)
    unsafe_configuration = json.loads(json.dumps(VALID_CONFIGURATIONS["supabase-mcp"]))
    unsafe_configuration["settings"]["project_ref"] = "abcdefghijklmno12345"
    _must_reject(validate_configuration, "supabase-mcp", unsafe_configuration)
    _must_reject(
        validate_configuration,
        "duckdb-mcp",
        {"settings": {}, "credentials": {}, "workspace_id": "../../host"},
    )

    safe_queries = (
        ("SELECT id, name FROM users WHERE id = 1", "postgres"),
        ('SELECT "name" FROM "users"', "postgres"),
        ("SELECT `name` FROM `users`", "mysql"),
        ("WITH recent AS (SELECT id FROM events) SELECT * FROM recent", "postgres"),
        ("SELECT count() FROM events", "clickhouse"),
        ("SELECT * FROM local_table", "duckdb"),
    )
    for query, dialect in safe_queries:
        validate_readonly_sql(query, dialect=dialect)
    for query, dialect in (
        ("SELECT 1; DELETE FROM users", "postgres"),
        ("WITH changed AS (DELETE FROM users RETURNING *) SELECT * FROM changed", "postgres"),
        ("SELECT * INTO copied FROM users", "tsql"),
        ("SELECT pg_read_file('/etc/passwd')", "postgres"),
        ("SELECT pg_advisory_lock(1)", "postgres"),
        ("SELECT pg_try_advisory_lock(1)", "postgres"),
        ('SELECT "pg_sleep"(1)', "postgres"),
        ('SELECT U&"pg\\005fsleep"(1)', "postgres"),
        ('SELECT U&"safe_name" UESCAPE \'!\'', "postgres"),
        ("SELECT `get_lock`('modelmirror', 10)", "mysql"),
        ("SELECT `get\\_lock`('modelmirror', 10)", "mysql"),
        ('SELECT "pg_advisory_xact_lock"(1)', "postgres"),
        ('SELECT "pg_try_advisory_xact_lock_shared"(1)', "postgres"),
        ("SELECT dblink_connect('host=external.example.com')", "postgres"),
        ("SELECT dblink_send_query('x', 'SELECT 1')", "postgres"),
        ("SELECT GET_LOCK('modelmirror', 15)", "mysql"),
        ("SELECT /*!50000 SLEEP(1) */ 1", "mysql"),
        ("SELECT /*M!100100 SLEEP(1) */ 1", "mysql"),
        ("SELECT /*+ MAX_EXECUTION_TIME(999999) */ 1", "mysql"),
        ("SELECT * FROM OPENQUERY(remote_server, 'SELECT 1')", "tsql"),
        ("SELECT count() FROM events SETTINGS max_execution_time=0", "clickhouse"),
        ("SELECT * FROM users FOR SHARE", "postgres"),
        ("SELECT * FROM users FOR KEY SHARE", "postgres"),
        ("SELECT * FROM users FOR UPDATE", "postgres"),
        ("SELECT * FROM url('http://169.254.169.254/latest/meta-data')", "clickhouse"),
        ("SELECT * FROM postgresql('private.internal', 'db', 't', 'u', 'p')", "clickhouse"),
        (
            "SELECT * FROM urlCluster('cluster', "
            "'http://169.254.169.254/latest/meta-data/', 'CSV', 'x String')",
            "clickhouse",
        ),
        ("SELECT * FROM iceberg('http://169.254.169.254/bucket')", "clickhouse"),
        ("SELECT * FROM read_parquet('https://example.com/private.parquet')", "duckdb"),
        ("SELECT * FROM postgres_scan('host=private.internal')", "duckdb"),
        ("ATTACH 'host.db' AS host", "duckdb"),
    ):
        _must_reject(validate_readonly_sql, query, dialect=dialect)

    validate_document({"status": "ok", "score": {"$gte": 1}})
    validate_document([{"$match": {"status": "ok"}}, {"$limit": 5}], pipeline=True)
    _must_reject(validate_document, [{"$out": "copied"}], pipeline=True)
    _must_reject(validate_document, {"$where": "sleep(1000)"})
    _must_reject(validate_document, [{"$project": {"x": {"$function": {}}}}], pipeline=True)

    rows, truncated = bounded_rows([{"id": value} for value in range(1_050)], max_rows=1_000)
    if len(rows) != 1_000 or not truncated:
        raise RuntimeError("row limit policy failed")

    _private_dns_smoke()
    _pinned_dns_smoke()
    asyncio.run(_schema_smoke())
    asyncio.run(_gateway_redaction_smoke())
    duckdb_result = asyncio.run(_duckdb_adapter_smoke())

    print(
        json.dumps(
            {
                "ok": True,
                "adapters": {key: sorted(value) for key, value in EXPECTED_TOOLS.items()},
                "configuration_policy": "passed",
                "sql_bypass_policy": "passed",
                "mongodb_bypass_policy": "passed",
                "row_output_policy": "passed",
                "input_schema_snapshots": "passed",
                "pinned_dns_policy": "passed",
                "private_dns_policy": "passed",
                "error_redaction": "passed",
                "hard_timeout": "passed",
                "duckdb_actual_call": duckdb_result,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
