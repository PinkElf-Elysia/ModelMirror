from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import asdict
from types import SimpleNamespace

import httpx
import pytest

import server.main as main_module
from server.rag.rag_service import RagRetrievalUnavailableError
from server.workflow_native.secure_http import WorkflowHttpRequestError
from server.xpert_runtime import RunRegistry
from server.xpert_runtime.execution_store import WorkflowExecutionStore


def _events(text: str) -> list[dict]:
    return [
        json.loads(line[5:].strip())
        for line in text.splitlines()
        if line.startswith("data:")
    ]


def _workflow(*, max_attempts: int = 2, failure_action: str = "error_output") -> dict:
    nodes = [
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
                "failureAction": failure_action,
                "errorVariable": (
                    "node_error" if failure_action == "error_output" else None
                ),
                "retryMode": "transient",
                "maxAttempts": max_attempts,
            },
        },
        {
            "id": "success",
            "type": "output",
            "data": {"kind": "output", "outputVariable": "http_response"},
        },
    ]
    edges = [
        {"id": "e1", "source": "input", "target": "request"},
        {"id": "success-edge", "source": "request", "target": "success"},
    ]
    if failure_action == "error_output":
        nodes.append(
            {
                "id": "handled",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "node_error"},
            }
        )
        edges.append(
            {
                "id": "error-edge",
                "source": "request",
                "sourceHandle": "error",
                "target": "handled",
            }
        )
    return {
        "id": "retry-runtime",
        "title": "Retry runtime",
        "nodes": nodes,
        "edges": edges,
    }


def _success_response(marker: str = "ok") -> dict:
    return {
        "statusCode": 200,
        "ok": True,
        "contentType": "application/json",
        "headers": {},
        "receivedBytes": len(marker),
        "body": {"marker": marker},
    }


def _two_retry_workflow() -> dict:
    workflow = _workflow(failure_action="stop")
    first = workflow["nodes"][1]
    first["id"] = "request-one"
    first["data"]["url"] = "https://example.com/one"
    first["data"]["outputVariable"] = "response_one"
    second = json.loads(json.dumps(first))
    second["id"] = "request-two"
    second["data"]["url"] = "https://example.com/two"
    second["data"]["outputVariable"] = "http_response"
    workflow["nodes"].insert(2, second)
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "request-one"},
        {"id": "e2", "source": "request-one", "target": "request-two"},
        {"id": "e3", "source": "request-two", "target": "success"},
    ]
    return workflow


def _data_table_retry_workflow() -> dict:
    return {
        "id": "data-table-retry-runtime",
        "title": "Data table retry runtime",
        "nodes": [
            {
                "id": "input",
                "type": "input",
                "data": {"kind": "input", "variableName": "user_input"},
            },
            {
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
                    "maxAttempts": 2,
                },
            },
            {
                "id": "output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "records"},
            },
        ],
        "edges": [
            {"id": "e1", "source": "input", "target": "query"},
            {"id": "e2", "source": "query", "target": "output"},
        ],
    }


def _upstream_table_then_http_retry_workflow() -> dict:
    workflow = _data_table_retry_workflow()
    query = workflow["nodes"][1]
    query["data"].pop("retryMode")
    query["data"].pop("maxAttempts")
    request = _workflow(failure_action="stop")["nodes"][1]
    output = workflow["nodes"][2]
    # The query result is intentionally consumed after the retrying node. A
    # restart cannot reproduce it without persisting the sensitive row payload.
    output["data"]["outputVariable"] = "records"
    workflow["nodes"] = [workflow["nodes"][0], query, request, output]
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "query"},
        {"id": "e2", "source": "query", "target": "request"},
        {"id": "e3", "source": "request", "target": "output"},
    ]
    return workflow


def _parallel_sibling_retry_workflow() -> dict:
    request = _workflow(failure_action="stop")["nodes"][1]
    return {
        "id": "parallel-sibling-retry-runtime",
        "title": "Parallel sibling retry runtime",
        "nodes": [
            {
                "id": "input",
                "type": "input",
                "data": {"kind": "input", "variableName": "user_input"},
            },
            {
                "id": "before",
                "type": "variable_assign",
                "data": {
                    "kind": "variable_assign",
                    "contractVersion": 2,
                    "outputVariable": "private_value",
                    "valueSource": "literal",
                    "literalValue": "private",
                },
            },
            request,
            {
                "id": "sibling",
                "type": "variable_assign",
                "data": {
                    "kind": "variable_assign",
                    "contractVersion": 2,
                    "outputVariable": "copied_value",
                    "valueSource": "variable",
                    "sourceVariable": "private_value",
                },
            },
            {
                "id": "request-output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "http_response"},
            },
            {
                "id": "sibling-output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "copied_value"},
            },
        ],
        "edges": [
            {"id": "e1", "source": "input", "target": "before"},
            {"id": "e2", "source": "before", "target": "request"},
            {"id": "e3", "source": "before", "target": "sibling"},
            {"id": "e4", "source": "request", "target": "request-output"},
            {"id": "e5", "source": "sibling", "target": "sibling-output"},
        ],
    }


def _exclusive_fallback_retry_workflow() -> dict:
    request = _workflow(failure_action="stop")["nodes"][1]
    return {
        "id": "exclusive-fallback-retry-runtime",
        "title": "Exclusive fallback retry runtime",
        "nodes": [
            {
                "id": "input",
                "type": "input",
                "data": {"kind": "input", "variableName": "payload"},
            },
            {
                "id": "condition",
                "type": "condition",
                "data": {
                    "kind": "condition",
                    "contractVersion": 2,
                    "inputVariable": "payload",
                    "operator": "equals",
                    "valueType": "text",
                    "value": "synthetic",
                },
            },
            request,
            {
                "id": "fallback",
                "type": "variable_assign",
                "data": {
                    "kind": "variable_assign",
                    "contractVersion": 2,
                    "outputVariable": "http_response",
                    "valueSource": "literal",
                    "literalValue": {"fallback": True},
                },
            },
            {
                "id": "consumer",
                "type": "variable_assign",
                "data": {
                    "kind": "variable_assign",
                    "contractVersion": 2,
                    "outputVariable": "selected_result",
                    "valueSource": "variable",
                    "sourceVariable": "http_response",
                },
            },
            {
                "id": "output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "selected_result"},
            },
        ],
        "edges": [
            {"id": "e1", "source": "input", "target": "condition"},
            {
                "id": "e2",
                "source": "condition",
                "sourceHandle": "true",
                "target": "request",
            },
            {
                "id": "e3",
                "source": "condition",
                "sourceHandle": "false",
                "target": "fallback",
            },
            {"id": "e4", "source": "request", "target": "consumer"},
            {"id": "e5", "source": "fallback", "target": "consumer"},
            {"id": "e6", "source": "consumer", "target": "output"},
        ],
    }


