from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field

try:
    from server.model_router.egress import request_provider_url
except ModuleNotFoundError:
    from model_router.egress import request_provider_url

from .image_catalog import ImageCatalogService, ImageModelProfile
from .stt import MultimodalServiceError, OpenRouterTarget


MAX_IMAGE_REFERENCE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_OUTPUT_BYTES = 25 * 1024 * 1024
MAX_IMAGE_OUTPUT_TOTAL_BYTES = 60 * 1024 * 1024
ALLOWED_REFERENCE_FORMATS = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
}
ALLOWED_OUTPUT_MEDIA_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/svg+xml",
}
RECRAFT_STYLE_MODEL_IDS = frozenset(
    {
        "recraft/recraft-v4-styles",
        "recraft/recraft-v4-styles-pro",
        "recraft/recraft-v4-styles-pro-vector",
        "recraft/recraft-v4-styles-vector",
    }
)
RECRAFT_STYLE_MIN_REFERENCE_EDGE_PX = 256


class ImageGenerationItem(BaseModel):
    data_url: str
    media_type: str
    output_bytes: int


class ImageGenerationUsage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None
    cost_kind: str = "unavailable"


class ImageGenerationResult(BaseModel):
    requested_model: str
    actual_model: str
    provider: str = "openrouter"
    request_id: str
    images: list[ImageGenerationItem] = Field(default_factory=list)
    usage: ImageGenerationUsage


