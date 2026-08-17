from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Iterable, Mapping, Sequence


REQUIRED_DRILLS = (
    "timeout",
    "http_429",
    "http_5xx",
    "empty_stream",
    "stream_interrupted",
    "strict_budget",
    "connection_disabled",
    "service_restart",
)

HARD_CONSTRAINT_OUTCOMES = {
    "batch_violation",
    "capability_violation",
    "connection_disabled_violation",
    "hard_constraint_violation",
    "modality_violation",
    "strict_budget_violation",
}


def percentile(values: Iterable[float | int | None], percentile_value: float) -> float | None:
    samples = sorted(
        float(value)
        for value in values
        if value is not None and math.isfinite(float(value))
    )
    if not samples:
        return None
    if len(samples) == 1:
        return samples[0]
    position = (len(samples) - 1) * max(0.0, min(1.0, percentile_value))
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return samples[lower]
    weight = position - lower
    return samples[lower] * (1 - weight) + samples[upper] * weight


def _parse_timestamp(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _rate(samples: Sequence[Mapping[str, object]], outcomes: set[str]) -> float | None:
    if not samples:
        return None
    return sum(str(item.get("outcome") or "") in outcomes for item in samples) / len(samples)


def _success_rate(samples: Sequence[Mapping[str, object]]) -> float | None:
    if not samples:
        return None
    return sum(bool(item.get("success")) for item in samples) / len(samples)


def _normalized_e2e(item: Mapping[str, object]) -> float | None:
    try:
        e2e_ms = float(item.get("e2e_ms"))
        output_tokens = int(item.get("output_tokens"))
    except (TypeError, ValueError):
        return None
    if e2e_ms < 0 or output_tokens <= 0:
        return None
    return e2e_ms * 100 / output_tokens


def evaluate_native_gate(
    native_samples: Sequence[Mapping[str, object]],
    sidecar_samples: Sequence[Mapping[str, object]],
    *,
    algorithm_version: str,
    config_hash: str,
    approval: Mapping[str, object] | None,
    now: datetime | None = None,
) -> dict[str, object]:
    current_time = now or datetime.now(UTC)
    native_count = len(native_samples)
    sidecar_count = len(sidecar_samples)
    timestamps = sorted(
        timestamp
        for timestamp in (
            _parse_timestamp(item.get("created_at")) for item in native_samples
        )
        if timestamp is not None
    )
    first_request_at = timestamps[0].isoformat() if timestamps else None
    last_request_at = timestamps[-1].isoformat() if timestamps else None
    observed_days = (
        (timestamps[-1] - timestamps[0]).total_seconds() / 86_400
        if len(timestamps) >= 2
        else 0.0
    )
    recent_observation = bool(
        timestamps
        and (current_time - timestamps[-1]).total_seconds() <= 86_400
    )
    native_success = _success_rate(native_samples)
    sidecar_success = _success_rate(sidecar_samples)
    native_stream_failure = _rate(
        native_samples, {"empty_stream", "stream_interrupted"}
    )
    sidecar_stream_failure = _rate(
        sidecar_samples, {"empty_stream", "stream_interrupted"}
    )
    native_ttft_p95 = percentile(
        (item.get("ttft_ms") for item in native_samples), 0.95
    )
    sidecar_ttft_p95 = percentile(
        (item.get("ttft_ms") for item in sidecar_samples), 0.95
    )
    native_e2e_p95 = percentile(
        (_normalized_e2e(item) for item in native_samples), 0.95
    )
    sidecar_e2e_p95 = percentile(
        (_normalized_e2e(item) for item in sidecar_samples), 0.95
    )
    planning_p95 = percentile(
        (item.get("planning_latency_ms") for item in native_samples), 0.95
    )
    hard_violations = sum(
        str(item.get("outcome") or "") in HARD_CONSTRAINT_OUTCOMES
        for item in native_samples
    )
    success_gate = bool(
        native_success is not None
        and sidecar_success is not None
        and native_success >= 0.98
        and native_success >= sidecar_success - 0.01
    )
    stream_gate = bool(
        native_stream_failure is not None
        and sidecar_stream_failure is not None
        and native_stream_failure <= 0.005
        and native_stream_failure <= sidecar_stream_failure + 0.0025
    )
    ttft_gate = bool(
        native_ttft_p95 is not None
        and sidecar_ttft_p95 is not None
        and native_ttft_p95
        <= sidecar_ttft_p95 + max(sidecar_ttft_p95 * 0.10, 150.0)
    )
    e2e_gate = bool(
        native_e2e_p95 is not None
        and sidecar_e2e_p95 is not None
        and native_e2e_p95
        <= sidecar_e2e_p95 + max(sidecar_e2e_p95 * 0.10, 250.0)
    )
    planning_gate = planning_p95 is not None and planning_p95 < 10.0
    automatic_checks = {
        "request_gate_met": native_count >= 500,
        "duration_gate_met": observed_days >= 14,
        "recent_observation_gate_met": recent_observation,
        "sidecar_baseline_gate_met": sidecar_count >= 100,
        "success_gate_met": success_gate,
        "stream_integrity_gate_met": stream_gate,
        "ttft_gate_met": ttft_gate,
        "normalized_e2e_gate_met": e2e_gate,
        "planning_latency_gate_met": planning_gate,
        "hard_constraint_gate_met": hard_violations == 0,
    }
    automatic_allowed = all(automatic_checks.values())
    approval_valid = bool(
        approval
        and str(approval.get("algorithm_version") or "") == algorithm_version
        and str(approval.get("config_hash") or "") == config_hash
        and bool(approval.get("no_open_p0_p1"))
        and all(
            bool((approval.get("drills") or {}).get(drill))
            for drill in REQUIRED_DRILLS
        )
    )
    labels = {
        "request_gate_met": "原生有效请求不足 500 次",
        "duration_gate_met": "首末有效观测跨度不足 14 天",
        "recent_observation_gate_met": "最近 24 小时没有原生有效观测",
        "sidecar_baseline_gate_met": "同期侧车有效对照不足 100 条",
        "success_gate_met": "成功率门禁未通过",
        "stream_integrity_gate_met": "空流或流中断门禁未通过",
        "ttft_gate_met": "TTFT P95 门禁未通过",
        "normalized_e2e_gate_met": "标准化 E2E P95 门禁未通过",
        "planning_latency_gate_met": "热缓存路由计算 P95 未低于 10ms",
        "hard_constraint_gate_met": "检测到硬约束违规",
    }
    blockers = [labels[name] for name, passed in automatic_checks.items() if not passed]
    if not approval_valid:
        blockers.append("缺少绑定当前算法版本与配置哈希的有效人工批准")
    return {
        "algorithm_version": algorithm_version,
        "config_hash": config_hash,
        "request_count": native_count,
        "sidecar_request_count": sidecar_count,
        "first_request_at": first_request_at,
        "last_request_at": last_request_at,
        "observed_days": round(max(0.0, observed_days), 2),
        "success_rate": native_success,
        "sidecar_success_rate": sidecar_success,
        "stream_failure_rate": native_stream_failure,
        "sidecar_stream_failure_rate": sidecar_stream_failure,
        "ttft_p95_ms": native_ttft_p95,
        "sidecar_ttft_p95_ms": sidecar_ttft_p95,
        "normalized_e2e_p95_ms_per_100_tokens": native_e2e_p95,
        "sidecar_normalized_e2e_p95_ms_per_100_tokens": sidecar_e2e_p95,
        "planning_latency_p95_ms": planning_p95,
        "hard_constraint_violation_count": hard_violations,
        **automatic_checks,
        "automatic_native_default_allowed": automatic_allowed,
        "approval_valid": approval_valid,
        "native_default_allowed": automatic_allowed and approval_valid,
        "manual_safety_gates_required": True,
        "blocking_reasons": blockers,
    }
