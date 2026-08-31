from __future__ import annotations

import hashlib
import logging
import os
import re
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal

import httpx

try:
    from server.model_router.egress import ProviderEgressPolicy, request_provider_url
    from server.model_router.service import ModelRouterService
except ModuleNotFoundError:
    from model_router.egress import ProviderEgressPolicy, request_provider_url
    from model_router.service import ModelRouterService

if TYPE_CHECKING:
    from server.model_router.multimodal_gateway import ManagedMultimodalGateway

from .stt import MultimodalServiceError, OpenRouterTarget


logger = logging.getLogger("modelmirror.multimodal")

MAX_SPEECH_INPUT_CHARS = 4_000
MAX_SPEECH_BYTES = 20 * 1024 * 1024
CATALOG_CACHE_SECONDS = 300.0
SPEECH_PROFILE_VERSION = "tts-contracts-2026-08-13-c1"
GEMINI_PCM_TTS_MODEL_ID = "google/gemini-3.1-flash-tts-preview"
DEEPGRAM_FLUX_TTS_MODEL_ID = "deepgram/flux-tts:free"
FISH_AUDIO_PUBLIC_VOICES = (
    "8ef4a238714b45718ce04243307c57a7",
    "802e3bc2b27e49c2995d23ef70e6ac89",
)
MINIMAX_SYSTEM_SPEECH_VOICES = (
    "Chinese (Mandarin)_News_Anchor",
    "Chinese (Mandarin)_Reliable_Executive",
    "Chinese (Mandarin)_Mature_Woman",
    "Chinese (Mandarin)_Warm_Girl",
    "English_expressive_narrator",
    "English_CalmWoman",
    "English_magnetic_voiced_man",
    "English_Graceful_Lady",
)
DEEPGRAM_FLUX_TTS_VOICES = (
    "flux-alexis-en",
    "flux-bree-en",
    "flux-brittany-en",
    "flux-brooke-en",
    "flux-bruce-en",
    "flux-cliff-en",
    "flux-cole-en",
    "flux-colin-en",
    "flux-conor-en",
    "flux-donovan-en",
    "flux-drew-en",
    "flux-elise-en",
    "flux-gemma-en",
    "flux-haley-en",
    "flux-hannah-en",
    "flux-heather-en",
    "flux-jack-en",
    "flux-kai-en",
    "flux-kelsey-en",
    "flux-kit-en",
    "flux-maeve-en",
    "flux-marcelo-en",
    "flux-marcus-en",
    "flux-meena-en",
    "flux-meghan-en",
    "flux-miles-en",
    "flux-naveen-en",
    "flux-paige-en",
    "flux-priya-en",
    "flux-rufus-en",
    "flux-sean-en",
    "flux-sharon-en",
    "flux-sienna-en",
    "flux-tanner-en",
    "flux-wade-en",
    "flux-wes-en",
)
SPEECH_OUTPUT_FORMATS: dict[str, str] = {
    GEMINI_PCM_TTS_MODEL_ID: "wav",
}
ALLOWED_SPEECH_PROFILES: dict[str, tuple[str, ...]] = {
    DEEPGRAM_FLUX_TTS_MODEL_ID: DEEPGRAM_FLUX_TTS_VOICES,
    "fish-audio/s1": FISH_AUDIO_PUBLIC_VOICES,
    "fish-audio/s2-pro": FISH_AUDIO_PUBLIC_VOICES,
    "fish-audio/s2.1-pro-free:free": FISH_AUDIO_PUBLIC_VOICES,
    "fish-audio/s2.1-pro": FISH_AUDIO_PUBLIC_VOICES,
    "minimax/speech-2.8-hd": MINIMAX_SYSTEM_SPEECH_VOICES,
    "minimax/speech-2.8-turbo": MINIMAX_SYSTEM_SPEECH_VOICES,
    "microsoft/mai-voice-2": (
        "en-US-Harper:MAI-Voice-2",
        "de-DE-Klaus:MAI-Voice-2",
        "es-MX-Valeria:MAI-Voice-2",
        "fr-FR-Soleil:MAI-Voice-2",
    ),
    "microsoft/mai-voice-2-flash": (
        "en-US-Harper:MAI-Voice-2",
        "de-DE-Klaus:MAI-Voice-2",
        "es-MX-Valeria:MAI-Voice-2",
        "fr-FR-Soleil:MAI-Voice-2",
    ),
    "mistralai/voxtral-mini-tts-2603": (
        "en_paul_neutral",
        "fr_marie_neutral",
        "gb_jane_neutral",
        "gb_oliver_neutral",
    ),
    "qwen/qwen-audio-3.0-tts-flash": (
        "longanhuan_v3.6",
        "loongjohn",
    ),
    "qwen/qwen-audio-3.0-tts-plus": (
        "longanlingxin",
        "longanlufeng",
    ),
    "x-ai/grok-voice-tts-1.0": (
        "ara",
        "eve",
        "leo",
        "rex",
        "sal",
    ),
    "deepgram/aura-2": (
        "aura-2-amalthea-en",
        "aura-2-andromeda-en",
        "aura-2-apollo-en",
        "aura-2-asteria-en",
    ),
    "zyphra/zonos-v0.1-transformer": (
        "american_female",
        "american_male",
        "british_female",
        "british_male",
    ),
    "zyphra/zonos-v0.1-hybrid": (
        "american_female",
        "american_male",
        "british_female",
        "british_male",
    ),
    "canopylabs/orpheus-3b-0.1-ft": (
        "dan",
        "jess",
        "leah",
        "leo",
    ),
    "sesame/csm-1b": (
        "conversational_a",
        "conversational_b",
        "read_speech_a",
        "read_speech_b",
    ),
    "hexgrad/kokoro-82m": (
        "af_alloy",
        "af_aoede",
        "af_bella",
        "am_fenrir",
    ),
    GEMINI_PCM_TTS_MODEL_ID: (
        "Aoede",
        "Charon",
        "Kore",
        "Puck",
    ),
}
OPENAI_SPEECH_PROFILES: dict[str, tuple[str, ...]] = {
    "gpt-4o-mini-tts": (
        "alloy",
        "ash",
        "ballad",
        "cedar",
        "coral",
        "echo",
        "fable",
        "marin",
        "nova",
        "onyx",
        "sage",
        "shimmer",
        "verse",
    ),
}


