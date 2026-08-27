from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

import server.main as main_module
from server.main import app, run_registry, workflow_execution_store
from server.rag.api import set_rag_service_for_tests
from server.rag.embedder import EmbeddingClient
from server.rag.rag_service import (
    KnowledgeBaseLockedError,
    KnowledgeBaseNotFoundError,
    RagService,
)
from server.rag.vector_store import LocalJsonVectorStore
from server.workflow_deployments import (
    WorkflowDeploymentConflictError,
    WorkflowDeploymentStore,
    WorkflowDeploymentValidationError,
)
from server.workflow_native.knowledge_proposal import (
    WorkflowKnowledgeProposalError,
    build_knowledge_proposal_receipt,
    validate_knowledge_write_proposal_config,
    validate_rendered_knowledge_proposal,
)
from server.workflow_native.node_contracts import (
    node_policy_service,
    workflow_node_contract_registry,
)


@pytest_asyncio.fixture
async def client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client


def _service(tmp_path: Path) -> RagService:
    storage = tmp_path / "rag-storage"
    return RagService(
        storage_dir=storage,
        uploads_dir=tmp_path / "rag-uploads",
        embedder=EmbeddingClient(api_key="", dimension=32),
        vector_store=LocalJsonVectorStore(storage / "vectors.json"),
        llm_enabled=False,
    )


def _node_data(kb_id: str, **patch: object) -> dict[str, object]:
    return {
        "kind": "knowledge_write_proposal",
        "contractVersion": 1,
        "knowledgeBaseId": kb_id,
        "titleTemplate": "公告：{{proposal_title}}",
        "contentVariable": "proposal_content",
        "tags": ["公告", "已核验", "公告"],
        "outputVariable": "proposal_receipt",
        **patch,
    }


def _workflow(kb_id: str, *, workflow_id: str = "draft") -> dict[str, object]:
    return {
        "id": workflow_id,
        "title": "Knowledge proposal",
        "variables": [
            {
                "id": "input-title",
                "name": "proposal_title",
                "kind": "input",
                "valueType": "text",
            },
            {
                "id": "input-content",
                "name": "proposal_content",
                "kind": "input",
                "valueType": "text",
            },
        ],
        "nodes": [
            {
                "id": "input",
                "type": "input",
                "data": {"kind": "input", "variableName": "user_input"},
            },
            {
                "id": "proposal",
                "type": "knowledge_write_proposal",
                "data": _node_data(kb_id),
            },
            {
                "id": "output",
                "type": "output",
                "data": {
                    "kind": "output",
                    "outputVariable": "proposal_receipt",
                },
            },
        ],
        "edges": [
            {"id": "input-proposal", "source": "input", "target": "proposal"},
            {"id": "proposal-output", "source": "proposal", "target": "output"},
        ],
    }


