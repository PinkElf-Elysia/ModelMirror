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

FILE_ASSET_SCHEMA_VERSION = 3

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
