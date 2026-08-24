from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any, Literal

import httpx

try:
    from server.model_router.repository import RouterRepositoryError
    from server.model_router.service import ModelRouterService, RouterServiceError
    from server.model_router.workload_control import (
        PROVIDER_WORKLOAD_CONTRACT_VERSION,
        ProviderWorkloadCallService,
        ProviderWorkloadPreparedCall,
    )
except ImportError:  # pragma: no cover - direct server package execution
    from model_router.repository import RouterRepositoryError
    from model_router.service import ModelRouterService, RouterServiceError
    from model_router.workload_control import (
        PROVIDER_WORKLOAD_CONTRACT_VERSION,
        ProviderWorkloadCallService,
        ProviderWorkloadPreparedCall,
    )

from .schemas import ProviderRouteCallReceipt, ProviderRouteReceiptSummary


MetaAgentRoutingMode = Literal["legacy", "managed_required", "degraded_required"]
MAX_META_AGENT_RESPONSE_BYTES = 1024 * 1024


class ManagedMetaAgentRoutingError(RuntimeError):
    def __init__(self, code: str, public_message: str, *, status_code: int = 502) -> None:
        super().__init__(code)
        self.code = code
        self.public_message = public_message
        self.status_code = status_code


class ManagedMetaAgentGateway:
    """Host-only managed JSON completion adapter for both Meta Agent entries."""

    def __init__(
        self,
        call_service: ProviderWorkloadCallService,
        *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self.call_service = call_service
        self._client_factory = client_factory

    @classmethod
    def for_router(
        cls,
        router_service: ModelRouterService,
        *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> ManagedMetaAgentGateway:
        return cls(
            ProviderWorkloadCallService(router_service),
            client_factory=client_factory,
        )

    def routing_mode(self) -> MetaAgentRoutingMode:
        control = self.call_service.control
        if not control.feature_enabled("meta_agent"):
            return "legacy"
        policy = control.get_policy("meta_agent")
        if policy.configured_status == "legacy":
            return "legacy"
        if policy.effective_status == "managed_required":
            return "managed_required"
        return "degraded_required"

    def start_run(self, *, parent_run_reference: str) -> ManagedMetaAgentRun:
        return ManagedMetaAgentRun(
            self,
            self.call_service.start_run(
                "meta_agent", parent_run_reference=parent_run_reference
            ),
        )


class ManagedMetaAgentRun:
    def __init__(self, gateway: ManagedMetaAgentGateway, run_id: str) -> None:
        self.gateway = gateway
        self.run_id = run_id
        self.status: Literal[
            "running", "passed", "failed", "uncertain", "cancelled"
        ] = "running"
        self.reason_codes: list[str] = []
        self.calls: list[ProviderRouteCallReceipt] = []

    async def complete_json(
        self,
        *,
        logical_call_key: str,
        call_sequence: int,
        model_id: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        prepared: ProviderWorkloadPreparedCall | None = None
        dispatched = False
        started = time.perf_counter()
        try:
            prepared = await self.gateway.call_service.prepare_call(
                run_id=self.run_id,
                entry_id="meta_agent",
                execution_shape="chat_json_object",
                model_id=model_id,
                logical_call_key=logical_call_key,
                call_sequence=call_sequence,
            )
            request_payload = {
                "model": model_id,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "stream": False,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "response_format": {"type": "json_object"},
            }
            client = (
                self.gateway._client_factory()
                if self.gateway._client_factory is not None
                else httpx.AsyncClient(
                    **self.gateway.call_service.transport.client_kwargs(
                        certification=True
                    )
                )
            )
            async with asyncio.timeout(60):
                async with client:
                    request = self.gateway.call_service.transport.build_authorized_stream_request(
                        client,
                        prepared.target,
                        prepared.authorized_target,
                        request_payload,
                    )
                    self.gateway.call_service.mark_dispatched(prepared)
                    dispatched = True
                    response = await self.gateway.call_service.transport.send_authorized_stream(
                        client, request
                    )
                    try:
                        payload = await self._read_response(response)
                    finally:
                        await response.aclose()
            text, actual_model, usage = self._completion_payload(
                payload, requested_model=model_id
            )
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.gateway.call_service.complete_call(
                prepared,
                status="passed",
                result_class="success",
                actual_model=actual_model,
                ttft_ms=elapsed_ms,
                e2e_ms=elapsed_ms,
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
            )
            self.calls.append(
                ProviderRouteCallReceipt(
                    call_sequence=call_sequence,
                    model_id=model_id,
                    actual_model=actual_model,
                    dispatched=True,
                    status="passed",
                    prompt_tokens=usage.get("prompt_tokens"),
                    completion_tokens=usage.get("completion_tokens"),
                    total_tokens=usage.get("total_tokens"),
                )
            )
            return text
        except asyncio.CancelledError:
            self._complete_call_safely(
                prepared,
                status="cancelled",
                result_class="client_cancelled",
                error_code="provider_workload_call_cancelled",
            )
            self._append_failure(
                call_sequence,
                model_id,
                "cancelled",
                "provider_workload_call_cancelled",
                dispatched=dispatched,
            )
            raise
        except ManagedMetaAgentRoutingError as exc:
            self._complete_call_safely(
                prepared,
                status="failed",
                result_class="provider_error" if dispatched else "preflight_error",
                error_code=exc.code,
            )
            self._append_failure(
                call_sequence,
                model_id,
                "failed",
                exc.code,
                dispatched=dispatched,
            )
            raise
        except RouterServiceError as exc:
            status = "uncertain" if dispatched else "failed"
            self._complete_call_safely(
                prepared,
                status=status,
                result_class="control_plane_error",
                error_code=exc.code,
            )
            self._append_failure(
                call_sequence,
                model_id,
                status,
                exc.code,
                dispatched=dispatched,
            )
            raise ManagedMetaAgentRoutingError(
                exc.code,
                "Meta Agent 的 Managed Provider 路由已失败关闭。",
                status_code=exc.status_code,
            ) from exc
        except (httpx.TimeoutException, TimeoutError) as exc:
            code = "provider_workload_timeout"
            status = "uncertain" if dispatched else "failed"
            self._complete_call_safely(
                prepared,
                status=status,
                result_class="transport_error",
                error_code=code,
            )
            self._append_failure(
                call_sequence,
                model_id,
                status,
                code,
                dispatched=dispatched,
            )
            raise ManagedMetaAgentRoutingError(
                code, "Meta Agent 的 Managed Provider 请求超时，系统未重放。"
            ) from exc
        except httpx.HTTPError as exc:
            code = "provider_workload_transport_error"
            status = "uncertain" if dispatched else "failed"
            self._complete_call_safely(
                prepared,
                status=status,
                result_class="transport_error",
                error_code=code,
            )
            self._append_failure(
                call_sequence,
                model_id,
                status,
                code,
                dispatched=dispatched,
            )
            raise ManagedMetaAgentRoutingError(
                code, "Meta Agent 的 Managed Provider 请求失败，系统未重放。"
            ) from exc
        except Exception as exc:
            code = "provider_workload_dispatch_uncertain" if dispatched else (
                "provider_workload_preflight_failed"
            )
            status = "uncertain" if dispatched else "failed"
            self._complete_call_safely(
                prepared,
                status=status,
                result_class="unexpected_error",
                error_code=code,
            )
            self._append_failure(
                call_sequence,
                model_id,
                status,
                code,
                dispatched=dispatched,
            )
            raise ManagedMetaAgentRoutingError(
                code, "Meta Agent 的 Managed Provider 调用失败，系统未重放。"
            ) from exc

    def finish(
        self,
        status: Literal["passed", "failed", "uncertain", "cancelled"],
        *,
        reason_code: str | None = None,
    ) -> None:
        if self.status != "running":
            return
        reason_codes = [reason_code] if reason_code else []
        try:
            self.gateway.call_service.complete_run(
                self.run_id,
                status=status,
                result_class=f"meta_agent_{status}",
                reason_codes=reason_codes,
            )
        except RouterRepositoryError as exc:
            if str(exc) != "provider_workload_run_not_running":
                raise
        self.status = status
        self.reason_codes = reason_codes

    def receipt_summary(self) -> ProviderRouteReceiptSummary:
        return ProviderRouteReceiptSummary(
            contract_version=PROVIDER_WORKLOAD_CONTRACT_VERSION,
            entry_id="meta_agent",
            routing_mode="managed_required",
            run_reference=self.run_id,
            status=self.status,
            call_count=sum(1 for call in self.calls if call.dispatched),
            reason_codes=list(self.reason_codes),
            calls=list(self.calls),
        )

    @staticmethod
    async def _read_response(response: httpx.Response) -> dict[str, Any]:
        if response.status_code < 200 or response.status_code >= 300:
            code = {
                401: "provider_workload_http_401",
                402: "provider_workload_http_402",
                403: "provider_workload_http_403",
                404: "provider_workload_http_404",
                429: "provider_workload_http_429",
            }.get(
                response.status_code,
                "provider_workload_http_5xx"
                if response.status_code >= 500
                else "provider_workload_http_error",
            )
            raise ManagedMetaAgentRoutingError(
                code, "Managed Provider 拒绝或未能完成 Meta Agent 请求。"
            )
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
            total += len(chunk)
            if total > MAX_META_AGENT_RESPONSE_BYTES:
                raise ManagedMetaAgentRoutingError(
                    "provider_workload_response_too_large",
                    "Managed Provider 的 Meta Agent 响应超过安全上限。",
                )
            chunks.append(chunk)
        try:
            payload = json.loads(b"".join(chunks))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ManagedMetaAgentRoutingError(
                "provider_workload_invalid_json_response",
                "Managed Provider 返回了无效的响应格式。",
            ) from exc
        if not isinstance(payload, dict):
            raise ManagedMetaAgentRoutingError(
                "provider_workload_invalid_json_response",
                "Managed Provider 返回了无效的响应格式。",
            )
        return payload

    @staticmethod
    def _completion_payload(
        payload: dict[str, Any], *, requested_model: str
    ) -> tuple[str, str | None, dict[str, int]]:
        actual_model = payload.get("model")
        actual_model = actual_model.strip() if isinstance(actual_model, str) else None
        if actual_model and actual_model != requested_model:
            raise ManagedMetaAgentRoutingError(
                "provider_workload_actual_model_mismatch",
                "Managed Provider 返回的实际模型与精确 Binding 不一致。",
            )
        choices = payload.get("choices")
        first = choices[0] if isinstance(choices, list) and choices else None
        message = first.get("message") if isinstance(first, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise ManagedMetaAgentRoutingError(
                "provider_workload_empty_response",
                "Managed Provider 未返回 Meta Agent 所需的 JSON 内容。",
            )
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ManagedMetaAgentRoutingError(
                "provider_workload_json_object_invalid",
                "Managed Provider 未返回有效 JSON Object。",
            ) from exc
        if not isinstance(parsed, dict):
            raise ManagedMetaAgentRoutingError(
                "provider_workload_json_object_invalid",
                "Managed Provider 未返回有效 JSON Object。",
            )
        raw_usage = payload.get("usage")
        usage: dict[str, int] = {}
        if isinstance(raw_usage, dict):
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                value = raw_usage.get(key)
                if (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and value >= 0
                ):
                    usage[key] = int(value)
        return content, actual_model, usage

    def _append_failure(
        self,
        call_sequence: int,
        model_id: str,
        status: Literal["failed", "uncertain", "cancelled"],
        error_code: str,
        *,
        dispatched: bool,
    ) -> None:
        self.calls.append(
            ProviderRouteCallReceipt(
                call_sequence=call_sequence,
                model_id=model_id,
                dispatched=dispatched,
                status=status,
                error_code=error_code,
            )
        )

    def _complete_call_safely(
        self,
        prepared: ProviderWorkloadPreparedCall | None,
        *,
        status: str,
        result_class: str,
        error_code: str,
    ) -> None:
        if prepared is None:
            return
        try:
            self.gateway.call_service.complete_call(
                prepared,
                status=status,
                result_class=result_class,
                error_code=error_code,
            )
        except RouterRepositoryError:
            return
