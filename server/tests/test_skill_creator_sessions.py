from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from server.skills import creator_api
from server.skills.creator_quality import CREATOR_CONTRACT_VERSION
from server.skills.creator_service import (
    CreatorGenerationRequest,
    CreatorGenerationResult,
    SkillCreatorService,
)
from server.skills.creator_store import (
    CREATOR_ASSISTANT_AGENT_ID,
    SkillCreatorConflictError,
    SkillCreatorSessionStore,
    SkillCreatorStorageError,
    SkillCreatorValidationError,
)
from server.skills.draft_store import (
    SkillDraftValidationError,
    WorkspaceSkillDraftStore,
)
from server.xpert_runtime import authoring_api
from server.xpert_runtime.authoring_service import AuthoringService
from server.xpert_runtime.authoring_store import AuthoringProposalStore
from server.xpert_runtime.authoring_toolset import AuthoringToolsetProvider
from server.xpert_runtime.toolset import RuntimeToolCall
from server.xperts import XpertStore


SKILL_PACKAGE = {
    "name": "review-notes",
    "slug": "review-notes",
    "description": (
        "Review meeting notes into a traceable action report with owners and open "
        "questions. Use when users provide completed notes; do not use for fiction or "
        "unrelated summaries."
    ),
    "skill_markdown": """---
name: review-notes
description: Review meeting notes into a traceable action report with owners and open questions. Use when users provide completed notes; do not use for fiction or unrelated summaries.
---

# Review notes

## Purpose and boundaries

Turn completed meeting notes into a factual report. Preserve source wording, separate
decisions from suggestions, and never invent a missing owner, deadline, or agreement.

## Inputs and prerequisites

Require the notes, meeting objective, and any known owners. Ask for clarification when the
source is incomplete or too ambiguous to identify an action safely.

## Workflow

1. Normalize headings and speaker labels without discarding original evidence.
2. Extract decisions, open questions, and actions while marking uncertainty.
3. Link each action to its source note and identify missing required fields.
4. Draft the required report with explicit `unknown` markers.
5. Verify evidence links, ownership fields, and unresolved questions before delivery.

## Output contract

Return decisions, open questions, and actions. Each action contains Evidence, Owner, Due,
and Status. Use `unknown` for unavailable values and never omit a required field silently.

## Quality checks

Trace every claim to the notes, keep unresolved ambiguity visible, and confirm each action
has either one named owner or an explicit `unknown` marker.

## Failure and degradation

If notes are missing, return the required inputs instead of a report. Preserve conflicting
accounts and request clarification. Mark partially checked output as partial rather than
claiming that all checks passed.

## Resources

This package needs no bundled resources; use only the notes supplied with the request.
""",
    "files": {},
}

CREATOR_REQUIREMENT_IDS = [
    "intent",
    "positive_example:0",
    "near_miss:0",
    "expected_output",
    "success_criterion:0",
    "success_criterion:1",
]

CREATOR_DESIGN = {
    "workflow_steps": [
        {"id": "normalize", "description": "Normalize the source notes safely."},
        {"id": "extract", "description": "Extract decisions and actions."},
        {"id": "draft", "description": "Draft the required report fields."},
        {"id": "verify", "description": "Verify evidence and ownership."},
    ],
    "output_contract": [
        {"id": "report", "description": "Return traceable decisions and actions."}
    ],
    "failure_modes": [
        {"id": "missing", "description": "Request missing inputs instead of guessing."}
    ],
    "resources": [],
    "assumptions": ["The supplied notes are the authoritative source."],
    "requirement_coverage": [
        {
            "requirement_id": item,
            "locations": [
                {
                    "path": "SKILL.md",
                    "section": (
                        "Purpose and boundaries"
                        if item in {"intent", "near_miss:0"}
                        else "Workflow"
                        if item == "positive_example:0"
                        else "Output contract"
                        if item == "expected_output"
                        else "Quality checks"
                    ),
                }
            ],
        }
        for item in CREATOR_REQUIREMENT_IDS
    ],
}


