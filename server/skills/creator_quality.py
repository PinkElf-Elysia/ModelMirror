from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

from .hook_contract import HOOK_MANIFEST_PATH
from .package_validation import SkillPackageV2, validate_skill_package


CREATOR_CONTRACT_VERSION = "skill-creator-contract-v1"
CREATOR_QUALITY_VERSION = "skill-creator-draft-quality-v1"
CREATOR_PLAYBOOK_VERSION = "claude-aligned-authoring-v1"
MAX_CREATOR_SKILL_MARKDOWN_CHARS = 6_000
MAX_CREATOR_RESOURCES = 6
MAX_CREATOR_RESOURCE_CHARS = 6_000
MAX_CREATOR_PAYLOAD_BYTES = 24_000
MAX_RESOURCE_BUILD_SKILL_MARKDOWN_CHARS = 20 * 1024
MAX_RESOURCE_BUILD_RESOURCES = 20
MAX_RESOURCE_BUILD_RESOURCE_CHARS = 24 * 1024
MAX_RESOURCE_BUILD_PAYLOAD_BYTES = 180 * 1024

_REFERENCE_DIR = Path(__file__).resolve().parent / "creator_reference"
_PLAYBOOK_PATH = _REFERENCE_DIR / "authoring-playbook-v1.md"

_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)
_HEADING_LINE_RE = re.compile(
    r"^(?P<marks>#{1,6})\s+(?P<title>.+?)\s*#*\s*$", re.MULTILINE
)
_FENCE_OPEN_RE = re.compile(r"^[ \t]*(?P<fence>`{3,}|~{3,})")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_TRIGGER_RE = re.compile(
    r"(?i)(?:\buse\s+when\b|\bwhen\s+(?:the\s+)?(?:user|task|agent)\b|"
    r"\btrigger(?:s|ed|ing)?\b|\bspecifically\s+for\b|"
    r"\b(?:apply|applies)\s+(?:during|to)\b|\b(?:intended|designed)\s+for\b|"
    r"适用于|用于.{0,24}(?:任务|场景|情况)|"
    r"当.{0,40}时|在[^。；;.!?]{1,40}(?:时|前|后)|触发(?:条件|场景)?)"
)
_CJK_CHAR_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_SCOPE_RE = re.compile(
    r"(?i)(?:^|\n)#{1,6}\s+(?:scope|purpose|boundaries|overview|when\s+to\s+use|"
    r"capabilit(?:y|ies)|use\s+cases?|用途|目标|概述|适用范围|适用场景|使用场景|"
    r"目的(?:与范围)?|能力与边界|何时使用|使用时机|边界)[^\n]*"
)
_WORKFLOW_RE = re.compile(
    r"(?i)(?:^|\n)#{1,6}\s+(?:(?:[^\n#]{1,60}\s+)?workflow\b|"
    r"procedure\b|process\b|steps?\b|[^\n#]{0,60}(?:工作流|流程|步骤))[^\n]*"
)
_INPUT_RE = re.compile(
    r"(?i)(?:^|\n)#{1,6}\s+(?:inputs?(?:\s+and\s+(?:preconditions?|prerequisites?))?|"
    r"preconditions?|prerequisites?|requirements?|输入|前置条件|先决条件|准备工作)[^\n]*"
)
_OUTPUT_RE = re.compile(
    r"(?i)(?:^|\n)#{1,6}\s+(?:output(?:\s+contract|\s+format)?|deliverables?|输出(?:合同|格式|要求)?|交付物)[^\n]*"
)
_FAILURE_RE = re.compile(
    r"(?i)(?:^|\n)#{1,6}\s+(?:fail(?:ure)?(?:\s+handling)?|errors?|degradation|fallback|"
    r"失败(?:处理|行为)?|错误处理|降级|回退|异常处理)[^\n]*"
)
_QUALITY_RE = re.compile(
    r"(?i)(?:^|\n)#{1,6}\s+(?:quality(?:\s+checks?)?|validation|verification|self[- ]check|"
    r"质量(?:检查|门槛)?|校验|验证|自检|验收)[^\n]*"
)
_PLACEHOLDER_RE = re.compile(
    r"(?i)(?:\b(?:todo|tbd|fixme|xxx|placeholder)\b|"
    r"\[(?:replace|insert|fill)[^\]]*\]|<[^>]*(?:replace|insert|fill)[^>]*>|"
    r"待补充|待完善|稍后完善|在此(?:填写|插入)|请(?:补充|填写))"
)

_CHECK_LABELS = {
    "contract_version": "Creator 合同版本",
    "package_structure": "包结构与安全",
    "instruction_depth": "可执行指令完整度",
    "description_trigger": "能力与触发描述",
    "scope": "用途与边界",
    "inputs_preconditions": "输入与前置条件",
    "workflow": "可执行工作流",
    "output_contract": "输出合同",
    "failure_behavior": "失败与降级行为",
    "quality_checks": "质量检查",
    "no_placeholders": "无占位内容",
    "resource_plan": "资源计划",
    "requirement_coverage": "会话需求覆盖",
}


