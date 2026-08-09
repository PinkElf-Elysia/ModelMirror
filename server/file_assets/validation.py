from __future__ import annotations

import codecs
import csv
import io
import json
import os
import re
import struct
import tempfile
import threading
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import BinaryIO
from xml.etree import ElementTree

import duckdb

from .contracts import FileInputKind, FileInteractionStatus, FilePurpose
from .registry import FileFormatRegistry, get_file_format_registry


MIB = 1024 * 1024
DEFAULT_MAX_PDF_PAGES = 1_000
DEFAULT_MAX_PARQUET_FIELDS = 2_000
DEFAULT_MAX_PARQUET_DEPTH = 64
DEFAULT_MAX_PARQUET_ROW_GROUPS = 10_000
DEFAULT_PARQUET_METADATA_TIMEOUT_SECONDS = 5.0
PARQUET_METADATA_MEMORY_LIMIT = "128MB"
DEFAULT_MAX_XLSX_ENTRIES = 10_000
DEFAULT_MAX_XLSX_MEMBER_BYTES = 100 * MIB
DEFAULT_MAX_XLSX_UNCOMPRESSED_BYTES = 250 * MIB
DEFAULT_MAX_XLSX_COMPRESSION_RATIO = 100
MAX_OOXML_XML_BYTES = 2 * MIB
_OOXML_MAIN_PARTS = {
    "docx": "word/document.xml",
    "pptx": "ppt/presentation.xml",
}
MAX_STRUCTURED_DEPTH = 64
MAX_STRUCTURED_NODES = 100_000
MAX_YAML_DEPTH = 50
MAX_YAML_ALIASES = 50
MAX_DELIMITED_COLUMNS = 200
MAX_DELIMITED_ROWS = 100_000
MAX_DELIMITED_CELLS = 1_000_000
MAX_DELIMITED_FIELD_CHARACTERS = 100_000
MAX_XML_ATTRIBUTES_PER_ELEMENT = 1_000
MAX_XML_TEXT_CHARACTERS = 500_000
MAX_SUBTITLE_CUES = 100_000
MAX_SUBTITLE_CUE_CHARACTERS = 100_000
_CHUNK_BYTES = 1024 * 1024

_ALLOWED_INPUTS = {
    (FilePurpose.CHAT, FileInputKind.DOCUMENT),
    (FilePurpose.RAG, FileInputKind.DOCUMENT),
    (FilePurpose.AGENT, FileInputKind.DOCUMENT),
    (FilePurpose.DATAX, FileInputKind.DATA_SOURCE),
    (FilePurpose.WORKFLOW, FileInputKind.DOCUMENT),
}
_TEXT_FORMATS = {
    "plain_text",
    "markdown",
    "csv",
    "tsv",
    "json",
    "jsonl",
    "yaml",
    "xml",
    "html",
    "srt",
    "vtt",
    "source_code",
    "configuration",
    "log",
}
_ALLOWED_ZIP_COMPRESSION = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
_BINARY_CONTROL_BYTES = frozenset(
    {*range(0x00, 0x09), 0x0B, *range(0x0E, 0x20), 0x7F}
)
_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]*$")


class FileValidationError(ValueError):
    """A stable, user-safe rejection from the local file boundary."""

    def __init__(self, error_code: str, status_code: int, message: str) -> None:
        if _SAFE_CODE.fullmatch(error_code) is None:
            raise ValueError("error_code must be a stable snake-case identifier")
        if status_code not in {413, 415, 422}:
            raise ValueError("file validation status must be 413, 415, or 422")
        super().__init__(message)
        self.error_code = error_code
        self.status_code = status_code
        self.message = message


@dataclass(frozen=True, slots=True)
class ValidatedFile:
    purpose: FilePurpose
    input_kind: FileInputKind
    format_id: str
    extension: str
    media_type: str
    byte_size: int


