from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from .config import OmniRouteSettings


class OmniRouteClientError(RuntimeError):
    def __init__(self, status_code: int, public_message: str):
        super().__init__(public_message)
        self.status_code = status_code
        self.public_message = public_message


def _public_error(status_code: int) -> str:
    if status_code == 401:
        return "OmniRoute 认证失败，请检查后端专用 API Key。"
    if status_code == 402:
        return "OmniRoute 预算或额度不足。"
    if status_code == 403:
        return "OmniRoute API Key 没有访问该资源的权限。"
    if status_code == 404:
        return "OmniRoute 未找到请求的资源。"
    if status_code == 429:
        return "OmniRoute 请求过于频繁，请稍后重试。"
    if status_code >= 500:
        return "OmniRoute 服务暂时不可用。"
    return "OmniRoute 返回了无法处理的响应。"


class OmniRouteClient:
    def __init__(
        self,
        settings: OmniRouteSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.settings = settings
        self._transport = transport

    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Accept": "application/json",
            "User-Agent": "ModelMirror/OmniRouteAdapter",
        }

    async def _get(self, path: str) -> dict[str, Any]:
        timeout = httpx.Timeout(self.settings.request_timeout_seconds)
        try:
            async with httpx.AsyncClient(
                base_url=self.settings.base_url,
                timeout=timeout,
                transport=self._transport,
            ) as client:
                response = await client.get(path, headers=self.headers())
        except httpx.TimeoutException as exc:
            raise OmniRouteClientError(504, "连接 OmniRoute 超时。") from exc
        except httpx.HTTPError as exc:
            raise OmniRouteClientError(502, "无法连接 OmniRoute 服务。") from exc

        if response.status_code >= 400:
            raise OmniRouteClientError(
                response.status_code,
                _public_error(response.status_code),
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise OmniRouteClientError(502, "OmniRoute 返回了无效 JSON。") from exc
        if not isinstance(payload, dict):
            raise OmniRouteClientError(502, "OmniRoute 返回了无效数据结构。")
        return payload

    async def fetch_models(self) -> dict[str, Any]:
        return await self._get("/v1/models?configuredOnly=true")

    async def fetch_route_candidates(self, channel: str) -> dict[str, Any]:
        encoded_channel = quote(channel, safe=":_-")
        try:
            return await self._get(f"/v1/auto-combo/{encoded_channel}/candidates")
        except OmniRouteClientError as exc:
            if exc.status_code != 404:
                raise
        return await self._get(f"/api/v1/auto-combo/{encoded_channel}/candidates")
