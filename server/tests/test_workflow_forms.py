from __future__ import annotations

import asyncio
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from jsonschema import Draft202012Validator

import server.api.workflow_deployments as deployment_api
import server.main as main_module
from server.workflow_deployments import (
    WorkflowDeploymentConflictError,
    WorkflowDeploymentStore,
    WorkflowDeploymentValidationError,
)
from server.workflow_forms import (
    WorkflowFormError,
    form_schema_checksum,
    issue_submission_token,
    loads_strict_json,
    validate_form_config,
    validate_public_base_url,
    validate_submission,
    verify_submission_token,
)
from server.workflow_native.node_contracts import workflow_node_contract_registry
from server.xpert_runtime.execution_store import WorkflowExecutionStore


def form_entry_data() -> dict:
    return {
        "kind": "form_event_entry",
        "contractVersion": 1,
        "formTitle": "需求登记",
        "formDescription": "请填写以下内容。",
        "submitLabel": "提交登记",
        "privacyNotice": "内容只用于本次处理。",
        "successTitle": "已收到",
        "successMessage": "可以关闭页面。",
        "theme": "light",
        "eventVariable": "form_event",
        "submissionVariable": "form_submission",
        "fields": [
            {
                "id": "field_name",
                "outputVariable": "name",
                "label": "姓名",
                "helpText": "",
                "placeholder": "请输入姓名",
                "type": "short_text",
                "required": True,
                "options": [],
            },
            {
                "id": "field_priority",
                "outputVariable": "priority",
                "label": "优先级",
                "helpText": "",
                "placeholder": "",
                "type": "single_select",
                "required": True,
                "options": [
                    {"id": "option_normal", "value": "normal", "label": "普通"},
                    {"id": "option_urgent", "value": "urgent", "label": "紧急"},
                ],
            },
            {
                "id": "field_consent",
                "outputVariable": "consent",
                "label": "确认",
                "helpText": "",
                "placeholder": "确认提交",
                "type": "boolean",
                "required": True,
                "options": [],
            },
        ],
    }


def form_workflow(*, waiting_kind: str | None = None) -> dict:
    nodes = [
        {
            "id": "entry",
            "type": "form_event_entry",
            "data": form_entry_data(),
        }
    ]
    if waiting_kind:
        waiting_data = {
            "kind": waiting_kind,
            "outputVariable": "wait_result",
        }
        if waiting_kind == "suspend_wait":
            waiting_data.update({"waitMode": "duration", "durationSeconds": 30})
        nodes.append({"id": "wait", "type": waiting_kind, "data": waiting_data})
    nodes.append(
        {
            "id": "output",
            "type": "output",
            "data": {"kind": "output", "outputVariable": "name"},
        }
    )
    return {
        "id": "draft",
        "title": "signed form",
        "nodes": nodes,
        "edges": [
            {
                "id": f"edge_{index}",
                "source": nodes[index]["id"],
                "target": nodes[index + 1]["id"],
            }
            for index in range(len(nodes) - 1)
        ],
        "variables": [],
    }


def test_form_contract_is_complete_deployment_only_and_strict() -> None:
    contract = workflow_node_contract_registry.require("form_event_entry")
    assert contract.contract_status == "complete"
    assert contract.execution.external_io is True
    assert contract.execution.can_wait is False
    assert contract.execution.error_semantics == "fail_closed"
    assert contract.planner.enabled is False
    assert contract.availability.workflow.state == "allow"
    for context in ("xpert", "goal", "handoff", "app", "evaluation", "evolution"):
        assert getattr(contract.availability, context).state == "deny"
    assert not list(Draft202012Validator(contract.config_schema).iter_errors(form_entry_data()))


