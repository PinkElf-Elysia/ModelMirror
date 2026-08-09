from __future__ import annotations

import json
from typing import Any


MAX_HEADING_PATH_LEVELS = 12
MAX_HEADING_SEGMENT_CHARS = 200
MAX_HEADING_PATH_CHARS = 1_200


def normalize_heading_path(value: Any) -> tuple[str, ...]:
    """Return a bounded heading path safe for indexes and API payloads."""

    if not isinstance(value, (list, tuple)):
        return ()
    normalized: list[str] = []
    retained_chars = 0
    for raw_segment in value:
        if len(normalized) >= MAX_HEADING_PATH_LEVELS:
            break
        if not isinstance(raw_segment, str):
            continue
        segment = raw_segment.strip()
        if not segment:
            continue
        remaining = MAX_HEADING_PATH_CHARS - retained_chars
        if remaining <= 0:
            break
        segment = segment[: min(MAX_HEADING_SEGMENT_CHARS, remaining)]
        if not segment:
            break
        normalized.append(segment)
        retained_chars += len(segment)
    return tuple(normalized)


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
    "normalize_heading_path",
]
