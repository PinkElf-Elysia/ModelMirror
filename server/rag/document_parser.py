from __future__ import annotations

from pathlib import Path

try:
    from server.file_assets.contracts import FileInputKind, FilePurpose
    from server.file_assets.document_parser import (
        LocalDocumentParseError,
        ParsedDocument,
        parse_chat_document,
    )
    from server.file_assets.registry import get_file_format_registry
    from server.file_assets.validation import FileUploadValidator, FileValidationError
except ModuleNotFoundError:
    from file_assets.contracts import FileInputKind, FilePurpose
    from file_assets.document_parser import (
        LocalDocumentParseError,
        ParsedDocument,
        parse_chat_document,
    )
    from file_assets.registry import get_file_format_registry
    from file_assets.validation import FileUploadValidator, FileValidationError


class DocumentParseError(ValueError):
    """Raised when a document cannot be parsed into plain text."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "document_parse_failed",
        status_code: int = 422,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.status_code = status_code


SUPPORTED_EXTENSIONS = set(
    get_file_format_registry().extensions_for(
        FilePurpose.RAG,
        FileInputKind.DOCUMENT,
    )
)


def supported_extensions() -> set[str]:
    """Return the supported document extensions."""

    return set(SUPPORTED_EXTENSIONS)


def parse_document(path: Path, original_filename: str | None = None) -> str:
    """Return the shared, validated ParsedDocument as RAG text."""

    parsed = parse_document_structured(path, original_filename)
    text = "\n\n".join(section.text for section in parsed.sections)
    if not text.strip():
        raise DocumentParseError(
            f"文档没有可读取的文本内容：{original_filename or path.name}"
        )
    return text


def parse_document_structured(
    path: Path,
    original_filename: str | None = None,
) -> ParsedDocument:
    """Validate and parse RAG/Agent inputs through the canonical file kernel."""

    display_name = original_filename or path.name
    try:
        validated = FileUploadValidator().validate_path(
            path,
            purpose=FilePurpose.RAG,
            input_kind=FileInputKind.DOCUMENT,
            filename=display_name,
            declared_media_type=None,
        )
        return parse_chat_document(
            path,
            format_id=validated.format_id,
            title=display_name,
        )
    except (FileValidationError, LocalDocumentParseError) as exc:
        message = getattr(exc, "message", None) or str(exc)
        raise DocumentParseError(
            message,
            error_code=getattr(exc, "error_code", "document_parse_failed"),
            status_code=getattr(exc, "status_code", 422),
        ) from exc