def test_form_config_and_values_reject_injection_and_coercion() -> None:
    config = form_entry_data()
    assert validate_submission(
        config,
        {"field_name": "示例", "field_priority": "normal", "field_consent": True},
    )["consent"] is True
    with pytest.raises(WorkflowFormError, match="fixed plain text"):
        validate_form_config({**config, "formTitle": "{{secret}}"})
    secret_field = dict(config["fields"][0])
    secret_field.update({"outputVariable": "password", "label": "登录密码"})
    with pytest.raises(WorkflowFormError, match="cannot collect passwords"):
        validate_form_config({**config, "fields": [secret_field, *config["fields"][1:]]})
    with pytest.raises(WorkflowFormError, match="unknown field"):
        validate_submission(
            config,
            {"field_name": "示例", "field_priority": "normal", "field_consent": True, "admin": True},
        )
    with pytest.raises(WorkflowFormError, match="required checkbox"):
        validate_submission(
            config,
            {"field_name": "示例", "field_priority": "normal", "field_consent": "true"},
        )
    with pytest.raises(WorkflowFormError, match="selected option"):
        validate_submission(
            config,
            {"field_name": "示例", "field_priority": "tampered", "field_consent": True},
        )
    with pytest.raises(WorkflowFormError, match="finite JSON"):
        loads_strict_json(b'{"value":NaN}')
    with pytest.raises(WorkflowFormError, match="duplicate field"):
        loads_strict_json(b'{"value":1,"value":2}')


def test_form_submission_preserves_all_supported_types_and_field_limits() -> None:
    config = form_entry_data()
    config["fields"] = [
        {
            "id": f"field_{field_type}",
            "outputVariable": f"value_{field_type}",
            "label": f"字段 {field_type}",
            "helpText": "",
            "placeholder": "",
            "type": field_type,
            "required": field_type != "number",
            "options": (
                [
                    {"id": "option_one", "value": "one", "label": "一"},
                    {"id": "option_two", "value": "two", "label": "二"},
                ]
                if field_type in {"single_select", "multi_select"}
                else []
            ),
        }
        for field_type in (
            "short_text",
            "long_text",
            "email",
            "number",
            "boolean",
            "date",
            "single_select",
            "multi_select",
        )
    ]
    values = {
        "field_short_text": "短文本",
        "field_long_text": "长文本",
        "field_email": "user@example.test",
        "field_number": None,
        "field_boolean": True,
        "field_date": "2026-02-28",
        "field_single_select": "one",
        "field_multi_select": ["one", "two"],
    }
    normalized = validate_submission(config, values)
    assert normalized == {
        "value_short_text": "短文本",
        "value_long_text": "长文本",
        "value_email": "user@example.test",
        "value_number": None,
        "value_boolean": True,
        "value_date": "2026-02-28",
        "value_single_select": "one",
        "value_multi_select": ["one", "two"],
    }

    for field_id, invalid in (
        ("field_email", "not-an-email"),
        ("field_date", "2026-02-30"),
        ("field_number", "12"),
        ("field_multi_select", ["unknown"]),
    ):
        with pytest.raises(WorkflowFormError):
            validate_submission(config, {**values, field_id: invalid})

    field_template = config["fields"][0]
    thirty = [
        {
            **field_template,
            "id": f"field_limit_{index}",
            "outputVariable": f"limit_value_{index}",
            "label": f"普通字段 {index}",
        }
        for index in range(30)
    ]
    assert len(validate_form_config({**config, "fields": thirty})["fields"]) == 30
    with pytest.raises(WorkflowFormError, match="1 to 30"):
        validate_form_config({**config, "fields": [*thirty, {
            **field_template,
            "id": "field_limit_30",
            "outputVariable": "limit_value_30",
            "label": "普通字段 30",
        }]})


def test_form_token_is_bound_to_key_version_and_schema() -> None:
    key_hash = "1" * 64
    checksum = form_schema_checksum(form_entry_data())
    token = issue_submission_token(
        form_id="form_" + "a" * 32,
        form_key_hash=key_hash,
        version=2,
        schema_checksum=checksum,
        now=100,
    )
    payload = verify_submission_token(
        token,
        form_id="form_" + "a" * 32,
        form_key_hash=key_hash,
        version=2,
        schema_checksum=checksum,
        now=200,
    )
    assert payload["version"] == 2
    for patch in (
        {"form_key_hash": "2" * 64},
        {"version": 3},
        {"schema_checksum": "3" * 64},
        {"now": 100 + 901},
    ):
        args = {
            "form_id": "form_" + "a" * 32,
            "form_key_hash": key_hash,
            "version": 2,
            "schema_checksum": checksum,
            "now": 200,
            **patch,
        }
        with pytest.raises(WorkflowFormError, match="submission session"):
            verify_submission_token(token, **args)


