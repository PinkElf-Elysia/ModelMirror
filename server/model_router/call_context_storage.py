"""v20 provenance only. No prompts, credentials, outputs or replay authority.

All access uses the Router owner's connection/lease. Origin creation is an
internal entry-boundary operation, never deserialization of request metadata.
"""
from __future__ import annotations

import json
import hashlib
import sqlite3
from dataclasses import asdict

from .call_context import ProviderCallContext, ProviderCallContextError, _reference
from .call_context import require_provider_call_context


CONTEXT_SCHEMA_VERSION = 20
CONTEXT_TABLES = {
    "provider_certification_call_contexts": """CREATE TABLE IF NOT EXISTS provider_certification_call_contexts (
        tenant_id TEXT NOT NULL, source TEXT NOT NULL, certification_id TEXT NOT NULL,
        identity_hash TEXT NOT NULL, context_json TEXT NOT NULL,
        PRIMARY KEY (tenant_id, source, certification_id)
    )""",
    "provider_canary_policy_revisions": """CREATE TABLE IF NOT EXISTS provider_canary_policy_revisions (
        tenant_id TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision > 0),
        policy_snapshot_hash TEXT NOT NULL, PRIMARY KEY (tenant_id)
    )""",
    "provider_execution_origins": """CREATE TABLE IF NOT EXISTS provider_execution_origins (
        tenant_id TEXT NOT NULL, execution_id TEXT NOT NULL, task_id TEXT NOT NULL,
        execution_reference TEXT NOT NULL,
        parent_execution_id TEXT, source_kind TEXT NOT NULL,
        actor_kind TEXT NOT NULL, actor_reference TEXT NOT NULL, purpose TEXT NOT NULL,
        PRIMARY KEY (tenant_id, execution_id)
    )""",
    "provider_workload_run_origins": """CREATE TABLE IF NOT EXISTS provider_workload_run_origins (
        tenant_id TEXT NOT NULL, run_id TEXT NOT NULL, execution_id TEXT NOT NULL,
        entry_id TEXT NOT NULL, policy_revision INTEGER NOT NULL CHECK(policy_revision > 0),
        PRIMARY KEY (tenant_id, run_id),
        FOREIGN KEY (tenant_id, execution_id) REFERENCES provider_execution_origins(tenant_id, execution_id)
    )""",
    "provider_call_contexts": """CREATE TABLE IF NOT EXISTS provider_call_contexts (
        tenant_id TEXT NOT NULL, attempt_id TEXT NOT NULL,
        owner_kind TEXT NOT NULL CHECK(owner_kind IN ('chat', 'workload')),
        context_json TEXT NOT NULL, PRIMARY KEY (tenant_id, attempt_id)
    )""",
}
SOURCE_ENTRIES = {
    "workflow_classic": {"workflow_interactive_llm", "workflow_interactive_agent", "workflow_interactive_vision"},
    "workflow_deployment": {"workflow_deployment_llm", "workflow_deployment_agent", "workflow_deployment_vision"},
    "xpert_chat": {"xpert", "xpert_vision"}, "xpert_app": {"xpert_app", "xpert_vision"},
    "route_agent_request": {"route_agent"}, "team_chat_request": {"team_chat"},
    "fusion_request": {"fusion"},
    "meta_agent_request": {"meta_agent"},
    "agent_shadow": {"agent_shadow"},
    "expert_team_planner": {"expert_team_planner"},
    "expert_team_agency": {"expert_team_dag"},
    "batch_request": {"openrouter_batch"},
    "multimodal_request": {
        "chat_image", "chat_document_native", "chat_audio_input", "chat_audio_output",
        "chat_video", "image_generation", "multimodal_transcription", "multimodal_speech",
        "xpert_transcription", "xpert_speech", "audio_generation",
        "multimodal_video_analysis", "video_generation", "realtime_voice",
    },
    "rag_request": {"rag_query_generate", "rag_processor_generate", "rag_embedding", "rag_rerank", "rag_vision"},
    "rag_service": {"rag_query_generate", "rag_processor_generate", "rag_embedding", "rag_rerank", "rag_vision"},
    "rag_evaluation": {"rag_query_generate", "rag_processor_generate", "rag_embedding", "rag_rerank", "rag_vision"},
    "rag_inherited": {"rag_query_generate", "rag_processor_generate", "rag_embedding", "rag_rerank", "rag_vision"},
    "skill_request": {"skill_rerank"},
    "skill_service": {"skill_rerank"},
    "skill_evaluation": {"skill_rerank"},
    "skill_inherited": {"skill_rerank"},
}
SERVICE_SOURCES = {
    "workflow_deployment": ("service", "workflow-deployment", "background_assistance"),
    "rag_service": ("service", "rag-runtime", "background_assistance"),
    "rag_evaluation": ("service", "rag-evaluation", "evaluation"),
    "skill_service": ("service", "skill-rerank", "background_assistance"),
    "skill_evaluation": ("service", "skill-evaluation", "evaluation"),
}
RAG_PARENT_SOURCES = frozenset({"workflow_classic", "workflow_deployment", "xpert_chat", "xpert_app"})
REQUEST_SOURCES = frozenset({"route_agent_request", "team_chat_request", "fusion_request", "meta_agent_request"})


