from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from server.workflow_deployments import (
    WorkflowDeploymentConflictError,
    WorkflowDeploymentStore,
    WorkflowDeploymentValidationError,
    _safe_error_summary,
)
from server.workflow_native.r20_nodes import (
    mcp_schema_checksum,
    validate_mcp_tool_v2_config,
)


def test_server_image_includes_workflow_deployment_module() -> None:
    dockerfile = Path(__file__).resolve().parents[1] / "Dockerfile"

    dockerfile_text = dockerfile.read_text(encoding="utf-8")
    assert "COPY workflow_deployments.py ." in dockerfile_text
    assert "COPY workflow_rss.py ." in dockerfile_text


def manual_workflow() -> dict:
    return {
        "id": "draft",
        "title": "manual",
        "nodes": [
            {
                "id": "start",
                "type": "input",
                "data": {"kind": "input", "variableName": "user_input"},
            },
            {
                "id": "end",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "user_input"},
            },
        ],
        "edges": [{"id": "e1", "source": "start", "target": "end"}],
    }


def http_workflow(*, with_wait: bool = False) -> dict:
    nodes = [
        {
            "id": "start",
            "type": "http_event_entry",
            "data": {"kind": "http_event_entry", "eventVariable": "http_event"},
        }
    ]
    if with_wait:
        nodes.append(
            {
                "id": "wait",
                "type": "suspend_wait",
                "data": {
                    "kind": "suspend_wait",
                    "waitMode": "duration",
                    "durationSeconds": 1,
                    "outputVariable": "resume_event",
                },
            }
        )
    nodes.append(
        {
            "id": "reply",
            "type": "http_event_reply",
            "data": {
                "kind": "http_event_reply",
                "statusCode": 201,
                "responseBodyType": "json",
                "bodyTemplate": '{"accepted":true}',
            },
        }
    )
    return {
        "id": "draft",
        "title": "http",
        "nodes": nodes,
        "edges": [
            {"id": f"e{index}", "source": nodes[index]["id"], "target": nodes[index + 1]["id"]}
            for index in range(len(nodes) - 1)
        ],
    }


def http_wait_workflow() -> dict:
    return {
        "id": "draft",
        "title": "http wait",
        "nodes": [
            {
                "id": "start",
                "type": "http_event_entry",
                "data": {
                    "kind": "http_event_entry",
                    "eventVariable": "http_event",
                    "bodyVariable": "request_body",
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
                "id": "end",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "resume_event"},
            },
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "wait"},
            {"id": "e2", "source": "wait", "target": "end"},
        ],
    }


def llm_workflow() -> dict:
    return {
        "id": "draft",
        "title": "published llm",
        "nodes": [
            {
                "id": "start",
                "type": "input",
                "data": {"kind": "input", "variableName": "user_input"},
            },
            {
                "id": "llm",
                "type": "llm",
                "data": {
                    "kind": "llm",
                    "modelId": "openai/gpt-5.6-luna",
                    "prompt": "Answer {{user_input}}",
                    "outputVariable": "llm_output",
                },
            },
            {
                "id": "end",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "llm_output"},
            },
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "llm"},
            {"id": "e2", "source": "llm", "target": "end"},
        ],
    }


def schedule_workflow() -> dict:
    return {
        "id": "draft",
        "title": "schedule",
        "nodes": [
            {
                "id": "start",
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
                "id": "end",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "schedule_event"},
            },
        ],
        "edges": [{"id": "e1", "source": "start", "target": "end"}],
    }


def secure_http_workflow(*, auth_type: str = "none") -> dict:
    return {
        "id": "draft",
        "title": "secure http",
        "nodes": [
            {
                "id": "start",
                "type": "input",
                "data": {"kind": "input", "variableName": "user_input"},
            },
            {
                "id": "http",
                "type": "http_request",
                "data": {
                    "kind": "http_request",
                    "contractVersion": 2,
                    "method": "GET",
                    "url": "https://api.example.test/items/{{user_input}}",
                    "queryItems": [],
                    "headerItems": [],
                    "bodyMode": "none",
                    "formFields": [],
                    "authType": auth_type,
                    "credentialId": "cred_http_test" if auth_type != "none" else "",
                    "apiKeyLocation": "header",
                    "apiKeyName": "X-API-Key",
                    "timeoutSeconds": 30,
                    "redirectLimit": 0,
                    "responseLimitBytes": 1_048_576,
                    "responseMode": "auto",
                    "statusPolicy": "success_only",
                    "outputVariable": "http_response",
                },
            },
            {
                "id": "end",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "http_response"},
            },
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "http"},
            {"id": "e2", "source": "http", "target": "end"},
        ],
    }


