from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Literal, TypeVar

import httpx

from .egress import ProviderEgressError
from .multimodal_control import (
    OPENROUTER_GENERATION_METADATA_REQUEST_TIMEOUT_SECONDS,
)
from .service import ModelRouterService, RouterServiceError
from .workflow_gateway import (
    ManagedWorkflowGateway,
    ManagedWorkflowNodeRun,
    ManagedWorkflowRoutingError,
    WorkflowProviderCallReceipt,
)
from .workload_control import (
    PROVIDER_WORKLOAD_CONTRACT_VERSION,
    ProviderWorkloadCallService,
    ProviderWorkloadPreparedCall,
    r8c_audio_parameter_profile_reason,
)


R8BEntryId = Literal[
    "chat_image",
    "chat_document_native",
    "rag_vision",
    "workflow_interactive_vision",
    "workflow_deployment_vision",
    "xpert_vision",
    "image_generation",
    "multimodal_transcription",
    "multimodal_speech",
    "xpert_transcription",
    "xpert_speech",
]
R8BRoutingMode = Literal["legacy", "managed_required", "degraded_required"]
_T = TypeVar("_T")
_MAX_IMAGE_RESPONSE_BYTES = 32 * 1024 * 1024
_OPENROUTER_GENERATION_METADATA_POLL_DELAYS_SECONDS = (
    0.0,
    1.0,
    1.0,
    2.0,
    2.0,
    3.0,
    4.0,
    5.0,
    5.0,
    5.0,
)
_OPENROUTER_GENERATION_METADATA_POLL_TIMEOUT_SECONDS = 30.0


@dataclass(slots=True)
class _GenerationMetadataObservation:
    actual_model: str | None
    get_count: int
    elapsed_ms: float


class ManagedMultimodalError(RuntimeError):
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


