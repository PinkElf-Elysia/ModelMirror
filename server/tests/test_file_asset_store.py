from __future__ import annotations

import os
import sqlite3
from io import BytesIO
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from server.file_assets.blob_store import (
    BlobValidationError,
    FileBlobStore,
    InvalidStorageKey,
)
from server.file_assets.lifecycle import FileAssetLifecycle
from server.file_assets.repository import (
    FILE_ASSET_SCHEMA_VERSION,
    FileAssetRepositoryError,
    SQLiteFileAssetRepository,
)
from server.file_assets.service import FileAssetService, FileAssetServiceError


def _asset(
    repository: SQLiteFileAssetRepository,
    store: FileBlobStore,
    *,
    tenant_id: str = "local",
    scope_id: str = "chat-1",
    expires_at: datetime | None = None,
    create_initial_binding: bool = False,
):
    receipt = store.write_bytes(b"private file body")
    return repository.create_asset(
        tenant_id,
        purpose="chat",
        scope_id=scope_id,
        display_name="user-notes.txt",
        format_id="txt",
        media_type="text/plain; charset=utf-8",
        storage_key=receipt.storage_key,
        sha256=receipt.sha256,
        byte_size=receipt.byte_size,
        status="ready",
        expires_at=expires_at,
        create_initial_binding=create_initial_binding,
    )


def test_blob_store_is_atomic_path_confined_and_filename_free(tmp_path: Path) -> None:
    store = FileBlobStore(tmp_path)
    receipt = store.write_stream(
        (b"hello ", b"world"), max_bytes=11
    )

    assert receipt.byte_size == 11
    assert len(receipt.sha256) == 64
    assert "hello" not in receipt.storage_key
    assert "user-notes.txt" not in receipt.storage_key
    assert store.read_bytes(receipt.storage_key) == b"hello world"
    assert not list((tmp_path / ".staging").iterdir())

    for unsafe in ("../outside", "blobs/aa/../secret.blob", "C:/secret"):
        with pytest.raises(InvalidStorageKey):
            store.read_bytes(unsafe)

    with pytest.raises(BlobValidationError, match="blob_too_large"):
        store.write_stream((b"ab", b"cd"), max_bytes=3)
    with pytest.raises(BlobValidationError, match="empty_blob"):
        store.write_bytes(b"")
    assert not list((tmp_path / ".staging").iterdir())


def test_repository_creates_four_tenant_scoped_metadata_tables(tmp_path: Path) -> None:
    repository = SQLiteFileAssetRepository(tmp_path)
    assert repository.count_schema_tenant_columns() == {
        "file_assets": True,
        "file_bindings": True,
        "file_artifacts": True,
        "file_audit_events": True,
    }

    with sqlite3.connect(repository.database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == (
            FILE_ASSET_SCHEMA_VERSION
        )
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(file_assets)").fetchall()
        }
    assert "content" not in columns
    assert "body" not in columns
    assert "internal_path" not in columns

    artifact_blob = FileBlobStore(tmp_path).write_bytes(
        b"wrong namespace", namespace="artifacts"
    )
    with pytest.raises(ValueError, match="storage_key"):
        repository.create_asset(
            "local",
            purpose="chat",
            scope_id="chat-1",
            display_name="wrong.txt",
            format_id="txt",
            media_type="text/plain",
            storage_key=artifact_blob.storage_key,
            sha256=artifact_blob.sha256,
            byte_size=artifact_blob.byte_size,
        )


