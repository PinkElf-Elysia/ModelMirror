from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from .provider_operations import (
    PROVIDER_OPERATION_CONTRACT_VERSION,
    ProviderOperationEndpointResolver,
    ProviderOperationTarget,
    ProviderOperationTransport,
    provider_operation_model_matches,
)
from .service import ModelRouterService, RouterServiceError
from .workload_control import (
    MAX_WORKLOAD_UNARY_RESPONSE_BYTES,
    WORKLOAD_RESPONSE_CHUNK_BYTES,
    PROVIDER_WORKLOAD_CONTRACT_VERSION,
    ProviderWorkloadCallService,
    ProviderWorkloadPreparedCall,
)


EMBEDDING_SPACE_CONTRACT_VERSION = "modelmirror-provider-embedding-space-v1"
RAG_EMBEDDING_ENTRY_ID = "rag_embedding"
EMBEDDING_RESPONSE_FIXED_OVERHEAD_BYTES = 16 * 1024
EMBEDDING_RESPONSE_BYTES_PER_COORDINATE = 32
EmbeddingRoutingMode = Literal["legacy", "managed_required", "degraded_required"]


class ManagedRagEmbeddingError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 409,
        receipt: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.receipt = receipt


@dataclass(frozen=True, slots=True)
class EmbeddingSpaceIdentity:
    contract_version: str
    provider_kind: str
    endpoint_identity_sha256: str
    model_id: str
    vector_dimension: int
    fingerprint: str

    def payload(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "provider_kind": self.provider_kind,
            "endpoint_identity_sha256": self.endpoint_identity_sha256,
            "model_id": self.model_id,
            "vector_dimension": self.vector_dimension,
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True, slots=True)
class ManagedEmbeddingResult:
    vectors: list[list[float]]
    identity: EmbeddingSpaceIdentity
    actual_model: str
    receipt: dict[str, Any]


@dataclass(slots=True)
class _EmbeddingCallReceipt:
    call_sequence: int
    model_id: str
    dispatched: bool
    status: str
    actual_model: str | None = None
    error_code: str | None = None
    e2e_ms: float | None = None
    prompt_tokens: int | None = None
    total_tokens: int | None = None

    def payload(self) -> dict[str, object | None]:
        return {
            "call_sequence": self.call_sequence,
            "model_id": self.model_id,
            "dispatched": self.dispatched,
            "status": self.status,
            "actual_model": self.actual_model,
            "error_code": self.error_code,
            "e2e_ms": self.e2e_ms,
            "prompt_tokens": self.prompt_tokens,
            "total_tokens": self.total_tokens,
        }