def manual_workflow_with_node(node: dict, *, output_variable: str) -> dict:
    workflow = manual_workflow()
    workflow["nodes"].insert(1, node)
    workflow["nodes"][2]["data"]["outputVariable"] = output_variable
    workflow["edges"] = [
        {"id": "e1", "source": "start", "target": node["id"]},
        {"id": "e2", "source": node["id"], "target": "end"},
    ]
    return workflow


def r18_file_workflow(kind: str) -> dict:
    workflow = manual_workflow()
    if kind == "file_output":
        node = {
            "id": "file",
            "type": "file_output",
            "data": {
                "kind": "file_output",
                "inputVariable": "user_input",
                "outputVariable": "generated_file",
                "format": "markdown",
                "filenameTemplate": "report",
                "titleTemplate": "",
                "columns": [],
            },
        }
        workflow["nodes"][1]["data"]["outputVariable"] = "generated_file"
    else:
        node = {
            "id": "document",
            "type": "document_extractor",
            "data": {
                "kind": "document_extractor",
                "assetIdVariable": "user_input",
                "outputVariable": "document_text",
            },
        }
        workflow["nodes"][1]["data"]["outputVariable"] = "document_text"
    workflow["nodes"].insert(1, node)
    workflow["edges"] = [
        {"id": "e1", "source": "start", "target": node["id"]},
        {"id": "e2", "source": node["id"], "target": "end"},
    ]
    return workflow


def failure_workflow(source_project_ids: list[str]) -> dict:
    return {
        "id": "draft",
        "title": "failure handler",
        "nodes": [
            {
                "id": "start",
                "type": "failure_event_entry",
                "data": {
                    "kind": "failure_event_entry",
                    "sourceProjectIds": source_project_ids,
                    "eventVariable": "failure_event",
                },
            },
            {
                "id": "end",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "failure_event"},
            },
        ],
        "edges": [{"id": "e1", "source": "start", "target": "end"}],
    }


def test_draft_revision_and_immutable_versions(tmp_path) -> None:
    store = WorkflowDeploymentStore(tmp_path)
    project = store.create_project(manual_workflow())

    updated = store.save_draft(
        project.project_id,
        expected_revision=1,
        workflow={**manual_workflow(), "title": "changed"},
    )
    assert updated.draft_revision == 2
    with pytest.raises(WorkflowDeploymentConflictError):
        store.save_draft(
            project.project_id,
            expected_revision=1,
            workflow=manual_workflow(),
        )

    first = store.publish(project.project_id)
    store.save_draft(
        project.project_id,
        expected_revision=2,
        workflow={**manual_workflow(), "title": "later"},
    )
    second = store.publish(project.project_id)

    assert [item.version for item in store.list_versions(project.project_id)] == [2, 1]
    assert first.workflow["title"] == "changed"
    assert second.workflow["title"] == "later"
    assert first.definition_checksum != second.definition_checksum


def test_publish_rejects_multiple_entries_credentials_and_wait_before_reply(tmp_path) -> None:
    store = WorkflowDeploymentStore(tmp_path)
    duplicate = manual_workflow()
    duplicate["nodes"].insert(
        1,
        {
            "id": "other-start",
            "type": "input",
            "data": {"kind": "input", "variableName": "other_input"},
        },
    )
    project = store.create_project(duplicate)
    with pytest.raises(WorkflowDeploymentValidationError, match="exactly one entry"):
        store.publish(project.project_id)

    credential = manual_workflow()
    credential["nodes"][1]["data"]["api_key"] = "sk-secret-value-123456"
    project = store.create_project(credential)
    with pytest.raises(WorkflowDeploymentValidationError, match="plaintext credentials"):
        store.publish(project.project_id)

    project = store.create_project(http_workflow(with_wait=True))
    with pytest.raises(WorkflowDeploymentValidationError) as captured:
        store.publish(project.project_id)
    assert any(
        issue.get("code") == "http_event_reply_after_suspend_wait"
        for issue in captured.value.issues
    )


def test_publish_accepts_complete_llm_and_http_timer_workflows(tmp_path) -> None:
    store = WorkflowDeploymentStore(tmp_path)

    llm_project = store.create_project(llm_workflow())
    llm_release = store.publish(llm_project.project_id)
    assert llm_release.trigger_kind == "manual"
    assert llm_release.entry_node_id == "start"

    http_project = store.create_project(http_wait_workflow())
    http_release = store.publish(http_project.project_id)
    assert http_release.trigger_kind == "http"
    assert http_release.entry_node_id == "start"


