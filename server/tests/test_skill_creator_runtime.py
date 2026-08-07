from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.skills.creator_runtime import (
    TrustedCreatorSourceProvider,
    WorkflowCreatorGenerationExecutor,
    build_creator_workflow_invocation,
)
from server.skills.creator_service import CreatorGenerationRequest, CreatorSourceDescriptor
from server.skills.creator_store import (
    SkillCreatorConflictError,
    SkillCreatorSession,
    SkillCreatorValidationError,
)
from server.xpert_runtime.authoring_store import AuthoringProposalStore
from server.xpert_runtime.execution_store import WorkflowExecutionStore
from server.xperts.context import XpertContextStore


def _request() -> CreatorGenerationRequest:
    return CreatorGenerationRequest(
        session={
            "session_id": "skillcreator_test",
            "session_revision": 3,
            "intent": "Create repeatable PDF evidence reports.",
            "positive_examples": ["Summarize this PDF with page references."],
            "near_miss_examples": ["Only extract the raw text."],
            "expected_output": "A concise report with page references.",
            "success_criteria": ["Every claim includes a page number."],
            "selected_evidence": [],
        },
        target_draft=None,
        allowed_tool="skill_authoring_propose_create",
    )


def _create_proposal(store: AuthoringProposalStore):
    return store.create(
        kind="skill_create",
        title="Create PDF evidence reporter",
        payload={
            "skill": {
                "name": "pdf-evidence-reporter",
                "slug": "pdf-evidence-reporter",
                "description": (
                    "Create cited PDF evidence reports when a user needs claims "
                    "traced to page numbers."
                ),
                "skill_markdown": (
                    "---\n"
                    "name: pdf-evidence-reporter\n"
                    "description: Create cited PDF evidence reports when a user needs "
                    "claims traced to page numbers.\n"
                    "---\n\n"
                    "# PDF evidence reporter\n\nRead the document and cite every claim.\n"
                ),
                "files": {},
            }
        },
        source_type="skill_creator",
        source_id="skillcreator_test",
        source_run_id="agent-run-1",
        source_task_id="workflow-task-1",
        creator_session_id="skillcreator_test",
        creator_session_revision=3,
        actor_kind="workflow_agent",
        actor_id="skill-creator-assistant-v1",
    )


def test_creator_workflow_is_fixed_to_one_typed_tool() -> None:
    invocation = build_creator_workflow_invocation(
        _request(), model_id="gateway/default-text"
    )

    agent = next(
        node
        for node in invocation.workflow["nodes"]
        if node["data"]["kind"] == "workflow_agent"
    )
    middleware = next(
        node
        for node in invocation.workflow["nodes"]
        if node["data"]["kind"] == "runtime_middleware"
    )
    assert agent["data"]["agentName"] == "skill-creator-assistant-v1"
    assert agent["data"]["modelId"] == "gateway/default-text"
    assert agent["data"]["toolNames"] == "skill_authoring_propose_create"
    assert agent["data"]["maxToolCalls"] == "2"
    assert agent["data"]["parallelToolCalls"] == "false"
    assert middleware["data"]["runtimeMiddlewareConfig"] == {
        "allow_create": True,
        "allow_update": False,
        "allowed_draft_ids": "",
    }
    assert invocation.runtime_metadata["creator_session_id"] == "skillcreator_test"
    assert invocation.runtime_metadata["creator_requirement_ids"] == [
        "intent",
        "positive_example:0",
        "near_miss:0",
        "expected_output",
        "success_criterion:0",
    ]
    assert "progressive disclosure" in agent["data"]["rolePrompt"].lower()
    assert "requirement_coverage" in agent["data"]["rolePrompt"]
    assert "## 用途与边界" in agent["data"]["rolePrompt"]
    generation_context = json.loads(invocation.inputs["creator_request"])
    assert generation_context["quality_requirements"]["accepted_heading_contract"][
        "purpose_and_scope"
    ] == [
        "Purpose and scope",
        "用途与边界",
        "适用范围与边界",
        "适用场景与边界",
    ]
    assert "repository" not in invocation.inputs["creator_request"].lower()


