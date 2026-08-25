"""Owner-scoped Review Factory for anonymous, static-token and OAuth Hub servers."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from .hub import (
    CALL_TIMEOUT_SECONDS,
    CANDIDATE_ID_RE,
    MAX_RESULT_BYTES,
    MCPHubService,
    MCPHubStore,
    HubError,
    HubUnknownOutcomeError,
    _json_bytes,
    _required_identifier,
    stable_digest,
)
from .hub_contracts import (
    SOP_VERSION,
    STATIC_TOKEN_SOP_VERSION,
    OAUTH_SOP_VERSION,
    HubCandidateSnapshotV1,
    HubContractRegistry,
    HubEvidenceBundle,
    HubEvidenceBundleV1,
    HubEvidenceBundleV2,
    HubEvidenceBundleV3,
    HubReviewedContractV1,
    HubReviewedContractV2,
    HubReviewedContractV3,
    canonical_json_bytes,
    contract_export,
    contract_signature,
    normalize_contract,
    stable_contract_id,
)
from .remote_auth import RemoteAuthError, RemoteAuthPolicyV1
from .remote_oauth import RemoteOAuthError, RemoteOAuthPolicyV2


MAX_REVIEW_ITEMS = 20
MAX_REVIEW_CONCURRENCY = 2
MAX_EVIDENCE_BYTES = 512 * 1024
MAX_STAGE_EVENTS = 200
MAX_TRANSIENT_PREVIEW_BYTES = 4 * 1024
REVIEW_RUN_ID_RE = re.compile(r"^hubreview_[0-9a-f]{32}$")
REVIEW_ITEM_ID_RE = re.compile(r"^hubitem_[0-9a-f]{32}$")
PROPOSAL_ID_RE = re.compile(r"^hubproposal_[0-9a-f]{32}$")
SENSITIVE_FIELD_RE = re.compile(
    r"(?:url|uri|path|file|command|header|token|secret|password|account|publish|delete|trade|device)",
    re.IGNORECASE,
)

SOP_STAGES: tuple[tuple[str, bool], ...] = (
    ("snapshot", True),
    ("static_policy", True),
    ("network_preflight", False),
    ("initialize", True),
    ("capability_check", False),
    ("tools_list", True),
    ("schema_freeze", False),
    ("effect_proposal", False),
    ("call_proposal", False),
    ("call_approval", False),
    ("representative_call", False),
    ("cleanup", False),
    ("human_decision", False),
    ("contract_publish", False),
)
SAFE_TO_RETRY = dict(SOP_STAGES)


def _normalize_evidence(value: dict[str, Any]) -> HubEvidenceBundle:
    if value.get("sop_version") == OAUTH_SOP_VERSION:
        return HubEvidenceBundleV3.model_validate(value)
    if value.get("sop_version") == STATIC_TOKEN_SOP_VERSION:
        return HubEvidenceBundleV2.model_validate(value)
    return HubEvidenceBundleV1.model_validate(value)


def review_factory_enabled() -> bool:
    return os.getenv("MCP_HUB_REVIEW_FACTORY_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def local_contract_publish_enabled() -> bool:
    return os.getenv(
        "MCP_HUB_LOCAL_CONTRACT_PUBLISH_ENABLED", "false"
    ).strip().lower() in {"1", "true", "yes", "on"}


def oauth_review_enabled() -> bool:
    return os.getenv("MCP_REMOTE_OAUTH_REVIEW_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


_DANGEROUS_SCOPE_TERMS = (
    "write",
    "admin",
    "delete",
    "remove",
    "publish",
    "trade",
    "payment",
    "device",
    "control",
    "execute",
    "command",
)
_READ_SCOPE_TERMS = ("read", "readonly", "search", "query", "list", "view")


def assess_oauth_scopes(scopes: tuple[str, ...]) -> dict[str, Any]:
    dangerous: list[str] = []
    unknown: list[str] = []
    read: list[str] = []
    for scope in scopes:
        lower = scope.lower()
        if scope == "offline_access":
            continue
        if any(term in lower for term in _DANGEROUS_SCOPE_TERMS):
            dangerous.append(scope)
        elif any(term in lower for term in _READ_SCOPE_TERMS):
            read.append(scope)
        else:
            unknown.append(scope)
    return {
        "classification": (
            "dangerous" if dangerous else "unknown" if unknown else "read_candidate"
        ),
        "dangerous_scopes": sorted(dangerous),
        "unknown_scopes": sorted(unknown),
        "read_candidate_scopes": sorted(read),
    }


def contract_signing_key() -> str:
    return os.getenv("MCP_HUB_CONTRACT_SIGNING_KEY", "")


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads_object(value: Any, default: Any) -> Any:
    try:
        return json.loads(str(value or ""))
    except (TypeError, json.JSONDecodeError):
        return default


class MCPHubReviewStore:
    """Additive Review Factory tables in the existing hub.sqlite3 database."""

    def __init__(self, hub_store: MCPHubStore | str | Path) -> None:
        self.path = Path(hub_store.path if isinstance(hub_store, MCPHubStore) else hub_store)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def _initialize(self) -> None:
        with self._lock, self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS hub_review_runs (
                    run_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    error_code TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    trigger TEXT NOT NULL DEFAULT 'manual'
                );
                CREATE INDEX IF NOT EXISTS idx_hub_review_runs_owner
                    ON hub_review_runs(tenant_id, owner_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS hub_review_items (
                    item_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES hub_review_runs(run_id) ON DELETE CASCADE,
                    tenant_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    server_name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    remote_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL,
                    current_stage TEXT NOT NULL DEFAULT '',
                    snapshot_json TEXT NOT NULL DEFAULT '{}',
                    evidence_json TEXT NOT NULL DEFAULT '{}',
                    evidence_digest TEXT NOT NULL DEFAULT '',
                    draft_contract_json TEXT NOT NULL DEFAULT '{}',
                    contract_fingerprint TEXT NOT NULL DEFAULT '',
                    error_code TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(run_id, server_name, version, remote_id)
                );
                CREATE INDEX IF NOT EXISTS idx_hub_review_items_run
                    ON hub_review_items(run_id, created_at);
                CREATE TABLE IF NOT EXISTS hub_review_stage_events (
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    item_id TEXT NOT NULL REFERENCES hub_review_items(item_id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL,
                    safe_to_retry INTEGER NOT NULL,
                    error_code TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    UNIQUE(item_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS hub_review_call_proposals (
                    proposal_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    item_id TEXT NOT NULL REFERENCES hub_review_items(item_id) ON DELETE CASCADE,
                    tenant_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    arguments_digest TEXT NOT NULL,
                    schema_digest TEXT NOT NULL,
                    proposal_digest TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(item_id)
                );
                CREATE TABLE IF NOT EXISTS hub_review_call_ledger (
                    proposal_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    result_digest TEXT NOT NULL DEFAULT '',
                    result_size INTEGER NOT NULL DEFAULT 0,
                    result_type TEXT NOT NULL DEFAULT '',
                    assertions_json TEXT NOT NULL DEFAULT '{}',
                    error_code TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS hub_local_contract_revisions (
                    revision_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    contract_id TEXT NOT NULL,
                    contract_fingerprint TEXT NOT NULL,
                    contract_json TEXT NOT NULL,
                    signature TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_hub_local_contract_identity
                    ON hub_local_contract_revisions(tenant_id, owner_id, contract_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS hub_contract_revocations (
                    revocation_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    contract_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_hub_contract_revocations_identity
                    ON hub_contract_revocations(tenant_id, owner_id, contract_id, created_at DESC);
                """
            )
            columns = {
                str(row[1])
                for row in db.execute("PRAGMA table_info(hub_review_runs)").fetchall()
            }
            if "trigger" not in columns:
                db.execute(
                    "ALTER TABLE hub_review_runs ADD COLUMN trigger TEXT NOT NULL DEFAULT 'manual'"
                )

    @staticmethod
    def _item(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["snapshot"] = _loads_object(item.pop("snapshot_json"), {})
        item["evidence"] = _loads_object(item.pop("evidence_json"), {})
        item["draft_contract"] = _loads_object(item.pop("draft_contract_json"), {})
        return item

    def create_run(
        self,
        tenant_id: str,
        owner_id: str,
        identities: list[dict[str, str]],
        *,
        allow_queued_when_busy: bool = False,
        trigger: str = "manual",
    ) -> dict[str, Any]:
        if not 1 <= len(identities) <= MAX_REVIEW_ITEMS:
            raise HubError(
                "复核批次必须包含 1–20 个候选。",
                code="hub_review_batch_size",
                status_code=422,
            )
        now = time.time()
        run_id = "hubreview_" + uuid.uuid4().hex
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            active = db.execute(
                "SELECT run_id FROM hub_review_runs WHERE tenant_id=? AND owner_id=? "
                "AND status IN ('queued','running','awaiting_operator') LIMIT 1",
                (tenant_id, owner_id),
            ).fetchone()
            if active is not None and not allow_queued_when_busy:
                raise HubError(
                    "当前本地运维者已有进行中的复核批次。",
                    code="hub_review_owner_busy",
                    status_code=409,
                )
            db.execute(
                "INSERT INTO hub_review_runs(run_id,tenant_id,owner_id,status,cancel_requested,error_code,created_at,updated_at,trigger) "
                "VALUES(?,?,?,?,0,'',?,?,?)",
                (run_id, tenant_id, owner_id, "queued", now, now, trigger[:40]),
            )
            for identity in identities:
                db.execute(
                    "INSERT INTO hub_review_items(item_id,run_id,tenant_id,owner_id,server_name,version,remote_id,state,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,'queued',?,?)",
                    (
                        "hubitem_" + uuid.uuid4().hex,
                        run_id,
                        tenant_id,
                        owner_id,
                        identity["server_name"],
                        identity["version"],
                        identity["remote_id"],
                        now,
                        now,
                    ),
                )
        return self.require_run(run_id, tenant_id, owner_id)

    def has_review_identity(
        self, tenant_id: str, owner_id: str, server_name: str, version: str
    ) -> bool:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT 1 FROM hub_review_items WHERE tenant_id=? AND owner_id=? AND server_name=? AND version=? "
                "AND state IN ('queued','running','evidence_ready','awaiting_call_approval','awaiting_decision','approved','drifted') LIMIT 1",
                (tenant_id, owner_id, server_name, version),
            ).fetchone()
        return row is not None

    def create_drift_record(
        self,
        tenant_id: str,
        owner_id: str,
        *,
        server_name: str,
        version: str,
        remote_id: str,
        error_code: str,
    ) -> dict[str, Any]:
        now = time.time()
        run_id = "hubreview_" + uuid.uuid4().hex
        item_id = "hubitem_" + uuid.uuid4().hex
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO hub_review_runs(run_id,tenant_id,owner_id,status,cancel_requested,error_code,created_at,updated_at,trigger) "
                "VALUES(?,?,?,?,0,?,?,?,'drift')",
                (run_id, tenant_id, owner_id, "completed", error_code, now, now),
            )
            db.execute(
                "INSERT INTO hub_review_items(item_id,run_id,tenant_id,owner_id,server_name,version,remote_id,state,current_stage,error_code,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,'drifted','snapshot',?,?,?)",
                (
                    item_id,
                    run_id,
                    tenant_id,
                    owner_id,
                    server_name,
                    version,
                    remote_id,
                    error_code,
                    now,
                    now,
                ),
            )
        self.add_event(
            run_id,
            item_id,
            "snapshot",
            "failed",
            error_code=error_code,
            payload={"reason": "published contract identity no longer matches Registry"},
        )
        return self.require_run(run_id, tenant_id, owner_id)

    def require_run(self, run_id: str, tenant_id: str, owner_id: str) -> dict[str, Any]:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM hub_review_runs WHERE run_id=? AND tenant_id=? AND owner_id=?",
                (run_id, tenant_id, owner_id),
            ).fetchone()
        if row is None:
            raise HubError("复核批次不存在。", code="hub_review_run_not_found", status_code=404)
        result = dict(row)
        result["cancel_requested"] = bool(result["cancel_requested"])
        result["items"] = self.list_items(run_id, tenant_id, owner_id)
        counts: dict[str, int] = {}
        for item in result["items"]:
            counts[item["state"]] = counts.get(item["state"], 0) + 1
        result["counts"] = counts
        return result

    def list_runs(self, tenant_id: str, owner_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT run_id FROM hub_review_runs WHERE tenant_id=? AND owner_id=? ORDER BY updated_at DESC",
                (tenant_id, owner_id),
            ).fetchall()
        return [self.require_run(str(row["run_id"]), tenant_id, owner_id) for row in rows]

    def list_items(self, run_id: str, tenant_id: str, owner_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT * FROM hub_review_items WHERE run_id=? AND tenant_id=? AND owner_id=? ORDER BY created_at,item_id",
                (run_id, tenant_id, owner_id),
            ).fetchall()
        return [self._decorate_item(self._item(row)) for row in rows]

    def _decorate_item(self, item: dict[str, Any]) -> dict[str, Any]:
        item["events"] = self.list_events(item["item_id"])
        item["proposal"] = self.get_proposal_for_item(item["item_id"])
        return item

    def require_item(
        self, run_id: str, item_id: str, tenant_id: str, owner_id: str
    ) -> dict[str, Any]:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM hub_review_items WHERE run_id=? AND item_id=? AND tenant_id=? AND owner_id=?",
                (run_id, item_id, tenant_id, owner_id),
            ).fetchone()
        if row is None:
            raise HubError("复核项不存在。", code="hub_review_item_not_found", status_code=404)
        return self._decorate_item(self._item(row))

    def set_run(self, run_id: str, *, status: str, error_code: str = "") -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "UPDATE hub_review_runs SET status=?,error_code=?,updated_at=? WHERE run_id=?",
                (status, error_code[:120], time.time(), run_id),
            )

    def request_cancel(self, run_id: str, tenant_id: str, owner_id: str) -> None:
        self.require_run(run_id, tenant_id, owner_id)
        with self._lock, self._connect() as db:
            db.execute(
                "UPDATE hub_review_runs SET cancel_requested=1,updated_at=? WHERE run_id=? AND tenant_id=? AND owner_id=?",
                (time.time(), run_id, tenant_id, owner_id),
            )

    def set_item(
        self,
        item_id: str,
        *,
        state: str | None = None,
        stage: str | None = None,
        candidate_id: str | None = None,
        snapshot: dict[str, Any] | None = None,
        evidence: dict[str, Any] | None = None,
        evidence_digest: str | None = None,
        draft_contract: dict[str, Any] | None = None,
        contract_fingerprint: str | None = None,
        error_code: str | None = None,
    ) -> None:
        updates: list[str] = ["updated_at=?"]
        params: list[Any] = [time.time()]
        mapping = {
            "state": state,
            "current_stage": stage,
            "candidate_id": candidate_id,
            "evidence_digest": evidence_digest,
            "contract_fingerprint": contract_fingerprint,
            "error_code": error_code,
        }
        for column, value in mapping.items():
            if value is not None:
                updates.append(f"{column}=?")
                params.append(str(value)[:4096])
        for column, value in (
            ("snapshot_json", snapshot),
            ("evidence_json", evidence),
            ("draft_contract_json", draft_contract),
        ):
            if value is not None:
                encoded = _json_text(value)
                if column == "evidence_json" and len(encoded.encode("utf-8")) > MAX_EVIDENCE_BYTES:
                    raise HubError(
                        "复核证据超过 512 KiB 上限。",
                        code="hub_review_evidence_too_large",
                        status_code=413,
                    )
                updates.append(f"{column}=?")
                params.append(encoded)
        params.append(item_id)
        with self._lock, self._connect() as db:
            db.execute(
                f"UPDATE hub_review_items SET {','.join(updates)} WHERE item_id=?",
                params,
            )

    def add_event(
        self,
        run_id: str,
        item_id: str,
        stage: str,
        status: str,
        *,
        error_code: str = "",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_payload = dict(payload or {})
        encoded = _json_text(clean_payload)
        if len(encoded.encode("utf-8")) > 16 * 1024:
            raise HubError("阶段证据过大。", code="hub_review_event_too_large", status_code=413)
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            count = int(
                db.execute(
                    "SELECT count(*) FROM hub_review_stage_events WHERE item_id=?",
                    (item_id,),
                ).fetchone()[0]
            )
            if count >= MAX_STAGE_EVENTS:
                raise HubError("复核事件超过上限。", code="hub_review_event_limit", status_code=409)
            sequence = count + 1
            db.execute(
                "INSERT INTO hub_review_stage_events VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    "hubevent_" + uuid.uuid4().hex,
                    run_id,
                    item_id,
                    sequence,
                    stage,
                    status,
                    int(SAFE_TO_RETRY.get(stage, False)),
                    error_code[:120],
                    encoded,
                    time.time(),
                ),
            )
        self.set_item(item_id, stage=stage)
        return self.list_events(item_id)[-1]

    def list_events(self, item_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT * FROM hub_review_stage_events WHERE item_id=? ORDER BY sequence",
                (item_id,),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["safe_to_retry"] = bool(item["safe_to_retry"])
            item["payload"] = _loads_object(item.pop("payload_json"), {})
            result.append(item)
        return result

    def create_proposal(
        self,
        *,
        run_id: str,
        item_id: str,
        tenant_id: str,
        owner_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        schema_digest: str,
    ) -> dict[str, Any]:
        existing = self.get_proposal_for_item(item_id)
        if existing is not None:
            return existing
        arguments_digest = stable_digest(arguments)
        proposal_body = {
            "run_id": run_id,
            "item_id": item_id,
            "tool_name": tool_name,
            "arguments": arguments,
            "arguments_digest": arguments_digest,
            "schema_digest": schema_digest,
        }
        proposal_id = "hubproposal_" + uuid.uuid4().hex
        now = time.time()
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO hub_review_call_proposals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    proposal_id,
                    run_id,
                    item_id,
                    tenant_id,
                    owner_id,
                    tool_name,
                    _json_text(arguments),
                    arguments_digest,
                    schema_digest,
                    stable_digest(proposal_body),
                    "proposed",
                    now,
                    now,
                ),
            )
        return self.require_proposal(proposal_id, tenant_id, owner_id)

    @staticmethod
    def _proposal(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["arguments"] = _loads_object(item.pop("arguments_json"), {})
        return item

    def get_proposal_for_item(self, item_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM hub_review_call_proposals WHERE item_id=?",
                (item_id,),
            ).fetchone()
        return self._proposal(row) if row else None

    def require_proposal(
        self, proposal_id: str, tenant_id: str, owner_id: str
    ) -> dict[str, Any]:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM hub_review_call_proposals WHERE proposal_id=? AND tenant_id=? AND owner_id=?",
                (proposal_id, tenant_id, owner_id),
            ).fetchone()
        if row is None:
            raise HubError("代表调用提案不存在。", code="hub_review_proposal_not_found", status_code=404)
        return self._proposal(row)

    def set_proposal_state(self, proposal_id: str, state: str) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "UPDATE hub_review_call_proposals SET state=?,updated_at=? WHERE proposal_id=?",
                (state, time.time(), proposal_id),
            )

    def begin_call(self, proposal: dict[str, Any], candidate_id: str) -> None:
        now = time.time()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM hub_review_call_ledger WHERE proposal_id=?",
                (proposal["proposal_id"],),
            ).fetchone() is not None:
                raise HubError("代表调用批准不可重放。", code="hub_review_call_replay", status_code=409)
            db.execute(
                "INSERT INTO hub_review_call_ledger(proposal_id,run_id,item_id,tenant_id,owner_id,candidate_id,state,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,'started',?,?)",
                (
                    proposal["proposal_id"],
                    proposal["run_id"],
                    proposal["item_id"],
                    proposal["tenant_id"],
                    proposal["owner_id"],
                    candidate_id,
                    now,
                    now,
                ),
            )

    def finish_call(
        self,
        proposal_id: str,
        *,
        state: str,
        result_digest: str = "",
        result_size: int = 0,
        result_type: str = "",
        assertions: dict[str, Any] | None = None,
        error_code: str = "",
    ) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "UPDATE hub_review_call_ledger SET state=?,result_digest=?,result_size=?,result_type=?,assertions_json=?,error_code=?,updated_at=? WHERE proposal_id=?",
                (
                    state,
                    result_digest,
                    int(result_size),
                    result_type[:80],
                    _json_text(assertions or {}),
                    error_code[:120],
                    time.time(),
                    proposal_id,
                ),
            )

    def recover_started_calls(self, tenant_id: str, owner_id: str) -> list[dict[str, str]]:
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT item_id,candidate_id,proposal_id FROM hub_review_call_ledger "
                "WHERE tenant_id=? AND owner_id=? AND state='started'",
                (tenant_id, owner_id),
            ).fetchall()
            db.execute(
                "UPDATE hub_review_call_ledger SET state='unknown_outcome',error_code='unknown_outcome',updated_at=? "
                "WHERE tenant_id=? AND owner_id=? AND state='started'",
                (time.time(), tenant_id, owner_id),
            )
            for row in rows:
                db.execute(
                    "UPDATE hub_review_items SET state='unknown_outcome',error_code='unknown_outcome',updated_at=? WHERE item_id=?",
                    (time.time(), row["item_id"]),
                )
        return [dict(row) for row in rows]

    def add_local_contract_revision(
        self,
        tenant_id: str,
        owner_id: str,
        contract: HubReviewedContractV1,
        signature: str,
    ) -> dict[str, Any]:
        revision_id = "hubrevision_" + uuid.uuid4().hex
        now = time.time()
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO hub_local_contract_revisions VALUES(?,?,?,?,?,?,?,?)",
                (
                    revision_id,
                    tenant_id,
                    owner_id,
                    contract.contract_id,
                    contract.contract_fingerprint,
                    contract_export(contract).decode("utf-8").strip(),
                    signature,
                    now,
                ),
            )
        return {
            "revision_id": revision_id,
            "contract_id": contract.contract_id,
            "contract_fingerprint": contract.contract_fingerprint,
            "created_at": now,
        }

    def list_local_contract_revisions(
        self, tenant_id: str, owner_id: str
    ) -> list[dict[str, Any]]:
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT * FROM hub_local_contract_revisions WHERE tenant_id=? AND owner_id=? ORDER BY created_at,revision_id",
                (tenant_id, owner_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_revocation(
        self, tenant_id: str, owner_id: str, contract_id: str, action: str, reason: str = ""
    ) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO hub_contract_revocations VALUES(?,?,?,?,?,?,?)",
                (
                    "hubrevocation_" + uuid.uuid4().hex,
                    tenant_id,
                    owner_id,
                    contract_id,
                    action,
                    reason[:500],
                    time.time(),
                ),
            )

    def is_contract_revoked(self, tenant_id: str, owner_id: str, contract_id: str) -> bool:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT action FROM hub_contract_revocations WHERE tenant_id=? AND owner_id=? AND contract_id=? "
                "ORDER BY created_at DESC,revocation_id DESC LIMIT 1",
                (tenant_id, owner_id, contract_id),
            ).fetchone()
        return bool(row and row["action"] == "revoke")


