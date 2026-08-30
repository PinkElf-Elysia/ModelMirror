from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import httpx
import pytest

import server.main as main_module
from server.rag.rag_service import (
    RagRetrievalContractError,
    RagRetrievalUnavailableError,
)
from server.workflow_deployments import WorkflowDeploymentStore
from server.workflow_native.error_routing import (
    build_error_receipt,
    route_data_table_error,
    route_http_error,
    route_knowledge_error,
)
from server.workflow_native.schemas import NativeWorkflowDefinition
from server.workflow_native.secure_http import WorkflowHttpRequestError
from server.workflow_native.validate import validate_workflow_graph
from server.xpert_runtime.execution_store import WorkflowExecutionStore


def _events(text: str) -> list[dict]:
    return [
        json.loads(line[5:].strip())
        for line in text.splitlines()
        if line.startswith("data:")
    ]


def _workflow(kind: str, data: dict) -> dict:
    return {
        "id": f"{kind}-error-routing",
        "title": "Error routing",
        "nodes": [
            {
                "id": "input",
                "type": "input",
                "data": {"kind": "input", "variableName": "user_input"},
            },
            {"id": "target", "type": kind, "data": {"kind": kind, **data}},
            {
                "id": "success",
                "type": "output",
                "data": {"kind": "output", "outputVariable": data["outputVariable"]},
            },
            {
                "id": "handled",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "node_error"},
            },
        ],
        "edges": [
            {"id": "e1", "source": "input", "target": "target"},
            {"id": "success-edge", "source": "target", "target": "success"},
            {
                "id": "error-edge",
                "source": "target",
                "sourceHandle": "error",
                "target": "handled",
            },
        ],
    }


def _http_data(**patch: object) -> dict:
    data = {
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
        "failureAction": "error_output",
        "errorVariable": "node_error",
    }
    data.update(patch)
    return data


def _scheduled_http_workflow() -> dict:
    workflow = _workflow("http_request", _http_data())
    workflow["nodes"][0] = {
        "id": "input",
        "type": "scheduled_start",
        "data": {
            "kind": "scheduled_start",
            "scheduleType": "interval",
            "intervalSeconds": 30,
            "timezone": "UTC",
            "eventVariable": "schedule_event",
        },
    }
    return workflow


def _failure_handler_workflow(source_project_id: str) -> dict:
    return {
        "id": "failure-handler",
        "title": "Failure handler",
        "nodes": [
            {
                "id": "failure-entry",
                "type": "failure_event_entry",
                "data": {
                    "kind": "failure_event_entry",
                    "sourceProjectIds": [source_project_id],
                    "eventVariable": "failure_event",
                },
            },
            {
                "id": "failure-output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "failure_event"},
            },
        ],
        "edges": [
            {
                "id": "failure-edge",
                "source": "failure-entry",
                "target": "failure-output",
            }
        ],
    }


def test_error_receipts_use_only_fixed_safe_fields() -> None:
    http = WorkflowHttpRequestError(
        "HTTP_STATUS_NOT_SUCCESSFUL",
        "secret upstream detail",
        status_code=503,
    )
    failure = route_http_error(http)
    assert failure is not None
    receipt = build_error_receipt(
        failure,
        node_id="node-1",
        node_kind="http_request",
    )
    assert receipt == {
        "status": "failed",
        "code": "HTTP_STATUS_NOT_SUCCESSFUL",
        "classification": "transient",
        "nodeId": "node-1",
        "nodeKind": "http_request",
        "attempts": 1,
        "exhausted": True,
        "message": "HTTP service returned an unsuccessful status.",
    }
    assert "secret" not in repr(receipt)
    assert route_http_error(
        WorkflowHttpRequestError("HTTP_PRIVATE_TARGET_FORBIDDEN", "secret")
    ) is None
    assert route_http_error(
        WorkflowHttpRequestError("HTTP_TLS_ERROR", "secret")
    ) is None
    for status_code in (401, 403):
        assert route_http_error(
            WorkflowHttpRequestError(
                "HTTP_STATUS_NOT_SUCCESSFUL",
                "secret",
                status_code=status_code,
            )
        ) is None
    assert route_data_table_error(sqlite3.OperationalError("database is locked"))
    assert route_data_table_error(
        sqlite3.OperationalError("no such table: busy_archive")
    ) is None
    assert route_data_table_error(RuntimeError("database is locked secret")) is None
    assert route_knowledge_error(
        RagRetrievalUnavailableError("rag_vector_index_unavailable", "secret")
    )
    for code in (
        "rag_vector_index_contract_mismatch",
        "rag_embedding_fingerprint_mismatch",
        "rag_embedding_dimension_mismatch",
    ):
        assert route_knowledge_error(RagRetrievalContractError(code, "secret")) is None
    assert route_http_error(
        WorkflowHttpRequestError("HTTP_DNS_UNAVAILABLE", "secret")
    ).classification == "transient"
    assert route_knowledge_error(RuntimeError("secret")) is None