def speech_output_format(model_id: str) -> str:
    return SPEECH_OUTPUT_FORMATS.get(model_id, "mp3")


@dataclass(frozen=True)
class SpeechResult:
    content: bytes
    requested_model: str
    actual_model: str
    provider: str
    request_id: str
    generation_id: str | None
    output_bytes: int
    response_format: str = "mp3"
    cost_usd: float | None = None
    cost_kind: str = "unavailable"
    execution_mode: Literal["managed", "legacy"] = "legacy"
    provider_route_receipts: list[dict[str, Any]] | None = None


class OpenRouterTtsAdapter:
    def __init__(
        self,
        *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        catalog_cache_seconds: float = CATALOG_CACHE_SECONDS,
        egress_policy: ProviderEgressPolicy | None = None,
    ) -> None:
        self._client_factory = client_factory or self._default_client
        self._managed_client_factory = client_factory or self._direct_client
        self.catalog_cache_seconds = max(0.0, float(catalog_cache_seconds))
        self._catalog_cache: dict[str, tuple[float, set[str]]] = {}
        self._egress_policy = egress_policy

    async def synthesize(
        self,
        target: OpenRouterTarget,
        *,
        model_id: str,
        text: str,
        voice: str,
        speed: float,
        provider: str = "openrouter",
    ) -> tuple[bytes, str | None, str]:
        await self._verify_speech_model(target, model_id, provider=provider)
        output_format = speech_output_format(model_id)
        upstream_format = "pcm" if output_format == "wav" else "mp3"
        client_factory = (
            self._managed_client_factory
            if target.connection_id
            else self._client_factory
        )
        async with client_factory() as client:
            try:
                response = await request_provider_url(
                    client,
                    self._egress_policy or ProviderEgressPolicy(),
                    target.connection_id if self._egress_policy else None,
                    "POST",
                    self._api_url(target.base_url, "audio/speech"),
                    headers=self._headers(target.api_key, provider=provider),
                    json={
                        "model": model_id,
                        "input": text,
                        "voice": voice,
                        "response_format": upstream_format,
                        "speed": speed,
                    },
                )
            except (
                httpx.ConnectTimeout,
                httpx.ReadTimeout,
                httpx.WriteTimeout,
            ) as exc:
                raise MultimodalServiceError(
                    "upstream_timeout",
                    "语音生成超时。请缩短文字后重试。",
                    status_code=504,
                ) from exc
            except httpx.HTTPError as exc:
                raise MultimodalServiceError(
                    "upstream_unreachable",
                    "暂时无法连接语音生成服务，请检查网络后重试。",
                    status_code=502,
                ) from exc
        self._raise_for_status(
            response,
            model_id=model_id,
            provider=provider,
        )
        if output_format == "wav":
            content = self._pcm_response_to_wav(response)
        else:
            self._validate_mp3(response)
            content = bytes(response.content)
        generation_id = str(
            response.headers.get("X-Generation-Id") or ""
        ).strip() or None
        return content, generation_id, output_format

    async def _verify_speech_model(
        self,
        target: OpenRouterTarget,
        model_id: str,
        *,
        provider: str = "openrouter",
    ) -> None:
        now = time.monotonic()
        cached = self._catalog_cache.get(target.cache_key)
        if cached and now - cached[0] <= self.catalog_cache_seconds:
            model_ids = cached[1]
        else:
            client_factory = (
                self._managed_client_factory
                if target.connection_id
                else self._client_factory
            )
            async with client_factory() as client:
                try:
                    response = await request_provider_url(
                        client,
                        self._egress_policy or ProviderEgressPolicy(),
                        target.connection_id if self._egress_policy else None,
                        "GET",
                        self._api_url(target.base_url, "models"),
                        headers=self._headers(
                            target.api_key,
                            provider=provider,
                        ),
                        params={"output_modalities": "speech"},
                    )
                except (
                    httpx.ConnectTimeout,
                    httpx.ReadTimeout,
                    httpx.WriteTimeout,
                ) as exc:
                    raise MultimodalServiceError(
                        "catalog_timeout",
                        "无法确认该模型的语音能力，请稍后重试。",
                        status_code=504,
                    ) from exc
                except httpx.HTTPError as exc:
                    raise MultimodalServiceError(
                        "catalog_unreachable",
                        "无法读取语音生成模型目录，请检查连接后重试。",
                        status_code=502,
                    ) from exc
            self._raise_for_status(
                response,
                model_id=model_id,
                provider=provider,
            )
            try:
                payload = response.json()
            except ValueError as exc:
                raise MultimodalServiceError(
                    "invalid_catalog",
                    "语音生成模型目录格式不兼容，请稍后重试。",
                    status_code=502,
                ) from exc
            rows = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                raise MultimodalServiceError(
                    "invalid_catalog",
                    "语音生成模型目录缺少模型列表，请稍后重试。",
                    status_code=502,
                )
            model_ids: set[str] = set()
            for item in rows:
                if not isinstance(item, dict):
                    continue
                item_id = str(item.get("id") or "").strip()
                if not item_id:
                    continue
                raw_outputs = item.get("output_modalities")
                if raw_outputs is None and isinstance(
                    item.get("architecture"), dict
                ):
                    raw_outputs = item["architecture"].get(
                        "output_modalities"
                    )
                if raw_outputs is None or "speech" in {
                    str(value)
                    for value in raw_outputs
                    if isinstance(value, str)
                }:
                    model_ids.add(item_id)
            self._catalog_cache[target.cache_key] = (now, model_ids)
        if model_id not in model_ids:
            raise MultimodalServiceError(
                "operation_mismatch",
                "所选模型不提供文字转语音能力，请选择标有“文字转语音”的模型。",
                status_code=422,
            )

    @staticmethod
    def _direct_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(connect=15, read=90, write=30, pool=10),
            follow_redirects=False,
            trust_env=False,
        )

    @staticmethod
    def _default_client() -> httpx.AsyncClient:
        timeout = httpx.Timeout(connect=15, read=90, write=30, pool=10)
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
    def _headers(
        api_key: str,
        *,
        provider: str = "openrouter",
    ) -> dict[str, str]:
        if provider == "openai":
            return {"Authorization": f"Bearer {api_key}"}
        title = os.getenv("OPENROUTER_APP_TITLE", "ModelMirror").strip()
        referer = os.getenv(
            "OPENROUTER_HTTP_REFERER", "http://localhost:5173"
        ).strip()
        return {
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": referer,
            "X-Title": title,
            "X-OpenRouter-Title": title,
        }

    @staticmethod
    def _raise_for_status(
        response: httpx.Response,
        *,
        model_id: str,
        provider: str = "openrouter",
    ) -> None:
        status = response.status_code
        if status < 400:
            return
        if status in {401, 403}:
            provider_label = "OpenAI" if provider == "openai" else "OpenRouter"
            raise MultimodalServiceError(
                "provider_credentials_invalid",
                f"{provider_label} 密钥无效或没有语音生成权限，请在模型服务连接中更新密钥。",
                status_code=502,
            )
        if status == 402:
            raise MultimodalServiceError(
                "provider_quota_exceeded",
                "OpenRouter 余额或预算不足，本次未生成语音。",
                status_code=402,
            )
        if status == 404:
            raise MultimodalServiceError(
                "model_unavailable",
                f"未找到语音模型 {model_id}，请刷新模型目录后重新选择。",
                status_code=422,
            )
        if status == 429:
            raise MultimodalServiceError(
                "provider_rate_limited",
                "语音生成服务请求过多，请稍后重试。",
                status_code=429,
            )
        if status >= 500:
            raise MultimodalServiceError(
                "provider_unavailable",
                "语音生成服务暂时不可用，请稍后重试。",
                status_code=502,
            )
        raise MultimodalServiceError(
            "provider_rejected_request",
            "语音生成请求未被接受，请检查模型、声线和文字长度。",
            status_code=422,
        )

    @staticmethod
    def _validate_mp3(response: httpx.Response) -> None:
        content = bytes(response.content)
        content_type = str(response.headers.get("content-type") or "")
        mime = content_type.split(";", 1)[0].strip().lower()
        if mime not in {"audio/mpeg", "audio/mp3"}:
            raise MultimodalServiceError(
                "invalid_audio_mime",
                "语音服务没有返回标准 MP3 音频，请稍后重试。",
                status_code=502,
            )
        if not content:
            raise MultimodalServiceError(
                "empty_speech",
                "语音服务没有返回音频，请稍后重试。",
                status_code=502,
            )
        if len(content) > MAX_SPEECH_BYTES:
            raise MultimodalServiceError(
                "speech_too_large",
                "生成的语音超过安全大小限制，请缩短文字后重试。",
                status_code=502,
            )
        is_mp3 = content.startswith(b"ID3") or (
            len(content) >= 2
            and content[0] == 0xFF
            and content[1] & 0xE0 == 0xE0
        )
        if not is_mp3:
            raise MultimodalServiceError(
                "invalid_speech_audio",
                "语音服务返回的 MP3 不完整或已损坏，请重新生成。",
                status_code=502,
            )

    @staticmethod
    def _pcm_response_to_wav(response: httpx.Response) -> bytes:
        content = bytes(response.content)
        content_type = str(response.headers.get("content-type") or "")
        parts = [part.strip() for part in content_type.split(";")]
        mime = parts[0].lower() if parts else ""
        parameters: dict[str, str] = {}
        for part in parts[1:]:
            key, separator, value = part.partition("=")
            if separator:
                parameters[key.strip().lower()] = value.strip()
        try:
            sample_rate = int(parameters.get("rate", ""))
            channels = int(parameters.get("channels", ""))
        except ValueError as exc:
            raise MultimodalServiceError(
                "invalid_audio_mime",
                "语音服务返回的 PCM 参数无效，请稍后重试。",
                status_code=502,
            ) from exc
        if mime != "audio/pcm" or sample_rate != 24_000 or channels != 1:
            raise MultimodalServiceError(
                "invalid_audio_mime",
                "语音服务没有返回已验证的 24 kHz 单声道 PCM，请稍后重试。",
                status_code=502,
            )
        if not content:
            raise MultimodalServiceError(
                "empty_speech",
                "语音服务没有返回音频，请稍后重试。",
                status_code=502,
            )
        if len(content) > MAX_SPEECH_BYTES or len(content) % 2:
            raise MultimodalServiceError(
                "invalid_speech_audio",
                "语音服务返回的 PCM 不完整或超过安全大小限制，请重新生成。",
                status_code=502,
            )
        bits_per_sample = 16
        block_align = channels * bits_per_sample // 8
        byte_rate = sample_rate * block_align
        header = b"".join(
            (
                b"RIFF",
                struct.pack("<I", 36 + len(content)),
                b"WAVE",
                b"fmt ",
                struct.pack(
                    "<IHHIIHH",
                    16,
                    1,
                    channels,
                    sample_rate,
                    byte_rate,
                    block_align,
                    bits_per_sample,
                ),
                b"data",
                struct.pack("<I", len(content)),
            )
        )
        return header + content


