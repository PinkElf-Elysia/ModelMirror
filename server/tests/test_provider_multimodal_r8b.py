from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from httpx import MockTransport, Request, Response
from PIL import Image

from server import main as main_module
from server.file_assets.document_parser import ParsedDocument, ParsedSection
from server.file_assets.service import ResolvedChatFile
from server.main import app
from server.model_router import configure_model_router, get_model_router_service
from server.model_router.egress import ProviderEgressPolicy
from server.model_router.multimodal_control import ProviderMultimodalTarget
from server.model_router.multimodal_gateway import (
    ManagedMultimodalError,
    ManagedMultimodalGateway,
)
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.schemas import (
    ProviderWorkloadActivationRequest,
    ProviderWorkloadBindingUpdate,
    ProviderWorkloadCertificationRequest,
    ProviderWorkloadPolicyUpdate,
    RouterConnectionCreate,
)
from server.model_router.service import ModelRouterService
from server.model_router.workload_control import (
    ProviderWorkloadCertificationService,
    ProviderWorkloadControlService,
    SYNTHETIC_NATIVE_PDF_DATA_URL,
    SYNTHETIC_VISION_PNG_DATA_URL,
)
from server.multimodal.image_catalog import (
    ImageModelCatalogResponse,
    ImageModelProfile,
)
from server.multimodal.api import _http_error
from server.multimodal.image_generation import ImageGenerationService
from server.multimodal.stt import MultimodalServiceError
from server.multimodal.vision_understanding import VisionUnderstandingService


def png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (2, 2), color=(0, 0, 255)).save(output, format="PNG")
    return output.getvalue()


def test_synthetic_vision_png_is_decodable() -> None:
    prefix = "data:image/png;base64,"
    assert SYNTHETIC_VISION_PNG_DATA_URL.startswith(prefix)
    payload = base64.b64decode(
        SYNTHETIC_VISION_PNG_DATA_URL.removeprefix(prefix),
        validate=True,
    )
    image = Image.open(io.BytesIO(payload))
    assert image.format == "PNG"
    assert image.size == (2, 2)
    image.verify()


class _ChatResponse(httpx.Response):
    def __init__(
        self,
        request: object,
        *,
        status_code: int = 200,
        actual_model: str = "provider/vision",
    ) -> None:
        content = (
            (
                f'data: {{"model":"{actual_model}","choices":'
                '[{"delta":{"content":"OK"},"finish_reason":null}]}\n\n'
                'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
                "data: [DONE]\n\n"
            ).encode("utf-8")
            if status_code < 400
            else b'{"error":{"message":"redacted-upstream-body"}}'
        )
        super().__init__(
            status_code,
            headers={"content-type": "text/event-stream"},
            content=content,
            request=httpx.Request("POST", str(request.get("url", "https://provider.test"))),
        )


class _ChatClient:
    def __init__(
        self,
        sent: list[dict[str, object]],
        *,
        status_code: int,
        actual_model: str = "provider/vision",
    ) -> None:
        self.sent = sent
        self.status_code = status_code
        self.actual_model = actual_model

    def build_request(self, method, url, **kwargs):
        return {"method": method, "url": str(url), **kwargs}

    async def send(self, request, *, stream, follow_redirects=False):
        assert stream is True
        assert follow_redirects is False
        self.sent.append(request)
        return _ChatResponse(
            request,
            status_code=self.status_code,
            actual_model=self.actual_model,
        )

    async def aclose(self) -> None:
        return None


def router_service(
    tmp_path: Path,
    transport: MockTransport,
    *,
    kind: str = "openrouter",
    scopes: list[str] | None = None,
) -> tuple[ModelRouterService, object]:
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="R8B Provider",
            kind=kind,
            base_url="https://provider.example/api/v1",
            api_key="r8b-secret",
            scopes=scopes or ["chat", "image"],
        ),
    )
    return (
        ModelRouterService(
            repository,
            client_factory=lambda: httpx.AsyncClient(
                transport=transport,
                follow_redirects=False,
                trust_env=False,
            ),
            egress_policy=ProviderEgressPolicy(
                resolver=lambda _host, _port: ["8.8.8.8"]
            ),
        ),
        connection,
    )


