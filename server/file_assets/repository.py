from __future__ import annotations

import re
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from .contracts import FilePurpose


FileAssetStatus = Literal[
    "validating",
    "processing",
    "ready",
    "failed",
    "expired",
    "deleting",
    "deleted",
]

FILE_ASSET_SCHEMA_VERSION = 5

_ASSET_STATUSES = {
    "validating",
    "processing",
    "ready",
    "failed",
    "expired",
    "deleting",
    "deleted",
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_STORAGE_KEY = re.compile(
    r"^(?:blobs|artifacts)/[0-9a-f]{2}/[0-9a-f]{32}\.(?:blob|artifact)$"
)


class FileAssetRepositoryError(Exception):
    """Base error for tenant-scoped file metadata persistence."""


@dataclass(frozen=True, slots=True)
class FileAssetRecord:
    id: str
    tenant_id: str
    purpose: str
    scope_id: str
    display_name: str
    format_id: str
    media_type: str
    storage_key: str
    sha256: str
    byte_size: int
    status: str
    reference_count: int
    expires_at: str | None
    last_error_code: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class FileArtifactRecord:
    id: str
    tenant_id: str
    asset_id: str
    kind: str
    storage_key: str
    sha256: str
    byte_size: int
    status: str
    expires_at: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class GarbageCollectionClaim:
    claim_id: str
    asset: FileAssetRecord
    storage_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FileAnalysisConfirmationRecord:
    tenant_id: str
    asset_id: str
    scope_id: str
    mode: str
    target_id: str
    config_digest: str
    prompt_sha256: str
    paid_acknowledged: bool
    revision: int
    expires_at: str
    confirmed_at: str


@dataclass(frozen=True, slots=True)
class FileAnalysisJobRecord:
    id: str
    tenant_id: str
    asset_id: str
    scope_id: str
    mode: str
    target_id: str
    config_digest: str
    prompt_sha256: str
    confirmation_revision: int
    selected_pages: str
    page_count: int
    processed_pages: int
    status: str
    cancel_requested: bool
    result_artifact_id: str | None
    actual_cost_usd: str | None
    error_code: str | None
    created_at: str
    updated_at: str
    completed_at: str | None


@dataclass(frozen=True, slots=True)
class FileAnalysisSendConfirmationRecord:
    tenant_id: str
    asset_id: str
    scope_id: str
    artifact_id: str
    prompt_sha256: str
    revision: int
    confirmed_at: str


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class SQLiteFileAssetRepository:
    """SQLite metadata store; file bytes are represented only by opaque keys."""

    def __init__(self, storage_dir: str | Path) -> None:
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.database_path = self.storage_dir / "file-assets.sqlite3"
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS file_assets (
            id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            purpose TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            display_name TEXT NOT NULL,
            format_id TEXT NOT NULL,
            media_type TEXT NOT NULL,
            storage_key TEXT NOT NULL UNIQUE,
            sha256 TEXT NOT NULL,
            byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
            status TEXT NOT NULL CHECK (
                status IN ('validating','processing','ready','failed',
                           'expired','deleting','deleted')
            ),
            reference_count INTEGER NOT NULL DEFAULT 0
                CHECK (reference_count >= 0),
            expires_at TEXT,
            last_error_code TEXT,
            gc_claim_id TEXT,
            gc_claimed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, id)
        );
        CREATE TABLE IF NOT EXISTS file_bindings (
            id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            asset_id TEXT NOT NULL,
            purpose TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            confirmed_at TEXT,
            confirmed_handling TEXT CHECK (
                confirmed_handling IS NULL
                OR confirmed_handling IN ('native','extract')
            ),
            confirmation_revision INTEGER NOT NULL DEFAULT 0
                CHECK (confirmation_revision >= 0),
            created_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, id),
            UNIQUE (tenant_id, asset_id, purpose, scope_id),
            FOREIGN KEY (tenant_id, asset_id)
                REFERENCES file_assets (tenant_id, id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS file_artifacts (
            id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            asset_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            storage_key TEXT NOT NULL UNIQUE,
            sha256 TEXT NOT NULL,
            byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
            status TEXT NOT NULL CHECK (
                status IN ('validating','processing','ready','failed',
                           'expired','deleting','deleted')
            ),
            expires_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, id),
            FOREIGN KEY (tenant_id, asset_id)
                REFERENCES file_assets (tenant_id, id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS file_audit_events (
            id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            asset_id TEXT,
            event_type TEXT NOT NULL,
            sha256 TEXT,
            format_id TEXT,
            byte_size INTEGER,
            status TEXT,
            error_code TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, id)
        );
        CREATE TABLE IF NOT EXISTS file_scope_tombstones (
            tenant_id TEXT NOT NULL,
            purpose TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            blocked_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, purpose, scope_id)
        );
        CREATE TABLE IF NOT EXISTS file_scope_cleanup_assets (
            tenant_id TEXT NOT NULL,
            purpose TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            asset_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, purpose, scope_id, asset_id)
        );
        CREATE TABLE IF NOT EXISTS file_analysis_confirmations (
            tenant_id TEXT NOT NULL,
            asset_id TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            mode TEXT NOT NULL CHECK (mode IN ('vision','provider_ocr')),
            target_id TEXT NOT NULL,
            config_digest TEXT NOT NULL,
            prompt_sha256 TEXT NOT NULL,
            paid_acknowledged INTEGER NOT NULL DEFAULT 0
                CHECK (paid_acknowledged IN (0, 1)),
            revision INTEGER NOT NULL CHECK (revision >= 1),
            expires_at TEXT NOT NULL,
            confirmed_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, asset_id, scope_id),
            FOREIGN KEY (tenant_id, asset_id)
                REFERENCES file_assets (tenant_id, id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS file_analysis_jobs (
            id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            asset_id TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            mode TEXT NOT NULL CHECK (mode IN ('vision','provider_ocr')),
            target_id TEXT NOT NULL,
            config_digest TEXT NOT NULL,
            prompt_sha256 TEXT NOT NULL,
            confirmation_revision INTEGER NOT NULL CHECK (confirmation_revision >= 1),
            selected_pages TEXT NOT NULL,
            page_count INTEGER NOT NULL CHECK (page_count >= 1 AND page_count <= 20),
            processed_pages INTEGER NOT NULL DEFAULT 0
                CHECK (processed_pages >= 0 AND processed_pages <= page_count),
            status TEXT NOT NULL CHECK (
                status IN ('queued','running','completed','failed',
                           'cancel_requested','cancelled','interrupted')
            ),
            cancel_requested INTEGER NOT NULL DEFAULT 0
                CHECK (cancel_requested IN (0, 1)),
            result_artifact_id TEXT,
            actual_cost_usd TEXT,
            error_code TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            PRIMARY KEY (tenant_id, id),
            FOREIGN KEY (tenant_id, asset_id)
                REFERENCES file_assets (tenant_id, id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS file_analysis_send_confirmations (
            tenant_id TEXT NOT NULL,
            asset_id TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            artifact_id TEXT NOT NULL,
            prompt_sha256 TEXT NOT NULL,
            revision INTEGER NOT NULL CHECK (revision >= 1),
            confirmed_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, asset_id, scope_id),
            FOREIGN KEY (tenant_id, asset_id)
                REFERENCES file_assets (tenant_id, id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_file_assets_tenant_scope
            ON file_assets (tenant_id, purpose, scope_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_file_assets_tenant_expiry
            ON file_assets (tenant_id, status, expires_at);
        CREATE INDEX IF NOT EXISTS idx_file_bindings_tenant_scope
            ON file_bindings (tenant_id, purpose, scope_id);
        CREATE INDEX IF NOT EXISTS idx_file_artifacts_tenant_asset
            ON file_artifacts (tenant_id, asset_id);
        CREATE INDEX IF NOT EXISTS idx_file_audit_tenant_created
            ON file_audit_events (tenant_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_file_scope_cleanup
            ON file_scope_cleanup_assets (tenant_id, purpose, scope_id);
        CREATE INDEX IF NOT EXISTS idx_file_analysis_scope
            ON file_analysis_jobs (tenant_id, scope_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_file_analysis_status
            ON file_analysis_jobs (tenant_id, status, updated_at);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_file_analysis_confirmation_once
            ON file_analysis_jobs (
                tenant_id, asset_id, scope_id, confirmation_revision
            );
        CREATE INDEX IF NOT EXISTS idx_file_analysis_send_scope
            ON file_analysis_send_confirmations (tenant_id, scope_id);
        """
        with self._lock, self._connect() as connection:
            current_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if current_version > FILE_ASSET_SCHEMA_VERSION:
                raise FileAssetRepositoryError("file_asset_schema_is_newer")
            connection.executescript(schema)
            binding_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(file_bindings)"
                ).fetchall()
            }
            if "confirmed_at" not in binding_columns:
                connection.execute(
                    "ALTER TABLE file_bindings ADD COLUMN confirmed_at TEXT"
                )
            if "confirmed_handling" not in binding_columns:
                connection.execute(
                    "ALTER TABLE file_bindings ADD COLUMN confirmed_handling TEXT"
                )
            if "confirmation_revision" not in binding_columns:
                connection.execute(
                    "ALTER TABLE file_bindings ADD COLUMN confirmation_revision "
                    "INTEGER NOT NULL DEFAULT 0"
                )
            analysis_job_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(file_analysis_jobs)"
                ).fetchall()
            }
            if "prompt_sha256" not in analysis_job_columns:
                connection.execute(
                    "ALTER TABLE file_analysis_jobs ADD COLUMN prompt_sha256 TEXT "
                    "NOT NULL DEFAULT '"
                    + ("0" * 64)
                    + "'"
                )
            connection.execute(f"PRAGMA user_version = {FILE_ASSET_SCHEMA_VERSION}")

    def create_asset(
        self,
        tenant_id: str,
        *,
        purpose: FilePurpose | str,
        scope_id: str,
        display_name: str,
        format_id: str,
        media_type: str,
        storage_key: str,
        sha256: str,
        byte_size: int,
        status: FileAssetStatus = "validating",
        expires_at: datetime | str | None = None,
        asset_id: str | None = None,
        create_initial_binding: bool = False,
    ) -> FileAssetRecord:
        clean_tenant = _identifier(tenant_id, "tenant_id")
        clean_status = _status(status)
        if create_initial_binding and clean_status in {"expired", "deleting", "deleted"}:
            raise ValueError("an initial binding requires a mutable asset")
        now = utc_now()
        identifier = _identifier(asset_id or f"file_{uuid.uuid4().hex}", "asset_id")
        values = (
            identifier,
            clean_tenant,
            FilePurpose(purpose).value,
            _identifier(scope_id, "scope_id"),
            _display_name(display_name),
            _identifier(format_id, "format_id"),
            _media_type(media_type),
            _storage_key(storage_key, namespace="blobs"),
            _sha256(sha256),
            _non_negative_int(byte_size, "byte_size"),
            clean_status,
            int(create_initial_binding),
            _timestamp(expires_at),
            now,
            now,
        )
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if create_initial_binding:
                blocked = connection.execute(
                    """
                    SELECT 1 FROM file_scope_tombstones
                    WHERE tenant_id = ? AND purpose = ? AND scope_id = ?
                    """,
                    (clean_tenant, FilePurpose(purpose).value, _identifier(scope_id, "scope_id")),
                ).fetchone()
                if blocked is not None:
                    raise FileAssetRepositoryError("file_scope_blocked")
            connection.execute(
                """
                INSERT INTO file_assets (
                    id, tenant_id, purpose, scope_id, display_name, format_id,
                    media_type, storage_key, sha256, byte_size, status,
                    reference_count, expires_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            if create_initial_binding:
                connection.execute(
                    """
                    INSERT INTO file_bindings (
                        id, tenant_id, asset_id, purpose, scope_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"bind_{uuid.uuid4().hex}",
                        clean_tenant,
                        identifier,
                        FilePurpose(purpose).value,
                        _identifier(scope_id, "scope_id"),
                        now,
                    ),
                )
        record = self.get_asset(clean_tenant, identifier)
        if record is None:  # pragma: no cover - defensive SQLite guard
            raise FileAssetRepositoryError("asset_create_failed")
        return record

    def get_asset(self, tenant_id: str, asset_id: str) -> FileAssetRecord | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM file_assets WHERE tenant_id = ? AND id = ?",
                (_identifier(tenant_id, "tenant_id"), _identifier(asset_id, "asset_id")),
            ).fetchone()
        return _asset_record(row) if row is not None else None

    def set_asset_status(
        self,
        tenant_id: str,
        asset_id: str,
        status: FileAssetStatus,
        *,
        error_code: str | None = None,
    ) -> FileAssetRecord | None:
        clean_tenant = _identifier(tenant_id, "tenant_id")
        clean_asset = _identifier(asset_id, "asset_id")
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE file_assets
                SET status = ?, last_error_code = ?, updated_at = ?
                WHERE tenant_id = ? AND id = ?
                  AND status NOT IN ('expired','deleting','deleted')
                """,
                (
                    _status(status),
                    _optional_code(error_code),
                    utc_now(),
                    clean_tenant,
                    clean_asset,
                ),
            )
        return self.get_asset(clean_tenant, clean_asset)

    def binding_exists(
        self,
        tenant_id: str,
        asset_id: str,
        *,
        purpose: FilePurpose | str,
        scope_id: str,
    ) -> bool:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM file_bindings
                WHERE tenant_id = ? AND asset_id = ? AND purpose = ? AND scope_id = ?
                """,
                (
                    _identifier(tenant_id, "tenant_id"),
                    _identifier(asset_id, "asset_id"),
                    FilePurpose(purpose).value,
                    _identifier(scope_id, "scope_id"),
                ),
            ).fetchone()
        return row is not None

    def confirm_binding(
        self,
        tenant_id: str,
        asset_id: str,
        *,
        purpose: FilePurpose | str,
        scope_id: str,
        handling: Literal["native", "extract"],
    ) -> tuple[int, str] | None:
        clean_tenant = _identifier(tenant_id, "tenant_id")
        clean_asset = _identifier(asset_id, "asset_id")
        clean_scope = _identifier(scope_id, "scope_id")
        clean_handling = _file_handling(handling)
        confirmed_at = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE file_bindings
                SET confirmed_at = ?, confirmed_handling = ?,
                    confirmation_revision = confirmation_revision + 1
                WHERE tenant_id = ? AND asset_id = ?
                  AND purpose = ? AND scope_id = ?
                """,
                (
                    confirmed_at,
                    clean_handling,
                    clean_tenant,
                    clean_asset,
                    FilePurpose(purpose).value,
                    clean_scope,
                ),
            )
            if cursor.rowcount != 1:
                return None
            row = connection.execute(
                """
                SELECT confirmation_revision, confirmed_at
                FROM file_bindings
                WHERE tenant_id = ? AND asset_id = ?
                  AND purpose = ? AND scope_id = ?
                """,
                (
                    clean_tenant,
                    clean_asset,
                    FilePurpose(purpose).value,
                    clean_scope,
                ),
            ).fetchone()
        if row is None:  # pragma: no cover - transaction invariant
            return None
        return int(row["confirmation_revision"]), str(row["confirmed_at"])

    def binding_confirmation_matches(
        self,
        tenant_id: str,
        asset_id: str,
        *,
        purpose: FilePurpose | str,
        scope_id: str,
        handling: Literal["native", "extract"],
        revision: int,
    ) -> bool:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM file_bindings
                WHERE tenant_id = ? AND asset_id = ?
                  AND purpose = ? AND scope_id = ?
                  AND confirmed_at IS NOT NULL
                  AND confirmed_handling = ?
                  AND confirmation_revision = ?
                """,
                (
                    _identifier(tenant_id, "tenant_id"),
                    _identifier(asset_id, "asset_id"),
                    FilePurpose(purpose).value,
                    _identifier(scope_id, "scope_id"),
                    _file_handling(handling),
                    _positive_int(revision, "confirmation_revision"),
                ),
            ).fetchone()
        return row is not None

    def clear_binding_confirmation(
        self,
        tenant_id: str,
        asset_id: str,
        *,
        purpose: FilePurpose | str,
        scope_id: str,
    ) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE file_bindings
                SET confirmed_at = NULL, confirmed_handling = NULL
                WHERE tenant_id = ? AND asset_id = ?
                  AND purpose = ? AND scope_id = ?
                """,
                (
                    _identifier(tenant_id, "tenant_id"),
                    _identifier(asset_id, "asset_id"),
                    FilePurpose(purpose).value,
                    _identifier(scope_id, "scope_id"),
                ),
            )
        return cursor.rowcount == 1

    def confirm_analysis_send(
        self,
        tenant_id: str,
        asset_id: str,
        *,
        scope_id: str,
        artifact_id: str,
        prompt_sha256: str,
    ) -> FileAnalysisSendConfirmationRecord | None:
        clean_tenant = _identifier(tenant_id, "tenant_id")
        clean_asset = _identifier(asset_id, "asset_id")
        clean_scope = _identifier(scope_id, "scope_id")
        clean_artifact = _identifier(artifact_id, "artifact_id")
        clean_prompt = _sha256(prompt_sha256)
        confirmed_at = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            binding = connection.execute(
                """
                SELECT 1 FROM file_bindings
                WHERE tenant_id = ? AND asset_id = ?
                  AND purpose = 'chat' AND scope_id = ?
                """,
                (clean_tenant, clean_asset, clean_scope),
            ).fetchone()
            artifact = connection.execute(
                """
                SELECT 1 FROM file_artifacts
                WHERE tenant_id = ? AND asset_id = ? AND id = ?
                  AND kind = 'chat_visual_analysis_v1' AND status = 'ready'
                """,
                (clean_tenant, clean_asset, clean_artifact),
            ).fetchone()
            if binding is None or artifact is None:
                return None
            prior = connection.execute(
                """
                SELECT revision FROM file_analysis_send_confirmations
                WHERE tenant_id = ? AND asset_id = ? AND scope_id = ?
                """,
                (clean_tenant, clean_asset, clean_scope),
            ).fetchone()
            revision = int(prior["revision"]) + 1 if prior is not None else 1
            connection.execute(
                """
                INSERT INTO file_analysis_send_confirmations (
                    tenant_id, asset_id, scope_id, artifact_id,
                    prompt_sha256, revision, confirmed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (tenant_id, asset_id, scope_id) DO UPDATE SET
                    artifact_id = excluded.artifact_id,
                    prompt_sha256 = excluded.prompt_sha256,
                    revision = excluded.revision,
                    confirmed_at = excluded.confirmed_at
                """,
                (
                    clean_tenant,
                    clean_asset,
                    clean_scope,
                    clean_artifact,
                    clean_prompt,
                    revision,
                    confirmed_at,
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM file_analysis_send_confirmations
                WHERE tenant_id = ? AND asset_id = ? AND scope_id = ?
                """,
                (clean_tenant, clean_asset, clean_scope),
            ).fetchone()
        return _analysis_send_confirmation_record(row) if row is not None else None

    def analysis_send_confirmation_matches(
        self,
        tenant_id: str,
        asset_id: str,
        *,
        scope_id: str,
        artifact_id: str,
        prompt_sha256: str,
        revision: int,
    ) -> bool:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM file_analysis_send_confirmations AS confirmation
                INNER JOIN file_bindings AS binding
                  ON binding.tenant_id = confirmation.tenant_id
                 AND binding.asset_id = confirmation.asset_id
                 AND binding.scope_id = confirmation.scope_id
                 AND binding.purpose = 'chat'
                INNER JOIN file_artifacts AS artifact
                  ON artifact.tenant_id = confirmation.tenant_id
                 AND artifact.asset_id = confirmation.asset_id
                 AND artifact.id = confirmation.artifact_id
                 AND artifact.kind = 'chat_visual_analysis_v1'
                 AND artifact.status = 'ready'
                WHERE confirmation.tenant_id = ?
                  AND confirmation.asset_id = ?
                  AND confirmation.scope_id = ?
                  AND confirmation.artifact_id = ?
                  AND confirmation.prompt_sha256 = ?
                  AND confirmation.revision = ?
                """,
                (
                    _identifier(tenant_id, "tenant_id"),
                    _identifier(asset_id, "asset_id"),
                    _identifier(scope_id, "scope_id"),
                    _identifier(artifact_id, "artifact_id"),
                    _sha256(prompt_sha256),
                    _positive_int(revision, "confirmation_revision"),
                ),
            ).fetchone()
        return row is not None

    def clear_analysis_send_confirmation(
        self, tenant_id: str, asset_id: str, *, scope_id: str
    ) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM file_analysis_send_confirmations
                WHERE tenant_id = ? AND asset_id = ? AND scope_id = ?
                """,
                (
                    _identifier(tenant_id, "tenant_id"),
                    _identifier(asset_id, "asset_id"),
                    _identifier(scope_id, "scope_id"),
                ),
            )
        return cursor.rowcount == 1

    def confirm_analysis(
        self,
        tenant_id: str,
        asset_id: str,
        *,
        scope_id: str,
        mode: str,
        target_id: str,
        config_digest: str,
        prompt_sha256: str,
        paid_acknowledged: bool,
        expires_at: datetime | str,
    ) -> FileAnalysisConfirmationRecord | None:
        """Bind an explicit one-shot analysis choice to one Chat asset revision."""

        clean_tenant = _identifier(tenant_id, "tenant_id")
        clean_asset = _identifier(asset_id, "asset_id")
        clean_scope = _identifier(scope_id, "scope_id")
        clean_mode = _analysis_mode(mode)
        clean_target = _identifier(target_id, "target_id")
        clean_config_digest = _sha256(config_digest)
        clean_prompt_sha256 = _sha256(prompt_sha256)
        clean_expires_at = _timestamp(expires_at)
        if clean_expires_at is None:  # pragma: no cover - required by signature
            raise ValueError("expires_at is required")
        confirmed_at = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_mutable_asset(connection, clean_tenant, clean_asset)
            binding = connection.execute(
                """
                SELECT 1 FROM file_bindings
                WHERE tenant_id = ? AND asset_id = ?
                  AND purpose = 'chat' AND scope_id = ?
                """,
                (clean_tenant, clean_asset, clean_scope),
            ).fetchone()
            if binding is None:
                return None
            prior = connection.execute(
                """
                SELECT revision FROM file_analysis_confirmations
                WHERE tenant_id = ? AND asset_id = ? AND scope_id = ?
                """,
                (clean_tenant, clean_asset, clean_scope),
            ).fetchone()
            revision = int(prior["revision"]) + 1 if prior is not None else 1
            connection.execute(
                """
                INSERT INTO file_analysis_confirmations (
                    tenant_id, asset_id, scope_id, mode, target_id,
                    config_digest, prompt_sha256, paid_acknowledged,
                    revision, expires_at, confirmed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (tenant_id, asset_id, scope_id) DO UPDATE SET
                    mode = excluded.mode,
                    target_id = excluded.target_id,
                    config_digest = excluded.config_digest,
                    prompt_sha256 = excluded.prompt_sha256,
                    paid_acknowledged = excluded.paid_acknowledged,
                    revision = excluded.revision,
                    expires_at = excluded.expires_at,
                    confirmed_at = excluded.confirmed_at
                """,
                (
                    clean_tenant,
                    clean_asset,
                    clean_scope,
                    clean_mode,
                    clean_target,
                    clean_config_digest,
                    clean_prompt_sha256,
                    int(bool(paid_acknowledged)),
                    revision,
                    clean_expires_at,
                    confirmed_at,
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM file_analysis_confirmations
                WHERE tenant_id = ? AND asset_id = ? AND scope_id = ?
                """,
                (clean_tenant, clean_asset, clean_scope),
            ).fetchone()
        return _analysis_confirmation_record(row) if row is not None else None

    def analysis_confirmation_matches(
        self,
        tenant_id: str,
        asset_id: str,
        *,
        scope_id: str,
        mode: str,
        target_id: str,
        config_digest: str,
        prompt_sha256: str,
        paid_acknowledged: bool,
        revision: int,
        now: datetime | None = None,
    ) -> bool:
        current = _timestamp(now or datetime.now(UTC))
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM file_analysis_confirmations AS confirmation
                INNER JOIN file_bindings AS binding
                  ON binding.tenant_id = confirmation.tenant_id
                 AND binding.asset_id = confirmation.asset_id
                 AND binding.scope_id = confirmation.scope_id
                 AND binding.purpose = 'chat'
                WHERE confirmation.tenant_id = ?
                  AND confirmation.asset_id = ?
                  AND confirmation.scope_id = ?
                  AND confirmation.mode = ?
                  AND confirmation.target_id = ?
                  AND confirmation.config_digest = ?
                  AND confirmation.prompt_sha256 = ?
                  AND confirmation.paid_acknowledged = ?
                  AND confirmation.revision = ?
                  AND confirmation.expires_at > ?
                """,
                (
                    _identifier(tenant_id, "tenant_id"),
                    _identifier(asset_id, "asset_id"),
                    _identifier(scope_id, "scope_id"),
                    _analysis_mode(mode),
                    _identifier(target_id, "target_id"),
                    _sha256(config_digest),
                    _sha256(prompt_sha256),
                    int(bool(paid_acknowledged)),
                    _positive_int(revision, "confirmation_revision"),
                    current,
                ),
            ).fetchone()
        return row is not None

    def create_analysis_job(
        self,
        tenant_id: str,
        asset_id: str,
        *,
        scope_id: str,
        mode: str,
        target_id: str,
        config_digest: str,
        prompt_sha256: str,
        paid_acknowledged: bool,
        confirmation_revision: int,
        selected_pages: tuple[int, ...],
        analysis_id: str | None = None,
        now: datetime | None = None,
    ) -> FileAnalysisJobRecord | None:
        """Create one queued job only while the exact confirmation is live."""

        clean_tenant = _identifier(tenant_id, "tenant_id")
        clean_asset = _identifier(asset_id, "asset_id")
        clean_scope = _identifier(scope_id, "scope_id")
        clean_mode = _analysis_mode(mode)
        clean_target = _identifier(target_id, "target_id")
        clean_config_digest = _sha256(config_digest)
        clean_prompt_sha256 = _sha256(prompt_sha256)
        clean_revision = _positive_int(
            confirmation_revision, "confirmation_revision"
        )
        clean_pages = _selected_pages(selected_pages)
        identifier = _identifier(
            analysis_id or f"analysis_{uuid.uuid4().hex}", "analysis_id"
        )
        created_at = _timestamp(now or datetime.now(UTC))
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            confirmation = connection.execute(
                """
                SELECT 1 FROM file_analysis_confirmations AS confirmation
                INNER JOIN file_bindings AS binding
                  ON binding.tenant_id = confirmation.tenant_id
                 AND binding.asset_id = confirmation.asset_id
                 AND binding.scope_id = confirmation.scope_id
                 AND binding.purpose = 'chat'
                WHERE confirmation.tenant_id = ?
                  AND confirmation.asset_id = ?
                  AND confirmation.scope_id = ?
                  AND confirmation.mode = ?
                  AND confirmation.target_id = ?
                  AND confirmation.config_digest = ?
                  AND confirmation.prompt_sha256 = ?
                  AND confirmation.paid_acknowledged = ?
                  AND confirmation.revision = ?
                  AND confirmation.expires_at > ?
                """,
                (
                    clean_tenant,
                    clean_asset,
                    clean_scope,
                    clean_mode,
                    clean_target,
                    clean_config_digest,
                    clean_prompt_sha256,
                    int(bool(paid_acknowledged)),
                    clean_revision,
                    created_at,
                ),
            ).fetchone()
            if confirmation is None:
                return None
            existing = connection.execute(
                """
                SELECT * FROM file_analysis_jobs
                WHERE tenant_id = ? AND asset_id = ? AND scope_id = ?
                  AND confirmation_revision = ?
                LIMIT 1
                """,
                (clean_tenant, clean_asset, clean_scope, clean_revision),
            ).fetchone()
            if existing is not None:
                return _analysis_job_record(existing)
            active = connection.execute(
                """
                SELECT 1 FROM file_analysis_jobs
                WHERE tenant_id = ? AND asset_id = ? AND scope_id = ?
                  AND status IN ('queued','running','cancel_requested')
                LIMIT 1
                """,
                (clean_tenant, clean_asset, clean_scope),
            ).fetchone()
            if active is not None:
                raise FileAssetRepositoryError("analysis_job_already_active")
            connection.execute(
                """
                INSERT INTO file_analysis_jobs (
                    id, tenant_id, asset_id, scope_id, mode, target_id,
                    config_digest, prompt_sha256, confirmation_revision, selected_pages,
                    page_count, processed_pages, status, cancel_requested,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'queued', 0, ?, ?)
                """,
                (
                    identifier,
                    clean_tenant,
                    clean_asset,
                    clean_scope,
                    clean_mode,
                    clean_target,
                    clean_config_digest,
                    clean_prompt_sha256,
                    clean_revision,
                    _encode_pages(clean_pages),
                    len(clean_pages),
                    created_at,
                    created_at,
                ),
            )
            row = connection.execute(
                "SELECT * FROM file_analysis_jobs WHERE tenant_id = ? AND id = ?",
                (clean_tenant, identifier),
            ).fetchone()
        return _analysis_job_record(row) if row is not None else None

    def get_analysis_job(
        self, tenant_id: str, analysis_id: str, *, scope_id: str | None = None
    ) -> FileAnalysisJobRecord | None:
        parameters: list[object] = [
            _identifier(tenant_id, "tenant_id"),
            _identifier(analysis_id, "analysis_id"),
        ]
        scope_clause = ""
        if scope_id is not None:
            scope_clause = " AND scope_id = ?"
            parameters.append(_identifier(scope_id, "scope_id"))
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM file_analysis_jobs "
                "WHERE tenant_id = ? AND id = ?" + scope_clause,
                parameters,
            ).fetchone()
        return _analysis_job_record(row) if row is not None else None

    def list_analysis_jobs(
        self, tenant_id: str, *, scope_id: str
    ) -> tuple[FileAnalysisJobRecord, ...]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM file_analysis_jobs
                WHERE tenant_id = ? AND scope_id = ?
                ORDER BY created_at DESC, id DESC
                """,
                (
                    _identifier(tenant_id, "tenant_id"),
                    _identifier(scope_id, "scope_id"),
                ),
            ).fetchall()
        return tuple(_analysis_job_record(row) for row in rows)

    def analysis_job_for_artifact(
        self,
        tenant_id: str,
        asset_id: str,
        *,
        scope_id: str,
        artifact_id: str,
    ) -> FileAnalysisJobRecord | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM file_analysis_jobs
                WHERE tenant_id = ? AND asset_id = ? AND scope_id = ?
                  AND result_artifact_id = ? AND status = 'completed'
                ORDER BY completed_at DESC, id DESC
                LIMIT 1
                """,
                (
                    _identifier(tenant_id, "tenant_id"),
                    _identifier(asset_id, "asset_id"),
                    _identifier(scope_id, "scope_id"),
                    _identifier(artifact_id, "artifact_id"),
                ),
            ).fetchone()
        return _analysis_job_record(row) if row is not None else None

    def claim_analysis_job(
        self, tenant_id: str, analysis_id: str
    ) -> FileAnalysisJobRecord | None:
        return self._transition_analysis_job(
            tenant_id,
            analysis_id,
            from_statuses=("queued",),
            status="running",
        )

    def update_analysis_progress(
        self,
        tenant_id: str,
        analysis_id: str,
        *,
        processed_pages: int,
    ) -> FileAnalysisJobRecord | None:
        clean_processed = _non_negative_int(processed_pages, "processed_pages")
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE file_analysis_jobs
                SET processed_pages = ?, updated_at = ?
                WHERE tenant_id = ? AND id = ?
                  AND status IN ('running','cancel_requested')
                  AND ? <= page_count
                """,
                (
                    clean_processed,
                    utc_now(),
                    _identifier(tenant_id, "tenant_id"),
                    _identifier(analysis_id, "analysis_id"),
                    clean_processed,
                ),
            )
        if cursor.rowcount != 1:
            return None
        return self.get_analysis_job(tenant_id, analysis_id)

    def complete_analysis_job(
        self,
        tenant_id: str,
        analysis_id: str,
        *,
        result_artifact_id: str,
        actual_cost_usd: str | None = None,
    ) -> FileAnalysisJobRecord | None:
        return self._transition_analysis_job(
            tenant_id,
            analysis_id,
            from_statuses=("running",),
            status="completed",
            result_artifact_id=_identifier(result_artifact_id, "result_artifact_id"),
            actual_cost_usd=_optional_cost(actual_cost_usd),
            completed=True,
        )

    def fail_analysis_job(
        self,
        tenant_id: str,
        analysis_id: str,
        *,
        error_code: str,
    ) -> FileAnalysisJobRecord | None:
        return self._transition_analysis_job(
            tenant_id,
            analysis_id,
            from_statuses=("queued", "running", "cancel_requested"),
            status="failed",
            error_code=_optional_code(error_code),
            completed=True,
        )

    def request_analysis_cancel(
        self, tenant_id: str, analysis_id: str
    ) -> FileAnalysisJobRecord | None:
        clean_tenant = _identifier(tenant_id, "tenant_id")
        clean_analysis = _identifier(analysis_id, "analysis_id")
        now = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE file_analysis_jobs
                SET status = CASE
                        WHEN status = 'queued' THEN 'cancelled'
                        ELSE 'cancel_requested'
                    END,
                    cancel_requested = 1,
                    completed_at = CASE WHEN status = 'queued' THEN ? ELSE completed_at END,
                    updated_at = ?
                WHERE tenant_id = ? AND id = ?
                  AND status IN ('queued','running')
                """,
                (now, now, clean_tenant, clean_analysis),
            )
            row = connection.execute(
                "SELECT * FROM file_analysis_jobs WHERE tenant_id = ? AND id = ?",
                (clean_tenant, clean_analysis),
            ).fetchone()
        return _analysis_job_record(row) if row is not None else None

    def acknowledge_analysis_cancel(
        self, tenant_id: str, analysis_id: str
    ) -> FileAnalysisJobRecord | None:
        return self._transition_analysis_job(
            tenant_id,
            analysis_id,
            from_statuses=("cancel_requested",),
            status="cancelled",
            completed=True,
        )

    def interrupt_stale_analysis_jobs(
        self,
        *,
        stale_before: datetime,
    ) -> int:
        """Mark abandoned billable work interrupted without replaying it."""

        now = utc_now()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE file_analysis_jobs
                SET status = 'interrupted', error_code = 'analysis_interrupted',
                    updated_at = ?, completed_at = ?
                WHERE status IN ('queued','running','cancel_requested')
                  AND updated_at <= ?
                """,
                (now, now, _timestamp(stale_before)),
            )
        return int(cursor.rowcount)

    def interrupt_analysis_job(
        self,
        tenant_id: str,
        analysis_id: str,
    ) -> FileAnalysisJobRecord | None:
        return self._transition_analysis_job(
            tenant_id,
            analysis_id,
            from_statuses=("queued", "running"),
            status="interrupted",
            error_code="analysis_interrupted",
            completed=True,
        )

    def _transition_analysis_job(
        self,
        tenant_id: str,
        analysis_id: str,
        *,
        from_statuses: tuple[str, ...],
        status: str,
        result_artifact_id: str | None = None,
        actual_cost_usd: str | None = None,
        error_code: str | None = None,
        completed: bool = False,
    ) -> FileAnalysisJobRecord | None:
        clean_tenant = _identifier(tenant_id, "tenant_id")
        clean_analysis = _identifier(analysis_id, "analysis_id")
        clean_status = _analysis_status(status)
        clean_from = tuple(_analysis_status(item) for item in from_statuses)
        placeholders = ",".join("?" for _ in clean_from)
        now = utc_now()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE file_analysis_jobs
                SET status = ?, result_artifact_id = ?, actual_cost_usd = ?,
                    error_code = ?, updated_at = ?, completed_at = ?
                WHERE tenant_id = ? AND id = ?
                  AND status IN ({placeholders})
                """,
                (
                    clean_status,
                    result_artifact_id,
                    actual_cost_usd,
                    error_code,
                    now,
                    now if completed else None,
                    clean_tenant,
                    clean_analysis,
                    *clean_from,
                ),
            )
        if cursor.rowcount != 1:
            return None
        return self.get_analysis_job(clean_tenant, clean_analysis)

    def get_bound_asset(
        self,
        tenant_id: str,
        asset_id: str,
        *,
        purpose: FilePurpose | str,
        scope_id: str,
    ) -> FileAssetRecord | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT asset.* FROM file_assets AS asset
                INNER JOIN file_bindings AS binding
                  ON binding.tenant_id = asset.tenant_id
                 AND binding.asset_id = asset.id
                WHERE asset.tenant_id = ? AND asset.id = ?
                  AND binding.purpose = ? AND binding.scope_id = ?
                  AND asset.status NOT IN ('expired','deleting','deleted')
                """,
                (
                    _identifier(tenant_id, "tenant_id"),
                    _identifier(asset_id, "asset_id"),
                    FilePurpose(purpose).value,
                    _identifier(scope_id, "scope_id"),
                ),
            ).fetchone()
        return _asset_record(row) if row is not None else None

    def list_bound_assets(
        self,
        tenant_id: str,
        *,
        purpose: FilePurpose | str,
        scope_id: str,
    ) -> tuple[FileAssetRecord, ...]:
        """List only assets bound to the exact tenant, purpose, and scope."""

        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT asset.* FROM file_assets AS asset
                INNER JOIN file_bindings AS binding
                  ON binding.tenant_id = asset.tenant_id
                 AND binding.asset_id = asset.id
                WHERE asset.tenant_id = ?
                  AND binding.purpose = ? AND binding.scope_id = ?
                  AND asset.status NOT IN ('expired','deleting','deleted')
                ORDER BY asset.created_at DESC, asset.id DESC
                """,
                (
                    _identifier(tenant_id, "tenant_id"),
                    FilePurpose(purpose).value,
                    _identifier(scope_id, "scope_id"),
                ),
            ).fetchall()
        return tuple(_asset_record(row) for row in rows)

    def add_binding(
        self,
        tenant_id: str,
        asset_id: str,
        *,
        purpose: FilePurpose | str,
        scope_id: str,
    ) -> bool:
        clean_tenant = _identifier(tenant_id, "tenant_id")
        clean_asset = _identifier(asset_id, "asset_id")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            blocked = connection.execute(
                """
                SELECT 1 FROM file_scope_tombstones
                WHERE tenant_id = ? AND purpose = ? AND scope_id = ?
                """,
                (clean_tenant, FilePurpose(purpose).value, _identifier(scope_id, "scope_id")),
            ).fetchone()
            if blocked is not None:
                raise FileAssetRepositoryError("file_scope_blocked")
            self._require_mutable_asset(connection, clean_tenant, clean_asset)
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO file_bindings (
                    id, tenant_id, asset_id, purpose, scope_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    f"bind_{uuid.uuid4().hex}",
                    clean_tenant,
                    clean_asset,
                    FilePurpose(purpose).value,
                    _identifier(scope_id, "scope_id"),
                    utc_now(),
                ),
            )
            created = cursor.rowcount == 1
            if created:
                connection.execute(
                    """
                    UPDATE file_assets
                    SET reference_count = reference_count + 1, updated_at = ?
                    WHERE tenant_id = ? AND id = ?
                    """,
                    (utc_now(), clean_tenant, clean_asset),
                )
        return created

    def remove_binding(
        self,
        tenant_id: str,
        asset_id: str,
        *,
        purpose: FilePurpose | str,
        scope_id: str,
        expire_if_unreferenced: bool = False,
    ) -> bool:
        clean_tenant = _identifier(tenant_id, "tenant_id")
        clean_asset = _identifier(asset_id, "asset_id")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                DELETE FROM file_bindings
                WHERE tenant_id = ? AND asset_id = ? AND purpose = ? AND scope_id = ?
                """,
                (
                    clean_tenant,
                    clean_asset,
                    FilePurpose(purpose).value,
                    _identifier(scope_id, "scope_id"),
                ),
            )
            removed = cursor.rowcount == 1
            if removed:
                now = utc_now()
                connection.execute(
                    """
                    UPDATE file_assets SET
                        reference_count = MAX(reference_count - 1, 0),
                        status = CASE
                            WHEN ? AND reference_count <= 1 THEN 'expired'
                            ELSE status
                        END,
                        expires_at = CASE
                            WHEN ? AND reference_count <= 1 THEN ?
                            ELSE expires_at
                        END,
                        updated_at = ?
                    WHERE tenant_id = ? AND id = ?
                    """,
                    (
                        int(expire_if_unreferenced),
                        int(expire_if_unreferenced),
                        now,
                        now,
                        clean_tenant,
                        clean_asset,
                    ),
                )
        return removed

    def remove_scope_bindings(
        self,
        tenant_id: str,
        *,
        purpose: FilePurpose | str,
        scope_id: str,
        expire_if_unreferenced: bool = False,
    ) -> tuple[str, ...]:
        """Atomically detach one tenant scope and return affected asset IDs."""

        clean_tenant = _identifier(tenant_id, "tenant_id")
        clean_purpose = FilePurpose(purpose).value
        clean_scope = _identifier(scope_id, "scope_id")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT asset_id, COUNT(*) AS binding_count
                FROM file_bindings
                WHERE tenant_id = ? AND purpose = ? AND scope_id = ?
                GROUP BY asset_id
                ORDER BY asset_id
                """,
                (clean_tenant, clean_purpose, clean_scope),
            ).fetchall()
            if not rows:
                return ()
            connection.execute(
                """
                DELETE FROM file_bindings
                WHERE tenant_id = ? AND purpose = ? AND scope_id = ?
                """,
                (clean_tenant, clean_purpose, clean_scope),
            )
            now = utc_now()
            for row in rows:
                asset_id = str(row["asset_id"])
                binding_count = max(1, int(row["binding_count"]))
                connection.execute(
                    """
                    UPDATE file_assets SET
                        reference_count = MAX(reference_count - ?, 0),
                        status = CASE
                            WHEN ? AND reference_count <= ? THEN 'expired'
                            ELSE status
                        END,
                        expires_at = CASE
                            WHEN ? AND reference_count <= ? THEN ?
                            ELSE expires_at
                        END,
                        updated_at = ?
                    WHERE tenant_id = ? AND id = ?
                    """,
                    (
                        binding_count,
                        int(expire_if_unreferenced),
                        binding_count,
                        int(expire_if_unreferenced),
                        binding_count,
                        now,
                        now,
                        clean_tenant,
                        asset_id,
                    ),
                )
        return tuple(str(row["asset_id"]) for row in rows)

    def block_scope_and_remove_bindings(
        self,
        tenant_id: str,
        *,
        purpose: FilePurpose | str,
        scope_id: str,
        expire_if_unreferenced: bool = False,
    ) -> tuple[str, ...]:
        """Durably block new bindings and atomically detach an exact scope.

        A cleanup ledger is retained after bindings disappear so a restarted
        caller cannot mistake an empty scope for completed physical GC.
        """

        clean_tenant = _identifier(tenant_id, "tenant_id")
        clean_purpose = FilePurpose(purpose).value
        clean_scope = _identifier(scope_id, "scope_id")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            now = utc_now()
            connection.execute(
                """
                INSERT OR IGNORE INTO file_scope_tombstones (
                    tenant_id, purpose, scope_id, blocked_at
                ) VALUES (?, ?, ?, ?)
                """,
                (clean_tenant, clean_purpose, clean_scope, now),
            )
            rows = connection.execute(
                """
                SELECT asset_id, COUNT(*) AS binding_count
                FROM file_bindings
                WHERE tenant_id = ? AND purpose = ? AND scope_id = ?
                GROUP BY asset_id
                ORDER BY asset_id
                """,
                (clean_tenant, clean_purpose, clean_scope),
            ).fetchall()
            for row in rows:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO file_scope_cleanup_assets (
                        tenant_id, purpose, scope_id, asset_id, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        clean_tenant,
                        clean_purpose,
                        clean_scope,
                        str(row["asset_id"]),
                        now,
                    ),
                )
            if rows:
                connection.execute(
                    """
                    DELETE FROM file_bindings
                    WHERE tenant_id = ? AND purpose = ? AND scope_id = ?
                    """,
                    (clean_tenant, clean_purpose, clean_scope),
                )
                for row in rows:
                    asset_id = str(row["asset_id"])
                    binding_count = max(1, int(row["binding_count"]))
                    connection.execute(
                        """
                        UPDATE file_assets SET
                            reference_count = MAX(reference_count - ?, 0),
                            status = CASE
                                WHEN ? AND reference_count <= ? THEN 'expired'
                                ELSE status
                            END,
                            expires_at = CASE
                                WHEN ? AND reference_count <= ? THEN ?
                                ELSE expires_at
                            END,
                            updated_at = ?
                        WHERE tenant_id = ? AND id = ?
                        """,
                        (
                            binding_count,
                            int(expire_if_unreferenced),
                            binding_count,
                            int(expire_if_unreferenced),
                            binding_count,
                            now,
                            now,
                            clean_tenant,
                            asset_id,
                        ),
                    )
            tracked = connection.execute(
                """
                SELECT asset_id FROM file_scope_cleanup_assets
                WHERE tenant_id = ? AND purpose = ? AND scope_id = ?
                ORDER BY asset_id
                """,
                (clean_tenant, clean_purpose, clean_scope),
            ).fetchall()
        return tuple(str(row["asset_id"]) for row in tracked)

    def scope_is_blocked(
        self,
        tenant_id: str,
        *,
        purpose: FilePurpose | str,
        scope_id: str,
    ) -> bool:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM file_scope_tombstones
                WHERE tenant_id = ? AND purpose = ? AND scope_id = ?
                """,
                (
                    _identifier(tenant_id, "tenant_id"),
                    FilePurpose(purpose).value,
                    _identifier(scope_id, "scope_id"),
                ),
            ).fetchone()
        return row is not None

    def scope_cleanup_asset_ids(
        self,
        tenant_id: str,
        *,
        purpose: FilePurpose | str,
        scope_id: str,
    ) -> tuple[str, ...]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT asset_id FROM file_scope_cleanup_assets
                WHERE tenant_id = ? AND purpose = ? AND scope_id = ?
                ORDER BY asset_id
                """,
                (
                    _identifier(tenant_id, "tenant_id"),
                    FilePurpose(purpose).value,
                    _identifier(scope_id, "scope_id"),
                ),
            ).fetchall()
        return tuple(str(row["asset_id"]) for row in rows)

    def scope_cleanup_pending(
        self,
        tenant_id: str,
        *,
        purpose: FilePurpose | str,
        scope_id: str,
    ) -> bool:
        """Report unreferenced metadata still awaiting physical scope cleanup."""

        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM file_assets
                WHERE tenant_id = ? AND purpose = ? AND scope_id = ?
                  AND reference_count = 0
                LIMIT 1
                """,
                (
                    _identifier(tenant_id, "tenant_id"),
                    FilePurpose(purpose).value,
                    _identifier(scope_id, "scope_id"),
                ),
            ).fetchone()
        return row is not None

    def create_artifact(
        self,
        tenant_id: str,
        asset_id: str,
        *,
        kind: str,
        storage_key: str,
        sha256: str,
        byte_size: int,
        status: FileAssetStatus = "ready",
        expires_at: datetime | str | None = None,
        artifact_id: str | None = None,
    ) -> FileArtifactRecord:
        clean_tenant = _identifier(tenant_id, "tenant_id")
        identifier = _identifier(
            artifact_id or f"artifact_{uuid.uuid4().hex}", "artifact_id"
        )
        now = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_mutable_asset(
                connection,
                clean_tenant,
                _identifier(asset_id, "asset_id"),
            )
            connection.execute(
                """
                INSERT INTO file_artifacts (
                    id, tenant_id, asset_id, kind, storage_key, sha256,
                    byte_size, status, expires_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    identifier,
                    clean_tenant,
                    _identifier(asset_id, "asset_id"),
                    _identifier(kind, "artifact_kind"),
                    _storage_key(storage_key, namespace="artifacts"),
                    _sha256(sha256),
                    _non_negative_int(byte_size, "byte_size"),
                    _status(status),
                    _timestamp(expires_at),
                    now,
                    now,
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM file_artifacts
                WHERE tenant_id = ? AND id = ?
                """,
                (clean_tenant, identifier),
            ).fetchone()
        if row is None:  # pragma: no cover
            raise FileAssetRepositoryError("artifact_create_failed")
        return _artifact_record(row)

    def list_artifacts(
        self, tenant_id: str, asset_id: str
    ) -> tuple[FileArtifactRecord, ...]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM file_artifacts
                WHERE tenant_id = ? AND asset_id = ?
                ORDER BY created_at, id
                """,
                (_identifier(tenant_id, "tenant_id"), _identifier(asset_id, "asset_id")),
            ).fetchall()
        return tuple(_artifact_record(row) for row in rows)

    def get_artifact(
        self,
        tenant_id: str,
        asset_id: str,
        artifact_id: str,
    ) -> FileArtifactRecord | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM file_artifacts
                WHERE tenant_id = ? AND asset_id = ? AND id = ?
                """,
                (
                    _identifier(tenant_id, "tenant_id"),
                    _identifier(asset_id, "asset_id"),
                    _identifier(artifact_id, "artifact_id"),
                ),
            ).fetchone()
        return _artifact_record(row) if row is not None else None

    def latest_artifact(
        self, tenant_id: str, asset_id: str, *, kind: str
    ) -> FileArtifactRecord | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM file_artifacts
                WHERE tenant_id = ? AND asset_id = ? AND kind = ?
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (
                    _identifier(tenant_id, "tenant_id"),
                    _identifier(asset_id, "asset_id"),
                    _identifier(kind, "artifact_kind"),
                ),
            ).fetchone()
        return _artifact_record(row) if row is not None else None

    def touch_artifact(
        self,
        tenant_id: str,
        asset_id: str,
        artifact_id: str,
        *,
        idle_seconds: int,
        hard_seconds: int,
        now: datetime | None = None,
    ) -> FileArtifactRecord | None:
        """Extend a ready artifact's idle TTL without crossing its hard TTL."""

        current = now or datetime.now(UTC)
        current_text = _timestamp(current)
        clean_tenant = _identifier(tenant_id, "tenant_id")
        clean_asset = _identifier(asset_id, "asset_id")
        clean_artifact = _identifier(artifact_id, "artifact_id")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM file_artifacts
                WHERE tenant_id = ? AND asset_id = ? AND id = ?
                """,
                (clean_tenant, clean_asset, clean_artifact),
            ).fetchone()
            if row is None or row["status"] != "ready":
                return None
            created = datetime.fromisoformat(
                str(row["created_at"]).replace("Z", "+00:00")
            )
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            hard_expiry = created.astimezone(UTC) + timedelta(
                seconds=max(1, int(hard_seconds))
            )
            expires_at = row["expires_at"]
            expired = hard_expiry <= current
            if expires_at is not None:
                parsed_expiry = datetime.fromisoformat(
                    str(expires_at).replace("Z", "+00:00")
                )
                if parsed_expiry.tzinfo is None:
                    parsed_expiry = parsed_expiry.replace(tzinfo=UTC)
                expired = expired or parsed_expiry.astimezone(UTC) <= current
            if expired:
                connection.execute(
                    """
                    UPDATE file_artifacts
                    SET status = 'expired', updated_at = ?
                    WHERE tenant_id = ? AND asset_id = ? AND id = ?
                    """,
                    (current_text, clean_tenant, clean_asset, clean_artifact),
                )
                return None
            refreshed_expiry = min(
                current + timedelta(seconds=max(1, int(idle_seconds))),
                hard_expiry,
            )
            connection.execute(
                """
                UPDATE file_artifacts
                SET expires_at = ?, updated_at = ?
                WHERE tenant_id = ? AND asset_id = ? AND id = ?
                  AND status = 'ready'
                """,
                (
                    _timestamp(refreshed_expiry),
                    current_text,
                    clean_tenant,
                    clean_asset,
                    clean_artifact,
                ),
            )
            refreshed = connection.execute(
                """
                SELECT * FROM file_artifacts
                WHERE tenant_id = ? AND asset_id = ? AND id = ?
                """,
                (clean_tenant, clean_asset, clean_artifact),
            ).fetchone()
        return _artifact_record(refreshed) if refreshed is not None else None

    def expire_due_artifacts(
        self, *, now: datetime | None = None
    ) -> tuple[FileArtifactRecord, ...]:
        current_text = _timestamp(now or datetime.now(UTC))
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE file_artifacts
                SET status = 'expired', updated_at = ?
                WHERE status = 'ready' AND expires_at IS NOT NULL
                  AND expires_at <= ?
                """,
                (current_text, current_text),
            )
            rows = connection.execute(
                """
                SELECT * FROM file_artifacts
                WHERE status = 'expired'
                ORDER BY expires_at, id
                LIMIT 500
                """
            ).fetchall()
        return tuple(_artifact_record(row) for row in rows)

    def list_expired_referenced_assets(
        self, *, purpose: FilePurpose | str
    ) -> tuple[FileAssetRecord, ...]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM file_assets
                WHERE purpose = ? AND status = 'expired' AND reference_count > 0
                ORDER BY expires_at, id
                """,
                (FilePurpose(purpose).value,),
            ).fetchall()
        return tuple(_asset_record(row) for row in rows)

    def record_audit_event(
        self,
        tenant_id: str,
        *,
        asset_id: str | None,
        event_type: str,
        sha256: str | None = None,
        format_id: str | None = None,
        byte_size: int | None = None,
        status: str | None = None,
        error_code: str | None = None,
    ) -> str:
        event_id = f"audit_{uuid.uuid4().hex}"
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO file_audit_events (
                    id, tenant_id, asset_id, event_type, sha256, format_id,
                    byte_size, status, error_code, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    _identifier(tenant_id, "tenant_id"),
                    _identifier(asset_id, "asset_id") if asset_id else None,
                    _identifier(event_type, "event_type"),
                    _sha256(sha256) if sha256 else None,
                    _identifier(format_id, "format_id") if format_id else None,
                    _non_negative_int(byte_size, "byte_size")
                    if byte_size is not None
                    else None,
                    _status(status) if status else None,
                    _optional_code(error_code),
                    utc_now(),
                ),
            )
        return event_id

    def referenced_storage_keys(self) -> frozenset[str]:
        """Return opaque keys known to SQLite for startup orphan reconciliation."""

        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT storage_key FROM file_assets
                UNION
                SELECT storage_key FROM file_artifacts
                """
            ).fetchall()
        return frozenset(row["storage_key"] for row in rows)

    def list_ready_assets(self) -> tuple[FileAssetRecord, ...]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM file_assets
                WHERE status = 'ready'
                ORDER BY tenant_id, created_at, id
                """
            ).fetchall()
        return tuple(_asset_record(row) for row in rows)

    def mark_original_missing(
        self,
        tenant_id: str,
        asset_id: str,
        *,
        detected_at: datetime | None = None,
    ) -> FileAssetRecord | None:
        """CAS a ready row to failed/expired after its private blob disappears."""

        clean_tenant = _identifier(tenant_id, "tenant_id")
        clean_asset = _identifier(asset_id, "asset_id")
        now = _timestamp(detected_at or datetime.now(UTC))
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE file_assets SET
                    status = CASE
                        WHEN reference_count = 0 THEN 'expired'
                        ELSE 'failed'
                    END,
                    expires_at = CASE
                        WHEN reference_count = 0 THEN ?
                        ELSE expires_at
                    END,
                    last_error_code = 'original_blob_missing',
                    updated_at = ?
                WHERE tenant_id = ? AND id = ? AND status = 'ready'
                """,
                (now, now, clean_tenant, clean_asset),
            )
            row = connection.execute(
                "SELECT * FROM file_assets WHERE tenant_id = ? AND id = ?",
                (clean_tenant, clean_asset),
            ).fetchone()
        if cursor.rowcount != 1 or row is None:
            return None
        return _asset_record(row)

    def claim_garbage_collection(
        self,
        *,
        now: datetime | None = None,
        limit: int = 100,
        stale_after_seconds: int = 300,
    ) -> tuple[GarbageCollectionClaim, ...]:
        current = now or datetime.now(UTC)
        current_text = _timestamp(current)
        stale_text = _timestamp(current - timedelta(seconds=max(1, stale_after_seconds)))
        safe_limit = max(1, min(int(limit), 500))
        claim_id = f"gc_{uuid.uuid4().hex}"
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE file_assets
                SET status = 'expired', gc_claim_id = NULL, gc_claimed_at = NULL,
                    updated_at = ?
                WHERE expires_at IS NOT NULL AND expires_at <= ?
                  AND status IN ('validating','processing','ready','failed')
                """,
                (current_text, current_text),
            )
            rows = connection.execute(
                """
                SELECT * FROM file_assets
                WHERE reference_count = 0 AND (
                    status = 'expired'
                    OR (status = 'deleting' AND gc_claimed_at <= ?)
                )
                ORDER BY expires_at, created_at, id
                LIMIT ?
                """,
                (stale_text, safe_limit),
            ).fetchall()
            identities = [(row["tenant_id"], row["id"]) for row in rows]
            for tenant_id, asset_id in identities:
                connection.execute(
                    """
                    UPDATE file_assets
                    SET status = 'deleting', gc_claim_id = ?, gc_claimed_at = ?,
                        updated_at = ?
                    WHERE tenant_id = ? AND id = ? AND reference_count = 0
                    """,
                    (claim_id, current_text, current_text, tenant_id, asset_id),
                )
            claimed_rows = connection.execute(
                "SELECT * FROM file_assets WHERE gc_claim_id = ?",
                (claim_id,),
            ).fetchall()
            claims: list[GarbageCollectionClaim] = []
            for row in claimed_rows:
                artifact_rows = connection.execute(
                    """
                    SELECT storage_key FROM file_artifacts
                    WHERE tenant_id = ? AND asset_id = ?
                    ORDER BY id
                    """,
                    (row["tenant_id"], row["id"]),
                ).fetchall()
                keys = (row["storage_key"],) + tuple(
                    item["storage_key"] for item in artifact_rows
                )
                claims.append(
                    GarbageCollectionClaim(
                        claim_id=claim_id,
                        asset=_asset_record(row),
                        storage_keys=keys,
                    )
                )
        return tuple(claims)

    def complete_garbage_collection(self, claim: GarbageCollectionClaim) -> bool:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                DELETE FROM file_assets
                WHERE tenant_id = ? AND id = ? AND reference_count = 0
                  AND status = 'deleting' AND gc_claim_id = ?
                """,
                (claim.asset.tenant_id, claim.asset.id, claim.claim_id),
            )
        return cursor.rowcount == 1

    def release_garbage_collection(
        self, claim: GarbageCollectionClaim, *, error_code: str
    ) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE file_assets
                SET status = 'expired', gc_claim_id = NULL, gc_claimed_at = NULL,
                    last_error_code = ?, updated_at = ?
                WHERE tenant_id = ? AND id = ? AND gc_claim_id = ?
                """,
                (
                    _optional_code(error_code),
                    utc_now(),
                    claim.asset.tenant_id,
                    claim.asset.id,
                    claim.claim_id,
                ),
            )
        return cursor.rowcount == 1

    def count_schema_tenant_columns(self) -> dict[str, bool]:
        tables = (
            "file_assets",
            "file_bindings",
            "file_artifacts",
            "file_audit_events",
            "file_analysis_confirmations",
            "file_analysis_jobs",
            "file_analysis_send_confirmations",
        )
        with self._lock, self._connect() as connection:
            return {
                table: any(
                    row["name"] == "tenant_id" and row["notnull"] == 1
                    for row in connection.execute(
                        f"PRAGMA table_info({table})"
                    ).fetchall()
                )
                for table in tables
            }

    @staticmethod
    def _require_mutable_asset(
        connection: sqlite3.Connection, tenant_id: str, asset_id: str
    ) -> None:
        row = connection.execute(
            """
            SELECT status FROM file_assets
            WHERE tenant_id = ? AND id = ?
            """,
            (tenant_id, asset_id),
        ).fetchone()
        if row is None:
            raise FileAssetRepositoryError("asset_not_found")
        if row["status"] in {"expired", "deleting", "deleted"}:
            raise FileAssetRepositoryError("asset_not_mutable")


def _asset_record(row: sqlite3.Row) -> FileAssetRecord:
    return FileAssetRecord(
        id=row["id"],
        tenant_id=row["tenant_id"],
        purpose=row["purpose"],
        scope_id=row["scope_id"],
        display_name=row["display_name"],
        format_id=row["format_id"],
        media_type=row["media_type"],
        storage_key=row["storage_key"],
        sha256=row["sha256"],
        byte_size=row["byte_size"],
        status=row["status"],
        reference_count=row["reference_count"],
        expires_at=row["expires_at"],
        last_error_code=row["last_error_code"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _artifact_record(row: sqlite3.Row) -> FileArtifactRecord:
    return FileArtifactRecord(
        id=row["id"],
        tenant_id=row["tenant_id"],
        asset_id=row["asset_id"],
        kind=row["kind"],
        storage_key=row["storage_key"],
        sha256=row["sha256"],
        byte_size=row["byte_size"],
        status=row["status"],
        expires_at=row["expires_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _analysis_confirmation_record(
    row: sqlite3.Row,
) -> FileAnalysisConfirmationRecord:
    return FileAnalysisConfirmationRecord(
        tenant_id=row["tenant_id"],
        asset_id=row["asset_id"],
        scope_id=row["scope_id"],
        mode=row["mode"],
        target_id=row["target_id"],
        config_digest=row["config_digest"],
        prompt_sha256=row["prompt_sha256"],
        paid_acknowledged=bool(row["paid_acknowledged"]),
        revision=int(row["revision"]),
        expires_at=row["expires_at"],
        confirmed_at=row["confirmed_at"],
    )


def _analysis_job_record(row: sqlite3.Row) -> FileAnalysisJobRecord:
    return FileAnalysisJobRecord(
        id=row["id"],
        tenant_id=row["tenant_id"],
        asset_id=row["asset_id"],
        scope_id=row["scope_id"],
        mode=row["mode"],
        target_id=row["target_id"],
        config_digest=row["config_digest"],
        prompt_sha256=row["prompt_sha256"],
        confirmation_revision=int(row["confirmation_revision"]),
        selected_pages=row["selected_pages"],
        page_count=int(row["page_count"]),
        processed_pages=int(row["processed_pages"]),
        status=row["status"],
        cancel_requested=bool(row["cancel_requested"]),
        result_artifact_id=row["result_artifact_id"],
        actual_cost_usd=row["actual_cost_usd"],
        error_code=row["error_code"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
    )


def _analysis_send_confirmation_record(
    row: sqlite3.Row,
) -> FileAnalysisSendConfirmationRecord:
    return FileAnalysisSendConfirmationRecord(
        tenant_id=row["tenant_id"],
        asset_id=row["asset_id"],
        scope_id=row["scope_id"],
        artifact_id=row["artifact_id"],
        prompt_sha256=row["prompt_sha256"],
        revision=int(row["revision"]),
        confirmed_at=row["confirmed_at"],
    )


def _identifier(value: object, field: str) -> str:
    clean = str(value or "").strip()
    if not clean or len(clean) > 256 or any(ord(character) < 32 for character in clean):
        raise ValueError(f"{field} is invalid")
    return clean


def _display_name(value: object) -> str:
    clean = str(value or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not clean or len(clean) > 255 or any(ord(character) < 32 for character in clean):
        raise ValueError("display_name is invalid")
    return clean


def _media_type(value: object) -> str:
    clean = str(value or "").split(";", 1)[0].strip().lower()
    if not clean or "/" not in clean or len(clean) > 255:
        raise ValueError("media_type is invalid")
    return clean


def _storage_key(value: object, *, namespace: str) -> str:
    clean = str(value or "").strip()
    if (
        _STORAGE_KEY.fullmatch(clean) is None
        or not clean.startswith(f"{namespace}/")
    ):
        raise ValueError("storage_key is invalid")
    return clean


def _sha256(value: object) -> str:
    clean = str(value or "").strip().lower()
    if _SHA256.fullmatch(clean) is None:
        raise ValueError("sha256 is invalid")
    return clean


def _status(value: object) -> str:
    clean = str(value or "").strip().lower()
    if clean not in _ASSET_STATUSES:
        raise ValueError("status is invalid")
    return clean


def _non_negative_int(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} is invalid")
    clean = int(value)  # type: ignore[arg-type]
    if clean < 0:
        raise ValueError(f"{field} is invalid")
    return clean


def _positive_int(value: object, field: str) -> int:
    clean = _non_negative_int(value, field)
    if clean < 1:
        raise ValueError(f"{field} is invalid")
    return clean


def _file_handling(value: object) -> Literal["native", "extract"]:
    clean = str(value or "").strip().lower()
    if clean not in {"native", "extract"}:
        raise ValueError("file handling is invalid")
    return clean  # type: ignore[return-value]


def _analysis_mode(value: object) -> Literal["vision", "provider_ocr"]:
    clean = str(value or "").strip().lower()
    if clean not in {"vision", "provider_ocr"}:
        raise ValueError("analysis mode is invalid")
    return clean  # type: ignore[return-value]


def _analysis_status(value: object) -> str:
    clean = str(value or "").strip().lower()
    if clean not in {
        "queued",
        "running",
        "completed",
        "failed",
        "cancel_requested",
        "cancelled",
        "interrupted",
    }:
        raise ValueError("analysis status is invalid")
    return clean


def _selected_pages(value: object) -> tuple[int, ...]:
    if isinstance(value, (str, bytes)):
        raise ValueError("selected_pages is invalid")
    try:
        pages = tuple(_positive_int(item, "selected_page") for item in value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ValueError("selected_pages is invalid") from exc
    if not pages or len(pages) > 20 or len(pages) != len(set(pages)):
        raise ValueError("selected_pages is invalid")
    if pages != tuple(sorted(pages)):
        raise ValueError("selected_pages must be sorted")
    return pages


def _encode_pages(value: tuple[int, ...]) -> str:
    return ",".join(str(page) for page in value)


def _optional_cost(value: object | None) -> str | None:
    if value is None:
        return None
    clean = str(value).strip()
    if not clean or len(clean) > 64:
        raise ValueError("actual_cost_usd is invalid")
    try:
        numeric = float(clean)
    except ValueError as exc:
        raise ValueError("actual_cost_usd is invalid") from exc
    if numeric < 0 or numeric == float("inf") or numeric != numeric:
        raise ValueError("actual_cost_usd is invalid")
    return clean


def _timestamp(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        normalized = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return normalized.astimezone(UTC).isoformat()
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def _optional_code(value: object | None) -> str | None:
    if value is None:
        return None
    return _identifier(value, "error_code")
