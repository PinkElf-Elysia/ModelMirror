from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from .blob_store import FileBlobStore
from .repository import (
    FileAssetRecord,
    GarbageCollectionClaim,
    SQLiteFileAssetRepository,
)


@dataclass(frozen=True, slots=True)
class GarbageCollectionResult:
    claimed: int
    deleted: int
    failed: int


@dataclass(frozen=True, slots=True)
class StartupCleanupResult:
    staging_deleted: int
    orphan_deleted: int
    missing_originals_marked: int


class FileAssetLifecycle:
    """Coordinates metadata claims with idempotent private-blob deletion."""

    def __init__(
        self,
        repository: SQLiteFileAssetRepository,
        blob_store: FileBlobStore,
    ) -> None:
        self.repository = repository
        self.blob_store = blob_store

    def garbage_collect(
        self,
        *,
        now: datetime | None = None,
        limit: int = 100,
    ) -> GarbageCollectionResult:
        claims = self.repository.claim_garbage_collection(
            now=now or datetime.now(UTC), limit=limit
        )
        deleted = 0
        failed = 0
        for claim in claims:
            try:
                self._delete_claim(claim)
            except Exception:
                failed += 1
                self.repository.release_garbage_collection(
                    claim, error_code="blob_delete_failed"
                )
                self.repository.record_audit_event(
                    claim.asset.tenant_id,
                    asset_id=claim.asset.id,
                    event_type="garbage_collection_failed",
                    sha256=claim.asset.sha256,
                    format_id=claim.asset.format_id,
                    byte_size=claim.asset.byte_size,
                    status="failed",
                    error_code="blob_delete_failed",
                )
                continue
            deleted += 1
        return GarbageCollectionResult(
            claimed=len(claims), deleted=deleted, failed=failed
        )

    def cleanup_startup(
        self,
        *,
        now: datetime | None = None,
        grace_seconds: int = 300,
    ) -> StartupCleanupResult:
        """Reconcile crash remnants before accepting new uploads.

        A grace window avoids racing a writer that has renamed its blob but has
        not yet committed the matching SQLite row.
        """

        current = now or datetime.now(UTC)
        threshold = current.timestamp() - max(1, int(grace_seconds))
        staging_deleted = self.blob_store.cleanup_staging(
            older_than_epoch=threshold
        )
        referenced = self.repository.referenced_storage_keys()
        orphan_deleted = 0
        for storage_key in self.blob_store.list_storage_keys(
            older_than_epoch=threshold
        ):
            if storage_key in referenced:
                continue
            if self.blob_store.delete(storage_key):
                orphan_deleted += 1
        missing_originals_marked = 0
        for asset in self.repository.list_ready_assets():
            if self.blob_store.exists(asset.storage_key):
                continue
            if self.reconcile_original(asset) is not None:
                missing_originals_marked += 1
        return StartupCleanupResult(
            staging_deleted=staging_deleted,
            orphan_deleted=orphan_deleted,
            missing_originals_marked=missing_originals_marked,
        )

    def reconcile_original(
        self, asset: FileAssetRecord
    ) -> FileAssetRecord | None:
        """Fail closed when SQLite says ready but the original blob is absent."""

        if asset.status != "ready" or self.blob_store.exists(asset.storage_key):
            return asset
        updated = self.repository.mark_original_missing(
            asset.tenant_id, asset.id
        )
        if updated is None:
            return None
        self.repository.record_audit_event(
            updated.tenant_id,
            asset_id=updated.id,
            event_type="original_blob_missing",
            sha256=updated.sha256,
            format_id=updated.format_id,
            byte_size=updated.byte_size,
            status=updated.status,
            error_code="original_blob_missing",
        )
        return updated

    def _delete_claim(self, claim: GarbageCollectionClaim) -> None:
        for storage_key in claim.storage_keys:
            self.blob_store.delete(storage_key)
        if not self.repository.complete_garbage_collection(claim):
            raise RuntimeError("garbage_collection_claim_lost")
        self.repository.record_audit_event(
            claim.asset.tenant_id,
            asset_id=claim.asset.id,
            event_type="garbage_collection_completed",
            sha256=claim.asset.sha256,
            format_id=claim.asset.format_id,
            byte_size=claim.asset.byte_size,
            status="deleted",
        )
