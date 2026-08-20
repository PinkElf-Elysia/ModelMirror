from __future__ import annotations

import os
import logging
from collections.abc import Callable

import httpx

from .repository import (
    DEFAULT_TENANT_ID,
    RouterConnectionNotFound,
    RouterRepository,
    SQLiteRouterRepository,
    utc_now,
)
from .egress import ProviderEgressError, ProviderEgressPolicy
from .schemas import (
    ConnectionScope,
    ConnectionTestResult,
    RouterConnection,
    RouterConnectionCreate,
    RouterConnectionUpdate,
    RouterPolicy,
    RouterStatus,
)


class RouterServiceError(Exception):
    def __init__(self, code: str, hint: str, *, status_code: int = 400) -> None:
        super().__init__(hint)
        self.code = code
        self.hint = hint
        self.status_code = status_code


class ModelRouterService:
    def __init__(
        self,
        repository: RouterRepository | None = None,
        *,
        tenant_id: str | None = None,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        egress_policy: ProviderEgressPolicy | None = None,
    ) -> None:
        self.tenant_id = self._resolve_tenant_id(tenant_id)
        self.repository = repository or SQLiteRouterRepository()
        self._client_factory = client_factory or (
            lambda: httpx.AsyncClient(
                timeout=httpx.Timeout(12.0, connect=5.0),
                follow_redirects=False,
                trust_env=False,
            )
        )
        self.egress_policy = egress_policy or ProviderEgressPolicy()

    @staticmethod
    def _resolve_tenant_id(explicit: str | None) -> str:
        if explicit is not None:
            clean = explicit.strip()
            if not clean:
                raise RouterServiceError(
                    "invalid_tenant_configuration",
                    "The injected provider tenant identifier cannot be empty.",
                    status_code=503,
                )
            return clean
        canonical = os.getenv("MODELMIRROR_DEFAULT_TENANT_ID", "").strip()
        legacy = os.getenv("MODEL_ROUTER_TENANT_ID", "").strip()
        if canonical and legacy and canonical != legacy:
            raise RouterServiceError(
                "tenant_configuration_conflict",
                "MODELMIRROR_DEFAULT_TENANT_ID conflicts with MODEL_ROUTER_TENANT_ID.",
                status_code=503,
            )
        selected = canonical or legacy or DEFAULT_TENANT_ID
        if selected != DEFAULT_TENANT_ID:
            raise RouterServiceError(
                "unsupported_tenant",
                "The initial provider control plane supports only tenant 'local'.",
                status_code=503,
            )
        if legacy and not canonical:
            logging.getLogger("modelmirror.model_router").warning(
                "MODEL_ROUTER_TENANT_ID is deprecated; use MODELMIRROR_DEFAULT_TENANT_ID."
            )
        return selected

    def list_connections(
        self,
        *,
        scope: ConnectionScope | None = None,
    ) -> list[RouterConnection]:
        connections = self.repository.list_connections(self.tenant_id)
        if scope is None:
            return connections
        return [
            connection
            for connection in connections
            if scope in connection.scopes
        ]

    async def create_connection(
        self, payload: RouterConnectionCreate
    ) -> RouterConnection:
        normalized_url = self.egress_policy.validate_for_storage(payload.base_url)
        await self.egress_policy.authorize(normalized_url)
        normalized = payload.model_copy(update={"base_url": normalized_url})
        return self.repository.create_connection(self.tenant_id, normalized)

    async def update_connection(
        self, connection_id: str, payload: RouterConnectionUpdate
    ) -> RouterConnection:
        normalized = payload
        if payload.base_url is not None:
            normalized_url = self.egress_policy.validate_for_storage(payload.base_url)
            await self.egress_policy.authorize(normalized_url)
            normalized = payload.model_copy(update={"base_url": normalized_url})
        return self.repository.update_connection(
            self.tenant_id, connection_id, normalized
        )

    async def test_unsaved_connection(
        self, payload: RouterConnectionCreate
    ) -> ConnectionTestResult:
        return await self._probe(
            self.egress_policy.validate_for_storage(payload.base_url),
            payload.api_key.get_secret_value(),
        )

    async def test_saved_connection(
        self, connection_id: str
    ) -> ConnectionTestResult:
        connection = self.repository.get_connection(self.tenant_id, connection_id)
        if not connection.enabled:
            raise RouterServiceError(
                "connection_disabled",
                "该模型服务已停用，请先启用后再测试。",
                status_code=409,
            )
        api_key = self.repository.resolve_api_key(self.tenant_id, connection_id)
        result = await self._probe(connection.base_url, api_key)
        save_result = getattr(self.repository, "save_test_result", None)
        if callable(save_result):
            error_code = None if result.ok else self._result_error_code(result)
            save_result(
                self.tenant_id,
                connection_id,
                health=result.health,
                model_count=result.model_count,
                checked_at=result.checked_at,
                error_code=error_code,
                error_hint=None if result.ok else result.message,
            )
        return result

    async def fetch_connection_models(
        self, connection_id: str
    ) -> tuple[ConnectionTestResult, list[str]]:
        connection = self.repository.get_connection(self.tenant_id, connection_id)
        if not connection.enabled:
            raise RouterServiceError(
                "connection_disabled",
                "该模型服务已停用。",
                status_code=409,
            )
        if "chat" not in connection.scopes:
            return self._scope_mismatch_result(connection), []
        api_key = self.repository.resolve_api_key(self.tenant_id, connection_id)
        result, model_ids, _ = await self._probe_with_models(
            connection.base_url, api_key
        )
        save_result = getattr(self.repository, "save_test_result", None)
        if callable(save_result):
            save_result(
                self.tenant_id,
                connection_id,
                health=result.health,
                model_count=result.model_count,
                checked_at=result.checked_at,
                error_code=None if result.ok else self._result_error_code(result),
                error_hint=None if result.ok else result.message,
            )
        return result, model_ids

    async def fetch_connection_model_records(
        self, connection_id: str
    ) -> tuple[ConnectionTestResult, list[dict[str, object]]]:
        connection = self.repository.get_connection(self.tenant_id, connection_id)
        if not connection.enabled:
            raise RouterServiceError(
                "connection_disabled",
                "该模型服务已停用。",
                status_code=409,
            )
        if "chat" not in connection.scopes:
            return self._scope_mismatch_result(connection), []
        api_key = self.repository.resolve_api_key(self.tenant_id, connection_id)
        result, _, records = await self._probe_with_models(
            connection.base_url, api_key
        )
        save_result = getattr(self.repository, "save_test_result", None)
        if callable(save_result):
            save_result(
                self.tenant_id,
                connection_id,
                health=result.health,
                model_count=result.model_count,
                checked_at=result.checked_at,
                error_code=None if result.ok else self._result_error_code(result),
                error_hint=None if result.ok else result.message,
            )
        return result, records

    def get_policy(self) -> RouterPolicy:
        return self.repository.get_policy(self.tenant_id)

    def save_policy(self, policy: RouterPolicy) -> RouterPolicy:
        if (
            policy.engine in {"native_canary", "native"}
            and not self.status().ready
        ):
            raise RouterServiceError(
                "native_connection_required",
                "请先新增并测试至少一个可用的模型服务连接，再启用本地试运行。",
                status_code=409,
            )
        if (
            policy.engine == "native"
            and os.getenv("MODEL_ROUTER_ALLOW_NATIVE_OVERRIDE", "false")
            .strip()
            .lower()
            not in {"1", "true", "yes", "on"}
        ):
            gate = self.diagnostics().get("migration_gate", {})
            if not bool(gate.get("native_default_allowed")):
                request_count = int(gate.get("request_count") or 0)
                observed_days = float(gate.get("observed_days") or 0)
                raise RouterServiceError(
                    "native_gate_not_met",
                    (
                        "本地默认尚未达到安全门槛："
                        f"当前 {request_count}/500 次请求、"
                        f"{observed_days:.1f}/14 天。"
                        "请先使用“本地试运行”，完成故障演练和人工验收。"
                    ),
                    status_code=409,
                )
        return self.repository.save_policy(self.tenant_id, policy)

    def approve_native_gate(
        self, *, no_open_p0_p1: bool, drills: dict[str, bool]
    ) -> dict[str, object]:
        gate = self.diagnostics().get("migration_gate", {})
        if not bool(gate.get("automatic_native_default_allowed")):
            blockers = gate.get("blocking_reasons") or []
            raise RouterServiceError(
                "native_automatic_gate_not_met",
                "自动门禁尚未通过：" + "；".join(str(item) for item in blockers),
                status_code=409,
            )
        writer = getattr(self.repository, "save_native_gate_approval", None)
        if not callable(writer):
            raise RouterServiceError(
                "native_approval_unavailable",
                "当前存储后端不支持原生路由批准。",
                status_code=501,
            )
        try:
            writer(
                self.tenant_id,
                algorithm_version=str(gate.get("algorithm_version") or ""),
                config_hash=str(gate.get("config_hash") or ""),
                no_open_p0_p1=no_open_p0_p1,
                drills=drills,
            )
        except ValueError as exc:
            raise RouterServiceError(
                "native_approval_incomplete", str(exc), status_code=422
            ) from exc
        return self.diagnostics()

    def revoke_native_gate(self) -> dict[str, object]:
        revoke = getattr(self.repository, "revoke_native_gate_approval", None)
        if callable(revoke):
            revoke(self.tenant_id)
        return self.diagnostics()

    def status(self) -> RouterStatus:
        connections = self.list_connections(scope="chat")
        online = [item for item in connections if item.health == "online"]
        policy = self.get_policy()
        return RouterStatus(
            tenant_id=self.tenant_id,
            engine=policy.engine,
            default_mode=policy.default_mode,
            compression_mode=policy.compression_mode,
            canary_percent=policy.canary_percent,
            connection_count=len(connections),
            online_connection_count=len(online),
            model_count=sum(item.model_count for item in online),
            ready=bool(online),
        )

    def diagnostics(self) -> dict[str, object]:
        reader = getattr(self.repository, "get_diagnostics", None)
        if not callable(reader):
            return {
                "tenant_id": self.tenant_id,
                "redacted": True,
                "migration_gate": {
                    "automatic_native_default_allowed": False,
                    "native_default_allowed": False,
                    "manual_safety_gates_required": True,
                },
                "recent_decisions": [],
                "recent_compressions": [],
            }
        return reader(self.tenant_id)

    async def _probe(self, base_url: str, api_key: str) -> ConnectionTestResult:
        result, _, _ = await self._probe_with_models(base_url, api_key)
        return result

    async def _probe_with_models(
        self, base_url: str, api_key: str
    ) -> tuple[ConnectionTestResult, list[str], list[dict[str, object]]]:
        checked_at = utc_now()
        models_url = self._models_url(base_url)
        headers = {"Authorization": f"Bearer {api_key.strip()}"}
        try:
            async with self._client_factory() as client:
                response = await self.egress_policy.request(
                    client, "GET", models_url, headers=headers
                )
        except ProviderEgressError:
            raise
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout):
            return (
                ConnectionTestResult(
                    ok=False,
                    health="offline",
                    message="无法连接该地址。请检查地址、网络和服务是否已启动。",
                    checked_at=checked_at,
                ),
                [],
                [],
            )
        except httpx.HTTPError:
            return (
                ConnectionTestResult(
                    ok=False,
                    health="offline",
                    message="模型服务连接失败，请稍后重试。",
                    checked_at=checked_at,
                ),
                [],
                [],
            )

        if response.status_code in {401, 403}:
            return (
                ConnectionTestResult(
                    ok=False,
                    health="offline",
                    message="密钥无效或没有读取模型目录的权限。",
                    checked_at=checked_at,
                ),
                [],
                [],
            )
        if response.status_code == 404:
            return (
                ConnectionTestResult(
                    ok=False,
                    health="offline",
                    message="未找到模型目录接口，请确认地址包含正确的 API 版本路径。",
                    checked_at=checked_at,
                ),
                [],
                [],
            )
        if response.status_code == 429:
            return (
                ConnectionTestResult(
                    ok=False,
                    health="offline",
                    message="模型服务暂时限流，请稍后重试。",
                    checked_at=checked_at,
                ),
                [],
                [],
            )
        if response.status_code >= 500:
            return (
                ConnectionTestResult(
                    ok=False,
                    health="offline",
                    message="模型服务暂时不可用，请检查服务状态后重试。",
                    checked_at=checked_at,
                ),
                [],
                [],
            )
        if response.status_code >= 400:
            return (
                ConnectionTestResult(
                    ok=False,
                    health="offline",
                    message="模型目录请求被拒绝，请检查服务地址和访问权限。",
                    checked_at=checked_at,
                ),
                [],
                [],
            )
        try:
            payload = response.json()
        except ValueError:
            payload = None
        raw_models = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(raw_models, list):
            return (
                ConnectionTestResult(
                    ok=False,
                    health="offline",
                    message="服务已连接，但模型目录格式不兼容。",
                    checked_at=checked_at,
                ),
                [],
                [],
            )
        model_ids = sorted(
            {
                str(item.get("id", "")).strip()
                for item in raw_models
                if isinstance(item, dict) and str(item.get("id", "")).strip()
            }
        )
        if not model_ids:
            return (
                ConnectionTestResult(
                    ok=False,
                    health="offline",
                    message="服务已连接，但没有发现可调用模型。",
                    checked_at=checked_at,
                ),
                [],
                [],
            )
        return (
            ConnectionTestResult(
                ok=True,
                health="online",
                model_count=len(model_ids),
                models_preview=model_ids[:8],
                message=f"连接成功，发现 {len(model_ids)} 个可用模型。",
                checked_at=checked_at,
            ),
            model_ids,
            [dict(item) for item in raw_models if isinstance(item, dict)],
        )

    @staticmethod
    def _models_url(base_url: str) -> str:
        lowered = base_url.lower()
        if lowered.endswith("/models"):
            return base_url
        if lowered.endswith("/v1"):
            return f"{base_url}/models"
        return f"{base_url}/v1/models"

    @staticmethod
    def _result_error_code(result: ConnectionTestResult) -> str:
        message = result.message
        if "密钥" in message:
            return "invalid_key"
        if "没有发现" in message:
            return "no_models"
        if "格式" in message:
            return "incompatible_catalog"
        if "限流" in message:
            return "rate_limited"
        return "unreachable"

    @staticmethod
    def _scope_mismatch_result(
        connection: RouterConnection,
    ) -> ConnectionTestResult:
        return ConnectionTestResult(
            ok=False,
            health=connection.health,
            model_count=0,
            models_preview=[],
            message="该连接未启用普通模型调用，不会进入智能调度。",
            checked_at=utc_now(),
        )


def translate_repository_error(exc: Exception) -> RouterServiceError:
    if isinstance(exc, ProviderEgressError):
        return RouterServiceError(exc.code, exc.message, status_code=422)
    if isinstance(exc, RouterConnectionNotFound):
        return RouterServiceError("not_found", str(exc), status_code=404)
    return RouterServiceError(
        "storage_error",
        "模型服务配置暂时无法读取，请检查本地存储后重试。",
        status_code=500,
    )
