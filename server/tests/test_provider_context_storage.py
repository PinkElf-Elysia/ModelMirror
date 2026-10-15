"""Synthetic v20 provenance, without Provider calls or user databases."""
import sqlite3
from dataclasses import replace

import pytest

from server.model_router.call_context import ProviderCallContextError
from server.model_router.call_context_storage import (
    CONTEXT_TABLES, ProviderContextStore, bind_workload_origin, create_context_schema,
    read_context, save_context, workload_context, verify_context, SOURCE_ENTRIES,
)
from server.model_router.repository import SQLiteRouterRepository


@pytest.mark.parametrize("entry", sorted(set().union(*SOURCE_ENTRIES.values())))
def test_every_managed_entry_requires_persisted_origin(tmp_path, entry):
    with SQLiteRouterRepository.open(tmp_path, master_key=b"s" * 32) as repo:
        # Historical rows stay readable; absence is never authority to dispatch.
        repo.claim_workload_run("local", run_id="historical", entry_id=entry,
                                policy_fingerprint="synthetic")
        assert repo.get_workload_run("local", "historical")["entry_id"] == entry
        with repo._connect() as db, pytest.raises(ProviderCallContextError, match="origin_missing"):
            workload_context(db, "local", "historical", "call", "logical", 1)


def test_absent_saved_and_supplied_context_never_authorizes_dispatch(tmp_path):
    with SQLiteRouterRepository.open(tmp_path, master_key=b"s" * 32) as repo:
        with repo._connect() as db, pytest.raises(ProviderCallContextError, match="origin_missing"):
            verify_context(db, "local", "old-call", None)


def test_source_registry_covers_all_current_workloads():
    from server.model_router.workload_control import ENTRY_ALLOWED_SHAPES
    assert set().union(*SOURCE_ENTRIES.values()) == set(ENTRY_ALLOWED_SHAPES)


@pytest.mark.parametrize("entry,source", [
    ("route_agent", "route_agent_request"), ("team_chat", "team_chat_request"), ("fusion", "fusion_request"),
])
def test_request_origin_is_atomic_and_explicit(tmp_path, entry, source):
    with SQLiteRouterRepository.open(tmp_path, master_key=b"s" * 32) as repo:
        repo.claim_workload_run("local", run_id="request-run", entry_id=entry,
            policy_fingerprint="synthetic", origin_source=source, policy_revision=8)
        origin = ProviderContextStore(repo).execution("local", "request-run")
        assert (origin["source_kind"], origin["actor_reference"], origin["purpose"]) == (source, "local-anonymous", "user_run")
        with repo._connect() as db:
            assert workload_context(db, "local", "request-run", "call", "logical", 8).entry_id == entry
        with pytest.raises(ProviderCallContextError):
            repo.claim_workload_run("local", run_id="rejected", entry_id="xpert",
                policy_fingerprint="synthetic", origin_source=source, policy_revision=8)
        with repo._connect() as db:
            assert not db.execute("SELECT 1 FROM provider_execution_origins WHERE execution_id='rejected'").fetchone()
            assert not db.execute("SELECT 1 FROM provider_workload_runs WHERE id='rejected'").fetchone()


def test_deployment_recovery_preserves_reference(tmp_path):
    repo = SQLiteRouterRepository.open(tmp_path, master_key=b"x" * 32)
    try:
        store = ProviderContextStore(repo)
        origin = store.register_execution(execution_id="before", task_id="task",
            source_kind="workflow_deployment", execution_reference="deployment-execution")
        assert origin["actor_kind"] == "service"
        restored = store.register_execution(execution_id="after", task_id="task",
            source_kind="workflow_deployment", execution_reference="deployment-execution", resume_from="before")
        assert restored["execution_reference"] == "deployment-execution"
        with pytest.raises(ProviderCallContextError, match="reference_mismatch"):
            store.register_execution(execution_id="wrong", task_id="task",
                source_kind="workflow_deployment", execution_reference="changed", resume_from="before")
    finally:
        repo.close()


def test_child_identity_survives_parent_alias_and_rejects_new_execution(tmp_path):
    with SQLiteRouterRepository.open(tmp_path, master_key=b"x" * 32) as repo:
        store = ProviderContextStore(repo)
        store.register_execution(execution_id="parent-before", task_id="parent-task", source_kind="xpert_chat")
        child = store.child_task_reference(parent_execution_id="parent-before", source_kind="xpert_chat",
            logical_parts=("handoff", "handoff-1"))
        store.register_execution(execution_id="parent-after", task_id="parent-task", source_kind="xpert_chat", resume_from="parent-before")
        assert store.child_task_reference(parent_execution_id="parent-after", source_kind="xpert_chat",
            logical_parts=("handoff", "handoff-1")) == child
        store.register_execution(execution_id="child-before", task_id=child, source_kind="xpert_chat", parent_execution_id="parent-before")
        with pytest.raises(ProviderCallContextError, match="execution_replay_blocked"):
            store.register_execution(execution_id="child-replayed", task_id=child, source_kind="xpert_chat", parent_execution_id="parent-after")
        assert store.child_task_reference(parent_execution_id="parent-after", source_kind="xpert_chat",
            logical_parts=("handoff", "handoff-2")) != child

