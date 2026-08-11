from __future__ import annotations

import base64
import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.file_assets.api import router
from server.file_assets.contracts import FilePurpose
from server.file_assets.output_service import FileOutputService, get_file_output_service
from server.file_assets.output_renderer import OutputRenderError, RenderedOutput
from server.file_assets.repository import FILE_ASSET_SCHEMA_VERSION, SQLiteFileAssetRepository
from server.file_assets.service import FileAssetService, FileAssetServiceError
from server.xperts.api import set_xpert_context_store_for_tests
from server.xperts.context import XpertContextStore


def _services(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FILE_ASSET_STORE_MODE", "native")
    monkeypatch.setenv("FILE_OUTPUT_ASSETS_ENABLED", "true")
    monkeypatch.setenv("CHAT_FILE_OUTPUT_TOOL_ENABLED", "true")
    monkeypatch.setenv("WORKFLOW_FILE_ASSETS_ENABLED", "true")
    file_service = FileAssetService(storage_dir=tmp_path, mode="native")
    output_service = FileOutputService(file_service)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_file_output_service] = lambda: output_service
    return TestClient(app), file_service, output_service


def _publish(service: FileOutputService, **overrides):
    values = {
        "content": b"ModelMirror output closure\n",
        "purpose": FilePurpose.CHAT,
        "scope_id": "chat-scope-1",
        "producer_kind": "chat_tool",
        "producer_artifact_id": "turn-1-file-1",
        "filename": "report.txt",
        "format_id": "plain_text",
        "media_type": "text/plain",
        "source_message_id": "turn-1",
    }
    values.update(overrides)
    return service.register_bytes(**values)


def test_output_capability_is_fail_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FILE_OUTPUT_ASSETS_ENABLED", "false")
    monkeypatch.setenv("CHAT_FILE_OUTPUT_TOOL_ENABLED", "false")
    file_service = FileAssetService(storage_dir=tmp_path, mode="native")
    service = FileOutputService(file_service)
    disabled = service.capabilities(purpose="chat", model_id="model-1")
    assert disabled.version == "modelmirror-file-output-capabilities-v1"
    assert disabled.interaction_status == "disabled"

    monkeypatch.setenv("FILE_OUTPUT_ASSETS_ENABLED", "true")
    monkeypatch.setenv("CHAT_FILE_OUTPUT_TOOL_ENABLED", "true")
    unverified = service.capabilities(purpose="chat", model_id="model-1")
    assert unverified.interaction_status == "planned"
    ready = service.capabilities(
        purpose="chat", model_id="model-1", verified_chat_tool=True
    )
    assert ready.registry_version == "modelmirror-file-formats-v5"
    assert ready.interaction_status == "ready"
    assert ready.limits.max_files_per_turn == 5
    assert ready.limits.hard_ttl_seconds == 7 * 24 * 60 * 60
    by_format = {item.format_id: item for item in ready.formats}
    assert "reuse" in by_format["plain_text"].actions
    assert "reuse" in by_format["png"].actions
    assert "save_rag" not in by_format["png"].actions

    workflow = service.capabilities(purpose="workflow", model_id=None)
    workflow_formats = {item.format_id: item for item in workflow.formats}
    assert workflow.interaction_status == "ready"
    assert "reuse" not in workflow_formats["png"].actions


def test_publish_list_preview_download_and_delete_are_scope_safe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client, file_service, service = _services(tmp_path, monkeypatch)
    published = _publish(service)
    assert published.status == "completed"
    assert published.asset_id
    assert published.expires_at

    duplicate = _publish(service)
    assert duplicate.output_id == published.output_id
    assert len(file_service.blob_store.list_storage_keys()) == 1

    listed = client.get("/api/files/outputs?purpose=chat&scope_id=chat-scope-1")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert "storage_key" not in listed.text
    assert "sha256" not in listed.text

    preview = client.get(
        f"/api/files/outputs/{published.output_id}/preview?purpose=chat&scope_id=chat-scope-1"
    )
    assert preview.status_code == 200
    assert preview.json()["text"] == "ModelMirror output closure\n"

    download = client.get(
        f"/api/files/outputs/{published.output_id}/download?purpose=chat&scope_id=chat-scope-1"
    )
    assert download.status_code == 200
    assert download.content == b"ModelMirror output closure\n"
    assert download.headers["x-content-type-options"] == "nosniff"
    assert download.headers["content-disposition"].startswith("attachment;")

    hidden = client.get(
        f"/api/files/outputs/{published.output_id}?purpose=chat&scope_id=other-scope"
    )
    assert hidden.status_code == 404

    deleted = client.delete(
        f"/api/files/outputs/{published.output_id}?purpose=chat&scope_id=chat-scope-1"
    )
    assert deleted.status_code == 204
    assert file_service.repository.get_asset(file_service.tenant_id, published.asset_id) is None