class FileUploadValidator:
    """Validate a seekable upload without materialising it in memory.

    Batch B deliberately accepts only the existing ready document/data-source
    surfaces. Images, audio and video remain owned by the multimodal endpoints.
    """

    def __init__(
        self,
        registry: FileFormatRegistry | None = None,
        *,
        max_pdf_pages: int = DEFAULT_MAX_PDF_PAGES,
        max_parquet_fields: int = DEFAULT_MAX_PARQUET_FIELDS,
        max_parquet_depth: int = DEFAULT_MAX_PARQUET_DEPTH,
        max_parquet_row_groups: int = DEFAULT_MAX_PARQUET_ROW_GROUPS,
        max_parquet_rows: int | None = None,
        max_parquet_columns: int | None = None,
        parquet_metadata_timeout_seconds: float = DEFAULT_PARQUET_METADATA_TIMEOUT_SECONDS,
        max_xlsx_entries: int = DEFAULT_MAX_XLSX_ENTRIES,
        max_xlsx_member_bytes: int = DEFAULT_MAX_XLSX_MEMBER_BYTES,
        max_xlsx_uncompressed_bytes: int = DEFAULT_MAX_XLSX_UNCOMPRESSED_BYTES,
        max_xlsx_compression_ratio: int = DEFAULT_MAX_XLSX_COMPRESSION_RATIO,
    ) -> None:
        self.registry = registry or get_file_format_registry()
        self.max_pdf_pages = _positive(max_pdf_pages, "max_pdf_pages")
        self.max_parquet_fields = _positive(
            max_parquet_fields, "max_parquet_fields"
        )
        self.max_parquet_depth = _positive(max_parquet_depth, "max_parquet_depth")
        self.max_parquet_row_groups = _positive(
            max_parquet_row_groups, "max_parquet_row_groups"
        )
        self.max_parquet_rows = (
            _positive(max_parquet_rows, "max_parquet_rows")
            if max_parquet_rows is not None
            else None
        )
        self.max_parquet_columns = (
            _positive(max_parquet_columns, "max_parquet_columns")
            if max_parquet_columns is not None
            else None
        )
        self.parquet_metadata_timeout_seconds = _positive_float(
            parquet_metadata_timeout_seconds,
            "parquet_metadata_timeout_seconds",
        )
        self.max_xlsx_entries = _positive(max_xlsx_entries, "max_xlsx_entries")
        self.max_xlsx_member_bytes = _positive(
            max_xlsx_member_bytes, "max_xlsx_member_bytes"
        )
        self.max_xlsx_uncompressed_bytes = _positive(
            max_xlsx_uncompressed_bytes, "max_xlsx_uncompressed_bytes"
        )
        self.max_xlsx_compression_ratio = _positive(
            max_xlsx_compression_ratio, "max_xlsx_compression_ratio"
        )

    def validate_path(
        self,
        path: str | Path,
        *,
        purpose: FilePurpose | str,
        input_kind: FileInputKind | str,
        filename: str,
        declared_media_type: str | None,
    ) -> ValidatedFile:
        source = Path(path)
        try:
            if source.is_symlink() or not source.is_file():
                raise OSError("not a regular file")
            with source.open("rb") as stream:
                return self.validate_stream(
                    stream,
                    purpose=purpose,
                    input_kind=input_kind,
                    filename=filename,
                    declared_media_type=declared_media_type,
                )
        except FileValidationError:
            raise
        except OSError as exc:
            raise _error(
                "file_unavailable",
                422,
                "文件无法安全读取，请重新选择后再试。",
            ) from exc

    def validate_stream(
        self,
        stream: BinaryIO,
        *,
        purpose: FilePurpose | str,
        input_kind: FileInputKind | str,
        filename: str,
        declared_media_type: str | None,
    ) -> ValidatedFile:
        clean_purpose, clean_kind, policy = self._policy(purpose, input_kind)
        extension = _safe_extension(filename)
        file_format = self.registry.by_extension(
            clean_purpose,
            clean_kind,
            extension,
            ready_only=False,
        )
        if file_format is None:
            raise _error(
                "unsupported_file_format",
                415,
                "该入口暂不支持此文件格式，请按页面提示选择文件。",
            )
        if file_format.interaction_status != FileInteractionStatus.READY:
            raise _error(
                "file_input_not_ready",
                422,
                file_format.status_reason
                or "该格式的安全解析链尚未完成，请稍后重试。",
            )
        media_type = _validated_media_type(declared_media_type, file_format.media_types)

        original_position = _stream_position(stream)
        try:
            byte_size = _stream_size(stream)
            if byte_size == 0:
                raise _error("empty_file", 422, "文件为空，请选择包含内容的文件。")
            if byte_size > policy.max_bytes_per_file:
                raise _error(
                    "file_too_large",
                    413,
                    f"文件超过当前入口的 {policy.max_bytes_per_file // MIB} MiB 上限。",
                )
            stream.seek(0)
            self._validate_content(file_format.format_id, stream, byte_size)
        finally:
            try:
                stream.seek(original_position)
            except (OSError, ValueError):
                pass

        return ValidatedFile(
            purpose=clean_purpose,
            input_kind=clean_kind,
            format_id=file_format.format_id,
            extension=extension,
            media_type=media_type,
            byte_size=byte_size,
        )

    def _policy(
        self,
        purpose: FilePurpose | str,
        input_kind: FileInputKind | str,
    ):
        try:
            clean_purpose = (
                purpose if isinstance(purpose, FilePurpose) else FilePurpose(purpose)
            )
            clean_kind = (
                input_kind
                if isinstance(input_kind, FileInputKind)
                else FileInputKind(input_kind)
            )
        except ValueError as exc:
            raise _error(
                "file_input_not_supported",
                422,
                "该模块暂不支持此类文件输入。",
            ) from exc
        if (clean_purpose, clean_kind) not in _ALLOWED_INPUTS:
            raise _error(
                "file_input_not_supported",
                422,
                "本批文件入口仅开放资料库、Data X 和智能体的既有安全格式。",
            )
        policy = next(
            (
                item
                for item in self.registry.policies_for(clean_purpose)
                if item.input_kind == clean_kind
            ),
            None,
        )
        readiness = next(
            (
                item
                for item in self.registry.capabilities_response(
                    purpose=clean_purpose
                ).capabilities
                if item.input_kind == clean_kind
            ),
            None,
        )
        if (
            policy is None
            or readiness is None
            or readiness.interaction_status != FileInteractionStatus.READY
        ):
            raise _error(
                "file_input_not_ready",
                422,
                "该模块的文件入口当前未启用，请稍后重试。",
            )
        return clean_purpose, clean_kind, policy

    def _validate_content(
        self, format_id: str, stream: BinaryIO, byte_size: int
    ) -> None:
        if format_id in _TEXT_FORMATS:
            _validate_text_bytes(stream)
            if format_id in {"csv", "tsv"}:
                _validate_delimited_text(
                    stream,
                    delimiter="," if format_id == "csv" else "\t",
                    label=format_id.upper(),
                )
            elif format_id == "json":
                _validate_json_text(stream)
            elif format_id == "jsonl":
                _validate_jsonl_text(stream)
            elif format_id == "yaml":
                _validate_yaml_text(stream)
            elif format_id == "xml":
                _validate_xml_text(stream)
            elif format_id == "html":
                _validate_html_text(stream)
            elif format_id in {"srt", "vtt"}:
                _validate_subtitle_text(stream, format_id=format_id)
            return
        if format_id == "pdf":
            self._validate_pdf(stream)
            return
        if format_id == "parquet":
            _validate_parquet(
                stream,
                byte_size,
                max_fields=self.max_parquet_fields,
                max_depth=self.max_parquet_depth,
                max_row_groups=self.max_parquet_row_groups,
                max_rows=self.max_parquet_rows,
                max_columns=self.max_parquet_columns,
                timeout_seconds=self.parquet_metadata_timeout_seconds,
            )
            return
        if format_id == "xlsx":
            self._validate_xlsx(stream)
            return
        if format_id in _OOXML_MAIN_PARTS:
            self._validate_office_ooxml(stream, format_id=format_id)
            return
        raise _error(
            "unsupported_file_format",
            415,
            "该文件格式尚未建立安全验证规则。",
        )

    def _validate_pdf(self, stream: BinaryIO) -> None:
        stream.seek(0)
        if stream.read(5) != b"%PDF-":
            raise _signature_mismatch("PDF")
        stream.seek(0)
        try:
            from PyPDF2 import PdfReader
            from PyPDF2.errors import PdfReadError

            reader = PdfReader(stream, strict=True)
            if reader.is_encrypted:
                raise _error(
                    "encrypted_pdf",
                    422,
                    "暂不支持加密 PDF，请先移除密码保护。",
                )
            page_count = len(reader.pages)
            if page_count < 1:
                raise _error("invalid_pdf", 422, "PDF 中没有可读取的页面。")
            if page_count > self.max_pdf_pages:
                raise _error(
                    "pdf_page_limit_exceeded",
                    422,
                    f"PDF 超过 {self.max_pdf_pages} 页，请拆分后上传。",
                )
            _validate_pdf_catalog(reader)
        except FileValidationError:
            raise
        except (PdfReadError, OSError, ValueError, TypeError, KeyError) as exc:
            raise _error(
                "invalid_pdf",
                422,
                "PDF 已损坏或结构无效，请重新导出后再试。",
            ) from exc
    def _validate_xlsx(self, stream: BinaryIO) -> None:
        stream.seek(0)
        if stream.read(4) != b"PK\x03\x04":
            raise _signature_mismatch("XLSX")
        stream.seek(0)
        try:
            with zipfile.ZipFile(stream) as archive:
                entries = archive.infolist()
                if not entries:
                    raise _error(
                        "invalid_xlsx", 422, "XLSX 容器为空或结构无效。"
                    )
                if len(entries) > self.max_xlsx_entries:
                    raise _xlsx_complexity()
                normalized: dict[str, zipfile.ZipInfo] = {}
                total_uncompressed = 0
                total_compressed = 0
                for entry in entries:
                    name = _safe_zip_member_name(entry.filename)
                    folded = name.casefold()
                    if folded in normalized:
                        raise _error(
                            "unsafe_xlsx_container",
                            422,
                            "XLSX 包含重名或不安全的内部路径。",
                        )
                    normalized[folded] = entry
                    if entry.flag_bits & 0x1:
                        raise _error(
                            "encrypted_xlsx",
                            422,
                            "暂不支持加密 XLSX，请先移除密码保护。",
                        )
                    mode = (entry.external_attr >> 16) & 0o170000
                    if mode not in {0, 0o040000, 0o100000}:
                        raise _error(
                            "unsafe_xlsx_container",
                            422,
                            "XLSX 包含符号链接或特殊文件。",
                        )
                    if entry.compress_type not in _ALLOWED_ZIP_COMPRESSION:
                        raise _error(
                            "unsafe_xlsx_container",
                            422,
                            "XLSX 使用了未允许的压缩方式。",
                        )
                    if entry.file_size > self.max_xlsx_member_bytes:
                        raise _xlsx_complexity()
                    total_uncompressed += max(0, entry.file_size)
                    total_compressed += max(0, entry.compress_size)

                if (
                    total_uncompressed > self.max_xlsx_uncompressed_bytes
                    or total_uncompressed
                    > max(1, total_compressed) * self.max_xlsx_compression_ratio
                ):
                    raise _xlsx_complexity()

                names = set(normalized)
                required = {"[content_types].xml", "xl/workbook.xml"}
                if not required.issubset(names):
                    raise _error(
                        "invalid_xlsx",
                        422,
                        "XLSX 缺少必要的工作簿结构，请重新导出后再试。",
                    )
                if any(_unsupported_xlsx_member(name) for name in names):
                    raise _error(
                        "unsupported_xlsx_feature",
                        422,
                        "XLSX 包含宏、ActiveX、OLE 或外部工作簿链接，当前不予处理。",
                    )
                corrupt = archive.testzip()
                if corrupt is not None:
                    raise _error(
                        "invalid_xlsx",
                        422,
                        "XLSX 内部文件校验失败，请重新导出后再试。",
                    )
                content_types = _read_zip_member(
                    archive, normalized["[content_types].xml"]
                )
                workbook = _read_zip_member(
                    archive, normalized["xl/workbook.xml"]
                )
                _require_xml_root(content_types, "Types")
                _require_xml_root(workbook, "workbook")
                if any(
                    token in content_types.lower()
                    for token in (b"macroenabled", b"activex", b"oleobject")
                ):
                    raise _error(
                        "unsupported_xlsx_feature",
                        422,
                        "XLSX 声明了宏、ActiveX 或 OLE 内容，当前不予处理。",
                    )
                for name, entry in normalized.items():
                    if name.endswith(".rels"):
                        _reject_external_relationships(
                            _read_zip_member(archive, entry)
                        )
                _validate_workbook_relationship_targets(
                    archive,
                    normalized,
                    workbook,
                )
        except FileValidationError:
            raise
        except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, EOFError) as exc:
            raise _error(
                "invalid_xlsx",
                422,
                "XLSX 已损坏或容器结构无效，请重新导出后再试。",
            ) from exc

    def _validate_office_ooxml(self, stream: BinaryIO, *, format_id: str) -> None:
        """Perform only bounded ZIP central-directory checks in the API process.

        No archive member is decompressed here.  XML, relationships, active
        fields, CRCs and content-type declarations are validated by the
        network-free Office sidecar before python-docx/python-pptx see them.
        """

        label = format_id.upper()
        invalid_code = f"invalid_{format_id}"
        unsafe_code = f"unsafe_{format_id}_container"
        unsupported_code = f"unsupported_{format_id}_feature"
        stream.seek(0)
        if stream.read(4) != b"PK\x03\x04":
            raise _signature_mismatch(label)
        stream.seek(0)
        try:
            with zipfile.ZipFile(stream) as archive:
                entries = archive.infolist()
                if not entries:
                    raise _error(
                        invalid_code,
                        422,
                        f"{label} 容器为空或结构无效。",
                    )
                if len(entries) > self.max_xlsx_entries:
                    raise _ooxml_complexity(format_id)

                normalized: dict[str, zipfile.ZipInfo] = {}
                total_uncompressed = 0
                total_compressed = 0
                for entry in entries:
                    name = _safe_ooxml_member_name(
                        entry.filename,
                        label=label,
                        error_code=unsafe_code,
                    )
                    folded = name.casefold()
                    if folded in normalized:
                        raise _error(
                            unsafe_code,
                            422,
                            f"{label} 包含重名或不安全的内部路径。",
                        )
                    normalized[folded] = entry
                    if entry.flag_bits & 0x1:
                        raise _error(
                            f"encrypted_{format_id}",
                            422,
                            f"暂不支持加密 {label}，请先移除密码保护。",
                        )
                    mode = (entry.external_attr >> 16) & 0o170000
                    if mode not in {0, 0o040000, 0o100000}:
                        raise _error(
                            unsafe_code,
                            422,
                            f"{label} 包含符号链接或特殊文件。",
                        )
                    if entry.compress_type not in _ALLOWED_ZIP_COMPRESSION:
                        raise _error(
                            unsafe_code,
                            422,
                            f"{label} 使用了未允许的压缩方式。",
                        )
                    if entry.file_size > self.max_xlsx_member_bytes:
                        raise _ooxml_complexity(format_id)
                    total_uncompressed += max(0, entry.file_size)
                    total_compressed += max(0, entry.compress_size)

                if (
                    total_uncompressed > self.max_xlsx_uncompressed_bytes
                    or total_uncompressed
                    > max(1, total_compressed) * self.max_xlsx_compression_ratio
                ):
                    raise _ooxml_complexity(format_id)

                main_part = _OOXML_MAIN_PARTS[format_id]
                required = {"[content_types].xml", main_part}
                if not required.issubset(normalized):
                    raise _error(
                        invalid_code,
                        422,
                        f"{label} 缺少必要的文档结构，请重新导出后再试。",
                    )
                if any(
                    _unsupported_office_ooxml_member(name)
                    for name in normalized
                ):
                    raise _error(
                        unsupported_code,
                        422,
                        f"{label} 包含宏、ActiveX、OLE 或嵌入对象，当前不予处理。",
                    )
        except FileValidationError:
            raise
        except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, EOFError) as exc:
            raise _error(
                invalid_code,
                422,
                f"{label} 已损坏或容器结构无效，请重新导出后再试。",
            ) from exc


