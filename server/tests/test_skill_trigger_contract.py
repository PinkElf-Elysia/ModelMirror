from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from server.skills.creator_store import SkillCreatorValidationError
from server.skills.finder import SkillFinder
from server.skills.skill_manager import InstalledSkill
from server.skills.trigger_contract import (
    SkillTriggerConflictError,
    SkillTriggerEvaluator,
    SkillTriggerNotFoundError,
    SkillTriggerStorageError,
    SkillTriggerStore,
    trigger_definition_digest,
    trigger_optimization_enabled,
)


ROOT = Path(__file__).resolve().parents[2]
INDEX_PATH = ROOT / "server" / "skills" / "data" / "skill_runtime_index.json"
SKILL_NAME = "incident-timeline-guide"
DESCRIPTION = (
    "将软件服务故障记录整理为无责事故复盘，提取时间线、根因、影响与纠正行动；"
    "适用于已有故障证据的 incident postmortem、outage review 和 action items，"
    "并提供可核验的 incident timeline guide。"
)


class StubSkillManager:
    def __init__(self, skills: list[InstalledSkill] | None = None) -> None:
        self.skills = list(skills or [])

    def list_installed_skills(self) -> list[InstalledSkill]:
        return list(self.skills)


def cases(*, unsafe_negative_terms: bool = False) -> list[dict[str, str]]:
    negative_one = (
        "请为产品发布公告、普通周报和摘要执行事故复盘"
        if unsafe_negative_terms
        else "把产品发布公告压缩成三句话摘要"
    )
    return [
        {
            "kind": "should_trigger",
            "source": "model",
            "text": "请分析线上服务故障，整理时间线、根因与纠正措施",
        },
        {
            "kind": "should_trigger",
            "source": "model",
            "text": "Turn outage notes into a blameless incident postmortem with action items",
        },
        {"kind": "should_not_trigger", "source": "model", "text": negative_one},
        {
            "kind": "should_not_trigger",
            "source": "model",
            "text": "编辑一份普通团队周报并调整语气",
        },
        {
            "kind": "exact_name_smoke",
            "source": "session",
            "text": SKILL_NAME,
        },
    ]


def definition_digest() -> str:
    return trigger_definition_digest(
        intent="根据软件故障记录生成无责事故复盘",
        positive_examples=["线上故障时间线", "outage postmortem"],
        near_miss_examples=["普通摘要", "团队周报"],
    )


def confirmed_suite(store: SkillTriggerStore, *, session_id: str = "session-one"):
    draft = store.save_draft(
        session_id=session_id,
        session_revision=1,
        definition_digest=definition_digest(),
        skill_name=SKILL_NAME,
        cases=cases(),
    )
    return store.confirm(
        suite_id=draft.suite_id,
        expected_suite_revision=draft.suite_revision,
        expected_suite_digest=draft.suite_digest,
        session_revision=1,
        definition_digest=definition_digest(),
        skill_name=SKILL_NAME,
        actor_id="local-console-instance",
    )


def test_trigger_flag_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SKILL_CREATOR_TRIGGER_OPTIMIZATION_ENABLED", raising=False)
    assert trigger_optimization_enabled() is False
    monkeypatch.setenv("SKILL_CREATOR_TRIGGER_OPTIMIZATION_ENABLED", "true")
    assert trigger_optimization_enabled() is True


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda items: items[:3], "should-not-trigger"),
        (lambda items: [*items, dict(items[0])], "unique"),
        (
            lambda items: [
                {**items[0], "text": f"Use {SKILL_NAME} for the outage"},
                *items[1:],
            ],
            "Exact Skill names",
        ),
        (
            lambda items: [
                *items,
                {
                    "kind": "exact_name_smoke",
                    "source": "user",
                    "text": f"Run {SKILL_NAME} now",
                },
            ],
            "Only one exact-name",
        ),
    ],
)
def test_suite_rejects_invalid_case_contract(tmp_path: Path, mutate, message: str) -> None:
    store = SkillTriggerStore(tmp_path)
    with pytest.raises(SkillCreatorValidationError, match=message):
        store.save_draft(
            session_id="session-one",
            session_revision=1,
            definition_digest=definition_digest(),
            skill_name=SKILL_NAME,
            cases=mutate(cases()),
        )


