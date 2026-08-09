from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.skills.creator_resource_build import (
    ResourceScriptTestReceipt,
    ResourceScriptTestResult,
    SkillResourceBuildStore,
)
from server.skills.creator_resource_plan import SkillResourcePlanStore
from server.skills.creator_store import (
    SkillCreatorConflictError,
    SkillCreatorStorageError,
    SkillCreatorValidationError,
)


SOURCE_IDS = {
    "intent",
    "positive_example:0",
    "near_miss:0",
    "expected_output",
    "success_criterion:0",
}


def _confirmed_plan(
    tmp_path: Path,
    *,
    resources: list[dict] | None = None,
    draft_id: str | None = None,
    draft_revision: int | None = None,
    draft_digest: str | None = None,
):
    store = SkillResourcePlanStore(tmp_path / "plan")
    plan = store.save_generated(
        session_id="skillcreator_build",
        session_revision=3,
        draft_id=draft_id,
        draft_revision=draft_revision,
        draft_digest=draft_digest,
        allowed_source_ids=SOURCE_IDS,
        payload={
            "skill_name": "review-incidents",
            "skill_description": (
                "Create evidence-bound incident reviews when users need a timeline and "
                "corrective actions; do not use for generic rewriting."
            ),
            "workflow_steps": [
                {"id": "collect", "instruction": "Collect explicit incident facts."},
                {"id": "normalize", "instruction": "Normalize timeline records."},
                {"id": "analyze", "instruction": "Separate known facts from gaps."},
                {"id": "deliver", "instruction": "Render and verify the report."},
            ],
            "output_contract": ["Return a Chinese Markdown incident report."],
            "failure_modes": ["Mark unavailable facts as pending confirmation."],
            "resources": resources or [],
            "clarifications": [],
        },
    )
    return store.confirm(
        plan.plan_id,
        expected_revision=plan.revision,
        expected_digest=plan.digest,
        session_revision=plan.session_revision,
        draft_revision=draft_revision,
        draft_digest=draft_digest,
    )


def _complex_resources() -> list[dict]:
    return [
        {
            "kind": "reference",
            "action": "create",
            "generation_cost": "medium",
            "path": "references/evidence-policy.md",
            "purpose": "Keep detailed evidence rules separate.",
            "source_ids": ["intent", "near_miss:0"],
            "used_by_steps": ["collect", "analyze"],
            "depends_on": [],
            "acceptance_checks": ["Defines known, unknown, and unsupported claims."],
        },
        {
            "kind": "script",
            "action": "create",
            "generation_cost": "high",
            "path": "scripts/normalize_timeline.py",
            "purpose": "Normalize timeline rows deterministically.",
            "source_ids": ["positive_example:0", "success_criterion:0"],
            "used_by_steps": ["normalize"],
            "depends_on": ["references/evidence-policy.md"],
            "acceptance_checks": ["Rejects malformed timestamps with a non-zero exit."],
        },
        {
            "kind": "asset",
            "action": "create",
            "generation_cost": "low",
            "path": "assets/report-template.md",
            "purpose": "Provide the report output skeleton.",
            "source_ids": ["expected_output"],
            "used_by_steps": ["deliver"],
            "depends_on": [],
            "acceptance_checks": ["Contains every required report section."],
        },
    ]


def _append_complete(
    store: SkillResourceBuildStore,
    build,
    *,
    target_id: str,
    content: str,
    script_tests: list[dict] | None = None,
):
    claimed = store.claim_next(
        build.build_id,
        expected_revision=build.revision,
        expected_digest=build.digest,
    )
    return store.append_segment(
        claimed.build_id,
        expected_revision=claimed.revision,
        expected_digest=claimed.digest,
        target_id=target_id,
        segment_index=0,
        content=content,
        complete=True,
        script_tests=script_tests,
    )


def test_zero_resource_build_moves_directly_to_skill_markdown(tmp_path: Path) -> None:
    plan = _confirmed_plan(tmp_path)
    store = SkillResourceBuildStore(tmp_path / "build")
    build = store.create(plan=plan)
    assert build.phase == "skill_markdown"
    assert build.state == "planned"
    assert store.create(plan=plan).build_id == build.build_id

    generated = _append_complete(
        store,
        build,
        target_id="SKILL.md",
        content="---\nname: review-incidents\ndescription: Use when reviewing incidents.\n---\n# Review\n",
    )
    assert generated.state == "awaiting_review"
    assert generated.skill_markdown_digest


