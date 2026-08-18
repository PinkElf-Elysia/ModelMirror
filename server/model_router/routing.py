from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal

from .omniroute_parity import (
    ALGORITHM_VERSION,
    LEGACY_ALGORITHM_VERSION,
    select_ranked_candidate,
    speed_score,
)
from .schemas import RoutingMode


BreakerState = Literal["closed", "open", "half_open"]

FACTOR_NAMES = (
    "quota",
    "health",
    "cost_inverse",
    "latency_inverse",
    "task_fit",
    "stability",
    "tier_priority",
    "tier_affinity",
    "specificity_match",
    "context_affinity",
    "cache_affinity",
    "reset_window_affinity",
    "connection_density",
)

DEFAULT_WEIGHTS = {
    "quota": 0.15,
    "health": 0.20,
    "cost_inverse": 0.15,
    "latency_inverse": 0.12,
    "task_fit": 0.08,
    "stability": 0.05,
    "tier_priority": 0.05,
    "tier_affinity": 0.05,
    "specificity_match": 0.05,
    "context_affinity": 0.05,
    "cache_affinity": 0.00,
    "reset_window_affinity": 0.00,
    "connection_density": 0.05,
}

MODE_WEIGHTS: dict[RoutingMode, dict[str, float]] = {
    "auto": DEFAULT_WEIGHTS,
    "reliable": {
        "quota": 0.14,
        "health": 0.37,
        "cost_inverse": 0.04,
        "latency_inverse": 0.05,
        "task_fit": 0.10,
        "stability": 0.20,
        "tier_priority": 0.05,
        "tier_affinity": 0.00,
        "specificity_match": 0.00,
        "context_affinity": 0.00,
        "cache_affinity": 0.00,
        "reset_window_affinity": 0.00,
        "connection_density": 0.05,
    },
    "fast": {
        "quota": 0.14,
        "health": 0.28,
        "cost_inverse": 0.05,
        "latency_inverse": 0.32,
        "task_fit": 0.10,
        "stability": 0.00,
        "tier_priority": 0.05,
        "tier_affinity": 0.00,
        "specificity_match": 0.00,
        "context_affinity": 0.01,
        "cache_affinity": 0.00,
        "reset_window_affinity": 0.00,
        "connection_density": 0.05,
    },
    "cheap": {
        "quota": 0.14,
        "health": 0.19,
        "cost_inverse": 0.37,
        "latency_inverse": 0.05,
        "task_fit": 0.10,
        "stability": 0.05,
        "tier_priority": 0.05,
        "tier_affinity": 0.00,
        "specificity_match": 0.00,
        "context_affinity": 0.00,
        "cache_affinity": 0.00,
        "reset_window_affinity": 0.00,
        "connection_density": 0.05,
    },
    "quality": {
        "quota": 0.10,
        "health": 0.18,
        "cost_inverse": 0.05,
        "latency_inverse": 0.05,
        "task_fit": 0.37,
        "stability": 0.15,
        "tier_priority": 0.05,
        "tier_affinity": 0.00,
        "specificity_match": 0.00,
        "context_affinity": 0.00,
        "cache_affinity": 0.00,
        "reset_window_affinity": 0.00,
        "connection_density": 0.05,
    },
    "offline": {
        "quota": 0.37,
        "health": 0.28,
        "cost_inverse": 0.10,
        "latency_inverse": 0.05,
        "task_fit": 0.00,
        "stability": 0.10,
        "tier_priority": 0.05,
        "tier_affinity": 0.00,
        "specificity_match": 0.00,
        "context_affinity": 0.00,
        "cache_affinity": 0.00,
        "reset_window_affinity": 0.00,
        "connection_density": 0.05,
    },
}


def clamp01(value: float) -> float:
    if value != value:
        return 0.0
    return max(0.0, min(1.0, float(value)))


