from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.skills.creator_quality import (
    CREATOR_CONTRACT_VERSION,
    CREATOR_PLAYBOOK_VERSION,
    build_session_requirements,
    evaluate_creator_final_package,
    evaluate_creator_payload,
    load_creator_authoring_playbook,
)
from server.skills.package_validation import validate_skill_package


def _requirements():
    return build_session_requirements(
        intent="Turn repeatable incident reviews into a reliable structured report.",
        positive_examples=["Review this completed incident timeline."],
        near_miss_examples=["Write a fictional outage story."],
        expected_output="A report with evidence, findings, owners, and next actions.",
        success_criteria=["Do not invent evidence", "Every action has an owner"],
    )


def _rich_payload(*, with_resource: bool = False) -> dict:
    description = (
        "Create evidence-based incident review reports with findings, owners, and "
        "follow-up actions. Use when an agent receives a completed incident timeline; "
        "do not use for fictional stories or unsupported root-cause speculation."
    )
    resource_instruction = ""
    files = {}
    resources = []
    if with_resource:
        resource_instruction = (
            "\nRun `scripts/check_report.py` after drafting to confirm that every action "
            "has an owner and that required sections are present."
        )
        files = {
            "scripts/check_report.py": (
                "def validate(report: str) -> bool:\n"
                "    return bool(report.strip()) and 'Owner:' in report\n"
            )
        }
        resources = [
            {
                "path": "scripts/check_report.py",
                "purpose": "Validate the required report structure before delivery.",
                "used_by_steps": ["verify"],
            }
        ]
    markdown = f"""---
name: incident-review
description: {description}
---

# Incident review

## Purpose and boundaries

Review completed incident evidence and produce a factual remediation report. Require a
timeline, observable impact, and named sources. Reject fictional writing requests and mark
unsupported claims as unknown instead of guessing a root cause.

## Inputs and prerequisites

Collect the incident timeline, affected services, evidence references, known responders,
and the reporting deadline. Ask for missing material before assigning causal findings.

## Workflow

1. Normalize the timeline while preserving timestamps and source labels.
2. Separate observed facts from hypotheses, and mark conflicting evidence explicitly.
3. Derive findings only from cited facts; record remaining uncertainty.
4. Draft actions with one accountable owner, a due date, and a verification condition.
5. Verify the complete report against the quality checks before delivery.{resource_instruction}

## Output contract

Return sections for impact, evidence timeline, findings, unknowns, and next actions. Each
finding cites evidence. Each action includes `Owner`, `Due`, and `Verification`. Use
`unknown` for unavailable values and never silently omit a required field.

## Quality checks

Confirm chronological consistency, trace every finding to evidence, and ensure every action
has exactly one owner and a testable completion condition. Report failed checks instead of
presenting the draft as complete.

## Failure handling

If evidence is missing, stop causal attribution and return a missing-evidence list. If
sources conflict, preserve both accounts and request clarification. If validation cannot run,
state that limitation and perform the same checks manually.

## Resources

Only load or run packaged resources from the workflow step that names them. Do not assume
network access or external files that are not supplied with the task.
"""
    sections = {
        "intent": "Purpose and boundaries",
        "positive_example:0": "Workflow",
        "near_miss:0": "Purpose and boundaries",
        "expected_output": "Output contract",
        "success_criterion:0": "Quality checks",
        "success_criterion:1": "Output contract",
    }
    return {
        "creator_contract_version": CREATOR_CONTRACT_VERSION,
        "skill": {
            "name": "incident-review",
            "description": description,
            "skill_markdown": markdown,
            "files": files,
        },
        "design": {
            "workflow_steps": [
                {"id": "normalize", "instruction": "Normalize the supplied timeline."},
                {"id": "separate", "instruction": "Separate facts from hypotheses."},
                {"id": "draft", "instruction": "Draft findings and owned actions."},
                {"id": "verify", "instruction": "Verify the report before delivery."},
            ],
            "output_contract": [
                {"field": "findings", "description": "Evidence-linked findings."}
            ],
            "failure_modes": [
                {"condition": "Evidence is missing.", "behavior": "Return missing inputs."}
            ],
            "resources": resources,
            "assumptions": ["The incident is complete before review begins."],
            "requirement_coverage": [
                {
                    "requirement_id": requirement_id,
                    "locations": [{"path": "SKILL.md", "section": section}],
                }
                for requirement_id, section in sections.items()
            ],
        },
    }