def test_secure_http_publish_activation_and_credential_lifecycle(tmp_path) -> None:
    credential = SimpleNamespace(status="active", kind="generic")
    store = WorkflowDeploymentStore(
        tmp_path,
        credential_validator=lambda credential_id: credential,
    )
    project = store.create_project(secure_http_workflow(auth_type="bearer"))
    release = store.publish(project.project_id)

    with pytest.raises(WorkflowDeploymentConflictError, match="disabled"):
        store.activate(
            project.project_id,
            release.version,
            webhooks_enabled=False,
            http_requests_enabled=False,
        )

    deployment, plaintext = store.activate(
        project.project_id,
        release.version,
        webhooks_enabled=False,
        http_requests_enabled=True,
    )
    assert deployment.active is True
    assert plaintext is None

    store.deactivate(project.project_id, release.version)
    credential.status = "revoked"
    with pytest.raises(WorkflowDeploymentConflictError, match="unavailable"):
        store.activate(
            project.project_id,
            release.version,
            webhooks_enabled=False,
            http_requests_enabled=True,
        )


@pytest.mark.parametrize(
    ("kind", "activation_flags"),
    [
        ("file_output", {"file_output_assets_enabled": True}),
        ("document_extractor", {"workflow_file_assets_enabled": True}),
    ],
)
def test_r18_file_nodes_are_fail_closed_at_deployment_activation(
    tmp_path,
    kind: str,
    activation_flags: dict[str, bool],
) -> None:
    store = WorkflowDeploymentStore(tmp_path / kind)
    project = store.create_project(r18_file_workflow(kind))
    release = store.publish(project.project_id)

    with pytest.raises(WorkflowDeploymentConflictError, match="disabled"):
        store.activate(
            project.project_id,
            release.version,
            webhooks_enabled=False,
        )

    deployment, plaintext = store.activate(
        project.project_id,
        release.version,
        webhooks_enabled=False,
        **activation_flags,
    )
    assert deployment.active is True
    assert plaintext is None


def test_r18_document_v2_cannot_fall_through_to_legacy_schema(tmp_path) -> None:
    node = {
        "id": "document",
        "type": "document_extractor",
        "data": {
            "kind": "document_extractor",
            "contractVersion": 2,
            "sourcePathVariable": "user_input",
            "outputVariable": "document_text",
        },
    }
    workflow = manual_workflow()
    workflow["nodes"].insert(1, node)
    workflow["nodes"][2]["data"]["outputVariable"] = node["data"]["outputVariable"]
    workflow["edges"] = [
        {"id": "e1", "source": "start", "target": node["id"]},
        {"id": "e2", "source": node["id"], "target": "end"},
    ]
    store = WorkflowDeploymentStore(tmp_path / node["id"])
    project = store.create_project(workflow)

    with pytest.raises(
        WorkflowDeploymentValidationError,
        match="does not satisfy its NodeContract",
    ):
        store.publish(project.project_id)


def test_r24_http_content_parser_activation_does_not_require_file_assets(tmp_path) -> None:
    workflow = manual_workflow()
    workflow["nodes"].insert(
        1,
        {
            "id": "content",
            "type": "document_extractor",
            "data": {
                "kind": "document_extractor",
                "contractVersion": 3,
                "sourceMode": "http_response",
                "inputVariable": "user_input",
                "format": "html",
                "outputMode": "structured",
                "outputVariable": "parsed_content",
            },
        },
    )
    workflow["nodes"][2]["data"]["outputVariable"] = "parsed_content"
    workflow["edges"] = [
        {"id": "e1", "source": "start", "target": "content"},
        {"id": "e2", "source": "content", "target": "end"},
    ]
    store = WorkflowDeploymentStore(tmp_path / "http-content")
    project = store.create_project(workflow)
    release = store.publish(project.project_id)

    deployment, plaintext = store.activate(
        project.project_id,
        release.version,
        webhooks_enabled=False,
        workflow_file_assets_enabled=False,
    )

    assert deployment.active is True
    assert plaintext is None


def test_secure_http_publish_rejects_legacy_and_missing_credentials(tmp_path) -> None:
    legacy = manual_workflow()
    legacy["nodes"].insert(
        1,
        {
            "id": "http",
            "type": "http_request",
            "data": {
                "kind": "http_request",
                "method": "GET",
                "url": "https://example.test",
                "outputVariable": "http_output",
            },
        },
    )
    legacy["nodes"][2]["data"]["outputVariable"] = "http_output"
    legacy["edges"] = [
        {"id": "e1", "source": "start", "target": "http"},
        {"id": "e2", "source": "http", "target": "end"},
    ]
    store = WorkflowDeploymentStore(tmp_path / "legacy")
    project = store.create_project(legacy)
    with pytest.raises(WorkflowDeploymentValidationError, match="migrated"):
        store.publish(project.project_id)

    unavailable = WorkflowDeploymentStore(
        tmp_path / "missing",
        credential_validator=lambda credential_id: (_ for _ in ()).throw(KeyError(credential_id)),
    )
    project = unavailable.create_project(secure_http_workflow(auth_type="api_key"))
    with pytest.raises(WorkflowDeploymentValidationError, match="unavailable"):
        unavailable.publish(project.project_id)


