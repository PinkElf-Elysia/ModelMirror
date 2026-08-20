from __future__ import annotations

import asyncio
import hashlib
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from .model_ids import is_realtime_chat_model_id
from .omniroute_parity import (
    ALGORITHM_VERSION,
    CONFIG_HASH,
    LEGACY_ALGORITHM_VERSION,
    classify_prompt_intent,
    get_task_fitness,
)
from .provider_chat import ProviderChatTarget
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
    """Expose the pinned upstream intent classifier as existing task tags."""

    intent = classify_prompt_intent(str(text or "")[-12_000:])
    if intent == "code":
        return {"coding"}
    if intent in {"math", "reasoning"}:
        return {"reasoning"}
    return set()


@dataclass(frozen=True)
class NativeDispatchTarget:
    connection_id: str
    connection_name: str
    model_id: str
    provider_chat_target: ProviderChatTarget
    context_length: int | None
    input_price_per_token: float | None
    output_price_per_token: float | None
    estimated_request_cost: float | None
    score: float

    @property
    def chat_completions_url(self) -> str:
        return self.provider_chat_target.chat_completions_url

    @property
    def api_key(self) -> str:
        return self.provider_chat_target.api_key


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
    algorithm_version: str
    config_hash: str
    task_type: str
    task_level: str
    selection_kind: str
    score_tier: str
    planning_latency_ms: float
    eligible_count: int
    finalist_count: int


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
        catalog_stale_seconds: float = 600.0,
    ) -> None:
        self.service = service
        self.repository = service.repository
        self.catalog_ttl_seconds = catalog_ttl_seconds
        self.catalog_stale_seconds = max(
            catalog_ttl_seconds, catalog_stale_seconds
        )
        self._catalog_cache: dict[str, _ConnectionCatalogCache] = {}
        self._cache_lock = asyncio.Lock()
        self._refresh_tasks: dict[str, asyncio.Task[None]] = {}

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
        task_type: str = "medium",
        task_level: str = "standard",
    ) -> NativeRoutePlan:
        session_hash = self.hash_session_id(session_id)
        algorithm_version = self.algorithm_version()
        candidates = await self.build_candidates(
            mode=mode,
            estimated_input_tokens=estimated_input_tokens,
            max_output_tokens=max_output_tokens,
            task_tags=preferred_tags or set(),
            task_type=task_type,
            task_level=task_level,
        )
        planning_started = time.perf_counter()
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
            task_type=task_type,
            algorithm_version=algorithm_version,
            rotator_key=(
                f"{self.service.tenant_id}:{mode}:{task_type}"
            ),
            incident_mode=(
                len(candidates) < 3
                or sum(
                    item.breaker_state != "closed" for item in candidates
                )
                >= max(2, len(candidates) // 3)
            ),
        )
        decision = decide_route(candidates, request)
        targets = self._dispatch_targets(decision)
        planning_latency_ms = (time.perf_counter() - planning_started) * 1000
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
                algorithm_version=decision.algorithm_version,
                config_hash=(
                    CONFIG_HASH
                    if decision.algorithm_version == ALGORITHM_VERSION
                    else LEGACY_ALGORITHM_VERSION
                ),
                task_type=task_type,
                task_level=task_level,
                selection_kind=decision.selection_kind,
                score_tier=decision.score_tier,
                planning_latency_ms=planning_latency_ms,
                eligible_count=decision.eligible_count,
                finalist_count=decision.finalist_count,
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
            algorithm_version=decision.algorithm_version,
            config_hash=(
                CONFIG_HASH
                if decision.algorithm_version == ALGORITHM_VERSION
                else LEGACY_ALGORITHM_VERSION
            ),
            task_type=task_type,
            task_level=task_level,
            selection_kind=decision.selection_kind,
            score_tier=decision.score_tier,
            planning_latency_ms=planning_latency_ms,
            eligible_count=decision.eligible_count,
            finalist_count=decision.finalist_count,
        )

    async def build_candidates(
        self,
        *,
        mode: RoutingMode,
        estimated_input_tokens: int,
        max_output_tokens: int,
        task_tags: set[str] | None = None,
        task_type: str = "medium",
        task_level: str = "standard",
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
        stats_reader_bulk = getattr(
            self.repository, "get_candidate_stats_bulk", None
        )
        use_bulk_stats = callable(stats_reader_bulk)
        stats_by_path = (
            stats_reader_bulk(self.service.tenant_id)
            if use_bulk_stats
            else {}
        )
        candidates: list[RoutingCandidate] = []
        for connection, records in zip(
            connections, records_by_connection, strict=True
        ):
            for record in records:
                model_id = str(record.get("id", "")).strip()
                if not is_realtime_chat_model_id(model_id):
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
                        task_type=task_type,
                        task_level=task_level,
                        stats=(
                            stats_by_path.get((connection.id, model_id), {})
                            if use_bulk_stats
                            else None
                        ),
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
        ttft_ms: float | None = None,
        e2e_ms: float | None = None,
        output_tokens: int | None = None,
        tokens_per_second: float | None = None,
    ) -> None:
        record_stats = getattr(self.repository, "record_candidate_outcome", None)
        if callable(record_stats):
            record_stats(
                self.service.tenant_id,
                target.connection_id,
                target.model_id,
                success=success,
                latency_ms=latency_ms,
                ttft_ms=ttft_ms,
                e2e_ms=e2e_ms,
                tokens_per_second=tokens_per_second,
            )
        update_decision = getattr(
            self.repository, "update_routing_decision_outcome", None
        )
        if callable(update_decision) and plan.decision_id:
            update_decision(
                self.service.tenant_id,
                plan.decision_id,
                outcome,
                ttft_ms=ttft_ms,
                e2e_ms=e2e_ms,
                output_tokens=output_tokens,
                tokens_per_second=tokens_per_second,
            )
        record_sample = getattr(
            self.repository, "record_router_candidate_sample", None
        )
        if callable(record_sample):
            record_sample(
                self.service.tenant_id,
                connection_id=target.connection_id,
                model_id=target.model_id,
                engine="native",
                algorithm_version=plan.algorithm_version,
                config_hash=plan.config_hash,
                task_type=plan.task_type,
                success=success,
                outcome=outcome,
                ttft_ms=ttft_ms,
                e2e_ms=e2e_ms,
                output_tokens=output_tokens,
                tokens_per_second=tokens_per_second,
                planning_latency_ms=plan.planning_latency_ms,
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
        if (
            cached
            and self.catalog_ttl_seconds > 0
            and now - cached.stored_at <= self.catalog_stale_seconds
        ):
            self._schedule_catalog_refresh(connection)
            return [dict(item) for item in cached.records]
        async with self._cache_lock:
            cached = self._catalog_cache.get(connection.id)
            now = time.monotonic()
            if cached and now - cached.stored_at <= self.catalog_ttl_seconds:
                return [dict(item) for item in cached.records]
            if (
                cached
                and self.catalog_ttl_seconds > 0
                and now - cached.stored_at <= self.catalog_stale_seconds
            ):
                self._schedule_catalog_refresh(connection)
                return [dict(item) for item in cached.records]
            result, records = await self.service.fetch_connection_model_records(
                connection.id
            )
            if not result.ok:
                self._catalog_cache.pop(connection.id, None)
                return []
            self._catalog_cache[connection.id] = _ConnectionCatalogCache(
                records=[dict(item) for item in records],
                stored_at=now,
            )
            return records

    def _schedule_catalog_refresh(self, connection: RouterConnection) -> None:
        task = self._refresh_tasks.get(connection.id)
        if task is not None and not task.done():
            return
        task = asyncio.create_task(self._refresh_connection(connection))
        self._refresh_tasks[connection.id] = task
        task.add_done_callback(
            lambda completed, connection_id=connection.id: (
                self._refresh_tasks.pop(connection_id, None)
            )
        )

    async def _refresh_connection(self, connection: RouterConnection) -> None:
        try:
            result, records = await self.service.fetch_connection_model_records(
                connection.id
            )
        except Exception:
            return
        if not result.ok:
            return
        async with self._cache_lock:
            self._catalog_cache[connection.id] = _ConnectionCatalogCache(
                records=[dict(item) for item in records],
                stored_at=time.monotonic(),
            )

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
        task_type: str,
        task_level: str,
        stats: dict[str, object] | None = None,
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
        if stats is None:
            stats = (
                stats_reader(self.service.tenant_id, connection.id, model_id)
                if callable(stats_reader)
                else {}
            )
        input_modalities, output_modalities = self._modalities(record)
        capabilities = self._capabilities(record)
        context_length = self._integer(record.get("context_length"))
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
            context_length=context_length,
            quota_remaining=100.0,
            cost_per_million_tokens=blended_cost,
            estimated_request_cost=estimated_cost,
            p95_latency_ms=self._float_or_none(
                stats.get("p95_latency_ms") or stats.get("latency_ema_ms")
            ),
            avg_ttft_ms=self._float_or_none(stats.get("ttft_ema_ms")),
            avg_e2e_latency_ms=self._float_or_none(stats.get("e2e_ema_ms")),
            avg_tokens_per_second=self._float_or_none(
                stats.get("tokens_per_second_ema")
            ),
            latency_stddev_ms=self._float_or_none(
                stats.get("latency_stddev_ms")
            ),
            error_rate=float(stats.get("error_rate", 0)),
            breaker_state=str(stats.get("breaker_state", "closed")),  # type: ignore[arg-type]
            task_fit=self._task_fit(
                model_id,
                task_type,
                task_level,
                capabilities,
                context_length,
            ),
            tier_priority=self._tier_priority(connection, model_id),
            context_affinity=self._context_affinity(
                context_length,
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
        for item in decision.ranked:
            candidate = item.candidate
            if not is_realtime_chat_model_id(candidate.model_id):
                continue
            connection = connections.get(candidate.connection_id)
            if connection is None:
                continue
            targets.append(
                NativeDispatchTarget(
                    connection_id=connection.id,
                    connection_name=connection.name,
                    model_id=candidate.model_id,
                    provider_chat_target=ProviderChatTarget.create(
                        source="managed",
                        provider_kind=connection.kind,
                        base_url=connection.base_url,
                        api_key=self.repository.resolve_api_key(
                            self.service.tenant_id, connection.id
                        ),
                        connection_id=connection.id,
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
            if len(targets) == 3:
                break
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
        model_id: str,
        task_type: str,
        task_level: str,
        capabilities: set[str],
        context_length: int | None,
    ) -> float:
        fitness = get_task_fitness(
            model_id,
            task_type,
            capabilities=frozenset(capabilities),
            context_length=context_length,
        )
        quality = NativeRouterEngine._tier_priority_from_id(model_id)
        if task_level == "critical":
            return fitness * 0.5 + quality * 0.5
        if task_level == "heavy":
            return fitness * 0.7 + quality * 0.3
        return fitness

    @staticmethod
    def _tier_priority(
        connection: RouterConnection, model_id: str
    ) -> float:
        lowered = model_id.lower()
        if lowered.endswith(":free") or "/free" in lowered:
            return 0.0
        return NativeRouterEngine._tier_priority_from_id(model_id)

    @staticmethod
    def _tier_priority_from_id(model_id: str) -> float:
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
        return next(
            (score for hint, score in quality_hints if hint in lowered),
            0.5,
        )

    @staticmethod
    def algorithm_version() -> str:
        configured = os.getenv(
            "MODEL_ROUTER_NATIVE_ALGORITHM", ALGORITHM_VERSION
        ).strip().lower()
        return (
            LEGACY_ALGORITHM_VERSION
            if configured in {"legacy", LEGACY_ALGORITHM_VERSION}
            else ALGORITHM_VERSION
        )

    @staticmethod
    def _float_or_none(value: object) -> float | None:
        try:
            parsed = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None

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
        """Compatibility seam for consumers migrating to Provider Chat v1."""

        return ProviderChatTarget.create(
            source="static",
            provider_kind="openai_compatible",
            base_url=base_url,
            api_key="",
        ).chat_completions_url

    @staticmethod
    def _integer(value: Any) -> int | None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None
