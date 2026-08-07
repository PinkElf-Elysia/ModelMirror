from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

import server.main as main_module
from server.skills.creator_evidence import (
    CreatorEvidenceError,
    _sanitize_text,
    build_creator_evidence_preview,
)
from server.xpert_runtime.execution_store import (
    WorkflowExecutionConflictError,
    WorkflowExecutionStore,
)
from server.xperts.context import XpertContextConflictError, XpertContextStore


def _create_completed_execution(
    store: WorkflowExecutionStore,
    *,
    task_id: str = "task-1",
    run_id: str = "run-1",
    run_type: str = "workflow",
    source_kind: str | None = "workflow_classic",
    runtime_metadata: dict | None = None,
):
    item = store.create(
        task_id=task_id,
        run_id=run_id,
        run_type=run_type,
        source_kind=source_kind,  # type: ignore[arg-type]
        workflow={
            "id": "workflow-1",
            "title": "安全审计流程",
            "nodes": [
                {"id": "input", "type": "input", "data": {"kind": "input"}},
                {
                    "id": "audit",
                    "type": "workflow_agent",
                    "data": {
                        "kind": "workflow_agent",
                        "title": "检查 C:\\Users\\alice\\private.txt",
                    },
                },
                {
                    "id": "skipped",
                    "type": "http_request",
                    "data": {"kind": "http_request", "title": "未执行分支"},
                },
                {"id": "output", "type": "output", "data": {"kind": "output"}},
            ],
            "edges": [],
        },
        inputs={
            "user_input": (
                "审计 C:\\Users\\alice\\private.txt，"
                "API_KEY=super-secret-value"
            ),
            "conversation_history": "不得进入素材的完整历史",
            "password": "also-secret",
        },
        runtime_metadata={
            "workflow_title": "安全审计流程",
            **dict(runtime_metadata or {}),
        },
    )
    store.append_event(
        task_id,
        {
            "event": "node_end",
            "node_id": "audit",
            "node_title": "检查本地文件",
            "node_type": "workflow_agent",
            "status": "completed",
        },
    )
    store.append_event(
        task_id,
        {
            "event": "tool_finished",
            "tool_name": "security_scan",
            "arguments": {"authorization": "Bearer raw-tool-secret"},
            "status": "completed",
        },
    )
    fake_prefixed_token = "sk-" + "test-only-redaction-value"
    return store.complete(
        task_id,
        result=(
            "报告保存在 /home/alice/report.txt；"
            "副本位于 \\\\fileserver\\private-share\\report.txt、"
            "/Volumes/team/report.txt 与 /srv/modelmirror/report.txt；"
            "Authorization: Bearer testonlybearervalue；" + fake_prefixed_token
        ),
    )


def _candidate(preview, kind: str):
    return next(item for item in preview.candidates if item.kind == kind)


def test_classic_preview_is_stable_bounded_and_redacted(tmp_path: Path) -> None:
    executions = WorkflowExecutionStore(tmp_path / "executions")
    _create_completed_execution(executions)

    first = build_creator_evidence_preview(
        executions,
        source_kind="workflow_classic",
        source_task_id="task-1",
        source_run_id="run-1",
    )
    second = build_creator_evidence_preview(
        executions,
        source_kind="workflow_classic",
        source_task_id="task-1",
        source_run_id="run-1",
    )

    assert first.preview_fingerprint == second.preview_fingerprint
    assert [item.candidate_id for item in first.candidates] == [
        item.candidate_id for item in second.candidates
    ]
    assert _candidate(first, "final_output_excerpt").default_selected is False
    assert _candidate(first, "successful_steps").summary.startswith("1. 检查")
    assert "未执行分支" not in _candidate(first, "successful_steps").summary
    assert _candidate(first, "tool_names").summary == "security_scan"
    io_summary = _candidate(first, "io_shape").summary
    assert "user_input" in io_summary
    assert "conversation_history" not in io_summary
    assert "password" not in io_summary

    payload = first.to_payload()
    assert "preview_fingerprint" in payload
    assert {"summary", "default_selected"} <= set(payload["candidates"][0])
    serialized = json.dumps(payload, ensure_ascii=False)
    for forbidden in (
        "super-secret-value",
        "also-secret",
        "raw-tool-secret",
        "test-only-redaction-value",
        "C:\\Users\\alice",
        "/home/alice",
        "fileserver",
        "/Volumes/team",
        "/srv/modelmirror",
        "不得进入素材的完整历史",
    ):
        assert forbidden not in serialized
    assert "[REDACTED]" in serialized
    assert "[LOCAL_PATH]" in serialized


