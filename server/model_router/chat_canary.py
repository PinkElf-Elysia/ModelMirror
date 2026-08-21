from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .provider_chat import (
    PROVIDER_CHAT_CONTRACT_VERSION,
    ProviderChatEndpointResolver,
    ProviderChatTarget,
)
from .repository import (
    RouterConnectionNotFound,
    RouterCredentialUnavailable,
)
from .schemas import (
    ProviderChatCanaryAdminResponse,
    ProviderChatCanaryAggregate,
    ProviderChatCanaryConnectionStatus,
    ProviderChatCanaryModelStatus,
    ProviderChatCanaryPublicStatus,
    ProviderChatCanaryRunSummary,
    RouterConnection,
)
from .service import ModelRouterService, RouterServiceError


PROVIDER_CHAT_CANARY_ENABLED_ENV = "MODEL_MIRROR_PROVIDER_CHAT_CANARY_ENABLED"
PROVIDER_CHAT_CERTIFICATION_MAX_AGE_ENV = (
    "MODEL_MIRROR_PROVIDER_CHAT_CERTIFICATION_MAX_AGE_SECONDS"
)
PROVIDER_CHAT_CANARY_CONTRACT_VERSION = "modelmirror-provider-chat-canary-v1"
PROVIDER_CHAT_CANARY_CONSENT_REVISION = "provider-chat-canary-consent-v1"
DEFAULT_PROVIDER_CHAT_CERTIFICATION_MAX_AGE_SECONDS = 24 * 60 * 60
MIN_PROVIDER_CHAT_CERTIFICATION_MAX_AGE_SECONDS = 5 * 60
MAX_PROVIDER_CHAT_CERTIFICATION_MAX_AGE_SECONDS = 30 * 24 * 60 * 60

_HARD_FAILURE_CODES = {
    "provider_chat_http_401",
    "provider_chat_http_402",
    "provider_chat_http_403",
    "provider_chat_http_404",
    "provider_chat_invalid_sse",
    "provider_chat_empty_stream",
    "provider_chat_missing_terminal",
}
_TRANSIENT_RESULT_CLASS = "transient_failure"


@dataclass(frozen=True, slots=True)
class ProviderChatCanaryEligibility:
    available: bool
    reason_code: str
    connection: RouterConnection | None = None
    certification: dict[str, object] | None = None
    connection_fingerprint: str | None = None
    paused: bool = False
    pause_reason: str | None = None
    baseline_overlap: bool = False


@dataclass(frozen=True, slots=True)
class ProviderChatCanaryDispatch:
    run_id: str
    target: ProviderChatTarget
    eligibility: ProviderChatCanaryEligibility