def test_session_requirement_ids_are_deterministic_and_explainable() -> None:
    requirements = _requirements()
    assert [item.requirement_id for item in requirements] == [
        "intent",
        "positive_example:0",
        "near_miss:0",
        "expected_output",
        "success_criterion:0",
        "success_criterion:1",
    ]
    assert requirements[2].kind == "near_miss"
    assert requirements[2].text == "Write a fictional outage story."


def test_complete_creator_payload_passes_with_traceable_checks() -> None:
    report = evaluate_creator_payload(_rich_payload(), requirements=_requirements())

    assert report.ready is True
    assert report.score == 100
    assert report.issues == ()
    serialized = report.to_dict()
    assert all(check["code"] == check["check_id"] for check in serialized["checks"])
    assert all(check["label"] for check in serialized["checks"])
    assert serialized["playbook_version"] == CREATOR_PLAYBOOK_VERSION


def test_final_package_gate_accepts_complete_package_without_design_metadata() -> None:
    skill = _rich_payload(with_resource=True)["skill"]

    report = evaluate_creator_final_package(
        root_name=skill["name"],
        skill_markdown=skill["skill_markdown"],
        files=skill["files"],
    )

    assert report.ready is True
    assert report.score == 100
    assert report.issues == ()


def test_final_package_gate_accepts_chinese_temporal_trigger_description() -> None:
    skill = _rich_payload(with_resource=True)["skill"]
    description = (
        "在发布文件前检查文件名与扩展名，允许.md、.json、.csv文件，阻止路径穿越、可执行文件和未知扩展，"
        "并用清晰的中文说明原因。"
    )
    markdown = skill["skill_markdown"].replace(skill["description"], description)

    report = evaluate_creator_final_package(
        root_name=skill["name"], skill_markdown=markdown, files=skill["files"]
    )

    assert report.ready is True
    assert "creator_description_trigger_missing" not in {
        issue.code for issue in report.issues
    }


def test_final_package_gate_accepts_explicit_english_usage_scope() -> None:
    skill = _rich_payload(with_resource=True)["skill"]
    description = (
        "Evaluates release readiness by verifying backward compatibility tests and load "
        "coverage while reviewing rollback procedures, specifically for pre-deployment "
        "validation. Not for live production issues or performance tuning."
    )
    markdown = skill["skill_markdown"].replace(skill["description"], description)

    report = evaluate_creator_final_package(
        root_name=skill["name"], skill_markdown=markdown, files=skill["files"]
    )

    assert "creator_description_trigger_missing" not in {
        issue.code for issue in report.issues
    }


def test_final_package_gate_accepts_compact_substantive_chinese_quality_checks() -> None:
    skill = _rich_payload(with_resource=True)["skill"]
    start = skill["skill_markdown"].index("## Quality checks")
    end = skill["skill_markdown"].index("## Failure handling")
    markdown = (
        skill["skill_markdown"][:start]
        + "## 质量检查\n\n- 核对事实来源与时间顺序。\n- 验证每项行动都有负责人和完成条件。\n\n"
        + skill["skill_markdown"][end:]
    )

    report = evaluate_creator_final_package(
        root_name=skill["name"], skill_markdown=markdown, files=skill["files"]
    )

    assert report.ready is True
    assert "creator_quality_checks_missing" not in {
        issue.code for issue in report.issues
    }


def test_final_package_gate_rejects_too_short_chinese_trigger_description() -> None:
    skill = _rich_payload(with_resource=True)["skill"]
    description = "在写入前检查文件。"
    markdown = skill["skill_markdown"].replace(skill["description"], description)

    report = evaluate_creator_final_package(
        root_name=skill["name"], skill_markdown=markdown, files=skill["files"]
    )

    assert report.ready is False
    assert "creator_description_trigger_missing" in {
        issue.code for issue in report.issues
    }


