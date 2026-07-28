from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from .client import OmniRouteClient, OmniRouteClientError
from .config import OmniRouteSettings
from .schemas import (
    ModelCandidate,
    ModelCatalogResponse,
    RouteCandidate,
    RouterStatusResponse,
)

CATALOG_VERSION = "omniroute-v3.8.48-runtime"
VERIFIED_SPEECH_MODEL_IDS = {"microsoft/mai-voice-2"}

CURATED_ROUTES: tuple[tuple[str, str, str], ...] = (
    ("auto", "智能调度", "按任务、质量、成本与可用性自动选择模型"),
    ("auto/fast", "极速通道", "优先降低首字延迟和总响应时间"),
    ("auto/cheap", "预算通道", "优先选择成本较低的可用模型"),
    ("auto/coding", "编程通道", "优先选择代码与工程任务适配模型"),
    ("auto/vision", "视觉通道", "为图片理解和多模态请求选择模型"),
    ("auto/smart", "质量通道", "优先选择综合质量较高的可用模型"),
)


def _string_list(value: Any, default: list[str] | None = None) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if isinstance(item, str) and item.strip()]
    return list(default or [])


def _capabilities(value: Any, input_modalities: list[str], model_type: str) -> list[str]:
    result: list[str] = []
    if isinstance(value, list):
        result.extend(str(item) for item in value if isinstance(item, str))
    elif isinstance(value, dict):
        result.extend(str(key) for key, enabled in value.items() if enabled)
    if model_type == "chat" and "text" not in result:
        result.append("text")
    for modality in input_modalities:
        if modality not in result:
            result.append(modality)
    return list(dict.fromkeys(result))


def _operations(
    input_modalities: list[str],
    output_modalities: list[str],
) -> list[str]:
    inputs = set(input_modalities)
    outputs = set(output_modalities)
    operations: list[str] = []
    if "transcription" in outputs:
        operations.append("transcribe")
    if "speech" in outputs:
        operations.append("synthesize_speech")
    if "audio" in outputs:
        operations.append("generate_audio")
    if "video" in outputs:
        operations.append("generate_video")
    if "embeddings" in outputs:
        operations.append("embed")
    if "rerank" in outputs:
        operations.append("rerank")
    if "audio" in inputs and "text" in outputs:
        operations.append("analyze_audio")
    if "video" in inputs and "text" in outputs:
        operations.append("analyze_video")
    if "text" in inputs and ({"text", "image"} & outputs):
        operations.append("chat")
    return list(dict.fromkeys(operations)) or ["chat"]


def _primary_operation(operations: list[str]) -> str:
    priority = (
        "transcribe",
        "synthesize_speech",
        "generate_audio",
        "generate_video",
        "embed",
        "rerank",
        "chat",
        "analyze_audio",
        "analyze_video",
    )
    return next(
        (operation for operation in priority if operation in operations),
        "chat",
    )


def _interaction(
    primary_operation: str,
    invocation_id: str,
) -> tuple[str, str]:
    if primary_operation in {"chat", "transcribe"}:
        return "ready", "chat"
    if (
        primary_operation == "synthesize_speech"
        and invocation_id in VERIFIED_SPEECH_MODEL_IDS
    ):
        return "ready", "chat"
    if primary_operation in {"embed", "rerank"}:
        return "ready", "rag"
    return "planned", "planned"


def normalize_model(raw: dict[str, Any]) -> ModelCandidate | None:
    invocation_id = str(raw.get("id") or "").strip()
    if not invocation_id:
        return None
    root = str(raw.get("root") or "").strip() or None
    profile_id = root or invocation_id
    provider = str(raw.get("owned_by") or invocation_id.split("/", 1)[0] or "unknown")
    model_type = str(raw.get("type") or "chat")
    input_modalities = _string_list(raw.get("input_modalities"), ["text"])
    output_modalities = _string_list(raw.get("output_modalities"), ["text"])
    operations = _operations(input_modalities, output_modalities)
    primary_operation = _primary_operation(operations)
    interaction_status, ui_entrypoint = _interaction(
        primary_operation,
        invocation_id,
    )
    free_value = raw.get("free")
    if not isinstance(free_value, bool):
        free_value = raw.get("is_free") if isinstance(raw.get("is_free"), bool) else None
    return ModelCandidate(
        profile_id=profile_id,
        invocation_id=invocation_id,
        root=root,
        name=str(raw.get("name") or invocation_id),
        provider=provider,
        type=model_type,
        context_length=(
            int(raw["context_length"])
            if isinstance(raw.get("context_length"), (int, float))
            else None
        ),
        max_output_tokens=(
            int(raw["max_output_tokens"])
            if isinstance(raw.get("max_output_tokens"), (int, float))
            else None
        ),
        input_modalities=input_modalities,
        output_modalities=output_modalities,
        operations=operations,
        primary_operation=primary_operation,
        interaction_status=interaction_status,
        ui_entrypoint=ui_entrypoint,
        capabilities=_capabilities(raw.get("capabilities"), input_modalities, model_type),
        invocable=model_type == "chat",
        availability="live" if model_type == "chat" else "disabled",
        free=free_value,
    )


def normalize_models(payload: dict[str, Any]) -> list[ModelCandidate]:
    raw_models = payload.get("data")
    if not isinstance(raw_models, list):
        raw_models = payload.get("models")
    if not isinstance(raw_models, list):
        return []
    models: list[ModelCandidate] = []
    seen: set[str] = set()
    for item in raw_models:
        if not isinstance(item, dict):
            continue
        candidate = normalize_model(item)
        if candidate is None or candidate.invocation_id in seen:
            continue
        if candidate.invocation_id == "auto" or candidate.invocation_id.startswith("auto/"):
            continue
        seen.add(candidate.invocation_id)
        models.append(candidate)
    return models


