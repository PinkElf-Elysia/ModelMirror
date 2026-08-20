from __future__ import annotations

import httpx
import pytest
from httpx import MockTransport, Request, Response

from server.model_router.egress import ProviderEgressPolicy
from server.model_router.provider_chat import (
    PROVIDER_CHAT_CONTRACT_VERSION,
    ProviderChatEndpointResolver,
    ProviderChatTarget,
    ProviderChatTransport,
)


@pytest.mark.parametrize(
    "value,base,models,chat",
    [
        (
            "https://provider.example",
            "https://provider.example/v1",
            "https://provider.example/v1/models",
            "https://provider.example/v1/chat/completions",
        ),
        (
            "https://provider.example/openai/v1/",
            "https://provider.example/openai/v1",
            "https://provider.example/openai/v1/models",
            "https://provider.example/openai/v1/chat/completions",
        ),
        (
            "https://provider.example/v1/models",
            "https://provider.example/v1",
            "https://provider.example/v1/models",
            "https://provider.example/v1/chat/completions",
        ),
        (
            "https://provider.example/v1/chat/completions",
            "https://provider.example/v1",
            "https://provider.example/v1/models",
            "https://provider.example/v1/chat/completions",
        ),
    ],
)
def test_endpoint_resolver_normalizes_openai_compatible_urls(
    value: str, base: str, models: str, chat: str
) -> None:
    endpoints = ProviderChatEndpointResolver.resolve(value)

    assert endpoints.base_url == base
    assert endpoints.models_url == models
    assert endpoints.chat_completions_url == chat
    assert PROVIDER_CHAT_CONTRACT_VERSION == "modelmirror-provider-chat-v1"


@pytest.mark.parametrize(
    "value",
    [
        "provider.example/v1",
        "ftp://provider.example/v1",
        "https://user:pass@provider.example/v1",
        "https://provider.example/v1?token=secret",
        "https://provider.example/v1#fragment",
    ],
)
def test_endpoint_resolver_rejects_ambiguous_or_credentialed_urls(value: str) -> None:
    with pytest.raises(ValueError, match="provider_chat_invalid_base_url"):
        ProviderChatEndpointResolver.resolve(value)


def test_target_requires_managed_connection_id_and_redacts_key_from_repr() -> None:
    with pytest.raises(ValueError, match="managed_connection_id_required"):
        ProviderChatTarget.create(
            source="managed",
            provider_kind="newapi",
            base_url="https://provider.example/v1",
            api_key="secret-value",
        )

    target = ProviderChatTarget.create(
        source="managed",
        provider_kind="newapi",
        base_url="https://provider.example/v1",
        api_key="secret-value",
        connection_id="connection-1",
    )

    assert "secret-value" not in repr(target)
    assert target.authorization_headers() == {"Authorization": "Bearer secret-value"}


@pytest.mark.asyncio
async def test_managed_certification_pins_one_ip_and_never_retries_post() -> None:
    requests: list[Request] = []

    def handler(request: Request) -> Response:
        requests.append(request)
        raise httpx.ConnectError("failed", request=request)

    policy = ProviderEgressPolicy(
        resolver=lambda _host, _port: ["8.8.8.8", "1.1.1.1"]
    )
    target = ProviderChatTarget.create(
        source="managed",
        provider_kind="newapi",
        base_url="https://provider.example/v1",
        api_key="secret-value",
        connection_id="connection-1",
    )
    transport = ProviderChatTransport(policy)
    async with httpx.AsyncClient(
        transport=MockTransport(handler), follow_redirects=True, trust_env=False
    ) as client:
        with pytest.raises(httpx.ConnectError):
            await transport.send_stream(
                client,
                target,
                {"model": "example", "stream": True},
                certification=True,
            )

    assert len(requests) == 1
    assert requests[0].url.host == "1.1.1.1"
    assert requests[0].headers["host"] == "provider.example"
    assert requests[0].extensions["sni_hostname"] == "provider.example"
    assert requests[0].headers["authorization"] == "Bearer secret-value"


@pytest.mark.asyncio
async def test_static_target_bypasses_dynamic_egress_and_stream_is_closed() -> None:
    policy = ProviderEgressPolicy(
        resolver=lambda _host, _port: (_ for _ in ()).throw(
            AssertionError("static target must not resolve through managed egress")
        )
    )
    target = ProviderChatTarget.create(
        source="static",
        provider_kind="openai_compatible",
        base_url="http://trusted-static:3000/v1",
        api_key="static-key",
    )
    seen: list[Request] = []

    def handler(request: Request) -> Response:
        seen.append(request)
        return Response(200, content=b"data: [DONE]\n\n")

    async with httpx.AsyncClient(
        transport=MockTransport(handler), follow_redirects=True, trust_env=False
    ) as client:
        async with ProviderChatTransport(policy).stream(
            client, target, {"model": "example", "stream": True}
        ) as response:
            assert await response.aread() == b"data: [DONE]\n\n"
        assert response.is_closed

    assert len(seen) == 1
    assert str(seen[0].url) == "http://trusted-static:3000/v1/chat/completions"


def test_transport_client_defaults_disable_proxy_redirects_and_retries() -> None:
    kwargs = ProviderChatTransport.client_kwargs(certification=True)
    timeout = kwargs["timeout"]

    assert kwargs["follow_redirects"] is False
    assert kwargs["trust_env"] is False
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == 5
    assert timeout.read == 45
