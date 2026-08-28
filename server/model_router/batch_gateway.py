from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

import httpx

from .egress import ProviderEgressError
from .provider_operations import (
    provider_operation_batch_model_matches,
    ProviderOperationTarget,
    ProviderOperationTransport,
)
from .repository import RouterCredentialUnavailable, RouterRepositoryError
from .service import ModelRouterService, RouterServiceError
from .workload_control import (
    ProviderWorkloadCallService,
    ProviderWorkloadPreparedCall,
)


ManagedBatchRoutingMode = Literal[
    "legacy", "managed_required", "degraded_required"
]
PENDING_BATCH_STATUSES = frozenset(
    {"validating", "in_progress", "finalizing", "cancelling"}
)
TERMINAL_BATCH_STATUSES = frozenset(
    {"completed", "failed", "expired", "cancelled"}
)
MAX_BATCH_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_IDEMPOTENCY_KEY_LENGTH = 200


class ManagedOpenRouterBatchGateway:
    """Idempotent OpenRouter Batch adapter backed by managed Provider evidence."""

    def __init__(
        self,
        call_service: ProviderWorkloadCallService,
        *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self.call_service = call_service
        self.router_service = call_service.router_service
        self.repository = call_service.repository
        self._client_factory = client_factory

    @classmethod
    def for_router(
        cls,
        router_service: ModelRouterService,
        *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> "ManagedOpenRouterBatchGateway":
        return cls(
            ProviderWorkloadCallService(router_service),
            client_factory=client_factory,
        )

    def routing_mode(self) -> ManagedBatchRoutingMode:
        control = self.call_service.control
        if not control.feature_enabled("openrouter_batch"):
            return "legacy"
        policy = control.get_policy("openrouter_batch")
        if policy.configured_status == "legacy":
            return "legacy"
        if policy.effective_status == "managed_required":
            return "managed_required"
        return "degraded_required"

    async def submit(
        self,
        upstream_payload: Mapping[str, object],
        *,
        idempotency_key: str | None,
    ) -> tuple[int, dict[str, object]]:
        clean_key = str(idempotency_key or "").strip()
        if not clean_key:
            raise RouterServiceError(
                "provider_batch_idempotency_key_required",
                "Managed Batch 提交必须提供 Idempotency-Key。",
                status_code=422,
            )
        if len(clean_key) > MAX_IDEMPOTENCY_KEY_LENGTH:
            raise RouterServiceError(
                "provider_batch_idempotency_key_invalid",
                "Idempotency-Key 超过允许长度。",
                status_code=422,
            )
        endpoint = str(upstream_payload.get("endpoint") or "")
        model_id = str(upstream_payload.get("model") or "")
        operation = self._operation_for_endpoint(endpoint)
        requests = upstream_payload.get("requests")
        request_count = len(requests) if isinstance(requests, list) else 0
        if not model_id or request_count < 1:
            raise RouterServiceError(
                "provider_batch_request_invalid",
                "Managed Batch 请求缺少模型或输入。",
                status_code=422,
            )
        policy = self.call_service.control.get_policy("openrouter_batch")
        if policy.effective_status != "managed_required":
            raise RouterServiceError(
                "provider_workload_policy_not_active",
                "OpenRouter Batch 控制面未就绪，提交已在派发前阻断。",
                status_code=409,
            )
        binding = next(
            (
                item
                for item in policy.bindings
                if item.execution_shape == operation
                and item.model_id == model_id
                and item.valid
            ),
            None,
        )
        if binding is None:
            raise RouterServiceError(
                "provider_workload_binding_missing",
                "OpenRouter Batch 缺少该精确模型和执行形态的合格 Binding。",
                status_code=409,
            )

        idempotency_hash = self._hash(clean_key)
        request_fingerprint = self._fingerprint(upstream_payload)
        parent_reference = f"openrouter-batch:{idempotency_hash}"
        run_id = self.call_service.stable_run_id(
            "openrouter_batch", parent_reference
        )
        job_id = f"mmbatch_{uuid.uuid4().hex}"
        try:
            job, created = self.repository.claim_provider_batch_job(
                self.router_service.tenant_id,
                job_id=job_id,
                connection_id=binding.connection_id,
                connection_fingerprint=binding.connection_fingerprint,
                endpoint=endpoint,
                model_id=model_id,
                idempotency_key_hash=idempotency_hash,
                request_fingerprint=request_fingerprint,
                purpose="runtime",
                request_count=request_count,
                workload_run_id=run_id,
            )
        except RouterRepositoryError as exc:
            if str(exc) == "provider_batch_idempotency_conflict":
                raise RouterServiceError(
                    str(exc),
                    "同一 Idempotency-Key 已用于不同的 Batch 请求。",
                    status_code=409,
                ) from exc
            raise
        if not created:
            if job.get("upstream_batch_id"):
                return await self.poll(str(job["id"]))
            return 202, self._stored_job_payload(job)

        prepared: ProviderWorkloadPreparedCall | None = None
        run_started = False
        dispatched = False
        started = time.perf_counter()
        try:
            claimed_run_id = self.call_service.start_stable_run(
                "openrouter_batch",
                parent_run_reference=parent_reference,
            )
            run_started = True
            if claimed_run_id != run_id:
                raise RouterServiceError(
                    "provider_batch_workload_run_mismatch",
                    "Batch 运行引用不一致，提交已在派发前阻断。",
                    status_code=409,
                )
            prepared = await self.call_service.prepare_call(
                run_id=run_id,
                entry_id="openrouter_batch",
                execution_shape=operation,
                model_id=model_id,
                logical_call_key=job_id,
                call_sequence=1,
            )
            if prepared.operation_target is None:
                raise RouterServiceError(
                    "provider_batch_operation_target_missing",
                    "Managed Batch 目标解析失败。",
                    status_code=409,
                )
            async with self._client() as client:
                request = self.call_service.operation_transport.build_authorized_request(
                    client,
                    prepared.operation_target,
                    prepared.authorized_target,
                    method="POST",
                    payload=upstream_payload,
                )
                self.call_service.mark_dispatched(prepared)
                dispatched = True
                response = await self.call_service.operation_transport.send_authorized(
                    client, request
                )
                try:
                    if not 200 <= response.status_code < 300:
                        code = self._http_error_code(response.status_code)
                        self.repository.fail_provider_batch_submission(
                            self.router_service.tenant_id,
                            job_id,
                            error_code=code,
                        )
                        self._finish_receipt(
                            prepared,
                            run_id,
                            status="failed",
                            result_class="provider_error",
                            code=code,
                            e2e_ms=(time.perf_counter() - started) * 1000,
                        )
                        return response.status_code, {
                            "error": "OpenRouter Batch 提交失败。",
                            "code": code,
                        }
                    response_payload = await self._read_json(response)
                finally:
                    await response.aclose()
            upstream_batch_id = self._upstream_id(response_payload)
            upstream_status = self._status(response_payload, default="validating")
            self._counts(response_payload, request_count)
            actual_model = response_payload.get("model")
            if (
                isinstance(actual_model, str)
                and actual_model
                and not provider_operation_batch_model_matches(
                    provider_kind="openrouter",
                    requested_model=model_id,
                    actual_model=actual_model,
                )
            ):
                response_payload = {**response_payload, "status": "failed"}
                upstream_status = "failed"
                response_payload["_modelmirror_error_code"] = (
                    "provider_batch_model_mismatch"
                )
            submitted_status = (
                upstream_status
                if upstream_status in {"validating", "in_progress", "finalizing"}
                else "finalizing"
            )
            job = self.repository.mark_provider_batch_submitted(
                self.router_service.tenant_id,
                job_id,
                upstream_batch_id=upstream_batch_id,
                status=submitted_status,
            )
            if upstream_status in TERMINAL_BATCH_STATUSES or upstream_status == "cancelling":
                job = self._update_job(job, response_payload, upstream_status)
            receipt_code = (
                str(response_payload.get("_modelmirror_error_code"))
                if response_payload.get("_modelmirror_error_code")
                else (
                    "provider_batch_upstream_failed"
                    if upstream_status == "failed"
                    else None
                )
            )
            self._finish_receipt(
                prepared,
                run_id,
                status="failed" if receipt_code else "passed",
                result_class=(
                    "batch_submission_failed" if receipt_code else "batch_submitted"
                ),
                code=receipt_code,
                actual_model=(
                    actual_model if isinstance(actual_model, str) else None
                ),
                e2e_ms=(time.perf_counter() - started) * 1000,
            )
            return 202, self._response_payload(job, response_payload)
        except RouterServiceError:
            if not dispatched:
                self._fail_preflight_job(job_id, prepared, run_id, run_started)
            raise
        except asyncio.CancelledError:
            code = "provider_batch_submission_cancelled"
            if dispatched:
                try:
                    self.repository.mark_provider_batch_uncertain(
                        self.router_service.tenant_id,
                        job_id,
                        error_code=code,
                    )
                finally:
                    self._finish_receipt(
                        prepared,
                        run_id,
                        status="uncertain",
                        result_class="client_cancelled_after_dispatch",
                        code=code,
                        e2e_ms=(time.perf_counter() - started) * 1000,
                    )
            else:
                self._fail_preflight_job(job_id, prepared, run_id, run_started)
            raise
        except (httpx.TimeoutException, httpx.RequestError, TimeoutError) as exc:
            code = "provider_batch_submission_uncertain"
            if dispatched:
                self.repository.mark_provider_batch_uncertain(
                    self.router_service.tenant_id,
                    job_id,
                    error_code=code,
                )
                self._finish_receipt(
                    prepared,
                    run_id,
                    status="uncertain",
                    result_class="transport_uncertain",
                    code=code,
                    e2e_ms=(time.perf_counter() - started) * 1000,
                )
                raise RouterServiceError(
                    code,
                    "Batch 提交结果不确定；同一幂等键不会再次发送。",
                    status_code=502,
                ) from exc
            self._fail_preflight_job(job_id, prepared, run_id, run_started)
            raise RouterServiceError(
                "provider_batch_preflight_failed",
                "Managed Batch 在派发前失败。",
                status_code=502,
            ) from exc
        except (ValueError, json.JSONDecodeError) as exc:
            code = "provider_batch_invalid_submission_response"
            if dispatched:
                self.repository.mark_provider_batch_uncertain(
                    self.router_service.tenant_id,
                    job_id,
                    error_code=code,
                )
                self._finish_receipt(
                    prepared,
                    run_id,
                    status="uncertain",
                    result_class="protocol_uncertain",
                    code=code,
                    e2e_ms=(time.perf_counter() - started) * 1000,
                )
            else:
                self._fail_preflight_job(job_id, prepared, run_id, run_started)
            raise RouterServiceError(
                code,
                "OpenRouter Batch 返回无效提交响应；系统不会自动重放。",
                status_code=502,
            ) from exc

    async def poll(self, local_job_id: str) -> tuple[int, dict[str, object]]:
        clean_job_id = str(local_job_id or "").strip()
        if not self.is_local_job_id(clean_job_id):
            raise RouterServiceError(
                "provider_batch_local_id_invalid",
                "Managed Batch 任务编号无效。",
                status_code=422,
            )
        job = self.repository.get_provider_batch_job(
            self.router_service.tenant_id, clean_job_id
        )
        if job is None or str(job.get("purpose")) != "runtime":
            raise RouterServiceError(
                "provider_batch_job_not_found",
                "Managed Batch 任务不存在。",
                status_code=404,
            )
        if not job.get("upstream_batch_id"):
            return 200, self._stored_job_payload(job)
        return await self._poll_job(job, include_results=True)

    async def resume_pending_runtime_jobs(self) -> int:
        """Run one GET-only pass; never submit or replay a Batch POST."""

        resumed = 0
        for job in self.repository.list_provider_batch_jobs(
            self.router_service.tenant_id, limit=500
        ):
            if (
                str(job.get("purpose")) != "runtime"
                or str(job.get("status")) not in PENDING_BATCH_STATUSES
                or not job.get("upstream_batch_id")
            ):
                continue
            try:
                await self._poll_job(job, include_results=False)
                resumed += 1
            except (RouterServiceError, RouterRepositoryError, ValueError):
                continue
        return resumed

    async def _poll_job(
        self,
        job: dict[str, object],
        *,
        include_results: bool,
    ) -> tuple[int, dict[str, object]]:
        try:
            connection = self.repository.get_connection(
                self.router_service.tenant_id, str(job["connection_id"])
            )
            if connection.kind != "openrouter" or "batch" not in connection.scopes:
                raise RouterServiceError(
                    "provider_batch_connection_unavailable",
                    "该 Batch 的 OpenRouter 连接不再可用。",
                    status_code=409,
                )
            api_key = self.repository.resolve_api_key(
                self.router_service.tenant_id, connection.id
            )
        except (RouterRepositoryError, RouterCredentialUnavailable) as exc:
            raise RouterServiceError(
                "provider_batch_connection_unavailable",
                "该 Batch 的 OpenRouter 连接或凭据不可用。",
                status_code=503,
            ) from exc
        target = ProviderOperationTarget.create(
            provider_kind=connection.kind,
            connection_id=connection.id,
            base_url=connection.base_url,
            api_key=api_key,
        )
        operation = self._operation_for_endpoint(str(job["endpoint"]))
        try:
            authorized = await self.call_service.operation_transport.authorize(
                target,
                operation,
                upstream_batch_id=str(job["upstream_batch_id"]),
            )
        except ProviderEgressError as exc:
            raise RouterServiceError(
                exc.code,
                "该 Batch 的 OpenRouter 出口当前未获授权。",
                status_code=409,
            ) from exc
        try:
            async with self._client() as client:
                request = self.call_service.operation_transport.build_authorized_request(
                    client,
                    target,
                    authorized,
                    method="GET",
                )
                response = await self.call_service.operation_transport.send_authorized(
                    client, request
                )
                try:
                    if not 200 <= response.status_code < 300:
                        raise RouterServiceError(
                            self._http_error_code(response.status_code),
                            "无法刷新 OpenRouter Batch 状态。",
                            status_code=502,
                        )
                    response_payload = await self._read_json(response)
                finally:
                    await response.aclose()
        except (httpx.TimeoutException, httpx.RequestError, TimeoutError) as exc:
            raise RouterServiceError(
                "provider_batch_poll_unavailable",
                "无法刷新 OpenRouter Batch 状态，请稍后重试。",
                status_code=502,
            ) from exc
        try:
            upstream_id = self._upstream_id(response_payload)
            if upstream_id != str(job["upstream_batch_id"]):
                raise ValueError("provider_batch_upstream_id_mismatch")
            upstream_status = self._status(
                response_payload, default=str(job["status"])
            )
            self._counts(
                response_payload, int(job.get("request_count") or 0)
            )
        except ValueError as exc:
            raise RouterServiceError(
                "provider_batch_invalid_poll_response",
                "OpenRouter Batch 返回了不一致的任务状态。",
                status_code=502,
            ) from exc
        actual_model = response_payload.get("model")
        if (
            isinstance(actual_model, str)
            and actual_model
            and not provider_operation_batch_model_matches(
                provider_kind="openrouter",
                requested_model=str(job["model_id"]),
                actual_model=actual_model,
            )
        ):
            response_payload = {**response_payload, "status": "failed"}
            upstream_status = "failed"
            response_payload["_modelmirror_error_code"] = (
                "provider_batch_model_mismatch"
            )
        latest = self._update_job(job, response_payload, upstream_status)
        return 200, self._response_payload(
            latest,
            response_payload,
            include_results=include_results,
        )

    def _update_job(
        self,
        job: dict[str, object],
        response_payload: Mapping[str, object],
        status: str,
    ) -> dict[str, object]:
        counts = self._counts(response_payload, int(job.get("request_count") or 0))
        usage, cost_value = self._usage(response_payload)
        error_code = (
            str(response_payload.get("_modelmirror_error_code"))
            if response_payload.get("_modelmirror_error_code")
            else ("provider_batch_upstream_failed" if status == "failed" else None)
        )
        if status not in PENDING_BATCH_STATUSES | TERMINAL_BATCH_STATUSES:
            status = "failed"
            error_code = "provider_batch_invalid_status"
        try:
            return self.repository.update_provider_batch_job(
                self.router_service.tenant_id,
                str(job["id"]),
                status=status,
                completed_count=counts["completed"],
                failed_count=counts["failed"],
                usage=usage,
                cost_value=cost_value,
                cost_currency="USD" if cost_value is not None else None,
                error_code=error_code,
            )
        except RouterRepositoryError as exc:
            if str(exc) == "provider_batch_job_not_pollable":
                current = self.repository.get_provider_batch_job(
                    self.router_service.tenant_id, str(job["id"])
                )
                if current is not None:
                    return current
            raise

    def _finish_receipt(
        self,
        prepared: ProviderWorkloadPreparedCall | None,
        run_id: str,
        *,
        status: Literal["passed", "failed", "uncertain", "cancelled"],
        result_class: str,
        code: str | None,
        actual_model: str | None = None,
        e2e_ms: float | None = None,
    ) -> None:
        if prepared is not None:
            self.call_service.complete_call(
                prepared,
                status=status,
                result_class=result_class,
                error_code=code,
                actual_model=actual_model,
                e2e_ms=e2e_ms,
            )
        self.call_service.complete_run(
            run_id,
            status=status,
            result_class=result_class,
            reason_codes=[code] if code else [],
        )

    def _fail_preflight_job(
        self,
        job_id: str,
        prepared: ProviderWorkloadPreparedCall | None,
        run_id: str,
        run_started: bool,
    ) -> None:
        code = "provider_batch_preflight_failed"
        try:
            self.repository.fail_provider_batch_submission(
                self.router_service.tenant_id, job_id, error_code=code
            )
        except RouterRepositoryError:
            pass
        if run_started:
            self._finish_receipt(
                prepared,
                run_id,
                status="failed",
                result_class="preflight_error",
                code=code,
            )

    def _client(self) -> httpx.AsyncClient:
        if self._client_factory is not None:
            return self._client_factory()
        return httpx.AsyncClient(**ProviderOperationTransport.client_kwargs())

    @staticmethod
    def is_local_job_id(value: str) -> bool:
        return (
            value.startswith("mmbatch_")
            and len(value) == len("mmbatch_") + 32
            and all(character in "0123456789abcdef" for character in value[8:])
        )

    @staticmethod
    def _operation_for_endpoint(endpoint: str) -> str:
        if endpoint == "/v1/chat/completions":
            return "openrouter_batch_chat"
        if endpoint == "/v1/embeddings":
            return "openrouter_batch_embeddings"
        raise RouterServiceError(
            "provider_batch_endpoint_invalid",
            "Managed Batch endpoint 不受支持。",
            status_code=422,
        )

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _fingerprint(value: Mapping[str, object]) -> str:
        return hashlib.sha256(
            json.dumps(
                dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
        ).hexdigest()

    @staticmethod
    async def _read_json(response: httpx.Response) -> dict[str, object]:
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
            total += len(chunk)
            if total > MAX_BATCH_RESPONSE_BYTES:
                raise ValueError("provider_batch_response_too_large")
            chunks.append(chunk)
        parsed = json.loads(b"".join(chunks))
        if not isinstance(parsed, dict):
            raise ValueError("provider_batch_invalid_json_response")
        return parsed

    @staticmethod
    def _upstream_id(payload: Mapping[str, object]) -> str:
        value = payload.get("id")
        clean = str(value or "").strip()
        if not clean or len(clean) > 256 or any(
            character
            not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-"
            for character in clean
        ):
            raise ValueError("provider_batch_invalid_upstream_id")
        return clean

    @staticmethod
    def _status(payload: Mapping[str, object], *, default: str) -> str:
        value = str(payload.get("status") or default)
        if value not in PENDING_BATCH_STATUSES | TERMINAL_BATCH_STATUSES:
            raise ValueError("provider_batch_invalid_status")
        return value

    @staticmethod
    def _counts(payload: Mapping[str, object], total: int) -> dict[str, int]:
        raw = payload.get("request_counts")
        if raw is None:
            return {"total": max(0, total), "completed": 0, "failed": 0}
        if not isinstance(raw, dict):
            raise ValueError("provider_batch_invalid_request_counts")
        values = raw

        def integer(name: str) -> int:
            value = values.get(name)
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            ):
                raise ValueError("provider_batch_invalid_request_counts")
            return value

        counts = {
            "total": integer("total"),
            "completed": integer("completed"),
            "failed": integer("failed"),
        }
        if (
            counts["total"] != max(0, total)
            or counts["completed"] + counts["failed"] > counts["total"]
        ):
            raise ValueError("provider_batch_inconsistent_request_counts")
        return counts

    @staticmethod
    def _usage(payload: Mapping[str, object]) -> tuple[dict[str, object], str | None]:
        raw = payload.get("usage")
        if not isinstance(raw, dict):
            return {}, None
        usage: dict[str, object] = {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = raw.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                usage[key] = value
        if isinstance(raw.get("is_byok"), bool):
            usage["is_byok"] = raw["is_byok"]
        cost_value: str | None = None
        value = raw.get("cost")
        if isinstance(value, (int, float, str)) and not isinstance(value, bool):
            try:
                decimal = Decimal(str(value))
                if decimal.is_finite() and decimal >= 0:
                    cost_value = format(decimal, "f")
                    usage["cost"] = float(decimal)
            except (InvalidOperation, ValueError, OverflowError):
                pass
        return usage, cost_value

    def _stored_job_payload(self, job: Mapping[str, object]) -> dict[str, object]:
        usage_raw = job.get("usage_json")
        try:
            usage = json.loads(str(usage_raw or "{}"))
        except json.JSONDecodeError:
            usage = {}
        return {
            "id": str(job["id"]),
            "object": "batch",
            "endpoint": str(job["endpoint"]),
            "model": str(job["model_id"]),
            "completion_window": "24h",
            "status": str(job["status"]),
            "created_at": self._epoch(job.get("created_at")),
            "finalized_at": self._epoch(job.get("completed_at")) if job.get("completed_at") else None,
            "request_counts": {
                "total": int(job.get("request_count") or 0),
                "completed": int(job.get("completed_count") or 0),
                "failed": int(job.get("failed_count") or 0),
            },
            "usage": usage or None,
            "results": None,
            "error": self._safe_job_error(job),
            "billing_authoritative": False,
        }

    def _response_payload(
        self,
        job: Mapping[str, object],
        upstream: Mapping[str, object],
        *,
        include_results: bool = True,
    ) -> dict[str, object]:
        payload = self._stored_job_payload(job)
        payload["status"] = str(upstream.get("status") or job["status"])
        payload["request_counts"] = self._counts(
            upstream, int(job.get("request_count") or 0)
        )
        usage, _cost = self._usage(upstream)
        payload["usage"] = usage or None
        finalized = upstream.get("finalized_at")
        if isinstance(finalized, (int, float)) and not isinstance(finalized, bool):
            payload["finalized_at"] = finalized
        results = upstream.get("results")
        payload["results"] = results if include_results and isinstance(results, list) else None
        if upstream.get("error") and not payload["error"]:
            payload["error"] = {
                "code": "provider_batch_upstream_failed",
                "message": "OpenRouter Batch 返回失败状态。",
            }
        return payload

    @staticmethod
    def _safe_job_error(job: Mapping[str, object]) -> dict[str, str] | None:
        code = str(job.get("error_code") or "").strip()
        if not code:
            return None
        messages = {
            "provider_batch_submission_uncertain": "Batch 提交结果不确定；系统不会自动重放。",
            "provider_batch_submission_cancelled": "Batch 派发后被取消，结果不确定；系统不会自动重放。",
            "server_restarted": "Server 在提交结果确认前重启；系统不会自动重放。",
            "provider_batch_model_mismatch": "Batch 实际模型与请求模型不一致。",
        }
        return {"code": code, "message": messages.get(code, "Batch 运行未成功完成。")}

    @staticmethod
    def _epoch(value: object) -> int:
        try:
            return int(datetime.fromisoformat(str(value)).timestamp())
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _http_error_code(status_code: int) -> str:
        if status_code in {401, 402, 403, 404, 409, 429}:
            return f"provider_batch_http_{status_code}"
        if 500 <= status_code < 600:
            return "provider_batch_http_5xx"
        return "provider_batch_http_error"
