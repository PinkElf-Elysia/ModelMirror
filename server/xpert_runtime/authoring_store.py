from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

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

META_PLANNER_ARTIFACT_KEY = "_meta_planner_generation_artifact"


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
    source_task_id: str | None = None
    target_id: str | None = None
    base_revision: int | None = None
    base_digest: str | None = None
    creator_session_id: str | None = None
    creator_session_revision: int | None = None
    payload_digest: str = ""
    content_digest: str | None = None
    apply_key: str = ""
    applied_apply_key: str | None = None
    applied_from_revision: int | None = None
    applied_resource_revision: int | None = None
    applied_content_digest: str | None = None
    status: ProposalStatus = "pending"
    revision: int = 1
    validation: dict[str, Any] = field(default_factory=dict)
    applied_resource_id: str | None = None
    actor_kind: str = "legacy"
    actor_id: str | None = None
    operator: str | None = None
    error: str | None = None
    decision_reason: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    meta_planner_model_repairs: list[dict[str, Any]] = field(default_factory=list)
    meta_planner_recipe_recovery: dict[str, Any] | None = None


def requires_recipe_recovery(proposal: AuthoringProposal) -> bool:
    if proposal.source_type != "meta_planner":
        return False
    artifact = proposal.payload.get(META_PLANNER_ARTIFACT_KEY)
    if not isinstance(artifact, dict) or not any(
        isinstance(item, dict) and item.get("recipe") is not None
        for item in artifact.get("attempts", [])
    ):
        return False
    receipt = proposal.meta_planner_recipe_recovery
    revision = receipt.get("revision") if isinstance(receipt, dict) else None
    return not (
        isinstance(receipt, dict) and isinstance(revision, int) and not isinstance(revision, bool)
        and 1 < revision <= proposal.revision
        and isinstance(artifact.get("checksum"), str) and len(artifact["checksum"]) == 64
        and receipt.get("artifact_checksum") == artifact["checksum"]
    )


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
        self._repair_errors: set[str] = set()
        self._load()
        self._load_model_repairs()

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
        source_task_id: str | None = None,
        target_id: str | None = None,
        base_revision: int | None = None,
        base_digest: str | None = None,
        creator_session_id: str | None = None,
        creator_session_revision: int | None = None,
        content_digest: str | None = None,
        actor_kind: str = "workflow_agent",
        actor_id: str | None = None,
        meta_planner_artifact: dict[str, Any] | None = None,
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
        if META_PLANNER_ARTIFACT_KEY in clean_payload:
            raise AuthoringProposalValidationError("失败产物只能由服务端生成流程保存。")
        if meta_planner_artifact is not None:
            if clean_source_type != "meta_planner" or kind not in {"xpert_create", "xpert_update"}:
                raise AuthoringProposalValidationError("失败产物仅用于元智能体 Xpert 提案。")
            clean_payload[META_PLANNER_ARTIFACT_KEY] = deepcopy(meta_planner_artifact)
            clean_payload = self._validate_payload(clean_payload, kind=kind)
        payload_digest = self._payload_digest(clean_payload)
        inferred_content_digest = self._skill_content_digest(
            clean_payload, kind=kind
        )
        clean_content_digest = self._optional_digest(
            content_digest or inferred_content_digest, "content_digest"
        )
        clean_base_digest = self._optional_digest(base_digest, "base_digest")
        clean_actor_kind = self._actor_kind(actor_kind)
        if creator_session_revision is not None and int(creator_session_revision) < 1:
            raise AuthoringProposalValidationError(
                "creator_session_revision must be positive."
            )
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
                source_task_id=self._optional_text(source_task_id, 200),
                target_id=self._optional_text(target_id, 200),
                base_revision=base_revision,
                base_digest=clean_base_digest,
                creator_session_id=self._optional_text(creator_session_id, 200),
                creator_session_revision=(
                    int(creator_session_revision)
                    if creator_session_revision is not None
                    else None
                ),
                payload_digest=payload_digest,
                content_digest=clean_content_digest,
                apply_key=f"apply_{uuid.uuid4().hex}",
                actor_kind=clean_actor_kind,
                actor_id=self._optional_text(actor_id, 200),
                created_at=now,
                updated_at=now,
            )
            self._items[proposal.proposal_id] = proposal
            try:
                self._save_unlocked()
            except Exception:
                self._items.pop(proposal.proposal_id, None)
                raise
            return self._copy(proposal)

    def require(self, proposal_id: str) -> AuthoringProposal:
        with self._lock:
            item = self._items.get(proposal_id)
            if item is None:
                raise AuthoringProposalNotFoundError(
                    f"Authoring proposal not found: {proposal_id}"
                )
            return self._copy(item)

    def require_apply_binding(
        self, proposal_id: str, *, revision: int, apply_key: str
    ) -> AuthoringProposal:
        with self._lock:
            item = self._require_unlocked(proposal_id)
            if item.status != "pending":
                raise AuthoringProposalConflictError(
                    f"Proposal is already {item.status}."
                )
            if item.revision != revision:
                raise AuthoringProposalConflictError(
                    "Proposal changed. Reload it before approval."
                )
            if not apply_key or item.apply_key != apply_key:
                raise AuthoringProposalConflictError(
                    "Proposal apply key changed. Reload it before approval."
                )
            if item.payload_digest != self._payload_digest(item.payload):
                raise AuthoringProposalConflictError(
                    "Proposal payload digest changed unexpectedly."
                )
            return self._copy(item)

    def get_meta_planner_model_repairs(self, proposal_id: str) -> list[dict[str, Any]]:
        with self._lock:
            item = self._require_unlocked(proposal_id)
            if proposal_id in self._repair_errors:
                raise AuthoringProposalConflictError("私有修复日志不可验证，已阻止继续调用；原提案仍可读取。")
            return deepcopy(item.meta_planner_model_repairs)

    def claim_meta_planner_model_repair(
        self, proposal_id: str, *, revision: int, payload_digest: str,
        expected_count: int, attempt: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        """Reserve one explicit call without changing candidate/revision."""
        with self._lock:
            self.get_meta_planner_model_repairs(proposal_id)
            item = self._require_unlocked(proposal_id)
            for prior in item.meta_planner_model_repairs:
                if prior["request_id"] == attempt["request_id"]:
                    if prior["request_checksum"] != attempt["request_checksum"]:
                        raise AuthoringProposalConflictError("同一次调用标识不能用于不同授权。")
                    return deepcopy(prior), False
                if prior.get("authorization_id") == attempt.get("authorization_id"):
                    raise AuthoringProposalConflictError("这份单次授权已消耗，不能更换调用标识再次派发。")
            self._require_pending_revision(item, revision)
            if item.source_type != "meta_planner" or item.kind not in {"xpert_create", "xpert_update"}:
                raise AuthoringProposalValidationError("仅元智能体失败提案允许定向修复。")
            if item.payload_digest != payload_digest or len(item.meta_planner_model_repairs) != expected_count:
                raise AuthoringProposalConflictError("提案或修复记录已变化，请重新预检并确认。")
            if len(item.meta_planner_model_repairs) >= 20:
                raise AuthoringProposalValidationError("此提案已达到 20 次显式修复上限。")
            if any(row["status"] == "dispatching" for row in item.meta_planner_model_repairs):
                raise AuthoringProposalConflictError("已有一次修复正在派发，不能并发调用。")
            saved = deepcopy(attempt)
            saved.update(status="dispatching", created_at=time.time(), completed_at=None)
            saved["checksum"] = self._payload_digest(saved)
            item.meta_planner_model_repairs.append(saved)
            try:
                self._save_model_repairs_unlocked(item)
            except BaseException:
                item.meta_planner_model_repairs.pop()
                raise
            return deepcopy(saved), True

    def finish_meta_planner_model_repair(
        self, proposal_id: str, *, request_id: str, request_checksum: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        with self._lock:
            self.get_meta_planner_model_repairs(proposal_id)
            item = self._require_unlocked(proposal_id)
            index = next((i for i, row in enumerate(item.meta_planner_model_repairs)
                          if row["request_id"] == request_id), None)
            if index is None:
                raise AuthoringProposalConflictError("找不到已占用的修复调用。")
            previous = item.meta_planner_model_repairs[index]
            if previous["request_checksum"] != request_checksum:
                raise AuthoringProposalConflictError("修复调用授权不一致。")
            if previous["status"] != "dispatching":
                return deepcopy(previous)
            if result.get("status") not in {"suggested", "invalid", "failed", "uncertain", "stale"}:
                raise AuthoringProposalValidationError("修复回执状态无效。")
            saved = {**previous, **deepcopy(result), "completed_at": time.time()}
            saved.pop("checksum", None)
            saved["checksum"] = self._payload_digest(saved)
            records = [*item.meta_planner_model_repairs]
            records[index] = saved
            encoded = json.dumps(records, ensure_ascii=False, allow_nan=False).encode("utf-8")
            if len(encoded) > 2 * 1024 * 1024:
                raise AuthoringProposalValidationError("私有修复回执已超过存储上限，不能继续。")
            item.meta_planner_model_repairs[index] = saved
            try:
                self._save_model_repairs_unlocked(item)
            except BaseException:
                item.meta_planner_model_repairs[index] = previous
                raise
            return deepcopy(saved)

    def list(
        self,
        *,
        status: str | None = None,
        kind: str | None = None,
        target_id: str | None = None,
        source_xpert_id: str | None = None,
        source_type: str | None = None,
        source_id: str | None = None,
        creator_session_id: str | None = None,
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
        if creator_session_id:
            items = [
                item
                for item in items
                if item.creator_session_id == creator_session_id
            ]
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
        return self._update_pending(
            proposal_id,
            revision=revision,
            title=title,
            payload=payload,
            base_revision=base_revision,
            preserve_meta_planner_ir=False,
        )

    def update_pending_from_headless_authoring(
        self,
        proposal_id: str,
        *,
        revision: int,
        payload: dict[str, Any],
        validation: dict[str, Any],
        content_digest: str | None = None,
        base_digest: str | None = None,
        recovery_guard: Callable[[], None] | None = None,
    ) -> AuthoringProposal:
        """Apply a server-validated Graph Patch without invalidating its IR."""

        return self._update_pending(
            proposal_id,
            revision=revision,
            payload=payload,
            preserve_meta_planner_ir=True,
            prevalidated_validation=validation,
            content_digest=content_digest,
            base_digest=base_digest,
            recovery_guard=recovery_guard,
        )

    def _update_pending(
        self,
        proposal_id: str,
        *,
        revision: int,
        title: str | None = None,
        payload: dict[str, Any] | None = None,
        base_revision: int | None = None,
        preserve_meta_planner_ir: bool,
        prevalidated_validation: dict[str, Any] | None = None,
        content_digest: str | None = None,
        base_digest: str | None = None,
        recovery_guard: Callable[[], None] | None = None,
    ) -> AuthoringProposal:
        with self._lock:
            current = self._require_unlocked(proposal_id)
            self._require_pending_revision(current, revision)
            if preserve_meta_planner_ir and requires_recipe_recovery(current):
                if recovery_guard is None:
                    raise AuthoringProposalValidationError("生成描述恢复缺少提交前完整预览绑定复核。", code="recipe_recovery_required")
                recovery_guard()
            item = self._copy(current)
            if preserve_meta_planner_ir:
                if item.source_type != "meta_planner" or item.kind not in {
                    "xpert_create",
                    "xpert_update",
                }:
                    raise AuthoringProposalValidationError(
                        "Headless authoring only accepts Meta Planner Xpert proposals."
                    )
            if not preserve_meta_planner_ir and requires_recipe_recovery(item):
                raise AuthoringProposalValidationError("生成描述尚未展开为有效候选，必须通过失败描述修复预览，不能整包修改占位图。", code="recipe_recovery_required")
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
                retained_artifact = item.payload.get(META_PLANNER_ARTIFACT_KEY)
                if META_PLANNER_ARTIFACT_KEY in next_payload and next_payload[META_PLANNER_ARTIFACT_KEY] != retained_artifact:
                    raise AuthoringProposalValidationError("失败产物为只读证据，不能通过整包编辑替换。")
                if retained_artifact is not None:
                    next_payload[META_PLANNER_ARTIFACT_KEY] = deepcopy(retained_artifact)
                    next_payload = self._validate_payload(next_payload, kind=item.kind)
                if item.source_type == "meta_planner" and item.kind in {
                    "xpert_create",
                    "xpert_update",
                }:
                    current_report = item.payload.get("meta_planner_report")
                    next_report = next_payload.get("meta_planner_report")
                    if isinstance(current_report, dict) and isinstance(
                        next_report, dict
                    ):
                        original_scope = current_report.get("authorized_scope")
                        if isinstance(original_scope, dict):
                            next_report["authorized_scope"] = deepcopy(
                                original_scope
                            )
                payload_changed = next_payload != item.payload
                if (
                    item.source_type == "meta_planner"
                    and payload_changed
                    and not preserve_meta_planner_ir
                ):
                    report = next_payload.get("meta_planner_report")
                    if isinstance(report, dict):
                        report["human_modified"] = True
                        if report.get("graph_ir") is not None:
                            report["graph_ir_status"] = "stale"
                item.payload = next_payload
                item.payload_digest = self._payload_digest(next_payload)
                item.content_digest = self._skill_content_digest(
                    next_payload, kind=item.kind
                )
            if base_revision is not None:
                if base_revision < 1:
                    raise AuthoringProposalValidationError(
                        "base_revision must be positive."
                    )
                item.base_revision = base_revision
            item.validation = dict(prevalidated_validation or {})
            if content_digest is not None:
                item.content_digest = self._optional_digest(
                    content_digest, "content_digest"
                )
            if base_digest is not None:
                item.base_digest = self._optional_digest(base_digest, "base_digest")
            item.error = None
            if preserve_meta_planner_ir and requires_recipe_recovery(current):
                if not (prevalidated_validation or {}).get("valid"):
                    raise AuthoringProposalValidationError("生成描述修复未通过服务端完整验证。", code="recipe_recovery_required")
                item.meta_planner_recipe_recovery = {
                    "artifact_checksum": current.payload[META_PLANNER_ARTIFACT_KEY]["checksum"],
                    "revision": current.revision + 1,
                }
            item.revision += 1
            item.apply_key = f"apply_{uuid.uuid4().hex}"
            item.applied_apply_key = None
            item.applied_from_revision = None
            item.updated_at = time.time()
            self._items[proposal_id] = item
            try:
                self._save_unlocked()
            except Exception:
                self._items[proposal_id] = current
                raise
            return self._copy(item)

    def set_validation(
        self,
        proposal_id: str,
        *,
        revision: int,
        validation: dict[str, Any],
        content_digest: str | None = None,
        base_digest: str | None = None,
    ) -> AuthoringProposal:
        with self._lock:
            item = self._require_unlocked(proposal_id)
            self._require_pending_revision(item, revision)
            item.validation = dict(validation)
            if content_digest is not None:
                item.content_digest = self._optional_digest(
                    content_digest, "content_digest"
                )
            if base_digest is not None:
                item.base_digest = self._optional_digest(base_digest, "base_digest")
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
        actor_kind: str = "local_console",
        actor_id: str | None = None,
        operator: str | None = None,
        apply_key: str | None = None,
        applied_resource_id: str | None = None,
        applied_resource_revision: int | None = None,
        applied_content_digest: str | None = None,
        error: str | None = None,
        decision_reason: str | None = None,
    ) -> AuthoringProposal:
        if status not in {"approved", "rejected", "cancelled", "conflict"}:
            raise AuthoringProposalValidationError("Invalid proposal transition.")
        with self._lock:
            item = self._require_unlocked(proposal_id)
            self._require_pending_revision(item, revision)
            item.status = status
            item.actor_kind = self._actor_kind(actor_kind)
            item.actor_id = self._optional_text(actor_id, 200)
            # ``operator`` is retained only as read-only legacy metadata.  New
            # callers authenticate the local console actor on the server.
            if item.operator is None and operator:
                item.operator = str(operator).strip()[:120] or None
            if status == "approved":
                if applied_resource_revision is not None and applied_resource_revision < 1:
                    raise AuthoringProposalValidationError(
                        "applied_resource_revision must be positive."
                    )
                item.applied_apply_key = self._optional_text(
                    apply_key or item.apply_key, 80
                )
                item.applied_from_revision = item.revision
                item.applied_resource_revision = applied_resource_revision
                item.applied_content_digest = self._optional_digest(
                    applied_content_digest, "applied_content_digest"
                )
            item.applied_resource_id = self._optional_text(applied_resource_id, 200)
            item.error = self._optional_text(error, 1000)
            item.decision_reason = self._optional_text(decision_reason, 1000)
            item.revision += 1
            item.updated_at = time.time()
            self._save_unlocked()
            return self._copy(item)

    @staticmethod
    def serialize(
        item: AuthoringProposal, *, include_payload: bool = False
    ) -> dict[str, Any]:
        data = asdict(item)
        data.pop("meta_planner_model_repairs", None)
        data.pop("meta_planner_recipe_recovery", None)
        data["payload"].pop(META_PLANNER_ARTIFACT_KEY, None)
        if not include_payload:
            encoded = json.dumps(data["payload"], ensure_ascii=False, separators=(",", ":"))
            summary = sorted(data["payload"].keys())[:20]
            data.pop("payload", None)
            data["payload_bytes"] = len(encoded.encode("utf-8"))
            data["payload_summary"] = summary
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
    def _payload_digest(payload: dict[str, Any]) -> str:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _skill_content_digest(
        payload: dict[str, Any], *, kind: ProposalKind
    ) -> str | None:
        if kind != "skill_create":
            return None
        skill = payload.get("skill") if isinstance(payload.get("skill"), dict) else payload
        result = validate_skill_package(
            root_name=skill.get("slug"),
            skill_markdown=skill.get("skill_markdown") or skill.get("SKILL.md"),
            files=skill.get("files") or {},
        )
        return result.content_digest if result.valid else None

    @staticmethod
    def _optional_digest(value: Any, field_name: str) -> str | None:
        if value is None:
            return None
        clean = str(value).strip().lower()
        if len(clean) != 64 or any(character not in "0123456789abcdef" for character in clean):
            raise AuthoringProposalValidationError(
                f"{field_name} must be a SHA-256 digest."
            )
        return clean

    @staticmethod
    def _actor_kind(value: Any) -> str:
        clean = str(value or "").strip()
        if clean not in {"local_console", "workflow_agent", "legacy"}:
            raise AuthoringProposalValidationError("Invalid proposal actor kind.")
        return clean

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
                    normalized_record = dict(record)
                    payload = normalized_record.get("payload")
                    if not isinstance(payload, dict):
                        raise TypeError("Proposal payload must be an object.")
                    actual_payload_digest = self._payload_digest(payload)
                    stored_payload_digest = normalized_record.get("payload_digest")
                    if stored_payload_digest:
                        if stored_payload_digest != actual_payload_digest:
                            raise ValueError("Proposal payload digest does not match.")
                    else:
                        normalized_record["payload_digest"] = actual_payload_digest
                        sanitized = True
                    if not normalized_record.get("apply_key"):
                        normalized_record["apply_key"] = f"apply_{uuid.uuid4().hex}"
                        sanitized = True
                    if "actor_kind" not in normalized_record:
                        normalized_record["actor_kind"] = "legacy"
                        sanitized = True
                    item = AuthoringProposal(**normalized_record)
                    self._actor_kind(item.actor_kind)
                    self._optional_digest(item.base_digest, "base_digest")
                    self._optional_digest(item.content_digest, "content_digest")
                except (TypeError, ValueError, AuthoringProposalValidationError):
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

    def _repair_path(self, proposal_id: str) -> Path:
        name = hashlib.sha256(proposal_id.encode("utf-8")).hexdigest()
        return self.storage_dir / "authoring_model_repairs" / f"{name}.json"

    def _save_model_repairs_unlocked(self, item: AuthoringProposal) -> None:
        self._write_atomic_json(self._repair_path(item.proposal_id), {
            "version": 1, "proposal_id": item.proposal_id, "attempts": item.meta_planner_model_repairs,
        }, max_bytes=2 * 1024 * 1024)

    def _load_model_repairs(self) -> None:
        for item in self._items.values():
            path = self._repair_path(item.proposal_id)
            try:
                if not path.exists():
                    continue
                if path.stat().st_size > 2 * 1024 * 1024:
                    raise ValueError("Repair journal too large.")
                raw = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict) or raw.get("version") != 1 or raw.get("proposal_id") != item.proposal_id:
                    raise ValueError("Repair journal identity mismatch.")
                rows = raw.get("attempts")
                if not isinstance(rows, list) or len(rows) > 20:
                    raise ValueError("Invalid repair journal.")
                seen = set()
                interrupted = False
                for row in rows:
                    if not isinstance(row, dict) or not isinstance(row.get("request_id"), str):
                        raise ValueError("Invalid repair receipt.")
                    if row.get("checksum") != self._payload_digest({k: v for k, v in row.items() if k != "checksum"}) or row["request_id"] in seen:
                        raise ValueError("Repair receipt checksum mismatch.")
                    if row.get("status") not in {"dispatching", "suggested", "invalid", "failed", "uncertain", "stale"}:
                        raise ValueError("Invalid repair status.")
                    seen.add(row["request_id"])
                    if row["status"] == "dispatching":
                        row.update(status="uncertain", completed_at=time.time(), reason_code="repair_interrupted")
                        row["checksum"] = self._payload_digest({k: v for k, v in row.items() if k != "checksum"})
                        interrupted = True
                item.meta_planner_model_repairs = rows
                if interrupted:
                    self._save_model_repairs_unlocked(item)
            except (OSError, TypeError, ValueError, AuthoringProposalValidationError):
                self._repair_errors.add(item.proposal_id)

    def _save_unlocked(self) -> None:
        payload = {
            "version": 2,
            "items": [{key: value for key, value in asdict(item).items()
                       if key != "meta_planner_model_repairs" and not (key == "meta_planner_recipe_recovery" and value is None)}
                      for item in self._items.values()],
            "quarantine": self._quarantine,
        }
        self._write_atomic_json(self.snapshot_path, payload)

    @staticmethod
    def _write_atomic_json(path: Path, payload: dict[str, Any], *, max_bytes: int | None = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            if max_bytes is not None and len(encoded) > max_bytes:
                raise AuthoringProposalValidationError("私有修复日志超过存储上限。")
            with temp_path.open("xb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
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
