from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from server import main as main_module
from server.file_assets import api as file_api_module
from server.file_assets.chat_output import (
    ChatOutputError,
    ChatOutputResult,
    run_chat_output_turn,
)
from server.file_assets.output_contracts import FileOutputResponse
from server.file_assets.service import FileAssetServiceError
from server.main import (
    ChatRequest,
    app,
    validate_chat_output_request,
    validate_chat_output_reuse_inputs,
)


VERIFIED_OUTPUT_TOOL_MODEL_IDS = (
    "openai/gpt-5.6-sol",
    "openai/gpt-5.6-luna",
    "google/gemini-3.6-flash",
    "anthropic/claude-sonnet-5",
    "x-ai/grok-4.5",
    "deepseek/deepseek-v4-pro",
    "qwen/qwen3.8-max",
    "moonshotai/kimi-k3",
    "minimax/minimax-m3",
    "z-ai/glm-5.2",
    "xiaomi/mimo-v2.5",
    "cohere/north-mini-code:free",
)


def _output(status: str = "completed") -> FileOutputResponse:
    return FileOutputResponse(
        output_id="output_" + "a" * 32,
        asset_id="file_" + "b" * 32 if status == "completed" else None,
        purpose="chat",
        scope_id="chat-output-scope",
        producer_kind="chat_tool",
        display_name="report.txt",
        format="plain_text",
        media_type="text/plain",
        byte_size=12 if status == "completed" else 0,
        preview_kind="text",
        status=status,
        expires_at="2026-08-16T00:00:00+00:00" if status == "completed" else None,
        error_code=None if status == "completed" else "output_render_failed",
        source_message_id="chat-output-scope",
        created_at="2026-08-09T00:00:00+00:00",
        updated_at="2026-08-09T00:00:00+00:00",
    )


class _OutputService:
    def __init__(self) -> None:
        self.calls: list[tuple[dict, dict]] = []

    def render_spec(self, specification: dict, **context):
        self.calls.append((specification, context))
        return _output()