def _knowledge_retry_workflow() -> dict:
    return {
        "id": "knowledge-retry-runtime",
        "title": "Knowledge retry runtime",
        "nodes": [
            {
                "id": "input",
                "type": "input",
                "data": {"kind": "input", "variableName": "user_input"},
            },
            {
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
                    "maxAttempts": 2,
                },
            },
            {
                "id": "output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "knowledge_result"},
            },
        ],
        "edges": [
            {"id": "e1", "source": "input", "target": "retrieval"},
            {"id": "e2", "source": "retrieval", "target": "output"},
        ],
    }


def _external_entry_retry_workflow(entry_kind: str) -> dict:
    if entry_kind == "http_event_entry":
        entry_data = {"kind": entry_kind, "eventVariable": "entry_event"}
        end = {
            "id": "output",
            "type": "http_event_reply",
            "data": {
                "kind": "http_event_reply",
                "statusCode": 202,
                "responseBodyType": "text",
                "bodyTemplate": "accepted",
            },
        }
    elif entry_kind == "form_event_entry":
        entry_data = {
            "kind": entry_kind,
            "contractVersion": 1,
            "formTitle": "Synthetic form",
            "formDescription": "Synthetic test only.",
            "submitLabel": "Submit",
            "privacyNotice": "Synthetic test only.",
            "successTitle": "Accepted",
            "successMessage": "You may close this page.",
            "theme": "light",
            "eventVariable": "entry_event",
            "submissionVariable": "entry_submission",
            "fields": [
                {
                    "id": "field_value",
                    "outputVariable": "user_input",
                    "label": "Value",
                    "helpText": "",
                    "placeholder": "Synthetic value",
                    "type": "short_text",
                    "required": True,
                    "options": [],
                }
            ],
        }
        end = {
            "id": "output",
            "type": "output",
            "data": {"kind": "output", "outputVariable": "http_response"},
        }
    elif entry_kind == "rss_event_entry":
        entry_data = {
            "kind": entry_kind,
            "contractVersion": 1,
            "feedUrl": "https://example.com/feed.xml",
            "pollIntervalMinutes": 15,
            "eventVariable": "entry_event",
            "itemVariable": "feed_item",
        }
        end = {
            "id": "output",
            "type": "output",
            "data": {"kind": "output", "outputVariable": "http_response"},
        }
    else:
        entry_data = {
            "kind": "email_event_entry",
            "contractVersion": 1,
            "host": "imap.example.com",
            "credentialId": "cred_test",
            "pollIntervalMinutes": 15,
            "eventVariable": "entry_event",
            "messageVariable": "email_message",
            "contentVariable": "email_content",
        }
        end = {
            "id": "output",
            "type": "output",
            "data": {"kind": "output", "outputVariable": "http_response"},
        }
    request = _workflow(failure_action="stop")["nodes"][1]
    return {
        "id": f"{entry_kind}-retry-runtime-gate",
        "title": "External entry retry runtime gate",
        "nodes": [
            {"id": "entry", "type": entry_kind, "data": entry_data},
            request,
            end,
        ],
        "edges": [
            {"id": "e1", "source": "entry", "target": "request"},
            {"id": "e2", "source": "request", "target": "output"},
        ],
    }


async def _start(
    client: httpx.AsyncClient,
    workflow: dict,
    *,
    inputs: dict | None = None,
) -> tuple[list[dict], str]:
    response = await client.post(
        "/api/workflow/run",
        json={
            "workflow": workflow,
            "inputs": {"user_input": "synthetic"} if inputs is None else inputs,
        },
    )
    assert response.status_code == 200, response.text
    events = _events(response.text)
    waiting = next(
        event for event in events if event.get("event") == "node_retry_scheduled"
    )
    return events, str(waiting["task_id"])


@pytest.mark.asyncio
async def test_http_retry_wait_resumes_once_and_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    clock = {"now": 100.0}
    calls = 0

    async def flaky_http(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise WorkflowHttpRequestError(
                "HTTP_STATUS_NOT_SUCCESSFUL",
                "sentinel-response-secret",
                status_code=503,
            )
        return {
            "statusCode": 200,
            "ok": True,
            "contentType": "application/json",
            "headers": {},
            "receivedBytes": 2,
            "body": {},
        }

    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    registry = RunRegistry()
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(main_module, "run_registry", registry)
    monkeypatch.setattr(main_module.time, "time", lambda: clock["now"])
    monkeypatch.setattr(main_module, "execute_workflow_http_request", flaky_http)
    monkeypatch.setenv("WORKFLOW_HTTP_REQUESTS_ENABLED", "true")
    monkeypatch.setenv("WORKFLOW_NODE_RETRIES_ENABLED", "true")
    main_module.request_windows.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    workflow = _workflow()
    workflow["nodes"][0]["data"].update(
        {
            "plannerRef": "entry",
            "plannerOutcomeMapV1": {"success": ""},
        }
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        initial_events, task_id = await _start(client, workflow)

    waiting = execution_store.require(task_id)
    assert waiting.status == "waiting"
    assert waiting.wait_kind == "node_retry"
    assert waiting.resume_at == 105.0
    assert "variables" not in waiting.continuation
    assert waiting.continuation["control_flow_trace"] == ["entry:success"]
    assert calls == 1
    assert "sentinel-response-secret" not in json.dumps(initial_events)

    clock["now"] = 105.0
    final_event = await main_module.resume_runtime_due_execution(task_id)
    assert final_event["event"] == "workflow_end"
    assert calls == 2
    completed = execution_store.require(task_id)
    assert completed.status == "completed"
    assert sum(
        event.get("event") == "node_retry_started"
        for event in completed.events
    ) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("inputs", "expected_payload"),
    [
        ({"user_input": "synthetic"}, "synthetic"),
        ({}, ""),
    ],
)
async def test_retry_resume_recreates_custom_input_node_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    inputs: dict,
    expected_payload: str,
) -> None:
    clock = {"now": 100.0}
    calls = 0

    async def flaky_http(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise WorkflowHttpRequestError("HTTP_TIMEOUT", "SENTINEL_PRIVATE_TIMEOUT")
        return _success_response()

    workflow = _workflow(failure_action="stop")
    workflow["nodes"][0]["data"]["variableName"] = "payload"
    workflow["nodes"][2]["data"]["outputVariable"] = "payload"
    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(main_module, "run_registry", RunRegistry())
    monkeypatch.setattr(main_module.time, "time", lambda: clock["now"])
    monkeypatch.setattr(main_module, "execute_workflow_http_request", flaky_http)
    monkeypatch.setenv("WORKFLOW_HTTP_REQUESTS_ENABLED", "true")
    monkeypatch.setenv("WORKFLOW_NODE_RETRIES_ENABLED", "true")
    main_module.request_windows.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        initial_events, task_id = await _start(client, workflow, inputs=inputs)

    assert not any(
        "SENTINEL_PRIVATE_TIMEOUT" in json.dumps(event)
        for event in initial_events
    )
    assert "variables" not in execution_store.require(task_id).continuation
    execution_store.require(task_id).continuation.pop("control_flow_trace")

    clock["now"] = 105.0
    final_event = await main_module.resume_runtime_due_execution(task_id)

    assert final_event["event"] == "workflow_end"
    assert final_event["variables"]["payload"] == expected_payload
    assert calls == 2


@pytest.mark.asyncio
async def test_retry_preflight_rejects_required_upstream_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    calls = {"table": 0, "http": 0}

    class _TableStore:
        def resolve_schema_version(self, *_args, **_kwargs):
            calls["table"] += 1
            return SimpleNamespace(version=1)

    async def should_not_call_http(*_args, **_kwargs):
        calls["http"] += 1
        return _success_response()

    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(main_module, "agent_table_store", _TableStore())
    monkeypatch.setattr(main_module, "execute_workflow_http_request", should_not_call_http)
    monkeypatch.setenv("WORKFLOW_HTTP_REQUESTS_ENABLED", "true")
    monkeypatch.setenv("WORKFLOW_NODE_RETRIES_ENABLED", "true")
    main_module.request_windows.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/workflow/run",
            json={
                "workflow": _upstream_table_then_http_retry_workflow(),
                "inputs": {"user_input": "synthetic"},
            },
        )

    assert response.status_code == 400, response.text
    assert response.json()["issues"][0]["code"] == (
        "node_retry_resume_variable_unavailable"
    )
    assert calls == {"table": 0, "http": 0}
    assert execution_store.list_items() == []