def classify_tool_effect(tool: dict[str, Any]) -> str:
    name = str(tool.get("name") or "")
    description = str(tool.get("description") or "")
    schema = tool.get("input_schema") if isinstance(tool.get("input_schema"), dict) else {}
    joined = " ".join([name, description, _json_text(schema)]).lower()
    if SENSITIVE_FIELD_RE.search(joined) or re.search(
        r"\b(?:execute|shell|admin|payment|purchase|send|post|deploy|control)\b", joined
    ):
        return "dangerous_candidate"
    if re.search(r"\b(?:delete|remove|write|update|create|insert|set|upload|publish|mutate)\b", joined):
        return "state_write_candidate"
    if re.search(r"\b(?:render|convert|generate|chart|artifact|export)\b", joined):
        return "artifact_candidate"
    if re.search(r"\b(?:read|search|find|get|list|lookup|query|describe|documentation|metadata)\b", joined):
        return "read_candidate"
    return "unknown"


def deterministic_arguments(schema: dict[str, Any]) -> dict[str, Any] | None:
    if schema.get("type") != "object":
        return None
    properties = schema.get("properties")
    required = schema.get("required", [])
    if not isinstance(properties, dict) or not isinstance(required, list) or len(required) > 3:
        return None
    result: dict[str, Any] = {}
    for raw_name in required:
        name = str(raw_name)
        if SENSITIVE_FIELD_RE.search(name):
            return None
        field = properties.get(name)
        if not isinstance(field, dict):
            return None
        if isinstance(field.get("enum"), list) and field["enum"]:
            value = field["enum"][0]
            if not isinstance(value, (str, int, bool)):
                return None
            result[name] = value
            continue
        kind = field.get("type")
        if kind == "string":
            max_length = field.get("maxLength")
            if not isinstance(max_length, int) or max_length < 1 or max_length > 1000:
                return None
            if field.get("pattern") or field.get("format"):
                return None
            min_length = int(field.get("minLength") or 0)
            if min_length > max_length:
                return None
            value = "modelmirror-review"[:max_length]
            if len(value) < min_length:
                value += "x" * (min_length - len(value))
            result[name] = value
        elif kind == "integer":
            minimum = field.get("minimum", 0)
            maximum = field.get("maximum")
            if not isinstance(minimum, int) or (
                maximum is not None and (not isinstance(maximum, int) or minimum > maximum)
            ):
                return None
            result[name] = minimum
        elif kind == "boolean":
            result[name] = False
        else:
            return None
    return result