@pytest.mark.parametrize(
    ("kind", "data", "output_variable"),
    [
        (
            "human_intervention",
            {
                "kind": "human_intervention",
                "prompt": "Please provide input",
                "outputVariable": "human_result",
            },
            "human_result",
        ),
        (
            "mcp_tool",
            {
                "kind": "mcp_tool",
                "toolName": "search",
                "argumentsJson": "{}",
                "outputVariable": "mcp_result",
            },
            "mcp_result",
        ),
        (
            "variable_assign",
            {
                "kind": "variable_assign",
                "variableName": "assigned",
                "template": "{{user_input}}",
            },
            "assigned",
        ),
    ],
)
def test_r20_legacy_nodes_must_be_explicitly_migrated_before_publish(
    tmp_path,
    kind: str,
    data: dict,
    output_variable: str,
) -> None:
    workflow = manual_workflow_with_node(
        {"id": "legacy", "type": kind, "data": data},
        output_variable=output_variable,
    )
    store = WorkflowDeploymentStore(tmp_path / kind)
    project = store.create_project(workflow)

    with pytest.raises(WorkflowDeploymentValidationError, match="explicitly migrated"):
        store.publish(project.project_id)


def test_legacy_knowledge_citation_must_migrate_before_publish(tmp_path) -> None:
    workflow = manual_workflow_with_node(
        {
            "id": "citation",
            "type": "knowledge_citation",
            "data": {
                "kind": "knowledge_citation",
                "knowledgeBaseId": "kb_test",
                "queryVariable": "user_input",
                "topK": 5,
                "outputVariable": "citations",
            },
        },
        output_variable="citations",
    )
    store = WorkflowDeploymentStore(tmp_path)
    project = store.create_project(workflow)

    with pytest.raises(WorkflowDeploymentValidationError, match="knowledge citation"):
        store.publish(project.project_id)


def test_mcp_v2_publish_and_activation_recheck_schema_and_feature_flag(tmp_path) -> None:
    schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": False,
    }
    node = {
        "id": "mcp",
        "type": "mcp_tool",
        "data": {
            "kind": "mcp_tool",
            "contractVersion": 2,
            "serverId": "server_alpha",
            "toolName": "search",
            "inputSchemaChecksum": mcp_schema_checksum(schema),
            "argumentMode": "fields",
            "argumentBindings": [
                {
                    "id": "query_binding",
                    "name": "query",
                    "binding": {"source": "variable", "variable": "user_input"},
                }
            ],
            "argumentsVariable": "mcp_arguments",
            "outputVariable": "mcp_result",
        },
    }
    current_schema = {"value": schema}

    def validate(data: dict) -> None:
        validate_mcp_tool_v2_config(data, input_schema=current_schema["value"])

    store = WorkflowDeploymentStore(tmp_path, mcp_tool_validator=validate)
    project = store.create_project(
        manual_workflow_with_node(node, output_variable="mcp_result")
    )
    release = store.publish(project.project_id)

    with pytest.raises(WorkflowDeploymentConflictError, match="disabled"):
        store.activate(
            project.project_id,
            release.version,
            webhooks_enabled=False,
            mcp_tools_enabled=False,
        )

    current_schema["value"] = {
        **schema,
        "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
    }
    with pytest.raises(WorkflowDeploymentConflictError, match="changed"):
        store.activate(
            project.project_id,
            release.version,
            webhooks_enabled=False,
            mcp_tools_enabled=True,
        )


@pytest.mark.parametrize("waiting_kind", ["human_intervention", "mcp_tool"])
def test_http_publish_rejects_r20_interactive_waiting_nodes(tmp_path, waiting_kind: str) -> None:
    workflow = http_workflow()
    if waiting_kind == "human_intervention":
        data = {
            "kind": waiting_kind,
            "contractVersion": 2,
            "interactionMode": "approval",
            "prompt": "Approve request",
            "outputVariable": "decision",
            "timeoutSeconds": 3600,
        }
    else:
        schema = {"type": "object", "properties": {}}
        data = {
            "kind": waiting_kind,
            "contractVersion": 2,
            "serverId": "server_alpha",
            "toolName": "search",
            "inputSchemaChecksum": mcp_schema_checksum(schema),
            "argumentMode": "fields",
            "argumentBindings": [],
            "argumentsVariable": "mcp_arguments",
            "outputVariable": "mcp_result",
        }
    workflow["nodes"].insert(1, {"id": "waiting", "type": waiting_kind, "data": data})
    workflow["edges"] = [
        {"id": "e1", "source": "start", "target": "waiting"},
        {"id": "e2", "source": "waiting", "target": "reply"},
    ]
    store = WorkflowDeploymentStore(
        tmp_path / waiting_kind,
        mcp_tool_validator=(
            (lambda node_data: validate_mcp_tool_v2_config(node_data, input_schema=schema))
            if waiting_kind == "mcp_tool"
            else None
        ),
    )
    project = store.create_project(workflow)

    with pytest.raises(WorkflowDeploymentValidationError, match="interactive waiting"):
        store.publish(project.project_id)


