from __future__ import annotations

import json
from pathlib import Path

import pytest

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


def _payload(*, resources=None, clarifications=None):
    return {
        "skill_name": "review-incidents",
        "skill_description": (
            "Create evidence-bound incident reviews when users need a timeline, "
            "root-cause boundaries, and corrective actions; do not use for generic rewriting."
        ),
        "workflow_steps": [
            {"id": "collect", "instruction": "Collect only explicit incident facts."},
            {"id": "normalize", "instruction": "Normalize times and missing values."},
            {"id": "analyze", "instruction": "Separate known causes from unknowns."},
            {"id": "deliver", "instruction": "Render and verify the final review."},
        ],
        "output_contract": ["Return a Chinese Markdown incident report."],
        "failure_modes": ["Mark unavailable facts as pending confirmation."],
        "resources": [
            {"generation_cost": "medium", **item}
            for item in (resources or [])
        ],
        "clarifications": clarifications or [],
    }


def _save(store: SkillResourcePlanStore, payload: dict):
    return store.save_generated(
        session_id="skillcreator_test",
        session_revision=4,
        draft_id=None,
        draft_revision=None,
        draft_digest=None,
        payload=payload,
        allowed_source_ids=SOURCE_IDS,
    )


def test_zero_resource_plan_is_ready_and_digest_survives_reload(tmp_path: Path) -> None:
    store = SkillResourcePlanStore(tmp_path)
    plan = _save(store, _payload())

    assert plan.state == "ready"
    assert plan.resources == []
    assert len(plan.digest) == 64
    restored = SkillResourcePlanStore(tmp_path).require(plan.plan_id)
    assert restored == plan


def test_failed_atomic_save_does_not_publish_an_in_memory_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SkillResourcePlanStore(tmp_path)
    plan = _save(store, _payload())

    def fail_save() -> None:
        raise OSError("disk full")

    monkeypatch.setattr(store, "_save_unlocked", fail_save)
    with pytest.raises(OSError, match="disk full"):
        store.patch(
            plan.plan_id,
            expected_revision=plan.revision,
            expected_digest=plan.digest,
            allowed_source_ids=SOURCE_IDS,
            changes={"skill_description": plan.skill_description + " Updated."},
        )
    assert store.require(plan.plan_id) == plan


def test_complex_plan_assigns_stable_ids_and_dependencies(tmp_path: Path) -> None:
    store = SkillResourcePlanStore(tmp_path)
    plan = _save(
        store,
        _payload(
            resources=[
                {
                    "kind": "reference",
                    "action": "create",
                    "path": "references/evidence-policy.md",
                    "purpose": "Keep detailed evidence boundaries out of SKILL.md.",
                    "source_ids": ["intent", "near_miss:0"],
                    "used_by_steps": ["collect", "analyze"],
                    "depends_on": [],
                    "acceptance_checks": ["Contains known, unknown, and unsupported cases."],
                },
                {
                    "kind": "script",
                    "action": "create",
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
                    "path": "assets/incident-review-template.md",
                    "purpose": "Provide the final report skeleton.",
                    "source_ids": ["expected_output"],
                    "used_by_steps": ["deliver"],
                    "depends_on": [],
                    "acceptance_checks": ["Contains every required output section."],
                },
            ]
        ),
    )

    assert len({item.resource_id for item in plan.resources}) == 3
    script = next(item for item in plan.resources if item.kind == "script")
    reference = next(item for item in plan.resources if item.kind == "reference")
    assert script.depends_on == [reference.resource_id]

    patched = store.patch(
        plan.plan_id,
        expected_revision=plan.revision,
        expected_digest=plan.digest,
        allowed_source_ids=SOURCE_IDS,
        changes={"skill_description": plan.skill_description + " Keep claims conservative."},
    )
    assert patched.revision == 2
    assert [item.resource_id for item in patched.resources] == [
        item.resource_id for item in plan.resources
    ]
    assert [item.spec_digest for item in patched.resources] == [
        item.spec_digest for item in plan.resources
    ]
    assert all(len(item.spec_digest) == 64 for item in plan.resources)
    assert patched.digest != plan.digest


def test_clarification_answers_are_immutable_and_require_regeneration(tmp_path: Path) -> None:
    store = SkillResourcePlanStore(tmp_path)
    plan = _save(
        store,
        _payload(
            clarifications=[
                {
                    "id": "policy_source",
                    "question": "Which policy defines incident severity?",
                    "reason": "The supplied examples do not define authoritative levels.",
                }
            ]
        ),
    )
    assert plan.state == "needs_input"

    with pytest.raises(SkillCreatorValidationError):
        store.save_answers(
            plan.plan_id,
            expected_revision=plan.revision,
            expected_digest=plan.digest,
            answers={},
        )

    answered = store.save_answers(
        plan.plan_id,
        expected_revision=plan.revision,
        expected_digest=plan.digest,
        answers={"policy_source": "No severity policy exists; use explicit missing markers."},
    )
    assert answered.state == "needs_regeneration"
    assert plan.clarification_answers == {}
    assert answered.clarification_answers["policy_source"].startswith("No severity")

    regenerated = store.save_generated(
        session_id=answered.session_id,
        session_revision=answered.session_revision,
        draft_id=None,
        draft_revision=None,
        draft_digest=None,
        payload=_payload(),
        allowed_source_ids=SOURCE_IDS,
        expected_plan_revision=answered.revision,
        expected_plan_digest=answered.digest,
    )
    assert regenerated.state == "ready"
    assert regenerated.clarification_answers == answered.clarification_answers


