"""Trusted reviewed-contract channel and bounded local Hub product evidence."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .hub import MCPHubService, MCPHubStore, HubError, stable_digest
from .hub_contracts import SOP_VERSION, HubReviewedContractV1
from .hub_review import MCPHubReviewService, review_factory_enabled


HEALTH_TTL_SECONDS = 24 * 60 * 60
MANUAL_REVALIDATE_INTERVAL_SECONDS = 10 * 60
MAINTENANCE_INTERVAL_SECONDS = 15 * 60
AUTO_REVIEW_INTERVAL_SECONDS = 24 * 60 * 60
EVENT_RETENTION_SECONDS = 90 * 24 * 60 * 60
MAX_OWNER_EVENTS = 50_000
MAX_AUTO_REVIEW_ITEMS = 20

TRUSTED_STATES = frozenset(
    {
        "ready",
        "stale",
        "degraded",
        "environment_blocked",
        "drifted",
        "revoked",
        "collision",
    }
)
PRODUCT_EVENT_TYPES = frozenset(
    {
        "trusted_list_view",
        "trusted_detail_view",
        "revalidate_started",
        "revalidate_succeeded",
        "revalidate_failed",
        "activation_started",
        "activation_succeeded",
        "activation_failed",
        "runtime_approval_shown",
        "runtime_approval_approved",
        "runtime_approval_rejected",
        "runtime_call_succeeded",
        "runtime_call_failed",
        "runtime_call_unknown_outcome",
        "candidate_disconnected",
        "contract_drifted",
        "contract_revoked",
    }
)
ENVIRONMENT_ERROR_CODES = frozenset(
    {
        "hub_dns_answer_invalid",
        "hub_dns_failed",
        "hub_dns_private_or_synthetic_denied",
        "hub_dns_timeout",
        "hub_egress_unavailable",
        "hub_peer_credentials_unavailable",
        "hub_peer_denied",
        "hub_proxy_unavailable",
        "hub_sidecar_unavailable",
        "hub_socket_path_unsafe",
    }
)
DRIFT_ERROR_CODES = frozenset(
    {
        "hub_source_drift",
        "hub_schema_drift",
        "hub_reviewed_contract_drift",
        "hub_contract_source_drift",
    }
)
STRUCTURAL_REVIEW_ERRORS = frozenset(
    {
        "hub_review_static_policy_denied",
        "hub_non_tool_capability_denied",
        "hub_tool_contract_denied",
        "hub_tools_capability_required",
        "manual_call_unavailable",
    }
)


def trusted_channel_enabled() -> bool:
    return os.getenv("MCP_HUB_TRUSTED_CHANNEL_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def auto_review_enabled() -> bool:
    return os.getenv("MCP_HUB_AUTO_REVIEW_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


class MCPHubTrustedStore:
    """Additive trusted-channel tables in the existing Hub SQLite database."""

    def __init__(self, hub_store: MCPHubStore | str | Path) -> None:
        self.path = Path(hub_store.path if isinstance(hub_store, MCPHubStore) else hub_store)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def _initialize(self) -> None:
        with self._lock, self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS hub_trusted_health (
                    tenant_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    contract_id TEXT NOT NULL,
                    contract_fingerprint TEXT NOT NULL,
                    state TEXT NOT NULL,
                    error_code TEXT NOT NULL DEFAULT '',
                    checked_at REAL NOT NULL,
                    next_check_at REAL NOT NULL,
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (tenant_id, owner_id, contract_id)
                );
                CREATE INDEX IF NOT EXISTS idx_hub_trusted_health_due
                    ON hub_trusted_health(tenant_id, owner_id, next_check_at);
                CREATE TABLE IF NOT EXISTS hub_auto_review_schedule (
                    tenant_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    identity_key TEXT NOT NULL,
                    source_digest TEXT NOT NULL,
                    sop_version TEXT NOT NULL,
                    last_run_id TEXT NOT NULL DEFAULT '',
                    last_result TEXT NOT NULL DEFAULT '',
                    error_code TEXT NOT NULL DEFAULT '',
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    last_attempt_at REAL NOT NULL DEFAULT 0,
                    next_attempt_at REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY (tenant_id, owner_id, identity_key)
                );
                CREATE INDEX IF NOT EXISTS idx_hub_auto_review_due
                    ON hub_auto_review_schedule(tenant_id, owner_id, next_attempt_at);
                CREATE TABLE IF NOT EXISTS hub_product_events (
                    event_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    contract_id TEXT NOT NULL DEFAULT '',
                    candidate_id TEXT NOT NULL DEFAULT '',
                    tool_digest TEXT NOT NULL DEFAULT '',
                    event_type TEXT NOT NULL,
                    outcome_code TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_hub_product_events_owner
                    ON hub_product_events(tenant_id, owner_id, created_at DESC);
                """
            )

    def health(self, tenant_id: str, owner_id: str, contract_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM hub_trusted_health WHERE tenant_id=? AND owner_id=? AND contract_id=?",
                (tenant_id, owner_id, contract_id),
            ).fetchone()
        return dict(row) if row is not None else None

    def set_health(
        self,
        tenant_id: str,
        owner_id: str,
        contract_id: str,
        contract_fingerprint: str,
        *,
        state: str,
        error_code: str = "",
        checked_at: float | None = None,
    ) -> dict[str, Any]:
        if state not in TRUSTED_STATES:
            raise ValueError("invalid trusted health state")
        now = time.time() if checked_at is None else float(checked_at)
        current = self.health(tenant_id, owner_id, contract_id)
        failures = 0 if state == "ready" else int((current or {}).get("consecutive_failures") or 0) + 1
        next_check = now + HEALTH_TTL_SECONDS
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO hub_trusted_health(tenant_id,owner_id,contract_id,contract_fingerprint,state,error_code,checked_at,next_check_at,consecutive_failures) "
                "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(tenant_id,owner_id,contract_id) DO UPDATE SET "
                "contract_fingerprint=excluded.contract_fingerprint,state=excluded.state,error_code=excluded.error_code,"
                "checked_at=excluded.checked_at,next_check_at=excluded.next_check_at,consecutive_failures=excluded.consecutive_failures",
                (
                    tenant_id,
                    owner_id,
                    contract_id,
                    contract_fingerprint,
                    state,
                    error_code[:120],
                    now,
                    next_check,
                    failures,
                ),
            )
        return self.health(tenant_id, owner_id, contract_id) or {}

    def record_event(
        self,
        tenant_id: str,
        owner_id: str,
        event_type: str,
        *,
        contract_id: str = "",
        candidate_id: str = "",
        tool_digest: str = "",
        outcome_code: str = "",
    ) -> None:
        if event_type not in PRODUCT_EVENT_TYPES:
            raise ValueError("invalid Hub product event")
        now = time.time()
        with self._lock, self._connect() as db:
            db.execute(
                "DELETE FROM hub_product_events WHERE created_at<?",
                (now - EVENT_RETENTION_SECONDS,),
            )
            count = int(
                db.execute(
                    "SELECT COUNT(*) FROM hub_product_events WHERE tenant_id=? AND owner_id=?",
                    (tenant_id, owner_id),
                ).fetchone()[0]
            )
            if count >= MAX_OWNER_EVENTS:
                db.execute(
                    "DELETE FROM hub_product_events WHERE event_id IN (SELECT event_id FROM hub_product_events "
                    "WHERE tenant_id=? AND owner_id=? ORDER BY created_at,event_id LIMIT ?)",
                    (tenant_id, owner_id, count - MAX_OWNER_EVENTS + 1),
                )
            db.execute(
                "INSERT INTO hub_product_events VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    "hubevent_" + uuid.uuid4().hex,
                    tenant_id,
                    owner_id,
                    contract_id[:80],
                    candidate_id[:80],
                    tool_digest[:64],
                    event_type,
                    outcome_code[:120],
                    now,
                ),
            )

    def metrics(self, tenant_id: str, owner_id: str, since: float) -> dict[str, Any]:
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT event_type,outcome_code,COUNT(*) AS count FROM hub_product_events "
                "WHERE tenant_id=? AND owner_id=? AND created_at>=? GROUP BY event_type,outcome_code",
                (tenant_id, owner_id, since),
            ).fetchall()
        counts: dict[str, int] = {}
        outcomes: dict[str, int] = {}
        for row in rows:
            event_type = str(row["event_type"])
            count = int(row["count"])
            counts[event_type] = counts.get(event_type, 0) + count
            outcome = str(row["outcome_code"] or "")
            if outcome:
                outcomes[outcome] = outcomes.get(outcome, 0) + count
        return {"events": counts, "outcomes": outcomes, "total": sum(counts.values())}

    def schedule_due(
        self,
        tenant_id: str,
        owner_id: str,
        identity_key: str,
        source_digest: str,
        now: float,
    ) -> bool:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM hub_auto_review_schedule WHERE tenant_id=? AND owner_id=? AND identity_key=?",
                (tenant_id, owner_id, identity_key),
            ).fetchone()
        if row is None:
            return True
        item = dict(row)
        if item["source_digest"] != source_digest or item["sop_version"] != SOP_VERSION:
            return True
        return float(item["next_attempt_at"] or 0) <= now

    def begin_schedule(
        self,
        tenant_id: str,
        owner_id: str,
        identity_key: str,
        source_digest: str,
        run_id: str,
    ) -> None:
        now = time.time()
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO hub_auto_review_schedule(tenant_id,owner_id,identity_key,source_digest,sop_version,last_run_id,last_result,last_attempt_at,next_attempt_at) "
                "VALUES(?,?,?,?,?,?,'queued',?,?) ON CONFLICT(tenant_id,owner_id,identity_key) DO UPDATE SET "
                "source_digest=excluded.source_digest,sop_version=excluded.sop_version,last_run_id=excluded.last_run_id,"
                "last_result='queued',error_code='',last_attempt_at=excluded.last_attempt_at,next_attempt_at=excluded.next_attempt_at",
                (
                    tenant_id,
                    owner_id,
                    identity_key,
                    source_digest,
                    SOP_VERSION,
                    run_id,
                    now,
                    now + AUTO_REVIEW_INTERVAL_SECONDS,
                ),
            )

    def finish_schedule(
        self,
        tenant_id: str,
        owner_id: str,
        identity_key: str,
        run_id: str,
        *,
        result: str,
        error_code: str = "",
    ) -> None:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM hub_auto_review_schedule WHERE tenant_id=? AND owner_id=? AND identity_key=? AND last_run_id=?",
                (tenant_id, owner_id, identity_key, run_id),
            ).fetchone()
            if row is None or str(row["last_result"]) != "queued":
                return
            failures = 0 if result in {"awaiting_call_approval", "published"} else int(row["failure_count"] or 0) + 1
            if error_code in STRUCTURAL_REVIEW_ERRORS:
                delay = 10 * 365 * 24 * 60 * 60
            elif failures <= 1:
                delay = 24 * 60 * 60
            elif failures == 2:
                delay = 72 * 60 * 60
            else:
                delay = 7 * 24 * 60 * 60
            db.execute(
                "UPDATE hub_auto_review_schedule SET last_result=?,error_code=?,failure_count=?,next_attempt_at=? "
                "WHERE tenant_id=? AND owner_id=? AND identity_key=?",
                (
                    result[:80],
                    error_code[:120],
                    failures,
                    time.time() + delay,
                    tenant_id,
                    owner_id,
                    identity_key,
                ),
            )


