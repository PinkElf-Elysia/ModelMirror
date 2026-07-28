from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal


CompressionProfile = Literal["auto", "off", "standard", "strong"]
ModelTextCallback = Callable[[str, list[dict[str, Any]], int], Awaitable[str]]
PersistSummaryCallback = Callable[[str, str, str | None], Awaitable[None]]

_CJK_PATTERN = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
_URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)
_CODE_FENCE_PATTERN = re.compile(r"```[\s\S]*?```")
_XML_PATTERN = re.compile(r"<[A-Za-z][^>]*>[\s\S]*?</[A-Za-z][^>]*>")
_CITATION_PATTERN = re.compile(
    r"(?:\[[0-9]{1,4}\]|\[source:[^\]]+\]|\bcitation[_ -]?id\b|\bsource[_ -]?id\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CompressionReport:
    applied: bool
    profile: CompressionProfile
    original_tokens: int
    final_tokens: int
    saved_tokens: int
    saved_ratio: float
    fidelity_status: Literal["passed", "not_needed", "fallback"]
    fallback_reason: str | None
    stages: tuple[str, ...]
    duration_ms: float
    summarized_messages: int = 0
    reused_summary: bool = False
    fits_context: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "profile": self.profile,
            "original_tokens": self.original_tokens,
            "final_tokens": self.final_tokens,
            "saved_tokens": self.saved_tokens,
            "saved_ratio": self.saved_ratio,
            "fidelity_status": self.fidelity_status,
            "fallback_reason": self.fallback_reason,
            "stages": list(self.stages),
            "duration_ms": self.duration_ms,
            "summarized_messages": self.summarized_messages,
            "reused_summary": self.reused_summary,
            "fits_context": self.fits_context,
        }


@dataclass(frozen=True)
class ContextOptimization:
    messages: list[dict[str, Any]]
    report: CompressionReport


def estimate_text_tokens(text: str) -> int:
    """Conservative tokenizer-free estimate for mixed CJK and Latin text."""

    if not text:
        return 0
    cjk_count = len(_CJK_PATTERN.findall(text))
    non_cjk_count = max(0, len(text) - cjk_count)
    return cjk_count + math.ceil(non_cjk_count / 4)


def message_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(content or "")


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    return sum(
        4 + estimate_text_tokens(message_content_text(message.get("content")))
        for message in messages
    )


def _looks_like_json(text: str) -> bool:
    stripped = text.strip()
    if not stripped or stripped[0] not in "[{" or stripped[-1] not in "]}":
        return False
    try:
        json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return False
    return True


def protected_markers(text: str) -> tuple[str, ...]:
    markers: list[str] = []
    markers.extend(_CODE_FENCE_PATTERN.findall(text))
    markers.extend(_URL_PATTERN.findall(text))
    markers.extend(_XML_PATTERN.findall(text))
    markers.extend(_CITATION_PATTERN.findall(text))
    if _looks_like_json(text):
        markers.append(text)
    return tuple(markers)


def _is_structured_or_referenced(text: str) -> bool:
    return bool(protected_markers(text))


def _latest_user_index(messages: list[dict[str, Any]]) -> int | None:
    return next(
        (
            index
            for index in range(len(messages) - 1, -1, -1)
            if messages[index].get("role") == "user"
        ),
        None,
    )


def _is_rag_message(message: dict[str, Any]) -> bool:
    metadata = message.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    return bool(
        message.get("rag_context")
        or str(message.get("name") or "").lower() == "rag_context"
        or str(metadata.get("kind") or "").lower() in {"rag", "rag_context"}
    )


def _is_tool_output(message: dict[str, Any]) -> bool:
    return bool(
        message.get("role") == "tool"
        or message.get("tool_call_id")
        or message.get("tool_output")
    )


