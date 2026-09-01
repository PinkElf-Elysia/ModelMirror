from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

import server.main as main_module
from server.rag.rag_service import RagRetrievalUnavailableError
from server.workflow_native.secure_http import WorkflowHttpRequestError
from server.xpert_runtime import RunRegistry
from server.xpert_runtime.execution_store import (
    WorkflowExecutionConflictError,
    WorkflowExecutionStore,
)


def _events(response_text: str) -> list[dict[str, Any]]:
    return [
        json.loads(line[5:].strip())
        for line in response_text.splitlines()
        if line.startswith("data:")
    ]


@pytest.mark.asyncio
async def test_httpx_request_access_log_does_not_expose_query_secret(caplog) -> None:
    sentinel = "SENTINEL_QUERY_CREDENTIAL_R29"

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    caplog.set_level(logging.INFO)
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as client:
        response = await client.get(f"https://public.example.test/?api_key={sentinel}")

    assert response.status_code == 200
    assert sentinel not in caplog.text


def _retry_workflow() -> dict[str, Any]:
    return {
        "id": "retry-security-regression",
        "title": "Retry security regression",
        "nodes": [
            {
                "id": "input",
                "type": "input",
                "data": {"kind": "input", "variableName": "user_input"},
            },
            {
                "id": "request",
                "type": "http_request",
                "data": {
                    "kind": "http_request",
                    "contractVersion": 2,
                    "method": "GET",
                    "url": "https://example.com/status",
                    "queryItems": [],
                    "headerItems": [],
                    "bodyMode": "none",
                    "formFields": [],
                    "authType": "none",
                    "timeoutSeconds": 30,
                    "redirectLimit": 0,
                    "responseLimitBytes": 1024,
                    "responseMode": "auto",
                    "statusPolicy": "success_only",
                    "outputVariable": "http_response",
                    "failureAction": "stop",
                    "retryMode": "transient",
                    "maxAttempts": 3,
                },
            },
            {
                "id": "output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "http_response"},
            },
        ],
        "edges": [
            {"id": "e1", "source": "input", "target": "request"},
            {"id": "e2", "source": "request", "target": "output"},
        ],
    }


def _data_table_retry_workflow() -> dict[str, Any]:
    workflow = _retry_workflow()
    workflow["id"] = "retry-security-table"
    workflow["nodes"][1] = {
        "id": "query",
        "type": "data_table_query",
        "data": {
            "kind": "data_table_query",
            "tableId": "table_retry_fixture",
            "versionPolicy": "pinned",
            "pinnedSchemaVersion": 1,
            "filter": None,
            "selectFields": ["name"],
            "sort": [],
            "limit": 20,
            "returnMode": "list",
            "outputVariable": "records",
            "failureAction": "stop",
            "retryMode": "transient",
            "maxAttempts": 3,
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "records"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "query"},
        {"id": "e2", "source": "query", "target": "output"},
    ]
    return workflow


def _knowledge_retry_workflow() -> dict[str, Any]:
    workflow = _retry_workflow()
    workflow["id"] = "retry-security-knowledge"
    workflow["nodes"][1] = {
        "id": "retrieval",
        "type": "knowledge_retrieval",
        "data": {
            "kind": "knowledge_retrieval",
            "contractVersion": 2,
            "queryVariable": "user_input",
            "knowledgeBaseId": "kb_retry_fixture",
            "top_k": "3",
            "returnMode": "result",
            "outputVariable": "knowledge_result",
            "failureAction": "stop",
            "retryMode": "transient",
            "maxAttempts": 3,
        },
    }
    workflow["nodes"][2]["data"]["outputVariable"] = "knowledge_result"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "retrieval"},
        {"id": "e2", "source": "retrieval", "target": "output"},
    ]
    return workflow


