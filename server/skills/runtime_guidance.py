from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Literal, Mapping

from .application_receipts import (
    SkillApplicationContractV1,
    SkillApplicationReceiptV1,
)


SKILL_RUNTIME_GUIDANCE_VERSION = "skill-runtime-guidance-v2"
SkillGuidanceRequirement = Literal["required", "available"]
SkillGuidanceSource = Literal["explicit", "activated", "plugin"]

_GUIDANCE_HELPER_TOOLS = frozenset(
    {
        "skill_list",
        "skill_read",
        "skill_stage",
        "skill_find",
        "skill_enable",
        "sandbox_list_files",
        "sandbox_read_file",
        "sandbox_search_files",
    }
)


class SkillRuntimeGuidanceError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class SkillRuntimeGuidanceEntryV2:
    skill_id: str
    requirement: SkillGuidanceRequirement
    sources: tuple[SkillGuidanceSource, ...]
    contract_id: str
    contract_fingerprint: str
    version_id: str
    source_kind: str
    content_digest: str
    trust_fingerprint: str | None


@dataclass(frozen=True, slots=True)
class SkillRuntimeGuidancePlanV2:
    version: str
    task_id: str
    run_id: str
    node_id: str
    entries: tuple[SkillRuntimeGuidanceEntryV2, ...]
    auto_discover: bool
    fingerprint: str

    @property
    def required_skill_ids(self) -> tuple[str, ...]:
        return tuple(
            entry.skill_id
            for entry in self.entries
            if entry.requirement == "required"
        )

    @property
    def available_skill_ids(self) -> tuple[str, ...]:
        return tuple(
            entry.skill_id
            for entry in self.entries
            if entry.requirement == "available"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "node_id": self.node_id,
            "entries": [asdict(entry) for entry in self.entries],
            "auto_discover": self.auto_discover,
            "fingerprint": self.fingerprint,
        }


def skill_runtime_guidance_enabled() -> bool:
    return os.getenv(
        "SKILL_RUNTIME_GUIDANCE_V2_ENABLED", "true"
    ).strip().lower() in {"1", "true", "yes", "on"}


def skill_guidance_plan_status_events(
    plan: SkillRuntimeGuidancePlanV2,
) -> tuple[dict[str, Any], ...]:
    """Project a guidance plan into bounded, content-free runtime events."""

    return tuple(
        {
            "event": "skill_runtime_status",
            "status": entry.requirement,
            "skill_id": entry.skill_id,
            "requirement": entry.requirement,
            "skill_version_id": entry.version_id,
            "source_kind": entry.source_kind,
            "run_id": plan.run_id,
        }
        for entry in plan.entries
    )