def test_dependencies_review_and_script_receipt_are_digest_bound(tmp_path: Path) -> None:
    plan = _confirmed_plan(tmp_path, resources=_complex_resources())
    store = SkillResourceBuildStore(tmp_path / "build")
    build = store.create(plan=plan)

    reference = next(item for item in build.resources if item.kind == "reference")
    generated = _append_complete(
        store,
        build,
        target_id=reference.resource_id,
        content="# Evidence policy\n\nUse only explicit facts.\n",
    )
    validated = store.record_validation(
        generated.build_id,
        expected_revision=generated.revision,
        expected_digest=generated.digest,
        target_id=reference.resource_id,
        issues=[],
    )
    accepted = store.review_resource(
        validated.build_id,
        resource_id=reference.resource_id,
        expected_revision=validated.revision,
        expected_digest=validated.digest,
        decision="accept",
    )

    script = next(item for item in accepted.resources if item.kind == "script")
    script_tests = [
        {
            "test_id": "valid_input",
            "args": ["../inputs/valid/input.json"],
            "fixtures": [
                {"path": "valid/input.json", "content": '{"time":"09:00"}'}
            ],
            "expected_exit_code": 0,
            "stdout_contains": ["09:00"],
            "stderr_contains": [],
        }
    ]
    script_generated = _append_complete(
        store,
        accepted,
        target_id=script.resource_id,
        content="import sys\nprint('09:00')\n",
        script_tests=script_tests,
    )
    script_item = next(
        item for item in script_generated.resources if item.resource_id == script.resource_id
    )
    with pytest.raises(SkillCreatorValidationError, match="receipt"):
        store.review_resource(
            script_generated.build_id,
            resource_id=script.resource_id,
            expected_revision=script_generated.revision,
            expected_digest=script_generated.digest,
            decision="accept",
        )

    receipt = ResourceScriptTestReceipt(
        receipt_id="receipt_test",
        script_digest=script_item.content_digest or "",
        profile="skill_authoring_v1",
        passed=True,
        results=[
            ResourceScriptTestResult(
                test_id="valid_input",
                passed=True,
                exit_code=0,
                stdout_sha256="1" * 64,
                stderr_sha256="2" * 64,
                duration_ms=3.5,
            )
        ],
    )
    script_validated = store.record_validation(
        script_generated.build_id,
        expected_revision=script_generated.revision,
        expected_digest=script_generated.digest,
        target_id=script.resource_id,
        issues=[],
        script_receipt=receipt,
    )
    script_accepted = store.review_resource(
        script_validated.build_id,
        resource_id=script.resource_id,
        expected_revision=script_validated.revision,
        expected_digest=script_validated.digest,
        decision="accept",
    )
    assert next(
        item for item in script_accepted.resources if item.resource_id == script.resource_id
    ).script_receipt == receipt


def test_validation_failure_gets_one_internal_repair_then_fails(tmp_path: Path) -> None:
    plan = _confirmed_plan(tmp_path, resources=[_complex_resources()[0]])
    store = SkillResourceBuildStore(tmp_path / "build")
    build = store.create(plan=plan)
    resource_id = build.resources[0].resource_id

    first = _append_complete(
        store, build, target_id=resource_id, content="# Invalid\n"
    )
    repair = store.record_validation(
        first.build_id,
        expected_revision=first.revision,
        expected_digest=first.digest,
        target_id=resource_id,
        issues=[{"code": "missing_policy", "message": "Policy details are missing."}],
    )
    assert repair.state == "planned"
    assert repair.resources[0].repair_count == 1
    assert repair.resources[0].content is None

    second = _append_complete(
        store, repair, target_id=resource_id, content="# Still invalid\n"
    )
    failed = store.record_validation(
        second.build_id,
        expected_revision=second.revision,
        expected_digest=second.digest,
        target_id=resource_id,
        issues=[{"code": "missing_policy", "message": "Policy details are missing."}],
    )
    assert failed.state == "failed"
    assert failed.resources[0].state == "failed"


def test_script_test_arguments_and_assertions_are_scanned_before_persistence(
    tmp_path: Path,
) -> None:
    script_resource = {**_complex_resources()[1], "depends_on": []}
    plan = _confirmed_plan(tmp_path, resources=[script_resource])
    store = SkillResourceBuildStore(tmp_path / "build")
    build = store.create(plan=plan)
    secret = "sk-" + "x" * 48

    with pytest.raises(SkillCreatorValidationError) as blocked:
        _append_complete(
            store,
            build,
            target_id=build.resources[0].resource_id,
            content="import sys\nprint('usage: normalize')\nraise SystemExit(0)\n",
            script_tests=[
                {
                    "test_id": "blocked_secret",
                    "args": [secret],
                    "fixtures": [],
                    "expected_exit_code": 0,
                    "stdout_contains": [],
                    "stderr_contains": [],
                }
            ],
        )
    assert blocked.value.code == "skill_credentials_blocked"
    assert secret not in store.snapshot_path.read_text(encoding="utf-8")


def test_script_test_fixture_count_is_bounded(tmp_path: Path) -> None:
    script_resource = {**_complex_resources()[1], "depends_on": []}
    plan = _confirmed_plan(tmp_path, resources=[script_resource])
    store = SkillResourceBuildStore(tmp_path / "build")
    build = store.create(plan=plan)

    with pytest.raises(SkillCreatorValidationError, match="fixture count"):
        _append_complete(
            store,
            build,
            target_id=build.resources[0].resource_id,
            content="import sys\nprint('usage: normalize')\nraise SystemExit(0)\n",
            script_tests=[
                {
                    "test_id": "too_many_fixtures",
                    "args": ["../inputs/case-0.txt"],
                    "fixtures": [
                        {"path": f"case-{index}.txt", "content": str(index)}
                        for index in range(9)
                    ],
                    "expected_exit_code": 0,
                    "stdout_contains": [],
                    "stderr_contains": [],
                }
            ],
        )