@dataclass(frozen=True, slots=True)
class RoutingCandidate:
    tenant_id: str
    connection_id: str
    connection_name: str
    model_id: str
    enabled: bool = True
    credential_available: bool = True
    input_modalities: frozenset[str] = frozenset({"text"})
    output_modalities: frozenset[str] = frozenset({"text"})
    capabilities: frozenset[str] = frozenset()
    context_length: int | None = None
    quota_remaining: float = 100.0
    cost_per_million_tokens: float | None = None
    estimated_request_cost: float | None = None
    p95_latency_ms: float | None = None
    avg_ttft_ms: float | None = None
    avg_e2e_latency_ms: float | None = None
    avg_tokens_per_second: float | None = None
    latency_stddev_ms: float | None = None
    error_rate: float = 0.0
    breaker_state: BreakerState = "closed"
    task_fit: float = 0.5
    tier_priority: float = 0.33
    tier_affinity: float = 0.5
    specificity_match: float = 0.5
    context_affinity: float = 0.5
    cache_affinity: float = 0.0
    reset_window_affinity: float = 0.5
    connection_pool_size: int = 1
    preference_tags: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class RoutingRequest:
    tenant_id: str
    mode: RoutingMode = "auto"
    required_input_modalities: frozenset[str] = frozenset({"text"})
    required_output_modalities: frozenset[str] = frozenset({"text"})
    required_capabilities: frozenset[str] = frozenset()
    estimated_input_tokens: int = 0
    max_output_tokens: int = 2048
    budget_usd: float | None = None
    budget_fallback: Literal["strict", "cheapest"] = "cheapest"
    preferred_tags: frozenset[str] = frozenset()
    last_known_good: tuple[str, str] | None = None
    excluded_paths: frozenset[tuple[str, str]] = frozenset()
    task_type: str = "medium"
    algorithm_version: str = ALGORITHM_VERSION
    rotator_key: str = "local:auto:medium"
    incident_mode: bool = False


@dataclass(frozen=True, slots=True)
class RankedCandidate:
    candidate: RoutingCandidate
    score: float
    factors: dict[str, float] | None = None


@dataclass(frozen=True, slots=True)
class RouterDecision:
    selected: RoutingCandidate
    ranked: tuple[RankedCandidate, ...]
    mode: RoutingMode
    reason_codes: tuple[str, ...]
    filtered_counts: dict[str, int] = field(default_factory=dict)
    algorithm_version: str = LEGACY_ALGORITHM_VERSION
    task_type: str = "medium"
    selection_kind: str = "ranked"
    score_tier: str = "top"
    eligible_count: int = 0
    finalist_count: int = 0


class NoEligibleCandidateError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        filtered_counts: dict[str, int] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.filtered_counts = filtered_counts or {}


def validate_weights(weights: dict[str, float]) -> bool:
    return set(weights) == set(FACTOR_NAMES) and abs(sum(weights.values()) - 1) < 0.01


def _hard_filter(
    candidates: list[RoutingCandidate],
    request: RoutingRequest,
) -> tuple[list[RoutingCandidate], dict[str, int]]:
    counts: dict[str, int] = {}
    eligible: list[RoutingCandidate] = []
    for candidate in candidates:
        reason: str | None = None
        if candidate.tenant_id != request.tenant_id:
            reason = "tenant"
        elif not candidate.enabled:
            reason = "connection_disabled"
        elif not candidate.credential_available:
            reason = "credential_unavailable"
        elif candidate.breaker_state == "open":
            reason = "breaker_open"
        elif (candidate.connection_id, candidate.model_id) in request.excluded_paths:
            reason = "request_excluded"
        elif not request.required_input_modalities.issubset(
            candidate.input_modalities
        ):
            reason = "input_modality"
        elif not request.required_output_modalities.issubset(
            candidate.output_modalities
        ):
            reason = "output_modality"
        elif not request.required_capabilities.issubset(candidate.capabilities):
            reason = "capability"
        elif (
            candidate.context_length is not None
            and request.estimated_input_tokens + request.max_output_tokens
            > candidate.context_length
        ):
            reason = "context_length"
        elif (
            request.budget_usd is not None
            and request.budget_fallback == "strict"
            and (
                candidate.estimated_request_cost is None
                or candidate.estimated_request_cost > request.budget_usd
            )
        ):
            reason = "strict_budget"
        if reason:
            counts[reason] = counts.get(reason, 0) + 1
        else:
            eligible.append(candidate)
    return eligible, counts


def _apply_soft_preferences(
    candidates: list[RoutingCandidate],
    request: RoutingRequest,
) -> tuple[list[RoutingCandidate], bool]:
    if not request.preferred_tags:
        return candidates, False
    preferred = [
        candidate
        for candidate in candidates
        if request.preferred_tags.intersection(candidate.preference_tags)
    ]
    return (preferred, False) if preferred else (candidates, True)


