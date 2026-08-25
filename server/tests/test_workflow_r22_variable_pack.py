from __future__ import annotations

import json

import httpx
import pytest
import pytest_asyncio

from server.main import app
from server.workflow_deployments import (
    WorkflowDeploymentValidationError,
    validate_publishable_workflow,
)
from server.workflow_native.r20_nodes import (
    MAX_WORKFLOW_NODE_OUTPUT_BYTES,
    WorkflowR20NodeError,
    execute_variable_aggregator_v2,
    validate_variable_aggregator_v2_config,
)


@pytest_asyncio.fixture
async def client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client


def _data(**patch: object) -> dict[str, object]:
    return {
        "kind": "variable_aggregator",
        "contractVersion": 2,
        "bindings": [
            {
                "id": "binding_customer",
                "sourceVariable": "customer",
                "outputField": "customer_snapshot",
            },
            {
                "id": "binding_order",
                "sourceVariable": "order",
                "outputField": "order_snapshot",
            },
        ],
        "outputVariable": "packed",
        **patch,
    }


def _workflow(node_data: dict[str, object]) -> dict[str, object]:
    return {
        "id": "variable-pack-v2",
        "title": "Variable pack V2",
        "variables": [
            {"id": "input-customer", "name": "customer", "kind": "input", "valueType": "json"},
            {"id": "input-order", "name": "order", "kind": "input", "valueType": "json"},
        ],
        "nodes": [
            {
                "id": "input",
                "type": "input",
                "data": {"kind": "input", "variableName": "user_input"},
            },
            {"id": "pack", "type": "variable_aggregator", "data": node_data},
            {
                "id": "output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "packed"},
            },
        ],
        "edges": [
            {"id": "e1", "source": "input", "target": "pack"},
            {"id": "e2", "source": "pack", "target": "output"},
        ],
    }


def _events(response: httpx.Response) -> list[dict]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def test_variable_pack_v2_preserves_typed_values_and_deep_copies() -> None:
    customer = {"name": "Ada", "flags": [True, None]}
    order = [1, {"amount": 19.5}]
    variables = {"customer": customer, "order": order}

    output_variable, result = execute_variable_aggregator_v2(_data(), variables)

    assert output_variable == "packed"
    assert result == {
        "customer_snapshot": customer,
        "order_snapshot": order,
    }
    assert result["customer_snapshot"] is not customer
    assert result["order_snapshot"] is not order
    result["customer_snapshot"]["flags"].append(False)  # type: ignore[index]
    assert customer == {"name": "Ada", "flags": [True, None]}


@pytest.mark.parametrize(
    ("patch", "code"),
    [
        ({"contractVersion": "2"}, "VARIABLE_PACK_CONTRACT_VERSION_INVALID"),
        ({"bindings": []}, "VARIABLE_PACK_BINDINGS_INVALID"),
        (
            {"bindings": [
                {"id": "same", "sourceVariable": "customer", "outputField": "first"},
                {"id": "same", "sourceVariable": "order", "outputField": "second"},
            ]},
            "VARIABLE_PACK_BINDING_ID_DUPLICATE",
        ),
        (
            {"bindings": [
                {"id": "first", "sourceVariable": "customer", "outputField": "same"},
                {"id": "second", "sourceVariable": "order", "outputField": "same"},
            ]},
            "VARIABLE_PACK_OUTPUT_FIELD_DUPLICATE",
        ),
        ({"outputVariable": "customer"}, "VARIABLE_PACK_OUTPUT_OVERLAPS_INPUT"),
    ],
)
def test_variable_pack_v2_rejects_invalid_contracts_before_execution(
    patch: dict[str, object],
    code: str,
) -> None:
    with pytest.raises(WorkflowR20NodeError) as caught:
        validate_variable_aggregator_v2_config(_data(**patch))
    assert caught.value.code == code
    if code == "VARIABLE_PACK_OUTPUT_FIELD_DUPLICATE":
        assert caught.value.safe_message == "变量打包输出字段不能重复。"