def _sse(events: list[dict], *, request_id: str) -> httpx.Response:
    body = "".join(
        f"data: {json.dumps(event, separators=(',', ':'))}\n\n" for event in events
    ) + "data: [DONE]\n\n"
    return httpx.Response(
        200,
        text=body,
        headers={"content-type": "text/event-stream", "x-request-id": request_id},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("model_id", VERIFIED_OUTPUT_TOOL_MODEL_IDS)
async def test_native_tool_round_trip_is_exact_single_and_no_fallback(
    model_id: str,
) -> None:
    requests: list[dict] = []
    arguments = json.dumps(
        {"format_id": "plain_text", "filename": "report.txt", "content": "hello\n"},
        separators=(",", ":"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            midpoint = len(arguments) // 2
            return _sse(
                [
                    {
                        "model": model_id,
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "call_1",
                                            "function": {
                                                "name": "modelmirror_create_file",
                                                "arguments": arguments[:midpoint],
                                            },
                                        }
                                    ]
                                },
                                "finish_reason": None,
                            }
                        ],
                    },
                    {
                        "model": model_id,
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "function": {"arguments": arguments[midpoint:]},
                                        }
                                    ]
                                },
                                "finish_reason": "tool_calls",
                            }
                        ],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                    },
                ],
                request_id="req-first",
            )
        return _sse(
            [
                {
                    "model": model_id,
                    "choices": [
                        {
                            "delta": {
                                "content": (
                                    "Created the requested file: "
                                    "[report.txt](sandbox:/mnt/data/report.txt)."
                                )
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 8, "completion_tokens": 4},
                }
            ],
            request_id="req-second",
        )

    service = _OutputService()
    result = await run_chat_output_turn(
        url="https://gateway.example/v1/chat/completions",
        key="test-key",
        headers={"Authorization": "Bearer test-key"},
        client_kwargs={"transport": httpx.MockTransport(handler)},
        model_id=model_id,
        messages=[{"role": "user", "content": "Create a text report."}],
        temperature=0.2,
        max_tokens=1000,
        top_p=None,
        seed=None,
        stop=None,
        output_service=service,  # type: ignore[arg-type]
        scope_id="chat-output-scope",
        output_context_id="chat-output-scope",
        provider_tag="test-provider",
    )
    assert len(requests) == 2
    assert requests[0]["model"] == requests[1]["model"] == model_id
    assert requests[0]["parallel_tool_calls"] is False
    assert requests[0]["provider"] == requests[1]["provider"] == {
        "only": ["test-provider"],
        "allow_fallbacks": False,
    }
    assert [tool["function"]["name"] for tool in requests[0]["tools"]] == [
        "modelmirror_create_file"
    ]
    assert requests[1]["tool_choice"] == "none"
    assert service.calls[0][0]["content"] == "hello\n"
    assert service.calls[0][1]["scope_id"] == "chat-output-scope"
    assert result.output is not None and result.output.status == "completed"
    final_text = "".join(result.text_chunks)
    assert "Created" in final_text
    assert "sandbox:" not in final_text
    assert "`report.txt`" in final_text
    assert result.usage["prompt_tokens"] == 18
    assert result.usage["completion_tokens"] == 9


@pytest.mark.asyncio
async def test_model_replacement_is_rejected_before_any_file_render() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return _sse(
            [
                {
                    "model": "provider/replacement",
                    "choices": [{"delta": {"content": "text"}, "finish_reason": "stop"}],
                }
            ],
            request_id="req-replaced",
        )

    service = _OutputService()
    with pytest.raises(ChatOutputError) as error:
        await run_chat_output_turn(
            url="https://gateway.example/v1/chat/completions",
            key="test-key",
            headers={},
            client_kwargs={"transport": httpx.MockTransport(handler)},
            model_id="provider/tool-model",
            messages=[{"role": "user", "content": "hello"}],
            temperature=0.2,
            max_tokens=100,
            top_p=None,
            seed=None,
            stop=None,
            output_service=service,  # type: ignore[arg-type]
            scope_id="chat-output-scope",
            output_context_id="chat-output-scope",
        )
    assert error.value.error_code == "output_model_replaced"
    assert service.calls == []


class _Catalog:
    def __init__(
        self,
        model_ids: tuple[str, ...] = ("openai/gpt-5.6-luna",),
    ) -> None:
        self.model_ids = model_ids

    async def get_catalog(self):
        return SimpleNamespace(
            router_status="online",
            stale=False,
            source="native",
            models=[
                SimpleNamespace(
                    invocation_id=model_id,
                    invocable=True,
                    availability="live",
                    input_modalities=["text"],
                    output_modalities=["text"],
                    operations=["chat"],
                    capabilities=["tools"],
                )
                for model_id in self.model_ids
            ],
        )


def _request(**updates) -> ChatRequest:
    payload = {
        "model_id": "openai/gpt-5.6-luna",
        "messages": [{"role": "user", "content": "Create a report."}],
        "gateway": "default",
        "file_scope_id": "chat-output-scope",
        "output_context_id": "turn-output-1",
        "output_mode": "allowlisted",
    }
    payload.update(updates)
    return ChatRequest.model_validate(payload)


@pytest.mark.asyncio
async def test_output_request_is_fail_closed_before_upstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FILE_OUTPUT_ASSETS_ENABLED", "false")
    monkeypatch.setenv("CHAT_FILE_OUTPUT_TOOL_ENABLED", "false")
    with pytest.raises(Exception) as disabled:
        await validate_chat_output_request(
            _request(),
            gateway_url="https://openrouter.ai/api/v1/chat/completions",
            direct_audio_requested=False,
            direct_video_requested=False,
            direct_file_requested=False,
        )
    assert disabled.value.status_code == 503

    monkeypatch.setenv("FILE_OUTPUT_ASSETS_ENABLED", "true")
    monkeypatch.setenv("CHAT_FILE_OUTPUT_TOOL_ENABLED", "true")
    monkeypatch.setattr(main_module, "get_catalog_coordinator", lambda: _Catalog())
    with pytest.raises(Exception) as smart:
        await validate_chat_output_request(
            _request(gateway="auto"),
            gateway_url="https://openrouter.ai/api/v1/chat/completions",
            direct_audio_requested=False,
            direct_video_requested=False,
            direct_file_requested=False,
        )
    assert smart.value.status_code == 422

    await validate_chat_output_request(
        _request(),
        gateway_url="https://openrouter.ai/api/v1/chat/completions",
        direct_audio_requested=False,
        direct_video_requested=False,
        direct_file_requested=False,
    )


@pytest.mark.asyncio
async def test_output_gate_accepts_only_real_provider_verified_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FILE_OUTPUT_ASSETS_ENABLED", "true")
    monkeypatch.setenv("CHAT_FILE_OUTPUT_TOOL_ENABLED", "true")
    monkeypatch.setattr(
        main_module,
        "get_catalog_coordinator",
        lambda: _Catalog(VERIFIED_OUTPUT_TOOL_MODEL_IDS),
    )

    await validate_chat_output_request(
        _request(model_id="openai/gpt-5.6-luna"),
        gateway_url="https://openrouter.ai/api/v1/chat/completions",
        direct_audio_requested=False,
        direct_video_requested=False,
        direct_file_requested=False,
    )

    for model_id in (
        *(item for item in VERIFIED_OUTPUT_TOOL_MODEL_IDS if item != "openai/gpt-5.6-luna"),
        "provider/unlisted-model",
    ):
        with pytest.raises(Exception) as unverified:
            await validate_chat_output_request(
                _request(model_id=model_id),
                gateway_url="https://openrouter.ai/api/v1/chat/completions",
                direct_audio_requested=False,
                direct_video_requested=False,
                direct_file_requested=False,
            )
        assert unverified.value.status_code == 422

    with pytest.raises(Exception) as wrong_connection:
        await validate_chat_output_request(
            _request(model_id="openai/gpt-5.6-luna"),
            gateway_url="https://gateway.example/v1/chat/completions",
            direct_audio_requested=False,
            direct_video_requested=False,
            direct_file_requested=False,
        )
    assert wrong_connection.value.status_code == 422


@pytest.mark.asyncio
async def test_output_capabilities_expose_only_luna_on_exact_openrouter_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FILE_OUTPUT_ASSETS_ENABLED", "true")
    monkeypatch.setenv("CHAT_FILE_OUTPUT_TOOL_ENABLED", "true")
    monkeypatch.setenv(
        "LLM_GATEWAY_URL",
        "https://openrouter.ai/api/v1/chat/completions",
    )
    monkeypatch.setenv("LLM_GATEWAY_KEY", "test-key")
    monkeypatch.setattr(
        file_api_module,
        "get_catalog_coordinator",
        lambda: _Catalog(VERIFIED_OUTPUT_TOOL_MODEL_IDS),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        luna = await client.get(
            "/api/files/output-capabilities",
            params={"purpose": "chat", "model_id": "openai/gpt-5.6-luna"},
        )
        qwen = await client.get(
            "/api/files/output-capabilities",
            params={"purpose": "chat", "model_id": "qwen/qwen3.8-max"},
        )
        monkeypatch.setenv(
            "LLM_GATEWAY_URL",
            "https://gateway.example/v1/chat/completions",
        )
        wrong_connection = await client.get(
            "/api/files/output-capabilities",
            params={"purpose": "chat", "model_id": "openai/gpt-5.6-luna"},
        )

    assert luna.status_code == 200
    assert luna.json()["interaction_status"] == "ready"
    assert qwen.status_code == 200
    assert qwen.json()["interaction_status"] == "planned"
    assert wrong_connection.status_code == 200
    assert wrong_connection.json()["interaction_status"] == "planned"


def test_output_reuse_binding_is_all_or_none_and_uses_exact_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_file = {
        "type": "input_file",
        "asset_id": "file_" + "c" * 32,
        "handling": "extract",
        "confirmation_revision": 1,
        "output_id": "output_" + "d" * 32,
    }
    with pytest.raises(ValueError):
        _request(
            output_mode="none",
            output_context_id=None,
            messages=[{"role": "user", "content": [base_file]}],
        )

    calls: list[dict] = []

    class _ReuseService:
        def validate_reuse_confirmation(self, output_id: str, **kwargs) -> None:
            calls.append({"output_id": output_id, **kwargs})

    service = _ReuseService()
    monkeypatch.setattr(main_module, "get_file_output_service", lambda: service)
    payload = _request(
        output_mode="none",
        output_context_id=None,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        **base_file,
                        "output_confirmation_revision": 7,
                    }
                ],
            }
        ],
    )
    validate_chat_output_reuse_inputs(payload)
    assert calls == [
        {
            "output_id": "output_" + "d" * 32,
            "asset_id": "file_" + "c" * 32,
            "purpose": main_module.FilePurpose.CHAT,
            "scope_id": "chat-output-scope",
            "handling": "extract",
            "target_id": "openai/gpt-5.6-luna",
            "gateway": "default",
            "output_confirmation_revision": 7,
        }
    ]