def test_confirm_is_bound_to_session_and_draft_snapshot(tmp_path: Path) -> None:
    store = SkillResourcePlanStore(tmp_path)
    plan = _save(store, _payload())

    with pytest.raises(SkillCreatorConflictError):
        store.confirm(
            plan.plan_id,
            expected_revision=plan.revision,
            expected_digest=plan.digest,
            session_revision=5,
            draft_revision=None,
            draft_digest=None,
        )
    confirmed = store.confirm(
        plan.plan_id,
        expected_revision=plan.revision,
        expected_digest=plan.digest,
        session_revision=4,
        draft_revision=None,
        draft_digest=None,
    )
    assert confirmed.state == "confirmed"
    assert confirmed.revision == plan.revision + 1

    with pytest.raises(SkillCreatorConflictError):
        store.patch(
            plan.plan_id,
            expected_revision=confirmed.revision,
            expected_digest=confirmed.digest,
            allowed_source_ids=SOURCE_IDS,
            changes={"skill_name": "changed-name"},
        )


@pytest.mark.parametrize(
    "resources",
    [
        [
            {
                "kind": "script",
                "path": "references/not-a-script.md",
                "purpose": "Wrong root.",
                "source_ids": ["intent"],
                "used_by_steps": ["collect"],
                "acceptance_checks": ["Must fail."],
            }
        ],
        [
            {
                "kind": "script",
                "path": "scripts/tool.sh",
                "purpose": "Unsupported script language.",
                "source_ids": ["intent"],
                "used_by_steps": ["collect"],
                "acceptance_checks": ["Must fail."],
            }
        ],
        [
            {
                "kind": "reference",
                "path": "references/con.md",
                "purpose": "Windows reserved path.",
                "source_ids": ["intent"],
                "used_by_steps": ["collect"],
                "acceptance_checks": ["Must fail."],
            }
        ],
        [
            {
                "kind": "reference",
                "path": "references/unknown.md",
                "purpose": "Unknown evidence.",
                "source_ids": ["invented_source"],
                "used_by_steps": ["collect"],
                "acceptance_checks": ["Must fail."],
            }
        ],
    ],
)
def test_invalid_resource_mappings_fail_before_persistence(tmp_path: Path, resources) -> None:
    store = SkillResourcePlanStore(tmp_path)
    with pytest.raises(SkillCreatorValidationError):
        _save(store, _payload(resources=resources))
    assert not store.snapshot_path.exists()


def test_resource_dependency_cycle_is_rejected(tmp_path: Path) -> None:
    store = SkillResourcePlanStore(tmp_path)
    resources = [
        {
            "kind": "reference",
            "path": "references/one.md",
            "purpose": "First reference.",
            "source_ids": ["intent"],
            "used_by_steps": ["collect"],
            "depends_on": ["references/two.md"],
            "acceptance_checks": ["Contains the first rule set."],
        },
        {
            "kind": "reference",
            "path": "references/two.md",
            "purpose": "Second reference.",
            "source_ids": ["intent"],
            "used_by_steps": ["analyze"],
            "depends_on": ["references/one.md"],
            "acceptance_checks": ["Contains the second rule set."],
        },
    ]
    with pytest.raises(SkillCreatorValidationError, match="cycle"):
        _save(store, _payload(resources=resources))


def test_store_quarantines_secret_record_and_fails_closed_on_top_level_damage(
    tmp_path: Path,
) -> None:
    store = SkillResourcePlanStore(tmp_path / "safe")
    plan = _save(store, _payload())
    payload = json.loads(store.snapshot_path.read_text(encoding="utf-8"))
    secret = "sk-" + "x" * 48
    poisoned = dict(payload["items"][0])
    poisoned["plan_id"] = "skillplan_poisoned"
    poisoned["session_id"] = "skillcreator_poisoned"
    poisoned["skill_description"] = f"OPENROUTER_API_KEY={secret}"
    payload["items"].append(poisoned)
    store.snapshot_path.write_text(json.dumps(payload), encoding="utf-8")

    restored = SkillResourcePlanStore(tmp_path / "safe")
    assert restored.require(plan.plan_id).digest == plan.digest
    rewritten = restored.snapshot_path.read_text(encoding="utf-8")
    assert secret not in rewritten
    assert "record_sha256" in rewritten

    broken_dir = tmp_path / "broken"
    broken_dir.mkdir()
    snapshot = broken_dir / "skill_creator_resource_plans.json"
    snapshot.write_text("{broken", encoding="utf-8")
    broken = SkillResourcePlanStore(broken_dir)
    with pytest.raises(SkillCreatorStorageError):
        broken.current_for_session("skillcreator_test")
    assert snapshot.read_text(encoding="utf-8") == "{broken"
