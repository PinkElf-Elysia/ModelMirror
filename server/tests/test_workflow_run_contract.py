from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

import httpx
import pytest
from fastapi import Request
from fastapi.responses import JSONResponse

import server.main as main_module
from server.workflow_native.schemas import NativeNodeKind
from server.workflow_native.validate import SUPPORTED_NODE_KINDS


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
