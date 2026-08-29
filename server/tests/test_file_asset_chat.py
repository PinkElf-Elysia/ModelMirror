from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from server import main as main_module
from server.file_assets import api as file_api
from server.file_assets.document_parser import ParsedDocument, ParsedSection
from server.file_assets.analysis import (
    FileAnalysisArtifact,
    FileAnalysisSection,
)
from server.file_assets.service import ResolvedChatFile
from server.file_assets.service import FileAssetServiceError
from server.main import (
    ChatMessage,
    ChatRequest,
    build_upstream_payload,
    chat_file_receipt_summary,
    chat_file_parts,
    chat_file_stream_succeeded,
    chat_file_terminal_events,
    chat_file_upstream_error,
    finalize_chat_file_stream,
    finalize_native_chat_file_events,
    log_chat_runtime_prepare_failure,
    prepare_chat_file_messages,
    should_fallback_model,
    validate_chat_file_request,
    validate_multimodal_content,
)
from server.main import app
from server.omniroute.config import OmniRouteSettings
from server.omniroute.telemetry import update_stream_state


def _asset_id(index: int = 0) -> str:
    return f"file_{index:032x}"


def _payload(
    *,
    files: int = 1,
    handling: str = "extract",
    gateway: str = "default",
    model_id: str = "openai/gpt-file",
    scope_id: str | None = "chat-session-1",
) -> ChatRequest:
    return ChatRequest.model_validate(
        {
            "model_id": model_id,
            "gateway": gateway,
            "file_scope_id": scope_id,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Summarize the attachments."},
                        *[
                            {
                                "type": "input_file",
                                "asset_id": _asset_id(index),
                                "handling": handling,
                                "confirmation_revision": 1,
                            }
                            for index in range(files)
                        ],
                    ],
                }
            ],
        }
    )


def test_chat_file_contract_is_explicit_and_scope_bound() -> None:
    payload = _payload(handling="native")

    parts = chat_file_parts(payload.messages)

    assert len(parts) == 1
    assert parts[0].asset_id == _asset_id()
    assert parts[0].handling == "native"
    assert parts[0].confirmation_revision == 1
    assert payload.file_scope_id == "chat-session-1"


def test_chat_file_contract_rejects_missing_confirmation_revision() -> None:
    payload = _payload().model_dump(mode="json")
    payload["messages"][0]["content"][1].pop("confirmation_revision")

    with pytest.raises(ValueError):
        ChatRequest.model_validate(payload)


def test_chat_analysis_artifact_is_explicit_and_rendered_as_untrusted_user_data() -> None:
    asset_id = _asset_id()
    payload = ChatRequest.model_validate(
        {
            "model_id": "current/chat-model",
            "file_scope_id": "chat-session-1",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Use the reviewed result."},
                        {
                            "type": "input_file",
                            "asset_id": asset_id,
                            "handling": "extract",
                            "confirmation_revision": 2,
                            "analysis_artifact_id": "artifact_" + "a" * 32,
                            "analysis_prompt": "Compare the recognized totals.",
                        },
                    ],
                }
            ],
        }
    )
    resolved = ResolvedChatFile(
        asset_id=asset_id,
        scope_id="chat-session-1",
        display_name="scan.pdf",
        format_id="pdf",
        media_type="application/pdf",
        byte_size=100,
        handling="extract",
        analysis_prompt="Compare the recognized totals.",
        analysis_artifact=FileAnalysisArtifact(
            asset_id=asset_id,
            source_filename="scan.pdf",
            source_sha256="b" * 64,
            format="pdf",
            mode="provider_ocr",
            target_id="target",
            connection_name="OpenRouter",
            model_id="exact/downstream-model",
            selected_pages=(1,),
            sections=(
                FileAnalysisSection(kind="ocr_text", text="Total: 42", page=1),
            ),
            processed_pages=1,
            extracted_chars=9,
        ),
    )

    prepared = prepare_chat_file_messages(payload, (resolved,))
    content = prepared.messages[0].content
    assert isinstance(content, list)
    rendered = "\n".join(
        part.text for part in content if getattr(part, "type", None) == "text"
    )
    assert "Compare the recognized totals." in rendered
    assert "untrusted user data" in rendered
    assert "Total: 42" in rendered
    assert "provider_ocr" in rendered
    assert not any(getattr(part, "type", None) == "input_file" for part in content)


