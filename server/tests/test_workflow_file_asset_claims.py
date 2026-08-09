from __future__ import annotations

import threading
from io import BytesIO
from pathlib import Path

import pytest

from server.file_assets import service as service_module
from server.file_assets.document_parser import ParsedDocument, ParsedSection
from server.file_assets.service import FileAssetService, FileAssetServiceError


def test_delete_rejects_asset_claimed_by_running_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKFLOW_FILE_ASSETS_ENABLED", "true")
    monkeypatch.setenv("FILE_ASSET_STORE_MODE", "shadow")
    service = FileAssetService(storage_dir=tmp_path, mode="shadow")
    uploaded = service.upload(
        BytesIO(b"workflow claim"),
        purpose="workflow",
        scope_id="workflow:claim-test",
        filename="claim.txt",
        declared_media_type="text/plain",
    )

    parse_started = threading.Event()
    allow_parse_to_finish = threading.Event()
    resolved: list[ParsedDocument] = []
    failures: list[BaseException] = []

    def blocking_parser(*args, **kwargs) -> ParsedDocument:
        parse_started.set()
        assert allow_parse_to_finish.wait(timeout=5)
        return ParsedDocument(
            format="txt",
            title="claim.txt",
            sections=[ParsedSection(text="workflow claim")],
            warnings=[],
            extracted_chars=14,
            truncated=False,
        )

    monkeypatch.setattr(service_module, "parse_chat_document", blocking_parser)

    def resolve() -> None:
        try:
            resolved.append(
                service.resolve_workflow_document(
                    uploaded.asset_id,
                    scope_id="workflow:claim-test",
                )
            )
        except BaseException as exc:  # pragma: no cover - assertion captures it
            failures.append(exc)

    thread = threading.Thread(target=resolve, daemon=True)
    thread.start()
    assert parse_started.wait(timeout=5)

    try:
        with pytest.raises(FileAssetServiceError) as raised:
            service.delete_asset(
                uploaded.asset_id,
                purpose="workflow",
                scope_id="workflow:claim-test",
            )
        assert raised.value.status_code == 409
        assert raised.value.error_code == "file_asset_in_use"
        assert "路径" not in raised.value.message
    finally:
        allow_parse_to_finish.set()
        thread.join(timeout=5)

    assert not thread.is_alive()
    assert failures == []
    assert resolved[0].sections[0].text == "workflow claim"

    # The read claim is released deterministically when parsing exits.
    service.delete_asset(
        uploaded.asset_id,
        purpose="workflow",
        scope_id="workflow:claim-test",
    )