def _description_has_actionable_trigger(description: str) -> bool:
    """Apply equivalent detail floors to compact CJK and space-delimited prose."""

    text = str(description or "").strip()
    cjk_count = len(_CJK_CHAR_RE.findall(text))
    minimum_length = 48 if cjk_count >= 24 else 80
    return len(text) >= minimum_length and bool(_TRIGGER_RE.search(text))


@dataclass(frozen=True, slots=True)
class CreatorRequirement:
    requirement_id: str
    kind: str
    text: str

    def to_dict(self) -> dict[str, str]:
        return {
            "requirement_id": self.requirement_id,
            "kind": self.kind,
            "text": self.text,
        }


@dataclass(frozen=True, slots=True)
class CreatorQualityIssue:
    code: str
    message: str
    severity: str = "error"
    path: str | None = None
    field: str | None = None
    requirement_id: str | None = None

    def to_dict(self) -> dict[str, str]:
        return {
            key: value
            for key, value in {
                "code": self.code,
                "message": self.message,
                "severity": self.severity,
                "path": self.path,
                "field": self.field,
                "requirement_id": self.requirement_id,
            }.items()
            if value is not None
        }


@dataclass(frozen=True, slots=True)
class CreatorQualityCheck:
    check_id: str
    passed: bool
    weight: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.check_id,
            "check_id": self.check_id,
            "label": _CHECK_LABELS.get(self.check_id, self.check_id),
            "passed": self.passed,
            "weight": self.weight,
        }


@dataclass(frozen=True, slots=True)
class CreatorDraftQualityReport:
    ready: bool
    score: int
    issues: tuple[CreatorQualityIssue, ...]
    checks: tuple[CreatorQualityCheck, ...]
    requirement_ids: tuple[str, ...]
    contract_version: str | None
    quality_version: str = CREATOR_QUALITY_VERSION
    playbook_version: str = CREATOR_PLAYBOOK_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "score": self.score,
            "quality_version": self.quality_version,
            "contract_version": self.contract_version,
            "playbook_version": self.playbook_version,
            "requirement_ids": list(self.requirement_ids),
            "checks": [check.to_dict() for check in self.checks],
            "issues": [issue.to_dict() for issue in self.issues],
        }


def build_session_requirements(
    *,
    intent: str = "",
    positive_examples: Sequence[str] = (),
    near_miss_examples: Sequence[str] = (),
    expected_output: str = "",
    success_criteria: Sequence[str] = (),
) -> tuple[CreatorRequirement, ...]:
    """Build deterministic IDs for the requirements captured by a Creator session."""

    requirements: list[CreatorRequirement] = []
    if _clean_text(intent):
        requirements.append(CreatorRequirement("intent", "intent", intent.strip()))
    for index, text in enumerate(positive_examples):
        if _clean_text(text):
            requirements.append(
                CreatorRequirement(
                    f"positive_example:{index}", "positive_example", text.strip()
                )
            )
    for index, text in enumerate(near_miss_examples):
        if _clean_text(text):
            requirements.append(
                CreatorRequirement(f"near_miss:{index}", "near_miss", text.strip())
            )
    if _clean_text(expected_output):
        requirements.append(
            CreatorRequirement("expected_output", "expected_output", expected_output.strip())
        )
    for index, text in enumerate(success_criteria):
        if _clean_text(text):
            requirements.append(
                CreatorRequirement(
                    f"success_criterion:{index}", "success_criterion", text.strip()
                )
            )
    return tuple(requirements)


def build_session_requirement_ids(**values: Any) -> tuple[str, ...]:
    return tuple(
        requirement.requirement_id for requirement in build_session_requirements(**values)
    )


def load_creator_authoring_playbook() -> str:
    """Return the pinned, local creation-stage guide used by the trusted Creator."""

    text = _PLAYBOOK_PATH.read_text(encoding="utf-8")
    marker = f"playbook-version: {CREATOR_PLAYBOOK_VERSION}"
    if marker not in text:
        raise RuntimeError("Creator authoring playbook version marker is missing.")
    return text


