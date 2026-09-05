from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import logging
import os
import re
import tempfile
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import httpx
from pydantic import BaseModel, Field

try:
    from server.model_router.egress import ProviderEgressPolicy, stream_provider_url
    from server.model_router.service import ModelRouterService
except ModuleNotFoundError:
    from model_router.egress import ProviderEgressPolicy, stream_provider_url
    from model_router.service import ModelRouterService

if TYPE_CHECKING:
    try:
        from server.model_router.multimodal_gateway import (
            ManagedMultimodalChatDispatch,
            ManagedMultimodalError,
            ManagedMultimodalGateway,
        )
    except ModuleNotFoundError:
        from model_router.multimodal_gateway import (
            ManagedMultimodalChatDispatch,
            ManagedMultimodalError,
            ManagedMultimodalGateway,
        )

from .audio_catalog import AudioCatalogService, AudioChatProfile
from .stt import MultimodalServiceError, OpenRouterTarget


logger = logging.getLogger("modelmirror.multimodal")


def _managed_multimodal_gateway_types() -> tuple[type[Any], type[Exception]]:
    """Load Managed gateway types lazily to avoid the API import cycle."""

    try:
        from server.model_router.multimodal_gateway import (
            ManagedMultimodalError,
            ManagedMultimodalGateway,
        )
    except ModuleNotFoundError:
        from model_router.multimodal_gateway import (
            ManagedMultimodalError,
            ManagedMultimodalGateway,
        )
    return ManagedMultimodalGateway, ManagedMultimodalError

LYRIA_MODEL_IDS = {
    "google/lyria-3-clip-preview",
    "google/lyria-3-pro-preview",
}
MAX_AUDIO_GENERATION_PROMPT_CHARS = 4_000
MAX_AUDIO_JOB_IMAGE_BYTES = 10 * 1024 * 1024
MAX_GENERATED_AUDIO_BYTES = 25 * 1024 * 1024
MAX_AUDIO_JOB_SSE_EVENT_BYTES = 2 * 1024 * 1024
MAX_AUDIO_JOB_SSE_STREAM_BYTES = (
    MAX_GENERATED_AUDIO_BYTES * 4 // 3 + 4 * 1024 * 1024
)
MAX_IDEMPOTENCY_KEY_CHARS = 128
DEFAULT_AUDIO_JOB_TTL_SECONDS = 30 * 60
IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
AUDIO_OUTPUT_TEMP_PATTERN = re.compile(r"^[0-9a-f]{64}\.tmp-[0-9a-f]{32}$")
TERMINAL_AUDIO_JOB_STATUSES = {"succeeded", "failed", "expired"}

AUDIO_JOB_IMAGE_FORMATS: dict[
    str, tuple[str, tuple[str, ...]]
] = {
    "jpg": (
        "image/jpeg",
        ("image/jpeg", "image/jpg", "application/octet-stream"),
    ),
    "jpeg": (
        "image/jpeg",
        ("image/jpeg", "image/jpg", "application/octet-stream"),
    ),
    "png": ("image/png", ("image/png", "application/octet-stream")),
    "webp": ("image/webp", ("image/webp", "application/octet-stream")),
}

SAFE_AUDIO_JOB_ERRORS: dict[str, str] = {
    "audio_output_incomplete": (
        "音乐服务没有返回完整音频，请重新提交；本次不会交付损坏文件。"
    ),
    "audio_output_too_large": (
        "生成音频超过本地安全上限，请缩短创作要求后重新提交。"
    ),
    "provider_payment_required": (
        "当前模型服务余额不足，请充值或更换可用连接后重试。"
    ),
    "provider_rate_limited": (
        "音乐生成请求较多，请稍后重试；不要连续重复提交。"
    ),
    "provider_timeout": "音乐生成等待超时，请稍后重新提交。",
    "provider_unavailable": "音乐生成服务暂时不可用，请稍后重试。",
    "provider_rejected_request": (
        "音乐生成请求未被接受，请调整创作描述或图片后重试。"
    ),
    "worker_interrupted": (
        "服务重启中断了本地接收，请重新提交音乐生成任务。"
    ),
    "provider_result_uncertain": (
        "Provider 请求已经派发，但服务中断前无法确认结果；系统不会自动重放，"
        "请先在供应商侧核对后再决定是否创建新任务。"
    ),
    "audio_expired": "临时音频已过期，请重新生成后下载。",
    "generation_failed": "音乐生成未完成，请检查输入后重新提交。",
    "managed_audio_job_retained": (
        "Managed 音频任务必须保留幂等证据，暂不能从任务列表中硬删除。"
    ),
    "audio_output_persistence_failed": (
        "Provider 已返回完整音频，但本地安全落盘失败；系统不会自动重放。"
    ),
}


class AudioJobParameters(BaseModel):
    has_image: bool = False


class AudioJobUsage(BaseModel):
    cost_usd: float | None = None
    cost_kind: Literal[
        "actual", "estimated", "unavailable"
    ] = "unavailable"


class AudioJobError(BaseModel):
    code: str
    message: str


class AudioJob(BaseModel):
    job_id: str
    status: Literal["queued", "running", "succeeded", "failed", "expired"]
    requested_model: str
    actual_model: str | None = None
    provider: Literal["openrouter"] = "openrouter"
    generation_id: str | None = None
    parameters: AudioJobParameters
    usage: AudioJobUsage
    output_bytes: int = 0
    created_at: str
    updated_at: str
    expires_at: str | None = None
    error: AudioJobError | None = None
    execution_mode: Literal["managed", "legacy"] = "legacy"
    provider_route_receipts: list[dict[str, object]] = Field(
        default_factory=list
    )
    provider_dispatch_state: Literal[
        "not_dispatched", "dispatched", "confirmed", "uncertain"
    ] | None = None
    retry_allowed: bool = True
    fallback_reason_codes: list[str] = Field(default_factory=list)


class AudioJobList(BaseModel):
    jobs: list[AudioJob] = Field(default_factory=list)


class AudioJobDeleteResult(BaseModel):
    removed: bool
    upstream_cancelled: Literal[False] = False


@dataclass(frozen=True)
class AudioContent:
    chunks: AsyncIterator[bytes]
    media_type: str
    content_length: int


@dataclass(frozen=True)
class AudioGenerationResult:
    content: bytes
    actual_model: str
    generation_id: str | None
    cost_usd: float | None


@dataclass(frozen=True)
class AudioJobTask:
    job_id: str
    target: OpenRouterTarget | None
    model_id: str
    prompt: str
    image_data_url: str | None
    managed_dispatch: ManagedMultimodalChatDispatch | None = None


@dataclass(frozen=True)
class AudioJobLaunch:
    job: AudioJob
    task: AudioJobTask | None


