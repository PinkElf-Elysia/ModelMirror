from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from .creator_store import (
    CREATOR_ASSISTANT_AGENT_ID,
    CreatorMode,
    CreatorSourceKind,
    SkillCreatorConflictError,
    SkillCreatorError,
    SkillCreatorSession,
    SkillCreatorSessionStore,
    SkillCreatorValidationError,
)
from .draft_store import WorkspaceSkillDraft, WorkspaceSkillDraftStore
from .package_validation import validate_skill_package

try:
    from server.xpert_runtime.authoring_service import AuthoringService
    from server.xpert_runtime.authoring_store import (
        AuthoringProposal,
        AuthoringProposalError,
    )
except ModuleNotFoundError:
    from xpert_runtime.authoring_service import AuthoringService
    from xpert_runtime.authoring_store import AuthoringProposal, AuthoringProposalError


@dataclass(frozen=True, slots=True)
class CreatorSourceDescriptor:
    source_kind: CreatorSourceKind
    source_task_id: str
    source_run_id: str
    source_xpert_id: str | None = None
    source_conversation_id: str | None = None
    source_message_id: str | None = None


@dataclass(frozen=True, slots=True)
class CreatorEvidencePreview:
    fingerprint: str
    candidates: tuple[dict[str, str], ...]


class CreatorSourceProvider(Protocol):
    @property
    def supported_sources(self) -> tuple[str, ...]: ...

    def validate_source(self, source: CreatorSourceDescriptor) -> None: ...

    def preview(self, session: SkillCreatorSession) -> CreatorEvidencePreview: ...

    def select(
        self,
        session: SkillCreatorSession,
        *,
        preview_fingerprint: str,
        candidate_ids: list[str],
    ) -> list[dict[str, str]]: ...


@dataclass(frozen=True, slots=True)
class CreatorGenerationRequest:
    session: dict[str, Any]
    target_draft: dict[str, Any] | None
    allowed_tool: str
    trusted_iteration: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class CreatorGenerationResult:
    proposal_id: str
    tool_name: str
    runtime_run_id: str | None = None
    runtime_task_id: str | None = None


class CreatorGenerationExecutor(Protocol):
    def available(self) -> bool: ...

    async def generate(
        self, request: CreatorGenerationRequest
    ) -> CreatorGenerationResult: ...


_generation_executor: CreatorGenerationExecutor | None = None
_generation_executor_lock = threading.RLock()


def configure_creator_generation_executor(
    executor: CreatorGenerationExecutor | None,
) -> None:
    """Configure the single trusted Creator model adapter.

    The adapter must execute the dedicated workflow_agent and return the ID of
    the proposal created by its one allowed authoring tool call. This module
    re-reads that proposal and verifies its trusted session and run bindings.
    """

    global _generation_executor
    with _generation_executor_lock:
        _generation_executor = executor


def get_creator_generation_executor() -> CreatorGenerationExecutor | None:
    with _generation_executor_lock:
        return _generation_executor