@pytest.mark.asyncio
async def test_creator_executor_replays_one_bound_pending_proposal(
    tmp_path: Path,
) -> None:
    store = AuthoringProposalStore(tmp_path)
    proposal = _create_proposal(store)
    calls = 0

    async def runner(_invocation):
        nonlocal calls
        calls += 1

    executor = WorkflowCreatorGenerationExecutor(
        store,
        model_id="gateway/default-text",
        model_available=lambda: True,
        runner=runner,
    )
    result = await executor.generate(_request())

    assert calls == 0
    assert result.proposal_id == proposal.proposal_id
    assert result.runtime_run_id == "agent-run-1"
    assert result.runtime_task_id == "workflow-task-1"


@pytest.mark.asyncio
async def test_creator_executor_fails_when_agent_does_not_call_tool(
    tmp_path: Path,
) -> None:
    store = AuthoringProposalStore(tmp_path)

    async def runner(_invocation):
        return None

    executor = WorkflowCreatorGenerationExecutor(
        store,
        model_id="gateway/default-text",
        model_available=lambda: True,
        runner=runner,
    )

    with pytest.raises(SkillCreatorValidationError) as caught:
        await executor.generate(_request())
    assert caught.value.code == "skill_creator_tool_not_called"


@pytest.mark.asyncio
async def test_creator_executor_accepts_exactly_one_proposal_from_runner(
    tmp_path: Path,
) -> None:
    store = AuthoringProposalStore(tmp_path)

    async def runner(_invocation):
        _create_proposal(store)

    executor = WorkflowCreatorGenerationExecutor(
        store,
        model_id="gateway/default-text",
        model_available=lambda: True,
        runner=runner,
    )

    result = await executor.generate(_request())
    assert store.require(result.proposal_id).creator_session_revision == 3


def test_trusted_source_provider_rebuilds_and_checks_classic_evidence(
    tmp_path: Path,
) -> None:
    execution_store = WorkflowExecutionStore(tmp_path)
    execution_store.create(
        task_id="task-classic",
        run_id="run-classic",
        run_type="workflow",
        source_kind="workflow_classic",
        workflow={
            "id": "workflow-1",
            "title": "PDF report workflow",
            "nodes": [
                {
                    "id": "summarize",
                    "type": "llm",
                    "data": {"kind": "llm", "title": "Summarize PDF"},
                }
            ],
        },
        inputs={"user_input": "Summarize the PDF with page references."},
    )
    execution_store.append_event(
        "task-classic",
        {
            "event": "node_end",
            "node_id": "summarize",
            "status": "completed",
        },
    )
    execution_store.complete("task-classic", result="Report with cited pages.")
    provider = TrustedCreatorSourceProvider(
        execution_store,
        XpertContextStore(tmp_path),
    )
    descriptor = CreatorSourceDescriptor(
        source_kind="workflow_classic",
        source_task_id="task-classic",
        source_run_id="run-classic",
    )
    provider.validate_source(descriptor)
    session = SkillCreatorSession(
        session_id="skillcreator_source",
        mode="run",
        source_kind="workflow_classic",
        source_task_id="task-classic",
        source_run_id="run-classic",
    )
    preview = provider.preview(session)
    assert preview.candidates
    assert all(item["summary"] for item in preview.candidates)

    selected = provider.select(
        session,
        preview_fingerprint=preview.fingerprint,
        candidate_ids=[preview.candidates[0]["candidate_id"]],
    )
    assert selected[0]["content_hash"] == preview.candidates[0]["content_hash"]
    assert selected[0]["title"]

    with pytest.raises(SkillCreatorConflictError):
        provider.select(
            session,
            preview_fingerprint="0" * 64,
            candidate_ids=[],
        )