def test_chat_scope_cleanup_preserves_output_hard_ttl_until_explicit_delete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client, file_service, service = _services(tmp_path, monkeypatch)
    published = _publish(service)
    assert published.asset_id

    assert file_service.delete_scope(
        purpose=FilePurpose.CHAT, scope_id="chat-scope-1"
    ) is False
    surviving = service.get_output(
        published.output_id,
        purpose=FilePurpose.CHAT,
        scope_id="chat-scope-1",
    )
    assert surviving.status == "completed"
    assert file_service.repository.get_asset(
        file_service.tenant_id, published.asset_id
    ) is not None

    deleted = client.delete(
        f"/api/files/outputs/{published.output_id}?purpose=chat&scope_id=chat-scope-1"
    )
    assert deleted.status_code == 204
    assert file_service.repository.get_asset(
        file_service.tenant_id, published.asset_id
    ) is None


def test_registration_rejects_executables_mismatch_and_turn_limits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _client, _file_service, service = _services(tmp_path, monkeypatch)
    with pytest.raises(FileAssetServiceError) as executable:
        _publish(service, filename="payload.exe")
    assert executable.value.error_code == "output_executable_not_allowed"

    with pytest.raises(FileAssetServiceError) as mismatch:
        _publish(service, filename="report.json")
    assert mismatch.value.error_code == "output_type_mismatch"

    for index in range(5):
        _publish(
            service,
            producer_artifact_id=f"turn-limit-{index}",
            source_message_id="turn-limit",
            filename=f"report-{index}.txt",
        )
    with pytest.raises(FileAssetServiceError) as count:
        _publish(
            service,
            producer_artifact_id="turn-limit-6",
            source_message_id="turn-limit",
            filename="report-6.txt",
        )
    assert count.value.status_code == 413
    assert count.value.error_code == "output_count_limit_exceeded"


