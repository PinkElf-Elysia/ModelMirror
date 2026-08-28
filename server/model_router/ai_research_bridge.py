from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import time
from dataclasses import dataclass
from typing import Annotated, Any, Literal

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .api import get_model_router_service
from .chat_canary import ProviderChatCanaryStreamEvidence
from .chat_stable import ProviderChatStableDispatch, ProviderChatStableService
from .service import ModelRouterService, RouterServiceError


MAX_MESSAGES = 128
MAX_TOTAL_MESSAGE_CHARS = 128_000
MAX_MESSAGE_CHARS = MAX_TOTAL_MESSAGE_CHARS
MAX_TOOLS = 32
MAX_TOOL_DESCRIPTION_CHARS = 2_048
MAX_TOOL_ARGUMENT_CHARS = 65_536
MAX_TOOL_SCHEMA_BYTES = 65_536
MAX_TOTAL_TOOL_SCHEMA_BYTES = 256_000
MAX_REQUEST_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class BridgeResponseError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class TextMessage(StrictModel):
    role: Literal["system", "user"]
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)

    @field_validator("content")
    @classmethod
    def require_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message content must contain text")
        return value


class ToolCallFunction(StrictModel):
    name: str = Field(min_length=1, max_length=64)
    arguments: str = Field(max_length=MAX_TOOL_ARGUMENT_CHARS)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not TOOL_NAME_PATTERN.fullmatch(value):
            raise ValueError("tool name contains unsupported characters")
        return value


class ToolCall(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    type: Literal["function"]
    function: ToolCallFunction


class AssistantMessage(StrictModel):
    role: Literal["assistant"]
    content: str | None = Field(default=None, max_length=MAX_MESSAGE_CHARS)
    tool_calls: list[ToolCall] = Field(default_factory=list, max_length=MAX_TOOLS)

    @model_validator(mode="after")
    def require_content_or_tool_call(self) -> "AssistantMessage":
        if not (self.content or "").strip() and not self.tool_calls:
            raise ValueError("assistant message requires text or tool_calls")
        return self


class ToolMessage(StrictModel):
    role: Literal["tool"]
    content: str = Field(max_length=MAX_MESSAGE_CHARS)
    tool_call_id: str = Field(min_length=1, max_length=128)


ChatMessage = Annotated[
    TextMessage | AssistantMessage | ToolMessage,
    Field(discriminator="role"),
]


class FunctionDefinition(StrictModel):
    name: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=MAX_TOOL_DESCRIPTION_CHARS)
    parameters: dict[str, Any]
    strict: bool | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not TOOL_NAME_PATTERN.fullmatch(value):
            raise ValueError("tool name contains unsupported characters")
        return value

    @field_validator("parameters")
    @classmethod
    def validate_parameters(cls, value: dict[str, Any]) -> dict[str, Any]:
        if value.get("type") != "object":
            raise ValueError("tool parameters must be an object JSON schema")
        if _json_size(value) > MAX_TOOL_SCHEMA_BYTES:
            raise ValueError("tool parameter schema exceeds the size limit")
        if _json_depth(value) > 16:
            raise ValueError("tool parameter schema exceeds the depth limit")
        return value


class FunctionTool(StrictModel):
    type: Literal["function"]
    function: FunctionDefinition


class NamedToolChoiceFunction(StrictModel):
    name: str = Field(min_length=1, max_length=64)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not TOOL_NAME_PATTERN.fullmatch(value):
            raise ValueError("tool name contains unsupported characters")
        return value


class NamedToolChoice(StrictModel):
    type: Literal["function"]
    function: NamedToolChoiceFunction


ToolChoice = Literal["none", "auto", "required"] | NamedToolChoice


