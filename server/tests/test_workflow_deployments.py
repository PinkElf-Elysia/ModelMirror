from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from server.workflow_deployments import (
    WorkflowDeploymentConflictError,
    WorkflowDeploymentStore,
    WorkflowDeploymentValidationError,
)


def test_server_image_includes_workflow_deployment_module() -> None:
    dockerfile = Path(__file__).resolve().parents[1] / "Dockerfile"

    assert "COPY workflow_deployments.py ." in dockerfile.read_text(encoding="utf-8")


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
