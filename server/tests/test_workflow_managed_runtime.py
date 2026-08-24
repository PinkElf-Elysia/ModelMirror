from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.responses import StreamingResponse

import server.main as main_module
from server.model_router.workflow_gateway import (
    ManagedWorkflowAgentRun,
    ManagedWorkflowRoutingError,
)
from server.xpert_runtime.agent_strategy.models import (
    AgentModelTurn,
    AgentToolCall,
    AgentUsage,
)
from server.xpert_runtime.execution_store import WorkflowExecutionStore
from server.xpert_runtime.approval_store import RuntimeApprovalStore
from server.xpert_runtime.toolset import RuntimeTool, RuntimeToolResult


MODEL_ID = "provider/workflow-model"


class FakeManagedNodeRun(ManagedWorkflowAgentRun):
    @property
    def entry_id(self) -> str:
        return self._fake_entry_id

    @entry_id.setter
    def entry_id(self, value: str) -> None:
        self._fake_entry_id = value

    @property
    def run_id(self) -> str:
        return self._fake_run_id

    @run_id.setter
    def run_id(self, value: str) -> None:
        self._fake_run_id = value

    @property
    def status(self) -> str:
        return self._fake_status

    @status.setter
    def status(self, value: str) -> None:
        self._fake_status = value

    @property
    def calls(self) -> list[Any]:
        return self._fake_calls

    @calls.setter
    def calls(self, value: list[Any]) -> None:
        self._fake_calls = value

    def __init__(self, entry_id: str) -> None:
        self.entry_id = entry_id
        self.run_id = "workrun_fake"
        self.status = "running"
        self.calls: list[Any] = []
        self.invocations: list[dict[str, Any]] = []
        self.json_outputs: list[str] = []
        self.agent_turns: list[AgentModelTurn] = []

    async def stream_text(self, **kwargs: Any):
        kwargs.setdefault("call_sequence", len(self.invocations) + 1)
        self.invocations.append({"shape": "chat_text", **kwargs})
        self.calls.append(
            SimpleNamespace(
                status="passed",
                actual_model=kwargs["model_id"],
                prompt_tokens=3,
                completion_tokens=2,
                total_tokens=5,
            )
        )
        yield "managed "
        yield "answer"

    async def complete_text_unary(self, **kwargs: Any) -> str:
        self.invocations.append({"shape": "chat_text_unary", **kwargs})
        return '{"categoryId":"category_2"}'

    async def complete_json_object(self, **kwargs: Any) -> str:
        kwargs.setdefault("call_sequence", len(self.invocations) + 1)
        self.invocations.append({"shape": "chat_json_object", **kwargs})
        self.calls.append(
            SimpleNamespace(
                status="passed",
                actual_model=kwargs["model_id"],
                prompt_tokens=3,
                completion_tokens=2,
                total_tokens=5,
            )
        )
        if self.json_outputs:
            return self.json_outputs.pop(0)
        return '{"order_id":"A-1"}'

    def require_shape(self, _model_id: str, _execution_shape: str) -> None:
        return None

    def resolve_strategy(
        self, *, requested_strategy: str, model_id: str, has_tools: bool
    ) -> str:
        del model_id
        if requested_strategy == "react":
            return "react"
        return "function_calling" if has_tools else "react"

    async def complete_text(self, **kwargs: Any) -> str:
        turn = await self.complete(**kwargs)
        return turn.content

    async def complete(self, **kwargs: Any) -> AgentModelTurn:
        kwargs.setdefault("call_sequence", len(self.invocations) + 1)
        shape = "chat_tools" if kwargs.get("tools") is not None else "chat_text"
        self.invocations.append({"shape": shape, **kwargs})
        self.calls.append(
            SimpleNamespace(
                status="passed",
                actual_model=kwargs["model_id"],
                prompt_tokens=3,
                completion_tokens=2,
                total_tokens=5,
            )
        )
        if self.agent_turns:
            return self.agent_turns.pop(0)
        return AgentModelTurn(
            content="managed answer",
            finish_reason="stop",
            usage=AgentUsage(prompt_tokens=3, completion_tokens=2, total_tokens=5),
            raw={"model": kwargs["model_id"]},
        )

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
    agent_mode = "legacy"
    agent_turns: list[AgentModelTurn] = []

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
            "xpert_chat": "xpert",
            "xpert_app": "xpert_app",
        }.get(str(source_kind or ""))

    @staticmethod
    def agent_entry_id(source_kind: str | None) -> str | None:
        return {
            "workflow_classic": "workflow_interactive_agent",
            "workflow_deployment": "workflow_deployment_agent",
            "xpert_chat": "xpert",
            "xpert_app": "xpert_app",
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

    def agent_routing_mode(self, source_kind: str | None) -> str:
        return self.agent_mode if self.agent_entry_id(source_kind) else "legacy"

    def start_node_run(self, **kwargs: str) -> FakeManagedNodeRun:
        self.started.append(dict(kwargs))
        entry_id = self.entry_id(kwargs["source_kind"])
        assert entry_id is not None
        run = FakeManagedNodeRun(entry_id)
        self.runs.append(run)
        return run

    def start_agent_run(self, **kwargs: str) -> FakeManagedNodeRun:
        self.started.append(dict(kwargs))
        entry_id = self.agent_entry_id(kwargs["source_kind"])
        assert entry_id is not None
        run = FakeManagedNodeRun(entry_id)
        run.agent_turns = list(self.agent_turns)
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


def test_only_direct_published_xpert_route_derives_managed_context() -> None:
    direct_request = main_module.Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/xperts/xpert-1/run",
            "headers": [],
            "route": SimpleNamespace(path="/api/xperts/{xpert_id}/run"),
        }
    )

    assert main_module._trusted_workflow_execution_source_kind(
        direct_request,
        runtime_run_type="xpert",
        resume_execution=None,
    ) == "xpert_chat"
    assert main_module._trusted_workflow_execution_source_kind(
        None,
        runtime_run_type="xpert",
        resume_execution=None,
    ) is None


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
    FakeManagedWorkflowGateway.agent_mode = "legacy"
    FakeManagedWorkflowGateway.agent_turns = []
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
async def test_independent_xpert_runtime_without_control_context_keeps_legacy_path(
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
@pytest.mark.parametrize(
    ("source_kind", "run_type", "entry_id"),
    [
        ("xpert_chat", "xpert", "xpert"),
        ("xpert_app", "xpert_app", "xpert_app"),
        ("xpert_app", "xpert", "xpert_app"),
    ],
)
async def test_xpert_control_context_uses_managed_provider_and_persists_source(
    source_kind: str,
    run_type: str,
    entry_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def legacy_must_not_run(*_args: Any, **_kwargs: Any):
        raise AssertionError("legacy Xpert stream must not run")

    monkeypatch.setattr(main_module, "stream_workflow_llm_text", legacy_must_not_run)
    task_id = f"{entry_id}-managed-task"
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
        runtime_run_type=run_type,
        runtime_execution_source_kind=source_kind,
        runtime_task_id=task_id,
    )
    events = await _events(response)
    llm_end = next(
        item
        for item in events
        if item.get("event") == "node_end" and item.get("node_id") == "llm"
    )
    execution = main_module.workflow_execution_store.get(task_id)

    assert llm_end["provider_route_receipts"]["entry_id"] == entry_id
    assert llm_end["provider_route_receipts"]["call_count"] == 1
    assert execution is not None
    assert execution.source_kind == source_kind
    assert FakeManagedWorkflowGateway.instances[0].started == [
        {
            "source_kind": source_kind,
            "execution_reference": task_id,
            "node_id": "llm",
        }
    ]


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


@pytest.mark.asyncio
@pytest.mark.parametrize("node_kind", ["agent", "workflow_agent"])
async def test_managed_agent_nodes_do_not_read_legacy_gateway(
    monkeypatch: pytest.MonkeyPatch,
    node_kind: str,
) -> None:
    FakeManagedWorkflowGateway.agent_mode = "managed_required"
    monkeypatch.setattr(main_module, "WORKFLOW_AGENT_ENABLED", True)

    def legacy_must_not_run(*_args: Any, **_kwargs: Any):
        raise AssertionError("legacy Agent gateway must not run")

    monkeypatch.setattr(main_module, "collect_chat_completion_text", legacy_must_not_run)
    monkeypatch.setattr(main_module, "stream_workflow_llm_text", legacy_must_not_run)
    monkeypatch.setattr(main_module, "stream_workflow_llm_messages", legacy_must_not_run)
    node_data = (
        {
            "kind": "agent",
            "agentMode": "direct",
            "modelId": MODEL_ID,
            "instruction": "Handle {{user_input}}",
            "outputVariable": "answer",
        }
        if node_kind == "agent"
        else {
            "kind": "workflow_agent",
            "agentName": "managed-agent",
            "modelId": MODEL_ID,
            "rolePrompt": "You are a managed agent.",
            "taskInput": "Handle {{user_input}}",
            "toolMode": "none",
            "outputVariable": "answer",
        }
    )
    response = await main_module._run_workflow_response(
        _workflow(
            [
                {"id": "input", "type": "input", "data": {"kind": "input"}},
                {"id": "agent-node", "type": node_kind, "data": node_data},
                {
                    "id": "output",
                    "type": "output",
                    "data": {"kind": "output", "outputVariable": "answer"},
                },
            ],
            [
                {"id": "e1", "source": "input", "target": "agent-node"},
                {"id": "e2", "source": "agent-node", "target": "output"},
            ],
        ),
        None,
        runtime_execution_source_kind="workflow_classic",
        runtime_task_id=f"managed-{node_kind}-task",
    )
    events = await _events(response)

    agent_end = next(
        item
        for item in events
        if item.get("event") == "node_end" and item.get("node_id") == "agent-node"
    )
    receipt = agent_end["provider_route_receipts"]
    assert receipt["entry_id"] == "workflow_interactive_agent"
    assert receipt["call_count"] == 1
    assert receipt["calls"][0]["model_id"] == MODEL_ID
    assert not [item for item in events if item.get("event") == "error"]
    assert FakeManagedWorkflowGateway.instances[0].started == [
        {
            "source_kind": "workflow_classic",
            "execution_reference": f"managed-{node_kind}-task",
            "node_id": "agent-node",
            "logical_phase": "initial",
        }
    ]


@pytest.mark.asyncio
async def test_managed_deployment_agent_uses_stable_execution_id() -> None:
    FakeManagedWorkflowGateway.agent_mode = "managed_required"
    response = await main_module._run_workflow_response(
        _workflow(
            [
                {"id": "input", "type": "input", "data": {"kind": "input"}},
                {
                    "id": "agent-node",
                    "type": "workflow_agent",
                    "data": {
                        "kind": "workflow_agent",
                        "agentName": "managed-deployment-agent",
                        "modelId": MODEL_ID,
                        "rolePrompt": "You are a managed agent.",
                        "taskInput": "Handle {{user_input}}",
                        "toolMode": "none",
                    },
                },
            ],
            [{"id": "e1", "source": "input", "target": "agent-node"}],
        ),
        None,
        runtime_execution_source_kind="workflow_deployment",
        runtime_metadata={
            "workflow_deployment_execution_id": "deployment-agent-execution-1"
        },
        runtime_task_id="deployment-agent-task-id",
    )
    events = await _events(response)

    agent_end = next(
        item
        for item in events
        if item.get("event") == "node_end" and item.get("node_id") == "agent-node"
    )
    assert agent_end["provider_route_receipts"]["entry_id"] == (
        "workflow_deployment_agent"
    )
    assert FakeManagedWorkflowGateway.instances[0].started == [
        {
            "source_kind": "workflow_deployment",
            "execution_reference": "deployment-agent-execution-1",
            "node_id": "agent-node",
            "logical_phase": "initial",
        }
    ]


@pytest.mark.asyncio
async def test_managed_workflow_agent_structured_output_uses_json_qualification() -> None:
    FakeManagedWorkflowGateway.agent_mode = "managed_required"
    response = await main_module._run_workflow_response(
        _workflow(
            [
                {"id": "input", "type": "input", "data": {"kind": "input"}},
                {
                    "id": "agent-node",
                    "type": "workflow_agent",
                    "data": {
                        "kind": "workflow_agent",
                        "agentName": "managed-structured-agent",
                        "modelId": MODEL_ID,
                        "rolePrompt": "Return JSON.",
                        "taskInput": "Extract {{user_input}}",
                        "toolMode": "none",
                        "outputVariable": "answer",
                    },
                },
                {
                    "id": "structured",
                    "type": "runtime_middleware",
                    "data": {
                        "kind": "runtime_middleware",
                        "runtimeMiddlewareId": "structured_output",
                        "runtimeMiddlewareKind": "runtime_middleware.structured_output",
                        "middlewarePriority": "20",
                        "runtimeMiddlewareConfig": {
                            "schema_json": {
                                "type": "object",
                                "required": ["order_id"],
                                "properties": {"order_id": {"type": "string"}},
                                "additionalProperties": False,
                            },
                            "repair_attempts": 0,
                        },
                    },
                },
            ],
            [
                {"id": "e1", "source": "input", "target": "agent-node"},
                {
                    "id": "bind-structured",
                    "source": "structured",
                    "target": "agent-node",
                    "sourceHandle": "middleware-binding",
                    "targetHandle": "middleware",
                },
            ],
        ),
        None,
        runtime_execution_source_kind="workflow_classic",
        runtime_task_id="managed-structured-agent-task",
    )
    events = await _events(response)

    agent_end = next(
        item
        for item in events
        if item.get("event") == "node_end" and item.get("node_id") == "agent-node"
    )
    managed_run = FakeManagedWorkflowGateway.instances[0].runs[0]
    assert json.loads(agent_end["output"]) == {"order_id": "A-1"}
    assert [item["shape"] for item in managed_run.invocations] == [
        "chat_json_object"
    ]
    assert agent_end["provider_route_receipts"]["call_count"] == 1


@pytest.mark.asyncio
async def test_managed_agent_provider_failure_never_calls_legacy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeManagedWorkflowGateway.agent_mode = "managed_required"
    monkeypatch.setattr(main_module, "WORKFLOW_AGENT_ENABLED", True)
    legacy_calls: list[str] = []

    async def legacy_must_not_run(*_args: Any, **_kwargs: Any) -> str:
        legacy_calls.append("legacy")
        return "legacy answer"

    async def fail_after_dispatch(
        self: FakeManagedNodeRun, **kwargs: Any
    ) -> AgentModelTurn:
        kwargs.setdefault("call_sequence", len(self.invocations) + 1)
        self.invocations.append({"shape": "chat_text", **kwargs})
        self.calls.append(
            SimpleNamespace(
                status="uncertain",
                actual_model=None,
                prompt_tokens=None,
                completion_tokens=None,
                total_tokens=None,
            )
        )
        error = ManagedWorkflowRoutingError(
            "provider_workload_transport_error",
            "Managed Provider dispatch failed closed.",
        )
        error.receipt = self.receipt_summary()
        raise error

    monkeypatch.setattr(
        main_module, "collect_chat_completion_text", legacy_must_not_run
    )
    monkeypatch.setattr(FakeManagedNodeRun, "complete", fail_after_dispatch)
    response = await main_module._run_workflow_response(
        _workflow(
            [
                {"id": "input", "type": "input", "data": {"kind": "input"}},
                {
                    "id": "agent-node",
                    "type": "agent",
                    "data": {
                        "kind": "agent",
                        "agentMode": "direct",
                        "modelId": MODEL_ID,
                        "instruction": "Handle {{user_input}}",
                    },
                },
            ],
            [{"id": "e1", "source": "input", "target": "agent-node"}],
        ),
        None,
        runtime_execution_source_kind="workflow_classic",
        runtime_task_id="managed-agent-failure-task",
    )
    events = await _events(response)

    error = next(item for item in events if item.get("event") == "error")
    assert error["code"] == "provider_workload_transport_error"
    assert error["provider_route_receipts"]["call_count"] == 1
    assert error["provider_route_receipts"]["calls"][0]["dispatched"] is True
    assert legacy_calls == []
    assert len(FakeManagedWorkflowGateway.instances[0].runs[0].invocations) == 1


@pytest.mark.asyncio
async def test_managed_agent_runtime_failure_finalizes_provider_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeManagedWorkflowGateway.agent_mode = "managed_required"
    monkeypatch.setattr(main_module, "WORKFLOW_AGENT_ENABLED", True)

    async def fail_after_model_turn(
        self: FakeManagedNodeRun, **kwargs: Any
    ) -> AgentModelTurn:
        kwargs.setdefault("call_sequence", len(self.invocations) + 1)
        self.invocations.append({"shape": "chat_text", **kwargs})
        self.calls.append(
            SimpleNamespace(
                status="passed",
                actual_model=kwargs["model_id"],
                prompt_tokens=3,
                completion_tokens=2,
                total_tokens=5,
            )
        )
        raise RuntimeError("synthetic runtime post-processing failure")

    monkeypatch.setattr(FakeManagedNodeRun, "complete", fail_after_model_turn)
    response = await main_module._run_workflow_response(
        _workflow(
            [
                {"id": "input", "type": "input", "data": {"kind": "input"}},
                {
                    "id": "agent-node",
                    "type": "agent",
                    "data": {
                        "kind": "agent",
                        "agentMode": "direct",
                        "modelId": MODEL_ID,
                        "instruction": "Handle {{user_input}}",
                    },
                },
            ],
            [{"id": "e1", "source": "input", "target": "agent-node"}],
        ),
        None,
        runtime_execution_source_kind="workflow_classic",
        runtime_task_id="managed-agent-runtime-failure",
    )
    events = await _events(response)

    managed_run = FakeManagedWorkflowGateway.instances[0].runs[0]
    assert managed_run.status == "failed"
    node_end = next(
        item
        for item in events
        if item.get("event") == "node_end" and item.get("node_id") == "agent-node"
    )
    assert node_end["provider_route_receipts"]["status"] == "failed"
    assert node_end["provider_route_receipts"]["call_count"] == 1


@pytest.mark.asyncio
async def test_managed_output_policy_block_finalizes_provider_run() -> None:
    FakeManagedWorkflowGateway.agent_mode = "managed_required"
    response = await main_module._run_workflow_response(
        _workflow(
            [
                {"id": "input", "type": "input", "data": {"kind": "input"}},
                {
                    "id": "agent-node",
                    "type": "workflow_agent",
                    "data": {
                        "kind": "workflow_agent",
                        "agentName": "managed-policy-agent",
                        "modelId": MODEL_ID,
                        "rolePrompt": "You are a managed agent.",
                        "taskInput": "Handle {{user_input}}",
                        "toolMode": "none",
                    },
                },
                {
                    "id": "content-policy",
                    "type": "runtime_middleware",
                    "data": {
                        "kind": "runtime_middleware",
                        "runtimeMiddlewareId": "content_policy",
                        "runtimeMiddlewareKind": "runtime_middleware.content_policy",
                        "middlewarePriority": "100",
                        "runtimeMiddlewareConfig": {
                            "phase": "output",
                            "rules": [
                                {
                                    "id": "rule_1",
                                    "label": "Synthetic block",
                                    "detector": "literal_terms",
                                    "action": "block",
                                    "terms": ["managed answer"],
                                    "caseSensitive": False,
                                }
                            ],
                        },
                    },
                },
            ],
            [
                {"id": "e1", "source": "input", "target": "agent-node"},
                {
                    "id": "bind-policy",
                    "source": "content-policy",
                    "target": "agent-node",
                    "sourceHandle": "middleware-binding",
                    "targetHandle": "middleware",
                },
            ],
        ),
        None,
        runtime_execution_source_kind="workflow_classic",
        runtime_task_id="managed-agent-policy-block",
    )
    events = await _events(response)

    managed_run = FakeManagedWorkflowGateway.instances[0].runs[0]
    assert managed_run.status == "failed"
    error = next(item for item in events if item.get("event") == "error")
    assert error["provider_route_receipts"]["status"] == "failed"
    assert error["provider_route_receipts"]["call_count"] == 1


@pytest.mark.asyncio
async def test_managed_workflow_agent_blocks_legacy_retry_before_dispatch() -> None:
    FakeManagedWorkflowGateway.agent_mode = "managed_required"
    response = await main_module._run_workflow_response(
        _workflow(
            [
                {"id": "input", "type": "input", "data": {"kind": "input"}},
                {
                    "id": "agent-node",
                    "type": "workflow_agent",
                    "data": {
                        "kind": "workflow_agent",
                        "agentName": "managed-agent",
                        "modelId": MODEL_ID,
                        "rolePrompt": "You are a managed agent.",
                        "taskInput": "Handle {{user_input}}",
                        "toolMode": "none",
                        "retryOnFailure": True,
                        "fallbackModelId": "provider/fallback-model",
                    },
                },
            ],
            [{"id": "e1", "source": "input", "target": "agent-node"}],
        ),
        None,
        runtime_execution_source_kind="workflow_classic",
        runtime_task_id="managed-agent-retry-block",
    )
    events = await _events(response)

    error = next(item for item in events if item.get("event") == "error")
    assert error["code"] == "provider_workload_legacy_retry_fallback_configured"
    assert error["provider_route_receipts"]["call_count"] == 0
    assert FakeManagedWorkflowGateway.instances[0].started == []
    assert not any(
        item.get("event") == "node_end" and item.get("node_id") == "agent-node"
        for item in events
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("requested_strategy", "model_text", "expected_shape", "expected_output"),
    [
        (
            "auto",
            "managed function answer",
            "chat_tools",
            "managed function answer",
        ),
        (
            "react",
            "FinalAnswer: managed react answer",
            "chat_text",
            "managed react answer",
        ),
    ],
)
async def test_managed_agent_strategy_is_selected_before_first_post(
    monkeypatch: pytest.MonkeyPatch,
    requested_strategy: str,
    model_text: str,
    expected_shape: str,
    expected_output: str,
) -> None:
    FakeManagedWorkflowGateway.agent_mode = "managed_required"
    FakeManagedWorkflowGateway.agent_turns = [
        AgentModelTurn(
            content=model_text,
            finish_reason="stop",
            usage=AgentUsage(prompt_tokens=4, completion_tokens=2, total_tokens=6),
            raw={"model": MODEL_ID},
        )
    ]
    monkeypatch.setattr(main_module, "WORKFLOW_AGENT_ENABLED", True)
    monkeypatch.setattr(main_module, "WORKFLOW_AGENT_STRATEGY_V2_ENABLED", True)

    class OneToolProvider:
        async def list_tools(self) -> list[RuntimeTool]:
            return [
                RuntimeTool(
                    name="lookup",
                    description="Read-only lookup",
                    input_schema={"type": "object", "properties": {}},
                )
            ]

    monkeypatch.setattr(main_module, "workflow_mcp_provider", OneToolProvider())
    response = await main_module._run_workflow_response(
        _workflow(
            [
                {"id": "input", "type": "input", "data": {"kind": "input"}},
                {
                    "id": "agent-node",
                    "type": "agent",
                    "data": {
                        "kind": "agent",
                        "agentMode": "tool_first",
                        "agentStrategy": requested_strategy,
                        "modelId": MODEL_ID,
                        "instruction": "Handle {{user_input}}",
                        "outputVariable": "answer",
                    },
                },
            ],
            [{"id": "e1", "source": "input", "target": "agent-node"}],
        ),
        None,
        runtime_execution_source_kind="workflow_classic",
        runtime_task_id=f"managed-agent-{requested_strategy}",
    )
    events = await _events(response)

    agent_end = next(
        item
        for item in events
        if item.get("event") == "node_end" and item.get("node_id") == "agent-node"
    )
    managed_run = FakeManagedWorkflowGateway.instances[0].runs[0]
    assert [item["shape"] for item in managed_run.invocations] == [expected_shape]
    assert agent_end["provider_route_receipts"]["call_count"] == 1
    assert agent_end["output"] == expected_output


@pytest.mark.asyncio
async def test_managed_function_calling_records_each_model_round_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeManagedWorkflowGateway.agent_mode = "managed_required"
    FakeManagedWorkflowGateway.agent_turns = [
        AgentModelTurn(
            content="",
            tool_calls=[
                AgentToolCall(
                    call_id="call_lookup_1",
                    name="lookup",
                    raw_arguments='{"q":"value"}',
                )
            ],
            finish_reason="tool_calls",
            usage=AgentUsage(prompt_tokens=4, completion_tokens=2, total_tokens=6),
            raw={"model": MODEL_ID},
        ),
        AgentModelTurn(
            content="managed tool answer",
            finish_reason="stop",
            usage=AgentUsage(prompt_tokens=6, completion_tokens=3, total_tokens=9),
            raw={"model": MODEL_ID},
        ),
    ]
    monkeypatch.setattr(main_module, "WORKFLOW_AGENT_ENABLED", True)
    monkeypatch.setattr(main_module, "WORKFLOW_AGENT_STRATEGY_V2_ENABLED", True)

    class ToolProvider:
        def __init__(self) -> None:
            self.tool = RuntimeTool(
                name="lookup",
                description="Read-only lookup",
                input_schema={
                    "type": "object",
                    "properties": {"q": {"type": "string"}},
                    "required": ["q"],
                },
                read_only=True,
            )
            self.calls: list[Any] = []

        async def list_tools(self) -> list[RuntimeTool]:
            return [self.tool]

        async def find_tool(self, tool_name: str) -> RuntimeTool | None:
            return self.tool if tool_name == self.tool.name else None

        async def call_tool(self, call: Any) -> RuntimeToolResult:
            self.calls.append(call)
            return RuntimeToolResult(
                output="lookup result",
                content=[{"type": "text", "text": "lookup result"}],
                metadata={"content_types": ["text"]},
                is_error=False,
            )

    provider = ToolProvider()
    original_provider = main_module.runtime_capabilities.require(
        "mcp_tools"
    ).implementation
    monkeypatch.setattr(main_module, "workflow_mcp_provider", provider)
    main_module.runtime_capabilities.register("mcp_tools", provider)
    try:
        response = await main_module._run_workflow_response(
            _workflow(
                [
                    {"id": "input", "type": "input", "data": {"kind": "input"}},
                    {
                        "id": "agent-node",
                        "type": "agent",
                        "data": {
                            "kind": "agent",
                            "agentMode": "tool_first",
                            "agentStrategy": "function_calling",
                            "modelId": MODEL_ID,
                            "instruction": "Use lookup for {{user_input}}",
                            "toolNames": "lookup",
                            "outputVariable": "answer",
                        },
                    },
                ],
                [{"id": "e1", "source": "input", "target": "agent-node"}],
            ),
            None,
            runtime_execution_source_kind="workflow_classic",
            runtime_task_id="managed-agent-tool-loop",
        )
        events = await _events(response)
    finally:
        main_module.runtime_capabilities.register("mcp_tools", original_provider)

    agent_end = next(
        item
        for item in events
        if item.get("event") == "node_end" and item.get("node_id") == "agent-node"
    )
    managed_run = FakeManagedWorkflowGateway.instances[0].runs[0]
    assert agent_end["output"] == "managed tool answer"
    assert [item["shape"] for item in managed_run.invocations] == [
        "chat_tools",
        "chat_tools",
    ]
    assert agent_end["provider_route_receipts"]["call_count"] == 2
    assert [
        item["call_sequence"]
        for item in agent_end["provider_route_receipts"]["calls"]
    ] == [1, 2]
    assert len(provider.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["replace", "revise"])
async def test_managed_hitl_resume_dispatches_only_explicit_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    decision: str,
) -> None:
    FakeManagedWorkflowGateway.agent_mode = "managed_required"
    approvals = RuntimeApprovalStore(tmp_path / "approvals")
    executions = WorkflowExecutionStore(tmp_path / "hitl-executions")
    monkeypatch.setattr(main_module, "runtime_approval_store", approvals)
    monkeypatch.setattr(main_module, "workflow_execution_store", executions)
    workflow = _workflow(
        [
            {"id": "input", "type": "input", "data": {"kind": "input"}},
            {
                "id": "agent-node",
                "type": "workflow_agent",
                "data": {
                    "kind": "workflow_agent",
                    "agentName": "managed-agent",
                    "modelId": MODEL_ID,
                    "rolePrompt": "You are a managed agent.",
                    "taskInput": "Handle {{user_input}}",
                    "toolMode": "none",
                    "outputVariable": "answer",
                },
            },
            {
                "id": "hitl",
                "type": "runtime_middleware",
                "data": {
                    "kind": "runtime_middleware",
                    "runtimeMiddlewareId": "human_in_the_loop",
                    "runtimeMiddlewareKind": "runtime_middleware.human_in_the_loop",
                    "middlewarePriority": "40",
                    "runtimeMiddlewareConfig": {
                        "interrupt_on_tools": "",
                        "final_confirmation": True,
                        "max_revision_rounds": 1,
                        "timeout_seconds": 3600,
                    },
                },
            },
            {
                "id": "output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "answer"},
            },
        ],
        [
            {"id": "e1", "source": "input", "target": "agent-node"},
            {"id": "e2", "source": "agent-node", "target": "output"},
            {
                "id": "bind-hitl",
                "source": "hitl",
                "target": "agent-node",
                "sourceHandle": "middleware-binding",
                "targetHandle": "middleware",
            },
        ],
    )
    response = await main_module._run_workflow_response(
        workflow,
        None,
        runtime_execution_source_kind="workflow_classic",
        runtime_task_id="managed-hitl-task",
    )
    events = await _events(response)
    pending = next(
        item for item in events if item.get("event") == "runtime_approval_pending"
    )
    approval = approvals.require(pending["approval_id"])
    decided = approvals.decide(
        approval.approval_id,
        revision=approval.revision,
        decision=decision,
        operator="tester",
        replacement_text="approved answer" if decision == "replace" else None,
        message="revise once" if decision == "revise" else None,
    )
    executions.mark_ready(pending["task_id"], approval_id=approval.approval_id)
    claimed = executions.claim(pending["task_id"], worker_id="test-worker")
    await main_module.resume_runtime_approval_execution(claimed, decided)

    completed = executions.require(pending["task_id"])
    assert len(FakeManagedWorkflowGateway.instances) == 2
    assert len(FakeManagedWorkflowGateway.instances[0].runs) == 1
    if decision == "replace":
        agent_end = next(
            item
            for item in completed.events
            if item.get("event") == "node_end"
            and item.get("node_id") == "agent-node"
        )
        assert completed.status == "completed"
        assert completed.result == "approved answer"
        assert agent_end["provider_route_receipts"]["call_count"] == 1
        assert FakeManagedWorkflowGateway.instances[1].started == []
    else:
        assert completed.status == "waiting"
        assert len(FakeManagedWorkflowGateway.instances[1].runs) == 1
        resumed_run = FakeManagedWorkflowGateway.instances[1].runs[0]
        assert resumed_run.receipt_summary()["call_count"] == 1
        assert FakeManagedWorkflowGateway.instances[1].started[0][
            "logical_phase"
        ].startswith("resume:")
        assert sum(
            1
            for item in completed.events
            if item.get("event") == "runtime_approval_pending"
        ) == 2


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


@pytest.mark.parametrize(
    "entry_id",
    [
        "workflow_interactive_agent",
        "workflow_deployment_agent",
        "xpert",
        "xpert_app",
    ],
)
def test_durable_event_preserves_integrated_workload_entry_ids(
    tmp_path: Path,
    entry_id: str,
) -> None:
    receipt = WorkflowExecutionStore._safe_provider_route_receipt(
        {
            "entry_id": entry_id,
            "status": "passed",
            "connection_id": "must-not-survive",
            "calls": [],
        }
    )

    assert receipt["entry_id"] == entry_id
    assert "connection_id" not in json.dumps(receipt)
