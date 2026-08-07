from __future__ import annotations

import os
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

try:
    from server.prompts.store import PromptProfileStore
    from server.skills.creator_quality import evaluate_creator_payload
    from server.skills.draft_store import (
        SkillDraftValidationError,
        WorkspaceSkillDraftStore,
    )
    from server.xperts.models import XpertDefinition, XpertDraft
    from server.xperts.store import XpertStore, default_xpert_workflow
    from server.xperts.validation import validate_xpert_definition
except ModuleNotFoundError:
    from prompts.store import PromptProfileStore
    from skills.creator_quality import evaluate_creator_payload
    from skills.draft_store import SkillDraftValidationError, WorkspaceSkillDraftStore
    from xperts.models import XpertDefinition, XpertDraft
    from xperts.store import XpertStore, default_xpert_workflow
    from xperts.validation import validate_xpert_definition

from .authoring_store import (
    AuthoringProposal,
    AuthoringProposalConflictError,
    AuthoringProposalStore,
    AuthoringProposalValidationError,
)


class AuthoringService:
    """Validates and applies approved proposals to draft-only resource layers."""

    XPERT_PATCH_FIELDS = {"name", "description", "tags", "starters", "draft"}
    PROMPT_PROFILE_PATCH_FIELDS = {
        "name",
        "slug",
        "description",
        "aliases",
        "template",
        "argument_hint",
        "tags",
        "public_app_allowed",
    }

    def __init__(
        self,
        proposal_store: AuthoringProposalStore,
        xpert_store: XpertStore,
        skill_draft_store: WorkspaceSkillDraftStore,
        prompt_profile_store: PromptProfileStore | None = None,
        *,
        xpert_preflight: Callable[[XpertDefinition], Any] | None = None,
        local_console_actor_id: str | None = None,
    ) -> None:
        self.proposal_store = proposal_store
        self.xpert_store = xpert_store
        self.skill_draft_store = skill_draft_store
        self.prompt_profile_store = prompt_profile_store
        self.xpert_preflight = xpert_preflight
        self.local_console_actor_id = (
            str(local_console_actor_id or "").strip()
            or self._load_or_create_local_console_actor_id()
        )
        self._apply_lock = threading.RLock()

    def validate(self, proposal_id: str, *, revision: int) -> AuthoringProposal:
        proposal = self.proposal_store.require(proposal_id)
        if proposal.revision != revision:
            raise AuthoringProposalConflictError(
                "Proposal changed. Reload it before validation."
            )
        details: dict[str, Any] = {}
        try:
            details = self._validate_payload(proposal)
            creator_quality = details.get("creator_quality")
            quality_ready = not isinstance(creator_quality, dict) or bool(
                creator_quality.get("ready")
            )
            validation = {
                "valid": quality_ready,
                "issues": (
                    []
                    if quality_ready
                    else list(creator_quality.get("issues") or [])[:20]
                ),
                **details,
            }
        except SkillDraftValidationError as exc:
            validation = self._skill_validation_result(exc)
        except AuthoringProposalValidationError as exc:
            issues = list(getattr(exc, "issues", []) or [])
            if not issues:
                issues = [
                    {
                        "code": getattr(exc, "code", "authoring_validation"),
                        "severity": "error",
                        "message": str(exc)[:500],
                    }
                ]
            validation = {
                "valid": False,
                "issues": issues[:20],
            }
        except Exception as exc:
            validation = {
                "valid": False,
                "issues": [
                    {
                        "code": "authoring_validation",
                        "severity": "error",
                        "message": str(exc)[:500],
                    }
                ],
            }
        return self.proposal_store.set_validation(
            proposal_id,
            revision=revision,
            validation=validation,
            content_digest=details.get("content_digest"),
            base_digest=details.get("base_digest"),
        )

    def update_pending(
        self,
        proposal_id: str,
        *,
        revision: int,
        title: str | None = None,
        payload: dict[str, Any] | None = None,
        base_revision: int | None = None,
    ) -> AuthoringProposal:
        with self._apply_lock:
            return self.proposal_store.update_pending(
                proposal_id,
                revision=revision,
                title=title,
                payload=payload,
                base_revision=base_revision,
            )

    def approve(
        self,
        proposal_id: str,
        *,
        revision: int,
        apply_key: str | None = None,
        reason: str = "",
        operator: str | None = None,
    ) -> AuthoringProposal:
        del operator  # Public callers cannot choose the trusted audit actor.
        with self._apply_lock:
            proposal = self.proposal_store.require(proposal_id)
            selected_apply_key = str(apply_key or proposal.apply_key).strip()
            if proposal.status == "approved":
                if (
                    proposal.applied_from_revision == revision
                    and proposal.applied_apply_key == selected_apply_key
                ):
                    return proposal
                raise AuthoringProposalConflictError(
                    "Proposal approval no longer matches this apply key."
                )
            proposal = self.proposal_store.require_apply_binding(
                proposal_id,
                revision=revision,
                apply_key=selected_apply_key,
            )
            applied_receipt = None
            if proposal.kind in {"skill_create", "skill_update"}:
                applied_receipt = self.skill_draft_store.require_proposal_receipt(
                    proposal.proposal_id, selected_apply_key
                )
            if applied_receipt is not None:
                return self.proposal_store.transition(
                    proposal_id,
                    revision=revision,
                    status="approved",
                    actor_kind="local_console",
                    actor_id=self.local_console_actor_id,
                    apply_key=selected_apply_key,
                    applied_resource_id=applied_receipt.draft_id,
                    applied_resource_revision=applied_receipt.content_revision,
                    applied_content_digest=applied_receipt.content_digest,
                    decision_reason=reason,
                )
            if proposal.revision != revision:
                raise AuthoringProposalConflictError(
                    "Proposal changed. Reload it before approval."
                )
            try:
                details = self._validate_payload(proposal)
            except AuthoringProposalConflictError as exc:
                self.proposal_store.transition(
                    proposal_id,
                    revision=revision,
                    status="conflict",
                    actor_kind="local_console",
                    actor_id=self.local_console_actor_id,
                    error=str(exc),
                )
                raise
            except SkillDraftValidationError as exc:
                validation = self._skill_validation_result(exc)
                self.proposal_store.set_validation(
                    proposal_id,
                    revision=revision,
                    validation=validation,
                )
                raise AuthoringProposalValidationError(
                    str(exc),
                    code="skill_package_invalid",
                    issues=list(validation.get("issues") or []),
                ) from exc
            creator_quality = details.get("creator_quality")
            if isinstance(creator_quality, dict) and not bool(
                creator_quality.get("ready")
            ):
                raise AuthoringProposalValidationError(
                    "Creator draft has not passed the static authoring completeness gate.",
                    code="skill_creator_draft_incomplete",
                    issues=list(creator_quality.get("issues") or [])[:20],
                )
            validation = {"valid": True, "issues": [], **details}
            proposal = self.proposal_store.set_validation(
                proposal_id,
                revision=revision,
                validation=validation,
                content_digest=details.get("content_digest"),
                base_digest=details.get("base_digest"),
            )
            resource_id = self._apply(proposal)
            applied_receipt = (
                self.skill_draft_store.require_proposal_receipt(
                    proposal.proposal_id, selected_apply_key
                )
                if proposal.kind in {"skill_create", "skill_update"}
                else None
            )
            return self.proposal_store.transition(
                proposal_id,
                revision=proposal.revision,
                status="approved",
                actor_kind="local_console",
                actor_id=self.local_console_actor_id,
                apply_key=selected_apply_key,
                applied_resource_id=resource_id,
                applied_resource_revision=(
                    applied_receipt.content_revision if applied_receipt else None
                ),
                applied_content_digest=(
                    applied_receipt.content_digest if applied_receipt else None
                ),
                decision_reason=reason,
            )

    def reject(
        self,
        proposal_id: str,
        *,
        revision: int,
        operator: str | None = None,
        reason: str = "",
    ) -> AuthoringProposal:
        del operator
        with self._apply_lock:
            return self.proposal_store.transition(
                proposal_id,
                revision=revision,
                status="rejected",
                actor_kind="local_console",
                actor_id=self.local_console_actor_id,
                error=reason,
                decision_reason=reason,
            )

    def cancel(
        self,
        proposal_id: str,
        *,
        revision: int,
        reason: str = "",
        operator: str | None = None,
    ) -> AuthoringProposal:
        del operator
        with self._apply_lock:
            return self.proposal_store.transition(
                proposal_id,
                revision=revision,
                status="cancelled",
                actor_kind="local_console",
                actor_id=self.local_console_actor_id,
                decision_reason=reason,
            )

    def _validate_payload(self, proposal: AuthoringProposal) -> dict[str, Any]:
        payload = proposal.payload
        if proposal.source_type == "meta_planner":
            report = payload.get("meta_planner_report")
            if isinstance(report, dict) and not report.get("human_modified"):
                planner_validation = report.get("validation")
                if (
                    isinstance(planner_validation, dict)
                    and not planner_validation.get("valid", False)
                ):
                    raise AuthoringProposalValidationError(
                        "Meta Planner candidate requires human edits before approval."
                    )
        if proposal.kind == "xpert_create":
            name = str(payload.get("name") or "").strip()
            if not name:
                raise AuthoringProposalValidationError("Xpert name is required.")
            draft_payload = payload.get("draft")
            draft = (
                XpertDraft.model_validate(draft_payload)
                if draft_payload is not None
                else XpertDraft(workflow=default_xpert_workflow("preview", name))
            )
            candidate = XpertDefinition(
                id="proposal-preview",
                slug=str(payload.get("slug") or "proposal-preview"),
                name=name,
                description=str(payload.get("description") or ""),
                tags=list(payload.get("tags") or []),
                starters=list(payload.get("starters") or []),
                draft=draft,
                created_at=time.time(),
                updated_at=time.time(),
            )
            result = self._validate_xpert_candidate(candidate)
            return {"resource_kind": "xpert", "node_count": result.node_count}

        if proposal.kind == "xpert_update":
            target_id = proposal.target_id or str(payload.get("xpert_id") or "")
            if not target_id:
                raise AuthoringProposalValidationError("Target Xpert is required.")
            current = self.xpert_store.get_xpert(target_id)
            if proposal.base_revision != current.draft_revision:
                raise AuthoringProposalConflictError(
                    "Target Xpert draft changed after this proposal was created."
                )
            patch = dict(payload.get("patch") or {})
            unknown = sorted(set(patch) - self.XPERT_PATCH_FIELDS)
            if unknown:
                raise AuthoringProposalValidationError(
                    f"Unsupported Xpert patch fields: {', '.join(unknown)}"
                )
            candidate = current.model_copy(deep=True)
            if "name" in patch:
                candidate.name = str(patch["name"] or "").strip()
            if "description" in patch:
                candidate.description = str(patch["description"] or "")
            if "tags" in patch:
                candidate.tags = list(patch["tags"] or [])
            if "starters" in patch:
                candidate.starters = list(patch["starters"] or [])
            if "draft" in patch:
                candidate.draft = XpertDraft.model_validate(patch["draft"])
            result = self._validate_xpert_candidate(candidate)
            return {"resource_kind": "xpert", "node_count": result.node_count}

        if proposal.kind == "prompt_profile_update":
            if self.prompt_profile_store is None:
                raise AuthoringProposalValidationError(
                    "Prompt Profile authoring is not configured."
                )
            target_id = proposal.target_id or str(payload.get("profile_id") or "")
            if not target_id:
                raise AuthoringProposalValidationError(
                    "Target Prompt Profile is required."
                )
            current = self.prompt_profile_store.get_profile(target_id)
            if proposal.base_revision != current.draft_revision:
                raise AuthoringProposalConflictError(
                    "Target Prompt Profile changed after this proposal was created."
                )
            patch = dict(payload.get("patch") or {})
            unknown = sorted(set(patch) - self.PROMPT_PROFILE_PATCH_FIELDS)
            if unknown:
                raise AuthoringProposalValidationError(
                    "Unsupported Prompt Profile patch fields: " + ", ".join(unknown)
                )
            if not patch:
                raise AuthoringProposalValidationError(
                    "Prompt Profile patch cannot be empty."
                )
            if "template" in patch:
                PromptProfileStore._validate_template(patch["template"])
            if "aliases" in patch:
                PromptProfileStore._clean_aliases(patch["aliases"])
            return {
                "resource_kind": "prompt_profile",
                "profile_id": current.id,
                "base_revision": current.draft_revision,
            }

        skill = dict(payload.get("skill") or payload)
        target_draft = None
        if proposal.kind == "skill_update":
            target_id = proposal.target_id or str(skill.get("draft_id") or "")
            if not target_id:
                raise AuthoringProposalValidationError("Target Skill draft is required.")
            target_draft = self.skill_draft_store.require(target_id)
            if proposal.base_revision != target_draft.revision:
                raise AuthoringProposalConflictError(
                    "Target Skill draft changed after this proposal was created."
                )
        normalized = WorkspaceSkillDraftStore.validate_package(
            name=str(skill.get("name") or (target_draft.name if target_draft else "")),
            slug=str(skill.get("slug") or (target_draft.slug if target_draft else "")),
            description=str(
                skill.get("description")
                if "description" in skill
                else (target_draft.description if target_draft else "")
            ),
            skill_markdown=str(
                skill.get("skill_markdown")
                or skill.get("SKILL.md")
                or (target_draft.skill_markdown if target_draft else "")
            ),
            files=dict(
                skill.get("files")
                if "files" in skill
                else (target_draft.files if target_draft else {})
            ),
        )
        details = {
            "resource_kind": "skill",
            "file_count": 1 + len(normalized["files"]),
            "total_bytes": WorkspaceSkillDraftStore._total_bytes(
                normalized["skill_markdown"], normalized["files"]
            ),
            "validator_version": WorkspaceSkillDraftStore.VALIDATOR_VERSION,
            "content_digest": WorkspaceSkillDraftStore.compute_content_digest(
                **normalized
            ),
            "base_digest": target_draft.content_digest if target_draft else None,
        }
        if proposal.source_type == "skill_creator":
            report = evaluate_creator_payload(
                payload,
                requirement_ids=payload.get("creator_requirement_ids") or (),
            )
            details["creator_quality"] = report.to_dict()
        return details

    @staticmethod
    def _skill_validation_result(exc: SkillDraftValidationError) -> dict[str, Any]:
        issues = getattr(exc, "issues", None)
        if not isinstance(issues, list) or not issues:
            issues = [
                {
                    "code": "skill_package_validation",
                    "severity": "error",
                    "message": str(exc)[:500],
                }
            ]
        return {
            "valid": False,
            "validator_version": WorkspaceSkillDraftStore.VALIDATOR_VERSION,
            "issues": issues[:20],
        }

    def _validate_xpert_candidate(self, candidate: XpertDefinition) -> Any:
        if self.xpert_preflight is None:
            result = validate_xpert_definition(candidate)
        else:
            result, _, _ = self.xpert_preflight(candidate)
        if not result.valid:
            raise AuthoringProposalValidationError(
                "; ".join(issue.message for issue in result.issues[:10])
            )
        return result

    def _apply(self, proposal: AuthoringProposal) -> str:
        payload = proposal.payload
        if proposal.kind == "xpert_create":
            item = self.xpert_store.create_xpert(
                name=str(payload.get("name") or ""),
                slug=payload.get("slug"),
                description=str(payload.get("description") or ""),
                tags=list(payload.get("tags") or []),
                starters=list(payload.get("starters") or []),
            )
            if payload.get("draft") is not None:
                item = self.xpert_store.update_xpert(
                    item.id, {"draft": payload["draft"]}
                )
            return item.id

        if proposal.kind == "xpert_update":
            target_id = proposal.target_id or str(payload.get("xpert_id") or "")
            item = self.xpert_store.update_xpert(
                target_id, dict(payload.get("patch") or {})
            )
            return item.id

        if proposal.kind == "prompt_profile_update":
            if self.prompt_profile_store is None:
                raise AuthoringProposalValidationError(
                    "Prompt Profile authoring is not configured."
                )
            target_id = proposal.target_id or str(payload.get("profile_id") or "")
            item = self.prompt_profile_store.update_profile(
                target_id,
                revision=int(proposal.base_revision or 0),
                patch=dict(payload.get("patch") or {}),
            )
            return item.id

        skill = dict(payload.get("skill") or payload)
        if proposal.kind == "skill_create":
            item = self.skill_draft_store.apply_proposal_create(
                proposal_id=proposal.proposal_id,
                apply_key=proposal.apply_key,
                name=str(skill.get("name") or ""),
                slug=str(skill.get("slug") or ""),
                description=str(skill.get("description") or ""),
                skill_markdown=str(
                    skill.get("skill_markdown") or skill.get("SKILL.md") or ""
                ),
                files=dict(skill.get("files") or {}),
                creator_session_id=proposal.creator_session_id,
            )
        else:
            target_id = proposal.target_id or str(skill.get("draft_id") or "")
            target = self.skill_draft_store.require(target_id)
            if proposal.base_revision != target.revision:
                raise AuthoringProposalConflictError(
                    "Target Skill draft changed after this proposal was created."
                )
            item = self.skill_draft_store.apply_proposal_update(
                target_id,
                proposal_id=proposal.proposal_id,
                apply_key=proposal.apply_key,
                expected_revision=int(proposal.base_revision or 0),
                expected_digest=str(proposal.base_digest or target.content_digest),
                name=skill.get("name"),
                slug=skill.get("slug"),
                description=skill.get("description"),
                skill_markdown=skill.get("skill_markdown") or skill.get("SKILL.md"),
                files=skill.get("files"),
            )
        return item.draft_id

    def _load_or_create_local_console_actor_id(self) -> str:
        storage_dir = Path(self.proposal_store.storage_dir)
        storage_dir.mkdir(parents=True, exist_ok=True)
        actor_path = storage_dir / "local_console_instance_id"
        try:
            existing = actor_path.read_text(encoding="ascii").strip()
        except FileNotFoundError:
            existing = ""
        except OSError:
            existing = ""
        if existing.startswith("console_") and len(existing) <= 80:
            return existing
        actor_id = f"console_{uuid.uuid4().hex}"
        temp_path = actor_path.with_name(
            f"{actor_path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
        )
        try:
            with temp_path.open("x", encoding="ascii", newline="\n") as handle:
                handle.write(actor_id + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temp_path, actor_path)
            except FileExistsError:
                winner = actor_path.read_text(encoding="ascii").strip()
                if not winner.startswith("console_") or len(winner) > 80:
                    raise RuntimeError(
                        "Local console actor identity is corrupted."
                    )
                actor_id = winner
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
        return actor_id
