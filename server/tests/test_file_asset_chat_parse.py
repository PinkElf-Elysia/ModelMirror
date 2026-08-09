from __future__ import annotations

import io
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from multiprocessing.connection import Connection
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PyPDF2 import PdfWriter
from PyPDF2._page import PageObject
from PyPDF2.generic import (
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
)

from server.file_assets.api import router
from server.file_assets.document_parser import (
    LocalDocumentParseError,
    MAX_PDF_PAGE_CHARACTERS,
    PDF_PARSE_WORKER_MEMORY_BYTES,
    _pdf_worker_address_space_limit,
    _parse_text_pdf,
)
from server.file_assets.registry import get_file_format_registry
from server.file_assets.service import (
    ChatFileSelection,
    FileAssetService,
    FileAssetServiceError,
    get_file_asset_service,
)


@pytest.fixture(autouse=True)
def _enable_chat_file_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAT_FILE_INPUT_ENABLED", "true")
    monkeypatch.setenv("FILE_ASSET_STORE_MODE", "shadow")


def _client(tmp_path: Path) -> tuple[TestClient, FileAssetService]:
    service = FileAssetService(storage_dir=tmp_path, mode="native")
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_file_asset_service] = lambda: service
    return TestClient(app), service


def _upload(
    client: TestClient,
    *,
    body: bytes,
    filename: str,
    media_type: str,
    scope_id: str = "chat-session-1",
):
    return client.post(
        "/api/files",
        data={"purpose": "chat", "scope_id": scope_id},
        files={"file": (filename, body, media_type)},
    )


def _confirm(
    client: TestClient,
    asset_id: str,
    *,
    handling: str = "extract",
    scope_id: str = "chat-session-1",
) -> ChatFileSelection:
    query = f"?purpose=chat&scope_id={scope_id}"
    parsed = client.post(f"/api/files/{asset_id}/parse{query}")
    assert parsed.status_code == 200
    confirmed = client.post(
        f"/api/files/{asset_id}/confirm{query}",
        json={"handling": handling},
    )
    assert confirmed.status_code == 200
    payload = confirmed.json()
    assert payload["asset_id"] == asset_id
    assert payload["handling"] == handling
    assert payload["confirmed_at"]
    return ChatFileSelection(
        asset_id=asset_id,
        handling=handling,  # type: ignore[arg-type]
        confirmation_revision=payload["confirmation_revision"],
    )


def _text_pdf(text: str = "ModelMirror PDF text") -> bytes:
    writer = PdfWriter()
    page = PageObject.create_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font)
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): font_ref}
            )
        }
    )
    stream = DecodedStreamObject()
    chunks = [text[index : index + 1_000] for index in range(0, len(text), 1_000)]
    operators = []
    for chunk in chunks:
        escaped = chunk.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        operators.append(f"({escaped}) Tj")
    stream.set_data(
        f"BT /F1 12 Tf 72 720 Td {' '.join(operators)} ET".encode("ascii")
    )
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.add_page(page)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _blank_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _slow_pdf_worker(
    _path: str,
    sender: Connection,
    _max_page_characters: int,
    _max_total_characters: int,
    _timeout_seconds: float,
) -> None:
    time.sleep(5)
    sender.close()


def test_chat_capability_only_advertises_verified_native_pdf() -> None:
    registry = get_file_format_registry()
    unknown = registry.capabilities_response(
        purpose="chat", model_id="vendor/unknown"
    )
    document = next(
        item for item in unknown.capabilities if item.input_kind.value == "document"
    )
    assert unknown.model_specific is False
    assert [item.handling.value for item in document.handling_options] == [
        "extract"
    ]

    verified = registry.capabilities_response(
        purpose="chat",
        model_id="vendor/file-model",
        verified_native_pdf=True,
    )
    document = next(
        item for item in verified.capabilities if item.input_kind.value == "document"
    )
    assert verified.model_specific is True
    assert [item.handling.value for item in document.handling_options] == [
        "extract",
        "native",
    ]
    assert document.handling_options[-1].format_ids == ("pdf",)


