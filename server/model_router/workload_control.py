from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Mapping

import httpx

from .chat_control import ProviderChatControlService
from .egress import AuthorizedProviderTarget, ProviderEgressError
from .provider_catalog import ProviderCatalogService
from .provider_chat import ProviderChatTarget, ProviderChatTransport
from .provider_operations import (
    ProviderOperationTarget,
    ProviderOperationTransport,
)
from .repository import RouterCredentialUnavailable, RouterRepositoryError
from .schemas import (
    ProviderWorkloadActivationRequest,
    ProviderWorkloadBindingSummary,
    ProviderWorkloadCertificationChecks,
    ProviderWorkloadCertificationListResponse,
    ProviderWorkloadCertificationRequest,
    ProviderWorkloadCertificationSummary,
    ProviderWorkloadDeactivationRequest,
    ProviderWorkloadEntryId,
    ProviderWorkloadExecutionShape,
    ProviderWorkloadOverview,
    ProviderWorkloadPolicyListResponse,
    ProviderWorkloadPolicyResponse,
    ProviderWorkloadPolicyUpdate,
    ProviderWorkloadPublicStatus,
    ProviderWorkloadCallSummary,
    ProviderWorkloadReceiptsResponse,
    ProviderWorkloadRunSummary,
    RouterConnection,
)
from .service import ModelRouterService, RouterServiceError


PROVIDER_WORKLOAD_CONTRACT_VERSION = "modelmirror-provider-workload-routing-v1"
PROVIDER_WORKLOAD_CERTIFICATION_ENABLED_ENV = (
    "MODEL_MIRROR_PROVIDER_CHAT_CERTIFICATION_ENABLED"
)
SYNTHETIC_UNARY_PROMPT = "Reply with OK."
SYNTHETIC_JSON_PROMPT = 'Return exactly one JSON object: {"ok":true}'
SYNTHETIC_EMBEDDING_INPUTS = (
    "ModelMirror embedding certification one.",
    "ModelMirror embedding certification two.",
)
SYNTHETIC_RERANK_QUERY = "ModelMirror provider routing certification"
SYNTHETIC_RERANK_DOCUMENTS = (
    "ModelMirror routes requests through an explicit provider control plane.",
    "This document is unrelated to provider routing.",
    "Managed bindings select one exact provider model.",
)
MAX_WORKLOAD_UNARY_RESPONSE_BYTES = 1024 * 1024
MAX_WORKLOAD_SSE_EVENT_BYTES = 256 * 1024
MAX_WORKLOAD_STREAM_BYTES = 4 * 1024 * 1024
WORKLOAD_RESPONSE_CHUNK_BYTES = 64 * 1024
ENTRY_FEATURE_FLAGS: dict[ProviderWorkloadEntryId, tuple[str, ...]] = {
    "agent_shadow": ("MODEL_CONTROL_AGENT_SHADOW_ENABLED",),
    "meta_agent": ("MODEL_CONTROL_META_AGENT_ENABLED",),
    "workflow_interactive_llm": ("MODEL_CONTROL_WORKFLOW_LLM_ENABLED",),
    "workflow_deployment_llm": (
        "MODEL_CONTROL_WORKFLOW_LLM_ENABLED",
        "MODEL_CONTROL_WORKFLOW_DEPLOYMENT_ENABLED",
    ),
    "workflow_interactive_agent": ("MODEL_CONTROL_WORKFLOW_AGENT_ENABLED",),
    "workflow_deployment_agent": (
        "MODEL_CONTROL_WORKFLOW_AGENT_ENABLED",
        "MODEL_CONTROL_WORKFLOW_DEPLOYMENT_ENABLED",
    ),
    "xpert": ("MODEL_CONTROL_XPERT_ENABLED",),
    "xpert_app": ("MODEL_CONTROL_XPERT_APP_ENABLED",),
    "expert_team_planner": ("MODEL_CONTROL_EXPERT_TEAM_PLANNER_ENABLED",),
    "expert_team_dag": ("MODEL_CONTROL_EXPERT_TEAM_DAG_ENABLED",),
    "fusion": ("MODEL_CONTROL_FUSION_ENABLED",),
    "route_agent": ("MODEL_CONTROL_ROUTE_AGENT_ENABLED",),
    "team_chat": ("MODEL_CONTROL_TEAM_CHAT_ENABLED",),
    "rag_query_generate": ("MODEL_CONTROL_RAG_QUERY_ENABLED",),
    "rag_processor_generate": ("MODEL_CONTROL_RAG_PROCESSOR_ENABLED",),
    "rag_embedding": ("MODEL_CONTROL_RAG_EMBEDDING_ENABLED",),
    "rag_rerank": ("MODEL_CONTROL_RAG_RERANK_ENABLED",),
    "skill_rerank": ("MODEL_CONTROL_SKILL_RERANK_ENABLED",),
    "openrouter_batch": ("MODEL_CONTROL_OPENROUTER_BATCH_ENABLED",),
}

ENTRY_ALLOWED_SHAPES: dict[
    ProviderWorkloadEntryId, frozenset[ProviderWorkloadExecutionShape]
] = {
    "agent_shadow": frozenset({"chat_tools"}),
    "meta_agent": frozenset({"chat_json_object"}),
    "workflow_interactive_llm": frozenset(
        {"chat_text", "chat_text_unary", "chat_json_object"}
    ),
    "workflow_deployment_llm": frozenset(
        {"chat_text", "chat_text_unary", "chat_json_object"}
    ),
    "workflow_interactive_agent": frozenset(
        {"chat_text", "chat_tools", "chat_json_object"}
    ),
    "workflow_deployment_agent": frozenset(
        {"chat_text", "chat_tools", "chat_json_object"}
    ),
    "xpert": frozenset({"chat_text", "chat_tools", "chat_json_object"}),
    "xpert_app": frozenset({"chat_text", "chat_tools", "chat_json_object"}),
    # The Agency planner emits a YAML plan through a non-streaming text request.
    "expert_team_planner": frozenset({"chat_text_unary"}),
    # The Agency DAG executes expert text steps and JSON acceptance checks.
    # These qualifications are independent and both must be explicit.
    "expert_team_dag": frozenset({"chat_text_unary", "chat_json_object"}),
    "fusion": frozenset({"chat_text", "fusion_native"}),
    "route_agent": frozenset({"chat_text"}),
    "team_chat": frozenset({"chat_text"}),
    "rag_query_generate": frozenset({"chat_text_unary"}),
    "rag_processor_generate": frozenset({"chat_json_object"}),
    "rag_embedding": frozenset({"embedding_vectors"}),
    "rag_rerank": frozenset({"rerank_documents"}),
    "skill_rerank": frozenset({"rerank_documents"}),
    "openrouter_batch": frozenset(
        {"openrouter_batch_chat", "openrouter_batch_embeddings"}
    ),
}

ENTRY_ALLOWED_LOCAL_FALLBACKS: dict[ProviderWorkloadEntryId, frozenset[str]] = {
    entry_id: frozenset({"none"}) for entry_id in ENTRY_FEATURE_FLAGS
}
ENTRY_ALLOWED_LOCAL_FALLBACKS.update(
    {
        "rag_query_generate": frozenset({"none", "extractive"}),
        "rag_rerank": frozenset({"none", "lexical"}),
        "skill_rerank": frozenset({"none", "lexical"}),
    }
)

# R6A deliberately provides only the control-plane foundation. Each later data-plane
# PR adds its entry here after its dedicated tests and real smoke are complete.
DATA_PLANE_INTEGRATED_ENTRIES: frozenset[ProviderWorkloadEntryId] = frozenset(
    {
        "agent_shadow",
        "meta_agent",
        "workflow_interactive_llm",
        "workflow_deployment_llm",
        "workflow_interactive_agent",
        "workflow_deployment_agent",
        "xpert",
        "xpert_app",
        "expert_team_planner",
        "expert_team_dag",
        "fusion",
        "route_agent",
        "team_chat",
    }
)


