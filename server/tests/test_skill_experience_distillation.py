from __future__ import annotations

import asyncio
import copy
import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.skills import experience as experience_module
from server.skills import experience_api
from server.skills.application_receipts import SkillApplicationReceiptStore
from server.skills.draft_store import WorkspaceSkillDraftStore
from server.skills.experience import (
    SkillExperienceCandidateStore,
    SkillExperienceConflictError,
    SkillExperienceError,
    SkillExperienceService,
    SkillExperienceSource,
    build_distilled_skill_brief,
    distilled_skill_brief_is_promotion_ready,
)
from server.skills.experience_distillation import (
    DISTILLATION_WORKFLOW_VERSION,
    SkillExperienceDistillationService,
    WorkflowSkillExperienceDistillationExecutor,
    build_distillation_invocation,
)
from server.skills.finder import SkillFinder
from server.skills.skill_manager import InstalledSkill
from server.xpert_runtime.execution_store import (
    WorkflowExecutionConflictError,
    WorkflowExecutionStore,
)
from server.xperts.context import XpertContextStore


class _SkillManager:
    def __init__(self, items: list[InstalledSkill] | None = None) -> None:
        self.items = list(items or [])

    def list_installed_skills(self) -> list[InstalledSkill]:
        return list(self.items)


class _Executor:
    def __init__(self, *, available: bool = True, delay: float = 0) -> None:
        self.is_available = available
        self.delay = delay
        self.calls = 0
        self.contexts: list[dict[str, Any]] = []

    def available(self) -> bool:
        return self.is_available

    async def analyze(self, *, analysis_key: str, context: dict[str, Any]):
        self.calls += 1
        self.contexts.append(context)
        if self.delay:
            await asyncio.sleep(self.delay)
        return build_distilled_skill_brief(
            _complete_brief(), revision=1, source="model"
        )


def _complete_brief(
    *, suggestion: str = "create", no_skill_reason: str | None = None
) -> dict[str, Any]:
    return {
        "suggestion": suggestion,
        "recommendation_reason": "这套检查在多次发布任务中可复用。",
        "no_skill_reason": no_skill_reason,
        "intent": "对 zzincidentplaybook 发布包执行确定性检查",
        "positive_examples": [
            "检查 zzincidentplaybook 发布包中的命名和扩展名",
            "核对 zzincidentplaybook 交付目录是否符合约定",
        ],
        "negative_examples": [
            "撰写普通项目周报",
            "编辑一张产品宣传图片",
        ],
        "expected_output": "输出逐项检查结果和失败原因。",
        "success_criteria": ["所有检查项均有明确结论"],
        "reusable_steps": ["读取发布清单", "逐项检查并汇总"],
        "failure_boundaries": ["缺少发布清单时停止并请求补充"],
        "resource_clues": ["可以使用文本 reference 保存命名规则"],
        "overfitting_risk": "不得把某一次发布目录写死。",
    }


def _runtime(
    tmp_path: Path,
    *,
    executor: Any | None = None,
    installed: list[InstalledSkill] | None = None,
) -> tuple[
    SkillExperienceService,
    SkillExperienceDistillationService,
    WorkflowExecutionStore,
    WorkspaceSkillDraftStore,
    _SkillManager,
]:
    executions = WorkflowExecutionStore(tmp_path / "executions")
    contexts = XpertContextStore(tmp_path / "contexts")
    receipts = SkillApplicationReceiptStore(tmp_path / "receipts")
    store = SkillExperienceCandidateStore(tmp_path / "experience")
    experience = SkillExperienceService(store, executions, contexts, receipts)
    manager = _SkillManager(installed)
    drafts = WorkspaceSkillDraftStore(tmp_path / "drafts")
    distillation = SkillExperienceDistillationService(
        experience,
        store,
        SkillFinder(skill_manager=manager),
        manager,
        drafts,
        executor=executor,
    )
    return experience, distillation, executions, drafts, manager


def _capture_selected(
    experience: SkillExperienceService,
    executions: WorkflowExecutionStore,
):
    executions.create(
        task_id="task-1",
        run_id="run-1",
        run_type="workflow",
        source_kind="workflow_classic",
        workflow={"id": "workflow-1", "title": "发布检查"},
        inputs={"user_input": "对 zzincidentplaybook 发布包做命名检查"},
    )
    executions.append_event(
        "task-1", {"event": "node_completed", "node_id": "check", "tool_name": "rg"}
    )
    executions.complete("task-1", result="检查完成")
    candidate, preview = experience.create_or_get(
        SkillExperienceSource(
            source_kind="workflow_classic",
            source_task_id="task-1",
            source_run_id="run-1",
        )
    )
    selected_ids = [
        item.candidate_id for item in preview.candidates if item.default_selected
    ]
    return experience.select_evidence(
        candidate.candidate_id,
        expected_revision=candidate.revision,
        expected_digest=candidate.digest,
        preview_fingerprint=preview.preview_fingerprint,
        evidence_ids=selected_ids,
    )


