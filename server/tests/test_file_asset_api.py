from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import starlette.formparsers
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.file_assets import api as file_asset_api
from server.file_assets.api import router
from server.file_assets.service import FileAssetService, get_file_asset_service


def _client(tmp_path: Path, *, mode: str = "native", tenant_id: str = "local"):
    service = FileAssetService(
        storage_dir=tmp_path,
        mode=mode,
        tenant_id=tenant_id,
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_file_asset_service] = lambda: service
    return TestClient(app), service


def _upload(
    client: TestClient,
    *,
    body: bytes = b"safe notes",
    filename: str = "notes.txt",
    media_type: str = "text/plain",
    purpose: str = "rag",
    scope_id: str = "kb-1",
):
    return client.post(
        "/api/files",
        data={"purpose": purpose, "scope_id": scope_id},
        files={"file": (filename, body, media_type)},
    )


class _UnexpectedUploadService:
    def __init__(self) -> None:
        self.upload_calls = 0

    def upload(self, *_args, **_kwargs):
        self.upload_calls += 1
        raise AssertionError("the upload service must not run")


def test_legacy_mode_disables_mutations_but_not_capabilities(tmp_path: Path) -> None:
    client, _service = _client(tmp_path, mode="legacy")
    assert client.get("/api/files/capabilities?purpose=rag").status_code == 200
    response = _upload(client)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "file_asset_store_disabled"
    assert not any(tmp_path.iterdir())


def test_declared_request_limit_rejects_before_multipart_or_service() -> None:
    assert file_asset_api.MAX_FILE_UPLOAD_BYTES == 50 * 1024 * 1024
    assert file_asset_api.MAX_MULTIPART_OVERHEAD_BYTES == 1024 * 1024
    assert file_asset_api.MAX_FILE_UPLOAD_REQUEST_BYTES == 51 * 1024 * 1024
    service = _UnexpectedUploadService()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_file_asset_service] = lambda: service

    with TestClient(app) as client:
        response = client.post(
            "/api/files",
            content=b"not-parsed",
            headers={
                "content-type": "multipart/form-data; boundary=unused",
                "content-length": str(
                    file_asset_api.MAX_FILE_UPLOAD_REQUEST_BYTES + 1
                ),
            },
        )

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "file_request_too_large"
    assert service.upload_calls == 0


def test_streamed_request_without_content_length_stops_before_full_spool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundary = b"modelmirror-boundary"
    prefix = (
        b"--" + boundary + b'\r\nContent-Disposition: form-data; name="purpose"\r\n\r\nrag\r\n'
        b"--" + boundary + b'\r\nContent-Disposition: form-data; name="scope_id"\r\n\r\nkb-1\r\n'
        b"--" + boundary
        + b'\r\nContent-Disposition: form-data; name="file"; filename="notes.txt"\r\n'
        b"Content-Type: text/plain\r\n\r\n"
    )
    first_file_chunk = b"a" * 512
    rejected_file_chunk = b"b" * 256
    first_request_chunk = prefix + first_file_chunk
    request_limit = len(first_request_chunk) + 32
    full_file_bytes = len(first_file_chunk) + len(rejected_file_chunk)
    request_chunks = [
        first_request_chunk,
        rejected_file_chunk,
        b"\r\n--" + boundary + b"--\r\n",
    ]

    real_spooled_file = starlette.formparsers.SpooledTemporaryFile
    tracked_files: list[Any] = []
    written_bytes = 0

    class TrackingSpooledFile:
        def __init__(self, *args, **kwargs) -> None:
            self._inner = real_spooled_file(*args, **kwargs)
            tracked_files.append(self)

        def write(self, content: bytes) -> int:
            nonlocal written_bytes
            written_bytes += len(content)
            return self._inner.write(content)

        def __getattr__(self, name: str):
            return getattr(self._inner, name)

    monkeypatch.setattr(
        starlette.formparsers, "SpooledTemporaryFile", TrackingSpooledFile
    )
    monkeypatch.setattr(
        file_asset_api, "MAX_FILE_UPLOAD_REQUEST_BYTES", request_limit
    )

    service = _UnexpectedUploadService()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_file_asset_service] = lambda: service
    sent: list[dict[str, Any]] = []
    incoming = [
        {
            "type": "http.request",
            "body": chunk,
            "more_body": index < len(request_chunks) - 1,
        }
        for index, chunk in enumerate(request_chunks)
    ]

    async def receive() -> dict[str, Any]:
        if incoming:
            return incoming.pop(0)
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/files",
        "raw_path": b"/api/files",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (
                b"content-type",
                b"multipart/form-data; boundary=" + boundary,
            ),
        ],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    asyncio.run(app(scope, receive, send))

    response_start = next(
        message for message in sent if message["type"] == "http.response.start"
    )
    response_body = b"".join(
        message.get("body", b"")
        for message in sent
        if message["type"] == "http.response.body"
    )
    assert response_start["status"] == 413
    assert json.loads(response_body)["detail"]["code"] == "file_request_too_large"
    assert service.upload_calls == 0
    assert len(incoming) == 1
    assert 0 < written_bytes < full_file_bytes
    assert tracked_files and all(item.closed for item in tracked_files)


