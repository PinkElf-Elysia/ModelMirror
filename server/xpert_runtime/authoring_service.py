from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

try:
    from server.prompts.store import PromptProfileStore
    from server.skills.draft_store import WorkspaceSkillDraftStore
    from server.xperts.models import XpertDefinition, XpertDraft
    from server.xperts.store import XpertStore, default_xpert_workflow
    from server.xperts.validation import validate_xpert_definition
except ModuleNotFoundError:
    from prompts.store import PromptProfileStore
    from skills.draft_store import WorkspaceSkillDraftStore
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
    ) -> None:
        self.proposal_store = proposal_store
        self.xpert_store = xpert_store
        self.skill_draft_store = skill_draft_store
        self.prompt_profile_store = prompt_profile_store
        self.xpert_preflight = xpert_preflight
        self._apply_lock = threading.RLock()

    def validate(self, proposal_id: str, *, revision: int) -> AuthoringProposal:
        proposal = self.proposal_store.require(proposal_id)
        if proposal.revision != revision:
            raise AuthoringProposalConflictError(
                "Proposal changed. Reload it before validation."
            )
        try:
            details = self._validate_payload(proposal)
            validation = {"valid": True, "issues": [], **details}
        except Exception as exc:
            validation = {
                "valid": False,
                "issues": [{"code": "authoring_validation", "message": str(exc)[:500]}],
            }
        return self.proposal_store.set_validation(
            proposal_id, revision=revision, validation=validation
        )

    def approve(
        self, proposal_id: str, *, revision: int, operator: str
    ) -> AuthoringProposal:
        with self._apply_lock:
            proposal = self.proposal_store.require(proposal_id)
            if proposal.revision != revision:
                raise AuthoringProposalConflictError(
                    "Proposal changed. Reload it before approval."
                )
            try:
                details = self._validate_payload(proposal)
            except AuthoringProposalConflictError as exc:
                return self.proposal_store.transition(
                    proposal_id,
                    revision=revision,
                    status="conflict",
                    operator=operator,
                    error=str(exc),
                )
            validation = {"valid": True, "issues": [], **details}
            proposal = self.proposal_store.set_validation(
                proposal_id, revision=revision, validation=validation
            )
            resource_id = self._apply(proposal)
            return self.proposal_store.transition(
                proposal_id,
                revision=proposal.revision,
                status="approved",
                operator=operator,
                applied_resource_id=resource_id,
            )

    def reject(
        self, proposal_id: str, *, revision: int, operator: str, reason: str = ""
    ) -> AuthoringProposal:
        return self.proposal_store.transition(
            proposal_id,
            revision=revision,
            status="rejected",
            operator=operator,
            error=reason,
        )

    def cancel(
        self, proposal_id: str, *, revision: int, operator: str
    ) -> AuthoringProposal:
        return self.proposal_store.transition(
            proposal_id,
            revision=revision,
            status="cancelled",
            operator=operator,
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
        return {
            "resource_kind": "skill",
            "file_count": 1 + len(normalized["files"]),
            "total_bytes": WorkspaceSkillDraftStore._total_bytes(
                normalized["skill_markdown"], normalized["files"]
            ),
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
            item = self.skill_draft_store.create(
                name=str(skill.get("name") or ""),
                slug=str(skill.get("slug") or ""),
                description=str(skill.get("description") or ""),
                skill_markdown=str(
                    skill.get("skill_markdown") or skill.get("SKILL.md") or ""
                ),
                files=dict(skill.get("files") or {}),
                source_proposal_id=proposal.proposal_id,
            )
        else:
            target_id = proposal.target_id or str(skill.get("draft_id") or "")
            target = self.skill_draft_store.require(target_id)
            item = self.skill_draft_store.update(
                target_id,
                revision=target.revision,
                name=skill.get("name"),
                slug=skill.get("slug"),
                description=skill.get("description"),
                skill_markdown=skill.get("skill_markdown") or skill.get("SKILL.md"),
                files=skill.get("files"),
            )
        return item.draft_id
