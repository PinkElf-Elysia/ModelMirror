from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken

from .schemas import (
    RouterConnection,
    RouterConnectionCreate,
    RouterConnectionUpdate,
    RouterPolicy,
    default_connection_scopes,
    normalize_connection_scopes,
)


SCHEMA_VERSION = 10
DEFAULT_TENANT_ID = "local"


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
        self._fernet = Fernet(self._resolve_master_key(master_key))
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        schema = """
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
                "latency_ema_ms": 1000.0,
                "latency_stddev_ms": 0.0,
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
            "latency_ema_ms": float(row["latency_ema_ms"] or 1000),
            "latency_stddev_ms": float(row["latency_stddev_ms"] or 0),
            "error_rate": (
                float(row["failure_count"]) / total if total > 0 else 0.0
            ),
            "consecutive_failures": int(row["consecutive_failures"]),
            "breaker_state": breaker_state,
            "breaker_open_until": open_until,
            "last_success_at": row["last_success_at"],
        }

    def record_candidate_outcome(
        self,
        tenant_id: str,
        connection_id: str,
        model_id: str,
        *,
        success: bool,
        latency_ms: float | None,
    ) -> dict[str, object]:
        clean_tenant = self._tenant_id(tenant_id)
        current = self.get_candidate_stats(
            clean_tenant, connection_id, model_id
        )
        success_count = int(current["success_count"]) + int(success)
        failure_count = int(current["failure_count"]) + int(not success)
        consecutive = 0 if success else int(current["consecutive_failures"]) + 1
        old_latency = float(current["latency_ema_ms"])
        sample = max(0.0, float(latency_ms or old_latency))
        latency_ema = sample if success_count + failure_count == 1 else (
            old_latency * 0.8 + sample * 0.2
        )
        latency_stddev = (
            float(current["latency_stddev_ms"]) * 0.8
            + abs(sample - old_latency) * 0.2
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
                    consecutive_failures, breaker_state, breaker_open_until,
                    last_success_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, connection_id, model_id) DO UPDATE SET
                    success_count = excluded.success_count,
                    failure_count = excluded.failure_count,
                    latency_ema_ms = excluded.latency_ema_ms,
                    latency_stddev_ms = excluded.latency_stddev_ms,
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
                    reserved_cost_usd, budget_status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        self, tenant_id: str, decision_id: str, outcome: str
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE router_decisions SET outcome = ?
                WHERE tenant_id = ? AND id = ?
                """,
                (outcome, self._tenant_id(tenant_id), decision_id),
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
                    d.budget_status, d.created_at,
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
            aggregate = connection.execute(
                """
                SELECT
                    COUNT(*) AS request_count,
                    SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END)
                        AS success_count,
                    SUM(CASE WHEN outcome = 'empty_stream' THEN 1 ELSE 0 END)
                        AS empty_stream_count,
                    MIN(created_at) AS first_request_at,
                    MAX(created_at) AS last_request_at
                FROM router_decisions
                WHERE tenant_id = ? AND engine = 'native'
                """,
                (clean_tenant,),
            ).fetchone()
        request_count = int(aggregate["request_count"] or 0)
        success_count = int(aggregate["success_count"] or 0)
        empty_count = int(aggregate["empty_stream_count"] or 0)
        first_request = aggregate["first_request_at"]
        observed_days = 0.0
        if first_request:
            try:
                observed_days = max(
                    0.0,
                    (
                        datetime.now(UTC)
                        - datetime.fromisoformat(str(first_request))
                    ).total_seconds()
                    / 86_400,
                )
            except ValueError:
                observed_days = 0.0
        gate = {
            "request_count": request_count,
            "success_count": success_count,
            "success_rate": (
                success_count / request_count if request_count else None
            ),
            "empty_stream_count": empty_count,
            "empty_stream_rate": (
                empty_count / request_count if request_count else None
            ),
            "first_request_at": first_request,
            "last_request_at": aggregate["last_request_at"],
            "observed_days": round(observed_days, 2),
            "request_gate_met": request_count >= 500,
            "duration_gate_met": observed_days >= 14,
            "automatic_native_default_allowed": (
                request_count >= 500 and observed_days >= 14
            ),
            "manual_safety_gates_required": True,
        }
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
        environment_key = os.getenv(
            "MODEL_ROUTER_CREDENTIAL_MASTER_KEY", ""
        ).strip()
        if environment_key:
            return self._normalize_key(environment_key)
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