def evaluate_creator_payload(
    payload: Any,
    *,
    requirements: Sequence[CreatorRequirement] = (),
    requirement_ids: Sequence[str] = (),
    resource_build: bool = False,
) -> CreatorDraftQualityReport:
    """Evaluate a workflow-agent Creator draft without affecting generic Skills.

    Callers must opt into this function. It deliberately is not part of
    ``validate_skill_package`` so manual, imported and legacy Skills retain their
    existing structural and security contract.
    """

    issues: list[CreatorQualityIssue] = []
    checks: list[CreatorQualityCheck] = []
    expected_ids = _expected_requirement_ids(requirements, requirement_ids, issues)
    contract_version: str | None = None

    if not isinstance(payload, Mapping):
        issues.append(
            CreatorQualityIssue(
                code="creator_payload_type",
                message="Creator output must be a wrapped package mapping.",
                field="payload",
            )
        )
        return _report(issues, checks, expected_ids, contract_version)

    _validate_generation_budget(payload, issues, resource_build=resource_build)

    raw_contract = payload.get("creator_contract_version")
    if isinstance(raw_contract, str):
        contract_version = raw_contract
    contract_ok = raw_contract == CREATOR_CONTRACT_VERSION
    if not contract_ok:
        issues.append(
            CreatorQualityIssue(
                code="creator_contract_version_unsupported",
                message=f"Creator output must use {CREATOR_CONTRACT_VERSION}.",
                field="creator_contract_version",
            )
        )
    checks.append(CreatorQualityCheck("contract_version", contract_ok, 5))

    raw_skill = payload.get("skill")
    package = _validate_wrapped_skill(raw_skill, issues)
    package_ok = package is not None
    checks.append(CreatorQualityCheck("package_structure", package_ok, 5))

    raw_design = payload.get("design")
    design = raw_design if isinstance(raw_design, Mapping) else None
    if design is None:
        issues.append(
            CreatorQualityIssue(
                code="creator_design_missing",
                message="Creator output must include a structured design coverage map.",
                field="design",
            )
        )

    if package is None:
        return _report(issues, checks, expected_ids, contract_version)

    body = _markdown_body(package.skill_markdown)
    structural_body = _structural_markdown(body)
    description_ok = _description_has_actionable_trigger(package.description)
    if not description_ok:
        issues.append(
            CreatorQualityIssue(
                code="creator_description_trigger_missing",
                message="Description must clearly state the capability and when the Skill should trigger.",
                path="SKILL.md",
                field="description",
            )
        )
    checks.append(CreatorQualityCheck("description_trigger", description_ok, 10))

    scope_ok = bool(_SCOPE_RE.search(structural_body))
    if not scope_ok:
        issues.append(
            CreatorQualityIssue(
                code="creator_scope_missing",
                message="SKILL.md must define its purpose, scope, or boundaries.",
                path="SKILL.md",
            )
        )
    checks.append(CreatorQualityCheck("scope", scope_ok, 8))

    inputs_ok = bool(_INPUT_RE.search(structural_body))
    if not inputs_ok:
        issues.append(
            CreatorQualityIssue(
                code="creator_inputs_preconditions_missing",
                message="SKILL.md must define inputs, prerequisites, or preconditions.",
                path="SKILL.md",
            )
        )
    checks.append(CreatorQualityCheck("inputs_preconditions", inputs_ok, 5))

    workflow_steps = _workflow_design_items(design, issues)
    workflow_ok = (
        len(workflow_steps) >= 4
        and bool(_WORKFLOW_RE.search(structural_body))
        and _executable_workflow_step_count(structural_body) >= 4
    )
    if not workflow_ok:
        issues.append(
            CreatorQualityIssue(
                code="creator_workflow_missing",
                message="Creator drafts must define at least four ordered workflow steps in design and SKILL.md.",
                path="SKILL.md",
                field="design.workflow_steps",
            )
        )
    checks.append(CreatorQualityCheck("workflow", workflow_ok, 10))

    output_contract = _design_items(design, "output_contract", issues)
    output_ok = bool(output_contract) and bool(_OUTPUT_RE.search(structural_body))
    if not output_ok:
        issues.append(
            CreatorQualityIssue(
                code="creator_output_contract_missing",
                message="Creator drafts must define a concrete output contract in design and SKILL.md.",
                path="SKILL.md",
                field="design.output_contract",
            )
        )
    checks.append(CreatorQualityCheck("output_contract", output_ok, 12))

    failure_modes = _design_items(design, "failure_modes", issues)
    failure_ok = bool(failure_modes) and bool(_FAILURE_RE.search(structural_body))
    if not failure_ok:
        issues.append(
            CreatorQualityIssue(
                code="creator_failure_behavior_missing",
                message="Creator drafts must define failure or degradation behavior in design and SKILL.md.",
                path="SKILL.md",
                field="design.failure_modes",
            )
        )
    checks.append(CreatorQualityCheck("failure_behavior", failure_ok, 12))

    if design is not None:
        assumptions = design.get("assumptions")
        if not isinstance(assumptions, list) or any(
            not _clean_text(item) for item in assumptions
        ):
            issues.append(
                CreatorQualityIssue(
                    code="creator_assumptions_invalid",
                    message="design.assumptions must be a list of explicit non-empty assumptions.",
                    field="design.assumptions",
                )
            )

    quality_ok = bool(_QUALITY_RE.search(structural_body))
    if not quality_ok:
        issues.append(
            CreatorQualityIssue(
                code="creator_quality_checks_missing",
                message="SKILL.md must include explicit validation or quality checks.",
                path="SKILL.md",
            )
        )
    checks.append(CreatorQualityCheck("quality_checks", quality_ok, 8))

    body_length = len(structural_body.strip())
    heading_count = len(_HEADING_RE.findall(structural_body))
    referenced_script = any(
        path.startswith("scripts/") and path in package.skill_markdown
        for path in package.files
    )
    complete_design = workflow_ok and output_ok and failure_ok and quality_ok
    body_depth_ok = heading_count >= 5 and (
        body_length >= 600
        or (body_length >= 350 and complete_design)
        or (body_length >= 300 and complete_design and referenced_script)
    )
    if not body_depth_ok:
        issues.append(
            CreatorQualityIssue(
                code="creator_body_too_thin",
                message=(
                    "Creator drafts need five clear sections plus enough executable "
                    "instructions, a complete design, or a referenced deterministic script."
                ),
                path="SKILL.md",
            )
        )
    checks.append(CreatorQualityCheck("instruction_depth", body_depth_ok, 10))

    placeholder_ok = not _PLACEHOLDER_RE.search(package.skill_markdown) and not any(
        _PLACEHOLDER_RE.search(content) for content in package.files.values()
    )
    if not placeholder_ok:
        issues.append(
            CreatorQualityIssue(
                code="creator_placeholder_remaining",
                message="Creator-generated drafts cannot retain TODO or placeholder content.",
            )
        )
    checks.append(CreatorQualityCheck("no_placeholders", placeholder_ok, 5))

    resource_ok = _validate_resources(package, design, workflow_steps, issues)
    checks.append(CreatorQualityCheck("resource_plan", resource_ok, 5))

    coverage_ok = _validate_coverage(package, design, expected_ids, issues)
    checks.append(CreatorQualityCheck("requirement_coverage", coverage_ok, 5))

    return _report(issues, checks, expected_ids, contract_version)