def test_r8b_adapter_resolves_exact_endpoint_without_secret_in_repr() -> None:
    openrouter = ProviderMultimodalTarget.create(
        provider_kind="openrouter",
        connection_id="conn-1",
        base_url="https://openrouter.example/api/v1/chat/completions",
        api_key="do-not-print",
        adapter_contract="openrouter_images_v1",
        execution_shape="image_generation",
    )
    compatible = ProviderMultimodalTarget.create(
        provider_kind="newapi",
        connection_id="conn-2",
        base_url="https://newapi.example/v1",
        api_key="also-secret",
        adapter_contract="openai_compatible_images_generations_v1",
        execution_shape="image_generation",
    )

    assert openrouter.endpoint_url == "https://openrouter.example/api/v1/images"
    assert compatible.endpoint_url == "https://newapi.example/v1/images/generations"
    assert "do-not-print" not in repr(openrouter)
    assert "also-secret" not in repr(compatible)


def test_native_pdf_certification_asset_is_a_single_page_xref_pdf() -> None:
    document = base64.b64decode(SYNTHETIC_NATIVE_PDF_DATA_URL.split(",", 1)[1])
    startxref = int(document.rsplit(b"startxref\n", 1)[1].splitlines()[0])

    assert document.startswith(b"%PDF-1.4")
    assert b"/Type /Pages /Count 1" in document
    assert b"/Type /Page " in document
    assert document[startxref:].startswith(b"xref\n")
    assert document.endswith(b"%%EOF\n")


@pytest.mark.asyncio
async def test_chat_image_certification_and_runtime_each_send_one_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[Request] = []

    def handler(request: Request) -> Response:
        requests.append(request)
        if request.method == "GET":
            return Response(200, json={"data": [{"id": "provider/vision"}]})
        body = json.loads(request.content)
        assert body["model"] == "provider/vision"
        assert body["messages"][0]["content"][1]["type"] == "image_url"
        return Response(
            200,
            content=(
                b'data: {"model":"provider/vision","choices":'
                b'[{"delta":{"content":"OK"},"finish_reason":null}]}\n\n'
                b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
                b"data: [DONE]\n\n"
            ),
            headers={"content-type": "text/event-stream"},
        )

    transport = MockTransport(handler)
    service, connection = router_service(tmp_path, transport)
    certification = await ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    ).run(
        connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape="chat_image_stream",
            model_id="provider/vision",
            adapter_contract="openrouter_chat_multimodal_v1",
            acknowledge_billed_call=True,
        ),
        idempotency_key="chat-image-cert",
    )
    assert certification.status == "passed"

    monkeypatch.setenv("MODEL_CONTROL_CHAT_IMAGE_ENABLED", "true")
    control = ProviderWorkloadControlService(service)
    saved = control.update_policy(
        "chat_image",
        ProviderWorkloadPolicyUpdate(
            expected_revision=0,
            bindings=[
                ProviderWorkloadBindingUpdate(
                    execution_shape="chat_image_stream",
                    model_id="provider/vision",
                    connection_id=connection.id,
                    adapter_contract="openrouter_chat_multimodal_v1",
                )
            ],
        ),
    )
    control.activate(
        "chat_image",
        ProviderWorkloadActivationRequest(
            expected_revision=saved.revision,
            no_open_p0_p1=True,
            acknowledge_fail_closed=True,
        ),
    )
    gateway = ManagedMultimodalGateway.for_router(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    )
    dispatch = await gateway.prepare_chat_dispatch(
        "chat_image",
        execution_shape="chat_image_stream",
        requested_model="provider/vision",
        parent_run_reference="chat:test-r8b",
    )
    async with httpx.AsyncClient(
        transport=transport, follow_redirects=False, trust_env=False
    ) as client:
        response = await dispatch.send(
            client,
            {
                "model": "provider/vision",
                "stream": True,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "describe"},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": "data:image/png;base64,"
                                    + base64.b64encode(png_bytes()).decode("ascii")
                                },
                            },
                        ],
                    }
                ],
            },
        )
        await response.aread()
        await response.aclose()
        with pytest.raises(ManagedMultimodalError) as duplicate:
            await dispatch.send(client, {"model": "provider/vision"})
    assert duplicate.value.code == "provider_multimodal_duplicate_post_blocked"
    receipt = dispatch.complete(
        status="passed",
        result_class="success",
        actual_model="provider/vision",
    )
    assert receipt["call_count"] == 1
    assert [item.method for item in requests].count("POST") == 2
    assert "r8b-secret" not in json.dumps(receipt)