@pytest.mark.parametrize(
    ("raw", "forbidden"),
    [
        ("password: my secret phrase", ("my", "secret", "phrase")),
        ('API_KEY="quoted secret phrase"', ("quoted", "secret", "phrase")),
        (r"C:\Users\Alice Smith\secret.txt", ("Alice", "Smith", "secret.txt")),
        ("C:/Users/Alice Smith/secret.txt", ("Alice", "Smith", "secret.txt")),
        (
            "file:///C:/Users/Alice Smith/secret.txt",
            ("C:/Users", "Alice", "Smith", "secret.txt"),
        ),
        (r"\\file server\private share\secret.txt", ("file server", "private share")),
        ("/Volumes/Private Disk/secret.txt", ("Private", "Disk", "secret.txt")),
        ("/srv/Model Mirror/secret.txt", ("Model", "Mirror", "secret.txt")),
    ],
)
def test_sanitize_text_redacts_spaced_secrets_and_local_paths(
    raw: str,
    forbidden: tuple[str, ...],
) -> None:
    sanitized = _sanitize_text(raw, max_chars=500)

    assert all(value not in sanitized for value in forbidden)
    assert "[REDACTED]" in sanitized or "[LOCAL_PATH]" in sanitized


def test_unmarked_legacy_or_forged_metadata_never_becomes_trusted(
    tmp_path: Path,
) -> None:
    executions = WorkflowExecutionStore(tmp_path / "executions")
    _create_completed_execution(
        executions,
        source_kind=None,
        runtime_metadata={"source_kind": "workflow_classic"},
    )
    restored = WorkflowExecutionStore(tmp_path / "executions").require("task-1")
    assert restored.source_kind is None

    with pytest.raises(CreatorEvidenceError) as caught:
        build_creator_evidence_preview(
            executions,
            source_kind="workflow_classic",
            source_task_id="task-1",
            source_run_id="run-1",
        )
    assert caught.value.code == "source_kind_mismatch"


@pytest.mark.parametrize(
    ("run_type", "metadata"),
    [
        ("xpert_app", {"app_id": "app-1"}),
        ("xpert_evaluation", {"evaluation_run_id": "eval-1"}),
        ("xpert", {"goal_id": "goal-1"}),
        ("xpert", {"handoff_id": "handoff-1"}),
    ],
)
def test_public_app_evaluation_goal_and_handoff_are_untrusted(
    tmp_path: Path,
    run_type: str,
    metadata: dict,
) -> None:
    executions = WorkflowExecutionStore(tmp_path / run_type)
    _create_completed_execution(
        executions,
        run_type=run_type,
        source_kind=None,
        runtime_metadata=metadata,
    )
    with pytest.raises(CreatorEvidenceError) as caught:
        build_creator_evidence_preview(
            executions,
            source_kind="xpert_chat",
            source_task_id="task-1",
            source_run_id="run-1",
            context_store=XpertContextStore(tmp_path / f"context-{run_type}"),
            source_xpert_id="xpert-1",
            source_conversation_id="conversation-1",
            source_message_id="message-1",
        )
    assert caught.value.code == "source_kind_mismatch"


def test_incomplete_and_changed_run_sources_fail_closed(tmp_path: Path) -> None:
    executions = WorkflowExecutionStore(tmp_path / "executions")
    executions.create(
        task_id="task-1",
        run_id="run-1",
        run_type="workflow",
        source_kind="workflow_classic",
        workflow={"nodes": [], "edges": []},
        inputs={},
    )
    with pytest.raises(CreatorEvidenceError) as waiting:
        build_creator_evidence_preview(
            executions,
            source_kind="workflow_classic",
            source_task_id="task-1",
            source_run_id="run-1",
        )
    assert waiting.value.code == "source_not_completed"

    executions.complete("task-1", result="done")
    executions.update_run_id("task-1", run_id="run-2")
    with pytest.raises(CreatorEvidenceError) as stale:
        build_creator_evidence_preview(
            executions,
            source_kind="workflow_classic",
            source_task_id="task-1",
            source_run_id="run-1",
        )
    assert stale.value.code == "source_run_mismatch"


