from __future__ import annotations

import json

import httpx
import pytest
import pytest_asyncio

import server.main as main_module
from server.main import WorkflowRunRequest, app
from server.workflow_deployments import (
    WorkflowDeploymentValidationError,
    validate_publishable_workflow,
)
from server.workflow_native.r20_nodes import (
    WorkflowR20NodeError,
    execute_code_v2,
    validate_code_v2_config,
)


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client


def _data(operation: str = "upper", **patch: object) -> dict[str, object]:
    return {
        "kind": "code",
        "contractVersion": 2,
        "operation": operation,
        "inputVariable": "source_value",
        "outputVariable": "clean_value",
        "replaceFrom": "旧",
        "replaceTo": "新",
        "concatValue": "！",
        **patch,
    }


def _workflow(node_data: dict[str, object]) -> dict[str, object]:
    output_variable = str(
        node_data.get("outputVariable")
        or node_data.get("codeOutputVariable")
        or "clean_value"
    )
    return {
        "id": "safe-text-v2",
        "title": "Safe text V2",
        "nodes": [
            {
                "id": "input",
                "type": "input",
                "data": {"kind": "input", "variableName": "source_value"},
            },
            {"id": "text", "type": "code", "data": node_data},
            {
                "id": "output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": output_variable},
            },
        ],
        "edges": [
            {"id": "e1", "source": "input", "target": "text"},
            {"id": "e2", "source": "text", "target": "output"},
        ],
    }


def _parse_sse(text: str) -> list[dict[str, object]]:
    return [
        json.loads(line[5:].strip())
        for line in text.splitlines()
        if line.startswith("data:")
    ]


@pytest.mark.parametrize(
    ("operation", "value", "patch", "expected"),
    [
        ("upper", "Straße 中文", {}, "STRASSE 中文"),
        ("lower", "ÄBC 中文", {}, "äbc 中文"),
        ("replace", "旧值与旧值", {}, "新值与新值"),
        ("concat", {"b": [2, None], "a": True}, {}, '{"b":[2,null],"a":true}！'),
    ],
)
def test_execute_code_v2_uses_safe_deterministic_operations(
    operation: str,
    value: object,
    patch: dict[str, object],
    expected: str,
) -> None:
    variables = {"source_value": value}

    output_variable, output = execute_code_v2(_data(operation, **patch), variables)

    assert output_variable == "clean_value"
    assert output == expected
    assert variables == {"source_value": value}


@pytest.mark.parametrize(
    ("patch", "expected_code"),
    [
        ({"operation": "python", "pythonCode": "print(input)"}, "CODE_LEGACY_FIELD_FORBIDDEN"),
        ({"pythonCode": "print(input)"}, "CODE_LEGACY_FIELD_FORBIDDEN"),
        ({"codeOperation": "upper"}, "CODE_LEGACY_FIELD_FORBIDDEN"),
        ({"operation": "eval"}, "CODE_OPERATION_INVALID"),
        ({"operation": " upper "}, "CODE_OPERATION_INVALID"),
        ({"inputVariable": True}, "CODE_INPUT_VARIABLE_INVALID"),
        ({"outputVariable": True}, "CODE_OUTPUT_VARIABLE_INVALID"),
    ],
)
def test_code_v2_rejects_python_and_legacy_field_bypasses(
    patch: dict[str, object],
    expected_code: str,
) -> None:
    with pytest.raises(WorkflowR20NodeError) as caught:
        validate_code_v2_config(_data(**patch))

    assert caught.value.code == expected_code
    assert "print(input)" not in caught.value.safe_message


@pytest.mark.parametrize("invalid_version", ["2", "02", "+2", 2.9, True])
def test_code_v2_rejects_noncanonical_contract_versions(
    invalid_version: object,
) -> None:
    with pytest.raises(WorkflowR20NodeError) as caught:
        validate_code_v2_config(_data(contractVersion=invalid_version))

    assert caught.value.code == "CODE_CONTRACT_VERSION_INVALID"


