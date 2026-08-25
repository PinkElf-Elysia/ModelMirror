from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import replace
from typing import Any, Literal, cast

import httpx

from .service import ModelRouterService, RouterServiceError
from .workflow_gateway import (
    ManagedWorkflowGateway,
    ManagedWorkflowNodeRun,
    ManagedWorkflowRoutingError,
    WorkflowEntryId,
)
from .workload_control import (
    PROVIDER_WORKLOAD_CONTRACT_VERSION,
    ProviderWorkloadCallService,
    ProviderWorkloadPreparedCall,
)


RouteTeamEntryId = Literal["route_agent", "team_chat"]
RouteTeamRoutingMode = Literal[
    "legacy", "managed_required", "degraded_required"
]


class ManagedRouteTeamGateway:
    """Fail-closed Provider control-plane adapter for Route Agent and Team Chat."""

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
    ) -> ManagedRouteTeamGateway:
        return cls(
            ProviderWorkloadCallService(router_service),
            client_factory=client_factory,
        )

    def routing_mode(self, entry_id: RouteTeamEntryId) -> RouteTeamRoutingMode:
        control = self.call_service.control
        if not control.feature_enabled(entry_id):
            return "legacy"
        policy = control.get_policy(entry_id)
        if policy.configured_status == "legacy":
            return "legacy"
        if policy.effective_status == "managed_required":
            return "managed_required"
        return "degraded_required"

    def start_run(self, entry_id: RouteTeamEntryId) -> ManagedRouteTeamRun:
        if self.routing_mode(entry_id) != "managed_required":
            raise ManagedWorkflowRoutingError(
                "provider_workload_policy_not_active",
                "该专家团入口的 Managed Provider 策略未就绪，当前调用失败关闭。",
                status_code=409,
                receipt=self.blocked_receipt(
                    entry_id, "provider_workload_policy_not_active"
                ),
            )
        try:
            run_id = self.call_service.start_run(entry_id)
        except RouterServiceError as exc:
            raise ManagedWorkflowRoutingError(
                exc.code,
                "该专家团入口的 Provider 资格已失效，当前调用失败关闭。",
                status_code=exc.status_code,
                receipt=self.blocked_receipt(entry_id, exc.code),
            ) from exc
        return ManagedRouteTeamRun(
            entry_id,
            ManagedWorkflowNodeRun(
                self._workflow_gateway,
                cast(WorkflowEntryId, entry_id),
                run_id,
            ),
        )

    @staticmethod
    def blocked_receipt(
        entry_id: RouteTeamEntryId, reason_code: str
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


class ManagedRouteTeamRun:
    """One Route Agent or Team Chat run with a fully preflighted call plan."""

    def __init__(
        self,
        entry_id: RouteTeamEntryId,
        node_run: ManagedWorkflowNodeRun,
    ) -> None:
        self.entry_id = entry_id
        self._node_run = node_run

    async def prepare_plan(
        self,
        *,
        model_id: str,
        logical_call_keys: Iterable[str],
    ) -> tuple[ProviderWorkloadPreparedCall, ...]:
        prepared: list[ProviderWorkloadPreparedCall] = []
        try:
            for call_sequence, logical_call_key in enumerate(
                logical_call_keys, start=1
            ):
                prepared.append(
                    await self._node_run.prepare_stream_call(
                        logical_call_key=logical_call_key,
                        call_sequence=call_sequence,
                        execution_shape="chat_text",
                        model_id=model_id,
                    )
                )
        except ManagedWorkflowRoutingError as exc:
            self.abandon(
                prepared,
                code="provider_workload_plan_preflight_aborted",
            )
            self.finish("failed", reason_code=exc.code)
            exc.receipt = self.receipt_summary()
            raise
        return tuple(prepared)

    async def stream_text(
        self,
        prepared: ProviderWorkloadPreparedCall,
        *,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[str]:
        try:
            authorized_target = (
                await self._node_run.gateway.call_service.transport.authorize_managed_target(
                    prepared.target
                )
            )
        except httpx.HTTPError as exc:
            code = str(getattr(exc, "code", "provider_workload_egress_rejected"))
            self._node_run.fail_prepared_call(prepared, code=code)
            raise ManagedWorkflowRoutingError(
                code,
                "Managed Provider 在实际派发前未通过出口授权，系统未发送请求。",
                status_code=409,
                receipt=self.receipt_summary(),
            ) from exc
        prepared = replace(prepared, authorized_target=authorized_target)
        async for delta in self._node_run.stream_prepared_text(
            prepared,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        ):
            yield delta

    def abandon(
        self,
        prepared_calls: Iterable[ProviderWorkloadPreparedCall],
        *,
        code: str,
        status: Literal["failed", "cancelled"] = "failed",
    ) -> None:
        for prepared in prepared_calls:
            self._node_run.fail_prepared_call(
                prepared,
                code=code,
                status=status,
                result_class=(
                    "client_cancelled" if status == "cancelled" else "plan_aborted"
                ),
            )

    def finish(
        self,
        status: Literal["passed", "failed", "uncertain", "cancelled"],
        *,
        reason_code: str | None = None,
    ) -> dict[str, Any]:
        self._node_run.finish(status, reason_code=reason_code)
        return self.receipt_summary()

    def failure_status(self) -> Literal["failed", "uncertain"]:
        return (
            "uncertain"
            if any(call.status == "uncertain" for call in self._node_run.calls)
            else "failed"
        )

    def receipt_summary(self) -> dict[str, Any]:
        summary = self._node_run.receipt_summary()
        summary["entry_id"] = self.entry_id
        summary["calls"] = sorted(
            summary["calls"], key=lambda item: int(item["call_sequence"])
        )
        return summary
