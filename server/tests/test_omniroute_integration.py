from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import pytest_asyncio

from server import main as main_module
from server.main import ChatRoutingOptions, app, omniroute_model_for_request
from server.omniroute.catalog import OmniRouteCatalogService, normalize_models
from server.omniroute.client import OmniRouteClientError
from server.omniroute.config import OmniRouteSettings
from server.omniroute.telemetry import (
    build_route_receipt,
    parse_omniroute_headers,
    update_stream_state,
)


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client


def enabled_settings() -> OmniRouteSettings:
    return OmniRouteSettings(
        enabled=True,
        base_url="http://omniroute:20128",
        api_key="test-key",
        default_router="newapi",
        catalog_ttl_seconds=30,
        stale_ttl_seconds=600,
        budget_headers_enabled=True,
    )


def test_catalog_normalization_uses_root_and_deduplicates_ids() -> None:
    models = normalize_models(
        {
            "object": "list",
            "data": [
                {
                    "id": "openai/gpt-4o",
                    "root": "gpt-4o",
                    "name": "GPT-4o",
                    "owned_by": "openai",
                    "context_length": 128000,
                    "input_modalities": ["text", "image"],
                    "capabilities": {"vision": True, "tools": True},
                },
                {"id": "openai/gpt-4o", "root": "duplicate"},
                {"id": "auto/fast", "type": "chat"},
                {"id": "openai/dall-e-3", "type": "image"},
                {"owned_by": "invalid"},
            ],
        }
    )

    assert len(models) == 2
    assert models[0].profile_id == "gpt-4o"
    assert models[0].invocation_id == "openai/gpt-4o"
    assert models[0].provider == "openai"
    assert models[0].input_modalities == ["text", "image"]
    assert models[0].output_modalities == ["text"]
    assert models[0].operations == ["analyze_image", "chat"]
    assert models[0].primary_operation == "chat"
    assert models[0].interaction_status == "ready"
    assert models[0].ui_entrypoint == "chat"
    assert "vision" in models[0].capabilities
    assert models[1].invocation_id == "openai/dall-e-3"
    assert models[1].invocable is False
    assert models[1].availability == "disabled"


def test_catalog_operations_route_specialized_models_to_adapted_ui() -> None:
    models = normalize_models(
        {
            "data": [
                {
                    "id": "openai/whisper-1",
                    "input_modalities": ["audio"],
                    "output_modalities": ["transcription"],
                },
                {
                    "id": "microsoft/mai-voice-2",
                    "input_modalities": ["text"],
                    "output_modalities": ["speech"],
                },
                {
                    "id": "example/unverified-voice",
                    "input_modalities": ["text"],
                    "output_modalities": ["speech"],
                },
                {
                    "id": "example/video-generator",
                    "input_modalities": ["text", "image"],
                    "output_modalities": ["video"],
                },
                {
                    "id": "example/image-generator",
                    "input_modalities": ["text", "image"],
                    "output_modalities": ["image"],
                },
                {
                    "id": "openrouter/auto",
                    "input_modalities": ["text", "image"],
                    "output_modalities": ["text", "image"],
                },
                {
                    "id": "openai/text-embedding-3-small",
                    "input_modalities": ["text"],
                    "output_modalities": ["embeddings"],
                },
                {
                    "id": "example/audio-analyst",
                    "input_modalities": ["text", "audio"],
                    "output_modalities": ["text"],
                },
            ]
        }
    )

    by_id = {model.invocation_id: model for model in models}
    transcription = by_id["openai/whisper-1"]
    assert transcription.operations == ["transcribe"]
    assert transcription.primary_operation == "transcribe"
    assert transcription.interaction_status == "ready"
    assert transcription.ui_entrypoint == "chat"

    speech = by_id["microsoft/mai-voice-2"]
    assert speech.operations == ["synthesize_speech"]
    assert speech.primary_operation == "synthesize_speech"
    assert speech.interaction_status == "ready"
    assert speech.ui_entrypoint == "chat"

    unverified_speech = by_id["example/unverified-voice"]
    assert unverified_speech.operations == ["synthesize_speech"]
    assert unverified_speech.interaction_status == "planned"
    assert unverified_speech.ui_entrypoint == "planned"

    image_generator = by_id["example/image-generator"]
    assert image_generator.operations == ["generate_image"]
    assert image_generator.primary_operation == "generate_image"
    assert image_generator.interaction_status == "planned"

    auto = by_id["openrouter/auto"]
    assert auto.operations == ["analyze_image", "chat"]
    assert auto.primary_operation == "chat"

    video = by_id["example/video-generator"]
    assert video.operations == ["generate_video"]
    assert video.ui_entrypoint == "planned"

    embedding = by_id["openai/text-embedding-3-small"]
    assert embedding.operations == ["embed"]
    assert embedding.interaction_status == "ready"
    assert embedding.ui_entrypoint == "rag"

    audio = by_id["example/audio-analyst"]
    assert audio.operations == ["analyze_audio", "chat"]
    assert audio.primary_operation == "chat"
    assert audio.ui_entrypoint == "chat"


