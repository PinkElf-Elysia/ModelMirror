from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

import server.main as main_module
from server.skills.creator_quality import CREATOR_CONTRACT_VERSION
from server.skills.creator_runtime import build_creator_workflow_invocation
from server.skills.creator_runtime import WorkflowCreatorGenerationExecutor
from server.skills.creator_service import (
    CreatorGenerationRequest,
    CreatorGenerationResult,
    SkillCreatorService,
)
from server.skills.creator_store import (
    CREATOR_ASSISTANT_AGENT_ID,
    SkillCreatorConflictError,
    SkillCreatorNotFoundError,
    SkillCreatorSessionStore,
)
from server.skills.draft_store import WorkspaceSkillDraftStore
from server.xpert_runtime import WorkflowExecutionStore
from server.xpert_runtime import authoring_api
from server.xpert_runtime.authoring_service import AuthoringService
from server.xpert_runtime.authoring_store import (
    AuthoringProposalConflictError,
    AuthoringProposalStore,
)
from server.xpert_runtime.authoring_toolset import AuthoringToolsetProvider
from server.xpert_runtime.run_registry import RunRegistry
from server.xpert_runtime.toolset import RuntimeToolCall, RuntimeToolError
from server.xperts import XpertStore


SKILL_PACKAGE = {
    "name": "review-notes",
    "slug": "review-notes",
    "description": (
        "Review meeting notes into an evidence-based action report with owners and "
        "unresolved questions. Use when users provide completed notes for quality review; "
        "do not use for fictional prose or unrelated document summaries."
    ),
    "skill_markdown": """---
name: review-notes
description: Review meeting notes into an evidence-based action report with owners and unresolved questions. Use when users provide completed notes for quality review; do not use for fictional prose or unrelated document summaries.
---

# Review notes

## Purpose and boundaries

Turn completed meeting notes into a factual quality report. Preserve speaker and source
labels, distinguish decisions from suggestions, and reject requests to invent missing
decisions or rewrite unrelated material as fiction.

## Inputs and prerequisites

Require the meeting notes, the meeting objective, and any known owners or deadlines. Ask
for clarification when the source is incomplete, ambiguous, or lacks enough context to
assign an action safely.

## Workflow

1. Normalize headings and speaker labels while preserving the original evidence.
2. Extract decisions, open questions, and proposed actions without inferring absent facts.
3. Connect each action to its supporting note and identify missing owner or due date fields.
4. Draft the report using the required output contract and explicit missing-value markers.
5. Run the checks in `references/checklist.md` and report any failed check before delivery.

## Output contract

Return a concise report with sections for decisions, open questions, and actions. Each
action must include `Evidence`, `Owner`, `Due`, and `Status`. Use `unknown` when the notes
do not provide a value, and never silently omit a required field.

## Quality checks

Confirm every reported item is traceable to the notes, every action has an explicit owner
or `unknown`, and unresolved ambiguity is visible. Apply `references/checklist.md` before
claiming the report is complete.

## Failure and degradation

If the notes are unavailable or too incomplete, stop and return the missing inputs. If
evidence conflicts, preserve both accounts and request clarification. If a check cannot be
completed, label the output partial rather than inventing a successful result.

## Resources

Read `references/checklist.md` only during the verification step; it defines the final
report completeness checks and does not replace the source notes.
""",
    "files": {
        "references/checklist.md": (
            "# Review checklist\n\n"
            "- Trace every decision and action to a source note.\n"
            "- Require Owner, Due, Status, and Evidence fields.\n"
            "- Mark unavailable values as unknown.\n"
        )
    },
}

CREATOR_REQUIREMENT_IDS = [
    "intent",
    "positive_example:0",
    "near_miss:0",
    "expected_output",
    "success_criterion:0",
]

CREATOR_DESIGN = {
    "workflow_steps": [
        {"id": "normalize", "description": "Normalize notes without losing evidence."},
        {"id": "extract", "description": "Extract decisions and open questions."},
        {"id": "draft", "description": "Draft actions with required fields."},
        {"id": "verify", "description": "Verify the report before delivery."},
    ],
    "output_contract": [
        {"id": "report", "description": "Return decisions, questions, and owned actions."}
    ],
    "failure_modes": [
        {"id": "missing", "description": "Return missing inputs instead of guessing."}
    ],
    "resources": [
        {
            "path": "references/checklist.md",
            "purpose": "Check report evidence and required fields.",
            "used_by_steps": ["verify"],
        }
    ],
    "assumptions": ["The supplied notes are the authoritative source."],
    "requirement_coverage": [
        {
            "requirement_id": "intent",
            "locations": [{"path": "SKILL.md", "section": "Purpose and boundaries"}],
        },
        {
            "requirement_id": "positive_example:0",
            "locations": [{"path": "SKILL.md", "section": "Workflow"}],
        },
        {
            "requirement_id": "near_miss:0",
            "locations": [{"path": "SKILL.md", "section": "Purpose and boundaries"}],
        },
        {
            "requirement_id": "expected_output",
            "locations": [{"path": "SKILL.md", "section": "Output contract"}],
        },
        {
            "requirement_id": "success_criterion:0",
            "locations": [{"path": "SKILL.md", "section": "Quality checks"}],
        },
    ],
}