class SkillCreatorService:
    VERSION = "skill-creator-v1"

    def __init__(
        self,
        session_store: SkillCreatorSessionStore,
        draft_store: WorkspaceSkillDraftStore,
        authoring_service: AuthoringService,
        *,
        enabled: bool | None = None,
        source_provider: CreatorSourceProvider | None = None,
        generation_executor: CreatorGenerationExecutor | None = None,
    ) -> None:
        self.session_store = session_store
        self.draft_store = draft_store
        self.authoring_service = authoring_service
        self.enabled = (
            os.getenv("SKILL_CREATOR_V2_ENABLED", "true").strip().lower()
            in {"1", "true", "yes", "on"}
            if enabled is None
            else bool(enabled)
        )
        self.source_provider = source_provider
        self.generation_executor = generation_executor
        self._generation_locks_guard = threading.RLock()
        self._generation_locks: dict[str, asyncio.Lock] = {}

    def status(self) -> dict[str, Any]:
        executor = self._executor()
        try:
            model_available = bool(executor and executor.available())
        except Exception:
            model_available = False
        sources = ["blank"]
        if self.source_provider is not None:
            sources.extend(self.source_provider.supported_sources)
        return {
            "version": self.VERSION,
            "enabled": self.enabled,
            "assistant_agent_id": CREATOR_ASSISTANT_AGENT_ID,
            "model_available": model_available,
            "supported_sources": list(dict.fromkeys(sources)),
            "quality_gate": "not_evaluated",
        }

    def require_enabled(self) -> None:
        if not self.enabled:
            raise SkillCreatorValidationError(
                "Skill Creator V2 is disabled.", code="skill_creator_disabled"
            )

    def create_session(
        self,
        *,
        mode: CreatorMode,
        intent: str,
        positive_examples: list[str],
        near_miss_examples: list[str],
        expected_output: str,
        success_criteria: list[str],
        source_kind: CreatorSourceKind = "blank",
        source_task_id: str | None = None,
        source_run_id: str | None = None,
        source_xpert_id: str | None = None,
        source_conversation_id: str | None = None,
        source_message_id: str | None = None,
    ) -> SkillCreatorSession:
        self.require_enabled()
        if mode == "run":
            if self.source_provider is None:
                raise SkillCreatorValidationError(
                    "Trusted runtime sources are not configured.",
                    code="skill_creator_source_unavailable",
                )
            self.source_provider.validate_source(
                CreatorSourceDescriptor(
                    source_kind=source_kind,
                    source_task_id=str(source_task_id or ""),
                    source_run_id=str(source_run_id or ""),
                    source_xpert_id=source_xpert_id,
                    source_conversation_id=source_conversation_id,
                    source_message_id=source_message_id,
                )
            )
        return self.session_store.create(
            mode=mode,
            intent=intent,
            positive_examples=positive_examples,
            near_miss_examples=near_miss_examples,
            expected_output=expected_output,
            success_criteria=success_criteria,
            source_kind=source_kind,
            source_task_id=source_task_id,
            source_run_id=source_run_id,
            source_xpert_id=source_xpert_id,
            source_conversation_id=source_conversation_id,
            source_message_id=source_message_id,
        )

    def list_sessions(self, *, limit: int) -> list[SkillCreatorSession]:
        self.require_enabled()
        return self.session_store.list(limit=limit)

    def get_session(
        self, session_id: str
    ) -> tuple[SkillCreatorSession, WorkspaceSkillDraft | None]:
        self.require_enabled()
        session = self.session_store.require(session_id)
        proposal = None
        if session.proposal_id:
            try:
                proposal = self.authoring_service.proposal_store.require(
                    session.proposal_id
                )
            except AuthoringProposalError:
                proposal = None
            if proposal is not None and not self._is_session_proposal(
                proposal, session
            ):
                raise SkillCreatorConflictError(
                    "Creator session references an unrelated proposal."
                )
        if proposal is None:
            candidates = [
                item
                for item in self.authoring_service.proposal_store.list(
                    creator_session_id=session.session_id, limit=20
                )
                if self._is_session_proposal(item, session)
                and item.status in {"pending", "approved"}
                and item.creator_session_revision == session.session_revision
            ]
            if len(candidates) > 1:
                raise SkillCreatorConflictError(
                    "Multiple Creator proposals match this session revision."
                )
            if candidates:
                proposal = candidates[0]
                session = self.session_store.bind_proposal(
                    session.session_id,
                    expected_session_revision=session.session_revision,
                    proposal_id=proposal.proposal_id,
                )
        if proposal is not None:
            if proposal.status == "pending" and proposal.kind in {
                "skill_create",
                "skill_update",
            }:
                receipt = self.draft_store.require_proposal_receipt(
                    proposal.proposal_id,
                    proposal.apply_key,
                )
                if receipt is not None:
                    proposal = self.authoring_service.approve(
                        proposal.proposal_id,
                        revision=proposal.revision,
                        apply_key=proposal.apply_key,
                        reason="Recovered a previously approved durable Skill draft apply.",
                    )
            if (
                proposal.status == "approved"
                and proposal.applied_resource_id
            ):
                session = self._bind_existing_draft(
                    session, proposal.applied_resource_id
                )
        draft = None
        if not session.draft_id:
            recovered = self.draft_store.find_by_creator_session(session.session_id)
            if recovered is not None:
                session = self.session_store.bind_draft(
                    session.session_id,
                    draft_id=recovered.draft_id,
                    draft_state_revision=recovered.revision,
                    content_revision=recovered.content_revision,
                    content_digest=recovered.content_digest,
                )
        if session.draft_id:
            draft = self.draft_store.require(session.draft_id)
            if (
                session.draft_state_revision != draft.revision
                or session.current_revision != draft.content_revision
                or session.current_digest != draft.content_digest
            ):
                session = self.session_store.bind_draft(
                    session.session_id,
                    draft_id=draft.draft_id,
                    draft_state_revision=draft.revision,
                    content_revision=draft.content_revision,
                    content_digest=draft.content_digest,
                )
        return session, draft

    def update_definition(
        self,
        session_id: str,
        *,
        expected_session_revision: int,
        changes: dict[str, Any],
    ) -> SkillCreatorSession:
        self.require_enabled()
        current = self.session_store.require(session_id)
        self._require_session_revision(current, expected_session_revision)
        self._cancel_pending_proposal(current)
        return self.session_store.update_definition(
            session_id,
            expected_session_revision=expected_session_revision,
            changes=changes,
        )

    def preview_source(self, session_id: str) -> CreatorEvidencePreview:
        self.require_enabled()
        session = self.session_store.require(session_id)
        if session.mode == "blank":
            return CreatorEvidencePreview(
                fingerprint=self._blank_preview_fingerprint(session), candidates=()
            )
        if self.source_provider is None:
            raise SkillCreatorValidationError(
                "Trusted runtime sources are not configured.",
                code="skill_creator_source_unavailable",
            )
        return self.source_provider.preview(session)

    def select_evidence(
        self,
        session_id: str,
        *,
        expected_session_revision: int,
        preview_fingerprint: str,
        candidate_ids: list[str],
    ) -> SkillCreatorSession:
        self.require_enabled()
        session = self.session_store.require(session_id)
        self._require_session_revision(session, expected_session_revision)
        self._cancel_pending_proposal(session)
        if session.mode == "blank":
            if candidate_ids or preview_fingerprint != self._blank_preview_fingerprint(
                session
            ):
                raise SkillCreatorConflictError(
                    "Creator evidence preview changed. Refresh it before confirming."
                )
            selected: list[dict[str, str]] = []
        else:
            if self.source_provider is None:
                raise SkillCreatorValidationError(
                    "Trusted runtime sources are not configured.",
                    code="skill_creator_source_unavailable",
                )
            selected = self.source_provider.select(
                session,
                preview_fingerprint=preview_fingerprint,
                candidate_ids=candidate_ids,
            )
        return self.session_store.set_evidence(
            session_id,
            expected_session_revision=expected_session_revision,
            preview_fingerprint=preview_fingerprint,
            selected_evidence=selected,
        )

    def create_blank_draft(
        self,
        session_id: str,
        *,
        expected_session_revision: int,
        skill_id: str,
        description: str,
    ) -> tuple[SkillCreatorSession, WorkspaceSkillDraft]:
        self.require_enabled()
        session = self.session_store.require(session_id)
        self._require_session_revision(session, expected_session_revision)
        self._require_ready_for_draft(session)
        if session.draft_id:
            draft = self.draft_store.require(session.draft_id)
            if draft.slug != skill_id:
                raise SkillCreatorConflictError(
                    "Creator session already owns another Skill draft."
                )
            return session, draft
        markdown = self._blank_skill_markdown(
            skill_id=skill_id,
            description=description,
            intent=session.intent,
            positive_examples=session.positive_examples,
            near_miss_examples=session.near_miss_examples,
            expected_output=session.expected_output,
            success_criteria=session.success_criteria,
        )
        draft = self.draft_store.create_creator_draft(
            creator_session_id=session.session_id,
            name=skill_id,
            slug=skill_id,
            description=description,
            skill_markdown=markdown,
            files={},
        )
        session = self.session_store.bind_draft(
            session.session_id,
            expected_session_revision=expected_session_revision,
            draft_id=draft.draft_id,
            draft_state_revision=draft.revision,
            content_revision=draft.content_revision,
            content_digest=draft.content_digest,
        )
        return session, draft

    def update_draft(
        self,
        session_id: str,
        *,
        expected_session_revision: int,
        expected_revision: int,
        expected_digest: str,
        changes: dict[str, Any],
    ) -> tuple[SkillCreatorSession, WorkspaceSkillDraft]:
        self.require_enabled()
        session = self.session_store.require(session_id)
        self._require_session_revision(session, expected_session_revision)
        if not session.draft_id:
            raise SkillCreatorConflictError("Creator session has no Skill draft.")
        self._cancel_pending_proposal(session)
        draft = self.draft_store.update(
            session.draft_id,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
            **changes,
        )
        session = self.session_store.bind_draft(
            session.session_id,
            expected_session_revision=expected_session_revision,
            draft_id=draft.draft_id,
            draft_state_revision=draft.revision,
            content_revision=draft.content_revision,
            content_digest=draft.content_digest,
        )
        return session, draft

    async def generate(
        self,
        session_id: str,
        *,
        expected_session_revision: int,
        trusted_iteration: dict[str, Any] | None = None,
    ) -> AuthoringProposal:
        self.require_enabled()
        session = self.session_store.require(session_id)
        async with self._generation_lock(session.session_id):
            return await self._generate_locked(
                session.session_id,
                expected_session_revision=expected_session_revision,
                trusted_iteration=trusted_iteration,
            )

    async def _generate_locked(
        self,
        session_id: str,
        *,
        expected_session_revision: int,
        trusted_iteration: dict[str, Any] | None = None,
    ) -> AuthoringProposal:
        self.require_enabled()
        session, draft = self.get_session(session_id)
        self._require_session_revision(session, expected_session_revision)
        self._require_ready_for_draft(session)
        self._require_ready_for_generation(session)
        executor = self._executor()
        if executor is None:
            raise SkillCreatorValidationError(
                "The Skill Creator model gateway is not configured.",
                code="model_gateway_unconfigured",
            )
        try:
            executor_available = executor.available()
        except Exception as exc:
            raise SkillCreatorValidationError(
                "The Skill Creator model gateway status is unavailable.",
                code="skill_creator_generation_failed",
            ) from exc
        if not executor_available:
            raise SkillCreatorValidationError(
                "The Skill Creator model gateway is not configured.",
                code="model_gateway_unconfigured",
            )
        self._cancel_pending_proposal(session)
        allowed_tool = (
            "skill_authoring_propose_update"
            if draft
            else "skill_authoring_propose_create"
        )
        try:
            result = await executor.generate(
                CreatorGenerationRequest(
                    session=asdict(session),
                    target_draft=(
                        WorkspaceSkillDraftStore.serialize(
                            draft, include_content=True
                        )
                        if draft
                        else None
                    ),
                    allowed_tool=allowed_tool,
                    trusted_iteration=self._normalize_trusted_iteration(
                        trusted_iteration,
                        expected_digest=(draft.content_digest if draft else None),
                    ),
                )
            )
        except SkillCreatorError:
            raise
        except Exception as exc:
            raise SkillCreatorValidationError(
                "The dedicated Skill Creator agent could not create a proposal.",
                code="skill_creator_generation_failed",
            ) from exc
        if not isinstance(result, CreatorGenerationResult):
            raise SkillCreatorValidationError(
                "Creator generator returned an invalid typed result.",
                code="skill_creator_generation_invalid",
            )
        if result.tool_name != allowed_tool:
            raise SkillCreatorValidationError(
                "Creator agent did not call the required Skill proposal tool.",
                code="skill_creator_tool_not_called",
            )
        if not result.runtime_run_id or not result.runtime_task_id:
            raise SkillCreatorValidationError(
                "Creator agent did not return a trusted workflow run binding.",
                code="skill_creator_proposal_binding_invalid",
            )
        try:
            proposal = self.authoring_service.proposal_store.require(
                result.proposal_id
            )
        except AuthoringProposalError as exc:
            raise SkillCreatorValidationError(
                "Creator agent did not produce a persisted Skill proposal.",
                code="skill_creator_proposal_binding_invalid",
            ) from exc
        expected_kind = "skill_update" if draft else "skill_create"
        if (
            proposal.status != "pending"
            or proposal.kind != expected_kind
            or proposal.creator_session_id != session.session_id
            or proposal.creator_session_revision != expected_session_revision
            or proposal.source_type != "skill_creator"
            or proposal.source_id != session.session_id
            or proposal.actor_kind != "workflow_agent"
            or proposal.actor_id != CREATOR_ASSISTANT_AGENT_ID
            or proposal.source_run_id != result.runtime_run_id
            or proposal.source_task_id != result.runtime_task_id
            or (draft is not None and proposal.target_id != draft.draft_id)
            or (draft is not None and proposal.base_revision != draft.revision)
            or (draft is not None and proposal.base_digest != draft.content_digest)
        ):
            self._mark_generated_proposal_conflict(
                proposal,
                "Creator proposal is not bound to the frozen session and draft.",
            )
            raise SkillCreatorValidationError(
                "Creator proposal is not bound to the frozen session and draft.",
                code="skill_creator_proposal_binding_invalid",
            )
        proposal = self.authoring_service.validate(
            proposal.proposal_id,
            revision=proposal.revision,
        )
        if not bool(proposal.validation.get("valid", False)):
            self.authoring_service.proposal_store.transition(
                proposal.proposal_id,
                revision=proposal.revision,
                status="conflict",
                actor_kind="workflow_agent",
                actor_id=CREATOR_ASSISTANT_AGENT_ID,
                error="Creator generated an invalid Skill package.",
                decision_reason="Rejected invalid Creator generation output.",
            )
            raise SkillCreatorValidationError(
                "The dedicated Skill Creator agent generated an invalid Skill package.",
                code="skill_creator_package_invalid",
            )
        try:
            self.session_store.bind_proposal(
                session.session_id,
                expected_session_revision=expected_session_revision,
                proposal_id=proposal.proposal_id,
            )
        except SkillCreatorConflictError:
            self._mark_generated_proposal_conflict(
                proposal,
                "Creator session changed before the generated proposal could be bound.",
            )
            raise
        return proposal

    @staticmethod
    def _normalize_trusted_iteration(
        value: dict[str, Any] | None,
        *,
        expected_digest: str | None,
    ) -> dict[str, Any] | None:
        if value is None:
            return None
        if not isinstance(value, dict) or not expected_digest:
            raise SkillCreatorValidationError(
                "Creator iteration context is invalid.",
                code="skill_creator_iteration_context_invalid",
            )
        run_id = str(value.get("evaluation_run_id") or "").strip()
        review_id = str(value.get("review_id") or "").strip()
        digest = str(value.get("evaluated_digest") or "").strip().lower()
        feedback = str(value.get("feedback") or "").strip()
        if (
            not run_id
            or not review_id
            or digest != expected_digest.lower()
            or not feedback
            or len(feedback) > 4_000
        ):
            raise SkillCreatorValidationError(
                "Creator iteration context no longer matches the reviewed draft.",
                code="skill_creator_iteration_context_invalid",
            )
        return {
            "evaluation_run_id": run_id[:200],
            "review_id": review_id[:200],
            "evaluated_digest": digest,
            "feedback": feedback,
        }

    def _mark_generated_proposal_conflict(
        self,
        proposal: AuthoringProposal,
        reason: str,
    ) -> None:
        try:
            current = self.authoring_service.proposal_store.require(
                proposal.proposal_id
            )
            if current.status != "pending":
                return
            self.authoring_service.proposal_store.transition(
                current.proposal_id,
                revision=current.revision,
                status="conflict",
                actor_kind="workflow_agent",
                actor_id=CREATOR_ASSISTANT_AGENT_ID,
                error=reason,
                decision_reason="Rejected stale Creator generation output.",
            )
        except AuthoringProposalError:
            return

    def _generation_lock(self, session_id: str) -> asyncio.Lock:
        clean_session_id = str(session_id or "").strip()
        with self._generation_locks_guard:
            lock = self._generation_locks.get(clean_session_id)
            if lock is None:
                lock = asyncio.Lock()
                self._generation_locks[clean_session_id] = lock
            return lock

    def serialize_draft(self, draft: WorkspaceSkillDraft) -> dict[str, Any]:
        data = WorkspaceSkillDraftStore.serialize(draft, include_content=True)
        result = validate_skill_package(
            root_name=draft.slug,
            skill_markdown=draft.skill_markdown,
            files=draft.files,
        )
        data["validation"] = result.to_dict()
        data["frontmatter"] = (
            {
                "name": result.package.name,
                "description": result.package.description,
                "license": result.package.license,
                "compatibility": result.package.compatibility,
                "metadata": result.package.metadata or {},
                "allowed_tools": list(result.package.allowed_tools),
            }
            if result.package is not None
            else None
        )
        return data

    def _bind_existing_draft(
        self, session: SkillCreatorSession, draft_id: str
    ) -> SkillCreatorSession:
        draft = self.draft_store.require(draft_id)
        return self.session_store.bind_draft(
            session.session_id,
            draft_id=draft.draft_id,
            draft_state_revision=draft.revision,
            content_revision=draft.content_revision,
            content_digest=draft.content_digest,
        )

    def _cancel_pending_proposal(self, session: SkillCreatorSession) -> None:
        if not session.proposal_id:
            return
        try:
            proposal = self.authoring_service.proposal_store.require(session.proposal_id)
        except Exception:
            return
        if proposal.status == "pending":
            self.authoring_service.cancel(
                proposal.proposal_id, revision=proposal.revision
            )

    def _executor(self) -> CreatorGenerationExecutor | None:
        return self.generation_executor or get_creator_generation_executor()

    @staticmethod
    def _is_session_proposal(
        proposal: AuthoringProposal, session: SkillCreatorSession
    ) -> bool:
        if (
            proposal.kind not in {"skill_create", "skill_update"}
            or proposal.creator_session_id != session.session_id
            or proposal.source_type != "skill_creator"
            or proposal.source_id != session.session_id
        ):
            return False
        if proposal.status == "pending":
            return (
                proposal.actor_kind == "workflow_agent"
                and proposal.actor_id == CREATOR_ASSISTANT_AGENT_ID
            )
        if proposal.status == "approved":
            return bool(
                proposal.applied_resource_id
                and proposal.applied_resource_revision
                and proposal.applied_content_digest
                and proposal.actor_kind == "local_console"
            )
        if proposal.status in {"rejected", "cancelled", "conflict"}:
            return proposal.actor_kind == "local_console"
        return False

    @staticmethod
    def _require_session_revision(
        session: SkillCreatorSession, expected_session_revision: int
    ) -> None:
        if session.session_revision != expected_session_revision:
            raise SkillCreatorConflictError(
                "Creator session changed. Reload it before saving."
            )

    @staticmethod
    def _require_ready_for_draft(session: SkillCreatorSession) -> None:
        if not (
            session.intent
            and session.expected_output
            and session.success_criteria
            and session.evidence_confirmed
        ):
            raise SkillCreatorValidationError(
                "Confirm the purpose, expected output, success criteria, and evidence first.",
                code="skill_creator_definition_incomplete",
            )

    @staticmethod
    def _require_ready_for_generation(session: SkillCreatorSession) -> None:
        missing: list[str] = []
        if not session.intent:
            missing.append("intent")
        if not session.positive_examples:
            missing.append("positive_examples")
        if not session.near_miss_examples:
            missing.append("near_miss_examples")
        if not session.expected_output:
            missing.append("expected_output")
        if not session.success_criteria:
            missing.append("success_criteria")
        if not session.evidence_confirmed:
            missing.append("evidence")
        if missing:
            raise SkillCreatorValidationError(
                "AI generation requires a purpose, a positive example, a near-miss "
                "boundary, an output contract, success criteria, and confirmed evidence. "
                f"Missing: {', '.join(missing)}.",
                code="skill_creator_generation_definition_incomplete",
            )

    @staticmethod
    def _blank_preview_fingerprint(session: SkillCreatorSession) -> str:
        payload = {
            "session_id": session.session_id,
            "session_revision": session.session_revision,
            "intent": session.intent,
            "positive_examples": session.positive_examples,
            "near_miss_examples": session.near_miss_examples,
            "expected_output": session.expected_output,
            "success_criteria": session.success_criteria,
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _blank_skill_markdown(
        *,
        skill_id: str,
        description: str,
        intent: str,
        positive_examples: list[str],
        near_miss_examples: list[str],
        expected_output: str,
        success_criteria: list[str],
    ) -> str:
        clean_description = str(description or "").strip()
        positive = "\n".join(f"- {item}" for item in positive_examples) or "- TODO: Add a concrete request that should trigger this Skill."
        near_miss = "\n".join(f"- {item}" for item in near_miss_examples) or "- TODO: Add a similar request that must not trigger this Skill."
        criteria = "\n".join(f"- {item}" for item in success_criteria)
        return (
            "---\n"
            f"name: {skill_id}\n"
            f"description: {json.dumps(clean_description, ensure_ascii=False)}\n"
            "---\n\n"
            f"# {skill_id}\n\n"
            "<!-- MODEL_MIRROR_MANUAL_SCAFFOLD: incomplete -->\n\n"
            "> This is a structured manual scaffold, not an evaluated Skill. Replace every "
            "TODO with task-specific, executable guidance before evaluation.\n\n"
            "## Purpose and scope\n\n"
            f"{intent.strip()}\n\n"
            "## Trigger boundaries\n\n"
            "Use this Skill for requests such as:\n\n"
            f"{positive}\n\n"
            "Do not use this Skill for near-miss requests such as:\n\n"
            f"{near_miss}\n\n"
            "## Inputs and preconditions\n\n"
            "- TODO: List the required input, accepted formats, and prerequisites.\n"
            "- TODO: State how to detect incomplete, ambiguous, or unsupported input.\n\n"
            "## Workflow\n\n"
            "1. TODO: Inspect and normalize the input without discarding source evidence.\n"
            "2. TODO: Perform the repeatable task using explicit decision rules.\n"
            "3. TODO: Validate the result against the quality checks below.\n"
            "4. TODO: Produce the output contract and clearly mark unresolved gaps.\n\n"
            "## Output contract\n\n"
            f"{expected_output.strip()}\n\n"
            "TODO: Define required fields, ordering, formats, and missing-value markers.\n\n"
            "## Quality checks\n\n"
            f"{criteria}\n\n"
            "## Failure and degradation\n\n"
            "- TODO: Fail closed when required evidence or dependencies are unavailable.\n"
            "- TODO: Explain what partial output is safe, and never invent missing facts.\n\n"
            "## Resources\n\n"
            "Add only reusable UTF-8 text resources under `references/`, `scripts/`, or "
            "`assets/`, and link every resource from the workflow step that uses it.\n"
        )