def test_private_webhook_key_idempotency_and_safe_snapshot(tmp_path) -> None:
    store = WorkflowDeploymentStore(tmp_path)
    project = store.create_project(http_workflow())
    release = store.publish(project.project_id)

    with pytest.raises(WorkflowDeploymentConflictError, match="disabled"):
        store.activate(project.project_id, release.version, webhooks_enabled=False)
    deployment, plaintext = store.activate(
        project.project_id,
        release.version,
        webhooks_enabled=True,
    )

    assert plaintext
    assert plaintext not in json.dumps(store.serialize_deployment(deployment))
    authenticated = store.authenticate_hook(deployment.hook_id or "", plaintext)
    assert authenticated.deployment_id == deployment.deployment_id

    body = b'{"private":"not persisted"}'
    first, created = store.create_webhook_execution(
        deployment,
        idempotency_key="same-request",
        content_type="application/json",
        body_size=len(body),
        body_sha256=hashlib.sha256(body).hexdigest(),
    )
    second, duplicate = store.create_webhook_execution(
        deployment,
        idempotency_key="same-request",
        content_type="application/json",
        body_size=len(body),
        body_sha256=hashlib.sha256(body).hexdigest(),
    )

    assert created is True
    assert duplicate is False
    assert first.execution_id == second.execution_id
    completed = store.complete_execution(
        first.execution_id,
        result="sensitive result that must not be stored verbatim",
    )
    assert completed.result_summary.startswith("completed output_bytes=")
    snapshot = store.snapshot_path.read_text(encoding="utf-8")
    assert plaintext not in snapshot
    assert "not persisted" not in snapshot
    assert "same-request" not in snapshot
    assert "sensitive result" not in snapshot


def test_failure_subscription_activation_conflicts_and_source_validation(tmp_path) -> None:
    store = WorkflowDeploymentStore(tmp_path)
    source = store.create_project(schedule_workflow())
    first_handler = store.create_project(failure_workflow([source.project_id]))
    first_release = store.publish(first_handler.project_id)

    with pytest.raises(WorkflowDeploymentConflictError, match="disabled"):
        store.activate(
            first_handler.project_id,
            first_release.version,
            webhooks_enabled=False,
        )
    first_deployment, _ = store.activate(
        first_handler.project_id,
        first_release.version,
        webhooks_enabled=False,
        failure_triggers_enabled=True,
    )
    subscription = store.failure_subscription(source.project_id)
    assert subscription is not None
    assert subscription.handler_deployment_id == first_deployment.deployment_id

    store.save_draft(
        first_handler.project_id,
        expected_revision=1,
        workflow={
            **failure_workflow([source.project_id]),
            "title": "failure handler v2",
        },
    )
    replacement_release = store.publish(first_handler.project_id)
    replacement_deployment, _ = store.activate(
        first_handler.project_id,
        replacement_release.version,
        webhooks_enabled=False,
        failure_triggers_enabled=True,
    )
    replacement_subscription = store.failure_subscription(source.project_id)
    assert first_deployment.active is False
    assert replacement_subscription is not None
    assert replacement_subscription.handler_deployment_id == (
        replacement_deployment.deployment_id
    )

    second_handler = store.create_project(failure_workflow([source.project_id]))
    second_release = store.publish(second_handler.project_id)
    with pytest.raises(WorkflowDeploymentConflictError, match="already has"):
        store.activate(
            second_handler.project_id,
            second_release.version,
            webhooks_enabled=False,
            failure_triggers_enabled=True,
        )
    assert store.active_deployment(first_handler.project_id) is not None

    missing = store.create_project(failure_workflow([f"wf_{'f' * 32}"]))
    with pytest.raises(WorkflowDeploymentConflictError, match="does not exist"):
        store.publish(missing.project_id)

    self_handler = store.create_project(failure_workflow([source.project_id]))
    store.save_draft(
        self_handler.project_id,
        expected_revision=1,
        workflow=failure_workflow([self_handler.project_id]),
    )
    with pytest.raises(WorkflowDeploymentConflictError, match="subscribe to itself"):
        store.publish(self_handler.project_id)


