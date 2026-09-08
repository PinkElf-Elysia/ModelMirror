from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import time
from dataclasses import dataclass, field
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
MAX_HYPOTHESIS_TOTAL_MESSAGE_CHARS = 512_000
MAX_MESSAGE_CHARS = MAX_TOTAL_MESSAGE_CHARS
MAX_TOOLS = 32
MAX_TOOL_DESCRIPTION_CHARS = 2_048
MAX_TOOL_ARGUMENT_CHARS = 65_536
MAX_TOOL_SCHEMA_BYTES = 65_536
MAX_TOTAL_TOOL_SCHEMA_BYTES = 256_000
MAX_REQUEST_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
MAX_STREAM_IDENTITY_BUFFER_BYTES = 1024 * 1024
TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
P2R_COHERENCE_PROMPT_SHA256 = (
    "b53c6ff219a1b4eb1689a9f5728c21e9c8b8b0de1e93babf1b5814214447bb02"
)
P2R_PHASE_REQUEST_PROTOCOL = "modelmirror-ai-research-p2r-phase-request-v1"
P2R_QUALIFICATION_RUN_PATTERN = re.compile(r"^p2rq_[0-9a-f]{32}$")
P2R_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
P2R_ARTIFACT_PATH_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$")
P2R_MAX_ARTIFACT_CHUNK_CHARS = 100_000
P2R_FIXED_TEMPERATURE = 0.2
P2R_FIXED_MAX_TOKENS = 30_000
P2R_PYTHON_RECEIPT_PROTOCOL = "modelmirror-ai-research-p2r-v1"
P2R_PYTHON_SANDBOX_IMAGE = (
    "python@sha256:401f6e1a67dad31a1bd78e9ad22d0ee0a3b52154e6bd30e90be696bb6a3d7461"
)
P2R_PYTHON_LIMITS = {
    "scriptBytes": 65_536,
    "streamBytes": 262_144,
    "timeoutSeconds": 30,
    "visibleStreamBytes": 49_152,
}
P2R_LOCKED_STATIC_ARTIFACT_SHA256 = {
    "references/ideation-patterns/overview.md": "20014a5167c41a0a71b492bb4b20e25947b4cf30c7f86234c020e8d45f7450b2",
    "references/ideation-patterns/companion-combos.md": "d3165ba86ae1546da37f8de0cecff93499afa700847e491ec071acdad12d9f23",
    "references/ideation-sub-patterns/overview.md": "bef036e8aed185e598f98caf45984b2444b47a45e3f35babe3bb038ff006a8fa",
    "references/ideation-sub-patterns/C00.md": "0bc790076bc61bc5a902094b3b1326375b20a2210540b9665c97728f8a869d56",
    "references/ideation-sub-patterns/C01.md": "a09198aec391b4beef0c9630b9c1a1793275ee6b72e8276646b76a95d69b7a3c",
    "references/ideation-sub-patterns/C02.md": "c526fdc812d2e955da7de11c64be2a0858fa65f331ae0679b5d9170a1d96b5ff",
    "references/ideation-sub-patterns/C03.md": "8c69e090645656f141b869a7701f6f611917395f2abf6578eced99520dea6da9",
    "references/ideation-sub-patterns/C04.md": "e479b0ce12a37ce4c9bc052fa2dd3cc9c7f52b77be4b619253fda8defa0c23fe",
    "references/ideation-sub-patterns/C05.md": "e6c07526be463b48ef858270dba3de30170ecb3fcde37ad66d4dc635e0f34d48",
    "references/ideation-sub-patterns/C06.md": "d1570fddded634848788b33c6a731dbce440d0450e6f3c550f0f517d87a7bba8",
    "references/ideation-sub-patterns/C07.md": "1cf23b65fbee15bbbffcda7f4af3542f60cc167134efce2e203cd0fc4aede69f",
    "references/ideation-sub-patterns/C08.md": "dd9b19e9af3315d69cd3435f2033c87d1d6ca44126603db2595ece8952f0e16b",
    "references/ideation-sub-patterns/C09.md": "e178b7925e08f94311f28639a88030f84ece1468b7080707472afe432ff4126e",
    "references/ideation-sub-patterns/C10.md": "8724aaff35a9704544a9be7625e6a05d58c606335f3a65e99a825d3bbce8366a",
    "references/ideation-sub-patterns/C11.md": "74f197a89da72069cef06640e425cb5fed8b36fa3f2247ac50690424ec904c65",
    "references/ideation-sub-patterns/C12.md": "6157bfd876ba96f837c0f577399f6afc1602e419c45fe77424f57b5e446a8927",
    "references/ideation-sub-patterns/C13.md": "7e19113e0aff6d02e77fc4a9898c6c5edae5a1b20f6c46bf65cd78fae0e62d9b",
    "references/ideation-sub-patterns/C14.md": "5eb5d1ba44a72a8e8274a8958a1e9ac64b0badb96896f637d2072733e2312abe",
    "references/ideation-sub-patterns/C15.md": "fce7d01cac2fe7f8562d5de2af87159547d7a7e9bb9b115560af14a00a6f8999",
    "references/ideation-sub-patterns/C16.md": "0978269b1da55a84902d42087c8d481a3fb40c6d299870d760ff9a64df1ed70e",
    "references/ideation-sub-patterns/C17.md": "7909db559de1066dee94d425dd43285f7a88c619ba65870632bf787629aaf167",
    "references/ideation-sub-patterns/C18.md": "871ad780b2120f7f4bd0930a3ae0d2c66e3928368a5ffbf36f4777c3fb8d01a6",
    "references/ideation-sub-patterns/C19.md": "80f960607eadbb4c61eb9e8c9b7394e76d36b568700fdcc73b53ea9cc7ac6cce",
    "references/ideation-sub-patterns/C20.md": "44b744d5822c8c662171e2f9607e78f0dd57246938254a22354e05386841c63f",
    "references/ideation-sub-patterns/C21.md": "5f37f2da95b53bfc32fa8a257293cbf549dc1959a45e933949e04bad4b65e89f",
    "references/ideation-sub-patterns/C22.md": "094fa0747a14ae5f706575ad7be75604d7ccd307420e39ad22c79bc870c5a17c",
    "references/ideation-sub-patterns/C23.md": "d301755650014fa6d6df8c14faadbdc32aea4952c2bca76e209981495682f455",
    "references/ideation-sub-patterns/C24.md": "b4f8759c8da5248c192265a3b42dfd6356e98fcae95a8f8d6048dabeee28bd8a",
    "references/ideation-sub-patterns/C25.md": "ced46168bbcc6ab1aeaf9a242b47631d6076097bd8758c08560c3faa888da4ff",
    "references/ideation-sub-patterns/C26.md": "8b3b77eb217046fa5c64a7f5fd38a67bbfc96550b92cc6249a46241d222318ae",
    "references/ideation-sub-patterns/C27.md": "1c6a3b88c1c50aab2841681e9eeea8e6279409b02f4221eca7aa8e3059795bf4",
    "references/ideation-sub-patterns/C28.md": "d72af42e36a4333f45f633454858cada56aca66b4b3ac1c711f23abad3273b6c",
    "references/ideation-sub-patterns/C29.md": "9f619738bcf908ebbb9b27a771b83007b4370ac5fc9f84738b3c823fb5789ca1",
    "references/ideation-sub-patterns/C30.md": "7e91d3605898cddc818268020289617ca76496905342b58adc4358eedc146497",
}

