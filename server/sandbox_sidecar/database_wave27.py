"""Staged Wave-27 native read-only data-service contracts.

The reviewed upstream projects define the product identity and tool intent.
This module exposes only project-bound read operations: all provider paths,
SQL text, PromQL selectors, headers, and credentials are constructed by the
sidecar.  Catalog clients cannot submit a query language, endpoint, DSN,
header, environment variable, or resource name.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import math
import re
from typing import Any, Mapping, Protocol

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .database_contracts import MAX_OUTPUT_BYTES
from .database_data_services import PROVIDER_TIMEOUT_SECONDS, _integer


GREPTIME_ADAPTER_ID = "greptimeteam-greptimedb-mcp-server"
WAVE27_ADAPTERS = frozenset({GREPTIME_ADAPTER_ID})

WAVE27_UPSTREAM_LOCKS = {
    GREPTIME_ADAPTER_ID: {
        "version": "v0.5.1",
        "commit": "ba3b732fe2113378f41c391da880b9ab75f2d862",
        "license": "MIT",
        "repository": "GreptimeTeam/greptimedb-mcp-server",
    },
}

WAVE27_SCHEMA_SHA256 = {
    GREPTIME_ADAPTER_ID: "86c8dbbfda387925e345fde14bdfdb3681c2b02e5072e5b84bfb7000e1aef65c",
}

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

GREPTIME_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
MAX_GREPTIME_ROWS = 200
MAX_GREPTIME_COLUMNS = 128


class Wave27Context(Protocol):
    adapter_id: str
    settings: Mapping[str, str | int]
    credentials: Mapping[str, str]


def _exact_keys(arguments: Mapping[str, Any], allowed: frozenset[str]) -> None:
    if set(arguments) - allowed:
        raise ValueError("wave27_argument_contract_mismatch")


def _rfc3339(value: object) -> tuple[str, float]:
    if not isinstance(value, str) or not value.strip() or len(value) > 64:
        raise ValueError("invalid_time")
    clean = value.strip()
    try:
        parsed = dt.datetime.fromisoformat(clean.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid_time") from exc
    if parsed.tzinfo is None:
        raise ValueError("invalid_time")
    timestamp = parsed.timestamp()
    if not math.isfinite(timestamp) or timestamp < 0:
        raise ValueError("invalid_time")
    normalized = parsed.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return normalized, timestamp


def _greptime_range(start: object, end: object) -> tuple[str, str]:
    start_text, start_value = _rfc3339(start)
    end_text, end_value = _rfc3339(end)
    if end_value <= start_value or end_value - start_value > 24 * 60 * 60:
        raise ValueError("greptime_range_denied")
    return start_text, end_text


def validate_wave27_arguments(
    adapter_id: str,
    tool_name: str,
    arguments: Mapping[str, Any],
) -> None:
    if adapter_id == GREPTIME_ADAPTER_ID:
        if tool_name in {"describe_table", "health_check"}:
            _exact_keys(arguments, frozenset())
            return
        if tool_name == "query_range":
            _exact_keys(arguments, frozenset({"start", "end", "limit"}))
            _greptime_range(arguments.get("start"), arguments.get("end"))
            if "limit" in arguments:
                _integer(
                    arguments["limit"],
                    minimum=1,
                    maximum=MAX_GREPTIME_ROWS,
                    code="invalid_limit",
                )
            return
    raise ValueError("wave27_tool_denied")


def _base_url(context: Wave27Context) -> str:
    scheme = "http" if context.settings.get("tls_mode") == "test-only-plaintext" else "https"
    return f"{scheme}://{context.settings['host']}:{int(context.settings['port'])}"


def _bounded_json_response(response: httpx.Response) -> Any:
    if 300 <= response.status_code < 400:
        raise ValueError("database_redirect_denied")
    if response.status_code == 429:
        raise ValueError("database_rate_limited")
    if response.status_code >= 500:
        raise ValueError("database_upstream_unavailable")
    if response.status_code >= 400:
        raise ValueError("database_provider_rejected")
    length = response.headers.get("content-length")
    if length is not None:
        try:
            declared = int(length)
        except ValueError as exc:
            raise ValueError("database_response_invalid") from exc
        if declared < 0 or declared > MAX_OUTPUT_BYTES:
            raise ValueError("output_too_large")
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > MAX_OUTPUT_BYTES:
            raise ValueError("output_too_large")
        chunks.append(chunk)
    try:
        return json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("database_invalid_json") from exc


def _greptime_sql(context: Wave27Context, sql: str) -> Any:
    basic = base64.b64encode(
        f"{context.settings['username']}:{context.credentials['password']}".encode("utf-8")
    ).decode("ascii")
    headers = {
        "Accept": "application/json",
        "Authorization": f"Basic {basic}",
        "User-Agent": "ModelMirror-GreptimeDB-ReadOnly/1",
    }
    verify = context.settings.get("tls_mode") != "test-only-plaintext"
    try:
        with httpx.Client(
            base_url=_base_url(context),
            headers=headers,
            timeout=httpx.Timeout(PROVIDER_TIMEOUT_SECONDS, connect=10.0),
            verify=verify,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            with client.stream(
                "POST",
                "/v1/sql",
                params={"db": str(context.settings["database"])},
                data={"sql": sql},
            ) as response:
                return _bounded_json_response(response)
    except httpx.TimeoutException as exc:
        raise ValueError("database_upstream_timeout") from exc
    except httpx.HTTPError as exc:
        raise ValueError("database_upstream_unavailable") from exc


def _greptime_records(value: object, *, limit: int) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("code") not in {0, None}:
        raise ValueError("greptime_response_invalid")
    outputs = value.get("output")
    if not isinstance(outputs, list) or len(outputs) != 1 or not isinstance(outputs[0], dict):
        raise ValueError("greptime_response_invalid")
    records = outputs[0].get("records")
    if not isinstance(records, dict):
        raise ValueError("greptime_response_invalid")
    schema = records.get("schema")
    columns_raw = schema.get("column_schemas") if isinstance(schema, dict) else None
    rows = records.get("rows")
    if not isinstance(columns_raw, list) or not isinstance(rows, list):
        raise ValueError("greptime_response_invalid")
    if len(columns_raw) > MAX_GREPTIME_COLUMNS:
        raise ValueError("greptime_response_invalid")
    columns: list[str] = []
    for item in columns_raw:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ValueError("greptime_response_invalid")
        columns.append(item["name"])
    for row in rows:
        if not isinstance(row, list) or len(row) != len(columns):
            raise ValueError("greptime_response_invalid")
    return {
        "columns": columns,
        "rows": rows[:limit],
        "returned_count": min(len(rows), limit),
        "truncated": len(rows) > limit,
    }


def _identifier(context: Wave27Context, setting: str) -> str:
    value = str(context.settings[setting])
    if not GREPTIME_IDENTIFIER.fullmatch(value):
        raise ValueError("greptime_identifier_invalid")
    return f"`{value}`"


def _greptime_range_sql(context: Wave27Context, start: str, end: str, limit: int) -> str:
    table = _identifier(context, "table")
    time_column = _identifier(context, "time_column")
    value_column = _identifier(context, "value_column")
    # start/end are canonical RFC3339 values produced by _greptime_range and
    # contain no quote characters. Every identifier is fixed by configuration.
    return (
        f"SELECT {time_column}, {value_column} FROM {table} "
        f"WHERE {time_column} >= '{start}' AND {time_column} <= '{end}' "
        f"ORDER BY {time_column} ASC LIMIT {limit + 1}"
    )


def build_greptime(context: Wave27Context) -> FastMCP:
    mcp = FastMCP("ModelMirror GreptimeDB Read Only")

    @mcp.tool(annotations=READ_ONLY)
    def describe_table() -> dict[str, Any]:
        """Describe the one project-bound GreptimeDB table."""
        table = str(context.settings["table"])
        database = str(context.settings["database"])
        sql = (
            "SELECT column_name, data_type, semantic_type, is_nullable "
            "FROM information_schema.columns "
            f"WHERE table_schema = '{database}' AND table_name = '{table}' "
            f"ORDER BY ordinal_position LIMIT {MAX_GREPTIME_COLUMNS + 1}"
        )
        return _greptime_records(
            _greptime_sql(context, sql),
            limit=MAX_GREPTIME_COLUMNS,
        )

    @mcp.tool(annotations=READ_ONLY)
    def query_range(start: str, end: str, limit: int = 200) -> dict[str, Any]:
        """Read fixed time/value columns for at most 24 hours and 200 rows."""
        start_text, end_text = _greptime_range(start, end)
        safe_limit = _integer(
            limit,
            minimum=1,
            maximum=MAX_GREPTIME_ROWS,
            code="invalid_limit",
        )
        sql = _greptime_range_sql(context, start_text, end_text, safe_limit)
        return _greptime_records(_greptime_sql(context, sql), limit=safe_limit)

    @mcp.tool(annotations=READ_ONLY)
    def health_check() -> dict[str, str]:
        """Verify the bound read-only principal with a constant query."""
        result = _greptime_records(
            _greptime_sql(context, "SELECT 1 AS modelmirror_readonly LIMIT 1"),
            limit=1,
        )
        if not result["rows"]:
            raise ValueError("greptime_response_invalid")
        return {"status": "ok"}

    return mcp


def preflight_greptime(context: Wave27Context) -> None:
    result = _greptime_records(
        _greptime_sql(context, "SELECT 1 AS modelmirror_readonly LIMIT 1"),
        limit=1,
    )
    if not result["rows"]:
        raise RuntimeError("database_preflight_failed")
