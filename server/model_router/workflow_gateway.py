from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from .repository import RouterRepositoryError
from .service import ModelRouterService, RouterServiceError
from .workload_control import (
    PROVIDER_WORKLOAD_CONTRACT_VERSION,
    ProviderWorkloadCallService,
    ProviderWorkloadPreparedCall,
)
try:
    from server.xpert_runtime.agent_strategy.models import (
        AgentModelTurn,
        AgentToolCall,
        AgentUsage,
    )
except ImportError:  # pragma: no cover - direct server package execution
    from xpert_runtime.agent_strategy.models import (
        AgentModelTurn,
        AgentToolCall,
        AgentUsage,
    )


WorkflowRoutingMode = Literal["legacy", "managed_required", "degraded_required"]
WorkflowSourceKind = Literal[
    "workflow_classic",
    "workflow_deployment",
    "xpert_chat",
    "xpert_app",
]
WorkflowEntryId = Literal[
    "workflow_interactive_llm",
    "workflow_deployment_llm",
    "workflow_interactive_agent",
    "workflow_deployment_agent",
    "xpert",
    "xpert_app",
]
WorkflowCallStatus = Literal[
    "passed", "failed", "uncertain", "cancelled"
]

MAX_RESPONSE_BYTES = 1024 * 1024
MAX_STREAM_BYTES = 4 * 1024 * 1024
MAX_SSE_EVENT_BYTES = 256 * 1024


class ManagedWorkflowRoutingError(RuntimeError):
    def __init__(
        self,
        code: str,
        public_message: str,
        *,
        status_code: int = 502,
        receipt: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.public_message = public_message
        self.status_code = status_code
        self.receipt = receipt


@dataclass(slots=True)
class WorkflowProviderCallReceipt:
    call_sequence: int
    model_id: str
    actual_model: str | None = None
    dispatched: bool = False
    status: WorkflowCallStatus = "failed"
    error_code: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "call_sequence": self.call_sequence,
            "model_id": self.model_id,
            "actual_model": self.actual_model,
            "dispatched": self.dispatched,
            "status": self.status,
            "error_code": self.error_code,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass(slots=True)
class _StreamEvidence:
    content_observed: bool = False
    terminal_observed: bool = False
    actual_model: str | None = None
    ttft_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(slots=True)
class _AgentStreamEvidence(_StreamEvidence):
    content_parts: list[str] = field(default_factory=list)
    tool_calls: dict[int, dict[str, str]] = field(default_factory=dict)
    finish_reason: str | None = None


class ManagedWorkflowGateway:
    """Classic Workflow adapter for one exact managed Provider target."""

    _ENTRY_BY_SOURCE: dict[WorkflowSourceKind, WorkflowEntryId] = {
        "workflow_classic": "workflow_interactive_llm",
        "workflow_deployment": "workflow_deployment_llm",
        "xpert_chat": "xpert",
        "xpert_app": "xpert_app",
    }
    _AGENT_ENTRY_BY_SOURCE: dict[WorkflowSourceKind, WorkflowEntryId] = {
        "workflow_classic": "workflow_interactive_agent",
        "workflow_deployment": "workflow_deployment_agent",
        "xpert_chat": "xpert",
        "xpert_app": "xpert_app",
    }
    _SOURCE_PREFIX: dict[WorkflowSourceKind, str] = {
        "workflow_classic": "interactive",
        "workflow_deployment": "deployment",
        "xpert_chat": "xpert",
        "xpert_app": "xpert_app",
    }

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
    ) -> ManagedWorkflowGateway:
        return cls(
            ProviderWorkloadCallService(router_service),
            client_factory=client_factory,
        )

    def routing_mode(self, source_kind: str | None) -> WorkflowRoutingMode:
        entry_id = self.entry_id(source_kind)
        if entry_id is None:
            return "legacy"
        control = self.call_service.control
        if not control.feature_enabled(entry_id):
            return "legacy"
        policy = control.get_policy(entry_id)
        if policy.configured_status == "legacy":
            return "legacy"
        if policy.effective_status == "managed_required":
            return "managed_required"
        return "degraded_required"

    def agent_routing_mode(self, source_kind: str | None) -> WorkflowRoutingMode:
        entry_id = self.agent_entry_id(source_kind)
        if entry_id is None:
            return "legacy"
        control = self.call_service.control
        if not control.feature_enabled(entry_id):
            return "legacy"
        policy = control.get_policy(entry_id)
        if policy.configured_status == "legacy":
            return "legacy"
        if policy.effective_status == "managed_required":
            return "managed_required"
        return "degraded_required"

    @classmethod
    def entry_id(cls, source_kind: str | None) -> WorkflowEntryId | None:
        return cls._ENTRY_BY_SOURCE.get(str(source_kind or ""))  # type: ignore[arg-type]

    @classmethod
    def agent_entry_id(cls, source_kind: str | None) -> WorkflowEntryId | None:
        return cls._AGENT_ENTRY_BY_SOURCE.get(  # type: ignore[arg-type]
            str(source_kind or "")
        )

    def start_node_run(
        self,
        *,
        source_kind: WorkflowSourceKind,
        execution_reference: str,
        node_id: str,
    ) -> ManagedWorkflowNodeRun:
        entry_id = self._ENTRY_BY_SOURCE[source_kind]
        if self.routing_mode(source_kind) != "managed_required":
            raise ManagedWorkflowRoutingError(
                "provider_workload_policy_not_active",
                "Workflow 的 Managed Provider 策略未就绪，当前节点失败关闭。",
                status_code=409,
            )
        clean_execution = execution_reference.strip()
        clean_node = node_id.strip()
        if not clean_execution or not clean_node:
            raise ManagedWorkflowRoutingError(
                "provider_workload_workflow_reference_required",
                "Workflow 模型节点缺少稳定执行引用。",
                status_code=409,
            )
        parent_reference = (
            f"{self._SOURCE_PREFIX[source_kind]}:{clean_execution}:{clean_node}"
        )
        try:
            run_id = self.call_service.start_stable_run(
                entry_id,
                parent_run_reference=parent_reference,
            )
        except RouterServiceError as exc:
            raise ManagedWorkflowRoutingError(
                exc.code,
                "Workflow 模型节点已有执行证据或资格已失效，系统不会自动重放。",
                status_code=exc.status_code,
                receipt=self.blocked_receipt(entry_id, exc.code),
            ) from exc
        return ManagedWorkflowNodeRun(self, entry_id, run_id)

    def start_agent_run(
        self,
        *,
        source_kind: WorkflowSourceKind,
        execution_reference: str,
        node_id: str,
        logical_phase: str = "initial",
    ) -> ManagedWorkflowAgentRun:
        entry_id = self._AGENT_ENTRY_BY_SOURCE[source_kind]
        if self.agent_routing_mode(source_kind) != "managed_required":
            raise ManagedWorkflowRoutingError(
                "provider_workload_policy_not_active",
                "Workflow Agent 的 Managed Provider 策略未就绪，当前节点失败关闭。",
                status_code=409,
            )
        clean_execution = execution_reference.strip()
        clean_node = node_id.strip()
        clean_phase = logical_phase.strip()
        if not clean_execution or not clean_node or not clean_phase:
            raise ManagedWorkflowRoutingError(
                "provider_workload_workflow_reference_required",
                "Workflow Agent 缺少稳定执行引用。",
                status_code=409,
            )
        parent_reference = (
            f"{self._SOURCE_PREFIX[source_kind]}:{clean_execution}:{clean_node}:"
            f"agent:{clean_phase}"
        )
        try:
            run_id = self.call_service.start_stable_run(
                entry_id,
                parent_run_reference=parent_reference,
            )
        except RouterServiceError as exc:
            raise ManagedWorkflowRoutingError(
                exc.code,
                "Workflow Agent 已有执行证据或资格已失效，系统不会自动重放。",
                status_code=exc.status_code,
                receipt=self.blocked_receipt(entry_id, exc.code),
            ) from exc
        return ManagedWorkflowAgentRun(
            ManagedWorkflowNodeRun(self, entry_id, run_id)
        )

    @staticmethod
    def blocked_receipt(entry_id: WorkflowEntryId, reason_code: str) -> dict[str, Any]:
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


