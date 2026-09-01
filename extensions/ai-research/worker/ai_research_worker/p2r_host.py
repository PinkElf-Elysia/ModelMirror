from __future__ import annotations

import argparse
import hashlib
import ipaddress
from io import BytesIO
import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

import httpx
import inspect_ai
from inspect_ai import Task, eval
from inspect_ai.dataset import Sample
from inspect_ai.event import SandboxEvent, SpanBeginEvent, ToolEvent
from inspect_ai.log import read_eval_log
from inspect_ai.model import (
    ChatMessage,
    ChatMessageAssistant,
    ChatMessageSystem,
    ChatMessageUser,
    GenerateConfig,
    Model,
    ModelAPI,
    ModelOutput,
    ModelUsage,
)
from inspect_ai.solver import generate, use_tools
from inspect_ai.tool import ToolCall, ToolInfo, tool
from inspect_ai.util import SandboxEnvironmentSpec, sandbox

from . import p2r_connectors as connector_qualification
from .p2r_input_gate import (
    LOCKED_V01_BUNDLE,
    LOCKED_V01_BUNDLE_SHA256,
    LOCKED_V01_RESEARCH_QUESTION,
    LOCKED_V01_SOURCE_COUNT,
    MAX_P2R_CLOCK_SKEW_SECONDS,
    MAX_P2R_QUALIFICATION_AGE_SECONDS,
    P2R_INPUT_PROTOCOL,
    QUALIFICATION_RUN_ID_PATTERN,
)
from .p2r_connectors import (
    BASE_IMAGE as CONNECTOR_BASE_IMAGE,
    CONNECTOR_ORDER,
    FIXED_QUERY as CONNECTOR_FIXED_QUERY,
    PACKAGE_VERSIONS as CONNECTOR_PACKAGE_VERSIONS,
    PROTOCOL as CONNECTOR_PROTOCOL,
    PYTHON_VERSION as CONNECTOR_PYTHON_VERSION,
    QUALIFICATION_AS_OF as CONNECTOR_QUALIFICATION_AS_OF,
    REQUIREMENTS_LOCK_SHA256 as CONNECTOR_REQUIREMENTS_LOCK_SHA256,
    RESEARCHSTUDIO_COMMIT,
    RETRY_POLICY as CONNECTOR_RETRY_POLICY,
    SCRIPT_HASHES as CONNECTOR_SCRIPT_HASHES,
    _public_hits,
)
from .p2r_phase_contracts import (
    PHASE_RECEIPT_PATHS,
    P2RPhaseContractError,
    RESEARCHSTUDIO_REUSE_ROOT_AGGREGATE_SHA256,
    RESEARCHSTUDIO_REUSE_ROOT_FILE_COUNT,
    RESEARCHSTUDIO_REUSE_ROOT_TOTAL_BYTES,
    canonical_json_bytes as phase_receipt_bytes,
    validate_phase_receipt,
    verify_locked_assets,
    verify_raw_artifact_manifest,
)


P2R_PROTOCOL = "modelmirror-ai-research-p2r-v1"
P2R_MODEL_ID = "openai/gpt-5.4"
P2R_SANDBOX_IMAGE = (
    "python@sha256:401f6e1a67dad31a1bd78e9ad22d0ee0a3b52154e6bd30e90be696bb6a3d7461"
)
COHERENCE_PROMPT_RELATIVE = Path("references/system-prompts/coherence_trace.txt")
COHERENCE_PROMPT_SHA256 = (
    "b53c6ff219a1b4eb1689a9f5728c21e9c8b8b0de1e93babf1b5814214447bb02"
)
SANDBOX_COMPOSE_RELATIVE = Path("worker/p2r-sandbox.compose.yml")
SANDBOX_COMPOSE_SHA256 = (
    "23e94e772deb824fa7c0b86fa54264e10ee66e6568529c436b678cc3c04d2b98"
)
MAX_SCRIPT_BYTES = 65_536
MAX_STREAM_BYTES = 262_144
MAX_VISIBLE_STREAM_BYTES = 49_152
MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_EVAL_LOG_BYTES = 32 * 1024 * 1024
MAX_BRIDGE_RESPONSE_BYTES = 2 * 1024 * 1024
EXEC_TIMEOUT_SECONDS = 30
P2R_COHERENCE_PHASE = "researchstudio.phase2.coherence"
P2R_PHASE_REQUEST_PROTOCOL = "modelmirror-ai-research-p2r-phase-request-v1"
MAX_PHASE_ARTIFACT_CHUNK_CHARS = 100_000


class P2RHostError(RuntimeError):
    pass


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _phase_artifact_messages(
    *,
    phase: str,
    qualification_run_id: str,
    previous_receipt_sha256: str,
    artifacts: list[tuple[str, bytes]],
) -> list[ChatMessageUser]:
    if phase != P2R_COHERENCE_PHASE or not QUALIFICATION_RUN_ID_PATTERN.fullmatch(
        qualification_run_id
    ):
        raise P2RHostError("P2R phase artifact identity is invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", previous_receipt_sha256):
        raise P2RHostError("P2R phase artifact receipt binding is invalid")
    if [path for path, _ in artifacts] != [
        "phase2_select/phase2_select_output.json",
        "phase2_generate/phase2_generate_output.json",
    ]:
        raise P2RHostError("P2R coherence artifact set is not fixed")
    messages: list[ChatMessageUser] = []
    for path, raw in artifacts:
        if not raw or len(raw) > MAX_INPUT_BYTES:
            raise P2RHostError("P2R coherence artifact is empty or oversized")
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise P2RHostError("P2R coherence artifact is not UTF-8") from exc
        chunks = [
            content[offset : offset + MAX_PHASE_ARTIFACT_CHUNK_CHARS]
            for offset in range(0, len(content), MAX_PHASE_ARTIFACT_CHUNK_CHARS)
        ]
        if not chunks:
            raise P2RHostError("P2R coherence artifact is empty")
        full_sha = _sha256(raw)
        for index, chunk in enumerate(chunks):
            envelope = {
                "protocol": P2R_PHASE_REQUEST_PROTOCOL,
                "qualificationRunId": qualification_run_id,
                "phase": phase,
                "previousReceiptSha256": previous_receipt_sha256,
                "artifact": {
                    "path": path,
                    "sha256": full_sha,
                    "sizeBytes": len(raw),
                    "chunkIndex": index,
                    "chunkCount": len(chunks),
                    "chunkSha256": _sha256(chunk.encode("utf-8")),
                    "content": chunk,
                },
            }
            messages.append(ChatMessageUser(content=_canonical_json(envelope)))
    return messages


def _validated_phase_history(
    messages: list[ChatMessage],
    *,
    prompt: bytes,
    phase: str,
    qualification_run_id: str,
    previous_receipt_sha256: str,
    artifacts: list[tuple[str, bytes]],
) -> str:
    try:
        prompt_text = prompt.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise P2RHostError("locked P2R prompt is not UTF-8") from exc
    expected_users = _phase_artifact_messages(
        phase=phase,
        qualification_run_id=qualification_run_id,
        previous_receipt_sha256=previous_receipt_sha256,
        artifacts=artifacts,
    )
    prefix_length = 1 + len(expected_users)
    if len(messages) < prefix_length:
        raise P2RHostError("Inspect omitted fixed coherence prompt artifacts")
    if messages[0].role != "system" or _text_content(messages[0].content) != prompt_text:
        raise P2RHostError("Inspect changed the locked coherence system prompt")
    for actual, expected in zip(messages[1:prefix_length], expected_users, strict=True):
        if actual.role != "user":
            raise P2RHostError("Inspect changed the fixed coherence artifact role order")
        content = _text_content(actual.content)
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise P2RHostError("Inspect supplied a malformed coherence artifact envelope") from exc
        if content != _canonical_json(parsed) or content != _text_content(expected.content):
            raise P2RHostError("Inspect changed a canonical coherence artifact envelope")
    remainder = messages[prefix_length:]
    if not remainder:
        return "coherence_initial"
    if len(remainder) != 2 or remainder[0].role != "assistant" or remainder[1].role != "tool":
        raise P2RHostError("Inspect supplied an invalid coherence finalize history")
    assistant, tool_message = remainder
    if _text_content(assistant.content):
        raise P2RHostError("coherence initial assistant mixed content with its tool call")
    calls = getattr(assistant, "tool_calls", None) or []
    if len(calls) != 1:
        raise P2RHostError("coherence finalize history does not contain one Python call")
    call = calls[0]
    code = call.arguments.get("code") if isinstance(call.arguments, dict) else None
    if (
        not isinstance(call.id, str)
        or not call.id
        or call.function != "python"
        or not isinstance(code, str)
        or not code
        or getattr(tool_message, "tool_call_id", None) != call.id
        or getattr(tool_message, "function", None) != "python"
        or getattr(tool_message, "error", None) is not None
    ):
        raise P2RHostError("coherence assistant/tool history is not bound to one Python call")
    receipt_text = _text_content(tool_message.content)
    try:
        receipt = json.loads(receipt_text)
    except json.JSONDecodeError as exc:
        raise P2RHostError("coherence tool history does not contain a JSON receipt") from exc
    if receipt_text != _canonical_json(receipt):
        raise P2RHostError("coherence tool history receipt is not canonical")
    _validate_tool_envelope(receipt, code)
    return "coherence_finalize"


