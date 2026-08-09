from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.sandbox_sidecar.engine import SandboxEngine
from server.skills.creator_resource_build import (
    ResourceScriptTest,
    ResourceScriptTestReceipt,
    ResourceScriptTestResult,
    SkillResourceBuildStore,
)
from server.skills.creator_resource_build_runtime import (
    RESOURCE_BUILDER_WORKFLOW_VERSION,
    ResourceBuildGenerationRequest,
    ResourceBuildSegment,
    SandboxCreatorScriptRunner,
    build_resource_builder_invocation,
    parse_resource_build_segment,
    validate_resource_content,
)
from server.skills.creator_resource_plan import SkillResourcePlanStore
from server.skills.creator_resource_build_service import SkillCreatorResourceBuildService
from server.skills.creator_store import SkillCreatorSession, SkillCreatorValidationError
from server.skills.draft_store import WorkspaceSkillDraftStore
from server.xpert_runtime.authoring_service import AuthoringService
from server.xpert_runtime.authoring_store import AuthoringProposalStore
from server.xpert_runtime.sandbox_client import LocalSandboxClient
from server.xperts.store import XpertStore


DESCRIPTION = (
    "Create evidence-bound incident reviews with timelines and corrective actions. Use when "
    "users provide incident facts; do not use for generic rewriting or fictional narratives."
)


def _plan(store: SkillResourcePlanStore, *, resources: list[dict] | None = None):
    raw = store.save_generated(
        session_id="skillcreator_resource_build",
        session_revision=1,
        draft_id=None,
        draft_revision=None,
        draft_digest=None,
        allowed_source_ids={"intent", "positive_example:0", "near_miss:0", "expected_output", "success_criterion:0"},
        payload={
            "skill_name": "incident-review",
            "skill_description": DESCRIPTION,
            "workflow_steps": [
                {"id": "collect", "instruction": "Collect explicit incident facts and missing fields."},
                {"id": "normalize", "instruction": "Normalize the timeline deterministically."},
                {"id": "analyze", "instruction": "Separate known causes from unknown claims."},
                {"id": "deliver", "instruction": "Render and verify the final review."},
            ],
            "output_contract": ["Return a Chinese Markdown incident review with six stable sections."],
            "failure_modes": ["Mark unavailable facts as pending confirmation and never invent them."],
            "resources": [{"generation_cost": "medium", **item} for item in (resources or [])],
            "clarifications": [],
        },
    )
    return store.confirm(
        raw.plan_id,
        expected_revision=raw.revision,
        expected_digest=raw.digest,
        session_revision=1,
        draft_revision=None,
        draft_digest=None,
    )


def _skill_markdown() -> str:
    return f"""---
name: incident-review
description: {DESCRIPTION}
---

# Incident review

## Purpose and scope

Use this Skill only for evidence-bound incident reviews. It preserves supplied facts, rejects
fictional additions, distinguishes missing evidence, and does not replace a general rewriting
workflow. The report remains useful when the root cause is unknown.

## Inputs and preconditions

Require an incident description with observable events. Accept partial records, but label every
missing time, owner, impact, cause, or corrective action as pending confirmation. Never treat the
reference policy as evidence about the current incident.

## Workflow

1. Collect explicit facts and classify unsupported claims with `references/evidence-policy.md`.
2. Normalize timeline rows using `scripts/normalize.py`; stop on malformed input.
3. Separate known, unknown, and contradicted claims before drawing conclusions.
4. Copy and render `assets/review-template.md`, then verify every required section.

## Output contract

Return Chinese Markdown with event summary, timeline, impact, root cause, corrective actions, and
pending confirmations. Preserve every supplied time and owner. Mark unknown causes explicitly.

## Failure and degradation

If the script rejects an input, report the validation failure and retain the raw fact for manual
review. If a required field is absent, write pending confirmation. Never guess a root cause or
silently omit conflicting evidence.

## Quality checks

Confirm chronological ordering, exact preservation of supplied facts, complete section coverage,
and a clear distinction between completed and proposed corrective actions. Re-run the script after
any timeline edit and inspect its non-zero failure result before delivery.

## Resources

- Read `references/evidence-policy.md` only when classifying a claim.
- Run `scripts/normalize.py ../inputs/timeline.txt` for deterministic normalization.
- Copy and render `assets/review-template.md` in the delivery step.
- For a focused lookup, run `rg -n \"unknown|unsupported\" references/evidence-policy.md`.
"""