def create_context_schema(connection):
    if not connection.in_transaction:
        connection.execute("BEGIN IMMEDIATE")
    for statement in CONTEXT_TABLES.values():
        connection.execute(statement)


def canary_policy_hash(policy):
    # Includes the legacy writer's timestamps, so a rollback writer cannot
    # silently reuse a revision. No URL, key or user content enters this hash.
    material = [policy[key] for key in ("connection_id", "enabled", "created_at", "updated_at")]
    return hashlib.sha256(json.dumps(material, separators=(",", ":")).encode()).hexdigest()


def canary_policy_revision(connection, tenant_id, policy):
    row = connection.execute(
        "SELECT revision, policy_snapshot_hash FROM provider_canary_policy_revisions WHERE tenant_id=?",
        (tenant_id,),
    ).fetchone()
    if policy is None or row is None or row["policy_snapshot_hash"] != canary_policy_hash(policy):
        return None  # Old policy has no proven revision; explicitly save it first.
    return row["revision"]


def require_canary_context(connection, tenant_id, run, supplied):
    _tenant(tenant_id)
    policy = connection.execute(
        "SELECT * FROM provider_chat_canary_policies WHERE tenant_id=?", (tenant_id,),
    ).fetchone()
    revision = canary_policy_revision(connection, tenant_id, policy)
    if policy is None or not policy["enabled"] or policy["connection_id"] != run["connection_id"]:
        raise ProviderCallContextError("provider_call_context_policy_changed")
    return require_provider_call_context(
        supplied, actor_kind="local_anonymous", actor_reference="local-anonymous",
        purpose="user_run", entry_id="chat_canary", parent_run_id=run["session_id_hash"],
        logical_call_id=run["id"], attempt_id=run["id"], policy_revision=revision,
    )


def assert_known_context_schema(connection):
    for name, expected in CONTEXT_TABLES.items():
        row = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
        normalize = lambda text: " ".join(text.replace("IF NOT EXISTS ", "").split())
        if row is None or normalize(row[0]) != normalize(expected):
            raise ValueError("provider_qualification_rollback_schema_mismatch")


def _check_ref(value):
    if not _reference(value):
        raise ProviderCallContextError("provider_call_context_reference_invalid")


def _tenant(value):
    if type(value) is not str or value != "local":
        raise ProviderCallContextError("provider_call_context_tenant_invalid")


def read_context(connection, tenant_id, attempt_id):
    _tenant(tenant_id)
    row = connection.execute(
        "SELECT context_json FROM provider_call_contexts WHERE tenant_id=? AND attempt_id=?",
        (tenant_id, attempt_id),
    ).fetchone()
    if row is None:
        raise ProviderCallContextError("provider_call_context_origin_missing")
    try:
        context = ProviderCallContext(**json.loads(row[0]))
    except (ValueError, TypeError):
        raise ProviderCallContextError("provider_call_context_storage_invalid") from None
    if context.tenant_id != tenant_id or context.attempt_id != attempt_id:
        raise ProviderCallContextError("provider_call_context_reference_mismatch")
    return context