def test_variable_pack_v2_enforces_binding_and_output_boundaries() -> None:
    fifty = [
        {"id": f"binding_{index}", "sourceVariable": f"source_{index}", "outputField": f"field_{index}"}
        for index in range(1, 51)
    ]
    variables = {f"source_{index}": index for index in range(1, 51)}
    _, result = execute_variable_aggregator_v2(_data(bindings=fifty), variables)
    assert len(result) == 50

    with pytest.raises(WorkflowR20NodeError) as too_many:
        validate_variable_aggregator_v2_config(
            _data(bindings=[
                *fifty,
                {"id": "binding_51", "sourceVariable": "source_51", "outputField": "field_51"},
            ])
        )
    assert too_many.value.code == "VARIABLE_PACK_BINDINGS_INVALID"

    empty_result_size = len(json.dumps(
        {"customer_snapshot": "", "order_snapshot": None},
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8"))
    exact_size_value = "x" * (
        MAX_WORKFLOW_NODE_OUTPUT_BYTES - empty_result_size
    )
    _, exact_result = execute_variable_aggregator_v2(
        _data(),
        {"customer": exact_size_value, "order": None},
    )
    assert exact_result["customer_snapshot"] == exact_size_value

    sentinel = "MM_R22_SECRET_SENTINEL"
    oversized = {
        "customer": f"{sentinel}{exact_size_value}",
        "order": None,
    }
    with pytest.raises(WorkflowR20NodeError) as too_large:
        execute_variable_aggregator_v2(_data(), oversized)
    assert too_large.value.code == "VARIABLE_PACK_OUTPUT_TOO_LARGE"
    assert sentinel not in too_large.value.safe_message


def test_variable_pack_v2_fails_before_assignment_for_missing_or_nonfinite_values() -> None:
    variables = {"customer": {"name": "Ada"}}
    with pytest.raises(WorkflowR20NodeError) as missing:
        execute_variable_aggregator_v2(_data(), variables)
    assert missing.value.code == "VARIABLE_PACK_SOURCE_UNAVAILABLE"
    assert set(variables) == {"customer"}

    with pytest.raises(ValueError, match="finite"):
        execute_variable_aggregator_v2(
            _data(),
            {"customer": float("nan"), "order": None},
        )


@pytest.mark.asyncio
async def test_variable_pack_v2_static_and_runtime_contract(client: httpx.AsyncClient) -> None:
    workflow = _workflow(_data())
    validation = await client.post(
        "/api/workflow-native/validate",
        json={"workflow": workflow},
    )
    assert validation.status_code == 200, validation.text
    assert validation.json()["valid"] is True, validation.json()["issues"]

    response = await client.post(
        "/api/workflow/run",
        json={
            "workflow": workflow,
            "inputs": {
                "customer": {"name": "Ada", "active": True},
                "order": [1, None, {"amount": 19.5}],
            },
        },
    )
    assert response.status_code == 200, response.text
    completed = next(
        event for event in _events(response) if event.get("event") == "workflow_end"
    )
    assert completed["variables"]["packed"] == {
        "customer_snapshot": {"name": "Ada", "active": True},
        "order_snapshot": [1, None, {"amount": 19.5}],
    }
    assert completed["final_output"] == (
        '{"customer_snapshot":{"name":"Ada","active":true},'
        '"order_snapshot":[1,null,{"amount":19.5}]}'
    )


@pytest.mark.asyncio
async def test_variable_pack_v2_runtime_fails_closed_without_downstream_execution(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/workflow/run",
        json={
            "workflow": _workflow(_data()),
            "inputs": {"customer": {"name": "Ada"}},
        },
    )
    events = _events(response)
    error = next(event for event in events if event.get("event") == "error")
    assert error["code"] == "VARIABLE_PACK_SOURCE_UNAVAILABLE"
    assert error["message"] == "变量打包的来源变量不可用。"
    assert not any(event.get("node_id") == "output" for event in events)
    assert not any(event.get("event") == "workflow_end" for event in events)


@pytest.mark.asyncio
async def test_variable_pack_output_cannot_overwrite_a_declared_constant(
    client: httpx.AsyncClient,
) -> None:
    workflow = _workflow(_data())
    workflow["variables"].append({
        "id": "constant-packed",
        "name": "packed",
        "kind": "constant",
        "valueType": "json",
        "defaultValue": {"protected": True},
    })

    validation = await client.post(
        "/api/workflow-native/validate",
        json={"workflow": workflow},
    )

    assert validation.status_code == 200, validation.text
    assert validation.json()["valid"] is False
    assert any(
        issue["code"] == "workflow_variable_producer_conflict"
        for issue in validation.json()["issues"]
    )


@pytest.mark.asyncio
async def test_legacy_variable_aggregator_still_runs_manually(
    client: httpx.AsyncClient,
) -> None:
    legacy = _workflow({
        "kind": "variable_aggregator",
        "variableNames": "customer, order",
        "outputTemplate": "",
        "outputVariable": "packed",
    })
    response = await client.post(
        "/api/workflow/run",
        json={
            "workflow": legacy,
            "inputs": {
                "customer": {"name": "Ada"},
                "order": [1, None],
            },
        },
    )

    assert response.status_code == 200, response.text
    completed = next(
        event for event in _events(response) if event.get("event") == "workflow_end"
    )
    assert completed["variables"]["packed"] == {
        "customer": {"name": "Ada"},
        "order": [1, None],
    }


@pytest.mark.asyncio
async def test_legacy_all_string_aggregate_keeps_json_string_output(
    client: httpx.AsyncClient,
) -> None:
    legacy = _workflow({
        "kind": "variable_aggregator",
        "variableNames": "customer, order",
        "outputTemplate": "",
        "outputVariable": "packed",
    })
    response = await client.post(
        "/api/workflow/run",
        json={
            "workflow": legacy,
            "inputs": {"customer": "Ada", "order": "A-17"},
        },
    )

    completed = next(
        event for event in _events(response) if event.get("event") == "workflow_end"
    )
    assert completed["variables"]["packed"] == (
        '{"customer": "Ada", "order": "A-17"}'
    )


def test_legacy_variable_aggregator_is_manual_only_and_cannot_publish() -> None:
    legacy = _workflow({
        "kind": "variable_aggregator",
        "variableNames": "customer, order",
        "outputTemplate": "",
        "outputVariable": "packed",
    })
    with pytest.raises(WorkflowDeploymentValidationError, match="explicitly migrated"):
        validate_publishable_workflow(legacy)

    trigger_kind, entry_node_id = validate_publishable_workflow(_workflow(_data()))
    assert trigger_kind == "manual"
    assert entry_node_id == "input"
