from __future__ import annotations

import hashlib
import hmac
import json
import os
import posixpath
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Literal, TypeVar

from .creator_quality import evaluate_creator_final_package
from .package_validation import (
    VALIDATOR_VERSION as PACKAGE_VALIDATOR_VERSION,
    compute_package_digest,
    scan_skill_package_credentials,
    validate_skill_package,
)


SkillDraftStatus = Literal["draft", "installed", "archived"]
SkillDraftInstallState = Literal["not_installed", "current", "outdated"]
SkillQualityStatus = Literal[
    "not_evaluated",
    "running",
    "accepted",
    "eval_waived",
    "outdated",
]
InstallResultT = TypeVar("InstallResultT")


class SkillDraftError(Exception):
    """Base error for workspace Skill drafts."""


class SkillDraftNotFoundError(SkillDraftError):
    pass


class SkillDraftConflictError(SkillDraftError):
    pass


class SkillDraftValidationError(SkillDraftError):
    pass


class SkillDraftStorageError(SkillDraftError):
    pass


@dataclass(slots=True, frozen=True)
class SkillDraftRevision:
    """Immutable content snapshot for one workspace Skill draft revision."""

    draft_id: str
    revision: int
    content_digest: str
    package: dict[str, Any]
    source_proposal_id: str | None = None
    source_apply_key: str | None = None
    created_at: float = field(default_factory=time.time)


@dataclass(slots=True, frozen=True)
class SkillProposalApplyReceipt:
    proposal_id: str
    apply_key: str
    draft_id: str
    content_revision: int
    content_digest: str
    created_at: float = field(default_factory=time.time)