def test_chat_text_upload_parse_preview_and_scope_are_closed(tmp_path: Path) -> None:
    client, service = _client(tmp_path)
    body = b"first line\nsecond private line\n"
    response = _upload(
        client, body=body, filename="notes.txt", media_type="text/plain"
    )
    assert response.status_code == 201
    uploaded = response.json()
    expires_at = datetime.fromisoformat(uploaded["expires_at"])
    assert timedelta(minutes=29) < expires_at - datetime.now(UTC) <= timedelta(minutes=30)

    asset_id = uploaded["asset_id"]
    query = "?purpose=chat&scope_id=chat-session-1"
    assert client.get(f"/api/files/{asset_id}/preview{query}").status_code == 409
    parsed_response = client.post(f"/api/files/{asset_id}/parse{query}")
    assert parsed_response.status_code == 200
    parsed = parsed_response.json()
    assert parsed["format"] == "plain_text"
    assert parsed["title"] == "notes.txt"
    assert parsed["sections"][0]["line_range"] == "1-2"
    assert "second private line" in parsed["sections"][0]["text"]
    assert parsed["truncated"] is False
    assert "storage_key" not in parsed
    preview = client.get(f"/api/files/{asset_id}/preview{query}").json()
    assert datetime.fromisoformat(preview.pop("artifact_expires_at")) >= datetime.fromisoformat(
        parsed.pop("artifact_expires_at")
    )
    assert preview == parsed
    assert client.get(
        f"/api/files/{asset_id}/preview?purpose=chat&scope_id=other"
    ).status_code == 404

    artifacts = service.repository.list_artifacts("local", asset_id)
    assert len(artifacts) == 1
    assert artifacts[0].kind == "chat_parsed_document_v1"
    database_bytes = service.repository.database_path.read_bytes()
    assert b"second private line" not in database_bytes


def test_chat_confirmation_is_server_authoritative_and_revisioned(
    tmp_path: Path,
) -> None:
    client, service = _client(tmp_path)
    asset_id = _upload(
        client,
        body=b"confirm only after preview",
        filename="confirm.txt",
        media_type="text/plain",
    ).json()["asset_id"]
    query = "?purpose=chat&scope_id=chat-session-1"

    before_preview = client.post(
        f"/api/files/{asset_id}/confirm{query}",
        json={"handling": "extract"},
    )
    assert before_preview.status_code == 409
    assert before_preview.json()["detail"]["code"] == "file_preview_not_ready"

    assert client.post(f"/api/files/{asset_id}/parse{query}").status_code == 200
    assert client.post(
        f"/api/files/{asset_id}/confirm?purpose=chat&scope_id=other-session",
        json={"handling": "extract"},
    ).status_code == 404
    unsupported_purpose = client.post(
        f"/api/files/{asset_id}/confirm?purpose=rag&scope_id=chat-session-1",
        json={"handling": "extract"},
    )
    assert unsupported_purpose.status_code == 422
    assert (
        unsupported_purpose.json()["detail"]["code"]
        == "file_confirmation_not_supported"
    )

    first = client.post(
        f"/api/files/{asset_id}/confirm{query}",
        json={"handling": "extract"},
    )
    assert first.status_code == 200
    first_revision = first.json()["confirmation_revision"]
    first_selection = ChatFileSelection(
        asset_id=asset_id,
        handling="extract",
        confirmation_revision=first_revision,
    )
    assert service.resolve_chat_inputs(
        [first_selection], scope_id="chat-session-1"
    )

    second = client.post(
        f"/api/files/{asset_id}/confirm{query}",
        json={"handling": "extract"},
    )
    assert second.status_code == 200
    second_revision = second.json()["confirmation_revision"]
    assert second_revision == first_revision + 1

    with pytest.raises(FileAssetServiceError) as stale:
        service.resolve_chat_inputs(
            [first_selection], scope_id="chat-session-1"
        )
    assert stale.value.error_code == "chat_file_confirmation_required"
    with pytest.raises(FileAssetServiceError) as wrong_handling:
        service.resolve_chat_inputs(
            [
                ChatFileSelection(
                    asset_id=asset_id,
                    handling="native",
                    confirmation_revision=second_revision,
                )
            ],
            scope_id="chat-session-1",
            native_pdf_verified=True,
        )
    assert wrong_handling.value.error_code == "chat_file_confirmation_required"

    assert service.resolve_chat_inputs(
        [
            ChatFileSelection(
                asset_id=asset_id,
                handling="extract",
                confirmation_revision=second_revision,
            )
        ],
        scope_id="chat-session-1",
    )


