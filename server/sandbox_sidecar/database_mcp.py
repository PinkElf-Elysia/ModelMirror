"""Built-in, fixed-tool Wave-5 database MCP adapters.

Each process serves exactly one reviewed adapter.  Remote connection material
arrives through a private sidecar handshake, is removed from the environment
at startup, and is never included in tool results or error messages.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import datetime as dt
import decimal
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Annotated, Any, Iterable, Iterator

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .database_contracts import (
    DATABASE_ADAPTERS,
    FILE_ID_PATTERN,
    MAX_OUTPUT_BYTES,
    SAFE_IDENTIFIER,
    ValidatedConfiguration,
    bounded_rows,
    clamp_rows,
    clamp_timeout,
    install_pinned_getaddrinfo,
    resolve_allowed_addresses,
    validate_configuration,
    validate_document,
    validate_readonly_sql,
)
from .database_data_services import (
    build_elasticsearch,
    build_prometheus,
    build_qdrant,
    preflight_elasticsearch,
    preflight_prometheus,
    preflight_qdrant,
)
from .database_graph_services import (
    build_arcadedb,
    build_milvus,
    build_neo4j,
    preflight_arcadedb,
    preflight_milvus,
    preflight_neo4j,
)
from .database_wave27 import build_greptime, preflight_greptime


READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
DatabaseFileId = Annotated[
    str,
    Field(
        description="从当前封存工作区选择 .duckdb 文件；不接受路径或 URI。",
        json_schema_extra={"x-modelmirror-input": "workspace-file"},
    ),
]


def opaque_file_id(workspace_id: str, relative_path: str) -> str:
    digest = hashlib.sha256(f"{workspace_id}:{relative_path}".encode("utf-8")).hexdigest()[:24]
    return f"mcpf_{digest}"


def _load_configuration(adapter_id: str) -> ValidatedConfiguration:
    encoded = os.environ.pop("MCP_DATABASE_CHILD_CONFIGURATION_B64", "")
    if not encoded or len(encoded) > 256 * 1024:
        raise RuntimeError("数据库配置握手缺失。")
    try:
        raw = base64.urlsafe_b64decode(encoded.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("数据库配置握手无效。") from exc
    return validate_configuration(adapter_id, payload)


def _safe_identifier(value: object, label: str) -> str:
    clean = str(value or "").strip()
    if not SAFE_IDENTIFIER.fullmatch(clean):
        raise ValueError(f"{label} 标识无效。")
    return clean


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, (dt.date, dt.datetime, dt.time)):
        return value.isoformat()
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return {"encoding": "base64", "data": base64.b64encode(value).decode("ascii")}
    if isinstance(value, dict):
        return {str(key): _json_value(child) for key, child in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(child) for child in value]
    return str(value)


def _bounded_sequence(rows: Iterable[Any], max_rows: int) -> tuple[list[Any], bool]:
    values: list[Any] = []
    used_bytes = 2
    for item in rows:
        if len(values) >= max_rows:
            return values, True
        normalized = _json_value(item)
        encoded = json.dumps(
            normalized, ensure_ascii=False, default=str, separators=(",", ":")
        ).encode("utf-8")
        if used_bytes + len(encoded) + 1 > MAX_OUTPUT_BYTES - 16 * 1024:
            return values, True
        values.append(normalized)
        used_bytes += len(encoded) + 1
    return values, False


def _result_payload(columns: list[str], rows: Iterable[Any], max_rows: int) -> dict[str, Any]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if len(normalized) >= max_rows:
            return {
                "columns": columns,
                "rows": normalized,
                "row_count": len(normalized),
                "truncated": True,
            }
        if isinstance(row, dict):
            item = {str(key): _json_value(value) for key, value in row.items()}
        else:
            item = {
                column: _json_value(row[index] if index < len(row) else None)
                for index, column in enumerate(columns)
            }
        candidate = normalized + [item]
        if len(
            json.dumps(candidate, ensure_ascii=False, default=str, separators=(",", ":")).encode("utf-8")
        ) > MAX_OUTPUT_BYTES - 16 * 1024:
            return {
                "columns": columns,
                "rows": normalized,
                "row_count": len(normalized),
                "truncated": True,
            }
        normalized.append(item)
    return {
        "columns": columns,
        "rows": normalized,
        "row_count": len(normalized),
        "truncated": False,
    }


def _sql_wrapper(query: str, max_rows: int, *, engine: str) -> str:
    clean = query.rstrip().rstrip(";").rstrip()
    return f"SELECT * FROM ({clean}) AS modelmirror_readonly LIMIT {max_rows + 1}"


class DatabaseContext:
    def __init__(self, adapter_id: str) -> None:
        self.adapter_id = adapter_id
        self.configuration = _load_configuration(adapter_id)
        self.settings = self.configuration.settings
        self.credentials = self.configuration.credentials
        self.workspace_id = self.configuration.workspace_id
        self.resolved_addresses: tuple[str, ...] = ()
        target_host: str | None = None
        if "host" in self.settings:
            target_host = str(self.settings["host"])
        elif adapter_id == "supabase-mcp":
            target_host = "api.supabase.com"
        encoded_pins = os.environ.pop("MCP_DATABASE_PINNED_DNS_B64", "")
        if encoded_pins:
            try:
                pin_payload = json.loads(base64.urlsafe_b64decode(encoded_pins.encode("ascii")))
                if not isinstance(pin_payload, dict) or set(pin_payload) != {"host", "addresses"}:
                    raise ValueError("invalid_pinned_dns")
                if pin_payload.get("host") != target_host or not isinstance(pin_payload.get("addresses"), list):
                    raise ValueError("invalid_pinned_dns")
                self.resolved_addresses = tuple(str(item) for item in pin_payload["addresses"])
            except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
                raise RuntimeError("数据库 DNS 固定握手无效。") from exc
        elif target_host is not None:
            # Direct invocation is supported for image diagnostics only.  The
            # production sidecar always supplies its reviewed DNS answer.
            port = int(self.settings.get("port") or 443)
            self.resolved_addresses = resolve_allowed_addresses(target_host, port)
        install_pinned_getaddrinfo(target_host, self.resolved_addresses)
        self._duckdb_files: dict[str, Path] = {}
        if adapter_id == "duckdb-mcp":
            self._index_duckdb_files()

    def _index_duckdb_files(self) -> None:
        assert self.workspace_id is not None
        base = Path(os.getenv("MCP_DATABASE_INPUT_ROOT", "/inputs")).resolve()
        root = (base / self.workspace_id).resolve()
        if root.parent != base or not root.is_dir() or root.is_symlink():
            raise RuntimeError("封存 DuckDB 工作区不可用。")
        for path in sorted(root.rglob("*")):
            if path.is_symlink() or not path.is_file() or path.suffix.lower() != ".duckdb":
                continue
            resolved = path.resolve()
            if root not in resolved.parents:
                continue
            relative = path.relative_to(root).as_posix()
            self._duckdb_files[opaque_file_id(self.workspace_id, relative)] = path
        if not self._duckdb_files:
            raise RuntimeError("封存工作区中没有 .duckdb 文件。")

    def duckdb_file(self, file_id: str) -> Path:
        if not FILE_ID_PATTERN.fullmatch(str(file_id or "")):
            raise ValueError("必须选择当前封存工作区中的 DuckDB 文件。")
        path = self._duckdb_files.get(file_id)
        if path is None or not path.is_file() or path.is_symlink():
            raise ValueError("所选 DuckDB 文件不存在。")
        return path


@contextlib.contextmanager
def _dbhub_cursor(
    context: DatabaseContext,
    *,
    timeout_seconds: int,
) -> Iterator[tuple[Any, str]]:
    settings = context.settings
    engine = str(settings["engine"])
    host = str(settings["host"])
    port = int(settings["port"])
    database = str(settings["database"])
    username = str(settings["username"])
    password = context.credentials["password"]
    tls_mode = str(settings["tls_mode"])
    connection: Any = None
    cursor: Any = None
    try:
        if engine == "postgresql":
            import psycopg

            connection = psycopg.connect(
                host=host,
                hostaddr=context.resolved_addresses[0],
                port=port,
                dbname=database,
                user=username,
                password=password,
                sslmode=tls_mode,
                connect_timeout=min(timeout_seconds, 10),
                options=(
                    "-c default_transaction_read_only=on "
                    f"-c statement_timeout={timeout_seconds * 1000} -c lock_timeout=3000"
                ),
                autocommit=False,
            )
            cursor = connection.cursor()
            cursor.execute("BEGIN READ ONLY")
            placeholder = "%s"
        elif engine in {"mysql", "mariadb"}:
            import certifi
            import pymysql

            ssl: dict[str, Any] | None = None
            ssl = {"check_hostname": True, "ca": certifi.where()}
            connection = pymysql.connect(
                host=host,
                port=port,
                database=database,
                user=username,
                password=password,
                connect_timeout=min(timeout_seconds, 10),
                read_timeout=timeout_seconds,
                write_timeout=timeout_seconds,
                ssl=ssl,
                autocommit=False,
                local_infile=False,
            )
            cursor = connection.cursor()
            # Fail closed if the server cannot establish a read-only transaction.
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
            try:
                cursor.execute(f"SET SESSION MAX_EXECUTION_TIME={timeout_seconds * 1000}")
            except Exception:
                cursor.execute(f"SET SESSION max_statement_time={timeout_seconds}")
            cursor.execute("START TRANSACTION READ ONLY")
            placeholder = "%s"
        else:
            raise RuntimeError("unsupported_database_engine")
        yield cursor, placeholder
    finally:
        if connection is not None:
            try:
                connection.rollback()
            except Exception:
                pass
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


def _cursor_payload(cursor: Any, max_rows: int) -> dict[str, Any]:
    columns = [str(item[0]) for item in (cursor.description or [])]
    def rows() -> Iterator[Any]:
        if not columns:
            return
        for _ in range(max_rows + 1):
            item = cursor.fetchone()
            if item is None:
                return
            yield item
    return _result_payload(columns, rows(), max_rows)


def _preflight_dbhub(context: DatabaseContext) -> None:
    engine = str(context.settings["engine"])
    with _dbhub_cursor(context, timeout_seconds=10) as (cursor, _):
        if engine == "postgresql":
            cursor.execute("SELECT current_setting('transaction_read_only')")
            value = str(cursor.fetchone()[0]).lower()
            if value not in {"on", "true", "1"}:
                raise RuntimeError("database_readonly_preflight_failed")
        elif engine in {"mysql", "mariadb"}:
            try:
                cursor.execute("SELECT @@transaction_read_only")
            except Exception:
                cursor.execute("SELECT @@tx_read_only")
            if int(cursor.fetchone()[0]) != 1:
                raise RuntimeError("database_readonly_preflight_failed")
        else:
            raise RuntimeError("unsupported_database_engine")
        cursor.execute("SELECT 1")
        if int(cursor.fetchone()[0]) != 1:
            raise RuntimeError("database_preflight_failed")


def build_dbhub(context: DatabaseContext) -> FastMCP:
    mcp = FastMCP("ModelMirror DBHub Read Only")

    @mcp.tool(annotations=READ_ONLY)
    def list_schemas(max_rows: int = 200) -> dict[str, Any]:
        """列出当前受控数据库账号可见的 schema。"""
        limit = clamp_rows(max_rows)
        with _dbhub_cursor(context, timeout_seconds=15) as (cursor, _):
            cursor.execute(
                _sql_wrapper(
                    "SELECT schema_name FROM information_schema.schemata ORDER BY schema_name",
                    limit,
                    engine=str(context.settings["engine"]),
                )
            )
            return _cursor_payload(cursor, limit)

    @mcp.tool(annotations=READ_ONLY)
    def list_tables(schema: str = "", max_rows: int = 200) -> dict[str, Any]:
        """列出当前数据库中的表和视图，可按 schema 收窄。"""
        limit = clamp_rows(max_rows)
        schema_name = _safe_identifier(schema, "Schema") if schema else ""
        engine = str(context.settings["engine"])
        with _dbhub_cursor(context, timeout_seconds=15) as (cursor, placeholder):
            query = "SELECT table_schema, table_name, table_type FROM information_schema.tables"
            params: tuple[Any, ...] = ()
            if schema_name:
                query += f" WHERE table_schema = {placeholder}"
                params = (schema_name,)
            query += " ORDER BY table_schema, table_name"
            cursor.execute(_sql_wrapper(query, limit, engine=engine), params)
            return _cursor_payload(cursor, limit)

    @mcp.tool(annotations=READ_ONLY)
    def describe_table(schema: str, table: str, max_rows: int = 200) -> dict[str, Any]:
        """读取指定表的列定义，不执行 DDL。"""
        limit = clamp_rows(max_rows)
        schema_name = _safe_identifier(schema, "Schema")
        table_name = _safe_identifier(table, "表")
        engine = str(context.settings["engine"])
        with _dbhub_cursor(context, timeout_seconds=15) as (cursor, placeholder):
            query = (
                "SELECT column_name, data_type, is_nullable, column_default, ordinal_position "
                "FROM information_schema.columns "
                f"WHERE table_schema = {placeholder} AND table_name = {placeholder} "
                "ORDER BY ordinal_position"
            )
            cursor.execute(_sql_wrapper(query, limit, engine=engine), (schema_name, table_name))
            return _cursor_payload(cursor, limit)

    @mcp.tool(annotations=READ_ONLY)
    def execute_sql(query: str, max_rows: int = 200, timeout_seconds: int = 15) -> dict[str, Any]:
        """在只读事务中执行单条 SELECT/WITH 查询。"""
        limit = clamp_rows(max_rows)
        timeout = clamp_timeout(timeout_seconds)
        engine = str(context.settings["engine"])
        dialect = {"postgresql": "postgres", "mysql": "mysql", "mariadb": "mysql"}[engine]
        clean = validate_readonly_sql(query, dialect=dialect)
        with _dbhub_cursor(context, timeout_seconds=timeout) as (cursor, _):
            cursor.execute(_sql_wrapper(clean, limit, engine=engine))
            return _cursor_payload(cursor, limit)

    return mcp


@contextlib.contextmanager
def _mongo_database(context: DatabaseContext, timeout: int = 15) -> Iterator[Any]:
    import certifi
    import pymongo

    tls_mode = str(context.settings["tls_mode"])
    client = pymongo.MongoClient(
        host=str(context.settings["host"]),
        port=int(context.settings["port"]),
        username=str(context.settings["username"]),
        password=context.credentials["password"],
        authSource=str(context.settings["auth_source"]),
        directConnection=True,
        tls=True,
        tlsCAFile=certifi.where(),
        tlsAllowInvalidCertificates=False,
        tlsAllowInvalidHostnames=False,
        serverSelectionTimeoutMS=min(timeout, 10) * 1000,
        connectTimeoutMS=min(timeout, 10) * 1000,
        socketTimeoutMS=timeout * 1000,
        retryWrites=False,
        appname="ModelMirror-Wave5-ReadOnly",
    )
    try:
        database = client[str(context.settings["database"])]
        database.command({"ping": 1}, maxTimeMS=min(timeout, 10) * 1000)
        yield database
    finally:
        client.close()


def _mongo_collection(value: str) -> str:
    clean = _safe_identifier(value, "集合")
    if clean.startswith("system."):
        raise ValueError("系统集合不可访问。")
    return clean


def build_mongodb(context: DatabaseContext) -> FastMCP:
    mcp = FastMCP("ModelMirror MongoDB Read Only")

    @mcp.tool(annotations=READ_ONLY)
    def list_collections(max_rows: int = 200) -> dict[str, Any]:
        """列出当前绑定数据库中的非系统集合。"""
        limit = clamp_rows(max_rows)
        with _mongo_database(context) as database:
            rows = [
                {"name": name}
                for name in sorted(database.list_collection_names())
                if not name.startswith("system.")
            ]
        values, truncated = bounded_rows(rows, max_rows=limit)
        return {"collections": values, "count": len(values), "truncated": truncated}

    @mcp.tool(annotations=READ_ONLY)
    def collection_schema(collection: str, sample_size: int = 100) -> dict[str, Any]:
        """从最多 200 份文档推断字段与 BSON 类型，不修改集合。"""
        name = _mongo_collection(collection)
        sample = max(1, min(int(sample_size), 200))
        fields: dict[str, set[str]] = {}
        with _mongo_database(context) as database:
            for document in database[name].find({}, limit=sample, max_time_ms=15_000):
                for key, value in document.items():
                    fields.setdefault(str(key), set()).add(type(value).__name__)
        return {"collection": name, "fields": [{"name": key, "types": sorted(types)} for key, types in sorted(fields.items())]}

    @mcp.tool(annotations=READ_ONLY)
    def collection_indexes(collection: str, max_rows: int = 200) -> dict[str, Any]:
        """读取集合索引元数据。"""
        name = _mongo_collection(collection)
        limit = clamp_rows(max_rows)
        with _mongo_database(context) as database:
            rows = database[name].list_indexes()
            values, truncated = _bounded_sequence(rows, limit)
        return {"collection": name, "indexes": values, "truncated": truncated}

    @mcp.tool(annotations=READ_ONLY)
    def find(
        collection: str,
        filter: dict[str, Any] | None = None,
        projection: dict[str, Any] | None = None,
        sort: dict[str, int] | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        """在当前数据库集合中执行结构化只读查询。"""
        name = _mongo_collection(collection)
        row_limit = clamp_rows(limit)
        query_filter = validate_document(filter or {})
        query_projection = validate_document(projection or {})
        query_sort = validate_document(sort or {})
        sort_items: list[tuple[str, int]] = []
        for key, direction in query_sort.items():
            if int(direction) not in {-1, 1}:
                raise ValueError("排序方向只能是 1 或 -1。")
            sort_items.append((str(key), int(direction)))
        with _mongo_database(context) as database:
            cursor = database[name].find(query_filter, query_projection or None, max_time_ms=15_000)
            if sort_items:
                cursor = cursor.sort(sort_items)
            values, truncated = _bounded_sequence(cursor.limit(row_limit + 1), row_limit)
        return {"collection": name, "documents": values, "count": len(values), "truncated": truncated}

    @mcp.tool(annotations=READ_ONLY)
    def aggregate(collection: str, pipeline: list[dict[str, Any]], limit: int = 200) -> dict[str, Any]:
        """执行不含 $out、$merge、脚本或服务端 JavaScript 的聚合。"""
        name = _mongo_collection(collection)
        row_limit = clamp_rows(limit)
        clean_pipeline = list(validate_document(pipeline, pipeline=True))
        clean_pipeline.append({"$limit": row_limit + 1})
        with _mongo_database(context) as database:
            rows = database[name].aggregate(clean_pipeline, maxTimeMS=15_000, allowDiskUse=False)
            values, truncated = _bounded_sequence(rows, row_limit)
        return {"collection": name, "documents": values, "count": len(values), "truncated": truncated}

    @mcp.tool(annotations=READ_ONLY)
    def count_documents(collection: str, filter: dict[str, Any] | None = None) -> dict[str, Any]:
        """统计结构化筛选命中的文档数量。"""
        name = _mongo_collection(collection)
        query_filter = validate_document(filter or {})
        with _mongo_database(context) as database:
            count = database[name].count_documents(query_filter, maxTimeMS=15_000)
        return {"collection": name, "count": int(count)}

    return mcp


def _preflight_mongodb(context: DatabaseContext) -> None:
    allowed_database = str(context.settings["database"])
    denied_actions = {
        "insert",
        "update",
        "remove",
        "createCollection",
        "createIndex",
        "dropCollection",
        "dropDatabase",
        "dropIndex",
        "renameCollectionSameDB",
        "bypassDocumentValidation",
        "convertToCapped",
        "collMod",
    }
    with _mongo_database(context, timeout=10) as database:
        status = database.command(
            {"connectionStatus": 1, "showPrivileges": True}, maxTimeMS=10_000
        )
        auth_info = status.get("authInfo") if isinstance(status, dict) else None
        roles = auth_info.get("authenticatedUserRoles") if isinstance(auth_info, dict) else None
        if not isinstance(roles, list) or not roles:
            raise RuntimeError("database_readonly_preflight_failed")
        role_scopes = {
            (str(item.get("role")), str(item.get("db")))
            for item in roles
            if isinstance(item, dict)
        }
        if role_scopes != {("read", allowed_database)}:
            raise RuntimeError("database_readonly_preflight_failed")
        privileges = auth_info.get("authenticatedUserPrivileges", [])
        if not isinstance(privileges, list):
            raise RuntimeError("database_readonly_preflight_failed")
        for privilege in privileges:
            actions = privilege.get("actions", []) if isinstance(privilege, dict) else []
            if set(map(str, actions)) & denied_actions:
                raise RuntimeError("database_readonly_preflight_failed")
        database.list_collection_names(filter={"name": {"$not": {"$regex": "^system\\."}}})


@contextlib.contextmanager
def _clickhouse_client(context: DatabaseContext, timeout: int = 15) -> Iterator[Any]:
    import clickhouse_connect

    tls_mode = str(context.settings["tls_mode"])
    client = clickhouse_connect.get_client(
        host=str(context.settings["host"]),
        port=int(context.settings["port"]),
        username=str(context.settings["username"]),
        password=context.credentials["password"],
        database=str(context.settings["database"]),
        secure=True,
        verify=True,
        connect_timeout=min(timeout, 10),
        send_receive_timeout=timeout,
        query_limit=1_001,
        settings={
            "readonly": 1,
            "max_execution_time": timeout,
            "max_result_rows": 1_001,
            "max_result_bytes": MAX_OUTPUT_BYTES,
            "result_overflow_mode": "throw",
        },
        product_name="ModelMirror-Wave5-ReadOnly",
    )
    try:
        yield client
    finally:
        client.close()


def _clickhouse_payload(result: Any, max_rows: int) -> dict[str, Any]:
    return _result_payload([str(item) for item in result.column_names], list(result.result_rows), max_rows)


def build_clickhouse(context: DatabaseContext) -> FastMCP:
    mcp = FastMCP("ModelMirror ClickHouse Read Only")

    @mcp.tool(annotations=READ_ONLY)
    def list_databases() -> dict[str, Any]:
        """返回当前固定数据库作用域，不枚举其他数据库。"""
        return {"databases": [{"name": str(context.settings["database"]), "scope": "configured"}]}

    @mcp.tool(annotations=READ_ONLY)
    def list_tables(max_rows: int = 200) -> dict[str, Any]:
        """列出当前固定 ClickHouse 数据库中的表。"""
        limit = clamp_rows(max_rows)
        database = str(context.settings["database"])
        with _clickhouse_client(context) as client:
            result = client.query(
                "SELECT name, engine, total_rows, total_bytes FROM system.tables "
                "WHERE database = {database:String} ORDER BY name LIMIT {limit:UInt32}",
                parameters={"database": database, "limit": limit + 1},
            )
        return _clickhouse_payload(result, limit)

    @mcp.tool(annotations=READ_ONLY)
    def run_query(query: str, max_rows: int = 200, timeout_seconds: int = 15) -> dict[str, Any]:
        """以 readonly=1 执行单条 ClickHouse SELECT/WITH 查询。"""
        limit = clamp_rows(max_rows)
        timeout = clamp_timeout(timeout_seconds)
        clean = validate_readonly_sql(query, dialect="clickhouse")
        with _clickhouse_client(context, timeout) as client:
            result = client.query(
                _sql_wrapper(clean, limit, engine="clickhouse"),
                settings={
                    "readonly": 1,
                    "max_execution_time": timeout,
                    "max_result_rows": limit + 1,
                    "max_result_bytes": MAX_OUTPUT_BYTES,
                    "result_overflow_mode": "throw",
                },
            )
        return _clickhouse_payload(result, limit)

    return mcp


def _preflight_clickhouse(context: DatabaseContext) -> None:
    with _clickhouse_client(context, timeout=10) as client:
        readonly = client.query("SELECT getSetting('readonly')").first_row[0]
        if int(readonly) not in {1, 2}:
            raise RuntimeError("database_readonly_preflight_failed")
        if int(client.query("SELECT 1").first_row[0]) != 1:
            raise RuntimeError("database_preflight_failed")


@contextlib.contextmanager
def _redis_client(context: DatabaseContext, timeout: int = 15) -> Iterator[Any]:
    import redis

    tls_mode = str(context.settings["tls_mode"])
    client = redis.Redis(
        host=str(context.settings["host"]),
        port=int(context.settings["port"]),
        db=int(context.settings["database"]),
        username=str(context.settings.get("username") or "") or None,
        password=context.credentials["password"],
        ssl=True,
        ssl_cert_reqs="required",
        ssl_check_hostname=True,
        socket_connect_timeout=min(timeout, 10),
        socket_timeout=timeout,
        retry_on_timeout=False,
        health_check_interval=0,
        decode_responses=False,
        client_name="ModelMirror-Wave5-ReadOnly",
    )
    try:
        client.ping()
        yield client
    finally:
        client.close()


def _redis_key(value: str, *, allow_pattern: bool = False) -> str:
    clean = str(value or "")
    if not clean or len(clean.encode("utf-8")) > 1_024 or any(char in clean for char in "\r\n\x00"):
        raise ValueError("Redis 键无效。")
    if not allow_pattern and any(char in clean for char in "*?["):
        raise ValueError("该工具不接受键通配符。")
    return clean


def _redis_values(values: list[Any], limit: int) -> tuple[list[Any], bool]:
    result, truncated = _bounded_sequence(values, limit)
    return result, truncated


def build_redis(context: DatabaseContext) -> FastMCP:
    mcp = FastMCP("ModelMirror Redis Read Only")

    @mcp.tool(annotations=READ_ONLY)
    def scan_keys(pattern: str = "*", limit: int = 200) -> dict[str, Any]:
        """以 SCAN 分页读取键名；不使用阻塞式 KEYS。"""
        clean = _redis_key(pattern, allow_pattern=True)
        row_limit = clamp_rows(limit)
        keys: list[Any] = []
        cursor = 0
        with _redis_client(context) as client:
            for _ in range(100):
                cursor, page = client.scan(cursor=cursor, match=clean, count=min(row_limit + 1, 500))
                keys.extend(page)
                if cursor == 0 or len(keys) > row_limit:
                    break
        values, truncated = _redis_values(keys, row_limit)
        return {"keys": values, "count": len(values), "truncated": truncated or cursor != 0}

    @mcp.tool(annotations=READ_ONLY)
    def get_value(key: str) -> dict[str, Any]:
        """读取字符串键；其他类型请使用对应只读工具。"""
        clean = _redis_key(key)
        with _redis_client(context) as client:
            value = client.get(clean)
        return {"key": clean, "value": _json_value(value), "found": value is not None}

    @mcp.tool(annotations=READ_ONLY)
    def get_type(key: str) -> dict[str, Any]:
        """读取键的数据类型。"""
        clean = _redis_key(key)
        with _redis_client(context) as client:
            value = client.type(clean)
        return {"key": clean, "type": _json_value(value)}

    @mcp.tool(annotations=READ_ONLY)
    def get_ttl(key: str) -> dict[str, Any]:
        """读取键的剩余生存时间，不修改过期时间。"""
        clean = _redis_key(key)
        with _redis_client(context) as client:
            value = client.ttl(clean)
        return {"key": clean, "ttl_seconds": int(value)}

    @mcp.tool(annotations=READ_ONLY)
    def hash_get_all(key: str, limit: int = 200) -> dict[str, Any]:
        """读取哈希字段和值，结果受行数和输出上限约束。"""
        clean = _redis_key(key)
        row_limit = clamp_rows(limit)
        with _redis_client(context) as client:
            rows: list[dict[str, Any]] = []
            cursor = 0
            for _ in range(100):
                cursor, page = client.hscan(
                    clean, cursor=cursor, count=min(row_limit + 1, 500)
                )
                rows.extend(
                    {"field": _json_value(field), "value": _json_value(value)}
                    for field, value in page.items()
                )
                if cursor == 0 or len(rows) > row_limit:
                    break
        values, output_truncated = _bounded_sequence(rows, row_limit)
        return {
            "key": clean,
            "entries": values,
            "truncated": output_truncated or cursor != 0,
        }

    @mcp.tool(annotations=READ_ONLY)
    def list_range(key: str, start: int = 0, limit: int = 200) -> dict[str, Any]:
        """读取列表片段。"""
        clean = _redis_key(key)
        row_limit = clamp_rows(limit)
        start_value = max(0, min(int(start), 10_000_000))
        with _redis_client(context) as client:
            raw = client.lrange(clean, start_value, start_value + row_limit)
        values, truncated = _redis_values(raw, row_limit)
        return {"key": clean, "values": values, "truncated": truncated}

    @mcp.tool(annotations=READ_ONLY)
    def set_members(key: str, limit: int = 200) -> dict[str, Any]:
        """用 SSCAN 读取集合成员。"""
        clean = _redis_key(key)
        row_limit = clamp_rows(limit)
        members: list[Any] = []
        cursor = 0
        with _redis_client(context) as client:
            for _ in range(100):
                cursor, page = client.sscan(clean, cursor=cursor, count=min(row_limit + 1, 500))
                members.extend(page)
                if cursor == 0 or len(members) > row_limit:
                    break
        values, truncated = _redis_values(members, row_limit)
        return {"key": clean, "members": values, "truncated": truncated or cursor != 0}

    @mcp.tool(annotations=READ_ONLY)
    def sorted_set_range(key: str, start: int = 0, limit: int = 200) -> dict[str, Any]:
        """读取有序集合片段及分数。"""
        clean = _redis_key(key)
        row_limit = clamp_rows(limit)
        start_value = max(0, min(int(start), 10_000_000))
        with _redis_client(context) as client:
            raw = client.zrange(clean, start_value, start_value + row_limit, withscores=True)
        rows = [{"member": _json_value(member), "score": float(score)} for member, score in raw]
        values, truncated = bounded_rows(rows, max_rows=row_limit)
        return {"key": clean, "members": values, "truncated": truncated}

    return mcp


def _preflight_redis(context: DatabaseContext) -> None:
    import redis

    with _redis_client(context, timeout=10) as client:
        connection = client.connection_pool.get_connection()
        try:
            for command in (
                ("SET", "__modelmirror_acl_probe__", "denied"),
                ("DEL", "__modelmirror_acl_probe__"),
                ("EVAL", "return 1", "0"),
                ("FLUSHDB",),
            ):
                connection.send_command("MULTI")
                if connection.read_response() not in {b"OK", "OK"}:
                    raise RuntimeError("database_readonly_preflight_failed")
                allowed = False
                try:
                    connection.send_command(*command)
                    response = connection.read_response()
                    allowed = response in {b"QUEUED", "QUEUED"}
                except redis.exceptions.ResponseError:
                    allowed = False
                finally:
                    connection.send_command("DISCARD")
                    try:
                        connection.read_response()
                    except redis.exceptions.ResponseError:
                        pass
                if allowed:
                    raise RuntimeError("database_readonly_preflight_failed")
        finally:
            client.connection_pool.release(connection)


@contextlib.contextmanager
def _duckdb_connection(context: DatabaseContext, file_id: str) -> Iterator[Any]:
    import duckdb

    path = context.duckdb_file(file_id)
    connection = duckdb.connect(
        str(path),
        read_only=True,
        config={
            "enable_external_access": "false",
            "allow_unsigned_extensions": "false",
            "threads": "1",
            "memory_limit": "256MB",
            "max_temp_directory_size": "0KB",
        },
    )
    try:
        yield connection
    finally:
        connection.close()


def _duckdb_payload(cursor: Any, max_rows: int) -> dict[str, Any]:
    columns = [str(item[0]) for item in (cursor.description or [])]
    def rows() -> Iterator[Any]:
        if not columns:
            return
        for _ in range(max_rows + 1):
            item = cursor.fetchone()
            if item is None:
                return
            yield item
    return _result_payload(columns, rows(), max_rows)


def build_duckdb(context: DatabaseContext) -> FastMCP:
    mcp = FastMCP("ModelMirror DuckDB Read Only")

    @mcp.tool(annotations=READ_ONLY)
    def list_schemas(database_file_id: DatabaseFileId, max_rows: int = 200) -> dict[str, Any]:
        """列出所选封存 DuckDB 文件中的 schema。"""
        limit = clamp_rows(max_rows)
        with _duckdb_connection(context, database_file_id) as connection:
            cursor = connection.execute(
                "SELECT schema_name FROM information_schema.schemata ORDER BY schema_name LIMIT ?",
                [limit + 1],
            )
            return _duckdb_payload(cursor, limit)

    @mcp.tool(annotations=READ_ONLY)
    def list_tables(database_file_id: DatabaseFileId, schema: str = "", max_rows: int = 200) -> dict[str, Any]:
        """列出所选 DuckDB 文件中的表和视图。"""
        limit = clamp_rows(max_rows)
        schema_name = _safe_identifier(schema, "Schema") if schema else ""
        with _duckdb_connection(context, database_file_id) as connection:
            query = "SELECT table_schema, table_name, table_type FROM information_schema.tables"
            params: list[Any] = []
            if schema_name:
                query += " WHERE table_schema = ?"
                params.append(schema_name)
            query += " ORDER BY table_schema, table_name LIMIT ?"
            params.append(limit + 1)
            return _duckdb_payload(connection.execute(query, params), limit)

    @mcp.tool(annotations=READ_ONLY)
    def describe_table(database_file_id: DatabaseFileId, schema: str, table: str, max_rows: int = 200) -> dict[str, Any]:
        """读取所选 DuckDB 文件中的表结构。"""
        limit = clamp_rows(max_rows)
        schema_name = _safe_identifier(schema, "Schema")
        table_name = _safe_identifier(table, "表")
        with _duckdb_connection(context, database_file_id) as connection:
            cursor = connection.execute(
                "SELECT column_name, data_type, is_nullable, column_default, ordinal_position "
                "FROM information_schema.columns WHERE table_schema = ? AND table_name = ? "
                "ORDER BY ordinal_position LIMIT ?",
                [schema_name, table_name, limit + 1],
            )
            return _duckdb_payload(cursor, limit)

    @mcp.tool(annotations=READ_ONLY)
    def query(database_file_id: DatabaseFileId, query: str, max_rows: int = 200, timeout_seconds: int = 15) -> dict[str, Any]:
        """在只读连接中执行单条 SELECT/WITH；禁止文件、网络与扩展访问。"""
        limit = clamp_rows(max_rows)
        clamp_timeout(timeout_seconds)  # Process-level timeout is enforced by the sidecar gateway.
        clean = validate_readonly_sql(query, dialect="duckdb")
        with _duckdb_connection(context, database_file_id) as connection:
            return _duckdb_payload(connection.execute(_sql_wrapper(clean, limit, engine="duckdb")), limit)

    return mcp


def _preflight_duckdb(context: DatabaseContext) -> None:
    if len(context._duckdb_files) > 64:
        raise RuntimeError("duckdb_file_limit_exceeded")
    for file_id in context._duckdb_files:
        with _duckdb_connection(context, file_id) as connection:
            if int(connection.execute("SELECT 1").fetchone()[0]) != 1:
                raise RuntimeError("database_preflight_failed")


def _supabase_query(context: DatabaseContext, query: str, timeout: int) -> Any:
    import httpx

    project_ref = str(context.settings["project_ref"])
    url = f"https://api.supabase.com/v1/projects/{project_ref}/database/query/read-only"
    headers = {
        "Authorization": f"Bearer {context.credentials['access_token']}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "ModelMirror-Wave5-ReadOnly/1",
    }
    with httpx.Client(
        timeout=httpx.Timeout(timeout, connect=min(timeout, 10)),
        follow_redirects=False,
        trust_env=False,
    ) as client:
        with client.stream("POST", url, headers=headers, json={"query": query}) as response:
            if response.is_redirect:
                raise ValueError("Supabase 重定向已被安全策略拒绝。")
            if response.status_code >= 400:
                raise ValueError(f"Supabase 只读请求失败（HTTP {response.status_code}）。")
            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > MAX_OUTPUT_BYTES:
                raise ValueError("Supabase 响应超过 256 KiB 上限。")
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > MAX_OUTPUT_BYTES:
                    raise ValueError("Supabase 响应超过 256 KiB 上限。")
                chunks.append(chunk)
            content = b"".join(chunks)
    try:
        return json.loads(content.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Supabase 返回了无效 JSON。") from exc


def _supabase_payload(value: Any, max_rows: int) -> dict[str, Any]:
    rows = value if isinstance(value, list) else value.get("result", value) if isinstance(value, dict) else []
    if not isinstance(rows, list):
        rows = [rows]
    values, truncated = _bounded_sequence(rows, max_rows)
    return {"rows": values, "row_count": len(values), "truncated": truncated}


def build_supabase(context: DatabaseContext) -> FastMCP:
    mcp = FastMCP("ModelMirror Supabase Read Only")

    @mcp.tool(annotations=READ_ONLY)
    def list_tables(max_rows: int = 200) -> dict[str, Any]:
        """列出绑定 Supabase 项目中的普通表，不访问其他项目。"""
        limit = clamp_rows(max_rows)
        query = _sql_wrapper(
            "SELECT table_schema, table_name, table_type FROM information_schema.tables "
            "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
            "ORDER BY table_schema, table_name",
            limit,
            engine="postgresql",
        )
        return _supabase_payload(_supabase_query(context, query, 15), limit)

    @mcp.tool(annotations=READ_ONLY)
    def list_extensions(max_rows: int = 200) -> dict[str, Any]:
        """读取绑定 Supabase 项目中已安装扩展的元数据。"""
        limit = clamp_rows(max_rows)
        query = _sql_wrapper(
            "SELECT extname, extversion FROM pg_extension ORDER BY extname",
            limit,
            engine="postgresql",
        )
        return _supabase_payload(_supabase_query(context, query, 15), limit)

    @mcp.tool(annotations=READ_ONLY)
    def execute_sql(query: str, max_rows: int = 200, timeout_seconds: int = 15) -> dict[str, Any]:
        """对绑定项目执行经词法与 AST 审核的单条只读 SELECT/WITH。"""
        limit = clamp_rows(max_rows)
        timeout = clamp_timeout(timeout_seconds)
        clean = validate_readonly_sql(query, dialect="postgres")
        wrapped = _sql_wrapper(clean, limit, engine="postgresql")
        return _supabase_payload(_supabase_query(context, wrapped, timeout), limit)

    return mcp


def _preflight_supabase(context: DatabaseContext) -> None:
    value = _supabase_query(context, "SELECT 1 AS modelmirror_preflight", 10)
    payload = _supabase_payload(value, 1)
    if not payload["rows"]:
        raise RuntimeError("database_preflight_failed")


BUILDERS = {
    "dbhub": build_dbhub,
    "mongodb-mcp": build_mongodb,
    "clickhouse-mcp": build_clickhouse,
    "redis-mcp": build_redis,
    "duckdb-mcp": build_duckdb,
    "supabase-mcp": build_supabase,
    "pab1it0-prometheus-mcp-server": build_prometheus,
    "qdrant-mcp-server-qdrant": build_qdrant,
    "cr7258-elasticsearch-mcp-server": build_elasticsearch,
    "zilliztech-mcp-server-milvus": build_milvus,
    "neo4j-contrib-mcp-neo4j": build_neo4j,
    "arcadedata-arcadedb": build_arcadedb,
    "greptimeteam-greptimedb-mcp-server": build_greptime,
}

PREFLIGHTS = {
    "dbhub": _preflight_dbhub,
    "mongodb-mcp": _preflight_mongodb,
    "clickhouse-mcp": _preflight_clickhouse,
    "redis-mcp": _preflight_redis,
    "duckdb-mcp": _preflight_duckdb,
    "supabase-mcp": _preflight_supabase,
    "pab1it0-prometheus-mcp-server": preflight_prometheus,
    "qdrant-mcp-server-qdrant": preflight_qdrant,
    "cr7258-elasticsearch-mcp-server": preflight_elasticsearch,
    "zilliztech-mcp-server-milvus": preflight_milvus,
    "neo4j-contrib-mcp-neo4j": preflight_neo4j,
    "arcadedata-arcadedb": preflight_arcadedb,
    "greptimeteam-greptimedb-mcp-server": preflight_greptime,
}


ADAPTER_TOOL_NAMES = {adapter_id: tuple(sorted(contract.tools)) for adapter_id, contract in DATABASE_ADAPTERS.items()}


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("adapter_id", choices=sorted(BUILDERS))
    arguments = parser.parse_args()
    try:
        context = DatabaseContext(arguments.adapter_id)
        PREFLIGHTS[arguments.adapter_id](context)
    except Exception:
        print(
            f"database adapter preflight failed: {arguments.adapter_id}",
            file=sys.stderr,
        )
        return 69
    BUILDERS[arguments.adapter_id](context).run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
