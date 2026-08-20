from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator, Literal, Mapping
from urllib.parse import SplitResult, urlsplit, urlunsplit

import httpx

from .egress import ProviderEgressPolicy


PROVIDER_CHAT_CONTRACT_VERSION = "modelmirror-provider-chat-v1"
ProviderChatSource = Literal["static", "managed", "sidecar"]


@dataclass(frozen=True, slots=True)
class ProviderChatEndpoints:
    base_url: str
    models_url: str
    chat_completions_url: str


class ProviderChatEndpointResolver:
    """Resolve OpenAI-compatible model and text-chat endpoints once."""

    @classmethod
    def resolve(cls, value: str) -> ProviderChatEndpoints:
        parsed = cls._parse(value)
        path = parsed.path.rstrip("/")
        lower_path = path.casefold()
        if lower_path.endswith("/chat/completions"):
            api_path = path[: -len("/chat/completions")]
        elif lower_path.endswith("/models"):
            api_path = path[: -len("/models")]
        elif lower_path.endswith("/v1"):
            api_path = path
        else:
            api_path = f"{path}/v1" if path else "/v1"
        api_path = api_path or "/v1"
        base_url = urlunsplit((parsed.scheme, parsed.netloc, api_path, "", ""))
        return ProviderChatEndpoints(
            base_url=base_url,
            models_url=f"{base_url}/models",
            chat_completions_url=f"{base_url}/chat/completions",
        )

    @staticmethod
    def _parse(value: str) -> SplitResult:
        parsed = urlsplit(str(value or "").strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("provider_chat_invalid_base_url")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("provider_chat_invalid_base_url")
        try:
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("provider_chat_invalid_base_url") from exc
        return parsed


@dataclass(frozen=True, slots=True)
class ProviderChatTarget:
    source: ProviderChatSource
    provider_kind: str
    endpoints: ProviderChatEndpoints
    connection_id: str | None = None
    _api_key: str = field(default="", repr=False, compare=False)

    @classmethod
    def create(
        cls,
        *,
        source: ProviderChatSource,
        provider_kind: str,
        base_url: str,
        api_key: str,
        connection_id: str | None = None,
    ) -> ProviderChatTarget:
        if source == "managed" and not connection_id:
            raise ValueError("provider_chat_managed_connection_id_required")
        if source != "managed" and connection_id:
            raise ValueError("provider_chat_connection_id_not_allowed")
        return cls(
            source=source,
            provider_kind=str(provider_kind or "openai_compatible"),
            endpoints=ProviderChatEndpointResolver.resolve(base_url),
            connection_id=connection_id,
            _api_key=str(api_key or ""),
        )

    @property
    def base_url(self) -> str:
        return self.endpoints.base_url

    @property
    def models_url(self) -> str:
        return self.endpoints.models_url

    @property
    def chat_completions_url(self) -> str:
        return self.endpoints.chat_completions_url

    @property
    def api_key(self) -> str:
        return self._api_key

    def authorization_headers(
        self, extra: Mapping[str, str] | None = None
    ) -> dict[str, str]:
        headers = dict(extra or {})
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers


class ProviderChatTransport:
    """Send text-chat requests while preserving each target's trust boundary."""

    def __init__(self, egress_policy: ProviderEgressPolicy) -> None:
        self.egress_policy = egress_policy

    @staticmethod
    def client_kwargs(*, certification: bool = False) -> dict[str, object]:
        timeout = (
            httpx.Timeout(connect=5, read=45, write=30, pool=5)
            if certification
            else httpx.Timeout(connect=15, read=None, write=30, pool=10)
        )
        return {
            "timeout": timeout,
            "follow_redirects": False,
            "trust_env": False,
            "transport": httpx.AsyncHTTPTransport(retries=0),
        }

    async def send_stream(
        self,
        client: httpx.AsyncClient,
        target: ProviderChatTarget,
        payload: Mapping[str, object],
        *,
        headers: Mapping[str, str] | None = None,
        certification: bool = False,
    ) -> httpx.Response:
        request_headers = target.authorization_headers(headers)
        if target.source == "managed":
            if certification:
                approved = await self.egress_policy.authorize(
                    target.chat_completions_url
                )
                request = client.build_request(
                    "POST",
                    approved.pinned_urls[0],
                    headers=approved.request_headers(request_headers),
                    extensions=approved.extensions,
                    json=dict(payload),
                )
                return await client.send(
                    request,
                    stream=True,
                    follow_redirects=False,
                )
            return await self.egress_policy.request(
                client,
                "POST",
                target.chat_completions_url,
                headers=request_headers,
                json=dict(payload),
                stream=True,
            )
        request = client.build_request(
            "POST",
            target.chat_completions_url,
            headers=request_headers,
            json=dict(payload),
        )
        return await client.send(
            request,
            stream=True,
        )

    @asynccontextmanager
    async def stream(
        self,
        client: httpx.AsyncClient,
        target: ProviderChatTarget,
        payload: Mapping[str, object],
        *,
        headers: Mapping[str, str] | None = None,
        certification: bool = False,
    ) -> AsyncIterator[httpx.Response]:
        response = await self.send_stream(
            client,
            target,
            payload,
            headers=headers,
            certification=certification,
        )
        try:
            yield response
        finally:
            await response.aclose()
