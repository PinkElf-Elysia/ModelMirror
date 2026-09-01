from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import HTTPException

import server.api.workflow_deployments as deployment_api
import server.main as main_module
from server.workflow_deployments import WorkflowDeploymentStore
from server.xpert_runtime.execution_store import WorkflowExecutionStore


def http_workflow(entry_data: dict | None = None) -> dict:
    return {
        "id": "draft",
        "title": "hook",
        "nodes": [
            {
                "id": "entry",
                "type": "http_event_entry",
                "data": {
                    "kind": "http_event_entry",
                    "eventVariable": "http_event",
                    **(entry_data or {}),
                },
            },
            {
                "id": "reply",
                "type": "http_event_reply",
                "data": {
                    "kind": "http_event_reply",
                    "statusCode": 201,
                    "responseBodyType": "json",
                    "bodyTemplate": '{"accepted":true}',
                },
            },
        ],
        "edges": [{"id": "e1", "source": "entry", "target": "reply"}],
    }


def http_wait_workflow() -> dict:
    return {
        "id": "draft",
        "title": "private hook timer",
        "nodes": [
            {
                "id": "entry",
                "type": "http_event_entry",
                "data": {
                    "kind": "http_event_entry",
                    "eventVariable": "http_event",
                    "bodyVariable": "request_body",
                    "acceptedContentType": "json",
                    "maxBodyBytes": 65_536,
                },
            },
            {
                "id": "wait",
                "type": "suspend_wait",
                "data": {
                    "kind": "suspend_wait",
                    "waitMode": "duration",
                    "durationSeconds": 1,
                    "outputVariable": "resume_event",
                },
            },
            {
                "id": "output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "resume_event"},
            },
        ],
        "edges": [
            {"id": "e1", "source": "entry", "target": "wait"},
            {"id": "e2", "source": "wait", "target": "output"},
        ],
    }


def schedule_workflow() -> dict:
    return {
        "id": "draft",
        "title": "scheduled source",
        "nodes": [
            {
                "id": "entry",
                "type": "scheduled_start",
                "data": {
                    "kind": "scheduled_start",
                    "scheduleType": "interval",
                    "intervalSeconds": 30,
                    "timezone": "UTC",
                    "eventVariable": "schedule_event",
                },
            },
            {
                "id": "output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "schedule_event"},
            },
        ],
        "edges": [{"id": "e1", "source": "entry", "target": "output"}],
    }


def failure_workflow(source_project_id: str) -> dict:
    return {
        "id": "draft",
        "title": "failure handler",
        "nodes": [
            {
                "id": "entry",
                "type": "failure_event_entry",
                "data": {
                    "kind": "failure_event_entry",
                    "sourceProjectIds": [source_project_id],
                    "eventVariable": "failure_event",
                },
            },
            {
                "id": "output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "failure_event"},
            },
        ],
        "edges": [{"id": "e1", "source": "entry", "target": "output"}],
    }


