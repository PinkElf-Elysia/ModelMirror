from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.skills.creator_evaluation import (
    SkillEvaluationConflictError,
    SkillEvaluationStorageError,
    SkillEvaluationValidationError,
)
from server.skills.creator_evaluation_suite import SkillEvaluationSuiteStore


DIGEST = "a" * 64
SESSION_DEFINITION_DIGEST = "d" * 64
REQUIREMENTS = ("intent", "expected_output", "success_criterion:0")


def _case(
    role: str,
    index: int,
    *,
    source: str | None = None,
    requirement_ids: tuple[str, ...] = REQUIREMENTS,
    resources: tuple[str, ...] = (),
) -> dict:
    value = {
        "case_id": f"case-{index}",
        "role": role,
        "name": f"Case {index}",
        "prompt": f"Complete scenario {index} without inventing facts.",
        "expected_behavior": "Return a bounded and verifiable result.",
        "fixtures": [],
        "assertions": [{"kind": "contains", "value": "done"}],
        "requirement_ids": list(requirement_ids),
        "required_resource_paths": list(resources),
        "workflow_step_ids": ["step-1"],
    }
    if source:
        value["source"] = source
    return value


def _core_cases() -> list[dict]:
    return [
        _case("normal", 1),
        _case("ambiguous", 2),
        _case("boundary", 3),
    ]


def _generated(store: SkillEvaluationSuiteStore, session_id: str = "session-one"):
    return store.save_generated(
        session_id=session_id,
        session_revision=3,
        session_definition_digest=SESSION_DEFINITION_DIGEST,
        draft_id=f"draft-{session_id}",
        draft_state_revision=2,
        draft_revision=1,
        draft_digest=DIGEST,
        quality_mode="objective",
        cases=_core_cases(),
        expected_suite_revision=None,
        expected_suite_digest=None,
        allowed_requirement_ids=REQUIREMENTS,
        allowed_resource_paths=("references/rules.md",),
        allowed_workflow_step_ids=("step-1",),
    )


def test_generated_suite_requires_exact_core_roles_and_round_trips(tmp_path: Path) -> None:
    store = SkillEvaluationSuiteStore(tmp_path)
    suite = _generated(store)

    assert suite.suite_revision == 1
    assert suite.state == "draft"
    assert [case.role for case in suite.cases] == ["normal", "ambiguous", "boundary"]
    assert all(case.source == "generated" for case in suite.cases)
    assert len({case.case_fingerprint for case in suite.cases}) == 3

    restored = SkillEvaluationSuiteStore(tmp_path).require(suite.suite_id)
    assert restored == suite

    invalid = _core_cases()
    invalid[-1]["role"] = "normal"
    with pytest.raises(SkillEvaluationValidationError) as captured:
        store.save_generated(
            session_id="session-two",
            session_revision=1,
            session_definition_digest=SESSION_DEFINITION_DIGEST,
            draft_id="draft-two",
            draft_state_revision=1,
            draft_revision=1,
            draft_digest=DIGEST,
            quality_mode="objective",
            cases=invalid,
            expected_suite_revision=None,
            expected_suite_digest=None,
            allowed_requirement_ids=REQUIREMENTS,
            allowed_resource_paths=(),
            allowed_workflow_step_ids=("step-1",),
        )
    assert captured.value.code == "skill_evaluation_suite_core_roles_required"