def test_media_output_reuse_ignores_client_image_bytes_and_resolves_attachments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_id = "output_" + "m" * 32
    asset_id = "file_" + "n" * 32
    audio_output_id = "output_" + "q" * 32
    audio_asset_id = "file_" + "r" * 32
    attachment_id = "att_" + "p" * 32
    with pytest.raises(ValueError):
        _request(
            output_mode="none",
            output_context_id=None,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/png;base64,Y2xpZW50"},
                            "output_id": output_id,
                            "output_asset_id": asset_id,
                        }
                    ],
                }
            ],
        )

    calls: list[dict] = []

    class _ReuseService:
        def resolve_media_reuse(self, resolved_output_id: str, **kwargs):
            calls.append({"output_id": resolved_output_id, **kwargs})
            media_type = "image/png" if kwargs["expected_kind"] == "image" else "audio/wav"
            content = b"trusted-image" if kwargs["expected_kind"] == "image" else b"trusted-audio"
            return SimpleNamespace(media_type=media_type), content

    monkeypatch.setattr(main_module, "get_file_output_service", lambda: _ReuseService())
    payload = _request(
        output_mode="none",
        output_context_id=None,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,Y2xpZW50"},
                        "output_id": output_id,
                        "output_asset_id": asset_id,
                        "output_confirmation_revision": 4,
                    },
                    {
                        "type": "input_audio",
                        "attachment_id": attachment_id,
                        "output_id": audio_output_id,
                        "output_asset_id": audio_asset_id,
                        "output_confirmation_revision": 4,
                    },
                ],
            }
        ],
    )
    images, attachments = validate_chat_output_reuse_inputs(payload)
    assert images[output_id] == "data:image/png;base64,dHJ1c3RlZC1pbWFnZQ=="
    assert attachments[attachment_id] == ("audio", b"trusted-audio")
    assert [call["expected_kind"] for call in calls] == ["image", "audio"]

    upstream = main_module.upstream_chat_messages(
        [
            main_module.ChatMessage.model_validate(
                {
                    "role": "user",
                    "content": [payload.messages[0].content[0].model_dump(mode="json")],
                }
            )
        ],
        resolved_output_images=images,
    )
    assert upstream[0]["content"] == [
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,dHJ1c3RlZC1pbWFnZQ=="},
        }
    ]


