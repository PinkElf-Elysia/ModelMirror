from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

try:
    from server.model_router.egress import request_provider_url
    from server.model_router.schemas import RouterConnection
    from server.model_router.service import ModelRouterService
except ModuleNotFoundError:
    from model_router.egress import request_provider_url
    from model_router.schemas import RouterConnection
    from model_router.service import ModelRouterService

from .stt import (
    ALLOWED_AUDIO_FORMATS,
    MANUAL_TRANSCRIPTION_PROFILES,
    VERIFIED_TRANSCRIPTION_PROFILES,
    MultimodalServiceError,
    OpenRouterTarget,
    manual_verification_enabled,
)
from .tts import (
    ALLOWED_SPEECH_PROFILES,
    OPENAI_SPEECH_PROFILES,
    speech_output_format,
)
from .readiness import (
    AvailabilityStatus,
    OperationReadiness,
    SupportLevel,
    VerificationStatus,
)


AUDIO_CATALOG_TTL_SECONDS = 300.0
AUDIO_CATALOG_STALE_SECONDS = 1_800.0
AUDIO_PROFILE_REGISTRY_VERSION = "modelmirror-audio-contracts-2026-08-14-c2"

NATIVE_AUDIO_VOICES = (
    "alloy",
    "echo",
    "fable",
    "nova",
    "onyx",
    "shimmer",
)
DIRECT_AUDIO_INPUT_FORMATS = (
    "wav",
    "mp3",
    "aac",
    "m4a",
    "flac",
    "ogg",
)

AudioProvider = Literal["openrouter", "openai"]
AudioCatalogSource = Literal["openrouter", "openai", "mixed"]
AudioModelOperation = Literal[
    "transcribe",
    "synthesize_speech",
    "analyze_audio",
    "generate_audio",
    "realtime_voice",
    "clone_voice",
]
AudioChatMode = Literal[
    "direct_audio_input",
    "native_streaming_audio_output",
    "transcribe",
    "synthesize_speech",
]


@dataclass(frozen=True)
class AudioContract:
    operations: tuple[AudioModelOperation, ...]
    chat_modes: tuple[AudioChatMode, ...] = ()
    input_formats: tuple[str, ...] = ()
    output_formats: tuple[str, ...] = ()
    voices: tuple[str, ...] = ()
    supports_streaming_input: bool = False
    supports_streaming_output: bool = False
    supports_image_prompt: bool = False
    price_per_generation_usd: float | None = None
    fixed_duration_seconds: int | None = None
    behavior_verified: bool = False
    interaction_adapted: bool = False
    manual_verification_required: bool = False
    support_level: SupportLevel = "native"
    availability_status: AvailabilityStatus | None = None
    verification_status: VerificationStatus | None = None
    planned_reason: str | None = None


