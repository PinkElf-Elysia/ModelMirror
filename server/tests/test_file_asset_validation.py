from __future__ import annotations

import io
import struct
import time
import zipfile
from pathlib import Path

import duckdb
import pytest
from openpyxl import Workbook
from PyPDF2 import PdfWriter
from PyPDF2.generic import (
    ArrayObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    TextStringObject,
)

import server.file_assets.validation as validation_module
from server.file_assets.validation import (
    FileUploadValidator,
    FileValidationError,
)


CONTENT_TYPES = b"""<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Override PartName="/xl/workbook.xml"
    ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
</Types>"""
WORKBOOK = b"""<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"/>"""


def _slow_pdf_validation_worker(
    _content: bytes,
    _max_pages: int,
    sender,
    _timeout_seconds: float,
) -> None:
    try:
        time.sleep(1)
    finally:
        sender.close()


def _expect_error(
    validator: FileUploadValidator,
    content: bytes,
    *,
    purpose: str,
    input_kind: str,
    filename: str,
    media_type: str,
    code: str,
    status: int,
) -> None:
    with pytest.raises(FileValidationError) as caught:
        validator.validate_stream(
            io.BytesIO(content),
            purpose=purpose,
            input_kind=input_kind,
            filename=filename,
            declared_media_type=media_type,
        )
    assert caught.value.error_code == code
    assert caught.value.status_code == status
    assert str(caught.value) == caught.value.message


def _pdf(*, pages: int = 1, password: str | None = None) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=100, height=100)
    if password:
        writer.encrypt(password)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _pdf_with_unsafe_feature(feature: str) -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    root = writer._root_object
    javascript_action = DictionaryObject(
        {
            NameObject("/S"): NameObject("/JavaScript"),
            NameObject("/JS"): TextStringObject("app.alert('unsafe')"),
        }
    )
    dangerous_action_types = {
        "action_launch": "/Launch",
        "action_submit_form": "/SubmitForm",
        "action_import_data": "/ImportData",
        "action_goto_embedded": "/GoToE",
        "action_rendition": "/Rendition",
        "action_rich_media": "/RichMediaExecute",
    }
    if feature == "embedded_files":
        root[NameObject("/Names")] = DictionaryObject(
            {
                NameObject("/EmbeddedFiles"): DictionaryObject(
                    {NameObject("/Names"): ArrayObject()}
                )
            }
        )
    elif feature == "javascript_names":
        root[NameObject("/Names")] = DictionaryObject(
            {
                NameObject("/JavaScript"): DictionaryObject(
                    {NameObject("/Names"): ArrayObject()}
                )
            }
        )
    elif feature == "open_action":
        root[NameObject("/OpenAction")] = javascript_action
    elif feature == "additional_action":
        root[NameObject("/AA")] = DictionaryObject(
            {NameObject("/WC"): javascript_action}
        )
    elif feature in {"acroform", "xfa"}:
        form = DictionaryObject({NameObject("/Fields"): ArrayObject()})
        if feature == "xfa":
            form[NameObject("/XFA")] = TextStringObject("unsafe-xfa")
        root[NameObject("/AcroForm")] = form
    elif feature == "file_attachment":
        annotation = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Annot"),
                NameObject("/Subtype"): NameObject("/FileAttachment"),
                NameObject("/Rect"): ArrayObject(
                    [NumberObject(0), NumberObject(0), NumberObject(10), NumberObject(10)]
                ),
            }
        )
        writer.pages[0][NameObject("/Annots")] = ArrayObject(
            [writer._add_object(annotation)]
        )
    elif feature == "page_additional_action":
        writer.pages[0][NameObject("/AA")] = DictionaryObject(
            {NameObject("/O"): javascript_action}
        )
    elif feature == "root_associated_file":
        root[NameObject("/AF")] = ArrayObject()
    elif feature == "page_associated_file":
        writer.pages[0][NameObject("/AF")] = ArrayObject()
    elif feature in {"annotation_associated_file", "annotation_action_chain"}:
        annotation = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Annot"),
                NameObject("/Subtype"): NameObject("/Text"),
                NameObject("/Rect"): ArrayObject(
                    [NumberObject(0), NumberObject(0), NumberObject(10), NumberObject(10)]
                ),
            }
        )
        if feature == "annotation_associated_file":
            annotation[NameObject("/AF")] = ArrayObject()
        else:
            annotation[NameObject("/A")] = DictionaryObject(
                {
                    NameObject("/S"): NameObject("/URI"),
                    NameObject("/URI"): TextStringObject("https://example.invalid"),
                    NameObject("/Next"): javascript_action,
                }
            )
        writer.pages[0][NameObject("/Annots")] = ArrayObject(
            [writer._add_object(annotation)]
        )
    elif feature in dangerous_action_types or feature in {
        "dangerous_action_chain",
        "safe_uri",
        "safe_goto",
    }:
        action_type = dangerous_action_types.get(feature)
        if feature == "safe_uri" or feature == "dangerous_action_chain":
            action = DictionaryObject(
                {
                    NameObject("/S"): NameObject("/URI"),
                    NameObject("/URI"): TextStringObject("https://example.invalid"),
                }
            )
        elif feature == "safe_goto":
            action = DictionaryObject({NameObject("/S"): NameObject("/GoTo")})
        else:
            action = DictionaryObject(
                {NameObject("/S"): NameObject(str(action_type))}
            )
        if feature == "dangerous_action_chain":
            action[NameObject("/Next")] = DictionaryObject(
                {NameObject("/S"): NameObject("/Launch")}
            )
        annotation = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Annot"),
                NameObject("/Subtype"): NameObject("/Link"),
                NameObject("/Rect"): ArrayObject(
                    [NumberObject(0), NumberObject(0), NumberObject(10), NumberObject(10)]
                ),
                NameObject("/A"): action,
            }
        )
        writer.pages[0][NameObject("/Annots")] = ArrayObject(
            [writer._add_object(annotation)]
        )
    elif feature == "additional_launch":
        root[NameObject("/AA")] = DictionaryObject(
            {
                NameObject("/WC"): DictionaryObject(
                    {NameObject("/S"): NameObject("/Launch")}
                )
            }
        )
    else:  # pragma: no cover - test fixture guard
        raise ValueError(feature)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _xlsx(
    *,
    extras: dict[str, bytes] | None = None,
    content_types: bytes = CONTENT_TYPES,
) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("xl/workbook.xml", WORKBOOK)
        for name, content in (extras or {}).items():
            archive.writestr(name, content)
    return output.getvalue()