class ManagedMultimodalGateway:
    """R8 managed Adapter boundary over the qualified workload call service."""

    def __init__(
        self,
        call_service: ProviderWorkloadCallService,
        *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self.call_service = call_service
        self._workflow_gateway = ManagedWorkflowGateway(
            call_service,
            client_factory=client_factory,
        )
        self._client_factory = client_factory

    @classmethod
    def for_router(
        cls,
        router_service: ModelRouterService,
        *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> "ManagedMultimodalGateway":
        return cls(
            ProviderWorkloadCallService(router_service),
            client_factory=client_factory,
        )

    def routing_mode(self, entry_id: R8BEntryId) -> R8BRoutingMode:
        control = self.call_service.control
        if not control.feature_enabled(entry_id):
            return "legacy"
        policy = control.get_policy(entry_id)
        if policy.configured_status == "legacy":
            return "legacy"
        if policy.effective_status == "managed_required":
            return "managed_required"
        return "degraded_required"

    def exact_model_id(
        self,
        entry_id: R8BEntryId,
        execution_shape: str,
        *,
        requested_model: str,
    ) -> str:
        policy = self.call_service.control.get_policy(entry_id)
        if policy.effective_status != "managed_required":
            raise self._blocked(entry_id, "provider_workload_policy_not_active")
        matches = [
            item.model_id
            for item in policy.bindings
            if item.execution_shape == execution_shape
            and item.model_id == requested_model
            and item.valid
        ]
        if len(matches) != 1:
            raise self._blocked(entry_id, "provider_workload_binding_missing")
        return matches[0]

    def certified_audio_parameters(
        self,
        entry_id: R8BEntryId,
        *,
        certification_id: str,
        execution_shape: Literal["audio_transcription", "audio_speech"],
    ) -> dict[str, object]:
        row = self.call_service.repository.get_workload_certification(
            self.call_service.router_service.tenant_id,
            certification_id,
        )
        if row is None or str(row.get("execution_shape") or "") != execution_shape:
            raise self._blocked(
                entry_id,
                "provider_multimodal_audio_parameter_contract_stale",
            )
        try:
            parsed = json.loads(str(row.get("profile_json") or "{}"))
        except (json.JSONDecodeError, TypeError, ValueError):
            parsed = {}
        profile = parsed if isinstance(parsed, dict) else {}
        reason = r8c_audio_parameter_profile_reason(execution_shape, profile)
        if reason is not None:
            raise self._blocked(entry_id, reason)
        return profile

    def start_run(
        self,
        entry_id: R8BEntryId,
        *,
        parent_run_reference: str,
        stable: bool,
    ) -> "ManagedMultimodalRun":
        if self.routing_mode(entry_id) != "managed_required":
            raise self._blocked(entry_id, "provider_workload_policy_not_active")
        parent = parent_run_reference.strip() or f"multimodal:{entry_id}:{uuid.uuid4().hex}"
        try:
            run_id = (
                self.call_service.start_stable_run(
                    entry_id, parent_run_reference=parent
                )
                if stable
                else self.call_service.start_run(
                    entry_id, parent_run_reference=parent
                )
            )
        except RouterServiceError as exc:
            raise ManagedMultimodalError(
                exc.code,
                "多模态 Managed Provider 运行在派发前被阻断。",
                status_code=exc.status_code,
                receipt=self.blocked_receipt(entry_id, exc.code),
            ) from exc
        delegate = ManagedWorkflowNodeRun(
            self._workflow_gateway,
            entry_id,  # type: ignore[arg-type]
            run_id,
        )
        return ManagedMultimodalRun(self, entry_id, delegate)

    async def prepare_chat_dispatch(
        self,
        entry_id: Literal["chat_image", "chat_document_native"],
        *,
        execution_shape: Literal["chat_image_stream", "chat_document_stream"],
        requested_model: str,
        parent_run_reference: str,
    ) -> "ManagedMultimodalChatDispatch":
        model_id = self.exact_model_id(
            entry_id,
            execution_shape,
            requested_model=requested_model,
        )
        run = self.start_run(
            entry_id,
            parent_run_reference=parent_run_reference,
            stable=True,
        )
        try:
            prepared = await self.call_service.prepare_call(
                run_id=run._delegate.run_id,  # noqa: SLF001 - same gateway boundary
                entry_id=entry_id,
                execution_shape=execution_shape,
                model_id=model_id,
                logical_call_key="chat",
                call_sequence=1,
            )
        except RouterServiceError as exc:
            receipt = run.finish_failure(exc.code)
            raise ManagedMultimodalError(
                exc.code,
                "多模态 Chat 在 Provider 派发前被阻断。",
                status_code=exc.status_code,
                receipt=receipt,
            ) from exc
        return ManagedMultimodalChatDispatch(run, prepared)

    @staticmethod
    def blocked_receipt(entry_id: R8BEntryId, reason_code: str) -> dict[str, Any]:
        return {
            "contract_version": PROVIDER_WORKLOAD_CONTRACT_VERSION,
            "entry_id": entry_id,
            "routing_mode": "managed_required",
            "run_reference": "blocked_before_dispatch",
            "status": "failed",
            "call_count": 0,
            "reason_codes": [reason_code],
            "calls": [],
        }

    def _blocked(self, entry_id: R8BEntryId, code: str) -> ManagedMultimodalError:
        return ManagedMultimodalError(
            code,
            "多模态入口缺少当前精确模型的合格 Managed Binding。",
            receipt=self.blocked_receipt(entry_id, code),
        )


class ManagedMultimodalRun:
    def __init__(
        self,
        gateway: ManagedMultimodalGateway,
        entry_id: R8BEntryId,
        delegate: ManagedWorkflowNodeRun,
    ) -> None:
        self.gateway = gateway
        self.entry_id = entry_id
        self._delegate = delegate

    async def complete_vision_json(
        self,
        *,
        logical_call_key: str,
        call_sequence: int,
        model_id: str,
        messages: list[dict[str, Any]],
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        try:
            text = await self._delegate.complete_json_object_for_shape(
                logical_call_key=logical_call_key,
                call_sequence=call_sequence,
                execution_shape="vision_json_unary",
                model_id=model_id,
                messages=messages,
                temperature=0,
                max_tokens=max_tokens,
            )
            parsed = json.loads(text)
            if not isinstance(parsed, dict):
                raise ValueError("provider_multimodal_vision_json_invalid")
            return parsed
        except (ManagedWorkflowRoutingError, json.JSONDecodeError, ValueError) as exc:
            if isinstance(exc, ManagedWorkflowRoutingError):
                code = exc.code
                status_code = exc.status_code
            else:
                code = "provider_multimodal_vision_json_invalid"
                status_code = 502
            raise ManagedMultimodalError(
                code,
                "视觉 Provider 未返回有效的结构化结果，系统未重试或切换目标。",
                status_code=status_code,
                receipt=self._delegate.receipt_summary(),
            ) from exc

    async def complete_image_generation(
        self,
        *,
        logical_call_key: str,
        model_id: str,
        payload: Mapping[str, object],
        parse_response: Callable[[httpx.Response], _T],
    ) -> tuple[_T, dict[str, Any]]:
        prepared: ProviderWorkloadPreparedCall | None = None
        dispatched = False
        response_received = False
        response_complete = False
        started = time.perf_counter()
        try:
            prepared = await self.gateway.call_service.prepare_call(
                run_id=self._delegate.run_id,
                entry_id=self.entry_id,
                execution_shape="image_generation",
                model_id=model_id,
                logical_call_key=logical_call_key,
                call_sequence=1,
            )
            target = prepared.multimodal_target
            if target is None:
                raise RouterServiceError(
                    "provider_multimodal_target_missing",
                    "图片生成缺少已授权的 Adapter 目标。",
                    status_code=409,
                )
            request_payload = self._image_request_payload(target, payload)
            request_payload["model"] = model_id
            async with self._client() as client:
                request = self.gateway.call_service.multimodal_transport.build_authorized_json_request(
                    client,
                    target,
                    prepared.authorized_target,
                    request_payload,
                )
                self.gateway.call_service.mark_dispatched(prepared)
                dispatched = True
                response = await self.gateway.call_service.multimodal_transport.send_authorized(
                    client, request
                )
                response_received = True
                try:
                    self._validate_status(response.status_code)
                    await self._read_bounded(response)
                    response_complete = True
                    result = parse_response(response)
                finally:
                    await response.aclose()
            elapsed_ms = (time.perf_counter() - started) * 1000
            actual_model = str(getattr(result, "actual_model", model_id) or model_id)
            usage = getattr(result, "usage", None)
            self.gateway.call_service.complete_call(
                prepared,
                status="passed",
                result_class="success",
                actual_model=actual_model,
                ttft_ms=elapsed_ms,
                e2e_ms=elapsed_ms,
                prompt_tokens=getattr(usage, "input_tokens", None),
                completion_tokens=getattr(usage, "output_tokens", None),
                total_tokens=getattr(usage, "total_tokens", None),
            )
            self._delegate.calls.append(
                WorkflowProviderCallReceipt(
                    call_sequence=1,
                    model_id=model_id,
                    actual_model=actual_model,
                    dispatched=True,
                    status="passed",
                    prompt_tokens=getattr(usage, "input_tokens", None),
                    completion_tokens=getattr(usage, "output_tokens", None),
                    total_tokens=getattr(usage, "total_tokens", None),
                )
            )
            self._delegate.finish("passed")
            return result, self._delegate.receipt_summary()
        except asyncio.CancelledError:
            self._record_failure(
                prepared, model_id, dispatched, "cancelled", "client_cancelled",
                "provider_workload_call_cancelled",
            )
            raise
        except ManagedMultimodalError as exc:
            status = "failed" if response_received or not dispatched else "uncertain"
            self._record_failure(
                prepared,
                model_id,
                dispatched,
                status,
                "provider_error"
                if response_received
                else "transport_error"
                if dispatched
                else "preflight_failure",
                exc.code,
            )
            raise ManagedMultimodalError(
                exc.code,
                str(exc),
                status_code=exc.status_code,
                receipt=self._delegate.receipt_summary(),
            ) from exc
        except Exception as exc:
            status = "failed" if response_received or not dispatched else "uncertain"
            code = self._error_code(exc, dispatched=dispatched, complete=response_complete)
            self._record_failure(
                prepared,
                model_id,
                dispatched,
                status,
                "provider_error" if response_complete else "transport_error",
                code,
            )
            raise ManagedMultimodalError(
                code,
                "图片生成 Managed Provider 调用失败，系统未重试或切换目标。",
                status_code=getattr(exc, "status_code", 502),
                receipt=self._delegate.receipt_summary(),
            ) from exc

    async def complete_audio(
        self,
        *,
        execution_shape: Literal["audio_transcription", "audio_speech"],
        logical_call_key: str,
        model_id: str,
        expected_connection_id: str,
        expected_certification_id: str,
        expected_connection_fingerprint: str,
        expected_adapter_contract: str | None,
        expected_protocol_version: str | None,
        payload: Mapping[str, object] | None,
        files: Mapping[str, tuple[str, bytes, str]] | None,
        parse_response: Callable[[httpx.Response], _T],
    ) -> tuple[_T, dict[str, Any]]:
        prepared: ProviderWorkloadPreparedCall | None = None
        dispatched = False
        response_received = False
        response_complete = False
        generation_id_observed: bool | None = None
        generation_metadata_get_count: int | None = None
        generation_metadata_wait_ms: float | None = None
        started = time.perf_counter()
        try:
            prepared = await self.gateway.call_service.prepare_call(
                run_id=self._delegate.run_id,
                entry_id=self.entry_id,
                execution_shape=execution_shape,
                model_id=model_id,
                logical_call_key=logical_call_key,
                call_sequence=1,
            )
            if (
                prepared.connection_id != expected_connection_id
                or prepared.certification_id != expected_certification_id
                or prepared.connection_fingerprint
                != expected_connection_fingerprint
                or prepared.adapter_contract != expected_adapter_contract
                or prepared.protocol_version != expected_protocol_version
            ):
                raise RouterServiceError(
                    "provider_workload_binding_changed",
                    "Workload Binding 或资格已变化，本次调用在 Provider 派发前失败关闭。",
                    status_code=409,
                )
            target = prepared.multimodal_target
            if target is None:
                raise RouterServiceError(
                    "provider_multimodal_target_missing",
                    "音频调用缺少已授权的 Adapter 目标。",
                    status_code=409,
                )
            async with self._client() as client:
                if files is not None:
                    transport = self.gateway.call_service.multimodal_transport
                    request = transport.build_authorized_multipart_request(
                        client,
                        target,
                        prepared.authorized_target,
                        data={
                            key: str(value)
                            for key, value in dict(payload or {}).items()
                            if value is not None
                        },
                        files=files,
                    )
                else:
                    transport = self.gateway.call_service.multimodal_transport
                    request = transport.build_authorized_json_request(
                        client,
                        target,
                        prepared.authorized_target,
                        dict(payload or {}),
                    )
                self.gateway.call_service.mark_dispatched(prepared)
                dispatched = True
                response = await self.gateway.call_service.multimodal_transport.send_authorized(
                    client, request
                )
                response_received = True
                generation_id = str(
                    response.headers.get("X-Generation-Id") or ""
                ).strip()
                if target.provider_kind == "openrouter":
                    generation_id_observed = bool(generation_id)
                    generation_metadata_get_count = 0
                    generation_metadata_wait_ms = 0.0
                try:
                    self._validate_status(response.status_code)
                    await self._read_bounded(response)
                    response_complete = True
                    result = parse_response(response)
                finally:
                    await response.aclose()
                actual_model = str(
                    getattr(result, "actual_model", "") or ""
                ).strip()
                if not actual_model and target.provider_kind == "openrouter":
                    if not generation_id:
                        raise ManagedMultimodalError(
                            "provider_multimodal_generation_id_missing",
                            "音频 Provider 未返回实际模型查询所需的 Generation ID。",
                            status_code=502,
                        )
                    observation = _GenerationMetadataObservation(None, 0, 0.0)
                    try:
                        await self._poll_openrouter_generation_model(
                            client=client,
                            target=target,
                            generation_id=generation_id,
                            observation=observation,
                        )
                    finally:
                        generation_metadata_get_count = observation.get_count
                        generation_metadata_wait_ms = observation.elapsed_ms
                    actual_model = str(observation.actual_model or "").strip()
                    if not actual_model:
                        raise ManagedMultimodalError(
                            "provider_multimodal_generation_metadata_wait_exhausted",
                            "音频 Provider 的实际模型元数据未在限定时间内可用。",
                            status_code=502,
                        )
                if not actual_model:
                    raise ManagedMultimodalError(
                        "provider_multimodal_actual_model_unverified",
                        "音频 Provider 未提供可验证的实际模型证据。",
                        status_code=502,
                    )
                if actual_model != model_id:
                    raise ManagedMultimodalError(
                        "provider_workload_model_mismatch",
                        "音频 Provider 返回的实际模型与 Binding 不一致。",
                        status_code=502,
                    )
                result = replace(result, actual_model=actual_model)
            elapsed_ms = (time.perf_counter() - started) * 1000
            actual_model = str(getattr(result, "actual_model", "") or "") or None
            usage = getattr(result, "usage", None)
            self.gateway.call_service.complete_call(
                prepared,
                status="passed",
                result_class="success",
                actual_model=actual_model,
                ttft_ms=elapsed_ms,
                e2e_ms=elapsed_ms,
                prompt_tokens=getattr(usage, "input_tokens", None),
                completion_tokens=getattr(usage, "output_tokens", None),
                total_tokens=getattr(usage, "total_tokens", None),
                generation_id_observed=generation_id_observed,
                generation_metadata_get_count=generation_metadata_get_count,
                generation_metadata_wait_ms=generation_metadata_wait_ms,
            )
            self._delegate.calls.append(
                WorkflowProviderCallReceipt(
                    call_sequence=1,
                    model_id=model_id,
                    actual_model=actual_model,
                    dispatched=True,
                    status="passed",
                    prompt_tokens=getattr(usage, "input_tokens", None),
                    completion_tokens=getattr(usage, "output_tokens", None),
                    total_tokens=getattr(usage, "total_tokens", None),
                )
            )
            self._delegate.finish("passed")
            return result, self._delegate.receipt_summary()
        except asyncio.CancelledError:
            status = "uncertain" if dispatched else "failed"
            code = (
                "provider_workload_dispatch_uncertain"
                if dispatched
                else "provider_workload_call_cancelled"
            )
            self._record_failure(
                prepared,
                model_id,
                dispatched,
                status,
                "client_cancelled",
                code,
                generation_id_observed=generation_id_observed,
                generation_metadata_get_count=generation_metadata_get_count,
                generation_metadata_wait_ms=generation_metadata_wait_ms,
            )
            raise
        except ManagedMultimodalError as exc:
            response_incomplete = (
                dispatched
                and not response_complete
                and exc.code == "provider_multimodal_response_too_large"
            )
            status = (
                "uncertain"
                if response_incomplete
                else "failed" if response_received or not dispatched else "uncertain"
            )
            self._record_failure(
                prepared,
                model_id,
                dispatched,
                status,
                "provider_error" if response_received else "transport_error",
                exc.code,
                generation_id_observed=generation_id_observed,
                generation_metadata_get_count=generation_metadata_get_count,
                generation_metadata_wait_ms=generation_metadata_wait_ms,
            )
            raise ManagedMultimodalError(
                exc.code,
                str(exc),
                status_code=exc.status_code,
                receipt=self._delegate.receipt_summary(),
            ) from exc
        except Exception as exc:
            status = "failed" if response_complete or not dispatched else "uncertain"
            code = self._error_code(
                exc, dispatched=dispatched, complete=response_complete
            )
            self._record_failure(
                prepared,
                model_id,
                dispatched,
                status,
                "provider_error" if response_complete else "transport_error",
                code,
                generation_id_observed=generation_id_observed,
                generation_metadata_get_count=generation_metadata_get_count,
                generation_metadata_wait_ms=generation_metadata_wait_ms,
            )
            raise ManagedMultimodalError(
                code,
                "音频 Managed Provider 调用失败，系统未重试或切换目标。",
                status_code=getattr(exc, "status_code", 502),
                receipt=self._delegate.receipt_summary(),
            ) from exc

    async def _poll_openrouter_generation_model(
        self,
        *,
        client: httpx.AsyncClient,
        target: object,
        generation_id: str,
        observation: _GenerationMetadataObservation,
    ) -> _GenerationMetadataObservation:
        """Poll one dispatched generation with bounded read-only GETs only."""

        transport = self.gateway.call_service.multimodal_transport
        loop = asyncio.get_running_loop()
        started = loop.time()
        deadline = (
            started + _OPENROUTER_GENERATION_METADATA_POLL_TIMEOUT_SECONDS
        )
        try:
            for delay_seconds in _OPENROUTER_GENERATION_METADATA_POLL_DELAYS_SECONDS:
                remaining_seconds = deadline - loop.time()
                if remaining_seconds <= 0:
                    break
                if delay_seconds:
                    if delay_seconds >= remaining_seconds:
                        await asyncio.sleep(remaining_seconds)
                        break
                    await asyncio.sleep(delay_seconds)
                remaining_seconds = deadline - loop.time()
                if remaining_seconds <= 0:
                    break
                attempt_timeout = min(
                    OPENROUTER_GENERATION_METADATA_REQUEST_TIMEOUT_SECONDS,
                    remaining_seconds,
                )
                def mark_metadata_get_dispatched() -> None:
                    observation.get_count += 1

                try:
                    async with asyncio.timeout(attempt_timeout):
                        actual_model = await (
                            transport.fetch_openrouter_generation_model(
                                client,
                                target,
                                generation_id,
                                on_dispatch=mark_metadata_get_dispatched,
                            )
                        )
                except asyncio.CancelledError:
                    raise
                except ProviderEgressError:
                    raise
                except (TimeoutError, httpx.HTTPError):
                    continue
                if actual_model:
                    observation.actual_model = actual_model
                    return observation
            return observation
        finally:
            observation.elapsed_ms = max(0.0, (loop.time() - started) * 1000)

    def finish_success(self) -> dict[str, Any]:
        self._delegate.finish("passed")
        return self._delegate.receipt_summary()

    def finish_failure(self, reason_code: str) -> dict[str, Any]:
        status = (
            "uncertain"
            if any(call.status == "uncertain" for call in self._delegate.calls)
            else "failed"
        )
        self._delegate.finish(status, reason_code=reason_code)
        return self._delegate.receipt_summary()

    def receipt_summary(self) -> dict[str, Any]:
        return self._delegate.receipt_summary()

    def _client(self) -> httpx.AsyncClient:
        if self.gateway._client_factory is not None:
            return self.gateway._client_factory()
        return httpx.AsyncClient(
            timeout=httpx.Timeout(connect=15, read=180, write=30, pool=10),
            follow_redirects=False,
            trust_env=False,
            transport=httpx.AsyncHTTPTransport(retries=0),
        )

    @staticmethod
    async def _read_bounded(response: httpx.Response) -> None:
        total = 0
        chunks: list[bytes] = []
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > _MAX_IMAGE_RESPONSE_BYTES:
                raise ManagedMultimodalError(
                    "provider_multimodal_response_too_large",
                    "多模态 Provider 响应超过安全上限。",
                    status_code=502,
                )
            chunks.append(chunk)
        response._content = b"".join(chunks)  # noqa: SLF001 - preserve parsed response

    @staticmethod
    def _image_request_payload(
        target: object,
        payload: Mapping[str, object],
    ) -> dict[str, object]:
        request_payload = dict(payload)
        if (
            getattr(target, "adapter_contract", None)
            != "openai_compatible_images_generations_v1"
        ):
            return request_payload
        unsupported = sorted(
            key
            for key in ("aspect_ratio", "seed", "input_references")
            if request_payload.get(key) is not None
        )
        if unsupported:
            raise ManagedMultimodalError(
                "provider_multimodal_adapter_parameter_unsupported",
                "OpenAI-compatible 图片生成 Adapter 不支持本次高级参数。",
                status_code=422,
            )
        resolution = request_payload.pop("resolution", None)
        if resolution is not None:
            request_payload["size"] = resolution
        request_payload["response_format"] = "b64_json"
        return request_payload

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
        raise ManagedMultimodalError(
            code,
            "多模态 Provider 请求失败。",
            status_code=status_code,
        )

    @staticmethod
    def _error_code(exc: Exception, *, dispatched: bool, complete: bool) -> str:
        if isinstance(exc, ManagedMultimodalError):
            return exc.code
        if isinstance(exc, RouterServiceError):
            return exc.code
        if isinstance(exc, ProviderEgressError):
            return exc.code
        if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
            return "provider_workload_timeout"
        if isinstance(exc, httpx.HTTPError):
            return "provider_workload_transport_error"
        if dispatched and not complete:
            return "provider_workload_dispatch_uncertain"
        return "provider_multimodal_invalid_response" if complete else "provider_workload_preflight_failed"

    def _record_failure(
        self,
        prepared: ProviderWorkloadPreparedCall | None,
        model_id: str,
        dispatched: bool,
        status: str,
        result_class: str,
        code: str,
        *,
        generation_id_observed: bool | None = None,
        generation_metadata_get_count: int | None = None,
        generation_metadata_wait_ms: float | None = None,
    ) -> None:
        if prepared is not None:
            self.gateway.call_service.complete_call(
                prepared,
                status=status,
                result_class=result_class,
                error_code=code,
                generation_id_observed=generation_id_observed,
                generation_metadata_get_count=generation_metadata_get_count,
                generation_metadata_wait_ms=generation_metadata_wait_ms,
            )
        self._delegate.calls.append(
            WorkflowProviderCallReceipt(
                call_sequence=1,
                model_id=model_id,
                actual_model=None,
                dispatched=dispatched,
                status=status,  # type: ignore[arg-type]
                error_code=code,
            )
        )
        self._delegate.finish(
            "uncertain" if status == "uncertain" else "failed",
            reason_code=code,
        )


class ManagedMultimodalChatDispatch:
    def __init__(
        self,
        run: ManagedMultimodalRun,
        prepared: ProviderWorkloadPreparedCall,
    ) -> None:
        self.run = run
        self.prepared = prepared
        self.dispatched = False
        self.completed = False
        self.started_at: float | None = None

    @property
    def target_model(self) -> str:
        return self.prepared.model_id

    async def send(
        self,
        client: httpx.AsyncClient,
        payload: Mapping[str, object],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        if self.dispatched:
            raise ManagedMultimodalError(
                "provider_multimodal_duplicate_post_blocked",
                "同一多模态 Chat 逻辑调用不能重复派发。",
                receipt=self.run.receipt_summary(),
            )
        target = self.prepared.multimodal_target
        if target is None:
            raise ManagedMultimodalError(
                "provider_multimodal_target_missing",
                "多模态 Chat 缺少已授权的 Adapter 目标。",
                receipt=self.run.receipt_summary(),
            )
        request = self.run.gateway.call_service.multimodal_transport.build_authorized_json_request(
            client,
            target,
            self.prepared.authorized_target,
            payload,
            headers=headers,
        )
        self.run.gateway.call_service.mark_dispatched(self.prepared)
        self.dispatched = True
        self.started_at = time.perf_counter()
        return await self.run.gateway.call_service.multimodal_transport.send_authorized(
            client, request
        )

    def complete(
        self,
        *,
        status: Literal["passed", "failed", "uncertain", "cancelled"],
        result_class: str,
        error_code: str | None = None,
        actual_model: str | None = None,
        ttft_ms: float | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
    ) -> dict[str, Any]:
        if self.completed:
            return self.run.receipt_summary()
        e2e_ms = (
            (time.perf_counter() - self.started_at) * 1000
            if self.started_at is not None
            else None
        )
        self.run.gateway.call_service.complete_call(
            self.prepared,
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
        self.run._delegate.calls.append(  # noqa: SLF001 - receipt adapter
            WorkflowProviderCallReceipt(
                call_sequence=1,
                model_id=self.prepared.model_id,
                actual_model=actual_model,
                dispatched=self.dispatched,
                status=status,
                error_code=error_code,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
            )
        )
        self.run._delegate.finish(status, reason_code=error_code)  # noqa: SLF001
        self.completed = True
        return self.run.receipt_summary()
