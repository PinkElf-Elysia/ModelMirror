from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.skills import api as skills_api
from server.skills import experience_api
from server.skills.application_receipts import SkillApplicationReceiptStore
from server.skills.creator_service import SkillCreatorService
from server.skills.creator_handoff import (
    SkillCreatorHandoffRequest,
    SkillCreatorHandoffService,
)
from server.skills.creator_runtime import TrustedCreatorSourceProvider
from server.skills.creator_store import SkillCreatorSessionStore, SkillCreatorStorageError
from server.skills.draft_store import WorkspaceSkillDraftStore
from server.skills.experience import (
    SkillExperienceCandidateStore,
    SkillExperienceConflictError,
    SkillExperienceError,
    SkillExperienceDecisionV1,
    SkillExperienceService,
    SkillExperienceSource,
    build_distilled_skill_brief,
)
from server.skills.experience_promotion import SkillExperiencePromotionService
from server.skills.lifecycle import SkillLifecycleStore
from server.skills.skill_manager import SkillManager, SkillValidationError
from server.xpert_runtime.authoring_service import AuthoringService
from server.xpert_runtime.authoring_store import AuthoringProposalStore
from server.xpert_runtime.execution_store import WorkflowExecutionStore
from server.xperts import XpertStore
from server.xperts.context import XpertContextStore


SKILL_MD = """---
name: release-review
description: Review controlled release packages and report deterministic checks. Use when validating repeatable releases; do not use for general summaries.
---

# Release review

## Purpose and boundaries

Review a controlled release package without inventing missing facts.

## Inputs and prerequisites

Require the release manifest and package inventory.

## Workflow

1. Read the supplied release manifest.
2. Compare each package entry with the declared rules.
3. Record failures with evidence.
4. Return the bounded report.

## Output contract

Return one status per check, supporting evidence, and unresolved inputs.

## Quality checks

Every conclusion must be traceable to supplied input.

## Failure and degradation

Stop and request the missing manifest instead of guessing.

## Resources

Use the bundled reference only when the workflow calls for release rules.
"""


def _runtime(tmp_path: Path):
    executions = WorkflowExecutionStore(tmp_path / "executions")
    contexts = XpertContextStore(tmp_path / "contexts")
    receipts = SkillApplicationReceiptStore(tmp_path / "receipts")
    candidates = SkillExperienceCandidateStore(tmp_path / "experience")
    experience = SkillExperienceService(candidates, executions, contexts, receipts)
    drafts = WorkspaceSkillDraftStore(tmp_path / "drafts")
    creator_store = SkillCreatorSessionStore(tmp_path / "creator")
    authoring = AuthoringService(
        AuthoringProposalStore(tmp_path / "authoring"),
        XpertStore(tmp_path / "xperts"),
        drafts,
        local_console_actor_id="promotion-test-console",
    )
    creator = SkillCreatorService(
        creator_store,
        drafts,
        authoring,
        enabled=True,
        source_provider=TrustedCreatorSourceProvider(executions, contexts),
    )
    lifecycle = SkillLifecycleStore(tmp_path / "lifecycle", enabled=True)
    manager = SkillManager(
        installed_dir=tmp_path / "installed",
        tmp_dir=tmp_path / "tmp",
        lifecycle_store=lifecycle,
    )
    promotion = SkillExperiencePromotionService(
        experience,
        candidates,
        creator,
        drafts,
        manager,
        lifecycle,
    )
    return experience, candidates, executions, creator, drafts, manager, lifecycle, promotion