def _valid_xlsx() -> bytes:
    workbook = Workbook()
    workbook.active.append(["id", "name"])
    workbook.active.append([1, "Alice"])
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def _mark_first_zip_member_encrypted(content: bytes) -> bytes:
    data = bytearray(content)
    local = data.find(b"PK\x03\x04")
    central = data.find(b"PK\x01\x02")
    assert local >= 0 and central >= 0
    local_flags = struct.unpack_from("<H", data, local + 6)[0] | 0x1
    central_flags = struct.unpack_from("<H", data, central + 8)[0] | 0x1
    struct.pack_into("<H", data, local + 6, local_flags)
    struct.pack_into("<H", data, central + 8, central_flags)
    return bytes(data)


def _parquet(
    tmp_path: Path,
    *,
    filename: str = "valid.parquet",
    select_sql: str = "SELECT 1::INTEGER AS id, 'Alice'::VARCHAR AS name",
    copy_options: str = "",
) -> bytes:
    path = tmp_path / filename
    connection = duckdb.connect(database=":memory:")
    try:
        connection.execute(
            f"COPY ({select_sql}) TO ? (FORMAT PARQUET{copy_options})",
            [str(path)],
        )
    finally:
        connection.close()
    return path.read_bytes()


@pytest.mark.parametrize(
    ("purpose", "kind", "filename", "media_type", "content", "format_id"),
    [
        ("rag", "document", "notes.txt", "text/plain", b"hello\nworld", "plain_text"),
        ("agent", "document", "README.md", "text/markdown", b"# Hello", "markdown"),
        ("datax", "data_source", "facts.csv", "text/csv", b"id,name\n1,Alice\n", "csv"),
        (
            "datax",
            "data_source",
            "facts.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            _valid_xlsx(),
            "xlsx",
        ),
    ],
)
def test_ready_formats_validate_without_loading_contract_drift(
    purpose: str,
    kind: str,
    filename: str,
    media_type: str,
    content: bytes,
    format_id: str,
) -> None:
    stream = io.BytesIO(content)
    stream.seek(min(2, len(content)))
    original_position = stream.tell()
    result = FileUploadValidator().validate_stream(
        stream,
        purpose=purpose,
        input_kind=kind,
        filename=filename,
        declared_media_type=media_type,
    )
    assert result.format_id == format_id
    assert result.byte_size == len(content)
    assert stream.tell() == original_position