def _creator_payload() -> dict:
    return {
        "skill": SKILL_PACKAGE,
        "design": CREATOR_DESIGN,
        "creator_contract_version": CREATOR_CONTRACT_VERSION,
        "creator_requirement_ids": CREATOR_REQUIREMENT_IDS,
    }


def _services(tmp_path: Path, *, executor=None):
    runtime_dir = tmp_path / "runtime"
    draft_store = WorkspaceSkillDraftStore(runtime_dir)
    authoring = AuthoringService(
        AuthoringProposalStore(runtime_dir),
        XpertStore(tmp_path / "xperts"),
        draft_store,
        local_console_actor_id="console_test_instance",
    )
    creator = SkillCreatorService(
        SkillCreatorSessionStore(runtime_dir),
        draft_store,
        authoring,
        enabled=True,
        generation_executor=executor,
    )
    return creator, authoring, draft_store


def _defined_session(service: SkillCreatorService):
    return service.create_session(
        mode="blank",
        intent="Turn a repeatable review process into a Skill.",
        positive_examples=["Review these meeting notes."],
        near_miss_examples=["Summarize an unrelated novel."],
        expected_output="A concise quality report.",
        success_criteria=["Identify unclear actions", "Keep the report concise"],
    )


def _confirm_blank_evidence(service: SkillCreatorService, session):
    preview = service.preview_source(session.session_id)
    return service.select_evidence(
        session.session_id,
        expected_session_revision=session.session_revision,
        preview_fingerprint=preview.fingerprint,
        candidate_ids=[],
    )


def test_session_store_is_atomic_fail_closed_and_secret_safe(tmp_path: Path) -> None:
    store = SkillCreatorSessionStore(tmp_path / "sessions")
    session = store.create(intent="Draft a reusable review workflow.")
    restored = SkillCreatorSessionStore(tmp_path / "sessions").require(
        session.session_id
    )
    assert restored.assistant_agent_id == CREATOR_ASSISTANT_AGENT_ID

    snapshot = store.snapshot_path
    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    secret_name = "OPENROUTER_" + "API_KEY"
    secret_value = "sk-" + ("a" * 48)
    payload["items"].append(
        {
            "session_id": "skillcreator_secret",
            "intent": f'{secret_name}="{secret_value}"',
        }
    )
    snapshot.write_text(json.dumps(payload), encoding="utf-8")
    sanitized = SkillCreatorSessionStore(tmp_path / "sessions")
    assert sanitized.require(session.session_id).session_id == session.session_id
    text = snapshot.read_text(encoding="utf-8")
    assert secret_name not in text
    assert secret_value not in text
    assert "record_sha256" in text

    corrupt_dir = tmp_path / "corrupt"
    corrupt_dir.mkdir()
    (corrupt_dir / "skill_creator_sessions.json").write_text(
        "{not-json", encoding="utf-8"
    )
    corrupt = SkillCreatorSessionStore(corrupt_dir)
    with pytest.raises(SkillCreatorStorageError):
        corrupt.list()
    with pytest.raises(SkillCreatorStorageError):
        corrupt.create(intent="must not overwrite")
    assert (corrupt_dir / "skill_creator_sessions.json").read_text() == "{not-json"


def test_legacy_session_resource_authoring_opt_in_is_explicit_and_durable(
    tmp_path: Path,
) -> None:
    store = SkillCreatorSessionStore(tmp_path / "resource-flow")
    legacy = store.create(mode="blank", intent="Review incidents")
    assert legacy.authoring_flow == "legacy"

    migrated = store.activate_resource_authoring(
        legacy.session_id,
        expected_session_revision=legacy.session_revision,
    )

    assert migrated.authoring_flow == "resource"
    assert migrated.session_revision == legacy.session_revision + 1
    assert (
        SkillCreatorSessionStore(tmp_path / "resource-flow")
        .require(legacy.session_id)
        .authoring_flow
        == "resource"
    )