@pytest.mark.asyncio
@pytest.mark.parametrize("upstream_status", [200, 500])
async def test_api_chat_image_managed_path_is_exact_and_never_falls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    upstream_status: int,
) -> None:
    encoded = base64.b64encode(png_bytes()).decode("ascii")

    def certification_handler(request: Request) -> Response:
        if request.method == "GET":
            return Response(200, json={"data": [{"id": "provider/vision"}]})
        return Response(
            200,
            content=(
                b'data: {"model":"provider/vision","choices":'
                b'[{"delta":{"content":"OK"},"finish_reason":null}]}\n\n'
                b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
                b"data: [DONE]\n\n"
            ),
            headers={"content-type": "text/event-stream"},
        )

    transport = MockTransport(certification_handler)
    service, connection = router_service(tmp_path, transport, scopes=["chat", "image"])
    certification = await ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    ).run(
        connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape="chat_image_stream",
            model_id="provider/vision",
            adapter_contract="openrouter_chat_multimodal_v1",
            acknowledge_billed_call=True,
        ),
        idempotency_key=f"api-chat-image-{upstream_status}",
    )
    assert certification.status == "passed"
    monkeypatch.setenv("MODEL_CONTROL_CHAT_IMAGE_ENABLED", "true")
    control = ProviderWorkloadControlService(service)
    saved = control.update_policy(
        "chat_image",
        ProviderWorkloadPolicyUpdate(
            expected_revision=0,
            bindings=[
                ProviderWorkloadBindingUpdate(
                    execution_shape="chat_image_stream",
                    model_id="provider/vision",
                    connection_id=connection.id,
                    adapter_contract="openrouter_chat_multimodal_v1",
                )
            ],
        ),
    )
    control.activate(
        "chat_image",
        ProviderWorkloadActivationRequest(
            expected_revision=saved.revision,
            no_open_p0_p1=True,
            acknowledge_fail_closed=True,
        ),
    )

    original_service = get_model_router_service()
    configure_model_router(service)
    sent: list[dict[str, object]] = []
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("", ""),
    )
    payload = {
        "model_id": "provider/vision",
        "gateway": "default",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "describe"},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{encoded}"},
                    },
                ],
            }
        ],
    }
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            monkeypatch.setattr(
                main_module.httpx,
                "AsyncClient",
                lambda **_kwargs: _ChatClient(sent, status_code=upstream_status),
            )
            response = await client.post("/api/chat", json=payload)
            unsupported_payload = json.loads(json.dumps(payload))
            unsupported_payload["tool_mode"] = "mcp_tools"
            unsupported_response = await client.post(
                "/api/chat", json=unsupported_payload
            )
            mixed_payload = json.loads(json.dumps(payload))
            mixed_payload["file_scope_id"] = "chat-session-r8b"
            mixed_payload["messages"][0]["content"].append(
                {
                    "type": "input_file",
                    "asset_id": "file_" + "1" * 32,
                    "handling": "native",
                    "confirmation_revision": 1,
                }
            )
            mixed_response = await client.post("/api/chat", json=mixed_payload)
    finally:
        configure_model_router(original_service)

    assert len(sent) == 1
    assert "8.8.8.8" in str(sent[0]["url"])
    assert sent[0]["headers"]["Host"] == "provider.example"
    assert all(item.get("headers", {}).get("Authorization") != "Bearer legacy" for item in sent)
    assert unsupported_response.status_code == 422
    assert unsupported_response.json()["code"] == (
        "provider_multimodal_request_shape_unsupported"
    )
    assert mixed_response.status_code == 422
    assert mixed_response.json()["code"] == (
        "provider_multimodal_mixed_shape_unsupported"
    )
    if upstream_status == 200:
        assert response.status_code == 200
        assert response.text.count("event: route_receipt") == 1
        assert response.text.rstrip().endswith("data: [DONE]")
    else:
        assert response.status_code == 500
        body = response.json()
        assert body["code"] == "provider_workload_http_5xx"
        assert body["route_receipt"]["call_count"] == 1
        assert "redacted-upstream-body" not in response.text
    stored = service.repository.list_workload_receipts("local")
    assert len(stored["calls"]) == 1
    assert stored["calls"][0]["dispatched"] == 1
    assert "describe" not in json.dumps(stored)
    assert "OK" not in json.dumps(stored)

    restarted_repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    restarted = restarted_repository.list_workload_receipts("local")
    assert len(restarted["calls"]) == 1
    assert restarted["calls"][0]["status"] != "uncertain"