def test_pdf_path_validation_accepts_a_real_unencrypted_pdf(tmp_path: Path) -> None:
    path = tmp_path / "stored-private-blob"
    path.write_bytes(_pdf())
    result = FileUploadValidator().validate_path(
        path,
        purpose="rag",
        input_kind="document",
        filename="guide.pdf",
        declared_media_type="application/octet-stream",
    )
    assert result.format_id == "pdf"
    assert result.media_type == "application/pdf"


def test_real_parquet_validates_from_stream_and_path(tmp_path: Path) -> None:
    content = _parquet(tmp_path)
    validator = FileUploadValidator()
    stream_result = validator.validate_stream(
        io.BytesIO(content),
        purpose="datax",
        input_kind="data_source",
        filename="facts.parquet",
        declared_media_type="application/octet-stream",
    )
    assert stream_result.format_id == "parquet"
    assert stream_result.byte_size == len(content)

    stored = tmp_path / "stored-private-blob"
    stored.write_bytes(content)
    path_result = validator.validate_path(
        stored,
        purpose="datax",
        input_kind="data_source",
        filename="facts.parquet",
        declared_media_type="application/vnd.apache.parquet",
    )
    assert path_result.format_id == "parquet"


@pytest.mark.parametrize(
    ("purpose", "kind", "filename", "media_type", "code"),
    [
        ("workflow", "document", "notes.txt", "text/plain", "file_input_not_ready"),
        ("rag", "image", "image.png", "image/png", "file_input_not_supported"),
        ("chat", "audio", "voice.mp3", "audio/mpeg", "file_input_not_supported"),
        ("chat", "video", "clip.mp4", "video/mp4", "file_input_not_supported"),
    ],
)
def test_batch_boundary_rejects_unwired_and_media_inputs(
    purpose: str, kind: str, filename: str, media_type: str, code: str
) -> None:
    _expect_error(
        FileUploadValidator(),
        b"safe-looking-body",
        purpose=purpose,
        input_kind=kind,
        filename=filename,
        media_type=media_type,
        code=code,
        status=422,
    )


def test_extension_and_mime_mismatch_return_415() -> None:
    validator = FileUploadValidator()
    _expect_error(
        validator,
        b"hello",
        purpose="rag",
        input_kind="document",
        filename="notes.exe",
        media_type="application/octet-stream",
        code="unsupported_file_format",
        status=415,
    )
    _expect_error(
        validator,
        b"hello",
        purpose="rag",
        input_kind="document",
        filename="notes.txt",
        media_type="application/pdf; charset=binary",
        code="mime_type_mismatch",
        status=415,
    )


@pytest.mark.parametrize("content", [b"hello\x00world", b"hello\x07world", b"hello\x7fworld"])
def test_text_formats_reject_nul_and_binary_control_bytes(content: bytes) -> None:
    _expect_error(
        FileUploadValidator(),
        content,
        purpose="rag",
        input_kind="document",
        filename="notes.txt",
        media_type="text/plain",
        code="binary_text_content",
        status=422,
    )


@pytest.mark.parametrize(
    ("purpose", "kind", "filename", "media_type"),
    [
        ("rag", "document", "notes.txt", "text/plain"),
        ("agent", "document", "README.md", "text/markdown"),
        ("datax", "data_source", "facts.csv", "text/csv"),
    ],
)
def test_text_formats_require_incremental_utf8_but_allow_bom(
    purpose: str, kind: str, filename: str, media_type: str
) -> None:
    accepted = FileUploadValidator().validate_stream(
        io.BytesIO(b"\xef\xbb\xbfhello,\xe4\xb8\x96\xe7\x95\x8c\n"),
        purpose=purpose,
        input_kind=kind,
        filename=filename,
        declared_media_type=media_type,
    )
    assert accepted.byte_size > 0

    _expect_error(
        FileUploadValidator(),
        b"valid-prefix\n\xff\xfe\x80binary-tail",
        purpose=purpose,
        input_kind=kind,
        filename=filename,
        media_type=media_type,
        code="invalid_text_encoding",
        status=422,
    )


def test_file_limit_is_checked_from_path_before_content_scan(tmp_path: Path) -> None:
    path = tmp_path / "oversized"
    with path.open("wb") as output:
        output.seek(10 * 1024 * 1024)
        output.write(b"x")
    with pytest.raises(FileValidationError) as caught:
        FileUploadValidator().validate_path(
            path,
            purpose="rag",
            input_kind="document",
            filename="notes.txt",
            declared_media_type="text/plain",
        )
    assert (caught.value.error_code, caught.value.status_code) == (
        "file_too_large",
        413,
    )