def test_suite_is_append_only_and_requires_reason_after_confirmation(tmp_path: Path) -> None:
    store = SkillTriggerStore(tmp_path)
    confirmed = confirmed_suite(store)
    assert confirmed.suite_revision == 2
    assert confirmed.state == "confirmed"
    assert confirmed.confirmed_actor_id == "local-console-instance"
    assert store.require_suite(confirmed.suite_id, revision=1).state == "draft"

    with pytest.raises(SkillCreatorValidationError, match="reason"):
        store.save_draft(
            session_id="session-one",
            session_revision=2,
            definition_digest=definition_digest(),
            skill_name=SKILL_NAME,
            cases=cases(),
            expected_suite_revision=confirmed.suite_revision,
            expected_suite_digest=confirmed.suite_digest,
        )

    revised = store.save_draft(
        session_id="session-one",
        session_revision=2,
        definition_digest=definition_digest(),
        skill_name=SKILL_NAME,
        cases=cases(),
        expected_suite_revision=confirmed.suite_revision,
        expected_suite_digest=confirmed.suite_digest,
        change_reason="Clarified the near-miss wording.",
    )
    assert revised.suite_revision == 3
    assert revised.based_on_revision == 2
    assert SkillTriggerStore(tmp_path).current_for_session("session-one") == revised

    with pytest.raises(SkillTriggerConflictError):
        store.mark_stale(
            suite_id=revised.suite_id,
            expected_suite_revision=2,
            expected_suite_digest=confirmed.suite_digest,
            reason="Index changed.",
        )


def test_secret_like_case_is_rejected_before_snapshot_write(tmp_path: Path) -> None:
    unsafe = cases()
    unsafe[0] = {
        **unsafe[0],
        "text": (
            "Use "
            + "OPENAI_API_KEY="
            + "fixture-credential-value-12345 to inspect outage notes"
        ),
    }
    store = SkillTriggerStore(tmp_path)
    with pytest.raises(SkillCreatorValidationError, match="credential-like"):
        store.save_draft(
            session_id="session-secret",
            session_revision=1,
            definition_digest=definition_digest(),
            skill_name=SKILL_NAME,
            cases=unsafe,
        )
    assert not store.snapshot_path.exists()


def test_store_isolates_bad_session_record(tmp_path: Path) -> None:
    store = SkillTriggerStore(tmp_path)
    confirmed_suite(store, session_id="session-good")
    confirmed_suite(store, session_id="session-bad")
    payload = json.loads(store.snapshot_path.read_text(encoding="utf-8"))
    payload["sessions"]["session-bad"][0]["cases"][0]["text"] = "tampered"
    store.snapshot_path.write_text(json.dumps(payload), encoding="utf-8")

    recovered = SkillTriggerStore(tmp_path)
    assert recovered.current_for_session("session-good") is not None
    assert recovered.current_for_session("session-bad") is None
    assert recovered.status()["quarantine_count"] == 1