def test_error_output_requires_current_contract_variable_and_exact_edge() -> None:
    workflow = NativeWorkflowDefinition.model_validate(
        _workflow("http_request", _http_data())
    )
    assert validate_workflow_graph(workflow).valid is True

    workflow.edges = [edge for edge in workflow.edges if edge.id != "error-edge"]
    assert "missing_node_error_edge" in {
        issue.code for issue in validate_workflow_graph(workflow).issues
    }

    workflow = NativeWorkflowDefinition.model_validate(
        _workflow("http_request", _http_data(errorVariable="http_response"))
    )
    assert "node_error_variable_conflict" in {
        issue.code for issue in validate_workflow_graph(workflow).issues
    }

    workflow = NativeWorkflowDefinition.model_validate(
        _workflow("http_request", _http_data(errorVariable="错误变量"))
    )
    assert "node_error_variable_invalid" in {
        issue.code for issue in validate_workflow_graph(workflow).issues
    }

    workflow = NativeWorkflowDefinition.model_validate(
        _workflow(
            "http_request",
            _http_data(failureAction="stop", errorVariable=None),
        )
    )
    assert "disabled_node_error_edge" in {
        issue.code for issue in validate_workflow_graph(workflow).issues
    }

    workflow = NativeWorkflowDefinition.model_validate(
        _workflow("http_request", _http_data())
    )
    duplicate = workflow.edges[-1].model_copy(
        update={"id": "error-edge-2", "target": "success"}
    )
    workflow.edges.append(duplicate)
    assert "duplicate_node_error_edge" in {
        issue.code for issue in validate_workflow_graph(workflow).issues
    }


@pytest.mark.asyncio
async def test_direct_run_rejects_invalid_error_routing_before_execution() -> None:
    cases: list[tuple[dict, str]] = []

    missing_edge = _workflow("http_request", _http_data())
    missing_edge["edges"] = [
        edge for edge in missing_edge["edges"] if edge["id"] != "error-edge"
    ]
    cases.append((missing_edge, "missing_node_error_edge"))

    duplicate_edge = _workflow("http_request", _http_data())
    duplicate_edge["edges"].append(
        {
            "id": "error-edge-2",
            "source": "target",
            "sourceHandle": "error",
            "target": "success",
        }
    )
    cases.append((duplicate_edge, "duplicate_node_error_edge"))

    input_conflict = _workflow(
        "http_request", _http_data(errorVariable="user_input")
    )
    cases.append((input_conflict, "duplicate_variable_producer"))

    constant_conflict = _workflow("http_request", _http_data())
    constant_conflict["variables"] = [
        {
            "id": "constant-error",
            "name": "node_error",
            "kind": "constant",
            "valueType": "text",
            "defaultValue": "x",
        }
    ]
    cases.append((constant_conflict, "workflow_variable_producer_conflict"))

    producer_conflict = _workflow("http_request", _http_data())
    producer_conflict["nodes"].insert(
        1,
        {
            "id": "assign",
            "type": "variable_assign",
            "data": {
                "kind": "variable_assign",
                "contractVersion": 2,
                "valueSource": "literal",
                "literalValue": "x",
                "outputVariable": "node_error",
            },
        },
    )
    producer_conflict["edges"][0] = {
        "id": "e1",
        "source": "input",
        "target": "assign",
    }
    producer_conflict["edges"].insert(
        1,
        {"id": "assign-target", "source": "assign", "target": "target"},
    )
    cases.append((producer_conflict, "duplicate_variable_producer"))

    cases.append(
        (
            _workflow("http_request", _http_data(contractVersion=1)),
            "node_error_routing_requires_v2",
        )
    )
    cases.append(
        (
            _workflow(
                "knowledge_retrieval",
                {
                    "contractVersion": 1,
                    "knowledgeBaseId": "kb-1",
                    "queryVariable": "user_input",
                    "top_k": "3",
                    "returnMode": "result",
                    "outputVariable": "knowledge",
                    "failureAction": "error_output",
                    "errorVariable": "node_error",
                },
            ),
            "node_error_routing_requires_v2",
        )
    )

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        for workflow, expected_code in cases:
            main_module.request_windows.clear()
            response = await client.post(
                "/api/workflow/run",
                json={"workflow": workflow, "inputs": {"user_input": "x"}},
            )
            assert response.status_code == 400, response.text
            assert expected_code in {
                issue["code"] for issue in response.json()["issues"]
            }
            assert "event: node_start" not in response.text


