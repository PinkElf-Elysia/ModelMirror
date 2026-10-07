from __future__ import annotations

from collections.abc import Callable
from typing import Any


class PlannerCompletionError(RuntimeError):
    status_code = 502

    def __init__(self, code: str, message: str, diagnostics: dict[str, Any]) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message
        self.diagnostics = diagnostics


def _token_count(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def validate_planner_completion(
    payload: dict[str, Any],
    *,
    max_tokens: int,
    observer: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Check completion before JSON parsing; disclose counters, never model text."""
    choices = payload.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices else {}
    choice = choice if isinstance(choice, dict) else {}
    reason = choice.get("finish_reason")
    reason = reason.strip() if isinstance(reason, str) else ""
    reason = reason if reason in {"stop", "length", "error", "content_filter", "tool_calls"} else (
        "missing" if not reason else "unrecognized"
    )
    message = choice.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    usage = payload.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    details = usage.get("completion_tokens_details")
    details = details if isinstance(details, dict) else {}
    provider_error = payload.get("error") is not None or choice.get("error") is not None
    diagnostics = {
        "finish_reason": reason,
        "requested_max_tokens": max_tokens,
        "content_chars": len(content) if isinstance(content, str) else 0,
        "provider_error": provider_error,
        **{key: _token_count(usage.get(key)) for key in (
            "prompt_tokens", "completion_tokens", "total_tokens",
        )},
        "reasoning_tokens": _token_count(details.get("reasoning_tokens")),
    }
    code = None
    if reason == "length":
        code = "meta_planner_output_truncated"
        public_message = "模型输出达到 Token 上限并被截断，未作为完整规划结果接受；已停止生成，未自动修复或重试。"
    elif reason != "stop" or provider_error:
        code = "meta_planner_completion_incomplete"
        public_message = "模型未返回可验证的正常完成状态，未作为完整规划结果接受；已停止生成，未自动修复或重试。"
    if code:
        diagnostics["error_code"] = code
    if observer is not None:
        observer(dict(diagnostics))
    if code:
        raise PlannerCompletionError(code, public_message, diagnostics)
    return diagnostics