def save_context(connection, context, *, owner_kind):
    if type(context) is not ProviderCallContext:
        raise ProviderCallContextError("provider_call_context_required")
    context.validate()
    if owner_kind not in {"chat", "workload"}:
        raise ProviderCallContextError("provider_call_context_owner_invalid")
    payload = json.dumps(asdict(context), sort_keys=True, separators=(",", ":"))
    existing = connection.execute(
        "SELECT owner_kind, context_json FROM provider_call_contexts WHERE tenant_id=? AND attempt_id=?",
        (context.tenant_id, context.attempt_id),
    ).fetchone()
    if existing is not None:
        if tuple(existing) != (owner_kind, payload):
            raise ProviderCallContextError("provider_call_context_conflict")
        return
    connection.execute("INSERT INTO provider_call_contexts VALUES (?, ?, ?, ?)",
                       (context.tenant_id, context.attempt_id, owner_kind, payload))


def verify_context(connection, tenant_id, attempt_id, supplied):
    # Historical receipts remain readable, but missing provenance is never
    # permission for a new dispatch (including low-level repository callers).
    saved = read_context(connection, tenant_id, attempt_id)
    if type(supplied) is not ProviderCallContext or supplied != saved:
        raise ProviderCallContextError("provider_call_context_conflict")
    return saved


def read_execution(connection, tenant_id, execution_id, _ancestors=()):
    _tenant(tenant_id)
    if execution_id in _ancestors or len(_ancestors) >= 64:
        raise ProviderCallContextError("provider_call_context_lineage_invalid")
    row = connection.execute(
        "SELECT * FROM provider_execution_origins WHERE tenant_id=? AND execution_id=?",
        (tenant_id, execution_id),
    ).fetchone()
    if row is None:
        raise ProviderCallContextError("provider_call_context_origin_missing")
    result = dict(row)
    if result["source_kind"] not in SOURCE_ENTRIES:
        raise ProviderCallContextError("provider_call_context_entry_invalid")
    for value in (execution_id, result["task_id"], result["execution_reference"]):
        _check_ref(value)
    if result["parent_execution_id"] is not None:
        _check_ref(result["parent_execution_id"])
    expected = SERVICE_SOURCES.get(result["source_kind"], ("local_anonymous", "local-anonymous", "user_run"))
    parent = None
    if result["source_kind"] in {"rag_inherited", "skill_inherited"}:
        if result["parent_execution_id"] is None:
            raise ProviderCallContextError("provider_call_context_origin_missing")
        parent = read_execution(connection, tenant_id, result["parent_execution_id"], _ancestors + (execution_id,))
        if parent["source_kind"] not in RAG_PARENT_SOURCES:
            raise ProviderCallContextError("provider_call_context_entry_mismatch")
        expected = tuple(parent[k] for k in ("actor_kind", "actor_reference", "purpose"))
    if tuple(result[k] for k in ("actor_kind", "actor_reference", "purpose")) != expected:
        raise ProviderCallContextError("provider_call_context_actor_mismatch")
    if result["parent_execution_id"] is not None and parent is None:
        parent = read_execution(connection, tenant_id, result["parent_execution_id"], _ancestors + (execution_id,))
        if parent["source_kind"] != result["source_kind"]:
            raise ProviderCallContextError("provider_call_context_entry_mismatch")
    return result


def bind_workload_origin(connection, tenant_id, run_id, execution_id, entry_id, revision):
    source = read_execution(connection, tenant_id, execution_id)
    if entry_id not in SOURCE_ENTRIES[source["source_kind"]]:
        raise ProviderCallContextError("provider_call_context_entry_mismatch")
    values = (tenant_id, run_id, execution_id, entry_id, revision)
    previous = connection.execute(
        "SELECT * FROM provider_workload_run_origins WHERE tenant_id=? AND run_id=?", (tenant_id, run_id),
    ).fetchone()
    if previous is not None:
        if tuple(previous) != values:
            raise ProviderCallContextError("provider_call_context_conflict")
        return
    connection.execute("INSERT INTO provider_workload_run_origins VALUES (?, ?, ?, ?, ?)", values)