def test_output_reuse_mismatch_is_rejected_before_file_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ReuseService:
        def validate_reuse_confirmation(self, _output_id: str, **_kwargs) -> None:
            raise FileAssetServiceError(
                409,
                "output_reuse_confirmation_required",
                "Confirm reuse again.",
            )

    service = _ReuseService()
    monkeypatch.setattr(main_module, "get_file_output_service", lambda: service)
    payload = _request(
        output_mode="none",
        output_context_id=None,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_file",
                        "asset_id": "file_" + "c" * 32,
                        "handling": "extract",
                        "confirmation_revision": 1,
                        "output_id": "output_" + "d" * 32,
                        "output_confirmation_revision": 7,
                    }
                ],
            }
        ],
    )
    with pytest.raises(Exception) as error:
        validate_chat_output_reuse_inputs(payload)
    assert error.value.status_code == 409
    assert error.value.detail == "Confirm reuse again."


@pytest.mark.asyncio
async def test_media_reuse_mismatch_reaches_zero_upstream_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upstream_requests: list[httpx.Request] = []

    class _ReuseService:
        def resolve_media_reuse(self, _output_id: str, **_kwargs):
            raise FileAssetServiceError(
                409,
                "output_reuse_confirmation_required",
                "Confirm media reuse again.",
            )

    def handler(request: httpx.Request) -> httpx.Response:
        upstream_requests.append(request)
        return _sse([], request_id="must-not-run")

    monkeypatch.setattr(main_module, "get_file_output_service", lambda: _ReuseService())
    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("https://openrouter.ai/api/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(
        main_module,
        "llm_client_kwargs",
        lambda: {"transport": httpx.MockTransport(handler)},
    )
    request = _request(
        output_mode="none",
        output_context_id=None,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,Y2xpZW50"},
                        "output_id": "output_" + "s" * 32,
                        "output_asset_id": "file_" + "t" * 32,
                        "output_confirmation_revision": 9,
                    }
                ],
            }
        ],
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/chat", json=request.model_dump(mode="json")
        )

    assert response.status_code == 409
    assert response.json()["error"] == "Confirm media reuse again."
    assert upstream_requests == []