def test_only_user_patch_can_add_regression_and_confirm_coverage(tmp_path: Path) -> None:
    store = SkillEvaluationSuiteStore(tmp_path)
    suite = _generated(store)

    model_cases = _core_cases() + [_case("regression", 4)]
    with pytest.raises(SkillEvaluationValidationError) as model_error:
        store.save_generated(
            session_id="session-two",
            session_revision=1,
            session_definition_digest=SESSION_DEFINITION_DIGEST,
            draft_id="draft-two",
            draft_state_revision=1,
            draft_revision=1,
            draft_digest=DIGEST,
            quality_mode="objective",
            cases=model_cases,
            expected_suite_revision=None,
            expected_suite_digest=None,
            allowed_requirement_ids=REQUIREMENTS,
            allowed_resource_paths=(),
            allowed_workflow_step_ids=("step-1",),
        )
    assert model_error.value.code == "skill_evaluation_suite_model_regression_forbidden"

    edited_cases = [
        SkillEvaluationSuiteStore.serialize_case(item) for item in suite.cases
    ]
    edited_cases[0].pop("case_fingerprint")
    edited_cases[0]["prompt"] = "A user-refined normal scenario."
    patched = store.patch(
        suite.suite_id,
        expected_suite_revision=suite.suite_revision,
        expected_suite_digest=suite.suite_digest,
        session_revision=4,
        session_definition_digest=SESSION_DEFINITION_DIGEST,
        draft_state_revision=2,
        cases=[*edited_cases, _case("regression", 4)],
        change_reason="Add a user-confirmed failure as a regression case.",
        allowed_requirement_ids=REQUIREMENTS,
        allowed_resource_paths=("references/rules.md",),
        allowed_workflow_step_ids=("step-1",),
    )
    assert patched.suite_revision == 2
    assert patched.cases[0].source == "user"
    assert patched.cases[-1].source == "user"

    confirmed = store.confirm(
        patched.suite_id,
        expected_suite_revision=patched.suite_revision,
        expected_suite_digest=patched.suite_digest,
        session_revision=5,
        session_definition_digest=SESSION_DEFINITION_DIGEST,
        draft_state_revision=2,
        allowed_requirement_ids=REQUIREMENTS,
        allowed_resource_paths=("references/rules.md",),
        allowed_workflow_step_ids=("step-1",),
    )
    assert confirmed.state == "confirmed"
    assert confirmed.suite_revision == 3

    changed = [SkillEvaluationSuiteStore.serialize_case(case) for case in confirmed.cases]
    changed[0]["prompt"] = "Use the updated normal scenario."
    with pytest.raises(SkillEvaluationValidationError) as reason_error:
        store.patch(
            confirmed.suite_id,
            expected_suite_revision=confirmed.suite_revision,
            expected_suite_digest=confirmed.suite_digest,
            session_revision=6,
            session_definition_digest=SESSION_DEFINITION_DIGEST,
            draft_state_revision=2,
            cases=changed,
            change_reason="",
            allowed_requirement_ids=REQUIREMENTS,
            allowed_resource_paths=("references/rules.md",),
            allowed_workflow_step_ids=("step-1",),
        )
    assert reason_error.value.code == "skill_evaluation_suite_change_reason_required"


def test_confirm_rejects_missing_or_unknown_frozen_coverage(tmp_path: Path) -> None:
    store = SkillEvaluationSuiteStore(tmp_path)
    cases = _core_cases()
    for case in cases:
        case["requirement_ids"] = ["intent"]
    suite = store.save_generated(
        session_id="session-one",
        session_revision=1,
        session_definition_digest=SESSION_DEFINITION_DIGEST,
        draft_id="draft-one",
        draft_state_revision=1,
        draft_revision=1,
        draft_digest=DIGEST,
        quality_mode="objective",
        cases=cases,
        expected_suite_revision=None,
        expected_suite_digest=None,
        allowed_requirement_ids=REQUIREMENTS,
        allowed_resource_paths=(),
        allowed_workflow_step_ids=("step-1",),
    )
    with pytest.raises(SkillEvaluationValidationError) as missing:
        store.confirm(
            suite.suite_id,
            expected_suite_revision=1,
            expected_suite_digest=suite.suite_digest,
            session_revision=2,
            session_definition_digest=SESSION_DEFINITION_DIGEST,
            draft_state_revision=1,
            allowed_requirement_ids=REQUIREMENTS,
            allowed_resource_paths=(),
            allowed_workflow_step_ids=("step-1",),
        )
    assert missing.value.code == "skill_evaluation_suite_coverage_incomplete"

    unknown = _core_cases()
    unknown[0]["requirement_ids"] = ["unknown-requirement"]
    with pytest.raises(SkillEvaluationValidationError) as invalid:
        store.save_generated(
            session_id="session-two",
            session_revision=1,
            session_definition_digest=SESSION_DEFINITION_DIGEST,
            draft_id="draft-two",
            draft_state_revision=1,
            draft_revision=1,
            draft_digest=DIGEST,
            quality_mode="objective",
            cases=unknown,
            expected_suite_revision=None,
            expected_suite_digest=None,
            allowed_requirement_ids=REQUIREMENTS,
            allowed_resource_paths=(),
            allowed_workflow_step_ids=("step-1",),
        )
    assert invalid.value.code == "skill_evaluation_suite_coverage_invalid"