def evaluate_creator_final_package(
    *,
    root_name: str,
    skill_markdown: str,
    files: Mapping[str, str] | None = None,
) -> CreatorDraftQualityReport:
    """Validate the installable completeness of a Creator package without design data.

    Creator proposals retain a richer design and coverage map, while immutable Draft
    revisions intentionally store only package bytes.  This final gate therefore
    checks the executable package itself and is safe to repeat before evaluation,
    review recovery, waiver, and installation.
    """

    issues: list[CreatorQualityIssue] = []
    checks: list[CreatorQualityCheck] = []
    result = validate_skill_package(
        root_name=root_name,
        skill_markdown=skill_markdown,
        files=dict(files or {}),
    )
    package = result.package
    package_ok = package is not None
    checks.append(CreatorQualityCheck("package_structure", package_ok, 5))
    if package is None:
        issues.append(
            CreatorQualityIssue(
                code="creator_package_invalid",
                message="Creator package failed the structural and security validator.",
                field="skill",
            )
        )
        return _report(issues, checks, (), None)

    body = _markdown_body(package.skill_markdown)
    structural_body = _structural_markdown(body)

    description_ok = _description_has_actionable_trigger(package.description)
    if not description_ok:
        issues.append(
            CreatorQualityIssue(
                code="creator_description_trigger_missing",
                message="Description must state the capability and when it should trigger.",
                path="SKILL.md",
                field="description",
            )
        )
    checks.append(CreatorQualityCheck("description_trigger", description_ok, 10))

    scope_ok = bool(_SCOPE_RE.search(structural_body))
    if not scope_ok:
        issues.append(
            CreatorQualityIssue(
                code="creator_scope_missing",
                message="SKILL.md must define its purpose, scope, or boundaries.",
                path="SKILL.md",
            )
        )
    checks.append(CreatorQualityCheck("scope", scope_ok, 10))

    inputs_ok = _section_has_substantive_content(structural_body, _INPUT_RE)
    if not inputs_ok:
        issues.append(
            CreatorQualityIssue(
                code="creator_inputs_preconditions_missing",
                message="SKILL.md must contain substantive inputs or prerequisites.",
                path="SKILL.md",
            )
        )
    checks.append(CreatorQualityCheck("inputs_preconditions", inputs_ok, 10))

    workflow_steps = _executable_workflow_steps(structural_body)
    workflow_ok = len(workflow_steps) >= 4 and _instructions_are_distinct(workflow_steps)
    if not workflow_ok:
        issues.append(
            CreatorQualityIssue(
                code="creator_workflow_missing",
                message="SKILL.md must contain at least four distinct executable workflow steps.",
                path="SKILL.md",
            )
        )
    checks.append(CreatorQualityCheck("workflow", workflow_ok, 20))

    output_ok = _section_has_substantive_content(structural_body, _OUTPUT_RE)
    if not output_ok:
        issues.append(
            CreatorQualityIssue(
                code="creator_output_contract_missing",
                message="SKILL.md must contain a substantive output contract.",
                path="SKILL.md",
            )
        )
    checks.append(CreatorQualityCheck("output_contract", output_ok, 15))

    failure_ok = _section_has_substantive_content(structural_body, _FAILURE_RE)
    if not failure_ok:
        issues.append(
            CreatorQualityIssue(
                code="creator_failure_behavior_missing",
                message="SKILL.md must contain substantive failure or degradation behavior.",
                path="SKILL.md",
            )
        )
    checks.append(CreatorQualityCheck("failure_behavior", failure_ok, 15))

    quality_ok = _section_has_substantive_content(structural_body, _QUALITY_RE)
    if not quality_ok:
        issues.append(
            CreatorQualityIssue(
                code="creator_quality_checks_missing",
                message="SKILL.md must contain substantive validation or quality checks.",
                path="SKILL.md",
            )
        )
    checks.append(CreatorQualityCheck("quality_checks", quality_ok, 10))

    scaffold_marker = "MODEL_MIRROR_MANUAL_SCAFFOLD" in package.skill_markdown
    placeholder_paths = [
        path for path, content in package.files.items() if _PLACEHOLDER_RE.search(content)
    ]
    placeholder_ok = (
        not scaffold_marker
        and not _PLACEHOLDER_RE.search(package.skill_markdown)
        and not placeholder_paths
    )
    if not placeholder_ok:
        issues.append(
            CreatorQualityIssue(
                code=(
                    "creator_manual_scaffold_incomplete"
                    if scaffold_marker
                    else "creator_placeholder_remaining"
                ),
                message="Creator packages must replace the manual scaffold and all placeholders.",
                path=(placeholder_paths[0] if placeholder_paths else "SKILL.md"),
            )
        )
    checks.append(CreatorQualityCheck("no_placeholders", placeholder_ok, 5))

    return _report(issues, checks, (), None)