@pytest.mark.asyncio
async def test_workflow_project_revision_publish_and_private_hook(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    deployment_store = WorkflowDeploymentStore(tmp_path / "deployments")
    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(deployment_api, "_store", deployment_store)
    monkeypatch.setattr(
        deployment_api,
        "_trigger_executor",
        main_module.run_deployed_workflow_trigger,
    )
    monkeypatch.setattr(main_module, "workflow_deployment_store", deployment_store)
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setenv("WORKFLOW_WEBHOOKS_ENABLED", "true")
    deployment_api._rate_windows.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        created = await client.post("/api/workflows", json={"workflow": http_workflow()})
        assert created.status_code == 201, created.text
        project_id = created.json()["project_id"]

        conflict = await client.put(
            f"/api/workflows/{project_id}/draft",
            json={"expected_revision": 99, "workflow": http_workflow()},
        )
        assert conflict.status_code == 409

        published = await client.post(f"/api/workflows/{project_id}/publish")
        assert published.status_code == 201, published.text
        version = published.json()["version"]
        activated = await client.post(
            f"/api/workflows/{project_id}/versions/{version}/activate"
        )
        assert activated.status_code == 200, activated.text
        activation = activated.json()
        plaintext_key = activation["webhook_key"]
        hook_id = activation["hook_id"]

        wrong_key = await client.post(
            f"/api/workflow-hooks/{hook_id}",
            content=b'{"private":"value-never-persisted"}',
            headers={
                "Content-Type": "application/json",
                "X-ModelMirror-Webhook-Key": "wrong",
                "Idempotency-Key": "request-one",
            },
        )
        assert wrong_key.status_code == 404

        headers = {
            "Content-Type": "application/json",
            "X-ModelMirror-Webhook-Key": plaintext_key,
            "Idempotency-Key": "request-one",
        }
        first = await client.post(
            f"/api/workflow-hooks/{hook_id}",
            content=b'{"private":"value-never-persisted"}',
            headers=headers,
        )
        duplicate = await client.post(
            f"/api/workflow-hooks/{hook_id}",
            content=b'{"private":"value-never-persisted"}',
            headers=headers,
        )
        assert first.status_code == 201, first.text
        assert first.json() == {"accepted": True}
        assert duplicate.status_code == 201

        rotated = await client.post(
            f"/api/workflows/{project_id}/versions/{version}/rotate-webhook-key"
        )
        assert rotated.status_code == 200
        rotated_key = rotated.json()["webhook_key"]
        assert rotated_key != plaintext_key
        old_key = await client.post(
            f"/api/workflow-hooks/{hook_id}",
            content=b"{}",
            headers={
                "Content-Type": "application/json",
                "X-ModelMirror-Webhook-Key": plaintext_key,
                "Idempotency-Key": "after-rotation",
            },
        )
        assert old_key.status_code == 404
        oversized = await client.post(
            f"/api/workflow-hooks/{hook_id}",
            content=b"{}",
            headers={
                "Content-Type": "application/json",
                "Content-Length": "1048577",
                "X-ModelMirror-Webhook-Key": rotated_key,
                "Idempotency-Key": "too-large",
            },
        )
        assert oversized.status_code == 413

        async def oversized_chunks():
            yield b"x" * 600_000
            yield b"x" * 600_000

        chunked_oversized = await client.post(
            f"/api/workflow-hooks/{hook_id}",
            content=oversized_chunks(),
            headers={
                "Content-Type": "text/plain",
                "X-ModelMirror-Webhook-Key": rotated_key,
                "Idempotency-Key": "chunked-too-large",
            },
        )
        assert chunked_oversized.status_code == 413

        executions = await client.get(f"/api/workflows/{project_id}/executions")
        assert executions.status_code == 200
        assert len(executions.json()["items"]) == 1

    deployment_snapshot = deployment_store.snapshot_path.read_text(encoding="utf-8")
    execution_snapshot = execution_store.snapshot_path.read_text(encoding="utf-8")
    assert plaintext_key not in deployment_snapshot
    assert "request-one" not in deployment_snapshot
    assert "value-never-persisted" not in deployment_snapshot
    assert "value-never-persisted" not in execution_snapshot


@pytest.mark.asyncio
async def test_webhook_disabled_and_body_limit_return_safe_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        deployment_api,
        "_store",
        WorkflowDeploymentStore(tmp_path / "deployments"),
    )
    monkeypatch.setenv("WORKFLOW_WEBHOOKS_ENABLED", "false")
    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        disabled = await client.post(
            "/api/workflow-hooks/unknown",
            content=b"{}",
            headers={"Content-Type": "application/json"},
        )
    assert disabled.status_code == 404


