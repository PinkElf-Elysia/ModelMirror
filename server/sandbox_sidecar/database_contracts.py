"""Private Wave-5 database adapter contracts and fail-closed input policy.

The catalog sends structured settings and credential slots.  This module
never accepts a DSN, URL, command, environment map, header map, or working
directory.  It is intentionally independent from the public catalog metadata
so a modified browser cannot select an executable or weaken the runtime
policy.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
from dataclasses import dataclass
from typing import Any, Mapping


MAX_ARGUMENT_BYTES = 128 * 1024
MAX_QUERY_BYTES = 32 * 1024
MAX_OUTPUT_BYTES = 256 * 1024
DEFAULT_MAX_ROWS = 200
HARD_MAX_ROWS = 1_000
DEFAULT_TIMEOUT_SECONDS = 15
HARD_TIMEOUT_SECONDS = 15

WORKSPACE_PATTERN = re.compile(r"mcpws_[0-9a-f]{32}")
FILE_ID_PATTERN = re.compile(r"mcpf_[0-9a-f]{24}")
HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9_$][A-Za-z0-9_$.-]{0,127}")
SAFE_USERNAME = re.compile(r"[A-Za-z0-9_$][A-Za-z0-9_$.+@-]{0,253}")
PROJECT_REF = re.compile(r"[a-z]{20}")
DATA_SERVICE_RESOURCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
ELASTIC_SEARCH_FIELD = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")
PROMETHEUS_METRIC = re.compile(r"[A-Za-z_:][A-Za-z0-9_:]{0,254}")

ADMIN_PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
)

TLS_MODES = frozenset({"verify-full"})
GRAPH_DATA_SERVICE_ADAPTERS = frozenset(
    {
        "zilliztech-mcp-server-milvus",
        "neo4j-contrib-mcp-neo4j",
        "arcadedata-arcadedb",
    }
)
WAVE_TWENTYSEVEN_DATA_SERVICE_ADAPTERS = frozenset(
    {
        "greptimeteam-greptimedb-mcp-server",
    }
)
WAVE_TWENTYNINE_DATA_SERVICE_ADAPTERS = frozenset(
    {
        "victoriametrics-community-mcp-victoriametrics",
    }
)
REMOTE_DATA_SERVICE_ADAPTERS = frozenset(
    {
        "pab1it0-prometheus-mcp-server",
        "qdrant-mcp-server-qdrant",
        "cr7258-elasticsearch-mcp-server",
        *GRAPH_DATA_SERVICE_ADAPTERS,
        *WAVE_TWENTYSEVEN_DATA_SERVICE_ADAPTERS,
        *WAVE_TWENTYNINE_DATA_SERVICE_ADAPTERS,
    }
)
STAGED_DATABASE_ADAPTERS = frozenset()
FORBIDDEN_CONFIGURATION_KEYS = frozenset(
    {
        "command",
        "commands",
        "argv",
        "url",
        "uri",
        "dsn",
        "connection_string",
        "environment",
        "env",
        "headers",
        "header",
        "cwd",
        "working_directory",
        "socket_path",
        "allowlist",
    }
)


@dataclass(frozen=True, slots=True)
class DatabaseAdapterContract:
    adapter_id: str
    tools: frozenset[str]
    required_settings: frozenset[str]
    optional_settings: frozenset[str]
    required_credentials: frozenset[str]
    optional_credentials: frozenset[str] = frozenset()
    workspace_required: bool = False


DATABASE_ADAPTERS: dict[str, DatabaseAdapterContract] = {
    "dbhub": DatabaseAdapterContract(
        "dbhub",
        frozenset({"list_schemas", "list_tables", "describe_table", "execute_sql"}),
        frozenset({"engine", "host", "port", "database", "tls_mode", "username"}),
        frozenset(),
        frozenset({"password"}),
    ),
    "mongodb-mcp": DatabaseAdapterContract(
        "mongodb-mcp",
        frozenset(
            {
                "list_collections",
                "collection_schema",
                "collection_indexes",
                "find",
                "aggregate",
                "count_documents",
            }
        ),
        frozenset({"host", "port", "database", "tls_mode", "username", "auth_source"}),
        frozenset(),
        frozenset({"password"}),
    ),
    "clickhouse-mcp": DatabaseAdapterContract(
        "clickhouse-mcp",
        frozenset({"list_databases", "list_tables", "run_query"}),
        frozenset({"host", "port", "database", "tls_mode", "username"}),
        frozenset(),
        frozenset({"password"}),
    ),
    "redis-mcp": DatabaseAdapterContract(
        "redis-mcp",
        frozenset(
            {
                "scan_keys",
                "get_value",
                "get_type",
                "get_ttl",
                "hash_get_all",
                "list_range",
                "set_members",
                "sorted_set_range",
            }
        ),
        frozenset({"host", "port", "tls_mode", "database"}),
        frozenset({"username"}),
        frozenset({"password"}),
    ),
    "duckdb-mcp": DatabaseAdapterContract(
        "duckdb-mcp",
        frozenset({"list_schemas", "list_tables", "describe_table", "query"}),
        frozenset(),
        frozenset(),
        frozenset(),
        workspace_required=True,
    ),
    "supabase-mcp": DatabaseAdapterContract(
        "supabase-mcp",
        frozenset({"list_tables", "list_extensions", "execute_sql"}),
        frozenset({"project_ref"}),
        frozenset(),
        frozenset({"access_token"}),
    ),
    "pab1it0-prometheus-mcp-server": DatabaseAdapterContract(
        "pab1it0-prometheus-mcp-server",
        frozenset(
            {
                "execute_query",
                "execute_range_query",
                "list_metrics",
                "get_metric_metadata",
                "get_targets",
            }
        ),
        frozenset({"host", "port", "tls_mode"}),
        frozenset(),
        frozenset(),
        frozenset({"bearer_token"}),
    ),
    "qdrant-mcp-server-qdrant": DatabaseAdapterContract(
        "qdrant-mcp-server-qdrant",
        frozenset({"get_collection_info", "scroll_points", "query_points"}),
        frozenset({"host", "port", "tls_mode", "collection"}),
        frozenset(),
        frozenset({"api_key"}),
    ),
    "cr7258-elasticsearch-mcp-server": DatabaseAdapterContract(
        "cr7258-elasticsearch-mcp-server",
        frozenset({"get_cluster_health", "get_index", "search_documents", "get_document"}),
        frozenset({"host", "port", "tls_mode", "index", "search_field", "username"}),
        frozenset(),
        frozenset({"password"}),
    ),
    "zilliztech-mcp-server-milvus": DatabaseAdapterContract(
        "zilliztech-mcp-server-milvus",
        frozenset({"list_collections", "describe_collection", "get_entities", "search_vectors"}),
        frozenset(
            {
                "host",
                "port",
                "tls_mode",
                "database",
                "collection",
                "vector_field",
                "output_fields",
                "username",
            }
        ),
        frozenset(),
        frozenset({"password"}),
    ),
    "neo4j-contrib-mcp-neo4j": DatabaseAdapterContract(
        "neo4j-contrib-mcp-neo4j",
        frozenset({"get_schema", "read_cypher"}),
        frozenset({"host", "port", "tls_mode", "database", "username"}),
        frozenset(),
        frozenset({"password"}),
    ),
    "arcadedata-arcadedb": DatabaseAdapterContract(
        "arcadedata-arcadedb",
        frozenset({"list_types", "describe_type", "read_query"}),
        frozenset({"host", "port", "tls_mode", "database", "username"}),
        frozenset(),
        frozenset({"password"}),
    ),
    "greptimeteam-greptimedb-mcp-server": DatabaseAdapterContract(
        "greptimeteam-greptimedb-mcp-server",
        frozenset({"describe_table", "query_range", "health_check"}),
        frozenset(
            {
                "host",
                "port",
                "database",
                "table",
                "time_column",
                "value_column",
                "tls_mode",
                "username",
            }
        ),
        frozenset(),
        frozenset({"password"}),
    ),
    "victoriametrics-community-mcp-victoriametrics": DatabaseAdapterContract(
        "victoriametrics-community-mcp-victoriametrics",
        frozenset({"metrics", "labels", "query", "query_range"}),
        frozenset({"host", "port", "tls_mode", "metric"}),
        frozenset(),
        frozenset(),
        frozenset({"bearer_token"}),
    ),
}


@dataclass(slots=True)
class ValidatedConfiguration:
    contract: DatabaseAdapterContract
    settings: dict[str, str | int]
    credentials: dict[str, str]
    workspace_id: str | None

    def clear_secrets(self) -> None:
        self.credentials.clear()


def _reject_forbidden_keys(value: object, *, depth: int = 0) -> None:
    if depth > 8:
        raise ValueError("configuration_too_deep")
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key).strip().lower()
            if key in FORBIDDEN_CONFIGURATION_KEYS:
                raise ValueError("forbidden_configuration_field")
            _reject_forbidden_keys(child, depth=depth + 1)
    elif isinstance(value, list):
        for child in value:
            _reject_forbidden_keys(child, depth=depth + 1)


def _normalize_host(value: object) -> str:
    raw = str(value or "").strip().rstrip(".")
    try:
        ipaddress.ip_address(raw.strip("[]"))
    except ValueError:
        pass
    else:
        raise ValueError("ip_literal_denied")
    if not raw or len(raw) > 253 or any(char in raw for char in "/\\@?#:\r\n\x00"):
        raise ValueError("invalid_host")
    try:
        ascii_host = raw.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("invalid_host") from exc
    labels = ascii_host.split(".")
    if len(labels) < 2 or any(not HOST_LABEL.fullmatch(label) for label in labels):
        raise ValueError("invalid_host")
    return ascii_host


def _safe_text(value: object, *, pattern: re.Pattern[str], code: str) -> str:
    clean = str(value or "").strip()
    if not pattern.fullmatch(clean) or "://" in clean or "\x00" in clean:
        raise ValueError(code)
    return clean


def _integer(value: object, *, minimum: int, maximum: int, code: str) -> int:
    if isinstance(value, bool):
        raise ValueError(code)
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(code) from exc
    if parsed < minimum or parsed > maximum:
        raise ValueError(code)
    return parsed


def validate_configuration(adapter_id: str, configuration: object) -> ValidatedConfiguration:
    contract = DATABASE_ADAPTERS.get(str(adapter_id or "").strip())
    if contract is None:
        raise ValueError("mcp_adapter_denied")
    if not isinstance(configuration, dict):
        raise ValueError("invalid_configuration")
    encoded = json.dumps(configuration, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_ARGUMENT_BYTES:
        raise ValueError("configuration_too_large")
    if set(configuration) - {"settings", "credentials", "workspace_id"}:
        raise ValueError("configuration_contract_mismatch")
    _reject_forbidden_keys(configuration)

    raw_settings = configuration.get("settings")
    raw_credentials = configuration.get("credentials")
    if not isinstance(raw_settings, dict) or not isinstance(raw_credentials, dict):
        raise ValueError("invalid_configuration")
    setting_keys = set(raw_settings)
    credential_keys = set(raw_credentials)
    if not contract.required_settings.issubset(setting_keys):
        raise ValueError("configuration_contract_mismatch")
    if setting_keys - contract.required_settings - contract.optional_settings:
        raise ValueError("configuration_contract_mismatch")
    if not contract.required_credentials.issubset(credential_keys):
        raise ValueError("configuration_contract_mismatch")
    if credential_keys - contract.required_credentials - contract.optional_credentials:
        raise ValueError("configuration_contract_mismatch")

    credentials: dict[str, str] = {}
    for key in credential_keys:
        value = raw_credentials.get(key)
        if not isinstance(value, str) or not value or len(value) > 20_000 or "\x00" in value:
            raise ValueError("invalid_credential")
        credentials[str(key)] = value

    settings: dict[str, str | int] = {}
    if adapter_id in {
        "dbhub",
        "mongodb-mcp",
        "clickhouse-mcp",
        "redis-mcp",
        *REMOTE_DATA_SERVICE_ADAPTERS,
    }:
        settings["host"] = _normalize_host(raw_settings.get("host"))
        settings["port"] = _integer(raw_settings.get("port"), minimum=1, maximum=65535, code="invalid_port")
        tls_mode = str(raw_settings.get("tls_mode") or "").strip().lower()
        test_plaintext = (
            adapter_id in REMOTE_DATA_SERVICE_ADAPTERS
            and tls_mode == "test-only-plaintext"
            and os.getenv("MCP_DATABASE_TEST_ALLOW_PLAINTEXT") == "true"
        )
        if tls_mode not in TLS_MODES and not test_plaintext:
            raise ValueError("invalid_tls_mode")
        settings["tls_mode"] = tls_mode

    if adapter_id == "dbhub":
        engine = str(raw_settings.get("engine") or "").strip().lower()
        if engine not in {"postgresql", "mysql", "mariadb"}:
            raise ValueError("invalid_engine")
        settings.update(
            engine=engine,
            database=_safe_text(raw_settings.get("database"), pattern=SAFE_IDENTIFIER, code="invalid_database"),
            username=_safe_text(raw_settings.get("username"), pattern=SAFE_USERNAME, code="invalid_username"),
        )
    elif adapter_id == "mongodb-mcp":
        settings.update(
            database=_safe_text(raw_settings.get("database"), pattern=SAFE_IDENTIFIER, code="invalid_database"),
            username=_safe_text(raw_settings.get("username"), pattern=SAFE_USERNAME, code="invalid_username"),
            auth_source=_safe_text(raw_settings.get("auth_source"), pattern=SAFE_IDENTIFIER, code="invalid_auth_source"),
        )
    elif adapter_id == "clickhouse-mcp":
        settings.update(
            database=_safe_text(raw_settings.get("database"), pattern=SAFE_IDENTIFIER, code="invalid_database"),
            username=_safe_text(raw_settings.get("username"), pattern=SAFE_USERNAME, code="invalid_username"),
        )
    elif adapter_id == "redis-mcp":
        settings["database"] = _integer(raw_settings.get("database"), minimum=0, maximum=15, code="invalid_database")
        username = str(raw_settings.get("username") or "").strip()
        if username:
            settings["username"] = _safe_text(username, pattern=SAFE_USERNAME, code="invalid_username")
    elif adapter_id == "supabase-mcp":
        settings["project_ref"] = _safe_text(
            raw_settings.get("project_ref"), pattern=PROJECT_REF, code="invalid_project_ref"
        )
    elif adapter_id == "qdrant-mcp-server-qdrant":
        settings["collection"] = _safe_text(
            raw_settings.get("collection"),
            pattern=DATA_SERVICE_RESOURCE,
            code="invalid_collection",
        )
    elif adapter_id == "cr7258-elasticsearch-mcp-server":
        index = _safe_text(
            raw_settings.get("index"),
            pattern=DATA_SERVICE_RESOURCE,
            code="invalid_index",
        )
        if index.startswith(("_", ".")):
            raise ValueError("invalid_index")
        settings["index"] = index
        settings["search_field"] = _safe_text(
            raw_settings.get("search_field"),
            pattern=ELASTIC_SEARCH_FIELD,
            code="invalid_search_field",
        )
        settings["username"] = _safe_text(
            raw_settings.get("username"),
            pattern=SAFE_USERNAME,
            code="invalid_username",
        )
    elif adapter_id == "zilliztech-mcp-server-milvus":
        settings["database"] = _safe_text(
            raw_settings.get("database"), pattern=DATA_SERVICE_RESOURCE, code="invalid_database"
        )
        settings["collection"] = _safe_text(
            raw_settings.get("collection"), pattern=DATA_SERVICE_RESOURCE, code="invalid_collection"
        )
        settings["vector_field"] = _safe_text(
            raw_settings.get("vector_field"), pattern=SAFE_IDENTIFIER, code="invalid_vector_field"
        )
        output_fields = str(raw_settings.get("output_fields") or "").strip()
        fields = [field.strip() for field in output_fields.split(",") if field.strip()]
        if not 1 <= len(fields) <= 32 or len(set(fields)) != len(fields):
            raise ValueError("invalid_output_fields")
        if any(not SAFE_IDENTIFIER.fullmatch(field) for field in fields):
            raise ValueError("invalid_output_fields")
        settings["output_fields"] = ",".join(fields)
        settings["username"] = _safe_text(
            raw_settings.get("username"), pattern=SAFE_USERNAME, code="invalid_username"
        )
    elif adapter_id in {"neo4j-contrib-mcp-neo4j", "arcadedata-arcadedb"}:
        settings["database"] = _safe_text(
            raw_settings.get("database"), pattern=DATA_SERVICE_RESOURCE, code="invalid_database"
        )
        settings["username"] = _safe_text(
            raw_settings.get("username"), pattern=SAFE_USERNAME, code="invalid_username"
        )
    elif adapter_id == "greptimeteam-greptimedb-mcp-server":
        settings["database"] = _safe_text(
            raw_settings.get("database"), pattern=DATA_SERVICE_RESOURCE, code="invalid_database"
        )
        settings["username"] = _safe_text(
            raw_settings.get("username"), pattern=SAFE_USERNAME, code="invalid_username"
        )
        settings["table"] = _safe_text(
            raw_settings.get("table"), pattern=SAFE_IDENTIFIER, code="invalid_table"
        )
        settings["time_column"] = _safe_text(
            raw_settings.get("time_column"), pattern=SAFE_IDENTIFIER, code="invalid_time_column"
        )
        settings["value_column"] = _safe_text(
            raw_settings.get("value_column"), pattern=SAFE_IDENTIFIER, code="invalid_value_column"
        )
    elif adapter_id == "victoriametrics-community-mcp-victoriametrics":
        settings["metric"] = _safe_text(
            raw_settings.get("metric"),
            pattern=PROMETHEUS_METRIC,
            code="invalid_metric",
        )

    workspace_raw = str(configuration.get("workspace_id") or "").strip()
    workspace_id: str | None = workspace_raw or None
    if contract.workspace_required:
        if not workspace_id or not WORKSPACE_PATTERN.fullmatch(workspace_id):
            raise ValueError("workspace_required")
    elif workspace_id is not None:
        raise ValueError("workspace_not_allowed")
    return ValidatedConfiguration(contract, settings, credentials, workspace_id)


def _admin_private_host_allowlist() -> frozenset[str]:
    raw = os.getenv("MCP_DATABASE_PRIVATE_HOST_ALLOWLIST", "")
    values: set[str] = set()
    for item in raw.split(","):
        clean = item.strip()
        if not clean:
            continue
        # Invalid administrator entries are ignored; they never broaden access.
        try:
            values.add(_normalize_host(clean))
        except ValueError:
            continue
    return frozenset(values)


def resolve_allowed_addresses(host: str, port: int) -> tuple[str, ...]:
    normalized = _normalize_host(host)
    admin_allowed = normalized in _admin_private_host_allowlist()
    try:
        records = socket.getaddrinfo(normalized, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("database_host_unresolved") from exc
    addresses: set[str] = set()
    for record in records:
        raw_address = record[4][0]
        try:
            address = ipaddress.ip_address(raw_address)
        except ValueError as exc:
            raise ValueError("database_host_unresolved") from exc
        # IPv4-mapped IPv6 addresses inherit the IPv4 classification.
        effective = (
            address.ipv4_mapped
            if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None
            else address
        )
        if (
            effective.is_loopback
            or effective.is_link_local
            or effective.is_multicast
            or effective.is_unspecified
            or effective.is_reserved
        ):
            raise ValueError("database_host_private")
        if not effective.is_global:
            private_allowed = admin_allowed and any(
                effective.version == network.version and effective in network
                for network in ADMIN_PRIVATE_NETWORKS
            )
            if not private_allowed:
                raise ValueError("database_host_private")
        addresses.add(address.compressed)
    if not addresses:
        raise ValueError("database_host_unresolved")
    return tuple(sorted(addresses))


def install_pinned_getaddrinfo(host: str | None, addresses: tuple[str, ...]) -> None:
    """Pin Python network clients to the sidecar-reviewed DNS answer.

    Drivers continue receiving the original hostname, so TLS uses that value
    for SNI and hostname verification.  Only socket address resolution is
    replaced.  Any attempt to resolve a different host fails closed.
    """

    normalized_host = _normalize_host(host) if host is not None else None
    parsed_addresses: tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...] = tuple(
        ipaddress.ip_address(value) for value in addresses
    )
    if normalized_host is None and parsed_addresses:
        raise ValueError("invalid_pinned_dns")
    if normalized_host is not None and not parsed_addresses:
        raise ValueError("invalid_pinned_dns")

    def pinned_getaddrinfo(
        requested_host: object,
        requested_port: object,
        family: int = 0,
        type: int = 0,
        proto: int = 0,
        flags: int = 0,
    ) -> list[tuple[int, int, int, str, tuple[Any, ...]]]:
        del flags
        if isinstance(requested_host, (bytes, bytearray)):
            try:
                requested = bytes(requested_host).decode("ascii", "strict")
            except UnicodeDecodeError as exc:
                raise socket.gaierror(
                    socket.EAI_NONAME, "database DNS target denied"
                ) from exc
        else:
            requested = str(requested_host or "")
        requested = requested.strip().rstrip(".").lower()
        allowed_ip = False
        try:
            requested_address = ipaddress.ip_address(requested.strip("[]"))
            allowed_ip = requested_address in parsed_addresses
        except ValueError:
            requested_address = None
        if not allowed_ip and (normalized_host is None or requested != normalized_host):
            raise socket.gaierror(socket.EAI_NONAME, "database DNS target denied")
        try:
            port = int(requested_port)
        except (TypeError, ValueError) as exc:
            raise socket.gaierror(socket.EAI_SERVICE, "database port denied") from exc
        results: list[tuple[int, int, int, str, tuple[Any, ...]]] = []
        for address in parsed_addresses:
            address_family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
            if family not in {0, socket.AF_UNSPEC, address_family}:
                continue
            socket_type = type or socket.SOCK_STREAM
            protocol = proto or socket.IPPROTO_TCP
            sockaddr: tuple[Any, ...]
            if address.version == 6:
                sockaddr = (address.compressed, port, 0, 0)
            else:
                sockaddr = (address.compressed, port)
            results.append((address_family, socket_type, protocol, "", sockaddr))
        if not results:
            raise socket.gaierror(socket.EAI_NONAME, "database DNS family denied")
        return results

    socket.getaddrinfo = pinned_getaddrinfo  # type: ignore[assignment]


_SQL_COMMENT_OR_LITERAL = re.compile(
    r"(?:--[^\r\n]*|/\*.*?\*/|'(?:''|[^'])*'|"
    r"(?P<dollar>\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$).*?(?P=dollar))",
    re.DOTALL,
)
_SQL_QUOTED_IDENTIFIER = re.compile(r'\"(?:\"\"|[^\"])*\"|`(?:``|[^`])*`')
_SQL_MUTATION = re.compile(
    r"\b(?:insert|into|update|delete|merge|replace|upsert|create|alter|drop|truncate|grant|revoke|comment|rename|call|execute|exec|prepare|deallocate|copy|attach|detach|install|load|vacuum|analyze|optimize|set|reset|use|lock|unlock|begin|commit|rollback|savepoint|release)\b",
    re.IGNORECASE,
)
_SQL_EXTERNAL_OR_DANGEROUS = re.compile(
    r"\b(?:into\s+(?:out|dump)?file|load_file|sleep|benchmark|sys_exec|sys_eval|get_lock|release_lock|is_free_lock|is_used_lock|pg_sleep|pg_advisory_lock|pg_try_advisory_lock|pg_advisory_unlock|pg_advisory_unlock_all|pg_advisory_lock_shared|pg_try_advisory_lock_shared|pg_advisory_unlock_shared|pg_advisory_xact_lock|pg_try_advisory_xact_lock|pg_advisory_xact_lock_shared|pg_try_advisory_xact_lock_shared|pg_notify|pg_logical_emit_message|set_config|dblink|dblink_exec|dblink_connect|dblink_connect_u|dblink_disconnect|dblink_send_query|dblink_get_result|dblink_cancel_query|dblink_error_message|dblink_open|dblink_close|lo_import|lo_export|pg_read_file|pg_read_binary_file|pg_ls_dir|pg_stat_file|pg_reload_conf|pg_terminate_backend|pg_cancel_backend|nextval|setval|xp_cmdshell|sp_execute_external_script|sp_getapplock|sp_releaseapplock|openquery|openrowset|opendatasource|bulk\s+insert|read_csv|read_json|read_parquet|read_ndjson|read_blob|csv_scan|json_scan|parquet_scan|sqlite_scan|postgres_scan|mysql_scan|glob|httpfs|url|s3|s3Cluster|hdfs|remote|remoteSecure|file|executable|mysql|postgresql|mongodb|redis|jdbc|odbc|azureBlobStorage|gcs)\s*\(",
    re.IGNORECASE,
)
_SQL_QUERY_MODIFIERS = re.compile(
    r"\bsettings\b|\buescape\b|\bfor\s+(?:no\s+key\s+update|key\s+share|share|update)\b",
    re.IGNORECASE,
)


def validate_readonly_sql(query: object, *, dialect: str) -> str:
    if not isinstance(query, str):
        raise ValueError("invalid_query")
    clean = query.strip()
    raw = clean.encode("utf-8")
    if not clean or len(raw) > MAX_QUERY_BYTES or "\x00" in clean:
        raise ValueError("invalid_query")
    def normalize_quoted_identifier(match: re.Match[str]) -> str:
        quoted = match.group(0)
        quote = quoted[0]
        inner = quoted[1:-1].replace(quote * 2, quote)
        if (
            match.start() >= 2
            and clean[match.start() - 2 : match.start()].lower() == "u&"
        ):
            raise ValueError("unicode_quoted_identifier_denied")
        if not SAFE_IDENTIFIER.fullmatch(inner):
            raise ValueError("unsafe_quoted_identifier")
        return inner

    normalized_identifiers = _SQL_QUOTED_IDENTIFIER.sub(normalize_quoted_identifier, clean)

    def scrub_comment_or_literal(match: re.Match[str]) -> str:
        token = match.group(0)
        lowered = token.lower()
        if token.startswith("/*!") or lowered.startswith("/*m!") or token.startswith("/*+"):
            raise ValueError("dangerous_comment_denied")
        return " "

    scrubbed = _SQL_COMMENT_OR_LITERAL.sub(
        scrub_comment_or_literal, normalized_identifiers
    ).strip()
    if scrubbed.endswith(";"):
        scrubbed = scrubbed[:-1].rstrip()
        clean = clean.rstrip()[:-1].rstrip()
    if ";" in scrubbed:
        raise ValueError("multiple_statements_denied")
    if not re.match(r"^(?:select|with)\b", scrubbed, re.IGNORECASE):
        raise ValueError("readonly_query_required")
    if (
        _SQL_MUTATION.search(scrubbed)
        or _SQL_EXTERNAL_OR_DANGEROUS.search(scrubbed)
        or _SQL_QUERY_MODIFIERS.search(scrubbed)
    ):
        raise ValueError("dangerous_query_denied")
    # The lexical gate above remains active even if sqlglot accepts a vendor
    # extension.  The AST pass catches writable CTEs and non-query roots.
    try:
        import sqlglot
        from sqlglot import expressions as exp
    except ImportError as exc:
        raise ValueError("query_parser_unavailable") from exc
    try:
        statements = sqlglot.parse(clean, read=dialect)
    except Exception as exc:
        raise ValueError("query_parse_failed") from exc
    if len(statements) != 1 or statements[0] is None:
        raise ValueError("multiple_statements_denied")
    statement = statements[0]
    forbidden_types = tuple(
        item
        for item in (
            getattr(exp, "DML", None),
            getattr(exp, "DDL", None),
            getattr(exp, "Command", None),
            getattr(exp, "Copy", None),
            getattr(exp, "Into", None),
            getattr(exp, "Set", None),
            getattr(exp, "Use", None),
            getattr(exp, "Transaction", None),
        )
        if item is not None
    )
    if forbidden_types and any(isinstance(node, forbidden_types) for node in statement.walk()):
        raise ValueError("dangerous_query_denied")
    if dialect.lower() == "clickhouse":
        for table in statement.find_all(exp.Table):
            if not isinstance(table.this, exp.Identifier):
                raise ValueError("clickhouse_table_function_denied")
    if statement.find(exp.Select) is None:
        raise ValueError("readonly_query_required")
    return clean


MONGO_DENIED_OPERATORS = frozenset(
    {"$out", "$merge", "$where", "$function", "$accumulator", "$currentOp", "$listSessions", "$listLocalSessions"}
)


def validate_document(value: object, *, pipeline: bool = False) -> Any:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_ARGUMENT_BYTES:
        raise ValueError("document_too_large")
    if pipeline and not isinstance(value, list):
        raise ValueError("invalid_pipeline")
    if not pipeline and not isinstance(value, dict):
        raise ValueError("invalid_document")

    def walk(item: object, depth: int) -> None:
        if depth > 12:
            raise ValueError("document_too_deep")
        if isinstance(item, dict):
            for raw_key, child in item.items():
                key = str(raw_key)
                if key in MONGO_DENIED_OPERATORS or "\x00" in key or len(key) > 200:
                    raise ValueError("dangerous_mongo_operator")
                if key == "$regex" and (not isinstance(child, str) or len(child) > 500):
                    raise ValueError("unsafe_regex")
                walk(child, depth + 1)
        elif isinstance(item, list):
            if len(item) > 1_000:
                raise ValueError("document_too_large")
            for child in item:
                walk(child, depth + 1)
        elif isinstance(item, str) and len(item) > 20_000:
            raise ValueError("document_value_too_large")

    walk(value, 0)
    return value


def bounded_rows(value: object, *, max_rows: object = DEFAULT_MAX_ROWS) -> tuple[Any, bool]:
    limit = _integer(max_rows, minimum=1, maximum=HARD_MAX_ROWS, code="invalid_row_limit")
    truncated = False
    if isinstance(value, list) and len(value) > limit:
        value = value[:limit]
        truncated = True
    encoded = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":")).encode("utf-8")
    if len(encoded) <= MAX_OUTPUT_BYTES:
        return value, truncated
    if not isinstance(value, list):
        raise ValueError("output_too_large")
    kept: list[Any] = []
    for item in value:
        candidate = kept + [item]
        if len(json.dumps(candidate, ensure_ascii=False, default=str, separators=(",", ":")).encode("utf-8")) > MAX_OUTPUT_BYTES:
            break
        kept.append(item)
    return kept, True


def clamp_timeout(value: object = DEFAULT_TIMEOUT_SECONDS) -> int:
    return _integer(value, minimum=1, maximum=HARD_TIMEOUT_SECONDS, code="invalid_timeout")


def clamp_rows(value: object = DEFAULT_MAX_ROWS) -> int:
    return _integer(value, minimum=1, maximum=HARD_MAX_ROWS, code="invalid_row_limit")
