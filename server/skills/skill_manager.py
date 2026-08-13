from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Literal, Mapping
from urllib.parse import urlparse

from .draft_store import SkillDraftValidationError, WorkspaceSkillDraftStore
from .local_import import LocalSkillImport, SkillLocalImportError, SkillLocalImportStore
from .package_validation import compute_package_digest
from .trust_scanner import SkillTrustTreeEntry, scan_skill_trust_receipt
from .trust_service import (
    SkillRuntimeEnvironment,
    SkillTrustError,
    SkillTrustService,
)


_REPLACE_DIFF_FILE_CHARS = 8 * 1024
_REPLACE_DIFF_TOTAL_CHARS = 128 * 1024
_REPLACE_CHANGE_LIMIT = 500
_PASSIVE_BINARY_SUFFIXES = {
    ".gif",
    ".jpeg",
    ".jpg",
    ".mp3",
    ".mp4",
    ".pdf",
    ".png",
    ".wav",
    ".webp",
    ".woff",
    ".woff2",
}


class SkillManagerError(Exception):
    """Base error raised by the Skill manager."""


class SkillInstallError(SkillManagerError):
    """Raised when a Skill cannot be installed."""


class SkillNotFoundError(SkillManagerError):
    """Raised when a requested Skill is not installed."""