def test_segment_parser_and_invocation_freeze_target(tmp_path: Path) -> None:
    plan = _plan(SkillResourcePlanStore(tmp_path), resources=[])
    # Directly move the real build to its server-selected SKILL.md target.
    store = SkillResourceBuildStore(tmp_path / "build-store")
    build = store.create(plan=plan)
    build = store.claim_next(build.build_id, expected_revision=build.revision, expected_digest=build.digest)
    invocation = build_resource_builder_invocation(
        ResourceBuildGenerationRequest(build=build, target_id="SKILL.md", segment_index=0),
        model_id="provider/model",
    )
    assert invocation.runtime_metadata["resource_builder_workflow_version"] == RESOURCE_BUILDER_WORKFLOW_VERSION
    assert invocation.runtime_metadata["resource_target_id"] == "SKILL.md"
    parsed = parse_resource_build_segment(
        json.dumps({
            "resource_build_version": "skill-resource-build-v1",
            "target_id": "SKILL.md",
            "segment_index": 0,
            "content": "segment",
            "complete": True,
            "script_tests": [],
        }),
        expected_target_id="SKILL.md",
        expected_segment_index=0,
    )
    assert parsed.complete is True
    with pytest.raises(SkillCreatorValidationError):
        parse_resource_build_segment(
            json.dumps({"resource_build_version": "skill-resource-build-v1", "target_id": "other", "segment_index": 0, "content": "x", "complete": True}),
            expected_target_id="SKILL.md",
            expected_segment_index=0,
        )


def test_resource_invocation_only_loads_direct_dependency_content(
    tmp_path: Path,
) -> None:
    plan = _plan(
        SkillResourcePlanStore(tmp_path),
        resources=[
            {
                "kind": "reference",
                "action": "create",
                "path": "references/policy.md",
                "purpose": "Define evidence boundaries.",
                "source_ids": ["intent"],
                "used_by_steps": ["collect"],
                "depends_on": [],
                "acceptance_checks": ["Defines supported claims."],
            },
            {
                "kind": "script",
                "action": "create",
                "path": "scripts/normalize.py",
                "purpose": "Normalize timeline facts.",
                "source_ids": ["positive_example:0"],
                "used_by_steps": ["normalize"],
                "depends_on": ["references/policy.md"],
                "acceptance_checks": ["Rejects malformed input."],
            },
            {
                "kind": "asset",
                "action": "create",
                "path": "assets/report.md",
                "purpose": "Render the report.",
                "source_ids": ["expected_output"],
                "used_by_steps": ["deliver"],
                "depends_on": [],
                "acceptance_checks": ["Contains required placeholders."],
            },
        ],
    )
    build = SkillResourceBuildStore(tmp_path / "build").create(plan=plan)
    reference, script, asset = build.resources
    reference_content = "# Policy\n\nUse explicit facts.\n"
    asset_content = "# Template\n\n{{summary}}\n"
    build = replace(
        build,
        resources=[
            replace(
                reference,
                state="accepted",
                content=reference_content,
                content_digest=__import__("hashlib").sha256(
                    reference_content.encode()
                ).hexdigest(),
            ),
            script,
            replace(
                asset,
                state="accepted",
                content=asset_content,
                content_digest=__import__("hashlib").sha256(
                    asset_content.encode()
                ).hexdigest(),
            ),
        ],
    )
    invocation = build_resource_builder_invocation(
        ResourceBuildGenerationRequest(
            build=build,
            target_id=script.resource_id,
            segment_index=0,
        ),
        model_id="provider/model",
    )
    context = json.loads(invocation.inputs["creator_request"])
    assert context["accepted_resource_content"] == {
        "references/policy.md": reference_content
    }