def _dedupe_plain_lines(text: str) -> str:
    lines = text.splitlines()
    if len(lines) < 3:
        return text
    seen: set[str] = set()
    result: list[str] = []
    for line in lines:
        normalized = " ".join(line.split()).strip().lower()
        if normalized and len(normalized) >= 24 and normalized in seen:
            continue
        if normalized:
            seen.add(normalized)
        result.append(line.rstrip())
    return "\n".join(result)


def _collapse_repeated_sentences(text: str) -> str:
    parts = re.split(r"(?<=[。！？.!?])(\s+)", text)
    if len(parts) < 3:
        return text
    result: list[str] = []
    last_sentence = ""
    for index, part in enumerate(parts):
        if index % 2 == 1:
            if result:
                result.append(part)
            continue
        normalized = " ".join(part.split()).strip().lower()
        if normalized and normalized == last_sentence and len(normalized) >= 16:
            if result and result[-1].isspace():
                result.pop()
            continue
        if normalized:
            last_sentence = normalized
        result.append(part)
    return "".join(result)


def _dedupe_rag_blocks(text: str) -> str:
    blocks = re.split(r"\n\s*\n", text)
    if len(blocks) < 2:
        return text
    seen: set[str] = set()
    result: list[str] = []
    for block in blocks:
        normalized = " ".join(block.split()).strip().lower()
        if normalized and normalized in seen:
            continue
        if normalized:
            seen.add(normalized)
        result.append(block.strip())
    return "\n\n".join(result)


