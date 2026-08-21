from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from server.skills.creator_evolution import SkillEvolutionPlanStore
from server.skills.creator_resource_plan import SkillResourcePlanStore
from server.skills.creator_store import (
    SkillCreatorConflictError,
    SkillCreatorStorageError,
    SkillCreatorValidationError,
)


HEX_A = "a" * 64
HEX_B = "b" * 64
HEX_C = "c" * 64
HEX_D = "d" * 64


def _bindings() -> dict[str, object]:
    return {
        "session_id": "skillcreator_test",
        "session_revision": 7,
        "draft_id": "skilldraft_test",
        "draft_state_revision": 5,
        "draft_revision": 3,
        "draft_digest": HEX_A,
        "evaluation_run_id": "skill_eval_run_test",
        "evaluation_run_revision": 11,
        "review_id": "skill_eval_review_test",
        "review_revision": 1,
        "suite_id": "skill_eval_suite_test",
        "suite_revision": 2,
        "suite_digest": HEX_B,
        "resource_plan_id": "skillplan_test",
        "resource_plan_revision": 4,
        "resource_plan_digest": HEX_C,
    }


def _payload(*, questions: bool = False) -> dict[str, object]:
    return {
        "diagnoses": [
            {
                "case_id": "core-normal",
                "evidence_item_ids": ["skill_eval_item_candidate"],
                "failure_types": ["assertion_failure"],
                "requirement_ids": ["intent"],
                "resource_ids": ["skillres_existing"],
                "sections": ["Workflow"],
                "summary": "The deterministic normalization step was not applied.",
            }
        ],
        "actions": [
            {
                "action_id": "update-normalizer",
                "action": "update",
                "resource_id": "skillres_existing",
                "purpose": "Normalize the input deterministically.",
                "source_ids": ["intent"],
                "used_by_steps": ["normalize"],
                "depends_on": [],
                "acceptance_checks": ["The CLI exits non-zero for invalid input."],
                "related_case_ids": ["core-normal"],
                "expected_improvement": "The failed normalization assertion passes.",
                "non_regression_case_ids": ["core-boundary"],
            }
        ],
        "workflow_steps": [
            {"step_id": "inspect", "instruction": "Inspect the supplied records."},
            {"step_id": "normalize", "instruction": "Run the deterministic normalizer."},
            {"step_id": "verify", "instruction": "Verify every normalized field."},
            {"step_id": "render", "instruction": "Render the required report."},
        ],
        "output_contract": ["Return the fixed report fields."],
        "failure_modes": ["Stop with a bounded error when input is invalid."],
        "expected_improvements": ["Normalization becomes deterministic."],
        "acceptance_criteria": ["The normal case assertion passes without regressing the boundary case."],
        "non_goals": ["Do not invent missing domain data."],
        "overfitting_risks": ["Do not hard-code the frozen case values."],
        "clarifications": (
            [
                {
                    "question_id": "missing-rule",
                    "question": "Which normalization table is authoritative?",
                    "reason": "The frozen evidence does not identify one.",
                }
            ]
            if questions
            else []
        ),
    }


def _allowed() -> dict[str, object]:
    return {
        "allowed_case_ids": {"core-normal", "core-boundary"},
        "allowed_item_ids": {"skill_eval_item_candidate"},
        "allowed_requirement_ids": {"intent"},
        "allowed_resources": {
            "skillres_existing": {"kind": "script", "path": "scripts/normalize.py"}
        },
        "allowed_source_ids": {"intent"},
        "allowed_step_ids": {"inspect", "normalize", "verify", "render"},
    }


def test_evolution_plan_is_append_only_and_answers_require_regeneration(tmp_path) -> None:
    store = SkillEvolutionPlanStore(tmp_path)
    waiting = store.save_generated(
        bindings=_bindings(),
        payload=_payload(questions=True),
        expected_plan_revision=None,
        expected_plan_digest=None,
        **_allowed(),
    )
    assert waiting.state == "needs_input"
    answered = store.save_answers(
        waiting.plan_id,
        expected_revision=waiting.revision,
        expected_digest=waiting.digest,
        answers={"missing-rule": "Use the table in the confirmed reference."},
    )
    assert answered.revision == 2
    assert answered.state == "needs_regeneration"

    regenerated = store.save_generated(
        bindings=_bindings(),
        payload=_payload(),
        expected_plan_revision=answered.revision,
        expected_plan_digest=answered.digest,
        **_allowed(),
    )
    confirmed = store.confirm(
        regenerated.plan_id,
        expected_revision=regenerated.revision,
        expected_digest=regenerated.digest,
    )
    assert confirmed.state == "confirmed"
    assert SkillEvolutionPlanStore(tmp_path).require(confirmed.plan_id).digest == confirmed.digest


def test_evolution_plan_rejects_unversioned_free_text_failure_types(tmp_path) -> None:
    store = SkillEvolutionPlanStore(tmp_path)
    payload = _payload()
    payload["diagnoses"][0]["failure_types"] = ["输出格式不稳定"]

    with pytest.raises(SkillCreatorValidationError):
        store.save_generated(
            bindings=_bindings(),
            payload=payload,
            expected_plan_revision=None,
            expected_plan_digest=None,
            **_allowed(),
        )