def _bounded_text(value: str, *, limit: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value, False
    prefix = encoded[:limit]
    while prefix:
        try:
            return prefix.decode("utf-8"), True
        except UnicodeDecodeError:
            prefix = prefix[:-1]
    return "", True


@tool(name="python")
def p2r_python():
    async def execute(code: str) -> str:
        """Execute a bounded Python dry-run in the isolated P2R sandbox.

        Args:
            code: A stdlib-only Python script used to test the candidate procedure.
        """

        script = code.encode("utf-8")
        if not script or len(script) > MAX_SCRIPT_BYTES:
            raise P2RHostError("python script is empty or exceeds the fixed size limit")
        try:
            result = await sandbox().exec(
                ["python3", "-"],
                input=code,
                timeout=EXEC_TIMEOUT_SECONDS,
                timeout_retry=False,
                concurrency=False,
            )
        except Exception as exc:
            raise P2RHostError("sandbox execution did not produce a complete result") from exc

        stdout_bytes = result.stdout.encode("utf-8")
        stderr_bytes = result.stderr.encode("utf-8")
        stdout_visible, stdout_truncated = _bounded_text(
            result.stdout, limit=MAX_VISIBLE_STREAM_BYTES
        )
        stderr_visible, stderr_truncated = _bounded_text(
            result.stderr, limit=MAX_VISIBLE_STREAM_BYTES
        )
        capture_exceeded = (
            len(stdout_bytes) > MAX_STREAM_BYTES or len(stderr_bytes) > MAX_STREAM_BYTES
        )
        envelope = {
            "protocol": P2R_PROTOCOL,
            "sandboxImage": P2R_SANDBOX_IMAGE,
            "command": ["python3", "-"],
            "scriptSha256": _sha256(script),
            "scriptSizeBytes": len(script),
            "exitCode": result.returncode,
            "stdout": stdout_visible,
            "stdoutSha256": _sha256(stdout_bytes),
            "stdoutSizeBytes": len(stdout_bytes),
            "stderr": stderr_visible,
            "stderrSha256": _sha256(stderr_bytes),
            "stderrSizeBytes": len(stderr_bytes),
            "limits": {
                "timeoutSeconds": EXEC_TIMEOUT_SECONDS,
                "scriptBytes": MAX_SCRIPT_BYTES,
                "streamBytes": MAX_STREAM_BYTES,
                "visibleStreamBytes": MAX_VISIBLE_STREAM_BYTES,
            },
            "truncation": {
                "stdout": stdout_truncated,
                "stderr": stderr_truncated,
                "captureExceeded": capture_exceeded,
            },
        }
        return json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    return execute


class ModelMirrorBridgeAPI(ModelAPI):
    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        phase: str,
        prompt: bytes,
        qualification_run_id: str,
        previous_receipt_sha256: str,
        artifacts: list[tuple[str, bytes]],
    ) -> None:
        base_url = _validated_loopback_endpoint(base_url)
        if phase != P2R_COHERENCE_PHASE:
            raise P2RHostError("P2R bridge phase is not enabled by the fixed host")
        super().__init__(model_name=P2R_MODEL_ID, base_url=base_url, api_key=token)
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._phase = phase
        self._prompt = bytes(prompt)
        self._qualification_run_id = qualification_run_id
        self._previous_receipt_sha256 = previous_receipt_sha256
        self._artifacts = [(path, bytes(raw)) for path, raw in artifacts]

    async def generate(
        self,
        input: list[ChatMessage],
        tools: list[ToolInfo],
        tool_choice: Any,
        config: GenerateConfig,
    ) -> ModelOutput:
        if (
            config.temperature not in {None, 0.2}
            or config.max_tokens not in {None, 30_000}
            or config.top_p is not None
        ):
            raise P2RHostError("Inspect attempted to change the fixed P2R sampling contract")
        if len(tools) != 1 or tools[0].name != "python":
            raise P2RHostError("Inspect changed the fixed coherence tool contract")
        stage = _validated_phase_history(
            input,
            prompt=self._prompt,
            phase=self._phase,
            qualification_run_id=self._qualification_run_id,
            previous_receipt_sha256=self._previous_receipt_sha256,
            artifacts=self._artifacts,
        )
        payload: dict[str, Any] = {
            "model": P2R_MODEL_ID,
            "messages": [_bridge_message(message) for message in input],
            "temperature": 0.2,
            "max_tokens": 30_000,
            "stream": False,
            "response_format": {"type": "json_object"},
        }
        if tools:
            payload["tools"] = [_bridge_tool(item) for item in tools]
            payload["tool_choice"] = _bridge_tool_choice(tool_choice)
            payload["parallel_tool_calls"] = False
        response_body = bytearray()
        response_status = 0
        response_content_type = ""
        route_run_id: str | None = None
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(1800.0), trust_env=False, follow_redirects=False
        ) as client:
            async with client.stream(
                "POST",
                f"{self._base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "X-ModelMirror-P2R-Phase": self._phase,
                },
                json=payload,
            ) as response:
                response_status = response.status_code
                response_content_type = response.headers.get("content-type", "")
                route_run_id = response.headers.get("X-ModelMirror-Route-Run-Id")
                async for chunk in response.aiter_bytes():
                    if len(response_body) + len(chunk) > MAX_BRIDGE_RESPONSE_BYTES:
                        raise P2RHostError("fixed model bridge response exceeded the size limit")
                    response_body.extend(chunk)
        if response_status != 200:
            raise P2RHostError(
                f"fixed model bridge rejected the phase: HTTP {response_status}"
            )
        if "application/json" not in response_content_type.lower():
            raise P2RHostError("fixed model bridge returned a non-JSON completion")
        if not isinstance(route_run_id, str) or not route_run_id.strip():
            raise P2RHostError("fixed model bridge omitted its route run identity")
        try:
            value = json.loads(response_body)
            choice = value["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise P2RHostError("fixed model bridge returned a malformed completion") from exc
        if value.get("model") not in {None, P2R_MODEL_ID}:
            raise P2RHostError("fixed model bridge returned a different model identity")

        tool_calls: list[ToolCall] = []
        tool_call_ids: set[str] = set()
        for call in message.get("tool_calls") or []:
            try:
                call_id = call["id"]
                function = call["function"]
                if (
                    not isinstance(call_id, str)
                    or not call_id
                    or len(call_id) > 128
                    or call_id in tool_call_ids
                    or function["name"] != "python"
                ):
                    raise TypeError
                arguments = json.loads(function["arguments"])
                if (
                    not isinstance(arguments, dict)
                    or set(arguments) != {"code"}
                    or not isinstance(arguments["code"], str)
                    or not arguments["code"]
                    or len(arguments["code"].encode("utf-8")) > MAX_SCRIPT_BYTES
                ):
                    raise TypeError
                tool_call_ids.add(call_id)
                tool_calls.append(
                    ToolCall(
                        id=call_id,
                        function="python",
                        arguments=arguments,
                    )
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise P2RHostError("fixed model bridge returned an invalid tool call") from exc
        content = message.get("content") or ""
        if not isinstance(content, str) or (not content and not tool_calls):
            raise P2RHostError("fixed model bridge returned an empty completion")
        if stage == "coherence_initial" and (
            content or len(tool_calls) != 1
        ):
            raise P2RHostError(
                "fixed model bridge did not return exactly one initial Python call"
            )
        if stage == "coherence_finalize":
            if tool_calls:
                raise P2RHostError("fixed model bridge repeated a coherence tool call")
            try:
                final_value = json.loads(content)
            except json.JSONDecodeError as exc:
                raise P2RHostError(
                    "fixed model bridge returned invalid coherence JSON"
                ) from exc
            if not isinstance(final_value, dict):
                raise P2RHostError("fixed model bridge returned the wrong coherence shape")
            _validate_coherence_schema(final_value)
        stop_reason = "tool_calls" if tool_calls else _stop_reason(choice.get("finish_reason"))
        output = ModelOutput.from_message(
            ChatMessageAssistant(
                content=content,
                tool_calls=tool_calls or None,
                model=P2R_MODEL_ID,
                metadata={"modelmirrorRouteRunId": route_run_id}
                if route_run_id
                else None,
            ),
            stop_reason=stop_reason,
        )
        output.model = P2R_MODEL_ID
        usage = value.get("usage") or {}
        output.usage = ModelUsage(
            input_tokens=_safe_int(usage.get("prompt_tokens")),
            output_tokens=_safe_int(usage.get("completion_tokens")),
            total_tokens=_safe_int(usage.get("total_tokens")),
        )
        return output


def _validated_loopback_endpoint(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise P2RHostError(
            "P2R model bridge endpoint must be an exact loopback HTTP URL"
        ) from exc
    if (
        parsed.scheme != "http"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != "/api/ai-research/v1"
        or port is None
    ):
        raise P2RHostError("P2R model bridge endpoint must be an exact loopback HTTP URL")
    hostname = parsed.hostname
    if hostname != "localhost":
        try:
            if hostname is None or not ipaddress.ip_address(hostname).is_loopback:
                raise ValueError
        except ValueError as exc:
            raise P2RHostError(
                "P2R model bridge endpoint must be an exact loopback HTTP URL"
            ) from exc
    return value.rstrip("/")


def _safe_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _stop_reason(value: object) -> str:
    if value == "length":
        return "max_tokens"
    if value == "content_filter":
        return "content_filter"
    return "stop"


def _text_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise P2RHostError("P2R bridge accepts text messages only")
    text: list[str] = []
    for item in content:
        value = item.model_dump(mode="json", exclude_none=True) if hasattr(item, "model_dump") else item
        if not isinstance(value, dict) or value.get("type") != "text" or not isinstance(
            value.get("text"), str
        ):
            raise P2RHostError("P2R bridge accepts text message parts only")
        text.append(value["text"])
    return "".join(text)


def _bridge_message(message: ChatMessage) -> dict[str, Any]:
    content = _text_content(message.content)
    if message.role in {"system", "user"}:
        return {"role": message.role, "content": content}
    if message.role == "assistant":
        result: dict[str, Any] = {"role": "assistant", "content": content or None}
        calls = getattr(message, "tool_calls", None) or []
        if calls:
            result["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.function,
                        "arguments": json.dumps(
                            call.arguments, ensure_ascii=False, separators=(",", ":")
                        ),
                    },
                }
                for call in calls
            ]
        return result
    if message.role == "tool":
        tool_call_id = getattr(message, "tool_call_id", None)
        if not tool_call_id:
            raise P2RHostError("tool response is missing its tool call id")
        return {"role": "tool", "content": content, "tool_call_id": tool_call_id}
    raise P2RHostError("P2R bridge received an unsupported message role")


def _bridge_tool(info: ToolInfo) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": info.name,
            "description": info.description,
            "parameters": info.parameters.model_dump(mode="json", exclude_none=True),
            "strict": True,
        },
    }


