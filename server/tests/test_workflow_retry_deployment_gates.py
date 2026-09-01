from __future__ import annotations

from types import SimpleNamespace

import pytest

import server.api.workflow_deployments as deployment_api
from server.evaluations.service import XpertEvaluationService
from server.evaluations.store import EvaluationStateError
from server.workflow_deployments import (
    WorkflowDeploymentConflictError,
    WorkflowDeploymentStore,
    WorkflowDeploymentValidationError,
    WorkflowTriggerExecution,
)
from server.workflow_native.schemas import NativeWorkflowDefinition
from server.xperts.app_api import _deployment_preflight
from server.xperts.app_models import XpertAppPolicy
from server.xperts.models import XpertDefinition, XpertDraft, XpertVersion


def _http_node() -> dict:
    return {
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
            "maxAttempts": 2,
        },
    }


def _workflow(entry_kind: str = "input") -> dict:
    if entry_kind == "http_event_entry":
        entry = {
            "id": "start",
            "type": entry_kind,
            "data": {"kind": entry_kind, "eventVariable": "http_event"},
        }
        end = {
            "id": "end",
            "type": "http_event_reply",
            "data": {
                "kind": "http_event_reply",
                "statusCode": 202,
                "responseBodyType": "text",
                "bodyTemplate": "accepted",
            },
        }
    elif entry_kind == "workflow_call_entry":
        entry = {
            "id": "start",
            "type": entry_kind,
            "data": {"kind": entry_kind, "eventVariable": "call_event"},
        }
        end = {
            "id": "end",
            "type": "output",
            "data": {"kind": "output", "outputVariable": "http_response"},
        }
    elif entry_kind == "form_event_entry":
        entry = {
            "id": "start",
            "type": entry_kind,
            "data": {
                "kind": entry_kind,
                "contractVersion": 1,
                "formTitle": "Synthetic request",
                "formDescription": "Submit a synthetic value.",
                "submitLabel": "Submit",
                "privacyNotice": "Synthetic test only.",
                "successTitle": "Accepted",
                "successMessage": "You may close this page.",
                "theme": "light",
                "eventVariable": "form_event",
                "submissionVariable": "form_submission",
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
            },
        }
        end = {
            "id": "end",
            "type": "output",
            "data": {"kind": "output", "outputVariable": "http_response"},
        }
    elif entry_kind == "rss_event_entry":
        entry = {
            "id": "start",
            "type": entry_kind,
            "data": {
                "kind": entry_kind,
                "contractVersion": 1,
                "feedUrl": "https://feeds.example.test/updates.xml",
                "pollIntervalMinutes": 15,
                "eventVariable": "rss_event",
                "itemVariable": "rss_item",
            },
        }
        end = {
            "id": "end",
            "type": "output",
            "data": {"kind": "output", "outputVariable": "http_response"},
        }
    elif entry_kind == "email_event_entry":
        entry = {
            "id": "start",
            "type": entry_kind,
            "data": {
                "kind": entry_kind,
                "contractVersion": 1,
                "host": "imap.example.test",
                "credentialId": "cred_email",
                "pollIntervalMinutes": 15,
                "eventVariable": "email_event",
                "messageVariable": "email_message",
                "contentVariable": "email_content",
            },
        }
        end = {
            "id": "end",
            "type": "output",
            "data": {"kind": "output", "outputVariable": "http_response"},
        }
    else:
        entry = {
            "id": "start",
            "type": "input",
            "data": {"kind": "input", "variableName": "user_input"},
        }
        end = {
            "id": "end",
            "type": "output",
            "data": {"kind": "output", "outputVariable": "http_response"},
        }
    request = _http_node()
    return {
        "id": "retry-gate",
        "title": "Retry gate",
        "nodes": [entry, request, end],
        "edges": [
            {"id": "e1", "source": "start", "target": "request"},
            {"id": "e2", "source": "request", "target": "end"},
        ],
    }


