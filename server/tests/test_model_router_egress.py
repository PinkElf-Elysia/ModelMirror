from __future__ import annotations

import ast
from pathlib import Path

import httpx
import pytest
from httpx import MockTransport, Request, Response

from server.model_router.egress import ProviderEgressError, ProviderEgressPolicy


@pytest.mark.asyncio
async def test_public_https_is_pinned_and_public_http_is_rejected() -> None:
    policy = ProviderEgressPolicy(
        resolver=lambda _host, _port: ["8.8.8.8"]
    )

    target = await policy.authorize("https://provider.example/v1")

    assert target.pinned_urls == ("https://8.8.8.8/v1",)
    assert target.host_header == "provider.example"
    assert target.sni_hostname == "provider.example"
    with pytest.raises(ProviderEgressError) as exc_info:
        await policy.authorize("http://provider.example/v1")
    assert exc_info.value.code == "provider_https_required"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/v1",
        "https://10.1.2.3/v1",
        "https://[fd00::1]/v1",
        "https://169.254.169.254/latest/meta-data",
        "https://224.0.0.1/v1",
        "https://0.0.0.0/v1",
    ],
)
async def test_protected_addresses_fail_closed(url: str) -> None:
    with pytest.raises(ProviderEgressError) as exc_info:
        await ProviderEgressPolicy().authorize(url)
    assert exc_info.value.code == "provider_address_blocked"


@pytest.mark.asyncio
async def test_exact_internal_allowlist_permits_newapi_but_not_metadata() -> None:
    policy = ProviderEgressPolicy(
        internal_allowlist="new-api:3000,169.254.169.254:80",
        resolver=lambda host, _port: {
            "new-api": ["172.20.0.4"],
        }.get(host, ["169.254.169.254"]),
    )

    target = await policy.authorize("http://new-api:3000/v1")
    assert target.pinned_urls == ("http://172.20.0.4:3000/v1",)
    assert target.host_header == "new-api:3000"

    with pytest.raises(ProviderEgressError) as exc_info:
        await policy.authorize("http://169.254.169.254/latest/meta-data")
    assert exc_info.value.code == "provider_address_blocked"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url,allowlist",
    [
        ("http://[fd00:ec2::254]:80/latest", "[fd00:ec2::254]:80"),
        ("http://224.0.0.1:80/v1", "224.0.0.1:80"),
        ("http://0.0.0.0:80/v1", "0.0.0.0:80"),
        ("http://240.0.0.1:80/v1", "240.0.0.1:80"),
    ],
)
async def test_internal_allowlist_cannot_override_permanent_blocks(
    url: str,
    allowlist: str,
) -> None:
    with pytest.raises(ProviderEgressError) as exc_info:
        await ProviderEgressPolicy(internal_allowlist=allowlist).authorize(url)
    assert exc_info.value.code == "provider_address_blocked"


@pytest.mark.parametrize(
    "value",
    [
        "https://provider.example\\confused/v1",
        "https://provider_example/v1",
        "https://-provider.example/v1",
    ],
)
def test_confusing_hostnames_are_rejected(value: str) -> None:
    with pytest.raises(ProviderEgressError) as exc_info:
        ProviderEgressPolicy().validate_for_storage(value)
    assert exc_info.value.code == "invalid_address"


@pytest.mark.asyncio
async def test_mixed_public_and_private_dns_answers_reject_entire_target() -> None:
    policy = ProviderEgressPolicy(
        resolver=lambda _host, _port: ["8.8.8.8", "10.0.0.8"]
    )

    with pytest.raises(ProviderEgressError) as exc_info:
        await policy.authorize("https://provider.example/v1")
    assert exc_info.value.code == "provider_address_blocked"