def _bridge_tool_choice(choice: object) -> object:
    if choice == "any":
        return "required"
    if choice in {"auto", "none"}:
        return choice
    name = getattr(choice, "name", None)
    if isinstance(name, str):
        return {"type": "function", "function": {"name": name}}
    return "auto"


def _iter_events(events: Iterable[object]) -> Iterable[object]:
    for event in events:
        yield event
        nested = getattr(event, "events", None)
        if isinstance(nested, list):
            yield from _iter_events(nested)


def _tool_result_text(result: object) -> str:
    if isinstance(result, str):
        return result
    if hasattr(result, "text") and isinstance(result.text, str):
        return result.text
    raise P2RHostError("python tool result is not a text receipt")


def _validated_tool_receipts(sample: object) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    events = list(_iter_events(getattr(sample, "events", None) or []))
    sandbox_events = [event for event in events if isinstance(event, SandboxEvent)]
    for event in events:
        if not isinstance(event, ToolEvent) or event.function != "python":
            continue
        if event.error is not None or event.failed is True or event.truncated is not None:
            raise P2RHostError("python tool event is failed or Inspect-truncated")
        code = event.arguments.get("code")
        if not isinstance(code, str) or not code:
            raise P2RHostError("python tool event is missing its script")
        try:
            envelope = json.loads(_tool_result_text(event.result))
        except json.JSONDecodeError as exc:
            raise P2RHostError("python tool receipt is not valid JSON") from exc
        _validate_tool_envelope(envelope, code)
        if not event.span_id:
            raise P2RHostError("python tool event is missing its Inspect span identity")
        tool_span_ids = {
            item.id
            for item in events
            if isinstance(item, SpanBeginEvent)
            and item.type == "tool"
            and item.name == "python"
            and item.parent_id == event.span_id
        }
        matching = [
            item
            for item in sandbox_events
            if item.action == "exec"
            and item.span_id in tool_span_ids
            and item.input == code
            and item.result == envelope["exitCode"]
            and item.cmd == "python3 -"
            and item.output
            == (
                f'{envelope["stderr"]}\n\n{envelope["stdout"]}'
                if envelope["stderr"]
                else envelope["stdout"]
            )
        ]
        if len(matching) != 1:
            raise P2RHostError("python tool receipt is not bound to an Inspect sandbox event")
        receipts.append({"toolCallId": event.id, **envelope})
    return receipts


def _modelmirror_route_run_ids(sample: object) -> list[str]:
    route_run_ids: list[str] = []
    assistant_count = 0
    for message in getattr(sample, "messages", None) or []:
        if not isinstance(message, ChatMessageAssistant) or message.model != P2R_MODEL_ID:
            continue
        assistant_count += 1
        metadata = message.metadata or {}
        run_id = metadata.get("modelmirrorRouteRunId")
        if not isinstance(run_id, str) or not run_id.strip():
            raise P2RHostError("a P2R model turn is missing its route run identity")
        if run_id not in route_run_ids:
            route_run_ids.append(run_id)
    if assistant_count == 0:
        raise P2RHostError("coherence phase preserved no P2R model turns")
    return route_run_ids


def _validate_tool_envelope(envelope: object, code: str) -> None:
    if not isinstance(envelope, dict):
        raise P2RHostError("python tool receipt has the wrong shape")
    if set(envelope) != {
        "protocol",
        "sandboxImage",
        "command",
        "scriptSha256",
        "scriptSizeBytes",
        "exitCode",
        "stdout",
        "stdoutSha256",
        "stdoutSizeBytes",
        "stderr",
        "stderrSha256",
        "stderrSizeBytes",
        "limits",
        "truncation",
    }:
        raise P2RHostError("python tool receipt has the wrong immutable schema")
    required = {
        "protocol": P2R_PROTOCOL,
        "sandboxImage": P2R_SANDBOX_IMAGE,
        "command": ["python3", "-"],
        "scriptSha256": _sha256(code.encode("utf-8")),
        "scriptSizeBytes": len(code.encode("utf-8")),
    }
    if any(envelope.get(key) != value for key, value in required.items()):
        raise P2RHostError("python tool receipt identity or script hash does not match")
    for stream in ("stdout", "stderr"):
        value = envelope.get(stream)
        if not isinstance(value, str):
            raise P2RHostError("python tool receipt is missing a stream")
        if envelope.get(f"{stream}Sha256") != _sha256(value.encode("utf-8")):
            raise P2RHostError("python tool stream hash does not match")
        if envelope.get(f"{stream}SizeBytes") != len(value.encode("utf-8")):
            raise P2RHostError("python tool stream size does not match")
    truncation = envelope.get("truncation")
    if truncation != {"captureExceeded": False, "stderr": False, "stdout": False}:
        raise P2RHostError("truncated python evidence is not admissible")
    if type(envelope.get("exitCode")) is not int or envelope["exitCode"] != 0:
        raise P2RHostError("failed python execution is not admissible evidence")
    if envelope.get("limits") != {
        "scriptBytes": MAX_SCRIPT_BYTES,
        "streamBytes": MAX_STREAM_BYTES,
        "timeoutSeconds": EXEC_TIMEOUT_SECONDS,
        "visibleStreamBytes": MAX_VISIBLE_STREAM_BYTES,
    }:
        raise P2RHostError("python tool receipt limits do not match the fixed host")


def _nonempty_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise P2RHostError(f"coherence output has an invalid {field}")
    return value


