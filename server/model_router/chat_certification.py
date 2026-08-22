from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

import httpx

from .provider_chat import (
    PROVIDER_CHAT_CONTRACT_VERSION,
    ProviderChatTarget,
    ProviderChatTransport,
)
from .provider_catalog import ProviderCatalogService
from .egress import ProviderEgressError
from .repository import RouterCredentialUnavailable, RouterRepositoryError
from .schemas import (
    ProviderChatCapability,
    ProviderChatCertificationChecks,
    ProviderChatCertificationListResponse,
    ProviderChatCertificationSummary,
    ProviderModelsRefreshResponse,
    RouterConnection,
)
from .service import ModelRouterService, RouterServiceError


PROVIDER_CHAT_CERTIFICATION_ENABLED_ENV = (
    "MODEL_MIRROR_PROVIDER_CHAT_CERTIFICATION_ENABLED"
)
SYNTHETIC_CERTIFICATION_PROMPT = "Reply with OK."
CHAT_TEXT_CERTIFICATION_MAX_TOKENS = 64
SYNTHETIC_TOOL_NAME = "modelmirror_certification_echo"
SYNTHETIC_TOOL_PROMPT = "Call the certification tool once with value OK."
SYNTHETIC_FILE_PROMPT = (
    "Create certification.txt as a plain text file containing exactly OK."
)
SYNTHETIC_TOOL = {
    "type": "function",
    "function": {
        "name": SYNTHETIC_TOOL_NAME,
        "description": "Return the fixed certification value without side effects.",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": ["value"],
            "properties": {"value": {"type": "string", "enum": ["OK"]}},
        },
    },
}
SYNTHETIC_FILE_TOOL_NAME = "modelmirror_create_file"
SYNTHETIC_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": SYNTHETIC_FILE_TOOL_NAME,
        "description": "Create one bounded file from a structured specification.",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": ["format_id", "filename", "content"],
            "properties": {
                "format_id": {"type": "string", "enum": ["plain_text"]},
                "filename": {
                    "type": "string",
                    "enum": ["certification.txt"],
                },
                "content": {"type": "string", "enum": ["OK"]},
            },
        },
    },
}


@dataclass(slots=True)
class _StreamEvidence:
    capability: ProviderChatCapability = "chat_text"
    checks: dict[str, bool] = field(
        default_factory=lambda: {
            "catalog_ok": True,
            "model_present": True,
            "chat_http_ok": False,
            "text_delta_observed": False,
            "tool_call_observed": False,
            "file_output_contract_observed": False,
            "capability_verified": False,
            "stream_completed": False,
            "terminal_observed": False,
        }
    )
    warning_codes: list[str] = field(default_factory=list)
    actual_model: str | None = None
    finish_reason: str | None = None
    ttft_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    tool_calls: dict[int, dict[str, str]] = field(default_factory=dict)