class ManagedWorkflowNodeRun:
    def __init__(
        self,
        gateway: ManagedWorkflowGateway,
        entry_id: WorkflowEntryId,
        run_id: str,
    ) -> None:
        self.gateway = gateway
        self.entry_id = entry_id
        self.run_id = run_id
        self.status: Literal[
            "running", "passed", "failed", "uncertain", "cancelled"
        ] = "running"
        self.reason_codes: list[str] = []
        self.calls: list[WorkflowProviderCallReceipt] = []

    async def stream_text(
        self,
        *,
        logical_call_key: str,
        call_sequence: int,
        model_id: str,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[str]:
        prepared = await self.prepare_stream_call(
            logical_call_key=logical_call_key,
            call_sequence=call_sequence,
            execution_shape="chat_text",
            model_id=model_id,
        )
        async for delta in self.stream_prepared_text(
            prepared,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            cancel_event=cancel_event,
        ):
            yield delta

    async def prepare_stream_call(
        self,
        *,
        logical_call_key: str,
        call_sequence: int,
        execution_shape: Literal["chat_text", "fusion_native"],
        model_id: str,
    ) -> ProviderWorkloadPreparedCall:
        try:
            return await self.gateway.call_service.prepare_call(
                run_id=self.run_id,
                entry_id=self.entry_id,
                execution_shape=execution_shape,
                model_id=model_id,
                logical_call_key=logical_call_key,
                call_sequence=call_sequence,
            )
        except asyncio.CancelledError:
            self._record_failure(
                None,
                call_sequence=call_sequence,
                model_id=model_id,
                dispatched=False,
                status="cancelled",
                result_class="client_cancelled",
                code="provider_workload_call_cancelled",
            )
            raise
        except ManagedWorkflowRoutingError as exc:
            self._record_failure(
                None,
                call_sequence=call_sequence,
                model_id=model_id,
                dispatched=False,
                status="failed",
                result_class="preflight_error",
                code=exc.code,
            )
            exc.receipt = self.receipt_summary()
            raise
        except RouterServiceError as exc:
            self._record_failure(
                None,
                call_sequence=call_sequence,
                model_id=model_id,
                dispatched=False,
                status="failed",
                result_class="control_plane_error",
                code=exc.code,
            )
            raise self._closed_error(exc.code, False) from exc
        except (httpx.TimeoutException, TimeoutError) as exc:
            self._record_failure(
                None,
                call_sequence=call_sequence,
                model_id=model_id,
                dispatched=False,
                status="failed",
                result_class="transport_error",
                code="provider_workload_timeout",
            )
            raise self._closed_error("provider_workload_timeout", False) from exc
        except httpx.HTTPError as exc:
            self._record_failure(
                None,
                call_sequence=call_sequence,
                model_id=model_id,
                dispatched=False,
                status="failed",
                result_class="transport_error",
                code="provider_workload_transport_error",
            )
            raise self._closed_error(
                "provider_workload_transport_error", False
            ) from exc
        except Exception as exc:
            self._record_failure(
                None,
                call_sequence=call_sequence,
                model_id=model_id,
                dispatched=False,
                status="failed",
                result_class="unexpected_error",
                code="provider_workload_preflight_failed",
            )
            raise self._closed_error(
                "provider_workload_preflight_failed", False
            ) from exc

    async def stream_prepared_text(
        self,
        prepared: ProviderWorkloadPreparedCall,
        *,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
        extra_payload: Mapping[str, Any] | None = None,
        expected_actual_model: str | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[str]:
        dispatched = False
        started = time.perf_counter()
        evidence = _StreamEvidence()
        try:
            payload: dict[str, Any] = {
                "model": prepared.model_id,
                "messages": messages,
                "stream": True,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            if extra_payload:
                conflicting = sorted(set(payload).intersection(extra_payload))
                if conflicting:
                    raise ManagedWorkflowRoutingError(
                        "provider_workload_payload_conflict",
                        "Managed Provider 的扩展请求字段与稳定调用契约冲突。",
                        status_code=409,
                    )
                payload.update(extra_payload)
            async with self._client() as client:
                request = self.gateway.call_service.transport.build_authorized_stream_request(
                    client,
                    prepared.target,
                    prepared.authorized_target,
                    payload,
                )
                self.gateway.call_service.mark_dispatched(prepared)
                dispatched = True
                response = await self.gateway.call_service.transport.send_authorized_stream(
                    client, request
                )
                try:
                    self._validate_status(response.status_code)
                    async for event in self._iter_sse_events(response, cancel_event):
                        for delta in self._consume_stream_event(
                            event,
                            evidence,
                            requested_model=(
                                expected_actual_model or prepared.model_id
                            ),
                            started=started,
                        ):
                            yield delta
                finally:
                    await response.aclose()
            if not evidence.content_observed:
                raise ManagedWorkflowRoutingError(
                    "provider_workload_empty_stream",
                    "Managed Provider 没有返回可用的 Workflow 文本流。",
                )
            if not evidence.terminal_observed:
                raise ManagedWorkflowRoutingError(
                    "provider_workload_missing_terminal",
                    "Managed Provider 的 Workflow 文本流缺少安全终止信号。",
                )
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.gateway.call_service.complete_call(
                prepared,
                status="passed",
                result_class="success",
                actual_model=evidence.actual_model,
                ttft_ms=evidence.ttft_ms,
                e2e_ms=elapsed_ms,
                prompt_tokens=evidence.prompt_tokens,
                completion_tokens=evidence.completion_tokens,
                total_tokens=evidence.total_tokens,
            )
            self.calls.append(
                WorkflowProviderCallReceipt(
                    call_sequence=prepared.call_sequence,
                    model_id=prepared.model_id,
                    actual_model=evidence.actual_model,
                    dispatched=True,
                    status="passed",
                    prompt_tokens=evidence.prompt_tokens,
                    completion_tokens=evidence.completion_tokens,
                    total_tokens=evidence.total_tokens,
                )
            )
        except asyncio.CancelledError:
            self._record_failure(
                prepared,
                call_sequence=prepared.call_sequence,
                model_id=prepared.model_id,
                dispatched=dispatched,
                status="cancelled",
                result_class="client_cancelled",
                code="provider_workload_call_cancelled",
            )
            raise
        except ManagedWorkflowRoutingError as exc:
            self._record_failure(
                prepared,
                call_sequence=prepared.call_sequence,
                model_id=prepared.model_id,
                dispatched=dispatched,
                status="failed",
                result_class="provider_error" if dispatched else "preflight_error",
                code=exc.code,
            )
            exc.receipt = self.receipt_summary()
            raise
        except RouterServiceError as exc:
            status: WorkflowCallStatus = "uncertain" if dispatched else "failed"
            self._record_failure(
                prepared,
                call_sequence=prepared.call_sequence,
                model_id=prepared.model_id,
                dispatched=dispatched,
                status=status,
                result_class="control_plane_error",
                code=exc.code,
            )
            raise self._closed_error(exc.code, dispatched) from exc
        except (httpx.TimeoutException, TimeoutError) as exc:
            status = "uncertain" if dispatched else "failed"
            self._record_failure(
                prepared,
                call_sequence=prepared.call_sequence,
                model_id=prepared.model_id,
                dispatched=dispatched,
                status=status,
                result_class="transport_error",
                code="provider_workload_timeout",
            )
            raise self._closed_error("provider_workload_timeout", dispatched) from exc
        except httpx.HTTPError as exc:
            status = "uncertain" if dispatched else "failed"
            self._record_failure(
                prepared,
                call_sequence=prepared.call_sequence,
                model_id=prepared.model_id,
                dispatched=dispatched,
                status=status,
                result_class="transport_error",
                code="provider_workload_transport_error",
            )
            raise self._closed_error(
                "provider_workload_transport_error", dispatched
            ) from exc
        except Exception as exc:
            status = "uncertain" if dispatched else "failed"
            code = (
                "provider_workload_dispatch_uncertain"
                if dispatched
                else "provider_workload_preflight_failed"
            )
            self._record_failure(
                prepared,
                call_sequence=prepared.call_sequence,
                model_id=prepared.model_id,
                dispatched=dispatched,
                status=status,
                result_class="unexpected_error",
                code=code,
            )
            raise self._closed_error(code, dispatched) from exc

    def fail_prepared_call(
        self,
        prepared: ProviderWorkloadPreparedCall,
        *,
        code: str,
        status: WorkflowCallStatus = "failed",
        result_class: str = "preflight_error",
    ) -> None:
        self._record_failure(
            prepared,
            call_sequence=prepared.call_sequence,
            model_id=prepared.model_id,
            dispatched=False,
            status=status,
            result_class=result_class,
            code=code,
        )

    async def complete_text_unary(
        self,
        *,
        logical_call_key: str,
        call_sequence: int,
        model_id: str,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
        cancel_event: asyncio.Event | None = None,
    ) -> str:
        return await self._complete_unary(
            logical_call_key=logical_call_key,
            call_sequence=call_sequence,
            execution_shape="chat_text_unary",
            model_id=model_id,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=None,
            require_json_object=False,
            cancel_event=cancel_event,
        )

    async def complete_agent_turn(
        self,
        *,
        logical_call_key: str,
        call_sequence: int,
        execution_shape: Literal["chat_text", "chat_tools"],
        model_id: str,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
        parallel_tool_calls: bool | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> AgentModelTurn:
        prepared: ProviderWorkloadPreparedCall | None = None
        dispatched = False
        started = time.perf_counter()
        evidence = _AgentStreamEvidence()
        try:
            if execution_shape == "chat_tools" and tools is None:
                raise ManagedWorkflowRoutingError(
                    "provider_workload_tools_required",
                    "Managed Workflow Agent 的 Tool Calling 轮次缺少工具定义。",
                    status_code=409,
                )
            if execution_shape == "chat_text" and tools is not None:
                raise ManagedWorkflowRoutingError(
                    "provider_workload_execution_shape_mismatch",
                    "Managed Workflow Agent 的文本轮次不得携带工具定义。",
                    status_code=409,
                )
            prepared = await self.gateway.call_service.prepare_call(
                run_id=self.run_id,
                entry_id=self.entry_id,
                execution_shape=execution_shape,
                model_id=model_id,
                logical_call_key=logical_call_key,
                call_sequence=call_sequence,
            )
            payload: dict[str, Any] = {
                "model": model_id,
                "messages": messages,
                "stream": True,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            if tools is not None:
                payload["tools"] = tools
                payload["tool_choice"] = tool_choice or "auto"
                if parallel_tool_calls is not None:
                    payload["parallel_tool_calls"] = bool(parallel_tool_calls)
            async with self._client() as client:
                request = self.gateway.call_service.transport.build_authorized_stream_request(
                    client,
                    prepared.target,
                    prepared.authorized_target,
                    payload,
                )
                self.gateway.call_service.mark_dispatched(prepared)
                dispatched = True
                response = await self.gateway.call_service.transport.send_authorized_stream(
                    client, request
                )
                try:
                    self._validate_status(response.status_code)
                    async for event in self._iter_sse_events(response, cancel_event):
                        self._consume_agent_stream_event(
                            event,
                            evidence,
                            requested_model=model_id,
                            started=started,
                        )
                finally:
                    await response.aclose()
            if not evidence.content_observed and not evidence.tool_calls:
                raise ManagedWorkflowRoutingError(
                    "provider_workload_empty_stream",
                    "Managed Provider 没有返回 Workflow Agent 可用的文本或 Tool Call。",
                )
            if not evidence.terminal_observed:
                raise ManagedWorkflowRoutingError(
                    "provider_workload_missing_terminal",
                    "Managed Provider 的 Workflow Agent 流缺少安全终止信号。",
                )
            if execution_shape == "chat_text" and evidence.tool_calls:
                raise ManagedWorkflowRoutingError(
                    "provider_workload_execution_shape_mismatch",
                    "Managed Provider 在纯文本 Workflow Agent 轮次返回了 Tool Call。",
                )
            tool_calls: list[AgentToolCall] = []
            for index in sorted(evidence.tool_calls):
                item = evidence.tool_calls[index]
                if not item["id"] or not item["name"]:
                    raise ManagedWorkflowRoutingError(
                        "provider_workload_incomplete_tool_call",
                        "Managed Provider 返回了不完整的 Workflow Agent Tool Call。",
                    )
                tool_calls.append(
                    AgentToolCall(
                        call_id=item["id"],
                        name=item["name"],
                        raw_arguments=item["arguments"] or "{}",
                    )
                )
            if evidence.finish_reason == "tool_calls" and not tool_calls:
                raise ManagedWorkflowRoutingError(
                    "provider_workload_incomplete_tool_call",
                    "Managed Provider 声明了 Tool Call，但未返回可执行参数。",
                )
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.gateway.call_service.complete_call(
                prepared,
                status="passed",
                result_class="success",
                actual_model=evidence.actual_model,
                ttft_ms=evidence.ttft_ms,
                e2e_ms=elapsed_ms,
                prompt_tokens=evidence.prompt_tokens,
                completion_tokens=evidence.completion_tokens,
                total_tokens=evidence.total_tokens,
            )
            self.calls.append(
                WorkflowProviderCallReceipt(
                    call_sequence=call_sequence,
                    model_id=model_id,
                    actual_model=evidence.actual_model,
                    dispatched=True,
                    status="passed",
                    prompt_tokens=evidence.prompt_tokens,
                    completion_tokens=evidence.completion_tokens,
                    total_tokens=evidence.total_tokens,
                )
            )
            return AgentModelTurn(
                content="".join(evidence.content_parts),
                tool_calls=tool_calls,
                finish_reason=evidence.finish_reason,
                usage=AgentUsage(
                    prompt_tokens=evidence.prompt_tokens or 0,
                    completion_tokens=evidence.completion_tokens or 0,
                    total_tokens=evidence.total_tokens or 0,
                ),
                raw={"model": evidence.actual_model or model_id},
            )
        except asyncio.CancelledError:
            self._record_failure(
                prepared,
                call_sequence=call_sequence,
                model_id=model_id,
                dispatched=dispatched,
                status="cancelled",
                result_class="client_cancelled",
                code="provider_workload_call_cancelled",
            )
            raise
        except ManagedWorkflowRoutingError as exc:
            self._record_failure(
                prepared,
                call_sequence=call_sequence,
                model_id=model_id,
                dispatched=dispatched,
                status="failed",
                result_class="provider_error" if dispatched else "preflight_error",
                code=exc.code,
            )
            exc.receipt = self.receipt_summary()
            raise
        except RouterServiceError as exc:
            status: WorkflowCallStatus = "uncertain" if dispatched else "failed"
            self._record_failure(
                prepared,
                call_sequence=call_sequence,
                model_id=model_id,
                dispatched=dispatched,
                status=status,
                result_class="control_plane_error",
                code=exc.code,
            )
            raise self._closed_error(exc.code, dispatched) from exc
        except (httpx.TimeoutException, TimeoutError) as exc:
            status = "uncertain" if dispatched else "failed"
            self._record_failure(
                prepared,
                call_sequence=call_sequence,
                model_id=model_id,
                dispatched=dispatched,
                status=status,
                result_class="transport_error",
                code="provider_workload_timeout",
            )
            raise self._closed_error("provider_workload_timeout", dispatched) from exc
        except httpx.HTTPError as exc:
            status = "uncertain" if dispatched else "failed"
            self._record_failure(
                prepared,
                call_sequence=call_sequence,
                model_id=model_id,
                dispatched=dispatched,
                status=status,
                result_class="transport_error",
                code="provider_workload_transport_error",
            )
            raise self._closed_error(
                "provider_workload_transport_error", dispatched
            ) from exc
        except Exception as exc:
            status = "uncertain" if dispatched else "failed"
            code = (
                "provider_workload_dispatch_uncertain"
                if dispatched
                else "provider_workload_preflight_failed"
            )
            self._record_failure(
                prepared,
                call_sequence=call_sequence,
                model_id=model_id,
                dispatched=dispatched,
                status=status,
                result_class="unexpected_error",
                code=code,
            )
            raise self._closed_error(code, dispatched) from exc

    async def complete_json_object(
        self,
        *,
        logical_call_key: str,
        call_sequence: int,
        model_id: str,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
        cancel_event: asyncio.Event | None = None,
    ) -> str:
        return await self._complete_unary(
            logical_call_key=logical_call_key,
            call_sequence=call_sequence,
            execution_shape="chat_json_object",
            model_id=model_id,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            require_json_object=True,
            cancel_event=cancel_event,
        )

    async def complete_json_object_for_shape(
        self,
        *,
        logical_call_key: str,
        call_sequence: int,
        execution_shape: str,
        model_id: str,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
        cancel_event: asyncio.Event | None = None,
    ) -> str:
        """Run a qualified JSON unary shape without weakening its exact Binding."""

        return await self._complete_unary(
            logical_call_key=logical_call_key,
            call_sequence=call_sequence,
            execution_shape=execution_shape,  # type: ignore[arg-type]
            model_id=model_id,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            require_json_object=True,
            cancel_event=cancel_event,
        )

    async def _complete_unary(
        self,
        *,
        logical_call_key: str,
        call_sequence: int,
        execution_shape: Literal["chat_text_unary", "chat_json_object"],
        model_id: str,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
        response_format: dict[str, str] | None,
        require_json_object: bool,
        cancel_event: asyncio.Event | None,
    ) -> str:
        prepared: ProviderWorkloadPreparedCall | None = None
        dispatched = False
        started = time.perf_counter()
        try:
            prepared = await self.gateway.call_service.prepare_call(
                run_id=self.run_id,
                entry_id=self.entry_id,
                execution_shape=execution_shape,
                model_id=model_id,
                logical_call_key=logical_call_key,
                call_sequence=call_sequence,
            )
            payload: dict[str, Any] = {
                "model": model_id,
                "messages": messages,
                "stream": False,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            if response_format is not None:
                payload["response_format"] = response_format
            async with self._client() as client:
                request = self.gateway.call_service.transport.build_authorized_stream_request(
                    client,
                    prepared.target,
                    prepared.authorized_target,
                    payload,
                )
                self.gateway.call_service.mark_dispatched(prepared)
                dispatched = True
                response = await self.gateway.call_service.transport.send_authorized_stream(
                    client, request
                )
                try:
                    self._validate_status(response.status_code)
                    raw = await self._read_with_cancel(response, cancel_event)
                finally:
                    await response.aclose()
            payload_body = self._decode_completion(raw)
            text, actual_model, usage = self._completion_text(
                payload_body,
                requested_model=model_id,
                require_json_object=require_json_object,
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
                WorkflowProviderCallReceipt(
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
            self._record_failure(
                prepared,
                call_sequence=call_sequence,
                model_id=model_id,
                dispatched=dispatched,
                status="cancelled",
                result_class="client_cancelled",
                code="provider_workload_call_cancelled",
            )
            raise
        except ManagedWorkflowRoutingError as exc:
            self._record_failure(
                prepared,
                call_sequence=call_sequence,
                model_id=model_id,
                dispatched=dispatched,
                status="failed",
                result_class="provider_error" if dispatched else "preflight_error",
                code=exc.code,
            )
            exc.receipt = self.receipt_summary()
            raise
        except RouterServiceError as exc:
            status: WorkflowCallStatus = "uncertain" if dispatched else "failed"
            self._record_failure(
                prepared,
                call_sequence=call_sequence,
                model_id=model_id,
                dispatched=dispatched,
                status=status,
                result_class="control_plane_error",
                code=exc.code,
            )
            raise self._closed_error(exc.code, dispatched) from exc
        except (httpx.TimeoutException, TimeoutError) as exc:
            status = "uncertain" if dispatched else "failed"
            self._record_failure(
                prepared,
                call_sequence=call_sequence,
                model_id=model_id,
                dispatched=dispatched,
                status=status,
                result_class="transport_error",
                code="provider_workload_timeout",
            )
            raise self._closed_error("provider_workload_timeout", dispatched) from exc
        except httpx.HTTPError as exc:
            status = "uncertain" if dispatched else "failed"
            self._record_failure(
                prepared,
                call_sequence=call_sequence,
                model_id=model_id,
                dispatched=dispatched,
                status=status,
                result_class="transport_error",
                code="provider_workload_transport_error",
            )
            raise self._closed_error(
                "provider_workload_transport_error", dispatched
            ) from exc
        except Exception as exc:
            status = "uncertain" if dispatched else "failed"
            code = (
                "provider_workload_dispatch_uncertain"
                if dispatched
                else "provider_workload_preflight_failed"
            )
            self._record_failure(
                prepared,
                call_sequence=call_sequence,
                model_id=model_id,
                dispatched=dispatched,
                status=status,
                result_class="unexpected_error",
                code=code,
            )
            raise self._closed_error(code, dispatched) from exc

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
                result_class=f"workflow_node_{status}",
                reason_codes=reason_codes,
            )
        except RouterRepositoryError as exc:
            if str(exc) != "provider_workload_run_not_running":
                raise
        self.status = status
        self.reason_codes = reason_codes

    def receipt_summary(self) -> dict[str, Any]:
        return {
            "contract_version": PROVIDER_WORKLOAD_CONTRACT_VERSION,
            "entry_id": self.entry_id,
            "routing_mode": "managed_required",
            "run_reference": self.run_id,
            "status": self.status,
            "call_count": sum(1 for call in self.calls if call.dispatched),
            "reason_codes": list(self.reason_codes),
            "calls": [call.as_dict() for call in self.calls],
        }

    def _client(self) -> httpx.AsyncClient:
        if self.gateway._client_factory is not None:
            return self.gateway._client_factory()
        return httpx.AsyncClient(
            **self.gateway.call_service.transport.client_kwargs()
        )

    async def _iter_sse_events(
        self,
        response: httpx.Response,
        cancel_event: asyncio.Event | None,
    ) -> AsyncIterator[str]:
        buffer = b""
        total = 0
        async for chunk in self._iter_response_bytes(response, cancel_event):
            total += len(chunk)
            if total > MAX_STREAM_BYTES:
                raise ManagedWorkflowRoutingError(
                    "provider_workload_stream_too_large",
                    "Managed Provider 的 Workflow 文本流超过安全上限。",
                )
            buffer += chunk
            while True:
                delimiters = [
                    (index, delimiter)
                    for delimiter in (b"\n\n", b"\r\n\r\n", b"\r\r")
                    if (index := buffer.find(delimiter)) >= 0
                ]
                if not delimiters:
                    break
                index, delimiter = min(delimiters, key=lambda item: item[0])
                raw_event = buffer[:index]
                buffer = buffer[index + len(delimiter) :]
                yield self._decode_sse_event(raw_event)
            if len(buffer) > MAX_SSE_EVENT_BYTES:
                raise ManagedWorkflowRoutingError(
                    "provider_workload_sse_event_too_large",
                    "Managed Provider 的 Workflow 流事件超过安全上限。",
                )
        if buffer.strip():
            yield self._decode_sse_event(buffer)

    async def _iter_response_bytes(
        self,
        response: httpx.Response,
        cancel_event: asyncio.Event | None,
    ) -> AsyncIterator[bytes]:
        # Preserve upstream SSE chunk delivery. A fixed 64 KiB chunk size makes
        # httpx buffer short streams until EOF, delaying both TTFT and cancel.
        iterator = response.aiter_bytes().__aiter__()
        while True:
            next_chunk = asyncio.create_task(anext(iterator))
            cancel_wait = (
                asyncio.create_task(cancel_event.wait())
                if cancel_event is not None
                else None
            )
            waiters = {next_chunk}
            if cancel_wait is not None:
                waiters.add(cancel_wait)
            done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            if cancel_wait is not None and cancel_wait in done and cancel_event.is_set():
                next_chunk.cancel()
                with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                    await next_chunk
                raise asyncio.CancelledError
            if cancel_wait is not None:
                cancel_wait.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await cancel_wait
            try:
                yield next_chunk.result()
            except StopAsyncIteration:
                return

    @staticmethod
    def _decode_sse_event(raw: bytes) -> str:
        if len(raw) > MAX_SSE_EVENT_BYTES:
            raise ManagedWorkflowRoutingError(
                "provider_workload_sse_event_too_large",
                "Managed Provider 的 Workflow 流事件超过安全上限。",
            )
        try:
            return raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError as exc:
            raise ManagedWorkflowRoutingError(
                "provider_workload_invalid_sse",
                "Managed Provider 返回了无效的 Workflow SSE。",
            ) from exc

    def _consume_stream_event(
        self,
        event: str,
        evidence: _StreamEvidence,
        *,
        requested_model: str,
        started: float,
    ) -> list[str]:
        data_lines: list[str] = []
        for line in event.split("\n"):
            if not line or line.startswith(":"):
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
                continue
            if line.startswith(("event:", "id:", "retry:")):
                continue
            raise ManagedWorkflowRoutingError(
                "provider_workload_invalid_sse",
                "Managed Provider 返回了无效的 Workflow SSE。",
            )
        if not data_lines:
            return []
        data = "\n".join(data_lines)
        if data == "[DONE]":
            evidence.terminal_observed = True
            return []
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ManagedWorkflowRoutingError(
                "provider_workload_invalid_sse",
                "Managed Provider 返回了无效的 Workflow SSE。",
            ) from exc
        if not isinstance(payload, dict) or isinstance(payload.get("error"), dict):
            raise ManagedWorkflowRoutingError(
                "provider_workload_invalid_sse",
                "Managed Provider 返回了无效的 Workflow SSE。",
            )
        self._read_usage(payload, evidence)
        actual_model = payload.get("model")
        if isinstance(actual_model, str) and actual_model.strip():
            clean_model = actual_model.strip()
            if clean_model != requested_model:
                raise ManagedWorkflowRoutingError(
                    "provider_workload_actual_model_mismatch",
                    "Managed Provider 返回的实际模型与 Workflow Binding 不一致。",
                )
            evidence.actual_model = clean_model
        choices = payload.get("choices")
        if not isinstance(choices, list):
            return []
        deltas: list[str] = []
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            if choice.get("finish_reason"):
                evidence.terminal_observed = True
            content_source = choice.get("delta")
            if not isinstance(content_source, dict):
                content_source = choice.get("message")
            content = (
                self._content_text(content_source.get("content"))
                if isinstance(content_source, dict)
                else ""
            )
            if content:
                if evidence.ttft_ms is None:
                    evidence.ttft_ms = (time.perf_counter() - started) * 1000
                evidence.content_observed = True
                deltas.append(content)
        return deltas

    def _consume_agent_stream_event(
        self,
        event: str,
        evidence: _AgentStreamEvidence,
        *,
        requested_model: str,
        started: float,
    ) -> None:
        data_lines: list[str] = []
        for line in event.split("\n"):
            if not line or line.startswith(":"):
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
                continue
            if line.startswith(("event:", "id:", "retry:")):
                continue
            raise ManagedWorkflowRoutingError(
                "provider_workload_invalid_sse",
                "Managed Provider 返回了无效的 Workflow Agent SSE。",
            )
        if not data_lines:
            return
        data = "\n".join(data_lines)
        if data == "[DONE]":
            evidence.terminal_observed = True
            return
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ManagedWorkflowRoutingError(
                "provider_workload_invalid_sse",
                "Managed Provider 返回了无效的 Workflow Agent SSE。",
            ) from exc
        if not isinstance(payload, dict) or isinstance(payload.get("error"), dict):
            raise ManagedWorkflowRoutingError(
                "provider_workload_invalid_sse",
                "Managed Provider 返回了无效的 Workflow Agent SSE。",
            )
        self._read_usage(payload, evidence)
        actual_model = payload.get("model")
        if isinstance(actual_model, str) and actual_model.strip():
            clean_model = actual_model.strip()
            if clean_model != requested_model:
                raise ManagedWorkflowRoutingError(
                    "provider_workload_actual_model_mismatch",
                    "Managed Provider 返回的实际模型与 Workflow Agent Binding 不一致。",
                )
            evidence.actual_model = clean_model
        choices = payload.get("choices")
        if not isinstance(choices, list):
            return
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            finish_reason = choice.get("finish_reason")
            if finish_reason:
                evidence.finish_reason = str(finish_reason)
                evidence.terminal_observed = True
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                delta = choice.get("message")
            if not isinstance(delta, dict):
                continue
            content = self._content_text(delta.get("content"))
            if content:
                if evidence.ttft_ms is None:
                    evidence.ttft_ms = (time.perf_counter() - started) * 1000
                evidence.content_observed = True
                evidence.content_parts.append(content)
            raw_calls = delta.get("tool_calls")
            if not isinstance(raw_calls, list):
                continue
            for fallback_index, raw_call in enumerate(raw_calls):
                if not isinstance(raw_call, dict):
                    continue
                raw_index = raw_call.get("index", fallback_index)
                try:
                    index = int(raw_index)
                except (TypeError, ValueError):
                    raise ManagedWorkflowRoutingError(
                        "provider_workload_invalid_tool_call",
                        "Managed Provider 返回了无效的 Workflow Agent Tool Call。",
                    )
                accumulated = evidence.tool_calls.setdefault(
                    index,
                    {"id": "", "name": "", "arguments": ""},
                )
                if raw_call.get("id"):
                    accumulated["id"] = str(raw_call["id"])
                function = raw_call.get("function")
                if isinstance(function, dict):
                    if function.get("name"):
                        accumulated["name"] += str(function["name"])
                    if function.get("arguments"):
                        accumulated["arguments"] += str(function["arguments"])
                if evidence.ttft_ms is None:
                    evidence.ttft_ms = (time.perf_counter() - started) * 1000

    async def _read_with_cancel(
        self,
        response: httpx.Response,
        cancel_event: asyncio.Event | None,
    ) -> bytes:
        read_task = asyncio.create_task(response.aread())
        cancel_wait = (
            asyncio.create_task(cancel_event.wait()) if cancel_event is not None else None
        )
        waiters = {read_task}
        if cancel_wait is not None:
            waiters.add(cancel_wait)
        done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        if cancel_wait is not None and cancel_wait in done and cancel_event.is_set():
            read_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await read_task
            raise asyncio.CancelledError
        if cancel_wait is not None:
            cancel_wait.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await cancel_wait
        raw = read_task.result()
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ManagedWorkflowRoutingError(
                "provider_workload_response_too_large",
                "Managed Provider 的 Workflow 响应超过安全上限。",
            )
        return raw

    @staticmethod
    def _decode_completion(raw: bytes) -> dict[str, Any]:
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ManagedWorkflowRoutingError(
                "provider_workload_invalid_json_response",
                "Managed Provider 返回了无效的 Workflow 响应。",
            ) from exc
        if not isinstance(payload, dict):
            raise ManagedWorkflowRoutingError(
                "provider_workload_invalid_json_response",
                "Managed Provider 返回了无效的 Workflow 响应。",
            )
        return payload

    @classmethod
    def _completion_text(
        cls,
        payload: Mapping[str, Any],
        *,
        requested_model: str,
        require_json_object: bool,
    ) -> tuple[str, str | None, dict[str, int]]:
        actual_model = payload.get("model")
        actual_model = actual_model.strip() if isinstance(actual_model, str) else None
        if actual_model and actual_model != requested_model:
            raise ManagedWorkflowRoutingError(
                "provider_workload_actual_model_mismatch",
                "Managed Provider 返回的实际模型与 Workflow Binding 不一致。",
            )
        choices = payload.get("choices")
        first = choices[0] if isinstance(choices, list) and choices else None
        message = first.get("message") if isinstance(first, dict) else None
        text = (
            cls._content_text(message.get("content"))
            if isinstance(message, dict)
            else ""
        )
        if not text.strip():
            raise ManagedWorkflowRoutingError(
                "provider_workload_empty_response",
                "Managed Provider 没有返回 Workflow 所需内容。",
            )
        if require_json_object:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ManagedWorkflowRoutingError(
                    "provider_workload_json_object_invalid",
                    "Managed Provider 没有返回有效的 JSON Object。",
                ) from exc
            if not isinstance(parsed, dict):
                raise ManagedWorkflowRoutingError(
                    "provider_workload_json_object_invalid",
                    "Managed Provider 没有返回有效的 JSON Object。",
                )
        usage: dict[str, int] = {}
        raw_usage = payload.get("usage")
        if isinstance(raw_usage, dict):
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                value = raw_usage.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    usage[key] = value
        return text, actual_model, usage

    @staticmethod
    def _content_text(value: Any) -> str:
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

    @staticmethod
    def _read_usage(payload: Mapping[str, Any], evidence: _StreamEvidence) -> None:
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            return
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                setattr(evidence, key, value)

    @staticmethod
    def _validate_status(status_code: int) -> None:
        if 200 <= status_code < 300:
            return
        code = {
            401: "provider_workload_http_401",
            402: "provider_workload_http_402",
            403: "provider_workload_http_403",
            404: "provider_workload_http_404",
            429: "provider_workload_http_429",
        }.get(
            status_code,
            "provider_workload_http_5xx"
            if status_code >= 500
            else "provider_workload_http_error",
        )
        raise ManagedWorkflowRoutingError(
            code,
            "Managed Provider 拒绝或未能完成 Workflow 请求。",
        )

    def _record_failure(
        self,
        prepared: ProviderWorkloadPreparedCall | None,
        *,
        call_sequence: int,
        model_id: str,
        dispatched: bool,
        status: WorkflowCallStatus,
        result_class: str,
        code: str,
    ) -> None:
        if prepared is not None:
            try:
                self.gateway.call_service.complete_call(
                    prepared,
                    status=status,
                    result_class=result_class,
                    error_code=code,
                )
            except RouterRepositoryError:
                pass
        self.calls.append(
            WorkflowProviderCallReceipt(
                call_sequence=call_sequence,
                model_id=model_id,
                dispatched=dispatched,
                status=status,
                error_code=code,
            )
        )

    def _closed_error(self, code: str, dispatched: bool) -> ManagedWorkflowRoutingError:
        return ManagedWorkflowRoutingError(
            code,
            (
                "Workflow 的 Managed Provider 调用结果不确定，系统未重放。"
                if dispatched
                else "Workflow 的 Managed Provider 路由在发送前失败关闭。"
            ),
            receipt=self.receipt_summary(),
        )


class ManagedWorkflowAgentRun:
    """One stable Agent node run with monotonically ordered Provider calls."""

    def __init__(self, node_run: ManagedWorkflowNodeRun) -> None:
        self._node_run = node_run
        self._call_sequence = 0

    @property
    def entry_id(self) -> WorkflowEntryId:
        return self._node_run.entry_id

    @property
    def run_id(self) -> str:
        return self._node_run.run_id

    @property
    def status(self) -> str:
        return self._node_run.status

    @property
    def calls(self) -> list[WorkflowProviderCallReceipt]:
        return self._node_run.calls

    def supports(self, model_id: str, execution_shape: str) -> bool:
        try:
            policy = self._node_run.gateway.call_service.control.get_policy(
                self.entry_id
            )
        except Exception:
            return False
        return bool(
            policy.effective_status == "managed_required"
            and any(
                item.valid
                and item.model_id == model_id
                and item.execution_shape == execution_shape
                for item in policy.bindings
            )
        )

    def resolve_strategy(
        self,
        *,
        requested_strategy: str,
        model_id: str,
        has_tools: bool,
    ) -> Literal["function_calling", "react"]:
        clean = requested_strategy.strip() or "auto"
        if clean not in {"auto", "function_calling", "react"}:
            raise ManagedWorkflowRoutingError(
                "provider_workload_agent_strategy_invalid",
                "Workflow Agent 策略无效。",
                status_code=422,
                receipt=self.receipt_summary(),
            )
        if clean == "function_calling":
            if not has_tools:
                raise ManagedWorkflowRoutingError(
                    "provider_workload_agent_tools_required",
                    "显式 Function Calling 策略缺少可用 Runtime 工具。",
                    status_code=409,
                    receipt=self.receipt_summary(),
                )
            self.require_shape(model_id, "chat_tools")
            return "function_calling"
        if not has_tools:
            self.require_shape(model_id, "chat_text")
            return "react"
        if clean == "react":
            self.require_shape(model_id, "chat_text")
            return "react"
        if self.supports(model_id, "chat_tools"):
            return "function_calling"
        if self.supports(model_id, "chat_text"):
            return "react"
        self.require_shape(model_id, "chat_tools")
        raise AssertionError("unreachable")

    def require_shape(self, model_id: str, execution_shape: str) -> None:
        if self.supports(model_id, execution_shape):
            return
        raise ManagedWorkflowRoutingError(
            "provider_workload_binding_missing",
            "Workflow Agent 缺少精确模型与执行形态的合格 Binding。",
            status_code=409,
            receipt=self.receipt_summary(),
        )

    async def stream_text(
        self,
        *,
        purpose: str,
        model_id: str,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[str]:
        self.require_shape(model_id, "chat_text")
        call_sequence, logical_call_key = self._next_call(purpose)
        async for delta in self._node_run.stream_text(
            logical_call_key=logical_call_key,
            call_sequence=call_sequence,
            model_id=model_id,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            cancel_event=cancel_event,
        ):
            yield delta

    async def complete_text(
        self,
        *,
        purpose: str,
        model_id: str,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
        cancel_event: asyncio.Event | None = None,
    ) -> str:
        turn = await self.complete(
            model_id=model_id,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            purpose=purpose,
            cancel_event=cancel_event,
        )
        if not turn.content.strip():
            raise ManagedWorkflowRoutingError(
                "provider_workload_empty_response",
                "Managed Provider 没有返回 Workflow Agent 所需文本。",
                receipt=self.receipt_summary(),
            )
        return turn.content

    async def complete_json_object(
        self,
        *,
        purpose: str,
        model_id: str,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
        cancel_event: asyncio.Event | None = None,
    ) -> str:
        self.require_shape(model_id, "chat_json_object")
        call_sequence, logical_call_key = self._next_call(purpose)
        return await self._node_run.complete_json_object(
            logical_call_key=logical_call_key,
            call_sequence=call_sequence,
            model_id=model_id,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            cancel_event=cancel_event,
        )

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
        purpose: str = "agent_model_round",
        cancel_event: asyncio.Event | None = None,
    ) -> AgentModelTurn:
        execution_shape: Literal["chat_text", "chat_tools"] = (
            "chat_tools" if tools is not None else "chat_text"
        )
        self.require_shape(model_id, execution_shape)
        call_sequence, logical_call_key = self._next_call(purpose)
        return await self._node_run.complete_agent_turn(
            logical_call_key=logical_call_key,
            call_sequence=call_sequence,
            execution_shape=execution_shape,
            model_id=model_id,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            tool_choice=tool_choice,
            parallel_tool_calls=parallel_tool_calls,
            cancel_event=cancel_event,
        )

    def finish(
        self,
        status: Literal["passed", "failed", "uncertain", "cancelled"],
        *,
        reason_code: str | None = None,
    ) -> None:
        self._node_run.finish(status, reason_code=reason_code)

    def receipt_summary(self) -> dict[str, Any]:
        return self._node_run.receipt_summary()

    def _next_call(self, purpose: str) -> tuple[int, str]:
        self._call_sequence += 1
        clean_purpose = purpose.strip() or "agent_model_round"
        return self._call_sequence, f"{clean_purpose}:{self._call_sequence}"
