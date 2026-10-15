"""Immutable admin certification source, not a runtime policy or a renewal."""
from dataclasses import asdict
import hashlib
import json

from .call_context import ProviderCallContext, ProviderCallContextError

TABLES = {'provider_chat': 'provider_chat_certifications',
          'provider_workload': 'provider_workload_certifications'}


def _facts(db, tenant_id, source, certification_id):
    if tenant_id != 'local' or source not in TABLES:
        raise ProviderCallContextError('provider_call_context_tenant_invalid')
    row = db.execute(f'SELECT * FROM {TABLES[source]} WHERE tenant_id=? AND id=?',
                     (tenant_id, certification_id)).fetchone()
    if row is None:
        raise ProviderCallContextError('provider_call_context_origin_missing')
    row = dict(row)
    # Only existing identity/configuration references, never content or results.
    identity = {key: row.get(key) for key in (
        'id', 'tenant_id', 'connection_id', 'connection_fingerprint', 'contract_version',
        'capability', 'execution_shape', 'requested_model', 'profile_fingerprint',
        'adapter_contract', 'protocol_version', 'idempotency_key_hash')}
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    context = ProviderCallContext(tenant_id=tenant_id, actor_kind='local_admin',
        actor_reference='local-admin', entry_id='certification', purpose='certification',
        parent_run_id=certification_id, logical_call_id=row['idempotency_key_hash'],
        attempt_id=certification_id, policy_revision=None)
    return fingerprint, context


def save_certification_origin(db, tenant_id, source, certification_id):
    """Only called inside the transaction that inserts a new certification."""
    fingerprint, context = _facts(db, tenant_id, source, certification_id)
    db.execute('INSERT INTO provider_certification_call_contexts VALUES (?, ?, ?, ?, ?)',
               (tenant_id, source, certification_id, fingerprint,
                json.dumps(asdict(context), sort_keys=True, separators=(',', ':'))))


def require_certification_origin(db, tenant_id, source, certification_id):
    fingerprint, expected = _facts(db, tenant_id, source, certification_id)
    row = db.execute('SELECT identity_hash, context_json FROM provider_certification_call_contexts '
                     'WHERE tenant_id=? AND source=? AND certification_id=?',
                     (tenant_id, source, certification_id)).fetchone()
    if row is None:
        raise ProviderCallContextError('provider_call_context_origin_missing')
    try:
        saved = ProviderCallContext(**json.loads(row['context_json']))
    except (TypeError, ValueError):
        raise ProviderCallContextError('provider_call_context_storage_invalid') from None
    if row['identity_hash'] != fingerprint or saved != expected:
        raise ProviderCallContextError('provider_call_context_reference_mismatch')
    if source == 'provider_workload':
        cert = db.execute('SELECT * FROM provider_workload_certifications WHERE tenant_id=? AND id=?',
                          (tenant_id, certification_id)).fetchone()
        session = db.execute('SELECT * FROM provider_multimodal_certification_sessions '
                             'WHERE tenant_id=? AND certification_id=?', (tenant_id, certification_id)).fetchone()
        if session is not None and any(session[key] != cert[key] for key in (
            'connection_id', 'requested_model', 'execution_shape', 'adapter_contract',
            'protocol_version', 'idempotency_key_hash',
        )):
            raise ProviderCallContextError('provider_call_context_reference_mismatch')
        jobs = db.execute("SELECT * FROM provider_batch_jobs WHERE tenant_id=? AND certification_id=? AND purpose='certification'",
                          (tenant_id, certification_id)).fetchall()
        endpoint = {'openrouter_batch_chat': '/v1/chat/completions',
                    'openrouter_batch_embeddings': '/v1/embeddings'}.get(cert['execution_shape'])
        if len(jobs) > 1 or any(
            job['connection_id'] != cert['connection_id']
            or job['connection_fingerprint'] != cert['connection_fingerprint']
            or job['model_id'] != cert['requested_model'] or job['endpoint'] != endpoint
            or job['idempotency_key_hash'] != cert['idempotency_key_hash'] for job in jobs
        ):
            raise ProviderCallContextError('provider_call_context_reference_mismatch')
    return saved


def certification_origin(repository, source, certification_id):
    with repository._lock, repository._connect() as db:
        return require_certification_origin(db, 'local', source, certification_id)
