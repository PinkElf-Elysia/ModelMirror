from __future__ import annotations

import inspect
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx


DeltaCallback = Callable[[str, dict[str, Any]], Awaitable[None] | None]


class GatewayError(RuntimeError):
    pass


class GatewayNotConfiguredError(GatewayError):
    pass


class GatewayCapabilityError(GatewayError):
    pass


class GatewayRequestError(GatewayError):
    pass


@dataclass(frozen=True, slots=True)
class NativeToolCall:
    call_id: str
    name: str
    arguments: str

    def as_message_value(self) -> dict[str, Any]:
        return {
            "id": self.call_id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments},
        }


@dataclass(frozen=True, slots=True)
class GatewayTurn:
    content: str
    tool_calls: tuple[NativeToolCall, ...]
    finish_reason: str
    model_id: str


class OpenAICompatibleGateway:
    """Raw OpenAI-compatible Chat Completions client for native tool calling."""

    def __init__(
        self,
        *,
        gateway_url: str | None = None,
        gateway_key: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._gateway_url = gateway_url
        self._gateway_key = gateway_key
        self._transport = transport

    def configuration(self) -> tuple[str, str, str]:
        if self._gateway_url is not None:
            url = self._gateway_url.strip()
            key = (self._gateway_key or "").strip()
        else:
            local_url = os.getenv(
                "LLM_GATEWAY_URL", "http://localhost:3000/v1/chat/completions"
            ).strip()
            local_key = os.getenv("LLM_GATEWAY_KEY", "").strip()
            openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
            if local_url and local_key:
                url, key = local_url, local_key
            elif openrouter_key:
                url = "https://openrouter.ai/api/v1/chat/completions"
                key = openrouter_key
            else:
                url, key = "", ""
        if not url or not key:
            raise GatewayNotConfiguredError(
                "LLM 网关未配置，请设置 LLM_GATEWAY_KEY 或 OPENROUTER_API_KEY。"
            )
        provider = "newAPI" if any(
            marker in url.lower() for marker in ("new-api", "localhost:3000", "127.0.0.1:3000")
        ) else "OpenAI-compatible"
        return url, key, provider

    async def stream_turn(
        self,
        *,
        model_id: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
        thinking_level: str,
        timeout_ms: int,
        on_delta: DeltaCallback,
    ) -> GatewayTurn:
        url, key, _ = self.configuration()
        payload: dict[str, Any] = {
            "model": model_id,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if thinking_level in {"low", "medium", "high", "xhigh"}:
            payload["reasoning"] = {"effort": thinking_level}
        timeout = httpx.Timeout(
            connect=min(15.0, timeout_ms / 1000),
            read=timeout_ms / 1000,
            write=30.0,
            pool=10.0,
        )
        kwargs: dict[str, Any] = {"timeout": timeout}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        async with httpx.AsyncClient(**kwargs) as client:
            async with client.stream(
                "POST",
                url,
                headers=self._headers(key),
                json=payload,
            ) as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    self._raise_upstream(response.status_code, body)
                content_parts: list[str] = []
                calls: dict[int, dict[str, str]] = {}
                finish_reason = ""
                saw_event = False
                async for line in response.aiter_lines():
                    clean = line.strip()
                    if not clean or clean.startswith(":"):
                        continue
                    if clean == "data: [DONE]":
                        break
                    raw = clean[5:].strip() if clean.startswith("data:") else clean
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    saw_event = True
                    if isinstance(event, dict) and isinstance(event.get("error"), dict):
                        message = str(event["error"].get("message") or "模型流返回错误")
                        self._raise_message(message)
                    choices = event.get("choices") if isinstance(event, dict) else None
                    if not isinstance(choices, list) or not choices:
                        continue
                    choice = choices[0] if isinstance(choices[0], dict) else {}
                    finish_reason = str(choice.get("finish_reason") or finish_reason)
                    delta = choice.get("delta")
                    if not isinstance(delta, dict):
                        message = choice.get("message")
                        if isinstance(message, dict):
                            delta = message
                        else:
                            continue
                    text = self._text(delta.get("content"))
                    if text:
                        content_parts.append(text)
                        await self._emit(on_delta, "text_delta", {"delta": text})
                    thought = self._text(
                        delta.get("reasoning_content", delta.get("reasoning"))
                    )
                    if thought:
                        await self._emit(on_delta, "thinking_delta", {"delta": thought})
                    raw_calls = delta.get("tool_calls")
                    if isinstance(raw_calls, list):
                        for raw_call in raw_calls:
                            if not isinstance(raw_call, dict):
                                continue
                            index = int(raw_call.get("index") or 0)
                            accumulated = calls.setdefault(
                                index, {"id": "", "name": "", "arguments": ""}
                            )
                            if raw_call.get("id"):
                                accumulated["id"] = str(raw_call["id"])
                            function = raw_call.get("function")
                            if isinstance(function, dict):
                                if function.get("name"):
                                    accumulated["name"] += str(function["name"])
                                if function.get("arguments"):
                                    argument_delta = str(function["arguments"])
                                    accumulated["arguments"] += argument_delta
                                    await self._emit(
                                        on_delta,
                                        "tool_call_delta",
                                        {"index": index, "delta": argument_delta},
                                    )
        if not saw_event:
            raise GatewayRequestError("模型没有返回可解析的流式响应。")
        tool_calls: list[NativeToolCall] = []
        for index in sorted(calls):
            item = calls[index]
            if not item["id"] or not item["name"]:
                raise GatewayRequestError("模型返回了不完整的 Tool Call。")
            tool_calls.append(
                NativeToolCall(
                    call_id=item["id"],
                    name=item["name"],
                    arguments=item["arguments"] or "{}",
                )
            )
        if finish_reason == "tool_calls" and not tool_calls:
            raise GatewayRequestError("模型声明了 Tool Call，但未返回可执行参数。")
        return GatewayTurn(
            content="".join(content_parts),
            tool_calls=tuple(tool_calls),
            finish_reason=finish_reason or ("tool_calls" if tool_calls else "stop"),
            model_id=model_id,
        )

    async def describe_image(
        self,
        *,
        image_path: Path,
        data_url: str,
        prompt: str,
        timeout_ms: int,
    ) -> str:
        url, key, _ = self.configuration()
        model_id = os.getenv(
            "OPENROUTER_VISION_FALLBACK_MODEL", "qwen/qwen2.5-vl-72b-instruct"
        ).strip()
        payload = {
            "model": model_id,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt or "请准确描述这张图片。"},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            "max_tokens": 2_048,
            "stream": False,
        }
        timeout = httpx.Timeout(connect=15, read=timeout_ms / 1000, write=30, pool=10)
        kwargs: dict[str, Any] = {"timeout": timeout}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        async with httpx.AsyncClient(**kwargs) as client:
            response = await client.post(url, headers=self._headers(key), json=payload)
        if response.status_code >= 400:
            self._raise_upstream(response.status_code, response.content)
        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise GatewayRequestError("视觉模型返回了无法解析的响应。") from exc
        text = self._text(content).strip()
        if not text:
            raise GatewayRequestError(f"视觉模型未描述图片 {image_path.name}。")
        return text

    @staticmethod
    async def _emit(callback: DeltaCallback, kind: str, payload: dict[str, Any]) -> None:
        result = callback(kind, payload)
        if inspect.isawaitable(result):
            await result

    @staticmethod
    def _headers(key: str) -> dict[str, str]:
        title = os.getenv("OPENROUTER_APP_TITLE", "ModelMirror").strip() or "ModelMirror"
        referer = os.getenv("OPENROUTER_HTTP_REFERER", "http://localhost:5173").strip()
        return {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": referer,
            "X-Title": title,
            "X-OpenRouter-Title": title,
        }

    @classmethod
    def _raise_upstream(cls, status_code: int, body: bytes) -> None:
        try:
            payload = json.loads(body.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            payload = {}
        error = payload.get("error") if isinstance(payload, dict) else None
        message = str(error.get("message") if isinstance(error, dict) else "").strip()
        cls._raise_message(message or f"模型服务返回 HTTP {status_code}。", status_code)

    @staticmethod
    def _raise_message(message: str, status_code: int | None = None) -> None:
        lowered = message.lower()
        tool_markers = ("tool", "function call", "function_call", "functions")
        unsupported_markers = (
            "not support",
            "unsupported",
            "does not support",
            "not available",
            "unknown field",
        )
        if any(marker in lowered for marker in tool_markers) and any(
            marker in lowered for marker in unsupported_markers
        ):
            raise GatewayCapabilityError(
                "当前模型或网关不支持原生 Tool Calling，请选择支持 tools 的聊天模型。"
            )
        if status_code in {401, 403}:
            raise GatewayRequestError("模型服务认证失败，请检查现有网关配置。")
        raise GatewayRequestError(message)

    @staticmethod
    def _text(value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            return "".join(parts)
        if isinstance(value, dict) and isinstance(value.get("text"), str):
            return value["text"]
        return ""
