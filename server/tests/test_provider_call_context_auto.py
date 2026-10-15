"""Trusted provenance does not replace Auto's explicitly planned attempts."""
from dataclasses import asdict, replace
import json

import pytest

from server.model_router.service import RouterServiceError
from server.tests.test_provider_chat_auto_service import _service


def _attempt(service, run, position=0):
    return service.claim_attempt(
        run, position=position, connection_id="synthetic-connection",
        provider_kind="openrouter",
    )


def test_auto_durable_origin_conflict_has_stable_error_and_no_second_dispatch(tmp_path, monkeypatch):
    service, _, repository = _service(tmp_path, monkeypatch)
    run = service.begin("auto", strategy="auto_native")
    attempt = _attempt(service, run)
    with repository._connect() as db:
        db.execute("UPDATE provider_call_contexts SET context_json=? WHERE tenant_id='local' AND attempt_id=?",
                   (json.dumps(asdict(replace(attempt.call_context, purpose="evaluation"))), attempt.attempt_id))
    with pytest.raises(RouterServiceError) as error:
        service.mark_dispatched(run, attempt)
    assert error.value.code == "provider_call_context_conflict"
    with pytest.raises(RouterServiceError) as error:
        service.mark_dispatched(run, attempt)
    assert error.value.code == "provider_chat_auto_duplicate_post_blocked"
    assert not repository.list_chat_control_receipts("local")["attempts"][0]["dispatched"]


def test_auto_attempts_share_origin_but_have_distinct_attempt_ids(tmp_path, monkeypatch):
    service, _, repository = _service(tmp_path, monkeypatch)
    run = service.begin("auto", strategy="auto_native")
    first = _attempt(service, run)
    service.mark_dispatched(run, first)
    service.complete_attempt(run, first, status="failed", result_class="transient_failure")
    second = _attempt(service, run, 1)
    service.mark_dispatched(run, second)
    assert first.call_context.actor_kind == "local_anonymous"
    assert first.call_context.actor_reference == "local-anonymous"
    assert first.call_context.entry_id == "chat_auto"
    assert first.call_context.purpose == "user_run"
    assert first.call_context.logical_call_id == second.call_context.logical_call_id
    assert first.call_context.attempt_id != second.call_context.attempt_id
    assert service.claim_attempt(
        run, position=1, connection_id="synthetic-connection", provider_kind="openrouter",
    ) is second
    with pytest.raises(RouterServiceError) as error:
        service.mark_dispatched(run, second)
    assert error.value.code == "provider_chat_auto_duplicate_post_blocked"
    rows = repository.list_chat_control_receipts("local")
    assert len(rows["attempts"]) == 2
    assert sum(row["dispatched"] for row in rows["attempts"]) == 2


@pytest.mark.parametrize("mutation,code", [
    ({"actor_kind": "service", "actor_reference": "scheduler"}, "actor_mismatch"),
    ({"purpose": "certification"}, "purpose_mismatch"),
    ({"entry_id": "xpert"}, "entry_mismatch"),
    ({"parent_run_id": "other-run"}, "reference_mismatch"),
    ({"logical_call_id": "other-call"}, "reference_mismatch"),
    ({"attempt_id": "other-attempt"}, "reference_mismatch"),
    ({"policy_revision": 999}, "policy_changed"),
])
def test_auto_context_drift_never_marks_dispatch(tmp_path, monkeypatch, mutation, code):
    service, _, repository = _service(tmp_path, monkeypatch)
    run = service.begin("auto", strategy="auto_native")
    attempt = _attempt(service, run)
    attempt.call_context = replace(attempt.call_context, **mutation)
    with pytest.raises(RouterServiceError) as error:
        service.mark_dispatched(run, attempt)
    assert error.value.code == "provider_call_context_" + code
    assert not repository.list_chat_control_receipts("local")["attempts"][0]["dispatched"]


