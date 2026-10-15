"""Batch original-source persistence and GET-only recovery, no real Provider."""
import json

import httpx
import pytest

from server.model_router.call_context import ProviderCallContextError
from server.model_router.call_context_storage import ProviderContextStore
from server.model_router.service import RouterServiceError
from server.tests.test_managed_openrouter_batch import _configured_gateway, _submission


def _handler(request):
    if request.url.path.endswith('/models'):
        return httpx.Response(200, json={'data': [{'id': 'provider/model'}]})
    if request.method == 'POST':
        cert = json.loads(request.content)['requests'][0]['custom_id'] == 'modelmirror-certification'
        return httpx.Response(202, json={'id': 'batch_certification' if cert else 'batch_runtime',
                                       'status': 'in_progress'})
    cert = request.url.path.endswith('/batch_certification')
    return httpx.Response(200, json={'id': 'batch_certification' if cert else 'batch_runtime',
        'status': 'completed', 'request_counts': {'total': 1, 'completed': 1, 'failed': 0},
        'results': [{'custom_id': 'modelmirror-certification' if cert else 'request-1',
                     'response': {'status_code': 200, 'body': {'model': 'provider/model'}}}]})


@pytest.mark.asyncio
async def test_batch_context_is_durable_and_not_in_public_result(tmp_path, monkeypatch):
    requests = []
    def handler(request):
        requests.append(request.method)
        return _handler(request)
    gateway, repo = await _configured_gateway(tmp_path, monkeypatch, handler)
    try:
        requests.clear()
        _, result = await gateway.submit(_submission(), idempotency_key='source-key')
        call = repo.list_workload_receipts('local', entry_id='openrouter_batch')['calls'][0]
        context = ProviderContextStore(repo).read('local', call['id'])
        assert (context.actor_kind, context.purpose, context.entry_id) == (
            'local_anonymous', 'user_run', 'openrouter_batch')
        assert await gateway.resume_pending_runtime_jobs() == 1
        assert requests == ['POST', 'GET']
        assert ProviderContextStore(repo).read('local', call['id']) == context
        assert 'actor_reference' not in json.dumps(result)
        with repo._connect() as db:
            stored = str([tuple(row) for row in db.execute('SELECT * FROM provider_call_contexts')])
        assert 'private-batch-input' not in stored and 'source-key' not in stored
    finally:
        repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['origin', 'call', 'actor', 'job_run', 'model', 'connection', 'fingerprint'])
async def test_batch_poll_missing_or_forged_source_has_zero_network(tmp_path, monkeypatch, damage):
    requests = []
    def handler(request):
        requests.append(request.method)
        return _handler(request)
    gateway, repo = await _configured_gateway(tmp_path, monkeypatch, handler)
    try:
        _, result = await gateway.submit(_submission(), idempotency_key='source-key')
        with repo._connect() as db:
            if damage == 'origin':
                db.execute('DELETE FROM provider_workload_run_origins')
            elif damage == 'call':
                db.execute('DELETE FROM provider_call_contexts')
            elif damage == 'actor':
                db.execute("UPDATE provider_execution_origins SET actor_kind='service', actor_reference='forged'")
            elif damage == 'model':
                db.execute("UPDATE provider_batch_jobs SET model_id='other/model' WHERE purpose='runtime'")
            elif damage == 'connection':
                db.execute("UPDATE provider_batch_jobs SET connection_id='other-connection' WHERE purpose='runtime'")
            elif damage == 'fingerprint':
                db.execute("UPDATE provider_batch_jobs SET connection_fingerprint='changed' WHERE purpose='runtime'")
            else:
                db.execute("UPDATE provider_batch_jobs SET workload_run_id='workrun_other' WHERE purpose='runtime'")
        requests.clear()
        assert await gateway.resume_pending_runtime_jobs() == 0
        with pytest.raises(RouterServiceError) as caught:
            await gateway.poll(result['id'])
        assert caught.value.code.startswith('provider_call_context_')
        assert requests == []
    finally:
        repo.close()


@pytest.mark.asyncio
async def test_batch_missing_origin_cannot_be_prepared_as_pending_adapter(tmp_path, monkeypatch):
    gateway, repo = await _configured_gateway(tmp_path, monkeypatch, _handler)
    try:
        run = gateway.call_service.start_stable_run('openrouter_batch', parent_run_reference='old-source-less-run')
        with pytest.raises(ProviderCallContextError, match='origin_missing'):
            await gateway.call_service.prepare_call(run_id=run, entry_id='openrouter_batch',
                execution_shape='openrouter_batch_chat', model_id='provider/model',
                logical_call_key='job', call_sequence=1)
    finally:
        repo.close()