def _validate_wrapped_skill(
    raw_skill: Any, issues: list[CreatorQualityIssue]
) -> SkillPackageV2 | None:
    if not isinstance(raw_skill, Mapping):
        issues.append(
            CreatorQualityIssue(
                code="creator_skill_missing",
                message="Creator output must include a typed skill package.",
                field="skill",
            )
        )
        return None
    root_name = raw_skill.get("root_name") or raw_skill.get("name") or raw_skill.get("slug")
    result = validate_skill_package(
        root_name=root_name,
        skill_markdown=raw_skill.get("skill_markdown"),
        files=raw_skill.get("files", {}),
    )
    if result.package is None:
        issues.append(
            CreatorQualityIssue(
                code="creator_package_invalid",
                message="Creator package failed the structural and security validator.",
                field="skill",
            )
        )
    return result.package


def _validate_generation_budget(
    payload: Mapping[str, Any],
    issues: list[CreatorQualityIssue],
    *,
    resource_build: bool,
) -> None:
    skill_markdown_limit = (
        MAX_RESOURCE_BUILD_SKILL_MARKDOWN_CHARS
        if resource_build
        else MAX_CREATOR_SKILL_MARKDOWN_CHARS
    )
    resource_count_limit = (
        MAX_RESOURCE_BUILD_RESOURCES if resource_build else MAX_CREATOR_RESOURCES
    )
    resource_size_limit = (
        MAX_RESOURCE_BUILD_RESOURCE_CHARS
        if resource_build
        else MAX_CREATOR_RESOURCE_CHARS
    )
    payload_limit = (
        MAX_RESOURCE_BUILD_PAYLOAD_BYTES
        if resource_build
        else MAX_CREATOR_PAYLOAD_BYTES
    )
    raw_skill = payload.get("skill")
    if isinstance(raw_skill, Mapping):
        markdown = raw_skill.get("skill_markdown")
        if isinstance(markdown, str) and len(markdown) > skill_markdown_limit:
            issues.append(
                CreatorQualityIssue(
                    code="creator_skill_markdown_budget_exceeded",
                    message=f"Creator SKILL.md is limited to {skill_markdown_limit} characters per generation.",
                    path="SKILL.md",
                )
            )
        files = raw_skill.get("files")
        if isinstance(files, Mapping):
            resource_paths = [
                path
                for path in files
                if isinstance(path, str) and not path.startswith("agents/")
            ]
            if len(resource_paths) > resource_count_limit:
                issues.append(
                    CreatorQualityIssue(
                        code="creator_resource_count_budget_exceeded",
                        message=f"Creator generation is limited to {resource_count_limit} bundled resources.",
                        field="skill.files",
                    )
                )
            for path in resource_paths:
                content = files.get(path)
                if isinstance(content, str) and len(content) > resource_size_limit:
                    issues.append(
                        CreatorQualityIssue(
                            code="creator_resource_budget_exceeded",
                            message=f"Each generated resource is limited to {resource_size_limit} characters.",
                            path=path,
                        )
                    )
    try:
        payload_bytes = len(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
    except (TypeError, ValueError, OverflowError):
        return
    if payload_bytes > payload_limit:
        issues.append(
            CreatorQualityIssue(
                code="creator_payload_budget_exceeded",
                message=f"Creator generation is limited to {payload_limit} UTF-8 bytes.",
                field="payload",
            )
        )


def _expected_requirement_ids(
    requirements: Sequence[CreatorRequirement],
    requirement_ids: Sequence[str],
    issues: list[CreatorQualityIssue],
) -> tuple[str, ...]:
    combined = [item.requirement_id for item in requirements] + list(requirement_ids)
    expected: list[str] = []
    seen: set[str] = set()
    for raw in combined:
        value = raw.strip() if isinstance(raw, str) else ""
        if not value or value in seen:
            if not value:
                issues.append(
                    CreatorQualityIssue(
                        code="creator_requirement_id_invalid",
                        message="Creator requirement IDs must be non-empty text.",
                        field="requirement_ids",
                    )
                )
            continue
        seen.add(value)
        expected.append(value)
    return tuple(expected)


def _design_items(
    design: Mapping[str, Any] | None,
    field: str,
    issues: list[CreatorQualityIssue],
) -> list[Any]:
    if design is None:
        return []
    value = design.get(field)
    if not isinstance(value, list) or any(not _meaningful_design_item(item) for item in value):
        if value is not None:
            issues.append(
                CreatorQualityIssue(
                    code="creator_design_item_invalid",
                    message=f"design.{field} must contain non-empty typed items.",
                    field=f"design.{field}",
                )
            )
        return []
    return value


def _workflow_design_items(
    design: Mapping[str, Any] | None,
    issues: list[CreatorQualityIssue],
) -> list[Mapping[str, Any]]:
    if design is None:
        return []
    value = design.get("workflow_steps")
    if not isinstance(value, list):
        return []
    items: list[Mapping[str, Any]] = []
    ids: list[str] = []
    descriptions: list[str] = []
    for item in value:
        if not isinstance(item, Mapping):
            items = []
            break
        item_id = item.get("id")
        description = item.get("description") or item.get("instruction")
        if not _clean_text(item_id) or not _clean_text(description):
            items = []
            break
        clean_description = str(description).strip()
        if not _instruction_has_substance(clean_description):
            items = []
            break
        items.append(item)
        ids.append(str(item_id).strip().casefold())
        descriptions.append(_normalized_instruction(clean_description))

    distinct = bool(items) and len(set(ids)) == len(ids)
    if distinct:
        for index, first in enumerate(descriptions):
            for second in descriptions[index + 1 :]:
                if first == second or SequenceMatcher(None, first, second).ratio() >= 0.9:
                    distinct = False
                    break
            if not distinct:
                break
    if not distinct:
        issues.append(
            CreatorQualityIssue(
                code="creator_workflow_steps_invalid",
                message="Workflow steps need unique IDs and materially different executable instructions.",
                field="design.workflow_steps",
            )
        )
        return []
    return items


def _meaningful_design_item(item: Any) -> bool:
    if isinstance(item, str):
        return len(item.strip()) >= 8
    if isinstance(item, Mapping):
        return any(
            isinstance(value, str) and len(value.strip()) >= 8
            for key, value in item.items()
            if key not in {"id", "path", "used_by_steps"}
        )
    return False


def _normalized_instruction(value: str) -> str:
    return " ".join(re.findall(r"\w+", value.casefold(), re.UNICODE))


def _instruction_has_substance(value: str) -> bool:
    clean = value.strip()
    if len(clean) >= 8:
        return True
    return len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]", clean)) >= 6