class _ExplosiveReplaceString(str):
    def replace(self, _old: str, _new: str, _count: int = -1) -> str:
        raise AssertionError("replace must not run after the output is known to exceed 5 MiB")


def test_code_v2_rejects_oversized_replace_before_allocating_result() -> None:
    variables = {"source_value": _ExplosiveReplaceString("a" * 60)}

    with pytest.raises(WorkflowR20NodeError) as caught:
        execute_code_v2(
            _data("replace", replaceFrom="a", replaceTo="x" * 100_000),
            variables,
        )

    assert caught.value.code == "CODE_OUTPUT_TOO_LARGE"


def test_code_v2_rejects_invalid_unicode_without_leaking_runtime_exception() -> None:
    variables = {"source_value": "\ud800"}

    with pytest.raises(WorkflowR20NodeError) as caught:
        execute_code_v2(_data("upper"), variables)

    assert caught.value.code == "CODE_OUTPUT_INVALID"
    assert "surrogate" not in caught.value.safe_message.lower()


def test_code_v2_fails_before_assignment_for_missing_or_oversized_output() -> None:
    variables: dict[str, object] = {}
    with pytest.raises(WorkflowR20NodeError) as missing:
        execute_code_v2(_data(), variables)
    assert missing.value.code == "CODE_INPUT_VARIABLE_UNAVAILABLE"
    assert variables == {}

    variables = {"source_value": "a" * (5 * 1_024 * 1_024)}
    with pytest.raises(WorkflowR20NodeError) as oversized:
        execute_code_v2(_data("concat", concatValue="b"), variables)
    assert oversized.value.code == "CODE_OUTPUT_TOO_LARGE"
    assert set(variables) == {"source_value"}


@pytest.mark.asyncio
async def test_code_v2_runtime_emits_typed_output_without_python(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/workflow/run",
        json={
            "workflow": _workflow(_data("upper")),
            "inputs": {"source_value": ["中文", 2, True]},
        },
    )

    assert response.status_code == 200, response.text
    events = _parse_sse(response.text)
    completed = next(event for event in events if event.get("event") == "workflow_end")
    assert completed["variables"]["clean_value"] == '["中文",2,TRUE]'
    assert completed["final_output"] == '["中文",2,TRUE]'


@pytest.mark.asyncio
async def test_code_v2_direct_run_rejects_string_contract_version(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/workflow/run",
        json={
            "workflow": _workflow(_data("upper", contractVersion="2")),
            "inputs": {"source_value": "must not run"},
        },
    )

    assert response.status_code == 200, response.text
    events = _parse_sse(response.text)
    error = next(event for event in events if event.get("event") == "error")
    assert error["code"] == "CODE_CONTRACT_VERSION_INVALID"
    assert not any(event.get("node_id") == "output" for event in events)
    assert not any(event.get("event") == "workflow_end" for event in events)


@pytest.mark.asyncio
async def test_code_v2_static_validation_accepts_contract_and_rejects_legacy_fields(
    client: httpx.AsyncClient,
) -> None:
    valid = await client.post(
        "/api/workflow-native/validate",
        json={"workflow": _workflow(_data("replace"))},
    )
    assert valid.status_code == 200, valid.text
    assert valid.json()["valid"] is True, valid.json()["issues"]

    invalid = await client.post(
        "/api/workflow-native/validate",
        json={
            "workflow": _workflow(
                _data("upper", pythonCode="UNIQUE_STATIC_PYTHON_SENTINEL")
            )
        },
    )
    assert invalid.status_code == 200, invalid.text
    payload = invalid.json()
    assert payload["valid"] is False
    assert "code_legacy_field_forbidden" in {
        issue["code"] for issue in payload["issues"]
    }
    assert "UNIQUE_STATIC_PYTHON_SENTINEL" not in invalid.text


