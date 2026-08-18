"""OmniRoute Auto-Combo scoring and selection adapted for ModelMirror.

Upstream:
  diegosouzapw/OmniRoute release/v3.8.49
  commit 36f8fd10052fd88f07e188b566f19a59c9cf5ea7
  open-sse/services/autoCombo/{engine,scoring,modePacks,speedRanking,
  taskFitness,selfHealing}.ts

Copyright (c) 2026 diegosouzapw. Licensed under the MIT License.
Modified for ModelMirror: translated to Python, operates on tenant-scoped
provider/model candidates, and constrains exploration to a competitive frontier.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import threading
from dataclasses import dataclass
from typing import Callable, Literal, Protocol, TypeVar


ALGORITHM_VERSION = "omniroute-parity-v2"
LEGACY_ALGORITHM_VERSION = "legacy-v1"
MAX_FRONTIER_CANDIDATES = 24
COMPETITIVE_SCORE_BAND = 0.10
MIN_FRONTIER_CANDIDATES = 3
SCORE_EPSILON = 1e-4
CLEAR_WINNER_THRESHOLD = 0.10

TierName = Literal["top", "mid", "rest"]

TIER_PREFERENCES: dict[str, dict[TierName, float]] = {
    "quality": {"top": 0.50, "mid": 0.30, "rest": 0.20},
    "fast": {"top": 0.30, "mid": 0.50, "rest": 0.20},
    "cheap": {"top": 0.20, "mid": 0.30, "rest": 0.50},
    "coding": {"top": 0.60, "mid": 0.25, "rest": 0.15},
    "default": {"top": 0.45, "mid": 0.35, "rest": 0.20},
}
EXPLORATION_RATES = {
    "auto": 0.05,
    "fast": 0.05,
    "quality": 0.10,
    "cheap": 0.05,
    "reliable": 0.0,
    "offline": 0.05,
}

_CONFIG_PAYLOAD = {
    "algorithm": ALGORITHM_VERSION,
    "max_frontier": MAX_FRONTIER_CANDIDATES,
    "score_band": COMPETITIVE_SCORE_BAND,
    "min_frontier": MIN_FRONTIER_CANDIDATES,
    "clear_winner": CLEAR_WINNER_THRESHOLD,
    "tier_preferences": TIER_PREFERENCES,
    "exploration_rates": EXPLORATION_RATES,
}
CONFIG_HASH = hashlib.sha256(
    json.dumps(_CONFIG_PAYLOAD, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()


class CandidateLike(Protocol):
    connection_id: str
    model_id: str
    capabilities: frozenset[str]
    context_length: int | None


class RankedLike(Protocol):
    candidate: CandidateLike
    score: float


RankedT = TypeVar("RankedT", bound=RankedLike)


@dataclass(frozen=True)
class NativeSelection:
    selected: RankedLike
    ordered: tuple[RankedLike, ...]
    selection_kind: str
    score_tier: str
    eligible_count: int
    finalist_count: int


@dataclass(frozen=True)
class SpeedFactors:
    ttft: float
    tps: float
    e2e: float
    p95: float
    health: float
    reliability: float
    stability: float

    @property
    def score(self) -> float:
        weighted = (
            self.ttft * 0.25
            + self.tps * 0.20
            + self.e2e * 0.18
            + self.p95 * 0.12
            + self.health * 0.05
            + self.reliability * 0.15
            + self.stability * 0.05
        )
        reliability_multiplier = max(
            0.05, math.pow(0.25 + 0.75 * self.reliability, 2)
        )
        stability_multiplier = max(
            0.05, math.pow(0.25 + 0.75 * self.stability, 2)
        )
        return _clamp01(
            weighted
            * reliability_multiplier
            * stability_multiplier
            * max(0.25, self.health)
        )


FITNESS_TABLE: dict[str, tuple[tuple[str, float], ...]] = {
    "coding": (
        ("claude-sonnet", 0.95), ("claude-opus", 0.92), ("claude-haiku", 0.78),
        ("gpt-4o", 0.90), ("gpt-4o-mini", 0.80), ("gpt-4-turbo", 0.88),
        ("o1", 0.93), ("o3", 0.95), ("o4-mini", 0.88), ("codex", 0.98),
        ("gemini-pro", 0.85), ("gemini-flash", 0.80), ("gemini-2.5-pro", 0.92),
        ("gemini-2.5-flash", 0.82), ("deepseek-coder", 0.90),
        ("deepseek-v3", 0.85), ("deepseek-r1", 0.88), ("deepseek-chat", 0.84),
        ("deepseek-v3.2", 0.86), ("qwen", 0.78), ("llama", 0.72),
        ("mistral", 0.75), ("mixtral", 0.77), ("grok-4-fast", 0.80),
        ("grok-4", 0.82), ("grok-3", 0.80), ("kimi-k2", 0.82),
        ("glm-5.1", 0.78), ("glm-5", 0.78), ("minimax-m2.5", 0.75),
        ("minimax-m2", 0.72),
    ),
    "review": (
        ("claude-sonnet", 0.92), ("claude-opus", 0.95), ("claude-haiku", 0.70),
        ("gpt-4o", 0.88), ("gpt-4o-mini", 0.72), ("o1", 0.90), ("o3", 0.92),
        ("gemini-pro", 0.90), ("gemini-2.5-pro", 0.93), ("gemini-flash", 0.75),
        ("deepseek-r1", 0.85), ("deepseek-v3", 0.80),
    ),
    "planning": (
        ("claude-opus", 0.95), ("claude-sonnet", 0.90), ("gpt-4o", 0.88),
        ("o1", 0.92), ("o3", 0.95), ("gemini-2.5-pro", 0.93),
        ("gemini-pro", 0.88), ("deepseek-r1", 0.85),
    ),
    "analysis": (
        ("claude-opus", 0.95), ("claude-sonnet", 0.92), ("gemini-2.5-pro", 0.95),
        ("gemini-pro", 0.88), ("gemini-3.1-pro", 0.95), ("gpt-4o", 0.85),
        ("o1", 0.90), ("o3", 0.93), ("deepseek-r1", 0.88),
        ("deepseek-chat", 0.80), ("kimi-k2", 0.82), ("glm-5.1", 0.82),
        ("glm-5", 0.78), ("minimax-m2.5", 0.76),
    ),
    "debugging": (
        ("claude-sonnet", 0.93), ("claude-opus", 0.90), ("gpt-4o", 0.88),
        ("o1", 0.85), ("deepseek-coder", 0.90), ("deepseek-v3", 0.82),
        ("gemini-flash", 0.78), ("codex", 0.92),
    ),
    "documentation": (
        ("claude-sonnet", 0.90), ("claude-opus", 0.88), ("gpt-4o", 0.92),
        ("gpt-4o-mini", 0.85), ("gemini-pro", 0.88), ("gemini-flash", 0.82),
        ("deepseek-v3", 0.78),
    ),
    "default": (
        ("claude-sonnet", 0.85), ("claude-opus", 0.85), ("gpt-4o", 0.85),
        ("gemini-pro", 0.80), ("gemini-3.1-pro", 0.85), ("deepseek-v3", 0.75),
        ("deepseek-chat", 0.74), ("gemini-flash", 0.72), ("grok-4-fast", 0.72),
        ("grok-4", 0.74), ("grok-3", 0.73), ("kimi-k2", 0.76),
        ("glm-5.1", 0.75), ("glm-5", 0.70), ("minimax-m2.5", 0.70),
    ),
}

TIER_TASK_FITNESS = {
    "premium": {"coding": 0.92, "review": 0.93, "planning": 0.94, "analysis": 0.95,
                "debugging": 0.90, "documentation": 0.88, "default": 0.85},
    "standard": {"coding": 0.85, "review": 0.84, "planning": 0.85, "analysis": 0.85,
                 "debugging": 0.82, "documentation": 0.85, "default": 0.78},
    "fast": {"coding": 0.78, "review": 0.72, "planning": 0.70, "analysis": 0.72,
             "debugging": 0.75, "documentation": 0.80, "default": 0.72},
    "budget": {"coding": 0.65, "review": 0.60, "planning": 0.55, "analysis": 0.58,
               "debugging": 0.60, "documentation": 0.70, "default": 0.55},
}


def _clamp01(value: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return max(0.0, min(1.0, float(value)))


def _lower_is_better(value: float | None, maximum: float) -> float:
    if value is None:
        return 0.5
    if not math.isfinite(value) or value < 0:
        return 0.0
    return _clamp01(1 - value / max(maximum, 1e-6))


def _higher_is_better(value: float | None, maximum: float) -> float:
    if value is None:
        return 0.5
    if not math.isfinite(value) or value < 0:
        return 0.0
    return _clamp01(value / max(maximum, 1e-6))


def speed_factors(
    *,
    ttft_ms: float | None,
    tps: float | None,
    e2e_ms: float | None,
    p95_ms: float | None,
    stddev_ms: float | None,
    failure_rate: float,
    breaker_state: str,
    maxima: dict[str, float],
) -> SpeedFactors:
    state = str(breaker_state).lower()
    health = 1.0 if state == "closed" else 0.5 if state == "half_open" else 0.0
    return SpeedFactors(
        ttft=_lower_is_better(ttft_ms, maxima.get("ttft", 1.0)),
        tps=_higher_is_better(tps, maxima.get("tps", 1.0)),
        e2e=_lower_is_better(e2e_ms, maxima.get("e2e", 1.0)),
        p95=_lower_is_better(p95_ms, maxima.get("p95", 1.0)),
        health=health,
        reliability=_clamp01(1 - max(0.0, failure_rate)),
        stability=_lower_is_better(stddev_ms, maxima.get("stddev", 0.001)),
    )


def speed_score(
    *,
    ttft_ms: float | None,
    tps: float | None,
    e2e_ms: float | None,
    p95_ms: float | None,
    stddev_ms: float | None,
    failure_rate: float,
    breaker_state: str,
    maxima: dict[str, float],
) -> float:
    """Allocation-light equivalent of ``speed_factors(...).score``."""

    state = str(breaker_state).lower()
    health = 1.0 if state == "closed" else 0.5 if state == "half_open" else 0.0
    reliability = _clamp01(1 - max(0.0, failure_rate))
    stability = _lower_is_better(stddev_ms, maxima.get("stddev", 0.001))
    weighted = (
        _lower_is_better(ttft_ms, maxima.get("ttft", 1.0)) * 0.25
        + _higher_is_better(tps, maxima.get("tps", 1.0)) * 0.20
        + _lower_is_better(e2e_ms, maxima.get("e2e", 1.0)) * 0.18
        + _lower_is_better(p95_ms, maxima.get("p95", 1.0)) * 0.12
        + health * 0.05
        + reliability * 0.15
        + stability * 0.05
    )
    reliability_base = 0.25 + 0.75 * reliability
    stability_base = 0.25 + 0.75 * stability
    return _clamp01(
        weighted
        * max(0.05, reliability_base * reliability_base)
        * max(0.05, stability_base * stability_base)
        * max(0.25, health)
    )


def _task_key(task_type: str) -> str:
    return {
        "code": "coding",
        "math": "analysis",
        "reasoning": "analysis",
        "creative": "documentation",
        "simple": "default",
        "medium": "default",
        "general": "default",
    }.get(str(task_type or "").lower(), str(task_type or "default").lower())


def get_task_fitness(
    model_id: str,
    task_type: str,
    *,
    capabilities: frozenset[str] = frozenset(),
    context_length: int | None = None,
) -> float:
    """Resolve capability tier first, then the pinned static/wildcard tables."""

    task = _task_key(task_type)
    if capabilities:
        if "reasoning" in capabilities:
            tier = "premium"
        elif "tools" in capabilities and (context_length or 0) >= 128_000:
            tier = "standard"
        elif "tools" in capabilities:
            tier = "fast"
        else:
            tier = "budget"
        scores = TIER_TASK_FITNESS[tier]
        return float(scores.get(task, scores["default"]))

    normalized = str(model_id or "").lower()
    table = FITNESS_TABLE.get(task, FITNESS_TABLE["default"])
    for pattern, score in table:
        if pattern in normalized:
            return score
    score = 0.5
    for pattern, wildcard_task, boost in (
        ("coder", "coding", 0.15),
        ("code", "coding", 0.10),
        ("fast", "coding", 0.05),
        ("thinking", "planning", 0.10),
        ("thinking", "analysis", 0.10),
    ):
        if pattern in normalized and task == wildcard_task:
            score += boost
    return min(1.0, score)


def _publisher(model_id: str) -> str:
    normalized = str(model_id or "").strip().lower()
    return normalized.split("/", 1)[0] if "/" in normalized else normalized


def build_competitive_frontier(
    ranked: list[RankedT],
    *,
    last_known_good: tuple[str, str] | None = None,
) -> list[RankedT]:
    """Collapse a flat catalog into provider representatives and a score band."""

    if not ranked:
        return []
    representatives: list[RankedT] = []
    seen_groups: set[tuple[str, str]] = set()
    lkg_item: RankedT | None = None
    for item in ranked:
        candidate = item.candidate
        if last_known_good == (candidate.connection_id, candidate.model_id):
            lkg_item = item
        key = (candidate.connection_id, _publisher(candidate.model_id))
        if key not in seen_groups:
            seen_groups.add(key)
            representatives.append(item)
    if lkg_item is not None and lkg_item not in representatives:
        representatives.append(lkg_item)
    representatives.sort(key=lambda item: (-item.score, item.candidate.connection_id, item.candidate.model_id))
    representatives = representatives[:MAX_FRONTIER_CANDIDATES]
    best = representatives[0].score
    frontier = [item for item in representatives if best - item.score <= COMPETITIVE_SCORE_BAND]
    if len(frontier) < min(MIN_FRONTIER_CANDIDATES, len(representatives)):
        frontier = representatives[: min(MIN_FRONTIER_CANDIDATES, len(representatives))]
    if lkg_item is not None and lkg_item in representatives and lkg_item not in frontier:
        frontier.append(lkg_item)
    return sorted(
        frontier,
        key=lambda item: (-item.score, item.candidate.connection_id, item.candidate.model_id),
    )


def _tier_preferences(mode: str) -> dict[TierName, float]:
    return TIER_PREFERENCES.get(mode, TIER_PREFERENCES["default"])


def _group_into_tiers(candidates: list[RankedT]) -> dict[TierName, list[RankedT]]:
    if not candidates:
        return {"top": [], "mid": [], "rest": []}
    best = candidates[0].score
    worst = candidates[-1].score
    score_range = best - worst
    tiers: dict[TierName, list[RankedT]] = {"top": [], "mid": [], "rest": []}
    for candidate in candidates:
        delta = best - candidate.score
        if delta <= SCORE_EPSILON:
            tiers["top"].append(candidate)
        elif score_range <= SCORE_EPSILON or delta <= score_range * 0.3:
            tiers["mid"].append(candidate)
        else:
            tiers["rest"].append(candidate)
    if not tiers["mid"] and tiers["rest"]:
        halfway = math.ceil(len(tiers["rest"]) / 2)
        tiers["mid"] = tiers["rest"][:halfway]
        tiers["rest"] = tiers["rest"][halfway:]
    return tiers


class _ScoreTierRotator:
    def __init__(self) -> None:
        self._counter = 0
        self._lock = threading.Lock()

    def pick(self, pool: list[RankedT]) -> RankedT:
        if not pool:
            raise ValueError("cannot select from an empty score tier")
        with self._lock:
            picked = pool[self._counter % len(pool)]
            self._counter = (self._counter + 1) % max(1, len(pool))
            return picked


class _RotatorRegistry:
    def __init__(self) -> None:
        self._items: dict[str, _ScoreTierRotator] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> _ScoreTierRotator:
        with self._lock:
            if key not in self._items:
                if len(self._items) >= 1_000:
                    self._items.pop(next(iter(self._items)))
                self._items[key] = _ScoreTierRotator()
            return self._items[key]


_ROTATORS = _RotatorRegistry()


def select_ranked_candidate(
    ranked: list[RankedT],
    *,
    mode: str,
    rotator_key: str,
    incident_mode: bool = False,
    last_known_good: tuple[str, str] | None = None,
    rng: Callable[[], float] = random.random,
) -> NativeSelection:
    if not ranked:
        raise ValueError("cannot select from an empty ranked candidate list")
    eligible_count = len(ranked)
    if mode == "reliable" and last_known_good is not None:
        selected = next(
            (
                item
                for item in ranked
                if (item.candidate.connection_id, item.candidate.model_id)
                == last_known_good
            ),
            None,
        )
        if selected is not None:
            ordered = (selected, *(item for item in ranked if item is not selected))
            return NativeSelection(selected, ordered, "lkgp", "top", eligible_count, len(ranked))
    if mode == "reliable":
        selected = ranked[0]
        return NativeSelection(selected, tuple(ranked), "ranked", "top", eligible_count, len(ranked))

    frontier = build_competitive_frontier(ranked, last_known_good=last_known_good)
    exploration_rate = 0.0 if incident_mode else EXPLORATION_RATES.get(mode, 0.05)
    if len(frontier) > 1 and rng() < exploration_rate:
        index = min(len(frontier) - 1, int(rng() * len(frontier)))
        selected = frontier[index]
        kind = "exploration"
        tier = "frontier"
    else:
        tiers = _group_into_tiers(frontier)
        if frontier[0].score - frontier[-1].score >= CLEAR_WINNER_THRESHOLD:
            selected = _ROTATORS.get(f"{rotator_key}:top").pick(tiers["top"])
            kind = "clear_winner"
            tier = "top"
        else:
            preferences = _tier_preferences(mode)
            active = {
                name: preferences[name] if tiers[name] else 0.0
                for name in ("top", "mid", "rest")
            }
            total = sum(active.values())
            draw = rng() * total if total > 0 else 0.0
            running = 0.0
            tier = "top"
            for name in ("top", "mid", "rest"):
                running += active[name]
                if active[name] and draw <= running:
                    tier = name
                    break
            selected = _ROTATORS.get(f"{rotator_key}:{tier}").pick(tiers[tier])
            kind = "tier_rotation"
    ordered = (selected, *(item for item in ranked if item is not selected))
    return NativeSelection(
        selected=selected,
        ordered=ordered,
        selection_kind=kind,
        score_tier=tier,
        eligible_count=eligible_count,
        finalist_count=len(frontier),
    )
