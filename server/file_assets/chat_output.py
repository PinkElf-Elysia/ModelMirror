from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from .output_contracts import FileOutputResponse
from .output_renderer import MAX_RENDER_SPEC_BYTES
from .output_service import FileOutputService


CREATE_FILE_TOOL_NAME = "modelmirror_create_file"
OPENROUTER_CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"
_VERIFIED_CHAT_OUTPUT_TARGETS = {
    "openai/gpt-5.6-luna": "openai",
}
ChatOutputResponseSender = Callable[
    [httpx.AsyncClient, dict[str, Any]], Awaitable[httpx.Response]
]
_SANDBOX_MARKDOWN_LINK_RE = re.compile(
    r"\[[^\]\r\n]{0,500}\]\(\s*sandbox:[^)\r\n]{1,2000}\)",
    re.IGNORECASE,
)
_SANDBOX_URI_RE = re.compile(r"sandbox:/[^\s)\]\r\n]{1,2000}", re.IGNORECASE)
CREATE_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": CREATE_FILE_TOOL_NAME,
        "description": (
            "Create at most one downloadable file for this turn from a bounded "
            "structured specification. This tool cannot access paths, shell, URLs, "
            "or raw binary data."
        ),
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": ["format_id", "filename"],
            "properties": {
                "format_id": {
                    "type": "string",
                    "enum": [
                        "plain_text", "markdown", "json", "csv",
                        "pdf", "docx", "xlsx", "pptx",
                    ],
                },
                "filename": {"type": "string", "minLength": 1, "maxLength": 160},
                "title": {"type": "string", "maxLength": 2000},
                "content": {},
                "rows": {"type": "array", "maxItems": 100000},
                "blocks": {"type": "array", "maxItems": 10000},
                "sheets": {"type": "array", "maxItems": 20},
                "slides": {"type": "array", "maxItems": 100},
            },
        },
    },
}