@dataclass(slots=True)
class _BridgeStreamEvidence(ProviderChatCanaryStreamEvidence):
    tool_call_observed: bool = False

    def _consume_event(self, event: str) -> None:
        ProviderChatCanaryStreamEvidence._consume_event(self, event)
        data_lines = [
            line[5:].lstrip()
            for line in event.split("\n")
            if line.startswith("data:")
        ]
        if not data_lines or data_lines == ["[DONE]"]:
            return
        try:
            payload = json.loads("\n".join(data_lines))
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(payload, dict):
            return
        for choice in payload.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            for container_name in ("delta", "message"):
                container = choice.get(container_name)
                if isinstance(container, dict) and isinstance(
                    container.get("tool_calls"), list
                ):
                    self.tool_call_observed = self.tool_call_observed or bool(
                        container["tool_calls"]
                    )

    def finish_for_bridge(
        self, *, transport_completed: bool, allow_tool_calls: bool
    ) -> tuple[str, str, str | None]:
        status_value, result_class, error_code, _, _ = (
            ProviderChatCanaryStreamEvidence.finish(
                self, transport_completed=transport_completed
            )
        )
        if (
            allow_tool_calls
            and self.tool_call_observed
            and self.terminal_observed
            and not self.invalid
            and error_code == "provider_chat_empty_stream"
        ):
            return "succeeded", "success", None
        return status_value, result_class, error_code


class StreamOptions(StrictModel):
    include_usage: bool = Field(default=False, alias="include_usage")


class ChatCompletionRequest(StrictModel):
    model: str = Field(min_length=1, max_length=256)
    messages: list[ChatMessage] = Field(min_length=1, max_length=MAX_MESSAGES)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1, le=32_768)
    max_completion_tokens: int | None = Field(default=None, ge=1, le=32_768)
    top_p: float | None = Field(default=None, gt=0, le=1)
    stop: str | list[str] | None = None
    stream: bool = False
    stream_options: StreamOptions | None = None
    tools: list[FunctionTool] | None = Field(
        default=None, min_length=1, max_length=MAX_TOOLS
    )
    tool_choice: ToolChoice | None = None
    parallel_tool_calls: bool | None = None

    @field_validator("stop")
    @classmethod
    def validate_stop(cls, value: str | list[str] | None):
        if value is None:
            return value
        items = [value] if isinstance(value, str) else value
        if not items or len(items) > 4:
            raise ValueError("stop must contain between one and four strings")
        if any(not isinstance(item, str) or not item or len(item) > 100 for item in items):
            raise ValueError("stop values must be non-empty strings of at most 100 chars")
        return value

    @model_validator(mode="after")
    def validate_shape(self) -> "ChatCompletionRequest":
        message_chars = 0
        declared_tools = {
            tool.function.name for tool in (self.tools or [])
        }
        if len(declared_tools) != len(self.tools or []):
            raise ValueError("tool names must be unique")
        if sum(_json_size(tool.function.parameters) for tool in (self.tools or [])) > MAX_TOTAL_TOOL_SCHEMA_BYTES:
            raise ValueError("tool parameter schemas exceed the total size limit")
        pending_calls: dict[str, str] = {}
        seen_call_ids: set[str] = set()
        for message in self.messages:
            content = getattr(message, "content", None)
            if isinstance(content, str):
                message_chars += len(content)
            if isinstance(message, AssistantMessage):
                for call in message.tool_calls:
                    if call.id in seen_call_ids:
                        raise ValueError("tool call ids must be unique")
                    if call.function.name not in declared_tools:
                        raise ValueError("assistant tool call is not declared")
                    seen_call_ids.add(call.id)
                    pending_calls[call.id] = call.function.name
                    message_chars += len(call.function.arguments)
            elif isinstance(message, ToolMessage):
                if message.tool_call_id not in pending_calls:
                    raise ValueError("tool message does not match a pending tool call")
                pending_calls.pop(message.tool_call_id)
        if pending_calls:
            raise ValueError("each assistant tool call requires a tool response")
        if message_chars > MAX_TOTAL_MESSAGE_CHARS:
            raise ValueError("messages exceed the total text limit")
        if self.stream_options is not None and not self.stream:
            raise ValueError("stream_options requires stream=true")
        if self.max_tokens is not None and self.max_completion_tokens is not None:
            raise ValueError(
                "max_tokens and max_completion_tokens cannot both be provided"
            )
        if not self.tools and (
            self.tool_choice is not None or self.parallel_tool_calls is not None
        ):
            raise ValueError("tool options require tools")
        if isinstance(self.tool_choice, NamedToolChoice):
            if self.tool_choice.function.name not in declared_tools:
                raise ValueError("tool_choice must name a declared tool")
        if _json_size(self.model_dump(by_alias=True, exclude_none=True)) > MAX_REQUEST_BYTES:
            raise ValueError("request exceeds the bridge size limit")
        return self