@pytest.mark.asyncio
async def test_analysis_is_async_idempotent_and_model_never_sees_private_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    private = InstalledSkill(
        skill_id="private-workspace-skill",
        name="私有发布检查 Skill",
        description="处理 zzincidentplaybook 发布包",
        repo_url="workspace://draft/private",
        sub_path="private-release-check",
        installed_at=1.0,
        source_kind="workspace_draft",
        source_id="private",
        content_digest="a" * 64,
    )
    executor = _Executor(delay=0.05)
    experience, distillation, executions, _, _ = _runtime(
        tmp_path, executor=executor, installed=[private]
    )
    candidate = _capture_selected(experience, executions)

    first = await distillation.start_analysis(
        candidate.candidate_id,
        expected_revision=candidate.revision,
        expected_digest=candidate.digest,
    )
    repeated = await distillation.start_analysis(
        candidate.candidate_id,
        expected_revision=candidate.revision,
        expected_digest=candidate.digest,
    )
    completed = await distillation.wait_for_analysis(candidate.candidate_id)

    assert first.state == repeated.state == "analyzing"
    assert completed.state == "awaiting_review"
    assert completed.brief is not None and completed.brief.complete
    assert executor.calls == 1
    provider_payload = json.dumps(executor.contexts, ensure_ascii=False)
    assert "private-workspace-skill" not in provider_payload
    assert "私有发布检查 Skill" not in provider_payload
    assert completed.overlaps


@pytest.mark.asyncio
async def test_concurrent_analysis_requests_share_one_provider_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    executor = _Executor(delay=0.05)
    experience, distillation, executions, _, _ = _runtime(
        tmp_path, executor=executor
    )
    candidate = _capture_selected(experience, executions)

    first, second = await asyncio.gather(
        distillation.start_analysis(
            candidate.candidate_id,
            expected_revision=candidate.revision,
            expected_digest=candidate.digest,
        ),
        distillation.start_analysis(
            candidate.candidate_id,
            expected_revision=candidate.revision,
            expected_digest=candidate.digest,
        ),
    )
    completed = await distillation.wait_for_analysis(candidate.candidate_id)

    assert first.analysis_attempt == second.analysis_attempt
    assert completed.state == "awaiting_review"
    assert executor.calls == 1


@pytest.mark.asyncio
async def test_dismiss_during_analysis_cannot_be_resurrected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    executor = _Executor(delay=0.05)
    experience, distillation, executions, _, _ = _runtime(
        tmp_path, executor=executor
    )
    candidate = _capture_selected(experience, executions)
    analyzing = await distillation.start_analysis(
        candidate.candidate_id,
        expected_revision=candidate.revision,
        expected_digest=candidate.digest,
    )
    dismissed = experience.dismiss(
        candidate.candidate_id,
        expected_revision=analyzing.revision,
        expected_digest=analyzing.digest,
        reason="用户撤销",
    )

    with pytest.raises(SkillExperienceConflictError):
        await distillation.wait_for_analysis(candidate.candidate_id)
    final = experience.store.require(candidate.candidate_id)
    assert dismissed.state == final.state == "dismissed"
    assert final.brief is None


@pytest.mark.asyncio
async def test_default_evidence_does_not_send_final_output_excerpt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    executor = _Executor()
    experience, distillation, executions, _, _ = _runtime(
        tmp_path, executor=executor
    )
    candidate = _capture_selected(experience, executions)
    await distillation.start_analysis(
        candidate.candidate_id,
        expected_revision=candidate.revision,
        expected_digest=candidate.digest,
    )
    await distillation.wait_for_analysis(candidate.candidate_id)

    provider_payload = json.dumps(executor.contexts, ensure_ascii=False)
    assert "final_output_excerpt" not in provider_payload
    assert "检查完成" not in provider_payload