class ChatOutputError(RuntimeError):
    def __init__(
        self,
        status_code: int,
        error_code: str,
        message: str,
        *,
        upstream_status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.message = message
        self.upstream_status_code = upstream_status_code


@dataclass(frozen=True, slots=True)
class _ToolCall:
    call_id: str
    name: str
    arguments_json: str


@dataclass(frozen=True, slots=True)
class _StreamResult:
    text_chunks: tuple[str, ...]
    tool_calls: tuple[_ToolCall, ...]
    finish_reason: str | None
    actual_model: str | None
    usage: dict[str, int]
    request_id: str | None


@dataclass(frozen=True, slots=True)
class ChatOutputResult:
    text_chunks: tuple[str, ...]
    output: FileOutputResponse | None
    actual_model: str
    usage: dict[str, int]
    request_id: str | None


async def run_chat_output_turn(
    *,
    url: str,
    key: str,
    headers: dict[str, str],
    client_kwargs: dict[str, Any],
    model_id: str,
    messages: list[dict[str, Any]],
    temperature: float,
    max_tokens: int,
    top_p: float | None,
    seed: int | None,
    stop: list[str] | None,
    output_service: FileOutputService,
    scope_id: str,
    output_context_id: str,
    provider_tag: str | None = None,
    response_sender: ChatOutputResponseSender | None = None,
) -> ChatOutputResult:
    request_payload: dict[str, Any] = {
        "model": model_id,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
        "tools": [CREATE_FILE_TOOL],
        "tool_choice": "auto",
        "parallel_tool_calls": False,
    }
    if provider_tag:
        request_payload["provider"] = {
            "only": [provider_tag],
            "allow_fallbacks": False,
        }
    if top_p is not None:
        request_payload["top_p"] = top_p
    if seed is not None:
        request_payload["seed"] = seed
    if stop:
        request_payload["stop"] = stop

    async with httpx.AsyncClient(**client_kwargs) as client:
        first = await _stream_once(
            client,
            url=url,
            key=key,
            headers=headers,
            payload=request_payload,
            response_sender=response_sender,
        )
        _require_exact_model(first.actual_model, model_id)
        if len(first.tool_calls) > 1:
            raise ChatOutputError(
                422,
                "output_tool_call_limit_exceeded",
                "The model requested more than one output file in one turn.",
            )
        if not first.tool_calls:
            if not first.text_chunks:
                raise ChatOutputError(502, "output_chat_empty", "The model returned no usable content.")
            return ChatOutputResult(
                text_chunks=first.text_chunks,
                output=None,
                actual_model=first.actual_model or model_id,
                usage=first.usage,
                request_id=first.request_id,
            )
        if first.finish_reason != "tool_calls":
            raise ChatOutputError(502, "output_tool_stream_incomplete", "The model tool stream did not finish safely.")

        tool_call = first.tool_calls[0]
        if tool_call.name != CREATE_FILE_TOOL_NAME:
            raise ChatOutputError(422, "output_tool_not_allowed", "The model requested an unapproved tool.")
        try:
            specification = json.loads(tool_call.arguments_json)
        except json.JSONDecodeError as exc:
            raise ChatOutputError(422, "output_tool_arguments_invalid", "The model returned an invalid file specification.") from exc
        if not isinstance(specification, dict):
            raise ChatOutputError(422, "output_tool_arguments_invalid", "The model returned an invalid file specification.")
        producer_digest = hashlib.sha256(
            (
                output_context_id
                + "\0"
                + model_id
                + "\0"
                + tool_call.call_id
                + "\0"
                + hashlib.sha256(tool_call.arguments_json.encode("utf-8")).hexdigest()
            ).encode("utf-8")
        ).hexdigest()
        output = output_service.render_spec(
            specification,
            purpose="chat",
            scope_id=scope_id,
            producer_kind="chat_tool",
            producer_artifact_id="chat_output_" + producer_digest,
            source_message_id=output_context_id,
        )

        assistant_message: dict[str, Any] = {
            "role": "assistant",
            "content": "".join(first.text_chunks) or None,
            "tool_calls": [
                {
                    "id": tool_call.call_id,
                    "type": "function",
                    "function": {
                        "name": tool_call.name,
                        "arguments": tool_call.arguments_json,
                    },
                }
            ],
        }
        tool_message = {
            "role": "tool",
            "tool_call_id": tool_call.call_id,
            "name": CREATE_FILE_TOOL_NAME,
            "content": json.dumps(
                {
                    "status": output.status,
                    "output_id": output.output_id,
                    "filename": output.display_name,
                    "error_code": output.error_code,
                },
                separators=(",", ":"),
            ),
        }
        second_payload = {
            **request_payload,
            "messages": [*messages, assistant_message, tool_message],
            "tool_choice": "none",
        }
        try:
            second = await _stream_once(
                client,
                url=url,
                key=key,
                headers=headers,
                payload=second_payload,
                response_sender=response_sender,
            )
        except ChatOutputError:
            text_chunks = tuple(first.text_chunks) or (
                "The file was processed, but the model's final text response was interrupted.",
            )
            return ChatOutputResult(
                text_chunks=text_chunks,
                output=output,
                actual_model=first.actual_model or model_id,
                usage=first.usage,
                request_id=first.request_id,
            )
        _require_exact_model(second.actual_model, model_id)
        if second.tool_calls:
            raise ChatOutputError(422, "output_tool_repeated", "The model attempted another file tool call.")
        text_chunks = _sanitize_output_file_references(
            (*first.text_chunks, *second.text_chunks),
            filename=output.display_name,
        )
        if not text_chunks:
            text_chunks = (
                "The requested file was processed."
                if output.status == "completed"
                else "The response completed, but the requested file could not be rendered.",
            )
        usage = _sum_usage(first.usage, second.usage)
        return ChatOutputResult(
            text_chunks=tuple(text_chunks),
            output=output,
            actual_model=second.actual_model or first.actual_model or model_id,
            usage=usage,
            request_id=second.request_id or first.request_id,
        )


def _sanitize_output_file_references(
    chunks: tuple[str, ...],
    *,
    filename: str,
) -> tuple[str, ...]:
    text = "".join(chunks)
    if not text:
        return ()
    safe_filename = str(filename or "generated file").replace("`", "").strip()
    if not safe_filename:
        safe_filename = "generated file"
    replacement = f"`{safe_filename}`"
    text = _SANDBOX_MARKDOWN_LINK_RE.sub(replacement, text)
    text = _SANDBOX_URI_RE.sub(replacement, text)
    return (text,)


async def _stream_once(
    client: httpx.AsyncClient,
    *,
    url: str,
    key: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    response_sender: ChatOutputResponseSender | None = None,
) -> _StreamResult:
    response = (
        await response_sender(client, payload)
        if response_sender is not None
        else await client.send(
            client.build_request("POST", url, headers=headers, json=payload),
            stream=True,
        )
    )
    if response.status_code >= 400:
        upstream_status_code = response.status_code
        await response.aclose()
        raise ChatOutputError(
            502,
            "output_model_upstream_error",
            "The selected model connection rejected the file-output request.",
            upstream_status_code=(
                upstream_status_code if response_sender is not None else None
            ),
        )
    text_chunks: list[str] = []
    calls: dict[int, dict[str, str]] = {}
    finish_reason: str | None = None
    actual_model: str | None = None
    usage: dict[str, int] = {}
    buffer = ""
    try:
        async for chunk in response.aiter_text():
            if not chunk:
                continue
            buffer += chunk
            events = buffer.split("\n\n")
            buffer = events.pop()
            for event in events:
                _consume_event(event, text_chunks=text_chunks, calls=calls)
                event_finish, event_model, event_usage = _event_state(event)
                finish_reason = event_finish or finish_reason
                actual_model = event_model or actual_model
                usage = _sum_usage(usage, event_usage)
        if buffer.strip():
            _consume_event(buffer, text_chunks=text_chunks, calls=calls)
            event_finish, event_model, event_usage = _event_state(buffer)
            finish_reason = event_finish or finish_reason
            actual_model = event_model or actual_model
            usage = _sum_usage(usage, event_usage)
    except ChatOutputError:
        raise
    except httpx.HTTPError as exc:
        raise ChatOutputError(503, "output_model_stream_interrupted", "The selected model stream was interrupted.") from exc
    finally:
        await response.aclose()
    if not finish_reason:
        raise ChatOutputError(503, "output_model_stream_interrupted", "The selected model stream ended without a terminal reason.")
    tool_calls = tuple(
        _ToolCall(
            call_id=value.get("id") or f"call_{index}",
            name=value.get("name") or "",
            arguments_json=value.get("arguments") or "",
        )
        for index, value in sorted(calls.items())
    )
    return _StreamResult(
        text_chunks=tuple(text_chunks),
        tool_calls=tool_calls,
        finish_reason=finish_reason,
        actual_model=actual_model,
        usage=usage,
        request_id=response.headers.get("x-request-id"),
    )


def _consume_event(
    event: str,
    *,
    text_chunks: list[str],
    calls: dict[int, dict[str, str]],
) -> None:
    for raw_line in event.splitlines():
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        choices = payload.get("choices")
        if not isinstance(choices, list):
            continue
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                delta = choice.get("message") if isinstance(choice.get("message"), dict) else {}
            content = delta.get("content")
            if isinstance(content, str) and content:
                text_chunks.append(content)
            raw_calls = delta.get("tool_calls")
            if not isinstance(raw_calls, list):
                continue
            for raw_call in raw_calls:
                if not isinstance(raw_call, dict):
                    continue
                index = raw_call.get("index", 0)
                if not isinstance(index, int) or index < 0 or index > 4:
                    raise ChatOutputError(422, "output_tool_call_invalid", "The model returned an invalid tool call.")
                current = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                if isinstance(raw_call.get("id"), str):
                    current["id"] += raw_call["id"]
                function = raw_call.get("function")
                if isinstance(function, dict):
                    if isinstance(function.get("name"), str):
                        current["name"] += function["name"]
                    if isinstance(function.get("arguments"), str):
                        current["arguments"] += function["arguments"]
                        if len(current["arguments"].encode("utf-8")) > MAX_RENDER_SPEC_BYTES:
                            raise ChatOutputError(413, "output_tool_arguments_too_large", "The model file specification exceeded 2 MiB.")


def _event_state(event: str) -> tuple[str | None, str | None, dict[str, int]]:
    finish: str | None = None
    model: str | None = None
    usage: dict[str, int] = {}
    for raw_line in event.splitlines():
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if isinstance(payload.get("model"), str):
            model = payload["model"]
        raw_usage = payload.get("usage")
        if isinstance(raw_usage, dict):
            usage = {
                str(key): int(value)
                for key, value in raw_usage.items()
                if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0
            }
        choices = payload.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                if isinstance(choice, dict) and choice.get("finish_reason"):
                    finish = str(choice["finish_reason"])
    return finish, model, usage


def _sum_usage(left: dict[str, int], right: dict[str, int]) -> dict[str, int]:
    keys = set(left) | set(right)
    return {key: max(0, int(left.get(key, 0))) + max(0, int(right.get(key, 0))) for key in keys}


def _require_exact_model(actual_model: str | None, requested_model: str) -> None:
    if actual_model is not None and actual_model != requested_model:
        raise ChatOutputError(
            502,
            "output_model_replaced",
            "The provider replaced the selected model, so file output was not accepted.",
        )


def verified_chat_output_provider(*, model_id: str, gateway_url: str) -> str | None:
    normalized_url = str(gateway_url or "").strip().lower().rstrip("/")
    if normalized_url != OPENROUTER_CHAT_COMPLETIONS_URL:
        return None
    return _VERIFIED_CHAT_OUTPUT_TARGETS.get(str(model_id or "").strip())


__all__ = [
    "CREATE_FILE_TOOL",
    "CREATE_FILE_TOOL_NAME",
    "OPENROUTER_CHAT_COMPLETIONS_URL",
    "ChatOutputError",
    "ChatOutputResult",
    "run_chat_output_turn",
    "verified_chat_output_provider",
]