# ResearchStudio IdeaSpark commit
# a785e3aca7a2f0cb9775d45a7f2b5d3bf16f076a. This registry is a closed
# qualification surface, not a generic prompt allowlist. Phase 0.5 uses the
# exact navigator NOTES constant from the locked next_step.py because upstream
# intentionally has no standalone prompt file for that step.
P2R_PHASE_CONTRACTS: dict[str, dict[str, Any]] = {
    "researchstudio.phase0.intent": {
        "promptSha256": "5560bf6a8c27ea903ce8690e73ef6ca9fcf15f0460da9f630ac1855d513babbb",
        "tools": False,
        "responseShape": "object",
        "artifactPaths": ("phase0/user_query.txt",),
    },
    "researchstudio.phase0.partition": {
        "promptSha256": "a55ad7fc652252d0de2cc4b1ba07edbcee18324518c694942c2303b554116edf",
        "tools": False,
        "responseShape": "array",
        "artifactPaths": ("phase0/user_query.txt", "phase0/lit_results.json"),
    },
    "researchstudio.phase0.pattern_summary": {
        "promptSha256": "743bcb71345a96edb6d796b00b078a25a2d859981404fd07f4d5d1b7140ff755",
        "tools": False,
        "responseShape": "object",
        "artifactPaths": ("phase0/lit_results.json",),
    },
    "researchstudio.phase0.coverage": {
        "promptSha256": "9fb00f6f376fef3a7b85d4b00c7be5b0f4f1d18be2a0d189dbe4c4da3b62bce9",
        "tools": False,
        "responseShape": "array",
        "artifactPaths": ("phase0/user_query.txt", "phase0/lit_table.md"),
    },
    "researchstudio.phase1.bottleneck": {
        "promptSha256": "58b822ea26d9de94d10a9ad7dcf9a94879489361b435cbd610c5fb310adcf68c",
        "tools": False,
        "responseShape": "object",
        "artifactPaths": (
            "phase0/user_query.txt",
            "phase0/lit_table.md",
            "phase0/fulltext_cache.json",
            "phase0/lit_results.json",
        ),
    },
    "researchstudio.phase2.select": {
        "promptSha256": "2acf389b68e0a3f752d4fc20299219c6332767f704ab36dc494b51c36c4ec435",
        "tools": False,
        "responseShape": "object",
        "artifactPaths": (
            "phase0/user_query.txt",
            "phase1/phase1_output.json",
            "references/ideation-patterns/overview.md",
            "references/ideation-patterns/companion-combos.md",
            "phase0/lit_table.md",
            "phase2_generate/closest_abstracts.json",
            "references/ideation-sub-patterns/overview.md",
        ),
    },
    "researchstudio.phase2.generate": {
        "promptSha256": "60728c5c2b351352b7bc49fafdbb320cd4413dcc33a0f40f3c73062679afa3c0",
        "tools": False,
        "responseShape": "object",
        "artifactPaths": (
            "phase2_select/phase2_select_output.json",
            "phase1/phase1_output.json",
            "phase2_generate/closest_abstracts.json",
            "phase0/fulltext_cache.json",
            "references/ideation-sub-patterns/overview.md",
        ),
        "dynamicArtifactPattern": r"references/ideation-sub-patterns/C(?:[0-2][0-9]|30)\.md",
        "dynamicArtifactMin": 1,
        "dynamicArtifactMax": 4,
    },
    "researchstudio.phase2.coherence": {
        "promptSha256": P2R_COHERENCE_PROMPT_SHA256,
        "tools": True,
        "responseShape": "object",
        "artifactPaths": (
            "phase2_select/phase2_select_output.json",
            "phase2_generate/phase2_generate_output.json",
        ),
    },
}
P2R_PYTHON_TOOL_DESCRIPTION = (
    "Execute a bounded Python dry-run in the isolated P2R sandbox."
)
P2R_PYTHON_PARAMETERS = {
    "type": "object",
    "properties": {
        "code": {
            "type": "string",
            "description": (
                "A stdlib-only Python script used to test the candidate procedure."
            ),
        }
    },
    "required": ["code"],
    "additionalProperties": False,
}