@pytest.mark.asyncio
async def test_failure_activation_list_api_and_safe_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    deployment_store = WorkflowDeploymentStore(tmp_path / "deployments")
    monkeypatch.setattr(deployment_api, "_store", deployment_store)
    monkeypatch.setenv("WORKFLOW_FAILURE_TRIGGERS_ENABLED", "false")

    source = deployment_store.create_project(schedule_workflow())
    source_release = deployment_store.publish(source.project_id)
    deployment_store.activate(
        source.project_id,
        source_release.version,
        webhooks_enabled=False,
        now=100,
    )
    handler = deployment_store.create_project(failure_workflow(source.project_id))
    handler_release = deployment_store.publish(handler.project_id)

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        projects = await client.get("/api/workflows?limit=1&offset=0")
        assert projects.status_code == 200, projects.text
        assert projects.json()["total"] == 2
        assert len(projects.json()["items"]) == 1
        assert set(projects.json()["items"][0]) == {
            "project_id",
            "title",
            "active_version",
            "active_trigger_kind",
            "updated_at",
        }

        active_schedules = await client.get(
            "/api/workflows?active_only=true&trigger_kind=schedule"
        )
        assert active_schedules.status_code == 200
        assert [item["project_id"] for item in active_schedules.json()["items"]] == [
            source.project_id
        ]

        disabled = await client.post(
            f"/api/workflows/{handler.project_id}/versions/{handler_release.version}/activate"
        )
        assert disabled.status_code == 409
        monkeypatch.setenv("WORKFLOW_FAILURE_TRIGGERS_ENABLED", "true")
        activated = await client.post(
            f"/api/workflows/{handler.project_id}/versions/{handler_release.version}/activate"
        )
        assert activated.status_code == 200, activated.text
        assert activated.json()["trigger_kind"] == "failure"

    source_execution = deployment_store.materialize_due_schedules(now=130)[0]
    deployment_store.fail_execution(
        source_execution.execution_id,
        error="Authorization: bearer-secret\nRuntimeError: safe failure",
    )
    pending = deployment_store.list_executions(handler.project_id)[0]
    captured_events: list[dict] = []

    async def execute_failure(execution, release, event):
        captured_events.append(event)
        return {
            "status": "completed",
            "task_id": "task-handler",
            "run_id": "run-handler",
            "result": "handled",
        }

    monkeypatch.setattr(deployment_api, "_trigger_executor", execute_failure)
    monkeypatch.setenv("WORKFLOW_FAILURE_TRIGGERS_ENABLED", "false")
    await deployment_api.WorkflowTriggerCoordinator().run_once()
    await asyncio.sleep(0)
    assert deployment_store.get_execution(pending.execution_id).status == "pending"
    assert all(event.get("type") != "workflow_failure" for event in captured_events)
    captured_events.clear()

    monkeypatch.setenv("WORKFLOW_FAILURE_TRIGGERS_ENABLED", "true")
    completed = await deployment_api._execute_trigger(
        pending,
        {"type": "workflow_failure", **pending.trigger_summary},
    )
    assert completed.status == "completed"
    assert captured_events == [
        {"type": "workflow_failure", **pending.trigger_summary}
    ]
    assert "Authorization" not in json.dumps(captured_events)
    serialized = deployment_store.serialize_execution(pending)
    assert serialized["source_execution_id"] == source_execution.execution_id
    assert serialized["test_mode"] is False


