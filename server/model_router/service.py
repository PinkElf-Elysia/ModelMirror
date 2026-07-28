from __future__ import annotations

import os
from collections.abc import Callable
from urllib.parse import urlparse

import httpx

from .repository import (
    DEFAULT_TENANT_ID,
    RouterConnectionNotFound,
    RouterRepository,
    SQLiteRouterRepository,
    utc_now,
)
from .schemas import (
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


def normalize_base_url(value: str) -> str:
    url = str(value or "").strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RouterServiceError(
            "invalid_address",
            "请输入以 http:// 或 https:// 开头的模型服务地址。",
        )
    if parsed.username or parsed.password:
        raise RouterServiceError(
            "invalid_address",
            "服务地址不能包含用户名或密码，请单独填写密钥。",
        )
    if parsed.query or parsed.fragment:
        raise RouterServiceError(
            "invalid_address",
            "服务地址不能包含查询参数或片段。",
        )
    return url


class ModelRouterService:
    def __init__(
        self,
        repository: RouterRepository | None = None,
        *,
        tenant_id: str | None = None,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self.repository = repository or SQLiteRouterRepository()
        self.tenant_id = (
            tenant_id
            or os.getenv("MODELMIRROR_DEFAULT_TENANT_ID", "").strip()
            or DEFAULT_TENANT_ID
        )
        self._client_factory = client_factory or (
            lambda: httpx.AsyncClient(
                timeout=httpx.Timeout(12.0, connect=5.0),
                follow_redirects=False,
            )
        )

    def list_connections(self) -> list[RouterConnection]:
        return self.repository.list_connections(self.tenant_id)

    def create_connection(
        self, payload: RouterConnectionCreate
    ) -> RouterConnection:
        normalized = payload.model_copy(
            update={"base_url": normalize_base_url(payload.base_url)}
        )
        return self.repository.create_connection(self.tenant_id, normalized)

    def update_connection(
        self, connection_id: str, payload: RouterConnectionUpdate
    ) -> RouterConnection:
        normalized = payload
        if payload.base_url is not None:
            normalized = payload.model_copy(
                update={"base_url": normalize_base_url(payload.base_url)}
            )
        return self.repository.update_connection(
            self.tenant_id, connection_id, normalized
        )

    async def test_unsaved_connection(
        self, payload: RouterConnectionCreate
    ) -> ConnectionTestResult:
        return await self._probe(
            normalize_base_url(payload.base_url),
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
            if not bool(gate.get("automatic_native_default_allowed")):
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

    def status(self) -> RouterStatus:
        connections = self.list_connections()
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
                response = await client.get(models_url, headers=headers)
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


def translate_repository_error(exc: Exception) -> RouterServiceError:
    if isinstance(exc, RouterConnectionNotFound):
        return RouterServiceError("not_found", str(exc), status_code=404)
    return RouterServiceError(
        "storage_error",
        "模型服务配置暂时无法读取，请检查本地存储后重试。",
        status_code=500,
    )