@dataclass(slots=True, frozen=True)
class SkillQualityDecision:
    status: Literal["running", "accepted", "eval_waived"]
    content_revision: int
    content_digest: str
    run_id: str | None = None
    decision_id: str | None = None
    actor_kind: str | None = None
    actor_id: str | None = None
    reason: str | None = None
    decided_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class WorkspaceSkillDraft:
    draft_id: str
    name: str
    slug: str
    description: str
    skill_markdown: str
    files: dict[str, str] = field(default_factory=dict)
    status: SkillDraftStatus = "draft"
    revision: int = 1
    content_revision: int = 1
    content_digest: str = ""
    source_proposal_id: str | None = None
    source_apply_key: str | None = None
    creator_session_id: str | None = None
    experience_candidate_id: str | None = None
    predecessor_draft_id: str | None = None
    update_target_skill_id: str | None = None
    update_expected_version_id: str | None = None
    update_expected_content_digest: str | None = None
    quality_required: bool = False
    quality_status: SkillQualityStatus = "not_evaluated"
    quality_decision: SkillQualityDecision | None = None
    installed_skill_id: str | None = None
    installed_content_revision: int | None = None
    installed_content_digest: str | None = None
    install_state: SkillDraftInstallState = "not_installed"
    needs_review: bool = False
    validation: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class WorkspaceSkillDraftStore:
    """File-backed, reviewable Skill packages that are not installed by default."""

    SCHEMA_VERSION = 2
    VALIDATOR_VERSION = PACKAGE_VALIDATOR_VERSION
    MAX_FILES = 40
    MAX_FILE_BYTES = 1024 * 1024
    MAX_TOTAL_BYTES = 5 * 1024 * 1024
    ALLOWED_ROOTS = {"scripts", "references", "assets", "agents", "hooks"}

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        package_dir = Path(__file__).resolve().parent
        runtime_dir = os.getenv("AGENT_TASK_STORAGE_DIR", "").strip()
        self.storage_dir = Path(
            storage_dir or runtime_dir or package_dir / "storage"
        )
        self.snapshot_path = self.storage_dir / "skill_drafts.json"
        self.backup_path = self.storage_dir / "skill_drafts.v1.backup.json"
        self._lock = threading.RLock()
        self._items: dict[str, WorkspaceSkillDraft] = {}
        self._revisions: dict[str, dict[int, SkillDraftRevision]] = {}
        self._proposal_receipts: dict[str, SkillProposalApplyReceipt] = {}
        self._quarantine: list[dict[str, Any]] = []
        self._load_error: str | None = None
        self._load()

    def create(
        self,
        *,
        name: str,
        slug: str,
        description: str,
        skill_markdown: str,
        files: dict[str, str] | None = None,
        source_proposal_id: str | None = None,
        source_apply_key: str | None = None,
        creator_session_id: str | None = None,
        experience_candidate_id: str | None = None,
        predecessor_draft_id: str | None = None,
        update_target_skill_id: str | None = None,
        update_expected_version_id: str | None = None,
        update_expected_content_digest: str | None = None,
        quality_required: bool = False,
        quality_status: SkillQualityStatus = "not_evaluated",
    ) -> WorkspaceSkillDraft:
        self._validate_quality_status(quality_status)
        normalized = self.validate_package(
            name=name,
            slug=slug,
            description=description,
            skill_markdown=skill_markdown,
            files=files or {},
        )
        digest = self.compute_content_digest(**normalized)
        update_projection = self._normalize_update_projection(
            experience_candidate_id=experience_candidate_id,
            predecessor_draft_id=predecessor_draft_id,
            update_target_skill_id=update_target_skill_id,
            update_expected_version_id=update_expected_version_id,
            update_expected_content_digest=update_expected_content_digest,
        )
        now = time.time()
        item = WorkspaceSkillDraft(
            draft_id=f"skilldraft_{uuid.uuid4().hex}",
            source_proposal_id=source_proposal_id,
            source_apply_key=source_apply_key,
            creator_session_id=creator_session_id,
            **update_projection,
            quality_required=bool(quality_required),
            quality_status=quality_status,
            content_digest=digest,
            created_at=now,
            updated_at=now,
            **normalized,
        )
        snapshot = self._snapshot_from_item(item, created_at=now)
        with self._lock:
            self._ensure_writable_unlocked()
            self._items[item.draft_id] = item
            self._revisions[item.draft_id] = {item.content_revision: snapshot}
            try:
                self._save_unlocked()
            except BaseException:
                self._items.pop(item.draft_id, None)
                self._revisions.pop(item.draft_id, None)
                raise
        return self._copy(item)

    def require(self, draft_id: str) -> WorkspaceSkillDraft:
        with self._lock:
            item = self._items.get(draft_id)
            if item is None:
                raise SkillDraftNotFoundError(f"Skill draft not found: {draft_id}")
            return self._copy(item)

    def find_by_creator_session(
        self, creator_session_id: str
    ) -> WorkspaceSkillDraft | None:
        clean = str(creator_session_id or "").strip()
        if not clean:
            return None
        with self._lock:
            matches = [
                item
                for item in self._items.values()
                if item.creator_session_id == clean
            ]
            if len(matches) > 1:
                raise SkillDraftStorageError(
                    "Multiple Workspace drafts reference one Creator session."
                )
            return self._copy(matches[0]) if matches else None

    def create_creator_draft(
        self,
        *,
        creator_session_id: str,
        name: str,
        slug: str,
        description: str,
        skill_markdown: str,
        files: dict[str, str] | None = None,
        experience_candidate_id: str | None = None,
        predecessor_draft_id: str | None = None,
        update_target_skill_id: str | None = None,
        update_expected_version_id: str | None = None,
        update_expected_content_digest: str | None = None,
    ) -> WorkspaceSkillDraft:
        """Create or replay the one manual draft owned by a Creator session."""

        with self._lock:
            existing = self.find_by_creator_session(creator_session_id)
            if existing is not None:
                normalized = self.validate_package(
                    name=name,
                    slug=slug,
                    description=description,
                    skill_markdown=skill_markdown,
                    files=files or {},
                )
                expected_digest = self.compute_content_digest(**normalized)
                first_snapshot = self._revisions.get(existing.draft_id, {}).get(1)
                expected_projection = self._normalize_update_projection(
                    experience_candidate_id=experience_candidate_id,
                    predecessor_draft_id=predecessor_draft_id,
                    update_target_skill_id=update_target_skill_id,
                    update_expected_version_id=update_expected_version_id,
                    update_expected_content_digest=update_expected_content_digest,
                )
                if (
                    first_snapshot is None
                    or not self._digests_equal(
                        first_snapshot.content_digest, expected_digest
                    )
                    or any(
                        getattr(existing, field_name) != value
                        for field_name, value in expected_projection.items()
                    )
                ):
                    raise SkillDraftConflictError(
                        "Creator session already owns a different Skill draft."
                    )
                return existing
            return self.create(
                name=name,
                slug=slug,
                description=description,
                skill_markdown=skill_markdown,
                files=files or {},
                creator_session_id=creator_session_id,
                experience_candidate_id=experience_candidate_id,
                predecessor_draft_id=predecessor_draft_id,
                update_target_skill_id=update_target_skill_id,
                update_expected_version_id=update_expected_version_id,
                update_expected_content_digest=update_expected_content_digest,
                quality_required=True,
                quality_status="not_evaluated",
            )

    def require_revision_snapshot(
        self,
        draft_id: str,
        *,
        revision: int | None = None,
        content_digest: str | None = None,
    ) -> SkillDraftRevision:
        """Return a copy of an immutable content revision.

        When ``revision`` is omitted the current content revision is returned.
        ``content_digest`` is an optional optimistic consistency check.
        """

        with self._lock:
            item = self._require_unlocked(draft_id)
            selected_revision = item.content_revision if revision is None else int(revision)
            snapshot = self._revisions.get(draft_id, {}).get(selected_revision)
            if snapshot is None:
                raise SkillDraftNotFoundError(
                    f"Skill draft content revision not found: {draft_id}@{selected_revision}"
                )
            if content_digest is not None and not self._digests_equal(
                snapshot.content_digest, content_digest
            ):
                raise SkillDraftConflictError(
                    "Skill draft content changed. Reload it before applying this operation."
                )
            return self._copy_revision(snapshot)

    def list_revision_snapshots(self, draft_id: str) -> list[SkillDraftRevision]:
        with self._lock:
            self._require_unlocked(draft_id)
            snapshots = sorted(
                self._revisions.get(draft_id, {}).values(),
                key=lambda item: item.revision,
            )
            return [self._copy_revision(item) for item in snapshots]

    def list_quarantined(self) -> list[dict[str, Any]]:
        with self._lock:
            return self._json_copy(self._quarantine)

    def list(
        self, *, status: str | None = None, limit: int = 100
    ) -> list[WorkspaceSkillDraft]:
        with self._lock:
            items = list(self._items.values())
        if status:
            items = [item for item in items if item.status == status]
        items.sort(key=lambda item: item.updated_at, reverse=True)
        return [self._copy(item) for item in items[: max(1, min(limit, 500))]]

    def update(
        self,
        draft_id: str,
        *,
        revision: int | None = None,
        expected_revision: int | None = None,
        expected_digest: str | None = None,
        name: str | None = None,
        slug: str | None = None,
        description: str | None = None,
        skill_markdown: str | None = None,
        files: dict[str, str] | None = None,
        source_proposal_id: str | None = None,
        source_apply_key: str | None = None,
    ) -> WorkspaceSkillDraft:
        with self._lock:
            self._ensure_writable_unlocked()
            item = self._require_unlocked(draft_id)
            self._require_expected_state(
                item,
                revision=revision,
                expected_revision=expected_revision,
                expected_digest=expected_digest,
            )
            if item.status == "archived":
                raise SkillDraftConflictError("Archived Skill drafts cannot be edited.")
            previous = self._copy(item)
            previous_revisions = dict(self._revisions.get(item.draft_id, {}))
            normalized = self.validate_package(
                name=item.name if name is None else name,
                slug=item.slug if slug is None else slug,
                description=item.description if description is None else description,
                skill_markdown=(
                    item.skill_markdown if skill_markdown is None else skill_markdown
                ),
                files=item.files if files is None else files,
            )
            digest = self.compute_content_digest(**normalized)
            content_changed = not self._digests_equal(item.content_digest, digest)
            for key, value in normalized.items():
                setattr(item, key, value)
            if content_changed:
                previous_quality_status = item.quality_status
                item.content_revision += 1
                item.content_digest = digest
                item.source_proposal_id = source_proposal_id
                item.source_apply_key = source_apply_key
                item.validation = {}
                item.needs_review = False
                item.status = "draft"
                if item.quality_required:
                    item.quality_status = (
                        "outdated"
                        if previous_quality_status
                        in {"running", "accepted", "eval_waived", "outdated"}
                        else "not_evaluated"
                    )
                item.install_state = (
                    "outdated" if item.installed_skill_id else "not_installed"
                )
                self._revisions.setdefault(item.draft_id, {})[
                    item.content_revision
                ] = self._snapshot_from_item(item)
            item.revision += 1
            item.updated_at = time.time()
            try:
                self._save_unlocked()
            except BaseException:
                self._items[draft_id] = previous
                self._revisions[draft_id] = previous_revisions
                raise
            return self._copy(item)

    def set_validation(
        self,
        draft_id: str,
        *,
        revision: int | None = None,
        expected_revision: int | None = None,
        expected_digest: str | None = None,
        validation: dict[str, Any],
    ) -> WorkspaceSkillDraft:
        with self._lock:
            self._ensure_writable_unlocked()
            item = self._require_unlocked(draft_id)
            self._require_expected_state(
                item,
                revision=revision,
                expected_revision=expected_revision,
                expected_digest=expected_digest,
            )
            previous = self._copy(item)
            bound_validation = self._bound_validation(item, validation)
            item.validation = bound_validation
            item.needs_review = not bool(bound_validation.get("valid", False))
            item.updated_at = time.time()
            try:
                self._save_unlocked()
            except BaseException:
                self._items[draft_id] = previous
                raise
            return self._copy(item)

    def begin_evaluation(
        self,
        draft_id: str,
        *,
        expected_revision: int,
        expected_digest: str,
        run_id: str,
    ) -> WorkspaceSkillDraft:
        with self._lock:
            self._ensure_writable_unlocked()
            item = self._require_unlocked(draft_id)
            self._require_expected_state(
                item,
                revision=None,
                expected_revision=expected_revision,
                expected_digest=expected_digest,
            )
            if not item.quality_required:
                raise SkillDraftValidationError(
                    "Only Creator drafts use the evaluation quality gate."
                )
            self._require_creator_final_package(item)
            clean_run_id = self._quality_identifier(run_id, "run_id")
            if (
                item.quality_status == "running"
                and item.quality_decision is not None
                and item.quality_decision.run_id == clean_run_id
                and item.quality_decision.content_revision == item.content_revision
                and self._digests_equal(
                    item.quality_decision.content_digest, item.content_digest
                )
            ):
                return self._copy(item)
            validation = self._validate_item_unlocked(item)
            previous = self._copy(item)
            item.validation = self._bound_validation(item, validation)
            item.needs_review = False
            item.quality_status = "running"
            item.quality_decision = SkillQualityDecision(
                status="running",
                run_id=clean_run_id,
                content_revision=item.content_revision,
                content_digest=item.content_digest,
            )
            item.revision += 1
            item.updated_at = time.time()
            try:
                self._save_unlocked()
            except BaseException:
                self._items[draft_id] = previous
                raise
            return self._copy(item)

    def accept_evaluation(
        self,
        draft_id: str,
        *,
        expected_revision: int,
        expected_digest: str,
        run_id: str,
        decision_id: str,
        actor_id: str,
        reason: str | None = None,
    ) -> WorkspaceSkillDraft:
        return self._set_quality_decision(
            draft_id,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
            status="accepted",
            run_id=run_id,
            decision_id=decision_id,
            actor_id=actor_id,
            reason=reason,
        )

    def waive_evaluation(
        self,
        draft_id: str,
        *,
        expected_revision: int,
        expected_digest: str,
        decision_id: str,
        actor_id: str,
        reason: str,
    ) -> WorkspaceSkillDraft:
        return self._set_quality_decision(
            draft_id,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
            status="eval_waived",
            run_id=None,
            decision_id=decision_id,
            actor_id=actor_id,
            reason=reason,
        )

    def mark_quality_outdated(
        self,
        draft_id: str,
        *,
        expected_revision: int,
        expected_digest: str,
    ) -> WorkspaceSkillDraft:
        with self._lock:
            self._ensure_writable_unlocked()
            item = self._require_unlocked(draft_id)
            self._require_expected_state(
                item,
                revision=None,
                expected_revision=expected_revision,
                expected_digest=expected_digest,
            )
            if not item.quality_required or item.quality_status in {
                "not_evaluated",
                "outdated",
            }:
                return self._copy(item)
            previous = self._copy(item)
            item.quality_status = "outdated"
            item.revision += 1
            item.updated_at = time.time()
            try:
                self._save_unlocked()
            except BaseException:
                self._items[draft_id] = previous
                raise
            return self._copy(item)

    def _set_quality_decision(
        self,
        draft_id: str,
        *,
        expected_revision: int,
        expected_digest: str,
        status: Literal["accepted", "eval_waived"],
        run_id: str | None,
        decision_id: str,
        actor_id: str,
        reason: str | None,
    ) -> WorkspaceSkillDraft:
        with self._lock:
            self._ensure_writable_unlocked()
            item = self._require_unlocked(draft_id)
            self._require_expected_state(
                item,
                revision=None,
                expected_revision=expected_revision,
                expected_digest=expected_digest,
            )
            if not item.quality_required:
                raise SkillDraftValidationError(
                    "Only Creator drafts use the evaluation quality gate."
                )
            self._require_creator_final_package(item)
            clean_decision_id = self._quality_identifier(
                decision_id, "decision_id"
            )
            clean_run_id = (
                self._quality_identifier(run_id, "run_id")
                if run_id is not None
                else None
            )
            clean_actor_id = self._quality_identifier(actor_id, "actor_id")
            clean_reason = self._quality_reason(reason, required=status == "eval_waived")
            if status == "accepted" and not clean_run_id:
                raise SkillDraftValidationError(
                    "Accepted Creator quality decisions require an evaluation run."
                )
            if (
                item.quality_status == status
                and item.quality_decision is not None
                and item.quality_decision.decision_id == clean_decision_id
            ):
                return self._copy(item)
            validation = self._validate_item_unlocked(item)
            previous = self._copy(item)
            item.validation = self._bound_validation(item, validation)
            item.needs_review = False
            item.quality_status = status
            item.quality_decision = SkillQualityDecision(
                status=status,
                run_id=clean_run_id,
                decision_id=clean_decision_id,
                actor_kind="local_console",
                actor_id=clean_actor_id,
                reason=clean_reason,
                content_revision=item.content_revision,
                content_digest=item.content_digest,
            )
            item.revision += 1
            item.updated_at = time.time()
            try:
                self._save_unlocked()
            except BaseException:
                self._items[draft_id] = previous
                raise
            return self._copy(item)

    def mark_installed(
        self,
        draft_id: str,
        *,
        revision: int | None = None,
        expected_revision: int | None = None,
        expected_digest: str | None = None,
        skill_id: str,
    ) -> WorkspaceSkillDraft:
        with self._lock:
            self._ensure_writable_unlocked()
            item = self._require_unlocked(draft_id)
            self._require_expected_state(
                item,
                revision=revision,
                expected_revision=expected_revision,
                expected_digest=expected_digest,
            )
            previous = self._copy(item)
            self._require_quality_gate(item)
            item.status = "installed"
            item.installed_skill_id = str(skill_id).strip()
            item.installed_content_revision = item.content_revision
            item.installed_content_digest = item.content_digest
            item.install_state = "current"
            item.revision += 1
            item.updated_at = time.time()
            try:
                self._save_unlocked()
            except BaseException:
                self._items[draft_id] = previous
                raise
            return self._copy(item)

    def mark_uninstalled_skill(
        self, skill_id: str
    ) -> WorkspaceSkillDraft | None:
        """Clear a Workspace draft's install projection after global uninstall.

        This server-owned reconciliation intentionally does not accept a client
        revision.  A retry can repair the draft state after the filesystem was
        already removed but persistence failed.
        """

        normalized_skill_id = str(skill_id or "").strip()
        if not normalized_skill_id:
            return None
        with self._lock:
            self._ensure_writable_unlocked()
            matches = [
                item
                for item in self._items.values()
                if item.installed_skill_id == normalized_skill_id
            ]
            if not matches:
                return None
            previous = {item.draft_id: self._copy(item) for item in matches}
            now = time.time()
            for item in matches:
                item.installed_skill_id = None
                item.installed_content_revision = None
                item.installed_content_digest = None
                item.install_state = "not_installed"
                if item.status != "archived":
                    item.status = "draft"
                item.revision += 1
                item.updated_at = now
            try:
                self._save_unlocked()
            except BaseException:
                self._items.update(previous)
                raise
            return self._copy(matches[-1])

    def mark_lifecycle_version_installed(
        self,
        draft_id: str,
        *,
        content_revision: int,
        content_digest: str,
        skill_id: str,
    ) -> WorkspaceSkillDraft:
        """Project a server-verified historical lifecycle version as installed."""

        with self._lock:
            self._ensure_writable_unlocked()
            item = self._require_unlocked(draft_id)
            snapshot = self._revisions.get(draft_id, {}).get(content_revision)
            if snapshot is None or not self._digests_equal(
                snapshot.content_digest, content_digest
            ):
                raise SkillDraftConflictError(
                    "Historical Skill draft revision is unavailable."
                )
            install_state = (
                "current"
                if snapshot.revision == item.content_revision
                and self._digests_equal(snapshot.content_digest, item.content_digest)
                else "outdated"
            )
            affected = [
                candidate
                for candidate in self._items.values()
                if candidate.installed_skill_id == str(skill_id).strip()
                and candidate.draft_id != item.draft_id
            ]
            if (
                item.installed_skill_id == str(skill_id).strip()
                and item.installed_content_revision == snapshot.revision
                and self._digests_equal(
                    item.installed_content_digest or "", snapshot.content_digest
                )
                and item.install_state == install_state
                and item.status == "installed"
                and all(candidate.install_state == "outdated" for candidate in affected)
            ):
                return self._copy(item)
            previous = {
                candidate.draft_id: self._copy(candidate)
                for candidate in [item, *affected]
            }
            now = time.time()
            for candidate in affected:
                candidate.install_state = "outdated"
                candidate.revision += 1
                candidate.updated_at = now
            item.installed_skill_id = str(skill_id).strip()
            item.installed_content_revision = snapshot.revision
            item.installed_content_digest = snapshot.content_digest
            item.install_state = install_state
            item.status = "installed"
            item.revision += 1
            item.updated_at = now
            try:
                self._save_unlocked()
            except BaseException:
                self._items.update(previous)
                raise
            return self._copy(item)

    def install_current(
        self,
        draft_id: str,
        *,
        expected_revision: int,
        expected_digest: str,
        installer: Callable[[WorkspaceSkillDraft], InstallResultT],
    ) -> tuple[WorkspaceSkillDraft, InstallResultT]:
        """Validate and install one immutable draft state under the store lock.

        Holding the lock across the installer callback prevents a concurrent draft
        edit from landing between validation and the global filesystem install.
        The callback receives a detached copy and must return an object exposing
        ``skill_id`` and ``content_digest``.  If saving the installed marker fails,
        the in-memory draft is restored; the installer's receipt makes a retry
        idempotent.
        """

        with self._lock:
            self._ensure_writable_unlocked()
            item = self._require_unlocked(draft_id)
            self._require_expected_state(
                item,
                revision=None,
                expected_revision=expected_revision,
                expected_digest=expected_digest,
            )
            if item.status == "archived":
                raise SkillDraftConflictError("Archived Skill drafts cannot be installed.")
            self._require_quality_gate(item)

            validation = self._validate_item_unlocked(item)
            install_result = installer(self._copy(item))
            installed_skill_id = str(getattr(install_result, "skill_id", "")).strip()
            installed_digest = str(
                getattr(install_result, "content_digest", "")
            ).strip()
            if not installed_skill_id:
                raise SkillDraftStorageError(
                    "Workspace Skill installer did not return a stable skill ID."
                )
            if not self._digests_equal(installed_digest, item.content_digest):
                raise SkillDraftStorageError(
                    "Installed Workspace Skill digest does not match the reviewed draft."
                )

            superseded = [
                candidate
                for candidate in self._items.values()
                if candidate.draft_id != item.draft_id
                and candidate.installed_skill_id == installed_skill_id
            ]
            previous = {
                candidate.draft_id: self._copy(candidate)
                for candidate in [item, *superseded]
            }
            try:
                now = time.time()
                for candidate in superseded:
                    candidate.install_state = "outdated"
                    candidate.revision += 1
                    candidate.updated_at = now
                item.validation = self._bound_validation(item, validation)
                item.needs_review = False
                item.status = "installed"
                item.installed_skill_id = installed_skill_id
                item.installed_content_revision = item.content_revision
                item.installed_content_digest = item.content_digest
                item.install_state = "current"
                item.revision += 1
                item.updated_at = now
                self._save_unlocked()
            except BaseException:
                self._items.update(previous)
                raise
            return self._copy(item), install_result

    def archive(
        self,
        draft_id: str,
        *,
        revision: int | None = None,
        expected_revision: int | None = None,
        expected_digest: str | None = None,
    ) -> WorkspaceSkillDraft:
        with self._lock:
            self._ensure_writable_unlocked()
            item = self._require_unlocked(draft_id)
            self._require_expected_state(
                item,
                revision=revision,
                expected_revision=expected_revision,
                expected_digest=expected_digest,
            )
            previous = self._copy(item)
            item.status = "archived"
            item.revision += 1
            item.updated_at = time.time()
            try:
                self._save_unlocked()
            except BaseException:
                self._items[draft_id] = previous
                raise
            return self._copy(item)

    def validate_draft(self, draft_id: str) -> dict[str, Any]:
        item = self.require(draft_id)
        return self._validate_item_unlocked(item)

    def find_applied_proposal(
        self, proposal_id: str, apply_key: str
    ) -> WorkspaceSkillDraft | None:
        """Return the draft revision already produced by one proposal application."""

        clean_proposal_id = str(proposal_id or "").strip()
        clean_apply_key = str(apply_key or "").strip()
        if not clean_proposal_id or not clean_apply_key:
            return None
        with self._lock:
            receipt = self._proposal_receipts.get(clean_apply_key)
            if receipt is not None:
                if receipt.proposal_id != clean_proposal_id:
                    raise SkillDraftConflictError(
                        "Proposal apply key is already bound to another proposal."
                    )
                return self._copy(self._require_unlocked(receipt.draft_id))
            for draft_id, snapshots in self._revisions.items():
                for snapshot in snapshots.values():
                    if (
                        snapshot.source_proposal_id == clean_proposal_id
                        and snapshot.source_apply_key == clean_apply_key
                    ):
                        return self._copy(self._require_unlocked(draft_id))
        return None

    def require_proposal_receipt(
        self, proposal_id: str, apply_key: str
    ) -> SkillProposalApplyReceipt | None:
        clean_proposal_id = str(proposal_id or "").strip()
        clean_apply_key = str(apply_key or "").strip()
        if not clean_proposal_id or not clean_apply_key:
            return None
        with self._lock:
            receipt = self._proposal_receipts.get(clean_apply_key)
            if receipt is not None:
                if receipt.proposal_id != clean_proposal_id:
                    raise SkillDraftConflictError(
                        "Proposal apply key is already bound to another proposal."
                    )
                return receipt
            for draft_id, snapshots in self._revisions.items():
                for snapshot in snapshots.values():
                    if (
                        snapshot.source_proposal_id == clean_proposal_id
                        and snapshot.source_apply_key == clean_apply_key
                    ):
                        receipt = SkillProposalApplyReceipt(
                            proposal_id=clean_proposal_id,
                            apply_key=clean_apply_key,
                            draft_id=draft_id,
                            content_revision=snapshot.revision,
                            content_digest=snapshot.content_digest,
                            created_at=snapshot.created_at,
                        )
                        return receipt
        return None

    def apply_proposal_create(
        self,
        *,
        proposal_id: str,
        apply_key: str,
        name: str,
        slug: str,
        description: str,
        skill_markdown: str,
        files: dict[str, str] | None = None,
        creator_session_id: str | None = None,
    ) -> WorkspaceSkillDraft:
        """Create a proposal-backed draft exactly once."""

        with self._lock:
            expected = self.compute_content_digest(
                **self.validate_package(
                    name=name,
                    slug=slug,
                    description=description,
                    skill_markdown=skill_markdown,
                    files=files or {},
                )
            )
            receipt = self.require_proposal_receipt(proposal_id, apply_key)
            if receipt is not None:
                if not self._digests_equal(receipt.content_digest, expected):
                    raise SkillDraftConflictError(
                        "Proposal apply key is already bound to different Skill content."
                    )
                self._persist_recovered_receipt_unlocked(receipt)
                return self._copy(self._require_unlocked(receipt.draft_id))
            item = self.create(
                name=name,
                slug=slug,
                description=description,
                skill_markdown=skill_markdown,
                files=files or {},
                source_proposal_id=proposal_id,
                source_apply_key=apply_key,
                creator_session_id=creator_session_id,
                quality_required=bool(creator_session_id),
                quality_status="not_evaluated",
            )
            self._persist_recovered_receipt_unlocked(
                SkillProposalApplyReceipt(
                    proposal_id=proposal_id,
                    apply_key=apply_key,
                    draft_id=item.draft_id,
                    content_revision=item.content_revision,
                    content_digest=item.content_digest,
                )
            )
            return item

    def apply_proposal_update(
        self,
        draft_id: str,
        *,
        proposal_id: str,
        apply_key: str,
        expected_revision: int,
        expected_digest: str,
        name: str | None = None,
        slug: str | None = None,
        description: str | None = None,
        skill_markdown: str | None = None,
        files: dict[str, str] | None = None,
    ) -> WorkspaceSkillDraft:
        """Apply a proposal update once and preserve revision provenance."""

        with self._lock:
            target = self._require_unlocked(draft_id)
            normalized = self.validate_package(
                name=target.name if name is None else name,
                slug=target.slug if slug is None else slug,
                description=target.description if description is None else description,
                skill_markdown=(
                    target.skill_markdown if skill_markdown is None else skill_markdown
                ),
                files=target.files if files is None else files,
            )
            expected_content_digest = self.compute_content_digest(**normalized)
            receipt = self.require_proposal_receipt(proposal_id, apply_key)
            if receipt is not None:
                if receipt.draft_id != draft_id:
                    raise SkillDraftConflictError(
                        "Proposal apply key is already bound to another Skill draft."
                    )
                if not self._digests_equal(
                    receipt.content_digest, expected_content_digest
                ):
                    raise SkillDraftConflictError(
                        "Proposal apply key is already bound to different Skill content."
                    )
                self._persist_recovered_receipt_unlocked(receipt)
                return self._copy(target)
            self._require_expected_state(
                target,
                revision=None,
                expected_revision=expected_revision,
                expected_digest=expected_digest,
            )
            if self._digests_equal(target.content_digest, expected_content_digest):
                receipt = SkillProposalApplyReceipt(
                    proposal_id=proposal_id,
                    apply_key=apply_key,
                    draft_id=draft_id,
                    content_revision=target.content_revision,
                    content_digest=target.content_digest,
                )
                self._persist_recovered_receipt_unlocked(receipt)
                return self._copy(target)
            item = self.update(
                draft_id,
                expected_revision=expected_revision,
                expected_digest=expected_digest,
                name=name,
                slug=slug,
                description=description,
                skill_markdown=skill_markdown,
                files=files,
                source_proposal_id=proposal_id,
                source_apply_key=apply_key,
            )
            self._persist_recovered_receipt_unlocked(
                SkillProposalApplyReceipt(
                    proposal_id=proposal_id,
                    apply_key=apply_key,
                    draft_id=draft_id,
                    content_revision=item.content_revision,
                    content_digest=item.content_digest,
                )
            )
            return item

    def _persist_recovered_receipt_unlocked(
        self, receipt: SkillProposalApplyReceipt
    ) -> None:
        current = self._proposal_receipts.get(receipt.apply_key)
        if current is not None:
            if current != receipt:
                raise SkillDraftConflictError(
                    "Proposal apply receipt conflicts with persisted state."
                )
            return
        self._proposal_receipts[receipt.apply_key] = receipt
        try:
            self._save_unlocked()
        except BaseException:
            self._proposal_receipts.pop(receipt.apply_key, None)
            raise

    def _validate_item_unlocked(self, item: WorkspaceSkillDraft) -> dict[str, Any]:
        self.validate_package(
            name=item.name,
            slug=item.slug,
            description=item.description,
            skill_markdown=item.skill_markdown,
            files=item.files,
        )
        result = validate_skill_package(
            root_name=item.slug,
            skill_markdown=item.skill_markdown,
            files=item.files,
        )
        validation = result.to_dict()
        validation.update(
            {
                "validator_version": self.VALIDATOR_VERSION,
                "content_revision": item.content_revision,
                "content_digest": item.content_digest,
                "stale": False,
            }
        )
        return validation

    def _bound_validation(
        self, item: WorkspaceSkillDraft, validation: dict[str, Any]
    ) -> dict[str, Any]:
        bound_validation = self._json_copy(dict(validation))
        bound_validation.update(
            {
                "validator_version": self.VALIDATOR_VERSION,
                "content_revision": item.content_revision,
                "content_digest": item.content_digest,
                "stale": False,
            }
        )
        return bound_validation

    @classmethod
    def validate_package(
        cls,
        *,
        name: str,
        slug: str,
        description: str,
        skill_markdown: str,
        files: dict[str, str],
    ) -> dict[str, Any]:
        result = validate_skill_package(
            root_name=slug,
            skill_markdown=skill_markdown,
            files=files,
        )
        if not result.valid or result.package is None:
            messages = [
                issue.message for issue in result.issues if issue.severity == "error"
            ]
            error = SkillDraftValidationError(
                "; ".join(messages[:5]) or "Skill package validation failed."
            )
            error.issues = [issue.to_dict() for issue in result.issues]  # type: ignore[attr-defined]
            raise error
        package = result.package
        # ``name`` and ``description`` remain accepted for V1 callers, while
        # canonical V2 metadata is always derived from validated frontmatter.
        del name, description
        return {
            "name": package.name,
            "slug": package.root_name,
            "description": package.description,
            "skill_markdown": package.skill_markdown,
            "files": dict(package.files),
        }

    @classmethod
    def compute_content_digest(
        cls,
        *,
        name: str,
        slug: str,
        description: str,
        skill_markdown: str,
        files: dict[str, str],
    ) -> str:
        """Use the unified V2 package digest over canonical paths and raw bytes."""

        del name, slug, description
        return compute_package_digest(skill_markdown, files)

    @classmethod
    def _validate_path(cls, raw_path: Any) -> str:
        value = str(raw_path or "").replace("\\", "/").strip()
        normalized = posixpath.normpath(value)
        path = PurePosixPath(normalized)
        if (
            not value
            or value.startswith("/")
            or normalized in {".", ".."}
            or ".." in path.parts
            or any(part.startswith(".") for part in path.parts)
            or path.parts[0] not in cls.ALLOWED_ROOTS
        ):
            raise SkillDraftValidationError(f"Unsafe Skill file path: {value}")
        if path.parts[0] == "agents" and normalized != "agents/openai.yaml":
            raise SkillDraftValidationError(
                "Only agents/openai.yaml is supported under agents/."
            )
        if path.parts[0] == "hooks" and normalized != "hooks/manifest.json":
            raise SkillDraftValidationError(
                "Only hooks/manifest.json is supported under hooks/."
            )
        return normalized

    @staticmethod
    def _parse_frontmatter(content: str) -> dict[str, str]:
        if not content.startswith("---"):
            return {}
        values: dict[str, str] = {}
        for line in content.splitlines()[1:]:
            if line.strip() == "---":
                break
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            if key.strip().lower() in {"name", "description"}:
                values[key.strip().lower()] = value.strip().strip('"').strip("'")
        return values

    @staticmethod
    def serialize(
        item: WorkspaceSkillDraft, *, include_content: bool = False
    ) -> dict[str, Any]:
        data = asdict(item)
        if not include_content:
            data.pop("skill_markdown", None)
            data.pop("files", None)
            data["file_count"] = 1 + len(item.files)
            data["total_bytes"] = WorkspaceSkillDraftStore._total_bytes(
                item.skill_markdown, item.files
            )
            data["file_paths"] = ["SKILL.md", *sorted(item.files)]
        return data

    @staticmethod
    def _total_bytes(markdown: str, files: dict[str, str]) -> int:
        return len(markdown.encode("utf-8")) + sum(
            len(value.encode("utf-8")) for value in files.values()
        )

    def _snapshot_from_item(
        self, item: WorkspaceSkillDraft, *, created_at: float | None = None
    ) -> SkillDraftRevision:
        return SkillDraftRevision(
            draft_id=item.draft_id,
            revision=item.content_revision,
            content_digest=item.content_digest,
            package=self._package_from_item(item),
            source_proposal_id=item.source_proposal_id,
            source_apply_key=item.source_apply_key,
            created_at=time.time() if created_at is None else created_at,
        )

    @staticmethod
    def _package_from_item(item: WorkspaceSkillDraft) -> dict[str, Any]:
        return {
            "name": item.name,
            "slug": item.slug,
            "description": item.description,
            "skill_markdown": item.skill_markdown,
            "files": dict(item.files),
        }

    def _require_unlocked(self, draft_id: str) -> WorkspaceSkillDraft:
        item = self._items.get(draft_id)
        if item is None:
            raise SkillDraftNotFoundError(f"Skill draft not found: {draft_id}")
        return item

    @staticmethod
    def _validate_quality_status(value: str) -> None:
        if value not in {
            "not_evaluated",
            "running",
            "accepted",
            "eval_waived",
            "outdated",
        }:
            raise SkillDraftValidationError(f"Invalid Skill quality status: {value}")

    @classmethod
    def _normalize_update_projection(
        cls,
        *,
        experience_candidate_id: str | None,
        predecessor_draft_id: str | None,
        update_target_skill_id: str | None,
        update_expected_version_id: str | None,
        update_expected_content_digest: str | None,
    ) -> dict[str, str | None]:
        projection = {
            "experience_candidate_id": cls._optional_text(experience_candidate_id),
            "predecessor_draft_id": cls._optional_text(predecessor_draft_id),
            "update_target_skill_id": cls._optional_text(update_target_skill_id),
            "update_expected_version_id": cls._optional_text(
                update_expected_version_id
            ),
            "update_expected_content_digest": cls._optional_text(
                update_expected_content_digest
            ),
        }
        update_fields = tuple(
            projection[name]
            for name in (
                "predecessor_draft_id",
                "update_target_skill_id",
                "update_expected_version_id",
                "update_expected_content_digest",
            )
        )
        if any(update_fields) and not all(update_fields):
            raise SkillDraftValidationError(
                "Workspace Skill update target metadata is incomplete."
            )
        if any(update_fields) and projection["experience_candidate_id"] is None:
            raise SkillDraftValidationError(
                "Workspace Skill update requires an experience candidate binding."
            )
        digest = projection["update_expected_content_digest"]
        if digest is not None:
            clean_digest = digest.lower()
            if len(clean_digest) != 64 or any(
                character not in "0123456789abcdef" for character in clean_digest
            ):
                raise SkillDraftValidationError(
                    "Workspace Skill update target digest is invalid."
                )
            projection["update_expected_content_digest"] = clean_digest
        return projection

    @staticmethod
    def _require_quality_gate(item: WorkspaceSkillDraft) -> None:
        WorkspaceSkillDraftStore._require_creator_final_package(item)
        decision = item.quality_decision
        decision_matches = bool(
            decision is not None
            and decision.status == item.quality_status
            and decision.content_revision == item.content_revision
            and WorkspaceSkillDraftStore._digests_equal(
                decision.content_digest, item.content_digest
            )
            and decision.actor_kind == "local_console"
            and decision.actor_id
            and decision.decision_id
            and (
                (item.quality_status == "accepted" and decision.run_id)
                or (
                    item.quality_status == "eval_waived"
                    and decision.reason
                )
            )
        )
        if item.quality_required and (
            item.quality_status not in {"accepted", "eval_waived"}
            or not decision_matches
        ):
            error = SkillDraftValidationError(
                "Creator Skill must pass or explicitly waive evaluation before installation."
            )
            error.issues = [  # type: ignore[attr-defined]
                {
                    "code": "skill_creator_quality_gate_required",
                    "severity": "error",
                    "field": "quality_status",
                    "message": str(error),
                }
            ]
            raise error

    @staticmethod
    def _require_creator_final_package(item: WorkspaceSkillDraft) -> None:
        if not item.quality_required:
            return
        report = evaluate_creator_final_package(
            root_name=item.slug,
            skill_markdown=item.skill_markdown,
            files=item.files,
        )
        if report.ready:
            return
        error = SkillDraftValidationError(
            "Creator Skill package is not complete enough for evaluation or installation."
        )
        error.issues = [issue.to_dict() for issue in report.issues]  # type: ignore[attr-defined]
        raise error

    @staticmethod
    def _quality_identifier(value: Any, field_name: str) -> str:
        clean = str(value or "").strip()
        if not clean or len(clean) > 200:
            raise SkillDraftValidationError(
                f"Invalid Creator quality {field_name}."
            )
        return clean

    @staticmethod
    def _quality_reason(value: Any, *, required: bool) -> str | None:
        if value is None:
            if required:
                raise SkillDraftValidationError(
                    "Evaluation waiver reason is required."
                )
            return None
        if not isinstance(value, str):
            raise SkillDraftValidationError("Quality decision reason must be text.")
        clean = value.strip()
        if required and not clean:
            raise SkillDraftValidationError(
                "Evaluation waiver reason is required."
            )
        if len(clean) > 4_000:
            raise SkillDraftValidationError("Quality decision reason is too long.")
        if clean and scan_skill_package_credentials(skill_markdown=clean):
            raise SkillDraftValidationError(
                "Quality decision reason contains blocked credential material."
            )
        return clean or None

    def _require_expected_state(
        self,
        item: WorkspaceSkillDraft,
        *,
        revision: int | None,
        expected_revision: int | None,
        expected_digest: str | None,
    ) -> None:
        if revision is not None and expected_revision is not None:
            if int(revision) != int(expected_revision):
                raise SkillDraftConflictError(
                    "Conflicting expected Skill draft revisions were supplied."
                )
        selected_revision = expected_revision if expected_revision is not None else revision
        if selected_revision is None:
            raise SkillDraftConflictError("Expected Skill draft revision is required.")
        self._require_revision(item, int(selected_revision))
        if expected_digest is not None and not self._digests_equal(
            item.content_digest, expected_digest
        ):
            raise SkillDraftConflictError(
                "Skill draft content changed. Reload it before applying this operation."
            )

    @staticmethod
    def _require_revision(item: WorkspaceSkillDraft, revision: int) -> None:
        if item.revision != revision:
            raise SkillDraftConflictError(
                "Skill draft changed. Reload it before applying this operation."
            )

    @staticmethod
    def _digests_equal(left: str, right: str) -> bool:
        return bool(left) and bool(right) and hmac.compare_digest(
            left.lower(), right.lower()
        )

    def _load(self) -> None:
        with self._lock:
            if not self.snapshot_path.exists():
                return
            try:
                raw_bytes = self.snapshot_path.read_bytes()
            except OSError as exc:
                self._load_error = f"Unable to read Skill draft storage: {exc}"
                return
            try:
                raw = json.loads(raw_bytes.decode("utf-8"))
            except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
                self._backup_original_unlocked(raw_bytes, corrupted=True)
                self._load_error = f"Skill draft storage is not valid UTF-8 JSON: {exc}"
                return
            if not isinstance(raw, dict) or not isinstance(raw.get("items", []), list):
                self._backup_original_unlocked(raw_bytes, corrupted=True)
                self._load_error = "Skill draft storage must contain an items array."
                return

            raw_version = raw.get("version", 1)
            try:
                version = int(raw_version)
            except (TypeError, ValueError):
                version = -1
            if version not in {1, self.SCHEMA_VERSION}:
                self._backup_original_unlocked(raw_bytes, corrupted=True)
                self._load_error = f"Unsupported Skill draft storage version: {raw_version}"
                return
            if version == 1:
                self._backup_original_unlocked(raw_bytes)

            migrated = version == 1
            existing_quarantine = raw.get("quarantine", [])
            if version == self.SCHEMA_VERSION and isinstance(existing_quarantine, list):
                for index, entry in enumerate(existing_quarantine):
                    safe_entry = self._sanitize_quarantine_entry(entry, index=index)
                    self._quarantine.append(safe_entry)
                    if not isinstance(entry, dict) or entry != safe_entry:
                        migrated = True
            for index, record in enumerate(raw.get("items", [])):
                if not isinstance(record, dict):
                    self._quarantine_record(
                        kind="item",
                        index=index,
                        reason_code="record_not_object",
                        reason="Skill draft record must be an object.",
                        record=record,
                    )
                    migrated = True
                    continue
                try:
                    item = self._decode_item(record, version=version)
                    if item.draft_id in self._items:
                        raise ValueError(f"Duplicate draft_id: {item.draft_id}")
                except (KeyError, TypeError, ValueError) as exc:
                    self._quarantine_record(
                        kind="item",
                        index=index,
                        reason_code="invalid_record",
                        reason=str(exc),
                        record=record,
                    )
                    migrated = True
                    continue
                self._items[item.draft_id] = item

            if version == self.SCHEMA_VERSION:
                revisions = raw.get("revisions", [])
                if not isinstance(revisions, list):
                    revisions = []
                    migrated = True
                for index, record in enumerate(revisions):
                    if not isinstance(record, dict):
                        self._quarantine_record(
                            kind="revision",
                            index=index,
                            reason_code="record_not_object",
                            reason="Skill revision record must be an object.",
                            record=record,
                        )
                        migrated = True
                        continue
                    try:
                        snapshot = self._decode_revision(record)
                        if snapshot.draft_id not in self._items:
                            raise ValueError(
                                f"Revision references missing draft_id: {snapshot.draft_id}"
                            )
                        existing = self._revisions.setdefault(snapshot.draft_id, {})
                        if snapshot.revision in existing:
                            raise ValueError(
                                f"Duplicate content revision: {snapshot.draft_id}@{snapshot.revision}"
                            )
                    except (KeyError, TypeError, ValueError) as exc:
                        self._quarantine_record(
                            kind="revision",
                            index=index,
                            reason_code="invalid_revision",
                            reason=str(exc),
                            record=record,
                        )
                        migrated = True
                        continue
                    existing[snapshot.revision] = snapshot

                receipts = raw.get("proposal_receipts", [])
                if not isinstance(receipts, list):
                    receipts = []
                    migrated = True
                for index, record in enumerate(receipts):
                    if not isinstance(record, dict):
                        self._quarantine_record(
                            kind="proposal_receipt",
                            index=index,
                            reason_code="record_not_object",
                            reason="Skill proposal receipt must be an object.",
                            record=record,
                        )
                        migrated = True
                        continue
                    try:
                        receipt = self._decode_proposal_receipt(record)
                        if receipt.draft_id not in self._items:
                            raise ValueError(
                                f"Receipt references missing draft_id: {receipt.draft_id}"
                            )
                        snapshot = self._revisions.get(receipt.draft_id, {}).get(
                            receipt.content_revision
                        )
                        if snapshot is None or not self._digests_equal(
                            snapshot.content_digest, receipt.content_digest
                        ):
                            raise ValueError(
                                "Receipt does not match an immutable Skill revision."
                            )
                        if receipt.apply_key in self._proposal_receipts:
                            raise ValueError(
                                f"Duplicate Skill proposal apply key: {receipt.apply_key}"
                            )
                    except (KeyError, TypeError, ValueError) as exc:
                        self._quarantine_record(
                            kind="proposal_receipt",
                            index=index,
                            reason_code="invalid_proposal_receipt",
                            reason=str(exc),
                            record=record,
                        )
                        migrated = True
                        continue
                    self._proposal_receipts[receipt.apply_key] = receipt

            for item in self._items.values():
                snapshots = self._revisions.setdefault(item.draft_id, {})
                current = snapshots.get(item.content_revision)
                if current is None or not self._digests_equal(
                    current.content_digest, item.content_digest
                ):
                    snapshots[item.content_revision] = self._snapshot_from_item(
                        item, created_at=item.updated_at
                    )
                    item.needs_review = item.needs_review or version == self.SCHEMA_VERSION
                    migrated = True

            if migrated:
                self._save_unlocked()

    def _decode_item(self, record: dict[str, Any], *, version: int) -> WorkspaceSkillDraft:
        draft_id = self._required_text(record, "draft_id")
        name = self._required_text(record, "name")
        slug = self._required_text(record, "slug")
        description = self._text(record.get("description", ""), "description")
        skill_markdown = self._required_text(record, "skill_markdown")
        files = self._text_files(record.get("files", {}))
        status = record.get("status", "draft")
        if status not in {"draft", "installed", "archived"}:
            raise ValueError(f"Invalid Skill draft status: {status}")
        revision = self._positive_int(record.get("revision", 1), "revision")
        content_revision = self._positive_int(
            record.get("content_revision", 1), "content_revision"
        )
        package = {
            "name": name,
            "slug": slug,
            "description": description,
            "skill_markdown": skill_markdown,
            "files": files,
        }
        self._reject_persisted_credentials(
            metadata=(name, slug, description),
            skill_markdown=skill_markdown,
            files=files,
        )
        actual_digest = self.compute_content_digest(**package)
        stored_digest = record.get("content_digest")
        if version == self.SCHEMA_VERSION:
            if not isinstance(stored_digest, str) or not self._digests_equal(
                stored_digest, actual_digest
            ):
                raise ValueError("Skill draft content_digest does not match its package.")

        installed_skill_id = self._optional_text(record.get("installed_skill_id"))
        installed_content_revision = self._optional_positive_int(
            record.get("installed_content_revision"), "installed_content_revision"
        )
        installed_content_digest = self._optional_text(
            record.get("installed_content_digest")
        )
        if version == 1:
            install_state: SkillDraftInstallState
            if installed_skill_id and status == "installed":
                install_state = "current"
                installed_content_revision = content_revision
                installed_content_digest = actual_digest
            elif installed_skill_id:
                install_state = "outdated"
            else:
                install_state = "not_installed"
        else:
            install_state = record.get("install_state", "not_installed")
            if install_state not in {"not_installed", "current", "outdated"}:
                raise ValueError(f"Invalid Skill install_state: {install_state}")

        validation = record.get("validation", {})
        if not isinstance(validation, dict):
            raise TypeError("Skill draft validation must be an object.")
        validation_credentials = scan_skill_package_credentials(
            skill_markdown=json.dumps(validation, ensure_ascii=False)
        )
        if validation_credentials:
            validation = {}
        needs_review = bool(record.get("needs_review", False)) or bool(
            validation_credentials
        )
        if version == 1 and validation:
            validation = self._json_copy(validation)
            validation.update(
                {
                    "validator_version": "legacy-v1-unbound",
                    "content_revision": content_revision,
                    "content_digest": actual_digest,
                    "stale": True,
                }
            )
            needs_review = True
        elif version == self.SCHEMA_VERSION and validation:
            validation = self._json_copy(validation)
            binding_matches = (
                validation.get("validator_version") == self.VALIDATOR_VERSION
                and validation.get("content_revision") == content_revision
                and self._digests_equal(
                    str(validation.get("content_digest") or ""), actual_digest
                )
                and validation.get("stale") is False
            )
            if not binding_matches:
                validation["stale"] = True
                needs_review = True
        try:
            self.validate_package(**package)
        except SkillDraftValidationError:
            needs_review = True

        created_at = self._timestamp(record.get("created_at", time.time()), "created_at")
        updated_at = self._timestamp(record.get("updated_at", created_at), "updated_at")
        quality_status = self._decode_quality_status(
            record.get("quality_status", "not_evaluated")
        )
        quality_decision = self._decode_quality_decision(
            record.get("quality_decision")
        )
        update_projection = self._normalize_update_projection(
            experience_candidate_id=record.get("experience_candidate_id"),
            predecessor_draft_id=record.get("predecessor_draft_id"),
            update_target_skill_id=record.get("update_target_skill_id"),
            update_expected_version_id=record.get("update_expected_version_id"),
            update_expected_content_digest=record.get(
                "update_expected_content_digest"
            ),
        )
        if quality_status in {"accepted", "eval_waived"}:
            decision_matches = bool(
                quality_decision is not None
                and quality_decision.status == quality_status
                and quality_decision.content_revision == content_revision
                and self._digests_equal(
                    quality_decision.content_digest, actual_digest
                )
                and quality_decision.actor_kind == "local_console"
                and quality_decision.actor_id
                and quality_decision.decision_id
            )
            if not decision_matches:
                quality_status = "outdated"

        return WorkspaceSkillDraft(
            draft_id=draft_id,
            name=name,
            slug=slug,
            description=description,
            skill_markdown=skill_markdown,
            files=files,
            status=status,
            revision=revision,
            content_revision=content_revision,
            content_digest=actual_digest,
            source_proposal_id=self._optional_text(record.get("source_proposal_id")),
            source_apply_key=self._optional_text(record.get("source_apply_key")),
            creator_session_id=self._optional_text(record.get("creator_session_id")),
            **update_projection,
            quality_required=bool(record.get("quality_required", False)),
            quality_status=quality_status,
            quality_decision=quality_decision,
            installed_skill_id=installed_skill_id,
            installed_content_revision=installed_content_revision,
            installed_content_digest=installed_content_digest,
            install_state=install_state,
            needs_review=needs_review,
            validation=self._json_copy(validation),
            created_at=created_at,
            updated_at=updated_at,
        )

    def _decode_revision(self, record: dict[str, Any]) -> SkillDraftRevision:
        draft_id = self._required_text(record, "draft_id")
        revision = self._positive_int(record.get("revision"), "revision")
        content_digest = self._required_text(record, "content_digest")
        package = record.get("package")
        if not isinstance(package, dict):
            raise TypeError("Skill revision package must be an object.")
        normalized_package = {
            "name": self._required_text(package, "name"),
            "slug": self._required_text(package, "slug"),
            "description": self._text(package.get("description", ""), "description"),
            "skill_markdown": self._required_text(package, "skill_markdown"),
            "files": self._text_files(package.get("files", {})),
        }
        self._reject_persisted_credentials(
            metadata=(
                normalized_package["name"],
                normalized_package["slug"],
                normalized_package["description"],
            ),
            skill_markdown=normalized_package["skill_markdown"],
            files=normalized_package["files"],
        )
        actual_digest = self.compute_content_digest(**normalized_package)
        if not self._digests_equal(content_digest, actual_digest):
            raise ValueError("Skill revision content_digest does not match its package.")
        return SkillDraftRevision(
            draft_id=draft_id,
            revision=revision,
            content_digest=actual_digest,
            package=normalized_package,
            source_proposal_id=self._optional_text(record.get("source_proposal_id")),
            source_apply_key=self._optional_text(record.get("source_apply_key")),
            created_at=self._timestamp(record.get("created_at", time.time()), "created_at"),
        )

    def _decode_proposal_receipt(
        self, record: dict[str, Any]
    ) -> SkillProposalApplyReceipt:
        content_digest = self._required_text(record, "content_digest").lower()
        if len(content_digest) != 64 or any(
            character not in "0123456789abcdef" for character in content_digest
        ):
            raise ValueError("Skill proposal receipt has an invalid content digest.")
        return SkillProposalApplyReceipt(
            proposal_id=self._required_text(record, "proposal_id"),
            apply_key=self._required_text(record, "apply_key"),
            draft_id=self._required_text(record, "draft_id"),
            content_revision=self._positive_int(
                record.get("content_revision"), "content_revision"
            ),
            content_digest=content_digest,
            created_at=self._timestamp(record.get("created_at", time.time()), "created_at"),
        )

    def _decode_quality_decision(
        self, record: Any
    ) -> SkillQualityDecision | None:
        if record is None:
            return None
        if not isinstance(record, dict):
            raise TypeError("Skill quality decision must be an object.")
        status = record.get("status")
        if status not in {"running", "accepted", "eval_waived"}:
            raise ValueError("Invalid Skill quality decision status.")
        content_digest = self._required_text(record, "content_digest").lower()
        if len(content_digest) != 64 or any(
            character not in "0123456789abcdef"
            for character in content_digest
        ):
            raise ValueError("Skill quality decision has an invalid digest.")
        reason = self._optional_text(record.get("reason"))
        if reason and scan_skill_package_credentials(skill_markdown=reason):
            raise ValueError("Skill quality decision contains blocked credentials.")
        return SkillQualityDecision(
            status=status,
            content_revision=self._positive_int(
                record.get("content_revision"), "content_revision"
            ),
            content_digest=content_digest,
            run_id=self._optional_text(record.get("run_id")),
            decision_id=self._optional_text(record.get("decision_id")),
            actor_kind=self._optional_text(record.get("actor_kind")),
            actor_id=self._optional_text(record.get("actor_id")),
            reason=reason,
            decided_at=self._timestamp(
                record.get("decided_at", time.time()), "decided_at"
            ),
        )

    @classmethod
    def _decode_quality_status(cls, value: Any) -> SkillQualityStatus:
        if not isinstance(value, str):
            raise TypeError("Skill quality_status must be text.")
        cls._validate_quality_status(value)
        return value  # type: ignore[return-value]

    @staticmethod
    def _required_text(record: dict[str, Any], key: str) -> str:
        if key not in record:
            raise KeyError(f"Missing required Skill draft field: {key}")
        return WorkspaceSkillDraftStore._text(record[key], key, required=True)

    @staticmethod
    def _text(value: Any, field_name: str, *, required: bool = False) -> str:
        if not isinstance(value, str):
            raise TypeError(f"Skill draft {field_name} must be text.")
        if required and not value.strip():
            raise ValueError(f"Skill draft {field_name} cannot be empty.")
        return value

    @staticmethod
    def _optional_text(value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("Optional Skill draft identifiers must be text.")
        clean = value.strip()
        return clean or None

    @staticmethod
    def _text_files(value: Any) -> dict[str, str]:
        if not isinstance(value, dict):
            raise TypeError("Skill draft files must be an object.")
        files: dict[str, str] = {}
        for path, content in value.items():
            if not isinstance(path, str) or not isinstance(content, str):
                raise TypeError("Skill draft file paths and contents must be text.")
            files[path] = content
        return files

    @staticmethod
    def _positive_int(value: Any, field_name: str) -> int:
        if isinstance(value, bool):
            raise TypeError(f"Skill draft {field_name} must be a positive integer.")
        try:
            result = int(value)
        except (TypeError, ValueError) as exc:
            raise TypeError(
                f"Skill draft {field_name} must be a positive integer."
            ) from exc
        if result < 1:
            raise ValueError(f"Skill draft {field_name} must be a positive integer.")
        return result

    @classmethod
    def _optional_positive_int(cls, value: Any, field_name: str) -> int | None:
        if value is None:
            return None
        return cls._positive_int(value, field_name)

    @staticmethod
    def _timestamp(value: Any, field_name: str) -> float:
        if isinstance(value, bool):
            raise TypeError(f"Skill draft {field_name} must be a timestamp.")
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise TypeError(f"Skill draft {field_name} must be a timestamp.") from exc

    def _quarantine_record(
        self,
        *,
        kind: str,
        index: int,
        reason_code: str,
        reason: str,
        record: Any,
    ) -> None:
        del reason
        self._quarantine.append(
            self._safe_quarantine_metadata(
                kind=kind,
                index=index,
                reason_code=reason_code,
                record=record,
                quarantined_at=time.time(),
            )
        )

    @staticmethod
    def _reject_persisted_credentials(
        *,
        metadata: tuple[str, ...] = (),
        skill_markdown: str,
        files: dict[str, str],
    ) -> None:
        issues = scan_skill_package_credentials(
            skill_markdown="\n".join((*metadata, skill_markdown)),
            files=files,
        )
        if not issues:
            return
        codes = ",".join(sorted({issue.code for issue in issues}))
        raise ValueError(f"credential_scan_blocked:{codes}")

    @classmethod
    def _safe_quarantine_metadata(
        cls,
        *,
        kind: str,
        index: int,
        reason_code: str,
        record: Any,
        quarantined_at: float,
    ) -> dict[str, Any]:
        try:
            raw = json.dumps(
                record,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, UnicodeEncodeError):
            raw = type(record).__name__.encode("ascii", errors="replace")
        safe_kind = kind if kind in {"item", "revision"} else "unknown"
        safe_reason_code = (
            reason_code
            if reason_code
            in {
                "record_not_object",
                "invalid_record",
                "invalid_revision",
                "legacy_quarantine",
            }
            else "legacy_quarantine"
        )
        return {
            "kind": safe_kind,
            "index": max(0, int(index)),
            "reason_code": safe_reason_code,
            "record_sha256": hashlib.sha256(raw).hexdigest(),
            "record_size_bytes": len(raw),
            "quarantined_at": float(quarantined_at),
        }

    @classmethod
    def _sanitize_quarantine_entry(
        cls, entry: Any, *, index: int
    ) -> dict[str, Any]:
        if not isinstance(entry, dict):
            return cls._safe_quarantine_metadata(
                kind="unknown",
                index=index,
                reason_code="legacy_quarantine",
                record=entry,
                quarantined_at=time.time(),
            )
        timestamp = entry.get("quarantined_at", time.time())
        try:
            safe_timestamp = float(timestamp)
        except (TypeError, ValueError):
            safe_timestamp = time.time()
        raw_index = entry.get("index", index)
        try:
            safe_index = int(raw_index)
        except (TypeError, ValueError):
            safe_index = index
        existing_digest = str(entry.get("record_sha256") or "").lower()
        existing_size = entry.get("record_size_bytes")
        if (
            "record" not in entry
            and len(existing_digest) == 64
            and all(character in "0123456789abcdef" for character in existing_digest)
            and isinstance(existing_size, int)
            and existing_size >= 0
        ):
            safe_kind = (
                str(entry.get("kind"))
                if entry.get("kind") in {"item", "revision"}
                else "unknown"
            )
            reason_code = str(entry.get("reason_code") or "legacy_quarantine")
            if reason_code not in {
                "record_not_object",
                "invalid_record",
                "invalid_revision",
                "legacy_quarantine",
            }:
                reason_code = "legacy_quarantine"
            return {
                "kind": safe_kind,
                "index": max(0, safe_index),
                "reason_code": reason_code,
                "record_sha256": existing_digest,
                "record_size_bytes": existing_size,
                "quarantined_at": safe_timestamp,
            }
        if "record" in entry:
            record = entry.get("record")
        else:
            # Hash the complete legacy entry if its original record is unavailable.
            record = entry
        return cls._safe_quarantine_metadata(
            kind=str(entry.get("kind") or "unknown"),
            index=safe_index,
            reason_code=str(entry.get("reason_code") or "legacy_quarantine"),
            record=record,
            quarantined_at=safe_timestamp,
        )

    def _backup_original_unlocked(
        self, raw_bytes: bytes, *, corrupted: bool = False
    ) -> None:
        backup_path = (
            self.storage_dir / "skill_drafts.corrupt.backup.json"
            if corrupted
            else self.backup_path
        )
        if backup_path.exists():
            return
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        temp_path = backup_path.with_name(
            f"{backup_path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
        )
        try:
            with temp_path.open("xb") as handle:
                handle.write(raw_bytes)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, backup_path)
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _ensure_writable_unlocked(self) -> None:
        if self._load_error:
            raise SkillDraftStorageError(
                f"Skill draft storage is quarantined and cannot be overwritten: {self._load_error}"
            )

    def _save_unlocked(self) -> None:
        self._ensure_writable_unlocked()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        temp_path = self.snapshot_path.with_name(
            f"{self.snapshot_path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
        )
        payload = {
            "version": self.SCHEMA_VERSION,
            "items": [
                asdict(item)
                for item in sorted(self._items.values(), key=lambda item: item.draft_id)
            ],
            "revisions": [
                asdict(snapshot)
                for draft_id in sorted(self._revisions)
                for snapshot in sorted(
                    self._revisions[draft_id].values(),
                    key=lambda item: item.revision,
                )
            ],
            "proposal_receipts": [
                asdict(receipt)
                for receipt in sorted(
                    self._proposal_receipts.values(), key=lambda item: item.apply_key
                )
            ],
            "quarantine": self._quarantine,
        }
        serialized = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        try:
            with temp_path.open("xb") as handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.snapshot_path)
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _json_copy(value: Any) -> Any:
        return json.loads(json.dumps(value, ensure_ascii=False))

    @staticmethod
    def _copy(item: WorkspaceSkillDraft) -> WorkspaceSkillDraft:
        payload = WorkspaceSkillDraftStore._json_copy(asdict(item))
        raw_decision = payload.get("quality_decision")
        if isinstance(raw_decision, dict):
            payload["quality_decision"] = SkillQualityDecision(**raw_decision)
        return WorkspaceSkillDraft(**payload)

    @staticmethod
    def _copy_revision(item: SkillDraftRevision) -> SkillDraftRevision:
        return SkillDraftRevision(
            **WorkspaceSkillDraftStore._json_copy(asdict(item))
        )
