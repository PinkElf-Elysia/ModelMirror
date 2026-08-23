from __future__ import annotations

import asyncio
import hashlib
import io
import tarfile
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import httpx
from unittest.mock import AsyncMock, Mock
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from server.coding_worker.api import configure_coding_worker_for_tests, router
import server.coding_worker.api as worker_api
from server.coding_worker.adapters import (
    LegacyHarnessDriver,
    LegacyHarnessSupervisor,
    legacy_substrate_from_service,
)
from server.coding_worker.evaluation import LegacyEvaluationAdapter
from server.coding_worker.provider import (
    FakeCodingAgentProvider,
    ProviderCapabilities,
    ProviderCheckpoint,
    ProviderEvent,
    ProviderEventKind,
    ProviderOpenRequest,
    ProviderSession,
)
from server.coding_worker.contracts import (
    OperationState,
    Origin,
    TaskCreateRequest,
    TaskSpec,
    TaskState,
)
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


def _client(
    tmp_path: Path,
    *,
    blocked: bool = False,
    provider: FakeCodingAgentProvider | None = None,
    master_key: bytes | None = None,
) -> tuple[TestClient, CodingWorkerService]:
    store = CodingWorkerStore(
        tmp_path / "worker", master_key=master_key or Fernet.generate_key()
    )
    broker = WorkspaceBroker(
        tmp_path / "worker",
        {
            "manifest": InMemoryWorkspaceSourceAdapter(
                {("source-01", "revision-01"): {"main.py": b"print('ok')\n"}}
            )
        },
        id_key=b"a" * 32,
    )
    selected_provider = provider or FakeCodingAgentProvider(
        block=asyncio.Event() if blocked else None
    )
    supervisor = LegacyHarnessSupervisor(selected_provider)
    service = CodingWorkerService(
        store=store,
        workspace_broker=broker,
        provider=LegacyHarnessDriver(selected_provider),
        harness_supervisor=supervisor,
    )
    evaluation = LegacyEvaluationAdapter(
        service,
        attestation_reader=supervisor.harness_attestations,
        controller_generation=lambda: supervisor.controller_generation,
    )
    configure_coding_worker_for_tests(
        legacy_substrate_from_service(service, evaluation=evaluation),
        enabled=True,
    )
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), service


def test_worker_error_mapping_uses_neutral_status_and_sanitizes_source_reason() -> None:
    class NeutralError(RuntimeError):
        code = "neutral_conflict"
        status = 409

    with pytest.raises(HTTPException) as neutral:
        worker_api._raise_worker_error(NeutralError("Neutral conflict."))
    assert neutral.value.status_code == 409
    assert neutral.value.detail == {
        "code": "neutral_conflict",
        "message": "Neutral conflict.",
    }

    class UnsafeSourceError(RuntimeError):
        code = "workspace_source_unavailable"
        status = 409
        reason = "C:/private/repository"

    with pytest.raises(HTTPException) as source:
        worker_api._raise_worker_error(UnsafeSourceError("private"))
    assert source.value.detail == {
        "code": "workspace_source_unavailable",
        "message": "Workspace source is unavailable.",
        "reason": "temporarily_unavailable",
    }


