from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken

from .gate import REQUIRED_DRILLS, evaluate_native_gate, percentile
from .omniroute_parity import ALGORITHM_VERSION, CONFIG_HASH
from .schemas import (
    RouterConnection,
    RouterConnectionCreate,
    RouterConnectionUpdate,
    RouterPolicy,
    default_connection_scopes,
    normalize_connection_scopes,
)


SCHEMA_VERSION = 12
DEFAULT_TENANT_ID = "local"
CANONICAL_MASTER_KEY_ENV = "MODEL_MIRROR_CREDENTIAL_MASTER_KEY"
LEGACY_MASTER_KEY_ENV = "MODEL_ROUTER_CREDENTIAL_MASTER_KEY"
REQUIRE_EXTERNAL_MASTER_KEY_ENV = (
    "MODEL_MIRROR_REQUIRE_EXTERNAL_CREDENTIAL_MASTER_KEY"
)
MASTER_KEY_FINGERPRINT_METADATA_KEY = "credential_master_key_fingerprint"
MASTER_KEY_VERSION_METADATA_KEY = "credential_master_key_version"
logger = logging.getLogger("modelmirror.model_router")


class RouterRepositoryError(Exception):
    """Base error for the native model-router repository."""


class RouterConnectionNotFound(RouterRepositoryError):
    """Raised when a tenant cannot see the requested connection."""


class RouterCredentialUnavailable(RouterRepositoryError):
    """Raised when encrypted connection material cannot be recovered."""


