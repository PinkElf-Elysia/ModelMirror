from __future__ import annotations

from dataclasses import dataclass


DEFAULT_SEPARATORS = ["\n\n", "\n", "。", "！", "？", ". ", " ", ""]


@dataclass(slots=True)
class TextChunk:
    text: str
    index: int
    start_char: int
    end_char: int
    chunk_type: str = "standard"
    parent_chunk_id: str | None = None
    parent_text: str | None = None


class TextSplitter:
    """Deterministic recursive-style splitter that preserves source offsets."""

    def __init__(
        self,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        separators: list[str] | None = None,
    ) -> None:
        if chunk_size < 100:
            raise ValueError("chunk_size must be at least 100")
        if chunk_overlap < 0 or chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be non-negative and smaller than chunk_size")
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.separators = _validated_separators(separators)

    def split_text(self, text: str) -> list[str]:
        return [chunk.text for chunk in self.split_segments(text)]

    def split_segments(self, text: str) -> list[TextChunk]:
        return _split_windowed(
            text,
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            separators=self.separators,
        )


class ParentChildTextSplitter:
    """Build parent context windows and child retrieval chunks with stable offsets."""

    def __init__(
        self,
        *,
        parent_chunk_size: int = 1500,
        parent_chunk_overlap: int = 100,
        child_chunk_size: int = 400,
        child_chunk_overlap: int = 50,
        parent_separators: list[str] | None = None,
        child_separators: list[str] | None = None,
    ) -> None:
        if parent_chunk_size <= child_chunk_size:
            raise ValueError("parent_chunk_size must be greater than child_chunk_size")
        self.parent = TextSplitter(
            parent_chunk_size,
            parent_chunk_overlap,
            parent_separators,
        )
        self.child = TextSplitter(
            child_chunk_size,
            child_chunk_overlap,
            child_separators,
        )

    def split_segments(self, text: str) -> list[TextChunk]:
        children: list[TextChunk] = []
        for parent_index, parent in enumerate(self.parent.split_segments(text)):
            parent_id = f"parent_{parent_index}"
            for child in self.child.split_segments(parent.text):
                children.append(
                    TextChunk(
                        text=child.text,
                        index=len(children),
                        start_char=parent.start_char + child.start_char,
                        end_char=parent.start_char + child.end_char,
                        chunk_type="child",
                        parent_chunk_id=parent_id,
                        parent_text=parent.text,
                    )
                )
        return children


def _validated_separators(value: list[str] | None) -> list[str]:
    if value is None:
        return list(DEFAULT_SEPARATORS)
    if not isinstance(value, list) or not value or len(value) > 20:
        raise ValueError("separators must contain between 1 and 20 items")
    separators: list[str] = []
    for item in value:
        if not isinstance(item, str) or len(item) > 20:
            raise ValueError("each separator must be a string of at most 20 characters")
        if item not in separators:
            separators.append(item)
    if "" not in separators:
        separators.append("")
    return separators


def _split_windowed(
    text: str,
    *,
    chunk_size: int,
    chunk_overlap: int,
    separators: list[str],
) -> list[TextChunk]:
    if not text.strip():
        return []
    chunks: list[TextChunk] = []
    cursor = 0
    text_length = len(text)
    while cursor < text_length:
        hard_end = min(cursor + chunk_size, text_length)
        end = _preferred_boundary(text, cursor, hard_end, separators)
        if end <= cursor:
            end = hard_end

        raw = text[cursor:end]
        leading = len(raw) - len(raw.lstrip())
        trailing = len(raw) - len(raw.rstrip())
        start_char = cursor + leading
        end_char = end - trailing
        if end_char > start_char:
            chunks.append(
                TextChunk(
                    text=text[start_char:end_char],
                    index=len(chunks),
                    start_char=start_char,
                    end_char=end_char,
                )
            )
        if end >= text_length:
            break
        cursor = max(cursor + 1, end - chunk_overlap)
    return chunks


def _preferred_boundary(
    text: str,
    start: int,
    hard_end: int,
    separators: list[str],
) -> int:
    minimum = min(hard_end, start + max(1, (hard_end - start) // 2))
    for separator in separators:
        if not separator:
            return hard_end
        position = text.rfind(separator, minimum, hard_end)
        if position >= start:
            return position + len(separator)
    return hard_end
