from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Callable

from .source_metadata import (
    MAX_HEADING_SEGMENT_CHARS,
    normalize_heading_path,
)


DEFAULT_SEPARATORS = ["\n\n", "\n", "。", "！", "？", ". ", " ", ""]
ESTIMATED_TOKEN_CHUNKER_CONTRACT = "rag-chunker-estimated-token-v1"
ESTIMATED_TOKEN_SIZE_UNIT = "estimated_tokens"
ESTIMATED_TOKEN_ESTIMATOR = "mixed_cjk_latin_v1"
MAX_HEADING_PREFIX_TOKENS = 64
_CJK_SCRIPT_PATTERN = re.compile(
    "["
    "\u1100-\u11ff"
    "\u2e80-\u303f"
    "\u3040-\u30ff"
    "\u3100-\u312f"
    "\u3130-\u318f"
    "\u31a0-\u31bf"
    "\u31f0-\u31ff"
    "\u3400-\u4dbf"
    "\u4e00-\u9fff"
    "\ua960-\ua97f"
    "\uac00-\ud7af"
    "\ud7b0-\ud7ff"
    "\uf900-\ufaff"
    "\uff66-\uff9d"
    "\U00020000-\U0002a6df"
    "\U0002a700-\U0002b73f"
    "\U0002b740-\U0002b81f"
    "\U0002b820-\U0002ceaf"
    "\U0002ceb0-\U0002ebef"
    "\U0002ebf0-\U0002ee5f"
    "\U0002f800-\U0002fa1f"
    "\U00030000-\U0003134f"
    "\U00031350-\U000323af"
    "]"
)


def estimate_mixed_cjk_latin_v1_tokens(text: str) -> int:
    """Stable RAG-only estimate: CJK scripts count 1, all other chars 1/4."""

    if not text:
        return 0
    cjk_count = len(_CJK_SCRIPT_PATTERN.findall(text))
    non_cjk_count = max(0, len(text) - cjk_count)
    return cjk_count + math.ceil(non_cjk_count / 4)


@dataclass(slots=True)
class TextChunk:
    text: str
    index: int
    start_char: int
    end_char: int
    chunk_type: str = "standard"
    parent_chunk_id: str | None = None
    parent_text: str | None = None
    parent_start_char: int | None = None
    parent_end_char: int | None = None


def with_heading_prefix(prefix: str, body: str) -> str:
    """Attach the bounded structural heading to index and context text equally."""

    clean_body = str(body or "").strip()
    if not prefix:
        return clean_body
    if clean_body == prefix or clean_body.startswith(prefix + "\n"):
        return clean_body
    return f"{prefix}\n{clean_body}" if clean_body else prefix


def bounded_generated_index_text(
    base_text: str,
    evidence_text: str,
    *,
    budget: int,
) -> str:
    """Keep the generated retrieval signal and add bounded segment evidence."""

    base = str(base_text or "").strip()
    evidence = str(evidence_text or "").strip()
    if not base or not evidence:
        return base
    combined = f"{base}\n{evidence}"
    if estimate_mixed_cjk_latin_v1_tokens(combined) <= budget:
        return combined
    evidence_budget = budget - estimate_mixed_cjk_latin_v1_tokens(f"{base}\n")
    if evidence_budget <= 0:
        return base
    bounded_evidence = _truncate_estimated_tokens(evidence, evidence_budget)
    candidate = f"{base}\n{bounded_evidence}" if bounded_evidence else base
    return (
        candidate
        if estimate_mixed_cjk_latin_v1_tokens(candidate) <= budget
        else base
    )