class _QuestionProvider(FakeCodingAgentProvider):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []
        self.restore_count = 0

    async def message(
        self, session: ProviderSession, text: str
    ) -> AsyncIterator[ProviderEvent]:
        self.messages.append(text)
        if len(self.messages) == 1:
            yield ProviderEvent(
                kind=ProviderEventKind.PLAN,
                data={
                    "explanation": "Confirm the bounded repair.",
                    "items": [
                        {"step": "inspect", "status": "completed"},
                        {"step": "repair", "status": "pending"},
                    ],
                },
            )
            yield ProviderEvent(
                kind=ProviderEventKind.TODO,
                data={
                    "items": [
                        {
                            "todo_id": "confirm-scope",
                            "content": "Confirm the repair scope",
                            "status": "in_progress",
                        }
                    ]
                },
            )
            yield ProviderEvent(
                kind=ProviderEventKind.QUESTION,
                data={
                    "question_id": "question_scope",
                    "prompt": "Which repair scope should be used?",
                    "options": [
                        {"option_id": "minimal", "label": "Minimal repair"},
                        {"option_id": "broad", "label": "Broader cleanup"},
                    ],
                },
            )
            return
        yield ProviderEvent(kind=ProviderEventKind.TURN_COMPLETED)

    async def restore(
        self, request: ProviderOpenRequest, checkpoint: ProviderCheckpoint
    ) -> ProviderSession:
        self.restore_count += 1
        return await super().restore(request, checkpoint)


class _UnavailableProvider(FakeCodingAgentProvider):
    def __init__(self) -> None:
        super().__init__()
        self.available = True

    async def capabilities(self) -> ProviderCapabilities:
        if not self.available:
            raise RuntimeError("provider unavailable")
        return await super().capabilities()


class _NativeInteractionOnlyProvider(FakeCodingAgentProvider):
    async def capabilities(self) -> ProviderCapabilities:
        capabilities = await super().capabilities()
        return capabilities.model_copy(
            update={
                "tool_names": tuple(
                    name
                    for name in capabilities.tool_names
                    if name
                    not in {
                        "update_plan",
                        "update_todo",
                        "request_user_input",
                        "compact_context",
                    }
                )
            }
        )


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
        "structured_plan": False,
        "user_questions": False,
        "context_compaction": False,
        "turn_history": False,
        "subtasks": False,
    }
    assert client.post("/api/coding-worker/v1/tasks", json=_payload()).status_code == 404


def test_native_provider_hints_do_not_enable_platform_interaction_capabilities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODING_WORKER_V16_ENABLED", "true")
    monkeypatch.setenv("CODING_WORKER_INTERACTION_ENABLED", "true")
    client, _service = _client(
        tmp_path, provider=_NativeInteractionOnlyProvider()
    )

    with client:
        capabilities = client.get("/api/coding-worker/v1/capabilities").json()

    assert capabilities["structured_plan"] is False
    assert capabilities["user_questions"] is False
    assert capabilities["context_compaction"] is False


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
        "structured_plan": False,
        "user_questions": False,
        "context_compaction": False,
        "turn_history": False,
        "subtasks": False,
    }
    encoded = response.text.lower()
    assert "opencode" not in encoded and "claude" not in encoded


def test_task_capabilities_are_bound_and_legacy_advanced_features_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FakeCodingAgentProvider(block=asyncio.Event())
    client, service = _client(tmp_path, provider=provider)
    monkeypatch.setenv("CODING_WORKER_V15_ENABLED", "true")
    monkeypatch.setenv("CODING_WORKER_SHELL_ENABLED", "true")
    request = TaskCreateRequest.model_validate(_payload("legacy-capability-task"))
    legacy = service.store.create_task(
        TaskSpec(
            **request.model_dump(),
            origin=Origin(module="worker-console", object_id="local-user"),
        )
    )

    with client:
        created = client.post(
            "/api/coding-worker/v1/tasks", json=_payload("capability-task")
        ).json()
        response = client.get(
            f"/api/coding-worker/v1/tasks/{created['task_id']}/capabilities"
        )
        assert response.status_code == 200
        statuses = {
            item["name"]: item for item in response.json()["capabilities"]
        }
        assert statuses["task_runtime"]["available"] is True
        assert statuses["professional_file_tools"]["available"] is True
        assert statuses["shell"]["available"] is True
        assert statuses["code_intelligence"]["reason"] == "feature_disabled"

        legacy_response = client.get(
            f"/api/coding-worker/v1/tasks/{legacy.task_id}/capabilities"
        )
        assert legacy_response.status_code == 200
        legacy_statuses = {
            item["name"]: item
            for item in legacy_response.json()["capabilities"]
        }
        assert legacy_statuses["task_runtime"]["available"] is True
        assert legacy_statuses["professional_file_tools"] == {
            "name": "professional_file_tools",
            "enabled": True,
            "supported": False,
            "available": False,
            "reason": "legacy_task",
        }


