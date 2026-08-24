from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.responses import StreamingResponse

import server.main as main_module
from server.model_router.workflow_gateway import ManagedWorkflowRoutingError
from server.xpert_runtime.execution_store import WorkflowExecutionStore


MODEL_ID = "provider/workflow-model"


class FakeManagedNodeRun:
    def __init__(self, entry_id: str) -> None:
        self.entry_id = entry_id
        self.run_id = "workrun_fake"
        self.status = "running"
        self.calls: list[Any] = []
        self.invocations: list[dict[str, Any]] = []
        self.json_outputs: list[str] = []

    async def stream_text(self, **kwargs: Any):
        self.invocations.append({"shape": "chat_text", **kwargs})
        yield "managed "
        yield "answer"

    async def complete_text_unary(self, **kwargs: Any) -> str:
        self.invocations.append({"shape": "chat_text_unary", **kwargs})
        return '{"categoryId":"category_2"}'

    async def complete_json_object(self, **kwargs: Any) -> str:
        self.invocations.append({"shape": "chat_json_object", **kwargs})
        if self.json_outputs:
            return self.json_outputs.pop(0)
        return '{"order_id":"A-1"}'

    def finish(self, status: str, *, reason_code: str | None = None) -> None:
        self.status = status
        self.reason_code = reason_code

    def receipt_summary(self) -> dict[str, Any]:
        calls = [
            {
                "call_sequence": int(item["call_sequence"]),
                "model_id": str(item["model_id"]),
                "actual_model": str(item["model_id"]),
                "dispatched": True,
                "status": "passed" if self.status == "passed" else "failed",
                "error_code": getattr(self, "reason_code", None),
                "prompt_tokens": 3,
                "completion_tokens": 2,
                "total_tokens": 5,
            }
            for item in self.invocations
        ]
        return {
            "contract_version": "modelmirror-provider-workload-routing-v1",
            "entry_id": self.entry_id,
            "routing_mode": "managed_required",
            "run_reference": self.run_id,
            "status": self.status,
            "call_count": len(calls),
            "reason_codes": (
                [self.reason_code] if getattr(self, "reason_code", None) else []
            ),
            "calls": calls,
        }


class FakeManagedWorkflowGateway:
    instances: list[FakeManagedWorkflowGateway] = []

    def __init__(self) -> None:
        self.started: list[dict[str, str]] = []
        self.runs: list[FakeManagedNodeRun] = []
        self.__class__.instances.append(self)

    @classmethod
    def for_router(cls, _service: Any) -> FakeManagedWorkflowGateway:
        return cls()

    @staticmethod
    def entry_id(source_kind: str | None) -> str | None:
        return {
            "workflow_classic": "workflow_interactive_llm",
            "workflow_deployment": "workflow_deployment_llm",
        }.get(str(source_kind or ""))

    @staticmethod
    def blocked_receipt(entry_id: str, reason_code: str) -> dict[str, Any]:
        return {
            "contract_version": "modelmirror-provider-workload-routing-v1",
            "entry_id": entry_id,
            "routing_mode": "managed_required",
            "run_reference": "blocked_before_dispatch",
            "status": "failed",
            "call_count": 0,
            "reason_codes": [reason_code],
            "calls": [],
        }

    def routing_mode(self, source_kind: str | None) -> str:
        return "managed_required" if self.entry_id(source_kind) else "legacy"

    def start_node_run(self, **kwargs: str) -> FakeManagedNodeRun:
        self.started.append(dict(kwargs))
        entry_id = self.entry_id(kwargs["source_kind"])
        assert entry_id is not None
        run = FakeManagedNodeRun(entry_id)
        self.runs.append(run)
        return run


def _workflow(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> Any:
    return main_module.WorkflowRunRequest.model_validate(
        {
            "workflow": {
                "id": "managed-workflow-test",
                "title": "Managed Workflow Test",
                "nodes": nodes,
                "edges": edges,
            },
            "inputs": {"user_input": "private workflow input"},
        }
    )


async def _events(response: Any) -> list[dict[str, Any]]:
    assert isinstance(response, StreamingResponse)
    payloads: list[dict[str, Any]] = []
    buffer = ""
    async for chunk in response.body_iterator:
        buffer += chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk)
        while "\n\n" in buffer:
            frame, buffer = buffer.split("\n\n", 1)
            for line in frame.splitlines():
                if line.startswith("data:"):
                    payloads.append(json.loads(line[5:].strip()))
    return payloads


@pytest.fixture(autouse=True)
def _isolated_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeManagedWorkflowGateway.instances = []
    monkeypatch.setattr(
        main_module, "ManagedWorkflowGateway", FakeManagedWorkflowGateway
    )
    monkeypatch.setattr(
        main_module,
        "workflow_execution_store",
        WorkflowExecutionStore(tmp_path / "executions"),
    )
    monkeypatch.setattr(main_module, "workflow_task_store", {})
    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("", ""))


