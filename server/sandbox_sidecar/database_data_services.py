"""Fixed native read-only facades for Wave-19A data services.

The reviewed upstream MCP projects establish product identity and tool intent,
but two of them also expose writes or broad control surfaces.  These facades
therefore speak only the providers' documented HTTPS APIs and construct every
path, header, and request body server-side.
"""

import base64
import datetime as dt
import json
import math
import os
import re
from typing import Annotated, Any, Mapping, Protocol
from urllib.parse import quote

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .database_contracts import MAX_ARGUMENT_BYTES, MAX_OUTPUT_BYTES


READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

PROMQL_PATTERN = re.compile(r"[^\x00\r\n]{1,4096}")
PROMETHEUS_METRIC = re.compile(r"[A-Za-z_:][A-Za-z0-9_:]{0,254}")
PROMETHEUS_POOL = re.compile(r"[A-Za-z0-9_.:/-]{1,200}")
PROMETHEUS_STEP = re.compile(r"([1-9][0-9]{0,5})(ms|s|m|h)")
ELASTIC_QUERY = re.compile(r"[^\x00\r\n]{1,2000}")
DOCUMENT_ID = re.compile(r"[^/\\\x00\r\n?#]{1,256}")
QDRANT_OFFSET = re.compile(r"[A-Za-z0-9_.:-]{1,200}")

MAX_PROMETHEUS_SERIES = 200
MAX_PROMETHEUS_POINTS = 1_000
MAX_QDRANT_POINTS = 100
MAX_QDRANT_VECTOR_DIMENSIONS = 4_096
MAX_ELASTIC_HITS = 100
PROVIDER_TIMEOUT_SECONDS = 12.0


class DataServiceContext(Protocol):
    adapter_id: str
    settings: Mapping[str, str | int]
    credentials: Mapping[str, str]


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


def _text(value: object, pattern: re.Pattern[str], code: str) -> str:
    if not isinstance(value, str):
        raise ValueError(code)
    clean = value.strip()
    if not pattern.fullmatch(clean):
        raise ValueError(code)
    return clean


def validate_promql(value: object) -> str:
    clean = _text(value, PROMQL_PATTERN, "invalid_promql")
    # PromQL is read-only, but bound syntactic fan-out and pathological input.
    if clean.count("{") != clean.count("}") or clean.count("(") != clean.count(")"):
        raise ValueError("invalid_promql")
    if clean.count("{") > 32 or clean.count("(") > 64 or clean.count("[") > 32:
        raise ValueError("promql_too_complex")
    return clean


def _timestamp(value: object) -> float:
    if not isinstance(value, str) or not value.strip() or len(value) > 64:
        raise ValueError("invalid_prometheus_time")
    clean = value.strip()
    try:
        parsed = float(clean)
    except ValueError:
        try:
            parsed_dt = dt.datetime.fromisoformat(clean.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("invalid_prometheus_time") from exc
        if parsed_dt.tzinfo is None:
            raise ValueError("invalid_prometheus_time")
        parsed = parsed_dt.timestamp()
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError("invalid_prometheus_time")
    return parsed


def _step_seconds(value: object) -> tuple[str, float]:
    clean = _text(value, PROMETHEUS_STEP, "invalid_prometheus_step")
    match = PROMETHEUS_STEP.fullmatch(clean)
    assert match is not None
    amount = int(match.group(1))
    multiplier = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}[match.group(2)]
    seconds = amount * multiplier
    if seconds < 15 or seconds > 3600:
        raise ValueError("invalid_prometheus_step")
    return clean, seconds


def validate_prometheus_range(start: object, end: object, step: object) -> tuple[str, str, str]:
    start_text = str(start or "").strip()
    end_text = str(end or "").strip()
    start_value = _timestamp(start_text)
    end_value = _timestamp(end_text)
    step_text, step_seconds = _step_seconds(step)
    duration = end_value - start_value
    if duration <= 0 or duration > 24 * 60 * 60:
        raise ValueError("prometheus_range_denied")
    if math.ceil(duration / step_seconds) + 1 > MAX_PROMETHEUS_POINTS:
        raise ValueError("prometheus_range_too_dense")
    return start_text, end_text, step_text


def validate_vector(value: object) -> list[float]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_QDRANT_VECTOR_DIMENSIONS:
        raise ValueError("invalid_qdrant_vector")
    vector: list[float] = []
    for item in value:
        if isinstance(item, bool):
            raise ValueError("invalid_qdrant_vector")
        try:
            number = float(item)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid_qdrant_vector") from exc
        if not math.isfinite(number) or abs(number) > 1_000_000:
            raise ValueError("invalid_qdrant_vector")
        vector.append(number)
    if len(json.dumps(vector, separators=(",", ":")).encode("utf-8")) > 64 * 1024:
        raise ValueError("invalid_qdrant_vector")
    return vector


