from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest
import httpx
from unittest.mock import AsyncMock
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.coding_worker.api import configure_coding_worker_for_tests, router
import server.coding_worker.api as worker_api
from server.coding_worker.provider import FakeCodingAgentProvider
from server.coding_worker.service import CodingWorkerService
from server.coding_worker.store import CodingWorkerStore
from server.coding_worker.workspace import InMemoryWorkspaceSourceAdapter, WorkspaceBroker


def _payload(client_task_id: str = "api-task-01") -> dict[str, object]:
    return {
        "client_task_id": client_task_id,
        "objective": "Inspect the project and report evidence.",
        "workspace_source": {
            "kind": "manifest",
            "source_id": "source-01",
            "revision": "revision-01",
        },
        "acceptance": {
            "contract_id": "contract-01",
            "required_checks": [
                {"check_id": "pytest", "label": "pytest", "kind": "command"}
            ],
        },
        "policy_profile": "inspect",
        "model_route": "coding/default",
    }


def _client(tmp_path: Path, *, blocked: bool = False) -> tuple[TestClient, CodingWorkerService]:
    store = CodingWorkerStore(tmp_path / "worker", master_key=Fernet.generate_key())
    broker = WorkspaceBroker(
        tmp_path / "worker",
        {
            "manifest": InMemoryWorkspaceSourceAdapter(
                {("source-01", "revision-01"): {"main.py": b"print('ok')\n"}}
            )
        },
        id_key=b"a" * 32,
    )
    provider = FakeCodingAgentProvider(block=asyncio.Event() if blocked else None)
    service = CodingWorkerService(store=store, workspace_broker=broker, provider=provider)
    configure_coding_worker_for_tests(service, enabled=True)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), service


def teardown_function() -> None:
    configure_coding_worker_for_tests(None, enabled=None)