@pytest.mark.asyncio
async def test_unconfigured_or_invalid_model_falls_back_to_nonempty_manual_brief(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    experience, distillation, executions, _, _ = _runtime(tmp_path)
    candidate = _capture_selected(experience, executions)

    await distillation.start_analysis(
        candidate.candidate_id,
        expected_revision=candidate.revision,
        expected_digest=candidate.digest,
    )
    completed = await distillation.wait_for_analysis(candidate.candidate_id)

    assert completed.state == "awaiting_review"
    assert completed.analysis_attempt is not None
    assert completed.analysis_attempt.status == "manual_required"
    assert completed.analysis_attempt.error_code == "skill_experience_analysis_unconfigured"
    assert completed.brief is not None
    assert completed.brief.intent
    assert completed.brief.complete is False
    assert completed.overlaps == ()


def test_distillation_prompt_pins_the_exact_real_provider_contract() -> None:
    invocation = build_distillation_invocation(
        analysis_key="a" * 64,
        context={"confirmed_evidence": [], "application_summary": {}},
        model_id="test-model",
    )

    prompt = invocation.workflow["nodes"][1]["data"]["rolePrompt"]
    assert '"version":"skill-experience-distillation-v1"' in prompt
    assert '"no_skill_reason":null' in prompt
    assert "the object must contain exactly those fields" in prompt
    assert "illustrative, not a classification to copy" in prompt
    assert "Use Simplified Chinese" in prompt
    assert "even when the confirmed evidence is English" in prompt


@pytest.mark.asyncio
async def test_executor_accepts_no_skill_without_inventing_reusable_steps() -> None:
    payload = _complete_brief(
        suggestion="no_skill",
        no_skill_reason="one_off_task",
    )
    for field in (
        "recommendation_reason",
        "intent",
        "positive_examples",
        "negative_examples",
        "expected_output",
        "success_criteria",
        "reusable_steps",
        "failure_boundaries",
        "resource_clues",
        "overfitting_risk",
    ):
        payload[field] = [] if field.endswith("s") or field in {
            "positive_examples",
            "negative_examples",
            "success_criteria",
            "reusable_steps",
            "failure_boundaries",
            "resource_clues",
        } else ""

    async def runner(_invocation: Any) -> str:
        return json.dumps(
            {"version": DISTILLATION_WORKFLOW_VERSION, **payload},
            ensure_ascii=False,
        )

    executor = WorkflowSkillExperienceDistillationExecutor(
        model_id="provider/model",
        model_available=lambda: True,
        runner=runner,
    )
    brief = await executor.analyze(analysis_key="a" * 64, context={})

    assert brief.suggestion == "no_skill"
    assert brief.no_skill_reason == "one_off_task"
    assert brief.recommendation_reason == ""
    assert brief.reusable_steps == ()
    assert brief.complete is True
    assert distilled_skill_brief_is_promotion_ready(brief) is False


@pytest.mark.asyncio
async def test_no_skill_override_still_requires_a_reusable_creator_brief(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    experience, distillation, executions, _, _ = _runtime(tmp_path)
    candidate = _capture_selected(experience, executions)
    minimal = _complete_brief(
        suggestion="no_skill",
        no_skill_reason="one_off_task",
    )
    minimal.update({
        "intent": "",
        "positive_examples": [],
        "negative_examples": [],
        "expected_output": "",
        "success_criteria": [],
        "reusable_steps": [],
        "failure_boundaries": [],
        "resource_clues": [],
        "overfitting_risk": "",
    })
    brief = build_distilled_skill_brief(minimal, revision=1, source="model")
    overlaps, fingerprint = distillation._build_overlaps(brief)
    analyzing, _ = experience.store.begin_analysis(
        candidate.candidate_id,
        expected_revision=candidate.revision,
        expected_digest=candidate.digest,
        analysis_key="a" * 64,
    )
    assert analyzing.analysis_attempt is not None
    reviewed = experience.store.complete_analysis(
        candidate.candidate_id,
        attempt_id=analyzing.analysis_attempt.attempt_id,
        analysis_key="a" * 64,
        brief=brief,
        overlaps=overlaps,
        overlap_fingerprint=fingerprint,
        executor_mode="model",
        error_code=None,
    )

    with pytest.raises(SkillExperienceError) as exc_info:
        distillation.decide(
            reviewed.candidate_id,
            expected_revision=reviewed.revision,
            expected_digest=reviewed.digest,
            decision="create",
            override_reason="用户确认这个模式需要长期复用。",
        )
    assert exc_info.value.code == "skill_experience_decision_required"


@pytest.mark.asyncio
async def test_invalid_provider_output_uses_manual_brief_without_retrying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    calls = 0

    async def runner(_invocation):
        nonlocal calls
        calls += 1
        return "not-json"

    executor = WorkflowSkillExperienceDistillationExecutor(
        model_id="test-model", model_available=lambda: True, runner=runner
    )
    experience, distillation, executions, _, _ = _runtime(
        tmp_path, executor=executor
    )
    candidate = _capture_selected(experience, executions)
    await distillation.start_analysis(
        candidate.candidate_id,
        expected_revision=candidate.revision,
        expected_digest=candidate.digest,
    )
    completed = await distillation.wait_for_analysis(candidate.candidate_id)
    await distillation.start_analysis(
        candidate.candidate_id,
        expected_revision=candidate.revision,
        expected_digest=candidate.digest,
    )

    assert calls == 1
    assert completed.analysis_attempt is not None
    assert completed.analysis_attempt.status == "manual_required"
    assert completed.analysis_attempt.error_code == "skill_experience_analysis_invalid"


@pytest.mark.asyncio
async def test_unavailable_finder_does_not_leave_analysis_stuck_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    executor = _Executor()
    experience, distillation, executions, drafts, manager = _runtime(
        tmp_path, executor=executor
    )
    distillation.finder = SkillFinder(
        index_path=tmp_path / "missing-runtime-index.json", skill_manager=manager
    )
    candidate = _capture_selected(experience, executions)
    await distillation.start_analysis(
        candidate.candidate_id,
        expected_revision=candidate.revision,
        expected_digest=candidate.digest,
    )
    completed = await distillation.wait_for_analysis(candidate.candidate_id)

    assert completed.state == "awaiting_review"
    assert completed.analysis_attempt is not None
    assert completed.analysis_attempt.status == "manual_required"
    assert completed.analysis_attempt.error_code == "skill_experience_store_unavailable"
    assert completed.brief is not None


@pytest.mark.asyncio
async def test_persisted_running_attempt_can_resume_after_service_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    experience, original, executions, drafts, manager = _runtime(tmp_path)
    candidate = _capture_selected(experience, executions)
    analysis_key = original._analysis_key(candidate)
    running, should_run = experience.store.begin_analysis(
        candidate.candidate_id,
        expected_revision=candidate.revision,
        expected_digest=candidate.digest,
        analysis_key=analysis_key,
    )
    assert should_run is True
    executor = _Executor()
    resumed = SkillExperienceDistillationService(
        experience,
        experience.store,
        SkillFinder(skill_manager=manager),
        manager,
        drafts,
        executor=executor,
    )

    await resumed.start_analysis(
        running.candidate_id,
        expected_revision=running.revision,
        expected_digest=running.digest,
    )
    completed = await resumed.wait_for_analysis(running.candidate_id)

    assert executor.calls == 1
    assert completed.state == "awaiting_review"


@pytest.mark.asyncio
async def test_source_change_during_analysis_fails_closed_and_marks_candidate_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    executor = _Executor(delay=0.05)
    experience, distillation, executions, _, _ = _runtime(
        tmp_path, executor=executor
    )
    candidate = _capture_selected(experience, executions)
    await distillation.start_analysis(
        candidate.candidate_id,
        expected_revision=candidate.revision,
        expected_digest=candidate.digest,
    )
    executions.fail("task-1", error="late invalidation")

    with pytest.raises(SkillExperienceConflictError) as exc_info:
        await distillation.wait_for_analysis(candidate.candidate_id)

    assert exc_info.value.code == "skill_experience_promotion_stale"
    assert experience.store.require(candidate.candidate_id).state == "stale"


@pytest.mark.asyncio
async def test_user_decision_requires_complete_brief_and_a_reasoned_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    experience, distillation, executions, _, _ = _runtime(tmp_path)
    candidate = _capture_selected(experience, executions)
    await distillation.start_analysis(
        candidate.candidate_id,
        expected_revision=candidate.revision,
        expected_digest=candidate.digest,
    )
    manual = await distillation.wait_for_analysis(candidate.candidate_id)
    assert manual.brief is not None and not manual.brief.complete

    edited = distillation.update_brief(
        manual.candidate_id,
        expected_revision=manual.revision,
        expected_digest=manual.digest,
        payload=_complete_brief(
            suggestion="no_skill", no_skill_reason="already_covered"
        ),
    )
    with pytest.raises(SkillExperienceError) as missing_override:
        distillation.decide(
            edited.candidate_id,
            expected_revision=edited.revision,
            expected_digest=edited.digest,
            decision="create",
        )
    assert missing_override.value.code == "skill_experience_decision_required"

    decided = distillation.decide(
        edited.candidate_id,
        expected_revision=edited.revision,
        expected_digest=edited.digest,
        decision="create",
        override_reason="这次运行包含尚未覆盖的确定性检查步骤。",
        new_boundary="仅用于 zzincidentplaybook 风格的受控发布包检查。",
    )
    assert decided.state == "promotion_ready"
    assert decided.decision is not None
    assert decided.decision.actor_kind == "local_console"


@pytest.mark.asyncio
async def test_update_target_must_resolve_to_installed_workspace_creator_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    executor = _Executor()
    experience, distillation, executions, drafts, manager = _runtime(
        tmp_path, executor=executor
    )
    draft = drafts.create(
        name="creator-release-check",
        slug="creator-release-check",
        description="处理 zzincidentplaybook 发布包",
        skill_markdown=(
            "---\nname: creator-release-check\ndescription: 处理 zzincidentplaybook 发布包\n---\n\n# 检查\n"
        ),
        creator_session_id="creator-session-1",
    )
    marked = drafts.mark_installed(draft.draft_id, revision=draft.revision, skill_id="workspace-1")
    manager.items.extend(
        [
            InstalledSkill(
                skill_id="workspace-1",
                name=marked.name,
                description=marked.description,
                repo_url=f"workspace://draft/{marked.draft_id}",
                sub_path=marked.slug,
                installed_at=1.0,
                source_kind="workspace_draft",
                source_id=marked.draft_id,
                content_digest=marked.content_digest,
            ),
            InstalledSkill(
                skill_id="git-1",
                name="Git 发布检查",
                description="处理 zzincidentplaybook 发布包",
                repo_url="https://example.invalid/skill.git",
                sub_path="release-check",
                installed_at=1.0,
                source_ref="a" * 40,
                source_kind="git",
            ),
        ]
    )
    candidate = _capture_selected(experience, executions)
    await distillation.start_analysis(
        candidate.candidate_id,
        expected_revision=candidate.revision,
        expected_digest=candidate.digest,
    )
    completed = await distillation.wait_for_analysis(candidate.candidate_id)

    with pytest.raises(SkillExperienceError) as invalid_target:
        distillation.decide(
            completed.candidate_id,
            expected_revision=completed.revision,
            expected_digest=completed.digest,
            decision="update",
            target_skill_id="git-1",
        )
    assert invalid_target.value.code == "skill_experience_update_target_invalid"

    updated = distillation.decide(
        completed.candidate_id,
        expected_revision=completed.revision,
        expected_digest=completed.digest,
        decision="update",
        target_skill_id="workspace-1",
    )
    assert updated.state == "promotion_ready"
    assert updated.decision is not None
    assert updated.decision.target_draft_id == marked.draft_id


@pytest.mark.asyncio
async def test_active_uninstalled_creator_draft_is_overlap_only_not_update_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    executor = _Executor()
    experience, distillation, executions, drafts, _ = _runtime(
        tmp_path, executor=executor
    )
    draft = drafts.create(
        name="draft-release-check",
        slug="draft-release-check",
        description="处理 zzincidentplaybook 发布包",
        skill_markdown=(
            "---\nname: draft-release-check\ndescription: 处理 zzincidentplaybook 发布包\n---\n\n# 检查\n"
        ),
        creator_session_id="creator-session-draft",
    )
    candidate = _capture_selected(experience, executions)
    await distillation.start_analysis(
        candidate.candidate_id,
        expected_revision=candidate.revision,
        expected_digest=candidate.digest,
    )
    completed = await distillation.wait_for_analysis(candidate.candidate_id)

    overlap = next(
        item for item in completed.overlaps if item.creator_draft_id == draft.draft_id
    )
    assert overlap.source_kind == "creator_draft"
    assert overlap.update_target_eligible is False
    assert overlap.installed_skill_id is None


@pytest.mark.asyncio
async def test_high_overlap_create_requires_new_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    installed = InstalledSkill(
        skill_id="existing-overlap",
        name="zzincidentplaybook-check",
        description="对 zzincidentplaybook 发布包执行确定性检查",
        repo_url="https://example.invalid/existing.git",
        sub_path="zzincidentplaybook-check",
        installed_at=1.0,
        source_ref="a" * 40,
        source_kind="git",
    )
    executor = _Executor()
    experience, distillation, executions, _, _ = _runtime(
        tmp_path, executor=executor, installed=[installed]
    )
    candidate = _capture_selected(experience, executions)
    await distillation.start_analysis(
        candidate.candidate_id,
        expected_revision=candidate.revision,
        expected_digest=candidate.digest,
    )
    completed = await distillation.wait_for_analysis(candidate.candidate_id)
    assert any(item.best_rank <= 3 for item in completed.overlaps)

    with pytest.raises(SkillExperienceError) as exc_info:
        distillation.decide(
            completed.candidate_id,
            expected_revision=completed.revision,
            expected_digest=completed.digest,
            decision="create",
        )
    assert exc_info.value.code == "skill_experience_decision_required"


@pytest.mark.asyncio
async def test_decision_rejects_changed_overlap_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    executor = _Executor()
    experience, distillation, executions, _, manager = _runtime(
        tmp_path, executor=executor
    )
    candidate = _capture_selected(experience, executions)
    await distillation.start_analysis(
        candidate.candidate_id,
        expected_revision=candidate.revision,
        expected_digest=candidate.digest,
    )
    completed = await distillation.wait_for_analysis(candidate.candidate_id)
    manager.items.append(
        InstalledSkill(
            skill_id="late-overlap",
            name="zzincidentplaybook-check",
            description="对 zzincidentplaybook 发布包执行确定性检查",
            repo_url="workspace://draft/late",
            sub_path="zzincidentplaybook-check",
            installed_at=2.0,
            source_kind="workspace_draft",
            source_id="late",
            content_digest="b" * 64,
        )
    )

    with pytest.raises(SkillExperienceConflictError) as exc_info:
        distillation.decide(
            completed.candidate_id,
            expected_revision=completed.revision,
            expected_digest=completed.digest,
            decision="create",
            new_boundary="只处理受控发布包。",
        )
    assert exc_info.value.code == "skill_experience_promotion_stale"


@pytest.mark.asyncio
async def test_nested_distillation_state_reloads_and_rejects_secret_edits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    executor = _Executor()
    experience, distillation, executions, _, _ = _runtime(
        tmp_path, executor=executor
    )
    candidate = _capture_selected(experience, executions)
    await distillation.start_analysis(
        candidate.candidate_id,
        expected_revision=candidate.revision,
        expected_digest=candidate.digest,
    )
    completed = await distillation.wait_for_analysis(candidate.candidate_id)
    reloaded = SkillExperienceCandidateStore(experience.store.storage_dir).require(
        candidate.candidate_id
    )
    assert reloaded.brief == completed.brief
    assert reloaded.overlaps == completed.overlaps

    payload = _complete_brief()
    payload["intent"] = "API_KEY=never-persist-this-secret-123456"
    with pytest.raises(SkillExperienceError):
        distillation.update_brief(
            completed.candidate_id,
            expected_revision=completed.revision,
            expected_digest=completed.digest,
            payload=payload,
        )
    assert "never-persist-this-secret" not in experience.store.snapshot_path.read_text(
        encoding="utf-8"
    )


def test_fixed_executor_rejects_extra_ids_and_uses_no_tools() -> None:
    captured: list[Any] = []

    async def runner(invocation):
        captured.append(invocation)
        payload = {"version": DISTILLATION_WORKFLOW_VERSION, **_complete_brief()}
        payload["candidate_id"] = "forged"
        return json.dumps(payload, ensure_ascii=False)

    executor = WorkflowSkillExperienceDistillationExecutor(
        model_id="test-model", model_available=lambda: True, runner=runner
    )
    with pytest.raises(SkillExperienceError) as exc_info:
        asyncio.run(
            executor.analyze(
                analysis_key="a" * 64,
                context={
                    "confirmed_evidence": [{"kind": "intent_summary", "summary": "test"}],
                    "application_summary": {},
                    "candidate_id": "must-not-pass",
                },
            )
        )
    assert exc_info.value.code == "skill_experience_analysis_invalid"
    assert captured[0].workflow["nodes"][1]["data"]["toolMode"] == "none"
    assert "must-not-pass" not in captured[0].inputs["experience_request"]


def test_brief_rejects_duplicates_and_keyword_stuffing() -> None:
    duplicated = _complete_brief()
    duplicated["negative_examples"][0] = duplicated["positive_examples"][0]
    with pytest.raises(SkillExperienceError):
        build_distilled_skill_brief(duplicated, revision=1, source="model")

    stuffed = _complete_brief()
    stuffed["intent"] = "pdf pdf pdf pdf pdf pdf pdf pdf pdf"
    with pytest.raises(SkillExperienceError) as exc_info:
        build_distilled_skill_brief(stuffed, revision=1, source="model")
    assert exc_info.value.code == "skill_experience_analysis_invalid"


def test_api_analyze_is_202_and_rejects_client_gate_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    experience, distillation, executions, _, _ = _runtime(tmp_path)
    candidate = _capture_selected(experience, executions)
    previous_service = experience_api._service
    previous_distillation = experience_api._distillation_service
    experience_api.configure_skill_experience(experience)
    experience_api.configure_skill_experience_distillation(distillation)
    app = FastAPI()
    app.include_router(experience_api.router)
    try:
        client = TestClient(app)
        invalid = client.post(
            f"/api/skills/experience/candidates/{candidate.candidate_id}/analyze",
            json={
                "expected_revision": candidate.revision,
                "expected_digest": candidate.digest,
                "target_skill_id": "forged",
            },
        )
        assert invalid.status_code == 422
        response = client.post(
            f"/api/skills/experience/candidates/{candidate.candidate_id}/analyze",
            json={
                "expected_revision": candidate.revision,
                "expected_digest": candidate.digest,
            },
        )
        assert response.status_code == 202
        assert response.json()["candidate"]["state"] == "analyzing"
    finally:
        experience_api.configure_skill_experience(previous_service)
        experience_api.configure_skill_experience_distillation(previous_distillation)


def test_api_polling_reaches_manual_review_without_leaving_analyzing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    experience, distillation, executions, _, _ = _runtime(tmp_path)
    candidate = _capture_selected(experience, executions)
    previous_service = experience_api._service
    previous_distillation = experience_api._distillation_service
    experience_api.configure_skill_experience(experience)
    experience_api.configure_skill_experience_distillation(distillation)
    app = FastAPI()
    app.include_router(experience_api.router)
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/skills/experience/candidates/{candidate.candidate_id}/analyze",
                json={
                    "expected_revision": candidate.revision,
                    "expected_digest": candidate.digest,
                },
            )
            assert response.status_code == 202
            state = "analyzing"
            for _ in range(50):
                current = client.get(
                    f"/api/skills/experience/candidates/{candidate.candidate_id}"
                )
                assert current.status_code == 200
                state = current.json()["candidate"]["state"]
                if state != "analyzing":
                    break
                time.sleep(0.01)
            assert state == "awaiting_review"
    finally:
        experience_api.configure_skill_experience(previous_service)
        experience_api.configure_skill_experience_distillation(previous_distillation)


def test_pr1_candidate_digest_migrates_without_destructive_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    experience, _, executions, _, _ = _runtime(tmp_path)
    candidate = _capture_selected(experience, executions)
    payload = json.loads(experience.store.snapshot_path.read_text(encoding="utf-8"))
    raw = payload["candidates"][0]
    for field in (
        "analysis_attempt",
        "brief",
        "overlaps",
        "overlap_fingerprint",
        "decision",
    ):
        raw.pop(field, None)
    digest_payload = dict(raw)
    digest_payload.pop("digest", None)
    raw["digest"] = experience_module._sha256(digest_payload)
    legacy_digest = raw["digest"]
    experience.store.snapshot_path.write_text(json.dumps(payload), encoding="utf-8")

    migrated_store = SkillExperienceCandidateStore(experience.store.storage_dir)
    migrated = migrated_store.require(candidate.candidate_id)
    assert migrated.digest != legacy_digest
    dismissed = migrated_store.dismiss(
        migrated.candidate_id,
        expected_revision=migrated.revision,
        expected_digest=migrated.digest,
        reason="不沉淀",
    )
    reloaded = SkillExperienceCandidateStore(experience.store.storage_dir).require(
        candidate.candidate_id
    )
    assert reloaded.digest == dismissed.digest
    assert reloaded.state == "dismissed"


def test_internal_distillation_run_cannot_be_recaptured_as_experience(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    experience, _, executions, _, _ = _runtime(tmp_path)
    executions.create(
        task_id="task-internal",
        run_id="run-internal",
        run_type="workflow",
        source_kind="workflow_classic",
        workflow={"id": "experience-distillation", "title": "internal"},
        inputs={"user_input": "internal"},
        runtime_metadata={
            "experience_analysis_key": "a" * 64,
            "experience_phase": "distillation",
        },
    )
    executions.complete("task-internal", result="done")

    with pytest.raises(SkillExperienceError) as exc_info:
        experience.create_or_get(
            SkillExperienceSource(
                source_kind="workflow_classic",
                source_task_id="task-internal",
                source_run_id="run-internal",
            )
        )
    assert exc_info.value.code == "skill_experience_source_invalid"


def test_distillation_execution_record_does_not_persist_private_payloads(
    tmp_path: Path,
) -> None:
    store = WorkflowExecutionStore(tmp_path / "private-executions")
    execution = store.create(
        task_id="distillation-task",
        run_id="distillation-run",
        run_type="workflow",
        workflow={
            "id": "experience-distillation",
            "nodes": [{"rolePrompt": "private distillation prompt"}],
        },
        inputs={"experience_request": "private confirmed evidence"},
        runtime_metadata={
            "experience_analysis_key": "a" * 64,
            "experience_workflow_version": DISTILLATION_WORKFLOW_VERSION,
            "experience_phase": "distillation",
        },
    )
    store.append_event(
        execution.task_id,
        {"event": "workflow_end", "final_output": "private distilled response"},
    )
    with pytest.raises(WorkflowExecutionConflictError):
        store.suspend(
            execution.task_id,
            wait_kind="approval",
            wait_id="approval-private",
            continuation={"variables": {"secret": "private continuation"}},
        )
    store.complete(execution.task_id, result="private distilled response")
    failed = store.create(
        task_id="distillation-failed-task",
        run_id="distillation-failed-run",
        run_type="workflow",
        workflow={"id": "experience-distillation-failed"},
        inputs={"experience_request": "private failed evidence"},
        runtime_metadata={
            "experience_analysis_key": "b" * 64,
            "experience_workflow_version": DISTILLATION_WORKFLOW_VERSION,
            "experience_phase": "distillation",
        },
    )
    store.append_event(
        failed.task_id,
        {"event": "error", "message": "private provider failure body"},
    )
    store.fail(failed.task_id, error="private provider failure body")

    persisted = store.snapshot_path.read_text(encoding="utf-8")
    private_texts = (
        "private distillation prompt",
        "private confirmed evidence",
        "private distilled response",
        "private failed evidence",
        "private provider failure body",
    )
    leaked = [private_text for private_text in private_texts if private_text in persisted]
    assert leaked == []


def test_distillation_privacy_projection_requires_complete_server_binding(
    tmp_path: Path,
) -> None:
    store = WorkflowExecutionStore(tmp_path / "ordinary-executions")
    execution = store.create(
        task_id="ordinary-task",
        run_id="ordinary-run",
        run_type="workflow",
        workflow={"id": "ordinary", "title": "Ordinary workflow"},
        inputs={"user_input": "ordinary input"},
        runtime_metadata={"experience_phase": "distillation"},
    )

    assert execution.workflow == {"id": "ordinary", "title": "Ordinary workflow"}
    assert execution.inputs == {"user_input": "ordinary input"}


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["attempt", "overlap", "brief_secret"])
async def test_semantically_forged_nested_records_are_quarantined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tamper: str
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    installed = InstalledSkill(
        skill_id="forgery-target",
        name="zzincidentplaybook-check",
        description="对 zzincidentplaybook 发布包执行确定性检查",
        repo_url="https://example.invalid/forgery.git",
        sub_path="zzincidentplaybook-check",
        installed_at=1.0,
        source_kind="git",
    )
    executor = _Executor()
    experience, distillation, executions, _, _ = _runtime(
        tmp_path, executor=executor, installed=[installed]
    )
    candidate = _capture_selected(experience, executions)
    await distillation.start_analysis(
        candidate.candidate_id,
        expected_revision=candidate.revision,
        expected_digest=candidate.digest,
    )
    completed = await distillation.wait_for_analysis(candidate.candidate_id)
    payload = json.loads(experience.store.snapshot_path.read_text(encoding="utf-8"))
    raw = copy.deepcopy(payload["candidates"][0])
    if tamper == "attempt":
        raw["analysis_attempt"]["executor_mode"] = "manual"
    elif tamper == "overlap":
        raw["overlaps"][0]["update_target_eligible"] = True
    else:
        raw["brief"]["intent"] = "API_KEY=forged-secret-1234567890"
        brief_digest_payload = dict(raw["brief"])
        brief_digest_payload.pop("digest", None)
        raw["brief"]["digest"] = experience_module._sha256(brief_digest_payload)
    candidate_digest_payload = dict(raw)
    candidate_digest_payload.pop("digest", None)
    raw["digest"] = experience_module._sha256(candidate_digest_payload)
    payload["candidates"][0] = raw
    experience.store.snapshot_path.write_text(json.dumps(payload), encoding="utf-8")

    reloaded = SkillExperienceCandidateStore(experience.store.storage_dir)
    assert reloaded.status()["candidate_count"] == 0
    assert reloaded.status()["quarantine_count"] == 1
    assert completed.candidate_id == candidate.candidate_id