def test_xpert_preview_requires_exact_bound_assistant_message(tmp_path: Path) -> None:
    executions = WorkflowExecutionStore(tmp_path / "executions")
    context = XpertContextStore(tmp_path / "context")
    conversation = context.create_conversation("xpert-1", title="修复发布流程")
    context.append_message(
        "xpert-1",
        conversation.conversation_id,
        role="user",
        content="Actually，请改成先验证再发布。token=do-not-keep-this",
        source_task_id="task-1",
        source_run_id="run-1",
    )
    assistant = context.append_message(
        "xpert-1",
        conversation.conversation_id,
        role="assistant",
        content="已经按要求完成。",
        source_task_id="task-1",
        source_run_id="run-1",
    )
    _create_completed_execution(
        executions,
        run_type="xpert",
        source_kind="xpert_chat",
        runtime_metadata={
            "xpert_id": "xpert-1",
            "conversation_id": conversation.conversation_id,
        },
    )

    preview = build_creator_evidence_preview(
        executions,
        source_kind="xpert_chat",
        source_task_id="task-1",
        source_run_id="run-1",
        context_store=context,
        source_xpert_id="xpert-1",
        source_conversation_id=conversation.conversation_id,
        source_message_id=assistant.message_id,
    )
    assert preview.source_title == "修复发布流程"
    correction = _candidate(preview, "user_correction")
    assert "先验证再发布" in correction.summary
    assert "do-not-keep-this" not in correction.summary

    for override, expected_code in (
        ({"source_xpert_id": "xpert-2"}, "xpert_source_mismatch"),
        ({"source_message_id": "missing-message"}, "source_message_mismatch"),
        ({"source_run_id": "run-stale"}, "source_run_mismatch"),
    ):
        arguments = {
            "source_kind": "xpert_chat",
            "source_task_id": "task-1",
            "source_run_id": "run-1",
            "context_store": context,
            "source_xpert_id": "xpert-1",
            "source_conversation_id": conversation.conversation_id,
            "source_message_id": assistant.message_id,
            **override,
        }
        with pytest.raises(CreatorEvidenceError) as caught:
            build_creator_evidence_preview(executions, **arguments)
        assert caught.value.code == expected_code


def test_xpert_message_binding_persists_and_rebinds_on_recovery(tmp_path: Path) -> None:
    context = XpertContextStore(tmp_path / "context")
    conversation = context.create_conversation("xpert-1")
    first = context.append_message(
        "xpert-1",
        conversation.conversation_id,
        role="user",
        content="请继续",
    )
    context.bind_message_execution(
        "xpert-1",
        conversation.conversation_id,
        first.message_id,
        source_task_id="task-1",
        source_run_id="run-1",
    )
    assert context.rebind_execution_run(
        "xpert-1",
        conversation.conversation_id,
        source_task_id="task-1",
        previous_run_id="run-1",
        source_run_id="run-2",
    ) == 1
    assert context.rebind_execution_run(
        "xpert-1",
        conversation.conversation_id,
        source_task_id="task-1",
        previous_run_id="run-1",
        source_run_id="run-2",
    ) == 0

    restored = XpertContextStore(tmp_path / "context").get_conversation(
        "xpert-1",
        conversation.conversation_id,
    )
    restored_message = next(item for item in restored.messages if item.message_id == first.message_id)
    assert restored_message.source_task_id == "task-1"
    assert restored_message.source_run_id == "run-2"
    with pytest.raises(XpertContextConflictError):
        context.bind_message_execution(
            "xpert-1",
            conversation.conversation_id,
            first.message_id,
            source_task_id="other-task",
            source_run_id="other-run",
        )


def test_execution_store_rejects_source_kind_run_type_conflicts(tmp_path: Path) -> None:
    executions = WorkflowExecutionStore(tmp_path / "executions")
    with pytest.raises(WorkflowExecutionConflictError):
        executions.create(
            task_id="task-1",
            run_id="run-1",
            run_type="xpert_app",
            source_kind="xpert_chat",
            workflow={"nodes": [], "edges": []},
            inputs={},
        )


@pytest.mark.asyncio
async def test_classic_route_assigns_source_kind_and_persists_completed_nodes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executions = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(main_module, "workflow_execution_store", executions)
    main_module.request_windows.clear()
    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"x-forwarded-for": "198.51.100.73"},
    ) as client:
        response = await client.post(
            "/api/workflow/run",
            json={
                "source_kind": "xpert_chat",
                "workflow": {
                    "id": "trusted-classic",
                    "title": "Trusted classic",
                    "nodes": [
                        {
                            "id": "input",
                            "type": "input",
                            "data": {"kind": "input", "variableName": "user_input"},
                        },
                        {
                            "id": "output",
                            "type": "output",
                            "data": {"kind": "output", "outputVariable": "user_input"},
                        },
                    ],
                    "edges": [{"id": "e1", "source": "input", "target": "output"}],
                },
                "inputs": {"user_input": "hello"},
            },
        )
    assert response.status_code == 200, response.text
    task_id = response.headers["X-ModelMirror-Runtime-Task-Id"]
    execution = executions.require(task_id)
    assert execution.status == "completed"
    assert execution.source_kind == "workflow_classic"
    assert [event["node_id"] for event in execution.events if event["event"] == "node_end"] == [
        "input",
        "output",
    ]
    main_module.request_windows.clear()