def _promotion_ready(
    experience: SkillExperienceService,
    store: SkillExperienceCandidateStore,
    executions: WorkflowExecutionStore,
    *,
    decision: str = "create",
    target_skill_id: str | None = None,
    target_draft_id: str | None = None,
):
    executions.create(
        task_id="task-promotion",
        run_id="run-promotion",
        run_type="workflow",
        source_kind="workflow_classic",
        workflow={"id": "release-flow", "title": "发布检查"},
        inputs={"user_input": "检查受控发布包并形成可复用流程"},
    )
    executions.append_event(
        "task-promotion",
        {"event": "node_completed", "node_id": "review", "tool_name": "rg"},
    )
    executions.complete("task-promotion", result="检查已完成")
    candidate, preview = experience.create_or_get(
        SkillExperienceSource(
            source_kind="workflow_classic",
            source_task_id="task-promotion",
            source_run_id="run-promotion",
        )
    )
    selected = experience.select_evidence(
        candidate.candidate_id,
        expected_revision=candidate.revision,
        expected_digest=candidate.digest,
        preview_fingerprint=preview.preview_fingerprint,
        evidence_ids=[
            item.candidate_id for item in preview.candidates if item.default_selected
        ],
    )
    key = "a" * 64
    analyzing, _ = store.begin_analysis(
        selected.candidate_id,
        expected_revision=selected.revision,
        expected_digest=selected.digest,
        analysis_key=key,
    )
    brief = build_distilled_skill_brief(
        {
            "suggestion": decision,
            "recommendation_reason": "该流程可以在多次受控发布中复用。",
            "no_skill_reason": None,
            "intent": "检查受控发布包并输出可追溯报告",
            "positive_examples": ["检查候选发布包", "核对发布清单"],
            "negative_examples": ["普通文章摘要", "编辑宣传图片"],
            "expected_output": "输出逐项检查结论和证据。",
            "success_criteria": ["每项均有结论", "缺失内容不编造"],
            "reusable_steps": ["读取清单", "逐项核对", "汇总结果"],
            "failure_boundaries": ["缺少清单时停止"],
            "resource_clues": ["发布规则可保存为 reference"],
            "overfitting_risk": "不得写死某次发布路径。",
        },
        revision=1,
        source="manual",
    )
    reviewed = store.complete_analysis(
        analyzing.candidate_id,
        attempt_id=analyzing.analysis_attempt.attempt_id,  # type: ignore[union-attr]
        analysis_key=key,
        brief=brief,
        overlaps=(),
        overlap_fingerprint="b" * 64,
        executor_mode="manual",
        error_code="skill_experience_analysis_unconfigured",
    )
    return store.decide(
        reviewed.candidate_id,
        expected_revision=reviewed.revision,
        expected_digest=reviewed.digest,
        decision=SkillExperienceDecisionV1(
            decision=decision,  # type: ignore[arg-type]
            target_skill_id=target_skill_id,
            target_draft_id=target_draft_id,
            override_reason=None,
            new_boundary=None,
            actor_kind="local_console",
            decided_at=time.time(),
        ),
    )


def test_create_promotion_hydrates_handoff_and_never_returns_blank_creator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    experience, store, executions, creator, drafts, _, _, promotion = _runtime(tmp_path)
    ready = _promotion_ready(experience, store, executions)
    handoff = creator.session_store.create_or_get_workflow_handoff(
        intent="检查受控发布包并形成可复用流程",
        positive_examples=["检查受控发布包并形成可复用流程"],
        near_miss_examples=["普通摘要"],
        expected_output="输出检查结果",
        success_criteria=["不编造"],
        source_task_id=ready.source_task_id,
        source_run_id=ready.source_run_id,
        trigger_required=True,
    )

    result = promotion.promote(
        ready.candidate_id,
        expected_revision=ready.revision,
        expected_digest=ready.digest,
    )
    repeated = promotion.promote(
        ready.candidate_id,
        expected_revision=ready.revision,
        expected_digest=ready.digest,
    )

    assert result.session.session_id == handoff.session_id == repeated.session.session_id
    assert result.route.endswith("?step=2")
    assert result.session.intent == ready.brief.intent
    assert result.session.positive_examples == list(ready.brief.positive_examples)
    assert result.session.evidence_confirmed is True
    assert result.session.selected_evidence
    assert result.session.authoring_flow == "resource"
    assert result.session.trigger_required is True
    assert result.session.run_experience_case["source"] == "run_experience"
    assert result.session.proposal_id is None
    assert result.draft is None
    assert drafts.find_by_creator_session(result.session.session_id) is None
    assert repeated.candidate.state == "promoted"
    assert len(creator.session_store.list()) == 1


