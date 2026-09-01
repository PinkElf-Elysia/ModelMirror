from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

import pytest
from starlette.responses import StreamingResponse

import server.main as main_module
from server.rag.api import set_rag_service_for_tests
from server.rag.embedder import EmbeddingClient
from server.rag.rag_service import RagService
from server.rag.vector_store import LocalJsonVectorStore
from server.workflow_native.schemas import NativeWorkflowDefinition
from server.workflow_native.secure_http import WorkflowHttpRequestError
from server.xpert_runtime import RunRegistry
from server.xpert_runtime.approval_store import RuntimeApprovalStore
from server.xpert_runtime.execution_store import WorkflowExecutionStore
from server.xperts import (
    XpertContextStore,
    XpertRunRequest,
    XpertStore,
    set_xpert_context_store_for_tests,
    set_xpert_store_for_tests,
)


def _published_xpert_with_history_bound_retry(
    store: XpertStore,
) -> tuple[str, int]:
    created = store.create_xpert(name="Retry history runtime")
    draft = created.draft.model_copy(deep=True)
    draft.history_variable = "chat_history"
    nodes = {node.id: node.model_copy(deep=True) for node in draft.workflow.nodes}
    agent = nodes["workflow-agent-1"]
    agent.data["modelId"] = "test/model"
    agent.data["taskInput"] = "HTTP response: {{http_response}}"
    request = {
        "id": "http-retry-1",
        "type": "http_request",
        "position": {"x": 220, "y": 140},
        "data": {
            "kind": "http_request",
            "contractVersion": 2,
            "method": "GET",
            "url": "https://example.test/status",
            "queryItems": [
                {
                    "id": "history-query",
                    "name": "history",
                    "binding": {
                        "source": "variable",
                        "variable": "chat_history",
                    },
                }
            ],
            "headerItems": [],
            "bodyMode": "none",
            "formFields": [],
            "authType": "none",
            "timeoutSeconds": 30,
            "redirectLimit": 0,
            "responseLimitBytes": 1_048_576,
            "responseMode": "auto",
            "statusPolicy": "success_only",
            "outputVariable": "http_response",
            "failureAction": "stop",
            "retryMode": "transient",
            "maxAttempts": 2,
        },
    }
    workflow_payload = draft.workflow.model_dump(mode="json")
    workflow_payload["nodes"] = [
        nodes["input-1"].model_dump(mode="json"),
        request,
        agent.model_dump(mode="json"),
        nodes["output-1"].model_dump(mode="json"),
    ]
    workflow_payload["edges"] = [
        {"id": "input-http", "source": "input-1", "target": "http-retry-1"},
        {
            "id": "http-agent",
            "source": "http-retry-1",
            "target": "workflow-agent-1",
        },
        {
            "id": "agent-output",
            "source": "workflow-agent-1",
            "target": "output-1",
        },
    ]
    draft.workflow = NativeWorkflowDefinition.model_validate(workflow_payload)
    updated = store.update_xpert(
        created.id,
        {"draft": draft.model_dump(mode="json")},
    )
    version = store.publish_xpert(
        created.id,
        expected_revision=updated.draft_revision,
    )
    return created.id, version.version