@pytest.mark.asyncio
async def test_image_generation_certification_uses_compatible_endpoint(
    tmp_path: Path,
) -> None:
    requests: list[Request] = []
    encoded = base64.b64encode(png_bytes()).decode("ascii")

    def handler(request: Request) -> Response:
        requests.append(request)
        if request.method == "GET":
            return Response(200, json={"data": [{"id": "provider/image"}]})
        assert request.url.path == "/api/v1/images/generations"
        return Response(
            200,
            json={
                "model": "provider/image",
                "data": [{"b64_json": encoded}],
            },
        )

    transport = MockTransport(handler)
    service, connection = router_service(
        tmp_path,
        transport,
        kind="newapi",
        scopes=["image"],
    )
    result = await ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    ).run(
        connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape="image_generation",
            model_id="provider/image",
            adapter_contract="openai_compatible_images_generations_v1",
            acknowledge_billed_call=True,
        ),
        idempotency_key="image-cert",
    )
    assert result.status == "passed"
    assert [item.method for item in requests].count("POST") == 1


@pytest.mark.asyncio
async def test_openrouter_image_certification_uses_declared_parameters_only(
    tmp_path: Path,
) -> None:
    requests: list[Request] = []
    encoded = base64.b64encode(png_bytes()).decode("ascii")

    def handler(request: Request) -> Response:
        requests.append(request)
        if request.method == "GET":
            return Response(200, json={"data": [{"id": "provider/image"}]})
        assert request.url.path == "/api/v1/images"
        body = json.loads(request.content)
        assert body == {
            "model": "provider/image",
            "prompt": "A single blue square on a white background.",
            "n": 1,
            "quality": "low",
            "aspect_ratio": "1:1",
        }
        return Response(
            200,
            json={"data": [{"b64_json": encoded}]},
        )

    transport = MockTransport(handler)
    service, connection = router_service(
        tmp_path,
        transport,
        kind="openrouter",
        scopes=["image"],
    )
    result = await ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    ).run(
        connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape="image_generation",
            model_id="provider/image",
            adapter_contract="openrouter_images_v1",
            acknowledge_billed_call=True,
        ),
        idempotency_key="openrouter-image-cert",
    )
    assert result.status == "passed"
    assert [item.method for item in requests].count("POST") == 1


@pytest.mark.asyncio
async def test_native_pdf_certification_uses_distinct_adapter_and_one_post(
    tmp_path: Path,
) -> None:
    requests: list[Request] = []

    def handler(request: Request) -> Response:
        requests.append(request)
        if request.method == "GET":
            return Response(200, json={"data": [{"id": "provider/pdf"}]})
        body = json.loads(request.content)
        assert body["plugins"] == [
            {"id": "file-parser", "pdf": {"engine": "native"}}
        ]
        file_part = body["messages"][0]["content"][1]
        assert file_part["type"] == "file"
        assert file_part["file"]["file_data"] == SYNTHETIC_NATIVE_PDF_DATA_URL
        return Response(
            200,
            content=(
                b'data: {"model":"provider/pdf","choices":'
                b'[{"delta":{"content":"OK"},"finish_reason":null}]}\n\n'
                b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
                b"data: [DONE]\n\n"
            ),
            headers={"content-type": "text/event-stream"},
        )

    transport = MockTransport(handler)
    service, connection = router_service(
        tmp_path,
        transport,
        scopes=["chat", "document"],
    )
    result = await ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    ).run(
        connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape="chat_document_stream",
            model_id="provider/pdf",
            adapter_contract="openrouter_chat_native_pdf_v1",
            acknowledge_billed_call=True,
        ),
        idempotency_key="native-pdf-certification",
    )

    assert result.status == "passed"
    assert [item.method for item in requests].count("POST") == 1