class ManagedRagEmbeddingGateway:
    """Managed Embedding adapter with one-dispatch receipts and space pinning."""

    def __init__(
        self,
        call_service: ProviderWorkloadCallService,
        *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self.call_service = call_service
        self._client_factory = client_factory

    @classmethod
    def for_router(
        cls,
        router_service: ModelRouterService,
        *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> "ManagedRagEmbeddingGateway":
        return cls(
            ProviderWorkloadCallService(router_service),
            client_factory=client_factory,
        )

    def routing_mode(self) -> EmbeddingRoutingMode:
        control = self.call_service.control
        if not control.feature_enabled(RAG_EMBEDDING_ENTRY_ID):
            return "legacy"
        policy = control.get_policy(RAG_EMBEDDING_ENTRY_ID)
        if policy.configured_status == "legacy":
            return "legacy"
        if policy.effective_status == "managed_required":
            return "managed_required"
        return "degraded_required"

    def qualification(self, model_id: str) -> EmbeddingSpaceIdentity:
        clean_model = str(model_id or "").strip()
        policy = self.call_service.control.get_policy(RAG_EMBEDDING_ENTRY_ID)
        if policy.effective_status != "managed_required":
            raise ManagedRagEmbeddingError(
                "provider_workload_policy_not_active",
                "RAG Embedding 的 Managed Provider 策略未就绪。",
            )
        binding = next(
            (
                item
                for item in policy.bindings
                if item.execution_shape == "embedding_vectors"
                and item.model_id == clean_model
                and item.valid
            ),
            None,
        )
        if binding is None:
            raise ManagedRagEmbeddingError(
                "provider_workload_binding_missing",
                "RAG Embedding 缺少该精确模型的合格 Binding。",
            )
        repository = self.call_service.repository
        certification = repository.get_workload_certification(
            self.call_service.router_service.tenant_id,
            binding.certification_id,
        )
        dimension = int((certification or {}).get("vector_dimension") or 0)
        if dimension <= 0:
            raise ManagedRagEmbeddingError(
                "provider_embedding_dimension_unqualified",
                "RAG Embedding 资格没有经过验证的向量维度。",
            )
        connection = repository.get_connection(
            self.call_service.router_service.tenant_id,
            binding.connection_id,
        )
        endpoint = ProviderOperationEndpointResolver.resolve(
            provider_kind=connection.kind,
            base_url=connection.base_url,
        ).embeddings_url
        return self.space_identity(
            provider_kind=connection.kind,
            endpoint=endpoint,
            model_id=clean_model,
            vector_dimension=dimension,
        )

    @staticmethod
    def response_bounded_batch_size(
        *,
        vector_dimension: int,
        requested_batch_size: int,
    ) -> int:
        """Bound a batch so one JSON vector response remains under the read cap."""

        clean_dimension = max(1, int(vector_dimension))
        clean_requested = max(1, int(requested_batch_size))
        available_bytes = max(
            1,
            MAX_WORKLOAD_UNARY_RESPONSE_BYTES
            - EMBEDDING_RESPONSE_FIXED_OVERHEAD_BYTES,
        )
        estimated_item_bytes = (
            clean_dimension * EMBEDDING_RESPONSE_BYTES_PER_COORDINATE
        ) + 512
        return max(
            1,
            min(clean_requested, available_bytes // estimated_item_bytes),
        )

    @staticmethod
    def space_identity(
        *,
        provider_kind: str,
        endpoint: str,
        model_id: str,
        vector_dimension: int,
    ) -> EmbeddingSpaceIdentity:
        endpoint_digest = hashlib.sha256(endpoint.encode("utf-8")).hexdigest()
        material = {
            "contract_version": EMBEDDING_SPACE_CONTRACT_VERSION,
            "provider_kind": str(provider_kind),
            "endpoint_identity_sha256": endpoint_digest,
            "model_id": str(model_id),
            "vector_dimension": int(vector_dimension),
            "provider_operation_contract_version": PROVIDER_OPERATION_CONTRACT_VERSION,
        }
        fingerprint = hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        return EmbeddingSpaceIdentity(
            contract_version=EMBEDDING_SPACE_CONTRACT_VERSION,
            provider_kind=str(provider_kind),
            endpoint_identity_sha256=endpoint_digest,
            model_id=str(model_id),
            vector_dimension=int(vector_dimension),
            fingerprint=fingerprint,
        )

    def start_index_run(self, job_id: str) -> "ManagedRagEmbeddingRun":
        parent_reference = self.index_parent_reference(job_id)
        try:
            run_id = self.call_service.start_stable_run(
                RAG_EMBEDDING_ENTRY_ID,
                parent_run_reference=parent_reference,
            )
        except RouterServiceError as exc:
            raise ManagedRagEmbeddingError(
                exc.code,
                "该索引 Job 已存在 Managed Embedding 调用证据，系统不会重放。",
                status_code=exc.status_code,
                receipt=self.blocked_receipt(exc.code),
            ) from exc
        return ManagedRagEmbeddingRun(self, run_id)

    def start_query_run(self, version_id: str) -> "ManagedRagEmbeddingRun":
        run_id = self.call_service.start_run(
            RAG_EMBEDDING_ENTRY_ID,
            parent_run_reference=(
                f"rag_embedding:query:{str(version_id)[:200]}:{uuid.uuid4().hex}"
            ),
        )
        return ManagedRagEmbeddingRun(self, run_id)

    def index_run_status(self, job_id: str) -> str | None:
        run_id = self.call_service.stable_run_id(
            RAG_EMBEDDING_ENTRY_ID,
            self.index_parent_reference(job_id),
        )
        try:
            row = self.call_service.repository.get_workload_run(
                self.call_service.router_service.tenant_id,
                run_id,
            )
        except Exception:
            return None
        return str(row.get("status") or "") or None

    @staticmethod
    def index_parent_reference(job_id: str) -> str:
        return f"rag_embedding:index:{str(job_id)[:200]}"

    @staticmethod
    def blocked_receipt(reason_code: str) -> dict[str, Any]:
        return {
            "contract_version": PROVIDER_WORKLOAD_CONTRACT_VERSION,
            "entry_id": RAG_EMBEDDING_ENTRY_ID,
            "routing_mode": "managed_required",
            "run_reference": "blocked_before_dispatch",
            "status": "failed",
            "call_count": 0,
            "reason_codes": [reason_code],
            "calls": [],
        }


class ManagedRagEmbeddingRun:
    def __init__(
        self,
        gateway: ManagedRagEmbeddingGateway,
        run_id: str,
    ) -> None:
        self.gateway = gateway
        self.run_id = run_id
        self.status = "running"
        self.reason_codes: list[str] = []
        self.calls: list[_EmbeddingCallReceipt] = []

    async def embed(
        self,
        texts: Sequence[str],
        *,
        model_id: str,
        logical_call_key: str,
        call_sequence: int,
        expected_space_fingerprint: str | None = None,
    ) -> ManagedEmbeddingResult:
        clean_texts = [str(item) for item in texts]
        if not clean_texts:
            raise ManagedRagEmbeddingError(
                "provider_embedding_inputs_required",
                "Managed Embedding 调用缺少输入。",
                status_code=422,
                receipt=self.receipt_summary(),
            )
        prepared: ProviderWorkloadPreparedCall | None = None
        dispatched = False
        started = time.perf_counter()
        try:
            qualification = self.gateway.qualification(model_id)
            if (
                expected_space_fingerprint
                and qualification.fingerprint != expected_space_fingerprint
            ):
                raise ManagedRagEmbeddingError(
                    "provider_embedding_space_changed",
                    "当前 Managed Embedding 空间与索引版本不一致。",
                )
            prepared = await self.gateway.call_service.prepare_call(
                run_id=self.run_id,
                entry_id=RAG_EMBEDDING_ENTRY_ID,
                execution_shape="embedding_vectors",
                model_id=model_id,
                logical_call_key=logical_call_key,
                call_sequence=call_sequence,
            )
            target = prepared.operation_target
            if target is None:
                raise ManagedRagEmbeddingError(
                    "provider_embedding_operation_target_missing",
                    "Managed Embedding 目标解析失败。",
                )
            payload = {
                "model": model_id,
                "input": clean_texts,
                "encoding_format": "float",
            }
            async with self._client() as client:
                request = self.gateway.call_service.operation_transport.build_authorized_request(
                    client,
                    target,
                    prepared.authorized_target,
                    method="POST",
                    payload=payload,
                )
                self.gateway.call_service.mark_dispatched(prepared)
                dispatched = True
                response = await self.gateway.call_service.operation_transport.send_authorized(
                    client, request
                )
                try:
                    if not 200 <= response.status_code < 300:
                        raise ManagedRagEmbeddingError(
                            self._http_error_code(response.status_code),
                            "Managed Embedding Provider 返回失败状态。",
                            status_code=502,
                        )
                    response_payload = await self._read_json(response)
                finally:
                    await response.aclose()
            vectors, actual_model, prompt_tokens, total_tokens = self._vectors(
                response_payload,
                provider_kind=target.provider_kind,
                requested_model=model_id,
                expected_count=len(clean_texts),
            )
            actual_dimension = len(vectors[0])
            identity = self.gateway.space_identity(
                provider_kind=target.provider_kind,
                endpoint=target.endpoints.embeddings_url,
                model_id=model_id,
                vector_dimension=actual_dimension,
            )
            if identity.fingerprint != qualification.fingerprint:
                raise ManagedRagEmbeddingError(
                    "provider_embedding_dimension_changed",
                    "Managed Embedding 实际维度与认证空间不一致。",
                    status_code=502,
                )
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.gateway.call_service.complete_call(
                prepared,
                status="passed",
                result_class="success",
                actual_model=actual_model,
                e2e_ms=elapsed_ms,
                prompt_tokens=prompt_tokens,
                total_tokens=total_tokens,
            )
            self.calls.append(
                _EmbeddingCallReceipt(
                    call_sequence=call_sequence,
                    model_id=model_id,
                    actual_model=actual_model,
                    dispatched=True,
                    status="passed",
                    e2e_ms=elapsed_ms,
                    prompt_tokens=prompt_tokens,
                    total_tokens=total_tokens,
                )
            )
            return ManagedEmbeddingResult(
                vectors=vectors,
                identity=identity,
                actual_model=actual_model,
                receipt=self.receipt_summary(),
            )
        except asyncio.CancelledError:
            self._fail_call(
                prepared,
                call_sequence=call_sequence,
                model_id=model_id,
                dispatched=dispatched,
                status="cancelled",
                result_class="client_cancelled",
                code="provider_embedding_cancelled",
            )
            raise
        except ManagedRagEmbeddingError as exc:
            self._fail_call(
                prepared,
                call_sequence=call_sequence,
                model_id=model_id,
                dispatched=dispatched,
                status="failed",
                result_class=("provider_error" if dispatched else "preflight_error"),
                code=exc.code,
            )
            exc.receipt = self.receipt_summary()
            raise
        except (httpx.TimeoutException, TimeoutError) as exc:
            code = "provider_embedding_timeout"
            self._fail_call(
                prepared,
                call_sequence=call_sequence,
                model_id=model_id,
                dispatched=dispatched,
                status="uncertain" if dispatched else "failed",
                result_class="transport_error",
                code=code,
            )
            raise ManagedRagEmbeddingError(
                code,
                "Managed Embedding Provider 请求超时。",
                status_code=504,
                receipt=self.receipt_summary(),
            ) from exc
        except httpx.HTTPError as exc:
            code = "provider_embedding_transport_error"
            self._fail_call(
                prepared,
                call_sequence=call_sequence,
                model_id=model_id,
                dispatched=dispatched,
                status="uncertain" if dispatched else "failed",
                result_class="transport_error",
                code=code,
            )
            raise ManagedRagEmbeddingError(
                code,
                "Managed Embedding Provider 连接失败。",
                status_code=502,
                receipt=self.receipt_summary(),
            ) from exc
        except RouterServiceError as exc:
            self._fail_call(
                prepared,
                call_sequence=call_sequence,
                model_id=model_id,
                dispatched=dispatched,
                status="failed",
                result_class="control_plane_error",
                code=exc.code,
            )
            raise ManagedRagEmbeddingError(
                exc.code,
                "Managed Embedding 控制面在派发前失败关闭。",
                status_code=exc.status_code,
                receipt=self.receipt_summary(),
            ) from exc
        except Exception as exc:
            code = "provider_embedding_internal_error"
            self._fail_call(
                prepared,
                call_sequence=call_sequence,
                model_id=model_id,
                dispatched=dispatched,
                status="uncertain" if dispatched else "failed",
                result_class="internal_error",
                code=code,
            )
            raise ManagedRagEmbeddingError(
                code,
                "Managed Embedding 响应处理失败。",
                status_code=502,
                receipt=self.receipt_summary(),
            ) from exc

    def finish_success(self) -> dict[str, Any]:
        if self.status == "running":
            self.status = "passed"
            self.gateway.call_service.complete_run(
                self.run_id,
                status="passed",
                result_class="success",
            )
        return self.receipt_summary()

    def receipt_summary(self) -> dict[str, Any]:
        return {
            "contract_version": PROVIDER_WORKLOAD_CONTRACT_VERSION,
            "entry_id": RAG_EMBEDDING_ENTRY_ID,
            "routing_mode": "managed_required",
            "run_reference": self.run_id,
            "status": self.status,
            "call_count": len(self.calls),
            "reason_codes": list(dict.fromkeys(self.reason_codes)),
            "calls": [item.payload() for item in self.calls],
        }

    def _fail_call(
        self,
        prepared: ProviderWorkloadPreparedCall | None,
        *,
        call_sequence: int,
        model_id: str,
        dispatched: bool,
        status: str,
        result_class: str,
        code: str,
    ) -> None:
        if prepared is not None:
            try:
                self.gateway.call_service.complete_call(
                    prepared,
                    status=status,
                    result_class=result_class,
                    error_code=code,
                )
            except Exception:
                pass
        self.calls.append(
            _EmbeddingCallReceipt(
                call_sequence=call_sequence,
                model_id=model_id,
                dispatched=dispatched,
                status=status,
                error_code=code,
            )
        )
        self.reason_codes.append(code)
        self.status = status
        try:
            self.gateway.call_service.complete_run(
                self.run_id,
                status=status,
                result_class=result_class,
                reason_codes=self.reason_codes,
            )
        except Exception:
            pass

    def _client(self) -> httpx.AsyncClient:
        if self.gateway._client_factory is not None:
            return self.gateway._client_factory()
        return httpx.AsyncClient(
            **ProviderOperationTransport.client_kwargs()
        )

    @staticmethod
    async def _read_json(response: httpx.Response) -> dict[str, object]:
        chunks: list[bytes] = []
        total_bytes = 0
        async for chunk in response.aiter_bytes(
            chunk_size=WORKLOAD_RESPONSE_CHUNK_BYTES
        ):
            total_bytes += len(chunk)
            if total_bytes > MAX_WORKLOAD_UNARY_RESPONSE_BYTES:
                raise ManagedRagEmbeddingError(
                    "provider_embedding_response_too_large",
                    "Managed Embedding Provider 响应超过安全上限。",
                    status_code=502,
                )
            chunks.append(chunk)
        try:
            value = json.loads(b"".join(chunks))
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
            raise ManagedRagEmbeddingError(
                "provider_embedding_invalid_json",
                "Managed Embedding Provider 返回无效 JSON。",
                status_code=502,
            ) from exc
        if not isinstance(value, dict):
            raise ManagedRagEmbeddingError(
                "provider_embedding_invalid_json",
                "Managed Embedding Provider 返回无效 JSON。",
                status_code=502,
            )
        return value

    @staticmethod
    def _vectors(
        response_payload: Mapping[str, object],
        *,
        provider_kind: str,
        requested_model: str,
        expected_count: int,
    ) -> tuple[list[list[float]], str, int | None, int | None]:
        actual_model = response_payload.get("model")
        if not isinstance(actual_model, str) or not provider_operation_model_matches(
            provider_kind=provider_kind,
            requested_model=requested_model,
            actual_model=actual_model,
        ):
            raise ManagedRagEmbeddingError(
                "provider_embedding_model_mismatch",
                "Managed Embedding Provider 实际模型与请求不一致。",
                status_code=502,
            )
        data = response_payload.get("data")
        if not isinstance(data, list) or len(data) != expected_count:
            raise ManagedRagEmbeddingError(
                "provider_embedding_vector_count_mismatch",
                "Managed Embedding Provider 返回的向量数量不一致。",
                status_code=502,
            )
        vectors_by_index: dict[int, list[float]] = {}
        dimensions: set[int] = set()
        for item in data:
            if not isinstance(item, dict):
                raise ManagedRagEmbeddingError(
                    "provider_embedding_invalid_vector",
                    "Managed Embedding Provider 返回无效向量。",
                    status_code=502,
                )
            index = item.get("index")
            raw_vector = item.get("embedding")
            if (
                not isinstance(index, int)
                or isinstance(index, bool)
                or index in vectors_by_index
                or not isinstance(raw_vector, list)
                or not raw_vector
            ):
                raise ManagedRagEmbeddingError(
                    "provider_embedding_invalid_vector",
                    "Managed Embedding Provider 返回无效向量。",
                    status_code=502,
                )
            if any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                for value in raw_vector
            ):
                raise ManagedRagEmbeddingError(
                    "provider_embedding_non_finite_vector",
                    "Managed Embedding Provider 返回非有限数值。",
                    status_code=502,
                )
            vector = [float(value) for value in raw_vector]
            vectors_by_index[index] = vector
            dimensions.add(len(vector))
        if set(vectors_by_index) != set(range(expected_count)):
            raise ManagedRagEmbeddingError(
                "provider_embedding_invalid_index",
                "Managed Embedding Provider 返回的向量索引不完整。",
                status_code=502,
            )
        if len(dimensions) != 1:
            raise ManagedRagEmbeddingError(
                "provider_embedding_dimension_mismatch",
                "Managed Embedding Provider 返回的向量维度不一致。",
                status_code=502,
            )
        usage = response_payload.get("usage")
        usage_mapping = usage if isinstance(usage, Mapping) else {}
        return (
            [vectors_by_index[index] for index in range(expected_count)],
            actual_model,
            ManagedRagEmbeddingRun._integer(usage_mapping.get("prompt_tokens")),
            ManagedRagEmbeddingRun._integer(usage_mapping.get("total_tokens")),
        )

    @staticmethod
    def _integer(value: object) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _http_error_code(status_code: int) -> str:
        if status_code in {401, 402, 403, 404, 413, 422, 429}:
            return f"provider_embedding_http_{status_code}"
        if 500 <= status_code <= 599:
            return "provider_embedding_http_5xx"
        return "provider_embedding_http_error"
