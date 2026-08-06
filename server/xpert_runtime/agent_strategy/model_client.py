from __future__ import annotations

from typing import Any

import httpx

from .models import AgentModelError, AgentModelTurn, AgentToolCall, AgentUsage


class OpenAICompatibleAgentModelClient:
    """Small Chat Completions adapter dedicated to agent tool-call turns."""

    def __init__(
        self,
        *,
        endpoint: str,
        headers: dict[str, str],
        client_kwargs: dict[str, Any] | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.headers = dict(headers)
        self.client_kwargs = dict(client_kwargs or {})

    async def complete(
        self,
        *,
        model_id: str,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
        parallel_tool_calls: bool | None = None,
    ) -> AgentModelTurn:
        payload: dict[str, Any] = {
            "model": model_id,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if tools is not None:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        if parallel_tool_calls is not None:
            payload["parallel_tool_calls"] = parallel_tool_calls

        async with httpx.AsyncClient(**self.client_kwargs) as client:
            response = await client.post(
                self.endpoint,
                headers=self.headers,
                json=payload,
            )

        if response.status_code >= 400:
            raise _model_error(response)
        try:
            data = response.json()
        except ValueError as exc:
            raise AgentModelError("模型返回了无法解析的 JSON 响应。") from exc
        if not isinstance(data, dict):
            raise AgentModelError("模型返回了无法解析的响应对象。")
        return parse_chat_completion(data)


def parse_chat_completion(payload: dict[str, Any]) -> AgentModelTurn:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise AgentModelError("模型响应缺少 choices。")
    choice = choices[0]
    if not isinstance(choice, dict):
        raise AgentModelError("模型响应 choices[0] 格式错误。")
    message = choice.get("message")
    if not isinstance(message, dict):
        message = choice.get("delta")
    if not isinstance(message, dict):
        raise AgentModelError("模型响应缺少 assistant message。")

    calls: list[AgentToolCall] = []
    raw_calls = message.get("tool_calls")
    if isinstance(raw_calls, list):
        for index, raw_call in enumerate(raw_calls):
            if not isinstance(raw_call, dict):
                continue
            function = raw_call.get("function")
            if not isinstance(function, dict):
                continue
            name = str(function.get("name") or "").strip()
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                raw_arguments = arguments
            elif isinstance(arguments, dict):
                import json

                raw_arguments = json.dumps(arguments, ensure_ascii=False)
            else:
                raw_arguments = "{}"
            calls.append(
                AgentToolCall(
                    call_id=str(raw_call.get("id") or f"call_{index + 1}"),
                    name=name,
                    raw_arguments=raw_arguments,
                )
            )

    usage_raw = payload.get("usage")
    usage = AgentUsage()
    if isinstance(usage_raw, dict):
        usage = AgentUsage(
            prompt_tokens=_safe_int(usage_raw.get("prompt_tokens")),
            completion_tokens=_safe_int(usage_raw.get("completion_tokens")),
            total_tokens=_safe_int(usage_raw.get("total_tokens")),
        )

    return AgentModelTurn(
        content=_content_text(message.get("content")),
        tool_calls=calls,
        finish_reason=(
            str(choice.get("finish_reason"))
            if choice.get("finish_reason") is not None
            else None
        ),
        usage=usage,
        raw=payload,
    )


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


def _model_error(response: httpx.Response) -> AgentModelError:
    message = f"模型网关请求失败：HTTP {response.status_code}"
    code: str | None = None
    param: str | None = None
    try:
        data = response.json()
    except ValueError:
        data = None
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            if error.get("message"):
                message = str(error["message"])
            if error.get("code") is not None:
                code = str(error["code"])
            if error.get("param") is not None:
                param = str(error["param"])
        elif data.get("message"):
            message = str(data["message"])
    return AgentModelError(
        message,
        status_code=response.status_code,
        code=code,
        param=param,
    )


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
