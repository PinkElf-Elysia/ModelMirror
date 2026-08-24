from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from server.sandbox_sidecar import oauth_server


ROOT = Path(__file__).resolve().parents[2]


def request(**extra: Any) -> dict[str, Any]:
    return {
        "action": "fetch_json",
        "target_id": "mcphub_" + "1" * 32,
        "url": "https://mcp.example.com/.well-known/oauth-protected-resource",
        "capability": "a" * 64,
        "document_kind": "protected_resource_metadata",
        **extra,
    }


def test_sidecar_contract_rejects_client_network_and_header_injection() -> None:
    normalized, host, capability = oauth_server._contract(
        request(),
        action="fetch_json",
        extra=frozenset({"document_kind"}),
    )
    assert normalized.startswith("https://mcp.example.com/")
    assert host == "mcp.example.com"
    assert capability == "a" * 64

    for injected in (
        {"headers": {"Authorization": "secret"}},
        {"tenant_id": "other"},
        {"proxy": "http://127.0.0.1"},
    ):
        with pytest.raises(oauth_server.HubSidecarError) as denied:
            oauth_server._contract(
                request(**injected),
                action="fetch_json",
                extra=frozenset({"document_kind"}),
            )
        assert denied.value.code == "mcp_remote_oauth_request_invalid"


def test_www_authenticate_parser_returns_only_one_fixed_metadata_hint() -> None:
    assert oauth_server._resource_metadata(
        'Bearer realm="mcp", resource_metadata="https://mcp.example.com/meta"'
    ) == "https://mcp.example.com/meta"
    assert oauth_server._resource_metadata("Bearer realm=\"mcp\"") == ""
    assert oauth_server._resource_metadata(
        'Basic resource_metadata="https://mcp.example.com/meta"'
    ) == ""
    with pytest.raises(oauth_server.HubSidecarError) as mixed:
        oauth_server._resource_metadata(
            'Bearer realm="mcp", Basic resource_metadata="https://evil.example/meta"'
        )
    assert mixed.value.code == "mcp_remote_oauth_challenge_ambiguous"
    with pytest.raises(oauth_server.HubSidecarError) as ambiguous:
        oauth_server._resource_metadata(
            'Bearer resource_metadata="https://one.example/meta", '
            'Bearer resource_metadata="https://two.example/meta"'
        )
    assert ambiguous.value.code == "mcp_remote_oauth_challenge_ambiguous"
    with pytest.raises(oauth_server.HubSidecarError) as numeric_scheme:
        oauth_server._resource_metadata(
            'Bearer realm="mcp", 123auth realm="other", '
            'resource_metadata="https://evil.example/meta"'
        )
    assert numeric_scheme.value.code == "mcp_remote_oauth_challenge_ambiguous"


@pytest.mark.asyncio
async def test_probe_uses_www_authenticate_hint_only_for_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Proxy:
        async def close(self) -> None:
            pass

    class Response:
        status_code = 200
        headers = {
            "www-authenticate": (
                'Bearer resource_metadata="https://attacker.example/meta"'
            )
        }

    class Stream:
        async def __aenter__(self) -> Response:
            return Response()

        async def __aexit__(self, *_args: Any) -> None:
            pass

    class Client:
        def stream(self, *_args: Any, **_kwargs: Any) -> Stream:
            return Stream()

        async def aclose(self) -> None:
            pass

    service = oauth_server.OAuthMetadataService()

    async def fake_client(*_args: Any) -> tuple[Proxy, Client]:
        return Proxy(), Client()

    monkeypatch.setattr(service, "_client", fake_client)
    result = await service.probe_resource(
        "https://mcp.example.com/mcp", "mcp.example.com", "a" * 64
    )
    assert result == {"status_class": "2xx", "resource_metadata_url": ""}


@pytest.mark.asyncio
async def test_dynamic_registration_contract_rejects_arbitrary_payload_before_network() -> None:
    service = oauth_server.OAuthMetadataService()
    with pytest.raises(oauth_server.HubSidecarError) as denied:
        await service.register_public_client(
            "https://auth.example.com/register",
            "auth.example.com",
            "a" * 64,
            {
                "redirect_uris": ["http://127.0.0.1:8765/callback"],
                "token_endpoint_auth_method": "client_secret_post",
                "grant_types": ["authorization_code"],
                "response_types": ["code"],
                "client_name": "ModelMirror local MCP OAuth",
            },
        )
    assert denied.value.code == "mcp_remote_oauth_registration_invalid"

    with pytest.raises(oauth_server.HubSidecarError) as client_name:
        await service.register_public_client(
            "https://auth.example.com/register",
            "auth.example.com",
            "a" * 64,
            {
                "redirect_uris": ["http://127.0.0.1:8765/callback"],
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code"],
                "response_types": ["code"],
                "client_name": "attacker-controlled-name",
            },
        )
    assert client_name.value.code == "mcp_remote_oauth_registration_invalid"

    assert oauth_server._valid_redirect_uri("http://127.0.0.1:bad/callback") is False


@pytest.mark.asyncio
async def test_dynamic_registration_unknown_outcome_is_never_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    class Proxy:
        async def close(self) -> None:
            pass

    class Stream:
        async def __aenter__(self) -> Any:
            nonlocal calls
            calls += 1
            import httpx

            raise httpx.ReadTimeout("ambiguous")

        async def __aexit__(self, *_args: Any) -> None:
            pass

    class Client:
        def stream(self, *_args: Any, **_kwargs: Any) -> Stream:
            return Stream()

        async def aclose(self) -> None:
            pass

    service = oauth_server.OAuthMetadataService()

    async def fake_client(*_args: Any) -> tuple[Proxy, Client]:
        return Proxy(), Client()

    monkeypatch.setattr(service, "_client", fake_client)
    with pytest.raises(oauth_server.HubSidecarError) as unknown:
        await service.register_public_client(
            "https://auth.example.com/register",
            "auth.example.com",
            "a" * 64,
            {
                "redirect_uris": ["http://127.0.0.1:8765/callback"],
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code"],
                "response_types": ["code"],
                "client_name": "ModelMirror local MCP OAuth",
            },
        )
    assert unknown.value.code == "mcp_remote_oauth_registration_unknown_outcome"
    assert calls == 1


