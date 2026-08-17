from __future__ import annotations

import re
import asyncio
from pathlib import Path
from typing import get_args

import httpx
import pytest
from fastapi import Request
from fastapi.responses import JSONResponse

import server.main as main_module
from server.workflow_native.schemas import NativeNodeKind
from server.workflow_native.validate import SUPPORTED_NODE_KINDS
from server.xpert_runtime.execution_store import WorkflowExecutionStore


def test_workflow_node_kind_contract_matches_frontend_and_validator() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    workflow_types_path = repository_root / "client" / "src" / "types" / "workflow.ts"
    workflow_types_source = workflow_types_path.read_text(encoding="utf-8")
    declaration = re.search(
        r"export\s+type\s+WorkflowNodeKind\s*=\s*(?P<body>.*?);",
        workflow_types_source,
        flags=re.DOTALL,
    )
    assert declaration is not None, (
        f"Could not find the WorkflowNodeKind declaration in {workflow_types_path}."
    )

    frontend_kinds = set(re.findall(r'"([a-z][a-z0-9_]*)"', declaration["body"]))
    assert frontend_kinds, (
        f"WorkflowNodeKind in {workflow_types_path} did not contain any string literals."
    )
    native_kinds = set(get_args(NativeNodeKind))
    assert native_kinds, "NativeNodeKind did not contain any Literal values."

    assert frontend_kinds == native_kinds, (
        "Frontend WorkflowNodeKind and backend NativeNodeKind drifted: "
        f"frontend_only={sorted(frontend_kinds - native_kinds)}, "
        f"backend_only={sorted(native_kinds - frontend_kinds)}"
    )
    assert native_kinds == SUPPORTED_NODE_KINDS, (
        "NativeNodeKind and validator SUPPORTED_NODE_KINDS drifted: "
        f"schema_only={sorted(native_kinds - SUPPORTED_NODE_KINDS)}, "
        f"validator_only={sorted(SUPPORTED_NODE_KINDS - native_kinds)}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("resource_kind", ["toolset_resource", "plugin_resource"])
async def test_workflow_run_accepts_resource_node_types(
    monkeypatch: pytest.MonkeyPatch,
    resource_kind: str,
) -> None:
    captured: dict[str, str | None] = {}

    async def fake_run_workflow_response(
        payload: main_module.WorkflowRunRequest,
        _request: Request,
    ) -> JSONResponse:
        captured["node_type"] = payload.workflow.nodes[0].type
        captured["data_kind"] = payload.workflow.nodes[0].data.get("kind")
        return JSONResponse(status_code=202, content={"accepted": True})

    monkeypatch.setattr(main_module, "_run_workflow_response", fake_run_workflow_response)
    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/workflow/run",
            json={
                "workflow": {
                    "id": f"{resource_kind}-contract-test",
                    "title": "Resource contract test",
                    "nodes": [
                        {
                            "id": "resource",
                            "type": resource_kind,
                            "data": {"kind": resource_kind},
                        }
                    ],
                    "edges": [],
                },
                "inputs": {},
            },
        )

    assert response.status_code == 202
    assert response.json() == {"accepted": True}
    assert captured == {
        "node_type": resource_kind,
        "data_kind": resource_kind,
    }


@pytest.mark.asyncio
async def test_workflow_run_rejects_unknown_node_type_before_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler_called = False

    async def fake_run_workflow_response(
        _payload: main_module.WorkflowRunRequest,
        _request: Request,
    ) -> JSONResponse:
        nonlocal handler_called
        handler_called = True
        return JSONResponse(status_code=202, content={"accepted": True})

    monkeypatch.setattr(main_module, "_run_workflow_response", fake_run_workflow_response)
    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/workflow/run",
            json={
                "workflow": {
                    "id": "unknown-kind-contract-test",
                    "title": "Unknown kind contract test",
                    "nodes": [
                        {
                            "id": "unknown",
                            "type": "unknown_workflow_node",
                            "data": {"kind": "unknown_workflow_node"},
                        }
                    ],
                    "edges": [],
                },
                "inputs": {},
            },
        )

    assert response.status_code == 422
    assert handler_called is False


@pytest.mark.asyncio
async def test_workflow_run_rejects_constant_override_before_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler_called = False

    async def fake_run_workflow_response(
        _payload: main_module.WorkflowRunRequest,
        _request: Request,
    ) -> JSONResponse:
        nonlocal handler_called
        handler_called = True
        return JSONResponse(status_code=202, content={"accepted": True})

    monkeypatch.setattr(main_module, "_run_workflow_response", fake_run_workflow_response)
    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/workflow/run",
            json={
                "workflow": {
                    "id": "constant-override-contract-test",
                    "title": "Constant override contract test",
                    "variables": [
                        {
                            "id": "constant-mode",
                            "name": "fixed_mode",
                            "kind": "constant",
                            "valueType": "text",
                            "defaultValue": "safe",
                        }
                    ],
                    "nodes": [
                        {
                            "id": "output",
                            "type": "output",
                            "data": {"kind": "output"},
                        }
                    ],
                    "edges": [],
                },
                "inputs": {"fixed_mode": "unsafe"},
            },
        )

    assert response.status_code == 422
    assert handler_called is False
    assert "workflow_constant_override_not_allowed:fixed_mode" in response.text


@pytest.mark.asyncio
async def test_workflow_cancel_persists_terminal_state_and_stops_live_task(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    task_id = "classic-cancel-task"
    run_id = "classic-cancel-run"
    execution_store = WorkflowExecutionStore(tmp_path / "workflow-executions")
    execution_store.create(
        task_id=task_id,
        run_id=run_id,
        run_type="workflow",
        workflow={"id": "cancel-contract", "title": "Cancel contract"},
        inputs={},
        source_kind="workflow_classic",
    )
    pause_event = asyncio.Event()
    task_store = {
        task_id: {
            "cancel_requested": False,
            "pause_event": pause_event,
        }
    }

    class FakeRunRegistry:
        cancelled: list[tuple[str, str]] = []

        async def cancel_run(self, target_run_id: str, *, reason: str):
            self.cancelled.append((target_run_id, reason))
            return None

    fake_registry = FakeRunRegistry()
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(main_module, "workflow_task_store", task_store)
    monkeypatch.setattr(main_module, "run_registry", fake_registry)
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _client: None)

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.post(f"/api/workflow/run/{task_id}/cancel")
        repeated = await client.post(f"/api/workflow/run/{task_id}/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert repeated.status_code == 200
    assert repeated.json()["status"] == "cancelled"
    assert task_store[task_id]["cancel_requested"] is True
    assert pause_event.is_set()
    assert fake_registry.cancelled == [(run_id, "cancelled_by_user")]
    assert execution_store.complete(task_id, result="late result").status == "cancelled"
    events = execution_store.require(task_id).events
    assert [event["event"] for event in events].count("workflow_cancelled") == 1
