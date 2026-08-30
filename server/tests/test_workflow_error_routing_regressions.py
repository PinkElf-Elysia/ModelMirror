from __future__ import annotations

import json
import sqlite3

import httpx
import pytest

import server.main as main_module
from server.workflow_native.error_routing import route_data_table_error, route_http_error
from server.workflow_native.secure_http import (
    WorkflowHttpRequestError,
    _resolve_fixed_public_dns,
    execute_workflow_http_request,
)


def _http_config(**patch: object) -> dict[str, object]:
    return {
        "kind": "http_request",
        "contractVersion": 2,
        "method": "GET",
        "url": "https://api.example.test/items",
        "queryItems": [],
        "headerItems": [],
        "bodyMode": "none",
        "formFields": [],
        "authType": "none",
        "credentialId": "",
        "timeoutSeconds": 30,
        "redirectLimit": 0,
        "responseLimitBytes": 1024,
        "responseMode": "auto",
        "statusPolicy": "success_only",
        "outputVariable": "http_response",
        **patch,
    }


class _Credentials:
    def get_public(self, credential_id: str):
        return type(
            "Credential",
            (),
            {"credential_id": credential_id, "kind": "generic", "status": "active"},
        )()

    def resolve(self, credential_id: str) -> str:
        return "unused"


async def _allow_public(url: str, policy: str) -> tuple[str, ...]:
    assert policy == "public_only"
    return ("93.184.216.34",)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "content", "headers"),
    [
        (401, b"not-json", {"Content-Type": "application/json"}),
        (403, b"\x00\x01\x02", {"Content-Type": "application/octet-stream"}),
        (401, b"x" * 1025, {"Content-Type": "text/plain"}),
    ],
)
async def test_success_only_rejects_status_before_untrusted_body(
    status_code: int,
    content: bytes,
    headers: dict[str, str],
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, headers=headers, content=content)

    with pytest.raises(WorkflowHttpRequestError) as raised:
        await execute_workflow_http_request(
            _http_config(),
            {},
            _Credentials(),
            transport=httpx.MockTransport(handler),
            url_validator=_allow_public,
        )

    assert raised.value.code == "HTTP_STATUS_NOT_SUCCESSFUL"
    assert raised.value.status_code == status_code


@pytest.mark.asyncio
async def test_capture_all_keeps_response_parsing_contract() -> None:
    async def valid_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            headers={"Content-Type": "application/json"},
            content=b'{"retryable":true}',
        )

    result = await execute_workflow_http_request(
        _http_config(statusPolicy="capture_all"),
        {},
        _Credentials(),
        transport=httpx.MockTransport(valid_handler),
        url_validator=_allow_public,
    )
    assert result["statusCode"] == 503
    assert result["ok"] is False
    assert result["body"] == {"retryable": True}

    async def invalid_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            headers={"Content-Type": "application/json"},
            content=b"not-json",
        )

    with pytest.raises(WorkflowHttpRequestError) as raised:
        await execute_workflow_http_request(
            _http_config(statusPolicy="capture_all"),
            {},
            _Credentials(),
            transport=httpx.MockTransport(invalid_handler),
            url_validator=_allow_public,
        )
    assert raised.value.code == "HTTP_RESPONSE_JSON_INVALID"


@pytest.mark.asyncio
async def test_fixed_dns_transport_failure_is_typed_as_transient_unavailable() -> None:
    async def unavailable_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("resolver unavailable", request=request)

    with pytest.raises(WorkflowHttpRequestError) as raised:
        await _resolve_fixed_public_dns(
            "example.test",
            transport=httpx.MockTransport(unavailable_handler),
        )
    assert raised.value.code == "HTTP_DNS_UNAVAILABLE"
    routed = route_http_error(raised.value)
    assert routed is not None
    assert routed.classification == "transient"