def test_final_package_gate_rejects_manual_scaffold_and_resource_placeholders() -> None:
    skill = _rich_payload(with_resource=True)["skill"]
    markdown = skill["skill_markdown"].replace(
        "# Incident review",
        "<!-- MODEL_MIRROR_MANUAL_SCAFFOLD: incomplete -->\n\n# Incident review",
    )
    files = dict(skill["files"])
    files["references/notes.md"] = "# Notes\n\nTODO: replace this placeholder.\n"

    report = evaluate_creator_final_package(
        root_name=skill["name"], skill_markdown=markdown, files=files
    )

    assert report.ready is False
    assert {issue.code for issue in report.issues} >= {
        "creator_manual_scaffold_incomplete"
    }


@pytest.mark.parametrize(
    ("original_heading", "issue_code"),
    [
        ("## Inputs and prerequisites", "creator_inputs_preconditions_missing"),
        ("## Workflow", "creator_workflow_missing"),
        ("## Output contract", "creator_output_contract_missing"),
        ("## Quality checks", "creator_quality_checks_missing"),
        ("## Failure handling", "creator_failure_behavior_missing"),
    ],
)
def test_final_package_gate_requires_substantive_operational_sections(
    original_heading: str, issue_code: str
) -> None:
    skill = _rich_payload()["skill"]
    markdown = skill["skill_markdown"].replace(original_heading, "## Background")

    report = evaluate_creator_final_package(
        root_name=skill["name"], skill_markdown=markdown, files=skill["files"]
    )

    assert report.ready is False
    assert issue_code in {issue.code for issue in report.issues}


def test_final_package_gate_rejects_repeated_workflow_steps() -> None:
    skill = _rich_payload()["skill"]
    markdown = skill["skill_markdown"]
    for instruction in (
        "Normalize the timeline while preserving timestamps and source labels.",
        "Separate observed facts from hypotheses, and mark conflicting evidence explicitly.",
        "Derive findings only from cited facts; record remaining uncertainty.",
        "Draft actions with one accountable owner, a due date, and a verification condition.",
        "Verify the complete report against the quality checks before delivery.",
    ):
        markdown = markdown.replace(instruction, "Repeat the same vague instruction.")

    report = evaluate_creator_final_package(
        root_name=skill["name"], skill_markdown=markdown, files=skill["files"]
    )

    assert report.ready is False
    assert "creator_workflow_missing" in {issue.code for issue in report.issues}


@pytest.mark.parametrize(
    "heading",
    [
        "Overview",
        "When to use",
        "Capabilities and boundaries",
        "适用场景",
        "使用场景与边界",
        "能力与边界",
        "何时使用",
    ],
)
def test_scope_accepts_explicit_equivalent_headings(heading: str) -> None:
    payload = _rich_payload()
    payload["skill"]["skill_markdown"] = payload["skill"][
        "skill_markdown"
    ].replace("Purpose and boundaries", heading)
    for coverage in payload["design"]["requirement_coverage"]:
        for location in coverage["locations"]:
            if location["section"] == "Purpose and boundaries":
                location["section"] = heading

    report = evaluate_creator_payload(payload, requirements=_requirements())

    assert report.ready is True
    assert "creator_scope_missing" not in {issue.code for issue in report.issues}


def test_scope_still_requires_an_explicit_semantic_heading() -> None:
    payload = _rich_payload()
    payload["skill"]["skill_markdown"] = payload["skill"][
        "skill_markdown"
    ].replace("Purpose and boundaries", "Notes")
    for coverage in payload["design"]["requirement_coverage"]:
        for location in coverage["locations"]:
            if location["section"] == "Purpose and boundaries":
                location["section"] = "Notes"

    report = evaluate_creator_payload(payload, requirements=_requirements())

    assert report.ready is False
    assert "creator_scope_missing" in {issue.code for issue in report.issues}


def test_short_but_complete_creator_payload_is_not_rejected_by_length_alone() -> None:
    payload = _rich_payload()
    description = payload["skill"]["description"]
    markdown = f"""---
name: incident-review
description: {description}
---

# Incident review
## Purpose and boundaries
Review evidence; reject fiction and unsupported causes.
## Inputs and prerequisites
Require a timeline, sources, and responders.
## Workflow
1. Normalize facts.
2. Split hypotheses.
3. Cite findings.
4. Assign owners.
5. Verify report.
## Output contract
Return impact, evidence, findings, unknowns, and owned actions.
## Quality checks
Trace findings, mark unknowns, and assign owners.
## Failure handling
Stop without evidence; preserve conflicts.
## Resources
Use no external resources.
"""
    payload["skill"]["skill_markdown"] = markdown
    body = markdown.split("---", 2)[-1].strip()
    assert 350 <= len(body) < 600

    report = evaluate_creator_payload(payload, requirements=_requirements())

    assert report.ready is True
    assert "creator_body_too_thin" not in {issue.code for issue in report.issues}


