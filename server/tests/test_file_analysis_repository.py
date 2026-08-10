from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from server.file_assets.blob_store import FileBlobStore
from server.file_assets.repository import (
    FileAssetRepositoryError,
    SQLiteFileAssetRepository,
)


def _bound_asset(
    repository: SQLiteFileAssetRepository,
    store: FileBlobStore,
    *,
    tenant_id: str = "local",
    scope_id: str = "chat-analysis",
):
    receipt = store.write_bytes(b"synthetic image bytes")
    return repository.create_asset(
        tenant_id,
        purpose="chat",
        scope_id=scope_id,
        display_name="synthetic.png",
        format_id="png",
        media_type="image/png",
        storage_key=receipt.storage_key,
        sha256=receipt.sha256,
        byte_size=receipt.byte_size,
        status="ready",
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
        create_initial_binding=True,
    )


def test_analysis_confirmation_and_job_are_exact_scoped_and_revisioned(
    tmp_path: Path,
) -> None:
    repository = SQLiteFileAssetRepository(tmp_path)
    asset = _bound_asset(repository, FileBlobStore(tmp_path))
    expires_at = datetime.now(UTC) + timedelta(minutes=5)
    first = repository.confirm_analysis(
        "local",
        asset.id,
        scope_id="chat-analysis",
        mode="vision",
        target_id="connection:model-a",
        config_digest="a" * 64,
        prompt_sha256="b" * 64,
        paid_acknowledged=False,
        expires_at=expires_at,
    )
    assert first is not None
    assert first.revision == 1
    assert first.paid_acknowledged is False

    assert repository.analysis_confirmation_matches(
        "local",
        asset.id,
        scope_id="chat-analysis",
        mode="vision",
        target_id="connection:model-a",
        config_digest="a" * 64,
        prompt_sha256="b" * 64,
        paid_acknowledged=False,
        revision=1,
    )
    assert not repository.analysis_confirmation_matches(
        "local",
        asset.id,
        scope_id="other-scope",
        mode="vision",
        target_id="connection:model-a",
        config_digest="a" * 64,
        prompt_sha256="b" * 64,
        paid_acknowledged=False,
        revision=1,
    )
    assert (
        repository.create_analysis_job(
            "local",
            asset.id,
            scope_id="chat-analysis",
            mode="vision",
            target_id="connection:model-b",
            config_digest="a" * 64,
            prompt_sha256="b" * 64,
            paid_acknowledged=False,
            confirmation_revision=1,
            selected_pages=(1,),
        )
        is None
    )

    second = repository.confirm_analysis(
        "local",
        asset.id,
        scope_id="chat-analysis",
        mode="provider_ocr",
        target_id="openrouter:mistral-ocr",
        config_digest="c" * 64,
        prompt_sha256="d" * 64,
        paid_acknowledged=True,
        expires_at=expires_at,
    )
    assert second is not None
    assert second.revision == 2
    assert not repository.analysis_confirmation_matches(
        "local",
        asset.id,
        scope_id="chat-analysis",
        mode="vision",
        target_id="connection:model-a",
        config_digest="a" * 64,
        prompt_sha256="b" * 64,
        paid_acknowledged=False,
        revision=1,
    )