def build_skill_runtime_guidance_plan(
    *,
    task_id: str,
    run_id: str,
    node_id: str,
    explicit_skill_ids: Iterable[str],
    plugin_skill_ids: Iterable[str],
    activated_skill_ids: Iterable[str],
    auto_discover: bool,
    contracts: Mapping[str, SkillApplicationContractV1],
) -> SkillRuntimeGuidancePlanV2:
    clean_task_id = _required_text(task_id, "task ID")
    clean_run_id = _required_text(run_id, "run ID")
    clean_node_id = _required_text(node_id, "node ID")
    explicit = _skill_ids(explicit_skill_ids)
    plugin = _skill_ids(plugin_skill_ids)
    activated = _skill_ids(activated_skill_ids)
    required = explicit | activated
    all_known = required | plugin
    entries: list[SkillRuntimeGuidanceEntryV2] = []
    for skill_id in sorted(all_known):
        contract = contracts.get(skill_id)
        if contract is None:
            if skill_id in required:
                raise SkillRuntimeGuidanceError(
                    "Required Skill no longer has a frozen application contract.",
                    code="skill_application_contract_stale",
                )
            continue
        if not contract.version_id or not contract.content_digest:
            if skill_id in required:
                raise SkillRuntimeGuidanceError(
                    "Required Skill version evidence is incomplete.",
                    code="skill_application_evidence_unavailable",
                )
            continue
        if (
            contract.source_kind in {"git", "local_import"}
            and not contract.trust_fingerprint
        ):
            if skill_id in required:
                raise SkillRuntimeGuidanceError(
                    "Required third-party Skill trust evidence is unavailable.",
                    code="skill_application_evidence_unavailable",
                )
            continue
        sources: list[SkillGuidanceSource] = []
        if skill_id in explicit:
            sources.append("explicit")
        if skill_id in activated:
            sources.append("activated")
        if skill_id in plugin:
            sources.append("plugin")
        entries.append(
            SkillRuntimeGuidanceEntryV2(
                skill_id=skill_id,
                requirement="required" if skill_id in required else "available",
                sources=tuple(sources),
                contract_id=contract.contract_id,
                contract_fingerprint=contract.fingerprint,
                version_id=contract.version_id,
                source_kind=contract.source_kind,
                content_digest=contract.content_digest,
                trust_fingerprint=contract.trust_fingerprint,
            )
        )
    identity = {
        "version": SKILL_RUNTIME_GUIDANCE_VERSION,
        "task_id": clean_task_id,
        "run_id": clean_run_id,
        "node_id": clean_node_id,
        "entries": [asdict(entry) for entry in entries],
        "auto_discover": bool(auto_discover),
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return SkillRuntimeGuidancePlanV2(
        version=SKILL_RUNTIME_GUIDANCE_VERSION,
        task_id=clean_task_id,
        run_id=clean_run_id,
        node_id=clean_node_id,
        entries=tuple(entries),
        auto_discover=bool(auto_discover),
        fingerprint=fingerprint,
    )


def missing_required_skill_ids(
    plan: SkillRuntimeGuidancePlanV2,
    receipts: Iterable[SkillApplicationReceiptV1 | Any],
) -> tuple[str, ...]:
    verified_contracts = {
        str(getattr(receipt, "contract_fingerprint", ""))
        for receipt in receipts
        if str(getattr(receipt, "run_id", "")) == plan.run_id
        and str(getattr(receipt, "task_id", "")) == plan.task_id
        and plan.node_id in tuple(getattr(receipt, "node_ids", ()) or ())
        and str(getattr(receipt, "compliance_status", "")) == "verified"
    }
    return tuple(
        entry.skill_id
        for entry in plan.entries
        if entry.requirement == "required"
        and entry.contract_fingerprint not in verified_contracts
    )


def tool_requires_skill_application(
    *,
    tool_name: str,
    read_only: bool,
    sensitive: bool,
    requires_approval: bool,
    terminal: bool,
) -> bool:
    clean_name = str(tool_name or "").strip()
    if clean_name in _GUIDANCE_HELPER_TOOLS:
        return False
    return bool(not read_only or sensitive or requires_approval or terminal)


def skill_application_repair_instruction(skill_ids: Iterable[str]) -> str:
    missing = sorted(_skill_ids(skill_ids))
    if not missing:
        raise SkillRuntimeGuidanceError(
            "Skill application repair requires at least one Skill.",
            code="skill_application_required",
        )
    return (
        "Required Skill application is incomplete. Before any final answer or "
        "side-effecting tool, call skill_read for each of these server-selected "
        f"Skill IDs: {', '.join(missing)}. Do not claim that a Skill was read "
        "without the tool result. Then continue the original task using the "
        "instructions that were returned."
    )


def _required_text(value: Any, field_name: str) -> str:
    clean = str(value or "").strip()
    if not clean or len(clean) > 240 or any(ord(char) < 32 for char in clean):
        raise SkillRuntimeGuidanceError(
            f"Invalid {field_name}.",
            code="skill_application_contract_stale",
        )
    return clean


def _skill_ids(values: Iterable[str]) -> set[str]:
    result: set[str] = set()
    for value in values:
        clean = str(value or "").strip()
        if not clean:
            continue
        if len(clean) > 240 or any(ord(char) < 32 for char in clean):
            raise SkillRuntimeGuidanceError(
                "Invalid Skill ID in guidance plan.",
                code="skill_application_contract_stale",
            )
        result.add(clean)
    return result