def _redacted_preview(value: Any) -> str:
    def clean(item: Any, key: str = "") -> Any:
        if key and SENSITIVE_FIELD_RE.search(key):
            return "[REDACTED]"
        if isinstance(item, dict):
            return {str(k): clean(v, str(k)) for k, v in item.items()}
        if isinstance(item, list):
            return [clean(part) for part in item[:50]]
        if isinstance(item, str):
            return item[:1000]
        return item

    raw = json.dumps(clean(value), ensure_ascii=False, sort_keys=True)
    encoded = raw.encode("utf-8")[:MAX_TRANSIENT_PREVIEW_BYTES]
    return encoded.decode("utf-8", errors="ignore")


class MCPHubReviewService:
    def __init__(
        self,
        hub_service: MCPHubService,
        store: MCPHubReviewStore,
        *,
        signing_key: str = "",
        repository_dir: str | Path | None = None,
    ) -> None:
        self.hub = hub_service
        self.store = store
        self.tenant_id = hub_service.tenant_id
        self.owner_id = hub_service.owner_id
        self.signing_key = str(signing_key or "")
        self.contracts = HubContractRegistry(
            local_store=store,
            tenant_id=self.tenant_id,
            owner_id=self.owner_id,
            signing_key=self.signing_key,
            repository_dir=repository_dir,
        )
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._item_locks: dict[str, asyncio.Lock] = {}

    def _require_enabled(self) -> None:
        if not review_factory_enabled():
            raise HubError("MCP Hub 复核工厂当前未启用。", code="hub_review_disabled", status_code=503)

    def status(self) -> dict[str, Any]:
        active = next(
            (
                item["run_id"]
                for item in self.store.list_runs(self.tenant_id, self.owner_id)
                if item["status"] in {"queued", "running", "awaiting_operator"}
            ),
            None,
        )
        return {
            "enabled": review_factory_enabled(),
            "local_publish_enabled": local_contract_publish_enabled(),
            "oauth_review_enabled": oauth_review_enabled(),
            "signing_key_configured": bool(self.signing_key),
            "sop_version": SOP_VERSION,
            "sop_versions": [
                SOP_VERSION,
                STATIC_TOKEN_SOP_VERSION,
                OAUTH_SOP_VERSION,
            ],
            "stages": [
                {"name": stage, "safe_to_retry": safe} for stage, safe in SOP_STAGES
            ],
            "max_batch_size": MAX_REVIEW_ITEMS,
            "max_concurrency": MAX_REVIEW_CONCURRENCY,
            "active_run_id": active,
            "operator_scope": "trusted-local-operator",
            "multi_tenant_admin": False,
        }

    async def start(self) -> None:
        for recovered in self.store.recover_started_calls(self.tenant_id, self.owner_id):
            candidate_id = str(recovered.get("candidate_id") or "")
            if CANDIDATE_ID_RE.fullmatch(candidate_id):
                try:
                    self.hub.store.update_candidate(
                        candidate_id,
                        self.tenant_id,
                        self.owner_id,
                        state="tainted",
                        taint_reason="unknown_outcome",
                    )
                except HubError:
                    pass
        if not review_factory_enabled():
            return
        runs = self.store.list_runs(self.tenant_id, self.owner_id)
        running = next((run for run in runs if run["status"] == "running"), None)
        if running is not None:
            if self._unsafe_resume_items(running):
                for item in self._unsafe_resume_items(running):
                    self.store.set_item(
                        item["item_id"],
                        state="interrupted",
                        error_code="hub_review_resume_unsafe_stage",
                    )
                self.store.set_run(
                    running["run_id"],
                    status="interrupted",
                    error_code="hub_review_resume_unsafe_stage",
                )
            else:
                self._schedule(running["run_id"])
        else:
            self._schedule_next_queued()

    async def close(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def create_run(
        self,
        identities: list[dict[str, str]],
        *,
        trigger: str = "manual",
        allow_queued_when_busy: bool = False,
    ) -> dict[str, Any]:
        self._require_enabled()
        normalized: list[dict[str, str]] = []
        seen: set[tuple[str, str, str]] = set()
        for identity in identities:
            server = self.hub.get_server(identity["server_name"], identity["version"])
            remote = next(
                (
                    item
                    for item in server["remotes"]
                    if item["remote_id"] == identity["remote_id"]
                ),
                None,
            )
            if remote is None or remote.get("eligibility") not in {
                "eligible",
                "static_token_candidate",
                *( ["oauth_discovery_candidate"] if oauth_review_enabled() else [] ),
            }:
                raise HubError(
                    "复核项必须来自当前 Registry 的可试连端点。",
                    code="hub_review_remote_ineligible",
                    status_code=409,
                )
            key = (server["server_name"], server["version"], remote["remote_id"])
            if key in seen:
                raise HubError("复核批次包含重复候选。", code="hub_review_duplicate_item", status_code=422)
            seen.add(key)
            normalized.append(
                {"server_name": key[0], "version": key[1], "remote_id": key[2]}
            )
        run = self.store.create_run(
            self.tenant_id,
            self.owner_id,
            normalized,
            trigger=trigger,
            allow_queued_when_busy=allow_queued_when_busy,
        )
        self._schedule(run["run_id"])
        return self.store.require_run(run["run_id"], self.tenant_id, self.owner_id)

    def _schedule(self, run_id: str) -> None:
        current = self._tasks.get(run_id)
        if current is None or current.done():
            self._tasks[run_id] = asyncio.create_task(self._run(run_id))

    def _schedule_next_queued(self) -> None:
        runs = self.store.list_runs(self.tenant_id, self.owner_id)
        if any(run["status"] in {"running", "awaiting_operator"} for run in runs):
            return
        queued = sorted(
            (run for run in runs if run["status"] == "queued"),
            key=lambda run: (float(run["created_at"]), str(run["run_id"])),
        )
        if queued:
            self._schedule(queued[0]["run_id"])

    async def _run(self, run_id: str) -> None:
        self.store.set_run(run_id, status="running")
        semaphore = asyncio.Semaphore(MAX_REVIEW_CONCURRENCY)
        run = self.store.require_run(run_id, self.tenant_id, self.owner_id)

        async def process(item: dict[str, Any]) -> None:
            async with semaphore:
                latest = self.store.require_run(run_id, self.tenant_id, self.owner_id)
                if latest["cancel_requested"]:
                    self.store.set_item(item["item_id"], state="cancelled", error_code="hub_review_cancelled")
                    return
                if item["state"] not in {"queued", "running", "interrupted", "failed"}:
                    return
                await self._process_item(run_id, item["item_id"])

        try:
            await asyncio.gather(*(process(item) for item in run["items"]))
            self._refresh_run_status(run_id)
        except asyncio.CancelledError:
            self.store.set_run(run_id, status="interrupted", error_code="hub_review_interrupted")
            raise
        except Exception:
            self.store.set_run(run_id, status="failed", error_code="hub_review_internal_error")
        finally:
            self._tasks.pop(run_id, None)

    def _event(
        self,
        run_id: str,
        item_id: str,
        stage: str,
        status: str = "passed",
        *,
        error_code: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.store.add_event(
            run_id, item_id, stage, status, error_code=error_code, payload=payload
        )

    async def _process_item(self, run_id: str, item_id: str) -> None:
        item = self.store.require_item(run_id, item_id, self.tenant_id, self.owner_id)
        self.store.set_item(item_id, state="running", error_code="")
        snapshot: HubCandidateSnapshotV1 | None = None
        binding_revision_digest = ""
        oauth_metadata: Any | None = None
        oauth_scope_assessment: dict[str, Any] | None = None
        oauth_candidate = False
        try:
            server = self.hub.get_server(item["server_name"], item["version"])
            remote = next(
                (entry for entry in server["remotes"] if entry["remote_id"] == item["remote_id"]),
                None,
            )
            if remote is None:
                raise HubError("Registry 远程端点已漂移。", code="hub_source_drift", status_code=409)
            snapshot = HubCandidateSnapshotV1(
                server_name=server["server_name"],
                version=server["version"],
                remote_id=remote["remote_id"],
                remote_url=remote["url"],
                origin=remote["origin"],
                source_digest=server["source_digest"],
                transport="streamable-http",
                publisher=str(server.get("publisher") or ""),
                registry_status=str(server.get("status") or ""),
            )
            self.store.set_item(item_id, snapshot=snapshot.model_dump(mode="json"))
            self._event(run_id, item_id, "snapshot", payload={"snapshot_digest": snapshot.snapshot_digest})
            if server["status"] not in {"active", "published"} or remote[
                "eligibility"
            ] not in {
                "eligible",
                "static_token_candidate",
                *(["oauth_discovery_candidate"] if oauth_review_enabled() else []),
            }:
                raise HubError("候选不满足静态准入。", code="hub_review_static_policy_denied", status_code=409)
            self._event(
                run_id,
                item_id,
                "static_policy",
                payload={"eligibility": remote["eligibility"]},
            )
            candidate = self.hub.create_candidate(
                snapshot.server_name, snapshot.version, snapshot.remote_id
            )
            self.store.set_item(item_id, candidate_id=candidate["candidate_id"])
            auth_policy = self.hub._candidate_auth_policy(candidate)
            oauth_candidate = bool(self.hub._candidate_oauth_source(candidate, remote=remote))
            if oauth_candidate:
                authorization = self.hub._require_oauth_authorization()
                try:
                    oauth_metadata = authorization.execution_metadata(
                        target_type="hub_candidate",
                        target_id=candidate["candidate_id"],
                        source_digest=candidate["source_digest"],
                    )
                    oauth_scope_assessment = assess_oauth_scopes(
                        oauth_metadata.scopes
                    )
                    if oauth_scope_assessment["dangerous_scopes"]:
                        raise HubError(
                            "OAuth Scope 含高危写入或控制语义，本轮拒绝复核。",
                            code="mcp_remote_oauth_high_risk_scope_denied",
                            status_code=409,
                        )
                except RemoteOAuthError as exc:
                    raise HubError(
                        str(exc), code=exc.code, status_code=exc.status_code
                    ) from None
            elif auth_policy is not None:
                binding_id = str(candidate.get("auth_binding_id") or "")
                if not binding_id or self.hub.remote_auth_broker is None:
                    raise HubError(
                        "静态 Token 候选必须先绑定凭据。",
                        code="mcp_remote_auth_binding_missing",
                        status_code=409,
                    )
                try:
                    binding = self.hub.remote_auth_broker.get_binding(
                        binding_id,
                        current_policy=auth_policy,
                        target_type="hub_candidate",
                        target_id=candidate["candidate_id"],
                    )
                except RemoteAuthError as exc:
                    raise HubError(
                        str(exc), code=exc.code, status_code=exc.status_code
                    ) from None
                binding_revision_digest = stable_digest(
                    {
                        "binding_id": binding.binding_id,
                        "revision": binding.revision,
                        "policy_fingerprint": binding.policy_fingerprint,
                    }
                )
            self._event(run_id, item_id, "network_preflight", "started")
            try:
                candidate = (
                    await self.hub.preflight_oauth_review(
                        candidate["candidate_id"],
                        expected_oauth_context=(
                            {
                                "policy_fingerprint": oauth_metadata.policy_fingerprint,
                                "scope_digest": oauth_metadata.scope_digest,
                                "token_revision_digest": oauth_metadata.token_revision_digest,
                                "resource_digest": oauth_metadata.resource_digest,
                                "discovery_fingerprint": oauth_metadata.discovery_fingerprint,
                                "registration_digest": oauth_metadata.registration_digest,
                            }
                            if oauth_metadata is not None
                            else None
                        ),
                    )
                    if oauth_candidate
                    else await self.hub.preflight(candidate["candidate_id"])
                )
            except HubError as exc:
                self._event(
                    run_id,
                    item_id,
                    "network_preflight",
                    "failed",
                    error_code=exc.code,
                )
                raise
            if self._cancel_item_if_requested(run_id, item_id):
                return
            self._event(run_id, item_id, "network_preflight", payload={"origin": candidate["origin"]})
            self._event(run_id, item_id, "initialize", payload={"protocol": "mcp"})
            capabilities = {
                "tools": True,
                "prompts": False,
                "resources": False,
                "roots": False,
                "sampling": False,
                "elicitation": False,
            }
            self._event(run_id, item_id, "capability_check", payload=capabilities)
            tools = list(candidate.get("tools") or [])
            self._event(run_id, item_id, "tools_list", payload={"tool_count": len(tools)})
            tool_digests = {str(tool["name"]): str(tool["schema_digest"]) for tool in tools}
            self._event(
                run_id,
                item_id,
                "schema_freeze",
                payload={"schema_digest": candidate["schema_digest"], "tool_schema_digests": tool_digests},
            )
            effects = {str(tool["name"]): classify_tool_effect(tool) for tool in tools}
            self._event(run_id, item_id, "effect_proposal", payload={"effects": effects})
            proposal = self._generate_proposal(
                run_id,
                item_id,
                tools,
                effects,
                str(candidate["schema_digest"]),
            )
            stage_evidence = {
                stage: {"status": "passed", "implementation_version": "v1"}
                for stage in (
                    "snapshot",
                    "static_policy",
                    "network_preflight",
                    "initialize",
                    "capability_check",
                    "tools_list",
                    "schema_freeze",
                    "effect_proposal",
                    "call_proposal",
                    "cleanup",
                )
            }
            stage_evidence["tools_list"]["count"] = len(tools)
            if proposal is None:
                stage_evidence["call_proposal"] = {
                    "status": "blocked",
                    "implementation_version": "v1",
                    "error_code": "manual_call_unavailable",
                }
            evidence_fields: dict[str, Any] = {
                "snapshot": snapshot,
                "stages": stage_evidence,
                "capabilities": capabilities,
                "schema_digest": str(candidate["schema_digest"]),
                "tool_schema_digests": tool_digests,
                "effect_proposals": effects,
                "representative_call": {},
                "cleanup": {
                    "temporary_session_closed": True,
                    "capability_revoked": True,
                },
            }
            evidence: HubEvidenceBundle
            if oauth_metadata is not None:
                evidence = HubEvidenceBundleV3(
                    **evidence_fields,
                    oauth_policy_fingerprint=oauth_metadata.policy_fingerprint,
                    discovery_fingerprint=oauth_metadata.discovery_fingerprint,
                    registration_digest=oauth_metadata.registration_digest,
                    resource_digest=oauth_metadata.resource_digest,
                    scope_source=oauth_metadata.scope_source,
                    authorized_scopes=oauth_metadata.scopes,
                    authorized_scope_digest=oauth_metadata.scope_digest,
                    token_revision_digest=oauth_metadata.token_revision_digest,
                    protocol_version=oauth_metadata.protocol_version,
                    scope_assessment=oauth_scope_assessment
                    or assess_oauth_scopes(oauth_metadata.scopes),
                )
            elif auth_policy is None:
                evidence = HubEvidenceBundleV1(**evidence_fields)
            else:
                evidence = HubEvidenceBundleV2(
                    **evidence_fields,
                    auth_mode=auth_policy.mode,
                    header_name=auth_policy.header_name,
                    auth_policy_fingerprint=auth_policy.policy_fingerprint,
                    credential_revision_digest=binding_revision_digest,
                )
            self.store.set_item(
                item_id,
                state="evidence_ready",
                evidence=evidence.model_dump(mode="json"),
                evidence_digest=evidence.evidence_digest,
            )
            if proposal is None:
                self._event(
                    run_id,
                    item_id,
                    "call_proposal",
                    "blocked",
                    error_code="manual_call_unavailable",
                )
                self.store.set_item(
                    item_id,
                    state="blocked",
                    error_code="manual_call_unavailable",
                )
            else:
                self._event(
                    run_id,
                    item_id,
                    "call_proposal",
                    payload={"proposal_digest": proposal["proposal_digest"], "tool_name": proposal["tool_name"]},
                )
                self.store.set_item(item_id, state="awaiting_call_approval")
        except HubError as exc:
            if self._cancel_item_if_requested(run_id, item_id):
                return
            if (
                snapshot is not None
                and remote is not None
                and oauth_candidate
                and oauth_metadata is None
            ):
                self.store.set_item(item_id, state="blocked", error_code=exc.code)
                return
            if snapshot is not None:
                failed_fields: dict[str, Any] = {
                    "snapshot": snapshot,
                    "stages": {
                        str(
                            self.store.require_item(
                                run_id,
                                item_id,
                                self.tenant_id,
                                self.owner_id,
                            ).get("current_stage")
                            or "snapshot"
                        ): {
                            "status": "failed",
                            "implementation_version": "v1",
                            "error_code": exc.code,
                        }
                    },
                    "capabilities": {},
                    "schema_digest": "",
                    "tool_schema_digests": {},
                    "effect_proposals": {},
                    "representative_call": {},
                    "cleanup": {
                        "temporary_session_closed": True,
                        "capability_revoked": True,
                    },
                    "fixed_errors": [exc.code],
                }
                failed_policy = (
                    RemoteAuthPolicyV1.model_validate(remote.get("auth_policy"))
                    if isinstance(remote.get("auth_policy"), dict)
                    and remote.get("auth_policy")
                    else None
                )
                if oauth_metadata is not None:
                    failed_evidence = HubEvidenceBundleV3(
                        **failed_fields,
                        oauth_policy_fingerprint=oauth_metadata.policy_fingerprint,
                        discovery_fingerprint=oauth_metadata.discovery_fingerprint,
                        registration_digest=oauth_metadata.registration_digest,
                        resource_digest=oauth_metadata.resource_digest,
                        scope_source=oauth_metadata.scope_source,
                        authorized_scopes=oauth_metadata.scopes,
                        authorized_scope_digest=oauth_metadata.scope_digest,
                        token_revision_digest=oauth_metadata.token_revision_digest,
                        protocol_version=oauth_metadata.protocol_version,
                        scope_assessment=oauth_scope_assessment
                        or assess_oauth_scopes(oauth_metadata.scopes),
                    )
                elif failed_policy is None:
                    failed_evidence: HubEvidenceBundle = HubEvidenceBundleV1(
                        **failed_fields
                    )
                else:
                    failed_evidence = HubEvidenceBundleV2(
                        **failed_fields,
                        auth_mode=failed_policy.mode,
                        header_name=failed_policy.header_name,
                        auth_policy_fingerprint=failed_policy.policy_fingerprint,
                        credential_revision_digest=(
                            binding_revision_digest
                            or stable_digest({"binding_revision": "unavailable"})
                        ),
                    )
                self.store.set_item(
                    item_id,
                    state="blocked",
                    evidence=failed_evidence.model_dump(mode="json"),
                    evidence_digest=failed_evidence.evidence_digest,
                    error_code=exc.code,
                )
            else:
                self.store.set_item(item_id, state="blocked", error_code=exc.code)
        except Exception:
            if not self._cancel_item_if_requested(run_id, item_id):
                self.store.set_item(item_id, state="failed", error_code="hub_review_internal_error")

    def _generate_proposal(
        self,
        run_id: str,
        item_id: str,
        tools: list[dict[str, Any]],
        effects: dict[str, str],
        schema_digest: str,
    ) -> dict[str, Any] | None:
        for tool in sorted(tools, key=lambda entry: str(entry.get("name") or "")):
            name = str(tool.get("name") or "")
            if effects.get(name) != "read_candidate":
                continue
            arguments = deterministic_arguments(dict(tool.get("input_schema") or {}))
            if arguments is None:
                continue
            return self.store.create_proposal(
                run_id=run_id,
                item_id=item_id,
                tenant_id=self.tenant_id,
                owner_id=self.owner_id,
                tool_name=name,
                arguments=arguments,
                schema_digest=str(tool.get("schema_digest") or ""),
            )
        return None

    def _require_evidence_auth_current(
        self,
        item: dict[str, Any],
        evidence: HubEvidenceBundle,
    ) -> None:
        if isinstance(evidence, HubEvidenceBundleV3):
            candidate = self.hub.store.require_candidate(
                str(item.get("candidate_id") or ""),
                self.tenant_id,
                self.owner_id,
            )
            try:
                current = self.hub._require_oauth_authorization().execution_metadata(
                    target_type="hub_candidate",
                    target_id=candidate["candidate_id"],
                    source_digest=candidate["source_digest"],
                )
            except RemoteOAuthError as exc:
                raise HubError(
                    str(exc), code=exc.code, status_code=exc.status_code
                ) from None
            if (
                current.policy_fingerprint != evidence.oauth_policy_fingerprint
                or current.discovery_fingerprint != evidence.discovery_fingerprint
                or current.registration_digest != evidence.registration_digest
                or current.resource_digest != evidence.resource_digest
                or current.scope_digest != evidence.authorized_scope_digest
                or current.token_revision_digest != evidence.token_revision_digest
                or current.protocol_version != evidence.protocol_version
                or current.scopes != evidence.authorized_scopes
            ):
                raise HubError(
                    "OAuth Token、Scope 或发现证据已变化，需要重新复核。",
                    code="mcp_remote_oauth_contract_scope_drift",
                    status_code=409,
                )
            return
        if not isinstance(evidence, HubEvidenceBundleV2):
            return
        candidate = self.hub.store.require_candidate(
            str(item.get("candidate_id") or ""),
            self.tenant_id,
            self.owner_id,
        )
        policy = self.hub._candidate_auth_policy(candidate)
        binding_id = str(candidate.get("auth_binding_id") or "")
        if policy is None or not binding_id or self.hub.remote_auth_broker is None:
            raise HubError(
                "静态 Token 复核凭据已失效。",
                code="mcp_remote_auth_binding_missing",
                status_code=409,
            )
        try:
            binding = self.hub.remote_auth_broker.get_binding(
                binding_id,
                current_policy=policy,
                target_type="hub_candidate",
                target_id=candidate["candidate_id"],
            )
        except RemoteAuthError as exc:
            raise HubError(str(exc), code=exc.code, status_code=exc.status_code) from None
        current_digest = stable_digest(
            {
                "binding_id": binding.binding_id,
                "revision": binding.revision,
                "policy_fingerprint": binding.policy_fingerprint,
            }
        )
        if (
            policy.policy_fingerprint != evidence.auth_policy_fingerprint
            or current_digest != evidence.credential_revision_digest
        ):
            raise HubError(
                "静态 Token 绑定 revision 已变化，需要重新复核。",
                code="mcp_remote_auth_binding_stale",
                status_code=409,
            )

    def generate_proposal(self, run_id: str, item_id: str) -> dict[str, Any]:
        self._require_enabled()
        self._require_run_not_cancelled(run_id, item_id)
        item = self.store.require_item(run_id, item_id, self.tenant_id, self.owner_id)
        if item["state"] != "awaiting_call_approval":
            raise HubError(
                "复核项当前不可生成代表调用提案。",
                code="hub_review_state_conflict",
                status_code=409,
            )
        proposal = item.get("proposal")
        if proposal is None:
            raise HubError(
                "该候选没有满足门禁的确定性代表调用。",
                code="manual_call_unavailable",
                status_code=409,
            )
        return proposal

    async def approve_proposal(
        self, run_id: str, item_id: str, proposal_id: str, expected_digest: str
    ) -> dict[str, Any]:
        self._require_enabled()
        self._require_run_not_cancelled(run_id, item_id)
        item = self.store.require_item(run_id, item_id, self.tenant_id, self.owner_id)
        proposal = self.store.require_proposal(proposal_id, self.tenant_id, self.owner_id)
        if proposal["run_id"] != run_id or proposal["item_id"] != item_id:
            raise HubError("代表调用提案范围不匹配。", code="hub_review_proposal_scope", status_code=409)
        if proposal["proposal_digest"] != expected_digest or proposal["state"] != "proposed":
            raise HubError("代表调用提案摘要无效或已使用。", code="hub_review_proposal_digest", status_code=409)
        if item["state"] != "awaiting_call_approval":
            raise HubError("复核项当前不可批准代表调用。", code="hub_review_state_conflict", status_code=409)
        lock = self._item_locks.setdefault(item_id, asyncio.Lock())
        async with lock:
            item = self.store.require_item(run_id, item_id, self.tenant_id, self.owner_id)
            proposal = self.store.require_proposal(proposal_id, self.tenant_id, self.owner_id)
            if proposal["state"] != "proposed":
                raise HubError("代表调用批准不可重放。", code="hub_review_call_replay", status_code=409)
            candidate_id = str(item.get("candidate_id") or "")
            evidence = _normalize_evidence(item["evidence"])
            self._require_evidence_auth_current(item, evidence)
            candidate = (
                await self.hub.preflight_oauth_review(
                    candidate_id,
                    expected_oauth_context=(
                        {
                            "policy_fingerprint": evidence.oauth_policy_fingerprint,
                            "scope_digest": evidence.authorized_scope_digest,
                            "token_revision_digest": evidence.token_revision_digest,
                            "resource_digest": evidence.resource_digest,
                            "discovery_fingerprint": evidence.discovery_fingerprint,
                            "registration_digest": evidence.registration_digest,
                        }
                        if isinstance(evidence, HubEvidenceBundleV3)
                        else None
                    ),
                )
                if isinstance(evidence, HubEvidenceBundleV3)
                else await self.hub.preflight(candidate_id)
            )
            self._require_run_not_cancelled(run_id, item_id)
            current_tool_digests = {
                str(tool["name"]): str(tool["schema_digest"])
                for tool in candidate.get("tools") or []
            }
            if (
                candidate.get("schema_digest") != evidence.schema_digest
                or current_tool_digests != evidence.tool_schema_digests
                or current_tool_digests.get(proposal["tool_name"]) != proposal["schema_digest"]
            ):
                self.store.set_item(item_id, state="drifted", error_code="hub_schema_drift")
                raise HubError("远程工具 Schema 已漂移。", code="hub_schema_drift", status_code=409)
            started = False
            preview = ""
            hub_lock = self.hub._candidate_locks.setdefault(candidate_id, asyncio.Lock())
            async with hub_lock:
                await self.hub._disconnect_live(candidate_id)
                try:
                    raw_candidate = self.hub.store.require_candidate(
                        candidate_id, self.tenant_id, self.owner_id
                    )
                    live = await self.hub._open_candidate(
                        raw_candidate,
                        allow_oauth_review=isinstance(evidence, HubEvidenceBundleV3),
                        expected_oauth_context=(
                            {
                                "policy_fingerprint": evidence.oauth_policy_fingerprint,
                                "scope_digest": evidence.authorized_scope_digest,
                                "token_revision_digest": evidence.token_revision_digest,
                                "resource_digest": evidence.resource_digest,
                                "discovery_fingerprint": evidence.discovery_fingerprint,
                                "registration_digest": evidence.registration_digest,
                            }
                            if isinstance(evidence, HubEvidenceBundleV3)
                            else None
                        ),
                    )
                    refreshed = await self.hub.bridge.list_tools(live.session_id)
                    refreshed_tools, refreshed_digest = self.hub._validate_tools(refreshed.get("tools"))
                    if (
                        refreshed_digest != evidence.schema_digest
                        or {tool["name"]: tool["schema_digest"] for tool in refreshed_tools}
                        != evidence.tool_schema_digests
                    ):
                        raise HubError("远程工具 Schema 已漂移。", code="hub_schema_drift", status_code=409)
                    self._require_run_not_cancelled(run_id, item_id)
                    self.store.begin_call(proposal, candidate_id)
                    self.store.set_proposal_state(proposal_id, "started")
                    self._event(run_id, item_id, "call_approval", payload={"proposal_digest": expected_digest})
                    self._event(run_id, item_id, "representative_call", "started")
                    started = True
                    response = await asyncio.wait_for(
                        self.hub.bridge.call(
                            live.session_id,
                            proposal["tool_name"],
                            dict(proposal["arguments"]),
                        ),
                        timeout=CALL_TIMEOUT_SECONDS,
                    )
                    result = response.get("result")
                    if not isinstance(result, dict):
                        raise HubError("远程结果结构无效。", code="hub_result_denied", status_code=502)
                    result_bytes = _json_bytes(result)
                    if len(result_bytes) > MAX_RESULT_BYTES:
                        raise HubError("远程结果超过大小上限。", code="hub_result_denied", status_code=502)
                    assertions = {
                        "result_is_object": True,
                        "remote_reported_error": bool(result.get("isError") or result.get("is_error")),
                    }
                    result_type = "mcp-content" if isinstance(result.get("content"), list) else "object"
                    self.store.finish_call(
                        proposal_id,
                        state="completed",
                        result_digest=stable_digest(result),
                        result_size=len(result_bytes),
                        result_type=result_type,
                        assertions=assertions,
                    )
                    self.store.set_proposal_state(proposal_id, "completed")
                    preview = _redacted_preview(result)
                    evidence_payload = evidence.model_dump(mode="json")
                    evidence_payload["representative_call"] = {
                        "proposal_digest": proposal["proposal_digest"],
                        "tool_name": proposal["tool_name"],
                        "arguments_digest": proposal["arguments_digest"],
                        "result_digest": stable_digest(result),
                        "result_size": len(result_bytes),
                        "result_type": result_type,
                        "assertions": assertions,
                    }
                    evidence_payload["cleanup"] = {
                        "temporary_session_closed": True,
                        "capability_revoked": True,
                    }
                    updated_evidence = _normalize_evidence(evidence_payload)
                    next_state = "blocked" if assertions["remote_reported_error"] else "awaiting_decision"
                    next_error = "hub_review_representative_call_error" if assertions["remote_reported_error"] else ""
                    self.store.set_item(
                        item_id,
                        state=next_state,
                        evidence=updated_evidence.model_dump(mode="json"),
                        evidence_digest=updated_evidence.evidence_digest,
                        error_code=next_error,
                    )
                    self._event(
                        run_id,
                        item_id,
                        "representative_call",
                        "passed" if not assertions["remote_reported_error"] else "failed",
                        error_code=next_error,
                        payload={
                            "result_digest": stable_digest(result),
                            "result_size": len(result_bytes),
                            "result_type": result_type,
                        },
                    )
                except Exception as exc:
                    if started:
                        self.store.finish_call(
                            proposal_id,
                            state="unknown_outcome",
                            error_code="unknown_outcome",
                        )
                        self.store.set_proposal_state(proposal_id, "unknown_outcome")
                        self.store.set_item(item_id, state="unknown_outcome", error_code="unknown_outcome")
                        self.hub.store.update_candidate(
                            candidate_id,
                            self.tenant_id,
                            self.owner_id,
                            state="tainted",
                            taint_reason="unknown_outcome",
                        )
                    elif isinstance(exc, HubError) and exc.code != "hub_review_cancelled":
                        self.store.set_item(item_id, state="drifted", error_code=exc.code)
                    if started:
                        raise HubUnknownOutcomeError() from exc
                    raise
                finally:
                    await self.hub._disconnect_live(candidate_id)
                    self._event(run_id, item_id, "cleanup", payload={"session_closed": True, "capability_revoked": True})
            self._refresh_run_status(run_id)
            return {
                "proposal_id": proposal_id,
                "state": self.store.require_item(run_id, item_id, self.tenant_id, self.owner_id)["state"],
                "preview": preview,
                "preview_truncated_at_bytes": MAX_TRANSIENT_PREVIEW_BYTES,
            }

    def decide(
        self,
        run_id: str,
        item_id: str,
        *,
        decision: str,
        expected_evidence_digest: str,
        allowed_tools: list[str],
        tool_effects: dict[str, str],
        acknowledge_unknown_oauth_scopes: bool = False,
    ) -> dict[str, Any]:
        self._require_enabled()
        self._require_run_not_cancelled(run_id, item_id)
        item = self.store.require_item(run_id, item_id, self.tenant_id, self.owner_id)
        if decision == "block":
            if item["evidence_digest"] != expected_evidence_digest:
                raise HubError("复核证据摘要已变化。", code="hub_review_evidence_digest", status_code=409)
            self.store.set_item(item_id, state="blocked", error_code="hub_review_human_blocked")
            self._event(run_id, item_id, "human_decision", "blocked")
            self._refresh_run_status(run_id)
            return self.store.require_item(run_id, item_id, self.tenant_id, self.owner_id)
        if decision != "approve" or item["state"] != "awaiting_decision":
            raise HubError("复核项当前不可批准。", code="hub_review_state_conflict", status_code=409)
        if item["evidence_digest"] != expected_evidence_digest:
            raise HubError("复核证据摘要已变化。", code="hub_review_evidence_digest", status_code=409)
        evidence = _normalize_evidence(item["evidence"])
        self._require_evidence_auth_current(item, evidence)
        if isinstance(evidence, HubEvidenceBundleV3):
            assessment = evidence.scope_assessment
            if assessment.get("dangerous_scopes"):
                raise HubError(
                    "OAuth Scope 含高危写入或控制语义，本轮禁止发布。",
                    code="mcp_remote_oauth_contract_scope_drift",
                    status_code=409,
                )
            if assessment.get("unknown_scopes") and not acknowledge_unknown_oauth_scopes:
                raise HubError(
                    "OAuth Scope 含未知语义，必须由本地运维者显式确认。",
                    code="mcp_remote_oauth_scope_ack_required",
                    status_code=409,
                )
        unique_tools = list(dict.fromkeys(str(name) for name in allowed_tools))
        if (
            not unique_tools
            or set(unique_tools) != set(tool_effects)
            or not set(unique_tools).issubset(evidence.tool_schema_digests)
            or any(tool_effects[name] != "read" for name in unique_tools)
        ):
            raise HubError(
                "V1 只允许人工确认为 read 的冻结工具子集。",
                code="hub_review_effect_denied",
                status_code=409,
            )
        existing, reason = self.contracts.lookup_identity(
            evidence.snapshot.server_name,
            evidence.snapshot.version,
            evidence.snapshot.remote_url,
        )
        if existing is not None and not reason:
            expected_policy = None
            if isinstance(evidence, HubEvidenceBundleV2):
                expected_policy = self.hub._candidate_auth_policy(
                    self.hub.store.require_candidate(
                        str(item.get("candidate_id") or ""),
                        self.tenant_id,
                        self.owner_id,
                    )
                )
            expected_oauth_policy = (
                self._current_oauth_policy(item, evidence)
                if isinstance(evidence, HubEvidenceBundleV3)
                else None
            )
            if (
                existing.schema_digest != evidence.schema_digest
                or existing.tool_schema_digests != evidence.tool_schema_digests
                or set(existing.allowed_tools) != set(unique_tools)
                or existing.tool_effects != tool_effects
                or getattr(existing, "remote_auth_policy", None) != expected_policy
                or getattr(existing, "remote_oauth_policy", None)
                != expected_oauth_policy
                or getattr(existing, "authorized_scopes", ())
                != (
                    evidence.authorized_scopes
                    if isinstance(evidence, HubEvidenceBundleV3)
                    else ()
                )
            ):
                raise HubError("同一身份的仓库契约不一致。", code="hub_contract_collision", status_code=409)
            contract = existing
        else:
            contract_fields: dict[str, Any] = {
                "contract_id": stable_contract_id(
                    evidence.snapshot.server_name,
                    evidence.snapshot.version,
                    evidence.snapshot.remote_url,
                ),
                "server_name": evidence.snapshot.server_name,
                "version": evidence.snapshot.version,
                "remote_url": evidence.snapshot.remote_url,
                "origin": evidence.snapshot.origin,
                "source_digest": evidence.snapshot.source_digest,
                "schema_digest": evidence.schema_digest,
                "tool_schema_digests": evidence.tool_schema_digests,
                "allowed_tools": sorted(unique_tools),
                "tool_effects": {name: "read" for name in sorted(unique_tools)},
                "limits": {
                    "max_arguments_bytes": 32 * 1024,
                    "max_result_bytes": MAX_RESULT_BYTES,
                    "call_timeout_seconds": int(CALL_TIMEOUT_SECONDS),
                    "max_concurrency": 1,
                },
                "evidence_digest": evidence.evidence_digest,
                "published_at": 0.0,
            }
            if isinstance(evidence, HubEvidenceBundleV2):
                candidate = self.hub.store.require_candidate(
                    str(item.get("candidate_id") or ""),
                    self.tenant_id,
                    self.owner_id,
                )
                policy = self.hub._candidate_auth_policy(candidate)
                if policy is None or policy.policy_fingerprint != evidence.auth_policy_fingerprint:
                    raise HubError(
                        "远程认证策略已漂移。",
                        code="mcp_remote_auth_binding_stale",
                        status_code=409,
                    )
                contract = HubReviewedContractV2(
                    **contract_fields,
                    remote_auth_policy=policy,
                )
            elif isinstance(evidence, HubEvidenceBundleV3):
                policy = self._current_oauth_policy(item, evidence)
                contract = HubReviewedContractV3(
                    **contract_fields,
                    remote_oauth_policy=policy,
                    authorized_scopes=evidence.authorized_scopes,
                    authorized_scope_digest=evidence.authorized_scope_digest,
                    protocol_version=evidence.protocol_version,
                )
            else:
                contract = HubReviewedContractV1(**contract_fields)
        self.store.set_item(
            item_id,
            state="approved",
            draft_contract=contract.model_dump(mode="json"),
            contract_fingerprint=contract.contract_fingerprint,
            error_code="",
        )
        self._event(
            run_id,
            item_id,
            "human_decision",
            payload={
                "decision": "approve",
                "contract_fingerprint": contract.contract_fingerprint,
                "unknown_oauth_scopes_acknowledged": bool(
                    isinstance(evidence, HubEvidenceBundleV3)
                    and evidence.scope_assessment.get("unknown_scopes")
                    and acknowledge_unknown_oauth_scopes
                ),
            },
        )
        self._refresh_run_status(run_id)
        return self.store.require_item(run_id, item_id, self.tenant_id, self.owner_id)

    def _current_oauth_policy(
        self,
        item: dict[str, Any],
        evidence: HubEvidenceBundleV3,
    ) -> RemoteOAuthPolicyV2:
        candidate = self.hub.store.require_candidate(
            str(item.get("candidate_id") or ""),
            self.tenant_id,
            self.owner_id,
        )
        oauth = self.hub._require_remote_oauth()
        subject = oauth.subject_resolver.resolve()
        if (
            subject.tenant_id != self.tenant_id
            or subject.owner_id != self.owner_id
        ):
            raise HubError(
                "OAuth 复核主体与 Hub Owner 不一致。",
                code="mcp_remote_oauth_scope_denied",
                status_code=403,
            )
        discovery = oauth.store.active_discovery(
            subject=subject,
            target_type="hub_candidate",
            target_id=candidate["candidate_id"],
        )
        if (
            discovery is None
            or not isinstance(discovery.policy, RemoteOAuthPolicyV2)
            or discovery.policy.policy_fingerprint
            != evidence.oauth_policy_fingerprint
        ):
            raise HubError(
                "OAuth 发现策略已漂移。",
                code="mcp_remote_oauth_contract_scope_drift",
                status_code=409,
            )
        return discovery.policy

    def publish(self, run_id: str, item_id: str, expected_fingerprint: str) -> dict[str, Any]:
        self._require_enabled()
        self._require_run_not_cancelled(run_id, item_id)
        if not local_contract_publish_enabled():
            raise HubError("本机契约发布当前未启用。", code="hub_local_contract_publish_disabled", status_code=503)
        if not self.signing_key:
            raise HubError("本机契约签名密钥未配置。", code="hub_contract_signing_key_missing", status_code=503)
        item = self.store.require_item(run_id, item_id, self.tenant_id, self.owner_id)
        if item["state"] not in {"approved", "published"} or item["contract_fingerprint"] != expected_fingerprint:
            raise HubError("契约指纹无效或复核项未批准。", code="hub_contract_fingerprint_mismatch", status_code=409)
        self._require_evidence_auth_current(
            item,
            _normalize_evidence(item["evidence"]),
        )
        contract = normalize_contract(item["draft_contract"])
        signature = contract_signature(contract, self.signing_key)
        revision = self.store.add_local_contract_revision(
            self.tenant_id, self.owner_id, contract, signature
        )
        self.store.add_revocation(
            self.tenant_id, self.owner_id, contract.contract_id, "restore", "published revision"
        )
        self.store.set_item(item_id, state="published")
        self._event(
            run_id,
            item_id,
            "contract_publish",
            payload={"contract_id": contract.contract_id, "revision_id": revision["revision_id"]},
        )
        self._refresh_run_status(run_id)
        oauth_contract = isinstance(contract, HubReviewedContractV3)
        return {
            **revision,
            "activation_eligible": not oauth_contract,
            "activation_reason": (
                "mcp_remote_oauth_runtime_disabled" if oauth_contract else ""
            ),
        }

    def export_contract(self, run_id: str, item_id: str) -> bytes:
        item = self.store.require_item(run_id, item_id, self.tenant_id, self.owner_id)
        if item["state"] not in {"approved", "published", "revoked"} or not item["draft_contract"]:
            raise HubError("当前没有可导出的契约草案。", code="hub_contract_export_unavailable", status_code=409)
        return contract_export(normalize_contract(item["draft_contract"]))

    async def revoke(self, contract_id: str, reason: str = "") -> dict[str, Any]:
        self._require_enabled()
        contract, lookup_reason = self.contracts.get_contract(contract_id)
        if contract is None:
            raise HubError("契约不存在。", code=lookup_reason, status_code=404)
        self.store.add_revocation(
            self.tenant_id, self.owner_id, contract.contract_id, "revoke", reason
        )
        disconnected = 0
        for candidate in self.hub.store.list_candidates(self.tenant_id, self.owner_id):
            if (
                candidate["server_name"],
                candidate["version"],
                candidate["remote_url"],
            ) == contract.identity:
                await self.hub.disconnect(candidate["candidate_id"])
                disconnected += 1
        if self.hub.trusted_service is not None:
            self.hub.trusted_service.record_runtime_event(
                "contract_revoked",
                {"contract_id": contract.contract_id},
            )
        return {
            "contract_id": contract.contract_id,
            "revoked": True,
            "disconnected_candidates": disconnected,
        }

    def resume(self, run_id: str) -> dict[str, Any]:
        self._require_enabled()
        run = self.store.require_run(run_id, self.tenant_id, self.owner_id)
        if run["status"] not in {"interrupted", "failed", "queued"}:
            raise HubError("复核批次当前不可恢复。", code="hub_review_resume_denied", status_code=409)
        if self._unsafe_resume_items(run):
            raise HubError(
                "复核批次停在不可安全重试的阶段，禁止恢复。",
                code="hub_review_resume_unsafe_stage",
                status_code=409,
            )
        self.store.set_run(run_id, status="queued", error_code="")
        self._schedule(run_id)
        return self.store.require_run(run_id, self.tenant_id, self.owner_id)

    def cancel(self, run_id: str) -> dict[str, Any]:
        self._require_enabled()
        self.store.request_cancel(run_id, self.tenant_id, self.owner_id)
        self._refresh_run_status(run_id)
        return self.store.require_run(run_id, self.tenant_id, self.owner_id)

    def _refresh_run_status(self, run_id: str) -> None:
        run = self.store.require_run(run_id, self.tenant_id, self.owner_id)
        if run["cancel_requested"]:
            call_in_progress = False
            for item in run["items"]:
                if (item.get("proposal") or {}).get("state") == "started":
                    call_in_progress = True
                    continue
                if item["state"] not in {
                    "blocked",
                    "cancelled",
                    "drifted",
                    "published",
                    "revoked",
                    "unknown_outcome",
                }:
                    self.store.set_item(item["item_id"], state="cancelled", error_code="hub_review_cancelled")
            self.store.set_run(run_id, status="running" if call_in_progress else "cancelled")
            return
        states = {item["state"] for item in run["items"]}
        if states & {"queued", "running", "interrupted"}:
            status = "running"
        elif states & {"awaiting_call_approval", "awaiting_decision", "approved"}:
            status = "awaiting_operator"
        else:
            status = "completed"
        self.store.set_run(run_id, status=status)
        if status == "completed":
            self._schedule_next_queued()

    @staticmethod
    def _unsafe_resume_items(run: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            item
            for item in run["items"]
            if item["state"] in {"running", "interrupted", "failed"}
            and item.get("current_stage")
            and not SAFE_TO_RETRY.get(str(item["current_stage"]), False)
        ]

    def _cancel_item_if_requested(self, run_id: str, item_id: str) -> bool:
        run = self.store.require_run(run_id, self.tenant_id, self.owner_id)
        if not run["cancel_requested"]:
            return False
        item = self.store.require_item(
            run_id, item_id, self.tenant_id, self.owner_id
        )
        if (item.get("proposal") or {}).get("state") == "started":
            return False
        if item["state"] not in {
            "blocked",
            "cancelled",
            "drifted",
            "published",
            "revoked",
            "unknown_outcome",
        }:
            self.store.set_item(
                item_id,
                state="cancelled",
                error_code="hub_review_cancelled",
            )
        return True

    def _require_run_not_cancelled(self, run_id: str, item_id: str) -> None:
        run = self.store.require_run(run_id, self.tenant_id, self.owner_id)
        if not run["cancel_requested"]:
            return
        self._cancel_item_if_requested(run_id, item_id)
        raise HubError(
            "复核批次已取消，禁止继续执行。",
            code="hub_review_cancelled",
            status_code=409,
        )

    async def reconcile_registry_drift(self) -> None:
        if not review_factory_enabled():
            return
        for contract in self.contracts.describe():
            if contract.get("collision") or contract.get("revoked"):
                continue
            server = self.hub.store.get_server(contract["server_name"], contract["version"])
            remote = next(
                (
                    item
                    for item in (server or {}).get("remotes", [])
                    if item.get("url") == contract["remote_url"]
                ),
                None,
            )
            source_mismatch = bool(
                contract.get("source_digest")
                and (server or {}).get("source_digest") != contract["source_digest"]
            )
            if server is None or remote is None or source_mismatch:
                for candidate in self.hub.store.list_candidates(self.tenant_id, self.owner_id):
                    if (
                        candidate["server_name"],
                        candidate["version"],
                        candidate["remote_url"],
                    ) == (contract["server_name"], contract["version"], contract["remote_url"]):
                        self.hub.store.update_candidate(
                            candidate["candidate_id"],
                            self.tenant_id,
                            self.owner_id,
                            state="drifted",
                            taint_reason="hub_source_drift",
                        )
                        await self.hub._disconnect_live(candidate["candidate_id"])
                        if self.hub.trusted_service is not None:
                            self.hub.trusted_service.record_runtime_event(
                                "contract_drifted",
                                {
                                    "contract_id": contract["contract_id"],
                                    "candidate_id": candidate["candidate_id"],
                                },
                                outcome_code="hub_source_drift",
                            )
                if self.store.has_review_identity(
                    self.tenant_id,
                    self.owner_id,
                    contract["server_name"],
                    contract["version"],
                ):
                    continue
                replacement = next(
                    (
                        item
                        for item in (server or {}).get("remotes", [])
                        if item.get("eligibility") == "eligible"
                    ),
                    None,
                )
                if replacement is not None:
                    self.store.create_run(
                        self.tenant_id,
                        self.owner_id,
                        [
                            {
                                "server_name": contract["server_name"],
                                "version": contract["version"],
                                "remote_id": replacement["remote_id"],
                            }
                        ],
                        allow_queued_when_busy=True,
                    )
                    self._schedule_next_queued()
                else:
                    self.store.create_drift_record(
                        self.tenant_id,
                        self.owner_id,
                        server_name=contract["server_name"],
                        version=contract["version"],
                        remote_id="remote_" + stable_digest(contract["contract_id"])[:16],
                        error_code="hub_source_drift",
                    )

    def reproducible_registry_selection(
        self, limit: int = MAX_REVIEW_ITEMS, seed: str = "hub-review-factory-v1"
    ) -> list[dict[str, str]]:
        items, _ = self.hub.store.list_servers(limit=50_000, offset=0)
        candidates: list[tuple[str, dict[str, str], str, str]] = []
        for server in items:
            if not server.get("is_latest") or server.get("status") not in {"active", "published"}:
                continue
            for remote in server.get("remotes") or []:
                if remote.get("eligibility") != "eligible":
                    continue
                identity = {
                    "server_name": server["server_name"],
                    "version": server["version"],
                    "remote_id": remote["remote_id"],
                }
                rank = stable_digest({"seed": seed, "source_digest": server["source_digest"], **identity})
                publisher = str(server.get("publisher") or "").strip()
                publisher_key = publisher or str(server["server_name"]).split("/", 1)[0]
                candidates.append((rank, identity, publisher_key, remote["origin"]))
        publisher_counts: dict[str, int] = {}
        origins: set[str] = set()
        selected: list[dict[str, str]] = []
        for _rank, identity, publisher, origin in sorted(candidates, key=lambda item: item[0]):
            if origin in origins or publisher_counts.get(publisher, 0) >= 2:
                continue
            origins.add(origin)
            publisher_counts[publisher] = publisher_counts.get(publisher, 0) + 1
            selected.append(identity)
            if len(selected) >= limit:
                break
        return selected

    def reproducible_static_token_selection(
        self,
        limit: int = 1,
        seed: str = "mcp-static-token-r1",
    ) -> list[dict[str, str]]:
        items, _ = self.hub.store.list_servers(limit=50_000, offset=0)
        ranked: list[tuple[str, dict[str, str], str, str]] = []
        for server in items:
            if not server.get("is_latest") or server.get("status") not in {
                "active",
                "published",
            }:
                continue
            for remote in server.get("remotes") or []:
                if remote.get("eligibility") != "static_token_candidate":
                    continue
                identity = {
                    "server_name": server["server_name"],
                    "version": server["version"],
                    "remote_id": remote["remote_id"],
                }
                publisher = str(server.get("publisher") or "").strip()
                publisher_key = publisher or str(server["server_name"]).split("/", 1)[0]
                rank = stable_digest(
                    {
                        "seed": seed,
                        "source_digest": server["source_digest"],
                        **identity,
                    }
                )
                ranked.append((rank, identity, publisher_key, remote["origin"]))
        selected: list[dict[str, str]] = []
        publishers: set[str] = set()
        origins: set[str] = set()
        for _rank, identity, publisher, origin in sorted(ranked):
            if publisher in publishers or origin in origins:
                continue
            publishers.add(publisher)
            origins.add(origin)
            selected.append(identity)
            if len(selected) >= max(0, min(int(limit), MAX_REVIEW_ITEMS)):
                break
        return selected


class ReviewIdentityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    server_name: str = Field(min_length=1, max_length=255)
    version: str = Field(min_length=1, max_length=255)
    remote_id: str = Field(min_length=1, max_length=40)


class ReviewRunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[ReviewIdentityRequest] = Field(min_length=1, max_length=MAX_REVIEW_ITEMS)


class ProposalApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_proposal_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReviewDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "block"]
    expected_evidence_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    allowed_tools: list[str] = Field(default_factory=list, max_length=50)
    tool_effects: dict[str, Literal["read"]] = Field(default_factory=dict)
    acknowledge_unknown_oauth_scopes: bool = False


class ContractPublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_contract_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class ContractRevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(default="", max_length=500)


router = APIRouter(tags=["mcp-hub-review-factory"])
_review_service: MCPHubReviewService | None = None


def configure_mcp_hub_review(service: MCPHubReviewService) -> None:
    global _review_service
    _review_service = service


def _service() -> MCPHubReviewService:
    if _review_service is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "hub_review_unconfigured", "error": "MCP Hub 复核工厂尚未配置。"},
        )
    return _review_service