def test_empty_session_records_cannot_exhaust_capacity(tmp_path: Path) -> None:
    seed = SkillTriggerStore(tmp_path)
    confirmed_suite(seed, session_id="seed-session")
    payload = json.loads(seed.snapshot_path.read_text(encoding="utf-8"))
    payload["sessions"] = {
        f"empty-session-{index}": []
        for index in range(500)
    }
    payload["receipts"] = {}
    seed.snapshot_path.write_text(json.dumps(payload), encoding="utf-8")

    recovered = SkillTriggerStore(tmp_path)
    assert recovered.status()["available"] is True
    assert recovered.status()["session_count"] == 0
    assert recovered.status()["quarantine_count"] == 500
    created = recovered.save_draft(
        session_id="new-session",
        session_revision=1,
        definition_digest=definition_digest(),
        skill_name=SKILL_NAME,
        cases=cases(),
    )
    assert created.suite_revision == 1
    assert SkillTriggerStore(tmp_path).current_for_session("new-session") == created


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    (("created_at", float("nan")), ("confirmed_at", float("inf"))),
)
def test_non_finite_suite_timestamp_is_quarantined(
    tmp_path: Path,
    field_name: str,
    invalid_value: float,
) -> None:
    store = SkillTriggerStore(tmp_path)
    confirmed_suite(store, session_id="session-non-finite")
    payload = json.loads(store.snapshot_path.read_text(encoding="utf-8"))
    payload["sessions"]["session-non-finite"][-1][field_name] = invalid_value
    store.snapshot_path.write_text(
        json.dumps(payload, allow_nan=True),
        encoding="utf-8",
    )

    recovered = SkillTriggerStore(tmp_path)
    assert recovered.status()["available"] is True
    assert recovered.current_for_session("session-non-finite") is None
    assert recovered.status()["session_count"] == 0
    assert recovered.status()["quarantine_count"] == 1


def test_top_level_corruption_fails_closed_without_overwrite(tmp_path: Path) -> None:
    snapshot = tmp_path / "skill_creator_trigger_contracts.json"
    snapshot.write_text("{not-json", encoding="utf-8")
    original = snapshot.read_bytes()
    store = SkillTriggerStore(tmp_path)
    assert store.status()["available"] is False
    with pytest.raises(SkillTriggerStorageError):
        store.current_for_session("session-one")
    with pytest.raises(SkillTriggerStorageError):
        store.save_draft(
            session_id="session-one",
            session_revision=1,
            definition_digest=definition_digest(),
            skill_name=SKILL_NAME,
            cases=cases(),
        )
    assert snapshot.read_bytes() == original


def test_evaluator_uses_production_finder_and_router_windows(tmp_path: Path) -> None:
    store = SkillTriggerStore(tmp_path)
    suite = confirmed_suite(store)
    evaluator = SkillTriggerEvaluator(
        SkillFinder(index_path=INDEX_PATH, skill_manager=StubSkillManager())
    )
    receipt = evaluator.evaluate(
        suite=suite,
        skill_id="incident-timeline-guide",
        skill_name=SKILL_NAME,
        description=DESCRIPTION,
    )

    projected = InstalledSkill(
        skill_id="incident-timeline-guide",
        name=SKILL_NAME,
        description=DESCRIPTION,
        repo_url="workspace://draft/incident-timeline-guide",
        sub_path="incident-timeline-guide",
        installed_at=0.0,
        source_kind="workspace_draft",
        source_id=suite.session_id,
        source_revision=suite.suite_revision,
        content_digest=receipt.description_digest,
        package_subpath="incident-timeline-guide",
    )
    finder = SkillFinder(
        index_path=INDEX_PATH,
        skill_manager=StubSkillManager([projected]),
    )
    target_id = "installed:incident-timeline-guide"
    for case, case_result in zip(suite.cases, receipt.case_results, strict=True):
        finder_top = finder.find(case.text)["results"]
        finder_recall = finder.recall(case.text)["results"]
        router_top = finder.find(case.text, router_eligible_only=True)["results"]
        router_recall = finder.recall(case.text, router_eligible_only=True)["results"]
        assert case_result.finder.rank_top_6 == _rank(finder_top, target_id)
        assert case_result.finder.rank_top_24 == _rank(finder_recall, target_id)
        assert case_result.router.rank_top_6 == _rank(router_top, target_id)
        assert case_result.router.rank_top_24 == _rank(router_recall, target_id)
        assert target_id not in {item.candidate_id for item in case_result.finder.competitors}
    assert receipt.passed is True
    assert all(result.passed for result in receipt.case_results)
    assert receipt.ranker_version == finder.find("outage")["rankerVersion"]
    assert receipt.runtime_index_fingerprint == finder.index_metadata()[
        "runtimeIndexFingerprint"
    ]
    assert receipt.directory_fingerprint == finder.index_metadata()[
        "directoryFingerprint"
    ]
    assert receipt.trust_index_fingerprint == finder.index_metadata()[
        "trustIndexFingerprint"
    ]

    negative = receipt.case_results[2]
    tampered_negative = replace(
        negative,
        finder=replace(
            negative.finder,
            rank_top_6=1,
            rank_top_24=1,
            in_top_6=True,
            in_top_24=True,
        ),
    )
    tampered = replace(
        receipt,
        case_results=(
            *receipt.case_results[:2],
            tampered_negative,
            *receipt.case_results[3:],
        ),
    )
    with pytest.raises(ValueError, match="gate result"):
        store.save_receipt(tampered)

    stored = store.save_receipt(receipt)
    reloaded = SkillTriggerStore(tmp_path)
    assert reloaded.require_receipt(stored.receipt_id) == stored
    persisted = json.loads(store.snapshot_path.read_text(encoding="utf-8"))
    receipt_payload = persisted["receipts"][stored.receipt_id]
    assert "text" not in json.dumps(receipt_payload)
    persisted["receipts"]["triggerreceipt_bad"] = dict(receipt_payload)
    invalid_boolean = dict(receipt_payload)
    invalid_boolean["passed"] = "false"
    persisted["receipts"]["triggerreceipt_invalid_boolean"] = invalid_boolean
    store.snapshot_path.write_text(json.dumps(persisted), encoding="utf-8")
    isolated = SkillTriggerStore(tmp_path)
    assert isolated.require_receipt(stored.receipt_id) == stored
    assert isolated.status()["quarantine_count"] == 2

    persisted["receipts"][stored.receipt_id]["created_at"] = float("nan")
    store.snapshot_path.write_text(
        json.dumps(persisted, allow_nan=True),
        encoding="utf-8",
    )
    non_finite = SkillTriggerStore(tmp_path)
    with pytest.raises(SkillTriggerNotFoundError):
        non_finite.require_receipt(stored.receipt_id)
    assert non_finite.status()["quarantine_count"] == 3


