"""Reviewed VictoriaMetrics v1.20.2 fixed-metric read-only contract."""

from __future__ import annotations

import datetime as dt
import json
import math
from typing import Any, Mapping, Protocol

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .database_contracts import MAX_OUTPUT_BYTES
from .database_data_services import PROVIDER_TIMEOUT_SECONDS, _integer


VICTORIA_ADAPTER_ID = "victoriametrics-community-mcp-victoriametrics"
WAVE29_DATABASE_ADAPTERS = frozenset({VICTORIA_ADAPTER_ID})
WAVE29_DATABASE_UPSTREAM_LOCKS = {
    VICTORIA_ADAPTER_ID: {
        "version": "v1.20.2",
        "commit": "28a8c2319a8893d30a8b023b0c62734d31a5fe4e",
        "license": "Apache-2.0",
        "repository": "VictoriaMetrics/mcp-victoriametrics",
    }
}

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
MAX_SERIES = 100
MAX_LABELS = 200
MAX_SAMPLES = 2_000


class Wave29DatabaseContext(Protocol):
    adapter_id: str
    settings: Mapping[str, str | int]
    credentials: Mapping[str, str]


def _exact_keys(arguments: Mapping[str, Any], allowed: frozenset[str]) -> None:
    if set(arguments) - allowed:
        raise ValueError("wave29_database_argument_contract_mismatch")


def _rfc3339(value: object) -> tuple[str, float]:
    if not isinstance(value, str) or not value.strip() or len(value) > 64:
        raise ValueError("invalid_time")
    try:
        parsed = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid_time") from exc
    if parsed.tzinfo is None:
        raise ValueError("invalid_time")
    timestamp = parsed.timestamp()
    if not math.isfinite(timestamp) or timestamp < 0:
        raise ValueError("invalid_time")
    return (
        parsed.astimezone(dt.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        timestamp,
    )


def validate_wave29_database_arguments(
    adapter_id: str,
    tool_name: str,
    arguments: Mapping[str, Any],
) -> None:
    if adapter_id != VICTORIA_ADAPTER_ID:
        raise ValueError("wave29_database_tool_denied")
    if tool_name in {"metrics", "labels"}:
        _exact_keys(arguments, frozenset())
        return
    if tool_name == "query":
        _exact_keys(arguments, frozenset({"time"}))
        if arguments.get("time") not in {None, ""}:
            _rfc3339(arguments["time"])
        return
    if tool_name == "query_range":
        _exact_keys(arguments, frozenset({"start", "end", "step_seconds"}))
        _start, start_seconds = _rfc3339(arguments.get("start"))
        _end, end_seconds = _rfc3339(arguments.get("end"))
        if end_seconds <= start_seconds or end_seconds - start_seconds > 24 * 60 * 60:
            raise ValueError("victoriametrics_range_denied")
        step = _integer(
            arguments.get("step_seconds", 60),
            minimum=1,
            maximum=3_600,
            code="invalid_step",
        )
        if math.ceil((end_seconds - start_seconds) / step) + 1 > MAX_SAMPLES:
            raise ValueError("victoriametrics_sample_limit_exceeded")
        return
    raise ValueError("wave29_database_tool_denied")


def _base_url(context: Wave29DatabaseContext) -> str:
    scheme = "http" if context.settings.get("tls_mode") == "test-only-plaintext" else "https"
    return f"{scheme}://{context.settings['host']}:{int(context.settings['port'])}"


def _bounded_response(response: httpx.Response) -> Any:
    if 300 <= response.status_code < 400:
        raise ValueError("database_redirect_denied")
    if response.status_code == 429:
        raise ValueError("database_rate_limited")
    if response.status_code >= 500:
        raise ValueError("database_upstream_unavailable")
    if response.status_code >= 400:
        raise ValueError("database_provider_rejected")
    declared = response.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) < 0 or int(declared) > MAX_OUTPUT_BYTES:
                raise ValueError("output_too_large")
        except ValueError as exc:
            if str(exc) == "output_too_large":
                raise
            raise ValueError("database_response_invalid") from exc
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_bytes():
        size += len(chunk)
        if size > MAX_OUTPUT_BYTES:
            raise ValueError("output_too_large")
        chunks.append(chunk)
    try:
        return json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("database_invalid_json") from exc


def _request(
    context: Wave29DatabaseContext,
    path: str,
    params: Mapping[str, str | int],
) -> Any:
    headers = {
        "Accept": "application/json",
        "User-Agent": "ModelMirror-VictoriaMetrics-ReadOnly/1.20.2-compatible",
    }
    token = context.credentials.get("bearer_token")
    if token:
        headers["Authorization"] = f"Bearer {token}"
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
            with client.stream("GET", path, params=dict(params)) as response:
                return _bounded_response(response)
    except httpx.TimeoutException as exc:
        raise ValueError("database_upstream_timeout") from exc
    except httpx.HTTPError as exc:
        raise ValueError("database_upstream_unavailable") from exc


