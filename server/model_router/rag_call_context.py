"""Host-only RAG/Skill provenance. Never deserialize an HTTP payload here.

Scopes carry only server IDs; repositories remain owned by the existing Router.
An explicit service boundary is used for internal callers, not anonymous-user
impersonation or request=None legacy bypass. Durable jobs restore saved facts.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
import hashlib
import uuid

from .call_context import ProviderCallContextError
from .call_context_storage import ProviderContextStore, SOURCE_ENTRIES
from .service import RouterServiceError


SOURCES = frozenset({"rag_request", "rag_service", "rag_evaluation", "rag_inherited",
                     "skill_request", "skill_service", "skill_evaluation", "skill_inherited"})


@dataclass(frozen=True, slots=True)
class OperationOrigin:
    execution_id: str
    source_kind: str


_origin: ContextVar[OperationOrigin | None] = ContextVar("rag_provider_origin", default=None)


def opaque_reference(domain: str, reference: str) -> str:
    # Callers supply host run/job/slot IDs, never query, document or tool input.
    if type(reference) is not str or not reference or len(reference) > 2048:
        raise ProviderCallContextError("provider_call_context_reference_invalid")
    return domain + "-" + hashlib.sha256(reference.encode()).hexdigest()


@contextmanager
def operation_scope(source_kind: str, *, reference: str | None = None, inherit: bool = True):
    if source_kind not in SOURCES:
        raise ProviderCallContextError("provider_call_context_entry_invalid")
    current = _origin.get()
    selected = current if inherit and current is not None else OperationOrigin(
        opaque_reference("rag-origin", uuid.uuid4().hex if reference is None else reference), source_kind)
    token = _origin.set(selected)
    try:
        yield selected
    finally:
        _origin.reset(token)


def scoped_operation(source_kind: str):
    """Group phases of one internal async operation without changing its DTO."""
    def decorate(function):
        @wraps(function)
        async def wrapped(*args, **kwargs):
            with operation_scope(source_kind):
                return await function(*args, **kwargs)
        return wrapped
    return decorate


def scoped_job(function):
    @wraps(function)
    async def wrapped(service, job_id, *args, **kwargs):
        with service._provider_job_scope(job_id):
            return await function(service, job_id, *args, **kwargs)
    return wrapped


def scoped_evaluation_run(function):
    @wraps(function)
    async def wrapped(service, run, *args, **kwargs):
        # The durable executor supplies this record, not an HTTP context DTO.
        with operation_scope("rag_evaluation", reference="tuner:" + str(run["run_id"]), inherit=False):
            return await function(service, run, *args, **kwargs)
    return wrapped


async def rag_request_origin():
    # FastAPI async dependency: no header/body fields can choose these facts.
    with operation_scope("rag_request", inherit=False):
        yield


async def skill_request_origin():
    with operation_scope("skill_request", inherit=False):
        yield


def _persist(store, origin):
    if type(origin) is not OperationOrigin or origin.source_kind not in SOURCES:
        raise ProviderCallContextError("provider_call_context_required")
    # Restored child origins already exist; do not overwrite their parent link.
    try:
        saved = store.execution("local", origin.execution_id)
    except ProviderCallContextError as exc:
        if exc.code != "provider_call_context_origin_missing":
            raise
        return store.register_execution(execution_id=origin.execution_id,
            task_id=origin.execution_id, source_kind=origin.source_kind)
    if saved["source_kind"] != origin.source_kind:
        raise ProviderCallContextError("provider_call_context_entry_mismatch")
    return saved


def bind_job(repository, job_id: str):
    store = ProviderContextStore(repository)
    current = _origin.get()
    if current is None:
        current = OperationOrigin(opaque_reference("rag-origin", "job-root:" + job_id), "rag_service")
    if not current.source_kind.startswith("rag_"):
        raise ProviderCallContextError("provider_call_context_entry_mismatch")
    _persist(store, current)
    reference = opaque_reference("rag-job", job_id)
    return store.register_execution(execution_id=reference, task_id=reference,
        source_kind=current.source_kind, parent_execution_id=current.execution_id)


@contextmanager
def inherited_operation(repository, parent_execution_id, logical_reference, *, domain="rag"):
    if domain not in {"rag", "skill"}:
        raise ProviderCallContextError("provider_call_context_entry_invalid")
    if type(logical_reference) is not str or not logical_reference:
        raise ProviderCallContextError("provider_call_context_reference_invalid")
    store = ProviderContextStore(repository)
    parent = store.execution("local", parent_execution_id)
    source = domain + "_inherited"
    reference = opaque_reference(domain + "-child", parent["task_id"] + ":" + logical_reference)
    store.register_execution(execution_id=reference, task_id=reference,
        source_kind=source, parent_execution_id=parent_execution_id)
    token = _origin.set(OperationOrigin(reference, source))
    try:
        yield
    finally:
        _origin.reset(token)


@contextmanager
def restored_job(repository, job_id: str):
    store = ProviderContextStore(repository)
    reference = opaque_reference("rag-job", job_id)
    saved = store.execution("local", reference)  # Missing old facts are not invented.
    if saved["source_kind"] not in {"rag_request", "rag_service", "rag_evaluation"}:
        raise ProviderCallContextError("provider_call_context_entry_mismatch")
    store.require_settled_execution(reference, source_kind=saved["source_kind"])
    token = _origin.set(OperationOrigin(reference, saved["source_kind"]))
    try:
        yield
    finally:
        _origin.reset(token)


def start_operation_run(call_service, entry_id, *, parent_reference, stable=False):
    """Persist origin before atomic run binding; a repeated phase cannot replay."""
    current = _origin.get()
    if current is None:
        source = "skill_service" if entry_id == "skill_rerank" else "rag_service"
        current = OperationOrigin(opaque_reference("rag-origin",
            parent_reference if stable else uuid.uuid4().hex), source)
    try:
        store = ProviderContextStore(call_service.repository)
        _persist(store, current)
        if entry_id not in SOURCE_ENTRIES[current.source_kind]:
            raise ProviderCallContextError("provider_call_context_entry_mismatch")
        reference = parent_reference if stable else current.execution_id + ":" + entry_id
        return call_service.start_stable_run(entry_id, parent_run_reference=reference,
            origin_execution_id=current.execution_id)
    except ProviderCallContextError as exc:
        raise RouterServiceError(exc.code, "Provider operation source is missing or inconsistent.", status_code=409) from exc


def require_operation_source(call_service, run_id, entry_id):
    store = ProviderContextStore(call_service.repository)
    with call_service.repository._lock, call_service.repository._connect() as db:
        row = db.execute("SELECT execution_id, entry_id FROM provider_workload_run_origins WHERE tenant_id='local' AND run_id=?",
                         (run_id,)).fetchone()
    try:
        if row is None:
            raise ProviderCallContextError("provider_call_context_origin_missing")
        if row["entry_id"] != entry_id:
            raise ProviderCallContextError("provider_call_context_entry_mismatch")
        saved = store.execution("local", row[0])
        if saved["source_kind"] not in SOURCES or entry_id not in SOURCE_ENTRIES[saved["source_kind"]]:
            raise ProviderCallContextError("provider_call_context_entry_mismatch")
        store.require_workload_source(run_id, source_kind=saved["source_kind"], execution_id=row[0])
    except ProviderCallContextError as exc:
        raise RouterServiceError(exc.code, "Provider operation source is missing or inconsistent.", status_code=409) from exc