def test_task_capabilities_reject_stale_binding_and_global_flags_follow_health(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _UnavailableProvider()
    client, service = _client(tmp_path, provider=provider)
    monkeypatch.setenv("CODING_WORKER_V15_ENABLED", "true")

    with client:
        created = client.post(
            "/api/coding-worker/v1/tasks", json=_payload("binding-task")
        ).json()
        provider.controller_generation = 7
        asyncio.run(service.refresh_provider_capabilities(force=True))
        response = client.get(
            f"/api/coding-worker/v1/tasks/{created['task_id']}/capabilities"
        )
        statuses = {
            item["name"]: item for item in response.json()["capabilities"]
        }
        assert statuses["professional_file_tools"]["reason"] == (
            "provider_binding_changed"
        )

        provider.available = False
        asyncio.run(service.refresh_provider_capabilities(force=True))
        capabilities = client.get(
            "/api/coding-worker/v1/capabilities"
        ).json()
        assert capabilities["task_runtime"] is False
        assert capabilities["professional_file_tools"] is False


def test_task_children_response_preserves_the_subtask_index_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _service = _client(tmp_path, blocked=True)
    monkeypatch.setenv("CODING_WORKER_V16_ENABLED", "true")
    monkeypatch.setenv("CODING_WORKER_SESSION_CONTROLS_ENABLED", "true")
    with client:
        created = client.post(
            "/api/coding-worker/v1/tasks", json=_payload("children-contract-task")
        ).json()
        response = client.get(
            f"/api/coding-worker/v1/tasks/{created['task_id']}/children"
        )
    assert response.status_code == 200
    assert response.json() == {"tasks": [], "subtasks": []}


def test_v16_plan_and_question_resume_once_from_encrypted_waiting_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODING_WORKER_V16_ENABLED", "true")
    monkeypatch.setenv("CODING_WORKER_INTERACTION_ENABLED", "true")
    key = Fernet.generate_key()
    provider = _QuestionProvider()
    client, service = _client(tmp_path, provider=provider, master_key=key)

    with client:
        capabilities = client.get("/api/coding-worker/v1/capabilities").json()
        assert capabilities["structured_plan"] is True
        assert capabilities["user_questions"] is True
        assert capabilities["context_compaction"] is True
        created = client.post(
            "/api/coding-worker/v1/tasks", json=_payload("question-task")
        ).json()
        task_id = created["task_id"]
        waiting = asyncio.run(
            service.wait_for(
                task_id, lambda item: item.state is TaskState.WAITING_INPUT
            )
        )
        assert waiting.reason == "user_input_required"

        plan = client.get(f"/api/coding-worker/v1/tasks/{task_id}/plan")
        assert plan.status_code == 200
        assert [item["step"] for item in plan.json()["items"]] == [
            "inspect",
            "repair",
        ]
        todo = client.get(f"/api/coding-worker/v1/tasks/{task_id}/todo")
        assert todo.status_code == 200
        assert todo.json()["items"] == [
            {
                "todo_id": "confirm-scope",
                "content": "Confirm the repair scope",
                "status": "in_progress",
            }
        ]
        questions = client.get(
            f"/api/coding-worker/v1/tasks/{task_id}/questions"
        )
        assert questions.status_code == 200
        assert questions.json()["questions"] == [
            {
                "task_id": task_id,
                "question_id": "question_scope",
                "turn_id": questions.json()["questions"][0]["turn_id"],
                "status": "pending",
                "prompt": "Which repair scope should be used?",
                "options": [
                    {"option_id": "minimal", "label": "Minimal repair"},
                    {"option_id": "broad", "label": "Broader cleanup"},
                ],
                "answer": None,
                "selected_option_id": None,
                "created_at": questions.json()["questions"][0]["created_at"],
                "resolved_at": None,
            }
        ]

        restarted_store = CodingWorkerStore(tmp_path / "worker", master_key=key)
        assert restarted_store.get_task(task_id).state is TaskState.WAITING_INPUT
        assert restarted_store.list_questions(task_id)[0].status.value == "pending"
        persisted = b"".join(
            path.read_bytes()
            for path in (tmp_path / "worker").glob("coding-worker.sqlite3*")
        )
        assert b"Which repair scope should be used?" not in persisted

        answered = client.post(
            f"/api/coding-worker/v1/tasks/{task_id}/questions/question_scope",
            json={"option_id": "minimal"},
        )
        assert answered.status_code == 202
        assert answered.json()["status"] == "resolved"
        replay = client.post(
            f"/api/coding-worker/v1/tasks/{task_id}/questions/question_scope",
            json={"option_id": "minimal"},
        )
        assert replay.status_code == 409
        assert replay.json()["detail"]["code"] == "question_already_resolved"
        terminal = asyncio.run(
            service.wait_for(task_id, lambda item: item.state is TaskState.BLOCKED)
        )

    assert terminal.reason == "acceptance_runner_pending"
    assert provider.restore_count == 1
    assert provider.messages[-1].endswith("Minimal repair [option:minimal]")


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


def test_create_is_idempotent_and_server_owns_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _service = _client(tmp_path)
    with client:
        assert client.get("/api/coding-worker/v1").json()["acceptance_checks"] == []
        first = client.post("/api/coding-worker/v1/tasks", json=_payload())
        monkeypatch.setenv("CODING_WORKER_MODEL_ROUTES", "coding/other")
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


def test_source_admission_returns_only_safe_conflict_reason(tmp_path: Path) -> None:
    client, service = _client(tmp_path)
    payload = _payload("missing-revision")
    payload["workspace_source"]["revision"] = "revision-missing"  # type: ignore[index]

    with client:
        response = client.post("/api/coding-worker/v1/tasks", json=payload)

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "workspace_source_unavailable",
        "message": "Workspace source is unavailable.",
        "reason": "revision_changed",
    }
    assert service.store.list_tasks() == []


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


