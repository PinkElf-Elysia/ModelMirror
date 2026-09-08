from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .document_parser import (
    DocumentParseError,
    parse_document,
    parse_document_structured,
)
from .source_metadata import (
    heading_path_source_hash,
    heading_path_source_truncated,
    normalize_heading_path,
)
from .processing_receipt import (
    LEGACY_PARSER_CONTRACT_VERSION,
    PARSER_CONTRACT_VERSION,
    PROCESSING_RECEIPT_VERSION,
    structure_hash,
)


_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_LIST_ITEM = re.compile(r"^\s*(?:[-+*]|\d+[.)])\s+\S")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
_HEADING_SOURCE_HASH = re.compile(r"^[0-9a-f]{64}$")
_SEMANTIC_LAYOUT_EXTENSIONS = {
    ".json",
    ".jsonl",
    ".ndjson",
    ".yaml",
    ".yml",
    ".xml",
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".java",
    ".go",
    ".rs",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".cs",
    ".php",
    ".rb",
    ".sh",
    ".ps1",
    ".sql",
    ".css",
    ".scss",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".log",
}
_SHARED_SECTION_EXTENSIONS = _SEMANTIC_LAYOUT_EXTENSIONS | {
    ".csv",
    ".tsv",
    ".xlsx",
    ".html",
    ".htm",
    ".srt",
    ".vtt",
    ".docx",
    ".pptx",
}


@dataclass(slots=True)
class DocumentBlock:
    block_id: str
    kind: str
    text: str
    start_char: int
    end_char: int
    heading_path: list[str] = field(default_factory=list)
    heading_path_source_hash: str = ""
    heading_path_source_truncated: bool = False
    page_number: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def payload(self, *, max_text: int | None = None) -> dict[str, Any]:
        value = asdict(self)
        if max_text is not None and len(self.text) > max_text:
            value["text"] = self.text[:max_text] + "..."
            value["truncated"] = True
        else:
            value["truncated"] = False
        return value


@dataclass(slots=True)
class ProcessedDocument:
    source_id: str
    filename: str
    title: str
    text: str
    blocks: list[DocumentBlock]
    warnings: list[str] = field(default_factory=list)
    parser_contract_version: str = LEGACY_PARSER_CONTRACT_VERSION
    processing_receipt: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None

    @property
    def block_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for block in self.blocks:
            counts[block.kind] = counts.get(block.kind, 0) + 1
        return counts

    def payload(
        self,
        *,
        include_text: bool = False,
        max_block_text: int | None = 600,
    ) -> dict[str, Any]:
        value = {
            "source_id": self.source_id,
            "filename": self.filename,
            "title": self.title,
            "character_count": len(self.text),
            "block_count": len(self.blocks),
            "block_counts": self.block_counts,
            "warnings": list(self.warnings),
            "parser_contract_version": self.parser_contract_version,
            "processing_receipt": self.processing_receipt,
            "error_code": self.error_code,
            "blocks": [block.payload(max_text=max_block_text) for block in self.blocks],
        }
        if include_text:
            value["text"] = self.text
        return value


