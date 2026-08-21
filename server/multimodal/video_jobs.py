from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import os
import re
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

try:
    from server.model_router.egress import ProviderEgressPolicy, request_provider_url
    from server.model_router.service import ModelRouterService
except ModuleNotFoundError:
    from model_router.egress import ProviderEgressPolicy, request_provider_url
    from model_router.service import ModelRouterService

from .stt import MultimodalServiceError, OpenRouterTarget
from .video_analysis import video_file_data_url, validated_video_url
from .video_catalog import (
    PROVIDER_OPTION_AUDIT,
    VideoCatalogService,
    VideoModelProfile,
)


logger = logging.getLogger("modelmirror.multimodal")

MAX_FIRST_FRAME_BYTES = 10 * 1024 * 1024
MAX_REFERENCE_IMAGE_COUNT = 3
MAX_REFERENCE_IMAGE_BYTES = 30 * 1024 * 1024
MAX_VIDEO_GENERATION_PROMPT_CHARS = 4_000
MAX_IDEMPOTENCY_KEY_CHARS = 128
IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "expired"}

FIRST_FRAME_FORMATS: dict[str, tuple[str, tuple[str, ...]]] = {
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

SAFE_JOB_ERRORS: dict[str, str] = {
    "provider_generation_failed": (
        "视频生成未成功，请调整提示词或参数后重新提交。"
    ),
    "provider_generation_cancelled": "上游已停止这项视频生成任务。",
    "provider_generation_expired": (
        "上游任务已过期，请重新提交；已生成内容可能无法继续下载。"
    ),
    "invalid_upstream_response": (
        "视频服务返回了无法识别的任务信息，请稍后重试。"
    ),
}


class VideoJobParameters(BaseModel):
    task_type: Literal["generate", "upscale"] = "generate"
    duration: int | None = None
    resolution: str | None = None
    aspect_ratio: str | None = None
    generate_audio: bool = False
    has_first_frame: bool = False
    has_last_frame: bool = False
    reference_image_count: int = 0
    has_source_video: bool = False
    upscale_factor: float | None = None
    creativity: int | None = None
    provider_option_keys: list[str] = Field(default_factory=list)


class VideoJobUsage(BaseModel):
    cost_usd: float | None = None
    cost_kind: Literal["actual", "estimated", "unavailable"] = "unavailable"


class VideoJobError(BaseModel):
    code: str
    message: str


class VideoJob(BaseModel):
    job_id: str
    status: Literal[
        "queued",
        "running",
        "succeeded",
        "failed",
        "cancelled",
        "expired",
    ]
    requested_model: str
    actual_model: str | None = None
    provider: Literal["openrouter"] = "openrouter"
    generation_id: str | None = None
    parameters: VideoJobParameters
    usage: VideoJobUsage
    created_at: str
    updated_at: str
    error: VideoJobError | None = None
    output_count: int = 0


class VideoJobList(BaseModel):
    jobs: list[VideoJob] = Field(default_factory=list)


class VideoJobDeleteResult(BaseModel):
    removed: bool
    upstream_cancelled: Literal[False] = False


@dataclass(frozen=True)
class VideoContent:
    chunks: AsyncIterator[bytes]
    media_type: str
    content_length: int | None


class OpenRouterVideoJobAdapter:
    def __init__(
        self,
        *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        egress_policy: ProviderEgressPolicy | None = None,
    ) -> None:
        self._client_factory = client_factory or self._default_client
        self._managed_client_factory = client_factory or self._direct_client
        self._egress_policy = egress_policy

    async def submit(
        self,
        target: OpenRouterTarget,
        payload: dict[str, object],
    ) -> dict[str, Any]:
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
                    self._api_url(target.base_url, "videos"),
                    headers=self._headers(target.api_key),
                    json=payload,
                )
            except (
                httpx.ConnectTimeout,
                httpx.ReadTimeout,
                httpx.WriteTimeout,
            ) as exc:
                raise self._transport_error(timeout=True) from exc
            except httpx.HTTPError as exc:
                raise self._transport_error(timeout=False) from exc
        self._raise_for_status(response, submitting=True)
        return self._json(response)

    async def poll(
        self,
        target: OpenRouterTarget,
        upstream_job_id: str,
    ) -> dict[str, Any]:
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
                    self._api_url(
                        target.base_url, f"videos/{upstream_job_id}"
                    ),
                    headers=self._headers(target.api_key),
                )
            except (
                httpx.ConnectTimeout,
                httpx.ReadTimeout,
                httpx.WriteTimeout,
            ) as exc:
                raise self._transport_error(timeout=True) from exc
            except httpx.HTTPError as exc:
                raise self._transport_error(timeout=False) from exc
        self._raise_for_status(response, submitting=False)
        return self._json(response)

    async def content(
        self,
        target: OpenRouterTarget,
        upstream_job_id: str,
        *,
        index: int,
    ) -> VideoContent:
        client_factory = (
            self._managed_client_factory
            if target.connection_id
            else self._client_factory
        )
        client = client_factory()
        try:
            response = await request_provider_url(
                client,
                self._egress_policy or ProviderEgressPolicy(),
                target.connection_id if self._egress_policy else None,
                "GET",
                self._api_url(
                    target.base_url,
                    f"videos/{upstream_job_id}/content",
                ),
                headers=self._headers(target.api_key),
                params={"index": index},
                stream=True,
            )
            self._raise_for_status(response, submitting=False)
            media_type = (
                response.headers.get("content-type", "")
                .split(";", 1)[0]
                .strip()
                .lower()
            )
            if not (
                media_type.startswith("video/")
                or media_type == "application/octet-stream"
            ):
                raise MultimodalServiceError(
                    "invalid_video_content",
                    "视频服务返回的内容不是可播放视频，请稍后重试。",
                    status_code=502,
                )
            content_length = self._content_length(response)
        except Exception:
            await client.aclose()
            raise

        async def chunks() -> AsyncIterator[bytes]:
            try:
                async for chunk in response.aiter_bytes():
                    if chunk:
                        yield chunk
            finally:
                await response.aclose()
                await client.aclose()

        return VideoContent(
            chunks=chunks(),
            media_type=(
                "video/mp4"
                if media_type == "application/octet-stream"
                else media_type
            ),
            content_length=content_length,
        )

    @staticmethod
    def _direct_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(connect=15, read=180, write=60, pool=10),
            follow_redirects=False,
            trust_env=False,
        )

    @staticmethod
    def _default_client() -> httpx.AsyncClient:
        timeout = httpx.Timeout(connect=15, read=180, write=60, pool=10)
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
    def _headers(api_key: str) -> dict[str, str]:
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
    def _api_url(base_url: str, path: str) -> str:
        root = str(base_url or "").strip().rstrip("/")
        for suffix in (
            "/chat/completions",
            "/audio/transcriptions",
            "/audio/speech",
            "/videos/models",
            "/models",
        ):
            if root.lower().endswith(suffix):
                root = root[: -len(suffix)].rstrip("/")
                break
        if not root.lower().endswith("/v1"):
            root = f"{root}/v1"
        return f"{root}/{path.lstrip('/')}"

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise MultimodalServiceError(
                "invalid_upstream_response",
                "视频服务返回了无法识别的任务信息，请稍后重试。",
                status_code=502,
            ) from exc
        if not isinstance(payload, dict):
            raise MultimodalServiceError(
                "invalid_upstream_response",
                "视频服务返回了无法识别的任务信息，请稍后重试。",
                status_code=502,
            )
        return payload

    @staticmethod
    def _transport_error(*, timeout: bool) -> MultimodalServiceError:
        if timeout:
            return MultimodalServiceError(
                "upstream_timeout",
                "视频服务响应超时，请稍后刷新任务状态；请勿重复提交。",
                status_code=504,
            )
        return MultimodalServiceError(
            "upstream_unreachable",
            "暂时无法连接视频服务，请检查网络后刷新任务状态。",
            status_code=502,
        )

    @staticmethod
    def _raise_for_status(
        response: httpx.Response, *, submitting: bool
    ) -> None:
        status = response.status_code
        if status < 400:
            return
        if status in {401, 403}:
            raise MultimodalServiceError(
                "provider_credentials_invalid",
                "OpenRouter 密钥无效或没有视频生成权限，请更新模型服务连接。",
                status_code=502,
            )
        if status == 402:
            raise MultimodalServiceError(
                "provider_quota_exceeded",
                "OpenRouter 余额或预算不足，本次视频任务未被接受。",
                status_code=402,
            )
        if status == 404:
            code = "model_unavailable" if submitting else "upstream_job_not_found"
            message = (
                "所选视频生成模型当前不可用，请刷新模型目录后重试。"
                if submitting
                else "上游已找不到该视频任务，任务可能已经过期。"
            )
            raise MultimodalServiceError(
                code,
                message,
                status_code=422 if submitting else 404,
            )
        if status == 413:
            raise MultimodalServiceError(
                "provider_file_too_large",
                "首帧图片超过上游限制，请压缩图片后重试。",
                status_code=413,
            )
        if status == 429:
            raise MultimodalServiceError(
                "provider_rate_limited",
                "视频生成请求过多，请稍后重试；已提交任务请直接刷新状态。",
                status_code=429,
            )
        if status >= 500:
            raise MultimodalServiceError(
                "provider_unavailable",
                "视频服务暂时不可用，请稍后重试；已提交任务请直接刷新状态。",
                status_code=502,
            )
        raise MultimodalServiceError(
            "provider_rejected_request",
            "视频生成请求未被接受，请检查模型和参数后重试。",
            status_code=422,
        )

    @staticmethod
    def _content_length(response: httpx.Response) -> int | None:
        raw = response.headers.get("content-length")
        if raw and raw.isdigit():
            return int(raw)
        return None