def bind_request_workload_origin(connection, tenant_id, run_id, entry_id, source_kind, revision):
    """Called only by a selected internal request adapter, in its run transaction.

    No inference from request=None, model names or client metadata. These
    request adapters have no background/resume entry; other sources need their
    own trusted boundary, not this local-anonymous origin.
    """
    _tenant(tenant_id)
    _check_ref(run_id)
    if type(source_kind) is not str or source_kind not in REQUEST_SOURCES:
        raise ProviderCallContextError("provider_call_context_entry_invalid")
    if entry_id not in SOURCE_ENTRIES[source_kind]:
        raise ProviderCallContextError("provider_call_context_entry_mismatch")
    connection.execute(
        "INSERT INTO provider_execution_origins VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?)",
        (tenant_id, run_id, run_id, run_id, source_kind, "local_anonymous", "local-anonymous", "user_run"),
    )
    bind_workload_origin(connection, tenant_id, run_id, run_id, entry_id, revision)


def workload_context(connection, tenant_id, run_id, call_id, logical_hash, revision):
    row = connection.execute(
        "SELECT * FROM provider_workload_run_origins WHERE tenant_id=? AND run_id=?", (tenant_id, run_id),
    ).fetchone()
    if row is None:
        raise ProviderCallContextError('provider_call_context_origin_missing')
    source = read_execution(connection, tenant_id, row["execution_id"])
    if row["policy_revision"] != revision:
        raise ProviderCallContextError("provider_call_context_policy_changed")
    return ProviderCallContext(
        tenant_id=tenant_id, actor_kind=source["actor_kind"], actor_reference=source["actor_reference"],
        purpose=source["purpose"], entry_id=row["entry_id"], parent_run_id=run_id,
        logical_call_id=logical_hash, attempt_id=call_id, policy_revision=revision,
    )