class _CertificationFailure(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ProviderChatCertificationService:
    def __init__(
        self,
        router_service: ModelRouterService,
        *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self.router_service = router_service
        self.repository = router_service.repository
        self.transport = ProviderChatTransport(router_service.egress_policy)
        self._client_factory = client_factory or (
            lambda: httpx.AsyncClient(
                **ProviderChatTransport.client_kwargs(certification=True)
            )
        )

    @staticmethod
    def enabled() -> bool:
        value = os.getenv(PROVIDER_CHAT_CERTIFICATION_ENABLED_ENV, "true")
        return value.strip().casefold() not in {"0", "false", "no", "off"}

    async def refresh_models(self, connection_id: str) -> ProviderModelsRefreshResponse:
        return await ProviderCatalogService(
            self.router_service
        ).refresh_connection_legacy(connection_id)

    def list(self) -> ProviderChatCertificationListResponse:
        rows = {
            (str(row["connection_id"]), str(row["capability"])): row
            for row in self._repository_method("list_chat_certifications")(
                self.router_service.tenant_id
            )
        }
        summaries = [
            self._summary(
                connection,
                capability,
                rows.get((connection.id, capability)),
            )
            for connection in self.router_service.list_connections()
            if "chat" in connection.scopes
            for capability in (
                "chat_text",
                "chat_tools",
                "chat_file_output",
            )
        ]
        return ProviderChatCertificationListResponse(
            enabled=self.enabled(),
            contract_version=PROVIDER_CHAT_CONTRACT_VERSION,
            certifications=summaries,
        )

    async def run(
        self,
        connection_id: str,
        *,
        model_id: str,
        capability: ProviderChatCapability = "chat_text",
        acknowledge_billed_call: bool,
        idempotency_key: str,
    ) -> ProviderChatCertificationSummary:
        if not self.enabled():
            raise RouterServiceError(
                "provider_chat_certification_disabled",
                "Provider Chat 认证已由部署配置关闭。",
                status_code=503,
            )
        if not acknowledge_billed_call:
            raise RouterServiceError(
                "billed_call_acknowledgement_required",
                "运行认证前必须确认本次调用可能产生少量费用。",
                status_code=422,
            )
        clean_idempotency_key = str(idempotency_key or "").strip()
        if not clean_idempotency_key or len(clean_idempotency_key) > 200:
            raise RouterServiceError(
                "invalid_idempotency_key",
                "Idempotency-Key 必须是 1 至 200 个字符。",
                status_code=422,
            )
        connection = self.repository.get_connection(
            self.router_service.tenant_id, connection_id
        )
        self._validate_connection(connection)

        refreshed = await self.refresh_models(connection_id)
        if not refreshed.ok:
            raise RouterServiceError(
                "provider_model_catalog_unavailable",
                "模型目录刷新失败，未发送认证调用。",
                status_code=409,
            )
        if model_id not in refreshed.model_ids:
            raise RouterServiceError(
                "provider_certification_model_not_found",
                "所选模型不在最新目录中，未发送认证调用。",
                status_code=409,
            )

        fingerprint = self._repository_method("connection_config_fingerprint")(
            self.router_service.tenant_id, connection_id
        )
        idempotency_hash = hashlib.sha256(
            clean_idempotency_key.encode("utf-8")
        ).hexdigest()
        try:
            row, created = self._repository_method("claim_chat_certification")(
                self.router_service.tenant_id,
                certification_id=f"chatcert_{uuid.uuid4().hex}",
                connection_id=connection_id,
                connection_fingerprint=fingerprint,
                contract_version=PROVIDER_CHAT_CONTRACT_VERSION,
                requested_model=model_id,
                capability=capability,
                idempotency_key_hash=idempotency_hash,
            )
        except RouterRepositoryError as exc:
            if str(exc) == "provider_chat_certification_already_running":
                raise RouterServiceError(
                    "provider_chat_certification_already_running",
                    "该连接已有一项 Chat 认证正在运行。",
                    status_code=409,
                ) from exc
            if str(exc) == "provider_chat_certification_idempotency_conflict":
                raise RouterServiceError(
                    "provider_chat_certification_idempotency_conflict",
                    "该 Idempotency-Key 已用于另一种 Chat 能力认证。",
                    status_code=409,
                ) from exc
            raise
        if not created:
            return self._summary(connection, capability, row)

        certification_id = str(row["id"])
        evidence = _StreamEvidence(capability=capability)
        started = time.perf_counter()
        status = "failed"
        error_code: str | None = None
        try:
            api_key = self.repository.resolve_api_key(
                self.router_service.tenant_id, connection_id
            )
            target = ProviderChatTarget.create(
                source="managed",
                provider_kind=connection.kind,
                base_url=connection.base_url,
                api_key=api_key,
                connection_id=connection.id,
            )
            payload = self._certification_payload(model_id, capability)
            async with asyncio.timeout(60):
                async with self._client_factory() as client:
                    async with self.transport.stream(
                        client,
                        target,
                        payload,
                        certification=True,
                    ) as response:
                        self._validate_status(response.status_code)
                        evidence.checks["chat_http_ok"] = True
                        await self._consume_sse(
                            response,
                            evidence,
                            started,
                            requested_model=model_id,
                        )
            status = "passed"
        except _CertificationFailure as exc:
            error_code = exc.code
        except httpx.ConnectTimeout:
            error_code = "provider_chat_connect_timeout"
        except httpx.ReadTimeout:
            error_code = "provider_chat_read_timeout"
        except httpx.TimeoutException:
            error_code = "provider_chat_timeout"
        except TimeoutError:
            error_code = "provider_chat_total_timeout"
        except httpx.ConnectError:
            error_code = "provider_chat_connect_error"
        except ProviderEgressError as exc:
            error_code = exc.code
        except RouterCredentialUnavailable:
            error_code = "provider_chat_credential_unavailable"
        except httpx.HTTPError:
            error_code = "provider_chat_transport_error"
        except asyncio.CancelledError:
            status = "uncertain"
            error_code = "provider_chat_cancelled"
            raise
        except Exception:
            error_code = "provider_chat_unexpected_error"
        finally:
            e2e_ms = (time.perf_counter() - started) * 1000
            completed = self._repository_method("complete_chat_certification")(
                self.router_service.tenant_id,
                certification_id,
                status=status,
                checks=evidence.checks,
                warning_codes=evidence.warning_codes,
                error_code=error_code,
                actual_model=evidence.actual_model,
                ttft_ms=evidence.ttft_ms,
                e2e_ms=e2e_ms,
                prompt_tokens=evidence.prompt_tokens,
                completion_tokens=evidence.completion_tokens,
                total_tokens=evidence.total_tokens,
            )
        return self._summary(connection, capability, completed)

    def _summary(
        self,
        connection: RouterConnection,
        capability: ProviderChatCapability,
        row: dict[str, object] | None,
    ) -> ProviderChatCertificationSummary:
        blocked_reason = self._blocked_reason(connection)
        if row is None:
            return ProviderChatCertificationSummary(
                connection_id=connection.id,
                connection_name=connection.name,
                capability=capability,
                can_run=blocked_reason is None,
                blocked_reason=blocked_reason,
            )
        status = str(row["status"])
        fingerprint = self._repository_method("connection_config_fingerprint")(
            self.router_service.tenant_id, connection.id
        )
        if row["connection_fingerprint"] != fingerprint and status != "running":
            status = "stale"
        if status == "running":
            blocked_reason = "provider_chat_certification_already_running"
        checks = json.loads(str(row["checks_json"] or "{}"))
        warnings = json.loads(str(row["warnings_json"] or "[]"))
        return ProviderChatCertificationSummary(
            certification_id=str(row["id"]),
            connection_id=connection.id,
            connection_name=connection.name,
            capability=capability,
            status=status,  # type: ignore[arg-type]
            can_run=blocked_reason is None,
            blocked_reason=blocked_reason,
            checks=ProviderChatCertificationChecks(**checks),
            warning_codes=[str(item) for item in warnings],
            error_code=str(row["error_code"]) if row["error_code"] else None,
            requested_model=str(row["requested_model"]),
            actual_model=str(row["actual_model"]) if row["actual_model"] else None,
            ttft_ms=self._float(row["ttft_ms"]),
            e2e_ms=self._float(row["e2e_ms"]),
            prompt_tokens=self._integer(row["prompt_tokens"]),
            completion_tokens=self._integer(row["completion_tokens"]),
            total_tokens=self._integer(row["total_tokens"]),
            created_at=str(row["created_at"]),
            completed_at=str(row["completed_at"]) if row["completed_at"] else None,
        )

    def _blocked_reason(self, connection: RouterConnection) -> str | None:
        if not self.enabled():
            return "provider_chat_certification_disabled"
        if not connection.enabled:
            return "connection_disabled"
        if "chat" not in connection.scopes:
            return "connection_chat_scope_required"
        if connection.health != "online" or not connection.last_checked_at:
            return "provider_model_catalog_not_checked"
        return None

    def _validate_connection(self, connection: RouterConnection) -> None:
        if not connection.enabled:
            raise RouterServiceError(
                "connection_disabled", "该模型服务已停用。", status_code=409
            )
        if "chat" not in connection.scopes:
            raise RouterServiceError(
                "connection_chat_scope_required",
                "该连接未启用 Chat scope。",
                status_code=409,
            )

    @staticmethod
    def _certification_payload(
        model_id: str,
        capability: ProviderChatCapability,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": model_id,
            "stream": True,
            "temperature": 0,
            "max_tokens": CHAT_TEXT_CERTIFICATION_MAX_TOKENS
            if capability == "chat_text"
            else 64,
        }
        if capability == "chat_text":
            payload["messages"] = [
                {"role": "user", "content": SYNTHETIC_CERTIFICATION_PROMPT}
            ]
            return payload
        if capability == "chat_tools":
            payload.update(
                {
                    "messages": [
                        {"role": "user", "content": SYNTHETIC_TOOL_PROMPT}
                    ],
                    "tools": [SYNTHETIC_TOOL],
                    "tool_choice": {
                        "type": "function",
                        "function": {"name": SYNTHETIC_TOOL_NAME},
                    },
                    "parallel_tool_calls": False,
                }
            )
            return payload
        payload.update(
            {
                "messages": [
                    {"role": "user", "content": SYNTHETIC_FILE_PROMPT}
                ],
                "tools": [SYNTHETIC_FILE_TOOL],
                "tool_choice": {
                    "type": "function",
                    "function": {"name": SYNTHETIC_FILE_TOOL_NAME},
                },
                "parallel_tool_calls": False,
            }
        )
        return payload

    @staticmethod
    def _validate_status(status_code: int) -> None:
        if 200 <= status_code < 300:
            return
        codes = {
            401: "provider_chat_http_401",
            403: "provider_chat_http_403",
            404: "provider_chat_http_404",
            429: "provider_chat_http_429",
        }
        raise _CertificationFailure(
            codes.get(
                status_code,
                "provider_chat_http_5xx"
                if status_code >= 500
                else "provider_chat_http_error",
            )
        )

    async def _consume_sse(
        self,
        response: httpx.Response,
        evidence: _StreamEvidence,
        started: float,
        *,
        requested_model: str,
    ) -> None:
        buffer = ""
        try:
            async for chunk in response.aiter_text():
                buffer += chunk.replace("\r\n", "\n").replace("\r", "\n")
                while "\n\n" in buffer:
                    event, buffer = buffer.split("\n\n", 1)
                    self._consume_event(event, evidence, started)
            if buffer.strip():
                self._consume_event(buffer, evidence, started)
        except _CertificationFailure:
            raise
        except Exception as exc:
            raise _CertificationFailure("provider_chat_stream_interrupted") from exc
        evidence.checks["stream_completed"] = True
        self._verify_capability(evidence)
        if not evidence.checks["terminal_observed"]:
            raise _CertificationFailure("provider_chat_missing_terminal")
        if evidence.actual_model is not None and evidence.actual_model != requested_model:
            raise _CertificationFailure("provider_chat_model_mismatch")
        if evidence.actual_model is None:
            evidence.warning_codes.append("actual_model_missing")
        if evidence.total_tokens is None:
            evidence.warning_codes.append("usage_missing")
        if evidence.finish_reason == "length":
            evidence.warning_codes.append("finish_reason_length")

    @staticmethod
    def _verify_capability(evidence: _StreamEvidence) -> None:
        if evidence.capability == "chat_text":
            if not evidence.checks["text_delta_observed"]:
                if evidence.finish_reason == "length":
                    raise _CertificationFailure(
                        "provider_chat_visible_text_budget_exhausted"
                    )
                raise _CertificationFailure("provider_chat_empty_stream")
            evidence.checks["capability_verified"] = True
            return
        if len(evidence.tool_calls) != 1:
            raise _CertificationFailure("provider_chat_tool_call_missing")
        tool_call = next(iter(evidence.tool_calls.values()))
        try:
            arguments = json.loads(tool_call.get("arguments") or "")
        except (json.JSONDecodeError, TypeError) as exc:
            raise _CertificationFailure(
                "provider_chat_tool_arguments_invalid"
            ) from exc
        if not isinstance(arguments, dict):
            raise _CertificationFailure("provider_chat_tool_arguments_invalid")
        if evidence.capability == "chat_tools":
            if tool_call.get("name") != SYNTHETIC_TOOL_NAME or arguments != {
                "value": "OK"
            }:
                raise _CertificationFailure("provider_chat_tool_contract_mismatch")
            evidence.checks["tool_call_observed"] = True
            evidence.checks["capability_verified"] = True
            return
        if tool_call.get("name") != SYNTHETIC_FILE_TOOL_NAME or arguments != {
            "format_id": "plain_text",
            "filename": "certification.txt",
            "content": "OK",
        }:
            raise _CertificationFailure(
                "provider_chat_file_output_contract_mismatch"
            )
        evidence.checks["tool_call_observed"] = True
        evidence.checks["file_output_contract_observed"] = True
        evidence.checks["capability_verified"] = True

    @staticmethod
    def _consume_event(event: str, evidence: _StreamEvidence, started: float) -> None:
        data_lines = []
        for line in event.split("\n"):
            if not line or line.startswith(":"):
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if not data_lines:
            return
        data = "\n".join(data_lines)
        if data == "[DONE]":
            evidence.checks["terminal_observed"] = True
            return
        try:
            payload = json.loads(data)
        except (json.JSONDecodeError, TypeError) as exc:
            raise _CertificationFailure("provider_chat_invalid_sse") from exc
        if not isinstance(payload, dict):
            raise _CertificationFailure("provider_chat_invalid_sse")
        model = payload.get("model")
        if isinstance(model, str) and model:
            evidence.actual_model = model
        usage = payload.get("usage")
        if isinstance(usage, dict):
            evidence.prompt_tokens = ProviderChatCertificationService._integer(
                usage.get("prompt_tokens")
            )
            evidence.completion_tokens = ProviderChatCertificationService._integer(
                usage.get("completion_tokens")
            )
            evidence.total_tokens = ProviderChatCertificationService._integer(
                usage.get("total_tokens")
            )
        choices = payload.get("choices")
        if not isinstance(choices, list):
            return
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            finish_reason = choice.get("finish_reason")
            if isinstance(finish_reason, str) and finish_reason:
                evidence.finish_reason = finish_reason
                evidence.checks["terminal_observed"] = True
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                continue
            content = delta.get("content")
            if isinstance(content, str) and content:
                if evidence.ttft_ms is None:
                    evidence.ttft_ms = (time.perf_counter() - started) * 1000
                evidence.checks["text_delta_observed"] = True
            raw_tool_calls = delta.get("tool_calls")
            if not isinstance(raw_tool_calls, list):
                continue
            for raw_call in raw_tool_calls:
                if not isinstance(raw_call, dict):
                    continue
                index = ProviderChatCertificationService._integer(
                    raw_call.get("index")
                )
                if index is None or index < 0 or index > 8:
                    raise _CertificationFailure("provider_chat_invalid_sse")
                accumulated = evidence.tool_calls.setdefault(
                    index,
                    {"id": "", "name": "", "arguments": ""},
                )
                call_id = raw_call.get("id")
                if isinstance(call_id, str):
                    accumulated["id"] += call_id
                function = raw_call.get("function")
                if not isinstance(function, dict):
                    continue
                name = function.get("name")
                if isinstance(name, str):
                    accumulated["name"] += name
                arguments = function.get("arguments")
                if isinstance(arguments, str):
                    accumulated["arguments"] += arguments

    def _repository_method(self, name: str):
        method = getattr(self.repository, name, None)
        if not callable(method):
            raise RouterServiceError(
                "provider_chat_certification_storage_unavailable",
                "当前 Router 存储不支持 Chat 认证。",
                status_code=503,
            )
        return method

    @staticmethod
    def _integer(value: object) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _float(value: object) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