def _validate_pdf_catalog(reader: object) -> None:
    """Reject active/embedded PDF features without opening content streams."""

    try:
        trailer = getattr(reader, "trailer")
        root = _pdf_dictionary(trailer.get("/Root"))
        names_value = root.get("/Names")
        if names_value is not None:
            names = _pdf_dictionary(names_value)
            if "/EmbeddedFiles" in names:
                raise _error(
                    "pdf_embedded_files_not_allowed",
                    422,
                    "PDF 包含附件。请移除嵌入文件并重新导出后上传。",
                )
            if "/JavaScript" in names:
                raise _error(
                    "pdf_javascript_not_allowed",
                    422,
                    "PDF 包含 JavaScript。请移除脚本并重新导出后上传。",
                )
        if "/OpenAction" in root:
            raise _error(
                "pdf_open_action_not_allowed",
                422,
                "PDF 包含打开时自动动作。请移除自动动作并重新导出后上传。",
            )
        if "/AF" in root:
            raise _error(
                "pdf_associated_files_not_allowed",
                422,
                "PDF 包含关联文件。请移除关联文件并重新导出后上传。",
            )
        _reject_pdf_action_risk(
            _pdf_additional_actions_risk(root.get("/AA")), location="PDF"
        )
        if "/AcroForm" in root:
            form = _pdf_dictionary(root.get("/AcroForm"))
            message = (
                "PDF 包含 XFA 表单。请转为不含表单的普通 PDF 后上传。"
                if "/XFA" in form
                else "PDF 包含交互式表单。请扁平化表单并重新导出后上传。"
            )
            raise _error("pdf_form_not_allowed", 422, message)

        for page in getattr(reader, "pages"):
            page_object = _pdf_dictionary(page)
            if "/AF" in page_object:
                raise _error(
                    "pdf_associated_files_not_allowed",
                    422,
                    "PDF 页面包含关联文件。请移除关联文件并重新导出后上传。",
                )
            _reject_pdf_action_risk(
                _pdf_additional_actions_risk(page_object.get("/AA")),
                location="PDF 页面",
            )
            annotations_value = page_object.get("/Annots")
            if annotations_value is None:
                continue
            annotations = _pdf_resolve(annotations_value)
            if not isinstance(annotations, (list, tuple)):
                raise ValueError("PDF annotations must be an array")
            for annotation_value in annotations:
                annotation = _pdf_dictionary(annotation_value)
                if "/AF" in annotation:
                    raise _error(
                        "pdf_associated_files_not_allowed",
                        422,
                        "PDF 批注包含关联文件。请移除关联文件并重新导出后上传。",
                    )
                if str(annotation.get("/Subtype") or "") == "/FileAttachment":
                    raise _error(
                        "pdf_file_attachment_not_allowed",
                        422,
                        "PDF 页面包含文件附件。请移除附件并重新导出后上传。",
                    )
                risk = _pdf_action_risk(annotation.get("/A")) or (
                    _pdf_additional_actions_risk(annotation.get("/AA"))
                )
                _reject_pdf_action_risk(risk, location="PDF 批注")
    except FileValidationError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError, RecursionError) as exc:
        raise _error(
            "invalid_pdf",
            422,
            "PDF 目录结构无效，请重新导出后再试。",
        ) from exc


