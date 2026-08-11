from __future__ import annotations

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
from .package_validation import compute_package_digest
from .trust_service import (
    SkillRuntimeEnvironment,
    SkillTrustError,
    SkillTrustService,
)


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
                environment=(
                    runtime_environment
                    or SkillRuntimeEnvironment.installation_baseline()
                ),
            )
        except SkillTrustError as exc:
            raise SkillValidationError(
                str(exc), code=exc.code, details=exc.details
            ) from exc

        with self._lock:
            self._ensure_dirs()
            target_dir = self._safe_skill_dir(skill_id)
            tmp_root = Path(
                tempfile.mkdtemp(prefix=f"{skill_id}-", dir=str(self.tmp_dir))
            )
            checkout_dir = tmp_root / "repo"
            staging_dir = self.installed_dir / f".{skill_id}.staging-{uuid.uuid4().hex}"
            backup_dir = self.installed_dir / f".{skill_id}.backup-{uuid.uuid4().hex}"
            committed = False
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
                installed[skill_id] = asdict(metadata)
                try:
                    if target_dir.exists():
                        target_dir.rename(backup_dir)
                    staging_dir.rename(target_dir)
                    self._write_metadata(installed)
                except Exception:
                    if target_dir.exists():
                        shutil.rmtree(target_dir, ignore_errors=True)
                    if backup_dir.exists():
                        backup_dir.rename(target_dir)
                    raise
                committed = True
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

            target_dir = self._safe_skill_dir(normalized_skill_id)
            if target_dir.exists():
                shutil.rmtree(target_dir)
            installed.pop(normalized_skill_id, None)
            self._write_metadata(installed)

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

    def get_skill_content(self, skill_id: str) -> str:
        """Read the raw ``SKILL.md`` content for an installed Skill."""

        normalized_skill_id = self._validate_skill_id(skill_id)
        with self._lock:
            self._reconcile_trust_metadata_unlocked()
            installed = self._read_metadata()
            if normalized_skill_id not in installed:
                raise SkillNotFoundError(f"Skill '{normalized_skill_id}' is not installed")

            package_dir = self._resolve_package_directory(
                normalized_skill_id, installed[normalized_skill_id]
            )
            skill_md = package_dir / "SKILL.md"
            return skill_md.read_text(encoding="utf-8", errors="replace")

    def get_skill_directory(self, skill_id: str) -> Path:
        """Return the validated installed directory for Runtime staging."""

        normalized_skill_id = self._validate_skill_id(skill_id)
        with self._lock:
            self._reconcile_trust_metadata_unlocked()
            installed = self._read_metadata()
            if normalized_skill_id not in installed:
                raise SkillNotFoundError(f"Skill '{normalized_skill_id}' is not installed")
            return self._resolve_package_directory(
                normalized_skill_id, installed[normalized_skill_id]
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
    ) -> InstalledSkill:
        """Apply the final server-side third-party activation gate."""

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
        try:
            self.trust_service.activation_decision(
                item,
                environment=runtime_environment,
                ephemeral_authorizations=ephemeral_authorizations,
                check_runtime=check_runtime,
            )
        except SkillTrustError as exc:
            raise SkillValidationError(
                str(exc), code=exc.code, details=exc.details
            ) from exc
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
        if item.source_kind != "git" or self.trust_service.mode == "off":
            return False
        package_dir: Path | None
        try:
            package_dir = self._resolve_package_directory(skill_id, record)
        except SkillManagerError:
            package_dir = None
        trust_metadata = self.trust_service.reconcile_metadata(
            record=record,
            package_dir=package_dir,
        )
        if not any(
            record.get(key) != value for key, value in trust_metadata.items()
        ):
            return False
        record.update(trust_metadata)
        return True

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
            or installed_item.source_kind != "workspace_draft"
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
        receipt = self._read_install_receipt(skill_id)
        if receipt is None:
            return
        if receipt.source_id != draft_id:
            raise SkillInstallError(
                "Workspace Skill install receipt belongs to another draft."
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
        self._remove_directory_if_present(staging_dir)
        self._remove_directory_if_present(backup_dir)
        self._receipt_path(skill_id).unlink(missing_ok=True)

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
        self._ensure_dirs_for_read()
        try:
            raw = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

        if isinstance(raw, dict) and isinstance(raw.get("skills"), dict):
            return raw["skills"]  # type: ignore[return-value]
        if isinstance(raw, dict):
            return raw  # backward-compatible flat shape
        return {}

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