def test_failure_dispatch_is_atomic_idempotent_sanitized_and_persistent(tmp_path) -> None:
    store = WorkflowDeploymentStore(tmp_path)
    source = store.create_project(schedule_workflow())
    source_release = store.publish(source.project_id)
    source_deployment, _ = store.activate(
        source.project_id,
        source_release.version,
        webhooks_enabled=False,
        now=100,
    )
    handler = store.create_project(failure_workflow([source.project_id]))
    handler_release = store.publish(handler.project_id)
    handler_deployment, _ = store.activate(
        handler.project_id,
        handler_release.version,
        webhooks_enabled=False,
        failure_triggers_enabled=True,
        now=101,
    )

    source_execution = store.materialize_due_schedules(now=130)[0]
    store.claim_execution(
        source_execution.execution_id,
        worker_id="source-worker",
        now=130,
    )
    store.fail_execution(
        source_execution.execution_id,
        error=(
            "Traceback: request_body=private\n"
            "RuntimeError: Authorization: Bearer bearer-secret "
            "api_key=secret-token boom"
        ),
        failed_node_id="dangerous-node-webhook_key=plain-node-secret",
        failed_node_title="外部调用 Authorization: plain-title-secret",
    )
    store.fail_execution(
        source_execution.execution_id,
        error="a duplicate failure callback",
    )

    handler_executions = store.list_executions(handler.project_id)
    assert len(handler_executions) == 1
    dispatched = handler_executions[0]
    assert dispatched.occurrence_key == (
        f"failure:{source_execution.execution_id}:{handler_deployment.deployment_id}"
    )
    assert dispatched.trigger_summary == {
        "source_project_id": source.project_id,
        "source_version": source_release.version,
        "source_deployment_id": source_deployment.deployment_id,
        "source_execution_id": source_execution.execution_id,
        "source_task_id": None,
        "source_run_id": None,
        "source_trigger_kind": "schedule",
        "failed_at": dispatched.scheduled_at,
        "error_summary": "RuntimeError: Authorization: [redacted]",
        "occurrence_key": dispatched.occurrence_key,
        "suppress_failure_dispatch": True,
        "test_mode": False,
        "failed_node_id": "dangerous-node-webhook_key=[redacted]",
        "failed_node_title": "外部调用 Authorization: [redacted]",
    }
    snapshot = store.snapshot_path.read_text(encoding="utf-8")
    assert '"version": "workflow-deployments-v4"' in snapshot
    assert "request_body=private" not in snapshot
    assert "secret-token" not in snapshot
    assert "bearer-secret" not in snapshot
    assert "plain-node-secret" not in snapshot
    assert "plain-title-secret" not in snapshot
    assert "Traceback" not in snapshot

    store.claim_execution(
        dispatched.execution_id,
        worker_id="handler-worker",
        now=131,
    )
    reloaded = WorkflowDeploymentStore(tmp_path)
    recovered = reloaded.get_execution(dispatched.execution_id)
    assert recovered is not None
    assert recovered.status == "pending"
    assert reloaded.failure_subscription(source.project_id) is not None

    reloaded.claim_execution(
        dispatched.execution_id,
        worker_id="handler-recovery",
        now=132,
    )
    reloaded.fail_execution(dispatched.execution_id, error="handler failed")
    assert len(reloaded.list_executions(handler.project_id)) == 1


@pytest.mark.parametrize(
    "unsafe_error, secrets",
    [
        (
            'request failed: {"authorization": "plain-auth-value", '
            '"cookie": "sid=plain-cookie-value"}',
            ["plain-auth-value", "plain-cookie-value"],
        ),
        ("client_secret=plain-client-secret", ["plain-client-secret"]),
        ("webhook_key=plain-webhook-secret", ["plain-webhook-secret"]),
        (
            "request=https://operator:plain-password@example.test/private",
            ["operator", "plain-password"],
        ),
        (
            "Authorization: Basic dXNlcjpwd2Q= downstream refused",
            ["dXNlcjpwd2Q="],
        ),
        (
            "token=eyJhbGciOiJIUzI1NiJ9.abcdefghijklmno.signaturevalue",
            ["eyJhbGciOiJIUzI1NiJ9", "abcdefghijklmno", "signaturevalue"],
        ),
    ],
)
def test_failure_error_summary_redacts_structured_credentials(
    unsafe_error: str,
    secrets: list[str],
) -> None:
    summary = _safe_error_summary(unsafe_error)
    assert "[redacted]" in summary
    for secret in secrets:
        assert secret not in summary