def test_evaluator_exposes_top_24_diagnostics_without_weakening_top_6_gate(
    tmp_path: Path,
) -> None:
    store = SkillTriggerStore(tmp_path)
    suite = confirmed_suite(store)
    evaluator = SkillTriggerEvaluator(
        SkillFinder(index_path=INDEX_PATH, skill_manager=StubSkillManager())
    )
    unsafe_description = (
        DESCRIPTION
        + " 同时处理产品发布公告、普通团队周报、三句话摘要和语气调整。"
    )
    receipt = evaluator.evaluate(
        suite=suite,
        skill_id="incident-timeline-guide",
        skill_name=SKILL_NAME,
        description=unsafe_description,
    )
    negative_results = [
        result for result in receipt.case_results if result.kind == "should_not_trigger"
    ]
    assert receipt.passed is False
    assert any(result.finder.in_top_6 or result.router.in_top_6 for result in negative_results)
    assert all(
        result.finder.rank_top_24 is not None or result.router.rank_top_24 is not None
        for result in negative_results
    )


def test_description_and_index_bind_receipt_fingerprint(tmp_path: Path) -> None:
    store = SkillTriggerStore(tmp_path)
    suite = confirmed_suite(store)
    evaluator = SkillTriggerEvaluator(
        SkillFinder(index_path=INDEX_PATH, skill_manager=StubSkillManager())
    )
    first = evaluator.evaluate(
        suite=suite,
        skill_id="incident-timeline-guide",
        skill_name=SKILL_NAME,
        description=DESCRIPTION,
    )
    second = evaluator.evaluate(
        suite=suite,
        skill_id="incident-timeline-guide",
        skill_name=SKILL_NAME,
        description=DESCRIPTION + " 仅输出可验证的行动项。",
    )
    assert first.description_digest != second.description_digest
    assert first.candidate_fingerprint != second.candidate_fingerprint
    assert first.receipt_id != second.receipt_id
    store.save_receipt(first)
    assert (
        store.matching_receipt(
            suite_id=suite.suite_id,
            suite_revision=suite.suite_revision,
            suite_digest=suite.suite_digest,
            description_digest=first.description_digest,
            runtime_index_fingerprint=first.runtime_index_fingerprint,
            candidate_fingerprint=first.candidate_fingerprint,
            candidate_set_fingerprint=first.candidate_set_fingerprint,
        )
        == first
    )

    unrelated = InstalledSkill(
        skill_id="workspace-weekly-summary",
        name="workspace-weekly-summary",
        description="Summarize a weekly team update.",
        repo_url="workspace://draft/workspace-weekly-summary",
        sub_path="workspace-weekly-summary",
        installed_at=1.0,
        source_kind="workspace_draft",
    )
    with_unrelated_candidate = SkillTriggerEvaluator(
        SkillFinder(
            index_path=INDEX_PATH,
            skill_manager=StubSkillManager([unrelated]),
        )
    ).evaluate(
        suite=suite,
        skill_id="incident-timeline-guide",
        skill_name=SKILL_NAME,
        description=DESCRIPTION,
    )
    assert with_unrelated_candidate.candidate_fingerprint == first.candidate_fingerprint
    assert (
        with_unrelated_candidate.candidate_set_fingerprint
        != first.candidate_set_fingerprint
    )