@dataclass(frozen=True, slots=True)
class BridgeSettings:
    enabled: bool
    token: str
    model_id: str

    @classmethod
    def from_env(cls) -> "BridgeSettings":
        enabled = os.getenv("AI_RESEARCH_S2S_ENABLED", "false").strip().casefold()
        return cls(
            enabled=enabled in {"1", "true", "yes", "on"},
            token=os.getenv("AI_RESEARCH_S2S_TOKEN", ""),
            model_id=os.getenv("AI_RESEARCH_LITERATURE_MODEL_ID", "").strip(),
        )


router = APIRouter(prefix="/api/ai-research/v1", tags=["ai-research-s2s"])


def require_bridge(
    authorization: Annotated[str | None, Header()] = None,
) -> BridgeSettings:
    settings = BridgeSettings.from_env()
    if not settings.enabled or not settings.token or not settings.model_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI Research model bridge is not configured",
        )
    scheme, separator, credential = (authorization or "").partition(" ")
    if (
        not separator
        or scheme.casefold() != "bearer"
        or not credential
        or not secrets.compare_digest(credential, settings.token)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid AI Research service credential",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return settings


def stable_service(
    router_service: ModelRouterService = Depends(get_model_router_service),
) -> ProviderChatStableService:
    return ProviderChatStableService(router_service)


@router.get("/models")
async def models(
    settings: BridgeSettings = Depends(require_bridge),
    stable: ProviderChatStableService = Depends(stable_service),
) -> dict[str, Any]:
    ready, reason = stable.readiness(settings.model_id, "chat_tools")
    if not ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=reason or "fixed model control is not ready",
        )
    return {
        "object": "list",
        "data": [
            {
                "id": settings.model_id,
                "object": "model",
                "created": 0,
                "owned_by": "modelmirror-control-plane",
            }
        ],
    }