def _success_data(value: object) -> Any:
    if not isinstance(value, dict) or value.get("status") != "success" or "data" not in value:
        raise ValueError("victoriametrics_response_invalid")
    return value["data"]


def _bounded_names(value: object) -> dict[str, Any]:
    data = _success_data(value)
    if not isinstance(data, list) or any(not isinstance(item, str) for item in data):
        raise ValueError("victoriametrics_response_invalid")
    names = [item[:256] for item in data[:MAX_LABELS]]
    return {"items": names, "count": len(names), "truncated": len(data) > len(names)}


def _bounded_query(value: object, *, range_query: bool) -> dict[str, Any]:
    data = _success_data(value)
    if not isinstance(data, dict) or data.get("resultType") not in {
        "vector",
        "matrix",
        "scalar",
        "string",
    }:
        raise ValueError("victoriametrics_response_invalid")
    result = data.get("result")
    if not isinstance(result, list):
        raise ValueError("victoriametrics_response_invalid")
    projected: list[dict[str, Any]] = []
    for series in result[:MAX_SERIES]:
        if not isinstance(series, dict):
            raise ValueError("victoriametrics_response_invalid")
        metric = series.get("metric", {})
        if not isinstance(metric, dict) or len(metric) > 64:
            raise ValueError("victoriametrics_response_invalid")
        labels = {
            str(key)[:128]: str(item)[:1_000]
            for key, item in metric.items()
            if isinstance(key, str) and isinstance(item, str)
        }
        if len(labels) != len(metric):
            raise ValueError("victoriametrics_response_invalid")
        sample_key = "values" if range_query else "value"
        samples = series.get(sample_key)
        if range_query:
            if not isinstance(samples, list) or len(samples) > MAX_SAMPLES:
                raise ValueError("victoriametrics_response_invalid")
        elif not isinstance(samples, list) or len(samples) != 2:
            raise ValueError("victoriametrics_response_invalid")
        projected.append({"metric": labels, sample_key: samples})
    payload = {
        "result_type": data["resultType"],
        "series": projected,
        "series_count": len(projected),
        "truncated": len(result) > len(projected),
    }
    if len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()) > MAX_OUTPUT_BYTES:
        raise ValueError("output_too_large")
    return payload


def build_victoriametrics(context: Wave29DatabaseContext) -> FastMCP:
    mcp = FastMCP("ModelMirror VictoriaMetrics Read Only")

    @mcp.tool(annotations=READ_ONLY)
    def metrics() -> dict[str, Any]:
        """List a bounded set of metric names from the configured instance."""

        return _bounded_names(
            _request(context, "/api/v1/label/__name__/values", {"limit": MAX_LABELS})
        )

    @mcp.tool(annotations=READ_ONLY)
    def labels() -> dict[str, Any]:
        """List a bounded set of label names from the configured instance."""

        return _bounded_names(_request(context, "/api/v1/labels", {"limit": MAX_LABELS}))

    @mcp.tool(annotations=READ_ONLY)
    def query(time: str = "") -> dict[str, Any]:
        """Query only the project-bound metric at one optional RFC3339 time."""

        params: dict[str, str | int] = {"query": str(context.settings["metric"])}
        if time:
            params["time"] = _rfc3339(time)[0]
        return _bounded_query(_request(context, "/api/v1/query", params), range_query=False)

    @mcp.tool(annotations=READ_ONLY)
    def query_range(start: str, end: str, step_seconds: int = 60) -> dict[str, Any]:
        """Read the project-bound metric for at most 24 hours and 2000 samples."""

        start_text, start_seconds = _rfc3339(start)
        end_text, end_seconds = _rfc3339(end)
        step = _integer(step_seconds, minimum=1, maximum=3_600, code="invalid_step")
        if end_seconds <= start_seconds or end_seconds - start_seconds > 24 * 60 * 60:
            raise ValueError("victoriametrics_range_denied")
        if math.ceil((end_seconds - start_seconds) / step) + 1 > MAX_SAMPLES:
            raise ValueError("victoriametrics_sample_limit_exceeded")
        return _bounded_query(
            _request(
                context,
                "/api/v1/query_range",
                {
                    "query": str(context.settings["metric"]),
                    "start": start_text,
                    "end": end_text,
                    "step": step,
                },
            ),
            range_query=True,
        )

    return mcp


def preflight_victoriametrics(context: Wave29DatabaseContext) -> None:
    value = _bounded_query(
        _request(
            context,
            "/api/v1/query",
            {"query": str(context.settings["metric"])},
        ),
        range_query=False,
    )
    if value["result_type"] != "vector" or value["series_count"] < 1:
        raise RuntimeError("database_preflight_failed")


WAVE29_DATABASE_SCHEMA_SHA256 = {
    VICTORIA_ADAPTER_ID: "df0a61fb43635b48c3b268bee77fbe672cf0fc373be95c7aa24f1e8d3ef8b64d",
}