def test_prompt_like_payload_fails_creator_gate_without_changing_generic_validation() -> None:
    description = "Summarize supplied notes into a short report. Use when a user asks for a summary."
    markdown = f"""---
name: summarize-notes
description: {description}
---

# Summarize notes

Read the notes and return a concise report.
"""
    skill = {
        "name": "summarize-notes",
        "description": description,
        "skill_markdown": markdown,
        "files": {},
    }
    generic = validate_skill_package(
        root_name="summarize-notes", skill_markdown=markdown, files={}
    )
    report = evaluate_creator_payload(
        {
            "creator_contract_version": CREATOR_CONTRACT_VERSION,
            "skill": skill,
            "design": {},
        },
        requirement_ids=("intent", "expected_output"),
    )

    assert generic.valid is True
    assert report.ready is False
    codes = {issue.code for issue in report.issues}
    assert "creator_body_too_thin" in codes
    assert "creator_workflow_missing" in codes
    assert "creator_output_contract_missing" in codes
    assert "creator_failure_behavior_missing" in codes
    assert "creator_requirement_coverage_missing" in codes


@pytest.mark.parametrize("wrapper", ["fence", "comment"])
def test_structural_sections_inside_non_instruction_regions_do_not_pass(
    wrapper: str,
) -> None:
    payload = _rich_payload()
    markdown = payload["skill"]["skill_markdown"]
    marker = "# Incident review"
    if wrapper == "fence":
        markdown = markdown.replace(marker, marker + "\n\n```markdown", 1) + "\n````\n"
    else:
        markdown = markdown.replace(marker, marker + "\n\n<!--", 1) + "\n-->\n"
    payload["skill"]["skill_markdown"] = markdown

    report = evaluate_creator_payload(payload, requirements=_requirements())

    assert report.ready is False
    codes = {issue.code for issue in report.issues}
    assert "creator_scope_missing" in codes
    assert "creator_workflow_missing" in codes
    assert "creator_requirement_location_invalid" in codes


def test_workflow_steps_require_unique_ids_and_materially_distinct_instructions() -> None:
    payload = _rich_payload()
    payload["design"]["workflow_steps"] = [
        {"id": "same", "description": "Repeat the same vague instruction."}
        for _ in range(4)
    ]

    report = evaluate_creator_payload(payload, requirements=_requirements())

    assert report.ready is False
    assert "creator_workflow_steps_invalid" in {
        issue.code for issue in report.issues
    }


def test_concise_chinese_workflow_instruction_is_not_rejected_by_english_length_gate() -> None:
    payload = _rich_payload()
    payload["design"]["workflow_steps"][3]["instruction"] = "阻止可执行文件"
    payload["skill"]["skill_markdown"] = payload["skill"][
        "skill_markdown"
    ].replace(
        "4. Draft actions with one accountable owner, a due date, and a verification condition.",
        "4. 阻止可执行文件",
    )

    report = evaluate_creator_payload(payload, requirements=_requirements())

    assert report.ready is True


def test_inputs_and_preconditions_are_required_as_a_real_section() -> None:
    payload = _rich_payload()
    payload["skill"]["skill_markdown"] = payload["skill"][
        "skill_markdown"
    ].replace("## Inputs and prerequisites", "## Background")

    report = evaluate_creator_payload(payload, requirements=_requirements())

    assert report.ready is False
    assert "creator_inputs_preconditions_missing" in {
        issue.code for issue in report.issues
    }


@pytest.mark.parametrize(
    "heading",
    ["Verification workflow", "Formatting workflow", "Combined audit workflow"],
)
def test_domain_specific_workflow_headings_are_accepted(heading: str) -> None:
    payload = _rich_payload()
    payload["skill"]["skill_markdown"] = payload["skill"][
        "skill_markdown"
    ].replace("## Workflow", f"## {heading}")
    for coverage in payload["design"]["requirement_coverage"]:
        for location in coverage["locations"]:
            if location["section"] == "Workflow":
                location["section"] = heading

    report = evaluate_creator_payload(payload, requirements=_requirements())

    assert report.ready is True


