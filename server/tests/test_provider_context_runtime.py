"""Entry-to-SQLite provenance with real runtime and mocked Provider transport."""
import json

import httpx
import pytest

import server.main as main
from server.model_router.call_context_storage import ProviderContextStore
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.service import ModelRouterService
from server.model_router.workflow_gateway import ManagedWorkflowGateway
from server.tests.test_workflow_managed_gateway import _activate, _qualified_router, MODEL_ID
from server.tests.test_workflow_managed_runtime import _events, _workflow
from server.xpert_runtime.execution_store import WorkflowExecutionStore
from server.xpert_runtime.run_registry import RunRegistry


@pytest.mark.asyncio
@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("source,entry,flag,run_type", [
    ("workflow_classic", "workflow_interactive_llm", "WORKFLOW_LLM", "workflow"),
    ("workflow_deployment", "workflow_deployment_llm", "WORKFLOW_DEPLOYMENT", "workflow"),
    ("xpert_chat", "xpert", "XPERT", "xpert"),
    ("xpert_app", "xpert_app", "XPERT_APP", "xpert_app"),
])
async def test_runtime_persists_selected_entry_without_public_actor_leak(
    tmp_path, monkeypatch, source, entry, flag, run_type, resume,
):
    service, connection = _qualified_router(tmp_path / "router")
    monkeypatch.setenv(f"MODEL_CONTROL_{flag}_ENABLED", "true")
    monkeypatch.setenv("MODEL_CONTROL_WORKFLOW_LLM_ENABLED", "true")
    _activate(service, connection, entry, shapes=("chat_text",))
    posts = []

    def handler(request):
        posts.append(request.method)
        frames = [
            {"model": MODEL_ID, "choices": [{"delta": {"content": "synthetic reply"}}]},
            {"model": MODEL_ID, "choices": [{"delta": {}, "finish_reason": "stop"}]},
        ]
        content = "".join("data: " + json.dumps(item) + "\n\n" for item in frames) + "data: [DONE]\n\n"
        return httpx.Response(200, text=content, headers={"content-type": "text/event-stream"})

    class RuntimeGateway(ManagedWorkflowGateway):
        @classmethod
        def for_router(cls, router_service):
            return super().for_router(router_service, client_factory=lambda: httpx.AsyncClient(
                transport=httpx.MockTransport(handler), follow_redirects=False, trust_env=False))

    monkeypatch.setattr(main, "ManagedWorkflowGateway", RuntimeGateway)
    monkeypatch.setattr(main, "get_model_router_service", lambda: service)
    monkeypatch.setattr(main, "workflow_execution_store", WorkflowExecutionStore(tmp_path / "executions"))
    monkeypatch.setattr(main, "workflow_task_store", {})
    monkeypatch.setattr(main, "run_registry", RunRegistry())
    monkeypatch.setattr(main, "get_llm_gateway_config", lambda: ("", ""))

    def no_legacy(*args, **kwargs):
        raise AssertionError("managed runtime must not call legacy")

    monkeypatch.setattr(main, "stream_workflow_llm_text", no_legacy)
    store = ProviderContextStore(service.repository)
    parent = None
    if source in {"xpert_chat", "xpert_app"}:
        parent = "parent"
        store.register_execution(execution_id=parent, task_id="parent-task", source_kind=source)
    try:
        payload = _workflow([
                {"id": "input", "type": "input", "data": {"kind": "input"}},
                {"id": "llm", "type": "llm", "data": {"kind": "llm", "modelId": MODEL_ID,
                    "prompt": "{{user_input}}", "outputVariable": "answer"}},
            ], [{"id": "edge", "source": "input", "target": "llm"}])
        metadata = {"workflow_deployment_execution_id": "deployment-id"} if source == "workflow_deployment" else {}
        resumed_execution = None
        if resume:
            store.register_execution(execution_id="before-restart", task_id="synthetic-task", source_kind=source,
                parent_execution_id=parent, execution_reference="deployment-id" if source == "workflow_deployment" else "synthetic-task")
            main.workflow_execution_store.create(task_id="synthetic-task", run_id="before-restart", run_type=run_type,
                source_kind=source, workflow=payload.workflow.model_dump(), inputs=dict(payload.inputs), runtime_metadata=metadata)
            main.workflow_execution_store.suspend("synthetic-task", wait_id="synthetic-approval",
                continuation={"queue": ["llm"], "queued": ["llm"], "executed": ["input"], "variables": dict(payload.inputs)})
            egress = service.egress_policy
            service.repository.close()
            service = ModelRouterService(SQLiteRouterRepository.open(tmp_path / "router", master_key=b"x" * 32), egress_policy=egress)
            store = ProviderContextStore(service.repository)
            monkeypatch.setattr(main, "workflow_execution_store", WorkflowExecutionStore(tmp_path / "executions"))
            main.workflow_execution_store.mark_ready("synthetic-task", wait_id="synthetic-approval")
            resumed_execution = main.workflow_execution_store.claim("synthetic-task", worker_id="synthetic-worker")
        response = await main._run_workflow_response(
            payload, None, runtime_execution_source_kind=source, runtime_run_type=run_type,
            runtime_task_id="synthetic-task", runtime_provider_parent_execution_id=parent,
            runtime_metadata=metadata, resume_execution=resumed_execution,
        )
        events = await _events(response)
        assert posts == ["POST"]
        receipt = next(item["provider_route_receipts"] for item in events
                       if item.get("event") == "node_end" and item.get("node_id") == "llm")
        assert receipt["entry_id"] == entry
        assert receipt["call_count"] == 1
        calls = service.repository.list_workload_receipts("local")["calls"]
        context = store.read("local", calls[0]["id"])
        assert context.entry_id == entry
        assert context.actor_kind == ("service" if source == "workflow_deployment" else "local_anonymous")
        with service.repository._connect() as db:
            origin = dict(db.execute("SELECT origin.* FROM provider_execution_origins origin "
                "JOIN provider_workload_run_origins run ON run.tenant_id=origin.tenant_id AND run.execution_id=origin.execution_id "
                "WHERE run.tenant_id='local' AND run.run_id=?", (context.parent_run_id,)).fetchone())
        assert origin["parent_execution_id"] == parent
        if resume:
            assert origin["execution_id"] != "before-restart"
            assert "before-restart" in main.workflow_execution_store.require("synthetic-task").previous_run_ids
        assert origin["execution_reference"] == ("deployment-id" if source == "workflow_deployment" else "synthetic-task")
        assert "actor_reference" not in json.dumps(events)
        duplicate = await main._run_workflow_response(
            payload, None, runtime_execution_source_kind=source, runtime_run_type=run_type,
            runtime_task_id="synthetic-task", runtime_provider_parent_execution_id=parent,
        )
        assert duplicate.status_code == 409
        assert json.loads(duplicate.body)["code"] == "provider_call_context_execution_replay_blocked"
        assert posts == ["POST"]
    finally:
        service.repository.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("attack", ["missing", "wrong_source", "wrong_task", "downgrade"])