@pytest.mark.asyncio
async def test_chat_endpoint_emits_one_output_event_before_terminal_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FILE_OUTPUT_ASSETS_ENABLED", "true")
    monkeypatch.setenv("CHAT_FILE_OUTPUT_TOOL_ENABLED", "true")
    monkeypatch.setattr(main_module, "get_catalog_coordinator", lambda: _Catalog())
    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("https://openrouter.ai/api/v1/chat/completions", "test-key"),
    )

    async def fake_turn(**_kwargs):
        return ChatOutputResult(
            text_chunks=("Answer body.",),
            output=_output(),
            actual_model="openai/gpt-5.6-luna",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            request_id="req-output",
        )

    monkeypatch.setattr(main_module, "run_chat_output_turn", fake_turn)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post("/api/chat", json=_request().model_dump(mode="json"))
    assert response.status_code == 200
    body = response.text
    assert body.count("event: output_file") == 1
    assert body.count("event: message_end") == 1
    assert body.count("data: [DONE]") == 1
    assert body.index("Answer body.") < body.index("event: output_file")
    assert body.index("event: output_file") < body.index("event: route_receipt")
    assert body.index("event: route_receipt") < body.index("event: message_end")
    assert body.index("event: message_end") < body.index("data: [DONE]")


@pytest.mark.asyncio
async def test_chat_endpoint_registers_only_embedded_media_and_preserves_sse_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FILE_OUTPUT_ASSETS_ENABLED", "true")
    monkeypatch.setenv("CHAT_FILE_OUTPUT_TOOL_ENABLED", "false")
    embedded_png = b"\x89PNG\r\n\x1a\nprovider-image"
    embedded_url = "data:image/png;base64," + base64.b64encode(embedded_png).decode("ascii")
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        event = {
            "model": "provider/media-model",
            "choices": [
                {
                    "delta": {
                        "content": [
                            {"type": "text", "text": "Generated image."},
                            {"type": "image_url", "image_url": {"url": embedded_url}},
                            {
                                "type": "image_url",
                                "image_url": {"url": "https://example.invalid/not-fetched.png"},
                            },
                        ]
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 4, "completion_tokens": 3, "total_tokens": 7},
        }
        return _sse([event], request_id="req-media")

    class _MediaOutputService:
        def __init__(self) -> None:
            self.calls: list[tuple[bytes, dict]] = []

        def register_bytes(self, content: bytes, **context):
            self.calls.append((content, context))
            return FileOutputResponse(
                output_id="output_" + "e" * 32,
                asset_id="file_" + "f" * 32,
                purpose="chat",
                scope_id=context["scope_id"],
                producer_kind=context["producer_kind"],
                display_name=context["filename"],
                format=context["format_id"],
                media_type=context["media_type"],
                byte_size=len(content),
                preview_kind="image",
                status="completed",
                expires_at="2026-08-16T00:00:00+00:00",
                source_message_id=context["source_message_id"],
                created_at="2026-08-09T00:00:00+00:00",
                updated_at="2026-08-09T00:00:00+00:00",
            )

    output_service = _MediaOutputService()
    monkeypatch.setattr(main_module, "get_file_output_service", lambda: output_service)
    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("https://gateway.example/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(
        main_module,
        "llm_client_kwargs",
        lambda: {"transport": httpx.MockTransport(handler)},
    )
    request = _request(
        model_id="provider/media-model",
        output_mode="none",
        output_context_id="assistant-message-1",
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post("/api/chat", json=request.model_dump(mode="json"))

    assert response.status_code == 200
    assert len(requests) == 1
    assert len(output_service.calls) == 1
    content, context = output_service.calls[0]
    assert content == embedded_png
    assert context["producer_kind"] == "chat_image"
    assert context["scope_id"] == "chat-output-scope"
    assert context["source_message_id"] == "assistant-message-1"
    body = response.text
    assert body.count("event: output_file") == 1
    assert "not-fetched.png" in body
    assert body.index("event: output_file") < body.index("event: route_receipt")
    assert body.index("event: route_receipt") < body.index("event: message_end")
    assert body.index("event: message_end") < body.index("data: [DONE]")
