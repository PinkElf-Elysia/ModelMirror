from __future__ import annotations

import base64
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

try:
    from server.model_router.egress import ProviderEgressPolicy, request_provider_url
    from server.model_router.service import ModelRouterService
except ModuleNotFoundError:
    from model_router.egress import ProviderEgressPolicy, request_provider_url
    from model_router.service import ModelRouterService

from .stt import MultimodalServiceError, OpenRouterTarget
from .video_catalog import VideoCatalogService


logger = logging.getLogger("modelmirror.multimodal")

MAX_VIDEO_BYTES = 20 * 1024 * 1024
MAX_VIDEO_PROMPT_CHARS = 4_000
MAX_VIDEO_URL_CHARS = 2_048

VIDEO_FORMATS: dict[str, tuple[str, tuple[str, ...]]] = {
    "mp4": ("video/mp4", ("video/mp4", "application/octet-stream")),
    "mpeg": ("video/mpeg", ("video/mpeg", "application/octet-stream")),
    "mpg": ("video/mpeg", ("video/mpeg", "application/octet-stream")),
    "mov": (
        "video/quicktime",
        ("video/quicktime", "video/mov", "application/octet-stream"),
    ),
    "webm": ("video/webm", ("video/webm", "application/octet-stream")),
}


def video_magic_matches(extension: str, content: bytes) -> bool:
    if extension in {"mp4", "mov"}:
        return len(content) >= 12 and content[4:8] == b"ftyp"
    if extension == "webm":
        return content.startswith(b"\x1a\x45\xdf\xa3")
    return content.startswith((b"\x00\x00\x01\xba", b"\x00\x00\x01\xb3"))


def video_file_data_url(
    filename: str,
    content_type: str | None,
    content: bytes,
) -> str:
    if not content:
        raise MultimodalServiceError(
            "empty_video",
            "视频文件为空，请重新选择文件。",
            status_code=422,
        )
    if len(content) > MAX_VIDEO_BYTES:
        raise MultimodalServiceError(
            "video_too_large",
            "视频文件不能超过 20 MiB，请压缩或缩短后重试。",
            status_code=413,
        )
    extension = Path(filename).suffix.lower().lstrip(".")
    profile = VIDEO_FORMATS.get(extension)
    if profile is None:
        raise MultimodalServiceError(
            "unsupported_video_format",
            "仅支持 MP4、MPEG、MOV 和 WebM 视频。",
            status_code=422,
        )
    mime, allowed_mimes = profile
    normalized_type = str(
        content_type or "application/octet-stream"
    ).lower()
    if normalized_type not in allowed_mimes or not video_magic_matches(
        extension, content
    ):
        raise MultimodalServiceError(
            "invalid_video_file",
            "文件内容与视频格式不匹配，请选择有效的视频文件。",
            status_code=422,
        )
    encoded = base64.b64encode(content).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def validated_video_url(value: str) -> str:
    url = str(value or "").strip()
    if len(url) > MAX_VIDEO_URL_CHARS:
        raise MultimodalServiceError(
            "invalid_video_url",
            "视频网址过长，请使用有效的 HTTPS 直链或 YouTube 链接。",
            status_code=422,
        )
    parsed = urlsplit(url)
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise MultimodalServiceError(
            "invalid_video_url",
            "仅支持不含账号信息的 HTTPS 视频直链或 YouTube 链接。",
            status_code=422,
        )
    return url


@dataclass(frozen=True)
class VideoAnalysisUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None
    cost_kind: str = "unavailable"


@dataclass(frozen=True)
class VideoAnalysisResult:
    text: str
    requested_model: str
    actual_model: str
    provider: str
    request_id: str
    source_kind: Literal["file", "url"]
    usage: VideoAnalysisUsage


