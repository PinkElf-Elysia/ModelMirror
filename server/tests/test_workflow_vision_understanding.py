from __future__ import annotations

import io
import json
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from server import main as main_module
from server.multimodal.vision_understanding import VisionUnderstandingService
from server.xpert_runtime.workflow_vision import (
    WorkflowVisionError,
    execute_workflow_vision,
    resolve_workflow_vision_asset,
)


def _png_bytes() -> bytes:
    image = Image.new("RGB", (64, 32), color=(245, 245, 245))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _vlm_response() -> dict:
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "ocr_text": "Invoice 2026-08",
                            "visual_summary": "A paid invoice with one line item.",
                            "tables": ["Service: 20"],
                            "charts": [],
                            "language": "en",
                            "warnings": [],
                        }
                    )
                }
            }
        ]
    }


def _workflow() -> dict:
    return {
        "id": "workflow-vision",
        "title": "Workflow vision",
        "nodes": [
            {
                "id": "input",
                "type": "input",
                "data": {"kind": "input", "variableName": "user_input"},
            },
            {
                "id": "vision",
                "type": "vision_understanding",
                "data": {
                    "kind": "vision_understanding",
                    "title": "Vision",
                    "assetIdVariable": "selected_file_asset_id",
                    "visionModelId": "test/vision-model",
                    "pdfPageStrategy": "auto",
                    "maxPages": 10,
                    "maxImageEdge": 1024,
                    "failurePolicy": "continue_on_error",
                    "outputVariable": "vision_result",
                },
            },
            {
                "id": "output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "vision_result"},
            },
        ],
        "edges": [
            {"id": "e1", "source": "input", "target": "vision"},
            {"id": "e2", "source": "vision", "target": "output"},
        ],
    }


def _events(response: httpx.Response) -> list[dict]:
    return [
        json.loads(line[5:].strip())
        for line in response.text.splitlines()
        if line.startswith("data:")
    ]


@pytest.mark.asyncio
async def test_workflow_vision_outputs_typed_result_without_creating_rag_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    class StubFileService:
        def resolve_workflow_visual_asset(self, asset_id: str, *, scope_id: str):
            calls.append((asset_id, scope_id))
            return SimpleNamespace(
                asset_id=asset_id,
                display_name="invoice.png",
                format_id="png",
                media_type="image/png",
                byte_size=len(_png_bytes()),
                content=_png_bytes(),
            )

    async def supports_image_input(_model_id: str) -> bool:
        return True

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(main_module, "get_file_asset_service", lambda: StubFileService())
    monkeypatch.setattr(main_module, "model_supports_image_input", supports_image_input)
    monkeypatch.setattr(
        main_module,
        "workflow_vision_service",
        VisionUnderstandingService(request_override=lambda *_: _vlm_response()),
    )
    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/workflow/run",
            json={
                "workflow": _workflow(),
                "inputs": {
                    "user_input": "read the invoice",
                    "selected_file_asset_id": "file_visual_1",
                },
            },
        )

    assert response.status_code == 200, response.text
    assert calls == [("file_visual_1", "workflow:workflow-vision")]
    events = _events(response)
    completed = next(item for item in events if item.get("event") == "workflow_end")
    result = completed["variables"]["vision_result"]
    assert isinstance(result, dict)
    assert result["asset"]["asset_id"] == "file_visual_1"
    assert result["processed_page_count"] == 1
    assert {item["kind"] for item in result["blocks"]} == {
        "image_ocr",
        "image_description",
        "visual_table",
    }
    assert "content" not in result["asset"]
    assert "path" not in response.text.lower()
    assert "test-key" not in response.text


def test_xpert_vision_asset_requires_explicit_runtime_sharing() -> None:
    class StubContextStore:
        def get_file(self, xpert_id: str, asset_id: str, **_kwargs):
            assert xpert_id == "source-xpert"
            return SimpleNamespace(
                asset_id=asset_id,
                filename="scan.pdf",
                size_bytes=4,
            )

        def read_file_bytes(self, _asset) -> bytes:
            return b"%PDF"

    with pytest.raises(WorkflowVisionError) as denied:
        resolve_workflow_vision_asset(
            asset_id="asset-1",
            workflow_id="ignored",
            runtime_run_type="xpert",
            runtime_metadata={"file_asset_ids": []},
            file_asset_service=SimpleNamespace(),
            xpert_context_store=StubContextStore(),
        )
    assert denied.value.error_code == "workflow_vision_asset_not_shared"

    allowed = resolve_workflow_vision_asset(
        asset_id="asset-1",
        workflow_id="ignored",
        runtime_run_type="xpert",
        runtime_metadata={
            "file_asset_ids": ["asset-1"],
            "file_owner_xpert_id": "source-xpert",
            "file_conversation_id": "conversation-1",
        },
        file_asset_service=SimpleNamespace(),
        xpert_context_store=StubContextStore(),
    )
    assert allowed.asset_id == "asset-1"
    assert allowed.format_id == "pdf"


@pytest.mark.asyncio
async def test_workflow_vision_failure_is_fail_closed_and_content_free() -> None:
    class FailedService:
        async def analyze_bytes(self, *_args, **_kwargs):
            from server.multimodal.vision_understanding import VisionProcessingError

            raise VisionProcessingError(
                r"provider failed C:\private\source.png api_key=secret-value"
            )

    with pytest.raises(WorkflowVisionError) as failure:
        await execute_workflow_vision(
            asset=SimpleNamespace(
                asset_id="asset-1",
                filename="scan.png",
                format_id="png",
                byte_size=3,
                content=b"png",
            ),
            model_id="test/model",
            pdf_page_strategy="auto",
            max_pages=10,
            max_image_edge=1024,
            failure_policy="strict",
            service=FailedService(),
        )
    assert failure.value.error_code == "workflow_vision_processing_failed"
    assert "private" not in failure.value.message.lower()
    assert "secret-value" not in failure.value.message