def _pdf_resolve(value: object) -> object:
    current = value
    for _ in range(8):
        getter = getattr(current, "get_object", None)
        if not callable(getter):
            return current
        resolved = getter()
        if resolved is current:
            return current
        current = resolved
    raise ValueError("PDF indirect object depth exceeded")


def _pdf_dictionary(value: object) -> dict:
    resolved = _pdf_resolve(value)
    if not isinstance(resolved, dict):
        raise ValueError("PDF object is not a dictionary")
    return resolved


_PDF_DANGEROUS_ACTION_TYPES = frozenset(
    {
        "/Launch",
        "/SubmitForm",
        "/ImportData",
        "/GoToE",
        "/Rendition",
        "/RichMediaExecute",
    }
)


def _pdf_action_risk(value: object | None) -> str | None:
    if value is None:
        return None
    pending: list[tuple[object, int]] = [(value, 0)]
    visited: set[int] = set()
    inspected = 0
    while pending:
        candidate, depth = pending.pop()
        if depth > 8:
            raise ValueError("PDF action chain depth exceeded")
        action = _pdf_dictionary(candidate)
        identity = id(action)
        if identity in visited:
            continue
        visited.add(identity)
        inspected += 1
        if inspected > 32:
            raise ValueError("PDF action chain size exceeded")
        action_type = str(action.get("/S") or "")
        if action_type == "/JavaScript" or "/JS" in action:
            return "javascript"
        if action_type in _PDF_DANGEROUS_ACTION_TYPES:
            return "active"
        next_value = action.get("/Next")
        if next_value is None:
            continue
        resolved_next = _pdf_resolve(next_value)
        if isinstance(resolved_next, dict):
            pending.append((resolved_next, depth + 1))
        elif isinstance(resolved_next, (list, tuple)):
            pending.extend((item, depth + 1) for item in resolved_next)
        else:
            raise ValueError("PDF action /Next is invalid")
    return None


def _pdf_additional_actions_risk(value: object | None) -> str | None:
    if value is None:
        return None
    actions = _pdf_dictionary(value)
    for action in actions.values():
        risk = _pdf_action_risk(action)
        if risk is not None:
            return risk
    return None


def _reject_pdf_action_risk(risk: str | None, *, location: str) -> None:
    if risk == "javascript":
        raise _error(
            "pdf_javascript_not_allowed",
            422,
            f"{location}包含 JavaScript。请移除脚本并重新导出后上传。",
        )
    if risk == "active":
        raise _error(
            "pdf_active_action_not_allowed",
            422,
            f"{location}包含可能启动程序、提交数据或执行富媒体的主动动作。请移除该动作并重新导出后上传。",
        )


def _safe_extension(filename: str) -> str:
    clean = str(filename or "").strip()
    if (
        not clean
        or len(clean) > 255
        or "/" in clean
        or "\\" in clean
        or any(ord(character) < 32 for character in clean)
    ):
        raise _error("invalid_filename", 422, "文件名无效，请重新选择文件。")
    extension = Path(clean).suffix.lower()
    if not extension:
        raise _error(
            "unsupported_file_format", 415, "文件缺少可识别的扩展名。"
        )
    return extension


def _validated_media_type(
    declared_media_type: str | None, allowed_media_types: tuple[str, ...]
) -> str:
    declared = (
        str(declared_media_type or "application/octet-stream")
        .split(";", 1)[0]
        .strip()
        .lower()
        or "application/octet-stream"
    )
    if declared != "application/octet-stream" and declared not in allowed_media_types:
        raise _error(
            "mime_type_mismatch",
            415,
            "文件类型声明与扩展名不一致，请重新导出或选择正确文件。",
        )
    return allowed_media_types[0]


def _stream_position(stream: BinaryIO) -> int:
    try:
        if not stream.seekable():
            raise OSError("stream is not seekable")
        return int(stream.tell())
    except (AttributeError, OSError, ValueError, TypeError) as exc:
        raise _error(
            "file_stream_not_seekable",
            422,
            "文件流无法安全检查，请重新上传。",
        ) from exc


def _stream_size(stream: BinaryIO) -> int:
    try:
        stream.seek(0, 2)
        size = int(stream.tell())
        stream.seek(0)
        return size
    except (OSError, ValueError, TypeError) as exc:
        raise _error(
            "file_unavailable",
            422,
            "文件无法安全读取，请重新选择后再试。",
        ) from exc


def _validate_text_bytes(stream: BinaryIO) -> None:
    stream.seek(0)
    decoder = codecs.getincrementaldecoder("utf-8-sig")(errors="strict")
    try:
        while chunk := stream.read(_CHUNK_BYTES):
            if not isinstance(chunk, bytes):
                raise _error(
                    "file_stream_invalid", 422, "文件流格式无效，请重新上传。"
                )
            if any(value in _BINARY_CONTROL_BYTES for value in chunk):
                raise _error(
                    "binary_text_content",
                    422,
                    "文本文件包含 NUL 或二进制控制字符，请转换为纯文本后再试。",
                )
            decoder.decode(chunk, final=False)
        decoder.decode(b"", final=True)
    except UnicodeDecodeError as exc:
        raise _error(
            "invalid_text_encoding",
            422,
            "文本文件不是有效的 UTF-8 编码，请转换编码后再试。",
        ) from exc


def _read_validated_utf8(stream: BinaryIO) -> str:
    stream.seek(0)
    try:
        return stream.read().decode("utf-8-sig")
    except (AttributeError, UnicodeDecodeError) as exc:  # defensive: preflight ran first.
        raise _error(
            "invalid_text_encoding",
            422,
            "文本文件不是有效的 UTF-8 编码，请转换编码后再试。",
        ) from exc


def _validate_delimited_text(
    stream: BinaryIO,
    *,
    delimiter: str,
    label: str,
) -> None:
    text = _read_validated_utf8(stream)
    row_count = 0
    cell_count = 0
    has_content = False
    try:
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
        for row in reader:
            row_count += 1
            if row_count > MAX_DELIMITED_ROWS:
                raise _error(
                    "delimited_row_limit_exceeded",
                    422,
                    f"{label} 超过 {MAX_DELIMITED_ROWS:,} 行，请拆分后上传。",
                )
            if len(row) > MAX_DELIMITED_COLUMNS:
                raise _error(
                    "delimited_column_limit_exceeded",
                    422,
                    f"{label} 超过 {MAX_DELIMITED_COLUMNS} 列，请精简后上传。",
                )
            cell_count += len(row)
            if cell_count > MAX_DELIMITED_CELLS:
                raise _error(
                    "delimited_cell_limit_exceeded",
                    422,
                    f"{label} 单元格数量过多，请拆分后上传。",
                )
            if any(len(value) > MAX_DELIMITED_FIELD_CHARACTERS for value in row):
                raise _error(
                    "delimited_field_limit_exceeded",
                    422,
                    f"{label} 包含过长单元格，请精简后上传。",
                )
            has_content = has_content or any(value.strip() for value in row)
    except FileValidationError:
        raise
    except csv.Error as exc:
        raise _error(
            "invalid_delimited_text",
            422,
            f"{label} 结构无效，请检查引号、分隔符和换行后重试。",
        ) from exc
    if not has_content:
        raise _error(
            "empty_delimited_text",
            422,
            f"{label} 中没有可读取的数据。",
        )


def _validate_json_text(stream: BinaryIO) -> None:
    text = _read_validated_utf8(stream)
    _preflight_json_structure(text, label="JSON")
    try:
        value = json.loads(text, parse_constant=_reject_json_constant)
    except FileValidationError:
        raise
    except RecursionError as exc:
        raise _structured_complexity("JSON") from exc
    except json.JSONDecodeError as exc:
        raise _error(
            "invalid_json",
            422,
            f"JSON 结构无效（第 {exc.lineno} 行，第 {exc.colno} 列）。",
        ) from exc
    _bounded_python_structure(value, label="JSON", max_depth=MAX_STRUCTURED_DEPTH)