def _enabled(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().casefold() not in {"", "0", "false", "no", "off"}


def _fingerprint(value: object) -> str:
    material = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _safe_json_object(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    return {}


class _WorkloadCertificationFailure(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _WorkloadCertificationUncertain(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(slots=True)
class _CertificationEvidence:
    checks: dict[str, bool]
    warning_codes: list[str]
    actual_model: str | None = None
    ttft_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    vector_dimension: int | None = None

    @classmethod
    def create(cls) -> "_CertificationEvidence":
        return cls(
            checks={
                "catalog_ok": True,
                "model_present": True,
                "http_ok": False,
                "response_complete": False,
                "content_observed": False,
                "json_object_verified": False,
                "fusion_profile_verified": False,
                "actual_model_verified": False,
                "embedding_vectors_verified": False,
                "rerank_results_verified": False,
                "batch_terminal_verified": False,
            },
            warning_codes=[],
        )


class ProviderWorkloadCertificationService:
    """Certify non-stream text, JSON, and native Fusion without user data."""

    def __init__(
        self,
        router_service: ModelRouterService,
        *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        batch_poll_interval_seconds: float = 1.0,
    ) -> None:
        self.router_service = router_service
        self.repository = router_service.repository
        self.transport = ProviderChatTransport(router_service.egress_policy)
        self.operation_transport = ProviderOperationTransport(
            router_service.egress_policy
        )
        self._client_factory = client_factory or (
            lambda: httpx.AsyncClient(
                **ProviderChatTransport.client_kwargs(certification=True)
            )
        )
        self._batch_poll_interval_seconds = max(
            0.0, float(batch_poll_interval_seconds)
        )

    @staticmethod
    def enabled() -> bool:
        return _enabled(
            os.getenv(PROVIDER_WORKLOAD_CERTIFICATION_ENABLED_ENV),
            default=True,
        )

    def list(self) -> ProviderWorkloadCertificationListResponse:
        rows = self._repository_method("list_workload_certifications")(
            self.router_service.tenant_id
        )
        summaries: list[ProviderWorkloadCertificationSummary] = []
        for row in rows:
            try:
                connection = self.repository.get_connection(
                    self.router_service.tenant_id,
                    str(row["connection_id"]),
                )
            except RouterRepositoryError:
                continue
            summaries.append(self._summary(connection, row))
        return ProviderWorkloadCertificationListResponse(
            enabled=self.enabled(),
            contract_version=PROVIDER_WORKLOAD_CONTRACT_VERSION,
            certifications=summaries,
        )

    async def run(
        self,
        connection_id: str,
        payload: ProviderWorkloadCertificationRequest,
        *,
        idempotency_key: str,
    ) -> ProviderWorkloadCertificationSummary:
        if not self.enabled():
            raise RouterServiceError(
                "provider_workload_certification_disabled",
                "Agent 与 Workflow Provider 资格认证已由部署配置关闭。",
                status_code=503,
            )
        if not payload.acknowledge_billed_call:
            raise RouterServiceError(
                "billed_call_acknowledgement_required",
                "运行资格认证前必须确认本次调用可能产生费用。",
                status_code=422,
            )
        clean_idempotency_key = str(idempotency_key or "").strip()
        if not clean_idempotency_key or len(clean_idempotency_key) > 200:
            raise RouterServiceError(
                "invalid_idempotency_key",
                "Idempotency-Key 必须是 1 至 200 个字符。",
                status_code=422,
            )
        if payload.execution_shape in {"chat_text", "chat_tools"}:
            raise RouterServiceError(
                "provider_workload_reuses_chat_certification",
                "该执行形态复用现有 Provider Chat 认证，请在 Chat 能力认证中运行。",
                status_code=409,
            )
        connection = self.repository.get_connection(
            self.router_service.tenant_id, connection_id
        )
        self._validate_connection(connection, payload.execution_shape)

        profile = self._profile(payload)
        profile_fingerprint = _fingerprint(profile)
        idempotency_hash = hashlib.sha256(
            clean_idempotency_key.encode("utf-8")
        ).hexdigest()
        existing = self._repository_method(
            "get_workload_certification_by_idempotency"
        )(
            self.router_service.tenant_id,
            connection_id,
            idempotency_hash,
        )
        if existing is not None:
            if (
                str(existing["execution_shape"]) != payload.execution_shape
                or str(existing["requested_model"]) != payload.model_id
                or str(existing["profile_fingerprint"]) != profile_fingerprint
            ):
                raise RouterServiceError(
                    "provider_workload_certification_idempotency_conflict",
                    "该 Idempotency-Key 已用于另一份 Workload 资格配置。",
                    status_code=409,
                )
            if (
                payload.execution_shape
                in {"openrouter_batch_chat", "openrouter_batch_embeddings"}
                and str(existing["status"]) == "uncertain"
            ):
                resumed = await self._resume_batch_certification(
                    connection,
                    existing,
                    payload,
                )
                if resumed is not None:
                    return resumed
            return self._summary(connection, existing)

        refreshed = await ProviderCatalogService(
            self.router_service
        ).refresh_connection(connection_id)
        if refreshed.status != "succeeded":
            raise RouterServiceError(
                "provider_model_catalog_unavailable",
                "模型目录刷新失败，未发送资格认证调用。",
                status_code=409,
            )
        if refreshed.truncated:
            raise RouterServiceError(
                "provider_workload_catalog_refresh_truncated",
                "最新目录已截断，不能作为精确模型资格证据。",
                status_code=409,
            )
        connection = self.repository.get_connection(
            self.router_service.tenant_id, connection_id
        )
        required_models = [payload.model_id]
        if payload.execution_shape == "fusion_native":
            required_models.extend(payload.candidate_model_ids)
            if payload.judge_model_id is not None:
                required_models.append(payload.judge_model_id)
        for model_id in dict.fromkeys(required_models):
            if not self.repository.list_catalog_models(
                self.router_service.tenant_id,
                connection_id=connection_id,
                model_id=model_id,
                status="active",
                limit=1,
            ):
                raise RouterServiceError(
                    "provider_workload_certification_model_not_found",
                    "资格所需的精确模型不在最新完整目录中，未发送付费调用。",
                    status_code=409,
                )

        connection_fingerprint = self.repository.connection_config_fingerprint(
            self.router_service.tenant_id, connection_id
        )
        try:
            row, created = self._repository_method(
                "claim_workload_certification"
            )(
                self.router_service.tenant_id,
                certification_id=f"workcert_{uuid.uuid4().hex}",
                connection_id=connection_id,
                connection_fingerprint=connection_fingerprint,
                contract_version=PROVIDER_WORKLOAD_CONTRACT_VERSION,
                execution_shape=payload.execution_shape,
                requested_model=payload.model_id,
                profile=profile,
                profile_fingerprint=profile_fingerprint,
                idempotency_key_hash=idempotency_hash,
            )
        except RouterRepositoryError as exc:
            code = str(exc)
            if code == "provider_workload_certification_already_running":
                raise RouterServiceError(
                    code,
                    "该连接已有一项 Workload 资格认证正在运行。",
                    status_code=409,
                ) from exc
            if code == "provider_workload_certification_idempotency_conflict":
                raise RouterServiceError(
                    code,
                    "该 Idempotency-Key 已用于另一份 Workload 资格配置。",
                    status_code=409,
                ) from exc
            raise
        if not created:
            return self._summary(connection, row)

        evidence = _CertificationEvidence.create()
        status = "failed"
        error_code: str | None = None
        started = time.perf_counter()
        try:
            api_key = self.repository.resolve_api_key(
                self.router_service.tenant_id, connection_id
            )
            async with asyncio.timeout(60):
                async with self._client_factory() as client:
                    if payload.execution_shape in {
                        "embedding_vectors",
                        "rerank_documents",
                        "openrouter_batch_chat",
                        "openrouter_batch_embeddings",
                    }:
                        terminal = await self._run_operation_certification(
                            client,
                            connection,
                            api_key,
                            payload,
                            evidence,
                            started,
                            certification_id=str(row["id"]),
                            idempotency_key_hash=idempotency_hash,
                            connection_fingerprint=connection_fingerprint,
                        )
                        if not terminal:
                            status = "uncertain"
                            error_code = "provider_batch_certification_pending"
                    else:
                        target = ProviderChatTarget.create(
                            source="managed",
                            provider_kind=connection.kind,
                            base_url=connection.base_url,
                            api_key=api_key,
                            connection_id=connection.id,
                        )
                        request_payload = self._request_payload(payload)
                        authorized = await self.transport.authorize_managed_target(target)
                        request = self.transport.build_authorized_stream_request(
                            client,
                            target,
                            authorized,
                            request_payload,
                        )
                        response = await self.transport.send_authorized_stream(
                            client, request
                        )
                        try:
                            self._validate_status(response.status_code)
                            evidence.checks["http_ok"] = True
                            if payload.execution_shape == "fusion_native":
                                await self._consume_fusion_stream(
                                    response,
                                    evidence,
                                    started,
                                    expected_model=(
                                        payload.judge_model_id or payload.model_id
                                    ),
                                )
                            else:
                                await self._consume_unary_response(
                                    response,
                                    evidence,
                                    started,
                                    requested_model=payload.model_id,
                                    execution_shape=payload.execution_shape,
                                )
                        finally:
                            await response.aclose()
            if payload.execution_shape == "fusion_native":
                evidence.checks["fusion_profile_verified"] = True
            if status != "uncertain":
                status = "passed"
        except _WorkloadCertificationUncertain as exc:
            status = "uncertain"
            error_code = exc.code
        except _WorkloadCertificationFailure as exc:
            error_code = exc.code
        except httpx.ConnectTimeout:
            error_code = "provider_workload_connect_timeout"
        except httpx.ReadTimeout:
            error_code = "provider_workload_read_timeout"
        except httpx.TimeoutException:
            error_code = "provider_workload_timeout"
        except TimeoutError:
            if payload.execution_shape in {
                "openrouter_batch_chat",
                "openrouter_batch_embeddings",
            }:
                status = "uncertain"
                error_code = "provider_batch_certification_pending"
            else:
                error_code = "provider_workload_total_timeout"
        except httpx.ConnectError:
            error_code = "provider_workload_connect_error"
        except ProviderEgressError as exc:
            error_code = exc.code
        except RouterCredentialUnavailable:
            error_code = "provider_workload_credential_unavailable"
        except httpx.HTTPError:
            error_code = "provider_workload_transport_error"
        except asyncio.CancelledError:
            status = "uncertain"
            error_code = "provider_workload_cancelled"
            raise
        except Exception:
            error_code = "provider_workload_unexpected_error"
        finally:
            completed = self._repository_method(
                "complete_workload_certification"
            )(
                self.router_service.tenant_id,
                str(row["id"]),
                status=status,
                checks=evidence.checks,
                warning_codes=evidence.warning_codes,
                error_code=error_code,
                actual_model=evidence.actual_model,
                ttft_ms=evidence.ttft_ms,
                e2e_ms=(time.perf_counter() - started) * 1000,
                prompt_tokens=evidence.prompt_tokens,
                completion_tokens=evidence.completion_tokens,
                total_tokens=evidence.total_tokens,
                vector_dimension=evidence.vector_dimension,
            )
        if status == "passed":
            self._record_certified_offering(connection, completed)
        return self._summary(connection, completed)

    async def resume_pending_batch_certifications(self) -> int:
        """Resume only GET polling for persisted Batch certifications."""

        resumed = 0
        jobs = self._repository_method("list_provider_batch_jobs")(
            self.router_service.tenant_id,
            limit=500,
        )
        for job in jobs:
            if (
                str(job.get("status") or "")
                not in {"validating", "in_progress", "finalizing"}
                or not job.get("upstream_batch_id")
                or not job.get("certification_id")
            ):
                continue
            certification = self._repository_method(
                "get_workload_certification"
            )(
                self.router_service.tenant_id,
                str(job["certification_id"]),
            )
            if certification is None or str(certification["status"]) != "uncertain":
                continue
            try:
                connection = self.repository.get_connection(
                    self.router_service.tenant_id,
                    str(certification["connection_id"]),
                )
                payload = ProviderWorkloadCertificationRequest(
                    execution_shape=str(certification["execution_shape"]),  # type: ignore[arg-type]
                    model_id=str(certification["requested_model"]),
                    acknowledge_billed_call=True,
                )
                result = await self._resume_batch_certification(
                    connection,
                    certification,
                    payload,
                )
                if result is not None:
                    resumed += 1
            except (RouterRepositoryError, RouterServiceError, ValueError):
                continue
        return resumed

    async def _resume_batch_certification(
        self,
        connection: RouterConnection,
        certification: dict[str, object],
        payload: ProviderWorkloadCertificationRequest,
    ) -> ProviderWorkloadCertificationSummary | None:
        job = self._repository_method("get_provider_batch_job_by_certification")(
            self.router_service.tenant_id,
            str(certification["id"]),
        )
        if (
            job is None
            or str(job.get("status") or "")
            not in {"validating", "in_progress", "finalizing"}
            or not job.get("upstream_batch_id")
        ):
            return None
        current_fingerprint = self.repository.connection_config_fingerprint(
            self.router_service.tenant_id,
            connection.id,
        )
        if (
            str(certification["connection_fingerprint"]) != current_fingerprint
            or str(job["connection_fingerprint"]) != current_fingerprint
        ):
            return None
        try:
            running = self._repository_method("resume_workload_certification")(
                self.router_service.tenant_id,
                str(certification["id"]),
            )
        except RouterRepositoryError as exc:
            if str(exc) in {
                "provider_workload_certification_not_resumable",
                "provider_workload_certification_already_running",
            }:
                latest = self._repository_method("get_workload_certification")(
                    self.router_service.tenant_id,
                    str(certification["id"]),
                )
                return self._summary(connection, latest) if latest else None
            raise

        evidence = _CertificationEvidence.create()
        status = "failed"
        error_code: str | None = None
        started = time.perf_counter()
        try:
            api_key = self.repository.resolve_api_key(
                self.router_service.tenant_id,
                connection.id,
            )
            target = ProviderOperationTarget.create(
                provider_kind=connection.kind,
                connection_id=connection.id,
                base_url=connection.base_url,
                api_key=api_key,
            )
            async with asyncio.timeout(60):
                async with self._client_factory() as client:
                    await self._poll_batch_until_terminal(
                        client,
                        target,
                        payload,
                        evidence,
                        job_id=str(job["id"]),
                        upstream_batch_id=str(job["upstream_batch_id"]),
                    )
            status = "passed"
        except _WorkloadCertificationUncertain as exc:
            status = "uncertain"
            error_code = exc.code
        except _WorkloadCertificationFailure as exc:
            error_code = exc.code
        except (httpx.TimeoutException, TimeoutError):
            status = "uncertain"
            error_code = "provider_batch_certification_pending"
        except ProviderEgressError as exc:
            status = "uncertain"
            error_code = exc.code
        except (RouterCredentialUnavailable, httpx.HTTPError):
            status = "uncertain"
            error_code = "provider_batch_poll_unavailable"
        except asyncio.CancelledError:
            status = "uncertain"
            error_code = "provider_workload_cancelled"
            raise
        except Exception:
            status = "uncertain"
            error_code = "provider_workload_unexpected_error"
        finally:
            completed = self._repository_method(
                "complete_workload_certification"
            )(
                self.router_service.tenant_id,
                str(running["id"]),
                status=status,
                checks=evidence.checks,
                warning_codes=evidence.warning_codes,
                error_code=error_code,
                actual_model=evidence.actual_model,
                ttft_ms=evidence.ttft_ms,
                e2e_ms=(time.perf_counter() - started) * 1000,
                prompt_tokens=evidence.prompt_tokens,
                completion_tokens=evidence.completion_tokens,
                total_tokens=evidence.total_tokens,
                vector_dimension=evidence.vector_dimension,
            )
        if status == "passed":
            self._record_certified_offering(connection, completed)
        return self._summary(connection, completed)

    def _record_certified_offering(
        self,
        connection: RouterConnection,
        certification: dict[str, object],
    ) -> None:
        execution_shape = str(certification["execution_shape"])
        profile = _safe_json_object(
            json.loads(str(certification.get("profile_json") or "{}"))
        )
        mapping: dict[str, tuple[str, str]] = {
            "embedding_vectors": ("embed", "managed_embedding"),
            "openrouter_batch_chat": ("chat", "openrouter_batch"),
            "openrouter_batch_embeddings": ("embed", "openrouter_batch"),
        }
        if execution_shape == "rerank_documents":
            access = str(profile.get("rerank_access_mode") or "")
            if access not in {"dedicated", "llm_json"}:
                return
            operation, access_mode = "rerank", f"managed_rerank_{access}"
        elif execution_shape in mapping:
            operation, access_mode = mapping[execution_shape]
        else:
            return
        self._repository_method("upsert_certified_catalog_offering")(
            self.router_service.tenant_id,
            connection_id=connection.id,
            model_id=str(certification["requested_model"]),
            operation=operation,
            access_mode=access_mode,
        )

    @staticmethod
    def _profile(
        payload: ProviderWorkloadCertificationRequest,
    ) -> dict[str, object]:
        return {
            "execution_shape": payload.execution_shape,
            "model_id": payload.model_id,
            "candidate_model_ids": list(payload.candidate_model_ids),
            "judge_model_id": payload.judge_model_id,
            "rerank_access_mode": payload.rerank_access_mode,
        }

    @staticmethod
    def _request_payload(
        payload: ProviderWorkloadCertificationRequest,
    ) -> dict[str, object]:
        request: dict[str, object] = {
            "model": payload.model_id,
            "temperature": 0,
            "max_tokens": 64,
        }
        if payload.execution_shape == "chat_text_unary":
            request.update(
                {
                    "stream": False,
                    "messages": [{"role": "user", "content": SYNTHETIC_UNARY_PROMPT}],
                }
            )
        elif payload.execution_shape == "chat_json_object":
            request.update(
                {
                    "stream": False,
                    "messages": [{"role": "user", "content": SYNTHETIC_JSON_PROMPT}],
                    "response_format": {"type": "json_object"},
                }
            )
        else:
            request.update(
                {
                    "stream": True,
                    "messages": [{"role": "user", "content": SYNTHETIC_UNARY_PROMPT}],
                    "plugins": [
                        {
                            "id": "fusion",
                            "analysis_models": list(payload.candidate_model_ids),
                            "model": payload.judge_model_id,
                        }
                    ],
                }
            )
        return request

    async def _run_operation_certification(
        self,
        client: httpx.AsyncClient,
        connection: RouterConnection,
        api_key: str,
        payload: ProviderWorkloadCertificationRequest,
        evidence: _CertificationEvidence,
        started: float,
        *,
        certification_id: str,
        idempotency_key_hash: str,
        connection_fingerprint: str,
    ) -> bool:
        target = ProviderOperationTarget.create(
            provider_kind=connection.kind,
            connection_id=connection.id,
            base_url=connection.base_url,
            api_key=api_key,
        )
        if payload.execution_shape in {
            "openrouter_batch_chat",
            "openrouter_batch_embeddings",
        }:
            return await self._run_batch_certification(
                client,
                target,
                payload,
                evidence,
                certification_id=certification_id,
                idempotency_key_hash=idempotency_key_hash,
                connection_fingerprint=connection_fingerprint,
            )

        operation = payload.execution_shape
        authorized = await self.operation_transport.authorize(
            target,
            operation,  # type: ignore[arg-type]
            rerank_access_mode=payload.rerank_access_mode,
        )
        request = self.operation_transport.build_authorized_request(
            client,
            target,
            authorized,
            method="POST",
            payload=self._operation_request_payload(payload),
        )
        response = await self.operation_transport.send_authorized(client, request)
        try:
            self._validate_status(response.status_code)
            evidence.checks["http_ok"] = True
            response_payload = await self._read_json_response(response)
            if operation == "embedding_vectors":
                self._validate_embedding_response(
                    response_payload,
                    evidence,
                    requested_model=payload.model_id,
                )
            else:
                self._validate_rerank_response(
                    response_payload,
                    evidence,
                    requested_model=payload.model_id,
                    access_mode=payload.rerank_access_mode,
                )
            evidence.ttft_ms = (time.perf_counter() - started) * 1000
            return True
        finally:
            await response.aclose()

    @staticmethod
    def _operation_request_payload(
        payload: ProviderWorkloadCertificationRequest,
    ) -> dict[str, object]:
        if payload.execution_shape == "embedding_vectors":
            return {
                "model": payload.model_id,
                "input": list(SYNTHETIC_EMBEDDING_INPUTS),
                "encoding_format": "float",
            }
        if payload.rerank_access_mode == "dedicated":
            return {
                "model": payload.model_id,
                "query": SYNTHETIC_RERANK_QUERY,
                "documents": list(SYNTHETIC_RERANK_DOCUMENTS),
                "top_n": len(SYNTHETIC_RERANK_DOCUMENTS),
            }
        return {
            "model": payload.model_id,
            "temperature": 0,
            "max_tokens": 128,
            "stream": False,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Return JSON with results containing every document index 0, 1, 2 "
                        "exactly once and a score from 0 to 1. Query: "
                        f"{SYNTHETIC_RERANK_QUERY} Documents: "
                        + " | ".join(SYNTHETIC_RERANK_DOCUMENTS)
                    ),
                }
            ],
        }

    @staticmethod
    async def _read_json_response(response: httpx.Response) -> dict[str, object]:
        chunks: list[bytes] = []
        total_bytes = 0
        async for chunk in response.aiter_bytes(
            chunk_size=WORKLOAD_RESPONSE_CHUNK_BYTES
        ):
            total_bytes += len(chunk)
            if total_bytes > MAX_WORKLOAD_UNARY_RESPONSE_BYTES:
                raise _WorkloadCertificationFailure(
                    "provider_workload_response_too_large"
                )
            chunks.append(chunk)
        try:
            parsed = json.loads(b"".join(chunks))
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
            raise _WorkloadCertificationFailure(
                "provider_workload_invalid_json_response"
            ) from exc
        if not isinstance(parsed, dict):
            raise _WorkloadCertificationFailure(
                "provider_workload_invalid_json_response"
            )
        return parsed

    @staticmethod
    def _validate_actual_model(
        response_payload: Mapping[str, object],
        evidence: _CertificationEvidence,
        *,
        requested_model: str,
    ) -> None:
        model = response_payload.get("model")
        if isinstance(model, str) and model:
            evidence.actual_model = model
            if model != requested_model:
                raise _WorkloadCertificationFailure(
                    "provider_workload_model_mismatch"
                )
            evidence.checks["actual_model_verified"] = True
        else:
            evidence.warning_codes.append("actual_model_missing")

    @classmethod
    def _validate_embedding_response(
        cls,
        response_payload: dict[str, object],
        evidence: _CertificationEvidence,
        *,
        requested_model: str,
    ) -> None:
        cls._validate_actual_model(
            response_payload, evidence, requested_model=requested_model
        )
        if not evidence.checks["actual_model_verified"]:
            raise _WorkloadCertificationFailure(
                "provider_embedding_actual_model_missing"
            )
        data = response_payload.get("data")
        if not isinstance(data, list) or len(data) != len(SYNTHETIC_EMBEDDING_INPUTS):
            raise _WorkloadCertificationFailure(
                "provider_embedding_vector_count_mismatch"
            )
        dimensions: set[int] = set()
        indexes: set[int] = set()
        for item in data:
            if not isinstance(item, dict):
                raise _WorkloadCertificationFailure(
                    "provider_embedding_invalid_vector"
                )
            index = item.get("index")
            vector = item.get("embedding")
            if not isinstance(index, int) or isinstance(index, bool):
                raise _WorkloadCertificationFailure(
                    "provider_embedding_invalid_index"
                )
            if not isinstance(vector, list) or not vector:
                raise _WorkloadCertificationFailure(
                    "provider_embedding_invalid_vector"
                )
            if any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                for value in vector
            ):
                raise _WorkloadCertificationFailure(
                    "provider_embedding_non_finite_vector"
                )
            indexes.add(index)
            dimensions.add(len(vector))
        if indexes != set(range(len(SYNTHETIC_EMBEDDING_INPUTS))):
            raise _WorkloadCertificationFailure(
                "provider_embedding_invalid_index"
            )
        if len(dimensions) != 1:
            raise _WorkloadCertificationFailure(
                "provider_embedding_dimension_mismatch"
            )
        evidence.vector_dimension = next(iter(dimensions))
        cls._read_usage(response_payload, evidence)
        evidence.checks["response_complete"] = True
        evidence.checks["content_observed"] = True
        evidence.checks["embedding_vectors_verified"] = True

    @classmethod
    def _validate_rerank_response(
        cls,
        response_payload: dict[str, object],
        evidence: _CertificationEvidence,
        *,
        requested_model: str,
        access_mode: str | None,
    ) -> None:
        cls._validate_actual_model(
            response_payload, evidence, requested_model=requested_model
        )
        cls._read_usage(response_payload, evidence)
        result_payload: object = response_payload
        if access_mode == "llm_json":
            choices = response_payload.get("choices")
            first = choices[0] if isinstance(choices, list) and choices else None
            message = first.get("message") if isinstance(first, dict) else None
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, str) or not content.strip():
                raise _WorkloadCertificationFailure(
                    "provider_rerank_empty_response"
                )
            try:
                result_payload = json.loads(content)
            except json.JSONDecodeError as exc:
                raise _WorkloadCertificationFailure(
                    "provider_rerank_invalid_json"
                ) from exc
        if not isinstance(result_payload, dict):
            raise _WorkloadCertificationFailure("provider_rerank_invalid_results")
        results = result_payload.get("results")
        if not isinstance(results, list) or len(results) != len(
            SYNTHETIC_RERANK_DOCUMENTS
        ):
            raise _WorkloadCertificationFailure(
                "provider_rerank_incomplete_results"
            )
        indexes: set[int] = set()
        for result in results:
            if not isinstance(result, dict):
                raise _WorkloadCertificationFailure(
                    "provider_rerank_invalid_results"
                )
            index = result.get("index")
            score = result.get("relevance_score", result.get("score"))
            if (
                not isinstance(index, int)
                or isinstance(index, bool)
                or isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(float(score))
                or not 0 <= float(score) <= 1
            ):
                raise _WorkloadCertificationFailure(
                    "provider_rerank_invalid_results"
                )
            indexes.add(index)
        if indexes != set(range(len(SYNTHETIC_RERANK_DOCUMENTS))):
            raise _WorkloadCertificationFailure(
                "provider_rerank_duplicate_or_missing_index"
            )
        evidence.checks["response_complete"] = True
        evidence.checks["content_observed"] = True
        evidence.checks["rerank_results_verified"] = True

    async def _run_batch_certification(
        self,
        client: httpx.AsyncClient,
        target: ProviderOperationTarget,
        payload: ProviderWorkloadCertificationRequest,
        evidence: _CertificationEvidence,
        *,
        certification_id: str,
        idempotency_key_hash: str,
        connection_fingerprint: str,
    ) -> bool:
        operation = payload.execution_shape
        endpoint = (
            "/v1/chat/completions"
            if operation == "openrouter_batch_chat"
            else "/v1/embeddings"
        )
        body: dict[str, object]
        if operation == "openrouter_batch_chat":
            body = {
                "model": payload.model_id,
                "messages": [{"role": "user", "content": SYNTHETIC_UNARY_PROMPT}],
                "max_tokens": 16,
                "temperature": 0,
            }
        else:
            body = {
                "model": payload.model_id,
                "input": SYNTHETIC_EMBEDDING_INPUTS[0],
                "encoding_format": "float",
            }
        request_payload = {
            "endpoint": endpoint,
            "model": payload.model_id,
            "requests": [{"custom_id": "modelmirror-certification", "body": body}],
        }
        request_fingerprint = _fingerprint(request_payload)
        authorized = await self.operation_transport.authorize(
            target, operation  # type: ignore[arg-type]
        )
        job_id = f"mmbatch_{uuid.uuid4().hex}"
        job, created = self._repository_method("claim_provider_batch_job")(
            self.router_service.tenant_id,
            job_id=job_id,
            connection_id=target.connection_id,
            connection_fingerprint=connection_fingerprint,
            endpoint=endpoint,
            model_id=payload.model_id,
            certification_id=certification_id,
            idempotency_key_hash=idempotency_key_hash,
            request_fingerprint=request_fingerprint,
            purpose="certification",
            request_count=1,
        )
        if not created:
            if str(job["request_fingerprint"]) != request_fingerprint:
                raise _WorkloadCertificationFailure(
                    "provider_batch_idempotency_conflict"
                )
            return str(job["status"]) == "completed"
        request = self.operation_transport.build_authorized_request(
            client,
            target,
            authorized,
            method="POST",
            payload=request_payload,
        )
        try:
            response = await self.operation_transport.send_authorized(client, request)
        except (httpx.HTTPError, TimeoutError) as exc:
            self._repository_method("mark_provider_batch_uncertain")(
                self.router_service.tenant_id,
                job_id,
                error_code="provider_batch_submission_uncertain",
            )
            raise _WorkloadCertificationUncertain(
                "provider_batch_submission_uncertain"
            ) from exc
        try:
            try:
                self._validate_status(response.status_code)
                submitted_payload = await self._read_json_response(response)
            except _WorkloadCertificationFailure as exc:
                self._repository_method("fail_provider_batch_submission")(
                    self.router_service.tenant_id,
                    job_id,
                    error_code=exc.code,
                )
                raise
        finally:
            await response.aclose()
        upstream_id = submitted_payload.get("id")
        upstream_status = str(submitted_payload.get("status") or "validating")
        if not isinstance(upstream_id, str) or not upstream_id:
            self._repository_method("fail_provider_batch_submission")(
                self.router_service.tenant_id,
                job_id,
                error_code="provider_batch_missing_upstream_id",
            )
            raise _WorkloadCertificationFailure(
                "provider_batch_missing_upstream_id"
            )
        initial_status = (
            upstream_status
            if upstream_status in {"validating", "in_progress", "finalizing"}
            else "validating"
        )
        self._repository_method("mark_provider_batch_submitted")(
            self.router_service.tenant_id,
            job_id,
            upstream_batch_id=upstream_id,
            status=initial_status,
        )
        return await self._poll_batch_until_terminal(
            client,
            target,
            payload,
            evidence,
            job_id=job_id,
            upstream_batch_id=upstream_id,
            initial_payload=submitted_payload,
        )

    async def _poll_batch_until_terminal(
        self,
        client: httpx.AsyncClient,
        target: ProviderOperationTarget,
        payload: ProviderWorkloadCertificationRequest,
        evidence: _CertificationEvidence,
        *,
        job_id: str,
        upstream_batch_id: str,
        initial_payload: dict[str, object] | None = None,
    ) -> bool:
        operation = payload.execution_shape
        polled_payload = initial_payload
        current_status = "validating"
        poll_count = 0
        while True:
            if polled_payload is None:
                if poll_count and self._batch_poll_interval_seconds:
                    await asyncio.sleep(self._batch_poll_interval_seconds)
                authorized_poll = await self.operation_transport.authorize(
                    target,
                    operation,  # type: ignore[arg-type]
                    upstream_batch_id=upstream_batch_id,
                )
                poll_request = self.operation_transport.build_authorized_request(
                    client,
                    target,
                    authorized_poll,
                    method="GET",
                )
                try:
                    poll_response = await self.operation_transport.send_authorized(
                        client, poll_request
                    )
                    try:
                        self._validate_status(poll_response.status_code)
                        polled_payload = await self._read_json_response(poll_response)
                    finally:
                        await poll_response.aclose()
                except _WorkloadCertificationFailure as exc:
                    self._repository_method("update_provider_batch_job")(
                        self.router_service.tenant_id,
                        job_id,
                        status="failed",
                        error_code=exc.code,
                    )
                    raise
                except (httpx.HTTPError, TimeoutError) as exc:
                    self._repository_method("update_provider_batch_job")(
                        self.router_service.tenant_id,
                        job_id,
                        status=current_status,
                        error_code="provider_batch_poll_uncertain",
                    )
                    raise _WorkloadCertificationUncertain(
                        "provider_batch_poll_uncertain"
                    ) from exc
                poll_count += 1

            status = str(polled_payload.get("status") or "in_progress")
            normalized_status = (
                status
                if status
                in {
                    "validating",
                    "in_progress",
                    "finalizing",
                    "completed",
                    "failed",
                    "cancelled",
                    "expired",
                }
                else "in_progress"
            )
            current_status = normalized_status
            terminal = normalized_status in {
                "completed",
                "failed",
                "cancelled",
                "expired",
            }
            counts = polled_payload.get("request_counts")
            count_map = counts if isinstance(counts, dict) else {}
            completed_count = self._integer(count_map.get("completed")) or 0
            failed_count = self._integer(count_map.get("failed")) or 0
            usage = polled_payload.get("usage")
            usage_map = {
                key: value
                for key, value in (
                    usage.items() if isinstance(usage, dict) else []
                )
                if key in {"prompt_tokens", "completion_tokens", "total_tokens"}
                and isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(float(value))
            }
            self._repository_method("update_provider_batch_job")(
                self.router_service.tenant_id,
                job_id,
                status=normalized_status,
                completed_count=completed_count,
                failed_count=failed_count,
                usage=usage_map,
                error_code=(
                    None
                    if normalized_status in {"validating", "in_progress", "finalizing", "completed"}
                    else f"provider_batch_{normalized_status}"
                ),
            )
            if not terminal:
                polled_payload = None
                continue
            if (
                normalized_status != "completed"
                or completed_count != 1
                or failed_count != 0
            ):
                raise _WorkloadCertificationFailure(
                    f"provider_batch_{normalized_status if normalized_status != 'completed' else 'result_failed'}"
                )
            results = polled_payload.get("results")
            first_result = (
                results[0]
                if isinstance(results, list) and len(results) == 1
                else None
            )
            if (
                not isinstance(first_result, dict)
                or first_result.get("custom_id") != "modelmirror-certification"
            ):
                raise _WorkloadCertificationFailure(
                    "provider_batch_result_mismatch"
                )
            result_response = first_result.get("response")
            result_status = (
                result_response.get("status_code")
                if isinstance(result_response, dict)
                else None
            )
            result_body = (
                result_response.get("body")
                if isinstance(result_response, dict)
                else None
            )
            actual_model = (
                result_body.get("model") if isinstance(result_body, dict) else None
            )
            if not isinstance(result_status, int) or not 200 <= result_status < 300:
                raise _WorkloadCertificationFailure("provider_batch_result_failed")
            if not isinstance(actual_model, str) or not actual_model:
                raise _WorkloadCertificationFailure(
                    "provider_batch_actual_model_missing"
                )
            if actual_model != payload.model_id:
                raise _WorkloadCertificationFailure(
                    "provider_workload_model_mismatch"
                )
            evidence.actual_model = actual_model
            evidence.checks["actual_model_verified"] = True
            evidence.checks["http_ok"] = True
            evidence.checks["response_complete"] = True
            evidence.checks["content_observed"] = True
            evidence.checks["batch_terminal_verified"] = True
            evidence.prompt_tokens = self._integer(usage_map.get("prompt_tokens"))
            evidence.completion_tokens = self._integer(
                usage_map.get("completion_tokens")
            )
            evidence.total_tokens = self._integer(usage_map.get("total_tokens"))
            return True

    @staticmethod
    async def _consume_unary_response(
        response: httpx.Response,
        evidence: _CertificationEvidence,
        started: float,
        *,
        requested_model: str,
        execution_shape: ProviderWorkloadExecutionShape,
    ) -> None:
        try:
            chunks: list[bytes] = []
            total_bytes = 0
            async for chunk in response.aiter_bytes(
                chunk_size=WORKLOAD_RESPONSE_CHUNK_BYTES
            ):
                total_bytes += len(chunk)
                if total_bytes > MAX_WORKLOAD_UNARY_RESPONSE_BYTES:
                    raise _WorkloadCertificationFailure(
                        "provider_workload_response_too_large"
                    )
                chunks.append(chunk)
            raw = b"".join(chunks)
            payload = json.loads(raw)
        except _WorkloadCertificationFailure:
            raise
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
            raise _WorkloadCertificationFailure(
                "provider_workload_invalid_json_response"
            ) from exc
        if not isinstance(payload, dict):
            raise _WorkloadCertificationFailure(
                "provider_workload_invalid_json_response"
            )
        evidence.checks["response_complete"] = True
        ProviderWorkloadCertificationService._read_usage(payload, evidence)
        model = payload.get("model")
        if isinstance(model, str) and model:
            evidence.actual_model = model
            if model != requested_model:
                raise _WorkloadCertificationFailure(
                    "provider_workload_model_mismatch"
                )
            evidence.checks["actual_model_verified"] = True
        else:
            evidence.warning_codes.append("actual_model_missing")
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise _WorkloadCertificationFailure("provider_workload_empty_response")
        first = choices[0]
        if not isinstance(first, dict):
            raise _WorkloadCertificationFailure("provider_workload_empty_response")
        message = first.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise _WorkloadCertificationFailure("provider_workload_empty_response")
        evidence.ttft_ms = (time.perf_counter() - started) * 1000
        evidence.checks["content_observed"] = True
        if execution_shape == "chat_json_object":
            try:
                parsed_content = json.loads(content)
            except json.JSONDecodeError as exc:
                raise _WorkloadCertificationFailure(
                    "provider_workload_json_object_invalid"
                ) from exc
            if parsed_content != {"ok": True}:
                raise _WorkloadCertificationFailure(
                    "provider_workload_json_object_contract_mismatch"
                )
            evidence.checks["json_object_verified"] = True

    @staticmethod
    async def _consume_fusion_stream(
        response: httpx.Response,
        evidence: _CertificationEvidence,
        started: float,
        *,
        expected_model: str,
    ) -> None:
        buffer = b""
        total_bytes = 0
        terminal = False
        try:
            async for chunk in response.aiter_bytes(
                chunk_size=WORKLOAD_RESPONSE_CHUNK_BYTES
            ):
                total_bytes += len(chunk)
                if total_bytes > MAX_WORKLOAD_STREAM_BYTES:
                    raise _WorkloadCertificationFailure(
                        "provider_workload_stream_too_large"
                    )
                buffer += chunk
                while True:
                    delimiters = [
                        (index, delimiter)
                        for delimiter in (b"\n\n", b"\r\n\r\n", b"\r\r")
                        if (index := buffer.find(delimiter)) >= 0
                    ]
                    if not delimiters:
                        break
                    index, delimiter = min(delimiters, key=lambda item: item[0])
                    event_bytes = buffer[:index]
                    buffer = buffer[index + len(delimiter) :]
                    if len(event_bytes) > MAX_WORKLOAD_SSE_EVENT_BYTES:
                        raise _WorkloadCertificationFailure(
                            "provider_workload_sse_event_too_large"
                        )
                    try:
                        event = event_bytes.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
                    except UnicodeDecodeError as exc:
                        raise _WorkloadCertificationFailure(
                            "provider_workload_invalid_sse"
                        ) from exc
                    terminal = (
                        ProviderWorkloadCertificationService._consume_sse_event(
                            event, evidence, started, expected_model=expected_model
                        )
                        or terminal
                    )
                if len(buffer) > MAX_WORKLOAD_SSE_EVENT_BYTES:
                    raise _WorkloadCertificationFailure(
                        "provider_workload_sse_event_too_large"
                    )
            if buffer.strip():
                try:
                    event = buffer.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
                except UnicodeDecodeError as exc:
                    raise _WorkloadCertificationFailure(
                        "provider_workload_invalid_sse"
                    ) from exc
                terminal = (
                    ProviderWorkloadCertificationService._consume_sse_event(
                        event, evidence, started, expected_model=expected_model
                    )
                    or terminal
                )
        except _WorkloadCertificationFailure:
            raise
        except Exception as exc:
            raise _WorkloadCertificationFailure(
                "provider_workload_stream_interrupted"
            ) from exc
        evidence.checks["response_complete"] = True
        if not evidence.checks["content_observed"]:
            raise _WorkloadCertificationFailure("provider_workload_empty_stream")
        if not terminal:
            raise _WorkloadCertificationFailure(
                "provider_workload_missing_terminal"
            )

    @staticmethod
    def _consume_sse_event(
        event: str,
        evidence: _CertificationEvidence,
        started: float,
        *,
        expected_model: str,
    ) -> bool:
        data_lines = [
            line[5:].lstrip()
            for line in event.split("\n")
            if line.startswith("data:")
        ]
        if not data_lines:
            return False
        data = "\n".join(data_lines)
        if data == "[DONE]":
            return True
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            raise _WorkloadCertificationFailure(
                "provider_workload_invalid_sse"
            ) from exc
        if not isinstance(payload, dict):
            raise _WorkloadCertificationFailure("provider_workload_invalid_sse")
        ProviderWorkloadCertificationService._read_usage(payload, evidence)
        model = payload.get("model")
        if isinstance(model, str) and model:
            evidence.actual_model = model
            if model != expected_model:
                raise _WorkloadCertificationFailure(
                    "provider_workload_model_mismatch"
                )
            evidence.checks["actual_model_verified"] = True
        choices = payload.get("choices")
        if not isinstance(choices, list):
            return False
        terminal = False
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            finish_reason = choice.get("finish_reason")
            terminal = terminal or bool(finish_reason)
            delta = choice.get("delta")
            content = delta.get("content") if isinstance(delta, dict) else None
            if isinstance(content, str) and content:
                if evidence.ttft_ms is None:
                    evidence.ttft_ms = (time.perf_counter() - started) * 1000
                evidence.checks["content_observed"] = True
        return terminal

    @staticmethod
    def _read_usage(
        payload: Mapping[str, object], evidence: _CertificationEvidence
    ) -> None:
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            return
        evidence.prompt_tokens = ProviderWorkloadCertificationService._integer(
            usage.get("prompt_tokens")
        )
        evidence.completion_tokens = ProviderWorkloadCertificationService._integer(
            usage.get("completion_tokens")
        )
        evidence.total_tokens = ProviderWorkloadCertificationService._integer(
            usage.get("total_tokens")
        )

    @staticmethod
    def _validate_status(status_code: int) -> None:
        if 200 <= status_code < 300:
            return
        code = {
            401: "provider_workload_http_401",
            403: "provider_workload_http_403",
            404: "provider_workload_http_404",
            429: "provider_workload_http_429",
        }.get(
            status_code,
            "provider_workload_http_5xx"
            if status_code >= 500
            else "provider_workload_http_error",
        )
        raise _WorkloadCertificationFailure(code)

    def _validate_connection(
        self,
        connection: RouterConnection,
        execution_shape: ProviderWorkloadExecutionShape,
    ) -> None:
        if not connection.enabled:
            raise RouterServiceError(
                "connection_disabled", "该模型服务已停用。", status_code=409
            )
        required_scope = self._required_scope(execution_shape)
        if required_scope not in connection.scopes:
            raise RouterServiceError(
                f"connection_{required_scope}_scope_required",
                f"Workload 资格要求连接启用 {required_scope} scope。",
                status_code=409,
            )
        if execution_shape == "fusion_native" and connection.kind != "openrouter":
            raise RouterServiceError(
                "provider_workload_fusion_requires_openrouter",
                "原生 Fusion 资格只允许 OpenRouter 类型连接。",
                status_code=409,
            )
        if execution_shape in {
            "openrouter_batch_chat",
            "openrouter_batch_embeddings",
        } and connection.kind != "openrouter":
            raise RouterServiceError(
                "provider_workload_batch_requires_openrouter",
                "Batch 资格只允许 OpenRouter 类型连接。",
                status_code=409,
            )

    @staticmethod
    def _required_scope(execution_shape: ProviderWorkloadExecutionShape) -> str:
        if execution_shape == "embedding_vectors":
            return "embedding"
        if execution_shape == "rerank_documents":
            return "rerank"
        if execution_shape in {
            "openrouter_batch_chat",
            "openrouter_batch_embeddings",
        }:
            return "batch"
        return "chat"

    def _summary(
        self,
        connection: RouterConnection,
        row: dict[str, object],
    ) -> ProviderWorkloadCertificationSummary:
        status = str(row["status"])
        blocked_reason: str | None = None
        try:
            fingerprint = self.repository.connection_config_fingerprint(
                self.router_service.tenant_id, connection.id
            )
        except RouterRepositoryError:
            fingerprint = ""
        if str(row["connection_fingerprint"]) != fingerprint and status != "running":
            status = "stale"
            blocked_reason = "provider_workload_connection_fingerprint_changed"
        elif status == "passed":
            time_reason = ProviderChatControlService._certification_time_status(  # noqa: SLF001
                row
            )
            if time_reason is not None:
                status = "stale"
                blocked_reason = time_reason.replace(
                    "provider_chat_", "provider_workload_", 1
                )
        profile = _safe_json_object(json.loads(str(row["profile_json"] or "{}")))
        required_scope = self._required_scope(
            str(row["execution_shape"])  # type: ignore[arg-type]
        )
        if blocked_reason is None and not connection.enabled:
            blocked_reason = "connection_disabled"
        elif blocked_reason is None and required_scope not in connection.scopes:
            blocked_reason = f"connection_{required_scope}_scope_required"
        elif blocked_reason is None and connection.health != "online":
            blocked_reason = "provider_connection_not_online"
        elif blocked_reason is None:
            try:
                self.repository.resolve_api_key(
                    self.router_service.tenant_id, connection.id
                )
            except RouterCredentialUnavailable:
                blocked_reason = "provider_workload_credential_unavailable"
        checks = _safe_json_object(json.loads(str(row["checks_json"] or "{}")))
        warnings = json.loads(str(row["warnings_json"] or "[]"))
        batch_job = None
        if str(row["execution_shape"]).startswith("openrouter_batch_"):
            batch_job = next(
                (
                    item
                    for item in self._repository_method("list_provider_batch_jobs")(
                        self.router_service.tenant_id,
                        connection_id=connection.id,
                        limit=100,
                    )
                    if str(item.get("certification_id") or "") == str(row["id"])
                ),
                None,
            )
        return ProviderWorkloadCertificationSummary(
            certification_id=str(row["id"]),
            connection_id=connection.id,
            connection_name=connection.name,
            provider_kind=connection.kind,
            execution_shape=str(row["execution_shape"]),  # type: ignore[arg-type]
            status=status,  # type: ignore[arg-type]
            can_run=(
                status == "passed"
                and blocked_reason is None
                and self.enabled()
            ),
            blocked_reason=blocked_reason,
            checks=ProviderWorkloadCertificationChecks(**checks),
            warning_codes=[str(item) for item in warnings],
            error_code=str(row["error_code"]) if row["error_code"] else None,
            requested_model=str(row["requested_model"]),
            actual_model=str(row["actual_model"]) if row["actual_model"] else None,
            candidate_model_ids=[
                str(item) for item in profile.get("candidate_model_ids", [])
            ],
            judge_model_id=(
                str(profile["judge_model_id"])
                if profile.get("judge_model_id")
                else None
            ),
            profile_fingerprint=str(row["profile_fingerprint"]),
            rerank_access_mode=(
                str(profile["rerank_access_mode"])  # type: ignore[arg-type]
                if profile.get("rerank_access_mode")
                else None
            ),
            batch_job_id=(str(batch_job["id"]) if batch_job else None),
            batch_status=(str(batch_job["status"]) if batch_job else None),
            ttft_ms=self._float(row["ttft_ms"]),
            e2e_ms=self._float(row["e2e_ms"]),
            prompt_tokens=self._integer(row["prompt_tokens"]),
            completion_tokens=self._integer(row["completion_tokens"]),
            total_tokens=self._integer(row["total_tokens"]),
            vector_dimension=self._integer(row.get("vector_dimension")),
            created_at=str(row["created_at"]),
            completed_at=str(row["completed_at"]) if row["completed_at"] else None,
        )

    def _repository_method(self, name: str):
        method = getattr(self.repository, name, None)
        if not callable(method):
            raise RouterServiceError(
                "provider_workload_storage_unavailable",
                "当前 Router 存储不支持 Workload 控制面。",
                status_code=503,
            )
        return method

    @staticmethod
    def _integer(value: object) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _float(value: object) -> float | None:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None


