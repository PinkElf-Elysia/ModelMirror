"""4C falsification tests: independent geometric fixtures, no provider access."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from server.file_assets import document_parser as shared_parser
from server.rag.document_parser import DocumentParseError
from server.rag.document_processor import StructuredDocumentProcessor


FIXTURES = Path(__file__).parent / "fixtures" / "rag_parser_v2"
PARSER = "canonical-structured-parser-v2"
RECEIPT = "rag-document-transform-receipt-v1"


@pytest.fixture(autouse=True)
def _no_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    async def forbidden(*args, **kwargs):
        raise AssertionError("4C tests must not dispatch a real Provider")

    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "request", forbidden)
    monkeypatch.setattr(httpx.Client, "request", lambda *a, **kw: pytest.fail("External HTTP forbidden"))


def process(name: str, **kwargs):
    return StructuredDocumentProcessor().process(
        FIXTURES / name, filename=name, source_id="fixture-source", **kwargs
    )


def receipt(document):
    value = document.payload(include_text=True)
    assert value["parser_contract_version"] == PARSER
    result = value["processing_receipt"]
    assert result["receipt_version"] == RECEIPT
    assert result["parser_contract_version"] == PARSER
    assert result["source_id"] == document.source_id
    assert result["block_count"] == len(document.blocks)
    assert len(result["structure_hash"]) == 64
    return result


def test_pdf_dispatch_uses_shared_killable_worker(monkeypatch):
    calls = []

    def fake_worker(path, *, title, **kwargs):
        calls.append(kwargs)
        return shared_parser.ParsedDocument(
            format="pdf", title=title,
            sections=(shared_parser.ParsedSection(text="Shared worker body", page=1),),
            extracted_chars=18,
        )

    import pdfplumber
    monkeypatch.setattr(shared_parser, "_parse_text_pdf", fake_worker)
    monkeypatch.setattr(pdfplumber, "open", lambda *a, **k: pytest.fail("RAG opened PDF in parent process"))
    document = process("page_edges.pdf")
    assert len(calls) == 1
    assert calls[0]["structured"] is True
    assert "Shared worker body" in document.text


def test_markdown_structure_and_offsets_have_replayable_receipt():
    document = process("structured.md")
    code = next(b for b in document.blocks if b.kind == "code")
    table = next(b for b in document.blocks if b.kind == "table")
    assert code.heading_path == ["Harbor Manual", "Operations", "Approval"]
    assert code.text == '```python\ndef approve(code):\n    return code == "HARBOR-42"\n```'
    assert "| Owner | Review team |" in table.text
    assert all(document.text[b.start_char:b.end_char] == b.text for b in document.blocks)
    assert receipt(document)["status"] == "complete"
    assert document.payload(include_text=True) == process("structured.md").payload(include_text=True)


def test_repeated_margins_have_hashed_operations_without_plaintext():
    document = process("page_edges.pdf")
    assert {b.page_number for b in document.blocks} == {1, 2, 3}
    assert "INTERNAL HEADER 42" not in document.text
    assert "PRIVATE FOOTER 42" not in document.text
    result = receipt(document)
    assert result["layout_status"] == "verified"
    operations = result["operations"]
    removed = [item for item in operations if item["code"] == "remove_repeated_page_edge"]
    assert {item["line_hash"] for item in removed} == {
        hashlib.sha256(line.encode()).hexdigest()
        for line in ("INTERNAL HEADER 42", "PRIVATE FOOTER 42")
    }
    assert all(item["pages"] == [1, 2, 3] and item["count"] == 3 for item in removed)
    assert "INTERNAL HEADER" not in json.dumps(result)
    assert "PRIVATE FOOTER" not in json.dumps(result)


def test_header_removal_disabled_preserves_content_and_receipt():
    document = process("page_edges.pdf", config={"remove_repeated_headers_footers": False})
    assert "INTERNAL HEADER 42" in document.text
    assert receipt(document)["operations"] == []


def test_two_columns_are_column_major_not_interleaved():
    document = process("two_columns.pdf")
    assert document.text.index("Left 4") < document.text.index("Right 1")
    assert len(document.blocks) == 2
    assert [b.metadata["column"] for b in document.blocks] == [1, 2]
    assert all(b.page_number == 1 for b in document.blocks)
    assert receipt(document)["layout_status"] == "verified"


def test_cross_column_object_cannot_claim_verified_layout():
    document = process("ambiguous_columns.pdf")
    result = receipt(document)
    assert result["status"] == "degraded"
    assert result["layout_status"] == "degraded"
    assert "rag_layout_degraded" in result["degradation_reasons"]
    assert document.payload()["error_code"] == "rag_layout_degraded"


def test_provable_ruled_table_preserves_page_and_cell_range():
    document = process("ruled_table.pdf")
    table = next(b for b in document.blocks if b.kind == "table")
    assert table.page_number == 1
    assert table.metadata["row_range"] == "A1:B3"
    assert "Harbor" in table.text and "42" in table.text
    assert table.metadata["table_bbox"] == [60.0, 92.0, 460.0, 212.0]
    assert receipt(document)["status"] == "complete"


def test_scan_without_ocr_is_explicit_failure():
    with pytest.raises(DocumentParseError) as caught:
        process("scanned.pdf")
    assert caught.value.error_code == "scanned_pdf_requires_ocr"


def test_scan_with_fake_vision_preserves_page_and_processing_receipt():
    document = process("scanned.pdf", extra_blocks=[{
        "block_id": "fake-vision-page-1", "kind": "image_description",
        "text": "Fake Vision: Harbor scanned record.", "page_number": 1,
        "metadata": {"visual_kind": "page_ocr"},
    }])
    assert [b.page_number for b in document.blocks] == [1]
    result = receipt(document)
    assert result["processed_pages"] == [1]
    assert any(item["code"] == "vision_page_text" for item in result["operations"])


def test_xlsx_retains_sheet_coordinate_values_and_empty_cells():
    document = process("multiple_sheets.xlsx")
    assert [b.metadata["sheet"] for b in document.blocks] == ["Inventory", "History"]
    inventory = document.blocks[0]
    assert inventory.metadata["row_range"] == "A1:C4"
    assert "B2: 42" in inventory.text and "B4: 0" in inventory.text
    assert "B3:" not in inventory.text and "C2:" not in inventory.text
    assert "C3: Awaiting review" in inventory.text
    assert receipt(document)["status"] == "complete"


def test_underreported_xlsx_dimension_cannot_hide_rows(tmp_path):
    from server.tests.test_file_asset_xlsx import _rewrite_zip_member
    source = tmp_path / "underreported.xlsx"
    source.write_bytes((FIXTURES / "multiple_sheets.xlsx").read_bytes())
    _rewrite_zip_member(source, "xl/worksheets/sheet1.xml", lambda content: content.replace(
        b'<x:sheetViews>', b'<x:dimension ref="A1:A1"/><x:sheetViews>', 1
    ))
    document = StructuredDocumentProcessor().process(source, filename=source.name, source_id="dimension-attack")
    assert "B2: 42" in document.text and "B4: 0" in document.text
    assert document.blocks[0].metadata["row_range"] == "A1:C4"


@pytest.mark.parametrize("field,value", [("degradation_reasons", [[]]), ("processed_pages", [{}])])
def test_malformed_receipt_projection_fails_closed_without_exception(field, value):
    from server.rag.processing_receipt import safe_processing_receipt
    value_to_check = receipt(process("structured.md"))
    value_to_check[field] = value
    assert safe_processing_receipt(value_to_check) == {}


@pytest.mark.parametrize("name", ["empty.txt", "corrupt.pdf"])
def test_empty_or_corrupt_file_never_becomes_empty_success(name):
    with pytest.raises(DocumentParseError) as caught:
        process(name)
    assert caught.value.error_code


def test_partial_pdf_page_failure_does_not_return_success(monkeypatch):
    # Actual geometry is covered by PDFs above; this injects a page extraction
    # failure inside the shared worker to test the error boundary, not layout.
    class BadPage:
        def extract_words(self, **kwargs):
            raise ValueError("private broken page text")

    class FakePdf:
        # The first page succeeds. Failure of the next must not leak a partial
        # payload or turn a two-page source into a one-page success.
        from types import SimpleNamespace
        pages = [SimpleNamespace(width=612, height=792, images=[], find_tables=lambda **k: [],
                                 extract_words=lambda **k: [{"text": "good page", "x0": 60, "x1": 120, "top": 100, "bottom": 112}]),
                 BadPage()]
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    class Sender:
        messages = []
        def send(self, value):
            self.messages.append(value)
        def close(self):
            pass

    import pdfplumber
    monkeypatch.setattr(pdfplumber, "open", lambda *a, **k: FakePdf())
    monkeypatch.setattr(shared_parser, "_apply_pdf_worker_resource_limits", lambda *a: None)
    sender = Sender()
    shared_parser._pdf_text_worker("fixture.pdf", sender, 100000, 500000, 10, structured=True)
    assert sender.messages[0][0] == "error"
    assert sender.messages[0][1]["code"] == "pdf_parse_failed"
    assert "private broken" not in json.dumps(sender.messages)
    assert len(sender.messages) == 1


def test_golden_fixture_manifest_matches_frozen_project_owned_files():
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["external_documents"] is False and manifest["real_provider_calls"] == 0
    assert len(manifest["fixtures"]) == 9
    for entry in manifest["fixtures"]:
        content = (FIXTURES / entry["path"]).read_bytes()
        if Path(entry["path"]).suffix in {".md", ".txt"}:
            content = content.replace(b"\r\n", b"\n")
        assert hashlib.sha256(content).hexdigest() == entry["sha256"], entry["path"]


def test_overlapping_text_bands_are_not_verified_as_single_column():
    from server.file_assets.pdf_layout import _columns
    words = [
        {"text": "first", "x0": 60, "x1": 120, "top": 100, "bottom": 115},
        {"text": "second", "x0": 60, "x1": 125, "top": 105, "bottom": 120},
    ]
    _, degraded = _columns(words)
    assert degraded


def test_repeated_table_header_at_page_edge_is_not_claimed_as_removed():
    from types import SimpleNamespace
    from server.file_assets.pdf_layout import structured_pdf_payload
    cells = [(60, 10, 160, 35), (160, 10, 260, 35), (60, 35, 160, 60), (160, 35, 260, 60)]
    table = SimpleNamespace(bbox=(60, 10, 260, 60), cells=cells, extract=lambda: [["Item", "Count"], ["Harbor", "42"]])
    words = [
        {"text": text, "x0": x, "x1": x + 45, "top": y, "bottom": y + 10}
        for text, x, y in [("Item", 65, 15), ("Count", 165, 15), ("Harbor", 65, 40), ("42", 165, 40)]
    ]
    page = SimpleNamespace(width=612, height=792, images=[], extract_words=lambda **k: words,
                           find_tables=lambda **k: [table])
    result = structured_pdf_payload(SimpleNamespace(pages=[page, page]), max_page_characters=1000, max_total_characters=2000)
    assert result["operations"] == []
    assert len(result["sections"]) == 2


def test_rendered_page_budget_counts_all_table_sections_together():
    from types import SimpleNamespace
    from server.file_assets.pdf_layout import PdfOutputLimit, structured_pdf_payload
    tables, words = [], []
    for y in (100, 300):
        cells = [(60, y, 160, y + 25), (160, y, 260, y + 25),
                 (60, y + 25, 160, y + 50), (160, y + 25, 260, y + 50)]
        tables.append(SimpleNamespace(bbox=(60, y, 260, y + 50), cells=cells, extract=lambda: [["A", "B"], ["C", "D"]]))
        words.extend({"text": text, "x0": x, "x1": x + 8, "top": yy, "bottom": yy + 10}
                     for text, x, yy in [("A", 65, y+5), ("B", 165, y+5), ("C", 65, y+30), ("D", 165, y+30)])
    page = SimpleNamespace(width=612, height=792, images=[], extract_words=lambda **k: words,
                           find_tables=lambda **k: tables)
    with pytest.raises(PdfOutputLimit):
        structured_pdf_payload(SimpleNamespace(pages=[page]), max_page_characters=45, max_total_characters=1000)