@pytest.mark.asyncio
async def test_managed_llm_runs_without_legacy_gateway_and_adds_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def legacy_must_not_run(*_args: Any, **_kwargs: Any):
        raise AssertionError("legacy workflow stream must not run")

    monkeypatch.setattr(main_module, "stream_workflow_llm_text", legacy_must_not_run)
    response = await main_module._run_workflow_response(
        _workflow(
            [
                {"id": "input", "type": "input", "data": {"kind": "input"}},
                {
                    "id": "llm",
                    "type": "llm",
                    "data": {
                        "kind": "llm",
                        "modelId": MODEL_ID,
                        "prompt": "{{user_input}}",
                        "outputVariable": "answer",
                    },
                },
                {
                    "id": "output",
                    "type": "output",
                    "data": {"kind": "output", "outputVariable": "answer"},
                },
            ],
            [
                {"id": "e1", "source": "input", "target": "llm"},
                {"id": "e2", "source": "llm", "target": "output"},
            ],
        ),
        None,
        runtime_execution_source_kind="workflow_classic",
        runtime_task_id="interactive-task",
    )
    events = await _events(response)

    llm_end = next(
        item
        for item in events
        if item.get("event") == "node_end" and item.get("node_id") == "llm"
    )
    receipt = llm_end["provider_route_receipts"]
    assert receipt["entry_id"] == "workflow_interactive_llm"
    assert receipt["call_count"] == 1
    assert receipt["calls"][0]["model_id"] == MODEL_ID
    assert [
        item["output"]
        for item in events
        if item.get("event") == "node_delta" and item.get("node_id") == "llm"
    ] == ["managed ", "answer"]
    assert FakeManagedWorkflowGateway.instances[0].started == [
        {
            "source_kind": "workflow_classic",
            "execution_reference": "interactive-task",
            "node_id": "llm",
        }
    ]


@pytest.mark.asyncio
async def test_excluded_runtime_source_keeps_legacy_llm_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    async def legacy_stream(model_id: str, prompt: str, **_kwargs: Any):
        calls.append((model_id, prompt))
        yield "legacy answer"

    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("https://legacy.example/v1/chat/completions", "legacy-key"),
    )
    monkeypatch.setattr(main_module, "stream_workflow_llm_text", legacy_stream)
    response = await main_module._run_workflow_response(
        _workflow(
            [
                {"id": "input", "type": "input", "data": {"kind": "input"}},
                {
                    "id": "llm",
                    "type": "llm",
                    "data": {
                        "kind": "llm",
                        "modelId": MODEL_ID,
                        "prompt": "{{user_input}}",
                        "outputVariable": "answer",
                    },
                },
            ],
            [{"id": "e1", "source": "input", "target": "llm"}],
        ),
        None,
        runtime_run_type="xpert",
        runtime_execution_source_kind="xpert_chat",
        runtime_task_id="excluded-xpert-task",
    )
    events = await _events(response)

    assert calls == [(MODEL_ID, "private workflow input")]
    assert FakeManagedWorkflowGateway.instances[0].started == []
    llm_end = next(
        item for item in events
        if item.get("event") == "node_end" and item.get("node_id") == "llm"
    )
    assert "provider_route_receipts" not in llm_end


