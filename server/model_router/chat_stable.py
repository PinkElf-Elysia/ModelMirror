from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass

from .chat_canary import (
    DEFAULT_PROVIDER_CHAT_CERTIFICATION_MAX_AGE_SECONDS,
    MAX_PROVIDER_CHAT_CERTIFICATION_MAX_AGE_SECONDS,
    MIN_PROVIDER_CHAT_CERTIFICATION_MAX_AGE_SECONDS,
    PROVIDER_CHAT_CERTIFICATION_MAX_AGE_ENV,
)
from .chat_control import ProviderChatControlService
from .egress import AuthorizedProviderTarget, ProviderEgressError
from .provider_chat import (
    PROVIDER_CHAT_CONTRACT_VERSION,
    ProviderChatTarget,
    ProviderChatTransport,
)
from .repository import RouterRepositoryError
from .schemas import ProviderChatCapability
from .service import ModelRouterService, RouterServiceError


@dataclass(frozen=True, slots=True)
class ProviderChatCertificationBinding:
    capability: ProviderChatCapability
    connection_id: str
    certification_id: str


@dataclass(frozen=True, slots=True)
class ProviderChatStableDispatch:
    run_id: str
    attempt_id: str
    policy_fingerprint: str
    strategy: str
    capability: ProviderChatCapability
    requested_model: str
    target: ProviderChatTarget
    connection_fingerprint: str
    authorized: AuthorizedProviderTarget
    position: int
    reason_codes: tuple[str, ...]
    certification_id: str
    started_at: float
    required_certifications: tuple[ProviderChatCertificationBinding, ...]
    gateway: str


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

    def readiness(
        self,
        model_id: str,
        capability: ProviderChatCapability = "chat_text",
    ) -> tuple[bool, str | None]:
        """Inspect the current stable managed route without creating a receipt."""

        return self._readiness(model_id, capability, scoped_certified=False)

    def readiness_scoped_certified(
        self,
        model_id: str,
        capability: ProviderChatCapability = "chat_text",
    ) -> tuple[bool, str | None]:
        """Inspect an explicit workload model using current route certifications."""

        return self._readiness(model_id, capability, scoped_certified=True)

    def _readiness(
        self,
        model_id: str,
        capability: ProviderChatCapability,
        *,
        scoped_certified: bool,
    ) -> tuple[bool, str | None]:

        if not self.control.feature_enabled():
            return False, "provider_chat_control_feature_disabled"
        policy = self.control.get_policy()
        clean_model = str(model_id or "").strip()
        if (
            policy.effective_mode == "legacy"
            or (not scoped_certified and clean_model not in policy.stable_model_ids)
        ):
            return False, "provider_chat_no_qualified_route"
        runtime_allowed, runtime_error = self.control.required_runtime_allowed(policy)
        if not runtime_allowed:
            return False, runtime_error or "provider_chat_required_gate_degraded"
        route = next(
            (item for item in policy.routes if item.capability == capability),
            None,
        )
        route_ids = list(route.connection_ids if route else [])
        if policy.effective_mode == "newapi_required_default":
            route_ids = route_ids[:1]
        qualifications = self._policy_qualifications(
            policy, model_id=clean_model, capability=capability
        )
        failures: list[str] = []
        for connection_id in route_ids:
            certification_id, reason = self._qualification_for_route(
                qualifications,
                connection_id=connection_id,
                model_id=clean_model,
                capability=capability,
                scoped_certified=scoped_certified,
            )
            if certification_id is not None:
                return True, None
            failures.append(reason)
        return False, failures[-1] if failures else "provider_chat_no_qualified_route"

    async def begin(
        self,
        model_id: str,
        capability: ProviderChatCapability = "chat_text",
    ) -> ProviderChatStablePreflight:
        return await self._begin(
            model_id,
            capability,
            scoped_certified=False,
            required_capabilities=(capability,),
        )

    async def begin_scoped_certified(
        self,
        model_id: str,
        capability: ProviderChatCapability = "chat_text",
        *,
        required_capabilities: tuple[ProviderChatCapability, ...] = (),
    ) -> ProviderChatStablePreflight:
        """Begin a fixed workload call without adding the model to stable chat."""

        normalized = tuple(dict.fromkeys((*required_capabilities, capability)))
        return await self._begin(
            model_id,
            capability,
            scoped_certified=True,
            required_capabilities=normalized,
        )

    async def _begin(
        self,
        model_id: str,
        capability: ProviderChatCapability,
        *,
        scoped_certified: bool,
        required_capabilities: tuple[ProviderChatCapability, ...],
    ) -> ProviderChatStablePreflight:
        if not self.control.feature_enabled():
            return ProviderChatStablePreflight(intercepted=False)
        policy = self.control.get_policy()
        clean_model = str(model_id or "").strip()
        gateway = "ai_research_scoped" if scoped_certified else "default"
        if (
            policy.effective_mode == "legacy"
            or (not scoped_certified and clean_model not in policy.stable_model_ids)
        ):
            return ProviderChatStablePreflight(intercepted=False)
        if scoped_certified:
            try:
                reconciliation = self._repository_method(
                    "reconcile_chat_control_completions"
                )(self.router_service.tenant_id)
            except RouterRepositoryError as exc:
                raise RouterServiceError(
                    "provider_chat_completion_reconciliation_pending",
                    "上一次科研模型调用的终态仍待持久化，请稍后重试。",
                    status_code=503,
                ) from exc
            if int(reconciliation.get("pending", 0)):
                raise RouterServiceError(
                    "provider_chat_completion_reconciliation_pending",
                    "上一次科研模型调用的终态仍待持久化，请稍后重试。",
                    status_code=503,
                )
        runtime_allowed, runtime_error = self.control.required_runtime_allowed(policy)
        if not runtime_allowed:
            error_code = runtime_error or "provider_chat_required_gate_degraded"
            run_id = f"chatrun_{uuid.uuid4().hex}"
            self._repository_method("claim_chat_control_run")(
                self.router_service.tenant_id,
                run_id=run_id,
                policy_fingerprint=policy.policy_fingerprint,
                capability=capability,
                requested_model=clean_model,
                strategy=policy.effective_mode,
                gateway=gateway,
                is_real_user=True,
                primary_newapi=True,
            )
            self._repository_method("complete_chat_control_run")(
                self.router_service.tenant_id,
                run_id,
                status="failed",
                result_class="preflight_failure",
                reason_codes=[error_code],
            )
            return ProviderChatStablePreflight(
                intercepted=True,
                error_code=error_code,
                route_receipt=self.route_receipt(
                    None,
                    requested_model=clean_model,
                    reason_codes=[error_code],
                    strategy=policy.effective_mode,
                ),
            )

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
            gateway=gateway,
            epoch_id=str(epoch["id"]) if epoch is not None else None,
            is_real_user=True,
            primary_newapi=True,
        )

        prebound_certifications: dict[
            ProviderChatCapability, ProviderChatCertificationBinding
        ] = {}
        if scoped_certified:
            for required_capability in required_capabilities:
                if required_capability == capability:
                    continue
                binding, reason = self._current_scoped_binding(
                    policy,
                    model_id=clean_model,
                    capability=required_capability,
                )
                if binding is None:
                    self._repository_method("complete_chat_control_run")(
                        self.router_service.tenant_id,
                        run_id,
                        status="failed",
                        result_class="preflight_failure",
                        reason_codes=[reason],
                    )
                    return ProviderChatStablePreflight(
                        intercepted=True,
                        error_code=reason,
                        route_receipt=self.route_receipt(
                            None,
                            requested_model=clean_model,
                            reason_codes=[reason],
                            strategy=policy.effective_mode,
                        ),
                    )
                prebound_certifications[required_capability] = binding

        qualification_by_connection = self._policy_qualifications(
            policy, model_id=clean_model, capability=capability
        )
        failures: list[str] = []
        candidate_route_ids = (
            route_ids[:1]
            if policy.effective_mode == "newapi_required_default"
            else route_ids
        )
        for position, connection_id in enumerate(candidate_route_ids):
            (
                connection,
                api_key,
                connection_fingerprint,
            ) = self.repository.get_connection_credential_snapshot(
                self.router_service.tenant_id,
                connection_id,
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
            certification_id, reason = self._qualification_for_route(
                qualification_by_connection,
                connection_id=connection_id,
                model_id=clean_model,
                capability=capability,
                scoped_certified=scoped_certified,
            )
            if certification_id is None:
                self._complete_preflight_attempt(attempt_id, reason)
                failures.append(reason)
                continue
            try:
                target = ProviderChatTarget.create(
                    source="managed",
                    provider_kind=connection.kind,
                    base_url=connection.base_url,
                    api_key=api_key,
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
            actual_binding = ProviderChatCertificationBinding(
                capability=capability,
                connection_id=connection_id,
                certification_id=certification_id,
            )
            bindings = {
                **prebound_certifications,
                capability: actual_binding,
            }
            return ProviderChatStablePreflight(
                intercepted=True,
                dispatch=ProviderChatStableDispatch(
                    run_id=run_id,
                    attempt_id=attempt_id,
                    policy_fingerprint=policy.policy_fingerprint,
                    strategy=policy.effective_mode,
                    capability=capability,
                    requested_model=clean_model,
                    target=target,
                    connection_fingerprint=connection_fingerprint,
                    authorized=authorized,
                    position=position,
                    reason_codes=tuple(reason_codes),
                    certification_id=certification_id,
                    started_at=time.perf_counter(),
                    required_certifications=tuple(
                        bindings[item] for item in required_capabilities
                    ),
                    gateway=gateway,
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
                strategy=policy.effective_mode,
            ),
        )

    def mark_dispatched(self, dispatch: ProviderChatStableDispatch) -> None:
        try:
            self.ensure_dispatch_current(dispatch)
            if not self.control.feature_enabled():
                raise RouterServiceError(
                    "provider_chat_policy_or_qualification_changed",
                    "Chat 路由策略或资格已变化，请刷新设置后重试。",
                    status_code=409,
                )
            self._repository_method(
                "mark_chat_control_attempt_dispatched_if_current"
            )(
                self.router_service.tenant_id,
                dispatch.attempt_id,
                expected_run_id=dispatch.run_id,
                expected_policy_fingerprint=dispatch.policy_fingerprint,
                expected_strategy=dispatch.strategy,
                expected_model=dispatch.requested_model,
                expected_capability=dispatch.capability,
                expected_connection_id=dispatch.target.connection_id,
                expected_connection_fingerprint=(
                    dispatch.connection_fingerprint
                ),
                required_certifications=tuple(
                    (
                        binding.capability,
                        binding.connection_id,
                        binding.certification_id,
                    )
                    for binding in dispatch.required_certifications
                ),
                contract_version=PROVIDER_CHAT_CONTRACT_VERSION,
                certification_max_age_seconds=(
                    self._certification_max_age_seconds()
                ),
            )
        except (RouterServiceError, RouterRepositoryError) as exc:
            code = (
                exc.code
                if isinstance(exc, RouterServiceError)
                else str(exc)
            )
            self._repository_method(
                "fail_chat_control_preflight_if_undispatched"
            )(
                self.router_service.tenant_id,
                dispatch.attempt_id,
                expected_run_id=dispatch.run_id,
                error_code=code,
            )
            if isinstance(exc, RouterServiceError):
                raise
            raise RouterServiceError(
                code,
                "Chat 路由策略或资格已变化，请刷新设置后重试。",
                status_code=409,
            ) from exc

    def fail_undispatched(
        self,
        dispatch: ProviderChatStableDispatch,
        *,
        error_code: str,
    ) -> bool:
        """Fail a claimed attempt only if no provider dispatch occurred."""

        return bool(
            self._repository_method(
                "fail_chat_control_preflight_if_undispatched"
            )(
                self.router_service.tenant_id,
                dispatch.attempt_id,
                expected_run_id=dispatch.run_id,
                error_code=error_code,
            )
        )

    def ensure_dispatch_current(self, dispatch: ProviderChatStableDispatch) -> None:
        """Fail closed if a multi-step capability drifts between model calls."""

        policy = self.control.get_policy()
        bindings = dispatch.required_certifications
        binding_capabilities = [binding.capability for binding in bindings]
        actual_bindings = [
            binding
            for binding in bindings
            if binding.capability == dispatch.capability
        ]
        invalid = (
            policy.effective_mode != dispatch.strategy
            or policy.policy_fingerprint != dispatch.policy_fingerprint
            or not bindings
            or len(binding_capabilities) != len(set(binding_capabilities))
            or len(actual_bindings) != 1
            or actual_bindings[0].connection_id != dispatch.target.connection_id
            or actual_bindings[0].certification_id != dispatch.certification_id
        )
        if not invalid:
            for binding in bindings:
                qualification, _ = self.control.current_qualification(
                    connection_id=binding.connection_id,
                    model_id=dispatch.requested_model,
                    capability=binding.capability,
                )
                if (
                    qualification is None
                    or qualification["certification_id"] != binding.certification_id
                ):
                    invalid = True
                    break
        if invalid:
            code = "provider_chat_policy_or_qualification_changed"
            raise RouterServiceError(
                code,
                "Chat 路由策略或资格已变化，请刷新设置后重试。",
                status_code=409,
            )

    def _current_scoped_binding(
        self,
        policy,
        *,
        model_id: str,
        capability: ProviderChatCapability,
    ) -> tuple[ProviderChatCertificationBinding | None, str]:
        route = next(
            (item for item in policy.routes if item.capability == capability),
            None,
        )
        route_ids = list(route.connection_ids if route else [])
        if policy.effective_mode == "newapi_required_default":
            route_ids = route_ids[:1]
        failures: list[str] = []
        for connection_id in route_ids:
            qualification, reason = self.control.current_qualification(
                connection_id=connection_id,
                model_id=model_id,
                capability=capability,
                require_exact_model=True,
            )
            if qualification is not None:
                return (
                    ProviderChatCertificationBinding(
                        capability=capability,
                        connection_id=connection_id,
                        certification_id=str(qualification["certification_id"]),
                    ),
                    "qualified",
                )
            failures.append(reason)
        return None, (
            failures[-1] if failures else "provider_chat_no_qualified_route"
        )

    @staticmethod
    def _policy_qualifications(
        policy,
        *,
        model_id: str,
        capability: ProviderChatCapability,
    ) -> dict[str, object]:
        return {
            item.connection_id: item
            for item in policy.qualifications
            if item.capability == capability and item.model_id == model_id
        }

    def _qualification_for_route(
        self,
        policy_qualifications: dict[str, object],
        *,
        connection_id: str,
        model_id: str,
        capability: ProviderChatCapability,
        scoped_certified: bool,
    ) -> tuple[str | None, str]:
        if scoped_certified:
            current, reason = self.control.current_qualification(
                connection_id=connection_id,
                model_id=model_id,
                capability=capability,
                require_exact_model=True,
            )
            return (
                str(current["certification_id"]) if current is not None else None,
                reason,
            )
        qualification = policy_qualifications.get(connection_id)
        if qualification is not None and qualification.valid:
            return str(qualification.certification_id), "qualified"
        return (
            None,
            qualification.reason_code
            if qualification is not None
            else "provider_chat_qualification_missing",
        )

    @staticmethod
    def _certification_max_age_seconds() -> int:
        raw = os.getenv(PROVIDER_CHAT_CERTIFICATION_MAX_AGE_ENV, "").strip()
        if not raw:
            return DEFAULT_PROVIDER_CHAT_CERTIFICATION_MAX_AGE_SECONDS
        try:
            value = int(raw)
        except ValueError as exc:
            raise RouterServiceError(
                "provider_chat_policy_or_qualification_changed",
                "Chat 路由策略或资格已变化，请刷新设置后重试。",
                status_code=409,
            ) from exc
        if not (
            MIN_PROVIDER_CHAT_CERTIFICATION_MAX_AGE_SECONDS
            <= value
            <= MAX_PROVIDER_CHAT_CERTIFICATION_MAX_AGE_SECONDS
        ):
            raise RouterServiceError(
                "provider_chat_policy_or_qualification_changed",
                "Chat 路由策略或资格已变化，请刷新设置后重试。",
                status_code=409,
            )
        return value

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
    ) -> bool:
        codes = list(dispatch.reason_codes)
        if reason_codes:
            codes.extend(reason_codes)
        if error_code:
            codes.append(error_code)
        codes = list(dict.fromkeys(codes))
        fields = dict(
            expected_run_id=dispatch.run_id,
            status=status,
            result_class=result_class,
            error_code=error_code,
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
        if dispatch.gateway == "default":
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
            return True
        if dispatch.gateway != "ai_research_scoped":
            raise RouterServiceError(
                "provider_chat_completion_gateway_invalid",
                "模型调用完成范围无效。",
                status_code=409,
            )
        self._repository_method("stage_chat_control_completion")(
            self.router_service.tenant_id,
            dispatch.attempt_id,
            **fields,
        )
        reconciliation = self._repository_method(
            "reconcile_chat_control_completions"
        )(
            self.router_service.tenant_id,
            attempt_id=dispatch.attempt_id,
        )
        return int(reconciliation.get("pending", 0)) == 0

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
        strategy: str = "newapi_preferred",
    ) -> dict[str, object]:
        codes = list(dispatch.reason_codes) if dispatch is not None else []
        codes.extend(reason_codes or [])
        return {
            "requested_model": requested_model,
            "actual_model": actual_model or requested_model,
            "provider": None,
            "strategy": dispatch.strategy if dispatch is not None else strategy,
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