@pytest.mark.asyncio
async def test_retry_preflight_rejects_parallel_sibling_before_external_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    calls = 0

    async def should_not_call_http(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _success_response()

    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(main_module, "execute_workflow_http_request", should_not_call_http)
    monkeypatch.setenv("WORKFLOW_HTTP_REQUESTS_ENABLED", "true")
    monkeypatch.setenv("WORKFLOW_NODE_RETRIES_ENABLED", "true")
    main_module.request_windows.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/workflow/run",
            json={
                "workflow": _parallel_sibling_retry_workflow(),
                "inputs": {"user_input": "synthetic"},
            },
        )

    assert response.status_code == 400, response.text
    codes = {issue["code"] for issue in response.json()["issues"]}
    assert "node_retry_resume_variable_unavailable" in codes
    assert "node_retry_resume_runtime_state_unavailable" in codes
    assert calls == 0
    assert execution_store.list_items() == []


@pytest.mark.asyncio
async def test_retry_runtime_guard_rejects_actual_parallel_resume_queue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    calls = 0

    async def fail_http(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise WorkflowHttpRequestError("HTTP_TIMEOUT", "SENTINEL_PRIVATE_HTTP_ERROR")

    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(main_module, "execute_workflow_http_request", fail_http)
    monkeypatch.setattr(
        main_module,
        "validate_workflow_graph",
        lambda _workflow: SimpleNamespace(issues=[]),
    )
    monkeypatch.setenv("WORKFLOW_HTTP_REQUESTS_ENABLED", "true")
    monkeypatch.setenv("WORKFLOW_NODE_RETRIES_ENABLED", "true")
    main_module.request_windows.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/workflow/run",
            json={
                "workflow": _parallel_sibling_retry_workflow(),
                "inputs": {"user_input": "synthetic"},
            },
        )

    assert response.status_code == 200, response.text
    events = _events(response.text)
    terminal = next(
        event
        for event in events
        if event.get("event") == "error" and event.get("terminal") is True
    )
    assert terminal["code"] == "NODE_RETRY_INPUT_SNAPSHOT_UNSAFE"
    assert calls == 1
    assert not any(event.get("event") == "node_retry_scheduled" for event in events)
    persisted = execution_store.require(str(terminal["task_id"]))
    assert persisted.status == "failed"
    assert persisted.wait_kind is None


@pytest.mark.asyncio
async def test_retry_resume_ignores_guaranteed_skipped_fallback_edge(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    clock = {"now": 100.0}
    calls = 0

    async def flaky_http(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise WorkflowHttpRequestError("HTTP_TIMEOUT", "SENTINEL_PRIVATE_TIMEOUT")
        return _success_response("retry-success")

    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(main_module, "run_registry", RunRegistry())
    monkeypatch.setattr(main_module.time, "time", lambda: clock["now"])
    monkeypatch.setattr(main_module, "execute_workflow_http_request", flaky_http)
    monkeypatch.setenv("WORKFLOW_HTTP_REQUESTS_ENABLED", "true")
    monkeypatch.setenv("WORKFLOW_NODE_RETRIES_ENABLED", "true")
    main_module.request_windows.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        initial_events, task_id = await _start(
            client,
            _exclusive_fallback_retry_workflow(),
        )

    assert any(event.get("event") == "node_retry_scheduled" for event in initial_events)
    waiting = execution_store.require(task_id)
    assert waiting.status == "waiting"
    assert "variables" not in waiting.continuation

    clock["now"] = 105.0
    final_event = await main_module.resume_runtime_due_execution(task_id)

    assert final_event["event"] == "workflow_end"
    assert final_event["variables"]["selected_result"]["body"] == {
        "marker": "retry-success"
    }
    assert calls == 2


@pytest.mark.asyncio
async def test_retry_wait_rejects_computed_variables_without_persisting_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    sentinel = "SENTINEL_PRIVATE_TABLE_RECORD"

    class _SensitiveTableStore:
        def resolve_schema_version(self, *_args, **_kwargs):
            return SimpleNamespace(version=1)

        def query_records(self, *_args, **_kwargs):
            return [{"record_id": "row_1", "name": sentinel}]

    async def fail_http(*_args, **_kwargs):
        raise WorkflowHttpRequestError(
            "HTTP_TIMEOUT",
            "SENTINEL_PRIVATE_HTTP_ERROR",
        )

    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(main_module, "agent_table_store", _SensitiveTableStore())
    monkeypatch.setattr(main_module, "execute_workflow_http_request", fail_http)
    monkeypatch.setattr(
        main_module,
        "validate_workflow_graph",
        lambda _workflow: SimpleNamespace(issues=[]),
    )
    monkeypatch.setenv("WORKFLOW_HTTP_REQUESTS_ENABLED", "true")
    monkeypatch.setenv("WORKFLOW_NODE_RETRIES_ENABLED", "true")
    main_module.request_windows.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/workflow/run",
            json={
                "workflow": _upstream_table_then_http_retry_workflow(),
                "inputs": {"user_input": "synthetic"},
            },
        )

    assert response.status_code == 200, response.text
    events = _events(response.text)
    terminal = next(
        event
        for event in events
        if event.get("event") == "error" and event.get("terminal") is True
    )
    assert terminal["code"] == "NODE_RETRY_INPUT_SNAPSHOT_UNSAFE"
    assert not any(event.get("event") == "node_retry_scheduled" for event in events)
    persisted = execution_store.require(str(terminal["task_id"]))
    assert persisted.status == "failed"
    serialized = json.dumps(asdict(persisted), ensure_ascii=False)
    assert sentinel not in serialized
    assert "SENTINEL_PRIVATE_HTTP_ERROR" not in serialized


@pytest.mark.asyncio
async def test_retry_wait_rejects_knowledge_proposal_content_input_without_persisting_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    sentinel = "SENTINEL_PRIVATE_KNOWLEDGE_PROPOSAL_CONTENT"
    workflow = _workflow(failure_action="stop")
    workflow["nodes"][2] = {
        "id": "proposal",
        "type": "knowledge_write_proposal",
        "data": {
            "kind": "knowledge_write_proposal",
            "contractVersion": 1,
            "knowledgeBaseId": "kb_synthetic",
            "titleTemplate": "Synthetic proposal",
            "contentVariable": "user_input",
            "tags": [],
            "outputVariable": "proposal_receipt",
        },
    }
    workflow["edges"][1]["target"] = "proposal"

    async def fail_http(*_args, **_kwargs):
        raise WorkflowHttpRequestError("HTTP_TIMEOUT", "SENTINEL_PRIVATE_HTTP_ERROR")

    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(main_module, "execute_workflow_http_request", fail_http)
    monkeypatch.setattr(
        main_module,
        "validate_workflow_graph",
        lambda _workflow: SimpleNamespace(issues=[]),
    )
    monkeypatch.setenv("WORKFLOW_HTTP_REQUESTS_ENABLED", "true")
    monkeypatch.setenv("WORKFLOW_NODE_RETRIES_ENABLED", "true")
    main_module.request_windows.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/workflow/run",
            json={"workflow": workflow, "inputs": {"user_input": sentinel}},
        )

    assert response.status_code == 200, response.text
    events = _events(response.text)
    terminal = next(
        event
        for event in events
        if event.get("event") == "error" and event.get("terminal") is True
    )
    assert terminal["code"] == "NODE_RETRY_INPUT_SNAPSHOT_UNSAFE"
    assert not any(event.get("event") == "node_retry_scheduled" for event in events)
    persisted = execution_store.require(str(terminal["task_id"]))
    serialized = json.dumps(asdict(persisted), ensure_ascii=False)
    assert sentinel not in serialized
    assert "SENTINEL_PRIVATE_HTTP_ERROR" not in serialized