def test_analysis_job_lifecycle_cancel_and_interrupted_recovery(tmp_path: Path) -> None:
    repository = SQLiteFileAssetRepository(tmp_path)
    asset = _bound_asset(repository, FileBlobStore(tmp_path))
    confirmed = repository.confirm_analysis(
        "local",
        asset.id,
        scope_id="chat-analysis",
        mode="provider_ocr",
        target_id="openrouter:mistral-ocr",
        config_digest="a" * 64,
        prompt_sha256="b" * 64,
        paid_acknowledged=True,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    assert confirmed is not None
    job = repository.create_analysis_job(
        "local",
        asset.id,
        scope_id="chat-analysis",
        mode="provider_ocr",
        target_id="openrouter:mistral-ocr",
        config_digest="a" * 64,
        prompt_sha256="b" * 64,
        paid_acknowledged=True,
        confirmation_revision=confirmed.revision,
        selected_pages=(1, 2, 4),
        analysis_id="analysis_running",
    )
    assert job is not None
    assert job.selected_pages == "1,2,4"
    assert job.status == "queued"
    duplicate = repository.create_analysis_job(
        "local",
        asset.id,
        scope_id="chat-analysis",
        mode="provider_ocr",
        target_id="openrouter:mistral-ocr",
        config_digest="a" * 64,
        prompt_sha256="b" * 64,
        paid_acknowledged=True,
        confirmation_revision=confirmed.revision,
        selected_pages=(1, 2, 4),
        analysis_id="analysis_duplicate_must_not_run",
    )
    assert duplicate is not None
    assert duplicate.id == job.id

    next_confirmation = repository.confirm_analysis(
        "local",
        asset.id,
        scope_id="chat-analysis",
        mode="vision",
        target_id="connection:model-a",
        config_digest="c" * 64,
        prompt_sha256="d" * 64,
        paid_acknowledged=False,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    assert next_confirmation is not None
    with pytest.raises(FileAssetRepositoryError, match="analysis_job_already_active"):
        repository.create_analysis_job(
            "local",
            asset.id,
            scope_id="chat-analysis",
            mode="vision",
            target_id="connection:model-a",
            config_digest="c" * 64,
            prompt_sha256="d" * 64,
            paid_acknowledged=False,
            confirmation_revision=next_confirmation.revision,
            selected_pages=(1,),
        )
    assert repository.claim_analysis_job("local", job.id).status == "running"  # type: ignore[union-attr]
    assert repository.update_analysis_progress(
        "local", job.id, processed_pages=1
    ).processed_pages == 1  # type: ignore[union-attr]
    requested = repository.request_analysis_cancel("local", job.id)
    assert requested is not None
    assert requested.status == "cancel_requested"
    assert requested.cancel_requested is True
    cancelled = repository.acknowledge_analysis_cancel("local", job.id)
    assert cancelled is not None
    assert cancelled.status == "cancelled"
    assert cancelled.completed_at is not None

    stale_confirmation = repository.confirm_analysis(
        "local",
        asset.id,
        scope_id="chat-analysis",
        mode="provider_ocr",
        target_id="openrouter:mistral-ocr",
        config_digest="a" * 64,
        prompt_sha256="b" * 64,
        paid_acknowledged=True,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    assert stale_confirmation is not None
    stale = repository.create_analysis_job(
        "local",
        asset.id,
        scope_id="chat-analysis",
        mode="provider_ocr",
        target_id="openrouter:mistral-ocr",
        config_digest="a" * 64,
        prompt_sha256="b" * 64,
        paid_acknowledged=True,
        confirmation_revision=stale_confirmation.revision,
        selected_pages=(1,),
        analysis_id="analysis_stale",
        now=datetime.now(UTC) - timedelta(minutes=10),
    )
    assert stale is not None
    assert repository.interrupt_stale_analysis_jobs(
        stale_before=datetime.now(UTC) - timedelta(minutes=5)
    ) == 1
    interrupted = repository.get_analysis_job("local", stale.id)
    assert interrupted is not None
    assert interrupted.status == "interrupted"
    assert interrupted.error_code == "analysis_interrupted"


def test_analysis_metadata_schema_never_persists_prompt_or_payload(tmp_path: Path) -> None:
    repository = SQLiteFileAssetRepository(tmp_path)
    with sqlite3.connect(repository.database_path) as connection:
        confirmation_columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(file_analysis_confirmations)"
            ).fetchall()
        }
        job_columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(file_analysis_jobs)"
            ).fetchall()
        }
    prohibited = {
        "prompt",
        "content",
        "body",
        "path",
        "file_data",
        "data_url",
        "response_body",
    }
    assert prohibited.isdisjoint(confirmation_columns)
    assert prohibited.isdisjoint(job_columns)
    assert "prompt_sha256" in confirmation_columns
    assert "prompt_sha256" in job_columns


def test_analysis_send_confirmation_binds_exact_artifact_and_prompt_hash(
    tmp_path: Path,
) -> None:
    repository = SQLiteFileAssetRepository(tmp_path)
    store = FileBlobStore(tmp_path)
    asset = _bound_asset(repository, store)
    receipt = store.write_bytes(b'{"safe":"result"}', namespace="artifacts")
    artifact = repository.create_artifact(
        "local",
        asset.id,
        kind="chat_visual_analysis_v1",
        storage_key=receipt.storage_key,
        sha256=receipt.sha256,
        byte_size=receipt.byte_size,
        expires_at=datetime.now(UTC) + timedelta(hours=2),
    )
    confirmed = repository.confirm_analysis_send(
        "local",
        asset.id,
        scope_id="chat-analysis",
        artifact_id=artifact.id,
        prompt_sha256="d" * 64,
    )
    assert confirmed is not None
    assert confirmed.revision == 1
    assert repository.analysis_send_confirmation_matches(
        "local",
        asset.id,
        scope_id="chat-analysis",
        artifact_id=artifact.id,
        prompt_sha256="d" * 64,
        revision=1,
    )
    assert not repository.analysis_send_confirmation_matches(
        "local",
        asset.id,
        scope_id="chat-analysis",
        artifact_id=artifact.id,
        prompt_sha256="e" * 64,
        revision=1,
    )
    assert repository.clear_analysis_send_confirmation(
        "local", asset.id, scope_id="chat-analysis"
    )
