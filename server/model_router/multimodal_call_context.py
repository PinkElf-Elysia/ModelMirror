"""Host-selected direct multimodal request origins; not an HTTP DTO."""
import uuid
from contextvars import ContextVar

from .call_context import ProviderCallContextError
from .call_context_storage import ProviderContextStore, SOURCE_ENTRIES

_vision_parent: ContextVar[str | None] = ContextVar('provider_vision_parent', default=None)


async def execute_inherited(repository, execution_id, operation, **kwargs):
    ProviderContextStore(repository).execution('local', execution_id)
    token = _vision_parent.set(execution_id)
    try:
        return await operation(**kwargs)
    finally:
        _vision_parent.reset(token)


def start_inherited_vision_run(call_service, entry_id, *, parent_reference, stable):
    execution_id = _vision_parent.get()
    if execution_id is None:
        raise ProviderCallContextError('provider_call_context_origin_missing')
    source = ProviderContextStore(call_service.repository).execution('local', execution_id)
    if entry_id not in SOURCE_ENTRIES[source['source_kind']]:
        raise ProviderCallContextError('provider_call_context_entry_mismatch')
    parent = parent_reference if stable else 'vision-operation:' + uuid.uuid4().hex
    return call_service.start_stable_run(entry_id, parent_run_reference=parent,
                                        origin_execution_id=execution_id)


def start_direct_run(call_service, entry_id, *, parent_reference, stable):
    # These existing adapters are direct request boundaries. Vision invoked by
    # RAG/Workflow/Xpert deliberately has no anonymous fallback here.
    if entry_id not in SOURCE_ENTRIES['multimodal_request']:
        raise ProviderCallContextError('provider_call_context_origin_missing')
    parent = parent_reference if stable else 'multimodal-request:' + uuid.uuid4().hex
    execution_id = call_service.stable_run_id(entry_id, parent)
    ProviderContextStore(call_service.repository).register_execution(
        execution_id=execution_id, task_id=execution_id, source_kind='multimodal_request',
    )
    return call_service.start_stable_run(entry_id, parent_run_reference=parent,
                                        origin_execution_id=execution_id)


def require_video_job_origin(call_service, row):
    expected_run = call_service.stable_run_id('video_generation', 'video-job:' + str(row['id']))
    if row.get('workload_run_id') != expected_run:
        raise ProviderCallContextError('provider_call_context_reference_mismatch')
    ProviderContextStore(call_service.repository).require_dispatched_run(expected_run,
        entry_id='video_generation', source_kind='multimodal_request', execution_id=expected_run)
    call = call_service.repository.get_workload_call('local', str(row.get('workload_call_id') or ''))
    if call is None or call.get('run_id') != expected_run or any(
        call.get(key) != row.get(key) for key in ('connection_id', 'requested_model', 'adapter_contract')
    ):
        raise ProviderCallContextError('provider_call_context_reference_mismatch')
