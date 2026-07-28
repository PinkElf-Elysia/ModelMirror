from __future__ import annotations

import json
from typing import Any, Mapping


HEADER_MAP = {
    "decision": "x-omniroute-decision",
    "actual_model": "x-omniroute-model",
    "provider": "x-omniroute-provider",
    "latency_ms": "x-omniroute-latency-ms",
    "response_cost_usd": "x-omniroute-response-cost",
    "tokens_in": "x-omniroute-tokens-in",
    "tokens_out": "x-omniroute-tokens-out",
    "fallback_attempts": "x-omniroute-fallback-attempts",
    "cache_hit": "x-omniroute-cache-hit",
    "request_id": "x-omniroute-request-id",
    "version": "x-omniroute-version",
}
HEADER_TO_FIELD = {header: field for field, header in HEADER_MAP.items()}


def _to_int(value: Any) -> int | None:
    try:
        return max(0, int(float(str(value))))
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    try:
        parsed = float(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _to_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "hit"}:
        return True
    if normalized in {"false", "0", "no", "miss"}:
        return False
    return None


def parse_omniroute_headers(headers: Mapping[str, str]) -> dict[str, Any]:
    lowered = {key.lower(): value for key, value in headers.items()}
    result: dict[str, Any] = {}
    for field, header in HEADER_MAP.items():
        value = lowered.get(header)
        if value is not None and value != "":
            result[field] = value
    for field in ("latency_ms", "tokens_in", "tokens_out", "fallback_attempts"):
        if field in result:
            result[field] = _to_int(result[field])
    if "response_cost_usd" in result:
        result["response_cost_usd"] = _to_float(result["response_cost_usd"])
    if "cache_hit" in result:
        result["cache_hit"] = _to_bool(result["cache_hit"])
    return result


def update_stream_state(line: str, state: dict[str, Any]) -> None:
    stripped = line.strip()
    if stripped.startswith(":"):
        comment = stripped[1:].strip()
        if "=" not in comment:
            return
        header, value = comment.split("=", 1)
        header = header.strip().lower()
        field = HEADER_TO_FIELD.get(header)
        if field is None:
            return
        parsed = parse_omniroute_headers({header: value.strip()})
        if field not in parsed:
            return
        if (
            field == "actual_model"
            and str(parsed[field]).startswith("auto")
            and state.get("actual_model")
            and not str(state["actual_model"]).startswith("auto")
        ):
            return
        if field in {"tokens_in", "tokens_out"} and state.get("_usage_observed"):
            return
        state[field] = parsed[field]
        return
    if not stripped.startswith("data:"):
        return
    data = stripped[5:].strip()
    if not data or data == "[DONE]":
        return
    try:
        payload = json.loads(data)
    except ValueError:
        return
    if not isinstance(payload, dict):
        return
    if isinstance(payload.get("model"), str) and payload["model"].strip():
        state["actual_model"] = payload["model"].strip()
    if isinstance(payload.get("provider"), str) and payload["provider"].strip():
        state["provider"] = payload["provider"].strip()

    choices = payload.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            candidate = choice.get("delta") or choice.get("message")
            if not isinstance(candidate, dict):
                continue
            content = candidate.get("content")
            if isinstance(content, str) and content:
                state["content_observed"] = True
            elif isinstance(content, list) and content:
                state["content_observed"] = True
            if candidate.get("images"):
                state["content_observed"] = True

    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return
    prompt_tokens = _to_int(usage.get("prompt_tokens") or usage.get("input_tokens"))
    completion_tokens = _to_int(
        usage.get("completion_tokens") or usage.get("output_tokens")
    )
    total_tokens = _to_int(usage.get("total_tokens"))
    if prompt_tokens is not None:
        state["tokens_in"] = prompt_tokens
    if completion_tokens is not None:
        state["tokens_out"] = completion_tokens
    if total_tokens is not None:
        state["tokens_total"] = total_tokens
    state["_usage_observed"] = True


def build_route_receipt(
    *,
    requested_model: str,
    header_state: dict[str, Any],
    stream_state: dict[str, Any],
) -> dict[str, Any]:
    tokens_in = stream_state.get("tokens_in", header_state.get("tokens_in"))
    tokens_out = stream_state.get("tokens_out", header_state.get("tokens_out"))
    tokens_total = stream_state.get("tokens_total")
    if tokens_total is None and isinstance(tokens_in, int) and isinstance(tokens_out, int):
        tokens_total = tokens_in + tokens_out
    response_cost = stream_state.get(
        "response_cost_usd",
        header_state.get("response_cost_usd"),
    )
    has_meaningful_usage = any(
        isinstance(value, int) and value > 0
        for value in (tokens_in, tokens_out, tokens_total)
    )
    if isinstance(response_cost, (int, float)) and (
        response_cost > 0 or has_meaningful_usage
    ):
        cost_kind = "actual"
    else:
        response_cost = None
        cost_kind = "unavailable"
    return {
        "requested_model": requested_model,
        "actual_model": stream_state.get("actual_model")
        or header_state.get("actual_model"),
        "provider": stream_state.get("provider") or header_state.get("provider"),
        "strategy": stream_state.get("decision") or header_state.get("decision"),
        "latency_ms": stream_state.get("latency_ms", header_state.get("latency_ms")),
        "tokens": {
            "input": tokens_in,
            "output": tokens_out,
            "total": tokens_total,
        },
        "response_cost_usd": response_cost,
        "cost_kind": cost_kind,
        "fallback_attempts": stream_state.get(
            "fallback_attempts",
            header_state.get("fallback_attempts", 0),
        ),
        "cache_hit": stream_state.get("cache_hit", header_state.get("cache_hit")),
        "request_id": stream_state.get("request_id") or header_state.get("request_id"),
        "version": stream_state.get("version") or header_state.get("version"),
    }


def route_receipt_sse(receipt: dict[str, Any]) -> bytes:
    payload = json.dumps(receipt, ensure_ascii=False, separators=(",", ":"))
    return f"event: route_receipt\ndata: {payload}\n\n".encode("utf-8")