def _routing_workflow(kind: str = "http_request", **patch: object) -> dict:
    from test_workflow_error_routing import _http_data, _workflow

    if kind == "http_request":
        return _workflow(kind, _http_data(**patch))
    return _workflow(kind, patch)


async def _post_run(workflow: dict) -> httpx.Response:
    main_module.request_windows.clear()
    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(
            "/api/workflow/run",
            json={"workflow": workflow, "inputs": {}},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["missing", "duplicate"])
async def test_run_preflight_rejects_invalid_error_edges(case: str) -> None:
    workflow = _routing_workflow()
    if case == "missing":
        workflow["edges"] = [edge for edge in workflow["edges"] if edge["id"] != "error-edge"]
    else:
        workflow["edges"].append(
            {
                "id": "error-edge-2",
                "source": "target",
                "sourceHandle": "error",
                "target": "success",
            }
        )

    response = await _post_run(workflow)
    assert response.status_code == 400
    body = response.json()
    assert any(
        issue["code"]
        == ("missing_node_error_edge" if case == "missing" else "duplicate_node_error_edge")
        for issue in body["issues"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "workflow_factory",
    [
        lambda: _routing_workflow(errorVariable="user_input"),
        lambda: _constant_conflict_workflow(),
        lambda: _producer_conflict_workflow(),
    ],
)
async def test_run_preflight_rejects_error_variable_producer_conflicts(workflow_factory) -> None:
    response = await _post_run(workflow_factory())
    assert response.status_code == 400
    body = response.json()
    assert any(
        issue["code"] in {"workflow_variable_producer_conflict", "duplicate_variable_producer"}
        for issue in body["issues"]
    )


def _constant_conflict_workflow() -> dict:
    workflow = _routing_workflow()
    workflow["variables"] = [
        {
            "id": "fixed-error",
            "name": "node_error",
            "kind": "constant",
            "valueType": "text",
            "defaultValue": "fixed",
        }
    ]
    return workflow


def _producer_conflict_workflow() -> dict:
    workflow = _routing_workflow()
    workflow["nodes"].append(
        {
            "id": "other-producer",
            "type": "variable_assign",
            "data": {
                "kind": "variable_assign",
                "contractVersion": 2,
                "valueSource": "literal",
                "literalValue": "x",
                "outputVariable": "node_error",
            },
        }
    )
    workflow["edges"].append(
        {"id": "other-producer-edge", "source": "input", "target": "other-producer"}
    )
    return workflow


@pytest.mark.asyncio
async def test_run_preflight_rejects_error_output_on_v1_http_and_knowledge() -> None:
    http_v1 = _routing_workflow(contractVersion=1)
    knowledge_v1 = _routing_workflow(
        "knowledge_retrieval",
        contractVersion=1,
        knowledgeBaseId="kb-1",
        queryVariable="user_input",
        top_k="3",
        returnMode="result",
        outputVariable="knowledge",
        failureAction="error_output",
        errorVariable="node_error",
    )

    for workflow in (http_v1, knowledge_v1):
        response = await _post_run(workflow)
        assert response.status_code == 400
        assert any(
            issue["code"] == "node_error_routing_requires_v2"
            for issue in response.json()["issues"]
        )


def test_data_table_only_routes_exact_sqlite_busy_errors() -> None:
    assert route_data_table_error(sqlite3.OperationalError("database is locked")) is not None
    assert route_data_table_error(sqlite3.OperationalError("database table is busy")) is not None
    assert route_data_table_error(
        sqlite3.OperationalError("no such table: busy_archive")
    ) is None


def test_dns_unavailable_is_routable_but_private_target_is_fatal() -> None:
    routed = route_http_error(
        WorkflowHttpRequestError(
            "HTTP_DNS_UNAVAILABLE",
            "temporary resolver failure",
        )
    )
    assert routed is not None
    assert routed.classification == "transient"
    assert route_http_error(
        WorkflowHttpRequestError(
            "HTTP_PRIVATE_TARGET_FORBIDDEN",
            "private target",
        )
    ) is None