class BridgeResponseError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class P2RRequestContext:
    phase: str
    stage: Literal["text", "coherence_initial", "coherence_finalize"]
    response_shape: Literal["object", "array"]
    qualification_run_id: str
    previous_receipt_sha256: str


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
    observed_models: set[str] = field(default_factory=set)
    invalid_model_observed: bool = False

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
        if payload.get("error") is not None:
            self.invalid = True
        if "model" in payload:
            model = payload["model"]
            if isinstance(model, str) and model:
                self.observed_models.add(model)
            else:
                self.invalid_model_observed = True
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


class JsonObjectResponseFormat(StrictModel):
    type: Literal["json_object"]


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
    response_format: JsonObjectResponseFormat | None = None
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
        if message_chars > MAX_HYPOTHESIS_TOTAL_MESSAGE_CHARS:
            raise ValueError("messages exceed the total text limit")
        if self.stream_options is not None and not self.stream:
            raise ValueError("stream_options requires stream=true")
        if self.response_format is not None and not any(
            "json" in (getattr(message, "content", None) or "").casefold()
            for message in self.messages
        ):
            raise ValueError("json_object response format requires a JSON instruction")
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
    p2r_token: str
    literature_model_id: str
    hypothesis_model_id: str
    p2r_enabled: bool
    p2r_tools_enabled: bool

    @classmethod
    def from_env(cls) -> "BridgeSettings":
        enabled = os.getenv("AI_RESEARCH_S2S_ENABLED", "false").strip().casefold()
        p2r_enabled = (
            os.getenv("AI_RESEARCH_P2R_ENABLED", "false").strip().casefold()
        )
        p2r_tools_enabled = (
            os.getenv("AI_RESEARCH_P2R_TOOLS_ENABLED", "false").strip().casefold()
        )
        return cls(
            enabled=enabled in {"1", "true", "yes", "on"},
            token=os.getenv("AI_RESEARCH_S2S_TOKEN", ""),
            p2r_token=os.getenv("AI_RESEARCH_P2R_S2S_TOKEN", ""),
            literature_model_id=os.getenv(
                "AI_RESEARCH_LITERATURE_MODEL_ID", ""
            ).strip(),
            hypothesis_model_id=os.getenv(
                "AI_RESEARCH_HYPOTHESIS_MODEL_ID", ""
            ).strip(),
            p2r_enabled=p2r_enabled in {"1", "true", "yes", "on"},
            p2r_tools_enabled=p2r_tools_enabled in {"1", "true", "yes", "on"},
        )


@dataclass(frozen=True, slots=True)
class ChatBridgeAuthorization:
    settings: BridgeSettings
    phase: str | None


router = APIRouter(prefix="/api/ai-research/v1", tags=["ai-research-s2s"])


def bridge_configuration() -> BridgeSettings:
    settings = BridgeSettings.from_env()
    if (
        not settings.enabled
        or not settings.token
        or not settings.literature_model_id
        or (
            settings.hypothesis_model_id
            and settings.hypothesis_model_id == settings.literature_model_id
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI Research model bridge is not configured",
        )
    return settings


def _require_service_credential(
    authorization: str | None,
    *,
    expected_token: str,
) -> None:
    scheme, separator, credential = (authorization or "").partition(" ")
    if (
        not separator
        or scheme.casefold() != "bearer"
        or not credential
        or not secrets.compare_digest(credential, expected_token)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid AI Research service credential",
            headers={"WWW-Authenticate": "Bearer"},
        )


def require_bridge(
    authorization: Annotated[str | None, Header()] = None,
    x_modelmirror_p2r_phase: Annotated[
        str | None, Header(alias="X-ModelMirror-P2R-Phase")
    ] = None,
) -> BridgeSettings:
    settings = bridge_configuration()
    _require_service_credential(authorization, expected_token=settings.token)
    if x_modelmirror_p2r_phase is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid AI Research service credential",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return settings


def _require_chat_credential(
    settings: BridgeSettings,
    authorization: str | None,
    *,
    phase: str | None,
) -> None:
    if phase is None:
        expected_token = settings.token
    else:
        if (
            not settings.p2r_token
            or secrets.compare_digest(settings.p2r_token, settings.token)
        ):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="AI Research P2R service credential is not configured",
            )
        expected_token = settings.p2r_token
    _require_service_credential(authorization, expected_token=expected_token)


def require_chat_bridge(
    authorization: Annotated[str | None, Header()] = None,
    x_modelmirror_p2r_phase: Annotated[
        str | None, Header(alias="X-ModelMirror-P2R-Phase")
    ] = None,
    settings: BridgeSettings = Depends(bridge_configuration),
) -> ChatBridgeAuthorization:
    _require_chat_credential(
        settings,
        authorization,
        phase=x_modelmirror_p2r_phase,
    )
    return ChatBridgeAuthorization(
        settings=settings,
        phase=x_modelmirror_p2r_phase,
    )


def stable_service(
    router_service: ModelRouterService = Depends(get_model_router_service),
) -> ProviderChatStableService:
    return ProviderChatStableService(router_service)