@pytest.mark.asyncio
async def test_api_chat_native_pdf_uses_exact_managed_adapter_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[Request] = []

    def certification_handler(request: Request) -> Response:
        requests.append(request)
        if request.method == "GET":
            return Response(200, json={"data": [{"id": "provider/pdf"}]})
        return Response(
            200,
            content=(
                b'data: {"model":"provider/pdf","choices":'
                b'[{"delta":{"content":"OK"},"finish_reason":null}]}\n\n'
                b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
                b"data: [DONE]\n\n"
            ),
            headers={"content-type": "text/event-stream"},
        )

    transport = MockTransport(certification_handler)
    service, connection = router_service(
        tmp_path,
        transport,
        scopes=["chat", "document"],
    )
    certification = await ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    ).run(
        connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape="chat_document_stream",
            model_id="provider/pdf",
            adapter_contract="openrouter_chat_native_pdf_v1",
            acknowledge_billed_call=True,
        ),
        idempotency_key="native-pdf-runtime-certification",
    )
    assert certification.status == "passed"

    monkeypatch.setenv("MODEL_CONTROL_CHAT_DOCUMENT_ENABLED", "true")
    control = ProviderWorkloadControlService(service)
    saved = control.update_policy(
        "chat_document_native",
        ProviderWorkloadPolicyUpdate(
            expected_revision=0,
            bindings=[
                ProviderWorkloadBindingUpdate(
                    execution_shape="chat_document_stream",
                    model_id="provider/pdf",
                    connection_id=connection.id,
                    adapter_contract="openrouter_chat_native_pdf_v1",
                )
            ],
        ),
    )
    control.activate(
        "chat_document_native",
        ProviderWorkloadActivationRequest(
            expected_revision=saved.revision,
            no_open_p0_p1=True,
            acknowledge_fail_closed=True,
        ),
    )

    class NativeFileService:
        def __init__(self) -> None:
            self.resolved = (
                ResolvedChatFile(
                    asset_id="file_" + "1" * 32,
                    scope_id="chat-session-r8b-pdf",
                    display_name="synthetic.pdf",
                    format_id="pdf",
                    media_type="application/pdf",
                    byte_size=16,
                    handling="native",
                    native_content=b"%PDF-R8B",
                    parsed_document=ParsedDocument(
                        format="pdf",
                        title="synthetic.pdf",
                        sections=(ParsedSection(text="fixed test", page=1),),
                        extracted_chars=10,
                    ),
                ),
            )

        def resolve_chat_inputs(self, *_args, **kwargs):
            assert kwargs["scope_id"] == "chat-session-r8b-pdf"
            assert kwargs["native_pdf_verified"] is True
            return self.resolved

        def finalize_chat_inputs(self, _files, *, success: bool):
            return success

    original_service = get_model_router_service()
    configure_model_router(service)
    sent: list[dict[str, object]] = []
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(
        main_module,
        "get_file_asset_service",
        lambda: NativeFileService(),
    )
    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("https://legacy-must-not-run.example/v1/chat/completions", "legacy"),
    )
    payload = {
        "model_id": "provider/pdf",
        "gateway": "default",
        "file_scope_id": "chat-session-r8b-pdf",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "summarize"},
                    {
                        "type": "input_file",
                        "asset_id": "file_" + "1" * 32,
                        "handling": "native",
                        "confirmation_revision": 1,
                    },
                ],
            }
        ],
    }
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            monkeypatch.setattr(
                main_module.httpx,
                "AsyncClient",
                lambda **_kwargs: _ChatClient(
                    sent,
                    status_code=200,
                    actual_model="provider/pdf",
                ),
            )
            response = await client.post("/api/chat", json=payload)
    finally:
        configure_model_router(original_service)

    assert response.status_code == 200
    assert len(sent) == 1
    assert "8.8.8.8" in str(sent[0]["url"])
    assert "legacy-must-not-run" not in str(sent)
    body = sent[0]["json"]
    assert body["plugins"] == [
        {"id": "file-parser", "pdf": {"engine": "native"}}
    ]
    file_part = body["messages"][0]["content"][1]
    assert file_part["type"] == "file"
    assert file_part["file"]["file_data"].startswith(
        "data:application/pdf;base64,"
    )
    assert response.text.count("event: route_receipt") == 1
    stored = service.repository.list_workload_receipts("local")
    assert len(stored["calls"]) == 1
    assert "summarize" not in json.dumps(stored)
    assert "%PDF-R8B" not in json.dumps(stored)