OPENROUTER_AUDIO_CONTRACTS: dict[str, AudioContract] = {
    "openai/gpt-audio": AudioContract(
        operations=("analyze_audio",),
        chat_modes=(
            "direct_audio_input",
            "native_streaming_audio_output",
        ),
        input_formats=DIRECT_AUDIO_INPUT_FORMATS,
        output_formats=("mp3",),
        voices=NATIVE_AUDIO_VOICES,
        supports_streaming_output=True,
        behavior_verified=True,
    ),
    "openai/gpt-audio-mini": AudioContract(
        operations=("analyze_audio",),
        chat_modes=(
            "direct_audio_input",
            "native_streaming_audio_output",
        ),
        input_formats=DIRECT_AUDIO_INPUT_FORMATS,
        output_formats=("mp3",),
        voices=NATIVE_AUDIO_VOICES,
        supports_streaming_output=True,
        behavior_verified=True,
    ),
    "google/gemini-3.6-flash": AudioContract(
        operations=("analyze_audio",),
        chat_modes=("direct_audio_input",),
        input_formats=DIRECT_AUDIO_INPUT_FORMATS,
        behavior_verified=True,
    ),
    "google/gemini-3.5-flash": AudioContract(
        operations=("analyze_audio",),
        chat_modes=("direct_audio_input",),
        input_formats=DIRECT_AUDIO_INPUT_FORMATS,
        behavior_verified=True,
    ),
    "google/gemini-3.5-flash-lite": AudioContract(
        operations=("analyze_audio",),
        chat_modes=("direct_audio_input",),
        input_formats=DIRECT_AUDIO_INPUT_FORMATS,
        behavior_verified=True,
    ),
    "mistralai/voxtral-small-24b-2507": AudioContract(
        operations=("analyze_audio",),
        chat_modes=("direct_audio_input",),
        input_formats=("wav", "mp3", "m4a", "flac", "ogg"),
        behavior_verified=True,
    ),
    "meta/muse-spark-1.1": AudioContract(
        operations=("analyze_audio",),
        availability_status="upstream_unavailable",
        verification_status="failed",
        planned_reason="实时行为探针返回供应商不可用，暂不开放。",
    ),
    "meta/muse-spark-1.2": AudioContract(
        operations=("analyze_audio",),
        chat_modes=("direct_audio_input",),
        input_formats=("wav",),
        behavior_verified=True,
    ),
    "thinkingmachines/inkling-small": AudioContract(
        operations=("analyze_audio",),
        chat_modes=("direct_audio_input",),
        input_formats=("wav",),
        behavior_verified=True,
    ),
    "openrouter/auto": AudioContract(
        operations=("analyze_audio",),
        behavior_verified=True,
        interaction_adapted=True,
        support_level="combined",
        planned_reason="智能调度会先将音频转成文字，再进入模型选择。",
    ),
    "openrouter/auto-beta": AudioContract(
        operations=("analyze_audio",),
        behavior_verified=True,
        interaction_adapted=True,
        support_level="combined",
        planned_reason="智能调度会先将音频转成文字，再进入模型选择。",
    ),
    "google/lyria-3-clip-preview": AudioContract(
        operations=("generate_audio",),
        output_formats=("mp3",),
        supports_image_prompt=True,
        price_per_generation_usd=0.04,
        fixed_duration_seconds=30,
        behavior_verified=True,
    ),
    "google/lyria-3-pro-preview": AudioContract(
        operations=("generate_audio",),
        output_formats=("mp3",),
        supports_image_prompt=True,
        price_per_generation_usd=0.08,
        behavior_verified=True,
    ),
}
for _model_id in (
    "google/gemini-2.5-flash",
    "google/gemini-2.5-flash-lite",
    "google/gemini-2.5-pro",
    "google/gemini-2.5-pro-preview",
    "google/gemini-2.5-pro-preview-05-06",
    "google/gemini-3.1-flash-lite",
    "google/gemini-3.1-flash-lite-preview",
    "google/gemini-3.1-pro-preview",
    "google/gemini-3.1-pro-preview-customtools",
    "google/gemini-3-flash-preview",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "thinkingmachines/inkling",
    "xiaomi/mimo-v2.5",
):
    OPENROUTER_AUDIO_CONTRACTS.setdefault(
        _model_id,
        AudioContract(
            operations=("analyze_audio",),
            chat_modes=("direct_audio_input",),
            input_formats=("wav",),
            behavior_verified=True,
        ),
    )
for _model_id, _profile in VERIFIED_TRANSCRIPTION_PROFILES.items():
    OPENROUTER_AUDIO_CONTRACTS.setdefault(
        _model_id,
        AudioContract(
            operations=("transcribe",),
            chat_modes=("transcribe",),
            input_formats=_profile.input_formats,
            behavior_verified=True,
        ),
    )
for _model_id, _profile in MANUAL_TRANSCRIPTION_PROFILES.items():
    OPENROUTER_AUDIO_CONTRACTS.setdefault(
        _model_id,
        AudioContract(
            operations=("transcribe",),
            chat_modes=("transcribe",),
            input_formats=_profile.input_formats,
            interaction_adapted=True,
            manual_verification_required=True,
            verification_status="manual_required",
            planned_reason=(
                "转写契约已接入，等待本地短音频人工验收。"
            ),
        ),
    )
for _model_id, _voices in ALLOWED_SPEECH_PROFILES.items():
    OPENROUTER_AUDIO_CONTRACTS.setdefault(
        _model_id,
        AudioContract(
            operations=("synthesize_speech",),
            chat_modes=("synthesize_speech",),
            output_formats=(speech_output_format(_model_id),),
            voices=_voices,
            behavior_verified=True,
        ),
    )

OPENAI_AUDIO_CONTRACTS: dict[str, AudioContract] = {
    "gpt-realtime-2.1-mini": AudioContract(
        operations=("realtime_voice",),
        input_formats=("webrtc",),
        output_formats=("webrtc",),
        voices=("marin", "cedar"),
        supports_streaming_input=True,
        supports_streaming_output=True,
        behavior_verified=True,
    ),
    "gpt-realtime-2.1": AudioContract(
        operations=("realtime_voice",),
        input_formats=("webrtc",),
        output_formats=("webrtc",),
        voices=("marin", "cedar"),
        supports_streaming_input=True,
        supports_streaming_output=True,
        behavior_verified=True,
    ),
    "gpt-4o-mini-tts": AudioContract(
        operations=("synthesize_speech", "clone_voice"),
        output_formats=("mp3",),
        voices=OPENAI_SPEECH_PROFILES["gpt-4o-mini-tts"],
        behavior_verified=True,
    ),
}


