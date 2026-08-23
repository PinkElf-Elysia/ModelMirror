from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any, Literal

import httpx

try:
    from server.agent_workspace.gateway import (
        DeltaCallback,
        GatewayCapabilityError,
        GatewayRequestError,
        GatewayTurn,
        OpenAICompatibleGateway,
    )
    from server.model_router.repository import RouterRepositoryError
    from server.model_router.service import RouterServiceError
    from server.model_router.workload_control import (
        ProviderWorkloadCallService,
        ProviderWorkloadPreparedCall,
    )
except ImportError:  # pragma: no cover - direct server package execution
    from agent_workspace.gateway import (
        DeltaCallback,
        GatewayCapabilityError,
        GatewayRequestError,
        GatewayTurn,
        OpenAICompatibleGateway,
    )
    from model_router.repository import RouterRepositoryError
    from model_router.service import RouterServiceError
    from model_router.workload_control import (
        ProviderWorkloadCallService,
        ProviderWorkloadPreparedCall,
    )


ShadowRoutingMode = Literal["legacy", "managed_required", "degraded_required"]


class ManagedShadowRoutingError(RuntimeError):
    def __init__(self, code: str, public_message: str) -> None:
        super().__init__(code)
        self.code = code
        self.public_message = public_message


class ManagedShadowGateway:
    """Host-only Engine Shadow adapter for one exact managed Provider target."""

    def __init__(
        self,
        call_service: ProviderWorkloadCallService,
        *,
        parser: OpenAICompatibleGateway | None = None,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self.call_service = call_service
        self.parser = parser or OpenAICompatibleGateway()
        self._client_factory = client_factory

    def routing_mode(self) -> ShadowRoutingMode:
        control = self.call_service.control
        if not control.feature_enabled("agent_shadow"):
            return "legacy"
        policy = control.get_policy("agent_shadow")
        if policy.configured_status == "legacy":
            return "legacy"
        if policy.effective_status == "managed_required":
            return "managed_required"
        return "degraded_required"

    def resolve_exact_model(self, requested_model_id: str) -> str | None:
        """Resolve only a currently valid exact chat_tools Binding."""

        clean = requested_model_id.strip()
        if not clean:
            return None
        policy = self.call_service.control.get_policy("agent_shadow")
        if policy.effective_status != "managed_required":
            return None
        binding = next(
            (
                item
                for item in policy.bindings
                if item.execution_shape == "chat_tools"
                and item.model_id == clean
                and item.valid
            ),
            None,
        )
        return binding.model_id if binding is not None else None

    def start_run(self, *, parent_run_reference: str) -> str:
        return self.call_service.start_run(
            "agent_shadow", parent_run_reference=parent_run_reference
        )

    async def stream_turn(
        self,
        *,
        workload_run_id: str,
        logical_call_key: str,
        call_sequence: int,
        model_id: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
        thinking_level: str,
        timeout_ms: int,
        on_delta: DeltaCallback,
    ) -> GatewayTurn:
        prepared: ProviderWorkloadPreparedCall | None = None
        dispatched = False
        try:
            prepared = await self.call_service.prepare_call(
                run_id=workload_run_id,
                entry_id="agent_shadow",
                execution_shape="chat_tools",
                model_id=model_id,
                logical_call_key=logical_call_key,
                call_sequence=call_sequence,
            )
            payload = self.parser.build_stream_payload(
                model_id=model_id,
                messages=messages,
                tools=tools,
                max_tokens=max_tokens,
                thinking_level=thinking_level,
            )
            client = (
                self._client_factory()
                if self._client_factory is not None
                else httpx.AsyncClient(
                    timeout=httpx.Timeout(
                        connect=min(15.0, timeout_ms / 1000),
                        read=timeout_ms / 1000,
                        write=30.0,
                        pool=10.0,
                    ),
                    follow_redirects=False,
                    trust_env=False,
                    transport=httpx.AsyncHTTPTransport(retries=0),
                )
            )
            async with client:
                request = self.call_service.transport.build_authorized_stream_request(
                    client,
                    prepared.target,
                    prepared.authorized_target,
                    payload,
                )
                self.call_service.mark_dispatched(prepared)
                dispatched = True
                started_at = time.monotonic()
                response = await self.call_service.transport.send_authorized_stream(
                    client, request
                )
                try:
                    turn = await self.parser.consume_stream_response(
                        response,
                        requested_model_id=model_id,
                        on_delta=on_delta,
                        started_at=started_at,
                        require_terminal=True,
                    )
                finally:
                    await response.aclose()
            if turn.model_id != model_id:
                raise ManagedShadowRoutingError(
                    "provider_workload_actual_model_mismatch",
                    "The managed Provider returned a different model than the exact Binding.",
                )
            self.call_service.complete_call(
                prepared,
                status="passed",
                result_class="success",
                actual_model=turn.model_id,
                ttft_ms=turn.ttft_ms,
                e2e_ms=turn.e2e_ms,
                prompt_tokens=turn.prompt_tokens,
                completion_tokens=turn.completion_tokens,
                total_tokens=turn.total_tokens,
            )
            return turn
        except asyncio.CancelledError:
            self._complete_call_safely(
                prepared,
                status="cancelled",
                result_class="client_cancelled",
                error_code="provider_workload_call_cancelled",
            )
            raise
        except ManagedShadowRoutingError as exc:
            self._complete_call_safely(
                prepared,
                status="failed",
                result_class="model_mismatch",
                error_code=exc.code,
            )
            raise GatewayRequestError(exc.public_message) from exc
        except GatewayCapabilityError:
            self._complete_call_safely(
                prepared,
                status="failed",
                result_class="capability_error",
                error_code="provider_workload_tool_capability_failed",
            )
            raise
        except GatewayRequestError:
            self._complete_call_safely(
                prepared,
                status="failed",
                result_class="provider_error" if dispatched else "preflight_error",
                error_code=(
                    "provider_workload_provider_request_failed"
                    if dispatched
                    else "provider_workload_preflight_failed"
                ),
            )
            raise
        except RouterServiceError as exc:
            self._complete_call_safely(
                prepared,
                status="uncertain" if dispatched else "failed",
                result_class="control_plane_error",
                error_code=exc.code,
            )
            raise GatewayRequestError(
                "The managed Provider route failed closed before a replay could occur."
            ) from exc
        except Exception as exc:
            self._complete_call_safely(
                prepared,
                status="uncertain" if dispatched else "failed",
                result_class="transport_error" if dispatched else "preflight_error",
                error_code=(
                    "provider_workload_dispatch_uncertain"
                    if dispatched
                    else "provider_workload_preflight_failed"
                ),
            )
            raise GatewayRequestError(
                "The managed Provider request failed without replay."
            ) from exc

    def finish_run(self, workload_run_id: str, shadow_status: str) -> None:
        status = "failed"
        result_class = "shadow_failed"
        reason_codes = [f"agent_shadow_{shadow_status}"]
        if shadow_status == "candidate_ready":
            status = "passed"
            result_class = "candidate_ready"
            reason_codes = []
        elif shadow_status == "stopped":
            status = "cancelled"
            result_class = "user_stopped"
        elif shadow_status == "interrupted":
            status = "uncertain"
            result_class = "server_interrupted"
        try:
            self.call_service.complete_run(
                workload_run_id,
                status=status,
                result_class=result_class,
                reason_codes=reason_codes,
            )
        except RouterRepositoryError as exc:
            if status == "passed" and str(exc) == (
                "provider_workload_run_passed_without_successful_calls"
            ):
                self.call_service.complete_run(
                    workload_run_id,
                    status="failed",
                    result_class="workload_call_failed",
                    reason_codes=["provider_workload_call_failed"],
                )
                return
            if str(exc) != "provider_workload_run_not_running":
                raise

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
            self.call_service.complete_call(
                prepared,
                status=status,
                result_class=result_class,
                error_code=error_code,
            )
        except RouterRepositoryError:
            # Preserve the original transport/control-plane failure. Startup
            # reconciliation will mark any still-running receipt uncertain.
            return