def test_chat_file_scope_is_required() -> None:
    payload = _payload(scope_id=None)

    with pytest.raises(HTTPException) as raised:
        validate_chat_file_request(payload)

    assert raised.value.status_code == 422


@pytest.mark.parametrize("gateway", ["auto", "omniroute"])
def test_smart_routing_requires_local_extraction(gateway: str) -> None:
    payload = _payload(
        gateway=gateway,
        model_id="auto" if gateway == "auto" else "auto/fast",
        handling="native",
    )

    with pytest.raises(HTTPException) as raised:
        validate_chat_file_request(payload)

    assert raised.value.status_code == 422


@pytest.mark.asyncio
async def test_chat_rejects_more_than_five_files() -> None:
    payload = _payload(files=6)

    with pytest.raises(HTTPException) as raised:
        await validate_multimodal_content(
            payload.model_id,
            payload.messages,
            trust_gateway_catalog=True,
        )

    assert raised.value.status_code == 400


@pytest.mark.asyncio
async def test_chat_files_are_only_allowed_on_latest_user_message() -> None:
    payload = _payload()
    payload.messages.append(
        ChatMessage.model_validate(
            {"role": "user", "content": "Follow-up without the attachment."}
        )
    )

    with pytest.raises(HTTPException) as raised:
        await validate_multimodal_content(
            payload.model_id,
            payload.messages,
            trust_gateway_catalog=True,
        )

    assert raised.value.status_code == 400


@pytest.mark.asyncio
async def test_chat_files_cannot_mix_with_other_media() -> None:
    payload = _payload()
    assert isinstance(payload.messages[0].content, list)
    mixed = [
        *payload.messages[0].content,
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,AA=="},
        },
    ]
    payload.messages[0] = ChatMessage.model_validate(
        {"role": "user", "content": mixed}
    )

    with pytest.raises(HTTPException) as raised:
        await validate_multimodal_content(
            payload.model_id,
            payload.messages,
            trust_gateway_catalog=True,
        )

    assert raised.value.status_code == 400


class _CatalogCoordinator:
    def __init__(self, *, stale: bool = False, availability: str = "live") -> None:
        self.stale = stale
        self.availability = availability

    async def get_catalog(self):
        return SimpleNamespace(
            router_status="online",
            stale=self.stale,
            source="native",
            models=[
                SimpleNamespace(
                    invocation_id="openai/gpt-file",
                    invocable=True,
                    availability=self.availability,
                    input_modalities=["text", "file"],
                    output_modalities=["text"],
                    operations=["analyze_document", "chat"],
                )
            ],
        )