def _executable_workflow_step_count(markdown: str) -> int:
    count = 0
    matches = list(_HEADING_LINE_RE.finditer(markdown))
    for index, match in enumerate(matches):
        if not _workflow_heading(match.group("title")):
            continue
        level = len(match.group("marks"))
        section_end = len(markdown)
        for following in matches[index + 1 :]:
            if len(following.group("marks")) <= level:
                section_end = following.start()
                break
        section = markdown[match.end() : section_end]
        count += sum(
            1
            for item in re.finditer(r"(?m)^\s*\d+[.)]\s+(.+?)\s*$", section)
            if _instruction_has_substance(item.group(1))
        )
    return count


def _executable_workflow_steps(markdown: str) -> list[str]:
    instructions: list[str] = []
    for title, section in _matching_sections(markdown):
        if not _workflow_heading(title):
            continue
        instructions.extend(
            item.group(1).strip()
            for item in re.finditer(r"(?m)^\s*\d+[.)]\s+(.+?)\s*$", section)
            if _instruction_has_substance(item.group(1))
            and not _PLACEHOLDER_RE.search(item.group(1))
        )
    return instructions


def _instructions_are_distinct(instructions: Sequence[str]) -> bool:
    normalized = [_normalized_instruction(item) for item in instructions]
    if any(not item for item in normalized) or len(set(normalized)) != len(normalized):
        return False
    for index, first in enumerate(normalized):
        for second in normalized[index + 1 :]:
            if SequenceMatcher(None, first, second).ratio() >= 0.9:
                return False
    return True