@pytest.mark.asyncio
async def test_dynamic_registration_marks_confidential_response_without_returning_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Proxy:
        async def close(self) -> None:
            pass

    class Response:
        status_code = 201
        headers = {"content-type": "application/json"}

        async def aiter_bytes(self):
            yield (
                b'{"client_id":"public-looking","client_secret":"hidden",'
                b'"token_endpoint_auth_method":"client_secret_post"}'
            )

    class Stream:
        async def __aenter__(self) -> Response:
            return Response()

        async def __aexit__(self, *_args: Any) -> None:
            pass

    class Client:
        def stream(self, *_args: Any, **_kwargs: Any) -> Stream:
            return Stream()

        async def aclose(self) -> None:
            pass

    service = oauth_server.OAuthMetadataService()

    async def fake_client(*_args: Any) -> tuple[Proxy, Client]:
        return Proxy(), Client()

    monkeypatch.setattr(service, "_client", fake_client)
    result = await service.register_public_client(
        "https://auth.example.com/register",
        "auth.example.com",
        "a" * 64,
        {
            "redirect_uris": ["http://127.0.0.1:8765/callback"],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "client_name": "ModelMirror local MCP OAuth",
        },
    )
    assert result == {"client_id": "public-looking", "contains_secret": True}
    assert "hidden" not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "body", "content_type"),
    [
        (201, b"not-json", "application/json"),
        (201, b'{"client_id":"created"}', "text/plain"),
        (200, b'{"client_id":"created"}', "application/json"),
        (500, b'{"error":"temporary"}', "application/json"),
        (
            201,
            b'{"client_id":"created","redirect_uris":["http://127.0.0.1:9999/other"],'
            b'"token_endpoint_auth_method":"none","grant_types":["authorization_code"],'
            b'"response_types":["code"]}',
            "application/json",
        ),
    ],
)
async def test_dynamic_registration_post_dispatch_validation_is_unknown_outcome(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    body: bytes,
    content_type: str,
) -> None:
    class Proxy:
        async def close(self) -> None:
            pass

    class Response:
        headers = {"content-type": content_type}

        def __init__(self) -> None:
            self.status_code = status_code

        async def aiter_bytes(self):
            yield body

    class Stream:
        async def __aenter__(self) -> Response:
            return Response()

        async def __aexit__(self, *_args: Any) -> None:
            pass

    class Client:
        def stream(self, *_args: Any, **_kwargs: Any) -> Stream:
            return Stream()

        async def aclose(self) -> None:
            pass

    service = oauth_server.OAuthMetadataService()

    async def fake_client(*_args: Any) -> tuple[Proxy, Client]:
        return Proxy(), Client()

    monkeypatch.setattr(service, "_client", fake_client)
    with pytest.raises(oauth_server.HubSidecarError) as unknown:
        await service.register_public_client(
            "https://auth.example.com/register",
            "auth.example.com",
            "a" * 64,
            {
                "redirect_uris": ["http://127.0.0.1:8765/callback"],
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code"],
                "response_types": ["code"],
                "client_name": "ModelMirror local MCP OAuth",
            },
        )
    assert unknown.value.code == "mcp_remote_oauth_registration_unknown_outcome"


@pytest.mark.asyncio
async def test_dynamic_registration_treats_empty_secret_fields_as_confidential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Proxy:
        async def close(self) -> None:
            pass

    class Response:
        status_code = 201
        headers = {"content-type": "application/json"}

        async def aiter_bytes(self):
            yield b'{"client_id":"created","client_secret":""}'

    class Stream:
        async def __aenter__(self) -> Response:
            return Response()

        async def __aexit__(self, *_args: Any) -> None:
            pass

    class Client:
        def stream(self, *_args: Any, **_kwargs: Any) -> Stream:
            return Stream()

        async def aclose(self) -> None:
            pass

    service = oauth_server.OAuthMetadataService()

    async def fake_client(*_args: Any) -> tuple[Proxy, Client]:
        return Proxy(), Client()

    monkeypatch.setattr(service, "_client", fake_client)
    result = await service.register_public_client(
        "https://auth.example.com/register",
        "auth.example.com",
        "a" * 64,
        {
            "redirect_uris": ["http://127.0.0.1:8765/callback"],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "client_name": "ModelMirror local MCP OAuth",
        },
    )
    assert result["contains_secret"] is True


def test_compose_keeps_oauth_http_in_a_networkless_non_root_sidecar() -> None:
    import yaml

    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    service = compose["services"]["mcp-remote-oauth"]
    assert service["network_mode"] == "none"
    assert service["user"] == "65532:65532"
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in service["security_opt"]
    assert any(
        str(item).endswith(":/run/modelmirror-oauth") for item in service["volumes"]
    )
    assert any(
        str(item).endswith(":/run/modelmirror-hub-egress")
        for item in service["volumes"]
    )
    assert "MODEL_MIRROR_CREDENTIAL_MASTER_KEY" not in service.get("environment", {})
    dockerfile = (ROOT / "server/sandbox_sidecar/Dockerfile.hub").read_text(
        encoding="utf-8"
    )
    assert "COPY oauth_server.py ./sandbox_sidecar/oauth_server.py" in dockerfile
    assert "USER 65532:65532" in dockerfile