class ImageGenerationService:
    def __init__(
        self,
        catalog_service: ImageCatalogService,
        *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self.catalog_service = catalog_service
        self.client_factory = client_factory or (
            lambda: httpx.AsyncClient(
                timeout=httpx.Timeout(180.0, connect=10.0),
                follow_redirects=False,
                trust_env=False,
            )
        )

    async def generate(
        self,
        *,
        model_id: str,
        prompt: str,
        n: int = 1,
        resolution: str | None = None,
        aspect_ratio: str | None = None,
        quality: str | None = None,
        output_format: str | None = None,
        background: str | None = None,
        seed: int | None = None,
        reference_filenames: list[str],
        reference_content_types: list[str | None],
        reference_contents: list[bytes],
    ) -> ImageGenerationResult:
        clean_model = model_id.strip()
        clean_prompt = prompt.strip()
        if not clean_model or len(clean_model) > 256:
            raise MultimodalServiceError(
                "invalid_image_model",
                "请选择可用的图片生成模型。",
                status_code=422,
            )
        if not clean_prompt or len(clean_prompt) > 4_000:
            raise MultimodalServiceError(
                "invalid_image_prompt",
                "创作描述需为 1–4,000 个字符。",
                status_code=422,
            )
        profile = await self._profile(clean_model)
        self._validate_value(profile, "n", n, default=1)
        self._validate_value(profile, "resolution", resolution)
        self._validate_value(profile, "aspect_ratio", aspect_ratio)
        self._validate_value(profile, "quality", quality)
        self._validate_value(profile, "output_format", output_format)
        self._validate_value(profile, "background", background)
        self._validate_value(profile, "seed", seed)
        references = self._references(
            profile,
            reference_filenames,
            reference_content_types,
            reference_contents,
        )

        payload: dict[str, Any] = {
            "model": clean_model,
            "prompt": clean_prompt,
        }
        for key, value in (
            ("n", n if n != 1 else None),
            ("resolution", resolution),
            ("aspect_ratio", aspect_ratio),
            ("quality", quality),
            ("output_format", output_format),
            ("background", background),
            ("seed", seed),
        ):
            if value is not None:
                payload[key] = value
        if references:
            payload["input_references"] = [
                {
                    "type": "image_url",
                    "image_url": {"url": data_url},
                }
                for data_url in references
            ]

        target = self.catalog_service.resolve_target()
        response = await self._request(target, payload)
        return self._result(clean_model, response)

    async def _profile(self, model_id: str) -> ImageModelProfile:
        catalog = await self.catalog_service.get_catalog()
        profile = next(
            (
                item
                for item in catalog.profiles
                if item.model_id == model_id
                and item.operation == "generate_image"
                and item.invocable
                and item.interaction_status == "ready"
            ),
            None,
        )
        if profile is None:
            raise MultimodalServiceError(
                "image_model_not_ready",
                "该模型当前没有已确认的图片生成能力，请刷新模型目录或选择其他模型。",
                status_code=422,
            )
        return profile

    async def _request(
        self,
        target: OpenRouterTarget,
        payload: dict[str, Any],
    ) -> httpx.Response:
        try:
            async with self.client_factory() as client:
                response = await request_provider_url(
                    client,
                    self.catalog_service.router_service.egress_policy,
                    target.connection_id,
                    "POST",
                    self.catalog_service._api_url(target.base_url, "images"),
                    headers={"Authorization": f"Bearer {target.api_key}"},
                    json=payload,
                )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
            raise MultimodalServiceError(
                "image_generation_timeout",
                "图片生成连接超时，请稍后重试。",
                status_code=504,
            ) from exc
        except httpx.HTTPError as exc:
            raise MultimodalServiceError(
                "image_generation_unavailable",
                "图片生成服务暂时不可用，请稍后重试。",
                status_code=503,
            ) from exc
        self._raise_for_status(response)
        return response

    @classmethod
    def _result(
        cls,
        requested_model: str,
        response: httpx.Response,
    ) -> ImageGenerationResult:
        try:
            payload = response.json()
        except ValueError as exc:
            raise MultimodalServiceError(
                "invalid_image_response",
                "图片生成返回了无法读取的结果，请重试或更换模型。",
                status_code=502,
            ) from exc
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list) or not data:
            raise MultimodalServiceError(
                "empty_image_response",
                "模型没有返回完整图片，请重试或更换模型。",
                status_code=502,
            )
        images: list[ImageGenerationItem] = []
        total_bytes = 0
        for item in data:
            if not isinstance(item, dict):
                continue
            encoded = str(item.get("b64_json") or "").strip()
            claimed_media_type = str(item.get("media_type") or "").lower()
            if not encoded:
                continue
            try:
                content = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError):
                continue
            if not content or len(content) > MAX_IMAGE_OUTPUT_BYTES:
                continue
            media_type = cls._detect_output_media_type(
                content,
                claimed_media_type,
            )
            if media_type is None:
                continue
            if not cls._matches_output_magic(content, media_type):
                continue
            total_bytes += len(content)
            if total_bytes > MAX_IMAGE_OUTPUT_TOTAL_BYTES:
                raise MultimodalServiceError(
                    "image_output_too_large",
                    "生成图片总大小超出安全限制，请减少生成数量。",
                    status_code=502,
                )
            images.append(
                ImageGenerationItem(
                    data_url=f"data:{media_type};base64,{encoded}",
                    media_type=media_type,
                    output_bytes=len(content),
                )
            )
        if not images:
            raise MultimodalServiceError(
                "damaged_image_response",
                "模型返回的图片不完整或格式不受支持，请重试或更换模型。",
                status_code=502,
            )
        usage = payload.get("usage") if isinstance(payload, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        cost = usage.get("cost")
        cost_usd = float(cost) if isinstance(cost, (int, float)) else None
        actual_model = str(payload.get("model") or requested_model)
        request_id = (
            response.headers.get("x-request-id")
            or str(payload.get("id") or "")
            or "unavailable"
        )
        return ImageGenerationResult(
            requested_model=requested_model,
            actual_model=actual_model,
            request_id=request_id,
            images=images,
            usage=ImageGenerationUsage(
                input_tokens=cls._integer(usage.get("prompt_tokens")),
                output_tokens=cls._integer(usage.get("completion_tokens")),
                total_tokens=cls._integer(usage.get("total_tokens")),
                cost_usd=cost_usd,
                cost_kind="actual" if cost_usd is not None else "unavailable",
            ),
        )

    @classmethod
    def _references(
        cls,
        profile: ImageModelProfile,
        filenames: list[str],
        content_types: list[str | None],
        contents: list[bytes],
    ) -> list[str]:
        descriptor = profile.supported_parameters.get("input_references")
        minimum = int(descriptor.min or 0) if descriptor is not None else 0
        if not contents:
            if minimum > 0:
                raise MultimodalServiceError(
                    "not_enough_image_references",
                    f"该模型至少需要 {minimum} 张风格参考图。",
                    status_code=422,
                )
            return []
        if descriptor is None:
            raise MultimodalServiceError(
                "image_references_not_supported",
                "该模型不支持参考图，请移除图片后重试。",
                status_code=422,
            )
        if not (len(filenames) == len(content_types) == len(contents)):
            raise MultimodalServiceError(
                "invalid_image_references",
                "参考图信息不完整，请重新选择图片。",
                status_code=422,
            )
        maximum = int(descriptor.max or 1)
        if len(contents) < minimum:
            raise MultimodalServiceError(
                "not_enough_image_references",
                f"该模型至少需要 {minimum} 张风格参考图。",
                status_code=422,
            )
        if len(contents) > maximum:
            raise MultimodalServiceError(
                "too_many_image_references",
                f"该模型最多支持 {maximum} 张参考图。",
                status_code=422,
            )
        result: list[str] = []
        for filename, content_type, content in zip(
            filenames, content_types, contents, strict=True
        ):
            suffix = Path(filename).suffix.lower().lstrip(".")
            media_type = ALLOWED_REFERENCE_FORMATS.get(suffix)
            if media_type is None or content_type not in {
                media_type,
                "image/jpg" if media_type == "image/jpeg" else media_type,
                "application/octet-stream",
            }:
                raise MultimodalServiceError(
                    "invalid_image_reference_format",
                    "参考图仅支持 JPG、PNG 或 WebP。",
                    status_code=422,
                )
            if not content or len(content) > MAX_IMAGE_REFERENCE_BYTES:
                raise MultimodalServiceError(
                    "invalid_image_reference_size",
                    "每张参考图需小于 10 MiB。",
                    status_code=413,
                )
            if not cls._matches_magic(content, suffix):
                raise MultimodalServiceError(
                    "invalid_image_reference_content",
                    "参考图内容与文件格式不一致，请重新导出后上传。",
                    status_code=422,
                )
            if profile.model_id in RECRAFT_STYLE_MODEL_IDS:
                try:
                    with Image.open(BytesIO(content)) as image:
                        width, height = image.size
                except (OSError, UnidentifiedImageError) as exc:
                    raise MultimodalServiceError(
                        "invalid_image_reference_content",
                        "参考图内容无法读取，请重新导出后上传。",
                        status_code=422,
                    ) from exc
                if min(width, height) < RECRAFT_STYLE_MIN_REFERENCE_EDGE_PX:
                    raise MultimodalServiceError(
                        "image_reference_too_small",
                        "Recraft 风格参考图的短边至少需要 256 像素。",
                        status_code=422,
                    )
            result.append(
                f"data:{media_type};base64,{base64.b64encode(content).decode('ascii')}"
            )
        return result

    @staticmethod
    def _validate_value(
        profile: ImageModelProfile,
        key: str,
        value: str | int | None,
        *,
        default: int | None = None,
    ) -> None:
        if value is None or value == default:
            return
        descriptor = profile.supported_parameters.get(key)
        if descriptor is None:
            raise MultimodalServiceError(
                "unsupported_image_parameter",
                f"当前模型不支持参数 {key}，请刷新能力或恢复默认值。",
                status_code=422,
            )
        if descriptor.values and str(value) not in descriptor.values:
            raise MultimodalServiceError(
                "invalid_image_parameter",
                f"参数 {key} 不在当前模型允许范围内。",
                status_code=422,
            )
        if isinstance(value, int):
            if descriptor.min is not None and value < descriptor.min:
                raise MultimodalServiceError(
                    "invalid_image_parameter",
                    f"参数 {key} 低于当前模型允许范围。",
                    status_code=422,
                )
            if descriptor.max is not None and value > descriptor.max:
                raise MultimodalServiceError(
                    "invalid_image_parameter",
                    f"参数 {key} 超出当前模型允许范围。",
                    status_code=422,
                )

    @staticmethod
    def _matches_magic(content: bytes, suffix: str) -> bool:
        if suffix in {"jpg", "jpeg"}:
            return content.startswith(b"\xff\xd8\xff")
        if suffix == "png":
            return content.startswith(b"\x89PNG\r\n\x1a\n")
        if suffix == "webp":
            return len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP"
        return False

    @staticmethod
    def _matches_output_magic(content: bytes, media_type: str) -> bool:
        if media_type == "image/jpeg":
            return content.startswith(b"\xff\xd8\xff")
        if media_type == "image/png":
            return content.startswith(b"\x89PNG\r\n\x1a\n")
        if media_type == "image/webp":
            return (
                len(content) >= 12
                and content[:4] == b"RIFF"
                and content[8:12] == b"WEBP"
            )
        if media_type == "image/svg+xml":
            return content.lstrip().startswith((b"<svg", b"<?xml"))
        return False

    @classmethod
    def _detect_output_media_type(
        cls,
        content: bytes,
        claimed: str,
    ) -> str | None:
        if claimed in ALLOWED_OUTPUT_MEDIA_TYPES:
            return claimed
        for media_type in ALLOWED_OUTPUT_MEDIA_TYPES:
            if cls._matches_output_magic(content, media_type):
                return media_type
        return None

    @staticmethod
    def _integer(value: Any) -> int | None:
        return int(value) if isinstance(value, (int, float)) else None

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        status = response.status_code
        if status < 400:
            return
        if status in {401, 403}:
            code, message = "invalid_image_key", "图片服务密钥无效或权限不足，请在设置中重新测试连接。"
        elif status == 402:
            code, message = "image_budget_exceeded", "当前额度不足，图片任务未完成。请补充额度或选择成本更低的模型。"
        elif status == 429:
            code, message = "image_rate_limited", "图片生成请求过于频繁，请稍后重试。"
        elif status >= 500:
            code, message = "image_provider_unavailable", "图片生成服务暂时不可用，请稍后重试或更换模型。"
        else:
            code, message = "image_request_rejected", "图片生成请求未被接受，请检查模型和参数后重试。"
        raise MultimodalServiceError(code, message, status_code=status)