def test_selected_evidence_keeps_sanitized_title_across_reload(
    tmp_path: Path,
) -> None:
    store = SkillCreatorSessionStore(tmp_path / "sessions")
    session = store.create(intent="Draft a reusable review workflow.")
    selected = store.set_evidence(
        session.session_id,
        expected_session_revision=session.session_revision,
        preview_fingerprint="a" * 64,
        selected_evidence=[
            {
                "candidate_id": "goal-summary",
                "kind": "goal",
                "title": "Successful run goal",
                "summary": "Review notes and return a concise report.",
                "content_hash": "b" * 64,
            }
        ],
    )
    restored = SkillCreatorSessionStore(tmp_path / "sessions").require(
        selected.session_id
    )
    assert restored.evidence_confirmed is True
    assert restored.selected_evidence[0]["title"] == "Successful run goal"


def test_local_console_actor_id_is_stable_during_concurrent_initialization(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"

    def create_actor(_: int) -> str:
        service = AuthoringService(
            AuthoringProposalStore(runtime_dir),
            XpertStore(tmp_path / "xperts"),
            WorkspaceSkillDraftStore(runtime_dir),
        )
        return service.local_console_actor_id

    with ThreadPoolExecutor(max_workers=8) as pool:
        actor_ids = set(pool.map(create_actor, range(16)))
    assert len(actor_ids) == 1
    assert next(iter(actor_ids)).startswith("console_")


def test_manual_draft_recovers_after_session_save_failure_and_is_quality_gated(
    tmp_path: Path,
) -> None:
    creator, _, drafts = _services(tmp_path)
    session = _confirm_blank_evidence(creator, _defined_session(creator))
    original_save = creator.session_store._save_unlocked
    failures = 0

    def fail_once() -> None:
        nonlocal failures
        failures += 1
        if failures == 1:
            raise OSError("simulated session disk full")
        original_save()

    creator.session_store._save_unlocked = fail_once  # type: ignore[method-assign]
    with pytest.raises(OSError, match="disk full"):
        creator.create_blank_draft(
            session.session_id,
            expected_session_revision=session.session_revision,
            skill_id="review-notes",
            description=SKILL_PACKAGE["description"],
        )
    assert len(drafts.list()) == 1

    recovered_session, recovered_draft = creator.create_blank_draft(
        session.session_id,
        expected_session_revision=session.session_revision,
        skill_id="review-notes",
        description=SKILL_PACKAGE["description"],
    )
    assert len(drafts.list()) == 1
    assert recovered_session.draft_id == recovered_draft.draft_id
    detail = creator.serialize_draft(recovered_draft)
    assert detail["frontmatter"]["name"] == "review-notes"
    assert detail["validation"]["valid"] is True
    assert detail["quality_required"] is True
    assert detail["quality_status"] == "not_evaluated"
    scaffold = recovered_draft.skill_markdown
    assert "MODEL_MIRROR_MANUAL_SCAFFOLD: incomplete" in scaffold
    assert "## Trigger boundaries" in scaffold
    assert "Review these meeting notes." in scaffold
    assert "Summarize an unrelated novel." in scaffold
    assert "A concise quality report." in scaffold
    assert "Identify unclear actions" in scaffold
    assert "## Failure and degradation" in scaffold

    with pytest.raises(
        SkillDraftValidationError, match="not complete enough"
    ) as incomplete:
        drafts.install_current(
            recovered_draft.draft_id,
            expected_revision=recovered_draft.revision,
            expected_digest=recovered_draft.content_digest,
            installer=lambda item: SimpleNamespace(
                skill_id="workspace-review-notes",
                content_digest=item.content_digest,
            ),
        )
    assert "creator_manual_scaffold_incomplete" in {
        issue["code"] for issue in incomplete.value.issues
    }


def test_creator_draft_cannot_add_or_edit_hook_manifest_directly(
    tmp_path: Path,
) -> None:
    creator, _, _ = _services(tmp_path)
    session = _confirm_blank_evidence(creator, _defined_session(creator))
    session, draft = creator.create_blank_draft(
        session.session_id,
        expected_session_revision=session.session_revision,
        skill_id="review-notes",
        description=SKILL_PACKAGE["description"],
    )

    with pytest.raises(SkillCreatorValidationError) as caught:
        creator.update_draft(
            session.session_id,
            expected_session_revision=session.session_revision,
            expected_revision=draft.revision,
            expected_digest=draft.content_digest,
            changes={
                "files": {
                    **draft.files,
                    "hooks/manifest.json": '{"version":"modelmirror-hook-manifest-v2","hooks":[]}',
                }
            },
        )

    assert caught.value.code == "skill_creator_hook_manifest_read_only"


def test_stale_session_patch_cannot_cancel_current_proposal(tmp_path: Path) -> None:
    creator, authoring, _ = _services(tmp_path)
    session = _confirm_blank_evidence(creator, _defined_session(creator))
    proposal = authoring.proposal_store.create(
        kind="skill_create",
        title="Create review notes",
        payload=_creator_payload(),
        source_type="skill_creator",
        source_id=session.session_id,
        creator_session_id=session.session_id,
        creator_session_revision=session.session_revision,
        actor_kind="workflow_agent",
        actor_id=CREATOR_ASSISTANT_AGENT_ID,
    )
    bound = creator.session_store.bind_proposal(
        session.session_id,
        expected_session_revision=session.session_revision,
        proposal_id=proposal.proposal_id,
    )

    with pytest.raises(SkillCreatorConflictError):
        creator.update_definition(
            session.session_id,
            expected_session_revision=session.session_revision,
            changes={"intent": "stale change"},
        )
    assert bound.session_revision > session.session_revision
    assert authoring.proposal_store.require(proposal.proposal_id).status == "pending"


def test_apply_key_retry_uses_fixed_receipt_after_later_edit(tmp_path: Path) -> None:
    _, authoring, drafts = _services(tmp_path)
    proposal = authoring.proposal_store.create(
        kind="skill_create",
        title="Create review notes",
        payload=_creator_payload(),
        source_type="skill_creator",
        source_id="skillcreator_one",
        creator_session_id="skillcreator_one",
        creator_session_revision=1,
    )
    original_transition = authoring.proposal_store.transition
    failed = False
    approval_reason = "The user reviewed and accepted this package."

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
            reason=approval_reason,
        )

    # Simulate a process restart between the durable draft write and proposal
    # transition. Recovery must use the persisted immutable receipt.
    runtime_dir = tmp_path / "runtime"
    drafts = WorkspaceSkillDraftStore(runtime_dir)
    authoring = AuthoringService(
        AuthoringProposalStore(runtime_dir),
        XpertStore(tmp_path / "xperts-restarted"),
        drafts,
        local_console_actor_id="console_test_instance",
    )

    approved = authoring.approve(
        proposal.proposal_id,
        revision=proposal.revision,
        apply_key=proposal.apply_key,
        reason=approval_reason,
    )
    assert len(drafts.list()) == 1
    assert approved.applied_resource_revision == 1
    assert approved.decision_reason == approval_reason
    fixed_digest = approved.applied_content_digest
    draft = drafts.require(approved.applied_resource_id or "")
    edited_markdown = draft.skill_markdown + "\nAdd a later manual edit.\n"
    edited = drafts.update(
        draft.draft_id,
        expected_revision=draft.revision,
        expected_digest=draft.content_digest,
        skill_markdown=edited_markdown,
    )
    replayed = authoring.approve(
        proposal.proposal_id,
        revision=proposal.revision,
        apply_key=proposal.apply_key,
    )
    assert replayed.applied_resource_revision == 1
    assert replayed.applied_content_digest == fixed_digest
    assert drafts.require(draft.draft_id).content_revision == edited.content_revision