def test_resolve_and_message_end_delete_original_but_keep_preview(
    tmp_path: Path,
) -> None:
    client, service = _client(tmp_path)
    asset_id = _upload(
        client,
        body=b"content retained only as parsed artifact",
        filename="brief.md",
        media_type="text/markdown",
    ).json()["asset_id"]
    record = service.repository.get_asset("local", asset_id)
    assert record is not None
    original_path = service.blob_store.storage_dir / record.storage_key

    selection = _confirm(client, asset_id)
    resolved = service.resolve_chat_inputs(
        [selection],
        scope_id="chat-session-1",
    )
    assert resolved[0].native_content is None
    assert resolved[0].parsed_document is not None
    assert service.finalize_chat_inputs(resolved, success=False) is False
    assert original_path.exists()
    assert service.resolve_chat_inputs(
        [selection], scope_id="chat-session-1"
    )

    assert service.finalize_chat_inputs(resolved, success=True) is True
    assert not original_path.exists()
    assert service.repository.binding_exists(
        "local", asset_id, purpose="chat", scope_id="chat-session-1"
    )
    response = client.get(
        f"/api/files/{asset_id}/preview?purpose=chat&scope_id=chat-session-1"
    )
    assert response.status_code == 200
    assert response.json()["format"] == "markdown"
    with pytest.raises(FileAssetServiceError) as stale_confirmation:
        service.resolve_chat_inputs([selection], scope_id="chat-session-1")
    assert stale_confirmation.value.error_code == "chat_file_confirmation_required"


def test_finalize_reports_retained_original_when_delete_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, service = _client(tmp_path)
    asset_id = _upload(
        client,
        body=b"retryable original",
        filename="retry.txt",
        media_type="text/plain",
    ).json()["asset_id"]
    resolved = service.resolve_chat_inputs(
        [_confirm(client, asset_id)],
        scope_id="chat-session-1",
    )

    def fail_delete(_storage_key: str) -> bool:
        raise OSError("simulated cleanup failure")

    monkeypatch.setattr(service.blob_store, "delete", fail_delete)
    assert service.finalize_chat_inputs(resolved, success=True) is False
    record = service.repository.get_asset("local", asset_id)
    assert record is not None
    assert service.blob_store.exists(record.storage_key)


def test_runtime_gate_blocks_resolve_after_upload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, service = _client(tmp_path)
    asset_id = _upload(
        client,
        body=b"feature gate",
        filename="gate.txt",
        media_type="text/plain",
    ).json()["asset_id"]
    selection = _confirm(client, asset_id)
    monkeypatch.setenv("CHAT_FILE_INPUT_ENABLED", "false")

    with pytest.raises(FileAssetServiceError) as caught:
        service.resolve_chat_inputs(
            [selection],
            scope_id="chat-session-1",
        )
    assert caught.value.error_code == "file_input_not_ready"


def test_scope_id_contract_is_shared_by_upload_and_resolve(tmp_path: Path) -> None:
    client, service = _client(tmp_path)
    valid_scope = "chat:session.1_part-2"
    uploaded = _upload(
        client,
        body=b"scope contract",
        filename="scope.txt",
        media_type="text/plain",
        scope_id=valid_scope,
    )
    assert uploaded.status_code == 201
    asset_id = uploaded.json()["asset_id"]
    resolved = service.resolve_chat_inputs(
        [_confirm(client, asset_id, scope_id=valid_scope)],
        scope_id=valid_scope,
    )
    assert resolved[0].scope_id == valid_scope

    rejected = _upload(
        client,
        body=b"bad scope",
        filename="bad.txt",
        media_type="text/plain",
        scope_id="chat/session",
    )
    assert rejected.status_code == 422


def test_pdf_text_extraction_has_killable_timeout(tmp_path: Path) -> None:
    path = tmp_path / "timeout.pdf"
    path.write_bytes(_text_pdf())
    started_at = time.monotonic()
    with pytest.raises(LocalDocumentParseError) as caught:
        _parse_text_pdf(
            path,
            title="timeout.pdf",
            timeout_seconds=0.05,
            worker_target=_slow_pdf_worker,
        )
    assert caught.value.error_code == "pdf_parse_timeout"
    assert time.monotonic() - started_at < 3


def test_pdf_text_extraction_rejects_single_page_resource_bomb(
    tmp_path: Path,
) -> None:
    path = tmp_path / "large-page.pdf"
    path.write_bytes(_text_pdf("A" * (MAX_PDF_PAGE_CHARACTERS + 1)))
    with pytest.raises(LocalDocumentParseError) as caught:
        _parse_text_pdf(path, title="large-page.pdf")
    assert caught.value.error_code == "pdf_parse_resource_limit"


def test_pdf_worker_memory_limit_is_a_finite_budget_above_spawn_baseline() -> None:
    baseline = 2 * 1024 * 1024 * 1024

    assert _pdf_worker_address_space_limit(None) == PDF_PARSE_WORKER_MEMORY_BYTES
    assert _pdf_worker_address_space_limit(baseline) == (
        baseline + PDF_PARSE_WORKER_MEMORY_BYTES
    )