def test_public_base_url_and_static_page_headers_are_fail_closed() -> None:
    assert validate_public_base_url("https://forms.example.test") == "https://forms.example.test"
    assert validate_public_base_url("http://127.0.0.1:5173/") == "http://127.0.0.1:5173"
    for value in (
        "",
        "http://forms.example.test",
        "http://[::1]:5173",
        "https://user:pass@forms.example.test",
        "https://forms.example.test/path",
        "https://forms.example.test?next=https://evil.test",
    ):
        with pytest.raises(WorkflowFormError, match="public base URL"):
            validate_public_base_url(value)

    static_server = (
        Path(__file__).resolve().parents[2] / "client" / "server.mjs"
    ).read_text(encoding="utf-8")
    for evidence in (
        'requestPath === "/forms" || requestPath.startsWith("/forms/")',
        '"Cache-Control": "no-store"',
        '"Referrer-Policy": "no-referrer"',
        '"X-Frame-Options": "DENY"',
        '"X-Content-Type-Options": "nosniff"',
        '"X-Robots-Tag": "noindex, nofollow"',
        "frame-ancestors 'none'",
    ):
        assert evidence in static_server


def test_form_publication_is_stable_and_raw_submission_is_never_persisted(tmp_path) -> None:
    store = WorkflowDeploymentStore(tmp_path)
    project = store.create_project(form_workflow())
    first = store.publish(project.project_id)
    deployment, key = store.activate(
        project.project_id,
        first.version,
        webhooks_enabled=False,
        forms_enabled=True,
        forms_public_base_url="http://127.0.0.1:5173",
    )
    assert key and key.startswith("mmform_")
    publication = store.get_form_publication(project.project_id)
    assert publication is not None and publication.active
    original_form_id = publication.form_id
    original_key_prefix = publication.form_key_prefix

    second = store.publish(project.project_id)
    second_deployment, second_key = store.activate(
        project.project_id,
        second.version,
        webhooks_enabled=False,
        forms_enabled=True,
        forms_public_base_url="http://localhost:5173",
    )
    publication = store.get_form_publication(project.project_id)
    assert publication is not None
    assert publication.form_id == original_form_id
    assert publication.form_key_prefix == original_key_prefix
    assert publication.version == second.version
    assert second_key is None

    sentinel = "FORM_SENTINEL_DO_NOT_PERSIST"
    item, created = store.create_form_execution(
        second_deployment,
        nonce="nonce_abcdefghijklmnopqrstuvwxyz",
        field_count=3,
        body_size=len(sentinel),
        body_sha256="a" * 64,
    )
    assert created and item.trigger_kind == "form"
    snapshot = store.snapshot_path.read_text(encoding="utf-8")
    assert sentinel not in snapshot
    assert key not in snapshot
    assert "form_key_hash" in snapshot

    recovered = WorkflowDeploymentStore(tmp_path)
    recovered_item = recovered.get_execution(item.execution_id)
    assert recovered_item is not None
    assert recovered_item.status == "failed"
    assert recovered_item.error_summary == (
        "Form submission values were not persisted; execution was not replayed."
    )


def test_v2_snapshot_loads_with_an_empty_additive_form_table(tmp_path) -> None:
    store = WorkflowDeploymentStore(tmp_path)
    project = store.create_project(form_workflow())
    store.publish(project.project_id)
    payload = json.loads(store.snapshot_path.read_text(encoding="utf-8"))
    payload["version"] = "workflow-deployments-v2"
    payload.pop("form_publications")
    store.snapshot_path.write_text(json.dumps(payload), encoding="utf-8")

    recovered = WorkflowDeploymentStore(tmp_path)
    assert recovered.require_project(project.project_id).project_id == project.project_id
    assert recovered.get_form_publication(project.project_id) is None


def test_form_publish_rejects_waiting_nodes(tmp_path) -> None:
    store = WorkflowDeploymentStore(tmp_path)
    project = store.create_project(form_workflow(waiting_kind="suspend_wait"))
    with pytest.raises(WorkflowDeploymentValidationError, match="persistent waiting"):
        store.publish(project.project_id)


def test_form_publish_rejects_field_output_that_overwrites_a_constant(tmp_path) -> None:
    workflow = form_workflow()
    workflow["variables"] = [
        {
            "id": "constant-name",
            "name": "name",
            "kind": "constant",
            "valueType": "text",
            "defaultValue": "fixed",
        }
    ]
    store = WorkflowDeploymentStore(tmp_path)
    project = store.create_project(workflow)
    with pytest.raises(WorkflowDeploymentValidationError):
        store.publish(project.project_id)


