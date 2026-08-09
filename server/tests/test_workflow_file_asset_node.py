from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from server import main as main_module
from server.file_assets.document_parser import ParsedDocument, ParsedSection
from server.file_assets.service import FileAssetServiceError


def _workflow(node_data: dict[str, str], *, workflow_id: str = "workflow-files") -> dict:
    return {
        "id": workflow_id,
        "title": "workflow file asset",
        "nodes": [
            {
                "id": "input",
                "type": "input",
                "data": {"kind": "input", "variableName": "user_input"},
            },
            {
                "id": "document",
                "type": "document_extractor",
                "data": {
                    "kind": "document_extractor",
                    "title": "Document asset",
                    "outputVariable": "document_text",
                    **node_data,
                },
            },
            {
                "id": "output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "document_text"},
            },
        ],
        "edges": [
            {"id": "e1", "source": "input", "target": "document"},
            {"id": "e2", "source": "document", "target": "output"},
        ],
    }


def _events(response: httpx.Response) -> list[dict]:
    return [
        json.loads(line[5:].strip())
        for line in response.text.splitlines()
        if line.startswith("data:")
    ]


@pytest.mark.asyncio
async def test_document_extractor_resolves_only_current_workflow_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    class StubFileService:
        def resolve_workflow_document(
            self, asset_id: str, *, scope_id: str
        ) -> ParsedDocument:
            calls.append((asset_id, scope_id))
            return ParsedDocument(
                format="txt",
                title="notes.txt",
                sections=[
                    ParsedSection(text="trusted facts", line_range="1-2")
                ],
                warnings=[],
                extracted_chars=13,
                truncated=False,
            )

    monkeypatch.setattr(main_module, "WORKFLOW_FILE_ASSETS_ENABLED", True)
    monkeypatch.setattr(
        main_module, "get_file_asset_service", lambda: StubFileService()
    )
    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/workflow/run",
            json={
                "workflow": _workflow(
                    {"assetIdVariable": "document_asset_id"},
                    workflow_id="scope-test",
                ),
                "inputs": {
                    "user_input": "summarize",
                    "document_asset_id": "file_123",
                },
            },
        )

    assert response.status_code == 200, response.text
    assert calls == [("file_123", "workflow:scope-test")]
    document_delta = next(
        event
        for event in _events(response)
        if event.get("event") == "node_delta"
        and event.get("node_id") == "document"
    )
    assert "不可信的用户数据" in document_delta["output"]
    assert "代码行 1-2" in document_delta["output"]
    assert "trusted facts" in document_delta["output"]


@pytest.mark.asyncio
async def test_document_extractor_rejects_cross_scope_asset_without_path_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StubFileService:
        def resolve_workflow_document(
            self, asset_id: str, *, scope_id: str
        ) -> ParsedDocument:
            raise FileAssetServiceError(
                404,
                "file_asset_not_found",
                "文件不存在或无权访问。",
            )

    monkeypatch.setattr(main_module, "WORKFLOW_FILE_ASSETS_ENABLED", True)
    monkeypatch.setattr(
        main_module, "get_file_asset_service", lambda: StubFileService()
    )
    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/workflow/run",
            json={
                "workflow": _workflow({"assetIdVariable": "document_asset_id"}),
                "inputs": {"document_asset_id": "file_other_scope"},
            },
        )
        events = _events(response)
        run_id = next(
            event["run_id"]
            for event in events
            if event.get("event") == "workflow_meta"
        )
        run_response = await client.get(f"/api/runtime/runs/{run_id}")

    errors = [event for event in events if event.get("event") == "error"]
    assert any(event.get("message") == "文件不存在或无权访问。" for event in errors)
    assert errors[-1]["code"] == "file_asset_not_found"
    assert not any(event.get("event") == "workflow_end" for event in events)
    assert run_response.status_code == 200
    assert run_response.json()["status"] == "failed"
    assert "storage" not in response.text.lower()
    assert "\\" not in response.text
    assert "storage" not in str(run_response.json().get("error", "")).lower()


