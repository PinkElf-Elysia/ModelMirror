from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.skills import experience, experience_api
from server.skills.application_receipts import (
    SkillApplicationReceiptStore,
    SkillApplicationScope,
    build_application_contract,
)
from server.skills.experience import (
    SkillExperienceCandidateStore,
    SkillExperienceConflictError,
    SkillExperienceError,
    SkillExperienceService,
    SkillExperienceSource,
    SkillExperienceStorageError,
)
from server.xpert_runtime.execution_store import WorkflowExecutionStore
from server.xperts.context import XpertContextStore


def _service(tmp_path: Path) -> tuple[
    SkillExperienceService,
    WorkflowExecutionStore,
    XpertContextStore,
    SkillApplicationReceiptStore,
]:
    executions = WorkflowExecutionStore(tmp_path / "executions")
    contexts = XpertContextStore(tmp_path / "contexts")
    receipts = SkillApplicationReceiptStore(tmp_path / "receipts")
    store = SkillExperienceCandidateStore(tmp_path / "experience")
    return (
        SkillExperienceService(store, executions, contexts, receipts),
        executions,
        contexts,
        receipts,
    )


def _complete_workflow(
    executions: WorkflowExecutionStore,
    *,
    task_id: str = "task-1",
    run_id: str = "run-1",
    user_input: str = "把发布前检查整理成可复用流程",
    result: str = "检查通过",
):
    executions.create(
        task_id=task_id,
        run_id=run_id,
        run_type="workflow",
        source_kind="workflow_classic",
        workflow={
            "id": "workflow-1",
            "title": "发布检查",
            "nodes": [{"id": "node-1", "title": "检查文件"}],
        },
        inputs={"user_input": user_input},
    )
    executions.append_event(
        task_id,
        {"event": "node_completed", "node_id": "node-1", "tool_name": "rg"},
    )
    return executions.complete(task_id, result=result)


def _workflow_source(*, run_id: str = "run-1") -> SkillExperienceSource:
    return SkillExperienceSource(
        source_kind="workflow_classic",
        source_task_id="task-1",
        source_run_id=run_id,
    )


def _rewrite_candidate(payload: dict, mutate) -> dict:
    changed = copy.deepcopy(payload)
    raw = changed["candidates"][0]
    mutate(raw)
    digest_payload = dict(raw)
    digest_payload.pop("digest", None)
    raw["digest"] = experience._sha256(digest_payload)
    return changed


def test_capture_is_idempotent_rebinds_current_run_and_excludes_output_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    service, executions, _, _ = _service(tmp_path)
    executions.create(
        task_id="task-1",
        run_id="run-1",
        run_type="workflow",
        source_kind="workflow_classic",
        workflow={"id": "workflow-1", "title": "发布检查"},
        inputs={"user_input": "检查发布包"},
    )
    executions.update_run_id("task-1", run_id="run-2")
    executions.complete("task-1", result="已完成发布检查")

    first, preview = service.create_or_get(_workflow_source(run_id="run-1"))
    second, repeated_preview = service.create_or_get(_workflow_source(run_id="run-2"))

    assert first.candidate_id == second.candidate_id
    assert first.source_run_id == "run-2"
    assert repeated_preview.preview_fingerprint == preview.preview_fingerprint
    output = next(item for item in preview.candidates if item.kind == "final_output_excerpt")
    assert output.default_selected is False
    assert first.selected_evidence == ()

    with pytest.raises(SkillExperienceError) as exc_info:
        service.create_or_get(_workflow_source(run_id="run-forged"))
    assert exc_info.value.code == "skill_experience_source_invalid"


def test_evidence_selection_is_server_derived_and_revision_guarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    service, executions, _, _ = _service(tmp_path)
    _complete_workflow(executions)
    candidate, preview = service.create_or_get(_workflow_source())
    selected_ids = [
        item.candidate_id for item in preview.candidates if item.default_selected
    ]

    updated = service.select_evidence(
        candidate.candidate_id,
        expected_revision=candidate.revision,
        expected_digest=candidate.digest,
        preview_fingerprint=preview.preview_fingerprint,
        evidence_ids=selected_ids,
    )

    assert updated.revision == 2
    assert [item.evidence_id for item in updated.selected_evidence] == selected_ids
    with pytest.raises(SkillExperienceConflictError) as exc_info:
        service.select_evidence(
            candidate.candidate_id,
            expected_revision=candidate.revision,
            expected_digest=candidate.digest,
            preview_fingerprint=preview.preview_fingerprint,
            evidence_ids=selected_ids,
        )
    assert exc_info.value.code == "skill_experience_candidate_conflict"