def test_noop_update_proposal_has_replayable_fixed_receipt(tmp_path: Path) -> None:
    _, authoring, drafts = _services(tmp_path)
    draft = drafts.create(
        name=SKILL_PACKAGE["name"],
        slug=SKILL_PACKAGE["slug"],
        description=SKILL_PACKAGE["description"],
        skill_markdown=SKILL_PACKAGE["skill_markdown"],
        files=SKILL_PACKAGE["files"],
    )
    proposal = authoring.proposal_store.create(
        kind="skill_update",
        title="Keep review notes unchanged",
        payload=_creator_payload(),
        source_type="skill_creator",
        source_id="skillcreator_update",
        target_id=draft.draft_id,
        base_revision=draft.revision,
        base_digest=draft.content_digest,
        creator_session_id="skillcreator_update",
        creator_session_revision=1,
    )
    approved = authoring.approve(
        proposal.proposal_id,
        revision=proposal.revision,
        apply_key=proposal.apply_key,
    )
    assert approved.applied_resource_revision == draft.content_revision
    assert drafts.require(draft.draft_id).content_revision == draft.content_revision

    edited = drafts.update(
        draft.draft_id,
        expected_revision=draft.revision,
        expected_digest=draft.content_digest,
        skill_markdown=draft.skill_markdown + "\nA later manual edit.\n",
    )
    replayed = authoring.approve(
        proposal.proposal_id,
        revision=proposal.revision,
        apply_key=proposal.apply_key,
    )
    assert replayed.applied_resource_revision == draft.content_revision
    assert replayed.applied_content_digest == draft.content_digest
    assert drafts.require(draft.draft_id).content_revision == edited.content_revision