def _validate_jsonl_text(stream: BinaryIO) -> None:
    text = _read_validated_utf8(stream)
    total_nodes = 0
    records = 0
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        records += 1
        _preflight_json_structure(line, label="JSONL")
        try:
            value = json.loads(line, parse_constant=_reject_json_constant)
        except FileValidationError:
            raise
        except RecursionError as exc:
            raise _structured_complexity("JSONL") from exc
        except json.JSONDecodeError as exc:
            raise _error(
                "invalid_jsonl",
                422,
                f"JSONL 第 {line_number} 行结构无效（第 {exc.colno} 列）。",
            ) from exc
        total_nodes += _bounded_python_structure(
            value,
            label="JSONL",
            max_depth=MAX_STRUCTURED_DEPTH,
            max_nodes=MAX_STRUCTURED_NODES - total_nodes,
        )
    if records == 0:
        raise _error("empty_jsonl", 422, "JSONL 中没有可读取的记录。")


def _reject_json_constant(value: str) -> object:
    raise _error(
        "non_finite_json_number",
        422,
        f"JSON 不允许非有限数值 {value}，请改用标准 JSON 数值或 null。",
    )


def _preflight_json_structure(text: str, *, label: str) -> None:
    """Bound nesting and a conservative node estimate before json.loads."""

    depth = 0
    estimated_nodes = 1
    in_string = False
    escaped = False
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            estimated_nodes += 1
            if depth > MAX_STRUCTURED_DEPTH:
                raise _structured_complexity(label)
        elif character in "]}":
            depth = max(0, depth - 1)
        elif character in ",:":
            estimated_nodes += 1
        if estimated_nodes > MAX_STRUCTURED_NODES:
            raise _structured_complexity(label)


def _bounded_python_structure(
    value: object,
    *,
    label: str,
    max_depth: int,
    max_nodes: int = MAX_STRUCTURED_NODES,
) -> int:
    if max_nodes <= 0:
        raise _structured_complexity(label)
    count = 0
    stack: list[tuple[object, int, frozenset[int]]] = [(value, 1, frozenset())]
    while stack:
        current, depth, ancestors = stack.pop()
        if depth > max_depth:
            raise _structured_complexity(label)
        count += 1
        if count > max_nodes:
            raise _structured_complexity(label)
        if isinstance(current, dict):
            identity = id(current)
            if identity in ancestors:
                raise _structured_complexity(label)
            next_ancestors = ancestors | {identity}
            for key, child in current.items():
                stack.append((child, depth + 1, next_ancestors))
                stack.append((key, depth + 1, next_ancestors))
        elif isinstance(current, (list, tuple)):
            identity = id(current)
            if identity in ancestors:
                raise _structured_complexity(label)
            next_ancestors = ancestors | {identity}
            stack.extend((child, depth + 1, next_ancestors) for child in current)
    return count


def _validate_yaml_text(stream: BinaryIO) -> None:
    text = _read_validated_utf8(stream)
    try:
        import yaml

        aliases = 0
        for token in yaml.scan(text, Loader=yaml.SafeLoader):
            if isinstance(token, yaml.tokens.AliasToken):
                aliases += 1
                if aliases > MAX_YAML_ALIASES:
                    raise _error(
                        "yaml_alias_limit_exceeded",
                        422,
                        f"YAML 别名超过 {MAX_YAML_ALIASES} 个，请展开或拆分后上传。",
                    )
            if isinstance(token, yaml.tokens.TagToken):
                handle, suffix = token.value
                if handle != "!!" or suffix not in {
                    "str", "int", "float", "bool", "null", "seq", "map",
                    "timestamp", "binary", "set", "omap", "pairs",
                }:
                    raise _error(
                        "yaml_custom_tag_not_allowed",
                        422,
                        "YAML 包含自定义标签；为避免执行不可信类型，本入口不予解析。",
                    )
        _preflight_yaml_events(text, yaml_module=yaml)
        documents = tuple(yaml.compose_all(text, Loader=yaml.SafeLoader))
        if not documents or all(document is None for document in documents):
            raise _error("empty_yaml", 422, "YAML 中没有可读取的内容。")
        total_nodes = 0
        for document in documents:
            if document is None:
                continue
            total_nodes += _validate_yaml_node(
                document,
                yaml_module=yaml,
                depth=1,
                active=frozenset(),
                remaining_nodes=MAX_STRUCTURED_NODES - total_nodes,
            )
    except FileValidationError:
        raise
    except (RecursionError, yaml.YAMLError) as exc:
        if isinstance(exc, RecursionError):
            raise _structured_complexity("YAML") from exc
        mark = getattr(exc, "problem_mark", None)
        location = (
            f"（第 {mark.line + 1} 行，第 {mark.column + 1} 列）"
            if mark is not None
            else ""
        )
        raise _error("invalid_yaml", 422, f"YAML 结构无效{location}。") from exc


def _preflight_yaml_events(text: str, *, yaml_module: object) -> None:
    depth = 0
    nodes = 0
    events = getattr(yaml_module, "events")
    container_starts = (events.MappingStartEvent, events.SequenceStartEvent)
    container_ends = (events.MappingEndEvent, events.SequenceEndEvent)
    value_events = (events.ScalarEvent, events.AliasEvent)
    for event in yaml_module.parse(text, Loader=yaml_module.SafeLoader):
        if isinstance(event, container_starts):
            depth += 1
            nodes += 1
            if depth > MAX_YAML_DEPTH:
                raise _structured_complexity("YAML")
        elif isinstance(event, container_ends):
            depth = max(0, depth - 1)
        elif isinstance(event, value_events):
            nodes += 1
        if nodes > MAX_STRUCTURED_NODES:
            raise _structured_complexity("YAML")
        tag = getattr(event, "tag", None)
        if tag is not None and not str(tag).startswith("tag:yaml.org,2002:"):
            raise _error(
                "yaml_custom_tag_not_allowed",
                422,
                "YAML 包含自定义标签；为避免执行不可信类型，本入口不予解析。",
            )


def _validate_yaml_node(
    node: object,
    *,
    yaml_module: object,
    depth: int,
    active: frozenset[int],
    remaining_nodes: int,
) -> int:
    if depth > MAX_YAML_DEPTH or remaining_nodes <= 0:
        raise _structured_complexity("YAML")
    identity = id(node)
    if identity in active:
        raise _error(
            "yaml_recursive_alias_not_allowed",
            422,
            "YAML 包含递归别名，无法安全展开。",
        )
    tag = str(getattr(node, "tag", ""))
    if not tag.startswith("tag:yaml.org,2002:"):
        raise _error(
            "yaml_custom_tag_not_allowed",
            422,
            "YAML 包含自定义标签；为避免执行不可信类型，本入口不予解析。",
        )
    count = 1
    next_active = active | {identity}
    value = getattr(node, "value", None)
    mapping_type = getattr(getattr(yaml_module, "nodes"), "MappingNode")
    sequence_type = getattr(getattr(yaml_module, "nodes"), "SequenceNode")
    if isinstance(node, mapping_type):
        seen_keys: set[str] = set()
        for key_node, value_node in value:
            key_signature = yaml_module.serialize(key_node)
            if key_signature in seen_keys:
                raise _error(
                    "yaml_duplicate_key",
                    422,
                    "YAML 包含重复键，请明确保留其中一个值后重试。",
                )
            seen_keys.add(key_signature)
            for child in (key_node, value_node):
                child_count = _validate_yaml_node(
                    child,
                    yaml_module=yaml_module,
                    depth=depth + 1,
                    active=next_active,
                    remaining_nodes=remaining_nodes - count,
                )
                count += child_count
                if count > remaining_nodes:
                    raise _structured_complexity("YAML")
    elif isinstance(node, sequence_type):
        for child in value:
            child_count = _validate_yaml_node(
                child,
                yaml_module=yaml_module,
                depth=depth + 1,
                active=next_active,
                remaining_nodes=remaining_nodes - count,
            )
            count += child_count
            if count > remaining_nodes:
                raise _structured_complexity("YAML")
    return count