def test_stale_preview_is_rejected_and_refresh_recovers_late_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    monkeypatch.setenv("SKILL_APPLICATION_RECEIPT_MODE", "audit")
    service, executions, _, receipts = _service(tmp_path)
    _complete_workflow(executions)
    contract = build_application_contract(
        skill_id="release-check",
        source_kind="workspace_draft",
        version_id="skillversion-1",
        content_digest="a" * 64,
    )
    scope = SkillApplicationScope(
        run_id="run-1",
        task_id="task-1",
        node_id="node-1",
        runtime_kind="workflow",
    )
    receipts.observe(contract, scope)
    candidate, preview = service.create_or_get(_workflow_source())
    receipts.observe(contract, scope, method="skill_read", tool_name="skill_read")
    initial_evidence_ids = [
        item.candidate_id for item in preview.candidates if item.default_selected
    ]

    updated = service.select_evidence(
        candidate.candidate_id,
        expected_revision=candidate.revision,
        expected_digest=candidate.digest,
        preview_fingerprint=preview.preview_fingerprint,
        evidence_ids=initial_evidence_ids,
    )
    assert len(updated.application_receipts) == 1

    executions.append_event(
        "task-1",
        {"event": "node_completed", "node_id": "node-2", "tool_name": "node"},
    )
    with pytest.raises(SkillExperienceConflictError) as exc_info:
        service.select_evidence(
            candidate.candidate_id,
            expected_revision=updated.revision,
            expected_digest=updated.digest,
            preview_fingerprint=preview.preview_fingerprint,
                evidence_ids=initial_evidence_ids,
        )
    assert exc_info.value.code == "skill_experience_evidence_stale"

    refreshed, refreshed_preview = service.get_candidate(candidate.candidate_id)
    assert refreshed.state == "stale"
    assert refreshed_preview is not None
    selected_ids = [
        item.candidate_id
        for item in refreshed_preview.candidates
        if item.default_selected
    ]
    recovered = service.select_evidence(
        candidate.candidate_id,
        expected_revision=refreshed.revision,
        expected_digest=refreshed.digest,
        preview_fingerprint=refreshed_preview.preview_fingerprint,
        evidence_ids=selected_ids,
    )
    assert recovered.state == "captured"
    assert recovered.selected_evidence
    assert len(recovered.application_receipts) == 1


def test_duplicate_capture_remains_idempotent_when_receipt_arrives_late(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    monkeypatch.setenv("SKILL_APPLICATION_RECEIPT_MODE", "audit")
    service, executions, _, receipts = _service(tmp_path)
    _complete_workflow(executions)
    candidate, _ = service.create_or_get(_workflow_source())
    contract = build_application_contract(
        skill_id="release-check",
        source_kind="workspace_draft",
        version_id="skillversion-1",
        content_digest="b" * 64,
    )
    receipts.observe(
        contract,
        SkillApplicationScope(
            run_id="run-1",
            task_id="task-1",
            node_id="node-1",
            runtime_kind="workflow",
        ),
        method="skill_read",
        tool_name="skill_read",
    )

    repeated, _ = service.create_or_get(_workflow_source())

    assert repeated.candidate_id == candidate.candidate_id
    assert repeated.revision == candidate.revision + 1
    assert len(repeated.application_receipts) == 1
    assert len(service.list_candidates()) == 1


def test_receipts_from_server_recorded_recovery_chain_are_retained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    monkeypatch.setenv("SKILL_APPLICATION_RECEIPT_MODE", "audit")
    service, executions, contexts, receipts = _service(tmp_path)
    executions.create(
        task_id="task-1",
        run_id="run-before-resume",
        run_type="workflow",
        source_kind="workflow_classic",
        workflow={"id": "workflow-1", "title": "发布检查"},
        inputs={"user_input": "检查发布包"},
    )
    contract = build_application_contract(
        skill_id="release-check",
        source_kind="workspace_draft",
        version_id="skillversion-1",
        content_digest="c" * 64,
    )
    receipts.observe(
        contract,
        SkillApplicationScope(
            run_id="run-before-resume",
            task_id="task-1",
            node_id="node-1",
            runtime_kind="workflow",
        ),
        method="skill_read",
        tool_name="skill_read",
    )
    executions.update_run_id("task-1", run_id="run-after-resume")
    executions.complete("task-1", result="检查通过")

    reloaded_service = SkillExperienceService(
        SkillExperienceCandidateStore(service.store.storage_dir),
        WorkflowExecutionStore(executions.storage_dir),
        contexts,
        SkillApplicationReceiptStore(receipts.storage_dir),
    )
    candidate, _ = reloaded_service.create_or_get(
        _workflow_source(run_id="run-before-resume")
    )

    assert candidate.source_run_id == "run-after-resume"
    assert len(candidate.application_receipts) == 1


def test_concurrent_capture_creates_exactly_one_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    service, executions, _, _ = _service(tmp_path)
    _complete_workflow(executions)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: service.create_or_get(_workflow_source())[0], range(16)))

    assert len({item.candidate_id for item in results}) == 1
    assert len(service.list_candidates()) == 1