def test_feature_flag_is_default_deny(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CODING_WORKER_V14_ENABLED", raising=False)
    monkeypatch.delenv("CODING_WORKER_V15_ENABLED", raising=False)
    monkeypatch.delenv("CODING_WORKER_SHELL_ENABLED", raising=False)
    monkeypatch.delenv("CODING_WORKER_CODE_INTELLIGENCE_ENABLED", raising=False)
    configure_coding_worker_for_tests(None, enabled=None)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    status = client.get("/api/coding-worker/v1").json()
    assert status["enabled"] is False
    assert status["capabilities"] == {
        "api_version": "v1",
        "task_runtime": False,
        "professional_file_tools": False,
        "shell": False,
        "operation_output": False,
        "changesets": False,
        "code_intelligence": False,
    }
    assert client.post("/api/coding-worker/v1/tasks", json=_payload()).status_code == 404


def test_v15_capabilities_are_vendor_neutral_and_independently_gated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _service = _client(tmp_path)
    monkeypatch.setenv("CODING_WORKER_V15_ENABLED", "true")
    monkeypatch.setenv("CODING_WORKER_SHELL_ENABLED", "true")
    monkeypatch.setenv("CODING_WORKER_CODE_INTELLIGENCE_ENABLED", "false")
    with client:
        response = client.get("/api/coding-worker/v1/capabilities")
    assert response.status_code == 200
    capabilities = response.json()
    assert capabilities == {
        "api_version": "v1",
        "task_runtime": True,
        "professional_file_tools": True,
        "shell": True,
        "operation_output": True,
        "changesets": True,
        "code_intelligence": False,
    }
    encoded = response.text.lower()
    assert "opencode" not in encoded and "claude" not in encoded


def test_enabled_runtime_fails_closed_when_sidecar_tokens_are_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODING_WORKER_V14_ENABLED", "true")
    monkeypatch.delenv("CODING_WORKER_SLOT_A_TOKEN", raising=False)
    monkeypatch.delenv("CODING_WORKER_SLOT_B_TOKEN", raising=False)
    configure_coding_worker_for_tests(None, enabled=None)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        status = client.get("/api/coding-worker/v1").json()
        assert status["enabled"] is True
        assert status["available"] is False
        assert status["reason"] == "coding_worker_config_invalid"
        unavailable = client.post("/api/coding-worker/v1/tasks", json=_payload())
        assert unavailable.status_code == 503
        assert unavailable.json()["detail"]["reason"] == "coding_worker_config_invalid"


def test_create_is_idempotent_and_server_owns_origin(tmp_path: Path) -> None:
    client, _service = _client(tmp_path)
    with client:
        assert client.get("/api/coding-worker/v1").json()["acceptance_checks"] == []
        first = client.post("/api/coding-worker/v1/tasks", json=_payload())
        second = client.post("/api/coding-worker/v1/tasks", json=_payload())
    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["task_id"] == second.json()["task_id"]
    assert first.json()["spec"]["origin"] == {
        "module": "worker-console",
        "object_id": "local-user",
    }

    forged = _payload("forged")
    forged["origin"] = {"module": "evil", "object_id": "evil"}
    assert client.post("/api/coding-worker/v1/tasks", json=forged).status_code == 422


def test_model_route_is_catalog_controlled(tmp_path: Path) -> None:
    client, _service = _client(tmp_path)
    payload = _payload()
    payload["model_route"] = "vendor/raw-model"
    response = client.post("/api/coding-worker/v1/tasks", json=payload)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "model_route_not_allowed"


def test_workspace_endpoints_use_task_and_opaque_entry_ids(tmp_path: Path) -> None:
    client, service = _client(tmp_path)
    with client:
        created = client.post("/api/coding-worker/v1/tasks", json=_payload()).json()
        task_id = created["task_id"]
        terminal = asyncio.run(
            service.wait_for(task_id, lambda item: item.state.value == "blocked")
        )
        assert terminal.workspace_id
        tree = client.get(f"/api/coding-worker/v1/tasks/{task_id}/workspace/tree")
        assert tree.status_code == 200
        entry = next(item for item in tree.json()["entries"] if item["kind"] == "file")
        assert entry["entry_id"].startswith("entry_")
        assert "C:" not in entry["entry_id"] and "/tmp/" not in entry["entry_id"]
        preview = client.get(
            f"/api/coding-worker/v1/tasks/{task_id}/workspace/entries/{entry['entry_id']}"
        )
        assert preview.text == "print('ok')\n"
        diff = client.get(f"/api/coding-worker/v1/tasks/{task_id}/workspace/diff")
        assert diff.status_code == 200 and diff.content == b""


def test_pin_unpin_and_delete_keep_active_task_safe(tmp_path: Path) -> None:
    client, _service = _client(tmp_path, blocked=True)
    with client:
        task = client.post("/api/coding-worker/v1/tasks", json=_payload()).json()
        task_id = task["task_id"]
        assert client.post(f"/api/coding-worker/v1/tasks/{task_id}/pin").json()["pinned"]
        assert not client.delete(f"/api/coding-worker/v1/tasks/{task_id}/pin").json()["pinned"]
        assert client.delete(f"/api/coding-worker/v1/tasks/{task_id}").status_code == 409
        assert client.post(f"/api/coding-worker/v1/tasks/{task_id}/cancel").status_code == 200
        assert client.delete(f"/api/coding-worker/v1/tasks/{task_id}").status_code == 204


def test_approval_endpoints_are_task_bound_and_single_decision(tmp_path: Path) -> None:
    client, service = _client(tmp_path, blocked=True)
    with client:
        first = client.post("/api/coding-worker/v1/tasks", json=_payload()).json()
        second = client.post(
            "/api/coding-worker/v1/tasks", json=_payload("api-task-02")
        ).json()
        approval = service.store.create_approval(
            task_id=first["task_id"],
            operation_id="api-approval-operation",
            capability="command",
            request={"argv": ["python", "-m", "pytest"]},
        )
        listed = client.get(
            f"/api/coding-worker/v1/tasks/{first['task_id']}/approvals"
        )
        assert listed.status_code == 200
        assert listed.json()["approvals"][0]["approval_id"] == approval.approval_id
        foreign = client.post(
            f"/api/coding-worker/v1/tasks/{second['task_id']}/approvals",
            json={"approval_id": approval.approval_id, "decision": "approve_once"},
        )
        assert foreign.status_code == 404
        decided = client.post(
            f"/api/coding-worker/v1/tasks/{first['task_id']}/approvals",
            json={"approval_id": approval.approval_id, "decision": "approve_once"},
        )
        assert decided.status_code == 200
        assert decided.json()["lease"]["operation_limit"] == 1
        replay = client.post(
            f"/api/coding-worker/v1/tasks/{first['task_id']}/approvals",
            json={"approval_id": approval.approval_id, "decision": "reject"},
        )
        assert replay.status_code == 409


def test_shell_approval_cannot_be_promoted_to_task_scope(tmp_path: Path) -> None:
    client, service = _client(tmp_path, blocked=True)
    with client:
        task = client.post("/api/coding-worker/v1/tasks", json=_payload()).json()
        operation_id = "shell-operation-api-01"
        approval = service.store.create_approval(
            task_id=task["task_id"],
            operation_id=operation_id,
            capability="shell",
            request={
                "operation_id": operation_id,
                "script_sha256": hashlib.sha256(b"pytest -q").hexdigest(),
                "cwd": ".",
                "mode": "inspect",
                "timeout_seconds": 120,
                "network_scope_sha256": None,
            },
        )
        denied = client.post(
            f"/api/coding-worker/v1/tasks/{task['task_id']}/approvals",
            json={"approval_id": approval.approval_id, "decision": "approve_task"},
        )
        assert denied.status_code == 409
        assert denied.json()["detail"]["code"] == "shell_task_approval_forbidden"
        once = client.post(
            f"/api/coding-worker/v1/tasks/{task['task_id']}/approvals",
            json={"approval_id": approval.approval_id, "decision": "approve_once"},
        )
        assert once.status_code == 200
        assert once.json()["lease"]["operation_limit"] == 1


def test_evidence_and_artifact_download_are_opaque_and_task_bound(
    tmp_path: Path,
) -> None:
    client, service = _client(tmp_path)
    with client:
        first = client.post("/api/coding-worker/v1/tasks", json=_payload()).json()
        second = client.post(
            "/api/coding-worker/v1/tasks", json=_payload("api-task-02")
        ).json()
        task_id = first["task_id"]
        asyncio.run(service.wait_for(task_id, lambda item: item.state.value == "blocked"))
        workspace_id = service.store.get_task(task_id).workspace_id
        tree_hash = service.workspace_broker.current_tree_hash(workspace_id)
        artifact = service.store.create_artifact(
            task_id=task_id,
            media_type="text/plain; charset=utf-8",
            content=b"2 passed in 0.10s\n",
            metadata={"check_id": "pytest", "workspace_tree_hash": tree_hash},
        )
        evidence = service.store.record_evidence(
            task_id=task_id,
            check_id="pytest",
            operation_id="api-evidence-operation",
            workspace_tree_hash=tree_hash,
            exit_code=0,
            artifact_id=artifact.artifact_id,
        )

        listed = client.get(f"/api/coding-worker/v1/tasks/{task_id}/evidence")
        assert listed.status_code == 200
        assert listed.json()["evidence"][0]["evidence_id"] == evidence.evidence_id
        artifacts = client.get(f"/api/coding-worker/v1/tasks/{task_id}/artifacts")
        assert artifacts.status_code == 200
        assert artifacts.json()["artifacts"][0]["artifact_id"] == artifact.artifact_id
        download = client.get(
            f"/api/coding-worker/v1/tasks/{task_id}/artifacts/{artifact.artifact_id}"
        )
        assert download.status_code == 200
        assert download.content == b"2 passed in 0.10s\n"
        assert download.headers["cache-control"] == "no-store"
        assert "C:" not in download.headers["content-disposition"]
        foreign = client.get(
            f"/api/coding-worker/v1/tasks/{second['task_id']}/artifacts/{artifact.artifact_id}"
        )
        assert foreign.status_code == 404


def test_preview_proxy_uses_only_task_slot_and_registered_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, service = _client(tmp_path)
    executor = AsyncMock()
    service.tool_broker = AsyncMock()
    service.tool_broker.executor = executor
    with client:
        task = client.post("/api/coding-worker/v1/tasks", json=_payload()).json()
        task_id = task["task_id"]
        asyncio.run(
            service.wait_for(task_id, lambda item: item.workspace_id is not None)
        )
        executor.service_status.return_value = {
            "service_id": "service_" + "a" * 32,
            "task_id": task_id,
            "state": "running",
            "preview_port": 4173,
        }
        seen: list[str] = []

        async def fetch(url: str) -> httpx.Response:
            seen.append(url)
            return httpx.Response(200, content=b"<h1>preview</h1>", headers={"content-type": "text/html"})

        monkeypatch.setattr(worker_api, "_fetch_preview", fetch)
        response = client.get(
            f"/api/coding-worker/v1/tasks/{task_id}/services/"
            f"service_{'a' * 32}/preview/app?mode=test"
        )
        assert response.status_code == 200
        assert response.content == b"<h1>preview</h1>"
        assert seen == ["http://coding-worker-default:4173/app?mode=test"]
        assert response.headers["cache-control"] == "no-store"
        assert "sandbox" in response.headers["content-security-policy"]