@dataclass(slots=True)
class ProviderChatCanaryStreamEvidence:
    started_at: float
    buffer: str = ""
    invalid: bool = False
    content_observed: bool = False
    terminal_observed: bool = False
    actual_model: str | None = None
    finish_reason: str | None = None
    ttft_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None

    def feed(self, value: str) -> None:
        self.buffer += value.replace("\r\n", "\n").replace("\r", "\n")
        while "\n\n" in self.buffer:
            event, self.buffer = self.buffer.split("\n\n", 1)
            self._consume_event(event)

    def finish(
        self,
        *,
        transport_completed: bool,
        transport_error_code: str | None = None,
    ) -> tuple[str, str, str | None, dict[str, bool], list[str]]:
        if self.buffer.strip():
            self._consume_event(self.buffer)
            self.buffer = ""
        checks = {
            "chat_http_ok": True,
            "text_delta_observed": self.content_observed,
            "stream_completed": transport_completed,
            "terminal_observed": self.terminal_observed,
        }
        warnings: list[str] = []
        if self.actual_model is None:
            warnings.append("actual_model_missing")
        if self.total_tokens is None:
            warnings.append("usage_missing")
        if self.finish_reason == "length":
            warnings.append("finish_reason_length")
        if transport_error_code is not None:
            result_class = (
                "client_cancelled"
                if transport_error_code == "provider_chat_client_cancelled"
                else "transient_failure"
            )
            status = "cancelled" if result_class == "client_cancelled" else "failed"
            return status, result_class, transport_error_code, checks, warnings
        if self.invalid:
            return (
                "failed",
                "hard_failure",
                "provider_chat_invalid_sse",
                checks,
                warnings,
            )
        if not self.content_observed:
            return (
                "failed",
                "hard_failure",
                "provider_chat_empty_stream",
                checks,
                warnings,
            )
        if not self.terminal_observed:
            return (
                "failed",
                "hard_failure",
                "provider_chat_missing_terminal",
                checks,
                warnings,
            )
        return "succeeded", "success", None, checks, warnings

    def _consume_event(self, event: str) -> None:
        data_lines = []
        for line in event.split("\n"):
            if not line or line.startswith(":"):
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if not data_lines:
            return
        data = "\n".join(data_lines)
        if data == "[DONE]":
            self.terminal_observed = True
            return
        try:
            payload = json.loads(data)
        except (json.JSONDecodeError, TypeError):
            self.invalid = True
            return
        if not isinstance(payload, dict):
            self.invalid = True
            return
        model = payload.get("model")
        if isinstance(model, str) and model:
            self.actual_model = model
        usage = payload.get("usage")
        if isinstance(usage, dict):
            self.prompt_tokens = self._integer(usage.get("prompt_tokens"))
            self.completion_tokens = self._integer(usage.get("completion_tokens"))
            self.total_tokens = self._integer(usage.get("total_tokens"))
        choices = payload.get("choices")
        if not isinstance(choices, list):
            return
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            finish_reason = choice.get("finish_reason")
            if isinstance(finish_reason, str) and finish_reason:
                self.finish_reason = finish_reason
                self.terminal_observed = True
            for container_name in ("delta", "message"):
                container = choice.get(container_name)
                if not isinstance(container, dict):
                    continue
                if self._has_text(container.get("content")):
                    self.content_observed = True
                    if self.ttft_ms is None:
                        self.ttft_ms = (time.perf_counter() - self.started_at) * 1000

    @classmethod
    def _has_text(cls, value: object) -> bool:
        if isinstance(value, str):
            return bool(value)
        if isinstance(value, list):
            return any(cls._has_text(item) for item in value)
        if isinstance(value, dict):
            text = value.get("text")
            return isinstance(text, str) and bool(text)
        return False

    @staticmethod
    def _integer(value: object) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None


