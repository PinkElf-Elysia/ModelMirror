"""C1 synthetic RAG lineage; no network and no user storage."""
import asyncio

import pytest

from server.model_router.call_context import ProviderCallContextError
from server.model_router.call_context_storage import ProviderContextStore
from server.model_router.rag_call_context import (
    bind_job, opaque_reference, operation_scope, restored_job, inherited_operation, _origin,
)
from server.model_router.repository import SQLiteRouterRepository


def test_job_source_survives_restart_without_inventing_old_origins(tmp_path):
    with SQLiteRouterRepository.open(tmp_path, master_key=b"x" * 32) as repo:
        with operation_scope("rag_request") as parent:
            job = bind_job(repo, "host-job")
        assert job["actor_reference"] == "local-anonymous"
        assert job["parent_execution_id"] == parent.execution_id
    with SQLiteRouterRepository.open(tmp_path, master_key=b"x" * 32) as repo:
        with restored_job(repo, "host-job"):
            assert _origin.get().execution_id == opaque_reference("rag-job", "host-job")
            assert _origin.get().source_kind == "rag_request"
        assert _origin.get() is None
        with pytest.raises(ProviderCallContextError, match="origin_missing"):
            with restored_job(repo, "old-job"):
                pytest.fail("must not invent an old job origin")


@pytest.mark.parametrize("source,actor,purpose", [
    ("rag_request", "local-anonymous", "user_run"),
    ("rag_service", "rag-runtime", "background_assistance"),
    ("rag_evaluation", "rag-evaluation", "evaluation"),
])
def test_job_inherits_explicit_source_not_metadata(tmp_path, source, actor, purpose):
    with SQLiteRouterRepository.open(tmp_path, master_key=b"x" * 32) as repo:
        with operation_scope(source, reference="server-run"):
            row = bind_job(repo, "job")
        assert (row["actor_reference"], row["purpose"]) == (actor, purpose)
        with repo._connect() as db:
            db.execute("UPDATE provider_execution_origins SET purpose='user_run' WHERE execution_id=?",
                       (row["execution_id"],))
        if source != "rag_request":
            with pytest.raises(ProviderCallContextError, match="actor_mismatch"):
                ProviderContextStore(repo).execution("local", row["execution_id"])


@pytest.mark.asyncio
async def test_scopes_are_task_local_and_reset_after_failure():
    async def task(source):
        with operation_scope(source) as origin:
            await asyncio.sleep(0)
            with operation_scope("rag_service") as child:
                assert child == origin
            return _origin.get().source_kind
    assert await asyncio.gather(task("rag_request"), task("rag_evaluation")) == ["rag_request", "rag_evaluation"]
    assert _origin.get() is None


@pytest.mark.parametrize("domain", ["rag", "skill"])
def test_knowledge_child_inherits_deployment_not_anonymous(tmp_path, domain):
    with SQLiteRouterRepository.open(tmp_path, master_key=b"x" * 32) as repo:
        store = ProviderContextStore(repo)
        store.register_execution(execution_id="deployment-run", task_id="deployment-task",
                                 source_kind="workflow_deployment")
        with inherited_operation(repo, "deployment-run", "node-1:kb-1", domain=domain):
            child = store.execution("local", _origin.get().execution_id)
            assert (child["actor_reference"], child["purpose"]) == ("workflow-deployment", "background_assistance")
            assert child["parent_execution_id"] == "deployment-run"
        with pytest.raises(ProviderCallContextError, match="origin_missing"):
            with inherited_operation(repo, "untrusted-client-run", "node"):
                pytest.fail("must reject missing parent")
    with pytest.raises(RuntimeError):
        with operation_scope("rag_request"):
            raise RuntimeError("synthetic")
    assert _origin.get() is None


def test_restored_job_with_unknown_model_outcome_cannot_resume(tmp_path):
    with SQLiteRouterRepository.open(tmp_path, master_key=b"x" * 32) as repo:
        row = bind_job(repo, "host-job")
        repo.claim_stable_workload_run("local", run_id="workrun-job", entry_id="rag_embedding",
            policy_fingerprint="synthetic", parent_run_reference="index-job",
            origin_execution_id=row["execution_id"], policy_revision=1)
    with SQLiteRouterRepository.open(tmp_path, master_key=b"x" * 32) as repo:
        with pytest.raises(ProviderCallContextError, match="execution_unresolved"):
            with restored_job(repo, "host-job"):
                pytest.fail("unknown outcome must never resume")


@pytest.mark.parametrize('source', ['rag_request', 'rag_service', 'rag_evaluation'])
def test_vision_only_job_retains_source_and_rejects_unknown_recovery(tmp_path, source):
    with SQLiteRouterRepository.open(tmp_path, master_key=b'x' * 32) as repo:
        with operation_scope(source, reference='vision-job-host'):
            origin = bind_job(repo, 'vision-job')
        repo.claim_stable_workload_run('local', run_id='vision-run', entry_id='rag_vision',
            policy_fingerprint='synthetic', parent_run_reference='vision-node',
            origin_execution_id=origin['execution_id'], policy_revision=1)
        with pytest.raises(ProviderCallContextError, match='execution_unresolved'):
            with restored_job(repo, 'vision-job'):
                pytest.fail('must not repeat a pending Vision stage')