def test_parity_workspace_export_is_flagged_terminal_and_path_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, service = _client(tmp_path)
    with client:
        created = client.post("/api/coding-worker/v1/tasks", json=_payload()).json()
        task_id = created["task_id"]
        disabled = client.post(
            f"/api/coding-worker/v1/tasks/{task_id}/workspace/parity-export"
        )
        assert disabled.status_code == 404
        monkeypatch.setenv("CODING_WORKER_PARITY_ENABLED", "true")
        asyncio.run(service.wait_for(task_id, lambda item: item.state.value == "blocked"))
        exported = client.post(
            f"/api/coding-worker/v1/tasks/{task_id}/workspace/parity-export"
        )
        assert exported.status_code == 200
        artifact = exported.json()
        assert artifact["metadata"]["kind"] == "parity_workspace_export"
        assert "workspace_tree_hash" in artifact["metadata"]
        assert "path" not in exported.text.lower()
        download = client.get(
            f"/api/coding-worker/v1/tasks/{task_id}/artifacts/{artifact['artifact_id']}"
        )
        with tarfile.open(fileobj=io.BytesIO(download.content), mode="r:") as archive:
            member = archive.getmember("main.py")
            assert member.mtime == 0 and member.uid == member.gid == 0
            assert archive.extractfile(member).read() == b"print('ok')\n"