async def test_runtime_resume_rejects_missing_or_changed_origin(tmp_path, monkeypatch, attack):
    with SQLiteRouterRepository.open(tmp_path / "router", master_key=b"x" * 32) as repository:
        service = ModelRouterService(repository)
        monkeypatch.setattr(main, "get_model_router_service", lambda: service)
        monkeypatch.setattr(main, "workflow_execution_store", WorkflowExecutionStore(tmp_path / "executions"))
        if attack != "missing":
            ProviderContextStore(repository).register_execution(execution_id="before", task_id="task", source_kind="xpert_chat")
        payload = _workflow([{"id": "input", "type": "input", "data": {"kind": "input"}}], [])
        source = "xpert_app" if attack == "wrong_source" else (None if attack == "downgrade" else "xpert_chat")
        task_id = "wrong" if attack == "wrong_task" else "task"
        main.workflow_execution_store.create(task_id=task_id,
            run_id="before", run_type="xpert", source_kind=source,
            workflow=payload.workflow.model_dump(), inputs=dict(payload.inputs))
        main.workflow_execution_store.suspend(task_id, wait_id="synthetic-approval", continuation={})
        main.workflow_execution_store.mark_ready(task_id, wait_id="synthetic-approval")
        snapshot = main.workflow_execution_store.claim(task_id, worker_id="synthetic-worker")
        response = await main._run_workflow_response(payload, None, runtime_run_type="xpert", resume_execution=snapshot)
        assert response.status_code == 409
        code = json.loads(response.body)["code"]
        assert code == ("provider_call_context_origin_missing" if attack == "missing" else "provider_call_context_reference_mismatch")
        assert repository.list_workload_receipts("local")["calls"] == []