@pytest.mark.asyncio
async def test_http_routable_failure_uses_error_edge_and_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_http(*args, **kwargs):
        raise WorkflowHttpRequestError("HTTP_TIMEOUT", "sentinel-secret")

    monkeypatch.setenv("WORKFLOW_HTTP_REQUESTS_ENABLED", "true")
    monkeypatch.setattr(main_module, "execute_workflow_http_request", fail_http)
    main_module.request_windows.clear()
    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/workflow/run",
            json={"workflow": _workflow("http_request", _http_data()), "inputs": {}},
        )
    events = _events(response.text)
    routed = next(event for event in events if event.get("event") == "node_error_routed")
    assert routed["error_code"] == "HTTP_TIMEOUT"
    target_end = next(
        event for event in events if event.get("event") == "node_end" and event.get("node_id") == "target"
    )
    assert target_end["status"] == "handled_error"
    assert target_end["variables"]["node_error"]["code"] == "HTTP_TIMEOUT"
    assert set(target_end["variables"]) == {"node_error"}
    assert any(event.get("event") == "workflow_end" for event in events)
    assert any(event.get("node_id") == "success" and event.get("event") == "node_skipped" for event in events)
    assert "sentinel-secret" not in response.text


@pytest.mark.asyncio
async def test_deployed_handled_error_completes_without_failure_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    async def fail_http(*args, **kwargs):
        raise WorkflowHttpRequestError("HTTP_TIMEOUT", "sentinel-secret")

    deployment_store = WorkflowDeploymentStore(tmp_path / "deployments")
    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(main_module, "workflow_deployment_store", deployment_store)
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setenv("WORKFLOW_HTTP_REQUESTS_ENABLED", "true")
    monkeypatch.setattr(main_module, "execute_workflow_http_request", fail_http)

    source = deployment_store.create_project(_scheduled_http_workflow())
    source_release = deployment_store.publish(source.project_id)
    deployment_store.activate(
        source.project_id,
        source_release.version,
        webhooks_enabled=False,
        http_requests_enabled=True,
        now=100,
    )
    handler = deployment_store.create_project(
        _failure_handler_workflow(source.project_id)
    )
    handler_release = deployment_store.publish(handler.project_id)
    deployment_store.activate(
        handler.project_id,
        handler_release.version,
        webhooks_enabled=False,
        failure_triggers_enabled=True,
        now=101,
    )

    execution = deployment_store.materialize_due_schedules(now=130)[0]
    result = await main_module.run_deployed_workflow_trigger(
        execution,
        source_release,
        {
            "type": "scheduled",
            "scheduledAt": 130,
            "startedAt": 130,
            "timezone": "UTC",
            "occurrenceKey": execution.occurrence_key,
        },
    )
    assert result["status"] == "completed"
    deployment_store.complete_execution(
        execution.execution_id,
        task_id=str(result.get("task_id") or ""),
        run_id=str(result.get("run_id") or ""),
        result=str(result.get("result") or ""),
    )
    assert deployment_store.get_execution(execution.execution_id).status == "completed"
    assert deployment_store.list_executions(handler.project_id) == []


@pytest.mark.asyncio
async def test_security_failure_cannot_use_error_edge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_http(*args, **kwargs):
        raise WorkflowHttpRequestError(
            "HTTP_PRIVATE_TARGET_FORBIDDEN", "sentinel-secret"
        )

    monkeypatch.setenv("WORKFLOW_HTTP_REQUESTS_ENABLED", "true")
    monkeypatch.setattr(main_module, "execute_workflow_http_request", fail_http)
    main_module.request_windows.clear()
    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/workflow/run",
            json={"workflow": _workflow("http_request", _http_data()), "inputs": {}},
        )
    events = _events(response.text)
    error = next(event for event in events if event.get("event") == "error")
    assert error["code"] == "HTTP_PRIVATE_TARGET_FORBIDDEN"
    assert not any(event.get("event") == "node_error_routed" for event in events)
    assert not any(event.get("event") == "workflow_end" for event in events)
    assert "sentinel-secret" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 403])
