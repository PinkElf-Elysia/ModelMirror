from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any

from .creator_service import SkillCreatorService
from .creator_store import (
    SkillCreatorConflictError,
    SkillCreatorError,
    SkillCreatorSession,
    SkillCreatorStorageError,
)
from .draft_store import (
    SkillDraftConflictError,
    SkillDraftError,
    SkillDraftStorageError,
    WorkspaceSkillDraft,
    WorkspaceSkillDraftStore,
)
from .experience import (
    SkillExperienceCandidateStore,
    SkillExperienceCandidateV1,
    SkillExperienceConflictError,
    SkillExperienceError,
    SkillExperiencePromotionV1,
    SkillExperienceService,
    SkillExperienceSource,
    build_distilled_skill_brief,
    distilled_skill_brief_is_promotion_ready,
)
from .lifecycle import SkillLifecycleError, SkillLifecycleStore
from .skill_manager import SkillManager, SkillManagerError


@dataclass(frozen=True, slots=True)
class SkillExperiencePromotionResult:
    candidate: SkillExperienceCandidateV1
    session: SkillCreatorSession
    draft: WorkspaceSkillDraft | None
    route: str


class SkillExperiencePromotionService:
    """Promote one reviewed run experience into a recoverable Creator session."""

    def __init__(
        self,
        experience_service: SkillExperienceService,
        candidate_store: SkillExperienceCandidateStore,
        creator_service: SkillCreatorService,
        draft_store: WorkspaceSkillDraftStore,
        skill_manager: SkillManager,
        lifecycle_store: SkillLifecycleStore,
    ) -> None:
        self.experience_service = experience_service
        self.candidate_store = candidate_store
        self.creator_service = creator_service
        self.draft_store = draft_store
        self.skill_manager = skill_manager
        self.lifecycle_store = lifecycle_store

    @property
    def enabled(self) -> bool:
        return self.experience_service.enabled

    def promote_trusted_handoff(
        self,
        *,
        task_id: str,
        run_id: str,
        intent: str,
    ) -> SkillExperiencePromotionResult:
        """Promote an explicitly configured middleware taskInput without a model call."""

        clean_intent = " ".join(str(intent or "").split())
        if not clean_intent:
            raise SkillExperienceError(
                "Creator middleware task input is empty.",
                code="skill_experience_source_invalid",
            )
        candidate, preview = self.experience_service.create_or_get(
            SkillExperienceSource(
                source_kind="workflow_classic",
                source_task_id=str(task_id or "").strip(),
                source_run_id=str(run_id or "").strip(),
            )
        )
        if candidate.state == "promoted":
            return self.promote(
                candidate.candidate_id,
                expected_revision=candidate.revision,
                expected_digest=candidate.digest,
            )
        if candidate.state == "captured" and not candidate.selected_evidence:
            candidate = self.experience_service.select_evidence(
                candidate.candidate_id,
                expected_revision=candidate.revision,
                expected_digest=candidate.digest,
                preview_fingerprint=preview.preview_fingerprint,
                evidence_ids=[
                    item.candidate_id
                    for item in preview.candidates
                    if item.default_selected
                ],
            )
        if candidate.state == "captured":
            brief = build_distilled_skill_brief(
                {
                    "suggestion": "create",
                    "recommendation_reason": (
                        "用户已在经典 Workflow 中显式配置 Creator handoff。"
                    ),
                    "no_skill_reason": None,
                    "intent": clean_intent,
                    "positive_examples": [
                        clean_intent,
                        "对同类输入重复执行上述流程，并保持相同的输出合同。",
                    ],
                    "negative_examples": [
                        "与上述用途无关的普通问答或内容改写。",
                        "只需记录一次性偏好、环境事实或临时状态的任务。",
                    ],
                    "expected_output": (
                        "直接完成上述任务；缺少必要信息时列出待确认项，不编造事实。"
                    ),
                    "success_criteria": [
                        "结果直接解决已声明的任务。",
                        "缺失信息明确标记且不编造。",
                    ],
                    "reusable_steps": [
                        "确认输入与边界。",
                        "按声明用途执行可复用流程。",
                        "核对输出合同并报告缺失项。",
                    ],
                    "failure_boundaries": [
                        "缺少必要输入时停止并请求补充。"
                    ],
                    "resource_clues": [],
                    "overfitting_risk": "不得写死本次运行的具体输入或输出。",
                },
                revision=1,
                source="manual",
            )
            candidate = self.candidate_store.prepare_trusted_handoff(
                candidate.candidate_id,
                expected_revision=candidate.revision,
                expected_digest=candidate.digest,
                brief=brief,
            )
        return self.promote(
            candidate.candidate_id,
            expected_revision=candidate.revision,
            expected_digest=candidate.digest,
        )

    def promote(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        expected_digest: str,
    ) -> SkillExperiencePromotionResult:
        try:
            return self._promote(
                candidate_id,
                expected_revision=expected_revision,
                expected_digest=expected_digest,
            )
        except SkillExperienceError:
            raise
        except (SkillCreatorStorageError, SkillDraftStorageError) as exc:
            raise SkillExperienceError(
                "Skill experience promotion storage is unavailable.",
                code="skill_experience_store_unavailable",
            ) from exc
        except (SkillCreatorConflictError, SkillDraftConflictError) as exc:
            raise SkillExperienceConflictError(
                "Skill experience promotion conflicts with current Creator state.",
                code="skill_experience_candidate_conflict",
            ) from exc
        except (SkillCreatorError, SkillDraftError) as exc:
            raise SkillExperienceError(
                "Skill experience promotion can no longer use the current Creator state.",
                code="skill_experience_promotion_stale",
            ) from exc

    def _promote(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        expected_digest: str,
    ) -> SkillExperiencePromotionResult:
        current = self.experience_service.require_current_candidate(candidate_id)
        if current.state == "promoted" and current.promotion is not None:
            session, draft = self.creator_service.get_session(
                current.promotion.session_id
            )
            return SkillExperiencePromotionResult(
                candidate=current,
                session=session,
                draft=draft,
                route=current.promotion.route,
            )
        if current.revision != int(expected_revision) or current.digest != str(
            expected_digest or ""
        ).strip().lower():
            raise SkillExperienceConflictError(
                "Skill experience candidate changed. Reload before promotion.",
                code="skill_experience_candidate_conflict",
                details={
                    "current_revision": current.revision,
                    "current_digest": current.digest,
                },
            )
        if (
            current.state != "promotion_ready"
            or current.brief is None
            or not current.brief.complete
            or not distilled_skill_brief_is_promotion_ready(current.brief)
            or current.decision is None
            or current.decision.decision not in {"create", "update"}
        ):
            raise SkillExperienceConflictError(
                "Review and confirm the Skill experience decision before promotion.",
                code="skill_experience_decision_required",
            )

        baseline = self._resolve_update_baseline(current)
        selected_evidence = [
            {
                "candidate_id": item.evidence_id,
                "kind": item.kind,
                "title": item.title,
                "summary": item.summary,
                "content_hash": item.content_hash,
            }
            for item in current.selected_evidence
        ]
        session = self.creator_service.session_store.create_or_get_experience_promotion(
            experience_candidate_id=current.candidate_id,
            intent=current.brief.intent,
            positive_examples=list(current.brief.positive_examples),
            near_miss_examples=list(current.brief.negative_examples),
            expected_output=current.brief.expected_output,
            success_criteria=list(current.brief.success_criteria),
            selected_evidence=selected_evidence,
            evidence_preview_fingerprint=current.evidence_preview_fingerprint,
            source_kind=current.source_kind,
            source_task_id=current.source_task_id,
            source_run_id=current.source_run_id,
            source_xpert_id=current.source_xpert_id,
            source_conversation_id=current.source_conversation_id,
            source_message_id=current.source_message_id,
            decision=current.decision.decision,
            update_target_skill_id=(baseline["skill_id"] if baseline else None),
            predecessor_draft_id=(baseline["draft_id"] if baseline else None),
            baseline_version_id=(baseline["version_id"] if baseline else None),
            baseline_content_digest=(baseline["content_digest"] if baseline else None),
            run_experience_case=self._regression_case(current),
        )

        draft: WorkspaceSkillDraft | None = None
        if baseline is not None:
            package = baseline["package"]
            draft = self.draft_store.create_creator_draft(
                creator_session_id=session.session_id,
                name=str(package["name"]),
                slug=str(package["slug"]),
                description=str(package["description"]),
                skill_markdown=str(package["skill_markdown"]),
                files=dict(package["files"]),
                experience_candidate_id=current.candidate_id,
                predecessor_draft_id=str(baseline["draft_id"]),
                update_target_skill_id=str(baseline["skill_id"]),
                update_expected_version_id=str(baseline["version_id"]),
                update_expected_content_digest=str(baseline["content_digest"]),
            )
            session = self.creator_service.session_store.bind_draft(
                session.session_id,
                draft_id=draft.draft_id,
                draft_state_revision=draft.revision,
                content_revision=draft.content_revision,
                content_digest=draft.content_digest,
            )

        route = f"/skills/create/{session.session_id}?step=2"
        promotion = SkillExperiencePromotionV1(
            session_id=session.session_id,
            route=route,
            decision=current.decision.decision,
            target_skill_id=(str(baseline["skill_id"]) if baseline else None),
            target_draft_id=(str(baseline["draft_id"]) if baseline else None),
            baseline_version_id=(str(baseline["version_id"]) if baseline else None),
            baseline_content_digest=(
                str(baseline["content_digest"]) if baseline else None
            ),
            promoted_at=time.time(),
        )
        promoted = self.candidate_store.mark_promoted(
            current.candidate_id,
            expected_revision=current.revision,
            expected_digest=current.digest,
            promotion=promotion,
        )
        return SkillExperiencePromotionResult(
            candidate=promoted,
            session=session,
            draft=draft,
            route=route,
        )

    def require_update_draft_current(self, draft: WorkspaceSkillDraft | None) -> None:
        """Fail closed when an experience update no longer targets its baseline."""

        if draft is None or draft.update_target_skill_id is None:
            return
        values = (
            draft.predecessor_draft_id,
            draft.update_expected_version_id,
            draft.update_expected_content_digest,
        )
        if not all(values):
            raise SkillExperienceError(
                "The Creator Skill update baseline is incomplete.",
                code="skill_experience_promotion_stale",
            )
        try:
            installed = self.skill_manager.get_installed_skill(
                draft.update_target_skill_id
            )
            state = self.lifecycle_store.require_state(draft.update_target_skill_id)
            version = self.lifecycle_store.require_version(
                draft.update_expected_version_id or ""
            )
        except (SkillManagerError, SkillLifecycleError) as exc:
            raise SkillExperienceError(
                "The Creator Skill update baseline is unavailable.",
                code="skill_experience_promotion_stale",
            ) from exc
        if (
            installed.source_kind != "workspace_draft"
            or installed.source_id != draft.predecessor_draft_id
            or installed.content_digest != draft.update_expected_content_digest
            or state.status != "active"
            or state.current_version_id != draft.update_expected_version_id
            or version.skill_id != draft.update_target_skill_id
            or version.package_digest != draft.update_expected_content_digest
            or version.source_kind != "workspace_draft"
            or version.source_id != draft.predecessor_draft_id
            or not self.skill_manager.installed_matches_lifecycle_version(
                draft.update_target_skill_id,
                draft.update_expected_version_id or "",
            )
        ):
            raise SkillExperienceConflictError(
                "The Creator Skill changed after this update was prepared.",
                code="skill_experience_promotion_stale",
            )

    def _resolve_update_baseline(
        self, candidate: SkillExperienceCandidateV1
    ) -> dict[str, Any] | None:
        decision = candidate.decision
        if decision is None or decision.decision == "create":
            return None
        if not decision.target_skill_id or not decision.target_draft_id:
            raise SkillExperienceError(
                "The selected Creator Skill update target is incomplete.",
                code="skill_experience_update_target_invalid",
            )
        try:
            installed = self.skill_manager.get_installed_skill(decision.target_skill_id)
            target_draft = self.draft_store.require(decision.target_draft_id)
            state = self.lifecycle_store.require_state(decision.target_skill_id)
            if state.status != "active" or not state.current_version_id:
                raise SkillExperienceError(
                    "The selected Creator Skill has no active immutable version.",
                    code="skill_experience_promotion_stale",
                )
            version = self.lifecycle_store.require_version(state.current_version_id)
        except SkillExperienceError:
            raise
        except (SkillManagerError, SkillDraftError, SkillLifecycleError) as exc:
            raise SkillExperienceError(
                "The selected Creator Skill update target is unavailable.",
                code="skill_experience_update_target_invalid",
            ) from exc
        if (
            installed.source_kind != "workspace_draft"
            or installed.source_id != decision.target_draft_id
            or target_draft.creator_session_id is None
            or target_draft.status == "archived"
            or target_draft.installed_skill_id != installed.skill_id
            or version.source_kind != "workspace_draft"
            or version.source_id != decision.target_draft_id
            or version.source_revision is None
            or version.package_digest != installed.content_digest
        ):
            raise SkillExperienceError(
                "Only the current immutable version of a Workspace Creator Skill may be updated.",
                code="skill_experience_update_target_invalid",
            )
        if not self.skill_manager.installed_matches_lifecycle_version(
            installed.skill_id, version.version_id
        ):
            raise SkillExperienceConflictError(
                "The installed Creator Skill bytes no longer match its immutable version.",
                code="skill_experience_promotion_stale",
            )
        try:
            snapshot = self.draft_store.require_revision_snapshot(
                decision.target_draft_id,
                revision=version.source_revision,
                content_digest=version.package_digest,
            )
        except (SkillDraftConflictError, SkillDraftError) as exc:
            raise SkillExperienceError(
                "The selected Creator Skill baseline can no longer be reproduced.",
                code="skill_experience_promotion_stale",
            ) from exc
        return {
            "skill_id": installed.skill_id,
            "draft_id": decision.target_draft_id,
            "version_id": version.version_id,
            "content_digest": version.package_digest,
            "package": snapshot.package,
        }

    @staticmethod
    def _regression_case(candidate: SkillExperienceCandidateV1) -> dict[str, Any]:
        if candidate.brief is None:
            raise SkillExperienceError(
                "Skill experience brief is unavailable.",
                code="skill_experience_promotion_stale",
            )
        prompt = (
            candidate.brief.positive_examples[0]
            if candidate.brief.positive_examples
            else candidate.brief.intent
        )
        expected = candidate.brief.expected_output
        if candidate.brief.success_criteria:
            expected = f"{expected} 验收：{'；'.join(candidate.brief.success_criteria)}"
        case_hash = hashlib.sha256(
            f"{candidate.candidate_id}:{prompt}:{expected}".encode("utf-8")
        ).hexdigest()[:20]
        return {
            "case_id": f"run_experience_{case_hash}",
            "role": "regression",
            "source": "run_experience",
            "name": "本次成功运行的回归验证",
            "prompt": prompt,
            "expected_behavior": expected,
            "fixtures": [],
            "assertions": [],
            "requirement_ids": [],
            "required_resource_paths": [],
            "workflow_step_ids": [],
        }
