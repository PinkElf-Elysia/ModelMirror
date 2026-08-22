from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta

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
    ProviderChatControlGateResponse,
    ProviderChatControlPolicyResponse,
    ProviderChatControlPolicyUpdate,
    ProviderChatControlPublicStatus,
    ProviderChatControlReceiptsResponse,
    ProviderChatControlRouteSummary,
    ProviderChatControlRunSummary,
    ProviderChatQualificationSummary,
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
                "强制默认必须等待 R5E 门禁和人工批准。",
                status_code=409,
            )

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

        connections = {}
        normalized_routes: list[dict[str, object]] = []
        for capability in CHAT_CONTROL_CAPABILITIES:
            for position, connection_id in enumerate(
                routes_by_capability[capability]
            ):
                connection = self.repository.get_connection(
                    self.router_service.tenant_id, connection_id
                )
                self._validate_route_connection(connection)
                connections[connection_id] = connection
                normalized_routes.append(
                    {
                        "capability": capability,
                        "position": position,
                        "connection_id": connection_id,
                    }
                )

        if text_routes:
            primary = connections[text_routes[0]]
            if primary.kind != "newapi":
                raise RouterServiceError(
                    "provider_chat_text_primary_newapi_required",
                    "普通文本路由的第一个目标必须是 newAPI。",
                    status_code=422,
                )

        qualifications: list[dict[str, object]] = []
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
        blockers.append("provider_chat_required_gate_pending_r5e")
        return ProviderChatControlGateResponse(
            contract_version=PROVIDER_CHAT_ROUTING_CONTRACT_VERSION,
            feature_enabled=policy.feature_enabled,
            data_plane_integrated=True,
            policy_fingerprint=policy.policy_fingerprint,
            configured_mode=policy.configured_mode,
            blocking_reason_codes=list(dict.fromkeys(blockers)),
        )

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
            and policy.configured_mode == "newapi_preferred"
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