def test_promotion_refuses_to_overwrite_an_edited_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    experience, store, executions, creator, _, _, _, promotion = _runtime(tmp_path)
    ready = _promotion_ready(experience, store, executions)
    captured = creator.session_store.create_or_get_run_capture(
        source_kind="workflow_classic",
        source_task_id=ready.source_task_id,
        source_run_id=ready.source_run_id,
    )
    creator.session_store.update_definition(
        captured.session_id,
        expected_session_revision=captured.session_revision,
        changes={"intent": "用户已编辑的定义"},
    )

    with pytest.raises(SkillExperienceConflictError) as exc_info:
        promotion.promote(
            ready.candidate_id,
            expected_revision=ready.revision,
            expected_digest=ready.digest,
        )
    assert exc_info.value.code == "skill_experience_candidate_conflict"


def test_middleware_uses_candidate_promotion_without_a_second_model_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    experience, store, executions, creator, drafts, _, _, promotion = _runtime(tmp_path)
    executions.create(
        task_id="task-handoff",
        run_id="run-handoff",
        run_type="workflow",
        source_kind="workflow_classic",
        workflow={"id": "handoff-flow", "title": "经验交接"},
        inputs={"user_input": "把发布检查沉淀为 Skill"},
    )
    executions.complete("task-handoff", result="需求分析完成")
    # Simulate the ordinary capture button winning the race before middleware.
    captured, _ = experience.create_or_get(
        SkillExperienceSource(
            source_kind="workflow_classic",
            source_task_id="task-handoff",
            source_run_id="run-handoff",
        )
    )
    handoff = SkillCreatorHandoffService(
        creator,
        enabled=True,
        promotion_service=promotion,
    )

    session = handoff.create_or_get(
        task_id="task-handoff",
        run_id="run-handoff",
        request=SkillCreatorHandoffRequest(
            node_id="creator-handoff",
            intent="检查发布清单并输出可追溯报告",
        ),
    )
    repeated = handoff.create_or_get(
        task_id="task-handoff",
        run_id="run-handoff",
        request=SkillCreatorHandoffRequest(
            node_id="creator-handoff",
            intent="检查发布清单并输出可追溯报告",
        ),
    )

    candidate = store.require(captured.candidate_id)
    assert candidate.state == "promoted"
    assert candidate.analysis_attempt is not None
    assert candidate.analysis_attempt.executor_mode == "trusted_handoff"
    assert candidate.analysis_attempt.status == "succeeded"
    assert candidate.promotion is not None
    assert session.session_id == repeated.session_id == candidate.promotion.session_id
    assert session.intent == "检查发布清单并输出可追溯报告"
    assert session.positive_examples and session.near_miss_examples
    assert drafts.find_by_creator_session(session.session_id) is None
    assert len(store.list_candidates()) == 1
    assert len(creator.session_store.list()) == 1


def test_middleware_preserves_legacy_handoff_while_promotion_flag_is_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "false")
    experience, store, executions, creator, drafts, _, _, promotion = _runtime(tmp_path)
    executions.create(
        task_id="task-disabled-handoff",
        run_id="run-disabled-handoff",
        run_type="workflow",
        source_kind="workflow_classic",
        workflow={"id": "handoff-flow", "title": "兼容交接"},
        inputs={"user_input": "保留旧 Creator 交接"},
    )
    executions.complete("task-disabled-handoff", result="需求分析完成")
    handoff = SkillCreatorHandoffService(
        creator,
        enabled=True,
        promotion_service=promotion,
    )

    session = handoff.create_or_get(
        task_id="task-disabled-handoff",
        run_id="run-disabled-handoff",
        request=SkillCreatorHandoffRequest(
            node_id="creator-handoff",
            intent="保留旧 Creator 交接",
        ),
    )

    assert session.intent == "保留旧 Creator 交接"
    assert session.experience_candidate_id is None
    assert drafts.find_by_creator_session(session.session_id) is None
    assert store.list_candidates() == []


def test_recovered_run_rebinds_pristine_capture_by_stable_task_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    _, store, executions, creator, _, _, _, promotion = _runtime(tmp_path)
    executions.create(
        task_id="task-recovered-handoff",
        run_id="run-before-recovery",
        run_type="workflow",
        source_kind="workflow_classic",
        workflow={"id": "handoff-flow", "title": "恢复交接"},
        inputs={"user_input": "沉淀恢复后的发布检查"},
    )
    executions.complete("task-recovered-handoff", result="需求分析完成")
    captured = creator.session_store.create_or_get_run_capture(
        source_kind="workflow_classic",
        source_task_id="task-recovered-handoff",
        source_run_id="run-before-recovery",
    )
    executions.update_run_id(
        "task-recovered-handoff", run_id="run-after-recovery"
    )
    handoff = SkillCreatorHandoffService(
        creator,
        enabled=True,
        promotion_service=promotion,
    )

    promoted = handoff.create_or_get(
        task_id="task-recovered-handoff",
        run_id="run-after-recovery",
        request=SkillCreatorHandoffRequest(
            node_id="creator-handoff",
            intent="沉淀恢复后的发布检查",
        ),
    )

    assert promoted.session_id == captured.session_id
    assert promoted.source_run_id == "run-after-recovery"
    assert promoted.experience_candidate_id
    assert len(creator.session_store.list()) == 1
    assert len(store.list_candidates()) == 1