def _creator_tool_arguments(skill: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "title": "Create review notes",
        "skill": dict(skill or SKILL_PACKAGE),
        "design": CREATOR_DESIGN,
        "creator_contract_version": CREATOR_CONTRACT_VERSION,
    }


def _thin_creator_tool_arguments() -> dict[str, Any]:
    skill = dict(SKILL_PACKAGE)
    skill["skill_markdown"] = """---
name: review-notes
description: Review meeting notes into an evidence-based action report with owners and unresolved questions. Use when users provide completed notes for quality review; do not use for fictional prose or unrelated document summaries.
---

# Review notes

Return a concise report.
"""
    skill["files"] = {}
    design = dict(CREATOR_DESIGN)
    design["resources"] = []
    return {
        "title": "Create incomplete review notes",
        "skill": skill,
        "design": design,
        "creator_contract_version": CREATOR_CONTRACT_VERSION,
    }


def _creator_proposal_payload(skill: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "skill": dict(skill or SKILL_PACKAGE),
        "design": CREATOR_DESIGN,
        "creator_contract_version": CREATOR_CONTRACT_VERSION,
        "creator_requirement_ids": CREATOR_REQUIREMENT_IDS,
    }


def _services(tmp_path: Path, *, executor: Any = None):
    runtime_dir = tmp_path / "runtime"
    draft_store = WorkspaceSkillDraftStore(runtime_dir)
    authoring = AuthoringService(
        AuthoringProposalStore(runtime_dir),
        XpertStore(tmp_path / "xperts"),
        draft_store,
        local_console_actor_id="console_regression_test",
    )
    creator = SkillCreatorService(
        SkillCreatorSessionStore(runtime_dir),
        draft_store,
        authoring,
        enabled=True,
        generation_executor=executor,
    )
    return creator, authoring, draft_store


def _ready_blank_session(service: SkillCreatorService):
    session = service.create_session(
        mode="blank",
        intent="Turn a repeatable review process into a Skill.",
        positive_examples=["Review these meeting notes."],
        near_miss_examples=["Summarize an unrelated novel."],
        expected_output="A concise quality report.",
        success_criteria=["Identify unclear actions", "Keep the report concise"],
    )
    preview = service.preview_source(session.session_id)
    return service.select_evidence(
        session.session_id,
        expected_session_revision=session.session_revision,
        preview_fingerprint=preview.fingerprint,
        candidate_ids=[],
    )


class _ProposalExecutor:
    def __init__(
        self,
        provider: AuthoringToolsetProvider,
        *,
        started: asyncio.Event | None = None,
        release: asyncio.Event | None = None,
    ) -> None:
        self.provider = provider
        self.started = started
        self.release = release
        self.calls = 0

    def available(self) -> bool:
        return True

    async def generate(
        self, request: CreatorGenerationRequest
    ) -> CreatorGenerationResult:
        self.calls += 1
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            await self.release.wait()
        session = request.session
        creator_config = {"allow_create": True}
        if request.allowed_tool == "skill_authoring_propose_update":
            creator_config = {
                "allow_update": True,
                "allowed_draft_ids": str(
                    (request.target_draft or {}).get("draft_id") or ""
                ),
            }
        result = await self.provider.call_tool(
            RuntimeToolCall(
                request.allowed_tool,
                _creator_tool_arguments(),
                {
                    "runtime_run_type": "workflow",
                    "creator_session_id": session["session_id"],
                    "creator_session_revision": session["session_revision"],
                    "run_id": "creator-run-regression",
                    "task_id": "creator-task-regression",
                    "creator_requirement_ids": CREATOR_REQUIREMENT_IDS,
                    "skill_creator_config": creator_config,
                },
            )
        )
        payload = json.loads(result.output)
        return CreatorGenerationResult(
            proposal_id=payload["proposal_id"],
            tool_name=request.allowed_tool,
            runtime_run_id="creator-run-regression",
            runtime_task_id="creator-task-regression",
        )


class _EmptyMcpProvider:
    def __init__(self) -> None:
        self.list_calls = 0

    async def list_tools(self):
        self.list_calls += 1
        return []

    async def find_tool(self, _tool_name: str):
        return None

    async def call_tool(self, _call):
        raise AssertionError("The dedicated Creator workflow must not call MCP.")


def _creator_create_decision(arguments: dict[str, Any]) -> str:
    return json.dumps(
        {
            "tool": "skill_authoring_propose_create",
            "arguments": arguments,
        },
        ensure_ascii=False,
    )