def _raise_http(exc: HubError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "error": str(exc)},
    ) from exc


@router.get("/api/mcp/hub/reviews/status")
async def review_status() -> dict[str, Any]:
    return _service().status()


@router.post("/api/mcp/hub/review-runs", status_code=201)
async def create_review_run(payload: ReviewRunCreateRequest) -> dict[str, Any]:
    try:
        return _service().create_run([item.model_dump() for item in payload.items])
    except HubError as exc:
        _raise_http(exc)


@router.get("/api/mcp/hub/review-runs")
async def list_review_runs() -> dict[str, Any]:
    service = _service()
    try:
        service._require_enabled()
        items = service.store.list_runs(service.tenant_id, service.owner_id)
        return {"items": items, "total": len(items)}
    except HubError as exc:
        _raise_http(exc)


@router.get("/api/mcp/hub/review-runs/{run_id}")
async def get_review_run(run_id: str) -> dict[str, Any]:
    service = _service()
    try:
        service._require_enabled()
        clean = _required_identifier(run_id, REVIEW_RUN_ID_RE, "run_id")
        return service.store.require_run(clean, service.tenant_id, service.owner_id)
    except HubError as exc:
        _raise_http(exc)


@router.post("/api/mcp/hub/review-runs/{run_id}/resume")
async def resume_review_run(run_id: str) -> dict[str, Any]:
    try:
        return _service().resume(_required_identifier(run_id, REVIEW_RUN_ID_RE, "run_id"))
    except HubError as exc:
        _raise_http(exc)


