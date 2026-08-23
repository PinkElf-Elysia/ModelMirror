from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence


MIN_PROVIDER_CHAT_GATE_REQUESTS = 500
MIN_PROVIDER_CHAT_GATE_DAYS = 14.0
MIN_PROVIDER_CHAT_GATE_SUCCESS_RATE = 0.99
MIN_PROVIDER_CHAT_GATE_MODEL_SUCCESSES = 10

REQUIRED_PROVIDER_CHAT_DRILLS = (
    "auth_failure",
    "http_429",
    "http_5xx",
    "connect_timeout",
    "read_timeout",
    "empty_stream",
    "invalid_sse",
    "stream_interrupted",
    "service_restart",
    "credential_invalid",
    "data_plane_offline",
    "preferred_fallback",
)


@dataclass(frozen=True, slots=True)
class ProviderChatGateEvaluation:
    ready: bool
    request_count: int
    success_count: int
    hard_failure_count: int
    observed_days: float
    success_rate: float | None
    model_successes: dict[str, int]
    blocking_reason_codes: tuple[str, ...]


def evaluate_provider_chat_gate(
    summary: Mapping[str, object],
    *,
    stable_model_ids: Sequence[str],
) -> ProviderChatGateEvaluation:
    request_count = _integer(summary.get("request_count"))
    success_count = _integer(summary.get("success_count"))
    hard_failure_count = _integer(summary.get("hard_failure_count"))
    observed_days = _float(summary.get("observed_days"))
    success_rate = (
        success_count / request_count if request_count > 0 else None
    )
    raw_model_successes = summary.get("model_successes")
    model_successes = {
        str(model_id): _integer(count)
        for model_id, count in (
            raw_model_successes.items()
            if isinstance(raw_model_successes, Mapping)
            else []
        )
    }
    blockers: list[str] = []
    if request_count < MIN_PROVIDER_CHAT_GATE_REQUESTS:
        blockers.append("provider_chat_gate_request_count_insufficient")
    if observed_days < MIN_PROVIDER_CHAT_GATE_DAYS:
        blockers.append("provider_chat_gate_observation_window_insufficient")
    if success_rate is None or success_rate < MIN_PROVIDER_CHAT_GATE_SUCCESS_RATE:
        blockers.append("provider_chat_gate_success_rate_insufficient")
    if any(
        model_successes.get(model_id, 0)
        < MIN_PROVIDER_CHAT_GATE_MODEL_SUCCESSES
        for model_id in stable_model_ids
    ):
        blockers.append("provider_chat_gate_model_samples_insufficient")
    if hard_failure_count:
        blockers.append("provider_chat_gate_hard_failure_observed")
    return ProviderChatGateEvaluation(
        ready=not blockers,
        request_count=request_count,
        success_count=success_count,
        hard_failure_count=hard_failure_count,
        observed_days=observed_days,
        success_rate=success_rate,
        model_successes=model_successes,
        blocking_reason_codes=tuple(blockers),
    )


def validate_provider_chat_drills(drills: Mapping[str, object]) -> list[str]:
    unknown = sorted(set(drills) - set(REQUIRED_PROVIDER_CHAT_DRILLS))
    if unknown:
        return ["provider_chat_gate_unknown_drill"]
    return [
        f"provider_chat_gate_drill_required:{name}"
        for name in REQUIRED_PROVIDER_CHAT_DRILLS
        if not bool(drills.get(name))
    ]


def _integer(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _float(value: object) -> float:
    try:
        return max(0.0, float(value or 0.0))
    except (TypeError, ValueError):
        return 0.0