def test_recovery_restarts_only_unconfirmed_resource(tmp_path: Path) -> None:
    plan = _confirmed_plan(tmp_path, resources=_complex_resources())
    store = SkillResourceBuildStore(tmp_path / "build")
    build = store.create(plan=plan)
    first_id = build.resources[0].resource_id
    generated = _append_complete(
        store, build, target_id=first_id, content="# Policy\n\nRules.\n"
    )
    validated = store.record_validation(
        generated.build_id,
        expected_revision=generated.revision,
        expected_digest=generated.digest,
        target_id=first_id,
        issues=[],
    )
    accepted = store.review_resource(
        validated.build_id,
        resource_id=first_id,
        expected_revision=validated.revision,
        expected_digest=validated.digest,
        decision="accept",
    )
    claimed = store.claim_next(
        accepted.build_id,
        expected_revision=accepted.revision,
        expected_digest=accepted.digest,
    )
    assert claimed.state == "generating"

    restored = SkillResourceBuildStore(tmp_path / "build")
    recovered = restored.recover_interrupted()
    assert recovered == [claimed.build_id]
    current = restored.require(claimed.build_id)
    assert current.state == "planned"
    assert current.resources[0].state == "accepted"
    assert current.resources[0].content == "# Policy\n\nRules.\n"
    assert current.current_resource_id is None


def test_atomic_failure_secret_block_and_top_level_corruption(tmp_path: Path, monkeypatch) -> None:
    plan = _confirmed_plan(tmp_path)
    store = SkillResourceBuildStore(tmp_path / "build")
    build = store.create(plan=plan)

    claimed = store.claim_next(
        build.build_id,
        expected_revision=build.revision,
        expected_digest=build.digest,
    )
    secret = "sk-" + "x" * 48
    with pytest.raises(SkillCreatorValidationError) as blocked:
        store.append_segment(
            claimed.build_id,
            expected_revision=claimed.revision,
            expected_digest=claimed.digest,
            target_id="SKILL.md",
            segment_index=0,
            content=f"OPENROUTER_API_KEY={secret}",
            complete=True,
        )
    assert blocked.value.code == "skill_credentials_blocked"
    assert secret not in store.snapshot_path.read_text(encoding="utf-8")

    before = store.require(build.build_id)
    monkeypatch.setattr(store, "_save_unlocked", lambda: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        store.mark_stale(build.build_id)
    assert store.require(build.build_id) == before

    broken_dir = tmp_path / "broken"
    broken_dir.mkdir()
    snapshot = broken_dir / "skill_creator_resource_builds.json"
    snapshot.write_text("{broken", encoding="utf-8")
    broken = SkillResourceBuildStore(broken_dir)
    with pytest.raises(SkillCreatorStorageError):
        broken.current_for_session("skillcreator_build")
    assert snapshot.read_text(encoding="utf-8") == "{broken"


def test_corrupt_record_is_quarantined_without_preserving_secret(tmp_path: Path) -> None:
    plan = _confirmed_plan(tmp_path)
    store = SkillResourceBuildStore(tmp_path / "build")
    build = store.create(plan=plan)
    payload = json.loads(store.snapshot_path.read_text(encoding="utf-8"))
    poison = dict(payload["items"][0])
    poison["build_id"] = "skillbuild_poison"
    poison["session_id"] = "skillcreator_poison"
    poison["skill_description"] = "OPENROUTER_API_KEY=sk-" + "x" * 48
    payload["items"].append(poison)
    store.snapshot_path.write_text(json.dumps(payload), encoding="utf-8")

    restored = SkillResourceBuildStore(tmp_path / "build")
    assert restored.require(build.build_id).digest == build.digest
    rewritten = restored.snapshot_path.read_text(encoding="utf-8")
    assert "OPENROUTER_API_KEY" not in rewritten
    assert "record_sha256" in rewritten


def test_kept_script_requires_a_reusable_digest_bound_receipt(tmp_path: Path) -> None:
    script_path = "scripts/normalize.py"
    plan = _confirmed_plan(
        tmp_path,
        draft_id="skilldraft_existing",
        draft_revision=4,
        draft_digest="d" * 64,
        resources=[
            {
                "kind": "script",
                "action": "keep",
                "generation_cost": "low",
                "path": script_path,
                "purpose": "Preserve a previously verified deterministic normalizer.",
                "source_ids": ["positive_example:0"],
                "used_by_steps": ["normalize"],
                "depends_on": [],
                "acceptance_checks": ["Existing digest still has a passing offline receipt."],
            }
        ],
    )
    store = SkillResourceBuildStore(tmp_path / "build")
    with pytest.raises(SkillCreatorValidationError) as caught:
        store.create(
            plan=plan,
            existing_files={script_path: "print('stable')\n"},
        )
    assert caught.value.code == "skill_creator_script_receipt_required"