@router.post("/chat/completions")
async def chat_completions(
    payload: ChatCompletionRequest,
    settings: BridgeSettings = Depends(require_bridge),
    stable: ProviderChatStableService = Depends(stable_service),
):
    if payload.model != settings.model_id:
        raise HTTPException(status_code=422, detail="model is not enabled for AI Research")
    capability = "chat_tools" if payload.tools else "chat_text"
    try:
        preflight = await stable.begin(settings.model_id, capability)
    except RouterServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="AI Research model preflight failed"
        ) from exc
    if not preflight.intercepted or preflight.dispatch is None:
        raise HTTPException(
            status_code=503,
            detail=preflight.error_code or "fixed model control is not ready",
        )

    upstream_payload = payload.model_dump(by_alias=True, exclude_none=True)
    completion_limit = upstream_payload.pop("max_completion_tokens", None)
    if completion_limit is not None:
        upstream_payload["max_tokens"] = completion_limit
    dispatch = preflight.dispatch
    client = httpx.AsyncClient(**stable.transport.client_kwargs())
    started = time.perf_counter()
    try:
        request = stable.transport.build_authorized_stream_request(
            client,
            dispatch.target,
            dispatch.authorized,
            upstream_payload,
            headers={
                "Accept": "text/event-stream" if payload.stream else "application/json"
            },
        )
        stable.mark_dispatched(dispatch)
        response = await stable.transport.send_authorized_stream(client, request)
    except RouterServiceError as exc:
        await client.aclose()
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc
    except (httpx.HTTPError, OSError, ValueError) as exc:
        stable.complete(
            dispatch,
            status="failed",
            result_class="transient_failure",
            error_code="ai_research_bridge_transport_failed",
            e2e_ms=_elapsed_ms(started),
        )
        await client.aclose()
        raise HTTPException(status_code=503, detail="fixed model transport failed") from exc
    except Exception as exc:
        try:
            stable.complete(
                dispatch,
                status="failed",
                result_class="transient_failure",
                error_code="ai_research_bridge_dispatch_failed",
                e2e_ms=_elapsed_ms(started),
            )
        except Exception:
            pass
        await client.aclose()
        raise HTTPException(
            status_code=503, detail="fixed model dispatch failed"
        ) from exc

    if response.status_code < 200 or response.status_code >= 300:
        try:
            await _read_bounded(response)
        except (httpx.HTTPError, ValueError):
            pass
        finally:
            await response.aclose()
            await client.aclose()
        result_class, code, hard_failure = stable.classify_http_failure(
            response.status_code
        )
        stable.complete(
            dispatch,
            status="failed",
            result_class=result_class,
            error_code=code,
            hard_failure=hard_failure,
            e2e_ms=_elapsed_ms(started),
        )
        return JSONResponse(
            status_code=503 if response.status_code >= 500 else 502,
            content={"error": {"message": "fixed model request failed", "code": code}},
            headers={"Cache-Control": "no-store"},
        )

    if payload.stream:
        return StreamingResponse(
            _stream_response(
                stable=stable,
                dispatch=dispatch,
                response=response,
                client=client,
                requested_model=settings.model_id,
                started=started,
                allow_tool_calls=bool(payload.tools),
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
                "X-ModelMirror-Route-Run-Id": dispatch.run_id,
            },
        )

    try:
        content = await _read_bounded(response)
        value = json.loads(content)
        _validate_completion_response(
            value,
            requested_model=settings.model_id,
            allowed_tool_names={
                tool.function.name for tool in (payload.tools or [])
            },
        )
        usage = value.get("usage") if isinstance(value.get("usage"), dict) else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _reject_invalid_response(
            stable, dispatch, "ai_research_bridge_invalid_json", started, exc
        )
    except BridgeResponseError as exc:
        _reject_invalid_response(stable, dispatch, exc.code, started, exc)
    except ValueError as exc:
        _reject_invalid_response(
            stable,
            dispatch,
            "ai_research_bridge_response_limit_exceeded",
            started,
            exc,
        )
    except (httpx.HTTPError, OSError) as exc:
        stable.complete(
            dispatch,
            status="failed",
            result_class="transient_failure",
            error_code="ai_research_bridge_response_read_failed",
            e2e_ms=_elapsed_ms(started),
        )
        raise HTTPException(status_code=503, detail="fixed model response failed") from exc
    finally:
        await response.aclose()
        await client.aclose()
    stable.complete(
        dispatch,
        status="succeeded",
        result_class="success",
        actual_model=settings.model_id,
        e2e_ms=_elapsed_ms(started),
        prompt_tokens=_integer(usage.get("prompt_tokens")),
        completion_tokens=_integer(usage.get("completion_tokens")),
        total_tokens=_integer(usage.get("total_tokens")),
    )
    return JSONResponse(
        content=value,
        headers={
            "Cache-Control": "no-store",
            "X-ModelMirror-Route-Run-Id": dispatch.run_id,
        },
    )