def test_parity_export_preserves_workspace_not_ready_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, service = _client(tmp_path)
    request = TaskCreateRequest.model_validate(_payload("queued-export"))
    task = service.store.create_task(
        TaskSpec(
            origin=Origin(module="worker-console", object_id="local-user"),
            **request.model_dump(),
        )
    )
    monkeypatch.setenv("CODING_WORKER_PARITY_ENABLED", "true")

    response = client.post(
        f"/api/coding-worker/v1/tasks/{task.task_id}/workspace/parity-export"
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "workspace_not_ready"


def test_preview_checks_task_before_rejecting_path(tmp_path: Path) -> None:
    client, _service = _client(tmp_path)

    response = client.get(
        "/api/coding-worker/v1/tasks/task_"
        + "f" * 32
        + "/services/service_"
        + "a" * 32
        + "/preview/%2E%2E%5Csecret"
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "task_not_found"


def test_pin_unpin_and_delete_keep_active_task_safe(tmp_path: Path) -> None:
    client, _service = _client(tmp_path, blocked=True)
    with client:
        task = client.post("/api/coding-worker/v1/tasks", json=_payload()).json()
        task_id = task["task_id"]
        assert client.post(f"/api/coding-worker/v1/tasks/{task_id}/pin").json()["pinned"]
        assert not client.delete(f"/api/coding-worker/v1/tasks/{task_id}/pin").json()["pinned"]
        assert client.delete(f"/api/coding-worker/v1/tasks/{task_id}").status_code == 409
        assert client.post(f"/api/coding-worker/v1/tasks/{task_id}/cancel").status_code == 200
        deleted = client.delete(f"/api/coding-worker/v1/tasks/{task_id}")
        assert deleted.status_code == 204, deleted.text


def test_harness_fault_endpoint_is_flag_and_token_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, service = _client(tmp_path, blocked=True)
    harness_broker = Mock()
    service.tool_broker = harness_broker
    token = "harness-controller-token-0123456789abcdef"
    payload = {
        "task_id": "task_" + "a" * 32,
        "component": "executor",
        "point": "after_side_effect_before_receipt",
    }
    with client:
        assert (
            client.post("/api/coding-worker/v1/harness/faults", json=payload).status_code
            == 404
        )
        monkeypatch.setenv("CODING_WORKER_HARNESS_V3_ENABLED", "true")
        monkeypatch.setenv("CODING_WORKER_HARNESS_CONTROLLER_TOKEN", token)
        assert (
            client.post("/api/coding-worker/v1/harness/faults", json=payload).status_code
            == 401
        )
        accepted = client.post(
            "/api/coding-worker/v1/harness/faults",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
    assert accepted.status_code == 202
    assert accepted.json() == {"status": "armed"}
    harness_broker.arm_harness_fault.assert_called_once_with(
        payload["task_id"], payload["component"], payload["point"]
    )


def test_harness_attestation_is_flag_token_and_two_provider_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, service = _client(tmp_path, blocked=True)
    token = "harness-controller-token-0123456789abcdef"
    service.harness_supervisor._provider.controller_generation = 9
    service.harness_supervisor._provider.harness_attestations = AsyncMock(
        return_value={
            "slot-a": {"route_id": "coding/default"},
            "slot-b": {"route_id": "coding/default"},
        }
    )
    with client:
        endpoint = "/api/coding-worker/v1/harness/attestation"
        assert client.get(endpoint).status_code == 404
        monkeypatch.setenv("CODING_WORKER_HARNESS_V3_ENABLED", "true")
        monkeypatch.setenv("CODING_WORKER_HARNESS_CONTROLLER_TOKEN", token)
        assert client.get(endpoint).status_code == 401
        response = client.get(
            endpoint,
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    assert response.json()["protocol"] == (
        "modelmirror-coding-harness-attestation/v1"
    )
    assert response.json()["controller_generation"] == 9
    assert len(response.json()["server_generation"]) == 32
    assert set(response.json()["providers"]) == {"slot-a", "slot-b"}
    assert len(response.json()["server_code_bundle_sha256"]) == 64


def test_harness_attestation_rejects_incomplete_provider_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, service = _client(tmp_path, blocked=True)
    token = "harness-controller-token-0123456789abcdef"
    monkeypatch.setenv("CODING_WORKER_HARNESS_V3_ENABLED", "true")
    monkeypatch.setenv("CODING_WORKER_HARNESS_CONTROLLER_TOKEN", token)
    service.harness_supervisor._provider.harness_attestations = AsyncMock(
        return_value={"slot-a": {"route_id": "coding/default"}}
    )
    with client:
        response = client.get(
            "/api/coding-worker/v1/harness/attestation",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == (
        "harness_attestation_unavailable"
    )


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


def test_decided_approval_never_leaves_an_orphaned_task_running(
    tmp_path: Path,
) -> None:
    client, service = _client(tmp_path, blocked=True)
    request = TaskCreateRequest.model_validate(_payload("orphaned-approval"))
    task = service.store.create_task(
        TaskSpec(
            **request.model_dump(),
            origin=Origin(module="test", object_id="orphaned-approval"),
        )
    )
    service.store.transition(task.task_id, TaskState.PREPARING)
    service.store.transition(task.task_id, TaskState.RUNNING)
    service.store.transition(task.task_id, TaskState.WAITING_APPROVAL)
    approval = service.store.create_approval(
        task_id=task.task_id,
        operation_id="orphaned-approval-operation",
        capability="command",
        request={"argv": ["python", "-m", "pytest"]},
    )

    with client:
        decided = client.post(
            f"/api/coding-worker/v1/tasks/{task.task_id}/approvals",
            json={"approval_id": approval.approval_id, "decision": "approve_once"},
        )

    assert decided.status_code == 200
    interrupted = service.store.get_task(task.task_id)
    assert interrupted.state is TaskState.INTERRUPTED
    assert interrupted.reason == "approval_resume_required"


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


def test_operation_output_and_changeset_queries_are_replayable_and_task_bound(
    tmp_path: Path,
) -> None:
    client, service = _client(tmp_path, blocked=True)
    with client:
        first = client.post("/api/coding-worker/v1/tasks", json=_payload()).json()
        second = client.post(
            "/api/coding-worker/v1/tasks", json=_payload("api-task-02")
        ).json()
        task_id = first["task_id"]
        task = asyncio.run(
            service.wait_for(task_id, lambda item: item.workspace_id is not None)
        )
        operation_id = "shell_api_output"
        operation = service.store.create_operation(
            task_id=task_id,
            operation_id=operation_id,
            tool_name="run_shell",
            intent_sha256="a" * 64,
            request={"arguments": {}, "workspace_id": task.workspace_id},
        )
        service.store.transition_operation(
            operation.operation_id, OperationState.RUNNING
        )
        first_chunk = service.store.append_event(
            task_id,
            "operation_output",
            {
                "operation_id": operation_id,
                "stream": "stdout",
                "text": "first\n",
                "truncated": False,
            },
        )
        service.store.append_event(task_id, "unrelated", {"value": True})
        second_chunk = service.store.append_event(
            task_id,
            "operation_output",
            {
                "operation_id": operation_id,
                "stream": "stderr",
                "text": "second\n",
                "truncated": False,
            },
        )
        changeset = {
            "changeset_id": "changeset_" + "b" * 32,
            "task_id": task_id,
            "operation_id": operation_id,
            "base_tree_hash": "c" * 64,
            "result_tree_hash": "d" * 64,
            "state": "applied",
            "entries": [],
            "artifact_id": None,
            "created_at": 1.0,
            "updated_at": 2.0,
        }
        service.store.transition_operation(
            operation.operation_id,
            OperationState.COMPLETED,
            result={"changeset": changeset},
        )

        output = client.get(
            f"/api/coding-worker/v1/tasks/{task_id}/operations/{operation_id}/output"
        )
        assert output.status_code == 200
        assert [item["text"] for item in output.json()["chunks"]] == [
            "first\n",
            "second\n",
        ]
        replay = client.get(
            f"/api/coding-worker/v1/tasks/{task_id}/operations/{operation_id}/output",
            params={"after": first_chunk.sequence},
        )
        assert [item["sequence"] for item in replay.json()["chunks"]] == [
            second_chunk.sequence
        ]
        queried = client.get(
            f"/api/coding-worker/v1/tasks/{task_id}/changesets/{operation_id}"
        )
        assert queried.status_code == 200
        assert queried.json() == changeset
        foreign_output = client.get(
            f"/api/coding-worker/v1/tasks/{second['task_id']}/operations/"
            f"{operation_id}/output"
        )
        foreign_changeset = client.get(
            f"/api/coding-worker/v1/tasks/{second['task_id']}/changesets/"
            f"{operation_id}"
        )
        assert foreign_output.status_code == 404
        assert foreign_changeset.status_code == 404


def test_code_intelligence_queries_are_task_bound_and_mark_stale_diagnostics(
    tmp_path: Path,
) -> None:
    client, service = _client(tmp_path, blocked=True)
    with client:
        first = client.post("/api/coding-worker/v1/tasks", json=_payload()).json()
        second = client.post(
            "/api/coding-worker/v1/tasks", json=_payload("api-task-code-02")
        ).json()
        task_id = first["task_id"]
        task = asyncio.run(
            service.wait_for(task_id, lambda item: item.workspace_id is not None)
        )
        assert task.workspace_id is not None
        entry = next(
            item
            for item in service.workspace_broker.tree(task.workspace_id)
            if item.display_path == "main.py"
        )
        tree_hash = service.workspace_broker.current_tree_hash(task.workspace_id)
        operation_id = "code_api_diagnostics"
        operation = service.store.create_operation(
            task_id=task_id,
            operation_id=operation_id,
            tool_name="code_diagnostics",
            intent_sha256="e" * 64,
            request={"arguments": {"entry_id": entry.entry_id}},
        )
        service.store.transition_operation(operation_id, OperationState.RUNNING)
        diagnostic = {
            "diagnostic_id": "diagnostic_" + "f" * 32,
            "task_id": task_id,
            "entry_id": entry.entry_id,
            "workspace_tree_hash": tree_hash,
            "range": {
                "start": {"line": 0, "character": 0},
                "end": {"line": 0, "character": 5},
            },
            "severity": "error",
            "code": "reportAssignmentType",
            "message": "Type is not assignable",
            "created_at": 1.0,
        }
        service.store.transition_operation(
            operation.operation_id,
            OperationState.COMPLETED,
            result={
                "task_id": task_id,
                "entry_id": entry.entry_id,
                "workspace_tree_hash": tree_hash,
                "operation": "diagnostics",
                "language": "python",
                "diagnostics": [diagnostic],
            },
        )

        queried = client.get(
            f"/api/coding-worker/v1/tasks/{task_id}/code-intelligence/{operation_id}"
        )
        diagnostics = client.get(
            f"/api/coding-worker/v1/tasks/{task_id}/diagnostics/{operation_id}"
        )
        assert queried.status_code == 200
        assert queried.json()["stale"] is False
        assert queried.json()["result"] == {"diagnostics": [diagnostic]}
        assert diagnostics.status_code == 200
        assert diagnostics.json()["diagnostics"] == [diagnostic]

        repository = service.workspace_broker.repository_path(task.workspace_id)
        repository.joinpath("main.py").write_text("print('changed')\n", encoding="utf-8")
        stale = client.get(
            f"/api/coding-worker/v1/tasks/{task_id}/diagnostics/{operation_id}"
        )
        assert stale.status_code == 200
        assert stale.json()["stale"] is True
        assert stale.json()["current_tree_hash"] != tree_hash
        foreign = client.get(
            f"/api/coding-worker/v1/tasks/{second['task_id']}/diagnostics/"
            f"{operation_id}"
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