def test_evaluator_reuses_frozen_finder_index_snapshot(tmp_path: Path) -> None:
    runtime_index = tmp_path / "runtime.json"
    runtime_index.write_bytes(INDEX_PATH.read_bytes())
    store = SkillTriggerStore(tmp_path / "store")
    suite = confirmed_suite(store)
    finder = SkillFinder(index_path=runtime_index, skill_manager=StubSkillManager())
    evaluator = SkillTriggerEvaluator(finder)
    first = evaluator.evaluate(
        suite=suite,
        skill_id="incident-timeline-guide",
        skill_name=SKILL_NAME,
        description=DESCRIPTION,
    )

    changed = json.loads(runtime_index.read_text(encoding="utf-8"))
    changed["candidates"] = changed["candidates"][:-1]
    changed["fingerprint"] = _runtime_index_fingerprint(changed)
    runtime_index.write_text(json.dumps(changed), encoding="utf-8")
    assert changed["fingerprint"] != first.runtime_index_fingerprint

    second = evaluator.evaluate(
        suite=suite,
        skill_id="incident-timeline-guide",
        skill_name=SKILL_NAME,
        description=DESCRIPTION,
    )
    assert second.runtime_index_fingerprint == first.runtime_index_fingerprint
    assert second.candidate_set_fingerprint == first.candidate_set_fingerprint
    assert second.receipt_id == first.receipt_id


def test_invalid_runtime_index_returns_stable_error(tmp_path: Path) -> None:
    store = SkillTriggerStore(tmp_path / "store")
    suite = confirmed_suite(store)
    bad_index = tmp_path / "runtime.json"
    bad_index.write_text("{}", encoding="utf-8")
    evaluator = SkillTriggerEvaluator(
        SkillFinder(index_path=bad_index, skill_manager=StubSkillManager())
    )
    with pytest.raises(SkillTriggerStorageError) as captured:
        evaluator.evaluate(
            suite=suite,
            skill_id="incident-timeline-guide",
            skill_name=SKILL_NAME,
            description=DESCRIPTION,
        )
    assert captured.value.code == "skill_trigger_index_unavailable"


def _rank(results: list[dict[str, object]], candidate_id: str) -> int | None:
    return next(
        (
            index
            for index, item in enumerate(results, 1)
            if item["candidateId"] == candidate_id
        ),
        None,
    )


def _runtime_index_fingerprint(payload: dict[str, object]) -> str:
    content = {
        "version": payload.get("version"),
        "rankerVersion": payload.get("rankerVersion"),
        "memberIndexFingerprint": payload.get("memberIndexFingerprint"),
        "catalogFingerprint": payload.get("catalogFingerprint"),
        "trustIndexFingerprint": payload.get("trustIndexFingerprint"),
        "supersededCandidateIds": payload.get("supersededCandidateIds", []),
        "candidates": payload.get("candidates"),
    }
    encoded = json.dumps(
        content,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