class SpeechService:
    def __init__(
        self,
        router_service: ModelRouterService,
        *,
        adapter: OpenRouterTtsAdapter | None = None,
        managed_gateway: ManagedMultimodalGateway | None = None,
    ) -> None:
        self.router_service = router_service
        self.adapter = adapter or OpenRouterTtsAdapter(
            egress_policy=router_service.egress_policy
        )
        self._managed_gateway = managed_gateway

    async def synthesize(
        self,
        *,
        model_id: str,
        text: str,
        voice: str,
        response_format: str | None,
        speed: float,
        idempotency_key: str | None = None,
        managed_entry_id: Literal[
            "multimodal_speech", "xpert_speech"
        ] = "multimodal_speech",
    ) -> SpeechResult:
        if self._managed_gateway is None:
            try:
                from server.model_router.multimodal_gateway import (
                    ManagedMultimodalGateway,
                )
            except ModuleNotFoundError:
                from model_router.multimodal_gateway import (
                    ManagedMultimodalGateway,
                )

            gateway = ManagedMultimodalGateway.for_router(self.router_service)
        else:
            gateway = self._managed_gateway
        managed = gateway.routing_mode(managed_entry_id) != "legacy"
        clean_model = (
            self._control_plane_model_id(model_id)
            if managed
            else self._model_id(model_id)
        )
        clean_text = self._text(text)
        clean_voice = (
            self._control_plane_voice(voice)
            if managed
            else self._voice(clean_model, voice)
        )
        clean_format = None
        if response_format is not None:
            clean_format = (
                self._control_plane_response_format(response_format)
                if managed
                else self._response_format(clean_model, response_format)
            )
        elif not managed:
            raise MultimodalServiceError(
                "unsupported_speech_format",
                "Legacy 语音调用必须指定输出格式。",
                status_code=422,
            )
        clean_speed = self._speed(speed)
        if managed:
            return await self._synthesize_managed(
                gateway,
                entry_id=managed_entry_id,
                model_id=clean_model,
                text=clean_text,
                voice=clean_voice,
                response_format=clean_format,
                speed=clean_speed,
                idempotency_key=idempotency_key,
            )
        provider = (
            "openai"
            if clean_model in OPENAI_SPEECH_PROFILES
            else "openrouter"
        )
        target = self._target(clean_model)
        decision_id = self._record_start(
            target,
            model_id=clean_model,
            input_bytes=len(clean_text.encode("utf-8")),
            provider=provider,
        )
        try:
            content, generation_id, actual_format = await self.adapter.synthesize(
                target,
                model_id=clean_model,
                text=clean_text,
                voice=clean_voice,
                speed=clean_speed,
                provider=provider,
            )
        except MultimodalServiceError as exc:
            self._record_failure(decision_id, exc.code)
            raise
        if actual_format != clean_format:
            self._record_failure(decision_id, "speech_format_mismatch")
            raise MultimodalServiceError(
                "speech_format_mismatch",
                "语音服务返回格式与请求不一致，请刷新模型能力后重试。",
                status_code=502,
            )
        self._record_success(decision_id, output_bytes=len(content))
        return SpeechResult(
            content=content,
            requested_model=clean_model,
            actual_model=clean_model,
            provider=provider,
            request_id=decision_id,
            generation_id=generation_id,
            output_bytes=len(content),
            response_format=actual_format,
        )

    async def _synthesize_managed(
        self,
        gateway: ManagedMultimodalGateway,
        *,
        entry_id: Literal["multimodal_speech", "xpert_speech"],
        model_id: str,
        text: str,
        voice: str,
        response_format: str | None,
        speed: float,
        idempotency_key: str | None,
    ) -> SpeechResult:
        try:
            from server.model_router.multimodal_gateway import (
                ManagedMultimodalError,
            )
        except ModuleNotFoundError:
            from model_router.multimodal_gateway import ManagedMultimodalError

        clean_key = str(idempotency_key or "").strip()
        if not clean_key or len(clean_key) > 200:
            raise MultimodalServiceError(
                "invalid_idempotency_key",
                "Managed 语音生成要求 1 至 200 个字符的 Idempotency-Key。",
                status_code=422,
                route_receipt=gateway.blocked_receipt(
                    entry_id, "invalid_idempotency_key"
                ),
            )
        try:
            exact_model = gateway.exact_model_id(
                entry_id,
                "audio_speech",
                requested_model=model_id,
            )
            policy = gateway.call_service.control.get_policy(entry_id)
            binding = next(
                (
                    item
                    for item in policy.bindings
                    if item.execution_shape == "audio_speech"
                    and item.model_id == exact_model
                    and item.valid
                ),
                None,
            )
            if binding is None:
                raise ManagedMultimodalError(
                    "provider_workload_binding_missing",
                    "语音 Binding 在派发前发生漂移。",
                    status_code=409,
                    receipt=gateway.blocked_receipt(
                        entry_id, "provider_workload_binding_missing"
                    ),
                )
            parameters = gateway.certified_audio_parameters(
                entry_id,
                certification_id=binding.certification_id,
                execution_shape="audio_speech",
            )
            certified_voice = str(parameters.get("certified_voice") or "")
            certified_response_format = str(
                parameters.get("certified_response_format") or ""
            )
            certified_upstream_format = str(
                parameters.get("certified_upstream_format") or ""
            )
            if voice != certified_voice:
                raise ManagedMultimodalError(
                    "provider_multimodal_speech_voice_not_certified",
                    "该声线未包含在当前 Provider 资格合同中。",
                    status_code=422,
                    receipt=gateway.blocked_receipt(
                        entry_id,
                        "provider_multimodal_speech_voice_not_certified",
                    ),
                )
            effective_response_format = (
                response_format or certified_response_format
            )
            if effective_response_format != certified_response_format:
                raise ManagedMultimodalError(
                    "provider_multimodal_speech_format_not_certified",
                    "该输出格式未包含在当前 Provider 资格合同中。",
                    status_code=422,
                    receipt=gateway.blocked_receipt(
                        entry_id,
                        "provider_multimodal_speech_format_not_certified",
                    ),
                )
            run = gateway.start_run(
                entry_id,
                parent_run_reference=(
                    "audio-speech:"
                    + hashlib.sha256(clean_key.encode("utf-8")).hexdigest()
                ),
                stable=True,
            )
            openrouter_adapter = (
                binding.adapter_contract == "openrouter_audio_speech_v1"
            )
            upstream_format = certified_upstream_format

            def parse_response(response: httpx.Response) -> SpeechResult:
                if effective_response_format == "wav" and openrouter_adapter:
                    content = self.adapter._pcm_response_to_wav(response)
                elif effective_response_format == "wav":
                    content = bytes(response.content)
                    if not (
                        len(content) >= 12
                        and content[:4] in {b"RIFF", b"RF64"}
                        and content[8:12] == b"WAVE"
                    ):
                        raise ManagedMultimodalError(
                            "invalid_speech_audio",
                            "语音 Provider 返回的 WAV 不完整或已损坏。",
                            status_code=502,
                        )
                else:
                    self.adapter._validate_mp3(response)
                    content = bytes(response.content)
                actual_model = str(
                    response.headers.get("x-model-id")
                    or response.headers.get("x-openrouter-model")
                    or ""
                ).strip()
                if actual_model and actual_model != exact_model:
                    raise ManagedMultimodalError(
                        "provider_workload_model_mismatch",
                        "语音 Provider 返回的实际模型与 Binding 不一致。",
                        status_code=502,
                    )
                return SpeechResult(
                    content=content,
                    requested_model=exact_model,
                    actual_model=actual_model,
                    provider="managed",
                    request_id="",
                    # The managed gateway consumes upstream generation metadata
                    # for the sanitized receipt before returning this result.
                    # Do not propagate the raw upstream identifier to callers.
                    generation_id=None,
                    output_bytes=len(content),
                    response_format=effective_response_format,
                    execution_mode="managed",
                )

            result, receipt = await run.complete_audio(
                execution_shape="audio_speech",
                logical_call_key="request",
                model_id=exact_model,
                expected_connection_id=binding.connection_id,
                expected_certification_id=binding.certification_id,
                expected_connection_fingerprint=binding.connection_fingerprint,
                expected_adapter_contract=binding.adapter_contract,
                expected_protocol_version=binding.protocol_version,
                payload={
                    "model": exact_model,
                    "input": text,
                    "voice": voice,
                    "response_format": upstream_format,
                    "speed": speed,
                },
                files=None,
                parse_response=parse_response,
            )
            provider_kind = next(
                (
                    item.provider_kind
                    for item in policy.bindings
                    if item.execution_shape == "audio_speech"
                    and item.model_id == exact_model
                ),
                None,
            )
            return replace(
                result,
                provider=str(provider_kind or "managed"),
                request_id=str(receipt.get("run_reference") or ""),
                provider_route_receipts=[receipt],
            )
        except ManagedMultimodalError as exc:
            raise MultimodalServiceError(
                exc.code,
                str(exc),
                status_code=exc.status_code,
                route_receipt=exc.receipt,
            ) from exc

    def _target(self, model_id: str) -> OpenRouterTarget:
        direct_openai = model_id in OPENAI_SPEECH_PROFILES
        connections = [
            item
            for item in self.router_service.list_connections()
            if item.kind == ("openai" if direct_openai else "openrouter")
            and item.enabled
            and item.health != "offline"
            and "audio" in item.scopes
        ]
        connections.sort(
            key=lambda item: (
                0 if item.health == "online" else 1,
                item.id,
            )
        )
        if connections:
            connection = connections[0]
            try:
                api_key = self.router_service.repository.resolve_api_key(
                    self.router_service.tenant_id,
                    connection.id,
                )
            except Exception as exc:
                provider_label = "OpenAI" if direct_openai else "OpenRouter"
                raise MultimodalServiceError(
                    "provider_credentials_unavailable",
                    f"无法读取 {provider_label} 连接密钥，请重新保存模型服务连接。",
                    status_code=503,
                ) from exc
            return OpenRouterTarget(
                base_url=connection.base_url,
                api_key=api_key,
                connection_id=connection.id,
                cache_key=f"connection:{connection.id}",
            )
        if direct_openai:
            raise MultimodalServiceError(
                "openai_audio_not_configured",
                "尚未配置 OpenAI 音频连接。请在“模型服务连接”中添加并测试 OpenAI。",
                status_code=503,
            )
        api_key = (
            os.getenv("MULTIMODAL_OPENROUTER_API_KEY", "").strip()
            or os.getenv("OPENROUTER_API_KEY", "").strip()
        )
        if not api_key:
            raise MultimodalServiceError(
                "openrouter_not_configured",
                "尚未配置 OpenRouter。请先在“模型服务连接”中添加并测试 OpenRouter。",
                status_code=503,
            )
        base_url = os.getenv(
            "MULTIMODAL_OPENROUTER_BASE_URL",
            "https://openrouter.ai/api/v1",
        ).strip()
        return OpenRouterTarget(
            base_url=base_url,
            api_key=api_key,
            connection_id=None,
            cache_key="environment:openrouter",
        )

    def _record_start(
        self,
        target: OpenRouterTarget,
        *,
        model_id: str,
        input_bytes: int,
        provider: str,
    ) -> str:
        record = getattr(
            self.router_service.repository,
            "record_routing_decision",
            None,
        )
        if not callable(record):
            raise MultimodalServiceError(
                "audit_unavailable",
                "暂时无法建立语音生成审计记录，请稍后重试。",
                status_code=503,
            )
        try:
            return str(
                record(
                    self.router_service.tenant_id,
                    session_id_hash=None,
                    engine=provider,
                    strategy="explicit",
                    operation="synthesize_speech",
                    connection_id=target.connection_id,
                    model_id=model_id,
                    reason_codes=[
                        "explicit_model",
                        "operation_synthesize_speech",
                    ],
                    input_bytes=input_bytes,
                )
            )
        except Exception as exc:
            raise MultimodalServiceError(
                "audit_unavailable",
                "暂时无法建立语音生成审计记录，请稍后重试。",
                status_code=503,
            ) from exc

    def _record_failure(self, decision_id: str, outcome: str) -> None:
        update = getattr(
            self.router_service.repository,
            "update_routing_decision_outcome",
            None,
        )
        if callable(update):
            try:
                update(self.router_service.tenant_id, decision_id, outcome)
            except Exception:
                logger.warning(
                    "Unable to update speech audit outcome: %s",
                    decision_id,
                )

    def _record_success(self, decision_id: str, *, output_bytes: int) -> None:
        update = getattr(
            self.router_service.repository,
            "update_routing_decision_usage",
            None,
        )
        if callable(update):
            try:
                update(
                    self.router_service.tenant_id,
                    decision_id,
                    outcome="success",
                    media_seconds=None,
                    settled_cost_usd=None,
                    cost_status="unavailable",
                    output_bytes=output_bytes,
                )
            except Exception:
                logger.warning(
                    "Unable to update speech audit usage: %s",
                    decision_id,
                )
            return
        self._record_failure(decision_id, "success")

    @staticmethod
    def _model_id(value: str) -> str:
        model_id = str(value or "").strip()
        if (
            model_id not in ALLOWED_SPEECH_PROFILES
            and model_id not in OPENAI_SPEECH_PROFILES
        ):
            raise MultimodalServiceError(
                "unsupported_speech_model",
                "该语音模型尚未完成行为验证，请从页面的可用模型中选择。",
                status_code=422,
            )
        return model_id

    @staticmethod
    def _control_plane_model_id(value: str) -> str:
        model_id = str(value or "").strip()
        if (
            not model_id
            or len(model_id) > 256
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", model_id)
        ):
            raise MultimodalServiceError(
                "unsupported_speech_model",
                "请选择控制面已认证的具体语音模型。",
                status_code=422,
            )
        return model_id

    @staticmethod
    def _control_plane_voice(value: str) -> str:
        voice = str(value or "").strip()
        if not voice or len(voice) > 128:
            raise MultimodalServiceError(
                "unsupported_voice",
                "语音声线为空或超过安全长度限制。",
                status_code=422,
            )
        return voice

    @staticmethod
    def _control_plane_response_format(value: str) -> str:
        response_format = str(value or "").strip().lower()
        if response_format not in {"mp3", "wav"}:
            raise MultimodalServiceError(
                "unsupported_speech_format",
                "Managed 语音仅支持 MP3 或 WAV 输出。",
                status_code=422,
            )
        return response_format

    @staticmethod
    def _text(value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise MultimodalServiceError(
                "empty_speech_input",
                "请输入需要生成语音的文字。",
                status_code=422,
            )
        if len(text) > MAX_SPEECH_INPUT_CHARS:
            raise MultimodalServiceError(
                "speech_input_too_long",
                "文字不能超过 4,000 个字符，请缩短后重试。",
                status_code=422,
            )
        return text

    @staticmethod
    def _voice(model_id: str, value: str) -> str:
        voice = str(value or "").strip()
        voices = (
            OPENAI_SPEECH_PROFILES.get(model_id)
            or ALLOWED_SPEECH_PROFILES[model_id]
        )
        if voice not in voices:
            raise MultimodalServiceError(
                "unsupported_voice",
                "该声线尚未完成行为验证，请使用当前页面提供的声线。",
                status_code=422,
            )
        return voice

    @staticmethod
    def _response_format(model_id: str, value: str) -> str:
        response_format = str(value or "").strip().lower()
        expected_format = speech_output_format(model_id)
        if response_format != expected_format:
            raise MultimodalServiceError(
                "unsupported_speech_format",
                f"该模型当前只支持 {expected_format.upper()} 输出。",
                status_code=422,
            )
        return response_format

    @staticmethod
    def _speed(value: float) -> float:
        if isinstance(value, bool):
            speed = 0.0
        else:
            try:
                speed = float(value)
            except (TypeError, ValueError):
                speed = 0.0
        if speed < 0.5 or speed > 2.0:
            raise MultimodalServiceError(
                "invalid_speech_speed",
                "语速应在 0.5 到 2.0 之间。",
                status_code=422,
            )
        return speed