class VideoJobService:
    def __init__(
        self,
        router_service: ModelRouterService,
        catalog_service: VideoCatalogService,
        *,
        adapter: OpenRouterVideoJobAdapter | None = None,
    ) -> None:
        self.router_service = router_service
        self.catalog_service = catalog_service
        self.adapter = adapter or OpenRouterVideoJobAdapter(
            egress_policy=router_service.egress_policy
        )

    async def create(
        self,
        *,
        model_id: str,
        prompt: str,
        idempotency_key: str,
        duration: int | None = None,
        resolution: str | None = None,
        aspect_ratio: str | None = None,
        generate_audio: bool = False,
        seed: int | None = None,
        first_frame_filename: str | None = None,
        first_frame_content_type: str | None = None,
        first_frame_content: bytes | None = None,
        last_frame_filename: str | None = None,
        last_frame_content_type: str | None = None,
        last_frame_content: bytes | None = None,
        reference_image_filenames: list[str] | None = None,
        reference_image_content_types: list[str | None] | None = None,
        reference_image_contents: list[bytes] | None = None,
        source_type: str | None = None,
        source_video_filename: str | None = None,
        source_video_content_type: str | None = None,
        source_video_content: bytes | None = None,
        source_video_url: str | None = None,
        upscale_factor: float | None = None,
        creativity: int | None = None,
        provider_options: dict[str, object] | None = None,
    ) -> VideoJob:
        self._ensure_enabled()
        clean_model = self._model_id(model_id)
        clean_key = self._idempotency_key(idempotency_key)
        tenant_id = self.router_service.tenant_id
        key_hash = hashlib.sha256(
            f"{tenant_id}\0{clean_key}".encode("utf-8")
        ).hexdigest()
        existing = (
            self.router_service.repository
            .get_video_job_by_idempotency_hash(tenant_id, key_hash)
        )
        if existing is not None:
            return self._public(existing)
        profile = await self._profile(
            clean_model,
            force=bool(
                provider_options
                or source_type
                or source_video_filename
                or source_video_url
                or upscale_factor is not None
                or creativity is not None
            ),
        )
        clean_prompt = self._prompt(
            prompt,
            required=not profile.requires_source_video,
        )
        source_video = self._source_video(
            source_type=source_type,
            filename=source_video_filename,
            content_type=source_video_content_type,
            content=source_video_content,
            video_url=source_video_url,
        )
        frame_data_url = self._first_frame(
            first_frame_filename,
            first_frame_content_type,
            first_frame_content,
        )
        last_frame_data_url = self._last_frame(
            last_frame_filename,
            last_frame_content_type,
            last_frame_content,
        )
        reference_data_urls = self._reference_images(
            reference_image_filenames,
            reference_image_content_types,
            reference_image_contents,
        )
        if (
            profile.interaction_status != "ready"
            and not profile.verification_entry_enabled
        ):
            raise MultimodalServiceError(
                "video_verification_required",
                "该模型尚未完成视频生成行为验收。仅可在本地人工核验名单中按最低规格测试。",
                status_code=422,
            )
        provider_payload, provider_option_keys = self._provider_payload(
            clean_model,
            profile,
            provider_options,
        )
        self._validate_parameters(
            profile,
            duration=duration,
            resolution=resolution,
            aspect_ratio=aspect_ratio,
            generate_audio=generate_audio,
            seed=seed,
            has_first_frame=frame_data_url is not None,
            has_last_frame=last_frame_data_url is not None,
            reference_image_count=len(reference_data_urls),
            has_source_video=source_video is not None,
            upscale_factor=upscale_factor,
            creativity=creativity,
        )
        if (
            profile.verification_requires_cost_estimate
            and self._estimated_cost_usd(
                profile,
                duration=duration,
                resolution=resolution,
                generate_audio=generate_audio,
                image_input_count=(
                    int(frame_data_url is not None)
                    + int(last_frame_data_url is not None)
                    + len(reference_data_urls)
                ),
            )
            is None
        ):
            raise MultimodalServiceError(
                "video_verification_cost_unavailable",
                "实时目录暂时无法可靠估算该高费用模型，请勿提交任务；请刷新能力目录后重试。",
                status_code=422,
            )
        stored_option_keys = list(provider_option_keys)
        if source_video is not None:
            stored_option_keys.append("source_video")
        if upscale_factor is not None:
            stored_option_keys.append(f"upscale_factor:{upscale_factor:g}")
        if creativity is not None:
            stored_option_keys.append(f"creativity:{creativity}")
        target = self.catalog_service.resolve_target()
        job_id = f"local_{uuid.uuid4().hex}"
        row, created = (
            self.router_service.repository.create_video_job_if_absent(
                tenant_id,
                job_id=job_id,
                idempotency_key_hash=key_hash,
                connection_id=target.connection_id,
                requested_model=clean_model,
                provider="openrouter",
                duration=duration,
                resolution=resolution,
                aspect_ratio=aspect_ratio,
                generate_audio=generate_audio,
                seed=seed,
                has_first_frame=frame_data_url is not None,
                has_last_frame=last_frame_data_url is not None,
                reference_image_count=len(reference_data_urls),
                provider_option_keys=stored_option_keys,
            )
        )
        if not created:
            return self._public(row)

        try:
            decision_id = self._record_start(
                target,
                model_id=clean_model,
                input_bytes=(
                    len(clean_prompt.encode("utf-8"))
                    + len(first_frame_content or b"")
                    + len(last_frame_content or b"")
                    + sum(
                        len(content)
                        for content in (reference_image_contents or [])
                    )
                    + len(source_video_content or b"")
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
        payload: dict[str, object] = {"model": clean_model}
        if clean_prompt:
            payload["prompt"] = clean_prompt
        if duration is not None:
            payload["duration"] = duration
        if resolution:
            payload["resolution"] = resolution
        if aspect_ratio:
            payload["aspect_ratio"] = aspect_ratio
        if generate_audio:
            payload["generate_audio"] = True
        if seed is not None:
            payload["seed"] = seed
        frame_images: list[dict[str, object]] = []
        if frame_data_url:
            frame_images.append(
                {
                    "type": "image_url",
                    "image_url": {"url": frame_data_url},
                    "frame_type": "first_frame",
                }
            )
        if last_frame_data_url:
            frame_images.append(
                {
                    "type": "image_url",
                    "image_url": {"url": last_frame_data_url},
                    "frame_type": "last_frame",
                }
            )
        if frame_images:
            payload["frame_images"] = frame_images
        if reference_data_urls:
            payload["input_references"] = [
                {
                    "type": "image_url",
                    "image_url": {"url": data_url},
                }
                for data_url in reference_data_urls
            ]
        if source_video:
            payload["input_references"] = [
                {
                    "type": "video_url",
                    "video_url": {"url": source_video},
                }
            ]
        if upscale_factor is not None:
            payload["upscale_factor"] = upscale_factor
        if creativity is not None:
            payload["creativity"] = creativity
        if provider_payload is not None:
            payload["provider"] = provider_payload
        try:
            upstream = await self.adapter.submit(target, payload)
            changes = self._upstream_changes(
                upstream, previous=row, submitting=True
            )
        except MultimodalServiceError as exc:
            self._record_failure(decision_id, exc.code)
            self._update(
                job_id,
                status="failed",
                error_code=exc.code,
            )
            raise
        row = self._update(job_id, **changes)
        self._update_audit(row)
        return self._public(row)

    def list(self, *, limit: int = 50) -> VideoJobList:
        rows = self.router_service.repository.list_video_jobs(
            self.router_service.tenant_id, limit=limit
        )
        return VideoJobList(jobs=[self._public(row) for row in rows])

    def get(self, job_id: str) -> VideoJob:
        return self._public(self._row(job_id))

    async def refresh(self, job_id: str) -> VideoJob:
        self._ensure_enabled()
        row = self._row(job_id)
        if str(row["status"]) in TERMINAL_STATUSES:
            return self._public(row)
        upstream_job_id = str(row.get("upstream_job_id") or "").strip()
        if not upstream_job_id:
            raise MultimodalServiceError(
                "job_submission_incomplete",
                "任务尚未取得上游编号，请稍后重试；请勿重复提交。",
                status_code=409,
            )
        target = self._target_for_row(row)
        upstream = await self.adapter.poll(target, upstream_job_id)
        changes = self._upstream_changes(
            upstream, previous=row, submitting=False
        )
        row = self._update(job_id, **changes)
        self._update_audit(row)
        return self._public(row)

    async def content(self, job_id: str, *, index: int) -> VideoContent:
        self._ensure_enabled()
        row = self._row(job_id)
        if str(row["status"]) != "succeeded":
            raise MultimodalServiceError(
                "video_not_ready",
                "视频尚未生成完成，请先刷新任务状态。",
                status_code=409,
            )
        output_count = max(0, int(row.get("output_count") or 0))
        if index < 0 or index >= output_count:
            raise MultimodalServiceError(
                "video_output_not_found",
                "未找到所选视频输出，请刷新任务状态后重试。",
                status_code=404,
            )
        upstream_job_id = str(row.get("upstream_job_id") or "").strip()
        if not upstream_job_id:
            raise MultimodalServiceError(
                "video_output_not_found",
                "该任务缺少可下载的视频输出。",
                status_code=404,
            )
        target = self._target_for_row(row)
        return await self.adapter.content(
            target, upstream_job_id, index=index
        )

    def delete(self, job_id: str) -> VideoJobDeleteResult:
        if not self.router_service.repository.delete_video_job(
            self.router_service.tenant_id, job_id
        ):
            raise self._not_found()
        return VideoJobDeleteResult(removed=True)

    async def _profile(
        self,
        model_id: str,
        *,
        force: bool = False,
    ) -> VideoModelProfile:
        catalog = await self.catalog_service.get_catalog(force=force)
        if catalog.status == "disabled":
            self._ensure_enabled()
        if force and (catalog.status != "online" or catalog.stale):
            raise MultimodalServiceError(
                "provider_options_not_verified",
                "暂时无法重新确认当前视频能力，请稍后刷新目录后重试。",
                status_code=503,
            )
        if catalog.status == "offline":
            raise MultimodalServiceError(
                "video_catalog_unavailable",
                "暂时无法确认模型的视频生成能力，请检查 OpenRouter 连接后重试。",
                status_code=503,
            )
        for profile in catalog.profiles:
            if (
                profile.model_id == model_id
                and profile.operation == "generate_video"
            ):
                return profile
        raise MultimodalServiceError(
            "operation_mismatch",
            "所选模型未确认支持视频生成，请刷新目录并选择“生成视频”模型。",
            status_code=422,
        )

    @staticmethod
    def _validate_parameters(
        profile: VideoModelProfile,
        *,
        duration: int | None,
        resolution: str | None,
        aspect_ratio: str | None,
        generate_audio: bool,
        seed: int | None,
        has_first_frame: bool,
        has_last_frame: bool,
        reference_image_count: int,
        has_source_video: bool,
        upscale_factor: float | None,
        creativity: int | None,
    ) -> None:
        if profile.requires_source_video:
            if not has_source_video:
                raise MultimodalServiceError(
                    "source_video_required",
                    "该增强模型必须提供一个源视频。",
                    status_code=422,
                )
            factor_range = profile.upscale_factor
            if (
                upscale_factor is None
                or factor_range is None
                or not math.isfinite(upscale_factor)
                or upscale_factor < factor_range.min
                or upscale_factor > factor_range.max
            ):
                raise MultimodalServiceError(
                    "unsupported_upscale_factor",
                    "视频放大倍数超出模型支持范围，请使用目录提供的范围。",
                    status_code=422,
                )
            if creativity is None or creativity not in profile.creativity:
                raise MultimodalServiceError(
                    "unsupported_creativity",
                    "请选择模型支持的精确或创意增强模式。",
                    status_code=422,
                )
            if any(
                (
                    duration is not None,
                    bool(resolution),
                    bool(aspect_ratio),
                    generate_audio,
                    seed is not None,
                    has_first_frame,
                    has_last_frame,
                    reference_image_count > 0,
                )
            ):
                raise MultimodalServiceError(
                    "upscale_generation_parameters_unsupported",
                    "视频增强不接受时长、分辨率、画幅、帧图、音频或随机种子参数。",
                    status_code=422,
                )
            return
        if has_source_video or upscale_factor is not None or creativity is not None:
            raise MultimodalServiceError(
                "video_upscale_unsupported",
                "所选模型不支持源视频增强，请移除增强参数或更换模型。",
                status_code=422,
            )
        if duration is not None and (
            duration <= 0
            or not profile.supported_durations
            or duration not in profile.supported_durations
        ):
            raise MultimodalServiceError(
                "unsupported_duration",
                "所选模型不支持这个视频时长，请使用模型提供的时长选项。",
                status_code=422,
            )
        if resolution and (
            len(resolution) > 32
            or not profile.supported_resolutions
            or resolution not in profile.supported_resolutions
        ):
            raise MultimodalServiceError(
                "unsupported_resolution",
                "所选模型不支持这个分辨率，请使用模型提供的分辨率选项。",
                status_code=422,
            )
        if aspect_ratio and (
            len(aspect_ratio) > 16
            or not profile.supported_aspect_ratios
            or aspect_ratio not in profile.supported_aspect_ratios
        ):
            raise MultimodalServiceError(
                "unsupported_aspect_ratio",
                "所选模型不支持这个画面比例，请使用模型提供的比例选项。",
                status_code=422,
            )
        if (
            resolution
            and aspect_ratio
            and profile.supported_sizes
            and not VideoJobService._supports_size_combination(
                profile,
                resolution=resolution,
                aspect_ratio=aspect_ratio,
            )
        ):
            raise MultimodalServiceError(
                "unsupported_video_size",
                "所选模型不支持这个分辨率与画面比例组合，请使用目录提供的尺寸。",
                status_code=422,
            )
        if has_first_frame and (
            "first_frame" not in profile.supported_frame_types
            and not profile.supports_first_frame
        ):
            raise MultimodalServiceError(
                "first_frame_unsupported",
                "所选模型不支持首帧图片，请移除图片或更换模型。",
                status_code=422,
            )
        if (
            has_last_frame
            and "last_frame" not in profile.supported_frame_types
        ):
            raise MultimodalServiceError(
                "last_frame_unsupported",
                "所选模型不支持尾帧图片，请移除尾帧或更换模型。",
                status_code=422,
            )
        if reference_image_count:
            max_references = profile.max_reference_images or 0
            if (
                not profile.supports_reference_images
                or reference_image_count > max_references
            ):
                raise MultimodalServiceError(
                    "reference_images_unsupported",
                    "所选模型不支持这些参考图，请减少数量或更换模型。",
                    status_code=422,
                )
        if generate_audio and not profile.supports_generated_audio:
            raise MultimodalServiceError(
                "generated_audio_unsupported",
                "所选模型不支持同时生成音频，请关闭音频选项。",
                status_code=422,
            )
        if seed is not None and (
            seed < 0 or seed > 2_147_483_647 or not profile.supports_seed
        ):
            raise MultimodalServiceError(
                "seed_unsupported",
                "所选模型不支持随机种子，请清空该参数。",
                status_code=422,
            )

    @staticmethod
    def _supports_size_combination(
        profile: VideoModelProfile,
        *,
        resolution: str,
        aspect_ratio: str,
    ) -> bool:
        resolution_match = re.fullmatch(r"(\d+)p", resolution.strip().lower())
        ratio_match = re.fullmatch(
            r"(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)",
            aspect_ratio.strip(),
        )
        if not resolution_match or not ratio_match:
            return True
        short_edge = int(resolution_match.group(1))
        ratio = float(ratio_match.group(1)) / float(ratio_match.group(2))
        for supported_size in profile.supported_sizes:
            size_match = re.fullmatch(
                r"(\d+)x(\d+)",
                supported_size.strip().lower(),
            )
            if not size_match:
                continue
            width, height = map(int, size_match.groups())
            if (
                width > 0
                and height > 0
                and min(width, height) == short_edge
                and abs(width / height - ratio) <= 0.02
            ):
                return True
        return False

    @staticmethod
    def _estimated_cost_usd(
        profile: VideoModelProfile,
        *,
        duration: int | None,
        resolution: str | None,
        generate_audio: bool,
        image_input_count: int,
    ) -> float | None:
        if duration is None or duration <= 0 or not resolution:
            return None

        pricing = profile.pricing_skus

        def number(key: str) -> float | None:
            raw = pricing.get(key)
            if raw is None:
                return None
            try:
                value = float(raw)
            except (TypeError, ValueError):
                return None
            return value if value >= 0 else None

        resolution_key = resolution.strip().lower()
        mode = "image_to_video" if image_input_count else "text_to_video"
        dollar_keys = (
            (
                f"duration_seconds_with_audio_{resolution_key}"
                if generate_audio
                else f"duration_seconds_without_audio_{resolution_key}"
            ),
            f"{mode}_duration_seconds_{resolution_key}",
            f"duration_seconds_{resolution_key}",
            (
                "duration_seconds_with_audio"
                if generate_audio
                else "duration_seconds_without_audio"
            ),
            f"{mode}_duration_seconds",
            "duration_seconds",
        )
        per_second = next(
            (value for key in dollar_keys if (value := number(key)) is not None),
            None,
        )
        if per_second is None:
            cent_keys = (
                f"cents_per_video_output_second_{resolution_key}",
                f"cents_per_second_output_{resolution_key}",
                "cents_per_video_output_second",
                "cents_per_second_output",
            )
            cents = next(
                (
                    value
                    for key in cent_keys
                    if (value := number(key)) is not None
                ),
                None,
            )
            if cents is not None:
                per_second = cents / 100
        if per_second is None:
            return None

        image_input_cents = (
            number("cents_per_image_input") or 0.0
        )
        return (
            per_second * duration
            + image_input_cents * image_input_count / 100
        )

    @staticmethod
    def _source_video(
        *,
        source_type: str | None,
        filename: str | None,
        content_type: str | None,
        content: bytes | None,
        video_url: str | None,
    ) -> str | None:
        supplied = any(
            value is not None
            for value in (
                source_type,
                filename,
                content_type,
                content,
                video_url,
            )
        )
        if not supplied:
            return None
        if source_type == "file":
            if video_url or not filename or content is None:
                raise MultimodalServiceError(
                    "invalid_video_source",
                    "请只提交一种视频来源：本地文件或 HTTPS 视频网址。",
                    status_code=422,
                )
            return video_file_data_url(filename, content_type, content)
        if source_type == "url":
            if filename or content_type or content is not None or not video_url:
                raise MultimodalServiceError(
                    "invalid_video_source",
                    "请只提交一种视频来源：本地文件或 HTTPS 视频网址。",
                    status_code=422,
                )
            return validated_video_url(video_url)
        raise MultimodalServiceError(
            "invalid_video_source",
            "请选择上传本地视频或使用 HTTPS 视频网址。",
            status_code=422,
        )

    @staticmethod
    def _first_frame(
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
        if not filename or content is None:
            raise MultimodalServiceError(
                "invalid_first_frame",
                "首帧图片不完整，请重新选择 JPEG、PNG 或 WebP 图片。",
                status_code=422,
            )
        if not content:
            raise MultimodalServiceError(
                "empty_first_frame",
                "首帧图片为空，请重新选择图片。",
                status_code=422,
            )
        if len(content) > MAX_FIRST_FRAME_BYTES:
            raise MultimodalServiceError(
                "first_frame_too_large",
                "首帧图片超过 10 MiB，请压缩后重试。",
                status_code=413,
            )
        suffix = Path(filename).suffix.lower().lstrip(".")
        profile = FIRST_FRAME_FORMATS.get(suffix)
        if profile is None:
            raise MultimodalServiceError(
                "unsupported_first_frame_format",
                "首帧只支持 JPEG、PNG 和 WebP 图片。",
                status_code=422,
            )
        media_type, allowed_types = profile
        clean_type = (content_type or "").split(";", 1)[0].strip().lower()
        if clean_type and clean_type not in allowed_types:
            raise MultimodalServiceError(
                "first_frame_type_mismatch",
                "图片扩展名与文件类型不一致，请重新导出图片后重试。",
                status_code=422,
            )
        if not VideoJobService._matches_image_signature(suffix, content):
            raise MultimodalServiceError(
                "invalid_first_frame",
                "首帧图片内容无效或与扩展名不一致，请重新导出后重试。",
                status_code=422,
            )
        encoded = base64.b64encode(content).decode("ascii")
        return f"data:{media_type};base64,{encoded}"

    @classmethod
    def _last_frame(
        cls,
        filename: str | None,
        content_type: str | None,
        content: bytes | None,
    ) -> str | None:
        return cls._additional_image(
            filename,
            content_type,
            content,
            label="尾帧",
            code_prefix="last_frame",
        )

    @classmethod
    def _reference_images(
        cls,
        filenames: list[str] | None,
        content_types: list[str | None] | None,
        contents: list[bytes] | None,
    ) -> list[str]:
        names = filenames or []
        types = content_types or []
        payloads = contents or []
        if not names and not types and not payloads:
            return []
        if len(names) != len(types) or len(names) != len(payloads):
            raise MultimodalServiceError(
                "invalid_reference_images",
                "参考图数据不完整，请重新选择图片。",
                status_code=422,
            )
        if len(names) > MAX_REFERENCE_IMAGE_COUNT:
            raise MultimodalServiceError(
                "too_many_reference_images",
                "参考图最多 3 张，请移除多余图片后重试。",
                status_code=422,
            )
        if sum(len(content) for content in payloads) > MAX_REFERENCE_IMAGE_BYTES:
            raise MultimodalServiceError(
                "reference_images_too_large",
                "参考图合计不能超过 30 MiB，请压缩后重试。",
                status_code=413,
            )
        result: list[str] = []
        for filename, content_type, content in zip(
            names,
            types,
            payloads,
            strict=True,
        ):
            data_url = cls._additional_image(
                filename,
                content_type,
                content,
                label="参考图",
                code_prefix="reference_image",
            )
            if data_url is not None:
                result.append(data_url)
        return result

    @classmethod
    def _additional_image(
        cls,
        filename: str | None,
        content_type: str | None,
        content: bytes | None,
        *,
        label: str,
        code_prefix: str,
    ) -> str | None:
        supplied = (
            filename is not None
            or content_type is not None
            or content is not None
        )
        if not supplied:
            return None
        if not filename or content is None:
            raise MultimodalServiceError(
                f"invalid_{code_prefix}",
                f"{label}图片不完整，请重新选择 JPEG、PNG 或 WebP 图片。",
                status_code=422,
            )
        if not content:
            raise MultimodalServiceError(
                f"empty_{code_prefix}",
                f"{label}图片为空，请重新选择图片。",
                status_code=422,
            )
        if len(content) > MAX_FIRST_FRAME_BYTES:
            raise MultimodalServiceError(
                f"{code_prefix}_too_large",
                f"{label}图片不能超过 10 MiB，请压缩后重试。",
                status_code=413,
            )
        suffix = Path(filename).suffix.lower().lstrip(".")
        image_profile = FIRST_FRAME_FORMATS.get(suffix)
        if image_profile is None:
            raise MultimodalServiceError(
                f"unsupported_{code_prefix}_format",
                f"{label}只支持 JPEG、PNG 和 WebP 图片。",
                status_code=422,
            )
        media_type, allowed_types = image_profile
        clean_type = (content_type or "").split(";", 1)[0].strip().lower()
        if clean_type and clean_type not in allowed_types:
            raise MultimodalServiceError(
                f"{code_prefix}_type_mismatch",
                f"{label}扩展名与文件类型不一致，请重新导出后重试。",
                status_code=422,
            )
        if not cls._matches_image_signature(suffix, content):
            raise MultimodalServiceError(
                f"invalid_{code_prefix}",
                f"{label}内容无效或与扩展名不一致，请重新导出后重试。",
                status_code=422,
            )
        encoded = base64.b64encode(content).decode("ascii")
        return f"data:{media_type};base64,{encoded}"

    @staticmethod
    def _provider_payload(
        model_id: str,
        profile: VideoModelProfile,
        values: dict[str, object] | None,
    ) -> tuple[dict[str, object] | None, list[str]]:
        if not values:
            return None, []
        if not isinstance(values, dict) or len(values) > 8:
            raise MultimodalServiceError(
                "invalid_provider_options",
                "高级参数格式无效，请关闭高级设置后重试。",
                status_code=422,
            )
        audited = PROVIDER_OPTION_AUDIT.get(model_id)
        if audited is None:
            raise MultimodalServiceError(
                "provider_options_unsupported",
                "所选模型没有经过验证的高级参数，请关闭高级设置。",
                status_code=422,
            )
        provider_slug, _ = audited
        live_definitions = {
            option.key: option for option in profile.provider_options
        }
        normalized: dict[str, object] = {}
        for key, value in values.items():
            if key not in live_definitions:
                raise MultimodalServiceError(
                    "provider_option_unavailable",
                    "模型当前不再支持所选高级参数，请刷新能力后重试。",
                    status_code=422,
                )
            definition = live_definitions[key]
            if definition.type == "text":
                if not isinstance(value, str) or len(value) > 2_000:
                    raise MultimodalServiceError(
                        "invalid_provider_option_value",
                        f"“{definition.label}”必须是 2,000 字符以内的文本。",
                        status_code=422,
                    )
                normalized[key] = value.strip()
            elif definition.type == "boolean":
                if not isinstance(value, bool):
                    raise MultimodalServiceError(
                        "invalid_provider_option_value",
                        f"“{definition.label}”必须使用开或关。",
                        status_code=422,
                    )
                normalized[key] = value
            else:
                raise MultimodalServiceError(
                    "provider_option_not_implemented",
                    "该高级参数尚未完成安全适配，请关闭后重试。",
                    status_code=422,
                )
        return (
            {
                "options": {
                    provider_slug: {
                        "parameters": normalized,
                    }
                }
            },
            sorted(normalized),
        )

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

    @staticmethod
    def _model_id(value: str) -> str:
        model_id = str(value or "").strip()
        if not model_id or len(model_id) > 256:
            raise MultimodalServiceError(
                "invalid_model_id",
                "请选择有效的视频生成模型。",
                status_code=422,
            )
        return model_id

    @staticmethod
    def _prompt(value: str, *, required: bool = True) -> str:
        prompt = str(value or "").strip()
        if (required and not prompt) or len(prompt) > MAX_VIDEO_GENERATION_PROMPT_CHARS:
            raise MultimodalServiceError(
                "invalid_prompt",
                "视频描述需为 1–4000 个字符；视频增强可留空。",
                status_code=422,
            )
        return prompt

    @staticmethod
    def _idempotency_key(value: str) -> str:
        key = str(value or "").strip()
        if not IDEMPOTENCY_KEY_PATTERN.fullmatch(key):
            raise MultimodalServiceError(
                "invalid_idempotency_key",
                "提交标识无效，请刷新页面后重试。",
                status_code=422,
            )
        return key

    def _ensure_enabled(self) -> None:
        if not self.catalog_service._enabled(
            "MULTIMODAL_VIDEO_GENERATION_ENABLED"
        ):
            raise MultimodalServiceError(
                "video_generation_disabled",
                "视频生成当前未启用，请在服务设置中开启后重试。",
                status_code=503,
            )

    def _row(self, job_id: str) -> dict[str, object]:
        clean_id = str(job_id or "").strip()
        if not clean_id or len(clean_id) > 80:
            raise self._not_found()
        row = self.router_service.repository.get_video_job(
            self.router_service.tenant_id, clean_id
        )
        if row is None:
            raise self._not_found()
        return row

    def _target_for_row(
        self, row: dict[str, object]
    ) -> OpenRouterTarget:
        connection_id = str(row.get("connection_id") or "").strip()
        if not connection_id:
            return self.catalog_service.resolve_target()
        try:
            connection = self.router_service.repository.get_connection(
                self.router_service.tenant_id, connection_id
            )
            if connection.kind != "openrouter" or not connection.enabled:
                raise ValueError("not an OpenRouter connection")
            api_key = self.router_service.repository.resolve_api_key(
                self.router_service.tenant_id, connection_id
            )
        except Exception as exc:
            raise MultimodalServiceError(
                "video_job_connection_unavailable",
                "此任务原先使用的模型服务连接已不可用，请恢复该连接后刷新。",
                status_code=503,
            ) from exc
        return OpenRouterTarget(
            base_url=connection.base_url,
            api_key=api_key,
            connection_id=connection.id,
            cache_key=f"connection:{connection.id}",
        )

    def _update(self, job_id: str, **changes: object) -> dict[str, object]:
        row = self.router_service.repository.update_video_job(
            self.router_service.tenant_id, job_id, **changes
        )
        if row is None:
            raise self._not_found()
        return row

    def _upstream_changes(
        self,
        payload: dict[str, Any],
        *,
        previous: dict[str, object],
        submitting: bool,
    ) -> dict[str, object]:
        upstream_id = str(payload.get("id") or "").strip()
        if submitting and not upstream_id:
            raise MultimodalServiceError(
                "invalid_upstream_response",
                "视频服务未返回任务编号，请稍后查看账单并谨慎重试。",
                status_code=502,
            )
        if not upstream_id:
            upstream_id = str(previous.get("upstream_job_id") or "").strip()
        status = self._status(payload.get("status"))
        actual_model = (
            str(payload.get("model") or "").strip()
            or (
                str(previous["actual_model"])
                if previous.get("actual_model")
                else None
            )
        )
        generation_id = (
            str(payload.get("generation_id") or "").strip()
            or (
                str(previous["generation_id"])
                if previous.get("generation_id")
                else None
            )
        )
        outputs = payload.get("unsigned_urls")
        output_count = (
            len(outputs)
            if isinstance(outputs, list)
            else int(previous.get("output_count") or 0)
        )
        if status == "succeeded":
            output_count = max(1, output_count)
        usage = payload.get("usage")
        cost = self._cost(usage)
        if cost is None and previous.get("cost_usd") is not None:
            cost = float(previous["cost_usd"])
        error_code = None
        if status == "failed":
            error_code = "provider_generation_failed"
        elif status == "cancelled":
            error_code = "provider_generation_cancelled"
        elif status == "expired":
            error_code = "provider_generation_expired"
        return {
            "upstream_job_id": upstream_id or None,
            "status": status,
            "actual_model": actual_model,
            "generation_id": generation_id,
            "cost_usd": cost,
            "cost_kind": "actual" if cost is not None else "unavailable",
            "error_code": error_code,
            "output_count": max(0, output_count),
        }

    @staticmethod
    def _status(value: object) -> str:
        mapping = {
            "pending": "queued",
            "in_progress": "running",
            "completed": "succeeded",
            "failed": "failed",
            "cancelled": "cancelled",
            "expired": "expired",
        }
        status = mapping.get(str(value or "").strip().lower())
        if not status:
            raise MultimodalServiceError(
                "invalid_upstream_response",
                "视频服务返回了无法识别的任务状态，请稍后刷新。",
                status_code=502,
            )
        return status

    @staticmethod
    def _cost(usage: object) -> float | None:
        if not isinstance(usage, dict):
            return None
        value = usage.get("cost")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return max(0.0, float(value))
        return None

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
                    operation="generate_video",
                    connection_id=target.connection_id,
                    model_id=model_id,
                    reason_codes=[
                        "explicit_model",
                        "operation_generate_video",
                    ],
                    input_bytes=input_bytes,
                )
            )
        except Exception as exc:
            raise MultimodalServiceError(
                "audit_unavailable",
                "暂时无法建立视频生成审计记录，请稍后重试。",
                status_code=503,
            ) from exc

    def _update_audit(self, row: dict[str, object]) -> None:
        decision_id = str(row.get("decision_id") or "").strip()
        if not decision_id:
            return
        status = str(row.get("status") or "")
        try:
            if status == "succeeded":
                cost = row.get("cost_usd")
                self.router_service.repository.update_routing_decision_usage(
                    self.router_service.tenant_id,
                    decision_id,
                    outcome="success",
                    media_seconds=(
                        float(row["duration"])
                        if row.get("duration") is not None
                        else None
                    ),
                    settled_cost_usd=(
                        float(cost) if cost is not None else None
                    ),
                    cost_status=str(
                        row.get("cost_kind") or "unavailable"
                    ),
                )
            else:
                self.router_service.repository.update_routing_decision_outcome(
                    self.router_service.tenant_id,
                    decision_id,
                    status,
                )
        except Exception:
            logger.warning(
                "Unable to update video job audit outcome: %s", decision_id
            )

    def _record_failure(self, decision_id: str, outcome: str) -> None:
        try:
            self.router_service.repository.update_routing_decision_outcome(
                self.router_service.tenant_id, decision_id, outcome
            )
        except Exception:
            logger.warning(
                "Unable to update video job audit failure: %s", decision_id
            )

    @staticmethod
    def _public(row: dict[str, object]) -> VideoJob:
        error_code = str(row.get("error_code") or "").strip()
        (
            provider_option_keys,
            has_source_video,
            upscale_factor,
            creativity,
        ) = VideoJobService._stored_parameter_metadata(
            row.get("provider_option_keys")
        )
        return VideoJob(
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
            parameters=VideoJobParameters(
                task_type=("upscale" if has_source_video else "generate"),
                duration=(
                    int(row["duration"])
                    if row.get("duration") is not None
                    else None
                ),
                resolution=(
                    str(row["resolution"])
                    if row.get("resolution")
                    else None
                ),
                aspect_ratio=(
                    str(row["aspect_ratio"])
                    if row.get("aspect_ratio")
                    else None
                ),
                generate_audio=bool(row.get("generate_audio")),
                has_first_frame=bool(row.get("has_first_frame")),
                has_last_frame=bool(row.get("has_last_frame")),
                reference_image_count=max(
                    0, int(row.get("reference_image_count") or 0)
                ),
                has_source_video=has_source_video,
                upscale_factor=upscale_factor,
                creativity=creativity,
                provider_option_keys=provider_option_keys,
            ),
            usage=VideoJobUsage(
                cost_usd=(
                    float(row["cost_usd"])
                    if row.get("cost_usd") is not None
                    else None
                ),
                cost_kind=str(row.get("cost_kind") or "unavailable"),
            ),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            error=(
                VideoJobError(
                    code=error_code,
                    message=SAFE_JOB_ERRORS.get(
                        error_code,
                        "视频任务未成功，请检查参数后重新提交。",
                    ),
                )
                if error_code
                else None
            ),
            output_count=max(0, int(row.get("output_count") or 0)),
        )

    @staticmethod
    def _stored_parameter_metadata(
        raw: object,
    ) -> tuple[list[str], bool, float | None, int | None]:
        try:
            values = json.loads(str(raw or "[]"))
        except (TypeError, ValueError):
            return [], False, None, None
        if not isinstance(values, list):
            return [], False, None, None
        has_source_video = "source_video" in values
        upscale_factor: float | None = None
        creativity: int | None = None
        for value in values:
            if not isinstance(value, str):
                continue
            if value.startswith("upscale_factor:"):
                try:
                    upscale_factor = float(value.split(":", 1)[1])
                except ValueError:
                    pass
            elif value.startswith("creativity:"):
                try:
                    creativity = int(value.split(":", 1)[1])
                except ValueError:
                    pass
        option_keys = sorted(
            {
                value
                for value in values
                if isinstance(value, str)
                and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", value)
                and value != "source_video"
            }
        )
        return option_keys, has_source_video, upscale_factor, creativity

    @staticmethod
    def _not_found() -> MultimodalServiceError:
        return MultimodalServiceError(
            "video_job_not_found",
            "未找到该视频任务，它可能已被移除。",
            status_code=404,
        )