def test_repository_migrates_v1_bindings_to_confirmation_schema(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "file-assets.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE file_bindings (
                id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                asset_id TEXT NOT NULL,
                purpose TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (tenant_id, id),
                UNIQUE (tenant_id, asset_id, purpose, scope_id)
            )
            """
        )
        connection.execute("PRAGMA user_version = 1")

    repository = SQLiteFileAssetRepository(tmp_path)
    with sqlite3.connect(repository.database_path) as connection:
        columns = {
            row[1]: row
            for row in connection.execute(
                "PRAGMA table_info(file_bindings)"
            ).fetchall()
        }
        table_names = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        schema_version = connection.execute("PRAGMA user_version").fetchone()[0]

    assert schema_version == FILE_ASSET_SCHEMA_VERSION
    assert {"confirmed_at", "confirmed_handling", "confirmation_revision"} <= set(
        columns
    )
    assert columns["confirmation_revision"][4] == "0"
    assert {
        "file_scope_tombstones",
        "file_scope_cleanup_assets",
    } <= table_names


def test_assets_bindings_and_artifacts_are_tenant_scoped(tmp_path: Path) -> None:
    store = FileBlobStore(tmp_path)
    repository = SQLiteFileAssetRepository(tmp_path)
    asset = _asset(repository, store)

    assert asset.display_name == "user-notes.txt"
    assert asset.display_name not in asset.storage_key
    assert repository.get_asset("other", asset.id) is None

    assert repository.add_binding(
        "local", asset.id, purpose="chat", scope_id="chat-1"
    )
    assert repository.binding_exists(
        "local", asset.id, purpose="chat", scope_id="chat-1"
    )
    assert repository.get_bound_asset(
        "local", asset.id, purpose="chat", scope_id="chat-1"
    ) == repository.get_asset("local", asset.id)
    assert repository.get_bound_asset(
        "other", asset.id, purpose="chat", scope_id="chat-1"
    ) is None
    assert not repository.add_binding(
        "local", asset.id, purpose="chat", scope_id="chat-1"
    )
    assert repository.get_asset("local", asset.id).reference_count == 1  # type: ignore[union-attr]
    assert not repository.remove_binding(
        "other", asset.id, purpose="chat", scope_id="chat-1"
    )
    assert repository.remove_binding(
        "local", asset.id, purpose="chat", scope_id="chat-1"
    )
    assert repository.get_asset("local", asset.id).reference_count == 0  # type: ignore[union-attr]

    initially_bound = _asset(
        repository,
        store,
        scope_id="chat-atomic",
        create_initial_binding=True,
    )
    assert initially_bound.reference_count == 1
    assert repository.get_bound_asset(
        "local", initially_bound.id, purpose="chat", scope_id="chat-atomic"
    ) is not None
    assert repository.remove_binding(
        "local",
        initially_bound.id,
        purpose="chat",
        scope_id="chat-atomic",
        expire_if_unreferenced=True,
    )
    released = repository.get_asset("local", initially_bound.id)
    assert released is not None
    assert released.reference_count == 0
    assert released.status == "expired"
    assert released.expires_at is not None

    artifact_blob = store.write_bytes(b"parsed text", namespace="artifacts")
    artifact = repository.create_artifact(
        "local",
        asset.id,
        kind="parsed_document",
        storage_key=artifact_blob.storage_key,
        sha256=artifact_blob.sha256,
        byte_size=artifact_blob.byte_size,
    )
    assert repository.list_artifacts("other", asset.id) == ()
    assert repository.list_artifacts("local", asset.id) == (artifact,)
    wrong_namespace = store.write_bytes(b"not an artifact")
    with pytest.raises(ValueError, match="storage_key"):
        repository.create_artifact(
            "local",
            asset.id,
            kind="wrong_namespace",
            storage_key=wrong_namespace.storage_key,
            sha256=wrong_namespace.sha256,
            byte_size=wrong_namespace.byte_size,
        )

    repository.set_asset_status("local", asset.id, "expired")
    with pytest.raises(FileAssetRepositoryError, match="asset_not_mutable"):
        repository.add_binding(
            "local", asset.id, purpose="chat", scope_id="chat-2"
        )
    rejected_blob = store.write_bytes(b"rejected", namespace="artifacts")
    with pytest.raises(FileAssetRepositoryError, match="asset_not_mutable"):
        repository.create_artifact(
            "local",
            asset.id,
            kind="late_preview",
            storage_key=rejected_blob.storage_key,
            sha256=rejected_blob.sha256,
            byte_size=rejected_blob.byte_size,
        )


def test_ttl_gc_respects_references_and_claims_once(tmp_path: Path) -> None:
    store = FileBlobStore(tmp_path)
    repository = SQLiteFileAssetRepository(tmp_path)
    expired_at = datetime.now(UTC) - timedelta(minutes=1)
    asset = _asset(repository, store, expires_at=expired_at)
    artifact_blob = store.write_bytes(b"preview", namespace="artifacts")
    repository.create_artifact(
        "local",
        asset.id,
        kind="preview",
        storage_key=artifact_blob.storage_key,
        sha256=artifact_blob.sha256,
        byte_size=artifact_blob.byte_size,
    )
    repository.add_binding("local", asset.id, purpose="chat", scope_id="chat-1")

    lifecycle = FileAssetLifecycle(repository, store)
    assert lifecycle.garbage_collect(now=datetime.now(UTC)).claimed == 0
    assert store.exists(asset.storage_key)

    repository.remove_binding("local", asset.id, purpose="chat", scope_id="chat-1")
    first_claim = repository.claim_garbage_collection(now=datetime.now(UTC))
    second_repository = SQLiteFileAssetRepository(tmp_path)
    assert len(first_claim) == 1
    assert second_repository.claim_garbage_collection(now=datetime.now(UTC)) == ()
    repository.set_asset_status("local", asset.id, "ready")
    assert repository.get_asset("local", asset.id).status == "deleting"  # type: ignore[union-attr]
    repository.release_garbage_collection(first_claim[0], error_code="test_retry")

    result = lifecycle.garbage_collect(now=datetime.now(UTC))
    assert result.claimed == 1
    assert result.deleted == 1
    assert result.failed == 0
    assert repository.get_asset("local", asset.id) is None
    assert not store.exists(asset.storage_key)
    assert not store.exists(artifact_blob.storage_key)

    with sqlite3.connect(repository.database_path) as connection:
        event = connection.execute(
            """
            SELECT tenant_id, asset_id, status, error_code
            FROM file_audit_events
            WHERE event_type = 'garbage_collection_completed'
            """
        ).fetchone()
    assert event == ("local", asset.id, "deleted", None)


def test_startup_cleanup_removes_old_partials_and_untracked_blobs_only(
    tmp_path: Path,
) -> None:
    store = FileBlobStore(tmp_path)
    repository = SQLiteFileAssetRepository(tmp_path)
    referenced = _asset(repository, store)
    orphan = store.write_bytes(b"renamed before sqlite insert")
    staging = store.staging_dir / "upload-interrupted.part"
    staging.write_bytes(b"partial")
    old = (datetime.now(UTC) - timedelta(minutes=10)).timestamp()
    os.utime(tmp_path / referenced.storage_key, (old, old))
    os.utime(tmp_path / orphan.storage_key, (old, old))
    os.utime(staging, (old, old))

    result = FileAssetLifecycle(repository, store).cleanup_startup(
        now=datetime.now(UTC), grace_seconds=60
    )

    assert result.staging_deleted == 1
    assert result.orphan_deleted == 1
    assert result.missing_originals_marked == 0
    assert store.exists(referenced.storage_key)
    assert not store.exists(orphan.storage_key)
    assert not staging.exists()


def test_blob_store_rejects_symlinked_managed_components(tmp_path: Path) -> None:
    store = FileBlobStore(tmp_path / "store")
    receipt = store.write_bytes(b"original")
    managed_path = store.storage_dir / receipt.storage_key
    external_dir = tmp_path / "external"
    external_dir.mkdir()
    external_file = external_dir / managed_path.name
    external_file.write_bytes(b"must remain untouched")
    managed_path.unlink()
    managed_path.parent.rmdir()
    try:
        os.symlink(external_dir, managed_path.parent, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(InvalidStorageKey, match="reparse"):
        store.read_bytes(receipt.storage_key)
    with pytest.raises(InvalidStorageKey, match="reparse"):
        store.delete(receipt.storage_key)
    assert external_file.read_bytes() == b"must remain untouched"


def test_blob_store_rechecks_staging_before_creating_temp_file(
    tmp_path: Path,
) -> None:
    store = FileBlobStore(tmp_path / "store")
    external_dir = tmp_path / "external-staging"
    external_dir.mkdir()
    store.staging_dir.rmdir()
    try:
        os.symlink(external_dir, store.staging_dir, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(InvalidStorageKey, match="reparse"):
        store.write_bytes(b"must not leave the storage root")
    assert list(external_dir.iterdir()) == []


def test_missing_ready_blob_is_reconciled_and_service_fails_closed(
    tmp_path: Path,
) -> None:
    store = FileBlobStore(tmp_path)
    repository = SQLiteFileAssetRepository(tmp_path)
    asset = _asset(repository, store, create_initial_binding=True)
    store.delete(asset.storage_key)

    result = FileAssetLifecycle(repository, store).cleanup_startup()
    assert result.missing_originals_marked == 1
    updated = repository.get_asset("local", asset.id)
    assert updated is not None
    assert updated.status == "failed"
    assert updated.last_error_code == "original_blob_missing"
    with sqlite3.connect(repository.database_path) as connection:
        event = connection.execute(
            """
            SELECT status, error_code FROM file_audit_events
            WHERE event_type = 'original_blob_missing'
            """
        ).fetchone()
    assert event == ("failed", "original_blob_missing")

    service = FileAssetService(
        storage_dir=tmp_path,
        mode="native",
        tenant_id="local",
        repository=repository,
        blob_store=store,
    )
    with pytest.raises(FileAssetServiceError) as exc_info:
        service.get_asset(asset.id, purpose="chat", scope_id="chat-1")
    assert exc_info.value.status_code == 409


def test_cleanup_failure_does_not_mask_validation_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = FileAssetService(
        storage_dir=tmp_path,
        mode="native",
        tenant_id="local",
    )

    def fail_delete(_storage_key: str) -> bool:
        raise OSError("simulated cleanup failure")

    monkeypatch.setattr(service.blob_store, "delete", fail_delete)
    with pytest.raises(FileAssetServiceError) as exc_info:
        service.upload(
            BytesIO(b"unsafe\x00text"),
            purpose="rag",
            scope_id="kb-1",
            filename="unsafe.txt",
            declared_media_type="text/plain",
        )
    assert exc_info.value.status_code == 422
    assert exc_info.value.error_code == "binary_text_content"