def test_atomic_publish_failure_rolls_back_in_memory_and_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    service, executions, _, _ = _service(tmp_path)
    _complete_workflow(executions)

    with monkeypatch.context() as scoped:
        scoped.setattr(experience.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("blocked")))
        with pytest.raises(SkillExperienceStorageError):
            service.create_or_get(_workflow_source())

    assert service.list_candidates() == []
    assert not service.store.snapshot_path.exists()


@pytest.mark.parametrize("status", ["running", "waiting", "failed", "cancelled"])
def test_non_completed_workflow_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    service, executions, _, _ = _service(tmp_path)
    executions.create(
        task_id="task-1",
        run_id="run-1",
        run_type="workflow",
        source_kind="workflow_classic",
        workflow={"id": "workflow-1", "title": "test"},
        inputs={"user_input": "test"},
    )
    if status == "waiting":
        executions.suspend(
            "task-1", wait_id="approval-1", continuation={"node": "node-1"}
        )
    elif status == "failed":
        executions.fail("task-1", error="failed")
    elif status == "cancelled":
        executions.cancel("task-1")

    with pytest.raises(Exception) as exc_info:
        service.create_or_get(_workflow_source())
    assert getattr(exc_info.value, "code", None) == "skill_experience_source_not_completed"


def test_private_xpert_message_scope_is_required_and_public_app_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    service, executions, contexts, _ = _service(tmp_path)
    conversation = contexts.create_conversation("xpert-1", title="复盘助手")
    contexts.append_message(
        "xpert-1",
        conversation.conversation_id,
        role="user",
        content="复盘这次发布",
        source_task_id="task-x",
        source_run_id="run-x",
    )
    assistant = contexts.append_message(
        "xpert-1",
        conversation.conversation_id,
        role="assistant",
        content="复盘完成",
        source_task_id="task-x",
        source_run_id="run-x",
    )
    executions.create(
        task_id="task-x",
        run_id="run-x",
        run_type="xpert",
        source_kind="xpert_chat",
        workflow={"id": "xpert-workflow", "title": "复盘助手"},
        inputs={"user_input": "复盘这次发布"},
        runtime_metadata={
            "xpert_id": "xpert-1",
            "conversation_id": conversation.conversation_id,
        },
    )
    executions.complete("task-x", result="复盘完成")
    candidate, _ = service.create_or_get(
        SkillExperienceSource(
            source_kind="xpert_chat",
            source_task_id="task-x",
            source_run_id="run-x",
            source_xpert_id="xpert-1",
            source_conversation_id=conversation.conversation_id,
            source_message_id=assistant.message_id,
        )
    )
    assert candidate.source_message_id == assistant.message_id

    executions.create(
        task_id="task-public",
        run_id="run-public",
        run_type="xpert",
        source_kind="xpert_app",
        workflow={"id": "app-workflow", "title": "public"},
        inputs={"user_input": "public"},
        runtime_metadata={"app_id": "app-1"},
    )
    executions.complete("task-public", result="done")
    with pytest.raises(Exception) as exc_info:
        service.create_or_get(
            SkillExperienceSource(
                source_kind="xpert_chat",
                source_task_id="task-public",
                source_run_id="run-public",
                source_xpert_id="xpert-1",
                source_conversation_id=conversation.conversation_id,
                source_message_id=assistant.message_id,
            )
        )
    assert getattr(exc_info.value, "code", None) == "skill_experience_source_invalid"


