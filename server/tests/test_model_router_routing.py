from __future__ import annotations

import pytest

from server.model_router.engine import NativeRouterEngine, infer_task_tags
from server.model_router.omniroute_parity import LEGACY_ALGORITHM_VERSION
from server.model_router.routing import (
    DEFAULT_WEIGHTS,
    MODE_WEIGHTS,
    NoEligibleCandidateError,
    RoutingCandidate,
    RoutingRequest,
    decide_route,
    validate_weights,
)


def candidate(
    name: str,
    *,
    cost: float,
    latency: float,
    task_fit: float,
    quota: float = 100,
    error_rate: float = 0,
    breaker: str = "closed",
    modalities: frozenset[str] = frozenset({"text"}),
    capabilities: frozenset[str] = frozenset(),
    context_length: int = 128_000,
    request_cost: float | None = None,
    latency_stddev: float | None = None,
) -> RoutingCandidate:
    return RoutingCandidate(
        tenant_id="local",
        connection_id=f"conn-{name}",
        connection_name=name,
        model_id=f"provider/{name}",
        cost_per_million_tokens=cost,
        estimated_request_cost=request_cost,
        p95_latency_ms=latency,
        latency_stddev_ms=(
            latency * 0.1 if latency_stddev is None else latency_stddev
        ),
        error_rate=error_rate,
        quota_remaining=quota,
        task_fit=task_fit,
        breaker_state=breaker,  # type: ignore[arg-type]
        input_modalities=modalities,
        capabilities=capabilities,
        context_length=context_length,
    )


def test_pinned_weights_are_normalized() -> None:
    assert DEFAULT_WEIGHTS["health"] == 0.20
    assert DEFAULT_WEIGHTS["cost_inverse"] == 0.15
    assert DEFAULT_WEIGHTS["latency_inverse"] == 0.12
    assert all(validate_weights(weights) for weights in MODE_WEIGHTS.values())


def test_task_hints_only_boost_matching_model_categories() -> None:
    assert infer_task_tags("请修复这段 Python 代码的 traceback") == {"coding"}
    assert infer_task_tags("请严谨证明这个结论并逐步推理") == {"reasoning"}
    assert infer_task_tags("请概括这段普通说明") == set()

    generic_code_fit = NativeRouterEngine._task_fit(
        "openrouter/pareto-code", "medium", "standard", set(), 128_000
    )
    coding_fit = NativeRouterEngine._task_fit(
        "openrouter/pareto-code", "code", "standard", set(), 128_000
    )
    general_quality_fit = NativeRouterEngine._task_fit(
        "openai/gpt-5.6-sol", "medium", "critical", set(), 128_000
    )
    assert coding_fit > generic_code_fit
    assert general_quality_fit > generic_code_fit


def test_six_modes_produce_result_oriented_choices() -> None:
    fast = candidate("fast", cost=8, latency=80, task_fit=0.6, quota=60)
    quality = candidate(
        "quality",
        cost=25,
        latency=900,
        latency_stddev=10,
        task_fit=1.0,
        quota=80,
    )
    cheap = candidate("cheap", cost=0.2, latency=650, task_fit=0.55, quota=70)
    offline = candidate("offline", cost=2, latency=700, task_fit=0.4, quota=100)
    pool = [quality, cheap, offline, fast]

    request = lambda mode: RoutingRequest(
        "local", mode=mode, algorithm_version=LEGACY_ALGORITHM_VERSION
    )
    assert decide_route(pool, request("fast")).selected == fast
    assert (
        decide_route(pool, request("quality")).selected
        == quality
    )
    assert decide_route(pool, request("cheap")).selected == cheap
    assert (
        decide_route(pool, request("offline")).selected
        == offline
    )
    assert decide_route(pool, request("auto")).ranked


def test_reliable_mode_prefers_healthy_lkgp_and_exits_open_path() -> None:
    sticky = candidate("sticky", cost=4, latency=500, task_fit=0.6)
    top = candidate("top", cost=3, latency=100, task_fit=0.9)
    request = RoutingRequest(
        "local",
        mode="reliable",
        last_known_good=(sticky.connection_id, sticky.model_id),
        algorithm_version=LEGACY_ALGORITHM_VERSION,
    )
    decision = decide_route([top, sticky], request)
    assert decision.selected == sticky
    assert "last_known_good" in decision.reason_codes

    opened = candidate(
        "sticky", cost=4, latency=500, task_fit=0.6, breaker="open"
    )
    decision = decide_route([top, opened], request)
    assert decision.selected == top
    assert decision.filtered_counts["breaker_open"] == 1


def test_hard_constraints_never_fail_open() -> None:
    text_only = candidate(
        "text",
        cost=1,
        latency=100,
        task_fit=1,
        capabilities=frozenset(),
        context_length=4096,
    )
    request = RoutingRequest(
        "local",
        required_input_modalities=frozenset({"text", "image"}),
        required_capabilities=frozenset({"tools"}),
        estimated_input_tokens=5000,
    )
    with pytest.raises(NoEligibleCandidateError) as exc:
        decide_route([text_only], request)
    assert exc.value.code == "no_eligible_candidate"


def test_preference_can_fail_open_but_strict_budget_cannot() -> None:
    known = candidate(
        "known",
        cost=1,
        latency=100,
        task_fit=0.8,
        request_cost=0.05,
    )
    preference = RoutingRequest(
        "local",
        preferred_tags=frozenset({"vision"}),
        algorithm_version=LEGACY_ALGORITHM_VERSION,
    )
    decision = decide_route([known], preference)
    assert decision.selected == known
    assert "preference_pool_fallback" in decision.reason_codes

    strict = RoutingRequest(
        "local",
        budget_usd=0.01,
        budget_fallback="strict",
    )
    with pytest.raises(NoEligibleCandidateError) as exc:
        decide_route([known], strict)
    assert exc.value.code == "strict_budget_exceeded"

    unknown_price = candidate(
        "unknown",
        cost=1,
        latency=100,
        task_fit=0.8,
        request_cost=None,
    )
    with pytest.raises(NoEligibleCandidateError):
        decide_route([unknown_price], strict)