async def _stream_response(
    *,
    stable: ProviderChatStableService,
    dispatch: ProviderChatStableDispatch,
    response: httpx.Response,
    client: httpx.AsyncClient,
    requested_model: str,
    started: float,
    allow_tool_calls: bool,
):
    evidence = _BridgeStreamEvidence(started_at=started)
    total = 0
    finalized = False
    try:
        async for chunk in response.aiter_text():
            encoded_size = len(chunk.encode("utf-8"))
            total += encoded_size
            if total > MAX_RESPONSE_BYTES:
                raise ValueError("stream exceeded bridge response limit")
            evidence.feed(chunk)
            yield chunk
        status_value, result_class, error_code = evidence.finish_for_bridge(
            transport_completed=True,
            allow_tool_calls=allow_tool_calls,
        )
        if evidence.actual_model not in {None, requested_model}:
            status_value = "failed"
            result_class = "hard_failure"
            error_code = "ai_research_bridge_model_mismatch"
        stable.complete(
            dispatch,
            status=status_value,
            result_class=result_class,
            error_code=error_code,
            actual_model=evidence.actual_model or requested_model,
            hard_failure=result_class == "hard_failure",
            ttft_ms=evidence.ttft_ms,
            e2e_ms=_elapsed_ms(started),
            prompt_tokens=evidence.prompt_tokens,
            completion_tokens=evidence.completion_tokens,
            total_tokens=evidence.total_tokens,
        )
        finalized = True
        if status_value != "succeeded":
            raise RuntimeError(error_code or "ai_research_bridge_invalid_stream")
    except asyncio.CancelledError:
        stable.complete(
            dispatch,
            status="cancelled",
            result_class="client_cancelled",
            error_code="provider_chat_client_cancelled",
            client_cancelled=True,
            e2e_ms=_elapsed_ms(started),
        )
        finalized = True
        raise
    except Exception:
        if not finalized:
            stable.complete(
                dispatch,
                status="failed",
                result_class="transient_failure",
                error_code="ai_research_bridge_stream_failed",
                e2e_ms=_elapsed_ms(started),
            )
            finalized = True
        raise
    finally:
        await response.aclose()
        await client.aclose()


async def _read_bounded(response: httpx.Response) -> bytes:
    total = 0
    chunks: list[bytes] = []
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise ValueError("response exceeded bridge limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _validate_completion_response(
    value: object,
    *,
    requested_model: str,
    allowed_tool_names: set[str],
) -> None:
    if not isinstance(value, dict):
        raise BridgeResponseError("ai_research_bridge_invalid_envelope")
    actual_model = value.get("model")
    if actual_model is not None and actual_model != requested_model:
        raise BridgeResponseError("ai_research_bridge_model_mismatch")
    choices = value.get("choices")
    if not isinstance(choices, list) or not choices:
        raise BridgeResponseError("ai_research_bridge_missing_choices")
    valid_choice = False
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str) and content:
            valid_choice = True
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list) or not tool_calls:
            continue
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict) or tool_call.get("type") != "function":
                raise BridgeResponseError("ai_research_bridge_invalid_tool_call")
            function = tool_call.get("function")
            if (
                not isinstance(tool_call.get("id"), str)
                or not tool_call["id"]
                or not isinstance(function, dict)
                or function.get("name") not in allowed_tool_names
                or not isinstance(function.get("arguments"), str)
            ):
                raise BridgeResponseError("ai_research_bridge_invalid_tool_call")
        valid_choice = True
    if not valid_choice:
        raise BridgeResponseError("ai_research_bridge_empty_completion")


def _reject_invalid_response(
    stable: ProviderChatStableService,
    dispatch: ProviderChatStableDispatch,
    error_code: str,
    started: float,
    exc: Exception,
) -> None:
    stable.complete(
        dispatch,
        status="failed",
        result_class="hard_failure",
        error_code=error_code,
        hard_failure=True,
        e2e_ms=_elapsed_ms(started),
    )
    raise HTTPException(
        status_code=502, detail="fixed model returned an invalid response"
    ) from exc


def _json_size(value: object) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def _json_depth(value: object) -> int:
    if isinstance(value, dict):
        return 1 + max((_json_depth(item) for item in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((_json_depth(item) for item in value), default=0)
    return 0


def _integer(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _elapsed_ms(started: float) -> float:
    return max(0.0, (time.perf_counter() - started) * 1000)
