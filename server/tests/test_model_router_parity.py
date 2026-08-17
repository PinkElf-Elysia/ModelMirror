from __future__ import annotations

import time
import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from server.model_router.gate import REQUIRED_DRILLS, evaluate_native_gate
from server.model_router.engine import NativeRouterEngine
from server.model_router.schemas import RouterConnection
from server.model_router.omniroute_parity import (
    ALGORITHM_VERSION,
    CONFIG_HASH,
    build_competitive_frontier,
    classify_prompt_intent,
    classify_task,
    select_ranked_candidate,
    speed_factors,
)
from server.model_router.routing import (
    RankedCandidate,
    RoutingCandidate,
    RoutingRequest,
    decide_route,
)


def _candidate(
    index: int,
    *,
    publisher: str | None = None,
    connection: str | None = None,
) -> RoutingCandidate:
    return RoutingCandidate(
        tenant_id="local",
        connection_id=connection or f"connection-{index % 4}",
        connection_name=f"Connection {index % 4}",
        model_id=f"{publisher or f'publisher-{index}'}/model-{index}",
        context_length=128_000,
        cost_per_million_tokens=1 + index / 100,
        estimated_request_cost=0.001 + index / 1_000_000,
        p95_latency_ms=200 + index % 25,
        avg_ttft_ms=40 + index % 10,
        avg_e2e_latency_ms=400 + index % 30,
        avg_tokens_per_second=50 + index % 15,
        latency_stddev_ms=10 + index % 5,
        task_fit=0.7 + (index % 5) / 100,
        tier_priority=0.7,
    )


def _ranked(index: int, score: float, *, publisher: str | None = None) -> RankedCandidate:
    return RankedCandidate(_candidate(index, publisher=publisher), score, {})


def test_pinned_multilingual_intent_and_task_difficulty() -> None:
    assert classify_prompt_intent("请实现一个 Python API") == "code"
    assert classify_prompt_intent("求解这个积分") == "math"
    assert classify_prompt_intent("写一篇短篇故事") == "creative"
    assert classify_prompt_intent("请搜索今天最新新闻") == "medium"
    assert classify_task(
        "hello",
        message_count=1,
        tool_count=0,
        output_tokens=200,
    ).level == "light"
    assert classify_task(
        "analyze architecture " * 400,
        message_count=20,
        tool_count=4,
        output_tokens=10_000,
    ).level == "heavy"


def test_unknown_speed_metrics_are_neutral() -> None:
    factors = speed_factors(
        ttft_ms=None,
        tps=None,
        e2e_ms=None,
        p95_ms=None,
        stddev_ms=None,
        failure_rate=0,
        breaker_state="closed",
        maxima={"ttft": 1, "tps": 1, "e2e": 1, "p95": 1, "stddev": 1},
    )
    assert factors.ttft == factors.tps == factors.e2e == 0.5
    assert factors.p95 == factors.stability == 0.5


def test_frontier_collapses_provider_groups_and_caps_at_24() -> None:
    ranked = [
        _ranked(index, 1 - index / 1000, publisher=f"publisher-{index // 2}")
        for index in range(80)
    ]
    frontier = build_competitive_frontier(ranked)
    groups = {
        (item.candidate.connection_id, item.candidate.model_id.split("/", 1)[0])
        for item in frontier
    }
    assert len(frontier) <= 24
    assert len(groups) == len(frontier)


def test_fixed_random_sequence_covers_exploration_clear_winner_and_lkgp() -> None:
    close = [_ranked(0, 0.90), _ranked(1, 0.89), _ranked(2, 0.88)]
    draws = iter((0.0, 0.99))
    explored = select_ranked_candidate(
        close,
        mode="quality",
        rotator_key="test:quality:code",
        rng=lambda: next(draws),
    )
    assert explored.selection_kind == "exploration"
    assert explored.selected == close[-1]

    winner = [_ranked(10, 0.95), _ranked(11, 0.70), _ranked(12, 0.68)]
    selected = select_ranked_candidate(
        winner,
        mode="auto",
        rotator_key="test:auto:medium",
        rng=lambda: 0.99,
    )
    assert selected.selection_kind == "clear_winner"
    assert selected.selected == winner[0]

    sticky = close[1]
    reliable = select_ranked_candidate(
        close,
        mode="reliable",
        rotator_key="test:reliable:medium",
        last_known_good=(
            sticky.candidate.connection_id,
            sticky.candidate.model_id,
        ),
    )
    assert reliable.selection_kind == "lkgp"
    assert reliable.selected == sticky