def _events(response: httpx.Response) -> list[dict[str, object]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def test_knowledge_proposal_contract_is_complete_private_and_not_plannable() -> None:
    contract = workflow_node_contract_registry.require("knowledge_write_proposal")

    assert contract.contract_status == "complete"
    assert contract.execution.side_effect == "write"
    assert contract.execution.idempotent is True
    assert contract.execution.external_io is True
    assert contract.execution.can_wait is False
    assert contract.execution.error_semantics == "fail_closed"
    assert contract.planner.enabled is False
    for context in ("workflow", "xpert", "goal", "handoff"):
        assert node_policy_service.decision(contract.kind, context).allowed
    for context in ("app", "evaluation", "evolution"):
        assert not node_policy_service.decision(contract.kind, context).allowed


def test_knowledge_proposal_config_and_runtime_values_are_strict() -> None:
    config = validate_knowledge_write_proposal_config(_node_data("kb_target"))
    assert config.tags == ("公告", "已核验")
    assert validate_rendered_knowledge_proposal(
        title="  可发布公告  ",
        content="  正文  ",
    ) == ("可发布公告", "正文")

    for patch in (
        {"contractVersion": 2},
        {"knowledgeBaseId": "{{dynamic_kb}}"},
        {"knowledgeBaseId": "kb_{dynamic}"},
        {"contentVariable": "not-valid!"},
        {"outputVariable": "proposal_content"},
        {"tags": ["tag"] * 21},
        {"tags": [1]},
    ):
        with pytest.raises(WorkflowKnowledgeProposalError):
            validate_knowledge_write_proposal_config(_node_data("kb_target", **patch))

    with pytest.raises(WorkflowKnowledgeProposalError) as non_text:
        validate_rendered_knowledge_proposal(title="Title", content={"raw": "body"})
    assert non_text.value.code == "KNOWLEDGE_PROPOSAL_INPUT_INVALID"
    with pytest.raises(WorkflowKnowledgeProposalError):
        validate_rendered_knowledge_proposal(title="x" * 161, content="body")
    with pytest.raises(WorkflowKnowledgeProposalError):
        validate_rendered_knowledge_proposal(title="Title", content="x" * 20_001)


def test_knowledge_proposal_receipt_never_contains_inbox_content() -> None:
    sentinel = "MM_R26_INBOX_SENTINEL"
    receipt = build_knowledge_proposal_receipt(
        {
            "proposal_id": "kwp_" + "a" * 32,
            "kb_id": "kb_target",
            "status": "pending",
            "revision": 1,
            "reused": False,
            "title": sentinel,
            "content": sentinel,
            "tags": [sentinel],
        },
        content_length=len(sentinel),
    )
    assert receipt == {
        "status": "pending",
        "proposalId": "kwp_" + "a" * 32,
        "knowledgeBaseId": "kb_target",
        "revision": 1,
        "reused": False,
        "contentLength": len(sentinel),
    }
    assert sentinel not in json.dumps(receipt)


@pytest.mark.asyncio
async def test_form_graph_cannot_reach_knowledge_inbox(
    client: httpx.AsyncClient,
) -> None:
    workflow = _workflow("kb_target")
    workflow["nodes"][0] = {
        "id": "input",
        "type": "form_event_entry",
        "data": {
            "kind": "form_event_entry",
            "contractVersion": 1,
            "formTitle": "Form",
            "formDescription": "Description",
            "submitLabel": "Submit",
            "privacyNotice": "Notice",
            "successTitle": "Accepted",
            "successMessage": "Accepted",
            "theme": "light",
            "eventVariable": "form_event",
            "submissionVariable": "submission",
            "fields": [
                {
                    "id": "field_name",
                    "outputVariable": "form_name",
                    "label": "Name",
                    "helpText": "",
                    "placeholder": "",
                    "type": "short_text",
                    "required": True,
                    "options": [],
                }
            ],
        },
    }
    response = await client.post(
        "/api/workflow-native/validate",
        json={"workflow": workflow},
    )
    assert response.status_code == 200
    assert any(
        issue["code"] == "form_knowledge_write_proposal_forbidden"
        for issue in response.json()["issues"]
    )


@pytest.mark.asyncio
async def test_persistent_wait_cannot_precede_private_proposal_content(
    client: httpx.AsyncClient,
) -> None:
    workflow = _workflow("kb_target")
    workflow["nodes"].insert(
        1,
        {
            "id": "wait",
            "type": "suspend_wait",
            "data": {
                "kind": "suspend_wait",
                "waitMode": "duration",
                "durationSeconds": 30,
                "outputVariable": "wait_event",
            },
        },
    )
    workflow["edges"] = [
        {"id": "input-wait", "source": "input", "target": "wait"},
        {"id": "wait-proposal", "source": "wait", "target": "proposal"},
        {"id": "proposal-output", "source": "proposal", "target": "output"},
    ]
    response = await client.post(
        "/api/workflow-native/validate",
        json={"workflow": workflow},
    )
    assert response.status_code == 200
    assert any(
        issue["code"] == "knowledge_proposal_after_wait_forbidden"
        for issue in response.json()["issues"]
    )


def test_publish_and_activate_revalidate_writable_knowledge_base(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    kb = service.create_knowledge_base("release target")
    available = True

    def validate_target(kb_id: str) -> None:
        if not available:
            raise ValueError("target disappeared")
        service.validate_knowledge_write_target(kb_id)

    store = WorkflowDeploymentStore(
        storage_dir=tmp_path / "workflow-store",
        knowledge_proposal_validator=validate_target,
    )
    project = store.create_project(_workflow(kb["id"]))
    release = store.publish(project.project_id)
    with pytest.raises(WorkflowDeploymentConflictError, match="disabled"):
        store.activate(
            project.project_id,
            release.version,
            webhooks_enabled=False,
        )

    available = False
    with pytest.raises(WorkflowDeploymentConflictError, match="unavailable"):
        store.activate(
            project.project_id,
            release.version,
            webhooks_enabled=False,
            knowledge_proposals_enabled=True,
        )

    missing_validator = WorkflowDeploymentStore(
        storage_dir=tmp_path / "missing-validator"
    )
    missing_project = missing_validator.create_project(_workflow(kb["id"]))
    with pytest.raises(WorkflowDeploymentValidationError, match="unavailable"):
        missing_validator.publish(missing_project.project_id)

    locked = service.create_knowledge_base("locked", corpus_locked=True)
    with pytest.raises(KnowledgeBaseLockedError):
        service.validate_knowledge_write_target(locked["id"])
    provisioning = service.create_knowledge_base(
        "provisioning",
        provisioning_status="preparing",
    )
    with pytest.raises(KnowledgeBaseNotFoundError):
        service.validate_knowledge_write_target(provisioning["id"])


@pytest.mark.asyncio
async def test_runtime_is_fail_closed_and_redacts_the_source_variable(
    client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    kb = service.create_knowledge_base("runtime target")
    set_rag_service_for_tests(service)
    workflow = _workflow(kb["id"])
    sentinel = "MM_R26_PRIVATE_BODY_71f129"
    request = {
        "workflow": workflow,
        "inputs": {
            "user_input": "submit",
            "proposal_title": "发布说明",
            "proposal_content": sentinel,
        },
    }
    try:
        monkeypatch.delenv("WORKFLOW_KNOWLEDGE_PROPOSALS_ENABLED", raising=False)
        disabled = await client.post("/api/workflow/run", json=request)
        disabled_events = _events(disabled)
        error = next(item for item in disabled_events if item.get("event") == "error")
        assert error["code"] == "WORKFLOW_KNOWLEDGE_PROPOSALS_DISABLED"
        assert service.list_knowledge_write_proposals(kb_id=kb["id"]) == []

        monkeypatch.setenv("WORKFLOW_KNOWLEDGE_PROPOSALS_ENABLED", "true")
        response = await client.post("/api/workflow/run", json=request)
        assert response.status_code == 200
        events = _events(response)
        assert not any(item.get("event") == "error" for item in events)
        proposal_delta = next(
            item
            for item in events
            if item.get("event") == "node_delta"
            and item.get("node_type") == "knowledge_write_proposal"
        )
        receipt = json.loads(str(proposal_delta["output"]))
        assert receipt["status"] == "pending"
        assert receipt["knowledgeBaseId"] == kb["id"]
        assert sentinel not in response.text

        task_id = response.headers["X-ModelMirror-Runtime-Task-Id"]
        run_id = response.headers["X-ModelMirror-Runtime-Run-Id"]
        execution = workflow_execution_store.get(task_id)
        assert execution is not None
        persisted = json.dumps(execution.inputs, ensure_ascii=False)
        assert sentinel not in persisted
        assert "body_sha256" in persisted
        checkpoints = await run_registry.list_checkpoints(run_id)
        checkpoint_json = json.dumps(
            [
                {
                    "summary": item.summary,
                    "metadata": item.metadata,
                }
                for item in checkpoints
            ],
            ensure_ascii=False,
        )
        assert sentinel not in checkpoint_json

        inbox = service.list_knowledge_write_proposals(kb_id=kb["id"])
        assert len(inbox) == 1
        assert inbox[0]["content"] == sentinel
    finally:
        set_rag_service_for_tests(None)


@pytest.mark.asyncio
async def test_upstream_node_delta_cannot_stream_proposal_source_content(
    client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    kb = service.create_knowledge_base("producer target")
    sentinel = "MM_R26_UPSTREAM_DELTA_SENTINEL_a710ef"
    workflow = _workflow(kb["id"])
    workflow["variables"] = [
        declaration
        for declaration in workflow["variables"]
        if declaration["name"] != "proposal_content"
    ]
    workflow["nodes"].insert(
        1,
        {
            "id": "prepare-content",
            "type": "variable_assign",
            "data": {
                "kind": "variable_assign",
                "contractVersion": 2,
                "outputVariable": "proposal_content",
                "valueSource": "literal",
                "literalValue": sentinel,
            },
        },
    )
    workflow["edges"] = [
        {"id": "input-prepare", "source": "input", "target": "prepare-content"},
        {"id": "prepare-proposal", "source": "prepare-content", "target": "proposal"},
        {"id": "proposal-output", "source": "proposal", "target": "output"},
    ]
    monkeypatch.setenv("WORKFLOW_KNOWLEDGE_PROPOSALS_ENABLED", "true")
    set_rag_service_for_tests(service)
    try:
        response = await client.post(
            "/api/workflow/run",
            json={
                "workflow": workflow,
                "inputs": {"user_input": "submit", "proposal_title": "上游正文"},
            },
        )
        assert response.status_code == 200
        assert sentinel not in response.text
        producer_delta = next(
            item
            for item in _events(response)
            if item.get("event") == "node_delta"
            and item.get("node_id") == "prepare-content"
        )
        assert producer_delta["output"] == "knowledge proposal source withheld"
        inbox = service.list_knowledge_write_proposals(kb_id=kb["id"])
        assert inbox[0]["content"] == sentinel
    finally:
        set_rag_service_for_tests(None)


@pytest.mark.asyncio
async def test_failed_workflow_agent_does_not_schedule_knowledge_proposal(
    client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    kb = service.create_knowledge_base("failed producer target")
    workflow = _workflow(kb["id"])
    workflow["variables"] = [
        declaration
        for declaration in workflow["variables"]
        if declaration["name"] != "proposal_content"
    ]
    workflow["nodes"].insert(
        1,
        {
            "id": "prepare-content",
            "type": "workflow_agent",
            "data": {
                "kind": "workflow_agent",
                "agentName": "announcement-agent",
                "modelId": "test/model",
                "rolePrompt": "Generate a synthetic announcement.",
                "taskInput": "{{user_input}}",
                "outputVariable": "proposal_content",
                "toolMode": "none",
                "exceptionHandling": "none",
            },
        },
    )
    workflow["edges"] = [
        {"id": "input-agent", "source": "input", "target": "prepare-content"},
        {"id": "agent-proposal", "source": "prepare-content", "target": "proposal"},
        {"id": "proposal-output", "source": "proposal", "target": "output"},
    ]

    async def failing_stream():
        raise OSError("synthetic gateway failure")
        yield ""  # pragma: no cover

    monkeypatch.setattr(
        main_module,
        "stream_workflow_llm_text",
        lambda *args, **kwargs: failing_stream(),
    )
    monkeypatch.setattr(
        main_module,
        "LLM_GATEWAY_URL",
        "http://test.invalid/v1/chat/completions",
    )
    monkeypatch.setattr(main_module, "LLM_GATEWAY_KEY", "synthetic-test-key")
    monkeypatch.setenv("WORKFLOW_KNOWLEDGE_PROPOSALS_ENABLED", "true")
    set_rag_service_for_tests(service)
    try:
        response = await client.post(
            "/api/workflow/run",
            json={
                "workflow": workflow,
                "inputs": {
                    "user_input": "draft announcement",
                    "proposal_title": "Failure boundary",
                },
            },
        )
        assert response.status_code == 200, response.text
        events = _events(response)
        errors = [item for item in events if item.get("event") == "error"]

        assert len(errors) == 1
        assert errors[0]["node_id"] == "prepare-content"
        assert "synthetic gateway failure" in str(errors[0]["message"])
        assert not any(
            item.get("node_id") == "proposal"
            for item in events
            if item.get("event") in {"node_delta", "node_end", "error"}
        )
        assert service.list_knowledge_write_proposals(kb_id=kb["id"]) == []

        execution = workflow_execution_store.get(
            response.headers["X-ModelMirror-Runtime-Task-Id"]
        )
        assert execution is not None
        assert execution.status == "failed"
    finally:
        set_rag_service_for_tests(None)


@pytest.mark.asyncio
async def test_unexpected_inbox_error_does_not_log_private_content(
    client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = _service(tmp_path)
    kb = service.create_knowledge_base("failing target")
    sentinel = "MM_R26_EXCEPTION_SENTINEL_48e17d"

    def explode(*args: object, **kwargs: object) -> dict[str, object]:
        raise RuntimeError(sentinel)

    monkeypatch.setattr(service, "create_knowledge_write_proposal", explode)
    monkeypatch.setenv("WORKFLOW_KNOWLEDGE_PROPOSALS_ENABLED", "true")
    set_rag_service_for_tests(service)
    caplog.set_level(logging.WARNING)
    try:
        response = await client.post(
            "/api/workflow/run",
            json={
                "workflow": _workflow(kb["id"]),
                "inputs": {
                    "user_input": "submit",
                    "proposal_title": "异常测试",
                    "proposal_content": sentinel,
                },
            },
        )
        events = _events(response)
        error = next(item for item in events if item.get("event") == "error")
        assert error["code"] == "KNOWLEDGE_PROPOSAL_CREATE_FAILED"
        assert sentinel not in response.text
        assert sentinel not in caplog.text
        assert "RuntimeError" in caplog.text
    finally:
        set_rag_service_for_tests(None)