def test_form_activation_is_disabled_by_default(tmp_path) -> None:
    store = WorkflowDeploymentStore(tmp_path)
    project = store.create_project(form_workflow())
    release = store.publish(project.project_id)
    with pytest.raises(WorkflowDeploymentConflictError, match="forms are disabled"):
        store.activate(
            project.project_id,
            release.version,
            webhooks_enabled=False,
        )


@pytest.mark.asyncio
async def test_public_form_api_is_same_origin_idempotent_and_rotation_invalidates_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = WorkflowDeploymentStore(tmp_path)
    observed_events: list[dict] = []

    async def executor(item, release, event):
        observed_events.append(event)
        return {"status": "completed", "result": "accepted"}

    monkeypatch.setattr(deployment_api, "_store", store)
    monkeypatch.setattr(deployment_api, "_trigger_executor", executor)
    monkeypatch.setenv("WORKFLOW_FORMS_ENABLED", "true")
    monkeypatch.setenv("WORKFLOW_FORMS_PUBLIC_BASE_URL", "http://127.0.0.1:5173")
    deployment_api._form_rate_windows.clear()
    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post("/api/workflows", json={"workflow": form_workflow()})
        assert created.status_code == 201
        project_id = created.json()["project_id"]
        published = await client.post(f"/api/workflows/{project_id}/publish")
        assert published.status_code == 201
        version = published.json()["version"]
        activated = await client.post(
            f"/api/workflows/{project_id}/versions/{version}/activate"
        )
        assert activated.status_code == 200
        share_url = activated.json()["form_share_url"]
        parsed = urlsplit(share_url)
        form_id = parsed.path.rsplit("/", 1)[-1]
        access_key = parse_qs(parsed.fragment)["access"][0]
        assert parsed.scheme == "http" and parsed.netloc == "127.0.0.1:5173"

        missing = await client.get(f"/api/workflow-forms/{form_id}/manifest")
        wrong = await client.get(
            f"/api/workflow-forms/{form_id}/manifest",
            headers={"X-ModelMirror-Form-Key": "mmform_wrong"},
        )
        assert missing.status_code == wrong.status_code == 404

        manifest = await client.get(
            f"/api/workflow-forms/{form_id}/manifest",
            headers={"X-ModelMirror-Form-Key": access_key},
        )
        assert manifest.status_code == 200
        assert manifest.headers["cache-control"] == "no-store"
        assert all("outputVariable" not in field for field in manifest.json()["fields"])
        token = manifest.json()["submissionToken"]
        sentinel = "FORM_API_SENTINEL"
        body = {
            "submissionToken": token,
            "values": {"field_name": sentinel, "field_priority": "urgent", "field_consent": True},
        }
        first, replay = await asyncio.gather(
            client.post(
                f"/api/workflow-forms/{form_id}/submissions",
                headers={"X-ModelMirror-Form-Key": access_key},
                json=body,
            ),
            client.post(
                f"/api/workflow-forms/{form_id}/submissions",
                headers={"X-ModelMirror-Form-Key": access_key},
                json=body,
            ),
        )
        assert first.status_code == replay.status_code == 202
        assert first.json() == replay.json() == {"status": "accepted"}
        await asyncio.sleep(0)
        assert len(store.list_executions(project_id)) == 1
        assert observed_events[0]["values"]["name"] == sentinel
        assert sentinel not in store.snapshot_path.read_text(encoding="utf-8")

        oversized = await client.post(
            f"/api/workflow-forms/{form_id}/submissions",
            headers={
                "Content-Type": "application/json",
                "X-ModelMirror-Form-Key": access_key,
            },
            content=b'{"submissionToken":"' + b"x" * 65_536,
        )
        assert oversized.status_code == 413

        rotated = await client.post(
            f"/api/workflows/{project_id}/versions/{version}/rotate-form-key"
        )
        assert rotated.status_code == 200
        new_key = parse_qs(urlsplit(rotated.json()["form_share_url"]).fragment)["access"][0]
        assert new_key != access_key
        old_key_response = await client.get(
            f"/api/workflow-forms/{form_id}/manifest",
            headers={"X-ModelMirror-Form-Key": access_key},
        )
        old_token_response = await client.post(
            f"/api/workflow-forms/{form_id}/submissions",
            headers={"X-ModelMirror-Form-Key": new_key},
            json=body,
        )
        assert old_key_response.status_code == old_token_response.status_code == 404

        deactivated = await client.post(
            f"/api/workflows/{project_id}/versions/{version}/deactivate"
        )
        assert deactivated.status_code == 200
        disabled = await client.get(
            f"/api/workflow-forms/{form_id}/manifest",
            headers={"X-ModelMirror-Form-Key": new_key},
        )
        assert disabled.status_code == 404


