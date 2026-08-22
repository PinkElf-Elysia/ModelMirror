from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Literal, Mapping


APPLICATION_RECEIPT_VERSION = "skill-application-receipt-v1"
ApplicationPolicy = Literal["advisory", "require_read", "require_stage"]
ApplicationMethod = Literal["prompt_injected", "skill_read", "skill_stage"]
ApplicationStatus = Literal["selected", "applied", "failed"]
ComplianceStatus = Literal["verified", "incomplete", "unverified"]

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,239}$")
_METHOD_ORDER = {"prompt_injected": 0, "skill_read": 1, "skill_stage": 2}
_MAX_RESOURCE_PATHS = 500
_MAX_REFERENCES = 64
_MAX_RECEIPTS = 2_000
_RETENTION_SECONDS = 30 * 24 * 60 * 60
_MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024


class SkillApplicationReceiptError(RuntimeError):
    def __init__(self, message: str, *, code: str = "skill_application_receipt_error"):
        super().__init__(message)
        self.code = code


class SkillApplicationReceiptStorageError(SkillApplicationReceiptError):
    pass


@dataclass(frozen=True, slots=True)
class SkillApplicationContractV1:
    contract_id: str
    version: str
    skill_id: str
    source_kind: str
    version_id: str | None
    content_digest: str | None
    trust_fingerprint: str | None
    policy: ApplicationPolicy
    required_resource_paths: tuple[str, ...]
    fingerprint: str


@dataclass(frozen=True, slots=True)
class SkillApplicationScope:
    run_id: str | None
    task_id: str | None
    node_id: str | None
    runtime_kind: str


@dataclass(slots=True)
class SkillApplicationReceiptV1:
    receipt_id: str
    version: str
    revision: int
    contract_id: str
    contract_fingerprint: str
    run_id: str | None
    task_id: str | None
    node_ids: tuple[str, ...]
    runtime_kind: str
    skill_id: str
    source_kind: str
    version_id: str | None
    content_digest: str | None
    trust_fingerprint: str | None
    policy: ApplicationPolicy
    required_resource_paths: tuple[str, ...]
    methods: tuple[ApplicationMethod, ...] = ()
    read_resource_paths: tuple[str, ...] = ()
    staged_resource_paths: tuple[str, ...] = ()
    resource_paths: tuple[str, ...] = ()
    resource_digests: dict[str, str] = field(default_factory=dict)
    resource_manifest_digest: str | None = None
    expected_resource_digests: dict[str, str] = field(default_factory=dict)
    expected_resource_manifest_digest: str | None = None
    resource_paths_truncated: bool = False
    tool_names: tuple[str, ...] = ()
    error_codes: tuple[str, ...] = ()
    references: tuple[str, ...] = ()
    application_status: ApplicationStatus = "selected"
    compliance_status: ComplianceStatus = "incomplete"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


def application_receipt_mode() -> Literal["off", "audit", "enforce"]:
    value = os.getenv("SKILL_APPLICATION_RECEIPT_MODE", "audit").strip().lower()
    return value if value in {"off", "audit", "enforce"} else "audit"  # type: ignore[return-value]


def build_application_contract(
    *,
    skill_id: str,
    source_kind: str,
    version_id: str | None,
    content_digest: str | None,
    trust_fingerprint: str | None = None,
    policy: ApplicationPolicy = "require_read",
    required_resource_paths: Iterable[str] = (),
) -> SkillApplicationContractV1:
    clean_skill_id = _safe_identifier(skill_id, "skill ID")
    clean_source = _safe_identifier(source_kind, "source kind")
    clean_version = _optional_identifier(version_id, "version ID")
    clean_digest = _optional_digest(content_digest, "content digest")
    clean_trust = _optional_digest(trust_fingerprint, "trust fingerprint")
    if policy not in {"advisory", "require_read", "require_stage"}:
        raise SkillApplicationReceiptError(
            "Invalid Skill application policy.", code="skill_application_contract_invalid"
        )
    clean_paths, paths_truncated = _resource_paths(required_resource_paths)
    if paths_truncated:
        raise SkillApplicationReceiptError(
            "Skill application contract declares too many resource paths.",
            code="skill_application_contract_limit",
        )
    identity = {
        "version": APPLICATION_RECEIPT_VERSION,
        "skill_id": clean_skill_id,
        "source_kind": clean_source,
        "version_id": clean_version,
        "content_digest": clean_digest,
        "trust_fingerprint": clean_trust,
        "policy": policy,
        "required_resource_paths": list(clean_paths),
    }
    fingerprint = _sha256_json(identity)
    return SkillApplicationContractV1(
        contract_id=f"skillappcontract_{fingerprint[:32]}",
        version=APPLICATION_RECEIPT_VERSION,
        skill_id=clean_skill_id,
        source_kind=clean_source,
        version_id=clean_version,
        content_digest=clean_digest,
        trust_fingerprint=clean_trust,
        policy=policy,
        required_resource_paths=clean_paths,
        fingerprint=fingerprint,
    )