def _prepare_creator_create_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    responses: list[str],
    *,
    provider_error_code: str | None = None,
):
    _, authoring, _ = _services(tmp_path)
    creator_provider = AuthoringToolsetProvider(authoring, "skill")
    empty_mcp = _EmptyMcpProvider()
    model_messages: list[str] = []

    async def fake_collect_chat_completion_text(
        _model_id: str,
        messages,
        **_kwargs: Any,
    ) -> str:
        model_messages.append(
            "\n".join(str(message.content or "") for message in messages)
        )
        response_index = len(model_messages) - 1
        if response_index >= len(responses):
            raise AssertionError("Creator made an unexpected extra model call.")
        return responses[response_index]

    if provider_error_code is not None:

        async def reject_creator_tool(_call) -> None:
            raise RuntimeToolError(
                "skill_authoring_propose_create",
                f"fatal Creator tool error: {provider_error_code}",
                code=provider_error_code,
            )

        monkeypatch.setattr(creator_provider, "call_tool", reject_creator_tool)

    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(
        main_module,
        "collect_chat_completion_text",
        fake_collect_chat_completion_text,
    )
    monkeypatch.setattr(main_module, "workflow_mcp_provider", empty_mcp)
    monkeypatch.setattr(
        main_module,
        "workflow_skill_creator_provider",
        creator_provider,
    )
    monkeypatch.setattr(
        main_module.runtime_capabilities.require("mcp_tools"),
        "implementation",
        empty_mcp,
    )
    monkeypatch.setattr(
        main_module.runtime_capabilities.require("skill_creator_tools"),
        "implementation",
        creator_provider,
    )
    monkeypatch.setattr(main_module, "run_registry", RunRegistry())
    monkeypatch.setattr(
        main_module,
        "workflow_execution_store",
        WorkflowExecutionStore(tmp_path / "scenario-executions"),
    )
    monkeypatch.setattr(main_module, "workflow_task_store", {})

    request = CreatorGenerationRequest(
        session={
            "session_id": "skillcreator_runtime_scenario",
            "session_revision": 1,
            "intent": "Create a reusable notes review.",
            "positive_examples": ["Review these notes."],
            "near_miss_examples": ["Write a novel."],
            "expected_output": "A concise report.",
            "success_criteria": ["Find unclear actions."],
            "selected_evidence": [],
        },
        target_draft=None,
        allowed_tool="skill_authoring_propose_create",
    )
    invocation = build_creator_workflow_invocation(
        request,
        model_id="gateway/default-text",
    )
    return authoring, invocation, model_messages


@pytest.mark.asyncio
async def test_creator_generation_reaches_runtime_with_only_the_requested_tool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, authoring, _ = _services(tmp_path)
    creator_provider = AuthoringToolsetProvider(authoring, "skill")
    all_creator_tools = {tool.name for tool in await creator_provider.list_tools()}
    assert all_creator_tools == {
        "skill_authoring_catalog",
        "skill_authoring_get_draft",
        "skill_authoring_propose_create",
        "skill_authoring_propose_update",
        "skill_authoring_validate_proposal",
    }
    empty_mcp = _EmptyMcpProvider()
    model_messages: list[str] = []
    model_token_budgets: list[int] = []
    budget_metadata: list[dict[str, Any]] = []
    model_calls = 0
    original_token_budget = main_module.workflow_agent_token_budget

    def observe_token_budget(metadata: dict[str, Any] | None) -> int:
        budget_metadata.append(dict(metadata or {}))
        return original_token_budget(metadata)

    monkeypatch.setattr(
        main_module,
        "workflow_agent_token_budget",
        observe_token_budget,
    )

    async def fake_collect_chat_completion_text(
        _model_id: str,
        messages,
        **kwargs: Any,
    ) -> str:
        nonlocal model_calls
        model_calls += 1
        model_messages.append(
            "\n".join(str(message.content or "") for message in messages)
        )
        model_token_budgets.append(int(kwargs.get("max_tokens") or 0))
        if model_calls == 1:
            return json.dumps(
                {
                    "tool": "skill_authoring_propose_create",
                    "arguments": _thin_creator_tool_arguments(),
                },
                ensure_ascii=False,
            )
        if model_calls == 2:
            return json.dumps(
                {
                    "tool": "skill_authoring_propose_create",
                    "arguments": _creator_tool_arguments(),
                },
                ensure_ascii=False,
            )
        return '{"answer":"proposal submitted"}'

    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(
        main_module,
        "collect_chat_completion_text",
        fake_collect_chat_completion_text,
    )
    monkeypatch.setattr(main_module, "workflow_mcp_provider", empty_mcp)
    monkeypatch.setattr(
        main_module,
        "workflow_skill_creator_provider",
        creator_provider,
    )
    monkeypatch.setattr(
        main_module.runtime_capabilities.require("mcp_tools"),
        "implementation",
        empty_mcp,
    )
    monkeypatch.setattr(
        main_module.runtime_capabilities.require("skill_creator_tools"),
        "implementation",
        creator_provider,
    )
    monkeypatch.setattr(main_module, "run_registry", RunRegistry())
    monkeypatch.setattr(
        main_module,
        "workflow_execution_store",
        WorkflowExecutionStore(tmp_path / "executions"),
    )
    monkeypatch.setattr(main_module, "workflow_task_store", {})

    request = CreatorGenerationRequest(
        session={
            "session_id": "skillcreator_runtime_regression",
            "session_revision": 1,
            "intent": "Create a reusable notes review.",
            "positive_examples": ["Review these notes."],
            "near_miss_examples": ["Write a novel."],
            "expected_output": "A concise report.",
            "success_criteria": ["Find unclear actions."],
            "selected_evidence": [],
        },
        target_draft=None,
        allowed_tool="skill_authoring_propose_create",
    )
    invocation = build_creator_workflow_invocation(
        request,
        model_id="gateway/default-text",
    )

    await main_module.run_skill_creator_generation(invocation)

    proposals = authoring.proposal_store.list(
        creator_session_id="skillcreator_runtime_regression"
    )
    assert len(proposals) == 1
    assert proposals[0].kind == "skill_create"
    assert proposals[0].source_type == "skill_creator"
    assert proposals[0].actor_id == CREATOR_ASSISTANT_AGENT_ID
    assert empty_mcp.list_calls >= 1
    assert model_calls == 2
    assert len(budget_metadata) == 1
    assert all(
        budget_metadata[0].get(key) == value
        for key, value in invocation.runtime_metadata.items()
    )
    assert main_module.SKILL_CREATOR_AGENT_MAX_TOKENS == 12_288
    assert model_token_budgets == [main_module.SKILL_CREATOR_AGENT_MAX_TOKENS] * 2
    assert main_module.workflow_agent_token_budget({}) == main_module.WORKFLOW_AGENT_MAX_TOKENS
    assert (
        main_module.workflow_agent_token_budget(
            {
                **invocation.runtime_metadata,
                "creator_workflow_version": "untrusted-version",
            }
        )
        == main_module.WORKFLOW_AGENT_MAX_TOKENS
    )
    first_prompt = model_messages[0]
    assert "creator_scope_missing" in model_messages[1]
    assert '"retryable": true' in model_messages[1]
    assert first_prompt.count("skill_authoring_propose_create") >= 1
    for forbidden in (
        "skill_authoring_catalog",
        "skill_authoring_get_draft",
        "skill_authoring_propose_update",
        "skill_authoring_validate_proposal",
    ):
        assert forbidden not in first_prompt


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("first_response", "expected_correction"),
    [
        ("{", "not a valid JSON proposal tool decision"),
        ("[]", "not a JSON object tool decision"),
        ('{"answer":"text-only draft"}', "text answer cannot complete"),
    ],
    ids=["malformed-json", "non-object-json", "answer-object"],
)
async def test_trusted_creator_recovers_non_tool_model_responses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    first_response: str,
    expected_correction: str,
) -> None:
    authoring, invocation, model_messages = _prepare_creator_create_runtime(
        tmp_path,
        monkeypatch,
        [
            first_response,
            _creator_create_decision(_creator_tool_arguments()),
        ],
    )

    await main_module.run_skill_creator_generation(invocation)

    proposals = authoring.proposal_store.list(
        creator_session_id="skillcreator_runtime_scenario"
    )
    assert len(proposals) == 1
    assert len(model_messages) == 2
    assert expected_correction in model_messages[1]