def _validate_xml_text(stream: BinaryIO) -> None:
    text = _read_validated_utf8(stream)
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", text, flags=re.IGNORECASE):
        raise _error(
            "xml_dtd_or_entity_not_allowed",
            422,
            "XML 不允许 DTD 或实体声明，请移除后重试。",
        )
    _preflight_xml_structure(text)
    try:
        from defusedxml import ElementTree as DefusedElementTree

        root = DefusedElementTree.fromstring(
            text,
            forbid_dtd=True,
            forbid_entities=True,
            forbid_external=True,
        )
    except Exception as exc:
        raise _error("invalid_xml", 422, "XML 结构无效或包含不安全实体。") from exc

    node_count = 0
    text_characters = 0
    stack: list[tuple[object, int]] = [(root, 1)]
    while stack:
        element, depth = stack.pop()
        if depth > MAX_STRUCTURED_DEPTH:
            raise _structured_complexity("XML")
        node_count += 1
        if node_count > MAX_STRUCTURED_NODES:
            raise _structured_complexity("XML")
        attributes = getattr(element, "attrib", {})
        if len(attributes) > MAX_XML_ATTRIBUTES_PER_ELEMENT:
            raise _structured_complexity("XML")
        tag = str(getattr(element, "tag", ""))
        if tag == "{http://www.w3.org/2001/XInclude}include":
            raise _error(
                "xml_xinclude_not_allowed",
                422,
                "XML 不允许 XInclude，请内联所需内容后重试。",
            )
        text_characters += len(str(getattr(element, "text", "") or ""))
        text_characters += len(str(getattr(element, "tail", "") or ""))
        text_characters += sum(len(str(key)) + len(str(value)) for key, value in attributes.items())
        if text_characters > MAX_XML_TEXT_CHARACTERS:
            raise _structured_complexity("XML")
        stack.extend((child, depth + 1) for child in list(element))


def _preflight_xml_structure(text: str) -> None:
    """Use expat callbacks to enforce budgets before constructing an XML tree."""

    from xml.parsers import expat

    parser = expat.ParserCreate()
    depth = 0
    nodes = 0
    text_characters = 0

    def start_element(_name: str, attributes: dict[str, str]) -> None:
        nonlocal depth, nodes, text_characters
        depth += 1
        nodes += 1
        if depth > MAX_STRUCTURED_DEPTH or nodes > MAX_STRUCTURED_NODES:
            raise _structured_complexity("XML")
        if len(attributes) > MAX_XML_ATTRIBUTES_PER_ELEMENT:
            raise _structured_complexity("XML")
        text_characters += sum(len(key) + len(value) for key, value in attributes.items())
        if text_characters > MAX_XML_TEXT_CHARACTERS:
            raise _structured_complexity("XML")

    def end_element(_name: str) -> None:
        nonlocal depth
        depth = max(0, depth - 1)

    def character_data(value: str) -> None:
        nonlocal text_characters
        text_characters += len(value)
        if text_characters > MAX_XML_TEXT_CHARACTERS:
            raise _structured_complexity("XML")

    parser.StartElementHandler = start_element
    parser.EndElementHandler = end_element
    parser.CharacterDataHandler = character_data
    parser.ExternalEntityRefHandler = lambda *_args: 0
    try:
        parser.Parse(text, True)
    except FileValidationError:
        raise
    except expat.ExpatError as exc:
        raise _error(
            "invalid_xml",
            422,
            f"XML 结构无效（第 {exc.lineno} 行，第 {exc.offset} 列）。",
        ) from exc


class _HTMLSafetyParser(HTMLParser):
    _VOID = {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.nodes = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.nodes += 1
        if self.nodes > MAX_STRUCTURED_NODES or len(attrs) > MAX_XML_ATTRIBUTES_PER_ELEMENT:
            raise _structured_complexity("HTML")
        clean = tag.lower()
        if clean not in self._VOID:
            self.stack.append(clean)
            if len(self.stack) > MAX_STRUCTURED_DEPTH:
                raise _structured_complexity("HTML")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.nodes += 1
        if self.nodes > MAX_STRUCTURED_NODES or len(attrs) > MAX_XML_ATTRIBUTES_PER_ELEMENT:
            raise _structured_complexity("HTML")

    def handle_endtag(self, tag: str) -> None:
        clean = tag.lower()
        if clean in self.stack:
            reverse_index = self.stack[::-1].index(clean)
            del self.stack[len(self.stack) - reverse_index - 1 :]


def _validate_html_text(stream: BinaryIO) -> None:
    text = _read_validated_utf8(stream)
    parser = _HTMLSafetyParser()
    try:
        parser.feed(text)
        parser.close()
    except FileValidationError:
        raise
    except Exception as exc:
        raise _error("invalid_html", 422, "HTML 结构无法安全读取，请修复后重试。") from exc


_SRT_TIMELINE = re.compile(
    r"^(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s+-->\s+"
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})(?:\s+.*)?$"
)
_VTT_TIMELINE = re.compile(
    r"^(?:(\d{2,}):)?(\d{2}):(\d{2})\.(\d{3})\s+-->\s+"
    r"(?:(\d{2,}):)?(\d{2}):(\d{2})\.(\d{3})(?:\s+.*)?$"
)


def _validate_subtitle_text(stream: BinaryIO, *, format_id: str) -> None:
    text = _read_validated_utf8(stream)
    lines = text.splitlines()
    if format_id == "vtt":
        first = next((line.strip() for line in lines if line.strip()), "")
        if not first.startswith("WEBVTT"):
            raise _error("invalid_vtt", 422, "VTT 必须以 WEBVTT 标头开始。")
    timeline = _VTT_TIMELINE if format_id == "vtt" else _SRT_TIMELINE
    cue_count = 0
    for block in re.split(r"\r?\n\s*\r?\n", text):
        if "-->" in block and len(block) > MAX_SUBTITLE_CUE_CHARACTERS:
            raise _error(
                "subtitle_cue_limit_exceeded",
                422,
                f"{format_id.upper()} 包含过长字幕段落，请拆分后上传。",
            )
    for line_number, line in enumerate(lines, start=1):
        if "-->" not in line:
            continue
        match = timeline.fullmatch(line.strip())
        if match is None:
            raise _error(
                f"invalid_{format_id}",
                422,
                f"{format_id.upper()} 第 {line_number} 行时间轴无效。",
            )
        values = tuple(int(value or 0) for value in match.groups())
        start = _subtitle_seconds(values[:4], vtt=format_id == "vtt")
        end = _subtitle_seconds(values[4:], vtt=format_id == "vtt")
        if start is None or end is None or end <= start:
            raise _error(
                f"invalid_{format_id}",
                422,
                f"{format_id.upper()} 第 {line_number} 行结束时间必须晚于开始时间。",
            )
        cue_count += 1
        if cue_count > MAX_SUBTITLE_CUES:
            raise _error(
                "subtitle_cue_limit_exceeded",
                422,
                f"{format_id.upper()} 字幕段落超过 {MAX_SUBTITLE_CUES:,} 条，请拆分后上传。",
            )
    if cue_count == 0:
        raise _error(
            f"invalid_{format_id}",
            422,
            f"{format_id.upper()} 中没有有效字幕时间轴。",
        )


def _subtitle_seconds(values: tuple[int, ...], *, vtt: bool) -> float | None:
    if vtt:
        hours, minutes, seconds, milliseconds = values
    else:
        hours, minutes, seconds, milliseconds = values
    if minutes > 59 or seconds > 59 or milliseconds > 999:
        return None
    return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000