def test_script_runner_produces_digest_bound_receipt(tmp_path: Path) -> None:
    engine = SandboxEngine(tmp_path / "sidecar", require_landlock=False)
    runner = SandboxCreatorScriptRunner(LocalSandboxClient(engine))
    content = """import sys
if len(sys.argv) != 2:
    raise SystemExit(2)
print(sys.argv[1].upper())
"""
    item = SimpleNamespace(
        kind="script",
        path="scripts/upper.py",
        content=content,
        content_digest=__import__("hashlib").sha256(content.encode()).hexdigest(),
        script_tests=[ResourceScriptTest(test_id="happy", args=["hello"], fixtures=[], expected_exit_code=0, stdout_contains=["HELLO"], stderr_contains=[])],
    )
    receipt = asyncio.run(runner.run(item))
    assert receipt.passed is True
    assert receipt.script_digest == item.content_digest
    assert receipt.profile == "skill_authoring_v1"


def test_script_runner_executes_javascript_cli_offline(tmp_path: Path) -> None:
    engine = SandboxEngine(tmp_path / "sidecar-js", require_landlock=False)
    runner = SandboxCreatorScriptRunner(LocalSandboxClient(engine))
    content = """const value = process.argv[2];
if (!value) {
  console.error('usage: upper.js VALUE');
  process.exit(2);
}
console.log(value.toUpperCase());
"""
    item = SimpleNamespace(
        kind="script",
        path="scripts/upper.js",
        content=content,
        content_digest=__import__("hashlib").sha256(content.encode()).hexdigest(),
        script_tests=[
            ResourceScriptTest(
                test_id="happy_js",
                args=["hello"],
                fixtures=[],
                expected_exit_code=0,
                stdout_contains=["HELLO"],
                stderr_contains=[],
            )
        ],
    )
    receipt = asyncio.run(runner.run(item))
    assert receipt.passed is True
    assert receipt.results[0].exit_code == 0


@pytest.mark.parametrize(
    ("path", "content", "expected_code"),
    [
        (
            "scripts/broken.py",
            "import sys\nprint(sys.argv[1]\nraise SystemExit(2)\n",
            "python_syntax_invalid",
        ),
        (
            "scripts/broken.js",
            "const value = ;\nprocess.argv; process.exit(2);\n",
            "javascript_syntax_invalid",
        ),
    ],
)
def test_resource_validation_rejects_script_syntax_errors(
    path: str, content: str, expected_code: str
) -> None:
    issues = validate_resource_content(
        SimpleNamespace(kind="script", path=path, content=content)
    )
    assert expected_code in {str(issue.get("code")) for issue in issues}


class _Builder:
    def available(self) -> bool:
        return True

    async def generate(self, request: ResourceBuildGenerationRequest) -> ResourceBuildSegment:
        target = request.target_id
        if target == "SKILL.md":
            content = _skill_markdown()
            tests = []
        else:
            item = next(item for item in request.build.resources if item.resource_id == target)
            if item.kind == "reference":
                content = "# Evidence policy\n\nKnown claims have direct support. Unknown claims stay pending. Unsupported claims are rejected.\n"
                tests = []
            elif item.kind == "asset":
                content = "# Incident review template\n\nCopy this template and replace placeholders: {{summary}}, {{timeline}}, {{actions}}.\n"
                tests = []
            else:
                content = """import sys
def main() -> int:
    if len(sys.argv) != 2:
        print('usage: normalize.py VALUE', file=sys.stderr)
        return 2
    print(sys.argv[1].strip())
    return 0
if __name__ == '__main__':
    raise SystemExit(main())
"""
                tests = [{"test_id": "happy", "args": ["09:02"], "fixtures": [], "expected_exit_code": 0, "stdout_contains": ["09:02"], "stderr_contains": []}]
        return ResourceBuildSegment(target_id=target, segment_index=request.segment_index, content=content, complete=True, script_tests=tests)


