"""Direct multimodal identity must not impersonate inherited Vision callers."""
from types import SimpleNamespace

import pytest

from server.model_router.call_context import ProviderCallContextError
from server.model_router.call_context_storage import ProviderContextStore, SOURCE_ENTRIES
from server.model_router.multimodal_call_context import start_direct_run, execute_inherited, start_inherited_vision_run
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.workload_control import ProviderWorkloadCallService


@pytest.mark.parametrize('entry', sorted(SOURCE_ENTRIES['multimodal_request']))
def test_direct_multimodal_entry_binds_exact_durable_source(tmp_path, entry):
    with SQLiteRouterRepository.open(tmp_path, master_key=b'x' * 32) as repo:
        service = SimpleNamespace(repository=repo, router_service=SimpleNamespace(tenant_id='local'))
        service.stable_run_id = lambda e, p: ProviderWorkloadCallService.stable_run_id(service, e, p)
        def start(e, *, parent_run_reference, origin_execution_id):
            run = service.stable_run_id(e, parent_run_reference)
            repo.claim_stable_workload_run('local', run_id=run, entry_id=e,
                policy_fingerprint='synthetic-policy', parent_run_reference=parent_run_reference,
                origin_execution_id=origin_execution_id, policy_revision=1)
            return run
        service.start_stable_run = start
        run = start_direct_run(service, entry, parent_reference='host-run:node', stable=True)
        source = ProviderContextStore(repo).require_workload_source(run,
            source_kind='multimodal_request', execution_id=run)
        assert (source['actor_kind'], source['purpose']) == ('local_anonymous', 'user_run')
        assert run == service.stable_run_id(entry, 'host-run:node')
        with repo._connect() as db:
            db.execute('DELETE FROM provider_workload_run_origins')
            db.execute('DELETE FROM provider_execution_origins')
        with pytest.raises(ProviderCallContextError, match='origin_missing'):
            start_direct_run(service, entry, parent_reference='host-run:node', stable=True)
        with repo._connect() as db:
            assert db.execute('SELECT count(*) FROM provider_execution_origins').fetchone()[0] == 0


@pytest.mark.parametrize('entry', ['rag_vision', 'workflow_interactive_vision',
    'workflow_deployment_vision', 'xpert_vision', 'xpert', 'certification'])
def test_inherited_or_admin_entry_cannot_be_fabricated_as_direct_request(entry):
    with pytest.raises(ProviderCallContextError, match='origin_missing'):
        start_direct_run(None, entry, parent_reference='host-run', stable=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('source,entry,actor,purpose', [
    ('workflow_classic', 'workflow_interactive_vision', 'local_anonymous', 'user_run'),
    ('workflow_deployment', 'workflow_deployment_vision', 'service', 'background_assistance'),
    ('xpert_chat', 'xpert_vision', 'local_anonymous', 'user_run'),
    ('xpert_app', 'xpert_vision', 'local_anonymous', 'user_run'),
])
async def test_vision_inherits_host_origin_and_scope_resets(tmp_path, source, entry, actor, purpose):
    with SQLiteRouterRepository.open(tmp_path, master_key=b'x' * 32) as repo:
        store = ProviderContextStore(repo)
        store.register_execution(execution_id='parent', task_id='task', source_kind=source)
        service = SimpleNamespace(repository=repo)
        def start(e, *, parent_run_reference, origin_execution_id):
            assert origin_execution_id == 'parent'
            assert e == entry and parent_run_reference == 'node:vision'
            return store.execution('local', origin_execution_id)
        service.start_stable_run = start
        async def operation():
            return start_inherited_vision_run(service, entry, parent_reference='node:vision', stable=True)
        result = await execute_inherited(repo, 'parent', operation)
        assert (result['actor_kind'], result['purpose']) == (actor, purpose)
        with pytest.raises(ProviderCallContextError, match='origin_missing'):
            await operation()
        with pytest.raises(ProviderCallContextError, match='origin_missing'):
            await execute_inherited(repo, 'missing-parent', operation)