@pytest.mark.parametrize("missing", [None, {}])
def test_auto_missing_context_does_not_fall_back(tmp_path, monkeypatch, missing):
    service, _, repository = _service(tmp_path, monkeypatch)
    run = service.begin("auto", strategy="auto_native")
    attempt = _attempt(service, run)
    attempt.call_context = missing
    with pytest.raises(RouterServiceError) as error:
        service.mark_dispatched(run, attempt)
    assert error.value.code == "provider_call_context_required"
    assert not repository.list_chat_control_receipts("local")["attempts"][0]["dispatched"]


def test_auto_attempt_cannot_be_dispatched_from_another_run(tmp_path, monkeypatch):
    service, _, repository = _service(tmp_path, monkeypatch)
    owner = service.begin("auto", strategy="auto_native")
    other = service.begin("auto", strategy="auto_native")
    attempt = _attempt(service, owner)
    with pytest.raises(RouterServiceError) as error:
        service.mark_dispatched(other, attempt)
    assert error.value.code == "provider_call_context_reference_mismatch"
    assert not repository.list_chat_control_receipts("local")["attempts"][0]["dispatched"]
    service.mark_dispatched(owner, attempt)


def test_auto_copied_attempt_cannot_bypass_live_dispatch_guard(tmp_path, monkeypatch):
    service, _, repository = _service(tmp_path, monkeypatch)
    run = service.begin("auto", strategy="auto_native")
    attempt = _attempt(service, run)
    copied = replace(attempt)
    service.mark_dispatched(run, attempt)
    with pytest.raises(RouterServiceError) as error:
        service.mark_dispatched(run, copied)
    assert error.value.code == "provider_call_context_reference_mismatch"
    assert sum(row["dispatched"] for row in repository.list_chat_control_receipts("local")["attempts"]) == 1


def test_auto_relabeling_run_membership_and_context_together_is_rejected(tmp_path, monkeypatch):
    service, _, repository = _service(tmp_path, monkeypatch)
    owner = service.begin("auto", strategy="auto_native")
    other = service.begin("auto", strategy="auto_native")
    attempt = _attempt(service, owner)
    other.attempts[attempt.position] = attempt
    attempt.call_context = replace(
        attempt.call_context, parent_run_id=other.run_id,
        logical_call_id=f"{other.run_id}:chat",
    )
    with pytest.raises(RouterServiceError):
        service.mark_dispatched(other, attempt)
    assert all(not row["dispatched"] for row in repository.list_chat_control_receipts("local")["attempts"])


def test_auto_replacing_live_attempt_cannot_replay_dispatch(tmp_path, monkeypatch):
    service, _, repository = _service(tmp_path, monkeypatch)
    run = service.begin("auto", strategy="auto_native")
    attempt = _attempt(service, run)
    service.mark_dispatched(run, attempt)
    copied = replace(attempt, dispatched=False)
    run.attempts[copied.position] = copied
    with pytest.raises(RouterServiceError) as error:
        service.mark_dispatched(run, copied)
    assert error.value.code == "provider_chat_auto_duplicate_post_blocked"
    assert sum(row["dispatched"] for row in repository.list_chat_control_receipts("local")["attempts"]) == 1


def test_auto_unknown_dispatch_write_cannot_be_replayed(tmp_path, monkeypatch):
    service, _, repository = _service(tmp_path, monkeypatch)
    run = service.begin("auto", strategy="auto_native")
    attempt = _attempt(service, run)
    original = repository.mark_chat_control_attempt_dispatched
    writes = []

    def write_then_lose_confirmation(*args, **kwargs):
        writes.append(1)
        original(*args, **kwargs)
        raise RuntimeError("synthetic write confirmation lost")

    monkeypatch.setattr(repository, "mark_chat_control_attempt_dispatched", write_then_lose_confirmation)
    with pytest.raises(RuntimeError, match="synthetic write confirmation lost"):
        service.mark_dispatched(run, attempt)
    assert not attempt.dispatched
    with pytest.raises(RouterServiceError) as error:
        service.mark_dispatched(run, attempt)
    assert error.value.code == "provider_chat_auto_duplicate_post_blocked"
    assert len(writes) == 1