def test_legacy_three_cases_migrate_without_model_and_conflicts_are_immutable(
    tmp_path: Path,
) -> None:
    store = SkillEvaluationSuiteStore(tmp_path)
    legacy_cases = [
        {
            key: value
            for key, value in case.items()
            if key not in {"role", "requirement_ids", "required_resource_paths", "workflow_step_ids"}
        }
        for case in _core_cases()
    ]
    migrated = store.migrate_case_set(
        session_id="session-one",
        session_revision=3,
        session_definition_digest=SESSION_DEFINITION_DIGEST,
        draft_id="draft-one",
        draft_state_revision=2,
        draft_revision=1,
        draft_digest=DIGEST,
        quality_mode="objective",
        cases=legacy_cases,
        allowed_requirement_ids=REQUIREMENTS,
    )
    assert migrated.state == "confirmed"
    assert migrated.suite_revision == 1
    assert all(case.source == "migrated" for case in migrated.cases)

    with pytest.raises(SkillEvaluationConflictError):
        store.patch(
            migrated.suite_id,
            expected_suite_revision=1,
            expected_suite_digest="b" * 64,
            session_revision=4,
            session_definition_digest=SESSION_DEFINITION_DIGEST,
            draft_state_revision=2,
            cases=[SkillEvaluationSuiteStore.serialize_case(case) for case in migrated.cases],
            change_reason="Retain the same cases.",
            allowed_requirement_ids=REQUIREMENTS,
            allowed_resource_paths=(),
            allowed_workflow_step_ids=(),
        )


def test_secrets_are_blocked_and_snapshot_corruption_fails_closed(tmp_path: Path) -> None:
    store = SkillEvaluationSuiteStore(tmp_path)
    cases = _core_cases()
    cases[0]["prompt"] = "Use token " + "sk-" + ("A" * 40)
    with pytest.raises(SkillEvaluationValidationError) as secret_error:
        store.save_generated(
            session_id="session-secret",
            session_revision=1,
            session_definition_digest=SESSION_DEFINITION_DIGEST,
            draft_id="draft-secret",
            draft_state_revision=1,
            draft_revision=1,
            draft_digest=DIGEST,
            quality_mode="objective",
            cases=cases,
            expected_suite_revision=None,
            expected_suite_digest=None,
            allowed_requirement_ids=REQUIREMENTS,
            allowed_resource_paths=(),
            allowed_workflow_step_ids=("step-1",),
        )
    assert secret_error.value.code == "skill_evaluation_suite_credentials_blocked"
    assert not store.snapshot_path.exists()

    store.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    store.snapshot_path.write_text("{broken", encoding="utf-8")
    damaged = SkillEvaluationSuiteStore(tmp_path)
    assert damaged.status()["available"] is False
    with pytest.raises(SkillEvaluationStorageError):
        damaged.current_for_session("session-one")


def test_single_session_corruption_is_quarantined_without_losing_healthy_record(
    tmp_path: Path,
) -> None:
    store = SkillEvaluationSuiteStore(tmp_path)
    broken = _generated(store, "session-broken")
    healthy = _generated(store, "session-healthy")
    payload = json.loads(store.snapshot_path.read_text(encoding="utf-8"))
    payload["sessions"]["session-broken"][0]["cases"][0]["case_fingerprint"] = "0" * 64
    store.snapshot_path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    restored = SkillEvaluationSuiteStore(tmp_path)
    assert restored.status()["available"] is True
    assert restored.status()["quarantine_count"] == 1
    assert restored.current_for_session("session-broken") is None
    assert restored.require(healthy.suite_id).session_id == "session-healthy"
    assert broken.suite_id != healthy.suite_id
