from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from .chat_control import ProviderChatControlService
from .service import ModelRouterService, RouterServiceError


AUTO_SIDECAR_ATTEMPTS_NOT_OBSERVED = "provider_attempts_not_observed"


@dataclass(slots=True)
class ProviderChatAutoAttempt:
    attempt_id: str
    position: int
    connection_id: str | None
    provider_kind: str
    started_at: float
    dispatched: bool = False
    finalized: bool = False


@dataclass(slots=True)
class ProviderChatAutoRun:
    run_id: str
    policy_fingerprint: str
    requested_model: str
    strategy: str
    boundary_reason_codes: tuple[str, ...]
    started_at: float
    attempts: dict[int, ProviderChatAutoAttempt] = field(default_factory=dict)
    finalized: bool = False


class ProviderChatAutoAuditService:
    """Persist Auto routing evidence without observing request or response text."""

    def __init__(self, router_service: ModelRouterService) -> None:
        self.router_service = router_service
        self.repository = router_service.repository
        self.control = ProviderChatControlService(router_service)

    def enabled(self) -> bool:
        if not self.control.feature_enabled():
            return False
        return self.control.get_policy().auto_enabled

    def begin(
        self,
        requested_model: str,
        *,
        strategy: str,
        sidecar_boundary: bool = False,
    ) -> ProviderChatAutoRun | None:
        if not self.control.feature_enabled():
            return None
        policy = self.control.get_policy()
        if not policy.auto_enabled:
            return None
        run = ProviderChatAutoRun(
            run_id=f"chatrun_{uuid.uuid4().hex}",
            policy_fingerprint=policy.policy_fingerprint,
            requested_model=str(requested_model or "").strip(),
            strategy=strategy,
            boundary_reason_codes=(
                (AUTO_SIDECAR_ATTEMPTS_NOT_OBSERVED,)
                if sidecar_boundary
                else ()
            ),
            started_at=time.perf_counter(),
        )
        self._repository_method("claim_chat_control_run")(
            self.router_service.tenant_id,
            run_id=run.run_id,
            policy_fingerprint=run.policy_fingerprint,
            capability="chat_text",
            requested_model=run.requested_model,
            strategy=run.strategy,
            gateway="auto",
            epoch_id=None,
            is_real_user=True,
            primary_newapi=False,
        )
        return run

    def claim_attempt(
        self,
        run: ProviderChatAutoRun,
        *,
        position: int,
        connection_id: str | None,
        provider_kind: str,
    ) -> ProviderChatAutoAttempt:
        if run.finalized:
            raise RouterServiceError(
                "provider_chat_auto_run_finalized",
                "Auto 审计运行已结束，不能再派发新的 Provider 尝试。",
                status_code=409,
            )
        existing = run.attempts.get(position)
        if existing is not None:
            return existing
        attempt = ProviderChatAutoAttempt(
            attempt_id=f"chatattempt_{uuid.uuid4().hex}",
            position=position,
            connection_id=connection_id,
            provider_kind=provider_kind,
            started_at=time.perf_counter(),
        )
        self._repository_method("claim_chat_control_attempt")(
            self.router_service.tenant_id,
            attempt_id=attempt.attempt_id,
            run_id=run.run_id,
            capability="chat_text",
            position=position,
            connection_id=connection_id,
            provider_kind=provider_kind,
        )
        run.attempts[position] = attempt
        return attempt

    def mark_dispatched(
        self,
        run: ProviderChatAutoRun,
        attempt: ProviderChatAutoAttempt,
    ) -> None:
        if attempt.dispatched:
            raise RouterServiceError(
                "provider_chat_auto_duplicate_post_blocked",
                "Auto Provider 尝试已派发，禁止重复调用。",
                status_code=409,
            )
        policy = self.control.get_policy()
        if (
            not self.control.feature_enabled()
            or not policy.auto_enabled
            or policy.policy_fingerprint != run.policy_fingerprint
        ):
            code = "provider_chat_auto_policy_changed"
            self.complete_attempt(
                run,
                attempt,
                status="failed",
                result_class="preflight_failure",
                error_code=code,
            )
            self.complete_run(
                run,
                status="failed",
                result_class="preflight_failure",
                reason_codes=[code],
            )
            raise RouterServiceError(
                code,
                "Auto 迁移门禁或控制策略已变化，请刷新后重试。",
                status_code=409,
            )
        self._repository_method("mark_chat_control_attempt_dispatched")(
            self.router_service.tenant_id,
            attempt.attempt_id,
        )
        attempt.dispatched = True

    def complete_attempt(
        self,
        run: ProviderChatAutoRun,
        attempt: ProviderChatAutoAttempt,
        *,
        status: str,
        result_class: str,
        error_code: str | None = None,
        actual_model: str | None = None,
        ttft_ms: float | None = None,
        e2e_ms: float | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
    ) -> None:
        if attempt.finalized:
            return
        self._repository_method("complete_chat_control_attempt")(
            self.router_service.tenant_id,
            attempt.attempt_id,
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
        attempt.finalized = True

    def complete_run(
        self,
        run: ProviderChatAutoRun,
        *,
        status: str,
        result_class: str,
        reason_codes: list[str] | None = None,
        actual_model: str | None = None,
        client_cancelled: bool = False,
        hard_failure: bool = False,
        ttft_ms: float | None = None,
        e2e_ms: float | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
    ) -> None:
        if run.finalized:
            return
        codes = list(run.boundary_reason_codes)
        codes.extend(reason_codes or [])
        self._repository_method("complete_chat_control_run")(
            self.router_service.tenant_id,
            run.run_id,
            status=status,
            result_class=result_class,
            reason_codes=list(dict.fromkeys(codes)),
            actual_model=actual_model,
            client_cancelled=client_cancelled,
            hard_failure=hard_failure,
            ttft_ms=ttft_ms,
            e2e_ms=e2e_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )
        run.finalized = True

    @staticmethod
    def classify_http_failure(status_code: int) -> tuple[str, str, bool]:
        code = f"provider_chat_http_{status_code}"
        if status_code in {401, 402, 403, 404}:
            return "hard_failure", code, True
        if status_code == 429 or status_code >= 500:
            return "transient_failure", code, False
        return "request_failure", code, False

    def _repository_method(self, name: str):
        method = getattr(self.repository, name, None)
        if not callable(method):
            raise RouterServiceError(
                "provider_chat_control_storage_unavailable",
                "当前 Router 存储不支持 Auto 调用审计。",
                status_code=503,
            )
        return method