@pytest.mark.asyncio
async def test_trusted_creator_stops_after_two_rejected_tool_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authoring, invocation, model_messages = _prepare_creator_create_runtime(
        tmp_path,
        monkeypatch,
        [
            _creator_create_decision(_thin_creator_tool_arguments()),
            _creator_create_decision(_thin_creator_tool_arguments()),
            _creator_create_decision(_creator_tool_arguments()),
        ],
    )

    with pytest.raises(RuntimeError, match="tool call budget exhausted"):
        await main_module.run_skill_creator_generation(invocation)

    assert len(model_messages) == 3
    assert authoring.proposal_store.list(
        creator_session_id="skillcreator_runtime_scenario"
    ) == []


@pytest.mark.asyncio
async def test_untrusted_creator_metadata_does_not_wrap_quality_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authoring, invocation, model_messages = _prepare_creator_create_runtime(
        tmp_path,
        monkeypatch,
        [_creator_create_decision(_thin_creator_tool_arguments())],
    )
    invocation.runtime_metadata["creator_workflow_version"] = "untrusted-version"
    assert not main_module.is_trusted_skill_creator_runtime(
        invocation.runtime_metadata
    )

    with pytest.raises(RuntimeError, match="creator_scope_missing"):
        await main_module.run_skill_creator_generation(invocation)

    assert len(model_messages) == 1
    assert authoring.proposal_store.list(
        creator_session_id="skillcreator_runtime_scenario"
    ) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_code",
    [
        "tool_denied",
        "authoring_scope_denied",
        "skill_creator_target_invalid",
    ],
)
async def test_creator_fatal_tool_errors_are_not_wrapped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_code: str,
) -> None:
    authoring, invocation, model_messages = _prepare_creator_create_runtime(
        tmp_path,
        monkeypatch,
        [_creator_create_decision(_creator_tool_arguments())],
        provider_error_code=error_code,
    )
    assert main_module.is_trusted_skill_creator_runtime(
        invocation.runtime_metadata
    )

    with pytest.raises(RuntimeError, match=error_code):
        await main_module.run_skill_creator_generation(invocation)

    assert len(model_messages) == 1
    assert authoring.proposal_store.list(
        creator_session_id="skillcreator_runtime_scenario"
    ) == []


