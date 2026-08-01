from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit


_JSON_OBJECT_PATTERN = re.compile(r"\{.*\}", re.DOTALL)


def normalize_file_extensions(values: Iterable[str]) -> set[str]:
    extensions: set[str] = set()
    for value in values:
        clean = str(value or "").strip().lower()
        if not clean:
            continue
        extensions.add(clean if clean.startswith(".") else f".{clean}")
    return extensions


def validate_selected_files(
    assets: Iterable[Any],
    *,
    enabled: bool,
    max_files: int,
    allowed_extensions: Iterable[str],
) -> None:
    selected = list(assets)
    if selected and not enabled:
        raise ValueError("This Xpert version does not allow file input.")
    if len(selected) > max_files:
        raise ValueError(
            f"This Xpert version accepts at most {max_files} files per run."
        )
    allowed = normalize_file_extensions(allowed_extensions)
    for asset in selected:
        extension = str(
            getattr(asset, "extension", None)
            or Path(str(getattr(asset, "filename", ""))).suffix
        ).lower()
        if extension not in allowed:
            raise ValueError(
                f"File type {extension or '<none>'} is not allowed by this Xpert version."
            )


def parse_conversation_enrichment(
    raw_text: str,
    *,
    suggestion_limit: int,
) -> tuple[str, list[str]]:
    match = _JSON_OBJECT_PATTERN.search(str(raw_text or ""))
    if not match:
        return "", []
    try:
        payload = json.loads(match.group(0))
    except (TypeError, ValueError, json.JSONDecodeError):
        return "", []
    if not isinstance(payload, dict):
        return "", []
    title = str(payload.get("title") or "").strip().replace("\n", " ")[:120]
    raw_suggestions = payload.get("suggestions")
    suggestions: list[str] = []
    if isinstance(raw_suggestions, list):
        for item in raw_suggestions:
            clean = str(item or "").strip()[:500]
            if clean and clean not in suggestions:
                suggestions.append(clean)
            if len(suggestions) >= max(1, min(suggestion_limit, 6)):
                break
    return title, suggestions


def deterministic_memory_reply(
    query: str,
    memories: Iterable[Any],
    *,
    min_confidence: float,
) -> tuple[str, str, float] | None:
    normalized_query = _normalize_text(query)
    if len(normalized_query) < 3:
        return None
    threshold = max(0.8, min(float(min_confidence), 1.0))
    best: tuple[float, Any] | None = None
    for item in memories:
        if str(getattr(item, "status", "active")) != "active":
            continue
        content = str(getattr(item, "content", "") or "").strip()
        title = str(getattr(item, "title", "") or "").strip()
        summary = str(getattr(item, "summary", "") or "").strip()
        tags = [str(value or "").strip() for value in getattr(item, "tags", [])]
        normalized_content = _normalize_text(content)
        normalized_title = _normalize_text(title)
        normalized_summary = _normalize_text(summary)
        normalized_tags = {_normalize_text(value) for value in tags if value}
        score = 0.0
        if normalized_query in {normalized_content, normalized_title, normalized_summary}:
            score = 1.0
        elif normalized_title and (
            normalized_title in normalized_query
            or normalized_query in normalized_title
        ):
            score = 0.97
        elif normalized_query in normalized_tags:
            score = 0.95
        explicit_confidence = getattr(item, "confidence", None)
        if explicit_confidence is not None:
            score = min(score, max(0.0, min(float(explicit_confidence), 1.0)))
        if score >= threshold and (best is None or score > best[0]):
            best = (score, item)
    if best is None:
        return None
    item = best[1]
    answer = str(
        getattr(item, "summary", "")
        or getattr(item, "content", "")
    ).strip()
    if not answer:
        return None
    return str(getattr(item, "memory_id", "")), answer[:8_000], best[0]


def gateway_audio_endpoint(chat_completions_url: str, endpoint: str) -> str:
    split = urlsplit(str(chat_completions_url or "").strip())
    if split.scheme not in {"http", "https"} or not split.netloc:
        raise ValueError("The configured LLM gateway URL is invalid.")
    path = split.path.rstrip("/")
    suffix = "/chat/completions"
    if path.endswith(suffix):
        path = path[: -len(suffix)]
    endpoint_path = f"{path.rstrip('/')}/{endpoint.lstrip('/')}"
    return urlunsplit((split.scheme, split.netloc, endpoint_path, "", ""))


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())