def test_goal_handoff_evaluation_and_creator_markers_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    excluded = [
        ("goal_id", "goal-1"),
        ("handoff_id", "handoff-1"),
        ("evaluation_run_id", "evaluation-1"),
        ("creator_session_id", "creator-1"),
    ]
    for index, (marker, value) in enumerate(excluded):
        scoped = tmp_path / str(index)
        service, executions, _, _ = _service(scoped)
        executions.create(
            task_id="task-1",
            run_id="run-1",
            run_type="workflow",
            source_kind="workflow_classic",
            workflow={"id": "workflow-1", "title": "excluded"},
            inputs={"user_input": "excluded"},
            runtime_metadata={marker: value},
        )
        executions.complete("task-1", result="done")
        with pytest.raises(Exception) as exc_info:
            service.create_or_get(_workflow_source())
        assert getattr(exc_info.value, "code", None) == "skill_experience_source_invalid"


def test_secrets_are_redacted_before_preview_and_never_persisted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    service, executions, _, _ = _service(tmp_path)
    secret = "never-persist-this-secret-123456"
    _complete_workflow(
        executions,
        user_input=f"发布前检查，API_KEY={secret}",
        result=f"Bearer {secret}",
    )
    candidate, preview = service.create_or_get(_workflow_source())
    selected = [item.candidate_id for item in preview.candidates]
    service.select_evidence(
        candidate.candidate_id,
        expected_revision=candidate.revision,
        expected_digest=candidate.digest,
        preview_fingerprint=preview.preview_fingerprint,
        evidence_ids=selected,
    )

    assert secret not in json.dumps(preview.to_payload(), ensure_ascii=False)
    assert secret not in service.store.snapshot_path.read_text(encoding="utf-8")


def test_user_supplied_dismissal_reason_cannot_persist_a_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    service, executions, _, _ = _service(tmp_path)
    _complete_workflow(executions)
    candidate, _ = service.create_or_get(_workflow_source())
    secret = "never-persist-this-secret-123456"

    with pytest.raises(Exception) as exc_info:
        service.dismiss(
            candidate.candidate_id,
            expected_revision=candidate.revision,
            expected_digest=candidate.digest,
            reason=f"API_KEY={secret}",
        )

    assert getattr(exc_info.value, "code", None) == "skill_experience_source_invalid"
    assert secret not in service.store.snapshot_path.read_text(encoding="utf-8")


def test_store_isolates_bad_records_and_top_level_corruption_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    service, executions, _, _ = _service(tmp_path)
    _complete_workflow(executions)
    candidate, _ = service.create_or_get(_workflow_source())
    snapshot_path = service.store.snapshot_path
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    payload["candidates"].append({"candidate_id": "broken"})
    snapshot_path.write_text(json.dumps(payload), encoding="utf-8")

    isolated = SkillExperienceCandidateStore(service.store.storage_dir)
    assert isolated.require(candidate.candidate_id).candidate_id == candidate.candidate_id
    assert isolated.status()["quarantine_count"] == 1

    original = b"{not-json"
    snapshot_path.write_bytes(original)
    corrupted = SkillExperienceCandidateStore(service.store.storage_dir)
    assert corrupted.status()["available"] is False
    with pytest.raises(SkillExperienceStorageError):
        corrupted.list_candidates()
    assert snapshot_path.read_bytes() == original


def test_store_rejects_unknown_snapshot_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    service, executions, _, _ = _service(tmp_path)
    _complete_workflow(executions)
    service.create_or_get(_workflow_source())
    payload = json.loads(service.store.snapshot_path.read_text(encoding="utf-8"))
    payload["version"] = "skill-experience-candidate-v999"
    service.store.snapshot_path.write_text(json.dumps(payload), encoding="utf-8")

    reloaded = SkillExperienceCandidateStore(service.store.storage_dir)

    assert reloaded.status()["available"] is False