def _apply_soft_budget(
    candidates: list[RoutingCandidate],
    request: RoutingRequest,
) -> tuple[list[RoutingCandidate], bool]:
    if request.budget_usd is None or request.budget_fallback == "strict":
        return candidates, False
    within_budget = [
        candidate
        for candidate in candidates
        if candidate.estimated_request_cost is not None
        and candidate.estimated_request_cost <= request.budget_usd
    ]
    if within_budget:
        return within_budget, False
    known = [
        candidate
        for candidate in candidates
        if candidate.estimated_request_cost is not None
    ]
    if not known:
        return candidates, True
    cheapest = min(
        candidate.estimated_request_cost
        for candidate in known
        if candidate.estimated_request_cost is not None
    )
    return (
        [
            candidate
            for candidate in known
            if candidate.estimated_request_cost == cheapest
        ],
        True,
    )


def calculate_factors(
    candidate: RoutingCandidate,
    pool: list[RoutingCandidate],
    *,
    metric_context: dict[str, float] | None = None,
) -> dict[str, float]:
    metrics = metric_context or _metric_context(pool)
    metric_key = tuple(metrics[name] for name in (
        "cost", "p95", "ttft", "e2e", "tps", "stddev"
    ))
    values = _cached_factor_values(
        candidate,
        metric_key,
    )
    return dict(zip(FACTOR_NAMES, values, strict=True))


def _factor_values_uncached(
    candidate: RoutingCandidate,
    metrics: dict[str, float],
) -> tuple[float, ...]:
    max_cost = metrics["cost"]
    maxima = {
        "p95": metrics["p95"],
        "ttft": metrics["ttft"],
        "e2e": metrics["e2e"],
        "tps": metrics["tps"],
        "stddev": metrics["stddev"],
    }
    cost = candidate.cost_per_million_tokens
    cost_inverse = 0.0 if cost is None else 1 - cost / max(max_cost, 0.001)
    health = (
        1.0
        if candidate.breaker_state == "closed"
        else 0.5
        if candidate.breaker_state == "half_open"
        else 0.0
    )
    composite_speed = speed_score(
        ttft_ms=candidate.avg_ttft_ms,
        tps=candidate.avg_tokens_per_second,
        e2e_ms=candidate.avg_e2e_latency_ms,
        p95_ms=candidate.p95_latency_ms,
        stddev_ms=candidate.latency_stddev_ms,
        failure_rate=candidate.error_rate,
        breaker_state=candidate.breaker_state,
        maxima=maxima,
    )
    stddev = candidate.latency_stddev_ms
    stability = 0.5 if stddev is None else (
        1 - stddev / max(maxima["stddev"], 0.001)
    ) * (1 - candidate.error_rate)
    return (
        clamp01(candidate.quota_remaining / 100),
        health,
        clamp01(cost_inverse),
        composite_speed,
        clamp01(candidate.task_fit),
        clamp01(stability),
        clamp01(candidate.tier_priority),
        clamp01(candidate.tier_affinity),
        clamp01(candidate.specificity_match),
        clamp01(candidate.context_affinity),
        clamp01(candidate.cache_affinity),
        clamp01(candidate.reset_window_affinity),
        clamp01(
            (max(1, candidate.connection_pool_size) - 1) / 10
        ),
    )


@lru_cache(maxsize=8_192)
def _cached_factor_values(
    candidate: RoutingCandidate,
    metric_key: tuple[float, ...],
) -> tuple[float, ...]:
    return _factor_values_uncached(
        candidate,
        dict(
            zip(
                ("cost", "p95", "ttft", "e2e", "tps", "stddev"),
                metric_key,
                strict=True,
            )
        ),
    )


def _metric_context(pool: list[RoutingCandidate]) -> dict[str, float]:
    max_cost = 0.001
    max_p95 = 1.0
    max_ttft = 0.0
    max_e2e = 0.0
    max_tps = 1.0
    max_stddev = 0.001
    for item in pool:
        if item.cost_per_million_tokens is not None:
            max_cost = max(max_cost, item.cost_per_million_tokens)
        if item.p95_latency_ms is not None:
            max_p95 = max(max_p95, item.p95_latency_ms)
        if item.avg_ttft_ms is not None:
            max_ttft = max(max_ttft, item.avg_ttft_ms)
        if item.avg_e2e_latency_ms is not None:
            max_e2e = max(max_e2e, item.avg_e2e_latency_ms)
        if item.avg_tokens_per_second is not None:
            max_tps = max(max_tps, item.avg_tokens_per_second)
        if item.latency_stddev_ms is not None:
            max_stddev = max(max_stddev, item.latency_stddev_ms)
    return {
        "cost": max_cost,
        "p95": max_p95,
        "ttft": max_ttft or max_p95,
        "e2e": max_e2e or max_p95,
        "tps": max_tps,
        "stddev": max_stddev,
    }


