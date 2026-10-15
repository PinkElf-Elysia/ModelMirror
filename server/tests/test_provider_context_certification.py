"""Synthetic certification origin, atomic creation, immutable GET recovery."""
import json

import pytest

from server.model_router.certification_call_context import certification_origin
from server.model_router.call_context import ProviderCallContextError, require_provider_call_context
from server.model_router.repository import SQLiteRouterRepository, RouterRepositoryError
from server.model_router.schemas import RouterConnectionCreate


def _claim(repo, source):
    connection = repo.create_connection('local', RouterConnectionCreate(
        name='synthetic', kind='openrouter', base_url='https://provider.example/v1',
        api_key='synthetic-only', scopes=['chat']))
    args = dict(certification_id='cert-source', connection_id=connection.id,
        connection_fingerprint=repo.connection_config_fingerprint('local', connection.id),
        contract_version='synthetic-v1', requested_model='provider/model', idempotency_key_hash='opaque-key-hash')
    if source == 'provider_chat':
        claim = repo.claim_chat_certification
    else:
        claim = repo.claim_workload_certification
        args.update(execution_shape='chat_text_unary', profile={}, profile_fingerprint='profile-hash')
    row, created = claim('local', **args)
    assert created
    return claim, args, row


@pytest.mark.parametrize('source', ['provider_chat', 'provider_workload'])
def test_certification_source_is_atomic_admin_fact_not_runtime_policy(tmp_path, source):
    with SQLiteRouterRepository.open(tmp_path, master_key=b'x' * 32) as repo:
        claim, args, row = _claim(repo, source)
        context = certification_origin(repo, source, row['id'])
        assert (context.actor_kind, context.purpose, context.entry_id, context.policy_revision) == (
            'local_admin', 'certification', 'certification', None)
        assert require_provider_call_context(context, actor_kind='local_admin', actor_reference='local-admin',
            purpose='certification', entry_id='certification', parent_run_id=row['id'],
            logical_call_id=args['idempotency_key_hash'], attempt_id=row['id'], policy_revision=None) == context
        with pytest.raises(ProviderCallContextError, match='policy_changed'):
            require_provider_call_context(context, actor_kind='local_admin', actor_reference='local-admin',
                purpose='certification', entry_id='certification', parent_run_id=row['id'],
                logical_call_id=args['idempotency_key_hash'], attempt_id=row['id'], policy_revision=1)
        assert claim('local', **args)[1] is False
        with repo._connect() as db:
            assert db.execute('SELECT count(*) FROM provider_certification_call_contexts').fetchone()[0] == 1
            payload = db.execute('SELECT context_json FROM provider_certification_call_contexts').fetchone()[0]
            assert 'synthetic-only' not in payload and 'https://' not in payload
    with SQLiteRouterRepository.open(tmp_path, master_key=b'x' * 32) as repo:
        assert certification_origin(repo, source, row['id']) == context
        if source == 'provider_workload':
            assert repo.resume_workload_certification('local', row['id'])['status'] == 'running'


@pytest.mark.parametrize('damage', ['missing', 'actor', 'model'])
def test_certification_resume_does_not_invent_or_accept_forged_origin(tmp_path, damage):
    with SQLiteRouterRepository.open(tmp_path, master_key=b'x' * 32) as repo:
        _, _, row = _claim(repo, 'provider_workload')
    with SQLiteRouterRepository.open(tmp_path, master_key=b'x' * 32) as repo:
        with repo._connect() as db:
            if damage == 'missing':
                db.execute('DELETE FROM provider_certification_call_contexts')
            elif damage == 'model':
                db.execute("UPDATE provider_workload_certifications SET requested_model='other/model'")
            else:
                raw = json.loads(db.execute('SELECT context_json FROM provider_certification_call_contexts').fetchone()[0])
                raw['actor_reference'] = 'forged-admin'
                db.execute('UPDATE provider_certification_call_contexts SET context_json=?', (json.dumps(raw),))
        with pytest.raises(RouterRepositoryError, match='provider_call_context'):
            repo.resume_workload_certification('local', row['id'])
        assert repo.get_workload_certification('local', row['id'])['status'] == 'uncertain'