@pytest.mark.asyncio
async def test_legacy_stop_config_reports_one_actual_attempt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    workflow = _workflow(failure_action="stop")
    request = next(node for node in workflow["nodes"] if node["id"] == "request")
    request["data"].pop("retryMode")
    request["data"].pop("maxAttempts")

    async def fail_http(*_args, **_kwargs):
        raise WorkflowHttpRequestError("HTTP_TIMEOUT", "SENTINEL_PRIVATE_TIMEOUT")

    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(main_module, "execute_workflow_http_request", fail_http)
    monkeypatch.setenv("WORKFLOW_HTTP_REQUESTS_ENABLED", "true")
    monkeypatch.setenv("WORKFLOW_NODE_RETRIES_ENABLED", "false")
    main_module.request_windows.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/workflow/run",
            json={"workflow": workflow, "inputs": {"user_input": "synthetic"}},
        )

    assert response.status_code == 200, response.text
    events = _events(response.text)
    terminal = next(
        event
        for event in events
        if event.get("event") == "error" and event.get("terminal") is True
    )
    assert terminal["attempt"] == 1
    assert terminal["max_attempts"] == 1
    assert terminal["exhausted"] is True
    assert not any(event.get("event") == "node_retry_scheduled" for event in events)


@pytest.mark.asyncio
async def test_retry_exhaustion_routes_once_with_actual_attempt_count(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    clock = {"now": 200.0}
    calls = 0

    async def fail_http(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise WorkflowHttpRequestError(
            "HTTP_TIMEOUT",
            "sentinel-timeout-secret",
        )

    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(main_module.time, "time", lambda: clock["now"])
    monkeypatch.setattr(main_module, "execute_workflow_http_request", fail_http)
    monkeypatch.setenv("WORKFLOW_HTTP_REQUESTS_ENABLED", "true")
    monkeypatch.setenv("WORKFLOW_NODE_RETRIES_ENABLED", "true")
    main_module.request_windows.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _, task_id = await _start(client, _workflow(max_attempts=2))

    clock["now"] = 205.0
    final_event = await main_module.resume_runtime_due_execution(task_id)
    assert final_event["event"] == "workflow_end"
    assert calls == 2
    completed = execution_store.require(task_id)
    routed = [
        event for event in completed.events if event.get("event") == "node_error_routed"
    ]
    assert len(routed) == 1
    assert routed[0]["attempt"] == 2
    request_end = next(
        event
        for event in completed.events
        if event.get("event") == "node_end" and event.get("node_id") == "request"
    )
    assert request_end["status"] == "handled_error"
    assert "sentinel-timeout-secret" not in json.dumps(completed.events)


@pytest.mark.asyncio
async def test_retry_switch_fails_closed_before_http_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    calls = 0

    async def should_not_run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {}

    monkeypatch.setattr(
        main_module,
        "workflow_execution_store",
        WorkflowExecutionStore(tmp_path / "executions"),
    )
    monkeypatch.setattr(main_module, "execute_workflow_http_request", should_not_run)
    monkeypatch.setenv("WORKFLOW_HTTP_REQUESTS_ENABLED", "true")
    monkeypatch.setenv("WORKFLOW_NODE_RETRIES_ENABLED", "false")
    main_module.request_windows.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/workflow/run",
            json={"workflow": _workflow(), "inputs": {}},
        )
    assert response.status_code == 409
    assert response.json()["code"] == "NODE_RETRIES_DISABLED"
    assert calls == 0


@pytest.mark.asyncio
async def test_retry_preflight_blocks_upstream_external_call_when_switch_is_off(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    calls = 0
    workflow = _two_retry_workflow()
    first_request = next(
        node for node in workflow["nodes"] if node["id"] == "request-one"
    )
    first_request["data"].pop("retryMode")
    first_request["data"].pop("maxAttempts")

    async def should_not_run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _success_response()

    monkeypatch.setattr(
        main_module,
        "workflow_execution_store",
        WorkflowExecutionStore(tmp_path / "executions"),
    )
    monkeypatch.setattr(main_module, "execute_workflow_http_request", should_not_run)
    monkeypatch.setenv("WORKFLOW_HTTP_REQUESTS_ENABLED", "true")
    monkeypatch.setenv("WORKFLOW_NODE_RETRIES_ENABLED", "false")
    main_module.request_windows.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/workflow/run",
            json={"workflow": workflow, "inputs": {}},
        )

    assert response.status_code == 409
    assert response.json()["code"] == "NODE_RETRIES_DISABLED"
    assert calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value", "expected_code"),
    [
        ("retryMode", "always", "INVALID_NODE_RETRY_MODE"),
        ("maxAttempts", 4, "INVALID_NODE_RETRY_MAX_ATTEMPTS"),
    ],
)
async def test_direct_run_rejects_invalid_retry_contract_before_node_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    field: str,
    value: object,
    expected_code: str,
) -> None:
    calls = 0
    workflow = _workflow()
    workflow["nodes"][1]["data"][field] = value

    async def should_not_run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _success_response()

    monkeypatch.setattr(
        main_module,
        "workflow_execution_store",
        WorkflowExecutionStore(tmp_path / "executions"),
    )
    monkeypatch.setattr(main_module, "execute_workflow_http_request", should_not_run)
    monkeypatch.setenv("WORKFLOW_HTTP_REQUESTS_ENABLED", "true")
    monkeypatch.setenv("WORKFLOW_NODE_RETRIES_ENABLED", "true")
    main_module.request_windows.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/workflow/run",
            json={"workflow": workflow, "inputs": {}},
        )

    assert response.status_code == 400
    body = response.json()
    assert body.get("code") == expected_code or any(
        issue.get("code", "").upper() == expected_code
        for issue in body.get("issues", [])
    )
    assert calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("retryMode", "none"),
        ("retryMode", "transient"),
        ("maxAttempts", 2),
    ],
)
async def test_direct_run_rejects_explicit_retry_on_unsupported_node_before_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    field: str,
    value: object,
) -> None:
    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    registry = RunRegistry()
    workflow = {
        "id": "unsupported-retry-direct-run",
        "title": "Unsupported retry direct run",
        "nodes": [
            {
                "id": "input",
                "type": "input",
                "data": {"kind": "input", "variableName": "user_input"},
            },
            {
                "id": "output",
                "type": "output",
                "data": {
                    "kind": "output",
                    "outputVariable": "user_input",
                    field: value,
                },
            },
        ],
        "edges": [{"id": "edge", "source": "input", "target": "output"}],
    }
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(main_module, "run_registry", registry)
    monkeypatch.setenv("WORKFLOW_NODE_RETRIES_ENABLED", "true")
    main_module.request_windows.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/workflow/run",
            json={"workflow": workflow, "inputs": {"user_input": "synthetic"}},
        )

    assert response.status_code == 400
    assert any(
        issue.get("code") == "node_retry_unsupported"
        for issue in response.json().get("issues", [])
    )
    assert execution_store.list_items() == []
    assert await registry.list_runs(run_type="workflow") == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("entry_kind", "expected_code"),
    [
        ("http_event_entry", "http_node_retry_forbidden"),
        ("form_event_entry", "form_node_retry_forbidden"),
        ("rss_event_entry", "rss_persistent_wait_forbidden"),
        ("email_event_entry", "email_persistent_wait_forbidden"),
    ],
)
async def test_external_entry_direct_run_rejects_retry_before_http_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    entry_kind: str,
    expected_code: str,
) -> None:
    calls = 0

    async def should_not_run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _success_response()

    monkeypatch.setattr(
        main_module,
        "workflow_execution_store",
        WorkflowExecutionStore(tmp_path / "executions"),
    )
    monkeypatch.setattr(main_module, "execute_workflow_http_request", should_not_run)
    monkeypatch.setenv("WORKFLOW_HTTP_REQUESTS_ENABLED", "true")
    monkeypatch.setenv("WORKFLOW_NODE_RETRIES_ENABLED", "true")
    main_module.request_windows.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/workflow/run",
            json={
                "workflow": _external_entry_retry_workflow(entry_kind),
                "inputs": {},
            },
        )

    assert response.status_code == 400
    assert any(
        issue.get("code") == expected_code
        for issue in response.json().get("issues", [])
    )
    assert calls == 0


