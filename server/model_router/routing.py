from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

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
    "reliable": DEFAULT_WEIGHTS,
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
        "context_affinity": 0.00,
        "cache_affinity": 0.00,
        "reset_window_affinity": 0.00,
        "connection_density": 0.06,
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


@dataclass(frozen=True)
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
    p95_latency_ms: float = 1000.0
    latency_stddev_ms: float = 0.0
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


@dataclass(frozen=True)
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


@dataclass(frozen=True)
class RankedCandidate:
    candidate: RoutingCandidate
    score: float
    factors: dict[str, float]


@dataclass(frozen=True)
class RouterDecision:
    selected: RoutingCandidate
    ranked: tuple[RankedCandidate, ...]
    mode: RoutingMode
    reason_codes: tuple[str, ...]
    filtered_counts: dict[str, int] = field(default_factory=dict)


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
) -> dict[str, float]:
    known_costs = [
        value
        for value in (item.cost_per_million_tokens for item in pool)
        if value is not None and value >= 0
    ]
    max_cost = max(known_costs, default=0.001)
    max_latency = max((item.p95_latency_ms for item in pool), default=1.0)
    max_stddev = max((item.latency_stddev_ms for item in pool), default=0.001)
    cost = candidate.cost_per_million_tokens
    cost_inverse = 0.0 if cost is None else 1 - cost / max(max_cost, 0.001)
    health = (
        1.0
        if candidate.breaker_state == "closed"
        else 0.5
        if candidate.breaker_state == "half_open"
        else 0.0
    )
    return {
        "quota": clamp01(candidate.quota_remaining / 100),
        "health": health,
        "cost_inverse": clamp01(cost_inverse),
        "latency_inverse": clamp01(
            1 - candidate.p95_latency_ms / max(max_latency, 1)
        ),
        "task_fit": clamp01(candidate.task_fit),
        "stability": clamp01(
            (1 - candidate.latency_stddev_ms / max(max_stddev, 0.001))
            * (1 - candidate.error_rate)
        ),
        "tier_priority": clamp01(candidate.tier_priority),
        "tier_affinity": clamp01(candidate.tier_affinity),
        "specificity_match": clamp01(candidate.specificity_match),
        "context_affinity": clamp01(candidate.context_affinity),
        "cache_affinity": clamp01(candidate.cache_affinity),
        "reset_window_affinity": clamp01(candidate.reset_window_affinity),
        "connection_density": clamp01(
            (max(1, candidate.connection_pool_size) - 1) / 10
        ),
    }


def rank_candidates(
    candidates: list[RoutingCandidate],
    mode: RoutingMode,
) -> list[RankedCandidate]:
    weights = MODE_WEIGHTS[mode]
    ranked = []
    for candidate in candidates:
        factors = calculate_factors(candidate, candidates)
        score = clamp01(
            sum(weights[name] * factors[name] for name in FACTOR_NAMES)
        )
        ranked.append(
            RankedCandidate(candidate=candidate, score=score, factors=factors)
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

    eligible, preference_fallback = _apply_soft_preferences(eligible, request)
    eligible, budget_fallback = _apply_soft_budget(eligible, request)
    reason_codes: list[str] = []
    if preference_fallback:
        reason_codes.append("preference_pool_fallback")
    if budget_fallback:
        reason_codes.append("soft_budget_cheapest_fallback")

    ranked = rank_candidates(eligible, request.mode)
    if request.mode == "reliable" and request.last_known_good is not None:
        for index, item in enumerate(ranked):
            path = (item.candidate.connection_id, item.candidate.model_id)
            if path == request.last_known_good:
                ranked.insert(0, ranked.pop(index))
                reason_codes.append("last_known_good")
                break

    selected = ranked[0].candidate
    reason_codes.append(f"mode_{request.mode}")
    if selected.breaker_state == "half_open":
        reason_codes.append("half_open_probe")
    return RouterDecision(
        selected=selected,
        ranked=tuple(ranked),
        mode=request.mode,
        reason_codes=tuple(reason_codes),
        filtered_counts=filtered_counts,
    )
