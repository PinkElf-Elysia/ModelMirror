from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from typing import Literal

from .call_context import (
    ProviderCallContext, ProviderCallContextError, require_provider_call_context,
)

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
    call_context: ProviderCallContext


@dataclass(frozen=True, slots=True)
class ProviderChatStablePreflight:
    intercepted: bool
    dispatch: ProviderChatStableDispatch | None = None
    error_code: str | None = None
    route_receipt: dict[str, object] | None = None


class ProviderChatStableService:
    """Plan one stable Managed Chat request without storing request content."""

    def __init__(
        self, router_service: ModelRouterService, *,
        origin_entry_id: Literal["chat_default", "ai_research_bridge"] = "chat_default",
    ) -> None:
        # Set only by the server-owned entry dependency, never request fields.
        if origin_entry_id not in {"chat_default", "ai_research_bridge"}:
            raise ProviderCallContextError("provider_call_context_entry_invalid")
        self._origin_entry_id = origin_entry_id
        # Server-owned creation facts, independent of the returned dispatch.
        # This is deliberately not a recovery store: a new service must not
        # infer historical provenance from a caller-supplied dispatch object.
        self._call_origins: dict[str, tuple[str, ProviderCallContext]] = {}
        self.router_service = router_service
        self.repository = router_service.repository
        self.control = ProviderChatControlService(router_service)
        self.transport = ProviderChatTransport(router_service.egress_policy)

    def _context_origin(self, gateway: str) -> tuple[str, str, str, str]:
        if gateway == "rpg_scoped":
            return "service", "rpg-bridge", "rpg_bridge", "user_run"
        if gateway == "ai_research_scoped" or (
            gateway == "default" and self._origin_entry_id == "ai_research_bridge"
        ):
            # A service-authenticated research run is not automatically a
            # background helper merely because it arrives through a bridge.
            return "service", "ai-research-bridge", "ai_research_bridge", "user_run"
        if gateway == "default":
            return "local_anonymous", "local-anonymous", "chat_default", "user_run"
        raise ProviderCallContextError("provider_call_context_entry_invalid")

    def _call_context(self, gateway: str, run_id: str, attempt_id: str, revision: int) -> ProviderCallContext:
        actor, reference, entry, purpose = self._context_origin(gateway)
        return ProviderCallContext(
            tenant_id=self.router_service.tenant_id,
            actor_kind=actor, actor_reference=reference, entry_id=entry, purpose=purpose,
            parent_run_id=run_id, logical_call_id=f"{run_id}:chat",
            attempt_id=attempt_id, policy_revision=revision,
        )

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
        *,
        required_capabilities: tuple[ProviderChatCapability, ...] = (),
    ) -> tuple[bool, str | None]:
        """Inspect an explicit workload model using current route certifications."""

        normalized = tuple(dict.fromkeys((*required_capabilities, capability)))
        return self._readiness(
            model_id,
            capability,
            scoped_certified=True,
            required_capabilities=normalized,
        )

    def _readiness(
        self,
        model_id: str,
        capability: ProviderChatCapability,
        *,
        scoped_certified: bool,
        required_capabilities: tuple[ProviderChatCapability, ...] = (),
    ) -> tuple[bool, str | None]:

        if not self.control.feature_enabled():
            return False, "provider_chat_control_feature_disabled"
        policy = self.control.peek_policy()
        clean_model = str(model_id or "").strip()
        if not scoped_certified:
            available, _, reason = self.control.managed_readiness(
                policy, clean_model, capability
            )
            return available, None if available else reason
        intercepted, error = self.control.route_admission(
            policy, clean_model, scoped_certified=True
        )
        if not intercepted or error is not None:
            return False, error
        route_ids = self.control.route_candidates(policy, capability)
        failures: list[str] = []
        for connection_id in route_ids:
            bindings, reason = self._scoped_bindings_for_connection(
                policy,
                model_id=clean_model,
                connection_id=connection_id,
                required_capabilities=(required_capabilities or (capability,)),
            )
            if bindings is not None:
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

    async def begin_rpg_certified(self, model_id: str) -> ProviderChatStablePreflight:
        """Use current text certification without changing stable chat membership."""
        return await self._begin(
            model_id, "chat_text", scoped_certified=True,
            required_capabilities=("chat_text",), scoped_gateway="rpg_scoped",
        )

    async def _begin(
        self,
        model_id: str,
        capability: ProviderChatCapability,
        *,
        scoped_certified: bool,
        required_capabilities: tuple[ProviderChatCapability, ...],
        scoped_gateway: str = "ai_research_scoped",
    ) -> ProviderChatStablePreflight:
        if not self.control.feature_enabled():
            return ProviderChatStablePreflight(intercepted=False)
        policy = self.control.get_policy()
        clean_model = str(model_id or "").strip()
        gateway = scoped_gateway if scoped_certified else "default"
        if gateway not in {"default", "ai_research_scoped", "rpg_scoped"}:
            raise ValueError("provider_chat_scope_invalid")
        intercepted, admission_error = self.control.route_admission(
            policy, clean_model, scoped_certified=scoped_certified,
        )
        if not intercepted:
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
        if admission_error is not None:
            error_code = admission_error
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

        route_ids = self.control.route_candidates(policy, capability)
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

        failures: list[str] = []
        for position, connection_id in enumerate(route_ids):
            (
                connection,
                api_key,
                connection_fingerprint,
            ) = self.repository.get_connection_credential_snapshot(
                self.router_service.tenant_id,
                connection_id,
            )
            attempt_id = f"chatattempt_{uuid.uuid4().hex}"
            call_context = self._call_context(gateway, run_id, attempt_id, policy.revision)
            self._repository_method("claim_chat_control_attempt")(
                self.router_service.tenant_id,
                attempt_id=attempt_id,
                run_id=run_id,
                capability=capability,
                position=position,
                connection_id=connection_id,
                provider_kind=connection.kind,
                call_context=call_context,
            )
            bindings: dict[
                ProviderChatCapability, ProviderChatCertificationBinding
            ] = {}
            if scoped_certified:
                scoped_bindings, reason = self._scoped_bindings_for_connection(
                    policy,
                    model_id=clean_model,
                    connection_id=connection_id,
                    required_capabilities=required_capabilities,
                )
                if scoped_bindings is not None:
                    bindings = scoped_bindings
                    certification_id = bindings[capability].certification_id
                else:
                    certification_id = None
            else:
                certification_id, reason = self.control.route_qualification(
                    policy,
                    connection_id=connection_id,
                    model_id=clean_model,
                    capability=capability,
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
            bindings[capability] = actual_binding
            self._call_origins[attempt_id] = (gateway, call_context)
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
                    call_context=call_context,
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
                call_context=dispatch.call_context,
            )
        except (RouterServiceError, RouterRepositoryError, ProviderCallContextError) as exc:
            code = (
                exc.code
                if isinstance(exc, (RouterServiceError, ProviderCallContextError))
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
        # Preserve the existing public policy/qualification error contract.
        # Provenance adds a check; it must not mask an established drift reason.
        try:
            origin = self._call_origins.get(dispatch.attempt_id)
            if origin is None:
                raise ProviderCallContextError("provider_call_context_origin_missing")
            expected_gateway, expected = origin
            if dispatch.gateway != expected_gateway:
                raise ProviderCallContextError("provider_call_context_entry_mismatch")
            if dispatch.run_id != expected.parent_run_id:
                raise ProviderCallContextError("provider_call_context_reference_mismatch")
            if expected.policy_revision != policy.revision:
                raise ProviderCallContextError("provider_call_context_policy_changed")
            require_provider_call_context(
                dispatch.call_context,
                actor_kind=expected.actor_kind, actor_reference=expected.actor_reference,
                purpose=expected.purpose, entry_id=expected.entry_id,
                parent_run_id=dispatch.run_id, logical_call_id=expected.logical_call_id,
                attempt_id=dispatch.attempt_id, policy_revision=policy.revision,
            )
        except ProviderCallContextError as exc:
            raise RouterServiceError(
                exc.code, "模型调用来源或策略已变化，本次调用在派发前阻断。", status_code=409,
            ) from exc

    def _scoped_bindings_for_connection(
        self,
        policy,
        *,
        model_id: str,
        connection_id: str,
        required_capabilities: tuple[ProviderChatCapability, ...],
    ) -> tuple[
        dict[ProviderChatCapability, ProviderChatCertificationBinding] | None,
        str,
    ]:
        bindings: dict[
            ProviderChatCapability, ProviderChatCertificationBinding
        ] = {}
        for capability in required_capabilities:
            route_ids = self.control.route_candidates(policy, capability)
            if connection_id not in route_ids:
                return None, "provider_chat_no_qualified_route"
            qualification, reason = self.control.current_qualification(
                connection_id=connection_id,
                model_id=model_id,
                capability=capability,
                require_exact_model=True,
            )
            if qualification is None:
                return None, reason
            bindings[capability] = ProviderChatCertificationBinding(
                capability=capability,
                connection_id=connection_id,
                certification_id=str(qualification["certification_id"]),
            )
        return bindings, "qualified"

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
        if dispatch.gateway not in {"ai_research_scoped", "rpg_scoped"}:
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