class ProviderContextStore:
    """Internal facade; does not open/migrate/recover a repository itself."""

    def __init__(self, repository):
        self.repository = repository

    def read(self, tenant_id, attempt_id):
        with self.repository._lock, self.repository._connect() as connection:
            return read_context(connection, tenant_id, attempt_id)

    def execution(self, tenant_id, execution_id):
        with self.repository._lock, self.repository._connect() as connection:
            return read_execution(connection, tenant_id, execution_id)

    def require_workload_source(self, run_id, *, source_kind, execution_id=None):
        """Validate host-owned lineage before accepting a Worker model request."""
        with self.repository._lock, self.repository._connect() as connection:
            row = connection.execute(
                "SELECT * FROM provider_workload_run_origins WHERE tenant_id='local' AND run_id=?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise ProviderCallContextError("provider_call_context_origin_missing")
            origin = read_execution(connection, "local", row["execution_id"])
            if (origin["source_kind"] != source_kind
                    or row["entry_id"] not in SOURCE_ENTRIES[source_kind]
                    or (execution_id is not None and row["execution_id"] != execution_id)):
                raise ProviderCallContextError("provider_call_context_entry_mismatch")
            return origin

    def require_settled_execution(self, execution_id, *, source_kind):
        """A new explicit segment may inherit facts, never an unknown outcome."""
        with self.repository._lock, self.repository._connect() as connection:
            origin = read_execution(connection, "local", execution_id)
            if origin["source_kind"] != source_kind:
                raise ProviderCallContextError("provider_call_context_entry_mismatch")
            unresolved = connection.execute(
                """SELECT 1 FROM provider_workload_run_origins o
                JOIN provider_workload_runs r ON r.tenant_id=o.tenant_id AND r.id=o.run_id
                WHERE o.tenant_id='local' AND o.execution_id=? AND
                (r.status IN ('running', 'uncertain') OR EXISTS (
                    SELECT 1 FROM provider_workload_calls c
                    WHERE c.tenant_id=r.tenant_id AND c.run_id=r.id
                    AND c.status IN ('running', 'uncertain'))) LIMIT 1""",
                (execution_id,),
            ).fetchone()
            if unresolved is not None:
                raise ProviderCallContextError("provider_call_context_execution_unresolved")
            return origin

    def require_dispatched_run(self, run_id, *, entry_id, source_kind, execution_id, call_facts=None):
        """GET recovery checks original provenance, never creates a new actor."""
        origin = self.require_workload_source(
            run_id, source_kind=source_kind, execution_id=execution_id,
        )
        with self.repository._lock, self.repository._connect() as connection:
            calls = connection.execute(
                "SELECT * FROM provider_workload_calls WHERE tenant_id='local' AND run_id=?",
                (run_id,),
            ).fetchall()
            if len(calls) != 1 or not calls[0]["dispatched"]:
                raise ProviderCallContextError("provider_call_context_reference_mismatch")
            call = calls[0]
            if call_facts is not None and any(call[key] != value for key, value in call_facts.items()):
                raise ProviderCallContextError('provider_call_context_reference_mismatch')
            saved = read_context(connection, "local", call["id"])
            expected = workload_context(connection, "local", run_id, call["id"],
                                        call["logical_call_key_hash"], saved.policy_revision)
            if saved != expected or saved.entry_id != entry_id:
                raise ProviderCallContextError("provider_call_context_reference_mismatch")
        return origin

    def child_task_reference(self, *, parent_execution_id, source_kind, logical_parts):
        """Opaque stable identity from runtime IDs only, never tool arguments."""
        parent = self.execution("local", parent_execution_id)
        if parent["source_kind"] != source_kind:
            raise ProviderCallContextError("provider_call_context_entry_mismatch")
        if type(logical_parts) is not tuple or not logical_parts or len(logical_parts) > 6:
            raise ProviderCallContextError("provider_call_context_reference_invalid")
        for part in logical_parts:
            _check_ref(part)
        material = json.dumps(("local", parent["task_id"], source_kind, logical_parts), separators=(",", ":"))
        return "provider-child-" + hashlib.sha256(material.encode("utf-8")).hexdigest()

    def register_execution(self, *, execution_id, task_id, source_kind, execution_reference=None,
                           parent_execution_id=None, resume_from=None):
        execution_reference = task_id if execution_reference is None else execution_reference
        for value in (execution_id, task_id, execution_reference):
            _check_ref(value)
        if source_kind not in SOURCE_ENTRIES:
            raise ProviderCallContextError("provider_call_context_entry_invalid")
        with self.repository._lock, self.repository._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if resume_from is None and connection.execute(
                "SELECT 1 FROM provider_execution_origins WHERE tenant_id='local' AND task_id=? AND execution_id<>?",
                (task_id, execution_id),
            ).fetchone() is not None:
                raise ProviderCallContextError("provider_call_context_execution_replay_blocked")
            if resume_from is not None:
                previous = read_execution(connection, "local", resume_from)
                if (previous["task_id"] != task_id or previous["source_kind"] != source_kind
                        or previous["execution_reference"] != execution_reference):
                    raise ProviderCallContextError("provider_call_context_reference_mismatch")
                actor, reference, purpose = (previous[k] for k in ("actor_kind", "actor_reference", "purpose"))
                parent_execution_id = previous["parent_execution_id"]
            elif parent_execution_id is not None:
                parent = read_execution(connection, "local", parent_execution_id)
                if (parent["source_kind"] != source_kind and not
                        (source_kind in {"rag_inherited", "skill_inherited"} and parent["source_kind"] in RAG_PARENT_SOURCES)):
                    raise ProviderCallContextError("provider_call_context_entry_mismatch")
                actor, reference, purpose = (parent[k] for k in ("actor_kind", "actor_reference", "purpose"))
            elif source_kind in {"rag_inherited", "skill_inherited"}:
                raise ProviderCallContextError("provider_call_context_origin_missing")
            elif source_kind in SERVICE_SOURCES:
                actor, reference, purpose = SERVICE_SOURCES[source_kind]
            else:
                actor, reference, purpose = "local_anonymous", "local-anonymous", "user_run"
            values = ("local", execution_id, task_id, execution_reference, parent_execution_id, source_kind, actor, reference, purpose)
            old = connection.execute(
                "SELECT * FROM provider_execution_origins WHERE tenant_id=? AND execution_id=?",
                ("local", execution_id),
            ).fetchone()
            if old is not None:
                if tuple(old) != values:
                    raise ProviderCallContextError("provider_call_context_conflict")
            else:
                if source_kind in {'multimodal_request', 'batch_request'} and connection.execute(
                    "SELECT 1 FROM provider_workload_runs WHERE tenant_id='local' AND id=?", (execution_id,),
                ).fetchone() is not None:
                    raise ProviderCallContextError('provider_call_context_origin_missing')
                connection.execute("INSERT INTO provider_execution_origins VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", values)
            return read_execution(connection, "local", execution_id)