async def test_http_permission_status_cannot_use_error_edge(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
) -> None:
    async def fail_http(*args, **kwargs):
        raise WorkflowHttpRequestError(
            "HTTP_STATUS_NOT_SUCCESSFUL",
            "sentinel-secret",
            status_code=status_code,
        )

    monkeypatch.setenv("WORKFLOW_HTTP_REQUESTS_ENABLED", "true")
    monkeypatch.setattr(main_module, "execute_workflow_http_request", fail_http)
    main_module.request_windows.clear()
    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/workflow/run",
            json={"workflow": _workflow("http_request", _http_data()), "inputs": {}},
        )
    events = _events(response.text)
    assert not any(event.get("event") == "node_error_routed" for event in events)
    assert not any(event.get("event") == "workflow_end" for event in events)
    assert "sentinel-secret" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code",
    [
        "rag_vector_index_contract_mismatch",
        "rag_embedding_fingerprint_mismatch",
        "rag_embedding_dimension_mismatch",
    ],
)
async def test_knowledge_contract_drift_cannot_use_error_edge(
    monkeypatch: pytest.MonkeyPatch,
    code: str,
) -> None:
    async def fail_knowledge(*args, **kwargs):
        raise RagRetrievalContractError(code, "sentinel-secret")

    monkeypatch.setattr(
        main_module,
        "execute_workflow_knowledge_retrieval",
        fail_knowledge,
    )
    main_module.request_windows.clear()
    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/workflow/run",
            json={
                "workflow": _workflow(
                    "knowledge_retrieval",
                    {
                        "contractVersion": 2,
                        "knowledgeBaseId": "kb-1",
                        "queryVariable": "user_input",
                        "top_k": "3",
                        "returnMode": "result",
                        "outputVariable": "knowledge",
                        "failureAction": "error_output",
                        "errorVariable": "node_error",
                    },
                ),
                "inputs": {"user_input": "x"},
            },
        )
    events = _events(response.text)
    assert not any(event.get("event") == "node_error_routed" for event in events)
    assert not any(event.get("event") == "workflow_end" for event in events)
    assert "sentinel-secret" not in response.text


@pytest.mark.asyncio
async def test_http_success_uses_normal_edge_and_skips_error_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def pass_http(*args, **kwargs):
        return {
            "statusCode": 200,
            "ok": True,
            "contentType": "application/json",
            "headers": {},
            "receivedBytes": 2,
            "body": {},
        }

    monkeypatch.setenv("WORKFLOW_HTTP_REQUESTS_ENABLED", "true")
    monkeypatch.setattr(main_module, "execute_workflow_http_request", pass_http)
    main_module.request_windows.clear()
    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/workflow/run",
            json={"workflow": _workflow("http_request", _http_data()), "inputs": {}},
        )
    events = _events(response.text)
    assert not any(event.get("event") == "node_error_routed" for event in events)
    assert any(
        event.get("node_id") == "handled" and event.get("event") == "node_skipped"
        for event in events
    )
    assert len([
        event for event in events
        if event.get("node_id") == "success" and event.get("event") == "node_end"
    ]) == 1
    assert any(event.get("event") == "workflow_end" for event in events)


@pytest.mark.asyncio
async def test_error_and_success_edges_fanin_only_schedule_target_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_http(*args, **kwargs):
        raise WorkflowHttpRequestError("HTTP_TIMEOUT", "sentinel-secret")

    workflow = _workflow("http_request", _http_data())
    workflow["nodes"] = [
        node for node in workflow["nodes"] if node["id"] not in {"success", "handled"}
    ] + [{
        "id": "join",
        "type": "output",
        "data": {"kind": "output", "outputVariable": "node_error"},
    }]
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "target"},
        {"id": "normal-to-join", "source": "target", "target": "join"},
        {
            "id": "error-to-join",
            "source": "target",
            "sourceHandle": "error",
            "target": "join",
        },
    ]
    monkeypatch.setenv("WORKFLOW_HTTP_REQUESTS_ENABLED", "true")
    monkeypatch.setattr(main_module, "execute_workflow_http_request", fail_http)
    main_module.request_windows.clear()
    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/workflow/run",
            json={"workflow": workflow, "inputs": {}},
        )
    events = _events(response.text)
    assert len([
        event for event in events
        if event.get("node_id") == "join" and event.get("event") == "node_end"
    ]) == 1
    assert "sentinel-secret" not in response.text


