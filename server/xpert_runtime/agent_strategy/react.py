from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal


@dataclass(slots=True)
class ReActDecision:
    kind: Literal["action", "answer", "invalid"]
    answer: str = ""
    action: str = ""
    action_input: dict[str, Any] | None = None
    error: str = ""


def build_react_prompt(
    *,
    system_prompt: str,
    tools_text: str,
    tool_names: list[str],
) -> str:
    names = json.dumps(tool_names, ensure_ascii=False)
    return (
        f"{system_prompt.strip()}\n\n"
        "你可以使用下列工具完成任务。需要工具时，每轮只调用一个工具；"
        "不要展示隐藏推理。严格使用以下控制格式：\n"
        'Action: {"action":"工具名","action_input":{"参数":"值"}}\n'
        "工具结果会以 Observation 返回。完成时输出：\n"
        "FinalAnswer: 最终答案\n\n"
        f"允许的工具名：{names}\n"
        f"工具定义：\n{tools_text}"
    )


def parse_react_decision(raw_response: str) -> ReActDecision:
    cleaned = strip_think_blocks(raw_response).strip()
    answer_match = re.search(r"final\s*answer\s*:\s*", cleaned, re.IGNORECASE)
    if answer_match:
        answer = cleaned[answer_match.end() :].strip()
        if answer:
            return ReActDecision(kind="answer", answer=answer)
        return ReActDecision(kind="invalid", error="FinalAnswer 为空。")

    action_match = re.search(r"action\s*:\s*", cleaned, re.IGNORECASE)
    if action_match:
        fragment = cleaned[action_match.end() :].lstrip()
        try:
            value, _ = json.JSONDecoder().raw_decode(fragment)
        except ValueError:
            return ReActDecision(kind="invalid", error="Action JSON 无法解析。")
        if not isinstance(value, dict):
            return ReActDecision(kind="invalid", error="Action 必须是 JSON 对象。")
        action = str(value.get("action") or "").strip()
        action_input = value.get("action_input")
        if not action:
            return ReActDecision(kind="invalid", error="Action 缺少工具名。")
        if not isinstance(action_input, dict):
            return ReActDecision(kind="invalid", error="action_input 必须是 JSON 对象。")
        return ReActDecision(
            kind="action",
            action=action,
            action_input=dict(action_input),
        )

    if not cleaned:
        return ReActDecision(kind="invalid", error="模型返回为空。")
    if re.search(r"thought\s*:", cleaned, re.IGNORECASE):
        return ReActDecision(kind="invalid", error="模型只返回了 Thought，缺少 Action 或 FinalAnswer。")
    return ReActDecision(kind="answer", answer=cleaned)


def strip_think_blocks(value: str) -> str:
    cleaned = value
    previous = None
    while cleaned != previous:
        previous = cleaned
        cleaned = re.sub(
            r"<think>.*?</think>",
            "",
            cleaned,
            flags=re.IGNORECASE | re.DOTALL,
        )
    cleaned = re.sub(r"<think>.*$", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
    return cleaned