class ProviderChatCanaryService:
    def __init__(self, router_service: ModelRouterService) -> None:
        self.router_service = router_service
        self.repository = router_service.repository

    @staticmethod
    def enabled() -> bool:
        value = os.getenv(PROVIDER_CHAT_CANARY_ENABLED_ENV, "false")
        return value.strip().casefold() not in {"0", "false", "no", "off"}

    def public_status(
        self,
        model_id: str,
        *,
        default_gateway_url: str | None = None,
    ) -> ProviderChatCanaryPublicStatus:
        clean_model = self._model_id(model_id)
        eligibility = self.eligibility(
            clean_model,
            default_gateway_url=default_gateway_url,
        )
        return ProviderChatCanaryPublicStatus(
            contract_version=PROVIDER_CHAT_CANARY_CONTRACT_VERSION,
            feature_enabled=self.enabled(),
            available=eligibility.available,
            model_id=clean_model,
            reason_code=eligibility.reason_code,
            consent_revision=PROVIDER_CHAT_CANARY_CONSENT_REVISION,
        )

    def admin_status(
        self,
        *,
        limit: int = 50,
        default_gateway_url: str | None = None,
    ) -> ProviderChatCanaryAdminResponse:
        tenant_id = self.router_service.tenant_id
        policy = self._repository_method("get_chat_canary_policy")(tenant_id)
        selected_connection_id = (
            str(policy["connection_id"]) if policy is not None else None
        )
        certifications = self._repository_method(
            "list_latest_chat_certifications_by_model"
        )(tenant_id)
        rows_by_connection: dict[str, list[dict[str, object]]] = {}
        latest_certifications: dict[tuple[str, str], dict[str, object]] = {}
        for row in certifications:
            connection_id = str(row["connection_id"])
            model_id = str(row["requested_model"])
            rows_by_connection.setdefault(connection_id, []).append(row)
            latest_certifications[(connection_id, model_id)] = row
        all_connections = self.router_service.list_connections()
        connections_by_id = {connection.id: connection for connection in all_connections}
        connections = [
            self._connection_status(
                connection,
                rows_by_connection.get(connection.id, []),
                selected=connection.id == selected_connection_id,
                policy_enabled=bool(policy and policy["enabled"]),
                default_gateway_url=default_gateway_url,
            )
            for connection in all_connections
            if connection.kind == "newapi"
        ]
        run_rows = self._repository_method("list_chat_canary_runs")(
            tenant_id,
            limit=max(1, min(int(limit), 100)),
        )
        summarized_runs: list[
            tuple[dict[str, object], ProviderChatCanaryRunSummary]
        ] = []
        for row in run_rows:
            current_evidence, stale_reason = self._run_currency(
                row,
                connections_by_id=connections_by_id,
                latest_certifications=latest_certifications,
            )
            summarized_runs.append(
                (
                    row,
                    self._run_summary(
                        row,
                        current_evidence=current_evidence,
                        stale_reason=stale_reason,
                    ),
                )
            )
        certification_max_age, _ = self._certification_max_age_seconds()
        return ProviderChatCanaryAdminResponse(
            contract_version=PROVIDER_CHAT_CANARY_CONTRACT_VERSION,
            feature_enabled=self.enabled(),
            policy_enabled=bool(policy and policy["enabled"]),
            selected_connection_id=selected_connection_id,
            consent_revision=PROVIDER_CHAT_CANARY_CONSENT_REVISION,
            certification_max_age_seconds=certification_max_age,
            connections=connections,
            runs=[summary for _, summary in summarized_runs],
            aggregates=self._aggregate_current_runs(summarized_runs),
        )

    def update_policy(
        self,
        connection_id: str,
        *,
        enabled: bool,
        default_gateway_url: str | None = None,
    ) -> ProviderChatCanaryAdminResponse:
        tenant_id = self.router_service.tenant_id
        connection = self.repository.get_connection(tenant_id, connection_id)
        if connection.kind != "newapi":
            raise RouterServiceError(
                "provider_chat_canary_newapi_only",
                "Chat 试运行首期仅支持 newAPI 连接。",
                status_code=409,
            )
        if enabled:
            if not self.enabled():
                raise RouterServiceError(
                    "provider_chat_canary_disabled",
                    "Provider Chat 试运行已由部署配置关闭。",
                    status_code=503,
                )
            reason = self._connection_reason(connection, require_credential=True)
            if reason is not None:
                raise self._eligibility_error(reason)
            certifications = self._repository_method(
                "list_latest_chat_certifications_by_model"
            )(tenant_id, connection_id=connection_id)
            current_fingerprint = self._fingerprint(connection.id)
            current_certifications = [
                row
                for row in certifications
                if (
                    row["status"] == "passed"
                    and row["connection_fingerprint"] == current_fingerprint
                    and row["contract_version"] == PROVIDER_CHAT_CONTRACT_VERSION
                )
            ]
            if not current_certifications:
                raise self._eligibility_error("certification_required")
            time_reasons = [
                self._certification_time_status(row)[0]
                for row in current_certifications
            ]
            if all(reason is not None for reason in time_reasons):
                raise self._eligibility_error(
                    next(reason for reason in time_reasons if reason is not None)
                )
        self._repository_method("save_chat_canary_policy")(
            tenant_id,
            connection_id=connection_id,
            enabled=enabled,
        )
        return self.admin_status(default_gateway_url=default_gateway_url)

    def eligibility(
        self,
        model_id: str,
        *,
        default_gateway_url: str | None = None,
    ) -> ProviderChatCanaryEligibility:
        tenant_id = self.router_service.tenant_id
        if not self.enabled():
            return ProviderChatCanaryEligibility(False, "feature_disabled")
        policy = self._repository_method("get_chat_canary_policy")(tenant_id)
        if policy is None:
            return ProviderChatCanaryEligibility(False, "policy_not_configured")
        if not policy["enabled"]:
            return ProviderChatCanaryEligibility(False, "policy_disabled")
        try:
            connection = self.repository.get_connection(
                tenant_id, str(policy["connection_id"])
            )
        except RouterConnectionNotFound:
            return ProviderChatCanaryEligibility(False, "connection_unavailable")
        reason = self._connection_reason(connection, require_credential=True)
        if reason is not None:
            return ProviderChatCanaryEligibility(False, reason, connection=connection)
        fingerprint = self._fingerprint(connection.id)
        certification = self._repository_method("get_latest_chat_certification")(
            tenant_id,
            connection.id,
            self._model_id(model_id),
        )
        if certification is None:
            return ProviderChatCanaryEligibility(
                False,
                "certification_required",
                connection=connection,
                connection_fingerprint=fingerprint,
            )
        if certification["connection_fingerprint"] != fingerprint:
            return ProviderChatCanaryEligibility(
                False,
                "certification_stale",
                connection=connection,
                certification=certification,
                connection_fingerprint=fingerprint,
            )
        if certification["contract_version"] != PROVIDER_CHAT_CONTRACT_VERSION:
            return ProviderChatCanaryEligibility(
                False,
                "certification_contract_stale",
                connection=connection,
                certification=certification,
                connection_fingerprint=fingerprint,
            )
        if certification["status"] != "passed":
            return ProviderChatCanaryEligibility(
                False,
                "certification_not_passed",
                connection=connection,
                certification=certification,
                connection_fingerprint=fingerprint,
            )
        certification_time_reason, _ = self._certification_time_status(certification)
        if certification_time_reason is not None:
            return ProviderChatCanaryEligibility(
                False,
                certification_time_reason,
                connection=connection,
                certification=certification,
                connection_fingerprint=fingerprint,
            )
        pause_reason = self._pause_reason(connection.id, model_id, certification)
        overlap = self._baseline_overlap(connection.base_url, default_gateway_url)
        if pause_reason is not None:
            return ProviderChatCanaryEligibility(
                False,
                "automatically_paused",
                connection=connection,
                certification=certification,
                connection_fingerprint=fingerprint,
                paused=True,
                pause_reason=pause_reason,
                baseline_overlap=overlap,
            )
        return ProviderChatCanaryEligibility(
            True,
            "available",
            connection=connection,
            certification=certification,
            connection_fingerprint=fingerprint,
            baseline_overlap=overlap,
        )

    def session_hash(self, session_id: str) -> str:
        clean_session = str(session_id or "").strip()
        if not clean_session or len(clean_session) > 200:
            raise RouterServiceError(
                "provider_chat_canary_invalid_session",
                "newAPI 试运行需要有效的页面会话标识。",
                status_code=422,
            )
        material = f"{self.router_service.tenant_id}:{clean_session}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def preflight_context(
        self,
        model_id: str,
        *,
        reason_code: str,
        default_gateway_url: str | None = None,
    ) -> ProviderChatCanaryEligibility:
        """Enrich a failed preflight for redacted evidence without changing it."""
        tenant_id = self.router_service.tenant_id
        policy = self._repository_method("get_chat_canary_policy")(tenant_id)
        if policy is None:
            return ProviderChatCanaryEligibility(False, reason_code)
        try:
            connection = self.repository.get_connection(
                tenant_id, str(policy["connection_id"])
            )
            fingerprint = self._fingerprint(connection.id)
        except (RouterConnectionNotFound, RouterCredentialUnavailable):
            return ProviderChatCanaryEligibility(False, reason_code)
        certification = self._repository_method("get_latest_chat_certification")(
            tenant_id,
            connection.id,
            self._model_id(model_id),
        )
        return ProviderChatCanaryEligibility(
            False,
            reason_code,
            connection=connection,
            certification=certification,
            connection_fingerprint=fingerprint,
            baseline_overlap=self._baseline_overlap(
                connection.base_url, default_gateway_url
            ),
        )

    def begin_run(
        self,
        model_id: str,
        *,
        session_id: str,
        default_gateway_url: str | None = None,
    ) -> ProviderChatCanaryDispatch:
        clean_model = self._model_id(model_id)
        eligibility = self.eligibility(
            clean_model,
            default_gateway_url=default_gateway_url,
        )
        if (
            not eligibility.available
            or eligibility.connection is None
            or eligibility.certification is None
            or eligibility.connection_fingerprint is None
        ):
            raise self._eligibility_error(eligibility.reason_code)
        try:
            api_key = self.repository.resolve_api_key(
                self.router_service.tenant_id,
                eligibility.connection.id,
            )
        except RouterCredentialUnavailable as exc:
            raise self._eligibility_error("credential_unavailable") from exc
        target = ProviderChatTarget.create(
            source="managed",
            provider_kind=eligibility.connection.kind,
            base_url=eligibility.connection.base_url,
            api_key=api_key,
            connection_id=eligibility.connection.id,
        )
        run_id = f"chatcanary_{os.urandom(16).hex()}"
        self._repository_method("claim_chat_canary_run")(
            self.router_service.tenant_id,
            run_id=run_id,
            connection_id=eligibility.connection.id,
            connection_fingerprint=eligibility.connection_fingerprint,
            certification_id=str(eligibility.certification["id"]),
            contract_version=PROVIDER_CHAT_CANARY_CONTRACT_VERSION,
            requested_model=clean_model,
            session_id_hash=self.session_hash(session_id),
            baseline_overlap=eligibility.baseline_overlap,
        )
        return ProviderChatCanaryDispatch(
            run_id=run_id,
            target=target,
            eligibility=eligibility,
        )

    def record_preflight_fallback(
        self,
        model_id: str,
        *,
        session_id: str,
        eligibility: ProviderChatCanaryEligibility,
    ) -> None:
        if (
            eligibility.connection is None
            or eligibility.connection_fingerprint is None
        ):
            return
        run_id = f"chatcanary_{os.urandom(16).hex()}"
        self._repository_method("claim_chat_canary_run")(
            self.router_service.tenant_id,
            run_id=run_id,
            connection_id=eligibility.connection.id,
            connection_fingerprint=eligibility.connection_fingerprint,
            certification_id=(
                str(eligibility.certification["id"])
                if eligibility.certification is not None
                else None
            ),
            contract_version=PROVIDER_CHAT_CANARY_CONTRACT_VERSION,
            requested_model=self._model_id(model_id),
            session_id_hash=self.session_hash(session_id),
            baseline_overlap=eligibility.baseline_overlap,
        )
        self._repository_method("complete_chat_canary_run")(
            self.router_service.tenant_id,
            run_id,
            status="preflight_fallback",
            result_class="preflight_fallback",
            checks={},
            warning_codes=[],
            error_code=eligibility.reason_code,
        )

    def mark_dispatched(self, run_id: str) -> None:
        self._repository_method("mark_chat_canary_dispatched")(
            self.router_service.tenant_id, run_id
        )

    def complete_run(
        self,
        run_id: str,
        *,
        status: str,
        result_class: str,
        error_code: str | None,
        checks: dict[str, bool],
        warning_codes: list[str],
        evidence: ProviderChatCanaryStreamEvidence | None = None,
        e2e_ms: float | None = None,
    ) -> dict[str, object]:
        return self._repository_method("complete_chat_canary_run")(
            self.router_service.tenant_id,
            run_id,
            status=status,
            result_class=result_class,
            checks=checks,
            warning_codes=warning_codes,
            error_code=error_code,
            actual_model=evidence.actual_model if evidence is not None else None,
            ttft_ms=evidence.ttft_ms if evidence is not None else None,
            e2e_ms=e2e_ms,
            prompt_tokens=evidence.prompt_tokens if evidence is not None else None,
            completion_tokens=(
                evidence.completion_tokens if evidence is not None else None
            ),
            total_tokens=evidence.total_tokens if evidence is not None else None,
        )

    @staticmethod
    def classify_http_failure(status_code: int) -> tuple[str, str]:
        code = f"provider_chat_http_{status_code}"
        if status_code in {401, 402, 403, 404}:
            return "hard_failure", code
        if status_code == 429 or status_code >= 500 or status_code == 408:
            return "transient_failure", code
        return "request_failure", code

    def _connection_status(
        self,
        connection: RouterConnection,
        certifications: list[dict[str, object]],
        *,
        selected: bool,
        policy_enabled: bool,
        default_gateway_url: str | None,
    ) -> ProviderChatCanaryConnectionStatus:
        connection_reason = self._connection_reason(
            connection, require_credential=False
        )
        models: list[ProviderChatCanaryModelStatus] = []
        for certification in certifications:
            model_id = str(certification["requested_model"])
            if selected and policy_enabled:
                eligibility = self.eligibility(
                    model_id, default_gateway_url=default_gateway_url
                )
            else:
                eligibility = self._eligibility_for_candidate(
                    connection,
                    certification,
                    default_gateway_url=default_gateway_url,
                )
            certification_status = str(certification["status"])
            certification_time_reason, certification_expires_at = (
                self._certification_time_status(certification)
            )
            if (
                certification_status != "running"
                and (
                    certification["connection_fingerprint"]
                    != self._fingerprint(connection.id)
                    or certification["contract_version"]
                    != PROVIDER_CHAT_CONTRACT_VERSION
                    or certification_time_reason is not None
                )
            ):
                certification_status = "stale"
            models.append(
                ProviderChatCanaryModelStatus(
                    model_id=model_id,
                    certification_id=str(certification["id"]),
                    certification_status=certification_status,
                    available=eligibility.available and selected and policy_enabled,
                    reason_code=eligibility.reason_code,
                    paused=eligibility.paused,
                    pause_reason=eligibility.pause_reason,
                    baseline_overlap=eligibility.baseline_overlap,
                    completed_at=(
                        str(certification["completed_at"])
                        if certification["completed_at"]
                        else None
                    ),
                    certification_expires_at=certification_expires_at,
                )
            )
        return ProviderChatCanaryConnectionStatus(
            connection_id=connection.id,
            connection_name=connection.name,
            eligible_connection=connection_reason is None,
            reason_code=connection_reason or "available",
            models=models,
        )

    def _eligibility_for_candidate(
        self,
        connection: RouterConnection,
        certification: dict[str, object],
        *,
        default_gateway_url: str | None,
    ) -> ProviderChatCanaryEligibility:
        reason = self._connection_reason(connection, require_credential=False)
        if reason is not None:
            return ProviderChatCanaryEligibility(False, reason, connection=connection)
        fingerprint = self._fingerprint(connection.id)
        if certification["connection_fingerprint"] != fingerprint:
            return ProviderChatCanaryEligibility(False, "certification_stale")
        if certification["contract_version"] != PROVIDER_CHAT_CONTRACT_VERSION:
            return ProviderChatCanaryEligibility(
                False, "certification_contract_stale"
            )
        if certification["status"] != "passed":
            return ProviderChatCanaryEligibility(False, "certification_not_passed")
        certification_time_reason, _ = self._certification_time_status(certification)
        if certification_time_reason is not None:
            return ProviderChatCanaryEligibility(False, certification_time_reason)
        pause_reason = self._pause_reason(
            connection.id,
            str(certification["requested_model"]),
            certification,
        )
        overlap = self._baseline_overlap(connection.base_url, default_gateway_url)
        return ProviderChatCanaryEligibility(
            pause_reason is None,
            "available" if pause_reason is None else "automatically_paused",
            connection=connection,
            certification=certification,
            connection_fingerprint=fingerprint,
            paused=pause_reason is not None,
            pause_reason=pause_reason,
            baseline_overlap=overlap,
        )

    def _connection_reason(
        self, connection: RouterConnection, *, require_credential: bool
    ) -> str | None:
        if connection.kind != "newapi":
            return "provider_chat_canary_newapi_only"
        if not connection.enabled:
            return "connection_disabled"
        if "chat" not in connection.scopes:
            return "connection_chat_scope_required"
        if connection.health != "online" or not connection.last_checked_at:
            return "connection_unavailable"
        if require_credential:
            try:
                self.repository.resolve_api_key(
                    self.router_service.tenant_id, connection.id
                )
            except RouterCredentialUnavailable:
                return "credential_unavailable"
        return None

    def _pause_reason(
        self,
        connection_id: str,
        model_id: str,
        certification: dict[str, object],
    ) -> str | None:
        rows = self._repository_method("list_chat_canary_runs")(
            self.router_service.tenant_id,
            connection_id=connection_id,
            requested_model=model_id,
            certification_id=str(certification["id"]),
            limit=100,
        )
        transient_count = 0
        for row in rows:
            result_class = str(row["result_class"] or "")
            error_code = str(row["error_code"] or "")
            if result_class == "success":
                return None
            if error_code in _HARD_FAILURE_CODES or result_class == "hard_failure":
                return error_code or "provider_chat_hard_failure"
            if result_class == _TRANSIENT_RESULT_CLASS:
                transient_count += 1
                if transient_count >= 3:
                    return "provider_chat_transient_failure_threshold"
        return None

    @staticmethod
    def _parse_utc_datetime(value: object) -> datetime | None:
        clean = str(value or "").strip()
        if not clean:
            return None
        try:
            parsed = datetime.fromisoformat(clean.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(UTC)

    @staticmethod
    def _certification_max_age_seconds() -> tuple[int | None, str | None]:
        raw = os.getenv(PROVIDER_CHAT_CERTIFICATION_MAX_AGE_ENV, "").strip()
        if not raw:
            return DEFAULT_PROVIDER_CHAT_CERTIFICATION_MAX_AGE_SECONDS, None
        try:
            value = int(raw)
        except ValueError:
            return None, "certification_ttl_invalid"
        if not (
            MIN_PROVIDER_CHAT_CERTIFICATION_MAX_AGE_SECONDS
            <= value
            <= MAX_PROVIDER_CHAT_CERTIFICATION_MAX_AGE_SECONDS
        ):
            return None, "certification_ttl_invalid"
        return value, None

    def _certification_time_status(
        self, certification: dict[str, object]
    ) -> tuple[str | None, str | None]:
        max_age_seconds, configuration_error = (
            self._certification_max_age_seconds()
        )
        if configuration_error is not None or max_age_seconds is None:
            return configuration_error or "certification_ttl_invalid", None
        completed_at = self._parse_utc_datetime(certification.get("completed_at"))
        if completed_at is None:
            return "certification_time_invalid", None
        expires_at = completed_at + timedelta(seconds=max_age_seconds)
        if datetime.now(UTC) >= expires_at:
            return "certification_expired", expires_at.isoformat()
        return None, expires_at.isoformat()

    def _run_currency(
        self,
        row: dict[str, object],
        *,
        connections_by_id: dict[str, RouterConnection],
        latest_certifications: dict[tuple[str, str], dict[str, object]],
    ) -> tuple[bool, str | None]:
        connection_id = str(row["connection_id"])
        model_id = str(row["requested_model"])
        connection = connections_by_id.get(connection_id)
        if connection is None:
            return False, "connection_unavailable"
        if row["connection_fingerprint"] != self._fingerprint(connection_id):
            return False, "connection_fingerprint_changed"
        if row["contract_version"] != PROVIDER_CHAT_CANARY_CONTRACT_VERSION:
            return False, "canary_contract_changed"
        certification = latest_certifications.get((connection_id, model_id))
        if certification is None:
            return False, "certification_required"
        if row["certification_id"] != certification["id"]:
            return False, "certification_window_changed"
        if certification["connection_fingerprint"] != row["connection_fingerprint"]:
            return False, "certification_stale"
        if certification["contract_version"] != PROVIDER_CHAT_CONTRACT_VERSION:
            return False, "certification_contract_stale"
        if certification["status"] != "passed":
            return False, "certification_not_passed"
        certification_time_reason, _ = self._certification_time_status(certification)
        if certification_time_reason is not None:
            return False, certification_time_reason
        return True, None

    @staticmethod
    def _aggregate_current_runs(
        summarized_runs: list[
            tuple[dict[str, object], ProviderChatCanaryRunSummary]
        ],
    ) -> list[ProviderChatCanaryAggregate]:
        groups: dict[tuple[str, str, str], list[dict[str, object]]] = {}
        for row, summary in summarized_runs:
            if not summary.current_evidence or not row["certification_id"]:
                continue
            key = (
                str(row["connection_id"]),
                str(row["requested_model"]),
                str(row["certification_id"]),
            )
            groups.setdefault(key, []).append(row)

        aggregates: list[ProviderChatCanaryAggregate] = []
        for (connection_id, model_id, certification_id), rows in sorted(
            groups.items()
        ):
            dispatched_runs = sum(bool(row["dispatched"]) for row in rows)
            succeeded_runs = sum(row["result_class"] == "success" for row in rows)
            ttft_values = [
                float(row["ttft_ms"])
                for row in rows
                if row["ttft_ms"] is not None
            ]
            e2e_values = [
                float(row["e2e_ms"])
                for row in rows
                if row["e2e_ms"] is not None
            ]
            completed_values = [
                str(row["completed_at"])
                for row in rows
                if row["completed_at"]
            ]
            aggregates.append(
                ProviderChatCanaryAggregate(
                    connection_id=connection_id,
                    model_id=model_id,
                    certification_id=certification_id,
                    total_runs=len(rows),
                    dispatched_runs=dispatched_runs,
                    succeeded_runs=succeeded_runs,
                    hard_failure_runs=sum(
                        row["result_class"] == "hard_failure" for row in rows
                    ),
                    transient_failure_runs=sum(
                        row["result_class"] == "transient_failure" for row in rows
                    ),
                    request_failure_runs=sum(
                        row["result_class"] == "request_failure" for row in rows
                    ),
                    cancelled_runs=sum(row["status"] == "cancelled" for row in rows),
                    uncertain_runs=sum(row["status"] == "uncertain" for row in rows),
                    preflight_fallback_runs=sum(
                        row["status"] == "preflight_fallback" for row in rows
                    ),
                    success_rate=(
                        round(succeeded_runs / dispatched_runs, 4)
                        if dispatched_runs
                        else None
                    ),
                    average_ttft_ms=(
                        round(sum(ttft_values) / len(ttft_values), 2)
                        if ttft_values
                        else None
                    ),
                    average_e2e_ms=(
                        round(sum(e2e_values) / len(e2e_values), 2)
                        if e2e_values
                        else None
                    ),
                    total_tokens=sum(
                        int(row["total_tokens"] or 0) for row in rows
                    ),
                    baseline_overlap=any(bool(row["baseline_overlap"]) for row in rows),
                    last_completed_at=(max(completed_values) if completed_values else None),
                )
            )
        return aggregates

    @staticmethod
    def _baseline_overlap(
        connection_url: str, default_gateway_url: str | None
    ) -> bool:
        if not default_gateway_url:
            return False
        try:
            managed = ProviderChatEndpointResolver.resolve(connection_url)
            default = ProviderChatEndpointResolver.resolve(default_gateway_url)
        except ValueError:
            return False
        return managed.chat_completions_url == default.chat_completions_url

    def _fingerprint(self, connection_id: str) -> str:
        return str(
            self._repository_method("connection_config_fingerprint")(
                self.router_service.tenant_id, connection_id
            )
        )

    def _repository_method(self, name: str):
        method = getattr(self.repository, name, None)
        if not callable(method):
            raise RouterServiceError(
                "provider_chat_canary_storage_unavailable",
                "当前 Router 存储不支持 Chat 试运行。",
                status_code=503,
            )
        return method

    @staticmethod
    def _model_id(value: str) -> str:
        clean = str(value or "").strip()
        if not clean or len(clean) > 512:
            raise RouterServiceError(
                "invalid_model_id",
                "model_id 必须是 1 至 512 个字符。",
                status_code=422,
            )
        return clean

    @staticmethod
    def _eligibility_error(reason: str) -> RouterServiceError:
        return RouterServiceError(
            reason,
            "当前连接尚不满足 newAPI Chat 试运行条件。",
            status_code=409,
        )

    @staticmethod
    def _run_summary(
        row: dict[str, object],
        *,
        current_evidence: bool = False,
        stale_reason: str | None = None,
    ) -> ProviderChatCanaryRunSummary:
        return ProviderChatCanaryRunSummary(
            run_id=str(row["id"]),
            connection_id=str(row["connection_id"]),
            model_id=str(row["requested_model"]),
            status=str(row["status"]),
            dispatched=bool(row["dispatched"]),
            result_class=str(row["result_class"]) if row["result_class"] else None,
            error_code=str(row["error_code"]) if row["error_code"] else None,
            checks=json.loads(str(row["checks_json"] or "{}")),
            warning_codes=[
                str(item) for item in json.loads(str(row["warnings_json"] or "[]"))
            ],
            ttft_ms=float(row["ttft_ms"]) if row["ttft_ms"] is not None else None,
            e2e_ms=float(row["e2e_ms"]) if row["e2e_ms"] is not None else None,
            prompt_tokens=(
                int(row["prompt_tokens"])
                if row["prompt_tokens"] is not None
                else None
            ),
            completion_tokens=(
                int(row["completion_tokens"])
                if row["completion_tokens"] is not None
                else None
            ),
            total_tokens=(
                int(row["total_tokens"])
                if row["total_tokens"] is not None
                else None
            ),
            baseline_overlap=bool(row["baseline_overlap"]),
            current_evidence=current_evidence,
            stale_reason=stale_reason,
            created_at=str(row["created_at"]),
            completed_at=str(row["completed_at"]) if row["completed_at"] else None,
        )