@router.post("/api/mcp/hub/review-runs/{run_id}/cancel")
async def cancel_review_run(run_id: str) -> dict[str, Any]:
    try:
        return _service().cancel(_required_identifier(run_id, REVIEW_RUN_ID_RE, "run_id"))
    except HubError as exc:
        _raise_http(exc)


@router.post("/api/mcp/hub/review-runs/{run_id}/items/{item_id}/call-proposals")
async def create_call_proposal(
    run_id: str, item_id: str, request: Request
) -> dict[str, Any]:
    try:
        if (await request.body()).strip():
            raise HubError(
                "代表调用提案不接受客户端参数。",
                code="hub_review_arbitrary_arguments_denied",
                status_code=422,
            )
        return _service().generate_proposal(
            _required_identifier(run_id, REVIEW_RUN_ID_RE, "run_id"),
            _required_identifier(item_id, REVIEW_ITEM_ID_RE, "item_id"),
        )
    except HubError as exc:
        _raise_http(exc)


@router.post(
    "/api/mcp/hub/review-runs/{run_id}/items/{item_id}/call-proposals/{proposal_id}/approve"
)
async def approve_call_proposal(
    run_id: str,
    item_id: str,
    proposal_id: str,
    payload: ProposalApproveRequest,
) -> dict[str, Any]:
    try:
        return await _service().approve_proposal(
            _required_identifier(run_id, REVIEW_RUN_ID_RE, "run_id"),
            _required_identifier(item_id, REVIEW_ITEM_ID_RE, "item_id"),
            _required_identifier(proposal_id, PROPOSAL_ID_RE, "proposal_id"),
            payload.expected_proposal_digest,
        )
    except HubError as exc:
        _raise_http(exc)