def test_private_xpert_promotion_preserves_message_scope_and_nonblank_brief(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    experience, store, executions, creator, _, _, _, promotion = _runtime(tmp_path)
    contexts = experience.context_store
    conversation = contexts.create_conversation("xpert-release", title="发布复盘")
    contexts.append_message(
        "xpert-release",
        conversation.conversation_id,
        role="user",
        content="复盘本次发布检查",
        source_task_id="task-xpert-promotion",
        source_run_id="run-xpert-promotion",
    )
    assistant = contexts.append_message(
        "xpert-release",
        conversation.conversation_id,
        role="assistant",
        content="发布检查复盘完成",
        source_task_id="task-xpert-promotion",
        source_run_id="run-xpert-promotion",
    )
    executions.create(
        task_id="task-xpert-promotion",
        run_id="run-xpert-promotion",
        run_type="xpert",
        source_kind="xpert_chat",
        workflow={"id": "xpert-release", "title": "发布复盘"},
        inputs={"user_input": "复盘本次发布检查"},
        runtime_metadata={
            "xpert_id": "xpert-release",
            "conversation_id": conversation.conversation_id,
        },
    )
    executions.complete("task-xpert-promotion", result="发布检查复盘完成")
    candidate, preview = experience.create_or_get(
        SkillExperienceSource(
            source_kind="xpert_chat",
            source_task_id="task-xpert-promotion",
            source_run_id="run-xpert-promotion",
            source_xpert_id="xpert-release",
            source_conversation_id=conversation.conversation_id,
            source_message_id=assistant.message_id,
        )
    )
    selected = experience.select_evidence(
        candidate.candidate_id,
        expected_revision=candidate.revision,
        expected_digest=candidate.digest,
        preview_fingerprint=preview.preview_fingerprint,
        evidence_ids=[
            item.candidate_id for item in preview.candidates if item.default_selected
        ],
    )
    brief = build_distilled_skill_brief(
        {
            "suggestion": "create",
            "recommendation_reason": "可复用发布复盘流程。",
            "intent": "复盘发布检查并输出可追溯改进项",
            "positive_examples": ["复盘发布检查", "总结发布质量问题"],
            "negative_examples": ["普通文章摘要", "图片编辑"],
            "expected_output": "输出证据、结论和改进项。",
            "success_criteria": ["结论可追溯", "缺失信息不编造"],
            "reusable_steps": ["收集证据", "定位原因", "形成改进项"],
            "failure_boundaries": ["缺少运行证据时停止"],
            "resource_clues": [],
            "overfitting_risk": "不得写死本次发布标识。",
        },
        revision=1,
        source="manual",
    )
    ready = store.prepare_trusted_handoff(
        selected.candidate_id,
        expected_revision=selected.revision,
        expected_digest=selected.digest,
        brief=brief,
    )

    result = promotion.promote(
        ready.candidate_id,
        expected_revision=ready.revision,
        expected_digest=ready.digest,
    )

    assert result.session.source_kind == "xpert_chat"
    assert result.session.source_xpert_id == "xpert-release"
    assert result.session.source_conversation_id == conversation.conversation_id
    assert result.session.source_message_id == assistant.message_id
    assert result.session.intent == brief.intent
    assert result.session.selected_evidence
    assert result.draft is None
    assert len(creator.session_store.list()) == 1


def test_update_promotion_clones_current_lifecycle_version_and_preserves_skill_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    experience, store, executions, creator, drafts, manager, lifecycle, promotion = _runtime(
        tmp_path
    )
    predecessor = drafts.create(
        name="release-review",
        slug="release-review",
        description="Review controlled release packages.",
        skill_markdown=SKILL_MD,
        files={"references/rules.md": "# Release rules\n\nUse declared rules only.\n"},
        creator_session_id="skillcreator-predecessor",
    )
    installed = manager.install_workspace_draft(
        draft_id=predecessor.draft_id,
        slug=predecessor.slug,
        skill_markdown=predecessor.skill_markdown,
        files=predecessor.files,
        source_revision=predecessor.content_revision,
    )
    manager.finalize_lifecycle_transaction(installed.skill_id)
    predecessor = drafts.mark_installed(
        predecessor.draft_id,
        expected_revision=predecessor.revision,
        expected_digest=predecessor.content_digest,
        skill_id=installed.skill_id,
    )
    baseline_state = lifecycle.require_state(installed.skill_id)
    baseline_version = lifecycle.require_version(baseline_state.current_version_id or "")
    ready = _promotion_ready(
        experience,
        store,
        executions,
        decision="update",
        target_skill_id=installed.skill_id,
        target_draft_id=predecessor.draft_id,
    )

    result = promotion.promote(
        ready.candidate_id,
        expected_revision=ready.revision,
        expected_digest=ready.digest,
    )
    assert result.draft is not None
    assert result.draft.draft_id != predecessor.draft_id
    assert result.draft.content_digest == baseline_version.package_digest
    assert result.draft.files == predecessor.files
    assert result.draft.predecessor_draft_id == predecessor.draft_id
    assert result.draft.update_target_skill_id == installed.skill_id
    assert result.draft.update_expected_version_id == baseline_version.version_id
    assert result.session.experience_baseline_version_id == baseline_version.version_id
    assert result.session.proposal_id is None
    assert result.session.cases_revision == 0
    assert result.session.quality_status == "not_evaluated"
    assert result.draft.quality_status == "not_evaluated"
    promotion.require_update_draft_current(result.draft)

    installed_reference = (
        manager.installed_dir
        / installed.skill_id
        / predecessor.slug
        / "references"
        / "rules.md"
    )
    installed_reference.write_text("# Tampered rules\n", encoding="utf-8")
    with pytest.raises(SkillExperienceConflictError) as tampered_info:
        promotion.require_update_draft_current(result.draft)
    assert tampered_info.value.code == "skill_experience_promotion_stale"
    installed_reference.write_text(
        "# Release rules\n\nUse declared rules only.\n", encoding="utf-8"
    )
    promotion.require_update_draft_current(result.draft)

    changed = drafts.update(
        result.draft.draft_id,
        expected_revision=result.draft.revision,
        expected_digest=result.draft.content_digest,
        files={"references/rules.md": "# Release rules\n\nUse current declared rules only.\n"},
    )
    accepted = drafts.waive_evaluation(
        changed.draft_id,
        expected_revision=changed.revision,
        expected_digest=changed.content_digest,
        decision_id="decision-promotion-update",
        actor_id="promotion-test-console",
        reason="Synthetic regression fixture accepted for transaction coverage.",
    )
    with pytest.raises(SkillValidationError) as confirmation_info:
        manager.install_workspace_draft(
            draft_id=accepted.draft_id,
            slug=accepted.slug,
            skill_markdown=accepted.skill_markdown,
            files=accepted.files,
            source_revision=accepted.content_revision,
            quality_required=True,
            quality_status=accepted.quality_status,
            quality_decision_id=accepted.quality_decision.decision_id,
            target_skill_id=accepted.update_target_skill_id,
            predecessor_draft_id=accepted.predecessor_draft_id,
            expected_current_version_id=accepted.update_expected_version_id,
            expected_current_digest=accepted.update_expected_content_digest,
            confirmed=False,
        )
    assert confirmation_info.value.code == "skill_experience_decision_required"

    previous_manager = skills_api._skill_manager
    previous_draft_store = skills_api._skill_draft_store
    previous_guard = skills_api._workspace_draft_install_guard
    skills_api.set_skill_manager_for_tests(manager)
    skills_api.set_skill_draft_store_for_tests(drafts)
    skills_api.configure_workspace_draft_install_guard(None)
    app = FastAPI()
    app.include_router(skills_api.router)
    try:
        with TestClient(app) as client:
            forged = client.post(
                f"/api/skills/drafts/{accepted.draft_id}/install",
                json={
                    "expected_revision": accepted.revision,
                    "expected_digest": accepted.content_digest,
                    "target_skill_id": installed.skill_id,
                    "expected_current_version_id": "skillversion-forged",
                    "expected_current_digest": accepted.update_expected_content_digest,
                    "confirmed": True,
                },
            )
        assert forged.status_code == 409
        assert forged.json()["detail"]["code"] == "skill_experience_promotion_stale"
        assert manager.get_installed_skill(installed.skill_id).source_id == predecessor.draft_id
        assert drafts.require(accepted.draft_id).install_state == "not_installed"
    finally:
        skills_api.set_skill_manager_for_tests(previous_manager)
        skills_api.set_skill_draft_store_for_tests(previous_draft_store)
        skills_api.configure_workspace_draft_install_guard(previous_guard)

    updated_draft, updated_skill = drafts.install_current(
        accepted.draft_id,
        expected_revision=accepted.revision,
        expected_digest=accepted.content_digest,
        installer=lambda item: manager.install_workspace_draft(
            draft_id=item.draft_id,
            slug=item.slug,
            skill_markdown=item.skill_markdown,
            files=item.files,
            source_revision=item.content_revision,
            quality_required=True,
            quality_status=item.quality_status,
            quality_decision_id=item.quality_decision.decision_id,
            target_skill_id=item.update_target_skill_id,
            predecessor_draft_id=item.predecessor_draft_id,
            expected_current_version_id=item.update_expected_version_id,
            expected_current_digest=item.update_expected_content_digest,
            confirmed=True,
        ),
    )
    manager.finalize_lifecycle_transaction(updated_skill.skill_id)

    assert updated_skill.skill_id == installed.skill_id
    assert updated_draft.install_state == "current"
    assert drafts.require(predecessor.draft_id).install_state == "outdated"
    versions = lifecycle.list_versions(installed.skill_id)
    assert len(versions) == 2
    assert lifecycle.require_state(installed.skill_id).current_version_id != baseline_version.version_id
    with pytest.raises(SkillExperienceConflictError) as stale_info:
        promotion.require_update_draft_current(result.draft)
    assert stale_info.value.code == "skill_experience_promotion_stale"

    with pytest.raises(SkillValidationError) as install_stale_info:
        manager.install_workspace_draft(
            draft_id=result.draft.draft_id,
            slug=result.draft.slug,
            skill_markdown=result.draft.skill_markdown,
            files=result.draft.files,
            source_revision=result.draft.content_revision,
            target_skill_id=result.draft.update_target_skill_id,
            predecessor_draft_id=result.draft.predecessor_draft_id,
            expected_current_version_id=result.draft.update_expected_version_id,
            expected_current_digest=result.draft.update_expected_content_digest,
            confirmed=True,
        )
    assert install_stale_info.value.code == "skill_experience_promotion_stale"

    current_state = lifecycle.require_state(installed.skill_id)
    rolled_back = manager.rollback_skill_version(
        installed.skill_id,
        baseline_version.version_id,
        expected_state_revision=current_state.revision,
        expected_current_version_id=current_state.current_version_id,
        expected_package_digest=baseline_version.package_digest,
        confirmed=True,
    )
    drafts.mark_lifecycle_version_installed(
        predecessor.draft_id,
        content_revision=baseline_version.source_revision or 0,
        content_digest=baseline_version.package_digest,
        skill_id=installed.skill_id,
    )
    manager.finalize_lifecycle_transaction(installed.skill_id)
    assert rolled_back.skill_id == installed.skill_id
    assert drafts.require(predecessor.draft_id).install_state == "current"
    assert drafts.require(updated_draft.draft_id).install_state == "outdated"

    manager.uninstall_skill(installed.skill_id)
    drafts.mark_uninstalled_skill(installed.skill_id)
    manager.finalize_lifecycle_transaction(installed.skill_id)
    assert drafts.require(predecessor.draft_id).install_state == "not_installed"
    assert drafts.require(updated_draft.draft_id).install_state == "not_installed"


def test_targeted_update_rejects_cross_source_or_changed_baseline(
    tmp_path: Path,
) -> None:
    lifecycle = SkillLifecycleStore(tmp_path / "lifecycle", enabled=True)
    manager = SkillManager(
        installed_dir=tmp_path / "installed",
        tmp_dir=tmp_path / "tmp",
        lifecycle_store=lifecycle,
    )
    with pytest.raises(SkillValidationError) as exc_info:
        manager.install_workspace_draft(
            draft_id="new-draft",
            slug="release-review",
            skill_markdown=SKILL_MD,
            files={},
            target_skill_id="missing-target",
            predecessor_draft_id="old-draft",
            expected_current_version_id="skillversion-missing",
            expected_current_digest="a" * 64,
            confirmed=True,
        )
    assert exc_info.value.code == "skill_experience_promotion_stale"


def test_update_promotion_rejects_tampered_installed_bytes_before_session_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    experience, store, executions, creator, drafts, manager, _, promotion = _runtime(
        tmp_path
    )
    predecessor = drafts.create(
        name="release-review",
        slug="release-review",
        description="Review controlled release packages.",
        skill_markdown=SKILL_MD,
        files={"references/rules.md": "# Release rules\n\nUse declared rules only.\n"},
        creator_session_id="skillcreator-predecessor",
    )
    installed = manager.install_workspace_draft(
        draft_id=predecessor.draft_id,
        slug=predecessor.slug,
        skill_markdown=predecessor.skill_markdown,
        files=predecessor.files,
        source_revision=predecessor.content_revision,
    )
    manager.finalize_lifecycle_transaction(installed.skill_id)
    predecessor = drafts.mark_installed(
        predecessor.draft_id,
        expected_revision=predecessor.revision,
        expected_digest=predecessor.content_digest,
        skill_id=installed.skill_id,
    )
    ready = _promotion_ready(
        experience,
        store,
        executions,
        decision="update",
        target_skill_id=installed.skill_id,
        target_draft_id=predecessor.draft_id,
    )
    target = (
        manager.installed_dir
        / installed.skill_id
        / predecessor.slug
        / "references"
        / "rules.md"
    )
    target.write_text("# Tampered before promotion\n", encoding="utf-8")

    with pytest.raises(SkillExperienceConflictError) as exc_info:
        promotion.promote(
            ready.candidate_id,
            expected_revision=ready.revision,
            expected_digest=ready.digest,
        )

    assert exc_info.value.code == "skill_experience_promotion_stale"
    assert creator.session_store.list() == []
    assert [item.draft_id for item in drafts.list()] == [predecessor.draft_id]


def test_promotion_revalidates_source_before_creating_creator_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    experience, store, executions, creator, _, _, _, promotion = _runtime(tmp_path)
    ready = _promotion_ready(experience, store, executions)
    executions.update_run_id(ready.source_task_id, run_id="run-promotion-recovered")

    with pytest.raises(SkillExperienceConflictError) as exc_info:
        promotion.promote(
            ready.candidate_id,
            expected_revision=ready.revision,
            expected_digest=ready.digest,
        )

    assert exc_info.value.code == "skill_experience_promotion_stale"
    assert store.require(ready.candidate_id).state == "stale"
    assert creator.session_store.list() == []


def test_promotion_recovers_when_candidate_projection_write_was_interrupted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    experience, store, executions, creator, _, _, _, promotion = _runtime(tmp_path)
    ready = _promotion_ready(experience, store, executions)
    original = store.mark_promoted
    calls = 0

    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("simulated candidate projection interruption")
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "mark_promoted", fail_once)
    with pytest.raises(OSError):
        promotion.promote(
            ready.candidate_id,
            expected_revision=ready.revision,
            expected_digest=ready.digest,
        )
    assert len(creator.session_store.list()) == 1
    assert store.require(ready.candidate_id).state == "promotion_ready"

    recovered = promotion.promote(
        ready.candidate_id,
        expected_revision=ready.revision,
        expected_digest=ready.digest,
    )
    reloaded = SkillExperienceCandidateStore(store.storage_dir).require(
        ready.candidate_id
    )
    assert recovered.candidate.state == reloaded.state == "promoted"
    assert reloaded.promotion is not None
    assert reloaded.promotion.session_id == recovered.session.session_id
    assert len(creator.session_store.list()) == 1