def test_evolution_plan_rejects_unfrozen_evidence_paths_and_credentials(tmp_path) -> None:
    store = SkillEvolutionPlanStore(tmp_path)
    wrong_path = _payload()
    wrong_path["actions"][0]["path"] = "scripts/other.py"  # type: ignore[index]
    with pytest.raises(SkillCreatorValidationError) as exc_info:
        store.save_generated(
            bindings=_bindings(), payload=wrong_path,
            expected_plan_revision=None, expected_plan_digest=None, **_allowed()
        )
    assert exc_info.value.code == "skill_creator_evolution_resource_invalid"

    secret = _payload()
    secret["expected_improvements"] = ["api_key = 'sk-" + "live-secret-value-1234567890'"]
    with pytest.raises(SkillCreatorValidationError) as exc_info:
        store.save_generated(
            bindings=_bindings(), payload=secret,
            expected_plan_revision=None, expected_plan_digest=None, **_allowed()
        )
    assert exc_info.value.code == "skill_creator_evolution_credentials_blocked"
    assert not store.snapshot_path.exists()


def test_evolution_plan_patch_preserves_server_minted_create_resources(tmp_path) -> None:
    store = SkillEvolutionPlanStore(tmp_path)
    payload = _payload()
    payload["actions"].append(  # type: ignore[union-attr]
        {
            "action_id": "create-template",
            "action": "create",
            "kind": "asset",
            "path": "assets/report-template.md",
            "purpose": "Provide the stable report skeleton.",
            "source_ids": ["intent"],
            "used_by_steps": ["render"],
            "depends_on": ["skillres_existing"],
            "acceptance_checks": ["The template contains every output field."],
            "related_case_ids": ["core-normal"],
            "expected_improvement": "Output fields remain stable.",
            "non_regression_case_ids": ["core-boundary"],
        }
    )
    generated = store.save_generated(
        bindings=_bindings(), payload=payload,
        expected_plan_revision=None, expected_plan_digest=None, **_allowed()
    )
    created = next(item for item in generated.actions if item.action == "create")
    assert created.resource_id.startswith("skillres_")
    patched = store.patch(
        generated.plan_id,
        expected_revision=generated.revision,
        expected_digest=generated.digest,
        changes={"non_goals": ["Do not add network access."]},
        **_allowed(),
    )
    assert next(item for item in patched.actions if item.action == "create").resource_id == created.resource_id


def test_evolution_plan_rejects_dependencies_on_deleted_resources(tmp_path) -> None:
    store = SkillEvolutionPlanStore(tmp_path)
    payload = _payload()
    payload["actions"][0]["action"] = "delete"  # type: ignore[index]
    payload["actions"].append(  # type: ignore[union-attr]
        {
            "action_id": "update-policy",
            "action": "update",
            "resource_id": "skillres_policy",
            "purpose": "Keep the policy aligned with the workflow.",
            "source_ids": ["intent"],
            "used_by_steps": ["verify"],
            "depends_on": ["skillres_existing"],
            "acceptance_checks": ["The policy remains directly readable."],
            "related_case_ids": ["core-normal"],
            "expected_improvement": "The policy matches the corrected workflow.",
            "non_regression_case_ids": ["core-boundary"],
        }
    )
    allowed = _allowed()
    allowed["allowed_resources"] = {
        "skillres_existing": {"kind": "script", "path": "scripts/normalize.py"},
        "skillres_policy": {"kind": "reference", "path": "references/policy.md"},
    }

    with pytest.raises(SkillCreatorValidationError) as exc_info:
        store.save_generated(
            bindings=_bindings(),
            payload=payload,
            expected_plan_revision=None,
            expected_plan_digest=None,
            **allowed,
        )

    assert exc_info.value.code == "skill_creator_evolution_resource_invalid"
    assert not store.snapshot_path.exists()


def test_mark_stale_is_bound_to_the_exact_plan_revision(tmp_path) -> None:
    store = SkillEvolutionPlanStore(tmp_path)
    first = store.save_generated(
        bindings=_bindings(), payload=_payload(),
        expected_plan_revision=None, expected_plan_digest=None, **_allowed()
    )
    second = store.patch(
        first.plan_id,
        expected_revision=first.revision,
        expected_digest=first.digest,
        changes={"non_goals": ["Do not add network access."]},
        **_allowed(),
    )

    with pytest.raises(SkillCreatorConflictError):
        store.mark_stale(
            first.plan_id,
            expected_revision=first.revision,
            expected_digest=first.digest,
        )

    assert store.require(second.plan_id).digest == second.digest
    assert store.require(second.plan_id).state == "ready"


