from __future__ import annotations

import base64
import copy
from types import SimpleNamespace

import httpx
import pytest

from server.workflow_native.secure_http import (
    WorkflowHttpRequestError,
    _PinnedPublicNetworkBackend,
    _resolve_fixed_public_dns,
    execute_workflow_http_request,
    validate_http_request_credential,
    validate_http_request_v2_config,
    validate_public_workflow_url,
)


class FakeCredentials:
    def __init__(
        self,
        secret: str = "test-secret",
        *,
        status: str = "active",
        catalog_project_id: str = "",
    ) -> None:
        self.secret = secret
        self.status = status
        self.catalog_project_id = catalog_project_id

    def get_public(self, credential_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            credential_id=credential_id,
            kind="generic",
            status=self.status,
            catalog_project_id=self.catalog_project_id,
        )

    def resolve(self, credential_id: str) -> str:
        if self.status != "active":
            raise LookupError(credential_id)
        return self.secret


def http_config(**patch: object) -> dict[str, object]:
    data: dict[str, object] = {
        "kind": "http_request",
        "contractVersion": 2,
        "method": "GET",
        "url": "https://api.example.test/items/{{item_id}}",
        "queryItems": [
            {
                "id": "query_1",
                "name": "include",
                "binding": {"source": "literal", "valueType": "boolean", "value": True},
            }
        ],
        "headerItems": [],
        "bodyMode": "none",
        "formFields": [],
        "authType": "none",
        "credentialId": "",
        "apiKeyLocation": "header",
        "apiKeyName": "X-API-Key",
        "timeoutSeconds": 30,
        "redirectLimit": 0,
        "responseLimitBytes": 1_024,
        "responseMode": "auto",
        "statusPolicy": "success_only",
        "outputVariable": "http_response",
    }
    data.update(patch)
    return data


async def allow_public(url: str, policy: str) -> tuple[str, ...]:
    assert policy == "public_only"
    assert url.startswith("https://api.example.test")
    return ("93.184.216.34",)


def assert_http_error(code: str, callback) -> None:
    with pytest.raises(WorkflowHttpRequestError) as raised:
        callback()
    assert raised.value.code == code


def test_http_v2_config_rejects_dynamic_origin_body_and_protected_headers() -> None:
    assert_http_error(
        "HTTP_DYNAMIC_ORIGIN_FORBIDDEN",
        lambda: validate_http_request_v2_config(
            http_config(url="https://{{host}}/items")
        ),
    )
    assert_http_error(
        "HTTP_METHOD_BODY_FORBIDDEN",
        lambda: validate_http_request_v2_config(
            http_config(
                bodyMode="text",
                bodyBinding={"source": "literal", "valueType": "text", "value": "x"},
            )
        ),
    )
    assert_http_error(
        "HTTP_PROTECTED_HEADER",
        lambda: validate_http_request_v2_config(
            http_config(
                headerItems=[
                    {
                        "id": "header_1",
                        "name": "Authorization",
                        "binding": {"source": "literal", "valueType": "text", "value": "x"},
                    }
                ]
            )
        ),
    )
    assert_http_error(
        "HTTP_PLAINTEXT_AUTH_PARAMETER_FORBIDDEN",
        lambda: validate_http_request_v2_config(
            http_config(
                queryItems=[
                    {
                        "id": "query_1",
                        "name": "access_token",
                        "binding": {
                            "source": "literal",
                            "valueType": "text",
                            "value": "plain-secret",
                        },
                    }
                ]
            )
        ),
    )


def test_http_v2_rejects_catalog_scoped_credentials() -> None:
    with pytest.raises(WorkflowHttpRequestError) as raised:
        validate_http_request_credential(
            http_config(authType="bearer", credentialId="cred_catalog"),
            FakeCredentials(catalog_project_id="mcp-project").get_public,
        )
    assert raised.value.code == "HTTP_CREDENTIAL_SCOPE_INVALID"


@pytest.mark.asyncio
async def test_http_v2_returns_bounded_structured_json_and_safe_headers() -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            headers={
                "Content-Type": "application/problem+json; charset=utf-8",
                "ETag": '"v1"',
                "Set-Cookie": "secret=cookie",
                "Location": "https://api.example.test/?token=secret",
            },
            json={"ok": True, "count": 2},
        )

    result = await execute_workflow_http_request(
        http_config(),
        {"item_id": "42"},
        FakeCredentials(),
        transport=httpx.MockTransport(handler),
        url_validator=allow_public,
    )

    assert seen[0].url.path == "/items/42"
    assert seen[0].url.params["include"] == "true"
    assert result["body"] == {"ok": True, "count": 2}
    assert result["statusCode"] == 200
    assert result["headers"] == {
        "content-type": "application/problem+json; charset=utf-8",
        "etag": '"v1"',
    }
    assert "secret" not in repr(result)