def test_upload_get_unimplemented_and_delete_are_scope_safe(tmp_path: Path) -> None:
    client, service = _client(tmp_path)
    response = _upload(client, body=b"line one\nline two")
    assert response.status_code == 201
    payload = response.json()
    assert payload["purpose"] == "rag"
    assert payload["scope_id"] == "kb-1"
    assert payload["status"] == "ready"
    assert "storage_key" not in payload
    assert "sha256" not in payload
    assert "content" not in payload
    asset_id = payload["asset_id"]

    query = "?purpose=rag&scope_id=kb-1"
    assert client.get(f"/api/files/{asset_id}{query}").json() == payload
    assert client.get(
        f"/api/files/{asset_id}?purpose=rag&scope_id=other"
    ).status_code == 404
    assert client.get(
        f"/api/files/{asset_id}?purpose=agent&scope_id=kb-1"
    ).status_code == 404
    assert client.get(f"/api/files/{asset_id}/preview{query}").status_code == 501
    assert client.post(f"/api/files/{asset_id}/parse{query}").status_code == 501

    other_client, _ = _client(tmp_path, tenant_id="other")
    assert other_client.get(f"/api/files/{asset_id}{query}").status_code == 404

    stored = service.repository.get_asset("local", asset_id)
    assert stored is not None
    blob_path = service.blob_store.storage_dir / stored.storage_key
    assert blob_path.exists()
    assert client.delete(f"/api/files/{asset_id}{query}").status_code == 204
    assert client.get(f"/api/files/{asset_id}{query}").status_code == 404
    assert not blob_path.exists()


def test_delete_returns_202_when_gc_fails_and_next_crud_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, service = _client(tmp_path)
    payload = _upload(client, body=b"delete-me").json()
    asset_id = payload["asset_id"]
    query = "?purpose=rag&scope_id=kb-1"
    record = service.repository.get_asset("local", asset_id)
    assert record is not None
    original_delete = service.blob_store.delete
    failed_once = False

    def fail_once(storage_key: str) -> bool:
        nonlocal failed_once
        if storage_key == record.storage_key and not failed_once:
            failed_once = True
            raise OSError("simulated physical deletion failure")
        return original_delete(storage_key)

    monkeypatch.setattr(service.blob_store, "delete", fail_once)
    response = client.delete(f"/api/files/{asset_id}{query}")
    assert response.status_code == 202
    assert response.json()["status"] == "cleanup_pending"
    assert not service.repository.binding_exists(
        "local", asset_id, purpose="rag", scope_id="kb-1"
    )
    pending = service.repository.get_asset("local", asset_id)
    assert pending is not None
    assert pending.status == "expired"
    assert pending.last_error_code == "blob_delete_failed"
    assert service.blob_store.exists(record.storage_key)
    with sqlite3.connect(service.repository.database_path) as connection:
        audit = connection.execute(
            """
            SELECT status, error_code FROM file_audit_events
            WHERE asset_id = ? AND event_type = 'garbage_collection_failed'
            """,
            (asset_id,),
        ).fetchone()
    assert audit == ("failed", "blob_delete_failed")

    monkeypatch.setattr(service.blob_store, "delete", original_delete)
    assert _upload(
        client,
        body=b"maintenance trigger",
        scope_id="kb-2",
    ).status_code == 201
    assert service.repository.get_asset("local", asset_id) is None
    assert not service.blob_store.exists(record.storage_key)


