from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any, Literal

import httpx

from .service import ModelRouterService, RouterServiceError
from .workflow_gateway import (
    ManagedWorkflowGateway,
    ManagedWorkflowNodeRun,
    ManagedWorkflowRoutingError,
)
from .workload_control import (
    PROVIDER_WORKLOAD_CONTRACT_VERSION,
    ProviderWorkloadCallService,
)


RAG_QUERY_ENTRY_ID = "rag_query_generate"
RAG_PROCESSOR_ENTRY_ID = "rag_processor_generate"
RagGenerationEntryId = Literal[
    "rag_query_generate",
    "rag_processor_generate",
]
RagGenerationRoutingMode = Literal[
    "legacy",
    "managed_required",
    "degraded_required",
]


class ManagedRagGenerationError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 409,
        receipt: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.receipt = receipt


class ManagedRagGenerationGateway:
    """RAG adapter over the already qualified one-dispatch unary transport."""

    def __init__(
        self,
        call_service: ProviderWorkloadCallService,
        *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self.call_service = call_service
        self._workflow_gateway = ManagedWorkflowGateway(
            call_service,
            client_factory=client_factory,
        )

    @classmethod
    def for_router(
        cls,
        router_service: ModelRouterService,
        *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> "ManagedRagGenerationGateway":
        return cls(
            ProviderWorkloadCallService(router_service),
            client_factory=client_factory,
        )

    def routing_mode(self, entry_id: RagGenerationEntryId) -> RagGenerationRoutingMode:
        control = self.call_service.control
        if not control.feature_enabled(entry_id):
            return "legacy"
        policy = control.get_policy(entry_id)
        if policy.configured_status == "legacy":
            return "legacy"
        if policy.effective_status == "managed_required":
            return "managed_required"
        return "degraded_required"

    def exact_model_id(
        self,
        entry_id: RagGenerationEntryId,
        execution_shape: Literal["chat_text_unary", "chat_json_object"],
        *,
        requested_model: str | None = None,
    ) -> str:
        policy = self.call_service.control.get_policy(entry_id)
        if policy.effective_status != "managed_required":
            raise ManagedRagGenerationError(
                "provider_workload_policy_not_active",
                "RAG Managed Provider 策略未就绪。",
                receipt=self.blocked_receipt(
                    entry_id, "provider_workload_policy_not_active"
                ),
            )
        clean_requested = str(requested_model or "").strip()
        models = sorted(
            {
                item.model_id
                for item in policy.bindings
                if item.execution_shape == execution_shape
                and item.valid
                and (not clean_requested or item.model_id == clean_requested)
            }
        )
        if not models:
            code = "provider_workload_binding_missing"
            raise ManagedRagGenerationError(
                code,
                "RAG 入口缺少该精确模型与执行形态的合格 Binding。",
                receipt=self.blocked_receipt(entry_id, code),
            )
        if len(models) != 1:
            code = "provider_workload_binding_ambiguous"
            raise ManagedRagGenerationError(
                code,
                "RAG 入口存在多个可选模型，无法确定唯一 Managed Binding。",
                receipt=self.blocked_receipt(entry_id, code),
            )
        return models[0]

    def local_fallback_mode(self, entry_id: RagGenerationEntryId) -> str:
        return str(
            self.call_service.control.get_policy(entry_id).local_fallback_mode
            or "none"
        )

    def start_run(
        self,
        entry_id: RagGenerationEntryId,
        *,
        parent_run_reference: str,
        stable: bool,
    ) -> "ManagedRagGenerationRun":
        if self.routing_mode(entry_id) != "managed_required":
            code = "provider_workload_policy_not_active"
            raise ManagedRagGenerationError(
                code,
                "RAG Managed Provider 策略未就绪，调用已在派发前阻断。",
                receipt=self.blocked_receipt(entry_id, code),
            )
        clean_parent = parent_run_reference.strip()
        if not clean_parent:
            clean_parent = f"rag:{entry_id}:{uuid.uuid4().hex}"
        try:
            run_id = (
                self.call_service.start_stable_run(
                    entry_id,
                    parent_run_reference=clean_parent,
                )
                if stable
                else self.call_service.start_run(
                    entry_id,
                    parent_run_reference=clean_parent,
                )
            )
        except RouterServiceError as exc:
            raise ManagedRagGenerationError(
                exc.code,
                "RAG Managed Provider 运行已存在或资格失效，系统不会重放。",
                status_code=exc.status_code,
                receipt=self.blocked_receipt(entry_id, exc.code),
            ) from exc
        delegate = ManagedWorkflowNodeRun(
            self._workflow_gateway,
            entry_id,  # type: ignore[arg-type]
            run_id,
        )
        return ManagedRagGenerationRun(entry_id, delegate)

    @staticmethod
    def blocked_receipt(
        entry_id: RagGenerationEntryId,
        reason_code: str,
    ) -> dict[str, Any]:
        return {
            "contract_version": PROVIDER_WORKLOAD_CONTRACT_VERSION,
            "entry_id": entry_id,
            "routing_mode": "managed_required",
            "run_reference": "blocked_before_dispatch",
            "status": "failed",
            "call_count": 0,
            "reason_codes": [reason_code],
            "calls": [],
        }


class ManagedRagGenerationRun:
    def __init__(
        self,
        entry_id: RagGenerationEntryId,
        delegate: ManagedWorkflowNodeRun,
    ) -> None:
        self.entry_id = entry_id
        self._delegate = delegate

    async def complete_text_unary(
        self,
        *,
        logical_call_key: str,
        call_sequence: int,
        model_id: str,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
    ) -> str:
        try:
            return await self._delegate.complete_text_unary(
                logical_call_key=logical_call_key,
                call_sequence=call_sequence,
                model_id=model_id,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except ManagedWorkflowRoutingError as exc:
            raise self._closed_error(exc) from exc

    async def complete_json_object(
        self,
        *,
        logical_call_key: str,
        call_sequence: int,
        model_id: str,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
    ) -> str:
        try:
            return await self._delegate.complete_json_object(
                logical_call_key=logical_call_key,
                call_sequence=call_sequence,
                model_id=model_id,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except ManagedWorkflowRoutingError as exc:
            raise self._closed_error(exc) from exc

    def finish_success(self) -> dict[str, Any]:
        self._delegate.finish("passed")
        return self._delegate.receipt_summary()

    def finish_failure(self, reason_code: str) -> dict[str, Any]:
        status = (
            "uncertain"
            if any(call.status == "uncertain" for call in self._delegate.calls)
            else "failed"
        )
        self._delegate.finish(status, reason_code=reason_code)
        return self._delegate.receipt_summary()

    def finish_cancelled(self) -> dict[str, Any]:
        self._delegate.finish(
            "cancelled",
            reason_code="provider_workload_call_cancelled",
        )
        return self._delegate.receipt_summary()

    def receipt_summary(self) -> dict[str, Any]:
        return self._delegate.receipt_summary()

    def _closed_error(
        self,
        exc: ManagedWorkflowRoutingError,
    ) -> ManagedRagGenerationError:
        return ManagedRagGenerationError(
            exc.code,
            "RAG Managed Provider 调用失败，系统未重试或切换目标。",
            status_code=exc.status_code,
            receipt=(
                exc.receipt
                if isinstance(exc.receipt, dict)
                else self._delegate.receipt_summary()
            ),
        )