def deterministic_compress(
    messages: list[dict[str, Any]],
    *,
    max_tool_output_chars: int,
    strength: Literal["standard", "strong"],
) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    latest_user = _latest_user_index(messages)
    result: list[dict[str, Any]] = []
    stages: list[str] = []
    seen_message_content: set[str] = set()
    for index, message in enumerate(messages):
        copied = dict(message)
        content = copied.get("content")
        if not isinstance(content, str):
            result.append(copied)
            continue
        protected = (
            copied.get("role") == "system"
            or index == latest_user
            or _is_structured_or_referenced(content)
        )
        if protected:
            result.append(copied)
            continue

        prepared = content
        if (
            _is_tool_output(copied)
            and len(prepared) > max_tool_output_chars
            and "[工具输出已折叠]" not in prepared
        ):
            tail_chars = max(120, max_tool_output_chars // 5)
            head_chars = max_tool_output_chars - tail_chars
            prepared = (
                prepared[:head_chars].rstrip()
                + "\n[工具输出已折叠]\n"
                + prepared[-tail_chars:].lstrip()
            )
            stages.append("tool_output_filtering")

        line_deduped = _dedupe_plain_lines(prepared)
        if line_deduped != prepared:
            prepared = line_deduped
            stages.append("redundancy_folding")

        if strength == "strong":
            sentence_deduped = _collapse_repeated_sentences(prepared)
            if sentence_deduped != prepared:
                prepared = sentence_deduped
                stages.append("caveman_redundancy")

        if _is_rag_message(copied):
            rag_deduped = _dedupe_rag_blocks(prepared)
            if rag_deduped != prepared:
                prepared = rag_deduped
                stages.append("rag_deduplication")

        normalized_message = " ".join(prepared.split()).strip().lower()
        if (
            strength == "strong"
            and normalized_message
            and len(normalized_message) >= 80
            and normalized_message in seen_message_content
            and copied.get("role") in {"assistant", "tool"}
        ):
            prepared = "[重复内容已折叠，保留首次出现内容]"
            stages.append("cross_message_deduplication")
        elif normalized_message:
            seen_message_content.add(normalized_message)
        copied["content"] = prepared
        result.append(copied)
    return result, tuple(dict.fromkeys(stages))


def _fidelity_ok(
    original: list[dict[str, Any]],
    prepared: list[dict[str, Any]],
) -> bool:
    latest_user = _latest_user_index(original)
    prepared_contents = [
        message_content_text(message.get("content")) for message in prepared
    ]
    for index, message in enumerate(original):
        original_text = message_content_text(message.get("content"))
        if message.get("role") == "system" or index == latest_user:
            if original_text not in prepared_contents:
                return False
        for marker in protected_markers(original_text):
            if not any(marker in content for content in prepared_contents):
                return False
    return True


async def optimize_context(
    messages: list[dict[str, Any]],
    *,
    profile: CompressionProfile = "auto",
    max_context_tokens: int = 128_000,
    max_output_tokens: int = 2_048,
    safety_margin_tokens: int | None = None,
    trigger_ratio: float = 0.8,
    keep_recent_messages: int = 8,
    max_tool_output_chars: int = 4_000,
    summary_model_id: str = "",
    summary_max_tokens: int = 1_500,
    summarizer: ModelTextCallback | None = None,
    existing_summary: str = "",
    summary_boundary: str = "",
    persist_summary: PersistSummaryCallback | None = None,
) -> ContextOptimization:
    started_at = time.perf_counter()
    original = [dict(message) for message in messages]
    original_tokens = estimate_messages_tokens(original)
    margin = (
        max(256, int(max_context_tokens * 0.05))
        if safety_margin_tokens is None
        else max(0, int(safety_margin_tokens))
    )
    required_tokens = original_tokens + max_output_tokens + margin
    trigger = int(max_context_tokens * max(0.5, min(trigger_ratio, 0.95)))
    if profile == "off":
        return _result(
            original,
            original_tokens,
            profile,
            started_at,
            fidelity_status="not_needed",
            fallback_reason="disabled",
            fits_context=required_tokens <= max_context_tokens,
        )
    if profile == "auto" and required_tokens < trigger:
        return _result(
            original,
            original_tokens,
            profile,
            started_at,
            fidelity_status="not_needed",
            fits_context=True,
        )

    strength: Literal["standard", "strong"] = (
        "strong" if profile == "strong" else "standard"
    )
    deterministic, stages = deterministic_compress(
        original,
        max_tool_output_chars=max_tool_output_chars,
        strength=strength,
    )
    deterministic_tokens = estimate_messages_tokens(deterministic)
    deterministic_savings = (
        (original_tokens - deterministic_tokens) / original_tokens
        if original_tokens
        else 0.0
    )
    if deterministic_savings < 0.10:
        deterministic = original
        deterministic_tokens = original_tokens
        stages = ()

    final_messages = deterministic
    summarized_messages = 0
    reused_summary = False
    fallback_reason: str | None = None
    current_required = deterministic_tokens + max_output_tokens + margin
    if current_required > trigger:
        latest_user = _latest_user_index(deterministic)
        protected_indices = {
            index
            for index, message in enumerate(deterministic)
            if message.get("role") == "system"
            or index == latest_user
            or _is_structured_or_referenced(
                message_content_text(message.get("content"))
            )
        }
        non_system_indices = [
            index
            for index, message in enumerate(deterministic)
            if message.get("role") != "system"
        ]
        recent_indices = set(non_system_indices[-max(1, keep_recent_messages) :])
        retain_indices = protected_indices | recent_indices
        omitted_indices = [
            index
            for index in non_system_indices
            if index not in retain_indices
        ]
        if existing_summary and summary_boundary:
            boundary_index = next(
                (
                    index
                    for index in omitted_indices
                    if str(deterministic[index].get("message_id") or "")
                    == summary_boundary
                ),
                None,
            )
            if boundary_index is not None:
                omitted_indices = [
                    index for index in omitted_indices if index > boundary_index
                ]
            elif any(
                str(deterministic[index].get("message_id") or "")
                == summary_boundary
                for index in retain_indices
            ):
                omitted_indices = []

        summary = existing_summary.strip()
        if omitted_indices and summarizer is not None:
            source_text = "\n\n".join(
                f"{str(deterministic[index].get('role') or 'unknown')}: "
                f"{message_content_text(deterministic[index].get('content'))}"
                for index in omitted_indices
            )
            summary_prompt = (
                "Summarize older conversation context. Preserve decisions, "
                "constraints, identifiers, unfinished tasks and user preferences. "
                "Do not invent facts and do not rewrite quoted code or references.\n\n"
                + (f"Existing summary:\n{summary}\n\n" if summary else "")
                + f"Older messages:\n{source_text}"
            )
            try:
                summary = (
                    await summarizer(
                        summary_model_id,
                        [{"role": "user", "content": summary_prompt}],
                        summary_max_tokens,
                    )
                ).strip()
            except Exception:
                fallback_reason = "summary_failed"
            if summary:
                summarized_messages = len(omitted_indices)
                stages = (*stages, "history_summary")
                if persist_summary is not None:
                    try:
                        boundary = (
                            str(
                                deterministic[omitted_indices[-1]].get(
                                    "message_id"
                                )
                                or ""
                            )
                            or None
                        )
                        await persist_summary(
                            summary,
                            summary_model_id,
                            boundary,
                        )
                    except Exception:
                        fallback_reason = "summary_persistence_failed"
        elif summary:
            reused_summary = True
        elif omitted_indices:
            fallback_reason = "summary_unavailable"

        if summary:
            summary_message = {
                "role": "system",
                "content": f"Conversation summary (derived):\n{summary}",
                "metadata": {"context_engine_summary": True},
            }
            retained_system = [
                message
                for index, message in enumerate(deterministic)
                if index in retain_indices and message.get("role") == "system"
            ]
            retained_non_system = [
                message
                for index, message in enumerate(deterministic)
                if index in retain_indices and message.get("role") != "system"
            ]
            final_messages = [
                *retained_system,
                summary_message,
                *retained_non_system,
            ]

    final_tokens = estimate_messages_tokens(final_messages)
    saved_ratio = (
        (original_tokens - final_tokens) / original_tokens
        if original_tokens
        else 0.0
    )
    if not _fidelity_ok(original, final_messages):
        return _result(
            original,
            original_tokens,
            profile,
            started_at,
            fidelity_status="fallback",
            fallback_reason="fidelity_check_failed",
            fits_context=required_tokens <= max_context_tokens,
        )
    if final_messages != original and saved_ratio < 0.10:
        return _result(
            original,
            original_tokens,
            profile,
            started_at,
            fidelity_status="fallback",
            fallback_reason="insufficient_savings",
            fits_context=required_tokens <= max_context_tokens,
        )
    fits_context = (
        final_tokens + max_output_tokens + margin <= max_context_tokens
    )
    if not fits_context and fallback_reason is None:
        fallback_reason = "context_limit_exceeded"
    return _result(
        final_messages,
        original_tokens,
        profile,
        started_at,
        fidelity_status="passed" if final_messages != original else "not_needed",
        fallback_reason=fallback_reason,
        stages=stages,
        summarized_messages=summarized_messages,
        reused_summary=reused_summary,
        fits_context=fits_context,
    )


def _result(
    messages: list[dict[str, Any]],
    original_tokens: int,
    profile: CompressionProfile,
    started_at: float,
    *,
    fidelity_status: Literal["passed", "not_needed", "fallback"],
    fallback_reason: str | None = None,
    stages: tuple[str, ...] = (),
    summarized_messages: int = 0,
    reused_summary: bool = False,
    fits_context: bool,
) -> ContextOptimization:
    final_tokens = estimate_messages_tokens(messages)
    saved_tokens = max(0, original_tokens - final_tokens)
    return ContextOptimization(
        messages=messages,
        report=CompressionReport(
            applied=messages != [] and saved_tokens > 0,
            profile=profile,
            original_tokens=original_tokens,
            final_tokens=final_tokens,
            saved_tokens=saved_tokens,
            saved_ratio=(
                saved_tokens / original_tokens if original_tokens > 0 else 0.0
            ),
            fidelity_status=fidelity_status,
            fallback_reason=fallback_reason,
            stages=tuple(dict.fromkeys(stages)),
            duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
            summarized_messages=summarized_messages,
            reused_summary=reused_summary,
            fits_context=fits_context,
        ),
    )