@pytest.mark.asyncio
async def test_trigger_executor_renews_lease_while_running(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    deployment_store = WorkflowDeploymentStore(tmp_path / "deployments")
    monkeypatch.setattr(deployment_api, "_store", deployment_store)
    monkeypatch.setattr(deployment_api, "WORKFLOW_TRIGGER_LEASE_SECONDS", 5.0)
    monkeypatch.setattr(deployment_api, "WORKFLOW_TRIGGER_HEARTBEAT_SECONDS", 0.01)
    source = deployment_store.create_project(schedule_workflow())
    release = deployment_store.publish(source.project_id)
    deployment_store.activate(
        source.project_id,
        release.version,
        webhooks_enabled=False,
        now=100,
    )
    pending = deployment_store.materialize_due_schedules(now=130)[0]
    renewed = asyncio.Event()
    original_renew = deployment_store.renew_execution_lease

    def observe_renewal(*args, **kwargs):
        result = original_renew(*args, **kwargs)
        renewed.set()
        return result

    monkeypatch.setattr(deployment_store, "renew_execution_lease", observe_renewal)

    async def execute_after_renewal(execution, published, event):
        await asyncio.wait_for(renewed.wait(), timeout=1)
        return {"status": "completed", "result": "renewed"}

    monkeypatch.setattr(deployment_api, "_trigger_executor", execute_after_renewal)
    completed = await deployment_api._execute_trigger(
        pending,
        {"type": "schedule_event"},
    )
    assert renewed.is_set()
    assert completed.status == "completed"


@pytest.mark.asyncio
async def test_failure_dispatch_preserves_determinable_failed_node(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    deployment_store = WorkflowDeploymentStore(tmp_path / "deployments")
    monkeypatch.setattr(deployment_api, "_store", deployment_store)
    monkeypatch.setenv("WORKFLOW_FAILURE_TRIGGERS_ENABLED", "true")
    source = deployment_store.create_project(schedule_workflow())
    source_release = deployment_store.publish(source.project_id)
    deployment_store.activate(
        source.project_id,
        source_release.version,
        webhooks_enabled=False,
        now=100,
    )
    handler = deployment_store.create_project(failure_workflow(source.project_id))
    handler_release = deployment_store.publish(handler.project_id)
    deployment_store.activate(
        handler.project_id,
        handler_release.version,
        webhooks_enabled=False,
        failure_triggers_enabled=True,
        now=101,
    )
    pending = deployment_store.materialize_due_schedules(now=130)[0]

    async def fail_at_known_node(execution, published, event):
        raise main_module.WorkflowStreamFailure(
            "provider rejected the request",
            task_id="task-source",
            run_id="run-source",
            failed_node_id="llm-node",
            failed_node_title="生成回复",
        )

    monkeypatch.setattr(deployment_api, "_trigger_executor", fail_at_known_node)
    failed = await deployment_api._execute_trigger(
        pending,
        {"type": "schedule_event"},
    )
    assert failed.status == "failed"
    assert failed.task_id == "task-source"
    assert failed.run_id == "run-source"
    dispatched = deployment_store.list_executions(handler.project_id)[0]
    assert dispatched.trigger_summary["source_task_id"] == "task-source"
    assert dispatched.trigger_summary["source_run_id"] == "run-source"
    assert dispatched.trigger_summary["failed_node_id"] == "llm-node"
    assert dispatched.trigger_summary["failed_node_title"] == "生成回复"


@pytest.mark.asyncio
async def test_workflow_stream_failure_retains_safe_node_identity() -> None:
    async def error_stream():
        yield (
            'data: {"event":"error","task_id":"task-source",'
            '"run_id":"run-source","node_id":"llm-node",'
            '"node_title":"生成回复","terminal":true,'
            '"message":"provider rejected"}\n\n'
        )

    response = main_module.StreamingResponse(error_stream())
    with pytest.raises(main_module.WorkflowStreamFailure) as failure:
        await main_module.consume_workflow_stream(response)
    assert str(failure.value) == "provider rejected"
    assert failure.value.task_id == "task-source"
    assert failure.value.run_id == "run-source"
    assert failure.value.failed_node_id == "llm-node"
    assert failure.value.failed_node_title == "生成回复"


@pytest.mark.asyncio
async def test_runtime_exception_reports_current_failed_node(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        main_module,
        "workflow_execution_store",
        WorkflowExecutionStore(tmp_path / "executions"),
    )
    payload = main_module.WorkflowRunRequest.model_validate(
        {
            "workflow": {
                "id": "known-node-failure",
                "title": "known node failure",
                "nodes": [
                    {
                        "id": "http-entry",
                        "type": "http_event_entry",
                        "data": {
                            "kind": "http_event_entry",
                            "title": "接收请求",
                            "eventVariable": "http_event",
                        },
                    },
                    {
                        "id": "invalid-reply",
                        "type": "http_event_reply",
                        "data": {
                            "kind": "http_event_reply",
                            "title": "故意失败的 JSON 回执",
                            "statusCode": 200,
                            "responseBodyType": "json",
                            "bodyTemplate": '{"ok":true',
                        },
                    },
                ],
                "edges": [
                    {"id": "e1", "source": "http-entry", "target": "invalid-reply"}
                ],
            },
            "inputs": {},
        }
    )
    response = await main_module._run_workflow_response(
        payload,
        None,
        runtime_execution_source_kind="workflow_deployment",
        runtime_trigger_event={"type": "http_event", "body": {}},
    )
    with pytest.raises(main_module.WorkflowStreamFailure) as failure:
        await main_module.consume_workflow_stream(response)
    assert failure.value.task_id
    assert failure.value.run_id
    assert failure.value.failed_node_id == "invalid-reply"
    assert failure.value.failed_node_title == "故意失败的 JSON 回执"


@pytest.mark.asyncio
async def test_http_entry_enforces_configured_content_type_and_body_limit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    deployment_store = WorkflowDeploymentStore(tmp_path / "deployments")
    monkeypatch.setattr(deployment_api, "_store", deployment_store)
    monkeypatch.setenv("WORKFLOW_WEBHOOKS_ENABLED", "true")
    deployment_api._rate_windows.clear()
    workflow = http_workflow(
        {
            "acceptedContentType": "json",
            "maxBodyBytes": 65_536,
            "bodyVariable": "request_body",
        }
    )
    project = deployment_store.create_project(workflow)
    release = deployment_store.publish(project.project_id)
    deployment, plaintext_key = deployment_store.activate(
        project.project_id,
        release.version,
        webhooks_enabled=True,
    )
    assert deployment.hook_id and plaintext_key

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        text_request = await client.post(
            f"/api/workflow-hooks/{deployment.hook_id}",
            content=b"plain text",
            headers={
                "Content-Type": "text/plain",
                "X-ModelMirror-Webhook-Key": plaintext_key,
                "Idempotency-Key": "text-rejected",
            },
        )
        oversized = await client.post(
            f"/api/workflow-hooks/{deployment.hook_id}",
            content=b"{}",
            headers={
                "Content-Type": "application/json",
                "Content-Length": "65537",
                "X-ModelMirror-Webhook-Key": plaintext_key,
                "Idempotency-Key": "configured-limit",
            },
        )

    assert text_request.status_code == 415
    assert oversized.status_code == 413
    assert deployment_store.list_executions(project.project_id) == []


@pytest.mark.asyncio
async def test_private_http_timer_returns_202_and_completes_after_resume(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    deployment_store = WorkflowDeploymentStore(tmp_path / "deployments")
    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(deployment_api, "_store", deployment_store)
    monkeypatch.setattr(
        deployment_api,
        "_trigger_executor",
        main_module.run_deployed_workflow_trigger,
    )
    monkeypatch.setattr(main_module, "workflow_deployment_store", deployment_store)
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setenv("WORKFLOW_WEBHOOKS_ENABLED", "true")
    deployment_api._rate_windows.clear()
    current_time = [100.0]
    monkeypatch.setattr(main_module.time, "time", lambda: current_time[0])

    project = deployment_store.create_project(http_wait_workflow())
    release = deployment_store.publish(project.project_id)
    deployment, plaintext_key = deployment_store.activate(
        project.project_id,
        release.version,
        webhooks_enabled=True,
        now=current_time[0],
    )
    assert deployment.hook_id and plaintext_key

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            f"/api/workflow-hooks/{deployment.hook_id}",
            content=b'{"private":"never persist this body"}',
            headers={
                "Content-Type": "application/json",
                "X-ModelMirror-Webhook-Key": plaintext_key,
                "Idempotency-Key": "private-timer-once",
            },
        )

    assert response.status_code == 202, response.text
    trigger_execution = deployment_store.list_executions(project.project_id)[0]
    assert trigger_execution.status == "waiting"
    assert trigger_execution.wait_kind == "timer"
    assert trigger_execution.resume_at == 101
    assert trigger_execution.task_id
    execution = execution_store.require(trigger_execution.task_id)
    assert execution.status == "waiting"
    snapshot = execution_store.snapshot_path.read_text(encoding="utf-8")
    assert "never persist this body" not in snapshot
    assert "private-timer-once" not in snapshot
    assert "body_unavailable_after_resume" in snapshot

    current_time[0] = 102.0
    completed = await main_module.resume_runtime_timer_execution(execution.task_id)

    assert completed["event"] == "workflow_end"
    assert execution_store.require(execution.task_id).status == "completed"
    completed_trigger = deployment_store.list_executions(project.project_id)[0]
    assert completed_trigger.status == "completed"
    assert completed_trigger.wait_kind is None
    assert completed_trigger.result_summary.startswith("completed output_bytes=")


@pytest.mark.asyncio
async def test_failed_timer_resume_dispatches_failure_handler_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    deployment_store = WorkflowDeploymentStore(tmp_path / "deployments")
    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(deployment_api, "_store", deployment_store)
    monkeypatch.setattr(
        deployment_api,
        "_trigger_executor",
        main_module.run_deployed_workflow_trigger,
    )
    monkeypatch.setattr(main_module, "workflow_deployment_store", deployment_store)
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setenv("WORKFLOW_WEBHOOKS_ENABLED", "true")
    monkeypatch.setenv("WORKFLOW_FAILURE_TRIGGERS_ENABLED", "true")
    deployment_api._rate_windows.clear()
    current_time = [100.0]
    monkeypatch.setattr(main_module.time, "time", lambda: current_time[0])

    source = deployment_store.create_project(http_wait_workflow())
    source_release = deployment_store.publish(source.project_id)
    source_deployment, plaintext_key = deployment_store.activate(
        source.project_id,
        source_release.version,
        webhooks_enabled=True,
        now=current_time[0],
    )
    handler = deployment_store.create_project(failure_workflow(source.project_id))
    handler_release = deployment_store.publish(handler.project_id)
    deployment_store.activate(
        handler.project_id,
        handler_release.version,
        webhooks_enabled=False,
        failure_triggers_enabled=True,
        now=current_time[0],
    )
    assert source_deployment.hook_id and plaintext_key

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            f"/api/workflow-hooks/{source_deployment.hook_id}",
            content=b"{}",
            headers={
                "Content-Type": "application/json",
                "X-ModelMirror-Webhook-Key": plaintext_key,
                "Idempotency-Key": "timer-failure",
            },
        )
    assert response.status_code == 202
    source_execution = deployment_store.list_executions(source.project_id)[0]
    assert source_execution.task_id

    async def fail_on_resume(*args, **kwargs):
        raise RuntimeError("Traceback private\nRuntimeError: resume failed")

    monkeypatch.setattr(main_module, "_run_workflow_response", fail_on_resume)
    current_time[0] = 102.0
    outcome = await main_module.resume_runtime_timer_execution(source_execution.task_id)

    assert outcome["status"] == "failed"
    assert deployment_store.get_execution(source_execution.execution_id).status == "failed"
    dispatched = deployment_store.list_executions(handler.project_id)
    assert len(dispatched) == 1
    assert dispatched[0].trigger_summary["source_execution_id"] == source_execution.execution_id
    assert dispatched[0].trigger_summary["suppress_failure_dispatch"] is True


def test_hook_rate_limit_is_60_per_minute() -> None:
    deployment_api._rate_windows.clear()
    for _ in range(60):
        deployment_api._check_rate_limit("hook-rate-test", now=100)
    with pytest.raises(HTTPException) as captured:
        deployment_api._check_rate_limit("hook-rate-test", now=100)
    assert captured.value.status_code == 429


def test_http_body_global_variable_is_redacted_from_checkpoints() -> None:
    safe = main_module.checkpoint_safe_workflow_variables(
        {
            "http_event": {"type": "http_event", "body": {"private": "value"}},
            "request_body": {"private": "value"},
            "ordinary": "keep-me",
        },
        ephemeral_names={"http_event", "request_body"},
    )

    serialized = json.dumps(safe, ensure_ascii=False)
    assert "private" not in serialized
    assert "value" not in serialized
    assert safe["ordinary"] == "keep-me"
    assert safe["http_event"]["body_unavailable_after_resume"] is True
    assert safe["request_body"]["body_unavailable_after_resume"] is True


@pytest.mark.asyncio
async def test_timer_wait_persists_and_resumes_typed_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    current_time = [100.0]
    monkeypatch.setattr(main_module.time, "time", lambda: current_time[0])
    payload = main_module.WorkflowRunRequest.model_validate(
        {
            "workflow": {
                "id": "timer-test",
                "title": "timer",
                "nodes": [
                    {
                        "id": "input",
                        "type": "input",
                        "data": {"kind": "input", "variableName": "user_input"},
                    },
                    {
                        "id": "wait",
                        "type": "suspend_wait",
                        "data": {
                            "kind": "suspend_wait",
                            "waitMode": "duration",
                            "durationSeconds": 1,
                            "outputVariable": "resume_event",
                        },
                    },
                    {
                        "id": "output",
                        "type": "output",
                        "data": {"kind": "output", "outputVariable": "resume_event"},
                    },
                ],
                "edges": [
                    {"id": "e1", "source": "input", "target": "wait"},
                    {"id": "e2", "source": "wait", "target": "output"},
                ],
            },
            "inputs": {"user_input": {"typed": [1, True]}},
        }
    )

    response = await main_module._run_workflow_response(
        payload,
        None,
        runtime_execution_source_kind="workflow_deployment",
    )
    waiting = await main_module.consume_workflow_stream(response)
    assert waiting["event"] == "timer_waiting"
    assert waiting["resume_at"] == 101
    execution = execution_store.require(waiting["task_id"])
    assert execution.status == "waiting"
    assert execution.inputs["user_input"] == {"typed": [1, True]}

    recovered_store = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(main_module, "workflow_execution_store", recovered_store)
    recovered = recovered_store.require(execution.task_id)
    assert recovered.status == "waiting"
    assert recovered.inputs["user_input"] == {"typed": [1, True]}
    current_time[0] = 102.0
    completed = await main_module.resume_runtime_timer_execution(execution.task_id)
    assert completed["event"] == "workflow_end"
    result = json.loads(completed["final_output"])
    assert result["wait_kind"] == "timer"
    assert result["scheduled_resume_at"] == 101
    assert recovered_store.require(execution.task_id).status == "completed"


@pytest.mark.asyncio
async def test_timer_until_rejects_more_than_thirty_days(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        main_module,
        "workflow_execution_store",
        WorkflowExecutionStore(tmp_path / "executions"),
    )
    until = (datetime.now(timezone.utc) + timedelta(days=31)).isoformat()
    payload = main_module.WorkflowRunRequest.model_validate(
        {
            "workflow": {
                "id": "timer-limit",
                "title": "timer limit",
                "nodes": [
                    {
                        "id": "input",
                        "type": "input",
                        "data": {"kind": "input", "variableName": "user_input"},
                    },
                    {
                        "id": "wait",
                        "type": "suspend_wait",
                        "data": {
                            "kind": "suspend_wait",
                            "waitMode": "until",
                            "untilTemplate": until,
                            "outputVariable": "resume_event",
                        },
                    },
                    {
                        "id": "output",
                        "type": "output",
                        "data": {"kind": "output", "outputVariable": "resume_event"},
                    },
                ],
                "edges": [
                    {"id": "e1", "source": "input", "target": "wait"},
                    {"id": "e2", "source": "wait", "target": "output"},
                ],
            },
            "inputs": {"user_input": "test"},
        }
    )

    response = await main_module._run_workflow_response(payload, None)
    with pytest.raises(RuntimeError, match="more than 30 days"):
        await main_module.consume_workflow_stream(response)


def test_cancelled_timer_is_never_recovered(tmp_path) -> None:
    store = WorkflowExecutionStore(tmp_path / "executions")
    store.create(
        task_id="cancelled-timer",
        run_id="run-cancelled-timer",
        run_type="workflow",
        workflow={"id": "timer", "title": "timer"},
        inputs={},
        source_kind="workflow_deployment",
    )
    store.suspend(
        "cancelled-timer",
        wait_kind="timer",
        wait_id="timer:cancelled",
        resume_at=100,
        continuation={"variables": {}, "queue": []},
    )
    store.cancel("cancelled-timer")

    assert store.list_due_timers(now=200) == []