@pytest.mark.asyncio
async def test_creator_update_runtime_overrides_model_target_with_frozen_draft(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, authoring, drafts = _services(tmp_path)
    target = drafts.create(
        name=SKILL_PACKAGE["name"],
        slug=SKILL_PACKAGE["slug"],
        description=SKILL_PACKAGE["description"],
        skill_markdown=SKILL_PACKAGE["skill_markdown"],
        files=SKILL_PACKAGE["files"],
        creator_session_id="skillcreator_update_runtime",
        quality_required=True,
    )
    creator_provider = AuthoringToolsetProvider(authoring, "skill")
    empty_mcp = _EmptyMcpProvider()
    model_prompts: list[str] = []

    async def fake_collect_chat_completion_text(
        _model_id: str,
        messages,
        **_kwargs: Any,
    ) -> str:
        model_prompts.append(
            "\n".join(str(message.content or "") for message in messages)
        )
        if len(model_prompts) == 1:
            return "I drafted the package as plain text instead of calling the tool."
        if len(model_prompts) == 2:
            return json.dumps(
                {
                    "tool": "skill_authoring_propose_update",
                    "arguments": {
                        "title": "Update review notes",
                        "draft_id": "skilldraft_model_controlled_target",
                        "base_revision": 999,
                        "skill": {
                            "files": {
                                "references/checklist.md": (
                                    "# Checklist\n\nCheck clarity and ownership.\n"
                                )
                            }
                        },
                        "design": CREATOR_DESIGN,
                        "creator_contract_version": CREATOR_CONTRACT_VERSION,
                    },
                },
                ensure_ascii=False,
            )
        return '{"answer":"update proposal submitted"}'

    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(
        main_module,
        "collect_chat_completion_text",
        fake_collect_chat_completion_text,
    )
    monkeypatch.setattr(main_module, "workflow_mcp_provider", empty_mcp)
    monkeypatch.setattr(
        main_module,
        "workflow_skill_creator_provider",
        creator_provider,
    )
    monkeypatch.setattr(
        main_module.runtime_capabilities.require("mcp_tools"),
        "implementation",
        empty_mcp,
    )
    monkeypatch.setattr(
        main_module.runtime_capabilities.require("skill_creator_tools"),
        "implementation",
        creator_provider,
    )
    monkeypatch.setattr(main_module, "run_registry", RunRegistry())
    monkeypatch.setattr(
        main_module,
        "workflow_execution_store",
        WorkflowExecutionStore(tmp_path / "update-executions"),
    )
    monkeypatch.setattr(main_module, "workflow_task_store", {})

    request = CreatorGenerationRequest(
        session={
            "session_id": "skillcreator_update_runtime",
            "session_revision": 4,
            "intent": "Improve the existing review Skill.",
            "positive_examples": ["Review these notes and assign ownership."],
            "near_miss_examples": ["Rewrite the notes as fiction."],
            "expected_output": "A concise review with action owners.",
            "success_criteria": ["Every action has an owner."],
            "selected_evidence": [],
        },
        target_draft=WorkspaceSkillDraftStore.serialize(
            target,
            include_content=True,
        ),
        allowed_tool="skill_authoring_propose_update",
    )
    invocation = build_creator_workflow_invocation(
        request,
        model_id="gateway/default-text",
    )

    await main_module.run_skill_creator_generation(invocation)

    proposals = authoring.proposal_store.list(
        creator_session_id="skillcreator_update_runtime"
    )
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.kind == "skill_update"
    assert proposal.target_id == target.draft_id
    assert proposal.base_revision == target.revision
    assert proposal.base_digest == target.content_digest
    assert proposal.target_id != "skilldraft_model_controlled_target"
    assert len(model_prompts) == 2
    assert "not a valid JSON proposal tool decision" in model_prompts[1]
    assert model_prompts[0].count("skill_authoring_propose_update") >= 1
    for forbidden in (
        "skill_authoring_catalog",
        "skill_authoring_get_draft",
        "skill_authoring_propose_create",
        "skill_authoring_validate_proposal",
    ):
        assert forbidden not in model_prompts[0]


@pytest.mark.asyncio
async def test_creator_executor_conflicts_wrong_pending_target_before_retry(
    tmp_path: Path,
) -> None:
    store = AuthoringProposalStore(tmp_path / "proposals")
    session_id = "skillcreator_retry_update"
    target_digest = "a" * 64
    wrong = store.create(
        kind="skill_update",
        title="Wrong update target",
        payload={"skill": {"files": {"references/wrong.md": "wrong"}}},
        source_type="skill_creator",
        source_id=session_id,
        source_run_id="wrong-run",
        source_task_id="wrong-task",
        target_id="skilldraft_wrong",
        base_revision=999,
        base_digest="b" * 64,
        creator_session_id=session_id,
        creator_session_revision=3,
        actor_kind="workflow_agent",
        actor_id=CREATOR_ASSISTANT_AGENT_ID,
    )
    correct_proposal_id = ""

    async def runner(_invocation) -> None:
        nonlocal correct_proposal_id
        correct = store.create(
            kind="skill_update",
            title="Correct update target",
            payload={
                "skill": {
                    "files": {"references/checklist.md": "# Updated checklist\n"}
                }
            },
            source_type="skill_creator",
            source_id=session_id,
            source_run_id="correct-run",
            source_task_id="correct-task",
            target_id="skilldraft_correct",
            base_revision=7,
            base_digest=target_digest,
            creator_session_id=session_id,
            creator_session_revision=3,
            actor_kind="workflow_agent",
            actor_id=CREATOR_ASSISTANT_AGENT_ID,
        )
        correct_proposal_id = correct.proposal_id

    executor = WorkflowCreatorGenerationExecutor(
        store,
        model_id="gateway/default-text",
        model_available=lambda: True,
        runner=runner,
    )
    request = CreatorGenerationRequest(
        session={
            "session_id": session_id,
            "session_revision": 3,
            "intent": "Update the existing Skill.",
            "positive_examples": [],
            "near_miss_examples": [],
            "expected_output": "Updated output.",
            "success_criteria": ["The update is correct."],
            "selected_evidence": [],
        },
        target_draft={
            "draft_id": "skilldraft_correct",
            "revision": 7,
            "content_revision": 2,
            "content_digest": target_digest,
            "name": "review-notes",
            "slug": "review-notes",
            "description": SKILL_PACKAGE["description"],
            "skill_markdown": SKILL_PACKAGE["skill_markdown"],
            "files": SKILL_PACKAGE["files"],
        },
        allowed_tool="skill_authoring_propose_update",
    )

    result = await executor.generate(request)

    assert result.proposal_id == correct_proposal_id
    assert store.require(wrong.proposal_id).status == "conflict"
    pending = store.list(
        creator_session_id=session_id,
        status="pending",
    )
    assert [item.proposal_id for item in pending] == [correct_proposal_id]


@pytest.mark.asyncio
async def test_generate_returns_validated_proposal_that_can_be_approved(
    tmp_path: Path,
) -> None:
    creator, authoring, drafts = _services(tmp_path)
    creator.generation_executor = _ProposalExecutor(
        AuthoringToolsetProvider(authoring, "skill")
    )
    session = _ready_blank_session(creator)

    proposal = await creator.generate(
        session.session_id,
        expected_session_revision=session.session_revision,
    )

    assert proposal.validation["valid"] is True
    assert proposal.validation["issues"] == []
    approved = authoring.approve(
        proposal.proposal_id,
        revision=proposal.revision,
        apply_key=proposal.apply_key,
        reason="The generated package matches the confirmed intent.",
    )
    assert approved.status == "approved"
    assert drafts.require(approved.applied_resource_id or "").source_proposal_id == (
        proposal.proposal_id
    )


@pytest.mark.asyncio
async def test_generated_update_is_validated_with_frozen_digests(
    tmp_path: Path,
) -> None:
    creator, authoring, _ = _services(tmp_path)
    creator.generation_executor = _ProposalExecutor(
        AuthoringToolsetProvider(authoring, "skill")
    )
    session = _ready_blank_session(creator)
    session, draft = creator.create_blank_draft(
        session.session_id,
        expected_session_revision=session.session_revision,
        skill_id="review-notes",
        description=SKILL_PACKAGE["description"],
    )

    proposal = await creator.generate(
        session.session_id,
        expected_session_revision=session.session_revision,
    )

    assert proposal.kind == "skill_update"
    assert proposal.validation["valid"] is True
    assert proposal.base_digest == draft.content_digest
    assert proposal.content_digest == proposal.validation["content_digest"]
    assert proposal.content_digest


@pytest.mark.asyncio
async def test_invalid_creator_update_is_rejected_before_proposal_persistence(
    tmp_path: Path,
) -> None:
    creator, authoring, _ = _services(tmp_path)
    session = _ready_blank_session(creator)
    session, draft = creator.create_blank_draft(
        session.session_id,
        expected_session_revision=session.session_revision,
        skill_id="review-notes",
        description=SKILL_PACKAGE["description"],
    )
    provider = AuthoringToolsetProvider(authoring, "skill")

    with pytest.raises(RuntimeToolError) as caught:
        await provider.call_tool(
            RuntimeToolCall(
                "skill_authoring_propose_update",
                {
                    "title": "Invalid update",
                    "draft_id": "model-controlled-draft",
                    "base_revision": 999,
                    "skill": {
                        "skill_markdown": (
                            "---\nname: INVALID NAME\n"
                            "description: invalid update\n---\n"
                        )
                    },
                },
                {
                    "runtime_run_type": "workflow",
                    "creator_session_id": session.session_id,
                    "creator_session_revision": session.session_revision,
                    "run_id": "creator-invalid-update-run",
                    "task_id": "creator-invalid-update-task",
                    "skill_creator_config": {
                        "allow_update": True,
                        "allowed_draft_ids": draft.draft_id,
                    },
                },
            )
        )

    assert caught.value.code == "skill_package_invalid"
    assert authoring.proposal_store.list(
        creator_session_id=session.session_id
    ) == []


@pytest.mark.asyncio
async def test_invalid_creator_create_is_rejected_before_proposal_persistence(
    tmp_path: Path,
) -> None:
    creator, authoring, _ = _services(tmp_path)
    session = _ready_blank_session(creator)
    provider = AuthoringToolsetProvider(authoring, "skill")

    with pytest.raises(RuntimeToolError) as caught:
        await provider.call_tool(
            RuntimeToolCall(
                "skill_authoring_propose_create",
                {
                    "title": "Invalid create",
                    "skill": {
                        "name": "INVALID NAME",
                        "slug": "INVALID NAME",
                        "description": "invalid create",
                        "skill_markdown": (
                            "---\nname: INVALID NAME\n"
                            "description: invalid create\n---\n"
                        ),
                        "files": {},
                    },
                },
                {
                    "runtime_run_type": "workflow",
                    "creator_session_id": session.session_id,
                    "creator_session_revision": session.session_revision,
                    "run_id": "creator-invalid-create-run",
                    "task_id": "creator-invalid-create-task",
                    "skill_creator_config": {"allow_create": True},
                },
            )
        )

    assert caught.value.code == "skill_package_invalid"
    assert authoring.proposal_store.list(
        creator_session_id=session.session_id
    ) == []


def test_approval_and_rejection_share_one_decision_lock(tmp_path: Path) -> None:
    _, authoring, drafts = _services(tmp_path)
    proposal = authoring.proposal_store.create(
        kind="skill_create",
        title="Create review notes",
        payload=_creator_proposal_payload(),
        source_type="skill_creator",
        source_id="skillcreator_decision_lock",
        creator_session_id="skillcreator_decision_lock",
        creator_session_revision=1,
        actor_kind="workflow_agent",
        actor_id=CREATOR_ASSISTANT_AGENT_ID,
    )
    apply_started = threading.Event()
    release_apply = threading.Event()
    reject_finished = threading.Event()
    original_apply = authoring._apply
    approve_results: list[Any] = []
    reject_results: list[Any] = []

    def blocking_apply(item):
        apply_started.set()
        assert release_apply.wait(timeout=3)
        return original_apply(item)

    def approve() -> None:
        try:
            approve_results.append(
                authoring.approve(
                    proposal.proposal_id,
                    revision=proposal.revision,
                    apply_key=proposal.apply_key,
                    reason="Approve this package.",
                )
            )
        except Exception as exc:  # pragma: no cover - assertion below reports it
            approve_results.append(exc)

    def reject() -> None:
        try:
            reject_results.append(
                authoring.reject(
                    proposal.proposal_id,
                    revision=proposal.revision,
                    reason="Discard this package.",
                )
            )
        except Exception as exc:
            reject_results.append(exc)
        finally:
            reject_finished.set()

    authoring._apply = blocking_apply  # type: ignore[method-assign]
    approve_thread = threading.Thread(target=approve)
    reject_thread = threading.Thread(target=reject)
    approve_thread.start()
    assert apply_started.wait(timeout=3)
    reject_thread.start()
    assert not reject_finished.wait(timeout=0.1)
    release_apply.set()
    approve_thread.join(timeout=3)
    reject_thread.join(timeout=3)

    assert approve_results and approve_results[0].status == "approved"
    assert len(reject_results) == 1
    assert isinstance(reject_results[0], AuthoringProposalConflictError)
    assert authoring.proposal_store.require(proposal.proposal_id).status == "approved"
    assert len(drafts.list()) == 1


def test_creator_get_session_recovers_durable_approval_receipt(
    tmp_path: Path,
) -> None:
    creator, authoring, _ = _services(tmp_path)
    session = _ready_blank_session(creator)
    proposal = authoring.proposal_store.create(
        kind="skill_create",
        title="Create review notes",
        payload=_creator_proposal_payload(),
        source_type="skill_creator",
        source_id=session.session_id,
        creator_session_id=session.session_id,
        creator_session_revision=session.session_revision,
        actor_kind="workflow_agent",
        actor_id=CREATOR_ASSISTANT_AGENT_ID,
    )
    creator.session_store.bind_proposal(
        session.session_id,
        expected_session_revision=session.session_revision,
        proposal_id=proposal.proposal_id,
    )
    original_transition = authoring.proposal_store.transition
    failed = False

    def fail_after_apply(*args, **kwargs):
        nonlocal failed
        if kwargs.get("status") == "approved" and not failed:
            failed = True
            raise OSError("simulated proposal transition failure")
        return original_transition(*args, **kwargs)

    authoring.proposal_store.transition = fail_after_apply  # type: ignore[method-assign]
    with pytest.raises(OSError, match="transition failure"):
        authoring.approve(
            proposal.proposal_id,
            revision=proposal.revision,
            apply_key=proposal.apply_key,
            reason="The user approved this package.",
        )

    runtime_dir = tmp_path / "runtime"
    restarted_drafts = WorkspaceSkillDraftStore(runtime_dir)
    restarted_authoring = AuthoringService(
        AuthoringProposalStore(runtime_dir),
        XpertStore(tmp_path / "xperts-restarted"),
        restarted_drafts,
        local_console_actor_id="console_recovery_test",
    )
    restarted_creator = SkillCreatorService(
        SkillCreatorSessionStore(runtime_dir),
        restarted_drafts,
        restarted_authoring,
        enabled=True,
    )

    recovered_session, recovered_draft = restarted_creator.get_session(
        session.session_id
    )
    recovered_proposal = restarted_authoring.proposal_store.require(
        proposal.proposal_id
    )
    assert recovered_proposal.status == "approved"
    assert recovered_proposal.decision_reason.startswith("Recovered")
    assert recovered_draft is not None
    assert recovered_session.draft_id == recovered_draft.draft_id
    assert recovered_proposal.applied_resource_id == recovered_draft.draft_id
    with pytest.raises(AuthoringProposalConflictError):
        restarted_authoring.reject(
            recovered_proposal.proposal_id,
            revision=recovered_proposal.revision,
            reason="Too late to discard an applied proposal.",
        )


@pytest.mark.asyncio
async def test_same_revision_concurrent_generate_serializes_to_one_pending_proposal(
    tmp_path: Path,
) -> None:
    creator, authoring, _ = _services(tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()
    executor = _ProposalExecutor(
        AuthoringToolsetProvider(authoring, "skill"),
        started=started,
        release=release,
    )
    creator.generation_executor = executor
    session = _ready_blank_session(creator)

    first = asyncio.create_task(
        creator.generate(
            session.session_id,
            expected_session_revision=session.session_revision,
        )
    )
    await asyncio.wait_for(started.wait(), timeout=2)
    second = asyncio.create_task(
        creator.generate(
            session.session_id,
            expected_session_revision=session.session_revision,
        )
    )
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(first, second, return_exceptions=True)

    proposals = authoring.proposal_store.list(
        creator_session_id=session.session_id,
        status="pending",
    )
    successes = [item for item in results if not isinstance(item, Exception)]
    conflicts = [
        item for item in results if isinstance(item, SkillCreatorConflictError)
    ]
    assert executor.calls == 1
    assert len(successes) == 1
    assert len(conflicts) == 1
    assert len(proposals) == 1
    assert successes[0].proposal_id == proposals[0].proposal_id
    restored_session, _ = creator.get_session(session.session_id)
    assert restored_session.proposal_id == proposals[0].proposal_id
    assert authoring.proposal_store.require(proposals[0].proposal_id).status == "pending"


@pytest.mark.asyncio
async def test_definition_change_during_generation_leaves_no_orphan_pending_proposal(
    tmp_path: Path,
) -> None:
    creator, authoring, _ = _services(tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()
    creator.generation_executor = _ProposalExecutor(
        AuthoringToolsetProvider(authoring, "skill"),
        started=started,
        release=release,
    )
    session = _ready_blank_session(creator)
    generation = asyncio.create_task(
        creator.generate(
            session.session_id,
            expected_session_revision=session.session_revision,
        )
    )
    await asyncio.wait_for(started.wait(), timeout=2)

    updated = creator.update_definition(
        session.session_id,
        expected_session_revision=session.session_revision,
        changes={"intent": "Use the newly clarified review process."},
    )
    release.set()

    with pytest.raises(SkillCreatorConflictError):
        await generation
    assert updated.session_revision > session.session_revision
    assert authoring.proposal_store.list(
        creator_session_id=session.session_id,
        status="pending",
    ) == []
    conflicts = authoring.proposal_store.list(
        creator_session_id=session.session_id,
        status="conflict",
    )
    assert len(conflicts) == 1
    assert conflicts[0].creator_session_revision == session.session_revision


@pytest.mark.asyncio
async def test_unknown_generation_session_does_not_allocate_a_lock(
    tmp_path: Path,
) -> None:
    creator, _, _ = _services(tmp_path)

    for index in range(3):
        with pytest.raises(SkillCreatorNotFoundError):
            await creator.generate(
                f"missing-session-{index}",
                expected_session_revision=1,
            )

    assert creator._generation_locks == {}


@pytest.mark.asyncio
async def test_authoring_api_persists_reason_structures_conflicts_and_guards_creator_patch(
    tmp_path: Path,
) -> None:
    _, authoring, _ = _services(tmp_path)
    creator_proposal = authoring.proposal_store.create(
        kind="skill_create",
        title="Create review notes",
        payload=_creator_proposal_payload(),
        source_type="skill_creator",
        source_id="skillcreator_api_regression",
        creator_session_id="skillcreator_api_regression",
        creator_session_revision=1,
        actor_kind="workflow_agent",
        actor_id=CREATOR_ASSISTANT_AGENT_ID,
    )
    creator_proposal = authoring.validate(
        creator_proposal.proposal_id,
        revision=creator_proposal.revision,
    )
    ordinary = authoring.proposal_store.create(
        kind="xpert_create",
        title="Ordinary proposal",
        payload={"name": "Ordinary Draft", "slug": "ordinary-draft"},
        source_type="workflow",
        source_id="workflow-task-regression",
    )
    previous_service = authoring_api._service
    app = FastAPI()
    app.include_router(authoring_api.router)
    try:
        authoring_api.configure_runtime_authoring(authoring)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            creator_patch = await client.patch(
                f"/api/runtime/authoring-proposals/{creator_proposal.proposal_id}",
                json={
                    "revision": creator_proposal.revision,
                    "title": "Unsafe raw Creator edit",
                },
            )
            assert creator_patch.status_code == 400
            assert creator_patch.json()["detail"]["code"] == (
                "skill_creator_proposal_managed"
            )

            ordinary_patch = await client.patch(
                f"/api/runtime/authoring-proposals/{ordinary.proposal_id}",
                json={
                    "revision": ordinary.revision,
                    "title": "Reviewed ordinary proposal",
                },
            )
            assert ordinary_patch.status_code == 200, ordinary_patch.text
            assert ordinary_patch.json()["title"] == "Reviewed ordinary proposal"

            stale = await client.post(
                f"/api/runtime/authoring-proposals/{creator_proposal.proposal_id}/approve",
                json={
                    "revision": creator_proposal.revision + 1,
                    "apply_key": creator_proposal.apply_key,
                    "reason": "stale decision",
                },
            )
            assert stale.status_code == 409
            assert stale.json()["detail"] == {
                "code": "authoring_conflict",
                "message": "Proposal changed. Reload it before approval.",
                "issues": [],
            }

            reason = "The user reviewed the generated package and approved it."
            approved = await client.post(
                f"/api/runtime/authoring-proposals/{creator_proposal.proposal_id}/approve",
                json={
                    "revision": creator_proposal.revision,
                    "apply_key": creator_proposal.apply_key,
                    "reason": reason,
                },
            )
            assert approved.status_code == 200, approved.text
            assert approved.json()["decision_reason"] == reason
            assert (
                authoring.proposal_store.require(creator_proposal.proposal_id).decision_reason
                == reason
            )
    finally:
        authoring_api._service = previous_service