def test_update_promotion_recovers_after_draft_created_before_session_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    experience, store, executions, creator, drafts, manager, _, promotion = _runtime(
        tmp_path
    )
    predecessor = drafts.create(
        name="release-review",
        slug="release-review",
        description="Review controlled release packages.",
        skill_markdown=SKILL_MD,
        files={"references/rules.md": "# Release rules\n\nUse declared rules only.\n"},
        creator_session_id="skillcreator-predecessor",
    )
    installed = manager.install_workspace_draft(
        draft_id=predecessor.draft_id,
        slug=predecessor.slug,
        skill_markdown=predecessor.skill_markdown,
        files=predecessor.files,
        source_revision=predecessor.content_revision,
    )
    manager.finalize_lifecycle_transaction(installed.skill_id)
    predecessor = drafts.mark_installed(
        predecessor.draft_id,
        expected_revision=predecessor.revision,
        expected_digest=predecessor.content_digest,
        skill_id=installed.skill_id,
    )
    ready = _promotion_ready(
        experience,
        store,
        executions,
        decision="update",
        target_skill_id=installed.skill_id,
        target_draft_id=predecessor.draft_id,
    )
    original_bind = creator.session_store.bind_draft
    calls = 0

    def fail_bind_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise SkillCreatorStorageError("simulated bind interruption")
        return original_bind(*args, **kwargs)

    monkeypatch.setattr(creator.session_store, "bind_draft", fail_bind_once)
    with pytest.raises(SkillExperienceError) as exc_info:
        promotion.promote(
            ready.candidate_id,
            expected_revision=ready.revision,
            expected_digest=ready.digest,
        )
    assert exc_info.value.code == "skill_experience_store_unavailable"
    session = creator.session_store.list()[0]
    created = drafts.find_by_creator_session(session.session_id)
    assert created is not None
    assert session.draft_id is None
    assert store.require(ready.candidate_id).state == "promotion_ready"

    recovered = promotion.promote(
        ready.candidate_id,
        expected_revision=ready.revision,
        expected_digest=ready.digest,
    )
    assert recovered.candidate.state == "promoted"
    assert recovered.draft is not None
    assert recovered.session.draft_id == recovered.draft.draft_id
    assert len(creator.session_store.list()) == 1
    assert len(
        [
            item
            for item in drafts.list()
            if item.creator_session_id == recovered.session.session_id
        ]
    ) == 1