def test_pdf_signature_corruption_encryption_and_page_limit_are_distinct() -> None:
    validator = FileUploadValidator()
    _expect_error(
        validator,
        b"not a pdf",
        purpose="rag",
        input_kind="document",
        filename="guide.pdf",
        media_type="application/pdf",
        code="file_signature_mismatch",
        status=415,
    )
    _expect_error(
        validator,
        b"%PDF-corrupt",
        purpose="rag",
        input_kind="document",
        filename="guide.pdf",
        media_type="application/pdf",
        code="invalid_pdf",
        status=422,
    )
    _expect_error(
        validator,
        _pdf(password="secret"),
        purpose="agent",
        input_kind="document",
        filename="private.pdf",
        media_type="application/pdf",
        code="encrypted_pdf",
        status=422,
    )
    _expect_error(
        FileUploadValidator(max_pdf_pages=1),
        _pdf(pages=2),
        purpose="rag",
        input_kind="document",
        filename="long.pdf",
        media_type="application/pdf",
        code="pdf_page_limit_exceeded",
        status=422,
    )


def test_pdf_upload_validation_is_killable_on_resource_timeout() -> None:
    started = time.monotonic()
    with pytest.raises(FileValidationError) as failure:
        validation_module._validate_pdf_in_worker(
            _pdf(),
            max_pages=1_000,
            timeout_seconds=0.05,
            worker_target=_slow_pdf_validation_worker,
        )
    assert failure.value.error_code == "pdf_validation_resource_limit"
    assert time.monotonic() - started < 2


@pytest.mark.parametrize(
    ("feature", "code"),
    [
        ("embedded_files", "pdf_embedded_files_not_allowed"),
        ("javascript_names", "pdf_javascript_not_allowed"),
        ("open_action", "pdf_open_action_not_allowed"),
        ("additional_action", "pdf_javascript_not_allowed"),
        ("acroform", "pdf_form_not_allowed"),
        ("xfa", "pdf_form_not_allowed"),
        ("file_attachment", "pdf_file_attachment_not_allowed"),
        ("page_additional_action", "pdf_javascript_not_allowed"),
        ("root_associated_file", "pdf_associated_files_not_allowed"),
        ("page_associated_file", "pdf_associated_files_not_allowed"),
        ("annotation_associated_file", "pdf_associated_files_not_allowed"),
        ("annotation_action_chain", "pdf_javascript_not_allowed"),
        ("action_launch", "pdf_active_action_not_allowed"),
        ("action_submit_form", "pdf_active_action_not_allowed"),
        ("action_import_data", "pdf_active_action_not_allowed"),
        ("action_goto_embedded", "pdf_active_action_not_allowed"),
        ("action_rendition", "pdf_active_action_not_allowed"),
        ("action_rich_media", "pdf_active_action_not_allowed"),
        ("dangerous_action_chain", "pdf_active_action_not_allowed"),
        ("additional_launch", "pdf_active_action_not_allowed"),
    ],
)
def test_pdf_rejects_embedded_active_and_form_features(
    feature: str, code: str
) -> None:
    _expect_error(
        FileUploadValidator(),
        _pdf_with_unsafe_feature(feature),
        purpose="rag",
        input_kind="document",
        filename="unsafe.pdf",
        media_type="application/pdf",
        code=code,
        status=422,
    )


@pytest.mark.parametrize("feature", ["safe_uri", "safe_goto"])
def test_pdf_keeps_safe_navigation_actions(feature: str) -> None:
    result = FileUploadValidator().validate_stream(
        io.BytesIO(_pdf_with_unsafe_feature(feature)),
        purpose="rag",
        input_kind="document",
        filename="navigation.pdf",
        declared_media_type="application/pdf",
    )
    assert result.format_id == "pdf"


@pytest.mark.parametrize("content", [b"PAR1missing-footer", b"missing-headerPAR1", b"PAR1"])
def test_parquet_requires_both_magic_markers(content: bytes) -> None:
    _expect_error(
        FileUploadValidator(),
        content,
        purpose="datax",
        input_kind="data_source",
        filename="facts.parquet",
        media_type="application/vnd.apache.parquet",
        code="file_signature_mismatch",
        status=415,
    )


def test_parquet_rejects_fake_footer_and_truncated_metadata(tmp_path: Path) -> None:
    valid = _parquet(tmp_path)
    invalid_files = (
        b"PAR1bodyPAR1",
        b"PAR1x" + struct.pack("<I", 1) + b"PAR1",
        valid[:-9] + valid[-8:],
    )
    for content in invalid_files:
        _expect_error(
            FileUploadValidator(),
            content,
            purpose="datax",
            input_kind="data_source",
            filename="facts.parquet",
            media_type="application/vnd.apache.parquet",
            code="invalid_parquet",
            status=422,
        )