@pytest.mark.asyncio
async def test_catalog_uses_last_good_result_as_stale() -> None:
    class FakeClient:
        calls = 0

        def __init__(self, _settings: OmniRouteSettings):
            pass

        async def fetch_models(self) -> dict[str, Any]:
            FakeClient.calls += 1
            if FakeClient.calls > 1:
                raise OmniRouteClientError(502, "offline")
            return {
                "data": [
                    {
                        "id": "openai/gpt-4o",
                        "root": "gpt-4o",
                        "owned_by": "openai",
                    }
                ]
            }

        async def fetch_route_candidates(self, channel: str) -> dict[str, Any]:
            return {
                "channel": channel,
                "candidates": [{"reachable": True, "excluded": False}],
            }

    service = OmniRouteCatalogService(
        enabled_settings,
        client_factory=FakeClient,  # type: ignore[arg-type]
    )
    live = await service.get_catalog(force=True)
    stale = await service.get_catalog(force=True)

    assert live.router_status == "online"
    assert live.models[0].invocable is True
    assert stale.router_status == "stale"
    assert stale.stale is True
    assert stale.models[0].availability == "degraded"


@pytest.mark.asyncio
async def test_disabled_catalog_does_not_create_live_candidates() -> None:
    settings = OmniRouteSettings(
        enabled=False,
        base_url="http://omniroute:20128",
        api_key="",
        default_router="newapi",
    )
    service = OmniRouteCatalogService(lambda: settings)

    catalog = await service.get_catalog()
    status = await service.get_status()

    assert catalog.source == "bundled"
    assert catalog.router_status == "disabled"
    assert catalog.models == []
    assert all(route.invocable is False for route in catalog.routes)
    assert status.redacted is True
    assert status.configured is False


@pytest.mark.asyncio
async def test_advertised_auto_routes_do_not_require_management_candidates_api() -> None:
    class FakeClient:
        def __init__(self, _settings: OmniRouteSettings):
            pass

        async def fetch_models(self) -> dict[str, Any]:
            return {
                "data": [
                    {"id": "auto/fast", "root": "auto/fast", "owned_by": "combo"},
                    {"id": "auto/cheap", "root": "auto/cheap", "owned_by": "combo"},
                    {"id": "auto/coding", "root": "auto/coding", "owned_by": "combo"},
                    {"id": "auto/vision", "root": "auto/vision", "owned_by": "combo"},
                    {"id": "auto/smart", "root": "auto/smart", "owned_by": "combo"},
                ]
            }

        async def fetch_route_candidates(self, _channel: str) -> dict[str, Any]:
            raise AssertionError("advertised routes must not use the management API")

    service = OmniRouteCatalogService(
        enabled_settings,
        client_factory=FakeClient,  # type: ignore[arg-type]
    )

    catalog = await service.get_catalog(force=True)

    assert catalog.router_status == "online"
    assert all(route.invocable for route in catalog.routes)
    assert all(route.availability == "live" for route in catalog.routes)