@pytest.mark.asyncio
async def test_request_preserves_host_and_sni_and_does_not_follow_redirects() -> None:
    requests: list[Request] = []

    def handler(request: Request) -> Response:
        requests.append(request)
        return Response(302, headers={"location": "http://127.0.0.1/private"})

    policy = ProviderEgressPolicy(
        resolver=lambda _host, _port: ["8.8.8.8"]
    )
    async with httpx.AsyncClient(
        transport=MockTransport(handler),
        follow_redirects=True,
        trust_env=False,
    ) as client:
        response = await policy.request(
            client,
            "GET",
            "https://provider.example/v1/models",
        )

    assert response.status_code == 302
    assert len(requests) == 1
    assert requests[0].url == "https://8.8.8.8/v1/models"
    assert requests[0].headers["host"] == "provider.example"
    assert requests[0].extensions["sni_hostname"] == "provider.example"


@pytest.mark.asyncio
async def test_dns_rebinding_cannot_change_the_approved_connection_address() -> None:
    resolutions = iter([["8.8.8.8"], ["127.0.0.1"]])
    policy = ProviderEgressPolicy(
        resolver=lambda _host, _port: next(resolutions)
    )
    seen_urls: list[str] = []

    def handler(request: Request) -> Response:
        seen_urls.append(str(request.url))
        return Response(200, json={"data": []})

    async with httpx.AsyncClient(transport=MockTransport(handler)) as client:
        await policy.request(client, "GET", "https://provider.example/v1/models")

    assert seen_urls == ["https://8.8.8.8/v1/models"]


@pytest.mark.asyncio
async def test_connect_failure_retries_only_preapproved_addresses() -> None:
    policy = ProviderEgressPolicy(
        resolver=lambda _host, _port: ["8.8.8.8", "1.1.1.1"]
    )
    seen_hosts: list[str] = []

    def handler(request: Request) -> Response:
        seen_hosts.append(request.url.host or "")
        if len(seen_hosts) == 1:
            raise httpx.ConnectError("failed", request=request)
        return Response(200)

    async with httpx.AsyncClient(transport=MockTransport(handler)) as client:
        response = await policy.request(client, "GET", "https://provider.example/v1")

    assert response.status_code == 200
    assert seen_hosts == ["1.1.1.1", "8.8.8.8"]


@pytest.mark.asyncio
async def test_saved_target_is_reauthorized_before_every_request() -> None:
    resolutions = iter([["8.8.8.8"], ["127.0.0.1"]])
    policy = ProviderEgressPolicy(
        resolver=lambda _host, _port: next(resolutions)
    )
    async with httpx.AsyncClient(
        transport=MockTransport(lambda _request: Response(200))
    ) as client:
        assert (
            await policy.request(client, "GET", "https://provider.example/v1")
        ).status_code == 200
        with pytest.raises(ProviderEgressError) as exc_info:
            await policy.request(client, "GET", "https://provider.example/v1")
    assert exc_info.value.code == "provider_address_blocked"


def test_dynamic_multimodal_consumers_do_not_call_target_base_url_directly() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    files = [
        "server/multimodal/audio_catalog.py",
        "server/multimodal/audio_jobs.py",
        "server/multimodal/image_catalog.py",
        "server/multimodal/image_generation.py",
        "server/multimodal/realtime.py",
        "server/multimodal/stt.py",
        "server/multimodal/tts.py",
        "server/multimodal/video_analysis.py",
        "server/multimodal/video_catalog.py",
        "server/multimodal/video_jobs.py",
    ]
    violations: list[str] = []
    for relative in files:
        path = repository_root / relative
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in {"get", "post", "put", "patch", "request", "stream"}:
                continue
            if any(
                isinstance(child, ast.Attribute)
                and child.attr == "base_url"
                and isinstance(child.value, ast.Name)
                and child.value.id == "target"
                for argument in (*node.args, *node.keywords)
                for child in ast.walk(argument.value if isinstance(argument, ast.keyword) else argument)
            ):
                violations.append(f"{relative}:{node.lineno}")
    assert violations == []