class SkillValidationError(SkillManagerError):
    """Raised when a Skill source or identifier is invalid."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


@dataclass(frozen=True)
class InstalledSkill:
    """Metadata stored for an installed Skill."""

    skill_id: str
    name: str
    description: str
    repo_url: str
    sub_path: str
    installed_at: float
    source_ref: str | None = None
    source_kind: str = "git"
    source_id: str | None = None
    source_revision: int | None = None
    content_digest: str = ""
    package_subpath: str = ""
    trust_state: str = "not_applicable"
    trust_receipt_id: str | None = None
    trust_fingerprint: str | None = None
    trust_risk_level: str | None = None
    trust_status: str | None = None
    trust_install_policy: str | None = None
    trust_compatibility_status: str | None = None
    trust_router_eligible: bool = False
    trust_package_digest: str | None = None
    trust_directory_tree_sha: str | None = None
    trust_verified_at: float | None = None


@dataclass(frozen=True)
class SkillInstallReceipt:
    """Crash-recovery record for a Workspace draft install transaction.

    The receipt intentionally contains metadata and filesystem basenames only;
    Skill contents remain in the staging directory and installed package.
    """

    version: int
    transaction_id: str
    phase: Literal["prepared", "swapped", "committed"]
    skill_id: str
    source_id: str
    source_revision: int | None
    content_digest: str
    package_subpath: str
    staging_name: str
    backup_name: str
    previous_metadata: dict[str, object] | None
    installed_metadata: dict[str, object]
    created_at: float
    previous_content_digest: str | None = None
    source_kind: Literal["workspace_draft", "local_import"] = "workspace_draft"


class SkillManager:
    """Install, list, read, and uninstall local Skill packages.

    The manager stores Skill directories under ``server/skills/installed`` by
    default. It is intentionally filesystem-backed for the MVP so it can be
    moved to a database later without changing the REST surface.
    """

    def __init__(
        self,
        installed_dir: Path | None = None,
        tmp_dir: Path | None = None,
        *,
        allow_local_repos: bool = False,
        git_timeout_seconds: int = 30,
        trust_service: SkillTrustService | None = None,
        local_import_store: SkillLocalImportStore | None = None,
        lifecycle_store: Any | None = None,
    ) -> None:
        package_dir = Path(__file__).resolve().parent
        self.installed_dir = Path(
            installed_dir
            or os.getenv("SKILL_INSTALLED_DIR")
            or package_dir / "installed"
        )
        self.tmp_dir = Path(
            tmp_dir or os.getenv("SKILL_TMP_DIR") or package_dir / "tmp"
        )
        self.allow_local_repos = allow_local_repos
        self.git_timeout_seconds = git_timeout_seconds
        self.metadata_path = self.installed_dir / "installed.json"
        self.trust_service = trust_service or SkillTrustService()
        self.local_import_store = local_import_store
        self.lifecycle_store = lifecycle_store
        self._lock = threading.RLock()
        self._trust_reconciled = False

    def install_skill(
        self,
        repo_url: str,
        sub_path: str = "",
        source_ref: str | None = None,
        *,
        ephemeral_trust_fingerprint: str | None = None,
        runtime_environment: SkillRuntimeEnvironment | None = None,
    ) -> InstalledSkill:
        """Install a Skill from a GitHub repository subdirectory.

        Args:
            repo_url: GitHub repository URL, or a local repository path only
                when ``allow_local_repos`` is enabled for tests.
            sub_path: Repository subdirectory containing ``SKILL.md``.
            source_ref: Optional audited 40-character Git commit to install.

        Returns:
            Metadata for the installed Skill.
        """

        normalized_repo_url = self._validate_repo_url(repo_url)
        normalized_sub_path = self._validate_sub_path(sub_path)
        normalized_source_ref = self._validate_source_ref(source_ref)
        skill_id = self._build_skill_id(normalized_repo_url, normalized_sub_path)
        try:
            _decision, trust_receipt = self.trust_service.install_decision(
                skill_id=skill_id,
                repo_url=normalized_repo_url,
                sub_path=normalized_sub_path,
                source_ref=normalized_source_ref,
                ephemeral_trust_fingerprint=ephemeral_trust_fingerprint,
                # Installing an exact, audited package is intentionally
                # separate from activating it in a particular runtime.  The
                # local console may keep a compatible package even when the
                # current chat/workflow lacks one of its declared tools.  A
                # Router install passes its concrete environment explicitly
                # because that operation also intends to activate the Skill
                # in the current run.
                environment=runtime_environment,
            )
        except SkillTrustError as exc:
            raise SkillValidationError(
                str(exc), code=exc.code, details=exc.details
            ) from exc

        with self._lock:
            self._ensure_dirs()
            self.recover_lifecycle_transaction(skill_id)
            target_dir = self._safe_skill_dir(skill_id)
            tmp_root = Path(
                tempfile.mkdtemp(prefix=f"{skill_id}-", dir=str(self.tmp_dir))
            )
            checkout_dir = tmp_root / "repo"
            staging_dir = self.installed_dir / f".{skill_id}.staging-{uuid.uuid4().hex}"
            backup_dir = self.installed_dir / f".{skill_id}.backup-{uuid.uuid4().hex}"
            committed = False
            lifecycle_receipt: Any | None = None
            try:
                self._git_sparse_clone(
                    normalized_repo_url,
                    normalized_sub_path,
                    checkout_dir,
                    normalized_source_ref,
                )
                source_dir = checkout_dir / normalized_sub_path if normalized_sub_path else checkout_dir
                skill_md = source_dir / "SKILL.md"
                if not skill_md.exists():
                    raise SkillInstallError(
                        f"SKILL.md not found in '{normalized_sub_path or '.'}'"
                    )

                try:
                    trust_metadata = self.trust_service.verify_checkout(
                        checkout_dir=checkout_dir,
                        source_dir=source_dir,
                        receipt=trust_receipt,
                        source_ref=normalized_source_ref,
                    )
                except SkillTrustError as exc:
                    raise SkillValidationError(
                        str(exc), code=exc.code, details=exc.details
                    ) from exc

                shutil.copytree(
                    source_dir,
                    staging_dir,
                    ignore=shutil.ignore_patterns(".git"),
                )
                metadata = self._parse_skill_metadata(
                    skill_id,
                    normalized_repo_url,
                    normalized_sub_path,
                    staging_dir / "SKILL.md",
                    normalized_source_ref,
                    trust_metadata=trust_metadata,
                )
                installed = self._read_metadata()
                previous_record = installed.get(skill_id)
                lifecycle_receipt = self._prepare_lifecycle_install(
                    skill_id=skill_id,
                    previous_record=previous_record,
                    target_metadata=metadata,
                    target_package_dir=staging_dir,
                )
                installed[skill_id] = asdict(metadata)
                try:
                    self._mark_lifecycle_swapped(lifecycle_receipt)
                    if target_dir.exists():
                        target_dir.rename(backup_dir)
                    staging_dir.rename(target_dir)
                    self._write_metadata(installed)
                    self._mark_lifecycle_metadata_committed(lifecycle_receipt)
                except Exception:
                    if target_dir.exists():
                        shutil.rmtree(target_dir, ignore_errors=True)
                    if backup_dir.exists():
                        backup_dir.rename(target_dir)
                    self._abort_lifecycle_transaction(lifecycle_receipt)
                    raise
                committed = True
                if lifecycle_receipt is not None:
                    self.finalize_lifecycle_transaction(skill_id)
                if backup_dir.exists():
                    shutil.rmtree(backup_dir, ignore_errors=True)
                return metadata
            finally:
                shutil.rmtree(tmp_root, ignore_errors=True)
                shutil.rmtree(staging_dir, ignore_errors=True)
                if committed:
                    shutil.rmtree(backup_dir, ignore_errors=True)

    def install_workspace_draft(
        self,
        *,
        draft_id: str,
        slug: str,
        skill_markdown: str,
        files: dict[str, str],
        source_revision: int | None = None,
        quality_required: bool = False,
        quality_status: str | None = None,
        quality_decision_id: str | None = None,
        quality_run_id: str | None = None,
    ) -> InstalledSkill:
        """Install or explicitly upgrade one reviewed Workspace Skill draft.

        A draft owns one stable Skill id. Retrying the same package digest is
        idempotent; changed content is swapped atomically and can be recovered
        from a persisted transaction receipt after process interruption.
        """

        clean_draft_id = str(draft_id or "").strip()
        if not clean_draft_id or len(clean_draft_id) > 200:
            raise SkillValidationError("Workspace Skill draft id is invalid.")
        if source_revision is not None and (
            isinstance(source_revision, bool)
            or not isinstance(source_revision, int)
            or source_revision < 1
        ):
            raise SkillValidationError(
                "Workspace Skill source revision must be positive."
            )
        frontmatter = WorkspaceSkillDraftStore._parse_frontmatter(skill_markdown)
        try:
            normalized = WorkspaceSkillDraftStore.validate_package(
                name=frontmatter.get("name", ""),
                slug=slug,
                description=frontmatter.get("description", ""),
                skill_markdown=skill_markdown,
                files=files,
            )
        except SkillDraftValidationError as exc:
            raise SkillValidationError(str(exc)) from exc
        clean_slug = normalized["slug"]
        skill_markdown = normalized["skill_markdown"]
        files = normalized["files"]
        content_digest = compute_package_digest(skill_markdown, files)
        deterministic_skill_id = self._workspace_skill_id(clean_draft_id)
        with self._lock:
            self._ensure_dirs()
            installed = self._read_metadata()
            skill_id = self._find_workspace_skill_id(installed, clean_draft_id)
            skill_id = skill_id or deterministic_skill_id
            self._recover_workspace_install_receipt(skill_id, clean_draft_id)
            installed = self._read_metadata()
            skill_id = self._find_workspace_skill_id(installed, clean_draft_id) or skill_id
            target_dir = self._safe_skill_dir(skill_id)
            previous_record = installed.get(skill_id)
            previous_metadata = (
                self._installed_skill_from_record(previous_record)
                if previous_record is not None
                else None
            )
            previous_content_digest = ""
            if target_dir.exists() and previous_record is None:
                raise SkillInstallError(
                    "Workspace Skill target exists without installed metadata."
                )
            if previous_metadata is not None:
                try:
                    current_package_dir = self._resolve_package_directory(
                        skill_id, previous_record
                    )
                    previous_content_digest = self._directory_content_digest(
                        current_package_dir
                    )
                except SkillNotFoundError:
                    previous_content_digest = ""
                if (
                    previous_content_digest == content_digest
                    and previous_metadata.content_digest == content_digest
                    and previous_metadata.package_subpath == clean_slug
                    and (target_dir / clean_slug / "SKILL.md").is_file()
                ):
                    return previous_metadata

            transaction_id = uuid.uuid4().hex
            staging_dir = self.installed_dir / (
                f".{skill_id}.staging-{transaction_id}"
            )
            backup_dir = self.installed_dir / (
                f".{skill_id}.backup-{transaction_id}"
            )
            package_dir = staging_dir / clean_slug
            metadata: InstalledSkill | None = None
            receipt: SkillInstallReceipt | None = None
            lifecycle_receipt: Any | None = None
            swapped = False
            metadata_write_attempted = False
            try:
                package_dir.mkdir(parents=True, exist_ok=False)
                (package_dir / "SKILL.md").write_bytes(
                    skill_markdown.encode("utf-8")
                )
                for relative_path, content in files.items():
                    target = package_dir.joinpath(*relative_path.split("/"))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content.encode("utf-8"))
                metadata = self._parse_skill_metadata(
                    skill_id,
                    f"workspace://draft/{clean_draft_id}",
                    "",
                    package_dir / "SKILL.md",
                    source_kind="workspace_draft",
                    source_id=clean_draft_id,
                    source_revision=source_revision,
                    content_digest=content_digest,
                    package_subpath=clean_slug,
                )
                lifecycle_receipt = self._prepare_lifecycle_install(
                    skill_id=skill_id,
                    previous_record=previous_record,
                    target_metadata=metadata,
                    target_package_dir=package_dir,
                    quality_evidence_status=(
                        "matched" if quality_required else "not_applicable"
                    ),
                    quality_required=quality_required,
                    quality_status=quality_status,
                    quality_decision_id=quality_decision_id,
                    quality_run_id=quality_run_id,
                )
                receipt = SkillInstallReceipt(
                    version=1,
                    transaction_id=transaction_id,
                    phase="prepared",
                    skill_id=skill_id,
                    source_id=clean_draft_id,
                    source_revision=source_revision,
                    content_digest=content_digest,
                    package_subpath=clean_slug,
                    staging_name=staging_dir.name,
                    backup_name=backup_dir.name,
                    previous_metadata=(
                        dict(previous_record)
                        if previous_record is not None
                        else None
                    ),
                    installed_metadata=asdict(metadata),
                    created_at=time.time(),
                    previous_content_digest=previous_content_digest or None,
                )
                self._write_install_receipt(receipt)
                self._mark_lifecycle_swapped(lifecycle_receipt)
                if target_dir.exists():
                    target_dir.rename(backup_dir)
                staging_dir.rename(target_dir)
                swapped = True
                receipt = replace(receipt, phase="swapped")
                self._write_install_receipt(receipt)
                installed[skill_id] = asdict(metadata)
                metadata_write_attempted = True
                self._write_metadata(installed)
                receipt = replace(receipt, phase="committed")
                self._write_install_receipt(receipt)
                self._mark_lifecycle_metadata_committed(lifecycle_receipt)
            except Exception:
                rollback_error: Exception | None = None
                try:
                    if backup_dir.exists():
                        self._remove_directory_if_present(target_dir)
                        backup_dir.rename(target_dir)
                    elif (swapped or previous_record is None) and target_dir.exists():
                        self._remove_directory_if_present(target_dir)
                    if metadata_write_attempted:
                        restored = self._read_metadata()
                        if previous_record is None:
                            restored.pop(skill_id, None)
                        else:
                            restored[skill_id] = dict(previous_record)
                        self._write_metadata(restored)
                    self._remove_directory_if_present(staging_dir)
                    self._remove_directory_if_present(backup_dir)
                    self._receipt_path(skill_id).unlink(missing_ok=True)
                    self._abort_lifecycle_transaction(lifecycle_receipt)
                except Exception as exc:
                    rollback_error = exc
                if rollback_error is not None:
                    raise SkillInstallError(
                        "Workspace Skill installation failed and rollback is "
                        "incomplete; retry the same installation to recover."
                    ) from rollback_error
                raise
            else:
                try:
                    self._remove_directory_if_present(backup_dir)
                    self._remove_directory_if_present(staging_dir)
                    self._receipt_path(skill_id).unlink(missing_ok=True)
                except Exception as exc:
                    raise SkillInstallError(
                        "Workspace Skill was installed, but transaction cleanup is "
                        "incomplete; retry the same installation to recover."
                    ) from exc
                if metadata is None:
                    raise SkillInstallError("Workspace Skill metadata was not created.")
                return metadata

    def install_local_import(
        self,
        *,
        record: LocalSkillImport,
        package_dir: Path,
        confirmed: bool = False,
        expected_installed_digest: str | None = None,
    ) -> InstalledSkill:
        """Install or explicitly replace one immutable local import package."""

        if (
            not record.local_skill_id
            or not record.package_digest
            or not record.trust_receipt
        ):
            raise SkillValidationError(
                "Local Skill import is incomplete.", code="skill_import_stale"
            )
        skill_id = self._validate_skill_id(record.local_skill_id)
        expected_previous = (
            str(expected_installed_digest or "").strip().casefold() or None
        )
        if expected_previous is not None and not re.fullmatch(
            r"[a-f0-9]{64}", expected_previous
        ):
            raise SkillValidationError(
                "Expected installed digest is invalid.",
                code="skill_import_package_mismatch",
            )
        try:
            local_receipt = self.trust_service.validate_local_receipt(
                record.trust_receipt,
                import_id=record.import_id,
                import_revision=record.content_revision,
                package_digest=record.package_digest,
            )
            self.trust_service.local_import_decision(
                local_receipt,
                skill_id=skill_id,
                import_id=record.import_id,
                import_revision=record.content_revision,
                package_digest=record.package_digest,
                ephemeral_trust_fingerprint=(
                    record.trust_fingerprint if confirmed else None
                ),
                environment=None,
            )
        except SkillTrustError as exc:
            raise SkillValidationError(
                str(exc), code=exc.code, details=exc.details
            ) from exc
        actual_source_digest = self.trust_service.compute_directory_digest(
            package_dir
        )
        if actual_source_digest != record.package_digest:
            raise SkillValidationError(
                "Local Skill package no longer matches its import receipt.",
                code="skill_import_package_mismatch",
            )
        trust_metadata = self.trust_service.receipt_metadata(
            local_receipt, verified_at=time.time()
        )

        with self._lock:
            self._ensure_dirs()
            installed = self._read_metadata()
            self._recover_install_receipt(
                skill_id,
                source_kind="local_import",
                source_id=record.import_id,
            )
            installed = self._read_metadata()
            target_dir = self._safe_skill_dir(skill_id)
            previous_record = installed.get(skill_id)
            previous_metadata = (
                self._installed_skill_from_record(previous_record)
                if previous_record is not None
                else None
            )
            previous_content_digest = ""
            if target_dir.exists() and previous_record is None:
                raise SkillInstallError(
                    "Local Skill target exists without installed metadata."
                )
            if previous_metadata is not None:
                if previous_metadata.source_kind != "local_import":
                    raise SkillValidationError(
                        "A local import cannot replace a Skill from another source.",
                        code="skill_import_replace_required",
                    )
                try:
                    current_package_dir = self._resolve_package_directory(
                        skill_id, previous_record or {}
                    )
                    previous_content_digest = self._directory_content_digest(
                        current_package_dir
                    )
                except SkillNotFoundError:
                    previous_content_digest = ""
                if (
                    previous_content_digest != previous_metadata.content_digest
                    or not previous_content_digest
                ):
                    raise SkillValidationError(
                        "Installed local Skill bytes no longer match metadata.",
                        code="skill_import_package_mismatch",
                    )
                if previous_content_digest == record.package_digest:
                    metadata = self._parse_skill_metadata(
                        skill_id,
                        f"local-import://{record.import_id}",
                        "",
                        current_package_dir / "SKILL.md",
                        source_kind="local_import",
                        source_id=record.import_id,
                        source_revision=record.content_revision,
                        content_digest=record.package_digest,
                        trust_metadata=trust_metadata,
                    )
                    installed[skill_id] = asdict(metadata)
                    self._write_metadata(installed)
                    return metadata
                if expected_previous is None:
                    raise SkillValidationError(
                        "Installing this import would replace a different local package.",
                        code="skill_import_replace_required",
                        details={
                            "skillId": skill_id,
                            "installedDigest": previous_content_digest,
                            "newDigest": record.package_digest,
                        },
                    )
                if expected_previous != previous_content_digest:
                    raise SkillValidationError(
                        "Installed local Skill changed. Reload before replacing it.",
                        code="skill_import_package_mismatch",
                    )
            elif expected_previous is not None:
                raise SkillValidationError(
                    "The local Skill to replace is no longer installed.",
                    code="skill_import_package_mismatch",
                )

            transaction_id = uuid.uuid4().hex
            staging_dir = self.installed_dir / f".{skill_id}.staging-{transaction_id}"
            backup_dir = self.installed_dir / f".{skill_id}.backup-{transaction_id}"
            metadata: InstalledSkill | None = None
            receipt: SkillInstallReceipt | None = None
            lifecycle_receipt: Any | None = None
            swapped = False
            metadata_write_attempted = False
            try:
                shutil.copytree(package_dir, staging_dir, symlinks=True)
                if self.trust_service.compute_directory_digest(staging_dir) != record.package_digest:
                    raise SkillValidationError(
                        "Local Skill package changed while it was being installed.",
                        code="skill_import_package_mismatch",
                    )
                metadata = self._parse_skill_metadata(
                    skill_id,
                    f"local-import://{record.import_id}",
                    "",
                    staging_dir / "SKILL.md",
                    source_kind="local_import",
                    source_id=record.import_id,
                    source_revision=record.content_revision,
                    content_digest=record.package_digest,
                    trust_metadata=trust_metadata,
                )
                lifecycle_receipt = self._prepare_lifecycle_install(
                    skill_id=skill_id,
                    previous_record=previous_record,
                    target_metadata=metadata,
                    target_package_dir=staging_dir,
                )
                receipt = SkillInstallReceipt(
                    version=1,
                    transaction_id=transaction_id,
                    phase="prepared",
                    skill_id=skill_id,
                    source_id=record.import_id,
                    source_revision=record.content_revision,
                    content_digest=record.package_digest,
                    package_subpath="",
                    staging_name=staging_dir.name,
                    backup_name=backup_dir.name,
                    previous_metadata=(
                        dict(previous_record) if previous_record is not None else None
                    ),
                    installed_metadata=asdict(metadata),
                    created_at=time.time(),
                    previous_content_digest=previous_content_digest or None,
                    source_kind="local_import",
                )
                self._write_install_receipt(receipt)
                self._mark_lifecycle_swapped(lifecycle_receipt)
                if target_dir.exists():
                    target_dir.rename(backup_dir)
                staging_dir.rename(target_dir)
                swapped = True
                self._write_install_receipt(replace(receipt, phase="swapped"))
                installed[skill_id] = asdict(metadata)
                metadata_write_attempted = True
                self._write_metadata(installed)
                self._write_install_receipt(replace(receipt, phase="committed"))
                self._mark_lifecycle_metadata_committed(lifecycle_receipt)
            except Exception:
                rollback_error: Exception | None = None
                try:
                    if backup_dir.exists():
                        self._remove_directory_if_present(target_dir)
                        backup_dir.rename(target_dir)
                    elif (swapped or previous_record is None) and target_dir.exists():
                        self._remove_directory_if_present(target_dir)
                    if metadata_write_attempted:
                        restored = self._read_metadata()
                        if previous_record is None:
                            restored.pop(skill_id, None)
                        else:
                            restored[skill_id] = dict(previous_record)
                        self._write_metadata(restored)
                    self._remove_directory_if_present(staging_dir)
                    self._remove_directory_if_present(backup_dir)
                    self._receipt_path(skill_id).unlink(missing_ok=True)
                    self._abort_lifecycle_transaction(lifecycle_receipt)
                except Exception as exc:
                    rollback_error = exc
                if rollback_error is not None:
                    raise SkillInstallError(
                        "Local Skill installation failed and rollback is incomplete; "
                        "retry the same installation to recover."
                    ) from rollback_error
                raise
            else:
                try:
                    self._remove_directory_if_present(backup_dir)
                    self._remove_directory_if_present(staging_dir)
                    self._receipt_path(skill_id).unlink(missing_ok=True)
                except Exception as exc:
                    raise SkillInstallError(
                        "Local Skill was installed, but transaction cleanup is incomplete; "
                        "retry the same installation to recover."
                    ) from exc
                if metadata is None:
                    raise SkillInstallError("Local Skill metadata was not created.")
                return metadata

    def install_local_import_current(
        self,
        import_id: str,
        *,
        expected_revision: int,
        expected_package_digest: str,
        expected_trust_fingerprint: str,
        confirmed: bool = False,
        expected_installed_digest: str | None = None,
    ) -> tuple[LocalSkillImport, InstalledSkill]:
        """Install a frozen import while preserving a single lock order.

        Runtime activation acquires the manager lock before consulting the
        import Store. Installation must use the same manager-to-Store order so
        concurrent activation, rescan, and replacement cannot deadlock.
        """

        if self.local_import_store is None:
            raise SkillValidationError(
                "Local Skill import storage is unavailable.",
                code="skill_import_storage_unavailable",
            )
        with self._lock:
            return self.local_import_store.install_current(
                import_id,
                expected_revision=expected_revision,
                expected_package_digest=expected_package_digest,
                expected_trust_fingerprint=expected_trust_fingerprint,
                installer=lambda frozen, package_dir: self.install_local_import(
                    record=frozen,
                    package_dir=package_dir,
                    confirmed=confirmed,
                    expected_installed_digest=expected_installed_digest,
                ),
            )

    def describe_local_import_replacement(
        self,
        *,
        record: LocalSkillImport,
        package_dir: Path,
    ) -> dict[str, Any] | None:
        """Return a bounded, read-only replacement preview for the console."""

        if not record.local_skill_id or not record.package_digest:
            return None
        skill_id = self._validate_skill_id(record.local_skill_id)
        if (
            self.trust_service.compute_directory_digest(package_dir)
            != record.package_digest
        ):
            raise SkillValidationError(
                "Local Skill package no longer matches its import receipt.",
                code="skill_import_package_mismatch",
            )
        with self._lock:
            installed = self._read_metadata()
            previous_record = installed.get(skill_id)
            if previous_record is None:
                return None
            previous = self._installed_skill_from_record(previous_record)
            base = {
                "skillId": skill_id,
                "sourceKind": previous.source_kind,
                "installedDigest": previous.content_digest,
                "newDigest": record.package_digest,
            }
            if previous.source_kind != "local_import":
                return {
                    **base,
                    "required": False,
                    "allowed": False,
                    "errorCode": "skill_import_replace_required",
                    "changes": [],
                    "changesTruncated": False,
                    "diffTruncated": False,
                }
            try:
                previous_dir = self._resolve_package_directory(
                    skill_id, previous_record
                )
                actual_previous_digest = self._directory_content_digest(previous_dir)
            except SkillManagerError:
                return {
                    **base,
                    "required": True,
                    "allowed": False,
                    "errorCode": "skill_import_package_mismatch",
                    "changes": [],
                    "changesTruncated": False,
                    "diffTruncated": False,
                }
            if actual_previous_digest != previous.content_digest:
                return {
                    **base,
                    "required": True,
                    "allowed": False,
                    "errorCode": "skill_import_package_mismatch",
                    "changes": [],
                    "changesTruncated": False,
                    "diffTruncated": False,
                }
            old_files = self._package_bytes(previous_dir)
            new_files = self._package_bytes(package_dir)
            return {
                **base,
                "required": actual_previous_digest != record.package_digest,
                "allowed": True,
                "errorCode": None,
                **self._replacement_changes(old_files, new_files),
            }

    @staticmethod
    def _package_bytes(package_dir: Path) -> dict[str, bytes]:
        files: dict[str, bytes] = {}
        for path in sorted(package_dir.rglob("*")):
            if path.is_symlink():
                raise SkillValidationError(
                    "Skill package contains an unsafe link.",
                    code="skill_import_package_mismatch",
                )
            if path.is_file():
                files[path.relative_to(package_dir).as_posix()] = path.read_bytes()
        return files

    @staticmethod
    def _replacement_changes(
        old_files: Mapping[str, bytes],
        new_files: Mapping[str, bytes],
    ) -> dict[str, Any]:
        changes: list[dict[str, Any]] = []
        diff_chars = 0
        diff_truncated = False
        all_paths = sorted(set(old_files) | set(new_files))
        for path in all_paths:
            old = old_files.get(path)
            new = new_files.get(path)
            if old == new:
                continue
            status = "added" if old is None else "removed" if new is None else "changed"
            old_bytes = old or b""
            new_bytes = new or b""
            suffix = Path(path).suffix.casefold()
            is_binary = suffix in _PASSIVE_BINARY_SUFFIXES
            old_text: str | None = None
            new_text: str | None = None
            if not is_binary:
                try:
                    old_text = old_bytes.decode("utf-8", errors="strict")
                    new_text = new_bytes.decode("utf-8", errors="strict")
                except UnicodeDecodeError:
                    is_binary = True
            change: dict[str, Any] = {
                "path": path,
                "status": status,
                "kind": "binary" if is_binary else "text",
                "oldSizeBytes": len(old_bytes) if old is not None else None,
                "newSizeBytes": len(new_bytes) if new is not None else None,
                "oldSha256": hashlib.sha256(old_bytes).hexdigest()
                if old is not None
                else None,
                "newSha256": hashlib.sha256(new_bytes).hexdigest()
                if new is not None
                else None,
            }
            if not is_binary and old_text is not None and new_text is not None:
                raw_diff = "\n".join(
                    difflib.unified_diff(
                        old_text.splitlines(),
                        new_text.splitlines(),
                        fromfile=f"installed/{path}",
                        tofile=f"import/{path}",
                        lineterm="",
                    )
                )
                remaining = max(0, _REPLACE_DIFF_TOTAL_CHARS - diff_chars)
                allowed = min(_REPLACE_DIFF_FILE_CHARS, remaining)
                if len(raw_diff) > allowed:
                    diff_truncated = True
                if allowed:
                    change["diff"] = raw_diff[:allowed]
                    change["diffTruncated"] = len(raw_diff) > allowed
                    diff_chars += min(len(raw_diff), allowed)
                else:
                    change["diff"] = ""
                    change["diffTruncated"] = bool(raw_diff)
            changes.append(change)
        changes_truncated = len(changes) > _REPLACE_CHANGE_LIMIT
        if changes_truncated:
            changes = changes[:_REPLACE_CHANGE_LIMIT]
        return {
            "changes": changes,
            "changesTruncated": changes_truncated,
            "diffTruncated": diff_truncated or changes_truncated,
        }

    def install_plugin_skill(
        self,
        *,
        plugin_id: str,
        plugin_slug: str,
        plugin_version: int,
        skill_slug: str,
        skill_markdown: str,
        files: dict[str, bytes],
    ) -> InstalledSkill:
        """Materialize one reviewed Plugin Skill as a versioned package."""

        if plugin_version < 1:
            raise SkillValidationError("Plugin version must be positive.")
        clean_plugin_slug = re.sub(
            r"[^a-z0-9]+", "-", plugin_slug.strip().lower()
        ).strip("-")
        clean_skill_slug = re.sub(
            r"[^a-z0-9]+", "-", skill_slug.strip().lower()
        ).strip("-")
        if not clean_plugin_slug or not clean_skill_slug:
            raise SkillValidationError("Plugin and Skill slugs are required.")
        if len(files) > 40:
            raise SkillValidationError("A Plugin Skill may contain at most 40 files.")
        total_bytes = len(skill_markdown.encode("utf-8"))
        normalized_files: dict[str, bytes] = {}
        for relative_path, content in files.items():
            normalized_path = WorkspaceSkillDraftStore._validate_path(
                relative_path
            )
            if len(content) > 1024 * 1024:
                raise SkillValidationError(
                    f"Plugin Skill file exceeds 1 MB: {normalized_path}"
                )
            total_bytes += len(content)
            normalized_files[normalized_path] = bytes(content)
        if total_bytes > 5 * 1024 * 1024:
            raise SkillValidationError("A Plugin Skill may not exceed 5 MB.")

        frontmatter = WorkspaceSkillDraftStore._parse_frontmatter(skill_markdown)
        try:
            WorkspaceSkillDraftStore.validate_package(
                name=frontmatter.get("name", ""),
                slug=clean_skill_slug,
                description=frontmatter.get("description", ""),
                skill_markdown=skill_markdown,
                files={},
            )
        except SkillDraftValidationError as exc:
            raise SkillValidationError(str(exc)) from exc

        skill_id = self._validate_skill_id(
            f"plugin-{clean_plugin_slug}-v{plugin_version}-{clean_skill_slug}"[:161]
        )
        with self._lock:
            self._ensure_dirs()
            installed = self._read_metadata()
            if skill_id in installed:
                return self._installed_skill_from_record(installed[skill_id])
            target_dir = self._safe_skill_dir(skill_id)
            if target_dir.exists():
                raise SkillInstallError("Plugin Skill target already exists.")
            temp_dir = Path(
                tempfile.mkdtemp(prefix=f"{skill_id}-", dir=str(self.tmp_dir))
            )
            try:
                (temp_dir / "SKILL.md").write_text(skill_markdown, encoding="utf-8")
                for relative_path, content in normalized_files.items():
                    target = temp_dir.joinpath(*relative_path.split("/"))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
                shutil.copytree(temp_dir, target_dir)
                metadata = self._parse_skill_metadata(
                    skill_id,
                    f"plugin://{plugin_id}/v{plugin_version}",
                    clean_skill_slug,
                    target_dir / "SKILL.md",
                    source_kind="plugin",
                    source_id=plugin_id,
                    source_revision=plugin_version,
                )
                installed[skill_id] = asdict(metadata)
                self._write_metadata(installed)
                return metadata
            except Exception:
                shutil.rmtree(target_dir, ignore_errors=True)
                raise
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

    def uninstall_skill(self, skill_id: str) -> None:
        """Remove an installed Skill by id."""

        normalized_skill_id = self._validate_skill_id(skill_id)
        with self._lock:
            installed = self._read_metadata()
            if normalized_skill_id not in installed:
                raise SkillNotFoundError(f"Skill '{normalized_skill_id}' is not installed")
            previous_record = dict(installed[normalized_skill_id])
            target_dir = self._safe_skill_dir(normalized_skill_id)
            lifecycle_receipt = self._prepare_lifecycle_uninstall(
                skill_id=normalized_skill_id,
                previous_record=previous_record,
            )
            removed_dir = self.installed_dir / (
                f".{normalized_skill_id}.uninstall-{uuid.uuid4().hex}"
            )
            moved = False
            try:
                self._mark_lifecycle_swapped(lifecycle_receipt)
                if target_dir.exists():
                    target_dir.rename(removed_dir)
                    moved = True
                installed.pop(normalized_skill_id, None)
                self._write_metadata(installed)
                self._mark_lifecycle_metadata_committed(lifecycle_receipt)
            except Exception:
                if moved and removed_dir.exists() and not target_dir.exists():
                    removed_dir.rename(target_dir)
                restored = self._read_metadata()
                restored[normalized_skill_id] = previous_record
                self._write_metadata(restored)
                self._abort_lifecycle_transaction(lifecycle_receipt)
                raise
            finally:
                if removed_dir.exists() and not target_dir.exists():
                    shutil.rmtree(removed_dir, ignore_errors=True)
            previous = self._installed_skill_from_record(previous_record)
            if lifecycle_receipt is not None and previous.source_kind == "git":
                self.finalize_lifecycle_transaction(normalized_skill_id)

    def rollback_skill_version(
        self,
        skill_id: str,
        version_id: str,
        *,
        expected_state_revision: int,
        expected_current_version_id: str | None,
        expected_package_digest: str,
        confirmed: bool,
    ) -> InstalledSkill:
        """Atomically switch the installed package to one immutable snapshot."""

        if not self._lifecycle_enabled():
            raise SkillValidationError(
                "Skill lifecycle management is disabled.",
                code="skill_lifecycle_disabled",
            )
        if not confirmed:
            raise SkillValidationError(
                "Skill rollback requires explicit confirmation.",
                code="skill_lifecycle_confirmation_required",
            )
        normalized_skill_id = self._validate_skill_id(skill_id)
        clean_digest = str(expected_package_digest or "").strip().casefold()
        with self._lock:
            state = self.lifecycle_store.require_state(normalized_skill_id)
            version = self.lifecycle_store.require_version(version_id)
            if (
                state.revision != expected_state_revision
                or state.current_version_id != expected_current_version_id
                or version.skill_id != normalized_skill_id
                or version.package_digest != clean_digest
            ):
                raise SkillValidationError(
                    "Skill lifecycle state changed. Reload before rollback.",
                    code="skill_lifecycle_version_conflict",
                )
            try:
                pending = self.lifecycle_store.require_transaction(
                    normalized_skill_id
                )
            except Exception as missing:
                if str(getattr(missing, "code", "")) != "skill_lifecycle_not_found":
                    raise
                pending = None
            if pending is not None:
                if (
                    pending.operation != "rollback"
                    or pending.target_version_id != version.version_id
                    or pending.phase not in {
                        "metadata_committed", "source_projected", "lifecycle_committed"
                    }
                ):
                    raise SkillValidationError(
                        "Another Skill lifecycle transaction is incomplete.",
                        code="skill_lifecycle_transaction_incomplete",
                    )
                current = self.get_installed_skill(normalized_skill_id)
                if current.content_digest != version.package_digest:
                    raise SkillValidationError(
                        "Installed Skill does not match the pending rollback.",
                        code="skill_lifecycle_package_mismatch",
                    )
                return current
            if state.current_version_id == version.version_id and state.status == "active":
                return self.get_installed_skill(normalized_skill_id)
            if version.source_kind == "workspace_draft" and version.quality_required and not (
                version.quality_evidence_status == "matched"
                and version.quality_status in {"accepted", "eval_waived"}
            ):
                raise SkillValidationError(
                    "Historical Creator Skill quality evidence is unavailable.",
                    code="skill_lifecycle_quality_unavailable",
                )
            if version.source_kind in {"git", "local_import"}:
                if (
                    version.trust_status == "blocked"
                    or version.trust_compatibility_status == "unsupported"
                    or not version.trust_receipt_id
                    or not version.trust_fingerprint
                    or version.trust_receipt_snapshot is None
                ):
                    raise SkillValidationError(
                        "Historical Skill trust evidence is unavailable.",
                        code="skill_lifecycle_trust_unavailable",
                    )
                frozen_item = self._parse_skill_metadata(
                    normalized_skill_id,
                    version.repo_url,
                    version.sub_path,
                    Path(self.lifecycle_store.package_directory(version.version_id))
                    / "SKILL.md",
                    version.source_ref,
                    source_kind=version.source_kind,
                    source_id=version.source_id,
                    source_revision=version.source_revision,
                    content_digest=version.package_digest,
                    package_subpath="",
                    trust_metadata={
                        "trust_state": "receipt_matched",
                        "trust_receipt_id": version.trust_receipt_id,
                        "trust_fingerprint": version.trust_fingerprint,
                        "trust_risk_level": version.trust_risk_level,
                        "trust_status": version.trust_status,
                        "trust_install_policy": version.trust_install_policy,
                        "trust_compatibility_status": version.trust_compatibility_status,
                        "trust_router_eligible": version.trust_router_eligible,
                        "trust_package_digest": version.package_digest,
                        "trust_directory_tree_sha": version.trust_directory_tree_sha,
                    },
                )
                try:
                    self.trust_service.frozen_receipt_activation_decision(
                        frozen_item,
                        version.trust_receipt_snapshot,
                        environment=None,
                        check_runtime=False,
                    )
                except SkillTrustError as exc:
                    raise SkillValidationError(
                        str(exc), code=exc.code, details=exc.details
                    ) from exc
            current_git_scan: dict[str, Any] | None = None
            if version.source_kind == "git":
                current_git_scan = self._scan_historical_git_version(version)
                if (
                    current_git_scan.get("installPolicy") == "block"
                    or current_git_scan.get("trustStatus") == "blocked"
                    or current_git_scan.get("compatibilityStatus") == "unsupported"
                ):
                    raise SkillValidationError(
                        "Historical Git Skill is blocked by the current offline scanner.",
                        code="skill_lifecycle_trust_unavailable",
                    )
            target_package = Path(
                self.lifecycle_store.package_directory(version.version_id)
            )
            trust_metadata = {
                "trust_state": (
                    "receipt_matched"
                    if version.source_kind in {"git", "local_import"}
                    else "not_applicable"
                ),
                "trust_receipt_id": version.trust_receipt_id,
                "trust_fingerprint": version.trust_fingerprint,
                "trust_risk_level": version.trust_risk_level,
                "trust_status": version.trust_status,
                "trust_install_policy": version.trust_install_policy,
                "trust_compatibility_status": version.trust_compatibility_status,
                "trust_router_eligible": bool(
                    version.trust_router_eligible
                    and (
                        current_git_scan is None
                        or current_git_scan.get("routerEligible") is True
                    )
                ),
                "trust_package_digest": version.package_digest,
                "trust_directory_tree_sha": version.trust_directory_tree_sha,
            }
            metadata = self._parse_skill_metadata(
                normalized_skill_id,
                version.repo_url,
                version.sub_path,
                target_package / "SKILL.md",
                version.source_ref,
                source_kind=version.source_kind,
                source_id=version.source_id,
                source_revision=version.source_revision,
                content_digest=version.package_digest,
                package_subpath="",
                trust_metadata=trust_metadata,
            )
            # Older Git metadata predates ``source_id``.  Rollback must preserve
            # that frozen identity exactly instead of letting the parser invent
            # the newer ``repo#subpath`` fallback, otherwise the selected
            # immutable version no longer matches its own installation record.
            if metadata.source_id != version.source_id:
                metadata = replace(metadata, source_id=version.source_id)
            installed = self._read_metadata()
            previous_record = installed.get(normalized_skill_id)
            if previous_record is not None:
                previous = self._installed_skill_from_record(previous_record)
                if previous.source_kind != version.source_kind:
                    raise SkillValidationError(
                        "Rollback cannot cross Skill source kinds.",
                        code="skill_lifecycle_source_conflict",
                    )
            transaction = self._prepare_lifecycle_install(
                skill_id=normalized_skill_id,
                previous_record=previous_record,
                target_metadata=metadata,
                target_package_dir=target_package,
                operation="rollback",
                target_version_id=version.version_id,
            )
            target_dir = self._safe_skill_dir(normalized_skill_id)
            staging_dir = self.installed_dir / (
                f".{normalized_skill_id}.rollback-{uuid.uuid4().hex}"
            )
            backup_dir = self.installed_dir / (
                f".{normalized_skill_id}.rollback-backup-{uuid.uuid4().hex}"
            )
            try:
                shutil.copytree(target_package, staging_dir)
                self._mark_lifecycle_swapped(transaction)
                if target_dir.exists():
                    target_dir.rename(backup_dir)
                staging_dir.rename(target_dir)
                installed[normalized_skill_id] = asdict(metadata)
                self._write_metadata(installed)
                self._mark_lifecycle_metadata_committed(transaction)
            except Exception:
                self._remove_directory_if_present(target_dir)
                if backup_dir.exists():
                    backup_dir.rename(target_dir)
                restored = self._read_metadata()
                if previous_record is None:
                    restored.pop(normalized_skill_id, None)
                else:
                    restored[normalized_skill_id] = dict(previous_record)
                self._write_metadata(restored)
                self._abort_lifecycle_transaction(transaction)
                raise
            finally:
                self._remove_directory_if_present(staging_dir)
                self._remove_directory_if_present(backup_dir)
            if transaction is not None and version.source_kind == "git":
                self.finalize_lifecycle_transaction(normalized_skill_id)
            return metadata

    def _scan_historical_git_version(self, version: Any) -> dict[str, Any]:
        if (
            not version.repo_url
            or not version.source_ref
            or not version.trust_directory_tree_sha
        ):
            raise SkillValidationError(
                "Historical Git Skill scan evidence is unavailable.",
                code="skill_lifecycle_trust_unavailable",
            )
        package_dir = Path(self.lifecycle_store.package_directory(version.version_id))
        files = self.lifecycle_store.read_directory(package_dir)
        entries = [
            SkillTrustTreeEntry(
                path=path,
                mode="100644",
                object_type="blob",
                object_id=hashlib.sha1(content).hexdigest(),
                size=len(content),
                content=content,
            )
            for path, content in sorted(files.items())
        ]
        receipt = scan_skill_trust_receipt(
            repo_url=version.repo_url,
            sub_path=version.sub_path,
            verified_commit=version.source_ref,
            directory_tree_sha=version.trust_directory_tree_sha,
            entries=entries,
        )
        if receipt.get("packageDigest") != version.package_digest:
            raise SkillValidationError(
                "Historical Git Skill package changed during offline scanning.",
                code="skill_lifecycle_package_mismatch",
            )
        return receipt

    def list_installed_skills(self) -> list[InstalledSkill]:
        """Return installed Skill metadata sorted by installation time."""

        with self._lock:
            self._reconcile_trust_metadata_unlocked()
            return [
                self._installed_skill_from_record(item)
                for item in sorted(
                    self._read_metadata().values(),
                    key=lambda value: float(value.get("installed_at", 0)),
                    reverse=True,
                )
            ]

    def get_skill_content(
        self, skill_id: str, *, version_id: str | None = None
    ) -> str:
        """Read the raw ``SKILL.md`` content for an installed Skill."""

        normalized_skill_id = self._validate_skill_id(skill_id)
        with self._lock:
            installed = self._read_metadata()
            if version_id is None:
                self._reconcile_trust_metadata_unlocked()
            if version_id is None and normalized_skill_id not in installed:
                raise SkillNotFoundError(f"Skill '{normalized_skill_id}' is not installed")

            package_dir = self._resolve_runtime_package_directory_unlocked(
                normalized_skill_id,
                installed.get(normalized_skill_id, {}),
                version_id=version_id,
            )
            skill_md = package_dir / "SKILL.md"
            return skill_md.read_text(encoding="utf-8", errors="replace")

    def get_skill_directory(
        self, skill_id: str, *, version_id: str | None = None
    ) -> Path:
        """Return the validated installed directory for Runtime staging."""

        normalized_skill_id = self._validate_skill_id(skill_id)
        with self._lock:
            installed = self._read_metadata()
            if version_id is None:
                self._reconcile_trust_metadata_unlocked()
            if version_id is None and normalized_skill_id not in installed:
                raise SkillNotFoundError(f"Skill '{normalized_skill_id}' is not installed")
            return self._resolve_runtime_package_directory_unlocked(
                normalized_skill_id,
                installed.get(normalized_skill_id, {}),
                version_id=version_id,
            )

    def bind_skill_versions(self, skill_ids: list[str] | set[str] | tuple[str, ...]) -> dict[str, str]:
        """Freeze active lifecycle versions for one newly-started run."""

        normalized = sorted(
            {self._validate_skill_id(str(item)) for item in skill_ids if str(item).strip()}
        )
        if not self._lifecycle_enabled():
            return {}
        try:
            with self._lock:
                installed = self._read_metadata()
                tracked = [
                    skill_id
                    for skill_id in normalized
                    if skill_id in installed
                    and str(installed[skill_id].get("source_kind") or "git")
                    in {"git", "local_import", "workspace_draft"}
                ]
            return dict(self.lifecycle_store.bind_current_versions(tracked))
        except Exception as exc:
            raise SkillValidationError(
                str(exc),
                code=str(getattr(exc, "code", "skill_lifecycle_version_unavailable")),
                details=getattr(exc, "details", None),
            ) from exc

    def finalize_lifecycle_transaction(self, skill_id: str) -> None:
        if not self._lifecycle_enabled():
            return
        try:
            try:
                receipt = self.lifecycle_store.require_transaction(skill_id)
            except Exception as missing:
                if str(getattr(missing, "code", "")) == "skill_lifecycle_not_found":
                    return
                raise
            if receipt.phase == "metadata_committed":
                receipt = self.lifecycle_store.advance_transaction(
                    skill_id,
                    transaction_id=receipt.transaction_id,
                    expected_phase="metadata_committed",
                    phase="source_projected",
                )
            if receipt.phase == "source_projected":
                state = self.lifecycle_store.require_state(skill_id)
                if receipt.operation == "uninstall":
                    self.lifecycle_store.mark_uninstalled(
                        skill_id, expected_revision=state.revision
                    )
                elif receipt.target_version_id is not None:
                    event_kind = {
                        "install": "installed",
                        "replace": "replaced",
                        "rollback": "rolled_back",
                    }[receipt.operation]
                    self.lifecycle_store.activate_version(
                        skill_id,
                        receipt.target_version_id,
                        expected_revision=state.revision,
                        event_kind=event_kind,
                    )
                receipt = self.lifecycle_store.advance_transaction(
                    skill_id,
                    transaction_id=receipt.transaction_id,
                    expected_phase="source_projected",
                    phase="lifecycle_committed",
                )
            if receipt.phase == "lifecycle_committed":
                self.lifecycle_store.finish_transaction(
                    skill_id, transaction_id=receipt.transaction_id
                )
        except Exception as exc:
            raise SkillInstallError(
                f"Skill lifecycle transaction could not be finalized: {exc}"
            ) from exc

    def recover_lifecycle_transaction(self, skill_id: str) -> bool:
        """Finish a provably committed Git transaction after interruption.

        Workspace drafts and local imports own an additional source projection,
        so their API paths must repair that Store before calling ``finalize``.
        """

        if not self._lifecycle_enabled():
            return False
        normalized_skill_id = self._validate_skill_id(skill_id)
        with self._lock:
            try:
                receipt = self.lifecycle_store.require_transaction(
                    normalized_skill_id
                )
            except Exception as missing:
                if str(getattr(missing, "code", "")) == "skill_lifecycle_not_found":
                    return False
                raise SkillInstallError(
                    "Skill lifecycle recovery receipt is unavailable."
                ) from missing
            if receipt.phase in {"source_projected", "lifecycle_committed"}:
                self.finalize_lifecycle_transaction(normalized_skill_id)
                return True
            if receipt.phase in {"prepared", "archived"}:
                if not self._lifecycle_installed_state_matches_version_unlocked(
                    normalized_skill_id, receipt.previous_version_id
                ):
                    raise SkillInstallError(
                        "Skill lifecycle transaction diverged before the package swap."
                    )
                self.lifecycle_store.abort_transaction(
                    normalized_skill_id,
                    transaction_id=receipt.transaction_id,
                )
                return True
            if receipt.phase == "swapped":
                self._recover_lifecycle_swapped_unlocked(receipt)
                receipt = self.lifecycle_store.require_transaction(
                    normalized_skill_id
                )
            if receipt.phase != "metadata_committed":
                raise SkillInstallError(
                    "Skill lifecycle transaction is incomplete and cannot be recovered automatically."
                )
            version_id = (
                receipt.previous_version_id
                if receipt.operation == "uninstall"
                else receipt.target_version_id
            )
            if not version_id:
                raise SkillInstallError(
                    "Skill lifecycle recovery receipt is incomplete."
                )
            version = self.lifecycle_store.require_version(version_id)
            if version.source_kind != "git":
                return False
            installed = self._read_metadata()
            if receipt.operation == "uninstall":
                if normalized_skill_id in installed or self._safe_skill_dir(
                    normalized_skill_id
                ).exists():
                    raise SkillInstallError(
                        "Git Skill uninstall is not fully committed."
                    )
            else:
                record = installed.get(normalized_skill_id)
                if record is None:
                    raise SkillInstallError(
                        "Git Skill metadata is unavailable during recovery."
                    )
                current = self._installed_skill_from_record(record)
                if (
                    current.source_kind != "git"
                    or current.content_digest != version.package_digest
                    or self._directory_content_digest(
                        self._resolve_package_directory(normalized_skill_id, record)
                    )
                    != version.package_digest
                ):
                    raise SkillInstallError(
                        "Git Skill bytes do not match the pending lifecycle transaction."
                    )
            self.finalize_lifecycle_transaction(normalized_skill_id)
            return True

    def _recover_lifecycle_swapped_unlocked(self, receipt: Any) -> None:
        """Deterministically finish a swap whose intent was durably recorded."""

        skill_id = receipt.skill_id
        installed = self._read_metadata()
        previous_record = installed.get(skill_id)
        target_dir = self._safe_skill_dir(skill_id)
        suffix = receipt.transaction_id.removeprefix("skilltxn_")
        staging_dir = self.installed_dir / f".{skill_id}.lifecycle-recovery-{suffix}"
        backup_dir = self.installed_dir / (
            f".{skill_id}.lifecycle-recovery-backup-{suffix}"
        )
        if receipt.operation == "uninstall":
            try:
                if target_dir.exists() and not backup_dir.exists():
                    target_dir.rename(backup_dir)
                installed.pop(skill_id, None)
                self._write_metadata(installed)
                self._mark_lifecycle_metadata_committed(receipt)
            except Exception:
                if backup_dir.exists() and not target_dir.exists():
                    backup_dir.rename(target_dir)
                restored = self._read_metadata()
                if previous_record is not None:
                    restored[skill_id] = dict(previous_record)
                    self._write_metadata(restored)
                raise
            finally:
                if backup_dir.exists() and not target_dir.exists():
                    self._remove_directory_if_present(backup_dir)
            return
        if not receipt.target_version_id:
            raise SkillInstallError(
                "Skill lifecycle recovery target is unavailable."
            )
        version = self.lifecycle_store.require_version(receipt.target_version_id)
        target_package = Path(
            self.lifecycle_store.package_directory(version.version_id)
        )
        metadata = self._metadata_from_lifecycle_version(version)
        try:
            self._remove_directory_if_present(staging_dir)
            shutil.copytree(target_package, staging_dir)
            if target_dir.exists():
                if backup_dir.exists():
                    self._remove_directory_if_present(target_dir)
                else:
                    target_dir.rename(backup_dir)
            staging_dir.rename(target_dir)
            installed[skill_id] = asdict(metadata)
            self._write_metadata(installed)
            self._mark_lifecycle_metadata_committed(receipt)
        except Exception:
            self._remove_directory_if_present(target_dir)
            if backup_dir.exists():
                backup_dir.rename(target_dir)
            restored = self._read_metadata()
            if previous_record is None:
                restored.pop(skill_id, None)
            else:
                restored[skill_id] = dict(previous_record)
            self._write_metadata(restored)
            raise
        finally:
            self._remove_directory_if_present(staging_dir)
            if target_dir.exists():
                self._remove_directory_if_present(backup_dir)

    def _lifecycle_installed_state_matches_version_unlocked(
        self, skill_id: str, version_id: str | None
    ) -> bool:
        installed = self._read_metadata()
        target_dir = self._safe_skill_dir(skill_id)
        if version_id is None:
            return skill_id not in installed and not target_dir.exists()
        record = installed.get(skill_id)
        if record is None or not target_dir.is_dir():
            return False
        version = self.lifecycle_store.require_version(version_id)
        current = self._installed_skill_from_record(record)
        try:
            actual_digest = self._directory_content_digest(
                self._resolve_package_directory(skill_id, record)
            )
        except Exception:
            return False
        return (
            current.source_kind == version.source_kind
            and current.content_digest == version.package_digest
            and actual_digest == version.package_digest
        )

    def _metadata_from_lifecycle_version(self, version: Any) -> InstalledSkill:
        package_dir = Path(
            self.lifecycle_store.package_directory(version.version_id)
        )
        return self._parse_skill_metadata(
            version.skill_id,
            version.repo_url,
            version.sub_path,
            package_dir / "SKILL.md",
            version.source_ref,
            source_kind=version.source_kind,
            source_id=version.source_id,
            source_revision=version.source_revision,
            content_digest=version.package_digest,
            package_subpath="",
            trust_metadata={
                "trust_state": (
                    "receipt_matched"
                    if version.source_kind in {"git", "local_import"}
                    else "not_applicable"
                ),
                "trust_receipt_id": version.trust_receipt_id,
                "trust_fingerprint": version.trust_fingerprint,
                "trust_risk_level": version.trust_risk_level,
                "trust_status": version.trust_status,
                "trust_install_policy": version.trust_install_policy,
                "trust_compatibility_status": version.trust_compatibility_status,
                "trust_router_eligible": version.trust_router_eligible,
                "trust_package_digest": version.package_digest,
                "trust_directory_tree_sha": version.trust_directory_tree_sha,
            },
        )

    def get_installed_skill(self, skill_id: str) -> InstalledSkill:
        """Return one installed Skill after trust metadata reconciliation."""

        normalized_skill_id = self._validate_skill_id(skill_id)
        with self._lock:
            self._reconcile_trust_metadata_unlocked()
            installed = self._read_metadata()
            record = installed.get(normalized_skill_id)
            if record is None:
                raise SkillNotFoundError(
                    f"Skill '{normalized_skill_id}' is not installed"
                )
            return self._installed_skill_from_record(record)

    def require_activation(
        self,
        skill_id: str,
        *,
        runtime_environment: SkillRuntimeEnvironment | None = None,
        ephemeral_authorizations: Mapping[str, str] | None = None,
        check_runtime: bool = True,
        version_id: str | None = None,
    ) -> InstalledSkill:
        """Apply the final server-side third-party activation gate."""

        try:
            normalized_skill_id = self._validate_skill_id(skill_id)
            with self._lock:
                if version_id is not None:
                    if not self._lifecycle_enabled():
                        raise SkillValidationError(
                            "Versioned Skill activation requires lifecycle management.",
                            code="skill_lifecycle_disabled",
                        )
                    version = self.lifecycle_store.require_version(version_id)
                    if version.skill_id != normalized_skill_id:
                        raise SkillValidationError(
                            "Skill version belongs to another Skill.",
                            code="skill_lifecycle_version_conflict",
                        )
                    package_dir = Path(
                        self.lifecycle_store.package_directory(version_id)
                    )
                    item = self._parse_skill_metadata(
                        normalized_skill_id,
                        version.repo_url,
                        version.sub_path,
                        package_dir / "SKILL.md",
                        version.source_ref,
                        source_kind=version.source_kind,
                        source_id=version.source_id,
                        source_revision=version.source_revision,
                        content_digest=version.package_digest,
                        trust_metadata={
                            "trust_state": (
                                "receipt_matched"
                                if version.source_kind in {"git", "local_import"}
                                else "not_applicable"
                            ),
                            "trust_receipt_id": version.trust_receipt_id,
                            "trust_fingerprint": version.trust_fingerprint,
                            "trust_risk_level": version.trust_risk_level,
                            "trust_status": version.trust_status,
                            "trust_install_policy": version.trust_install_policy,
                            "trust_compatibility_status": version.trust_compatibility_status,
                            "trust_router_eligible": version.trust_router_eligible,
                            "trust_package_digest": version.package_digest,
                            "trust_directory_tree_sha": version.trust_directory_tree_sha,
                        },
                    )
                else:
                    installed = self._read_metadata()
                    record = installed.get(normalized_skill_id)
                    if record is None:
                        raise SkillNotFoundError(
                            f"Skill '{normalized_skill_id}' is not installed"
                        )
                    if self._reconcile_skill_trust_metadata_unlocked(
                        normalized_skill_id, record
                    ):
                        self._write_metadata(installed)
                    item = self._installed_skill_from_record(record)
                if version_id is not None:
                    local_receipt = None
                else:
                    local_receipt = (
                        self._resolve_local_import_receipt_unlocked(item)
                        if item.source_kind == "local_import"
                        and self.trust_service.mode != "off"
                        else None
                    )
            if version_id is not None and item.source_kind in {"git", "local_import"}:
                if (
                    item.trust_status == "blocked"
                    or item.trust_compatibility_status == "unsupported"
                    or not item.trust_receipt_id
                    or not item.trust_fingerprint
                    or version.trust_receipt_snapshot is None
                ):
                    raise SkillValidationError(
                        "Historical Skill trust evidence is not activatable.",
                        code="skill_lifecycle_trust_unavailable",
                    )
                self.trust_service.frozen_receipt_activation_decision(
                    item,
                    version.trust_receipt_snapshot,
                    environment=runtime_environment,
                    ephemeral_authorizations=ephemeral_authorizations,
                    check_runtime=check_runtime,
                )
                return item
            self.trust_service.activation_decision(
                item,
                environment=runtime_environment,
                ephemeral_authorizations=ephemeral_authorizations,
                check_runtime=check_runtime,
                local_receipt=local_receipt,
            )
        except (SkillTrustError, SkillLocalImportError) as exc:
            raise SkillValidationError(
                str(exc),
                code=str(getattr(exc, "code", "") or "skill_trust_receipt_missing"),
                details=dict(getattr(exc, "details", {}) or {}),
            ) from exc
        return item

    def trust_activation_decision(
        self,
        skill_id: str,
        *,
        runtime_environment: SkillRuntimeEnvironment | None = None,
        ephemeral_authorizations: Mapping[str, str] | None = None,
        check_runtime: bool = True,
    ):
        """Return the server-authoritative activation decision for UI projection."""

        normalized_skill_id = self._validate_skill_id(skill_id)
        with self._lock:
            installed = self._read_metadata()
            record = installed.get(normalized_skill_id)
            if record is None:
                raise SkillNotFoundError(
                    f"Skill '{normalized_skill_id}' is not installed"
                )
            if self._reconcile_skill_trust_metadata_unlocked(
                normalized_skill_id, record
            ):
                self._write_metadata(installed)
            item = self._installed_skill_from_record(record)
            local_receipt = (
                self._resolve_local_import_receipt_unlocked(item)
                if item.source_kind == "local_import"
                and self.trust_service.mode != "off"
                else None
            )
        return self.trust_service.activation_decision(
            item,
            environment=runtime_environment,
            ephemeral_authorizations=ephemeral_authorizations,
            check_runtime=check_runtime,
            local_receipt=local_receipt,
        )

    def acknowledge_trust(
        self,
        skill_id: str,
        *,
        expected_trust_fingerprint: str,
        confirmed: bool,
    ) -> InstalledSkill:
        """Persist one exact Git or local-import acknowledgement."""

        normalized_skill_id = self._validate_skill_id(skill_id)
        expected = str(expected_trust_fingerprint or "").casefold()
        with self._lock:
            installed = self._read_metadata()
            record = installed.get(normalized_skill_id)
            if record is None:
                raise SkillNotFoundError(
                    f"Skill '{normalized_skill_id}' is not installed"
                )
            if self._reconcile_skill_trust_metadata_unlocked(
                normalized_skill_id, record
            ):
                self._write_metadata(installed)
            item = self._installed_skill_from_record(record)
            if (
                item.source_kind not in {"git", "local_import"}
                or item.trust_state != "receipt_matched"
                or item.trust_fingerprint != expected
            ):
                raise SkillTrustError(
                    "Installed Skill trust receipt changed. Reload before confirming.",
                    code="skill_trust_candidate_stale",
                    details={
                        "skillId": item.skill_id,
                        "trustFingerprint": item.trust_fingerprint,
                    },
                )
            local_receipt = (
                self._resolve_local_import_receipt_unlocked(item)
                if item.source_kind == "local_import"
                else None
            )
        self.trust_service.acknowledge(
            skill_id=item.skill_id,
            trust_fingerprint=expected,
            confirmed=confirmed,
            receipt=local_receipt,
        )
        return item

    @staticmethod
    def _workspace_skill_id(draft_id: str) -> str:
        digest = hashlib.sha256(draft_id.encode("utf-8")).hexdigest()[:20]
        return f"workspace-{digest}"

    def _find_workspace_skill_id(
        self,
        installed: dict[str, dict[str, object]],
        draft_id: str,
    ) -> str | None:
        expected_url = f"workspace://draft/{draft_id}"
        matches: list[str] = []
        for candidate_id, record in installed.items():
            item = self._installed_skill_from_record(record)
            if (
                item.source_kind == "workspace_draft"
                and item.source_id == draft_id
            ) or item.repo_url == expected_url:
                matches.append(candidate_id)
        if len(matches) > 1:
            raise SkillInstallError(
                "Workspace Skill draft has multiple installed mappings."
            )
        return matches[0] if matches else None

    def _installed_skill_from_record(
        self, record: dict[str, object]
    ) -> InstalledSkill:
        repo_url = str(record.get("repo_url") or "")
        source_kind = str(record.get("source_kind") or "").strip()
        source_id_value = record.get("source_id")
        source_id = (
            str(source_id_value).strip() if source_id_value is not None else None
        )
        source_revision_value = record.get("source_revision")
        source_revision = (
            int(source_revision_value)
            if isinstance(source_revision_value, int)
            and not isinstance(source_revision_value, bool)
            and source_revision_value > 0
            else None
        )
        if not source_kind:
            if repo_url.startswith("workspace://draft/"):
                source_kind = "workspace_draft"
                source_id = source_id or repo_url.removeprefix("workspace://draft/")
            elif repo_url.startswith("plugin://"):
                source_kind = "plugin"
                source_id = source_id or repo_url.removeprefix("plugin://").split(
                    "/", 1
                )[0]
            else:
                source_kind = "git"
        content_digest = str(record.get("content_digest") or "").lower()
        if not re.fullmatch(r"[a-f0-9]{64}", content_digest):
            content_digest = ""
        package_subpath = str(record.get("package_subpath") or "").strip()
        try:
            package_subpath = self._validate_sub_path(package_subpath)
        except SkillValidationError:
            package_subpath = ""
        source_ref_value = record.get("source_ref")
        trust_verified_at_value = record.get("trust_verified_at")
        return InstalledSkill(
            skill_id=str(record.get("skill_id") or ""),
            name=str(record.get("name") or ""),
            description=str(record.get("description") or ""),
            repo_url=repo_url,
            sub_path=str(record.get("sub_path") or ""),
            installed_at=float(record.get("installed_at") or 0),
            source_ref=(
                str(source_ref_value) if source_ref_value is not None else None
            ),
            source_kind=source_kind,
            source_id=source_id,
            source_revision=source_revision,
            content_digest=content_digest,
            package_subpath=package_subpath,
            trust_state=str(record.get("trust_state") or "not_applicable"),
            trust_receipt_id=_optional_string(record.get("trust_receipt_id")),
            trust_fingerprint=_optional_string(record.get("trust_fingerprint")),
            trust_risk_level=_optional_string(record.get("trust_risk_level")),
            trust_status=_optional_string(record.get("trust_status")),
            trust_install_policy=_optional_string(
                record.get("trust_install_policy")
            ),
            trust_compatibility_status=_optional_string(
                record.get("trust_compatibility_status")
            ),
            trust_router_eligible=bool(record.get("trust_router_eligible", False)),
            trust_package_digest=_optional_string(
                record.get("trust_package_digest")
            ),
            trust_directory_tree_sha=_optional_string(
                record.get("trust_directory_tree_sha")
            ),
            trust_verified_at=(
                float(trust_verified_at_value)
                if isinstance(trust_verified_at_value, (int, float))
                and not isinstance(trust_verified_at_value, bool)
                and trust_verified_at_value > 0
                else None
            ),
        )

    def _reconcile_trust_metadata_unlocked(self) -> None:
        if self._trust_reconciled or self.trust_service.mode == "off":
            return
        installed = self._read_metadata()
        changed = False
        for skill_id, record in installed.items():
            changed = (
                self._reconcile_skill_trust_metadata_unlocked(skill_id, record)
                or changed
            )
        if changed:
            self._write_metadata(installed)
        self._trust_reconciled = True

    def _reconcile_skill_trust_metadata_unlocked(
        self,
        skill_id: str,
        record: dict[str, object],
    ) -> bool:
        item = self._installed_skill_from_record(record)
        if self.trust_service.mode == "off":
            return False
        package_dir: Path | None
        try:
            package_dir = self._resolve_package_directory(skill_id, record)
        except SkillManagerError:
            package_dir = None
        if item.source_kind == "local_import":
            try:
                receipt = self._resolve_local_import_receipt_unlocked(
                    item, package_dir=package_dir
                )
                trust_metadata = self.trust_service.receipt_metadata(
                    receipt, verified_at=item.trust_verified_at or time.time()
                )
            except (SkillLocalImportError, SkillTrustError, ValueError):
                trust_metadata = self.trust_service.unverified_metadata()
        elif item.source_kind == "git":
            trust_metadata = self.trust_service.reconcile_metadata(
                record=record,
                package_dir=package_dir,
            )
        else:
            return False
        if not any(
            record.get(key) != value for key, value in trust_metadata.items()
        ):
            return False
        record.update(trust_metadata)
        return True

    def _resolve_local_import_receipt_unlocked(
        self,
        item: InstalledSkill,
        *,
        package_dir: Path | None = None,
        allow_uninstalled: bool = False,
    ) -> dict[str, Any]:
        if self.local_import_store is None or item.source_kind != "local_import":
            raise SkillTrustError(
                "Local Skill trust receipt is unavailable.",
                code="skill_trust_receipt_missing",
            )
        import_id = str(item.source_id or "")
        try:
            record = self.local_import_store.require(import_id)
        except SkillLocalImportError as exc:
            raise SkillTrustError(
                "Local Skill trust receipt is unavailable.",
                code="skill_trust_receipt_missing",
            ) from exc
        if (
            record.content_revision != item.source_revision
            or record.package_digest != item.content_digest
            or (
                not allow_uninstalled
                and record.installed_skill_id != item.skill_id
            )
        ):
            raise SkillTrustError(
                "Installed local Skill no longer matches its import receipt.",
                code="skill_trust_receipt_missing",
            )
        resolved_package = package_dir
        if resolved_package is None:
            metadata = self._read_metadata().get(item.skill_id)
            if metadata is None:
                raise SkillTrustError(
                    "Installed local Skill metadata is unavailable.",
                    code="skill_trust_receipt_missing",
                )
            resolved_package = self._resolve_package_directory(
                item.skill_id, metadata
            )
        if self._directory_content_digest(resolved_package) != item.content_digest:
            raise SkillTrustError(
                "Installed local Skill bytes no longer match its import receipt.",
                code="skill_trust_package_mismatch",
            )
        return self.trust_service.validate_local_receipt(
            record.trust_receipt,
            import_id=record.import_id,
            import_revision=record.content_revision,
            package_digest=record.package_digest,
        )

    def _resolve_package_directory(
        self, skill_id: str, record: dict[str, object]
    ) -> Path:
        target = self._safe_skill_dir(skill_id)
        if not target.exists() or not target.is_dir():
            raise SkillNotFoundError(f"Skill '{skill_id}' directory is unavailable")
        item = self._installed_skill_from_record(record)
        if item.package_subpath:
            nested = (target / item.package_subpath).resolve()
            if target.resolve() not in nested.parents:
                raise SkillValidationError(
                    f"Unsafe package path for Skill '{skill_id}'"
                )
            if (nested / "SKILL.md").is_file():
                return nested
        if (target / "SKILL.md").is_file():
            return target
        candidates = [
            child
            for child in target.iterdir()
            if child.is_dir()
            and target.resolve() in child.resolve().parents
            and (child / "SKILL.md").is_file()
        ]
        if len(candidates) == 1:
            return candidates[0]
        raise SkillNotFoundError(f"Skill '{skill_id}' is missing SKILL.md")

    @staticmethod
    def _directory_content_digest(package_dir: Path) -> str:
        skill_md = package_dir / "SKILL.md"
        if not skill_md.is_file():
            raise SkillNotFoundError("Installed Skill package is missing SKILL.md")
        files: dict[str, bytes] = {}
        for path in package_dir.rglob("*"):
            if path.is_file() and path != skill_md:
                files[path.relative_to(package_dir).as_posix()] = path.read_bytes()
        return compute_package_digest(skill_md.read_bytes(), files)

    def _receipt_path(self, skill_id: str) -> Path:
        normalized_skill_id = self._validate_skill_id(skill_id)
        return self.installed_dir / f".workspace-install-{normalized_skill_id}.receipt.json"

    def _write_install_receipt(self, receipt: SkillInstallReceipt) -> None:
        path = self._receipt_path(receipt.skill_id)
        temporary_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            encoded = json.dumps(
                asdict(receipt), ensure_ascii=False, indent=2
            ).encode("utf-8")
            with temporary_path.open("xb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
        finally:
            temporary_path.unlink(missing_ok=True)

    def _read_install_receipt(self, skill_id: str) -> SkillInstallReceipt | None:
        path = self._receipt_path(skill_id)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SkillInstallError("Workspace Skill install receipt is invalid.") from exc
        if not isinstance(raw, dict):
            raise SkillInstallError("Workspace Skill install receipt is invalid.")
        try:
            receipt = SkillInstallReceipt(**raw)
        except (TypeError, ValueError) as exc:
            raise SkillInstallError("Workspace Skill install receipt is invalid.") from exc
        expected_staging = f".{receipt.skill_id}.staging-{receipt.transaction_id}"
        expected_backup = f".{receipt.skill_id}.backup-{receipt.transaction_id}"
        if (
            receipt.version != 1
            or receipt.skill_id != skill_id
            or receipt.phase not in {"prepared", "swapped", "committed"}
            or receipt.source_kind not in {"workspace_draft", "local_import"}
            or not re.fullmatch(r"[a-f0-9]{32}", receipt.transaction_id)
            or not re.fullmatch(r"[a-f0-9]{64}", receipt.content_digest)
            or (
                receipt.previous_content_digest is not None
                and (
                    not isinstance(receipt.previous_content_digest, str)
                    or not re.fullmatch(
                        r"[a-f0-9]{64}", receipt.previous_content_digest
                    )
                )
            )
            or receipt.staging_name != expected_staging
            or receipt.backup_name != expected_backup
            or not isinstance(receipt.installed_metadata, dict)
            or (
                receipt.previous_metadata is not None
                and not isinstance(receipt.previous_metadata, dict)
            )
            or (
                receipt.source_revision is not None
                and (
                    isinstance(receipt.source_revision, bool)
                    or not isinstance(receipt.source_revision, int)
                    or receipt.source_revision < 1
                )
            )
        ):
            raise SkillInstallError("Workspace Skill install receipt is invalid.")
        self._validate_sub_path(receipt.package_subpath)
        installed_item = self._installed_skill_from_record(
            receipt.installed_metadata
        )
        if (
            installed_item.skill_id != receipt.skill_id
            or installed_item.source_kind != receipt.source_kind
            or installed_item.source_id != receipt.source_id
            or installed_item.source_revision != receipt.source_revision
            or installed_item.content_digest != receipt.content_digest
            or installed_item.package_subpath != receipt.package_subpath
        ):
            raise SkillInstallError("Workspace Skill install receipt is invalid.")
        return receipt

    def _transaction_dir(self, name: str, expected_prefix: str) -> Path:
        if Path(name).name != name or not name.startswith(expected_prefix):
            raise SkillInstallError("Workspace Skill install receipt path is invalid.")
        target = (self.installed_dir / name).resolve()
        if target.parent != self.installed_dir.resolve():
            raise SkillInstallError("Workspace Skill install receipt path is invalid.")
        return target

    def _recover_workspace_install_receipt(
        self, skill_id: str, draft_id: str
    ) -> None:
        self._recover_install_receipt(
            skill_id,
            source_kind="workspace_draft",
            source_id=draft_id,
        )

    def _recover_install_receipt(
        self,
        skill_id: str,
        *,
        source_kind: Literal["workspace_draft", "local_import"],
        source_id: str,
    ) -> None:
        receipt = self._read_install_receipt(skill_id)
        if receipt is None:
            return
        if receipt.source_kind != source_kind or receipt.source_id != source_id:
            raise SkillInstallError(
                "Skill install receipt belongs to another immutable source."
            )
        target_dir = self._safe_skill_dir(skill_id)
        staging_dir = self._transaction_dir(
            receipt.staging_name, f".{skill_id}.staging-"
        )
        backup_dir = self._transaction_dir(
            receipt.backup_name, f".{skill_id}.backup-"
        )
        target_matches = False
        package_dir = target_dir / receipt.package_subpath
        if (package_dir / "SKILL.md").is_file():
            target_matches = (
                self._directory_content_digest(package_dir) == receipt.content_digest
            )
        installed = self._read_metadata()
        if target_matches:
            installed[skill_id] = dict(receipt.installed_metadata)
            self._write_metadata(installed)
        else:
            previous_target_matches = False
            previous_digest = receipt.previous_content_digest
            if receipt.previous_metadata is not None and previous_digest:
                try:
                    previous_package_dir = self._resolve_package_directory(
                        skill_id, receipt.previous_metadata
                    )
                    previous_target_matches = (
                        self._directory_content_digest(previous_package_dir)
                        == previous_digest
                    )
                except SkillNotFoundError:
                    previous_target_matches = False
            if previous_target_matches:
                installed[skill_id] = dict(receipt.previous_metadata or {})
                self._write_metadata(installed)
            elif backup_dir.exists():
                self._remove_directory_if_present(target_dir)
                backup_dir.rename(target_dir)
                if receipt.previous_metadata is None:
                    installed.pop(skill_id, None)
                else:
                    installed[skill_id] = dict(receipt.previous_metadata)
                self._write_metadata(installed)
            elif receipt.previous_metadata is not None:
                raise SkillInstallError(
                    "Workspace Skill upgrade backup is unavailable; retry recovery "
                    "after restoring the backup."
                )
            else:
                self._remove_directory_if_present(target_dir)
                installed.pop(skill_id, None)
                self._write_metadata(installed)
        if self._lifecycle_enabled():
            try:
                lifecycle_receipt = self.lifecycle_store.require_transaction(
                    skill_id
                )
            except Exception as missing:
                if str(getattr(missing, "code", "")) != "skill_lifecycle_not_found":
                    raise
                lifecycle_receipt = None
            if lifecycle_receipt is not None:
                if target_matches:
                    if lifecycle_receipt.phase == "archived":
                        self._mark_lifecycle_swapped(lifecycle_receipt)
                        lifecycle_receipt = self.lifecycle_store.require_transaction(
                            skill_id
                        )
                    if lifecycle_receipt.phase == "swapped":
                        self._mark_lifecycle_metadata_committed(
                            lifecycle_receipt
                        )
                    elif lifecycle_receipt.phase not in {
                        "metadata_committed",
                        "source_projected",
                        "lifecycle_committed",
                    }:
                        raise SkillInstallError(
                            "Skill install and lifecycle recovery receipts disagree."
                        )
                elif lifecycle_receipt.phase in {
                    "prepared",
                    "archived",
                    "swapped",
                }:
                    self._abort_lifecycle_transaction(lifecycle_receipt)
                else:
                    raise SkillInstallError(
                        "Committed Skill lifecycle metadata cannot be rolled back."
                    )
        self._remove_directory_if_present(staging_dir)
        self._remove_directory_if_present(backup_dir)
        self._receipt_path(skill_id).unlink(missing_ok=True)

    def _lifecycle_enabled(self) -> bool:
        return bool(
            self.lifecycle_store is not None
            and getattr(self.lifecycle_store, "enabled", False)
        )

    def _resolve_runtime_package_directory_unlocked(
        self,
        skill_id: str,
        record: Mapping[str, object],
        *,
        version_id: str | None,
    ) -> Path:
        if version_id is None:
            return self._resolve_package_directory(skill_id, record)
        if not self._lifecycle_enabled():
            raise SkillValidationError(
                "Versioned Skill access requires lifecycle management.",
                code="skill_lifecycle_disabled",
            )
        try:
            version = self.lifecycle_store.require_version(version_id)
            if version.skill_id != skill_id:
                raise SkillValidationError(
                    "Skill version belongs to another installed Skill.",
                    code="skill_lifecycle_version_conflict",
                )
            return Path(self.lifecycle_store.package_directory(version_id))
        except SkillValidationError:
            raise
        except Exception as exc:
            raise SkillValidationError(
                str(exc),
                code=str(getattr(exc, "code", "skill_lifecycle_version_unavailable")),
                details=getattr(exc, "details", None),
            ) from exc

    def _stage_lifecycle_version(
        self,
        metadata: InstalledSkill,
        package_dir: Path,
        *,
        event_kind: str,
        quality_evidence_status: str = "not_applicable",
        quality_required: bool = False,
        quality_status: str | None = None,
        quality_decision_id: str | None = None,
        quality_run_id: str | None = None,
    ) -> Any | None:
        if not self._lifecycle_enabled() or metadata.source_kind not in {
            "git", "local_import", "workspace_draft"
        }:
            return None
        try:
            files = self.lifecycle_store.read_directory(package_dir)
            trust_receipt_snapshot = self._lifecycle_trust_receipt_snapshot(
                metadata
            )
            return self.lifecycle_store.stage_version(
                installed=metadata,
                files=files,
                event_kind=event_kind,
                quality_evidence_status=quality_evidence_status,
                quality_required=quality_required,
                quality_status=quality_status,
                quality_decision_id=quality_decision_id,
                quality_run_id=quality_run_id,
                trust_receipt_snapshot=trust_receipt_snapshot,
            )
        except Exception as exc:
            raise SkillInstallError(
                f"Skill lifecycle version could not be archived: {exc}"
            ) from exc

    def _lifecycle_trust_receipt_snapshot(
        self, metadata: InstalledSkill
    ) -> dict[str, Any] | None:
        try:
            if metadata.source_kind == "git":
                if not metadata.trust_receipt_id:
                    return None
                receipt = self.trust_service.receipt_by_id(
                    metadata.trust_receipt_id
                )
                return self.trust_service.validate_frozen_git_receipt(
                    receipt,
                    receipt_id=metadata.trust_receipt_id,
                    trust_fingerprint=str(metadata.trust_fingerprint or ""),
                    package_digest=str(metadata.content_digest or ""),
                    directory_tree_sha=metadata.trust_directory_tree_sha,
                )
            if metadata.source_kind == "local_import":
                if self.local_import_store is None or not metadata.source_id:
                    return None
                record = self.local_import_store.require(metadata.source_id)
                return self.trust_service.validate_local_receipt(
                    record.trust_receipt,
                    import_id=metadata.source_id,
                    import_revision=metadata.source_revision,
                    package_digest=str(metadata.content_digest or ""),
                )
        except (SkillTrustError, SkillLocalImportError):
            try:
                for version in self.lifecycle_store.list_versions(
                    metadata.skill_id
                ):
                    if (
                        version.source_kind == metadata.source_kind
                        and version.source_id == metadata.source_id
                        and version.source_revision == metadata.source_revision
                        and version.source_ref == metadata.source_ref
                        and version.package_digest == metadata.content_digest
                        and version.trust_fingerprint == metadata.trust_fingerprint
                        and version.trust_receipt_snapshot is not None
                    ):
                        return dict(version.trust_receipt_snapshot)
            except Exception:
                pass
            raise
        return None

    def _prepare_lifecycle_install(
        self,
        *,
        skill_id: str,
        previous_record: Mapping[str, object] | None,
        target_metadata: InstalledSkill,
        target_package_dir: Path,
        operation: Literal["install", "replace", "rollback"] | None = None,
        target_version_id: str | None = None,
        quality_evidence_status: str = "not_applicable",
        quality_required: bool = False,
        quality_status: str | None = None,
        quality_decision_id: str | None = None,
        quality_run_id: str | None = None,
    ) -> Any | None:
        if not self._lifecycle_enabled() or target_metadata.source_kind not in {
            "git", "local_import", "workspace_draft"
        }:
            return None
        previous_version_id: str | None = None
        if previous_record is not None:
            previous = self._installed_skill_from_record(previous_record)
            previous_package = self._resolve_package_directory(skill_id, previous_record)
            try:
                state = self.lifecycle_store.require_state(skill_id)
            except Exception as missing:
                if str(getattr(missing, "code", "")) != "skill_lifecycle_not_found":
                    raise
                state = None
            previous_version = None
            if state is not None and state.current_version_id:
                current_version = self.lifecycle_store.require_version(
                    state.current_version_id
                )
                if (
                    current_version.package_digest == previous.content_digest
                    and current_version.source_kind == previous.source_kind
                    and current_version.source_id == previous.source_id
                    and current_version.source_revision == previous.source_revision
                    and current_version.source_ref == previous.source_ref
                    and self._directory_content_digest(previous_package)
                    == previous.content_digest
                ):
                    previous_version = current_version
            if previous_version is None:
                previous_version = self._stage_lifecycle_version(
                    previous, previous_package, event_kind="version_archived"
                )
                state = self.lifecycle_store.require_state(skill_id)
            assert state is not None
            if state.current_version_id is None:
                state = self.lifecycle_store.activate_version(
                    skill_id,
                    previous_version.version_id,
                    expected_revision=state.revision,
                    event_kind="recovered",
                )
            elif state.current_version_id != previous_version.version_id:
                raise SkillInstallError(
                    "Installed Skill differs from the lifecycle current version."
                )
            previous_version_id = previous_version.version_id
        if target_version_id is not None:
            target_version = self.lifecycle_store.require_version(
                target_version_id
            )
            if (
                target_version.skill_id != skill_id
                or target_version.package_digest != target_metadata.content_digest
                or target_version.source_kind != target_metadata.source_kind
                or target_version.source_id != target_metadata.source_id
                or target_version.source_revision != target_metadata.source_revision
                or target_version.source_ref != target_metadata.source_ref
                or Path(self.lifecycle_store.package_directory(target_version_id))
                != Path(target_package_dir)
            ):
                raise SkillInstallError(
                    "Historical Skill lifecycle target changed before rollback."
                )
        else:
            target_version = self._stage_lifecycle_version(
                target_metadata,
                target_package_dir,
                event_kind="version_prepared",
                quality_evidence_status=quality_evidence_status,
                quality_required=quality_required,
                quality_status=quality_status,
                quality_decision_id=quality_decision_id,
                quality_run_id=quality_run_id,
            )
        state = self.lifecycle_store.require_state(skill_id)
        receipt = self.lifecycle_store.prepare_transaction(
            skill_id=skill_id,
            operation=(
                operation
                or ("replace" if previous_version_id is not None else "install")
            ),
            previous_version_id=previous_version_id,
            target_version_id=target_version.version_id,
            expected_state_revision=state.revision,
        )
        return self.lifecycle_store.advance_transaction(
            skill_id,
            transaction_id=receipt.transaction_id,
            expected_phase="prepared",
            phase="archived",
        )

    def _prepare_lifecycle_uninstall(
        self,
        *,
        skill_id: str,
        previous_record: Mapping[str, object],
    ) -> Any | None:
        if not self._lifecycle_enabled():
            return None
        previous = self._installed_skill_from_record(previous_record)
        if previous.source_kind not in {"git", "local_import", "workspace_draft"}:
            return None
        package = self._resolve_package_directory(skill_id, previous_record)
        try:
            state = self.lifecycle_store.require_state(skill_id)
        except Exception as missing:
            if str(getattr(missing, "code", "")) != "skill_lifecycle_not_found":
                raise
            state = None
        version = None
        if state is not None and state.current_version_id:
            current_version = self.lifecycle_store.require_version(
                state.current_version_id
            )
            if (
                current_version.package_digest == previous.content_digest
                and current_version.source_kind == previous.source_kind
                and current_version.source_id == previous.source_id
                and current_version.source_revision == previous.source_revision
                and current_version.source_ref == previous.source_ref
                and self._directory_content_digest(package) == previous.content_digest
            ):
                version = current_version
        if version is None:
            version = self._stage_lifecycle_version(
                previous, package, event_kind="version_archived"
            )
            state = self.lifecycle_store.require_state(skill_id)
        assert state is not None
        if state.current_version_id is None:
            state = self.lifecycle_store.activate_version(
                skill_id,
                version.version_id,
                expected_revision=state.revision,
                event_kind="recovered",
            )
        elif state.current_version_id != version.version_id:
            raise SkillInstallError(
                "Installed Skill differs from the lifecycle current version."
            )
        receipt = self.lifecycle_store.prepare_transaction(
            skill_id=skill_id,
            operation="uninstall",
            previous_version_id=version.version_id,
            target_version_id=None,
            expected_state_revision=state.revision,
        )
        return self.lifecycle_store.advance_transaction(
            skill_id,
            transaction_id=receipt.transaction_id,
            expected_phase="prepared",
            phase="archived",
        )

    def _mark_lifecycle_metadata_committed(self, receipt: Any | None) -> None:
        if receipt is None:
            return
        self.lifecycle_store.advance_transaction(
            receipt.skill_id,
            transaction_id=receipt.transaction_id,
            expected_phase="swapped",
            phase="metadata_committed",
        )

    def _mark_lifecycle_swapped(self, receipt: Any | None) -> None:
        if receipt is None:
            return
        self.lifecycle_store.advance_transaction(
            receipt.skill_id,
            transaction_id=receipt.transaction_id,
            expected_phase="archived",
            phase="swapped",
        )

    def _abort_lifecycle_transaction(self, receipt: Any | None) -> None:
        if receipt is None:
            return
        try:
            self.lifecycle_store.abort_transaction(
                receipt.skill_id, transaction_id=receipt.transaction_id
            )
        except Exception as exc:
            raise SkillInstallError(
                "Skill install failed and lifecycle recovery is incomplete."
            ) from exc

    @staticmethod
    def _remove_directory_if_present(path: Path) -> None:
        if path.exists():
            shutil.rmtree(path)

    def _ensure_dirs(self) -> None:
        self.installed_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        if not self.metadata_path.exists():
            self._write_metadata({})

    def _read_metadata(self) -> dict[str, dict[str, object]]:
        try:
            self._ensure_dirs_for_read()
            raw = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SkillInstallError(
                "Installed Skill metadata is unavailable or corrupt."
            ) from exc

        if isinstance(raw, dict) and "skills" in raw:
            skills = raw.get("skills")
            if isinstance(skills, dict) and all(
                isinstance(key, str) and isinstance(value, dict)
                for key, value in skills.items()
            ):
                return skills  # type: ignore[return-value]
            raise SkillInstallError(
                "Installed Skill metadata is unavailable or corrupt."
            )
        if isinstance(raw, dict) and all(
            isinstance(key, str) and isinstance(value, dict)
            for key, value in raw.items()
        ):
            return raw  # backward-compatible flat shape
        raise SkillInstallError("Installed Skill metadata is unavailable or corrupt.")

    def _write_metadata(self, skills: dict[str, dict[str, object]]) -> None:
        self.installed_dir.mkdir(parents=True, exist_ok=True)
        payload = {"skills": skills}
        temporary_path = self.installed_dir / f".installed-{uuid.uuid4().hex}.json.tmp"
        try:
            encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            with temporary_path.open("xb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.metadata_path)
        finally:
            temporary_path.unlink(missing_ok=True)

    def _ensure_dirs_for_read(self) -> None:
        self.installed_dir.mkdir(parents=True, exist_ok=True)
        if not self.metadata_path.exists():
            self.metadata_path.write_text('{"skills": {}}', encoding="utf-8")

    def _safe_skill_dir(self, skill_id: str) -> Path:
        target_dir = (self.installed_dir / skill_id).resolve()
        installed_root = self.installed_dir.resolve()
        if target_dir != installed_root and installed_root in target_dir.parents:
            return target_dir
        raise SkillValidationError(f"Unsafe Skill id '{skill_id}'")

    def _git_sparse_clone(
        self,
        repo_url: str,
        sub_path: str,
        checkout_dir: Path,
        source_ref: str | None = None,
    ) -> None:
        if source_ref:
            self._run_command(["git", "init", str(checkout_dir)])
            self._run_command(
                ["git", "-C", str(checkout_dir), "remote", "add", "origin", repo_url]
            )
            self._run_command(
                ["git", "-C", str(checkout_dir), "sparse-checkout", "init", "--cone"]
            )
            if sub_path:
                self._run_command(
                    ["git", "-C", str(checkout_dir), "sparse-checkout", "set", sub_path]
                )
            self._run_command(
                ["git", "-C", str(checkout_dir), "fetch", "--depth", "1", "origin", source_ref]
            )
            self._run_command(
                ["git", "-C", str(checkout_dir), "checkout", "--detach", "FETCH_HEAD"]
            )
            return

        clone_command = [
            "git",
            "clone",
            "--depth",
            "1",
            "--filter=blob:none",
            "--sparse",
            repo_url,
            str(checkout_dir),
        ]
        self._run_command(clone_command)

        if sub_path:
            self._run_command(
                ["git", "-C", str(checkout_dir), "sparse-checkout", "set", sub_path]
            )

    def _run_command(self, command: list[str]) -> None:
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.git_timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise SkillInstallError("git is not available on this server") from exc
        except subprocess.TimeoutExpired as exc:
            raise SkillInstallError("Skill install timed out") from exc

        if completed.returncode != 0:
            stderr = completed.stderr.strip() or completed.stdout.strip()
            raise SkillInstallError(stderr or "git command failed")

    def _parse_skill_metadata(
        self,
        skill_id: str,
        repo_url: str,
        sub_path: str,
        skill_md: Path,
        source_ref: str | None = None,
        *,
        source_kind: str = "git",
        source_id: str | None = None,
        source_revision: int | None = None,
        content_digest: str | None = None,
        package_subpath: str = "",
        trust_metadata: Mapping[str, Any] | None = None,
    ) -> InstalledSkill:
        content = skill_md.read_text(encoding="utf-8", errors="replace")
        frontmatter = self._parse_frontmatter(content)
        heading = self._first_heading(content)
        description = (
            str(frontmatter.get("description", "")).strip()
            or self._first_paragraph(content)
            or f"Installed Skill from {repo_url}"
        )
        name = (
            str(frontmatter.get("name", "")).strip()
            or heading
            or self._title_from_path(sub_path)
        )

        trust_values = dict(trust_metadata or {})
        return InstalledSkill(
            skill_id=skill_id,
            name=name[:120],
            description=description[:500],
            repo_url=repo_url,
            sub_path=sub_path,
            installed_at=time.time(),
            source_ref=source_ref,
            source_kind=source_kind,
            source_id=source_id or f"{repo_url}#{sub_path}",
            source_revision=source_revision,
            content_digest=content_digest or "",
            package_subpath=package_subpath,
            trust_state=str(
                trust_values.get("trust_state")
                or ("unverified_legacy" if source_kind == "git" else "not_applicable")
            ),
            trust_receipt_id=_optional_string(trust_values.get("trust_receipt_id")),
            trust_fingerprint=_optional_string(trust_values.get("trust_fingerprint")),
            trust_risk_level=_optional_string(trust_values.get("trust_risk_level")),
            trust_status=_optional_string(trust_values.get("trust_status")),
            trust_install_policy=_optional_string(
                trust_values.get("trust_install_policy")
            ),
            trust_compatibility_status=_optional_string(
                trust_values.get("trust_compatibility_status")
            ),
            trust_router_eligible=bool(
                trust_values.get("trust_router_eligible", False)
            ),
            trust_package_digest=_optional_string(
                trust_values.get("trust_package_digest")
            ),
            trust_directory_tree_sha=_optional_string(
                trust_values.get("trust_directory_tree_sha")
            ),
            trust_verified_at=(
                float(trust_values["trust_verified_at"])
                if isinstance(trust_values.get("trust_verified_at"), (int, float))
                else None
            ),
        )

    @staticmethod
    def _parse_frontmatter(content: str) -> dict[str, str]:
        if not content.startswith("---"):
            return {}

        lines = content.splitlines()
        values: dict[str, str] = {}
        for line in lines[1:]:
            if line.strip() == "---":
                break
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip().lower()
            if key in {"name", "description"}:
                values[key] = value.strip().strip('"').strip("'")
        return values

    @staticmethod
    def _first_heading(content: str) -> str:
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                return stripped.lstrip("#").strip()
        return ""

    @staticmethod
    def _first_paragraph(content: str) -> str:
        in_frontmatter = content.startswith("---")
        for line in content.splitlines():
            stripped = line.strip()
            if in_frontmatter:
                if stripped == "---":
                    in_frontmatter = False
                continue
            if not stripped or stripped.startswith("#") or stripped.startswith("---"):
                continue
            return stripped
        return ""

    @staticmethod
    def _title_from_path(sub_path: str) -> str:
        last_part = (sub_path.rstrip("/") or "skill").split("/")[-1]
        return last_part.replace("-", " ").replace("_", " ").title()

    def _validate_repo_url(self, repo_url: str) -> str:
        raw = repo_url.strip()
        if not raw:
            raise SkillValidationError("repo_url is required")

        if self.allow_local_repos:
            path = Path(raw)
            if path.exists():
                return str(path.resolve())
            parsed = urlparse(raw)
            if parsed.scheme == "file":
                local_path = Path(parsed.path)
                if local_path.exists():
                    return raw

        parsed = urlparse(raw)
        if parsed.scheme != "https" or parsed.netloc.lower() != "github.com":
            raise SkillValidationError("Only https://github.com repositories are allowed")

        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 2:
            raise SkillValidationError("GitHub repo URL must include owner and repo")

        owner, repo = parts[:2]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner):
            raise SkillValidationError("Invalid GitHub owner")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+(?:\.git)?", repo):
            raise SkillValidationError("Invalid GitHub repo")

        return f"https://github.com/{owner}/{repo.removesuffix('.git')}"

    @staticmethod
    def _validate_sub_path(sub_path: str) -> str:
        normalized = sub_path.strip().strip("/\\")
        if not normalized:
            return ""
        if "\\" in normalized:
            normalized = normalized.replace("\\", "/")
        parts = [part for part in normalized.split("/") if part]
        if any(part in {"", ".", ".."} for part in parts):
            raise SkillValidationError("Invalid Skill sub_path")
        if any(not re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts):
            raise SkillValidationError("Invalid Skill sub_path")
        return "/".join(parts)

    @staticmethod
    def _validate_source_ref(source_ref: str | None) -> str | None:
        if source_ref is None:
            return None
        normalized = source_ref.strip().lower()
        if not re.fullmatch(r"[a-f0-9]{40}", normalized):
            raise SkillValidationError("Skill source ref must be a full Git commit SHA")
        return normalized

    @staticmethod
    def _validate_skill_id(skill_id: str) -> str:
        normalized = skill_id.strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,160}", normalized):
            raise SkillValidationError("Invalid Skill id")
        return normalized

    @staticmethod
    def _build_skill_id(repo_url: str, sub_path: str) -> str:
        parsed = urlparse(repo_url)
        if parsed.scheme in {"http", "https"}:
            raw = f"{parsed.path.strip('/')}/{sub_path}".strip("/")
        else:
            raw = f"{Path(repo_url).name}/{sub_path}".strip("/")

        slug = re.sub(r"[^a-zA-Z0-9]+", "-", raw).strip("-").lower()
        return slug[:140] or "skill"


def _optional_string(value: object) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None
