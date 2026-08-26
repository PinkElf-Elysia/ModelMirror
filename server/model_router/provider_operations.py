from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Mapping
from urllib.parse import urlsplit, urlunsplit

import httpx

from .egress import AuthorizedProviderTarget, ProviderEgressPolicy
from .provider_chat import ProviderChatEndpointResolver


PROVIDER_OPERATION_CONTRACT_VERSION = "modelmirror-provider-operation-v1"
OPENROUTER_BATCHES_URL = "https://openrouter.ai/api/beta/batches"
ProviderOperation = Literal[
    "embedding_vectors",
    "rerank_documents",
    "openrouter_batch_chat",
    "openrouter_batch_embeddings",
]
ProviderRerankAccessMode = Literal["dedicated", "llm_json"]


def provider_operation_model_matches(
    *,
    provider_kind: str,
    requested_model: str,
    actual_model: str,
) -> bool:
    """Match exact IDs, plus OpenRouter's documented provider-local response form."""

    if actual_model == requested_model:
        return True
    if str(provider_kind).casefold() != "openrouter":
        return False
    provider, separator, provider_local_model = requested_model.partition("/")
    return bool(provider and separator and provider_local_model) and (
        actual_model == provider_local_model
    )


@dataclass(frozen=True, slots=True)
class ProviderOperationEndpoints:
    base_url: str
    embeddings_url: str
    embeddings_models_url: str
    rerank_url: str
    chat_completions_url: str
    batches_url: str | None


class ProviderOperationEndpointResolver:
    """Resolve retrieval and OpenRouter Batch endpoints without guessing models."""

    @staticmethod
    def resolve(*, provider_kind: str, base_url: str) -> ProviderOperationEndpoints:
        chat = ProviderChatEndpointResolver.resolve(base_url)
        parsed = urlsplit(chat.base_url)
        api_path = parsed.path.rstrip("/") or "/v1"
        base = urlunsplit((parsed.scheme, parsed.netloc, api_path, "", ""))
        return ProviderOperationEndpoints(
            base_url=base,
            embeddings_url=f"{base}/embeddings",
            embeddings_models_url=f"{base}/embeddings/models",
            rerank_url=f"{base}/rerank",
            chat_completions_url=chat.chat_completions_url,
            batches_url=(
                OPENROUTER_BATCHES_URL
                if str(provider_kind).casefold() == "openrouter"
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class ProviderOperationTarget:
    provider_kind: str
    connection_id: str
    endpoints: ProviderOperationEndpoints
    _api_key: str = field(default="", repr=False, compare=False)

    @classmethod
    def create(
        cls,
        *,
        provider_kind: str,
        connection_id: str,
        base_url: str,
        api_key: str,
    ) -> "ProviderOperationTarget":
        clean_connection = str(connection_id or "").strip()
        if not clean_connection:
            raise ValueError("provider_operation_connection_id_required")
        return cls(
            provider_kind=str(provider_kind or "openai_compatible"),
            connection_id=clean_connection,
            endpoints=ProviderOperationEndpointResolver.resolve(
                provider_kind=provider_kind,
                base_url=base_url,
            ),
            _api_key=str(api_key or ""),
        )

    def endpoint_for(
        self,
        operation: ProviderOperation,
        *,
        rerank_access_mode: ProviderRerankAccessMode | None = None,
        upstream_batch_id: str | None = None,
    ) -> str:
        if operation == "embedding_vectors":
            return self.endpoints.embeddings_url
        if operation == "rerank_documents":
            if rerank_access_mode == "llm_json":
                return self.endpoints.chat_completions_url
            if rerank_access_mode != "dedicated":
                raise ValueError("provider_rerank_access_mode_required")
            return self.endpoints.rerank_url
        if self.provider_kind.casefold() != "openrouter" or not self.endpoints.batches_url:
            raise ValueError("provider_batch_requires_openrouter")
        if upstream_batch_id is None:
            return self.endpoints.batches_url
        clean_batch_id = str(upstream_batch_id).strip()
        if not clean_batch_id or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-"
            for character in clean_batch_id
        ):
            raise ValueError("provider_batch_invalid_upstream_id")
        return f"{self.endpoints.batches_url}/{clean_batch_id}"

    def authorization_headers(
        self, extra: Mapping[str, str] | None = None
    ) -> dict[str, str]:
        headers = dict(extra or {})
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers


class ProviderOperationTransport:
    """Build one pinned request; callers record dispatch before sending it."""

    def __init__(self, egress_policy: ProviderEgressPolicy) -> None:
        self.egress_policy = egress_policy

    @staticmethod
    def client_kwargs() -> dict[str, object]:
        return {
            "timeout": httpx.Timeout(connect=5, read=45, write=45, pool=5),
            "follow_redirects": False,
            "trust_env": False,
            "transport": httpx.AsyncHTTPTransport(retries=0),
        }

    async def authorize(
        self,
        target: ProviderOperationTarget,
        operation: ProviderOperation,
        *,
        rerank_access_mode: ProviderRerankAccessMode | None = None,
        upstream_batch_id: str | None = None,
    ) -> AuthorizedProviderTarget:
        return await self.egress_policy.authorize(
            target.endpoint_for(
                operation,
                rerank_access_mode=rerank_access_mode,
                upstream_batch_id=upstream_batch_id,
            )
        )

    @staticmethod
    def build_authorized_request(
        client: httpx.AsyncClient,
        target: ProviderOperationTarget,
        authorized: AuthorizedProviderTarget,
        *,
        method: Literal["GET", "POST"],
        payload: Mapping[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Request:
        return client.build_request(
            method,
            authorized.pinned_urls[0],
            headers=authorized.request_headers(target.authorization_headers(headers)),
            extensions=authorized.extensions,
            json=dict(payload) if payload is not None else None,
        )

    @staticmethod
    async def send_authorized(
        client: httpx.AsyncClient, request: httpx.Request
    ) -> httpx.Response:
        return await client.send(request, stream=True, follow_redirects=False)
