from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from .chat_gate import (
    MIN_PROVIDER_CHAT_GATE_DAYS,
    MIN_PROVIDER_CHAT_GATE_MODEL_SUCCESSES,
    MIN_PROVIDER_CHAT_GATE_REQUESTS,
    MIN_PROVIDER_CHAT_GATE_SUCCESS_RATE,
    REQUIRED_PROVIDER_CHAT_DRILLS,
    evaluate_provider_chat_gate,
    validate_provider_chat_drills,
)
from .chat_canary import (
    DEFAULT_PROVIDER_CHAT_CERTIFICATION_MAX_AGE_SECONDS,
    MAX_PROVIDER_CHAT_CERTIFICATION_MAX_AGE_SECONDS,
    MIN_PROVIDER_CHAT_CERTIFICATION_MAX_AGE_SECONDS,
    PROVIDER_CHAT_CERTIFICATION_MAX_AGE_ENV,
)
from .provider_chat import PROVIDER_CHAT_CONTRACT_VERSION
from .repository import RouterCredentialUnavailable, RouterRepositoryError
from .schemas import (
    ProviderChatCapability,
    ProviderChatControlAttemptSummary,
    ProviderChatGateEvidenceSummary,
    ProviderChatGateModelProgress,
    ProviderChatControlGateResponse,
    ProviderChatControlPolicyResponse,
    ProviderChatControlPolicyUpdate,
    ProviderChatControlPublicStatus,
    ProviderChatControlReceiptsResponse,
    ProviderChatControlRouteSummary,
    ProviderChatControlRunSummary,
    ProviderChatQualificationSummary,
    ProviderChatRequiredActivationRequest,
)
from .service import ModelRouterService, RouterServiceError


PROVIDER_CHAT_ROUTING_CONTRACT_VERSION = "modelmirror-provider-chat-routing-v1"
MODEL_CONTROL_CHAT_ENABLED_ENV = "MODEL_CONTROL_CHAT_ENABLED"
CHAT_CONTROL_CAPABILITIES: tuple[ProviderChatCapability, ...] = (
    "chat_text",
    "chat_tools",
    "chat_file_output",
)