@pytest.mark.asyncio
async def test_vision_managed_path_never_calls_legacy_override() -> None:
    calls: list[str] = []

    class FakeRun:
        async def complete_vision_json(self, **_kwargs):
            calls.append("managed")
            return {
                "ocr_text": "",
                "visual_summary": "blue square",
                "tables": [],
                "charts": [],
                "language": "en",
                "warnings": [],
            }

        def finish_success(self):
            return {"status": "passed", "call_count": 1, "calls": []}

        def finish_failure(self, _reason):
            return {"status": "failed", "call_count": 1, "calls": []}

    class FakeGateway:
        def routing_mode(self, _entry):
            return "managed_required"

        def blocked_receipt(self, entry_id, reason_code):
            return {
                "entry_id": entry_id,
                "status": "failed",
                "call_count": 0,
                "reason_codes": [reason_code],
                "calls": [],
            }

        def exact_model_id(self, _entry, _shape, *, requested_model):
            return requested_model

        def start_run(self, *_args, **_kwargs):
            return FakeRun()

    def legacy_override(*_args):
        raise AssertionError("legacy target must not run")

    service = VisionUnderstandingService(
        request_override=legacy_override,
        managed_gateway=FakeGateway(),  # type: ignore[arg-type]
    )
    result = await service.analyze_bytes(
        png_bytes(),
        filename="square.png",
        source_id="source-1",
        config={
            "vision_model_id": "provider/vision",
            "pdf_page_strategy": "auto",
            "render_dpi": 144,
            "max_pages": 10,
            "max_image_edge": 1024,
            "failure_policy": "continue_on_error",
        },
        managed_entry_id="rag_vision",
    )
    assert calls == ["managed"]
    assert result.execution_mode == "managed"
    assert result.provider_route_receipts[0]["call_count"] == 1


@pytest.mark.asyncio
async def test_managed_image_generation_requires_idempotency_and_returns_receipt() -> None:
    encoded = base64.b64encode(png_bytes()).decode("ascii")

    class FakeRun:
        async def complete_image_generation(self, *, parse_response, **_kwargs):
            result = parse_response(
                httpx.Response(
                    200,
                    json={
                        "id": "img-1",
                        "model": "provider/image",
                        "data": [{"b64_json": encoded}],
                    },
                )
            )
            return result, {"status": "passed", "call_count": 1, "calls": []}

    class FakeGateway:
        def routing_mode(self, _entry):
            return "managed_required"

        def blocked_receipt(self, entry_id, reason_code):
            return {
                "entry_id": entry_id,
                "status": "failed",
                "call_count": 0,
                "reason_codes": [reason_code],
                "calls": [],
            }

        def exact_model_id(self, _entry, _shape, *, requested_model):
            return requested_model

        def start_run(self, *_args, **_kwargs):
            return FakeRun()

    class FakeCatalog:
        router_service = SimpleNamespace()

        async def get_catalog(self):
            return ImageModelCatalogResponse(
                source="openrouter",
                status="online",
                stale=False,
                synced_at=None,
                profiles=[
                    ImageModelProfile(
                        model_id="provider/image",
                        display_name="Image",
                        operation="generate_image",
                    )
                ],
            )

    service = ImageGenerationService(
        FakeCatalog(),  # type: ignore[arg-type]
        managed_gateway=FakeGateway(),  # type: ignore[arg-type]
    )
    arguments = {
        "model_id": "provider/image",
        "prompt": "blue square",
        "reference_filenames": [],
        "reference_content_types": [],
        "reference_contents": [],
    }
    with pytest.raises(MultimodalServiceError) as missing:
        await service.generate(**arguments)
    assert missing.value.code == "invalid_idempotency_key"
    assert missing.value.route_receipt["call_count"] == 0
    assert _http_error(missing.value).detail["route_receipt"]["call_count"] == 0

    result = await service.generate(**arguments, idempotency_key="image-one")
    assert result.execution_mode == "managed"
    assert result.provider == "managed"
    assert result.provider_route_receipts[0]["call_count"] == 1