@pytest.mark.asyncio
async def test_http_v2_accepts_dns_json_media_type_as_structured_json() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "application/dns-json"},
            content=b'{"Status":0,"Question":[{"name":"example.com","type":1}]}',
        )

    result = await execute_workflow_http_request(
        http_config(),
        {"item_id": "42"},
        FakeCredentials(),
        transport=httpx.MockTransport(handler),
        url_validator=allow_public,
    )

    assert result["contentType"] == "application/dns-json"
    assert result["body"] == {
        "Status": 0,
        "Question": [{"name": "example.com", "type": 1}],
    }


@pytest.mark.asyncio
async def test_http_v2_still_rejects_other_application_media_types() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "application/octet-stream"},
            content=b"not-text-contract-data",
        )

    with pytest.raises(WorkflowHttpRequestError) as raised:
        await execute_workflow_http_request(
            http_config(),
            {"item_id": "42"},
            FakeCredentials(),
            transport=httpx.MockTransport(handler),
            url_validator=allow_public,
        )

    assert raised.value.code == "HTTP_BINARY_RESPONSE_FORBIDDEN"


@pytest.mark.asyncio
async def test_http_v2_url_template_values_cannot_inject_path_or_query_structure() -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, headers={"Content-Type": "text/plain"}, text="ok")

    await execute_workflow_http_request(
        http_config(url="https://api.example.test/items/{{item_id}}?filter={{filter}}"),
        {"item_id": "folder/42", "filter": "active&admin=true#fragment"},
        FakeCredentials(),
        transport=httpx.MockTransport(handler),
        url_validator=allow_public,
    )

    assert seen[0].url.raw_path.startswith(b"/items/folder%2F42?")
    assert seen[0].url.params["filter"] == "active&admin=true#fragment"
    assert "admin" not in seen[0].url.params


@pytest.mark.asyncio
async def test_http_v2_uses_encrypted_bearer_reference_without_returning_secret() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-secret"
        return httpx.Response(204, headers={"Content-Type": "text/plain"}, content=b"")

    result = await execute_workflow_http_request(
        http_config(authType="bearer", credentialId="cred_test"),
        {"item_id": "1"},
        FakeCredentials(),
        transport=httpx.MockTransport(handler),
        url_validator=allow_public,
    )

    assert result["body"] == ""
    assert "test-secret" not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("location", ["header", "query"])
async def test_http_v2_applies_api_key_credential_at_selected_location(
    location: str,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if location == "header":
            assert request.headers["X-Test-Key"] == "synthetic-api-key"
            assert "api_key" not in request.url.params
        else:
            assert request.url.params["api_key"] == "synthetic-api-key"
            assert "X-Test-Key" not in request.headers
        return httpx.Response(200, headers={"Content-Type": "application/json"}, json={"ok": True})

    result = await execute_workflow_http_request(
        http_config(
            authType="api_key",
            credentialId="cred_api_key",
            apiKeyLocation=location,
            apiKeyName="X-Test-Key" if location == "header" else "api_key",
        ),
        {"item_id": "1"},
        FakeCredentials(secret="synthetic-api-key"),
        transport=httpx.MockTransport(handler),
        url_validator=allow_public,
    )

    assert result["body"] == {"ok": True}
    assert "synthetic-api-key" not in repr(result)


@pytest.mark.asyncio
async def test_http_v2_applies_basic_credential_without_exposing_plaintext() -> None:
    username = "demo-user"
    password = "demo-pass"
    expected = base64.b64encode(f"{username}:{password}".encode()).decode()

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Basic {expected}"
        return httpx.Response(200, headers={"Content-Type": "text/plain"}, text="accepted")

    result = await execute_workflow_http_request(
        http_config(authType="basic", credentialId="cred_basic"),
        {"item_id": "1"},
        FakeCredentials(secret='{"username":"demo-user","password":"demo-pass"}'),
        transport=httpx.MockTransport(handler),
        url_validator=allow_public,
    )

    assert result["body"] == "accepted"
    assert username not in repr(result)
    assert password not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("config_patch", "secret", "forbidden_tokens"),
    [
        (
            {"authType": "bearer", "credentialId": "cred_reflect_bearer"},
            "reflected-bearer-token",
            ["reflected-bearer-token"],
        ),
        (
            {
                "authType": "api_key",
                "credentialId": "cred_reflect_query",
                "apiKeyLocation": "query",
                "apiKeyName": "api_key",
            },
            "reflected-query-token",
            ["reflected-query-token"],
        ),
        (
            {"authType": "basic", "credentialId": "cred_reflect_basic"},
            '{"username":"reflect-user","password":"reflect-pass"}',
            [
                "reflect-user",
                "reflect-pass",
                base64.b64encode(b"reflect-user:reflect-pass").decode(),
            ],
        ),
    ],
)
async def test_http_v2_redacts_reflected_auth_material_from_response_output(
    config_patch: dict[str, object],
    secret: str,
    forbidden_tokens: list[str],
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "Content-Type": "application/json",
                "X-Request-Id": request.headers.get("Authorization", "")
                or request.headers.get("X-Reflect-Key", ""),
            },
            json={
                "authorization": request.headers.get("Authorization"),
                "apiKey": request.headers.get("X-Reflect-Key"),
                "requestUrl": str(request.url),
            },
        )

    if config_patch.get("apiKeyLocation") == "header":
        config_patch["apiKeyName"] = "X-Reflect-Key"
    result = await execute_workflow_http_request(
        http_config(**config_patch),
        {"item_id": "1"},
        FakeCredentials(secret=secret),
        transport=httpx.MockTransport(handler),
        url_validator=allow_public,
    )

    rendered = repr(result)
    assert "[REDACTED]" in rendered
    assert all(token not in rendered for token in forbidden_tokens)


