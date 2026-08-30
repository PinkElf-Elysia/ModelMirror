from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any


GENERATED_PARENT_IDENTITY_VERSION = "rag-generated-parent-v1"
GENERATED_PARENT_WINDOW_IDENTITY_VERSION = "rag-generated-parent-window-v1"
_GENERATED_PARENT_PATTERN = re.compile(r"^generated_v1_[0-9a-f]{64}$")
_GENERATED_PARENT_WINDOW_PATTERN = re.compile(
    r"^(generated_v1_[0-9a-f]{64}):window_v1:"
    r"((?:parent|segment)_[0-9]+):([0-9a-f]{64})$"
)


@dataclass(frozen=True, slots=True)
class GeneratedSegmentSourceMapping:
    source_block_id: str | None
    source_block_ids: tuple[str, ...]
    start_char: int
    end_char: int
    status: str


def canonical_source_text_hash(value: Any) -> str | None:
    text = str(value or "")
    if not text.strip():
        return None
    encoded = json.dumps(
        text,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def generated_parent_identity(
    document_id: str,
    item: dict[str, Any],
    blocks_by_id: dict[str, dict[str, Any]],
) -> tuple[str, tuple[str, ...]]:
    """Bind a generated retrieval parent to its exact canonical evidence lineage."""

    item_id = str(item.get("item_id") or "")
    item_type = str(item.get("item_type") or "generated")
    index_text = str(item.get("index_text") or "")
    context_text = str(item.get("context_text") or "")
    raw_block_ids = item.get("source_block_ids")
    if (
        not document_id
        or not item_id
        or not index_text.strip()
        or not context_text.strip()
        or not isinstance(raw_block_ids, list)
    ):
        raise ValueError("generated item identity is incomplete")
    source_block_ids = tuple(str(block_id) for block_id in raw_block_ids)
    if (
        not source_block_ids
        or any(not block_id for block_id in source_block_ids)
        or len(set(source_block_ids)) != len(source_block_ids)
    ):
        raise ValueError("generated item source-block identity is invalid")

    block_receipts: list[dict[str, str]] = []
    for block_id in source_block_ids:
        block = blocks_by_id.get(block_id)
        block_hash = canonical_source_text_hash(
            block.get("text") if isinstance(block, dict) else None
        )
        if (
            not isinstance(block, dict)
            or str(block.get("kind") or "") == "heading"
            or not block_hash
        ):
            raise ValueError("generated item source-block identity is invalid")
        block_receipts.append(
            {"source_block_id": block_id, "source_block_hash": block_hash}
        )

    context_source_ranges = _validated_generated_source_ranges(
        item,
        source_block_ids=source_block_ids,
        blocks_by_id=blocks_by_id,
    )

    payload = {
        "contract_version": GENERATED_PARENT_IDENTITY_VERSION,
        "document_id": document_id,
        "item_id": item_id,
        "item_type": item_type,
        "index_text": index_text,
        "context_text": context_text,
        "source_blocks": block_receipts,
    }
    if context_source_ranges:
        # Keep the legacy v1 digest stable for artifacts that predate structured
        # source ranges; current generated items bind the new range evidence.
        payload["context_source_ranges"] = context_source_ranges
    digest = hashlib.sha256(
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return f"generated_v1_{digest}", source_block_ids


def generated_parent_window_identity(
    item_parent_id: str,
    local_parent_id: str,
    parent_text: str,
) -> str:
    """Bind one splitter parent window to its exact generated context text."""

    if not _GENERATED_PARENT_PATTERN.fullmatch(str(item_parent_id or "")):
        raise ValueError("generated parent identity is invalid")
    if not re.fullmatch(r"(?:parent|segment)_[0-9]+", str(local_parent_id or "")):
        raise ValueError("generated parent window identity is invalid")
    if not str(parent_text or ""):
        raise ValueError("generated parent window text is unavailable")
    digest = hashlib.sha256(
        json.dumps(
            {
                "contract_version": GENERATED_PARENT_WINDOW_IDENTITY_VERSION,
                "item_parent_id": item_parent_id,
                "local_parent_id": local_parent_id,
                "parent_text": parent_text,
            },
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return f"{item_parent_id}:window_v1:{local_parent_id}:{digest}"


def resolve_generated_item_parent_identity(
    parent_chunk_id: str,
    *,
    parent_text: str | None,
) -> str:
    """Resolve and, for window identities, verify the item-level parent digest."""

    value = str(parent_chunk_id or "")
    if _GENERATED_PARENT_PATTERN.fullmatch(value):
        return value
    match = _GENERATED_PARENT_WINDOW_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError("generated parent window identity is invalid")
    item_parent_id, local_parent_id, _digest = match.groups()
    expected = generated_parent_window_identity(
        item_parent_id,
        local_parent_id,
        str(parent_text or ""),
    )
    if expected != value:
        raise ValueError("generated parent window identity is invalid")
    return item_parent_id


def generated_segment_source_mapping(
    segment_text: str,
    source_blocks: list[dict[str, Any]],
    *,
    segment_start: int | None = None,
    segment_end: int | None = None,
    context_source_ranges: Any = None,
) -> GeneratedSegmentSourceMapping:
    """Map one generated child to exactly one canonical source block, or fail closed."""

    if (
        isinstance(segment_start, int)
        and not isinstance(segment_start, bool)
        and isinstance(segment_end, int)
        and not isinstance(segment_end, bool)
        and segment_end > segment_start >= 0
        and isinstance(context_source_ranges, list)
        and context_source_ranges
    ):
        overlaps: list[tuple[str, int, int]] = []
        for item in context_source_ranges:
            if not isinstance(item, dict):
                continue
            try:
                context_start = int(item["context_start"])
                context_end = int(item["context_end"])
                source_start = int(item["source_start"])
            except (KeyError, TypeError, ValueError):
                continue
            overlap_start = max(segment_start, context_start)
            overlap_end = min(segment_end, context_end)
            if overlap_end <= overlap_start:
                continue
            block_id = str(item.get("source_block_id") or "")
            if not block_id:
                continue
            mapped_start = source_start + (overlap_start - context_start)
            mapped_end = source_start + (overlap_end - context_start)
            overlaps.append((block_id, mapped_start, mapped_end))
        block_ids = {item[0] for item in overlaps}
        if len(block_ids) == 1:
            return GeneratedSegmentSourceMapping(
                source_block_id=overlaps[0][0],
                source_block_ids=(overlaps[0][0],),
                start_char=min(item[1] for item in overlaps),
                end_char=max(item[2] for item in overlaps),
                status="eligible",
            )
        if len(block_ids) > 1:
            return GeneratedSegmentSourceMapping(
                source_block_id=None,
                source_block_ids=tuple(
                    dict.fromkeys(item[0] for item in overlaps)
                ),
                start_char=0,
                end_char=0,
                status="ambiguous_multi_source",
            )
        return GeneratedSegmentSourceMapping(None, (), 0, 0, "unmapped")

    needle = str(segment_text or "").strip()
    if not needle:
        return GeneratedSegmentSourceMapping(None, (), 0, 0, "unmapped")
    matches: list[tuple[str, int, int]] = []
    repeated_within_block = False
    for block in source_blocks:
        source_text = str(block.get("text") or "")
        first = source_text.find(needle)
        if first < 0:
            continue
        if source_text.find(needle, first + 1) >= 0:
            repeated_within_block = True
            continue
        block_id = str(block.get("block_id") or "")
        if not block_id:
            continue
        start = int(block.get("start_char") or 0) + first
        matches.append((block_id, start, start + len(needle)))
    if len(matches) == 1 and not repeated_within_block:
        block_id, start, end = matches[0]
        return GeneratedSegmentSourceMapping(
            block_id,
            (block_id,),
            start,
            end,
            "eligible",
        )
    if len({item[0] for item in matches}) > 1:
        return GeneratedSegmentSourceMapping(
            None,
            tuple(dict.fromkeys(item[0] for item in matches)),
            0,
            0,
            "ambiguous_multi_source",
        )
    return GeneratedSegmentSourceMapping(None, (), 0, 0, "unmapped")


def generated_segment_source_span(
    segment_text: str,
    source_blocks: list[dict[str, Any]],
) -> tuple[int, int]:
    """Return an exact, unique source span or an explicit unknown span."""

    mapping = generated_segment_source_mapping(segment_text, source_blocks)
    return mapping.start_char, mapping.end_char


def generated_source_block_match_status(
    source_block_id: Any,
    source_block_ids: Any,
    start_char: Any,
    end_char: Any,
) -> str:
    """Recompute generated lineage eligibility from fail-closed public fields."""

    block_id = str(source_block_id or "")
    block_ids = (
        tuple(str(item) for item in source_block_ids if str(item))
        if isinstance(source_block_ids, (list, tuple))
        else ()
    )
    if len(block_ids) > 1:
        return "ambiguous_multi_source"
    if len(block_ids) != 1 or not block_id or block_ids[0] != block_id:
        return "unmapped"
    if (
        isinstance(start_char, bool)
        or isinstance(end_char, bool)
        or not isinstance(start_char, int)
        or not isinstance(end_char, int)
        or start_char < 0
        or end_char <= start_char
    ):
        return "unmapped"
    return "eligible"


def _validated_generated_source_ranges(
    item: dict[str, Any],
    *,
    source_block_ids: tuple[str, ...],
    blocks_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    raw_ranges = item.get("context_source_ranges")
    if raw_ranges is None:
        return []
    if not isinstance(raw_ranges, list):
        raise ValueError("generated item source ranges are invalid")
    if not raw_ranges:
        return []
    context_text = str(item.get("context_text") or "")
    normalized: list[dict[str, Any]] = []
    previous_context_end = 0
    for raw in raw_ranges:
        if not isinstance(raw, dict):
            raise ValueError("generated item source ranges are invalid")
        block_id = str(raw.get("source_block_id") or "")
        values = [
            raw.get("context_start"),
            raw.get("context_end"),
            raw.get("source_start"),
            raw.get("source_end"),
        ]
        if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
            raise ValueError("generated item source ranges are invalid")
        context_start, context_end, source_start, source_end = values
        block = blocks_by_id.get(block_id)
        if block_id not in source_block_ids or not isinstance(block, dict):
            raise ValueError("generated item source ranges are invalid")
        block_text = str(block.get("text") or "")
        block_start = int(block.get("start_char") or 0)
        if (
            context_start < previous_context_end
            or context_end <= context_start
            or context_end > len(context_text)
            or source_start < block_start
            or source_end <= source_start
            or source_end > block_start + len(block_text)
            or context_end - context_start != source_end - source_start
        ):
            raise ValueError("generated item source ranges are invalid")
        source_offset = source_start - block_start
        if context_text[context_start:context_end] != block_text[
            source_offset : source_offset + (source_end - source_start)
        ]:
            raise ValueError("generated item source ranges are invalid")
        normalized.append(
            {
                "source_block_id": block_id,
                "context_start": context_start,
                "context_end": context_end,
                "source_start": source_start,
                "source_end": source_end,
            }
        )
        previous_context_end = context_end
    if tuple(item["source_block_id"] for item in normalized) != source_block_ids:
        raise ValueError("generated item source ranges are invalid")
    return normalized


__all__ = [
    "GENERATED_PARENT_IDENTITY_VERSION",
    "GENERATED_PARENT_WINDOW_IDENTITY_VERSION",
    "GeneratedSegmentSourceMapping",
    "canonical_source_text_hash",
    "generated_parent_identity",
    "generated_parent_window_identity",
    "generated_segment_source_mapping",
    "generated_segment_source_span",
    "generated_source_block_match_status",
    "resolve_generated_item_parent_identity",
]