def _route_channel(route_id: str) -> str:
    return "auto" if route_id == "auto" else route_id.removeprefix("auto/")


async def _route_candidate(
    client: OmniRouteClient,
    route_id: str,
    name: str,
    description: str,
    advertised_route_ids: set[str],
) -> RouteCandidate:
    channel = _route_channel(route_id)
    route_is_advertised = route_id in advertised_route_ids
    if route_id == "auto":
        route_is_advertised = route_is_advertised or any(
            route.startswith("auto/") for route in advertised_route_ids
        )
    if route_is_advertised:
        return RouteCandidate(
            id=route_id,
            name=name,
            description=description,
            channel=channel,
            invocable=True,
            availability="live",
        )
    try:
        payload = await client.fetch_route_candidates(channel)
    except OmniRouteClientError:
        return RouteCandidate(
            id=route_id,
            name=name,
            description=description,
            channel=channel,
            invocable=False,
            availability="degraded",
        )
    candidates = payload.get("candidates")
    candidate_rows = candidates if isinstance(candidates, list) else []
    reachable_count = sum(
        1
        for item in candidate_rows
        if isinstance(item, dict)
        and item.get("reachable") is True
        and item.get("excluded") is not True
    )
    return RouteCandidate(
        id=route_id,
        name=name,
        description=description,
        channel=channel,
        candidate_count=len(candidate_rows),
        reachable_count=reachable_count,
        invocable=reachable_count > 0,
        availability="live" if reachable_count > 0 else "degraded",
    )


def _disabled_routes() -> list[RouteCandidate]:
    return [
        RouteCandidate(
            id=route_id,
            name=name,
            description=description,
            channel=_route_channel(route_id),
        )
        for route_id, name, description in CURATED_ROUTES
    ]


class OmniRouteCatalogService:
    def __init__(
        self,
        settings_factory: Callable[[], OmniRouteSettings],
        client_factory: Callable[[OmniRouteSettings], OmniRouteClient] = OmniRouteClient,
    ):
        self._settings_factory = settings_factory
        self._client_factory = client_factory
        self._lock = asyncio.Lock()
        self._last_good: ModelCatalogResponse | None = None
        self._last_good_monotonic = 0.0

    def reset(self) -> None:
        self._last_good = None
        self._last_good_monotonic = 0.0

    async def get_catalog(self, *, force: bool = False) -> ModelCatalogResponse:
        settings = self._settings_factory()
        if not settings.enabled:
            return ModelCatalogResponse(
                source="bundled",
                router_status="disabled",
                stale=False,
                synced_at=None,
                catalog_version=CATALOG_VERSION,
                routes=_disabled_routes(),
            )
        if not settings.configured:
            return ModelCatalogResponse(
                source="bundled",
                router_status="offline",
                stale=False,
                synced_at=None,
                catalog_version=CATALOG_VERSION,
                routes=_disabled_routes(),
            )

        now = time.monotonic()
        if (
            not force
            and self._last_good is not None
            and now - self._last_good_monotonic <= settings.catalog_ttl_seconds
        ):
            return self._last_good.model_copy(deep=True)

        async with self._lock:
            now = time.monotonic()
            if (
                not force
                and self._last_good is not None
                and now - self._last_good_monotonic <= settings.catalog_ttl_seconds
            ):
                return self._last_good.model_copy(deep=True)
            try:
                client = self._client_factory(settings)
                payload = await client.fetch_models()
                raw_models = payload.get("data")
                if not isinstance(raw_models, list):
                    raw_models = payload.get("models")
                if not isinstance(raw_models, list):
                    raw_models = []
                advertised_route_ids = {
                    str(item.get("id")).strip()
                    for item in raw_models
                    if isinstance(item, dict)
                    and (
                        str(item.get("id") or "").strip() == "auto"
                        or str(item.get("id") or "").strip().startswith("auto/")
                    )
                }
                route_results = await asyncio.gather(
                    *(
                        _route_candidate(
                            client,
                            route_id,
                            name,
                            description,
                            advertised_route_ids,
                        )
                        for route_id, name, description in CURATED_ROUTES
                    )
                )
                models = normalize_models(payload)
                result = ModelCatalogResponse(
                    source="omniroute",
                    router_status="online",
                    stale=False,
                    synced_at=datetime.now(timezone.utc).isoformat(),
                    catalog_version=CATALOG_VERSION,
                    models=models,
                    routes=list(route_results),
                )
                self._last_good = result
                self._last_good_monotonic = time.monotonic()
                return result.model_copy(deep=True)
            except Exception:
                if (
                    self._last_good is not None
                    and now - self._last_good_monotonic <= settings.stale_ttl_seconds
                ):
                    stale = self._last_good.model_copy(deep=True)
                    stale.router_status = "stale"
                    stale.stale = True
                    for model in stale.models:
                        model.availability = "degraded"
                    for route in stale.routes:
                        route.availability = "degraded"
                    return stale
                return ModelCatalogResponse(
                    source="bundled",
                    router_status="offline",
                    stale=False,
                    synced_at=None,
                    catalog_version=CATALOG_VERSION,
                    routes=_disabled_routes(),
                )

    async def get_status(self) -> RouterStatusResponse:
        settings = self._settings_factory()
        catalog = await self.get_catalog()
        return RouterStatusResponse(
            enabled=settings.enabled,
            configured=settings.configured,
            status=catalog.router_status,
            version=CATALOG_VERSION,
            candidate_count=len(catalog.models),
            route_count=sum(1 for route in catalog.routes if route.invocable),
            synced_at=catalog.synced_at,
            stale=catalog.stale,
        )