def validate_qdrant_offset(value: object) -> str | int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("invalid_qdrant_offset")
    if isinstance(value, int):
        if value < 0:
            raise ValueError("invalid_qdrant_offset")
        return value
    return _text(value, QDRANT_OFFSET, "invalid_qdrant_offset")


def validate_data_service_arguments(adapter_id: str, tool_name: str, arguments: Mapping[str, Any]) -> None:
    if adapter_id == "pab1it0-prometheus-mcp-server":
        if tool_name in {"execute_query", "execute_range_query"}:
            validate_promql(arguments.get("query"))
        if tool_name == "execute_query" and arguments.get("time") is not None:
            _timestamp(arguments.get("time"))
        if tool_name == "execute_range_query":
            validate_prometheus_range(arguments.get("start"), arguments.get("end"), arguments.get("step"))
        for field in ("limit", "offset"):
            if field in arguments and arguments[field] is not None:
                _integer(
                    arguments[field],
                    minimum=0 if field == "offset" else 1,
                    maximum=10_000 if field == "offset" else MAX_PROMETHEUS_SERIES,
                    code=f"invalid_{field}",
                )
        if arguments.get("metric") is not None:
            _text(arguments["metric"], PROMETHEUS_METRIC, "invalid_metric")
        if arguments.get("scrape_pool") is not None:
            _text(arguments["scrape_pool"], PROMETHEUS_POOL, "invalid_scrape_pool")
        if arguments.get("filter_pattern") is not None:
            pattern = str(arguments["filter_pattern"])
            if not pattern or len(pattern) > 100 or "\x00" in pattern:
                raise ValueError("invalid_filter_pattern")
        if arguments.get("refresh_cache") not in {None, False}:
            raise ValueError("refresh_cache_denied")
        if tool_name == "get_targets" and arguments.get("state", "active") != "active":
            raise ValueError("prometheus_target_state_denied")
    elif adapter_id == "qdrant-mcp-server-qdrant":
        if tool_name == "query_points":
            validate_vector(arguments.get("vector"))
        if tool_name == "scroll_points" and "offset" in arguments:
            validate_qdrant_offset(arguments.get("offset"))
        if "limit" in arguments:
            _integer(arguments["limit"], minimum=1, maximum=MAX_QDRANT_POINTS, code="invalid_limit")
        if "with_payload" in arguments and not isinstance(arguments["with_payload"], bool):
            raise ValueError("invalid_with_payload")
    elif adapter_id == "cr7258-elasticsearch-mcp-server":
        if tool_name == "search_documents":
            _text(arguments.get("query"), ELASTIC_QUERY, "invalid_elasticsearch_query")
            if "max_rows" in arguments:
                _integer(arguments["max_rows"], minimum=1, maximum=MAX_ELASTIC_HITS, code="invalid_row_limit")
        if tool_name == "get_document":
            _text(arguments.get("id"), DOCUMENT_ID, "invalid_document_id")


def _base_url(context: DataServiceContext) -> str:
    scheme = "http" if context.settings.get("tls_mode") == "test-only-plaintext" else "https"
    return f"{scheme}://{context.settings['host']}:{int(context.settings['port'])}"


def _headers(context: DataServiceContext) -> dict[str, str]:
    headers = {"Accept": "application/json", "User-Agent": "ModelMirror-Database-ReadOnly/1"}
    if context.adapter_id == "pab1it0-prometheus-mcp-server":
        token = context.credentials.get("bearer_token")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    elif context.adapter_id == "qdrant-mcp-server-qdrant":
        headers["api-key"] = context.credentials["api_key"]
    elif context.adapter_id == "cr7258-elasticsearch-mcp-server":
        basic = base64.b64encode(
            f"{context.settings['username']}:{context.credentials['password']}".encode("utf-8")
        ).decode("ascii")
        headers["Authorization"] = f"Basic {basic}"
    elif context.adapter_id == "zilliztech-mcp-server-milvus":
        token = f"{context.settings['username']}:{context.credentials['password']}"
        headers["Authorization"] = f"Bearer {token}"
        headers["Request-Timeout"] = "10"
    elif context.adapter_id in {"neo4j-contrib-mcp-neo4j", "arcadedata-arcadedb"}:
        basic = base64.b64encode(
            f"{context.settings['username']}:{context.credentials['password']}".encode("utf-8")
        ).decode("ascii")
        headers["Authorization"] = f"Basic {basic}"
    return headers