def test_certification_context_failure_rolls_back_new_certification(tmp_path, monkeypatch):
    import server.model_router.repository as module
    with SQLiteRouterRepository.open(tmp_path, master_key=b'x' * 32) as repo:
        def reject(*args):
            raise ProviderCallContextError('provider_call_context_storage_invalid')
        monkeypatch.setattr(module, 'save_certification_origin', reject)
        with pytest.raises(RouterRepositoryError, match='storage_invalid'):
            _claim(repo, 'provider_workload')
        assert repo.list_workload_certifications('local') == []


@pytest.mark.asyncio
async def test_chat_certification_origin_deleted_during_dns_blocks_post(tmp_path, monkeypatch):
    from server.tests.test_provider_chat_certification import _service, _handler
    service, connection_id, requests = _service(tmp_path, _handler)
    authorize = service.transport.authorize_managed_target
    async def corrupt(target):
        approved = await authorize(target)
        with service.repository._connect() as db:
            db.execute('DELETE FROM provider_certification_call_contexts')
        return approved
    monkeypatch.setattr(service.transport, 'authorize_managed_target', corrupt)
    try:
        result = await service.run(connection_id, model_id='provider/model',
            acknowledge_billed_call=True, idempotency_key='cert-origin')
        assert result.status == 'failed'
        assert result.error_code == 'provider_call_context_origin_missing'
        assert not [r for r in requests if r.method == 'POST']
    finally:
        service.repository.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['origin', 'model', 'connection'])
async def test_batch_certification_recovery_missing_origin_has_zero_network(tmp_path, monkeypatch, damage):
    from server.tests.test_managed_openrouter_batch import _configured_gateway
    from server.tests.test_provider_context_batch import _handler
    from server.model_router.workload_control import ProviderWorkloadCertificationService
    requests = []
    def handler(request):
        requests.append(request.method)
        return _handler(request)
    gateway, repo = await _configured_gateway(tmp_path, monkeypatch, handler)
    try:
        with repo._connect() as db:
            db.execute("UPDATE provider_workload_certifications SET status='uncertain'")
            db.execute("UPDATE provider_batch_jobs SET status='in_progress' WHERE purpose='certification'")
            if damage == 'origin':
                db.execute('DELETE FROM provider_certification_call_contexts')
            elif damage == 'model':
                db.execute("UPDATE provider_batch_jobs SET model_id='other/model' WHERE purpose='certification'")
            else:
                db.execute("UPDATE provider_batch_jobs SET connection_id='other-connection' WHERE purpose='certification'")
        requests.clear()
        certs = ProviderWorkloadCertificationService(gateway.router_service, client_factory=gateway._client_factory)
        assert await certs.resume_pending_batch_certifications() == 0
        assert requests == []
        assert repo.list_workload_certifications('local')[0]['status'] == 'uncertain'
    finally:
        repo.close()


@pytest.mark.asyncio
async def test_realtime_confirmation_missing_origin_blocks_qualification_not_cleanup(tmp_path, monkeypatch):
    from server.tests.test_multimodal_realtime import managed_realtime_stack, managed_realtime_transport, SDP_OFFER
    from server.model_router.schemas import ProviderRealtimeCertificationSessionRequest, ProviderRealtimeCertificationCompleteRequest
    from server.model_router.service import RouterServiceError
    monkeypatch.setenv('MULTIMODAL_REALTIME_VOICE_ENABLED', 'true')
    requests = []
    service, repo, connection_id = managed_realtime_stack(tmp_path, managed_realtime_transport(requests))
    try:
        session = await service.begin_certification(connection_id,
            ProviderRealtimeCertificationSessionRequest(model_id='gpt-realtime-2.1-mini',
                adapter_contract='openai_realtime_sdp_v1', offer_sdp=SDP_OFFER, acknowledge_billed_call=True),
            idempotency_key='context-realtime')
        with repo._connect() as db:
            db.execute('DELETE FROM provider_certification_call_contexts')
        requests.clear()
        with pytest.raises(RouterServiceError) as caught:
            await service.complete_certification(session.certification_id,
                ProviderRealtimeCertificationCompleteRequest(media_observed=True, hangup_observed=True))
        assert caught.value.code == 'provider_call_context_origin_missing'
        assert requests == []
        assert repo.get_workload_certification('local', session.certification_id)['status'] != 'passed'
        await service.shutdown()
        assert requests and all(request.url.path.endswith('/hangup') for request in requests)
    finally:
        await service.shutdown()
        repo.close()