class _ToolCallingExecutor:
    def __init__(self, provider: AuthoringToolsetProvider) -> None:
        self.provider = provider

    def available(self) -> bool:
        return True

    async def generate(
        self, request: CreatorGenerationRequest
    ) -> CreatorGenerationResult:
        session = request.session
        result = await self.provider.call_tool(
            RuntimeToolCall(
                request.allowed_tool,
                {
                    "title": "Create review notes",
                    "skill": SKILL_PACKAGE,
                    "design": CREATOR_DESIGN,
                    "creator_contract_version": CREATOR_CONTRACT_VERSION,
                },
                {
                    "runtime_run_type": "xpert",
                    "creator_session_id": session["session_id"],
                    "creator_session_revision": session["session_revision"],
                    "run_id": "creator-run-1",
                    "task_id": "creator-task-1",
                    "creator_requirement_ids": CREATOR_REQUIREMENT_IDS,
                    "skill_creator_config": {"allow_create": True},
                },
            )
        )
        payload = json.loads(result.output)
        return CreatorGenerationResult(
            proposal_id=payload["proposal_id"],
            tool_name=request.allowed_tool,
            runtime_run_id="creator-run-1",
            runtime_task_id="creator-task-1",
        )


class _FailingExecutor:
    def available(self) -> bool:
        return True

    async def generate(
        self, request: CreatorGenerationRequest
    ) -> CreatorGenerationResult:
        del request
        raise RuntimeError("provider response included untrusted text only")


@pytest.mark.asyncio
async def test_generate_accepts_only_bound_tool_proposal_and_revision(
    tmp_path: Path,
) -> None:
    creator, authoring, drafts = _services(tmp_path)
    provider = AuthoringToolsetProvider(authoring, "skill")
    creator.generation_executor = _ToolCallingExecutor(provider)
    session = _confirm_blank_evidence(creator, _defined_session(creator))

    with pytest.raises(SkillCreatorConflictError):
        await creator.generate(
            session.session_id,
            expected_session_revision=session.session_revision - 1,
        )
    assert authoring.proposal_store.list(creator_session_id=session.session_id) == []

    proposal = await creator.generate(
        session.session_id,
        expected_session_revision=session.session_revision,
    )
    assert proposal.creator_session_id == session.session_id
    assert proposal.creator_session_revision == session.session_revision
    assert proposal.source_type == "skill_creator"
    assert proposal.source_run_id == "creator-run-1"
    assert proposal.source_task_id == "creator-task-1"
    assert proposal.actor_kind == "workflow_agent"
    assert drafts.list() == []