def _matching_sections(markdown: str) -> list[tuple[str, str]]:
    matches = list(_HEADING_LINE_RE.finditer(markdown))
    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        level = len(match.group("marks"))
        section_end = len(markdown)
        for following in matches[index + 1 :]:
            if len(following.group("marks")) <= level:
                section_end = following.start()
                break
        sections.append((match.group("title"), markdown[match.end() : section_end]))
    return sections


def _section_has_substantive_content(markdown: str, heading_re: re.Pattern[str]) -> bool:
    for title, section in _matching_sections(markdown):
        synthetic_heading = f"\n## {title}"
        if not heading_re.search(synthetic_heading):
            continue
        if _PLACEHOLDER_RE.search(section):
            continue
        plain = re.sub(r"(?m)^\s*(?:[-*+] |\d+[.)]\s+)", "", section)
        plain = re.sub(r"[`*_>#|]", "", plain)
        plain = re.sub(r"\s+", " ", plain).strip()
        # A single character carries materially more information in compact CJK
        # prose than in space-delimited English. Keep the existing 40-character
        # floor for other languages, but accept an equivalent CJK content floor
        # instead of rejecting otherwise actionable Chinese sections.
        if len(plain) >= 40 or len(_CJK_CHAR_RE.findall(plain)) >= 20:
            return True
    return False


def _workflow_heading(title: str) -> bool:
    return bool(
        re.search(r"(?i)(?:\bworkflow\b|\bprocedure\b|\bprocess\b|\bsteps?\b|工作流|流程|步骤)", title)
    )


def _validate_resources(
    package: SkillPackageV2,
    design: Mapping[str, Any] | None,
    workflow_steps: Sequence[Any],
    issues: list[CreatorQualityIssue],
) -> bool:
    resources = design.get("resources") if design is not None else None
    if resources is None:
        resources = []
    if not isinstance(resources, list):
        issues.append(
            CreatorQualityIssue(
                code="creator_resource_plan_mismatch",
                message="design.resources must be a list.",
                field="design.resources",
            )
        )
        return False

    step_ids = {
        str(item.get("id")).strip()
        for item in workflow_steps
        if isinstance(item, Mapping) and _clean_text(item.get("id"))
    }
    planned_paths: set[str] = set()
    valid = True
    invalid_item = False
    for item in resources:
        if not isinstance(item, Mapping):
            valid = False
            invalid_item = True
            continue
        path = item.get("path")
        purpose = item.get("purpose")
        used_by = item.get("used_by_steps")
        if (
            not _clean_text(path)
            or path not in package.files
            or not _clean_text(purpose)
            or not isinstance(used_by, list)
            or not used_by
            or any(step not in step_ids for step in used_by)
        ):
            valid = False
            invalid_item = True
            continue
        planned_paths.add(path)
        if path not in package.skill_markdown:
            issues.append(
                CreatorQualityIssue(
                    code="creator_resource_unreferenced",
                    message="Every planned resource must be directly referenced by SKILL.md.",
                    path=path,
                )
            )
            valid = False

    if invalid_item:
        issues.append(
            CreatorQualityIssue(
                code="creator_resource_plan_mismatch",
                message="Each resource needs a real path, purpose, and existing workflow step IDs.",
                field="design.resources",
            )
        )

    package_resource_paths = {
        path
        for path in package.files
        if not path.startswith("agents/") and path != HOOK_MANIFEST_PATH
    }
    if planned_paths != package_resource_paths:
        issues.append(
            CreatorQualityIssue(
                code="creator_resource_plan_mismatch",
                message="design.resources must exactly cover packaged scripts, references, and assets.",
                field="design.resources",
            )
        )
        valid = False
    return valid