@pytest.mark.asyncio
async def test_http_v2_uses_rotated_secret_immediately_and_revocation_fails_closed() -> None:
    credentials = FakeCredentials(secret="synthetic-token-v1")
    seen_authorization: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_authorization.append(request.headers["Authorization"])
        return httpx.Response(200, headers={"Content-Type": "text/plain"}, text="ok")

    config = http_config(authType="bearer", credentialId="cred_rotating")
    transport = httpx.MockTransport(handler)
    await execute_workflow_http_request(
        config,
        {"item_id": "1"},
        credentials,
        transport=transport,
        url_validator=allow_public,
    )
    credentials.secret = "synthetic-token-v2"
    await execute_workflow_http_request(
        config,
        {"item_id": "1"},
        credentials,
        transport=transport,
        url_validator=allow_public,
    )

    credentials.status = "revoked"
    with pytest.raises(WorkflowHttpRequestError) as revoked:
        await execute_workflow_http_request(
            config,
            {"item_id": "1"},
            credentials,
            transport=transport,
            url_validator=allow_public,
        )

    assert seen_authorization == [
        "Bearer synthetic-token-v1",
        "Bearer synthetic-token-v2",
    ]
    assert revoked.value.code == "HTTP_CREDENTIAL_UNAVAILABLE"
    assert "synthetic-token" not in str(revoked.value)


@pytest.mark.asyncio
async def test_http_v2_status_policy_redirect_and_response_limits_fail_closed() -> None:
    async def status_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, headers={"Content-Type": "application/json"}, json={"secret": "body"})

    with pytest.raises(WorkflowHttpRequestError) as status_error:
        await execute_workflow_http_request(
            http_config(),
            {"item_id": "1"},
            FakeCredentials(),
            transport=httpx.MockTransport(status_handler),
            url_validator=allow_public,
        )
    assert status_error.value.code == "HTTP_STATUS_NOT_SUCCESSFUL"
    assert "secret" not in str(status_error.value)

    captured = await execute_workflow_http_request(
        http_config(statusPolicy="capture_all"),
        {"item_id": "1"},
        FakeCredentials(),
        transport=httpx.MockTransport(status_handler),
        url_validator=allow_public,
    )
    assert captured["statusCode"] == 409
    assert captured["ok"] is False

    requests: list[httpx.Request] = []

    async def redirect_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(302, headers={"Location": "/done"})
        return httpx.Response(200, headers={"Content-Type": "text/plain"}, text="done")

    redirected = await execute_workflow_http_request(
        http_config(
            method="POST",
            bodyMode="text",
            bodyBinding={"source": "literal", "valueType": "text", "value": "payload"},
            redirectLimit=1,
        ),
        {"item_id": "1"},
        FakeCredentials(),
        transport=httpx.MockTransport(redirect_handler),
        url_validator=allow_public,
    )
    assert [request.method for request in requests] == ["POST", "GET"]
    assert redirected["body"] == "done"

    async def large_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Type": "text/plain"}, content=b"x" * 1_025)

    with pytest.raises(WorkflowHttpRequestError) as large_error:
        await execute_workflow_http_request(
            http_config(),
            {"item_id": "1"},
            FakeCredentials(),
            transport=httpx.MockTransport(large_handler),
            url_validator=allow_public,
        )
    assert large_error.value.code == "HTTP_RESPONSE_TOO_LARGE"