def _knowledge_workflow() -> dict:
    workflow = _workflow()
    workflow["nodes"][1] = {
        "id": "request",
        "type": "knowledge_retrieval",
        "data": {
            "kind": "knowledge_retrieval",
            "contractVersion": 2,
            "knowledgeBaseId": "kb_local",
            "queryVariable": "user_input",
            "top_k": "5",
            "returnMode": "result",
            "outputVariable": "http_response",
            "failureAction": "stop",
            "retryMode": "transient",
            "maxAttempts": 2,
        },
    }
    return workflow


def test_activation_requires_retry_switch_and_revalidates_knowledge_target(tmp_path) -> None:
    eligible = True
    calls: list[str] = []

    def validate_target(kb_id: str) -> str:
        calls.append(kb_id)
        if not eligible:
            raise ValueError("drifted")
        return "fingerprint-v1"

    store = WorkflowDeploymentStore(
        tmp_path,
        knowledge_retry_validator=validate_target,
    )
    project = store.create_project(_knowledge_workflow())
    release = store.publish(project.project_id)
    assert calls == ["kb_local"]

    with pytest.raises(
        WorkflowDeploymentConflictError,
        match="node retries are disabled",
    ):
        store.activate(
            project.project_id,
            release.version,
            webhooks_enabled=False,
        )

    eligible = False
    with pytest.raises(
        WorkflowDeploymentConflictError,
        match="unavailable or ineligible",
    ):
        store.activate(
            project.project_id,
            release.version,
            webhooks_enabled=False,
            node_retries_enabled=True,
        )
    assert calls == ["kb_local", "kb_local"]


@pytest.mark.parametrize(
    "entry_kind",
    [
        "http_event_entry",
        "form_event_entry",
        "rss_event_entry",
        "email_event_entry",
        "workflow_call_entry",
    ],
)
def test_external_and_callable_entries_reject_configuration_aware_wait(
    tmp_path,
    entry_kind: str,
) -> None:
    store = WorkflowDeploymentStore(
        tmp_path,
        credential_validator=(
            (lambda _credential_id: SimpleNamespace(kind="generic", status="active"))
            if entry_kind == "email_event_entry"
            else None
        ),
        credential_resolver=(
            (
                lambda _credential_id: (
                    '{"username":"synthetic@example.test","password":"not-a-secret"}'
                )
            )
            if entry_kind == "email_event_entry"
            else None
        ),
    )
    project = store.create_project(_workflow(entry_kind))
    if entry_kind == "workflow_call_entry":
        with pytest.raises(WorkflowDeploymentValidationError, match="Callable"):
            store.publish(project.project_id)
        return
    expected_code = {
        "http_event_entry": "http_node_retry_forbidden",
        "form_event_entry": "form_node_retry_forbidden",
        "rss_event_entry": "rss_persistent_wait_forbidden",
        "email_event_entry": "email_persistent_wait_forbidden",
    }[entry_kind]
    with pytest.raises(WorkflowDeploymentValidationError) as raised:
        store.publish(project.project_id)
    assert any(
        issue.get("code") == expected_code and issue.get("node_id") == "request"
        for issue in raised.value.issues
    ), raised.value.issues


def test_public_app_preflight_rejects_retry_configuration() -> None:
    workflow = NativeWorkflowDefinition.model_validate(
        {**_workflow(), "version": "test", "source": "classic"}
    )
    version = XpertVersion(
        version=1,
        draft_revision=1,
        workflow=workflow,
        input_variable="user_input",
        history_variable="history",
        output_variable="http_response",
        checksum="checksum",
        published_at=1.0,
    )
    result = _deployment_preflight(version, XpertAppPolicy())
    assert any(
        issue["code"] == "app_node_retries_forbidden"
        for issue in result["issues"]
    )