@pytest.mark.asyncio
async def test_form_runtime_keeps_raw_values_out_of_sse_checkpoints_and_store(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    execution_store = WorkflowExecutionStore(tmp_path / "runtime-executions")
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    sentinel = "FORM_RUNTIME_SENTINEL_MUST_STAY_EPHEMERAL"
    event = {
        "type": "form_submission",
        "form_id": "form_" + "b" * 32,
        "submission_id": "sub_ephemeral_test",
        "received_at": 100.0,
        "occurrence_key": "form:ephemeral-test",
        "field_count": 3,
        "body_size": len(sentinel),
        "body_sha256": "b" * 64,
        "values": {"name": sentinel, "priority": "normal", "consent": True},
    }
    payload = main_module.WorkflowRunRequest.model_validate(
        {"workflow": form_workflow(), "inputs": {}}
    )
    response = await main_module._run_workflow_response(
        payload,
        None,
        runtime_execution_source_kind="workflow_deployment",
        runtime_trigger_event=event,
        runtime_metadata={"workflow_trigger_kind": "form"},
    )
    chunks: list[str] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk))
    stream_text = "".join(chunks)

    assert sentinel not in stream_text
    assert "form output_bytes=" in stream_text
    persisted = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / "runtime-executions").rglob("*.json")
    )
    assert sentinel not in persisted


@pytest.mark.asyncio
async def test_form_agent_failure_stops_before_downstream_nodes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    execution_store = WorkflowExecutionStore(tmp_path / "runtime-executions")
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)

    async def failing_model(*args, **kwargs):
        raise RuntimeError("synthetic model failure")
        yield "unreachable"

    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(main_module, "stream_workflow_llm_text", failing_model)
    workflow = form_workflow()
    workflow["nodes"] = [
        workflow["nodes"][0],
        {
            "id": "workflow_agent",
            "type": "workflow_agent",
            "data": {
                "kind": "workflow_agent",
                "agentName": "form-agent",
                "modelId": "deepseek/deepseek-chat",
                "rolePrompt": "Process synthetic form content.",
                "taskInput": "{{name}}",
                "outputVariable": "agent_output",
            },
        },
        {
            "id": "downstream",
            "type": "variable_assign",
            "data": {
                "kind": "variable_assign",
                "contractVersion": 2,
                "outputVariable": "downstream_value",
                "valueSource": "literal",
                "literalValue": "must-not-run",
            },
        },
    ]
    workflow["edges"] = [
        {"id": "edge_entry_agent", "source": "entry", "target": "workflow_agent"},
        {"id": "edge_agent_downstream", "source": "workflow_agent", "target": "downstream"},
    ]
    event = {
        "type": "form_submission",
        "form_id": "form_" + "c" * 32,
        "submission_id": "sub_fail_closed_test",
        "received_at": 100.0,
        "occurrence_key": "form:fail-closed-test",
        "field_count": 3,
        "body_size": 32,
        "body_sha256": "c" * 64,
        "values": {"name": "synthetic", "priority": "normal", "consent": True},
    }
    payload = main_module.WorkflowRunRequest.model_validate(
        {"workflow": workflow, "inputs": {}}
    )
    response = await main_module._run_workflow_response(
        payload,
        None,
        runtime_execution_source_kind="workflow_deployment",
        runtime_trigger_event=event,
        runtime_metadata={"workflow_trigger_kind": "form"},
    )
    chunks: list[str] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk))
    stream_text = "".join(chunks)
    events = [
        json.loads(line[5:].strip())
        for line in stream_text.splitlines()
        if line.startswith("data:")
    ]

    assert any(event.get("node_id") == "workflow_agent" for event in events)
    assert not any(event.get("node_id") == "downstream" for event in events)
    assert not any(event.get("event") == "workflow_end" for event in events)
