from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

from .package_validation import compute_skill_content_digest
from .trust_scanner import (
    SKILL_TRUST_INDEX_VERSION,
    build_skill_trust_summary,
    sha256_json,
    source_key,
)


SkillTrustGateMode = Literal["off", "audit", "enforce"]

SKILL_TRUST_ACK_STORE_VERSION = 1
SKILL_TRUST_ERROR_CODES = {
    "skill_trust_index_unavailable",
    "skill_trust_receipt_missing",
    "skill_trust_package_mismatch",
    "skill_trust_policy_blocked",
    "skill_trust_ack_required",
    "skill_runtime_incompatible",
    "skill_trust_candidate_stale",
}


class SkillTrustError(Exception):
    """Stable, structured failure raised by the third-party Skill gate."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        if code not in SKILL_TRUST_ERROR_CODES:
            raise ValueError(f"Unsupported Skill trust error code: {code}")
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


@dataclass(frozen=True)
class SkillTrustAcknowledgement:
    skill_id: str
    receipt_id: str
    trust_fingerprint: str
    actor_kind: str
    confirmed_at: float


class SkillTrustAcknowledgementStore:
    """Atomic local-console acknowledgements bound to one exact receipt."""

    def __init__(
        self,
        storage_dir: Path | str | None = None,
        *,
        path: Path | str | None = None,
    ) -> None:
        if path is not None:
            self.path = Path(path)
        else:
            root = Path(
                storage_dir
                or os.getenv("AGENT_TASK_STORAGE_DIR")
                or Path(__file__).resolve().parent / "storage"
            )
            self.path = root / "skill_trust_acknowledgements.json"
        self._lock = threading.RLock()

    def acknowledge(
        self,
        *,
        skill_id: str,
        receipt_id: str,
        trust_fingerprint: str,
        actor_kind: str = "local_console",
    ) -> SkillTrustAcknowledgement:
        clean_skill_id = _skill_id(skill_id)
        clean_receipt_id = _receipt_id(receipt_id)
        clean_fingerprint = _sha256(trust_fingerprint, "trust fingerprint")
        clean_actor = str(actor_kind or "").strip()
        if clean_actor != "local_console":
            raise SkillTrustError(
                "Only the local console can persist a Skill trust acknowledgement.",
                code="skill_trust_ack_required",
            )
        item = SkillTrustAcknowledgement(
            skill_id=clean_skill_id,
            receipt_id=clean_receipt_id,
            trust_fingerprint=clean_fingerprint,
            actor_kind=clean_actor,
            confirmed_at=time.time(),
        )
        with self._lock:
            payload = self._read_unlocked()
            acknowledgements = dict(payload["acknowledgements"])
            acknowledgements[clean_skill_id] = asdict(item)
            self._write_unlocked(acknowledgements)
        return item

    def revoke(self, skill_id: str) -> bool:
        clean_skill_id = _skill_id(skill_id)
        with self._lock:
            payload = self._read_unlocked()
            acknowledgements = dict(payload["acknowledgements"])
            removed = acknowledgements.pop(clean_skill_id, None) is not None
            if removed:
                self._write_unlocked(acknowledgements)
            return removed

    def is_acknowledged(self, skill_id: str, trust_fingerprint: str) -> bool:
        clean_skill_id = _skill_id(skill_id)
        clean_fingerprint = _sha256(trust_fingerprint, "trust fingerprint")
        with self._lock:
            item = self._read_unlocked()["acknowledgements"].get(clean_skill_id)
        return bool(
            isinstance(item, dict)
            and item.get("actor_kind") == "local_console"
            and item.get("trust_fingerprint") == clean_fingerprint
        )

    def get(self, skill_id: str) -> SkillTrustAcknowledgement | None:
        clean_skill_id = _skill_id(skill_id)
        with self._lock:
            raw = self._read_unlocked()["acknowledgements"].get(clean_skill_id)
        return self._decode_item(raw) if isinstance(raw, dict) else None

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "version": SKILL_TRUST_ACK_STORE_VERSION,
                "acknowledgements": {},
            }
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SkillTrustError(
                "Skill trust acknowledgement Store is unavailable.",
                code="skill_trust_ack_required",
            ) from exc
        if (
            not isinstance(payload, dict)
            or payload.get("version") != SKILL_TRUST_ACK_STORE_VERSION
            or not isinstance(payload.get("acknowledgements"), dict)
        ):
            raise SkillTrustError(
                "Skill trust acknowledgement Store is invalid.",
                code="skill_trust_ack_required",
            )
        clean: dict[str, dict[str, Any]] = {}
        for key, raw in payload["acknowledgements"].items():
            if not isinstance(raw, dict):
                continue
            try:
                item = self._decode_item(raw)
            except (SkillTrustError, TypeError, ValueError):
                continue
            if key == item.skill_id:
                clean[key] = asdict(item)
        return {
            "version": SKILL_TRUST_ACK_STORE_VERSION,
            "acknowledgements": clean,
        }

    @staticmethod
    def _decode_item(raw: Mapping[str, Any]) -> SkillTrustAcknowledgement:
        confirmed_at = float(raw.get("confirmed_at") or 0)
        if confirmed_at <= 0:
            raise ValueError("invalid acknowledgement time")
        actor = str(raw.get("actor_kind") or "")
        if actor != "local_console":
            raise ValueError("invalid acknowledgement actor")
        return SkillTrustAcknowledgement(
            skill_id=_skill_id(str(raw.get("skill_id") or "")),
            receipt_id=_receipt_id(str(raw.get("receipt_id") or "")),
            trust_fingerprint=_sha256(
                str(raw.get("trust_fingerprint") or ""), "trust fingerprint"
            ),
            actor_kind=actor,
            confirmed_at=confirmed_at,
        )

    def _write_unlocked(self, acknowledgements: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        payload = {
            "version": SKILL_TRUST_ACK_STORE_VERSION,
            "acknowledgements": dict(sorted(acknowledgements.items())),
        }
        try:
            encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            with temporary.open("xb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class SkillRuntimeEnvironment:
    tool_names: frozenset[str] = frozenset()
    tool_providers: frozenset[str] = frozenset()
    sandbox_commands: frozenset[str] = frozenset()
    credentials_available: bool = False
    host_filesystem_available: bool = False
    desktop_control_available: bool = False

    @classmethod
    def installation_baseline(cls) -> "SkillRuntimeEnvironment":
        """Return capabilities guaranteed by the local offline Skill runtime."""

        return cls(
            tool_names=frozenset(
                {
                    "skill_read",
                    "skill_stage",
                    "sandbox_list_files",
                    "sandbox_read_file",
                    "sandbox_search_files",
                    "sandbox_write_file",
                    "sandbox_shell",
                }
            ),
            tool_providers=frozenset({"skill", "sandbox"}),
            sandbox_commands=frozenset({"python", "python3", "node", "rg"}),
        )

    @classmethod
    def from_metadata(cls, metadata: Mapping[str, Any] | None) -> "SkillRuntimeEnvironment":
        source = dict(metadata or {})
        environment = source.get("skill_runtime_environment")
        environment = dict(environment) if isinstance(environment, dict) else {}
        tool_names = _string_set(environment.get("tool_names"))
        tool_providers = _string_set(environment.get("tool_providers"))
        sandbox_config = source.get("sandbox_config")
        sandbox_config = dict(sandbox_config) if isinstance(sandbox_config, dict) else {}
        commands = _string_set(sandbox_config.get("allowed_commands"))
        if "sandbox_shell" in tool_names and not commands:
            commands = {
                "python",
                "python3",
                "node",
                "npm",
                "npx",
                "git",
                "rg",
            }
        return cls(
            tool_names=frozenset(tool_names),
            tool_providers=frozenset(tool_providers),
            sandbox_commands=frozenset(commands),
            credentials_available=bool(
                environment.get("credentials_available", False)
            ),
            host_filesystem_available=bool(
                environment.get("host_filesystem_available", False)
            ),
            desktop_control_available=bool(
                environment.get("desktop_control_available", False)
            ),
        )


@dataclass(frozen=True)
class SkillTrustDecision:
    mode: SkillTrustGateMode
    allowed: bool
    receipt_id: str | None
    trust_fingerprint: str | None
    risk_level: str | None
    trust_status: str
    install_policy: str
    compatibility_status: str
    router_eligible: bool
    acknowledgement_required: bool
    acknowledgement_satisfied: bool
    reason_codes: tuple[str, ...]
    missing_capabilities: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "allowed": self.allowed,
            "receiptId": self.receipt_id,
            "trustFingerprint": self.trust_fingerprint,
            "riskLevel": self.risk_level,
            "trustStatus": self.trust_status,
            "installPolicy": self.install_policy,
            "compatibilityStatus": self.compatibility_status,
            "routerEligible": self.router_eligible,
            "acknowledgementRequired": self.acknowledgement_required,
            "acknowledgementSatisfied": self.acknowledgement_satisfied,
            "reasonCodes": list(self.reason_codes),
            "missingCapabilities": list(self.missing_capabilities),
        }


class SkillTrustService:
    """Fail-closed resolver and policy gate for fixed third-party Git Skills."""

    def __init__(
        self,
        *,
        index_path: Path | str | None = None,
        acknowledgement_store: SkillTrustAcknowledgementStore | None = None,
        mode: SkillTrustGateMode | str | None = None,
        git_timeout_seconds: int = 30,
    ) -> None:
        self.index_path = Path(
            index_path
            or Path(__file__).resolve().parent / "data" / "skill_trust_index.json"
        )
        raw_mode = str(mode or os.getenv("SKILL_TRUST_GATE_MODE") or "enforce").strip().lower()
        if raw_mode not in {"off", "audit", "enforce"}:
            raw_mode = "enforce"
        self.mode: SkillTrustGateMode = raw_mode  # type: ignore[assignment]
        self.acknowledgements = acknowledgement_store or SkillTrustAcknowledgementStore()
        self.git_timeout_seconds = max(5, min(int(git_timeout_seconds), 120))
        self._lock = threading.RLock()
        self._loaded_mtime_ns: int | None = None
        self._index: dict[str, Any] | None = None
        self._receipts_by_id: dict[str, dict[str, Any]] = {}
        self._receipts_by_source: dict[str, dict[str, Any]] = {}
        self._load_error: str | None = None

    @property
    def index_fingerprint(self) -> str | None:
        if self.mode == "off":
            return None
        self._load_index()
        return str((self._index or {}).get("fingerprint") or "") or None

    def acknowledge(
        self,
        *,
        skill_id: str,
        trust_fingerprint: str,
        confirmed: bool,
        actor_kind: str = "local_console",
    ) -> SkillTrustAcknowledgement:
        if not confirmed:
            raise SkillTrustError(
                "Skill trust acknowledgement requires explicit confirmation.",
                code="skill_trust_ack_required",
            )
        receipt = self.receipt_by_fingerprint(trust_fingerprint)
        if receipt["installPolicy"] != "confirm":
            raise SkillTrustError(
                "This Skill receipt cannot be acknowledged.",
                code="skill_trust_policy_blocked",
            )
        return self.acknowledgements.acknowledge(
            skill_id=skill_id,
            receipt_id=receipt["receiptId"],
            trust_fingerprint=receipt["trustFingerprint"],
            actor_kind=actor_kind,
        )

    def revoke(self, skill_id: str) -> bool:
        return self.acknowledgements.revoke(skill_id)

    def receipt_by_fingerprint(self, trust_fingerprint: str) -> dict[str, Any]:
        fingerprint = _sha256(trust_fingerprint, "trust fingerprint")
        self._require_index()
        for receipt in self._receipts_by_id.values():
            if receipt["trustFingerprint"] == fingerprint:
                return receipt
        raise SkillTrustError(
            "Skill trust receipt is unavailable.",
            code="skill_trust_receipt_missing",
        )

    def receipt_by_id(self, receipt_id: str) -> dict[str, Any]:
        """Return one validated, detached trust receipt for UI inspection."""

        clean_receipt_id = _receipt_id(receipt_id)
        self._require_index()
        receipt = self._receipts_by_id.get(clean_receipt_id)
        if receipt is None:
            raise SkillTrustError(
                "Skill trust receipt is unavailable.",
                code="skill_trust_receipt_missing",
            )
        return json.loads(json.dumps(receipt, ensure_ascii=False))

    def summary_index(self) -> dict[str, Any]:
        """Return the deterministic compact index used by the Skill shelf."""

        index = self._require_index()
        return build_skill_trust_summary(index)

    def source_receipt_map(self) -> dict[str, str]:
        """Map each unique fixed install source to its shared receipt."""

        self._require_index()
        return {
            key: str(receipt["receiptId"])
            for key, receipt in sorted(self._receipts_by_source.items())
        }

    def resolve_source(
        self,
        repo_url: str,
        sub_path: str,
        source_ref: str | None,
    ) -> dict[str, Any] | None:
        if self.mode == "off":
            return None
        self._require_index()
        if not source_ref or not re.fullmatch(r"[0-9a-fA-F]{40}", source_ref):
            return None
        key = source_key(repo_url, sub_path, source_ref)
        receipt = self._receipts_by_source.get(key)
        return dict(receipt) if receipt is not None else None

    def install_decision(
        self,
        *,
        skill_id: str,
        repo_url: str,
        sub_path: str,
        source_ref: str | None,
        ephemeral_trust_fingerprint: str | None = None,
        allow_pending_confirmation: bool = False,
        environment: SkillRuntimeEnvironment | None = None,
    ) -> tuple[SkillTrustDecision, dict[str, Any] | None]:
        if self.mode == "off":
            return self._off_decision(), None
        receipt: dict[str, Any] | None = None
        missing_reason = "skill_trust_receipt_missing"
        try:
            receipt = self.resolve_source(repo_url, sub_path, source_ref)
        except SkillTrustError as exc:
            if self.mode == "enforce":
                raise
            missing_reason = exc.code
        if receipt is None:
            decision = self._missing_decision(missing_reason)
            self._raise_if_denied(decision)
            return decision, None
        decision = self._evaluate_receipt(
            receipt,
            skill_id=skill_id,
            ephemeral_trust_fingerprint=ephemeral_trust_fingerprint,
            allow_pending_confirmation=allow_pending_confirmation,
            environment=environment,
        )
        self._raise_if_denied(decision)
        return decision, receipt

    def candidate_decision(
        self,
        candidate: Mapping[str, Any],
        *,
        skill_id: str | None = None,
        ephemeral_trust_fingerprint: str | None = None,
        allow_pending_confirmation: bool = False,
        environment: SkillRuntimeEnvironment | None = None,
        require_router_eligible: bool = False,
    ) -> tuple[SkillTrustDecision, dict[str, Any] | None]:
        if self.mode == "off":
            return self._off_decision(), None
        source = candidate.get("installSource")
        trust = candidate.get("trust")
        if not isinstance(source, Mapping) or not isinstance(trust, Mapping):
            decision = self._missing_decision("skill_trust_receipt_missing")
            self._raise_if_denied(decision)
            return decision, None
        try:
            receipt = self.resolve_source(
                str(source.get("repoUrl") or ""),
                str(source.get("subPath") or ""),
                str(source.get("verifiedCommit") or ""),
            )
        except SkillTrustError as exc:
            if self.mode == "enforce":
                raise
            decision = self._missing_decision(exc.code)
            return decision, None
        if receipt is None:
            decision = self._missing_decision("skill_trust_receipt_missing")
            self._raise_if_denied(decision)
            return decision, None
        summary_matches = all(
            trust.get(key) == receipt.get(receipt_key)
            for key, receipt_key in (
                ("receiptId", "receiptId"),
                ("trustFingerprint", "trustFingerprint"),
                ("riskLevel", "riskLevel"),
                ("trustStatus", "trustStatus"),
                ("installPolicy", "installPolicy"),
                ("compatibilityStatus", "compatibilityStatus"),
                ("routerEligible", "routerEligible"),
            )
        )
        if not summary_matches:
            raise SkillTrustError(
                "Skill trust receipt changed. Run skill_find again.",
                code="skill_trust_candidate_stale",
                details={"candidateId": candidate.get("candidateId")},
            )
        if require_router_eligible and not bool(receipt.get("routerEligible")):
            raise SkillTrustError(
                "This Skill requires manual installation and is excluded from Agent Router discovery.",
                code="skill_trust_policy_blocked",
                details={
                    "candidateId": candidate.get("candidateId"),
                    "receiptId": receipt.get("receiptId"),
                    "routerEligible": False,
                },
            )
        decision = self._evaluate_receipt(
            receipt,
            skill_id=skill_id,
            ephemeral_trust_fingerprint=ephemeral_trust_fingerprint,
            allow_pending_confirmation=allow_pending_confirmation,
            environment=environment,
        )
        self._raise_if_denied(decision)
        return decision, receipt

    def activation_decision(
        self,
        installed_skill: Any,
        *,
        environment: SkillRuntimeEnvironment | None,
        ephemeral_authorizations: Mapping[str, str] | None = None,
        check_runtime: bool = True,
    ) -> SkillTrustDecision:
        if str(getattr(installed_skill, "source_kind", "git")) != "git":
            return self._off_decision(trust_status="not_applicable")
        if self.mode == "off":
            return self._off_decision()
        source_ref = str(getattr(installed_skill, "source_ref", "") or "")
        try:
            receipt = self.resolve_source(
                str(getattr(installed_skill, "repo_url", "") or ""),
                str(getattr(installed_skill, "sub_path", "") or ""),
                source_ref,
            )
        except SkillTrustError as exc:
            if self.mode == "enforce":
                raise
            return self._missing_decision(exc.code)
        state_matches = bool(
            receipt
            and getattr(installed_skill, "trust_state", "") == "receipt_matched"
            and getattr(installed_skill, "trust_receipt_id", None)
            == receipt["receiptId"]
            and getattr(installed_skill, "trust_fingerprint", None)
            == receipt["trustFingerprint"]
            and getattr(installed_skill, "trust_package_digest", None)
            == receipt["packageDigest"]
        )
        if not state_matches or receipt is None:
            decision = self._missing_decision("skill_trust_receipt_missing")
            self._raise_if_denied(decision)
            return decision
        skill_id = str(getattr(installed_skill, "skill_id", "") or "")
        ephemeral = dict(ephemeral_authorizations or {}).get(skill_id)
        decision = self._evaluate_receipt(
            receipt,
            skill_id=skill_id,
            ephemeral_trust_fingerprint=ephemeral,
            allow_pending_confirmation=False,
            environment=environment if check_runtime else None,
        )
        self._raise_if_denied(decision)
        return decision

    def verify_checkout(
        self,
        *,
        checkout_dir: Path,
        source_dir: Path,
        receipt: Mapping[str, Any] | None,
        source_ref: str | None,
    ) -> dict[str, Any]:
        if self.mode == "off":
            return {"trust_state": "off"}
        evidence_error: str | None = None
        actual_head: str | None = None
        tree_sha: str | None = None
        package_digest: str | None = None
        try:
            actual_head = self._git_output(
                checkout_dir, ["rev-parse", "HEAD"]
            ).casefold()
            if not source_ref or actual_head != source_ref.casefold():
                raise ValueError("checkout HEAD mismatch")
            object_name = "HEAD^{tree}" if source_dir == checkout_dir else (
                f"HEAD:{source_dir.relative_to(checkout_dir).as_posix()}"
            )
            tree_sha = self._git_output(
                checkout_dir, ["rev-parse", object_name]
            ).casefold()
            self._validate_git_modes(checkout_dir, source_dir)
            package_digest = self.compute_directory_digest(source_dir)
            if receipt is None:
                raise ValueError("receipt missing")
            if tree_sha != str(receipt.get("directoryTreeSha") or "").casefold():
                raise ValueError("directory tree mismatch")
            if package_digest != str(receipt.get("packageDigest") or "").casefold():
                raise ValueError("package digest mismatch")
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
            evidence_error = "skill_trust_package_mismatch"
        if evidence_error and self.mode == "enforce":
            raise SkillTrustError(
                "Installed Skill bytes do not match the fixed trust receipt.",
                code="skill_trust_package_mismatch",
            )
        if evidence_error or receipt is None:
            return self.unverified_metadata()
        return self.receipt_metadata(receipt, verified_at=time.time())

    def reconcile_metadata(
        self,
        *,
        record: Mapping[str, Any],
        package_dir: Path | None,
    ) -> dict[str, Any]:
        source_kind = str(record.get("source_kind") or "git")
        repo_url = str(record.get("repo_url") or "")
        if source_kind != "git" or repo_url.startswith(("workspace://", "plugin://")):
            return {}
        if self.mode == "off":
            return {}
        receipt: dict[str, Any] | None = None
        try:
            receipt = self.resolve_source(
                repo_url,
                str(record.get("sub_path") or ""),
                str(record.get("source_ref") or ""),
            )
            digest = self.compute_directory_digest(package_dir) if package_dir else None
        except (OSError, SkillTrustError, ValueError):
            receipt = None
            digest = None
        if (
            receipt is None
            or not digest
            or digest != str(receipt.get("packageDigest") or "")
        ):
            return self.unverified_metadata()
        current_verified_at = record.get("trust_verified_at")
        verified_at = (
            float(current_verified_at)
            if isinstance(current_verified_at, (int, float))
            and not isinstance(current_verified_at, bool)
            and current_verified_at > 0
            else time.time()
        )
        return self.receipt_metadata(receipt, verified_at=verified_at)

    @staticmethod
    def receipt_metadata(
        receipt: Mapping[str, Any], *, verified_at: float
    ) -> dict[str, Any]:
        return {
            "trust_state": "receipt_matched",
            "trust_receipt_id": receipt.get("receiptId"),
            "trust_fingerprint": receipt.get("trustFingerprint"),
            "trust_risk_level": receipt.get("riskLevel"),
            "trust_status": receipt.get("trustStatus"),
            "trust_install_policy": receipt.get("installPolicy"),
            "trust_compatibility_status": receipt.get("compatibilityStatus"),
            "trust_package_digest": receipt.get("packageDigest"),
            "trust_directory_tree_sha": receipt.get("directoryTreeSha"),
            "trust_verified_at": verified_at,
        }

    @staticmethod
    def unverified_metadata() -> dict[str, Any]:
        return {
            "trust_state": "unverified_legacy",
            "trust_receipt_id": None,
            "trust_fingerprint": None,
            "trust_risk_level": None,
            "trust_status": "unknown",
            "trust_install_policy": "block",
            "trust_compatibility_status": "unsupported",
            "trust_package_digest": None,
            "trust_directory_tree_sha": None,
            "trust_verified_at": None,
        }

    @staticmethod
    def compute_directory_digest(package_dir: Path | None) -> str:
        if package_dir is None or not package_dir.is_dir():
            raise ValueError("Skill package directory is unavailable")
        files: dict[str, bytes] = {}
        for path in sorted(package_dir.rglob("*")):
            relative = path.relative_to(package_dir)
            if ".git" in relative.parts:
                continue
            if path.is_symlink():
                raise ValueError("Skill package contains a symbolic link")
            if not path.is_file():
                continue
            try:
                if path.stat().st_nlink > 1:
                    raise ValueError("Skill package contains a hard link")
            except OSError as exc:
                raise ValueError("Skill package metadata is unavailable") from exc
            files[relative.as_posix()] = path.read_bytes()
        if "SKILL.md" not in files:
            raise ValueError("Skill package is missing SKILL.md")
        return compute_skill_content_digest(files)

    def _evaluate_receipt(
        self,
        receipt: Mapping[str, Any],
        *,
        skill_id: str | None,
        ephemeral_trust_fingerprint: str | None,
        allow_pending_confirmation: bool,
        environment: SkillRuntimeEnvironment | None,
    ) -> SkillTrustDecision:
        reason_codes = tuple(
            dict.fromkeys(
                str(item.get("code") or "")
                for item in receipt.get("findings", [])
                if isinstance(item, Mapping) and str(item.get("code") or "")
            )
        )
        policy = str(receipt.get("installPolicy") or "block")
        fingerprint = str(receipt.get("trustFingerprint") or "")
        acknowledgement_required = policy == "confirm"
        acknowledged = not acknowledgement_required
        if acknowledgement_required and skill_id:
            try:
                acknowledged = self.acknowledgements.is_acknowledged(
                    skill_id, fingerprint
                )
            except SkillTrustError:
                acknowledged = False
        if (
            acknowledgement_required
            and ephemeral_trust_fingerprint
            and ephemeral_trust_fingerprint == fingerprint
        ):
            acknowledged = True
        missing = self._missing_runtime_capabilities(receipt, environment)
        blocked = (
            policy == "block"
            or str(receipt.get("trustStatus") or "") == "blocked"
            or str(receipt.get("compatibilityStatus") or "") == "unsupported"
        )
        allowed_by_policy = not blocked and (
            not acknowledgement_required
            or acknowledged
            or allow_pending_confirmation
        )
        allowed = allowed_by_policy and not missing
        if self.mode == "audit":
            allowed = True
        return SkillTrustDecision(
            mode=self.mode,
            allowed=allowed,
            receipt_id=str(receipt.get("receiptId") or "") or None,
            trust_fingerprint=fingerprint or None,
            risk_level=str(receipt.get("riskLevel") or "") or None,
            trust_status=str(receipt.get("trustStatus") or "unknown"),
            install_policy=policy,
            compatibility_status=str(
                receipt.get("compatibilityStatus") or "unsupported"
            ),
            router_eligible=bool(receipt.get("routerEligible")),
            acknowledgement_required=acknowledgement_required,
            acknowledgement_satisfied=acknowledged,
            reason_codes=reason_codes[:20],
            missing_capabilities=tuple(missing),
        )

    @staticmethod
    def _missing_runtime_capabilities(
        receipt: Mapping[str, Any],
        environment: SkillRuntimeEnvironment | None,
    ) -> list[str]:
        if environment is None:
            return []
        tools = set(environment.tool_names)
        providers = {item.casefold() for item in environment.tool_providers}
        commands = {item.casefold() for item in environment.sandbox_commands}
        capabilities = dict(receipt.get("capabilities") or {})
        missing: list[str] = []
        scripts = [item for item in receipt.get("scripts", []) if isinstance(item, Mapping)]
        if scripts:
            if "skill_stage" not in tools or "sandbox_shell" not in tools:
                missing.append("local_script_runtime")
            languages = {str(item.get("language") or "").casefold() for item in scripts}
            if "python" in languages and not {"python", "python3"}.intersection(commands):
                missing.append("python")
            if "javascript" in languages and "node" not in commands:
                missing.append("node")
        if capabilities.get("fileWrite") and "sandbox_write_file" not in tools:
            missing.append("sandbox_write_file")
        if capabilities.get("shell") and "sandbox_shell" not in tools:
            missing.append("sandbox_shell")
        if capabilities.get("packageManager") and not {
            "npm",
            "npx",
            "pnpm",
            "yarn",
            "pip",
            "pip3",
            "uv",
        }.intersection(commands):
            missing.append("package_manager")
        network_provider = any(
            token in provider
            for provider in providers
            for token in ("browser", "mcp", "external_xpert")
        )
        if capabilities.get("network") and not network_provider:
            missing.append("network")
        if capabilities.get("browser") and not any(
            name.startswith("browser_") for name in tools
        ):
            missing.append("browser")
        if capabilities.get("mcp") and not any("mcp" in provider for provider in providers):
            missing.append("mcp")
        if capabilities.get("credentials") and not environment.credentials_available:
            missing.append("credentials")
        if capabilities.get("hostFilesystem") and not environment.host_filesystem_available:
            missing.append("host_filesystem")
        if capabilities.get("desktopControl") and not environment.desktop_control_available:
            missing.append("desktop_control")
        if any(
            isinstance(finding, Mapping)
            and finding.get("code") == "trust_tool_unknown"
            for finding in receipt.get("findings", [])
        ):
            missing.append("allowed_tool:unknown")
        for raw_tool in receipt.get("allowedTools", []):
            tool = str(raw_tool or "").strip().casefold()
            expected: str | None = None
            if tool in {"read"}:
                expected = "sandbox_read_file"
            elif tool in {"grep", "glob"}:
                expected = "sandbox_search_files"
            elif tool in {"write", "edit"}:
                expected = "sandbox_write_file"
            elif "browser" in tool:
                expected = "browser"
            elif "bash" in tool or "shell" in tool:
                expected = "sandbox_shell"
            elif tool in {"skill_read", "skill_stage", "sandbox_read_file", "sandbox_search_files", "sandbox_list_files", "sandbox_write_file"}:
                expected = tool
            if expected == "browser" and not any(name.startswith("browser_") for name in tools):
                missing.append("allowed_tool:browser")
            elif expected and expected != "browser" and expected not in tools:
                missing.append(f"allowed_tool:{expected}")
        return sorted(set(missing))

    def _raise_if_denied(self, decision: SkillTrustDecision) -> None:
        if self.mode != "enforce" or decision.allowed:
            return
        details = decision.to_dict()
        if decision.receipt_id is None:
            code = (
                "skill_trust_index_unavailable"
                if "skill_trust_index_unavailable" in decision.reason_codes
                else "skill_trust_receipt_missing"
            )
            raise SkillTrustError(
                "Skill trust receipt is unavailable.",
                code=code,
                details=details,
            )
        if decision.missing_capabilities:
            raise SkillTrustError(
                "Skill requirements are unavailable in the current runtime.",
                code="skill_runtime_incompatible",
                details=details,
            )
        if decision.install_policy == "block" or decision.trust_status == "blocked":
            raise SkillTrustError(
                "Skill trust policy blocks this source.",
                code="skill_trust_policy_blocked",
                details=details,
            )
        if decision.acknowledgement_required and not decision.acknowledgement_satisfied:
            raise SkillTrustError(
                "This Skill version requires explicit local trust acknowledgement.",
                code="skill_trust_ack_required",
                details=details,
            )
        raise SkillTrustError(
            "Skill trust receipt is unavailable.",
            code="skill_trust_receipt_missing",
            details=details,
        )

    def _require_index(self) -> dict[str, Any]:
        self._load_index()
        if self._index is None:
            raise SkillTrustError(
                "Skill trust index is unavailable.",
                code="skill_trust_index_unavailable",
                details={"reason": self._load_error or "unavailable"},
            )
        return self._index

    def _load_index(self) -> None:
        with self._lock:
            try:
                stat = self.index_path.stat()
            except OSError as exc:
                self._index = None
                self._receipts_by_id = {}
                self._receipts_by_source = {}
                self._load_error = type(exc).__name__
                self._loaded_mtime_ns = None
                return
            if self._index is not None and self._loaded_mtime_ns == stat.st_mtime_ns:
                return
            try:
                payload = json.loads(self.index_path.read_text(encoding="utf-8"))
                receipts_by_id, receipts_by_source = self._validate_index(payload)
            except (
                OSError,
                ValueError,
                TypeError,
                json.JSONDecodeError,
                SkillTrustError,
            ) as exc:
                self._index = None
                self._receipts_by_id = {}
                self._receipts_by_source = {}
                self._load_error = type(exc).__name__
                self._loaded_mtime_ns = stat.st_mtime_ns
                return
            self._index = payload
            self._receipts_by_id = receipts_by_id
            self._receipts_by_source = receipts_by_source
            self._load_error = None
            self._loaded_mtime_ns = stat.st_mtime_ns

    @staticmethod
    def _validate_index(
        payload: Any,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        if (
            not isinstance(payload, dict)
            or payload.get("version") != SKILL_TRUST_INDEX_VERSION
            or not isinstance(payload.get("receipts"), list)
            or not isinstance(payload.get("candidateReceipts"), dict)
            or not re.fullmatch(r"[0-9a-f]{64}", str(payload.get("fingerprint") or ""))
            or not re.fullmatch(r"[0-9a-f]{64}", str(payload.get("catalogFingerprint") or ""))
        ):
            raise ValueError("Skill trust index schema is invalid")
        fingerprint_payload = {
            key: value for key, value in payload.items() if key != "fingerprint"
        }
        if sha256_json(fingerprint_payload) != payload["fingerprint"]:
            raise ValueError("Skill trust index fingerprint is invalid")
        by_id: dict[str, dict[str, Any]] = {}
        by_source: dict[str, dict[str, Any]] = {}
        for raw in payload["receipts"]:
            if not isinstance(raw, dict):
                raise ValueError("Skill trust receipt is invalid")
            receipt = dict(raw)
            receipt_id = _receipt_id(str(receipt.get("receiptId") or ""))
            fingerprint = _sha256(
                str(receipt.get("trustFingerprint") or ""), "trust fingerprint"
            )
            receipt_payload = {
                key: value
                for key, value in receipt.items()
                if key != "trustFingerprint"
            }
            if sha256_json(receipt_payload) != fingerprint:
                raise ValueError("Skill trust receipt fingerprint is invalid")
            source = receipt.get("source")
            if not isinstance(source, dict):
                raise ValueError("Skill trust receipt source is invalid")
            key = source_key(
                str(source.get("repoUrl") or ""),
                str(source.get("subPath") or ""),
                str(source.get("verifiedCommit") or ""),
            )
            if receipt_id in by_id or key in by_source:
                raise ValueError("Skill trust receipt is duplicated")
            if receipt.get("riskLevel") not in {"low", "medium", "high", "critical"}:
                raise ValueError("Skill trust risk is invalid")
            if receipt.get("installPolicy") not in {"allow", "confirm", "block"}:
                raise ValueError("Skill trust policy is invalid")
            if receipt.get("trustStatus") not in {"verified", "conditional", "blocked"}:
                raise ValueError("Skill trust status is invalid")
            if receipt.get("compatibilityStatus") not in {"portable", "conditional", "unsupported"}:
                raise ValueError("Skill compatibility is invalid")
            if not isinstance(receipt.get("routerEligible"), bool):
                raise ValueError("Skill Router eligibility is invalid")
            by_id[receipt_id] = receipt
            by_source[key] = receipt
        if any(
            not isinstance(candidate_id, str)
            or receipt_id not in by_id
            for candidate_id, receipt_id in payload["candidateReceipts"].items()
        ):
            raise ValueError("Skill trust candidate mapping is invalid")
        return by_id, by_source

    def _git_output(self, checkout_dir: Path, arguments: list[str]) -> str:
        completed = subprocess.run(
            ["git", "-C", str(checkout_dir), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=self.git_timeout_seconds,
        )
        if completed.returncode != 0:
            raise RuntimeError("Git trust verification failed")
        return completed.stdout.strip()

    def _validate_git_modes(self, checkout_dir: Path, source_dir: Path) -> None:
        relative = "" if source_dir == checkout_dir else source_dir.relative_to(checkout_dir).as_posix()
        arguments = ["ls-tree", "-r", "-z", "HEAD"]
        if relative:
            arguments.extend(["--", relative])
        completed = subprocess.run(
            ["git", "-C", str(checkout_dir), *arguments],
            check=False,
            capture_output=True,
            timeout=self.git_timeout_seconds,
        )
        if completed.returncode != 0:
            raise RuntimeError("Git mode verification failed")
        for record in completed.stdout.split(b"\0"):
            if not record:
                continue
            metadata, _separator, _path = record.partition(b"\t")
            mode, _object_type, _object_id = metadata.split(b" ", 2)
            if mode not in {b"100644", b"100755", b"120000", b"160000"}:
                raise ValueError("Unsupported Git file mode")

    def _missing_decision(self, reason: str) -> SkillTrustDecision:
        return SkillTrustDecision(
            mode=self.mode,
            allowed=self.mode != "enforce",
            receipt_id=None,
            trust_fingerprint=None,
            risk_level=None,
            trust_status="unknown",
            install_policy="block",
            compatibility_status="unsupported",
            router_eligible=False,
            acknowledgement_required=False,
            acknowledgement_satisfied=False,
            reason_codes=(reason,),
        )

    def _off_decision(self, *, trust_status: str = "off") -> SkillTrustDecision:
        return SkillTrustDecision(
            mode="off",
            allowed=True,
            receipt_id=None,
            trust_fingerprint=None,
            risk_level=None,
            trust_status=trust_status,
            install_policy="allow",
            compatibility_status="portable",
            router_eligible=True,
            acknowledgement_required=False,
            acknowledgement_satisfied=True,
            reason_codes=(),
        )


def _sha256(value: str, label: str) -> str:
    normalized = str(value or "").strip().casefold()
    if not re.fullmatch(r"[0-9a-f]{64}", normalized):
        raise SkillTrustError(
            f"Skill {label} is invalid.",
            code="skill_trust_receipt_missing",
        )
    return normalized


def _receipt_id(value: str) -> str:
    normalized = str(value or "").strip()
    if not re.fullmatch(r"skill-trust-[0-9a-f]{24}", normalized):
        raise SkillTrustError(
            "Skill trust receipt id is invalid.",
            code="skill_trust_receipt_missing",
        )
    return normalized


def _skill_id(value: str) -> str:
    normalized = str(value or "").strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,160}", normalized):
        raise SkillTrustError(
            "Skill id is invalid.",
            code="skill_trust_receipt_missing",
        )
    return normalized


def _string_set(value: Any) -> set[str]:
    if isinstance(value, (list, tuple, set, frozenset)):
        return {str(item).strip() for item in value if str(item).strip()}
    return {
        item.strip()
        for item in re.split(r"[,\n]", str(value or ""))
        if item.strip()
    }


__all__ = [
    "SKILL_TRUST_ACK_STORE_VERSION",
    "SKILL_TRUST_ERROR_CODES",
    "SkillRuntimeEnvironment",
    "SkillTrustAcknowledgement",
    "SkillTrustAcknowledgementStore",
    "SkillTrustDecision",
    "SkillTrustError",
    "SkillTrustGateMode",
    "SkillTrustService",
]
