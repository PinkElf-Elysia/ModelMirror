from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from .chat_control import ProviderChatControlService
from .egress import AuthorizedProviderTarget, ProviderEgressError
from .provider_chat import ProviderChatTarget, ProviderChatTransport
from .schemas import ProviderChatCapability
from .service import ModelRouterService, RouterServiceError


@dataclass(frozen=True, slots=True)
class ProviderChatStableDispatch:
    run_id: str
    attempt_id: str
    policy_fingerprint: str
    capability: ProviderChatCapability
    requested_model: str
    target: ProviderChatTarget
    authorized: AuthorizedProviderTarget
    position: int
    reason_codes: tuple[str, ...]
    started_at: float


@dataclass(frozen=True, slots=True)
class ProviderChatStablePreflight:
    intercepted: bool
    dispatch: ProviderChatStableDispatch | None = None
    error_code: str | None = None
    route_receipt: dict[str, object] | None = None


class ProviderChatStableService:
    """Plan one stable Managed Chat request without storing request content."""

    def __init__(self, router_service: ModelRouterService) -> None:
        self.router_service = router_service
        self.repository = router_service.repository
        self.control = ProviderChatControlService(router_service)
        self.transport = ProviderChatTransport(router_service.egress_policy)

    async def begin(
        self,
        model_id: str,
        capability: ProviderChatCapability = "chat_text",
    ) -> ProviderChatStablePreflight:
        if not self.control.feature_enabled():
            return ProviderChatStablePreflight(intercepted=False)
        policy = self.control.get_policy()
        clean_model = str(model_id or "").strip()
        if (
            policy.effective_mode == "legacy"
            or clean_model not in policy.stable_model_ids
        ):
            return ProviderChatStablePreflight(intercepted=False)

        route = next(
            (item for item in policy.routes if item.capability == capability),
            None,
        )
        route_ids = list(route.connection_ids if route else [])
        epoch = self._repository_method("get_open_chat_control_gate_epoch")(
            self.router_service.tenant_id,
            policy.policy_fingerprint,
        )
        run_id = f"chatrun_{uuid.uuid4().hex}"
        self._repository_method("claim_chat_control_run")(
            self.router_service.tenant_id,
            run_id=run_id,
            policy_fingerprint=policy.policy_fingerprint,
            capability=capability,
            requested_model=clean_model,
            strategy=policy.effective_mode,
            gateway="default",
            epoch_id=str(epoch["id"]) if epoch is not None else None,
            is_real_user=True,
            primary_newapi=True,
        )

        qualification_by_connection = {
            item.connection_id: item
            for item in policy.qualifications
            if item.capability == capability and item.model_id == clean_model
        }
        failures: list[str] = []
        for position, connection_id in enumerate(route_ids):
            connection = self.repository.get_connection(
                self.router_service.tenant_id, connection_id
            )
            attempt_id = f"chatattempt_{uuid.uuid4().hex}"
            self._repository_method("claim_chat_control_attempt")(
                self.router_service.tenant_id,
                attempt_id=attempt_id,
                run_id=run_id,
                capability=capability,
                position=position,
                connection_id=connection_id,
                provider_kind=connection.kind,
            )
            qualification = qualification_by_connection.get(connection_id)
            if qualification is None or not qualification.valid:
                reason = (
                    qualification.reason_code
                    if qualification is not None
                    else "provider_chat_qualification_missing"
                )
                self._complete_preflight_attempt(attempt_id, reason)
                failures.append(reason)
                continue
            try:
                target = ProviderChatTarget.create(
                    source="managed",
                    provider_kind=connection.kind,
                    base_url=connection.base_url,
                    api_key=self.repository.resolve_api_key(
                        self.router_service.tenant_id, connection_id
                    ),
                    connection_id=connection_id,
                )
                authorized = await self.transport.authorize_managed_target(target)
            except ProviderEgressError as exc:
                self._complete_preflight_attempt(attempt_id, exc.code)
                failures.append(exc.code)
                continue
            except Exception as exc:
                reason = getattr(exc, "code", None) or "provider_chat_preflight_failed"
                self._complete_preflight_attempt(attempt_id, str(reason))
                failures.append(str(reason))
                continue

            reason_codes = list(dict.fromkeys(failures))
            if position:
                reason_codes.append("provider_chat_preflight_backup_selected")
            return ProviderChatStablePreflight(
                intercepted=True,
                dispatch=ProviderChatStableDispatch(
                    run_id=run_id,
                    attempt_id=attempt_id,
                    policy_fingerprint=policy.policy_fingerprint,
                    capability=capability,
                    requested_model=clean_model,
                    target=target,
                    authorized=authorized,
                    position=position,
                    reason_codes=tuple(reason_codes),
                    started_at=time.perf_counter(),
                ),
            )

        error_code = failures[-1] if failures else "provider_chat_no_qualified_route"
        self._repository_method("complete_chat_control_run")(
            self.router_service.tenant_id,
            run_id,
            status="failed",
            result_class="preflight_failure",
            reason_codes=list(dict.fromkeys(failures or [error_code])),
        )
        return ProviderChatStablePreflight(
            intercepted=True,
            error_code=error_code,
            route_receipt=self.route_receipt(
                None,
                requested_model=clean_model,
                reason_codes=list(dict.fromkeys(failures or [error_code])),
            ),
        )

    def mark_dispatched(self, dispatch: ProviderChatStableDispatch) -> None:
        try:
            self.ensure_dispatch_current(dispatch)
        except RouterServiceError as exc:
            self._complete_preflight_attempt(dispatch.attempt_id, exc.code)
            self._repository_method("complete_chat_control_run")(
                self.router_service.tenant_id,
                dispatch.run_id,
                status="failed",
                result_class="preflight_failure",
                reason_codes=[exc.code],
            )
            raise
        self._repository_method("mark_chat_control_attempt_dispatched")(
            self.router_service.tenant_id, dispatch.attempt_id
        )

    def ensure_dispatch_current(self, dispatch: ProviderChatStableDispatch) -> None:
        """Fail closed if a multi-step capability drifts between model calls."""

        policy = self.control.get_policy()
        qualification = next(
            (
                item
                for item in policy.qualifications
                if item.capability == dispatch.capability
                and item.model_id == dispatch.requested_model
                and item.connection_id == dispatch.target.connection_id
            ),
            None,
        )
        if (
            policy.effective_mode != "newapi_preferred"
            or policy.policy_fingerprint != dispatch.policy_fingerprint
            or qualification is None
            or not qualification.valid
        ):
            code = "provider_chat_policy_or_qualification_changed"
            raise RouterServiceError(
                code,
                "Chat 路由策略或资格已变化，请刷新设置后重试。",
                status_code=409,
            )

    def complete(
        self,
        dispatch: ProviderChatStableDispatch,
        *,
        status: str,
        result_class: str,
        error_code: str | None = None,
        actual_model: str | None = None,
        client_cancelled: bool = False,
        hard_failure: bool = False,
        ttft_ms: float | None = None,
        e2e_ms: float | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        reason_codes: list[str] | None = None,
    ) -> None:
        codes = list(dispatch.reason_codes)
        if reason_codes:
            codes.extend(reason_codes)
        if error_code:
            codes.append(error_code)
        codes = list(dict.fromkeys(codes))
        self._repository_method("complete_chat_control_attempt")(
            self.router_service.tenant_id,
            dispatch.attempt_id,
            status=status,
            result_class=result_class,
            error_code=error_code,
            actual_model=actual_model,
            ttft_ms=ttft_ms,
            e2e_ms=e2e_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )
        self._repository_method("complete_chat_control_run")(
            self.router_service.tenant_id,
            dispatch.run_id,
            status=status,
            result_class=result_class,
            reason_codes=codes,
            actual_model=actual_model,
            client_cancelled=client_cancelled,
            hard_failure=hard_failure,
            ttft_ms=ttft_ms,
            e2e_ms=e2e_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )

    @staticmethod
    def classify_http_failure(status_code: int) -> tuple[str, str, bool]:
        code = f"provider_chat_http_{status_code}"
        if status_code in {401, 402, 403, 404}:
            return "hard_failure", code, True
        if status_code == 429 or status_code >= 500:
            return "transient_failure", code, False
        return "request_failure", code, False

    @staticmethod
    def route_receipt(
        dispatch: ProviderChatStableDispatch | None,
        *,
        requested_model: str,
        actual_model: str | None = None,
        reason_codes: list[str] | None = None,
        request_id: str | None = None,
        ttft_ms: float | None = None,
        e2e_ms: float | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
    ) -> dict[str, object]:
        codes = list(dispatch.reason_codes) if dispatch is not None else []
        codes.extend(reason_codes or [])
        return {
            "requested_model": requested_model,
            "actual_model": actual_model or requested_model,
            "provider": None,
            "strategy": "newapi_preferred",
            "engine": (
                dispatch.target.provider_kind
                if dispatch is not None
                else "managed_chat_blocked"
            ),
            "reason_codes": list(dict.fromkeys(codes)),
            "latency_ms": e2e_ms,
            "ttft_ms": ttft_ms,
            "tokens": {
                "input": prompt_tokens,
                "output": completion_tokens,
                "total": total_tokens,
            },
            "response_cost_usd": None,
            "cost_kind": "unavailable",
            "fallback_attempts": dispatch.position if dispatch is not None else 0,
            "cache_hit": None,
            "request_id": request_id,
            "version": "2",
        }

    def _complete_preflight_attempt(self, attempt_id: str, code: str) -> None:
        self._repository_method("complete_chat_control_attempt")(
            self.router_service.tenant_id,
            attempt_id,
            status="failed",
            result_class="preflight_failure",
            error_code=code,
        )

    def _repository_method(self, name: str):
        method = getattr(self.repository, name, None)
        if not callable(method):
            raise RouterServiceError(
                "provider_chat_control_storage_unavailable",
                "当前 Router 存储不支持 Managed Chat 路由。",
                status_code=503,
            )
        return method
