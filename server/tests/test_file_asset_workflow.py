from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.file_assets.api import router
from server.file_assets.contracts import FileInteractionStatus, FilePurpose
from server.file_assets.registry import get_file_format_registry
from server.file_assets.service import (
    FileAssetService,
    FileAssetServiceError,
    get_file_asset_service,
)


def _service(tmp_path: Path) -> FileAssetService:
    return FileAssetService(storage_dir=tmp_path, mode="native", tenant_id="local")


def test_workflow_capability_is_disabled_until_both_runtime_gates_are_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = get_file_format_registry()
    monkeypatch.delenv("WORKFLOW_FILE_ASSETS_ENABLED", raising=False)
    monkeypatch.setenv("FILE_ASSET_STORE_MODE", "native")
    disabled = registry.capabilities_response(
        purpose=FilePurpose.WORKFLOW
    ).capabilities[0]
    assert disabled.interaction_status is FileInteractionStatus.DISABLED
    assert disabled.status_reason
    assert registry.extensions_for("workflow", "document") == ()

    monkeypatch.setenv("WORKFLOW_FILE_ASSETS_ENABLED", "true")
    ready = registry.capabilities_response(
        purpose=FilePurpose.WORKFLOW
    ).capabilities[0]
    assert ready.interaction_status is FileInteractionStatus.READY
    assert {"txt", "xlsx", "docx", "pptx"} <= {
        extension.removeprefix(".")
        for extension in registry.extensions_for("workflow", "document")
    }

    monkeypatch.setenv("FILE_ASSET_STORE_MODE", "legacy")
    legacy = registry.capabilities_response(
        purpose=FilePurpose.WORKFLOW
    ).capabilities[0]
    assert legacy.interaction_status is FileInteractionStatus.DISABLED


def test_workflow_asset_resolution_is_scope_bound_and_path_opaque(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKFLOW_FILE_ASSETS_ENABLED", "true")
    monkeypatch.setenv("FILE_ASSET_STORE_MODE", "native")
    service = _service(tmp_path)
    uploaded = service.upload(
        io.BytesIO("第一段\n第二段".encode("utf-8")),
        purpose=FilePurpose.WORKFLOW,
        scope_id="workflow:wf-safe",
        filename="notes.txt",
        declared_media_type="text/plain",
    )

    parsed = service.resolve_workflow_document(
        uploaded.asset_id,
        scope_id="workflow:wf-safe",
    )
    assert parsed.format == "plain_text"
    assert "第一段" in "\n".join(section.text for section in parsed.sections)

    with pytest.raises(FileAssetServiceError) as error:
        service.resolve_workflow_document(
            uploaded.asset_id,
            scope_id="workflow:wf-other",
        )
    assert error.value.status_code == 404
    assert error.value.error_code == "file_asset_not_found"


def test_workflow_asset_list_is_scope_bound_and_public_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKFLOW_FILE_ASSETS_ENABLED", "true")
    monkeypatch.setenv("FILE_ASSET_STORE_MODE", "native")
    service = _service(tmp_path)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_file_asset_service] = lambda: service
    client = TestClient(app)

    first = service.upload(
        io.BytesIO(b"first workflow file"),
        purpose=FilePurpose.WORKFLOW,
        scope_id="workflow:wf-one",
        filename="first.txt",
        declared_media_type="text/plain",
    )
    service.upload(
        io.BytesIO(b"second workflow file"),
        purpose=FilePurpose.WORKFLOW,
        scope_id="workflow:wf-two",
        filename="second.txt",
        declared_media_type="text/plain",
    )

    response = client.get(
        "/api/files",
        params={"purpose": "workflow", "scope_id": "workflow:wf-one"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["asset_id"] == first.asset_id
    assert payload["items"][0]["display_name"] == "first.txt"
    serialized = str(payload).lower()
    assert "storage_key" not in serialized
    assert "sha256" not in serialized
    assert "second workflow file" not in serialized

    other_scope = client.get(
        "/api/files",
        params={"purpose": "workflow", "scope_id": "workflow:wf-missing"},
    )
    assert other_scope.status_code == 200
    assert other_scope.json() == {"items": [], "total": 0}
