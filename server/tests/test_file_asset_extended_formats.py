from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest

from server.file_assets import document_parser as parser_module
from server.file_assets import validation as validation_module
from server.file_assets.contracts import FileInputKind, FilePurpose
from server.file_assets.document_parser import parse_chat_document
from server.file_assets.registry import get_file_format_registry
from server.file_assets.validation import FileUploadValidator, FileValidationError
from server.rag.document_parser import parse_document, parse_document_structured
from server.rag.document_processor import StructuredDocumentProcessor


EXTENDED_SAMPLES = {
    "csv": ("records.csv", "name,formula\nAlice,=SUM(A1:A2)\n"),
    "tsv": ("records.tsv", "name\tvalue\nAlice\t42\n"),
    "json": ("payload.json", ' {\n  "名字": "模镜", "ok": true\n}\n'),
    "jsonl": ("events.jsonl", '{"id":1}\n{"id":2}\n'),
    "yaml": ("config.yaml", "service:\n  enabled: true\n"),
    "xml": ("data.xml", "<root><item id=\"1\">模镜</item></root>"),
    "html": (
        "page.html",
        "<h1>安全标题</h1><p>正文</p><script>secret()</script>"
        "<iframe src=\"https://example.invalid\">remote</iframe>",
    ),
    "srt": (
        "captions.srt",
        "1\n00:00:00,000 --> 00:00:01,200\n你好\n",
    ),
    "vtt": (
        "captions.vtt",
        "WEBVTT\n\n00:00.000 --> 00:01.500\nHello\n",
    ),
    "source_code": ("app.py", "  def greet():\n      return '你好'\n"),
    "configuration": ("app.toml", "[service]\nenabled = true\n"),
    "log": ("service.log", "2026-08-07 INFO started\n"),
}