def _published_xpert_with_sensitive_proposal_before_retry(
    store: XpertStore,
    *,
    knowledge_base_id: str,
    source_kind: str,
    proposal_binding: str,
) -> tuple[str, int, str, str]:
    created = store.create_xpert(name=f"Sensitive {source_kind} retry")
    draft = created.draft.model_copy(deep=True)
    nodes = {node.id: node.model_copy(deep=True) for node in draft.workflow.nodes}
    if source_kind == "history":
        source_variable = "chat_history"
        compatibility_alias = "conversation_history"
        draft.history_variable = source_variable
    else:
        source_variable = "custom_input"
        compatibility_alias = "user_input"
        draft.input_variable = source_variable
        nodes["input-1"].data["variableName"] = source_variable
    proposal_variable = (
        source_variable
        if proposal_binding == "custom"
        else compatibility_alias
    )
    agent = nodes["workflow-agent-1"]
    agent.data["modelId"] = "test/model"
    agent.data["taskInput"] = "HTTP response: {{http_response}}"
    proposal = {
        "id": "proposal-1",
        "type": "knowledge_write_proposal",
        "position": {"x": 180, "y": 120},
        "data": {
            "kind": "knowledge_write_proposal",
            "contractVersion": 1,
            "knowledgeBaseId": knowledge_base_id,
            "titleTemplate": "Sensitive source proposal",
            "contentVariable": proposal_variable,
            "tags": [],
            "outputVariable": "proposal_receipt",
        },
    }
    request = {
        "id": "http-retry-1",
        "type": "http_request",
        "position": {"x": 320, "y": 120},
        "data": {
            "kind": "http_request",
            "contractVersion": 2,
            "method": "GET",
            "url": "https://example.test/status",
            "queryItems": [],
            "headerItems": [],
            "bodyMode": "none",
            "formFields": [],
            "authType": "none",
            "timeoutSeconds": 30,
            "redirectLimit": 0,
            "responseLimitBytes": 1_048_576,
            "responseMode": "auto",
            "statusPolicy": "success_only",
            "outputVariable": "http_response",
            "failureAction": "stop",
            "retryMode": "transient",
            "maxAttempts": 2,
        },
    }
    workflow_payload = draft.workflow.model_dump(mode="json")
    workflow_payload["nodes"] = [
        nodes["input-1"].model_dump(mode="json"),
        proposal,
        request,
        agent.model_dump(mode="json"),
        nodes["output-1"].model_dump(mode="json"),
    ]
    workflow_payload["edges"] = [
        {"id": "input-proposal", "source": "input-1", "target": "proposal-1"},
        {"id": "proposal-http", "source": "proposal-1", "target": "http-retry-1"},
        {"id": "http-agent", "source": "http-retry-1", "target": "workflow-agent-1"},
        {"id": "agent-output", "source": "workflow-agent-1", "target": "output-1"},
    ]
    draft.workflow = NativeWorkflowDefinition.model_validate(workflow_payload)
    updated = store.update_xpert(
        created.id,
        {"draft": draft.model_dump(mode="json")},
    )
    version = store.publish_xpert(
        created.id,
        expected_revision=updated.draft_revision,
    )
    return created.id, version.version, source_variable, compatibility_alias


def test_checkpoint_safe_runtime_metadata_redacts_only_sensitive_xpert_alias_group() -> None:
    metadata = {
        "xpert_id": "xpert-1",
        "xpert_input_variable": "custom_input",
        "xpert_history_variable": "custom_history",
        "memory_reply": {"memory_id": "memory-1", "answer": "safe memory"},
        "conversation_title": "Existing title",
        "conversation_messages": [
            {"role": "user", "content": "existing history"}
        ],
    }

    input_safe = main_module.checkpoint_safe_workflow_runtime_metadata(
        metadata,
        sensitive_variable_names={"custom_input", "user_input"},
        trusted_xpert_context=True,
    )
    assert input_safe["memory_reply"] is None
    assert input_safe["conversation_title"] == "Existing title"
    assert input_safe["conversation_messages"] == metadata["conversation_messages"]

    history_safe = main_module.checkpoint_safe_workflow_runtime_metadata(
        metadata,
        sensitive_variable_names={"custom_history", "conversation_history"},
        trusted_xpert_context=True,
    )
    assert history_safe["memory_reply"] == metadata["memory_reply"]
    assert history_safe["conversation_title"] is None
    assert history_safe["conversation_messages"] == []

    classic_safe = main_module.checkpoint_safe_workflow_runtime_metadata(
        metadata,
        sensitive_variable_names={"user_input", "conversation_history"},
        trusted_xpert_context=False,
    )
    assert classic_safe == metadata
    assert metadata["memory_reply"]["answer"] == "safe memory"
    assert metadata["conversation_title"] == "Existing title"