def _exact_object(value: object, keys: set[str], *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise P2RHostError(f"coherence output has the wrong {field} schema")
    return value


def _validate_record_list(
    value: object,
    *,
    keys: set[str],
    field: str,
    allow_empty: bool,
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise P2RHostError(f"coherence output has an invalid {field} list")
    records: list[dict[str, Any]] = []
    for item in value:
        records.append(_exact_object(item, keys, field=field))
    return records


def _validate_coherence_schema(value: object) -> dict[str, Any]:
    root = _exact_object(
        value,
        {"trace_report", "verdict", "unrepaired", "applied_revisions"},
        field="root",
    )
    trace = _exact_object(
        root["trace_report"],
        {
            "formalized_procedure",
            "dry_run",
            "degenerate_probes",
            "claim_step_map",
            "naive_comparison",
        },
        field="trace_report",
    )
    for step in _validate_record_list(
        trace["formalized_procedure"],
        keys={"step", "consumes", "produces", "note"},
        field="formalized_procedure",
        allow_empty=False,
    ):
        _nonempty_text(step["step"], field="formalized step")
        _nonempty_text(step["note"], field="formalized note")
        for field in ("consumes", "produces"):
            if not isinstance(step[field], list) or any(
                not isinstance(item, str) or not item.strip() for item in step[field]
            ):
                raise P2RHostError(f"coherence output has invalid {field}")
    dry_run = _exact_object(
        trace["dry_run"],
        {"instance", "execution", "computed_quantities", "anomalies"},
        field="dry_run",
    )
    _nonempty_text(dry_run["instance"], field="dry-run instance")
    execution = _exact_object(
        dry_run["execution"], {"mode", "script", "output"}, field="execution"
    )
    if execution["mode"] != "executed":
        raise P2RHostError("coherence output is not receipt-backed executed evidence")
    _nonempty_text(execution["script"], field="execution script")
    if not isinstance(execution["output"], str):
        raise P2RHostError("coherence output has invalid execution output")
    for item in _validate_record_list(
        dry_run["computed_quantities"],
        keys={"quantity", "value", "arithmetic"},
        field="computed_quantities",
        allow_empty=False,
    ):
        for field in item:
            _nonempty_text(item[field], field=f"computed quantity {field}")
    for item in _validate_record_list(
        dry_run["anomalies"],
        keys={"anomaly", "kind", "why"},
        field="anomalies",
        allow_empty=True,
    ):
        _nonempty_text(item["anomaly"], field="anomaly")
        _nonempty_text(item["why"], field="anomaly why")
        if item["kind"] not in {"structural", "instance_contingent"}:
            raise P2RHostError("coherence output has invalid anomaly kind")
    for item in _validate_record_list(
        trace["degenerate_probes"],
        keys={"probe", "behavior", "finding"},
        field="degenerate_probes",
        allow_empty=False,
    ):
        _nonempty_text(item["probe"], field="degenerate probe")
        _nonempty_text(item["behavior"], field="degenerate behavior")
        if item["finding"] is not None and not isinstance(item["finding"], str):
            raise P2RHostError("coherence output has invalid degenerate finding")
    for item in _validate_record_list(
        trace["claim_step_map"],
        keys={
            "claim",
            "established_by",
            "strength_grade",
            "assumptions_missing",
            "arbitration",
            "measured",
        },
        field="claim_step_map",
        allow_empty=False,
    ):
        _nonempty_text(item["claim"], field="claim")
        _nonempty_text(item["established_by"], field="claim established_by")
        if item["strength_grade"] not in {
            "established",
            "conditional",
            "overclaim",
            "empirical",
        } or item["arbitration"] not in {"executed-mc", "argument", "proof-obligations"}:
            raise P2RHostError("coherence output has invalid claim grading")
        if not isinstance(item["assumptions_missing"], list) or any(
            not isinstance(entry, str) or not entry.strip()
            for entry in item["assumptions_missing"]
        ):
            raise P2RHostError("coherence output has invalid claim assumptions")
        if item["measured"] is not None and not isinstance(item["measured"], str):
            raise P2RHostError("coherence output has invalid claim measurement")
    naive = _exact_object(
        trace["naive_comparison"],
        {
            "declared_branch",
            "naive_version",
            "naive_fairness",
            "instance_behavior",
            "verdict",
            "reasoning",
        },
        field="naive_comparison",
    )
    for field in ("declared_branch", "naive_version", "naive_fairness", "reasoning"):
        _nonempty_text(naive[field], field=f"naive {field}")
    if naive["verdict"] not in {"confronts_obstacle", "equivalent_to_naive", "n_a"}:
        raise P2RHostError("coherence output has invalid naive verdict")
    behavior = _exact_object(
        naive["instance_behavior"],
        {"naive", "mechanism", "divergence", "kind"},
        field="naive instance_behavior",
    )
    for field in ("naive", "mechanism", "divergence"):
        _nonempty_text(behavior[field], field=f"naive behavior {field}")
    if behavior["kind"] not in {"structural", "instance_contingent"}:
        raise P2RHostError("coherence output has invalid naive behavior kind")
    if root["verdict"] not in {"pass", "patched"}:
        raise P2RHostError("coherence output has invalid verdict")
    unrepaired = _validate_record_list(
        root["unrepaired"],
        keys={
            "finding",
            "severity",
            "why_not_repaired",
            "verbatim_step_quote",
            "executed_evidence",
            "reading_dependence",
            "structural_requirement",
        },
        field="unrepaired",
        allow_empty=True,
    )
    for item in unrepaired:
        for field in (
            "finding",
            "why_not_repaired",
            "verbatim_step_quote",
            "executed_evidence",
            "reading_dependence",
        ):
            _nonempty_text(item[field], field=f"unrepaired {field}")
        if item["severity"] not in {"blocking", "note"}:
            raise P2RHostError("coherence output has invalid unrepaired severity")
        requirement = item["structural_requirement"]
        if (item["severity"] == "blocking" and not isinstance(requirement, str)) or (
            item["severity"] == "note" and requirement is not None
        ):
            raise P2RHostError("coherence output has invalid structural requirement")
        if isinstance(requirement, str):
            _nonempty_text(requirement, field="structural requirement")
    revisions = _validate_record_list(
        root["applied_revisions"],
        keys={"scope", "op", "field", "value", "outcome", "delta_summary"},
        field="applied_revisions",
        allow_empty=True,
    )
    for item in revisions:
        if item["scope"] != "coherence" or item["op"] not in {
            "replace",
            "append_sentence",
            "append_items",
        } or item["outcome"] != "applied":
            raise P2RHostError("coherence output has invalid revision contract")
        for field in ("field", "delta_summary"):
            _nonempty_text(item[field], field=f"revision {field}")
    if (root["verdict"] == "pass" and revisions) or (
        root["verdict"] == "patched" and not revisions
    ):
        raise P2RHostError("coherence verdict and applied revisions disagree")
    return root


def _coherence_value(sample: object) -> dict[str, Any]:
    output = getattr(sample, "output", None)
    completion = getattr(output, "completion", None)
    if not isinstance(completion, str) or not completion.strip():
        raise P2RHostError("coherence phase has no terminal assistant JSON")
    try:
        value = json.loads(completion)
    except json.JSONDecodeError as exc:
        raise P2RHostError("coherence phase did not return valid JSON") from exc
    return _validate_coherence_schema(value)


def _validate_coherence_execution(
    value: dict[str, Any], receipts: list[dict[str, Any]]
) -> None:
    if len(receipts) != 1 or receipts[0].get("exitCode") != 0:
        raise P2RHostError("coherence execution requires exactly one successful trusted receipt")
    try:
        execution = value["trace_report"]["dry_run"]["execution"]
        mode = execution["mode"]
        script = execution["script"]
        output = execution["output"]
    except (KeyError, TypeError) as exc:
        raise P2RHostError("coherence output is missing the dry-run execution block") from exc
    if mode != "executed" or not isinstance(script, str) or not isinstance(output, str):
        raise P2RHostError("coherence phase did not claim a receipt-backed execution")
    matching = [
        receipt
        for receipt in receipts
        if receipt["scriptSha256"] == _sha256(script.encode("utf-8"))
        and receipt["stdout"] == output
    ]
    if len(matching) != 1:
        raise P2RHostError("coherence script/output is not bound to a trusted tool receipt")


def _blocking_findings(value: dict[str, Any]) -> list[dict[str, Any]]:
    unrepaired = value.get("unrepaired")
    if not isinstance(unrepaired, list):
        raise P2RHostError("coherence output is missing unrepaired findings")
    blocking: list[dict[str, Any]] = []
    for item in unrepaired:
        if not isinstance(item, dict):
            raise P2RHostError("coherence finding has the wrong shape")
        if item.get("severity") == "blocking":
            for key in (
                "verbatim_step_quote",
                "executed_evidence",
                "reading_dependence",
                "structural_requirement",
            ):
                if not isinstance(item.get(key), str) or not item[key].strip():
                    raise P2RHostError(f"blocking finding is missing {key}")
            blocking.append(item)
    return blocking


def _durable_write(path: Path, data: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _atomic_deliver(
    run_dir: Path,
    *,
    value: dict[str, Any],
    blocking: list[dict[str, Any]],
    eval_log: dict[str, Any],
    eval_archive: bytes,
    receipt: dict[str, Any],
) -> Path:
    final = run_dir / "phase2_coherence"
    if final.exists() or final.is_symlink():
        raise P2RHostError("coherence output directory already exists; evidence is immutable")
    staging = run_dir / f".phase2_coherence.staging-{uuid.uuid4().hex}"
    staging.mkdir(mode=0o700)
    try:
        output_bytes = _json_bytes(value)
        blocking_bytes = _json_bytes(blocking)
        log_bytes = _json_bytes(eval_log)
        receipt = {
            **receipt,
            "artifacts": {
                "phase2_coherence_output.json": {
                    "sha256": _sha256(output_bytes),
                    "sizeBytes": len(output_bytes),
                },
                "blocking_findings.json": {
                    "sha256": _sha256(blocking_bytes),
                    "sizeBytes": len(blocking_bytes),
                },
                "eval-log.json": {
                    "sha256": _sha256(log_bytes),
                    "sizeBytes": len(log_bytes),
                },
                "eval-log.eval": {
                    "sha256": _sha256(eval_archive),
                    "sizeBytes": len(eval_archive),
                },
            },
        }
        _durable_write(staging / "phase2_coherence_output.json", output_bytes)
        _durable_write(staging / "blocking_findings.json", blocking_bytes)
        _durable_write(staging / "eval-log.json", log_bytes)
        _durable_write(staging / "eval-log.eval", eval_archive)
        _durable_write(staging / "execution_receipt.json", _json_bytes(receipt))
        os.replace(staging, final)
        _fsync_directory(run_dir)
        return final
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _safe_bytes(path: Path, root: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise P2RHostError(f"required input is missing or unsafe: {path.name}")
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise P2RHostError(f"required input escapes its fixed root: {path.name}") from exc
    data = path.read_bytes()
    if not data or len(data) > MAX_INPUT_BYTES:
        raise P2RHostError(f"required input is empty or oversized: {path.name}")
    return data


def _safe_eval_archive(path: Path, root: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise P2RHostError("coherence Inspect archive is missing or unsafe")
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise P2RHostError("coherence Inspect archive escapes the fixed root") from exc
    data = path.read_bytes()
    if not data or len(data) > MAX_EVAL_LOG_BYTES:
        raise P2RHostError("coherence Inspect archive is empty or oversized")
    return data


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _safe_input(path: Path, root: Path) -> bytes:
    data = _safe_bytes(path, root)
    try:
        value = json.loads(data)
    except json.JSONDecodeError as exc:
        raise P2RHostError(f"required input is invalid JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise P2RHostError(f"required input is not an object: {path.name}")
    return data


def _validated_phase2_receipt_chain(
    run_dir: Path,
    *,
    input_receipt: bytes,
    qualification_run_id: str,
) -> bytes:
    previous = input_receipt
    for phase in ("phase0", "phase1", "phase2"):
        relative = PHASE_RECEIPT_PATHS[phase]
        receipt_bytes = _safe_input(run_dir / relative, run_dir)
        try:
            value = json.loads(receipt_bytes)
            validated = validate_phase_receipt(
                value,
                previous_receipt_bytes=previous,
            )
            if receipt_bytes != phase_receipt_bytes(validated):
                raise P2RPhaseContractError("phase receipt bytes are not canonical")
            if (
                validated.get("phase") != phase
                or validated.get("runId") != qualification_run_id
            ):
                raise P2RPhaseContractError("phase receipt identity is inconsistent")
            verify_raw_artifact_manifest(
                run_dir,
                validated["inputArtifacts"],
                allowed_paths=validated["inputArtifacts"],
            )
            verify_raw_artifact_manifest(
                run_dir,
                validated["outputArtifacts"],
                allowed_paths=validated["outputArtifacts"],
            )
            if phase == "phase0":
                degraded = run_dir / "phase0" / ".connectors_degraded"
                if degraded.exists() or degraded.is_symlink():
                    raise P2RPhaseContractError("Phase 0 degraded marker is present")
                if _safe_bytes(
                    run_dir / "phase0" / ".lit_grounding_mode", run_dir
                ) != b"real":
                    raise P2RPhaseContractError(
                        "Phase 0 grounding mode is not exactly real"
                    )
            if phase == "phase2":
                required = {
                    "phase2_select/phase2_select_output.json",
                    "phase2_generate/phase2_generate_output.json",
                }
                if not required.issubset(validated["outputArtifacts"]):
                    raise P2RPhaseContractError(
                        "Phase 2 receipt does not bind coherence inputs"
                    )
        except (json.JSONDecodeError, P2RPhaseContractError) as exc:
            raise P2RHostError("P2R Phase 0-2 receipt chain is invalid") from exc
        previous = receipt_bytes
    return previous


def _utc_datetime(value: object, *, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise P2RHostError(f"P2R {field} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise P2RHostError(f"P2R {field} is invalid") from exc
    if parsed.tzinfo != timezone.utc:
        raise P2RHostError(f"P2R {field} is not UTC")
    return parsed


def _validated_connector_receipt(run_dir: Path) -> bytes:
    input_receipt_bytes = _safe_input(run_dir / "p2r-input-receipt.json", run_dir)
    input_receipt = _validated_input_receipt(input_receipt_bytes)
    evidence_dir = run_dir / "connector-qualification"
    if evidence_dir.is_symlink() or not evidence_dir.is_dir():
        raise P2RHostError("P2R connector qualification evidence is missing or unsafe")
    evidence_dir = evidence_dir.resolve(strict=True)
    try:
        evidence_dir.relative_to(run_dir)
    except ValueError as exc:
        raise P2RHostError("P2R connector qualification evidence escapes the run") from exc
    expected_names = {"connector-receipt.json"} | {
        f"{name}-hits.json" for name in CONNECTOR_ORDER
    }
    if {item.name for item in evidence_dir.iterdir()} != expected_names:
        raise P2RHostError("P2R connector qualification evidence is incomplete")
    receipt_bytes = _safe_input(
        evidence_dir / "connector-receipt.json", evidence_dir
    )
    receipt = json.loads(receipt_bytes)
    if (
        set(receipt)
        != {
            "protocol",
            "status",
            "degraded",
            "qualificationRunId",
            "p2rInputReceiptSha256",
            "inputIssuedAt",
            "qualifiedAt",
            "query",
            "asOf",
            "researchStudioCommit",
            "researchStudioReuseRoot",
            "pythonVersion",
            "baseImage",
            "retryPolicy",
            "qualifierSha256",
            "requirementsLockSha256",
            "scriptSha256",
            "packageVersions",
            "connectors",
            "artifacts",
            "claimLevel",
        }
        or receipt.get("protocol") != CONNECTOR_PROTOCOL
        or receipt.get("status") != "ready"
        or receipt.get("degraded") is not False
        or receipt.get("qualificationRunId") != input_receipt["qualificationRunId"]
        or receipt.get("p2rInputReceiptSha256") != _sha256(input_receipt_bytes)
        or receipt.get("inputIssuedAt") != input_receipt["issuedAt"]
        or receipt.get("query") != CONNECTOR_FIXED_QUERY
        or receipt.get("asOf")
        != CONNECTOR_QUALIFICATION_AS_OF.isoformat().replace("+00:00", "Z")
        or receipt.get("researchStudioCommit") != RESEARCHSTUDIO_COMMIT
        or receipt.get("researchStudioReuseRoot")
        != {
            "fileCount": RESEARCHSTUDIO_REUSE_ROOT_FILE_COUNT,
            "totalBytes": RESEARCHSTUDIO_REUSE_ROOT_TOTAL_BYTES,
            "aggregateSha256": RESEARCHSTUDIO_REUSE_ROOT_AGGREGATE_SHA256,
        }
        or receipt.get("pythonVersion") != CONNECTOR_PYTHON_VERSION
        or receipt.get("baseImage") != CONNECTOR_BASE_IMAGE
        or receipt.get("retryPolicy") != CONNECTOR_RETRY_POLICY
        or receipt.get("qualifierSha256")
        != _sha256(Path(connector_qualification.__file__).read_bytes())
        or receipt.get("requirementsLockSha256")
        != CONNECTOR_REQUIREMENTS_LOCK_SHA256
        or receipt.get("scriptSha256") != CONNECTOR_SCRIPT_HASHES
        or receipt.get("packageVersions") != CONNECTOR_PACKAGE_VERSIONS
        or receipt.get("claimLevel") != "qualification_only"
    ):
        raise P2RHostError("P2R connector receipt does not match the locked profile")
    issued_at = _utc_datetime(input_receipt["issuedAt"], field="input issuedAt")
    qualified_at = _utc_datetime(receipt.get("qualifiedAt"), field="qualifiedAt")
    now = datetime.now(timezone.utc)
    if (
        qualified_at < issued_at - timedelta(seconds=MAX_P2R_CLOCK_SKEW_SECONDS)
        or qualified_at > now + timedelta(seconds=MAX_P2R_CLOCK_SKEW_SECONDS)
        or (now - qualified_at).total_seconds() > MAX_P2R_QUALIFICATION_AGE_SECONDS
    ):
        raise P2RHostError("P2R connector qualification is not fresh or ordered")
    connector_facts = receipt.get("connectors")
    artifacts = receipt.get("artifacts")
    expected_artifacts = {f"{name}-hits.json" for name in CONNECTOR_ORDER}
    if (
        not isinstance(connector_facts, dict)
        or set(connector_facts) != set(CONNECTOR_ORDER)
        or not isinstance(artifacts, dict)
        or set(artifacts) != expected_artifacts
    ):
        raise P2RHostError("P2R connector receipt is incomplete")
    for name in CONNECTOR_ORDER:
        filename = f"{name}-hits.json"
        fact = connector_facts[name]
        artifact = artifacts[filename]
        hit_count = fact.get("hitCount") if isinstance(fact, dict) else None
        artifact_size = artifact.get("sizeBytes") if isinstance(artifact, dict) else None
        artifact_sha = artifact.get("sha256") if isinstance(artifact, dict) else None
        if (
            not isinstance(fact, dict)
            or set(fact)
            != {
                "status",
                "hitCount",
                "authMode",
                "artifact",
                "probeAttempts",
            }
            | ({"successfulVenueCount"} if name == "openreview" else set())
            or fact.get("status") != "ready"
            or fact.get("artifact") != filename
            or isinstance(hit_count, bool)
            or not isinstance(hit_count, int)
            or hit_count <= 0
            or not isinstance(artifact, dict)
            or set(artifact) != {"sha256", "sizeBytes"}
            or isinstance(artifact_size, bool)
            or not isinstance(artifact_size, int)
            or artifact_size <= 0
            or not isinstance(artifact_sha, str)
        ):
            raise P2RHostError(f"P2R connector fact is invalid: {name}")
        attempts = fact.get("probeAttempts")
        if not isinstance(attempts, list) or not attempts:
            raise P2RHostError(f"P2R connector probe history is invalid: {name}")
        if attempts == [{"sequence": 1, "outcome": "ready"}]:
            pass
        elif name == "semanticscholar" and len(attempts) == 2:
            first, second = attempts
            error = first.get("error") if isinstance(first, dict) else None
            backoff = first.get("backoffSeconds") if isinstance(first, dict) else None
            if (
                not isinstance(first, dict)
                or set(first) != {"sequence", "outcome", "error", "backoffSeconds"}
                or first.get("sequence") != 1
                or first.get("outcome") != "failed"
                or error
                != {
                    "type": "HTTPError",
                    "httpStatus": 429,
                    "category": "rate_limited",
                }
                or isinstance(backoff, bool)
                or not isinstance(backoff, int)
                or not (
                    CONNECTOR_RETRY_POLICY["semanticscholar"]["retryAfterSeconds"]["min"]
                    <= backoff
                    <= CONNECTOR_RETRY_POLICY["semanticscholar"]["retryAfterSeconds"]["max"]
                )
                or second != {"sequence": 2, "outcome": "ready"}
            ):
                raise P2RHostError(
                    "P2R Semantic Scholar retry evidence is inconsistent"
                )
        else:
            raise P2RHostError(f"P2R connector probe history is invalid: {name}")
        if name == "openreview":
            if (
                fact.get("authMode") != "credentials_present"
                or isinstance(fact.get("successfulVenueCount"), bool)
                or not isinstance(fact.get("successfulVenueCount"), int)
                or not 1 <= fact.get("successfulVenueCount") <= 3
            ):
                raise P2RHostError("P2R OpenReview qualification is not authenticated")
        elif name == "arxiv" and fact.get("authMode") != "anonymous":
            raise P2RHostError("P2R arXiv auth mode is invalid")
        elif name in {"openalex", "semanticscholar"} and fact.get("authMode") not in {
            "anonymous",
            "api_key",
        }:
            raise P2RHostError(f"P2R connector auth mode is invalid: {name}")
        data = _safe_bytes(evidence_dir / filename, evidence_dir)
        if (
            artifact_size != len(data)
            or artifact_sha != _sha256(data)
        ):
            raise P2RHostError(f"P2R connector artifact integrity failed: {name}")
        try:
            hits = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise P2RHostError(f"P2R connector artifact is invalid JSON: {name}") from exc
        try:
            normalized = _public_hits(name, hits)
        except Exception as exc:
            raise P2RHostError(f"P2R connector artifact is invalid: {name}") from exc
        if len(normalized) != hit_count:
            raise P2RHostError(f"P2R connector hit count differs: {name}")
    return receipt_bytes


def _validated_input_receipt(data: bytes) -> dict[str, Any]:
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise P2RHostError("P2R input receipt is invalid JSON") from exc
    required_keys = {
        "protocol",
        "status",
        "qualificationRunId",
        "issuedAt",
        "projectId",
        "literatureRunId",
        "title",
        "researchQuestion",
        "researchQuestionSha256",
        "sourceCount",
        "bundleSha256",
        "lockedProfile",
        "handoff",
        "scientificClaim",
        "claimLevel",
    }
    if not isinstance(value, dict) or set(value) != required_keys:
        raise P2RHostError("P2R input receipt has the wrong shape")
    question = value.get("researchQuestion")
    if (
        value.get("protocol") != P2R_INPUT_PROTOCOL
        or value.get("status") != "verified"
        or not isinstance(value.get("qualificationRunId"), str)
        or QUALIFICATION_RUN_ID_PATTERN.fullmatch(value.get("qualificationRunId")) is None
        or value.get("projectId") != LOCKED_V01_BUNDLE.project_id
        or value.get("literatureRunId") != LOCKED_V01_BUNDLE.run_id
        or value.get("sourceCount") != LOCKED_V01_SOURCE_COUNT
        or value.get("bundleSha256") != LOCKED_V01_BUNDLE_SHA256
        or question != LOCKED_V01_RESEARCH_QUESTION
        or value.get("researchQuestionSha256")
        != _sha256(LOCKED_V01_RESEARCH_QUESTION.encode("utf-8"))
        or value.get("lockedProfile")
        != {
            "researchYamlSha256": LOCKED_V01_BUNDLE.research_yaml_sha256,
            "manifestSha256": LOCKED_V01_BUNDLE.manifest_sha256,
            "receiptSha256": LOCKED_V01_BUNDLE.receipt_sha256,
            "sourceLockSha256": LOCKED_V01_BUNDLE.source_lock_sha256,
        }
        or value.get("handoff")
        != {
            "mode": "eligibility_and_exact_research_question",
            "upstreamPhase0RetrievalRequired": True,
            "v01ReviewInjected": False,
        }
        or value.get("scientificClaim") != "none"
        or value.get("claimLevel") != "qualification_only"
    ):
        raise P2RHostError("P2R input receipt does not match the locked V0.1 handoff")
    _utc_datetime(value.get("issuedAt"), field="input issuedAt")
    title = value.get("title")
    if not isinstance(title, str) or not title.strip() or len(title) > 120:
        raise P2RHostError("P2R input receipt title is invalid")
    return value


@dataclass(frozen=True)
class P2RInputs:
    run_dir: Path
    input_receipt: bytes
    connector_receipt: bytes
    phase2_receipt: bytes
    prompt: bytes
    select: bytes
    candidate: bytes
    compose: Path


@dataclass(frozen=True)
class _VerifiedCoherenceHandoff:
    """Internal value returned only after the complete on-disk chain is revalidated.

    Public post-coherence operations accept roots, not this Python object. The
    leading underscore is deliberate: constructing this value is not an
    authority boundary and no public operation trusts a caller-supplied instance.
    """

    run_dir: Path
    run_id: str
    phase2_receipt_sha256: str
    coherence_receipt: bytes
    coherence_receipt_sha256: str
    raw_candidate: dict[str, Any]
    raw_candidate_bytes: bytes
    coherence_output: dict[str, Any]
    coherence_output_bytes: bytes
    blocking_findings: tuple[dict[str, Any], ...]


def _validated_coherence_handoff(inputs: P2RInputs) -> _VerifiedCoherenceHandoff:
    run_dir = inputs.run_dir.resolve(strict=True)
    coherence_dir = run_dir / "phase2_coherence"
    if coherence_dir.is_symlink() or not coherence_dir.is_dir():
        raise P2RHostError("coherence evidence directory is missing or unsafe")
    coherence_dir = coherence_dir.resolve(strict=True)
    try:
        coherence_dir.relative_to(run_dir)
    except ValueError as exc:
        raise P2RHostError("coherence evidence escapes the run directory") from exc
    allowed = {
        "phase2_coherence_output.json",
        "blocking_findings.json",
        "eval-log.json",
        "eval-log.eval",
        "execution_receipt.json",
        # H1-A pre-dispatch derivatives. Their validation remains owned by the
        # post-coherence Host; they never establish execution authority.
        "canonical-selection-receipt.json",
        "merge-next-action.json",
        "collision-next-action.json",
    }
    names = {item.name for item in coherence_dir.iterdir()}
    required = {
        "phase2_coherence_output.json",
        "blocking_findings.json",
        "eval-log.json",
        "eval-log.eval",
        "execution_receipt.json",
    }
    if not required.issubset(names) or not names.issubset(allowed):
        raise P2RHostError("coherence evidence directory has an unknown shape")

    output_bytes = _safe_input(
        coherence_dir / "phase2_coherence_output.json", coherence_dir
    )
    blocking_bytes = _safe_bytes(
        coherence_dir / "blocking_findings.json", coherence_dir
    )
    log_bytes = _safe_input(coherence_dir / "eval-log.json", coherence_dir)
    eval_archive = _safe_eval_archive(
        coherence_dir / "eval-log.eval", coherence_dir
    )
    receipt_bytes = _safe_input(
        coherence_dir / "execution_receipt.json", coherence_dir
    )
    try:
        output_value = _validate_coherence_schema(json.loads(output_bytes))
        blocking_value = json.loads(blocking_bytes)
        log_value = json.loads(log_bytes)
        receipt = json.loads(receipt_bytes)
        raw_candidate = json.loads(inputs.candidate)
        input_receipt = json.loads(inputs.input_receipt)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise P2RHostError("coherence handoff contains invalid JSON") from exc
    expected_blocking = _blocking_findings(output_value)
    if (
        not isinstance(blocking_value, list)
        or blocking_value != expected_blocking
        or blocking_bytes != _json_bytes(expected_blocking)
        or output_bytes != _json_bytes(output_value)
        or not isinstance(raw_candidate, dict)
        or not isinstance(input_receipt, dict)
    ):
        raise P2RHostError("coherence handoff facts or canonical bytes differ")

    receipt_keys = {
        "protocol",
        "phase",
        "runId",
        "previousReceiptSha256",
        "modelId",
        "inspectVersion",
        "sandboxImage",
        "promptSha256",
        "p2rInputReceiptSha256",
        "p2rConnectorReceiptSha256",
        "v01Bundle",
        "inputArtifacts",
        "toolReceipts",
        "modelRouteRunIds",
        "evalLogExport",
        "blockingFindingCount",
        "claimLevel",
        "artifacts",
    }
    expected_artifacts = {
        "phase2_coherence_output.json": {
            "sha256": _sha256(output_bytes),
            "sizeBytes": len(output_bytes),
        },
        "blocking_findings.json": {
            "sha256": _sha256(blocking_bytes),
            "sizeBytes": len(blocking_bytes),
        },
        "eval-log.json": {
            "sha256": _sha256(log_bytes),
            "sizeBytes": len(log_bytes),
        },
        "eval-log.eval": {
            "sha256": _sha256(eval_archive),
            "sizeBytes": len(eval_archive),
        },
    }
    run_id = input_receipt.get("qualificationRunId")
    if (
        not isinstance(receipt, dict)
        or set(receipt) != receipt_keys
        or receipt.get("protocol") != P2R_PROTOCOL
        or receipt.get("phase") != "researchstudio_phase2_coherence"
        or receipt.get("runId") != run_id
        or receipt.get("previousReceiptSha256") != _sha256(inputs.phase2_receipt)
        or receipt.get("modelId") != P2R_MODEL_ID
        or receipt.get("inspectVersion") != "0.3.260"
        or receipt.get("sandboxImage") != P2R_SANDBOX_IMAGE
        or receipt.get("promptSha256") != _sha256(inputs.prompt)
        or receipt.get("p2rInputReceiptSha256") != _sha256(inputs.input_receipt)
        or receipt.get("p2rConnectorReceiptSha256")
        != _sha256(inputs.connector_receipt)
        or receipt.get("v01Bundle")
        != {
            "projectId": input_receipt.get("projectId"),
            "literatureRunId": input_receipt.get("literatureRunId"),
            "bundleSha256": input_receipt.get("bundleSha256"),
            "sourceCount": input_receipt.get("sourceCount"),
        }
        or receipt.get("inputArtifacts")
        != {
            "phase2_select/phase2_select_output.json": _sha256(inputs.select),
            "phase2_generate/phase2_generate_output.json": _sha256(inputs.candidate),
        }
        or receipt.get("evalLogExport")
        != {
            "inspectVersion": "0.3.260",
            "format": "eval",
            "headerOnly": False,
            "resolveAttachments": "full",
        }
        or receipt.get("blockingFindingCount") != len(expected_blocking)
        or receipt.get("claimLevel") != "qualification_only"
        or receipt.get("artifacts") != expected_artifacts
        or receipt_bytes != _json_bytes(receipt)
        or not isinstance(run_id, str)
        or QUALIFICATION_RUN_ID_PATTERN.fullmatch(run_id) is None
    ):
        raise P2RHostError("coherence execution receipt does not bind the handoff")

    try:
        eval_log = read_eval_log(
            BytesIO(eval_archive), format="eval", resolve_attachments="full"
        )
    except Exception as exc:
        raise P2RHostError("coherence Inspect archive cannot be re-read") from exc
    archive_value = eval_log.model_dump(mode="json", exclude_none=True)
    if log_bytes != _json_bytes(archive_value) or log_value != archive_value:
        raise P2RHostError("coherence JSON export differs from the original Inspect archive")
    if eval_log.status != "success" or not eval_log.samples or len(eval_log.samples) != 1:
        raise P2RHostError("coherence EvalLog has no unique successful sample")
    sample = eval_log.samples[0]
    try:
        logged_value = _coherence_value(sample)
        logged_tool_receipts = _validated_tool_receipts(sample)
        logged_route_run_ids = _modelmirror_route_run_ids(sample)
    except P2RHostError:
        raise
    except Exception as exc:
        raise P2RHostError("coherence EvalLog evidence cannot be re-derived") from exc
    if logged_value != output_value:
        raise P2RHostError("coherence EvalLog terminal output differs from the handoff")

    tool_receipts = receipt.get("toolReceipts")
    if not isinstance(tool_receipts, list) or len(tool_receipts) != 1:
        raise P2RHostError("coherence handoff has no unique trusted tool receipt")
    tool_receipt = tool_receipts[0]
    if not isinstance(tool_receipt, dict) or not isinstance(
        tool_receipt.get("toolCallId"), str
    ):
        raise P2RHostError("coherence tool receipt identity is invalid")
    envelope = {key: value for key, value in tool_receipt.items() if key != "toolCallId"}
    script = output_value["trace_report"]["dry_run"]["execution"]["script"]
    _validate_tool_envelope(envelope, script)
    _validate_coherence_execution(output_value, tool_receipts)
    route_run_ids = receipt.get("modelRouteRunIds")
    if (
        not isinstance(route_run_ids, list)
        or not route_run_ids
        or any(not isinstance(item, str) or not item.strip() for item in route_run_ids)
        or len(route_run_ids) != len(set(route_run_ids))
    ):
        raise P2RHostError("coherence model route identity is invalid")
    if tool_receipts != logged_tool_receipts or route_run_ids != logged_route_run_ids:
        raise P2RHostError("coherence receipt differs from facts re-derived from EvalLog")

    return _VerifiedCoherenceHandoff(
        run_dir=run_dir,
        run_id=run_id,
        phase2_receipt_sha256=_sha256(inputs.phase2_receipt),
        coherence_receipt=receipt_bytes,
        coherence_receipt_sha256=_sha256(receipt_bytes),
        raw_candidate=raw_candidate,
        raw_candidate_bytes=inputs.candidate,
        coherence_output=output_value,
        coherence_output_bytes=output_bytes,
        blocking_findings=tuple(expected_blocking),
    )


def load_verified_coherence_handoff(
    *, repository_root: Path, skill_root: Path, run_dir: Path
) -> _VerifiedCoherenceHandoff:
    """Revalidate the complete pre-H1 chain without executing a model or tool."""

    return _validated_coherence_handoff(
        _load_inputs(
            repository_root=repository_root,
            skill_root=skill_root,
            run_dir=run_dir,
        )
    )


def _load_inputs(*, repository_root: Path, skill_root: Path, run_dir: Path) -> P2RInputs:
    if repository_root.is_symlink() or skill_root.is_symlink() or run_dir.is_symlink():
        raise P2RHostError("P2R roots must not be symbolic links")
    repository_root = repository_root.resolve(strict=True)
    skill_root = skill_root.resolve(strict=True)
    run_dir = run_dir.resolve(strict=True)
    if not run_dir.is_dir():
        raise P2RHostError("run directory is missing or unsafe")
    prompt_path = skill_root / COHERENCE_PROMPT_RELATIVE
    if prompt_path.is_symlink() or not prompt_path.is_file():
        raise P2RHostError("locked ResearchStudio coherence prompt is missing")
    prompt = prompt_path.read_bytes()
    if _sha256(prompt) != COHERENCE_PROMPT_SHA256:
        raise P2RHostError("locked ResearchStudio coherence prompt hash does not match")
    try:
        verify_locked_assets(skill_root)
    except P2RPhaseContractError as exc:
        raise P2RHostError("locked ResearchStudio reuse root does not match") from exc
    compose = repository_root / "extensions/ai-research" / SANDBOX_COMPOSE_RELATIVE
    if compose.is_symlink() or not compose.is_file():
        raise P2RHostError("fixed P2R sandbox compose file is missing or unsafe")
    compose_bytes = compose.read_bytes()
    if _sha256(compose_bytes) != SANDBOX_COMPOSE_SHA256:
        raise P2RHostError("fixed P2R sandbox compose hash does not match")
    input_receipt = _safe_input(run_dir / "p2r-input-receipt.json", run_dir)
    input_value = _validated_input_receipt(input_receipt)
    phase2_receipt = _validated_phase2_receipt_chain(
        run_dir,
        input_receipt=input_receipt,
        qualification_run_id=input_value["qualificationRunId"],
    )
    return P2RInputs(
        run_dir=run_dir,
        input_receipt=input_receipt,
        connector_receipt=_validated_connector_receipt(run_dir),
        phase2_receipt=phase2_receipt,
        prompt=prompt,
        select=_safe_input(run_dir / "phase2_select/phase2_select_output.json", run_dir),
        candidate=_safe_input(run_dir / "phase2_generate/phase2_generate_output.json", run_dir),
        compose=compose,
    )


def _bridge_runtime_configuration() -> tuple[str, str]:
    endpoint = os.getenv(
        "AI_RESEARCH_S2S_ENDPOINT", "http://127.0.0.1:8000/api/ai-research/v1"
    )
    token = os.getenv("AI_RESEARCH_P2R_S2S_TOKEN", "")
    configured_model = os.getenv("AI_RESEARCH_HYPOTHESIS_MODEL_ID", "")
    if not token or configured_model != P2R_MODEL_ID:
        raise P2RHostError(
            "P2R-specific model bridge credentials or model identity are unavailable"
        )
    return endpoint, token


def run_coherence(*, repository_root: Path, skill_root: Path, run_dir: Path) -> Path:
    if inspect_ai.__version__ != "0.3.260":
        raise P2RHostError("P2R requires Inspect AI 0.3.260 exactly")
    inputs = _load_inputs(
        repository_root=repository_root, skill_root=skill_root, run_dir=run_dir
    )
    input_receipt = _validated_input_receipt(inputs.input_receipt)
    endpoint, token = _bridge_runtime_configuration()
    coherence_artifacts = [
        ("phase2_select/phase2_select_output.json", inputs.select),
        ("phase2_generate/phase2_generate_output.json", inputs.candidate),
    ]
    phase2_receipt_sha256 = _sha256(inputs.phase2_receipt)
    model = Model(
        ModelMirrorBridgeAPI(
            base_url=endpoint,
            token=token,
            phase=P2R_COHERENCE_PHASE,
            prompt=inputs.prompt,
            qualification_run_id=input_receipt["qualificationRunId"],
            previous_receipt_sha256=phase2_receipt_sha256,
            artifacts=coherence_artifacts,
        ),
        GenerateConfig(max_tokens=30_000, temperature=0.2, parallel_tool_calls=False),
    )
    phase_messages = _phase_artifact_messages(
        phase=P2R_COHERENCE_PHASE,
        qualification_run_id=input_receipt["qualificationRunId"],
        previous_receipt_sha256=phase2_receipt_sha256,
        artifacts=coherence_artifacts,
    )
    sample = Sample(
        id="researchstudio-coherence",
        input=[
            ChatMessageSystem(content=inputs.prompt.decode("utf-8")),
            *phase_messages,
        ],
    )
    task = Task(
        dataset=[sample],
        solver=[use_tools(p2r_python(), tool_choice="auto"), generate(tool_calls="loop")],
        model=model,
        sandbox=SandboxEnvironmentSpec(type="docker", config=str(inputs.compose)),
        fail_on_error=True,
        message_limit=12,
        time_limit=1800,
        working_limit=600,
        display_name="ResearchStudio Phase 2.3 coherence P2R",
    )
    log_dir = inputs.run_dir / f".p2r-inspect-{uuid.uuid4().hex}"
    prior_output_limit = os.environ.get("INSPECT_SANDBOX_MAX_EXEC_OUTPUT_SIZE")
    os.environ["INSPECT_SANDBOX_MAX_EXEC_OUTPUT_SIZE"] = str(MAX_STREAM_BYTES)
    try:
        logs = eval(
            task,
            model=model,
            display="none",
            score=False,
            log_samples=True,
            log_dir=str(log_dir),
            max_samples=1,
            max_sandboxes=1,
        )
    finally:
        if prior_output_limit is None:
            os.environ.pop("INSPECT_SANDBOX_MAX_EXEC_OUTPUT_SIZE", None)
        else:
            os.environ["INSPECT_SANDBOX_MAX_EXEC_OUTPUT_SIZE"] = prior_output_limit
    if len(logs) != 1 or logs[0].status != "success":
        raise P2RHostError("Inspect coherence task did not reach success")
    log = logs[0]
    if not log.samples or len(log.samples) != 1:
        raise P2RHostError("Inspect coherence task did not preserve exactly one sample")
    result_sample = log.samples[0]
    receipts = _validated_tool_receipts(result_sample)
    if not receipts:
        raise P2RHostError("coherence phase produced no trusted Python tool receipt")
    value = _coherence_value(result_sample)
    _validate_coherence_execution(value, receipts)
    blocking = _blocking_findings(value)
    route_run_ids = _modelmirror_route_run_ids(result_sample)
    location = getattr(log, "location", None)
    if not isinstance(location, (str, Path)) or not str(location).strip():
        raise P2RHostError("Inspect did not expose the original EvalLog location")
    log_root = log_dir.resolve(strict=True)
    archive_path = Path(location)
    archives = [item.resolve(strict=True) for item in log_root.rglob("*.eval")]
    if len(archives) != 1 or archive_path.resolve(strict=True) != archives[0]:
        raise P2RHostError("Inspect did not preserve one unique original EvalLog")
    eval_archive = _safe_eval_archive(archive_path, log_root)
    try:
        archived_log = read_eval_log(
            BytesIO(eval_archive), format="eval", resolve_attachments="full"
        )
    except Exception as exc:
        raise P2RHostError("Inspect original EvalLog cannot be re-read") from exc
    log_value = archived_log.model_dump(mode="json", exclude_none=True)
    if (
        archived_log.status != "success"
        or not archived_log.samples
        or len(archived_log.samples) != 1
    ):
        raise P2RHostError("Inspect original EvalLog has no unique successful sample")
    receipt = {
        "protocol": P2R_PROTOCOL,
        "phase": "researchstudio_phase2_coherence",
        "runId": input_receipt["qualificationRunId"],
        "previousReceiptSha256": phase2_receipt_sha256,
        "modelId": P2R_MODEL_ID,
        "inspectVersion": "0.3.260",
        "sandboxImage": P2R_SANDBOX_IMAGE,
        "promptSha256": _sha256(inputs.prompt),
        "p2rInputReceiptSha256": _sha256(inputs.input_receipt),
        "p2rConnectorReceiptSha256": _sha256(inputs.connector_receipt),
        "v01Bundle": {
            "projectId": input_receipt["projectId"],
            "literatureRunId": input_receipt["literatureRunId"],
            "bundleSha256": input_receipt["bundleSha256"],
            "sourceCount": input_receipt["sourceCount"],
        },
        "inputArtifacts": {
            "phase2_select/phase2_select_output.json": _sha256(inputs.select),
            "phase2_generate/phase2_generate_output.json": _sha256(inputs.candidate),
        },
        "toolReceipts": receipts,
        "modelRouteRunIds": route_run_ids,
        "evalLogExport": {
            "inspectVersion": "0.3.260",
            "format": "eval",
            "headerOnly": False,
            "resolveAttachments": "full",
        },
        "blockingFindingCount": len(blocking),
        "claimLevel": "qualification_only",
    }
    return _atomic_deliver(
        inputs.run_dir,
        value=value,
        blocking=blocking,
        eval_log=log_value,
        eval_archive=eval_archive,
        receipt=receipt,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the fixed P2R coherence host")
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--skill-root", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    output = run_coherence(
        repository_root=args.repository_root,
        skill_root=args.skill_root,
        run_dir=args.run_dir,
    )
    print(json.dumps({"status": "delivered", "path": str(output)}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