@router.get("/models")
async def models(
    settings: BridgeSettings = Depends(require_bridge),
    stable: ProviderChatStableService = Depends(stable_service),
) -> dict[str, Any]:
    model_ids: list[str] = []
    unavailable_reasons: list[str] = []
    literature_ready, literature_reason = stable.readiness(
        settings.literature_model_id, "chat_tools"
    )
    if literature_ready:
        model_ids.append(settings.literature_model_id)
    elif literature_reason:
        unavailable_reasons.append(literature_reason)
    if (
        settings.hypothesis_model_id
        and settings.p2r_enabled
        and settings.p2r_tools_enabled
    ):
        hypothesis_ready, hypothesis_reason = stable.readiness_scoped_certified(
            settings.hypothesis_model_id,
            "chat_text",
            required_capabilities=("chat_text", "chat_tools"),
        )
        if hypothesis_ready:
            model_ids.append(settings.hypothesis_model_id)
        elif hypothesis_reason:
            unavailable_reasons.append(hypothesis_reason)
    if not model_ids:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                unavailable_reasons[0]
                if unavailable_reasons
                else "fixed model control is not ready"
            ),
        )
    return {
        "object": "list",
        "data": [
            {
                "id": model_id,
                "object": "model",
                "created": 0,
                "owned_by": "modelmirror-control-plane",
            }
            for model_id in model_ids
        ],
    }


