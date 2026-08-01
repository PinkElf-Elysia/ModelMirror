from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from .repository import SQLiteRouterRepository
from .routing import (
    NoEligibleCandidateError,
    RouterDecision,
    RoutingCandidate,
    RoutingRequest,
    decide_route,
)
from .schemas import RouterConnection, RoutingMode
from .service import ModelRouterService


def infer_task_tags(text: str) -> set[str]:
    """Infer only high-confidence task hints without another model request."""

    normalized = str(text or "")[-12_000:].lower()
    coding_hints = (
        "```",
        "traceback",
        "stack trace",
        "syntaxerror",
        "exception",
        "debug",
        "refactor",
        "代码",
        "编程",
        "函数",
        "报错",
        "单元测试",
        "正则",
        "sql ",
        "python ",
        "javascript ",
        "typescript ",
        "java ",
        "golang ",
        "rust ",
    )
    reasoning_hints = (
        "逐步推理",
        "严谨证明",
        "数学证明",
        "逻辑推导",
        "多步分析",
        "step-by-step reasoning",
        "prove that",
        "formal proof",
    )
    tags: set[str] = set()
    if any(hint in normalized for hint in coding_hints):
        tags.add("coding")
    if any(hint in normalized for hint in reasoning_hints):
        tags.add("reasoning")
    return tags


@dataclass(frozen=True)
class NativeDispatchTarget:
    connection_id: str
    connection_name: str
    model_id: str
    chat_completions_url: str
    api_key: str
    context_length: int | None
    input_price_per_token: float | None
    output_price_per_token: float | None
    estimated_request_cost: float | None
    score: float


@dataclass(frozen=True)
class NativeRoutePlan:
    decision_id: str
    session_id_hash: str | None
    mode: RoutingMode
    reason_codes: tuple[str, ...]
    targets: tuple[NativeDispatchTarget, ...]
    candidates_considered: int
    budget_usd: float | None
    budget_fallback: str


@dataclass
class _ConnectionCatalogCache:
    records: list[dict[str, object]]
    stored_at: float