class OpenRouterAudioJobAdapter:
    def __init__(
        self,
        *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        egress_policy: ProviderEgressPolicy | None = None,
    ) -> None:
        self._client_factory = client_factory or self._default_client
        self._egress_policy = egress_policy

    async def generate(
        self,
        target: OpenRouterTarget,
        *,
        model_id: str,
        prompt: str,
        image_data_url: str | None,
    ) -> AudioGenerationResult:
        payload = self._payload(
            model_id=model_id,
            prompt=prompt,
            image_data_url=image_data_url,
        )
        async with self._client_factory() as client:
            try:
                async with stream_provider_url(
                    client,
                    self._egress_policy or ProviderEgressPolicy(),
                    target.connection_id if self._egress_policy else None,
                    "POST",
                    self._api_url(target.base_url),
                    headers=self._headers(target.api_key),
                    json=payload,
                ) as response:
                    await self._raise_for_status(response)
                    return await self._consume_response(
                        response,
                        requested_model=model_id,
                        require_observed_model=False,
                    )
            except MultimodalServiceError:
                raise
            except (
                httpx.ConnectTimeout,
                httpx.ReadTimeout,
                httpx.WriteTimeout,
                httpx.PoolTimeout,
            ) as exc:
                raise MultimodalServiceError(
                    "provider_timeout",
                    SAFE_AUDIO_JOB_ERRORS["provider_timeout"],
                    status_code=504,
                ) from exc
            except httpx.HTTPError as exc:
                raise MultimodalServiceError(
                    "provider_unavailable",
                    SAFE_AUDIO_JOB_ERRORS["provider_unavailable"],
                    status_code=502,
                ) from exc

    async def generate_managed(
        self,
        dispatch: ManagedMultimodalChatDispatch,
        *,
        model_id: str,
        prompt: str,
        on_dispatched: Callable[[], None] | None = None,
    ) -> AudioGenerationResult:
        """Send exactly one qualified POST through the R8D dispatch guard."""

        payload = self._payload(
            model_id=model_id,
            prompt=prompt,
            image_data_url=None,
        )
        async with self._client_factory() as client:
            response: httpx.Response | None = None
            try:
                response = await dispatch.send(
                    client,
                    payload,
                    headers={
                        "Accept": "text/event-stream",
                        "HTTP-Referer": "http://localhost",
                        "X-Title": "ModelMirror audio generation",
                    },
                    on_dispatched=on_dispatched,
                )
                await self._raise_for_status(response)
                return await self._consume_response(
                    response,
                    requested_model=model_id,
                    require_observed_model=True,
                )
            except MultimodalServiceError:
                raise
            except (
                httpx.ConnectTimeout,
                httpx.ReadTimeout,
                httpx.WriteTimeout,
                httpx.PoolTimeout,
            ) as exc:
                raise MultimodalServiceError(
                    "provider_result_uncertain",
                    SAFE_AUDIO_JOB_ERRORS["provider_result_uncertain"],
                    status_code=504,
                ) from exc
            except httpx.HTTPError as exc:
                raise MultimodalServiceError(
                    "provider_result_uncertain",
                    SAFE_AUDIO_JOB_ERRORS["provider_result_uncertain"],
                    status_code=502,
                ) from exc
            finally:
                if response is not None:
                    await response.aclose()

    @staticmethod
    def _payload(
        *,
        model_id: str,
        prompt: str,
        image_data_url: str | None,
    ) -> dict[str, object]:
        content: str | list[dict[str, object]] = prompt
        if image_data_url:
            content = [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": image_data_url},
                },
            ]
        return {
            "model": model_id,
            "stream": True,
            "messages": [{"role": "user", "content": content}],
        }

    async def _consume_response(
        self,
        response: httpx.Response,
        *,
        requested_model: str,
        require_observed_model: bool,
    ) -> AudioGenerationResult:
        encoded_parts: list[str] = []
        encoded_length = 0
        actual_model: str | None = None
        generation_id = response.headers.get("x-generation-id") or None
        finish_reason: str | None = None
        usage: object = None
        saw_done = False

        async for event in self._iter_sse_events(response):
            data_lines = [
                line[5:].lstrip()
                for line in event.split("\n")
                if line.startswith("data:")
            ]
            if not data_lines:
                continue
            raw = "\n".join(data_lines)
            if saw_done:
                raise self._incomplete_error()
            if raw == "[DONE]":
                saw_done = True
                continue
            try:
                item = json.loads(raw)
            except ValueError as exc:
                raise self._incomplete_error() from exc
            if not isinstance(item, dict):
                raise self._incomplete_error()
            if isinstance(item.get("error"), dict):
                raise MultimodalServiceError(
                    "provider_unavailable",
                    SAFE_AUDIO_JOB_ERRORS["provider_unavailable"],
                    status_code=502,
                )
            if isinstance(item.get("model"), str) and item["model"].strip():
                observed_model = item["model"].strip()
                if require_observed_model and (
                    observed_model != requested_model
                    or (
                        actual_model is not None
                        and observed_model != actual_model
                    )
                ):
                    raise MultimodalServiceError(
                        "provider_workload_model_mismatch",
                        "音乐 Provider 返回的实际模型与 Binding 不一致。",
                        status_code=502,
                    )
                actual_model = observed_model
            if isinstance(item.get("id"), str):
                generation_id = generation_id or item["id"]
            if isinstance(item.get("usage"), dict):
                usage = item["usage"]
            choices = item.get("choices")
            if not isinstance(choices, list) or not choices:
                continue
            choice = choices[0]
            if not isinstance(choice, dict):
                continue
            if isinstance(choice.get("finish_reason"), str):
                finish_reason = choice["finish_reason"]
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                continue
            audio = delta.get("audio")
            if not isinstance(audio, dict):
                continue
            data = audio.get("data")
            if not isinstance(data, str) or not data:
                continue
            encoded_length += len(data)
            if encoded_length > (MAX_GENERATED_AUDIO_BYTES * 4 // 3 + 16):
                raise MultimodalServiceError(
                    "audio_output_too_large",
                    SAFE_AUDIO_JOB_ERRORS["audio_output_too_large"],
                    status_code=502,
                )
            encoded_parts.append(data)

        if not saw_done:
            if require_observed_model:
                raise MultimodalServiceError(
                    "provider_result_uncertain",
                    SAFE_AUDIO_JOB_ERRORS["provider_result_uncertain"],
                    status_code=502,
                )
            raise self._incomplete_error()
        if finish_reason != "stop" or not encoded_parts:
            raise self._incomplete_error()
        if require_observed_model and not actual_model:
            raise MultimodalServiceError(
                "provider_workload_actual_model_unverified",
                "音乐 Provider 未返回可验证的实际模型。",
                status_code=502,
            )
        if (
            require_observed_model
            and actual_model
            and actual_model != requested_model
        ):
            raise MultimodalServiceError(
                "provider_workload_model_mismatch",
                "音乐 Provider 返回的实际模型与 Binding 不一致。",
                status_code=502,
            )
        try:
            audio_bytes = b"".join(
                base64.b64decode(part, validate=True)
                for part in encoded_parts
            )
        except (binascii.Error, ValueError):
            try:
                audio_bytes = base64.b64decode(
                    "".join(encoded_parts), validate=True
                )
            except (binascii.Error, ValueError) as exc:
                raise self._incomplete_error() from exc
        if (
            len(audio_bytes) < 1_024
            or len(audio_bytes) > MAX_GENERATED_AUDIO_BYTES
            or not self._is_mp3(audio_bytes)
        ):
            raise self._incomplete_error()
        return AudioGenerationResult(
            content=audio_bytes,
            actual_model=actual_model or requested_model,
            generation_id=generation_id,
            cost_usd=self._cost(usage),
        )

    @staticmethod
    async def _iter_sse_events(
        response: httpx.Response,
    ) -> AsyncIterator[str]:
        buffer = b""
        total_bytes = 0
        async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
            total_bytes += len(chunk)
            if total_bytes > MAX_AUDIO_JOB_SSE_STREAM_BYTES:
                raise MultimodalServiceError(
                    "audio_output_too_large",
                    SAFE_AUDIO_JOB_ERRORS["audio_output_too_large"],
                    status_code=502,
                )
            buffer += chunk
            while True:
                delimiters = [
                    (index, delimiter)
                    for delimiter in (b"\n\n", b"\r\n\r\n", b"\r\r")
                    if (index := buffer.find(delimiter)) >= 0
                ]
                if not delimiters:
                    break
                index, delimiter = min(delimiters, key=lambda item: item[0])
                event_bytes = buffer[:index]
                buffer = buffer[index + len(delimiter) :]
                if len(event_bytes) > MAX_AUDIO_JOB_SSE_EVENT_BYTES:
                    raise MultimodalServiceError(
                        "audio_output_too_large",
                        SAFE_AUDIO_JOB_ERRORS["audio_output_too_large"],
                        status_code=502,
                    )
                try:
                    yield event_bytes.decode("utf-8").replace(
                        "\r\n", "\n"
                    ).replace("\r", "\n")
                except UnicodeDecodeError as exc:
                    raise OpenRouterAudioJobAdapter._incomplete_error() from exc
            if len(buffer) > MAX_AUDIO_JOB_SSE_EVENT_BYTES:
                raise MultimodalServiceError(
                    "audio_output_too_large",
                    SAFE_AUDIO_JOB_ERRORS["audio_output_too_large"],
                    status_code=502,
                )
        if buffer.strip():
            try:
                yield buffer.decode("utf-8").replace("\r\n", "\n").replace(
                    "\r", "\n"
                )
            except UnicodeDecodeError as exc:
                raise OpenRouterAudioJobAdapter._incomplete_error() from exc

    @staticmethod
    def _default_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(180.0, connect=15.0),
            follow_redirects=False,
            trust_env=False,
        )

    @staticmethod
    def _api_url(base_url: str) -> str:
        return f"{base_url.rstrip('/')}/chat/completions"

    @staticmethod
    def _headers(api_key: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "HTTP-Referer": "http://localhost",
            "X-Title": "ModelMirror audio generation",
        }

    @staticmethod
    async def _raise_for_status(response: httpx.Response) -> None:
        status = response.status_code
        if status < 400:
            return
        if status == 401:
            raise MultimodalServiceError(
                "provider_unauthorized",
                "音乐模型服务密钥无效，请在设置中重新测试连接。",
                status_code=401,
            )
        if status == 402:
            raise MultimodalServiceError(
                "provider_payment_required",
                SAFE_AUDIO_JOB_ERRORS["provider_payment_required"],
                status_code=402,
            )
        if status == 429:
            raise MultimodalServiceError(
                "provider_rate_limited",
                SAFE_AUDIO_JOB_ERRORS["provider_rate_limited"],
                status_code=429,
            )
        if status >= 500:
            raise MultimodalServiceError(
                "provider_unavailable",
                SAFE_AUDIO_JOB_ERRORS["provider_unavailable"],
                status_code=502,
            )
        raise MultimodalServiceError(
            "provider_rejected_request",
            SAFE_AUDIO_JOB_ERRORS["provider_rejected_request"],
            status_code=422,
        )

    @staticmethod
    def _cost(usage: object) -> float | None:
        if not isinstance(usage, dict):
            return None
        value = usage.get("cost")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return max(0.0, float(value))
        return None

    @classmethod
    def _is_mp3(cls, content: bytes) -> bool:
        audio_start = cls._id3v2_audio_start(content)
        if audio_start is None:
            return False

        audio_end = len(content)
        saw_id3v1 = False
        saw_apev2 = False
        while True:
            if (
                audio_end - audio_start >= 128
                and content[audio_end - 128 : audio_end - 125] == b"TAG"
            ):
                if saw_id3v1:
                    return False
                saw_id3v1 = True
                audio_end -= 128
                continue

            if (
                audio_end - audio_start >= 32
                and content[audio_end - 32 : audio_end - 24]
                == b"APETAGEX"
            ):
                if saw_apev2:
                    return False
                ape_start = cls._apev2_start(
                    content,
                    audio_start=audio_start,
                    tag_end=audio_end,
                )
                if ape_start is None:
                    return False
                saw_apev2 = True
                audio_end = ape_start
                continue
            break

        return cls._has_complete_layer3_frames(
            content,
            audio_start=audio_start,
            audio_end=audio_end,
        )

    @staticmethod
    def _id3v2_audio_start(content: bytes) -> int | None:
        if not content.startswith(b"ID3"):
            return 0
        if len(content) < 10:
            return None

        major_version = content[3]
        revision = content[4]
        flags = content[5]
        allowed_flag_masks = {2: 0xC0, 3: 0xE0, 4: 0xF0}
        allowed_flags = allowed_flag_masks.get(major_version)
        if (
            allowed_flags is None
            or revision == 0xFF
            or flags & ~allowed_flags
        ):
            return None

        size_bytes = content[6:10]
        if any(value & 0x80 for value in size_bytes):
            return None
        tag_size = (
            (size_bytes[0] << 21)
            | (size_bytes[1] << 14)
            | (size_bytes[2] << 7)
            | size_bytes[3]
        )
        has_footer = major_version == 4 and bool(flags & 0x10)
        audio_start = 10 + tag_size + (10 if has_footer else 0)
        if audio_start > len(content):
            return None
        if has_footer:
            footer = content[audio_start - 10 : audio_start]
            if (
                footer[:3] != b"3DI"
                or footer[3:6] != content[3:6]
                or footer[6:10] != size_bytes
            ):
                return None
        return audio_start

    @staticmethod
    def _apev2_start(
        content: bytes,
        *,
        audio_start: int,
        tag_end: int,
    ) -> int | None:
        footer_start = tag_end - 32
        footer = content[footer_start:tag_end]
        if len(footer) != 32 or footer[:8] != b"APETAGEX":
            return None

        version = int.from_bytes(footer[8:12], "little")
        tag_size = int.from_bytes(footer[12:16], "little")
        item_count = int.from_bytes(footer[16:20], "little")
        flags = int.from_bytes(footer[20:24], "little")
        if (
            version != 2_000
            or tag_size < 32
            or tag_size > tag_end - audio_start
            or footer[24:32] != b"\x00" * 8
            or flags & 0x1FFFFFFF
            or flags & 0x60000000
        ):
            return None

        items_start = tag_end - tag_size
        tag_start = items_start
        contains_header = bool(flags & 0x80000000)
        if contains_header:
            tag_start = items_start - 32
            if tag_start < audio_start:
                return None
            header = content[tag_start:items_start]
            header_flags = int.from_bytes(header[20:24], "little")
            if (
                header[:8] != b"APETAGEX"
                or header[8:20] != footer[8:20]
                or header_flags != (flags | 0x20000000)
                or header[24:32] != b"\x00" * 8
            ):
                return None

        cursor = items_start
        if item_count > (footer_start - items_start) // 11:
            return None
        seen_keys: set[bytes] = set()
        for _ in range(item_count):
            if cursor + 8 > footer_start:
                return None
            value_size = int.from_bytes(
                content[cursor : cursor + 4], "little"
            )
            item_flags = int.from_bytes(
                content[cursor + 4 : cursor + 8], "little"
            )
            if item_flags & ~0x7:
                return None
            key_start = cursor + 8
            key_end = content.find(b"\x00", key_start, footer_start)
            if key_end < 0:
                return None
            key = content[key_start:key_end]
            normalized_key = key.lower()
            if (
                not 2 <= len(key) <= 255
                or b"=" in key
                or any(value < 0x20 or value > 0x7E for value in key)
                or normalized_key in seen_keys
            ):
                return None
            seen_keys.add(normalized_key)
            cursor = key_end + 1 + value_size
            if cursor > footer_start:
                return None
        if cursor != footer_start:
            return None
        return tag_start

    @staticmethod
    def _has_complete_layer3_frames(
        content: bytes,
        *,
        audio_start: int,
        audio_end: int,
    ) -> bool:
        mpeg1_bitrates = (
            0,
            32,
            40,
            48,
            56,
            64,
            80,
            96,
            112,
            128,
            160,
            192,
            224,
            256,
            320,
            0,
        )
        mpeg2_bitrates = (
            0,
            8,
            16,
            24,
            32,
            40,
            48,
            56,
            64,
            80,
            96,
            112,
            128,
            144,
            160,
            0,
        )
        sample_rates = {
            0b11: (44_100, 48_000, 32_000),
            0b10: (22_050, 24_000, 16_000),
            0b00: (11_025, 12_000, 8_000),
        }

        cursor = audio_start
        frame_count = 0
        stream_signature: tuple[int, int] | None = None
        while cursor < audio_end:
            if audio_end - cursor < 4:
                return False
            header = int.from_bytes(content[cursor : cursor + 4], "big")
            version_bits = (header >> 19) & 0x3
            layer_bits = (header >> 17) & 0x3
            bitrate_index = (header >> 12) & 0xF
            sample_rate_index = (header >> 10) & 0x3
            padding = (header >> 9) & 0x1
            emphasis = header & 0x3
            if (
                header >> 21 != 0x7FF
                or version_bits == 0b01
                or layer_bits != 0b01
                or bitrate_index in {0, 0xF}
                or sample_rate_index == 0b11
                or emphasis == 0b10
            ):
                return False

            rates = sample_rates.get(version_bits)
            if rates is None:
                return False
            sample_rate = rates[sample_rate_index]
            bitrate_kbps = (
                mpeg1_bitrates[bitrate_index]
                if version_bits == 0b11
                else mpeg2_bitrates[bitrate_index]
            )
            coefficient = 144_000 if version_bits == 0b11 else 72_000
            frame_length = (
                coefficient * bitrate_kbps // sample_rate + padding
            )
            has_crc = not bool((header >> 16) & 0x1)
            channel_mode = (header >> 6) & 0x3
            side_info_size = (
                (17 if channel_mode == 0b11 else 32)
                if version_bits == 0b11
                else (9 if channel_mode == 0b11 else 17)
            )
            minimum_frame_length = 4 + (2 if has_crc else 0) + side_info_size
            if (
                frame_length < minimum_frame_length
                or cursor + frame_length > audio_end
            ):
                return False

            signature = (version_bits, sample_rate)
            if stream_signature is None:
                stream_signature = signature
            elif signature != stream_signature:
                return False
            cursor += frame_length
            frame_count += 1

        return cursor == audio_end and frame_count >= 2

    @staticmethod
    def _incomplete_error() -> MultimodalServiceError:
        return MultimodalServiceError(
            "audio_output_incomplete",
            SAFE_AUDIO_JOB_ERRORS["audio_output_incomplete"],
            status_code=502,
        )


