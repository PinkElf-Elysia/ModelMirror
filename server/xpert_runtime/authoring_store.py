from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

try:
    from server.skills.package_validation import (
        scan_skill_package_credentials,
        validate_skill_package,
    )
except ModuleNotFoundError:
    from skills.package_validation import (
        scan_skill_package_credentials,
        validate_skill_package,
    )


ProposalKind = Literal[
    "xpert_create",
    "xpert_update",
    "prompt_profile_update",
    "skill_create",
    "skill_update",
]
ProposalStatus = Literal[
    "pending",
    "approved",
    "rejected",
    "cancelled",
    "conflict",
]


class AuthoringProposalError(Exception):
    """Base error for safe self-authoring proposals."""


class AuthoringProposalNotFoundError(AuthoringProposalError):
    pass


class AuthoringProposalConflictError(AuthoringProposalError):
    pass


class AuthoringProposalValidationError(AuthoringProposalError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "authoring_validation",
        issues: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.issues = list(issues or [])


@dataclass(slots=True)
class AuthoringProposal:
    proposal_id: str
    kind: ProposalKind
    title: str
    payload: dict[str, Any]
    source_type: str
    source_id: str
    source_xpert_id: str | None = None
    source_run_id: str | None = None
    target_id: str | None = None
    base_revision: int | None = None
    status: ProposalStatus = "pending"
    revision: int = 1
    validation: dict[str, Any] = field(default_factory=dict)
    applied_resource_id: str | None = None
    operator: str | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class AuthoringProposalStore:
    """Atomic file-backed queue for Xpert and Skill authoring proposals."""

    MAX_PROPOSALS_PER_RUN = 5
    MAX_PENDING_PER_SOURCE = 20
    MAX_PAYLOAD_BYTES = 6 * 1024 * 1024

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        package_dir = Path(__file__).resolve().parent
        self.storage_dir = Path(
            storage_dir
            or os.getenv("AGENT_TASK_STORAGE_DIR", "").strip()
            or package_dir / "storage"
        )
        self.snapshot_path = self.storage_dir / "authoring_proposals.json"
        self._lock = threading.RLock()
        self._items: dict[str, AuthoringProposal] = {}
        self._quarantine: list[dict[str, Any]] = []
        self._load()

    def create(
        self,
        *,
        kind: ProposalKind,
        title: str,
        payload: dict[str, Any],
        source_type: str,
        source_id: str,
        source_xpert_id: str | None = None,
        source_run_id: str | None = None,
        target_id: str | None = None,
        base_revision: int | None = None,
    ) -> AuthoringProposal:
        if kind not in {
            "xpert_create",
            "xpert_update",
            "prompt_profile_update",
            "skill_create",
            "skill_update",
        }:
            raise AuthoringProposalValidationError("Unsupported proposal kind.")
        clean_title = str(title or "").strip()
        clean_source_type = str(source_type or "").strip()
        clean_source_id = str(source_id or "").strip()
        if not clean_title or len(clean_title) > 200:
            raise AuthoringProposalValidationError(
                "Proposal title is required and limited to 200 characters."
            )
        if kind in {"skill_create", "skill_update"}:
            title_issues = scan_skill_package_credentials(
                skill_markdown=clean_title
            )
            if title_issues:
                raise AuthoringProposalValidationError(
                    "Skill proposal title contains blocked credential material.",
                    code="skill_credentials_blocked",
                    issues=[issue.to_dict() for issue in title_issues],
                )
        if not clean_source_type or not clean_source_id:
            raise AuthoringProposalValidationError(
                "Proposal source_type and source_id are required."
            )
        clean_payload = self._validate_payload(payload, kind=kind)
        with self._lock:
            if source_run_id:
                run_count = sum(
                    1
                    for item in self._items.values()
                    if item.source_run_id == source_run_id
                )
                if run_count >= self.MAX_PROPOSALS_PER_RUN:
                    raise AuthoringProposalValidationError(
                        "A single run can create at most five authoring proposals."
                    )
            source_key = source_xpert_id or f"{clean_source_type}:{clean_source_id}"
            pending_count = sum(
                1
                for item in self._items.values()
                if item.status == "pending"
                and (item.source_xpert_id or f"{item.source_type}:{item.source_id}")
                == source_key
            )
            if pending_count >= self.MAX_PENDING_PER_SOURCE:
                raise AuthoringProposalValidationError(
                    "This source already has 20 pending authoring proposals."
                )
            now = time.time()
            proposal = AuthoringProposal(
                proposal_id=f"proposal_{uuid.uuid4().hex}",
                kind=kind,
                title=clean_title,
                payload=clean_payload,
                source_type=clean_source_type[:80],
                source_id=clean_source_id[:240],
                source_xpert_id=self._optional_text(source_xpert_id, 200),
                source_run_id=self._optional_text(source_run_id, 200),
                target_id=self._optional_text(target_id, 200),
                base_revision=base_revision,
                created_at=now,
                updated_at=now,
            )
            self._items[proposal.proposal_id] = proposal
            self._save_unlocked()
            return self._copy(proposal)

    def require(self, proposal_id: str) -> AuthoringProposal:
        with self._lock:
            item = self._items.get(proposal_id)
            if item is None:
                raise AuthoringProposalNotFoundError(
                    f"Authoring proposal not found: {proposal_id}"
                )
            return self._copy(item)

    def list(
        self,
        *,
        status: str | None = None,
        kind: str | None = None,
        target_id: str | None = None,
        source_xpert_id: str | None = None,
        source_type: str | None = None,
        source_id: str | None = None,
        limit: int = 100,
    ) -> list[AuthoringProposal]:
        with self._lock:
            items = list(self._items.values())
        if status:
            items = [item for item in items if item.status == status]
        if kind:
            items = [item for item in items if item.kind == kind]
        if target_id:
            items = [item for item in items if item.target_id == target_id]
        if source_xpert_id:
            items = [item for item in items if item.source_xpert_id == source_xpert_id]
        if source_type:
            items = [item for item in items if item.source_type == source_type]
        if source_id:
            items = [item for item in items if item.source_id == source_id]
        items.sort(key=lambda item: item.updated_at, reverse=True)
        return [self._copy(item) for item in items[: max(1, min(limit, 500))]]

    def update_pending(
        self,
        proposal_id: str,
        *,
        revision: int,
        title: str | None = None,
        payload: dict[str, Any] | None = None,
        base_revision: int | None = None,
    ) -> AuthoringProposal:
        with self._lock:
            item = self._require_unlocked(proposal_id)
            self._require_pending_revision(item, revision)
            if title is not None:
                clean_title = str(title).strip()
                if not clean_title or len(clean_title) > 200:
                    raise AuthoringProposalValidationError("Invalid proposal title.")
                if item.kind in {"skill_create", "skill_update"}:
                    title_issues = scan_skill_package_credentials(
                        skill_markdown=clean_title
                    )
                    if title_issues:
                        raise AuthoringProposalValidationError(
                            "Skill proposal title contains blocked credential material.",
                            code="skill_credentials_blocked",
                            issues=[issue.to_dict() for issue in title_issues],
                        )
                item.title = clean_title
            if payload is not None:
                next_payload = self._validate_payload(payload, kind=item.kind)
                payload_changed = next_payload != item.payload
                if item.source_type == "meta_planner" and payload_changed:
                    report = next_payload.get("meta_planner_report")
                    if isinstance(report, dict):
                        report["human_modified"] = True
                item.payload = next_payload
            if base_revision is not None:
                if base_revision < 1:
                    raise AuthoringProposalValidationError(
                        "base_revision must be positive."
                    )
                item.base_revision = base_revision
            item.validation = {}
            item.error = None
            item.revision += 1
            item.updated_at = time.time()
            self._save_unlocked()
            return self._copy(item)

    def set_validation(
        self,
        proposal_id: str,
        *,
        revision: int,
        validation: dict[str, Any],
    ) -> AuthoringProposal:
        with self._lock:
            item = self._require_unlocked(proposal_id)
            self._require_pending_revision(item, revision)
            item.validation = dict(validation)
            item.error = None
            item.updated_at = time.time()
            self._save_unlocked()
            return self._copy(item)

    def transition(
        self,
        proposal_id: str,
        *,
        revision: int,
        status: ProposalStatus,
        operator: str,
        applied_resource_id: str | None = None,
        error: str | None = None,
    ) -> AuthoringProposal:
        if status not in {"approved", "rejected", "cancelled", "conflict"}:
            raise AuthoringProposalValidationError("Invalid proposal transition.")
        with self._lock:
            item = self._require_unlocked(proposal_id)
            self._require_pending_revision(item, revision)
            item.status = status
            item.operator = str(operator or "operator").strip()[:120] or "operator"
            item.applied_resource_id = self._optional_text(applied_resource_id, 200)
            item.error = self._optional_text(error, 1000)
            item.revision += 1
            item.updated_at = time.time()
            self._save_unlocked()
            return self._copy(item)

    @staticmethod
    def serialize(
        item: AuthoringProposal, *, include_payload: bool = False
    ) -> dict[str, Any]:
        data = asdict(item)
        if not include_payload:
            encoded = json.dumps(item.payload, ensure_ascii=False, separators=(",", ":"))
            data.pop("payload", None)
            data["payload_bytes"] = len(encoded.encode("utf-8"))
            data["payload_summary"] = sorted(item.payload.keys())[:20]
        return data

    def _require_unlocked(self, proposal_id: str) -> AuthoringProposal:
        item = self._items.get(proposal_id)
        if item is None:
            raise AuthoringProposalNotFoundError(
                f"Authoring proposal not found: {proposal_id}"
            )
        return item

    @staticmethod
    def _require_pending_revision(item: AuthoringProposal, revision: int) -> None:
        if item.status != "pending":
            raise AuthoringProposalConflictError(
                f"Proposal is already {item.status}."
            )
        if item.revision != revision:
            raise AuthoringProposalConflictError(
                "Proposal changed. Reload it before applying this operation."
            )

    def _validate_payload(
        self,
        payload: dict[str, Any],
        *,
        kind: ProposalKind | None = None,
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise AuthoringProposalValidationError("Proposal payload must be an object.")
        try:
            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            decoded = json.loads(encoded)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AuthoringProposalValidationError(
                "Proposal payload must be JSON serializable."
            ) from exc
        if len(encoded.encode("utf-8")) > self.MAX_PAYLOAD_BYTES:
            raise AuthoringProposalValidationError("Proposal payload is too large.")
        if kind in {"skill_create", "skill_update"}:
            self._validate_skill_payload_before_persist(kind, decoded)
        return decoded

    @staticmethod
    def _validate_skill_payload_before_persist(
        kind: ProposalKind,
        payload: dict[str, Any],
    ) -> None:
        skill = payload.get("skill") if isinstance(payload.get("skill"), dict) else payload
        markdown = skill.get("skill_markdown") or skill.get("SKILL.md")
        files = skill.get("files")
        payload_text = "\n".join(AuthoringProposalStore._iter_text(payload))
        credential_issues = scan_skill_package_credentials(
            skill_markdown=payload_text,
            files=files,
        )
        if credential_issues:
            codes = ", ".join(sorted({issue.code for issue in credential_issues}))
            raise AuthoringProposalValidationError(
                f"Skill proposal contains blocked credential material ({codes}).",
                code="skill_credentials_blocked",
                issues=[issue.to_dict() for issue in credential_issues],
            )

        if kind != "skill_create":
            return
        result = validate_skill_package(
            root_name=skill.get("slug"),
            skill_markdown=markdown,
            files=files or {},
        )
        if result.valid and result.package is not None:
            mismatches: list[dict[str, Any]] = []
            if skill.get("name") != result.package.name:
                mismatches.append(
                    {
                        "code": "skill_package_name_mismatch",
                        "severity": "error",
                        "field": "name",
                        "message": "Package name must match SKILL.md frontmatter name.",
                    }
                )
            if skill.get("description") != result.package.description:
                mismatches.append(
                    {
                        "code": "skill_package_description_mismatch",
                        "severity": "error",
                        "field": "description",
                        "message": "Package description must match SKILL.md frontmatter description.",
                    }
                )
            if not mismatches:
                return
            raise AuthoringProposalValidationError(
                "Skill proposal metadata does not match SKILL.md.",
                code="skill_package_invalid",
                issues=mismatches,
            )
        details = "; ".join(
            f"{issue.code}: {issue.message}" for issue in result.issues[:8]
        )
        raise AuthoringProposalValidationError(
            details or "Skill proposal package validation failed.",
            code="skill_package_invalid",
            issues=[issue.to_dict() for issue in result.issues],
        )

    @staticmethod
    def _iter_text(value: Any):
        if isinstance(value, str):
            yield value
            return
        if isinstance(value, dict):
            for key, item in value.items():
                if isinstance(key, str):
                    yield key
                yield from AuthoringProposalStore._iter_text(item)
            return
        if isinstance(value, list):
            for item in value:
                yield from AuthoringProposalStore._iter_text(item)

    def _load(self) -> None:
        with self._lock:
            if not self.snapshot_path.exists():
                return
            try:
                raw = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
                items = raw.get("items", []) if isinstance(raw, dict) else []
                if not isinstance(items, list):
                    raise ValueError("Authoring proposal items must be a list.")
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                self._items = {}
                return

            sanitized = False
            for index, record in enumerate(items):
                if not isinstance(record, dict) or not record.get("proposal_id"):
                    self._quarantine.append(
                        self._safe_quarantine_record(record, index=index)
                    )
                    sanitized = True
                    continue
                try:
                    item = AuthoringProposal(**record)
                except (TypeError, ValueError):
                    self._quarantine.append(
                        self._safe_quarantine_record(record, index=index)
                    )
                    sanitized = True
                    continue
                if item.kind in {"skill_create", "skill_update"}:
                    searchable_text = "\n".join(self._iter_text(record))
                    issues = scan_skill_package_credentials(
                        skill_markdown=searchable_text,
                        files=(
                            item.payload.get("skill", {}).get("files")
                            if isinstance(item.payload.get("skill"), dict)
                            else item.payload.get("files")
                        ),
                    )
                    if issues:
                        self._quarantine.append(
                            self._safe_quarantine_record(record, index=index)
                        )
                        sanitized = True
                        continue
                if item.proposal_id in self._items:
                    self._quarantine.append(
                        self._safe_quarantine_record(record, index=index)
                    )
                    sanitized = True
                    continue
                self._items[item.proposal_id] = item

            existing_quarantine = raw.get("quarantine", []) if isinstance(raw, dict) else []
            if isinstance(existing_quarantine, list):
                for index, entry in enumerate(existing_quarantine):
                    if not isinstance(entry, dict):
                        sanitized = True
                        continue
                    digest = str(entry.get("record_sha256") or "").lower()
                    size = entry.get("record_size_bytes")
                    if (
                        len(digest) == 64
                        and all(character in "0123456789abcdef" for character in digest)
                        and isinstance(size, int)
                        and size >= 0
                    ):
                        self._quarantine.append(
                            {
                                "index": index,
                                "reason_code": "blocked_or_invalid_proposal",
                                "record_sha256": digest,
                                "record_size_bytes": size,
                                "quarantined_at": time.time(),
                            }
                        )
                    else:
                        sanitized = True
            if sanitized:
                self._save_unlocked()

    @staticmethod
    def _safe_quarantine_record(record: Any, *, index: int) -> dict[str, Any]:
        try:
            encoded = json.dumps(
                record,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, UnicodeEncodeError):
            encoded = type(record).__name__.encode("ascii", errors="replace")
        return {
            "index": max(0, int(index)),
            "reason_code": "blocked_or_invalid_proposal",
            "record_sha256": hashlib.sha256(encoded).hexdigest(),
            "record_size_bytes": len(encoded),
            "quarantined_at": time.time(),
        }

    def _save_unlocked(self) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        temp_path = self.snapshot_path.with_name(
            f"{self.snapshot_path.name}.{uuid.uuid4().hex}.tmp"
        )
        payload = {
            "version": 2,
            "items": [asdict(item) for item in self._items.values()],
            "quarantine": self._quarantine,
        }
        try:
            encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            with temp_path.open("xb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.snapshot_path)
        finally:
            temp_path.unlink(missing_ok=True)

    @staticmethod
    def _copy(item: AuthoringProposal) -> AuthoringProposal:
        return AuthoringProposal(**json.loads(json.dumps(asdict(item), ensure_ascii=False)))

    @staticmethod
    def _optional_text(value: Any, maximum: int) -> str | None:
        if value is None:
            return None
        clean = str(value).strip()
        return clean[:maximum] if clean else None