class ProviderWorkloadControlService:
    """Manage exact entry/model/shape bindings without changing data planes."""

    def __init__(self, router_service: ModelRouterService) -> None:
        self.router_service = router_service
        self.repository = router_service.repository

    @staticmethod
    def feature_enabled(entry_id: ProviderWorkloadEntryId) -> bool:
        return all(
            _enabled(os.getenv(flag), default=False)
            for flag in ENTRY_FEATURE_FLAGS[entry_id]
        )

    @staticmethod
    def data_plane_integrated(entry_id: ProviderWorkloadEntryId) -> bool:
        return entry_id in DATA_PLANE_INTEGRATED_ENTRIES

    def policies(self) -> ProviderWorkloadPolicyListResponse:
        return ProviderWorkloadPolicyListResponse(
            contract_version=PROVIDER_WORKLOAD_CONTRACT_VERSION,
            policies=[self.get_policy(entry_id) for entry_id in ENTRY_FEATURE_FLAGS],
        )

    def get_policy(
        self, entry_id: ProviderWorkloadEntryId
    ) -> ProviderWorkloadPolicyResponse:
        bundle = self._repository_method("get_workload_policy_bundle")(
            self.router_service.tenant_id,
            entry_id=entry_id,
        )
        return self._policy_response(entry_id, bundle)

    def update_policy(
        self,
        entry_id: ProviderWorkloadEntryId,
        payload: ProviderWorkloadPolicyUpdate,
    ) -> ProviderWorkloadPolicyResponse:
        allowed_shapes = ENTRY_ALLOWED_SHAPES[entry_id]
        if payload.local_fallback_mode not in ENTRY_ALLOWED_LOCAL_FALLBACKS[entry_id]:
            raise RouterServiceError(
                "provider_workload_local_fallback_not_allowed",
                "该入口不允许所选本地降级模式。",
                status_code=422,
            )
        qualified: list[dict[str, object]] = []
        for binding in payload.bindings:
            if binding.execution_shape not in allowed_shapes:
                raise RouterServiceError(
                    "provider_workload_execution_shape_not_allowed",
                    "该入口不允许所选 Provider 执行形态。",
                    status_code=422,
                )
            qualification, reason = self._current_qualification(
                connection_id=binding.connection_id,
                model_id=binding.model_id,
                execution_shape=binding.execution_shape,
                rerank_access_mode=binding.rerank_access_mode,
            )
            if qualification is None:
                raise RouterServiceError(
                    reason,
                    "Binding 缺少当前完整目录、精确模型或对应执行形态资格。",
                    status_code=409,
                )
            qualified.append(qualification)
        policy_fingerprint = self._policy_fingerprint(
            entry_id,
            qualified,
            local_fallback_mode=payload.local_fallback_mode,
        )
        try:
            bundle = self._repository_method("replace_workload_policy")(
                self.router_service.tenant_id,
                entry_id=entry_id,
                expected_revision=payload.expected_revision,
                policy_fingerprint=policy_fingerprint,
                local_fallback_mode=payload.local_fallback_mode,
                bindings=qualified,
            )
        except RouterRepositoryError as exc:
            if str(exc) == "provider_workload_policy_revision_conflict":
                raise RouterServiceError(
                    str(exc),
                    "Workload 策略 revision 已变化，请刷新后重试。",
                    status_code=409,
                ) from exc
            raise
        return self._policy_response(entry_id, bundle)

    def activate(
        self,
        entry_id: ProviderWorkloadEntryId,
        payload: ProviderWorkloadActivationRequest,
    ) -> ProviderWorkloadPolicyResponse:
        policy = self.get_policy(entry_id)
        blockers = list(policy.blocking_reason_codes)
        if not self.data_plane_integrated(entry_id):
            blockers.append("provider_workload_data_plane_not_integrated")
        if not policy.feature_enabled:
            blockers.append("provider_workload_feature_disabled")
        if not payload.no_open_p0_p1:
            blockers.append("provider_workload_open_p0_p1_not_confirmed")
        if not payload.acknowledge_fail_closed:
            blockers.append("provider_workload_fail_closed_not_acknowledged")
        if blockers:
            raise RouterServiceError(
                blockers[0],
                "当前入口尚未满足 managed_required 人工激活条件。",
                status_code=409,
            )
        try:
            bundle = self._repository_method("activate_workload_policy")(
                self.router_service.tenant_id,
                entry_id=entry_id,
                expected_revision=payload.expected_revision,
                policy_fingerprint=policy.policy_fingerprint,
                no_open_p0_p1=payload.no_open_p0_p1,
                acknowledge_fail_closed=payload.acknowledge_fail_closed,
            )
        except RouterRepositoryError as exc:
            raise RouterServiceError(
                str(exc),
                "Workload 策略已变化，未执行激活。",
                status_code=409,
            ) from exc
        return self._policy_response(entry_id, bundle)

    def deactivate(
        self,
        entry_id: ProviderWorkloadEntryId,
        payload: ProviderWorkloadDeactivationRequest,
    ) -> ProviderWorkloadPolicyResponse:
        try:
            bundle = self._repository_method("deactivate_workload_policy")(
                self.router_service.tenant_id,
                entry_id=entry_id,
                expected_revision=payload.expected_revision,
            )
        except RouterRepositoryError as exc:
            raise RouterServiceError(
                str(exc),
                "Workload 策略 revision 已变化，请刷新后重试。",
                status_code=409,
            ) from exc
        return self._policy_response(entry_id, bundle)

    def overview(self) -> ProviderWorkloadOverview:
        policies = self.policies().policies
        blockers = sorted(
            {
                reason
                for policy in policies
                for reason in policy.blocking_reason_codes
            }
        )
        return ProviderWorkloadOverview(
            contract_version=PROVIDER_WORKLOAD_CONTRACT_VERSION,
            entry_count=len(policies),
            feature_enabled_count=sum(item.feature_enabled for item in policies),
            managed_required_count=sum(
                item.effective_status == "managed_required" for item in policies
            ),
            degraded_required_count=sum(
                item.effective_status == "degraded_required" for item in policies
            ),
            qualified_binding_count=sum(
                binding.valid for policy in policies for binding in policy.bindings
            ),
            blocking_reason_codes=blockers,
            policies=policies,
        )

    def public_status(
        self,
        entry_id: ProviderWorkloadEntryId,
        model_id: str,
        execution_shape: ProviderWorkloadExecutionShape,
    ) -> ProviderWorkloadPublicStatus:
        clean_model = str(model_id or "").strip()
        policy = self.get_policy(entry_id)
        binding = next(
            (
                item
                for item in policy.bindings
                if item.model_id == clean_model
                and item.execution_shape == execution_shape
            ),
            None,
        )
        reason = "provider_workload_available"
        available = True
        if execution_shape not in ENTRY_ALLOWED_SHAPES[entry_id]:
            reason = "provider_workload_execution_shape_not_allowed"
            available = False
        elif not policy.feature_enabled:
            reason = "provider_workload_feature_disabled"
            available = False
        elif not policy.data_plane_integrated:
            reason = "provider_workload_data_plane_not_integrated"
            available = False
        elif policy.effective_status != "managed_required":
            reason = "provider_workload_policy_not_active"
            available = False
        elif binding is None:
            reason = "provider_workload_binding_missing"
            available = False
        elif not binding.valid:
            reason = binding.reason_code
            available = False
        return ProviderWorkloadPublicStatus(
            contract_version=PROVIDER_WORKLOAD_CONTRACT_VERSION,
            entry_id=entry_id,
            execution_shape=execution_shape,
            model_id=clean_model,
            feature_enabled=policy.feature_enabled,
            status=policy.effective_status,
            available=available,
            blocks_before_dispatch=not available,
            reason_code=reason,
        )

    def receipts(
        self,
        *,
        entry_id: ProviderWorkloadEntryId | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> ProviderWorkloadReceiptsResponse:
        bundle = self._repository_method("list_workload_receipts")(
            self.router_service.tenant_id,
            entry_id=entry_id,
            limit=limit,
            cursor=cursor,
        )
        calls_by_run: dict[str, list[ProviderWorkloadCallSummary]] = {}
        for row in bundle["calls"]:
            calls_by_run.setdefault(str(row["run_id"]), []).append(
                ProviderWorkloadCallSummary(
                    call_id=str(row["id"]),
                    run_id=str(row["run_id"]),
                    entry_id=str(row["entry_id"]),  # type: ignore[arg-type]
                    execution_shape=str(row["execution_shape"]),  # type: ignore[arg-type]
                    model_id=str(row["requested_model"]),
                    connection_id=(
                        str(row["connection_id"]) if row["connection_id"] else None
                    ),
                    call_sequence=int(row["call_sequence"]),
                    dispatched=bool(row["dispatched"]),
                    status=str(row["status"]),
                    result_class=(
                        str(row["result_class"]) if row["result_class"] else None
                    ),
                    error_code=str(row["error_code"]) if row["error_code"] else None,
                    actual_model=(
                        str(row["actual_model"]) if row["actual_model"] else None
                    ),
                    ttft_ms=self._float(row["ttft_ms"]),
                    e2e_ms=self._float(row["e2e_ms"]),
                    prompt_tokens=self._integer(row["prompt_tokens"]),
                    completion_tokens=self._integer(row["completion_tokens"]),
                    total_tokens=self._integer(row["total_tokens"]),
                    created_at=str(row["created_at"]),
                    completed_at=(
                        str(row["completed_at"]) if row["completed_at"] else None
                    ),
                )
            )
        runs = [
            ProviderWorkloadRunSummary(
                run_id=str(row["id"]),
                entry_id=str(row["entry_id"]),  # type: ignore[arg-type]
                policy_fingerprint=str(row["policy_fingerprint"]),
                parent_run_reference=(
                    str(row["parent_run_reference"])
                    if row["parent_run_reference"]
                    else None
                ),
                status=str(row["status"]),
                result_class=(
                    str(row["result_class"]) if row["result_class"] else None
                ),
                reason_codes=[
                    str(item)
                    for item in json.loads(str(row["reason_codes_json"] or "[]"))
                ],
                created_at=str(row["created_at"]),
                completed_at=(
                    str(row["completed_at"]) if row["completed_at"] else None
                ),
                calls=calls_by_run.get(str(row["id"]), []),
            )
            for row in bundle["runs"]
        ]
        return ProviderWorkloadReceiptsResponse(
            contract_version=PROVIDER_WORKLOAD_CONTRACT_VERSION,
            runs=runs,
            next_cursor=bundle["next_cursor"],
        )

    def _policy_response(
        self,
        entry_id: ProviderWorkloadEntryId,
        bundle: dict[str, object],
    ) -> ProviderWorkloadPolicyResponse:
        policies = list(bundle.get("policies") or [])
        bindings = list(bundle.get("bindings") or [])
        approvals = list(bundle.get("approvals") or [])
        policy = policies[0] if policies else None
        fingerprint = (
            str(policy["policy_fingerprint"])
            if isinstance(policy, dict)
            else self._policy_fingerprint(
                entry_id, [], local_fallback_mode="none"
            )
        )
        configured_status = (
            str(policy["status"]) if isinstance(policy, dict) else "legacy"
        )
        binding_summaries: list[ProviderWorkloadBindingSummary] = []
        for row in bindings:
            if not isinstance(row, dict):
                continue
            valid, reason, connection = self._binding_validity(row)
            binding_summaries.append(
                ProviderWorkloadBindingSummary(
                    execution_shape=str(row["execution_shape"]),  # type: ignore[arg-type]
                    model_id=str(row["model_id"]),
                    connection_id=str(row["connection_id"]),
                    connection_name=(
                        connection.name if connection is not None else "已移除连接"
                    ),
                    provider_kind=(
                        connection.kind if connection is not None else None
                    ),
                    certification_id=str(row["certification_id"]),
                    certification_source=str(row["certification_source"]),  # type: ignore[arg-type]
                    connection_fingerprint=str(row["connection_fingerprint"]),
                    qualification_fingerprint=str(row["qualification_fingerprint"]),
                    rerank_access_mode=(
                        str(row["rerank_access_mode"])  # type: ignore[arg-type]
                        if row.get("rerank_access_mode")
                        else None
                    ),
                    valid=valid,
                    reason_code=reason,
                )
            )
        approval_record_valid = any(
            isinstance(approval, dict)
            and str(approval["policy_fingerprint"]) == fingerprint
            and approval.get("revoked_at") is None
            and bool(approval["no_open_p0_p1"])
            and bool(approval["acknowledge_fail_closed"])
            for approval in approvals
        )
        approval_valid = (
            approval_record_valid
            and bool(binding_summaries)
            and all(item.valid for item in binding_summaries)
        )
        blockers: list[str] = []
        if not binding_summaries:
            blockers.append("provider_workload_bindings_required")
        blockers.extend(
            sorted({item.reason_code for item in binding_summaries if not item.valid})
        )
        if not self.data_plane_integrated(entry_id):
            blockers.append("provider_workload_data_plane_not_integrated")
        if not self.feature_enabled(entry_id):
            blockers.append("provider_workload_feature_disabled")
        if configured_status == "managed_required" and not approval_valid:
            blockers.append("provider_workload_approval_missing")
        effective_status = configured_status
        if configured_status in {"managed_required", "degraded_required"} and blockers:
            effective_status = "degraded_required"
        return ProviderWorkloadPolicyResponse(
            contract_version=PROVIDER_WORKLOAD_CONTRACT_VERSION,
            entry_id=entry_id,
            feature_enabled=self.feature_enabled(entry_id),
            data_plane_integrated=self.data_plane_integrated(entry_id),
            configured_status=configured_status,  # type: ignore[arg-type]
            effective_status=effective_status,  # type: ignore[arg-type]
            revision=int(policy["revision"]) if isinstance(policy, dict) else 0,
            policy_fingerprint=fingerprint,
            local_fallback_mode=(
                str(policy.get("local_fallback_mode") or "none")  # type: ignore[arg-type]
                if isinstance(policy, dict)
                else "none"
            ),
            bindings=binding_summaries,
            approval_valid=approval_valid,
            blocking_reason_codes=list(dict.fromkeys(blockers)),
            updated_at=(
                str(policy["updated_at"]) if isinstance(policy, dict) else None
            ),
        )

    def _current_qualification(
        self,
        *,
        connection_id: str,
        model_id: str,
        execution_shape: ProviderWorkloadExecutionShape,
        rerank_access_mode: str | None = None,
    ) -> tuple[dict[str, object] | None, str]:
        if execution_shape == "rerank_documents":
            if rerank_access_mode not in {"dedicated", "llm_json"}:
                return None, "provider_workload_rerank_access_mode_required"
        elif rerank_access_mode is not None:
            return None, "provider_workload_rerank_access_mode_not_allowed"
        if execution_shape in {"chat_text", "chat_tools"}:
            qualification, reason = ProviderChatControlService(
                self.router_service
            )._current_qualification(  # noqa: SLF001 - shared qualification contract
                connection_id=connection_id,
                model_id=model_id,
                capability=execution_shape,
            )
            if qualification is None:
                return None, reason
            return (
                {
                    **qualification,
                    "execution_shape": execution_shape,
                    "certification_source": "provider_chat",
                    "qualification_fingerprint": _fingerprint(qualification),
                },
                "qualified",
            )
        try:
            connection = self.repository.get_connection(
                self.router_service.tenant_id, connection_id
            )
        except RouterRepositoryError:
            return None, "provider_workload_connection_missing"
        if not connection.enabled:
            return None, "connection_disabled"
        required_scope = ProviderWorkloadCertificationService._required_scope(
            execution_shape
        )
        if required_scope not in connection.scopes:
            return None, f"connection_{required_scope}_scope_required"
        if connection.health != "online":
            return None, "provider_connection_not_online"
        try:
            self.repository.resolve_api_key(
                self.router_service.tenant_id, connection_id
            )
        except RouterCredentialUnavailable:
            return None, "provider_workload_credential_unavailable"
        if execution_shape == "fusion_native" and connection.kind != "openrouter":
            return None, "provider_workload_fusion_requires_openrouter"
        if execution_shape in {
            "openrouter_batch_chat",
            "openrouter_batch_embeddings",
        } and connection.kind != "openrouter":
            return None, "provider_workload_batch_requires_openrouter"
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
            return None, "provider_workload_model_inventory_missing"
        refresh = next(
            (
                item
                for item in self.repository.list_catalog_refreshes(
                    self.router_service.tenant_id,
                    connection_id=connection_id,
                    limit=500,
                )
                if str(item["id"]) == str(inventory[0]["last_refresh_id"])
            ),
            None,
        )
        if refresh is None or str(refresh["status"]) != "succeeded":
            return None, "provider_workload_catalog_refresh_missing"
        if bool(refresh["truncated"]):
            return None, "provider_workload_catalog_refresh_truncated"
        if str(refresh["connection_fingerprint"]) != fingerprint:
            return None, "provider_workload_catalog_stale"
        certification = self._repository_method(
            "get_latest_workload_certification"
        )(
            self.router_service.tenant_id,
            connection_id,
            model_id,
            execution_shape,
            rerank_access_mode=rerank_access_mode,
        )
        if certification is None:
            return None, "provider_workload_certification_required"
        if str(certification["status"]) != "passed":
            return None, "provider_workload_certification_not_passed"
        if str(certification["connection_fingerprint"]) != fingerprint:
            return None, "provider_workload_certification_stale"
        if str(certification["contract_version"]) != PROVIDER_WORKLOAD_CONTRACT_VERSION:
            return None, "provider_workload_contract_stale"
        time_reason = ProviderChatControlService._certification_time_status(  # noqa: SLF001
            certification
        )
        if time_reason is not None:
            return None, time_reason.replace("provider_chat_", "provider_workload_", 1)
        expected_actual_model = model_id
        if execution_shape == "fusion_native":
            profile = _safe_json_object(
                json.loads(str(certification.get("profile_json") or "{}"))
            )
            judge_model_id = profile.get("judge_model_id")
            if not isinstance(judge_model_id, str) or not judge_model_id:
                return None, "provider_workload_fusion_profile_mismatch"
            expected_actual_model = judge_model_id
        actual_model = certification.get("actual_model")
        if actual_model and str(actual_model) != expected_actual_model:
            return None, "provider_workload_certification_model_mismatch"
        qualification = {
            "execution_shape": execution_shape,
            "connection_id": connection_id,
            "model_id": model_id,
            "certification_id": str(certification["id"]),
            "certification_source": "provider_workload",
            "connection_fingerprint": fingerprint,
            "contract_version": PROVIDER_WORKLOAD_CONTRACT_VERSION,
            "profile_fingerprint": str(certification["profile_fingerprint"]),
            "rerank_access_mode": rerank_access_mode,
        }
        qualification["qualification_fingerprint"] = _fingerprint(qualification)
        return qualification, "qualified"

    def _binding_validity(
        self, row: dict[str, object]
    ) -> tuple[bool, str, RouterConnection | None]:
        try:
            connection = self.repository.get_connection(
                self.router_service.tenant_id, str(row["connection_id"])
            )
        except RouterRepositoryError:
            return False, "provider_workload_connection_missing", None
        current, reason = self._current_qualification(
            connection_id=str(row["connection_id"]),
            model_id=str(row["model_id"]),
            execution_shape=str(row["execution_shape"]),  # type: ignore[arg-type]
            rerank_access_mode=(
                str(row["rerank_access_mode"])
                if row.get("rerank_access_mode")
                else None
            ),
        )
        if current is None:
            return False, reason, connection
        if str(current["certification_source"]) != str(
            row["certification_source"]
        ):
            return False, "provider_workload_qualification_changed", connection
        if str(current["certification_id"]) != str(row["certification_id"]):
            return False, "provider_workload_newer_certification_requires_policy_update", connection
        if str(current["connection_fingerprint"]) != str(
            row["connection_fingerprint"]
        ):
            return False, "provider_workload_connection_fingerprint_changed", connection
        if str(current["qualification_fingerprint"]) != str(
            row["qualification_fingerprint"]
        ):
            return False, "provider_workload_qualification_changed", connection
        return True, "qualified", connection

    @staticmethod
    def _policy_fingerprint(
        entry_id: ProviderWorkloadEntryId,
        bindings: list[dict[str, object]],
        *,
        local_fallback_mode: str,
    ) -> str:
        material = [
            {
                "execution_shape": item["execution_shape"],
                "model_id": item["model_id"],
                "connection_id": item["connection_id"],
                "rerank_access_mode": item.get("rerank_access_mode"),
                "qualification_fingerprint": item["qualification_fingerprint"],
            }
            for item in sorted(
                bindings,
                key=lambda value: (
                    str(value["execution_shape"]),
                    str(value["model_id"]),
                    str(value["connection_id"]),
                ),
            )
        ]
        return _fingerprint(
            {
                "contract_version": PROVIDER_WORKLOAD_CONTRACT_VERSION,
                "entry_id": entry_id,
                "local_fallback_mode": local_fallback_mode,
                "bindings": material,
            }
        )

    def _repository_method(self, name: str):
        method = getattr(self.repository, name, None)
        if not callable(method):
            raise RouterServiceError(
                "provider_workload_storage_unavailable",
                "当前 Router 存储不支持 Workload 控制面。",
                status_code=503,
            )
        return method

    @staticmethod
    def _integer(value: object) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _float(value: object) -> float | None:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None


@dataclass(frozen=True, slots=True)
class ProviderWorkloadPreparedCall:
    run_id: str
    call_id: str
    entry_id: ProviderWorkloadEntryId
    execution_shape: ProviderWorkloadExecutionShape
    model_id: str
    call_sequence: int
    connection_id: str
    certification_id: str
    connection_fingerprint: str
    policy_fingerprint: str
    target: ProviderChatTarget
    authorized_target: AuthorizedProviderTarget


class ProviderWorkloadCallService:
    """Exact managed target and one-dispatch receipt guard for R6 data planes."""

    def __init__(self, router_service: ModelRouterService) -> None:
        self.router_service = router_service
        self.repository = router_service.repository
        self.control = ProviderWorkloadControlService(router_service)
        self.transport = ProviderChatTransport(router_service.egress_policy)

    def start_run(
        self,
        entry_id: ProviderWorkloadEntryId,
        *,
        parent_run_reference: str | None = None,
    ) -> str:
        policy = self.control.get_policy(entry_id)
        self._ensure_active(policy)
        run_id = f"workrun_{uuid.uuid4().hex}"
        self.repository.claim_workload_run(
            self.router_service.tenant_id,
            run_id=run_id,
            entry_id=entry_id,
            policy_fingerprint=policy.policy_fingerprint,
            parent_run_reference=parent_run_reference,
        )
        return run_id

    def start_stable_run(
        self,
        entry_id: ProviderWorkloadEntryId,
        *,
        parent_run_reference: str,
    ) -> str:
        """Claim one durable logical run and reject every later replay."""

        clean_parent = parent_run_reference.strip()
        if not clean_parent:
            raise RouterServiceError(
                "provider_workload_parent_run_reference_required",
                "Workload 稳定运行缺少父执行引用。",
                status_code=422,
            )
        policy = self.control.get_policy(entry_id)
        self._ensure_active(policy)
        material = json.dumps(
            {
                "tenant_id": self.router_service.tenant_id,
                "entry_id": entry_id,
                "parent_run_reference": clean_parent,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        run_id = f"workrun_{hashlib.sha256(material.encode('utf-8')).hexdigest()}"
        row, created = self.repository.claim_stable_workload_run(
            self.router_service.tenant_id,
            run_id=run_id,
            entry_id=entry_id,
            policy_fingerprint=policy.policy_fingerprint,
            parent_run_reference=clean_parent,
        )
        if (
            str(row["entry_id"]) != entry_id
            or str(row["parent_run_reference"] or "") != clean_parent
        ):
            raise RouterServiceError(
                "provider_workload_stable_run_collision",
                "Workload 稳定运行引用发生冲突，系统不会派发 Provider 请求。",
                status_code=409,
            )
        if not created:
            raise RouterServiceError(
                "provider_workload_logical_run_replay_blocked",
                "该 Workflow 模型节点已有执行证据，系统不会自动重放。",
                status_code=409,
            )
        return run_id

    async def prepare_call(
        self,
        *,
        run_id: str,
        entry_id: ProviderWorkloadEntryId,
        execution_shape: ProviderWorkloadExecutionShape,
        model_id: str,
        logical_call_key: str,
        call_sequence: int,
    ) -> ProviderWorkloadPreparedCall:
        policy = self.control.get_policy(entry_id)
        self._ensure_active(policy)
        run = self.repository.get_workload_run(
            self.router_service.tenant_id, run_id
        )
        if str(run["entry_id"]) != entry_id:
            raise RouterServiceError(
                "provider_workload_run_entry_mismatch",
                "Workload 运行入口与本次调用不一致。",
                status_code=409,
            )
        if str(run["status"]) != "running":
            raise RouterServiceError(
                "provider_workload_run_not_running",
                "Workload 运行已结束或状态不确定，系统不会重放调用。",
                status_code=409,
            )
        if str(run["policy_fingerprint"]) != policy.policy_fingerprint:
            raise RouterServiceError(
                "provider_workload_policy_changed",
                "Workload 策略已变化，本次运行在 Provider 派发前失败关闭。",
                status_code=409,
            )
        binding = next(
            (
                item
                for item in policy.bindings
                if item.execution_shape == execution_shape
                and item.model_id == model_id
                and item.valid
            ),
            None,
        )
        if binding is None:
            raise RouterServiceError(
                "provider_workload_binding_missing",
                "当前入口没有该精确模型与执行形态的合格 Binding。",
                status_code=409,
            )
        connection = self.repository.get_connection(
            self.router_service.tenant_id, binding.connection_id
        )
        api_key = self.repository.resolve_api_key(
            self.router_service.tenant_id, binding.connection_id
        )
        target = ProviderChatTarget.create(
            source="managed",
            provider_kind=connection.kind,
            base_url=connection.base_url,
            api_key=api_key,
            connection_id=connection.id,
        )
        authorized = await self.transport.authorize_managed_target(target)
        call_id = f"workcall_{uuid.uuid4().hex}"
        row, created = self.repository.claim_workload_call(
            self.router_service.tenant_id,
            call_id=call_id,
            run_id=run_id,
            entry_id=entry_id,
            execution_shape=execution_shape,
            requested_model=model_id,
            connection_id=binding.connection_id,
            certification_id=binding.certification_id,
            connection_fingerprint=binding.connection_fingerprint,
            logical_call_key_hash=hashlib.sha256(
                logical_call_key.encode("utf-8")
            ).hexdigest(),
            call_sequence=call_sequence,
        )
        if not created:
            raise RouterServiceError(
                "provider_workload_logical_call_replay_blocked",
                "该逻辑调用已存在，系统不会自动重放。",
                status_code=409,
            )
        return ProviderWorkloadPreparedCall(
            run_id=run_id,
            call_id=str(row["id"]),
            entry_id=entry_id,
            execution_shape=execution_shape,
            model_id=model_id,
            call_sequence=call_sequence,
            connection_id=binding.connection_id,
            certification_id=binding.certification_id,
            connection_fingerprint=binding.connection_fingerprint,
            policy_fingerprint=policy.policy_fingerprint,
            target=target,
            authorized_target=authorized,
        )

    def mark_dispatched(self, prepared: ProviderWorkloadPreparedCall) -> None:
        policy = self.control.get_policy(prepared.entry_id)
        self._ensure_active(policy)
        run = self.repository.get_workload_run(
            self.router_service.tenant_id, prepared.run_id
        )
        if (
            str(run["status"]) != "running"
            or str(run["entry_id"]) != prepared.entry_id
            or str(run["policy_fingerprint"]) != prepared.policy_fingerprint
            or policy.policy_fingerprint != prepared.policy_fingerprint
        ):
            raise RouterServiceError(
                "provider_workload_policy_changed",
                "Workload 策略已变化，本次调用在 Provider 派发前失败关闭。",
                status_code=409,
            )
        binding = next(
            (
                item
                for item in policy.bindings
                if item.execution_shape == prepared.execution_shape
                and item.model_id == prepared.model_id
                and item.connection_id == prepared.connection_id
                and item.certification_id == prepared.certification_id
                and item.connection_fingerprint == prepared.connection_fingerprint
                and item.valid
            ),
            None,
        )
        if binding is None:
            raise RouterServiceError(
                "provider_workload_binding_changed",
                "Workload Binding 或资格已变化，本次调用在 Provider 派发前失败关闭。",
                status_code=409,
            )
        try:
            self.repository.mark_workload_call_dispatched(
                self.router_service.tenant_id,
                prepared.call_id,
                run_id=prepared.run_id,
                entry_id=prepared.entry_id,
                execution_shape=prepared.execution_shape,
                requested_model=prepared.model_id,
                connection_id=prepared.connection_id,
                certification_id=prepared.certification_id,
                connection_fingerprint=prepared.connection_fingerprint,
                policy_fingerprint=prepared.policy_fingerprint,
            )
        except RouterRepositoryError as exc:
            if str(exc) == "provider_workload_dispatch_preconditions_changed":
                raise RouterServiceError(
                    "provider_workload_dispatch_preconditions_changed",
                    "Workload 派发前置条件已变化，系统未发送 Provider 请求。",
                    status_code=409,
                ) from exc
            raise

    def complete_call(
        self,
        prepared: ProviderWorkloadPreparedCall,
        *,
        status: str,
        result_class: str | None = None,
        error_code: str | None = None,
        actual_model: str | None = None,
        ttft_ms: float | None = None,
        e2e_ms: float | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
    ) -> None:
        self.repository.complete_workload_call(
            self.router_service.tenant_id,
            prepared.call_id,
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

    def complete_run(
        self,
        run_id: str,
        *,
        status: str,
        result_class: str | None = None,
        reason_codes: list[str] | None = None,
    ) -> None:
        self.repository.complete_workload_run(
            self.router_service.tenant_id,
            run_id,
            status=status,
            result_class=result_class,
            reason_codes=reason_codes,
        )

    @staticmethod
    def _ensure_active(policy: ProviderWorkloadPolicyResponse) -> None:
        if policy.effective_status != "managed_required":
            raise RouterServiceError(
                "provider_workload_policy_not_active",
                "该入口未处于合格的 managed_required 状态。",
                status_code=409,
            )
