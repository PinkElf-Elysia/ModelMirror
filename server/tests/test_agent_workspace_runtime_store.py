from __future__ import annotations

from pathlib import Path

import pytest

from server.agent_workspace.runtime_store import (
    AgentRuntimeStore,
    RuntimeConflictError,
)


def test_session_messages_tasks_and_events_survive_restart(tmp_path: Path) -> None:
    root = tmp_path / "agent-workspace"
    store = AgentRuntimeStore(root)
    session = store.create_session(
        agent_id="default_agent",
        title="Runtime smoke",
        model_id="openai/test-model",
        thinking_level="medium",
        approval_mode="always-ask",
        skillset_id="general-agent-default",
    )
    task = store.create_task(
        session.session_id,
        prompt="Inspect the workspace",
        kind="chat",
        model_id=session.model_id,
        thinking_level=session.thinking_level,
        approval_mode=session.approval_mode,
    )
    store.update_task(task.task_id, status="running")
    store.append_message(
        session.session_id,
        task_id=task.task_id,
        role="assistant",
        content="Working",
    )
    first = store.append_event(
        session.session_id,
        "text_delta",
        task_id=task.task_id,
        payload={"delta": "Working"},
    )
    second = store.append_event(
        session.session_id,
        "completed",
        task_id=task.task_id,
        payload={"output": "Done"},
    )
    store.update_task(task.task_id, status="completed", output="Done")

    restarted = AgentRuntimeStore(root)
    detail = restarted.get_session_detail(session.session_id)

    assert detail.session.status == "idle"
    assert [message.role for message in detail.messages] == ["user", "assistant"]
    assert detail.tasks[-1].output == "Done"
    assert second.sequence > first.sequence
    assert [event.type for event in restarted.list_events(
        session.session_id, after=first.sequence
    )][0] == "completed"
    assert detail.last_event_sequence >= second.sequence
    assert restarted.session_workspace(session.session_id).is_dir()


def test_only_one_active_task_per_session(tmp_path: Path) -> None:
    store = AgentRuntimeStore(tmp_path / "workspace")
    session = store.create_session(
        agent_id="default_agent",
        title="Serial",
        model_id="test/model",
        thinking_level="low",
        approval_mode="read-only",
        skillset_id="general-agent-default",
    )
    store.create_task(
        session.session_id,
        prompt="one",
        kind="chat",
        model_id=session.model_id,
        thinking_level=session.thinking_level,
        approval_mode=session.approval_mode,
    )

    with pytest.raises(RuntimeConflictError, match="active task"):
        store.create_task(
            session.session_id,
            prompt="two",
            kind="chat",
            model_id=session.model_id,
            thinking_level=session.thinking_level,
            approval_mode=session.approval_mode,
        )


def test_approval_is_durable_and_single_decision(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    store = AgentRuntimeStore(root)
    session = store.create_session(
        agent_id="default_agent",
        title="Approval",
        model_id="test/model",
        thinking_level="medium",
        approval_mode="always-ask",
        skillset_id="general-agent-default",
    )
    task = store.create_task(
        session.session_id,
        prompt="write a file",
        kind="chat",
        model_id=session.model_id,
        thinking_level=session.thinking_level,
        approval_mode=session.approval_mode,
    )
    approval = store.create_approval(
        session_id=session.session_id,
        task_id=task.task_id,
        tool_call_id="call_write",
        tool_name="write_file",
        arguments={"file_path": "result.txt", "content": "ok"},
    )

    decided = AgentRuntimeStore(root).decide_approval(
        approval.approval_id, approved=True, message="reviewed"
    )
    assert decided.status == "approved"
    assert decided.decision_message == "reviewed"

    with pytest.raises(RuntimeConflictError, match="already"):
        store.decide_approval(approval.approval_id, approved=False)
