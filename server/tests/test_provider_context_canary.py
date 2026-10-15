"""Canary provenance uses real isolated SQLite; no external requests."""
import json
from dataclasses import replace

import pytest

from server.model_router.call_context import ProviderCallContextError
from server.model_router.call_context_storage import ProviderContextStore
from server.model_router.chat_canary import ProviderChatCanaryService
from server.model_router.repository import SQLiteRouterRepository, RouterRepositoryError
from server.model_router.service import ModelRouterService, RouterServiceError
from server.tests.test_provider_chat_canary_repository import _repository, _pass_certification


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_MIRROR_PROVIDER_CHAT_CANARY_ENABLED", "true")
    repo, connection_id = _repository(tmp_path)
    _pass_certification(repo, connection_id)
    service = ProviderChatCanaryService(ModelRouterService(repo))
    service.update_policy(connection_id, enabled=True)
    yield repo, service, connection_id
    repo.close()


def test_current_policy_revision_and_private_context(configured):
    repo, service, connection_id = configured
    service.update_policy(connection_id, enabled=True)
    dispatch = service.begin_run("provider/model", session_id="synthetic-session")
    context = ProviderContextStore(repo).read("local", dispatch.run_id)
    assert context.policy_revision == 2
    assert context.parent_run_id == service.session_hash("synthetic-session")
    assert (context.actor_kind, context.purpose, context.entry_id) == ("local_anonymous", "user_run", "chat_canary")
    assert context.attempt_id == context.logical_call_id == dispatch.run_id
    service.mark_dispatched(dispatch.run_id)
    with pytest.raises(RouterServiceError, match="可信来源"):
        service.mark_dispatched(dispatch.run_id)
    public = service.public_status("provider/model").model_dump_json()
    assert "actor_reference" not in public and "synthetic-session" not in public
    with repo._connect() as db:
        payload = db.execute("SELECT context_json FROM provider_call_contexts").fetchone()[0]
        assert "secret-key" not in payload and "https://" not in payload


@pytest.mark.parametrize("change", ["save", "disable", "legacy_write", "delete", "purpose"])
def test_drift_or_context_tampering_blocks_before_dispatch(configured, change):
    repo, service, connection_id = configured
    dispatch = service.begin_run("provider/model", session_id="session")
    if change in {"save", "disable"}:
        service.update_policy(connection_id, enabled=change != "disable")
    else:
        with repo._connect() as db:
            if change == "legacy_write":
                db.execute("UPDATE provider_chat_canary_policies SET updated_at='changed'")
            elif change == "delete":
                db.execute("DELETE FROM provider_call_contexts")
            else:
                row = db.execute("SELECT context_json FROM provider_call_contexts").fetchone()
                value = json.loads(row[0])
                value["purpose"] = "evaluation"
                db.execute("UPDATE provider_call_contexts SET context_json=?", (json.dumps(value),))
    with pytest.raises(RouterServiceError):
        service.mark_dispatched(dispatch.run_id)
    assert repo.list_chat_canary_runs("local")[0]["dispatched"] == 0


def test_wrong_origin_and_missing_context_are_not_dispatch_authority(configured):
    repo, service, _ = configured
    dispatch = service.begin_run("provider/model", session_id="session")
    saved = ProviderContextStore(repo).read("local", dispatch.run_id)
    with pytest.raises(ProviderCallContextError):
        repo.mark_chat_canary_dispatched("local", dispatch.run_id, call_context=replace(saved, purpose="smoke"))
    with pytest.raises(ProviderCallContextError):
        repo.mark_chat_canary_dispatched("local", dispatch.run_id)
    assert repo.list_chat_canary_runs("local")[0]["dispatched"] == 0


def test_claim_race_rolls_back_context_and_run(configured, monkeypatch):
    repo, service, connection_id = configured
    original = repo.claim_chat_canary_run
    def race(*args, **kwargs):
        repo.save_chat_canary_policy("local", connection_id=connection_id, enabled=True)
        return original(*args, **kwargs)
    monkeypatch.setattr(repo, "claim_chat_canary_run", race)
    with pytest.raises((ProviderCallContextError, RouterServiceError)):
        service.begin_run("provider/model", session_id="session")
    assert repo.list_chat_canary_runs("local") == []
    with repo._connect() as db:
        assert db.execute("SELECT count(*) FROM provider_call_contexts").fetchone()[0] == 0


def test_dispatch_write_acknowledgement_loss_never_replays(configured, monkeypatch):
    repo, service, _ = configured
    dispatch = service.begin_run("provider/model", session_id="session")
    original = repo.mark_chat_canary_dispatched
    writes = []
    def lost_ack(*args, **kwargs):
        writes.append(1)
        original(*args, **kwargs)
        raise RouterRepositoryError("synthetic_ack_lost")
    monkeypatch.setattr(repo, "mark_chat_canary_dispatched", lost_ack)
    with pytest.raises(RouterServiceError):
        service.mark_dispatched(dispatch.run_id)
    with pytest.raises(RouterServiceError):
        service.mark_dispatched(dispatch.run_id)
    assert writes == [1]
    assert repo.list_chat_canary_runs("local")[0]["dispatched"] == 1


@pytest.mark.parametrize("dispatched", [False, True])
def test_restart_retains_origin_but_does_not_replay(configured, tmp_path, dispatched):
    repo, service, _ = configured
    dispatch = service.begin_run("provider/model", session_id="session")
    saved = ProviderContextStore(repo).read("local", dispatch.run_id)
    if dispatched:
        service.mark_dispatched(dispatch.run_id)
    repo.close()
    with SQLiteRouterRepository.open(tmp_path, master_key=b"x" * 32) as restored:
        assert ProviderContextStore(restored).read("local", dispatch.run_id) == saved
        assert restored.get_chat_canary_policy("local")["revision"] == saved.policy_revision
        new_service = ProviderChatCanaryService(ModelRouterService(restored))
        with pytest.raises(RouterServiceError):
            new_service.mark_dispatched(dispatch.run_id)
        with pytest.raises(RouterRepositoryError):
            restored.mark_chat_canary_dispatched("local", dispatch.run_id, call_context=saved)
        assert restored.list_chat_canary_runs("local")[0]["status"] == "uncertain"


def test_old_policy_requires_explicit_save_without_rewriting_evidence(configured):
    repo, service, connection_id = configured
    with repo._connect() as db:
        db.execute("DELETE FROM provider_canary_policy_revisions")
    assert repo.get_chat_canary_policy("local")["revision"] is None
    assert not service.public_status("provider/model").available
    with pytest.raises(RouterServiceError):
        service.begin_run("provider/model", session_id="session")
    assert repo.list_chat_canary_runs("local") == []
    service.update_policy(connection_id, enabled=True)
    assert service.begin_run("provider/model", session_id="session").run_id
