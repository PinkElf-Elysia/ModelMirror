from __future__ import annotations

import io
import json

import pytest
from PIL import Image

from server import main as main_module
from server.evaluations import api as evaluation_api
from server.evaluations.executor import XpertEvaluationExecutor
from server.evaluations.store import XpertEvaluationStore
from server.evaluations.vision_fixtures import EvaluationVisionFixtureService
from server.evaluations.vision_preflight import inspect_vision_node
from server.evaluations.vision_runtime import record_evaluation_vision_receipt
from server.meta_agent.meta_planner_v2 import compile_xpert_candidate
from server.multimodal.vision_understanding import VisionUnderstandingService
from server.multimodal.vision_v2 import VisionExecutionRejected
from server.tests.test_evaluation_vision_fixtures import _file_assets, _upload
from server.tests.test_meta_planner_vision_graph import (
    _plan, binding_fixture, vision_intent, vision_request, vision_snapshot,
)
from server.tests.test_workflow_vision_v2 import ManagedStub
from server.workflow_native.schemas import NativeWorkflowDefinition


class ManagedVisionFixture(ManagedStub):
    def vision_binding_snapshot(self, entry, model):
        assert entry == "xpert_vision" and model == "model/vision"
        return binding_fixture()

    def receipt_summary(self):
        return {
            "entry_id": "xpert_vision", "status": "running", "call_count": len(self.calls),
            "calls": [
                {"model_id": "model/vision", "actual_model": "model/vision", "dispatched": True, "status": "passed", "total_tokens": 17, "call_sequence": index}
                for index, _ in enumerate(self.calls, 1)
            ],
        }

    def finish_failure(self, code):
        self.finished = code
        return {**self.receipt_summary(), "status": "passed" if code == "passed" else "failed"}


def _setup(tmp_path, monkeypatch, *, pdf=False):
    assets = _file_assets(tmp_path)
    store = XpertEvaluationStore(tmp_path / "evaluations", vision_fixtures=EvaluationVisionFixtureService(assets))
    dataset = store.create_dataset("合成附件全链路")
    if pdf:
        content = io.BytesIO()
        Image.new("RGB", (100, 100), "white").save(
            content, format="PDF", save_all=True,
            append_images=[Image.new("RGB", (100, 100), "blue")],
        )
        uploaded = _upload(assets, dataset["dataset_id"], content=content.getvalue(), filename="synthetic.pdf", media_type="application/pdf")
    else:
        uploaded = _upload(assets, dataset["dataset_id"])
    dataset = store.put_cases(dataset["dataset_id"], revision=dataset["revision"], cases=[{
        "case_id": "image", "message": "读取合成附件并汇总。", "attachment": {"asset_id": uploaded.asset_id},
        "expected": {"contains": ["已完成"]},
        "vision": [{"node_ref": "retrieve", "model_id": "model/vision", "page_count": 2 if pdf else 1, "required_blocks": ["ocr"], "content_anchors": [{"kind": "ocr", "text": "INV-2048"}]}],
    }])
    version = store.publish_dataset(dataset["dataset_id"], revision=dataset["revision"])
    candidate = compile_xpert_candidate(request=vision_request(), plan=_plan(), blueprint=vision_intent(), snapshot=vision_snapshot(), target=None)
    draft = candidate["draft"]
    workflow = NativeWorkflowDefinition.model_validate(draft["workflow"])
    node = next(node for node in workflow.nodes if node.type == "vision_understanding")
    target = {
        "target_id": "candidate", "label": "合成视觉候选", "source": {}, "xpert": {"id": "synthetic"},
        "workflow": draft["workflow"], "input_variable": draft["input_variable"],
        "history_variable": draft["history_variable"], "output_variable": draft["output_variable"],
        "agent_config": {}, "checksum": "synthetic-target",
        "resources": {"vision_models": [inspect_vision_node(node, nested=False, binding_resolver=lambda *_: binding_fixture())]},
    }
    run = store.create_run(dataset_version=version, cases=version["cases"], baseline=None, candidates=[target], config={"budget": {"repetitions": 1, "max_model_calls": 4}}, warnings=[])
    stub = ManagedVisionFixture()
    service = VisionUnderstandingService(managed_gateway=stub)
    monkeypatch.setattr(main_module, "get_file_asset_service", lambda: assets)
    monkeypatch.setattr(main_module, "get_xpert_evaluation_store", lambda: store)
    monkeypatch.setattr(evaluation_api, "_store", store)
    monkeypatch.setattr(main_module, "workflow_vision_service", service)
    monkeypatch.setattr(main_module, "WORKFLOW_AGENT_STRATEGY_V2_ENABLED", False)
    monkeypatch.setattr(main_module.ManagedWorkflowGateway, "agent_routing_mode", lambda *_: "legacy")
    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("https://mock.invalid", "synthetic-key"))
    agent_inputs = []
    async def agent_stream(model_id, prompt, **kwargs):
        assert model_id == "model/agent"
        agent_inputs.append(prompt)
        yield "已完成"
    monkeypatch.setattr(main_module, "stream_workflow_llm_text", agent_stream)
    return store, run, stub, agent_inputs


