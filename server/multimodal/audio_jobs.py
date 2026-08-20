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
from typing import Literal

import httpx
from pydantic import BaseModel, Field

try:
    from server.model_router.egress import ProviderEgressPolicy, stream_provider_url
    from server.model_router.service import ModelRouterService
except ModuleNotFoundError:
    from model_router.egress import ProviderEgressPolicy, stream_provider_url
    from model_router.service import ModelRouterService

from .audio_catalog import AudioCatalogService, AudioChatProfile
from .stt import MultimodalServiceError, OpenRouterTarget


logger = logging.getLogger("modelmirror.multimodal")

LYRIA_MODEL_IDS = {
    "google/lyria-3-clip-preview",
    "google/lyria-3-pro-preview",
}
MAX_AUDIO_GENERATION_PROMPT_CHARS = 4_000
MAX_AUDIO_JOB_IMAGE_BYTES = 10 * 1024 * 1024
MAX_GENERATED_AUDIO_BYTES = 25 * 1024 * 1024
MAX_IDEMPOTENCY_KEY_CHARS = 128
DEFAULT_AUDIO_JOB_TTL_SECONDS = 30 * 60
IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
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
    "audio_expired": "临时音频已过期，请重新生成后下载。",
    "generation_failed": "音乐生成未完成，请检查输入后重新提交。",
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
    target: OpenRouterTarget
    model_id: str
    prompt: str
    image_data_url: str | None


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
        content: str | list[dict[str, object]] = prompt
        if image_data_url:
            content = [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": image_data_url},
                },
            ]
        payload = {
            "model": model_id,
            "stream": True,
            "messages": [{"role": "user", "content": content}],
        }

        encoded_parts: list[str] = []
        encoded_length = 0
        actual_model = model_id
        generation_id: str | None = None
        finish_reason: str | None = None
        usage: object = None
        saw_done = False

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
                    generation_id = (
                        response.headers.get("x-generation-id") or None
                    )
                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        raw = line[6:]
                        if raw.strip() == "[DONE]":
                            saw_done = True
                            continue
                        try:
                            item = json.loads(raw)
                        except ValueError as exc:
                            raise self._incomplete_error() from exc
                        if isinstance(item.get("error"), dict):
                            raise MultimodalServiceError(
                                "provider_unavailable",
                                SAFE_AUDIO_JOB_ERRORS["provider_unavailable"],
                                status_code=502,
                            )
                        if isinstance(item.get("model"), str):
                            actual_model = item["model"]
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
                                SAFE_AUDIO_JOB_ERRORS[
                                    "audio_output_too_large"
                                ],
                                status_code=502,
                            )
                        encoded_parts.append(data)
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

        if not saw_done or finish_reason != "stop" or not encoded_parts:
            raise self._incomplete_error()
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
            actual_model=actual_model,
            generation_id=generation_id,
            cost_usd=self._cost(usage),
        )

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
        await response.aread()
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

    @staticmethod
    def _is_mp3(content: bytes) -> bool:
        return content.startswith(b"ID3") or (
            len(content) >= 2
            and content[0] == 0xFF
            and content[1] & 0xE0 == 0xE0
        )

    @staticmethod
    def _incomplete_error() -> MultimodalServiceError:
        return MultimodalServiceError(
            "audio_output_incomplete",
            SAFE_AUDIO_JOB_ERRORS["audio_output_incomplete"],
            status_code=502,
        )


class AudioJobService:
    def __init__(
        self,
        router_service: ModelRouterService,
        catalog_service: AudioCatalogService,
        *,
        adapter: OpenRouterAudioJobAdapter | None = None,
        output_dir: str | Path | None = None,
        ttl_seconds: int = DEFAULT_AUDIO_JOB_TTL_SECONDS,
    ) -> None:
        self.router_service = router_service
        self.catalog_service = catalog_service
        self.adapter = adapter or OpenRouterAudioJobAdapter(
            egress_policy=router_service.egress_policy
        )
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

    async def run(self, task: AudioJobTask) -> None:
        row = self._update(task.job_id, status="running", error_code=None)
        if row is None:
            return
        decision_id = str(row.get("decision_id") or "")
        try:
            result = await self.adapter.generate(
                task.target,
                model_id=task.model_id,
                prompt=task.prompt,
                image_data_url=task.image_data_url,
            )
            await asyncio.to_thread(
                self._write_output, task.job_id, result.content
            )
            expires_at = (
                datetime.now(UTC) + timedelta(seconds=self.ttl_seconds)
            ).isoformat()
            changes: dict[str, object] = {
                "status": "succeeded",
                "actual_model": result.actual_model,
                "generation_id": result.generation_id,
                "output_bytes": len(result.content),
                "error_code": None,
                "expires_at": expires_at,
            }
            if result.cost_usd is not None:
                changes["cost_usd"] = result.cost_usd
                changes["cost_kind"] = "actual"
            row = self._update(task.job_id, **changes)
            if row is None:
                await asyncio.to_thread(self._remove_output, task.job_id)
                return
            self._record_success(
                decision_id,
                output_bytes=len(result.content),
                cost_usd=result.cost_usd,
            )
        except MultimodalServiceError as exc:
            await asyncio.to_thread(self._remove_output, task.job_id)
            self._update(
                task.job_id,
                status="failed",
                error_code=exc.code,
            )
            self._record_failure(decision_id, exc.code)
        except Exception:
            logger.exception(
                "Audio job failed without exposing request content: %s",
                task.job_id,
            )
            await asyncio.to_thread(self._remove_output, task.job_id)
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
        return AudioJobList(jobs=[self._public(row) for row in rows])

    def get(self, job_id: str) -> AudioJob:
        return self._public(self._refresh_expiry(self._row(job_id)))

    async def content(self, job_id: str) -> AudioContent:
        self._ensure_enabled()
        row = self._refresh_expiry(self._row(job_id))
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
        self._row(job_id)
        if not self.router_service.repository.delete_audio_job(
            self.router_service.tenant_id, job_id
        ):
            raise self._not_found()
        self._remove_output(job_id)
        return AudioJobDeleteResult(removed=True)

    def cleanup_expired(self) -> None:
        rows = self.router_service.repository.list_audio_jobs(
            self.router_service.tenant_id, limit=100
        )
        now = datetime.now(UTC)
        for row in rows:
            job_id = str(row["id"])
            status = str(row.get("status") or "")
            if status in {"queued", "running"}:
                continue
            if status != "succeeded":
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

    def recover_interrupted(self) -> None:
        rows = self.router_service.repository.list_audio_jobs(
            self.router_service.tenant_id, limit=100
        )
        for row in rows:
            if str(row.get("status") or "") not in {"queued", "running"}:
                continue
            job_id = str(row["id"])
            self._update(
                job_id,
                status="failed",
                error_code="worker_interrupted",
            )
            self._record_failure(
                str(row.get("decision_id") or ""),
                "worker_interrupted",
            )
            self._remove_output(job_id)
        self.cleanup_expired()

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

    def _remove_output(self, job_id: str) -> None:
        self._output_path(job_id).unlink(missing_ok=True)

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

    @staticmethod
    def _public(row: dict[str, object]) -> AudioJob:
        error_code = str(row.get("error_code") or "").strip()
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
                if row.get("generation_id")
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