@pytest.mark.asyncio
async def test_managed_image_http_failure_finishes_receipt_without_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[Request] = []
    encoded = base64.b64encode(png_bytes()).decode("ascii")

    def handler(request: Request) -> Response:
        requests.append(request)
        if request.method == "GET":
            return Response(200, json={"data": [{"id": "provider/image"}]})
        if [item.method for item in requests].count("POST") == 1:
            return Response(200, json={"data": [{"b64_json": encoded}]})
        body = json.loads(request.content)
        assert body["size"] == "1024x1024"
        assert body["response_format"] == "b64_json"
        assert "resolution" not in body
        return Response(500, json={"error": {"message": "do-not-persist"}})

    transport = MockTransport(handler)
    service, connection = router_service(
        tmp_path,
        transport,
        kind="newapi",
        scopes=["image"],
    )
    certification = await ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    ).run(
        connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape="image_generation",
            model_id="provider/image",
            adapter_contract="openai_compatible_images_generations_v1",
            acknowledge_billed_call=True,
        ),
        idempotency_key="image-failure-cert",
    )
    assert certification.status == "passed"

    monkeypatch.setenv("MODEL_CONTROL_IMAGE_GENERATION_ENABLED", "true")
    control = ProviderWorkloadControlService(service)
    saved = control.update_policy(
        "image_generation",
        ProviderWorkloadPolicyUpdate(
            expected_revision=0,
            bindings=[
                ProviderWorkloadBindingUpdate(
                    execution_shape="image_generation",
                    model_id="provider/image",
                    connection_id=connection.id,
                    adapter_contract="openai_compatible_images_generations_v1",
                )
            ],
        ),
    )
    control.activate(
        "image_generation",
        ProviderWorkloadActivationRequest(
            expected_revision=saved.revision,
            no_open_p0_p1=True,
            acknowledge_fail_closed=True,
        ),
    )
    gateway = ManagedMultimodalGateway.for_router(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    )
    run = gateway.start_run(
        "image_generation",
        parent_run_reference="image:test-http-failure",
        stable=True,
    )

    with pytest.raises(ManagedMultimodalError) as failure:
        await run.complete_image_generation(
            logical_call_key="image-http-failure",
            model_id="provider/image",
            payload={
                "prompt": "fixed test prompt",
                "resolution": "1024x1024",
            },
            parse_response=lambda response: response.json(),
        )

    assert failure.value.code == "provider_workload_http_5xx"
    assert failure.value.receipt["status"] == "failed"
    assert failure.value.receipt["call_count"] == 1
    assert failure.value.receipt["calls"][0]["dispatched"] is True
    assert [item.method for item in requests].count("POST") == 2
    stored = service.repository.list_workload_receipts("local")
    assert stored["calls"][-1]["status"] == "failed"
    assert "do-not-persist" not in json.dumps(stored)

    unsupported_run = gateway.start_run(
        "image_generation",
        parent_run_reference="image:test-unsupported-parameter",
        stable=True,
    )
    with pytest.raises(ManagedMultimodalError) as unsupported:
        await unsupported_run.complete_image_generation(
            logical_call_key="image-unsupported-parameter",
            model_id="provider/image",
            payload={"prompt": "fixed test prompt", "aspect_ratio": "16:9"},
            parse_response=lambda response: response.json(),
        )
    assert unsupported.value.code == "provider_multimodal_adapter_parameter_unsupported"
    assert unsupported.value.receipt["calls"][0]["dispatched"] is False
    assert [item.method for item in requests].count("POST") == 2