@pytest.mark.asyncio
async def test_real_runner_mock_provider_captures_fixed_vision_then_agent(tmp_path, monkeypatch):
    store, run, stub, agent_inputs = _setup(tmp_path, monkeypatch)
    executor = XpertEvaluationExecutor(store, target_runner=main_module.run_xpert_evaluation_target)
    await executor._execute_run(store.claim_next_run())
    result = store.require_run(run["run_id"])
    item = result["items"][0]
    assert item["status"] == "completed", item
    assert item["vision_evidence"] == "verified", item
    assert len(stub.calls) == 1 and len(agent_inputs) == 1
    assert "INV-2048" in agent_inputs[0]
    assert item["usage"]["vision_actual_tokens"] == 17
    assert item["usage"]["vision_token_usage_verified"] is True
    assert item["vision_reads"][0]["asset_sha256"] == result["dataset"]["cases"][0]["attachment"]["sha256"]
    safe = json.dumps({"reads": item["vision_reads"], "receipts": item["vision_receipts"]}, ensure_ascii=False)
    assert "INV-2048" not in safe
    assert "base64" not in safe
    before = len(stub.calls)
    store.recover_runs()
    assert store.claim_items(run["run_id"], 1) == []
    assert len(stub.calls) == before


@pytest.mark.asyncio
async def test_correct_answer_without_vision_execution_is_not_verified(tmp_path, monkeypatch):
    store, run, stub, _ = _setup(tmp_path, monkeypatch)
    async def skipped(*args):
        return {"output": "已完成", "vision_reads": [], "usage": {}}
    executor = XpertEvaluationExecutor(store, target_runner=skipped)
    await executor._execute_run(store.claim_next_run())
    item = store.require_run(run["run_id"])["items"][0]
    assert item["vision_evidence"] == "failed"
    metric = next(metric for metric in item["metrics"] if metric["kind"] == "workflow_vision_match")
    assert metric["score"] == 0
    assert stub.calls == []


def test_known_vision_usage_exhausts_budget_after_receipt_is_persisted(tmp_path, monkeypatch):
    store, run, _, _ = _setup(tmp_path, monkeypatch)
    store.claim_next_run()
    item = store.claim_items(run["run_id"], 1)[0]
    metadata = {"evaluation_run_id": run["run_id"], "evaluation_item_id": item["item_id"]}
    with pytest.raises(VisionExecutionRejected) as failure:
        record_evaluation_vision_receipt(store, metadata, node_ref="retrieve", receipt={"status": "passed", "calls": [{"dispatched": True, "status": "passed", "total_tokens": 64_001}]})
    assert failure.value.code == "workflow_vision_token_budget_exhausted"
    saved = store.require_run(run["run_id"])["items"][0]["vision_receipts"]["retrieve"]
    assert saved["usage"]["known_total_tokens"] == 64_001


@pytest.mark.asyncio
async def test_pdf_pages_are_rendered_and_counted_once_each(tmp_path, monkeypatch):
    store, run, stub, agent_inputs = _setup(tmp_path, monkeypatch, pdf=True)
    executor = XpertEvaluationExecutor(store, target_runner=main_module.run_xpert_evaluation_target)
    await executor._execute_run(store.claim_next_run())
    item = store.require_run(run["run_id"])["items"][0]
    assert item["status"] == "completed", item
    assert item["vision_evidence"] == "verified"
    assert len(stub.calls) == 2 and len(agent_inputs) == 1
    assert item["vision_reads"][0]["processed_page_count"] == 2
    assert item["usage"]["vision_actual_tokens"] == 34


@pytest.mark.asyncio
async def test_attachment_tamper_after_run_creation_never_dispatches(tmp_path, monkeypatch):
    store, run, stub, agent_inputs = _setup(tmp_path, monkeypatch)
    assets = main_module.get_file_asset_service()
    attachment = run["dataset"]["cases"][0]["attachment"]
    record = assets.repository.get_asset("tenant-a", attachment["asset_id"])
    blob_path = assets.blob_store.storage_dir.joinpath(*record.storage_key.split("/"))
    blob_path.write_bytes(b"x" * record.byte_size)
    executor = XpertEvaluationExecutor(store, target_runner=main_module.run_xpert_evaluation_target)
    await executor._execute_run(store.claim_next_run())
    item = store.require_run(run["run_id"])["items"][0]
    assert item["status"] == "failed", item
    assert item["score"] == 0
    assert stub.calls == [] and agent_inputs == []


@pytest.mark.asyncio
async def test_raw_visual_content_in_evidence_never_enters_report(tmp_path, monkeypatch):
    from server.tests.test_evaluation_vision_evidence import _safe_evidence

    store, run, stub, _ = _setup(tmp_path, monkeypatch)
    async def malicious_runner(*args):
        return {"output": "已完成", "vision_reads": [{**_safe_evidence(), "ocr": "DO-NOT-PERSIST-RAW-OCR"}], "usage": {}}
    executor = XpertEvaluationExecutor(store, target_runner=malicious_runner)
    await executor._execute_run(store.claim_next_run())
    result = store.require_run(run["run_id"])
    assert result["items"][0]["status"] == "failed"
    assert "DO-NOT-PERSIST-RAW-OCR" not in json.dumps(result)
    assert stub.calls == []