@pytest.mark.asyncio
async def test_code_v2_runtime_fails_closed_without_running_downstream(
    client: httpx.AsyncClient,
) -> None:
    workflow = _workflow(_data("upper", pythonCode="UNIQUE_PYTHON_SENTINEL"))
    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"source_value": "secret input"}},
    )

    assert response.status_code == 200, response.text
    events = _parse_sse(response.text)
    error = next(event for event in events if event.get("event") == "error")
    assert error["code"] == "CODE_LEGACY_FIELD_FORBIDDEN"
    assert error["node_id"] == "text"
    serialized_error = json.dumps(error, ensure_ascii=False)
    assert "UNIQUE_PYTHON_SENTINEL" not in serialized_error
    assert "secret input" not in serialized_error
    assert not any(event.get("node_id") == "output" for event in events)
    assert not any(event.get("event") == "workflow_end" for event in events)


@pytest.mark.asyncio
async def test_legacy_python_cannot_bypass_static_validation_via_manual_run(
    client: httpx.AsyncClient,
) -> None:
    workflow = _workflow(
        {
            "kind": "code",
            "codeOperation": "python",
            "codeInputVariable": "source_value",
            "codeOutputVariable": "clean_value",
            "pythonCode": "print('UNIQUE_LEGACY_PYTHON_SENTINEL')",
        }
    )
    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"source_value": "safe input"}},
    )

    assert response.status_code == 200, response.text
    events = _parse_sse(response.text)
    error = next(event for event in events if event.get("event") == "error")
    assert error["code"] == "LEGACY_CODE_MANUAL_RUN_FORBIDDEN"
    assert error["node_id"] == "text"
    assert "UNIQUE_LEGACY_PYTHON_SENTINEL" not in json.dumps(
        error, ensure_ascii=False
    )
    assert not any(event.get("node_id") == "output" for event in events)
    assert not any(event.get("event") == "workflow_end" for event in events)


@pytest.mark.asyncio
async def test_legacy_safe_text_operation_remains_manually_runnable(
    client: httpx.AsyncClient,
) -> None:
    workflow = _workflow(
        {
            "kind": "code",
            "codeOperation": "lower",
            "codeInputVariable": "source_value",
            "codeOutputVariable": "clean_value",
        }
    )
    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"source_value": "ÄBC 中文"}},
    )

    assert response.status_code == 200, response.text
    final_event = next(
        event for event in _parse_sse(response.text) if event.get("event") == "workflow_end"
    )
    assert final_event["variables"]["clean_value"] == "äbc 中文"


@pytest.mark.asyncio
async def test_legacy_python_published_snapshot_keeps_historical_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = _workflow(
        {
            "kind": "code",
            "codeOperation": "python",
            "codeInputVariable": "source_value",
            "codeOutputVariable": "clean_value",
            "pythonCode": "print(input)",
        }
    )
    monkeypatch.setattr(
        main_module,
        "run_safe_code_node",
        lambda _node, _variables: "legacy snapshot output",
    )
    response = await main_module._run_workflow_response(
        WorkflowRunRequest.model_validate(
            {"workflow": workflow, "inputs": {"source_value": "historical"}}
        ),
        None,
        runtime_execution_source_kind="workflow_deployment",
    )

    final_event = await main_module.consume_workflow_stream(response)

    assert final_event["event"] == "workflow_end"
    assert final_event["variables"]["clean_value"] == "legacy snapshot output"


def test_publish_gate_accepts_v2_and_rejects_all_legacy_text_nodes() -> None:
    trigger_kind, entry_node_id = validate_publishable_workflow(
        _workflow(_data("upper"))
    )
    assert trigger_kind == "manual"
    assert entry_node_id == "input"

    legacy_code = {
        "kind": "code",
        "codeOperation": "upper",
        "codeInputVariable": "source_value",
        "codeOutputVariable": "clean_value",
    }
    with pytest.raises(WorkflowDeploymentValidationError, match="explicitly migrated"):
        validate_publishable_workflow(_workflow(legacy_code))

    legacy_template = {
        "kind": "template_transform",
        "template": "{{source_value}}",
        "outputVariable": "clean_value",
    }
    with pytest.raises(WorkflowDeploymentValidationError, match="variable_assign"):
        validate_publishable_workflow(_workflow(legacy_template))
