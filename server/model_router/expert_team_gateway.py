from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, cast

import httpx

try:
    from server.orchestration_worker import (
        AgencyModelRequest,
        AgencyModelResponse,
        AgencyWorkerError,
    )
except ImportError:  # pragma: no cover - direct server package execution
    from orchestration_worker import (
        AgencyModelRequest,
        AgencyModelResponse,
        AgencyWorkerError,
    )

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
)


ExpertTeamEntryId = Literal["expert_team_planner", "expert_team_dag"]
ExpertTeamRoutingMode = Literal[
    "legacy", "managed_required", "degraded_required"
]


class ManagedExpertTeamGateway:
    """Provider control-plane adapter for Agency Worker host callbacks."""

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
    ) -> ManagedExpertTeamGateway:
        return cls(
            ProviderWorkloadCallService(router_service),
            client_factory=client_factory,
        )

    def routing_mode(self, entry_id: ExpertTeamEntryId) -> ExpertTeamRoutingMode:
        control = self.call_service.control
        if not control.feature_enabled(entry_id):
            return "legacy"
        policy = control.get_policy(entry_id)
        if policy.configured_status == "legacy":
            return "legacy"
        if policy.effective_status == "managed_required":
            return "managed_required"
        return "degraded_required"

    def start_run(
        self,
        entry_id: ExpertTeamEntryId,
        *,
        parent_run_reference: str,
    ) -> ManagedExpertTeamRun:
        if self.routing_mode(entry_id) != "managed_required":
            raise ManagedWorkflowRoutingError(
                "provider_workload_policy_not_active",
                "Expert Team 的 Managed Provider 策略未就绪，当前调用失败关闭。",
                status_code=409,
                receipt=self.blocked_receipt(
                    entry_id, "provider_workload_policy_not_active"
                ),
            )
        try:
            run_id = self.call_service.start_stable_run(
                entry_id,
                parent_run_reference=parent_run_reference,
            )
        except RouterServiceError as exc:
            raise ManagedWorkflowRoutingError(
                exc.code,
                "Expert Team 已有执行证据或资格已失效，系统不会自动重放。",
                status_code=exc.status_code,
                receipt=self.blocked_receipt(entry_id, exc.code),
            ) from exc
        node_run = ManagedWorkflowNodeRun(
            self._workflow_gateway,
            cast(WorkflowEntryId, entry_id),
            run_id,
        )
        return ManagedExpertTeamRun(entry_id, node_run)

    @staticmethod
    def blocked_receipt(
        entry_id: ExpertTeamEntryId, reason_code: str
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


class ManagedExpertTeamRun:
    """One Planner or DAG segment with stable Worker request receipts."""

    def __init__(
        self,
        entry_id: ExpertTeamEntryId,
        node_run: ManagedWorkflowNodeRun,
    ) -> None:
        self.entry_id = entry_id
        self._node_run = node_run
        self._call_sequence = 0

    async def complete(self, request: AgencyModelRequest) -> AgencyModelResponse:
        self._call_sequence += 1
        call_sequence = self._call_sequence
        messages = [
            {"role": message.role, "content": message.content}
            for message in request.messages
        ]
        try:
            if request.json_response:
                content = await self._node_run.complete_json_object(
                    logical_call_key=request.request_id,
                    call_sequence=call_sequence,
                    model_id=request.model_id,
                    messages=messages,
                    temperature=request.temperature,
                    max_tokens=request.max_tokens,
                )
            else:
                content = await self._node_run.complete_text_unary(
                    logical_call_key=request.request_id,
                    call_sequence=call_sequence,
                    model_id=request.model_id,
                    messages=messages,
                    temperature=request.temperature,
                    max_tokens=request.max_tokens,
                )
        except ManagedWorkflowRoutingError as exc:
            raise AgencyWorkerError(
                exc.public_message,
                code=exc.code,
            ) from exc

        receipt = next(
            (
                item
                for item in self._node_run.calls
                if item.call_sequence == call_sequence
            ),
            None,
        )
        usage: dict[str, int] = {}
        if receipt is not None:
            if receipt.prompt_tokens is not None:
                usage["input_tokens"] = receipt.prompt_tokens
            if receipt.completion_tokens is not None:
                usage["output_tokens"] = receipt.completion_tokens
        return AgencyModelResponse(content=content, usage=usage)

    def finish(
        self,
        status: Literal["passed", "failed", "uncertain", "cancelled"],
        *,
        reason_code: str | None = None,
    ) -> dict[str, Any]:
        self._node_run.finish(status, reason_code=reason_code)
        return self.receipt_summary()

    def receipt_summary(self) -> dict[str, Any]:
        summary = self._node_run.receipt_summary()
        summary["calls"] = sorted(
            summary["calls"], key=lambda item: int(item["call_sequence"])
        )
        return summary
