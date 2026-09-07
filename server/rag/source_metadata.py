from __future__ import annotations

import hashlib
import json
from typing import Any


MAX_HEADING_PATH_LEVELS = 12
MAX_HEADING_SEGMENT_CHARS = 200
MAX_HEADING_PATH_CHARS = 1_200


def heading_path_segments(value: Any) -> tuple[str, ...]:
    """Return cleaned source segments without applying persistence bounds."""

    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(
        raw_segment.strip()
        for raw_segment in value
        if isinstance(raw_segment, str) and raw_segment.strip()
    )


def heading_path_source_hash(value: Any) -> str:
    """Bind a bounded block to its complete cleaned source path without text leakage."""

    segments = heading_path_segments(value)
    if not segments:
        return ""
    canonical = json.dumps(
        list(segments),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def normalize_heading_path(value: Any) -> tuple[str, ...]:
    """Return a bounded heading path while preserving root and nearest leaf."""

    raw_segments = heading_path_segments(value)
    normalized = [
        segment[:MAX_HEADING_SEGMENT_CHARS] for segment in raw_segments
    ]
    if len(normalized) > MAX_HEADING_PATH_LEVELS:
        normalized = [
            normalized[0],
            *normalized[-(MAX_HEADING_PATH_LEVELS - 1) :],
        ]
    if sum(len(segment) for segment in normalized) <= MAX_HEADING_PATH_CHARS:
        return tuple(normalized)
    if len(normalized) <= 1:
        return tuple(normalized)

    root = normalized[0]
    leaf = normalized[-1]
    remaining = MAX_HEADING_PATH_CHARS - len(root) - len(leaf)
    nearest_middle: list[str] = []
    for segment in reversed(normalized[1:-1]):
        if remaining <= 0:
            break
        retained = segment[:remaining]
        if retained:
            nearest_middle.append(retained)
            remaining -= len(retained)
    return (root, *reversed(nearest_middle), leaf)


def heading_path_source_truncated(value: Any) -> bool:
    """Report whether the persisted path omits or truncates a source segment."""

    segments = heading_path_segments(value)
    return bool(segments) and segments != normalize_heading_path(segments)


def heading_path_boundary(value: Any) -> tuple[str, ...]:
    """Return a non-duplicated root/leaf boundary for a truncated source path."""

    segments = heading_path_segments(value)
    if not segments:
        return ()
    if len(segments) == 1 or segments[0] == segments[-1]:
        return (segments[0],)
    return (segments[0], segments[-1])


def encode_heading_path(value: Any) -> str:
    return json.dumps(
        list(normalize_heading_path(value)),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def decode_heading_path(value: Any) -> tuple[str, ...]:
    if value is None or value == "":
        return ()
    if isinstance(value, (list, tuple)):
        return normalize_heading_path(value)
    if not isinstance(value, str):
        return ()
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return ()
    return normalize_heading_path(parsed)


__all__ = [
    "MAX_HEADING_PATH_CHARS",
    "MAX_HEADING_PATH_LEVELS",
    "MAX_HEADING_SEGMENT_CHARS",
    "decode_heading_path",
    "encode_heading_path",
    "heading_path_boundary",
    "heading_path_source_hash",
    "heading_path_source_truncated",
    "heading_path_segments",
    "normalize_heading_path",
]