def test_native_pdf_requires_verification_and_also_requires_text_preview(
    tmp_path: Path,
) -> None:
    client, service = _client(tmp_path)
    pdf = _text_pdf()
    asset_id = _upload(
        client,
        body=pdf,
        filename="paper.pdf",
        media_type="application/pdf",
    ).json()["asset_id"]
    selection = [_confirm(client, asset_id, handling="native")]

    try:
        service.resolve_chat_inputs(selection, scope_id="chat-session-1")
        raise AssertionError("unverified native PDF must be rejected")
    except Exception as exc:
        assert getattr(exc, "error_code", None) == "native_file_handling_not_available"

    resolved = service.resolve_chat_inputs(
        selection,
        scope_id="chat-session-1",
        native_pdf_verified=True,
    )
    assert resolved[0].native_content == pdf
    assert resolved[0].parsed_document is not None
    assert "ModelMirror PDF text" in resolved[0].parsed_document.sections[0].text

    scanned_id = _upload(
        client,
        body=_blank_pdf(),
        filename="scan.pdf",
        media_type="application/pdf",
        scope_id="chat-session-2",
    ).json()["asset_id"]
    response = client.post(
        f"/api/files/{scanned_id}/parse?purpose=chat&scope_id=chat-session-2"
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "scanned_pdf_requires_ocr"
    assert "视觉流水线" in response.json()["detail"]["message"]


def test_artifact_idle_touch_stops_at_hard_expiry(tmp_path: Path) -> None:
    client, service = _client(tmp_path)
    asset_id = _upload(
        client,
        body=b"ttl test",
        filename="ttl.txt",
        media_type="text/plain",
    ).json()["asset_id"]
    client.post(
        f"/api/files/{asset_id}/parse?purpose=chat&scope_id=chat-session-1"
    )
    artifact = service.repository.latest_artifact(
        "local", asset_id, kind="chat_parsed_document_v1"
    )
    assert artifact is not None
    created = datetime.fromisoformat(artifact.created_at)
    hard_expiry = created + timedelta(hours=24)
    with sqlite3.connect(service.repository.database_path) as connection:
        connection.execute(
            "UPDATE file_artifacts SET expires_at = ? WHERE tenant_id = ? AND id = ?",
            (hard_expiry.isoformat(), "local", artifact.id),
        )
    touched = service.repository.touch_artifact(
        "local",
        asset_id,
        artifact.id,
        idle_seconds=2 * 60 * 60,
        hard_seconds=24 * 60 * 60,
        now=created + timedelta(hours=23),
    )
    assert touched is not None
    assert datetime.fromisoformat(touched.expires_at or "") == hard_expiry
    assert service.repository.touch_artifact(
        "local",
        asset_id,
        artifact.id,
        idle_seconds=2 * 60 * 60,
        hard_seconds=24 * 60 * 60,
        now=hard_expiry,
    ) is None


def test_original_and_artifact_payload_ttls_are_physically_independent(
    tmp_path: Path,
) -> None:
    client, service = _client(tmp_path)
    asset_id = _upload(
        client,
        body=b"independent lifecycle",
        filename="lifecycle.txt",
        media_type="text/plain",
    ).json()["asset_id"]
    query = "?purpose=chat&scope_id=chat-session-1"
    assert client.post(f"/api/files/{asset_id}/parse{query}").status_code == 200
    asset = service.repository.get_asset("local", asset_id)
    artifact = service.repository.latest_artifact(
        "local", asset_id, kind="chat_parsed_document_v1"
    )
    assert asset is not None and artifact is not None
    original_path = service.blob_store.storage_dir / asset.storage_key
    artifact_path = service.blob_store.storage_dir / artifact.storage_key

    past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    with sqlite3.connect(service.repository.database_path) as connection:
        connection.execute(
            "UPDATE file_assets SET expires_at = ? WHERE tenant_id = ? AND id = ?",
            (past, "local", asset_id),
        )
    service._run_maintenance_if_due(force=True)
    assert not original_path.exists()
    assert artifact_path.exists()
    assert client.get(f"/api/files/{asset_id}/preview{query}").status_code == 200

    with sqlite3.connect(service.repository.database_path) as connection:
        connection.execute(
            "UPDATE file_artifacts SET status = 'ready', expires_at = ? "
            "WHERE tenant_id = ? AND id = ?",
            (past, "local", artifact.id),
        )
    service._run_maintenance_if_due(force=True)
    assert not artifact_path.exists()
    response = client.get(f"/api/files/{asset_id}/preview{query}")
    assert response.status_code == 410
    assert response.json()["detail"]["code"] == "file_preview_expired"