class MCPHubTrustedChannelService:
    def __init__(
        self,
        hub: MCPHubService,
        review: MCPHubReviewService,
        store: MCPHubTrustedStore,
    ) -> None:
        self.hub = hub
        self.review = review
        self.store = store
        self.tenant_id = hub.tenant_id
        self.owner_id = hub.owner_id
        self._maintenance_task: asyncio.Task[None] | None = None
        self._kick_task: asyncio.Task[None] | None = None
        self._contract_locks: dict[str, asyncio.Lock] = {}
        self._activation_locks: dict[str, asyncio.Lock] = {}
        self._maintenance_lock = asyncio.Lock()

    def _require_enabled(self) -> None:
        if not trusted_channel_enabled():
            raise HubError(
                "MCP Hub 可信频道当前未启用。",
                code="hub_trusted_channel_disabled",
                status_code=503,
            )

    async def start(self) -> None:
        if not trusted_channel_enabled():
            return
        self._maintenance_task = asyncio.create_task(self._maintenance_loop())
        if float(self.hub.store.meta("snapshot_at", "0") or 0) > 0:
            self.on_registry_sync()

    async def close(self) -> None:
        tasks = [task for task in (self._maintenance_task, self._kick_task) if task]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def on_registry_sync(self) -> None:
        if not trusted_channel_enabled():
            return
        if self._kick_task is None or self._kick_task.done():
            self._kick_task = asyncio.create_task(self.run_maintenance())

    async def _maintenance_loop(self) -> None:
        while True:
            await asyncio.sleep(MAINTENANCE_INTERVAL_SECONDS)
            try:
                await self.run_maintenance()
            except Exception:
                pass

    def status(self) -> dict[str, Any]:
        entries = self._entries()
        counts: dict[str, int] = {}
        for item in entries:
            counts[item["availability_state"]] = counts.get(item["availability_state"], 0) + 1
        return {
            "enabled": trusted_channel_enabled(),
            "auto_review_enabled": auto_review_enabled(),
            "owner_scope": "local-owner",
            "health_ttl_seconds": HEALTH_TTL_SECONDS,
            "counts": counts,
            "total": len(entries),
        }

    def activation_guard(
        self, contract_id: str, contract_fingerprint: str
    ) -> tuple[bool, str]:
        if not trusted_channel_enabled():
            return True, ""
        health = self.store.health(self.tenant_id, self.owner_id, contract_id)
        if health is None or health.get("contract_fingerprint") != contract_fingerprint:
            return False, "hub_trusted_revalidation_required"
        if (
            health.get("state") == "ready"
            and time.time() - float(health.get("checked_at") or 0) <= HEALTH_TTL_SECONDS
        ):
            return True, ""
        state = str(health.get("state") or "stale")
        return False, {
            "environment_blocked": "hub_trusted_environment_blocked",
            "degraded": "hub_trusted_degraded",
            "drifted": "hub_reviewed_contract_drift",
            "revoked": "hub_contract_revoked",
            "collision": "hub_contract_collision",
        }.get(state, "hub_trusted_revalidation_required")

    def _entries(self) -> list[dict[str, Any]]:
        candidates = self.hub.store.list_candidates(self.tenant_id, self.owner_id)
        result: list[dict[str, Any]] = []
        now = time.time()
        has_registry_snapshot = float(self.hub.store.meta("snapshot_at", "0") or 0) > 0
        for raw in self.review.contracts.describe():
            contract_id = str(raw["contract_id"])
            health = self.store.health(self.tenant_id, self.owner_id, contract_id)
            server = self.hub.store.get_server(str(raw["server_name"]), str(raw["version"]))
            remote = next(
                (
                    item
                    for item in (server or {}).get("remotes", [])
                    if item.get("url") == raw.get("remote_url")
                ),
                None,
            )
            source_matches = bool(
                server
                and server.get("status") in {"active", "published"}
                and remote
                and remote.get("eligibility") == "eligible"
                and (
                    not raw.get("source_digest")
                    or server.get("source_digest") == raw.get("source_digest")
                )
            )
            if raw.get("collision"):
                state = "collision"
            elif raw.get("revoked"):
                state = "revoked"
            elif not has_registry_snapshot:
                state = "stale"
            elif not source_matches:
                state = "drifted"
            elif health is None or health.get("contract_fingerprint") != raw.get("contract_fingerprint"):
                state = "stale"
            elif health.get("state") == "ready" and now - float(health.get("checked_at") or 0) > HEALTH_TTL_SECONDS:
                state = "stale"
            else:
                state = str(health.get("state") or "stale")
            candidate = next(
                (
                    item
                    for item in candidates
                    if (
                        item["server_name"],
                        item["version"],
                        item["remote_url"],
                    )
                    == (
                        raw["server_name"],
                        raw["version"],
                        raw["remote_url"],
                    )
                ),
                None,
            )
            result.append(
                {
                    "contract_id": contract_id,
                    "contract_fingerprint": raw["contract_fingerprint"],
                    "contract_source": raw.get("contract_source", "repository"),
                    "server_name": raw["server_name"],
                    "version": raw["version"],
                    "title": str((server or {}).get("title") or raw["server_name"]),
                    "description": str((server or {}).get("description") or "")[:2000],
                    "publisher": str((server or {}).get("publisher") or ""),
                    "categories": list((server or {}).get("categories") or [])[:20],
                    "origin": raw["origin"],
                    "allowed_tools": list(raw.get("allowed_tools") or []),
                    "tool_effects": dict(raw.get("tool_effects") or {}),
                    "evidence_digest": raw["evidence_digest"],
                    "published_at": float(raw.get("published_at") or 0),
                    "availability_state": state,
                    "health_checked_at": float((health or {}).get("checked_at") or 0),
                    "health_error_code": str((health or {}).get("error_code") or ""),
                    "candidate_id": str((candidate or {}).get("candidate_id") or ""),
                    "candidate_state": str((candidate or {}).get("state") or ""),
                    "connected": bool(
                        candidate
                        and self.hub._live.get(str(candidate["candidate_id"])) is not None
                    ),
                }
            )
        return sorted(result, key=lambda item: (item["title"].lower(), item["contract_id"]))

    def list_servers(
        self,
        *,
        query: str = "",
        state: str = "",
        source: str = "",
        limit: int = 50,
        cursor: int = 0,
    ) -> dict[str, Any]:
        self._require_enabled()
        clean_query = query.strip().lower()
        items = [
            item
            for item in self._entries()
            if (not state or item["availability_state"] == state)
            and (not source or item["contract_source"] == source)
            and (
                not clean_query
                or clean_query
                in " ".join(
                    [item["title"], item["server_name"], item["publisher"], item["description"]]
                ).lower()
            )
        ]
        self.store.record_event(self.tenant_id, self.owner_id, "trusted_list_view")
        page = items[cursor : cursor + limit]
        return {
            "items": page,
            "total": len(items),
            "next_cursor": cursor + len(page) if cursor + len(page) < len(items) else None,
        }

    def get_server(self, contract_id: str) -> dict[str, Any]:
        self._require_enabled()
        item = next((entry for entry in self._entries() if entry["contract_id"] == contract_id), None)
        if item is None:
            raise HubError("可信 Hub 契约不存在。", code="hub_contract_not_found", status_code=404)
        self.store.record_event(
            self.tenant_id,
            self.owner_id,
            "trusted_detail_view",
            contract_id=contract_id,
        )
        return item

    def _require_contract(
        self, contract_id: str, expected_fingerprint: str
    ) -> HubReviewedContractV1:
        contract, reason = self.review.contracts.get_contract(contract_id)
        if contract is None or reason:
            raise HubError(
                "可信 Hub 契约当前不可用。",
                code=reason or "hub_contract_not_found",
                status_code=409 if reason else 404,
            )
        if contract.contract_fingerprint != expected_fingerprint:
            raise HubError(
                "可信 Hub 契约指纹已变化。",
                code="hub_contract_fingerprint_mismatch",
                status_code=409,
            )
        return contract

    @staticmethod
    def _health_state_for_error(code: str) -> str:
        if code == "hub_registry_snapshot_missing":
            return "stale"
        if code in DRIFT_ERROR_CODES:
            return "drifted"
        if code in ENVIRONMENT_ERROR_CODES:
            return "environment_blocked"
        return "degraded"

    async def _disconnect_contract_candidates(
        self, contract: HubReviewedContractV1, *, drifted: bool
    ) -> None:
        for candidate in self.hub.store.list_candidates(self.tenant_id, self.owner_id):
            if (
                candidate["server_name"],
                candidate["version"],
                candidate["remote_url"],
            ) != contract.identity:
                continue
            await self.hub._disconnect_live(candidate["candidate_id"])
            if drifted:
                self.hub.store.update_candidate(
                    candidate["candidate_id"],
                    self.tenant_id,
                    self.owner_id,
                    state="drifted",
                    taint_reason="hub_reviewed_contract_drift",
                )

    async def revalidate(
        self,
        contract_id: str,
        expected_fingerprint: str,
        *,
        enforce_manual_rate_limit: bool = False,
    ) -> dict[str, Any]:
        self._require_enabled()
        contract = self._require_contract(contract_id, expected_fingerprint)
        lock = self._contract_locks.setdefault(contract_id, asyncio.Lock())
        async with lock:
            health = self.store.health(self.tenant_id, self.owner_id, contract_id)
            if (
                enforce_manual_rate_limit
                and health is not None
                and time.time() - float(health.get("checked_at") or 0)
                < MANUAL_REVALIDATE_INTERVAL_SECONDS
            ):
                raise HubError(
                    "该契约刚刚完成检查，请稍后再试。",
                    code="hub_trusted_recheck_rate_limited",
                    status_code=429,
                )
            self.store.record_event(
                self.tenant_id,
                self.owner_id,
                "revalidate_started",
                contract_id=contract_id,
            )
            try:
                if float(self.hub.store.meta("snapshot_at", "0") or 0) <= 0:
                    raise HubError(
                        "请先同步官方 Registry，再复核可信契约。",
                        code="hub_registry_snapshot_missing",
                        status_code=409,
                    )
                result = await self.hub.inspect_reviewed_contract(contract)
            except HubError as exc:
                state = self._health_state_for_error(exc.code)
                self.store.set_health(
                    self.tenant_id,
                    self.owner_id,
                    contract_id,
                    contract.contract_fingerprint,
                    state=state,
                    error_code=exc.code,
                )
                if state == "drifted":
                    await self._disconnect_contract_candidates(contract, drifted=True)
                    event_type = "contract_drifted"
                else:
                    event_type = "revalidate_failed"
                self.store.record_event(
                    self.tenant_id,
                    self.owner_id,
                    event_type,
                    contract_id=contract_id,
                    outcome_code=exc.code,
                )
                raise
            self.store.set_health(
                self.tenant_id,
                self.owner_id,
                contract_id,
                contract.contract_fingerprint,
                state="ready",
            )
            self.store.record_event(
                self.tenant_id,
                self.owner_id,
                "revalidate_succeeded",
                contract_id=contract_id,
            )
            return result

    async def activate(
        self, contract_id: str, expected_fingerprint: str
    ) -> dict[str, Any]:
        self._require_enabled()
        lock = self._activation_locks.setdefault(contract_id, asyncio.Lock())
        async with lock:
            contract = self._require_contract(contract_id, expected_fingerprint)
            self.store.record_event(
                self.tenant_id,
                self.owner_id,
                "activation_started",
                contract_id=contract_id,
            )
            try:
                inspected = await self.revalidate(contract_id, expected_fingerprint)
                candidate = self.hub.create_candidate(
                    contract.server_name,
                    contract.version,
                    str(inspected["remote_id"]),
                )
                candidate = self.hub.store.update_candidate(
                    candidate["candidate_id"],
                    self.tenant_id,
                    self.owner_id,
                    state="verified",
                    schema_digest=str(inspected["schema_digest"]),
                    tools=list(inspected["tools"]),
                )
                activated = await self.hub.activate(
                    candidate["candidate_id"], str(inspected["schema_digest"])
                )
            except HubError as exc:
                self.store.record_event(
                    self.tenant_id,
                    self.owner_id,
                    "activation_failed",
                    contract_id=contract_id,
                    outcome_code=exc.code,
                )
                raise
            self.store.record_event(
                self.tenant_id,
                self.owner_id,
                "activation_succeeded",
                contract_id=contract_id,
                candidate_id=str(activated["candidate_id"]),
            )
            return activated

    async def run_maintenance(self) -> None:
        if not trusted_channel_enabled():
            return
        if self._maintenance_lock.locked():
            return
        async with self._maintenance_lock:
            await self._run_maintenance()

    async def _run_maintenance(self) -> None:
        await self._reconcile_finished_auto_runs()
        if float(self.hub.store.meta("snapshot_at", "0") or 0) <= 0:
            return
        due_contracts: list[tuple[HubReviewedContractV1, str]] = []
        now = time.time()
        for raw in self.review.contracts.describe():
            if raw.get("collision") or raw.get("revoked"):
                continue
            health = self.store.health(self.tenant_id, self.owner_id, str(raw["contract_id"]))
            if (
                health is None
                or health.get("state") == "stale"
                or float(health.get("next_check_at") or 0) <= now
            ):
                contract, reason = self.review.contracts.get_contract(str(raw["contract_id"]))
                if contract is not None and not reason:
                    due_contracts.append((contract, contract.contract_fingerprint))
        semaphore = asyncio.Semaphore(2)

        async def check(contract: HubReviewedContractV1, fingerprint: str) -> None:
            async with semaphore:
                try:
                    await self.revalidate(contract.contract_id, fingerprint)
                except HubError:
                    pass

        await asyncio.gather(*(check(contract, fingerprint) for contract, fingerprint in due_contracts))
        await self._schedule_auto_review()

    async def _schedule_auto_review(self) -> None:
        if not auto_review_enabled() or not review_factory_enabled():
            return
        if any(
            run["status"] in {"queued", "running", "awaiting_operator"}
            for run in self.review.store.list_runs(self.tenant_id, self.owner_id)
        ):
            return
        reviewed_identities = {
            (str(item["server_name"]), str(item["version"]), str(item["remote_url"]))
            for item in self.review.contracts.describe()
            if not item.get("collision") and not item.get("revoked")
        }
        now = time.time()
        selected: list[dict[str, str]] = []
        schedule_rows: list[tuple[str, str]] = []
        for identity in self.review.reproducible_registry_selection(limit=200):
            server = self.hub.store.get_server(identity["server_name"], identity["version"])
            remote = next(
                (
                    item
                    for item in (server or {}).get("remotes", [])
                    if item.get("remote_id") == identity["remote_id"]
                ),
                None,
            )
            if server is None or remote is None:
                continue
            if (identity["server_name"], identity["version"], str(remote["url"])) in reviewed_identities:
                continue
            key = stable_digest(identity)
            source_digest = str(server.get("source_digest") or "")
            if not self.store.schedule_due(
                self.tenant_id, self.owner_id, key, source_digest, now
            ):
                continue
            if self.review.store.has_review_identity(
                self.tenant_id,
                self.owner_id,
                identity["server_name"],
                identity["version"],
            ):
                continue
            selected.append(identity)
            schedule_rows.append((key, source_digest))
            if len(selected) >= MAX_AUTO_REVIEW_ITEMS:
                break
        if not selected:
            return
        try:
            run = self.review.create_run(selected, trigger="automatic")
        except HubError as exc:
            if exc.code == "hub_review_owner_busy":
                return
            raise
        for key, source_digest in schedule_rows:
            self.store.begin_schedule(
                self.tenant_id,
                self.owner_id,
                key,
                source_digest,
                str(run["run_id"]),
            )

    async def _reconcile_finished_auto_runs(self) -> None:
        for run in self.review.store.list_runs(self.tenant_id, self.owner_id):
            if run.get("trigger") != "automatic" or run["status"] not in {"completed", "awaiting_operator"}:
                continue
            for item in run["items"]:
                identity = {
                    "server_name": str(item["server_name"]),
                    "version": str(item["version"]),
                    "remote_id": str(item["remote_id"]),
                }
                self.store.finish_schedule(
                    self.tenant_id,
                    self.owner_id,
                    stable_digest(identity),
                    str(run["run_id"]),
                    result=str(item["state"]),
                    error_code=str(item.get("error_code") or ""),
                )

    def record_runtime_event(
        self,
        event_type: str,
        metadata: dict[str, Any] | None = None,
        *,
        outcome_code: str = "",
    ) -> None:
        if not trusted_channel_enabled() or event_type not in PRODUCT_EVENT_TYPES:
            return
        safe = metadata or {}
        tool_name = str(safe.get("tool_name") or "")
        self.store.record_event(
            self.tenant_id,
            self.owner_id,
            event_type,
            contract_id=str(safe.get("contract_id") or ""),
            candidate_id=str(safe.get("candidate_id") or ""),
            tool_digest=stable_digest(tool_name) if tool_name else "",
            outcome_code=outcome_code,
        )

    def metrics(self, window: Literal["7d", "30d", "90d"]) -> dict[str, Any]:
        self._require_enabled()
        days = {"7d": 7, "30d": 30, "90d": 90}[window]
        return {
            "window": window,
            **self.store.metrics(
                self.tenant_id,
                self.owner_id,
                time.time() - days * 24 * 60 * 60,
            ),
        }


class TrustedContractRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_contract_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


router = APIRouter(tags=["mcp-hub-trusted-channel"])
_trusted_service: MCPHubTrustedChannelService | None = None


def configure_mcp_hub_trusted(service: MCPHubTrustedChannelService) -> None:
    global _trusted_service
    _trusted_service = service


def _service() -> MCPHubTrustedChannelService:
    if _trusted_service is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "hub_trusted_unconfigured", "error": "MCP Hub 可信频道尚未配置。"},
        )
    return _trusted_service


def _raise_http(exc: HubError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "error": str(exc)},
    ) from exc


@router.get("/api/mcp/hub/trusted/status")
async def trusted_status() -> dict[str, Any]:
    return _service().status()


@router.get("/api/mcp/hub/trusted/servers")
async def list_trusted_servers(
    q: str = Query(default="", max_length=200),
    state: str = Query(default="", max_length=40),
    source: str = Query(default="", max_length=20),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    try:
        if state and state not in TRUSTED_STATES:
            raise HubError("可信状态筛选无效。", code="hub_trusted_state_invalid", status_code=422)
        if source and source not in {"repository", "local"}:
            raise HubError("契约来源筛选无效。", code="hub_trusted_source_invalid", status_code=422)
        return _service().list_servers(
            query=q, state=state, source=source, limit=limit, cursor=cursor
        )
    except HubError as exc:
        _raise_http(exc)


@router.get("/api/mcp/hub/trusted/servers/{contract_id}")
async def get_trusted_server(contract_id: str) -> dict[str, Any]:
    try:
        return _service().get_server(contract_id)
    except HubError as exc:
        _raise_http(exc)


@router.post("/api/mcp/hub/trusted/servers/{contract_id}/revalidate")
async def revalidate_trusted_server(
    contract_id: str, payload: TrustedContractRequest
) -> dict[str, Any]:
    try:
        await _service().revalidate(
            contract_id,
            payload.expected_contract_fingerprint,
            enforce_manual_rate_limit=True,
        )
        return _service().get_server(contract_id)
    except HubError as exc:
        _raise_http(exc)


@router.post("/api/mcp/hub/trusted/servers/{contract_id}/activate")
async def activate_trusted_server(
    contract_id: str, payload: TrustedContractRequest
) -> dict[str, Any]:
    try:
        return await _service().activate(
            contract_id, payload.expected_contract_fingerprint
        )
    except HubError as exc:
        _raise_http(exc)


@router.get("/api/mcp/hub/trusted/metrics")
async def trusted_metrics(
    window: Literal["7d", "30d", "90d"] = Query(default="30d"),
) -> dict[str, Any]:
    try:
        return _service().metrics(window)
    except HubError as exc:
        _raise_http(exc)