def test_route_receipt_prefers_stream_usage_and_does_not_claim_zero_cost() -> None:
    headers = parse_omniroute_headers(
        {
            "X-OmniRoute-Model": "openai/gpt-4o",
            "X-OmniRoute-Provider": "openai",
            "X-OmniRoute-Response-Cost": "0",
            "X-OmniRoute-Request-Id": "req-1",
        }
    )
    stream_state: dict[str, Any] = {}
    update_stream_state(
        'data: {"model":"openai/gpt-4o","usage":{"prompt_tokens":12,'
        '"completion_tokens":8,"total_tokens":20}}',
        stream_state,
    )
    receipt = build_route_receipt(
        requested_model="auto",
        header_state=headers,
        stream_state=stream_state,
    )

    assert receipt["actual_model"] == "openai/gpt-4o"
    assert receipt["tokens"]["total"] == 20
    assert receipt["response_cost_usd"] == 0
    assert receipt["cost_kind"] == "actual"

    unknown = build_route_receipt(
        requested_model="auto",
        header_state={"response_cost_usd": 0},
        stream_state={},
    )
    assert unknown["response_cost_usd"] is None
    assert unknown["cost_kind"] == "unavailable"


def test_omniroute_mode_maps_to_published_auto_aliases() -> None:
    assert (
        omniroute_model_for_request(
            "auto",
            ChatRoutingOptions(mode="balanced"),
        )
        == "auto"
    )
    assert (
        omniroute_model_for_request("auto", ChatRoutingOptions(mode="fast"))
        == "auto/fast"
    )
    assert (
        omniroute_model_for_request("auto", ChatRoutingOptions(mode="quality"))
        == "auto/smart"
    )
    assert (
        omniroute_model_for_request("auto", ChatRoutingOptions(mode="cheap"))
        == "auto/cheap"
    )
    assert (
        omniroute_model_for_request("auto", ChatRoutingOptions(mode="reliable"))
        == "auto/lkgp"
    )
    assert (
        omniroute_model_for_request("auto", ChatRoutingOptions(mode="offline"))
        == "auto/offline"
    )
    assert (
        omniroute_model_for_request(
            "auto/coding",
            ChatRoutingOptions(mode="quality"),
        )
        == "auto/coding"
    )


def test_stream_telemetry_reads_final_comments_without_overwriting_real_model() -> None:
    state: dict[str, Any] = {}
    update_stream_state(
        'data: {"model":"openai/gpt-5.6-sol","provider":"Azure",'
        '"choices":[{"delta":{"content":"hello"}}],'
        '"usage":{"prompt_tokens":20,"completion_tokens":2,"total_tokens":22}}',
        state,
    )
    update_stream_state(": x-omniroute-model=auto", state)
    update_stream_state(": x-omniroute-provider=openrouter", state)
    update_stream_state(": x-omniroute-latency-ms=321", state)
    update_stream_state(": x-omniroute-response-cost=0.00125", state)
    update_stream_state(": x-omniroute-tokens-in=1", state)

    receipt = build_route_receipt(
        requested_model="auto",
        header_state={},
        stream_state=state,
    )

    assert state["content_observed"] is True
    assert receipt["actual_model"] == "openai/gpt-5.6-sol"
    assert receipt["provider"] == "openrouter"
    assert receipt["latency_ms"] == 321
    assert receipt["tokens"]["input"] == 20
    assert receipt["response_cost_usd"] == 0.00125
    assert receipt["cost_kind"] == "actual"