@pytest.mark.asyncio
async def test_published_xpert_custom_history_survives_http_retry_resume(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    xpert_store = XpertStore(tmp_path / "xperts")
    context_store = XpertContextStore(tmp_path / "xpert-runtime")
    execution_store = WorkflowExecutionStore(tmp_path / "workflow-executions")
    set_xpert_store_for_tests(xpert_store)
    set_xpert_context_store_for_tests(context_store)
    monkeypatch.setattr(main_module, "xpert_context_store", context_store)
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    run_registry = RunRegistry()
    monkeypatch.setattr(main_module, "run_registry", run_registry)
    monkeypatch.setenv("WORKFLOW_HTTP_REQUESTS_ENABLED", "true")
    monkeypatch.setenv("WORKFLOW_NODE_RETRIES_ENABLED", "true")
    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    main_module.request_windows.clear()

    observed_history: list[str] = []
    clock = {"now": 1_000.0}

    async def flaky_http(
        _config: dict[str, Any],
        variables: dict[str, Any],
        *_args: Any,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        observed_history.append(str(variables["chat_history"]))
        if len(observed_history) == 1:
            raise WorkflowHttpRequestError(
                "HTTP_TIMEOUT",
                "PRIVATE_XPERT_HISTORY_TIMEOUT",
            )
        return {
            "statusCode": 200,
            "ok": True,
            "contentType": "application/json",
            "headers": {},
            "receivedBytes": 2,
            "body": {"marker": "recovered"},
        }

    async def fake_agent_stream(
        _model_id: str,
        _prompt: str,
        *,
        system_prompt: str | None = None,
    ):
        del system_prompt
        yield "recovered xpert answer"

    monkeypatch.setattr(main_module.time, "time", lambda: clock["now"])
    monkeypatch.setattr(main_module, "execute_workflow_http_request", flaky_http)
    monkeypatch.setattr(main_module, "stream_workflow_llm_text", fake_agent_stream)

    try:
        xpert_id, version = _published_xpert_with_history_bound_retry(xpert_store)
        messages = [
            {"role": "user", "content": "previous user turn"},
            {"role": "assistant", "content": "previous assistant turn"},
        ]
        expected_history = json.dumps(messages, ensure_ascii=False)
        prepared = await main_module.prepare_published_xpert_run(
            xpert_id,
            XpertRunRequest(
                message="current request",
                messages=messages,
                version=version,
            ),
        )

        assert prepared.runtime_metadata["xpert_history_variable"] == "chat_history"
        assert prepared.request.inputs["chat_history"] == expected_history

        response = await main_module._run_workflow_response(
            prepared.request,
            None,
            runtime_run_type="xpert",
            runtime_source_id=xpert_id,
            runtime_metadata=prepared.runtime_metadata,
            runtime_execution_source_kind="xpert_chat",
        )

        # The trusted published-Xpert history input must pass runtime validation;
        # the old regression returned a JSON 400 before making the first call.
        assert isinstance(response, StreamingResponse), getattr(
            response, "status_code", None
        )
        assert response.status_code == 200
        pending = await main_module.consume_workflow_stream(response)
        assert pending["event"] == "node_retry_scheduled"
        task_id = str(pending["task_id"])
        waiting = execution_store.require(task_id)
        assert waiting.status == "waiting"
        assert waiting.wait_kind == "node_retry"
        assert waiting.runtime_metadata["xpert_history_variable"] == "chat_history"

        original_run_id = waiting.run_id
        recovery_registry = RunRegistry()
        monkeypatch.setattr(main_module, "run_registry", recovery_registry)
        clock["now"] = float(waiting.resume_at or 0.0)
        completed = await main_module.resume_runtime_due_execution(task_id)

        assert completed["event"] == "workflow_end"
        assert completed["final_output"] == "recovered xpert answer"
        assert observed_history == [expected_history, expected_history]
        persisted = execution_store.require(task_id)
        assert persisted.status == "completed"
        recovery_run = await recovery_registry.get_run(persisted.run_id)
        assert recovery_run is not None
        assert recovery_run.metadata["recovery_run_from"] == original_run_id
        recovery_checkpoints = await recovery_registry.list_checkpoints(
            persisted.run_id
        )
        assert any(
            checkpoint.metadata.get("recovery_run_from") == original_run_id
            for checkpoint in recovery_checkpoints
        )
        assert sum(
            event.get("event") == "node_retry_scheduled"
            for event in persisted.events
        ) == 1
        assert sum(
            event.get("event") == "workflow_end" for event in persisted.events
        ) == 1
    finally:
        set_xpert_context_store_for_tests(None)
        set_xpert_store_for_tests(None)
        main_module.request_windows.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_kind", "proposal_binding"),
    [
        ("history", "custom"),
        ("history", "compatibility"),
        ("input", "custom"),
        ("input", "compatibility"),
    ],
)
async def test_sensitive_xpert_source_aliases_are_redacted_across_retry_resume(
    source_kind: str,
    proposal_binding: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    xpert_store = XpertStore(tmp_path / "xperts")
    context_store = XpertContextStore(tmp_path / "xpert-runtime")
    execution_store = WorkflowExecutionStore(tmp_path / "workflow-executions")
    rag_storage = tmp_path / "rag-storage"
    rag_service = RagService(
        storage_dir=rag_storage,
        uploads_dir=tmp_path / "rag-uploads",
        embedder=EmbeddingClient(api_key="", dimension=32),
        vector_store=LocalJsonVectorStore(rag_storage / "vectors.json"),
        llm_enabled=False,
    )
    knowledge_base = rag_service.create_knowledge_base("Sensitive source test")
    set_xpert_store_for_tests(xpert_store)
    set_xpert_context_store_for_tests(context_store)
    set_rag_service_for_tests(rag_service)
    monkeypatch.setattr(main_module, "xpert_context_store", context_store)
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    run_registry = RunRegistry()
    monkeypatch.setattr(main_module, "run_registry", run_registry)
    monkeypatch.setenv("WORKFLOW_HTTP_REQUESTS_ENABLED", "true")
    monkeypatch.setenv("WORKFLOW_NODE_RETRIES_ENABLED", "true")
    monkeypatch.setenv("WORKFLOW_KNOWLEDGE_PROPOSALS_ENABLED", "true")
    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    main_module.request_windows.clear()

    calls = 0
    clock = {"now": 1_000.0}

    async def flaky_http(
        _config: dict[str, Any],
        _variables: dict[str, Any],
        *_args: Any,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise WorkflowHttpRequestError("HTTP_TIMEOUT", "PRIVATE_SENTINEL")
        return {
            "statusCode": 200,
            "ok": True,
            "contentType": "application/json",
            "headers": {},
            "receivedBytes": 2,
            "body": {"marker": "recovered"},
        }

    async def fake_agent_stream(
        _model_id: str,
        _prompt: str,
        *,
        system_prompt: str | None = None,
    ):
        del system_prompt
        yield "completed"

    monkeypatch.setattr(main_module.time, "time", lambda: clock["now"])
    monkeypatch.setattr(main_module, "execute_workflow_http_request", flaky_http)
    monkeypatch.setattr(main_module, "stream_workflow_llm_text", fake_agent_stream)

    try:
        xpert_id, version, source_variable, compatibility_alias = (
            _published_xpert_with_sensitive_proposal_before_retry(
                xpert_store,
                knowledge_base_id=knowledge_base["id"],
                source_kind=source_kind,
                proposal_binding=proposal_binding,
            )
        )
        sentinel = (
            f"MM_PRIVATE_{source_kind.upper()}_"
            f"{proposal_binding.upper()}_SENTINEL"
        )
        prepared = await main_module.prepare_published_xpert_run(
            xpert_id,
            XpertRunRequest(
                message=(sentinel if source_kind == "input" else "current request"),
                messages=(
                    [{"role": "user", "content": sentinel}]
                    if source_kind == "history"
                    else []
                ),
                version=version,
            ),
        )
        assert prepared.runtime_metadata["xpert_input_variable"] == (
            source_variable if source_kind == "input" else "user_input"
        )

        response = await main_module._run_workflow_response(
            prepared.request,
            None,
            runtime_run_type="xpert",
            runtime_source_id=xpert_id,
            runtime_metadata=prepared.runtime_metadata,
            runtime_execution_source_kind="xpert_chat",
        )
        assert isinstance(response, StreamingResponse)
        pending = await main_module.consume_workflow_stream(response)
        assert pending["event"] == "node_retry_scheduled"
        waiting = execution_store.require(str(pending["task_id"]))
        assert waiting.status == "waiting"
        assert waiting.wait_kind == "node_retry"
        for protected_name in (source_variable, compatibility_alias):
            protected = waiting.inputs[protected_name]
            assert isinstance(protected, dict)
            assert protected["body_unavailable_after_resume"] is True
        assert sentinel not in json.dumps(waiting.inputs, ensure_ascii=False)
        assert sentinel not in json.dumps(asdict(waiting), ensure_ascii=False)
        assert sentinel not in execution_store.snapshot_path.read_text(
            encoding="utf-8"
        )
        runtime_run = await run_registry.get_run(waiting.run_id)
        assert runtime_run is not None
        checkpoints = await run_registry.list_checkpoints(waiting.run_id)
        assert sentinel not in json.dumps(
            {
                "run": asdict(runtime_run),
                "checkpoints": [asdict(item) for item in checkpoints],
            },
            ensure_ascii=False,
        )
        if source_kind == "history":
            assert waiting.runtime_metadata["conversation_messages"] == []
            assert waiting.runtime_metadata["conversation_title"] is None
        else:
            assert waiting.runtime_metadata["memory_reply"] is None

        inbox = rag_service.list_knowledge_write_proposals(
            kb_id=knowledge_base["id"]
        )
        assert len(inbox) == 1
        assert sentinel in inbox[0]["content"]
        clock["now"] = float(waiting.resume_at or 0.0)
        completed = await main_module.resume_runtime_due_execution(waiting.task_id)

        assert completed["event"] == "workflow_end"
        assert completed["final_output"] == "completed"
        assert calls == 2
        assert len(
            rag_service.list_knowledge_write_proposals(
                kb_id=knowledge_base["id"]
            )
        ) == 1
    finally:
        set_rag_service_for_tests(None)
        set_xpert_context_store_for_tests(None)
        set_xpert_store_for_tests(None)
        main_module.request_windows.clear()


@pytest.mark.asyncio
async def test_sensitive_xpert_history_alias_is_redacted_in_regular_wait_continuation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    xpert_store = XpertStore(tmp_path / "xperts")
    context_store = XpertContextStore(tmp_path / "xpert-runtime")
    execution_store = WorkflowExecutionStore(tmp_path / "workflow-executions")
    approval_store = RuntimeApprovalStore(tmp_path / "approvals")
    rag_storage = tmp_path / "rag-storage"
    rag_service = RagService(
        storage_dir=rag_storage,
        uploads_dir=tmp_path / "rag-uploads",
        embedder=EmbeddingClient(api_key="", dimension=32),
        vector_store=LocalJsonVectorStore(rag_storage / "vectors.json"),
        llm_enabled=False,
    )
    knowledge_base = rag_service.create_knowledge_base("Regular wait alias test")
    set_xpert_store_for_tests(xpert_store)
    set_xpert_context_store_for_tests(context_store)
    set_rag_service_for_tests(rag_service)
    monkeypatch.setattr(main_module, "xpert_context_store", context_store)
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(main_module, "runtime_approval_store", approval_store)
    run_registry = RunRegistry()
    monkeypatch.setattr(main_module, "run_registry", run_registry)
    monkeypatch.setenv("WORKFLOW_KNOWLEDGE_PROPOSALS_ENABLED", "true")
    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    main_module.request_windows.clear()

    try:
        created = xpert_store.create_xpert(name="Sensitive regular wait")
        draft = created.draft.model_copy(deep=True)
        draft.history_variable = "chat_history"
        nodes = {node.id: node.model_copy(deep=True) for node in draft.workflow.nodes}
        agent = nodes["workflow-agent-1"]
        agent.data["modelId"] = "test/model"
        agent.data["taskInput"] = "Approval: {{approval_result}}"
        workflow_payload = draft.workflow.model_dump(mode="json")
        workflow_payload["nodes"] = [
            nodes["input-1"].model_dump(mode="json"),
            {
                "id": "proposal-1",
                "type": "knowledge_write_proposal",
                "position": {"x": 180, "y": 120},
                "data": {
                    "kind": "knowledge_write_proposal",
                    "contractVersion": 1,
                    "knowledgeBaseId": knowledge_base["id"],
                    "titleTemplate": "Sensitive history proposal",
                    # Exercise the reverse direction: the proposal binds the
                    # fixed compatibility alias while the Xpert declares a
                    # custom history variable.
                    "contentVariable": "conversation_history",
                    "tags": [],
                    "outputVariable": "proposal_receipt",
                },
            },
            {
                "id": "approval-1",
                "type": "human_intervention",
                "position": {"x": 320, "y": 120},
                "data": {
                    "kind": "human_intervention",
                    "contractVersion": 2,
                    "interactionMode": "approval",
                    "prompt": "Approve the generated proposal receipt.",
                    "outputVariable": "approval_result",
                    "timeoutSeconds": 3600,
                },
            },
            agent.model_dump(mode="json"),
            nodes["output-1"].model_dump(mode="json"),
        ]
        workflow_payload["edges"] = [
            {"id": "input-proposal", "source": "input-1", "target": "proposal-1"},
            {"id": "proposal-approval", "source": "proposal-1", "target": "approval-1"},
            {"id": "approval-agent", "source": "approval-1", "target": "workflow-agent-1"},
            {"id": "agent-output", "source": "workflow-agent-1", "target": "output-1"},
        ]
        draft.workflow = NativeWorkflowDefinition.model_validate(workflow_payload)
        updated = xpert_store.update_xpert(
            created.id,
            {"draft": draft.model_dump(mode="json")},
        )
        version = xpert_store.publish_xpert(
            created.id,
            expected_revision=updated.draft_revision,
        )
        sentinel = "MM_PRIVATE_REGULAR_WAIT_HISTORY"
        prepared = await main_module.prepare_published_xpert_run(
            created.id,
            XpertRunRequest(
                message="current request",
                messages=[{"role": "user", "content": sentinel}],
                version=version.version,
            ),
        )
        response = await main_module._run_workflow_response(
            prepared.request,
            None,
            runtime_run_type="xpert",
            runtime_source_id=created.id,
            runtime_metadata=prepared.runtime_metadata,
            runtime_execution_source_kind="xpert_chat",
        )
        assert isinstance(response, StreamingResponse)
        pending = await main_module.consume_workflow_stream(response)
        assert pending["event"] == "runtime_approval_pending"
        waiting = execution_store.require(str(pending["task_id"]))
        for persisted in (waiting.inputs, waiting.continuation["variables"]):
            for protected_name in ("chat_history", "conversation_history"):
                protected = persisted[protected_name]
                assert isinstance(protected, dict)
                assert protected["body_unavailable_after_resume"] is True
            assert sentinel not in json.dumps(persisted, ensure_ascii=False)
        assert sentinel not in json.dumps(asdict(waiting), ensure_ascii=False)
        assert sentinel not in execution_store.snapshot_path.read_text(
            encoding="utf-8"
        )
        runtime_run = await run_registry.get_run(waiting.run_id)
        assert runtime_run is not None
        checkpoints = await run_registry.list_checkpoints(waiting.run_id)
        assert sentinel not in json.dumps(
            {
                "run": asdict(runtime_run),
                "checkpoints": [asdict(item) for item in checkpoints],
            },
            ensure_ascii=False,
        )
    finally:
        set_rag_service_for_tests(None)
        set_xpert_context_store_for_tests(None)
        set_xpert_store_for_tests(None)
        main_module.request_windows.clear()
