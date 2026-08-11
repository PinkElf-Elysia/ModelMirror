from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from server.sandbox_sidecar.file_mcp import (
    OUTPUT_RENDER_MARKER_NAME,
    OUTPUT_RENDER_MARKER_OWNER,
    OutputRenderDocumentError,
    WorkspaceContext,
    render_output_document_payload,
)


def _stage(root: Path, spec: dict) -> tuple[WorkspaceContext, Path]:
    workspace_id = "mcpws_" + "a" * 32
    input_base = root / "inputs"
    output_base = root / "outputs"
    memory_base = root / "memory"
    input_root = input_base / workspace_id
    input_root.mkdir(parents=True)
    raw = json.dumps(
        spec, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")
    source = input_root / "spec.json"
    source.write_bytes(raw)
    marker = {
        "owner": OUTPUT_RENDER_MARKER_OWNER,
        "workspace_id": workspace_id,
        "source_name": "spec.json",
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "format_id": spec["format_id"],
    }
    (input_root / OUTPUT_RENDER_MARKER_NAME).write_text(
        json.dumps(marker, separators=(",", ":")), encoding="utf-8"
    )
    environment = {
        "MCP_FILE_WORKSPACE_ID": workspace_id,
        "MCP_FILE_INPUT_ROOT": str(input_base),
        "MCP_FILE_OUTPUT_ROOT": str(output_base),
        "MCP_FILE_MEMORY_ROOT": str(memory_base),
    }
    with patch.dict(os.environ, environment):
        context = WorkspaceContext()
    return context, source


def _blocks() -> list[dict]:
    return [
        {"kind": "heading", "text": "安全报告", "level": 1, "items": [], "rows": []},
        {"kind": "paragraph", "text": "ModelMirror isolated output renderer.", "level": 1, "items": [], "rows": []},
        {"kind": "list", "text": None, "level": 1, "items": ["first", "second"], "rows": []},
        {"kind": "table", "text": None, "level": 1, "items": [], "rows": [["name", "value"], ["safe", "yes"]]},
    ]


class OutputRendererSidecarTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_generates_safe_local_files(self) -> None:
        cases = [
            ("pdf", ".pdf", {"blocks": _blocks()}),
            ("docx", ".docx", {"blocks": _blocks()}),
            (
                "xlsx",
                ".xlsx",
                {"sheets": [{"name": "Data", "rows": [["value", "formula"], [-3.5, "=2+2"]]}]},
            ),
            ("pptx", ".pptx", {"slides": [{"title": "Slide 1", "blocks": _blocks()}]}),
        ]
        for format_id, suffix, extra in cases:
            with self.subTest(format_id=format_id):
                case_root = self.root / format_id
                spec = {
                    "format_id": format_id,
                    "filename": "report" + suffix,
                    "title": "Output closure",
                    "content": None,
                    "rows": [],
                    "blocks": [],
                    "sheets": [],
                    "slides": [],
                    **extra,
                }
                context, source = _stage(case_root, spec)
                payload = render_output_document_payload(context, source)
                output = context.output_root / payload["relative_path"]
                self.assertTrue(output.is_file())
                self.assertEqual(output.suffix, suffix)
                self.assertLess(output.stat().st_size, 50 * 1024 * 1024)
                self.assertEqual(
                    payload["sha256"], hashlib.sha256(output.read_bytes()).hexdigest()
                )
                self.assertEqual(payload["format_id"], format_id)

                if format_id == "pdf":
                    self.assertTrue(output.read_bytes().startswith(b"%PDF-"))
                elif format_id == "xlsx":
                    from openpyxl import load_workbook

                    workbook = load_workbook(output, read_only=True, data_only=False)
                    self.assertEqual(workbook["Data"]["A2"].value, -3.5)
                    self.assertEqual(workbook["Data"]["B2"].value, "'=2+2")
                    self.assertNotEqual(workbook["Data"]["B2"].data_type, "f")
                    self.assertTrue(payload["warnings"])
                else:
                    with zipfile.ZipFile(output) as archive:
                        names = set(archive.namelist())
                        self.assertFalse(any("vbaProject" in name for name in names))
                        for name in (item for item in names if item.endswith(".rels")):
                            self.assertNotIn(b'TargetMode="External"', archive.read(name))

    def test_rejects_marker_and_source_tampering(self) -> None:
        spec = {
            "format_id": "docx",
            "filename": "report.docx",
            "title": "Output",
            "content": None,
            "rows": [],
            "blocks": _blocks(),
            "sheets": [],
            "slides": [],
        }
        context, source = _stage(self.root / "tamper", spec)
        source.write_text("{}", encoding="utf-8")
        with self.assertRaises(OutputRenderDocumentError) as error:
            render_output_document_payload(context, source)
        self.assertEqual(error.exception.code, "output_render_handoff_invalid")
        self.assertEqual(list(context.output_root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