class AudioChatProfile(BaseModel):
    model_id: str
    display_name: str
    provider: AudioProvider
    connection_id: str | None = None
    invocable: bool
    interaction_status: Literal["ready", "planned", "disabled"]
    status_reason: str | None = None
    operations: list[AudioModelOperation] = Field(default_factory=list)
    chat_modes: list[AudioChatMode] = Field(default_factory=list)
    input_formats: list[str] = Field(default_factory=list)
    output_formats: list[str] = Field(default_factory=list)
    voices: list[str] = Field(default_factory=list)
    supports_streaming_input: bool = False
    supports_streaming_output: bool = False
    supports_image_prompt: bool = False
    price_per_generation_usd: float | None = None
    fixed_duration_seconds: int | None = None
    operation_readiness: list[OperationReadiness] = Field(
        default_factory=list
    )


class AudioModelCatalogResponse(BaseModel):
    source: AudioCatalogSource
    status: Literal["online", "stale", "offline", "disabled"]
    stale: bool
    synced_at: str | None
    catalog_version: str = AUDIO_PROFILE_REGISTRY_VERSION
    microphone_enabled: bool = False
    profiles: list[AudioChatProfile] = Field(default_factory=list)


class _CachedAudioCatalog:
    def __init__(
        self,
        profiles: list[AudioChatProfile],
        source: AudioCatalogSource,
        synced_at: str,
        stored_at: float,
    ) -> None:
        self.profiles = profiles
        self.source = source
        self.synced_at = synced_at
        self.stored_at = stored_at