@pytest.mark.parametrize(
    ("format_id", "filename", "media_type", "valid_content"),
    [
        ("png", "image.png", "image/png", b"\x89PNG\r\n\x1a\nbody"),
        ("webp", "image.webp", "image/webp", b"RIFF\x04\x00\x00\x00WEBP"),
        ("wav", "audio.wav", "audio/wav", b"RIFF\x04\x00\x00\x00WAVE"),
        ("mp3", "audio.mp3", "audio/mpeg", b"ID3\x04\x00\x00"),
        ("flac", "audio.flac", "audio/flac", b"fLaC\x00\x00"),
        ("ogg", "audio.ogg", "audio/ogg", b"OggS\x00\x02"),
        ("m4a", "audio.m4a", "audio/mp4", b"\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00"),
        ("audio_webm", "audio.webm", "audio/webm", b"\x1a\x45\xdf\xa3body"),
        ("mp4", "video.mp4", "video/mp4", b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00"),
        ("mov", "video.mov", "video/quicktime", b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00"),
        ("video_webm", "video.webm", "video/webm", b"\x1a\x45\xdf\xa3body"),
        ("mpeg", "video.mpeg", "video/mpeg", b"\x00\x00\x01\xb3body"),
    ],
)
def test_captured_media_is_sniffed_before_registration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    format_id: str,
    filename: str,
    media_type: str,
    valid_content: bytes,
) -> None:
    _client, _file_service, service = _services(tmp_path, monkeypatch)
    output = _publish(
        service,
        content=valid_content,
        producer_kind="browser",
        producer_artifact_id=f"valid-{format_id}",
        filename=filename,
        format_id=format_id,
        media_type=media_type,
    )
    assert output.status == "completed"

    with pytest.raises(FileAssetServiceError) as invalid:
        _publish(
            service,
            content=b"not-the-claimed-media-type",
            producer_kind="browser",
            producer_artifact_id=f"invalid-{format_id}",
            filename=filename,
            format_id=format_id,
            media_type=media_type,
        )
    assert invalid.value.error_code == "output_signature_invalid"


def test_explicit_local_and_mcp_artifacts_are_copied_without_source_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _client, file_service, service = _services(tmp_path / "state", monkeypatch)
    trusted = tmp_path / "producer"
    trusted.mkdir()
    source = trusted / "result.md"
    source.write_bytes(b"# Explicit artifact\n")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()

    registered = service.register_runtime_artifact(
        source,
        trusted_root=trusted,
        producer_kind="sandbox",
        producer_artifact_id="sandbox-artifact-1",
        filename="result.md",
        media_type="text/markdown",
        runtime_metadata={
            "xpert_id": "xpert-1",
            "conversation_id": "conversation-1",
            "run_id": "run-1",
            "node_id": "node-1",
        },
        expected_size=source.stat().st_size,
        expected_sha256=digest,
    )
    assert registered is not None
    assert registered.scope_id == "xpert:xpert-1:conversation-1"
    row = file_service.repository.get_output_record(
        file_service.tenant_id, registered.output_id
    )
    assert row is not None
    assert str(source) not in repr(row)

    embedded = service.register_mcp_embedded_artifacts(
        [
            {
                "type": "resource",
                "resource": {
                    "blob": base64.b64encode(b"MCP artifact\n").decode("ascii"),
                    "mimeType": "text/plain",
                },
                "_meta": {
                    "modelmirror/outputArtifact": {
                        "artifact_id": "artifact-1",
                        "filename": "mcp-result.txt",
                    }
                },
            },
            {
                "type": "resource_link",
                "uri": "https://example.com/not-fetched.txt",
            },
        ],
        runtime_metadata={"workflow_id": "workflow-1", "run_id": "run-2"},
        tool_name="create_report",
    )
    assert len(embedded) == 1
    assert embedded[0]["scope_id"] == "workflow:workflow-1"
    assert embedded[0]["producer_kind"] == "mcp"


def test_local_artifact_rejects_hash_change_and_link(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _client, _file_service, service = _services(tmp_path / "state", monkeypatch)
    trusted = tmp_path / "producer"
    trusted.mkdir()
    source = trusted / "result.txt"
    source.write_text("changed", encoding="utf-8")
    with pytest.raises(FileAssetServiceError) as changed:
        service.register_local_artifact(
            source,
            trusted_root=trusted,
            purpose="workflow",
            scope_id="workflow:one",
            producer_kind="sandbox",
            producer_artifact_id="changed-1",
            filename="result.txt",
            media_type="text/plain",
            expected_sha256="0" * 64,
        )
    assert changed.value.error_code == "output_source_changed"

    link = trusted / "link.txt"
    try:
        link.symlink_to(source)
    except OSError:
        pytest.skip("Symlink creation is unavailable on this host.")
    with pytest.raises(FileAssetServiceError) as linked:
        service.register_local_artifact(
            link,
            trusted_root=trusted,
            purpose="workflow",
            scope_id="workflow:one",
            producer_kind="sandbox",
            producer_artifact_id="link-1",
            filename="link.txt",
            media_type="text/plain",
        )
    assert linked.value.error_code == "output_source_link_denied"


def test_outputs_without_message_id_use_independent_producer_turns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _client, _file_service, service = _services(tmp_path, monkeypatch)
    for index in range(7):
        item = _publish(
            service,
            producer_artifact_id=f"independent-{index}",
            source_message_id=None,
        )
        assert item.status == "completed"


def test_integrity_mismatch_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _client, file_service, service = _services(tmp_path, monkeypatch)
    output = _publish(service)
    asset = file_service.repository.get_asset(file_service.tenant_id, output.asset_id)
    assert asset is not None
    path = file_service.blob_store.storage_dir / asset.storage_key
    path.write_bytes(b"tampered")
    with pytest.raises(FileAssetServiceError) as error:
        service.read_output(output.output_id, purpose="chat", scope_id="chat-scope-1")
    assert error.value.error_code == "file_output_integrity_failed"


def test_reuse_creates_short_lived_input_copy_without_consuming_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _client, file_service, service = _services(tmp_path, monkeypatch)
    output = _publish(service)
    confirmation = service.confirm_reuse(
        output.output_id,
        purpose="chat",
        scope_id="chat-scope-1",
        handling="extract",
        target_id="provider/model-1",
        gateway="default",
    )
    assert confirmation.asset_id != output.asset_id
    assert confirmation.confirmation_revision == 1
    assert confirmation.output_confirmation_revision == 1
    copied = file_service.repository.get_asset(
        file_service.tenant_id, confirmation.asset_id
    )
    original = file_service.repository.get_asset(
        file_service.tenant_id, output.asset_id
    )
    assert copied is not None and original is not None
    assert copied.storage_key != original.storage_key
    assert copied.sha256 == original.sha256

    service.validate_reuse_confirmation(
        output.output_id,
        asset_id=confirmation.asset_id,
        purpose="chat",
        scope_id="chat-scope-1",
        handling="extract",
        target_id="provider/model-1",
        gateway="default",
        output_confirmation_revision=confirmation.output_confirmation_revision,
    )
    with pytest.raises(FileAssetServiceError) as changed_target:
        service.validate_reuse_confirmation(
            output.output_id,
            asset_id=confirmation.asset_id,
            purpose="chat",
            scope_id="chat-scope-1",
            handling="extract",
            target_id="provider/model-2",
            gateway="default",
            output_confirmation_revision=confirmation.output_confirmation_revision,
        )
    assert changed_target.value.error_code == "output_reuse_confirmation_required"

    file_service.delete_asset(
        confirmation.asset_id, purpose="chat", scope_id="chat-scope-1"
    )
    _record, content = service.read_output(
        output.output_id, purpose="chat", scope_id="chat-scope-1"
    )
    assert content == b"ModelMirror output closure\n"


def test_chat_media_reuse_keeps_the_original_asset_and_revalidates_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _client, _file_service, service = _services(tmp_path, monkeypatch)
    content = b"\x89PNG\r\n\x1a\nconfirmed-image"
    output = _publish(
        service,
        content=content,
        producer_kind="chat_image",
        producer_artifact_id="chat-image-reuse-1",
        filename="generated.png",
        format_id="png",
        media_type="image/png",
    )
    confirmation = service.confirm_reuse(
        output.output_id,
        purpose="chat",
        scope_id="chat-scope-1",
        handling="extract",
        target_id="provider/vision-model",
        gateway="default",
    )
    assert confirmation.asset_id == output.asset_id
    assert confirmation.confirmation_revision == confirmation.output_confirmation_revision

    record, resolved = service.resolve_media_reuse(
        output.output_id,
        asset_id=confirmation.asset_id,
        scope_id="chat-scope-1",
        target_id="provider/vision-model",
        gateway="default",
        output_confirmation_revision=confirmation.output_confirmation_revision,
        expected_kind="image",
    )
    assert record.asset_id == output.asset_id
    assert resolved == content

    with pytest.raises(FileAssetServiceError) as changed_model:
        service.resolve_media_reuse(
            output.output_id,
            asset_id=confirmation.asset_id,
            scope_id="chat-scope-1",
            target_id="provider/other-model",
            gateway="default",
            output_confirmation_revision=confirmation.output_confirmation_revision,
            expected_kind="image",
        )
    assert changed_model.value.error_code == "output_reuse_confirmation_required"

    with pytest.raises(FileAssetServiceError) as wrong_kind:
        service.resolve_media_reuse(
            output.output_id,
            asset_id=confirmation.asset_id,
            scope_id="chat-scope-1",
            target_id="provider/vision-model",
            gateway="default",
            output_confirmation_revision=confirmation.output_confirmation_revision,
            expected_kind="video",
        )
    assert wrong_kind.value.error_code == "output_reuse_not_supported"


def test_workflow_reuse_creates_a_scope_owned_input_without_auto_selecting_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _client, file_service, service = _services(tmp_path, monkeypatch)
    output = _publish(
        service,
        purpose=FilePurpose.WORKFLOW,
        scope_id="workflow:workflow-1",
        producer_kind="sandbox",
        producer_artifact_id="workflow-run-1-file-1",
    )
    confirmation = service.confirm_reuse(
        output.output_id,
        purpose="workflow",
        scope_id="workflow:workflow-1",
        handling="extract",
        target_id="workflow-1",
        gateway="default",
    )
    copied = file_service.repository.get_bound_asset(
        file_service.tenant_id,
        confirmation.asset_id,
        purpose=FilePurpose.WORKFLOW,
        scope_id="workflow:workflow-1",
    )
    assert copied is not None
    assert copied.id != output.asset_id
    assert confirmation.output_confirmation_revision == 1

    with pytest.raises(FileAssetServiceError) as changed_scope:
        service.confirm_reuse(
            output.output_id,
            purpose="workflow",
            scope_id="workflow:workflow-1",
            handling="extract",
            target_id="workflow-2",
            gateway="default",
        )
    assert changed_scope.value.error_code == "output_reuse_confirmation_required"


def test_agent_reuse_imports_into_the_exact_legacy_conversation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _client, _file_service, service = _services(tmp_path, monkeypatch)
    context = XpertContextStore(tmp_path / "xpert-runtime")
    conversation = context.create_conversation("xpert-1")
    set_xpert_context_store_for_tests(context)
    try:
        scope_id = f"xpert:xpert-1:{conversation.conversation_id}"
        output = _publish(
            service,
            purpose=FilePurpose.AGENT,
            scope_id=scope_id,
            producer_kind="mcp",
            producer_artifact_id="agent-run-1-file-1",
        )
        confirmation = service.confirm_reuse(
            output.output_id,
            purpose="agent",
            scope_id=scope_id,
            handling="extract",
            target_id="xpert-1",
            gateway="default",
        )
        imported = context.get_file(
            "xpert-1",
            confirmation.asset_id,
            conversation_id=conversation.conversation_id,
        )
        assert imported.filename == "report.txt"
        assert context.build_file_context(
            "xpert-1",
            [confirmation.asset_id],
            conversation_id=conversation.conversation_id,
        )
        assert confirmation.output_confirmation_revision == 1
    finally:
        set_xpert_context_store_for_tests(None)


def test_restart_interrupts_active_outputs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FILE_OUTPUT_ASSETS_ENABLED", "true")
    service = FileAssetService(storage_dir=tmp_path, mode="native")
    repository = service.repository
    record = repository.create_output_record(
        service.tenant_id,
        purpose="chat",
        scope_id="chat-scope-1",
        producer_kind="chat_tool",
        producer_artifact_id="queued-1",
        display_name="queued.txt",
        format_id="plain_text",
        media_type="text/plain",
        preview_kind="text",
        status="running",
    )
    spec = service.blob_store.write_bytes(b'{"format_id":"plain_text"}', max_bytes=1024)
    task = repository.create_output_task(
        service.tenant_id,
        record.id,
        status="running",
        spec_storage_key=spec.storage_key,
        spec_sha256=spec.sha256,
        spec_byte_size=spec.byte_size,
        spec_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    replacement = FileAssetService(storage_dir=tmp_path, mode="native")
    replacement._ensure_ready()
    interrupted = replacement.repository.get_output_record(replacement.tenant_id, record.id)
    assert interrupted is not None
    assert interrupted.status == "interrupted"
    assert interrupted.error_code == "output_interrupted"
    interrupted_task = replacement.repository.latest_output_task(
        replacement.tenant_id, record.id
    )
    assert interrupted_task is not None
    assert interrupted_task.id == task.id
    assert interrupted_task.status == "interrupted"
    assert interrupted_task.spec_storage_key == spec.storage_key


class _FailOnceRenderer:
    def __init__(self) -> None:
        self.calls = 0

    def render(self, _payload):
        self.calls += 1
        if self.calls == 1:
            raise OutputRenderError(503, "output_renderer_timeout", "timed out")
        return RenderedOutput(
            content=b"retry completed\n",
            filename="retry.txt",
            format_id="plain_text",
            media_type="text/plain",
        )


def test_failed_render_keeps_private_spec_and_retry_consumes_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FILE_ASSET_STORE_MODE", "native")
    monkeypatch.setenv("FILE_OUTPUT_ASSETS_ENABLED", "true")
    file_service = FileAssetService(storage_dir=tmp_path, mode="native")
    renderer = _FailOnceRenderer()
    service = FileOutputService(file_service, renderer=renderer)
    failed = service.render_spec(
        {
            "format_id": "plain_text",
            "filename": "retry.txt",
            "content": "retry completed\n",
        },
        purpose="chat",
        scope_id="chat-scope-1",
        producer_kind="chat_tool",
        producer_artifact_id="render-retry-1",
        source_message_id="turn-retry",
    )
    assert failed.status == "failed"
    assert failed.error_code == "output_renderer_timeout"
    task = file_service.repository.latest_output_task(
        file_service.tenant_id, failed.output_id
    )
    assert task is not None and task.spec_storage_key
    assert task.spec_storage_key in file_service.repository.referenced_storage_keys()

    completed = service.retry_output(
        failed.output_id, purpose="chat", scope_id="chat-scope-1"
    )
    assert completed.status == "completed"
    assert completed.asset_id
    finished_task = file_service.repository.latest_output_task(
        file_service.tenant_id, failed.output_id
    )
    assert finished_task is not None
    assert finished_task.status == "completed"
    assert finished_task.spec_storage_key is None
    assert task.spec_storage_key not in file_service.blob_store.list_storage_keys()


def test_expired_and_explicitly_deleted_retry_specs_are_detached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FILE_ASSET_STORE_MODE", "native")
    monkeypatch.setenv("FILE_OUTPUT_ASSETS_ENABLED", "true")
    file_service = FileAssetService(storage_dir=tmp_path, mode="native")
    service = FileOutputService(file_service)

    def failed_output(name: str, expiry: datetime):
        record = file_service.repository.create_output_record(
            file_service.tenant_id,
            purpose="chat",
            scope_id="chat-scope-1",
            producer_kind="chat_tool",
            producer_artifact_id=name,
            display_name=f"{name}.txt",
            format_id="plain_text",
            media_type="text/plain",
            preview_kind="text",
            status="failed",
        )
        spec = file_service.blob_store.write_bytes(b"private retry", max_bytes=1024)
        file_service.repository.create_output_task(
            file_service.tenant_id,
            record.id,
            status="failed",
            spec_storage_key=spec.storage_key,
            spec_sha256=spec.sha256,
            spec_byte_size=spec.byte_size,
            spec_expires_at=expiry,
        )
        return record, spec.storage_key

    expired, expired_key = failed_output(
        "expired-retry", datetime.now(UTC) - timedelta(seconds=1)
    )
    service.list_outputs(purpose="chat", scope_id="chat-scope-1")
    expired_task = file_service.repository.latest_output_task(
        file_service.tenant_id, expired.id
    )
    assert expired_task is not None and expired_task.spec_storage_key is None
    assert expired_key not in file_service.blob_store.list_storage_keys()

    deleted, deleted_key = failed_output(
        "deleted-retry", datetime.now(UTC) + timedelta(hours=1)
    )
    assert service.delete_output(
        deleted.id, purpose="chat", scope_id="chat-scope-1"
    ) is False
    deleted_task = file_service.repository.latest_output_task(
        file_service.tenant_id, deleted.id
    )
    assert deleted_task is not None and deleted_task.spec_storage_key is None
    assert deleted_key not in file_service.blob_store.list_storage_keys()


def test_schema_v7_is_additive_and_tenant_scoped(tmp_path: Path) -> None:
    repository = SQLiteFileAssetRepository(tmp_path)
    with sqlite3.connect(repository.database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == FILE_ASSET_SCHEMA_VERSION == 7
    columns = repository.count_schema_tenant_columns()
    assert columns["file_output_records"]
    assert columns["file_output_tasks"]
    assert columns["file_output_confirmations"]