class NativeRouterEngine:
    def __init__(
        self,
        service: ModelRouterService,
        *,
        catalog_ttl_seconds: float = 30.0,
    ) -> None:
        self.service = service
        self.repository = service.repository
        self.catalog_ttl_seconds = catalog_ttl_seconds
        self._catalog_cache: dict[str, _ConnectionCatalogCache] = {}
        self._cache_lock = asyncio.Lock()

    async def plan(
        self,
        *,
        mode: RoutingMode,
        session_id: str | None,
        estimated_input_tokens: int,
        max_output_tokens: int,
        required_input_modalities: set[str] | None = None,
        required_output_modalities: set[str] | None = None,
        required_capabilities: set[str] | None = None,
        preferred_tags: set[str] | None = None,
        budget_usd: float | None = None,
        budget_fallback: str = "cheapest",
        excluded_paths: set[tuple[str, str]] | None = None,
        audit_engine: str = "native",
    ) -> NativeRoutePlan:
        session_hash = self.hash_session_id(session_id)
        candidates = await self.build_candidates(
            mode=mode,
            estimated_input_tokens=estimated_input_tokens,
            max_output_tokens=max_output_tokens,
            task_tags=preferred_tags or set(),
        )
        last_known_good = None
        get_lkgp = getattr(self.repository, "get_last_known_good", None)
        if callable(get_lkgp):
            last_known_good = get_lkgp(self.service.tenant_id, session_hash)
        request = RoutingRequest(
            tenant_id=self.service.tenant_id,
            mode=mode,
            required_input_modalities=frozenset(
                required_input_modalities or {"text"}
            ),
            required_output_modalities=frozenset(
                required_output_modalities or {"text"}
            ),
            required_capabilities=frozenset(required_capabilities or set()),
            estimated_input_tokens=estimated_input_tokens,
            max_output_tokens=max_output_tokens,
            budget_usd=budget_usd,
            budget_fallback=(
                "strict" if budget_fallback == "strict" else "cheapest"
            ),
            preferred_tags=frozenset(preferred_tags or set()),
            last_known_good=last_known_good,
            excluded_paths=frozenset(excluded_paths or set()),
        )
        decision = decide_route(candidates, request)
        targets = self._dispatch_targets(decision)
        record = getattr(self.repository, "record_routing_decision", None)
        decision_id = ""
        if callable(record):
            decision_id = record(
                self.service.tenant_id,
                session_id_hash=session_hash,
                engine=audit_engine,
                strategy=mode,
                connection_id=decision.selected.connection_id,
                model_id=decision.selected.model_id,
                reason_codes=list(decision.reason_codes),
                budget_limit_usd=(
                    budget_usd if audit_engine == "native" else None
                ),
                reserved_cost_usd=(
                    decision.selected.estimated_request_cost
                    if budget_usd is not None and audit_engine == "native"
                    else None
                ),
            )
        return NativeRoutePlan(
            decision_id=decision_id,
            session_id_hash=session_hash,
            mode=mode,
            reason_codes=decision.reason_codes,
            targets=targets,
            candidates_considered=len(decision.ranked),
            budget_usd=budget_usd,
            budget_fallback=budget_fallback,
        )

    async def build_candidates(
        self,
        *,
        mode: RoutingMode,
        estimated_input_tokens: int,
        max_output_tokens: int,
        task_tags: set[str] | None = None,
    ) -> list[RoutingCandidate]:
        connections = [
            item for item in self.service.list_connections() if item.enabled
        ]
        records_by_connection = await asyncio.gather(
            *(self._records_for_connection(item) for item in connections)
        )
        provider_counts: dict[str, int] = {}
        for connection in connections:
            provider_counts[connection.kind] = (
                provider_counts.get(connection.kind, 0) + 1
            )
        candidates: list[RoutingCandidate] = []
        for connection, records in zip(
            connections, records_by_connection, strict=True
        ):
            for record in records:
                model_id = str(record.get("id", "")).strip()
                if not model_id:
                    continue
                candidates.append(
                    self._candidate_from_record(
                        connection,
                        record,
                        mode=mode,
                        estimated_input_tokens=estimated_input_tokens,
                        max_output_tokens=max_output_tokens,
                        connection_pool_size=provider_counts[connection.kind],
                        task_tags=task_tags or set(),
                    )
                )
        return candidates

    def record_outcome(
        self,
        plan: NativeRoutePlan,
        target: NativeDispatchTarget,
        *,
        success: bool,
        latency_ms: float | None,
        outcome: str,
    ) -> None:
        record_stats = getattr(self.repository, "record_candidate_outcome", None)
        if callable(record_stats):
            record_stats(
                self.service.tenant_id,
                target.connection_id,
                target.model_id,
                success=success,
                latency_ms=latency_ms,
            )
        update_decision = getattr(
            self.repository, "update_routing_decision_outcome", None
        )
        if callable(update_decision) and plan.decision_id:
            update_decision(
                self.service.tenant_id, plan.decision_id, outcome
            )
        if not success and plan.budget_usd is not None and plan.decision_id:
            settle_budget = getattr(
                self.repository, "settle_routing_budget", None
            )
            if callable(settle_budget):
                settle_budget(
                    self.service.tenant_id,
                    plan.decision_id,
                    settled_cost_usd=None,
                    status="released",
                )

    def settle_budget(
        self,
        plan: NativeRoutePlan,
        target: NativeDispatchTarget,
        *,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> tuple[float | None, str]:
        actual_cost = None
        if (
            isinstance(input_tokens, int)
            and isinstance(output_tokens, int)
            and target.input_price_per_token is not None
            and target.output_price_per_token is not None
        ):
            actual_cost = (
                max(0, input_tokens) * target.input_price_per_token
                + max(0, output_tokens) * target.output_price_per_token
            )
        settled_cost = (
            actual_cost
            if actual_cost is not None
            else target.estimated_request_cost
        )
        status = "not_set"
        if plan.budget_usd is not None:
            if settled_cost is None:
                status = "unavailable"
            elif settled_cost <= plan.budget_usd:
                status = (
                    "settled"
                    if actual_cost is not None
                    else "covered_by_reservation"
                )
            else:
                status = "over_limit"
            settle = getattr(self.repository, "settle_routing_budget", None)
            if callable(settle) and plan.decision_id:
                settle(
                    self.service.tenant_id,
                    plan.decision_id,
                    settled_cost_usd=settled_cost,
                    status=status,
                )
        return actual_cost, status

    async def _records_for_connection(
        self, connection: RouterConnection
    ) -> list[dict[str, object]]:
        cached = self._catalog_cache.get(connection.id)
        now = time.monotonic()
        if cached and now - cached.stored_at <= self.catalog_ttl_seconds:
            return [dict(item) for item in cached.records]
        async with self._cache_lock:
            cached = self._catalog_cache.get(connection.id)
            now = time.monotonic()
            if cached and now - cached.stored_at <= self.catalog_ttl_seconds:
                return [dict(item) for item in cached.records]
            result, records = await self.service.fetch_connection_model_records(
                connection.id
            )
            if not result.ok:
                return []
            self._catalog_cache[connection.id] = _ConnectionCatalogCache(
                records=[dict(item) for item in records],
                stored_at=now,
            )
            return records

    def _candidate_from_record(
        self,
        connection: RouterConnection,
        record: dict[str, object],
        *,
        mode: RoutingMode,
        estimated_input_tokens: int,
        max_output_tokens: int,
        connection_pool_size: int,
        task_tags: set[str],
    ) -> RoutingCandidate:
        model_id = str(record["id"]).strip()
        input_price, output_price = self._prices(record)
        blended_cost = (
            input_price * 1_000_000 * 0.6
            + output_price * 1_000_000 * 0.4
            if input_price is not None and output_price is not None
            else None
        )
        estimated_cost = (
            input_price * estimated_input_tokens
            + output_price * max_output_tokens
            if input_price is not None and output_price is not None
            else None
        )
        stats_reader = getattr(self.repository, "get_candidate_stats", None)
        stats = (
            stats_reader(self.service.tenant_id, connection.id, model_id)
            if callable(stats_reader)
            else {}
        )
        input_modalities, output_modalities = self._modalities(record)
        capabilities = self._capabilities(record)
        preference_tags = self._preference_tags(
            connection, model_id, input_modalities, capabilities
        )
        return RoutingCandidate(
            tenant_id=self.service.tenant_id,
            connection_id=connection.id,
            connection_name=connection.name,
            model_id=model_id,
            enabled=connection.enabled,
            credential_available=bool(connection.masked_key),
            input_modalities=frozenset(input_modalities),
            output_modalities=frozenset(output_modalities),
            capabilities=frozenset(capabilities),
            context_length=self._integer(record.get("context_length")),
            quota_remaining=100.0,
            cost_per_million_tokens=blended_cost,
            estimated_request_cost=estimated_cost,
            p95_latency_ms=float(stats.get("latency_ema_ms", 1000)),
            latency_stddev_ms=float(stats.get("latency_stddev_ms", 0)),
            error_rate=float(stats.get("error_rate", 0)),
            breaker_state=str(stats.get("breaker_state", "closed")),  # type: ignore[arg-type]
            task_fit=self._task_fit(model_id, mode, task_tags),
            tier_priority=self._tier_priority(connection, model_id),
            context_affinity=self._context_affinity(
                self._integer(record.get("context_length")),
                estimated_input_tokens + max_output_tokens,
            ),
            connection_pool_size=connection_pool_size,
            preference_tags=frozenset(preference_tags),
        )

    def _dispatch_targets(
        self, decision: RouterDecision
    ) -> tuple[NativeDispatchTarget, ...]:
        connections = {
            item.id: item for item in self.service.list_connections()
        }
        targets: list[NativeDispatchTarget] = []
        for item in decision.ranked[:3]:
            candidate = item.candidate
            connection = connections.get(candidate.connection_id)
            if connection is None:
                continue
            targets.append(
                NativeDispatchTarget(
                    connection_id=connection.id,
                    connection_name=connection.name,
                    model_id=candidate.model_id,
                    chat_completions_url=self._chat_url(connection.base_url),
                    api_key=self.repository.resolve_api_key(
                        self.service.tenant_id, connection.id
                    ),
                    context_length=candidate.context_length,
                    input_price_per_token=self._prices(
                        self._catalog_record(connection.id, candidate.model_id)
                    )[0],
                    output_price_per_token=self._prices(
                        self._catalog_record(connection.id, candidate.model_id)
                    )[1],
                    estimated_request_cost=candidate.estimated_request_cost,
                    score=item.score,
                )
            )
        if not targets:
            raise NoEligibleCandidateError(
                "no_dispatch_target",
                "没有可调用的模型服务连接。",
            )
        return tuple(targets)

    def _catalog_record(
        self, connection_id: str, model_id: str
    ) -> dict[str, object]:
        cached = self._catalog_cache.get(connection_id)
        if cached is None:
            return {}
        return next(
            (
                record
                for record in cached.records
                if str(record.get("id", "")).strip() == model_id
            ),
            {},
        )

    @staticmethod
    def mode_for_request(
        model_id: str,
        requested_mode: str | None,
        default_mode: RoutingMode = "auto",
    ) -> RoutingMode:
        mode = (requested_mode or "").strip().lower()
        aliases = {"balanced": "auto"}
        mode = aliases.get(mode, mode)
        if mode in {"auto", "fast", "quality", "cheap", "reliable", "offline"}:
            return mode  # type: ignore[return-value]
        suffix = model_id.strip().lower().removeprefix("auto/").split(":", 1)[0]
        if suffix in {"fast", "quality", "cheap", "reliable", "offline"}:
            return suffix  # type: ignore[return-value]
        return default_mode

    @staticmethod
    def stable_canary_selected(
        session_id: str | None, canary_percent: int
    ) -> bool:
        if canary_percent <= 0:
            return False
        if canary_percent >= 100:
            return True
        stable = session_id or "anonymous"
        bucket = int.from_bytes(
            hashlib.sha256(stable.encode("utf-8")).digest()[:4], "big"
        ) % 100
        return bucket < canary_percent

    def hash_session_id(self, session_id: str | None) -> str | None:
        if not session_id:
            return None
        material = f"{self.service.tenant_id}:{session_id}".encode("utf-8")
        return hashlib.sha256(material).hexdigest()

    @staticmethod
    def _prices(
        record: dict[str, object]
    ) -> tuple[float | None, float | None]:
        pricing = record.get("pricing")
        if not isinstance(pricing, dict):
            return None, None

        def parse(name: str) -> float | None:
            try:
                value = float(pricing.get(name))
            except (TypeError, ValueError):
                return None
            return max(0.0, value)

        return parse("prompt"), parse("completion")

    @staticmethod
    def _modalities(
        record: dict[str, object]
    ) -> tuple[set[str], set[str]]:
        architecture = record.get("architecture")
        raw_input: object = None
        raw_output: object = None
        if isinstance(architecture, dict):
            raw_input = architecture.get("input_modalities")
            raw_output = architecture.get("output_modalities")
            modality = str(architecture.get("modality", "")).lower()
        else:
            modality = ""
        inputs = (
            {str(item).lower() for item in raw_input}
            if isinstance(raw_input, list)
            else {"text"}
        )
        outputs = (
            {str(item).lower() for item in raw_output}
            if isinstance(raw_output, list)
            else {"text"}
        )
        if "image" in modality:
            inputs.add("image")
        return inputs or {"text"}, outputs or {"text"}

    @staticmethod
    def _capabilities(record: dict[str, object]) -> set[str]:
        parameters = record.get("supported_parameters")
        values = (
            {str(item).lower() for item in parameters}
            if isinstance(parameters, list)
            else set()
        )
        capabilities: set[str] = set()
        if {"tools", "tool_choice"}.intersection(values):
            capabilities.add("tools")
        if {"reasoning", "include_reasoning"}.intersection(values):
            capabilities.add("reasoning")
        return capabilities

    @staticmethod
    def _preference_tags(
        connection: RouterConnection,
        model_id: str,
        input_modalities: set[str],
        capabilities: set[str],
    ) -> set[str]:
        lowered = model_id.lower()
        tags = {"chat"}
        if "image" in input_modalities:
            tags.update({"vision", "multimodal"})
        if "reasoning" in capabilities or any(
            hint in lowered for hint in ("reason", "thinking", "o3", "o4")
        ):
            tags.add("reasoning")
        if any(
            hint in lowered
            for hint in ("code", "coder", "codex", "devstral", "sol")
        ):
            tags.add("coding")
        specialized_only = any(
            hint in lowered
            for hint in (
                "code",
                "coder",
                "codex",
                "devstral",
                "embedding",
                "rerank",
                "moderation",
                "image-gen",
                "text-to-speech",
            )
        )
        if not specialized_only:
            tags.add("general")
        host = (urlparse(connection.base_url).hostname or "").lower()
        if connection.kind == "newapi" or host in {
            "localhost",
            "127.0.0.1",
            "new-api",
        }:
            tags.add("offline")
        return tags

    @staticmethod
    def _task_fit(
        model_id: str, mode: RoutingMode, task_tags: set[str]
    ) -> float:
        lowered = model_id.lower()
        quality_hints = (
            ("opus-5", 1.0),
            ("gpt-5.6", 0.98),
            ("fable-5", 0.96),
            ("sonnet-4.6", 0.92),
            ("gemini-3", 0.90),
            ("kimi-k3", 0.88),
            ("deepseek-v4", 0.86),
        )
        quality = next(
            (score for hint, score in quality_hints if hint in lowered),
            0.62,
        )
        if mode == "quality":
            return quality
        if "coding" in task_tags and any(
            hint in lowered
            for hint in ("code", "coder", "codex", "devstral", "sol")
        ):
            return max(quality, 0.92)
        if "reasoning" in task_tags and (
            "reasoning" in lowered
            or any(hint in lowered for hint in ("o3", "o4", "thinking"))
        ):
            return max(quality, 0.92)
        return max(0.5, quality * 0.8)

    @staticmethod
    def _tier_priority(
        connection: RouterConnection, model_id: str
    ) -> float:
        lowered = model_id.lower()
        if lowered.endswith(":free") or "/free" in lowered:
            return 0.0
        quality_hints = (
            ("opus-5", 1.0),
            ("gpt-5.6", 0.98),
            ("fable-5", 0.96),
            ("sonnet-4.6", 0.92),
            ("gemini-3", 0.90),
            ("kimi-k3", 0.88),
            ("deepseek-v4", 0.86),
        )
        return next(
            (score for hint, score in quality_hints if hint in lowered),
            0.5,
        )

    @staticmethod
    def _context_affinity(
        context_length: int | None, required_tokens: int
    ) -> float:
        if context_length is None or context_length <= 0:
            return 0.5
        if required_tokens <= 0:
            return 0.5
        return max(0.0, min(1.0, context_length / (required_tokens * 2)))

    @staticmethod
    def _chat_url(base_url: str) -> str:
        base = base_url.rstrip("/")
        if base.lower().endswith("/models"):
            return f"{base[:-7]}/chat/completions"
        if base.lower().endswith("/v1"):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"

    @staticmethod
    def _integer(value: Any) -> int | None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None
