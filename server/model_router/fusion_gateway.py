from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, Literal, cast

import httpx

from .service import ModelRouterService, RouterServiceError
from .workload_control import (
    PROVIDER_WORKLOAD_CONTRACT_VERSION,
    ProviderWorkloadCallService,
    ProviderWorkloadPreparedCall,
)
from .workflow_gateway import (
    ManagedWorkflowGateway,
    ManagedWorkflowNodeRun,
    ManagedWorkflowRoutingError,
    WorkflowEntryId,
)


FUSION_MODEL_ID = "openrouter/fusion"
FusionRoutingMode = Literal["legacy", "managed_required", "degraded_required"]


@dataclass(frozen=True, slots=True)
class ManagedApplicationFusionPlan:
    candidates: tuple[ProviderWorkloadPreparedCall, ...]
    judge: ProviderWorkloadPreparedCall


class ManagedFusionGateway:
    """Fail-closed Provider control-plane adapter for Fusion."""

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

    @classmethod
    def for_router(
        cls,
        router_service: ModelRouterService,
        *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> ManagedFusionGateway:
        return cls(
            ProviderWorkloadCallService(router_service),
            client_factory=client_factory,
        )

    def routing_mode(self) -> FusionRoutingMode:
        control = self.call_service.control
        if not control.feature_enabled("fusion"):
            return "legacy"
        policy = control.get_policy("fusion")
        if policy.configured_status == "legacy":
            return "legacy"
        if policy.effective_status == "managed_required":
            return "managed_required"
        return "degraded_required"

    def start_run(self) -> ManagedFusionRun:
        if self.routing_mode() != "managed_required":
            raise ManagedWorkflowRoutingError(
                "provider_workload_policy_not_active",
                "Fusion 的 Managed Provider 策略未就绪，当前调用失败关闭。",
                status_code=409,
                receipt=self.blocked_receipt("provider_workload_policy_not_active"),
            )
        try:
            run_id = self.call_service.start_run("fusion")
        except RouterServiceError as exc:
            raise ManagedWorkflowRoutingError(
                exc.code,
                "Fusion 的 Provider 资格已失效，当前调用失败关闭。",
                status_code=exc.status_code,
                receipt=self.blocked_receipt(exc.code),
            ) from exc
        node_run = ManagedWorkflowNodeRun(
            self._workflow_gateway,
            cast(WorkflowEntryId, "fusion"),
            run_id,
        )
        return ManagedFusionRun(node_run)

    @staticmethod
    def blocked_receipt(reason_code: str) -> dict[str, Any]:
        return {
            "contract_version": PROVIDER_WORKLOAD_CONTRACT_VERSION,
            "entry_id": "fusion",
            "routing_mode": "managed_required",
            "run_reference": "blocked_before_dispatch",
            "status": "failed",
            "call_count": 0,
            "reason_codes": [reason_code],
            "calls": [],
        }


class ManagedFusionRun:
    """One explicit native or application Fusion run with planned calls."""

    def __init__(self, node_run: ManagedWorkflowNodeRun) -> None:
        self._node_run = node_run

    async def prepare_native(
        self,
        *,
        candidate_model_ids: list[str],
        judge_model_id: str,
    ) -> ProviderWorkloadPreparedCall:
        prepared = await self._node_run.prepare_stream_call(
            logical_call_key="native:1",
            call_sequence=1,
            execution_shape="fusion_native",
            model_id=FUSION_MODEL_ID,
        )
        profile = {
            "execution_shape": "fusion_native",
            "model_id": FUSION_MODEL_ID,
            "candidate_model_ids": list(candidate_model_ids),
            "judge_model_id": judge_model_id,
        }
        profile_fingerprint = hashlib.sha256(
            json.dumps(profile, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        try:
            certification = self._node_run.gateway.call_service.repository.get_latest_workload_certification(
                self._node_run.gateway.call_service.router_service.tenant_id,
                prepared.connection_id,
                FUSION_MODEL_ID,
                "fusion_native",
                profile_fingerprint=profile_fingerprint,
            )
        except Exception as exc:
            code = "provider_workload_fusion_profile_unavailable"
            self._node_run.fail_prepared_call(prepared, code=code)
            self._node_run.finish("failed", reason_code=code)
            raise ManagedWorkflowRoutingError(
                code,
                "原生 Fusion 的资格 Profile 当前不可用。",
                status_code=503,
                receipt=self.receipt_summary(),
            ) from exc
        if (
            certification is None
            or str(certification["id"]) != prepared.certification_id
        ):
            code = "provider_workload_fusion_profile_mismatch"
            self._node_run.fail_prepared_call(prepared, code=code)
            self._node_run.finish("failed", reason_code=code)
            raise ManagedWorkflowRoutingError(
                code,
                "原生 Fusion 的候选与裁判配置未通过当前精确资格认证。",
                status_code=409,
                receipt=self.receipt_summary(),
            )
        return prepared

    async def prepare_application(
        self,
        *,
        candidate_model_ids: list[str],
        judge_model_id: str,
    ) -> ManagedApplicationFusionPlan:
        prepared: list[ProviderWorkloadPreparedCall] = []
        try:
            for index, model_id in enumerate(candidate_model_ids, start=1):
                prepared.append(
                    await self._node_run.prepare_stream_call(
                        logical_call_key=f"candidate:{index}:{model_id}",
                        call_sequence=index,
                        execution_shape="chat_text",
                        model_id=model_id,
                    )
                )
            judge = await self._node_run.prepare_stream_call(
                logical_call_key=f"judge:{len(candidate_model_ids) + 1}:{judge_model_id}",
                call_sequence=len(candidate_model_ids) + 1,
                execution_shape="chat_text",
                model_id=judge_model_id,
            )
        except asyncio.CancelledError:
            self._abandon_prepared(
                prepared,
                code="provider_workload_call_cancelled",
                status="cancelled",
                result_class="client_cancelled",
            )
            self._node_run.finish("cancelled", reason_code="provider_workload_call_cancelled")
            raise
        except ManagedWorkflowRoutingError as exc:
            self._abandon_prepared(
                prepared,
                code="provider_workload_fusion_preflight_aborted",
            )
            self._node_run.finish("failed", reason_code=exc.code)
            exc.receipt = self.receipt_summary()
            raise
        return ManagedApplicationFusionPlan(tuple(prepared), judge)

    async def collect_prepared_text(
        self,
        prepared: ProviderWorkloadPreparedCall,
        *,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
        cancel_event: asyncio.Event | None = None,
    ) -> str:
        parts: list[str] = []
        async for delta in self._node_run.stream_prepared_text(
            prepared,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            cancel_event=cancel_event,
        ):
            parts.append(delta)
        return "".join(parts)

    async def stream_native(
        self,
        prepared: ProviderWorkloadPreparedCall,
        *,
        candidate_model_ids: list[str],
        judge_model_id: str,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[str]:
        async for delta in self._node_run.stream_prepared_text(
            prepared,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            extra_payload={
                "plugins": [
                    {
                        "id": "fusion",
                        "analysis_models": list(candidate_model_ids),
                        "model": judge_model_id,
                    }
                ]
            },
            expected_actual_model=judge_model_id,
            cancel_event=cancel_event,
        ):
            yield delta

    def abandon_judge(
        self,
        prepared: ProviderWorkloadPreparedCall,
        *,
        code: str = "provider_workload_fusion_no_candidate_answers",
        status: Literal["failed", "cancelled"] = "failed",
    ) -> None:
        self._node_run.fail_prepared_call(
            prepared,
            code=code,
            status=status,
            result_class="planned_call_not_dispatched",
        )

    async def stream_events(
        self,
        *,
        use_native_fusion: bool,
        candidate_model_ids: list[str],
        judge_model_id: str,
        messages: list[dict[str, Any]],
        user_question: str,
        temperature: float,
        max_tokens: int,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        if use_native_fusion:
            yield {
                "event": "fusion_stage",
                "stage": "native_start",
                "message": "正在启动原生 Fusion 会诊室...",
            }
            native_text = ""
            try:
                prepared = await self.prepare_native(
                    candidate_model_ids=candidate_model_ids,
                    judge_model_id=judge_model_id,
                )
                async for delta in self.stream_native(
                    prepared,
                    candidate_model_ids=candidate_model_ids,
                    judge_model_id=judge_model_id,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    cancel_event=cancel_event,
                ):
                    native_text += delta
                    yield {"event": "fusion_delta", "output": delta}
            except asyncio.CancelledError:
                self.finish(
                    "cancelled", reason_code="provider_workload_call_cancelled"
                )
                raise
            except ManagedWorkflowRoutingError as exc:
                status = self.failure_status()
                receipt = self.finish(status, reason_code=exc.code)
                if native_text.strip():
                    yield {
                        "event": "fusion_end",
                        "mode": "native_partial",
                        "final_output": native_text,
                        "warning": "原生 Fusion 中途结束，系统未切换第二条 Fusion 路径。",
                        "reason_code": exc.code,
                        "provider_route_receipts": receipt,
                    }
                else:
                    yield self._error_event(exc.code, receipt)
                return
            receipt = self.finish("passed")
            yield {
                "event": "fusion_end",
                "mode": "native",
                "final_output": native_text,
                "provider_route_receipts": receipt,
            }
            return

        try:
            plan = await self.prepare_application(
                candidate_model_ids=candidate_model_ids,
                judge_model_id=judge_model_id,
            )
        except asyncio.CancelledError:
            raise
        except ManagedWorkflowRoutingError as exc:
            yield self._error_event(exc.code, self.receipt_summary())
            return

        for model_id in candidate_model_ids:
            yield {"event": "model_start", "model_id": model_id}

        async def collect_candidate(
            prepared: ProviderWorkloadPreparedCall,
        ) -> dict[str, str]:
            try:
                answer = await self.collect_prepared_text(
                    prepared,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    cancel_event=cancel_event,
                )
                return {
                    "model_id": prepared.model_id,
                    "answer": answer,
                    "error_code": "",
                }
            except ManagedWorkflowRoutingError as exc:
                return {
                    "model_id": prepared.model_id,
                    "answer": "",
                    "error_code": exc.code,
                }

        tasks = [
            asyncio.create_task(collect_candidate(prepared))
            for prepared in plan.candidates
        ]
        answers: list[dict[str, str]] = []
        try:
            for task in asyncio.as_completed(tasks):
                result = await task
                if result["error_code"]:
                    yield {
                        "event": "model_error",
                        "model_id": result["model_id"],
                        "message": "候选模型调用失败，系统未切换备用 Provider。",
                        "reason_code": result["error_code"],
                    }
                else:
                    answers.append(result)
                    yield {
                        "event": "model_end",
                        "model_id": result["model_id"],
                        "output": result["answer"],
                    }
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.abandon_judge(
                plan.judge,
                code="provider_workload_call_cancelled",
                status="cancelled",
            )
            self.finish("cancelled", reason_code="provider_workload_call_cancelled")
            raise

        if not answers:
            self.abandon_judge(plan.judge)
            status = self.failure_status()
            receipt = self.finish(
                status,
                reason_code="provider_workload_fusion_no_candidate_answers",
            )
            yield self._error_event(
                "provider_workload_fusion_no_candidate_answers", receipt
            )
            return

        judge_messages = [
            {
                "role": "user",
                "content": fusion_judge_prompt(user_question, answers),
            }
        ]
        yield {
            "event": "fusion_stage",
            "stage": "judge_start",
            "message": "候选答案已收齐，裁判模型正在合并共识...",
        }
        try:
            final_output = ""
            async for delta in self._node_run.stream_prepared_text(
                plan.judge,
                messages=judge_messages,
                temperature=0.35,
                max_tokens=max_tokens,
                cancel_event=cancel_event,
            ):
                final_output += delta
                yield {"event": "fusion_delta", "output": delta}
        except asyncio.CancelledError:
            self.finish("cancelled", reason_code="provider_workload_call_cancelled")
            raise
        except ManagedWorkflowRoutingError as exc:
            status = self.failure_status()
            receipt = self.finish(status, reason_code=exc.code)
            yield self._error_event(exc.code, receipt)
            return

        final_status: Literal["passed", "failed", "uncertain"] = (
            "passed"
            if all(call.status == "passed" for call in self._node_run.calls)
            else self.failure_status()
        )
        receipt = self.finish(
            final_status,
            reason_code=(
                None
                if final_status == "passed"
                else "provider_workload_fusion_partial_candidate_failure"
            ),
        )
        yield {
            "event": "fusion_end",
            "mode": "application",
            "final_output": final_output,
            "provider_route_receipts": receipt,
        }

    def finish(
        self,
        status: Literal["passed", "failed", "uncertain", "cancelled"],
        *,
        reason_code: str | None = None,
    ) -> dict[str, Any]:
        self._node_run.finish(status, reason_code=reason_code)
        return self.receipt_summary()

    def failure_status(self) -> Literal["failed", "uncertain"]:
        return (
            "uncertain"
            if any(call.status == "uncertain" for call in self._node_run.calls)
            else "failed"
        )

    def receipt_summary(self) -> dict[str, Any]:
        summary = self._node_run.receipt_summary()
        summary["entry_id"] = "fusion"
        summary["calls"] = sorted(
            summary["calls"], key=lambda item: int(item["call_sequence"])
        )
        return summary

    def _abandon_prepared(
        self,
        prepared: list[ProviderWorkloadPreparedCall],
        *,
        code: str,
        status: Literal["failed", "cancelled"] = "failed",
        result_class: str = "preflight_error",
    ) -> None:
        for item in prepared:
            self._node_run.fail_prepared_call(
                item,
                code=code,
                status=status,
                result_class=result_class,
            )

    @staticmethod
    def _error_event(code: str, receipt: dict[str, Any]) -> dict[str, Any]:
        return {
            "event": "error",
            "message": "Fusion 的 Managed Provider 调用失败，系统未执行备用路径。",
            "reason_code": code,
            "provider_route_receipts": receipt,
        }


def fusion_judge_prompt(
    user_question: str,
    model_answers: list[dict[str, str]],
) -> str:
    answer_blocks = "\n\n".join(
        f"### 候选模型：{item['model_id']}\n{item['answer']}"
        for item in model_answers
    )
    return f"""你是模镜专家团的首席裁判。请阅读多个模型对同一问题的回答，做事实核验、去重、互补整合，输出一份更可靠、更清晰的综合意见。

用户问题：
{user_question}

候选回答：
{answer_blocks}

输出要求：
1. 先给出“专家团综合意见”。
2. 标注不同模型观点中有价值的互补点。
3. 对不确定或冲突的信息明确提示复核。
4. 最后给出按优先级排序的行动清单。"""