@router.post("/chat/completions")
async def chat_completions(
    payload: ChatCompletionRequest,
    bridge_auth: ChatBridgeAuthorization = Depends(require_chat_bridge),
    stable: ProviderChatStableService = Depends(stable_service),
):
    settings = bridge_auth.settings
    x_modelmirror_p2r_phase = bridge_auth.phase
    p2r_context: P2RRequestContext | None = None
    required_scoped_capabilities: tuple[str, ...] = ()
    if payload.model == settings.literature_model_id:
        if x_modelmirror_p2r_phase is not None:
            raise HTTPException(
                status_code=422,
                detail="P2R phase header is not accepted for the literature model",
            )
        selected_model_id = settings.literature_model_id
        scoped_certified = False
    elif (
        settings.hypothesis_model_id
        and payload.model == settings.hypothesis_model_id
    ):
        selected_model_id = settings.hypothesis_model_id
        scoped_certified = True
        if x_modelmirror_p2r_phase is None:
            if payload.tools:
                raise HTTPException(
                    status_code=422,
                    detail="tools are not enabled for ordinary hypothesis requests",
                )
            required_scoped_capabilities = ("chat_text",)
        else:
            p2r_context = _require_p2r_phase_request(
                payload,
                settings=settings,
                phase=x_modelmirror_p2r_phase,
            )
            required_scoped_capabilities = ("chat_text", "chat_tools")
    else:
        raise HTTPException(status_code=422, detail="model is not enabled for AI Research")
    if payload.response_format is not None and not scoped_certified:
        raise HTTPException(
            status_code=422,
            detail="response_format is only enabled for the hypothesis model",
        )
    if (
        not scoped_certified
        and _message_char_count(payload.messages) > MAX_TOTAL_MESSAGE_CHARS
    ):
        raise HTTPException(status_code=422, detail="messages exceed the total text limit")
    capability = "chat_tools" if payload.tools else "chat_text"
    if scoped_certified:
        ready, reason = stable.readiness_scoped_certified(
            selected_model_id,
            capability,
            required_capabilities=required_scoped_capabilities,
        )
        if not ready:
            raise HTTPException(
                status_code=503,
                detail=reason or "fixed model control is not ready",
            )
    try:
        preflight = await (
            stable.begin_scoped_certified(
                selected_model_id,
                capability,
                required_capabilities=required_scoped_capabilities,
            )
            if scoped_certified
            else stable.begin(selected_model_id, capability)
        )
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
    dispatched = False

    def record_dispatch_failure(
        *,
        status: str,
        result_class: str,
        error_code: str,
        client_cancelled: bool = False,
    ) -> None:
        if dispatched:
            stable.complete(
                dispatch,
                status=status,
                result_class=result_class,
                error_code=error_code,
                client_cancelled=client_cancelled,
                e2e_ms=_elapsed_ms(started),
            )
            return
        stable.fail_undispatched(dispatch, error_code=error_code)

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
        dispatched = True
        response = await stable.transport.send_authorized_stream(client, request)
    except asyncio.CancelledError:
        await _close_nonstream_after_cancellation(response=None, client=client)
        try:
            record_dispatch_failure(
                status="cancelled",
                result_class="client_cancelled",
                error_code="provider_chat_client_cancelled",
                client_cancelled=True,
            )
        except Exception:
            pass
        raise
    except RouterServiceError as exc:
        await client.aclose()
        record_dispatch_failure(
            status="failed",
            result_class="transient_failure",
            error_code=exc.code,
        )
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc
    except (httpx.HTTPError, OSError, ValueError) as exc:
        record_dispatch_failure(
            status="failed",
            result_class="transient_failure",
            error_code="ai_research_bridge_transport_failed",
        )
        await client.aclose()
        raise HTTPException(status_code=503, detail="fixed model transport failed") from exc
    except Exception as exc:
        try:
            record_dispatch_failure(
                status="failed",
                result_class="transient_failure",
                error_code="ai_research_bridge_dispatch_failed",
            )
        except Exception:
            pass
        await client.aclose()
        raise HTTPException(
            status_code=503, detail="fixed model dispatch failed"
        ) from exc

    if response.status_code < 200 or response.status_code >= 300:
        result_class, code, hard_failure = stable.classify_http_failure(
            response.status_code
        )
        try:
            try:
                await _read_bounded_and_close(response=response, client=client)
            except Exception:
                pass
        except asyncio.CancelledError:
            await _close_nonstream_after_cancellation(
                response=response,
                client=client,
            )
            stable.complete(
                dispatch,
                status="failed",
                result_class=result_class,
                error_code=code,
                hard_failure=hard_failure,
                e2e_ms=_elapsed_ms(started),
            )
            raise
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
                requested_model=selected_model_id,
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
        content = await _read_bounded_and_close(response=response, client=client)
    except asyncio.CancelledError:
        await _close_nonstream_after_cancellation(response=response, client=client)
        stable.complete(
            dispatch,
            status="cancelled",
            result_class="client_cancelled",
            error_code="provider_chat_client_cancelled",
            client_cancelled=True,
            e2e_ms=_elapsed_ms(started),
        )
        raise
    except ValueError as exc:
        _reject_invalid_response(
            stable,
            dispatch,
            "ai_research_bridge_response_limit_exceeded",
            started,
            exc,
        )
    except Exception as exc:
        stable.complete(
            dispatch,
            status="failed",
            result_class="transient_failure",
            error_code="ai_research_bridge_response_read_failed",
            e2e_ms=_elapsed_ms(started),
        )
        raise HTTPException(
            status_code=503, detail="fixed model response failed"
        ) from exc
    try:
        value = json.loads(content)
        actual_model = _validate_completion_response(
            value,
            requested_model=selected_model_id,
            allowed_tool_names={
                tool.function.name for tool in (payload.tools or [])
            },
            p2r_context=p2r_context,
            require_model_identity=scoped_certified,
        )
        usage = value.get("usage") if isinstance(value.get("usage"), dict) else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _reject_invalid_response(
            stable, dispatch, "ai_research_bridge_invalid_json", started, exc
        )
    except BridgeResponseError as exc:
        _reject_invalid_response(stable, dispatch, exc.code, started, exc)
    stable.complete(
        dispatch,
        status="succeeded",
        result_class="success",
        actual_model=actual_model,
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
    identity_verified = False
    pending_chunks: list[str] = []
    pending_bytes = 0
    try:
        async for chunk in response.aiter_text():
            encoded_size = len(chunk.encode("utf-8"))
            total += encoded_size
            if total > MAX_RESPONSE_BYTES:
                raise ValueError("stream exceeded bridge response limit")
            pending_chunks.append(chunk)
            pending_bytes += encoded_size
            evidence.feed(chunk)
            if evidence.invalid:
                raise BridgeResponseError("provider_chat_invalid_sse")
            if evidence.invalid_model_observed:
                raise BridgeResponseError(
                    "ai_research_bridge_model_identity_invalid"
                )
            if evidence.observed_models - {requested_model}:
                raise BridgeResponseError("ai_research_bridge_model_mismatch")
            identity_verified = identity_verified or (
                requested_model in evidence.observed_models
            )
            if pending_bytes > MAX_STREAM_IDENTITY_BUFFER_BYTES:
                code = (
                    "ai_research_bridge_stream_event_buffer_exceeded"
                    if identity_verified
                    else "ai_research_bridge_stream_identity_buffer_exceeded"
                )
                raise BridgeResponseError(code)
            if evidence.buffer or not identity_verified:
                continue
            yield "".join(pending_chunks)
            pending_chunks.clear()
            pending_bytes = 0
        status_value, result_class, error_code = evidence.finish_for_bridge(
            transport_completed=True,
            allow_tool_calls=allow_tool_calls,
        )
        if evidence.invalid_model_observed:
            raise BridgeResponseError(
                "ai_research_bridge_model_identity_invalid"
            )
        if evidence.observed_models - {requested_model}:
            raise BridgeResponseError("ai_research_bridge_model_mismatch")
        if requested_model not in evidence.observed_models:
            raise BridgeResponseError(
                "ai_research_bridge_model_identity_required"
            )
        if pending_chunks and status_value == "succeeded":
            yield "".join(pending_chunks)
            pending_chunks.clear()
            pending_bytes = 0
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
    except BridgeResponseError as exc:
        if not finalized:
            stable.complete(
                dispatch,
                status="failed",
                result_class="hard_failure",
                error_code=exc.code,
                hard_failure=True,
                e2e_ms=_elapsed_ms(started),
            )
            finalized = True
        raise RuntimeError(exc.code) from exc
    except (asyncio.CancelledError, GeneratorExit):
        if not finalized:
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
        await _close_stream_resources(response=response, client=client)


async def _close_stream_resources(
    *, response: httpx.Response, client: httpx.AsyncClient
) -> None:
    close_error: BaseException | None = None
    try:
        await response.aclose()
    except asyncio.CancelledError as exc:
        close_error = exc
    except BaseException as exc:
        close_error = exc
    try:
        await client.aclose()
    except asyncio.CancelledError as exc:
        close_error = exc
    except BaseException as exc:
        close_error = close_error or exc
    if close_error is not None:
        raise close_error


async def _read_bounded(response: httpx.Response) -> bytes:
    total = 0
    chunks: list[bytes] = []
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise ValueError("response exceeded bridge limit")
        chunks.append(chunk)
    return b"".join(chunks)


async def _read_bounded_and_close(
    *,
    response: httpx.Response,
    client: httpx.AsyncClient,
) -> bytes:
    try:
        content = await _read_bounded(response)
    except asyncio.CancelledError:
        await _close_nonstream_after_cancellation(
            response=response,
            client=client,
        )
        raise
    except Exception:
        await _close_nonstream_after_cancellation(
            response=response,
            client=client,
        )
        raise

    close_error: Exception | None = None
    try:
        await response.aclose()
    except asyncio.CancelledError:
        await _close_nonstream_after_cancellation(response=None, client=client)
        raise
    except Exception as exc:
        close_error = exc
    try:
        await client.aclose()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        close_error = close_error or exc
    if close_error is not None:
        raise close_error
    return content


async def _close_nonstream_after_cancellation(
    *,
    response: httpx.Response | None,
    client: httpx.AsyncClient,
) -> None:
    """Best-effort close without replacing the caller's cancellation."""

    if response is not None:
        try:
            await response.aclose()
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
    try:
        await client.aclose()
    except asyncio.CancelledError:
        pass
    except Exception:
        pass


def _validate_completion_response(
    value: object,
    *,
    requested_model: str,
    allowed_tool_names: set[str],
    p2r_context: P2RRequestContext | None = None,
    require_model_identity: bool = False,
) -> str | None:
    if not isinstance(value, dict):
        raise BridgeResponseError("ai_research_bridge_invalid_envelope")
    actual_model = value.get("model")
    if (p2r_context is not None or require_model_identity) and actual_model is None:
        raise BridgeResponseError("ai_research_bridge_model_identity_required")
    if actual_model is not None and actual_model != requested_model:
        raise BridgeResponseError("ai_research_bridge_model_mismatch")
    choices = value.get("choices")
    if not isinstance(choices, list) or not choices:
        raise BridgeResponseError("ai_research_bridge_missing_choices")
    if p2r_context is not None:
        _validate_p2r_completion_choices(choices, p2r_context)
        return actual_model
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
    return actual_model


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


def _message_char_count(messages: list[ChatMessage]) -> int:
    total = 0
    for message in messages:
        content = getattr(message, "content", None)
        if isinstance(content, str):
            total += len(content)
        if isinstance(message, AssistantMessage):
            total += sum(len(call.function.arguments) for call in message.tool_calls)
    return total


def _require_p2r_phase_request(
    payload: ChatCompletionRequest,
    *,
    settings: BridgeSettings,
    phase: str | None,
) -> P2RRequestContext:
    if not settings.p2r_enabled or not settings.p2r_tools_enabled:
        raise HTTPException(status_code=422, detail="P2R qualification is disabled")
    if phase is None or phase not in P2R_PHASE_CONTRACTS:
        raise HTTPException(status_code=422, detail="invalid P2R phase")
    contract = P2R_PHASE_CONTRACTS[phase]
    _validate_p2r_sampling(payload)
    if not payload.messages or not isinstance(payload.messages[0], TextMessage):
        raise HTTPException(status_code=422, detail="invalid P2R phase prompt")
    system = payload.messages[0]
    if system.role != "system" or any(
        isinstance(message, TextMessage) and message.role == "system"
        for message in payload.messages[1:]
    ):
        raise HTTPException(status_code=422, detail="invalid P2R phase prompt")
    prompt_hash = hashlib.sha256(system.content.encode("utf-8")).hexdigest()
    if not secrets.compare_digest(prompt_hash, contract["promptSha256"]):
        raise HTTPException(status_code=422, detail="invalid P2R phase prompt")

    artifact_messages: list[TextMessage] = []
    tail: list[ChatMessage] = []
    reached_tail = False
    for message in payload.messages[1:]:
        if (
            not reached_tail
            and isinstance(message, TextMessage)
            and message.role == "user"
        ):
            artifact_messages.append(message)
        else:
            reached_tail = True
            tail.append(message)
    qualification_run_id, previous_receipt_sha256 = (
        _validate_p2r_artifact_envelopes(
            artifact_messages,
            phase=phase,
            contract=contract,
        )
    )

    response_shape = contract["responseShape"]
    if response_shape not in {"object", "array"}:
        raise HTTPException(status_code=422, detail="invalid P2R phase registry")
    if contract["tools"] is False:
        if tail or payload.tools:
            raise HTTPException(status_code=422, detail="invalid P2R text phase contract")
        if (response_shape == "object") != (payload.response_format is not None):
            raise HTTPException(status_code=422, detail="invalid P2R response contract")
        return P2RRequestContext(
            phase=phase,
            stage="text",
            response_shape=response_shape,
            qualification_run_id=qualification_run_id,
            previous_receipt_sha256=previous_receipt_sha256,
        )

    _validate_p2r_tool_contract(payload)
    if not tail:
        stage: Literal["coherence_initial", "coherence_finalize"] = (
            "coherence_initial"
        )
    elif (
        len(tail) == 2
        and isinstance(tail[0], AssistantMessage)
        and isinstance(tail[1], ToolMessage)
    ):
        _validate_p2r_python_receipt(tail[0], tail[1])
        stage = "coherence_finalize"
    else:
        raise HTTPException(status_code=422, detail="invalid P2R coherence history")
    return P2RRequestContext(
        phase=phase,
        stage=stage,
        response_shape=response_shape,
        qualification_run_id=qualification_run_id,
        previous_receipt_sha256=previous_receipt_sha256,
    )


def _validate_p2r_sampling(payload: ChatCompletionRequest) -> None:
    if (
        payload.temperature != P2R_FIXED_TEMPERATURE
        or payload.max_tokens != P2R_FIXED_MAX_TOKENS
        or payload.max_completion_tokens is not None
        or payload.top_p is not None
        or payload.stop is not None
        or payload.stream
        or payload.stream_options is not None
    ):
        raise HTTPException(status_code=422, detail="invalid P2R sampling contract")


def _validate_p2r_tool_contract(payload: ChatCompletionRequest) -> None:
    tools = payload.tools or []
    if len(tools) != 1:
        raise HTTPException(status_code=422, detail="invalid P2R tool contract")
    function = tools[0].function
    if (
        function.name != "python"
        or function.description != P2R_PYTHON_TOOL_DESCRIPTION
        or function.parameters != P2R_PYTHON_PARAMETERS
        or function.strict is not True
        or payload.tool_choice != "auto"
        or payload.parallel_tool_calls is not False
        or payload.response_format is None
    ):
        raise HTTPException(status_code=422, detail="invalid P2R tool contract")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _validate_p2r_artifact_envelopes(
    messages: list[TextMessage],
    *,
    phase: str,
    contract: dict[str, Any],
) -> tuple[str, str]:
    if not messages:
        raise HTTPException(status_code=422, detail="missing P2R artifact envelopes")
    path_order: list[str] = []
    groups: dict[str, list[dict[str, Any]]] = {}
    qualification_run_id: str | None = None
    previous_receipt_sha256: str | None = None
    for message in messages:
        try:
            envelope = json.loads(message.content)
        except (json.JSONDecodeError, TypeError) as exc:
            raise HTTPException(status_code=422, detail="invalid P2R artifact envelope") from exc
        if (
            not isinstance(envelope, dict)
            or _canonical_json(envelope) != message.content
            or set(envelope)
            != {
                "protocol",
                "qualificationRunId",
                "phase",
                "previousReceiptSha256",
                "artifact",
            }
        ):
            raise HTTPException(status_code=422, detail="invalid P2R artifact envelope")
        artifact = envelope.get("artifact")
        if (
            not isinstance(artifact, dict)
            or set(artifact)
            != {
                "path",
                "sha256",
                "sizeBytes",
                "chunkIndex",
                "chunkCount",
                "chunkSha256",
                "content",
            }
            or envelope.get("protocol") != P2R_PHASE_REQUEST_PROTOCOL
            or envelope.get("phase") != phase
        ):
            raise HTTPException(status_code=422, detail="invalid P2R artifact envelope")
        run_id = envelope.get("qualificationRunId")
        previous = envelope.get("previousReceiptSha256")
        if (
            not isinstance(run_id, str)
            or not P2R_QUALIFICATION_RUN_PATTERN.fullmatch(run_id)
            or not isinstance(previous, str)
            or not P2R_SHA256_PATTERN.fullmatch(previous)
        ):
            raise HTTPException(status_code=422, detail="invalid P2R artifact binding")
        if qualification_run_id is None:
            qualification_run_id = run_id
            previous_receipt_sha256 = previous
        elif run_id != qualification_run_id or previous != previous_receipt_sha256:
            raise HTTPException(status_code=422, detail="mixed P2R artifact binding")

        path = artifact.get("path")
        full_sha = artifact.get("sha256")
        chunk_sha = artifact.get("chunkSha256")
        content = artifact.get("content")
        size_bytes = artifact.get("sizeBytes")
        chunk_index = artifact.get("chunkIndex")
        chunk_count = artifact.get("chunkCount")
        if (
            not isinstance(path, str)
            or not P2R_ARTIFACT_PATH_PATTERN.fullmatch(path)
            or any(part in {"", ".", ".."} for part in path.split("/"))
            or not isinstance(full_sha, str)
            or not P2R_SHA256_PATTERN.fullmatch(full_sha)
            or not isinstance(chunk_sha, str)
            or not P2R_SHA256_PATTERN.fullmatch(chunk_sha)
            or not isinstance(content, str)
            or len(content) > P2R_MAX_ARTIFACT_CHUNK_CHARS
            or isinstance(size_bytes, bool)
            or not isinstance(size_bytes, int)
            or size_bytes < 0
            or isinstance(chunk_index, bool)
            or not isinstance(chunk_index, int)
            or isinstance(chunk_count, bool)
            or not isinstance(chunk_count, int)
            or chunk_count < 1
            or chunk_count > MAX_MESSAGES
            or chunk_index < 0
            or chunk_index >= chunk_count
        ):
            raise HTTPException(status_code=422, detail="invalid P2R artifact metadata")
        if hashlib.sha256(content.encode("utf-8")).hexdigest() != chunk_sha:
            raise HTTPException(status_code=422, detail="invalid P2R artifact chunk hash")
        if path not in groups:
            path_order.append(path)
            groups[path] = []
        elif path_order[-1] != path:
            raise HTTPException(status_code=422, detail="non-contiguous P2R artifact chunks")
        if chunk_index != len(groups[path]):
            raise HTTPException(status_code=422, detail="out-of-order P2R artifact chunk")
        groups[path].append(artifact)

    fixed_paths = list(contract["artifactPaths"])
    if path_order[: len(fixed_paths)] != fixed_paths:
        raise HTTPException(status_code=422, detail="invalid P2R artifact order")
    dynamic_paths = path_order[len(fixed_paths) :]
    dynamic_pattern = contract.get("dynamicArtifactPattern")
    if dynamic_pattern is None:
        if dynamic_paths or len(path_order) != len(fixed_paths):
            raise HTTPException(status_code=422, detail="invalid P2R artifact set")
    else:
        minimum = contract["dynamicArtifactMin"]
        maximum = contract["dynamicArtifactMax"]
        if (
            len(dynamic_paths) < minimum
            or len(dynamic_paths) > maximum
            or dynamic_paths != sorted(dynamic_paths)
            or any(re.fullmatch(dynamic_pattern, path) is None for path in dynamic_paths)
        ):
            raise HTTPException(status_code=422, detail="invalid P2R dynamic artifacts")

    for path in path_order:
        chunks = groups[path]
        expected_count = len(chunks)
        if any(item["chunkCount"] != expected_count for item in chunks):
            raise HTTPException(status_code=422, detail="incomplete P2R artifact chunks")
        content = "".join(item["content"] for item in chunks)
        raw = content.encode("utf-8")
        if (
            any(item["sha256"] != chunks[0]["sha256"] for item in chunks)
            or any(item["sizeBytes"] != chunks[0]["sizeBytes"] for item in chunks)
            or hashlib.sha256(raw).hexdigest() != chunks[0]["sha256"]
            or len(raw) != chunks[0]["sizeBytes"]
        ):
            raise HTTPException(status_code=422, detail="invalid P2R artifact integrity")
        locked_sha256 = P2R_LOCKED_STATIC_ARTIFACT_SHA256.get(path)
        if locked_sha256 is not None and not secrets.compare_digest(
            chunks[0]["sha256"], locked_sha256
        ):
            raise HTTPException(
                status_code=422, detail="invalid P2R locked artifact"
            )
    assert qualification_run_id is not None
    assert previous_receipt_sha256 is not None
    return qualification_run_id, previous_receipt_sha256


def _validate_p2r_python_receipt(
    assistant: AssistantMessage, tool_message: ToolMessage
) -> None:
    if (assistant.content or "").strip() or len(assistant.tool_calls) != 1:
        raise HTTPException(status_code=422, detail="invalid P2R coherence history")
    call = assistant.tool_calls[0]
    if call.function.name != "python" or tool_message.tool_call_id != call.id:
        raise HTTPException(status_code=422, detail="invalid P2R tool receipt binding")
    try:
        arguments = json.loads(call.function.arguments)
        receipt = json.loads(tool_message.content)
    except (json.JSONDecodeError, TypeError) as exc:
        raise HTTPException(status_code=422, detail="invalid P2R tool receipt") from exc
    if (
        not isinstance(arguments, dict)
        or set(arguments) != {"code"}
        or not isinstance(arguments.get("code"), str)
        or not arguments["code"]
        or len(arguments["code"].encode("utf-8")) > P2R_PYTHON_LIMITS["scriptBytes"]
        or not isinstance(receipt, dict)
        or _canonical_json(receipt) != tool_message.content
        or set(receipt)
        != {
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
        }
    ):
        raise HTTPException(status_code=422, detail="invalid P2R tool receipt")
    code = arguments["code"]
    code_bytes = code.encode("utf-8")
    if (
        receipt.get("protocol") != P2R_PYTHON_RECEIPT_PROTOCOL
        or receipt.get("sandboxImage") != P2R_PYTHON_SANDBOX_IMAGE
        or receipt.get("command") != ["python3", "-"]
        or receipt.get("scriptSha256") != hashlib.sha256(code_bytes).hexdigest()
        or receipt.get("scriptSizeBytes") != len(code_bytes)
        or receipt.get("limits") != P2R_PYTHON_LIMITS
        or receipt.get("truncation")
        != {"captureExceeded": False, "stderr": False, "stdout": False}
        or isinstance(receipt.get("exitCode"), bool)
        or not isinstance(receipt.get("exitCode"), int)
    ):
        raise HTTPException(status_code=422, detail="invalid P2R tool receipt")
    for stream in ("stdout", "stderr"):
        stream_value = receipt.get(stream)
        if not isinstance(stream_value, str):
            raise HTTPException(status_code=422, detail="invalid P2R tool receipt")
        stream_bytes = stream_value.encode("utf-8")
        if (
            receipt.get(f"{stream}Sha256")
            != hashlib.sha256(stream_bytes).hexdigest()
            or receipt.get(f"{stream}SizeBytes") != len(stream_bytes)
        ):
            raise HTTPException(status_code=422, detail="invalid P2R tool receipt")


def _validate_p2r_completion_choices(
    choices: list[Any], context: P2RRequestContext
) -> None:
    if len(choices) != 1 or not isinstance(choices[0], dict):
        raise BridgeResponseError("ai_research_bridge_invalid_p2r_choices")
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, dict) or message.get("role") not in {None, "assistant"}:
        raise BridgeResponseError("ai_research_bridge_invalid_p2r_message")
    content = message.get("content")
    tool_calls = message.get("tool_calls")
    if context.stage == "coherence_initial":
        if (
            (content is not None and content != "")
            or not isinstance(tool_calls, list)
            or len(tool_calls) != 1
        ):
            raise BridgeResponseError("ai_research_bridge_missing_p2r_tool_call")
        call = tool_calls[0]
        if (
            not isinstance(call, dict)
            or call.get("type") != "function"
            or not isinstance(call.get("id"), str)
            or not call["id"]
            or not isinstance(call.get("function"), dict)
            or call["function"].get("name") != "python"
            or not isinstance(call["function"].get("arguments"), str)
        ):
            raise BridgeResponseError("ai_research_bridge_invalid_tool_call")
        try:
            arguments = json.loads(call["function"]["arguments"])
        except (json.JSONDecodeError, TypeError) as exc:
            raise BridgeResponseError("ai_research_bridge_invalid_tool_call") from exc
        if (
            not isinstance(arguments, dict)
            or set(arguments) != {"code"}
            or not isinstance(arguments.get("code"), str)
            or not arguments["code"]
            or len(arguments["code"].encode("utf-8"))
            > P2R_PYTHON_LIMITS["scriptBytes"]
        ):
            raise BridgeResponseError("ai_research_bridge_invalid_tool_call")
        if choice.get("finish_reason") != "tool_calls":
            raise BridgeResponseError(
                "ai_research_bridge_invalid_p2r_finish_reason"
            )
        return
    if tool_calls is not None and tool_calls != []:
        raise BridgeResponseError("ai_research_bridge_unexpected_p2r_tool_call")
    if not isinstance(content, str) or not content.strip():
        raise BridgeResponseError("ai_research_bridge_empty_completion")
    try:
        structured = json.loads(content)
    except json.JSONDecodeError as exc:
        raise BridgeResponseError("ai_research_bridge_invalid_p2r_json") from exc
    if context.response_shape == "object" and not isinstance(structured, dict):
        raise BridgeResponseError("ai_research_bridge_invalid_p2r_shape")
    if context.response_shape == "array" and not isinstance(structured, list):
        raise BridgeResponseError("ai_research_bridge_invalid_p2r_shape")
    if choice.get("finish_reason") != "stop":
        raise BridgeResponseError("ai_research_bridge_invalid_p2r_finish_reason")


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