def test_trigger_lease_renewal_and_fencing_reject_stale_worker(tmp_path) -> None:
    store = WorkflowDeploymentStore(tmp_path)
    source = store.create_project(schedule_workflow())
    release = store.publish(source.project_id)
    store.activate(
        source.project_id,
        release.version,
        webhooks_enabled=False,
        now=100,
    )
    execution = store.materialize_due_schedules(now=130)[0]
    first_claim = store.claim_execution(
        execution.execution_id,
        worker_id="worker-one",
        lease_seconds=10,
        now=130,
    )
    first_token = str(first_claim.lease_token)
    store.renew_execution_lease(
        execution.execution_id,
        lease_token=first_token,
        lease_seconds=10,
        now=135,
    )

    with pytest.raises(WorkflowDeploymentConflictError, match="already leased"):
        store.claim_execution(
            execution.execution_id,
            worker_id="worker-two-too-soon",
            now=144,
        )

    second_claim = store.claim_execution(
        execution.execution_id,
        worker_id="worker-two",
        now=146,
    )
    second_token = str(second_claim.lease_token)
    assert second_token != first_token
    with pytest.raises(WorkflowDeploymentConflictError, match="no longer owned"):
        store.complete_execution(
            execution.execution_id,
            result="stale result",
            expected_lease_token=first_token,
        )
    assert store.get_execution(execution.execution_id).lease_owner == "worker-two"

    completed = store.complete_execution(
        execution.execution_id,
        result="current result",
        expected_lease_token=second_token,
    )
    assert completed.status == "completed"


def test_concurrent_failure_callbacks_materialize_once(tmp_path) -> None:
    store = WorkflowDeploymentStore(tmp_path)
    source = store.create_project(schedule_workflow())
    source_release = store.publish(source.project_id)
    store.activate(
        source.project_id,
        source_release.version,
        webhooks_enabled=False,
        now=100,
    )
    handler = store.create_project(failure_workflow([source.project_id]))
    handler_release = store.publish(handler.project_id)
    store.activate(
        handler.project_id,
        handler_release.version,
        webhooks_enabled=False,
        failure_triggers_enabled=True,
        now=101,
    )
    source_execution = store.materialize_due_schedules(now=130)[0]
    barrier = threading.Barrier(4)

    def fail_from_worker(index: int) -> str:
        barrier.wait()
        return store.fail_execution(
            source_execution.execution_id,
            error=f"worker {index} failed",
        ).status

    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(fail_from_worker, range(4)))
    assert statuses == ["failed"] * 4
    assert len(store.list_executions(handler.project_id)) == 1


def test_concurrent_handler_activation_keeps_single_subscription(tmp_path) -> None:
    store = WorkflowDeploymentStore(tmp_path)
    source = store.create_project(schedule_workflow())
    handlers = [
        store.create_project(failure_workflow([source.project_id]))
        for _ in range(2)
    ]
    releases = [store.publish(handler.project_id) for handler in handlers]
    barrier = threading.Barrier(2)

    def activate_handler(index: int) -> str:
        barrier.wait()
        try:
            store.activate(
                handlers[index].project_id,
                releases[index].version,
                webhooks_enabled=False,
                failure_triggers_enabled=True,
            )
            return "activated"
        except WorkflowDeploymentConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(activate_handler, range(2)))
    assert sorted(results) == ["activated", "conflict"]
    subscription = store.failure_subscription(source.project_id)
    assert subscription is not None
    assert subscription.handler_project_id in {handler.project_id for handler in handlers}


def test_v2_load_rejects_duplicate_failure_subscription_source(tmp_path) -> None:
    store = WorkflowDeploymentStore(tmp_path)
    source = store.create_project(schedule_workflow())
    handler = store.create_project(failure_workflow([source.project_id]))
    release = store.publish(handler.project_id)
    store.activate(
        handler.project_id,
        release.version,
        webhooks_enabled=False,
        failure_triggers_enabled=True,
    )
    raw = json.loads(store.snapshot_path.read_text(encoding="utf-8"))
    raw["failure_subscriptions"].append(dict(raw["failure_subscriptions"][0]))
    store.snapshot_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(WorkflowDeploymentValidationError, match="snapshot is invalid"):
        WorkflowDeploymentStore(tmp_path)


def test_failure_deactivation_stops_new_events_without_cancelling_materialized(tmp_path) -> None:
    store = WorkflowDeploymentStore(tmp_path)
    source = store.create_project(schedule_workflow())
    source_release = store.publish(source.project_id)
    store.activate(
        source.project_id,
        source_release.version,
        webhooks_enabled=False,
        now=100,
    )
    handler = store.create_project(failure_workflow([source.project_id]))
    handler_release = store.publish(handler.project_id)
    store.activate(
        handler.project_id,
        handler_release.version,
        webhooks_enabled=False,
        failure_triggers_enabled=True,
        now=101,
    )

    first_source_execution = store.materialize_due_schedules(now=130)[0]
    store.fail_execution(first_source_execution.execution_id, error="first")
    materialized = store.list_executions(handler.project_id)[0]
    store.deactivate(handler.project_id, handler_release.version)
    assert store.failure_subscription(source.project_id) is None
    assert store.get_execution(materialized.execution_id).status == "pending"

    second_source_execution = store.materialize_due_schedules(now=160)[0]
    store.fail_execution(second_source_execution.execution_id, error="second")
    assert len(store.list_executions(handler.project_id)) == 1