def test_incident_mode_disables_exploration() -> None:
    ranked = [_ranked(20, 0.90), _ranked(21, 0.89), _ranked(22, 0.88)]
    selected = select_ranked_candidate(
        ranked,
        mode="quality",
        rotator_key="test:incident:medium",
        incident_mode=True,
        rng=lambda: 0.0,
    )
    assert selected.selection_kind != "exploration"


def test_gate_requires_current_version_sidecar_baseline_and_bound_approval() -> None:
    now = datetime.now(UTC)
    native = []
    for index in range(500):
        created_at = now - timedelta(days=14) + timedelta(
            seconds=index * (14 * 86_400 / 499)
        )
        native.append(
            {
                "success": True,
                "outcome": "success",
                "ttft_ms": 100,
                "e2e_ms": 500,
                "output_tokens": 100,
                "planning_latency_ms": 2,
                "created_at": created_at.isoformat(),
            }
        )
    sidecar = [
        {
            "success": True,
            "outcome": "success",
            "ttft_ms": 100,
            "e2e_ms": 500,
            "output_tokens": 100,
            "planning_latency_ms": None,
            "created_at": now.isoformat(),
        }
        for _ in range(100)
    ]
    approval = {
        "algorithm_version": ALGORITHM_VERSION,
        "config_hash": CONFIG_HASH,
        "no_open_p0_p1": True,
        "drills": {name: True for name in REQUIRED_DRILLS},
    }
    gate = evaluate_native_gate(
        native,
        sidecar,
        algorithm_version=ALGORITHM_VERSION,
        config_hash=CONFIG_HASH,
        approval=approval,
        now=now,
    )
    assert gate["automatic_native_default_allowed"] is True
    assert gate["native_default_allowed"] is True

    stale_approval = {**approval, "config_hash": "previous-config"}
    invalid = evaluate_native_gate(
        native,
        sidecar,
        algorithm_version=ALGORITHM_VERSION,
        config_hash=CONFIG_HASH,
        approval=stale_approval,
        now=now,
    )
    assert invalid["automatic_native_default_allowed"] is True
    assert invalid["native_default_allowed"] is False


def test_650_candidate_hot_planner_p95_is_under_10ms() -> None:
    candidates = [_candidate(index) for index in range(650)]
    request = RoutingRequest(
        "local",
        mode="auto",
        task_type="medium",
        rotator_key="benchmark:auto:medium",
        incident_mode=True,
    )
    durations = []
    for _ in range(1_000):
        started = time.perf_counter()
        decision = decide_route(candidates, request)
        durations.append((time.perf_counter() - started) * 1000)
        assert decision.finalist_count <= 24
    durations.sort()
    p95 = durations[int(len(durations) * 0.95) - 1]
    assert p95 < 10.0


@pytest.mark.asyncio
async def test_stale_catalog_returns_without_waiting_for_background_refresh() -> None:
    class Service:
        tenant_id = "local"
        repository = object()

        def __init__(self) -> None:
            self.calls = 0

        async def fetch_connection_model_records(self, _connection_id: str):
            self.calls += 1
            if self.calls > 1:
                await asyncio.sleep(0.1)
            return SimpleNamespace(ok=True), [{"id": f"provider/model-{self.calls}"}]

    service = Service()
    engine = NativeRouterEngine(
        service,  # type: ignore[arg-type]
        catalog_ttl_seconds=30,
        catalog_stale_seconds=600,
    )
    connection = RouterConnection(
        id="connection-a",
        tenant_id="local",
        name="Local",
        kind="openai_compatible",
        base_url="https://example.test/v1",
        masked_key="ab**cd",
        scopes=["chat"],
        enabled=True,
        health="online",
        created_at="2026-08-17T00:00:00+00:00",
        updated_at="2026-08-17T00:00:00+00:00",
    )
    first = await engine._records_for_connection(connection)
    assert first[0]["id"] == "provider/model-1"
    engine._catalog_cache[connection.id].stored_at -= 31

    started = time.perf_counter()
    stale = await engine._records_for_connection(connection)
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert stale[0]["id"] == "provider/model-1"
    assert elapsed_ms < 20
    await asyncio.gather(*engine._refresh_tasks.values())
    assert engine._catalog_cache[connection.id].records[0]["id"] == "provider/model-2"
