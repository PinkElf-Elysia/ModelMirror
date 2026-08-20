from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .repository import (
    CANONICAL_MASTER_KEY_ENV,
    LEGACY_MASTER_KEY_ENV,
    MASTER_KEY_FINGERPRINT_METADATA_KEY,
    MASTER_KEY_VERSION_METADATA_KEY,
    SQLiteRouterRepository,
    utc_now,
)


@dataclass(frozen=True)
class CredentialMigrationResult:
    migrated_credentials: int
    backup_path: str
    status: str = "completed"


class CredentialMigrationError(RuntimeError):
    pass


def migrate_credentials(
    storage_dir: str | Path,
    *,
    source_key: str | bytes | None = None,
    target_key: str | bytes | None = None,
    fail_after: int | None = None,
) -> CredentialMigrationResult:
    directory = Path(storage_dir)
    database_path = directory / "router.sqlite3"
    key_path = directory / "credential-master.key"
    if not database_path.is_file():
        raise CredentialMigrationError(f"Router database not found: {database_path}")

    target_raw = target_key or os.getenv(CANONICAL_MASTER_KEY_ENV, "").strip()
    if not target_raw:
        raise CredentialMigrationError(
            f"{CANONICAL_MASTER_KEY_ENV} is required as the migration target."
        )
    source_raw = source_key or os.getenv(LEGACY_MASTER_KEY_ENV, "").strip()
    if not source_raw and key_path.is_file():
        source_raw = key_path.read_bytes().strip()
    if not source_raw:
        source_raw = target_raw

    source = Fernet(SQLiteRouterRepository._normalize_key(source_raw))
    target_bytes = SQLiteRouterRepository._normalize_key(target_raw)
    target = Fernet(target_bytes)

    connection = sqlite3.connect(database_path, timeout=15, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        preflight_data_version = int(
            connection.execute("PRAGMA data_version").fetchone()[0]
        )
        rows = connection.execute(
            "SELECT tenant_id, id, api_key_ciphertext FROM router_connections "
            "ORDER BY tenant_id, id"
        ).fetchall()
        plaintext_by_id: list[tuple[bytes, str, str]] = []
        try:
            for row in rows:
                plaintext_by_id.append(
                    (
                        source.decrypt(
                            str(row["api_key_ciphertext"]).encode("ascii")
                        ),
                        str(row["tenant_id"]),
                        str(row["id"]),
                    )
                )
        except (InvalidToken, ValueError) as exc:
            raise CredentialMigrationError(
                "Credential preflight failed with the old key; no database changes were made."
            ) from exc

        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
        backup_path = directory / f"router.sqlite3.backup-{timestamp}"
        backup = sqlite3.connect(backup_path)
        try:
            connection.backup(backup)
        finally:
            backup.close()

        try:
            connection.execute("BEGIN IMMEDIATE")
            locked_data_version = int(
                connection.execute("PRAGMA data_version").fetchone()[0]
            )
            if locked_data_version != preflight_data_version:
                raise CredentialMigrationError(
                    "Router database changed during credential migration; "
                    "no changes were committed. Stop provider writes and retry."
                )
            for index, (plaintext, tenant_id, connection_id) in enumerate(
                plaintext_by_id, start=1
            ):
                ciphertext = target.encrypt(plaintext).decode("ascii")
                connection.execute(
                    "UPDATE router_connections SET api_key_ciphertext = ? "
                    "WHERE tenant_id = ? AND id = ?",
                    (ciphertext, tenant_id, connection_id),
                )
                if fail_after is not None and index >= fail_after:
                    raise CredentialMigrationError("Injected migration interruption.")

            for plaintext, tenant_id, connection_id in plaintext_by_id:
                row = connection.execute(
                    "SELECT api_key_ciphertext FROM router_connections "
                    "WHERE tenant_id = ? AND id = ?",
                    (tenant_id, connection_id),
                ).fetchone()
                if row is None or target.decrypt(
                    str(row["api_key_ciphertext"]).encode("ascii")
                ) != plaintext:
                    raise CredentialMigrationError(
                        "Credential verification failed; migration was rolled back."
                    )

            connection.execute(
                "CREATE TABLE IF NOT EXISTS router_metadata ("
                "key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            now = utc_now()
            connection.executemany(
                "INSERT INTO router_metadata (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "updated_at=excluded.updated_at",
                (
                    (
                        MASTER_KEY_FINGERPRINT_METADATA_KEY,
                        hashlib.sha256(target_bytes).hexdigest(),
                        now,
                    ),
                    (MASTER_KEY_VERSION_METADATA_KEY, "1", now),
                ),
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
    finally:
        connection.close()

    return CredentialMigrationResult(
        migrated_credentials=len(rows),
        backup_path=str(backup_path),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Atomically migrate ModelMirror provider credentials."
    )
    parser.add_argument("--storage-dir", required=True)
    args = parser.parse_args()
    try:
        result = migrate_credentials(args.storage_dir)
    except CredentialMigrationError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        return 1
    print(json.dumps(asdict(result), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