class _ScriptRunner:
    async def run(self, item):
        return ResourceScriptTestReceipt(
            receipt_id="script_receipt_test",
            script_digest=item.content_digest,
            profile="skill_authoring_v1",
            passed=True,
            results=[ResourceScriptTestResult(test_id="happy", passed=True, exit_code=0, stdout_sha256="a" * 64, stderr_sha256="b" * 64, duration_ms=1)],
        )


def test_complex_build_reaches_valid_standard_proposal(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    plan_store = SkillResourcePlanStore(runtime)
    plan = _plan(plan_store, resources=[
        {"kind": "reference", "action": "create", "path": "references/evidence-policy.md", "purpose": "Keep claim boundaries separate.", "source_ids": ["intent", "near_miss:0"], "used_by_steps": ["collect", "analyze"], "depends_on": [], "acceptance_checks": ["Defines known, unknown, and unsupported claims."]},
        {"kind": "script", "action": "create", "path": "scripts/normalize.py", "purpose": "Normalize timeline values deterministically.", "source_ids": ["positive_example:0"], "used_by_steps": ["normalize"], "depends_on": ["references/evidence-policy.md"], "acceptance_checks": ["Returns non-zero for missing input."]},
        {"kind": "asset", "action": "create", "path": "assets/review-template.md", "purpose": "Provide reusable output boilerplate.", "source_ids": ["expected_output"], "used_by_steps": ["deliver"], "depends_on": [], "acceptance_checks": ["Contains all output placeholders."]},
    ])
    draft_store = WorkspaceSkillDraftStore(runtime)
    authoring = AuthoringService(AuthoringProposalStore(runtime), XpertStore(tmp_path / "xperts"), draft_store, local_console_actor_id="console-test")
    session = SkillCreatorSession(
        session_id=plan.session_id,
        session_revision=1,
        intent="Create an evidence-bound incident review.",
        positive_examples=["Review the 09:02 outage timeline."],
        near_miss_examples=["Rewrite this paragraph."],
        expected_output="Return six stable Markdown sections.",
        success_criteria=["Preserve times and never invent a cause."],
        evidence_confirmed=True,
        state="editing_draft",
    )
    creator = SimpleNamespace(authoring_service=authoring, get_session=lambda _id: (session, None), require_enabled=lambda: None)
    planning = SimpleNamespace(plan_store=plan_store, require_enabled=lambda: None)
    service = SkillCreatorResourceBuildService(
        creator,
        planning,
        SkillResourceBuildStore(runtime),
        builder=_Builder(),
        script_runner=_ScriptRunner(),
        enabled=True,
    )
    build = asyncio.run(service.start(session.session_id, plan_id=plan.plan_id, expected_session_revision=1, expected_plan_revision=plan.revision, expected_plan_digest=plan.digest))
    while build.phase == "resources":
        build = asyncio.run(service.next(build.build_id, expected_session_revision=1, expected_revision=build.revision, expected_digest=build.digest))
        assert build.state == "awaiting_review"
        resource_id = build.current_resource_id
        assert resource_id
        build = service.review_resource(build.build_id, resource_id=resource_id, expected_session_revision=1, expected_revision=build.revision, expected_digest=build.digest, decision="accept")
    build = asyncio.run(service.next(build.build_id, expected_session_revision=1, expected_revision=build.revision, expected_digest=build.digest))
    assert build.state == "awaiting_review", build.skill_validation_issues
    build, proposal = service.finalize(build.build_id, expected_session_revision=1, expected_revision=build.revision, expected_digest=build.digest, decision="accept")
    assert proposal is not None
    assert proposal.validation["valid"] is True
    assert build.proposal_id == proposal.proposal_id
    assert proposal.payload["skill"]["files"].keys() == {
        "references/evidence-policy.md", "scripts/normalize.py", "assets/review-template.md"
    }
