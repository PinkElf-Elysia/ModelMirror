from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .document_parser import DocumentParseError, parse_document


_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_LIST_ITEM = re.compile(r"^\s*(?:[-+*]|\d+[.)])\s+\S")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")


@dataclass(slots=True)
class DocumentBlock:
    block_id: str
    kind: str
    text: str
    start_char: int
    end_char: int
    heading_path: list[str] = field(default_factory=list)
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
            pages = self._pdf_pages(path)
            pages, warnings = self._clean_pdf_pages(
                pages,
                remove_repeated=bool(options.get("remove_repeated_headers_footers", True)),
            )
            text, blocks = self._page_blocks(pages, source_id)
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
            merged.append(
                DocumentBlock(
                    block_id=str(raw.get("block_id") or self._stable_block_id(source_id, str(raw.get("kind") or "visual"), index)),
                    kind=str(raw.get("kind") or "image_description"),
                    text=block_text,
                    start_char=start,
                    end_char=cursor,
                    heading_path=[str(item) for item in (raw.get("heading_path") or []) if str(item).strip()],
                    page_number=self._optional_int(raw.get("page_number")),
                    metadata=dict(metadata) if isinstance(metadata, dict) else {},
                )
            )
        return merged, "".join(parts)

    def _stable_block_id(self, source_id: str, kind: str, index: int) -> str:
        digest = hashlib.sha256(f"{source_id}:{kind}:extra:{index}".encode("utf-8")).hexdigest()[:20]
        return f"block_{digest}"

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
        text = "".join(lines[start_index:end_index]).strip()
        start = offsets[start_index]
        end = start + len("".join(lines[start_index:end_index]).rstrip("\n"))
        return self._block(source_id, kind, text, start, end, heading_path=list(headings))

    def _pdf_pages(self, path: Path) -> list[str]:
        try:
            import pdfplumber  # type: ignore[import-not-found]

            with pdfplumber.open(path) as pdf:
                return [page.extract_text() or "" for page in pdf.pages]
        except ImportError:
            pass
        except Exception as exc:  # pragma: no cover - parser-specific failure.
            raise DocumentParseError(f"PDF parsing failed: {path.name}") from exc
        try:
            import PyPDF2  # type: ignore[import-not-found]

            with path.open("rb") as handle:
                reader = PyPDF2.PdfReader(handle)
                return [page.extract_text() or "" for page in reader.pages]
        except Exception as exc:  # pragma: no cover - dependency/parser-specific failure.
            raise DocumentParseError(f"PDF parsing failed: {path.name}") from exc

    def _clean_pdf_pages(self, pages: list[str], *, remove_repeated: bool) -> tuple[list[str], list[str]]:
        cleaned = [self._clean_text(page) for page in pages]
        if not remove_repeated or len(cleaned) < 2:
            return cleaned, []
        edge_counts: dict[str, int] = {}
        page_edges: list[tuple[str, str]] = []
        for page in cleaned:
            lines = [line.strip() for line in page.splitlines() if line.strip()]
            edges = (lines[0] if lines else "", lines[-1] if lines else "")
            page_edges.append(edges)
            for value in set(edges):
                if 2 <= len(value) <= 200:
                    edge_counts[value] = edge_counts.get(value, 0) + 1
        threshold = max(2, math.ceil(len(cleaned) * 0.6))
        repeated = {value for value, count in edge_counts.items() if count >= threshold}
        if not repeated:
            return cleaned, []
        output: list[str] = []
        for page in cleaned:
            lines = page.splitlines()
            while lines and lines[0].strip() in repeated:
                lines.pop(0)
            while lines and lines[-1].strip() in repeated:
                lines.pop()
            output.append(self._clean_text("\n".join(lines)))
        return output, [f"Removed {len(repeated)} repeated PDF header/footer lines."]

    def _page_blocks(self, pages: list[str], source_id: str) -> tuple[str, list[DocumentBlock]]:
        blocks: list[DocumentBlock] = []
        parts: list[str] = []
        cursor = 0
        for page_number, page in enumerate(pages, start=1):
            if not page.strip():
                continue
            if parts:
                parts.append("\n\n")
                cursor += 2
            start = cursor
            parts.append(page)
            cursor += len(page)
            blocks.append(
                self._block(
                    source_id,
                    "page",
                    page,
                    start,
                    cursor,
                    page_number=page_number,
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
    ) -> DocumentBlock:
        digest = hashlib.sha256(f"{source_id}:{kind}:{start}:{end}".encode("utf-8")).hexdigest()[:20]
        return DocumentBlock(
            block_id=f"block_{digest}",
            kind=kind,
            text=text,
            start_char=start,
            end_char=end,
            heading_path=list(heading_path or []),
            page_number=page_number,
        )