class StructuredDocumentProcessor:
    """Parse supported local documents into stable, structure-aware blocks."""

    def process(
        self,
        path: Path,
        *,
        filename: str,
        source_id: str,
        config: dict[str, Any] | None = None,
        extracted_text: str | None = None,
        extra_blocks: list[DocumentBlock | dict[str, Any]] | None = None,
    ) -> ProcessedDocument:
        options = dict(config or {})
        extension = Path(filename).suffix.lower()
        parsed = None
        operations: list[dict[str, Any]] = []
        degradation_reasons: list[str] = []
        if extension in {".png", ".jpg", ".jpeg", ".webp"} and extra_blocks:
            text = ""
            blocks = []
            warnings = []
        elif extracted_text is not None:
            text = self._clean_text(extracted_text)
            blocks = self._plain_blocks(text, source_id)
            warnings: list[str] = []
        elif extension in {".md", ".markdown"}:
            text = self._clean_text(parse_document(path, filename))
            blocks = self._markdown_blocks(
                text,
                source_id,
                preserve_tables=bool(options.get("preserve_tables", True)),
                preserve_code=bool(options.get("preserve_code_blocks", True)),
            )
            warnings = []
        elif extension == ".pdf":
            parsed = parse_document_structured(
                path, filename,
                remove_repeated_pdf_edges=bool(options.get("remove_repeated_headers_footers", True)),
                preserve_pdf_tables=bool(options.get("preserve_tables", True)),
            )
            text, blocks = self._shared_section_blocks(parsed, source_id)
            warnings = list(parsed.warnings)
            operations = [dict(item) for item in parsed.operations]
            if operations:
                warnings.append("Removed repeated PDF page edges; see hashed transform receipt.")
            if not blocks and not extra_blocks:
                raise DocumentParseError("PDF has no text layer; OCR is required.", error_code="scanned_pdf_requires_ocr")
        elif extension in _SHARED_SECTION_EXTENSIONS:
            parsed = parse_document_structured(path, filename)
            text, blocks = self._shared_section_blocks(parsed, source_id)
            warnings = list(parsed.warnings)
        else:
            text = self._clean_text(parse_document(path, filename))
            blocks = self._plain_blocks(text, source_id)
            warnings = []

        blocks, text = self._merge_extra_blocks(
            source_id=source_id,
            blocks=blocks,
            text=text,
            extra_blocks=extra_blocks or [],
        )
        if not blocks:
            raise DocumentParseError(f"Document produced no structured blocks: {filename}")
        layout_status = parsed.layout_status if parsed is not None else "not_applicable"
        if parsed is not None and parsed.truncated:
            degradation_reasons.append("rag_document_truncated")
        if layout_status == "degraded":
            degradation_reasons.append("rag_layout_degraded")
        if extension == ".pdf" and parsed is not None:
            vision_pages = sorted({
                block.page_number for block in blocks
                if block.kind.startswith(("image_", "visual_")) and block.page_number is not None
            })
            missing_pages = sorted(set(parsed.empty_pages) - set(vision_pages))
            if missing_pages:
                degradation_reasons.append("scanned_pdf_requires_ocr")
            if any(page < 1 or page > parsed.page_count for page in vision_pages):
                degradation_reasons.append("rag_vision_page_identity_invalid")
            if vision_pages:
                operations.append({"code": "vision_page_text", "pages": vision_pages, "count": len(vision_pages)})
        processed_pages = sorted({block.page_number for block in blocks if block.page_number is not None})
        with path.open("rb") as source:
            source_content_hash = hashlib.file_digest(source, "sha256").hexdigest()
        processing_receipt = {
            "receipt_version": PROCESSING_RECEIPT_VERSION,
            "parser_contract_version": PARSER_CONTRACT_VERSION,
            "source_id": source_id, "source_content_hash": source_content_hash,
            "status": "degraded" if degradation_reasons else "complete",
            "layout_status": layout_status, "page_count": parsed.page_count if parsed is not None else 0,
            "processed_pages": processed_pages, "empty_pages": list(parsed.empty_pages) if parsed is not None else [],
            "degraded_pages": list(parsed.degraded_pages) if parsed is not None else [],
            "degradation_reasons": degradation_reasons, "operations": operations,
            "block_count": len(blocks), "structure_hash": structure_hash([asdict(block) for block in blocks]),
        }
        title = Path(filename).stem
        if bool(options.get("extract_title", True)):
            heading = next((block.text for block in blocks if block.kind == "heading"), "")
            if heading:
                title = heading.lstrip("#").strip()[:300]
        return ProcessedDocument(
            source_id=source_id,
            filename=filename,
            title=title,
            text=text,
            blocks=blocks,
            warnings=warnings,
            parser_contract_version=PARSER_CONTRACT_VERSION,
            processing_receipt=processing_receipt,
            error_code=degradation_reasons[0] if degradation_reasons else None,
        )

    def _merge_extra_blocks(
        self,
        *,
        source_id: str,
        blocks: list[DocumentBlock],
        text: str,
        extra_blocks: list[DocumentBlock | dict[str, Any]],
    ) -> tuple[list[DocumentBlock], str]:
        if not extra_blocks:
            return blocks, text

        merged = list(blocks)
        parts = [text] if text else []
        cursor = len(text)
        for index, value in enumerate(extra_blocks):
            if isinstance(value, DocumentBlock):
                raw = value.payload()
            else:
                raw = dict(value)
            block_text = self._clean_text(str(raw.get("text") or ""))
            if not block_text:
                continue
            if parts:
                parts.append("\n\n")
                cursor += 2
            start = cursor
            parts.append(block_text)
            cursor += len(block_text)
            metadata = raw.get("metadata")
            raw_heading_path = raw.get("heading_path")
            source_hash, source_truncated = self._heading_lineage(
                raw_heading_path,
                inherited_hash=raw.get("heading_path_source_hash"),
                inherited_truncated=raw.get("heading_path_source_truncated"),
            )
            merged.append(
                DocumentBlock(
                    block_id=str(raw.get("block_id") or self._stable_block_id(source_id, str(raw.get("kind") or "visual"), index)),
                    kind=str(raw.get("kind") or "image_description"),
                    text=block_text,
                    start_char=start,
                    end_char=cursor,
                    heading_path=list(normalize_heading_path(raw_heading_path)),
                    heading_path_source_hash=source_hash,
                    heading_path_source_truncated=source_truncated,
                    page_number=self._optional_int(raw.get("page_number")),
                    metadata=dict(metadata) if isinstance(metadata, dict) else {},
                )
            )
        return merged, "".join(parts)

    def _stable_block_id(self, source_id: str, kind: str, index: int) -> str:
        digest = hashlib.sha256(f"{source_id}:{kind}:extra:{index}".encode("utf-8")).hexdigest()[:20]
        return f"block_{digest}"

    def _heading_lineage(
        self,
        value: Any,
        *,
        inherited_hash: Any = None,
        inherited_truncated: Any = None,
    ) -> tuple[str, bool]:
        computed_hash = heading_path_source_hash(value)
        computed_truncated = heading_path_source_truncated(value)
        if computed_truncated:
            return computed_hash, True
        candidate_hash = str(inherited_hash or "").strip().lower()
        valid_inherited = (
            _HEADING_SOURCE_HASH.fullmatch(candidate_hash) is not None
            and type(inherited_truncated) is bool
        )
        if valid_inherited and (
            inherited_truncated is True or candidate_hash == computed_hash
        ):
            return candidate_hash, bool(inherited_truncated)
        return computed_hash, False

    def _optional_int(self, value: Any) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _clean_text(self, text: str) -> str:
        normalized = unicodedata.normalize("NFKC", text.replace("\r\n", "\n").replace("\r", "\n"))
        normalized = _CONTROL_CHARS.sub("", normalized)
        lines = [line.rstrip() for line in normalized.split("\n")]
        compact: list[str] = []
        blank = False
        for line in lines:
            if line.strip():
                compact.append(line)
                blank = False
            elif not blank:
                compact.append("")
                blank = True
        return "\n".join(compact).strip()

    def _plain_blocks(self, text: str, source_id: str) -> list[DocumentBlock]:
        blocks: list[DocumentBlock] = []
        for match in re.finditer(r"\S(?:.*?\S)?(?=\n\s*\n|\Z)", text, re.DOTALL):
            raw = match.group(0).strip()
            kind = "list" if all(_LIST_ITEM.match(line) for line in raw.splitlines() if line.strip()) else "paragraph"
            blocks.append(self._block(source_id, kind, raw, match.start(), match.end()))
        return blocks

    def _markdown_blocks(
        self,
        text: str,
        source_id: str,
        *,
        preserve_tables: bool,
        preserve_code: bool,
    ) -> list[DocumentBlock]:
        lines = text.splitlines(keepends=True)
        offsets: list[int] = []
        cursor = 0
        for line in lines:
            offsets.append(cursor)
            cursor += len(line)
        blocks: list[DocumentBlock] = []
        headings: list[str] = []
        index = 0
        while index < len(lines):
            stripped = lines[index].strip()
            if not stripped:
                index += 1
                continue
            start_index = index
            heading_match = _HEADING.match(stripped)
            if heading_match:
                level = len(heading_match.group(1))
                headings = headings[: level - 1] + [heading_match.group(2).strip()]
                index += 1
                blocks.append(
                    self._line_block(source_id, "heading", lines, offsets, start_index, index, headings)
                )
                continue
            if stripped.startswith(("```", "~~~")):
                marker = stripped[:3]
                index += 1
                while index < len(lines) and not lines[index].strip().startswith(marker):
                    index += 1
                index = min(len(lines), index + 1)
                kind = "code" if preserve_code else "paragraph"
                blocks.append(self._line_block(source_id, kind, lines, offsets, start_index, index, headings))
                continue
            if (
                preserve_tables
                and "|" in stripped
                and index + 1 < len(lines)
                and _TABLE_SEPARATOR.match(lines[index + 1].strip())
            ):
                index += 2
                while index < len(lines) and "|" in lines[index] and lines[index].strip():
                    index += 1
                blocks.append(self._line_block(source_id, "table", lines, offsets, start_index, index, headings))
                continue
            if _LIST_ITEM.match(stripped):
                index += 1
                while index < len(lines) and (_LIST_ITEM.match(lines[index].strip()) or lines[index].startswith(("  ", "\t"))):
                    index += 1
                blocks.append(self._line_block(source_id, "list", lines, offsets, start_index, index, headings))
                continue

            index += 1
            while index < len(lines):
                candidate = lines[index].strip()
                if not candidate or _HEADING.match(candidate) or candidate.startswith(("```", "~~~")):
                    break
                if _LIST_ITEM.match(candidate):
                    break
                if preserve_tables and "|" in candidate and index + 1 < len(lines) and _TABLE_SEPARATOR.match(lines[index + 1].strip()):
                    break
                index += 1
            blocks.append(self._line_block(source_id, "paragraph", lines, offsets, start_index, index, headings))
        return blocks

    def _line_block(
        self,
        source_id: str,
        kind: str,
        lines: list[str],
        offsets: list[int],
        start_index: int,
        end_index: int,
        headings: list[str],
    ) -> DocumentBlock:
        raw = "".join(lines[start_index:end_index])
        text = raw.strip()
        leading = len(raw) - len(raw.lstrip())
        start = offsets[start_index] + leading
        end = start + len(text)
        return self._block(source_id, kind, text, start, end, heading_path=list(headings))

    def _shared_section_blocks(
        self,
        parsed: Any,
        source_id: str,
    ) -> tuple[str, list[DocumentBlock]]:
        """Preserve ParsedSection source coordinates in RAG blocks."""

        blocks: list[DocumentBlock] = []
        parts: list[str] = []
        cursor = 0
        for section in parsed.sections:
            if parts:
                parts.append("\n\n")
                cursor += 2
            start = cursor
            parts.append(section.text)
            cursor += len(section.text)
            source_heading_path = list(section.heading_path or [])
            heading_path = list(normalize_heading_path(source_heading_path))
            metadata = {
                key: value
                for key, value in {
                    "page": section.page,
                    "slide": section.slide,
                    "sheet": section.sheet,
                    "line_range": section.line_range,
                    "row_range": section.row_range,
                    "time_range": section.time_range,
                    "heading_path": heading_path or None,
                    "column": section.column,
                    "table_bbox": list(section.table_bbox) if section.table_bbox else None,
                    "layout_status": section.layout_status if parsed.format == "pdf" else None,
                }.items()
                if value is not None
            }
            if section.kind in {"table", "page"}:
                kind = section.kind
            elif parsed.format in {"csv", "tsv", "xlsx"}:
                kind = "table"
            elif parsed.format in {"srt", "vtt"}:
                kind = "subtitle"
            elif heading_path and heading_path[-1] == section.text:
                kind = "heading"
            elif parsed.format in {
                "json", "jsonl", "yaml", "xml", "configuration"
            }:
                kind = "structured"
            elif parsed.format in {"source_code", "log"}:
                kind = "source"
            else:
                kind = "paragraph"
            blocks.append(
                self._block(
                    source_id,
                    kind,
                    section.text,
                    start,
                    cursor,
                    heading_path=source_heading_path,
                    page_number=section.page,
                    metadata=metadata,
                )
            )
        return "".join(parts), blocks

    def _block(
        self,
        source_id: str,
        kind: str,
        text: str,
        start: int,
        end: int,
        *,
        heading_path: list[str] | None = None,
        page_number: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DocumentBlock:
        digest = hashlib.sha256(f"{source_id}:{kind}:{start}:{end}".encode("utf-8")).hexdigest()[:20]
        source_heading_path = list(heading_path or [])
        return DocumentBlock(
            block_id=f"block_{digest}",
            kind=kind,
            text=text,
            start_char=start,
            end_char=end,
            heading_path=list(normalize_heading_path(source_heading_path)),
            heading_path_source_hash=heading_path_source_hash(source_heading_path),
            heading_path_source_truncated=heading_path_source_truncated(
                source_heading_path
            ),
            page_number=page_number,
            metadata=dict(metadata or {}),
        )