def test_parquet_resource_limits_use_small_real_metadata(tmp_path: Path) -> None:
    flat = _parquet(tmp_path, filename="flat.parquet")
    _expect_error(
        FileUploadValidator(max_parquet_fields=1),
        flat,
        purpose="datax",
        input_kind="data_source",
        filename="facts.parquet",
        media_type="application/vnd.apache.parquet",
        code="parquet_complexity_limit_exceeded",
        status=422,
    )

    nested = _parquet(
        tmp_path,
        filename="nested.parquet",
        select_sql="SELECT {'nested': {'value': 1::INTEGER}} AS payload",
    )
    _expect_error(
        FileUploadValidator(max_parquet_depth=2),
        nested,
        purpose="datax",
        input_kind="data_source",
        filename="nested.parquet",
        media_type="application/vnd.apache.parquet",
        code="parquet_complexity_limit_exceeded",
        status=422,
    )

    row_groups = _parquet(
        tmp_path,
        filename="row-groups.parquet",
        select_sql="SELECT range::INTEGER AS id FROM range(5000)",
        copy_options=", ROW_GROUP_SIZE 2048",
    )
    _expect_error(
        FileUploadValidator(max_parquet_row_groups=1),
        row_groups,
        purpose="datax",
        input_kind="data_source",
        filename="row-groups.parquet",
        media_type="application/vnd.apache.parquet",
        code="parquet_complexity_limit_exceeded",
        status=422,
    )


def test_parquet_metadata_timeout_is_stable_and_masked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = _parquet(tmp_path)

    class ImmediateTimer:
        daemon = False

        def __init__(self, _interval: float, callback) -> None:
            self.callback = callback

        def start(self) -> None:
            self.callback()

        def cancel(self) -> None:
            return None

        def join(self, timeout: float | None = None) -> None:
            del timeout

    monkeypatch.setattr(validation_module.threading, "Timer", ImmediateTimer)
    _expect_error(
        FileUploadValidator(parquet_metadata_timeout_seconds=0.1),
        content,
        purpose="datax",
        input_kind="data_source",
        filename="facts.parquet",
        media_type="application/vnd.apache.parquet",
        code="parquet_validation_timeout",
        status=422,
    )


def test_xlsx_rejects_bad_signature_and_missing_required_structure() -> None:
    _expect_error(
        FileUploadValidator(),
        b"not a zip",
        purpose="datax",
        input_kind="data_source",
        filename="facts.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        code="file_signature_mismatch",
        status=415,
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
    _expect_error(
        FileUploadValidator(),
        output.getvalue(),
        purpose="datax",
        input_kind="data_source",
        filename="facts.xlsx",
        media_type="application/octet-stream",
        code="invalid_xlsx",
        status=422,
    )


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (_xlsx(extras={"../escape.xml": b"x"}), "unsafe_xlsx_container"),
        (_xlsx(extras={"xl/vbaProject.bin": b"macro"}), "unsupported_xlsx_feature"),
        (_mark_first_zip_member_encrypted(_xlsx()), "encrypted_xlsx"),
        (
            _xlsx(
                extras={
                    "xl/_rels/workbook.xml.rels": b"""<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="r1" Type="x" Target="https://example.test/book.xlsx" TargetMode="External"/></Relationships>"""
                }
            ),
            "unsupported_xlsx_feature",
        ),
    ],
)
def test_xlsx_rejects_unsafe_container_features(content: bytes, code: str) -> None:
    _expect_error(
        FileUploadValidator(),
        content,
        purpose="datax",
        input_kind="data_source",
        filename="facts.xlsx",
        media_type="application/octet-stream",
        code=code,
        status=422,
    )


def test_xlsx_resource_limits_reject_entry_and_compression_bombs() -> None:
    many_entries = _xlsx(extras={f"xl/worksheets/sheet{i}.xml": b"x" for i in range(3)})
    _expect_error(
        FileUploadValidator(max_xlsx_entries=4),
        many_entries,
        purpose="datax",
        input_kind="data_source",
        filename="facts.xlsx",
        media_type="application/octet-stream",
        code="xlsx_complexity_limit_exceeded",
        status=422,
    )
    compressed = _xlsx(extras={"xl/worksheets/sheet1.xml": b"A" * 100_000})
    _expect_error(
        FileUploadValidator(max_xlsx_compression_ratio=2),
        compressed,
        purpose="datax",
        input_kind="data_source",
        filename="facts.xlsx",
        media_type="application/octet-stream",
        code="xlsx_complexity_limit_exceeded",
        status=422,
    )