def test_generic_notes_heading_is_not_a_workflow() -> None:
    payload = _rich_payload()
    payload["skill"]["skill_markdown"] = payload["skill"][
        "skill_markdown"
    ].replace("## Workflow", "## Notes")
    for coverage in payload["design"]["requirement_coverage"]:
        for location in coverage["locations"]:
            if location["section"] == "Workflow":
                location["section"] = "Notes"

    report = evaluate_creator_payload(payload, requirements=_requirements())

    assert report.ready is False
    assert "creator_workflow_missing" in {issue.code for issue in report.issues}


def test_coverage_requires_real_markdown_path_and_exact_heading() -> None:
    payload = _rich_payload()
    payload["design"]["requirement_coverage"][0]["locations"] = [
        {"path": "references/missing.md", "section": "Purpose and boundaries"}
    ]

    report = evaluate_creator_payload(payload, requirements=_requirements())

    assert report.ready is False
    issue = next(
        issue
        for issue in report.issues
        if issue.code == "creator_requirement_location_invalid"
    )
    assert issue.requirement_id == "intent"


def test_resources_must_match_files_be_referenced_and_bind_to_real_steps() -> None:
    payload = _rich_payload(with_resource=True)
    payload["design"]["resources"][0]["used_by_steps"] = ["missing-step"]

    report = evaluate_creator_payload(payload, requirements=_requirements())

    assert report.ready is False
    assert "creator_resource_plan_mismatch" in {
        issue.code for issue in report.issues
    }


def test_server_generated_hook_manifest_is_not_a_planned_resource() -> None:
    payload = _rich_payload(with_resource=True)
    payload["skill"]["files"]["hooks/manifest.json"] = json.dumps(
        {
            "version": "modelmirror-hook-manifest-v2",
            "hooks": [
                {
                    "hook_id": "check_report",
                    "event": "pre_tool_use",
                    "mode": "guard",
                    "tool_names": ["sandbox_write_file"],
                    "script_path": "scripts/check_report.py",
                    "purpose": "Block an invalid report path before writing.",
                    "acceptance_checks": [
                        "Safe report paths pass and unsafe paths are denied."
                    ],
                    "timeout_seconds": 15,
                }
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    )

    report = evaluate_creator_payload(payload, requirements=_requirements())

    assert "creator_resource_plan_mismatch" not in {
        issue.code for issue in report.issues
    }


def test_creator_generation_budget_is_stricter_than_generic_package_limits() -> None:
    payload = _rich_payload(with_resource=True)
    payload["skill"]["files"]["scripts/check_report.py"] = "#" * 6_001

    report = evaluate_creator_payload(payload, requirements=_requirements())

    codes = {issue.code for issue in report.issues}
    assert report.ready is False
    assert "creator_resource_budget_exceeded" in codes


def test_creator_generation_limits_resource_count() -> None:
    payload = _rich_payload()
    payload["skill"]["files"] = {
        f"references/item-{index}.md": f"# Item {index}\n\nUseful detail."
        for index in range(7)
    }

    report = evaluate_creator_payload(payload, requirements=_requirements())

    assert "creator_resource_count_budget_exceeded" in {
        issue.code for issue in report.issues
    }
    resource_build_report = evaluate_creator_payload(
        payload,
        requirements=_requirements(),
        resource_build=True,
    )
    assert "creator_resource_count_budget_exceeded" not in {
        issue.code for issue in resource_build_report.issues
    }


def test_playbook_and_license_are_local_versioned_attributed_resources() -> None:
    playbook = load_creator_authoring_playbook()
    reference_dir = Path(__file__).parents[1] / "skills" / "creator_reference"
    license_text = (
        reference_dir / "LICENSE-ANTHROPIC-SKILL-CREATOR.txt"
    ).read_text(encoding="utf-8")

    assert f"playbook-version: {CREATOR_PLAYBOOK_VERSION}" in playbook
    assert "Modified by the ModelMirror project" in playbook
    assert "Copyright 2026 Anthropic, PBC" in playbook
    assert "Apache License" in license_text
    assert "Version 2.0, January 2004" in license_text
    assert "END OF TERMS AND CONDITIONS" in license_text