def rank_candidates(
    candidates: list[RoutingCandidate],
    mode: RoutingMode,
) -> list[RankedCandidate]:
    weights = MODE_WEIGHTS[mode]
    metric_context = _metric_context(candidates)
    metric_key = tuple(metric_context[name] for name in (
        "cost", "p95", "ttft", "e2e", "tps", "stddev"
    ))
    weight_values = tuple(weights[name] for name in FACTOR_NAMES)
    ranked = []
    for candidate in candidates:
        factor_values = _cached_factor_values(candidate, metric_key)
        score = clamp01(
            sum(
                weight * value
                for weight, value in zip(
                    weight_values, factor_values, strict=True
                )
            )
        )
        ranked.append(
            RankedCandidate(candidate=candidate, score=score)
        )
    return sorted(
        ranked,
        key=lambda item: (
            -item.score,
            item.candidate.connection_id,
            item.candidate.model_id,
        ),
    )


def decide_route(
    candidates: list[RoutingCandidate],
    request: RoutingRequest,
) -> RouterDecision:
    eligible, filtered_counts = _hard_filter(candidates, request)
    if not eligible:
        code = (
            "strict_budget_exceeded"
            if filtered_counts.get("strict_budget")
            else "context_limit_exceeded"
            if filtered_counts.get("context_length")
            else "no_eligible_candidate"
        )
        message = (
            "没有可在严格预算内调用的模型。"
            if code == "strict_budget_exceeded"
            else "当前内容超过所有可用候选的上下文限制。"
            if code == "context_limit_exceeded"
            else "没有满足当前能力、连接状态和上下文要求的模型。"
        )
        raise NoEligibleCandidateError(
            code, message, filtered_counts=filtered_counts
        )

    if request.algorithm_version == LEGACY_ALGORITHM_VERSION:
        eligible, preference_fallback = _apply_soft_preferences(eligible, request)
    else:
        preference_fallback = False
    eligible, budget_fallback = _apply_soft_budget(eligible, request)
    reason_codes: list[str] = []
    if preference_fallback:
        reason_codes.append("preference_pool_fallback")
    if budget_fallback:
        reason_codes.append("soft_budget_cheapest_fallback")

    ranked = rank_candidates(eligible, request.mode)
    selection_kind = "ranked"
    score_tier = "top"
    finalist_count = len(ranked)
    if request.algorithm_version == LEGACY_ALGORITHM_VERSION:
        if request.mode == "reliable" and request.last_known_good is not None:
            for index, item in enumerate(ranked):
                path = (item.candidate.connection_id, item.candidate.model_id)
                if path == request.last_known_good:
                    ranked.insert(0, ranked.pop(index))
                    reason_codes.append("last_known_good")
                    selection_kind = "lkgp"
                    break
        selected_item = ranked[0]
    else:
        selection = select_ranked_candidate(
            ranked,
            mode=request.mode,
            rotator_key=request.rotator_key,
            incident_mode=request.incident_mode,
            last_known_good=request.last_known_good,
        )
        selected_item = selection.selected
        ranked = list(selection.ordered)
        selection_kind = selection.selection_kind
        score_tier = selection.score_tier
        finalist_count = selection.finalist_count
        reason_codes.append(f"selection_{selection_kind}")
        reason_codes.append(f"score_tier_{score_tier}")
    selected = selected_item.candidate
    reason_codes.append(f"mode_{request.mode}")
    if selected.breaker_state == "half_open":
        reason_codes.append("half_open_probe")
    return RouterDecision(
        selected=selected,
        ranked=tuple(ranked),
        mode=request.mode,
        reason_codes=tuple(reason_codes),
        filtered_counts=filtered_counts,
        algorithm_version=request.algorithm_version,
        task_type=request.task_type,
        selection_kind=selection_kind,
        score_tier=score_tier,
        eligible_count=len(eligible),
        finalist_count=finalist_count,
    )