def test_v19_upgrade_backup_and_no_historical_invention(tmp_path):
    with SQLiteRouterRepository.open(tmp_path, master_key=b"s" * 32):
        pass
    with sqlite3.connect(tmp_path / "router.sqlite3") as db:
        for name in reversed(CONTEXT_TABLES):
            db.execute(f"DROP TABLE {name}")
        db.execute("PRAGMA user_version=19")
        db.execute("INSERT INTO router_metadata VALUES ('synthetic', 'kept', 'now')")
    with SQLiteRouterRepository.open(tmp_path, master_key=b"s" * 32) as repository:
        with repository._connect() as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 20
            for name in CONTEXT_TABLES:
                assert db.execute(f"SELECT count(*) FROM {name}").fetchone()[0] == 0
            assert db.execute("SELECT value FROM router_metadata WHERE key='synthetic'").fetchone()[0] == "kept"
    backups = list(tmp_path.glob("router.sqlite3.backup-*"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 19
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_context_schema_transaction_and_tenant_keys():
    with sqlite3.connect(":memory:") as db:
        create_context_schema(db)
        for name in CONTEXT_TABLES:
            assert next(row for row in db.execute(f"PRAGMA table_info({name})") if row[1] == "tenant_id")[5] > 0
        db.rollback()
        assert not db.execute("SELECT name FROM sqlite_master WHERE name LIKE 'provider_%'").fetchall()


def test_origin_inheritance_recovery_and_context_immutability(tmp_path):
    with SQLiteRouterRepository.open(tmp_path, master_key=b"s" * 32) as repository:
        store = ProviderContextStore(repository)
        root = store.register_execution(execution_id="root", task_id="task", source_kind="xpert_chat")
        child = store.register_execution(execution_id="child", task_id="child-task", source_kind="xpert_chat", parent_execution_id="root")
        assert child["actor_reference"] == root["actor_reference"] == "local-anonymous"
        with repository._connect() as db:
            bind_workload_origin(db, "local", "workrun", "child", "xpert", 5)
            context = workload_context(db, "local", "workrun", "call", "logical-hash", 5)
            save_context(db, context, owner_kind="workload")
        with repository._connect() as db, pytest.raises(ProviderCallContextError, match="conflict"):
            save_context(db, replace(context, purpose="evaluation"), owner_kind="workload")
    with SQLiteRouterRepository.open(tmp_path, master_key=b"s" * 32) as repository:
        store = ProviderContextStore(repository)
        assert store.read("local", "call") == context
        recovered = store.register_execution(execution_id="resumed", task_id="child-task", source_kind="xpert_chat", resume_from="child")
        assert recovered["parent_execution_id"] == "root"
        assert recovered["purpose"] == "user_run"
        with pytest.raises(ProviderCallContextError, match="reference_mismatch"):
            store.register_execution(execution_id="wrong", task_id="other-task", source_kind="xpert_chat", resume_from="child")
        with pytest.raises(ProviderCallContextError, match="tenant_invalid"):
            store.read("other", "call")


@pytest.mark.parametrize("operation", ["resume", "inherit"])
def test_missing_history_never_mints_anonymous_identity(tmp_path, operation):
    with SQLiteRouterRepository.open(tmp_path, master_key=b"s" * 32) as repository:
        store = ProviderContextStore(repository)
        kwargs = {"resume_from" if operation == "resume" else "parent_execution_id": "old-missing"}
        with pytest.raises(ProviderCallContextError, match="origin_missing"):
            store.register_execution(execution_id="new", task_id="task", source_kind="xpert_chat", **kwargs)
        with repository._connect() as db:
            assert db.execute("SELECT count(*) FROM provider_execution_origins").fetchone()[0] == 0


def test_entry_substitution_and_revision_drift_fail_closed(tmp_path):
    with SQLiteRouterRepository.open(tmp_path, master_key=b"s" * 32) as repository:
        store = ProviderContextStore(repository)
        store.register_execution(execution_id="root", task_id="task", source_kind="xpert_app")
        with repository._connect() as db:
            with pytest.raises(ProviderCallContextError, match="entry_mismatch"):
                bind_workload_origin(db, "local", "wrong", "root", "xpert", 4)
            bind_workload_origin(db, "local", "run", "root", "xpert_app", 4)
            with pytest.raises(ProviderCallContextError, match="policy_changed"):
                workload_context(db, "local", "run", "call", "logical", 5)
        with pytest.raises(ProviderCallContextError, match="entry_mismatch"):
            store.register_execution(execution_id="child", task_id="child-task", source_kind="xpert_chat", parent_execution_id="root")