@pytest.mark.asyncio
async def test_public_url_rejects_private_and_dns_rebinding_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "server.workflow_native.secure_http.socket.getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("127.0.0.1", 443))],
    )
    with pytest.raises(WorkflowHttpRequestError) as private_error:
        await validate_public_workflow_url("https://example.test", "public_only")
    assert private_error.value.code == "HTTP_PRIVATE_TARGET_FORBIDDEN"

    monkeypatch.setattr(
        "server.workflow_native.secure_http.socket.getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("100.64.0.1", 443))],
    )
    with pytest.raises(WorkflowHttpRequestError) as shared_error:
        await validate_public_workflow_url("https://example.test", "public_only")
    assert shared_error.value.code == "HTTP_PRIVATE_TARGET_FORBIDDEN"


@pytest.mark.asyncio
async def test_public_url_resolves_all_synthetic_dns_through_fixed_doh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "server.workflow_native.secure_http.socket.getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("198.18.0.8", 443))],
    )
    seen: list[str] = []

    async def fixed_public_dns(hostname: str) -> tuple[str, ...]:
        seen.append(hostname)
        return ("93.184.216.34",)

    monkeypatch.setattr(
        "server.workflow_native.secure_http._resolve_fixed_public_dns",
        fixed_public_dns,
    )

    assert await validate_public_workflow_url("https://example.test", "public_only") == (
        "93.184.216.34",
    )
    assert seen == ["example.test"]


@pytest.mark.asyncio
async def test_public_url_does_not_fallback_for_mixed_or_other_private_dns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def forbidden_fixed_dns(hostname: str) -> tuple[str, ...]:
        raise AssertionError(f"unexpected fixed DNS lookup for {hostname}")

    monkeypatch.setattr(
        "server.workflow_native.secure_http._resolve_fixed_public_dns",
        forbidden_fixed_dns,
    )
    for addresses in (
        ("93.184.216.34", "198.18.0.8"),
        ("10.0.0.8",),
    ):
        monkeypatch.setattr(
            "server.workflow_native.secure_http.socket.getaddrinfo",
            lambda *args, addresses=addresses, **kwargs: [
                (2, 1, 6, "", (address, 443)) for address in addresses
            ],
        )
        with pytest.raises(WorkflowHttpRequestError) as raised:
            await validate_public_workflow_url("https://example.test", "public_only")
        assert raised.value.code == "HTTP_PRIVATE_TARGET_FORBIDDEN"


@pytest.mark.asyncio
async def test_fixed_doh_is_bounded_typed_and_rejects_private_answers() -> None:
    requests: list[httpx.Request] = []

    async def public_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        record_type = request.url.params["type"]
        answer = (
            [{"name": "example.test.", "type": 1, "data": "93.184.216.34"}]
            if record_type == "A"
            else []
        )
        return httpx.Response(
            200,
            headers={"Content-Type": "application/dns-json"},
            json={"Status": 0, "Answer": answer},
        )

    assert await _resolve_fixed_public_dns(
        "example.test",
        transport=httpx.MockTransport(public_handler),
    ) == ("93.184.216.34",)
    assert [request.url.host for request in requests] == ["1.1.1.1", "1.1.1.1"]
    assert [request.url.params["type"] for request in requests] == ["A", "AAAA"]

    async def private_handler(request: httpx.Request) -> httpx.Response:
        record_type = request.url.params["type"]
        answer = (
            [{"name": "example.test.", "type": 1, "data": "127.0.0.1"}]
            if record_type == "A"
            else []
        )
        return httpx.Response(
            200,
            headers={"Content-Type": "application/dns-json"},
            json={"Status": 0, "Answer": answer},
        )

    with pytest.raises(WorkflowHttpRequestError) as private_error:
        await _resolve_fixed_public_dns(
            "example.test",
            transport=httpx.MockTransport(private_handler),
        )
    assert private_error.value.code == "HTTP_PRIVATE_TARGET_FORBIDDEN"


@pytest.mark.asyncio
async def test_pinned_backend_connects_to_approved_ip_not_hostname() -> None:
    backend = _PinnedPublicNetworkBackend()
    calls: list[tuple[str, int]] = []
    stream = object()

    class Delegate:
        async def connect_tcp(self, host: str, port: int, **kwargs: object) -> object:
            calls.append((host, port))
            return stream

        async def sleep(self, seconds: float) -> None:
            return None

    backend._delegate = Delegate()  # type: ignore[assignment]
    backend.approve("API.EXAMPLE.TEST.", 443, ("93.184.216.34",))

    assert await backend.connect_tcp("api.example.test", 443) is stream
    assert calls == [("93.184.216.34", 443)]

    with pytest.raises(WorkflowHttpRequestError) as missing:
        await backend.connect_tcp("rebound.example.test", 443)
    assert missing.value.code == "HTTP_DNS_PIN_MISSING"


def test_http_config_validation_does_not_mutate_definition() -> None:
    data = http_config()
    before = copy.deepcopy(data)
    validate_http_request_v2_config(data)
    assert data == before