def bounded_heading_prefix(value: object, *, budget: int) -> tuple[str, bool]:
    """Keep the root and nearest heading inside a deterministic token budget."""

    normalized_path = normalize_heading_path(value)
    heading_path = normalized_path
    source_truncated = False
    if isinstance(value, (list, tuple)):
        raw_segments = [
            item.strip()
            for item in value
            if isinstance(item, str) and item.strip()
        ]
        source_truncated = (
            len(raw_segments) != len(normalized_path)
            or any(
                raw_segments[index] != normalized_path[index]
                for index in range(min(len(raw_segments), len(normalized_path)))
            )
        )
        if source_truncated and raw_segments:
            root = raw_segments[0][:MAX_HEADING_SEGMENT_CHARS]
            leaf = raw_segments[-1][:MAX_HEADING_SEGMENT_CHARS]
            heading_path = (root,) if root == leaf else (root, leaf)
    if not heading_path:
        return "", source_truncated
    full = " > ".join(heading_path)
    if estimate_mixed_cjk_latin_v1_tokens(full) <= budget:
        return full, source_truncated
    if budget <= 0:
        return "", True
    selected = (
        heading_path[0]
        if len(heading_path) == 1
        else f"{heading_path[0]} > {heading_path[-1]}"
    )
    if estimate_mixed_cjk_latin_v1_tokens(selected) <= budget:
        return selected, True
    if len(heading_path) == 1:
        return _truncate_estimated_tokens(heading_path[0], budget), True
    separator = " > "
    separator_tokens = estimate_mixed_cjk_latin_v1_tokens(separator)
    available = max(1, budget - separator_tokens)
    root_budget = max(1, available // 2)
    leaf_budget = max(1, available - root_budget)
    root = _truncate_estimated_tokens(heading_path[0], root_budget)
    leaf = _truncate_estimated_tokens(heading_path[-1], leaf_budget)
    combined = f"{root}{separator}{leaf}" if root and leaf else root or leaf
    return _truncate_estimated_tokens(combined, budget), True


def heading_prefix_budget(
    *,
    index_budget: int,
    index_overlap: int,
    context_budget: int,
    context_overlap: int,
) -> int:
    """Reserve one estimated token for the separator newline and one for body text."""

    return min(
        MAX_HEADING_PREFIX_TOKENS,
        max(0, index_budget - index_overlap - 2),
        max(0, context_budget - context_overlap - 2),
        max(0, min(index_budget, context_budget) // 4),
    )


def _truncate_estimated_tokens(text: str, budget: int) -> str:
    if budget <= 0:
        return ""
    if estimate_mixed_cjk_latin_v1_tokens(text) <= budget:
        return text
    low = 0
    high = len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if estimate_mixed_cjk_latin_v1_tokens(text[:middle]) <= budget:
            low = middle
        else:
            high = middle - 1
    return text[:low].rstrip()


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
                        parent_start_char=parent.start_char,
                        parent_end_char=parent.end_char,
                    )
                )
        return children


class EstimatedTokenTextSplitter:
    """Deterministic splitter bounded by the shared tokenizer-free estimate."""

    def __init__(
        self,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        separators: list[str] | None = None,
    ) -> None:
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        if chunk_overlap < 0 or chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be non-negative and smaller than chunk_size")
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.separators = _validated_separators(separators)

    def split_text(self, text: str) -> list[str]:
        return [chunk.text for chunk in self.split_segments(text)]

    def split_segments(self, text: str) -> list[TextChunk]:
        return _split_measured_windowed(
            text,
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            separators=self.separators,
            measure=estimate_mixed_cjk_latin_v1_tokens,
        )


class EstimatedTokenParentChildTextSplitter:
    """Parent/child splitter with independent estimated-token budgets."""

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
        self.parent = EstimatedTokenTextSplitter(
            parent_chunk_size,
            parent_chunk_overlap,
            parent_separators,
        )
        self.child = EstimatedTokenTextSplitter(
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
                        parent_start_char=parent.start_char,
                        parent_end_char=parent.end_char,
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
        end = (
            text_length
            if hard_end == text_length
            else _preferred_boundary(text, cursor, hard_end, separators)
        )
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


def _split_measured_windowed(
    text: str,
    *,
    chunk_size: int,
    chunk_overlap: int,
    separators: list[str],
    measure: Callable[[str], int],
) -> list[TextChunk]:
    if not text.strip():
        return []
    chunks: list[TextChunk] = []
    cursor = 0
    text_length = len(text)
    while cursor < text_length:
        hard_end = _maximum_measured_end(
            text,
            start=cursor,
            budget=chunk_size,
            measure=measure,
        )
        if hard_end <= cursor:
            hard_end = min(cursor + 1, text_length)
        end = (
            text_length
            if hard_end == text_length
            else _preferred_boundary(text, cursor, hard_end, separators)
        )
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
        overlap_start = _minimum_measured_start(
            text,
            end=end,
            budget=chunk_overlap,
            measure=measure,
        )
        cursor = max(cursor + 1, overlap_start)
    return chunks


def _maximum_measured_end(
    text: str,
    *,
    start: int,
    budget: int,
    measure: Callable[[str], int],
) -> int:
    low = start
    # mixed_cjk_latin_v1 charges every code point at least 1/4 token. Keeping
    # the search window within 4 * budget prevents each chunk from rescanning
    # the entire remaining document while preserving the exact result.
    high = min(len(text), start + max(0, budget) * 4)
    while low < high:
        middle = (low + high + 1) // 2
        if measure(text[start:middle]) <= budget:
            low = middle
        else:
            high = middle - 1
    return low


def _minimum_measured_start(
    text: str,
    *,
    end: int,
    budget: int,
    measure: Callable[[str], int],
) -> int:
    if budget <= 0:
        return end
    # The same lower bound makes overlap search proportional to the configured
    # overlap instead of the total prefix length.
    low = max(0, end - budget * 4)
    high = end
    while low < high:
        middle = (low + high) // 2
        if measure(text[middle:end]) <= budget:
            high = middle
        else:
            low = middle + 1
    return low


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