def _request_json(
    context: DataServiceContext,
    method: str,
    path: str,
    *,
    params: Mapping[str, str | int] | None = None,
    payload: Mapping[str, Any] | None = None,
) -> Any:
    if method not in {"GET", "POST"} or not path.startswith("/") or ".." in path:
        raise ValueError("database_request_denied")
    if payload is not None:
        encoded_payload = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded_payload) > MAX_ARGUMENT_BYTES:
            raise ValueError("database_request_too_large")
    verify = context.settings.get("tls_mode") != "test-only-plaintext"
    try:
        with httpx.Client(
            base_url=_base_url(context),
            headers=_headers(context),
            timeout=httpx.Timeout(PROVIDER_TIMEOUT_SECONDS, connect=10.0),
            verify=verify,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            with client.stream(method, path, params=params, json=payload) as response:
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
                        declared_length = int(length)
                    except ValueError as exc:
                        raise ValueError("database_response_invalid") from exc
                    if declared_length < 0:
                        raise ValueError("database_response_invalid")
                    if declared_length > MAX_OUTPUT_BYTES:
                        raise ValueError("output_too_large")
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > MAX_OUTPUT_BYTES:
                        raise ValueError("output_too_large")
                    chunks.append(chunk)
    except httpx.TimeoutException as exc:
        raise ValueError("database_upstream_timeout") from exc
    except httpx.HTTPError as exc:
        raise ValueError("database_upstream_unavailable") from exc
    try:
        return json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("database_invalid_json") from exc


def _prometheus_data(context: DataServiceContext, endpoint: str, params: Mapping[str, str | int] | None = None) -> Any:
    payload = _request_json(context, "GET", f"/api/v1/{endpoint}", params=params)
    if not isinstance(payload, dict) or payload.get("status") != "success" or "data" not in payload:
        raise ValueError("prometheus_response_invalid")
    return payload["data"]


def _bounded_list(value: object, limit: int) -> tuple[list[Any], bool]:
    if not isinstance(value, list):
        raise ValueError("database_response_invalid")
    return value[:limit], len(value) > limit


def build_prometheus(context: DataServiceContext) -> FastMCP:
    mcp = FastMCP("ModelMirror Prometheus Read Only")

    @mcp.tool(annotations=READ_ONLY)
    def execute_query(query: str, time: str | None = None) -> dict[str, Any]:
        """Execute one bounded read-only PromQL instant query."""
        clean = validate_promql(query)
        params: dict[str, str] = {"query": clean}
        if time is not None:
            _timestamp(time)
            params["time"] = time.strip()
        data = _prometheus_data(context, "query", params)
        if not isinstance(data, dict):
            raise ValueError("prometheus_response_invalid")
        result, truncated = _bounded_list(data.get("result"), MAX_PROMETHEUS_SERIES)
        return {"resultType": data.get("resultType"), "result": result, "truncated": truncated}

    @mcp.tool(annotations=READ_ONLY)
    def execute_range_query(query: str, start: str, end: str, step: str) -> dict[str, Any]:
        """Execute one range query limited to 24 hours and 1000 points per series."""
        clean = validate_promql(query)
        start_value, end_value, step_value = validate_prometheus_range(start, end, step)
        data = _prometheus_data(
            context,
            "query_range",
            {"query": clean, "start": start_value, "end": end_value, "step": step_value},
        )
        if not isinstance(data, dict):
            raise ValueError("prometheus_response_invalid")
        result, truncated = _bounded_list(data.get("result"), MAX_PROMETHEUS_SERIES)
        return {"resultType": data.get("resultType"), "result": result, "truncated": truncated}

    @mcp.tool(annotations=READ_ONLY)
    def list_metrics(limit: int = 100, offset: int = 0, filter_pattern: str | None = None) -> dict[str, Any]:
        """List bounded metric names without refreshing or mutating provider state."""
        safe_limit = _integer(limit, minimum=1, maximum=MAX_PROMETHEUS_SERIES, code="invalid_limit")
        safe_offset = _integer(offset, minimum=0, maximum=10_000, code="invalid_offset")
        values = _prometheus_data(context, "label/__name__/values")
        if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
            raise ValueError("prometheus_response_invalid")
        if filter_pattern is not None:
            if not filter_pattern or len(filter_pattern) > 100 or "\x00" in filter_pattern:
                raise ValueError("invalid_filter_pattern")
            needle = filter_pattern.casefold()
            values = [item for item in values if needle in item.casefold()]
        selected = values[safe_offset : safe_offset + safe_limit]
        return {
            "metrics": selected,
            "total_count": len(values),
            "returned_count": len(selected),
            "offset": safe_offset,
            "has_more": safe_offset + len(selected) < len(values),
        }

    @mcp.tool(annotations=READ_ONLY)
    def get_metric_metadata(metric: str | None = None, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        """Read bounded Prometheus metric type/help/unit metadata."""
        safe_limit = _integer(limit, minimum=1, maximum=MAX_PROMETHEUS_SERIES, code="invalid_limit")
        safe_offset = _integer(offset, minimum=0, maximum=10_000, code="invalid_offset")
        params: dict[str, str | int] = {"limit": safe_limit + safe_offset}
        if metric is not None:
            params["metric"] = _text(metric, PROMETHEUS_METRIC, "invalid_metric")
        data = _prometheus_data(context, "metadata", params)
        if not isinstance(data, dict):
            raise ValueError("prometheus_response_invalid")
        names = sorted(str(name) for name in data) if metric is None else [metric]
        selected = names[safe_offset : safe_offset + safe_limit]
        metadata = {name: data.get(name, []) for name in selected}
        return {
            "metadata": metadata,
            "total_count": len(names),
            "returned_count": len(selected),
            "offset": safe_offset,
            "has_more": safe_offset + len(selected) < len(names),
        }

    @mcp.tool(annotations=READ_ONLY)
    def get_targets(scrape_pool: str | None = None, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        """Read active scrape targets only; dropped-target enumeration is disabled."""
        safe_limit = _integer(limit, minimum=1, maximum=MAX_PROMETHEUS_SERIES, code="invalid_limit")
        safe_offset = _integer(offset, minimum=0, maximum=10_000, code="invalid_offset")
        params: dict[str, str] = {"state": "active"}
        if scrape_pool is not None:
            params["scrapePool"] = _text(scrape_pool, PROMETHEUS_POOL, "invalid_scrape_pool")
        data = _prometheus_data(context, "targets", params)
        if not isinstance(data, dict):
            raise ValueError("prometheus_response_invalid")
        active = data.get("activeTargets")
        if not isinstance(active, list):
            raise ValueError("prometheus_response_invalid")
        selected = active[safe_offset : safe_offset + safe_limit]
        return {
            "activeTargets": selected,
            "total_active": len(active),
            "returned_active": len(selected),
            "offset": safe_offset,
            "has_more": safe_offset + len(selected) < len(active),
        }

    return mcp


def preflight_prometheus(context: DataServiceContext) -> None:
    data = _prometheus_data(context, "status/buildinfo")
    if not isinstance(data, dict) or not isinstance(data.get("version"), str):
        raise RuntimeError("database_preflight_failed")


def _qdrant_result(context: DataServiceContext, method: str, path: str, payload: Mapping[str, Any] | None = None) -> Any:
    value = _request_json(context, method, path, payload=payload)
    if not isinstance(value, dict) or value.get("status") not in {"ok", None} or "result" not in value:
        raise ValueError("qdrant_response_invalid")
    return value["result"]


def _qdrant_collection_path(context: DataServiceContext, suffix: str = "") -> str:
    collection = quote(str(context.settings["collection"]), safe="")
    return f"/collections/{collection}{suffix}"


def build_qdrant(context: DataServiceContext) -> FastMCP:
    mcp = FastMCP("ModelMirror Qdrant Read Only")

    @mcp.tool(annotations=READ_ONLY)
    def get_collection_info() -> dict[str, Any]:
        """Describe the one project-bound Qdrant collection."""
        result = _qdrant_result(context, "GET", _qdrant_collection_path(context))
        if not isinstance(result, dict):
            raise ValueError("qdrant_response_invalid")
        return result

    @mcp.tool(annotations=READ_ONLY)
    def scroll_points(limit: int = 50, offset: str | int | None = None, with_payload: bool = True) -> dict[str, Any]:
        """Page through the bound collection without vectors or arbitrary filters."""
        safe_limit = _integer(limit, minimum=1, maximum=MAX_QDRANT_POINTS, code="invalid_limit")
        safe_offset = validate_qdrant_offset(offset)
        body: dict[str, Any] = {
            "limit": safe_limit,
            "with_payload": bool(with_payload),
            "with_vector": False,
        }
        if safe_offset is not None:
            body["offset"] = safe_offset
        result = _qdrant_result(context, "POST", _qdrant_collection_path(context, "/points/scroll"), body)
        if not isinstance(result, dict) or not isinstance(result.get("points"), list):
            raise ValueError("qdrant_response_invalid")
        points = result["points"][:safe_limit]
        return {"points": points, "next_page_offset": result.get("next_page_offset"), "truncated": len(result["points"]) > safe_limit}

    @mcp.tool(annotations=READ_ONLY)
    def query_points(vector: list[float], limit: int = 10, with_payload: bool = True) -> dict[str, Any]:
        """Search the bound collection with a caller-supplied numeric vector."""
        safe_vector = validate_vector(vector)
        safe_limit = _integer(limit, minimum=1, maximum=MAX_QDRANT_POINTS, code="invalid_limit")
        result = _qdrant_result(
            context,
            "POST",
            _qdrant_collection_path(context, "/points/query"),
            {"query": safe_vector, "limit": safe_limit, "with_payload": bool(with_payload), "with_vector": False},
        )
        points = result.get("points") if isinstance(result, dict) else result
        if not isinstance(points, list):
            raise ValueError("qdrant_response_invalid")
        return {"points": points[:safe_limit], "truncated": len(points) > safe_limit}

    return mcp


def preflight_qdrant(context: DataServiceContext) -> None:
    result = _qdrant_result(context, "GET", _qdrant_collection_path(context))
    if not isinstance(result, dict) or result.get("status") not in {"green", "yellow", "red"}:
        raise RuntimeError("database_preflight_failed")


def _elastic_path(context: DataServiceContext, suffix: str) -> str:
    index = quote(str(context.settings["index"]), safe="")
    return f"/{index}{suffix}"


def build_elasticsearch(context: DataServiceContext) -> FastMCP:
    mcp = FastMCP("ModelMirror Elasticsearch Read Only")

    @mcp.tool(annotations=READ_ONLY)
    def get_cluster_health() -> dict[str, Any]:
        """Read health for the one project-bound index."""
        index = quote(str(context.settings["index"]), safe="")
        value = _request_json(context, "GET", f"/_cluster/health/{index}")
        if not isinstance(value, dict):
            raise ValueError("elasticsearch_response_invalid")
        return {
            key: value.get(key)
            for key in (
                "cluster_name",
                "status",
                "timed_out",
                "number_of_nodes",
                "number_of_data_nodes",
                "active_primary_shards",
                "active_shards",
                "relocating_shards",
                "initializing_shards",
                "unassigned_shards",
            )
        }

    @mcp.tool(annotations=READ_ONLY)
    def get_index() -> dict[str, Any]:
        """Read mappings for the project-bound index; settings and aliases stay hidden."""
        value = _request_json(context, "GET", _elastic_path(context, "/_mapping"))
        if not isinstance(value, dict):
            raise ValueError("elasticsearch_response_invalid")
        return value

    @mcp.tool(annotations=READ_ONLY)
    def search_documents(query: str, max_rows: int = 50) -> dict[str, Any]:
        """Run one bounded match query on the configured search field."""
        clean = _text(query, ELASTIC_QUERY, "invalid_elasticsearch_query")
        limit = _integer(max_rows, minimum=1, maximum=MAX_ELASTIC_HITS, code="invalid_row_limit")
        value = _request_json(
            context,
            "POST",
            _elastic_path(context, "/_search"),
            payload={
                "size": limit,
                "track_total_hits": False,
                "query": {"match": {str(context.settings["search_field"]): {"query": clean}}},
            },
        )
        if not isinstance(value, dict) or not isinstance(value.get("hits"), dict):
            raise ValueError("elasticsearch_response_invalid")
        hits = value["hits"].get("hits")
        if not isinstance(hits, list):
            raise ValueError("elasticsearch_response_invalid")
        return {"hits": hits[:limit], "returned_count": min(len(hits), limit), "truncated": len(hits) > limit}

    @mcp.tool(annotations=READ_ONLY)
    def get_document(id: Annotated[str, Field(max_length=256)]) -> dict[str, Any]:
        """Read one document ID from the project-bound index."""
        clean = _text(id, DOCUMENT_ID, "invalid_document_id")
        value = _request_json(context, "GET", _elastic_path(context, f"/_doc/{quote(clean, safe='')}"))
        if not isinstance(value, dict):
            raise ValueError("elasticsearch_response_invalid")
        return value

    return mcp


def preflight_elasticsearch(context: DataServiceContext) -> None:
    value = _request_json(context, "GET", _elastic_path(context, "/_mapping"))
    if not isinstance(value, dict) or not value:
        raise RuntimeError("database_preflight_failed")