@pytest.mark.asyncio
async def test_classifier_rule_hit_creates_no_managed_run() -> None:
    response = await main_module._run_workflow_response(
        _workflow(
            [
                {"id": "input", "type": "input", "data": {"kind": "input"}},
                {
                    "id": "classifier",
                    "type": "question_classifier",
                    "data": {
                        "kind": "question_classifier",
                        "contractVersion": 2,
                        "inputVariable": "user_input",
                        "outputVariable": "category",
                        "classificationMode": "rules_then_model",
                        "caseSensitive": False,
                        "modelId": MODEL_ID,
                        "defaultLabel": "其他",
                        "categoriesV2": [
                            {
                                "id": "category_1",
                                "label": "Private",
                                "description": "Rule hit",
                                "keywords": ["private"],
                                "matchMode": "contains_any",
                            }
                        ],
                    },
                },
            ],
            [{"id": "e1", "source": "input", "target": "classifier"}],
        ),
        None,
        runtime_execution_source_kind="workflow_classic",
        runtime_task_id="classifier-rule-task",
    )
    events = await _events(response)

    assert FakeManagedWorkflowGateway.instances[0].started == []
    classifier_end = next(
        item for item in events if item.get("event") == "node_end"
    )
    assert "provider_route_receipts" not in classifier_end


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("node_kind", "node_data", "expected_shape"),
    [
        (
            "parameter_extractor",
            {
                "contractVersion": 2,
                "inputVariable": "user_input",
                "outputVariable": "parameters",
                "schemaMode": "fields",
                "outputShape": "object",
                "fields": [
                    {
                        "id": "field_1",
                        "name": "order_id",
                        "description": "Order identifier",
                        "valueType": "string",
                        "required": True,
                        "nullable": False,
                    }
                ],
                "repairAttempts": 0,
            },
            "chat_json_object",
        ),
        (
            "question_classifier",
            {
                "contractVersion": 2,
                "inputVariable": "user_input",
                "outputVariable": "category",
                "classificationMode": "model_only",
                "caseSensitive": False,
                "defaultLabel": "其他",
                "categoriesV2": [
                    {
                        "id": "category_1",
                        "label": "A",
                        "description": "A",
                        "keywords": [],
                        "matchMode": "contains_any",
                    },
                    {
                        "id": "category_2",
                        "label": "B",
                        "description": "B",
                        "keywords": [],
                        "matchMode": "contains_any",
                    },
                ],
            },
            "chat_text_unary",
        ),
    ],
)
async def test_managed_v2_missing_model_uses_exact_text_fallback_binding(
    node_kind: str,
    node_data: dict[str, Any],
    expected_shape: str,
) -> None:
    response = await main_module._run_workflow_response(
        _workflow(
            [
                {"id": "input", "type": "input", "data": {"kind": "input"}},
                {
                    "id": "model-node",
                    "type": node_kind,
                    "data": {"kind": node_kind, **node_data},
                },
            ],
            [{"id": "e1", "source": "input", "target": "model-node"}],
        ),
        None,
        runtime_execution_source_kind="workflow_classic",
        runtime_task_id=f"fallback-{node_kind}",
    )
    events = await _events(response)

    assert not [item for item in events if item.get("event") == "error"]
    managed_run = FakeManagedWorkflowGateway.instances[0].runs[0]
    assert managed_run.invocations[0]["shape"] == expected_shape
    assert managed_run.invocations[0]["model_id"] == main_module.TEXT_FALLBACK_MODEL


@pytest.mark.asyncio
async def test_parameter_extractor_repair_is_two_planned_calls() -> None:
    original_start = FakeManagedWorkflowGateway.start_node_run

    def start_with_repair(
        self: FakeManagedWorkflowGateway, **kwargs: str
    ) -> FakeManagedNodeRun:
        run = original_start(self, **kwargs)
        run.json_outputs = ['{"order_id":3}', '{"order_id":"A-2"}']
        return run

    FakeManagedWorkflowGateway.start_node_run = start_with_repair
    try:
        response = await main_module._run_workflow_response(
            _workflow(
                [
                    {"id": "input", "type": "input", "data": {"kind": "input"}},
                    {
                        "id": "extractor",
                        "type": "parameter_extractor",
                        "data": {
                            "kind": "parameter_extractor",
                            "contractVersion": 2,
                            "inputVariable": "user_input",
                            "modelId": MODEL_ID,
                            "outputVariable": "parameters",
                            "schemaMode": "fields",
                            "outputShape": "object",
                            "fields": [
                                {
                                    "id": "field_1",
                                    "name": "order_id",
                                    "description": "Order identifier",
                                    "valueType": "string",
                                    "required": True,
                                    "nullable": False,
                                }
                            ],
                            "jsonSchema": {},
                            "repairAttempts": 1,
                        },
                    },
                ],
                [{"id": "e1", "source": "input", "target": "extractor"}],
            ),
            None,
            runtime_execution_source_kind="workflow_classic",
            runtime_task_id="extractor-repair-task",
        )
        events = await _events(response)
    finally:
        FakeManagedWorkflowGateway.start_node_run = original_start

    extractor_end = next(
        item
        for item in events
        if item.get("event") == "node_end" and item.get("node_id") == "extractor"
    )
    receipt = extractor_end["provider_route_receipts"]
    assert receipt["call_count"] == 2
    assert [item["call_sequence"] for item in receipt["calls"]] == [1, 2]
    assert [
        item["logical_call_key"]
        for item in FakeManagedWorkflowGateway.instances[0].runs[0].invocations
    ] == ["parameter_extractor:initial", "parameter_extractor:repair:1"]