def test_concurrent_promotion_requests_converge_on_one_candidate_and_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    experience, store, executions, creator, _, _, _, promotion = _runtime(tmp_path)
    ready = _promotion_ready(experience, store, executions)

    def promote_once():
        return promotion.promote(
            ready.candidate_id,
            expected_revision=ready.revision,
            expected_digest=ready.digest,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: promote_once(), range(2)))

    assert first.session.session_id == second.session.session_id
    assert first.candidate.candidate_id == second.candidate.candidate_id
    assert first.candidate.state == second.candidate.state == "promoted"
    assert len(creator.session_store.list()) == 1
    assert len(store.list_candidates()) == 1


def test_promote_api_returns_prefilled_session_and_idempotent_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    experience, store, executions, _, _, _, _, promotion = _runtime(tmp_path)
    ready = _promotion_ready(experience, store, executions)
    previous_service = experience_api._service
    previous_promotion = experience_api._promotion_service
    experience_api.configure_skill_experience(experience)
    experience_api.configure_skill_experience_promotion(promotion)
    app = FastAPI()
    app.include_router(experience_api.router)
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/skills/experience/candidates/{ready.candidate_id}/promote",
                json={
                    "expected_revision": ready.revision,
                    "expected_digest": ready.digest,
                },
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["creator_session_id"] == payload["session"]["session_id"]
            assert payload["route"].endswith("?step=2")
            assert payload["session"]["intent"]
            assert payload["session"]["positive_examples"]
            assert payload["draft"] is None

            # Promoted retries are idempotent even when the caller only has the
            # original decision revision from a lost HTTP response.
            repeated = client.post(
                f"/api/skills/experience/candidates/{ready.candidate_id}/promote",
                json={
                    "expected_revision": ready.revision,
                    "expected_digest": ready.digest,
                },
            )
            assert repeated.status_code == 200
            assert repeated.json()["creator_session_id"] == payload["creator_session_id"]
    finally:
        experience_api.configure_skill_experience(previous_service)
        experience_api.configure_skill_experience_promotion(previous_promotion)