@pytest.mark.asyncio
async def test_retry_switch_disabled_while_waiting_fails_without_second_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    clock = {"now": 300.0}
    calls = 0

    async def transient_http(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise WorkflowHttpRequestError(
            "HTTP_TIMEOUT",
            "sentinel-timeout-secret",
        )

    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(main_module.time, "time", lambda: clock["now"])
    monkeypatch.setattr(main_module, "execute_workflow_http_request", transient_http)
    monkeypatch.setenv("WORKFLOW_HTTP_REQUESTS_ENABLED", "true")
    monkeypatch.setenv("WORKFLOW_NODE_RETRIES_ENABLED", "true")
    main_module.request_windows.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _, task_id = await _start(client, _workflow(max_attempts=3))

    assert calls == 1
    assert execution_store.require(task_id).status == "waiting"
    monkeypatch.setenv("WORKFLOW_NODE_RETRIES_ENABLED", "false")
    clock["now"] = 305.0

    final_event = await main_module.resume_runtime_due_execution(task_id)

    assert final_event["event"] == "error"
    assert final_event["code"] == "NODE_RETRIES_DISABLED"
    assert calls == 1
    failed = execution_store.require(task_id)
    assert failed.status == "failed"
    assert failed.wait_kind is None
    assert sum(
        event.get("event") == "node_retry_scheduled"
        for event in failed.events
    ) == 1
    assert "sentinel-timeout-secret" not in json.dumps(failed.events)


@pytest.mark.asyncio
async def test_stop_after_retry_exhaustion_emits_terminal_actual_attempts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    clock = {"now": 1_200.0}
    calls = 0

    async def timeout(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise WorkflowHttpRequestError("HTTP_TIMEOUT", "SENTINEL_PRIVATE_TIMEOUT")

    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(main_module.time, "time", lambda: clock["now"])
    monkeypatch.setattr(main_module, "execute_workflow_http_request", timeout)
    monkeypatch.setenv("WORKFLOW_HTTP_REQUESTS_ENABLED", "true")
    monkeypatch.setenv("WORKFLOW_NODE_RETRIES_ENABLED", "true")
    main_module.request_windows.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _, task_id = await _start(
            client,
            _workflow(max_attempts=3, failure_action="stop"),
        )

    clock["now"] = 1_205.0
    second = await main_module.resume_runtime_due_execution(task_id)
    assert second["event"] == "node_retry_scheduled"
    clock["now"] = 1_235.0
    terminal = await main_module.resume_runtime_due_execution(task_id)

    assert calls == 3
    assert terminal["event"] == "error"
    assert terminal["terminal"] is True
    assert terminal["code"] == "HTTP_TIMEOUT"
    assert terminal["attempt"] == 3
    assert terminal["max_attempts"] == 3
    assert terminal["classification"] == "transient"
    assert terminal["exhausted"] is True
    persisted = execution_store.require(task_id)
    terminal_events = [
        event
        for event in persisted.events
        if event.get("event") == "error" and event.get("terminal") is True
    ]
    assert terminal_events[-1]["attempt"] == 3
    assert "SENTINEL_PRIVATE_TIMEOUT" not in json.dumps(persisted.events)


@pytest.mark.asyncio
async def test_two_retry_nodes_keep_independent_resume_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    clock = {"now": 400.0}
    calls = {"one": 0, "two": 0}

    async def flaky_http(config, *_args, **_kwargs):
        marker = "one" if str(config.get("url", "")).endswith("/one") else "two"
        calls[marker] += 1
        if calls[marker] == 1:
            raise WorkflowHttpRequestError("HTTP_TIMEOUT", "private-timeout")
        return _success_response(marker)

    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(main_module.time, "time", lambda: clock["now"])
    monkeypatch.setattr(main_module, "execute_workflow_http_request", flaky_http)
    monkeypatch.setenv("WORKFLOW_HTTP_REQUESTS_ENABLED", "true")
    monkeypatch.setenv("WORKFLOW_NODE_RETRIES_ENABLED", "true")
    main_module.request_windows.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _, task_id = await _start(client, _two_retry_workflow())

    assert calls == {"one": 1, "two": 0}
    clock["now"] = 405.0
    second_wait = await main_module.resume_runtime_due_execution(task_id)
    assert second_wait["event"] == "node_retry_scheduled"
    assert second_wait["node_id"] == "request-two"
    assert calls == {"one": 2, "two": 1}

    clock["now"] = 410.0
    completed = await main_module.resume_runtime_due_execution(task_id)
    assert completed["event"] == "workflow_end"
    assert calls == {"one": 2, "two": 2}
    started = [
        (event.get("node_id"), event.get("attempt"))
        for event in execution_store.require(task_id).events
        if event.get("event") == "node_retry_started"
    ]
    assert started == [("request-one", 2), ("request-two", 2)]


@pytest.mark.asyncio
async def test_three_attempt_retry_uses_retry_after_then_fixed_backoff(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    clock = {"now": 500.0}
    calls = 0

    async def flaky_http(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise WorkflowHttpRequestError(
                "HTTP_STATUS_NOT_SUCCESSFUL",
                "private-429-body",
                status_code=429,
                retry_after_seconds=120,
            )
        if calls == 2:
            raise WorkflowHttpRequestError("HTTP_TIMEOUT", "private-timeout")
        return _success_response()

    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(main_module.time, "time", lambda: clock["now"])
    monkeypatch.setattr(main_module, "execute_workflow_http_request", flaky_http)
    monkeypatch.setenv("WORKFLOW_HTTP_REQUESTS_ENABLED", "true")
    monkeypatch.setenv("WORKFLOW_NODE_RETRIES_ENABLED", "true")
    main_module.request_windows.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _, task_id = await _start(client, _workflow(max_attempts=3, failure_action="stop"))

    assert execution_store.require(task_id).resume_at == 620.0
    clock["now"] = 620.0
    second_wait = await main_module.resume_runtime_due_execution(task_id)
    assert second_wait["event"] == "node_retry_scheduled"
    assert execution_store.require(task_id).resume_at == 650.0
    clock["now"] = 650.0
    completed = await main_module.resume_runtime_due_execution(task_id)
    assert completed["event"] == "workflow_end"
    assert calls == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        (lambda continuation: continuation["scheduler"].update({"version": 1}),
         "NODE_RETRY_SCHEDULER_INVALID"),
        (lambda continuation: continuation["retry_state"].update({"version": True}),
         "NODE_RETRY_STATE_INVALID"),
        (lambda continuation: continuation["retry_state"].update({"error_code": "UNKNOWN"}),
         "NODE_RETRY_STATE_INVALID"),
        (lambda continuation: continuation["retry_state"].update({"unexpected": "value"}),
         "NODE_RETRY_STATE_INVALID"),
        (lambda continuation: continuation.update({"variables": {"secret": "value"}}),
         "NODE_RETRY_STATE_INVALID"),
        (lambda continuation: continuation.update({"control_flow_trace": ["native:true"]}),
         "NODE_RETRY_STATE_INVALID"),
    ],
)
async def test_tampered_retry_continuation_fails_before_second_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    mutation,
    expected_code: str,
) -> None:
    clock = {"now": 700.0}
    calls = 0

    async def transient_http(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise WorkflowHttpRequestError("HTTP_TIMEOUT", "private-timeout")

    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(main_module.time, "time", lambda: clock["now"])
    monkeypatch.setattr(main_module, "execute_workflow_http_request", transient_http)
    monkeypatch.setenv("WORKFLOW_HTTP_REQUESTS_ENABLED", "true")
    monkeypatch.setenv("WORKFLOW_NODE_RETRIES_ENABLED", "true")
    main_module.request_windows.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _, task_id = await _start(client, _workflow(max_attempts=3))

    mutation(execution_store.require(task_id).continuation)
    clock["now"] = 705.0
    failed = await main_module.resume_runtime_due_execution(task_id)
    assert failed["event"] == "error"
    assert failed["code"] == expected_code
    assert calls == 1
    persisted_error = str(execution_store.require(task_id).error or "")
    assert persisted_error.startswith(f"{expected_code}:")
    assert "private-timeout" not in persisted_error


@pytest.mark.asyncio
async def test_cancel_during_retry_call_cannot_publish_late_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    clock = {"now": 800.0}
    calls = 0
    task_id = ""
    execution_store = WorkflowExecutionStore(tmp_path / "executions")

    async def cancel_then_succeed(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise WorkflowHttpRequestError("HTTP_TIMEOUT", "private-timeout")
        execution_store.cancel(task_id)
        return _success_response("late-success")

    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(main_module.time, "time", lambda: clock["now"])
    monkeypatch.setattr(main_module, "execute_workflow_http_request", cancel_then_succeed)
    monkeypatch.setenv("WORKFLOW_HTTP_REQUESTS_ENABLED", "true")
    monkeypatch.setenv("WORKFLOW_NODE_RETRIES_ENABLED", "true")
    main_module.request_windows.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _, task_id = await _start(client, _workflow(failure_action="stop"))

    clock["now"] = 805.0
    cancelled = await main_module.resume_runtime_due_execution(task_id)
    assert cancelled["event"] == "workflow_cancelled"
    assert cancelled["status"] == "cancelled"
    stored = execution_store.require(task_id)
    assert stored.status == "cancelled"
    assert not any(event.get("event") == "workflow_end" for event in stored.events)
    assert not any(
        event.get("event") == "node_end" and event.get("node_id") == "request"
        for event in stored.events
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stale_outcome",
    ["success", "error"],
    ids=["stale-success", "stale-error"],
)
async def test_expired_retry_worker_cannot_override_terminal_winner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    caplog: pytest.LogCaptureFixture,
    stale_outcome: str,
) -> None:
    clock = {"now": 1_200.0}
    calls = 0
    stale_worker_started = asyncio.Event()
    release_stale_worker = asyncio.Event()
    stale_error_sentinel = "SENTINEL_STALE_RETRY_WORKER_PRIVATE_ERROR"

    async def contested_http(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise WorkflowHttpRequestError("HTTP_TIMEOUT", "private-timeout")
        if calls == 2:
            stale_worker_started.set()
            await release_stale_worker.wait()
            if stale_outcome == "error":
                raise RuntimeError(stale_error_sentinel)
            return _success_response("stale-worker")
        if calls == 3:
            return _success_response("lease-winner")
        raise AssertionError(f"Unexpected HTTP attempt: {calls}")

    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    registry = RunRegistry()
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(main_module, "run_registry", registry)
    monkeypatch.setattr(main_module.time, "time", lambda: clock["now"])
    monkeypatch.setattr(main_module, "execute_workflow_http_request", contested_http)
    monkeypatch.setenv("WORKFLOW_HTTP_REQUESTS_ENABLED", "true")
    monkeypatch.setenv("WORKFLOW_NODE_RETRIES_ENABLED", "true")
    main_module.request_windows.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _, task_id = await _start(
            client,
            _workflow(max_attempts=2, failure_action="stop"),
        )

    clock["now"] = 1_205.0
    stale_worker = asyncio.create_task(
        main_module.resume_runtime_due_execution(task_id)
    )
    await asyncio.wait_for(stale_worker_started.wait(), timeout=2)
    stale_lease = execution_store.require(task_id)
    assert stale_lease.status == "running"
    assert stale_lease.lease_token

    clock["now"] = 1_326.0
    winner_result = await main_module.resume_runtime_due_execution(task_id)
    assert winner_result["event"] == "workflow_end"
    assert winner_result["variables"]["http_response"]["body"] == {
        "marker": "lease-winner"
    }

    release_stale_worker.set()
    stale_result = await asyncio.wait_for(stale_worker, timeout=2)
    assert stale_result == {"status": "completed", "task_id": task_id}
    assert calls == 3

    completed = execution_store.require(task_id)
    assert completed.status == "completed"
    assert "lease-winner" in str(completed.result)
    assert "stale-worker" not in str(completed.result)
    assert sum(
        event.get("event") == "workflow_end" for event in completed.events
    ) == 1
    assert stale_error_sentinel not in json.dumps(completed.events)
    assert stale_error_sentinel not in str(completed.error)

    registry_run = await registry.get_run(completed.run_id)
    assert registry_run is not None
    assert registry_run.status == "completed"
    assert registry_run.error is None
    assert len(await registry.list_runs(run_type="workflow")) == 1
    assert stale_error_sentinel not in caplog.text


@pytest.mark.asyncio
async def test_unknown_http_exception_is_not_retried_or_leaked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls = 0

    async def broken_http(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("SENTINEL_PRIVATE_INTERNAL_EXCEPTION")

    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(main_module, "execute_workflow_http_request", broken_http)
    monkeypatch.setenv("WORKFLOW_HTTP_REQUESTS_ENABLED", "true")
    monkeypatch.setenv("WORKFLOW_NODE_RETRIES_ENABLED", "true")
    main_module.request_windows.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/workflow/run",
            json={"workflow": _workflow(failure_action="stop"), "inputs": {}},
        )

    events = _events(response.text)
    error = next(event for event in events if event.get("event") == "error")
    assert error["code"] == "WORKFLOW_NODE_FAILED"
    assert error["message"] == "Workflow node failed."
    assert calls == 1
    task_id = str(error["task_id"])
    persisted = execution_store.require(task_id)
    assert "SENTINEL" not in json.dumps(persisted.events)
    assert "SENTINEL" not in str(persisted.error)
    assert "SENTINEL" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_code",
    [
        "HTTP_DNS_UNAVAILABLE",
        "HTTP_RESPONSE_PROTOCOL_INVALID",
        "HTTP_REQUEST_FAILED",
    ],
)
async def test_http_security_and_protocol_failures_bypass_retry_and_error_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    error_code: str,
) -> None:
    calls = 0

    async def fail_once(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise WorkflowHttpRequestError(error_code, "SENTINEL_PRIVATE_HTTP_FAILURE")

    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(main_module, "execute_workflow_http_request", fail_once)
    monkeypatch.setenv("WORKFLOW_HTTP_REQUESTS_ENABLED", "true")
    monkeypatch.setenv("WORKFLOW_NODE_RETRIES_ENABLED", "true")
    main_module.request_windows.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/workflow/run",
            json={"workflow": _workflow(), "inputs": {}},
        )

    events = _events(response.text)
    terminal = next(
        event
        for event in events
        if event.get("event") == "error" and event.get("terminal") is True
    )
    assert terminal["code"] == error_code
    assert calls == 1
    assert not any(event.get("event") == "node_retry_scheduled" for event in events)
    assert not any(event.get("event") == "node_error_routed" for event in events)
    persisted = execution_store.require(str(terminal["task_id"]))
    assert "SENTINEL_PRIVATE_HTTP_FAILURE" not in json.dumps(persisted.events)


@pytest.mark.asyncio
async def test_data_table_busy_wait_resumes_once_with_typed_records(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    clock = {"now": 900.0}
    calls = 0

    class _RetryTableStore:
        def resolve_schema_version(self, *_args, **_kwargs):
            return SimpleNamespace(version=1)

        def query_records(self, *_args, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                error = sqlite3.OperationalError("SENTINEL_PRIVATE_TABLE_LOCK")
                error.sqlite_errorcode = sqlite3.SQLITE_BUSY
                raise error
            return [{"record_id": "row_1", "name": "Recovered"}]

    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(main_module, "agent_table_store", _RetryTableStore())
    monkeypatch.setattr(main_module.time, "time", lambda: clock["now"])
    monkeypatch.setenv("WORKFLOW_NODE_RETRIES_ENABLED", "true")
    main_module.request_windows.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        initial_events, task_id = await _start(client, _data_table_retry_workflow())

    waiting = execution_store.require(task_id)
    assert waiting.status == "waiting"
    assert waiting.resume_at == 905.0
    assert calls == 1
    assert "SENTINEL_PRIVATE_TABLE_LOCK" not in json.dumps(initial_events)

    clock["now"] = 905.0
    final_event = await main_module.resume_runtime_due_execution(task_id)
    assert final_event["event"] == "workflow_end"
    assert calls == 2
    completed = execution_store.require(task_id)
    assert final_event["variables"]["records"] == [
        {"record_id": "row_1", "name": "Recovered"}
    ]
    assert "SENTINEL_PRIVATE_TABLE_LOCK" not in json.dumps(completed.events)


@pytest.mark.asyncio
async def test_knowledge_backend_wait_resumes_same_fixed_version(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    clock = {"now": 1_000.0}
    calls = 0
    resolved_targets: list[str] = []

    def fixed_target(kb_id: str) -> tuple[str, str]:
        resolved_targets.append(kb_id)
        return ("a" * 64, "ragv_fixed")

    async def flaky_retrieval(*_args, **kwargs):
        nonlocal calls
        calls += 1
        assert kwargs["version_id"] == "ragv_fixed"
        if calls == 1:
            raise RagRetrievalUnavailableError(
                "rag_vector_backend_unavailable",
                "SENTINEL_PRIVATE_VECTOR_BACKEND",
            )
        return (
            {
                "knowledge_base_id": "kb_retry_fixture",
                "version_id": "ragv_fixed",
                "context": "Recovered context",
                "sources": [],
                "citations": [],
            },
            {
                "kb_id": "kb_retry_fixture",
                "version_id": "ragv_fixed",
                "hit_count": 0,
                "citation_count": 0,
                "context_length": 17,
                "warning_count": 0,
                "contract_version": 2,
                "return_mode": "result",
            },
        )

    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    registry = RunRegistry()
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(main_module, "run_registry", registry)
    monkeypatch.setattr(main_module.time, "time", lambda: clock["now"])
    monkeypatch.setattr(main_module, "resolve_workflow_knowledge_retry_target", fixed_target)
    monkeypatch.setattr(
        main_module,
        "execute_workflow_knowledge_retrieval",
        flaky_retrieval,
    )
    monkeypatch.setenv("WORKFLOW_NODE_RETRIES_ENABLED", "true")
    main_module.request_windows.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        initial_events, task_id = await _start(client, _knowledge_retry_workflow())

    retry_state = execution_store.require(task_id).continuation["retry_state"]
    assert retry_state["target_fingerprint"] == "a" * 64
    assert retry_state["target_version_id"] == "ragv_fixed"
    assert calls == 1
    assert "SENTINEL_PRIVATE_VECTOR_BACKEND" not in json.dumps(initial_events)
    assert all(
        run.status != "waiting"
        for run in await registry.list_runs(run_type="knowledge_retrieval")
    )

    clock["now"] = 1_005.0
    final_event = await main_module.resume_runtime_due_execution(task_id)
    assert final_event["event"] == "workflow_end"
    assert calls == 2
    assert resolved_targets == ["kb_retry_fixture"] * 4
    completed = execution_store.require(task_id)
    assert (
        final_event["variables"]["knowledge_result"]["version_id"]
        == "ragv_fixed"
    )
    assert "SENTINEL_PRIVATE_VECTOR_BACKEND" not in json.dumps(completed.events)
    assert all(
        run.status != "waiting"
        for run in await registry.list_runs(run_type="knowledge_retrieval")
    )


@pytest.mark.asyncio
async def test_knowledge_child_run_is_cancelled_when_backend_error_races_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    registry = RunRegistry()

    def fixed_target(_kb_id: str) -> tuple[str, str]:
        return ("a" * 64, "ragv_fixed")

    async def cancel_then_fail(*_args, **_kwargs):
        execution = execution_store.list_items(limit=1)[0]
        execution_store.cancel(execution.task_id, error="cancelled")
        raise RagRetrievalUnavailableError(
            "rag_vector_backend_unavailable",
            "SENTINEL_PRIVATE_CANCELLED_RETRIEVAL",
        )

    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(main_module, "run_registry", registry)
    monkeypatch.setattr(main_module, "resolve_workflow_knowledge_retry_target", fixed_target)
    monkeypatch.setattr(
        main_module,
        "execute_workflow_knowledge_retrieval",
        cancel_then_fail,
    )
    monkeypatch.setenv("WORKFLOW_NODE_RETRIES_ENABLED", "true")
    main_module.request_windows.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/workflow/run",
            json={
                "workflow": _knowledge_retry_workflow(),
                "inputs": {"user_input": "synthetic"},
            },
        )

    assert response.status_code == 200
    events = _events(response.text)
    assert events[-1]["event"] == "workflow_cancelled"
    child_runs = await registry.list_runs(run_type="knowledge_retrieval")
    assert len(child_runs) == 1
    assert child_runs[0].status == "cancelled"
    assert child_runs[0].error == "KNOWLEDGE_RETRIEVAL_CANCELLED"
    checkpoints = await registry.list_checkpoints(child_runs[0].run_id)
    serialized = json.dumps(
        {
            "events": events,
            "child": {
                "status": child_runs[0].status,
                "error": child_runs[0].error,
                "metadata": child_runs[0].metadata,
            },
            "checkpoints": [
                {
                    "event_type": checkpoint.event_type,
                    "summary": checkpoint.summary,
                    "metadata": checkpoint.metadata,
                }
                for checkpoint in checkpoints
            ],
        },
        default=str,
    )
    assert "SENTINEL_PRIVATE_CANCELLED_RETRIEVAL" not in serialized


@pytest.mark.asyncio
async def test_knowledge_retry_target_drift_fails_before_second_retrieval(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    clock = {"now": 1_100.0}
    calls = 0
    target_calls = 0

    def drifting_target(_kb_id: str) -> tuple[str, str]:
        nonlocal target_calls
        target_calls += 1
        if target_calls <= 2:
            return ("a" * 64, "ragv_original")
        return ("b" * 64, "ragv_replacement")

    async def unavailable(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise RagRetrievalUnavailableError(
            "rag_vector_backend_unavailable",
            "SENTINEL_PRIVATE_VECTOR_BACKEND",
        )

    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(main_module.time, "time", lambda: clock["now"])
    monkeypatch.setattr(main_module, "resolve_workflow_knowledge_retry_target", drifting_target)
    monkeypatch.setattr(
        main_module,
        "execute_workflow_knowledge_retrieval",
        unavailable,
    )
    monkeypatch.setenv("WORKFLOW_NODE_RETRIES_ENABLED", "true")
    main_module.request_windows.clear()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _, task_id = await _start(client, _knowledge_retry_workflow())

    clock["now"] = 1_105.0
    failed = await main_module.resume_runtime_due_execution(task_id)
    assert failed["event"] == "error"
    assert failed["code"] == "NODE_RETRY_STATE_INVALID"
    assert calls == 1
    assert target_calls == 4
    persisted = execution_store.require(task_id)
    assert persisted.status == "failed"
    assert "SENTINEL_PRIVATE_VECTOR_BACKEND" not in json.dumps(persisted.events)
    assert "SENTINEL_PRIVATE_VECTOR_BACKEND" not in str(persisted.error)


@pytest.mark.asyncio
async def test_stream_consumer_preserves_legacy_node_error_failure_semantics() -> None:
    async def frames():
        yield (
            'data: {"event":"error","node_id":"legacy-code",'
            '"message":"legacy node fallback"}\n\n'
        )
        yield 'data: {"event":"workflow_end","final_output":"completed"}\n\n'

    response = main_module.StreamingResponse(
        frames(),
        media_type="text/event-stream",
    )
    with pytest.raises(main_module.WorkflowStreamFailure, match="legacy node fallback"):
        await main_module.consume_workflow_stream(response)


@pytest.mark.asyncio
async def test_stream_consumer_retains_explicit_terminal_node_error_metadata() -> None:
    async def frames():
        yield (
            'data: {"event":"error","terminal":true,"node_id":"request",'
            '"code":"HTTP_TIMEOUT","message":"HTTP request timed out."}\n\n'
        )

    response = main_module.StreamingResponse(
        frames(),
        media_type="text/event-stream",
    )
    with pytest.raises(main_module.WorkflowStreamFailure) as raised:
        await main_module.consume_workflow_stream(response)
    assert raised.value.code == "HTTP_TIMEOUT"