@pytest.mark.asyncio
async def test_document_extractor_sanitizes_unexpected_resolver_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_path = r"C:\private\workflow-blobs\customer-secret.pdf"

    class StubFileService:
        def resolve_workflow_document(
            self, asset_id: str, *, scope_id: str
        ) -> ParsedDocument:
            raise RuntimeError(f"parser crashed while reading {secret_path}")

    monkeypatch.setattr(main_module, "WORKFLOW_FILE_ASSETS_ENABLED", True)
    monkeypatch.setattr(
        main_module, "get_file_asset_service", lambda: StubFileService()
    )
    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/workflow/run",
            json={
                "workflow": _workflow({"assetIdVariable": "document_asset_id"}),
                "inputs": {"document_asset_id": "file_parser_failure"},
            },
        )
        events = _events(response)
        run_id = next(
            event["run_id"]
            for event in events
            if event.get("event") == "workflow_meta"
        )
        run_response = await client.get(f"/api/runtime/runs/{run_id}")

    errors = [event for event in events if event.get("event") == "error"]
    assert errors[-1]["run_id"] == run_id
    assert errors[-1]["node_id"] == "document"
    assert errors[-1]["code"] == "workflow_document_read_rejected"
    assert errors[-1]["message"] == "文档未通过安全校验或无法读取，工作流已停止。"
    assert not any(event.get("event") == "workflow_end" for event in events)
    assert run_response.status_code == 200
    assert run_response.json()["status"] == "failed"
    assert secret_path not in response.text
    assert secret_path not in str(run_response.json().get("error", ""))


@pytest.mark.asyncio
async def test_document_extractor_file_assets_fail_closed_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module, "WORKFLOW_FILE_ASSETS_ENABLED", False)
    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/workflow/run",
            json={
                "workflow": _workflow({"assetIdVariable": "document_asset_id"}),
                "inputs": {"document_asset_id": "file_disabled"},
            },
        )

    events = _events(response)
    errors = [event for event in events if event.get("event") == "error"]
    assert any("工作流文件资产当前未启用" in event.get("message", "") for event in errors)
    assert errors[-1]["code"] == "workflow_file_assets_disabled"
    assert not any(event.get("event") == "workflow_end" for event in events)


@pytest.mark.asyncio
async def test_legacy_document_path_compatibility_rejects_traversal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "rag"
    root.mkdir()
    (tmp_path / "secret.txt").write_text("must not leak", encoding="utf-8")
    monkeypatch.setattr(main_module, "WORKFLOW_DOC_EXTRACTOR_ROOT", str(root))
    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/workflow/run",
            json={
                "workflow": _workflow({"sourcePathVariable": "document_path"}),
                "inputs": {"document_path": "../secret.txt"},
            },
        )
        events = _events(response)
        run_id = next(
            event["run_id"]
            for event in events
            if event.get("event") == "workflow_meta"
        )
        run_response = await client.get(f"/api/runtime/runs/{run_id}")

    errors = [event for event in events if event.get("event") == "error"]
    assert errors[-1]["code"] == "workflow_document_read_rejected"
    assert errors[-1]["message"] == "文档未通过安全校验或无法读取，工作流已停止。"
    assert not any(event.get("event") == "workflow_end" for event in events)
    assert run_response.status_code == 200
    assert run_response.json()["status"] == "failed"
    assert "must not leak" not in response.text
    assert str((tmp_path / "secret.txt").resolve()) not in response.text
    assert str(root) not in response.text
    assert "secret.txt" not in str(run_response.json().get("error", ""))


def test_legacy_document_path_rejects_symlinked_component(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "rag"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("must not escape", encoding="utf-8")
    linked_directory = root / "linked"
    try:
        linked_directory.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    monkeypatch.setattr(main_module, "WORKFLOW_DOC_EXTRACTOR_ROOT", str(root))

    with pytest.raises(ValueError, match="legacy_document_reparse_rejected"):
        main_module.read_legacy_workflow_document("linked/secret.txt")


@pytest.mark.asyncio
async def test_workflow_id_cannot_escape_asset_scope() -> None:
    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/workflow/run",
            json={
                "workflow": _workflow(
                    {"assetIdVariable": "document_asset_id"},
                    workflow_id="../other-scope",
                ),
                "inputs": {"document_asset_id": "file_123"},
            },
        )

    assert response.status_code == 422
