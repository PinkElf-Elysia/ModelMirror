from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from server.multimodal.vision_understanding import (
    VisionPagePlan,
    VisionProcessingError,
    VisionUnderstandingService,
)
from server.multimodal.vision_v2 import VisionExecutionRejected, validate_vision_analysis_v2
from server.xpert_runtime.execution_budget import XpertExecutionBudget, use_execution_budget
from server.xpert_runtime.workflow_vision import (
    WorkflowVisionAsset,
    WorkflowVisionError,
    execute_workflow_vision,
)


ANALYSIS = {
    "ocr_text": "INV-2048",
    "visual_summary": "一张合成发票。",
    "tables": ["金额 120 元"],
    "charts": [],
    "language": "zh",
    "warnings": [],
}


class ManagedStub:
    def __init__(self, analysis=None):
        self.analysis = ANALYSIS if analysis is None else analysis
        self.calls = []
        self.active = 0
        self.peak = 0
        self.mode = "managed_required"
        self.finished = None

    def routing_mode(self, entry):
        return self.mode

    def exact_model_id(self, entry, shape, *, requested_model):
        assert shape == "vision_json_unary"
        return requested_model

    def vision_binding_snapshot(self, entry, model):
        return {"entry_id": entry, "model_id": model, "checksum": "fixture"}

    def start_run(self, entry, **kwargs):
        return self

    async def complete_vision_json(self, **kwargs):
        self.calls.append(kwargs)
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(0.01)
            return deepcopy(self.analysis)
        finally:
            self.active -= 1

    def finish_success(self):
        return self.finish_failure("passed")

    def finish_failure(self, code):
        self.finished = code
        return {
            "status": "passed" if code == "passed" else "failed",
            "call_count": len(self.calls),
            "calls": [
                {"dispatched": True, "status": "passed", "total_tokens": None}
                for _ in self.calls
            ],
        }


def service_for(monkeypatch, stub, pages=1):
    service = VisionUnderstandingService(managed_gateway=stub, max_concurrency=8)
    monkeypatch.setattr(service, "_plan_source", lambda *args: [
        VisionPagePlan(page_number=i, selected=True, reason="all_pages")
        for i in range(1, pages + 1)
    ])
    monkeypatch.setattr(service, "_render_page", lambda *args: {
        "content": b"synthetic", "mime_type": "image/png",
    })
    monkeypatch.setattr(service, "_targets", lambda: pytest.fail("V2 entered legacy targets"))
    return service


async def execute(service, **kwargs):
    params = dict(
        asset=WorkflowVisionAsset("asset1", "invoice.png", "png", 3, b"png"),
        model_id="fixture/vision", pdf_page_strategy="all", max_pages=10,
        max_image_edge=2048, failure_policy="continue_on_error", service=service,
        managed_entry_id="xpert_vision", contract_version=2,
        binding_snapshot={"entry_id": "xpert_vision", "model_id": "fixture/vision", "checksum": "fixture"},
    )
    params.update(kwargs)
    return await execute_workflow_vision(**params)


@pytest.mark.parametrize("field,value", [
    ("ocr_text", 12), ("visual_summary", {}), ("tables", [1]),
    ("charts", [{"text": "bad"}]), ("language", None), ("warnings", "bad"),
    ("secret", "forbidden"),
])
def test_managed_output_is_strict(field, value):
    with pytest.raises(ValueError):
        validate_vision_analysis_v2({**ANALYSIS, field: value})


@pytest.mark.asyncio
async def test_managed_calls_share_execution_budget_and_unknown_usage(monkeypatch):
    stub = ManagedStub()
    service = service_for(monkeypatch, stub, pages=4)
    budget = XpertExecutionBudget(max_concurrency=1, recursion_limit=100, max_model_calls=4)
    with use_execution_budget(budget):
        payload, _ = await execute(service)
    assert len(stub.calls) == budget.model_calls == 4
    assert stub.peak == 1
    assert payload["execution_summary"]["unverified_token_calls"] == 4
    assert payload["execution_summary"]["known_total_tokens"] == 0
    assert payload["execution_summary"]["token_usage_verified"] is False
    assert len(payload["asset"]["sha256"]) == 64
    assert "简体中文" in stub.calls[0]["messages"][0]["content"]


@pytest.mark.asyncio
async def test_budget_failure_cancels_pending_pages_without_retry(monkeypatch):
    stub = ManagedStub()
    service = service_for(monkeypatch, stub, pages=4)
    budget = XpertExecutionBudget(max_concurrency=1, recursion_limit=100, max_model_calls=1)
    with use_execution_budget(budget), pytest.raises(WorkflowVisionError) as failure:
        await execute(service)
    assert failure.value.error_code == "workflow_vision_model_budget_exhausted"
    count = len(stub.calls)
    await asyncio.sleep(0.02)
    assert len(stub.calls) == count <= 1
    assert stub.active == 0
    assert stub.finished == "workflow_vision_execution_interrupted"


@pytest.mark.asyncio
@pytest.mark.parametrize("pages,limit", [(11, 10), (21, 20)])
async def test_total_pages_fail_before_any_dispatch(monkeypatch, pages, limit):
    stub = ManagedStub()
    service = service_for(monkeypatch, stub, pages=pages)
    with pytest.raises(WorkflowVisionError):
        await execute(service, max_pages=limit)
    assert stub.calls == []


@pytest.mark.asyncio
async def test_invalid_managed_payload_does_not_retry_or_pass(monkeypatch):
    stub = ManagedStub({"ocr_text": 100})
    service = service_for(monkeypatch, stub)
    with pytest.raises(WorkflowVisionError, match="所有选中页面"):
        await execute(service)
    assert len(stub.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "degraded_required"])
async def test_managed_only_no_fallback(monkeypatch, mode):
    stub = ManagedStub()
    stub.mode = mode
    service = service_for(monkeypatch, stub)
    with pytest.raises(WorkflowVisionError):
        await execute(service)
    assert stub.calls == []


@pytest.mark.asyncio
async def test_service_rejects_v2_without_budget_context():
    service = VisionUnderstandingService()
    with pytest.raises(VisionProcessingError):
        await service.analyze_bytes(
            b"png", filename="invoice.png", source_id="asset1",
            config={"contract_version": 2, "vision_model_id": "fixture/vision"},
        )