class AudioCatalogService:
    def __init__(
        self,
        router_service: ModelRouterService,
        *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self.router_service = router_service
        self.client_factory = client_factory or self._default_client
        self.managed_client_factory = client_factory or self._direct_client
        self._cache: _CachedAudioCatalog | None = None
        self._lock = asyncio.Lock()

    def peek_catalog(self) -> AudioModelCatalogResponse | None:
        """Return the last bounded snapshot without performing provider I/O."""
        cached = self._cache
        if cached is None:
            return None
        age = time.monotonic() - cached.stored_at
        if age > AUDIO_CATALOG_STALE_SECONDS:
            return None
        return self._response(
            cached,
            stale=age > AUDIO_CATALOG_TTL_SECONDS,
        )

    async def get_catalog(
        self,
        *,
        force: bool = False,
    ) -> AudioModelCatalogResponse:
        chat_enabled = self._enabled("MULTIMODAL_CHAT_AUDIO_ENABLED")
        streaming_enabled = self._enabled(
            "MULTIMODAL_STREAMING_AUDIO_ENABLED"
        )
        generation_enabled = self._enabled(
            "MULTIMODAL_AUDIO_GENERATION_ENABLED"
        )
        realtime_enabled = self._enabled(
            "MULTIMODAL_REALTIME_VOICE_ENABLED"
        )
        future_audio_enabled = any(
            self._enabled(name)
            for name in (
                "MULTIMODAL_AUDIO_GENERATION_ENABLED",
                "MULTIMODAL_REALTIME_VOICE_ENABLED",
                "MULTIMODAL_VOICE_CLONING_ENABLED",
            )
        )
        if not chat_enabled and not streaming_enabled and not future_audio_enabled:
            profiles = self._local_registry_profiles(
                chat_enabled=False,
                streaming_enabled=False,
                generation_enabled=False,
                realtime_enabled=False,
            )
            return AudioModelCatalogResponse(
                source="mixed",
                status="disabled",
                stale=False,
                synced_at=None,
                microphone_enabled=self._enabled(
                    "MULTIMODAL_MICROPHONE_ENABLED"
                ),
                profiles=profiles,
            )

        now = time.monotonic()
        cached = self._cache
        if (
            not force
            and cached is not None
            and now - cached.stored_at <= AUDIO_CATALOG_TTL_SECONDS
        ):
            return self._response(cached, stale=False)

        async with self._lock:
            now = time.monotonic()
            cached = self._cache
            if (
                not force
                and cached is not None
                and now - cached.stored_at <= AUDIO_CATALOG_TTL_SECONDS
            ):
                return self._response(cached, stale=False)
            try:
                profiles, source, partial_failure = await self._fetch_all(
                    chat_enabled=chat_enabled,
                    streaming_enabled=streaming_enabled,
                    generation_enabled=generation_enabled,
                    realtime_enabled=realtime_enabled,
                )
            except (MultimodalServiceError, httpx.HTTPError, ValueError):
                if (
                    cached is not None
                    and now - cached.stored_at
                    <= AUDIO_CATALOG_STALE_SECONDS
                ):
                    return self._response(cached, stale=True)
                return AudioModelCatalogResponse(
                    source="openrouter",
                    status="offline",
                    stale=False,
                    synced_at=None,
                    microphone_enabled=self._enabled(
                        "MULTIMODAL_MICROPHONE_ENABLED"
                    ),
                    profiles=[],
                )

            if (
                partial_failure
                and cached is not None
                and now - cached.stored_at <= AUDIO_CATALOG_STALE_SECONDS
            ):
                return self._response(cached, stale=True)
            synced_at = datetime.now(UTC).isoformat()
            self._cache = _CachedAudioCatalog(
                profiles,
                source,
                synced_at,
                now,
            )
            return self._response(self._cache, stale=False)

    async def _fetch_all(
        self,
        *,
        chat_enabled: bool,
        streaming_enabled: bool,
        generation_enabled: bool,
        realtime_enabled: bool,
    ) -> tuple[list[AudioChatProfile], AudioCatalogSource, bool]:
        targets = self._catalog_targets()
        results = await asyncio.gather(
            *(
                self._fetch_provider(
                    provider,
                    target,
                    chat_enabled=chat_enabled,
                    streaming_enabled=streaming_enabled,
                    generation_enabled=generation_enabled,
                    realtime_enabled=realtime_enabled,
                )
                for provider, target in targets
            ),
            return_exceptions=True,
        )
        has_openai_target = any(
            provider == "openai" for provider, _ in targets
        )
        profiles: list[AudioChatProfile] = []
        successful_sources: set[AudioProvider] = set()
        partial_failure = False
        for (provider, _), result in zip(targets, results, strict=True):
            if isinstance(result, Exception):
                partial_failure = True
                continue
            successful_sources.add(provider)
            profiles.extend(result)
        if not has_openai_target:
            profiles.extend(
                self._direct_openai_placeholders(
                    realtime_enabled=realtime_enabled,
                )
            )
            successful_sources.add("openai")
        if not successful_sources:
            raise MultimodalServiceError(
                "audio_catalog_unavailable",
                "暂时无法读取音频模型目录，请检查模型服务连接。",
                status_code=502,
            )

        unique: dict[tuple[str, str | None, str], AudioChatProfile] = {}
        for profile in profiles:
            unique[
                (profile.provider, profile.connection_id, profile.model_id)
            ] = profile
        ordered = sorted(
            unique.values(),
            key=lambda profile: (
                0 if profile.interaction_status == "ready" else 1,
                0 if profile.interaction_status == "planned" else 1,
                profile.display_name.casefold(),
                profile.model_id,
            ),
        )
        source: AudioCatalogSource
        if len(successful_sources) > 1:
            source = "mixed"
        elif "openai" in successful_sources:
            source = "openai"
        else:
            source = "openrouter"
        return ordered, source, partial_failure

    async def _fetch_provider(
        self,
        provider: AudioProvider,
        target: OpenRouterTarget,
        *,
        chat_enabled: bool,
        streaming_enabled: bool,
        generation_enabled: bool,
        realtime_enabled: bool,
    ) -> list[AudioChatProfile]:
        params = (
            {"output_modalities": "all", "sort": "newest"}
            if provider == "openrouter"
            else None
        )
        client_factory = (
            self.managed_client_factory
            if target.connection_id
            else self.client_factory
        )
        async with client_factory() as client:
            response = await request_provider_url(
                client,
                self.router_service.egress_policy,
                target.connection_id,
                "GET",
                self._api_url(target.base_url, "models"),
                headers=self._provider_headers(provider, target.api_key),
                params=params,
            )
        self._raise_for_status(response)

        profiles: list[AudioChatProfile] = []
        for item in self._items(response.json()):
            profile = self._profile_from_item(
                provider,
                target.connection_id,
                item,
                chat_enabled=chat_enabled,
                streaming_enabled=streaming_enabled,
                generation_enabled=generation_enabled,
                realtime_enabled=realtime_enabled,
            )
            if profile is not None:
                profiles.append(profile)
        return profiles

    def _profile_from_item(
        self,
        provider: AudioProvider,
        connection_id: str | None,
        item: dict[str, Any],
        *,
        chat_enabled: bool,
        streaming_enabled: bool,
        generation_enabled: bool,
        realtime_enabled: bool,
        invocable: bool = True,
    ) -> AudioChatProfile | None:
        model_id = str(item.get("id") or "").strip()
        if not model_id:
            return None
        inputs = self._modalities(item, "input_modalities")
        outputs = self._modalities(item, "output_modalities")
        contract = (
            OPENROUTER_AUDIO_CONTRACTS.get(model_id)
            if provider == "openrouter"
            else OPENAI_AUDIO_CONTRACTS.get(model_id)
        )
        if provider == "openai" and contract is None:
            return None
        if provider == "openrouter" and not (
            "audio" in inputs
            or bool({"audio", "speech", "transcription"} & outputs)
        ):
            return None

        operations = list(contract.operations) if contract else []
        if "transcription" in outputs and "transcribe" not in operations:
            operations.append("transcribe")
        if "speech" in outputs and "synthesize_speech" not in operations:
            operations.append("synthesize_speech")
        if (
            "audio" in inputs
            and "text" in outputs
            and "analyze_audio" not in operations
        ):
            operations.append("analyze_audio")
        if (
            "audio" in outputs
            and "audio" not in inputs
            and "generate_audio" not in operations
        ):
            operations.append("generate_audio")
        if not operations:
            return None

        chat_modes: list[AudioChatMode] = []
        input_formats: set[str] = set(
            contract.input_formats if contract else ()
        )
        output_formats: set[str] = set(
            contract.output_formats if contract else ()
        )
        catalog_voices = set(
            self._string_list(item.get("supported_voices"))
        )
        voices: set[str] = set(contract.voices if contract else ())
        if contract and contract.behavior_verified and voices:
            if catalog_voices:
                voices.intersection_update(catalog_voices)
        else:
            voices.update(catalog_voices)
        status_reason: str | None = None
        operation_ready = False
        execution_allowed = bool(
            contract
            and (
                contract.behavior_verified
                or (
                    contract.interaction_adapted
                    and (
                        not contract.manual_verification_required
                        or manual_verification_enabled(model_id)
                    )
                )
            )
        )

        if provider == "openrouter" and "transcription" in outputs:
            input_formats.update(ALLOWED_AUDIO_FORMATS)
            if contract and contract.behavior_verified and chat_enabled:
                chat_modes.append("transcribe")
            elif not contract or not contract.behavior_verified:
                status_reason = "该转写模型尚未完成格式与语言行为验证。"
            else:
                status_reason = "聊天音频功能当前未启用。"

        if execution_allowed and contract:
            for mode in contract.chat_modes:
                if mode == "synthesize_speech" and not voices:
                    status_reason = (
                        "已验证声线不在实时目录中，请刷新后重新选择模型。"
                    )
                    continue
                if mode == "native_streaming_audio_output":
                    if streaming_enabled:
                        chat_modes.append(mode)
                    continue
                if chat_enabled:
                    chat_modes.append(mode)
            if "generate_audio" in operations:
                if generation_enabled:
                    operation_ready = True
                else:
                    status_reason = "音乐生成功能当前未启用。"
            if "realtime_voice" in operations:
                if realtime_enabled:
                    operation_ready = True
                else:
                    status_reason = "实时语音功能当前未启用。"
            if not chat_modes:
                if (
                    not operation_ready
                    and not contract.interaction_adapted
                    and status_reason is None
                ):
                    status_reason = "对应的聊天音频功能当前未启用。"
        elif contract and contract.planned_reason:
            status_reason = contract.planned_reason
        elif "transcribe" in operations:
            status_reason = "该转写模型尚未完成格式与语言行为验证。"
        elif "synthesize_speech" in operations:
            status_reason = "该语音合成模型尚未完成声音与输出格式验证。"
        elif "analyze_audio" in operations:
            status_reason = "该音频理解模型尚未完成本地调用验证。"
        elif "generate_audio" in operations:
            status_reason = "该音频生成模型尚未完成本地交互适配。"

        operation_readiness = self._build_operation_readiness(
            model_id=model_id,
            operations=operations,
            contract=contract,
            invocable=invocable,
            chat_enabled=chat_enabled,
            streaming_enabled=streaming_enabled,
            generation_enabled=generation_enabled,
            realtime_enabled=realtime_enabled,
        )
        ready_entries = [
            item
            for item in operation_readiness
            if item.interaction_status == "ready"
        ]
        available_ready_entries = [
            item
            for item in ready_entries
            if item.availability_status
            in {"available", "needs_configuration"}
        ]
        if chat_modes or operation_ready or available_ready_entries:
            interaction_status: Literal["ready", "planned", "disabled"] = (
                "ready"
            )
            status_reason = next(
                (
                    item.status_reason
                    for item in available_ready_entries
                    if item.status_reason
                ),
                None,
            )
        elif ready_entries:
            interaction_status = "disabled"
            status_reason = next(
                (item.status_reason for item in ready_entries if item.status_reason),
                status_reason,
            )
        elif any(
            item.interaction_status == "planned"
            for item in operation_readiness
        ):
            interaction_status = "planned"
            status_reason = next(
                (
                    item.status_reason
                    for item in operation_readiness
                    if item.interaction_status == "planned"
                    and item.status_reason
                ),
                status_reason,
            )
        else:
            interaction_status = "disabled"
            status_reason = next(
                (
                    item.status_reason
                    for item in operation_readiness
                    if item.status_reason
                ),
                status_reason,
            )

        return AudioChatProfile(
            model_id=model_id,
            display_name=str(item.get("name") or model_id).strip(),
            provider=provider,
            connection_id=connection_id,
            invocable=invocable,
            interaction_status=interaction_status,
            status_reason=status_reason,
            operations=operations,
            chat_modes=list(dict.fromkeys(chat_modes)),
            input_formats=sorted(input_formats),
            output_formats=sorted(output_formats),
            voices=sorted(voices),
            supports_streaming_input=bool(
                contract and contract.supports_streaming_input
            ),
            supports_streaming_output=bool(
                contract and contract.supports_streaming_output
            ),
            supports_image_prompt=bool(
                contract and contract.supports_image_prompt
            ),
            price_per_generation_usd=(
                contract.price_per_generation_usd if contract else None
            ),
            fixed_duration_seconds=(
                contract.fixed_duration_seconds if contract else None
            ),
            operation_readiness=operation_readiness,
        )

    @staticmethod
    def _operation_feature_enabled(
        operation: AudioModelOperation,
        *,
        chat_enabled: bool,
        streaming_enabled: bool,
        generation_enabled: bool,
        realtime_enabled: bool,
    ) -> bool:
        if operation == "generate_audio":
            return generation_enabled
        if operation == "realtime_voice":
            return realtime_enabled
        if operation == "clone_voice":
            return False
        if operation == "synthesize_speech":
            return chat_enabled
        if operation == "analyze_audio":
            return chat_enabled
        if operation == "transcribe":
            return chat_enabled
        return streaming_enabled

    @staticmethod
    def _disabled_feature_reason(
        operation: AudioModelOperation,
    ) -> str:
        if operation == "generate_audio":
            return "音乐生成功能当前未启用。"
        if operation == "realtime_voice":
            return "实时语音功能当前未启用。"
        if operation == "clone_voice":
            return "声音克隆仍处于安全门禁，本轮不开放。"
        return "聊天音频功能当前未启用。"

    @classmethod
    def _build_operation_readiness(
        cls,
        *,
        model_id: str,
        operations: list[AudioModelOperation],
        contract: AudioContract | None,
        invocable: bool,
        chat_enabled: bool,
        streaming_enabled: bool,
        generation_enabled: bool,
        realtime_enabled: bool,
    ) -> list[OperationReadiness]:
        is_floating_alias = model_id in {
            "~google/gemini-flash-latest",
            "~google/gemini-pro-latest",
        }
        result: list[OperationReadiness] = []
        for operation in operations:
            if is_floating_alias:
                result.append(
                    OperationReadiness(
                        operation=operation,
                        interaction_status="disabled",
                        availability_status="disabled",
                        verification_status="not_applicable",
                        support_level="native",
                        status_reason=(
                            "浮动别名不作为独立候选，请选择固定版本。"
                        ),
                    )
                )
                continue

            availability_override = (
                contract.availability_status if contract else None
            )
            verification_override = (
                contract.verification_status if contract else None
            )
            support_level = contract.support_level if contract else "native"
            implemented = bool(
                contract
                and (
                    contract.behavior_verified
                    or (
                        contract.interaction_adapted
                        and (
                            not contract.manual_verification_required
                            or manual_verification_enabled(model_id)
                        )
                    )
                )
                and operation != "clone_voice"
            )
            if availability_override == "upstream_unavailable":
                result.append(
                    OperationReadiness(
                        operation=operation,
                        interaction_status="disabled",
                        availability_status="upstream_unavailable",
                        verification_status=verification_override or "failed",
                        support_level=support_level,
                        status_reason=contract.planned_reason if contract else None,
                    )
                )
                continue

            if implemented:
                enabled = cls._operation_feature_enabled(
                    operation,
                    chat_enabled=chat_enabled,
                    streaming_enabled=streaming_enabled,
                    generation_enabled=generation_enabled,
                    realtime_enabled=realtime_enabled,
                )
                if not enabled:
                    availability: AvailabilityStatus = "disabled"
                    reason = cls._disabled_feature_reason(operation)
                elif not invocable:
                    availability = "needs_configuration"
                    reason = (
                        "请在模型服务连接中添加对应的音频连接并完成测试。"
                    )
                else:
                    availability = "available"
                    reason = (
                        contract.planned_reason
                        if contract and contract.interaction_adapted
                        else None
                    )
                result.append(
                    OperationReadiness(
                        operation=operation,
                        interaction_status="ready",
                        availability_status=availability,
                        verification_status=verification_override or "verified",
                        support_level=support_level,
                        status_reason=reason,
                    )
                )
                continue

            if operation == "clone_voice":
                reason = "上游暂不支持验证删除临时音色。"
            elif contract and contract.planned_reason:
                reason = contract.planned_reason
            elif operation == "transcribe":
                reason = "该转写模型尚未完成格式与语言行为验证。"
            elif operation == "synthesize_speech":
                reason = "该语音合成模型尚未完成声音与输出格式验证。"
            elif operation == "analyze_audio":
                reason = "该音频理解模型尚未完成本地调用验证。"
            else:
                reason = "该音频生成模型尚未完成本地交互适配。"
            result.append(
                OperationReadiness(
                    operation=operation,
                    interaction_status="planned",
                    availability_status="verification_required",
                    verification_status=(
                        verification_override
                        or ("contract_verified" if contract else "manual_required")
                    ),
                    support_level=support_level,
                    status_reason=reason,
                )
            )
        return result

    def _catalog_targets(
        self,
    ) -> list[tuple[AudioProvider, OpenRouterTarget]]:
        targets: list[tuple[AudioProvider, OpenRouterTarget]] = []
        try:
            targets.append(("openrouter", self.resolve_target()))
        except MultimodalServiceError:
            pass

        openai_connections = [
            item
            for item in self.router_service.list_connections()
            if item.kind == "openai"
            and item.enabled
            and item.health != "offline"
            and bool({"audio", "realtime"} & set(item.scopes))
        ]
        openai_connections.sort(
            key=lambda item: (0 if item.health == "online" else 1, item.id)
        )
        if openai_connections:
            targets.append(
                (
                    "openai",
                    self._target_from_connection(openai_connections[0]),
                )
            )
        return targets

    @staticmethod
    def _synthetic_catalog_item(
        model_id: str,
        contract: AudioContract,
    ) -> dict[str, Any]:
        inputs: list[str] = []
        outputs: list[str] = []
        if "analyze_audio" in contract.operations:
            inputs.append("audio")
            outputs.append("text")
        if "transcribe" in contract.operations:
            outputs.append("transcription")
        if "synthesize_speech" in contract.operations:
            outputs.append("speech")
        if "generate_audio" in contract.operations:
            outputs.append("audio")
        return {
            "id": model_id,
            "name": model_id,
            "architecture": {
                "input_modalities": inputs,
                "output_modalities": outputs,
            },
            "supported_voices": list(contract.voices),
        }

    def _local_registry_profiles(
        self,
        *,
        chat_enabled: bool,
        streaming_enabled: bool,
        generation_enabled: bool,
        realtime_enabled: bool,
    ) -> list[AudioChatProfile]:
        profiles: list[AudioChatProfile] = []
        for provider, contracts in (
            ("openrouter", OPENROUTER_AUDIO_CONTRACTS),
            ("openai", OPENAI_AUDIO_CONTRACTS),
        ):
            for model_id, contract in contracts.items():
                profile = self._profile_from_item(
                    provider,
                    None,
                    self._synthetic_catalog_item(model_id, contract),
                    chat_enabled=chat_enabled,
                    streaming_enabled=streaming_enabled,
                    generation_enabled=generation_enabled,
                    realtime_enabled=realtime_enabled,
                    invocable=False,
                )
                if profile is not None:
                    profiles.append(profile)
        return sorted(
            profiles,
            key=lambda item: (item.provider, item.display_name.casefold()),
        )

    def _direct_openai_placeholders(
        self,
        *,
        realtime_enabled: bool,
    ) -> list[AudioChatProfile]:
        profiles: list[AudioChatProfile] = []
        for model_id, contract in OPENAI_AUDIO_CONTRACTS.items():
            profile = self._profile_from_item(
                "openai",
                None,
                self._synthetic_catalog_item(model_id, contract),
                chat_enabled=True,
                streaming_enabled=True,
                generation_enabled=False,
                realtime_enabled=realtime_enabled,
                invocable=False,
            )
            if profile is not None:
                profiles.append(profile)
        return profiles

    def resolve_target(self) -> OpenRouterTarget:
        connections = [
            item
            for item in self.router_service.list_connections(scope="audio")
            if item.kind == "openrouter"
            and item.enabled
            and item.health != "offline"
        ]
        connections.sort(
            key=lambda item: (0 if item.health == "online" else 1, item.id)
        )
        if connections:
            return self._target_from_connection(connections[0])

        api_key = (
            os.getenv("MULTIMODAL_OPENROUTER_API_KEY", "").strip()
            or os.getenv("OPENROUTER_API_KEY", "").strip()
        )
        if not api_key:
            raise MultimodalServiceError(
                "openrouter_not_configured",
                "尚未配置 OpenRouter。",
                status_code=503,
            )
        return OpenRouterTarget(
            base_url=os.getenv(
                "MULTIMODAL_OPENROUTER_BASE_URL",
                "https://openrouter.ai/api/v1",
            ).strip(),
            api_key=api_key,
            connection_id=None,
            cache_key="environment:openrouter",
        )

    def _target_from_connection(
        self,
        connection: RouterConnection,
    ) -> OpenRouterTarget:
        try:
            api_key = self.router_service.repository.resolve_api_key(
                self.router_service.tenant_id,
                connection.id,
            )
        except Exception as exc:
            raise MultimodalServiceError(
                "provider_credentials_unavailable",
                f"无法读取“{connection.name}”的连接密钥，请重新保存连接。",
                status_code=503,
            ) from exc
        return OpenRouterTarget(
            base_url=connection.base_url,
            api_key=api_key,
            connection_id=connection.id,
            cache_key=f"connection:{connection.id}",
        )

    @classmethod
    def chat_completions_url(cls, target: OpenRouterTarget) -> str:
        return cls._api_url(target.base_url, "chat/completions")

    @staticmethod
    def _response(
        cached: _CachedAudioCatalog,
        *,
        stale: bool,
    ) -> AudioModelCatalogResponse:
        return AudioModelCatalogResponse(
            source=cached.source,
            status="stale" if stale else "online",
            stale=stale,
            synced_at=cached.synced_at,
            microphone_enabled=AudioCatalogService._enabled(
                "MULTIMODAL_MICROPHONE_ENABLED"
            ),
            profiles=[item.model_copy(deep=True) for item in cached.profiles],
        )

    @staticmethod
    def _enabled(name: str) -> bool:
        return os.getenv(name, "false").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    @staticmethod
    def _api_url(base_url: str, path: str) -> str:
        root = str(base_url or "").strip().rstrip("/")
        for suffix in (
            "/chat/completions",
            "/audio/transcriptions",
            "/audio/speech",
            "/models",
        ):
            if root.lower().endswith(suffix):
                root = root[: -len(suffix)].rstrip("/")
                break
        if not root.lower().endswith("/v1"):
            root = f"{root}/v1"
        return f"{root}/{path.lstrip('/')}"

    @staticmethod
    def _provider_headers(
        provider: AudioProvider,
        api_key: str,
    ) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {api_key}"}
        if provider == "openrouter":
            title = os.getenv("OPENROUTER_APP_TITLE", "ModelMirror").strip()
            referer = os.getenv(
                "OPENROUTER_HTTP_REFERER", "http://localhost:5173"
            ).strip()
            headers.update(
                {
                    "HTTP-Referer": referer,
                    "X-Title": title,
                    "X-OpenRouter-Title": title,
                }
            )
        return headers

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code >= 400:
            raise httpx.HTTPStatusError(
                "audio catalog unavailable",
                request=response.request,
                response=response,
            )

    @staticmethod
    def _items(payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, dict) or not isinstance(
            payload.get("data"), list
        ):
            raise ValueError("invalid audio catalog")
        return [
            item for item in payload["data"] if isinstance(item, dict)
        ]

    @staticmethod
    def _modalities(item: dict[str, Any], field: str) -> set[str]:
        raw = item.get(field)
        architecture = item.get("architecture")
        if raw is None and isinstance(architecture, dict):
            raw = architecture.get(field)
        if not isinstance(raw, list):
            return set()
        return {
            str(value).strip().lower()
            for value in raw
            if isinstance(value, str) and value.strip()
        }

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [
            str(item).strip()
            for item in value
            if isinstance(item, str) and item.strip()
        ]

    @staticmethod
    def _direct_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(connect=15, read=30, write=15, pool=10),
            follow_redirects=False,
            trust_env=False,
        )

    @staticmethod
    def _default_client() -> httpx.AsyncClient:
        timeout = httpx.Timeout(connect=15, read=30, write=15, pool=10)
        kwargs: dict[str, Any] = {
            "timeout": timeout,
            "follow_redirects": False,
            "trust_env": False,
        }
        proxy = (
            os.getenv("OPENROUTER_PROXY")
            or os.getenv("HTTPS_PROXY")
            or os.getenv("HTTP_PROXY")
            or os.getenv("ALL_PROXY")
            or None
        )
        if proxy:
            kwargs["proxy"] = proxy
        return httpx.AsyncClient(**kwargs)