def _structured_complexity(label: str) -> FileValidationError:
    return _error(
        "structured_content_too_complex",
        422,
        f"{label} 的层级或节点数量超过安全上限，请拆分后上传。",
    )


def _validate_parquet(
    stream: BinaryIO,
    byte_size: int,
    *,
    max_fields: int,
    max_depth: int,
    max_row_groups: int,
    max_rows: int | None,
    max_columns: int | None,
    timeout_seconds: float,
) -> None:
    if byte_size < 12:
        raise _signature_mismatch("Parquet")
    stream.seek(0)
    first = stream.read(4)
    stream.seek(-8, 2)
    footer = stream.read(8)
    if len(footer) != 8:
        raise _error(
            "invalid_parquet",
            422,
            "Parquet 文件尾部不完整，请重新导出后再试。",
        )
    last = footer[4:]
    if first != b"PAR1" or last != b"PAR1":
        raise _signature_mismatch("Parquet")

    metadata_bytes = struct.unpack("<I", footer[:4])[0]
    if metadata_bytes < 1 or metadata_bytes > byte_size - 12:
        raise _error(
            "invalid_parquet",
            422,
            "Parquet 元数据长度无效，文件可能已损坏或被截断。",
        )

    temporary_path: Path | None = None
    source_path = _regular_stream_path(stream)
    try:
        if source_path is None:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix="modelmirror-parquet-", suffix=".parquet"
            )
            temporary_path = Path(temporary_name)
            stream.seek(0)
            with os.fdopen(descriptor, "wb") as output:
                remaining = byte_size
                while remaining:
                    chunk = stream.read(min(_CHUNK_BYTES, remaining))
                    if not isinstance(chunk, bytes) or not chunk:
                        raise _error(
                            "invalid_parquet",
                            422,
                            "Parquet 文件读取不完整，请重新导出后再试。",
                        )
                    output.write(chunk)
                    remaining -= len(chunk)
            source_path = temporary_path

        _validate_parquet_metadata(
            source_path,
            max_fields=max_fields,
            max_depth=max_depth,
            max_row_groups=max_row_groups,
            max_rows=max_rows,
            max_columns=max_columns,
            timeout_seconds=timeout_seconds,
        )
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _regular_stream_path(stream: BinaryIO) -> Path | None:
    raw_name = getattr(stream, "name", None)
    if not isinstance(raw_name, (str, os.PathLike)):
        return None
    try:
        path = Path(raw_name)
        if path.is_symlink() or not path.is_file():
            return None
        return path
    except OSError:
        return None


def _validate_parquet_metadata(
    path: Path,
    *,
    max_fields: int,
    max_depth: int,
    max_row_groups: int,
    max_rows: int | None,
    max_columns: int | None,
    timeout_seconds: float,
) -> None:
    connection: duckdb.DuckDBPyConnection | None = None
    timer: threading.Timer | None = None
    timed_out = threading.Event()
    try:
        connection = duckdb.connect(database=":memory:")
        connection.execute("SET threads = 1")
        connection.execute(f"SET memory_limit = '{PARQUET_METADATA_MEMORY_LIMIT}'")

        def interrupt_query() -> None:
            timed_out.set()
            try:
                connection.interrupt()
            except duckdb.Error:
                pass

        timer = threading.Timer(timeout_seconds, interrupt_query)
        timer.daemon = True
        timer.start()
        if timed_out.is_set():
            raise _parquet_timeout()

        file_rows = connection.execute(
            "SELECT num_rows, num_row_groups FROM parquet_file_metadata(?)",
            [str(path)],
        ).fetchmany(2)
        if timed_out.is_set():
            raise _parquet_timeout()
        schema_rows = connection.execute(
            "SELECT num_children FROM parquet_schema(?) LIMIT ?",
            [str(path), max_fields + 2],
        ).fetchall()
        if timed_out.is_set():
            raise _parquet_timeout()
    except FileValidationError:
        raise
    except (duckdb.Error, OSError, OverflowError, TypeError, ValueError) as exc:
        if timed_out.is_set():
            raise _parquet_timeout() from exc
        raise _error(
            "invalid_parquet",
            422,
            "Parquet 元数据或结构无效，请重新导出后再试。",
        ) from exc
    finally:
        if timer is not None:
            timer.cancel()
            timer.join()
        if connection is not None:
            try:
                connection.close()
            except duckdb.Error:
                pass

    if timed_out.is_set():
        raise _parquet_timeout()
    if len(file_rows) != 1:
        raise _error(
            "invalid_parquet",
            422,
            "Parquet 文件级元数据无效，请重新导出后再试。",
        )
    row_count = int(file_rows[0][0])
    row_group_count = int(file_rows[0][1])
    if row_count < 0:
        raise _error(
            "invalid_parquet",
            422,
            "Parquet 行数元数据无效，请重新导出后再试。",
        )
    if max_rows is not None and row_count > max_rows:
        raise _error(
            "parquet_row_limit_exceeded",
            422,
            f"Parquet 超过 {max_rows:,} 行上限，请拆分后上传。",
        )
    if row_group_count < 0:
        raise _error(
            "invalid_parquet",
            422,
            "Parquet Row Group 元数据无效，请重新导出后再试。",
        )
    if row_group_count > max_row_groups:
        raise _parquet_complexity()

    if not schema_rows:
        raise _error(
            "invalid_parquet",
            422,
            "Parquet 中没有可读取的结构定义，请重新导出后再试。",
        )
    top_level_columns = int(schema_rows[0][0] or 0)
    if top_level_columns < 0:
        raise _error(
            "invalid_parquet",
            422,
            "Parquet 顶层列数元数据无效，请重新导出后再试。",
        )
    if max_columns is not None and top_level_columns > max_columns:
        raise _error(
            "parquet_column_limit_exceeded",
            422,
            f"Parquet 超过 {max_columns} 列上限，请拆分或精简后上传。",
        )
    field_count = max(0, len(schema_rows) - 1)
    if field_count > max_fields:
        raise _parquet_complexity()
    if _parquet_schema_depth(schema_rows) > max_depth:
        raise _parquet_complexity()


def _parquet_schema_depth(schema_rows: list[tuple[object, ...]]) -> int:
    remaining_children: list[int] = []
    maximum_depth = 0
    for index, row in enumerate(schema_rows):
        while remaining_children and remaining_children[-1] == 0:
            remaining_children.pop()
        if index > 0 and not remaining_children:
            raise _error(
                "invalid_parquet",
                422,
                "Parquet Schema 层级结构无效，请重新导出后再试。",
            )
        depth = len(remaining_children)
        maximum_depth = max(maximum_depth, depth)
        if remaining_children:
            remaining_children[-1] -= 1
        child_count = int(row[0] or 0)
        if child_count < 0:
            raise _error(
                "invalid_parquet",
                422,
                "Parquet Schema 子节点数量无效，请重新导出后再试。",
            )
        if child_count:
            remaining_children.append(child_count)

    while remaining_children and remaining_children[-1] == 0:
        remaining_children.pop()
    if remaining_children:
        raise _error(
            "invalid_parquet",
            422,
            "Parquet Schema 结构不完整，请重新导出后再试。",
        )
    return maximum_depth


def _parquet_complexity() -> FileValidationError:
    return _error(
        "parquet_complexity_limit_exceeded",
        422,
        "Parquet 元数据结构超过安全上限，请拆分或简化数据文件后再试。",
    )


def _parquet_timeout() -> FileValidationError:
    return _error(
        "parquet_validation_timeout",
        422,
        "Parquet 元数据校验超时，请拆分文件或重新导出后再试。",
    )