def _validate_coverage(
    package: SkillPackageV2,
    design: Mapping[str, Any] | None,
    expected_ids: Sequence[str],
    issues: list[CreatorQualityIssue],
) -> bool:
    raw_coverage = design.get("requirement_coverage") if design is not None else None
    if not isinstance(raw_coverage, list):
        issues.append(
            CreatorQualityIssue(
                code="creator_requirement_coverage_missing",
                message="design.requirement_coverage must locate every session requirement in package Markdown.",
                field="design.requirement_coverage",
            )
        )
        return False

    markdown_files = {
        "SKILL.md": package.skill_markdown,
        **{
            path: text
            for path, text in package.files.items()
            if PurePosixPath(path).suffix.lower() in {".md", ".markdown"}
        },
    }
    headings = {
        path: set(_HEADING_RE.findall(_structural_markdown(text)))
        for path, text in markdown_files.items()
    }
    expected = set(expected_ids)
    covered: set[str] = set()
    valid = True
    for item in raw_coverage:
        if not isinstance(item, Mapping):
            valid = False
            continue
        requirement_id = item.get("requirement_id")
        locations = item.get("locations")
        if not isinstance(requirement_id, str) or requirement_id not in expected:
            issues.append(
                CreatorQualityIssue(
                    code="creator_requirement_coverage_unknown",
                    message="Coverage contains an unknown session requirement ID.",
                    field="design.requirement_coverage",
                    requirement_id=requirement_id if isinstance(requirement_id, str) else None,
                )
            )
            valid = False
            continue
        if not isinstance(locations, list) or not locations:
            valid = False
            continue
        location_valid = True
        for location in locations:
            if not isinstance(location, Mapping):
                location_valid = False
                continue
            path = location.get("path")
            section = location.get("section")
            if (
                not isinstance(path, str)
                or path not in markdown_files
                or not isinstance(section, str)
                or section not in headings[path]
            ):
                location_valid = False
        if not location_valid:
            issues.append(
                CreatorQualityIssue(
                    code="creator_requirement_location_invalid",
                    message="Coverage locations must reference an existing Markdown file and exact heading.",
                    field="design.requirement_coverage",
                    requirement_id=requirement_id,
                )
            )
            valid = False
            continue
        covered.add(requirement_id)

    for requirement_id in expected:
        if requirement_id not in covered:
            issues.append(
                CreatorQualityIssue(
                    code="creator_requirement_uncovered",
                    message="A captured Creator requirement is not located in the draft package.",
                    field="design.requirement_coverage",
                    requirement_id=requirement_id,
                )
            )
            valid = False
    return valid


def _markdown_body(markdown: str) -> str:
    if not markdown.startswith("---"):
        return markdown
    match = re.search(r"\A---\s*\n.*?\n---\s*(?:\n|\Z)", markdown, re.DOTALL)
    return markdown[match.end() :] if match else markdown


def _structural_markdown(markdown: str) -> str:
    """Remove non-instruction regions before structural completeness checks."""

    without_comments = _HTML_COMMENT_RE.sub("", markdown)
    visible: list[str] = []
    fence_character: str | None = None
    fence_length = 0
    for line in without_comments.splitlines(keepends=True):
        stripped = line.lstrip()
        if fence_character is None:
            match = _FENCE_OPEN_RE.match(line)
            if match is None:
                visible.append(line)
                continue
            fence = match.group("fence")
            fence_character = fence[0]
            fence_length = len(fence)
        elif re.match(
            rf"^{re.escape(fence_character)}{{{fence_length},}}[ \t]*(?:\r?\n)?$",
            stripped,
        ):
            fence_character = None
            fence_length = 0
        if line.endswith(("\n", "\r")):
            visible.append("\n")
    return "".join(visible)


def _clean_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _report(
    issues: Iterable[CreatorQualityIssue],
    checks: Sequence[CreatorQualityCheck],
    requirement_ids: Sequence[str],
    contract_version: str | None,
) -> CreatorDraftQualityReport:
    issue_tuple = tuple(issues)
    score = sum(check.weight for check in checks if check.passed)
    ready = bool(checks) and score == 100 and not any(
        issue.severity == "error" for issue in issue_tuple
    )
    return CreatorDraftQualityReport(
        ready=ready,
        score=score,
        issues=issue_tuple,
        checks=tuple(checks),
        requirement_ids=tuple(requirement_ids),
        contract_version=contract_version,
    )


__all__ = [
    "CREATOR_CONTRACT_VERSION",
    "CREATOR_PLAYBOOK_VERSION",
    "CREATOR_QUALITY_VERSION",
    "MAX_CREATOR_PAYLOAD_BYTES",
    "MAX_CREATOR_RESOURCE_CHARS",
    "MAX_CREATOR_RESOURCES",
    "MAX_CREATOR_SKILL_MARKDOWN_CHARS",
    "CreatorDraftQualityReport",
    "CreatorQualityCheck",
    "CreatorQualityIssue",
    "CreatorRequirement",
    "build_session_requirement_ids",
    "build_session_requirements",
    "evaluate_creator_final_package",
    "evaluate_creator_payload",
    "load_creator_authoring_playbook",
]