class OpenRouterVideoAnalysisAdapter:
    def __init__(
        self,
        *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        egress_policy: ProviderEgressPolicy | None = None,
    ) -> None:
        self._client_factory = client_factory or self._default_client
        self._managed_client_factory = client_factory or self._direct_client
        self._egress_policy = egress_policy

    async def analyze(
        self,
        target: OpenRouterTarget,
        *,
        model_id: str,
        prompt: str,
        video_source: str,
    ) -> tuple[str, str, VideoAnalysisUsage]:
        payload = {
            "model": model_id,
            "stream": False,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "video_url",
                            "video_url": {"url": video_source},
                        },
                    ],
                }
            ],
        }
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
                    self._api_url(target.base_url),
                    headers=self._headers(target.api_key),
                    json=payload,
                )
            except (
                httpx.ConnectTimeout,
                httpx.ReadTimeout,
                httpx.WriteTimeout,
            ) as exc:
                raise MultimodalServiceError(
                    "upstream_timeout",
                    "视频分析超时。请缩短视频、压缩文件或稍后重试。",
                    status_code=504,
                ) from exc
            except httpx.HTTPError as exc:
                raise MultimodalServiceError(
                    "upstream_unreachable",
                    "暂时无法连接视频分析服务，请检查网络后重试。",
                    status_code=502,
                ) from exc
        self._raise_for_status(response, model_id=model_id)
        try:
            body = response.json()
        except ValueError as exc:
            raise MultimodalServiceError(
                "invalid_upstream_response",
                "视频分析服务返回了无法识别的结果，请稍后重试。",
                status_code=502,
            ) from exc
        text = self._text(body)
        if not text:
            raise MultimodalServiceError(
                "empty_video_analysis",
                "模型没有返回可用的视频分析内容，请更换问题或模型后重试。",
                status_code=502,
            )
        actual_model = (
            str(body.get("model") or "").strip()
            if isinstance(body, dict)
            else ""
        ) or model_id
        usage = self._usage(body.get("usage") if isinstance(body, dict) else None)
        return text, actual_model, usage

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
    def _api_url(base_url: str) -> str:
        root = str(base_url or "").strip().rstrip("/")
        if root.lower().endswith("/chat/completions"):
            return root
        if not root.lower().endswith("/v1"):
            root = f"{root}/v1"
        return f"{root}/chat/completions"

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
    def _text(payload: object) -> str:
        if not isinstance(payload, dict):
            return ""
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        choice = choices[0]
        message = choice.get("message") if isinstance(choice, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            texts = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                value = part.get("text")
                if isinstance(value, str) and value.strip():
                    texts.append(value.strip())
            return "\n".join(texts)
        return ""

    @staticmethod
    def _usage(raw: object) -> VideoAnalysisUsage:
        usage = raw if isinstance(raw, dict) else {}

        def number(name: str) -> float | None:
            value = usage.get(name)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return max(0.0, float(value))
            return None

        def integer(*names: str) -> int | None:
            for name in names:
                value = number(name)
                if value is not None:
                    return int(value)
            return None

        cost = number("cost")
        return VideoAnalysisUsage(
            input_tokens=integer("input_tokens", "prompt_tokens"),
            output_tokens=integer("output_tokens", "completion_tokens"),
            total_tokens=integer("total_tokens"),
            cost_usd=cost,
            cost_kind="actual" if cost is not None else "unavailable",
        )

    @staticmethod
    def _raise_for_status(response: httpx.Response, *, model_id: str) -> None:
        status = response.status_code
        if status < 400:
            return
        if status in {401, 403}:
            raise MultimodalServiceError(
                "provider_credentials_invalid",
                "OpenRouter 密钥无效或没有视频分析权限，请更新模型服务连接。",
                status_code=502,
            )
        if status == 402:
            raise MultimodalServiceError(
                "provider_quota_exceeded",
                "OpenRouter 余额或预算不足，本次未进行视频分析。",
                status_code=402,
            )
        if status == 413:
            raise MultimodalServiceError(
                "provider_file_too_large",
                "上游拒绝了视频大小，请压缩或缩短视频后重试。",
                status_code=413,
            )
        if status == 404:
            raise MultimodalServiceError(
                "model_unavailable",
                f"未找到视频分析模型 {model_id}，请刷新模型目录后重新选择。",
                status_code=422,
            )
        if status == 429:
            raise MultimodalServiceError(
                "provider_rate_limited",
                "视频分析请求过多，请稍后重试。",
                status_code=429,
            )
        if status >= 500:
            raise MultimodalServiceError(
                "provider_unavailable",
                "视频分析服务暂时不可用，请稍后重试。",
                status_code=502,
            )
        raise MultimodalServiceError(
            "provider_rejected_request",
            "视频分析请求未被接受，请检查模型、视频格式和问题内容。",
            status_code=422,
        )


class VideoAnalysisService:
    def __init__(
        self,
        router_service: ModelRouterService,
        catalog_service: VideoCatalogService,
        *,
        adapter: OpenRouterVideoAnalysisAdapter | None = None,
    ) -> None:
        self.router_service = router_service
        self.catalog_service = catalog_service
        self.adapter = adapter or OpenRouterVideoAnalysisAdapter(
            egress_policy=router_service.egress_policy
        )

    async def analyze(
        self,
        *,
        model_id: str,
        prompt: str,
        source_type: str,
        filename: str | None = None,
        content_type: str | None = None,
        content: bytes | None = None,
        video_url: str | None = None,
    ) -> VideoAnalysisResult:
        if not self.catalog_service._enabled(
            "MULTIMODAL_VIDEO_ANALYSIS_ENABLED"
        ):
            raise MultimodalServiceError(
                "video_analysis_disabled",
                "视频分析当前未启用，请在服务设置中开启后重试。",
                status_code=503,
            )
        clean_model = self._model_id(model_id)
        clean_prompt = self._prompt(prompt)
        clean_source_type = self._source_type(source_type)
        if clean_source_type == "file":
            if video_url or content is None or filename is None:
                raise self._source_error()
            video_source = self._file_source(filename, content_type, content)
            input_bytes = len(content)
        else:
            if content is not None or filename is not None or not video_url:
                raise self._source_error()
            video_source = self._url_source(video_url)
            input_bytes = 0
        await self._verify_model(clean_model, clean_source_type)
        target = self.catalog_service.resolve_target()
        decision_id = self._record_start(
            target, model_id=clean_model, input_bytes=input_bytes
        )
        try:
            text, actual_model, usage = await self.adapter.analyze(
                target,
                model_id=clean_model,
                prompt=clean_prompt,
                video_source=video_source,
            )
        except MultimodalServiceError as exc:
            self._record_failure(decision_id, exc.code)
            raise
        self._record_success(decision_id, usage)
        return VideoAnalysisResult(
            text=text,
            requested_model=clean_model,
            actual_model=actual_model,
            provider="openrouter",
            request_id=decision_id,
            source_kind=clean_source_type,
            usage=usage,
        )

    async def _verify_model(
        self,
        model_id: str,
        source_type: Literal["file", "url"],
    ) -> None:
        catalog = await self.catalog_service.get_catalog()
        if catalog.status == "disabled":
            raise MultimodalServiceError(
                "video_analysis_disabled",
                "视频分析当前未启用，请在服务设置中开启后重试。",
                status_code=503,
            )
        if catalog.status == "offline":
            raise MultimodalServiceError(
                "video_catalog_unavailable",
                "暂时无法确认模型的视频能力，请检查 OpenRouter 连接后重试。",
                status_code=503,
            )
        for profile in catalog.profiles:
            if (
                profile.model_id == model_id
                and profile.operation == "analyze_video"
                and source_type in profile.supported_input_sources
            ):
                return
        raise MultimodalServiceError(
            "operation_mismatch",
            "所选模型未确认支持视频分析，请选择标有“视频理解”的模型。",
            status_code=422,
        )

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
                    operation="analyze_video",
                    connection_id=target.connection_id,
                    model_id=model_id,
                    reason_codes=[
                        "explicit_model",
                        "operation_analyze_video",
                    ],
                    input_bytes=input_bytes,
                )
            )
        except Exception as exc:
            raise MultimodalServiceError(
                "audit_unavailable",
                "暂时无法建立视频分析审计记录，请稍后重试。",
                status_code=503,
            ) from exc

    def _record_failure(self, decision_id: str, outcome: str) -> None:
        try:
            self.router_service.repository.update_routing_decision_outcome(
                self.router_service.tenant_id, decision_id, outcome
            )
        except Exception:
            logger.warning("Unable to update video analysis audit outcome: %s", decision_id)

    def _record_success(
        self, decision_id: str, usage: VideoAnalysisUsage
    ) -> None:
        try:
            self.router_service.repository.update_routing_decision_usage(
                self.router_service.tenant_id,
                decision_id,
                outcome="success",
                media_seconds=None,
                settled_cost_usd=usage.cost_usd,
                cost_status=usage.cost_kind,
            )
        except Exception:
            logger.warning("Unable to update video analysis audit usage: %s", decision_id)

    @staticmethod
    def _model_id(value: str) -> str:
        model_id = str(value or "").strip()
        if (
            not model_id
            or len(model_id) > 256
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", model_id)
        ):
            raise MultimodalServiceError(
                "invalid_model_id",
                "请选择一个有效的视频分析模型。",
                status_code=422,
            )
        return model_id

    @staticmethod
    def _prompt(value: str) -> str:
        prompt = str(value or "").strip()
        if not prompt or len(prompt) > MAX_VIDEO_PROMPT_CHARS:
            raise MultimodalServiceError(
                "invalid_prompt",
                "请输入 1–4000 个字符的视频分析问题。",
                status_code=422,
            )
        return prompt

    @staticmethod
    def _source_type(value: str) -> Literal["file", "url"]:
        if value not in {"file", "url"}:
            raise MultimodalServiceError(
                "invalid_source_type",
                "请选择上传本地视频或使用视频网址。",
                status_code=422,
            )
        return value

    @staticmethod
    def _source_error() -> MultimodalServiceError:
        return MultimodalServiceError(
            "invalid_video_source",
            "请只提交一种视频来源：本地文件或 HTTPS 视频网址。",
            status_code=422,
        )

    @staticmethod
    def _file_source(
        filename: str,
        content_type: str | None,
        content: bytes,
    ) -> str:
        return video_file_data_url(filename, content_type, content)

    @staticmethod
    def _magic_matches(extension: str, content: bytes) -> bool:
        return video_magic_matches(extension, content)

    @staticmethod
    def _url_source(value: str) -> str:
        return validated_video_url(value)