@pytest.fixture(autouse=True)
def _enable_chat_files(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAT_FILE_INPUT_ENABLED", "true")
    monkeypatch.setenv("FILE_ASSET_STORE_MODE", "shadow")


def _validate(filename: str, content: str) -> str:
    result = FileUploadValidator().validate_stream(
        BytesIO(content.encode("utf-8")),
        purpose=FilePurpose.CHAT,
        input_kind=FileInputKind.DOCUMENT,
        filename=filename,
        declared_media_type=None,
    )
    return result.format_id


def _expect_error(filename: str, content: str, code: str) -> None:
    with pytest.raises(FileValidationError) as captured:
        _validate(filename, content)
    assert captured.value.error_code == code
    assert captured.value.status_code == 422


@pytest.mark.parametrize(
    ("format_id", "sample"),
    tuple(EXTENDED_SAMPLES.items()),
)
def test_every_extended_ready_format_has_validator_and_parser(
    tmp_path: Path,
    format_id: str,
    sample: tuple[str, str],
) -> None:
    filename, content = sample
    assert _validate(filename, content) == format_id
    path = tmp_path / filename
    path.write_text(content, encoding="utf-8")

    parsed = parse_chat_document(path, format_id=format_id, title=filename)

    assert parsed.format == format_id
    assert parsed.sections
    assert parsed.extracted_chars == len(content)
    assert parsed.truncated is False


def test_registry_exposes_extended_formats_to_chat_rag_agent_and_workflow() -> None:
    registry = get_file_format_registry()
    expected = set(EXTENDED_SAMPLES)
    for purpose in (
        FilePurpose.CHAT,
        FilePurpose.RAG,
        FilePurpose.AGENT,
        FilePurpose.WORKFLOW,
    ):
        policy = next(
            item
            for item in registry.policies_for(purpose)
            if item.input_kind == FileInputKind.DOCUMENT
        )
        assert expected.issubset(policy.format_ids)

    chat = registry.capabilities_response(purpose=FilePurpose.CHAT).capabilities
    document = next(item for item in chat if item.input_kind == FileInputKind.DOCUMENT)
    extract = next(item for item in document.handling_options if item.handling.value == "extract")
    assert expected.issubset(extract.format_ids)


def test_shared_parsed_document_is_used_by_rag(tmp_path: Path) -> None:
    path = tmp_path / "payload.json"
    source = '{\n  "message": "来自共享解析器"\n}\n'
    path.write_text(source, encoding="utf-8")

    parsed = parse_document_structured(path, "payload.json")

    assert parsed.format == "json"
    assert parsed.sections[0].text == source
    assert parse_document(path, "payload.json") == source


def test_source_and_structured_text_preserve_layout_and_unicode(
    tmp_path: Path,
) -> None:
    source = "  const 名称 = '模镜';\n\n"
    path = tmp_path / "app.ts"
    path.write_text(source, encoding="utf-8")

    parsed = parse_chat_document(path, format_id="source_code", title="app.ts")

    assert parsed.sections[0].text == source
    assert parsed.sections[0].line_range == "1-2"

    processed = StructuredDocumentProcessor().process(
        path,
        filename="app.ts",
        source_id="source_layout",
    )
    assert processed.text == source
    assert processed.blocks[0].text == source


def test_delimited_preview_keeps_row_source_and_formula_as_inert_text(
    tmp_path: Path,
) -> None:
    path = tmp_path / "formula.csv"
    path.write_text("name,value\nrow,=1+1\n", encoding="utf-8")

    parsed = parse_chat_document(path, format_id="csv", title="formula.csv")

    assert parsed.sections[0].row_range == "1-2"
    assert "=1+1" in parsed.sections[0].text


def test_html_only_extracts_safe_text_and_heading_sources(tmp_path: Path) -> None:
    filename, source = EXTENDED_SAMPLES["html"]
    path = tmp_path / filename
    path.write_text(source, encoding="utf-8")

    parsed = parse_chat_document(path, format_id="html", title=filename)
    output = "\n".join(section.text for section in parsed.sections)

    assert "安全标题" in output
    assert "正文" in output
    assert "secret" not in output
    assert "remote" not in output
    assert "example.invalid" not in output
    assert any(section.heading_path == ("安全标题",) for section in parsed.sections)
    assert parsed.warnings


@pytest.mark.parametrize("format_id", ("srt", "vtt"))
def test_subtitle_preview_preserves_timeline_source(
    tmp_path: Path,
    format_id: str,
) -> None:
    filename, source = EXTENDED_SAMPLES[format_id]
    path = tmp_path / filename
    path.write_text(source, encoding="utf-8")

    parsed = parse_chat_document(path, format_id=format_id, title=filename)

    assert parsed.sections[0].time_range
    assert "-->" in parsed.sections[0].time_range
    assert parsed.sections[0].line_range


@pytest.mark.parametrize(
    ("format_id", "filename", "source", "expected_ranges"),
    (
        (
            "srt",
            "spaced.srt",
            "1\n00:00:00,000 --> 00:00:01,000\nFirst\n\n\n\n"
            "2\n00:00:02,000 --> 00:00:03,000\nSecond\n",
            ("1-3", "7-9"),
        ),
        (
            "vtt",
            "spaced.vtt",
            "WEBVTT\n\n\n\n00:00.000 --> 00:01.000\nFirst\n\n\n"
            "00:02.000 --> 00:03.000\nSecond\n",
            ("5-6", "9-10"),
        ),
    ),
)
def test_subtitle_line_ranges_use_exact_offsets_across_multiple_blank_lines(
    tmp_path: Path,
    format_id: str,
    filename: str,
    source: str,
    expected_ranges: tuple[str, str],
) -> None:
    path = tmp_path / filename
    path.write_text(source, encoding="utf-8")

    parsed = parse_chat_document(path, format_id=format_id, title=filename)

    assert tuple(section.line_range for section in parsed.sections) == expected_ranges
    assert all(section.time_range and "-->" in section.time_range for section in parsed.sections)


@pytest.mark.parametrize(
    ("filename", "content", "code"),
    (
        ("bad.json", '{"value": NaN}', "non_finite_json_number"),
        ("bad.jsonl", '{"ok":1}\n{"bad":}\n', "invalid_jsonl"),
        ("bad.yaml", "value: !unsafe payload\n", "yaml_custom_tag_not_allowed"),
        ("duplicate.yaml", "key: 1\nkey: 2\n", "yaml_duplicate_key"),
        ("bad.xml", "<!DOCTYPE root><root />", "xml_dtd_or_entity_not_allowed"),
        (
            "include.xml",
            '<root xmlns:xi="http://www.w3.org/2001/XInclude"><xi:include href="x"/></root>',
            "xml_xinclude_not_allowed",
        ),
        ("bad.srt", "1\n00:61:00,000 --> 00:62:00,000\nBad\n", "invalid_srt"),
        ("bad.vtt", "WEBVTT\n\n00:02.000 --> 00:01.000\nBad\n", "invalid_vtt"),
    ),
)
def test_structured_damage_returns_stable_errors(
    filename: str,
    content: str,
    code: str,
) -> None:
    _expect_error(filename, content, code)


def test_resource_bombs_are_rejected_before_tree_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(validation_module, "MAX_STRUCTURED_DEPTH", 4)
    _expect_error("deep.json", '[[[[[0]]]]]', "structured_content_too_complex")
    _expect_error("deep.xml", "<a><b><c><d><e/></d></c></b></a>", "structured_content_too_complex")
    _expect_error("deep.html", "<a><b><c><d><e>x</e></d></c></b></a>", "structured_content_too_complex")

    monkeypatch.setattr(validation_module, "MAX_YAML_DEPTH", 3)
    _expect_error("deep.yaml", "[[[[0]]]]", "structured_content_too_complex")

    monkeypatch.setattr(validation_module, "MAX_DELIMITED_COLUMNS", 3)
    _expect_error("wide.csv", "a,b,c,d\n1,2,3,4\n", "delimited_column_limit_exceeded")


def test_yaml_alias_budget_is_checked_before_composition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(validation_module, "MAX_YAML_ALIASES", 2)
    _expect_error(
        "aliases.yaml",
        "base: &base [1]\na: *base\nb: *base\nc: *base\n",
        "yaml_alias_limit_exceeded",
    )


def test_output_is_bounded_without_unicode_normalization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(parser_module, "MAX_EXTRACTED_CHARACTERS", 12)
    source = "  e\u0301 = '值'\nsecond line\n"
    path = tmp_path / "code.py"
    path.write_text(source, encoding="utf-8")

    parsed = parse_chat_document(path, format_id="source_code", title="code.py")

    assert parsed.truncated is True
    assert parsed.extracted_chars == len(source)
    assert parsed.sections[0].text == source[:12]
    assert "e\u0301" in parsed.sections[0].text
    assert parsed.warnings