def _safe_ooxml_member_name(
    value: str,
    *,
    label: str,
    error_code: str,
) -> str:
    clean = str(value or "")
    if (
        not clean
        or len(clean) > 512
        or "\\" in clean
        or "\x00" in clean
        or clean.startswith("/")
        or ":" in clean.split("/", 1)[0]
    ):
        raise _error(
            error_code, 422, f"{label} 包含不安全的内部路径。"
        )
    path = PurePosixPath(clean)
    if any(part in {"", ".", ".."} or len(part) > 160 for part in path.parts):
        raise _error(
            error_code, 422, f"{label} 包含不安全的内部路径。"
        )
    return path.as_posix()


def _safe_zip_member_name(value: str) -> str:
    return _safe_ooxml_member_name(
        value,
        label="XLSX",
        error_code="unsafe_xlsx_container",
    )


def _unsupported_xlsx_member(name: str) -> bool:
    clean = name.casefold()
    return (
        clean.endswith("vbaproject.bin")
        or clean.startswith("xl/activex/")
        or clean.startswith("xl/embeddings/")
        or clean.startswith("xl/externallinks/")
    )


def _unsupported_office_ooxml_member(name: str) -> bool:
    clean = name.casefold()
    allowed_printer_settings = bool(
        re.fullmatch(r"ppt/printersettings/printersettings[0-9]+\.bin", clean)
    )
    return (
        clean.endswith("vbaproject.bin")
        or clean.endswith("vbadata.xml")
        or "/activex/" in f"/{clean}"
        or "/embeddings/" in f"/{clean}"
        or "/oleobjects/" in f"/{clean}"
        or (clean.endswith(".bin") and not allowed_printer_settings)
    )


def _read_zip_member(archive: zipfile.ZipFile, entry: zipfile.ZipInfo) -> bytes:
    if entry.file_size > MAX_OOXML_XML_BYTES:
        raise _xlsx_complexity()
    with archive.open(entry) as stream:
        content = stream.read(MAX_OOXML_XML_BYTES + 1)
    if len(content) > MAX_OOXML_XML_BYTES:
        raise _xlsx_complexity()
    return content


def _require_xml_root(content: bytes, expected_local_name: str) -> None:
    if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise _error(
            "unsafe_xlsx_container",
            422,
            "XLSX 包含不安全的 XML 声明。",
        )
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise _error(
            "invalid_xlsx", 422, "XLSX 内部 XML 结构无效。"
        ) from exc
    local_name = root.tag.rsplit("}", 1)[-1]
    if local_name != expected_local_name:
        raise _error("invalid_xlsx", 422, "XLSX 内部结构与格式不一致。")


def _reject_external_relationships(content: bytes) -> None:
    if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise _error(
            "unsafe_xlsx_container",
            422,
            "XLSX 包含不安全的 XML 声明。",
        )
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise _error(
            "invalid_xlsx", 422, "XLSX 关系文件结构无效。"
        ) from exc
    for element in root.iter():
        for key, value in element.attrib.items():
            if key.rsplit("}", 1)[-1].casefold() == "targetmode" and str(
                value
            ).casefold() == "external":
                raise _error(
                    "unsupported_xlsx_feature",
                    422,
                    "XLSX 包含外部关系，当前不予处理。",
                )


def _validate_workbook_relationship_targets(
    archive: zipfile.ZipFile,
    normalized: dict[str, zipfile.ZipInfo],
    workbook_content: bytes,
) -> None:
    relationship_name = "xl/_rels/workbook.xml.rels"
    relationship_entry = normalized.get(relationship_name)
    if relationship_entry is None:
        raise _error(
            "invalid_xlsx",
            422,
            "XLSX 缺少工作簿关系定义，请重新导出后再试。",
        )
    relationship_content = _read_zip_member(archive, relationship_entry)
    if b"<!DOCTYPE" in relationship_content.upper() or b"<!ENTITY" in relationship_content.upper():
        raise _error(
            "unsafe_xlsx_container",
            422,
            "XLSX 包含不安全的 XML 声明。",
        )
    try:
        relationship_root = ElementTree.fromstring(relationship_content)
        workbook_root = ElementTree.fromstring(workbook_content)
    except ElementTree.ParseError as exc:
        raise _error(
            "invalid_xlsx",
            422,
            "XLSX 工作簿关系结构无效。",
        ) from exc

    relationships: dict[str, str] = {}
    available_names = set(normalized)
    for element in relationship_root.iter():
        if element.tag.rsplit("}", 1)[-1] != "Relationship":
            continue
        relationship_id = str(element.attrib.get("Id") or "").strip()
        target = str(element.attrib.get("Target") or "").strip()
        target_mode = str(element.attrib.get("TargetMode") or "").strip().casefold()
        if target_mode == "external":
            raise _error(
                "unsupported_xlsx_feature",
                422,
                "XLSX 包含外部关系，当前不予处理。",
            )
        if not relationship_id or not target or relationship_id in relationships:
            raise _error(
                "invalid_xlsx",
                422,
                "XLSX 工作簿关系定义不完整或重复。",
            )
        resolved = _resolve_ooxml_target("xl/workbook.xml", target)
        if resolved.casefold() not in available_names:
            raise _error(
                "invalid_xlsx",
                422,
                "XLSX 引用了缺失的内部工作簿内容，请重新导出后再试。",
            )
        relationships[relationship_id] = resolved

    sheets = [
        element
        for element in workbook_root.iter()
        if element.tag.rsplit("}", 1)[-1] == "sheet"
    ]
    if not sheets:
        raise _error(
            "invalid_xlsx",
            422,
            "XLSX 中没有可读取的工作表定义。",
        )
    for sheet in sheets:
        relationship_id = next(
            (
                str(value).strip()
                for key, value in sheet.attrib.items()
                if key.rsplit("}", 1)[-1].casefold() == "id"
            ),
            "",
        )
        if not relationship_id or relationship_id not in relationships:
            raise _error(
                "invalid_xlsx",
                422,
                "XLSX 工作表关系缺失，请重新导出后再试。",
            )


def _resolve_ooxml_target(source_part: str, target: str) -> str:
    clean = str(target or "").strip()
    if (
        not clean
        or "\\" in clean
        or "\x00" in clean
        or "?" in clean
        or "#" in clean
        or ":" in clean.split("/", 1)[0]
    ):
        raise _error(
            "unsafe_xlsx_container",
            422,
            "XLSX 包含不安全的内部关系目标。",
        )
    parts = [] if clean.startswith("/") else list(PurePosixPath(source_part).parent.parts)
    for part in PurePosixPath(clean.lstrip("/")).parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                raise _error(
                    "unsafe_xlsx_container",
                    422,
                    "XLSX 内部关系目标越过了容器根目录。",
                )
            parts.pop()
            continue
        if len(part) > 160:
            raise _error(
                "unsafe_xlsx_container",
                422,
                "XLSX 内部关系目标名称过长。",
            )
        parts.append(part)
    if not parts:
        raise _error(
            "invalid_xlsx",
            422,
            "XLSX 内部关系目标为空。",
        )
    return PurePosixPath(*parts).as_posix()


def _signature_mismatch(label: str) -> FileValidationError:
    return _error(
        "file_signature_mismatch",
        415,
        f"文件内容与 {label} 格式不一致，请重新导出后再试。",
    )


def _xlsx_complexity() -> FileValidationError:
    return _error(
        "xlsx_complexity_limit_exceeded",
        422,
        "XLSX 内部结构超过安全上限，请拆分工作簿后再试。",
    )


def _ooxml_complexity(format_id: str) -> FileValidationError:
    return _error(
        f"{format_id}_complexity_limit_exceeded",
        422,
        f"{format_id.upper()} 内部结构超过安全上限，请拆分或精简文档后再试。",
    )


def _error(code: str, status: int, message: str) -> FileValidationError:
    return FileValidationError(code, status, message)


def _positive(value: int, name: str) -> int:
    clean = int(value)
    if clean < 1:
        raise ValueError(f"{name} must be positive")
    return clean


def _positive_float(value: float, name: str) -> float:
    clean = float(value)
    if clean <= 0:
        raise ValueError(f"{name} must be positive")
    return clean