class SkillApplicationReceiptStore:
    """Atomic, bounded evidence that a frozen Skill was actually applied."""

    SCHEMA_VERSION = 1

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        package_dir = Path(__file__).resolve().parent
        runtime_dir = os.getenv("AGENT_TASK_STORAGE_DIR", "").strip()
        self.storage_dir = Path(storage_dir or runtime_dir or package_dir / "storage")
        self.snapshot_path = self.storage_dir / "skill_application_receipts.json"
        self._lock = threading.RLock()
        self._receipts: dict[str, SkillApplicationReceiptV1] = {}
        self._quarantine: list[dict[str, Any]] = []
        self._load_error: str | None = None
        self._load()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "version": APPLICATION_RECEIPT_VERSION,
                "mode": application_receipt_mode(),
                "available": self._load_error is None,
                "receipt_count": len(self._receipts),
                "quarantine_count": len(self._quarantine),
                "error_code": (
                    "skill_application_receipt_store_corrupt"
                    if self._load_error
                    else None
                ),
            }

    def record_selection(
        self,
        contract: SkillApplicationContractV1,
        scope: SkillApplicationScope,
    ) -> SkillApplicationReceiptV1 | None:
        return self.observe(contract, scope)

    def observe(
        self,
        contract: SkillApplicationContractV1,
        scope: SkillApplicationScope,
        *,
        method: ApplicationMethod | None = None,
        resource_paths: Iterable[str] = (),
        read_resource_paths: Iterable[str] = (),
        resource_digests: Mapping[str, str] | None = None,
        expected_resource_digests: Mapping[str, str] | None = None,
        tool_name: str | None = None,
        error_code: str | None = None,
    ) -> SkillApplicationReceiptV1 | None:
        if application_receipt_mode() == "off":
            return None
        clean_scope = self._scope(scope)
        if method is not None and method not in _METHOD_ORDER:
            raise SkillApplicationReceiptError(
                "Invalid Skill application method.",
                code="skill_application_receipt_invalid",
            )
        observed_paths = tuple(resource_paths)
        explicit_read_paths, read_paths_truncated = _resource_paths(
            read_resource_paths
        )
        paths, paths_truncated = _resource_paths(
            (*observed_paths, *explicit_read_paths)
        )
        digests = self._resource_digests(resource_digests or {}, allowed_paths=paths)
        expected_digests = self._resource_digests(
            expected_resource_digests or {},
            allowed_paths={*paths, *contract.required_resource_paths},
        )
        clean_tool = _optional_identifier(tool_name, "tool name")
        clean_error = _optional_identifier(error_code, "error code")
        receipt_id = self._receipt_id(contract, clean_scope)
        with self._lock:
            self._ensure_writable_unlocked()
            previous = copy.deepcopy(self._receipts)
            previous_quarantine = copy.deepcopy(self._quarantine)
            existing = self._receipts.get(receipt_id)
            now = time.time()
            if existing is None:
                item = SkillApplicationReceiptV1(
                    receipt_id=receipt_id,
                    version=APPLICATION_RECEIPT_VERSION,
                    revision=1,
                    contract_id=contract.contract_id,
                    contract_fingerprint=contract.fingerprint,
                    run_id=clean_scope.run_id,
                    task_id=clean_scope.task_id,
                    node_ids=(clean_scope.node_id,) if clean_scope.node_id else (),
                    runtime_kind=clean_scope.runtime_kind,
                    skill_id=contract.skill_id,
                    source_kind=contract.source_kind,
                    version_id=contract.version_id,
                    content_digest=contract.content_digest,
                    trust_fingerprint=contract.trust_fingerprint,
                    policy=contract.policy,
                    required_resource_paths=contract.required_resource_paths,
                    created_at=now,
                    updated_at=now,
                )
            else:
                item = copy.deepcopy(existing)
            methods = set(item.methods)
            if method is not None and clean_error is None:
                methods.add(method)
            merged_paths = set(item.resource_paths)
            merged_paths.update(paths)
            all_paths, merged_truncated = _resource_paths(merged_paths)
            errors = set(item.error_codes)
            merged_digests = dict(item.resource_digests)
            for path, digest in digests.items():
                previous_digest = merged_digests.get(path)
                if previous_digest is not None and previous_digest != digest:
                    errors.add("skill_application_resource_digest_changed")
                    continue
                merged_digests[path] = digest
            merged_digests = {
                path: merged_digests[path]
                for path in all_paths
                if path in merged_digests
            }
            merged_expected_digests = dict(item.expected_resource_digests)
            for path, digest in expected_digests.items():
                previous_digest = merged_expected_digests.get(path)
                if previous_digest is not None and previous_digest != digest:
                    errors.add("skill_application_expected_digest_changed")
                    continue
                merged_expected_digests[path] = digest
            merged_expected_digests = {
                path: merged_expected_digests[path]
                for path in all_paths
                if path in merged_expected_digests
            }
            tools = set(item.tool_names)
            if clean_tool:
                tools.add(clean_tool)
            if clean_error:
                errors.add(clean_error)
            updated = copy.deepcopy(item)
            updated.node_ids = tuple(
                sorted(
                    {
                        *item.node_ids,
                        *(
                            (clean_scope.node_id,)
                            if clean_scope.node_id
                            else ()
                        ),
                    }
                )
            )
            updated.methods = tuple(sorted(methods, key=_METHOD_ORDER.__getitem__))
            read_paths = set(item.read_resource_paths)
            staged_paths = set(item.staged_resource_paths)
            if method == "skill_read" and clean_error is None:
                read_paths.update(paths)
            if clean_error is None:
                read_paths.update(explicit_read_paths)
            if method == "skill_stage" and clean_error is None:
                staged_paths.update(paths)
            updated.read_resource_paths = _resource_paths(read_paths)[0]
            updated.staged_resource_paths = _resource_paths(staged_paths)[0]
            updated.resource_paths = all_paths
            updated.resource_digests = dict(sorted(merged_digests.items()))
            updated.resource_manifest_digest = (
                _sha256_json(updated.resource_digests)
                if updated.resource_digests
                else None
            )
            updated.expected_resource_digests = dict(
                sorted(merged_expected_digests.items())
            )
            updated.expected_resource_manifest_digest = (
                _sha256_json(updated.expected_resource_digests)
                if updated.expected_resource_digests
                else None
            )
            evidence_paths = {
                *updated.read_resource_paths,
                *updated.staged_resource_paths,
            }
            if any(
                path not in updated.resource_digests
                or path not in updated.expected_resource_digests
                for path in evidence_paths
            ):
                errors.add("skill_application_resource_digest_missing")
            if any(
                path in updated.resource_digests
                and path in updated.expected_resource_digests
                and updated.resource_digests[path]
                != updated.expected_resource_digests[path]
                for path in evidence_paths
            ):
                errors.add("skill_application_resource_digest_mismatch")
            updated.resource_paths_truncated = bool(
                item.resource_paths_truncated
                or paths_truncated
                or read_paths_truncated
                or merged_truncated
            )
            updated.tool_names = tuple(sorted(tools))[:64]
            updated.error_codes = tuple(sorted(errors))[:64]
            updated.application_status = (
                "applied"
                if updated.methods
                else ("failed" if updated.error_codes else "selected")
            )
            updated.compliance_status = self._compliance_status(updated)
            updated.updated_at = now
            if existing is not None and self._equivalent(existing, updated):
                return copy.deepcopy(existing)
            if existing is not None:
                updated.revision = existing.revision + 1
            self._receipts[receipt_id] = updated
            self._prune_unlocked(now=now)
            try:
                self._save_unlocked()
            except Exception:
                self._receipts = previous
                self._quarantine = previous_quarantine
                raise
            return copy.deepcopy(updated)

    def protect(self, receipt_id: str, *, reference_id: str) -> SkillApplicationReceiptV1:
        clean_receipt_id = _safe_identifier(receipt_id, "receipt ID")
        clean_reference = _safe_identifier(reference_id, "reference ID")
        with self._lock:
            self._ensure_writable_unlocked()
            item = self._receipts.get(clean_receipt_id)
            if item is None:
                raise SkillApplicationReceiptError(
                    "Skill application receipt was not found.",
                    code="skill_application_receipt_missing",
                )
            references = tuple(sorted({*item.references, clean_reference}))
            if references == item.references:
                return copy.deepcopy(item)
            if len(references) > _MAX_REFERENCES:
                raise SkillApplicationReceiptError(
                    "Skill application receipt has too many references.",
                    code="skill_application_receipt_reference_limit",
                )
            previous = copy.deepcopy(self._receipts)
            updated = copy.deepcopy(item)
            updated.references = references
            updated.revision += 1
            updated.updated_at = time.time()
            self._receipts[clean_receipt_id] = updated
            try:
                self._save_unlocked()
            except Exception:
                self._receipts = previous
                raise
            return copy.deepcopy(updated)

    def require(self, receipt_id: str) -> SkillApplicationReceiptV1:
        clean = _safe_identifier(receipt_id, "receipt ID")
        with self._lock:
            self._ensure_readable_unlocked()
            item = self._receipts.get(clean)
            if item is None:
                raise SkillApplicationReceiptError(
                    "Skill application receipt was not found.",
                    code="skill_application_receipt_missing",
                )
            return copy.deepcopy(item)

    def require_verified(self, receipt_id: str) -> SkillApplicationReceiptV1:
        item = self.require(receipt_id)
        if item.compliance_status != "verified":
            raise SkillApplicationReceiptError(
                "Skill application receipt is incomplete.",
                code="skill_application_receipt_incomplete",
            )
        return item

    def list_receipts(
        self,
        *,
        run_id: str | None = None,
        task_id: str | None = None,
        skill_id: str | None = None,
    ) -> list[SkillApplicationReceiptV1]:
        with self._lock:
            self._ensure_readable_unlocked()
            items = list(self._receipts.values())
            if run_id is not None:
                items = [item for item in items if item.run_id == run_id]
            if task_id is not None:
                items = [item for item in items if item.task_id == task_id]
            if skill_id is not None:
                items = [item for item in items if item.skill_id == skill_id]
            return [
                copy.deepcopy(item)
                for item in sorted(items, key=lambda value: (value.created_at, value.receipt_id))
            ]

    def _load(self) -> None:
        if not self.snapshot_path.exists():
            return
        try:
            raw_bytes = self.snapshot_path.read_bytes()
            if len(raw_bytes) > _MAX_SNAPSHOT_BYTES:
                raise ValueError("snapshot too large")
            payload = json.loads(raw_bytes.decode("utf-8"))
            if not isinstance(payload, dict) or payload.get("schema_version") != self.SCHEMA_VERSION:
                raise ValueError("invalid schema")
            raw_receipts = payload.get("receipts")
            if not isinstance(raw_receipts, list):
                raise ValueError("invalid receipts")
            receipts: dict[str, SkillApplicationReceiptV1] = {}
            raw_quarantine = payload.get("quarantine") or []
            if not isinstance(raw_quarantine, list):
                raise ValueError("invalid quarantine")
            quarantine = [
                item
                for item in (
                    _decode_quarantine_record(raw) for raw in raw_quarantine[-200:]
                )
                if item is not None
            ]
            for raw in raw_receipts:
                try:
                    item = self._decode_receipt(raw)
                    if item.receipt_id in receipts:
                        raise ValueError("duplicate receipt")
                    receipts[item.receipt_id] = item
                except Exception:
                    encoded = json.dumps(
                        raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    ).encode("utf-8", errors="replace")
                    quarantine.append(
                        {
                            "code": "skill_application_receipt_record_invalid",
                            "sha256": hashlib.sha256(encoded).hexdigest(),
                            "size_bytes": len(encoded),
                        }
                    )
            self._receipts = receipts
            self._quarantine = quarantine[-200:]
        except Exception as exc:
            self._load_error = f"skill_application_receipt_store_corrupt:{type(exc).__name__}"

    def _save_unlocked(self) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": self.SCHEMA_VERSION,
            "version": APPLICATION_RECEIPT_VERSION,
            "receipts": [
                asdict(item)
                for item in sorted(self._receipts.values(), key=lambda value: value.receipt_id)
            ],
            "quarantine": list(self._quarantine[-200:]),
        }
        content = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if len(content) > _MAX_SNAPSHOT_BYTES:
            raise SkillApplicationReceiptStorageError(
                "Skill application receipt store reached its bounded capacity.",
                code="skill_application_receipt_store_limit",
            )
        temporary = self.snapshot_path.with_name(
            f".{self.snapshot_path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with temporary.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.snapshot_path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _prune_unlocked(self, *, now: float) -> None:
        expired = [
            receipt_id
            for receipt_id, item in self._receipts.items()
            if not item.references and now - item.updated_at > _RETENTION_SECONDS
        ]
        for receipt_id in expired:
            self._receipts.pop(receipt_id, None)
        unprotected = sorted(
            (item for item in self._receipts.values() if not item.references),
            key=lambda item: (item.updated_at, item.receipt_id),
        )
        overflow = max(0, len(self._receipts) - _MAX_RECEIPTS)
        for item in unprotected[:overflow]:
            self._receipts.pop(item.receipt_id, None)

    @classmethod
    def _decode_receipt(cls, raw: Any) -> SkillApplicationReceiptV1:
        if not isinstance(raw, dict):
            raise ValueError("receipt must be an object")
        contract = build_application_contract(
            skill_id=raw.get("skill_id"),
            source_kind=raw.get("source_kind"),
            version_id=raw.get("version_id"),
            content_digest=raw.get("content_digest"),
            trust_fingerprint=raw.get("trust_fingerprint"),
            policy=raw.get("policy"),
            required_resource_paths=raw.get("required_resource_paths") or (),
        )
        if raw.get("version") != APPLICATION_RECEIPT_VERSION:
            raise ValueError("invalid version")
        if raw.get("contract_id") != contract.contract_id or raw.get("contract_fingerprint") != contract.fingerprint:
            raise ValueError("invalid contract identity")
        scope = cls._scope(
            SkillApplicationScope(
                run_id=raw.get("run_id"),
                task_id=raw.get("task_id"),
                node_id=raw.get("node_id"),
                runtime_kind=raw.get("runtime_kind"),
            )
        )
        receipt_id = cls._receipt_id(contract, scope)
        if raw.get("receipt_id") != receipt_id:
            raise ValueError("invalid receipt identity")
        raw_methods = raw.get("methods") or []
        if not isinstance(raw_methods, list):
            raise ValueError("invalid methods")
        methods = tuple(raw_methods)
        if any(item not in _METHOD_ORDER for item in methods) or len(set(methods)) != len(methods):
            raise ValueError("invalid methods")
        raw_paths = raw.get("resource_paths") or []
        if not isinstance(raw_paths, list):
            raise ValueError("invalid resource paths")
        paths, paths_truncated = _resource_paths(raw_paths)
        read_paths = _decode_resource_path_list(raw.get("read_resource_paths"))
        staged_paths = _decode_resource_path_list(
            raw.get("staged_resource_paths")
        )
        if not set(read_paths).issubset(paths) or not set(staged_paths).issubset(
            paths
        ):
            raise ValueError("invalid method resource paths")
        digests = cls._resource_digests(raw.get("resource_digests") or {}, allowed_paths=paths)
        manifest = _sha256_json(digests) if digests else None
        if raw.get("resource_manifest_digest") != manifest:
            raise ValueError("invalid resource manifest")
        expected_digests = cls._resource_digests(
            raw.get("expected_resource_digests") or {}, allowed_paths=paths
        )
        expected_manifest = _sha256_json(expected_digests) if expected_digests else None
        if raw.get("expected_resource_manifest_digest") != expected_manifest:
            raise ValueError("invalid expected resource manifest")
        application_status = raw.get("application_status")
        compliance_status = raw.get("compliance_status")
        if application_status not in {"selected", "applied", "failed"}:
            raise ValueError("invalid application status")
        if compliance_status not in {"verified", "incomplete", "unverified"}:
            raise ValueError("invalid compliance status")
        item = SkillApplicationReceiptV1(
            receipt_id=receipt_id,
            version=APPLICATION_RECEIPT_VERSION,
            revision=max(1, int(raw.get("revision") or 0)),
            contract_id=contract.contract_id,
            contract_fingerprint=contract.fingerprint,
            run_id=scope.run_id,
            task_id=scope.task_id,
            node_ids=_decode_identifier_list(
                raw.get("node_ids"), field_name="node ID", limit=128
            ),
            runtime_kind=scope.runtime_kind,
            skill_id=contract.skill_id,
            source_kind=contract.source_kind,
            version_id=contract.version_id,
            content_digest=contract.content_digest,
            trust_fingerprint=contract.trust_fingerprint,
            policy=contract.policy,
            required_resource_paths=contract.required_resource_paths,
            methods=tuple(sorted(methods, key=_METHOD_ORDER.__getitem__)),
            read_resource_paths=read_paths,
            staged_resource_paths=staged_paths,
            resource_paths=paths,
            resource_digests=digests,
            resource_manifest_digest=manifest,
            expected_resource_digests=expected_digests,
            expected_resource_manifest_digest=expected_manifest,
            resource_paths_truncated=bool(raw.get("resource_paths_truncated") or paths_truncated),
            tool_names=_decode_identifier_list(
                raw.get("tool_names"), field_name="tool name", limit=64
            ),
            error_codes=_decode_identifier_list(
                raw.get("error_codes"), field_name="error code", limit=64
            ),
            references=_decode_identifier_list(
                raw.get("references"), field_name="reference ID", limit=64
            ),
            application_status=application_status,
            compliance_status=compliance_status,
            created_at=float(raw.get("created_at") or 0),
            updated_at=float(raw.get("updated_at") or 0),
        )
        if item.compliance_status != cls._compliance_status(item):
            raise ValueError("invalid compliance status")
        return item

    @staticmethod
    def _scope(scope: SkillApplicationScope) -> SkillApplicationScope:
        run_id = _optional_identifier(scope.run_id, "run ID")
        task_id = _optional_identifier(scope.task_id, "task ID")
        if not run_id and not task_id:
            raise SkillApplicationReceiptError(
                "A run ID or task ID is required.",
                code="skill_application_scope_invalid",
            )
        return SkillApplicationScope(
            run_id=run_id,
            task_id=task_id,
            node_id=_optional_identifier(scope.node_id, "node ID"),
            runtime_kind=_safe_identifier(scope.runtime_kind, "runtime kind"),
        )

    @staticmethod
    def _receipt_id(
        contract: SkillApplicationContractV1, scope: SkillApplicationScope
    ) -> str:
        fingerprint = _sha256_json(
            {
                "version": APPLICATION_RECEIPT_VERSION,
                "contract_fingerprint": contract.fingerprint,
                "run_id": scope.run_id,
                "task_id": scope.task_id,
                "runtime_kind": scope.runtime_kind,
            }
        )
        return f"skillappreceipt_{fingerprint[:32]}"

    @staticmethod
    def _resource_digests(
        values: Mapping[str, str], *, allowed_paths: Iterable[str]
    ) -> dict[str, str]:
        allowed = set(allowed_paths)
        result: dict[str, str] = {}
        for raw_path, raw_digest in values.items():
            path = _resource_path(raw_path)
            digest = _optional_digest(raw_digest, "resource digest")
            if path in allowed and digest:
                result[path] = digest
        return dict(sorted(result.items()))

    @staticmethod
    def _compliance_status(item: SkillApplicationReceiptV1) -> ComplianceStatus:
        if not item.version_id or not item.content_digest:
            return "unverified"
        if item.source_kind in {"git", "local_import"} and not item.trust_fingerprint:
            return "unverified"
        integrity_errors = {
            "skill_application_contract_stale",
            "skill_application_resource_digest_changed",
            "skill_application_expected_digest_changed",
            "skill_application_resource_digest_missing",
            "skill_application_resource_digest_mismatch",
        }
        if integrity_errors.intersection(item.error_codes):
            return "unverified"
        evidence_paths = {*item.read_resource_paths, *item.staged_resource_paths}
        if any(
            path not in item.resource_digests
            or path not in item.expected_resource_digests
            or item.resource_digests[path] != item.expected_resource_digests[path]
            for path in evidence_paths
        ):
            return "unverified"
        methods = set(item.methods)
        if item.policy == "advisory":
            satisfied = bool(methods)
        elif item.policy == "require_read":
            satisfied = "skill_read" in methods and set(
                item.required_resource_paths
            ).issubset(item.read_resource_paths)
        else:
            satisfied = {"skill_read", "skill_stage"}.issubset(methods) and set(
                item.required_resource_paths
            ).issubset(item.staged_resource_paths)
        return "verified" if satisfied else "incomplete"

    @staticmethod
    def _equivalent(
        left: SkillApplicationReceiptV1, right: SkillApplicationReceiptV1
    ) -> bool:
        ignored = {"revision", "updated_at"}
        left_payload = asdict(left)
        right_payload = asdict(right)
        for key in ignored:
            left_payload.pop(key, None)
            right_payload.pop(key, None)
        return left_payload == right_payload

    def _ensure_readable_unlocked(self) -> None:
        if self._load_error:
            raise SkillApplicationReceiptStorageError(
                "Skill application receipt store is unavailable.",
                code="skill_application_receipt_store_corrupt",
            )

    def _ensure_writable_unlocked(self) -> None:
        self._ensure_readable_unlocked()


class SkillApplicationObserver:
    """Resolve server-owned Skill identity before appending application evidence."""

    def __init__(
        self,
        store: SkillApplicationReceiptStore,
        skill_manager_provider: Callable[[], Any],
    ) -> None:
        self.store = store
        self.skill_manager_provider = skill_manager_provider

    def resolve_contract(
        self,
        skill_id: str,
        *,
        version_id: str | None = None,
        source_kind: str | None = None,
        content_digest: str | None = None,
        trust_fingerprint: str | None = None,
        policy: ApplicationPolicy = "require_read",
        required_resource_paths: Iterable[str] = (),
    ) -> SkillApplicationContractV1:
        manager = self.skill_manager_provider()
        clean_skill_id = str(skill_id or "").strip()
        clean_version_id = str(version_id or "").strip() or None
        clean_source_kind = str(source_kind or "").strip() or None
        clean_content_digest = str(content_digest or "").strip() or None
        clean_trust_fingerprint = str(trust_fingerprint or "").strip() or None
        if clean_version_id and (
            not clean_content_digest or not clean_source_kind
        ):
            snapshot = manager.lifecycle_store.require_version(clean_version_id)
            if snapshot.skill_id != clean_skill_id:
                raise SkillApplicationReceiptError(
                    "Skill application version does not match the selected Skill.",
                    code="skill_application_contract_mismatch",
                )
            clean_source_kind = clean_source_kind or snapshot.source_kind
            clean_content_digest = clean_content_digest or snapshot.package_digest
            clean_trust_fingerprint = (
                clean_trust_fingerprint or snapshot.trust_fingerprint
            )
        if not clean_content_digest or not clean_source_kind:
            installed = next(
                (
                    item
                    for item in manager.list_installed_skills()
                    if item.skill_id == clean_skill_id
                ),
                None,
            )
            if installed is not None:
                clean_source_kind = clean_source_kind or installed.source_kind
                clean_content_digest = (
                    clean_content_digest or installed.content_digest or None
                )
                clean_trust_fingerprint = (
                    clean_trust_fingerprint or installed.trust_fingerprint
                )
        return build_application_contract(
            skill_id=clean_skill_id,
            source_kind=clean_source_kind or "unknown",
            version_id=clean_version_id,
            content_digest=clean_content_digest,
            trust_fingerprint=clean_trust_fingerprint,
            policy=policy,
            required_resource_paths=required_resource_paths,
        )

    def record(
        self,
        *,
        skill_id: str,
        run_id: str | None,
        task_id: str | None,
        node_id: str | None,
        runtime_kind: str,
        version_id: str | None = None,
        source_kind: str | None = None,
        content_digest: str | None = None,
        trust_fingerprint: str | None = None,
        policy: ApplicationPolicy = "require_read",
        required_resource_paths: Iterable[str] = (),
        method: ApplicationMethod | None = None,
        resource_paths: Iterable[str] = (),
        read_resource_paths: Iterable[str] = (),
        resource_digests: Mapping[str, str] | None = None,
        expected_resource_digests: Mapping[str, str] | None = None,
        tool_name: str | None = None,
        error_code: str | None = None,
    ) -> SkillApplicationReceiptV1 | None:
        contract = self.resolve_contract(
            skill_id,
            version_id=version_id,
            source_kind=source_kind,
            content_digest=content_digest,
            trust_fingerprint=trust_fingerprint,
            policy=policy,
            required_resource_paths=required_resource_paths,
        )
        return self.store.observe(
            contract,
            SkillApplicationScope(
                run_id=run_id,
                task_id=task_id,
                node_id=node_id,
                runtime_kind=runtime_kind,
            ),
            method=method,
            resource_paths=resource_paths,
            read_resource_paths=read_resource_paths,
            resource_digests=resource_digests,
            expected_resource_digests=expected_resource_digests,
            tool_name=tool_name,
            error_code=error_code,
        )


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_identifier(value: Any, field_name: str) -> str:
    clean = str(value or "").strip()
    if not _SAFE_ID_RE.fullmatch(clean) or any(ord(char) < 32 for char in clean):
        raise SkillApplicationReceiptError(
            f"Invalid {field_name}.", code="skill_application_receipt_invalid"
        )
    return clean


def _optional_identifier(value: Any, field_name: str) -> str | None:
    if value is None or not str(value).strip():
        return None
    return _safe_identifier(value, field_name)


def _optional_digest(value: Any, field_name: str) -> str | None:
    if value is None or not str(value).strip():
        return None
    clean = str(value).strip().lower()
    if not _DIGEST_RE.fullmatch(clean):
        raise SkillApplicationReceiptError(
            f"Invalid {field_name}.", code="skill_application_receipt_invalid"
        )
    return clean


def _resource_path(value: Any) -> str:
    clean = str(value or "").replace("\\", "/").strip()
    path = PurePosixPath(clean)
    if (
        not clean
        or clean.startswith("/")
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or len(clean) > 240
    ):
        raise SkillApplicationReceiptError(
            "Invalid Skill resource path.",
            code="skill_application_receipt_invalid",
        )
    return path.as_posix()


def _resource_paths(values: Iterable[Any]) -> tuple[tuple[str, ...], bool]:
    normalized = sorted({_resource_path(value) for value in values})
    return tuple(normalized[:_MAX_RESOURCE_PATHS]), len(normalized) > _MAX_RESOURCE_PATHS


def _decode_identifier_list(
    value: Any, *, field_name: str, limit: int
) -> tuple[str, ...]:
    raw = value or []
    if not isinstance(raw, list) or len(raw) > limit:
        raise ValueError(f"invalid {field_name} list")
    return tuple(sorted({_safe_identifier(item, field_name) for item in raw}))


def _decode_resource_path_list(value: Any) -> tuple[str, ...]:
    raw = value or []
    if not isinstance(raw, list):
        raise ValueError("invalid resource path list")
    paths, truncated = _resource_paths(raw)
    if truncated:
        raise ValueError("resource path list exceeds limit")
    return paths


def _decode_quarantine_record(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    code = str(value.get("code") or "").strip()
    digest = str(value.get("sha256") or "").strip().lower()
    try:
        size_bytes = max(0, int(value.get("size_bytes") or 0))
    except (TypeError, ValueError):
        return None
    if code != "skill_application_receipt_record_invalid" or not _DIGEST_RE.fullmatch(
        digest
    ):
        return None
    return {"code": code, "sha256": digest, "size_bytes": size_bytes}


__all__ = [
    "APPLICATION_RECEIPT_VERSION",
    "SkillApplicationContractV1",
    "SkillApplicationReceiptError",
    "SkillApplicationObserver",
    "SkillApplicationReceiptStorageError",
    "SkillApplicationReceiptStore",
    "SkillApplicationReceiptV1",
    "SkillApplicationScope",
    "application_receipt_mode",
    "build_application_contract",
]