@pytest.mark.asyncio
async def test_omniroute_chat_forwards_headers_and_emits_one_receipt(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent_requests: list[dict[str, Any]] = []

    class FakeResponse:
        status_code = 200
        headers = {
            "X-OmniRoute-Model": "openai/gpt-4o",
            "X-OmniRoute-Provider": "openai",
            "X-OmniRoute-Decision": "balanced",
            "X-OmniRoute-Request-Id": "req-123",
            "X-OmniRoute-Version": "3.8.49",
            "X-OmniRoute-Response-Cost": "0.00125",
            "X-OmniRoute-Latency-Ms": "42",
        }
        content = b""

        async def aiter_text(self):
            yield 'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
            yield (
                'data: {"model":"openai/gpt-4o","choices":[],'
                '"usage":{"prompt_tokens":5,"completion_tokens":2,"total_tokens":7}}\n\n'
            )
            yield "data: [DONE]\n\n"

        async def aread(self):
            return self.content

        async def aclose(self):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def build_request(self, method, url, headers, json):
            request = {
                "method": method,
                "url": url,
                "headers": headers,
                "json": json,
            }
            sent_requests.append(request)
            return request

        async def send(self, request, stream):
            assert stream is True
            return FakeResponse()

        async def aclose(self):
            return None

    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(
        main_module,
        "get_omniroute_settings",
        lambda: enabled_settings(),
    )
    monkeypatch.setattr(main_module.httpx, "AsyncClient", FakeClient)

    response = await client.post(
        "/api/chat",
        json={
            "model_id": "auto",
            "gateway": "omniroute",
            "messages": [{"role": "user", "content": "hi"}],
            "routing": {
                "session_id": "session-1",
                "mode": "balanced",
                "budget_usd": 0.05,
                "budget_fallback": "strict",
            },
        },
    )

    assert response.status_code == 200, response.text
    assert sent_requests[0]["url"].endswith("/v1/chat/completions")
    assert sent_requests[0]["headers"]["X-OmniRoute-Mode"] == "balanced"
    assert sent_requests[0]["headers"]["X-OmniRoute-Budget"] == "0.05"
    assert sent_requests[0]["headers"]["X-OmniRoute-Budget-Fallback"] == "strict"
    assert sent_requests[0]["headers"]["X-Session-Id"] == "session-1"
    assert sent_requests[0]["json"]["model"] == "auto"
    assert response.text.count("event: route_receipt") == 1
    assert response.text.index("event: route_receipt") < response.text.index(
        "data: [DONE]"
    )
    receipt_line = next(
        line
        for event in response.text.split("\n\n")
        if event.startswith("event: route_receipt")
        for line in event.splitlines()
        if line.startswith("data:")
    )
    receipt = json.loads(receipt_line[5:].strip())
    assert receipt["actual_model"] == "openai/gpt-4o"
    assert receipt["provider"] == "openai"
    assert receipt["tokens"]["total"] == 7
    assert receipt["response_cost_usd"] == 0.00125
    assert receipt["request_id"] == "req-123"


@pytest.mark.asyncio
async def test_omniroute_empty_success_stream_becomes_explicit_error(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResponse:
        status_code = 200
        headers = {
            "X-OmniRoute-Model": "claude-sonnet-4.6",
            "X-OmniRoute-Provider": "aug",
            "X-OmniRoute-Request-Id": "req-empty",
        }
        content = b""

        async def aiter_text(self):
            yield ": x-omniroute-tokens-out=0\n"
            yield "data: [DONE]\n\n"

        async def aread(self):
            return self.content

        async def aclose(self):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def build_request(self, method, url, headers, json):
            return {
                "method": method,
                "url": url,
                "headers": headers,
                "json": json,
            }

        async def send(self, request, stream):
            assert stream is True
            return FakeResponse()

        async def aclose(self):
            return None

    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(
        main_module,
        "get_omniroute_settings",
        lambda: enabled_settings(),
    )
    monkeypatch.setattr(main_module.httpx, "AsyncClient", FakeClient)

    response = await client.post(
        "/api/chat",
        json={
            "model_id": "auto",
            "gateway": "omniroute",
            "messages": [{"role": "user", "content": "hi"}],
            "routing": {"mode": "quality"},
        },
    )

    assert response.status_code == 200
    assert response.text.count("event: route_receipt") == 1
    assert "模型服务返回了成功状态，但没有生成正文" in response.text
    assert response.text.rstrip().endswith("data: [DONE]")


@pytest.mark.asyncio
async def test_routing_options_rejected_for_default_gateway(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://new-api/v1/chat/completions", "key"),
    )
    response = await client.post(
        "/api/chat",
        json={
            "model_id": "openai/gpt-4o",
            "messages": [{"role": "user", "content": "hi"}],
            "routing": {"mode": "cheap"},
        },
    )

    assert response.status_code == 400
    assert "仅适用于智能调度" in response.json()["error"]