@pytest.mark.parametrize(
    ("body", "filename", "media_type", "expected_status", "expected_code"),
    [
        (b"hello", "notes.exe", "application/octet-stream", 415, "unsupported_file_format"),
        (b"hello\x00world", "notes.txt", "text/plain", 422, "binary_text_content"),
        (b"", "notes.txt", "text/plain", 422, "empty_file"),
        (b"x" * (10 * 1024 * 1024 + 1), "notes.txt", "text/plain", 413, "file_too_large"),
    ],
)
def test_upload_rejects_unsafe_or_oversized_files_and_cleans_blobs(
    tmp_path: Path,
    body: bytes,
    filename: str,
    media_type: str,
    expected_status: int,
    expected_code: str,
) -> None:
    client, service = _client(tmp_path)
    response = _upload(
        client, body=body, filename=filename, media_type=media_type
    )
    assert response.status_code == expected_status
    assert response.json()["detail"]["code"] == expected_code
    assert service.blob_store.list_storage_keys() == ()
    with sqlite3.connect(service.repository.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM file_assets").fetchone()[0] == 0
        event = connection.execute(
            "SELECT status, error_code FROM file_audit_events ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    assert event == ("failed", expected_code)


def test_only_ready_document_and_data_source_surfaces_can_upload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHAT_FILE_INPUT_ENABLED", "true")
    monkeypatch.setenv("FILE_ASSET_STORE_MODE", "shadow")
    client, _service = _client(tmp_path, mode="shadow")
    chat_response = _upload(
        client, purpose="chat", scope_id="chat-session-1"
    )
    assert chat_response.status_code == 201
    assert chat_response.json()["expires_at"] is not None

    workflow_response = _upload(client, purpose="workflow")
    assert workflow_response.status_code == 422
    assert workflow_response.json()["detail"]["code"] == "file_input_not_ready"

    csv_response = _upload(
        client,
        body=b"name,value\na,1\n",
        filename="data.csv",
        media_type="text/csv",
        purpose="datax",
        scope_id="project-1",
    )
    assert csv_response.status_code == 201


def test_agent_unified_file_asset_endpoint_stays_fail_closed(
    tmp_path: Path,
) -> None:
    client, service = _client(tmp_path, mode="shadow")

    capabilities = client.get("/api/files/capabilities?purpose=agent")
    assert capabilities.status_code == 200
    capability = capabilities.json()["capabilities"][0]
    assert capability["purpose"] == "agent"
    assert capability["interaction_status"] == "disabled"
    assert "Xpert" in capability["status_reason"]
    assert capability["handling_options"] == []

    response = _upload(
        client,
        purpose="agent",
        scope_id="xpert-conversation-1",
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "file_input_not_ready"
    assert service.blob_store.list_storage_keys() == ()
    with sqlite3.connect(service.repository.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM file_assets").fetchone()[0] == 0


def test_chat_upload_requires_explicit_feature_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FILE_ASSET_STORE_MODE", "shadow")
    monkeypatch.setenv("CHAT_FILE_INPUT_ENABLED", "false")
    client, _service = _client(tmp_path, mode="shadow")
    response = _upload(
        client, purpose="chat", scope_id="chat-session-disabled"
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "file_input_not_ready"


def test_expiry_conflict_startup_gc_and_database_are_body_free(tmp_path: Path) -> None:
    client, service = _client(tmp_path)
    body = b"unique-secret-body-not-for-sqlite"
    payload = _upload(client, body=body).json()
    asset_id = payload["asset_id"]
    query = "?purpose=rag&scope_id=kb-1"

    service.repository.set_asset_status("local", asset_id, "processing")
    assert client.get(f"/api/files/{asset_id}{query}").status_code == 409
    service.repository.set_asset_status("local", asset_id, "expired")
    assert client.get(f"/api/files/{asset_id}{query}").status_code == 410
    assert client.delete(f"/api/files/{asset_id}{query}").status_code == 204

    orphan_receipt = service.blob_store.write_bytes(b"ttl-orphan")
    service.repository.create_asset(
        "local",
        purpose="rag",
        scope_id="kb-old",
        display_name="old.txt",
        format_id="plain_text",
        media_type="text/plain",
        storage_key=orphan_receipt.storage_key,
        sha256=orphan_receipt.sha256,
        byte_size=orphan_receipt.byte_size,
        status="ready",
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    restarted = FileAssetService(storage_dir=tmp_path, mode="native", tenant_id="local")
    restarted.repository
    assert not restarted.blob_store.exists(orphan_receipt.storage_key)

    database_bytes = (tmp_path / "file-assets.sqlite3").read_bytes()
    assert body not in database_bytes


def test_persistence_failure_removes_the_private_blob(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, service = _client(tmp_path)
    repository = service.repository

    def fail_create_asset(*_args, **_kwargs):
        raise sqlite3.OperationalError("simulated persistence failure")

    monkeypatch.setattr(repository, "create_asset", fail_create_asset)
    response = _upload(client, body=b"must-not-survive")

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "file_asset_persistence_failed"
    assert service.blob_store.list_storage_keys() == ()
    assert "storage_key" not in response.text
    assert "sha256" not in response.text
    assert "must-not-survive" not in response.text
    with sqlite3.connect(repository.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM file_assets").fetchone()[0] == 0
        event = connection.execute(
            "SELECT status, error_code FROM file_audit_events "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    assert event == ("failed", "file_asset_persistence_failed")


def test_chat_scope_delete_is_idempotent_tenant_scoped_and_cleans_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHAT_FILE_INPUT_ENABLED", "true")
    monkeypatch.setenv("FILE_ASSET_STORE_MODE", "native")
    client, service = _client(tmp_path)
    first = _upload(
        client,
        body=b"first private chat file",
        purpose="chat",
        scope_id="chat-target",
    ).json()
    second = _upload(
        client,
        body=b"second private chat file",
        purpose="chat",
        scope_id="chat-target",
    ).json()
    untouched = _upload(
        client,
        body=b"other scope",
        purpose="chat",
        scope_id="chat-other",
    ).json()
    other_client, _other_service = _client(tmp_path, tenant_id="other")
    other_tenant = _upload(
        other_client,
        body=b"other tenant",
        purpose="chat",
        scope_id="chat-target",
    ).json()

    parse_query = "?purpose=chat&scope_id=chat-target"
    assert client.post(
        f"/api/files/{first['asset_id']}/parse{parse_query}"
    ).status_code == 200
    target_records = [
        service.repository.get_asset("local", item["asset_id"])
        for item in (first, second)
    ]
    assert all(item is not None for item in target_records)
    target_keys = {
        record.storage_key
        for record in target_records
        if record is not None
    }
    target_keys.update(
        artifact.storage_key
        for artifact in service.repository.list_artifacts(
            "local", first["asset_id"]
        )
    )

    response = client.delete(
        "/api/files/scopes/chat-target?purpose=chat"
    )

    assert response.status_code == 204
    for item in (first, second):
        assert service.repository.get_asset("local", item["asset_id"]) is None
    assert all(not service.blob_store.exists(key) for key in target_keys)
    assert client.get(
        f"/api/files/{untouched['asset_id']}?purpose=chat&scope_id=chat-other"
    ).status_code == 200
    assert other_client.get(
        f"/api/files/{other_tenant['asset_id']}?purpose=chat&scope_id=chat-target"
    ).status_code == 200
    assert client.delete(
        "/api/files/scopes/chat-target?purpose=chat"
    ).status_code == 204
    assert client.delete(
        "/api/files/scopes/chat-other?purpose=rag"
    ).status_code == 422


def test_chat_scope_delete_returns_202_until_blob_gc_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHAT_FILE_INPUT_ENABLED", "true")
    monkeypatch.setenv("FILE_ASSET_STORE_MODE", "native")
    client, service = _client(tmp_path)
    payload = _upload(
        client,
        body=b"scope cleanup retry",
        purpose="chat",
        scope_id="chat-retry",
    ).json()
    asset_id = payload["asset_id"]
    record = service.repository.get_asset("local", asset_id)
    assert record is not None
    original_delete = service.blob_store.delete
    failed_once = False

    def fail_once(storage_key: str) -> bool:
        nonlocal failed_once
        if storage_key == record.storage_key and not failed_once:
            failed_once = True
            raise OSError("simulated scope cleanup failure")
        return original_delete(storage_key)

    monkeypatch.setattr(service.blob_store, "delete", fail_once)
    first_delete = client.delete(
        "/api/files/scopes/chat-retry?purpose=chat"
    )

    assert first_delete.status_code == 202
    assert first_delete.json()["status"] == "cleanup_pending"
    assert not service.repository.binding_exists(
        "local", asset_id, purpose="chat", scope_id="chat-retry"
    )
    assert service.repository.scope_cleanup_pending(
        "local", purpose="chat", scope_id="chat-retry"
    )
    assert service.blob_store.exists(record.storage_key)

    monkeypatch.setattr(service.blob_store, "delete", original_delete)
    retry = client.delete(
        "/api/files/scopes/chat-retry?purpose=chat"
    )

    assert retry.status_code == 204
    assert service.repository.get_asset("local", asset_id) is None
    assert not service.blob_store.exists(record.storage_key)