def test_failure_activation_does_not_replay_history_and_v1_loads_empty_table(tmp_path) -> None:
    store = WorkflowDeploymentStore(tmp_path)
    source = store.create_project(schedule_workflow())
    source_release = store.publish(source.project_id)
    store.activate(
        source.project_id,
        source_release.version,
        webhooks_enabled=False,
        now=100,
    )
    historical = store.materialize_due_schedules(now=130)[0]
    store.fail_execution(historical.execution_id, error="failed before subscription")

    handler = store.create_project(failure_workflow([source.project_id]))
    handler_release = store.publish(handler.project_id)
    store.activate(
        handler.project_id,
        handler_release.version,
        webhooks_enabled=False,
        failure_triggers_enabled=True,
        now=131,
    )
    store.fail_execution(historical.execution_id, error="historical callback repeated")
    assert store.list_executions(handler.project_id) == []

    raw = json.loads(store.snapshot_path.read_text(encoding="utf-8"))
    raw["version"] = "workflow-deployments-v1"
    raw.pop("failure_subscriptions")
    store.snapshot_path.write_text(
        json.dumps(raw, ensure_ascii=False),
        encoding="utf-8",
    )
    loaded_v1 = WorkflowDeploymentStore(tmp_path)
    assert loaded_v1.failure_subscription(source.project_id) is None


def test_schedule_uses_latest_misfire_and_skips_overlap(tmp_path) -> None:
    store = WorkflowDeploymentStore(tmp_path)
    project = store.create_project(schedule_workflow())
    release = store.publish(project.project_id)
    deployment, plaintext = store.activate(
        project.project_id,
        release.version,
        webhooks_enabled=False,
        now=100,
    )

    assert plaintext is None
    assert deployment.next_run_at == 130
    latest = store.materialize_due_schedules(now=195)
    assert len(latest) == 1
    assert latest[0].scheduled_at == 190
    assert latest[0].status == "pending"

    skipped = store.materialize_due_schedules(now=220)
    assert len(skipped) == 1
    assert skipped[0].status == "skipped"
    assert skipped[0].error_summary == "Previous occurrence is still active."


def test_once_schedule_keeps_timezone_aware_timestamp(tmp_path) -> None:
    workflow = schedule_workflow()
    workflow["nodes"][0]["data"].update(
        {
            "scheduleType": "once",
            "onceAt": datetime.fromtimestamp(500, tz=timezone.utc).isoformat(),
        }
    )
    project_store = WorkflowDeploymentStore(tmp_path)
    project = project_store.create_project(workflow)
    release = project_store.publish(project.project_id)
    deployment, _ = project_store.activate(
        project.project_id,
        release.version,
        webhooks_enabled=False,
        now=100,
    )

    assert deployment.next_run_at == 500


def test_once_schedule_interprets_friendly_local_time_in_selected_timezone(tmp_path) -> None:
    workflow = schedule_workflow()
    workflow["nodes"][0]["data"].update(
        {
            "scheduleType": "once",
            "onceAt": "2026-08-20T09:30",
            "timezone": "Asia/Shanghai",
        }
    )
    store = WorkflowDeploymentStore(tmp_path)
    project = store.create_project(workflow)
    release = store.publish(project.project_id)
    deployment, _ = store.activate(
        project.project_id,
        release.version,
        webhooks_enabled=False,
        now=100,
    )

    assert deployment.next_run_at == datetime(
        2026, 8, 20, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai")
    ).timestamp()


def test_cron_schedule_uses_iana_timezone(tmp_path) -> None:
    workflow = schedule_workflow()
    workflow["nodes"][0]["data"].update(
        {
            "scheduleType": "cron",
            "cronExpression": "0 9 * * *",
            "timezone": "America/Phoenix",
        }
    )
    store = WorkflowDeploymentStore(tmp_path)
    project = store.create_project(workflow)
    release = store.publish(project.project_id)
    now = datetime(2026, 8, 17, 8, 30, tzinfo=ZoneInfo("America/Phoenix")).timestamp()
    deployment, _ = store.activate(
        project.project_id,
        release.version,
        webhooks_enabled=False,
        now=now,
    )

    next_local = datetime.fromtimestamp(
        deployment.next_run_at or 0,
        tz=ZoneInfo("America/Phoenix"),
    )
    assert (next_local.hour, next_local.minute) == (9, 0)


def test_corrupt_snapshot_fails_closed_instead_of_erasing_state(tmp_path) -> None:
    snapshot = tmp_path / "workflow_deployments.json"
    snapshot.write_text("{not-json", encoding="utf-8")

    with pytest.raises(
        WorkflowDeploymentValidationError,
        match="refusing to start with empty state",
    ):
        WorkflowDeploymentStore(tmp_path)

    assert snapshot.read_text(encoding="utf-8") == "{not-json"