def test_store_quarantines_semantically_invalid_candidate_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    service, executions, _, _ = _service(tmp_path)
    _complete_workflow(executions)
    candidate, preview = service.create_or_get(_workflow_source())
    service.select_evidence(
        candidate.candidate_id,
        expected_revision=candidate.revision,
        expected_digest=candidate.digest,
        preview_fingerprint=preview.preview_fingerprint,
        evidence_ids=[preview.candidates[0].candidate_id],
    )
    base = json.loads(service.store.snapshot_path.read_text(encoding="utf-8"))

    def inject_secret(raw: dict) -> None:
        summary = "API_KEY=never-persist-this-secret-123456"
        raw["selected_evidence"][0]["summary"] = summary
        raw["selected_evidence"][0]["content_hash"] = experience._sha256(
            {
                "kind": raw["selected_evidence"][0]["kind"],
                "summary": summary,
            }
        )

    def duplicate_evidence(raw: dict) -> None:
        raw["selected_evidence"].append(
            copy.deepcopy(raw["selected_evidence"][0])
        )

    def inject_xpert_scope(raw: dict) -> None:
        raw["source_xpert_id"] = "xpert-forged"
        raw["source_conversation_id"] = "conversation-forged"
        raw["source_message_id"] = "message-forged"

    for name, mutate in (
        ("secret", inject_secret),
        ("duplicate", duplicate_evidence),
        ("scope", inject_xpert_scope),
    ):
        storage_dir = tmp_path / f"invalid-{name}"
        storage_dir.mkdir()
        snapshot_path = storage_dir / "skill_experience_candidates.json"
        snapshot_path.write_text(
            json.dumps(_rewrite_candidate(base, mutate)),
            encoding="utf-8",
        )

        reloaded = SkillExperienceCandidateStore(storage_dir)

        assert reloaded.status()["candidate_count"] == 0
        assert reloaded.status()["quarantine_count"] == 1


def test_source_invalidation_is_projected_as_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    service, executions, _, _ = _service(tmp_path)
    _complete_workflow(executions)
    candidate, _ = service.create_or_get(_workflow_source())
    executions.fail("task-1", error="late invalidation")

    stale, preview = service.get_candidate(candidate.candidate_id)

    assert stale.state == "stale"
    assert stale.revision == candidate.revision + 1
    assert preview is None


def test_candidate_get_fails_closed_when_receipt_store_is_corrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    service, executions, contexts, _ = _service(tmp_path)
    _complete_workflow(executions)
    candidate, _ = service.create_or_get(_workflow_source())
    receipt_dir = tmp_path / "corrupt-receipts"
    receipt_dir.mkdir()
    (receipt_dir / "skill_application_receipts.json").write_bytes(b"{not-json")
    corrupted_receipts = SkillApplicationReceiptStore(receipt_dir)
    corrupted_service = SkillExperienceService(
        service.store,
        executions,
        contexts,
        corrupted_receipts,
    )

    with pytest.raises(SkillExperienceStorageError):
        corrupted_service.get_candidate(candidate.candidate_id)


def test_api_status_is_always_readable_and_disabled_routes_are_hidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "false")
    service, _, _, _ = _service(tmp_path)
    previous = experience_api._service
    experience_api.configure_skill_experience(service)
    app = FastAPI()
    app.include_router(experience_api.router)
    try:
        client = TestClient(app)
        status = client.get("/api/skills/experience/status")
        assert status.status_code == 200
        assert status.json()["enabled"] is False
        blocked = client.get("/api/skills/experience/candidates")
        assert blocked.status_code == 404
        assert blocked.json()["detail"]["code"] == "skill_experience_disabled"
    finally:
        experience_api.configure_skill_experience(previous)


def test_experience_promotion_is_enabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", raising=False)

    assert experience.experience_promotion_enabled() is True


def test_api_rejects_client_submitted_summary_or_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_EXPERIENCE_PROMOTION_ENABLED", "true")
    service, executions, _, _ = _service(tmp_path)
    _complete_workflow(executions)
    previous = experience_api._service
    experience_api.configure_skill_experience(service)
    app = FastAPI()
    app.include_router(experience_api.router)
    try:
        response = TestClient(app).post(
            "/api/skills/experience/candidates",
            json={
                "source_kind": "workflow_classic",
                "source_task_id": "task-1",
                "source_run_id": "run-1",
                "summary": "client must not control this",
                "state": "promotion_ready",
            },
        )
        assert response.status_code == 422
        assert service.list_candidates() == []
    finally:
        experience_api.configure_skill_experience(previous)