class ProviderChatControlService:
    """Managed Chat policy, qualification, gate, and receipt foundation."""

    def __init__(self, router_service: ModelRouterService) -> None:
        self.router_service = router_service
        self.repository = router_service.repository

    @staticmethod
    def feature_enabled() -> bool:
        value = os.getenv(MODEL_CONTROL_CHAT_ENABLED_ENV, "false")
        return value.strip().casefold() not in {"", "0", "false", "no", "off"}

    def get_policy(self) -> ProviderChatControlPolicyResponse:
        bundle = self._repository_method("get_chat_control_policy_bundle")(
            self.router_service.tenant_id
        )
        response = self._policy_response(bundle)
        self._sync_gate_epoch(response)
        return response

    def update_policy(
        self, payload: ProviderChatControlPolicyUpdate
    ) -> ProviderChatControlPolicyResponse:
        if payload.mode == "newapi_required_default":
            raise RouterServiceError(
                "provider_chat_required_activation_not_available",
                "强制默认只能通过 required Go/No-Go 门禁人工激活。",
                status_code=409,
            )

        current_policy = self.get_policy()
        stable_models = sorted(set(payload.stable_model_ids))
        routes_by_capability = {
            route.capability: list(route.connection_ids) for route in payload.routes
        }
        for capability in CHAT_CONTROL_CAPABILITIES:
            routes_by_capability.setdefault(capability, [])

        text_routes = routes_by_capability["chat_text"]
        if stable_models and not text_routes:
            raise RouterServiceError(
                "provider_chat_text_route_required",
                "稳定模型允许列表不为空时必须配置普通文本路由。",
                status_code=422,
            )
        if payload.mode == "newapi_preferred" and not stable_models:
            raise RouterServiceError(
                "provider_chat_stable_models_required",
                "启用 newAPI 首选前必须选择至少一个稳定模型。",
                status_code=422,
            )

        normalized_routes: list[dict[str, object]] = [
            {
                "capability": capability,
                "position": position,
                "connection_id": connection_id,
            }
            for capability in CHAT_CONTROL_CAPABILITIES
            for position, connection_id in enumerate(routes_by_capability[capability])
        ]
        rollback_only = (
            current_policy.configured_mode == "newapi_required_default"
            and payload.mode == "newapi_preferred"
        )
        if rollback_only:
            current_routes = {
                route.capability: list(route.connection_ids)
                for route in current_policy.routes
            }
            if (
                stable_models != current_policy.stable_model_ids
                or bool(payload.auto_enabled) != current_policy.auto_enabled
                or any(
                    routes_by_capability[capability]
                    != current_routes.get(capability, [])
                    for capability in CHAT_CONTROL_CAPABILITIES
                )
            ):
                raise RouterServiceError(
                    "provider_chat_required_rollback_scope_change_not_allowed",
                    "从 required 回退 preferred 时只能改变模式；其他策略变更需在回退后单独保存。",
                    status_code=409,
                )
            bundle = self._repository_method("get_chat_control_policy_bundle")(
                self.router_service.tenant_id
            )
            qualifications = [
                {
                    "capability": str(item["capability"]),
                    "connection_id": str(item["connection_id"]),
                    "model_id": str(item["model_id"]),
                    "certification_id": str(item["certification_id"]),
                    "connection_fingerprint": str(item["connection_fingerprint"]),
                    "contract_version": str(item["contract_version"]),
                }
                for item in list(bundle.get("qualifications") or [])
            ]
        else:
            connections = {}
            for route in normalized_routes:
                connection_id = str(route["connection_id"])
                connection = self.repository.get_connection(
                    self.router_service.tenant_id, connection_id
                )
                self._validate_route_connection(connection)
                connections[connection_id] = connection

            if text_routes:
                primary = connections[text_routes[0]]
                if primary.kind != "newapi":
                    raise RouterServiceError(
                        "provider_chat_text_primary_newapi_required",
                        "普通文本路由的第一个目标必须是 newAPI。",
                        status_code=422,
                    )

            qualifications = []
            for route in normalized_routes:
                capability = str(route["capability"])
                connection_id = str(route["connection_id"])
                for model_id in stable_models:
                    qualification, reason = self._current_qualification(
                        connection_id=connection_id,
                        model_id=model_id,
                        capability=capability,
                    )
                    if qualification is None:
                        raise RouterServiceError(
                            reason,
                            self._qualification_hint(reason, model_id),
                            status_code=409,
                        )
                    qualifications.append(qualification)

        fingerprint = self._fingerprint(
            mode=payload.mode,
            auto_enabled=payload.auto_enabled,
            stable_models=stable_models,
            routes=normalized_routes,
            qualifications=qualifications,
        )
        try:
            bundle = self._repository_method("replace_chat_control_policy")(
                self.router_service.tenant_id,
                expected_revision=payload.expected_revision,
                mode=payload.mode,
                auto_enabled=payload.auto_enabled,
                policy_fingerprint=fingerprint,
                stable_model_ids=stable_models,
                routes=normalized_routes,
                qualifications=qualifications,
            )
        except RouterRepositoryError as exc:
            if str(exc) == "provider_chat_policy_revision_conflict":
                raise RouterServiceError(
                    "provider_chat_policy_revision_conflict",
                    "控制策略已被其他管理操作更新，请刷新后重试。",
                    status_code=409,
                ) from exc
            raise
        response = self._policy_response(bundle)
        self._sync_gate_epoch(response)
        return response

    def gate(self) -> ProviderChatControlGateResponse:
        policy = self.get_policy()
        blockers: list[str] = []
        if not policy.feature_enabled:
            blockers.append("provider_chat_control_feature_disabled")
        if policy.configured_mode == "legacy":
            blockers.append("provider_chat_control_legacy_mode")
        if not policy.stable_model_ids:
            blockers.append("provider_chat_control_stable_models_required")
        text_routes = next(
            (
                route.connection_ids
                for route in policy.routes
                if route.capability == "chat_text"
            ),
            [],
        )
        if not text_routes:
            blockers.append("provider_chat_control_text_route_required")
        if any(not item.valid for item in policy.qualifications):
            blockers.append("provider_chat_control_qualification_stale")
        epoch = self._repository_method("get_open_chat_control_gate_epoch")(
            self.router_service.tenant_id,
            policy.policy_fingerprint,
        )
        latest_epoch = epoch or self._repository_method(
            "get_latest_chat_control_gate_epoch"
        )(
            self.router_service.tenant_id,
            policy.policy_fingerprint,
        )
        summary: dict[str, object] = {}
        if latest_epoch is not None:
            summary = self._repository_method("summarize_chat_control_gate")(
                self.router_service.tenant_id,
                epoch_id=str(latest_epoch["id"]),
            )
        evaluation = evaluate_provider_chat_gate(
            summary,
            stable_model_ids=policy.stable_model_ids,
        )
        if epoch is None:
            blockers.append("provider_chat_gate_epoch_unavailable")
            if latest_epoch is not None and str(latest_epoch["status"]) == "degraded":
                blockers.append("provider_chat_gate_hard_failure_recertification_required")
        else:
            blockers.extend(evaluation.blocking_reason_codes)
        approval = self._repository_method("get_chat_control_gate_approval")(
            self.router_service.tenant_id,
            policy_fingerprint=policy.policy_fingerprint,
        )
        acceptance_evidence: list[dict[str, object]] = []
        if latest_epoch is not None:
            acceptance_evidence = self._repository_method(
                "list_chat_control_acceptance_evidence"
            )(
                self.router_service.tenant_id,
                policy_fingerprint=policy.policy_fingerprint,
                epoch_id=str(latest_epoch["id"]),
            )
        evidence_kinds = {
            str(item["evidence_kind"])
            for item in acceptance_evidence
            if bool(item["passed"])
        }
        required_evidence = {
            "newapi_quota_decrement",
            "newapi_usage_log",
            "newapi_restart_persistence",
        }
        evidence_complete = required_evidence <= evidence_kinds
        required_active = bool(
            policy.feature_enabled
            and policy.configured_mode == "newapi_required_default"
            and epoch is not None
            and str(epoch["status"]) == "active"
            and approval is not None
            and bool(approval.get("no_open_p0_p1"))
            and bool(approval.get("acknowledge_fail_closed"))
            and not validate_provider_chat_drills(
                approval.get("drills")
                if isinstance(approval.get("drills"), dict)
                else {}
            )
            and evidence_complete
            and all(item.valid for item in policy.qualifications)
        )
        automatic_ready = bool(
            epoch is not None
            and evaluation.ready
            and policy.feature_enabled
            and policy.configured_mode in {
                "newapi_preferred",
                "newapi_required_default",
            }
            and policy.stable_model_ids
            and text_routes
            and all(item.valid for item in policy.qualifications)
        )
        activation_available = bool(
            automatic_ready
            and policy.configured_mode == "newapi_preferred"
            and str(epoch["status"]) in {"open", "collecting", "ready"}
        )
        if policy.configured_mode == "newapi_preferred" and automatic_ready:
            blockers.append("provider_chat_required_manual_approval_pending")
        if (
            policy.configured_mode == "newapi_required_default"
            and not required_active
        ):
            blockers.append("provider_chat_required_gate_degraded")
        return ProviderChatControlGateResponse(
            contract_version=PROVIDER_CHAT_ROUTING_CONTRACT_VERSION,
            feature_enabled=policy.feature_enabled,
            data_plane_integrated=True,
            policy_fingerprint=policy.policy_fingerprint,
            configured_mode=policy.configured_mode,
            required_activation_available=activation_available,
            required_active=required_active,
            ready=automatic_ready,
            epoch_id=(str(latest_epoch["id"]) if latest_epoch is not None else None),
            epoch_status=(
                str(latest_epoch["status"]) if latest_epoch is not None else None
            ),
            epoch_started_at=(
                str(latest_epoch["started_at"])
                if latest_epoch is not None
                else None
            ),
            epoch_closed_at=(
                str(latest_epoch["closed_at"])
                if latest_epoch is not None and latest_epoch.get("closed_at")
                else None
            ),
            hard_failure_code=(
                str(latest_epoch["hard_failure_code"])
                if latest_epoch is not None and latest_epoch.get("hard_failure_code")
                else None
            ),
            minimum_request_count=MIN_PROVIDER_CHAT_GATE_REQUESTS,
            minimum_observed_days=MIN_PROVIDER_CHAT_GATE_DAYS,
            minimum_success_rate=MIN_PROVIDER_CHAT_GATE_SUCCESS_RATE,
            request_count=evaluation.request_count,
            success_count=evaluation.success_count,
            hard_failure_count=evaluation.hard_failure_count,
            observed_days=evaluation.observed_days,
            success_rate=evaluation.success_rate,
            model_progress=[
                ProviderChatGateModelProgress(
                    model_id=model_id,
                    success_count=evaluation.model_successes.get(model_id, 0),
                    minimum_success_count=MIN_PROVIDER_CHAT_GATE_MODEL_SUCCESSES,
                    ready=(
                        evaluation.model_successes.get(model_id, 0)
                        >= MIN_PROVIDER_CHAT_GATE_MODEL_SUCCESSES
                    ),
                )
                for model_id in policy.stable_model_ids
            ],
            required_drills=list(REQUIRED_PROVIDER_CHAT_DRILLS),
            approval_recorded=approval is not None,
            acceptance_evidence_complete=evidence_complete,
            acceptance_evidence=[
                ProviderChatGateEvidenceSummary(
                    evidence_kind=str(item["evidence_kind"]),
                    passed=bool(item["passed"]),
                    observed_at=str(item["observed_at"]),
                )
                for item in acceptance_evidence
            ],
            blocking_reason_codes=list(dict.fromkeys(blockers)),
        )

    def activate_required(
        self, payload: ProviderChatRequiredActivationRequest
    ) -> ProviderChatControlGateResponse:
        correlation_reference = (
            payload.newapi_correlation_reference.get_secret_value().strip()
        )
        if not 8 <= len(correlation_reference) <= 512:
            raise RouterServiceError(
                "provider_chat_gate_correlation_reference_invalid",
                "newAPI 验收关联引用长度必须为 8 到 512 个字符。",
                status_code=422,
            )
        gate = self.gate()
        if not gate.required_activation_available or gate.epoch_id is None:
            code = next(
                (
                    item
                    for item in gate.blocking_reason_codes
                    if item != "provider_chat_required_manual_approval_pending"
                ),
                "provider_chat_required_gate_not_ready",
            )
            raise RouterServiceError(
                code,
                "当前证据尚未满足 newAPI 强制默认激活门禁。",
                status_code=409,
            )
        drill_errors = validate_provider_chat_drills(payload.drills)
        if drill_errors:
            raise RouterServiceError(
                drill_errors[0],
                "必须逐项完成并确认全部 required 故障演练。",
                status_code=422,
            )
        if not payload.no_open_p0_p1:
            raise RouterServiceError(
                "provider_chat_gate_p0_p1_attestation_required",
                "必须确认当前无未解决 P0/P1 问题。",
                status_code=422,
            )
        if not payload.acknowledge_fail_closed:
            raise RouterServiceError(
                "provider_chat_gate_fail_closed_ack_required",
                "必须确认 required 模式不可用时失败关闭且不会自动回退。",
                status_code=422,
            )
        evidence_checks = {
            "newapi_quota_decrement": payload.quota_decrement_verified,
            "newapi_usage_log": payload.usage_log_verified,
            "newapi_restart_persistence": payload.restart_persistence_verified,
        }
        if not all(evidence_checks.values()):
            raise RouterServiceError(
                "provider_chat_gate_acceptance_evidence_required",
                "必须完成 newAPI 额度、用量日志和重启持久化验收。",
                status_code=422,
            )
        try:
            self._repository_method("activate_chat_control_required")(
                self.router_service.tenant_id,
                expected_revision=payload.expected_revision,
                policy_fingerprint=gate.policy_fingerprint,
                epoch_id=gate.epoch_id,
                no_open_p0_p1=payload.no_open_p0_p1,
                drills=payload.drills,
                acknowledge_fail_closed=payload.acknowledge_fail_closed,
                correlation_hash=hashlib.sha256(
                    correlation_reference.encode("utf-8")
                ).hexdigest(),
                evidence_checks=evidence_checks,
            )
        except RouterRepositoryError as exc:
            code = str(exc)
            status_code = 409 if code.startswith("provider_chat_gate_") or code == (
                "provider_chat_policy_revision_conflict"
            ) else 422
            raise RouterServiceError(
                code,
                "激活前证据、策略或资格已变化，请刷新后重新审查。",
                status_code=status_code,
            ) from exc
        return self.gate()

    def required_runtime_allowed(
        self, policy: ProviderChatControlPolicyResponse | None = None
    ) -> tuple[bool, str | None]:
        current = policy or self.get_policy()
        if current.effective_mode != "newapi_required_default":
            return True, None
        gate = self.gate()
        if gate.required_active:
            return True, None
        return False, "provider_chat_required_gate_degraded"

    def public_status(
        self, model_id: str, capability: ProviderChatCapability
    ) -> ProviderChatControlPublicStatus:
        clean_model = str(model_id or "").strip()
        if not clean_model or len(clean_model) > 512:
            raise RouterServiceError(
                "invalid_model_id", "model_id 必须是有效模型 ID。", status_code=422
            )
        bundle = self._repository_method("get_chat_control_policy_bundle")(
            self.router_service.tenant_id
        )
        policy_row = bundle.get("policy")
        configured_mode = (
            str(policy_row["mode"])
            if isinstance(policy_row, dict)
            else "legacy"
        )
        enabled = self.feature_enabled()
        if not enabled:
            reason = "provider_chat_control_feature_disabled"
            available = False
            would_block = False
        elif configured_mode == "legacy":
            reason = "provider_chat_control_legacy_mode"
            available = False
            would_block = False
        else:
            policy = self._policy_response(bundle)
            if clean_model not in policy.stable_model_ids:
                reason = "provider_chat_model_not_stable"
                available = False
                would_block = False
            else:
                text_route = next(
                    (
                        route
                        for route in policy.routes
                        if route.capability == capability
                    ),
                    None,
                )
                route_ids = text_route.connection_ids if text_route else []
                valid_ids = {
                    item.connection_id
                    for item in policy.qualifications
                    if item.capability == capability
                    and item.model_id == clean_model
                    and item.valid
                }
                available = any(item in valid_ids for item in route_ids)
                would_block = not available
                reason = (
                    "qualified"
                    if available
                    else "provider_chat_no_qualified_route"
                )
        return ProviderChatControlPublicStatus(
            contract_version=PROVIDER_CHAT_ROUTING_CONTRACT_VERSION,
            feature_enabled=enabled,
            data_plane_integrated=True,
            model_id=clean_model,
            capability=capability,
            effective_mode=configured_mode if enabled else "legacy",
            available=available,
            would_block=would_block,
            reason_code=reason,
        )

    def receipts(
        self, *, limit: int = 50, cursor: str | None = None
    ) -> ProviderChatControlReceiptsResponse:
        try:
            rows = self._repository_method("list_chat_control_receipts")(
                self.router_service.tenant_id,
                limit=max(1, min(int(limit), 100)),
                cursor=cursor,
            )
        except RouterRepositoryError as exc:
            if str(exc) == "provider_chat_receipt_cursor_invalid":
                raise RouterServiceError(
                    "provider_chat_receipt_cursor_invalid",
                    "Receipt 分页游标无效或不属于当前租户。",
                    status_code=422,
                ) from exc
            raise
        attempts_by_run: dict[str, list[ProviderChatControlAttemptSummary]] = (
            defaultdict(list)
        )
        for row in rows["attempts"]:
            attempts_by_run[str(row["run_id"])].append(
                ProviderChatControlAttemptSummary(
                    attempt_id=str(row["id"]),
                    run_id=str(row["run_id"]),
                    capability=str(row["capability"]),
                    provider_kind=str(row["provider_kind"]),
                    connection_id=(
                        str(row["connection_id"])
                        if row.get("connection_id")
                        else None
                    ),
                    position=int(row["position"]),
                    dispatched=bool(row["dispatched"]),
                    status=str(row["status"]),
                    result_class=(
                        str(row["result_class"])
                        if row.get("result_class")
                        else None
                    ),
                    error_code=(
                        str(row["error_code"]) if row.get("error_code") else None
                    ),
                    actual_model=(
                        str(row["actual_model"])
                        if row.get("actual_model")
                        else None
                    ),
                    ttft_ms=self._float(row.get("ttft_ms")),
                    e2e_ms=self._float(row.get("e2e_ms")),
                    total_tokens=self._integer(row.get("total_tokens")),
                    created_at=str(row["created_at"]),
                    completed_at=(
                        str(row["completed_at"])
                        if row.get("completed_at")
                        else None
                    ),
                )
            )
        runs = []
        for row in rows["runs"]:
            reason_codes = json.loads(str(row.get("reason_codes_json") or "[]"))
            runs.append(
                ProviderChatControlRunSummary(
                    run_id=str(row["id"]),
                    policy_fingerprint=str(row["policy_fingerprint"]),
                    capability=str(row["capability"]),
                    requested_model=str(row["requested_model"]),
                    actual_model=(
                        str(row["actual_model"])
                        if row.get("actual_model")
                        else None
                    ),
                    gateway=str(row["gateway"]),
                    strategy=str(row["strategy"]),
                    status=str(row["status"]),
                    result_class=(
                        str(row["result_class"])
                        if row.get("result_class")
                        else None
                    ),
                    reason_codes=[str(item) for item in reason_codes],
                    ttft_ms=self._float(row.get("ttft_ms")),
                    e2e_ms=self._float(row.get("e2e_ms")),
                    total_tokens=self._integer(row.get("total_tokens")),
                    created_at=str(row["created_at"]),
                    completed_at=(
                        str(row["completed_at"])
                        if row.get("completed_at")
                        else None
                    ),
                    attempts=attempts_by_run[str(row["id"])],
                )
            )
        return ProviderChatControlReceiptsResponse(
            contract_version=PROVIDER_CHAT_ROUTING_CONTRACT_VERSION,
            runs=runs,
            next_cursor=(
                str(rows["next_cursor"]) if rows.get("next_cursor") else None
            ),
        )

    def _policy_response(
        self, bundle: dict[str, object]
    ) -> ProviderChatControlPolicyResponse:
        policy = bundle.get("policy")
        if not isinstance(policy, dict):
            fingerprint = self._fingerprint(
                mode="legacy",
                auto_enabled=False,
                stable_models=[],
                routes=[],
                qualifications=[],
            )
            return ProviderChatControlPolicyResponse(
                contract_version=PROVIDER_CHAT_ROUTING_CONTRACT_VERSION,
                feature_enabled=self.feature_enabled(),
                data_plane_integrated=True,
                configured_mode="legacy",
                effective_mode="legacy",
                auto_enabled=False,
                revision=0,
                policy_fingerprint=fingerprint,
                routes=[
                    ProviderChatControlRouteSummary(
                        capability=capability, connection_ids=[]
                    )
                    for capability in CHAT_CONTROL_CAPABILITIES
                ],
            )

        routes_by_capability: dict[str, list[str]] = defaultdict(list)
        for route in list(bundle.get("routes") or []):
            routes_by_capability[str(route["capability"])].append(
                str(route["connection_id"])
            )
        qualifications = [
            self._qualification_summary(row)
            for row in list(bundle.get("qualifications") or [])
        ]
        configured_mode = str(policy["mode"])
        enabled = self.feature_enabled()
        return ProviderChatControlPolicyResponse(
            contract_version=PROVIDER_CHAT_ROUTING_CONTRACT_VERSION,
            feature_enabled=enabled,
            data_plane_integrated=True,
            configured_mode=configured_mode,
            effective_mode=configured_mode if enabled else "legacy",
            auto_enabled=bool(policy["auto_enabled"]),
            revision=int(policy["revision"]),
            policy_fingerprint=str(policy["policy_fingerprint"]),
            stable_model_ids=[
                str(item)
                for item in json.loads(str(policy["stable_models_json"] or "[]"))
            ],
            routes=[
                ProviderChatControlRouteSummary(
                    capability=capability,
                    connection_ids=routes_by_capability[capability],
                )
                for capability in CHAT_CONTROL_CAPABILITIES
            ],
            qualifications=qualifications,
            updated_at=str(policy["updated_at"]),
        )

    def _qualification_summary(
        self, row: dict[str, object]
    ) -> ProviderChatQualificationSummary:
        connection_id = str(row["connection_id"])
        model_id = str(row["model_id"])
        capability = str(row["capability"])
        try:
            connection = self.repository.get_connection(
                self.router_service.tenant_id, connection_id
            )
            current, reason = self._current_qualification(
                connection_id=connection_id,
                model_id=model_id,
                capability=capability,
            )
            valid = (
                current is not None
                and current["certification_id"] == row["certification_id"]
                and current["connection_fingerprint"]
                == row["connection_fingerprint"]
            )
            return ProviderChatQualificationSummary(
                capability=capability,
                connection_id=connection_id,
                connection_name=connection.name,
                provider_kind=connection.kind,
                model_id=model_id,
                certification_id=str(row["certification_id"]),
                valid=valid,
                reason_code="qualified" if valid else reason,
            )
        except RouterRepositoryError:
            raise RouterServiceError(
                "provider_chat_route_connection_missing",
                "已保存的 Chat 路由连接不存在。",
                status_code=409,
            )

    def _current_qualification(
        self,
        *,
        connection_id: str,
        model_id: str,
        capability: str,
    ) -> tuple[dict[str, object] | None, str]:
        connection = self.repository.get_connection(
            self.router_service.tenant_id, connection_id
        )
        if not connection.enabled:
            return None, "connection_disabled"
        if "chat" not in connection.scopes:
            return None, "connection_chat_scope_required"
        if connection.health != "online":
            return None, "provider_connection_not_online"
        try:
            self.repository.resolve_api_key(
                self.router_service.tenant_id, connection_id
            )
        except RouterCredentialUnavailable:
            return None, "provider_chat_credential_unavailable"
        fingerprint = self.repository.connection_config_fingerprint(
            self.router_service.tenant_id, connection_id
        )
        inventory = self.repository.list_catalog_models(
            self.router_service.tenant_id,
            connection_id=connection_id,
            model_id=model_id,
            status="active",
            limit=1,
        )
        if not inventory:
            return None, "provider_chat_model_inventory_missing"
        inventory_row = inventory[0]
        refresh = next(
            (
                item
                for item in self.repository.list_catalog_refreshes(
                    self.router_service.tenant_id,
                    connection_id=connection_id,
                    limit=500,
                )
                if str(item["id"]) == str(inventory_row["last_refresh_id"])
            ),
            None,
        )
        if refresh is None or str(refresh["status"]) != "succeeded":
            return None, "provider_chat_catalog_refresh_missing"
        if bool(refresh["truncated"]):
            return None, "provider_chat_catalog_refresh_truncated"
        if str(refresh["connection_fingerprint"]) != fingerprint:
            return None, "provider_chat_catalog_stale"
        certification = self.repository.get_latest_chat_certification(
            self.router_service.tenant_id,
            connection_id,
            model_id,
            capability,
        )
        if certification is None:
            return None, "provider_chat_capability_certification_required"
        if str(certification["status"]) != "passed":
            return None, "provider_chat_capability_certification_not_passed"
        if str(certification["connection_fingerprint"]) != fingerprint:
            return None, "provider_chat_capability_certification_stale"
        if str(certification["contract_version"]) != PROVIDER_CHAT_CONTRACT_VERSION:
            return None, "provider_chat_capability_contract_stale"
        hard_failure_reader = getattr(
            self.repository,
            "get_latest_chat_control_hard_failure",
            None,
        )
        if callable(hard_failure_reader):
            hard_failure = hard_failure_reader(
                self.router_service.tenant_id,
                connection_id=connection_id,
                model_id=model_id,
                capability=capability,
            )
            if hard_failure is not None and self._timestamp_at_or_after(
                hard_failure.get("completed_at"),
                certification.get("completed_at"),
            ):
                return None, "provider_chat_hard_failure_recertification_required"
        time_reason = self._certification_time_status(certification)
        if time_reason is not None:
            return None, time_reason
        actual_model = certification.get("actual_model")
        if actual_model and str(actual_model) != model_id:
            return None, "provider_chat_certification_model_mismatch"
        return (
            {
                "capability": capability,
                "connection_id": connection_id,
                "model_id": model_id,
                "certification_id": str(certification["id"]),
                "connection_fingerprint": fingerprint,
                "contract_version": PROVIDER_CHAT_CONTRACT_VERSION,
            },
            "qualified",
        )

    def _validate_route_connection(self, connection) -> None:
        if not connection.enabled:
            raise RouterServiceError(
                "connection_disabled", "路由目标连接已停用。", status_code=409
            )
        if "chat" not in connection.scopes:
            raise RouterServiceError(
                "connection_chat_scope_required",
                "路由目标必须启用 Chat scope。",
                status_code=409,
            )
        try:
            self.repository.resolve_api_key(
                self.router_service.tenant_id, connection.id
            )
        except RouterCredentialUnavailable as exc:
            raise RouterServiceError(
                "provider_chat_credential_unavailable",
                "路由目标凭据当前无法解密。",
                status_code=409,
            ) from exc

    def _sync_gate_epoch(self, policy: ProviderChatControlPolicyResponse) -> None:
        expected_qualifications = len(policy.stable_model_ids) * sum(
            len(route.connection_ids) for route in policy.routes
        )
        text_routes = next(
            (
                route.connection_ids
                for route in policy.routes
                if route.capability == "chat_text"
            ),
            [],
        )
        qualified = bool(
            policy.feature_enabled
            and policy.configured_mode in {
                "newapi_preferred",
                "newapi_required_default",
            }
            and policy.stable_model_ids
            and text_routes
            and expected_qualifications > 0
            and len(policy.qualifications) == expected_qualifications
            and all(item.valid for item in policy.qualifications)
        )
        self._repository_method("sync_chat_control_gate_epoch")(
            self.router_service.tenant_id,
            epoch_id=f"chatgate_{uuid.uuid4().hex}",
            policy_fingerprint=policy.policy_fingerprint,
            qualified=qualified,
            invalidation_code=(
                "provider_chat_policy_or_qualification_changed"
                if policy.revision
                else "provider_chat_policy_not_configured"
            ),
        )

    @staticmethod
    def _certification_time_status(
        certification: dict[str, object],
    ) -> str | None:
        raw = os.getenv(PROVIDER_CHAT_CERTIFICATION_MAX_AGE_ENV, "").strip()
        if raw:
            try:
                max_age_seconds = int(raw)
            except ValueError:
                return "provider_chat_certification_ttl_invalid"
            if not (
                MIN_PROVIDER_CHAT_CERTIFICATION_MAX_AGE_SECONDS
                <= max_age_seconds
                <= MAX_PROVIDER_CHAT_CERTIFICATION_MAX_AGE_SECONDS
            ):
                return "provider_chat_certification_ttl_invalid"
        else:
            max_age_seconds = DEFAULT_PROVIDER_CHAT_CERTIFICATION_MAX_AGE_SECONDS
        completed_at_text = str(certification.get("completed_at") or "").strip()
        try:
            completed_at = datetime.fromisoformat(
                completed_at_text.replace("Z", "+00:00")
            )
        except ValueError:
            return "provider_chat_certification_time_invalid"
        if completed_at.tzinfo is None:
            return "provider_chat_certification_time_invalid"
        if datetime.now(UTC) >= completed_at.astimezone(UTC) + timedelta(
            seconds=max_age_seconds
        ):
            return "provider_chat_certification_expired"
        return None

    @staticmethod
    def _fingerprint(
        *,
        mode: str,
        auto_enabled: bool,
        stable_models: list[str],
        routes: list[dict[str, object]],
        qualifications: list[dict[str, object]],
    ) -> str:
        material = {
            "contract_version": PROVIDER_CHAT_ROUTING_CONTRACT_VERSION,
            "mode": mode,
            "auto_enabled": bool(auto_enabled),
            "stable_models": stable_models,
            "routes": routes,
            "qualifications": qualifications,
        }
        return hashlib.sha256(
            json.dumps(
                material,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _qualification_hint(reason: str, model_id: str) -> str:
        hints = {
            "provider_chat_model_inventory_missing": "模型不在当前完整目录中。",
            "provider_chat_catalog_refresh_truncated": "最近目录已截断，不能形成稳定资格。",
            "provider_chat_catalog_stale": "目录对应的连接配置已变化。",
            "provider_chat_capability_certification_required": "缺少当前能力的真实认证。",
            "provider_chat_capability_certification_not_passed": "当前能力认证尚未通过。",
            "provider_chat_capability_certification_stale": "当前能力认证已因连接变化而过期。",
        }
        return f"模型 {model_id} 无法加入稳定策略：{hints.get(reason, reason)}"

    @staticmethod
    def _timestamp_at_or_after(candidate: object, baseline: object) -> bool:
        try:
            candidate_time = datetime.fromisoformat(
                str(candidate or "").replace("Z", "+00:00")
            )
            baseline_time = datetime.fromisoformat(
                str(baseline or "").replace("Z", "+00:00")
            )
        except ValueError:
            return True
        if candidate_time.tzinfo is None or baseline_time.tzinfo is None:
            return True
        return candidate_time.astimezone(UTC) >= baseline_time.astimezone(UTC)

    def _repository_method(self, name: str):
        method = getattr(self.repository, name, None)
        if not callable(method):
            raise RouterServiceError(
                "provider_chat_control_storage_unavailable",
                "当前 Router 存储不支持 Chat 控制策略。",
                status_code=503,
            )
        return method

    @staticmethod
    def _integer(value: object) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _float(value: object) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