def test_evaluation_preflight_rejects_retry_configuration() -> None:
    workflow = NativeWorkflowDefinition.model_validate(
        {**_workflow(), "version": "test", "source": "classic"}
    )
    service = object.__new__(XpertEvaluationService)
    issues, _, _ = service._safe_preflight(workflow, recursion_path=())
    assert any(
        issue["code"] == "evaluation_node_retries_forbidden"
        for issue in issues
    )


def test_evolution_snapshot_path_rejects_retry_configuration() -> None:
    workflow = NativeWorkflowDefinition.model_validate(
        {**_workflow(), "version": "test", "source": "classic"}
    )
    xpert = XpertDefinition(
        id="xpert-retry",
        slug="xpert-retry",
        name="Retry Xpert",
        draft=XpertDraft(
            workflow=workflow,
            input_variable="user_input",
            output_variable="http_response",
        ),
        created_at=1.0,
        updated_at=1.0,
    )
    service = XpertEvaluationService(
        object(),
        xpert_store=object(),
        proposal_store=object(),
        prompt_preflight=lambda current: (
            SimpleNamespace(issues=[]),
            current.draft.workflow,
            [],
        ),
        toolset_store=object(),
        plugin_store=object(),
        rag_service=object(),
        context_store=object(),
    )

    with pytest.raises(EvaluationStateError, match="durable node retries"):
        service.snapshot_xpert_draft(
            xpert,
            source={"kind": "evolution_baseline"},
            label="Evolution baseline",
            model_policy="snapshot",
            override_model_id=None,
            target_id="evolution:xpert-retry:r1",
        )


@pytest.mark.asyncio
async def test_activation_api_passes_retry_feature_switch(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Store:
        def activate(self, project_id: str, version: int, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(trigger_kind="manual"), None

        @staticmethod
        def serialize_deployment(_deployment) -> dict:
            return {"active": True}

    monkeypatch.setattr(deployment_api, "_store", Store())
    monkeypatch.setenv("WORKFLOW_NODE_RETRIES_ENABLED", "true")
    result = await deployment_api.activate_workflow("wf_test", 1)
    assert result == {"active": True}
    assert captured["node_retries_enabled"] is True


def test_deployment_retry_timeline_is_safe_idempotent_and_bounded(tmp_path) -> None:
    store = WorkflowDeploymentStore(tmp_path)
    execution = WorkflowTriggerExecution(
        execution_id="wfx_retry",
        project_id="wf_retry",
        version=1,
        deployment_id="wfd_retry",
        trigger_kind="schedule",
        occurrence_key="schedule:synthetic",
    )
    with store._lock:
        store._executions[execution.execution_id] = execution
        store._persist_unlocked()

    for index in range(10):
        event = {
            "event": "node_retry_scheduled",
            "node_id": f"node-{index}",
            "node_type": "http_request",
            "attempt": 2,
            "max_attempts": 3,
            "resume_at": 100 + index,
            "error_code": "HTTP_TIMEOUT",
            "classification": "transient",
            "url": f"https://secret.invalid/{index}?token=sentinel",
        }
        store.record_execution_retry_event(execution.execution_id, event)

    duplicate = {
        "event": "node_retry_scheduled",
        "node_id": "node-9",
        "node_type": "http_request",
        "attempt": 2,
        "max_attempts": 3,
        "resume_at": 999,
        "error_code": "HTTP_TIMEOUT",
        "classification": "transient",
        "response_body": "sentinel-response-body",
    }
    store.record_execution_retry_event(execution.execution_id, duplicate)

    saved = store.get_execution(execution.execution_id)
    assert saved is not None
    events = saved.trigger_summary["retry_events"]
    assert len(events) == 8
    assert [event["node_id"] for event in events] == [
        f"node-{index}" for index in range(2, 10)
    ]
    assert events[-1]["resume_at"] == 109
    assert "sentinel" not in str(events)
    assert all(
        set(event)
        == {
            "event",
            "node_id",
            "node_type",
            "attempt",
            "max_attempts",
            "resume_at",
            "error_code",
            "classification",
        }
        for event in events
    )
