from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.file_assets.output_renderer import (
    FileOutputRenderer,
    OutputRenderError,
    OutputRenderSidecar,
    validate_render_spec,
)


def test_local_text_json_and_csv_rendering_is_bounded_and_deterministic(
    tmp_path: Path,
) -> None:
    renderer = FileOutputRenderer()
    text = renderer.render(
        {"format_id": "plain_text", "filename": "note.txt", "content": "hello\n"}
    )
    assert text.content == b"hello\n"
    assert text.media_type == "text/plain"

    structured = renderer.render(
        {
            "format_id": "json",
            "filename": "data.json",
            "content": {"answer": 42, "safe": True},
        }
    )
    assert b'"answer": 42' in structured.content
    assert structured.content.endswith(b"\n")

    csv_output = renderer.render(
        {
            "format_id": "csv",
            "filename": "data.csv",
            "rows": [
                ["label", "value"],
                ["formula", "=2+2"],
                ["negative", "-12.5"],
                ["command", "@SUM(A1:A2)"],
            ],
        }
    )
    decoded = csv_output.content.decode("utf-8-sig")
    assert "'=2+2" in decoded
    assert "-12.5" in decoded
    assert "'-12.5" not in decoded
    assert "'@SUM(A1:A2)" in decoded
    assert csv_output.warnings == (
        "Spreadsheet-like formulas were neutralized as text.",
    )


def test_render_spec_rejects_unknown_fields_non_finite_values_and_limits() -> None:
    with pytest.raises(OutputRenderError) as unknown:
        validate_render_spec(
            {
                "format_id": "plain_text",
                "filename": "note.txt",
                "content": "ok",
                "path": "C:/secret.txt",
            }
        )
    assert unknown.value.error_code == "output_spec_invalid"

    with pytest.raises(OutputRenderError) as non_finite:
        validate_render_spec(
            {
                "format_id": "json",
                "filename": "data.json",
                "content": {"value": float("nan")},
            }
        )
    assert non_finite.value.error_code == "output_spec_invalid"

    with pytest.raises(OutputRenderError) as columns:
        validate_render_spec(
            {
                "format_id": "xlsx",
                "filename": "wide.xlsx",
                "sheets": [{"name": "Data", "rows": [["x"] * 201]}],
            }
        )
    assert columns.value.error_code == "output_spec_invalid"


class _FakeManager:
    def __init__(
        self,
        output_root: Path,
        *,
        tamper_digest: bool = False,
        delay: float = 0.0,
    ) -> None:
        self.output_root = output_root
        self.tamper_digest = tamper_digest
        self.delay = delay
        self.workspace_id = ""
        self.connected = 0
        self.called = 0
        self.disconnected = 0

    async def connect_profile(self, **profile):
        self.connected += 1
        assert profile["reconnect_attempts"] == 0
        assert profile["network_policy"] == "catalog-files-none"
        assert profile["server_command"][-1] == "output-renderer-mcp"
        self.workspace_id = profile["environment"]["MCP_FILE_WORKSPACE_ID"]
        if self.delay:
            await asyncio.sleep(self.delay)
        return "session-1"

    async def call_tool(self, session_id: str, tool_name: str, arguments: dict):
        self.called += 1
        assert session_id == "session-1"
        assert tool_name == "render_output_document"
        assert arguments["file_id"].startswith("mcpf_")
        if self.delay:
            await asyncio.sleep(self.delay)
        content = b"%PDF-1.7\n% isolated renderer fixture\n"
        workspace = self.output_root / self.workspace_id
        workspace.mkdir(parents=True)
        path = workspace / "output.pdf"
        path.write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        if self.tamper_digest:
            digest = "0" * 64
        return SimpleNamespace(
            isError=False,
            structuredContent={
                "artifact_name": "output.pdf",
                "relative_path": "output.pdf",
                "size_bytes": len(content),
                "sha256": digest,
                "format_id": "pdf",
                "media_type": "application/pdf",
                "warnings": ["fixture warning"],
            },
        )

    async def disconnect(self, session_id: str):
        self.disconnected += 1


def _pdf_spec() -> dict:
    return {
        "format_id": "pdf",
        "filename": "report.pdf",
        "title": "Report",
        "blocks": [
            {"kind": "heading", "text": "Summary", "level": 1},
            {"kind": "paragraph", "text": "Bounded content."},
        ],
    }


def test_isolated_renderer_uses_opaque_workspace_verifies_hash_and_cleans(
    tmp_path: Path,
) -> None:
    input_root = tmp_path / "inputs"
    output_root = tmp_path / "outputs"
    manager = _FakeManager(output_root)
    sidecar = OutputRenderSidecar(
        input_root=input_root,
        output_root=output_root,
        manager_factory=lambda: manager,
    )
    rendered = FileOutputRenderer(sidecar=sidecar).render(_pdf_spec())
    assert rendered.content.startswith(b"%PDF-")
    assert rendered.warnings == ("fixture warning",)
    assert manager.connected == manager.called == manager.disconnected == 1
    assert list(input_root.iterdir()) == []
    assert list(output_root.iterdir()) == []


def test_isolated_renderer_rejects_tampered_artifact_and_still_cleans(
    tmp_path: Path,
) -> None:
    input_root = tmp_path / "inputs"
    output_root = tmp_path / "outputs"
    manager = _FakeManager(output_root, tamper_digest=True)
    renderer = FileOutputRenderer(
        sidecar=OutputRenderSidecar(
            input_root=input_root,
            output_root=output_root,
            manager_factory=lambda: manager,
        )
    )
    with pytest.raises(OutputRenderError) as error:
        renderer.render(_pdf_spec())
    assert error.value.error_code == "output_renderer_integrity_failed"
    assert list(input_root.iterdir()) == []
    assert list(output_root.iterdir()) == []


def test_isolated_renderer_has_one_absolute_deadline_and_no_retry(
    tmp_path: Path,
) -> None:
    input_root = tmp_path / "inputs"
    output_root = tmp_path / "outputs"
    manager = _FakeManager(output_root, delay=0.03)
    renderer = FileOutputRenderer(
        sidecar=OutputRenderSidecar(
            input_root=input_root,
            output_root=output_root,
            manager_factory=lambda: manager,
            operation_timeout=0.05,
        )
    )
    with pytest.raises(OutputRenderError) as error:
        renderer.render(_pdf_spec())
    assert error.value.error_code == "output_renderer_timeout"
    assert manager.connected == 1
    assert manager.called == 1
    assert list(input_root.iterdir()) == []
    assert list(output_root.iterdir()) == []