class RouterRepository(Protocol):
    def list_connections(self, tenant_id: str) -> list[RouterConnection]: ...

    def create_connection(
        self, tenant_id: str, payload: RouterConnectionCreate
    ) -> RouterConnection: ...

    def update_connection(
        self, tenant_id: str, connection_id: str, payload: RouterConnectionUpdate
    ) -> RouterConnection: ...

    def get_connection(
        self, tenant_id: str, connection_id: str
    ) -> RouterConnection: ...

    def resolve_api_key(self, tenant_id: str, connection_id: str) -> str: ...

    def get_policy(self, tenant_id: str) -> RouterPolicy: ...

    def save_policy(self, tenant_id: str, policy: RouterPolicy) -> RouterPolicy: ...


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class SQLiteRouterRepository:
    """Tenant-scoped SQLite persistence with encrypted provider credentials."""

    def __init__(
        self,
        storage_dir: str | Path | None = None,
        *,
        master_key: str | bytes | None = None,
    ) -> None:
        package_dir = Path(__file__).resolve().parent
        self.storage_dir = Path(
            storage_dir
            or os.getenv("MODEL_ROUTER_STORAGE_DIR", "").strip()
            or package_dir / "storage"
        )
        self.database_path = self.storage_dir / "router.sqlite3"
        self.master_key_path = self.storage_dir / "credential-master.key"
        self._lock = threading.RLock()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._master_key = self._resolve_master_key(master_key)
        self._fernet = Fernet(self._master_key)
        self._initialize()
        self._verify_or_record_master_key()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS router_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS router_connections (
            id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            base_url TEXT NOT NULL,
            masked_key TEXT NOT NULL,
            api_key_ciphertext TEXT NOT NULL,
            scopes_json TEXT NOT NULL DEFAULT '["chat"]',
            enabled INTEGER NOT NULL DEFAULT 1,
            health TEXT NOT NULL DEFAULT 'untested',
            model_count INTEGER NOT NULL DEFAULT 0,
            last_checked_at TEXT,
            last_error_code TEXT,
            last_error_hint TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, id)
        );
        CREATE TABLE IF NOT EXISTS router_policies (
            tenant_id TEXT PRIMARY KEY,
            engine TEXT NOT NULL DEFAULT 'sidecar',
            default_mode TEXT NOT NULL DEFAULT 'auto',
            canary_percent INTEGER NOT NULL DEFAULT 0,
            compression_mode TEXT NOT NULL DEFAULT 'auto',
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS router_candidate_stats (
            tenant_id TEXT NOT NULL,
            connection_id TEXT NOT NULL,
            model_id TEXT NOT NULL,
            success_count INTEGER NOT NULL DEFAULT 0,
            failure_count INTEGER NOT NULL DEFAULT 0,
            latency_ema_ms REAL,
            latency_stddev_ms REAL NOT NULL DEFAULT 0,
            ttft_ema_ms REAL,
            e2e_ema_ms REAL,
            tokens_per_second_ema REAL,
            sample_count INTEGER NOT NULL DEFAULT 0,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            breaker_state TEXT NOT NULL DEFAULT 'closed',
            breaker_open_until REAL,
            last_success_at TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, connection_id, model_id)
        );
        CREATE TABLE IF NOT EXISTS router_decisions (
            id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            session_id_hash TEXT,
            engine TEXT NOT NULL,
            strategy TEXT NOT NULL,
            operation TEXT NOT NULL DEFAULT 'chat',
            selected_connection_id TEXT,
            selected_model_id TEXT,
            reason_codes_json TEXT NOT NULL DEFAULT '[]',
            outcome TEXT,
            input_bytes INTEGER,
            output_bytes INTEGER,
            media_seconds REAL,
            budget_limit_usd REAL,
            reserved_cost_usd REAL,
            settled_cost_usd REAL,
            budget_status TEXT,
            algorithm_version TEXT,
            config_hash TEXT,
            task_type TEXT,
            task_level TEXT,
            selection_kind TEXT,
            score_tier TEXT,
            planning_latency_ms REAL,
            eligible_count INTEGER,
            finalist_count INTEGER,
            ttft_ms REAL,
            e2e_ms REAL,
            output_tokens INTEGER,
            tokens_per_second REAL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, id)
        );
        CREATE TABLE IF NOT EXISTS compression_runs (
            id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            request_id TEXT,
            profile TEXT NOT NULL,
            original_tokens INTEGER NOT NULL,
            final_tokens INTEGER NOT NULL,
            fidelity_status TEXT NOT NULL,
            fallback_reason TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, id)
        );
        CREATE TABLE IF NOT EXISTS video_jobs (
            id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            idempotency_key_hash TEXT NOT NULL,
            decision_id TEXT,
            connection_id TEXT,
            requested_model TEXT NOT NULL,
            actual_model TEXT,
            provider TEXT NOT NULL DEFAULT 'openrouter',
            upstream_job_id TEXT,
            generation_id TEXT,
            status TEXT NOT NULL,
            duration INTEGER,
            resolution TEXT,
            aspect_ratio TEXT,
            generate_audio INTEGER NOT NULL DEFAULT 0,
            seed INTEGER,
            has_first_frame INTEGER NOT NULL DEFAULT 0,
            has_last_frame INTEGER NOT NULL DEFAULT 0,
            reference_image_count INTEGER NOT NULL DEFAULT 0,
            provider_option_keys TEXT NOT NULL DEFAULT '[]',
            cost_usd REAL,
            cost_kind TEXT NOT NULL DEFAULT 'unavailable',
            error_code TEXT,
            output_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, id),
            UNIQUE (tenant_id, idempotency_key_hash)
        );
        CREATE TABLE IF NOT EXISTS audio_jobs (
            id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            idempotency_key_hash TEXT NOT NULL,
            decision_id TEXT,
            connection_id TEXT,
            requested_model TEXT NOT NULL,
            actual_model TEXT,
            provider TEXT NOT NULL DEFAULT 'openrouter',
            generation_id TEXT,
            status TEXT NOT NULL,
            has_image INTEGER NOT NULL DEFAULT 0,
            output_bytes INTEGER NOT NULL DEFAULT 0,
            cost_usd REAL,
            cost_kind TEXT NOT NULL DEFAULT 'unavailable',
            error_code TEXT,
            expires_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, id),
            UNIQUE (tenant_id, idempotency_key_hash)
        );
        CREATE TABLE IF NOT EXISTS realtime_calls (
            id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            decision_id TEXT,
            connection_id TEXT NOT NULL,
            model_id TEXT NOT NULL,
            provider TEXT NOT NULL DEFAULT 'openai',
            upstream_call_id TEXT,
            voice TEXT NOT NULL,
            vad_mode TEXT NOT NULL,
            language TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT,
            expires_at TEXT NOT NULL,
            ended_at TEXT,
            duration_seconds REAL,
            input_tokens INTEGER,
            output_tokens INTEGER,
            cost_usd REAL,
            cost_kind TEXT NOT NULL DEFAULT 'unavailable',
            error_code TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, id)
        );
        CREATE TABLE IF NOT EXISTS router_candidate_samples (
            id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            connection_id TEXT,
            model_id TEXT,
            engine TEXT NOT NULL,
            algorithm_version TEXT NOT NULL,
            config_hash TEXT NOT NULL,
            task_type TEXT,
            success INTEGER NOT NULL,
            outcome TEXT NOT NULL,
            ttft_ms REAL,
            e2e_ms REAL,
            output_tokens INTEGER,
            tokens_per_second REAL,
            planning_latency_ms REAL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, id)
        );
        CREATE TABLE IF NOT EXISTS router_gate_approvals (
            tenant_id TEXT PRIMARY KEY,
            algorithm_version TEXT NOT NULL,
            config_hash TEXT NOT NULL,
            no_open_p0_p1 INTEGER NOT NULL,
            drills_json TEXT NOT NULL,
            approved_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS provider_chat_certifications (
            id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            connection_id TEXT NOT NULL,
            connection_fingerprint TEXT NOT NULL,
            contract_version TEXT NOT NULL,
            requested_model TEXT NOT NULL,
            actual_model TEXT,
            idempotency_key_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            checks_json TEXT NOT NULL DEFAULT '{}',
            warnings_json TEXT NOT NULL DEFAULT '[]',
            error_code TEXT,
            ttft_ms REAL,
            e2e_ms REAL,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            total_tokens INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            PRIMARY KEY (tenant_id, id),
            UNIQUE (tenant_id, connection_id, idempotency_key_hash)
        );
        CREATE INDEX IF NOT EXISTS idx_router_connections_tenant_enabled
            ON router_connections (tenant_id, enabled);
        CREATE INDEX IF NOT EXISTS idx_router_decisions_tenant_created
            ON router_decisions (tenant_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_compression_runs_tenant_created
            ON compression_runs (tenant_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_video_jobs_tenant_created
            ON video_jobs (tenant_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_video_jobs_tenant_status
            ON video_jobs (tenant_id, status);
        CREATE INDEX IF NOT EXISTS idx_audio_jobs_tenant_created
            ON audio_jobs (tenant_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_audio_jobs_tenant_status
            ON audio_jobs (tenant_id, status);
        CREATE INDEX IF NOT EXISTS idx_realtime_calls_tenant_created
            ON realtime_calls (tenant_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_realtime_calls_tenant_status
            ON realtime_calls (tenant_id, status);
        CREATE INDEX IF NOT EXISTS idx_router_samples_gate
            ON router_candidate_samples (
                tenant_id, engine, algorithm_version, config_hash, created_at
            );
        CREATE INDEX IF NOT EXISTS idx_router_samples_candidate
            ON router_candidate_samples (
                tenant_id, connection_id, model_id, algorithm_version, created_at
            );
        CREATE INDEX IF NOT EXISTS idx_provider_chat_certifications_recent
            ON provider_chat_certifications (
                tenant_id, connection_id, created_at DESC
            );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_provider_chat_certifications_running
            ON provider_chat_certifications (tenant_id, connection_id)
            WHERE status = 'running';
        """
        with self._lock, self._connect() as connection:
            previous_schema_version = int(
                connection.execute("PRAGMA user_version").fetchone()[0]
            )
            connection.executescript(schema)
            connection_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(router_connections)"
                ).fetchall()
            }
            if "scopes_json" not in connection_columns:
                connection.execute(
                    "ALTER TABLE router_connections "
                    "ADD COLUMN scopes_json "
                    """TEXT NOT NULL DEFAULT '["chat"]'"""
                )
            if previous_schema_version < 8:
                connection.execute(
                    "UPDATE router_connections "
                    """SET scopes_json = '["chat","audio"]' """
                    "WHERE kind = 'openrouter'"
                )
                connection.execute(
                    "UPDATE router_connections "
                    """SET scopes_json = '["chat"]' """
                    "WHERE kind IN ('newapi', 'openai_compatible')"
                )
            existing = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(router_candidate_stats)"
                ).fetchall()
            }
            migrations = {
                "latency_stddev_ms": (
                    "ALTER TABLE router_candidate_stats "
                    "ADD COLUMN latency_stddev_ms REAL NOT NULL DEFAULT 0"
                ),
                "consecutive_failures": (
                    "ALTER TABLE router_candidate_stats "
                    "ADD COLUMN consecutive_failures INTEGER NOT NULL DEFAULT 0"
                ),
                "breaker_open_until": (
                    "ALTER TABLE router_candidate_stats "
                    "ADD COLUMN breaker_open_until REAL"
                ),
                "last_success_at": (
                    "ALTER TABLE router_candidate_stats "
                    "ADD COLUMN last_success_at TEXT"
                ),
                "ttft_ema_ms": (
                    "ALTER TABLE router_candidate_stats "
                    "ADD COLUMN ttft_ema_ms REAL"
                ),
                "e2e_ema_ms": (
                    "ALTER TABLE router_candidate_stats "
                    "ADD COLUMN e2e_ema_ms REAL"
                ),
                "tokens_per_second_ema": (
                    "ALTER TABLE router_candidate_stats "
                    "ADD COLUMN tokens_per_second_ema REAL"
                ),
                "sample_count": (
                    "ALTER TABLE router_candidate_stats "
                    "ADD COLUMN sample_count INTEGER NOT NULL DEFAULT 0"
                ),
            }
            for column, statement in migrations.items():
                if column not in existing:
                    connection.execute(statement)
            decision_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(router_decisions)"
                ).fetchall()
            }
            decision_migrations = {
                "budget_limit_usd": (
                    "ALTER TABLE router_decisions "
                    "ADD COLUMN budget_limit_usd REAL"
                ),
                "reserved_cost_usd": (
                    "ALTER TABLE router_decisions "
                    "ADD COLUMN reserved_cost_usd REAL"
                ),
                "settled_cost_usd": (
                    "ALTER TABLE router_decisions "
                    "ADD COLUMN settled_cost_usd REAL"
                ),
                "budget_status": (
                    "ALTER TABLE router_decisions "
                    "ADD COLUMN budget_status TEXT"
                ),
                "operation": (
                    "ALTER TABLE router_decisions "
                    "ADD COLUMN operation TEXT NOT NULL DEFAULT 'chat'"
                ),
                "input_bytes": (
                    "ALTER TABLE router_decisions "
                    "ADD COLUMN input_bytes INTEGER"
                ),
                "output_bytes": (
                    "ALTER TABLE router_decisions "
                    "ADD COLUMN output_bytes INTEGER"
                ),
                "media_seconds": (
                    "ALTER TABLE router_decisions "
                    "ADD COLUMN media_seconds REAL"
                ),
                "algorithm_version": (
                    "ALTER TABLE router_decisions "
                    "ADD COLUMN algorithm_version TEXT"
                ),
                "config_hash": (
                    "ALTER TABLE router_decisions ADD COLUMN config_hash TEXT"
                ),
                "task_type": (
                    "ALTER TABLE router_decisions ADD COLUMN task_type TEXT"
                ),
                "task_level": (
                    "ALTER TABLE router_decisions ADD COLUMN task_level TEXT"
                ),
                "selection_kind": (
                    "ALTER TABLE router_decisions ADD COLUMN selection_kind TEXT"
                ),
                "score_tier": (
                    "ALTER TABLE router_decisions ADD COLUMN score_tier TEXT"
                ),
                "planning_latency_ms": (
                    "ALTER TABLE router_decisions "
                    "ADD COLUMN planning_latency_ms REAL"
                ),
                "eligible_count": (
                    "ALTER TABLE router_decisions ADD COLUMN eligible_count INTEGER"
                ),
                "finalist_count": (
                    "ALTER TABLE router_decisions ADD COLUMN finalist_count INTEGER"
                ),
                "ttft_ms": (
                    "ALTER TABLE router_decisions ADD COLUMN ttft_ms REAL"
                ),
                "e2e_ms": (
                    "ALTER TABLE router_decisions ADD COLUMN e2e_ms REAL"
                ),
                "output_tokens": (
                    "ALTER TABLE router_decisions ADD COLUMN output_tokens INTEGER"
                ),
                "tokens_per_second": (
                    "ALTER TABLE router_decisions "
                    "ADD COLUMN tokens_per_second REAL"
                ),
            }
            for column, statement in decision_migrations.items():
                if column not in decision_columns:
                    connection.execute(statement)
            video_job_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(video_jobs)"
                ).fetchall()
            }
            video_job_migrations = {
                "has_last_frame": (
                    "ALTER TABLE video_jobs "
                    "ADD COLUMN has_last_frame INTEGER NOT NULL DEFAULT 0"
                ),
                "reference_image_count": (
                    "ALTER TABLE video_jobs "
                    "ADD COLUMN reference_image_count "
                    "INTEGER NOT NULL DEFAULT 0"
                ),
                "provider_option_keys": (
                    "ALTER TABLE video_jobs "
                    "ADD COLUMN provider_option_keys "
                    "TEXT NOT NULL DEFAULT '[]'"
                ),
            }
            for column, statement in video_job_migrations.items():
                if column not in video_job_columns:
                    connection.execute(statement)
            now = utc_now()
            connection.execute(
                """
                UPDATE provider_chat_certifications
                SET status = 'uncertain', error_code = 'server_restarted',
                    updated_at = ?, completed_at = ?
                WHERE status = 'running'
                """,
                (now, now),
            )
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def create_connection(
        self, tenant_id: str, payload: RouterConnectionCreate
    ) -> RouterConnection:
        clean_tenant = self._tenant_id(tenant_id)
        api_key = payload.api_key.get_secret_value().strip()
        now = utc_now()
        connection_id = f"conn_{uuid.uuid4().hex}"
        encrypted = self._fernet.encrypt(api_key.encode("utf-8")).decode("ascii")
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO router_connections (
                    id, tenant_id, name, kind, base_url, masked_key,
                    api_key_ciphertext, scopes_json, enabled, health,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    connection_id,
                    clean_tenant,
                    payload.name,
                    payload.kind,
                    payload.base_url,
                    self._mask(api_key),
                    encrypted,
                    json.dumps(payload.scopes, separators=(",", ":")),
                    int(payload.enabled),
                    "untested" if payload.enabled else "disabled",
                    now,
                    now,
                ),
            )
        return self.get_connection(clean_tenant, connection_id)

    def list_connections(self, tenant_id: str) -> list[RouterConnection]:
        clean_tenant = self._tenant_id(tenant_id)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM router_connections
                WHERE tenant_id = ?
                ORDER BY updated_at DESC, id ASC
                """,
                (clean_tenant,),
            ).fetchall()
        return [self._public_connection(row) for row in rows]

    def get_connection(
        self, tenant_id: str, connection_id: str
    ) -> RouterConnection:
        clean_tenant = self._tenant_id(tenant_id)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM router_connections
                WHERE tenant_id = ? AND id = ?
                """,
                (clean_tenant, connection_id),
            ).fetchone()
        if row is None:
            raise RouterConnectionNotFound("Model service connection was not found.")
        return self._public_connection(row)

    def update_connection(
        self, tenant_id: str, connection_id: str, payload: RouterConnectionUpdate
    ) -> RouterConnection:
        current = self.get_connection(tenant_id, connection_id)
        updates: list[str] = []
        values: list[object] = []
        for field_name in ("name", "base_url"):
            value = getattr(payload, field_name)
            if value is not None:
                updates.append(f"{field_name} = ?")
                values.append(value)
        if payload.scopes is not None:
            updates.append("scopes_json = ?")
            values.append(
                json.dumps(payload.scopes, separators=(",", ":"))
            )
        if payload.api_key is not None:
            api_key = payload.api_key.get_secret_value().strip()
            if not api_key:
                raise RouterRepositoryError("api_key cannot be empty")
            updates.extend(["api_key_ciphertext = ?", "masked_key = ?"])
            values.extend(
                [
                    self._fernet.encrypt(api_key.encode("utf-8")).decode("ascii"),
                    self._mask(api_key),
                ]
            )
        if payload.enabled is not None:
            updates.extend(["enabled = ?", "health = ?"])
            values.extend(
                [
                    int(payload.enabled),
                    "untested" if payload.enabled else "disabled",
                ]
            )
        if not updates:
            return current
        updates.append("updated_at = ?")
        values.append(utc_now())
        values.extend([self._tenant_id(tenant_id), connection_id])
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE router_connections SET {", ".join(updates)}
                WHERE tenant_id = ? AND id = ?
                """,
                tuple(values),
            )
            if cursor.rowcount != 1:
                raise RouterConnectionNotFound(
                    "Model service connection was not found."
                )
        return self.get_connection(tenant_id, connection_id)

    def resolve_api_key(self, tenant_id: str, connection_id: str) -> str:
        clean_tenant = self._tenant_id(tenant_id)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT api_key_ciphertext FROM router_connections
                WHERE tenant_id = ? AND id = ?
                """,
                (clean_tenant, connection_id),
            ).fetchone()
        if row is None:
            raise RouterConnectionNotFound("Model service connection was not found.")
        try:
            return self._fernet.decrypt(
                str(row["api_key_ciphertext"]).encode("ascii")
            ).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeDecodeError) as exc:
            raise RouterCredentialUnavailable(
                "The saved credential is unavailable. Please enter the key again."
            ) from exc

    def connection_config_fingerprint(
        self, tenant_id: str, connection_id: str
    ) -> str:
        clean_tenant = self._tenant_id(tenant_id)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT kind, base_url, scopes_json, api_key_ciphertext
                FROM router_connections
                WHERE tenant_id = ? AND id = ?
                """,
                (clean_tenant, connection_id),
            ).fetchone()
        if row is None:
            raise RouterConnectionNotFound("Model service connection was not found.")
        material = json.dumps(
            {
                "kind": row["kind"],
                "base_url": row["base_url"],
                "scopes": json.loads(row["scopes_json"]),
                "credential": row["api_key_ciphertext"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def claim_chat_certification(
        self,
        tenant_id: str,
        *,
        certification_id: str,
        connection_id: str,
        connection_fingerprint: str,
        contract_version: str,
        requested_model: str,
        idempotency_key_hash: str,
    ) -> tuple[dict[str, object], bool]:
        clean_tenant = self._tenant_id(tenant_id)
        now = utc_now()
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                """
                SELECT * FROM provider_chat_certifications
                WHERE tenant_id = ? AND connection_id = ?
                    AND idempotency_key_hash = ?
                """,
                (clean_tenant, connection_id, idempotency_key_hash),
            ).fetchone()
            if existing is not None:
                return dict(existing), False
            try:
                connection.execute(
                    """
                    INSERT INTO provider_chat_certifications (
                        id, tenant_id, connection_id, connection_fingerprint,
                        contract_version, requested_model,
                        idempotency_key_hash, status, checks_json,
                        warnings_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'running', '{}', '[]', ?, ?)
                    """,
                    (
                        certification_id,
                        clean_tenant,
                        connection_id,
                        connection_fingerprint,
                        contract_version,
                        requested_model,
                        idempotency_key_hash,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise RouterRepositoryError(
                    "provider_chat_certification_already_running"
                ) from exc
            row = connection.execute(
                """
                SELECT * FROM provider_chat_certifications
                WHERE tenant_id = ? AND id = ?
                """,
                (clean_tenant, certification_id),
            ).fetchone()
        return dict(row), True

    def complete_chat_certification(
        self,
        tenant_id: str,
        certification_id: str,
        *,
        status: str,
        checks: dict[str, bool],
        warning_codes: list[str],
        error_code: str | None = None,
        actual_model: str | None = None,
        ttft_ms: float | None = None,
        e2e_ms: float | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
    ) -> dict[str, object]:
        if status not in {"passed", "failed", "uncertain"}:
            raise RouterRepositoryError("invalid_provider_chat_certification_status")
        clean_tenant = self._tenant_id(tenant_id)
        now = utc_now()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE provider_chat_certifications
                SET status = ?, checks_json = ?, warnings_json = ?,
                    error_code = ?, actual_model = ?, ttft_ms = ?, e2e_ms = ?,
                    prompt_tokens = ?, completion_tokens = ?, total_tokens = ?,
                    updated_at = ?, completed_at = ?
                WHERE tenant_id = ? AND id = ? AND status = 'running'
                """,
                (
                    status,
                    json.dumps(checks, sort_keys=True, separators=(",", ":")),
                    json.dumps(warning_codes, separators=(",", ":")),
                    error_code,
                    actual_model,
                    ttft_ms,
                    e2e_ms,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    now,
                    now,
                    clean_tenant,
                    certification_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RouterRepositoryError("provider_chat_certification_not_running")
            row = connection.execute(
                """
                SELECT * FROM provider_chat_certifications
                WHERE tenant_id = ? AND id = ?
                """,
                (clean_tenant, certification_id),
            ).fetchone()
        return dict(row)

    def list_chat_certifications(self, tenant_id: str) -> list[dict[str, object]]:
        clean_tenant = self._tenant_id(tenant_id)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT certification.*
                FROM provider_chat_certifications AS certification
                JOIN (
                    SELECT connection_id, MAX(created_at) AS latest_created_at
                    FROM provider_chat_certifications
                    WHERE tenant_id = ?
                    GROUP BY connection_id
                ) AS latest
                ON latest.connection_id = certification.connection_id
                    AND latest.latest_created_at = certification.created_at
                WHERE certification.tenant_id = ?
                ORDER BY certification.created_at DESC, certification.id DESC
                """,
                (clean_tenant, clean_tenant),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_test_result(
        self,
        tenant_id: str,
        connection_id: str,
        *,
        health: str,
        model_count: int,
        checked_at: str,
        error_code: str | None = None,
        error_hint: str | None = None,
    ) -> RouterConnection:
        self.get_connection(tenant_id, connection_id)
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE router_connections
                SET health = ?, model_count = ?, last_checked_at = ?,
                    last_error_code = ?, last_error_hint = ?, updated_at = ?
                WHERE tenant_id = ? AND id = ?
                """,
                (
                    health,
                    model_count,
                    checked_at,
                    error_code,
                    error_hint,
                    checked_at,
                    self._tenant_id(tenant_id),
                    connection_id,
                ),
            )
        return self.get_connection(tenant_id, connection_id)

    def get_policy(self, tenant_id: str) -> RouterPolicy:
        clean_tenant = self._tenant_id(tenant_id)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM router_policies WHERE tenant_id = ?",
                (clean_tenant,),
            ).fetchone()
        configured_engine = os.getenv("MODEL_ROUTER_ENGINE", "").strip().lower()
        if configured_engine == "sidecar":
            return RouterPolicy(
                tenant_id=clean_tenant,
                engine="sidecar",
                default_mode=(row["default_mode"] if row is not None else "auto"),
                canary_percent=(
                    row["canary_percent"] if row is not None else 0
                ),
                compression_mode=(
                    row["compression_mode"] if row is not None else "auto"
                ),
                updated_at=(row["updated_at"] if row is not None else None),
            )
        if row is None:
            engine = configured_engine or "sidecar"
            if engine not in {"sidecar", "shadow", "native_canary", "native"}:
                engine = "sidecar"
            try:
                canary_percent = int(
                    os.getenv("MODEL_ROUTER_CANARY_PERCENT", "0")
                )
            except ValueError:
                canary_percent = 0
            return RouterPolicy(
                tenant_id=clean_tenant,
                engine=engine,
                canary_percent=max(0, min(100, canary_percent)),
            )
        return RouterPolicy(
            tenant_id=clean_tenant,
            engine=row["engine"],
            default_mode=row["default_mode"],
            canary_percent=row["canary_percent"],
            compression_mode=row["compression_mode"],
            updated_at=row["updated_at"],
        )

    def save_policy(self, tenant_id: str, policy: RouterPolicy) -> RouterPolicy:
        clean_tenant = self._tenant_id(tenant_id)
        now = utc_now()
        normalized = policy.model_copy(
            update={"tenant_id": clean_tenant, "updated_at": now}
        )
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO router_policies (
                    tenant_id, engine, default_mode, canary_percent,
                    compression_mode, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id) DO UPDATE SET
                    engine = excluded.engine,
                    default_mode = excluded.default_mode,
                    canary_percent = excluded.canary_percent,
                    compression_mode = excluded.compression_mode,
                    updated_at = excluded.updated_at
                """,
                (
                    clean_tenant,
                    normalized.engine,
                    normalized.default_mode,
                    normalized.canary_percent,
                    normalized.compression_mode,
                    now,
                ),
            )
        return normalized

    def count_schema_tenant_columns(self) -> dict[str, bool]:
        tables = (
            "router_connections",
            "router_policies",
            "router_candidate_stats",
            "router_decisions",
            "compression_runs",
            "video_jobs",
            "audio_jobs",
            "realtime_calls",
        )
        with self._lock, self._connect() as connection:
            return {
                table: any(
                    row["name"] == "tenant_id"
                    for row in connection.execute(
                        f"PRAGMA table_info({table})"
                    ).fetchall()
                )
                for table in tables
            }

    def create_video_job_if_absent(
        self,
        tenant_id: str,
        *,
        job_id: str,
        idempotency_key_hash: str,
        connection_id: str | None,
        requested_model: str,
        provider: str,
        duration: int | None,
        resolution: str | None,
        aspect_ratio: str | None,
        generate_audio: bool,
        seed: int | None,
        has_first_frame: bool,
        has_last_frame: bool = False,
        reference_image_count: int = 0,
        provider_option_keys: list[str] | None = None,
    ) -> tuple[dict[str, object], bool]:
        """Atomically claim an idempotency key before a paid upstream call."""

        clean_tenant = self._tenant_id(tenant_id)
        now = utc_now()
        values = (
            job_id,
            clean_tenant,
            idempotency_key_hash,
            connection_id,
            requested_model,
            provider,
            "queued",
            duration,
            resolution,
            aspect_ratio,
            int(generate_audio),
            seed,
            int(has_first_frame),
            int(has_last_frame),
            max(0, int(reference_image_count)),
            json.dumps(
                sorted(set(provider_option_keys or [])),
                ensure_ascii=True,
                separators=(",", ":"),
            ),
            now,
            now,
        )
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO video_jobs (
                    id, tenant_id, idempotency_key_hash, connection_id,
                    requested_model, provider, status, duration, resolution,
                    aspect_ratio, generate_audio, seed, has_first_frame,
                    has_last_frame, reference_image_count,
                    provider_option_keys, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                values,
            )
            created = cursor.rowcount == 1
            row = connection.execute(
                """
                SELECT * FROM video_jobs
                WHERE tenant_id = ? AND idempotency_key_hash = ?
                """,
                (clean_tenant, idempotency_key_hash),
            ).fetchone()
        if row is None:
            raise RouterRepositoryError("video job idempotency claim failed")
        return dict(row), created

    def get_video_job(
        self, tenant_id: str, job_id: str
    ) -> dict[str, object] | None:
        clean_tenant = self._tenant_id(tenant_id)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM video_jobs
                WHERE tenant_id = ? AND id = ?
                """,
                (clean_tenant, job_id),
            ).fetchone()
        return dict(row) if row is not None else None

    def get_video_job_by_idempotency_hash(
        self, tenant_id: str, idempotency_key_hash: str
    ) -> dict[str, object] | None:
        clean_tenant = self._tenant_id(tenant_id)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM video_jobs
                WHERE tenant_id = ? AND idempotency_key_hash = ?
                """,
                (clean_tenant, idempotency_key_hash),
            ).fetchone()
        return dict(row) if row is not None else None

    def list_video_jobs(
        self, tenant_id: str, *, limit: int = 50
    ) -> list[dict[str, object]]:
        clean_tenant = self._tenant_id(tenant_id)
        safe_limit = max(1, min(int(limit), 100))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM video_jobs
                WHERE tenant_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (clean_tenant, safe_limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_video_job(
        self,
        tenant_id: str,
        job_id: str,
        **changes: object,
    ) -> dict[str, object] | None:
        allowed = {
            "decision_id",
            "actual_model",
            "upstream_job_id",
            "generation_id",
            "status",
            "cost_usd",
            "cost_kind",
            "error_code",
            "output_count",
        }
        selected = {
            key: value for key, value in changes.items() if key in allowed
        }
        if not selected:
            return self.get_video_job(tenant_id, job_id)
        selected["updated_at"] = utc_now()
        assignments = ", ".join(f"{key} = ?" for key in selected)
        values = list(selected.values())
        values.extend((self._tenant_id(tenant_id), job_id))
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE video_jobs SET {assignments}
                WHERE tenant_id = ? AND id = ?
                """,
                values,
            )
            if cursor.rowcount != 1:
                return None
        return self.get_video_job(tenant_id, job_id)

    def delete_video_job(self, tenant_id: str, job_id: str) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM video_jobs
                WHERE tenant_id = ? AND id = ?
                """,
                (self._tenant_id(tenant_id), job_id),
            )
        return cursor.rowcount == 1

    def create_audio_job_if_absent(
        self,
        tenant_id: str,
        *,
        job_id: str,
        idempotency_key_hash: str,
        connection_id: str | None,
        requested_model: str,
        provider: str,
        has_image: bool,
        cost_usd: float | None = None,
        cost_kind: str = "unavailable",
    ) -> tuple[dict[str, object], bool]:
        """Atomically claim an idempotency key before a paid audio call."""

        clean_tenant = self._tenant_id(tenant_id)
        now = utc_now()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO audio_jobs (
                    id, tenant_id, idempotency_key_hash, connection_id,
                    requested_model, provider, status, has_image,
                    cost_usd, cost_kind, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    clean_tenant,
                    idempotency_key_hash,
                    connection_id,
                    requested_model,
                    provider,
                    int(has_image),
                    cost_usd,
                    cost_kind,
                    now,
                    now,
                ),
            )
            created = cursor.rowcount == 1
            row = connection.execute(
                """
                SELECT * FROM audio_jobs
                WHERE tenant_id = ? AND idempotency_key_hash = ?
                """,
                (clean_tenant, idempotency_key_hash),
            ).fetchone()
        if row is None:
            raise RouterRepositoryError("audio job idempotency claim failed")
        return dict(row), created

    def get_audio_job(
        self, tenant_id: str, job_id: str
    ) -> dict[str, object] | None:
        clean_tenant = self._tenant_id(tenant_id)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM audio_jobs
                WHERE tenant_id = ? AND id = ?
                """,
                (clean_tenant, job_id),
            ).fetchone()
        return dict(row) if row is not None else None

    def get_audio_job_by_idempotency_hash(
        self, tenant_id: str, idempotency_key_hash: str
    ) -> dict[str, object] | None:
        clean_tenant = self._tenant_id(tenant_id)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM audio_jobs
                WHERE tenant_id = ? AND idempotency_key_hash = ?
                """,
                (clean_tenant, idempotency_key_hash),
            ).fetchone()
        return dict(row) if row is not None else None

    def list_audio_jobs(
        self, tenant_id: str, *, limit: int = 50
    ) -> list[dict[str, object]]:
        clean_tenant = self._tenant_id(tenant_id)
        safe_limit = max(1, min(int(limit), 100))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM audio_jobs
                WHERE tenant_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (clean_tenant, safe_limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_audio_job(
        self,
        tenant_id: str,
        job_id: str,
        **changes: object,
    ) -> dict[str, object] | None:
        allowed = {
            "decision_id",
            "actual_model",
            "generation_id",
            "status",
            "output_bytes",
            "cost_usd",
            "cost_kind",
            "error_code",
            "expires_at",
        }
        selected = {
            key: value for key, value in changes.items() if key in allowed
        }
        if not selected:
            return self.get_audio_job(tenant_id, job_id)
        selected["updated_at"] = utc_now()
        assignments = ", ".join(f"{key} = ?" for key in selected)
        values = list(selected.values())
        values.extend((self._tenant_id(tenant_id), job_id))
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE audio_jobs SET {assignments}
                WHERE tenant_id = ? AND id = ?
                """,
                values,
            )
            if cursor.rowcount != 1:
                return None
        return self.get_audio_job(tenant_id, job_id)

    def delete_audio_job(self, tenant_id: str, job_id: str) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM audio_jobs
                WHERE tenant_id = ? AND id = ?
                """,
                (self._tenant_id(tenant_id), job_id),
            )
        return cursor.rowcount == 1

    def create_realtime_call(
        self,
        tenant_id: str,
        *,
        session_id: str,
        decision_id: str | None,
        connection_id: str,
        model_id: str,
        provider: str,
        voice: str,
        vad_mode: str,
        language: str,
        expires_at: str,
    ) -> dict[str, object]:
        clean_tenant = self._tenant_id(tenant_id)
        now = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO realtime_calls (
                    id, tenant_id, decision_id, connection_id, model_id,
                    provider, voice, vad_mode, language, status, expires_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'connecting', ?, ?, ?)
                """,
                (
                    session_id,
                    clean_tenant,
                    decision_id,
                    connection_id,
                    model_id,
                    provider,
                    voice,
                    vad_mode,
                    language,
                    expires_at,
                    now,
                    now,
                ),
            )
        row = self.get_realtime_call(clean_tenant, session_id)
        if row is None:
            raise RouterRepositoryError("realtime call creation failed")
        return row

    def get_realtime_call(
        self,
        tenant_id: str,
        session_id: str,
    ) -> dict[str, object] | None:
        clean_tenant = self._tenant_id(tenant_id)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM realtime_calls
                WHERE tenant_id = ? AND id = ?
                """,
                (clean_tenant, session_id),
            ).fetchone()
        return dict(row) if row is not None else None

    def list_active_realtime_calls(
        self,
        tenant_id: str,
    ) -> list[dict[str, object]]:
        clean_tenant = self._tenant_id(tenant_id)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM realtime_calls
                WHERE tenant_id = ? AND status IN ('connecting', 'active')
                ORDER BY created_at ASC, id ASC
                """,
                (clean_tenant,),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_realtime_call(
        self,
        tenant_id: str,
        session_id: str,
        **changes: object,
    ) -> dict[str, object] | None:
        allowed = {
            "upstream_call_id",
            "status",
            "started_at",
            "ended_at",
            "duration_seconds",
            "input_tokens",
            "output_tokens",
            "cost_usd",
            "cost_kind",
            "error_code",
        }
        selected = {
            key: value for key, value in changes.items() if key in allowed
        }
        if not selected:
            return self.get_realtime_call(tenant_id, session_id)
        selected["updated_at"] = utc_now()
        assignments = ", ".join(f"{key} = ?" for key in selected)
        values = list(selected.values())
        values.extend((self._tenant_id(tenant_id), session_id))
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE realtime_calls SET {assignments}
                WHERE tenant_id = ? AND id = ?
                """,
                values,
            )
            if cursor.rowcount != 1:
                return None
        return self.get_realtime_call(tenant_id, session_id)

    def get_candidate_stats(
        self,
        tenant_id: str,
        connection_id: str,
        model_id: str,
    ) -> dict[str, object]:
        clean_tenant = self._tenant_id(tenant_id)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM router_candidate_stats
                WHERE tenant_id = ? AND connection_id = ? AND model_id = ?
                """,
                (clean_tenant, connection_id, model_id),
            ).fetchone()
        if row is None:
            return {
                "success_count": 0,
                "failure_count": 0,
                "latency_ema_ms": None,
                "p95_latency_ms": None,
                "latency_stddev_ms": None,
                "ttft_ema_ms": None,
                "e2e_ema_ms": None,
                "tokens_per_second_ema": None,
                "sample_count": 0,
                "error_rate": 0.0,
                "consecutive_failures": 0,
                "breaker_state": "closed",
                "breaker_open_until": None,
                "last_success_at": None,
            }
        breaker_state = str(row["breaker_state"])
        open_until = row["breaker_open_until"]
        if (
            breaker_state == "open"
            and open_until is not None
            and float(open_until) <= time.time()
        ):
            breaker_state = "half_open"
        total = int(row["success_count"]) + int(row["failure_count"])
        return {
            "success_count": int(row["success_count"]),
            "failure_count": int(row["failure_count"]),
            "latency_ema_ms": (
                float(row["latency_ema_ms"])
                if row["latency_ema_ms"] is not None
                else None
            ),
            "p95_latency_ms": self._candidate_latency_p95(
                clean_tenant, connection_id, model_id
            ),
            "latency_stddev_ms": (
                float(row["latency_stddev_ms"])
                if int(row["sample_count"] or 0) > 0
                else None
            ),
            "ttft_ema_ms": row["ttft_ema_ms"],
            "e2e_ema_ms": row["e2e_ema_ms"],
            "tokens_per_second_ema": row["tokens_per_second_ema"],
            "sample_count": int(row["sample_count"] or 0),
            "error_rate": (
                float(row["failure_count"]) / total if total > 0 else 0.0
            ),
            "consecutive_failures": int(row["consecutive_failures"]),
            "breaker_state": breaker_state,
            "breaker_open_until": open_until,
            "last_success_at": row["last_success_at"],
        }

    def get_candidate_stats_bulk(
        self, tenant_id: str
    ) -> dict[tuple[str, str], dict[str, object]]:
        clean_tenant = self._tenant_id(tenant_id)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM router_candidate_stats WHERE tenant_id = ?",
                (clean_tenant,),
            ).fetchall()
            sample_rows = connection.execute(
                """
                SELECT connection_id, model_id, e2e_ms
                FROM router_candidate_samples
                WHERE tenant_id = ? AND e2e_ms IS NOT NULL
                """,
                (clean_tenant,),
            ).fetchall()
        latencies: dict[tuple[str, str], list[float]] = {}
        for row in sample_rows:
            if row["connection_id"] is None or row["model_id"] is None:
                continue
            key = (str(row["connection_id"]), str(row["model_id"]))
            latencies.setdefault(key, []).append(float(row["e2e_ms"]))
        result: dict[tuple[str, str], dict[str, object]] = {}
        for row in rows:
            key = (str(row["connection_id"]), str(row["model_id"]))
            breaker_state = str(row["breaker_state"])
            open_until = row["breaker_open_until"]
            if (
                breaker_state == "open"
                and open_until is not None
                and float(open_until) <= time.time()
            ):
                breaker_state = "half_open"
            total = int(row["success_count"]) + int(row["failure_count"])
            sample_count = int(row["sample_count"] or 0)
            result[key] = {
                "success_count": int(row["success_count"]),
                "failure_count": int(row["failure_count"]),
                "latency_ema_ms": row["latency_ema_ms"],
                "p95_latency_ms": percentile(latencies.get(key, ()), 0.95),
                "latency_stddev_ms": (
                    float(row["latency_stddev_ms"])
                    if sample_count > 0
                    else None
                ),
                "ttft_ema_ms": row["ttft_ema_ms"],
                "e2e_ema_ms": row["e2e_ema_ms"],
                "tokens_per_second_ema": row["tokens_per_second_ema"],
                "sample_count": sample_count,
                "error_rate": (
                    float(row["failure_count"]) / total if total > 0 else 0.0
                ),
                "consecutive_failures": int(row["consecutive_failures"]),
                "breaker_state": breaker_state,
                "breaker_open_until": open_until,
                "last_success_at": row["last_success_at"],
            }
        return result

    def _candidate_latency_p95(
        self, tenant_id: str, connection_id: str, model_id: str
    ) -> float | None:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT e2e_ms FROM router_candidate_samples
                WHERE tenant_id = ? AND connection_id = ? AND model_id = ?
                    AND e2e_ms IS NOT NULL
                ORDER BY created_at DESC LIMIT 200
                """,
                (tenant_id, connection_id, model_id),
            ).fetchall()
        return percentile((row["e2e_ms"] for row in rows), 0.95)

    @staticmethod
    def _optional_float(value: object) -> float | None:
        try:
            parsed = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        return max(0.0, parsed)

    @staticmethod
    def _ema(previous: float | None, sample: float | None) -> float | None:
        if sample is None:
            return previous
        if previous is None:
            return sample
        return previous * 0.8 + sample * 0.2

    def record_candidate_outcome(
        self,
        tenant_id: str,
        connection_id: str,
        model_id: str,
        *,
        success: bool,
        latency_ms: float | None,
        ttft_ms: float | None = None,
        e2e_ms: float | None = None,
        tokens_per_second: float | None = None,
    ) -> dict[str, object]:
        clean_tenant = self._tenant_id(tenant_id)
        current = self.get_candidate_stats(
            clean_tenant, connection_id, model_id
        )
        success_count = int(current["success_count"]) + int(success)
        failure_count = int(current["failure_count"]) + int(not success)
        consecutive = 0 if success else int(current["consecutive_failures"]) + 1
        old_latency = self._optional_float(current.get("latency_ema_ms"))
        sample = self._optional_float(latency_ms)
        latency_ema = self._ema(old_latency, sample)
        latency_stddev = (
            self._ema(
                self._optional_float(current.get("latency_stddev_ms")),
                abs(sample - old_latency)
                if sample is not None and old_latency is not None
                else None,
            )
            or 0.0
        )
        ttft_ema = self._ema(
            self._optional_float(current.get("ttft_ema_ms")),
            self._optional_float(ttft_ms),
        )
        e2e_ema = self._ema(
            self._optional_float(current.get("e2e_ema_ms")),
            self._optional_float(e2e_ms),
        )
        tps_ema = self._ema(
            self._optional_float(current.get("tokens_per_second_ema")),
            self._optional_float(tokens_per_second),
        )
        sample_count = int(current.get("sample_count") or 0) + int(
            any(
                value is not None
                for value in (sample, ttft_ms, e2e_ms, tokens_per_second)
            )
        )
        breaker_state = "closed"
        breaker_open_until: float | None = None
        if not success and consecutive >= 3:
            breaker_state = "open"
            breaker_open_until = time.time() + min(
                1800.0, 300.0 * (2 ** (consecutive - 3))
            )
        last_success_at = utc_now() if success else current["last_success_at"]
        now = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO router_candidate_stats (
                    tenant_id, connection_id, model_id, success_count,
                    failure_count, latency_ema_ms, latency_stddev_ms,
                    ttft_ema_ms, e2e_ema_ms, tokens_per_second_ema,
                    sample_count,
                    consecutive_failures, breaker_state, breaker_open_until,
                    last_success_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, connection_id, model_id) DO UPDATE SET
                    success_count = excluded.success_count,
                    failure_count = excluded.failure_count,
                    latency_ema_ms = excluded.latency_ema_ms,
                    latency_stddev_ms = excluded.latency_stddev_ms,
                    ttft_ema_ms = excluded.ttft_ema_ms,
                    e2e_ema_ms = excluded.e2e_ema_ms,
                    tokens_per_second_ema = excluded.tokens_per_second_ema,
                    sample_count = excluded.sample_count,
                    consecutive_failures = excluded.consecutive_failures,
                    breaker_state = excluded.breaker_state,
                    breaker_open_until = excluded.breaker_open_until,
                    last_success_at = excluded.last_success_at,
                    updated_at = excluded.updated_at
                """,
                (
                    clean_tenant,
                    connection_id,
                    model_id,
                    success_count,
                    failure_count,
                    latency_ema,
                    latency_stddev,
                    ttft_ema,
                    e2e_ema,
                    tps_ema,
                    sample_count,
                    consecutive,
                    breaker_state,
                    breaker_open_until,
                    last_success_at,
                    now,
                ),
            )
        return self.get_candidate_stats(clean_tenant, connection_id, model_id)

    def record_routing_decision(
        self,
        tenant_id: str,
        *,
        session_id_hash: str | None,
        engine: str,
        strategy: str,
        connection_id: str | None,
        model_id: str | None,
        reason_codes: list[str],
        outcome: str | None = None,
        operation: str = "chat",
        input_bytes: int | None = None,
        budget_limit_usd: float | None = None,
        reserved_cost_usd: float | None = None,
        algorithm_version: str | None = None,
        config_hash: str | None = None,
        task_type: str | None = None,
        task_level: str | None = None,
        selection_kind: str | None = None,
        score_tier: str | None = None,
        planning_latency_ms: float | None = None,
        eligible_count: int | None = None,
        finalist_count: int | None = None,
    ) -> str:
        clean_tenant = self._tenant_id(tenant_id)
        decision_id = f"decision_{uuid.uuid4().hex}"
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO router_decisions (
                    id, tenant_id, session_id_hash, engine, strategy, operation,
                    selected_connection_id, selected_model_id,
                    reason_codes_json, outcome, input_bytes, budget_limit_usd,
                    reserved_cost_usd, budget_status, algorithm_version,
                    config_hash, task_type, task_level, selection_kind,
                    score_tier, planning_latency_ms, eligible_count,
                    finalist_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    clean_tenant,
                    session_id_hash,
                    engine,
                    strategy,
                    operation,
                    connection_id,
                    model_id,
                    json.dumps(reason_codes, ensure_ascii=False),
                    outcome,
                    max(0, int(input_bytes)) if input_bytes is not None else None,
                    budget_limit_usd,
                    reserved_cost_usd,
                    "reserved" if budget_limit_usd is not None else None,
                    algorithm_version,
                    config_hash,
                    task_type,
                    task_level,
                    selection_kind,
                    score_tier,
                    planning_latency_ms,
                    eligible_count,
                    finalist_count,
                    utc_now(),
                ),
            )
        return decision_id

    def update_routing_decision_usage(
        self,
        tenant_id: str,
        decision_id: str,
        *,
        outcome: str,
        media_seconds: float | None,
        settled_cost_usd: float | None,
        cost_status: str,
        output_bytes: int | None = None,
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE router_decisions
                SET outcome = ?, media_seconds = ?,
                    settled_cost_usd = ?, budget_status = ?,
                    output_bytes = COALESCE(?, output_bytes)
                WHERE tenant_id = ? AND id = ?
                """,
                (
                    outcome,
                    (
                        max(0.0, float(media_seconds))
                        if media_seconds is not None
                        else None
                    ),
                    (
                        max(0.0, float(settled_cost_usd))
                        if settled_cost_usd is not None
                        else None
                    ),
                    cost_status,
                    (
                        max(0, int(output_bytes))
                        if output_bytes is not None
                        else None
                    ),
                    self._tenant_id(tenant_id),
                    decision_id,
                ),
            )

    def update_routing_decision_outcome(
        self,
        tenant_id: str,
        decision_id: str,
        outcome: str,
        *,
        ttft_ms: float | None = None,
        e2e_ms: float | None = None,
        output_tokens: int | None = None,
        tokens_per_second: float | None = None,
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE router_decisions
                SET outcome = ?, ttft_ms = ?, e2e_ms = ?,
                    output_tokens = ?, tokens_per_second = ?
                WHERE tenant_id = ? AND id = ?
                """,
                (
                    outcome,
                    self._optional_float(ttft_ms),
                    self._optional_float(e2e_ms),
                    max(0, int(output_tokens)) if output_tokens is not None else None,
                    self._optional_float(tokens_per_second),
                    self._tenant_id(tenant_id),
                    decision_id,
                ),
            )

    def record_router_candidate_sample(
        self,
        tenant_id: str,
        *,
        connection_id: str | None,
        model_id: str | None,
        engine: str,
        algorithm_version: str,
        config_hash: str,
        task_type: str | None,
        success: bool,
        outcome: str,
        ttft_ms: float | None,
        e2e_ms: float | None,
        output_tokens: int | None,
        tokens_per_second: float | None,
        planning_latency_ms: float | None,
        created_at: str | None = None,
    ) -> str:
        clean_tenant = self._tenant_id(tenant_id)
        sample_id = f"sample_{uuid.uuid4().hex}"
        timestamp = created_at or utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO router_candidate_samples (
                    id, tenant_id, connection_id, model_id, engine,
                    algorithm_version, config_hash, task_type, success,
                    outcome, ttft_ms, e2e_ms, output_tokens,
                    tokens_per_second, planning_latency_ms, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sample_id,
                    clean_tenant,
                    connection_id,
                    model_id,
                    str(engine),
                    str(algorithm_version),
                    str(config_hash),
                    task_type,
                    int(bool(success)),
                    str(outcome or "unknown"),
                    self._optional_float(ttft_ms),
                    self._optional_float(e2e_ms),
                    max(0, int(output_tokens)) if output_tokens is not None else None,
                    self._optional_float(tokens_per_second),
                    self._optional_float(planning_latency_ms),
                    timestamp,
                ),
            )
            connection.execute(
                """
                DELETE FROM router_candidate_samples
                WHERE tenant_id = ? AND created_at < ?
                """,
                (
                    clean_tenant,
                    (datetime.now(UTC) - timedelta(days=30)).isoformat(),
                ),
            )
            if connection_id and model_id:
                connection.execute(
                    """
                    DELETE FROM router_candidate_samples
                    WHERE tenant_id = ? AND connection_id = ? AND model_id = ?
                        AND algorithm_version = ? AND id NOT IN (
                            SELECT id FROM router_candidate_samples
                            WHERE tenant_id = ? AND connection_id = ?
                                AND model_id = ? AND algorithm_version = ?
                            ORDER BY created_at DESC LIMIT 200
                        )
                    """,
                    (
                        clean_tenant,
                        connection_id,
                        model_id,
                        algorithm_version,
                        clean_tenant,
                        connection_id,
                        model_id,
                        algorithm_version,
                    ),
                )
        return sample_id

    def save_native_gate_approval(
        self,
        tenant_id: str,
        *,
        algorithm_version: str,
        config_hash: str,
        no_open_p0_p1: bool,
        drills: dict[str, bool],
    ) -> dict[str, object]:
        if not no_open_p0_p1 or not all(drills.get(item) for item in REQUIRED_DRILLS):
            raise ValueError("all safety drills and the P0/P1 attestation are required")
        clean_tenant = self._tenant_id(tenant_id)
        approved_at = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO router_gate_approvals (
                    tenant_id, algorithm_version, config_hash,
                    no_open_p0_p1, drills_json, approved_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id) DO UPDATE SET
                    algorithm_version = excluded.algorithm_version,
                    config_hash = excluded.config_hash,
                    no_open_p0_p1 = excluded.no_open_p0_p1,
                    drills_json = excluded.drills_json,
                    approved_at = excluded.approved_at
                """,
                (
                    clean_tenant,
                    algorithm_version,
                    config_hash,
                    1,
                    json.dumps(drills, sort_keys=True),
                    approved_at,
                ),
            )
        return self.get_native_gate_approval(clean_tenant) or {}

    def get_native_gate_approval(self, tenant_id: str) -> dict[str, object] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM router_gate_approvals WHERE tenant_id = ?",
                (self._tenant_id(tenant_id),),
            ).fetchone()
        if row is None:
            return None
        return {
            "algorithm_version": row["algorithm_version"],
            "config_hash": row["config_hash"],
            "no_open_p0_p1": bool(row["no_open_p0_p1"]),
            "drills": json.loads(str(row["drills_json"] or "{}")),
            "approved_at": row["approved_at"],
        }

    def revoke_native_gate_approval(self, tenant_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "DELETE FROM router_gate_approvals WHERE tenant_id = ?",
                (self._tenant_id(tenant_id),),
            )

    def settle_routing_budget(
        self,
        tenant_id: str,
        decision_id: str,
        *,
        settled_cost_usd: float | None,
        status: str,
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE router_decisions
                SET settled_cost_usd = ?, budget_status = ?
                WHERE tenant_id = ? AND id = ?
                """,
                (
                    settled_cost_usd,
                    status,
                    self._tenant_id(tenant_id),
                    decision_id,
                ),
            )

    def get_last_known_good(
        self, tenant_id: str, session_id_hash: str | None
    ) -> tuple[str, str] | None:
        if not session_id_hash:
            return None
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT selected_connection_id, selected_model_id
                FROM router_decisions
                WHERE tenant_id = ? AND session_id_hash = ?
                    AND outcome = 'success'
                    AND selected_connection_id IS NOT NULL
                    AND selected_model_id IS NOT NULL
                ORDER BY created_at DESC LIMIT 1
                """,
                (self._tenant_id(tenant_id), session_id_hash),
            ).fetchone()
        if row is None:
            return None
        return str(row["selected_connection_id"]), str(row["selected_model_id"])

    def record_compression_run(
        self,
        tenant_id: str,
        *,
        request_id: str | None,
        profile: str,
        original_tokens: int,
        final_tokens: int,
        fidelity_status: str,
        fallback_reason: str | None,
    ) -> str:
        run_id = f"compression_{uuid.uuid4().hex}"
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO compression_runs (
                    id, tenant_id, request_id, profile, original_tokens,
                    final_tokens, fidelity_status, fallback_reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    self._tenant_id(tenant_id),
                    request_id,
                    profile,
                    max(0, int(original_tokens)),
                    max(0, int(final_tokens)),
                    fidelity_status,
                    fallback_reason,
                    utc_now(),
                ),
            )
        return run_id

    def get_diagnostics(
        self, tenant_id: str, *, limit: int = 20
    ) -> dict[str, object]:
        clean_tenant = self._tenant_id(tenant_id)
        safe_limit = max(1, min(int(limit), 100))
        with self._lock, self._connect() as connection:
            decision_rows = connection.execute(
                """
                SELECT d.id, d.engine, d.strategy, d.operation,
                    d.selected_model_id, d.input_bytes, d.output_bytes,
                    d.media_seconds,
                    d.reason_codes_json, d.outcome, d.budget_limit_usd,
                    d.reserved_cost_usd, d.settled_cost_usd,
                    d.budget_status, d.algorithm_version, d.config_hash,
                    d.task_type, d.task_level, d.selection_kind,
                    d.score_tier, d.planning_latency_ms, d.eligible_count,
                    d.finalist_count, d.ttft_ms, d.e2e_ms,
                    d.output_tokens, d.tokens_per_second, d.created_at,
                    c.name AS connection_name
                FROM router_decisions d
                LEFT JOIN router_connections c
                    ON c.tenant_id = d.tenant_id
                    AND c.id = d.selected_connection_id
                WHERE d.tenant_id = ?
                ORDER BY d.created_at DESC
                LIMIT ?
                """,
                (clean_tenant, safe_limit),
            ).fetchall()
            compression_rows = connection.execute(
                """
                SELECT id, request_id, profile, original_tokens, final_tokens,
                    fidelity_status, fallback_reason, created_at
                FROM compression_runs
                WHERE tenant_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (clean_tenant, safe_limit),
            ).fetchall()
            stats_rows = connection.execute(
                """
                SELECT breaker_state, COUNT(*) AS count
                FROM router_candidate_stats
                WHERE tenant_id = ?
                GROUP BY breaker_state
                """,
                (clean_tenant,),
            ).fetchall()
            native_sample_rows = connection.execute(
                """
                SELECT
                    CASE WHEN outcome IN ('success', 'output_limit')
                        THEN 1 ELSE 0 END AS success,
                    outcome, ttft_ms, e2e_ms, output_tokens,
                    planning_latency_ms, created_at
                FROM router_decisions
                WHERE tenant_id = ? AND engine = 'native'
                    AND algorithm_version = ? AND config_hash = ?
                    AND outcome IS NOT NULL
                ORDER BY created_at ASC
                """,
                (clean_tenant, ALGORITHM_VERSION, CONFIG_HASH),
            ).fetchall()
            first_native_at = (
                native_sample_rows[0]["created_at"]
                if native_sample_rows
                else "9999-12-31T23:59:59+00:00"
            )
            sidecar_sample_rows = connection.execute(
                """
                SELECT success, outcome, ttft_ms, e2e_ms, output_tokens,
                    planning_latency_ms, created_at
                FROM router_candidate_samples
                WHERE tenant_id = ? AND engine = 'sidecar'
                    AND created_at >= ?
                ORDER BY created_at ASC
                """,
                (clean_tenant, first_native_at),
            ).fetchall()
            approval_row = connection.execute(
                "SELECT * FROM router_gate_approvals WHERE tenant_id = ?",
                (clean_tenant,),
            ).fetchone()
        approval = (
            {
                "algorithm_version": approval_row["algorithm_version"],
                "config_hash": approval_row["config_hash"],
                "no_open_p0_p1": bool(approval_row["no_open_p0_p1"]),
                "drills": json.loads(str(approval_row["drills_json"] or "{}")),
                "approved_at": approval_row["approved_at"],
            }
            if approval_row is not None
            else None
        )
        gate = evaluate_native_gate(
            [dict(row) for row in native_sample_rows],
            [dict(row) for row in sidecar_sample_rows],
            algorithm_version=ALGORITHM_VERSION,
            config_hash=CONFIG_HASH,
            approval=approval,
        )
        return {
            "tenant_id": clean_tenant,
            "redacted": True,
            "breaker_summary": {
                str(row["breaker_state"]): int(row["count"])
                for row in stats_rows
            },
            "migration_gate": gate,
            "recent_decisions": [
                {
                    "id": row["id"],
                    "engine": row["engine"],
                    "strategy": row["strategy"],
                    "operation": row["operation"],
                    "model_id": row["selected_model_id"],
                    "connection_name": row["connection_name"],
                    "input_bytes": row["input_bytes"],
                    "output_bytes": row["output_bytes"],
                    "media_seconds": row["media_seconds"],
                    "reason_codes": json.loads(
                        str(row["reason_codes_json"] or "[]")
                    ),
                    "outcome": row["outcome"],
                    "algorithm_version": row["algorithm_version"],
                    "config_hash": row["config_hash"],
                    "task_type": row["task_type"],
                    "task_level": row["task_level"],
                    "selection_kind": row["selection_kind"],
                    "score_tier": row["score_tier"],
                    "planning_latency_ms": row["planning_latency_ms"],
                    "eligible_count": row["eligible_count"],
                    "finalist_count": row["finalist_count"],
                    "ttft_ms": row["ttft_ms"],
                    "e2e_ms": row["e2e_ms"],
                    "output_tokens": row["output_tokens"],
                    "tokens_per_second": row["tokens_per_second"],
                    "budget": {
                        "limit_usd": row["budget_limit_usd"],
                        "reserved_cost_usd": row["reserved_cost_usd"],
                        "settled_cost_usd": row["settled_cost_usd"],
                        "status": row["budget_status"],
                    },
                    "created_at": row["created_at"],
                }
                for row in decision_rows
            ],
            "gate_approval": approval,
            "recent_compressions": [
                {
                    "id": row["id"],
                    "request_id": row["request_id"],
                    "profile": row["profile"],
                    "original_tokens": row["original_tokens"],
                    "final_tokens": row["final_tokens"],
                    "saved_tokens": max(
                        0,
                        int(row["original_tokens"])
                        - int(row["final_tokens"]),
                    ),
                    "fidelity_status": row["fidelity_status"],
                    "fallback_reason": row["fallback_reason"],
                    "created_at": row["created_at"],
                }
                for row in compression_rows
            ],
        }

    def _resolve_master_key(self, supplied: str | bytes | None) -> bytes:
        if supplied:
            return self._normalize_key(supplied)
        canonical_key = os.getenv(CANONICAL_MASTER_KEY_ENV, "").strip()
        if canonical_key:
            return self._normalize_key(canonical_key)
        require_external = os.getenv(
            REQUIRE_EXTERNAL_MASTER_KEY_ENV, "false"
        ).strip().lower() in {"1", "true", "yes", "on"}
        if require_external:
            raise RouterCredentialUnavailable(
                f"{CANONICAL_MASTER_KEY_ENV} is required for provider management."
            )
        legacy_key = os.getenv(LEGACY_MASTER_KEY_ENV, "").strip()
        if legacy_key:
            logger.warning(
                "%s is deprecated; configure %s and run the credential migration command.",
                LEGACY_MASTER_KEY_ENV,
                CANONICAL_MASTER_KEY_ENV,
            )
            return self._normalize_key(legacy_key)
        if self.master_key_path.exists():
            return self._normalize_key(self.master_key_path.read_bytes().strip())
        key = Fernet.generate_key()
        temporary = self.master_key_path.with_suffix(".tmp")
        temporary.write_bytes(key)
        os.replace(temporary, self.master_key_path)
        try:
            os.chmod(self.master_key_path, 0o600)
        except OSError:
            pass
        return key

    def _verify_or_record_master_key(self) -> None:
        fingerprint = self._key_fingerprint(self._master_key)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM router_metadata WHERE key = ?",
                (MASTER_KEY_FINGERPRINT_METADATA_KEY,),
            ).fetchone()
            if row is not None:
                if not hmac.compare_digest(str(row["value"]), fingerprint):
                    raise RouterCredentialUnavailable(
                        "The configured credential master key does not match the router database. "
                        "Run the explicit credential migration command or restore the previous key."
                    )
                return
            encrypted_rows = connection.execute(
                "SELECT api_key_ciphertext FROM router_connections"
            ).fetchall()
            try:
                for encrypted in encrypted_rows:
                    self._fernet.decrypt(
                        str(encrypted["api_key_ciphertext"]).encode("ascii")
                    )
            except (InvalidToken, ValueError) as exc:
                raise RouterCredentialUnavailable(
                    "Existing provider credentials cannot be read with the configured master key. "
                    "Run the explicit credential migration command."
                ) from exc
            now = utc_now()
            connection.executemany(
                """
                INSERT INTO router_metadata (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (
                    (MASTER_KEY_FINGERPRINT_METADATA_KEY, fingerprint, now),
                    (MASTER_KEY_VERSION_METADATA_KEY, "1", now),
                ),
            )

    @staticmethod
    def _key_fingerprint(key: bytes) -> str:
        return hashlib.sha256(key).hexdigest()

    @staticmethod
    def _normalize_key(value: str | bytes) -> bytes:
        raw = value.encode("utf-8") if isinstance(value, str) else bytes(value)
        try:
            Fernet(raw)
            return raw
        except ValueError:
            return base64.urlsafe_b64encode(hashlib.sha256(raw).digest())

    @staticmethod
    def _public_connection(row: sqlite3.Row) -> RouterConnection:
        kind = row["kind"]
        try:
            decoded_scopes = json.loads(row["scopes_json"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            decoded_scopes = default_connection_scopes(kind)
        if not isinstance(decoded_scopes, list):
            decoded_scopes = default_connection_scopes(kind)
        scopes = normalize_connection_scopes(
            [
                scope
                for scope in decoded_scopes
                if scope in {"chat", "audio", "realtime"}
            ],
            kind=kind,
        )
        return RouterConnection(
            id=row["id"],
            tenant_id=row["tenant_id"],
            name=row["name"],
            kind=kind,
            base_url=row["base_url"],
            masked_key=row["masked_key"],
            scopes=scopes,
            enabled=bool(row["enabled"]),
            health=row["health"],
            model_count=row["model_count"],
            last_checked_at=row["last_checked_at"],
            last_error_code=row["last_error_code"],
            last_error_hint=row["last_error_hint"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _mask(value: str) -> str:
        if len(value) <= 4:
            return "*" * len(value)
        return f"{value[:2]}{'*' * min(12, len(value) - 4)}{value[-2:]}"

    @staticmethod
    def _tenant_id(value: str) -> str:
        tenant_id = str(value or "").strip()
        if not tenant_id or len(tenant_id) > 120:
            raise RouterRepositoryError("tenant_id is invalid")
        return tenant_id