@pytest.mark.asyncio
async def test_capabilities_only_expose_native_pdf_for_live_openrouter_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHAT_FILE_INPUT_ENABLED", "true")
    monkeypatch.setenv("FILE_ASSET_STORE_MODE", "shadow")
    monkeypatch.setenv(
        "LLM_GATEWAY_URL",
        "https://openrouter.ai/api/v1/chat/completions",
    )
    monkeypatch.setenv("LLM_GATEWAY_KEY", "test-key")
    monkeypatch.setattr(
        file_api,
        "get_catalog_coordinator",
        lambda: _CatalogCoordinator(),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(
            "/api/files/capabilities",
            params={"purpose": "chat", "model_id": "openai/gpt-file"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["model_specific"] is True
    document = next(
        item
        for item in payload["capabilities"]
        if item["input_kind"] == "document"
    )
    assert [item["handling"] for item in document["handling_options"]] == [
        "extract",
        "native",
    ]
    assert document["handling_options"][1]["format_ids"] == ["pdf"]


@pytest.mark.asyncio
async def test_capabilities_expose_native_pdf_from_managed_control_plane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHAT_FILE_INPUT_ENABLED", "true")
    monkeypatch.setenv("FILE_ASSET_STORE_MODE", "shadow")
    monkeypatch.setenv("MODEL_CONTROL_CHAT_DOCUMENT_ENABLED", "true")
    monkeypatch.delenv("LLM_GATEWAY_URL", raising=False)
    monkeypatch.delenv("LLM_GATEWAY_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    class ManagedControl:
        @staticmethod
        def feature_enabled(entry_id: str) -> bool:
            assert entry_id == "chat_document_native"
            return True

        def __init__(self, _router_service) -> None:
            pass

        def public_status(
            self,
            entry_id: str,
            model_id: str,
            execution_shape: str,
        ):
            assert (
                entry_id,
                model_id,
                execution_shape,
            ) == (
                "chat_document_native",
                "openai/gpt-file",
                "chat_document_stream",
            )
            return SimpleNamespace(
                status="managed_required",
                available=True,
            )

    monkeypatch.setattr(
        file_api,
        "ProviderWorkloadControlService",
        ManagedControl,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(
            "/api/files/capabilities",
            params={"purpose": "chat", "model_id": "openai/gpt-file"},
        )

    assert response.status_code == 200
    document = next(
        item
        for item in response.json()["capabilities"]
        if item["input_kind"] == "document"
    )
    assert [item["handling"] for item in document["handling_options"]] == [
        "extract",
        "native",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("gateway_url", "stale", "availability"),
    [
        ("https://newapi.example/v1/chat/completions", False, "live"),
        ("https://openrouter.ai/api/v1/chat/completions", True, "live"),
        ("https://openrouter.ai/api/v1/chat/completions", False, "degraded"),
    ],
)
async def test_capabilities_fail_closed_without_live_openrouter_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    gateway_url: str,
    stale: bool,
    availability: str,
) -> None:
    monkeypatch.setenv("CHAT_FILE_INPUT_ENABLED", "true")
    monkeypatch.setenv("FILE_ASSET_STORE_MODE", "shadow")
    monkeypatch.setenv("LLM_GATEWAY_URL", gateway_url)
    monkeypatch.setenv("LLM_GATEWAY_KEY", "test-key")
    monkeypatch.setattr(
        file_api,
        "get_catalog_coordinator",
        lambda: _CatalogCoordinator(
            stale=stale,
            availability=availability,
        ),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(
            "/api/files/capabilities",
            params={"purpose": "chat", "model_id": "openai/gpt-file"},
        )

    assert response.status_code == 200
    document = next(
        item
        for item in response.json()["capabilities"]
        if item["input_kind"] == "document"
    )
    assert [item["handling"] for item in document["handling_options"]] == [
        "extract"
    ]


@pytest.mark.asyncio
async def test_chat_native_pdf_verification_requires_live_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        main_module,
        "get_catalog_coordinator",
        lambda: _CatalogCoordinator(),
    )
    assert await main_module.model_supports_native_pdf_input(
        "openai/gpt-file"
    )

    monkeypatch.setattr(
        main_module,
        "get_catalog_coordinator",
        lambda: _CatalogCoordinator(availability="degraded"),
    )
    assert not await main_module.model_supports_native_pdf_input(
        "openai/gpt-file"
    )


def _resolved_file(*, handling: str) -> ResolvedChatFile:
    return ResolvedChatFile(
        asset_id=_asset_id(),
        scope_id="chat-session-1",
        display_name="private.pdf" if handling == "native" else "notes.md",
        format_id="pdf" if handling == "native" else "markdown",
        media_type=(
            "application/pdf" if handling == "native" else "text/markdown"
        ),
        byte_size=16,
        handling=handling,  # type: ignore[arg-type]
        native_content=b"%PDF-private" if handling == "native" else None,
        parsed_document=ParsedDocument(
            format="pdf" if handling == "native" else "markdown",
            title="private.pdf" if handling == "native" else "notes.md",
            sections=(ParsedSection(text="untrusted file text", page=1),),
            extracted_chars=19,
        ),
    )


def test_native_pdf_payload_uses_explicit_native_parser_contract() -> None:
    payload = _payload(handling="native")
    resolved = (_resolved_file(handling="native"),)

    upstream = build_upstream_payload(
        payload,
        payload.model_id,
        resolved_chat_files=resolved,
    )

    assert upstream["plugins"] == [
        {"id": "file-parser", "pdf": {"engine": "native"}}
    ]
    file_part = upstream["messages"][0]["content"][1]
    assert file_part["type"] == "file"
    assert file_part["file"]["filename"] == "private.pdf"
    assert file_part["file"]["file_data"].startswith(
        "data:application/pdf;base64,"
    )


def test_extracted_file_becomes_untrusted_user_text_without_original() -> None:
    payload = _payload(handling="extract")
    resolved = (_resolved_file(handling="extract"),)

    prepared = prepare_chat_file_messages(payload, resolved)
    upstream = build_upstream_payload(prepared, prepared.model_id)

    assert "plugins" not in upstream
    assert upstream["messages"][0]["role"] == "user"
    serialized = str(upstream["messages"])
    assert "不可信的用户数据" in serialized
    assert "untrusted file text" in serialized
    assert "%PDF-private" not in serialized
    assert "input_file" not in serialized


def test_native_pdf_never_uses_existing_model_fallback() -> None:
    payload = _payload(handling="native")

    assert not should_fallback_model(
        503,
        "model temporarily unavailable",
        {"error": "unavailable"},
        payload.model_id,
        payload.messages,
    )


def test_file_stream_requires_content_transport_and_terminal_signal() -> None:
    state: dict[str, object] = {}
    update_stream_state(
        'data: {"choices":[{"delta":{"content":"answer"}}]}',
        state,
    )
    assert not chat_file_stream_succeeded(
        state,
        transport_completed=True,
        runtime_status="completed",
    )

    update_stream_state(
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
        state,
    )
    assert chat_file_stream_succeeded(
        state,
        transport_completed=True,
        runtime_status="completed",
    )
    assert not chat_file_stream_succeeded(
        state,
        transport_completed=False,
        runtime_status="completed",
    )
    assert not chat_file_stream_succeeded(
        state,
        transport_completed=True,
        runtime_status="error",
    )


def test_done_only_stream_is_not_a_successful_file_answer() -> None:
    state: dict[str, object] = {}
    update_stream_state("data: [DONE]", state)

    assert not chat_file_stream_succeeded(
        state,
        transport_completed=True,
        runtime_status="completed",
    )


class _FinalizationService:
    def __init__(self, result: bool) -> None:
        self.result = result
        self.calls: list[tuple[tuple[ResolvedChatFile, ...], bool]] = []

    def finalize_chat_inputs(
        self,
        files: tuple[ResolvedChatFile, ...],
        *,
        success: bool,
    ) -> bool:
        self.calls.append((files, success))
        return self.result


@pytest.mark.asyncio
async def test_file_receipt_uses_actual_finalization_result() -> None:
    resolved = (_resolved_file(handling="extract"),)
    removed_service = _FinalizationService(True)
    retained_service = _FinalizationService(False)

    removed_retained = await finalize_chat_file_stream(
        removed_service,
        resolved,
        success=True,
    )
    retained = await finalize_chat_file_stream(
        retained_service,
        resolved,
        success=True,
    )

    assert removed_retained is False
    assert retained is True
    assert removed_service.calls == [(resolved, True)]
    assert retained_service.calls == [(resolved, True)]
    assert chat_file_receipt_summary(
        resolved,
        originals_retained=removed_retained,
    )["originals_retained"] is False
    assert chat_file_receipt_summary(
        resolved,
        originals_retained=retained,
    )["originals_retained"] is True


@pytest.mark.asyncio
async def test_failed_file_stream_retains_original_for_retry() -> None:
    resolved = (_resolved_file(handling="extract"),)
    service = _FinalizationService(False)

    originals_retained = await finalize_chat_file_stream(
        service,
        resolved,
        success=False,
    )

    assert originals_retained is True
    assert service.calls == [(resolved, False)]


@pytest.mark.asyncio
async def test_file_finalization_exception_is_reported_as_retained(
    caplog: pytest.LogCaptureFixture,
) -> None:
    resolved = (_resolved_file(handling="extract"),)

    class _FailingFinalizationService:
        def finalize_chat_inputs(self, *_args, **_kwargs) -> bool:
            raise OSError("sensitive local path must not be logged")

    originals_retained = await finalize_chat_file_stream(
        _FailingFinalizationService(),
        resolved,
        success=True,
    )

    assert originals_retained is True
    assert "original_cleanup_failed" in caplog.text
    assert "sensitive local path" not in caplog.text


def test_file_terminal_sequence_has_one_message_end_before_done() -> None:
    receipt = {
        "requested_model": "openai/gpt-file",
        "files": {"count": 1, "originals_retained": False},
    }

    events = chat_file_terminal_events(receipt)
    rendered = b"".join(events)

    assert rendered.count(b"event: route_receipt") == 1
    assert rendered.count(b"event: message_end") == 1
    assert rendered.count(b"data: [DONE]") == 1
    assert rendered.index(b"event: message_end") < rendered.index(b"data: [DONE]")
    failure = b"".join(chat_file_terminal_events(None))
    assert b'"error"' in failure
    assert b"event: message_end" not in failure
    assert failure.index(b'"error"') < failure.index(b"data: [DONE]")
    assert chat_file_terminal_events(
        None,
        failure_error_emitted=True,
    ) == (b"data: [DONE]\n\n",)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transport_completed", "include_partial_content"),
    [(True, True), (False, False)],
    ids=["incomplete", "transport_failure"],
)
async def test_native_router_file_failure_fake_stream_is_explicit_and_retained(
    transport_completed: bool,
    include_partial_content: bool,
) -> None:
    state: dict[str, object] = {}
    if include_partial_content:
        update_stream_state(
            'data: {"choices":[{"delta":{"content":"partial"}}]}',
            state,
        )
    resolved = (_resolved_file(handling="extract"),)
    service = _FinalizationService(False)
    receipt = {"requested_model": "auto", "version": "2"}

    succeeded, events = await finalize_native_chat_file_events(
        service,
        resolved,
        stream_state=state,
        transport_completed=transport_completed,
        runtime_status="error" if not transport_completed else "completed",
        receipt=receipt,
        failure_error_emitted=False,
    )
    rendered = b"".join(events)

    assert succeeded is False
    assert service.calls == [(resolved, False)]
    assert b'"error"' in rendered
    assert rendered.count(b"data: [DONE]") == 1
    assert b"event: message_end" not in rendered
    assert b"event: route_receipt" not in rendered
    assert "files" not in receipt


@pytest.mark.parametrize("status", [401, 402, 413, 429, 500])
def test_file_upstream_errors_are_stable_and_body_independent(status: int) -> None:
    message, code = chat_file_upstream_error(status)

    assert message
    assert code == f"file_upstream_http_{status}"
    assert "secret-file-body" not in message


@pytest.mark.parametrize("native", [False, True])
def test_file_runtime_prepare_failure_log_omits_exception_content(
    caplog: pytest.LogCaptureFixture,
    native: bool,
) -> None:
    caplog.set_level("WARNING")

    log_chat_runtime_prepare_failure(
        native=native,
        direct_file_requested=True,
        model_id="openai/gpt-file",
        error=RuntimeError("secret extracted file body"),
    )

    assert "code=runtime_prepare_failed" in caplog.text
    assert "openai/gpt-file" in caplog.text
    assert "secret extracted file body" not in caplog.text


class _ResolvedFileService:
    def __init__(self, resolved: ResolvedChatFile) -> None:
        self.resolved = (resolved,)
        self.resolve_calls: list[tuple[object, str, bool]] = []
        self.finalize_calls: list[tuple[tuple[ResolvedChatFile, ...], bool]] = []

    def resolve_chat_inputs(
        self,
        selections,
        *,
        scope_id: str,
        native_pdf_verified: bool = False,
    ) -> tuple[ResolvedChatFile, ...]:
        self.resolve_calls.append((tuple(selections), scope_id, native_pdf_verified))
        return self.resolved

    def finalize_chat_inputs(
        self,
        files: tuple[ResolvedChatFile, ...],
        *,
        success: bool,
    ) -> bool:
        self.finalize_calls.append((files, success))
        return success


class _FakeUpstreamResponse:
    def __init__(self, chunks: tuple[str, ...]) -> None:
        self.status_code = 200
        self.headers = {"x-request-id": "req_file_fake"}
        self._chunks = chunks
        self.closed = False

    async def aiter_text(self):
        for chunk in self._chunks:
            yield chunk

    async def aread(self) -> bytes:
        return b""

    async def aclose(self) -> None:
        self.closed = True


class _FakeUpstreamClient:
    def __init__(self, chunks: tuple[str, ...]) -> None:
        self.response = _FakeUpstreamResponse(chunks)
        self.requests: list[SimpleNamespace] = []
        self.closed = False

    def build_request(self, method, url, *, headers, json):
        request = SimpleNamespace(
            method=method,
            url=url,
            headers=headers,
            payload=json,
        )
        self.requests.append(request)
        return request

    async def send(self, request, *, stream: bool):
        assert stream is True
        assert request in self.requests
        return self.response

    async def aclose(self) -> None:
        self.closed = True


_SUCCESS_STREAM = (
    'data: {"model":"openai/gpt-file","choices":[{"delta":{"content":"ok"}}]}\n\n',
    'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
    "data: [DONE]\n\n",
)


async def _post_file_chat_with_fake_upstream(
    monkeypatch: pytest.MonkeyPatch,
    *,
    payload: ChatRequest,
    service: _ResolvedFileService,
    gateway_url: str,
    chunks: tuple[str, ...] = _SUCCESS_STREAM,
):
    fake_upstream = _FakeUpstreamClient(chunks)
    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: (gateway_url, "test-key"),
    )
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(main_module, "get_file_asset_service", lambda: service)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as api_client:
        monkeypatch.setattr(
            main_module.httpx,
            "AsyncClient",
            lambda **_kwargs: fake_upstream,
        )
        response = await api_client.post(
            "/api/chat",
            json=payload.model_dump(mode="json"),
        )
    return response, fake_upstream


@pytest.mark.asyncio
async def test_confirmed_extracted_file_is_sent_as_untrusted_user_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _ResolvedFileService(_resolved_file(handling="extract"))
    response, upstream = await _post_file_chat_with_fake_upstream(
        monkeypatch,
        payload=_payload(handling="extract"),
        service=service,
        gateway_url="https://newapi.example/v1/chat/completions",
    )

    assert response.status_code == 200
    assert len(upstream.requests) == 1
    sent = upstream.requests[0].payload
    assert upstream.requests[0].url == "https://newapi.example/v1/chat/completions"
    serialized = str(sent["messages"])
    assert "不可信的用户数据" in serialized
    assert "untrusted file text" in serialized
    assert "input_file" not in serialized
    assert "file_data" not in serialized
    assert "plugins" not in sent
    assert response.text.count("event: message_end") == 1
    assert response.text.index("event: message_end") < response.text.index(
        "data: [DONE]"
    )
    assert service.finalize_calls == [(service.resolved, True)]


@pytest.mark.asyncio
async def test_omniroute_extract_sends_only_confirmed_untrusted_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _ResolvedFileService(_resolved_file(handling="extract"))
    monkeypatch.setattr(
        main_module,
        "get_omniroute_settings",
        lambda: OmniRouteSettings(
            enabled=True,
            base_url="http://omniroute.test:20128",
            api_key="test-key",
            default_router="newapi",
        ),
    )

    response, upstream = await _post_file_chat_with_fake_upstream(
        monkeypatch,
        payload=_payload(
            handling="extract",
            gateway="omniroute",
            model_id="auto",
        ),
        service=service,
        gateway_url="https://unused-default.example/v1/chat/completions",
    )

    assert response.status_code == 200
    assert len(upstream.requests) == 1
    request = upstream.requests[0]
    assert request.url == "http://omniroute.test:20128/v1/chat/completions"
    assert request.payload["model"] == "auto"
    serialized_messages = str(request.payload["messages"])
    assert "不可信的用户数据" in serialized_messages
    assert "untrusted file text" in serialized_messages
    assert "input_file" not in serialized_messages
    assert "file_data" not in serialized_messages
    assert "data:application/pdf" not in serialized_messages
    assert "%PDF-private" not in serialized_messages
    assert "plugins" not in request.payload
    selections = service.resolve_calls[0][0]
    assert len(selections) == 1
    assert selections[0].confirmation_revision == 1
    assert selections[0].handling == "extract"
    assert response.text.count("event: route_receipt") == 1
    assert response.text.count("event: message_end") == 1
    assert response.text.rstrip().endswith("data: [DONE]")
    assert service.finalize_calls == [(service.resolved, True)]


@pytest.mark.asyncio
async def test_confirmed_native_pdf_only_uses_verified_openrouter_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _ResolvedFileService(_resolved_file(handling="native"))
    monkeypatch.setattr(
        main_module,
        "get_catalog_coordinator",
        lambda: _CatalogCoordinator(),
    )
    response, upstream = await _post_file_chat_with_fake_upstream(
        monkeypatch,
        payload=_payload(handling="native"),
        service=service,
        gateway_url="https://openrouter.ai/api/v1/chat/completions",
    )

    assert response.status_code == 200
    assert len(upstream.requests) == 1
    request = upstream.requests[0]
    assert request.url == "https://openrouter.ai/api/v1/chat/completions"
    assert request.payload["plugins"] == [
        {"id": "file-parser", "pdf": {"engine": "native"}}
    ]
    file_part = request.payload["messages"][0]["content"][1]
    assert file_part["type"] == "file"
    assert file_part["file"]["file_data"].startswith(
        "data:application/pdf;base64,"
    )
    assert service.resolve_calls[0][2] is True
    assert service.finalize_calls == [(service.resolved, True)]


@pytest.mark.asyncio
async def test_unconfirmed_or_cross_scope_file_never_constructs_upstream_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("https://newapi.example/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)

    class _ForbiddenClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("rejected file request must not create an upstream client")

    class _CrossScopeService:
        def resolve_chat_inputs(self, *_args, **_kwargs):
            raise FileAssetServiceError(
                404,
                "file_asset_not_found",
                "文件不存在或不属于当前作用域。",
            )

    raw_unconfirmed = _payload().model_dump(mode="json")
    raw_unconfirmed["messages"][0]["content"][1].pop("confirmation_revision")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as api_client:
        monkeypatch.setattr(main_module.httpx, "AsyncClient", _ForbiddenClient)
        unconfirmed = await api_client.post("/api/chat", json=raw_unconfirmed)
        monkeypatch.setattr(
            main_module,
            "get_file_asset_service",
            lambda: _CrossScopeService(),
        )
        cross_scope = await api_client.post(
            "/api/chat",
            json=_payload(scope_id="other-session").model_dump(mode="json"),
        )

    assert unconfirmed.status_code == 422
    assert cross_scope.status_code == 404


@pytest.mark.asyncio
async def test_incomplete_fake_file_stream_keeps_original_and_reports_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _ResolvedFileService(_resolved_file(handling="extract"))
    response, _upstream = await _post_file_chat_with_fake_upstream(
        monkeypatch,
        payload=_payload(handling="extract"),
        service=service,
        gateway_url="https://newapi.example/v1/chat/completions",
        chunks=(
            'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n',
        ),
    )

    assert response.status_code == 200
    assert '"error"' in response.text
    assert "event: message_end" not in response.text
    assert service.finalize_calls == [(service.resolved, False)]