@pytest.mark.asyncio
async def test_creator_api_flag_manual_flow_and_trusted_actor(tmp_path: Path) -> None:
    creator, authoring, _ = _services(tmp_path)
    previous_creator = creator_api._service
    previous_authoring = authoring_api._service
    app = FastAPI()
    app.include_router(creator_api.router)
    app.include_router(authoring_api.router)
    try:
        creator_api.configure_skill_creator(creator)
        authoring_api.configure_runtime_authoring(authoring)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            status = await client.get("/api/skills/creator/status")
            assert status.status_code == 200
            assert status.json()["assistant_agent_id"] == CREATOR_ASSISTANT_AGENT_ID
            assert status.json()["hook_manifest_version"] == "modelmirror-hook-manifest-v2"
            assert status.json()["hook_result_version"] == "modelmirror-hook-result-v1"
            assert status.json()["hook_runtimes"] == ["python", "javascript"]

            created = await client.post(
                "/api/skills/creator/sessions",
                json={
                    "mode": "blank",
                    "intent": "Create a reusable notes review.",
                    "positive_examples": ["Review these notes."],
                    "near_miss_examples": ["Write a novel."],
                    "expected_output": "A concise report.",
                    "success_criteria": ["Find unclear actions"],
                },
            )
            assert created.status_code == 201, created.text
            session = created.json()["session"]
            preview = await client.post(
                f"/api/skills/creator/sessions/{session['session_id']}/source-preview"
            )
            assert preview.status_code == 200
            assert set(preview.json()) >= {
                "preview_fingerprint",
                "source_kind",
                "source_task_id",
                "source_run_id",
                "candidates",
            }
            confirmed = await client.put(
                f"/api/skills/creator/sessions/{session['session_id']}/evidence",
                json={
                    "expected_session_revision": session["session_revision"],
                    "preview_fingerprint": preview.json()["preview_fingerprint"],
                    "candidate_ids": [],
                },
            )
            assert confirmed.status_code == 200, confirmed.text
            session = confirmed.json()["session"]
            creator.generation_executor = _FailingExecutor()
            failed_generation = await client.post(
                f"/api/skills/creator/sessions/{session['session_id']}/generate",
                json={"expected_session_revision": session["session_revision"]},
            )
            assert failed_generation.status_code == 502
            assert failed_generation.json()["detail"]["code"] == (
                "skill_creator_generation_failed"
            )
            draft_response = await client.post(
                f"/api/skills/creator/sessions/{session['session_id']}/draft",
                json={
                    "expected_session_revision": session["session_revision"],
                    "skill_id": "review-notes",
                    "description": SKILL_PACKAGE["description"],
                },
            )
            assert draft_response.status_code == 201, draft_response.text
            assert draft_response.json()["draft"]["frontmatter"]["name"] == "review-notes"

            proposal = authoring.proposal_store.create(
                kind="xpert_create",
                title="API proposal",
                payload={"name": "API Draft", "slug": "api-draft"},
                source_type="workflow",
                source_id="task-1",
            )
            spoofed = await client.post(
                f"/api/runtime/authoring-proposals/{proposal.proposal_id}/approve",
                json={
                    "revision": proposal.revision,
                    "apply_key": proposal.apply_key,
                    "operator": "spoofed-user",
                },
            )
            assert spoofed.status_code == 422
            approved = await client.post(
                f"/api/runtime/authoring-proposals/{proposal.proposal_id}/approve",
                json={
                    "revision": proposal.revision,
                    "apply_key": proposal.apply_key,
                },
            )
            assert approved.status_code == 200, approved.text
            assert approved.json()["actor_kind"] == "local_console"
            assert approved.json()["actor_id"] == "console_test_instance"
            assert approved.json()["operator"] is None
    finally:
        creator_api._service = previous_creator
        authoring_api._service = previous_authoring