def test_evolution_store_quarantines_one_record_and_top_level_corruption_fails_closed(tmp_path) -> None:
    store = SkillEvolutionPlanStore(tmp_path)
    item = store.save_generated(
        bindings=_bindings(), payload=_payload(),
        expected_plan_revision=None, expected_plan_digest=None, **_allowed()
    )
    snapshot = json.loads(store.snapshot_path.read_text(encoding="utf-8"))
    snapshot["items"].append({"plan_id": "broken", "secret": "must-not-be-copied"})
    store.snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    recovered = SkillEvolutionPlanStore(tmp_path)
    assert recovered.require(item.plan_id).digest == item.digest
    persisted = store.snapshot_path.read_text(encoding="utf-8")
    assert "must-not-be-copied" not in persisted
    assert recovered.status()["quarantine_count"] == 1

    store.snapshot_path.write_text("{not-json", encoding="utf-8")
    failed = SkillEvolutionPlanStore(tmp_path)
    with pytest.raises(SkillCreatorStorageError):
        failed.current_for_session("skillcreator_test")
    assert store.snapshot_path.read_text(encoding="utf-8") == "{not-json"


def test_evolution_store_quarantines_revision_history_without_revision_one(tmp_path) -> None:
    store = SkillEvolutionPlanStore(tmp_path)
    first = store.save_generated(
        bindings=_bindings(), payload=_payload(),
        expected_plan_revision=None, expected_plan_digest=None, **_allowed()
    )
    second = store.patch(
        first.plan_id,
        expected_revision=first.revision,
        expected_digest=first.digest,
        changes={"non_goals": ["Do not add network access."]},
        **_allowed(),
    )
    snapshot = json.loads(store.snapshot_path.read_text(encoding="utf-8"))
    snapshot["items"] = [item for item in snapshot["items"] if item["revision"] == second.revision]
    store.snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

    recovered = SkillEvolutionPlanStore(tmp_path)
    assert recovered.current_for_session("skillcreator_test") is None
    assert recovered.status()["plan_count"] == 0
    assert recovered.status()["quarantine_count"] == 1


def test_evolution_store_revalidates_self_consistent_record_shape(tmp_path) -> None:
    store = SkillEvolutionPlanStore(tmp_path)
    item = store.save_generated(
        bindings=_bindings(), payload=_payload(),
        expected_plan_revision=None, expected_plan_digest=None, **_allowed()
    )
    values = asdict(item)
    values.pop("digest")
    values["workflow_steps"][0].pop("instruction")
    tampered = store._build(**values)
    snapshot = json.loads(store.snapshot_path.read_text(encoding="utf-8"))
    snapshot["items"] = [asdict(tampered)]
    store.snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

    recovered = SkillEvolutionPlanStore(tmp_path)
    assert recovered.current_for_session("skillcreator_test") is None
    assert recovered.status()["quarantine_count"] == 1


def _resource_payload() -> dict[str, object]:
    return {
        "skill_name": "incident-review",
        "skill_description": "Create a structured incident review when requested.",
        "workflow_steps": [
            {"step_id": "inspect", "instruction": "Inspect the input."},
            {"step_id": "normalize", "instruction": "Normalize the facts."},
            {"step_id": "verify", "instruction": "Verify the facts."},
            {"step_id": "render", "instruction": "Render the report."},
        ],
        "output_contract": ["Return the fixed report."],
        "failure_modes": ["Mark missing facts as unknown."],
        "resources": [
            {
                "kind": "script",
                "action": "update",
                "generation_cost": "medium",
                "path": "scripts/normalize.py",
                "purpose": "Normalize facts.",
                "source_ids": ["intent"],
                "used_by_steps": ["normalize"],
                "depends_on": [],
                "acceptance_checks": ["Exit non-zero on invalid input."],
            }
        ],
    }


def test_resource_plan_evolution_rebind_is_idempotent(tmp_path) -> None:
    store = SkillResourcePlanStore(tmp_path)
    generated = store.save_generated(
        session_id="skillcreator_test",
        session_revision=1,
        draft_id="skilldraft_test",
        draft_revision=2,
        draft_digest=HEX_D,
        payload=_resource_payload(),
        allowed_source_ids={"intent"},
    )
    source = store.confirm(
        generated.plan_id,
        expected_revision=generated.revision,
        expected_digest=generated.digest,
        session_revision=1,
        draft_revision=2,
        draft_digest=HEX_D,
    )
    ready = store.save_evolution_revision(
        source_plan_id=source.plan_id,
        source_revision=source.revision,
        source_digest=source.digest,
        session_revision=3,
        draft_id="skilldraft_test",
        draft_revision=2,
        draft_digest=HEX_D,
        payload=_resource_payload(),
        allowed_source_ids={"intent"},
    )
    confirmed = store.confirm(
        ready.plan_id,
        expected_revision=ready.revision,
        expected_digest=ready.digest,
        session_revision=3,
        draft_revision=2,
        draft_digest=HEX_D,
    )
    replay = store.save_evolution_revision(
        source_plan_id=source.plan_id,
        source_revision=source.revision,
        source_digest=source.digest,
        session_revision=3,
        draft_id="skilldraft_test",
        draft_revision=2,
        draft_digest=HEX_D,
        payload=_resource_payload(),
        allowed_source_ids={"intent"},
    )
    assert replay.revision == confirmed.revision
    assert replay.digest == confirmed.digest