@pytest.mark.asyncio
async def test_managed_v1_provider_failure_does_not_return_legacy_empty_object(
    caplog: pytest.LogCaptureFixture,
) -> None:
    original_complete = FakeManagedNodeRun.complete_json_object

    async def fail_closed(self: FakeManagedNodeRun, **kwargs: Any) -> str:
        self.invocations.append({"shape": "chat_json_object", **kwargs})
        error = ManagedWorkflowRoutingError(
            "provider_workload_http_503",
            "Workflow Managed Provider failed closed.",
        )
        error.receipt = self.receipt_summary()
        try:
            raise RuntimeError(
                "private prompt https://private-provider.example secret-key"
            )
        except RuntimeError as cause:
            raise error from cause

    FakeManagedNodeRun.complete_json_object = fail_closed
    try:
        response = await main_module._run_workflow_response(
            _workflow(
                [
                    {"id": "input", "type": "input", "data": {"kind": "input"}},
                    {
                        "id": "extractor",
                        "type": "parameter_extractor",
                        "data": {
                            "kind": "parameter_extractor",
                            "contractVersion": 1,
                            "inputVariable": "user_input",
                            "modelId": MODEL_ID,
                            "outputVariable": "parameters",
                            "schema": "order_id:string",
                        },
                    },
                ],
                [{"id": "e1", "source": "input", "target": "extractor"}],
            ),
            None,
            runtime_execution_source_kind="workflow_classic",
            runtime_task_id="v1-fail-closed-task",
        )
        events = await _events(response)
    finally:
        FakeManagedNodeRun.complete_json_object = original_complete

    error = next(item for item in events if item.get("event") == "error")
    assert error["code"] == "provider_workload_http_503"
    assert error["provider_route_receipts"]["entry_id"] == (
        "workflow_interactive_llm"
    )
    assert not any(
        item.get("event") == "node_end" and item.get("node_id") == "extractor"
        for item in events
    )
    assert not any(item.get("output") == "{}" for item in events)
    assert "private prompt" not in caplog.text
    assert "private-provider.example" not in caplog.text
    assert "secret-key" not in caplog.text


@pytest.mark.asyncio
async def test_deployment_binding_uses_stable_execution_id() -> None:
    response = await main_module._run_workflow_response(
        _workflow(
            [
                {"id": "input", "type": "input", "data": {"kind": "input"}},
                {
                    "id": "llm",
                    "type": "llm",
                    "data": {
                        "kind": "llm",
                        "modelId": MODEL_ID,
                        "prompt": "{{user_input}}",
                    },
                },
            ],
            [{"id": "e1", "source": "input", "target": "llm"}],
        ),
        None,
        runtime_execution_source_kind="workflow_deployment",
        runtime_metadata={
            "workflow_deployment_execution_id": "deployment-execution-1"
        },
        runtime_task_id="deployment-task-id",
    )
    await _events(response)

    assert FakeManagedWorkflowGateway.instances[0].started == [
        {
            "source_kind": "workflow_deployment",
            "execution_reference": "deployment-execution-1",
            "node_id": "llm",
        }
    ]


def test_durable_event_sanitizes_provider_receipt(tmp_path: Path) -> None:
    store = WorkflowExecutionStore(tmp_path / "durable")
    store.create(
        task_id="task",
        run_id="run",
        run_type="workflow",
        workflow={"id": "wf", "title": "WF"},
        inputs={},
        source_kind="workflow_classic",
    )
    store.append_event(
        "task",
        {
            "event": "node_end",
            "node_id": "llm",
            "provider_route_receipts": {
                "entry_id": "workflow_interactive_llm",
                "status": "passed",
                "run_reference": "workrun-safe",
                "connection_id": "must-not-survive",
                "prompt": "private prompt",
                "reason_codes": [],
                "calls": [
                    {
                        "call_sequence": 1,
                        "model_id": MODEL_ID,
                        "actual_model": MODEL_ID,
                        "dispatched": True,
                        "status": "passed",
                        "total_tokens": 5,
                        "connection_id": "must-not-survive",
                        "output": "private output",
                    }
                ],
            },
        },
    )

    receipt = store.require("task").events[0]["provider_route_receipts"]
    assert receipt["call_count"] == 1
    assert receipt["calls"][0]["model_id"] == MODEL_ID
    serialized = json.dumps(receipt)
    assert "connection_id" not in serialized
    assert "private prompt" not in serialized
    assert "private output" not in serialized


def test_durable_event_bounds_malformed_provider_receipt_numbers(
    tmp_path: Path,
) -> None:
    store = WorkflowExecutionStore(tmp_path / "durable-malformed")
    store.create(
        task_id="task-malformed",
        run_id="run-malformed",
        run_type="workflow",
        workflow={"id": "wf", "title": "WF"},
        inputs={},
        source_kind="workflow_classic",
    )
    store.append_event(
        "task-malformed",
        {
            "event": "node_end",
            "node_id": "llm",
            "provider_route_receipts": {
                "entry_id": "workflow_interactive_llm",
                "status": "passed",
                "calls": [
                    {
                        "call_sequence": "not-a-number",
                        "model_id": MODEL_ID,
                        "dispatched": True,
                        "status": "passed",
                    }
                ],
            },
        },
    )

    receipt = store.require("task-malformed").events[0]["provider_route_receipts"]
    assert receipt["calls"][0]["call_sequence"] == 1