async def _start_retry(
    client: httpx.AsyncClient,
    workflow: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    response = await client.post(
        "/api/workflow/run",
        json={
            "workflow": workflow or _retry_workflow(),
            "inputs": {"user_input": "synthetic"},
        },
    )
    assert response.status_code == 200, response.text
    scheduled = next(
        event
        for event in _events(response.text)
        if event.get("event") == "node_retry_scheduled"
    )
    return str(scheduled["task_id"]), scheduled


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "node_kind",
    ["http_request", "data_table_query", "knowledge_retrieval"],
)
async def test_reclaimed_lease_after_retry_started_prevents_actual_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    node_kind: str,
) -> None:
    clock = {"now": 3_000.0}
    calls = 0
    store = WorkflowExecutionStore(tmp_path / "executions")
    registry = RunRegistry()

    async def transient_http(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        raise WorkflowHttpRequestError("HTTP_TIMEOUT", "PRIVATE_HTTP_CALL")

    class _BusyTableStore:
        def resolve_schema_version(self, *_args: Any, **_kwargs: Any) -> Any:
            return SimpleNamespace(version=1)

        def query_records(self, *_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
            nonlocal calls
            calls += 1
            error = sqlite3.OperationalError("PRIVATE_TABLE_CALL")
            error.sqlite_errorcode = sqlite3.SQLITE_BUSY
            raise error

    def fixed_target(_kb_id: str) -> tuple[str, str]:
        return ("a" * 64, "ragv_fixed")

    async def unavailable_retrieval(
        *_args: Any,
        **_kwargs: Any,
    ) -> tuple[Any, dict[str, Any]]:
        nonlocal calls
        calls += 1
        raise RagRetrievalUnavailableError(
            "rag_vector_backend_unavailable",
            "PRIVATE_KNOWLEDGE_CALL",
        )

    workflows = {
        "http_request": _retry_workflow(),
        "data_table_query": _data_table_retry_workflow(),
        "knowledge_retrieval": _knowledge_retry_workflow(),
    }
    monkeypatch.setattr(main_module, "workflow_execution_store", store)
    monkeypatch.setattr(main_module, "run_registry", registry)
    monkeypatch.setattr(main_module.time, "time", lambda: clock["now"])
    monkeypatch.setenv("WORKFLOW_NODE_RETRIES_ENABLED", "true")
    if node_kind == "http_request":
        monkeypatch.setattr(
            main_module,
            "execute_workflow_http_request",
            transient_http,
        )
        monkeypatch.setenv("WORKFLOW_HTTP_REQUESTS_ENABLED", "true")
    elif node_kind == "data_table_query":
        monkeypatch.setattr(main_module, "agent_table_store", _BusyTableStore())
    else:
        monkeypatch.setattr(
            main_module,
            "resolve_workflow_knowledge_retry_target",
            fixed_target,
        )
        monkeypatch.setattr(
            main_module,
            "execute_workflow_knowledge_retrieval",
            unavailable_retrieval,
        )
    main_module.request_windows.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        task_id, _ = await _start_retry(client, workflows[node_kind])

    assert calls == 1
    waiting = store.require(task_id)
    wait_id = str(waiting.wait_id)
    clock["now"] = float(waiting.resume_at or 0.0)
    original_append_event = store.append_event
    reclaimed = None

    def append_event_and_reclaim(
        event_task_id: str,
        event: dict[str, Any],
        **kwargs: Any,
    ) -> Any:
        nonlocal reclaimed
        saved = original_append_event(event_task_id, event, **kwargs)
        if event.get("event") == "node_retry_started":
            clock["now"] += main_module.WORKFLOW_DURABLE_WAIT_LEASE_SECONDS + 1.0
            reclaimed = store.claim_due_wait(
                task_id,
                wait_kind="node_retry",
                wait_id=wait_id,
                worker_id="replacement-worker",
                lease_seconds=120.0,
                now=clock["now"],
            )
        return saved

    monkeypatch.setattr(store, "append_event", append_event_and_reclaim)

    stale_result = await main_module.resume_runtime_due_execution(task_id)

    assert stale_result == {"status": "running", "task_id": task_id}
    assert calls == 1
    assert reclaimed is not None
    persisted = store.require(task_id)
    assert persisted.status == "running"
    assert persisted.lease_owner == "replacement-worker"
    assert persisted.lease_token == reclaimed.lease_token
    assert sum(
        event.get("event") == "node_retry_started"
        for event in persisted.events
    ) == 1
    assert not any(
        event.get("event") in {"node_delta", "node_end", "workflow_end"}
        and event.get("node_id") in {"request", "query", "retrieval", None}
        for event in persisted.events
    )


@pytest.mark.asyncio
async def test_knowledge_retry_rechecks_lease_after_started_checkpoint_before_retrieval(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    clock = {"now": 4_000.0}
    calls = 0
    store = WorkflowExecutionStore(tmp_path / "executions")
    registry = RunRegistry()

    def fixed_target(_kb_id: str) -> tuple[str, str]:
        return ("a" * 64, "ragv_fixed")

    async def unavailable_retrieval(
        *_args: Any,
        **_kwargs: Any,
    ) -> tuple[Any, dict[str, Any]]:
        nonlocal calls
        calls += 1
        raise RagRetrievalUnavailableError(
            "rag_vector_backend_unavailable",
            "PRIVATE_KNOWLEDGE_CALL",
        )

    monkeypatch.setattr(main_module, "workflow_execution_store", store)
    monkeypatch.setattr(main_module, "run_registry", registry)
    monkeypatch.setattr(main_module.time, "time", lambda: clock["now"])
    monkeypatch.setattr(
        main_module,
        "resolve_workflow_knowledge_retry_target",
        fixed_target,
    )
    monkeypatch.setattr(
        main_module,
        "execute_workflow_knowledge_retrieval",
        unavailable_retrieval,
    )
    monkeypatch.setenv("WORKFLOW_NODE_RETRIES_ENABLED", "true")
    main_module.request_windows.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        task_id, _ = await _start_retry(client, _knowledge_retry_workflow())

    assert calls == 1
    waiting = store.require(task_id)
    wait_id = str(waiting.wait_id)
    clock["now"] = float(waiting.resume_at or 0.0)
    original_record_checkpoint = registry.record_checkpoint
    reclaimed = None

    async def checkpoint_and_reclaim(*args: Any, **kwargs: Any) -> Any:
        nonlocal reclaimed
        checkpoint = await original_record_checkpoint(*args, **kwargs)
        if kwargs.get("event_type") == "knowledge_retrieval.started":
            clock["now"] += main_module.WORKFLOW_DURABLE_WAIT_LEASE_SECONDS + 1.0
            reclaimed = store.claim_due_wait(
                task_id,
                wait_kind="node_retry",
                wait_id=wait_id,
                worker_id="replacement-worker",
                lease_seconds=120.0,
                now=clock["now"],
            )
        return checkpoint

    monkeypatch.setattr(registry, "record_checkpoint", checkpoint_and_reclaim)

    stale_result = await main_module.resume_runtime_due_execution(task_id)

    assert stale_result == {"status": "running", "task_id": task_id}
    assert calls == 1
    assert reclaimed is not None
    persisted = store.require(task_id)
    assert persisted.status == "running"
    assert persisted.lease_owner == "replacement-worker"
    assert not any(
        event.get("event") in {"node_delta", "node_end", "workflow_end"}
        and event.get("node_id") in {"retrieval", None}
        for event in persisted.events
    )
    child_runs = await registry.list_runs(run_type="knowledge_retrieval")
    assert any(run.status == "cancelled" for run in child_runs)
    assert not any(run.status == "completed" for run in child_runs)


class _XpertContextWriteSpy:
    def __init__(self) -> None:
        self.append_calls = 0
        self.title_calls = 0
        self.rebind_calls = 0

    def append_message(self, *_args: Any, **_kwargs: Any) -> Any:
        self.append_calls += 1
        return SimpleNamespace(message_id="unexpected")

    def update_conversation_title(self, *_args: Any, **_kwargs: Any) -> Any:
        self.title_calls += 1
        return None

    def rebind_execution_run(self, *_args: Any, **_kwargs: Any) -> None:
        self.rebind_calls += 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "takeover_phase",
    ["completed_checkpoint", "enrichment_return"],
)
async def test_private_xpert_retry_lost_lease_blocks_all_postprocessing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    takeover_phase: str,
) -> None:
    clock = {"now": 5_000.0}
    http_calls = 0
    enrichment_calls = 0
    memory_calls = 0
    reclaimed = None
    task_id = ""
    wait_id = ""
    store = WorkflowExecutionStore(tmp_path / "executions")
    registry = RunRegistry()
    context_spy = _XpertContextWriteSpy()

    async def flaky_http(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal http_calls
        http_calls += 1
        if http_calls == 1:
            raise WorkflowHttpRequestError("HTTP_TIMEOUT", "PRIVATE_XPERT_HTTP")
        return {
            "statusCode": 200,
            "ok": True,
            "contentType": "application/json",
            "headers": {},
            "receivedBytes": 2,
            "body": {"marker": "recovered"},
        }

    def reclaim() -> None:
        nonlocal reclaimed
        clock["now"] += main_module.WORKFLOW_DURABLE_WAIT_LEASE_SECONDS + 1.0
        reclaimed = store.claim_due_wait(
            task_id,
            wait_kind="node_retry",
            wait_id=wait_id,
            worker_id="replacement-worker",
            lease_seconds=120.0,
            now=clock["now"],
        )

    async def fake_enrichment(*_args: Any, **_kwargs: Any) -> tuple[str, list[str]]:
        nonlocal enrichment_calls
        enrichment_calls += 1
        if takeover_phase == "enrichment_return":
            reclaim()
        return ("Recovered title", ["Follow up"])

    async def fake_memory(*_args: Any, **_kwargs: Any) -> list[Any]:
        nonlocal memory_calls
        memory_calls += 1
        return []

    monkeypatch.setattr(main_module, "workflow_execution_store", store)
    monkeypatch.setattr(main_module, "run_registry", registry)
    monkeypatch.setattr(main_module, "xpert_context_store", context_spy)
    monkeypatch.setattr(main_module.time, "time", lambda: clock["now"])
    monkeypatch.setattr(main_module, "execute_workflow_http_request", flaky_http)
    monkeypatch.setattr(
        main_module,
        "generate_xpert_conversation_enrichment",
        fake_enrichment,
    )
    monkeypatch.setattr(
        main_module,
        "generate_xpert_memory_candidates",
        fake_memory,
    )
    monkeypatch.setenv("WORKFLOW_HTTP_REQUESTS_ENABLED", "true")
    monkeypatch.setenv("WORKFLOW_NODE_RETRIES_ENABLED", "true")
    main_module.request_windows.clear()

    payload = main_module.WorkflowRunRequest.model_validate(
        {
            "workflow": _retry_workflow(),
            "inputs": {"user_input": "synthetic"},
        }
    )
    response = await main_module._run_workflow_response(
        payload,
        None,
        runtime_run_type="xpert",
        runtime_source_id="xpert-private-retry",
        runtime_metadata={
            "xpert_id": "xpert-private-retry",
            "xpert_version": 1,
            "conversation_id": "conversation-private-retry",
            "conversation_title": "New conversation",
            "conversation_message_count": 0,
            "conversation_messages": [],
            "xpert_features": {
                "generated_questions": {"enabled": True, "count": 1},
                "conversation_title": {"enabled": True},
            },
            "memory_write_enabled": True,
            "memory_write_target": "xpert",
            "memory_write_max_candidates": 1,
        },
        runtime_execution_source_kind="xpert_chat",
    )
    pending = await main_module.consume_workflow_stream(response)
    assert pending["event"] == "node_retry_scheduled"
    task_id = str(pending["task_id"])
    waiting = store.require(task_id)
    wait_id = str(waiting.wait_id)
    clock["now"] = float(waiting.resume_at or 0.0)

    original_record_checkpoint = registry.record_checkpoint

    async def checkpoint_and_maybe_reclaim(*args: Any, **kwargs: Any) -> Any:
        checkpoint = await original_record_checkpoint(*args, **kwargs)
        if (
            takeover_phase == "completed_checkpoint"
            and kwargs.get("event_type") == "xpert.completed"
        ):
            reclaim()
        return checkpoint

    monkeypatch.setattr(registry, "record_checkpoint", checkpoint_and_maybe_reclaim)

    stale_result = await main_module.resume_runtime_due_execution(task_id)

    assert stale_result == {"status": "running", "task_id": task_id}
    assert http_calls == 2
    assert enrichment_calls == (
        1 if takeover_phase == "enrichment_return" else 0
    )
    assert context_spy.append_calls == 0
    assert context_spy.title_calls == 0
    assert memory_calls == 0
    assert reclaimed is not None
    persisted = store.require(task_id)
    assert persisted.status == "running"
    assert persisted.lease_owner == "replacement-worker"
    assert persisted.lease_token == reclaimed.lease_token
    assert not any(
        event.get("event") == "workflow_end" for event in persisted.events
    )
    checkpoints = await registry.list_checkpoints(persisted.run_id)
    assert not any(
        checkpoint.event_type == "xpert.conversation.enriched"
        for checkpoint in checkpoints
    )


def _rewrite_snapshot(
    store: WorkflowExecutionStore,
    mutation: Callable[[dict[str, Any]], None],
) -> WorkflowExecutionStore:
    payload = json.loads(store.snapshot_path.read_text(encoding="utf-8"))
    item = payload["items"][0]
    mutation(item)
    store.snapshot_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return WorkflowExecutionStore(store.storage_dir)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("extra_key", "extra_value"),
    [
        ("runtime_context", {"system_prompt": "SENTINEL_RUNTIME_CONTEXT"}),
        ("final_output", "SENTINEL_FINAL_OUTPUT"),
        ("agent_state", {"messages": ["SENTINEL_AGENT_STATE"]}),
        (
            "skill_creator_handoff_request",
            {"requirement": "SENTINEL_CREATOR_HANDOFF"},
        ),
    ],
)
async def test_retry_resume_rejects_extra_continuation_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    extra_key: str,
    extra_value: Any,
) -> None:
    clock = {"now": 1_000.0}
    calls = 0

    async def transient_http(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        raise WorkflowHttpRequestError("HTTP_TIMEOUT", "PRIVATE_HTTP_SENTINEL")

    store = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(main_module, "workflow_execution_store", store)
    monkeypatch.setattr(main_module, "run_registry", RunRegistry())
    monkeypatch.setattr(main_module.time, "time", lambda: clock["now"])
    monkeypatch.setattr(main_module, "execute_workflow_http_request", transient_http)
    monkeypatch.setenv("WORKFLOW_HTTP_REQUESTS_ENABLED", "true")
    monkeypatch.setenv("WORKFLOW_NODE_RETRIES_ENABLED", "true")
    main_module.request_windows.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        task_id, _ = await _start_retry(client)

    def inject_extra_state(item: dict[str, Any]) -> None:
        item["continuation"][extra_key] = extra_value

    reopened = _rewrite_snapshot(store, inject_extra_state)
    monkeypatch.setattr(main_module, "workflow_execution_store", reopened)
    clock["now"] = 1_005.0

    failed = await main_module.resume_runtime_due_execution(task_id)

    assert failed["event"] == "error"
    assert failed["code"] == "NODE_RETRY_STATE_INVALID"
    assert calls == 1
    persisted = reopened.require(task_id)
    assert persisted.status == "failed"
    serialized = json.dumps(
        {"events": persisted.events, "error": persisted.error},
        ensure_ascii=False,
    )
    assert "PRIVATE_HTTP_SENTINEL" not in serialized
    assert "SENTINEL_" not in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event_field", "corrupt_value"),
    [
        ("attempt", 3),
        ("wait_id", "node_retry:corrupt"),
        ("resume_at", 1_006.0),
    ],
)
async def test_retry_resume_rejects_schedule_journal_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    event_field: str,
    corrupt_value: Any,
) -> None:
    clock = {"now": 2_000.0}
    calls = 0

    async def transient_http(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        raise WorkflowHttpRequestError("HTTP_TIMEOUT", "PRIVATE_EVENT_SENTINEL")

    store = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(main_module, "workflow_execution_store", store)
    monkeypatch.setattr(main_module, "run_registry", RunRegistry())
    monkeypatch.setattr(main_module.time, "time", lambda: clock["now"])
    monkeypatch.setattr(main_module, "execute_workflow_http_request", transient_http)
    monkeypatch.setenv("WORKFLOW_HTTP_REQUESTS_ENABLED", "true")
    monkeypatch.setenv("WORKFLOW_NODE_RETRIES_ENABLED", "true")
    main_module.request_windows.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        task_id, _ = await _start_retry(client)

    def corrupt_latest_schedule(item: dict[str, Any]) -> None:
        scheduled = [
            event
            for event in item["events"]
            if event.get("event") == "node_retry_scheduled"
            and event.get("node_id") == "request"
        ]
        assert scheduled
        scheduled[-1][event_field] = corrupt_value

    reopened = _rewrite_snapshot(store, corrupt_latest_schedule)
    monkeypatch.setattr(main_module, "workflow_execution_store", reopened)
    clock["now"] = 2_005.0

    failed = await main_module.resume_runtime_due_execution(task_id)

    assert failed["event"] == "error"
    assert failed["code"] == "NODE_RETRY_STATE_INVALID"
    assert calls == 1
    persisted = reopened.require(task_id)
    assert persisted.status == "failed"
    assert "PRIVATE_EVENT_SENTINEL" not in str(persisted.error or "")


def _create_due_retry(store: WorkflowExecutionStore) -> None:
    store.create(
        task_id="task-retry-lease",
        run_id="run-retry-lease",
        run_type="workflow",
        workflow={"id": "wf-retry", "nodes": [], "edges": []},
        inputs={},
        source_kind="workflow_deployment",
    )
    store.suspend(
        "task-retry-lease",
        wait_kind="node_retry",
        wait_id="node_retry:lease",
        resume_at=100.0,
        continuation={
            "queue": ["http-1"],
            "queued": ["http-1"],
            "executed": ["input-1"],
            "scheduler": {"version": 2},
            "retry_state": {
                "version": 1,
                "node_id": "http-1",
                "node_kind": "http_request",
                "next_attempt": 2,
                "max_attempts": 2,
                "error_code": "HTTP_TIMEOUT",
                "classification": "transient",
                "resume_at": 100.0,
                "target_fingerprint": None,
                "target_version_id": None,
            },
            "execution_budget": None,
        },
    )


def test_stale_release_token_cannot_clear_reclaimed_worker_lease(tmp_path: Path) -> None:
    store = WorkflowExecutionStore(tmp_path / "executions")
    _create_due_retry(store)
    current = time.time()

    first = store.claim_due_wait(
        "task-retry-lease",
        wait_kind="node_retry",
        wait_id="node_retry:lease",
        worker_id="worker-a",
        lease_seconds=5,
        now=current,
    )
    second = store.claim_due_wait(
        "task-retry-lease",
        wait_kind="node_retry",
        wait_id="node_retry:lease",
        worker_id="worker-b",
        lease_seconds=30,
        now=current + 5.0,
    )

    assert first.lease_token
    assert second.lease_token
    assert first.lease_token != second.lease_token
    with pytest.raises(WorkflowExecutionConflictError):
        store.release_ready(
            "task-retry-lease",
            expected_lease_token=first.lease_token,
        )

    still_claimed = store.require("task-retry-lease")
    assert still_claimed.status == "running"
    assert still_claimed.lease_owner == "worker-b"
    assert still_claimed.lease_token == second.lease_token
    assert still_claimed.lease_expires_at == current + 35.0

    released = store.release_ready(
        "task-retry-lease",
        expected_lease_token=second.lease_token,
    )
    assert released.status == "ready"
    assert released.lease_owner is None
    assert released.lease_token is None


def test_current_worker_can_defer_after_lease_expiry_without_restart(tmp_path: Path) -> None:
    store = WorkflowExecutionStore(tmp_path / "executions")
    _create_due_retry(store)
    expired_now = time.time() - 10.0
    claimed = store.claim_due_wait(
        "task-retry-lease",
        wait_kind="node_retry",
        wait_id="node_retry:lease",
        worker_id="worker-a",
        lease_seconds=5,
        now=expired_now,
    )

    assert claimed.lease_token
    assert claimed.lease_expires_at < time.time()

    released = store.release_ready(
        "task-retry-lease",
        expected_lease_token=claimed.lease_token,
    )

    assert released.status == "ready"
    assert released.lease_owner is None
    assert released.lease_token is None