@router.post("/api/mcp/hub/review-runs/{run_id}/items/{item_id}/decision")
async def decide_review_item(
    run_id: str, item_id: str, payload: ReviewDecisionRequest
) -> dict[str, Any]:
    try:
        return _service().decide(
            _required_identifier(run_id, REVIEW_RUN_ID_RE, "run_id"),
            _required_identifier(item_id, REVIEW_ITEM_ID_RE, "item_id"),
            decision=payload.decision,
            expected_evidence_digest=payload.expected_evidence_digest,
            allowed_tools=payload.allowed_tools,
            tool_effects=dict(payload.tool_effects),
            acknowledge_unknown_oauth_scopes=(
                payload.acknowledge_unknown_oauth_scopes
            ),
        )
    except HubError as exc:
        _raise_http(exc)


@router.post("/api/mcp/hub/review-runs/{run_id}/items/{item_id}/publish")
async def publish_review_contract(
    run_id: str, item_id: str, payload: ContractPublishRequest
) -> dict[str, Any]:
    try:
        return _service().publish(
            _required_identifier(run_id, REVIEW_RUN_ID_RE, "run_id"),
            _required_identifier(item_id, REVIEW_ITEM_ID_RE, "item_id"),
            payload.expected_contract_fingerprint,
        )
    except HubError as exc:
        _raise_http(exc)


@router.get("/api/mcp/hub/review-runs/{run_id}/items/{item_id}/contract-export")
async def export_review_contract(run_id: str, item_id: str) -> Response:
    try:
        content = _service().export_contract(
            _required_identifier(run_id, REVIEW_RUN_ID_RE, "run_id"),
            _required_identifier(item_id, REVIEW_ITEM_ID_RE, "item_id"),
        )
        return Response(
            content=content,
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="hub-reviewed-contract.json"'},
        )
    except HubError as exc:
        _raise_http(exc)


@router.get("/api/mcp/hub/contracts")
async def list_hub_contracts() -> dict[str, Any]:
    service = _service()
    try:
        service._require_enabled()
        items = service.contracts.describe()
        return {"items": items, "total": len(items)}
    except HubError as exc:
        _raise_http(exc)


@router.post("/api/mcp/hub/contracts/{contract_id}/revoke")
async def revoke_hub_contract(
    contract_id: str, payload: ContractRevokeRequest
) -> dict[str, Any]:
    try:
        return await _service().revoke(contract_id, payload.reason)
    except HubError as exc:
        _raise_http(exc)