@pytest.mark.asyncio
async def test_unknown_read_failures_stop_without_routing_or_raw_detail(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class BrokenTable:
        def resolve_schema_version(self, *args, **kwargs):
            return SimpleNamespace(version=1)

        def query_records(self, *args, **kwargs):
            raise RuntimeError("sentinel-secret table failure")

    async def broken_knowledge(*args, **kwargs):
        raise RuntimeError("sentinel-secret knowledge failure")

    cases = [
        (
            "data_table_query",
            {
                "tableId": "table-1",
                "versionPolicy": "latest",
                "selectFields": [],
                "filter": None,
                "sort": [],
                "limit": 20,
                "returnMode": "list",
                "outputVariable": "records",
                "failureAction": "error_output",
                "errorVariable": "node_error",
            },
        ),
        (
            "knowledge_retrieval",
            {
                "contractVersion": 2,
                "knowledgeBaseId": "kb-1",
                "queryVariable": "user_input",
                "top_k": "3",
                "returnMode": "result",
                "outputVariable": "knowledge",
                "failureAction": "error_output",
                "errorVariable": "node_error",
            },
        ),
    ]
    monkeypatch.setattr(main_module, "agent_table_store", BrokenTable())
    monkeypatch.setattr(main_module, "execute_workflow_knowledge_retrieval", broken_knowledge)
    for kind, data in cases:
        main_module.request_windows.clear()
        transport = httpx.ASGITransport(app=main_module.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/workflow/run",
                json={"workflow": _workflow(kind, data), "inputs": {"user_input": "x"}},
            )
        events = _events(response.text)
        assert not any(event.get("event") == "node_error_routed" for event in events)
        assert not any(event.get("event") == "workflow_end" for event in events)
        assert "sentinel-secret" not in response.text
    assert "sentinel-secret" not in caplog.text


@pytest.mark.asyncio
async def test_data_table_busy_and_knowledge_unavailable_are_routable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BusyTable:
        def resolve_schema_version(self, *args, **kwargs):
            raise sqlite3.OperationalError("database is locked")

        def query_records(self, *args, **kwargs):
            raise AssertionError("query must not run after schema resolution fails")

    async def unavailable(*args, **kwargs):
        raise RagRetrievalUnavailableError(
            "rag_vector_index_unavailable", "sentinel-secret"
        )

    cases = [
        (
            "data_table_query",
            {
                "tableId": "table-1",
                "versionPolicy": "latest",
                "selectFields": [],
                "filter": None,
                "sort": [],
                "limit": 20,
                "returnMode": "list",
                "outputVariable": "records",
                "failureAction": "error_output",
                "errorVariable": "node_error",
            },
            "DATA_TABLE_QUERY_BUSY",
        ),
        (
            "knowledge_retrieval",
            {
                "contractVersion": 2,
                "knowledgeBaseId": "kb-1",
                "queryVariable": "user_input",
                "top_k": "3",
                "returnMode": "result",
                "outputVariable": "knowledge",
                "failureAction": "error_output",
                "errorVariable": "node_error",
            },
            "KNOWLEDGE_RETRIEVAL_UNAVAILABLE",
        ),
    ]
    monkeypatch.setattr(main_module, "agent_table_store", BusyTable())
    monkeypatch.setattr(main_module, "execute_workflow_knowledge_retrieval", unavailable)
    for kind, data, expected_code in cases:
        main_module.request_windows.clear()
        transport = httpx.ASGITransport(app=main_module.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/workflow/run",
                json={"workflow": _workflow(kind, data), "inputs": {"user_input": "x"}},
            )
        assert response.status_code == 200
        events = _events(response.text)
        routed = next(event for event in events if event.get("event") == "node_error_routed")
        assert routed["error_code"] == expected_code
        assert any(event.get("event") == "workflow_end" for event in events)
        assert "sentinel-secret" not in response.text