def is_complete_mp3(content: bytes) -> bool:
    """Return whether content is one complete, bounded Layer III stream."""

    return OpenRouterAudioJobAdapter._is_mp3(content)


class AudioJobService:
    def __init__(
        self,
        router_service: ModelRouterService,
        catalog_service: AudioCatalogService,
        *,
        adapter: OpenRouterAudioJobAdapter | None = None,
        managed_gateway: ManagedMultimodalGateway | None = None,
        output_dir: str | Path | None = None,
        ttl_seconds: int = DEFAULT_AUDIO_JOB_TTL_SECONDS,
    ) -> None:
        self.router_service = router_service
        self.catalog_service = catalog_service
        self.adapter = adapter or OpenRouterAudioJobAdapter(
            egress_policy=router_service.egress_policy
        )
        if managed_gateway is None:
            managed_gateway_type, _ = _managed_multimodal_gateway_types()
            self.managed_gateway = managed_gateway_type.for_router(router_service)
        else:
            self.managed_gateway = managed_gateway
        self.output_dir = Path(
            output_dir
            or Path(tempfile.gettempdir()) / "modelmirror-audio-jobs"
        )
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = max(60, min(int(ttl_seconds), 24 * 60 * 60))

    async def create(
        self,
        *,
        model_id: str,
        prompt: str,
        idempotency_key: str,
        image_filename: str | None = None,
        image_content_type: str | None = None,
        image_content: bytes | None = None,
    ) -> AudioJobLaunch:
        self._ensure_enabled()
        clean_model = self._model_id(model_id)
        clean_prompt = self._prompt(prompt)
        clean_key = self._idempotency_key(idempotency_key)
        image_data_url = self._image(
            image_filename,
            image_content_type,
            image_content,
        )
        if self.managed_gateway.routing_mode("audio_generation") != "legacy":
            return await self._create_managed(
                model_id=clean_model,
                prompt=clean_prompt,
                idempotency_key=clean_key,
                image_data_url=image_data_url,
            )
        profile = await self._profile(
            clean_model, has_image=image_data_url is not None
        )
        target = self.catalog_service.resolve_target()
        tenant_id = self.router_service.tenant_id
        key_hash = hashlib.sha256(
            f"{tenant_id}\0{clean_key}".encode("utf-8")
        ).hexdigest()
        existing = (
            self.router_service.repository
            .get_audio_job_by_idempotency_hash(tenant_id, key_hash)
        )
        if existing is not None:
            return AudioJobLaunch(job=self._public(existing), task=None)

        job_id = f"audio_{uuid.uuid4().hex}"
        row, created = (
            self.router_service.repository.create_audio_job_if_absent(
                tenant_id,
                job_id=job_id,
                idempotency_key_hash=key_hash,
                connection_id=target.connection_id,
                requested_model=clean_model,
                provider="openrouter",
                has_image=image_data_url is not None,
                cost_usd=profile.price_per_generation_usd,
                cost_kind=(
                    "estimated"
                    if profile.price_per_generation_usd is not None
                    else "unavailable"
                ),
            )
        )
        if not created:
            return AudioJobLaunch(job=self._public(row), task=None)
        try:
            decision_id = self._record_start(
                target,
                model_id=clean_model,
                input_bytes=(
                    len(clean_prompt.encode("utf-8"))
                    + len(image_content or b"")
                ),
            )
        except MultimodalServiceError as exc:
            self._update(
                job_id,
                status="failed",
                error_code=exc.code,
            )
            raise
        row = self._update(job_id, decision_id=decision_id)
        return AudioJobLaunch(
            job=self._public(row),
            task=AudioJobTask(
                job_id=job_id,
                target=target,
                model_id=clean_model,
                prompt=clean_prompt,
                image_data_url=image_data_url,
            ),
        )

    async def _create_managed(
        self,
        *,
        model_id: str,
        prompt: str,
        idempotency_key: str,
        image_data_url: str | None,
    ) -> AudioJobLaunch:
        _, managed_error_type = _managed_multimodal_gateway_types()
        entry_id = "audio_generation"
        execution_shape = "audio_generation_stream"
        try:
            exact_model = self.managed_gateway.exact_model_id(
                entry_id,
                execution_shape,
                requested_model=model_id,
            )
            policy = self.managed_gateway.call_service.control.get_policy(
                entry_id
            )
            binding = next(
                (
                    item
                    for item in policy.bindings
                    if item.execution_shape == execution_shape
                    and item.model_id == exact_model
                    and item.valid
                ),
                None,
            )
            if binding is None:
                raise managed_error_type(
                    "provider_workload_binding_missing",
                    "音乐生成缺少当前精确模型的合格 Managed Binding。",
                    status_code=409,
                    receipt=self.managed_gateway.blocked_receipt(
                        entry_id, "provider_workload_binding_missing"
                    ),
                )
            profile = self.managed_gateway.certified_audio_parameters(
                entry_id,
                certification_id=binding.certification_id,
                execution_shape=execution_shape,
            )
            if image_data_url is not None and not bool(
                profile.get("supports_image_prompt")
            ):
                raise managed_error_type(
                    "provider_multimodal_audio_image_prompt_unsupported",
                    "当前 Managed 音频生成资格不支持图片提示。",
                    status_code=422,
                    receipt=self.managed_gateway.blocked_receipt(
                        entry_id,
                        "provider_multimodal_audio_image_prompt_unsupported",
                    ),
                )
        except managed_error_type as exc:
            raise self._managed_error(exc) from exc

        tenant_id = self.router_service.tenant_id
        key_hash = hashlib.sha256(
            f"{tenant_id}\0{idempotency_key}".encode("utf-8")
        ).hexdigest()
        existing = self.router_service.repository.get_audio_job_by_idempotency_hash(
            tenant_id, key_hash
        )
        if existing is not None:
            return AudioJobLaunch(job=self._public(existing), task=None)

        job_id = f"audio_{uuid.uuid4().hex}"
        row, created = self.router_service.repository.create_audio_job_if_absent(
            tenant_id,
            job_id=job_id,
            idempotency_key_hash=key_hash,
            connection_id=binding.connection_id,
            requested_model=exact_model,
            provider="openrouter",
            has_image=image_data_url is not None,
            cost_kind="unavailable",
            workload_run_id=f"managed-reservation:{job_id}",
        )
        if not created:
            return AudioJobLaunch(job=self._public(row), task=None)

        try:
            dispatch = await self.managed_gateway.prepare_chat_dispatch(
                entry_id,
                execution_shape=execution_shape,
                requested_model=exact_model,
                parent_run_reference=f"audio-job:{job_id}",
            )
        except managed_error_type as exc:
            self._update(
                job_id,
                status="failed",
                error_code=exc.code,
                provider_dispatch_state="not_dispatched",
                post_dispatched=False,
                provider_terminal_status="failed",
            )
            raise self._managed_error(exc) from exc

        prepared = dispatch.prepared
        row = self._update(
            job_id,
            workload_run_id=prepared.run_id,
            workload_call_id=prepared.call_id,
            policy_fingerprint=prepared.policy_fingerprint,
            connection_fingerprint=prepared.connection_fingerprint,
            adapter_contract=prepared.adapter_contract,
            protocol_version=prepared.protocol_version,
            provider_dispatch_state="not_dispatched",
            post_dispatched=False,
        )
        if row is None:
            raise MultimodalServiceError(
                "audit_unavailable",
                "暂时无法建立音乐生成审计记录，请稍后重试。",
                status_code=503,
                route_receipt=dispatch.run.finish_failure(
                    "provider_multimodal_audio_job_link_failed"
                ),
            )
        return AudioJobLaunch(
            job=self._public(row),
            task=AudioJobTask(
                job_id=job_id,
                target=None,
                model_id=exact_model,
                prompt=prompt,
                image_data_url=None,
                managed_dispatch=dispatch,
            ),
        )

    async def run(self, task: AudioJobTask) -> None:
        row = self._update(task.job_id, status="running", error_code=None)
        if row is None:
            return
        decision_id = str(row.get("decision_id") or "")
        dispatch = task.managed_dispatch
        try:
            if dispatch is not None:
                result = await self.adapter.generate_managed(
                    dispatch,
                    model_id=task.model_id,
                    prompt=task.prompt,
                    on_dispatched=lambda: self._update(
                        task.job_id,
                        provider_dispatch_state="dispatched",
                        post_dispatched=True,
                    ),
                )
            else:
                if task.target is None:
                    raise MultimodalServiceError(
                        "provider_unavailable",
                        SAFE_AUDIO_JOB_ERRORS["provider_unavailable"],
                        status_code=502,
                    )
                result = await self.adapter.generate(
                    task.target,
                    model_id=task.model_id,
                    prompt=task.prompt,
                    image_data_url=task.image_data_url,
                )
            try:
                await self._write_output_cancellation_safe(
                    task.job_id, result.content
                )
            except Exception as exc:
                if dispatch is None:
                    raise
                if not dispatch.completed:
                    dispatch.complete(
                        status="failed",
                        result_class="local_persistence_error",
                        error_code="audio_output_persistence_failed",
                        actual_model=result.actual_model,
                    )
                self._update(
                    task.job_id,
                    status="failed",
                    actual_model=result.actual_model,
                    generation_id=result.generation_id,
                    output_bytes=0,
                    error_code="audio_output_persistence_failed",
                    provider_dispatch_state="confirmed",
                    post_dispatched=True,
                    provider_terminal_status="failed",
                )
                logger.warning(
                    "Managed audio output persistence failed job=%s",
                    task.job_id,
                )
                return
            expires_at = (
                datetime.now(UTC) + timedelta(seconds=self.ttl_seconds)
            ).isoformat()
            changes: dict[str, object] = {
                "status": "running" if dispatch is not None else "succeeded",
                "actual_model": result.actual_model,
                "generation_id": result.generation_id,
                "output_bytes": len(result.content),
                "error_code": None,
                "expires_at": expires_at,
            }
            if dispatch is not None:
                changes.update(
                    provider_dispatch_state="dispatched",
                    post_dispatched=True,
                )
            if result.cost_usd is not None:
                changes["cost_usd"] = result.cost_usd
                changes["cost_kind"] = "actual"
            row = self._update(task.job_id, **changes)
            if row is None:
                await asyncio.to_thread(self._remove_output, task.job_id)
                raise RuntimeError("audio_job_finalize_store_missing")
            if dispatch is not None:
                dispatch.complete(
                    status="passed",
                    result_class="success",
                    actual_model=result.actual_model,
                )
                row = self._update(
                    task.job_id,
                    status="succeeded",
                    provider_dispatch_state="confirmed",
                    post_dispatched=True,
                    provider_terminal_status="passed",
                )
                if row is None:
                    raise RuntimeError("audio_job_finalize_store_missing")
            self._record_success(
                decision_id,
                output_bytes=len(result.content),
                cost_usd=result.cost_usd,
            )
        except asyncio.CancelledError:
            await asyncio.to_thread(self._remove_output, task.job_id)
            if dispatch is not None:
                status = "uncertain" if dispatch.dispatched else "cancelled"
                code = (
                    "provider_result_uncertain"
                    if dispatch.dispatched
                    else "provider_workload_call_cancelled"
                )
                if not dispatch.completed:
                    dispatch.complete(
                        status=status,
                        result_class="client_cancelled",
                        error_code=code,
                    )
                self._update(
                    task.job_id,
                    status="failed",
                    error_code=code,
                    provider_dispatch_state=(
                        "uncertain" if dispatch.dispatched else "not_dispatched"
                    ),
                    post_dispatched=dispatch.dispatched,
                    provider_terminal_status=status,
                )
            else:
                self._update(
                    task.job_id,
                    status="failed",
                    error_code="worker_interrupted",
                )
                self._record_failure(decision_id, "worker_interrupted")
            raise
        except MultimodalServiceError as exc:
            await asyncio.to_thread(self._remove_output, task.job_id)
            if dispatch is not None:
                uncertain = (
                    dispatch.dispatched
                    and exc.code == "provider_result_uncertain"
                )
                call_status = "uncertain" if uncertain else "failed"
                if not dispatch.completed:
                    dispatch.complete(
                        status=call_status,
                        result_class=(
                            "transport_error" if uncertain else "provider_error"
                        ),
                        error_code=exc.code,
                    )
                self._update(
                    task.job_id,
                    status="failed",
                    error_code=exc.code,
                    provider_dispatch_state=(
                        "uncertain"
                        if uncertain
                        else "confirmed"
                        if dispatch.dispatched
                        else "not_dispatched"
                    ),
                    post_dispatched=dispatch.dispatched,
                    provider_terminal_status=call_status,
                )
            else:
                self._update(
                    task.job_id,
                    status="failed",
                    error_code=exc.code,
                )
                self._record_failure(decision_id, exc.code)
        except Exception as exc:
            if dispatch is not None and dispatch.completed:
                logger.warning(
                    "Managed audio job final status reconciliation is pending "
                    "job=%s code=job_store_unavailable",
                    task.job_id,
                )
                return
            logger.exception(
                "Audio job failed without exposing request content: %s",
                task.job_id,
            )
            await asyncio.to_thread(self._remove_output, task.job_id)
            if dispatch is not None:
                uncertain = dispatch.dispatched and not dispatch.completed
                code = (
                    "provider_result_uncertain"
                    if uncertain
                    else "generation_failed"
                )
                if not dispatch.completed:
                    dispatch.complete(
                        status="uncertain" if uncertain else "failed",
                        result_class=(
                            "transport_error" if uncertain else "local_failure"
                        ),
                        error_code=code,
                    )
                self._update(
                    task.job_id,
                    status="failed",
                    error_code=code,
                    provider_dispatch_state=(
                        "uncertain"
                        if uncertain
                        else "confirmed"
                        if dispatch.completed
                        else "not_dispatched"
                    ),
                    post_dispatched=dispatch.dispatched,
                    provider_terminal_status=(
                        "uncertain" if uncertain else "failed"
                    ),
                )
            else:
                self._update(
                    task.job_id,
                    status="failed",
                    error_code="generation_failed",
                )
                self._record_failure(decision_id, "generation_failed")

    def list(self, *, limit: int = 50) -> AudioJobList:
        self.cleanup_expired()
        rows = self.router_service.repository.list_audio_jobs(
            self.router_service.tenant_id, limit=limit
        )
        rows = [self._reconcile_managed_terminal(row) for row in rows]
        return AudioJobList(jobs=[self._public(row) for row in rows])

    def get(self, job_id: str) -> AudioJob:
        row = self._reconcile_managed_terminal(self._row(job_id))
        return self._public(self._refresh_expiry(row))

    async def content(self, job_id: str) -> AudioContent:
        self._ensure_enabled()
        row = self._reconcile_managed_terminal(self._row(job_id))
        row = self._refresh_expiry(row)
        if str(row["status"]) != "succeeded":
            raise MultimodalServiceError(
                "audio_not_ready",
                "音乐尚未生成完成，请稍后刷新任务状态。",
                status_code=409,
            )
        path = self._output_path(job_id)
        if not path.is_file():
            row = self._update(
                job_id,
                status="expired",
                error_code="audio_expired",
                output_bytes=0,
            )
            raise MultimodalServiceError(
                "audio_expired",
                SAFE_AUDIO_JOB_ERRORS["audio_expired"],
                status_code=410,
            )
        size = int(path.stat().st_size)

        async def chunks() -> AsyncIterator[bytes]:
            with path.open("rb") as handle:
                while True:
                    chunk = await asyncio.to_thread(handle.read, 64 * 1024)
                    if not chunk:
                        break
                    yield chunk

        return AudioContent(
            chunks=chunks(),
            media_type="audio/mpeg",
            content_length=size,
        )

    def delete(self, job_id: str) -> AudioJobDeleteResult:
        row = self._row(job_id)
        if row.get("workload_run_id"):
            raise MultimodalServiceError(
                "managed_audio_job_retained",
                SAFE_AUDIO_JOB_ERRORS["managed_audio_job_retained"],
                status_code=409,
            )
        if not self.router_service.repository.delete_audio_job(
            self.router_service.tenant_id, job_id
        ):
            raise self._not_found()
        self._remove_output(job_id)
        return AudioJobDeleteResult(removed=True)

    def cleanup_expired(
        self,
        *,
        include_terminal_orphans: bool = False,
    ) -> None:
        now = datetime.now(UTC)
        before_created_at: str | None = None
        before_id: str | None = None
        while True:
            rows = self.router_service.repository.list_audio_jobs_for_cleanup(
                self.router_service.tenant_id,
                limit=100,
                before_created_at=before_created_at,
                before_id=before_id,
                include_non_success_terminal=include_terminal_orphans,
            )
            if not rows:
                return
            for row in rows:
                job_id = str(row["id"])
                if str(row.get("status") or "") != "succeeded":
                    self._remove_output(job_id)
                    continue
                expires_at = self._datetime(row.get("expires_at"))
                if (
                    expires_at is None
                    or expires_at <= now
                    or not self._output_path(job_id).is_file()
                ):
                    self._update(
                        job_id,
                        status="expired",
                        error_code="audio_expired",
                        output_bytes=0,
                    )
                    self._remove_output(job_id)
            if len(rows) < 100:
                return
            before_created_at = str(rows[-1]["created_at"])
            before_id = str(rows[-1]["id"])

    def recover_interrupted(self) -> None:
        self._cleanup_orphaned_output_temps()
        before_created_at: str | None = None
        before_id: str | None = None
        while True:
            rows = self.router_service.repository.list_active_audio_jobs(
                self.router_service.tenant_id,
                limit=100,
                before_created_at=before_created_at,
                before_id=before_id,
            )
            if not rows:
                break
            for row in rows:
                job_id = str(row["id"])
                workload_call = None
                if row.get("workload_call_id"):
                    workload_call = (
                        self.router_service.repository.get_workload_call(
                            self.router_service.tenant_id,
                            str(row["workload_call_id"]),
                        )
                    )
                managed_dispatched = bool(row.get("workload_run_id")) and (
                    bool(row.get("post_dispatched"))
                    or bool(workload_call and workload_call.get("dispatched"))
                )
                workload_status = str(
                    workload_call.get("status") if workload_call else ""
                )
                if workload_status == "passed":
                    path = self._output_path(job_id)
                    valid_output = False
                    try:
                        content = path.read_bytes()
                        valid_output = is_complete_mp3(content)
                    except OSError:
                        content = b""
                    if valid_output:
                        self._update(
                            job_id,
                            status="succeeded",
                            actual_model=(
                                workload_call.get("actual_model")
                                if workload_call
                                else row.get("actual_model")
                            ),
                            output_bytes=len(content),
                            error_code=None,
                            expires_at=(
                                row.get("expires_at")
                                or (
                                    datetime.now(UTC)
                                    + timedelta(seconds=self.ttl_seconds)
                                ).isoformat()
                            ),
                            provider_dispatch_state="confirmed",
                            post_dispatched=True,
                            provider_terminal_status="passed",
                        )
                        continue
                    self._update(
                        job_id,
                        status="failed",
                        error_code="audio_output_persistence_failed",
                        output_bytes=0,
                        provider_dispatch_state="confirmed",
                        post_dispatched=True,
                        provider_terminal_status="passed",
                    )
                    self._remove_output(job_id)
                    continue
                if workload_status == "failed":
                    self._update(
                        job_id,
                        status="failed",
                        error_code=(
                            str(workload_call.get("error_code") or "")
                            or "generation_failed"
                        ),
                        provider_dispatch_state="confirmed",
                        post_dispatched=bool(
                            workload_call and workload_call.get("dispatched")
                        ),
                        provider_terminal_status="failed",
                    )
                    self._remove_output(job_id)
                    continue
                self._update(
                    job_id,
                    status="failed",
                    error_code=(
                        "provider_result_uncertain"
                        if managed_dispatched
                        else "worker_interrupted"
                    ),
                    provider_dispatch_state=(
                        "uncertain"
                        if managed_dispatched
                        else str(
                            row.get("provider_dispatch_state")
                            or "not_dispatched"
                        )
                    ),
                    provider_terminal_status=(
                        "uncertain" if managed_dispatched else "failed"
                    ),
                    post_dispatched=managed_dispatched,
                )
                if not row.get("workload_run_id"):
                    self._record_failure(
                        str(row.get("decision_id") or ""),
                        "worker_interrupted",
                    )
                self._remove_output(job_id)
            if len(rows) < 100:
                break
            before_created_at = str(rows[-1]["created_at"])
            before_id = str(rows[-1]["id"])
        self.cleanup_expired(include_terminal_orphans=True)

    async def _profile(
        self, model_id: str, *, has_image: bool
    ) -> AudioChatProfile:
        catalog = await self.catalog_service.get_catalog(force=False)
        if catalog.status == "disabled":
            self._ensure_enabled()
        if catalog.status == "offline":
            raise MultimodalServiceError(
                "audio_catalog_unavailable",
                "暂时无法确认音乐模型能力，请检查 OpenRouter 连接后重试。",
                status_code=503,
            )
        profile = next(
            (
                item
                for item in catalog.profiles
                if item.model_id == model_id
                and "generate_audio" in item.operations
            ),
            None,
        )
        if profile is None or not profile.invocable:
            raise MultimodalServiceError(
                "operation_mismatch",
                "所选模型未确认支持音乐生成，请刷新目录后重新选择。",
                status_code=422,
            )
        if has_image and not profile.supports_image_prompt:
            raise MultimodalServiceError(
                "image_prompt_unsupported",
                "所选音乐模型不支持图片提示，请移除图片后重试。",
                status_code=422,
            )
        return profile

    @staticmethod
    def _model_id(value: str) -> str:
        clean = str(value or "").strip()
        if clean not in LYRIA_MODEL_IDS:
            raise MultimodalServiceError(
                "operation_mismatch",
                "本批次只开放已验证的 Lyria Clip 和 Lyria Pro。",
                status_code=422,
            )
        return clean

    @staticmethod
    def _prompt(value: str) -> str:
        clean = str(value or "").strip()
        if not clean:
            raise MultimodalServiceError(
                "empty_prompt",
                "请填写音乐创作描述后再提交。",
                status_code=422,
            )
        if len(clean) > MAX_AUDIO_GENERATION_PROMPT_CHARS:
            raise MultimodalServiceError(
                "prompt_too_long",
                "音乐创作描述不能超过 4,000 个字符。",
                status_code=422,
            )
        return clean

    @staticmethod
    def _idempotency_key(value: str) -> str:
        clean = str(value or "").strip()
        if (
            len(clean) > MAX_IDEMPOTENCY_KEY_CHARS
            or not IDEMPOTENCY_KEY_PATTERN.fullmatch(clean)
        ):
            raise MultimodalServiceError(
                "invalid_idempotency_key",
                "提交标识格式无效，请刷新页面后重试。",
                status_code=422,
            )
        return clean

    @staticmethod
    def _image(
        filename: str | None,
        content_type: str | None,
        content: bytes | None,
    ) -> str | None:
        supplied = (
            filename is not None
            or content_type is not None
            or content is not None
        )
        if not supplied:
            return None
        if not filename or content is None or not content:
            raise MultimodalServiceError(
                "invalid_image_prompt",
                "图片提示不完整，请重新选择 JPEG、PNG 或 WebP 图片。",
                status_code=422,
            )
        if len(content) > MAX_AUDIO_JOB_IMAGE_BYTES:
            raise MultimodalServiceError(
                "image_prompt_too_large",
                "图片提示超过 10 MiB，请压缩后重试。",
                status_code=413,
            )
        suffix = Path(filename).suffix.lower().lstrip(".")
        profile = AUDIO_JOB_IMAGE_FORMATS.get(suffix)
        if profile is None:
            raise MultimodalServiceError(
                "unsupported_image_prompt_format",
                "图片提示只支持 JPEG、PNG 和 WebP。",
                status_code=422,
            )
        media_type, allowed_types = profile
        clean_type = (content_type or "").split(";", 1)[0].strip().lower()
        if clean_type and clean_type not in allowed_types:
            raise MultimodalServiceError(
                "image_prompt_type_mismatch",
                "图片扩展名与文件类型不一致，请重新导出后重试。",
                status_code=422,
            )
        if not AudioJobService._matches_image_signature(suffix, content):
            raise MultimodalServiceError(
                "invalid_image_prompt",
                "图片内容无效或与扩展名不一致，请重新导出后重试。",
                status_code=422,
            )
        encoded = base64.b64encode(content).decode("ascii")
        return f"data:{media_type};base64,{encoded}"

    @staticmethod
    def _matches_image_signature(suffix: str, content: bytes) -> bool:
        if suffix in {"jpg", "jpeg"}:
            return content.startswith(b"\xff\xd8\xff")
        if suffix == "png":
            return content.startswith(b"\x89PNG\r\n\x1a\n")
        if suffix == "webp":
            return (
                len(content) >= 12
                and content.startswith(b"RIFF")
                and content[8:12] == b"WEBP"
            )
        return False

    def _record_start(
        self,
        target: OpenRouterTarget,
        *,
        model_id: str,
        input_bytes: int,
    ) -> str:
        try:
            return str(
                self.router_service.repository.record_routing_decision(
                    self.router_service.tenant_id,
                    session_id_hash=None,
                    engine="openrouter",
                    strategy="explicit",
                    operation="generate_audio",
                    connection_id=target.connection_id,
                    model_id=model_id,
                    reason_codes=[
                        "explicit_model",
                        "operation_generate_audio",
                    ],
                    input_bytes=input_bytes,
                )
            )
        except Exception as exc:
            raise MultimodalServiceError(
                "audit_unavailable",
                "暂时无法建立音乐生成审计记录，请稍后重试。",
                status_code=503,
            ) from exc

    def _record_success(
        self,
        decision_id: str,
        *,
        output_bytes: int,
        cost_usd: float | None,
    ) -> None:
        if not decision_id:
            return
        try:
            self.router_service.repository.update_routing_decision_usage(
                self.router_service.tenant_id,
                decision_id,
                outcome="success",
                media_seconds=None,
                settled_cost_usd=cost_usd,
                cost_status=(
                    "actual" if cost_usd is not None else "unavailable"
                ),
                output_bytes=output_bytes,
            )
        except Exception:
            logger.warning(
                "Unable to update audio job audit outcome: %s", decision_id
            )

    def _record_failure(self, decision_id: str, outcome: str) -> None:
        if not decision_id:
            return
        try:
            self.router_service.repository.update_routing_decision_outcome(
                self.router_service.tenant_id, decision_id, outcome
            )
        except Exception:
            logger.warning(
                "Unable to update audio job audit failure: %s", decision_id
            )

    def _row(self, job_id: str) -> dict[str, object]:
        row = self.router_service.repository.get_audio_job(
            self.router_service.tenant_id, str(job_id or "").strip()
        )
        if row is None:
            raise self._not_found()
        return row

    def _update(
        self, job_id: str, **changes: object
    ) -> dict[str, object] | None:
        return self.router_service.repository.update_audio_job(
            self.router_service.tenant_id, job_id, **changes
        )

    def _refresh_expiry(
        self, row: dict[str, object]
    ) -> dict[str, object]:
        if str(row.get("status") or "") != "succeeded":
            return row
        expires_at = self._datetime(row.get("expires_at"))
        if (
            expires_at is not None
            and expires_at > datetime.now(UTC)
            and self._output_path(str(row["id"])).is_file()
        ):
            return row
        updated = self._update(
            str(row["id"]),
            status="expired",
            error_code="audio_expired",
            output_bytes=0,
        )
        self._remove_output(str(row["id"]))
        return updated or row

    def _reconcile_managed_terminal(
        self, row: dict[str, object]
    ) -> dict[str, object]:
        if (
            str(row.get("status") or "") not in {"queued", "running"}
            or not row.get("workload_call_id")
        ):
            return row
        workload_call = self.router_service.repository.get_workload_call(
            self.router_service.tenant_id,
            str(row["workload_call_id"]),
        )
        if (
            workload_call is None
            or str(workload_call.get("status") or "") != "passed"
        ):
            return row
        path = self._output_path(str(row["id"]))
        try:
            content = path.read_bytes()
        except OSError:
            return row
        if not is_complete_mp3(content):
            return row
        try:
            updated = self._update(
                str(row["id"]),
                status="succeeded",
                actual_model=(
                    workload_call.get("actual_model")
                    or row.get("actual_model")
                ),
                output_bytes=len(content),
                error_code=None,
                expires_at=(
                    row.get("expires_at")
                    or (
                        datetime.now(UTC)
                        + timedelta(seconds=self.ttl_seconds)
                    ).isoformat()
                ),
                provider_dispatch_state="confirmed",
                post_dispatched=True,
                provider_terminal_status="passed",
            )
        except Exception:
            logger.warning(
                "Managed audio job terminal reconciliation is still pending "
                "job=%s code=job_store_unavailable",
                row["id"],
            )
            return row
        return updated or row

    def _write_output(self, job_id: str, content: bytes) -> None:
        path = self._output_path(job_id)
        temporary = path.with_suffix(f".tmp-{uuid.uuid4().hex}")
        try:
            temporary.write_bytes(content)
            try:
                temporary.chmod(0o600)
            except OSError:
                pass
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    async def _write_output_cancellation_safe(
        self, job_id: str, content: bytes
    ) -> None:
        write_future = asyncio.get_running_loop().run_in_executor(
            None,
            self._write_output,
            job_id,
            content,
        )
        cancelled = False
        while True:
            try:
                await asyncio.shield(write_future)
                break
            except asyncio.CancelledError:
                cancelled = True
            except Exception:
                if not cancelled:
                    raise
                break
        if cancelled:
            self._remove_output(job_id)
            raise asyncio.CancelledError

    def _remove_output(self, job_id: str) -> None:
        self._output_path(job_id).unlink(missing_ok=True)

    def _cleanup_orphaned_output_temps(self) -> None:
        for temporary in self.output_dir.iterdir():
            if not AUDIO_OUTPUT_TEMP_PATTERN.fullmatch(temporary.name):
                continue
            try:
                if temporary.is_file():
                    temporary.unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "Unable to remove orphaned audio output temporary file"
                )

    def _output_path(self, job_id: str) -> Path:
        digest = hashlib.sha256(
            f"{self.router_service.tenant_id}\0{job_id}".encode("utf-8")
        ).hexdigest()
        return self.output_dir / f"{digest}.mp3"

    @staticmethod
    def _datetime(value: object) -> datetime | None:
        try:
            parsed = datetime.fromisoformat(str(value or ""))
        except ValueError:
            return None
        return (
            parsed.replace(tzinfo=UTC)
            if parsed.tzinfo is None
            else parsed.astimezone(UTC)
        )

    def _public(self, row: dict[str, object]) -> AudioJob:
        error_code = str(row.get("error_code") or "").strip()
        execution_mode: Literal["managed", "legacy"] = (
            "managed" if row.get("workload_run_id") else "legacy"
        )
        dispatch_state = str(
            row.get("provider_dispatch_state") or ""
        ).strip()
        if dispatch_state not in {
            "not_dispatched",
            "dispatched",
            "confirmed",
            "uncertain",
        }:
            dispatch_state = ""
        receipts = self._managed_receipts(row) if execution_mode == "managed" else []
        return AudioJob(
            job_id=str(row["id"]),
            status=str(row["status"]),
            requested_model=str(row["requested_model"]),
            actual_model=(
                str(row["actual_model"]) if row.get("actual_model") else None
            ),
            provider="openrouter",
            generation_id=(
                str(row["generation_id"])
                if row.get("generation_id") and execution_mode == "legacy"
                else None
            ),
            parameters=AudioJobParameters(
                has_image=bool(row.get("has_image")),
            ),
            usage=AudioJobUsage(
                cost_usd=(
                    float(row["cost_usd"])
                    if row.get("cost_usd") is not None
                    else None
                ),
                cost_kind=str(row.get("cost_kind") or "unavailable"),
            ),
            output_bytes=max(0, int(row.get("output_bytes") or 0)),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            expires_at=(
                str(row["expires_at"]) if row.get("expires_at") else None
            ),
            error=(
                AudioJobError(
                    code=error_code,
                    message=SAFE_AUDIO_JOB_ERRORS.get(
                        error_code,
                        SAFE_AUDIO_JOB_ERRORS["generation_failed"],
                    ),
                )
                if error_code
                else None
            ),
            execution_mode=execution_mode,
            provider_route_receipts=receipts,
            provider_dispatch_state=(
                dispatch_state if dispatch_state else None
            ),
            retry_allowed=not (
                execution_mode == "managed"
                and str(row.get("status") or "") == "failed"
                and bool(row.get("post_dispatched"))
            ),
            fallback_reason_codes=(
                [error_code]
                if execution_mode == "managed" and error_code
                else []
            ),
        )

    def _managed_receipts(
        self, row: dict[str, object]
    ) -> list[dict[str, object]]:
        run_id = str(row.get("workload_run_id") or "").strip()
        call_id = str(row.get("workload_call_id") or "").strip()
        if not run_id or not call_id:
            return []
        try:
            run = self.router_service.repository.get_workload_run(
                self.router_service.tenant_id, run_id
            )
            call = self.router_service.repository.get_workload_call(
                self.router_service.tenant_id, call_id
            )
        except Exception:
            logger.warning(
                "Unable to project audio job workload receipt: %s", row.get("id")
            )
            return []
        if call is None or str(call.get("run_id") or "") != run_id:
            return []
        try:
            raw_reasons = json.loads(str(run.get("reason_codes_json") or "[]"))
        except (json.JSONDecodeError, TypeError, ValueError):
            raw_reasons = []
        reasons = (
            [str(item) for item in raw_reasons if isinstance(item, str)]
            if isinstance(raw_reasons, list)
            else []
        )
        dispatched = bool(call.get("dispatched"))
        public_call: dict[str, object] = {
            "call_sequence": max(1, int(call.get("call_sequence") or 1)),
            "model_id": str(call.get("requested_model") or ""),
            "actual_model": (
                str(call["actual_model"]) if call.get("actual_model") else None
            ),
            "dispatched": dispatched,
            "status": str(call.get("status") or "failed"),
            "error_code": (
                str(call["error_code"]) if call.get("error_code") else None
            ),
            "prompt_tokens": (
                int(call["prompt_tokens"])
                if call.get("prompt_tokens") is not None
                else None
            ),
            "completion_tokens": (
                int(call["completion_tokens"])
                if call.get("completion_tokens") is not None
                else None
            ),
            "total_tokens": (
                int(call["total_tokens"])
                if call.get("total_tokens") is not None
                else None
            ),
        }
        return [
            {
                "contract_version": "modelmirror-provider-workload-routing-v1",
                "entry_id": "audio_generation",
                "routing_mode": "managed_required",
                "run_reference": run_id,
                "status": str(run.get("status") or "failed"),
                "call_count": 1 if dispatched else 0,
                "reason_codes": reasons,
                "calls": [public_call],
            }
        ]

    @staticmethod
    def _managed_error(exc: ManagedMultimodalError) -> MultimodalServiceError:
        return MultimodalServiceError(
            exc.code,
            exc.public_message,
            status_code=exc.status_code,
            route_receipt=exc.receipt,
        )

    @staticmethod
    def _not_found() -> MultimodalServiceError:
        return MultimodalServiceError(
            "audio_job_not_found",
            "未找到该音乐任务，它可能已被移除。",
            status_code=404,
        )

    @staticmethod
    def _ensure_enabled() -> None:
        if os.getenv(
            "MULTIMODAL_AUDIO_GENERATION_ENABLED", "false"
        ).strip().lower() not in {"1", "true", "yes", "on"}:
            raise MultimodalServiceError(
                "audio_generation_disabled",
                "音乐生成功能当前未启用。",
                status_code=503,
            )
